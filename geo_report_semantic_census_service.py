"""Report Studio → Intelligence Analysis Platform: Semantic Census候補モデル（Phase 2）。

詳細設計: docs/report-studio/INTELLIGENCE_PLATFORM_PHASE0_2_PLAN.md（Section H）。

AC/SC分類は複数フィールドを同時に出すmulti-output classificationであり、label全体を
1つのclassとして扱うとclass数が組合せ爆発するため使わない。Field単位で個別の
classifier/分類ロジックを持つ。

Semantic Census対象fieldとCurrent Prompt1の自由記述fieldを明示的に分離する:
Phase 2のBenchmark/Router SimulationはSEMANTIC_CENSUS_FIELDS_*だけを対象にする。
GENERATIVE_FIELDS_*（自由記述、`classification_reason`等）はPhase 2では生成しない
——Lightweightで生成する方式は未設計・未検証であり、Phase 3の課題として明確に
切り分ける（それが解決するまでLightweight結果をCanonical Prompt1 Outputへ
直接採用しない）。

`narrative_priority_score`はいかなるSemantic Census分類器（B〜E）の入力特徴にも
使わない——importanceは分類結果を変える情報ではなく、Router専用。
"""

from __future__ import annotations

from collections import Counter

import numpy as np

import geo_llm_common
from geo_report_prompt1_classification_service import (
    ANALYSIS_CLUSTER_CLASSIFICATION_SCHEMA,
    SENTIMENT_CLUSTER_CLASSIFICATION_SCHEMA,
    _batch_results_schema,
    _format_representatives,
)

# --- Field Registry（Section H、重要: 分類対象fieldと自由記述fieldを明示的に分離する） ---
#
# 2026-09-13訂正（Gate 3実データ実行で発覚）: 当初SEMANTIC_CENSUS_FIELDS_ACに含めていた
# source_assessment/tone_gradient/voice_direction/report_use_status/continuity_relevance
# の5フィールドは、ANALYSIS_CLUSTER_CLASSIFICATION_SCHEMA上でもenum制約の無い自由記述
# フィールドであり（実際production Prompt1はこれらを日本語自然文で出力する——例:
# "該当なし"/"強い批判"——一方Small LLMは英語の短いラベルで応答するなど、表現・言語が
# 一致する保証が無い）、field agreementが0.0近辺に張り付いた（分類対象として不適切、
# Small LLM自体の障害ではない）。Categorical Census対象として有効なのは実際にenumを
# 持つnoise_flag（boolean）とrepresentative_quality（High/Medium/Low）の2つのみ。
# 残り5つはGENERATIVE_FIELDS_ACへ移した——「意味的に同じ内容を指しているか」は
# exact-string-matchで測れないため、Semantic Census（field-wise classifier）の対象には
# しない（必要ならdisagreement caseの人手/Claude semantic reviewのみ行う）。
SEMANTIC_CENSUS_FIELDS_AC = ["noise_flag", "representative_quality"]
GENERATIVE_FIELDS_AC = [
    "final_topic_label", "change_signal_summary", "noise_reason",
    "voice_summary", "notable_phrases", "notes_for_prompt2",
    "source_assessment", "tone_gradient", "voice_direction",
    "report_use_status", "continuity_relevance",
]
SEMANTIC_CENSUS_FIELDS_SC = [
    "theme_relevance", "source_class", "reaction_type", "attitude",
    "cluster_consistency", "apply_to_cluster",
]
GENERATIVE_FIELDS_SC = ["classification_reason"]

# Hybrid（E）分類器の入力に使ってよい決定的数値特徴量（Section D/Eで明示された6個限定。
# narrative_priority_scoreは含めない——Router専用のため）。
HYBRID_NUMERIC_FEATURES = [
    "fallback_share", "source_entropy", "language_entropy",
    "preliminary_consistency_score", "complexity_score", "representative_diversity_score",
]

METHODS = ("small_llm", "embedding_nn", "logistic_regression", "gradient_boosting", "hybrid")


