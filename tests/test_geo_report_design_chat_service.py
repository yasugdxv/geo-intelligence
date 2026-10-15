import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_current_pipeline_service as cp  # noqa: E402
import geo_report_design_chat_service as chat_service  # noqa: E402
import geo_report_project_assets_service as assets  # noqa: E402
from geo_report_design_expert_adapter import GeoExpertDesignAdapter  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


class FakeAdapter:
    """テスト用の軽量Adapter（Knowledge/Retrievalを固定文字列にし、実Expert AI
    ファイル読み込み・DB問い合わせを行わない）。"""

    def __init__(self, retrieved: str = ""):
        self._retrieved = retrieved
        self.retrieve_calls = []

    def get_expert_type(self) -> str:
        return "fake"

    def get_knowledge_version(self) -> str:
        return "fake-v1"

    def build_knowledge_context(self) -> str:
        return "FAKE KNOWLEDGE"

    def retrieve_context(self, client, mentioned_entity_names: list) -> str:
        self.retrieve_calls.append(list(mentioned_entity_names))
        return self._retrieved

    def build_design_guidance(self) -> str:
        return "FAKE GUIDANCE"


def _mock_azure_client(response_data: dict):
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(response_data)))],
    )
    return client


def test_geo_expert_design_adapter_satisfies_protocol():
    from geo_report_design_expert_adapter import ReportDesignExpertAdapter
    adapter = GeoExpertDesignAdapter()
    assert isinstance(adapter, ReportDesignExpertAdapter)
    assert adapter.get_expert_type() == "geo"


def test_geo_expert_design_adapter_loads_prompt0_framework():
    adapter = GeoExpertDesignAdapter()
    guidance = adapter.build_design_guidance()
    assert len(guidance) > 500
    assert "signal_narrative_master.csv" in guidance or "Narrative" in guidance


def test_geo_expert_design_adapter_retrieve_context_empty_names_is_safe():
    client = FakeSupabaseClient()
    adapter = GeoExpertDesignAdapter()
    # Media Intelligenceは常に取得を試みるが、Fakeクライアントは空リストを返すだけで例外は起きない
    text = adapter.retrieve_context(client, [])
    assert isinstance(text, str)


def _first_turn_response():
    return {
        "assistant_reply": "承知しました。まずレポートの目的を教えてください。",
        "report_design_patch": {
            "purpose": "選挙情勢モニタリング", "decision_use_case": None, "audience": None,
            "region_scope": None, "period_scope": None, "cadence": None, "central_hypothesis": None,
            "key_questions": None, "analytical_axes": None, "comparison_axes": None, "drivers": None,
            "stakeholders": ["Katie Hobbs"], "monitoring_points": None, "assumptions": None,
            "primary_graph_signals": None, "common_primary_graph_start": None,
        },
        "signals": [{
            "signal_id": "S1", "name": "Arizona Governor", "purpose": "Track AZ governor race",
            "what_to_capture": "Public reaction to candidates", "expected_public_voice": "Mixed",
            "risk_or_relevance": "High salience race", "continuity_role": "Core", "status": "active",
            "discovery_mode": False,
        }],
        "queries": None,
        "mentioned_entity_names": ["Katie Hobbs"],
        "clarifying_questions": ["対象地域は全米ですか、特定州のみですか？"],
        "narrative_master_generation_requested": False,
        "report_template_generation_requested": False,
    }


def test_process_chat_turn_persists_turns_and_updates_design_state():
    """2026-09-15変更: チャットの提案はconfirmed列（report_design/signals/queries）を
    直接書き換えず、pending_*列にのみ保存される（ユーザーが「FIX」ボタンを押すまで
    正式なDesignにはならない、提案→FIXワークフロー）。"""
    client = FakeSupabaseClient()
    azure_client = _mock_azure_client(_first_turn_response())
    adapter = FakeAdapter()

    result = chat_service.process_chat_turn(
        client, azure_client, "gpt-4o", "proj-1", "選挙モニタリングレポートを作りたい", adapter=adapter)

    assert result["assistant_reply"] == "承知しました。まずレポートの目的を教えてください。"
    assert result["clarifying_questions"] == ["対象地域は全米ですか、特定州のみですか？"]

    design_state = assets.get_design_state(client, "proj-1")
    # confirmed列はFIXされるまで空のまま
    assert design_state["report_design"] == {}
    assert design_state["signals"] == []
    # pending列に提案内容が保存されている
    assert design_state["pending_report_design"]["purpose"] == "選挙情勢モニタリング"
    assert design_state["pending_report_design"]["stakeholders"] == ["Katie Hobbs"]
    assert design_state["pending_signals"][0]["signal_id"] == "S1"
    # queriesはNoneだったのでpendingも設定されない
    assert design_state["pending_queries"] is None

    turns = chat_service.list_turns(client, "proj-1")
    assert len(turns) == 2
    assert turns[0]["role"] == "user"
    assert turns[0]["turn_no"] == 1
    assert turns[1]["role"] == "assistant"
    assert turns[1]["turn_no"] == 2
    assert turns[1]["applied_diff"]["signals_replaced"] is True
    assert turns[1]["applied_diff"]["queries_replaced"] is False


