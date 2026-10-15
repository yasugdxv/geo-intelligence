"""Report Studio → Intelligence Analysis Platform: Semantic Census Benchmark実施（Phase 2）。

詳細設計: docs/report-studio/INTELLIGENCE_PLATFORM_PHASE0_2_PLAN.md（Section J）。

Reference Dataset（Section C）・Feature Dataset（Section E）・Benchmark Split
Assignment（Section I）・Embedding Cache（Section G）・Semantic Census分類器
（Section H）を束ね、Train=model fitting／Validation=method選定・calibration・
threshold決定／Test=最終評価のみ、という厳密な役割分離のもとで評価する。

用語の訂正（重要）: Referenceデータ自体がCurrent Prompt1（Strong LLM）の出力である
ため、ここで計測できるのは独立したground truthとの一致＝「Accuracy」ではなく
「Expected Reference Agreement」（Current Prompt1との一致度）である。本モジュールの
出力キーはすべて`reference_agreement`と呼び、`accuracy`という語は使わない。
"""

from __future__ import annotations

import time
import uuid
from collections import Counter, defaultdict

import numpy as np
import openai

import geo_llm_common
from geo_external_signal_service import _select_all_paginated
from geo_report_prompt1_orchestration_service import (
    _load_analysis_clusters_and_representatives_batch,
    _load_sentiment_clusters_and_representatives_batch,
)
from geo_report_semantic_census_service import (
    HYBRID_NUMERIC_FEATURES,
    FieldClassifier,
    _build_feature_matrix,
    _semantic_census_validation_schema,
    classify_semantic_census_batch,
    extract_field_predictions,
    semantic_census_fields,
)

FEATURE_VERSION = "v1-no-embeddings"


def _new_id() -> str:
    return str(uuid.uuid4())


def _chunked(items: list, size: int = 500):
    for i in range(0, len(items), size):
        yield items[i:i + size]


# ---------------------------------------------------------------------------
# データ読み込み
# ---------------------------------------------------------------------------

def load_reference_labels(client, run_id: str, task: str) -> dict:
    """{cluster_id: label_json}（AC/SC taskはitem_key=''固定、cluster_id単位で1つ）。"""
    rows = _select_all_paginated(client, "geo_report_reference_examples", {
        "select": "cluster_id,label_json", "run_id": f"eq.{run_id}", "task": f"eq.{task}",
    })
    return {r["cluster_id"]: r["label_json"] for r in rows}


def load_split_assignments(client, benchmark_run_id: str, task: str, split_scheme: str) -> dict:
    """{cluster_id: {"split":..., "excluded_due_to_temporal_leakage": bool}}。"""
    rows = _select_all_paginated(client, "geo_report_benchmark_split_assignments", {
        "select": "cluster_id,split,excluded_due_to_temporal_leakage",
        "benchmark_run_id": f"eq.{benchmark_run_id}", "task": f"eq.{task}", "split_scheme": f"eq.{split_scheme}",
    })
    return {r["cluster_id"]: {"split": r["split"],
                                "excluded": r.get("excluded_due_to_temporal_leakage", False)} for r in rows}


def load_numeric_features(client, run_id: str, cluster_type: str) -> dict:
    rows = _select_all_paginated(client, "geo_report_cluster_features", {
        "select": "cluster_id," + ",".join(HYBRID_NUMERIC_FEATURES),
        "run_id": f"eq.{run_id}", "cluster_type": f"eq.{cluster_type}", "feature_version": f"eq.{FEATURE_VERSION}",
    })
    return {r["cluster_id"]: {k: r.get(k) for k in HYBRID_NUMERIC_FEATURES} for r in rows}


