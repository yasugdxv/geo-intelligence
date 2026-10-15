"""Report Studio → Intelligence Analysis Platform: Phase 2.5 Gate 1
(Representative Benchmark, 3-way比較)。

詳細設計: docs/report-studio/INTELLIGENCE_PLATFORM_PHASE0_2_PLAN.md（Section F）＋
2026-09-13ユーザー指示（Phase 2.5 Gate 1）。

Phase 2の結果（Logistic Regression/Hybridが優秀）は「Current Reservoir代表投稿が
たまたま十分良い」ことと交絡している可能性があるため、Phase 3着手前に以下の3-wayを
同一Strong LLM・同一クラスタ集合で比較する:

  A. Historical Reference   — 既存geo_report_reference_examples（Production Prompt1の
                               実行結果そのもの、rerunしない）
  B. Current-Reservoir Rerun — 本番と同じreservoir representativesのまま、同じStrong
                               LLMで再分類する（LLMの非決定性/prompt drift単体の影響）
  C. New-Representative Rerun — typical_diverse_boundary_v1（Section F）で選び直した
                               representativesを使い、同じStrong LLMで再分類する
                               （代表選定方式を変えた影響）

A⇔B: Referenceデータ自体の再現性下限（reproducibility floor）
A⇔C, B⇔C: 新しい代表選定方式が既存より劣化していないか

既知の制限（正直に明記、データを捏造しない）: Section Fのtypical_diverse_boundary_v1は
本来record単位のsource_class_preliminary/attitude_hint/reaction_type_hint/is_exceptionを
Boundary/Exception役の判定に使う設計だが、これらはreservoir選出済みrepresentativeにしか
永続化されておらず、`geo_report_records`にはクラスタ内の任意recordのhintが無い。
本Gate 1実装ではこれらをNone/Falseとして渡す（`select_typical_diverse_boundary`は
その場合、Boundary役をcentroid距離のみで選ぶ設計になっている——嘘の値を作らず、
機能を握りつぶさない安全なfallback）。Typical/Diverse役はembedding距離のみに依存する
ため、この制限の影響を受けない。
"""

from __future__ import annotations

import random
import uuid
from collections import defaultdict

import geo_llm_common
from geo_external_signal_service import _select_all_paginated
from geo_report_prompt1_classification_service import (
    SENTIMENT_CLUSTER_CLASSIFICATION_SCHEMA,
    classify_sentiment_clusters_batch,
)
from geo_report_prompt1_orchestration_service import (
    _load_sentiment_clusters_and_representatives_batch,
    _sentiment_cluster_to_row,
)
from geo_report_representative_strategy_service import (
    save_current_reservoir_variants,
    save_typical_diverse_boundary_variant,
    select_typical_diverse_boundary,
)
from geo_report_semantic_census_benchmark_service import load_reference_labels, load_split_assignments
from geo_report_embedding_benchmark_service import compute_embeddings_for_records, get_embeddings

_CLUSTER_ID_COLUMN = {"analysis": "analysis_cluster_id", "sentiment": "sentiment_cluster_id"}
_REPRESENTATIVES_TABLE_COLUMN = {"analysis": "analysis_cluster_id", "sentiment": "sentiment_cluster_id"}


def _new_id() -> str:
    return str(uuid.uuid4())


def _chunked(items: list, size: int = 150):
    for i in range(0, len(items), size):
        yield items[i:i + size]


# ---------------------------------------------------------------------------
# サンプリング
# ---------------------------------------------------------------------------

def sample_clusters_for_gate1(client, run_id: str, benchmark_run_id: str, task: str, split_scheme: str, *,
                                 split: str = "test", n: int = 150, seed: int = 42) -> list:
    """Reference labelがあり、指定split_scheme上でexcludeされていないクラスタから
    決定的にn件サンプリングする（再実行しても同じサンプルになる）。"""
    reference = load_reference_labels(client, run_id, task)
    splits = load_split_assignments(client, benchmark_run_id, task, split_scheme)
    candidates = sorted(
        cid for cid, info in splits.items()
        if info["split"] == split and not info["excluded"] and cid in reference
    )
    rng = random.Random(seed)
    rng.shuffle(candidates)
    return sorted(candidates[:n])


# ---------------------------------------------------------------------------
# New-Representative Rerun用: record-level embeddingとrecordメタデータ
# ---------------------------------------------------------------------------

