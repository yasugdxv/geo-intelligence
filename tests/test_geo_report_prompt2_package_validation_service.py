"""Prompt 2 base input packageの自動検証（geo_report_prompt2_package_validation_service）のテスト。

実fixtureでの根拠（2026-10-02のUS中間選挙/Evidence Intelligence）:
  record_cluster_map_001.csvが1,000行のみ（総レコード209,976/391,909件）、population/weekly/
  source_bucket_summaryも1,000件分から再集計されていたのに、ZIP生成は成功扱いだった。
  2026-09-16の過去Run（58,396件、全件入り）は同じ検証で通る。
"""
import csv
import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_current_pipeline_service as cp  # noqa: E402
import geo_report_prompt2_package_validation_service as pv  # noqa: E402
import geo_report_prompt_export_service as export_svc  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

REQUIRED = export_svc.PROMPT2_BASE_INPUT_FIXED_FILES
N_TOTAL, N_DUP, CHUNK = 120, 20, 50
N_NONDUP = N_TOTAL - N_DUP


def _csv_text(rows, columns):
    return cp.rows_to_csv_text(rows, columns)


def _record_row(i, *, dup=False, signal="S1", date_key="2026-05-05"):
    return {
        "record_id": f"r{i}", "timestamp": f"{date_key}T00:00:00Z", "date_key": date_key,
        "week_start": "2026-05-04" if date_key != "unknown" else "unknown", "week_end": "2026-05-10",
        "signal_source": signal, "theme_id": "T1", "theme_name": "Theme1", "trend_role": "primary",
        "trend_start_date": "2026-05-01", "source_file": "S1.csv", "source_bucket": "media",
        "source_class_preliminary": "Media", "reaction_type_hint": "", "attitude_hint": "unknown",
        "narrative_id": "N1", "narrative_name": "N1", "match_status": "matched", "matched_terms": "x",
        "cluster_cue": "general", "match_signature": "x",
        "analysis_cluster_id": "" if dup else "AC_1", "sentiment_cluster_id": "" if dup else "SC_1",
        "is_duplicate": dup, "duplicate_group_id": "text:abc" if dup else "", "url_hash": "", "text_hash": "",
    }


def make_package(*, chunk_size=CHUNK, contract="public_voice_monitor_v4_3", discovery=False):
    """矛盾の無い最小のPrompt 2 base input（files dict, manifest dict）を作る。"""
    records = [_record_row(i, dup=(i >= N_NONDUP)) for i in range(N_TOTAL)]
    chunks = [records[i:i + chunk_size] for i in range(0, len(records), chunk_size)]
    files = {}
    for n, chunk in enumerate(chunks, start=1):
        files[f"record_cluster_map_{n:03d}.csv"] = _csv_text(chunk, cp.RECORD_MAP_COLUMNS)
    evidence = contract.startswith("evidence_intelligence")
    files["all_clusters.csv"] = _csv_text([{"cluster_id": "AC_1", "eligible_records": N_NONDUP}],
                                           ["cluster_id", "eligible_records"])
    files["sentiment_cluster_representatives.csv"] = _csv_text(
        [{"cluster_id": "SC_1", "representative_no": 1}], ["cluster_id", "representative_no"])
    files["cluster_representatives.csv"] = "cluster_id\r\nAC_1\r\n"
    files["raw_reference_candidates.csv"] = files["sentiment_cluster_representatives.csv"]
    files["population_narrative_by_signal.csv"] = _csv_text(
        [{"signal_source": "S1", "narrative_id": "N1", "narrative_name": "N1", "records": N_NONDUP}],
        ["signal_source", "narrative_id", "narrative_name", "records"])
    files["population_narrative_by_signal_date.csv"] = _csv_text(
        [{"date": "2026-05-05", "signal_source": "S1", "narrative_id": "N1", "records": N_NONDUP}],
        ["date", "signal_source", "narrative_id", "records"])
    files["population_narrative_by_signal_language.csv"] = "signal_source,narrative_id,language_group,records\r\n"
    weekly = _csv_text([{"week_start": "2026-05-04", "records": N_NONDUP}], ["week_start", "records"])
    files["weekly_trend_raw_basis.csv"] = weekly
    files["timeline_basis.csv"] = weekly
    files["source_bucket_summary.csv"] = _csv_text(
        [{"signal_source": "S1", "source_bucket": "media", "source_class_preliminary": "Media",
          "nonduplicate_records": N_NONDUP}],
        ["signal_source", "source_bucket", "source_class_preliminary", "nonduplicate_records"])
    files["monitoring_scope.csv"] = _csv_text(
        [{"signal_source": "S1", "theme_id": "T1", "theme_name": "Theme1", "trend_role": "primary",
          "configured_start_date": "2026-05-01", "actual_min_date": "2026-05-05", "actual_max_date": "2026-05-05",
          "nonduplicate_records": N_NONDUP, "common_primary_graph_start": "2026-05-01",
          "effective_start_date": "2026-05-01", "effective_start_source": "master_trend_start_date"}],
        ["signal_source", "theme_id", "theme_name", "trend_role", "configured_start_date", "actual_min_date",
         "actual_max_date", "nonduplicate_records", "common_primary_graph_start", "effective_start_date",
         "effective_start_source"])
    title = "evidence_intelligence_report" if evidence else "public_opinion_report"
    rule = "not_applicable (evidence)" if evidence else "source_class=Public Voice; duplicate=false"
    files["report_config.yaml"] = (
        f"report_id: r1\nreport_title: {title}\ncreated_at: 2026-10-03\n"
        f"analysis_policy: narrative_master_v4_final_streaming_cluster_review\n"
        f"analysis_contract: {contract}\ncommon_primary_graph_start: 2026-05-01\n"
        f"primary_graph_signals: {'' if evidence else 'S1'}\nfinal_graph_rule: {rule}\nsignals:\n  - S1\n")
    files["README_prompt2.md"] = "readme"
    files["signal_narrative_master.csv"] = "signal_source,trend_role\r\nS1,primary\r\n"
    if discovery:
        files["discovery_candidates.csv"] = "discovery_cluster_id\r\nD1\r\n"
        files["discovery_candidate_representatives.csv"] = "discovery_cluster_id\r\nD1\r\n"
    manifest = {"total_records": str(N_TOTAL), "nonduplicate_records": str(N_NONDUP), "record_map_chunks": "1",
                "input_files": "S1.csv"}
    return files, manifest


