"""Intelligence AI: 会話のサーバー保存（geo_intelligence_ai_conversations）。

目的: ブラウザ内（localStorage）だけだった会話履歴を、ログインしている人に限りサーバーにも保存し、別のパソコンでも
続きを見られるようにする。

守っていること:
  - 本人（owner_id = 認証済みの identity_id）の会話だけを、読む・書く・消す。他人の会話は存在も分からない（404）。
    管理者でも他人の会話は読めない（調査結果の本文を含みうるため）。
  - 未ログイン（匿名）では使えない。匿名の会話は、従来どおりブラウザ内のみ。
  - 保存するのは、画面が持っている会話そのもの（メッセージ・調査条件・参照ID）。サーバー側で内容を解釈・加工しない。
  - 1人あたり最新50件まで。1件あたりのサイズ上限あり。
  - テーブル未作成（SQL未適用）のときは、呼び出し側に NotAvailable を返す（画面はブラウザ内保存だけで動く）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

MAX_CONVERSATIONS = 50
MAX_BYTES = 1_500_000
MAX_MESSAGES = 300
_ID = re.compile(r"^[A-Za-z0-9_\-]{6,80}$")


class ConversationError(Exception):
    """入力不正（400）。"""


class ConversationNotFound(ConversationError):
    """対象なし／他人の会話（404）。"""


class NotAvailable(ConversationError):
    """テーブル未作成（503）。"""


def _missing_table(exc: Exception) -> bool:
    text = str(exc)
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            text += " " + (resp.text or "")
        except Exception:  # noqa: BLE001
            pass
    return any(m in text for m in ("PGRST205", "42P01", "does not exist", "Could not find the table"))


def _guard(fn):
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except ConversationError:
            raise
        except Exception as exc:  # noqa: BLE001
            if _missing_table(exc):
                raise NotAvailable("会話のサーバー保存用テーブルが未作成です（sql/2026-10-14_geo_ia_extensions.sql を適用してください）") from exc
            raise
    return wrapper


def _require_owner(owner_id) -> str:
    if not owner_id or not isinstance(owner_id, str):
        raise ConversationError("ログインが必要です")
    return owner_id


def _present(r: dict) -> dict:
    return {"id": r["conversation_id"], "title": r.get("title") or "", "messages": r.get("messages") or [],
            "preset": r.get("preset"), "updatedAt": r.get("updated_at")}


@_guard
def list_conversations(client, owner_id) -> list:
    owner = _require_owner(owner_id)
    rows = client.select("geo_intelligence_ai_conversations", {
        "select": "*", "owner_id": f"eq.{owner}", "order": "updated_at.desc", "limit": str(MAX_CONVERSATIONS)})
    return [_present(r) for r in rows]


@_guard
def put_conversation(client, owner_id, conversation_id: str, *, title: str, messages: list, preset, updated_at: str | None = None) -> dict:
    owner = _require_owner(owner_id)
    if not _ID.match(conversation_id or ""):
        raise ConversationError("会話IDの形式が正しくありません")
    if not isinstance(messages, list) or len(messages) > MAX_MESSAGES:
        raise ConversationError("メッセージの形式または件数が正しくありません")
    if preset is not None and not isinstance(preset, dict):
        raise ConversationError("プリセット情報の形式が正しくありません")
    if len(json.dumps([messages, preset], ensure_ascii=False)) > MAX_BYTES:
        raise ConversationError("会話が大きすぎて保存できません")
    now = datetime.now(timezone.utc).isoformat()
    existing = client.select("geo_intelligence_ai_conversations", {"select": "conversation_id,owner_id", "conversation_id": f"eq.{conversation_id}", "limit": "1"})
    row = {"title": (title or "")[:200], "messages": messages, "preset": preset, "updated_at": updated_at or now}
    if existing:
        if existing[0]["owner_id"] != owner:
            raise ConversationNotFound("会話が見つかりません")        # 他人の会話は、存在も分からない
        client.update("geo_intelligence_ai_conversations", {"conversation_id": f"eq.{conversation_id}", "owner_id": f"eq.{owner}"}, row)
    else:
        client.insert("geo_intelligence_ai_conversations", [{"conversation_id": conversation_id, "owner_id": owner, **row}])
        _trim_old(client, owner)
    return {"id": conversation_id, "updatedAt": row["updated_at"]}


def _trim_old(client, owner: str) -> None:
    """1人あたり最新 MAX_CONVERSATIONS 件までにする（古い会話から削除）。"""
    rows = client.select("geo_intelligence_ai_conversations", {
        "select": "conversation_id", "owner_id": f"eq.{owner}", "order": "updated_at.desc", "limit": "1000"})
    for r in rows[MAX_CONVERSATIONS:]:
        client.delete("geo_intelligence_ai_conversations", {"conversation_id": f"eq.{r['conversation_id']}", "owner_id": f"eq.{owner}"})


@_guard
def delete_conversation(client, owner_id, conversation_id: str) -> None:
    owner = _require_owner(owner_id)
    rows = client.select("geo_intelligence_ai_conversations", {"select": "conversation_id", "conversation_id": f"eq.{conversation_id}",
                                                               "owner_id": f"eq.{owner}", "limit": "1"})
    if not rows:
        raise ConversationNotFound("会話が見つかりません")
    client.delete("geo_intelligence_ai_conversations", {"conversation_id": f"eq.{conversation_id}", "owner_id": f"eq.{owner}"})
