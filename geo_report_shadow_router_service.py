"""Report Studio → Intelligence Analysis Platform: Phase 3A SC-only Shadow Router。

詳細設計: docs/report-studio/2026-09-13_intelligence_platform_phase0-2/SUMMARY.md
（Section 20/23）。Production Canonical（`geo_report_prompt1_orchestration_service.py`
等）は本モジュールを一切importしない・参照しない。Shadow Mode中は実際のLLM呼び出しを
一切削減しない——Routerの判断（`lightweight_accept`/`would_escalate`）は記録するのみ。

設計原則（Section 20の合意事項をそのままコードへ反映）:
1. PrimaryはLogistic Regressionに一本化する。HybridはChallenger
   （比較記録専用、Router判断には使わない）。Gradient Boosting/Small LLMは対象外。
2. Cross-Run Generalization検証のため、学習済みモデル（分類器・calibrator・
   confidence cutoff・novelty gate閾値・Train centroids）を凍結して永続化し、
   新しいRunへ適用する際も再学習しない（`geo_report_shadow_router_models`）。
3. Evidence Sufficiency Gate（Section 20.2）——representative件数/有効文字数が
   閾値未満のクラスタは、confidenceに関わらず強制escalateする
   （Current Reservoir Sampling選定が全件空文字列になりうる実在の不具合への
   guard、Gate 1 Section 18.3で発見）。
4. Importance routingはPhase 3Aでは使わない（Confidence + Novelty + Evidence
   Sufficiencyの3 gateのみ）。
5. Compatibility（Shadow vs Canonical）・Stability（Strong rerun vs Strong
   historical、Gate 1で49.3%と実測済み）・Adjudicated Quality（不一致の
   Evidenceベースレビュー）を混同しない——本モジュールはCompatibility計算まで
   （`field_agreement_json`/`critical_field_disagreement`）を担当し、
   Adjudicated Qualityは`adjudication_status`列を人間/Claudeレビューが
   後から更新する入れ物として用意するのみ（自動判定はしない）。
"""

from __future__ import annotations

import base64
import pickle
import uuid
from collections import defaultdict

import numpy as np

from geo_external_signal_service import _select_all_paginated
from geo_report_embedding_benchmark_service import (
    EMBEDDING_MODEL_NAME,
    EMBEDDING_MODEL_VERSION,
    compute_embeddings_for_representatives,
)
from geo_report_router_simulation_service import (
    solve_confidence_cutoff_for_target_rate,
    solve_novelty_gate_threshold,
)
from geo_report_semantic_census_benchmark_service import (
    apply_calibration,
    fit_calibration,
    load_cluster_centroids,
    load_numeric_features,
    load_reference_labels,
    load_split_assignments,
    compute_novelty_proxy,
)
from geo_report_semantic_census_service import (
    FieldClassifier,
    _build_feature_matrix,
    semantic_census_fields,
)

TASK = "sc_classification"
CLUSTER_TYPE = "sentiment"
FEATURE_VERSION = "v1-no-embeddings"
MODEL_VERSION = "v1"  # 学習アルゴリズム自体を変えたら上げる(id/created_atと併用、監査用)
_ROLE_BY_METHOD = {"logistic_regression": "primary", "hybrid": "challenger"}


# 「危険な値反転」パターン（2026-09-13ユーザー指摘）: joint(全field同時一致)基準の
# routing_safety_disagreement_rateだけでは、「positive→neutral」のような軽微な
# ズレと「positive→negative」「Public Voice→Fact Only」のような業務上重大な反転を
# 区別できない。ここでは実際にPrompt2の3-way分母計算（G.1章、source_class=
# "Public Voice"が必須条件）やレポート採否（apply_to_cluster/theme_relevance）に
# 直結する、意味が真逆になる組だけを明示的に列挙する（推測で網羅を広げない）。
_CRITICAL_FLIP_PATTERNS = {
    "theme_relevance": [frozenset({"relevant", "irrelevant"})],
    "apply_to_cluster": [frozenset({True, False})],
    "attitude": [frozenset({"positive", "negative"})],
    "source_class": [frozenset({"Public Voice", "Fact Only"}), frozenset({"Public Voice", "Media"})],
}


_CRITICAL_FLIP_PATTERNS_STR = {
    field: [frozenset(str(v) for v in pair) for pair in pairs]
    for field, pairs in _CRITICAL_FLIP_PATTERNS.items()
}