def run_validation(files, manifest, *, contract="public_voice_monitor_v4_3", discovery_ids=()):
    return pv.validate_prompt2_base_files(
        files, required_files=REQUIRED, manifest=manifest, record_map_chunk_size=CHUNK,
        discovery_signal_ids=discovery_ids, expected_analysis_contract=contract)


def codes(issues, severity=pv.SEVERITY_ERROR):
    return {i["code"] for i in issues if i["severity"] == severity}


def test_consistent_package_has_no_errors():
    files, manifest = make_package()
    issues = run_validation(files, manifest)
    assert not pv.has_errors(issues), pv.format_issues(issues)
    # 既知: manifestのrecord_map_chunksはPreprocess時点で常に1（実際は3 chunk）
    assert "manifest_record_map_chunks_stale" in codes(issues, pv.SEVERITY_WARNING)


def test_required_files_are_checked_against_the_export_contract():
    files, manifest = make_package()
    del files["all_clusters.csv"]
    assert "missing_required_file" in codes(run_validation(files, manifest))


def test_truncated_record_map_is_detected_the_us_evidence_incident():
    """record mapが先頭1chunk(50行)しか無い状態（実fixtureでは1,000行/209,976件）。"""
    files, manifest = make_package()
    for name in ("record_cluster_map_002.csv", "record_cluster_map_003.csv"):
        del files[name]
    found = codes(run_validation(files, manifest))
    assert {"record_map_row_count_mismatch", "record_map_chunk_count_mismatch",
            "record_map_nonduplicate_mismatch"} <= found
    assert any(c.startswith("derived_count_mismatch:") for c in found)   # 派生CSVの合計とも食い違う


def test_missing_record_map_chunk_in_the_middle_is_detected():
    files, manifest = make_package()
    del files["record_cluster_map_002.csv"]
    assert "record_map_chunk_gap" in codes(run_validation(files, manifest))


def test_derived_csv_computed_from_truncated_records_is_detected():
    files, manifest = make_package()
    files["population_narrative_by_signal.csv"] = _csv_text(
        [{"signal_source": "S1", "narrative_id": "N1", "narrative_name": "N1", "records": 818}],
        ["signal_source", "narrative_id", "narrative_name", "records"])
    assert "derived_count_mismatch:population_narrative_by_signal.csv" in codes(run_validation(files, manifest))


