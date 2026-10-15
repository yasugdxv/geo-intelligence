import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_project_assets_service as assets  # noqa: E402
import geo_report_run_provisioning_service as provisioning  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _valid_master_rows():
    return [
        {
            "signal_id": "S1", "narrative_id": "S1-01", "narrative_name": "Narrative A",
            "trend_role": "primary", "trend_start_date": "2026-07-01",
            "include_keywords": "alpha", "is_fallback": "false", "active": "true",
        },
        {
            "signal_id": "S1", "narrative_id": "S1-99", "narrative_name": "Fallback",
            "trend_role": "", "trend_start_date": "",
            "include_keywords": "", "is_fallback": "true", "active": "true",
        },
        {
            "signal_id": "S2", "narrative_id": "S2-01", "narrative_name": "Narrative B",
            "trend_role": "", "trend_start_date": "",
            "include_keywords": "beta", "is_fallback": "false", "active": "true",
        },
        {
            "signal_id": "S2", "narrative_id": "S2-99", "narrative_name": "Fallback",
            "trend_role": "", "trend_start_date": "",
            "include_keywords": "", "is_fallback": "true", "active": "true",
        },
    ]


def test_sync_shell_mirror_columns_fills_both_directions():
    row = {"signal_id": "s1", "narrative_id": "S1-01", "narrative_name": "N"}
    synced = assets.sync_shell_mirror_columns(row)
    assert synced["signal_source"] == "S1"
    assert synced["sub_narrative_id"] == "S1-01"
    assert synced["sub_narrative"] == "N"

    # Shell側だけが埋まっている場合も逆方向に補完される
    row2 = {"signal_source": "S3", "sub_narrative_id": "S3-01", "sub_narrative": "N2"}
    synced2 = assets.sync_shell_mirror_columns(row2)
    assert synced2["signal_id"] == "S3"
    assert synced2["narrative_id"] == "S3-01"
    assert synced2["narrative_name"] == "N2"


def test_csv_round_trip_preserves_rows():
    rows = _valid_master_rows()
    csv_text = assets.narrative_master_rows_to_csv_text(rows)
    assert csv_text.splitlines()[0] == ",".join(assets.NARRATIVE_MASTER_COLUMNS)
    parsed = assets.parse_narrative_master_csv(csv_text)
    assert len(parsed) == 4
    assert {r["narrative_id"] for r in parsed} == {"S1-01", "S1-99", "S2-01", "S2-99"}
    assert parsed[0]["include_keywords"] == "alpha"


def test_build_bs_query_plan_csv_uses_design_chat_schema_field_names_directly():
    # geo_report_design_chat_service.CHAT_TURN_SCHEMA["queries"]と完全一致する列名
    # ・配列フィールドをそのままserializeできることを確認する(変換ロジック無し)。
    queries = [
        {
            "signal_id": "S1", "report_topic_label": "Arizona Governor",
            "fixed_query_group_a": ["Katie Hobbs", "Arizona governor"],
            "fixed_query_group_b": ["election"], "event_terms": ["debate"],
            "exclude_terms": ["sports"], "continuity_role": "Core",
            "collection_notes": "primary race",
        },
        {
            "signal_id": "S2", "report_topic_label": "Georgia Senate",
            "fixed_query_group_a": [], "fixed_query_group_b": [], "event_terms": [],
            "exclude_terms": [], "continuity_role": "Watch", "collection_notes": "",
        },
    ]
    csv_text = assets.build_bs_query_plan_csv(queries)
    lines = csv_text.splitlines()
    assert lines[0] == ",".join(assets.BS_QUERY_PLAN_COLUMNS)
    assert "S1" in lines[1] and "Katie Hobbs|Arizona governor" in lines[1]
    assert lines[2].startswith("S2,Georgia Senate,,,,,Watch,")


def test_build_bs_query_plan_csv_handles_empty_queries():
    csv_text = assets.build_bs_query_plan_csv([])
    assert csv_text.splitlines() == [",".join(assets.BS_QUERY_PLAN_COLUMNS)]