def load_cluster_centroids(client, run_id: str, cluster_type: str, cluster_ids: list) -> dict:
    """cluster_id -> np.ndarray（representative embeddingの平均、Section G）。
    埋め込みが1件も無いクラスタはキー自体が無い。**クラスタ毎に個別クエリを投げない**
    （クラスタ数が多いと個別クエリの往復回数がボトルネックになるため、実データで
    発覚・修正した——representative id一覧とembeddingをそれぞれ1回の一括IN取得で
    済ませ、その場でcluster_id別にグルーピングしてcentroidを計算する）。"""
    if not cluster_ids:
        return {}
    from geo_report_embedding_benchmark_service import (
        _CLUSTER_ID_COLUMN, _REPRESENTATIVES_TABLE, _select_by_values_chunked, get_embeddings,
    )
    table = _REPRESENTATIVES_TABLE[cluster_type]
    cluster_col = _CLUSTER_ID_COLUMN[cluster_type]
    reps = _select_by_values_chunked(client, table, {"select": f"id,{cluster_col}", "run_id": f"eq.{run_id}"},
                                        cluster_col, cluster_ids)
    rep_ids_by_cluster: dict = defaultdict(list)
    for r in reps:
        rep_ids_by_cluster[r[cluster_col]].append(r["id"])
    all_rep_ids = [rid for ids in rep_ids_by_cluster.values() for rid in ids]
    embeddings = get_embeddings(client, run_id, "representative", all_rep_ids)

    centroids = {}
    for cid, rep_ids in rep_ids_by_cluster.items():
        vectors = [embeddings[rid] for rid in rep_ids if rid in embeddings]
        if vectors:
            centroids[cid] = np.mean(vectors, axis=0)
    return centroids


# ---------------------------------------------------------------------------
# Calibration（Validationでfit、Testへ適用）
# ---------------------------------------------------------------------------

def fit_calibration(raw_confidences: list, correct_flags: list):
    """IsotonicRegressionで(raw_confidence -> 実際の一致率)を学習する。Small LLMの
    自己申告confidenceにもLogistic/Gradient BoostingのPredict_probaにも同じ仕組みを
    使う（binベースのmappingと数学的に同じ、単調写像）。十分なsignalが無ければ
    Noneを返し、恒等写像として扱う。"""
    if len(raw_confidences) < 5 or len(set(correct_flags)) < 2:
        return None
    from sklearn.isotonic import IsotonicRegression
    ir = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    ir.fit(raw_confidences, correct_flags)
    return ir


def apply_calibration(calibrator, raw_confidences: list) -> list:
    if calibrator is None:
        return [float(c) if c is not None else 0.0 for c in raw_confidences]
    return [float(v) for v in calibrator.predict([c if c is not None else 0.0 for c in raw_confidences])]


# ---------------------------------------------------------------------------
# 指標計算
# ---------------------------------------------------------------------------

def compute_field_metrics(y_true: list, y_pred: list) -> dict:
    """戻り値: {"reference_agreement": float, "macro_f1": float, "per_class": {...}}。
    "accuracy"ではなく"reference_agreement"と呼ぶ（Referenceが独立ground truthではないため）。"""
    from sklearn.metrics import precision_recall_fscore_support
    labels = sorted({str(v) for v in y_true} | {str(v) for v in y_pred})
    y_true_s = [str(v) for v in y_true]
    y_pred_s = [str(v) for v in y_pred]
    if not labels:
        return {"reference_agreement": None, "macro_f1": None, "per_class": {}}
    precision, recall, f1, support = precision_recall_fscore_support(
        y_true_s, y_pred_s, labels=labels, zero_division=0)
    per_class = {lbl: {"precision": float(p), "recall": float(r), "f1": float(f), "support": int(s)}
                  for lbl, p, r, f, s in zip(labels, precision, recall, f1, support)}
    reference_agreement = float(np.mean([a == b for a, b in zip(y_true_s, y_pred_s)]))
    return {"reference_agreement": reference_agreement, "macro_f1": float(np.mean(f1)), "per_class": per_class}


def compute_class_support(labels_by_split: dict) -> dict:
    """labels_by_split: {"train":[...], "validation":[...], "test":[...]}。
    戻り値: {split: {class_value: count}}。少数class可視化用。"""
    return {split: dict(Counter(str(v) for v in values)) for split, values in labels_by_split.items()}


def unseen_reference_label_fields(train_values_by_field: dict, item_label: dict, fields: list) -> list:
    """item_label（あるcluster/split中のReference label）のうち、Trainに1度も
    出現しなかった値を持つfield名を返す。Benchmark診断専用（Section H参照）。"""
    unseen = []
    for f in fields:
        train_values = train_values_by_field.get(f, set())
        if str(item_label.get(f)) not in train_values:
            unseen.append(f)
    return unseen


