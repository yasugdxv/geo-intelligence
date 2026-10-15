"""Phase E: Daily Intelligence Brief（「今日どこを見るべきか」）の集計。

方針（ユーザー指示）:
- 新しい分析AIは作らない。既存のインテリジェンス資産（Monitoring Alert / Theme Candidate /
  Episode Review backlog / Theme Sync backlog / Collection health）を、今日見るべき順序に
  整理するだけ。LLMは一切呼ばない（既存のLLM生成済みフィールド——Alertのtitle/reason、
  Theme Candidateのtitle/question等——をそのまま使う。要約の再生成はしない）。
- 「運用上どうか（Operations）」ではなく「何を見るべきか（Daily Brief）」が目的。重複する
  集計はgeo_operations_service（Phase C/D、別画面）をそのまま再利用し、ここで再実装しない。
- 選定は決定論的固定優先順位（Critical/High Alert → 新規Theme Candidate → accepted未Sync →
  Evidence多いReview backlog → Collection failure → stale source）。LLMに自由に選ばせない。
- 重複抑制は既存の参照ID（geo_monitoring_themes.theme_candidate_id）だけで行う。新しい
  突き合わせ（LLM matching）はしない。
- Evidence（記事タイトル・Source・公開日・URL）は既存の根拠参照（source_external_signal_ids /
  monitoring_alerts.related_signal_ids）からgeo_external_signalsを引くだけ——複製しない。
- DB migrationは行わない。Seen/Unseenは既存のstatus（NEW）・acknowledged_atをそのまま使う。
  「前回確認以降」はクライアント側（localStorage）のタイムスタンプをsinceとして受け取る任意
  パラメータにとどめ、無指定時は直近24時間をフォールバックにする（新規保存列は増やさない）。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import geo_operations_service as ops
import geo_theme_explorer_service as explorer

BRIEF_VERSION = "daily_intelligence_brief_v1"

FALLBACK_WINDOW_HOURS = 24
NEW_THEME_LIMIT = 5
HIGH_EVIDENCE_THRESHOLD = 5     # Episode ClusterのEvidence（member_count）がこれ以上なら「多い」扱い
POINT_LIMIT = 5                 # 今日のポイント
MANAGER_LIMIT = 3               # マネージャー向けSummary

# 一部のセクション単位が失敗しても全体は返す。LLMは呼ばないため、ここで起きる例外はDB/ネットワーク由来のみ。
_CACHE: dict[bool, tuple[str, dict]] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        return ops._parse(value)
    except Exception:  # noqa: BLE001
        return None


def _is_new_since(ts: str | None, cutoff: datetime) -> bool:
    dt = _parse(ts)
    return bool(dt and dt >= cutoff)


# ---------------------------------------------------------------------------
# Evidence（既存の根拠参照からgeo_external_signalsを引くだけ）
# ---------------------------------------------------------------------------

def _fetch_signals(client, signal_ids: list[str], *, per_item: int = 3) -> dict[str, dict]:
    ids = [i for i in dict.fromkeys(signal_ids) if i]
    found: dict = {}
    for i in range(0, len(ids), 80):
        chunk = ids[i:i + 80]
        try:
            for a in client.select("geo_external_signals", {
                    "select": "external_signal_id,title,url,source_name,source_domain,published_at,created_at",
                    "external_signal_id": "in.(" + ",".join(chunk) + ")", "limit": str(len(chunk))}):
                found[a["external_signal_id"]] = a
        except Exception:  # noqa: BLE001 — Evidence取得失敗でBrief全体を止めない
            pass
    return found


def _evidence_list(ids: list[str] | None, signal_by_id: dict, *, limit: int = 3) -> list[dict]:
    arts = [signal_by_id[i] for i in (ids or []) if i in signal_by_id]
    arts.sort(key=lambda a: a.get("published_at") or a.get("created_at") or "", reverse=True)
    return [{
        "title": a.get("title"), "url": a.get("url"),
        "source": a.get("source_name") or a.get("source_domain"),
        "published_at": a.get("published_at") or a.get("created_at"),
    } for a in arts[:limit]]


# ---------------------------------------------------------------------------
# 新しく見るべきテーマ（Theme Candidate、NEW = 未確認のまま再利用）
# ---------------------------------------------------------------------------

def _build_new_themes(client, *, is_demo: bool, cutoff: datetime,
                       suppress_candidate_ids: set[str]) -> dict:
    rows = client.select("geo_theme_candidates", {
        "select": "candidate_id,title,question,actors,regions,themes,generation_model,"
                  "created_at,latest_signal_at,source_external_signal_ids",
        "status": "eq.NEW", "is_demo": f"eq.{str(is_demo).lower()}",
        "order": "created_at.desc", "limit": "200"})
    for r in rows:
        explorer.decorate_candidate(r)
    signal_by_id = _fetch_signals(
        client, [sid for r in rows for sid in (r.get("source_external_signal_ids") or [])[:40]])

    by_origin: dict[str, int] = {}
    items = []
    for r in rows:
        by_origin[r["candidate_origin"]] = by_origin.get(r["candidate_origin"], 0) + 1
        if r["candidate_id"] in suppress_candidate_ids:
            continue
        if len(items) >= NEW_THEME_LIMIT:
            continue
        items.append({
            "candidate_id": r["candidate_id"], "title": r["title"], "question": r.get("question"),
            "origin": r["candidate_origin"], "origin_label": r["candidate_origin_label"],
            "actors": (r.get("actors") or [])[:4], "regions": (r.get("regions") or [])[:3],
            "created_at": r.get("created_at"), "latest_signal_at": r.get("latest_signal_at"),
            "is_new_since_last_visit": _is_new_since(r.get("created_at"), cutoff),
            "evidence": _evidence_list(r.get("source_external_signal_ids"), signal_by_id),
        })
    return {"total_new": len(rows), "by_origin": by_origin, "items": items,
            "suppressed_as_duplicate": len(suppress_candidate_ids & {r["candidate_id"] for r in rows})}


# ---------------------------------------------------------------------------
# 重要な変化（Monitoring Alert、acknowledged_at=null=未確認のまま再利用）
# ---------------------------------------------------------------------------

def _build_alerts(client, monitoring: dict, *, cutoff: datetime) -> tuple[dict, set[str]]:
    """戻り値: (alerts section, このAlertが指すTheme CandidateのID集合=重複抑制用)"""
    alerts = monitoring.get("alerts") or []
    if not alerts:
        return {"unacknowledged_count": 0, "high_count": 0, "items": []}, set()

    alert_ids = [a["alert_id"] for a in alerts]
    detail_by_id: dict = {}
    for i in range(0, len(alert_ids), 80):
        chunk = alert_ids[i:i + 80]
        for a in client.select("geo_monitoring_alerts", {
                "select": "alert_id,related_signal_ids", "alert_id": "in.(" + ",".join(chunk) + ")",
                "limit": str(len(chunk))}):
            detail_by_id[a["alert_id"]] = a.get("related_signal_ids") or []

    theme_ids = list({a["monitoring_theme_id"] for a in alerts})
    theme_candidate_by_theme: dict = {}
    if theme_ids:
        rows = client.select("geo_monitoring_themes", {
            "select": "monitoring_theme_id,theme_candidate_id",
            "monitoring_theme_id": "in.(" + ",".join(theme_ids) + ")", "limit": str(len(theme_ids))})
        theme_candidate_by_theme = {r["monitoring_theme_id"]: r.get("theme_candidate_id") for r in rows}

    signal_by_id = _fetch_signals(client, [sid for ids in detail_by_id.values() for sid in ids[:40]])

    dup_candidate_ids: set[str] = set()
    items = []
    for a in alerts:
        related_candidate_id = theme_candidate_by_theme.get(a["monitoring_theme_id"])
        if related_candidate_id:
            dup_candidate_ids.add(related_candidate_id)
        items.append({
            "alert_id": a["alert_id"], "monitoring_theme_id": a["monitoring_theme_id"],
            "theme_name": a.get("theme_name"), "severity": a.get("severity"),
            "title": a.get("title"), "reason": a.get("reason"), "detected_at": a.get("detected_at"),
            "is_new_since_last_visit": _is_new_since(a.get("detected_at"), cutoff),
            "related_theme_candidate_id": related_candidate_id,
            "evidence": _evidence_list(detail_by_id.get(a["alert_id"]), signal_by_id),
        })
    return {"unacknowledged_count": monitoring["unacknowledged_alerts"],
            "high_count": monitoring["high_alerts"], "items": items}, dup_candidate_ids


# ---------------------------------------------------------------------------
# レビュー待ち / Theme同期待ち（件数サマリのみ、全件は出さない）
# ---------------------------------------------------------------------------

def _build_review_summary(client, review: dict, *, is_demo: bool) -> dict:
    rows = client.select("geo_episode_clusters", {
        "select": "member_count", "cluster_status": "eq.candidate",
        "is_demo": f"eq.{str(is_demo).lower()}", "limit": "5000"})
    high_evidence = sum(1 for r in rows if (r.get("member_count") or 0) >= HIGH_EVIDENCE_THRESHOLD)
    return {
        "pending": review["pending"], "high_evidence_count": high_evidence,
        "oldest_waiting_days": review["oldest_waiting_days"],
        "summary_text": (
            f"レビュー待ち{review['pending']}件"
            + (f"、うちEvidenceが多い候補{high_evidence}件" if high_evidence else "")
            + (f"、最古{review['oldest_waiting_days']}日前" if review["oldest_waiting_days"] is not None else "")
        ) if review["pending"] else "レビュー待ちなし",
    }


def _build_theme_sync_summary(theme_sync: dict) -> dict:
    return {
        "pending": theme_sync["pending_count"], "retry": theme_sync["retry_count"],
        "summary_text": (
            f"承認済みのうち{theme_sync['pending_count']}件がテーマ未生成です"
            + (f"（うち{theme_sync['retry_count']}件は前回失敗）" if theme_sync["retry_count"] else "")
        ) if theme_sync["pending_count"] else "Theme同期待ちなし",
    }


# ---------------------------------------------------------------------------
# システム上の注意（異常が無い限り目立たせない）
# ---------------------------------------------------------------------------

def _build_operations_notice(collection: dict) -> dict:
    problems = (collection.get("problem_sources") or []) + (collection.get("failed_runs") or [])
    stale_running = collection.get("stale_running") or []
    scheduler = collection.get("scheduler") or {}
    scheduler_issue = bool(scheduler.get("configured")) and not scheduler.get("healthy")
    data_stopped = collection["data_freshness"].get("status") == "stale"
    has_issue = bool(problems or stale_running or scheduler_issue or data_stopped)
    if not has_issue:
        return {"has_issue": False, "summary_text": "運用上の異常なし", "items": []}
    items = []
    if data_stopped:
        items.append({"kind": "data_stopped", "detail": collection["data_freshness"].get("label", "")})
    for p in problems[:5]:
        items.append({"kind": p.get("kind", "problem"), "detail": p.get("name") or p.get("connector")})
    if stale_running:
        items.append({"kind": "stale_running", "detail": f"{len(stale_running)}件の取得処理が停滞"})
    if scheduler_issue:
        items.append({"kind": "scheduler", "detail": "定期実行(Scheduler)のheartbeatが古い"})
    return {"has_issue": True,
            "summary_text": f"要確認: {len(items)}件の運用上の注意事項があります", "items": items}


# ---------------------------------------------------------------------------
# 今日のポイント / マネージャー向けSummary（決定論的固定優先順位）
# ---------------------------------------------------------------------------

def _build_points(alerts: dict, new_themes: dict, theme_sync: dict,
                   review: dict, operations_notice: dict) -> list[dict]:
    points = []
    if alerts["high_count"]:
        points.append({"category": "alert", "severity": "high",
                       "text": f"重要な変化が{alerts['high_count']}件あります（要確認のMonitoring Alert）"})
    elif alerts["unacknowledged_count"]:
        points.append({"category": "alert", "severity": "medium",
                       "text": f"未確認のMonitoring Alertが{alerts['unacknowledged_count']}件あります"})
    if new_themes["items"]:
        points.append({"category": "new_theme", "severity": "medium",
                       "text": f"新しく見るべきテーマが{len(new_themes['items'])}件あります"
                                f"（うち記事から発見{new_themes['by_origin'].get('article_discovery', 0)}件）"})
    if theme_sync["pending"]:
        points.append({"category": "theme_sync", "severity": "low" if not theme_sync["retry"] else "medium",
                       "text": f"承認済みで未テーマ化が{theme_sync['pending']}件あります"})
    if review["high_evidence_count"]:
        points.append({"category": "review", "severity": "low",
                       "text": f"レビュー待ちのうちEvidenceが多い候補が{review['high_evidence_count']}件あります"})
    if operations_notice["has_issue"]:
        points.append({"category": "operations", "severity": "medium", "text": operations_notice["summary_text"]})
    if review["pending"] and not review["high_evidence_count"]:
        points.append({"category": "review", "severity": "low",
                       "text": f"レビュー待ちが{review['pending']}件あります"
                                + (f"（最古{review['oldest_waiting_days']}日前）"
                                   if review["oldest_waiting_days"] is not None else "")})
    return points[:POINT_LIMIT]


def _build_manager_summary(points: list[dict]) -> list[str]:
    # ポイントの文言をそのまま使う（AIが経営影響を新たに作らない）
    return [p["text"] for p in points[:MANAGER_LIMIT]]


# ---------------------------------------------------------------------------
# 組み立て
# ---------------------------------------------------------------------------

def _fingerprint(sec: dict, new_theme_rows_sig: str) -> str:
    rev, ts, mon, col = sec.get("review"), sec.get("theme_sync"), sec.get("monitoring"), sec.get("collection")
    payload = {
        "review_pending": rev and rev["pending"], "review_oldest": rev and rev["oldest_waiting_at"],
        "theme_sync_pending": ts and ts["pending_count"],
        "alerts": mon and [(a["alert_id"], a["detected_at"]) for a in mon["alerts"]],
        "collection_problems": col and len(col.get("problem_sources") or []) + len(col.get("failed_runs") or []),
        "scheduler_healthy": col and (col.get("scheduler") or {}).get("healthy"),
        "new_themes": new_theme_rows_sig,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def build_brief(client, *, is_demo: bool = False, since: str | None = None,
                 now: datetime | None = None) -> dict:
    now = now or _now()
    cutoff = _parse(since) or (now - timedelta(hours=FALLBACK_WINDOW_HOURS))

    errors: dict = {}
    summary = ops.get_operations_summary(client, is_demo=is_demo)
    sec = {k: summary.get(k) for k in ("review", "theme_sync", "theme_candidates", "monitoring", "collection")}
    errors.update(summary.get("errors") or {})

    monitoring = sec.get("monitoring")
    alerts, dup_candidate_ids = ({"unacknowledged_count": 0, "high_count": 0, "items": []}, set())
    if monitoring is not None:
        try:
            alerts, dup_candidate_ids = _build_alerts(client, monitoring, cutoff=cutoff)
        except Exception as exc:  # noqa: BLE001
            errors["alerts"] = f"{type(exc).__name__}: {str(exc)[:160]}"

    new_themes = {"total_new": 0, "by_origin": {}, "items": [], "suppressed_as_duplicate": 0}
    new_theme_sig = ""
    try:
        new_themes = _build_new_themes(client, is_demo=is_demo, cutoff=cutoff,
                                        suppress_candidate_ids=dup_candidate_ids)
        new_theme_sig = str((new_themes["total_new"],
                            [i["candidate_id"] for i in new_themes["items"]]))
    except Exception as exc:  # noqa: BLE001
        errors["new_themes"] = f"{type(exc).__name__}: {str(exc)[:160]}"

    review_summary = {"pending": 0, "high_evidence_count": 0, "oldest_waiting_days": None,
                       "summary_text": "レビュー待ちなし"}
    if sec.get("review") is not None:
        try:
            review_summary = _build_review_summary(client, sec["review"], is_demo=is_demo)
        except Exception as exc:  # noqa: BLE001
            errors["review_summary"] = f"{type(exc).__name__}: {str(exc)[:160]}"

    theme_sync_summary = _build_theme_sync_summary(sec["theme_sync"]) if sec.get("theme_sync") else {
        "pending": 0, "retry": 0, "summary_text": "Theme同期待ちなし"}

    operations_notice = _build_operations_notice(sec["collection"]) if sec.get("collection") else {
        "has_issue": False, "summary_text": "取得できません", "items": []}

    points = _build_points(alerts, new_themes, theme_sync_summary, review_summary, operations_notice)
    manager_summary = _build_manager_summary(points)

    is_empty = not (alerts["items"] or new_themes["items"] or review_summary["pending"]
                     or theme_sync_summary["pending"] or operations_notice["has_issue"])

    payload = {
        "version": BRIEF_VERSION, "generated_at": now.isoformat(), "is_demo": is_demo,
        "window_since": cutoff.isoformat(), "window_is_fallback": since is None,
        "points": points, "manager_summary": manager_summary,
        "changes": alerts, "new_themes": new_themes,
        "review": review_summary, "theme_sync": theme_sync_summary,
        "operations_notice": operations_notice,
        "is_empty": is_empty, "errors": errors,
    }

    fp = _fingerprint(sec, new_theme_sig)
    payload["fingerprint"] = fp
    _CACHE[is_demo] = (fp, payload)
    return payload


def get_daily_brief(client, *, is_demo: bool = False, since: str | None = None) -> dict:
    return build_brief(client, is_demo=is_demo, since=since)
