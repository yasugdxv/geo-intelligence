"""Geopolitical Intelligence Operating System (IOS): Native Report Draft API。

POST /report-drafts（source=ai_generated）とPOST /{report_draft_id}/ai-review は
Expert AI呼び出しをBackgroundTasksで実行し、即座にstatus=pendingの行を返す
（既存geo_ios_consultation_api.pyと同じパターン）。Frontendはポーリングして完了を検知する。

新しいLLM呼び出しはdraft_generationモード（新規追加）とdraft_review（既存モード、無改修、
Context拡張のみ）の2種のみで、CQAS生成・ranking・Actor選定・Retrieval等の既存経路には
一切書き込まない。
"""
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ai_client import make_openai_client  # noqa: E402
from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

import geo_documents_store
import geo_ios_analysis_session_service as session_service
import geo_ios_expert_ai_adapter as expert_ai_adapter
import geo_ios_report_draft_service as draft_service
import geo_ios_situation_presentation_service as presentation_service

_config = load_config()
_client = SupabaseClient(_config)
_azure_client, _model = make_openai_client(_config)

geo_ios_report_draft_router = APIRouter(prefix="/api/geo/ios/report-drafts")


def _require_azure_client():
    if not _azure_client:
        raise HTTPException(status_code=503, detail="Azure OpenAI / OpenAI のAPIキーが未設定です")


def _fetch_design_and_situation(analysis_session_id: str) -> tuple[dict, dict]:
    """geo_ios_situation_presentation_api.pyのget_situation_presentationと同じ取得ロジック
    （新しいLLM呼び出しはしない、既存の永続化済みデータの読み取りのみ）。"""
    design = session_service.get_current_design(_client, analysis_session_id)
    if not design:
        raise HTTPException(status_code=400, detail="Analysis Designがまだ生成されていません")

    candidates = session_service.list_scenario_candidates(_client, analysis_session_id)
    response_id = None
    for candidate in candidates:
        response_id = ((candidate.get("content") or {}).get("source_cqas") or {}).get("response_id")
        if response_id:
            break
    if not response_id:
        raise HTTPException(status_code=400, detail="Scenario候補がまだ生成されていません")

    rows = _client.select("geo_expert_responses", {
        "select": "response_json", "response_id": f"eq.{response_id}", "limit": "1"})
    if not rows:
        raise HTTPException(status_code=404, detail=f"response_id={response_id}のExpert AI応答が見つかりません")
    response_json = rows[0]["response_json"]

    situation = presentation_service.strip_debug_sources(
        presentation_service.build_situation_presentation(
            design=design, candidates=candidates, response_json=response_json))
    return design, situation


def _run_draft_generation_in_background(report_draft_id: str, analysis_session_id: str,
                                         theme: str, created_by: str):
    try:
        design, situation = _fetch_design_and_situation(analysis_session_id)
        result = expert_ai_adapter.run_draft_generation(
            _client, _azure_client, _model, design=design, situation=situation,
            theme=theme, actor_id=created_by)
        draft_service.mark_draft_completed(
            _client, report_draft_id, sections=result["draft_sections"],
            expert_request_id=result["request_id"], expert_response_id=result["response_id"])
    except Exception as e:
        draft_service.mark_draft_failed(_client, report_draft_id, error_message=f"{type(e).__name__}: {e}")


def _run_draft_ai_edit_in_background(report_draft_id: str, base_report_draft_id: str,
                                      analysis_session_id: str, theme: str, instruction: str,
                                      created_by: str, additional_material_text: str | None = None):
    try:
        base_draft = draft_service.get_draft(_client, base_report_draft_id)
        design, situation = _fetch_design_and_situation(analysis_session_id)
        result = expert_ai_adapter.run_draft_ai_edit(
            _client, _azure_client, _model, draft_sections=(base_draft or {}).get("sections") or [],
            design=design, situation=situation, theme=theme, instruction=instruction,
            additional_material_text=additional_material_text, actor_id=created_by)
        draft_service.mark_draft_completed(
            _client, report_draft_id, sections=result["draft_sections"],
            expert_request_id=result["request_id"], expert_response_id=result["response_id"])
    except Exception as e:
        draft_service.mark_draft_failed(_client, report_draft_id, error_message=f"{type(e).__name__}: {e}")


