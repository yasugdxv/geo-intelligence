"""Geopolitical Intelligence OS: Consultation Turns APIのテスト。

POST時点でstatus=pendingを即座に返し、実際のExpert AI呼び出しはBackgroundTasksへ
1件だけ登録されること、その内部処理関数（_run_turn_in_background）が正常時は
completed、例外時はfailedへ遷移させることを確認する。実際のAzure OpenAI呼び出しは
行わない（geo_ios_expert_ai_adapter.run_consultation_turnをmonkeypatch）。
"""
import sys
from pathlib import Path

from fastapi import BackgroundTasks, HTTPException
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_consultation_api as consultation_api  # noqa: E402
import geo_ios_consultation_service as consultation_service  # noqa: E402
import geo_ios_expert_ai_adapter as expert_ai_adapter  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _request(**overrides):
    base = dict(analysis_session_id="session-1", consultation_mode="framing",
                user_message="この局面をどう見る？", draft_text=None, created_by="analyst-1")
    base.update(overrides)
    return consultation_api.CreateConsultationTurnRequest(**base)


def test_create_consultation_turn_returns_pending_immediately_and_schedules_one_task(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(consultation_api, "_client", client)
    monkeypatch.setattr(consultation_api, "_azure_client", object())
    bg = BackgroundTasks()

    turn = consultation_api.create_consultation_turn(_request(), bg)

    assert turn["status"] == "pending"
    assert len(bg.tasks) == 1
    assert bg.tasks[0].func is consultation_api._run_turn_in_background


def test_create_consultation_turn_requires_azure_client(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(consultation_api, "_client", client)
    monkeypatch.setattr(consultation_api, "_azure_client", None)
    with pytest.raises(HTTPException) as exc_info:
        consultation_api.create_consultation_turn(_request(), BackgroundTasks())
    assert exc_info.value.status_code == 503


def test_create_consultation_turn_invalid_mode_returns_400(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(consultation_api, "_client", client)
    monkeypatch.setattr(consultation_api, "_azure_client", object())
    with pytest.raises(HTTPException) as exc_info:
        consultation_api.create_consultation_turn(_request(consultation_mode="not-a-mode"), BackgroundTasks())
    assert exc_info.value.status_code == 400


def test_background_task_marks_turn_completed_on_success(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(consultation_api, "_client", client)
    monkeypatch.setattr(consultation_api, "_azure_client", object())
    monkeypatch.setattr(consultation_api, "_model", "gpt-test")

    def fake_run_consultation_turn(client_, azure_client_, model_, *, mode, query_text, draft_text, actor_id):
        assert mode == "research_design"  # framing -> research_design
        return {"request_id": "req-1", "response_id": "resp-1", "response_json": {}}
    monkeypatch.setattr(expert_ai_adapter, "run_consultation_turn", fake_run_consultation_turn)

    turn = consultation_service.create_pending_turn(
        client, "session-1", consultation_mode="framing", user_message="質問",
        draft_text=None, created_by="analyst-1")

    consultation_api._run_turn_in_background(
        turn["consultation_turn_id"], consultation_mode="framing", user_message="質問",
        draft_text=None, actor_id="analyst-1")

    updated = consultation_service.get_turn(client, turn["consultation_turn_id"])
    assert updated["status"] == "completed"
    assert updated["expert_request_id"] == "req-1"
    assert updated["expert_response_id"] == "resp-1"


def test_background_task_marks_turn_failed_on_exception(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(consultation_api, "_client", client)
    monkeypatch.setattr(consultation_api, "_azure_client", object())
    monkeypatch.setattr(consultation_api, "_model", "gpt-test")

    def failing_run_consultation_turn(*args, **kwargs):
        raise RuntimeError("LLM呼び出しに失敗しました")
    monkeypatch.setattr(expert_ai_adapter, "run_consultation_turn", failing_run_consultation_turn)

    turn = consultation_service.create_pending_turn(
        client, "session-1", consultation_mode="framing", user_message="質問",
        draft_text=None, created_by="analyst-1")

    consultation_api._run_turn_in_background(
        turn["consultation_turn_id"], consultation_mode="framing", user_message="質問",
        draft_text=None, actor_id="analyst-1")

    updated = consultation_service.get_turn(client, turn["consultation_turn_id"])
    assert updated["status"] == "failed"
    assert "LLM呼び出しに失敗しました" in updated["error_message"]


def test_pre_review_mode_prefixes_query_with_review_instruction(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(consultation_api, "_client", client)
    monkeypatch.setattr(consultation_api, "_azure_client", object())
    monkeypatch.setattr(consultation_api, "_model", "gpt-test")

    captured = {}

    def fake_run_consultation_turn(client_, azure_client_, model_, *, mode, query_text, draft_text, actor_id):
        captured["mode"] = mode
        captured["query_text"] = query_text
        captured["draft_text"] = draft_text
        return {"request_id": "req-1", "response_id": "resp-1", "response_json": {}}
    monkeypatch.setattr(expert_ai_adapter, "run_consultation_turn", fake_run_consultation_turn)

    turn = consultation_service.create_pending_turn(
        client, "session-1", consultation_mode="pre_review", user_message="論理展開を見てほしい",
        draft_text="レポート本文...", created_by="analyst-1")

    consultation_api._run_turn_in_background(
        turn["consultation_turn_id"], consultation_mode="pre_review", user_message="論理展開を見てほしい",
        draft_text="レポート本文...", actor_id="analyst-1")

    assert captured["mode"] == "draft_review"
    assert "論理展開を見てほしい" in captured["query_text"]
    assert captured["draft_text"] == "レポート本文..."


def test_get_consultation_turn_embeds_answer_when_completed(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(consultation_api, "_client", client)
    turn = consultation_service.create_pending_turn(
        client, "session-1", consultation_mode="framing", user_message="質問",
        draft_text=None, created_by="analyst-1")
    client.tables.setdefault("geo_expert_responses", []).append({
        "response_id": "resp-1",
        "response_json": {"one_line_assessment": "見立て", "executive_summary": {}},
    })
    consultation_service.mark_completed(
        client, turn["consultation_turn_id"], expert_request_id="req-1", expert_response_id="resp-1")

    result = consultation_api.get_consultation_turn(turn["consultation_turn_id"])
    assert result["answer"]["one_line_assessment"] == "見立て"


def test_get_consultation_turn_unknown_id_returns_404(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(consultation_api, "_client", client)
    with pytest.raises(HTTPException) as exc_info:
        consultation_api.get_consultation_turn("does-not-exist")
    assert exc_info.value.status_code == 404


def test_list_consultation_turns_returns_turns_for_session_and_mode(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(consultation_api, "_client", client)
    consultation_service.create_pending_turn(
        client, "session-1", consultation_mode="framing", user_message="Q1",
        draft_text=None, created_by="analyst-1")
    consultation_service.create_pending_turn(
        client, "session-1", consultation_mode="research_direction", user_message="Q2",
        draft_text=None, created_by="analyst-1")

    result = consultation_api.list_consultation_turns("session-1", "framing")
    assert len(result["turns"]) == 1
    assert result["turns"][0]["user_message"] == "Q1"
