import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_semantic_census_benchmark_service as bench  # noqa: E402
import geo_report_embedding_benchmark_service as embsvc  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

RUN_ID = "run-1"
BENCHMARK_RUN_ID = "bench-1"
TASK = "sc_classification"


def _seed_reference(client, cluster_id, label: dict):
    client.insert("geo_report_reference_examples", [{
        "id": f"ref-{cluster_id}", "run_id": RUN_ID, "execution_id": "exec-1", "task": TASK,
        "cluster_type": "sentiment", "cluster_id": cluster_id, "item_key": "", "label_json": label,
    }])


def _seed_split(client, cluster_id, split):
    client.insert("geo_report_benchmark_split_assignments", [{
        "id": f"split-{cluster_id}", "benchmark_run_id": BENCHMARK_RUN_ID, "run_id": RUN_ID, "task": TASK,
        "cluster_type": "sentiment", "cluster_id": cluster_id, "item_key": "",
        "split_scheme": "temporal", "split": split, "excluded_due_to_temporal_leakage": False,
    }])


def _seed_representative_with_embedding(client, cluster_id, rep_id, vector):
    client.insert("geo_report_sentiment_cluster_representatives", [{
        "id": rep_id, "run_id": RUN_ID, "sentiment_cluster_id": cluster_id, "title": "", "text_for_review": "x",
    }])
    client.insert("geo_report_embedding_cache", [{
        "id": f"emb-{rep_id}", "run_id": RUN_ID, "entity_type": "representative", "entity_id": rep_id,
        "embedding_model": embsvc.EMBEDDING_MODEL_NAME, "embedding_version": embsvc.EMBEDDING_MODEL_VERSION,
        "input_text_hash": f"hash-{rep_id}",
        "embedding_vector": list(vector), "dimension": len(vector),
    }])


_SC_LABEL_A = {
    "theme_relevance": "relevant", "source_class": "Public Voice", "reaction_type": "Evaluation",
    "attitude": "positive", "cluster_consistency": "High", "apply_to_cluster": True,
}
_SC_LABEL_B = {
    "theme_relevance": "irrelevant", "source_class": "Media", "reaction_type": "Information Only",
    "attitude": "neutral", "cluster_consistency": "Low", "apply_to_cluster": False,
}


def _seed_cluster(client, cluster_id, label, split, vector):
    _seed_reference(client, cluster_id, label)
    _seed_split(client, cluster_id, split)
    _seed_representative_with_embedding(client, cluster_id, f"rep-{cluster_id}", vector)


def _seed_dataset(client):
    # 4 train (2 of each label near [1,0] / [0,1]), 2 validation, 2 test
    _seed_cluster(client, "SC_1", _SC_LABEL_A, "train", [1.0, 0.05])
    _seed_cluster(client, "SC_2", _SC_LABEL_A, "train", [0.95, 0.0])
    _seed_cluster(client, "SC_3", _SC_LABEL_B, "train", [0.0, 1.0])
    _seed_cluster(client, "SC_4", _SC_LABEL_B, "train", [0.05, 0.95])
    _seed_cluster(client, "SC_5", _SC_LABEL_A, "validation", [0.9, 0.1])
    _seed_cluster(client, "SC_6", _SC_LABEL_B, "validation", [0.1, 0.9])
    _seed_cluster(client, "SC_7", _SC_LABEL_A, "test", [0.92, 0.02])
    _seed_cluster(client, "SC_8", _SC_LABEL_B, "test", [0.02, 0.92])


def test_run_embedding_method_benchmark_produces_clean_split_metrics():
    client = FakeSupabaseClient()
    _seed_dataset(client)

    result = bench.run_embedding_method_benchmark(
        client, BENCHMARK_RUN_ID, RUN_ID, TASK, "sentiment", "temporal", "logistic_regression")

    assert result["benchmark"] is not None
    b = result["benchmark"]
    assert b["method"] == "logistic_regression"
    assert b["split"] == "test"
    metrics = b["field_metrics_json"]
    assert set(metrics.keys()) == {
        "theme_relevance", "source_class", "reaction_type", "attitude", "cluster_consistency", "apply_to_cluster"}
    for f, m in metrics.items():
        assert m["reference_agreement"] == 1.0  # perfectly separated synthetic data
        assert m["production_eligible"] is True
        assert "class_support" in m
    assert b["cluster_exact_match_rate"] == 1.0

    predictions = result["predictions"]
    splits_seen = {p["split"] for p in predictions}
    assert splits_seen == {"validation", "test"}
    for p in predictions:
        assert p["field_calibrated_confidence_json"]
        assert p["overall_calibrated_confidence"] is not None
        assert p["unseen_reference_label_fields"] == []  # all values seen in train


def test_save_benchmark_results_is_idempotent_on_rerun():
    client = FakeSupabaseClient()
    _seed_dataset(client)
    result = bench.run_embedding_method_benchmark(
        client, BENCHMARK_RUN_ID, RUN_ID, TASK, "sentiment", "temporal", "logistic_regression")

    bench.save_benchmark_results(client, result)
    bench.save_benchmark_results(client, result)

    benchmarks = client.select("geo_report_semantic_census_benchmarks", {
        "select": "id", "benchmark_run_id": f"eq.{BENCHMARK_RUN_ID}",
    })
    predictions = client.select("geo_report_semantic_census_predictions", {
        "select": "id", "benchmark_run_id": f"eq.{BENCHMARK_RUN_ID}",
    })
    assert len(benchmarks) == 1
    assert len(predictions) == 4  # 2 validation + 2 test


def test_compute_field_metrics_reports_reference_agreement_not_accuracy_key():
    metrics = bench.compute_field_metrics(["A", "B", "A"], ["A", "B", "B"])
    assert "reference_agreement" in metrics
    assert "accuracy" not in metrics
    assert round(metrics["reference_agreement"], 4) == round(2 / 3, 4)


def test_fit_and_apply_calibration_maps_low_raw_confidence_down():
    calibrator = bench.fit_calibration([0.9, 0.9, 0.9, 0.2, 0.2, 0.2], [1, 1, 1, 0, 0, 0])
    calibrated = bench.apply_calibration(calibrator, [0.9, 0.2])
    assert calibrated[0] > calibrated[1]


def test_unseen_reference_label_fields_flags_class_missing_from_train():
    train_values = {"theme_relevance": {"relevant", "irrelevant"}}
    item_label = {"theme_relevance": "uncertain"}
    unseen = bench.unseen_reference_label_fields(train_values, item_label, ["theme_relevance"])
    assert unseen == ["theme_relevance"]
