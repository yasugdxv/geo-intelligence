"""Intelligence AI: プリセット調査 API。

既存の /api/geo/intelligence-ai（検索・横断分析・マルチターン）と同じ認証方式
（geo_ios_identity.get_optional_ios_identity）を使い、requester_context は認証済みIDからのみ作る
（クライアントの自己申告は受け付けない）。既存ルーターのコードは変更していない。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ai_client import make_openai_client  # noqa: E402
from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

import geo_intelligence_ai_conversation_store as store
import geo_intelligence_ai_preset_service as svc
import geo_ios_identity
import geo_llm_common
import geo_knowledge_access_policy

_config = load_config()
_client = SupabaseClient(_config)
_azure_client, _model = make_openai_client(_config)

geo_intelligence_ai_preset_router = APIRouter(prefix="/api/geo/intelligence-ai/presets")


def _requester_context(identity) -> dict:
    return geo_knowledge_access_policy.build_requester_context(
        user_id=identity.identity_id if identity.authenticated else None,
        department=identity.department if identity.authenticated else None,
        is_authenticated=identity.authenticated)


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except svc.PresetError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except geo_llm_common.GeoLLMError:
        # LLMの一時的な失敗（形式不正・タイムアウト等）。調査のやり直しで通ることが多い
        raise HTTPException(status_code=502, detail="AIの応答を処理できませんでした。もう一度お試しください。")
    except Exception as exc:  # noqa: BLE001 — 資料本文・クエリはエラーに含めない
        raise HTTPException(status_code=502, detail=f"調査に失敗しました（{type(exc).__name__}）")


@geo_intelligence_ai_preset_router.get("")
def list_presets(lang: str = "ja", identity=Depends(geo_ios_identity.get_optional_ios_identity)):
    """プリセット定義と、各データソースの現在の接続状況（実際に読み取りを試した結果）。lang=en で英語表示。"""
    return svc.list_presets(_client, lang)


class ExtractRequest(BaseModel):
    preset_id: str
    message: str
    conditions: dict | None = None
    lang: str = "ja"


@geo_intelligence_ai_preset_router.post("/extract")
def extract(req: ExtractRequest, identity=Depends(geo_ios_identity.get_optional_ios_identity)):
    """自由入力から条件を読み取る。既に入力済みの項目は再質問しない。"""
    return _guard(svc.extract_conditions, _azure_client, _model, req.preset_id, req.message, req.conditions, req.lang)


class RunRequest(BaseModel):
    preset_id: str
    conditions: dict
    lang: str = "ja"


@geo_intelligence_ai_preset_router.post("/run")
def run(req: RunRequest, identity=Depends(geo_ios_identity.get_optional_ios_identity)):
    return _guard(svc.run_preset, _client, _azure_client, _model, identity=identity,
                  requester_context=_requester_context(identity), preset_id=req.preset_id, conditions=req.conditions,
                  lang=req.lang)


class FollowupRequest(BaseModel):
    message: str
    context: dict
    history: list[dict] = []
    lang: str = "ja"


@geo_intelligence_ai_preset_router.post("/followup")
def followup(req: FollowupRequest, identity=Depends(geo_ios_identity.get_optional_ios_identity)):
    """調査後の追加質問。保持された参照IDは、このリクエストのユーザー権限で取り直す。"""
    return _guard(svc.followup, _client, _azure_client, _model, identity=identity,
                  requester_context=_requester_context(identity), message=req.message, context=req.context,
                  history=req.history, lang=req.lang)


# ---- 会話のサーバー保存（ログイン必須。本人の会話だけ） ----
geo_intelligence_ai_conversation_router = APIRouter(prefix="/api/geo/intelligence-ai/conversations")


def _conv(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except store.NotAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except store.ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except store.ConversationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 — 会話の中身はエラーに含めない
        raise HTTPException(status_code=500, detail=f"会話を処理できませんでした（{type(exc).__name__}）")


class ConversationBody(BaseModel):
    title: str = ""
    messages: list[dict]
    preset: dict | None = None
    updatedAt: str | None = None


@geo_intelligence_ai_conversation_router.get("")
def list_conversations(identity=Depends(geo_ios_identity.verify_ios_identity)):
    return {"items": _conv(store.list_conversations, _client, identity.identity_id)}


@geo_intelligence_ai_conversation_router.put("/{conversation_id}")
def put_conversation(conversation_id: str, body: ConversationBody, identity=Depends(geo_ios_identity.verify_ios_identity)):
    return _conv(store.put_conversation, _client, identity.identity_id, conversation_id, title=body.title,
                 messages=body.messages, preset=body.preset, updated_at=body.updatedAt)


@geo_intelligence_ai_conversation_router.delete("/{conversation_id}")
def delete_conversation(conversation_id: str, identity=Depends(geo_ios_identity.verify_ios_identity)):
    _conv(store.delete_conversation, _client, identity.identity_id, conversation_id)
    return {"ok": True}