def test_validate_narrative_master_passes_on_valid_master():
    errors = assets.validate_narrative_master(_valid_master_rows())
    assert errors == []


def test_validate_narrative_master_detects_duplicate_narrative_id():
    rows = _valid_master_rows()
    rows.append({"signal_id": "S3", "narrative_id": "S1-01", "narrative_name": "dup",
                 "is_fallback": "false", "active": "true"})
    errors = assets.validate_narrative_master(rows)
    assert any("重複" in e for e in errors)


def test_validate_narrative_master_detects_missing_fallback():
    rows = [r for r in _valid_master_rows() if r["narrative_id"] != "S1-99"]
    errors = assets.validate_narrative_master(rows)
    assert any("Signal S1" in e and "fallback" in e for e in errors)


def test_validate_narrative_master_detects_extra_fallback():
    rows = _valid_master_rows()
    rows.append({"signal_id": "S1", "narrative_id": "S1-98", "narrative_name": "second fallback",
                 "is_fallback": "true", "active": "true"})
    errors = assets.validate_narrative_master(rows)
    assert any("Signal S1" in e and "2個" in e for e in errors)


def test_validate_narrative_master_detects_invalid_primary_trend_start_date():
    rows = _valid_master_rows()
    rows[0]["trend_start_date"] = "not-a-date"
    errors = assets.validate_narrative_master(rows)
    assert any("trend_role=primary" in e for e in errors)


def test_validate_narrative_master_detects_comma_separated_list_fields():
    """2026-09-15追加、外部監査で発覚した重大バグの再現テスト: split_phrasesは
    セミコロン(;)のみで分割しカンマは区切り文字として扱わないため、AIがカンマ区切りで
    include_keywords等を生成すると、そのSignalは1レコードもマッチせず100% fallback
    になる（実データで確認済み）。セミコロンが無くカンマがある値をPublish Gateで検知
    してブロックすること。"""
    rows = _valid_master_rows()
    rows[0]["include_keywords"] = "red line, bottom line, core interest"
    errors = assets.validate_narrative_master(rows)
    assert any("カンマ区切り" in e and "include_keywords" in e for e in errors)


def test_validate_narrative_master_allows_semicolon_separated_list_fields():
    rows = _valid_master_rows()
    rows[0]["include_keywords"] = "red line; bottom line; core interest"
    errors = assets.validate_narrative_master(rows)
    assert errors == []


def test_validate_narrative_master_allows_single_phrase_with_no_delimiter():
    """区切り文字が無い単一フレーズ（カンマもセミコロンも無い）は誤検知しないこと。"""
    rows = _valid_master_rows()
    rows[0]["include_keywords"] = "managed competition"
    errors = assets.validate_narrative_master(rows)
    assert errors == []


def test_validate_narrative_master_empty_rows():
    assert assets.validate_narrative_master([]) == ["Narrative Masterに行がありません。"]


# ---------------------------------------------------------------------------
# normalize_list_field_delimiters（2026-09-16追加、Phase4）: comma/semicolon/
# mixed inputをすべてcanonical（セミコロン区切り）へ正規化する。
# ---------------------------------------------------------------------------

def test_normalize_list_field_delimiters_comma_input():
    row = {"include_keywords": "red line, bottom line, core interest"}
    normalized = assets.normalize_list_field_delimiters(row)
    assert normalized["include_keywords"] == "red line; bottom line; core interest"


def test_normalize_list_field_delimiters_semicolon_input_is_canonical_already():
    row = {"include_keywords": "stabilization; guardrails; managed competition"}
    normalized = assets.normalize_list_field_delimiters(row)
    assert normalized["include_keywords"] == "stabilization; guardrails; managed competition"


def test_normalize_list_field_delimiters_mixed_input():
    row = {"include_keywords": "red line, bottom line; core interest,sovereignty"}
    normalized = assets.normalize_list_field_delimiters(row)
    assert normalized["include_keywords"] == "red line; bottom line; core interest; sovereignty"