def _load_record_pool_meta(client, run_id: str, cluster_type: str, cluster_ids: list) -> dict:
    """cluster_id -> [{"record_id", "source_name", "source_bucket", "language", "match_status"}, ...]。"""
    cluster_col = _CLUSTER_ID_COLUMN[cluster_type]
    rows = _select_all_paginated(client, "geo_report_records", {
        "select": f"record_id,source_name,source_bucket,language,match_status,{cluster_col}",
        "run_id": f"eq.{run_id}", cluster_col: "in.(" + ",".join(cluster_ids) + ")",
    })
    by_cluster: dict = defaultdict(list)
    for r in rows:
        by_cluster[r[cluster_col]].append(r)
    return by_cluster


def build_new_representative_variants(client, run_id: str, cluster_type: str, cluster_ids: list, *,
                                         raw_csv_paths: list) -> int:
    """クラスタ内の全recordをrecord単位でembedding化し(Content Cache)、
    typical_diverse_boundary_v1選定を行って保存する。戻り値: 選定を保存したクラスタ数。"""
    if not cluster_ids:
        return 0
    compute_embeddings_for_records(client, run_id, cluster_type, cluster_ids, raw_csv_paths=raw_csv_paths)
    pool_by_cluster = _load_record_pool_meta(client, run_id, cluster_type, cluster_ids)

    saved = 0
    for cid in cluster_ids:
        pool = pool_by_cluster.get(cid, [])
        if not pool:
            continue
        record_ids = [r["record_id"] for r in pool]
        embeddings = get_embeddings(client, run_id, "record", record_ids)
        records_for_selection = [{
            "record_id": r["record_id"],
            "source_class_preliminary": None, "attitude_hint": None, "reaction_type_hint": None,
            "is_exception": False,
        } for r in pool]
        selection = select_typical_diverse_boundary(records_for_selection, embeddings)
        if not selection:
            continue
        save_typical_diverse_boundary_variant(client, run_id, cluster_type, cid, selection)
        saved += 1
    return saved


def _build_representative_rows_for_new_variant(client, run_id: str, cluster_type: str, cluster_ids: list, *,
                                                   raw_csv_paths: list) -> dict:
    """typical_diverse_boundary_v1のrecord_idからclassify_sentiment_clusters_batchが
    期待するrepresentative行の形（record_id/title/source_name/source_bucket/
    language_group/match_status/text_for_review）を組み立てる。"""
    cluster_col = _REPRESENTATIVES_TABLE_COLUMN[cluster_type]
    variant_rows = _select_all_paginated(client, "geo_report_representative_selection_variants", {
        "select": f"cluster_id,representative_record_ids", "run_id": f"eq.{run_id}",
        "cluster_type": f"eq.{cluster_type}", "strategy": "eq.typical_diverse_boundary_v1",
        "cluster_id": "in.(" + ",".join(cluster_ids) + ")",
    })
    all_record_ids = sorted({rid for row in variant_rows for rid in row["representative_record_ids"]})
    if not all_record_ids:
        return {}

    # record_idでchunkしたIN filterで絞り込む（URL長対策、既存パターンと同じ）
    meta_by_record: dict = {}
    for chunk in _chunked(all_record_ids):
        rows = _select_all_paginated(client, "geo_report_records", {
            "select": "record_id,source_name,source_bucket,language,match_status",
            "run_id": f"eq.{run_id}", "record_id": "in.(" + ",".join(chunk) + ")",
        })
        for r in rows:
            meta_by_record[r["record_id"]] = r

    from geo_report_benchmark_raw_text_service import load_record_texts
    texts_by_record = load_record_texts(raw_csv_paths, set(all_record_ids))

    reps_by_cluster: dict = {}
    for row in variant_rows:
        reps = []
        for rid in row["representative_record_ids"]:
            meta = meta_by_record.get(rid, {})
            text_info = texts_by_record.get(rid, {})
            reps.append({
                "record_id": rid, "title": text_info.get("title"),
                "source_name": meta.get("source_name"), "source_bucket": meta.get("source_bucket"),
                "language_group": meta.get("language"), "match_status": meta.get("match_status"),
                "text_for_review": text_info.get("text"),
            })
        reps_by_cluster[row["cluster_id"]] = reps
    return reps_by_cluster


# ---------------------------------------------------------------------------
# Strong LLM再分類（reconcile_batch_llm_resultsによるアイテム単位retry、Prompt1と同じ手法）
# ---------------------------------------------------------------------------