def test_rows_from_a_previous_preprocess_execution_are_detected_as_stale():
    """実fixture(Evidence)では、record mapの標本1,000行のうち97行(9.7%)が、このRunの入力ではない
    旧ファイル(9/14のexport)由来だった。geo_report_recordsはrun_idでしか区別されず、同じRunで
    Preprocessをやり直すと旧実行の行が残るため、manifestのinput_filesに無いsource_fileを検出する。"""
    files, manifest = make_package()
    rows = list(csv.DictReader(io.StringIO(files["record_cluster_map_001.csv"])))
    for row in rows[:5]:
        row["source_file"] = "OLD_20260914.csv"
    files["record_cluster_map_001.csv"] = _csv_text(rows, cp.RECORD_MAP_COLUMNS)
    issues = run_validation(files, manifest)
    stale = [i for i in issues if i["code"] == "record_map_stale_source_file"]
    assert stale and "5件" in stale[0]["message"] and "OLD_20260914.csv" in stale[0]["message"]
    # input_filesが無いmanifest（旧形式）ではこの検査は行わない（誤検出しない）
    manifest_without_files = {k: v for k, v in manifest.items() if k != "input_files"}
    assert "record_map_stale_source_file" not in codes(run_validation(files, manifest_without_files))


def test_duplicate_flag_contract():
    files, manifest = make_package()
    text = files["record_cluster_map_003.csv"].replace("text:abc", "text:abc", 1)
    rows = list(csv.DictReader(io.StringIO(text)))
    rows[0]["analysis_cluster_id"] = "AC_1"                      # 重複行にclusterIDが入っている
    files["record_cluster_map_003.csv"] = _csv_text(rows, cp.RECORD_MAP_COLUMNS)
    assert "duplicate_row_has_cluster_id" in codes(run_validation(files, manifest))

    files, manifest = make_package()
    rows = list(csv.DictReader(io.StringIO(files["record_cluster_map_001.csv"])))
    rows[0]["sentiment_cluster_id"] = ""                         # 非重複行にclusterIDが無い
    files["record_cluster_map_001.csv"] = _csv_text(rows, cp.RECORD_MAP_COLUMNS)
    assert "nonduplicate_row_missing_cluster_id" in codes(run_validation(files, manifest))


def test_cluster_ids_in_record_map_must_exist_in_cluster_master():
    files, manifest = make_package()
    files["all_clusters.csv"] = _csv_text([{"cluster_id": "AC_OTHER", "eligible_records": N_NONDUP}],
                                           ["cluster_id", "eligible_records"])
    files["sentiment_cluster_representatives.csv"] = _csv_text(
        [{"cluster_id": "SC_OTHER", "representative_no": 1}], ["cluster_id", "representative_no"])
    found = codes(run_validation(files, manifest))
    assert {"record_map_unknown_analysis_cluster", "record_map_unknown_sentiment_cluster"} <= found


def test_duplicate_cluster_ids_in_master_are_detected():
    files, manifest = make_package()
    files["all_clusters.csv"] = _csv_text(
        [{"cluster_id": "AC_1", "eligible_records": 50}, {"cluster_id": "AC_1", "eligible_records": 50}],
        ["cluster_id", "eligible_records"])
    assert "duplicate_cluster_id" in codes(run_validation(files, manifest))


def test_monitoring_scope_completeness_and_start_date():
    files, manifest = make_package()
    files["report_config.yaml"] = files["report_config.yaml"].replace("  - S1\n", "  - S1\n  - S2\n")
    assert "monitoring_scope_missing_signal" in codes(run_validation(files, manifest))

    files, manifest = make_package(contract="evidence_intelligence_v1")
    rows = list(csv.DictReader(io.StringIO(files["monitoring_scope.csv"])))
    rows[0]["configured_start_date"] = ""
    rows[0]["effective_start_date"] = ""
    files["monitoring_scope.csv"] = _csv_text(rows, list(rows[0].keys()))
    assert "monitoring_scope_start_missing" in codes(run_validation(files, manifest, contract="evidence_intelligence_v1"))

    files, manifest = make_package(contract="evidence_intelligence_v1")
    rows = list(csv.DictReader(io.StringIO(files["monitoring_scope.csv"])))
    rows[0]["configured_start_date"] = ""                    # effective_start_dateで補完されている -> warningのみ
    files["monitoring_scope.csv"] = _csv_text(rows, list(rows[0].keys()))
    issues = run_validation(files, manifest, contract="evidence_intelligence_v1")
    assert "monitoring_scope_start_missing" not in codes(issues)
    assert "configured_start_date_empty" in codes(issues, pv.SEVERITY_WARNING)


