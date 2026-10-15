"""Source ResearchのQuery Expansion Layer（日本語Query → 英語記事のRecall改善）。

新しいResearch Engineは作らず、既存の内部記事検索（geo_source_research_service）の前段へ
「検索語の展開」だけを足す。最終回答の問い（ユーザーの日本語Query）は変更しない。

検索語は出自を区別して保持する:
  original  : ユーザーQuery原文から取った語
  translated: 英訳Queryから取った語
  expanded  : 重要概念・同義語・正式名称・略語（commonly used English expression）

★ Guard（ユーザーの問いを勝手に広げない）:
  許可: 翻訳 / 同義語 / 正式名称 / acronym / 英語での一般的な言い方
  禁止: 新しい研究テーマの追加 / 地域の変更 / Actorの勝手な追加 / 時間範囲の変更
  実装: (1) LLMに「追加したもの」を自己申告させ、申告があれば展開語を破棄
        (2) 展開語に、Queryに無い年・数字があれば破棄（時間範囲の変更）
        (3) 展開語に、Query（原文＋英訳）に無い国・地域名があれば破棄（地域の変更）
        (4) 件数・長さの上限
  破棄した語とその理由はguard.droppedに残す（監査可能）。
LLMが使えない/失敗した場合は原文の語だけで検索する（Researchを止めない）。
"""
from __future__ import annotations

import re

import geo_llm_common

# SOURCE_INTELLIGENCE_V1 Freezeで固定したバージョン（挙動を変えたら必ず上げる）
QUERY_EXPANSION_VERSION = "query_expansion_v1"

MAX_EXPANDED_TERMS = 8
MAX_TERM_LENGTH = 60

_CJK = re.compile(r"[぀-ヿ一-鿿]")
_PARTICLES = re.compile(r"[のはをにがでとやかもへ、。,，\s・「」（）()]+")
_ASCII_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]{2,}")
_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "into", "over", "about", "around", "near", "between",
    "are", "was", "were", "has", "have", "had", "its", "their", "than", "then", "not", "any", "all", "how",
    "what", "why", "who", "whom", "which", "does", "did", "will", "would", "could", "can", "may",
}
# 地域・国名（展開語に、Queryに無い地域が現れたら破棄するための簡易リスト。網羅ではなくGuardの一部）
_REGION_WORDS = {
    "china", "taiwan", "japan", "korea", "russia", "ukraine", "iran", "israel", "gaza", "palestine", "india",
    "pakistan", "philippines", "vietnam", "indonesia", "singapore", "thailand", "malaysia", "australia",
    "canada", "mexico", "brazil", "germany", "france", "britain", "england", "italy", "turkey", "egypt",
    "saudi", "yemen", "syria", "iraq", "lebanon", "afghanistan", "myanmar", "cuba", "venezuela", "africa",
    "europe", "asia", "america", "usa", "hormuz", "crimea", "tibet", "xinjiang", "hongkong", "macau",
}

_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["translated_query", "key_concepts", "synonyms_and_names", "added_topics", "added_regions",
                 "added_actors", "changed_time_range"],
    "properties": {
        "translated_query": {"type": "string"},
        "key_concepts": {"type": "array", "items": {"type": "string"}},
        "synonyms_and_names": {"type": "array", "items": {"type": "string"}},
        "added_topics": {"type": "array", "items": {"type": "string"}},
        "added_regions": {"type": "array", "items": {"type": "string"}},
        "added_actors": {"type": "array", "items": {"type": "string"}},
        "changed_time_range": {"type": "boolean"},
    },
}
_PROMPT = (
    "あなたは地政学ニュース検索の翻訳・検索語展開アシスタントです。ユーザーの日本語の問いを、英語記事の"
    "検索に使える形へ変換してください。\n"
    "translated_query: 問いの忠実な英訳（1文）。\n"
    "key_concepts: 問いの中心概念の英語表現（2〜4語程度のフレーズ、最大4個）。\n"
    "synonyms_and_names: 同義語・正式名称・略語（acronym）・英語圏で一般的な言い方のみ（最大4個）。\n"
    "【禁止】問いに無い新しい研究テーマ・地域・Actor（人物/組織/国）の追加、時間範囲の変更。"
    "許可されるのは翻訳・同義語・正式名称・略語・一般的な英語表現だけです。\n"
    "もし上記の禁止事項に当たる語を追加してしまった場合は、added_topics/added_regions/added_actors/"
    "changed_time_rangeに正直に申告してください（申告された語は破棄されます）。")


def has_cjk(text: str) -> bool:
    return bool(_CJK.search(text or ""))


def original_terms(query: str) -> list:
    """原文から検索語を取る。ASCII語（3文字以上）と、助詞で区切った日本語の語（2文字以上）。"""
    terms = [w for w in _ASCII_WORD.findall(query or "") if w.lower() not in _STOPWORDS]
    for seg in _PARTICLES.split(query or ""):
        seg = _ASCII_WORD.sub("", seg).strip()
        if len(seg) >= 2 and has_cjk(seg):
            terms.append(seg)
    return _dedupe(terms)[:8]


