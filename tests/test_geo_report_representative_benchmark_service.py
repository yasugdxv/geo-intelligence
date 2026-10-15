import json
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_embedding_benchmark_service as embsvc  # noqa: E402
import geo_report_representative_benchmark_service as gate1  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

RUN_ID = "run-1"
BENCHMARK_RUN_ID = "bench-1"
TASK = "sc_classification"

_HEADING_ID_RE = re.compile(r"^### sentiment_cluster_id: (\S+)", re.MULTILINE)

_SC_LABEL = {
    "theme_relevance": "relevant", "source_class": "Public Voice", "reaction_type": "Evaluation",
    "attitude": "positive", "cluster_consistency": "High", "apply_to_cluster": True,
    "exception_pattern": [], "classification_reason": "ok",
}


def _mock_azure_client_returning(label: dict):
    client = MagicMock()

    def _create(*args, messages, **kwargs):
        user_content = messages[-1]["content"]
        ids = _HEADING_ID_RE.findall(user_content)
        results = [{"cluster_id": cid, **label} for cid in ids]
        payload = {"results": results}
        return MagicMock(choices=[MagicMock(message=MagicMock(content=json.dumps(payload)))], usage=None)

    client.chat.completions.create.side_effect = _create
    return client


def _seed_cluster(client, cluster_id, split, *, with_reservoir_rep=True):
    client.insert("geo_report_sentiment_clusters", [{
        "id": f"row-{cluster_id}", "run_id": RUN_ID, "sentiment_cluster_id": cluster_id,
        "signal_id": "S1", "report_topic_label": "Test",
    }])
    if with_reservoir_rep:
        client.insert("geo_report_sentiment_cluster_representatives", [{
            "id": f"rep-{cluster_id}", "run_id": RUN_ID, "sentiment_cluster_id": cluster_id,
            "representative_no": 1, "record_id": f"r-{cluster_id}-1", "title": "t", "text_for_review": "some text",
        }])
    client.insert("geo_report_reference_examples", [{
        "id": f"ref-{cluster_id}", "run_id": RUN_ID, "execution_id": "exec-1", "task": TASK,
        "cluster_type": "sentiment", "cluster_id": cluster_id, "item_key": "", "label_json": _SC_LABEL,
    }])
    client.insert("geo_report_benchmark_split_assignments", [{
        "id": f"split-{cluster_id}", "benchmark_run_id": BENCHMARK_RUN_ID, "run_id": RUN_ID, "task": TASK,
        "cluster_type": "sentiment", "cluster_id": cluster_id, "item_key": "",
        "split_scheme": "temporal", "split": split, "excluded_due_to_temporal_leakage": False,
    }])


def test_sample_clusters_for_gate1_is_deterministic_and_excludes_ineligible():
    client = FakeSupabaseClient()
    for i in range(10):
        _seed_cluster(client, f"SC_{i}", "test")
    _seed_cluster(client, "SC_excluded_split", "validation")

    sample1 = gate1.sample_clusters_for_gate1(client, RUN_ID, BENCHMARK_RUN_ID, TASK, "temporal", n=5, seed=1)
    sample2 = gate1.sample_clusters_for_gate1(client, RUN_ID, BENCHMARK_RUN_ID, TASK, "temporal", n=5, seed=1)
    assert sample1 == sample2
    assert len(sample1) == 5
    assert "SC_excluded_split" not in sample1
    assert all(cid.startswith("SC_") for cid in sample1)


def test_rerun_strong_llm_for_variant_current_reservoir_uses_existing_representatives():
    client = FakeSupabaseClient()
    for cid in ["SC_1", "SC_2"]:
        _seed_cluster(client, cid, "test")
    azure_client = _mock_azure_client_returning(_SC_LABEL)

    results = gate1.rerun_strong_llm_for_variant(
        client, azure_client, "gpt-strong", RUN_ID, "sentiment", ["SC_1", "SC_2"], "current_reservoir")

    assert set(results.keys()) == {"SC_1", "SC_2"}
    assert results["SC_1"]["theme_relevance"] == "relevant"