def test_normalize_list_field_delimiters_applies_to_all_four_list_fields():
    row = {
        "include_keywords": "a,b", "required_context_keywords": "c,d",
        "exclude_keywords": "e,f", "cluster_split_terms": "g,h",
    }
    normalized = assets.normalize_list_field_delimiters(row)
    assert normalized["include_keywords"] == "a; b"
    assert normalized["required_context_keywords"] == "c; d"
    assert normalized["exclude_keywords"] == "e; f"
    assert normalized["cluster_split_terms"] == "g; h"


def test_normalize_list_field_delimiters_leaves_other_fields_untouched():
    row = {"narrative_name": "Some, name, with, commas", "include_keywords": "a,b"}
    normalized = assets.normalize_list_field_delimiters(row)
    assert normalized["narrative_name"] == "Some, name, with, commas"


def test_save_narrative_master_draft_normalizes_comma_separated_fields_and_publish_gate_passes():
    """2026-09-16追加、S3実データバグの再現・修正確認: カンマ区切りのInclude
    Keywords等を持つ行をDraft保存すると、自動的にセミコロン区切りへ正規化され、
    Publish Gate（validate_narrative_master）のdelimiter不整合チェックにも
    引っかからないこと（=保存時点で根本原因が解消されること）。"""
    rows = _valid_master_rows()
    rows[0]["include_keywords"] = "red line, bottom line, core interest, sovereignty"
    rows[0]["required_context_keywords"] = "Taiwan, cross-strait, US-China"

    client = FakeSupabaseClient()
    asset = assets.save_narrative_master_draft(client, "proj-1", rows)
    saved_row = asset["content"]["rows"][0]
    assert saved_row["include_keywords"] == "red line; bottom line; core interest; sovereignty"
    assert saved_row["required_context_keywords"] == "Taiwan; cross-strait; US-China"
    assert assets.validate_narrative_master(asset["content"]["rows"]) == []


def test_save_narrative_master_draft_rejects_invalid():
    client = FakeSupabaseClient()
    bad_rows = [{"signal_id": "", "narrative_id": "", "is_fallback": "false", "active": "true"}]
    try:
        assets.save_narrative_master_draft(client, "proj-1", bad_rows)
        assert False, "should have raised"
    except ValueError as exc:
        assert "検証エラー" in str(exc)


def test_save_narrative_master_draft_upserts_single_draft_row():
    client = FakeSupabaseClient()
    asset1 = assets.save_narrative_master_draft(client, "proj-1", _valid_master_rows())
    assert asset1["status"] == "draft"
    assert len(client.tables["geo_report_project_assets"]) == 1

    updated_rows = _valid_master_rows()
    updated_rows[0]["include_keywords"] = "alpha;gamma"
    asset2 = assets.save_narrative_master_draft(client, "proj-1", updated_rows)
    # 同じdraft行が更新される（新規行は増えない）
    assert len(client.tables["geo_report_project_assets"]) == 1
    assert asset2["id"] == asset1["id"]

    loaded = assets.get_draft(client, "proj-1", "narrative_master")
    # 2026-09-16追加: 保存時にセミコロン区切りの正規形（"; "区切り）へ統一される
    # （normalize_list_field_delimiters、Phase4）。
    assert loaded["content"]["rows"][0]["include_keywords"] == "alpha; gamma"


def test_download_upload_narrative_master_csv_round_trip():
    client = FakeSupabaseClient()
    assets.save_narrative_master_draft(client, "proj-1", _valid_master_rows())
    csv_text = assets.download_narrative_master_csv(client, "proj-1", status="draft")

    client2 = FakeSupabaseClient()
    asset = assets.upload_narrative_master_csv(client2, "proj-1", csv_text)
    assert asset["row_count"] == 4


def test_diff_narrative_master_detects_added_removed_changed():
    old_rows = _valid_master_rows()
    new_rows = [dict(r) for r in old_rows]
    new_rows[0]["include_keywords"] = "alpha;delta"  # changed
    new_rows.pop()  # removed S2-99
    new_rows.append({"signal_id": "S3", "narrative_id": "S3-01", "narrative_name": "New",
                      "is_fallback": "false", "active": "true"})  # added

    diff = assets.diff_narrative_master(old_rows, new_rows)
    assert {d["narrative_id"] for d in diff["added"]} == {"S3-01"}
    assert {d["narrative_id"] for d in diff["removed"]} == {"S2-99"}
    assert len(diff["changed"]) == 1
    assert diff["changed"][0]["narrative_id"] == "S1-01"
    assert "include_keywords" in diff["changed"][0]["fields"]


