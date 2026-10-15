"""Source自然言語検索: 自然言語 → 構造化フィルタ（keywords/media_type/region/language/is_primary/tag）。

「中国半導体政策の一次情報」「イラン国内政治に強い英語ソース」のような問いを、Source
Libraryの既存filterへ変換する。変換結果をUIへ返してchipとして見せる（ブラックボックスに
しない）。LLMが使えない/失敗した場合は、キーワード検索へフォールバックする（検索自体は止めない）。
"""
from __future__ import annotations

import geo_llm_common
import geo_source_library_service as lib
import geo_source_semantic_search as semantic

_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["keywords", "media_type", "region", "language", "is_primary", "tags"],
    "properties": {
        "keywords": {"type": "array", "items": {"type": "string"}},
        "media_type": {"type": "string", "enum": ["", *lib.MEDIA_TYPES]},
        "region": {"type": "string"},
        "language": {"type": "string"},
        "is_primary": {"type": "string", "enum": ["", "true", "false"]},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
}
_PROMPT = (
    "あなたは地政学情報ソースDBの検索アシスタントです。ユーザーの自然言語の依頼を、DB検索条件へ"
    "変換してください。keywords: 検索に使う主要語（最大4、日本語は原文、固有名詞は英語表記も可）。"
    "media_type: 明確に分かる場合のみ（シンクタンク=think_tank、政府=government、通信社=news_agency等）。"
    "region: 明確な場合のみ。language: ISO言語コード(ja/en/zh等)、明確な場合のみ。"
    "is_primary: 「一次情報」を求める場合のみtrue。tags: 主題Tag候補（最大4）。"
    "不明な項目は空文字/空配列にする。推測で条件を足さない。")


def _fallback(text: str) -> dict:
    return {"keywords": [w for w in text.replace("　", " ").split() if w][:4], "media_type": None,
            "region": None, "language": None, "is_primary": None, "tags": []}


def interpret_query(azure_client, model: str, text: str) -> dict:
    """戻り値: {"filters": {...}, "used_llm": bool, "error": str|None}"""
    text = (text or "").strip()
    if not text:
        return {"filters": _fallback(""), "used_llm": False, "error": None}
    if azure_client is None:
        return {"filters": _fallback(text), "used_llm": False, "error": "AI分析エンジン未設定のためキーワード検索にしました"}
    try:
        result = geo_llm_common.call_llm_structured(
            azure_client, model, _PROMPT, text, _SCHEMA, "source_search_interpretation", max_attempts=2)
    except geo_llm_common.GeoLLMError as exc:
        return {"filters": _fallback(text), "used_llm": False,
                "error": f"検索意図の解釈に失敗したためキーワード検索にしました: {exc}"}
    d = result["data"]
    return {"filters": {
        "keywords": [k.strip() for k in d.get("keywords", []) if k.strip()][:4],
        "media_type": d.get("media_type") or None, "region": d.get("region") or None,
        "language": d.get("language") or None,
        "is_primary": {"true": True, "false": False}.get(d.get("is_primary")),
        "tags": [t.strip().lstrip("#") for t in d.get("tags", []) if t.strip()][:4]},
        "used_llm": True, "error": None}


def search_sources(client, azure_client, model: str, text: str, *, page: int = 1, page_size: int = 25,
                   extra_filters: dict | None = None) -> dict:
    """自然言語検索の実行。まず意味検索（ソースの特徴カードの内容で探し、理由つきで返す）を試し、
    AIが使えない・失敗したときは、従来のキーワード検索へ切り替える（検索自体は止めない）。"""
    text = (text or "").strip()
    semantic_error = None
    if text and azure_client is not None:
        if page > 1:      # 意味検索は上位20件を1ページで返す
            return {"interpretation": {"filters": {"keywords": [], "media_type": None, "region": None, "language": None,
                                                   "is_primary": None, "tags": []}, "used_llm": True, "error": None,
                                       "mode": "semantic", "understood": text},
                    "sources": [], "page": page, "page_size": page_size, "has_more": False, "legacy_mode": False}
        try:
            return semantic.semantic_search(client, azure_client, model, text, extra_filters=extra_filters)
        except Exception as exc:  # noqa: BLE001 — 意味検索の失敗で検索を止めない
            semantic_error = f"意味検索に失敗したため、キーワード検索にしました: {str(exc)[:80]}"
    result = _keyword_search_sources(client, azure_client, model, text, page=page, page_size=page_size,
                                     extra_filters=extra_filters)
    if semantic_error:      # 切り替えた理由を、画面に出す（キーワード検索側の注記より優先する）
        result["interpretation"]["error"] = semantic_error
    return result


def _keyword_search_sources(client, azure_client, model: str, text: str, *, page: int = 1, page_size: int = 25,
                            extra_filters: dict | None = None) -> dict:
    """従来のキーワード検索。keywordごと/tagごとに検索して結果をunionし、条件にmatchした数で順位付けする
    （全件をFrontへ返さず、1ページ分のみ）。"""
    interp = interpret_query(azure_client, model, text)
    f = interp["filters"]
    base = dict(extra_filters or {})
    for key in ("media_type", "region", "language", "is_primary"):
        if f.get(key) is not None and base.get(key) is None:
            base[key] = f[key]

    hits: dict = {}
    terms = [("q", k) for k in f["keywords"]] + [("tag", t) for t in f["tags"]]
    if not terms:
        return {"interpretation": interp, **lib.list_sources(client, page=page, page_size=page_size, **base)}
    for kind, term in terms:
        res = lib.list_sources(client, page=1, page_size=100, **{**base, kind: term})
        for row in res["sources"]:
            entry = hits.setdefault(row["source_id"], {"row": row, "score": 0})
            entry["score"] += 1
    ranked = sorted(hits.values(), key=lambda e: (-e["score"], e["row"]["name"]))
    start = (max(1, page) - 1) * page_size
    rows = [e["row"] for e in ranked[start:start + page_size]]
    return {"interpretation": interp, "sources": rows, "page": page, "page_size": page_size,
            "has_more": len(ranked) > start + page_size, "legacy_mode": False}
