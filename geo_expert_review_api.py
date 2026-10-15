"""地政学ナレッジ＆インテリジェンスDB Phase2B-2: Internal Expert Learning Layer APIルーター。

既存/api/geo/knowledge/*（Phase1/2A/2B）とは独立したパス空間（/api/geo/expert-review/*）に
マウントする。既存api_server.pyの変更は「このモジュールのimportとinclude_routerの2行追加」のみ
（既存geo_knowledge_api.py分離時と同じ方針。1250行超の既存ファイルへの大量追記を避けるため）。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402
from ai_client import make_openai_client  # noqa: E402

import geo_expert_review_service as review_service
import geo_knowledge_service

_config = load_config()
_client = SupabaseClient(_config)
_azure_client, _model = make_openai_client(_config)

geo_expert_review_router = APIRouter(prefix="/api/geo/expert-review")


def _require_azure():
    if not _azure_client:
        raise HTTPException(status_code=503, detail="Azure OpenAI / OpenAI のAPIキーが未設定です")


def _wrap_value_error(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except review_service.ExpertReviewChangeNotPendingError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except geo_knowledge_service.InvalidStatusTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class StartReviewSessionRequest(BaseModel):
    theme_id: str
    reviewer_id: str
    final_text: str
    case_id: str | None = None
    report_id: str | None = None
    draft_text: str | None = None
    draft_expert_response_id: str | None = None
    reviewer_label: str | None = None
    review_type: str = "case_report"
    is_demo: bool = False


@geo_expert_review_router.post("/sessions")
def start_review_session(req: StartReviewSessionRequest):
    return _wrap_value_error(review_service.start_review_session, _client, **req.model_dump())


@geo_expert_review_router.get("/sessions/{review_session_id}")
def get_review_session(review_session_id: str):
    rows = _client.select("geo_expert_review_sessions", {
        "select": "*", "review_session_id": f"eq.{review_session_id}", "limit": "1",
    })
    if not rows:
        raise HTTPException(status_code=404, detail="review_session_idが見つかりません")
    return rows[0]


class AddReviewNoteRequest(BaseModel):
    comment_text: str
    note_type: str = "free_text"
    anchor_excerpt: str | None = None
    author: str | None = None
    source_format: str | None = None


@geo_expert_review_router.post("/sessions/{review_session_id}/notes")
def add_review_note(review_session_id: str, req: AddReviewNoteRequest):
    return _wrap_value_error(review_service.add_review_note, _client,
                              review_session_id=review_session_id, **req.model_dump())


@geo_expert_review_router.get("/sessions/{review_session_id}/notes")
def list_review_notes(review_session_id: str):
    return {"notes": _client.select("geo_expert_review_notes", {
        "select": "*", "review_session_id": f"eq.{review_session_id}", "order": "created_at.asc",
    })}


class ExtractDeltasRequest(BaseModel):
    actor_id: str
    model: str | None = None


@geo_expert_review_router.post("/sessions/{review_session_id}/extract-deltas")
def extract_deltas(review_session_id: str, req: ExtractDeltasRequest):
    _require_azure()
    return _wrap_value_error(review_service.extract_deltas, _client, _azure_client,
                              req.model or _model, review_session_id, actor_id=req.actor_id)


@geo_expert_review_router.get("/sessions/{review_session_id}/changes")
def list_review_changes(review_session_id: str):
    return {"changes": _client.select("geo_expert_review_changes", {
        "select": "*", "review_session_id": f"eq.{review_session_id}", "order": "created_at.asc",
    })}


class PromoteChangeRequest(BaseModel):
    reviewer_id: str
    reviewer_role: str = "expert_reviewer"
    edited_content: dict | None = None


@geo_expert_review_router.post("/changes/{change_id}/promote")
def promote_change(change_id: str, req: PromoteChangeRequest):
    return _wrap_value_error(review_service.promote_change, _client, change_id=change_id, **req.model_dump())


class ReinforceChangeRequest(BaseModel):
    reviewer_id: str
    reinforced_knowledge_item_id: str


@geo_expert_review_router.post("/changes/{change_id}/reinforce")
def reinforce_change(change_id: str, req: ReinforceChangeRequest):
    return _wrap_value_error(review_service.reinforce_change, _client, change_id=change_id, **req.model_dump())


class ChangeActorRequest(BaseModel):
    reviewer_id: str


@geo_expert_review_router.post("/changes/{change_id}/keep-case-only")
def keep_case_only(change_id: str, req: ChangeActorRequest):
    return _wrap_value_error(review_service.keep_case_only, _client, change_id=change_id, **req.model_dump())


@geo_expert_review_router.post("/changes/{change_id}/reject")
def reject_change(change_id: str, req: ChangeActorRequest):
    return _wrap_value_error(review_service.reject_change, _client, change_id=change_id, **req.model_dump())