def test_build_new_representative_variants_and_rerun(monkeypatch):
    def _encode(texts):
        return [np.array([float(len(t)), float(len(t)) * 2.0], dtype=np.float32) for t in texts]
    monkeypatch.setattr(embsvc, "_encode_texts", _encode)

    def _fake_load_record_texts(csv_paths, record_ids):
        return {rid: {"title": f"title-{rid}", "text": f"text body for {rid} " * (1 + hash(rid) % 5)}
                 for rid in record_ids}
    import geo_report_benchmark_raw_text_service as rawtext
    monkeypatch.setattr(rawtext, "load_record_texts", _fake_load_record_texts)

    client = FakeSupabaseClient()
    _seed_cluster(client, "SC_1", "test", with_reservoir_rep=False)
    client.insert("geo_report_records", [
        {"id": f"row-r{i}", "run_id": RUN_ID, "record_id": f"r{i}", "source_file": "f.csv", "signal_id": "S1",
         "sentiment_cluster_id": "SC_1", "source_name": "Outlet", "source_bucket": "media", "language": "en",
         "match_status": "matched"}
        for i in range(6)
    ])

    saved = gate1.build_new_representative_variants(
        client, RUN_ID, "sentiment", ["SC_1"], raw_csv_paths=["dummy.csv"])
    assert saved == 1

    variant_rows = client.select("geo_report_representative_selection_variants", {
        "select": "*", "run_id": f"eq.{RUN_ID}", "strategy": "eq.typical_diverse_boundary_v1",
    })
    assert len(variant_rows) == 1
    assert len(variant_rows[0]["representative_record_ids"]) <= 5

    azure_client = _mock_azure_client_returning(_SC_LABEL)
    results = gate1.rerun_strong_llm_for_variant(
        client, azure_client, "gpt-strong", RUN_ID, "sentiment", ["SC_1"], "typical_diverse_boundary_v1",
        raw_csv_paths=["dummy.csv"])
    assert results.get("SC_1", {}).get("theme_relevance") == "relevant"


def test_compare_three_way_reports_agreement_and_disagreements():
    historical = {"SC_1": {"theme_relevance": "relevant", "attitude": "positive"},
                  "SC_2": {"theme_relevance": "relevant", "attitude": "negative"}}
    reservoir_rerun = {"SC_1": {"theme_relevance": "relevant", "attitude": "positive"},
                       "SC_2": {"theme_relevance": "relevant", "attitude": "positive"}}  # SC_2 attitude disagrees
    new_rep_rerun = {"SC_1": {"theme_relevance": "relevant", "attitude": "positive"},
                     "SC_2": {"theme_relevance": "relevant", "attitude": "negative"}}

    result = gate1.compare_three_way(
        historical, reservoir_rerun, new_rep_rerun, ["SC_1", "SC_2"],
        fields=["theme_relevance", "attitude"])

    ab = result["pairwise"]["A_historical_vs_B_reservoir_rerun"]
    assert ab["field_agreement"]["theme_relevance"] == 1.0
    assert ab["field_agreement"]["attitude"] == 0.5
    assert ab["exact_match_rate"] == 0.5
    assert result["disagreements"]["A_historical_vs_B_reservoir_rerun"] == ["SC_2"]

    ac = result["pairwise"]["A_historical_vs_C_new_rep_rerun"]
    assert ac["exact_match_rate"] == 1.0
    assert result["disagreements"]["A_historical_vs_C_new_rep_rerun"] == []


