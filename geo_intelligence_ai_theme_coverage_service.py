"""Geopolitical Intelligence OS Phase 2F-6: Theme Discovery Internal Intelligence Bridge。

「Themeを選ぶAI」ではない。Theme Discovery（Phase 2G、今回UIは作らない）に対して、
そのThemeについて社内で何が既に分かっているかを安全に返す**Intelligence Memory API**。
このBridgeがTheme重要度・優先順位・採否を判断することは一切しない（§15、それらは
Phase 2G側の責務）。

絶対条件（§3 Stable Core、INTELLIGENCE_AI_GROUNDED_ANALYSIS_V1のFreeze Manifest参照）:
Permission-filtered retrieval / Backend metadata filter / Lexical Hybrid Search / Context
selection / Lineage deduplication / Grounded Analysis / Source Trace / Review awareness /
Version awarenessはすべて既存geo_intelligence_ai_search_service.search()/
geo_intelligence_ai_analysis_service.analyze()をそのまま呼ぶ。新しい検索ロジック・新しい
Permissionロジックはここでは一切作らない。

2段階構成（§12 Token/Cost Control）:
    Stage 1（既定、LLM呼び出し無し）: search()の結果だけから件数・最新日・類似Report・
        既出Actor・deterministic gap（Python側のset比較のみ）を組み立てる。
    Stage 2（include_analysis=True時のみ）: 既存analyze()をmode="coverage"でそのまま
        呼び、findings/coverage/source_refsを「既存の主要論点」としてそのまま転用する
        （analyze()自体のSchema/ロジックは一切変更しない）。

No Hidden-corpus Leakage（§6）: 件数・類似Report・Actor・Gap判定はすべて、呼び出し時の
requester_context/is_internal_callerでsearch()がPermission評価した後の結果だけから
組み立てる。Unauthorized Reportの存在を示唆する文言（「他部門に○件あります」等）は
一切生成しない——単に「見えている範囲でのcoverage」を返すだけである。
"""
import geo_intelligence_ai_analysis_service as analysis_service
import geo_intelligence_ai_search_service as search_service

# Stage 1のcount/latest date集計はsearch()のlimitで打ち切られる（新しいCOUNTクエリ・
# 新しい検索ロジックは作らない、既存limitパラメータの再利用のみ）。この上限を超える
# eligible+一致件数がある場合、件数は実数より少なく出ることがある——既知の制約として
# Completion Reportで明示する。
DEFAULT_COVERAGE_SCAN_LIMIT = 200
DEFAULT_SIMILAR_LIMIT = 5


def _build_query_text(theme_candidate: dict) -> str:
    """既存Lexical Hybrid Search（'simple' config、AND一致）向けに、短いキーワード列を
    優先する（Phase 2F-4のresolve_current_query()と同じ教訓——長い自然文はAND一致を
    満たせずrecallが落ちる）。theme_titleを主とし、無ければkeywordsで代替する。"""
    title = (theme_candidate.get("theme_title") or "").strip()
    if title:
        return title
    keywords = theme_candidate.get("keywords") or []
    return " ".join(keywords[:4])


def _build_filters(theme_candidate: dict) -> dict | None:
    """§3 Stable Core: Backend metadata filterは既存search()のfilters引数をそのまま使う。
    新しいfilterキーは追加しない。"""
    filters = {}
    if theme_candidate.get("date_from"):
        filters["date_from"] = theme_candidate["date_from"]
    if theme_candidate.get("date_to"):
        filters["date_to"] = theme_candidate["date_to"]
    return filters or None


def _empty_coverage(theme_candidate: dict) -> dict:
    return {
        "theme_title": theme_candidate.get("theme_title"),
        "coverage": {
            "accessible_report_count": 0, "accessible_knowledge_count": 0,
            "approved_report_count": 0, "in_review_report_count": 0, "uploaded_only_report_count": 0,
        },
        "recency": {"latest_report_date": None, "latest_approved_report_date": None},
        "similar_internal_intelligence": [],
        "key_actors_already_covered": [],
        "potential_coverage_gaps": sorted(set(
            (theme_candidate.get("keywords") or [])
            + (theme_candidate.get("actors") or [])
            + (theme_candidate.get("regions") or [])
        )),
        "existing_analysis_themes": [],
        "source_refs": [],
        "stage": "metadata_only",
        "warnings": [],
    }


def _term_covered(term: str, covered_terms: set) -> bool:
    t = term.lower()
    return any(t in c or c in t for c in covered_terms if c)