def _is_critical_flip(field: str, predicted, canonical) -> bool:
    """predicted/canonicalの型を必ずstr()で揃えてから比較する——分類器は
    `str(label)`で学習しているため(apply_to_clusterのようなbool fieldでも予測値は
    文字列"True"/"False")、canonical(DBのboolean型)とそのまま比較すると
    "True" != Trueで常に不一致扱いになってしまう(既存のfield_agreement計算
    (`apply_shadow_router_to_run`内)と同じstr()徹底ルールに合わせる)。"""
    patterns = _CRITICAL_FLIP_PATTERNS_STR.get(field)
    if not patterns:
        return False
    predicted_s, canonical_s = str(predicted), str(canonical)
    if predicted_s == canonical_s:
        return False
    pair = frozenset({predicted_s, canonical_s})
    return pair in patterns


def _load_reference_dataset_execution_id(client, run_id: str, task: str) -> str | None:
    """このRun/taskのReference Dataset(geo_report_reference_examples)が実際に
    どのProm1 execution由来かを1件取得する(build_reference_examples_from_execution
    により同一run_id×taskは常に単一execution_idのはず)。Cross-Run適用時に
    「どの版のReferenceで学習したモデルか」を追跡するfingerprintとして使う。"""
    rows = client.select("geo_report_reference_examples", {
        "select": "execution_id", "run_id": f"eq.{run_id}", "task": f"eq.{task}", "limit": "1",
    })
    return rows[0]["execution_id"] if rows else None


def _new_id() -> str:
    return str(uuid.uuid4())


def _chunked(items: list, size: int = 500):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _encode_blob(obj) -> str:
    return base64.b64encode(pickle.dumps(obj)).decode("ascii")


def _decode_blob(blob: str):
    return pickle.loads(base64.b64decode(blob.encode("ascii")))


# ---------------------------------------------------------------------------
# 学習・凍結（Train field classifiers on Train split, calibration+cutoff on Validation）
# ---------------------------------------------------------------------------

