"""Report Studio → Intelligence Analysis Platform: Feature Dataset（Phase 1、v1）。

詳細設計: docs/report-studio/INTELLIGENCE_PLATFORM_PHASE0_2_PLAN.md（Section E）。

`geo_report_cluster_features`にはsplit/method非依存のcluster factのみを持たせる。
`novelty_proxy_score`/`nearest_neighbor_distance`/`prediction_entropy`はここに
置かない（split_scheme・methodに依存するため`geo_report_semantic_census_predictions`
にのみ持たせる、Section D/E参照）。

`narrative_priority_score`はRouter専用のimportance featureであり、Semantic Census
分類器（B〜E、`geo_report_semantic_census_service.py`）の入力特徴には使わない。
"""

from __future__ import annotations

import math
import uuid

from geo_external_signal_service import _select_all_paginated

FEATURE_VERSION_V1 = "v1-no-embeddings"

_CONSISTENCY_SCORE_MAP = {"High": 1.0, "Medium": 0.5, "Low": 0.0}
_PRIORITY_SCORE_MAP = {"High": 1.0, "Medium": 0.5, "Low": 0.0}

_CLUSTER_TABLES = {
    "analysis": ("geo_report_analysis_clusters", "analysis_cluster_id",
                  "geo_report_analysis_cluster_representatives", "analysis_cluster_id"),
    "sentiment": ("geo_report_sentiment_clusters", "sentiment_cluster_id",
                   "geo_report_sentiment_cluster_representatives", "sentiment_cluster_id"),
}


def _new_id() -> str:
    return str(uuid.uuid4())


def _chunked(items: list, size: int = 500):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _shannon_entropy(distribution_str: str) -> float:
    """"value:count|value2:count2"形式の分布文字列からShannon entropy(bit単位)を計算する。
    1種類しか値が無ければ0.0（Analysis Cluster/Sentiment Clusterのクラスタリングキーの
    一部にsource_class_preliminary等が含まれるため、Sentiment Clusterではほぼ常に0.0に
    なる——これは実データの構造上の性質であり、バグではない）。"""
    if not distribution_str:
        return 0.0
    counts = []
    for part in distribution_str.split("|"):
        if ":" not in part:
            continue
        _, count_str = part.rsplit(":", 1)
        try:
            counts.append(int(count_str))
        except ValueError:
            continue
    total = sum(counts)
    if total <= 0:
        return 0.0
    entropy = 0.0
    for c in counts:
        if c <= 0:
            continue
        p = c / total
        entropy -= p * math.log2(p)
    return round(entropy, 6)


def _complexity_score(row: dict) -> float:
    cluster_records = row.get("cluster_records") or 0
    exception_count = row.get("exception_candidate_count") or 0
    requires_review = 1.0 if row.get("requires_review") else 0.0
    exception_ratio = (exception_count / cluster_records) if cluster_records else 0.0
    return round(0.5 * exception_ratio + 0.5 * requires_review, 4)


def _load_narrative_priority_meta(client, run_id: str) -> dict:
    """narrative_id(sub_narrative_id優先) -> {"priority": ..., "trend_role": ...}。
    Narrative Masterの34列契約から読む(Prompt1 Targeted Stanceのrequired_context読み込み
    と同じパターン)。"""
    run_rows = client.select("geo_report_collection_runs", {
        "select": "narrative_master_asset_id", "run_id": f"eq.{run_id}"})
    asset_id = run_rows[0].get("narrative_master_asset_id") if run_rows else None
    if not asset_id:
        return {}
    asset_rows = client.select("geo_report_project_assets", {"select": "content", "id": f"eq.{asset_id}"})
    if not asset_rows:
        return {}
    rows = (asset_rows[0].get("content") or {}).get("rows") or []
    meta: dict = {}
    for r in rows:
        nid = r.get("sub_narrative_id") or r.get("narrative_id")
        if nid:
            meta[nid] = {"priority": r.get("priority"), "trend_role": r.get("trend_role")}
    return meta


def _narrative_priority_score(meta: dict | None) -> float:
    if not meta:
        return 0.5
    priority_score = _PRIORITY_SCORE_MAP.get(meta.get("priority"), 0.5)
    trend_boost = 0.2 if meta.get("trend_role") == "primary" else 0.0
    return round(min(1.0, priority_score + trend_boost), 4)


def _representative_diversity_score(reps: list) -> float | None:
    """v1（no-embedding）の粗い代理指標: 代表投稿のsource_nameの多様性
    （distinct数/件数）。embeddingが無い段階でも計算できる簡易版。"""
    if not reps:
        return None
    names = [r.get("source_name") for r in reps if r.get("source_name")]
    if not names:
        return None
    return round(len(set(names)) / len(names), 4)


def compute_features_for_run(client, run_id: str, cluster_type: str) -> int:
    """指定run_id・cluster_typeの全クラスタについてv1 featureを計算し、
    `geo_report_cluster_features`（feature_version='v1-no-embeddings'）へ書き込む。
    戻り値: 書き込んだクラスタ数。"""
    cluster_table, cluster_id_col, reps_table, reps_cluster_id_col = _CLUSTER_TABLES[cluster_type]

    cluster_rows = _select_all_paginated(client, cluster_table, {"select": "*", "run_id": f"eq.{run_id}"})
    if not cluster_rows:
        return 0

    reps_rows = _select_all_paginated(client, reps_table, {
        "select": f"{reps_cluster_id_col},source_name", "run_id": f"eq.{run_id}",
    })
    reps_by_cluster: dict = {}
    for r in reps_rows:
        reps_by_cluster.setdefault(r[reps_cluster_id_col], []).append(r)

    priority_meta = _load_narrative_priority_meta(client, run_id)

    feature_rows = []
    for row in cluster_rows:
        cluster_id = row[cluster_id_col]
        narrative_meta = priority_meta.get(row.get("narrative_id"))
        feature_rows.append({
            "id": _new_id(), "run_id": run_id, "cluster_type": cluster_type, "cluster_id": cluster_id,
            "feature_version": FEATURE_VERSION_V1,
            "volume": row.get("cluster_records"),
            "preliminary_consistency_score": _CONSISTENCY_SCORE_MAP.get(row.get("preliminary_consistency")),
            "cohesion": None,
            "dispersion": None,
            "source_entropy": _shannon_entropy(row.get("source_distribution")),
            "language_entropy": _shannon_entropy(row.get("language_distribution")),
            "fallback_share": row.get("fallback_share"),
            "narrative_similarity": None,
            "complexity_score": _complexity_score(row),
            "representative_diversity_score": _representative_diversity_score(
                reps_by_cluster.get(cluster_id, [])),
            "narrative_priority_score": _narrative_priority_score(narrative_meta),
            "metadata_json": {"signal_id": row.get("signal_id"), "narrative_id": row.get("narrative_id")},
        })

    client.delete("geo_report_cluster_features", {
        "run_id": f"eq.{run_id}", "cluster_type": f"eq.{cluster_type}", "feature_version": f"eq.{FEATURE_VERSION_V1}",
    })
    for chunk in _chunked(feature_rows):
        client.insert("geo_report_cluster_features", chunk, prefer="return=minimal")

    return len(feature_rows)
