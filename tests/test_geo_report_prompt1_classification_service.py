import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_current_pipeline_service as cp  # noqa: E402
import geo_report_prompt1_classification_service as classify  # noqa: E402


def _mock_azure_client(response_data: dict):
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(response_data)))],
    )
    return client


_CLUSTER_ROW = {
    "cluster_id": "AC_deadbeefdeadbe", "signal_id": "S1", "report_topic_label": "Arizona Governor",
    "narrative_id": "S1-01", "narrative_name": "Support", "cluster_records": 10, "eligible_records": 9,
    "preliminary_consistency": "Medium", "source_class_preliminary": "Public_Voice_Candidate",
    "attitude_hint": "positive",
}

_REPRESENTATIVES = [
    {"record_id": "r1", "title": "Post about Hobbs", "source_name": "Twitter", "source_bucket": "public_social",
     "language_group": "English", "match_status": "matched", "text_for_review": "I support Hobbs strongly."},
]


def test_prompt1_system_prompt_embeds_real_instruction_text():
    prompt = classify._prompt1_system_prompt()
    assert len(prompt) > 5000
    assert "targeted" in prompt.lower() or "Targeted" in prompt


def test_classify_analysis_cluster_calls_llm_and_returns_structured_data():
    response = {
        "final_topic_label": "Hobbs support narrative", "source_assessment": "Consistent public support",
        "tone_gradient": "Supportive", "voice_direction": "Positive toward candidate",
        "report_use_status": "include", "continuity_relevance": "ongoing",
        "matched_previous_topic_id": None, "change_signal_summary": "No major change",
        "noise_flag": False, "noise_reason": "", "voice_summary": "Supporters cite economic record.",
        "notable_phrases": "strongly support", "representative_quality": "High",
        "notes_for_prompt2": "Stable narrative.",
    }
    azure_client = _mock_azure_client(response)
    result = classify.classify_analysis_cluster(azure_client, "gpt-4o", _CLUSTER_ROW, _REPRESENTATIVES)
    assert result["data"]["final_topic_label"] == "Hobbs support narrative"
    assert result["data"]["representative_quality"] == "High"
    assert azure_client.chat.completions.create.called


def test_classify_sentiment_cluster_returns_11_field_relevant_fields():
    response = {
        "theme_relevance": "relevant", "source_class": "Public Voice", "reaction_type": "Evaluation",
        "attitude": "positive", "cluster_consistency": "Medium", "apply_to_cluster": True,
        "exception_pattern": [], "classification_reason": "Clear supportive evaluation of candidate.",
    }
    azure_client = _mock_azure_client(response)
    result = classify.classify_sentiment_cluster(azure_client, "gpt-4o", _CLUSTER_ROW, _REPRESENTATIVES)
    data = result["data"]
    assert data["theme_relevance"] == "relevant"
    assert data["source_class"] == "Public Voice"
    assert data["apply_to_cluster"] is True
    assert classify.exception_pattern_to_text(data["exception_pattern"]) == ""


def test_classify_sentiment_cluster_exception_pattern_joins_with_pipe():
    response = {
        "theme_relevance": "uncertain", "source_class": "Mixed", "reaction_type": "Mixed",
        "attitude": "mixed", "cluster_consistency": "Low", "apply_to_cluster": False,
        "exception_pattern": ["mixed_source", "fallback_context"],
        "classification_reason": "Cluster mixes multiple source types.",
    }
    azure_client = _mock_azure_client(response)
    result = classify.classify_sentiment_cluster(azure_client, "gpt-4o", _CLUSTER_ROW, _REPRESENTATIVES)
    assert classify.exception_pattern_to_text(result["data"]["exception_pattern"]) == "mixed_source|fallback_context"


def test_classify_targeted_stance_allows_empty_targets():
    azure_client = _mock_azure_client({"targets": []})
    result = classify.classify_targeted_stance(azure_client, "gpt-4o", _CLUSTER_ROW, _REPRESENTATIVES)
    assert result["data"]["targets"] == []


def test_classify_targeted_stance_returns_target_fields():
    response = {"targets": [{
        "target_type": "candidate", "target_name": "Katie Hobbs", "target_normalized_id": "hobbs_katie",
        "stance_polarity": "favorable", "stance_strength": "strong", "stance_confidence": "high",
        "stance_evidence": "I support Hobbs strongly.", "stance_basis": "direct_support",
        "stance_cluster_consistency": "High", "apply_stance_to_cluster": True,
        "stance_exception_pattern": "", "stance_reason": "Direct explicit support statement.",
    }]}
    azure_client = _mock_azure_client(response)
    result = classify.classify_targeted_stance(
        azure_client, "gpt-4o", _CLUSTER_ROW, _REPRESENTATIVES, candidate_target_names=["Katie Hobbs"])
    target = result["data"]["targets"][0]
    assert target["target_name"] == "Katie Hobbs"
    assert target["stance_polarity"] == "favorable"
    assert target["stance_confidence"] == "high"


# ---------------------------------------------------------------------------
# バッチ分類（複数クラスタを1回のLLM呼び出しでまとめて処理する、2026-09-11）
# ---------------------------------------------------------------------------