def test_date_coverage_must_match_between_record_map_and_monitoring_scope():
    files, manifest = make_package()
    rows = list(csv.DictReader(io.StringIO(files["monitoring_scope.csv"])))
    rows[0]["actual_max_date"] = "2026-05-09"                # record mapの最新日(05-05)と異なる
    files["monitoring_scope.csv"] = _csv_text(rows, list(rows[0].keys()))
    assert "date_coverage_mismatch" in codes(run_validation(files, manifest))


def test_report_config_mode_consistency():
    # Evidence契約なのにPublic Voice向けのtitle/final_graph_rule
    files, manifest = make_package(contract="evidence_intelligence_v1")
    files["report_config.yaml"] = files["report_config.yaml"].replace(
        "report_title: evidence_intelligence_report", "report_title: public_opinion_report").replace(
        "final_graph_rule: not_applicable (evidence)", "final_graph_rule: source_class=Public Voice")
    found = codes(run_validation(files, manifest, contract="evidence_intelligence_v1"))
    assert {"mode_title_mismatch", "mode_graph_rule_mismatch"} <= found

    # 契約名そのものの食い違い
    files, manifest = make_package(contract="public_voice_monitor_v4_3")
    assert "analysis_contract_mismatch" in codes(run_validation(files, manifest, contract="evidence_intelligence_v1"))

    # 契約名が無い旧形式は警告（互換のためerrorにしない）
    files, manifest = make_package()
    files["report_config.yaml"] = files["report_config.yaml"].replace(
        "analysis_contract: public_voice_monitor_v4_3\n", "")
    issues = run_validation(files, manifest)
    assert not pv.has_errors(issues)
    assert "analysis_contract_missing" in codes(issues, pv.SEVERITY_WARNING)


def test_public_voice_with_empty_primary_graph_signals_but_primary_rows_is_a_warning():
    files, manifest = make_package()
    files["report_config.yaml"] = files["report_config.yaml"].replace("primary_graph_signals: S1", "primary_graph_signals: ")
    issues = run_validation(files, manifest)
    assert "primary_graph_signals_empty" in codes(issues, pv.SEVERITY_WARNING)
    assert not pv.has_errors(issues)


def test_discovery_conditional_files():
    files, manifest = make_package()
    assert "missing_discovery_file" in codes(run_validation(files, manifest, discovery_ids=["S6"]))
    files, manifest = make_package(discovery=True)
    assert not pv.has_errors(run_validation(files, manifest, discovery_ids=["S6"]))
    assert "unexpected_discovery_files" in codes(run_validation(files, manifest), pv.SEVERITY_WARNING)


def test_expected_total_unknown_degrades_to_warning_not_a_false_pass_silently():
    files, _ = make_package()
    issues = run_validation(files, {})
    assert "expected_total_unknown" in codes(issues, pv.SEVERITY_WARNING)


def test_zip_integrity():
    issues, files = pv.validate_zip_integrity(b"not a zip")
    assert "zip_corrupt" in codes(issues)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.csv", "x")
    issues, files = pv.validate_zip_integrity(buf.getvalue())
    assert not issues and files == {"a.csv": b"x"}


# ---------------------------------------------------------------------------
# build_prompt2_base_input_zip への組み込み（統合）
# ---------------------------------------------------------------------------

def _seed_realistic_run(client, *, n_records=N_TOTAL, contract="public_voice_monitor_v4_3"):
    """build_prompt2_base_input_zipが読むDB/Artifactを、矛盾の無い内容で用意する。"""
    from tests.test_geo_report_prompt_export_service import _seed_run_and_assets
    _seed_run_and_assets(client, prompt1_contract_version="v4.3")
    files, manifest = make_package(chunk_size=10**9, contract=contract)
    manifest_csv = _csv_text([{"total_records": N_TOTAL, "nonduplicate_records": N_NONDUP, "record_map_chunks": 1,
                                "input_files": "S1.csv"}],
                              ["total_records", "nonduplicate_records", "record_map_chunks", "input_files"])
    artifacts = {
        "cluster_labeling_input.csv": files["all_clusters.csv"],
        "cluster_representatives.csv": files["cluster_representatives.csv"],
        "sentiment_cluster_representatives.csv": files["sentiment_cluster_representatives.csv"],
        "monitoring_scope.csv": files["monitoring_scope.csv"],
        "report_config.yaml": files["report_config.yaml"],
        "cluster_manifest.csv": manifest_csv,
    }
    for name, text in artifacts.items():
        client.insert("geo_report_run_artifacts", [{
            "id": f"art-{name}", "run_id": "run-1", "execution_id": None, "execution_method": "system_snapshot",
            "artifact_type": "prompt1_input", "file_name": name, "content_text": text, "sha256": "x",
            "size_bytes": len(text), "media_type": "text/csv"}])
    db_rows = [{"id": f"rec-{i:06d}", "run_id": "run-1", "signal_id": r["signal_source"],
                "timestamp_raw": r["timestamp"], **{k: v for k, v in r.items()
                                                    if k not in ("signal_source", "timestamp")}}
               for i, r in enumerate(_record_row(i, dup=(i >= N_NONDUP)) for i in range(n_records))]
    client.tables.setdefault("geo_report_records", []).extend(db_rows)


