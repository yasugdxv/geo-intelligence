"""Report Studio → Intelligence Analysis Platform: Benchmark Split Assignment（Phase 2）。

詳細設計: docs/report-studio/INTELLIGENCE_PLATFORM_PHASE0_2_PLAN.md（Section I）。

splitはReference Datasetの属性ではなくBenchmark実験属性として分離管理する
（`geo_report_benchmark_split_assignments`、`geo_report_reference_examples`自体は
immutable）。

Leakage Guard: 同一record_id/duplicate_group_id/url_hash/text_hashを共有するクラスタ
同士をLeakage Group（connected component）としてまとめ、
- narrative_holdout split_schemeでは、Leakage Group全体を必ず同一splitへ置く
- temporal split_schemeでは、時間境界を跨ぐLeakage Groupを評価対象から除外する
  （クラスタのReference Labelやレコード構成そのものは変更しない）
"""

from __future__ import annotations

import hashlib
import uuid
from collections import defaultdict

from geo_external_signal_service import _select_all_paginated

_CLUSTER_ID_COLUMN = {"analysis": "analysis_cluster_id", "sentiment": "sentiment_cluster_id"}
_CLUSTER_TABLE = {"analysis": "geo_report_analysis_clusters", "sentiment": "geo_report_sentiment_clusters"}


def _new_id() -> str:
    return str(uuid.uuid4())


def _chunked(items: list, size: int = 500):
    for i in range(0, len(items), size):
        yield items[i:i + size]


class _UnionFind:
    def __init__(self):
        self.parent: dict = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def build_leakage_groups(client, run_id: str, cluster_type: str, cluster_ids: list) -> dict:
    """cluster_id -> leakage_group_id（record_id/duplicate_group_id/url_hash/text_hashを
    共有するクラスタ同士のconnected component）。cluster_idsに含まれないクラスタは無視する
    （このBenchmarkの評価対象クラスタだけで完結させる）。"""
    if not cluster_ids:
        return {}
    col = _CLUSTER_ID_COLUMN[cluster_type]
    record_rows = _select_all_paginated(client, "geo_report_records", {
        "select": f"record_id,duplicate_group_id,url_hash,text_hash,{col}",
        "run_id": f"eq.{run_id}",
    })
    cluster_id_set = set(cluster_ids)
    uf = _UnionFind()
    key_to_cluster: dict = {}
    seen_clusters = set()
    for r in record_rows:
        cid = r.get(col)
        if cid not in cluster_id_set:
            continue
        uf.find(cid)
        seen_clusters.add(cid)
        for key_type, val in (("record", r.get("record_id")), ("dup", r.get("duplicate_group_id")),
                                ("url", r.get("url_hash")), ("text", r.get("text_hash"))):
            if not val:
                continue
            key = f"{key_type}:{val}"
            if key in key_to_cluster:
                uf.union(cid, key_to_cluster[key])
            else:
                key_to_cluster[key] = cid
    groups = {}
    for cid in cluster_ids:
        if cid in seen_clusters:
            groups[cid] = uf.find(cid)
        else:
            groups[cid] = cid  # レコードが見つからない場合、自分自身だけの孤立グループとして扱う
    return groups


def _deterministic_bucket(seed_text: str, random_seed: int) -> float:
    """[0,1)の疑似乱数値をrecord_id等と無関係な決定的hashから作る（再現性のため
    実際の乱数生成器ではなくhashベースにする）。"""
    h = hashlib.sha256(f"{random_seed}:{seed_text}".encode("utf-8")).hexdigest()
    return int(h[:12], 16) / float(0x1000000000000)


