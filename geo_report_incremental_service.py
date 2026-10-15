"""Report Studio Phase4: Incremental Evidence Processing。

前回run（baseline）と今回の新規入力を比較し、クラスタのbase_key
（signal|narrative_id|time_bucket|source_bucket|split_label|language|match_signature、
run_preprocess内部で使われる決定的な組み立てキー）とrecord_id集合が完全一致する
クラスタのみ、既存classificationを再利用する。

**メンバーシップが変わったクラスタ（新規記録の合流・一部入れ替わり等）は、
件数が近いといった理由で安易にreuseせず、必ず再分類対象に回す**（ユーザー要件）。
判定はrecord_id集合の完全一致のみで行い、ヒューリスティックは使わない。

base_key・membership情報は新規カラムを持たず、既存geo_report_records
（run_preprocessが決定的に書き込む列のみ）から再構築する
（geo_report_processing_service.get_match_signature等、既存の決定的関数を再利用）。
"""

from __future__ import annotations

import geo_report_processing_service as processing


def base_key_for_record_row(record_row: dict) -> str:
    """geo_report_recordsの1行から、run_preprocessが実際に使ったbase_keyを再構築する。
    match_signatureはmatched_terms/match_statusから決定的に再計算できるため、
    新規カラムを追加せず既存フィールドだけで再現する（processing.get_match_signature
    と全く同じロジック、二重実装しない）。"""
    match_signature = processing.get_match_signature(
        record_row.get("matched_terms") or "", record_row.get("match_status") or "")
    return "|".join([
        record_row.get("signal_id") or "",
        record_row.get("narrative_id") or "",
        record_row.get("time_bucket") or "",
        record_row.get("source_bucket") or "",
        record_row.get("split_label") or "",
        record_row.get("language") or "",
        match_signature,
    ])


def build_baseline_membership(baseline_records: list) -> dict:
    """{base_key: frozenset(record_id)}。run_preprocessが実際にクラスタへ算入した条件
    （in_scope かつ 非重複）と同じ条件のレコードのみ対象にする。"""
    membership: dict = {}
    for r in baseline_records:
        if not r.get("period_in_scope") or r.get("is_duplicate"):
            continue
        key = base_key_for_record_row(r)
        membership.setdefault(key, set()).add(r.get("record_id"))
    return {k: frozenset(v) for k, v in membership.items()}


def derive_membership_by_cluster_id(records: list) -> dict:
    """{cluster_id: (base_key, frozenset(record_id))}。in_scope かつ 非重複のレコードのみ。
    永続化済みのgeo_report_clustersはbase_key/record_ids列を持たない（新規カラムを
    増やさない設計）ため、cluster_id単位のmembershipをrecord側から決定的に再構築する。"""
    grouped: dict = {}
    for r in records:
        cluster_id = r.get("cluster_id")
        if not cluster_id or not r.get("period_in_scope") or r.get("is_duplicate"):
            continue
        entry = grouped.setdefault(cluster_id, {"base_key": base_key_for_record_row(r), "record_ids": set()})
        entry["record_ids"].add(r.get("record_id"))
    return {cid: (v["base_key"], frozenset(v["record_ids"])) for cid, v in grouped.items()}


def build_baseline_classification_by_base_key(baseline_records: list, baseline_clusters: list) -> dict:
    """{base_key: (cluster_row, classification)}。同じbase_keyのクラスタが複数run内に
    存在することは無い前提（1 run内ではbase_key→shard付きcluster_idは1:Nだが、
    通常の運用サイズではshardingが発生しないため実務上1:1として扱う。shardingが
    発生し複数cluster_idが同一base_keyを持つ場合は、record_count最大のものを採用する
    ——安全側に倒す設計で、誤って小さい方のクラスタのclassificationを全体へ適用しない）。"""
    membership_by_cluster_id = derive_membership_by_cluster_id(baseline_records)
    clusters_by_id = {c["cluster_id"]: c for c in baseline_clusters}

    by_base_key: dict = {}
    for cluster_id, (base_key, _members) in membership_by_cluster_id.items():
        cluster = clusters_by_id.get(cluster_id)
        if cluster is None or not base_key:
            continue
        existing = by_base_key.get(base_key)
        if existing is None or cluster.get("record_count", 0) > existing.get("record_count", 0):
            by_base_key[base_key] = cluster
    return {base_key: (cluster, cluster.get("classification")) for base_key, cluster in by_base_key.items()}


def classify_reuse_plan(new_cluster_rows: list, baseline_records: list, baseline_clusters: list) -> dict:
    """{new_cluster_id: {"action": "reuse"|"reprocess", "reused_from_cluster_id": str|None,
    "classification": dict|None}}。

    reuseの条件（両方満たす場合のみ）:
      1. base_keyがbaselineの同一base_keyと一致する
      2. record_id集合が完全一致する（1件でも増減・入れ替わりがあれば不一致）
    """
    baseline_membership = build_baseline_membership(baseline_records)
    baseline_by_key = build_baseline_classification_by_base_key(baseline_records, baseline_clusters)

    plan = {}
    for cluster in new_cluster_rows:
        base_key = cluster["base_key"]
        new_members = frozenset(cluster["record_ids"])
        baseline_members = baseline_membership.get(base_key)
        baseline_entry = baseline_by_key.get(base_key)

        if (baseline_members is not None and baseline_members == new_members
                and baseline_entry is not None and baseline_entry[1] is not None):
            baseline_cluster_row, classification = baseline_entry
            plan[cluster["cluster_id"]] = {
                "action": "reuse",
                "reused_from_cluster_id": baseline_cluster_row["cluster_id"],
                "classification": classification,
            }
        else:
            plan[cluster["cluster_id"]] = {
                "action": "reprocess", "reused_from_cluster_id": None, "classification": None,
            }
    return plan


def summarize_reuse_plan(plan: dict) -> dict:
    reused = sum(1 for v in plan.values() if v["action"] == "reuse")
    reprocess = sum(1 for v in plan.values() if v["action"] == "reprocess")
    return {"total_clusters": len(plan), "reused": reused, "reprocess": reprocess}
