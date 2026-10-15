import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_processing_service as svc  # noqa: E402


# ---------------------------------------------------------------------------
# 低レベルユーティリティ（run.ps1の各関数とのShell Parity単体確認）
# ---------------------------------------------------------------------------

def test_normalize_signal_key_variants():
    assert svc.normalize_signal_key("S01") == "S1"
    assert svc.normalize_signal_key("s2a") == "S2A"
    assert svc.normalize_signal_key("003") == "S3"
    assert svc.normalize_signal_key("s10") == "S10"
    assert svc.normalize_signal_key("garbage") == "GARBAGE"


def test_detect_signal_from_file_variants():
    assert svc.detect_signal_from_file("S2A_something.csv") == "S2A"
    assert svc.detect_signal_from_file("S2Coffee.csv") == "S2"
    assert svc.detect_signal_from_file("signal_04_export.csv") == "S4"
    assert svc.detect_signal_from_file("no_signal_here.csv") == "UNMAPPED"
    assert svc.detect_signal_from_file(
        "イラン_S1各国政府の受け止め_20260831.csv"
    ) == "S1"


def test_get_source_bucket_priority_order():
    assert svc.get_source_bucket("https://x.com/foo/status/1", "TwitterSocialMedia", "x") == "public_social"
    assert svc.get_source_bucket("https://gov.example.go.jp/press", "News", "Ministry") == "official"
    assert svc.get_source_bucket("https://example.com/article", "News", "Example Times") == "media"
    assert svc.get_source_bucket("https://example.com/blog/post", "Blog", "Example Blog") == "public_blog"
    assert svc.get_source_bucket("https://prtimes.jp/x", "News", "PR TIMES") == "pr_release"
    assert svc.get_source_bucket("", "", "") == "unknown"


def test_get_dedup_url_key_normalizes_and_strips_home():
    assert svc.get_dedup_url_key("https://Example.com/Article/1?utm=x") == "https://example.com/article/1?utm=x"
    assert svc.get_dedup_url_key("https://example.com/") == ""
    assert svc.get_dedup_url_key("https://example.com/search") == ""
    assert svc.get_dedup_url_key("") == ""


def test_get_split_label_matches_first_hit():
    rules = "label_a=term1~term2;label_b=term3"
    assert svc.get_split_label("this mentions term3 only", rules) == "label_b"
    assert svc.get_split_label("this mentions term1", rules) == "label_a"
    assert svc.get_split_label("no match here", rules) == "general"
    assert svc.get_split_label("anything", "") == "general"


def test_get_match_signature():
    assert svc.get_match_signature("", "fallback") == "fallback"
    assert svc.get_match_signature("", "unmapped") == "unmapped"
    assert svc.get_match_signature("zzz|aaa|bbb|ccc", "matched") == "aaa+bbb+ccc"
    assert svc.get_match_signature("", "matched") == "matched_general"


def test_get_priority_weight():
    assert svc.get_priority_weight("High") == 30
    assert svc.get_priority_weight("medium") == 20
    assert svc.get_priority_weight("Low") == 10
    assert svc.get_priority_weight("") == 10


def test_get_time_bucket_granularities():
    from datetime import datetime, timezone
    date = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)  # Thursday
    assert svc.get_time_bucket(date, "day") == "2026-08-27"
    assert svc.get_time_bucket(date, "month") == "2026-08"
    assert svc.get_time_bucket(date, "none") == "all"
    assert svc.get_time_bucket(date, "week") == "2026-08-24"  # 直近月曜
    assert svc.get_time_bucket(None, "day") == "unknown"


# ---------------------------------------------------------------------------
# Narrative Master → Rule 構築
# ---------------------------------------------------------------------------

def _master_row(signal_id, narrative_id, *, include="", required="", exclude="",
                 is_fallback="false", active="true", report_priority="Medium",
                 dimension_config_json=None):
    if dimension_config_json is None:
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
        "narrative_description": "desc", "include_keywords": include, "required_context": required,
        "exclude_keywords": exclude, "cluster_split_rules": "", "cluster_time_granularity": "day",
        "report_priority": report_priority, "report_section": "Key Findings", "report_order": "1",
        "continuity_role": "Watch", "max_cluster_records": "", "representative_count": "",
        "is_fallback": is_fallback, "active": active,
        "dimension_config_json": dimension_config_json,
    }


def test_build_master_rules_requires_fallback_per_signal():
    rows = [_master_row("S1", "S1-N01", include="alpha")]
    try:
        svc.build_master_rules(rows)
        assert False, "expected missing-fallback error"
    except ValueError as exc:
        assert "No enabled fallback" in str(exc)