def test_representative_stats_by_strategy_compares_count_and_tokens(monkeypatch):
    def _encode(texts):
        return [np.array([float(len(t)), float(len(t)) * 2.0], dtype=np.float32) for t in texts]
    monkeypatch.setattr(embsvc, "_encode_texts", _encode)

    def _fake_load_record_texts(csv_paths, record_ids):
        return {rid: {"title": f"title-{rid}", "text": "x" * 40} for rid in record_ids}
    import geo_report_benchmark_raw_text_service as rawtext
    monkeypatch.setattr(rawtext, "load_record_texts", _fake_load_record_texts)

    client = FakeSupabaseClient()
    _seed_cluster(client, "SC_1", "test", with_reservoir_rep=True)
    client.insert("geo_report_records", [
        {"id": f"row-r{i}", "run_id": RUN_ID, "record_id": f"r{i}", "source_file": "f.csv", "signal_id": "S1",
         "sentiment_cluster_id": "SC_1", "source_name": "Outlet", "source_bucket": "media", "language": "en",
         "match_status": "matched"}
        for i in range(6)
    ])
    gate1.build_new_representative_variants(client, RUN_ID, "sentiment", ["SC_1"], raw_csv_paths=["dummy.csv"])

    stats = gate1.representative_stats_by_strategy(client, RUN_ID, "sentiment", ["SC_1"], raw_csv_paths=["dummy.csv"])
    assert stats["current_reservoir"]["n_clusters"] == 1
    assert stats["typical_diverse_boundary_v1"]["n_clusters"] == 1
    assert stats["current_reservoir"]["avg_representative_count"] == 1  # _seed_cluster inserts 1 reservoir rep
    assert stats["typical_diverse_boundary_v1"]["avg_representative_count"] <= 5


def test_build_review_bundle_for_disagreements_includes_full_record_pool(monkeypatch):
    def _encode(texts):
        return [np.array([float(len(t)), float(len(t)) * 2.0], dtype=np.float32) for t in texts]
    monkeypatch.setattr(embsvc, "_encode_texts", _encode)

    def _fake_load_record_texts(csv_paths, record_ids):
        return {rid: {"title": f"title-{rid}", "text": f"text-{rid}"} for rid in record_ids}
    import geo_report_benchmark_raw_text_service as rawtext
    monkeypatch.setattr(rawtext, "load_record_texts", _fake_load_record_texts)

    client = FakeSupabaseClient()
    _seed_cluster(client, "SC_1", "test", with_reservoir_rep=True)
    client.insert("geo_report_records", [
        {"id": f"row-r{i}", "run_id": RUN_ID, "record_id": f"r{i}", "source_file": "f.csv", "signal_id": "S1",
         "sentiment_cluster_id": "SC_1", "source_name": "Outlet", "source_bucket": "media", "language": "en",
         "match_status": "matched"}
        for i in range(6)
    ])
    gate1.build_new_representative_variants(client, RUN_ID, "sentiment", ["SC_1"], raw_csv_paths=["dummy.csv"])

    bundle = gate1.build_review_bundle_for_disagreements(
        client, RUN_ID, "sentiment", ["SC_1"],
        historical={"SC_1": {"attitude": "positive"}},
        reservoir_rerun={"SC_1": {"attitude": "positive"}},
        new_rep_rerun={"SC_1": {"attitude": "negative"}},
        raw_csv_paths=["dummy.csv"])

    assert "SC_1" in bundle
    entry = bundle["SC_1"]
    assert entry["historical_reference"] == {"attitude": "positive"}
    assert entry["new_representative_rerun"] == {"attitude": "negative"}
    assert len(entry["full_record_pool"]) == 6
    assert all(r["text"] for r in entry["full_record_pool"])


def test_sample_disagreements_for_review_is_deterministic_and_capped():
    ids = [f"SC_{i}" for i in range(50)]
    sample1 = gate1.sample_disagreements_for_review(ids, n=10, seed=7)
    sample2 = gate1.sample_disagreements_for_review(ids, n=10, seed=7)
    assert sample1 == sample2
    assert len(sample1) == 10
    assert set(sample1) <= set(ids)