def rerun_strong_llm_for_variant(client, azure_client, model: str, run_id: str, cluster_type: str,
                                    cluster_ids: list, strategy: str, *,
                                    raw_csv_paths: list | None = None,
                                    batch_size: int = 20, max_extra_rounds: int = 2) -> dict:
    """strategy: 'current_reservoir' | 'typical_diverse_boundary_v1'。
    戻り値: {cluster_id: item_body}（SENTIMENT_CLUSTER_CLASSIFICATION_SCHEMAの全フィールド）。
    分類に失敗したクラスタはキー自体が無い。"""
    loaded = _load_sentiment_clusters_and_representatives_batch(client, run_id, cluster_ids)
    cluster_rows = {cid: info[0] for cid, info in loaded.items()}

    if strategy == "current_reservoir":
        reps_by_cluster = {cid: info[1] for cid, info in loaded.items()}
    elif strategy == "typical_diverse_boundary_v1":
        if not raw_csv_paths:
            raise ValueError("typical_diverse_boundary_v1にはraw_csv_pathsが必要です。")
        reps_by_cluster = _build_representative_rows_for_new_variant(
            client, run_id, cluster_type, cluster_ids, raw_csv_paths=raw_csv_paths)
    else:
        raise ValueError(f"未対応のstrategy: {strategy}")

    usable_ids = [cid for cid in cluster_ids if cid in cluster_rows and reps_by_cluster.get(cid)]
    results: dict = {}
    for i in range(0, len(usable_ids), batch_size):
        remaining = usable_ids[i:i + batch_size]
        for _round in range(max_extra_rounds + 1):
            if not remaining:
                break
            entries = [{"cluster_row": cluster_rows[cid], "representatives": reps_by_cluster[cid]}
                        for cid in remaining]
            try:
                response = classify_sentiment_clusters_batch(azure_client, model, entries)
                raw_results = response["data"].get("results", [])
            except Exception:
                continue
            reconciled = geo_llm_common.reconcile_batch_llm_results(
                remaining, raw_results, SENTIMENT_CLUSTER_CLASSIFICATION_SCHEMA)
            results.update(reconciled["valid"])
            remaining = list(dict.fromkeys(reconciled["missing"] + reconciled["invalid"]))
    return results


# ---------------------------------------------------------------------------
# 比較
# ---------------------------------------------------------------------------

SC_COMPARISON_FIELDS = [
    "theme_relevance", "source_class", "reaction_type", "attitude",
    "cluster_consistency", "apply_to_cluster",
]


def _field_value(source: dict, cid: str, field: str):
    row = source.get(cid)
    if row is None:
        return None
    return row.get(field)


def compare_three_way(historical: dict, reservoir_rerun: dict, new_rep_rerun: dict, cluster_ids: list, *,
                         fields: list = SC_COMPARISON_FIELDS) -> dict:
    """3ペア(A⇔B, A⇔C, B⇔C)についてfield毎agreement率とcluster完全一致率を計算する。
    戻り値: {"pairwise": {pair_name: {"field_agreement": {field: rate}, "exact_match_rate": rate,
    "n_compared": int}}, "disagreements": {pair_name: [cluster_id, ...]}}（全fieldどれか1つでも
    不一致だったクラスタ一覧、レビュー対象抽出用）。"""
    sources = {"A_historical": historical, "B_reservoir_rerun": reservoir_rerun, "C_new_rep_rerun": new_rep_rerun}
    pairs = [("A_historical", "B_reservoir_rerun"), ("A_historical", "C_new_rep_rerun"),
             ("B_reservoir_rerun", "C_new_rep_rerun")]

    pairwise = {}
    disagreements = {}
    for left, right in pairs:
        pair_name = f"{left}_vs_{right}"
        common_ids = [cid for cid in cluster_ids if cid in sources[left] and cid in sources[right]]
        field_agreement = {}
        for f in fields:
            if not common_ids:
                field_agreement[f] = None
                continue
            matches = sum(
                1 for cid in common_ids
                if str(_field_value(sources[left], cid, f)) == str(_field_value(sources[right], cid, f)))
            field_agreement[f] = matches / len(common_ids)
        exact_match_ids = [
            cid for cid in common_ids
            if all(str(_field_value(sources[left], cid, f)) == str(_field_value(sources[right], cid, f))
                    for f in fields)
        ]
        pairwise[pair_name] = {
            "field_agreement": field_agreement,
            "exact_match_rate": len(exact_match_ids) / len(common_ids) if common_ids else None,
            "n_compared": len(common_ids),
        }
        disagreements[pair_name] = sorted(set(common_ids) - set(exact_match_ids))

    return {"pairwise": pairwise, "disagreements": disagreements}