def test_process_chat_turn_retrieval_uses_confirmed_stakeholders_from_prior_turn():
    client = FakeSupabaseClient()
    adapter = FakeAdapter(retrieved="ACTOR INTEL: Katie Hobbs...")

    # 1ターン目: stakeholdersはまだ空 → retrieve_contextは空リストで呼ばれる
    chat_service.process_chat_turn(
        client, _mock_azure_client(_first_turn_response()), "gpt-4o", "proj-1",
        "選挙モニタリングレポートを作りたい", adapter=adapter)
    assert adapter.retrieve_calls[0] == []

    # 2ターン目: 前ターンでstakeholders=["Katie Hobbs"]が確定しているので、それが渡される
    second_response = {
        "assistant_reply": "了解です。", "report_design_patch": {f: None for f in [
            "purpose", "decision_use_case", "audience", "region_scope", "period_scope", "cadence",
            "central_hypothesis"]} | {f: None for f in [
            "key_questions", "analytical_axes", "comparison_axes", "drivers", "stakeholders",
            "monitoring_points", "assumptions", "primary_graph_signals", "common_primary_graph_start"]},
        "signals": None, "queries": None, "mentioned_entity_names": [], "clarifying_questions": [],
        "narrative_master_generation_requested": False,
        "report_template_generation_requested": False,
    }
    chat_service.process_chat_turn(
        client, _mock_azure_client(second_response), "gpt-4o", "proj-1", "続けて", adapter=adapter)
    assert adapter.retrieve_calls[1] == ["Katie Hobbs"]


def test_process_chat_turn_increments_turn_numbers_across_multiple_turns():
    client = FakeSupabaseClient()
    adapter = FakeAdapter()
    for i in range(3):
        response = dict(_first_turn_response())
        response["signals"] = None
        response["queries"] = None
        chat_service.process_chat_turn(
            client, _mock_azure_client(response), "gpt-4o", "proj-1", f"message {i}", adapter=adapter)
    turns = chat_service.list_turns(client, "proj-1")
    assert [t["turn_no"] for t in turns] == [1, 2, 3, 4, 5, 6]


def test_generate_narrative_master_rows_for_signal_requires_existing_signal():
    client = FakeSupabaseClient()
    adapter = FakeAdapter()
    try:
        chat_service.generate_narrative_master_rows_for_signal(
            client, _mock_azure_client({"rows": []}), "gpt-4o", "proj-1", "S1", adapter=adapter)
        assert False, "should have raised"
    except ValueError as exc:
        assert "S1" in str(exc)


def test_generate_narrative_master_rows_for_signal_returns_shell_synced_rows():
    client = FakeSupabaseClient()
    adapter = FakeAdapter()
    assets.save_design_state(client, "proj-1", signals=[{
        "signal_id": "S1", "name": "Arizona Governor", "purpose": "p", "what_to_capture": "c",
        "expected_public_voice": "v", "risk_or_relevance": "r", "continuity_role": "Core", "status": "active",
    }], queries=[{
        "signal_id": "S1", "report_topic_label": "Arizona州知事選の論調",
        "fixed_query_group_a": [], "fixed_query_group_b": [], "event_terms": [], "exclude_terms": [],
        "continuity_role": "Core", "collection_notes": "",
    }])

    generated = {
        "rows": [
            {"narrative_id": "S1-01", "narrative_name": "Support", "include_keywords": "hobbs",
             "exclude_keywords": "", "required_context_keywords": "", "public_voice_cues": "",
             "cluster_split_terms": "", "tone_hint": "", "public_voice_hint": "", "report_use_hint": "",
             "continuity_hint": "", "notes": "", "is_fallback": False, "priority": "High"},
            {"narrative_id": "S1-99", "narrative_name": "Fallback", "include_keywords": "",
             "exclude_keywords": "", "required_context_keywords": "", "public_voice_cues": "",
             "cluster_split_terms": "", "tone_hint": "", "public_voice_hint": "", "report_use_hint": "",
             "continuity_hint": "", "notes": "", "is_fallback": True, "priority": "Medium"},
        ],
    }
    rows = chat_service.generate_narrative_master_rows_for_signal(
        client, _mock_azure_client(generated), "gpt-4o", "proj-1", "S1", adapter=adapter)

    assert len(rows) == 2
    assert rows[0]["signal_id"] == "S1"
    assert rows[0]["signal_source"] == "S1"  # Shell mirror同期済み
    assert rows[0]["sub_narrative_id"] == "S1-01"
    # 2026-09-15発覚: これらの列が空のままだとShell（Preprocess）出力の
    # cluster_labeling_input.csv/signal_narrative_master.csvまで空欄が伝播していた。
    # Signal定義・Queryから自動補完されることを確認する。
    assert rows[0]["signal_name"] == "Arizona Governor"
    assert rows[0]["theme_id"] == "S1"
    assert rows[0]["theme_name"] == "Arizona Governor"
    assert rows[0]["report_topic_label"] == "Arizona州知事選の論調"
    assert rows[0]["main_narrative"] == "Support"
    assert rows[1]["main_narrative"] == "Fallback"
    # 2026-09-15追加、ChatGPT検証で発覚: active/signal_name_internalがAI生成
    # スキーマに存在せず全行空欄のまま出力されていた（Publish Gate自体は
    # parse_bool(row.get("active"), True)によりactive欠落でも通ってしまうため
    # 実害を検知しにくいが、Export CSV上は明示的な値であるべき）。
    assert rows[0]["active"] is True
    assert rows[1]["active"] is True
    assert rows[0]["signal_name_internal"] == "Arizona Governor"
    # 2026-09-15追加、外部レビュー指摘: max_cluster_records/representative_countも
    # 同様に全行空欄だった。Shell側（geo_report_current_pipeline_service）の正式
    # Default値（200/8）で補完されることを確認する。
    assert rows[0]["max_cluster_records"] == cp.DEFAULT_MAX_CLUSTER_RECORDS
    assert rows[0]["representative_count"] == cp.DEFAULT_REPRESENTATIVE_COUNT
    # 生成結果はそのままsave_narrative_master_draftへ通せる(Publish Gateを満たす)
    errors = assets.validate_narrative_master(rows)
    assert errors == []