def content_words(text: str) -> list:
    """英訳Queryからの内容語（ストップワードを除く、3文字以上）。"""
    return _dedupe([w.lower() for w in _ASCII_WORD.findall(text or "") if w.lower() not in _STOPWORDS])[:8]


def _dedupe(items: list) -> list:
    seen, out = set(), []
    for x in items:
        k = x.lower()
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def _numbers(text: str) -> set:
    return set(re.findall(r"\d+", text or ""))


def _regions(text: str) -> set:
    words = set(re.findall(r"[a-z]+", (text or "").lower()))
    return {w for w in words if w in _REGION_WORDS}


def apply_guards(query: str, translated: str, terms: list, llm_flags: dict) -> tuple:
    """展開語にGuardを適用する。戻り値: (採用した語, 破棄 [{term, reason}], 翻訳を採用するか)"""
    dropped, kept = [], []
    flagged = {k: llm_flags.get(k) for k in ("added_topics", "added_regions", "added_actors") if llm_flags.get(k)}
    if llm_flags.get("changed_time_range"):
        flagged["changed_time_range"] = True
    if flagged:
        dropped.extend({"term": t, "reason": "LLMが問いにない要素の追加を自己申告したため破棄"} for t in terms)
        return [], dropped, True      # 翻訳自体は下で数字/地域のGuardを通す
    allowed_numbers = _numbers(query) | _numbers(translated)
    allowed_regions = _regions(query) | _regions(translated)
    for t in terms:
        t = (t or "").strip().strip('"')
        if not t:
            continue
        if len(t) > MAX_TERM_LENGTH or len(t.split()) > 6:
            dropped.append({"term": t[:80], "reason": "長すぎる（検索語ではなく文のため）"})
        elif not _numbers(t) <= allowed_numbers:
            dropped.append({"term": t, "reason": "問いにない年・数字を含む（時間範囲の変更の恐れ）"})
        elif not _regions(t) <= allowed_regions:
            dropped.append({"term": t, "reason": "問いにない国・地域名を含む（地域の変更の恐れ）"})
        else:
            kept.append(t)
    return _dedupe(kept)[:MAX_EXPANDED_TERMS], dropped, True


def expand_query(azure_client, model: str | None, query: str) -> dict:
    """戻り値: {original, translated, translated_terms, key_concepts, expanded_terms, search_terms,
    used_llm, error, guard{dropped}}。search_termsは [{term, kind}]（kind: original/translated/expanded）。"""
    query = (query or "").strip()
    result = {"version": QUERY_EXPANSION_VERSION, "original": query, "translated": None, "key_concepts": [],
              "expanded_terms": [], "used_llm": False, "error": None, "guard": {"dropped": []}}
    search_terms = [{"term": t, "kind": "original"} for t in original_terms(query)]
    # 日本語（CJK）を含まないQueryは翻訳不要。LLMは使わない
    if not has_cjk(query):
        result["search_terms"] = search_terms
        return result
    if azure_client is None:
        result["error"] = "AI未設定のため英語展開は行わず、原文の語だけで検索します"
        result["search_terms"] = search_terms
        return result
    try:
        res = geo_llm_common.call_llm_structured(
            azure_client, model, _PROMPT, query, _SCHEMA, "source_query_expansion", max_attempts=2)
    except geo_llm_common.GeoLLMError as exc:
        result["error"] = f"英語展開に失敗したため原文の語だけで検索します: {str(exc)[:80]}"
        result["search_terms"] = search_terms
        return result
    d = res["data"]
    translated = (d.get("translated_query") or "").strip()[:200]
    concept_and_synonyms = [*d.get("key_concepts", []), *d.get("synonyms_and_names", [])]
    expanded, dropped, _ = apply_guards(query, translated, concept_and_synonyms, d)
    # 翻訳自体にも数字・地域のGuard（Queryに無い年や地域が英訳に混入していないか）
    tr_dropped = False
    if translated:
        if not _numbers(translated) <= _numbers(query):
            dropped.append({"term": translated, "reason": "英訳に問いにない年・数字が含まれるため翻訳を破棄"})
            tr_dropped = True
        # 日本語Queryは地域名が日本語なので、英訳の地域名は検証不能 → 展開語側(上記)のみで担保する
    result.update({"used_llm": True, "translated": None if tr_dropped else (translated or None),
                   "expanded_terms": expanded, "key_concepts": [t for t in d.get("key_concepts", []) if t in expanded],
                   "guard": {"dropped": dropped}})
    if result["translated"]:
        search_terms += [{"term": w, "kind": "translated"} for w in content_words(result["translated"])]
    search_terms += [{"term": t, "kind": "expanded"} for t in expanded]
    result["search_terms"] = _dedupe_terms(search_terms)
    return result


def _dedupe_terms(items: list) -> list:
    seen, out = set(), []
    for it in items:
        k = it["term"].lower()
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out