def sample_disagreements_for_review(disagreement_cluster_ids: list, *, n: int = 25, seed: int = 42) -> list:
    """人間/Claudeレビュー対象として、不一致クラスタからn件を決定的にサンプリングする。"""
    rng = random.Random(seed)
    ids = sorted(disagreement_cluster_ids)
    rng.shuffle(ids)
    return sorted(ids[:n])


# ---------------------------------------------------------------------------
# Representative count / token量比較 + レビュー用バンドル
# ---------------------------------------------------------------------------

def representative_stats_by_strategy(client, run_id: str, cluster_type: str, cluster_ids: list, *,
                                        raw_csv_paths: list) -> dict:
    """strategy別に、クラスタあたりの平均representative件数・平均合計文字数
    （4文字≒1トークンの粗い見積り、Prompt1本番と同じ換算）を比較する。"""
    reservoir_reps = {cid: info[1] for cid, info in
                        _load_sentiment_clusters_and_representatives_batch(client, run_id, cluster_ids).items()}
    new_reps = _build_representative_rows_for_new_variant(
        client, run_id, cluster_type, cluster_ids, raw_csv_paths=raw_csv_paths)

    def _stats(reps_by_cluster: dict) -> dict:
        counts = [len(reps) for reps in reps_by_cluster.values()]
        chars = [sum(len(r.get("title") or "") + len(r.get("text_for_review") or "") for r in reps)
                  for reps in reps_by_cluster.values()]
        n = len(counts)
        return {
            "n_clusters": n,
            "avg_representative_count": (sum(counts) / n) if n else None,
            "avg_total_chars": (sum(chars) / n) if n else None,
            "avg_estimated_tokens": (sum(chars) / n / 4) if n else None,
        }

    return {"current_reservoir": _stats(reservoir_reps), "typical_diverse_boundary_v1": _stats(new_reps)}


def build_review_bundle_for_disagreements(client, run_id: str, cluster_type: str, cluster_ids: list, *,
                                             historical: dict, reservoir_rerun: dict, new_rep_rerun: dict,
                                             raw_csv_paths: list) -> dict:
    """screening/provisional review用: 不一致クラスタについて、3-wayの各判定結果＋
    reservoir/new-representative双方の代表投稿本文＋クラスタ所属の全record本文を
    1つにまとめる。**analyst-verified（人手確定レビュー）ではなく、Claudeによる
    screening/provisional reviewの入力として使う**（Gate 1はHuman Goldを持たない）。"""
    reservoir_reps = {cid: info[1] for cid, info in
                        _load_sentiment_clusters_and_representatives_batch(client, run_id, cluster_ids).items()}
    new_reps = _build_representative_rows_for_new_variant(
        client, run_id, cluster_type, cluster_ids, raw_csv_paths=raw_csv_paths)
    pool_by_cluster = _load_record_pool_meta(client, run_id, cluster_type, cluster_ids)
    all_record_ids = sorted({r["record_id"] for pool in pool_by_cluster.values() for r in pool})
    from geo_report_benchmark_raw_text_service import load_record_texts
    texts_by_record = load_record_texts(raw_csv_paths, set(all_record_ids))

    bundle = {}
    for cid in cluster_ids:
        full_pool = []
        for r in pool_by_cluster.get(cid, []):
            text_info = texts_by_record.get(r["record_id"], {})
            full_pool.append({
                "record_id": r["record_id"], "source_name": r.get("source_name"),
                "source_bucket": r.get("source_bucket"), "language": r.get("language"),
                "match_status": r.get("match_status"),
                "title": text_info.get("title"), "text": text_info.get("text"),
            })
        bundle[cid] = {
            "historical_reference": historical.get(cid),
            "reservoir_rerun": reservoir_rerun.get(cid),
            "new_representative_rerun": new_rep_rerun.get(cid),
            "reservoir_representatives": reservoir_reps.get(cid, []),
            "new_representatives": new_reps.get(cid, []),
            "full_record_pool": full_pool,
        }
    return bundle