def compute_novelty_proxy(centroid: np.ndarray, train_centroids: list) -> float:
    """そのsplit_schemeのTrain centroid群への最近傍距離(1-cosine類似度)。method非依存の
    量——method間で共通利用する(Section E/K)。`novelty_proxy_score`と
    `nearest_neighbor_distance`は現時点では同一の値として扱う（将来、真のOOD検出を
    導入する際に別々の計算へ分離する余地を残すため、2つの列名のまま維持する）。"""
    if not train_centroids:
        return 1.0
    sims = []
    for tc in train_centroids:
        denom = (np.linalg.norm(centroid) * np.linalg.norm(tc))
        sims.append(float(np.dot(centroid, tc) / denom) if denom else 0.0)
    return 1.0 - max(sims)


def cluster_exact_match_rate(field_predictions_by_cluster: dict, reference_by_cluster: dict, fields: list) -> float:
    if not field_predictions_by_cluster:
        return 0.0
    matches = 0
    for cid, preds in field_predictions_by_cluster.items():
        ref = reference_by_cluster.get(cid, {})
        if all(str(preds.get(f)) == str(ref.get(f)) for f in fields):
            matches += 1
    return matches / len(field_predictions_by_cluster)


# ---------------------------------------------------------------------------
# Benchmark実行（embedding系method）
# ---------------------------------------------------------------------------

def run_embedding_method_benchmark(client, benchmark_run_id: str, run_id: str, task: str, cluster_type: str,
                                      split_scheme: str, method: str) -> dict:
    """B/C/D/E（embedding_nn/logistic_regression/gradient_boosting/hybrid）を1本実行する。
    Train=fit、Validationでcalibration、Testで最終評価。戻り値: benchmark行(dict)と
    predictions行(list)のタプルを持つdict {"benchmark": {...}, "predictions": [...]}。"""
    fields = semantic_census_fields(task)
    reference = load_reference_labels(client, run_id, task)
    splits = load_split_assignments(client, benchmark_run_id, task, split_scheme)
    cluster_ids_by_split = defaultdict(list)
    for cid, info in splits.items():
        if info["excluded"] or cid not in reference:
            continue
        cluster_ids_by_split[info["split"]].append(cid)

    all_cluster_ids = [cid for s in ("train", "validation", "test") for cid in cluster_ids_by_split[s]]
    centroids = load_cluster_centroids(client, run_id, cluster_type, all_cluster_ids)
    numeric_features = load_numeric_features(client, run_id, cluster_type) if method == "hybrid" else None

    def _usable(cids):
        return [cid for cid in cids if cid in centroids]

    train_ids = _usable(cluster_ids_by_split["train"])
    validation_ids = _usable(cluster_ids_by_split["validation"])
    test_ids = _usable(cluster_ids_by_split["test"])

    if not train_ids:
        return {"benchmark": None, "predictions": []}

    X_train = _build_feature_matrix(train_ids, centroids, numeric_features_by_cluster=numeric_features)
    labels_by_field = {f: [str(reference[cid].get(f)) for cid in train_ids] for f in fields}
    classifiers = {f: FieldClassifier(method).fit(X_train, labels_by_field[f]) for f in fields}

    train_values_by_field = {f: set(labels_by_field[f]) for f in fields}

    def _predict_split(cids):
        if not cids:
            return {}
        X = _build_feature_matrix(cids, centroids, numeric_features_by_cluster=numeric_features)
        per_field = {f: classifiers[f].predict_with_confidence(X) for f in fields}
        results = {}
        for i, cid in enumerate(cids):
            preds = {f: per_field[f][i][0] for f in fields}
            confs = {f: per_field[f][i][1] for f in fields}
            results[cid] = (preds, confs)
        return results

    validation_results = _predict_split(validation_ids)
    test_results = _predict_split(test_ids)

    train_centroids = [centroids[cid] for cid in train_ids]
    novelty_by_cluster = {cid: compute_novelty_proxy(centroids[cid], train_centroids)
                           for cid in validation_ids + test_ids}

    # Validation上でfield毎にcalibrationをfitする
    calibrators = {}
    for f in fields:
        raw = [validation_results[cid][1][f] for cid in validation_ids]
        correct = [1 if str(validation_results[cid][0][f]) == str(reference[cid].get(f)) else 0
                    for cid in validation_ids]
        calibrators[f] = fit_calibration(raw, correct)

    predictions_rows = []
    field_predictions_by_cluster = {}
    for split_name, results, cids in (("validation", validation_results, validation_ids),
                                        ("test", test_results, test_ids)):
        for cid in cids:
            preds, raw_confs = results[cid]
            calibrated = {f: apply_calibration(calibrators[f], [raw_confs[f]])[0] for f in fields}
            overall_calibrated = min(calibrated.values()) if calibrated else None
            unseen = unseen_reference_label_fields(train_values_by_field, reference[cid], fields)
            predictions_rows.append({
                "id": _new_id(), "benchmark_run_id": benchmark_run_id, "run_id": run_id, "task": task,
                "cluster_type": cluster_type, "cluster_id": cid, "item_key": "", "method": method,
                "method_variant": "default", "split_scheme": split_scheme, "split": split_name,
                "field_predictions_json": preds, "field_raw_confidence_json": raw_confs,
                "field_calibrated_confidence_json": calibrated,
                "overall_raw_confidence": min(raw_confs.values()) if raw_confs else None,
                "overall_calibrated_confidence": overall_calibrated,
                "unseen_reference_label_fields": unseen,
                "novelty_proxy_score": novelty_by_cluster[cid],
                "nearest_neighbor_distance": novelty_by_cluster[cid],
            })
            if split_name == "test":
                field_predictions_by_cluster[cid] = preds

    field_metrics = {}
    class_support = {}
    for f in fields:
        y_true = [str(reference[cid].get(f)) for cid in test_ids]
        y_pred = [str(test_results[cid][0][f]) for cid in test_ids]
        field_metrics[f] = compute_field_metrics(y_true, y_pred)
        class_support[f] = compute_class_support({
            "train": [reference[cid].get(f) for cid in train_ids],
            "validation": [reference[cid].get(f) for cid in validation_ids],
            "test": [reference[cid].get(f) for cid in test_ids],
        })
        field_metrics[f]["class_support"] = class_support[f]
        train_class_count = len(train_values_by_field[f])
        field_metrics[f]["production_eligible"] = train_class_count >= 2

    exact_match = cluster_exact_match_rate(field_predictions_by_cluster,
                                              {cid: reference[cid] for cid in test_ids}, fields)

    benchmark_row = {
        "id": _new_id(), "benchmark_run_id": benchmark_run_id, "run_id": run_id, "task": task,
        "method": method, "method_variant": "default", "split_scheme": split_scheme, "split": "test",
        "field_metrics_json": field_metrics, "cluster_exact_match_rate": exact_match,
        "calibration_json": {f: "isotonic" if calibrators[f] is not None else "identity" for f in fields},
        "relative_cost": 0.0, "latency_ms_avg": None,
    }
    return {"benchmark": benchmark_row, "predictions": predictions_rows}


