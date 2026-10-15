"""ソースの自然言語検索（意味検索）: タイトル（名称）ではなく、ソースの特徴・内容の関係性で探す。

従来の検索は、依頼文をキーワード・Tag・媒体などの条件に変換して、名称・説明・備考の部分一致で探していた。
そのため、名称に無い語（例:「半導体」）や、日本語の依頼は当たらなかった。本モジュールは、各ソースの
「特徴カード」（description・thematic_scope・geographic_scope・media_type・languages。
scripts/geo_source_profile_draft.py で作成）を一覧にしてAIへ渡し、依頼文に合うソースを、理由つきで選ばせる。

守っていること:
  - AIが返せるのは、渡した一覧にあるソースの番号だけ。実在しない番号は捨てる（ソースを作らない）。
  - 一覧に無い情報で推測しない。特徴カードが「AI生成・要確認」のものは、そのことを注意として残す。
  - 立場・信頼性の注意（偏り・低い信頼性など）が説明にあれば、検索結果に「注意」として必ず付ける。
  - 失敗（AI未設定・応答エラー）の場合は、呼び出し側が従来のキーワード検索へ切り替える。
"""
from __future__ import annotations

import geo_llm_common
import geo_source_library_service as lib

MAX_RESULTS = 20
MAX_DESC_CHARS = 260
AI_MARK = "【AI生成・要確認】"

_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["understood", "key_concepts", "matches"],
    "properties": {
        "understood": {"type": "string"},
        "key_concepts": {"type": "array", "items": {"type": "string"}},
        "matches": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["n", "score", "reason", "caution"],
            "properties": {"n": {"type": "integer"}, "score": {"type": "integer"},
                           "reason": {"type": "string"}, "caution": {"type": "string"}}}},
    },
}

_PROMPT = (
    "あなたは地政学情報ソースDBの検索アシスタントです。ユーザーの依頼に合うソースを、下の一覧（[番号]つき）から選んでください。\n"
    "【選び方】\n"
    "- ソースの名称ではなく、特徴（何を扱うか・どの地域か・どんな立場の発信か・媒体）で、依頼との関係を判断する。"
    "名称に依頼の語が無くても、特徴が合えば選ぶ。\n"
    "- 『一次情報』を求める依頼には、政府・国際機関・当事者の公式発表を優先する。『分析』『解説』なら、"
    "シンクタンク・専門メディアを優先する。『日本語』『英語』などの言語指定は、言語の項目を見る。\n"
    "- 関係が弱いものは選ばない。件数を埋めるために選ばない。最大20件、関係が強い順。\n"
    "- score: 5=依頼にぴったり、4=よく合う、3=関連はある、2=周辺的。2以下は原則として選ばない。\n"
    "- reason: なぜ合うかを1文で（一覧の特徴に書かれている内容だけを根拠にする。書かれていない事を足さない）。\n"
    "- caution: 説明に、立場の偏り・信頼性の低さ・限定的な内容などの注意が書かれていれば、短く書く。無ければ空文字。\n"
    "- understood: 依頼をどう理解したかを1文で。key_concepts: 依頼の中心概念を最大5つ。\n"
    "一覧にない番号は書かない。")


def _line(n: int, r: dict) -> str:
    desc = (r.get("description") or "").replace(AI_MARK, "").strip()[:MAX_DESC_CHARS]
    langs = ",".join(r.get("languages") or []) or "?"
    return (f"[{n}] {r['name']} | 媒体:{r.get('media_type') or '?'} | 言語:{langs} | 地域:{r.get('geographic_scope') or r.get('region') or '?'}"
            f" | 話題:{r.get('thematic_scope') or '?'} | {desc}")


def _candidates(client, extra_filters: dict | None) -> list:
    rows = client.select("geo_source_registry", {
        "select": "*", "archived_at": "is.null", "order": "name.asc", "limit": "500"})
    f = extra_filters or {}
    out = []
    for r in rows:
        if f.get("media_type") and r.get("media_type") != f["media_type"]:
            continue
        if f.get("region") and f["region"].lower() not in f"{r.get('region') or ''} {r.get('geographic_scope') or ''}".lower():
            continue
        if f.get("language") and f["language"] not in (r.get("languages") or []):
            continue
        if f.get("is_primary") is not None and bool(r.get("is_primary")) != bool(f["is_primary"]):
            continue
        out.append(r)
    return out


def semantic_search(client, azure_client, model: str, text: str, *, extra_filters: dict | None = None) -> dict:
    """戻り値: {"interpretation", "sources", "page", "page_size", "has_more", "legacy_mode"}（従来の検索と同じ形）。
    各ソースに match_score / match_reason / match_caution を付ける。失敗は例外（呼び出し側が代替する）。"""
    text = (text or "").strip()
    rows = _candidates(client, extra_filters)
    if not rows:
        return {"interpretation": {"filters": {"keywords": [], "media_type": None, "region": None, "language": None,
                                               "is_primary": None, "tags": []}, "used_llm": True, "error": None,
                                   "mode": "semantic", "understood": text},
                "sources": [], "page": 1, "page_size": MAX_RESULTS, "has_more": False, "legacy_mode": False}
    catalog = "\n".join(_line(i, r) for i, r in enumerate(rows, 1))
    res = geo_llm_common.call_llm_structured(
        azure_client, model, _PROMPT, f"# ユーザーの依頼\n{text}\n\n# ソース一覧\n{catalog}", _SCHEMA,
        "source_semantic_search", max_attempts=3)
    data = res["data"]

    picked, seen = [], set()
    for m in sorted(data.get("matches") or [], key=lambda x: -int(x.get("score") or 0)):
        n = m.get("n")
        if not isinstance(n, int) or not (1 <= n <= len(rows)) or n in seen or int(m.get("score") or 0) < 3:
            continue          # 一覧に無い番号・重複・関連の弱いものは捨てる
        seen.add(n)
        r = dict(rows[n - 1])
        r["match_score"] = int(m["score"])
        r["match_reason"] = (m.get("reason") or "").strip()
        caution = (m.get("caution") or "").strip()
        if (r.get("description") or "").startswith(AI_MARK):
            caution = (caution + " " if caution else "") + "特徴はAI生成（要確認）"
        r["match_caution"] = caution
        picked.append(r)
        if len(picked) >= MAX_RESULTS:
            break
    lib._attach_list_extras(client, picked)
    return {"interpretation": {
        "filters": {"keywords": [k for k in (data.get("key_concepts") or []) if k][:5], "media_type": None,
                    "region": None, "language": None, "is_primary": None, "tags": []},
        "used_llm": True, "error": None, "mode": "semantic", "understood": (data.get("understood") or "").strip()},
        "sources": picked, "page": 1, "page_size": MAX_RESULTS, "has_more": False, "legacy_mode": False}
