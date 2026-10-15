"""Geopolitical Intelligence OS: REVIEW MODE — Existing Report Import API（§17-22）。

既存Attachment/File infrastructure（geo_documents_store.extract_text、既存
report-drafts/{id}/importと同じ既存パターン）をそのまま再利用する。新しい独自File
storageは作らない。Expert AI Core・CQAS・S Ranking・Reading Exposure選定ロジックには
一切書き込まない。
"""
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ai_client import make_openai_client  # noqa: E402
from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

import geo_documents_store
import geo_ios_analysis_session_service as session_service
import geo_ios_report_draft_service as draft_service
import geo_ios_review_import_service as review_import_service

_config = load_config()
_client = SupabaseClient(_config)
_azure_client, _model = make_openai_client(_config)

geo_ios_review_import_router = APIRouter(prefix="/api/geo/ios/review-import")

# PowerPoint(.pptx)は既存geo_documents_store.extract_textが対応していない
# （python-pptx等の新規依存が必要、既存依存関係のみ再利用する今回の実装方針では未対応）。
# 明確なエラーで止め、黙ってプレーンテキスト誤変換しない。
_UNSUPPORTED_EXTENSIONS = (".pptx", ".ppt")


def _require_azure_client():
    if not _azure_client:
        raise HTTPException(status_code=503, detail="Azure OpenAI / OpenAI のAPIキーが未設定です")


@geo_ios_review_import_router.post("/{analysis_session_id}/import")
async def import_report_for_review(
    analysis_session_id: str, created_by: str = Form(...),
    file: UploadFile | None = File(None), pasted_text: str | None = Form(None),
):
    """Step1（§18）: PDF/Word/Text pasteのいずれかで既存レポートを取り込む。まだ評価は
    しない——テキスト抽出のみ行い、Context抽出・Reading再構成は後続の
    /generate-review-readingsで行う。"""
    session = session_service.get_session(_client, analysis_session_id)
    if not session:
        raise HTTPException(status_code=404, detail="analysis_session_idが見つかりません")
    if session.get("mode") != "review":
        raise HTTPException(status_code=400, detail="このsessionはREVIEW MODEではありません")

    if file is not None:
        filename = file.filename or "upload"
        if any(filename.lower().endswith(ext) for ext in _UNSUPPORTED_EXTENSIONS):
            raise HTTPException(
                status_code=400,
                detail="PowerPoint(.pptx/.ppt)は現時点で未対応です。PDF/Word/テキスト貼り付けをお使いください。")
        raw_bytes = await file.read()
        extracted = geo_documents_store.extract_text(raw_bytes, filename, file.content_type or "")
        if not extracted["ok"]:
            raise HTTPException(status_code=400, detail=f"文書からテキストを抽出できませんでした: {extracted['error']}")
        original_text = extracted["text"]
        original_filename = filename
        original_mime_type = file.content_type
    elif pasted_text and pasted_text.strip():
        original_text = pasted_text
        original_filename = None
        original_mime_type = "text/plain"
    else:
        raise HTTPException(status_code=400, detail="fileまたはpasted_textのいずれかが必要です")

    created = review_import_service.create_review_import(
        _client, analysis_session_id, original_text=original_text,
        original_filename=original_filename, original_mime_type=original_mime_type,
        created_by=created_by)
    return created


def _run_reconstruction_in_background(analysis_session_id: str, actor_id: str):
    try:
        review_import_service.run_reconstruction_and_alternatives(
            _client, _azure_client, _model, analysis_session_id, actor_id=actor_id)
    except Exception:  # noqa: BLE001 — 失敗はrun_reconstruction_and_alternatives内でstatus="failed"に記録済み
        pass


class GenerateReviewReadingsRequest(BaseModel):
    actor_id: str = "analyst"