@pytest.fixture
def validation_on(monkeypatch):
    monkeypatch.setattr(export_svc, "PROMPT2_BASE_VALIDATION_ENABLED", True)


def test_fetch_all_records_is_not_truncated_by_server_side_row_cap():
    """回帰: PostgREST(db-max-rows=1000)相当のFakeでも全件取得できる（修正前は1,000件で打ち切り）。"""
    client = FakeSupabaseClient(max_rows=1000)
    client.tables["geo_report_records"] = [
        {"id": f"rec-{i:06d}", "run_id": "run-1", "record_id": f"r{i}"} for i in range(2500)]
    records = export_svc._fetch_all_records(client, "run-1")
    assert len(records) == 2500
    assert len({r["id"] for r in records}) == 2500


def test_build_prompt2_base_zip_with_server_row_cap_produces_complete_valid_package(validation_on, monkeypatch):
    client = FakeSupabaseClient(max_rows=1000)
    _seed_realistic_run(client)
    monkeypatch.setattr(export_svc, "RECORD_MAP_CHUNK_SIZE", CHUNK)
    monkeypatch.setattr(export_svc, "build_record_cluster_map_chunks",
                        lambda records, chunk_size=CHUNK: _chunks(records))
    zip_bytes = export_svc.build_prompt2_base_input_zip(client, "run-1")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = sorted(n for n in zf.namelist() if n.startswith("record_cluster_map_"))
        assert names == ["record_cluster_map_001.csv", "record_cluster_map_002.csv", "record_cluster_map_003.csv"]
        rows = sum(len(list(csv.DictReader(io.StringIO(zf.read(n).decode("utf-8"))))) for n in names)
    assert rows == N_TOTAL


def _chunks(records):
    """chunk_size=CHUNKでrecord mapを分割する（本物のbuild_record_cluster_map_chunksのchunk_sizeだけ差し替え）。"""
    rows = []
    for r in records:
        rows.append({csv_col: r.get(db_col, "") for db_col, csv_col in export_svc._RECORD_MAP_DB_TO_CSV.items()})
    chunks = [rows[i:i + CHUNK] for i in range(0, len(rows), CHUNK)] or [[]]
    return [_csv_text(c, cp.RECORD_MAP_COLUMNS) for c in chunks]


def test_build_prompt2_base_zip_fails_when_record_map_is_truncated(validation_on, monkeypatch):
    """取得が打ち切られた場合（今回のインシデントの再現）、ZIPを返さずValueErrorにする。"""
    client = FakeSupabaseClient()
    _seed_realistic_run(client)
    full = export_svc._fetch_all_records

    monkeypatch.setattr(export_svc, "_fetch_all_records", lambda c, r: full(c, r)[:1000 // 20])
    monkeypatch.setattr(export_svc, "build_record_cluster_map_chunks", lambda records, chunk_size=CHUNK: _chunks(records))
    with pytest.raises(ValueError) as exc:
        export_svc.build_prompt2_base_input_zip(client, "run-1")
    assert "record_map_row_count_mismatch" in str(exc.value)


def test_missing_keyword_matcher_version_is_a_warning_for_old_packages():
    files, manifest = make_package()
    assert "keyword_matcher_missing" in codes(run_validation(files, manifest), pv.SEVERITY_WARNING)
    files["report_config.yaml"] += "keyword_matcher: keyword_matcher_v2\n"
    assert "keyword_matcher_missing" not in codes(run_validation(files, manifest), pv.SEVERITY_WARNING)
