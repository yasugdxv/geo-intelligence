"""Geopolitical Intelligence OS Phase 2F-1: Intelligence AI — Permission-filtered Lexical
Hybrid（FTS-based）Report / Knowledge Search（Function A）。

対象: geo_reports（原本/成果物）+ geo_knowledge_items（knowledge_type="approved_report"、
Report等からPublicationされた再利用可能な知識）。両者は`source_type`で区別する
（§4-1「ReportとKnowledge Itemは区別する」）。

絶対条件（§3）: Permission EvaluationはRetrievalより前に行う。
    Normalized Identity → Permission Evaluation → Eligible Corpus → Retrieval
既存geo_knowledge_access_policy（Phase 2E）をそのまま使い、新しい権限ロジックは作らない。
既存search_geo_knowledge_items/search_geo_reports RPC（Full Text Search、ts_rank_cd）に
新設のp_eligible_ids引数を渡すことで、ranking計算自体をeligible corpusだけに限定する
——retrieveしてからUnauthorized documentを落とす方式は使わない。

Embedding/pgvectorは使わない（Azure OpenAIにembeddings用Deploymentが存在しないことを
Phase 2F Auditで確認済み。geo_report_cluster_semantic_service.py等の既存判断と同じ）。
そのため本モジュールが提供するのは真のsemantic/vector検索ではなく、keyword（Full Text
Search、'simple' config、ts_rank_cd）+ metadata filterによるLexical Hybrid Search。
真のsemantic検索はAzure OpenAI embeddings deployment導入後の別タスクとして切り出す。

Phase 2F-2追加: 各resultへ`snippet`（「なぜHITしたか」を示す抜粋）を付与する。RPC側
（sql/2026-09-29b_geo_intelligence_ai_search_snippet.sql）がranking計算と同じ列から
ts_headlineで抜粋するだけで、新しいLLM呼び出しは行わない。ranking順序・permission
evaluationのロジックには一切手を入れていない。

Phase 2F-3追加: date/region/theme/key_actor/review_status/source_type/confidentiality_label
のmetadata filterを、Frontend側の後付けfilterからBackendのEligible Corpus解決へ移す。
必ず Permission Evaluation → Metadata Filter → Retrieval/Ranking の順を守る——
resolve_eligible_*_idsは「permission合格の行だけ」に対してmetadata filterを適用し、
その結果のID集合だけをRPCのp_eligible_idsへ渡す（permission不合格の行がmetadata filterで
先に弾かれてpermission判定をすり抜けることは無い）。Knowledge Itemはregion/theme/
key_actor列を持たないため、これらのfilterが指定された場合はKnowledge Item側を
候補から除外する（データが無い項目を誤って「一致」扱いしない、fail-safe側に倒す）。
"""
import geo_knowledge_access_policy as access_policy
import geo_report_service

APPROVED_REPORT_KNOWLEDGE_TYPE = "approved_report"

# geo_reports/geo_knowledge_itemsの行を横断してcan_access_report系関数へ渡すための
# 最小限の列のみを取得する（本文・summary等の重いフィールドは持ってこない、§6 Eligible
# Corpus解決は軽量であること）。Phase 2F-3のmetadata filter判定に必要な列
# （document_id/country_region/key_actors/uploaded_at/structured_content等）のみ追加する。
_REPORT_POLICY_COLUMNS = (
    "report_id,human_visibility,agent_sharing_policy,allowed_org_units,allowed_domains,"
    "uploaded_by,is_demo,document_id,country_region,key_actors,uploaded_at,report_identity_id"
)
_KNOWLEDGE_ITEM_POLICY_COLUMNS = (
    "knowledge_item_id,status,retrieval_status,human_visibility,agent_sharing_policy,"
    "allowed_org_units,allowed_domains,created_by,is_demo,structured_content"
)


