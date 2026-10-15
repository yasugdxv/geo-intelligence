"""Theme Discovery「本当に探索する」拡張のAPI（ユーザー指示、2026-10-07）。

チャット（/chat）とWeb取り込み付き探索実行（/run）の2つだけを足す。Source自動提案は
既存の POST /api/geo/source-library/recommend をフロントエンドが直接呼ぶ（新規API不要）。
/runの戻り値job_idは、既存の GET /api/geo/theme-explorer/discover/{job_id}（無改修）で
そのままポーリングできる。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ai_client import make_openai_client  # noqa: E402
from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

import geo_ios_identity
import geo_theme_discovery_live_service as live
import geo_theme_discovery_service as discovery

_config = load_config()
_client = SupabaseClient(_config)
_azure_client, _model = make_openai_client(_config)

geo_theme_discovery_live_router = APIRouter(prefix="/api/geo/theme-explorer/live")


class ChatBody(BaseModel):
    history: list[dict] = []
    message: str


@geo_theme_discovery_live_router.post("/chat")
def chat(body: ChatBody):
    """探索要件をチャットですり合わせる。会話はDBに保存しない（historyは呼び出し元が保持）。"""
    try:
        return live.refine_brief(_azure_client, _model, history=body.history, message=body.message)
    except discovery.DiscoveryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class RunBody(BaseModel):
    brief: dict
    source_ids: list[str] = []
    include_web: bool = True   # 既定はWeb検索あり（ソース外も含めて探す）
    is_demo: bool = False


@geo_theme_discovery_live_router.post("/run")
def run(body: RunBody,
        identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    """必要ならWeb検索で最新情報をgeo_external_signalsへ取り込んだ上で、既存の
    テーマ探索ジョブ（discover()）を開始する。書き込みを伴うため認証必須。"""
    try:
        return live.run_live_discovery(
            _client, _azure_client, _model, brief=body.brief, source_ids=body.source_ids,
            include_web=body.include_web, is_demo=body.is_demo)
    except discovery.DiscoveryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