def _valid_template_content():
    return {
        "style_css": "<style>.card{}</style>",
        "shell_html": "<html></html>",
        "sections": [
            {"section_id": "executive-summary", "title": "Executive Summary", "order": 1,
             "component_type": "narrative_block", "required_fields": ["central_conclusion"]},
            {"section_id": "trend-chart", "title": "Trend", "order": 2,
             "component_type": "stacked_bar_chart", "required_fields": ["periods"]},
        ],
    }


def test_validate_report_template_passes_on_valid_content():
    assert assets.validate_report_template(_valid_template_content()) == []


def test_validate_report_template_rejects_unknown_component_type():
    content = _valid_template_content()
    content["sections"][0]["component_type"] = "raw_html_dump"
    errors = assets.validate_report_template(content)
    assert any("未対応" in e for e in errors)


def test_validate_report_template_rejects_duplicate_section_id():
    content = _valid_template_content()
    content["sections"][1]["section_id"] = "executive-summary"
    errors = assets.validate_report_template(content)
    assert any("重複" in e for e in errors)


def test_save_report_template_draft_round_trip():
    client = FakeSupabaseClient()
    asset = assets.save_report_template_draft(client, "proj-1", _valid_template_content())
    assert asset["status"] == "draft"
    assert asset["row_count"] == 2
    loaded = assets.get_draft(client, "proj-1", "report_template")
    assert assets.get_template_sections(loaded)[0]["section_id"] == "executive-summary"


def test_design_state_get_returns_empty_defaults_when_absent():
    client = FakeSupabaseClient()
    state = assets.get_design_state(client, "proj-1")
    assert state["report_design"] == {}
    assert state["signals"] == []
    assert state["queries"] == []


def test_design_state_save_then_get_roundtrip():
    client = FakeSupabaseClient()
    assets.save_design_state(client, "proj-1", report_design={"purpose": "test"},
                              signals=[{"signal_id": "S1"}], queries=[])
    state = assets.get_design_state(client, "proj-1")
    assert state["report_design"] == {"purpose": "test"}
    assert state["signals"] == [{"signal_id": "S1"}]

    # 再保存は同じ行を更新する（新規行を増やさない）
    assets.save_design_state(client, "proj-1", queries=[{"signal_id": "S1", "fixed_query_group_a": ["x"]}])
    assert len(client.tables["geo_report_design_state"]) == 1
    state2 = assets.get_design_state(client, "proj-1")
    assert state2["report_design"] == {"purpose": "test"}  # 既存値は保持される
    assert state2["queries"] == [{"signal_id": "S1", "fixed_query_group_a": ["x"]}]


def test_publish_design_requires_drafts():
    client = FakeSupabaseClient()
    try:
        assets.publish_design(client, "proj-1", "analyst")
        assert False, "should have raised"
    except ValueError as exc:
        assert "Narrative Master" in str(exc)


def test_publish_design_calls_rpc_with_expected_payload():
    client = FakeSupabaseClient()
    assets.save_narrative_master_draft(client, "proj-1", _valid_master_rows())
    assets.save_report_template_draft(client, "proj-1", _valid_template_content())
    assets.save_design_state(client, "proj-1", report_design={"purpose": "p"}, signals=[], queries=[])

    fake_version_row = {
        "id": "dv-1", "project_id": "proj-1", "version": 1,
        "report_design": {"purpose": "p"}, "signals": [], "queries": [],
        "narrative_master_asset_id": "nm-1", "report_template_asset_id": "rt-1",
    }
    client.rpc_responses = {"geo_report_publish_design": fake_version_row}

    result = assets.publish_design(client, "proj-1", "analyst")
    assert result == fake_version_row