def save_benchmark_results(client, result: dict) -> None:
    if result["benchmark"] is not None:
        b = result["benchmark"]
        client.delete("geo_report_semantic_census_benchmarks", {
            "benchmark_run_id": f"eq.{b['benchmark_run_id']}", "split_scheme": f"eq.{b['split_scheme']}",
            "task": f"eq.{b['task']}", "method": f"eq.{b['method']}", "method_variant": f"eq.{b['method_variant']}",
            "split": f"eq.{b['split']}",
        })
        client.insert("geo_report_semantic_census_benchmarks", [b], prefer="return=minimal")
    predictions = result["predictions"]
    if predictions:
        b0 = predictions[0]
        client.delete("geo_report_semantic_census_predictions", {
            "benchmark_run_id": f"eq.{b0['benchmark_run_id']}", "split_scheme": f"eq.{b0['split_scheme']}",
            "task": f"eq.{b0['task']}", "method": f"eq.{b0['method']}",
            "method_variant": f"eq.{b0['method_variant']}",
        })
        for chunk in _chunked(predictions):
            client.insert("geo_report_semantic_census_predictions", chunk, prefer="return=minimal")


# ---------------------------------------------------------------------------
# Benchmark実行（A. Small LLM）
# ---------------------------------------------------------------------------

