"""地政学ナレッジ＆インテリジェンスDB Phase1: Expert AI向けExternal Signal Retriever。

geo_retrieval_service.py（Knowledge Retrieval）とは明確に分離した別経路。
Knowledge Item状態機械・Entity Mergeには一切触れない、読み取り専用。

返却するSignal dictには常にsource_role='discovery', evidence_status='unverified'を
保持したまま返す（フィルタしない）。呼び出し側（geo_expert_engine._build_system_prompt等）が
「Discovery/未検証の外部シグナルである」ことをプロンプトに明示する責任を持つ。

External SignalsだけでFoundation/Actor Core Profile/Precedent/Validated Knowledgeを
自動更新しない（Knowledge PromotionはPhase2以降）。
"""
import re

_FRESHNESS_KEYWORDS = ("latest", "recent", "current", "今", "直近", "最近", "動き", "状況")


def needs_external_signal_lookup(query_text: str, freshness_requirement=None) -> bool:
    """すべての質問で必ず検索しない。freshness_requirement（既存GeoIntelligenceRequestの
    フィールド、現状呼び出し元での判定ロジックが未実装だったためここで初めて使う）が真値、
    または query_text が最新性を示すキーワードにマッチする場合のみTrueを返す。"""
    if freshness_requirement:
        return True
    if not query_text:
        return False
    lowered = query_text.lower()
    return any(kw.lower() in lowered for kw in _FRESHNESS_KEYWORDS)


def _query_ids_for_theme_or_case(client, *, theme_id: str = None, case_id: str = None) -> list:
    params = {"select": "query_id"}
    if theme_id and case_id:
        params["or"] = f"(theme_id.eq.{theme_id},case_id.eq.{case_id})"
    elif theme_id:
        params["theme_id"] = f"eq.{theme_id}"
    elif case_id:
        params["case_id"] = f"eq.{case_id}"
    else:
        return []
    rows = client.select("geo_external_signal_queries", params)
    return [r["query_id"] for r in rows]


def _signals_via_query_matches(client, query_ids: list, *, limit: int) -> list:
    if not query_ids:
        return []
    match_rows = client.select("geo_external_signal_query_matches", {
        "select": "external_signal_id,last_matched_at",
        "query_id": f"in.({','.join(query_ids)})",
        "order": "last_matched_at.desc",
        "limit": str(limit * 3),  # 記事側の重複除去後にlimit件残るよう余裕を持って取得
    })
    signal_ids = list(dict.fromkeys(r["external_signal_id"] for r in match_rows))[:limit]
    if not signal_ids:
        return []
    rows = client.select("geo_external_signals", {
        "select": "*", "external_signal_id": f"in.({','.join(signal_ids)})",
        "order": "published_at.desc.nullslast",
    })
    return rows


def _signals_via_keyword_fallback(client, query_text: str, *, countries: list = None,
                                   since: str = None, limit: int) -> list:
    if not query_text:
        return []
    escaped = query_text.replace("%", "").replace(",", " ").strip()
    if not escaped:
        return []
    params = {
        "select": "*", "or": f"(title.ilike.*{escaped}*,summary.ilike.*{escaped}*)",
        "order": "published_at.desc.nullslast", "limit": str(limit),
    }
    if since:
        params["published_at"] = f"gte.{since}"
    if countries:
        params["source_country"] = f"in.({','.join(countries)})"
    return client.select("geo_external_signals", params)


def search_external_signals(client, query_text, *, theme_id: str = None, case_id: str = None,
                             countries: list = None, since: str = None, limit: int = 10) -> list:
    """geo_retrieval_service.pyのシグネチャ規約(client, query_text, *, filters..., limit)に倣う。

    【重要】title/summaryの文字列一致だけに頼らない。GDELT記事は英語/中国語等が主体のため、
    日本語の質問文（例:「台湾海峡の最近の動きは？」）とはほぼ一致しない。優先順位:
      1. theme_id / case_id が分かっている場合: geo_external_signal_queries をtheme_id/case_idで
         検索 → query_matches経由で該当queryにヒットした直近Signalsを返す（文字列一致を
         経由しない、最も精度が高い経路）
      2. 1で該当Queryが無い/該当Signalsが0件/theme_id・case_id未指定の場合のみ、
         title/summary ILIKE + source_country/published_atによるkeyword fallback検索を行う
    """
    if theme_id or case_id:
        query_ids = _query_ids_for_theme_or_case(client, theme_id=theme_id, case_id=case_id)
        signals = _signals_via_query_matches(client, query_ids, limit=limit)
        if signals:
            return signals

    return _signals_via_keyword_fallback(client, query_text, countries=countries, since=since, limit=limit)
