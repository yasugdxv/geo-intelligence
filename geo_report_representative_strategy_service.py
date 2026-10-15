"""Report Studio → Intelligence Analysis Platform: Representative Selection候補（Phase 1/2）。

詳細設計: docs/report-studio/INTELLIGENCE_PLATFORM_PHASE0_2_PLAN.md（Section F）。

Strategy 'current_reservoir'（既存のまま、変更なし）と、新規Strategy
'typical_diverse_boundary_v1'（候補母集団はBenchmark対象クラスタに属する全record、
既存reservoir代表5件からの選び直しではない）を比較できるようにする。

Boundary候補の定義（record-level Reference labelは存在しないため、これらのみを使う）:
(a) centroidから遠いがクラスタのmatch対象ではあるrecord
(b) 隣接クラスタのcentroidにも近いrecord（クラスタ境界上にある可能性）
(c) source_class_preliminary/attitude_hint/reaction_type_hintがクラスタ多数派と異なるrecord
(d) 既存のis_exception判定に該当するrecord
"""

from __future__ import annotations

import uuid
from collections import Counter

import numpy as np

_CLUSTER_ID_COLUMN = {"analysis": "analysis_cluster_id", "sentiment": "sentiment_cluster_id"}
_REPRESENTATIVES_TABLE = {
    "analysis": "geo_report_analysis_cluster_representatives",
    "sentiment": "geo_report_sentiment_cluster_representatives",
}
_REPRESENTATIVE_CLUSTER_COLUMN = {"analysis": "analysis_cluster_id", "sentiment": "sentiment_cluster_id"}

MAX_REPRESENTATIVES = 5


def _new_id() -> str:
    return str(uuid.uuid4())