def test_generate_narrative_master_for_all_signals_requires_at_least_one_signal():
    """2026-09-14発覚: Narrative Masterカードの唯一の生成手段がSignal 1件ずつの手動生成
    かDesign Chatでの自然言語依頼だけで、画面から一括生成する導線が無かった。新設した
    公開エントリポイントがSignal未設計時に分かりやすいエラーを返すことを確認する。"""
    client = FakeSupabaseClient()
    try:
        chat_service.generate_narrative_master_for_all_signals(
            client, _mock_azure_client({"rows": []}), "gpt-4o", "proj-1")
        assert False, "should have raised"
    except ValueError as exc:
        assert "Signal" in str(exc)


def test_generate_narrative_master_for_all_signals_generates_and_saves_for_every_signal():
    client = FakeSupabaseClient()
    adapter = FakeAdapter()
    assets.save_design_state(client, "proj-1", signals=[
        {"signal_id": "S1", "name": "A", "purpose": "p", "what_to_capture": "c",
         "expected_public_voice": "v", "risk_or_relevance": "r", "continuity_role": "Core", "status": "active"},
        {"signal_id": "S2", "name": "B", "purpose": "p", "what_to_capture": "c",
         "expected_public_voice": "v", "risk_or_relevance": "r", "continuity_role": "Core", "status": "active"},
    ])

    def _row(signal_id, suffix, is_fallback=False):
        return {
            "narrative_id": f"{signal_id}-{suffix}", "narrative_name": "n", "include_keywords": "",
            "exclude_keywords": "", "required_context_keywords": "", "public_voice_cues": "",
            "cluster_split_terms": "", "tone_hint": "", "public_voice_hint": "", "report_use_hint": "",
            "continuity_hint": "", "notes": "", "is_fallback": is_fallback, "priority": "High",
        }

    azure_client = _mock_azure_client_sequence([
        {"rows": [_row("S1", "01"), _row("S1", "99", is_fallback=True)]},
        {"rows": [_row("S2", "01"), _row("S2", "99", is_fallback=True)]},
    ])

    result = chat_service.generate_narrative_master_for_all_signals(
        client, azure_client, "gpt-4o", "proj-1", adapter=adapter)

    assert result["row_count"] == 4
    assert set(result["signals_generated"]) == {"S1", "S2"}
    assert result["errors"] == []

    draft = assets.get_draft(client, "proj-1", "narrative_master")
    saved_ids = {r["narrative_id"] for r in draft["content"]["rows"]}
    assert saved_ids == {"S1-01", "S1-99", "S2-01", "S2-99"}


def _mock_azure_client_sequence(response_list):
    """複数回のLLM呼び出しに対して、呼び出し順に別々のレスポンスを返すFake（1ターンの
    Design Chat応答＋その後のSignal別Narrative Master生成N回、という一連の呼び出しを
    1つのMagicMockで再現するため）。"""
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        MagicMock(choices=[MagicMock(message=MagicMock(content=json.dumps(r)))])
        for r in response_list
    ]
    return client


def _narrative_row(signal_id: str, suffix: str, *, is_fallback: bool = False) -> dict:
    return {
        "narrative_id": f"{signal_id}-{suffix}", "narrative_name": "n", "include_keywords": "",
        "exclude_keywords": "", "required_context_keywords": "", "public_voice_cues": "",
        "cluster_split_terms": "", "tone_hint": "", "public_voice_hint": "", "report_use_hint": "",
        "continuity_hint": "", "notes": "", "is_fallback": is_fallback, "priority": "High",
    }


