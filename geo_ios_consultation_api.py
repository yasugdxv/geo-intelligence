"""Geopolitical Intelligence OS: Consultation Turns API（切り口相談/調査方針/プレレビュー）。

POST /consultation-turns はExpert AI呼び出し（実測1〜3分）をBackgroundTasksで実行し、
即座にstatus=pendingの行を返す。Frontendはconsultation_turn_idをポーリングして完了を
検知する——ページ離脱・再訪してもDBのstatusを見れば進捗が分かる（ユーザー要件:
「分析中と表示して一旦離れても戻ってこれるように」）。フルパワーの標準実行を維持する
（高速化のための簡略化はしない、というユーザー判断）。
"""
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from ai_client import make_openai_client  # noqa: E402
from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

import geo_ios_consultation_service as consultation_service
import geo_ios_expert_ai_adapter as expert_ai_adapter

_config = load_config()
_client = SupabaseClient(_config)
_azure_client, _model = make_openai_client(_config)

geo_ios_consultation_router = APIRouter(prefix="/api/geo/ios/consultation-turns")


def _require_azure_client():
    if not _azure_client:
        raise HTTPException(status_code=503, detail="Azure OpenAI / OpenAI のAPIキーが未設定です")


class CreateConsultationTurnRequest(BaseModel):
    analysis_session_id: str
    consultation_mode: str
    user_message: str
    draft_text: str | None = None
    created_by: str


def _with_answer(turn: dict) -> dict:
    if turn and turn.get("status") == "completed" and turn.get("expert_response_id"):
        rows = _client.select("geo_expert_responses", {
            "select": "response_json", "response_id": f"eq.{turn['expert_response_id']}", "limit": "1"})
        if rows:
            turn = {**turn, "answer": consultation_service.build_answer_summary(rows[0]["response_json"])}
    return turn


def _run_turn_in_background(consultation_turn_id: str, *, consultation_mode: str,
                             user_message: str, draft_text: str | None, actor_id: str):
    """BackgroundTasksから呼ばれる。FastAPIのBackgroundTaskは例外を投げても呼び出し元
    （HTTPレスポンス）へは伝播しないため、ここで必ず捕捉しTurnをfailedへ更新する
    （放置するとTurnがpendingのまま残り、Frontendが永久にポーリングし続けてしまう）。"""
    try:
        expert_mode = consultation_service.MODE_TO_EXPERT_MODE[consultation_mode]
        query_text = user_message
        if consultation_mode == "pre_review":
            query_text = f"以下のドラフトをレビューしてください。\n\n【依頼内容】\n{user_message}"
        result = expert_ai_adapter.run_consultation_turn(
            _client, _azure_client, _model, mode=expert_mode, query_text=query_text,
            draft_text=draft_text, actor_id=actor_id)
        consultation_service.mark_completed(
            _client, consultation_turn_id,
            expert_request_id=result["request_id"], expert_response_id=result["response_id"])
    except Exception as e:
        consultation_service.mark_failed(
            _client, consultation_turn_id, error_message=f"{type(e).__name__}: {e}")


@geo_ios_consultation_router.post("")
def create_consultation_turn(req: CreateConsultationTurnRequest, background_tasks: BackgroundTasks):
    _require_azure_client()
    try:
        turn = consultation_service.create_pending_turn(
            _client, req.analysis_session_id, consultation_mode=req.consultation_mode,
            user_message=req.user_message, draft_text=req.draft_text, created_by=req.created_by)
    except consultation_service.ConsultationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    background_tasks.add_task(
        _run_turn_in_background, turn["consultation_turn_id"],
        consultation_mode=req.consultation_mode, user_message=req.user_message,
        draft_text=req.draft_text, actor_id=req.created_by)
    return turn


@geo_ios_consultation_router.get("/{consultation_turn_id}")
def get_consultation_turn(consultation_turn_id: str):
    turn = consultation_service.get_turn(_client, consultation_turn_id)
    if not turn:
        raise HTTPException(status_code=404, detail="consultation_turn_idが見つかりません")
    return _with_answer(turn)


@geo_ios_consultation_router.get("")
def list_consultation_turns(analysis_session_id: str, consultation_mode: str):
    turns = consultation_service.list_turns(
        _client, analysis_session_id, consultation_mode=consultation_mode)
    return {"turns": [_with_answer(t) for t in turns]}
