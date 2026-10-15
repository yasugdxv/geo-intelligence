import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_router_simulation_service as router  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

BENCHMARK_RUN_ID = "bench-1"
TASK = "sc_classification"
SPLIT_SCHEME = "temporal"
METHOD = "logistic_regression"


def _seed_prediction(client, cluster_id, split, confidence, novelty=0.1):
    client.insert("geo_report_semantic_census_predictions", [{
        "id": f"pred-{split}-{cluster_id}", "benchmark_run_id": BENCHMARK_RUN_ID, "run_id": "run-1",
        "task": TASK, "cluster_type": "sentiment", "cluster_id": cluster_id, "item_key": "",
        "method": METHOD, "method_variant": "default", "split_scheme": SPLIT_SCHEME, "split": split,
        "field_predictions_json": {}, "field_calibrated_confidence_json": {},
        "overall_calibrated_confidence": confidence, "novelty_proxy_score": novelty,
        "nearest_neighbor_distance": novelty,
    }])


def _seed_uniform_confidences(client, split, confidences):
    for i, c in enumerate(confidences):
        _seed_prediction(client, f"{split}_{i}", split, c)


def test_solve_confidence_cutoff_for_target_rate_picks_expected_quantile():
    rows = [{"overall_calibrated_confidence": c} for c in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]]
    cutoff = router.solve_confidence_cutoff_for_target_rate(rows, 0.2)
    # 20% escalated (confidence < cutoff) out of 10 -> cutoff around the 2nd-lowest value
    assert cutoff in (0.2, 0.3)


def test_simulate_router_for_method_reports_actual_rate_close_to_target():
    client = FakeSupabaseClient()
    # 100 validation + 100 test rows, confidence uniformly spread 0.01..1.00, low novelty everywhere
    _seed_uniform_confidences(client, "validation", [i / 100 for i in range(1, 101)])
    _seed_uniform_confidences(client, "test", [i / 100 for i in range(1, 101)])

    results = router.simulate_router_for_method(
        client, BENCHMARK_RUN_ID, TASK, SPLIT_SCHEME, METHOD,
        target_escalation_rates=(0.3,))

    non_importance = [r for r in results if not r["importance_routing_enabled"]]
    assert len(non_importance) == 1
    entry = non_importance[0]
    assert entry["infeasible"] is False
    assert abs(entry["actual_escalation_rate"] - 0.3) < 0.05
    assert entry["strong_llm_calls"] + entry["candidate_method_calls"] == 100


def test_simulate_router_marks_infeasible_when_mandatory_rate_exceeds_target():
    client = FakeSupabaseClient()
    # half of validation/test rows have very high novelty -> mandatory_rate ~= 0.5
    for i in range(10):
        novelty = 0.99 if i < 5 else 0.01
        _seed_prediction(client, f"val_{i}", "validation", confidence=0.9, novelty=novelty)
        _seed_prediction(client, f"test_{i}", "test", confidence=0.9, novelty=novelty)

    results = router.simulate_router_for_method(
        client, BENCHMARK_RUN_ID, TASK, SPLIT_SCHEME, METHOD, target_escalation_rates=(0.2,),
        novelty_gate_percentile=0.5)  # median threshold -> top half (novelty=0.99) gets escalated
    non_importance = [r for r in results if not r["importance_routing_enabled"]]
    assert non_importance[0]["infeasible"] is True
    assert non_importance[0]["mandatory_rate"] >= 0.2


def test_simulate_router_for_method_exposes_escalated_cluster_ids_consistently():
    client = FakeSupabaseClient()
    _seed_uniform_confidences(client, "validation", [i / 100 for i in range(1, 101)])
    _seed_uniform_confidences(client, "test", [i / 100 for i in range(1, 101)])

    results = router.simulate_router_for_method(
        client, BENCHMARK_RUN_ID, TASK, SPLIT_SCHEME, METHOD, target_escalation_rates=(0.3,))
    entry = next(r for r in results if not r["importance_routing_enabled"])

    assert len(entry["escalated_cluster_ids"]) == entry["strong_llm_calls"]
    assert len(entry["non_escalated_cluster_ids"]) == entry["candidate_method_calls"]
    assert set(entry["escalated_cluster_ids"]) & set(entry["non_escalated_cluster_ids"]) == set()
    assert len(entry["escalated_cluster_ids"]) + len(entry["non_escalated_cluster_ids"]) == 100


def test_simulate_router_importance_routing_increases_escalation():
    client = FakeSupabaseClient()
    _seed_uniform_confidences(client, "validation", [i / 20 for i in range(1, 21)])
    _seed_uniform_confidences(client, "test", [i / 20 for i in range(1, 21)])
    importance_by_cluster = {f"validation_{i}": 0.9 for i in range(20)}
    importance_by_cluster.update({f"test_{i}": 0.9 for i in range(20)})

    results = router.simulate_router_for_method(
        client, BENCHMARK_RUN_ID, TASK, SPLIT_SCHEME, METHOD, target_escalation_rates=(0.2,),
        importance_by_cluster=importance_by_cluster, importance_threshold=0.8)

    with_importance = next(r for r in results if r["importance_routing_enabled"])
    without_importance = next(r for r in results if not r["importance_routing_enabled"])
    assert with_importance["mandatory_rate"] >= without_importance["mandatory_rate"]