def assess_internal_coverage(
        client, azure_client, model, theme_candidate: dict, *,
        requester_context: dict = None, is_internal_caller: bool = True, caller_domain: str = None,
        include_demo: bool = False, include_analysis: bool = False,
        similar_limit: int = DEFAULT_SIMILAR_LIMIT,
        coverage_scan_limit: int = DEFAULT_COVERAGE_SCAN_LIMIT) -> dict:
    """Phase 2F-6本体。theme_candidateは最小共通schema（§4、全項目省略可、theme_titleまたは
    keywordsのどちらかがあれば検索可能）:
        theme_title, theme_description, keywords, actors, regions, signal_summary,
        date_from, date_to

    requester_context/is_internal_caller/caller_domainは既存search()/analyze()と同じ意味
    （人間はrequester_context、Agentはis_internal_caller/caller_domain、§16 Security Test 7）。
    """
    query_text = _build_query_text(theme_candidate)
    if not query_text.strip():
        return _empty_coverage(theme_candidate)

    filters = _build_filters(theme_candidate)
    search_result = search_service.search(
        client, query_text, requester_context=requester_context, is_internal_caller=is_internal_caller,
        caller_domain=caller_domain, include_demo=include_demo, limit=coverage_scan_limit, filters=filters)
    results = search_result["results"]

    report_entries = [r for r in results if r["source_type"] == "report"]
    ki_entries = [r for r in results if r["source_type"] == "knowledge_item"]

    approved = [r for r in report_entries if r.get("review_status") == "approved"]
    in_review = [r for r in report_entries if r.get("review_status") == "in_review"]
    uploaded_only = [r for r in report_entries if r.get("review_status") == "uploaded_only"]

    report_dates = sorted((r["date"] for r in report_entries if r.get("date")), reverse=True)
    approved_dates = sorted((r["date"] for r in approved if r.get("date")), reverse=True)

    key_actors_covered = sorted({a for r in report_entries for a in (r.get("key_actors") or [])})
    regions_covered = sorted({a for r in report_entries for a in (r.get("region") or [])})
    themes_covered = sorted({r["theme"] for r in report_entries if r.get("theme")})
    covered_terms = {t.lower() for t in (key_actors_covered + regions_covered + themes_covered)}

    candidate_terms = sorted(set(
        (theme_candidate.get("keywords") or [])
        + (theme_candidate.get("actors") or [])
        + (theme_candidate.get("regions") or [])
    ))
    potential_gaps = [term for term in candidate_terms if not _term_covered(term, covered_terms)]

    # ranking順（既にsearch()がtext_score降順）を保ったまま上位similar_limit件だけ採用。
    # source_refは既存report_id/knowledge_item_idをそのまま使う（§14、独自IDを作らない）。
    similar = []
    for r in results[:similar_limit]:
        similar.append({
            "source_ref": r["report_id"] if r["source_type"] == "report" else r["knowledge_item_id"],
            "source_type": r["source_type"], "title": r.get("title"), "date": r.get("date"),
            "review_status": r.get("review_status"),
            "lexical_relevance": r.get("text_score"),
            "snippet": analysis_service.clean_snippet(r.get("snippet")),
        })

    output = {
        "theme_title": theme_candidate.get("theme_title"),
        "coverage": {
            "accessible_report_count": len(report_entries),
            "accessible_knowledge_count": len(ki_entries),
            "approved_report_count": len(approved),
            "in_review_report_count": len(in_review),
            "uploaded_only_report_count": len(uploaded_only),
        },
        "recency": {
            "latest_report_date": report_dates[0] if report_dates else None,
            "latest_approved_report_date": approved_dates[0] if approved_dates else None,
        },
        "similar_internal_intelligence": similar,
        "key_actors_already_covered": key_actors_covered,
        "potential_coverage_gaps": potential_gaps,
        "existing_analysis_themes": [],
        "source_refs": [],
        "stage": "metadata_only",
        "warnings": [],
    }

    if not include_analysis:
        return output

    # Stage 2: 既存analyze()をmode="coverage"でそのまま呼ぶ（Schema/ロジック無変更、§11）。
    # findings/source_refsは「既存の主要論点」としてそのまま転用するだけで、新しいLLM
    # Schemaはここでは定義しない。
    analysis_result = analysis_service.analyze(
        client, azure_client, model, query_text, mode="coverage", filters=filters,
        requester_context=requester_context, is_internal_caller=is_internal_caller,
        caller_domain=caller_domain, include_demo=include_demo, limit=coverage_scan_limit)

    output["existing_analysis_themes"] = [
        {"claim": f["claim"], "claim_type": f["claim_type"], "source_refs": f["source_refs"]}
        for f in analysis_result["findings"]
    ]
    output["source_refs"] = analysis_result["source_refs"]
    output["warnings"] = analysis_result["warnings"]
    output["stage"] = "with_analysis"
    return output
