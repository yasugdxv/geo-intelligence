import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_prompt1_output_service as output_svc  # noqa: E402


def _mock_azure_client(response_data: dict):
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(response_data)))],
    )
    return client


_ANALYSIS_CLUSTER_ROWS = [
    {"cluster_id": "AC_1", "signal_id": "S1", "report_topic_label": "Arizona Governor"},
    {"cluster_id": "AC_2", "signal_id": "S1", "report_topic_label": "Arizona Governor"},
    {"cluster_id": "AC_3", "signal_id": "S1", "report_topic_label": "Arizona Governor"},
]


def _analysis_classification(*, report_use_status="include", noise_flag=False,
                               matched_previous_topic_id=None):
    return {
        "final_topic_label": "Support narrative", "source_assessment": "Consistent",
        "tone_gradient": "Supportive", "voice_direction": "Positive", "report_use_status": report_use_status,
        "continuity_relevance": "ongoing", "matched_previous_topic_id": matched_previous_topic_id,
        "change_signal_summary": "No change", "noise_flag": noise_flag, "noise_reason": "",
        "voice_summary": "Supporters cite record.", "notable_phrases": "strongly support",
        "representative_quality": "High", "notes_for_prompt2": "Stable.",
    }


def test_build_classified_clusters_rows_skips_unclassified_and_maps_columns():
    classifications = {"AC_1": _analysis_classification(), "AC_2": _analysis_classification()}
    rows = output_svc.build_classified_clusters_rows(_ANALYSIS_CLUSTER_ROWS, classifications)
    assert len(rows) == 2  # AC_3は未分類なので除外
    assert set(rows[0].keys()) == set(output_svc.CLASSIFIED_CLUSTERS_COLUMNS)
    assert rows[0]["cluster_id"] == "AC_1"
    assert rows[0]["final_topic_label"] == "Support narrative"


def test_build_public_voice_examples_rows_excludes_report_use_status_exclude():
    classifications = {
        "AC_1": _analysis_classification(report_use_status="include"),
        "AC_2": _analysis_classification(report_use_status="exclude"),
    }
    representatives_by_cluster = {
        "AC_1": [{"record_id": "r1", "url": "https://x.com/1", "author": "u1", "published_at": "2026-08-10",
                   "text_for_review": "I support this."}],
        "AC_2": [{"record_id": "r2", "url": "https://x.com/2", "author": "u2", "published_at": "2026-08-10",
                   "text_for_review": "Something excluded."}],
    }
    rows = output_svc.build_public_voice_examples_rows(
        [_ANALYSIS_CLUSTER_ROWS[0], _ANALYSIS_CLUSTER_ROWS[1]], classifications, representatives_by_cluster)
    assert len(rows) == 1
    assert rows[0]["cluster_id"] == "AC_1"
    assert set(rows[0].keys()) == set(output_svc.PUBLIC_VOICE_EXAMPLES_COLUMNS)
    assert rows[0]["targeted_stance_status"] == "unavailable"


def test_build_public_voice_examples_rows_limits_and_uses_best_stance():
    classifications = {"AC_1": _analysis_classification()}
    reps = [{"record_id": f"r{i}", "url": f"https://x.com/{i}", "text_for_review": "t"} for i in range(5)]
    representatives_by_cluster = {"AC_1": reps}
    stances = {"AC_1": [
        {"target_name": "Low conf target", "stance_polarity": "unclear", "stance_confidence": "low"},
        {"target_name": "High conf target", "stance_polarity": "favorable", "stance_confidence": "high"},
    ]}
    rows = output_svc.build_public_voice_examples_rows(
        [_ANALYSIS_CLUSTER_ROWS[0]], classifications, representatives_by_cluster, stances,
        max_examples_per_cluster=2)
    assert len(rows) == 2  # 上限で絞られる
    assert rows[0]["targeted_stance_status"] == "available"
    assert rows[0]["primary_stance_target"] == "High conf target"


def test_build_sentiment_cluster_classified_rows_status_derivation():
    classifications = {
        "SC_1": {"theme_relevance": "relevant", "source_class": "Public Voice", "reaction_type": "Evaluation",
                 "attitude": "positive", "cluster_consistency": "High", "apply_to_cluster": True,
                 "exception_pattern": [], "classification_reason": "clear"},
        "SC_2": {"theme_relevance": "relevant", "source_class": "Public Voice", "reaction_type": "Evaluation",
                 "attitude": "positive", "cluster_consistency": "High", "apply_to_cluster": True,
                 "exception_pattern": ["mixed_source"], "classification_reason": "mixed"},
    }
    stances = {"SC_1": [{"stance_confidence": "high"}], "SC_2": [{"stance_confidence": "low"}]}
    rows = output_svc.build_sentiment_cluster_classified_rows(classifications, stances)
    by_id = {r["sentiment_cluster_id"]: r for r in rows}
    assert by_id["SC_1"]["targeted_stance_status"] == "available"
    assert by_id["SC_1"]["targeted_stance_count"] == 1
    assert by_id["SC_2"]["targeted_stance_status"] == "ambiguous"
    assert by_id["SC_2"]["exception_pattern"] == "mixed_source"
    # SC_3相当(stanceデータなし)のケース
    no_stance_rows = output_svc.build_sentiment_cluster_classified_rows({"SC_3": classifications["SC_1"]})
    assert no_stance_rows[0]["targeted_stance_status"] == "not_applicable"
    assert set(rows[0].keys()) == set(output_svc.SENTIMENT_CLUSTER_CLASSIFIED_COLUMNS)


