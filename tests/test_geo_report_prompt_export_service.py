import csv
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_prompt_export_service as export_svc  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


@pytest.fixture(autouse=True)
def _disable_prompt2_base_validation_by_default(monkeypatch):
    """このファイルの既存テストは、discovery provenance・Canonical Execution選択等の別の振る舞いを
    確認するため、中身がダミー("dummy content for ...")のartifactでPrompt 2 base ZIPを組み立てる。
    ZIP生成時の自動検証（2026-10-03追加、geo_report_prompt2_package_validation_service）は
    本物のCSV構造を要求するため、ここでは既定で無効にする。検証自体のテスト
    （末尾の「Prompt 2 base package validation」節）は個別にTrueへ戻す。"""
    monkeypatch.setattr(export_svc, "PROMPT2_BASE_VALIDATION_ENABLED", False)


def _narrative_master_row(**overrides):
    row = {
        "signal_id": "S1", "signal_name_internal": "", "report_topic_label": "Arizona Governor",
        "narrative_id": "N1", "narrative_name": "N1", "required_context": "", "tone_hint": "",
        "public_voice_hint": "", "report_use_hint": "", "continuity_hint": "", "notes": "",
        "signal_source": "S1", "signal_name": "Arizona Governor", "theme_id": "T1", "theme_name": "Theme1",
        "trend_role": "primary", "trend_start_date": "2026-05-01", "main_narrative": "Main",
        "sub_narrative": "N1", "sub_narrative_id": "N1", "stance_default": "", "include_keywords": "governor",
        "required_context_keywords": "", "exclude_keywords": "", "public_voice_cues": "", "cluster_split_terms": "",
        "definition_ja": "", "pa_implication_ja": "", "example_phrases": "", "priority": "High",
        "max_cluster_records": "200", "representative_count": "8", "is_fallback": "false", "active": "true",
    }
    row.update(overrides)
    return row


def _seed_run_and_assets(client, *, run_id="run-1", project_id="proj-1",
                          prompt1_contract_version=None, prompt2_contract_version=None):
    client.insert("geo_report_project_assets", [{
        "id": "nm-1", "project_id": project_id, "asset_type": "narrative_master", "status": "published",
        "content": {"rows": [_narrative_master_row()]},
    }])
    template_content = {
        "style_css": "<style>.x{}</style>",
        "shell_html": "<html><head>{style}</head><body>{sections}</body></html>",
        "sections": [
            {"section_id": "exec-summary", "title": "Executive Summary", "order": 1,
             "component_type": "narrative_block", "required_fields": ["central_conclusion"]},
        ],
    }
    client.insert("geo_report_project_assets", [{
        "id": "tmpl-1", "project_id": project_id, "asset_type": "report_template", "status": "published",
        "content": template_content,
    }])
    client.insert("geo_report_collection_runs", [{
        "run_id": run_id, "project_id": project_id, "narrative_master_asset_id": "nm-1",
        "report_template_asset_id": "tmpl-1",
        "prompt1_contract_version": prompt1_contract_version,
        "prompt2_contract_version": prompt2_contract_version,
    }])


def _seed_prompt1_input_artifacts(client, run_id="run-1"):
    for name in export_svc.PROMPT1_INPUT_REQUIRED_FILES:
        if name == "signal_narrative_master.csv":
            continue
        client.insert("geo_report_run_artifacts", [{
            "id": f"art-{name}", "run_id": run_id, "execution_id": None, "execution_method": "system_snapshot",
            "artifact_type": "prompt1_input", "file_name": name, "content_text": f"dummy content for {name}",
            "sha256": "x", "size_bytes": 10, "media_type": "text/csv",
        }])


def _seed_prompt_snapshots(client, run_id="run-1"):
    for name in ("prompt1_instruction.md", "prompt2_instruction.md"):
        client.insert("geo_report_run_artifacts", [{
            "id": f"art-{name}", "run_id": run_id, "execution_id": None, "execution_method": "system_snapshot",
            "artifact_type": "prompt_snapshot", "file_name": name, "content_text": f"# {name}",
            "sha256": "x", "size_bytes": 10, "media_type": "text/markdown",
        }])


# ---------------------------------------------------------------------------
# Prompt1 Package Export
# ---------------------------------------------------------------------------

def test_build_prompt1_input_zip_has_all_11_files():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client)
    _seed_prompt1_input_artifacts(client)

    zip_bytes = export_svc.build_prompt1_input_zip(client, "run-1")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = set(zf.namelist())
    assert names == set(export_svc.PROMPT1_INPUT_REQUIRED_FILES)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        master_csv = zf.read("signal_narrative_master.csv").decode("utf-8")
    assert "Arizona Governor" in master_csv


def test_export_prompt1_package_wraps_inner_zip_and_instruction():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client)
    _seed_prompt1_input_artifacts(client)
    _seed_prompt_snapshots(client)

    outer_zip = export_svc.export_prompt1_package(client, "run-1")
    with zipfile.ZipFile(io.BytesIO(outer_zip)) as zf:
        names = set(zf.namelist())
        assert names == {"prompt1_input_run-1.zip", "prompt1_instruction.md"}
        assert zf.read("prompt1_instruction.md").decode("utf-8") == "# prompt1_instruction.md"
        with zipfile.ZipFile(io.BytesIO(zf.read("prompt1_input_run-1.zip"))) as inner:
            assert set(inner.namelist()) == set(export_svc.PROMPT1_INPUT_REQUIRED_FILES)


# ---------------------------------------------------------------------------
# Prompt1 Package Import
# ---------------------------------------------------------------------------

def _csv_bytes(rows: list, columns: list) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