def semantic_census_fields(task: str) -> list:
    return SEMANTIC_CENSUS_FIELDS_AC if task == "ac_classification" else SEMANTIC_CENSUS_FIELDS_SC


# ---------------------------------------------------------------------------
# A. Small LLM（minimal schema、自由記述フィールドを含まない）
# ---------------------------------------------------------------------------

def _semantic_census_item_schema(task: str) -> dict:
    full_schema = ANALYSIS_CLUSTER_CLASSIFICATION_SCHEMA if task == "ac_classification" \
        else SENTIMENT_CLUSTER_CLASSIFICATION_SCHEMA
    fields = semantic_census_fields(task)
    props = {"cluster_id": {"type": "string"}}
    required = ["cluster_id"]
    for f in fields:
        props[f] = full_schema["properties"][f]
        required.append(f)
        conf_field = f"{f}_confidence"
        props[conf_field] = {"type": "number"}
        required.append(conf_field)
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def _semantic_census_validation_schema(task: str) -> dict:
    """geo_llm_common.reconcile_batch_llm_results用: id_field(cluster_id)を
    reconcile側が既に取り除いた後のitem_bodyを検証するschema
    （_semantic_census_item_schemaは呼び出しスキーマ用でcluster_idをrequiredに含むため、
    そのままでは除去後のitem_bodyの検証に使えない）。"""
    item_schema = _semantic_census_item_schema(task)
    props = {k: v for k, v in item_schema["properties"].items() if k != "cluster_id"}
    required = [r for r in item_schema["required"] if r != "cluster_id"]
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def _semantic_census_system_prompt(task: str) -> str:
    label = "Analysis Cluster" if task == "ac_classification" else "Sentiment Cluster"
    return (
        f"あなたはReport StudioのSemantic Census（軽量事前分類）担当です。{label}の"
        "分類フィールドのみを、既存Prompt1の判断基準に沿って高速に判定してください。"
        "理由や要約などの自由記述は一切生成しないこと。各フィールドについて、自身の"
        "判断がどれだけ確信できるかを0〜1の数値でconfidenceとして付けてください。"
    )


def build_semantic_census_batch_user_prompt(task: str, clusters: list) -> str:
    """clusters: [{"cluster_row": dict, "representatives": list}, ...]（既存Prompt1
    batch関数と同じ入力形状）。"""
    lines = [f"## Semantic Census対象（バッチ、複数クラスタを含む、task={task}）", ""]
    for item in clusters:
        cluster_row = item["cluster_row"]
        lines.append(f"### cluster_id: {cluster_row.get('cluster_id')}")
        lines.append(f"signal_id: {cluster_row.get('signal_id')}")
        lines.append("")
        lines.append(_format_representatives(item["representatives"]))
        lines.append("")
    return "\n".join(lines)


def classify_semantic_census_batch(azure_client, model: str, task: str, clusters: list, *,
                                     max_attempts: int = 2) -> dict:
    """Method A(Small LLM)。modelは'gpt-4o-mini'/'gpt-4.1-mini'等の安価deploymentを渡す。
    戻り値: geo_llm_common.call_llm_structuredの戻り値そのまま
    （`data["results"]`が各クラスタの{"cluster_id":..., <field>:..., <field>_confidence:...}）。"""
    item_schema = _semantic_census_item_schema(task)
    schema = _batch_results_schema(item_schema)
    user_prompt = build_semantic_census_batch_user_prompt(task, clusters)
    schema_name = f"geo_report_semantic_census_{task}_batch"
    return geo_llm_common.call_llm_structured(
        azure_client, model, _semantic_census_system_prompt(task), user_prompt,
        schema, schema_name, max_attempts=max_attempts)


def extract_field_predictions(task: str, item: dict) -> tuple:
    """Small LLM生バッチ結果の1item dictから、
    (field_predictions:{field:value}, field_raw_confidence:{field:float})を取り出す。"""
    fields = semantic_census_fields(task)
    predictions = {f: item.get(f) for f in fields}
    confidences = {f: item.get(f"{f}_confidence") for f in fields}
    return predictions, confidences


