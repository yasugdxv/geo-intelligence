"""Geopolitical Intelligence OS: Native Report Draft APIのテスト。

POST時点でstatus=pendingを即座に返しBackgroundTasksへ1件だけ登録すること、内部処理関数が
正常時completed・例外時failedへ遷移させること、Human Editのstale edit 409を確認する。
実際のAzure OpenAI呼び出しは行わない（geo_ios_expert_ai_adapterをmonkeypatch）。
"""
import asyncio
import sys
from pathlib import Path

from fastapi import BackgroundTasks, HTTPException
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_report_draft_api as draft_api  # noqa: E402
import geo_ios_report_draft_service as draft_service  # noqa: E402
import geo_ios_expert_ai_adapter as expert_ai_adapter  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _seed_session(client, session_id="session-1"):
    client.tables.setdefault("geo_ios_analysis_sessions", []).append({
        "analysis_session_id": session_id, "title": "Test", "theme": "テストテーマ",
        "user_id": "analyst-1", "status": "design_in_progress",
        "report_generation_mode": "native", "linked_report_project_id": None,
    })


def _create_request(**overrides):
    base = dict(analysis_session_id="session-1", created_by="analyst-1", source="ai_generated",
                sections=None, base_report_draft_id=None, change_note=None)
    base.update(overrides)
    return draft_api.CreateDraftRequest(**base)


def test_create_draft_ai_generated_returns_pending_and_schedules_one_task(monkeypatch):
    client = FakeSupabaseClient()
    _seed_session(client)
    monkeypatch.setattr(draft_api, "_client", client)
    monkeypatch.setattr(draft_api, "_azure_client", object())
    bg = BackgroundTasks()

    draft = draft_api.create_draft(_create_request(), bg)

    assert draft["status"] == "pending"
    assert len(bg.tasks) == 1
    assert bg.tasks[0].func is draft_api._run_draft_generation_in_background