@geo_ios_review_import_router.post("/{analysis_session_id}/generate-review-readings")
def generate_review_readings(analysis_session_id: str, req: GenerateReviewReadingsRequest,
                              background_tasks: BackgroundTasks):
    """Step1のCTA「この内容で読み方をレビューする」。Context抽出→Reading再構成→
    Reading A(Current)+Alternative(B-E)のmaterializeをBackgroundTasksで実行する
    （既存report-drafts/.../importと同じポーリングパターン）。"""
    _require_azure_client()
    review_import = review_import_service.get_review_import(_client, analysis_session_id)
    if not review_import:
        raise HTTPException(status_code=404, detail="まずimportを実行してください")
    background_tasks.add_task(_run_reconstruction_in_background, analysis_session_id, req.actor_id)
    return {"status": "processing"}


@geo_ios_review_import_router.get("/{analysis_session_id}")
def get_review_import_status(analysis_session_id: str):
    review_import = review_import_service.get_review_import(_client, analysis_session_id)
    if not review_import:
        raise HTTPException(status_code=404, detail="review_importがまだ作成されていません")
    return review_import


class SetRevisionStrategyRequest(BaseModel):
    selected_scenario_candidate_id: str
    strategy: str
    actor_id: str = "analyst"


@geo_ios_review_import_router.post("/{analysis_session_id}/revision-strategy")
def set_revision_strategy(analysis_session_id: str, req: SetRevisionStrategyRequest):
    """§21 Revision Strategy。Reading選択後にのみ意味を持つ（keep_and_reinforce=現在の
    読みを維持して補強／replace=選択したReadingへ変更／integrate=現在の読みと統合）。"""
    try:
        return review_import_service.set_revision_strategy(
            _client, analysis_session_id, selected_scenario_candidate_id=req.selected_scenario_candidate_id,
            strategy=req.strategy, actor_id=req.actor_id)
    except review_import_service.ReviewImportError as e:
        raise HTTPException(status_code=400, detail=str(e))


class GenerateRevisedDraftRequest(BaseModel):
    created_by: str


def _run_review_revision_in_background(report_draft_id: str, analysis_session_id: str):
    try:
        review_import = review_import_service.get_review_import(_client, analysis_session_id)
        session = session_service.get_session(_client, analysis_session_id)
        reconstructed = review_import.get("reconstructed_reading") or {}
        original_reading = reconstructed.get("primary_reading") or ""
        target_reading = original_reading
        strategy = review_import.get("revision_strategy")
        selected_id = review_import.get("selected_scenario_candidate_id")
        if strategy in ("replace", "integrate") and selected_id:
            selected_row = session_service.get_scenario_candidate(_client, selected_id)
            if selected_row:
                target_reading = (selected_row.get("content") or {}).get("why_this_matters") or original_reading
        extracted_context = review_import.get("extracted_context") or {}
        theme = extracted_context.get("analysis_theme") or (session or {}).get("title") or ""
        sections = draft_service.run_review_revision(
            _azure_client, _model, original_report_text=review_import["original_text"],
            original_reading=original_reading, target_reading=target_reading,
            strategy=strategy, theme=theme)
        draft_service.mark_draft_completed(
            _client, report_draft_id, sections=sections, expert_request_id=None, expert_response_id=None)
    except Exception as e:
        draft_service.mark_draft_failed(_client, report_draft_id, error_message=f"{type(e).__name__}: {e}")


@geo_ios_review_import_router.post("/{analysis_session_id}/generate-revised-draft")
def generate_revised_draft(analysis_session_id: str, req: GenerateRevisedDraftRequest,
                            background_tasks: BackgroundTasks):
    """§11 Step3「レポートをリバイズ」。既存Draft versioning（geo_ios_report_drafts、
    source="ai_generated"）へそのまま相乗りする——新しいDraft的storageは作らない。"""
    _require_azure_client()
    review_import = review_import_service.get_review_import(_client, analysis_session_id)
    if not review_import or not review_import.get("revision_strategy"):
        raise HTTPException(status_code=400, detail="revision_strategyが未設定です")
    created = draft_service.create_draft_version(
        _client, analysis_session_id, source="ai_generated", created_by=req.created_by,
        change_note=f"Review revision: {review_import['revision_strategy']}")
    background_tasks.add_task(_run_review_revision_in_background, created["report_draft_id"], analysis_session_id)
    return created