def test_publish_design_rejects_unchanged_publish():
    client = FakeSupabaseClient()
    assets.save_narrative_master_draft(client, "proj-1", _valid_master_rows())
    assets.save_report_template_draft(client, "proj-1", _valid_template_content())
    assets.save_design_state(client, "proj-1", report_design={"purpose": "p"}, signals=[], queries=[])

    narrative_draft = assets.get_draft(client, "proj-1", "narrative_master")
    template_draft = assets.get_draft(client, "proj-1", "report_template")
    # 直前のPublishedスナップショットを模す（現Draftと同一内容）
    client.insert("geo_report_project_assets", [{
        "id": "nm-published-1", "project_id": "proj-1", "asset_type": "narrative_master",
        "status": "published", "version": 1, "content": narrative_draft["content"],
    }])
    client.insert("geo_report_project_assets", [{
        "id": "rt-published-1", "project_id": "proj-1", "asset_type": "report_template",
        "status": "published", "version": 1, "content": template_draft["content"],
    }])
    # publish_designはRPC呼び出し前にreport_designへ自動補完(autofill_prompt2_report_config)
    # を適用するため、「変更なし」判定用のPublishedスナップショット側も同じ補完後の値に
    # 揃える必要がある（さもないと自動補完で追加されたfinal_graph_ruleの分だけ差分が
    # 生まれ、意図せず「変更あり」判定になってしまう）。
    published_report_design = provisioning.autofill_prompt2_report_config({"purpose": "p"}, [])
    client.insert("geo_report_design_versions", [{
        "id": "dv-1", "project_id": "proj-1", "version": 1,
        "report_design": published_report_design, "signals": [], "queries": [],
        "narrative_master_asset_id": "nm-published-1", "report_template_asset_id": "rt-published-1",
    }])

    try:
        assets.publish_design(client, "proj-1", "analyst")
        assert False, "should have raised"
    except ValueError as exc:
        assert "変更がありません" in str(exc)


def _seed_full_design(client, project_id="proj-1"):
    assets.save_narrative_master_draft(client, project_id, _valid_master_rows())
    assets.save_report_template_draft(client, project_id, _valid_template_content())
    assets.save_design_state(client, project_id, report_design={"purpose": "p"}, signals=[], queries=[])
    client.insert("geo_report_design_versions", [{
        "id": "dv-1", "project_id": project_id, "version": 1,
        "report_design": {"purpose": "p"}, "signals": [], "queries": [],
        "narrative_master_asset_id": "nm-1", "report_template_asset_id": "rt-1",
    }])
    client.insert("geo_report_design_chat_turns", [{
        "id": "turn-1", "project_id": project_id, "turn_no": 1, "role": "user", "content": "hello",
    }])


def test_get_pinned_run_names_returns_only_runs_with_narrative_master_asset():
    client = FakeSupabaseClient()
    client.insert("geo_report_collection_runs", [
        {"run_id": "run-pinned", "project_id": "proj-1", "run_name": "Pinned Run",
         "narrative_master_asset_id": "nm-1"},
        {"run_id": "run-unpinned", "project_id": "proj-1", "run_name": "Unpinned Run",
         "narrative_master_asset_id": None},
    ])
    names = assets.get_pinned_run_names(client, "proj-1")
    assert names == ["Pinned Run"]


def test_reset_design_rejects_when_pinned_run_exists():
    """2026-09-14発覚: Designを一度Publishした後に間違いに気づいても、消してやり直す
    手段が無かった（ユーザー報告:「デザインを消したり、新規始める方法がない」）。
    ただしPinned Runが残ったままDesign Versionを消すとFK制約で壊れるため、
    まずはRunが残っている間は拒否されることを確認する。"""
    client = FakeSupabaseClient()
    _seed_full_design(client)
    client.insert("geo_report_collection_runs", [
        {"run_id": "run-1", "project_id": "proj-1", "run_name": "USsel Run",
         "narrative_master_asset_id": "nm-1"},
    ])

    try:
        assets.reset_design(client, "proj-1")
        assert False, "should have raised"
    except ValueError as exc:
        assert "USsel Run" in str(exc)

    # 何も削除されていないこと
    assert client.tables.get("geo_report_design_versions")
    assert client.tables.get("geo_report_project_assets")