def _build_prompt1_output_zip_bytes():
    files = {}
    files["classified_clusters.csv"] = _csv_bytes([{
        "cluster_id": "AC_1", "signal_id": "S1", "report_topic_label": "Arizona Governor",
        "final_topic_label": "Governor race", "source_assessment": "Public Voice", "tone_gradient": "Positive",
        "voice_direction": "Supportive", "report_use_status": "include", "continuity_relevance": "ongoing",
        "matched_previous_topic_id": "", "change_signal_summary": "steady", "noise_flag": "false",
        "noise_reason": "", "voice_summary": "summary", "notable_phrases": "phrase",
        "representative_quality": "high", "notes_for_prompt2": "",
    }], export_svc.prompt1_output.CLASSIFIED_CLUSTERS_COLUMNS)
    files["public_voice_examples.csv"] = _csv_bytes([], export_svc.prompt1_output.PUBLIC_VOICE_EXAMPLES_COLUMNS)
    files["continuity_change_candidates.csv"] = _csv_bytes(
        [], export_svc.prompt1_output.CONTINUITY_CHANGE_CANDIDATES_COLUMNS)
    files["narrative_master_update_candidates.csv"] = _csv_bytes(
        [], export_svc.prompt1_output.NARRATIVE_MASTER_UPDATE_CANDIDATES_COLUMNS)
    files["sentiment_cluster_classified.csv"] = _csv_bytes([{
        "sentiment_cluster_id": "SC_1", "theme_relevance": "relevant", "source_class": "Public Voice",
        "reaction_type": "comment", "attitude": "positive", "cluster_consistency": "High",
        "apply_to_cluster": "true", "exception_pattern": "", "classification_reason": "matches",
        "targeted_stance_status": "available", "targeted_stance_count": "1",
    }], export_svc.prompt1_output.SENTIMENT_CLUSTER_CLASSIFIED_COLUMNS)
    files["targeted_stance_classified.csv"] = _csv_bytes([{
        "stance_id": "TS-1", "sentiment_cluster_id": "SC_1", "signal_id": "S1", "target_type": "Candidate",
        "target_name": "Jane Doe", "target_normalized_id": "", "stance_polarity": "favorable",
        "stance_strength": "strong", "stance_confidence": "high", "stance_evidence": "quote",
        "stance_basis": "direct", "stance_cluster_consistency": "High", "apply_stance_to_cluster": "true",
        "stance_exception_pattern": "", "stance_reason": "clear support",
    }], export_svc.prompt1_output.TARGETED_STANCE_CLASSIFIED_COLUMNS)
    files["prompt1_quality_audit.csv"] = _csv_bytes([], export_svc.prompt1_output.PROMPT1_QUALITY_AUDIT_COLUMNS)
    files["prompt1_summary.md"] = b"# Prompt1 Summary\n"
    files["classification_rule_applied.md"] = b"# Classification Rule Applied\n"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_import_prompt1_result_persists_classifications_and_artifacts():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client)
    zip_bytes = _build_prompt1_output_zip_bytes()

    result = export_svc.import_prompt1_result(client, "run-1", zip_bytes)
    execution_id = result["execution_id"]

    execution = client.select("geo_report_executions", {"select": "*", "id": f"eq.{execution_id}"})[0]
    assert execution["status"] == "completed"
    assert execution["execution_method"] == "chatgpt_import"
    assert execution["stage"] == "prompt1"

    sentiment_cls = client.select("geo_report_sentiment_cluster_classifications", {
        "select": "*", "execution_id": f"eq.{execution_id}", "sentiment_cluster_id": "eq.SC_1",
    })
    assert len(sentiment_cls) == 1
    assert sentiment_cls[0]["theme_relevance"] == "relevant"

    analysis_cls = client.select("geo_report_analysis_cluster_classifications", {
        "select": "*", "execution_id": f"eq.{execution_id}", "cluster_id": "eq.AC_1",
    })
    assert len(analysis_cls) == 1
    assert analysis_cls[0]["noise_flag"] is False

    stance_rows = client.select("geo_report_targeted_stance", {
        "select": "*", "execution_id": f"eq.{execution_id}", "sentiment_cluster_id": "eq.SC_1",
    })
    assert len(stance_rows) == 1
    assert stance_rows[0]["target_name"] == "Jane Doe"

    artifacts = client.select("geo_report_run_artifacts", {
        "select": "*", "execution_id": f"eq.{execution_id}", "artifact_type": "eq.prompt1_output",
    })
    assert {a["file_name"] for a in artifacts} == set(export_svc.PROMPT1_OUTPUT_REQUIRED_FILES)


def test_import_prompt1_result_raises_on_missing_file():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("classified_clusters.csv", "a,b\n")
    try:
        export_svc.import_prompt1_result(client, "run-1", buf.getvalue())
        assert False, "should have raised"
    except ValueError as exc:
        assert "必須ファイル" in str(exc)


# ---------------------------------------------------------------------------
# Phase H (evidence_intelligence_v1): Import時のContract Validation
# ---------------------------------------------------------------------------

