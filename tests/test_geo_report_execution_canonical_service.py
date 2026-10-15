import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_execution_canonical_service as canonical  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _seed_run(client, run_id="run-1", project_id="proj-1"):
    client.insert("geo_report_collection_runs", [{"run_id": run_id, "project_id": project_id}])


def _seed_execution(client, execution_id, run_id, stage, status="completed", **extra):
    client.insert("geo_report_executions", [{
        "id": execution_id, "run_id": run_id, "stage": stage, "status": status, **extra,
    }])


def test_set_canonical_execution_prompt1_updates_run_column():
    client = FakeSupabaseClient()
    _seed_run(client)
    _seed_execution(client, "exec-p1-b", "run-1", "prompt1")

    result = canonical.set_canonical_execution(client, "run-1", "prompt1", "exec-p1-b")

    run = client.select("geo_report_collection_runs", {"select": "*", "run_id": "eq.run-1"})[0]
    assert run["canonical_prompt1_execution_id"] == "exec-p1-b"
    assert result["staled_execution_ids"] == []


def test_set_canonical_execution_prompt2_updates_both_run_columns():
    client = FakeSupabaseClient()
    _seed_run(client)
    _seed_execution(client, "exec-p2-b", "run-1", "prompt2")

    canonical.set_canonical_execution(client, "run-1", "prompt2", "exec-p2-b")

    run = client.select("geo_report_collection_runs", {"select": "*", "run_id": "eq.run-1"})[0]
    assert run["canonical_prompt2_execution_id"] == "exec-p2-b"
    assert run["canonical_report_execution_id"] == "exec-p2-b"


def test_set_canonical_execution_rejects_wrong_run():
    client = FakeSupabaseClient()
    _seed_run(client, run_id="run-1")
    _seed_run(client, run_id="run-2")
    _seed_execution(client, "exec-other-run", "run-2", "prompt1")

    try:
        canonical.set_canonical_execution(client, "run-1", "prompt1", "exec-other-run")
        assert False, "should have raised"
    except ValueError as exc:
        assert "run_id=run-1のExecutionではありません" in str(exc)


def test_set_canonical_execution_rejects_wrong_stage():
    client = FakeSupabaseClient()
    _seed_run(client)
    _seed_execution(client, "exec-wrong-stage", "run-1", "aggregation")

    try:
        canonical.set_canonical_execution(client, "run-1", "prompt1", "exec-wrong-stage")
        assert False, "should have raised"
    except ValueError as exc:
        assert "stage" in str(exc)


def test_set_canonical_execution_rejects_non_completed_status():
    client = FakeSupabaseClient()
    _seed_run(client)
    _seed_execution(client, "exec-running", "run-1", "prompt1", status="running")

    try:
        canonical.set_canonical_execution(client, "run-1", "prompt1", "exec-running")
        assert False, "should have raised"
    except ValueError as exc:
        assert "completed" in str(exc)


def test_set_canonical_execution_rejects_unknown_stage():
    client = FakeSupabaseClient()
    _seed_run(client)
    _seed_execution(client, "exec-1", "run-1", "prompt1")
    try:
        canonical.set_canonical_execution(client, "run-1", "bogus_stage", "exec-1")
        assert False, "should have raised"
    except ValueError as exc:
        assert "stage" in str(exc)


def test_switching_canonical_prompt1_stales_downstream_aggregation_and_prompt2():
    client = FakeSupabaseClient()
    _seed_run(client)
    _seed_execution(client, "exec-p1-a", "run-1", "prompt1")
    _seed_execution(client, "exec-p1-b", "run-1", "prompt1")
    # exec-p1-a由来のAggregation/Prompt2（旧Canonical系列）
    _seed_execution(client, "exec-agg-a", "run-1", "aggregation", input_prompt1_execution_id="exec-p1-a")
    _seed_execution(client, "exec-p2-a", "run-1", "prompt2", input_aggregation_execution_id="exec-agg-a")
    # まだpendingのバッチが残る別のAggregation（completedでないため対象外）
    _seed_execution(client, "exec-agg-pending", "run-1", "aggregation", status="running",
                     input_prompt1_execution_id="exec-p1-a")

    result = canonical.set_canonical_execution(client, "run-1", "prompt1", "exec-p1-b")

    staled = set(result["staled_execution_ids"])
    assert staled == {"exec-agg-a", "exec-p2-a"}

    agg_a = client.select("geo_report_executions", {"select": "*", "id": "eq.exec-agg-a"})[0]
    p2_a = client.select("geo_report_executions", {"select": "*", "id": "eq.exec-p2-a"})[0]
    agg_pending = client.select("geo_report_executions", {"select": "*", "id": "eq.exec-agg-pending"})[0]
    assert agg_a["status"] == "stale"
    assert p2_a["status"] == "stale"
    assert agg_pending["status"] == "running"  # completed以外は対象外のまま