def test_process_chat_turn_narrative_master_generation_requested_generates_for_all_signals():
    """ユーザーがDesign Chatで「ナラティブマスターを作って」のように依頼した場合、
    AIがCSV本文をテキストで書く代わりにnarrative_master_generation_requested=trueを返し、
    process_chat_turnが登録済み全Signal分を自動生成・Draftへ保存することを確認する
    （2026-09-14発覚: 従来はAIがチャット内にCSV文面を書くだけで実際には何も保存されず、
    ユーザーには「生成されない」ように見えていた）。"""
    client = FakeSupabaseClient()
    adapter = FakeAdapter()
    assets.save_design_state(client, "proj-1", signals=[
        {"signal_id": "S1", "name": "A", "purpose": "p", "what_to_capture": "c",
         "expected_public_voice": "v", "risk_or_relevance": "r", "continuity_role": "Core", "status": "active"},
        {"signal_id": "S2", "name": "B", "purpose": "p", "what_to_capture": "c",
         "expected_public_voice": "v", "risk_or_relevance": "r", "continuity_role": "Core", "status": "active"},
    ])

    chat_response = dict(_first_turn_response())
    chat_response["signals"] = None
    chat_response["narrative_master_generation_requested"] = True
    chat_response["assistant_reply"] = "全Signal分のNarrative Masterを生成します。"

    s1_rows_response = {"rows": [_narrative_row("S1", "01"), _narrative_row("S1", "99", is_fallback=True)]}
    s2_rows_response = {"rows": [_narrative_row("S2", "01", is_fallback=True)]}
    azure_client = _mock_azure_client_sequence([chat_response, s1_rows_response, s2_rows_response])

    result = chat_service.process_chat_turn(
        client, azure_client, "gpt-4o", "proj-1", "ではナラティブマスターつくて", adapter=adapter)

    gen = result["narrative_master_generation"]
    assert gen["row_count"] == 3
    assert set(gen["signals_generated"]) == {"S1", "S2"}
    assert gen["errors"] == []

    draft = assets.get_draft(client, "proj-1", "narrative_master")
    saved_ids = {r["narrative_id"] for r in draft["content"]["rows"]}
    assert saved_ids == {"S1-01", "S1-99", "S2-01"}


def test_process_chat_turn_report_template_generation_requested_generates_and_saves():
    """2026-09-16追加、ユーザー要望:「テンプレートもチャット上でHTMLリンクをだして、
    内容やり取りしたあとに保存する」。report_template_generation_requested=trueの
    場合、process_chat_turnがReport Template Draftを自動生成・保存することを確認する。"""
    client = FakeSupabaseClient()
    adapter = FakeAdapter()

    chat_response = dict(_first_turn_response())
    chat_response["signals"] = None
    chat_response["report_template_generation_requested"] = True
    chat_response["assistant_reply"] = "Report Templateの構成を生成します。"

    template_response = {"sections": [
        {"section_id": "s1", "title": "Summary", "order": 1,
         "component_type": "narrative_block", "required_fields": ["central_conclusion"]},
    ]}
    azure_client = _mock_azure_client_sequence([chat_response, template_response])

    result = chat_service.process_chat_turn(
        client, azure_client, "gpt-4o", "proj-1", "テンプレート作って", adapter=adapter)

    gen = result["report_template_generation"]
    assert gen["section_count"] == 1

    draft = assets.get_draft(client, "proj-1", "report_template")
    assert draft["content"]["sections"][0]["section_id"] == "s1"


def test_process_chat_turn_without_template_request_leaves_report_template_untouched():
    client = FakeSupabaseClient()
    adapter = FakeAdapter()
    response = dict(_first_turn_response())
    response["report_template_generation_requested"] = False

    result = chat_service.process_chat_turn(
        client, _mock_azure_client(response), "gpt-4o", "proj-1", "レポート設計を続けたい", adapter=adapter)

    assert result["report_template_generation"] is None
    assert assets.get_draft(client, "proj-1", "report_template") is None


def test_process_chat_turn_rejects_non_date_common_primary_graph_start():
    """2026-09-14発覚: AIがcommon_primary_graph_startへ"Pre-summit"のような自然文
    ラベルを書き込むと、後のRun作成でPostgreSQLのdate型カラムへの挿入が
    "invalid input syntax for type date"で失敗し、フロントには「Failed to fetch」としか
    見えなかった。YYYY-MM-DD形式でない値はDesign Stateへ保存される前に弾かれ、
    他のreport_design_patchフィールドは正常に保存されることを確認する。"""
    client = FakeSupabaseClient()
    adapter = FakeAdapter()
    response = dict(_first_turn_response())
    response["report_design_patch"]["common_primary_graph_start"] = "Pre-summit"

    result = chat_service.process_chat_turn(
        client, _mock_azure_client(response), "gpt-4o", "proj-1", "起点ラベルを設定して", adapter=adapter)

    design_state = assets.get_design_state(client, "proj-1")
    assert design_state["pending_report_design"].get("common_primary_graph_start") is None
    # 同じpatch内の他フィールドは影響を受けず保存される
    assert design_state["pending_report_design"]["purpose"] == "選挙情勢モニタリング"
    assert result["applied_diff"]["rejected_fields"] == {"common_primary_graph_start": "Pre-summit"}


def test_process_chat_turn_accepts_valid_date_common_primary_graph_start():
    client = FakeSupabaseClient()
    adapter = FakeAdapter()
    response = dict(_first_turn_response())
    response["report_design_patch"]["common_primary_graph_start"] = "2026-09-01"

    result = chat_service.process_chat_turn(
        client, _mock_azure_client(response), "gpt-4o", "proj-1", "起点日を設定して", adapter=adapter)

    design_state = assets.get_design_state(client, "proj-1")
    assert design_state["pending_report_design"]["common_primary_graph_start"] == "2026-09-01"
    assert "rejected_fields" not in result["applied_diff"]


