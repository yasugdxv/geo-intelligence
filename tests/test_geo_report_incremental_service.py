import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_incremental_service as incr  # noqa: E402
import geo_report_processing_service as processing  # noqa: E402


def _master_row(signal_id, narrative_id, *, include="", is_fallback="false"):
    # Phase R1 Human Check指摘（Iran/Hormuz Calibration実データで発見）:
    # dimension_config_jsonはfallback以外のNarrativeで必須（build_master_rulesが
    # preflightで検証する）。テスト用の最小限の分類軸を既定値として持たせる。
    dimension_config_json = "{}" if is_fallback == "true" else json.dumps({
        "dummy_dimension": {"type": "single", "label": "dummy", "definition": "test",
                             "evidence_rule": "test", "allowed_values": ["a", "unknown"],
                             "unknown_value": "unknown"},
    })
    return {
        "signal_id": signal_id, "signal_name_internal": f"{signal_id} name",
        "signal_order": "1", "signal_role": "Core", "report_topic_label": f"{signal_id} topic",
        "narrative_id": narrative_id, "narrative_name": f"{narrative_id} name",
        "narrative_description": "desc", "include_keywords": include, "required_context": "",
        "exclude_keywords": "", "cluster_split_rules": "", "cluster_time_granularity": "day",
        "report_priority": "Medium", "report_section": "Key Findings", "report_order": "1",
        "continuity_role": "Watch", "max_cluster_records": "", "representative_count": "",
        "is_fallback": is_fallback, "active": "true",
        "dimension_config_json": dimension_config_json,
    }


MASTER_ROWS = [
    _master_row("S1", "S1-N01", include="alpha"),
    _master_row("S1", "S1-N99", is_fallback="true"),
]

_FIELDS = ["documentId", "timestamp", "documentName", "documentText", "sourceUrl",
           "languageDisplayName", "documentTypeName", "authorName", "authorPlaceName"]


def _write_csv(path: Path, rows: list):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _row(doc_id, day, text=None):
    # 本文をrecord_idごとに変える（同一本文だとtext_hashベースの重複排除に引っかかり、
    # 意図せず別recordが弾かれてmembershipテストが崩れるため）。
    text = text or f"alpha situation report regarding {doc_id}"
    return {"documentId": doc_id, "timestamp": f"2026-08-{day:02d}T00:00:00Z",
            "documentName": "Alpha", "documentText": text, "sourceUrl": f"https://news.example.com/{doc_id}",
            "languageDisplayName": "English", "documentTypeName": "News", "authorName": "", "authorPlaceName": "US"}


def _run(tmp_path, rows, filename="S1_x.csv"):
    p = tmp_path / filename
    _write_csv(p, rows)
    result = processing.run_preprocess([str(p)], MASTER_ROWS, start_date="2026-08-01", end_date="2026-08-31")
    cluster_rows = processing.build_cluster_rows(result.clusters)
    return result, cluster_rows


def _classify(cluster_rows):
    """テスト用の簡易classification（実LLMは呼ばない、reuse判定ロジックの検証が目的）。"""
    for c in cluster_rows:
        c["classification"] = {"result": {"dimensions": {}}, "validity_status": "PASS"}
    return cluster_rows


def test_base_key_for_record_row_matches_actual_clustering(tmp_path):
    _, cluster_rows = _run(tmp_path, [_row("r1", 10)])
    record_rows_by_id = {}
    # run_preprocessのrecord_rowsはPreprocessResultにあるが_run()はcluster_rowsしか返さないため再取得
    result, _ = _run(tmp_path, [_row("r1", 10)], filename="S1_y.csv")
    record_row = next(r for r in result.record_rows if r["record_id"] == "r1")
    cluster = cluster_rows[0]
    assert incr.base_key_for_record_row(record_row) == cluster["base_key"]