def _build_ei_prompt1_output_zip_bytes(*, with_discovery=False, discovery_rows=None,
                                         missing_evidence_directness=False):
    files = {}
    ei_cols = list(export_svc.prompt1_output.EI_CLASSIFIED_CLUSTERS_COLUMNS)
    ei_row = {
        "cluster_id": "AC_1", "signal_id": "S1", "report_topic_label": "Trade", "actor_category": "Official",
        "actor_name": "White House", "evidence_type": "Official Statement", "source_authority": "Principal",
        "evidence_directness": "Direct", "position_summary": "supports deal", "objective": "",
        "incentive": "", "constraint": "", "bargaining_position": "flexible", "red_line": "",
        "hypothesis_relevance": "H1", "evidence_relation": "supporting", "evidence_strength": "Strong",
        "materiality": "High", "report_use_status": "Main", "reaction": "not_applicable",
        "attitude": "not_applicable", "notes_for_prompt2": "",
    }
    if missing_evidence_directness:
        ei_cols.remove("evidence_directness")
        ei_row.pop("evidence_directness")
    files["classified_clusters.csv"] = _csv_bytes([ei_row], ei_cols)
    files["public_voice_examples.csv"] = _csv_bytes([], export_svc.prompt1_output.PUBLIC_VOICE_EXAMPLES_COLUMNS)
    files["continuity_change_candidates.csv"] = _csv_bytes(
        [], export_svc.prompt1_output.CONTINUITY_CHANGE_CANDIDATES_COLUMNS)
    files["narrative_master_update_candidates.csv"] = _csv_bytes(
        [], export_svc.prompt1_output.NARRATIVE_MASTER_UPDATE_CANDIDATES_COLUMNS)
    files["sentiment_cluster_classified.csv"] = _csv_bytes([{
        "sentiment_cluster_id": "SC_1", "theme_relevance": "relevant", "actor_category": "Official",
        "evidence_relation": "supporting", "cluster_consistency": "High", "apply_to_cluster": "true",
        "exception_pattern": "", "classification_reason": "matches",
    }], export_svc.prompt1_output.EI_SENTIMENT_CLUSTER_CLASSIFIED_COLUMNS)
    files["targeted_stance_classified.csv"] = _csv_bytes(
        [], export_svc.prompt1_output.TARGETED_STANCE_CLASSIFIED_COLUMNS)
    files["prompt1_quality_audit.csv"] = _csv_bytes([], export_svc.prompt1_output.PROMPT1_QUALITY_AUDIT_COLUMNS)
    files["prompt1_summary.md"] = b"# Prompt1 Summary\n"
    files["classification_rule_applied.md"] = b"# Classification Rule Applied\n"

    if with_discovery:
        rows = discovery_rows if discovery_rows is not None else [{
            "discovery_cluster_id": "DC_1", "signal_id": "S6", "cluster_size": "3", "dominant_tokens": "tariff",
            "source_types": "media", "topic_label": "New tariff proposal", "topic_summary": "summary",
            "key_actors": "MOFCOM", "existing_signal_overlap": "false", "linked_signal_ids": "",
            "why_new_or_different": "new policy angle", "potential_H1_H2_H3_effect": "H1",
            "business_relevance": "High", "materiality": "Medium", "confidence": "Medium",
        }]
        files["discovery_candidates_classified.csv"] = _csv_bytes(
            rows, export_svc.prompt1_output.DISCOVERY_CANDIDATES_CLASSIFIED_COLUMNS)
        files["discovery_evidence_examples.csv"] = _csv_bytes(
            [], export_svc.prompt1_output.DISCOVERY_EVIDENCE_EXAMPLES_COLUMNS)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _seed_discovery_run_config(client, run_id="run-1", *, discovery_signal_ids=("S6",)):
    client.insert("geo_report_run_config", [{
        "run_id": run_id, "config_json": {"discovery_signal_ids": list(discovery_signal_ids)},
    }])


def test_import_prompt1_result_accepts_valid_evidence_intelligence_output():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client, prompt1_contract_version="evidence_intelligence_v1")
    zip_bytes = _build_ei_prompt1_output_zip_bytes(with_discovery=True)

    result = export_svc.import_prompt1_result(client, "run-1", zip_bytes)
    artifacts = client.select("geo_report_run_artifacts", {
        "select": "*", "execution_id": f"eq.{result['execution_id']}", "artifact_type": "eq.prompt1_output",
    })
    assert "discovery_candidates_classified.csv" in {a["file_name"] for a in artifacts}


def test_import_prompt1_result_rejects_missing_evidence_directness_for_ei():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client, prompt1_contract_version="evidence_intelligence_v1")
    zip_bytes = _build_ei_prompt1_output_zip_bytes(missing_evidence_directness=True)
    try:
        export_svc.import_prompt1_result(client, "run-1", zip_bytes)
        assert False, "should have raised"
    except ValueError as exc:
        assert "evidence_directness" in str(exc)


def test_import_prompt1_result_rejects_ei_output_missing_discovery_files_when_expected():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client, prompt1_contract_version="evidence_intelligence_v1")
    _seed_discovery_run_config(client)
    zip_bytes = _build_ei_prompt1_output_zip_bytes(with_discovery=False)
    try:
        export_svc.import_prompt1_result(client, "run-1", zip_bytes)
        assert False, "should have raised"
    except ValueError as exc:
        assert "discovery_candidates_classified.csv" in str(exc)


def test_import_prompt1_result_does_not_require_discovery_files_when_not_configured():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client, prompt1_contract_version="evidence_intelligence_v1")
    # discovery_signal_idsを設定しない（Public Voice等、discovery_mode Signal無し）
    zip_bytes = _build_ei_prompt1_output_zip_bytes(with_discovery=False)
    result = export_svc.import_prompt1_result(client, "run-1", zip_bytes)
    assert result["execution_id"]


def test_import_prompt1_result_v43_does_not_require_evidence_directness():
    """v4.3のRun（prompt1_contract_version未設定）では、EI専用フィールドを要求しない
    （既存動作を変えない）。"""
    client = FakeSupabaseClient()
    _seed_run_and_assets(client)
    zip_bytes = _build_prompt1_output_zip_bytes()
    result = export_svc.import_prompt1_result(client, "run-1", zip_bytes)
    assert result["execution_id"]


def test_evidence_directness_survives_import_and_reexport_round_trip():
    """ラウンド5指示#4: Import→DB保存→build_prompt1_output_zip_for_runでの再生成の
    後もevidence_directnessがCluster単位で完全に保持される。discovery系2ファイルも
    同様に失われず再現される（ラウンド3指示#6）。"""
    client = FakeSupabaseClient()
    _seed_run_and_assets(client, prompt1_contract_version="evidence_intelligence_v1")
    zip_bytes = _build_ei_prompt1_output_zip_bytes(with_discovery=True)
    result = export_svc.import_prompt1_result(client, "run-1", zip_bytes)
    execution_id = result["execution_id"]

    reexported = export_svc.build_prompt1_output_zip_for_run(client, "run-1", execution_id)
    with zipfile.ZipFile(io.BytesIO(reexported)) as zf:
        names = set(zf.namelist())
        assert "discovery_candidates_classified.csv" in names
        assert "discovery_evidence_examples.csv" in names
        discovery_text = zf.read("discovery_candidates_classified.csv").decode("utf-8")
        assert "DC_1" in discovery_text


