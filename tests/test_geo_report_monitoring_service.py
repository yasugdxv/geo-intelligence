import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_monitoring_service as monitoring  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _base_client(**extra_tables) -> FakeSupabaseClient:
    tables = {"geo_themes": [], "geo_events": [], "geo_knowledge_gaps": [], "geo_audit_logs": []}
    tables.update(extra_tables)
    return FakeSupabaseClient(tables)


def _item(**overrides):
    base = {
        "provenance": {"evidence_basis": "observed", "observed_record_ids": ["r1"],
                        "observed_cluster_ids": [], "expert_context_refs": [], "counterevidence_record_ids": []},
        "confidence": "Medium",
    }
    base.update(overrides)
    return base


PROJECT = {"project_id": "proj-1", "project_code": "iran", "project_title": "イラン監視"}
NARRATIVE_HIGH = {"report_priority": "High", "report_topic_label": "検証条件", "narrative_name": "強硬姿勢"}
NARRATIVE_LOW = {"report_priority": "Low", "report_topic_label": "その他", "narrative_name": "その他"}


# ---------------------------------------------------------------------------
# 昇格ルール（決定的フィルタ）
# ---------------------------------------------------------------------------

def test_select_updates_excludes_stable_and_insufficient_evidence():
    final_draft = {"assessment_delta": [
        _item(item="a", change_type="Intensified"),
        _item(item="b", change_type="Stable"),
        _item(item="c", change_type="Insufficient_evidence"),
        _item(item="d", change_type="Reframed"),
    ]}
    updates = monitoring.select_updates(final_draft)
    assert {u["item"] for u in updates} == {"a", "d"}


def test_select_alerts_only_triggered():
    final_draft = {"watchpoint_updates": [
        _item(watchpoint="w1", status="Triggered"),
        _item(watchpoint="w2", status="Possible_trigger"),
        _item(watchpoint="w3", status="Not_observed"),
    ]}
    alerts = monitoring.select_alerts(final_draft)
    assert [a["watchpoint"] for a in alerts] == ["w1"]


def test_intelligence_need_candidate_from_possible_trigger_watchpoint():
    final_draft = {"watchpoint_updates": [_item(watchpoint="w2", status="Possible_trigger")]}
    candidates = monitoring.select_intelligence_need_candidates(final_draft, NARRATIVE_HIGH)
    assert any(c["reason"] == "watchpoint_possible_trigger" for c in candidates)


def test_intelligence_need_candidate_from_low_confidence_central_assessment():
    final_draft = {
        "central_assessment": _item(summary="x", confidence="Low"),
        "continuity_assessment": {"state": "Insufficient evidence", "rationale": "..."},
    }
    candidates = monitoring.select_intelligence_need_candidates(final_draft, NARRATIVE_HIGH)
    assert any(c["reason"] == "central_assessment_confidence_low_insufficient_evidence" for c in candidates)


def test_intelligence_need_candidate_not_generated_when_confidence_high():
    final_draft = {
        "central_assessment": _item(summary="x", confidence="High"),
        "continuity_assessment": {"state": "Stable", "rationale": "..."},
    }
    candidates = monitoring.select_intelligence_need_candidates(final_draft, NARRATIVE_HIGH)
    assert candidates == []


def test_intelligence_need_candidate_from_finding_missing_counterevidence_and_actor_context():
    final_draft = {"findings": [_item(
        finding_id="f1", statement="x", confidence="Low",
        provenance={"evidence_basis": "observed", "observed_record_ids": ["r1"], "observed_cluster_ids": [],
                    "expert_context_refs": [], "counterevidence_record_ids": []},
    )]}
    candidates = monitoring.select_intelligence_need_candidates(final_draft, NARRATIVE_HIGH)
    assert any(c["reason"] == "missing_counterevidence_and_actor_context" for c in candidates)


def test_intelligence_need_not_generated_for_low_priority_narrative():
    final_draft = {"findings": [_item(
        finding_id="f1", statement="x", confidence="Low",
        provenance={"evidence_basis": "observed", "observed_record_ids": ["r1"], "observed_cluster_ids": [],
                    "expert_context_refs": [], "counterevidence_record_ids": []},
    )]}
    candidates = monitoring.select_intelligence_need_candidates(final_draft, NARRATIVE_LOW)
    assert candidates == []


# ---------------------------------------------------------------------------
# Theme自動確保
# ---------------------------------------------------------------------------

def test_get_or_create_theme_for_project_is_idempotent():
    client = _base_client()
    first = monitoring.get_or_create_theme_for_project(client, PROJECT)
    second = monitoring.get_or_create_theme_for_project(client, PROJECT)
    assert first["theme_id"] == second["theme_id"]
    assert len(client.tables["geo_themes"]) == 1