def _run_draft_import_in_background(report_draft_id: str, raw_text: str, theme: str):
    try:
        sections = draft_service.run_document_import(_azure_client, _model, raw_text=raw_text, theme=theme)
        draft_service.mark_draft_completed(
            _client, report_draft_id, sections=sections, expert_request_id=None, expert_response_id=None)
    except Exception as e:
        draft_service.mark_draft_failed(_client, report_draft_id, error_message=f"{type(e).__name__}: {e}")


def _run_ai_review_in_background(draft_ai_review_id: str, report_draft_id: str,
                                  analysis_session_id: str, theme: str, requested_by: str):
    try:
        draft = draft_service.get_draft(_client, report_draft_id)
        design, situation = _fetch_design_and_situation(analysis_session_id)
        result = expert_ai_adapter.run_draft_ai_review(
            _client, _azure_client, _model, draft_sections=draft.get("sections") or [],
            design=design, situation=situation, theme=theme, actor_id=requested_by)
        draft_service.mark_ai_review_completed(
            _client, draft_ai_review_id, findings=result["findings"],
            expert_request_id=result["request_id"], expert_response_id=result["response_id"])
    except Exception as e:
        draft_service.mark_ai_review_failed(
            _client, draft_ai_review_id, error_message=f"{type(e).__name__}: {e}")


class CreateDraftRequest(BaseModel):
    analysis_session_id: str
    created_by: str
    source: str
    sections: list | None = None
    base_report_draft_id: str | None = None
    change_note: str | None = None


@geo_ios_report_draft_router.post("")
def create_draft(req: CreateDraftRequest, background_tasks: BackgroundTasks):
    session = session_service.get_session(_client, req.analysis_session_id)
    if not session:
        raise HTTPException(status_code=404, detail="analysis_session_idが見つかりません")

    try:
        created = draft_service.create_draft_version(
            _client, req.analysis_session_id, source=req.source, created_by=req.created_by,
            sections=req.sections, base_report_draft_id=req.base_report_draft_id,
            change_note=req.change_note)
    except draft_service.StaleDraftEditError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except draft_service.ReportDraftError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if req.source == "ai_generated":
        _require_azure_client()
        background_tasks.add_task(
            _run_draft_generation_in_background, created["report_draft_id"],
            req.analysis_session_id, session.get("theme") or session.get("title") or "",
            req.created_by)
    return created


@geo_ios_report_draft_router.post("/{analysis_session_id}/ai-edit")
async def ai_edit_draft(
    analysis_session_id: str, background_tasks: BackgroundTasks,
    actor_id: str = Form(...), instruction: str = Form(...),
    file: UploadFile | None = File(None),
):
    """チャット指示によるDraft書き換え。既存create_draft(source=ai_generated)と同じ
    immutable versioningパターンをそのまま使う——in-place更新は行わない。

    2026-09-23変更、ユーザー指示「AIで編集するところに追加資料をアップする機能が
    いるね」: JSON bodyからmultipart/form-dataへ変更し、既存import_draftと同じ
    geo_documents_store.extract_textでオプショナルな添付資料をテキスト抽出する
    （抽出できない場合はエラーで止める——黙って無視しない）。"""
    _require_azure_client()
    if not instruction.strip():
        raise HTTPException(status_code=400, detail="instructionは必須です")
    session = session_service.get_session(_client, analysis_session_id)
    if not session:
        raise HTTPException(status_code=404, detail="analysis_session_idが見つかりません")
    base_draft = draft_service.get_current_draft(_client, analysis_session_id)
    if not base_draft or base_draft.get("status") != "completed":
        raise HTTPException(status_code=400, detail="編集対象のDraftがまだ生成されていません")

    additional_material_text = None
    if file is not None:
        raw_bytes = await file.read()
        extracted = geo_documents_store.extract_text(raw_bytes, file.filename or "upload", file.content_type or "")
        if not extracted["ok"]:
            raise HTTPException(status_code=400, detail=f"添付資料からテキストを抽出できませんでした: {extracted['error']}")
        additional_material_text = extracted["text"]

    created = draft_service.create_draft_version(
        _client, analysis_session_id, source="ai_generated", created_by=actor_id,
        change_note=instruction)
    background_tasks.add_task(
        _run_draft_ai_edit_in_background, created["report_draft_id"], base_draft["report_draft_id"],
        analysis_session_id, session.get("theme") or session.get("title") or "", instruction,
        actor_id, additional_material_text)
    return created