def _resolve_theme_names(client, report_rows: list) -> dict:
    """Phase 2F-3 themeフィルタ専用。geo_reports自体はtheme列を持たず、geo_documents経由
    （document_id→theme_id→geo_themes.name_ja）でしか分からないため、themeフィルタが実際に
    指定された時だけ、permission合格済みの候補集合（既に絞り込み済み、全件ではない）に限定
    してjoinする。"""
    document_ids = sorted({r["document_id"] for r in report_rows if r.get("document_id")})
    if not document_ids:
        return {}
    docs = client.select("geo_documents", {
        "select": "document_id,theme_id", "document_id": f"in.({','.join(document_ids)})"})
    theme_id_by_doc = {d["document_id"]: d.get("theme_id") for d in docs}
    theme_ids = sorted({t for t in theme_id_by_doc.values() if t})
    themes_by_id = {}
    if theme_ids:
        themes = client.select("geo_themes", {
            "select": "theme_id,name_ja", "theme_id": f"in.({','.join(theme_ids)})"})
        themes_by_id = {t["theme_id"]: t["name_ja"] for t in themes}
    return {r["report_id"]: themes_by_id.get(theme_id_by_doc.get(r["document_id"]))
            for r in report_rows if r.get("document_id")}


def _report_matches_filters(row: dict, filters: dict, theme_by_report_id: dict) -> bool:
    if filters.get("region") and filters["region"] not in (row.get("country_region") or []):
        return False
    if filters.get("key_actor") and filters["key_actor"] not in (row.get("key_actors") or []):
        return False
    if filters.get("theme") and theme_by_report_id.get(row["report_id"]) != filters["theme"]:
        return False
    if filters.get("review_status") and row.get("review_status") != filters["review_status"]:
        return False
    if filters.get("confidentiality_label") and \
            access_policy.derive_ui_label_from_policy(row) != filters["confidentiality_label"]:
        return False
    date_from, date_to = filters.get("date_from"), filters.get("date_to")
    if date_from or date_to:
        d = (row.get("uploaded_at") or "")[:10]
        if not d or (date_from and d < date_from) or (date_to and d > date_to):
            return False
    return True


def _knowledge_item_matches_filters(row: dict, filters: dict) -> bool:
    # region/theme/key_actorはKnowledge Itemに対応する列が無いため、指定時は「データが無い
    # ので一致不明」として除外する（誤って一致扱いにしない、fail-safe側）。
    if filters.get("region") or filters.get("theme") or filters.get("key_actor"):
        return False
    if filters.get("review_status") and filters["review_status"] != "approved":
        return False
    if filters.get("confidentiality_label") and \
            access_policy.derive_ui_label_from_policy(row) != filters["confidentiality_label"]:
        return False
    date_from, date_to = filters.get("date_from"), filters.get("date_to")
    if date_from or date_to:
        structured = row.get("structured_content") or {}
        d = (structured.get("approval_date") or "")[:10]
        if not d or (date_from and d < date_from) or (date_to and d > date_to):
            return False
    return True


def resolve_eligible_report_ids(client, *, requester_context: dict = None,
                                 is_internal_caller: bool = True, caller_domain: str = None,
                                 include_demo: bool = False, filters: dict = None) -> set:
    """§3 Permission Evaluation（Report側）。id+policy列のみの軽量selectを行い、
    Pythonでcan_access_reportを適用してeligible id集合を返す。この関数の戻り値だけを
    Retrieval（RPC呼び出し）へ渡す——Retrievalの結果を後から絞り込むのではない。

    Phase 2F-3: filters（dict、省略可）はPermission判定に合格した行だけへ適用する
    （§0「Permission Filter → Metadata Filter → Retrieval」の順序）。"""
    if filters and filters.get("source_type") == "knowledge_item":
        return set()
    params = {"select": _REPORT_POLICY_COLUMNS}
    if not include_demo:
        params["is_demo"] = "eq.false"
    rows = client.select("geo_reports", params)
    if requester_context is not None:
        permitted = [r for r in rows
                     if access_policy.can_access_report(r, operation="list", requester_context=requester_context)]
    else:
        permitted = [r for r in rows
                     if access_policy.can_access_report(
                         r, operation="retrieve_for_ai", is_internal_caller=is_internal_caller,
                         caller_domain=caller_domain)]
    if not filters:
        return {r["report_id"] for r in permitted}
    if filters.get("review_status"):
        geo_report_service.derive_report_review_status(client, permitted)
    theme_by_report_id = _resolve_theme_names(client, permitted) if filters.get("theme") else {}
    return {r["report_id"] for r in permitted if _report_matches_filters(r, filters, theme_by_report_id)}