def test_reset_design_clears_everything_when_no_pinned_run():
    client = FakeSupabaseClient()
    _seed_full_design(client)
    # 未Pin Run（narrative_master_asset_id無し）はReset対象のチェックに影響しない
    client.insert("geo_report_collection_runs", [
        {"run_id": "run-unpinned", "project_id": "proj-1", "run_name": "Unpinned",
         "narrative_master_asset_id": None},
    ])

    assets.reset_design(client, "proj-1")

    assert client.tables.get("geo_report_design_versions", []) == []
    assert client.tables.get("geo_report_project_assets", []) == []
    assert client.tables.get("geo_report_design_state", []) == []
    assert client.tables.get("geo_report_design_chat_turns", []) == []
    # Runは一切削除しない
    assert len(client.tables.get("geo_report_collection_runs", [])) == 1

    state = assets.get_design_state(client, "proj-1")
    assert state["report_design"] == {}
    assert state["signals"] == []


def test_save_pending_design_state_does_not_touch_confirmed_columns():
    """2026-09-15追加: Design Chatの提案→FIXワークフロー。チャット中の提案は
    pending_*列にのみ保存され、confirmed列（report_design/signals/queries）は
    FIXされるまで変わらないことを確認する。"""
    client = FakeSupabaseClient()
    assets.save_pending_design_state(
        client, "proj-1", report_design={"purpose": "draft purpose"},
        signals=[{"signal_id": "S1"}], queries=[{"signal_id": "S1", "report_topic_label": "x"}])

    state = assets.get_design_state(client, "proj-1")
    assert state["report_design"] == {}
    assert state["signals"] == []
    assert state["queries"] == []
    assert state["pending_report_design"] == {"purpose": "draft purpose"}
    assert state["pending_signals"] == [{"signal_id": "S1"}]
    assert state["pending_queries"] == [{"signal_id": "S1", "report_topic_label": "x"}]


def test_fix_design_state_commits_pending_and_clears_pending_columns():
    client = FakeSupabaseClient()
    assets.save_pending_design_state(
        client, "proj-1", report_design={"purpose": "final purpose"},
        signals=[{"signal_id": "S1"}], queries=[{"signal_id": "S1"}])

    state = assets.fix_design_state(client, "proj-1")

    # report_designはfix_design_state内でautofill_prompt2_report_configが適用される
    # ため、明示的に設定したpurposeはそのまま維持しつつfinal_graph_ruleが自動補完される
    # （このテストのsignalsにcontinuity_role='Core'が無いため primary_graph_signals
    # は補完されない）。
    assert state["report_design"]["purpose"] == "final purpose"
    assert state["report_design"]["final_graph_rule"]
    assert state["signals"] == [{"signal_id": "S1"}]
    assert state["queries"] == [{"signal_id": "S1"}]
    assert state["pending_report_design"] is None
    assert state["pending_signals"] is None
    assert state["pending_queries"] is None


def test_fix_design_state_leaves_confirmed_columns_unchanged_when_no_pending():
    """一度もチャットで提案されていない項目（pending=None）はFIXしても既存の
    confirmed値を勝手に消さない（report_designへのautofillによる項目追加は
    「値の上書き」ではないため許容する）。"""
    client = FakeSupabaseClient()
    assets.save_design_state(client, "proj-1", report_design={"purpose": "existing"},
                              signals=[{"signal_id": "S1"}], queries=[{"signal_id": "S1"}])
    # queriesだけpending提案がある状態を模す
    assets.save_pending_design_state(client, "proj-1", queries=[{"signal_id": "S1", "report_topic_label": "y"}])

    state = assets.fix_design_state(client, "proj-1")

    assert state["report_design"]["purpose"] == "existing"  # 既存値は変更なし
    assert state["signals"] == [{"signal_id": "S1"}]  # 変更なし
    assert state["queries"] == [{"signal_id": "S1", "report_topic_label": "y"}]  # FIXされた