# ---------------------------------------------------------------------------
# Phase R1 Human Check指摘（Iran/Hormuz Calibration実データで発見）:
# dimension_config_jsonのpreflight検証
# ---------------------------------------------------------------------------

def test_build_master_rules_accepts_missing_dimension_config_json():
    """dimension_config_jsonを持たないNarrativeはもう投入時点のエラーにしない
    （Babel Street等の外部ツール由来のNarrative Masterがこの暗黙列を持たなくても
    投入できるようにするための変更）。geo_report_classification_service.
    get_dimension_configが自動的にDEFAULT_DIMENSION_CONFIGへフォールバックする。"""
    rows = [
        _master_row("S1", "S1-N01", include="alpha", dimension_config_json=""),
        _master_row("S1", "S1-N99", is_fallback="true"),
    ]
    rules_by_signal, fallback_by_signal, signal_alias = svc.build_master_rules(rows)
    assert set(signal_alias) == {"S1"}


def test_build_master_rules_rejects_invalid_json_dimension_config():
    rows = [
        _master_row("S1", "S1-N01", include="alpha", dimension_config_json="{not valid json"),
        _master_row("S1", "S1-N99", is_fallback="true"),
    ]
    try:
        svc.build_master_rules(rows)
        assert False, "expected dimension_config_json preflight error"
    except ValueError as exc:
        assert "S1-N01" in str(exc)


def test_build_master_rules_does_not_require_dimension_config_json_for_fallback():
    """fallback Narrativeはdimension_config_jsonが空（{}）でよい——分類対象では
    ないため。"""
    rows = [
        _master_row("S1", "S1-N01", include="alpha"),
        _master_row("S1", "S1-N99", is_fallback="true", dimension_config_json="{}"),
    ]
    rules_by_signal, fallback_by_signal, signal_alias = svc.build_master_rules(rows)
    assert set(signal_alias) == {"S1"}


def test_build_master_rules_accepts_valid_dimension_config_json():
    rows = [
        _master_row("S1", "S1-N01", include="alpha"),
        _master_row("S1", "S1-N99", is_fallback="true"),
    ]
    rules_by_signal, fallback_by_signal, signal_alias = svc.build_master_rules(rows)
    assert set(signal_alias) == {"S1"}


def test_build_master_rules_and_match_master():
    rows = [
        _master_row("S1", "S1-N01", include="alpha;beta"),
        _master_row("S1", "S1-N02", include="gamma", required="delta"),
        _master_row("S1", "S1-N99", is_fallback="true"),
    ]
    rules_by_signal, fallback_by_signal, signal_alias = svc.build_master_rules(rows)
    assert set(signal_alias) == {"S1"}

    matched = svc.match_master(rules_by_signal["S1"], fallback_by_signal["S1"], "text mentions alpha here")
    assert matched["match_status"] == "matched"
    assert matched["rule"].narrative_id == "S1-N01"

    unmatched_required = svc.match_master(rules_by_signal["S1"], fallback_by_signal["S1"], "text mentions gamma only")
    assert unmatched_required["match_status"] == "fallback"

    fallback_hit = svc.match_master(rules_by_signal["S1"], fallback_by_signal["S1"], "nothing relevant here")
    assert fallback_hit["match_status"] == "fallback"
    assert fallback_hit["rule"].narrative_id == "S1-N99"


def test_build_master_rules_exclude_wins_over_include():
    rows = [
        _master_row("S1", "S1-N01", include="alpha", exclude="notrelevant"),
        _master_row("S1", "S1-N99", is_fallback="true"),
    ]
    rules_by_signal, fallback_by_signal, _ = svc.build_master_rules(rows)
    result = svc.match_master(rules_by_signal["S1"], fallback_by_signal["S1"], "alpha but notrelevant context")
    assert result["match_status"] == "fallback"


# ---------------------------------------------------------------------------
# run_preprocess end-to-end（小規模フィクスチャ、Shell主要ロジックの結合確認）
# ---------------------------------------------------------------------------

_INPUT_FIELDS = [
    "documentId", "timestamp", "documentName", "documentText", "sourceUrl",
    "languageDisplayName", "documentTypeName", "authorName", "authorPlaceName",
]


