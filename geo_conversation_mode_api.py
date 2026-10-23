"""Intelligence AI: Conversation Mode v1 の API（/api/geo/conversation-mode）。

Feature Flag（環境変数 GEO_CONVERSATION_MODE_ENABLED、既定OFF）がOFFのときは、/config が enabled=false を返し、
それ以外のエンドポイントは404を返す。ルーターは常に登録してよい（OFFなら何も起きない）。

認証は既存 /chat と同じ get_optional_ios_identity（未ログインでも利用可・その場合は保存はブラウザ内のみ）。
requester_context はこのリクエストの認証済みIDからだけ作る（クライアントの自己申告は受け付けない）。
"""
import json
import queue
import threading

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ai_client import make_openai_client  # noqa: E402
from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

import geo_conversation_mode_service as svc
import geo_ios_identity
import geo_knowledge_access_policy
import geo_llm_common

_config = load_config()
_client = SupabaseClient(_config)
_azure_client, _model = make_openai_client(_config)

geo_conversation_mode_router = APIRouter(prefix="/api/geo/conversation-mode")


def _requester_context(identity) -> dict:
    return geo_knowledge_access_policy.build_requester_context(
        user_id=identity.identity_id if identity.authenticated else None,
        department=identity.department if identity.authenticated else None,
        is_authenticated=identity.authenticated)


def _error_of(exc: Exception) -> tuple:
    """例外を (HTTPステータス, 利用者に見せる文言) に変える。会話・資料の本文はエラーに含めない。"""
    if isinstance(exc, svc.Disabled):
        return 404, str(exc)
    if isinstance(exc, svc.Busy):
        return 409, str(exc)
    if isinstance(exc, svc.BudgetExceeded):
        return 429, str(exc)
    if isinstance(exc, svc.ConversationModeError):
        return 400, str(exc)
    if isinstance(exc, geo_llm_common.GeoLLMError):
        return 502, "AIの応答を処理できませんでした。もう一度お試しください。"
    return 502, f"会話を処理できませんでした（{type(exc).__name__}）"


def _precheck() -> None:
    if not svc.is_enabled():
        raise HTTPException(status_code=404, detail="会話モードは無効です")
    if not _azure_client:
        raise HTTPException(status_code=503, detail="AI分析エンジンが設定されていません")


def _guard(fn, *args, **kwargs):
    _precheck()
    try:
        return fn(*args, **kwargs)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        status, detail = _error_of(exc)
        raise HTTPException(status_code=status, detail=detail)


def _stream(fn, **kwargs):
    """発言ができた順に、1行1イベントのJSON（NDJSON）で返す。待ち時間の体感を減らすため、全員分がそろうのを待たない。
    イベント: status（進捗の文言）/ speaker・speakers（これから発言する人）/ utterance（できた発言）/
    done（最終結果。通常のAPIと同じ内容）/ error（失敗。status と detail を持つ）。"""
    _precheck()
    q: queue.Queue = queue.Queue()

    def work():
        try:
            q.put({"type": "done", "result": fn(emit=q.put, **kwargs)})
        except Exception as exc:  # noqa: BLE001
            status, detail = _error_of(exc)
            q.put({"type": "error", "status": status, "detail": detail})
        finally:
            q.put(None)

    threading.Thread(target=work, daemon=True).start()

    def lines():
        while True:
            event = q.get()
            if event is None:
                return
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@geo_conversation_mode_router.get("/config")
def get_config():
    """画面がモード選択を出すかどうかの判定。OFFのときは {enabled: false} のみ。"""
    return svc.config()


class ThemesRequest(BaseModel):
    mode: str


@geo_conversation_mode_router.post("/themes")
def themes(req: ThemesRequest, identity=Depends(geo_ios_identity.get_optional_ios_identity)):
    return _guard(svc.recommended_themes, _client, req.mode)


class StartRequest(BaseModel):
    mode: str
    theme: str
    theme_source: str = "free"        # "recommended"（おすすめから選択）| "free"（自由入力）
    carry: dict | None = None         # モード切替時: 前のStateの facts / open_questions / evidence / theme / parent_id
    with_web: bool | None = None      # 第2フラグがONのときだけ有効


@geo_conversation_mode_router.post("/start")
def start(req: StartRequest, identity=Depends(geo_ios_identity.get_optional_ios_identity)):
    return _guard(svc.start, _client, _azure_client, _model, identity=identity,
                  requester_context=_requester_context(identity), mode=req.mode, theme=req.theme,
                  theme_source=req.theme_source, carry=req.carry, with_web=req.with_web)


class TurnRequest(BaseModel):
    state: dict
    messages: list[dict]
    action: str                       # ask | continue | summarize | pause | resume
    text: str | None = None
    chip: str | None = None
    addressee: str | None = None      # 発言者の指名（speaker_id）


@geo_conversation_mode_router.post("/turn")
def turn(req: TurnRequest, identity=Depends(geo_ios_identity.get_optional_ios_identity)):
    return _guard(svc.turn, _client, _azure_client, _model, identity=identity,
                  requester_context=_requester_context(identity), state=req.state, messages=req.messages,
                  action=req.action, text=req.text, chip=req.chip, addressee=req.addressee)


@geo_conversation_mode_router.post("/start/stream")
def start_stream(req: StartRequest, identity=Depends(geo_ios_identity.get_optional_ios_identity)):
    """/start と同じ処理を、発言ができた順に返す（NDJSON）。"""
    return _stream(svc.start, client=_client, azure_client=_azure_client, model=_model, identity=identity,
                   requester_context=_requester_context(identity), mode=req.mode, theme=req.theme,
                   theme_source=req.theme_source, carry=req.carry, with_web=req.with_web)


@geo_conversation_mode_router.post("/turn/stream")
def turn_stream(req: TurnRequest, identity=Depends(geo_ios_identity.get_optional_ios_identity)):
    """/turn と同じ処理を、発言ができた順に返す（NDJSON）。"""
    return _stream(svc.turn, client=_client, azure_client=_azure_client, model=_model, identity=identity,
                   requester_context=_requester_context(identity), state=req.state, messages=req.messages,
                   action=req.action, text=req.text, chip=req.chip, addressee=req.addressee)