_CLUSTER_ROW_2 = {**_CLUSTER_ROW, "cluster_id": "AC_second00000000"}


def test_prompt1_batch_system_prompt_includes_independence_constraints():
    prompt = classify._prompt1_batch_system_prompt()
    assert "independently" in prompt
    assert "Do not merge clusters" in prompt


def test_build_analysis_cluster_batch_user_prompt_includes_every_cluster_id():
    prompt = classify.build_analysis_cluster_batch_user_prompt([
        {"cluster_row": _CLUSTER_ROW, "representatives": _REPRESENTATIVES},
        {"cluster_row": _CLUSTER_ROW_2, "representatives": _REPRESENTATIVES},
    ])
    assert _CLUSTER_ROW["cluster_id"] in prompt
    assert _CLUSTER_ROW_2["cluster_id"] in prompt


def test_classify_analysis_clusters_batch_returns_results_array():
    response = {"results": [
        {"cluster_id": _CLUSTER_ROW["cluster_id"], "final_topic_label": "Hobbs support narrative",
         "source_assessment": "Consistent", "tone_gradient": "Supportive", "voice_direction": "Positive",
         "report_use_status": "include", "continuity_relevance": "ongoing", "matched_previous_topic_id": None,
         "change_signal_summary": "No change", "noise_flag": False, "noise_reason": "",
         "voice_summary": "Supporters cite economic record.", "notable_phrases": "strongly support",
         "representative_quality": "High", "notes_for_prompt2": "Stable."},
        {"cluster_id": _CLUSTER_ROW_2["cluster_id"], "final_topic_label": "Second narrative",
         "source_assessment": "Mixed", "tone_gradient": "Neutral", "voice_direction": "Neutral",
         "report_use_status": "include", "continuity_relevance": "new", "matched_previous_topic_id": None,
         "change_signal_summary": "New topic", "noise_flag": False, "noise_reason": "",
         "voice_summary": "Mixed reactions.", "notable_phrases": "", "representative_quality": "Medium",
         "notes_for_prompt2": ""},
    ]}
    azure_client = _mock_azure_client(response)
    result = classify.classify_analysis_clusters_batch(
        azure_client, "gpt-4o",
        [{"cluster_row": _CLUSTER_ROW, "representatives": _REPRESENTATIVES},
         {"cluster_row": _CLUSTER_ROW_2, "representatives": _REPRESENTATIVES}])
    ids = [r["cluster_id"] for r in result["data"]["results"]]
    assert ids == [_CLUSTER_ROW["cluster_id"], _CLUSTER_ROW_2["cluster_id"]]


def test_classify_sentiment_clusters_batch_returns_results_array():
    response = {"results": [
        {"cluster_id": _CLUSTER_ROW["cluster_id"], "theme_relevance": "relevant", "source_class": "Public Voice",
         "reaction_type": "Evaluation", "attitude": "positive", "cluster_consistency": "Medium",
         "apply_to_cluster": True, "exception_pattern": [], "classification_reason": "Clear support."},
    ]}
    azure_client = _mock_azure_client(response)
    result = classify.classify_sentiment_clusters_batch(
        azure_client, "gpt-4o", [{"cluster_row": _CLUSTER_ROW, "representatives": _REPRESENTATIVES}])
    assert result["data"]["results"][0]["theme_relevance"] == "relevant"


def test_classify_targeted_stance_batch_returns_results_with_targets():
    response = {"results": [
        {"cluster_id": _CLUSTER_ROW["cluster_id"], "targets": [{
            "target_type": "candidate", "target_name": "Katie Hobbs", "target_normalized_id": "hobbs_katie",
            "stance_polarity": "favorable", "stance_strength": "strong", "stance_confidence": "high",
            "stance_evidence": "I support Hobbs strongly.", "stance_basis": "direct_support",
            "stance_cluster_consistency": "High", "apply_stance_to_cluster": True,
            "stance_exception_pattern": "", "stance_reason": "Direct explicit support statement.",
        }]},
        {"cluster_id": _CLUSTER_ROW_2["cluster_id"], "targets": []},
    ]}
    azure_client = _mock_azure_client(response)
    result = classify.classify_targeted_stance_batch(
        azure_client, "gpt-4o",
        [{"cluster_row": _CLUSTER_ROW, "representatives": _REPRESENTATIVES,
          "candidate_target_names": ["Katie Hobbs"]},
         {"cluster_row": _CLUSTER_ROW_2, "representatives": _REPRESENTATIVES}])
    results_by_id = {r["cluster_id"]: r for r in result["data"]["results"]}
    assert results_by_id[_CLUSTER_ROW["cluster_id"]]["targets"][0]["target_name"] == "Katie Hobbs"
    assert results_by_id[_CLUSTER_ROW_2["cluster_id"]]["targets"] == []


# ---------------------------------------------------------------------------
# compute_prompt1_quality_audit
# ---------------------------------------------------------------------------