def _write_input_csv(path: Path, rows: list):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_INPUT_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def test_run_preprocess_end_to_end(tmp_path):
    master_rows = [
        _master_row("S1", "S1-N01", include="alpha"),
        _master_row("S1", "S1-N99", is_fallback="true"),
    ]

    rows = [
        {"documentId": "r1", "timestamp": "2026-08-10T00:00:00Z", "documentName": "Alpha news",
         "documentText": "Report about alpha situation.", "sourceUrl": "https://news.example.com/alpha-1",
         "languageDisplayName": "English", "documentTypeName": "News",
         "authorName": "", "authorPlaceName": "US"},
        # 同一URL・同一signalの重複（mediaはURL dedup対象）
        {"documentId": "r2", "timestamp": "2026-08-10T01:00:00Z", "documentName": "Alpha news copy",
         "documentText": "Different text but same URL.", "sourceUrl": "https://news.example.com/alpha-1",
         "languageDisplayName": "English", "documentTypeName": "News",
         "authorName": "", "authorPlaceName": "US"},
        # 期間外
        {"documentId": "r3", "timestamp": "2026-01-01T00:00:00Z", "documentName": "Old alpha",
         "documentText": "alpha but old", "sourceUrl": "https://news.example.com/alpha-old",
         "languageDisplayName": "English", "documentTypeName": "News",
         "authorName": "", "authorPlaceName": "US"},
        # fallback行き（includeキーワード無し）
        {"documentId": "r4", "timestamp": "2026-08-11T00:00:00Z", "documentName": "Unrelated",
         "documentText": "nothing about the topic", "sourceUrl": "https://news.example.com/other",
         "languageDisplayName": "English", "documentTypeName": "News",
         "authorName": "", "authorPlaceName": "JP"},
    ]
    input_path = tmp_path / "S1_sample.csv"
    _write_input_csv(input_path, rows)

    result = svc.run_preprocess(
        [str(input_path)], master_rows, start_date="2026-08-01", end_date="2026-08-31",
    )

    assert result.total_records == 4
    assert result.in_scope_total == 3  # r3は期間外
    assert result.nonduplicate_total == 2  # r2はr1とURL重複
    assert result.signal_matched.get("S1") == 1  # r1のみalphaにmatch
    assert result.signal_fallback.get("S1") == 1  # r4がfallback

    cluster_rows = svc.build_cluster_rows(result.clusters)
    assert len(cluster_rows) >= 1
    matched_clusters = [c for c in cluster_rows if c["narrative_id"] == "S1-N01"]
    assert matched_clusters and matched_clusters[0]["cluster_records"] == 1

    audit_rows = svc.build_preprocess_audit_rows(result, {"S1": []})
    s1_audit = next(r for r in audit_rows if r["signal_id"] == "S1")
    assert s1_audit["total_records"] == 4
    assert s1_audit["nonduplicate_records"] == 2
    assert s1_audit["qa_status"] == "PASS"


def test_run_preprocess_preflight_rejects_unmapped_filename(tmp_path):
    master_rows = [
        _master_row("S1", "S1-N01", include="alpha"),
        _master_row("S1", "S1-N99", is_fallback="true"),
    ]
    input_path = tmp_path / "no_signal_here.csv"
    _write_input_csv(input_path, [
        {"documentId": "r1", "timestamp": "2026-08-10T00:00:00Z", "documentName": "x",
         "documentText": "alpha", "sourceUrl": "https://news.example.com/x",
         "languageDisplayName": "English", "documentTypeName": "News", "authorName": "", "authorPlaceName": "US"},
    ])
    try:
        svc.run_preprocess([str(input_path)], master_rows)
        assert False, "expected preflight failure"
    except ValueError as exc:
        assert "Signal preflight failed" in str(exc)


def test_run_preprocess_social_dedup_uses_text_hash_not_url(tmp_path):
    master_rows = [
        _master_row("S1", "S1-N01", include="alpha"),
        _master_row("S1", "S1-N99", is_fallback="true"),
    ]
    rows = [
        {"documentId": "s1", "timestamp": "2026-08-10T00:00:00Z", "documentName": "",
         "documentText": "alpha situation update from social", "sourceUrl": "https://x.com/user/status/1",
         "languageDisplayName": "English", "documentTypeName": "TwitterSocialMedia",
         "authorName": "user1", "authorPlaceName": "US"},
        # 別URL・同一本文 → public_socialはtext hash dedup対象なので重複扱いになるはず
        {"documentId": "s2", "timestamp": "2026-08-10T01:00:00Z", "documentName": "",
         "documentText": "alpha situation update from social", "sourceUrl": "https://x.com/user2/status/2",
         "languageDisplayName": "English", "documentTypeName": "TwitterSocialMedia",
         "authorName": "user2", "authorPlaceName": "US"},
    ]
    input_path = tmp_path / "S1_social.csv"
    _write_input_csv(input_path, rows)

    result = svc.run_preprocess([str(input_path)], master_rows, start_date="2026-08-01", end_date="2026-08-31")
    assert result.nonduplicate_total == 1