def test_process_chat_turn_persists_analysis_mode_patch():
    """2026-09-15追加、外部監査指摘への対応: AIがreport_design_patch.analysis_modeを
    設定した場合、他のフィールドと同様にpending_report_designへ反映されること。"""
    client = FakeSupabaseClient()
    adapter = FakeAdapter()
    response = dict(_first_turn_response())
    response["report_design_patch"] = dict(response["report_design_patch"])
    response["report_design_patch"]["analysis_mode"] = "evidence_intelligence"

    chat_service.process_chat_turn(
        client, _mock_azure_client(response), "gpt-4o", "proj-1", "地政学インテリジェンス分析にしたい",
        adapter=adapter)

    design_state = assets.get_design_state(client, "proj-1")
    assert design_state["pending_report_design"]["analysis_mode"] == "evidence_intelligence"


def test_process_chat_turn_without_generation_request_leaves_narrative_master_untouched():
    client = FakeSupabaseClient()
    adapter = FakeAdapter()
    response = dict(_first_turn_response())
    response["narrative_master_generation_requested"] = False

    result = chat_service.process_chat_turn(
        client, _mock_azure_client(response), "gpt-4o", "proj-1", "レポート設計を続けたい", adapter=adapter)

    assert result["narrative_master_generation"] is None
    assert assets.get_draft(client, "proj-1", "narrative_master") is None


# ---------------------------------------------------------------------------
# Template Extraction + Leakage Scan
# ---------------------------------------------------------------------------

_ORIGINAL_REPORT_HTML = (
    '<html><body><div class="kicker">CONTINUITY REPORT</div>'
    '<h1>2026 U.S. Midterm Purple State Monitor</h1>'
    '<p>調査期間: 2026-08-19から2026-09-06。'
    'Public Voiceの62%が候補者の経済政策を支持と回答した。'
    '詳細は<a href="https://example.com/source-article-123">こちらの記事</a>を参照。'
    '「もう既存政治には期待できない」という声が多数観測された。</p>'
    '</body></html>'
)


def test_scan_template_for_old_content_leakage_passes_on_clean_extraction():
    clean_template = {
        "style_css": "<style>.kicker{}</style>",
        "shell_html": '<html><body>{sections}</body></html>',
        "sections": [{
            "section_id": "executive-summary", "title": "Executive Summary", "order": 1,
            "component_type": "narrative_block", "required_fields": ["central_conclusion"],
            "example_markup": '<div class="kicker">[レポート種別]</div><h1>[レポートタイトル]</h1>'
                               '<p>[調査期間の説明][主要な発見の要約][代表的な意見の引用（一般化）]</p>',
        }],
    }
    scan = chat_service.scan_template_for_old_content_leakage(_ORIGINAL_REPORT_HTML, clean_template)
    assert scan["passed"] is True
    assert scan["leaked_items"] == []


def test_scan_template_for_old_content_leakage_detects_leaked_url():
    leaky_template = {
        "style_css": "<style>.kicker{}</style>",
        "shell_html": '<html><body>{sections}</body></html>',
        "sections": [{
            "section_id": "executive-summary", "title": "Executive Summary", "order": 1,
            "component_type": "narrative_block", "required_fields": ["central_conclusion"],
            "example_markup": '<a href="https://example.com/source-article-123">こちらの記事</a>',
        }],
    }
    scan = chat_service.scan_template_for_old_content_leakage(_ORIGINAL_REPORT_HTML, leaky_template)
    assert scan["passed"] is False
    assert any("example.com/source-article-123" in item for item in scan["leaked_items"])


def test_scan_template_for_old_content_leakage_ignores_css_numbers_copied_into_style_css():
    """style_cssは元HTMLの<style>ブロックをそのままコピーする設計（Plan I.1）のため、
    font-size/line-height/opacity等のCSS数値がstyle_cssへ再現されても実データ漏洩として
    誤検知しないこと（過去に発生した実際の不具合の再現テスト）。"""
    original_with_css = (
        '<html><head><style>'
        '.kicker{font-size:12.5px;line-height:1.35;} .card{opacity:0.6;border-radius:10.5px;} '
        '.bar{width:100%;} .x{z-index:103;}'
        '</style></head><body><div class="kicker">CONTINUITY REPORT</div>'
        '<h1>2026 U.S. Midterm Purple State Monitor</h1>'
        '<p>調査期間: 2026-08-19から2026-09-06。Public Voiceの62%が候補者の経済政策を支持と回答した。</p>'
        '</body></html>'
    )
    clean_template = {
        "style_css": ".kicker{font-size:12.5px;line-height:1.35;} .card{opacity:0.6;border-radius:10.5px;} "
                     ".bar{width:100%;} .x{z-index:103;}",
        "shell_html": '<html><body>{sections}</body></html>',
        "sections": [{
            "section_id": "executive-summary", "title": "Executive Summary", "order": 1,
            "component_type": "narrative_block", "required_fields": ["central_conclusion"],
            "example_markup": '<div class="kicker">[レポート種別]</div><h1>[レポートタイトル]</h1>'
                               '<p>[調査期間の説明][主要な発見の要約]</p>',
        }],
    }
    scan = chat_service.scan_template_for_old_content_leakage(original_with_css, clean_template)
    assert scan["passed"] is True
    assert scan["leaked_items"] == []


