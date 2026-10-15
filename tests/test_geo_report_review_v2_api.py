"""Report Review V2 APIのテスト。POST時点でstatus=pendingを即座に返しBackgroundTasksへ
1件だけ登録すること、Draft未完了・本文空では400になること、completed済みなら
BackgroundTasksへ登録せずそのまま返す（Expert AIを再度呼ばない）ことを確認する。
実際のAzure OpenAI呼び出しは行わない。"""
import sys
from pathlib import Path

from fastapi import BackgroundTasks, HTTPException
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_review_v2_api as review_v2_api  # noqa: E402
import geo_report_review_v2_service as review_v2_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_running_sessions():
    review_v2_api._RUNNING_SESSIONS.clear()
    yield
    review_v2_api._RUNNING_SESSIONS.clear()


def _seed_completed_uploaded_draft(client, *, draft_id="draft-1", session_id="session-1"):
    client.tables.setdefault("geo_ios_report_drafts", []).append({
        "report_draft_id": draft_id, "analysis_session_id": session_id, "version": 1,
        "source": "uploaded", "status": "completed",
        "sections": [{"heading": "現状認識", "body": "規制強化が続いている。"}],
    })


def test_run_report_review_v2_returns_pending_and_schedules_one_task(monkeypatch):
    client = FakeSupabaseClient()
    _seed_completed_uploaded_draft(client)
    monkeypatch.setattr(review_v2_api, "_client", client)
    monkeypatch.setattr(review_v2_api, "_azure_client", object())
    bg = BackgroundTasks()

    result = review_v2_api.run_report_review_v2(
        "session-1", review_v2_api.RunReportReviewV2Request(
            report_draft_id="draft-1", requested_by="reviewer-1"), bg)

    assert result["status"] == "pending"
    assert len(bg.tasks) == 1
    assert bg.tasks[0].func is review_v2_api._run_report_reading_review_in_background


def test_run_report_review_v2_requires_completed_draft(monkeypatch):
    client = FakeSupabaseClient()
    client.tables.setdefault("geo_ios_report_drafts", []).append({
        "report_draft_id": "draft-2", "analysis_session_id": "session-2", "version": 1,
        "source": "uploaded", "status": "pending", "sections": [],
    })
    monkeypatch.setattr(review_v2_api, "_client", client)
    monkeypatch.setattr(review_v2_api, "_azure_client", object())
    bg = BackgroundTasks()

    with pytest.raises(HTTPException) as exc_info:
        review_v2_api.run_report_review_v2(
            "session-2", review_v2_api.RunReportReviewV2Request(
                report_draft_id="draft-2", requested_by="reviewer-1"), bg)
    assert exc_info.value.status_code == 400


def test_run_report_review_v2_requires_nonempty_sections(monkeypatch):
    client = FakeSupabaseClient()
    client.tables.setdefault("geo_ios_report_drafts", []).append({
        "report_draft_id": "draft-3", "analysis_session_id": "session-3", "version": 1,
        "source": "uploaded", "status": "completed", "sections": [],
    })
    monkeypatch.setattr(review_v2_api, "_client", client)
    monkeypatch.setattr(review_v2_api, "_azure_client", object())
    bg = BackgroundTasks()

    with pytest.raises(HTTPException) as exc_info:
        review_v2_api.run_report_review_v2(
            "session-3", review_v2_api.RunReportReviewV2Request(
                report_draft_id="draft-3", requested_by="reviewer-1"), bg)
    assert exc_info.value.status_code == 400


def test_run_report_review_v2_does_not_reschedule_once_completed(monkeypatch):
    client = FakeSupabaseClient()
    _seed_completed_uploaded_draft(client, draft_id="draft-4", session_id="session-4")
    monkeypatch.setattr(review_v2_api, "_client", client)
    monkeypatch.setattr(review_v2_api, "_azure_client", object())
    review_v2_service.start_run(client, "session-4", report_draft_id="draft-4", created_by="reviewer-1")
    client.update(review_v2_service._RUNS_TABLE, {"analysis_session_id": "eq.session-4"},
                  {"status": "completed", "comparison_findings": []})
    bg = BackgroundTasks()

    result = review_v2_api.run_report_review_v2(
        "session-4", review_v2_api.RunReportReviewV2Request(
            report_draft_id="draft-4", requested_by="reviewer-1"), bg)

    assert result["status"] == "completed"
    assert len(bg.tasks) == 0


