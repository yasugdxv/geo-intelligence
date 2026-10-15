"""ナレッジ検索の自然言語検索（エンティティ・ナレッジ項目・レポートを横断）。

従来のHybrid検索（FTS＋完全一致＋部分一致）は、語の一致で探すため、自然な文の依頼
（例:「トランプ政権の関税への姿勢がわかる資料」）や、言い換え・日本語⇔英語の差に弱かった。
本モジュールは、エンティティ・ナレッジ項目・レポートの要約を1つの一覧にしてAIへ渡し、依頼に合うものを
理由つきで選ばせる。

守っていること:
  - ナレッジ項目は、通常検索と同じアクセス制御を必ず通す（承認済み・参照可能・human_visibility）。
    AIには、アクセスできるものだけを渡す。AIが勝手に増やすことはできない。
  - AIが返せるのは、一覧にある参照（E1/K3/R2など）だけ。存在しない参照は捨てる。
  - 失敗（AI未設定・応答エラー）の場合は、呼び出し側が従来のHybrid検索に切り替える。
  - DBへは何も書かない（読み取りのみ）。
"""
from __future__ import annotations

import geo_knowledge_access_policy as access
import geo_llm_common

MAX_PER_KIND = 12
MAX_ENTITIES = 150
MAX_ITEMS = 200
MAX_REPORTS = 100
SNIPPET = 220

_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["understood", "matches"],
    "properties": {
        "understood": {"type": "string"},
        "matches": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["ref", "score", "reason"],
            "properties": {"ref": {"type": "string"}, "score": {"type": "integer"}, "reason": {"type": "string"}}}},
    },
}
_PROMPT = (
    "あなたは地政学ナレッジDBの検索アシスタントです。ユーザーの依頼に合うものを、下の一覧から選んでください。"
    "一覧には3種類あります: E=エンティティ（国・組織・人物）、K=ナレッジ項目（アクターの分析・先例・ルール等）、"
    "R=レポート。\n"
    "【選び方】\n"
    "- 名称の一致だけでなく、内容の関係（その資料が依頼の疑問に答えるか）で判断する。言い換え・日本語と英語の差も踏まえる。\n"
    "- 関係が弱いものは選ばない。件数を埋めるために選ばない。種類ごとに最大12件、関係が強い順。\n"
    "- score: 5=依頼にぴったり、4=よく合う、3=関連はある。2以下は選ばない。\n"
    "- reason: なぜ合うかを1文で。一覧に書かれている内容だけを根拠にし、書かれていない事を足さない。\n"
    "- understood: 依頼をどう理解したかを1文で。\n"
    "一覧にない参照は書かない。")


def _clip(text, n=SNIPPET) -> str:
    return " ".join(str(text or "").split())[:n]


def _flatten(value, limit: int = SNIPPET) -> str:
    parts: list = []

    def walk(v):
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
    walk(value)
    return _clip(" ".join(parts), limit)


def _load(client, *, status: list | None, knowledge_types: list | None, include_demo: bool,
          requester_context: dict | None) -> dict:
    demo = {} if include_demo else {"is_demo": "eq.false"}
    entities = client.select("geo_entities", {"select": "*", "order": "canonical_name.asc",
                                              "limit": str(MAX_ENTITIES), **demo})
    items = client.select("geo_knowledge_items", {"select": "*", "order": "updated_at.desc",
                                                  "limit": str(MAX_ITEMS), **demo})
    if status:
        items = [i for i in items if i.get("status") in status]
    if knowledge_types:
        items = [i for i in items if i.get("knowledge_type") in knowledge_types]
    # 通常検索と同じ: 参照可能（承認済み・available）かつ、閲覧権限のあるものだけ
    items = [i for i in items if access.is_retrievable(i)
             and access.can_human_access(i, requester_context=requester_context)]
    try:
        reports = client.select("geo_reports", {"select": "*", "order": "uploaded_at.desc",
                                                "limit": str(MAX_REPORTS), **demo})
    except Exception:  # noqa: BLE001 — レポートが読めなくても、他の2種類は探せる
        reports = []
    titles: dict = {}
    doc_ids = [r["document_id"] for r in reports if r.get("document_id")]
    if doc_ids:
        try:
            for d in client.select("geo_documents", {
                    "select": "document_id,title", "document_id": "in.(" + ",".join(doc_ids) + ")"}):
                titles[d["document_id"]] = d.get("title")
        except Exception:  # noqa: BLE001
            pass
    for r in reports:
        r["document_title"] = titles.get(r.get("document_id")) or r.get("document_title")
    return {"entities": entities, "items": items, "reports": reports}


def _catalog(data: dict) -> str:
    lines = []
    for i, e in enumerate(data["entities"], 1):
        names = " / ".join(x for x in (e.get("canonical_name"), e.get("name_ja"), e.get("name_en")) if x)
        lines.append(f"[E{i}] {names} | 種別:{e.get('entity_type')} | 国:{e.get('country_code') or '?'}")
    for i, k in enumerate(data["items"], 1):
        body = k.get("summary") or _flatten(k.get("structured_content"))
        lines.append(f"[K{i}] {k.get('title')} | 種類:{k.get('knowledge_type')} | {_clip(body)}")
    for i, r in enumerate(data["reports"], 1):
        meta = f"話題:{', '.join(r.get('topics') or [])} | 地域:{', '.join(r.get('country_region') or [])} | 主体:{', '.join(r.get('key_actors') or [])}"
        lines.append(f"[R{i}] {r.get('document_title') or '(無題)'} | {meta} | {_clip(r.get('summary'))}")
    return "\n".join(lines)


def natural_search(client, azure_client, model: str, text: str, *, status: list | None = None,
                   knowledge_types: list | None = None, include_demo: bool = False,
                   requester_context: dict | None = None) -> dict:
    """戻り値: {"understood", "entities": [{entity, score, reason}], "knowledge_items": [...], "reports": [...]}。
    失敗は例外（呼び出し側が従来検索へ切り替える）。"""
    text = (text or "").strip()
    data = _load(client, status=status, knowledge_types=knowledge_types, include_demo=include_demo,
                 requester_context=requester_context)
    empty = {"understood": text, "entities": [], "knowledge_items": [], "reports": []}
    if not (data["entities"] or data["items"] or data["reports"]):
        return empty
    res = geo_llm_common.call_llm_structured(
        azure_client, model, _PROMPT, f"# ユーザーの依頼\n{text}\n\n# 一覧\n{_catalog(data)}", _SCHEMA,
        "knowledge_natural_search", max_attempts=3)
    out = {"understood": (res["data"].get("understood") or "").strip(), "entities": [], "knowledge_items": [],
           "reports": []}
    source = {"E": (data["entities"], "entities", "entity"), "K": (data["items"], "knowledge_items", "knowledge_item"),
              "R": (data["reports"], "reports", "report")}
    seen: set = set()
    for m in sorted(res["data"].get("matches") or [], key=lambda x: -int(x.get("score") or 0)):
        ref = (m.get("ref") or "").strip().upper()
        if len(ref) < 2 or ref[0] not in source or not ref[1:].isdigit() or ref in seen or int(m.get("score") or 0) < 3:
            continue
        rows, key, field = source[ref[0]]
        n = int(ref[1:])
        if not 1 <= n <= len(rows) or len(out[key]) >= MAX_PER_KIND:
            continue                      # 一覧に無い参照・多すぎるものは捨てる
        seen.add(ref)
        out[key].append({field: rows[n - 1], "score": int(m["score"]), "reason": (m.get("reason") or "").strip()})
    return out
