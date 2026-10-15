"""Phase C: インテリジェンス運用（Operational Workflow / Pipeline Health）の状態集計。

「担当者が朝開いて、何が止まっていて、次に何をすべきか」が分かる1本のSummaryを作る。

方針:
- 表示はDBの現在状態・既存run statusだけから作る。LLMは一切呼ばない（Theme同期の実行は
  既存 geo_theme_candidate_pipeline_service を、ユーザーが明示的に押した時だけ使う）。
- 既存のTheme Candidate生成・Episode Cluster判定・Alert生成・Article Discoveryには触れない。
  既存サービスの関数を読み取りで呼ぶだけ。
- 一部のセクションが失敗しても全体は返す（errors に理由を入れ、そのセクションだけ欠ける）。
- 新しいtable / statusは追加しない（「Theme同期待ち」はEpisode側から決定論的に計算）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import geo_freshness_service
import geo_scheduler_service
import geo_scheduler_tick as scheduler_tick
import geo_source_library_service as lib
import geo_theme_candidate_pipeline_service as pipeline
import geo_theme_candidate_service as candidate_service
import geo_theme_explorer_service as explorer

SUMMARY_VERSION = "operations_summary_v1"

REVIEW_ITEM_LIMIT = 5
ALERT_ITEM_LIMIT = 5
RECENT_DAYS = 7
RUN_LOOKBACK_DAYS = 14
RUNNING_TOO_LONG_HOURS = 24
SCHEDULER_ACTIVE_DAYS = 3        # scheduled_external の実行がこの日数以内なら「稼働中」
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}

# Collectionの対象外とする内部run（Theme同期・Scheduler自身の行はCollectionではない）
_NON_COLLECTION_CONNECTORS = set(scheduler_tick.INTERNAL_CONNECTOR_TYPES) | {pipeline.SYNC_RUN_CONNECTOR_TYPE}

# 画面向けの名称（内部用語は詳細表示でのみ出す）
QUAD_LABELS = {1: "言葉での協調", 2: "実行を伴う協調", 3: "言葉での対立", 4: "実力行使を伴う対立"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        return geo_scheduler_service._parse(value)    # 小数秒の桁数に依存しない解析（Scheduler側と共通）
    except ValueError:
        return None


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _hours_since(dt: datetime | None, now: datetime) -> float | None:
    return None if dt is None else round((now - dt).total_seconds() / 3600, 1)


def _state(level: str, label: str, detail: str = "") -> dict:
    """level: ok（正常）/ attention（要対応）/ stopped（停止・stale）。"""
    return {"level": level, "label": label, "detail": detail}


# ---------------------------------------------------------------------------
# Review（Episode Clusterのレビュー待ち）
# ---------------------------------------------------------------------------

def _entity_names(client, ids: list) -> dict:
    ids = [i for i in dict.fromkeys(ids) if i]
    if not ids:
        return {}
    try:
        return candidate_service._canonical_names(client, ids)  # 既存の名称解決を再利用
    except Exception:  # noqa: BLE001 — 名称が引けなくても件数表示は続ける
        return {}


def build_review(client, *, is_demo: bool = False, now: datetime | None = None) -> dict:
    now = now or _now()
    demo = f"eq.{str(is_demo).lower()}"
    clusters = client.select("geo_episode_clusters", {
        "select": "episode_cluster_id,cluster_status,created_at,actor_entity_id,quad_class,"
                  "member_count,last_event_date,representative_geo,reviewed_at,reviewed_by",
        "is_demo": demo, "limit": "5000"})
    by_status: dict = {}
    for c in clusters:
        by_status[c["cluster_status"]] = by_status.get(c["cluster_status"], 0) + 1

    waiting = sorted((c for c in clusters if c["cluster_status"] == "candidate"),
                     key=lambda c: c.get("created_at") or "")
    oldest = _parse(waiting[0].get("created_at")) if waiting else None
    top = waiting[:REVIEW_ITEM_LIMIT]
    names = _entity_names(client, [c.get("actor_entity_id") for c in top])
    items = [{
        "episode_cluster_id": c["episode_cluster_id"],
        "actor": names.get(c.get("actor_entity_id"), "(不明)"),
        "regions": list(c.get("representative_geo") or [])[:3],
        "episode_type": QUAD_LABELS.get(c.get("quad_class"), "分類なし"),
        "evidence_count": c.get("member_count") or 0,
        "last_event_date": c.get("last_event_date"),
        "waiting_since": c.get("created_at"),
    } for c in top]

    reviewed = [c for c in clusters if c.get("reviewed_at")]
    last_review = max(reviewed, key=lambda c: c["reviewed_at"]) if reviewed else None
    return {
        "total_clusters": len(clusters),
        "by_status": by_status,
        "pending": len(waiting),
        "accepted": by_status.get("accepted", 0),
        "rejected": by_status.get("rejected", 0),
        "oldest_waiting_at": _iso(oldest),
        "oldest_waiting_days": None if oldest is None else (now - oldest).days,
        "items": items,
        "last_review": None if not last_review else {
            "reviewed_at": last_review["reviewed_at"], "reviewed_by": last_review.get("reviewed_by"),
            "status": last_review["cluster_status"]},
    }


# ---------------------------------------------------------------------------
# Theme同期（accepted → Theme Candidate未生成）
# ---------------------------------------------------------------------------

def _recent_sync_runs(client, *, is_demo: bool, limit: int = 20) -> list:
    return pipeline.list_sync_runs(client, is_demo=is_demo, limit=limit)


def _last_error_by_cluster(runs: list) -> dict:
    """個別Syncの実行履歴から「最後の試行が失敗/生成不可だったcluster」を引く（新しい順に最初の1件）。"""
    out: dict = {}
    for r in runs:                       # started_at降順
        params = r.get("request_parameters") or {}
        if params.get("mode") != "single":
            continue
        for cid in params.get("cluster_ids") or []:
            if cid in out:
                continue
            failed = r.get("status") == "failed" or (r.get("error_count") or 0) > 0
            out[cid] = {"failed": failed, "at": r.get("started_at"),
                        "message": r.get("error_summary")} if failed else {"failed": False}
    return out


def build_theme_sync(client, *, is_demo: bool = False) -> dict:
    materials = candidate_service.list_candidate_worthy_material(client, is_demo=is_demo)
    existing_keys = pipeline._existing_candidate_cluster_keys(client, is_demo=is_demo)
    pending_materials = [m for m in materials if pipeline._material_key(m["cluster_ids"]) not in existing_keys]

    accepted = client.select("geo_episode_clusters", {
        "select": "episode_cluster_id", "cluster_status": "eq.accepted",
        "is_demo": f"eq.{str(is_demo).lower()}", "limit": "5000"})
    worthy_ids = {cid for m in materials for cid in m["cluster_ids"]}
    not_eligible = [a["episode_cluster_id"] for a in accepted if a["episode_cluster_id"] not in worthy_ids]

    runs = _recent_sync_runs(client, is_demo=is_demo)
    last_errors = _last_error_by_cluster(runs)
    pending = []
    for m in pending_materials:
        errs = [last_errors[c] for c in m["cluster_ids"] if last_errors.get(c, {}).get("failed")]
        pending.append({
            "cluster_ids": m["cluster_ids"],
            "source_type": m.get("source_type"),
            "actors": (m.get("actors") or [])[:4],
            "regions": (m.get("regions") or [])[:3],
            "evidence_count": m.get("member_count_total") or len(m.get("evidence_bullets") or []),
            "latest_signal_at": m.get("latest_signal_at"),
            "retry": bool(errs),
            "last_error": errs[0].get("message") if errs else None,
            "last_error_at": errs[0].get("at") if errs else None,
        })
    pending.sort(key=lambda p: (not p["retry"], p.get("latest_signal_at") or ""))
    return {
        "pending_count": len(pending),
        "retry_count": sum(1 for p in pending if p["retry"]),
        "pending": pending,
        "accepted_not_eligible": len(not_eligible),
        "recent_runs": [{
            "run_id": r.get("ingestion_run_id"), "started_at": r.get("started_at"),
            "completed_at": r.get("completed_at"), "status": r.get("status"),
            "mode": (r.get("request_parameters") or {}).get("mode"),
            "requested_by": r.get("requested_by"),
            "created": r.get("inserted_count") or 0, "merged": r.get("updated_count") or 0,
            "no_candidate": r.get("skipped_count") or 0, "failed": r.get("error_count") or 0,
            "error_summary": r.get("error_summary"),
        } for r in runs[:5]],
    }


# ---------------------------------------------------------------------------
# Theme Candidate
# ---------------------------------------------------------------------------

def build_theme_candidates(client, *, is_demo: bool = False, now: datetime | None = None) -> dict:
    now = now or _now()
    rows = client.select("geo_theme_candidates", {
        "select": "candidate_id,status,generation_model,created_at",
        "is_demo": f"eq.{str(is_demo).lower()}", "limit": "5000"})
    by_status: dict = {}
    by_origin: dict = {}
    cutoff = now - timedelta(days=RECENT_DAYS)
    new_recent = 0
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        o = explorer.candidate_origin(r)
        by_origin[o] = by_origin.get(o, 0) + 1
        created = _parse(r.get("created_at"))
        if r["status"] == "NEW" and created and created >= cutoff:
            new_recent += 1
    return {"total": len(rows), "by_status": by_status, "by_origin": by_origin,
            "new_recent": new_recent, "recent_days": RECENT_DAYS,
            "active": len(rows) - by_status.get("DISMISSED", 0)}


# ---------------------------------------------------------------------------
# Monitoring / Alert
# ---------------------------------------------------------------------------

def build_monitoring(client) -> dict:
    themes = client.select("geo_monitoring_themes", {
        "select": "monitoring_theme_id,display_name,status", "status": "eq.active", "limit": "500"})
    name_by_id = {t["monitoring_theme_id"]: t["display_name"] for t in themes}
    alerts: list = []
    if name_by_id:
        ids = ",".join(name_by_id)
        alerts = client.select("geo_monitoring_alerts", {
            "select": "alert_id,monitoring_theme_id,severity,alert_type,title,reason,detected_at",
            "monitoring_theme_id": f"in.({ids})", "acknowledged_at": "is.null",
            "order": "detected_at.desc", "limit": "200"})
    alerts.sort(key=lambda a: (SEVERITY_RANK.get(a.get("severity"), 3), _neg_ts(a.get("detected_at"))))
    top = [{
        "alert_id": a["alert_id"], "monitoring_theme_id": a["monitoring_theme_id"],
        "theme_name": name_by_id.get(a["monitoring_theme_id"]), "severity": a.get("severity"),
        "title": a.get("title"), "reason": a.get("reason"), "detected_at": a.get("detected_at"),
    } for a in alerts[:ALERT_ITEM_LIMIT]]
    freshness = lib.get_global_freshness(client)
    return {
        "active_themes": len(themes), "unacknowledged_alerts": len(alerts),
        "high_alerts": sum(1 for a in alerts if a.get("severity") == "high"),
        "alerts": top, "data_freshness": freshness,
    }


def _neg_ts(value) -> float:
    dt = _parse(value)
    return -dt.timestamp() if dt else 0.0


# ---------------------------------------------------------------------------
# Collection（Phase A のCollection Reliabilityを利用）
# ---------------------------------------------------------------------------

def build_collection(client, *, now: datetime | None = None) -> dict:
    now = now or _now()
    # 実行ログは1回だけ読み、Collection表示用（内部行を除く）とScheduler判定用（全行）に使い分ける
    all_runs = scheduler_tick.load_recent_runs(client, now=now, days=RUN_LOOKBACK_DAYS)
    runs = [r for r in all_runs if r.get("connector_type") not in _NON_COLLECTION_CONNECTORS]

    sources = client.select("geo_source_registry", {
        "select": "*", "archived_at": "is.null", "limit": "1000"})
    # 自動収集の対象 = automation_allowed のSourceのみ（connector_enabledは手動専用Sourceも既定でtrue）。
    # 手動専用のSource（引用由来など）は「更新停止」とは見なさない
    auto = [s for s in sources if s.get("automation_allowed")]

    problems = []
    fresh = never_run = 0
    for s in auto:
        f = geo_freshness_service.classify_source_freshness(s, now=now)
        if f["status"] in ("overdue", "failing"):
            problems.append({
                "kind": "stale" if f["status"] == "overdue" else "failed",
                "name": s.get("name"), "source_id": s["source_id"],
                "last_success_at": s.get("last_successful_ingestion_at"),
                "hours_since_success": _hours_since(_parse(s.get("last_successful_ingestion_at")), now),
                "failure_count": f["failure_count"]})
        elif f["status"] == "unknown":
            never_run += 1
        else:
            fresh += 1

    registry = geo_scheduler_service.load_job_registry()
    overview = scheduler_tick.scheduler_overview(client, registry=registry, now=now)
    overview_stale = overview["stale_running"]

    # 実行中 / 最新が失敗のrun（同じconnector+sourceの最新runのみ見る）
    latest: dict = {}
    for r in runs:                       # 新しい順
        latest.setdefault((r["connector_type"], r.get("source_id")), r)
    running, failed_runs = [], []
    stale_ids = {x["ingestion_run_id"] for x in overview_stale}
    for r in latest.values():
        started = _parse(r.get("started_at"))
        if r["status"] in ("running", "queued"):
            hrs = _hours_since(started, now)
            is_stale = r["ingestion_run_id"] in stale_ids          # そのJobの上限時間を超えた=止まっている
            running.append({"connector": r["connector_type"], "started_at": r.get("started_at"),
                            "hours_running": hrs, "stale": is_stale,
                            "too_long": is_stale or bool(hrs and hrs > RUNNING_TOO_LONG_HOURS)})
        elif r["status"] == "failed":
            failed_runs.append({"connector": r["connector_type"], "at": r.get("started_at"),
                                "message": r.get("error_summary")})

    freshness = lib.get_global_freshness(client)
    last_any = _parse(runs[0]["started_at"]) if runs else None

    return {
        "data_freshness": freshness,
        "auto_sources": len(auto), "fresh_sources": fresh, "never_run_sources": never_run,
        "problem_sources": problems, "running": running, "failed_runs": failed_runs,
        "scheduler": {
            "configured": overview["configured"], "healthy": overview["healthy"], "status": overview["status"],
            "last_heartbeat_at": overview["last_heartbeat_at"], "heartbeat_age_minutes": overview["age_minutes"],
            "healthy_within_minutes": overview["healthy_within_minutes"],
            "last_run_at": overview["last_heartbeat_at"], "last_any_run_at": _iso(last_any),
            "next_expected_at": overview["next_expected_at"], "note": overview["note"],
            "jobs": overview["jobs"],
            "gdelt_doc": overview.get("gdelt_doc"),       # GDELT DOCの全体レート制御（429のクールダウン中か等）
        },
        "stale_running": overview["stale_running"],
    }


# ---------------------------------------------------------------------------
# 組み立て
# ---------------------------------------------------------------------------

def _section(errors: dict, key: str, fn):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — 部分障害: 他のセクションは返す
        errors[key] = f"{type(exc).__name__}: {str(exc)[:160]}"
        return None


def build_health(sec: dict) -> dict:
    """上部のPipeline Health（収集 / 候補整理 / レビュー / テーマ化 / モニタリング）。"""
    h: dict = {}
    col = sec.get("collection")
    if col is None:
        h["collection"] = _state("unknown", "取得できません")
    else:
        fresh = col["data_freshness"]
        if fresh.get("status") == "stale":
            h["collection"] = _state("stopped", "停止", fresh.get("label", ""))
        elif col["problem_sources"] or col["failed_runs"] or col.get("stale_running") or (
                col["scheduler"]["configured"] and not col["scheduler"]["healthy"]):
            h["collection"] = _state("attention", "要対応",
                                     f"更新停止/失敗 {len(col['problem_sources']) + len(col['failed_runs'])}件")
        else:
            h["collection"] = _state("ok", "正常", fresh.get("label", ""))
    rev = sec.get("review")
    h["episode"] = _state("unknown", "取得できません") if rev is None else _state(
        "ok", f"{rev['total_clusters']}件", "候補整理済みの出来事のまとまり")
    h["review"] = _state("unknown", "取得できません") if rev is None else (
        _state("attention", f"{rev['pending']}件待ち") if rev["pending"] else _state("ok", "待ちなし"))
    ts = sec.get("theme_sync")
    h["theme_sync"] = _state("unknown", "取得できません") if ts is None else (
        _state("attention", f"{ts['pending_count']}件待ち",
               f"うち再試行 {ts['retry_count']}件" if ts["retry_count"] else "") if ts["pending_count"]
        else _state("ok", "待ちなし"))
    mon = sec.get("monitoring")
    if mon is None:
        h["monitoring"] = _state("unknown", "取得できません")
    else:
        detail = f"{mon['active_themes']}テーマ / 未確認Alert {mon['unacknowledged_alerts']}件"
        stale = mon["data_freshness"].get("status") == "stale"
        h["monitoring"] = _state("stopped" if stale else ("attention" if mon["unacknowledged_alerts"] else "ok"),
                                 "データ停止" if stale else ("要確認" if mon["unacknowledged_alerts"] else "正常"),
                                 detail)
    return h


def build_action_queue(sec: dict) -> list:
    q = []
    mon, ts, rev, col = sec.get("monitoring"), sec.get("theme_sync"), sec.get("review"), sec.get("collection")
    if mon and mon["unacknowledged_alerts"]:
        first = mon["alerts"][0] if mon["alerts"] else None
        q.append({"key": "alerts", "severity": "high" if mon["high_alerts"] else "medium",
                  "count": mon["unacknowledged_alerts"],
                  "title": f"{mon['unacknowledged_alerts']}件の未確認Alertがあります",
                  "detail": (f"重要 {mon['high_alerts']}件 / 最新: {first['theme_name']}" if first else ""),
                  "cta": {"kind": "link", "label": "確認する",
                          "to": f"/geo/monitoring-themes/{first['monitoring_theme_id']}" if first
                          else "/geo/monitoring-themes"}})
    if col:
        stopped = col["data_freshness"].get("status") == "stale"
        n_prob = len(col["problem_sources"]) + len(col["failed_runs"])
        if stopped or n_prob:
            names = [p["name"] for p in col["problem_sources"][:3]] + [f["connector"] for f in col["failed_runs"][:2]]
            q.append({"key": "collection", "severity": "high" if stopped else "medium", "count": max(n_prob, 1),
                      "title": "情報収集が止まっています" if stopped else f"{n_prob}件のSourceで更新が止まっています",
                      "detail": col["data_freshness"].get("label", "") if stopped else "、".join(names),
                      "cta": {"kind": "anchor", "label": "確認する", "to": "collection"}})
    if col:
        sr = col.get("stale_running") or []
        if sr:
            q.append({"key": "stale_running", "severity": "medium", "count": len(sr),
                      "title": f"{len(sr)}件の取得処理が「実行中」のまま止まっています",
                      "detail": "、".join(sorted({x["job_id"] for x in sr})) + "（Schedulerの次回tick、または recover-stale で補正されます）",
                      "cta": {"kind": "anchor", "label": "確認する", "to": "collection"}})
        sch = col.get("scheduler") or {}
        if sch and not sch.get("configured"):
            q.append({"key": "scheduler", "severity": "low", "count": 1,
                      "title": "定期実行（Scheduler）が未登録です",
                      "detail": "収集は手動実行です。本番サーバへの登録待ち（Runbook参照）",
                      "cta": {"kind": "anchor", "label": "確認する", "to": "collection"}})
        elif sch and not sch.get("healthy"):
            q.append({"key": "scheduler", "severity": "medium", "count": 1,
                      "title": "定期実行（Scheduler）が止まっています",
                      "detail": f"最終heartbeatは{sch.get('heartbeat_age_minutes')}分前です。timer/cronを確認してください",
                      "cta": {"kind": "anchor", "label": "確認する", "to": "collection"}})
    if ts and ts["pending_count"]:
        q.append({"key": "theme_sync", "severity": "medium", "count": ts["pending_count"],
                  "title": f"{ts['pending_count']}件の承認済みがテーマ未生成です"
                           + (f"（うち{ts['retry_count']}件は前回失敗、再試行できます）" if ts["retry_count"] else ""),
                  "detail": "", "cta": {"kind": "action", "label": "Theme Candidateを生成", "to": "theme_sync"}})
    if rev and rev["pending"]:
        q.append({"key": "review", "severity": "medium", "count": rev["pending"],
                  "title": f"{rev['pending']}件のまとまりがレビュー待ちです",
                  "detail": f"最も古い待ち: {rev['oldest_waiting_days']}日前" if rev["oldest_waiting_days"] is not None else "",
                  "cta": {"kind": "link", "label": "レビューする", "to": "/geo/episode-candidates"}})
    q.sort(key=lambda x: SEVERITY_RANK.get(x["severity"], 3))
    return q


def build_daily(sec: dict) -> dict:
    rev, ts, tc, mon, col = (sec.get(k) for k in ("review", "theme_sync", "theme_candidates", "monitoring", "collection"))
    return {
        "review_pending": rev["pending"] if rev else None,
        "theme_sync_pending": ts["pending_count"] if ts else None,
        "new_themes": tc["new_recent"] if tc else None,
        "unacknowledged_alerts": mon["unacknowledged_alerts"] if mon else None,
        "stale_sources": (len(col["problem_sources"]) if col else None),
    }


def get_operations_summary(client, *, is_demo: bool = False) -> dict:
    now = _now()
    errors: dict = {}
    sec = {
        "review": _section(errors, "review", lambda: build_review(client, is_demo=is_demo, now=now)),
        "theme_sync": _section(errors, "theme_sync", lambda: build_theme_sync(client, is_demo=is_demo)),
        "theme_candidates": _section(errors, "theme_candidates",
                                     lambda: build_theme_candidates(client, is_demo=is_demo, now=now)),
        "monitoring": _section(errors, "monitoring", lambda: build_monitoring(client)),
        "collection": _section(errors, "collection", lambda: build_collection(client, now=now)),
    }
    return {
        "version": SUMMARY_VERSION, "generated_at": now.isoformat(), "is_demo": is_demo,
        "health": build_health(sec), "action_queue": build_action_queue(sec), "daily": build_daily(sec),
        **sec, "errors": errors,
    }