_CLUSTER_LOADER = {
    "analysis": _load_analysis_clusters_and_representatives_batch,
    "sentiment": _load_sentiment_clusters_and_representatives_batch,
}


def _categorize_llm_exception(exc: Exception) -> str:
    """Gate 3診断（2026-09-13）: 障害を「一次原因」の種類で分類する。ネットワーク断/
    timeout（transport）とAzure/API側の応答（api）を明確に分けることで、ある回の
    失敗率上昇を安易に「Small LLM/Semantic Census自体の問題」に帰属させないため
    （2026-09-13の3時間超stallは実際は外部ネットワーク断が一次原因であり、
    ai_client.make_openai_clientのhttpx.Clientにtimeout未設定だったことは
    「断が起きた際に無期限待機してしまう実装上の弱点（増幅要因）」に過ぎず、
    Small LLM自体の障害としてカウントしてはならない、という整理に基づく）。"""
    if isinstance(exc, openai.APITimeoutError):
        return "transport_timeout"
    if isinstance(exc, openai.APIConnectionError):
        return "transport_connection"
    if isinstance(exc, openai.RateLimitError):
        return "api_rate_limit_exhausted"  # call_llm_structuredの内部backoff(最大5回)を使い切った後
    if isinstance(exc, openai.InternalServerError):
        return "api_server_error"
    if isinstance(exc, openai.APIStatusError):
        return "api_other"
    if isinstance(exc, geo_llm_common.GeoLLMError):
        # call_llm_structured内部の3段階(構造化出力→手動JSON抽出→訂正再試行)すべてで
        # schema検証に失敗した場合に送出される（2026-09-13実測で判明: 当初"unknown"に
        # 分類されており、これがSC benchmarkの失敗の主因だった。ネットワーク/API障害では
        # なく、Semantic Census pipeline側のschema/parse失敗＝Category C）。
        return "pipeline_schema_validation_failure"
    return "unknown"


def _new_diagnostics() -> dict:
    return {
        "total_clusters_attempted": 0, "total_calls": 0, "successful_calls": 0,
        "failed_calls_by_category": defaultdict(int),
        "latencies_ms": [], "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0,
        "missing_item_events": 0, "invalid_item_events": 0,
        "duplicate_item_events": 0, "unknown_item_events": 0,
        "final_unresolved_clusters": 0,
    }


def _classify_clusters_with_small_llm(azure_client, model: str, task: str, loaded: dict, cluster_ids: list,
                                         *, batch_size: int = 20, max_extra_rounds: int = 2,
                                         diagnostics: dict | None = None) -> dict:
    """cluster_id -> (field_predictions, field_raw_confidence)。分類に失敗した
    クラスタはキー自体が無い（Benchmark実行が1件の失敗で止まらないようにする）。
    diagnosticsに`_new_diagnostics()`形式のdictを渡すと、Gate 3の障害分類
    （transport/api/pipeline）・latency・token使用量をin-placeで積算する。

    Gate 3診断（2026-09-13）: 当初はバッチ応答の`results`にたまたま含まれた
    cluster_idだけを拾っており、モデルが結果を落とした/truncateしたクラスタは
    無条件で失われていた（20クラスタ中20件を個別に単発再実行したところ全件成功
    しており、per-cluster content/schema/model capability起因ではないと確認済み）。
    Prompt1本番の`_classify_batch_with_retry`と同じ手法
    （geo_llm_common.reconcile_batch_llm_results）で、missing/invalidになった
    クラスタだけをアイテム単位で最大max_extra_rounds回まで再試行する
    （バッチ全体は再送しない）。"""
    validation_schema = _semantic_census_validation_schema(task)
    results = {}
    for i in range(0, len(cluster_ids), batch_size):
        remaining = [cid for cid in cluster_ids[i:i + batch_size] if cid in loaded]
        if not remaining:
            continue
        if diagnostics is not None:
            diagnostics["total_clusters_attempted"] += len(remaining)
        for _round in range(max_extra_rounds + 1):
            if not remaining:
                break
            clusters = [{"cluster_row": loaded[cid][0], "representatives": loaded[cid][1]} for cid in remaining]
            if diagnostics is not None:
                diagnostics["total_calls"] += 1
            call_started = time.monotonic()
            try:
                response = classify_semantic_census_batch(azure_client, model, task, clusters)
                raw_results = response.get("data", {}).get("results", [])
            except Exception as e:  # noqa: BLE001 — このラウンドは失敗として分類し次ラウンドへ
                if diagnostics is not None:
                    diagnostics["failed_calls_by_category"][_categorize_llm_exception(e)] += 1
                continue  # remainingを変えず次ラウンドへ(このラウンドは呼び出し自体が失敗)。
            if diagnostics is not None:
                diagnostics["successful_calls"] += 1
                diagnostics["latencies_ms"].append(
                    response.get("latency_ms") or int((time.monotonic() - call_started) * 1000))
                usage = response.get("usage") or {}
                diagnostics["input_tokens"] += usage.get("prompt_tokens") or 0
                diagnostics["output_tokens"] += usage.get("completion_tokens") or 0
                diagnostics["cached_tokens"] += usage.get("cached_tokens") or 0
            reconciled = geo_llm_common.reconcile_batch_llm_results(remaining, raw_results, validation_schema)
            for cid, item in reconciled["valid"].items():
                results[cid] = extract_field_predictions(task, item)
            if diagnostics is not None:
                diagnostics["missing_item_events"] += len(reconciled["missing"])
                diagnostics["invalid_item_events"] += len(reconciled["invalid"])
                diagnostics["duplicate_item_events"] += len(reconciled["duplicate"])
                diagnostics["unknown_item_events"] += len(reconciled["unknown"])
            remaining = list(dict.fromkeys(reconciled["missing"] + reconciled["invalid"]))
        if diagnostics is not None and remaining:
            diagnostics["final_unresolved_clusters"] += len(remaining)
    return results