def test_scan_template_for_old_content_leakage_detects_leaked_quote_and_number():
    leaky_template = {
        "style_css": "", "shell_html": "",
        "sections": [{
            "section_id": "s1", "title": "t", "order": 1, "component_type": "narrative_block",
            "required_fields": [], "example_markup": "Public Voiceの62%が...もう既存政治には期待できないという声",
        }],
    }
    scan = chat_service.scan_template_for_old_content_leakage(_ORIGINAL_REPORT_HTML, leaky_template)
    assert scan["passed"] is False
    assert len(scan["leaked_items"]) >= 1


def test_scan_template_for_old_content_leakage_passes_when_original_has_no_candidates():
    plain_html = "<html><body><p>短い一般的な説明文のみ</p></body></html>"
    scan = chat_service.scan_template_for_old_content_leakage(plain_html, {"anything": "goes"})
    assert scan["passed"] is True


def test_extract_template_from_report_passes_leakage_scan_on_clean_extraction():
    client = FakeSupabaseClient()
    adapter = FakeAdapter()
    generated = {
        "style_css": "<style>.kicker{}</style>",
        "shell_html": '<html><body>{sections}</body></html>',
        "sections": [{
            "section_id": "executive-summary", "title": "Executive Summary", "order": 1,
            "component_type": "narrative_block", "required_fields": ["central_conclusion"],
            "example_markup": '<div class="kicker">[レポート種別]</div><h1>[タイトル]</h1>'
                               '<p>[調査期間][主要な発見の要約]</p>',
        }],
    }
    result = chat_service.extract_template_from_report(
        client, _mock_azure_client(generated), "gpt-4o", _ORIGINAL_REPORT_HTML, adapter=adapter)

    assert result["leakage_scan"]["passed"] is True
    assert result["template"]["sections"][0]["section_id"] == "executive-summary"
    errors = assets.validate_report_template(result["template"])
    assert errors == []


def test_extract_template_from_report_fails_leakage_scan_when_llm_leaves_real_data():
    client = FakeSupabaseClient()
    adapter = FakeAdapter()
    generated = {
        "style_css": "", "shell_html": "<html><body>{sections}</body></html>",
        "sections": [{
            "section_id": "executive-summary", "title": "Executive Summary", "order": 1,
            "component_type": "narrative_block", "required_fields": ["central_conclusion"],
            "example_markup": "Public Voiceの62%が候補者の経済政策を支持と回答した。",
        }],
    }
    result = chat_service.extract_template_from_report(
        client, _mock_azure_client(generated), "gpt-4o", _ORIGINAL_REPORT_HTML, adapter=adapter)

    assert result["leakage_scan"]["passed"] is False
    assert result["leakage_scan"]["leaked_items"]


def test_generate_report_template_returns_default_css_and_ai_proposed_sections():
    client = FakeSupabaseClient()
    adapter = FakeAdapter()
    generated = {
        "sections": [
            {"section_id": "executive-summary", "title": "Executive Summary", "order": 1,
             "component_type": "narrative_block", "required_fields": ["central_conclusion"]},
            {"section_id": "voice-trend", "title": "Public Voice Trend", "order": 2,
             "component_type": "stacked_bar_chart", "required_fields": ["periods", "shares"]},
        ],
    }
    template = chat_service.generate_report_template(
        client, _mock_azure_client(generated), "gpt-4o", "proj-1", adapter=adapter)

    assert template["style_css"] == chat_service._DEFAULT_STYLE_CSS
    assert template["shell_html"] == chat_service._DEFAULT_SHELL_HTML
    assert len(template["sections"]) == 2
    # 生成結果はそのままsave_report_template_draftへ通せる(Validation Gateを満たす)
    errors = assets.validate_report_template(template)
    assert errors == []


# ---------------------------------------------------------------------------
# FIX（提案→確定ワークフロー、2026-09-15）
# ---------------------------------------------------------------------------

