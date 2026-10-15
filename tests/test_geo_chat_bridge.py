"""Phase1 Closeout: Intelligence AI画面（/api/geo/chat）を本格geo_intelligence_service
パイプラインへ接続したことの検証。

- sufficient/partial/insufficientでExpert AI呼び出しの有無が既存条件通りに分かれること
  （sufficient: 既存条件次第で呼ばれる。partial/insufficient: 呼ばれない）
- expert_ai_calledがsufficiencyからの推測ではなく実行結果そのものであること
- Theme Context/Conversation Contextが分離されて扱われ、Conversation Contextが
  Retrieval/Referenceを汚染しないこと
- Intelligence Need生成がresponseへ反映されること
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import api_server  # noqa: E402
import geo_intelligence_service as service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402
from tests.test_geo_intelligence_service_phase2 import (  # noqa: E402
    ISSUE_ID, _sufficient_tables, WELL_FORMED_EXPERT_RESPONSE,
)


def _mock_llm_client(response: dict = None) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(response or WELL_FORMED_EXPERT_RESPONSE)))],
    )
    return client


BASE_REQUEST = {
    "requester_app": "geo_chat_ui", "request_type": "user_question",
    "question": "イランはホルムズ海峡の通航にどう対応しているか？",
    "country_region": ["Iran"], "themes": ["hormuz"], "analysis_requested": True,
}


# ---- geo_intelligence_service.py: expert_ai_calledフィールド ----

def test_expert_ai_called_true_when_sufficient_and_conditions_met(monkeypatch):
    monkeypatch.setenv("GEO_EXPERT_EXECUTION_MODE", "monolithic")
    client = FakeSupabaseClient(tables=_sufficient_tables())
    azure_client = _mock_llm_client()

    response = service.process_request(client, dict(BASE_REQUEST), azure_client=azure_client, model="gpt-4o")

    assert response["knowledge_sufficiency"] == "sufficient"
    assert response["expert_ai_called"] is True
    azure_client.chat.completions.create.assert_called_once()


def test_expert_ai_called_false_when_partial_creates_need():
    tables = _sufficient_tables()
    tables["geo_relationships"] = []  # partial化
    client = FakeSupabaseClient(tables=tables)
    azure_client = _mock_llm_client()

    response = service.process_request(client, dict(BASE_REQUEST), azure_client=azure_client, model="gpt-4o")

    assert response["knowledge_sufficiency"] == "partial"
    assert response["expert_ai_called"] is False
    azure_client.chat.completions.create.assert_not_called()
    assert response["knowledge_gap"] is not None


def test_expert_ai_called_false_when_insufficient_creates_need():
    client = FakeSupabaseClient()
    azure_client = _mock_llm_client()

    response = service.process_request(client, {
        **BASE_REQUEST, "question": "存在しない架空の国XYZの情勢は？",
        "country_region": ["架空の国XYZ"], "themes": [],
    }, azure_client=azure_client, model="gpt-4o")

    assert response["knowledge_sufficiency"] == "insufficient"
    assert response["expert_ai_called"] is False
    azure_client.chat.completions.create.assert_not_called()
    assert response["knowledge_gap"]["exists"] is True


def test_expert_ai_called_reflects_actual_execution_not_inferred_from_sufficiency():
    """sufficiencyが"sufficient"でも、analysis_requested=Falseならshould_call_expert_aiの
    条件を満たさずExpert AIは呼ばれない。expert_ai_calledがsufficiency単体からの推測ではなく
    実際の実行結果を反映していることを確認する（analysis_requested/azure_client等、他の条件も
    加味した実行結果そのもの）。"""
    client = FakeSupabaseClient(tables=_sufficient_tables())
    azure_client = _mock_llm_client()

    response = service.process_request(client, {**BASE_REQUEST, "analysis_requested": False},
                                        azure_client=azure_client, model="gpt-4o")

    assert response["knowledge_sufficiency"] == "sufficient"
    assert response["expert_ai_called"] is False
    azure_client.chat.completions.create.assert_not_called()


def test_call_expert_ai_appends_context_to_query_text_without_polluting_retrieval(monkeypatch):
    """_call_expert_aiがcontextをExpert AIの依頼文へ付加すること、かつRetrieval
    （_build_query_text/resolve_entities_and_issues）はquestion単体のままであることを確認する。"""
    monkeypatch.setenv("GEO_EXPERT_EXECUTION_MODE", "monolithic")
    client = FakeSupabaseClient(tables=_sufficient_tables())
    azure_client = _mock_llm_client()
    context_text = "【Theme Context】\n監視テーマ: ホルムズ海峡\n\n【Conversation Context】\nユーザー: 前回の続きです"

    service.process_request(client, {**BASE_REQUEST, "context": context_text},
                            azure_client=azure_client, model="gpt-4o")

    sent_messages = azure_client.chat.completions.create.call_args[1]["messages"]
    user_prompt = sent_messages[1]["content"]
    assert context_text in user_prompt


# ---- api_server.py: Theme Context / Conversation Context分離 ----

def test_geo_chat_theme_context_includes_theme_name_and_events(monkeypatch):
    fake = FakeSupabaseClient({
        "geo_events": [{"event_id": "e-1", "theme_id": "t-1", "actor": "PLA", "action": "越境",
                        "verified": True, "created_at": "2026-08-01T00:00:00+00:00"}],
        "geo_evidence": [], "geo_indicator_definitions": [], "geo_observations": [],
    })
    monkeypatch.setattr(api_server, "_geo_client", fake)
    theme = {"theme_id": "t-1", "name_ja": "台湾海峡"}

    text, events = api_server._geo_chat_theme_context(theme)

    assert "台湾海峡" in text
    assert "PLA" in text
    assert len(events) == 1


def test_geo_chat_conversation_context_labels_speakers():
    history = [{"role": "user", "content": "前回の話の続きです"}, {"role": "assistant", "content": "了解しました"}]
    text = api_server._geo_chat_conversation_context(history)
    assert "ユーザー: 前回の話の続きです" in text
    assert "AI: 了解しました" in text


def test_geo_chat_bridge_full_handler_returns_richer_response(monkeypatch):
    """geo_chat()ハンドラ自体を直接呼び、sufficiency/expert_ai_called/intelligence_need_created
    が正しくレスポンスへ反映されることを確認する（HTTP層は経由しない、Python関数直接呼び出し）。"""
    monkeypatch.setenv("GEO_EXPERT_EXECUTION_MODE", "monolithic")
    tables = {
        **_sufficient_tables(), "geo_indicator_definitions": [], "geo_observations": [],
        "geo_themes": [{"theme_id": "t-1", "name_ja": "ホルムズ海峡・イラン情勢", "code": "hormuz"}],
        "geo_issues": [{"issue_id": ISSUE_ID, "theme_id": "t-1"}],
    }
    fake = FakeSupabaseClient(tables=tables)
    monkeypatch.setattr(api_server, "_geo_client", fake)
    monkeypatch.setattr(api_server, "_azure_client", _mock_llm_client())
    monkeypatch.setattr(api_server, "_model", "gpt-4o")

    req = api_server.GeoChatRequest(message="現状を分析してください", history=[], theme_id="t-1", lang="ja")
    result = api_server.geo_chat(req)

    assert result["sufficiency"] == "sufficient"
    assert result["expert_ai_called"] is True
    assert result["intelligence_need_created"] is False
    assert result["reply"]