def test_reference_examples_used_columns_cover_template_required_fields():
    """ラウンド2指示#4: EI_REFERENCE_EXAMPLES_USED_COLUMNSが、sec8_representative_
    evidenceのrequired_fields（Section単位の集約情報を除く）を全てカバーする
    ことを機械的に検証する（「1:1対応」という主張の裏付け）。"""
    covered = set(export_svc.prompt2_output.EI_REFERENCE_EXAMPLES_USED_COLUMNS)
    required = (set(export_svc.prompt2_output.SEC8_REPRESENTATIVE_EVIDENCE_REQUIRED_FIELDS)
                 - export_svc.prompt2_output.SEC8_SECTION_LEVEL_ONLY_FIELDS)
    assert required.issubset(covered)


# ---------------------------------------------------------------------------
# Prompt2 Base Input
# ---------------------------------------------------------------------------

def _seed_records(client, run_id="run-1", *, count=3):
    for i in range(count):
        client.insert("geo_report_records", [{
            "id": f"rec-{i}", "run_id": run_id, "record_id": f"rec-{i}", "timestamp_raw": "2026-05-05T00:00:00Z",
            "date_key": "2026-05-05", "week_start": "2026-05-04", "week_end": "2026-05-10", "signal_id": "S1",
            "theme_id": "T1", "theme_name": "Theme1", "trend_role": "primary", "trend_start_date": "2026-05-01",
            "source_file": "input.csv", "source_bucket": "News", "source_class_preliminary": "Public_Voice_Candidate",
            "reaction_type_hint": "comment", "attitude_hint": "positive", "narrative_id": "N1",
            "narrative_name": "N1", "match_status": "matched", "matched_terms": "governor",
            "cluster_cue": "cue1", "match_signature": "sig1", "analysis_cluster_id": "AC_1",
            "sentiment_cluster_id": "SC_1", "is_duplicate": False, "duplicate_group_id": "",
            "url_hash": f"uh-{i}", "text_hash": f"th-{i}",
        }])


def test_fetch_all_records_paginates_by_id_across_multiple_pages():
    """2026-09-17追加、ユーザー報告「ダウンロードしたらinternal server errorが出た」
    への対応検証: 従来のrecord_id(索引無し)+OFFSETページングを、id(主キー、索引済み)
    によるキーセットページングへ変更した。1ページ(1000件)を超える件数でも、
    重複・欠落無く全件取得できることを確認する。"""
    client = FakeSupabaseClient()
    _seed_records(client, count=1500)

    records = export_svc._fetch_all_records(client, "run-1")

    assert len(records) == 1500
    assert sorted(r["id"] for r in records) == sorted(f"rec-{i}" for i in range(1500))


def test_build_record_cluster_map_chunks_splits_by_chunk_size():
    records = [{"record_id": f"r{i}", "is_duplicate": False} for i in range(5)]
    chunks = export_svc.build_record_cluster_map_chunks(records, chunk_size=2)
    assert len(chunks) == 3
    header = chunks[0].splitlines()[0]
    assert header.split(",") == export_svc.cp.RECORD_MAP_COLUMNS


def test_build_record_cluster_map_chunks_empty_records_yields_one_header_only_chunk():
    chunks = export_svc.build_record_cluster_map_chunks([], chunk_size=2)
    assert len(chunks) == 1
    assert len(chunks[0].splitlines()) == 1


def test_build_prompt2_base_input_zip_has_fixed_files_and_record_map_chunks():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client)
    _seed_prompt1_input_artifacts(client)
    _seed_records(client, count=3)

    zip_bytes = export_svc.build_prompt2_base_input_zip(client, "run-1")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = set(zf.namelist())
        fixed_present = set(export_svc.PROMPT2_BASE_INPUT_FIXED_FILES) - {"timeline_basis.csv"}
        assert fixed_present.issubset(names)
        assert "timeline_basis.csv" in names
        assert "record_cluster_map_001.csv" in names

        pop_by_signal = zf.read("population_narrative_by_signal.csv").decode("utf-8")
        assert "S1" in pop_by_signal

        lang_csv = zf.read("population_narrative_by_signal_language.csv").decode("utf-8")
        assert len(lang_csv.strip().splitlines()) == 1  # ヘッダのみ（言語列未実装のP0既知の制限）

        timeline = zf.read("timeline_basis.csv").decode("utf-8")
        weekly = zf.read("weekly_trend_raw_basis.csv").decode("utf-8")
        assert timeline == weekly


# ---------------------------------------------------------------------------
# Prompt2 Package Export / Import
# ---------------------------------------------------------------------------

def _seed_canonical_prompt1_with_existing_output_artifacts(client, run_id="run-1"):
    execution_id = "exec-prompt1-1"
    client.update("geo_report_collection_runs", {"run_id": f"eq.{run_id}"}, {
        "canonical_prompt1_execution_id": execution_id,
    })
    for name in export_svc.PROMPT1_OUTPUT_REQUIRED_FILES:
        client.insert("geo_report_run_artifacts", [{
            "id": f"art-out-{name}", "run_id": run_id, "execution_id": execution_id,
            "execution_method": "application_ai", "artifact_type": "prompt1_output", "file_name": name,
            "content_text": f"dummy {name}", "sha256": "x", "size_bytes": 10,
            "media_type": "text/markdown" if name.endswith(".md") else "text/csv",
        }])
    return execution_id


def test_build_prompt1_output_zip_for_run_uses_existing_artifacts_when_complete():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client)
    execution_id = _seed_canonical_prompt1_with_existing_output_artifacts(client)

    zip_bytes = export_svc.build_prompt1_output_zip_for_run(client, "run-1", execution_id)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        assert set(zf.namelist()) == set(export_svc.PROMPT1_OUTPUT_REQUIRED_FILES)
        assert zf.read("prompt1_summary.md").decode("utf-8") == "dummy prompt1_summary.md"


