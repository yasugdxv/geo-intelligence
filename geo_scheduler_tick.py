"""Phase D: Scheduler tick（「今やるべきJobを判定して実行する」1回分の処理）。

OS側のtimer/cron（systemd timer等）が一定間隔でこのtickを呼ぶ。tick自体は常駐せず、毎回
「DBの状態 → 今やるべきJobは何か」を決めて実行し、終了する（再起動に強い、状態は全てDB）。

既存のConnector実行ロジック・catch-up・重複防止・Episode Cluster・Theme生成には触れず、
geo_scheduler_service（Job定義の読み込み・lock・stale補正・実行）を呼ぶだけの薄い層。

新しいDB tableは作らない。状態は geo_ingestion_runs の次の2種類の行だけで持つ:
- connector_type='scheduler_job'       : Job1回の実行結果（retry判定・「Schedulerから見たJobの成否」）
- connector_type='scheduler_heartbeat' : tickが生きている印（1行だけを更新し続ける、行は増えない）

Human Gate: ここから実行できるのはjobs（収集系）だけ。Episode ClusterのAccept・
Theme Candidate Syncは一切実行しない（manual_only_jobsはrun_jobが拒否する）。
LLMは使わない。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import geo_connector_base as base
import geo_gdelt_rate_limit as gdelt_rl
import geo_scheduler_service as svc

JOB_RUN_CONNECTOR_TYPE = "scheduler_job"
HEARTBEAT_CONNECTOR_TYPE = "scheduler_heartbeat"
# Collectionの状態表示（run一覧・失敗/実行中の判定）から除外する、Scheduler自身の内部行
INTERNAL_CONNECTOR_TYPES = frozenset({JOB_RUN_CONNECTOR_TYPE, HEARTBEAT_CONNECTOR_TYPE, "theme_candidate_sync"})

RUN_LOOKBACK_DAYS = 14
DEFAULT_TICK_INTERVAL_MINUTES = 15
DEFAULT_HEARTBEAT_HEALTHY_MINUTES = 45
DEFAULT_EXHAUSTED_COOLDOWN_HOURS = 6

CADENCE_SECONDS = {
    "every_15_minutes": 15 * 60, "every_30_minutes": 30 * 60, "hourly": 3600, "every_6_hours": 6 * 3600, "daily": 86400,
    "weekly": 7 * 86400, "monthly": 30 * 86400, "quarterly": 91 * 86400, "annual": 365 * 86400,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def cadence_seconds(job: dict) -> int:
    return CADENCE_SECONDS.get(job.get("cadence"), 86400)


def tunables(registry: dict) -> dict:
    d = registry.get("defaults") or {}
    return {
        "tick_interval_minutes": d.get("tick_interval_minutes", DEFAULT_TICK_INTERVAL_MINUTES),
        "heartbeat_healthy_minutes": d.get("heartbeat_healthy_minutes", DEFAULT_HEARTBEAT_HEALTHY_MINUTES),
        "exhausted_cooldown_hours": d.get("exhausted_cooldown_hours", DEFAULT_EXHAUSTED_COOLDOWN_HOURS),
    }


def retry_policy(registry: dict, job: dict) -> tuple[int, int]:
    """(max_retries, backoff_seconds)。retry/backoffの設定はYAMLのこの一箇所（Jobごと→defaults）だけ。"""
    default = (registry.get("defaults") or {}).get("retry_policy") or {}
    own = job.get("retry_policy") or {}
    return (own.get("max_retries", default.get("max_retries", 2)),
            own.get("backoff_seconds", default.get("backoff_seconds", 300)))


# ---------------------------------------------------------------------------
# 状態の読み込み
# ---------------------------------------------------------------------------

def load_recent_runs(client, *, now: datetime, is_demo: bool = False, days: int = RUN_LOOKBACK_DAYS) -> list:
    """直近の実行ログを1回だけ読む（Job数ぶん個別にselectしない）。新しい順。"""
    since = (now - timedelta(days=days)).isoformat()
    return client.select("geo_ingestion_runs", {
        "select": "ingestion_run_id,connector_type,run_type,status,started_at,completed_at,"
                  "error_summary,requested_by,request_parameters,next_scheduled_at,fetched_count,"
                  "inserted_count,error_count",
        "is_demo": f"eq.{str(is_demo).lower()}", "started_at": f"gte.{since}",
        "order": "started_at.desc", "limit": "2000"})


def _ts(row: dict, key: str) -> datetime | None:
    return svc._parse(row.get(key))


def _job_rows(runs: list, job_id: str) -> list:
    return [r for r in runs if r["connector_type"] == JOB_RUN_CONNECTOR_TYPE
            and (r.get("request_parameters") or {}).get("job_id") == job_id]


def find_stale_runs(runs: list, registry: dict, *, now: datetime) -> list:
    """status=runningのまま、そのJobのstale_after_hoursを超えた行を全て返す（最新の1件だけでなく、
    過去に取り残された行も含める）。runningだから永久にskip、という状態を作らない。"""
    out = []
    for job_id, job in registry.get("jobs", {}).items():
        if job.get("enabled") is False:
            continue
        hours = job.get("stale_after_hours", (registry.get("defaults") or {}).get("stale_after_hours", 6))
        cts = set(svc._connector_types_for_job(job)) | {JOB_RUN_CONNECTOR_TYPE}
        for r in runs:
            if r["connector_type"] not in cts or r.get("status") != "running":
                continue
            if r["connector_type"] == JOB_RUN_CONNECTOR_TYPE and (r.get("request_parameters") or {}).get("job_id") != job_id:
                continue
            if svc.is_stale(r, stale_after_hours=hours, now=now):
                out.append({"job_id": job_id, "connector_type": r["connector_type"],
                            "ingestion_run_id": r["ingestion_run_id"], "started_at": r.get("started_at"),
                            "running_hours": round((now - _ts(r, "started_at")).total_seconds() / 3600, 1),
                            "stale_after_hours": hours})
    return out


def recover_stale(client, registry: dict, *, now: datetime, is_demo: bool = False, dry_run: bool = False) -> list:
    """取り残されたrunningを「interrupted（=failed＋STALE_RUNNING_RECOVERED）」へ補正する。
    成功扱いにはしない。dry_runならDBを変更せず、補正対象だけを返す。"""
    stale = find_stale_runs(load_recent_runs(client, now=now, is_demo=is_demo), registry, now=now)
    if not dry_run:
        for s in stale:
            base.complete_ingestion_run(
                client, ingestion_run_id=s["ingestion_run_id"], status="failed",
                error_summary=(f"STALE_RUNNING_RECOVERED: status=runningのまま{s['running_hours']}時間経過"
                               f"（上限{s['stale_after_hours']}時間）。クラッシュ/中断として補正しました"
                               "（成功扱いではありません。再試行可能）"))
    return stale


# ---------------------------------------------------------------------------
# Jobの状態判定（due / running / backoff / exhausted / blocked / not_configured ...）
# ---------------------------------------------------------------------------

def evaluate_job(job_id: str, job: dict, registry: dict, runs: list, *, now: datetime) -> dict:
    out = {"job_id": job_id, "group": job.get("job_type"), "cadence": job.get("cadence"),
           "cadence_hint": job.get("cadence_hint"), "dedicated": bool(job.get("dedicated")),
           "state": "scheduled", "reason": "", "due": False, "last_success_at": None, "next_due_at": None,
           "consecutive_failures": 0, "retry_classification": None, "next_retry_at": None}
    if job.get("enabled") is False:
        return {**out, "state": "disabled", "reason": job.get("disabled_reason", "disabled")}
    if svc.missing_params(job):
        return {**out, "state": "not_configured",
                "reason": f"対象の設定が未入力です（{', '.join(svc.missing_params(job))}）"}

    cts = set(svc._connector_types_for_job(job))
    hours = job.get("stale_after_hours", (registry.get("defaults") or {}).get("stale_after_hours", 6))
    conn_rows = [r for r in runs if r["connector_type"] in cts]
    for r in conn_rows:
        if r.get("status") == "running":
            if svc.is_stale(r, stale_after_hours=hours, now=now):
                return {**out, "state": "stale", "reason": "status=runningのまま長時間経過（回復対象）"}
            return {**out, "state": "running",
                    "reason": f"{r['connector_type']} が実行中（{_ts(r, 'started_at').isoformat()}から）"}

    cadence = timedelta(seconds=cadence_seconds(job))
    ok = ("completed", "completed_with_warnings")
    successes = [_ts(r, "completed_at") or _ts(r, "started_at") for r in conn_rows if r.get("status") in ok]
    jrows = _job_rows(runs, job_id)
    job_successes = [_ts(r, "completed_at") or _ts(r, "started_at") for r in jrows if r.get("status") in ok]
    last_success = max([t for t in successes + job_successes if t], default=None)
    out["last_success_at"] = svc._iso(last_success)
    out["next_due_at"] = svc._iso(last_success + cadence) if last_success else None

    # 失敗の連続（成功より後のscheduler_job行だけを数える）
    fails = []
    for r in jrows:                                     # 新しい順
        if r.get("status") == "failed":
            fails.append(r)
        elif r.get("status") in ok:
            break
    if fails:
        fail_end = _ts(fails[0], "completed_at") or _ts(fails[0], "started_at")
        if fail_end and (last_success is None or fail_end > last_success):
            n = len(fails)
            cls = (fails[0].get("request_parameters") or {}).get("retry") or svc.classify_error(
                fails[0].get("error_summary"))
            out.update(consecutive_failures=n, retry_classification=cls)
            max_retries, backoff = retry_policy(registry, job)
            cooldown = timedelta(hours=tunables(registry)["exhausted_cooldown_hours"])
            if cls == "non_retryable":
                if now - fail_end < cadence:
                    return {**out, "state": "blocked",
                            "reason": "再試行しても解決しない失敗です（設定・認証・スキーマ等）。原因を直してください"}
            elif n > max_retries:
                if now - fail_end < cooldown:
                    return {**out, "state": "exhausted", "next_retry_at": svc._iso(fail_end + cooldown),
                            "reason": f"再試行を{max_retries}回使い切りました。人の確認待ちです"
                                      f"（{cooldown}後に自動で再開）"}
            else:
                retry_at = fail_end + timedelta(seconds=backoff * (2 ** (n - 1)))
                out["next_retry_at"] = svc._iso(retry_at)
                if now < retry_at:
                    return {**out, "state": "backoff", "reason": f"再試行待ち（{n}回目の失敗後、{svc._iso(retry_at)}まで待機）"}
            return {**out, "state": "due", "due": True, "reason": f"再試行（連続失敗{n}回、{cls}）"}

    if last_success is None:
        return {**out, "state": "due", "due": True, "reason": "成功履歴がありません（初回実行）"}
    if now - last_success >= cadence:
        return {**out, "state": "due", "due": True,
                "reason": f"前回成功から{round((now - last_success).total_seconds() / 3600, 1)}時間経過（周期 {job.get('cadence')}）"}
    return {**out, "state": "scheduled", "reason": "周期内（次回予定まで待機）"}


def evaluate_all(registry: dict, runs: list, *, now: datetime) -> list:
    return [evaluate_job(jid, job, registry, runs, now=now) for jid, job in registry.get("jobs", {}).items()]


# ---------------------------------------------------------------------------
# Jobの実行結果 → run logの状態
# ---------------------------------------------------------------------------

def summarize_outcome(run_result: dict) -> dict:
    """svc.run_job()の戻り値を、scheduler_job行の(status, error_summary, counts, retry分類)へ要約する。"""
    oc = run_result.get("outcome")
    if oc == "error":
        msg = run_result.get("error")
        return {"status": "failed", "error": msg, "retry": run_result.get("retry_classification") or "unknown",
                "counts": {"fetched_count": 0, "error_count": 1}}
    if oc != "executed":
        return {"status": "cancelled", "error": run_result.get("reason"), "retry": "none", "counts": {}}
    res = run_result.get("result") or {}
    if "query_results" in res:                                     # GDELT News DOC（queryごとの結果）
        qs = res["query_results"] or []
        bad = [q for q in qs if q.get("status") == "failed"]
        msg = "; ".join(f"{q.get('query_id', '?')}: {q.get('error_summary') or 'failed'}" for q in bad)[:500] or None
        if bad and len(bad) == len(qs):
            return {"status": "failed", "error": msg, "retry": svc.classify_error(msg),
                    "counts": {"fetched_count": len(qs), "error_count": len(bad)}}
        return {"status": "completed_with_warnings" if bad else "completed", "error": msg, "retry": "none",
                "counts": {"fetched_count": len(qs), "error_count": len(bad)}}
    if "stopped_reason" in res:                                    # GDELT raw stream catch-up
        err = res.get("last_error")
        counts = {"fetched_count": res.get("calls_made", 0), "error_count": 1 if err else 0}
        if err:
            return {"status": "failed", "error": str(err)[:500], "retry": svc.classify_error(str(err)), "counts": counts}
        done = res.get("stopped_reason") == "caught_up"
        return {"status": "completed" if done else "completed_with_warnings",
                "error": None if done else f"追いつき途中（{res.get('stopped_reason')}、残り{res.get('remaining_batch_count')}件）。次のtickで継続",
                "retry": "none", "counts": counts}
    st = res.get("status", "completed")                           # IMF / World Bank / OFAC
    if st == "failed":
        return {"status": "failed", "error": str(res.get("error_summary"))[:500], "retry": svc.classify_error(res.get("error_summary")),
                "counts": {"error_count": 1}}
    return {"status": "completed_with_warnings" if st == "completed_with_warnings" else "completed",
            "error": None, "retry": "none", "counts": {}}


def execute_job(client, job_id: str, registry: dict, *, now: datetime, trigger: str,
                requested_by: str = "batch", is_demo: bool = False, force: bool = False) -> dict:
    """1 Jobを実行し、結果をscheduler_job行として記録する（lock・依存・Human Gateはrun_jobが判定）。
    requested_by="batch"はConnector側で run_type='scheduled_external' として記録される値（定期実行の印）。
    手動実行(CLI)は"cli"を渡す。"""
    job = registry.get("jobs", {}).get(job_id)
    if job is None:
        return svc.run_job(client, job_id, registry=registry, is_demo=is_demo, requested_by=requested_by, force=force)
    # 実行しない（lock/依存/未設定）場合に空の行を作らないよう、先に判定する
    if not force:
        if svc.is_job_locked(client, job, is_demo=is_demo, now=now):
            return {"job_id": job_id, "outcome": "skipped", "reason": "locked（既に実行中）"}
        ok, why = svc.check_dependencies_satisfied(client, job, registry=registry, is_demo=is_demo, now=now)
        if not ok:
            return {"job_id": job_id, "outcome": "skipped", "reason": why}
        # GDELT DOCは、定期・catch-up・手動のどれか1つだけが走る（別プロセスの収集中は見送る）
        if job.get("connector_type") == "gdelt" and gdelt_rl.lease_status()["held"]:
            return {"job_id": job_id, "outcome": "skipped", "reason": "locked（GDELT DOC収集が別プロセスで実行中）"}
        # GDELTの429による全体クールダウン中は起動しない（起動しても即失敗するだけで、失敗行が増えるため）
        if job.get("connector_type") == "gdelt":
            remaining = gdelt_rl.get_state()["cooldown_remaining_seconds"]
            if remaining > gdelt_rl.MAX_COOLDOWN_WAIT_SECONDS:
                return {"job_id": job_id, "outcome": "skipped",
                        "reason": f"GDELT 429のクールダウン中です（あと約{int(remaining // 60) + 1}分）。明けてから自動で再試行します"}
    streak = 0                                                    # 直近の連続失敗数（成功で途切れる）
    for r in _job_rows(load_recent_runs(client, now=now, is_demo=is_demo), job_id):
        if r.get("status") == "failed":
            streak += 1
        elif r.get("status") in ("completed", "completed_with_warnings"):
            break
    attempt = streak + 1
    run = base.create_ingestion_run(
        client, source_id=None, connector_type=JOB_RUN_CONNECTOR_TYPE,
        run_type="scheduled_external" if trigger == "tick" else "cli", requested_by=requested_by,
        request_parameters={"job_id": job_id, "trigger": trigger, "attempt": attempt}, is_demo=is_demo)
    result = svc.run_job(client, job_id, registry=registry, is_demo=is_demo, requested_by=requested_by, force=force)
    if result.get("outcome") == "not_configured":
        # 実行していないので失敗として数えない（設定が入るまでnot_configured）。行は取り消し扱い
        summary = {"status": "cancelled", "error": result.get("reason"), "retry": "none", "counts": {}}
    else:
        summary = summarize_outcome(result)
    base.complete_ingestion_run(client, ingestion_run_id=run["ingestion_run_id"], status=summary["status"],
                                counts=summary["counts"], error_summary=summary["error"])
    client.update("geo_ingestion_runs", {"ingestion_run_id": f"eq.{run['ingestion_run_id']}"}, {
        "request_parameters": {"job_id": job_id, "trigger": trigger, "attempt": attempt,
                               "retry": summary["retry"], "outcome": result.get("outcome")}})
    return {**result, "scheduler_status": summary["status"], "retry": summary["retry"],
            "scheduler_run_id": run["ingestion_run_id"]}


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

def touch_heartbeat(client, *, now: datetime, is_demo: bool = False, info: dict | None = None) -> None:
    """tickが生きている印。geo_ingestion_runsの1行だけを更新し続ける（毎tickで行は増えない）。"""
    rows = client.select("geo_ingestion_runs", {
        "select": "ingestion_run_id", "connector_type": f"eq.{HEARTBEAT_CONNECTOR_TYPE}",
        "is_demo": f"eq.{str(is_demo).lower()}", "limit": "1"})
    params = {"last_tick_at": now.isoformat(), **(info or {})}
    if rows:
        client.update("geo_ingestion_runs", {"ingestion_run_id": f"eq.{rows[0]['ingestion_run_id']}"},
                      {"completed_at": now.isoformat(), "request_parameters": params})
        return
    run = base.create_ingestion_run(client, source_id=None, connector_type=HEARTBEAT_CONNECTOR_TYPE,
                                    run_type="scheduled_external", requested_by="scheduler",
                                    request_parameters=params, is_demo=is_demo)
    base.complete_ingestion_run(client, ingestion_run_id=run["ingestion_run_id"], status="completed")
    client.update("geo_ingestion_runs", {"ingestion_run_id": f"eq.{run['ingestion_run_id']}"},
                  {"completed_at": now.isoformat()})


def get_heartbeat(client, *, now: datetime, registry: dict, is_demo: bool = False) -> dict:
    t = tunables(registry)
    rows = client.select("geo_ingestion_runs", {
        "select": "completed_at,request_parameters", "connector_type": f"eq.{HEARTBEAT_CONNECTOR_TYPE}",
        "is_demo": f"eq.{str(is_demo).lower()}", "limit": "1"})
    if not rows:
        return {"configured": False, "healthy": False, "status": "not_configured", "last_heartbeat_at": None,
                "age_minutes": None, "healthy_within_minutes": t["heartbeat_healthy_minutes"]}
    last = svc._parse((rows[0].get("request_parameters") or {}).get("last_tick_at") or rows[0].get("completed_at"))
    age = (now - last).total_seconds() / 60 if last else None
    healthy = age is not None and age <= t["heartbeat_healthy_minutes"]
    return {"configured": True, "healthy": healthy, "status": "healthy" if healthy else "unhealthy",
            "last_heartbeat_at": svc._iso(last), "age_minutes": None if age is None else round(age, 1),
            "healthy_within_minutes": t["heartbeat_healthy_minutes"]}


# ---------------------------------------------------------------------------
# tick / plan
# ---------------------------------------------------------------------------

def _selected_jobs(registry: dict, only: list | None, include_dedicated: bool) -> list:
    ids = []
    for jid, job in registry.get("jobs", {}).items():
        if only:
            if jid in only:
                ids.append(jid)
        elif include_dedicated or not job.get("dedicated"):
            ids.append(jid)
    return ids


def run_tick(client, *, registry: dict | None = None, now: datetime | None = None, only: list | None = None,
             include_dedicated: bool = False, dry_run: bool = False, is_demo: bool = False,
             heartbeat: bool = True) -> dict:
    """今やるべきJobを判定して実行する。dry_run=TrueはDBを一切変更しない（plan）。

    - 専用枠（dedicated: OFAC等の重いJob）は、`only`で明示された時だけ実行する
      （他Jobの周期をブロックしないよう、別のtimer/プロセスで動かす）。
    - 1 Jobの失敗・skipは他のJobを止めない。
    - heartbeat=Falseは手動・検証用（人が手で実行したtickを「定期実行が動いている」と誤表示しないため。
      OSのtimer/cronから呼ぶ通常のtickは既定のheartbeat=True）。"""
    registry = registry or svc.load_job_registry()
    now = now or _now()
    result: dict = {"tick_at": now.isoformat(), "dry_run": dry_run, "jobs": []}

    beat = heartbeat and not dry_run
    if beat:
        touch_heartbeat(client, now=now, is_demo=is_demo, info={"phase": "start"})
    result["recovered_stale" if not dry_run else "would_recover_stale"] = recover_stale(
        client, registry, now=now, is_demo=is_demo, dry_run=dry_run)

    runs = load_recent_runs(client, now=now, is_demo=is_demo)
    selected = set(_selected_jobs(registry, only, include_dedicated))
    ran = 0
    for ev in evaluate_all(registry, runs, now=now):
        jid = ev["job_id"]
        entry = {**ev, "selected": jid in selected, "action": "none"}
        if jid not in selected:
            entry["action"] = "not_selected"
        elif ev["due"]:
            if dry_run:
                entry["action"] = "would_run"
                entry["catchup_gap"] = svc.compute_catchup_gap(client, jid, registry["jobs"][jid], is_demo=is_demo, now=now)
                entry["produces_phases"] = registry["jobs"][jid].get("produces_phases", [])
            else:
                res = execute_job(client, jid, registry, now=now, trigger="tick", is_demo=is_demo)
                entry["action"] = "ran" if res.get("outcome") == "executed" else res.get("outcome", "skipped")
                entry["outcome"] = res.get("scheduler_status") or res.get("outcome")
                entry["detail"] = res.get("reason") or res.get("error")
                ran += 1 if res.get("outcome") == "executed" else 0
                if beat:
                    touch_heartbeat(client, now=_now(), is_demo=is_demo, info={"phase": "running", "last_job": jid})
        result["jobs"].append(entry)

    if beat:
        touch_heartbeat(client, now=_now(), is_demo=is_demo, info={
            "phase": "idle", "jobs_due": sum(1 for j in result["jobs"] if j.get("due")), "jobs_ran": ran})
    result["jobs_ran"] = ran
    return result


# ---------------------------------------------------------------------------
# Operations画面向けの概況（読み取りのみ）
# ---------------------------------------------------------------------------

def scheduler_overview(client, *, registry: dict | None = None, now: datetime | None = None,
                       is_demo: bool = False) -> dict:
    registry = registry or svc.load_job_registry()
    now = now or _now()
    runs = load_recent_runs(client, now=now, is_demo=is_demo)
    hb = get_heartbeat(client, now=now, registry=registry, is_demo=is_demo)
    jobs = evaluate_all(registry, runs, now=now)
    nxt = [j["next_due_at"] for j in jobs if j["state"] in ("scheduled", "backoff") and (j["next_due_at"] or j["next_retry_at"])]
    nxt += [j["next_retry_at"] for j in jobs if j["state"] == "backoff" and j["next_retry_at"]]
    return {
        **hb,
        "note": ("定期実行(Scheduler)は未登録です。Job定義・Runbook・手動実行は用意済みで、"
                 "本番サーバへの登録待ち（PRODUCTION DEPLOYMENT PENDING）です。") if not hb["configured"]
        else ("定期実行が動いています。" if hb["healthy"] else "定期実行の最終heartbeatが古くなっています。タイマー/cronを確認してください。"),
        "next_expected_at": min([n for n in nxt if n], default=None),
        "jobs": [{k: j[k] for k in ("job_id", "group", "cadence", "state", "reason", "last_success_at", "next_due_at",
                                    "consecutive_failures", "retry_classification", "next_retry_at", "dedicated")}
                 for j in jobs],
        "stale_running": find_stale_runs(runs, registry, now=now),
        # GDELT DOCの全体レート制御の状態（429のクールダウン中か、収集が実行中か）
        "gdelt_doc": {**gdelt_rl.get_state(), "collection_running": gdelt_rl.lease_status()["held"],
                      "min_interval_seconds": gdelt_rl.MIN_INTERVAL_SECONDS},
    }