def train_shadow_router_model(client, benchmark_run_id: str, run_id: str, method: str, split_scheme: str, *,
                                 target_escalation_rate: float = 0.3, novelty_gate_percentile: float = 0.95,
                                 evidence_min_representative_count: int = 1,
                                 evidence_min_total_chars: int = 50) -> dict | None:
    """既存Phase 2 Benchmarkと同じTrain=fit/Validation=calibration+threshold解決の
    枠組みで学習し、`geo_report_shadow_router_models`へ永続化する（Cross-Run
    Generalizationのため凍結）。method: 'logistic_regression'|'hybrid'のみ
    （Section 20の合意、Gradient Boosting/Small LLMはPhase 3A対象外）。
    戻り値: 保存したmodel row（dict）。学習不能な場合はNone。"""
    if method not in _ROLE_BY_METHOD:
        raise ValueError(f"Phase 3Aで未対応のmethod: {method}（logistic_regression/hybridのみ）")

    fields = semantic_census_fields(TASK)
    reference = load_reference_labels(client, run_id, TASK)
    splits = load_split_assignments(client, benchmark_run_id, TASK, split_scheme)
    cluster_ids_by_split = defaultdict(list)
    for cid, info in splits.items():
        if info["excluded"] or cid not in reference:
            continue
        cluster_ids_by_split[info["split"]].append(cid)

    all_cluster_ids = [cid for s in ("train", "validation", "test") for cid in cluster_ids_by_split[s]]
    centroids = load_cluster_centroids(client, run_id, CLUSTER_TYPE, all_cluster_ids)
    numeric_features = load_numeric_features(client, run_id, CLUSTER_TYPE) if method == "hybrid" else None

    def _usable(cids):
        return [cid for cid in cids if cid in centroids]

    train_ids = _usable(cluster_ids_by_split["train"])
    validation_ids = _usable(cluster_ids_by_split["validation"])
    if not train_ids or not validation_ids:
        return None

    X_train = _build_feature_matrix(train_ids, centroids, numeric_features_by_cluster=numeric_features)
    labels_by_field = {f: [str(reference[cid].get(f)) for cid in train_ids] for f in fields}
    classifiers = {f: FieldClassifier(method).fit(X_train, labels_by_field[f]) for f in fields}

    X_validation = _build_feature_matrix(validation_ids, centroids, numeric_features_by_cluster=numeric_features)
    per_field_validation = {f: classifiers[f].predict_with_confidence(X_validation) for f in fields}

    calibrators = {}
    calibrated_confidence_by_cluster = defaultdict(dict)
    for f in fields:
        raw = [per_field_validation[f][i][1] for i in range(len(validation_ids))]
        correct = [1 if per_field_validation[f][i][0] == str(reference[validation_ids[i]].get(f)) else 0
                    for i in range(len(validation_ids))]
        calibrators[f] = fit_calibration(raw, correct)
        calibrated = apply_calibration(calibrators[f], raw)
        for cid, c in zip(validation_ids, calibrated):
            calibrated_confidence_by_cluster[cid][f] = c

    overall_calibrated_by_cluster = {
        cid: min(calibrated_confidence_by_cluster[cid].values()) for cid in validation_ids
    }

    train_centroids = [centroids[cid] for cid in train_ids]
    novelty_by_cluster = {cid: compute_novelty_proxy(centroids[cid], train_centroids) for cid in validation_ids}

    novelty_gate_threshold = solve_novelty_gate_threshold(
        [{"novelty_proxy_score": novelty_by_cluster[cid]} for cid in validation_ids],
        percentile=novelty_gate_percentile)
    mandatory_flags = [novelty_by_cluster[cid] > novelty_gate_threshold for cid in validation_ids]
    mandatory_rate = sum(mandatory_flags) / len(mandatory_flags) if mandatory_flags else 0.0
    if mandatory_rate >= target_escalation_rate:
        remaining_rate = 0.0
        remaining_rows = []
    else:
        remaining_rate = (target_escalation_rate - mandatory_rate) / (1 - mandatory_rate)
        remaining_rows = [{"overall_calibrated_confidence": overall_calibrated_by_cluster[cid]}
                            for cid, m in zip(validation_ids, mandatory_flags) if not m]
    confidence_cutoff = solve_confidence_cutoff_for_target_rate(remaining_rows, remaining_rate) \
        if remaining_rows else 0.0

    calibration_json = {f: ("isotonic" if calibrators[f] is not None else "identity") for f in fields}
    reference_dataset_execution_id = _load_reference_dataset_execution_id(client, run_id, TASK)

    model_row = {
        "id": _new_id(), "task": TASK, "cluster_type": CLUSTER_TYPE, "method": method,
        "role": _ROLE_BY_METHOD[method], "trained_on_run_id": run_id,
        "trained_on_benchmark_run_id": benchmark_run_id, "split_scheme": split_scheme,
        "target_escalation_rate": target_escalation_rate, "feature_version": FEATURE_VERSION,
        "embedding_model": EMBEDDING_MODEL_NAME, "embedding_version": EMBEDDING_MODEL_VERSION,
        "classifiers_blob": _encode_blob(classifiers), "calibrators_blob": _encode_blob(calibrators),
        "confidence_cutoff": confidence_cutoff, "novelty_gate_threshold": novelty_gate_threshold,
        "train_centroids_json": [c.tolist() for c in train_centroids],
        "evidence_min_representative_count": evidence_min_representative_count,
        "evidence_min_total_chars": evidence_min_total_chars, "status": "active",
        "model_version": MODEL_VERSION, "reference_dataset_execution_id": reference_dataset_execution_id,
        "calibration_json": calibration_json,
    }
    client.insert("geo_report_shadow_router_models", [model_row], prefer="return=minimal")
    return model_row


def load_shadow_router_model(client, model_id: str) -> dict:
    rows = client.select("geo_report_shadow_router_models", {"select": "*", "id": f"eq.{model_id}"})
    if not rows:
        raise ValueError(f"model_id={model_id} が見つかりません。")
    row = rows[0]
    return {
        **row,
        "classifiers": _decode_blob(row["classifiers_blob"]),
        "calibrators": _decode_blob(row["calibrators_blob"]),
        "train_centroids": [np.asarray(c, dtype=np.float32) for c in row["train_centroids_json"]],
    }


# ---------------------------------------------------------------------------
# 適用（Evidence Sufficiency Gate → Novelty Gate → Confidence Gate）
# ---------------------------------------------------------------------------