def _sentiment_cluster_stats(cluster_id, signal_id, *, record_count, duplicate_count=0, fallback_count=0,
                              source_class_preliminary="Public_Voice_Candidate"):
    rule = cp.Rule(
        signal_source=signal_id, signal_name=signal_id, theme_id="T", theme_name="Theme",
        trend_role="primary", trend_start_date="2026-07-01", main_narrative="Main",
        sub_narrative="Sub", sub_narrative_id=f"{signal_id}-01", stance_default="Unknown",
    )
    return cp.ClusterStats(
        cluster_id=cluster_id, kind="sentiment", rule=rule, week_start="2026-08-10", week_end="2026-08-16",
        source_class_preliminary=source_class_preliminary, source_bucket="public_social",
        reaction_type_hint="Evaluation", attitude_hint="positive", cluster_cue="general",
        match_signature="matched_general", representative_limit=8,
        record_count=record_count, duplicate_count=duplicate_count, fallback_count=fallback_count,
    )


def test_compute_prompt1_quality_audit_basic_aggregation():
    clusters = {
        "SC_a": _sentiment_cluster_stats("SC_a", "S1", record_count=10, fallback_count=2),
        "SC_b": _sentiment_cluster_stats("SC_b", "S1", record_count=5, source_class_preliminary="Fact_Only_Candidate"),
    }
    classifications = {
        "SC_a": {"theme_relevance": "relevant", "source_class": "Public Voice", "attitude": "positive",
                 "apply_to_cluster": True},
        "SC_b": {"theme_relevance": "relevant", "source_class": "Fact Only", "attitude": "not_applicable",
                 "apply_to_cluster": False},
    }
    rows = classify.compute_prompt1_quality_audit(clusters, classifications)
    s1_row = next(r for r in rows if r["signal_id"] == "S1")
    all_row = next(r for r in rows if r["signal_id"] == "ALL")

    assert s1_row["total_eligible_records"] == 15
    assert s1_row["preliminary_public_voice_candidates"] == 10
    assert s1_row["final_public_voice_records"] == 10
    assert s1_row["final_threeway_sentiment_records"] == 10
    assert s1_row["excluded_fact_only"] == 5
    assert s1_row["fallback_records_total"] == 2
    assert s1_row["fallback_records_included"] == 2  # SC_aはapply_to_cluster=True
    assert s1_row["public_voice_retention_rate"] == 1.0
    assert s1_row["classification_coverage_rate"] == 1.0
    assert s1_row["qa_status"] == "PASS"
    assert all_row["total_eligible_records"] == 15


def test_compute_prompt1_quality_audit_flags_low_retention_as_review_required():
    clusters = {
        "SC_a": _sentiment_cluster_stats("SC_a", "S1", record_count=100),
    }
    classifications = {
        "SC_a": {"theme_relevance": "irrelevant", "source_class": "Media", "attitude": "not_applicable",
                 "apply_to_cluster": False},
    }
    rows = classify.compute_prompt1_quality_audit(clusters, classifications)
    s1_row = next(r for r in rows if r["signal_id"] == "S1")
    assert s1_row["public_voice_retention_rate"] == 0.0
    assert s1_row["qa_status"] == "REVIEW REQUIRED"
    assert "retention" in s1_row["qa_notes"]


def test_compute_prompt1_quality_audit_unclassified_reduces_coverage():
    clusters = {
        "SC_a": _sentiment_cluster_stats("SC_a", "S1", record_count=10),
        "SC_b": _sentiment_cluster_stats("SC_b", "S1", record_count=10),
    }
    classifications = {
        "SC_a": {"theme_relevance": "relevant", "source_class": "Public Voice", "attitude": "positive",
                 "apply_to_cluster": True},
        # SC_bは分類結果が無い(未分類)
    }
    rows = classify.compute_prompt1_quality_audit(clusters, classifications)
    s1_row = next(r for r in rows if r["signal_id"] == "S1")
    assert s1_row["classification_coverage_rate"] == 0.5
    assert s1_row["qa_status"] == "REVIEW REQUIRED"


def test_compute_prompt1_quality_audit_targeted_stance_rates():
    clusters = {
        "SC_a": _sentiment_cluster_stats("SC_a", "S1", record_count=10),
    }
    classifications = {
        "SC_a": {"theme_relevance": "relevant", "source_class": "Public Voice", "attitude": "positive",
                 "apply_to_cluster": True},
    }
    stances = {"SC_a": [
        {"stance_polarity": "favorable", "stance_confidence": "high"},
        {"stance_polarity": "unclear", "stance_confidence": "low"},
    ]}
    rows = classify.compute_prompt1_quality_audit(clusters, classifications, stances)
    s1_row = next(r for r in rows if r["signal_id"] == "S1")
    assert s1_row["targeted_stance_available_records"] == 10
    assert s1_row["targeted_stance_directional_records"] == 10
    assert s1_row["targeted_stance_high_medium_records"] == 10
    assert s1_row["targeted_stance_low_confidence_records"] == 10
    assert s1_row["targeted_stance_ambiguous_records"] == 10
    assert s1_row["targeted_stance_coverage_rate"] == 1.0
    assert s1_row["targeted_stance_usable_rate"] == 1.0