def _percentile(values: list, p: float):
    if not values:
        return None
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))
    return s[idx]


def _summarize_diagnostics(diagnostics: dict) -> dict:
    """Gate 3報告用に、latency percentile等の集計値を追加したコピーを返す
    （生のlatencies_msリストはJSON化のためlen()のみ残し、percentileへ要約する）。"""
    latencies = diagnostics["latencies_ms"]
    return {
        "total_clusters_attempted": diagnostics["total_clusters_attempted"],
        "total_calls": diagnostics["total_calls"], "successful_calls": diagnostics["successful_calls"],
        "failed_calls_by_category": dict(diagnostics["failed_calls_by_category"]),
        "latency_ms_p50": _percentile(latencies, 0.50), "latency_ms_p95": _percentile(latencies, 0.95),
        "latency_ms_max": max(latencies) if latencies else None,
        "input_tokens": diagnostics["input_tokens"], "output_tokens": diagnostics["output_tokens"],
        "cached_tokens": diagnostics["cached_tokens"],
        "missing_item_events": diagnostics["missing_item_events"],
        "invalid_item_events": diagnostics["invalid_item_events"],
        "duplicate_item_events": diagnostics["duplicate_item_events"],
        "unknown_item_events": diagnostics["unknown_item_events"],
        "final_unresolved_clusters": diagnostics["final_unresolved_clusters"],
    }


