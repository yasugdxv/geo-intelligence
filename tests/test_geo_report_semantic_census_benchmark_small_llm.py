import json
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_semantic_census_benchmark_service as bench  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

RUN_ID = "run-1"
BENCHMARK_RUN_ID = "bench-1"
TASK = "sc_classification"

_HEADING_ID_RE = re.compile(r"^### cluster_id: (\S+)", re.MULTILINE)

_SC_LABEL_A = {
    "theme_relevance": "relevant", "source_class": "Public Voice", "reaction_type": "Evaluation",
    "attitude": "positive", "cluster_consistency": "High", "apply_to_cluster": True,
}


def _mock_azure_client_returning(label: dict):
    client = MagicMock()

    def _create(*args, messages, **kwargs):
        user_content = messages[-1]["content"]
        ids = _HEADING_ID_RE.findall(user_content)
        results = []
        for cid in ids:
            item = {"cluster_id": cid}
            for k, v in label.items():
                item[k] = v
                item[f"{k}_confidence"] = 0.9
            results.append(item)
        payload = {"results": results}
        return MagicMock(choices=[MagicMock(message=MagicMock(content=json.dumps(payload)))], usage=None)

    client.chat.completions.create.side_effect = _create
    return client


def _seed_cluster(client, cluster_id, split):
    client.insert("geo_report_sentiment_clusters", [{
        "id": f"row-{cluster_id}", "run_id": RUN_ID, "sentiment_cluster_id": cluster_id,
        "signal_id": "S1", "report_topic_label": "Test",
    }])
    client.insert("geo_report_sentiment_cluster_representatives", [{
        "id": f"rep-{cluster_id}", "run_id": RUN_ID, "sentiment_cluster_id": cluster_id,
        "representative_no": 1, "record_id": "r1", "title": "t", "text_for_review": "some text",
    }])
    client.insert("geo_report_reference_examples", [{
        "id": f"ref-{cluster_id}", "run_id": RUN_ID, "execution_id": "exec-1", "task": TASK,
        "cluster_type": "sentiment", "cluster_id": cluster_id, "item_key": "", "label_json": _SC_LABEL_A,
    }])
    client.insert("geo_report_benchmark_split_assignments", [{
        "id": f"split-{cluster_id}", "benchmark_run_id": BENCHMARK_RUN_ID, "run_id": RUN_ID, "task": TASK,
        "cluster_type": "sentiment", "cluster_id": cluster_id, "item_key": "",
        "split_scheme": "temporal", "split": split, "excluded_due_to_temporal_leakage": False,
    }])


def _mock_azure_client_dropping_once(label: dict, drop_cluster_id: str):
    """1回目の呼び出しでdrop_cluster_idだけ結果から欠落させ(missing再現)、
    2回目以降(アイテム単位retry)は全件返す。"""
    client = MagicMock()
    call_count = {"n": 0}

    def _create(*args, messages, **kwargs):
        call_count["n"] += 1
        user_content = messages[-1]["content"]
        ids = _HEADING_ID_RE.findall(user_content)
        results = []
        for cid in ids:
            if call_count["n"] == 1 and cid == drop_cluster_id:
                continue
            item = {"cluster_id": cid}
            for k, v in label.items():
                item[k] = v
                item[f"{k}_confidence"] = 0.9
            results.append(item)
        payload = {"results": results}
        return MagicMock(choices=[MagicMock(message=MagicMock(content=json.dumps(payload)))], usage=None)

    client.chat.completions.create.side_effect = _create
    return client, call_count


def test_classify_clusters_with_small_llm_retries_missing_cluster_item_level():
    client = FakeSupabaseClient()
    for cid in ["SC_1", "SC_2", "SC_3"]:
        _seed_cluster(client, cid, "test")
    azure_client, call_count = _mock_azure_client_dropping_once(_SC_LABEL_A, drop_cluster_id="SC_2")
    loaded = bench._CLUSTER_LOADER["sentiment"](client, RUN_ID, ["SC_1", "SC_2", "SC_3"])

    results = bench._classify_clusters_with_small_llm(
        azure_client, "gpt-4o-mini", TASK, loaded, ["SC_1", "SC_2", "SC_3"], batch_size=20)

    # 1回目でSC_2が欠落しても、バッチ全体を再送せずSC_2だけ再試行して回収できること。
    assert set(results.keys()) == {"SC_1", "SC_2", "SC_3"}
    assert call_count["n"] == 2  # 1回目(3件)+2回目(SC_2のみのretry)


