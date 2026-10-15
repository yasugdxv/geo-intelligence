"""Phase C: インテリジェンス運用（Pipeline Health）Summaryのテスト。

表示用の集計のみを検証する（LLMは呼ばない／既存のTheme生成ロジックは変更しない）。
個別Sync・一括Sync・冪等性・失敗時の挙動は test_geo_theme_candidate_pipeline_service.py で検証済み。
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_llm_common  # noqa: E402
import geo_operations_service as ops  # noqa: E402
import geo_theme_candidate_pipeline_service as pipeline_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402
from tests.test_geo_theme_candidate_service import _cluster, _fake_framing_llm, _member, _tables  # noqa: E402

NOW = datetime(2026, 10, 6, 0, 0, tzinfo=timezone.utc)


def _iso(**delta) -> str:
    return (NOW - timedelta(**delta)).isoformat()


def _base_tables(**extra) -> dict:
    t = _tables(clusters=extra.pop("clusters", []), members=extra.pop("members", []),
                candidates=extra.pop("candidates", []))
    t.update({
        "geo_monitoring_themes": [], "geo_monitoring_alerts": [], "geo_ingestion_runs": [],
        "geo_source_registry": [],
        "geo_external_signals": [{"external_signal_id": "s1", "published_at": _iso(hours=2)}],
    })
    t.update(extra)
    return t


# ---- Review待ち ----------------------------------------------------------------------

def test_pending_review_count_and_oldest_waiting():
    client = FakeSupabaseClient(tables=_base_tables(clusters=[
        _cluster("c-old", cluster_status="candidate", created_at=_iso(days=10)),
        _cluster("c-new", cluster_status="candidate", created_at=_iso(days=1)),
        _cluster("c-acc", cluster_status="accepted", created_at=_iso(days=3)),
    ]))
    r = ops.build_review(client, now=NOW)
    assert r["pending"] == 2 and r["accepted"] == 1 and r["total_clusters"] == 3
    assert r["oldest_waiting_days"] == 10
    assert r["items"][0]["episode_cluster_id"] == "c-old"        # 最も古い待ちが先頭
    assert r["items"][0]["actor"] == "White House"
    assert r["items"][0]["episode_type"] == "言葉での協調"        # 内部のquad番号を出さない


def test_review_empty_state():
    r = ops.build_review(FakeSupabaseClient(tables=_base_tables()), now=NOW)
    assert r["pending"] == 0 and r["items"] == [] and r["oldest_waiting_days"] is None


# ---- Accepted → Theme未同期 ----------------------------------------------------------

def _accepted_client():
    m = _member("cand-1", "c-1")
    return FakeSupabaseClient(tables=_base_tables(
        clusters=[_cluster("c-1")], members=[m["member"]], candidates=[m["candidate"]]))


def test_accepted_unsynced_cluster_is_listed_as_theme_sync_pending():
    s = ops.build_theme_sync(_accepted_client())
    assert s["pending_count"] == 1 and s["retry_count"] == 0
    assert s["pending"][0]["cluster_ids"] == ["c-1"] and s["pending"][0]["retry"] is False


def test_synced_cluster_is_not_pending_and_weak_accepted_is_not_eligible():
    client = _accepted_client()
    client.tables["geo_theme_candidates"].append(
        {"candidate_id": "t1", "source_episode_cluster_ids": ["c-1"], "is_demo": False})
    client.tables["geo_episode_clusters"].append(
        _cluster("c-weak", member_count=1, trajectory="stable"))
    s = ops.build_theme_sync(client)
    assert s["pending_count"] == 0
    assert s["accepted_not_eligible"] == 1        # 根拠が少ない承認済みは「異常」ではなく対象外


def test_previous_failure_marks_item_retryable_and_keeps_cluster_pending():
    client = _accepted_client()
    client.tables["geo_ingestion_runs"].append({
        "ingestion_run_id": "r1", "connector_type": "theme_candidate_sync", "is_demo": False,
        "status": "completed_with_warnings", "started_at": _iso(hours=1), "error_count": 1,
        "error_summary": "LLM生成失敗", "request_parameters": {"mode": "single", "cluster_ids": ["c-1"]}})
    s = ops.build_theme_sync(client)
    assert s["pending_count"] == 1 and s["pending"][0]["retry"] is True
    assert s["retry_count"] == 1 and s["pending"][0]["last_error"] == "LLM生成失敗"
    assert s["recent_runs"][0]["failed"] == 1


def test_later_success_clears_retry_flag():
    client = _accepted_client()
    for rid, hours, err in (("r-old", 5, 1), ("r-new", 1, 0)):
        client.tables["geo_ingestion_runs"].append({
            "ingestion_run_id": rid, "connector_type": "theme_candidate_sync", "is_demo": False,
            "status": "completed", "started_at": _iso(hours=hours), "error_count": err,
            "request_parameters": {"mode": "single", "cluster_ids": ["c-1"]}})
    assert ops.build_theme_sync(client)["pending"][0]["retry"] is False


def test_sync_failure_keeps_cluster_accepted_and_pending(monkeypatch):
    """LLM失敗後もaccepted・Theme同期待ちのまま（再試行可能）で、半端なCandidateが残らない。"""
    client = _accepted_client()

    def boom(*a, **k):
        raise RuntimeError("timeout")
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", boom)
    try:
        pipeline_service.sync_cluster(client, azure_client=object(), model="m",
                                      episode_cluster_id="c-1", requested_by="u")
    except pipeline_service.PipelineError:
        pass
    assert client.tables["geo_episode_clusters"][0]["cluster_status"] == "accepted"
    assert client.tables["geo_theme_candidates"] == []
    s = ops.build_theme_sync(client)
    assert s["pending_count"] == 1 and s["pending"][0]["retry"] is True


def test_sync_twice_does_not_duplicate_and_leaves_no_pending(monkeypatch):
    client = _accepted_client()
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _fake_framing_llm())
    first = pipeline_service.sync_cluster(client, azure_client=object(), model="m",
                                          episode_cluster_id="c-1", requested_by="u")
    second = pipeline_service.sync_cluster(client, azure_client=object(), model="m",
                                           episode_cluster_id="c-1", requested_by="u")
    assert first["outcome"] == "created" and second["outcome"] == "merged"
    assert len(client.tables["geo_theme_candidates"]) == 1
    assert ops.build_theme_sync(client)["pending_count"] == 0


# ---- Theme Candidate -----------------------------------------------------------------

def test_theme_candidate_counts_by_status_and_origin():
    client = FakeSupabaseClient(tables=_base_tables())
    client.tables["geo_theme_candidates"] += [
        {"candidate_id": "a", "status": "NEW", "generation_model": "article_discovery/gpt",
         "created_at": _iso(days=1), "is_demo": False},
        {"candidate_id": "b", "status": "HANDED_OFF", "generation_model": "gpt",
         "created_at": _iso(days=20), "is_demo": False},
        {"candidate_id": "c", "status": "DISMISSED", "generation_model": None,
         "created_at": _iso(days=2), "is_demo": False},
    ]
    r = ops.build_theme_candidates(client, now=NOW)
    assert r["by_status"] == {"NEW": 1, "HANDED_OFF": 1, "DISMISSED": 1}
    assert r["by_origin"] == {"article_discovery": 1, "situation": 2}
    assert r["new_recent"] == 1 and r["active"] == 2


# ---- Monitoring Alert ----------------------------------------------------------------

def test_alerts_ordered_by_severity_then_newest_and_acknowledged_excluded():
    client = FakeSupabaseClient(tables=_base_tables(
        geo_monitoring_themes=[{"monitoring_theme_id": "m1", "display_name": "台湾", "status": "active"}],
        geo_monitoring_alerts=[
            {"alert_id": "low-new", "monitoring_theme_id": "m1", "severity": "low", "detected_at": _iso(hours=1)},
            {"alert_id": "high-old", "monitoring_theme_id": "m1", "severity": "high", "detected_at": _iso(days=2)},
            {"alert_id": "high-new", "monitoring_theme_id": "m1", "severity": "high", "detected_at": _iso(hours=3)},
            {"alert_id": "done", "monitoring_theme_id": "m1", "severity": "high", "detected_at": _iso(hours=1),
             "acknowledged_at": _iso(minutes=5)},
        ]))
    m = ops.build_monitoring(client)
    assert [a["alert_id"] for a in m["alerts"]] == ["high-new", "high-old", "low-new"]
    assert m["unacknowledged_alerts"] == 3 and m["high_alerts"] == 2 and m["active_themes"] == 1


# ---- Collection ----------------------------------------------------------------------

def _source(name, days_ago, cadence="daily", **kw):
    return {"source_id": name, "name": name, "automation_allowed": True, "update_cadence": cadence,
            "last_successful_ingestion_at": _iso(days=days_ago), "failure_count": 0, **kw}


def test_collection_lists_only_problem_sources_not_all():
    client = FakeSupabaseClient(tables=_base_tables(geo_source_registry=[
        _source("fresh-1", 0.1), _source("fresh-2", 0.2), _source("OFAC", 47),
        {"source_id": "manual", "name": "manual", "automation_allowed": False}]))
    c = ops.build_collection(client, now=NOW)
    assert [p["name"] for p in c["problem_sources"]] == ["OFAC"]
    assert c["auto_sources"] == 3 and c["fresh_sources"] == 2


def test_collection_running_failed_and_scheduler_not_configured():
    client = FakeSupabaseClient(tables=_base_tables(geo_ingestion_runs=[
        {"ingestion_run_id": "1", "connector_type": "ofac", "source_id": None, "run_type": "cli", "is_demo": False,
         "status": "running", "started_at": _iso(hours=7)},
        {"ingestion_run_id": "2", "connector_type": "gdelt", "source_id": None, "run_type": "cli", "is_demo": False,
         "status": "failed", "started_at": _iso(hours=2), "error_summary": "429"},
        {"ingestion_run_id": "3", "connector_type": "theme_candidate_sync", "source_id": None,
         "run_type": "cli", "is_demo": False, "status": "failed", "started_at": _iso(hours=1)},   # Collectionではない
    ]))
    c = ops.build_collection(client, now=NOW)
    assert [r["connector"] for r in c["running"]] == ["ofac"] and c["running"][0]["too_long"] is False
    assert [f["connector"] for f in c["failed_runs"]] == ["gdelt"]
    assert c["scheduler"]["configured"] is False and c["scheduler"]["next_expected_at"] is None


def test_scheduler_is_configured_and_healthy_only_while_heartbeat_is_fresh():
    import geo_scheduler_tick as st
    client = FakeSupabaseClient(tables=_base_tables())
    assert ops.build_collection(client, now=NOW)["scheduler"]["status"] == "not_configured"
    st.touch_heartbeat(client, now=NOW - timedelta(minutes=10))
    s = ops.build_collection(client, now=NOW)["scheduler"]
    assert s["configured"] is True and s["healthy"] is True and s["heartbeat_age_minutes"] == 10.0
    s = ops.build_collection(client, now=NOW + timedelta(hours=3))["scheduler"]
    assert s["configured"] is True and s["healthy"] is False and s["status"] == "unhealthy"


def test_stale_running_job_is_reported_with_its_job_and_flagged_in_running_list():
    client = FakeSupabaseClient(tables=_base_tables(geo_ingestion_runs=[
        {"ingestion_run_id": "stuck", "connector_type": "gdelt_stream", "source_id": None, "run_type": "cli",
         "is_demo": False, "status": "running", "started_at": _iso(hours=17)}]))
    c = ops.build_collection(client, now=NOW)
    assert c["stale_running"][0]["job_id"] == "COLLECTION_GDELT_STREAM"
    assert c["running"][0]["stale"] is True and c["running"][0]["too_long"] is True


def test_scheduler_internal_rows_are_not_shown_as_collection_runs():
    client = FakeSupabaseClient(tables=_base_tables(geo_ingestion_runs=[
        {"ingestion_run_id": "j", "connector_type": "scheduler_job", "source_id": None, "run_type": "scheduled_external",
         "is_demo": False, "status": "failed", "started_at": _iso(hours=1), "request_parameters": {"job_id": "COLLECTION_GDELT_DOC"}}]))
    c = ops.build_collection(client, now=NOW)
    assert c["failed_runs"] == [] and c["running"] == []


# ---- Summary全体 ---------------------------------------------------------------------

def test_summary_empty_state_only_notes_scheduler_not_registered():
    client = FakeSupabaseClient(tables=_base_tables())
    r = ops.get_operations_summary(client)
    assert r["errors"] == {}
    assert [(a["key"], a["severity"]) for a in r["action_queue"]] == [("scheduler", "low")]   # 緊急ではない案内のみ
    assert r["health"]["review"]["level"] == "ok" and r["health"]["theme_sync"]["level"] == "ok"


def test_summary_has_no_actions_when_everything_is_healthy():
    import geo_scheduler_tick as st
    client = FakeSupabaseClient(tables=_base_tables())
    st.touch_heartbeat(client, now=datetime.now(timezone.utc))
    assert ops.get_operations_summary(client)["action_queue"] == []


def test_dead_scheduler_and_stale_job_appear_in_action_queue():
    import geo_scheduler_tick as st
    client = FakeSupabaseClient(tables=_base_tables(geo_ingestion_runs=[
        {"ingestion_run_id": "stuck", "connector_type": "gdelt_stream", "source_id": None, "run_type": "cli",
         "is_demo": False, "status": "running", "started_at": _iso(hours=17)}]))
    st.touch_heartbeat(client, now=datetime.now(timezone.utc) - timedelta(hours=5))
    keys = {a["key"]: a for a in ops.get_operations_summary(client)["action_queue"]}
    assert keys["scheduler"]["severity"] == "medium" and "止まっています" in keys["scheduler"]["title"]
    assert "stale_running" in keys


def test_action_queue_contains_review_and_sync_and_is_prioritised():
    client = _accepted_client()
    client.tables["geo_episode_clusters"].append(_cluster("c-wait", cluster_status="candidate",
                                                          created_at=_iso(days=2)))
    client.tables["geo_monitoring_themes"].append(
        {"monitoring_theme_id": "m1", "display_name": "台湾", "status": "active"})
    client.tables["geo_monitoring_alerts"].append(
        {"alert_id": "a1", "monitoring_theme_id": "m1", "severity": "high", "detected_at": _iso(hours=1)})
    r = ops.get_operations_summary(client)
    keys = [a["key"] for a in r["action_queue"]]
    assert keys[0] == "alerts"                          # 重要Alertが最優先
    assert {"theme_sync", "review"} <= set(keys)
    sync = next(a for a in r["action_queue"] if a["key"] == "theme_sync")
    assert sync["cta"]["kind"] == "action"
    review = next(a for a in r["action_queue"] if a["key"] == "review")
    assert review["cta"]["to"] == "/geo/episode-candidates"
    assert r["daily"]["review_pending"] == 1 and r["daily"]["theme_sync_pending"] == 1


def test_partial_failure_returns_other_sections_and_error_reason():
    client = _accepted_client()

    class Broken(FakeSupabaseClient):
        def select(self, table, params):
            if table == "geo_monitoring_themes":
                raise RuntimeError("db down")
            return super().select(table, params)
    broken = Broken(tables=client.tables)
    r = ops.get_operations_summary(broken)
    assert r["monitoring"] is None and "monitoring" in r["errors"]
    assert r["health"]["monitoring"]["level"] == "unknown"
    assert r["theme_sync"]["pending_count"] == 1 and r["review"] is not None   # 他は表示できる


def test_summary_does_not_call_llm(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("Summary表示でLLMを呼んではいけない")
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", forbidden)
    ops.get_operations_summary(_accepted_client())


def test_stale_collection_is_stopped_and_top_of_queue():
    client = FakeSupabaseClient(tables=_base_tables())
    client.tables["geo_external_signals"] = [{"external_signal_id": "s", "published_at": _iso(days=40)}]
    r = ops.get_operations_summary(client)
    assert r["health"]["collection"]["level"] == "stopped"
    assert r["action_queue"][0]["key"] == "collection"


# ---- API -----------------------------------------------------------------------------

def test_summary_api_endpoint(monkeypatch):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    import geo_operations_api as api
    monkeypatch.setattr(api, "_client", FakeSupabaseClient(tables=_base_tables()))
    app = FastAPI()
    app.include_router(api.geo_operations_router)
    res = TestClient(app).get("/api/geo/operations/summary")
    assert res.status_code == 200
    body = res.json()
    assert body["version"] == "operations_summary_v1" and set(body["health"]) == {
        "collection", "episode", "review", "theme_sync", "monitoring"}