def test_create_draft_unknown_session_returns_404(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(draft_api, "_client", client)
    with pytest.raises(HTTPException) as exc_info:
        draft_api.create_draft(_create_request(), BackgroundTasks())
    assert exc_info.value.status_code == 404


def test_create_draft_human_edit_stale_base_returns_409(monkeypatch):
    client = FakeSupabaseClient()
    _seed_session(client)
    monkeypatch.setattr(draft_api, "_client", client)
    v1 = draft_service.create_draft_version(client, "session-1", source="human_edit", created_by="a",
                                             sections=[{"heading": "H", "body": "B"}], base_report_draft_id=None)
    with pytest.raises(HTTPException) as exc_info:
        draft_api.create_draft(_create_request(
            source="human_edit", sections=[{"heading": "H2", "body": "B2"}],
            base_report_draft_id="wrong-id"), BackgroundTasks())
    assert exc_info.value.status_code == 409
    assert v1["version"] == 1  # 参照用（未使用警告避け・実際にv1が存在する前提を明示）


def test_background_draft_generation_marks_completed_on_success(monkeypatch):
    client = FakeSupabaseClient()
    _seed_session(client)
    monkeypatch.setattr(draft_api, "_client", client)
    monkeypatch.setattr(draft_api, "_azure_client", object())
    monkeypatch.setattr(draft_api, "_model", "gpt-test")
    monkeypatch.setattr(draft_api, "_fetch_design_and_situation",
                         lambda session_id: ({"investigation_question": "Q"}, {"main_scenario": {}}))

    def fake_run_draft_generation(client_, azure_, model_, *, design, situation, theme, actor_id):
        return {
            "draft_sections": [{"heading": "Executive assessment", "body": "本文", "supporting_evidence_ids": []}],
            "request_id": "req-1", "response_id": "resp-1",
        }
    monkeypatch.setattr(expert_ai_adapter, "run_draft_generation", fake_run_draft_generation)

    draft = draft_service.create_draft_version(client, "session-1", source="ai_generated", created_by="analyst-1")
    draft_api._run_draft_generation_in_background(draft["report_draft_id"], "session-1", "テーマ", "analyst-1")

    updated = draft_service.get_draft(client, draft["report_draft_id"])
    assert updated["status"] == "completed"
    assert updated["sections"][0]["heading"] == "Executive assessment"
    assert updated["content_hash"]


def test_background_draft_generation_marks_failed_on_exception(monkeypatch):
    client = FakeSupabaseClient()
    _seed_session(client)
    monkeypatch.setattr(draft_api, "_client", client)
    monkeypatch.setattr(draft_api, "_azure_client", object())
    monkeypatch.setattr(draft_api, "_model", "gpt-test")
    monkeypatch.setattr(draft_api, "_fetch_design_and_situation",
                         lambda session_id: ({}, {}))

    def failing_run_draft_generation(*args, **kwargs):
        raise RuntimeError("LLM呼び出しに失敗しました")
    monkeypatch.setattr(expert_ai_adapter, "run_draft_generation", failing_run_draft_generation)

    draft = draft_service.create_draft_version(client, "session-1", source="ai_generated", created_by="analyst-1")
    draft_api._run_draft_generation_in_background(draft["report_draft_id"], "session-1", "テーマ", "analyst-1")

    updated = draft_service.get_draft(client, draft["report_draft_id"])
    assert updated["status"] == "failed"
    assert "LLM呼び出しに失敗しました" in updated["error_message"]


def test_create_ai_review_requires_draft_completed(monkeypatch):
    client = FakeSupabaseClient()
    _seed_session(client)
    monkeypatch.setattr(draft_api, "_client", client)
    monkeypatch.setattr(draft_api, "_azure_client", object())
    draft = draft_service.create_draft_version(client, "session-1", source="ai_generated", created_by="analyst-1")

    with pytest.raises(HTTPException) as exc_info:
        draft_api.create_ai_review(
            draft["report_draft_id"], draft_api.RequestAiReviewRequest(requested_by="analyst-1"),
            BackgroundTasks())
    assert exc_info.value.status_code == 400


def test_background_ai_review_marks_completed_with_findings(monkeypatch):
    client = FakeSupabaseClient()
    _seed_session(client)
    monkeypatch.setattr(draft_api, "_client", client)
    monkeypatch.setattr(draft_api, "_azure_client", object())
    monkeypatch.setattr(draft_api, "_model", "gpt-test")
    monkeypatch.setattr(draft_api, "_fetch_design_and_situation", lambda session_id: ({}, {}))

    def fake_run_draft_ai_review(client_, azure_, model_, *, draft_sections, design, situation, theme, actor_id):
        return {"findings": [{"severity": "Major", "finding": "X"}], "request_id": "r", "response_id": "r"}
    monkeypatch.setattr(expert_ai_adapter, "run_draft_ai_review", fake_run_draft_ai_review)

    draft = draft_service.create_draft_version(client, "session-1", source="ai_generated", created_by="analyst-1")
    draft_service.mark_draft_completed(
        client, draft["report_draft_id"], sections=[{"heading": "H", "body": "B", "supporting_evidence_ids": []}],
        expert_request_id="r", expert_response_id="r")
    attempt = draft_service.create_ai_review_attempt(client, draft["report_draft_id"], requested_by="analyst-1")

    draft_api._run_ai_review_in_background(
        attempt["draft_ai_review_id"], draft["report_draft_id"], "session-1", "テーマ", "analyst-1")

    updated = draft_service.get_ai_review(client, attempt["draft_ai_review_id"])
    assert updated["status"] == "completed"
    assert updated["findings"][0]["severity"] == "Major"
    assert updated["findings"][0]["status"] == "open"


# ---- ai_edit_draft: チャット指示によるDraft書き換え -----------------------------

def test_ai_edit_draft_requires_completed_base_draft(monkeypatch):
    client = FakeSupabaseClient()
    _seed_session(client)
    monkeypatch.setattr(draft_api, "_client", client)
    monkeypatch.setattr(draft_api, "_azure_client", object())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(draft_api.ai_edit_draft(
            "session-1", background_tasks=BackgroundTasks(),
            actor_id="analyst-1", instruction="もっと簡潔に", file=None))
    assert exc_info.value.status_code == 400


def test_ai_edit_draft_requires_nonempty_instruction(monkeypatch):
    client = FakeSupabaseClient()
    _seed_session(client)
    monkeypatch.setattr(draft_api, "_client", client)
    monkeypatch.setattr(draft_api, "_azure_client", object())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(draft_api.ai_edit_draft(
            "session-1", background_tasks=BackgroundTasks(),
            actor_id="analyst-1", instruction="   ", file=None))
    assert exc_info.value.status_code == 400


def test_ai_edit_draft_creates_new_pending_version_and_schedules_one_task(monkeypatch):
    client = FakeSupabaseClient()
    _seed_session(client)
    monkeypatch.setattr(draft_api, "_client", client)
    monkeypatch.setattr(draft_api, "_azure_client", object())
    base = draft_service.create_draft_version(client, "session-1", source="ai_generated", created_by="analyst-1")
    draft_service.mark_draft_completed(
        client, base["report_draft_id"], sections=[{"heading": "H", "body": "元の本文", "supporting_evidence_ids": []}],
        expert_request_id="r", expert_response_id="r")
    bg = BackgroundTasks()

    edited = asyncio.run(draft_api.ai_edit_draft(
        "session-1", background_tasks=bg,
        actor_id="analyst-1", instruction="もっと簡潔に", file=None))

    assert edited["status"] == "pending"
    assert edited["version"] == base["version"] + 1
    assert edited["parent_report_draft_id"] == base["report_draft_id"]
    assert len(bg.tasks) == 1
    assert bg.tasks[0].func is draft_api._run_draft_ai_edit_in_background


# 2026-09-23追加、ユーザー指示「AIで編集するところに追加資料をアップする機能がいるね」。

def test_ai_edit_draft_rejects_when_attachment_text_extraction_fails(monkeypatch):
    client = FakeSupabaseClient()
    _seed_session(client)
    monkeypatch.setattr(draft_api, "_client", client)
    monkeypatch.setattr(draft_api, "_azure_client", object())
    monkeypatch.setattr(
        draft_api.geo_documents_store, "extract_text",
        lambda raw, filename, mime: {"ok": False, "error": "抽出処理エラー", "title": "", "text": ""})
    base = draft_service.create_draft_version(client, "session-1", source="ai_generated", created_by="analyst-1")
    draft_service.mark_draft_completed(
        client, base["report_draft_id"], sections=[{"heading": "H", "body": "元の本文", "supporting_evidence_ids": []}],
        expert_request_id="r", expert_response_id="r")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(draft_api.ai_edit_draft(
            "session-1", background_tasks=BackgroundTasks(), actor_id="analyst-1", instruction="修正して",
            file=_FakeUploadFile(b"broken", "broken.pdf", "application/pdf")))
    assert exc_info.value.status_code == 400


def test_ai_edit_draft_with_attachment_passes_extracted_text_to_background_task(monkeypatch):
    client = FakeSupabaseClient()
    _seed_session(client)
    monkeypatch.setattr(draft_api, "_client", client)
    monkeypatch.setattr(draft_api, "_azure_client", object())
    monkeypatch.setattr(
        draft_api.geo_documents_store, "extract_text",
        lambda raw, filename, mime: {"ok": True, "error": None, "title": "追加資料",
                                      "text": "追加資料の全文", "language": None, "published_at": None})
    base = draft_service.create_draft_version(client, "session-1", source="ai_generated", created_by="analyst-1")
    draft_service.mark_draft_completed(
        client, base["report_draft_id"], sections=[{"heading": "H", "body": "元の本文", "supporting_evidence_ids": []}],
        expert_request_id="r", expert_response_id="r")
    bg = BackgroundTasks()

    asyncio.run(draft_api.ai_edit_draft(
        "session-1", background_tasks=bg, actor_id="analyst-1", instruction="この資料を踏まえて修正して",
        file=_FakeUploadFile(b"dummy", "material.pdf", "application/pdf")))

    assert len(bg.tasks) == 1
    assert bg.tasks[0].args[-1] == "追加資料の全文"


def test_background_draft_ai_edit_marks_completed_with_new_sections(monkeypatch):
    client = FakeSupabaseClient()
    _seed_session(client)
    monkeypatch.setattr(draft_api, "_client", client)
    monkeypatch.setattr(draft_api, "_azure_client", object())
    monkeypatch.setattr(draft_api, "_model", "gpt-test")
    monkeypatch.setattr(draft_api, "_fetch_design_and_situation", lambda session_id: ({}, {}))
    base = draft_service.create_draft_version(client, "session-1", source="ai_generated", created_by="analyst-1")
    draft_service.mark_draft_completed(
        client, base["report_draft_id"], sections=[{"heading": "H", "body": "元の本文", "supporting_evidence_ids": []}],
        expert_request_id="r", expert_response_id="r")
    edited = draft_service.create_draft_version(client, "session-1", source="ai_generated", created_by="analyst-1")

    def fake_run_draft_ai_edit(client_, azure_, model_, *, draft_sections, design, situation, theme, instruction,
                                actor_id, additional_material_text=None):
        assert draft_sections[0]["body"] == "元の本文"
        assert instruction == "もっと簡潔に"
        return {"draft_sections": [{"heading": "H", "body": "書き直し後の本文"}],
                "request_id": "req-2", "response_id": "resp-2"}
    monkeypatch.setattr(expert_ai_adapter, "run_draft_ai_edit", fake_run_draft_ai_edit)

    draft_api._run_draft_ai_edit_in_background(
        edited["report_draft_id"], base["report_draft_id"], "session-1", "テーマ", "もっと簡潔に", "analyst-1")

    updated = draft_service.get_draft(client, edited["report_draft_id"])
    assert updated["status"] == "completed"
    assert updated["sections"][0]["body"] == "書き直し後の本文"


# ---- import_draft: アップロード文書からのDraft取込 ------------------------------

class _FakeUploadFile:
    def __init__(self, content: bytes, filename: str, content_type: str):
        self._content = content
        self.filename = filename
        self.content_type = content_type

    async def read(self):
        return self._content


def test_import_draft_rejects_when_text_extraction_fails(monkeypatch):
    client = FakeSupabaseClient()
    _seed_session(client)
    monkeypatch.setattr(draft_api, "_client", client)
    monkeypatch.setattr(draft_api, "_azure_client", object())
    monkeypatch.setattr(
        draft_api.geo_documents_store, "extract_text",
        lambda raw, filename, mime: {"ok": False, "error": "抽出処理エラー", "title": "", "text": ""})

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(draft_api.import_draft(
            "session-1", created_by="analyst-1",
            file=_FakeUploadFile(b"broken", "broken.pdf", "application/pdf"),
            background_tasks=BackgroundTasks()))
    assert exc_info.value.status_code == 400


def test_import_draft_creates_pending_uploaded_draft_and_schedules_one_task(monkeypatch):
    client = FakeSupabaseClient()
    _seed_session(client)
    monkeypatch.setattr(draft_api, "_client", client)
    monkeypatch.setattr(draft_api, "_azure_client", object())
    monkeypatch.setattr(
        draft_api.geo_documents_store, "extract_text",
        lambda raw, filename, mime: {"ok": True, "error": None, "title": "既存レポート",
                                      "text": "アップロードされた文書の全文", "language": None, "published_at": None})
    bg = BackgroundTasks()

    created = asyncio.run(draft_api.import_draft(
        "session-1", created_by="analyst-1",
        file=_FakeUploadFile(b"dummy pdf bytes", "existing-report.pdf", "application/pdf"),
        background_tasks=bg))

    assert created["status"] == "pending"
    assert created["source"] == "uploaded"
    assert len(bg.tasks) == 1
    assert bg.tasks[0].func is draft_api._run_draft_import_in_background


def test_import_draft_rejects_unsupported_extension_before_creating_draft(monkeypatch):
    client = FakeSupabaseClient()
    _seed_session(client)
    monkeypatch.setattr(draft_api, "_client", client)
    monkeypatch.setattr(draft_api, "_azure_client", object())
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(draft_api.import_draft(
            "session-1", created_by="analyst-1",
            file=_FakeUploadFile(b"data", "sheet.xlsx", "application/vnd.ms-excel"),
            background_tasks=BackgroundTasks()))
    assert exc_info.value.status_code == 400
    assert "対応していない" in exc_info.value.detail
    assert draft_service.get_current_draft(client, "session-1") is None  # 失敗Draftを作らない


def test_import_draft_rejects_too_long_text_before_creating_draft(monkeypatch):
    client = FakeSupabaseClient()
    _seed_session(client)
    monkeypatch.setattr(draft_api, "_client", client)
    monkeypatch.setattr(draft_api, "_azure_client", object())
    monkeypatch.setattr(
        draft_api.geo_documents_store, "extract_text",
        lambda raw, filename, mime: {"ok": True, "error": None, "title": "", "language": None,
                                      "published_at": None,
                                      "text": "あ" * (draft_service.IMPORT_MAX_TEXT_CHARS + 1)})
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(draft_api.import_draft(
            "session-1", created_by="analyst-1",
            file=_FakeUploadFile(b"dummy", "long.pdf", "application/pdf"),
            background_tasks=BackgroundTasks()))
    assert exc_info.value.status_code == 413
    assert "長すぎ" in exc_info.value.detail
    assert draft_service.get_current_draft(client, "session-1") is None


def test_background_draft_import_marks_completed_with_sections_from_llm(monkeypatch):
    client = FakeSupabaseClient()
    _seed_session(client)
    monkeypatch.setattr(draft_api, "_client", client)
    monkeypatch.setattr(draft_api, "_azure_client", object())
    monkeypatch.setattr(draft_api, "_model", "gpt-test")
    created = draft_service.create_draft_version(client, "session-1", source="uploaded", created_by="analyst-1")

    def fake_run_document_import(azure_, model_, *, raw_text, theme):
        assert raw_text == "アップロードされた文書の全文"
        return [{"heading": "既存レポートの内容", "body": raw_text}]
    monkeypatch.setattr(draft_service, "run_document_import", fake_run_document_import)

    draft_api._run_draft_import_in_background(
        created["report_draft_id"], "アップロードされた文書の全文", "テーマ")

    updated = draft_service.get_draft(client, created["report_draft_id"])
    assert updated["status"] == "completed"
    assert updated["sections"][0]["heading"] == "既存レポートの内容"


def test_background_draft_import_marks_failed_on_exception(monkeypatch):
    client = FakeSupabaseClient()
    _seed_session(client)
    monkeypatch.setattr(draft_api, "_client", client)
    monkeypatch.setattr(draft_api, "_azure_client", object())
    monkeypatch.setattr(draft_api, "_model", "gpt-test")
    created = draft_service.create_draft_version(client, "session-1", source="uploaded", created_by="analyst-1")

    def failing_import(*args, **kwargs):
        raise RuntimeError("セクション分割に失敗しました")
    monkeypatch.setattr(draft_service, "run_document_import", failing_import)

    draft_api._run_draft_import_in_background(created["report_draft_id"], "本文", "テーマ")

    updated = draft_service.get_draft(client, created["report_draft_id"])
    assert updated["status"] == "failed"
    assert "セクション分割に失敗しました" in updated["error_message"]
