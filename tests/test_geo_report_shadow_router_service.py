import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_embedding_benchmark_service as embsvc  # noqa: E402
import geo_report_shadow_router_service as shadow  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

RUN_ID = "run-1"
BENCHMARK_RUN_ID = "bench-1"
TASK = "sc_classification"


@pytest.fixture(autouse=True)
def _fake_embedding_model(monkeypatch):
    # apply_shadow_router_to_run()はcompute_embeddings_for_representatives()を呼ぶため
    # (training時に手作業でcacheへ入れた値と、apply時に実際に計算される値の不一致を防ぐ
    # ため)、length-basedな決定的encoderへ差し替える。テキスト長で2クラスが分離される
    # よう、representativeのtext_for_review長を意図的に設計する(下記_seed_*参照)。
    def _encode(texts):
        return [np.array([float(len(t)), float(len(t)) * 2.0], dtype=np.float32) for t in texts]
    monkeypatch.setattr(embsvc, "_encode_texts", _encode)

_SC_LABEL_A = {
    "theme_relevance": "relevant", "source_class": "Public Voice", "reaction_type": "Evaluation",
    "attitude": "positive", "cluster_consistency": "High", "apply_to_cluster": True,
}
_SC_LABEL_B = {
    "theme_relevance": "irrelevant", "source_class": "Media", "reaction_type": "Information Only",
    "attitude": "neutral", "cluster_consistency": "Low", "apply_to_cluster": False,
}


def _seed_reference(client, cluster_id, label):
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


def _seed_sentiment_cluster(client, cluster_id):
    client.insert("geo_report_sentiment_clusters", [{
        "id": f"row-{cluster_id}", "run_id": RUN_ID, "sentiment_cluster_id": cluster_id,
        "signal_id": "S1", "report_topic_label": "Test",
    }])


# text_for_reviewの文字数(length-based fake encoderにより embedding=[len, len*2])で
# 2クラスが分離するよう、Aは短文・Bは長文にする(evidence sufficiency閾値50文字を
# 超える長さにする)。TEXT_MIDはconfidence cutoffが意味を持つ検証点を作るための
# 境界線上の長さ(A/Bのほぼ中間)。
_TEXT_A = "short A content " * 4
_TEXT_B = "much longer B content " * 6
_TEXT_MID = "b" * 103


def _seed_representative(client, cluster_id, rep_id, *, text):
    client.insert("geo_report_sentiment_cluster_representatives", [{
        "id": rep_id, "run_id": RUN_ID, "sentiment_cluster_id": cluster_id, "title": "", "text_for_review": text,
    }])


def _seed_cluster(client, cluster_id, label, split, *, text):
    _seed_reference(client, cluster_id, label)
    _seed_split(client, cluster_id, split)
    _seed_sentiment_cluster(client, cluster_id)
    _seed_representative(client, cluster_id, f"rep-{cluster_id}", text=text)


def _seed_dataset(client):
    _seed_cluster(client, "SC_1", _SC_LABEL_A, "train", text=_TEXT_A)
    _seed_cluster(client, "SC_2", _SC_LABEL_A, "train", text=_TEXT_A + "!")
    _seed_cluster(client, "SC_3", _SC_LABEL_B, "train", text=_TEXT_B)
    _seed_cluster(client, "SC_4", _SC_LABEL_B, "train", text=_TEXT_B + "!")
    _seed_cluster(client, "SC_5", _SC_LABEL_A, "validation", text=_TEXT_A + "??")
    _seed_cluster(client, "SC_6", _SC_LABEL_B, "validation", text=_TEXT_B + "??")
    # 境界線上(低confidence)のvalidation点を追加——これが無いと、validationが2件だけの
    # 場合に「confidenceの高い順で下位30%」の閾値がほぼ最大値付近になり、test側の
    # 高confidence点まで誤ってescalateされてしまう(quantile推定が粗すぎるため)。
    _seed_cluster(client, "SC_10", _SC_LABEL_A, "validation", text=_TEXT_MID)
    _seed_cluster(client, "SC_11", _SC_LABEL_B, "validation", text=_TEXT_MID + "x")
    _seed_cluster(client, "SC_7", _SC_LABEL_A, "test", text=_TEXT_A + "?")
    _seed_cluster(client, "SC_8", _SC_LABEL_B, "test", text=_TEXT_B + "?")
    # trainingが読むcentroidはgeo_report_embedding_cacheから来るため、事前に一括計算しておく
    all_cluster_ids = [f"SC_{i}" for i in range(1, 9)] + ["SC_10", "SC_11"]
    embsvc.compute_embeddings_for_representatives(client, RUN_ID, "sentiment", all_cluster_ids)


def _train(client, method="logistic_regression"):
    return shadow.train_shadow_router_model(client, BENCHMARK_RUN_ID, RUN_ID, method, "temporal",
                                              target_escalation_rate=0.3)


