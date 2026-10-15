"""Geopolitical Intelligence OS: 前提整理（Intake）Chat + Analysis Context + Session Attachment API。

/api/geo/ios/analysis-sessions/{id}/intake/* に配置する（既存geo_ios_analysis_session_api.py
と同じprefix配下、新しい名前空間は増やさない）。既存Analysis Session
（geo_ios_analysis_session_service）をそのまま参照するのみで、Session自体のCRUDは
既存APIを使う（本ファイルでは作らない）。
"""
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ai_client import make_openai_client  # noqa: E402
from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

import geo_ios_intake_service as intake_service
import geo_llm_common

_config = load_config()
_client = SupabaseClient(_config)
_azure_client, _model = make_openai_client(_config)

geo_ios_intake_router = APIRouter(prefix="/api/geo/ios/analysis-sessions")


def _require_azure_client():
    if not _azure_client:
        raise HTTPException(status_code=503, detail="Azure OpenAI / OpenAI のAPIキーが未設定です")


# ---- Analysis Context ---------------------------------------------------------

@geo_ios_intake_router.get("/{analysis_session_id}/intake/context")
def get_current_context(analysis_session_id: str):
    context = intake_service.get_current_context(_client, analysis_session_id)
    if not context:
        raise HTTPException(status_code=404, detail="Analysis Contextがまだ整理されていません")
    return context


@geo_ios_intake_router.get("/{analysis_session_id}/intake/context/history")
def list_context_history(analysis_session_id: str):
    return {"versions": intake_service.list_context_history(_client, analysis_session_id)}


# ---- Intake Turn（前提整理チャット） -------------------------------------------

class SendIntakeMessageRequest(BaseModel):
    user_message: str
    actor_id: str


@geo_ios_intake_router.post("/{analysis_session_id}/intake/messages")
def send_intake_message(analysis_session_id: str, req: SendIntakeMessageRequest):
    _require_azure_client()
    try:
        return intake_service.send_intake_message(
            _client, _azure_client, _model, analysis_session_id,
            user_message=req.user_message, created_by=req.actor_id)
    except intake_service.IntakeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@geo_ios_intake_router.get("/{analysis_session_id}/intake/messages")
def list_intake_turns(analysis_session_id: str):
    return {"turns": intake_service.list_turns(_client, analysis_session_id)}


# ---- Session Attachment -------------------------------------------------------

@geo_ios_intake_router.post("/{analysis_session_id}/intake/attachments")
async def upload_session_attachment(analysis_session_id: str, actor_id: str = Form(...),
                                     file: UploadFile = File(...)):
    """アップロード後、自動で1往復のIntake Turnを発生させる（§4: 資料が追加されたら
    AIが前提整理に反映し、追加Q&Aできるようにする）。Turn発生にはAzure OpenAIが必要——
    未設定の場合は資料の保存自体は成功させ、Turn発生のみスキップする（アップロード自体を
    AI可用性に依存させない）。"""
    raw_bytes = await file.read()
    try:
        attachment = intake_service.add_attachment(
            _client, analysis_session_id, filename=file.filename or "upload",
            mime_type=file.content_type or "", raw_bytes=raw_bytes, uploaded_by=actor_id)
    except intake_service.IntakeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 構造化要約の生成はTurn発生より前に行う——資料追加直後の自動Turn自体が、この資料の
    # 内容を踏まえて反応する最初の機会であり、ここで先頭2000文字のfallbackのままだと
    # 後半の人物・事実がその後のTurnでも一度も見えないまま固定されてしまう（ユーザー指示、
    # 2026-09-24b、Hungary Blind Testで発覚）。Turn発生と同じく、失敗してもアップロード
    # 自体は既に成功済みという前提でtry/exceptする。
    if _azure_client:
        try:
            attachment = intake_service.generate_attachment_structured_context(
                _client, _azure_client, _model, attachment["attachment_id"])
        except (intake_service.IntakeError, geo_llm_common.GeoLLMError):
            pass

    turn_result = None
    if _azure_client:
        try:
            turn_result = intake_service.send_attachment_added_turn(
                _client, _azure_client, _model, analysis_session_id,
                attachment=attachment, created_by=actor_id)
        except intake_service.IntakeError:
            turn_result = None
    return {
        "attachment": attachment,
        "turn": turn_result["turn"] if turn_result else None,
        "context": turn_result["context"] if turn_result else None,
    }


@geo_ios_intake_router.get("/{analysis_session_id}/intake/attachments")
def list_session_attachments(analysis_session_id: str):
    return {"attachments": intake_service.list_attachments(_client, analysis_session_id)}