def test_export_prompt2_package_bundles_prompt1_output_base_input_template_and_instruction():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client)
    _seed_prompt1_input_artifacts(client)
    _seed_prompt_snapshots(client)
    _seed_records(client, count=2)
    _seed_canonical_prompt1_with_existing_output_artifacts(client)

    outer_zip = export_svc.export_prompt2_package(client, "run-1")
    with zipfile.ZipFile(io.BytesIO(outer_zip)) as zf:
        names = set(zf.namelist())
        assert names == {
            "prompt1_output_run-1.zip", "prompt2_base_input_run-1.zip", "report_template.html",
            "report_template.json", "prompt2_instruction.md",
        }
        assert "Executive Summary" in zf.read("report_template.html").decode("utf-8")
        assert zf.read("prompt2_instruction.md").decode("utf-8") == "# prompt2_instruction.md"
        # report_template.jsonはSection定義そのもの（machine-readable Source of Truth）。
        template_sections = json.loads(zf.read("report_template.json").decode("utf-8"))
        assert isinstance(template_sections, list) and len(template_sections) > 0


def test_export_prompt2_package_requires_canonical_prompt1():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client)
    try:
        export_svc.export_prompt2_package(client, "run-1")
        assert False, "should have raised"
    except ValueError as exc:
        assert "Prompt1" in str(exc)


# ---------------------------------------------------------------------------
# Phase H (evidence_intelligence_v1): Discovery Provenance / Preflight / Canonical隔離性
# ---------------------------------------------------------------------------

def _seed_provenance_chain(client, *, run_id="run-1", preprocess_execution_id="exec-preprocess-1",
                             prompt1_execution_id="exec-prompt1-1", discovery_raw_rows=None,
                             discovery_classified_rows=None, include_discovery_output=True):
    """Preprocess Execution → Prompt1 Executionという実際のProvenanceチェーンを
    構築する（geo_report_executions.input_preprocess_execution_id経由）。
    discovery_candidates.csv（生データ）はpreprocess_execution_idへ、
    discovery_candidates_classified.csv（Prompt1出力）はprompt1_execution_idへ、
    それぞれ正しくexecution_idスコープで保存する。"""
    client.insert("geo_report_executions", [{
        "id": preprocess_execution_id, "run_id": run_id, "stage": "preprocess",
        "execution_method": "python", "status": "completed",
    }])
    client.insert("geo_report_executions", [{
        "id": prompt1_execution_id, "run_id": run_id, "stage": "prompt1", "execution_method": "chatgpt_import",
        "status": "completed", "input_preprocess_execution_id": preprocess_execution_id,
    }])
    client.update("geo_report_collection_runs", {"run_id": f"eq.{run_id}"}, {
        "canonical_prompt1_execution_id": prompt1_execution_id,
    })

    if discovery_raw_rows is not None:
        client.insert("geo_report_run_artifacts", [{
            "id": "art-discovery-raw", "run_id": run_id, "execution_id": preprocess_execution_id,
            "execution_method": "python", "artifact_type": "prompt1_input",
            "file_name": "discovery_candidates.csv",
            "content_text": _csv_bytes(discovery_raw_rows,
                export_svc.prompt1_output.DISCOVERY_CANDIDATES_CLASSIFIED_COLUMNS).decode("utf-8"),
            "sha256": "x", "size_bytes": 10, "media_type": "text/csv",
        }])
        # PROMPT1_INPUT_OPTIONAL_FILESはdiscovery_candidates.csv/discovery_candidate_
        # representatives.csvの両方を要求するため、テストでも両方を用意する。
        client.insert("geo_report_run_artifacts", [{
            "id": "art-discovery-reps-raw", "run_id": run_id, "execution_id": preprocess_execution_id,
            "execution_method": "python", "artifact_type": "prompt1_input",
            "file_name": "discovery_candidate_representatives.csv",
            "content_text": "discovery_cluster_id,title\nDC_1,dummy\n",
            "sha256": "x", "size_bytes": 10, "media_type": "text/csv",
        }])

    for name in export_svc.PROMPT1_OUTPUT_REQUIRED_FILES:
        client.insert("geo_report_run_artifacts", [{
            "id": f"art-out-{name}", "run_id": run_id, "execution_id": prompt1_execution_id,
            "execution_method": "chatgpt_import", "artifact_type": "prompt1_output", "file_name": name,
            "content_text": f"dummy {name}", "sha256": "x", "size_bytes": 10,
            "media_type": "text/markdown" if name.endswith(".md") else "text/csv",
        }])
    if include_discovery_output and discovery_classified_rows is not None:
        client.insert("geo_report_run_artifacts", [{
            "id": "art-discovery-classified", "run_id": run_id, "execution_id": prompt1_execution_id,
            "execution_method": "chatgpt_import", "artifact_type": "prompt1_output",
            "file_name": "discovery_candidates_classified.csv",
            "content_text": _csv_bytes(discovery_classified_rows,
                export_svc.prompt1_output.DISCOVERY_CANDIDATES_CLASSIFIED_COLUMNS).decode("utf-8"),
            "sha256": "x", "size_bytes": 10, "media_type": "text/csv",
        }])
        client.insert("geo_report_run_artifacts", [{
            "id": "art-discovery-evidence", "run_id": run_id, "execution_id": prompt1_execution_id,
            "execution_method": "chatgpt_import", "artifact_type": "prompt1_output",
            "file_name": "discovery_evidence_examples.csv",
            "content_text": _csv_bytes([], export_svc.prompt1_output.DISCOVERY_EVIDENCE_EXAMPLES_COLUMNS)
                .decode("utf-8"),
            "sha256": "x", "size_bytes": 10, "media_type": "text/csv",
        }])
    return preprocess_execution_id, prompt1_execution_id


_DISCOVERY_ROW = {
    "discovery_cluster_id": "DC_1", "signal_id": "S6", "cluster_size": "3", "dominant_tokens": "tariff",
    "source_types": "media", "topic_label": "New tariff proposal", "topic_summary": "summary",
    "key_actors": "MOFCOM", "existing_signal_overlap": "false", "linked_signal_ids": "",
    "why_new_or_different": "new policy angle", "potential_H1_H2_H3_effect": "H1",
    "business_relevance": "High", "materiality": "Medium", "confidence": "Medium",
}