def test_train_shadow_router_model_saves_frozen_model_and_round_trips():
    client = FakeSupabaseClient()
    _seed_dataset(client)

    model_row = _train(client)
    assert model_row is not None
    assert model_row["role"] == "primary"
    assert model_row["task"] == "sc_classification"

    saved = client.select("geo_report_shadow_router_models", {"select": "*", "id": f"eq.{model_row['id']}"})
    assert len(saved) == 1

    loaded = shadow.load_shadow_router_model(client, model_row["id"])
    assert set(loaded["classifiers"].keys()) == {
        "theme_relevance", "source_class", "reaction_type", "attitude", "cluster_consistency", "apply_to_cluster"}
    assert len(loaded["train_centroids"]) == 4  # 4 train clusters
    assert loaded["train_centroids"][0].shape == (2,)

    # Cross-Run適用前に「何で学習したか」を追跡できるprovenance情報
    assert model_row["model_version"] == shadow.MODEL_VERSION
    assert model_row["reference_dataset_execution_id"] == "exec-1"  # _seed_referenceが使うexecution_id
    assert model_row["embedding_model"] == embsvc.EMBEDDING_MODEL_NAME
    assert model_row["embedding_version"] == embsvc.EMBEDDING_MODEL_VERSION
    assert set(model_row["calibration_json"].keys()) == set(loaded["classifiers"].keys())


def test_hybrid_method_gets_challenger_role():
    client = FakeSupabaseClient()
    _seed_dataset(client)
    # hybrid needs numeric features too
    for cid in ["SC_1", "SC_2", "SC_3", "SC_4", "SC_5", "SC_6"]:
        client.insert("geo_report_cluster_features", [{
            "id": f"feat-{cid}", "run_id": RUN_ID, "cluster_type": "sentiment", "cluster_id": cid,
            "feature_version": shadow.FEATURE_VERSION, "fallback_share": 0.0, "source_entropy": 0.0,
            "language_entropy": 0.0, "preliminary_consistency_score": 1.0, "complexity_score": 0.0,
            "representative_diversity_score": 0.0,
        }])
    model_row = _train(client, method="hybrid")
    assert model_row["role"] == "challenger"


def test_apply_shadow_router_accepts_confident_well_evidenced_cluster():
    client = FakeSupabaseClient()
    _seed_dataset(client)
    model_row = _train(client)

    rows = shadow.apply_shadow_router_to_run(client, model_row["id"], RUN_ID, cluster_ids=["SC_7", "SC_8"])
    assert len(rows) == 2
    for r in rows:
        assert r["router_decision"] == "lightweight_accept"
        assert r["router_reason"] == []
        assert r["evidence_sufficiency_json"]["sufficient"] is True


def test_apply_shadow_router_forces_escalate_on_insufficient_evidence():
    client = FakeSupabaseClient()
    _seed_dataset(client)
    # SC_9: well-separated (confident) point but empty representative text -> must still escalate
    _seed_reference(client, "SC_9", _SC_LABEL_A)
    _seed_split(client, "SC_9", "test")
    _seed_sentiment_cluster(client, "SC_9")
    _seed_representative(client, "SC_9", "rep-SC_9", text="")
    embsvc.compute_embeddings_for_representatives(client, RUN_ID, "sentiment", ["SC_9"])

    model_row = _train(client)
    rows = shadow.apply_shadow_router_to_run(client, model_row["id"], RUN_ID, cluster_ids=["SC_9"])
    assert len(rows) == 1
    assert rows[0]["router_decision"] == "would_escalate"
    assert "insufficient_evidence" in rows[0]["router_reason"]


def test_apply_shadow_router_computes_field_agreement_when_canonical_available():
    client = FakeSupabaseClient()
    _seed_dataset(client)
    model_row = _train(client)

    client.insert("geo_report_collection_runs", [{
        "run_id": RUN_ID, "canonical_prompt1_execution_id": "exec-canon-1",
    }])
    client.insert("geo_report_sentiment_cluster_classifications", [{
        "id": "cls-1", "execution_id": "exec-canon-1", "run_id": RUN_ID, "sentiment_cluster_id": "SC_7",
        **_SC_LABEL_A,
    }])

    rows = shadow.apply_shadow_router_to_run(client, model_row["id"], RUN_ID, cluster_ids=["SC_7", "SC_8"])
    by_id = {r["cluster_id"]: r for r in rows}
    assert by_id["SC_7"]["canonical_strong_result_json"] is not None
    assert by_id["SC_7"]["critical_field_disagreement"] is False
    assert by_id["SC_8"]["canonical_strong_result_json"] is None
    assert by_id["SC_8"]["field_agreement_json"] is None