def _load_evidence_sufficiency(client, run_id: str, cluster_ids: list, *,
                                  min_representative_count: int, min_total_chars: int) -> dict:
    """cluster_id -> {"representative_count", "usable_text_count", "total_chars", "sufficient"}。"""
    rows = _select_all_paginated(client, "geo_report_sentiment_cluster_representatives", {
        "select": "sentiment_cluster_id,title,text_for_review", "run_id": f"eq.{run_id}",
        "sentiment_cluster_id": "in.(" + ",".join(cluster_ids) + ")",
    }) if cluster_ids else []
    by_cluster: dict = defaultdict(list)
    for r in rows:
        by_cluster[r["sentiment_cluster_id"]].append(r)

    result = {}
    for cid in cluster_ids:
        reps = by_cluster.get(cid, [])
        lengths = [len((r.get("title") or "") + (r.get("text_for_review") or "")) for r in reps]
        usable = [length for length in lengths if length > 0]
        total_chars = sum(lengths)
        sufficient = len(reps) >= min_representative_count and len(usable) > 0 and total_chars >= min_total_chars
        result[cid] = {
            "representative_count": len(reps), "usable_text_count": len(usable),
            "total_chars": total_chars, "sufficient": sufficient,
        }
    return result


def _load_canonical_strong_results(client, run_id: str, cluster_ids: list) -> dict:
    """cluster_id -> canonical Prompt1 SC分類結果(dict)。Runがまだcanonical_prompt1_
    execution_idを持たない、またはそのクラスタが未分類の場合はキー自体が無い。"""
    if not cluster_ids:
        return {}
    run_rows = client.select("geo_report_collection_runs", {
        "select": "canonical_prompt1_execution_id", "run_id": f"eq.{run_id}"})
    if not run_rows or not run_rows[0].get("canonical_prompt1_execution_id"):
        return {}
    execution_id = run_rows[0]["canonical_prompt1_execution_id"]
    rows = _select_all_paginated(client, "geo_report_sentiment_cluster_classifications", {
        "select": "*", "run_id": f"eq.{run_id}", "execution_id": f"eq.{execution_id}",
        "sentiment_cluster_id": "in.(" + ",".join(cluster_ids) + ")",
    })
    return {r["sentiment_cluster_id"]: r for r in rows}


def apply_shadow_router_to_run(client, model_id: str, run_id: str, *, cluster_ids: list | None = None) -> list:
    """model_idの凍結済みモデルをrun_idへ適用する。cluster_idsを渡さない場合は
    そのRunの全SCクラスタが対象。戻り値: `geo_report_shadow_router_predictions`
    行(dict)のリスト（未保存、呼び出し側がsave_shadow_router_predictionsで保存する）。"""
    model = load_shadow_router_model(client, model_id)
    fields = semantic_census_fields(model["task"])

    if cluster_ids is None:
        cluster_rows = _select_all_paginated(client, "geo_report_sentiment_clusters", {
            "select": "sentiment_cluster_id", "run_id": f"eq.{run_id}"})
        cluster_ids = [r["sentiment_cluster_id"] for r in cluster_rows]
    if not cluster_ids:
        return []

    compute_embeddings_for_representatives(client, run_id, model["cluster_type"], cluster_ids)
    centroids = load_cluster_centroids(client, run_id, model["cluster_type"], cluster_ids)
    numeric_features = load_numeric_features(client, run_id, model["cluster_type"]) \
        if model["method"] == "hybrid" else None
    usable_ids = [cid for cid in cluster_ids if cid in centroids]

    evidence = _load_evidence_sufficiency(
        client, run_id, cluster_ids,
        min_representative_count=model["evidence_min_representative_count"],
        min_total_chars=model["evidence_min_total_chars"])
    canonical_by_cluster = _load_canonical_strong_results(client, run_id, cluster_ids)

    classifiers = model["classifiers"]
    calibrators = model["calibrators"]
    rows = []
    if usable_ids:
        X = _build_feature_matrix(usable_ids, centroids, numeric_features_by_cluster=numeric_features)
        per_field = {f: classifiers[f].predict_with_confidence(X) for f in fields}
        for i, cid in enumerate(usable_ids):
            preds = {f: per_field[f][i][0] for f in fields}
            raw_confs = {f: per_field[f][i][1] for f in fields}
            calibrated = {f: apply_calibration(calibrators[f], [raw_confs[f]])[0] for f in fields}
            overall_calibrated = min(calibrated.values()) if calibrated else None
            novelty = compute_novelty_proxy(centroids[cid], model["train_centroids"])
            ev = evidence.get(cid, {"representative_count": 0, "usable_text_count": 0,
                                       "total_chars": 0, "sufficient": False})

            reasons = []
            if not ev["sufficient"]:
                reasons.append("insufficient_evidence")
            if novelty > model["novelty_gate_threshold"]:
                reasons.append("novelty")
            if not reasons and (overall_calibrated or 0.0) < model["confidence_cutoff"]:
                reasons.append("low_confidence")
            router_decision = "would_escalate" if reasons else "lightweight_accept"

            canonical = canonical_by_cluster.get(cid)
            field_agreement = None
            critical_disagreement = None
            if canonical is not None:
                field_agreement = {f: (str(preds.get(f)) == str(canonical.get(f))) for f in fields}
                critical_disagreement = not all(field_agreement.values())

            rows.append({
                "id": _new_id(), "model_id": model_id, "run_id": run_id, "cluster_type": model["cluster_type"],
                "cluster_id": cid, "field_predictions_json": preds,
                "field_calibrated_confidence_json": calibrated,
                "overall_calibrated_confidence": overall_calibrated,
                "novelty_proxy_score": novelty, "nearest_neighbor_distance": novelty,
                "evidence_sufficiency_json": ev, "router_decision": router_decision,
                "router_reason": reasons, "canonical_strong_result_json": canonical,
                "field_agreement_json": field_agreement, "critical_field_disagreement": critical_disagreement,
                "adjudication_status": "pending", "adjudication_notes": None,
            })
    return rows