def test_build_baseline_membership_excludes_duplicates_and_out_of_scope():
    records = [
        {"record_id": "r1", "signal_id": "S1", "narrative_id": "S1-N01", "time_bucket": "2026-08-10",
         "source_bucket": "media", "split_label": "general", "language": "English",
         "matched_terms": "alpha", "match_status": "matched", "period_in_scope": True, "is_duplicate": False},
        {"record_id": "r2", "signal_id": "S1", "narrative_id": "S1-N01", "time_bucket": "2026-08-10",
         "source_bucket": "media", "split_label": "general", "language": "English",
         "matched_terms": "alpha", "match_status": "matched", "period_in_scope": True, "is_duplicate": True},
        {"record_id": "r3", "signal_id": "S1", "narrative_id": "S1-N01", "time_bucket": "2026-08-10",
         "source_bucket": "media", "split_label": "general", "language": "English",
         "matched_terms": "alpha", "match_status": "matched", "period_in_scope": False, "is_duplicate": False},
    ]
    membership = incr.build_baseline_membership(records)
    assert len(membership) == 1
    (only_key, only_members), = membership.items()
    assert only_members == frozenset({"r1"})


def test_reuse_plan_exact_membership_match_reuses(tmp_path):
    baseline_result, baseline_clusters = _run(tmp_path, [_row("r1", 10), _row("r2", 10)], filename="S1_base.csv")
    baseline_clusters = _classify(baseline_clusters)

    new_result, new_clusters = _run(tmp_path, [_row("r1", 10), _row("r2", 10)], filename="S1_t1.csv")

    plan = incr.classify_reuse_plan(new_clusters, baseline_result.record_rows, baseline_clusters)
    assert all(v["action"] == "reuse" for v in plan.values())
    summary = incr.summarize_reuse_plan(plan)
    assert summary["reused"] == summary["total_clusters"]
    assert summary["reprocess"] == 0


def test_reuse_plan_new_member_forces_reprocess(tmp_path):
    baseline_result, baseline_clusters = _run(tmp_path, [_row("r1", 10)], filename="S1_base.csv")
    baseline_clusters = _classify(baseline_clusters)

    # 同じbase_key（同日・同narrative）にr2が新規合流 → membership変化
    new_result, new_clusters = _run(tmp_path, [_row("r1", 10), _row("r2", 10)], filename="S1_t1.csv")

    plan = incr.classify_reuse_plan(new_clusters, baseline_result.record_rows, baseline_clusters)
    assert all(v["action"] == "reprocess" for v in plan.values()), (
        "メンバーシップが変わったクラスタは件数が近くてもreuseしてはいけない")


def test_reuse_plan_member_removed_forces_reprocess(tmp_path):
    baseline_result, baseline_clusters = _run(tmp_path, [_row("r1", 10), _row("r2", 10)], filename="S1_base.csv")
    baseline_clusters = _classify(baseline_clusters)

    new_result, new_clusters = _run(tmp_path, [_row("r1", 10)], filename="S1_t1.csv")

    plan = incr.classify_reuse_plan(new_clusters, baseline_result.record_rows, baseline_clusters)
    assert all(v["action"] == "reprocess" for v in plan.values())


def test_reuse_plan_brand_new_cluster_reprocesses(tmp_path):
    baseline_result, baseline_clusters = _run(tmp_path, [_row("r1", 10)], filename="S1_base.csv")
    baseline_clusters = _classify(baseline_clusters)

    # 全く新しい日付（新base_key）の記録のみ → baselineに対応クラスタが無い
    new_result, new_clusters = _run(tmp_path, [_row("r9", 20)], filename="S1_t1.csv")

    plan = incr.classify_reuse_plan(new_clusters, baseline_result.record_rows, baseline_clusters)
    assert all(v["action"] == "reprocess" for v in plan.values())
    assert all(v["reused_from_cluster_id"] is None for v in plan.values())


def test_reuse_plan_copies_baseline_classification_content(tmp_path):
    baseline_result, baseline_clusters = _run(tmp_path, [_row("r1", 10)], filename="S1_base.csv")
    baseline_clusters = _classify(baseline_clusters)
    baseline_clusters[0]["classification"]["result"]["dimensions"] = {"foo": {"value": "bar"}}

    new_result, new_clusters = _run(tmp_path, [_row("r1", 10)], filename="S1_t1.csv")
    plan = incr.classify_reuse_plan(new_clusters, baseline_result.record_rows, baseline_clusters)

    reused_entry = next(v for v in plan.values() if v["action"] == "reuse")
    assert reused_entry["classification"]["result"]["dimensions"] == {"foo": {"value": "bar"}}
