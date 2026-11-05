"""ナレッジ追加 API（チャットで依頼 → AIが調査・登録 → 人が確認）。

認証済みのIDだけが使える（SSO/資格情報）。人の確認は、既存の
POST /api/geo/knowledge/items/{id}/confirm-ai-approval を使う（レビュー担当・管理担当の権限が必要）。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ai_client import make_openai_client  # noqa: E402
from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

import geo_ios_identity
import geo_knowledge_intake_service as svc
import geo_llm_common

_config = load_config()
_client = SupabaseClient(_config)
_azure_client, _model = make_openai_client(_config)

geo_knowledge_intake_router = APIRouter(prefix="/api/geo/knowledge-intake")


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except svc.IntakeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except geo_llm_common.GeoLLMError:
        raise HTTPException(status_code=502, detail="AIの応答を処理できませんでした。もう一度お試しください。")
    except Exception as exc:  # noqa: BLE001 — 本文・検索語はエラーに含めない
        raise HTTPException(status_code=502, detail=f"処理に失敗しました（{type(exc).__name__}）")


class Message(BaseModel):
    role: str
    content: str


class Card(BaseModel):
    name_en: str | None = None
    name_ja: str | None = None
    entity_type: str | None = None
    country_code: str | None = None
    focus: str | None = None


class ChatRequest(BaseModel):
    messages: list[Message]
    card: Card | None = None


@geo_knowledge_intake_router.post("/chat")
def chat(req: ChatRequest, identity=Depends(geo_ios_identity.verify_ios_identity)):
    """要件を詰める会話の1ターン。毎回、これまでの会話とカードを送る（サーバーは会話を保存しない）。"""
    return _guard(svc.chat, _client, _azure_client, _model, [m.model_dump() for m in req.messages],
                  req.card.model_dump() if req.card else None)


class StartRequest(BaseModel):
    card: Card


@geo_knowledge_intake_router.post("/jobs")
def start(req: StartRequest, identity=Depends(geo_ios_identity.verify_ios_identity)):
    """調査・登録・AI評価を、バックグラウンドで始める。戻り値の job_id で状況を問い合わせる。"""
    job_id = _guard(svc.start_job, _client, _azure_client, _model, req.card.model_dump(), identity.identity_id)
    return {"job_id": job_id}


@geo_knowledge_intake_router.get("/jobs/{job_id}")
def job_status(job_id: str, identity=Depends(geo_ios_identity.verify_ios_identity)):
    job = svc.get_job(job_id, identity.identity_id)
    if not job:
        raise HTTPException(status_code=404, detail="ジョブが見つかりません（サーバーの再起動で消えた可能性があります。一覧を確認してください）")
    return {k: job[k] for k in ("job_id", "status", "stage", "result", "error")}


@geo_knowledge_intake_router.get("/items")
def items(limit: int = 50, identity=Depends(geo_ios_identity.verify_ios_identity)):
    """ナレッジ追加で登録された項目の一覧（AI暫定承認 / 人が確認済み / 候補のまま）。"""
    return {"items": _guard(svc.list_intake_items, _client, limit=max(1, min(limit, 200)))}
