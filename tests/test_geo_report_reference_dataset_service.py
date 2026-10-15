import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_reference_dataset_service as refds  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

RUN_ID = "run-1"
EXECUTION_ID = "exec-1"


def _seed_ac(client):
    client.insert("geo_report_analysis_cluster_classifications", [{
        "id": "ac-row-1", "execution_id": EXECUTION_ID, "run_id": RUN_ID, "cluster_id": "AC_1",
        "final_topic_label": "Support narrative", "source_assessment": "Consistent",
        "tone_gradient": "Supportive", "voice_direction": "Positive", "report_use_status": "include",
        "continuity_relevance": "ongoing", "matched_previous_topic_id": None,
        "change_signal_summary": "None", "noise_flag": False, "noise_reason": "",
        "voice_summary": "summary", "notable_phrases": "phrase", "representative_quality": "High",
        "notes_for_prompt2": "notes",
    }])


def _seed_sc(client):
    client.insert("geo_report_sentiment_cluster_classifications", [{
        "id": "sc-row-1", "execution_id": EXECUTION_ID, "run_id": RUN_ID, "sentiment_cluster_id": "SC_1",
        "theme_relevance": "relevant", "source_class": "Public Voice", "reaction_type": "Evaluation",
        "attitude": "positive", "cluster_consistency": "High", "apply_to_cluster": True,
        "exception_pattern": "", "classification_reason": "clear",
        "targeted_stance_status": "available", "targeted_stance_count": 1,
    }])


def _seed_ts(client):
    client.insert("geo_report_targeted_stance", [{
        "id": "ts-row-1", "execution_id": EXECUTION_ID, "run_id": RUN_ID,
        "sentiment_cluster_id": "SC_1", "signal_id": "S1",
        "target_type": "candidate", "target_name": "Hobbs", "target_normalized_id": "hobbs",
        "target_key": "hobbs", "stance_polarity": "favorable", "stance_strength": "strong",
        "stance_confidence": "high", "stance_evidence": "quote", "stance_basis": "direct_support",
        "stance_cluster_consistency": "High", "apply_stance_to_cluster": True,
        "stance_exception_pattern": "", "stance_reason": "reason",
    }])


def test_build_reference_examples_from_execution_creates_ac_sc_ts_rows():
    client = FakeSupabaseClient()
    _seed_ac(client)
    _seed_sc(client)
    _seed_ts(client)

    counts = refds.build_reference_examples_from_execution(client, RUN_ID, EXECUTION_ID)
    assert counts == {"ac_classification": 1, "sc_classification": 1, "targeted_stance": 1}

    examples = client.select("geo_report_reference_examples", {"select": "*", "execution_id": f"eq.{EXECUTION_ID}"})
    assert len(examples) == 3

    ac = next(e for e in examples if e["task"] == "ac_classification")
    assert ac["cluster_type"] == "analysis"
    assert ac["cluster_id"] == "AC_1"
    assert ac["item_key"] == ""
    assert ac["label_json"]["final_topic_label"] == "Support narrative"
    assert "id" not in ac["label_json"] and "execution_id" not in ac["label_json"]

    sc = next(e for e in examples if e["task"] == "sc_classification")
    assert sc["cluster_type"] == "sentiment"
    assert sc["cluster_id"] == "SC_1"
    assert sc["label_json"]["theme_relevance"] == "relevant"

    ts = next(e for e in examples if e["task"] == "targeted_stance")
    assert ts["cluster_type"] == "sentiment"
    assert ts["cluster_id"] == "SC_1"
    assert ts["item_key"] == "candidate:hobbs"
    assert ts["label_json"]["stance_polarity"] == "favorable"
    assert "sentiment_cluster_id" not in ts["label_json"]

    for e in examples:
        assert e["source"] == "current_prompt1_strong_llm"
        assert e["reference_quality"] == "reference_only"
        assert e["human_reviewed"] is False


def test_build_reference_examples_from_execution_is_idempotent_on_rerun():
    client = FakeSupabaseClient()
    _seed_ac(client)
    refds.build_reference_examples_from_execution(client, RUN_ID, EXECUTION_ID)
    refds.build_reference_examples_from_execution(client, RUN_ID, EXECUTION_ID)

    examples = client.select("geo_report_reference_examples", {"select": "*", "execution_id": f"eq.{EXECUTION_ID}"})
    assert len(examples) == 1
