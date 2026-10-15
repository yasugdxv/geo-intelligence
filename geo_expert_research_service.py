"""Expert AI Phase R1 Chunk4: Research/Investigation OS。

専門家の調査プロセス（問いを理解する → 初期仮説を置く → 何を調べる必要があるか決める →
Current Evidenceを取得する → Evidenceの直接性と信頼性を評価する → Key Point候補を
比較する → 十分な材料が揃った後に既存Thinking OSへ渡す）のうち、Stage1呼び出し前の
決定的な部分（Research Sufficiency Gate）と、External Research（Web検索）を担当する。

設計方針（承認済みPlan・ユーザー指示に基づく）:
- Internal DB Retrieval（既存build_source_pack、DB専用）→ Research Sufficiency Gate
  （本モジュール、決定的・LLM不要）→ 不足している場合のみExternal Research、という
  順序を守る。Web Searchを先に行わない。
- External Researchは既存article_analyzer.pyの`responses.create(tools=[
  {"type":"web_search_preview"}], tool_choice="required")` + url_citationのみ採用
  パターンを「Transport」としてのみ流用する。「何を調べるか」はResearch OS側
  （本モジュール）が決める。
- Evidence Qualificationで実際に採用したものだけを既存geo_documentsへ永続化する
  （検索結果を無差別に全件保存しない）。source_type（誰が発信したか）は既存
  geo_source_registry.source_typeをget-or-createで流用する（新規カラム不要）。
  evidence_class/temporal_statusはgeo_documentsの新規カラム
  （sql/2026-09-03_geo_documents_research_qualification.sql）に持たせる。
  claim_relevance（このClaimへの直接性）はここでは付与しない——Stage1が実際の
  Claimと紐付けて判断する（既存のevidence_relevance機構、Phase R1 Chunk2）。
- source_type/evidence_class/claim_relevance/temporal_statusの4軸を混同しない。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Phase R1-A: Research Sufficiency Gate（決定的、Stage1呼び出し前、追加Azure Callなし）
# ---------------------------------------------------------------------------

# 検証用の目安（絶対基準にはしない）: Direct Evidenceが2件以上あればsufficient候補、
# 1件ならpartial、0件ならinsufficient。build_source_packは既にkeyword-matchedの
# document/evidenceのみを返す設計のため、件数自体が粗いが妥当なproxyになる。
_SUFFICIENT_THRESHOLD = 2

# Phase R1 Chunk6後P0-1: Named Entity Coverage（件数だけでなく、クエリが名指ししている
# 固有名詞そのものについてEvidenceがあるかを見る）。Direct Evidenceの件数閾値を満たしても、
# クエリが具体的に名指しした主体（例: TSMC）についてEvidence本文中に一切言及が無い場合
# ——他の隣接キーワード（例: 半導体・輸出管理）だけで件数が積み上がっている場合——は
# sufficientとみなさない。追加Azure Callは発生させない（Foundation Pack由来の厳密な
# Actor/Institution/Action/Decisionの6セル判定はLLM呼び出しが必要になるため今回は
# スコープ外とし、決定的に実装可能な「名指しされた固有名詞の本文中言及有無」を
# Coverageの代理指標として使う）。
_NAMED_ENTITY_TERM_PATTERN = re.compile(r"[A-Za-z]{3,}")

# Phase R1 Human Check実データで発見: ユーザーが依頼文の中でExpert AI自身の出力語彙
# （Key Point/Main Scenario/Observable Indicators/Frame-breaking等の英語見出し）を
# そのまま使って質問を構成すると、これらの一般語まで「固有名詞」として抽出され、
# Evidence本文に存在しない（当然、地名・組織名ではないため）としてsufficientを
# 不必要にpartialへ引き下げてしまっていた。Expert AIプロンプト自体の語彙をstopwordとして
# 除外する（既存の_GENERIC_KANJI_KEYWORD_STOPWORDSと同じ考え方）。
_NAMED_ENTITY_TERM_STOPWORDS = {
    "key", "point", "points", "actor", "actors", "institution", "institutional",
    "structural", "structure", "evidence", "primary", "main", "scenario",
    "alternative", "observable", "indicator", "indicators", "leverage", "gap",
    "gaps", "frame", "breaking", "narrative", "audience", "research", "constraint",
    "constraints", "red", "line", "lines", "analysis", "distributed", "mixed",
    "unknown", "current", "hypothesis", "hypotheses",
}


def _query_named_entity_terms(query_text: str) -> list:
    """クエリ中のLatin 3文字以上の連続（TSMC/MATCH/NATO/EU等、固有名詞である可能性が
    高い語）を重複無く抽出する。日本語の一般的な話題語（半導体・輸出管理等）は対象外
    ——これらはCoverage判定ではなく既存のDirect Evidence件数判定でカバーする。
    Expert AI自身の出力語彙（Key Point等）もstopwordとして除外する。"""
    seen = []
    for m in _NAMED_ENTITY_TERM_PATTERN.finditer(query_text or ""):
        term = m.group(0)
        if term.lower() in _NAMED_ENTITY_TERM_STOPWORDS:
            continue
        if term not in seen:
            seen.append(term)
    return seen


def assess_research_sufficiency(source_pack: dict, institution_intelligence_pack: dict | None = None, *,
                                 query_text: str = "") -> dict:
    """Stage1呼び出し前の決定的な粗判定。current_evidence（DB上のCurrent Evidence量）と
    institutional_coverage（Institution Intelligenceの充足度）を別々に評価し、overallは
    current_evidence優先で決める（External Researchのトリガー判定に使うのはcurrent_
    evidenceのみ——Actor/Institution Coverageの不足は既存のgeneral_knowledge_used機構で
    別途安全に扱われるため、本Gateの主目的ではない）。

    query_text指定時は、クエリが名指しした固有名詞（Named Entity Coverage）についても
    Evidence本文中の言及有無を確認し、件数閾値を満たしていても未言及の固有名詞があれば
    sufficientへ昇格させない（Direct Evidence「件数」とQuestionに答えるための「材料」が
    一致しないケースへの対応）。"""
    document_ids = set(source_pack.get("document_ids") or [])
    evidence_ids = set(source_pack.get("evidence_ids") or [])
    direct_count = len(document_ids | evidence_ids)

    if direct_count >= _SUFFICIENT_THRESHOLD:
        current_evidence = "sufficient"
    elif direct_count == 1:
        current_evidence = "partial"
    else:
        current_evidence = "insufficient"

    count_based_current_evidence = current_evidence
    source_text_lower = (source_pack.get("text") or "").lower()
    named_entity_coverage_gaps = [
        term for term in _query_named_entity_terms(query_text)
        if term.lower() not in source_text_lower
    ]
    if named_entity_coverage_gaps and current_evidence == "sufficient":
        current_evidence = "partial"

    institutional_coverage = "not_evaluated"
    if institution_intelligence_pack is not None:
        institutions = institution_intelligence_pack.get("institutions") or []
        if not institutions:
            institutional_coverage = "not_evaluated"
        elif any(i.get("profile_source") == "institution_intelligence_active" for i in institutions):
            institutional_coverage = "sufficient"
        elif any(i.get("profile_source") == "institution_intelligence_candidate" for i in institutions):
            institutional_coverage = "partial"
        else:
            institutional_coverage = "insufficient"

    gaps = []
    if count_based_current_evidence != "sufficient":
        gaps.append(
            f"current_evidence={count_based_current_evidence}（Direct Evidence {direct_count}件、"
            f"目安{_SUFFICIENT_THRESHOLD}件以上でsufficient候補）")
    if named_entity_coverage_gaps:
        gaps.append(
            "named_entity_coverage_gap: クエリが名指しした固有名詞 "
            f"{named_entity_coverage_gaps} がEvidence本文中に見当たりません"
            "（件数は足りていても、名指しされた主体そのものの材料が不足している可能性）")
    if institutional_coverage == "insufficient":
        gaps.append("institutional_coverage=insufficient（Institution Intelligence Packに"
                     "候補は存在するが実Profileが無い）")

    return {
        "current_evidence": current_evidence,
        "institutional_coverage": institutional_coverage,
        "overall": current_evidence,
        "direct_evidence_count": direct_count,
        "named_entity_coverage_gaps": named_entity_coverage_gaps,
        "gaps": gaps,
    }


# ---------------------------------------------------------------------------
# Phase R1-D: External Research Adapter（article_analyzer.pyの検索手段を流用、
# 検索ロジック・採否判断は本モジュールが持つ）
# ---------------------------------------------------------------------------

def _extract_json(output_text: str) -> dict:
    """article_analyzer._extract_jsonと同型（コードブロック優先、無ければ最初の{}を拾う）。"""
    text = (output_text or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            text = text[start:end + 1]
    return json.loads(text)


def _extract_web_search_citations(resp) -> list:
    """article_analyzer._extract_evidenceと同型: responses APIのannotations
    (url_citation)のみをEvidence候補として拾う。本文中の自由記述URLは信用しない
    （Web検索ツールは実在しないURLを本文中に生成することがある、検証済みの既知問題）。"""
    citations = []
    seen_urls = set()
    for item in getattr(resp, "output", []) or []:
        if getattr(item, "type", None) != "message":
            continue
        for c in getattr(item, "content", []) or []:
            for ann in getattr(c, "annotations", None) or []:
                if getattr(ann, "type", None) != "url_citation":
                    continue
                url = getattr(ann, "url", None)
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                citations.append({"url": url, "title": getattr(ann, "title", None)})
    return citations


_RESEARCH_SYSTEM_PROMPT = """あなたは地政学インテリジェンスのResearch Assistantです。
専門家が局面を判断するために不足している具体的な調査項目（Investigation Question）に
ついて、Web検索ツールを使って関連する一次情報・報道を探してください。

