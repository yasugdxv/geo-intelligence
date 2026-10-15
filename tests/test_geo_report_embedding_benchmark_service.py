import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_embedding_benchmark_service as embsvc  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

RUN_ID = "run-1"


def _fake_encode(monkeypatch):
    """_encode_texts()を差し替え、実モデルを使わず決定的なdummy vectorを返す
    （テキストの長さから作る、実sentence-transformersを呼ばない）。"""
    def _encode(texts):
        return [np.array([float(len(t)), float(len(t)) * 2.0], dtype=np.float32) for t in texts]
    monkeypatch.setattr(embsvc, "_encode_texts", _encode)


def test_compute_embeddings_for_representatives_caches_and_skips_recompute(monkeypatch):
    _fake_encode(monkeypatch)
    client = FakeSupabaseClient()
    client.insert("geo_report_sentiment_cluster_representatives", [
        {"id": "rep-1", "run_id": RUN_ID, "sentiment_cluster_id": "SC_1", "title": "t1", "text_for_review": "hello"},
        {"id": "rep-2", "run_id": RUN_ID, "sentiment_cluster_id": "SC_1", "title": "t2", "text_for_review": "world!"},
    ])

    new_count = embsvc.compute_embeddings_for_representatives(client, RUN_ID, "sentiment", ["SC_1"])
    assert new_count == 2

    cached = client.select("geo_report_embedding_cache", {"select": "*", "run_id": f"eq.{RUN_ID}"})
    assert len(cached) == 2
    assert all(c["entity_type"] == "representative" for c in cached)
    assert all(c["embedding_model"] == embsvc.EMBEDDING_MODEL_NAME for c in cached)

    # re-running should not recompute (same texts -> same hash -> cache hit)
    new_count_2 = embsvc.compute_embeddings_for_representatives(client, RUN_ID, "sentiment", ["SC_1"])
    assert new_count_2 == 0
    cached_again = client.select("geo_report_embedding_cache", {"select": "id", "run_id": f"eq.{RUN_ID}"})
    assert len(cached_again) == 2


def test_get_embeddings_returns_vectors_by_entity_id(monkeypatch):
    _fake_encode(monkeypatch)
    client = FakeSupabaseClient()
    client.insert("geo_report_sentiment_cluster_representatives", [
        {"id": "rep-1", "run_id": RUN_ID, "sentiment_cluster_id": "SC_1", "title": "t1", "text_for_review": "hello"},
    ])
    embsvc.compute_embeddings_for_representatives(client, RUN_ID, "sentiment", ["SC_1"])

    vectors = embsvc.get_embeddings(client, RUN_ID, "representative", ["rep-1", "rep-missing"])
    assert set(vectors.keys()) == {"rep-1"}
    assert vectors["rep-1"].shape == (2,)


def test_compute_cluster_centroid_averages_representative_embeddings(monkeypatch):
    _fake_encode(monkeypatch)
    client = FakeSupabaseClient()
    client.insert("geo_report_sentiment_cluster_representatives", [
        {"id": "rep-1", "run_id": RUN_ID, "sentiment_cluster_id": "SC_1", "title": "", "text_for_review": "ab"},
        {"id": "rep-2", "run_id": RUN_ID, "sentiment_cluster_id": "SC_1", "title": "", "text_for_review": "abcd"},
    ])
    embsvc.compute_embeddings_for_representatives(client, RUN_ID, "sentiment", ["SC_1"])

    centroid = embsvc.compute_cluster_centroid(client, RUN_ID, "sentiment", "SC_1")
    assert centroid is not None
    # normalize_text("", "ab").strip() == "ab" (len 2) -> [2,4]; ("", "abcd") -> "abcd" (len 4) -> [4,8]
    # mean == [3,6]
    assert np.allclose(centroid, np.array([3.0, 6.0]))


def test_compute_cluster_centroid_returns_none_when_no_embeddings():
    client = FakeSupabaseClient()
    assert embsvc.compute_cluster_centroid(client, RUN_ID, "sentiment", "SC_missing") is None
