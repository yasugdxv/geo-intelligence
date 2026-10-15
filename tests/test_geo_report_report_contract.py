"""Report Type(report_kind)・Prompt/Templateのrouting・日付semantics・primary_graph_signalsの回帰テスト（2026-10-05）。

US中間選挙のRunに、消費税・食品値上げ用のPrompt 2が渡っていた不具合の再発防止。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_current_pipeline_service as cp  # noqa: E402
import geo_report_profile_registry as registry  # noqa: E402
import geo_report_prompt_export_service as export_svc  # noqa: E402
import geo_report_run_provisioning_service as provisioning  # noqa: E402
from tests.test_geo_report_review_reasons_and_lint_integration import master_rows as base_master_rows  # noqa: E402
from tests.test_geo_report_review_reasons_and_lint_integration import news, write  # noqa: E402
from tests.test_geo_report_run_provisioning_service import PinningFakeClient  # noqa: E402

US_SECTIONS = [{"order": 1, "section_id": "sec_01_overview"}, {"order": 2, "section_id": "sec_02_electoral_position"},
               {"order": 4, "section_id": "sec_04_candidate_narratives"}]
CONSUMPTION_SECTIONS = [{"order": 1, "section_id": "sec_summary"}, {"order": 2, "section_id": "sec_voices"}]
SIGNAL_IDS = [f"S{i}" for i in range(1, 18)]


def master_17():
    rows = []
    for sid in SIGNAL_IDS:
        for row in base_master_rows():
            rows.append(dict(row, signal_id=sid, theme_id=f"T_{sid}", theme_name=f"Race {sid}",
                             trend_role="primary", trend_start_date="2026-07-01",
                             narrative_id=row["narrative_id"].replace("S1", sid)))
    return rows


def make_run(report_design, *, sections=US_SECTIONS, rows=None, design_signals=None, period=("2026-09-07", "2026-09-30")):
    client = PinningFakeClient()
    pid = "proj-1"
    client.insert("geo_report_project_assets", [
        {"id": "nm-1", "project_id": pid, "asset_type": "narrative_master", "status": "published",
         "content": {"rows": rows if rows is not None else master_17()}},
        {"id": "rt-1", "project_id": pid, "asset_type": "report_template", "status": "published",
         "content": {"sections": sections}}])
    client.insert("geo_report_design_versions", [{
        "id": "dv-1", "project_id": pid, "version": 1, "report_design": report_design,
        "signals": design_signals or [], "queries": [],
        "narrative_master_asset_id": "nm-1", "report_template_asset_id": "rt-1"}])
    run = provisioning.create_run_with_pinned_snapshot(client, pid, period_start=period[0], period_end=period[1])
    config = client.select("geo_report_run_config", {"select": "config_json", "run_id": f"eq.{run['run_id']}"})[0]["config_json"]
    return client, run, config


def pinned_prompt(client, run, name):
    rows = client.select("geo_report_run_artifacts", {"select": "content_text", "run_id": f"eq.{run['run_id']}",
                                                      "artifact_type": "eq.prompt_snapshot", "file_name": f"eq.{name}"})
    return rows[0]["content_text"]


US_DESIGN = {"analysis_mode": "public_voice_monitor", "report_kind": "us_midterm_elections",
             "historical_trend_start_date": "2026-07-01"}


# --- Test A: US Midterm routing -------------------------------------------------------------------------------
def test_us_midterm_run_gets_us_prompt2_and_us_profile():
    client, run, config = make_run(US_DESIGN)
    prompt2 = pinned_prompt(client, run, "prompt2_instruction.md")
    prompt1 = pinned_prompt(client, run, "prompt1_instruction.md")
    assert run["prompt2_contract_version"] == "us_midterm_v4.3"
    for forbidden in ("消費税", "食品値上げ", "2026-05-01", "S1 または S7", "S1・S7"):
        assert forbidden not in prompt2
    assert "targeted_stance_classified" in prompt2 and "Candidate Favorability" in prompt2
    assert "targeted_stance_classified" in prompt1
    assert "v4.3" in prompt1.splitlines()[0] and "v4.2" not in prompt1.splitlines()[0]      # title/versionの不一致を直した
    assert (config["report_kind"], config["prompt2_profile"], config["template_id"]) == (
        "us_midterm_elections", "us_midterm_elections_prompt2_v4_3", "us_midterm_state_races")
    assert config["analysis_contract"] == "public_voice_monitor_v4_3"      # 分析方式とReport Typeは別の値
    assert config["template_asset_id"] == "rt-1" and len(config["template_hash"]) == 64


# --- Test B: Consumption tax routing --------------------------------------------------------------------------
def test_consumption_tax_run_gets_consumption_prompt2_not_us():
    client, run, config = make_run({"analysis_mode": "public_voice_monitor", "report_kind": "consumption_tax_food_price"},
                                   sections=CONSUMPTION_SECTIONS)
    prompt2 = pinned_prompt(client, run, "prompt2_instruction.md")
    assert run["prompt2_contract_version"] == "v4.3" and "消費税" in prompt2
    assert "Candidate Favorability" not in prompt2 and "Electoral Position" not in prompt2
    assert config["report_kind"] == "consumption_tax_food_price" and config["prompt2_profile"].startswith("consumption_tax")


# --- Test C: Missing / unknown profile fails fast, never falls back ----------------------------------------------
@pytest.mark.parametrize("design", [
    {"analysis_mode": "public_voice_monitor"},                                                # report_kind無し
    {"analysis_mode": "public_voice_monitor", "report_kind": "something_new"},                # 未登録
    {"analysis_mode": "public_voice_monitor", "report_kind": "evidence_intelligence"},        # analysis_modeと不一致
])
def test_missing_or_unknown_report_kind_fails_fast_without_default_prompt(design):
    with pytest.raises(ValueError, match=registry.PROFILE_NOT_FOUND):
        make_run(design)


def test_evidence_mode_resolves_its_own_profile_without_report_kind():
    client, run, config = make_run({"analysis_mode": "evidence_intelligence", "primary_graph_signals": []},
                                   sections=CONSUMPTION_SECTIONS)
    assert config["report_kind"] == "evidence_intelligence" and run["prompt2_contract_version"] == "evidence_intelligence_v1"


def test_failed_pin_leaves_no_orphaned_run_when_profile_not_found():
    client = PinningFakeClient()
    client.insert("geo_report_design_versions", [{
        "id": "dv-1", "project_id": "p", "version": 1, "report_design": {"analysis_mode": "public_voice_monitor"},
        "signals": [], "queries": [], "narrative_master_asset_id": "nm-1", "report_template_asset_id": "rt-1"}])
    with pytest.raises(ValueError):
        provisioning.create_run_with_pinned_snapshot(client, "p")
    assert client.select("geo_report_collection_runs", {"select": "run_id"}) == []


# --- Test D: observation window vs historical baseline ----------------------------------------------------------
def test_observation_window_and_historical_baseline_are_separate_concepts():
    client, run, config = make_run(US_DESIGN)
    assert (config["observation_start_date"], config["observation_end_date"]) == ("2026-09-07", "2026-09-30")
    assert config["historical_trend_start_date"] == "2026-07-01"
    assert config["primary_chart_start"] == config["common_primary_graph_start"] == "2026-09-07"   # max(07-01, 09-07)


def test_run_without_observation_window_keeps_historical_start_as_chart_start():
    client, run, config = make_run(US_DESIGN, period=(None, None))
    assert config["historical_trend_start_date"] == "2026-07-01" and config["primary_chart_start"] == "2026-07-01"
    assert config["observation_start_date"] is None


# --- Test E: unavailable history cannot silently enter the chart -----------------------------------------------
def _preprocess(tmp_path, dates, **kw):
    rows = []
    for i, d in enumerate(dates):
        row = news(i, f"alpha topic details number{i}x")
        row["timestamp"] = f"{d}T10:00:00Z"
        rows.append(row)
    write(tmp_path / "S1_x.csv", rows)
    master = base_master_rows()
    for row in master:
        row.update(trend_role="primary", trend_start_date="2026-07-01")
    return cp.run_preprocess([str(tmp_path / "S1_x.csv")], master, **kw)


def test_chart_start_never_precedes_observation_window_or_available_data(tmp_path):
    result = _preprocess(tmp_path, ["2026-09-07", "2026-09-15", "2026-09-30"],
                         period_start="2026-09-07", period_end="2026-09-30")
    assert result.historical_trend_start == "2026-07-01"                 # 履歴の基準は保持
    assert result.common_primary_graph_start == "2026-09-07"            # 7/1・8/1のデータは無いのでグラフに含めない
    scope = cp.build_monitoring_scope_rows(result, run_period_start="2026-09-07", run_period_end="2026-09-30")[0]
    assert scope["configured_start_date"] == "2026-07-01"                # 設定値は設定値のまま
    assert (scope["effective_start_date"], scope["effective_start_source"]) == ("2026-09-07", "run_period_start")
    assert (scope["observation_start_date"], scope["observation_end_date"], scope["historical_trend_start_date"]) == (
        "2026-09-07", "2026-09-30", "2026-07-01")


def test_chart_start_moves_to_first_data_date_when_data_starts_after_window(tmp_path):
    result = _preprocess(tmp_path, ["2026-09-10", "2026-09-20"], period_start="2026-09-07", period_end="2026-09-30")
    assert result.common_primary_graph_start == "2026-09-10"


def test_yaml_records_report_type_dates_and_profiles(tmp_path):
    result = _preprocess(tmp_path, ["2026-09-07", "2026-09-30"], period_start="2026-09-07", period_end="2026-09-30")
    _, _, config = make_run(US_DESIGN)
    text = cp.build_report_config_yaml(result, report_id="r", topic=config["report_title"],
                                       primary_graph_signals=config["primary_graph_signals"], final_graph_rule="x",
                                       created_at="t", analysis_mode="public_voice_monitor",
                                       analysis_contract=config["analysis_contract"], run_config=config)
    for expected in ("report_kind: us_midterm_elections", "report_title: US Midterm Election Monitor",
                     "prompt2_profile: us_midterm_elections_prompt2_v4_3", "observation_start_date: 2026-09-07",
                     "observation_end_date: 2026-09-30", "historical_trend_start_date: 2026-07-01",
                     "primary_chart_start: 2026-09-07", "primary_graph_signals_source: narrative_master_trend_role_primary"):
        assert expected in text, expected
    assert "primary_graph_signals: S1,S2" in text                           # 空にならない


# --- Test F: Prompt semantic mismatch ----------------------------------------------------------------------------
def _consumption_prompt2():
    return provisioning.load_prompt_instruction("prompt2", version="v4.3")["text"]


def _us_prompt2():
    return provisioning.load_prompt_instruction("prompt2", version="us_midterm_v4.3")["text"]


def test_us_run_with_consumption_prompt_fails_contract_validation():
    _, _, config = make_run(US_DESIGN)
    issues = registry.validate_run_contract(config, prompt2_text=_consumption_prompt2())
    codes = {i["code"] for i in registry.errors_only(issues)}
    assert "prompt_profile_mismatch" in codes and "prompt_profile_missing_expected" in codes


def test_consumption_run_with_us_prompt_fails_contract_validation():
    _, _, config = make_run({"analysis_mode": "public_voice_monitor", "report_kind": "consumption_tax_food_price"},
                            sections=CONSUMPTION_SECTIONS)
    assert registry.errors_only(registry.validate_run_contract(config, prompt2_text=_us_prompt2()))


def test_matching_prompts_pass_contract_validation():
    _, _, config = make_run(US_DESIGN)
    assert not registry.errors_only(registry.validate_run_contract(
        config, prompt1_text=provisioning.load_prompt_instruction("prompt1")["text"], prompt2_text=_us_prompt2(),
        template_content={"sections": US_SECTIONS}))


def test_pin_fails_when_loaded_prompt_belongs_to_another_report_kind(monkeypatch):
    original = provisioning.load_prompt_instruction

    def wrong_loader(stage, *, version=None):
        return original(stage, version="v4.3")                                # 消費税用を常に返す不具合を再現

    monkeypatch.setattr(provisioning, "load_prompt_instruction", wrong_loader)
    with pytest.raises(ValueError, match="Report Contract"):
        make_run(US_DESIGN)


def test_template_without_required_sections_is_rejected_for_us_profile():
    with pytest.raises(ValueError, match="template_profile_mismatch"):
        make_run(US_DESIGN, sections=CONSUMPTION_SECTIONS)


# --- Test G: primary_graph_signals is explicitly resolved ---------------------------------------------------------
def test_all_17_races_resolve_primary_graph_signals_explicitly():
    client, run, config = make_run(US_DESIGN)
    assert config["primary_graph_signals"] == SIGNAL_IDS and config["signals"] == SIGNAL_IDS
    assert config["primary_graph_signals_source"] == "narrative_master_trend_role_primary"
    assert config["signals_source"] == "narrative_master"
    assert "primary_graph_signals" not in (run.get("prompt2_config_warnings") or [])


def test_primary_graph_signals_resolution_sources():
    resolve = provisioning.resolve_primary_graph_signals
    assert resolve({"primary_graph_signals": ["S2"]}, [], master_17()) == (["S2"], "design")
    assert resolve({"primary_graph_signals": []}, [], master_17()) == ([], "design_explicit_empty")
    assert resolve({}, [{"signal_id": "S3", "continuity_role": "Core"}], master_17()) == (["S3"], "design_core_autofill")
    assert resolve({}, [], [dict(r, trend_role="secondary") for r in master_17()]) == (None, "unresolved")


def test_unresolved_primary_graph_signals_is_rejected_before_packaging():
    _, _, config = make_run(US_DESIGN, rows=[dict(r, trend_role="secondary") for r in master_17()])
    assert config["primary_graph_signals"] == [] and config["primary_graph_signals_source"] == "unresolved"
    codes = {i["code"] for i in registry.validate_run_contract(config)}
    assert "primary_graph_signals_unresolved" in codes                        # 『空=全Signal』の暗黙仕様は使わない


# --- contract validation: dates / signals / report type -----------------------------------------------------------
def good_scope(**overrides):
    row = {"signal_source": "S1", "actual_min_date": "2026-09-07", "actual_max_date": "2026-09-30",
           "effective_start_date": "2026-09-07", "common_primary_graph_start": "2026-09-07"}
    row.update(overrides)
    return [row]


def test_good_run_passes_and_date_contradictions_fail():
    _, run, config = make_run(US_DESIGN)
    period = ("2026-09-07", "2026-09-30")
    assert not registry.errors_only(registry.validate_run_contract(config, monitoring_scope_rows=good_scope(), run_period=period))
    for override, code in [({"actual_min_date": "2026-07-01"}, "data_before_observation_window"),
                           ({"actual_max_date": "2026-10-02"}, "data_after_observation_window"),
                           ({"effective_start_date": "2026-07-01"}, "effective_start_before_observation_window"),
                           ({"common_primary_graph_start": "2026-07-01"}, "chart_start_before_observation_window"),
                           ({"signal_source": "S99"}, "signals_mismatch")]:
        codes = {i["code"] for i in registry.validate_run_contract(config, monitoring_scope_rows=good_scope(**override), run_period=period)}
        assert code in codes, code
    assert "observation_window_mismatch" in {i["code"] for i in registry.validate_run_contract(
        config, run_period=("2026-09-01", "2026-09-30"))}


def test_run_without_report_kind_fails_contract_validation():
    codes = {i["code"] for i in registry.validate_run_contract({"analysis_contract": "x", "signals": ["S1"],
                                                                 "primary_graph_signals": ["S1"]})}
    assert "report_kind_missing" in codes


# --- export hook / README -----------------------------------------------------------------------------------------
def test_export_assert_report_contract_blocks_legacy_run_and_passes_valid_run(monkeypatch):
    monkeypatch.setattr(export_svc, "REPORT_CONTRACT_VALIDATION_ENABLED", True)
    client, run, config = make_run(US_DESIGN)
    run_row = client.select("geo_report_collection_runs", {"select": "*", "run_id": f"eq.{run['run_id']}"})[0]
    export_svc._assert_report_contract(client, run["run_id"], run_row, monitoring_scope_csv=None)    # 例外なし
    # 旧Run相当（report_kindが固定されていない）は止まる
    client.update("geo_report_run_config", {"run_id": f"eq.{run['run_id']}"}, {"config_json": {"signals": ["S1"]}})
    with pytest.raises(ValueError, match="Report Contract"):
        export_svc._assert_report_contract(client, run["run_id"], run_row)


def test_export_blocks_run_whose_pinned_prompt_is_for_another_report(monkeypatch):
    monkeypatch.setattr(export_svc, "REPORT_CONTRACT_VALIDATION_ENABLED", True)
    client, run, config = make_run(US_DESIGN)
    run_row = client.select("geo_report_collection_runs", {"select": "*", "run_id": f"eq.{run['run_id']}"})[0]
    client.update("geo_report_run_artifacts", {"run_id": f"eq.{run['run_id']}", "file_name": "eq.prompt2_instruction.md"},
                  {"content_text": _consumption_prompt2()})                                            # 事故の再現
    with pytest.raises(ValueError, match="prompt_profile_mismatch"):
        export_svc._assert_report_contract(client, run["run_id"], run_row)


def test_readme_note_is_run_aware_and_empty_for_legacy_runs():
    client, run, config = make_run(US_DESIGN)
    run_row = client.select("geo_report_collection_runs", {"select": "*", "run_id": f"eq.{run['run_id']}"})[0]
    note = export_svc._run_aware_readme_note(client, run["run_id"], run_row)
    for expected in ("us_midterm_elections", "us_midterm_elections_prompt2_v4_3", "us_midterm_state_races",
                     "2026-09-07 - 2026-09-30", "Historical trend baseline: 2026-07-01", "Primary chart start: 2026-09-07",
                     "Configured Signals (17)", "narrative_master_trend_role_primary"):
        assert expected in note, expected
    client.update("geo_report_run_config", {"run_id": f"eq.{run['run_id']}"}, {"config_json": {"signals": ["S1"]}})
    assert export_svc._run_aware_readme_note(client, run["run_id"], run_row) == ""


def test_legacy_evidence_run_without_report_kind_is_allowed_with_warning_but_legacy_public_voice_run_is_not():
    legacy_evidence = {"analysis_mode": "evidence_intelligence", "signals": ["S1"], "primary_graph_signals": []}
    issues = registry.validate_run_contract(legacy_evidence)
    assert not registry.errors_only(issues) or {i["code"] for i in registry.errors_only(issues)} <= {"primary_graph_signals_unresolved"}
    assert "report_kind_not_recorded_legacy" in {i["code"] for i in issues}
    legacy_public_voice = {"analysis_mode": "public_voice_monitor", "signals": ["S1"], "primary_graph_signals": ["S1"]}
    assert "report_kind_missing" in {i["code"] for i in registry.errors_only(registry.validate_run_contract(legacy_public_voice))}


@pytest.mark.parametrize("kind", ["us_midterm_elections", "consumption_tax_food_price"])
def test_prompt2_instruction_names_every_file_the_import_requires(kind):
    """Prompt 2の出力ZIPはImport（import_prompt2_result）が必須ファイル名を要求する。指示文がその名前を
    全て含んでいないと、ChatGPTの出力を取り込めない（US用Prompt 2の初版で、このずれが起きかけた）。"""
    profile = registry.PROFILES[kind]
    text = provisioning.load_prompt_instruction("prompt2", version=profile["prompt2_resource_version"])["text"]
    missing = [name for name in export_svc.PROMPT2_OUTPUT_REQUIRED_FILES if name not in text]
    assert not missing, missing