def test_run_report_review_v2_does_not_start_second_run_while_running(monkeypatch):
    client = FakeSupabaseClient()
    _seed_completed_uploaded_draft(client, draft_id="draft-6", session_id="session-6")
    monkeypatch.setattr(review_v2_api, "_client", client)
    monkeypatch.setattr(review_v2_api, "_azure_client", object())
    req = review_v2_api.RunReportReviewV2Request(report_draft_id="draft-6", requested_by="reviewer-1")
    bg1, bg2 = BackgroundTasks(), BackgroundTasks()

    first = review_v2_api.run_report_review_v2("session-6", req, bg1)
    second = review_v2_api.run_report_review_v2("session-6", req, bg2)  # 連打

    assert first["status"] == "pending" and second["status"] == "pending"
    assert len(bg1.tasks) == 1
    assert len(bg2.tasks) == 0  # 重ねて開始しない


def test_run_report_review_v2_retry_after_failure_resets_to_pending_and_clears_error(monkeypatch):
    client = FakeSupabaseClient()
    _seed_completed_uploaded_draft(client, draft_id="draft-7", session_id="session-7")
    monkeypatch.setattr(review_v2_api, "_client", client)
    monkeypatch.setattr(review_v2_api, "_azure_client", object())
    review_v2_service.start_run(client, "session-7", report_draft_id="draft-7", created_by="reviewer-1")
    client.update(review_v2_service._RUNS_TABLE, {"analysis_session_id": "eq.session-7"},
                  {"status": "failed", "error_message": "前回の失敗"})
    bg = BackgroundTasks()

    result = review_v2_api.run_report_review_v2(
        "session-7", review_v2_api.RunReportReviewV2Request(
            report_draft_id="draft-7", requested_by="reviewer-1"), bg)

    assert result["status"] == "pending"
    assert result["error_message"] is None  # 古いエラーを画面に出し続けない
    assert len(bg.tasks) == 1
    stored = review_v2_service.get_run(client, "session-7")
    assert stored["status"] == "pending" and stored["error_message"] is None


def test_background_run_releases_running_mark_even_when_it_fails(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("失敗")
    monkeypatch.setattr(review_v2_service, "execute_run", boom)
    assert review_v2_api._try_mark_running("session-8") is True
    review_v2_api._run_report_reading_review_in_background("session-8", "本文", "reviewer-1")
    assert review_v2_api._try_mark_running("session-8") is True  # 解除済みなので再度開始できる


def test_get_report_review_v2_404_before_any_run(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(review_v2_api, "_client", client)

    with pytest.raises(HTTPException) as exc_info:
        review_v2_api.get_report_review_v2("session-5")
    assert exc_info.value.status_code == 404


def test_save_human_expert_disposition_endpoint(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(review_v2_api, "_client", client)
    review_v2_service.start_run(client, "session-6", report_draft_id="draft-6", created_by="reviewer-1")

    result = review_v2_api.save_human_expert_disposition(
        "session-6", review_v2_api.SaveHumanExpertDispositionRequest(
            source="reading", finding_id="f1", disposition="agree", reason="妥当",
            reviewed_by="expert-1"))

    assert result["human_expert_dispositions"]["reading:f1"]["disposition"] == "agree"


def test_save_human_expert_disposition_endpoint_rejects_invalid_disposition(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(review_v2_api, "_client", client)
    review_v2_service.start_run(client, "session-7", report_draft_id="draft-7", created_by="reviewer-1")

    with pytest.raises(HTTPException) as exc_info:
        review_v2_api.save_human_expert_disposition(
            "session-7", review_v2_api.SaveHumanExpertDispositionRequest(
                source="reading", finding_id="f1", disposition="unknown", reviewed_by="expert-1"))
    assert exc_info.value.status_code == 400