def assign_narrative_holdout_split(client, run_id: str, task: str, cluster_type: str,
                                     cluster_ids: list, *, random_seed: int = 42,
                                     test_share: float = 0.2, validation_share: float = 0.2) -> dict:
    """narrative_id単位でTestを完全ホールドアウトし、Leakage Groupをまたがないよう拡張する。
    戻り値: {cluster_id: {"split": ..., "group_key": leakage_group_id}}。"""
    col = _CLUSTER_ID_COLUMN[cluster_type]
    cluster_rows = _select_all_paginated(client, _CLUSTER_TABLE[cluster_type], {
        "select": f"{col},narrative_id", "run_id": f"eq.{run_id}",
    })
    narrative_by_cluster = {r[col]: r.get("narrative_id") or "" for r in cluster_rows if r[col] in set(cluster_ids)}

    narratives = sorted(set(narrative_by_cluster.values()))
    test_narratives = {n for n in narratives if _deterministic_bucket(n, random_seed) < test_share}

    leakage_groups = build_leakage_groups(client, run_id, cluster_type, cluster_ids)

    split_by_cluster = {}
    for cid in cluster_ids:
        narrative_id = narrative_by_cluster.get(cid, "")
        split_by_cluster[cid] = "test" if narrative_id in test_narratives else "train_or_validation"

    # Leakage Group内に1つでもtestがあれば、Group全体をtestへ拡張する
    group_has_test = defaultdict(bool)
    for cid, group_id in leakage_groups.items():
        if split_by_cluster.get(cid) == "test":
            group_has_test[group_id] = True
    for cid, group_id in leakage_groups.items():
        if group_has_test[group_id]:
            split_by_cluster[cid] = "test"

    # train_or_validationをvalidation_share/(1-test_share相当)で振り分ける
    remaining = [cid for cid, s in split_by_cluster.items() if s == "train_or_validation"]
    validation_ratio_within_remaining = validation_share / max(1e-9, (1.0 - test_share))
    for cid in remaining:
        bucket = _deterministic_bucket(cid, random_seed + 1)
        split_by_cluster[cid] = "validation" if bucket < validation_ratio_within_remaining else "train"

    return {cid: {"split": split_by_cluster[cid], "group_key": leakage_groups.get(cid, cid)}
             for cid in cluster_ids}


def assign_temporal_split(client, run_id: str, task: str, cluster_type: str,
                            cluster_ids: list, *, test_share: float = 0.2,
                            validation_share: float = 0.2) -> dict:
    """week_start昇順でtrain(古い)/validation(中間)/test(最新)に切る。時間境界を跨ぐ
    Leakage Groupは評価対象から除外する（excluded_due_to_temporal_leakage=true）。
    戻り値: {cluster_id: {"split": ..., "group_key": ..., "excluded_due_to_temporal_leakage": bool}}。"""
    col = _CLUSTER_ID_COLUMN[cluster_type]
    cluster_rows = _select_all_paginated(client, _CLUSTER_TABLE[cluster_type], {
        "select": f"{col},week_start", "run_id": f"eq.{run_id}",
    })
    week_by_cluster = {r[col]: (r.get("week_start") or "") for r in cluster_rows if r[col] in set(cluster_ids)}

    ordered = sorted(cluster_ids, key=lambda cid: (week_by_cluster.get(cid, ""), cid))
    n = len(ordered)
    n_test = max(1, round(n * test_share)) if n else 0
    n_validation = max(1, round(n * validation_share)) if n else 0
    n_train = n - n_test - n_validation

    split_by_cluster = {}
    for i, cid in enumerate(ordered):
        if i < n_train:
            split_by_cluster[cid] = "train"
        elif i < n_train + n_validation:
            split_by_cluster[cid] = "validation"
        else:
            split_by_cluster[cid] = "test"

    leakage_groups = build_leakage_groups(client, run_id, cluster_type, cluster_ids)
    splits_in_group = defaultdict(set)
    for cid, group_id in leakage_groups.items():
        splits_in_group[group_id].add(split_by_cluster[cid])

    result = {}
    for cid in cluster_ids:
        group_id = leakage_groups.get(cid, cid)
        straddles = len(splits_in_group[group_id]) > 1
        result[cid] = {
            "split": split_by_cluster[cid], "group_key": group_id,
            "excluded_due_to_temporal_leakage": straddles,
        }
    return result


def save_split_assignments(client, benchmark_run_id: str, run_id: str, task: str, cluster_type: str,
                             split_scheme: str, assignments: dict, *, item_key: str = "",
                             random_seed: int = 42) -> int:
    """assignments: {cluster_id: {"split":..., "group_key":..., "excluded_due_to_temporal_leakage":bool?}}
    をgeo_report_benchmark_split_assignmentsへ書き込む（delete-then-insertでidempotent）。"""
    client.delete("geo_report_benchmark_split_assignments", {
        "benchmark_run_id": f"eq.{benchmark_run_id}", "split_scheme": f"eq.{split_scheme}", "task": f"eq.{task}",
    })
    rows = [{
        "id": _new_id(), "benchmark_run_id": benchmark_run_id, "run_id": run_id, "task": task,
        "cluster_type": cluster_type, "cluster_id": cid, "item_key": item_key,
        "split_scheme": split_scheme, "split": info["split"], "group_key": info.get("group_key"),
        "excluded_due_to_temporal_leakage": info.get("excluded_due_to_temporal_leakage", False),
        "random_seed": random_seed,
    } for cid, info in assignments.items()]
    for chunk in _chunked(rows):
        client.insert("geo_report_benchmark_split_assignments", chunk, prefer="return=minimal")
    return len(rows)