def _chunked(items: list, size: int = 500):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def save_current_reservoir_variants(client, run_id: str, cluster_type: str, cluster_ids: list) -> int:
    """既存のreservoir sampling代表投稿(Production representatives)を
    strategy='current_reservoir'として`geo_report_representative_selection_variants`
    へそのままコピー保存する（baseline、変更なし）。"""
    table = _REPRESENTATIVES_TABLE[cluster_type]
    cluster_col = _REPRESENTATIVE_CLUSTER_COLUMN[cluster_type]
    from geo_external_signal_service import _select_all_paginated
    reps = _select_all_paginated(client, table, {
        "select": f"record_id,{cluster_col}", "run_id": f"eq.{run_id}",
        cluster_col: "in.(" + ",".join(cluster_ids) + ")",
    })
    by_cluster: dict = {}
    for r in reps:
        by_cluster.setdefault(r[cluster_col], []).append(r["record_id"])

    client.delete("geo_report_representative_selection_variants", {
        "run_id": f"eq.{run_id}", "cluster_type": f"eq.{cluster_type}", "strategy": "eq.current_reservoir",
    })
    rows = [{
        "id": _new_id(), "run_id": run_id, "cluster_type": cluster_type, "cluster_id": cid,
        "strategy": "current_reservoir", "representative_record_ids": record_ids,
        "selection_metadata": {"source": "production_reservoir_sampling"},
    } for cid, record_ids in by_cluster.items()]
    for chunk in _chunked(rows):
        client.insert("geo_report_representative_selection_variants", chunk, prefer="return=minimal")
    return len(rows)


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = (np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def select_typical_diverse_boundary(records: list, embeddings_by_record_id: dict, *,
                                      max_count: int = MAX_REPRESENTATIVES) -> list:
    """records: [{"record_id":..., "source_class_preliminary":..., "attitude_hint":...,
    "reaction_type_hint":..., "is_exception": bool}, ...]（クラスタに属する全record）。
    embeddings_by_record_id: {record_id: np.ndarray}（record-level embedding、
    Section GのContent Cacheから取得済みのもの）。

    戻り値: 最大max_count件の{"record_id":..., "role": "typical"|"diverse"|"boundary"|
    "exception"}のリスト。embeddingが無いrecordは候補から除外する。"""
    candidates = [r for r in records if r["record_id"] in embeddings_by_record_id]
    if not candidates:
        return []

    vectors = {r["record_id"]: embeddings_by_record_id[r["record_id"]] for r in candidates}
    centroid = np.mean(list(vectors.values()), axis=0)

    def _dist_to_centroid(rid):
        return 1.0 - _cosine_similarity(vectors[rid], centroid)

    remaining = list(vectors.keys())
    selected: list = []

    # 2 Typical: centroidに近い順
    remaining.sort(key=_dist_to_centroid)
    for rid in remaining[:2]:
        if len(selected) < max_count:
            selected.append({"record_id": rid, "role": "typical"})
    remaining = [rid for rid in remaining if rid not in {s["record_id"] for s in selected}]

    by_id = {r["record_id"]: r for r in candidates}

    # 1 Important/Exception: 既存is_exception判定に該当するrecordを優先確保する。
    # diverse/boundaryより先に選ぶ——どちらもヒューリスティックにoutlierを狙うため、
    # 後回しにすると同じ少数のoutlierをdiverse/boundaryが先に食い合い、既存の
    # is_exceptionフラグを持つrecordが選ばれずに終わるリスクがある。
    if remaining and len(selected) < max_count:
        exception_candidates = [rid for rid in remaining if by_id[rid].get("is_exception")]
        if exception_candidates:
            exception_rid = max(exception_candidates, key=_dist_to_centroid)
            selected.append({"record_id": exception_rid, "role": "exception"})
            remaining = [rid for rid in remaining if rid != exception_rid]

    # 1 Diverse: 既選択集合とのmax-min距離(MMR)
    if remaining and len(selected) < max_count:
        def _min_dist_to_selected(rid):
            return min(1.0 - _cosine_similarity(vectors[rid], vectors[s["record_id"]]) for s in selected)
        diverse_rid = max(remaining, key=_min_dist_to_selected)
        selected.append({"record_id": diverse_rid, "role": "diverse"})
        remaining = [rid for rid in remaining if rid != diverse_rid]

    # 1 Boundary: 優先順位 (c)preliminary hintがクラスタ多数派と異なる > (a)centroidから遠い
    if remaining and len(selected) < max_count:
        majority_source = Counter(r.get("source_class_preliminary") for r in candidates).most_common(1)
        majority_attitude = Counter(r.get("attitude_hint") for r in candidates).most_common(1)
        majority_source = majority_source[0][0] if majority_source else None
        majority_attitude = majority_attitude[0][0] if majority_attitude else None

        def _is_hint_divergent(rid):
            r = by_id[rid]
            return (r.get("source_class_preliminary") != majority_source
                     or r.get("attitude_hint") != majority_attitude)

        divergent = [rid for rid in remaining if _is_hint_divergent(rid)]
        if divergent:
            boundary_rid = max(divergent, key=_dist_to_centroid)
        else:
            boundary_rid = max(remaining, key=_dist_to_centroid)
        selected.append({"record_id": boundary_rid, "role": "boundary"})
        remaining = [rid for rid in remaining if rid != boundary_rid]

    # exception候補が無かった場合のフォールバック（枠を埋めるため残りから1件補充）
    if remaining and len(selected) < max_count:
        selected.append({"record_id": remaining[0], "role": "exception"})

    return selected[:max_count]


def save_typical_diverse_boundary_variant(client, run_id: str, cluster_type: str, cluster_id: str,
                                             selection: list) -> None:
    client.delete("geo_report_representative_selection_variants", {
        "run_id": f"eq.{run_id}", "cluster_type": f"eq.{cluster_type}", "cluster_id": f"eq.{cluster_id}",
        "strategy": "eq.typical_diverse_boundary_v1",
    })
    client.insert("geo_report_representative_selection_variants", [{
        "id": _new_id(), "run_id": run_id, "cluster_type": cluster_type, "cluster_id": cluster_id,
        "strategy": "typical_diverse_boundary_v1",
        "representative_record_ids": [s["record_id"] for s in selection],
        "selection_metadata": {"roles": selection},
    }], prefer="return=minimal")