def test_build_prompt2_base_input_zip_includes_discovery_via_provenance_when_configured():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client)
    _seed_prompt1_input_artifacts(client)
    _seed_records(client, count=2)
    _seed_discovery_run_config(client)
    _seed_provenance_chain(client, discovery_raw_rows=[_DISCOVERY_ROW],
                            discovery_classified_rows=[_DISCOVERY_ROW])

    zip_bytes = export_svc.build_prompt2_base_input_zip(client, "run-1")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        assert "discovery_candidates.csv" in zf.namelist()
        assert "DC_1" in zf.read("discovery_candidates.csv").decode("utf-8")


def test_build_prompt2_base_input_zip_omits_discovery_when_not_configured():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client)
    _seed_prompt1_input_artifacts(client)
    _seed_records(client, count=2)
    # discovery_signal_idsを設定しない（Public Voice等）
    _seed_provenance_chain(client, discovery_raw_rows=None, include_discovery_output=False)

    zip_bytes = export_svc.build_prompt2_base_input_zip(client, "run-1")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        assert "discovery_candidates.csv" not in zf.namelist()


def test_export_prompt2_package_rejects_when_discovery_output_missing():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client)
    _seed_prompt1_input_artifacts(client)
    _seed_prompt_snapshots(client)
    _seed_records(client, count=2)
    _seed_discovery_run_config(client)
    _seed_provenance_chain(client, discovery_raw_rows=[_DISCOVERY_ROW], include_discovery_output=False)

    try:
        export_svc.export_prompt2_package(client, "run-1")
        assert False, "should have raised"
    except ValueError as exc:
        assert "discovery_candidates_classified.csv" in str(exc) or "discovery_evidence_examples.csv" in str(exc)


def test_export_prompt2_package_rejects_when_discovery_ids_do_not_match_1to1():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client)
    _seed_prompt1_input_artifacts(client)
    _seed_prompt_snapshots(client)
    _seed_records(client, count=2)
    _seed_discovery_run_config(client)
    raw_rows = [_DISCOVERY_ROW, {**_DISCOVERY_ROW, "discovery_cluster_id": "DC_2"}]
    classified_rows = [_DISCOVERY_ROW]  # DC_2が欠落している
    _seed_provenance_chain(client, discovery_raw_rows=raw_rows, discovery_classified_rows=classified_rows)

    try:
        export_svc.export_prompt2_package(client, "run-1")
        assert False, "should have raised"
    except ValueError as exc:
        assert "1:1 Contract" in str(exc)


def test_export_prompt2_package_rejects_when_classified_has_extra_id_not_in_raw():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client)
    _seed_prompt1_input_artifacts(client)
    _seed_prompt_snapshots(client)
    _seed_records(client, count=2)
    _seed_discovery_run_config(client)
    raw_rows = [_DISCOVERY_ROW]
    classified_rows = [_DISCOVERY_ROW, {**_DISCOVERY_ROW, "discovery_cluster_id": "DC_EXTRA"}]
    _seed_provenance_chain(client, discovery_raw_rows=raw_rows, discovery_classified_rows=classified_rows)

    try:
        export_svc.export_prompt2_package(client, "run-1")
        assert False, "should have raised"
    except ValueError as exc:
        assert "classifiedにのみ存在する" in str(exc)


def test_export_prompt2_package_accepts_matching_discovery_ids():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client)
    _seed_prompt1_input_artifacts(client)
    _seed_prompt_snapshots(client)
    _seed_records(client, count=2)
    _seed_discovery_run_config(client)
    _seed_provenance_chain(client, discovery_raw_rows=[_DISCOVERY_ROW],
                            discovery_classified_rows=[_DISCOVERY_ROW])

    outer_zip = export_svc.export_prompt2_package(client, "run-1")
    assert outer_zip  # 例外が出ずに生成できる


def test_export_prompt2_package_uses_only_canonical_execution_provenance():
    """ラウンド4指示#4・ラウンド7指示#1: 同一Runに2つのPreprocess Execution
    （A=discovery無し、B=discovery有りでCanonical Prompt1が参照）が存在する場合、
    Canonical Prompt1のinput_preprocess_execution_idがBを指せば、Prompt2 Export
    はB由来のdiscoveryのみを使い、A由来のartifactは混入しない。"""
    client = FakeSupabaseClient()
    _seed_run_and_assets(client)
    _seed_prompt1_input_artifacts(client)
    _seed_prompt_snapshots(client)
    _seed_records(client, count=2)
    _seed_discovery_run_config(client)

    # Preprocess Execution A（discovery無し、Canonicalではない）
    client.insert("geo_report_executions", [{
        "id": "exec-preprocess-A", "run_id": "run-1", "stage": "preprocess",
        "execution_method": "python", "status": "failed",
    }])

    # Preprocess Execution B（discovery有り）+ Prompt1がBを参照してCanonical採用
    other_row = {**_DISCOVERY_ROW, "discovery_cluster_id": "DC_FROM_B"}
    _seed_provenance_chain(client, preprocess_execution_id="exec-preprocess-B",
                            prompt1_execution_id="exec-prompt1-B",
                            discovery_raw_rows=[other_row], discovery_classified_rows=[other_row])

    zip_bytes = export_svc.build_prompt2_base_input_zip(client, "run-1")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        discovery_text = zf.read("discovery_candidates.csv").decode("utf-8")
        assert "DC_FROM_B" in discovery_text

    # Export自体もB由来のdiscoveryだけで問題なく完了する（1:1一致）。
    outer_zip = export_svc.export_prompt2_package(client, "run-1")
    assert outer_zip