def test_fix_design_commits_pending_and_auto_generates_narrative_master_for_all_signals():
    """2026-09-15追加、ユーザー要望:「(Designが)FIXしたらナラティブマスターもFIX
    できるから自動で作成して保存する」。FIXボタン相当のエントリポイントが、
    チャットで提案されたsignalsを確定し、確定した全Signal分のNarrative Masterを
    自動生成・保存することを確認する。"""
    client = FakeSupabaseClient()
    adapter = FakeAdapter()
    assets.save_pending_design_state(client, "proj-1", report_design={"purpose": "final"}, signals=[
        {"signal_id": "S1", "name": "A", "purpose": "p", "what_to_capture": "c",
         "expected_public_voice": "v", "risk_or_relevance": "r", "continuity_role": "Core", "status": "active"},
        {"signal_id": "S2", "name": "B", "purpose": "p", "what_to_capture": "c",
         "expected_public_voice": "v", "risk_or_relevance": "r", "continuity_role": "Core", "status": "active"},
    ])

    s1_rows_response = {"rows": [_narrative_row("S1", "01"), _narrative_row("S1", "99", is_fallback=True)]}
    s2_rows_response = {"rows": [_narrative_row("S2", "01", is_fallback=True)]}
    azure_client = _mock_azure_client_sequence([s1_rows_response, s2_rows_response])

    result = chat_service.fix_design(client, azure_client, "gpt-4o", "proj-1", adapter=adapter)

    # report_designはfix_design_state内でautofill_prompt2_report_configが適用される
    # ため、明示的なpurposeはそのまま維持しつつ、continuity_role='Core'のS1/S2が
    # primary_graph_signalsへ、final_graph_ruleが既定値で自動補完される（2026-09-15）。
    report_design = result["design_state"]["report_design"]
    assert report_design["purpose"] == "final"
    assert report_design["primary_graph_signals"] == ["S1", "S2"]
    assert report_design["final_graph_rule"]
    assert result["design_state"]["pending_report_design"] is None
    gen = result["narrative_master_generation"]
    assert gen["row_count"] == 3
    assert set(gen["signals_generated"]) == {"S1", "S2"}

    draft = assets.get_draft(client, "proj-1", "narrative_master")
    saved_ids = {r["narrative_id"] for r in draft["content"]["rows"]}
    assert saved_ids == {"S1-01", "S1-99", "S2-01"}

    # 2026-09-16追加: FIX結果にダウンロードURLが含まれる（ユーザー要望:「チャット内で
    # FIXしたという確認をとったら、下部にFIXした設計、シグナル、ナラティブマスター、
    # クエリ、テンプレートのダウンロードリンクがでる」）。この時点でNarrative Masterは
    # 自動生成済みなのでリンクに含まれるが、Templateはまだ生成していないので含まれない。
    downloads = result["downloads"]
    assert downloads["report_design"] == "/api/geo/report-studio/projects/proj-1/design/report-design/download"
    assert downloads["signals"] == "/api/geo/report-studio/projects/proj-1/design/signals/download"
    assert downloads["queries"] == "/api/geo/report-studio/projects/proj-1/design/queries/export"
    assert "narrative_master" in downloads
    assert "report_template" not in downloads


def test_fix_design_skips_narrative_master_generation_when_no_signals():
    client = FakeSupabaseClient()
    adapter = FakeAdapter()
    assets.save_pending_design_state(client, "proj-1", report_design={"purpose": "final"})

    result = chat_service.fix_design(client, _mock_azure_client({}), "gpt-4o", "proj-1", adapter=adapter)

    # Signalが無いためprimary_graph_signalsは補完できないが、final_graph_ruleは
    # 既定値で自動補完される（2026-09-15）。
    report_design = result["design_state"]["report_design"]
    assert report_design["purpose"] == "final"
    assert report_design["final_graph_rule"]
    assert "primary_graph_signals" not in report_design
    assert result["narrative_master_generation"] is None
    assert "narrative_master" not in result["downloads"]
    assert assets.get_draft(client, "proj-1", "narrative_master") is None


# ---------------------------------------------------------------------------
# Report Templateプレビュー（2026-09-15）
# ---------------------------------------------------------------------------

def test_render_template_design_preview_renders_every_component_type_with_placeholders():
    """全component_typeがプレースホルダーで例外なくレンダリングされ、実際の
    Final Report生成と同じrender_report_section/render_final_reportで組み立てられる
    ことを確認する（実データが無いDesign段階でのプレビュー用）。"""
    content = {
        "style_css": "<style>.page{color:#000}</style>",
        "shell_html": '<html><head>{style}</head><body>{sections}</body></html>',
        "sections": [
            {"section_id": "s1", "title": "Summary", "order": 1,
             "component_type": "narrative_block", "required_fields": ["central_conclusion"]},
            {"section_id": "s2", "title": "Metrics", "order": 2,
             "component_type": "metric_grid", "required_fields": ["metrics"]},
            {"section_id": "s3", "title": "Trend", "order": 3,
             "component_type": "stacked_bar_chart", "required_fields": ["interpretation"]},
            {"section_id": "s4", "title": "Scenarios", "order": 4,
             "component_type": "scenario_cards", "required_fields": ["scenarios"]},
            {"section_id": "s5", "title": "Comparison", "order": 5,
             "component_type": "comparison_table", "required_fields": ["columns"]},
            {"section_id": "s6", "title": "Quotes", "order": 6,
             "component_type": "example_quotes", "required_fields": ["quotes"]},
            {"section_id": "s7", "title": "Limits", "order": 7,
             "component_type": "data_limits_note", "required_fields": ["limitations"]},
        ],
    }

    html = chat_service.render_template_design_preview(content)

    assert "<style>.page{color:#000}</style>" in html
    for section_id, title in [("s1", "Summary"), ("s2", "Metrics"), ("s3", "Trend"),
                               ("s4", "Scenarios"), ("s5", "Comparison"), ("s6", "Quotes"),
                               ("s7", "Limits")]:
        assert f'data-section-id="{section_id}"' in html
        assert title in html
    assert "グラフはFinal Report生成時に実データで描画されます" in html
    # 実データ（数値・固有名詞等）は一切含まれず、プレースホルダー文言のみ
    assert "（このSectionの中心的な結論をここに記述）" in html