def resolve_eligible_knowledge_item_ids(client, *, requester_context: dict = None,
                                         is_internal_caller: bool = True, caller_domain: str = None,
                                         include_demo: bool = False,
                                         knowledge_type: str = APPROVED_REPORT_KNOWLEDGE_TYPE,
                                         filters: dict = None) -> set:
    """§3 Permission Evaluation（Knowledge Item側）。既存filter_human_accessible_
    knowledge_items/filter_agent_accessible_knowledge_itemsをそのまま再利用する
    （is_retrievable＝status=active AND retrieval_status=availableの判定も含む）。

    Phase 2F-3: filters（dict、省略可）はPermission判定に合格した行だけへ適用する。"""
    if filters and filters.get("source_type") == "report":
        return set()
    params = {"select": _KNOWLEDGE_ITEM_POLICY_COLUMNS, "knowledge_type": f"eq.{knowledge_type}"}
    if not include_demo:
        params["is_demo"] = "eq.false"
    rows = client.select("geo_knowledge_items", params)
    if requester_context is not None:
        eligible = access_policy.filter_human_accessible_knowledge_items(
            rows, requester_context=requester_context)
    else:
        eligible = access_policy.filter_agent_accessible_knowledge_items(
            rows, is_internal_caller=is_internal_caller, caller_domain=caller_domain)
    if not filters:
        return {r["knowledge_item_id"] for r in eligible}
    return {r["knowledge_item_id"] for r in eligible if _knowledge_item_matches_filters(r, filters)}


def knowledge_item_result(item: dict) -> dict:
    """approved_report Knowledge Itemの表示用metadataを組み立てる。knowledge_type=
    approved_reportは既存Publication Route A/B（Human Gate #2通過済み）でのみ作られる
    ため、review_statusは常に"approved"（geo_reportsのように未レビュー状態は存在しない、
    §4-4「Uploaded onlyとReviewed/Approvedを明確に区別する」の"Approved"側に相当）。"""
    structured_content = item.get("structured_content") or {}
    return {
        "source_type": "knowledge_item",
        "knowledge_item_id": item["knowledge_item_id"],
        "title": item.get("title"),
        "summary": item.get("summary"),
        "date": structured_content.get("approval_date"),
        # 2F-1時点の既知の制約: Knowledge Item自体はregion/theme/key_actors列を持たない
        # （geo_reportsのような列は無い）。source_report_id/source_report_project_id経由で
        # 元Reportを辿れば取得できるが、今回は追加joinをせず未設定のままにする（2F-2以降の
        # 拡張候補として残す、§13 Remaining Gaps参照）。
        "region": None, "theme": None, "key_actors": None,
        "review_status": "approved",
        "confidentiality_label": access_policy.derive_ui_label_from_policy(item),
        "source_report_id": structured_content.get("source_report_id"),
        "source_report_project_id": structured_content.get("report_project_id"),
    }


def report_result(report: dict) -> dict:
    return {
        "source_type": "report",
        "report_id": report["report_id"],
        # Phase 2F-3: 同一Reportの複数versionを同一lineageとして束ねるための識別子
        # （geo_intelligence_ai_analysis_service._build_context_sourcesのlineage grouping用）。
        "report_identity_id": report.get("report_identity_id") or report["report_id"],
        "title": report.get("document_title"),
        "summary": report.get("summary"),
        "date": (report.get("document") or {}).get("published_at") or report.get("uploaded_at"),
        "region": report.get("country_region"),
        "theme": report.get("theme_name"),
        "key_actors": report.get("key_actors"),
        "review_status": report.get("review_status"),
        "confidentiality_label": report.get("confidentiality_label"),
        "version": report.get("version"),
        "author": report.get("author"),
        "report_type": report.get("report_type"),
    }