def _build_prompt2_output_zip_bytes():
    files = {
        "final_report.html": b"<html><body>Report</body></html>",
        "report_brief.md": b"# Brief",
        "biweekly_trend_data.csv": b"period,n\n",
        "weekly_trend_data.csv": b"period,n\n",
        "monthly_summary_data.csv": b"period,n\n",
        "denominator_audit.csv": b"signal_id,n\n",
        "reference_examples_used.csv": b"example_id\n",
        "continuity_context_next.json": b'{"schema_version": "1.2"}',
        "continuity_change_log.md": b"# Log",
        "methodology_notes.md": b"# Methodology",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_import_prompt2_result_persists_execution_and_artifacts():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client)
    zip_bytes = _build_prompt2_output_zip_bytes()

    result = export_svc.import_prompt2_result(client, "run-1", zip_bytes)
    execution_id = result["execution_id"]

    execution = client.select("geo_report_executions", {"select": "*", "id": f"eq.{execution_id}"})[0]
    assert execution["status"] == "completed"
    assert execution["stage"] == "prompt2"
    assert execution["execution_method"] == "chatgpt_import"
    assert execution["input_aggregation_execution_id"] is None

    final_report = client.select("geo_report_run_artifacts", {
        "select": "*", "execution_id": f"eq.{execution_id}", "artifact_type": "eq.final_report",
    })
    assert len(final_report) == 1
    assert final_report[0]["file_name"] == "final_report.html"

    other_outputs = client.select("geo_report_run_artifacts", {
        "select": "*", "execution_id": f"eq.{execution_id}", "artifact_type": "eq.prompt2_output",
    })
    assert {a["file_name"] for a in other_outputs} == set(export_svc.PROMPT2_OUTPUT_REQUIRED_FILES) - {
        "final_report.html"}


def test_get_final_report_html_reads_canonical_report_execution_artifact():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client)
    client.insert("geo_report_executions", [{
        "id": "exec-report-1", "run_id": "run-1", "stage": "prompt2", "execution_method": "application_ai",
        "status": "completed",
    }])
    client.update("geo_report_collection_runs", {"run_id": "eq.run-1"}, {
        "canonical_report_execution_id": "exec-report-1",
    })
    client.insert("geo_report_run_artifacts", [{
        "id": "art-final-report", "run_id": "run-1", "execution_id": "exec-report-1",
        "execution_method": "application_ai", "artifact_type": "final_report", "file_name": "final_report.html",
        "content_text": "<html><body>Hello Report</body></html>", "sha256": "x", "size_bytes": 10,
        "media_type": "text/html",
    }])

    html = export_svc.get_final_report_html(client, "run-1")
    assert "Hello Report" in html


def test_get_final_report_html_raises_when_not_yet_finalized():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client)
    try:
        export_svc.get_final_report_html(client, "run-1")
        assert False, "should have raised"
    except ValueError as exc:
        assert "確定していません" in str(exc)


def test_import_prompt2_result_raises_on_missing_file():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("final_report.html", "<html></html>")
    try:
        export_svc.import_prompt2_result(client, "run-1", buf.getvalue())
        assert False, "should have raised"
    except ValueError as exc:
        assert "必須ファイル" in str(exc)


# ---------------------------------------------------------------------------
# Phase H (evidence_intelligence_v1): Prompt2 Output Import — schema/section coverage
# ---------------------------------------------------------------------------

def _build_ei_prompt2_output_zip_bytes(*, final_report_html=None, denominator_audit_header=None):
    ei_cols = export_svc.prompt2_output.EI_TREND_DATA_COLUMNS
    denom_header = denominator_audit_header or export_svc.prompt2_output.EI_DENOMINATOR_AUDIT_COLUMNS
    ref_cols = export_svc.prompt2_output.EI_REFERENCE_EXAMPLES_USED_COLUMNS
    default_html = (
        '<html><body>'
        '<section data-section-id="exec-summary">'
        '<div data-field="central_conclusion">Deal likely by September.</div>'
        '</section>'
        '</body></html>'
    )
    files = {
        "final_report.html": (final_report_html or default_html).encode("utf-8"),
        "report_brief.md": b"# Brief",
        "biweekly_trend_data.csv": (",".join(ei_cols) + "\n").encode("utf-8"),
        "weekly_trend_data.csv": (",".join(ei_cols) + "\n").encode("utf-8"),
        "monthly_summary_data.csv": (",".join(ei_cols) + "\n").encode("utf-8"),
        "denominator_audit.csv": (",".join(denom_header) + "\n").encode("utf-8"),
        "reference_examples_used.csv": (",".join(ref_cols) + "\n").encode("utf-8"),
        "continuity_context_next.json": b'{"schema_version": "1.2"}',
        "continuity_change_log.md": b"# Log",
        "methodology_notes.md": b"# Methodology",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_import_prompt2_result_accepts_valid_ei_output_with_no_warnings():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client, prompt2_contract_version="evidence_intelligence_v1")
    zip_bytes = _build_ei_prompt2_output_zip_bytes()

    result = export_svc.import_prompt2_result(client, "run-1", zip_bytes)
    assert result["section_coverage_warnings"] == []


def test_import_prompt2_result_rejects_ei_output_with_missing_denominator_audit_columns():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client, prompt2_contract_version="evidence_intelligence_v1")
    zip_bytes = _build_ei_prompt2_output_zip_bytes(denominator_audit_header=["signal_id", "n"])
    try:
        export_svc.import_prompt2_result(client, "run-1", zip_bytes)
        assert False, "should have raised"
    except ValueError as exc:
        assert "denominator_audit.csv" in str(exc)


def test_import_prompt2_result_v43_does_not_enforce_ei_schema():
    """v4.3のRunでは、EI版で必須の列が無くてもschema validationは呼ばれない
    （既存動作を変えない）。"""
    client = FakeSupabaseClient()
    _seed_run_and_assets(client)  # prompt2_contract_version未設定 = v4.3扱い
    zip_bytes = _build_prompt2_output_zip_bytes()  # v4.3の適当なヘッダ（denominator_audit.csv=signal_id,n）
    result = export_svc.import_prompt2_result(client, "run-1", zip_bytes)
    assert result["execution_id"]
    assert result["section_coverage_warnings"] is None