def save_shadow_router_predictions(client, rows: list) -> None:
    if not rows:
        return
    model_id = rows[0]["model_id"]
    run_id = rows[0]["run_id"]
    client.delete("geo_report_shadow_router_predictions", {
        "model_id": f"eq.{model_id}", "run_id": f"eq.{run_id}",
    })
    for chunk in _chunked(rows):
        client.insert("geo_report_shadow_router_predictions", chunk, prefer="return=minimal")


# ---------------------------------------------------------------------------
# Run単位サマリ（Section 23.2）
# ---------------------------------------------------------------------------

def compute_shadow_router_run_summary(client, model_id: str, run_id: str, *,
                                         batch_size: int | None = None, max_tokens: int | None = None) -> dict:
    """Section 23.2のRun単位集計。Shadow Mode中は実削減が発生しないため、ここで
    出すbatch/call/token数は常にProjected Savings（推定）であり、Realized Savings
    ではないことを呼び出し側は明記すること。"""
    from geo_report_prompt1_orchestration_service import (
        MAX_BATCH_INPUT_TOKENS, MAX_CLUSTERS_PER_BATCH, _estimate_representative_weight_by_cluster,
        _pack_token_aware_batches,
    )

    rows = _select_all_paginated(client, "geo_report_shadow_router_predictions", {
        "select": "*", "model_id": f"eq.{model_id}", "run_id": f"eq.{run_id}",
    })
    n_total = len(rows)
    escalated = [r for r in rows if r["router_decision"] == "would_escalate"]
    accepted = [r for r in rows if r["router_decision"] == "lightweight_accept"]

    weight_by_id = _estimate_representative_weight_by_cluster(
        client, run_id, "geo_report_sentiment_cluster_representatives", "sentiment_cluster_id")
    batches = _pack_token_aware_batches(
        [r["cluster_id"] for r in escalated], weight_by_id,
        max_items=batch_size or MAX_CLUSTERS_PER_BATCH, max_tokens=max_tokens or MAX_BATCH_INPUT_TOKENS)

    with_canonical = [r for r in rows if r.get("canonical_strong_result_json") is not None]
    critical_field_agreement = None
    if with_canonical:
        critical_field_agreement = sum(
            1 for r in with_canonical if not r["critical_field_disagreement"]) / len(with_canonical)

    # Routing Safety（ユーザー指摘、2026-09-13）: Compatibility(全体平均)とは別に、
    # 「lightweight_acceptと判断したクラスタの中で、実際にはCanonical Strong LLMと
    # 不一致だった割合」を独立して見る——これが高いと「Strongへ送るべき難しいケースを
    # 誤ってLightweight側へ通してしまっている」ことを意味する。
    accepted_with_canonical = [r for r in accepted if r.get("canonical_strong_result_json") is not None]
    routing_safety_disagreement_rate = None
    if accepted_with_canonical:
        routing_safety_disagreement_rate = sum(
            1 for r in accepted_with_canonical if r["critical_field_disagreement"]) / len(accepted_with_canonical)

    # field毎の一致率（escalated/accepted別、Critical fieldのPrecision/Recallの代わりに
    # まずAgreement rateとして提示する——Referenceが独立ground truthではないため
    # "precision/recall"ではなく"agreement"と呼ぶ、Gate 3以来の用語規律を踏襲）。
    field_agreement_by_field: dict = {}
    if with_canonical:
        all_fields = set()
        for r in with_canonical:
            all_fields.update((r.get("field_agreement_json") or {}).keys())
        for f in all_fields:
            values = [r["field_agreement_json"][f] for r in with_canonical if f in (r.get("field_agreement_json") or {})]
            accepted_values = [r["field_agreement_json"][f] for r in accepted_with_canonical
                                 if f in (r.get("field_agreement_json") or {})]
            field_agreement_by_field[f] = {
                "overall": (sum(values) / len(values)) if values else None,
                "lightweight_accept_only": (sum(accepted_values) / len(accepted_values)) if accepted_values else None,
            }

    reason_counts: dict = defaultdict(int)
    for r in escalated:
        for reason in (r.get("router_reason") or []):
            reason_counts[reason] += 1

    # 「危険な値反転」件数（2026-09-13ユーザー指摘）: joint disagreement(6 field中
    # 1つでも不一致)だけでは、positive→neutralのような軽微なズレとpositive→negative
    # のような業務上重大な反転を区別できない。lightweight_acceptのみを対象に、
    # _CRITICAL_FLIP_PATTERNSに該当するfield×行を数える。
    def _critical_flips(r: dict) -> list:
        preds = r.get("field_predictions_json") or {}
        canon = r.get("canonical_strong_result_json") or {}
        return [f for f in _CRITICAL_FLIP_PATTERNS if _is_critical_flip(f, preds.get(f), canon.get(f))]

    accepted_flip_rows = [(r, _critical_flips(r)) for r in accepted_with_canonical]
    accepted_with_critical_flip = [r for r, flips in accepted_flip_rows if flips]
    critical_flip_by_field: dict = defaultdict(int)
    for _r, flips in accepted_flip_rows:
        for f in flips:
            critical_flip_by_field[f] += 1

    # Novelty/Evidence Sufficiency Gateが「本当に危ういケース」を拾えているか:
    # would_escalateのうちそのgateがreasonにあり、かつ実際にcanonicalと何らかの
    # field不一致があったクラスタの件数（Gateの実効性の直接的な裏付けになる）。
    def _gate_captured_disagreement_count(reason: str) -> int:
        return sum(1 for r in escalated
                    if reason in (r.get("router_reason") or [])
                    and r.get("canonical_strong_result_json") is not None
                    and r["critical_field_disagreement"])

    return {
        "model_id": model_id, "run_id": run_id, "n_total_clusters": n_total,
        "would_escalate_count": len(escalated), "lightweight_accept_count": len(accepted),
        "lightweight_accept_rate": (len(accepted) / n_total) if n_total else None,
        "routing_safety_disagreement_rate": routing_safety_disagreement_rate,
        "field_agreement_by_field": field_agreement_by_field,
        "critical_flip_rate_within_lightweight_accept": (
            len(accepted_with_critical_flip) / len(accepted_with_canonical)) if accepted_with_canonical else None,
        "critical_flip_count_within_lightweight_accept": len(accepted_with_critical_flip),
        "critical_flip_by_field_within_lightweight_accept": dict(critical_flip_by_field),
        "novelty_gate_captured_disagreement_count": _gate_captured_disagreement_count("novelty"),
        "evidence_sufficiency_gate_captured_disagreement_count":
            _gate_captured_disagreement_count("insufficient_evidence"),
        "would_be_escalation_rate": (len(escalated) / n_total) if n_total else None,
        "projected_strong_llm_batches": len(batches),
        "projected_strong_llm_estimated_input_tokens": sum(weight_by_id.get(r["cluster_id"], 0) for r in escalated),
        "escalation_reason_counts": dict(reason_counts),
        "n_with_canonical_comparison": len(with_canonical),
        "compatibility_critical_field_agreement": critical_field_agreement,
        "note": "batch/token数はShadow Mode中のProjected Savingsであり、Realized Savingsではない。",
    }