def test_save_and_summarize_shadow_router_predictions():
    client = FakeSupabaseClient()
    _seed_dataset(client)
    _seed_reference(client, "SC_9", _SC_LABEL_A)
    _seed_split(client, "SC_9", "test")
    _seed_sentiment_cluster(client, "SC_9")
    _seed_representative(client, "SC_9", "rep-SC_9", text="")
    embsvc.compute_embeddings_for_representatives(client, RUN_ID, "sentiment", ["SC_9"])

    model_row = _train(client)
    rows = shadow.apply_shadow_router_to_run(client, model_row["id"], RUN_ID, cluster_ids=["SC_7", "SC_8", "SC_9"])
    shadow.save_shadow_router_predictions(client, rows)

    saved = client.select("geo_report_shadow_router_predictions", {
        "select": "id", "model_id": f"eq.{model_row['id']}", "run_id": f"eq.{RUN_ID}"})
    assert len(saved) == 3

    # idempotent re-save
    shadow.save_shadow_router_predictions(client, rows)
    saved_again = client.select("geo_report_shadow_router_predictions", {
        "select": "id", "model_id": f"eq.{model_row['id']}", "run_id": f"eq.{RUN_ID}"})
    assert len(saved_again) == 3

    summary = shadow.compute_shadow_router_run_summary(client, model_row["id"], RUN_ID)
    assert summary["n_total_clusters"] == 3
    assert summary["would_escalate_count"] == 1  # SC_9 (insufficient evidence)
    assert summary["lightweight_accept_count"] == 2
    # SC_9(空文字列)はinsufficient_evidenceに加え、embedding=[0,0]がTrain centroidから
    # 遠いためnoveltyも同時にmandatory escalateしうる——複数gateの同時発火は正しい挙動。
    assert summary["escalation_reason_counts"].get("insufficient_evidence") == 1
    assert "Projected Savings" in summary["note"]
    # canonical比較が無いためRouting Safety/field agreementはNone
    assert summary["routing_safety_disagreement_rate"] is None


def test_routing_safety_flags_lightweight_accept_that_disagrees_with_canonical():
    client = FakeSupabaseClient()
    _seed_dataset(client)
    model_row = _train(client)

    client.insert("geo_report_collection_runs", [{
        "run_id": RUN_ID, "canonical_prompt1_execution_id": "exec-canon-1",
    }])
    # SC_7はモデルが正しくlabel_Aとして予測するはずだが、Canonical Strong LLM側は
    # (架空の)label_Bを返した、というケース——lightweight_acceptしたにも関わらず
    # 実際にはCanonicalと不一致だった、というRouting Safety上の懸念を再現する。
    client.insert("geo_report_sentiment_cluster_classifications", [{
        "id": "cls-7", "execution_id": "exec-canon-1", "run_id": RUN_ID, "sentiment_cluster_id": "SC_7",
        **_SC_LABEL_B,
    }])
    client.insert("geo_report_sentiment_cluster_classifications", [{
        "id": "cls-8", "execution_id": "exec-canon-1", "run_id": RUN_ID, "sentiment_cluster_id": "SC_8",
        **_SC_LABEL_B,
    }])

    rows = shadow.apply_shadow_router_to_run(client, model_row["id"], RUN_ID, cluster_ids=["SC_7", "SC_8"])
    shadow.save_shadow_router_predictions(client, rows)
    assert all(r["router_decision"] == "lightweight_accept" for r in rows)  # 両方confident/evidence充分
    assert rows[0]["critical_field_disagreement"] is True  # SC_7: 予測A vs canonical B

    summary = shadow.compute_shadow_router_run_summary(client, model_row["id"], RUN_ID)
    # SC_7は不一致・SC_8は一致(label_Bを予測しlabel_Bと一致) -> 2件中1件disagree
    assert summary["routing_safety_disagreement_rate"] == 0.5
    assert "theme_relevance" in summary["field_agreement_by_field"]
    assert summary["field_agreement_by_field"]["theme_relevance"]["lightweight_accept_only"] == 0.5
    assert summary["lightweight_accept_rate"] == 1.0

    # SC_7はlabel_A(relevant/Public Voice/True)を予測したがcanonicalはlabel_B
    # (irrelevant/Media/False) -> theme_relevance/source_class/apply_to_clusterの
    # 3 fieldがcritical flip patternに該当する(attitudeはpositive/neutralで
    # 対象外パターンのため含まれない)。
    assert summary["critical_flip_count_within_lightweight_accept"] == 1  # SC_7のみ
    assert summary["critical_flip_rate_within_lightweight_accept"] == 0.5
    flips_by_field = summary["critical_flip_by_field_within_lightweight_accept"]
    assert flips_by_field.get("theme_relevance") == 1
    assert flips_by_field.get("apply_to_cluster") == 1
    assert flips_by_field.get("source_class") == 1
    assert "attitude" not in flips_by_field


def test_is_critical_flip_ignores_non_critical_value_transitions():
    # positive<->neutralのような軽微な遷移はcritical flipとして扱わない
    assert shadow._is_critical_flip("attitude", "positive", "neutral") is False
    assert shadow._is_critical_flip("attitude", "positive", "negative") is True
    # bool fieldは分類器がstr(label)で学習するため、文字列/実boolどちらの型でも
    # 正しく判定できること(型不一致バグの再発防止)
    assert shadow._is_critical_flip("apply_to_cluster", "True", False) is True
    assert shadow._is_critical_flip("apply_to_cluster", True, False) is True
    assert shadow._is_critical_flip("apply_to_cluster", "True", "True") is False
    assert shadow._is_critical_flip("noise_flag", True, False) is False  # 未定義fieldは対象外