# ---------------------------------------------------------------------------
# B/C/D/E. Embedding系（field毎classifier）
# ---------------------------------------------------------------------------

def _build_feature_matrix(cluster_ids: list, centroid_by_cluster: dict, *,
                            numeric_features_by_cluster: dict | None = None) -> np.ndarray:
    """cluster_id順にcentroid embedding（+ Hybridの場合は数値特徴量concatenate）を
    並べた行列を作る。centroidが無いclusterは行がNoneになるため、呼び出し側で
    事前にcluster_idsをフィルタしておくこと。"""
    rows = []
    for cid in cluster_ids:
        vec = centroid_by_cluster[cid]
        if numeric_features_by_cluster is not None:
            extra = numeric_features_by_cluster.get(cid, {})
            extra_vec = np.array([extra.get(f) if extra.get(f) is not None else 0.0
                                    for f in HYBRID_NUMERIC_FEATURES], dtype=np.float32)
            vec = np.concatenate([vec, extra_vec])
        rows.append(vec)
    return np.vstack(rows)


class FieldClassifier:
    """1 field分のclassifier（method='embedding_nn'/'logistic_regression'/
    'gradient_boosting'/'hybrid'）。sklearnの薄いラッパー。"""

    def __init__(self, method: str):
        self.method = method
        self.model = None
        self.classes_: list = []

    def fit(self, X: np.ndarray, y: list) -> "FieldClassifier":
        self.classes_ = sorted(set(y))
        if len(self.classes_) < 2:
            # 単一classしか無いTrainではsklearn分類器を学習できないため、
            # 定数予測にfall backする（production_eligible判定はSection J側の責務）。
            self.model = None
            return self
        if self.method == "embedding_nn":
            from sklearn.neighbors import KNeighborsClassifier
            k = min(5, len(y))
            self.model = KNeighborsClassifier(n_neighbors=k, metric="cosine")
        elif self.method in ("logistic_regression", "hybrid"):
            from sklearn.linear_model import LogisticRegression
            self.model = LogisticRegression(max_iter=1000)
        elif self.method == "gradient_boosting":
            from sklearn.ensemble import GradientBoostingClassifier
            self.model = GradientBoostingClassifier()
        else:
            raise ValueError(f"未対応のmethod: {self.method}")
        self.model.fit(X, y)
        return self

    def predict_with_confidence(self, X: np.ndarray) -> list:
        """戻り値: [(prediction, confidence), ...]（Xの行順）。"""
        if self.model is None:
            const = self.classes_[0] if self.classes_ else None
            return [(const, 0.0) for _ in range(X.shape[0])]
        proba = self.model.predict_proba(X)
        classes = list(self.model.classes_)
        results = []
        for row in proba:
            idx = int(np.argmax(row))
            results.append((classes[idx], float(row[idx])))
        return results


def train_field_classifiers(method: str, task: str, X: np.ndarray, labels_by_field: dict) -> dict:
    """labels_by_field: {field: [label, ...]}（Xと同じ行順）。
    戻り値: {field: FieldClassifier}。"""
    fields = semantic_census_fields(task)
    classifiers = {}
    for f in fields:
        clf = FieldClassifier(method)
        clf.fit(X, labels_by_field[f])
        classifiers[f] = clf
    return classifiers


def predict_with_field_classifiers(classifiers: dict, task: str, X: np.ndarray) -> list:
    """戻り値: cluster毎に(field_predictions:dict, field_raw_confidence:dict)のリスト
    （Xの行順）。"""
    fields = semantic_census_fields(task)
    per_field_results = {f: classifiers[f].predict_with_confidence(X) for f in fields}
    n = X.shape[0]
    results = []
    for i in range(n):
        preds = {f: per_field_results[f][i][0] for f in fields}
        confs = {f: per_field_results[f][i][1] for f in fields}
        results.append((preds, confs))
    return results