def run_small_llm_method_benchmark(client, azure_client, model: str, benchmark_run_id: str, run_id: str,
                                      task: str, cluster_type: str, split_scheme: str, *,
                                      batch_size: int = 20, collect_diagnostics: bool = False) -> dict:
    """Method A(Small LLM)。Trainはfittingしない（zero/few-shot判定のため）が、
    train_values_by_fieldはunseen_reference_label診断のために計算する。
    Validation側の結果でfield毎にcalibrationをfitし、Testへ適用する。

    collect_diagnostics=Trueの場合、戻り値へ`diagnostics`キー（Gate 3報告用の
    transport/api障害内訳・latency percentile・token使用量、_summarize_diagnostics
    形式）を追加する。既定Falseは既存呼び出し元・テストと完全互換（キー自体が無い）。"""
    fields = semantic_census_fields(task)
    reference = load_reference_labels(client, run_id, task)
    splits = load_split_assignments(client, benchmark_run_id, task, split_scheme)
    cluster_ids_by_split = defaultdict(list)
    for cid, info in splits.items():
        if info["excluded"] or cid not in reference:
            continue
        cluster_ids_by_split[info["split"]].append(cid)

    train_ids = cluster_ids_by_split["train"]
    validation_ids = cluster_ids_by_split["validation"]
    test_ids = cluster_ids_by_split["test"]
    if not train_ids or not (validation_ids or test_ids):
        return {"benchmark": None, "predictions": []}

    train_values_by_field = {f: {str(reference[cid].get(f)) for cid in train_ids} for f in fields}

    loader = _CLUSTER_LOADER[cluster_type]
    loaded_validation = loader(client, run_id, validation_ids) if validation_ids else {}
    loaded_test = loader(client, run_id, test_ids) if test_ids else {}

    validation_diag = _new_diagnostics() if collect_diagnostics else None
    test_diag = _new_diagnostics() if collect_diagnostics else None
    validation_results = _classify_clusters_with_small_llm(
        azure_client, model, task, loaded_validation, validation_ids, batch_size=batch_size,
        diagnostics=validation_diag)
    test_results = _classify_clusters_with_small_llm(
        azure_client, model, task, loaded_test, test_ids, batch_size=batch_size, diagnostics=test_diag)

    validation_ids = [cid for cid in validation_ids if cid in validation_results]
    test_ids = [cid for cid in test_ids if cid in test_results]

    calibrators = {}
    for f in fields:
        raw = [validation_results[cid][1].get(f) for cid in validation_ids]
        correct = [1 if str(validation_results[cid][0].get(f)) == str(reference[cid].get(f)) else 0
                    for cid in validation_ids]
        calibrators[f] = fit_calibration(raw, correct)

    predictions_rows = []
    field_predictions_by_cluster = {}
    for split_name, results, cids in (("validation", validation_results, validation_ids),
                                        ("test", test_results, test_ids)):
        for cid in cids:
            preds, raw_confs = results[cid]
            calibrated = {f: apply_calibration(calibrators[f], [raw_confs.get(f)])[0] for f in fields}
            unseen = unseen_reference_label_fields(train_values_by_field, reference[cid], fields)
            predictions_rows.append({
                "id": _new_id(), "benchmark_run_id": benchmark_run_id, "run_id": run_id, "task": task,
                "cluster_type": cluster_type, "cluster_id": cid, "item_key": "", "method": "small_llm",
                "method_variant": model, "split_scheme": split_scheme, "split": split_name,
                "field_predictions_json": preds, "field_raw_confidence_json": raw_confs,
                "field_calibrated_confidence_json": calibrated,
                "overall_raw_confidence": min((v for v in raw_confs.values() if v is not None), default=None),
                "overall_calibrated_confidence": min(calibrated.values()) if calibrated else None,
                "unseen_reference_label_fields": unseen,
                "novelty_proxy_score": None, "nearest_neighbor_distance": None,
            })
            if split_name == "test":
                field_predictions_by_cluster[cid] = preds

    field_metrics = {}
    for f in fields:
        y_true = [str(reference[cid].get(f)) for cid in test_ids]
        y_pred = [str(test_results[cid][0].get(f)) for cid in test_ids]
        field_metrics[f] = compute_field_metrics(y_true, y_pred)
        field_metrics[f]["class_support"] = compute_class_support({
            "train": [reference[cid].get(f) for cid in train_ids],
            "validation": [reference[cid].get(f) for cid in validation_ids],
            "test": [reference[cid].get(f) for cid in test_ids],
        })
        field_metrics[f]["production_eligible"] = len(train_values_by_field[f]) >= 2

    exact_match = cluster_exact_match_rate(field_predictions_by_cluster,
                                              {cid: reference[cid] for cid in test_ids}, fields)

    benchmark_row = {
        "id": _new_id(), "benchmark_run_id": benchmark_run_id, "run_id": run_id, "task": task,
        "method": "small_llm", "method_variant": model, "split_scheme": split_scheme, "split": "test",
        "field_metrics_json": field_metrics, "cluster_exact_match_rate": exact_match,
        "calibration_json": {f: "isotonic" if calibrators[f] is not None else "identity" for f in fields},
        "model_version": model, "relative_cost": None, "latency_ms_avg": None,
    }
    result = {"benchmark": benchmark_row, "predictions": predictions_rows}
    if collect_diagnostics:
        result["diagnostics"] = {
            "validation": _summarize_diagnostics(validation_diag), "test": _summarize_diagnostics(test_diag),
        }
    return result