def test_switching_canonical_prompt1_does_not_stale_executions_already_built_from_new_canonical():
    client = FakeSupabaseClient()
    _seed_run(client)
    _seed_execution(client, "exec-p1-b", "run-1", "prompt1")
    _seed_execution(client, "exec-agg-b", "run-1", "aggregation", input_prompt1_execution_id="exec-p1-b")

    result = canonical.set_canonical_execution(client, "run-1", "prompt1", "exec-p1-b")

    assert result["staled_execution_ids"] == []
    agg_b = client.select("geo_report_executions", {"select": "*", "id": "eq.exec-agg-b"})[0]
    assert agg_b["status"] == "completed"


def test_switching_canonical_prompt1_stales_report_edit_chain_built_on_old_prompt2():
    client = FakeSupabaseClient()
    _seed_run(client)
    _seed_execution(client, "exec-p1-a", "run-1", "prompt1")
    _seed_execution(client, "exec-p1-b", "run-1", "prompt1")
    # exec-p1-a -> agg-a -> p2-a -> edit-1 -> edit-2 という旧Canonical系列全体
    _seed_execution(client, "exec-agg-a", "run-1", "aggregation", input_prompt1_execution_id="exec-p1-a")
    _seed_execution(client, "exec-p2-a", "run-1", "prompt2", input_aggregation_execution_id="exec-agg-a")
    _seed_execution(client, "exec-edit-1", "run-1", "report_edit", parent_execution_id="exec-p2-a")
    _seed_execution(client, "exec-edit-2", "run-1", "report_edit", parent_execution_id="exec-edit-1")

    result = canonical.set_canonical_execution(client, "run-1", "prompt1", "exec-p1-b")

    staled = set(result["staled_execution_ids"])
    assert staled == {"exec-agg-a", "exec-p2-a", "exec-edit-1", "exec-edit-2"}
    for exec_id in ("exec-agg-a", "exec-p2-a", "exec-edit-1", "exec-edit-2"):
        row = client.select("geo_report_executions", {"select": "*", "id": f"eq.{exec_id}"})[0]
        assert row["status"] == "stale"


def test_propagate_staleness_prompt2_with_no_report_edits_is_no_op():
    client = FakeSupabaseClient()
    _seed_run(client)
    staled = canonical.propagate_staleness(client, "run-1", "prompt2", "exec-p2-b")
    assert staled == []


def test_switching_canonical_prompt2_stales_report_edit_chain_from_old_prompt2():
    client = FakeSupabaseClient()
    _seed_run(client)
    _seed_execution(client, "exec-p2-old", "run-1", "prompt2")
    _seed_execution(client, "exec-p2-new", "run-1", "prompt2")
    # 旧Prompt2からの連鎖: prompt2-old -> edit1 -> edit2
    _seed_execution(client, "exec-edit-1", "run-1", "report_edit", parent_execution_id="exec-p2-old")
    _seed_execution(client, "exec-edit-2", "run-1", "report_edit", parent_execution_id="exec-edit-1")
    # まだ未完了のReport Edit(completedでないため対象外)
    _seed_execution(client, "exec-edit-pending", "run-1", "report_edit", status="running",
                     parent_execution_id="exec-p2-old")

    result = canonical.set_canonical_execution(client, "run-1", "prompt2", "exec-p2-new")

    staled = set(result["staled_execution_ids"])
    assert staled == {"exec-edit-1", "exec-edit-2"}
    edit1 = client.select("geo_report_executions", {"select": "*", "id": "eq.exec-edit-1"})[0]
    edit2 = client.select("geo_report_executions", {"select": "*", "id": "eq.exec-edit-2"})[0]
    edit_pending = client.select("geo_report_executions", {"select": "*", "id": "eq.exec-edit-pending"})[0]
    assert edit1["status"] == "stale"
    assert edit2["status"] == "stale"
    assert edit_pending["status"] == "running"  # completed以外は対象外のまま


def test_switching_canonical_prompt2_does_not_stale_edits_already_from_new_prompt2():
    client = FakeSupabaseClient()
    _seed_run(client)
    _seed_execution(client, "exec-p2-new", "run-1", "prompt2")
    _seed_execution(client, "exec-edit-new", "run-1", "report_edit", parent_execution_id="exec-p2-new")

    result = canonical.set_canonical_execution(client, "run-1", "prompt2", "exec-p2-new")

    assert result["staled_execution_ids"] == []
    edit_new = client.select("geo_report_executions", {"select": "*", "id": "eq.exec-edit-new"})[0]
    assert edit_new["status"] == "completed"