def test_classify_clusters_with_small_llm_categorizes_transport_vs_success(monkeypatch):
    import openai
    client = FakeSupabaseClient()
    for cid in ["SC_1", "SC_2"]:
        _seed_cluster(client, cid, "test")
    azure_client = _mock_azure_client_returning(_SC_LABEL_A)
    loaded = bench._CLUSTER_LOADER["sentiment"](client, RUN_ID, ["SC_1", "SC_2"])

    real_classify = bench.classify_semantic_census_batch
    call_count = {"n": 0}

    def _flaky(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise openai.APITimeoutError(request=MagicMock())
        return real_classify(*args, **kwargs)

    monkeypatch.setattr(bench, "classify_semantic_census_batch", _flaky)

    diagnostics = bench._new_diagnostics()
    results = bench._classify_clusters_with_small_llm(
        azure_client, "gpt-4o-mini", TASK, loaded, ["SC_1", "SC_2"], batch_size=20, diagnostics=diagnostics)

    assert set(results.keys()) == {"SC_1", "SC_2"}
    assert diagnostics["total_calls"] == 2  # 1回目timeout + 2回目成功
    assert diagnostics["successful_calls"] == 1
    assert diagnostics["failed_calls_by_category"]["transport_timeout"] == 1
    assert diagnostics["final_unresolved_clusters"] == 0
    summary = bench._summarize_diagnostics(diagnostics)
    assert summary["latency_ms_p50"] is not None


def test_run_small_llm_method_benchmark_end_to_end():
    client = FakeSupabaseClient()
    for cid in ["SC_1", "SC_2"]:
        _seed_cluster(client, cid, "train")
    _seed_cluster(client, "SC_3", "validation")
    _seed_cluster(client, "SC_4", "test")
    azure_client = _mock_azure_client_returning(_SC_LABEL_A)

    result = bench.run_small_llm_method_benchmark(
        client, azure_client, "gpt-4o-mini", BENCHMARK_RUN_ID, RUN_ID, TASK, "sentiment", "temporal")

    assert result["benchmark"] is not None
    b = result["benchmark"]
    assert b["method"] == "small_llm"
    assert b["method_variant"] == "gpt-4o-mini"
    for f, m in b["field_metrics_json"].items():
        assert m["reference_agreement"] == 1.0  # mock always returns the reference label itself

    predictions = result["predictions"]
    assert {p["split"] for p in predictions} == {"validation", "test"}
    for p in predictions:
        assert p["method"] == "small_llm"
        assert p["field_calibrated_confidence_json"]


def test_run_small_llm_method_benchmark_collect_diagnostics():
    client = FakeSupabaseClient()
    for cid in ["SC_1", "SC_2"]:
        _seed_cluster(client, cid, "train")
    _seed_cluster(client, "SC_3", "validation")
    _seed_cluster(client, "SC_4", "test")
    azure_client = _mock_azure_client_returning(_SC_LABEL_A)

    result = bench.run_small_llm_method_benchmark(
        client, azure_client, "gpt-4o-mini", BENCHMARK_RUN_ID, RUN_ID, TASK, "sentiment", "temporal",
        collect_diagnostics=True)

    assert "diagnostics" in result
    assert result["diagnostics"]["validation"]["successful_calls"] == 1
    assert result["diagnostics"]["test"]["successful_calls"] == 1
    assert result["diagnostics"]["validation"]["failed_calls_by_category"] == {}

    # collect_diagnostics=False(既定)は既存呼び出し元互換のためキー自体が無い
    result_default = bench.run_small_llm_method_benchmark(
        client, azure_client, "gpt-4o-mini", BENCHMARK_RUN_ID, RUN_ID, TASK, "sentiment", "temporal")
    assert "diagnostics" not in result_default
