"""Geopolitical Intelligence OS: Consultation Turns（切り口相談/調査方針/プレレビュー）のテスト。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_consultation_service as consultation_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def test_create_pending_turn_defaults_to_pending_status():
    client = FakeSupabaseClient()
    turn = consultation_service.create_pending_turn(
        client, "session-1", consultation_mode="framing", user_message="この局面をどう見る？",
        draft_text=None, created_by="analyst-1")
    assert turn["status"] == "pending"
    assert turn["consultation_mode"] == "framing"
    assert turn.get("expert_request_id") is None


def test_create_pending_turn_rejects_unknown_mode():
    client = FakeSupabaseClient()
    with pytest.raises(consultation_service.ConsultationError):
        consultation_service.create_pending_turn(
            client, "session-1", consultation_mode="not-a-mode", user_message="x",
            draft_text=None, created_by="analyst-1")


def test_create_pending_turn_pre_review_requires_draft_text():
    client = FakeSupabaseClient()
    with pytest.raises(consultation_service.ConsultationError):
        consultation_service.create_pending_turn(
            client, "session-1", consultation_mode="pre_review", user_message="レビューして",
            draft_text=None, created_by="analyst-1")
    # draft_textがあれば通る
    turn = consultation_service.create_pending_turn(
        client, "session-1", consultation_mode="pre_review", user_message="レビューして",
        draft_text="レポート本文...", created_by="analyst-1")
    assert turn["status"] == "pending"


def test_create_pending_turn_rejects_empty_message():
    client = FakeSupabaseClient()
    with pytest.raises(consultation_service.ConsultationError):
        consultation_service.create_pending_turn(
            client, "session-1", consultation_mode="framing", user_message="   ",
            draft_text=None, created_by="analyst-1")


def test_mark_completed_sets_status_and_references():
    client = FakeSupabaseClient()
    turn = consultation_service.create_pending_turn(
        client, "session-1", consultation_mode="framing", user_message="質問",
        draft_text=None, created_by="analyst-1")
    updated = consultation_service.mark_completed(
        client, turn["consultation_turn_id"], expert_request_id="req-1", expert_response_id="resp-1")
    assert updated["status"] == "completed"
    assert updated["expert_request_id"] == "req-1"
    assert updated["expert_response_id"] == "resp-1"
    assert updated["completed_at"] is not None


def test_mark_failed_sets_status_and_error_message():
    client = FakeSupabaseClient()
    turn = consultation_service.create_pending_turn(
        client, "session-1", consultation_mode="framing", user_message="質問",
        draft_text=None, created_by="analyst-1")
    updated = consultation_service.mark_failed(
        client, turn["consultation_turn_id"], error_message="Timeout: llm call failed")
    assert updated["status"] == "failed"
    assert "Timeout" in updated["error_message"]


def test_list_turns_filters_by_mode_and_orders_by_created_at():
    client = FakeSupabaseClient()
    consultation_service.create_pending_turn(
        client, "session-1", consultation_mode="framing", user_message="Q1",
        draft_text=None, created_by="analyst-1")
    consultation_service.create_pending_turn(
        client, "session-1", consultation_mode="research_direction", user_message="Q2",
        draft_text=None, created_by="analyst-1")
    consultation_service.create_pending_turn(
        client, "session-1", consultation_mode="framing", user_message="Q3",
        draft_text=None, created_by="analyst-1")
    turns = consultation_service.list_turns(client, "session-1", consultation_mode="framing")
    assert [t["user_message"] for t in turns] == ["Q1", "Q3"]


def test_build_answer_summary_uses_only_mode_independent_fields():
    response_json = {
        "one_line_assessment": "一言見立て",
        "executive_summary": {"what_changed": "A", "why_it_matters": "B", "what_to_watch_decide": "C"},
        "review_findings": [{"severity": "major"}, {"severity": "minor"}],
    }
    summary = consultation_service.build_answer_summary(response_json)
    assert summary["one_line_assessment"] == "一言見立て"
    assert summary["what_changed"] == "A"
    assert summary["review_findings_count"] == 2


def test_build_answer_summary_handles_missing_fields():
    summary = consultation_service.build_answer_summary({})
    assert summary["one_line_assessment"] is None
    assert summary["review_findings_count"] == 0
