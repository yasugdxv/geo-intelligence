import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_prompt2_output_service as output_svc  # noqa: E402


def _candidate(*, candidate_id="CC-1", signal_id="S1", report_topic_label="Arizona Governor",
                matched_previous_topic_id=None, candidate_change_type="New",
                current_evidence_summary="Support growing", business_relevance="High salience",
                confidence="Medium"):
    return {
        "candidate_id": candidate_id, "signal_id": signal_id, "report_topic_label": report_topic_label,
        "matched_previous_topic_id": matched_previous_topic_id, "candidate_change_type": candidate_change_type,
        "current_evidence_summary": current_evidence_summary, "representative_example_ids": "",
        "counter_evidence_summary": "", "business_relevance": business_relevance, "confidence": confidence,
        "caution_for_prompt2": "",
    }


def test_build_reference_examples_used_rows_maps_fields_and_role():
    examples = [{
        "example_id": "PVE-1", "signal_id": "S1", "report_topic_label": "Arizona Governor",
        "source_url": "https://x.com/1", "author_or_source": "u1", "timestamp": "2026-08-10",
        "post_summary": "summary", "notable_phrase": "phrase", "voice_direction": "Positive",
        "continuity_relevance": "ongoing",
    }]
    rows = output_svc.build_reference_examples_used_rows(examples, role_by_example_id={"PVE-1": "primary_quote"})
    assert len(rows) == 1
    assert set(rows[0].keys()) == set(output_svc.REFERENCE_EXAMPLES_USED_COLUMNS)
    assert rows[0]["role_in_report"] == "primary_quote"
    assert rows[0]["continuity_role"] == "ongoing"


def test_build_reference_examples_used_rows_defaults_role_to_empty():
    examples = [{
        "example_id": "PVE-1", "signal_id": "S1", "report_topic_label": "T", "source_url": "", "timestamp": "",
    }]
    rows = output_svc.build_reference_examples_used_rows(examples)
    assert rows[0]["role_in_report"] == ""


def test_build_continuity_context_next_first_run_no_previous():
    candidates = [_candidate(candidate_change_type="New")]
    result = output_svc.build_continuity_context_next(
        candidates, report_theme="Purple State Monitor", current_period="2026-08-19/2026-09-01")
    assert result["schema_version"] == "1.2"
    assert len(result["emerging_topics"]) == 1
    assert result["emerging_topics"][0]["topic_label"] == "Arizona Governor"
    assert result["ongoing_topics"] == []
    assert result["resolved_or_dormant_topics"] == []


def test_build_continuity_context_next_maps_change_types_to_buckets():
    candidates = [
        _candidate(candidate_id="CC-1", candidate_change_type="New"),
        _candidate(candidate_id="CC-2", candidate_change_type="Intensifying",
                    matched_previous_topic_id="T-old-1"),
        _candidate(candidate_id="CC-3", candidate_change_type="Weakening",
                    matched_previous_topic_id="T-old-2"),
        _candidate(candidate_id="CC-4", candidate_change_type="Dormant candidate",
                    matched_previous_topic_id="T-old-3"),
    ]
    result = output_svc.build_continuity_context_next(
        candidates, report_theme="T", current_period="P2")
    assert len(result["emerging_topics"]) == 1
    assert len(result["ongoing_topics"]) == 1  # Intensifying
    assert len(result["fading_topics"]) == 1  # Weakening
    assert len(result["resolved_or_dormant_topics"]) == 1  # Dormant candidate


def test_build_continuity_context_next_carries_forward_unmentioned_previous_topics():
    previous = {
        "ongoing_topics": [{"topic_id": "T-old-1", "topic_label": "Old Topic", "status": "Continuing",
                             "current_assessment": "was ongoing", "first_observed_period": "P1"}],
    }
    candidates = [_candidate(candidate_id="CC-1", candidate_change_type="New", matched_previous_topic_id=None)]
    result = output_svc.build_continuity_context_next(
        candidates, report_theme="T", current_period="P2", previous_continuity_context=previous)
    dormant_ids = {t["topic_id"] for t in result["resolved_or_dormant_topics"]}
    assert "T-old-1" in dormant_ids
    carried = next(t for t in result["resolved_or_dormant_topics"] if t["topic_id"] == "T-old-1")
    assert carried["status"] == "Dormant"
    assert carried["topic_label"] == "Old Topic"  # 黙って消さず引き継がれる


def test_build_continuity_change_log_md_first_run_vs_followup():
    empty_context = output_svc.build_continuity_context_next([], report_theme="T", current_period="P1")
    md_first = output_svc.build_continuity_change_log_md(empty_context, has_baseline=False)
    assert "初回Run" in md_first

    candidates = [_candidate(candidate_change_type="New")]
    context2 = output_svc.build_continuity_context_next(candidates, report_theme="T", current_period="P2")
    md_followup = output_svc.build_continuity_change_log_md(context2, has_baseline=True)
    assert "[New] Arizona Governor" in md_followup


def test_build_report_brief_md_with_and_without_executive_summary():
    sections = {"exec-summary": {
        "central_conclusion": "Support is stable", "change_from_previous": "No shift",
        "drivers": ["economy"], "confidence_note": "Moderate",
    }}
    brief = output_svc.build_report_brief_md(sections, executive_summary_section_id="exec-summary")
    assert "Support is stable" in brief
    assert "economy" in brief

    empty_brief = output_svc.build_report_brief_md({}, executive_summary_section_id="exec-summary")
    assert "空です" in empty_brief


def test_build_methodology_notes_md_contains_key_facts():
    notes = output_svc.build_methodology_notes_md(
        prompt1_contract_version="v4.3", prompt2_contract_version="v4.3",
        common_primary_graph_start="2026-07-01", primary_graph_signals=["S1", "S7"], model="gpt-4o")
    assert "v4.3" in notes
    assert "2026-07-01" in notes
    assert "S1, S7" in notes
    assert "gpt-4o" in notes


def test_serialize_continuity_context_next_round_trips_json():
    context = output_svc.build_continuity_context_next([], report_theme="T", current_period="P1")
    text = output_svc.serialize_continuity_context_next(context)
    assert json.loads(text)["schema_version"] == "1.2"