def test_render_template_design_preview_skips_unsupported_component_type():
    content = {
        "style_css": "", "shell_html": "<html><body>{sections}</body></html>",
        "sections": [
            {"section_id": "s1", "title": "Unknown", "order": 1,
             "component_type": "not_a_real_type", "required_fields": []},
            {"section_id": "s2", "title": "Summary", "order": 2,
             "component_type": "narrative_block", "required_fields": []},
        ],
    }

    html = chat_service.render_template_design_preview(content)

    assert 'data-section-id="s1"' not in html
    assert 'data-section-id="s2"' in html


# ---------------------------------------------------------------------------
# ファイルアップロード（2026-09-16、ユーザー要望:「デザインパートはチャットUIだけで、
# ファイルアップできる形にしよう。それであれば、途中のものもそこから始められる」）
# ---------------------------------------------------------------------------

def test_handle_chat_file_upload_csv_saves_narrative_master_draft():
    seed_client = FakeSupabaseClient()
    assets.save_narrative_master_draft(seed_client, "seed", [
        {"signal_id": "S1", "narrative_id": "S1-01", "narrative_name": "Support",
         "is_fallback": "false", "active": "true"},
        {"signal_id": "S1", "narrative_id": "S1-99", "narrative_name": "Fallback",
         "is_fallback": "true", "active": "true"},
    ])
    csv_text = assets.download_narrative_master_csv(seed_client, "seed", status="draft")

    client = FakeSupabaseClient()
    result = chat_service.handle_chat_file_upload(
        client, _mock_azure_client({}), "gpt-4o", "proj-1", "master.csv", csv_text.encode("utf-8"))

    assert result["kind"] == "narrative_master"
    assert result["success"] is True
    assert result["row_count"] == 2
    assert result["signal_ids"] == ["S1"]

    draft = assets.get_draft(client, "proj-1", "narrative_master")
    assert len(draft["content"]["rows"]) == 2

    turns = chat_service.list_turns(client, "proj-1")
    assert len(turns) == 2
    assert turns[0]["role"] == "user"
    assert "master.csv" in turns[0]["content"]
    assert turns[1]["role"] == "assistant"


def test_handle_chat_file_upload_html_extracts_and_saves_template_when_leakage_scan_passes():
    client = FakeSupabaseClient()
    adapter = FakeAdapter()
    extracted = {
        "style_css": "<style>.page{color:#000}</style>",
        "shell_html": "<html><head>{style}</head><body>{sections}</body></html>",
        "sections": [
            {"section_id": "s1", "title": "Summary", "order": 1, "component_type": "narrative_block",
             "required_fields": ["central_conclusion"], "example_markup": "<div>placeholder</div>"},
        ],
    }
    azure_client = _mock_azure_client(extracted)
    html_source = "<html><body><h1>Report</h1></body></html>"  # 実データ相当の記述を含まない単純なHTML

    result = chat_service.handle_chat_file_upload(
        client, azure_client, "gpt-4o", "proj-1", "final_report.html", html_source.encode("utf-8"),
        adapter=adapter)

    assert result["kind"] == "report_template"
    assert result["success"] is True
    assert result["section_count"] == 1

    draft = assets.get_draft(client, "proj-1", "report_template")
    assert draft["content"]["sections"][0]["section_id"] == "s1"


def test_handle_chat_file_upload_html_rejects_when_leakage_scan_fails():
    """抽出結果に元HTMLの実データ（URL）がそのまま残ってしまっているケースでは
    Draftへ保存しない（Plan I.2 Leakage Gate）。"""
    client = FakeSupabaseClient()
    adapter = FakeAdapter()
    extracted = {
        "style_css": "", "shell_html": "<html><body>{sections}</body></html>",
        "sections": [
            {"section_id": "s1", "title": "Summary", "order": 1, "component_type": "narrative_block",
             "required_fields": [], "example_markup": "https://example.com/leaked-source-123"},
        ],
    }
    azure_client = _mock_azure_client(extracted)
    html_source = '<html><body><a href="https://example.com/leaked-source-123">link</a></body></html>'

    result = chat_service.handle_chat_file_upload(
        client, azure_client, "gpt-4o", "proj-1", "final_report.html", html_source.encode("utf-8"),
        adapter=adapter)

    assert result["kind"] == "report_template"
    assert result["success"] is False
    assert result["leakage_scan"]["passed"] is False
    assert assets.get_draft(client, "proj-1", "report_template") is None


def test_handle_chat_file_upload_rejects_unsupported_extension():
    client = FakeSupabaseClient()
    result = chat_service.handle_chat_file_upload(
        client, _mock_azure_client({}), "gpt-4o", "proj-1", "notes.txt", b"hello")

    assert result["kind"] == "unsupported"
    assert result["success"] is False