def search(client, query_text: str, *, requester_context: dict = None, is_internal_caller: bool = True,
           caller_domain: str = None, include_demo: bool = False, limit: int = 20,
           filters: dict = None) -> dict:
    """Intelligence AI Function A本体。§3の順序（Identity→Permission→Eligible Corpus→
    Retrieval）をこの関数の中で機械的に強制する。呼び出し側（API層）がrequester_context
    （人間、認証済みIosIdentity由来）またはis_internal_caller/caller_domain
    （Agent、認証済みCallerIdentity由来）のいずれかを渡す。

    Phase 2F-3: filters（dict、省略可。date_from/date_to/region/theme/key_actor/
    review_status/source_type/confidentiality_label）はresolve_eligible_*_ids内部
    （Permission判定の後）で適用され、Eligible Corpus自体を絞り込む——Retrieval結果を
    後から絞り込むUI側filterとは別物。"""
    if not (query_text or "").strip():
        return {"results": [], "report_count": 0, "knowledge_item_count": 0}

    eligible_report_ids = resolve_eligible_report_ids(
        client, requester_context=requester_context, is_internal_caller=is_internal_caller,
        caller_domain=caller_domain, include_demo=include_demo, filters=filters)
    eligible_ki_ids = resolve_eligible_knowledge_item_ids(
        client, requester_context=requester_context, is_internal_caller=is_internal_caller,
        caller_domain=caller_domain, include_demo=include_demo, filters=filters)

    results = []

    if eligible_report_ids:
        report_hits = client.rpc("search_geo_reports", {
            "p_query": query_text, "p_eligible_ids": sorted(eligible_report_ids),
            "p_include_demo": include_demo, "p_limit": limit,
        }) or []
        # Defense-in-depth（§28 Security Test）: RPC自体のp_eligible_idsフィルタを信頼
        # した上で、SQL側の実装ミス・将来の変更に備え、返ってきたIDがeligible_report_ids
        # 集合に本当に含まれるかをPython側でも再チェックする。二重チェックであり、
        # 正常時はここで何も弾かれない。
        report_hits = [h for h in report_hits if h.get("report_id") in eligible_report_ids]
        if report_hits:
            text_score_by_id = {h["report_id"]: h.get("text_score") or 0.0 for h in report_hits}
            snippet_by_id = {h["report_id"]: h.get("snippet") for h in report_hits}
            reports = geo_report_service.get_reports_by_ids(client, [h["report_id"] for h in report_hits])
            for r in reports:
                entry = report_result(r)
                entry["text_score"] = text_score_by_id.get(r["report_id"], 0.0)
                entry["snippet"] = snippet_by_id.get(r["report_id"])
                results.append(entry)

    if eligible_ki_ids:
        ki_hits = client.rpc("search_geo_knowledge_items", {
            "p_query": query_text, "p_status": ["active"],
            "p_knowledge_types": [APPROVED_REPORT_KNOWLEDGE_TYPE], "p_include_demo": include_demo,
            "p_limit": limit, "p_eligible_ids": sorted(eligible_ki_ids),
        }) or []
        ki_hits = [h for h in ki_hits if h.get("knowledge_item_id") in eligible_ki_ids]
        if ki_hits:
            hit_ids = [h["knowledge_item_id"] for h in ki_hits]
            text_score_by_id = {h["knowledge_item_id"]: h.get("text_score") or 0.0 for h in ki_hits}
            snippet_by_id = {h["knowledge_item_id"]: h.get("snippet") for h in ki_hits}
            items = client.select("geo_knowledge_items", {
                "select": "*", "knowledge_item_id": f"in.({','.join(hit_ids)})"})
            for item in items:
                entry = knowledge_item_result(item)
                entry["text_score"] = text_score_by_id.get(item["knowledge_item_id"], 0.0)
                entry["snippet"] = snippet_by_id.get(item["knowledge_item_id"])
                results.append(entry)

    results.sort(key=lambda r: r["text_score"], reverse=True)
    results = results[:limit]
    return {
        "results": results,
        "report_count": sum(1 for r in results if r["source_type"] == "report"),
        "knowledge_item_count": sum(1 for r in results if r["source_type"] == "knowledge_item"),
    }