def test_build_targeted_stance_classified_rows_flattens_and_allows_empty():
    assert output_svc.build_targeted_stance_classified_rows({}, {}) == []
    stances = {"SC_1": [{
        "target_type": "candidate", "target_name": "Hobbs", "target_normalized_id": "hobbs",
        "stance_polarity": "favorable", "stance_strength": "strong", "stance_confidence": "high",
        "stance_evidence": "quote", "stance_basis": "direct_support", "stance_cluster_consistency": "High",
        "apply_stance_to_cluster": True, "stance_exception_pattern": "", "stance_reason": "reason",
    }]}
    rows = output_svc.build_targeted_stance_classified_rows(stances, {"SC_1": "S1"})
    assert len(rows) == 1
    assert rows[0]["signal_id"] == "S1"
    assert set(rows[0].keys()) == set(output_svc.TARGETED_STANCE_CLASSIFIED_COLUMNS)


def test_generate_continuity_change_candidate_returns_full_row():
    response = {
        "candidate_change_type": "Intensifying", "current_evidence_summary": "Support growing",
        "counter_evidence_summary": "Some pushback observed", "business_relevance": "High salience",
        "confidence": "Medium", "caution_for_prompt2": "Small sample",
    }
    azure_client = _mock_azure_client(response)
    row = output_svc.generate_continuity_change_candidate(
        azure_client, "gpt-4o", "S1", "Arizona Governor", [_analysis_classification()],
        matched_previous_topic_id="T-001", representative_example_ids=["PVE-1", "PVE-2"])
    assert set(row.keys()) == set(output_svc.CONTINUITY_CHANGE_CANDIDATES_COLUMNS)
    assert row["candidate_change_type"] == "Intensifying"
    assert row["matched_previous_topic_id"] == "T-001"
    assert row["representative_example_ids"] == "PVE-1|PVE-2"


def test_group_classified_clusters_by_topic():
    rows = [
        {"signal_id": "S1", "report_topic_label": "Topic A", "x": 1},
        {"signal_id": "S1", "report_topic_label": "Topic A", "x": 2},
        {"signal_id": "S1", "report_topic_label": "Topic B", "x": 3},
        {"signal_id": "S2", "report_topic_label": "Topic A", "x": 4},
    ]
    groups = output_svc.group_classified_clusters_by_topic(rows)
    assert len(groups[("S1", "Topic A")]) == 2
    assert len(groups[("S1", "Topic B")]) == 1
    assert len(groups[("S2", "Topic A")]) == 1


def test_generate_narrative_master_update_candidates_allows_empty():
    azure_client = _mock_azure_client({"candidates": []})
    candidates = output_svc.generate_narrative_master_update_candidates(
        azure_client, "gpt-4o", "S1", {"fallback_share": 0.1}, [])
    assert candidates == []


def test_generate_narrative_master_update_candidates_returns_signal_tagged_rows():
    response = {"candidates": [{
        "report_topic_label": "Topic A", "current_narrative": "old", "suggested_narrative": "new",
        "add_include_keywords": "kw1;kw2", "add_required_context": "", "add_exclude_keywords": "",
        "add_cluster_split_terms": "", "reason": "High fallback share", "priority": "High",
    }]}
    azure_client = _mock_azure_client(response)
    candidates = output_svc.generate_narrative_master_update_candidates(
        azure_client, "gpt-4o", "S1", {"fallback_share": 0.4}, [{"final_topic_label": "x"}])
    assert len(candidates) == 1
    assert candidates[0]["signal_id"] == "S1"
    assert set(candidates[0].keys()) == set(output_svc.NARRATIVE_MASTER_UPDATE_CANDIDATES_COLUMNS)


def test_build_prompt1_summary_md_and_classification_rule_applied_md():
    audit_rows = [
        {"signal_id": "S1", "classification_coverage_rate": 1.0, "public_voice_retention_rate": 0.8,
         "targeted_stance_usable_rate": 0.9, "qa_status": "PASS"},
        {"signal_id": "ALL", "classification_coverage_rate": 1.0, "public_voice_retention_rate": 0.8,
         "targeted_stance_usable_rate": 0.9, "qa_status": "PASS"},
    ]
    summary = output_svc.build_prompt1_summary_md(audit_rows, _ANALYSIS_CLUSTER_ROWS, [])
    assert "Prompt1 Summary" in summary
    assert "qa_status: PASS" in summary

    rule_md = output_svc.build_classification_rule_applied_md("v4.3", ["特例1件対応"])
    assert "v4.3" in rule_md
    assert "特例1件対応" in rule_md