def test_import_prompt2_result_rejects_when_section_entirely_missing():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client, prompt2_contract_version="evidence_intelligence_v1")
    html_without_section = "<html><body>No sections here.</body></html>"
    zip_bytes = _build_ei_prompt2_output_zip_bytes(final_report_html=html_without_section)
    try:
        export_svc.import_prompt2_result(client, "run-1", zip_bytes)
        assert False, "should have raised"
    except ValueError as exc:
        assert "exec-summary" in str(exc)


def test_import_prompt2_result_records_warning_for_empty_field_but_does_not_reject():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client, prompt2_contract_version="evidence_intelligence_v1")
    html_empty_field = (
        '<html><body><section data-section-id="exec-summary">'
        '<div data-field="central_conclusion"></div>'
        '</section></body></html>'
    )
    zip_bytes = _build_ei_prompt2_output_zip_bytes(final_report_html=html_empty_field)

    result = export_svc.import_prompt2_result(client, "run-1", zip_bytes)
    assert result["section_coverage_warnings"] == [
        {"section_id": "exec-summary", "field": "central_conclusion", "reason": "empty"}]
    audit = client.select("geo_report_run_artifacts", {
        "select": "*", "execution_id": f"eq.{result['execution_id']}",
        "file_name": "eq.section_coverage_audit.json",
    })
    assert len(audit) == 1


def test_import_prompt2_result_insufficient_evidence_text_is_not_a_warning():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client, prompt2_contract_version="evidence_intelligence_v1")
    html_insufficient = (
        '<html><body><section data-section-id="exec-summary">'
        '<div data-field="central_conclusion">Insufficient evidence</div>'
        '</section></body></html>'
    )
    zip_bytes = _build_ei_prompt2_output_zip_bytes(final_report_html=html_insufficient)

    result = export_svc.import_prompt2_result(client, "run-1", zip_bytes)
    assert result["section_coverage_warnings"] == []


def test_import_prompt2_result_missing_field_is_recorded_as_warning():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client, prompt2_contract_version="evidence_intelligence_v1")
    html_missing_field = '<html><body><section data-section-id="exec-summary"></section></body></html>'
    zip_bytes = _build_ei_prompt2_output_zip_bytes(final_report_html=html_missing_field)

    result = export_svc.import_prompt2_result(client, "run-1", zip_bytes)
    assert result["section_coverage_warnings"] == [
        {"section_id": "exec-summary", "field": "central_conclusion", "reason": "missing"}]


# ---------------------------------------------------------------------------
# Combined ChatGPT Session（Prompt1+Prompt2を1セッションで通しで実行、2026-09-16追加）
# ---------------------------------------------------------------------------

def _seed_preprocess_only(client, run_id="run-1", *, preprocess_execution_id="exec-preprocess-1"):
    """Prompt1がまだ存在しない（Preprocessのみ完了した）状態を作る。"""
    client.insert("geo_report_executions", [{
        "id": preprocess_execution_id, "run_id": run_id, "stage": "preprocess",
        "execution_method": "python", "status": "completed",
    }])
    client.update("geo_report_collection_runs", {"run_id": f"eq.{run_id}"}, {
        "canonical_preprocess_execution_id": preprocess_execution_id,
    })
    return preprocess_execution_id


def test_export_combined_package_requires_preprocess_complete():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client)
    try:
        export_svc.export_combined_prompt1_prompt2_package(client, "run-1")
        assert False, "should have raised"
    except ValueError as exc:
        assert "Preprocess" in str(exc)


def test_export_combined_package_bundles_both_prompt_inputs_and_instructions():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client)
    _seed_prompt1_input_artifacts(client)
    _seed_prompt_snapshots(client)
    _seed_records(client, count=2)
    _seed_preprocess_only(client)

    zip_bytes = export_svc.export_combined_prompt1_prompt2_package(client, "run-1")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = set(zf.namelist())
        assert names == {
            "prompt1_input_run-1.zip", "prompt2_base_input_run-1.zip", "report_template.html",
            "report_template.json", "prompt1_instruction.md", "prompt2_instruction.md",
            "README_combined_session.md",
        }
        assert zf.read("prompt1_instruction.md").decode("utf-8") == "# prompt1_instruction.md"
        assert zf.read("prompt2_instruction.md").decode("utf-8") == "# prompt2_instruction.md"
        with zipfile.ZipFile(io.BytesIO(zf.read("prompt1_input_run-1.zip"))) as inner:
            assert set(inner.namelist()) == set(export_svc.PROMPT1_INPUT_REQUIRED_FILES)


def test_export_combined_package_works_without_canonical_prompt1():
    """discovery系のProvenance解決が、Canonical Prompt1が存在しない状態でも
    run.canonical_preprocess_execution_idへフォールバックして動くことを確認する
    （_resolve_canonical_preprocess_execution_idの2026-09-16修正）。"""
    client = FakeSupabaseClient()
    _seed_run_and_assets(client)
    _seed_prompt1_input_artifacts(client)
    _seed_records(client, count=2)
    _seed_preprocess_only(client)
    # discovery_signal_idsを設定しない（無ければスキップされるだけで、エラーにはならない）

    zip_bytes = export_svc.build_prompt2_base_input_zip(client, "run-1")
    assert zip_bytes  # 例外なく生成できる


def test_import_combined_prompt1_prompt2_result_auto_adopts_both_canonicals():
    client = FakeSupabaseClient()
    _seed_run_and_assets(client)
    prompt1_zip = _build_prompt1_output_zip_bytes()
    prompt2_zip = _build_prompt2_output_zip_bytes()

    result = export_svc.import_combined_prompt1_prompt2_result(client, "run-1", prompt1_zip, prompt2_zip)

    run = client.select("geo_report_collection_runs", {"select": "*", "run_id": "eq.run-1"})[0]
    assert run["canonical_prompt1_execution_id"] == result["prompt1_execution_id"]
    assert run["canonical_prompt2_execution_id"] == result["prompt2_execution_id"]
    assert run["canonical_report_execution_id"] == result["prompt2_execution_id"]