# ---------------------------------------------------------------------------
# Monitoring（geo_events）書き込み・冪等性
# ---------------------------------------------------------------------------

SOURCE_REF = {"project_id": "proj-1", "run_id": "run-1", "signal_id": "S1",
              "narrative_id": "S1-N01", "synthesis_id": "synth-1"}


def test_promote_updates_and_alerts_creates_events_and_is_idempotent():
    client = _base_client()
    theme = monitoring.get_or_create_theme_for_project(client, PROJECT)
    updates = [_item(item="verification stance intensified", change_type="Intensified")]
    alerts = [_item(watchpoint="official stance clarified", status="Triggered")]

    result1 = monitoring.promote_updates_and_alerts(
        client, theme_id=theme["theme_id"], narrative_name="強硬姿勢",
        updates=updates, alerts=alerts, source_ref=SOURCE_REF)
    assert len(result1["updates"]) == 1
    assert len(result1["alerts"]) == 1
    assert len(client.tables["geo_events"]) == 2
    assert client.tables["geo_events"][0]["unknown_fields"]["report_studio_narrative_id"] == "S1-N01"

    # 同じsynthesis_idで再度呼んでも重複投稿しない
    result2 = monitoring.promote_updates_and_alerts(
        client, theme_id=theme["theme_id"], narrative_name="強硬姿勢",
        updates=updates, alerts=alerts, source_ref=SOURCE_REF)
    assert len(result2["updates"]) == 0
    assert len(result2["alerts"]) == 0
    assert len(client.tables["geo_events"]) == 2


# ---------------------------------------------------------------------------
# Intelligence Needs（geo_knowledge_gaps）書き込み・重複統合
# ---------------------------------------------------------------------------

def test_create_gap_from_report_studio_inserts_new_gap():
    client = _base_client()
    candidate = {"reason": "watchpoint_possible_trigger", "detail": "検証メカニズムの部分的な言及", "priority": "medium"}
    gap = monitoring.create_gap_from_report_studio(
        client, project=PROJECT, narrative_row_data=NARRATIVE_HIGH, candidate=candidate, source_ref=SOURCE_REF)
    assert gap is not None
    assert gap["origin_type"] == "external_app_request"
    assert gap["requested_by_app"] == "report_studio"
    assert gap["source_context"]["synthesis_id"] == "synth-1"
    assert len(client.tables["geo_knowledge_gaps"]) == 1


def test_create_gap_from_report_studio_merges_duplicate():
    client = _base_client()
    candidate = {"reason": "watchpoint_possible_trigger", "detail": "検証メカニズムの部分的な言及", "priority": "medium"}
    first = monitoring.create_gap_from_report_studio(
        client, project=PROJECT, narrative_row_data=NARRATIVE_HIGH, candidate=candidate, source_ref=SOURCE_REF)
    second = monitoring.create_gap_from_report_studio(
        client, project=PROJECT, narrative_row_data=NARRATIVE_HIGH, candidate=candidate, source_ref=SOURCE_REF)
    assert len(client.tables["geo_knowledge_gaps"]) == 1
    assert second["gap_id"] == first["gap_id"]
    assert client.tables["geo_knowledge_gaps"][0]["request_count"] == 2


# ---------------------------------------------------------------------------
# オーケストレーション
# ---------------------------------------------------------------------------

def test_promote_synthesis_end_to_end():
    client = _base_client()
    synthesis = {
        "id": "synth-1",
        "run_id": "run-1",
        "signal_id": "S1",
        "narrative_id": "S1-N01",
        "final_draft": {
            "central_assessment": _item(summary="strong stance", confidence="Medium"),
            "continuity_assessment": {"state": "Continuing", "rationale": "..."},
            "findings": [],
            "assessment_delta": [
                _item(item="verification stance intensified", change_type="Intensified"),
                _item(item="china stance unchanged", change_type="Stable"),
            ],
            "watchpoint_updates": [
                _item(watchpoint="official stance clarified", status="Triggered"),
                _item(watchpoint="anonymous report confirmed", status="Possible_trigger"),
            ],
        },
    }
    result = monitoring.promote_synthesis(
        client, project=PROJECT, narrative_row_data=NARRATIVE_HIGH, synthesis=synthesis)

    assert result["updates_evaluated"] == 1  # Stableは除外
    assert result["updates_created"] == 1
    assert result["alerts_evaluated"] == 1
    assert result["alerts_created"] == 1
    assert result["intelligence_need_candidates"] == 1  # Possible_triggerのみ
    assert result["intelligence_needs_created"] == 1

    assert len(client.tables["geo_events"]) == 2
    assert len(client.tables["geo_knowledge_gaps"]) == 1
