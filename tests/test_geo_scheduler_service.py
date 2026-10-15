"""Phase D: Production Scheduling / Job Orchestrationのテスト。

既存のConnector実行ロジック（GDELT/IMF/World Bank/OFAC）・catch-up・retry/lock/
stale検知そのものの既存実装には一切触れない。ここではオーケストレーション層
（geo_scheduler_service.py）単体のロジック——lock判定・stale検知・依存関係チェック・
dry run・catch-upギャップ計算・エラー分類・Job実行の安全な分岐——のみを検証する。
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_connector_base as base  # noqa: E402
import geo_scheduler_service as scheduler  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

NOW = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)

_TEST_REGISTRY = {
    "defaults": {"stale_after_hours": 6},
    "jobs": {
        "COLLECTION_A": {
            "job_type": "collection", "connector_type": "conn_a", "cli_command": "a",
            "cadence": "daily", "depends_on": [], "stale_after_hours": 2,
        },
        "COLLECTION_B": {
            "job_type": "collection", "connector_type": "conn_b", "cli_command": "b",
            "cadence": "daily", "depends_on": ["COLLECTION_A"], "stale_after_hours": 2,
        },
        "COLLECTION_DISABLED": {
            "job_type": "collection", "connector_type": "conn_disabled", "cli_command": "d",
            "cadence": "daily", "enabled": False, "disabled_reason": "credential_missing",
        },
        "COLLECTION_GDELT_DOC": {
            "job_type": "collection", "connector_type": "gdelt", "cli_command": "gdelt-batch",
            "cadence": "daily", "depends_on": [], "stale_after_hours": 3,
            "catchup": {"enabled": True, "max_single_window_days": 7},
        },
    },
    "manual_only_jobs": {
        "THEME_CANDIDATE_SYNC": {
            "job_type": "human_triggered", "connector_type": "theme_candidate_sync",
        },
    },
}


def _running_row(connector_type: str, started_at: datetime, *, is_demo=False) -> dict:
    return {"ingestion_run_id": f"run-{connector_type}", "connector_type": connector_type,
            "status": "running", "started_at": started_at.isoformat(), "is_demo": is_demo,
            "completed_at": None, "error_summary": None}


def _completed_row(connector_type: str, started_at: datetime, *, status="completed", is_demo=False,
                    error_summary=None) -> dict:
    return {"ingestion_run_id": f"run-{connector_type}-done", "connector_type": connector_type,
            "status": status, "started_at": started_at.isoformat(),
            "completed_at": (started_at + timedelta(minutes=5)).isoformat(),
            "is_demo": is_demo, "error_summary": error_summary}


# ---- stale detection / lock --------------------------------------------------------

def test_is_stale_true_when_running_past_threshold():
    run = _running_row("conn_a", NOW - timedelta(hours=5))
    assert scheduler.is_stale(run, stale_after_hours=2, now=NOW) is True


def test_is_stale_false_when_running_within_threshold():
    run = _running_row("conn_a", NOW - timedelta(minutes=30))
    assert scheduler.is_stale(run, stale_after_hours=2, now=NOW) is False


def test_is_stale_false_when_not_running():
    run = _completed_row("conn_a", NOW - timedelta(hours=10))
    assert scheduler.is_stale(run, stale_after_hours=2, now=NOW) is False


def test_job_locked_when_running_and_not_stale():
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [_running_row("conn_a", NOW - timedelta(minutes=10))]})
    job = _TEST_REGISTRY["jobs"]["COLLECTION_A"]
    assert scheduler.is_job_locked(client, job, now=NOW) is True


def test_job_not_locked_when_running_row_is_stale():
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [_running_row("conn_a", NOW - timedelta(hours=5))]})
    job = _TEST_REGISTRY["jobs"]["COLLECTION_A"]
    assert scheduler.is_job_locked(client, job, now=NOW) is False


def test_job_not_locked_when_last_run_completed():
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [_completed_row("conn_a", NOW - timedelta(hours=1))]})
    job = _TEST_REGISTRY["jobs"]["COLLECTION_A"]
    assert scheduler.is_job_locked(client, job, now=NOW) is False


def test_detect_and_mark_stale_jobs_marks_failed_not_success():
    """item9: 自動的に成功扱いにしないこと。"""
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [_running_row("conn_a", NOW - timedelta(hours=10))]})
    marked = scheduler.detect_and_mark_stale_jobs(client, registry=_TEST_REGISTRY, now=NOW)
    assert len(marked) == 1
    assert marked[0]["job_id"] == "COLLECTION_A"
    row = client.tables["geo_ingestion_runs"][0]
    assert row["status"] == "failed"
    assert "stale" in row["error_summary"]


def test_detect_and_mark_stale_jobs_leaves_fresh_running_alone():
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [_running_row("conn_a", NOW - timedelta(minutes=5))]})
    marked = scheduler.detect_and_mark_stale_jobs(client, registry=_TEST_REGISTRY, now=NOW)
    assert marked == []
    assert client.tables["geo_ingestion_runs"][0]["status"] == "running"


def test_detect_and_mark_stale_jobs_skips_disabled_jobs():
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [
        _running_row("conn_disabled", NOW - timedelta(hours=100))]})
    marked = scheduler.detect_and_mark_stale_jobs(client, registry=_TEST_REGISTRY, now=NOW)
    assert marked == []


# ---- dependency checking ------------------------------------------------------------

def test_dependencies_satisfied_when_upstream_completed():
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [_completed_row("conn_a", NOW - timedelta(hours=1))]})
    ok, reason = scheduler.check_dependencies_satisfied(client, _TEST_REGISTRY["jobs"]["COLLECTION_B"],
                                                         registry=_TEST_REGISTRY)
    assert ok is True


def test_dependencies_satisfied_even_when_upstream_never_ran():
    """仕様7（Phase D）: 依存Jobは「順序」だけを保証する。未実行でも後続は止めない。"""
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": []})
    ok, _ = scheduler.check_dependencies_satisfied(client, _TEST_REGISTRY["jobs"]["COLLECTION_B"],
                                                   registry=_TEST_REGISTRY)
    assert ok is True


def test_dependencies_satisfied_even_when_upstream_failed():
    """1 Sourceの失敗で全Pipelineを止めない（取得済みのデータは下流処理できる）。"""
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [
        _completed_row("conn_a", NOW - timedelta(hours=1), status="failed")]})
    ok, _ = scheduler.check_dependencies_satisfied(client, _TEST_REGISTRY["jobs"]["COLLECTION_B"],
                                                   registry=_TEST_REGISTRY, now=NOW)
    assert ok is True


def test_dependencies_wait_only_while_upstream_is_running():
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [_running_row("conn_a", NOW - timedelta(minutes=5))]})
    ok, reason = scheduler.check_dependencies_satisfied(client, _TEST_REGISTRY["jobs"]["COLLECTION_B"],
                                                        registry=_TEST_REGISTRY, now=NOW)
    assert ok is False and "COLLECTION_A" in reason


def test_one_source_failure_does_not_block_independent_job():
    """item5後段: 1 Sourceの失敗で全体を止めない——依存関係の無いJob同士は独立。"""
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [
        _completed_row("conn_disabled", NOW, status="failed")]})
    # COLLECTION_Aはdepends_on=[]なのでconn_disabledの失敗と無関係に実行判定できる
    ok, _ = scheduler.check_dependencies_satisfied(client, _TEST_REGISTRY["jobs"]["COLLECTION_A"],
                                                    registry=_TEST_REGISTRY)
    assert ok is True


# ---- error classification (item22) --------------------------------------------------

def test_classify_error_network_timeout_is_retryable():
    assert scheduler.classify_error("requests.exceptions.ReadTimeout: timed out") == "retryable"
    assert scheduler.classify_error("ProxyError: Unable to connect to proxy") == "retryable"


def test_classify_error_credential_missing_is_non_retryable():
    assert scheduler.classify_error("ConnectorDisabledError: credential_configured=false") == "non_retryable"


def test_classify_error_unknown_pattern():
    assert scheduler.classify_error("something completely novel happened") == "unknown"
    assert scheduler.classify_error(None) == "unknown"


# ---- run_job: lock/deps/disabled/human-gate guard -----------------------------------

def test_run_job_skips_when_locked():
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [_running_row("conn_a", NOW - timedelta(minutes=5))]})
    result = scheduler.run_job(client, "COLLECTION_A", registry=_TEST_REGISTRY)
    assert result["outcome"] == "skipped"
    assert "locked" in result["reason"]


def test_run_job_skips_when_dependency_not_satisfied():
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": []})
    result = scheduler.run_job(client, "COLLECTION_B", registry=_TEST_REGISTRY)
    assert result["outcome"] == "skipped"


def test_run_job_skips_disabled_job():
    client = FakeSupabaseClient()
    result = scheduler.run_job(client, "COLLECTION_DISABLED", registry=_TEST_REGISTRY)
    assert result["outcome"] == "skipped"
    assert result["reason"] == "credential_missing"


def test_run_job_refuses_human_gate_job():
    """item12: Episode Cluster accepted以降の半自動Jobは、Schedulerから実行できないこと。"""
    client = FakeSupabaseClient()
    try:
        scheduler.run_job(client, "THEME_CANDIDATE_SYNC", registry=_TEST_REGISTRY)
        assert False, "Human Gate後のJobはSchedulerError になるはず"
    except scheduler.SchedulerError as e:
        assert "Human Gate" in str(e)


def test_run_job_executor_missing_reports_skip_not_crash():
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [_completed_row("conn_a", NOW - timedelta(days=1))]})
    result = scheduler.run_job(client, "COLLECTION_A", registry=_TEST_REGISTRY)
    assert result["outcome"] == "skipped"
    assert "実行方法未定義" in result["reason"]


def test_run_job_force_bypasses_lock():
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [_running_row("conn_a", NOW - timedelta(minutes=5))]})
    # 実行方法未定義のため最終的にはskippedになるが、lockではなくそちらの理由で止まることを確認する
    # （force=Trueでlockチェック自体は通過していることの間接確認）
    result = scheduler.run_job(client, "COLLECTION_A", registry=_TEST_REGISTRY, force=True)
    assert result["reason"] != "locked（既に実行中）"


def test_run_job_default_requested_by_is_batch_for_scheduled_external_tagging(monkeypatch):
    """Phase D（重要な回帰テスト、2026-10-06）: run_job()がrequested_byを明示指定されない場合、
    既定値"batch"がそのままExecutor（ひいてはConnector側のrun_type導出ロジック）へ渡される
    こと。GDELT等のConnectorは requested_by=="batch" の場合のみ run_type="scheduled_external"
    として記録する設計のため、ここで"batch"以外の値（例えば独自の"scheduler"という文字列）に
    すり替わると、本物のScheduler実行であってもrun_type="manual"のまま記録され、
    「本当にSchedulerが動いたか」を事後に一切判別できなくなる（Phase Dの核心機能が壊れる）。
    手動実行（Manual Trigger API等）はrequested_byへ人間のidentityを明示的に渡すため、この
    既定値には影響されない。"""
    captured = {}

    def fake_executor(client, job, *, is_demo, requested_by):
        captured["requested_by"] = requested_by
        return {"ok": True}

    monkeypatch.setitem(scheduler._EXECUTORS, "COLLECTION_A", fake_executor)
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": []})

    scheduler.run_job(client, "COLLECTION_A", registry=_TEST_REGISTRY)

    assert captured["requested_by"] == "batch"


# ---- catch-up gap computation (item8) ------------------------------------------------

def test_catchup_gap_detects_implicit_floor_bug_window():
    """Phase Aで発見したgdelt_doc_catchupの暗黙7日打ち切りバグをSchedulerが検知できること。"""
    client = FakeSupabaseClient(tables={"geo_external_signal_queries": [
        {"name": "Taiwan Strait", "enabled": True, "last_run_at": (NOW - timedelta(days=26)).isoformat()},
        {"name": "Iran sanctions", "enabled": True, "last_run_at": (NOW - timedelta(days=5)).isoformat()},
    ]})
    gap = scheduler.compute_catchup_gap(client, "COLLECTION_GDELT_DOC",
                                         _TEST_REGISTRY["jobs"]["COLLECTION_GDELT_DOC"], now=NOW)
    assert gap["gap_days"] == 26
    assert gap["oldest_query_name"] == "Taiwan Strait"
    assert gap["exceeds_implicit_floor"] is True
    assert gap["recommended_since"] is not None


def test_catchup_gap_within_normal_window_does_not_flag():
    client = FakeSupabaseClient(tables={"geo_external_signal_queries": [
        {"name": "Taiwan Strait", "enabled": True, "last_run_at": (NOW - timedelta(hours=12)).isoformat()},
    ]})
    gap = scheduler.compute_catchup_gap(client, "COLLECTION_GDELT_DOC",
                                         _TEST_REGISTRY["jobs"]["COLLECTION_GDELT_DOC"], now=NOW)
    assert gap["exceeds_implicit_floor"] is False
    assert gap["recommended_since"] is None


# ---- dry run (item21): DB変更しない ---------------------------------------------------

def test_dry_run_does_not_write_to_db():
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [_completed_row("conn_a", NOW - timedelta(days=1))]})
    before = len(client.tables["geo_ingestion_runs"])
    result = scheduler.dry_run_job(client, "COLLECTION_A", registry=_TEST_REGISTRY, now=NOW)
    assert result["dry_run"] is True
    assert len(client.tables["geo_ingestion_runs"]) == before  # 書き込みなし


def test_dry_run_reports_would_run_true_when_clear():
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [_completed_row("conn_a", NOW - timedelta(days=1))]})
    result = scheduler.dry_run_job(client, "COLLECTION_A", registry=_TEST_REGISTRY, now=NOW)
    assert result["would_run"] is True


def test_dry_run_reports_would_run_false_for_disabled_job():
    client = FakeSupabaseClient()
    result = scheduler.dry_run_job(client, "COLLECTION_DISABLED", registry=_TEST_REGISTRY, now=NOW)
    assert result["would_run"] is False
    assert result["enabled"] is False


def test_dry_run_reports_manual_only_job_as_not_schedulable():
    client = FakeSupabaseClient()
    result = scheduler.dry_run_job(client, "THEME_CANDIDATE_SYNC", registry=_TEST_REGISTRY, now=NOW)
    assert result["would_run"] is False


# ---- scheduler health (item19/37): 未設定を正直に報告する ------------------------------

def test_scheduler_health_reports_not_configured_when_no_scheduled_runs():
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": []})
    health = scheduler.get_scheduler_health(client, registry=_TEST_REGISTRY, now=NOW)
    assert health["configured"] is False
    assert health["status"] == "not_configured"


def test_scheduler_health_detects_recent_scheduled_runs_but_stays_honest():
    """scheduled_externalの実行履歴があっても、configuredはFalseのまま
    （本番OS Scheduler自体を登録していないため、detectedと混同しない）。"""
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [{
        "ingestion_run_id": "r1", "run_type": "scheduled_external",
        "started_at": (NOW - timedelta(hours=2)).isoformat(), "is_demo": False,
    }]})
    health = scheduler.get_scheduler_health(client, registry=_TEST_REGISTRY, now=NOW)
    assert health["detected_recent_scheduled_runs"] is True
    assert health["configured"] is False


# ---- job status aggregation (item4) --------------------------------------------------

def test_get_job_status_never_run():
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": []})
    status = scheduler.get_job_status(client, "COLLECTION_A", registry=_TEST_REGISTRY, now=NOW)
    assert status["status"] == "never_run"
    assert status["last_success_at"] is None


def test_get_job_status_stale_overrides_running():
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [_running_row("conn_a", NOW - timedelta(hours=10))]})
    status = scheduler.get_job_status(client, "COLLECTION_A", registry=_TEST_REGISTRY, now=NOW)
    assert status["status"] == "stale"


def test_get_job_status_reports_retry_classification_for_failed():
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [
        _completed_row("conn_a", NOW - timedelta(hours=1), status="failed",
                       error_summary="ProxyError: connection refused")]})
    status = scheduler.get_job_status(client, "COLLECTION_A", registry=_TEST_REGISTRY, now=NOW)
    assert status["status"] == "failed"
    assert status["retry_classification"] == "retryable"