厳守事項:
- 必ずWeb検索ツールを1回以上呼び出すこと。検索せずに回答してはいけない。
- 検索クエリには、固有名詞・政策名・機関名・日付等をできるだけ具体的に含めること。
- 望ましい情報源の種類（{search_lens}）を優先して検索すること。ただし実際に見つかった
  情報源の種類は別途Evidence Qualificationで客観的に再判定するため、ここでは検索行動の
  指針として使うだけでよい（primary_officialを要求してもReutersのような二次報道しか
  見つからない場合はそのまま報告してよい、無理に一次情報だと主張しない）。
- 見つけた情報の要旨を1〜3文の日本語で説明し、その後改行してJSON1個のみを出力すること:
```json
{{"summary": "...", "found_relevant_information": true/false}}
```

Investigation Question（不足している調査項目）: {question}
背景（何を判断するための調査か）: {context}
"""


def run_external_research(azure_client, model: str, *, question: str, context: str,
                           search_lens: str = "current_news, primary_official") -> dict:
    """External Researchの1回分の呼び出し（既存CallBudgetとは別立てのSearchBudgetで
    数える、専用の検索実行アダプタ）。article_analyzer.verify_claimと同じ
    `responses.create(tools=[web_search_preview], tool_choice="required")`パターンを
    流用するが、プロンプト・目的（1つのClaimの実在確認ではなく、Investigation
    Questionを解くための情報収集）は別物。戻り値: {"summary", "found", "citations",
    "usage"}。"""
    resp = azure_client.responses.create(
        model=model,
        instructions=_RESEARCH_SYSTEM_PROMPT.format(question=question, context=context, search_lens=search_lens),
        input="上記のInvestigation Questionについて検索してください。",
        tools=[{"type": "web_search_preview"}],
        tool_choice="required",
    )
    try:
        data = _extract_json(resp.output_text)
    except (ValueError, json.JSONDecodeError):
        data = {}
    return {
        "summary": data.get("summary"),
        "found": bool(data.get("found_relevant_information")),
        "citations": _extract_web_search_citations(resp),
        "usage": getattr(resp, "usage", None),
    }


# ---------------------------------------------------------------------------
# Evidence Qualification: 実際に採用したものだけ既存geo_documentsへ永続化する
# ---------------------------------------------------------------------------

# geo_source_registry.source_type（既存、official_site/news/sns/commercial_data/other）
# へ決定論的に分類する。要求したSearch Lens（primary_official等）をそのまま信用せず、
# 実際のドメインから独立に再判定する（Evidence Qualificationの核心）。
_OFFICIAL_TLD_SUFFIXES = (".gov", ".mil")
_OFFICIAL_DOMAIN_HINTS = (
    "europa.eu", "nato.int", "un.org", "imf.org", "worldbank.org", "wto.org", "oecd.org",
)
_NEWS_DOMAIN_HINTS = (
    "reuters.com", "apnews.com", "afp.com", "bloomberg.com", "ft.com", "wsj.com",
    "nytimes.com", "bbc.co.uk", "bbc.com", "cnn.com", "nikkei.com", "asahi.com",
    "yomiuri.co.jp", "kyodonews.net", "washingtonpost.com", "theguardian.com",
)
_SNS_DOMAIN_HINTS = ("twitter.com", "x.com", "facebook.com", "reddit.com", "t.me")


def _domain_from_url(url: str) -> str:
    try:
        return (urlparse(url).netloc or url).lower()
    except ValueError:
        return url.lower()


def classify_source_type_from_url(url: str) -> str:
    """geo_source_registry.source_type（5値: official_site/news/sns/commercial_data/
    other）へドメインから決定論的に分類する。要求したSearch Lensは一切参照しない
    （検索時の意図と実際の情報源種別を混同しないため）。"""
    domain = _domain_from_url(url)
    if domain.endswith(_OFFICIAL_TLD_SUFFIXES) or any(h in domain for h in _OFFICIAL_DOMAIN_HINTS):
        return "official_site"
    if any(h in domain for h in _NEWS_DOMAIN_HINTS):
        return "news"
    if any(h in domain for h in _SNS_DOMAIN_HINTS):
        return "sns"
    return "other"


def classify_temporal_status(published_at: str | None) -> str:
    """published_atからtemporal_status（current/recent/historical/unknown）を粗く
    分類する。superseded判定はここでは行わない（同一トピックの後続文書が現れた際に
    別途評価する、Chunk4では未実装のP1候補）。"""
    if not published_at:
        return "unknown"
    try:
        published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
    except ValueError:
        return "unknown"
    age_days = (datetime.now(timezone.utc) - published).days
    if age_days < 0:
        return "unknown"
    if age_days <= 30:
        return "current"
    if age_days <= 180:
        return "recent"
    return "historical"


def get_or_create_domain_source(client, *, theme_id: str, domain: str, source_type: str) -> dict:
    """geo_source_registry（既存、テーマ単位のSource登録）を、ドメイン単位でget-or-create
    する。同一ドメインが同一テーマで既に登録済みならそれを返す（新規カラム不要、
    既存Infrastructureの再利用）。"""
    existing = client.select("geo_source_registry", {
        "select": "*", "theme_id": f"eq.{theme_id}", "name": f"eq.{domain}",
    })
    if existing:
        return existing[0]
    inserted = client.insert("geo_source_registry", [{
        "theme_id": theme_id, "name": domain, "url": f"https://{domain}",
        "source_type": source_type, "notes": "Expert AI External Researchにより自動登録",
    }])
    return inserted[0]


def qualify_and_persist_citations(client, *, theme_id: str, citations: list) -> list:
    """Web Search Adapterが返したcitations（{"url","title"}）のうち、Evidence
    Qualificationを通過したものだけをgeo_documentsへ永続化する。既存
    content_hash一意制約（geo_documents_content_hash_idx）をdedupにそのまま使う
    ——同一URLは同じcontent_hashになるため、既に永続化済みのURLは重複挿入されない
    （INSERT自体が失敗するのではなく、事前に存在確認してskipする）。
    戻り値: 新規に永続化したgeo_documentsの行一覧。"""
    persisted = []
    for c in citations:
        url = c.get("url")
        if not url:
            continue
        content_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
        existing = client.select("geo_documents", {"select": "document_id", "content_hash": f"eq.{content_hash}"})
        if existing:
            continue  # 既に永続化済み（同一URL）。重複保存しない。

        domain = _domain_from_url(url)
        source_type = classify_source_type_from_url(url)
        source = get_or_create_domain_source(client, theme_id=theme_id, domain=domain, source_type=source_type)

        row = {
            "theme_id": theme_id, "source_id": source["source_id"],
            "title": c.get("title") or url, "url": url, "canonical_url": url,
            "published_at": None,  # Web検索ツールは構造化されたpublished_atを返さないため未取得
            "content_hash": content_hash,
            "extracted_text": c.get("title") or "",
            "fetch_status": "成功", "is_mock": False,
            "evidence_class": "current_direct_evidence",
            "temporal_status": classify_temporal_status(None),
            "research_origin": "external_research",
        }
        inserted = client.insert("geo_documents", [row])
        if inserted:
            persisted.append(inserted[0])
    return persisted


# ---------------------------------------------------------------------------
# Research Cycle: Research Sufficiency Gateが不足と判定した場合のみ、targeted
# External Researchを実行し、Qualification済みEvidenceを永続化する。
# ---------------------------------------------------------------------------

def run_research_cycle(client, azure_client, model: str, *, theme_id: str, query_text: str,
                        sufficiency: dict, search_budget: int = 2) -> dict:
    """Research Sufficiency Gateがinsufficient/partialの場合のみ、不足している
    Investigation Question（現状はcurrent_evidence gapのみ）についてExternal
    Researchを実行する。既存CallBudget（Stage1/Stage2/Repair、合計3）とは別立ての
    search_budget（既定2）を消費する。戻り値のsearch_calls_usedをexecution_metadataへ
    独立記録すること（既存CallBudgetには影響しない）。"""
    if sufficiency.get("current_evidence") == "sufficient":
        return {
            "triggered": False, "documents_added": [], "search_calls_used": 0,
            "stop_condition": "sufficient_comparison",
        }

    question = f"{query_text} について、直近の公式発表・報道を確認する。"
    context = f"Research Sufficiency Gateがcurrent_evidence={sufficiency.get('current_evidence')}と判定したため、不足しているCurrent Evidenceを補うための調査。"

    documents_added = []
    search_calls_used = 0
    stop_condition = "no_new_evidence"
    try:
        result = run_external_research(azure_client, model, question=question, context=context)
        search_calls_used += 1
        if result["citations"]:
            documents_added = qualify_and_persist_citations(client, theme_id=theme_id, citations=result["citations"])
            stop_condition = "sufficient_comparison" if documents_added else "no_new_evidence"
        else:
            stop_condition = "evidence_unobtainable"
    except Exception as exc:  # noqa: BLE001 — External Research失敗時もReasoning自体は継続させる
        stop_condition = "evidence_unobtainable"
        return {
            "triggered": True, "documents_added": [], "search_calls_used": search_calls_used,
            "stop_condition": stop_condition, "error": str(exc),
        }

    if search_calls_used >= search_budget:
        stop_condition = stop_condition if documents_added else "budget_limit"

    return {
        "triggered": True, "documents_added": documents_added,
        "search_calls_used": search_calls_used, "stop_condition": stop_condition,
    }