@geo_ios_report_draft_router.post("/{analysis_session_id}/import")
async def import_draft(
    analysis_session_id: str, created_by: str = Form(...), file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None,
):
    """文書アップロードによるDraft取込（ユーザー指示: 「生成以外にもアップロードする
    オプションがあるべき」）。抽出は既存geo_documents_store.extract_text（docx/pdf/
    html/textを既に扱える、Report Studio等でも使われている共通処理）をそのまま使い、
    セクション分割のみAIに行わせる（run_document_import、Expert AI Engineは経由しない）。"""
    _require_azure_client()
    session = session_service.get_session(_client, analysis_session_id)
    if not session:
        raise HTTPException(status_code=404, detail="analysis_session_idが見つかりません")

    # 処理（Draft作成・AI呼び出し）を始める前に、形式・サイズ・本文の長さを確認し、
    # 合わない場合は理由つきで断る（失敗したDraftを作らない）。
    raw_bytes = await file.read()
    file_problem = draft_service.validate_import_file(file.filename or "", len(raw_bytes))
    if file_problem:
        raise HTTPException(status_code=413 if len(raw_bytes) > draft_service.IMPORT_MAX_FILE_BYTES else 400,
                            detail=file_problem)
    extracted = geo_documents_store.extract_text(raw_bytes, file.filename or "upload", file.content_type or "")
    if not extracted["ok"]:
        raise HTTPException(status_code=400, detail=f"文書からテキストを抽出できませんでした: {extracted['error']}")
    text_problem = draft_service.validate_import_text(extracted["text"])
    if text_problem:
        raise HTTPException(status_code=413 if extracted["text"].strip() else 400, detail=text_problem)

    created = draft_service.create_draft_version(
        _client, analysis_session_id, source="uploaded", created_by=created_by,
        change_note=f"アップロード: {file.filename}")
    background_tasks.add_task(
        _run_draft_import_in_background, created["report_draft_id"], extracted["text"],
        session.get("theme") or session.get("title") or "")
    return created


@geo_ios_report_draft_router.get("/current")
def get_current_draft(analysis_session_id: str):
    current = draft_service.get_current_draft(_client, analysis_session_id)
    if not current:
        raise HTTPException(status_code=404, detail="Draftがまだ生成されていません")
    return current


@geo_ios_report_draft_router.get("")
def list_draft_history(analysis_session_id: str):
    return {"drafts": draft_service.list_draft_history(_client, analysis_session_id)}


class RequestAiReviewRequest(BaseModel):
    requested_by: str


@geo_ios_report_draft_router.post("/{report_draft_id}/ai-review")
def create_ai_review(report_draft_id: str, req: RequestAiReviewRequest,
                      background_tasks: BackgroundTasks):
    _require_azure_client()
    draft = draft_service.get_draft(_client, report_draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="report_draft_idが見つかりません")
    if draft.get("status") != "completed":
        raise HTTPException(status_code=400, detail="Draftの生成が完了していません")

    created = draft_service.create_ai_review_attempt(
        _client, report_draft_id, requested_by=req.requested_by)

    session = session_service.get_session(_client, draft["analysis_session_id"])
    theme = (session or {}).get("theme") or (session or {}).get("title") or ""
    background_tasks.add_task(
        _run_ai_review_in_background, created["draft_ai_review_id"], report_draft_id,
        draft["analysis_session_id"], theme, req.requested_by)
    return created


@geo_ios_report_draft_router.get("/{report_draft_id}/ai-review/latest")
def get_latest_ai_review(report_draft_id: str):
    review = draft_service.get_latest_ai_review(_client, report_draft_id)
    if not review:
        raise HTTPException(status_code=404, detail="AI Reviewがまだ実行されていません")
    return review


@geo_ios_report_draft_router.get("/{report_draft_id}/ai-review/history")
def list_ai_review_history(report_draft_id: str):
    return {"reviews": draft_service.list_ai_review_history(_client, report_draft_id)}


class WaiveFindingRequest(BaseModel):
    finding_id: str
    waiver_reason: str
    waived_by: str


@geo_ios_report_draft_router.patch("/{report_draft_id}/ai-review/{draft_ai_review_id}/waive")
def waive_finding(report_draft_id: str, draft_ai_review_id: str, req: WaiveFindingRequest):
    try:
        return draft_service.waive_finding(
            _client, draft_ai_review_id, finding_id=req.finding_id,
            waiver_reason=req.waiver_reason, waived_by=req.waived_by)
    except draft_service.ReportDraftError as e:
        raise HTTPException(status_code=400, detail=str(e))
