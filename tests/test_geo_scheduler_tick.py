"""Phase D: Scheduler tick（期限判定・retry・heartbeat・stale回復・多重起動防止）のテスト。

既存のConnector実行ロジックは呼ばず、Job実行（svc._EXECUTORS）はフェイクに差し替える。
DB状態はFakeSupabaseClient（geo_ingestion_runs）だけで表現する。
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_scheduler_cli as cli  # noqa: E402
import geo_scheduler_service as svc  # noqa: E402
import geo_scheduler_tick as tick  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

NOW = datetime.now(timezone.utc)      # フェイクのinsertがstarted_at=現在時刻を入れるため、実時刻基準で組み立てる

REGISTRY = {
    "defaults": {"retry_policy": {"max_retries": 2, "backoff_seconds": 300}, "stale_after_hours": 6,
                 "heartbeat_healthy_minutes": 45, "exhausted_cooldown_hours": 6},
    "jobs": {
        "COLLECTION_DOC": {"job_type": "collection", "connector_type": "gdelt", "catchup_connector_type": "gdelt_doc_catchup",
                           "cli_command": "gdelt-batch", "cadence": "daily", "depends_on": [], "stale_after_hours": 3},
        "COLLECTION_STREAM": {"job_type": "collection", "connector_type": "gdelt_stream",
                              "catchup_connector_type": "gdelt_stream_catchup", "cli_command": "gdelt-stream-catchup",
                              "cadence": "every_15_minutes", "depends_on": ["COLLECTION_DOC"], "stale_after_hours": 2},
        "COLLECTION_OFAC": {"job_type": "collection", "connector_type": "ofac", "cli_command": "ofac", "cadence": "daily",
                            "dedicated": True, "stale_after_hours": 48, "retry_policy": {"max_retries": 1, "backoff_seconds": 1800}},
        "COLLECTION_IMF": {"job_type": "collection", "connector_type": "imf", "cli_command": "imf", "cadence": "quarterly",
                           "params": {"theme_id": None, "entity_id": None, "country_code": None}},
        "COLLECTION_OFF": {"job_type": "collection", "connector_type": "x", "cadence": "daily", "enabled": False,
                           "disabled_reason": "credential_missing"},
    },
    "manual_only_jobs": {"THEME_CANDIDATE_SYNC": {"job_type": "human_triggered", "connector_type": "theme_candidate_sync"}},
}


def _client(*rows):
    return FakeSupabaseClient(tables={"geo_ingestion_runs": list(rows), "geo_external_signal_queries": []})


def _row(connector_type, started_ago, *, status="completed", minutes_long=5, job_id=None, retry=None, err=None, rid=None):
    started = NOW - started_ago
    params = {"job_id": job_id} if job_id else {}
    if retry:
        params["retry"] = retry
    return {"ingestion_run_id": rid or f"{connector_type}-{job_id or ''}-{int(started_ago.total_seconds())}",
            "connector_type": connector_type, "run_type": "scheduled_external", "status": status,
            "started_at": started.isoformat(),
            "completed_at": None if status == "running" else (started + timedelta(minutes=minutes_long)).isoformat(),
            "error_summary": err, "request_parameters": params, "is_demo": False}


def _job_row(job_id, started_ago, **kw):
    return _row(tick.JOB_RUN_CONNECTOR_TYPE, started_ago, job_id=job_id, **kw)


def _fake_executors(monkeypatch, **fns):
    monkeypatch.setattr(svc, "_EXECUTORS", {k: v for k, v in fns.items()})


def _ev(client, job_id):
    runs = tick.load_recent_runs(client, now=NOW)
    return tick.evaluate_job(job_id, REGISTRY["jobs"][job_id], REGISTRY, runs, now=NOW)


# ---- 期限判定 ----------------------------------------------------------------------

def test_first_run_is_due():
    assert _ev(_client(), "COLLECTION_DOC")["state"] == "due"


def test_within_cadence_is_scheduled_and_after_cadence_is_due():
    assert _ev(_client(_row("gdelt", timedelta(hours=3))), "COLLECTION_DOC")["state"] == "scheduled"
    assert _ev(_client(_row("gdelt", timedelta(hours=30))), "COLLECTION_DOC")["state"] == "due"


def test_connector_type_success_counts_for_cadence_even_if_run_by_hand():
    """手動CLI/APIの成功も「最後の成功」に数える（直後に二重実行しない）。"""
    c = _client(_row("gdelt_doc_catchup", timedelta(hours=1)))
    assert _ev(c, "COLLECTION_DOC")["state"] == "scheduled"


def test_disabled_and_not_configured_jobs_are_not_due():
    c = _client()
    assert _ev(c, "COLLECTION_OFF")["state"] == "disabled"
    ev = _ev(c, "COLLECTION_IMF")
    assert ev["state"] == "not_configured" and ev["due"] is False


def test_every_15_minutes_cadence():
    c = _client(_row("gdelt_stream", timedelta(minutes=5)))
    assert _ev(c, "COLLECTION_STREAM")["state"] == "scheduled"
    c = _client(_row("gdelt_stream", timedelta(minutes=30)))
    assert _ev(c, "COLLECTION_STREAM")["state"] == "due"


# ---- 重複起動防止 -------------------------------------------------------------------

def test_running_job_is_not_launched_again():
    c = _client(_row("gdelt", timedelta(minutes=20), status="running"))
    assert _ev(c, "COLLECTION_DOC")["state"] == "running"
    res = tick.execute_job(c, "COLLECTION_DOC", REGISTRY, now=NOW, trigger="tick")
    assert res["outcome"] == "skipped" and "locked" in res["reason"]
    assert not [r for r in c.tables["geo_ingestion_runs"] if r["connector_type"] == tick.JOB_RUN_CONNECTOR_TYPE]


def test_tick_lock_blocks_second_acquire_and_clears_stale_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "LOCK_DIR", tmp_path)
    a, b = cli.TickLock("t"), cli.TickLock("t")
    assert a.acquire() is True and b.acquire() is False
    a.release()
    assert b.acquire() is True
    b.release()
    # 古い鍵（クラッシュの残り）は自動で無効
    old = cli.TickLock("old")
    assert old.acquire()
    import os
    os.close(old.fd)                      # クラッシュしたプロセスの残骸を再現（開いたままでは消せない）
    old.fd = None
    os.utime(old.path, (0, 0))
    new = cli.TickLock("old")
    assert new.acquire() is True
    new.release()


# ---- stale running recovery ---------------------------------------------------------

def test_stale_running_is_recognised_and_recovered_to_failed_not_success():
    stale = _row("gdelt_stream", timedelta(hours=17), status="running", rid="stale-1")
    c = _client(stale)
    assert _ev(c, "COLLECTION_STREAM")["state"] == "stale"
    rec = tick.recover_stale(c, REGISTRY, now=NOW)
    assert [r["ingestion_run_id"] for r in rec] == ["stale-1"]
    row = c.tables["geo_ingestion_runs"][0]
    assert row["status"] == "failed" and "STALE_RUNNING_RECOVERED" in row["error_summary"]
    # 回復後は永久skipにならず、再び実行できる
    assert _ev(c, "COLLECTION_STREAM")["state"] in ("due", "backoff")


def test_recover_stale_handles_old_rows_beneath_a_newer_running_row_and_leaves_fresh_alone():
    c = _client(_row("gdelt", timedelta(hours=40), status="running", rid="old-stuck"),
                _row("gdelt", timedelta(minutes=5), status="running", rid="live"))
    rec = tick.recover_stale(c, REGISTRY, now=NOW)
    assert [r["ingestion_run_id"] for r in rec] == ["old-stuck"]
    live = next(r for r in c.tables["geo_ingestion_runs"] if r["ingestion_run_id"] == "live")
    assert live["status"] == "running"


def test_recover_stale_dry_run_changes_nothing():
    c = _client(_row("gdelt_stream", timedelta(hours=17), status="running", rid="s"))
    assert len(tick.recover_stale(c, REGISTRY, now=NOW, dry_run=True)) == 1
    assert c.tables["geo_ingestion_runs"][0]["status"] == "running"


# ---- Retry / backoff / 上限 ---------------------------------------------------------

def test_retryable_failure_waits_for_backoff_then_retries():
    c = _client(_job_row("COLLECTION_DOC", timedelta(minutes=2), status="failed", retry="retryable", err="ReadTimeout"))
    ev = _ev(c, "COLLECTION_DOC")
    assert ev["state"] == "backoff" and ev["consecutive_failures"] == 1 and ev["next_retry_at"]
    c = _client(_job_row("COLLECTION_DOC", timedelta(minutes=20), status="failed", retry="retryable", err="ReadTimeout"))
    assert _ev(c, "COLLECTION_DOC")["state"] == "due"       # 300秒(5分)のbackoffを過ぎた


def test_backoff_grows_exponentially_per_consecutive_failure():
    c = _client(_job_row("COLLECTION_DOC", timedelta(minutes=30), status="failed", retry="retryable"),
                _job_row("COLLECTION_DOC", timedelta(minutes=12), status="failed", retry="retryable"))
    ev = _ev(c, "COLLECTION_DOC")      # 2回失敗 → 300*2=600秒=10分待ち。最後の失敗の完了は7分前 → まだbackoff
    assert ev["consecutive_failures"] == 2 and ev["state"] == "backoff"


def test_max_retries_exhausted_stops_automatic_retry_then_cools_down():
    rows = [_job_row("COLLECTION_DOC", timedelta(hours=1, minutes=i * 10), status="failed", retry="retryable")
            for i in range(3)]                                           # 3連続失敗 > max_retries(2)
    ev = _ev(_client(*rows), "COLLECTION_DOC")
    assert ev["state"] == "exhausted" and ev["due"] is False
    old = [_job_row("COLLECTION_DOC", timedelta(hours=8, minutes=i * 10), status="failed", retry="retryable") for i in range(3)]
    assert _ev(_client(*old), "COLLECTION_DOC")["state"] == "due"       # 6時間のcooldown後は再開（無限停止にしない）


def test_non_retryable_failure_is_blocked_until_next_cadence():
    c = _client(_job_row("COLLECTION_DOC", timedelta(hours=2), status="failed", retry="non_retryable", err="credential missing"))
    ev = _ev(c, "COLLECTION_DOC")
    assert ev["state"] == "blocked" and ev["due"] is False


def test_success_resets_failure_streak():
    c = _client(_job_row("COLLECTION_DOC", timedelta(hours=30), status="failed", retry="retryable"),
                _job_row("COLLECTION_DOC", timedelta(hours=26), status="completed"))
    ev = _ev(c, "COLLECTION_DOC")
    assert ev["consecutive_failures"] == 0 and ev["state"] == "due"


def test_retry_policy_is_read_from_one_place_job_then_defaults():
    assert tick.retry_policy(REGISTRY, REGISTRY["jobs"]["COLLECTION_DOC"]) == (2, 300)
    assert tick.retry_policy(REGISTRY, REGISTRY["jobs"]["COLLECTION_OFAC"]) == (1, 1800)


# ---- 実行と記録 --------------------------------------------------------------------

def test_execute_job_records_success_row(monkeypatch):
    _fake_executors(monkeypatch, COLLECTION_DOC=lambda client, job, **kw: {"query_results": [{"status": "completed"}]})
    c = _client()
    res = tick.execute_job(c, "COLLECTION_DOC", REGISTRY, now=NOW, trigger="tick")
    assert res["outcome"] == "executed" and res["scheduler_status"] == "completed"
    row = next(r for r in c.tables["geo_ingestion_runs"] if r["connector_type"] == tick.JOB_RUN_CONNECTOR_TYPE)
    assert row["status"] == "completed" and row["request_parameters"]["job_id"] == "COLLECTION_DOC"


def test_network_failure_is_recorded_as_retryable_and_leaves_no_partial_state(monkeypatch):
    def boom(client, job, **kw):
        raise TimeoutError("ReadTimeout: HTTPSConnectionPool read timed out")
    _fake_executors(monkeypatch, COLLECTION_DOC=boom)
    c = _client()
    res = tick.execute_job(c, "COLLECTION_DOC", REGISTRY, now=NOW, trigger="tick")
    assert res["scheduler_status"] == "failed" and res["retry"] == "retryable"
    assert _ev(c, "COLLECTION_DOC")["state"] == "backoff"


def test_missing_credentials_is_non_retryable(monkeypatch):
    def boom(client, job, **kw):
        raise RuntimeError("ConnectorDisabledError: credential not configured")
    _fake_executors(monkeypatch, COLLECTION_DOC=boom)
    c = _client()
    res = tick.execute_job(c, "COLLECTION_DOC", REGISTRY, now=NOW, trigger="tick")
    assert res["retry"] == "non_retryable"
    assert _ev(c, "COLLECTION_DOC")["state"] == "blocked"


def test_repeated_failures_stop_at_max_retries(monkeypatch):
    calls = {"n": 0}

    def boom(client, job, **kw):
        calls["n"] += 1
        raise TimeoutError("Timeout")
    _fake_executors(monkeypatch, COLLECTION_DOC=boom)
    c = _client()
    for _ in range(6):                       # backoffを無視して連打しても、execute_jobが記録する失敗で状態は進む
        tick.execute_job(c, "COLLECTION_DOC", REGISTRY, now=NOW, trigger="manual", force=True)
    ev = _ev(c, "COLLECTION_DOC")
    assert ev["state"] == "exhausted" and ev["due"] is False      # 以後tickは自動で再試行しない


def test_summarize_outcome_variants():
    s = tick.summarize_outcome
    assert s({"outcome": "executed", "result": {"query_results": [{"status": "completed"}, {"status": "failed", "error_summary": "GDELT status=429"}]}})["status"] == "completed_with_warnings"
    assert s({"outcome": "executed", "result": {"query_results": [{"status": "failed", "error_summary": "GDELT status=429"}]}})["retry"] == "retryable"
    assert s({"outcome": "executed", "result": {"stopped_reason": "caught_up", "calls_made": 2}})["status"] == "completed"
    assert s({"outcome": "executed", "result": {"stopped_reason": "max_calls_reached", "remaining_batch_count": 40}})["status"] == "completed_with_warnings"
    assert s({"outcome": "executed", "result": {"stopped_reason": "no_progress", "last_error": "ProxyError"}})["status"] == "failed"
    assert s({"outcome": "executed", "result": {"status": "failed", "error_summary": "schema mismatch"}})["retry"] == "non_retryable"


# ---- tick全体 -----------------------------------------------------------------------

def test_tick_one_job_failure_does_not_stop_the_others(monkeypatch):
    def fail(client, job, **kw):
        raise TimeoutError("ReadTimeout")
    ran = []

    def ok(client, job, **kw):
        ran.append("stream")
        return {"stopped_reason": "caught_up", "calls_made": 1}
    _fake_executors(monkeypatch, COLLECTION_DOC=fail, COLLECTION_STREAM=ok)
    c = _client()
    res = tick.run_tick(c, registry=REGISTRY, now=NOW)
    by = {j["job_id"]: j for j in res["jobs"]}
    assert by["COLLECTION_DOC"]["outcome"] == "failed"
    assert by["COLLECTION_STREAM"]["action"] == "ran" and ran == ["stream"]   # DOCが失敗してもstreamは動く


def test_stream_waits_only_while_doc_is_running():
    c = _client(_row("gdelt", timedelta(minutes=10), status="running"))
    res = tick.execute_job(c, "COLLECTION_STREAM", REGISTRY, now=NOW, trigger="tick")
    assert res["outcome"] == "skipped" and "COLLECTION_DOC" in res["reason"]


def test_tick_excludes_dedicated_jobs_unless_selected(monkeypatch):
    seen = []
    _fake_executors(monkeypatch, COLLECTION_DOC=lambda c, j, **k: (seen.append("doc"), {"query_results": []})[1],
                    COLLECTION_STREAM=lambda c, j, **k: (seen.append("stream"), {"stopped_reason": "caught_up"})[1],
                    COLLECTION_OFAC=lambda c, j, **k: (seen.append("ofac"), {"status": "completed"})[1])
    tick.run_tick(_client(), registry=REGISTRY, now=NOW)
    assert "ofac" not in seen
    tick.run_tick(_client(), registry=REGISTRY, now=NOW, only=["COLLECTION_OFAC"])
    assert seen[-1] == "ofac"


def test_dry_run_writes_nothing_and_reports_plan():
    c = _client(_row("gdelt_stream", timedelta(hours=17), status="running", rid="s"))
    before = [dict(r) for r in c.tables["geo_ingestion_runs"]]
    res = tick.run_tick(c, registry=REGISTRY, now=NOW, dry_run=True, include_dedicated=True)
    assert c.tables["geo_ingestion_runs"] == before and not c.updated and not c.inserted
    assert res["would_recover_stale"][0]["ingestion_run_id"] == "s"
    assert any(j["action"] == "would_run" for j in res["jobs"])


def test_not_configured_job_is_never_executed(monkeypatch):
    called = []
    _fake_executors(monkeypatch, COLLECTION_IMF=lambda c, j, **k: called.append(1))
    c = _client()
    res = tick.run_tick(c, registry=REGISTRY, now=NOW, only=["COLLECTION_IMF"])
    imf = next(j for j in res["jobs"] if j["job_id"] == "COLLECTION_IMF")
    assert called == [] and imf["state"] == "not_configured" and imf["action"] == "none"
    assert not [r for r in c.tables["geo_ingestion_runs"] if r["connector_type"] == tick.JOB_RUN_CONNECTOR_TYPE]


# ---- heartbeat ----------------------------------------------------------------------

def test_heartbeat_is_a_single_updated_row_and_health_follows_its_age():
    c = _client()
    assert tick.get_heartbeat(c, now=NOW, registry=REGISTRY)["status"] == "not_configured"
    tick.touch_heartbeat(c, now=NOW)
    tick.touch_heartbeat(c, now=NOW + timedelta(minutes=15))
    hb_rows = [r for r in c.tables["geo_ingestion_runs"] if r["connector_type"] == tick.HEARTBEAT_CONNECTOR_TYPE]
    assert len(hb_rows) == 1                                   # tickごとに行が増えない
    assert tick.get_heartbeat(c, now=NOW + timedelta(minutes=20), registry=REGISTRY)["status"] == "healthy"
    assert tick.get_heartbeat(c, now=NOW + timedelta(hours=3), registry=REGISTRY)["status"] == "unhealthy"


def test_tick_updates_heartbeat_even_when_nothing_runs(monkeypatch):
    _fake_executors(monkeypatch)
    c = _client(_row("gdelt", timedelta(hours=1)), _row("gdelt_stream", timedelta(minutes=2)))
    tick.run_tick(c, registry=REGISTRY, now=NOW)
    assert tick.get_heartbeat(c, now=NOW, registry=REGISTRY)["configured"] is True


# ---- Human Gate ---------------------------------------------------------------------

def test_scheduler_cannot_run_theme_sync_or_touch_cluster_status(monkeypatch):
    try:
        svc.run_job(_client(), "THEME_CANDIDATE_SYNC", registry=REGISTRY)
        assert False
    except svc.SchedulerError as e:
        assert "Human Gate" in str(e)
    src = Path(tick.__file__).read_text(encoding="utf-8") + Path(cli.__file__).read_text(encoding="utf-8")
    assert "geo_episode_clusters" not in src and "cluster_status" not in src and "sync_theme_candidates" not in src


# ---- Operations向け概況 -------------------------------------------------------------

def test_scheduler_overview_for_operations_screen():
    c = _client(_row("gdelt_stream", timedelta(hours=17), status="running", rid="s"))
    ov = tick.scheduler_overview(c, registry=REGISTRY, now=NOW)
    assert ov["status"] == "not_configured" and "PRODUCTION DEPLOYMENT PENDING" in ov["note"]
    assert ov["stale_running"][0]["ingestion_run_id"] == "s"
    assert {j["job_id"] for j in ov["jobs"]} >= {"COLLECTION_DOC", "COLLECTION_STREAM"}


# ---- 日時の解析（実データで発見した不具合の回帰テスト） ---------------------------------------

def test_parse_accepts_any_number_of_fractional_digits():
    """PostgRESTのタイムスタンプは小数秒が5桁などになることがある（Python 3.10のfromisoformatは非対応）。"""
    for text in ("2026-10-04T06:31:48.67346+00:00", "2026-10-04T06:31:48.6+00:00", "2026-10-04T06:31:48.673461+00:00",
                 "2026-10-04T06:31:48+00:00", "2026-10-04T06:31:48.67346Z", "2026-10-04T06:31:48.1234567+00:00"):
        assert svc._parse(text) is not None, text
    assert svc._parse("2026-10-04T06:31:48.67346+00:00").microsecond == 673460
    assert svc._parse(None) is None


def test_evaluate_job_handles_five_digit_fraction_rows():
    row = _row("gdelt", timedelta(hours=1))
    row["started_at"] = "2026-10-04T06:31:48.67346+00:00"
    row["completed_at"] = "2026-10-04T06:36:48.67346+00:00"
    assert tick.evaluate_job("COLLECTION_DOC", REGISTRY["jobs"]["COLLECTION_DOC"], REGISTRY, [row], now=NOW)["state"] == "due"


def test_manual_tick_without_heartbeat_does_not_look_like_a_running_scheduler(monkeypatch):
    _fake_executors(monkeypatch)
    c = _client(_row("gdelt", timedelta(hours=1)), _row("gdelt_stream", timedelta(minutes=2)))
    tick.run_tick(c, registry=REGISTRY, now=NOW, heartbeat=False)
    assert tick.get_heartbeat(c, now=NOW, registry=REGISTRY)["configured"] is False
