"""地政学インテリジェンス基盤 Phase2: Geo Intelligence Agent Service（Retrieval統合・
Knowledge Sufficiency判定・回答生成）。

外部アプリからの問い合わせ窓口。既存のGeo Knowledge/Foundation・Retrieval・Claim/Evidence・
Geopolitical Expert AI等は一切変更せず再利用する。Geo Intelligence専用の検索ロジックは
再実装せず、既存の以下をそのまま呼び出す:
    - geo_search_service.search_entities/search_knowledge_items/search_claims（FTS横断検索）
    - geo_retrieval_service.get_foundation_knowledge/get_relevant_precedents/
      get_relevant_mechanisms/get_relevant_reasoning_rules/get_contested_knowledge/
      get_internal_expert_assessments/get_reference_claims_for_issue（Issue単位の精密検索）
    - geo_relationship_service.list_relationships_for_entity
    - geo_freshness_service.classify_knowledge_freshness
    - geo_expert_engine.run_expert_request（既存Geopolitical Expert AI、条件を満たす場合のみ）
既存Retrieval（geo_search_service.py）が内部で作るretrieval_run_idは呼び出し側へ返さない設計
のため、本Agent向けに1本のRunへ統合記録するAdapter（_record_retrieval_run）のみを追加する。

責務分離（各段階を独立関数にし、Phase3以降も個別に差し替えられるようにする）:

    Request → normalize_request → resolve_entities_and_issues → retrieve_knowledge
    → assess_sufficiency → assess_confidence → generate_analysis
      （sufficient かつ analysis_requested の場合のみ既存Expert AIを呼ぶ。
        Knowledge不足をExpert AIの推測で穴埋めすることはしない）
    → build_response

Phase2でも未接続の箇所（Phase3の対象、明示的に関数境界を分けてある）:
    - geo_knowledge_gap_service: geo_cases前提の実装のため、Case化していない外部Requestとは
      未接続。knowledge_gapはPhase2では「今回の検索で見つからなかったカテゴリ一覧」という
      軽量な派生情報に留める（_missing_categories）。
"""
import os
import time
import uuid
from datetime import datetime, timezone

from config_utils import load_config

import geo_audit
import geo_expert_engine
import geo_freshness_service
import geo_knowledge_access_policy
import geo_knowledge_gap_service
import geo_relationship_service
import geo_retrieval_service
import geo_search_service

_config = load_config()

RETRIEVAL_LIMIT = 10

# Phase2: 分類体系は既存のknowledge_type/information_classをそのまま使う（新設しない）。
FOUNDATION_KNOWLEDGE_TYPES = ("country_foundation", "actor_profile", "party_profile",
                              "relationship_assessment", "historical_path_dependency")

# request_type→Expert AI呼び出し時のmode対応（既存geo_expert_schema.MODESの範囲内で選ぶ。
# Geo Intelligence専用の新モードは作らない）。
EXPERT_MODE_BY_REQUEST_TYPE = {
    "user_question": "case_analysis",
    "weekly_article": "executive_brief",
}

# geo_retrieval_results.result_typeの既存CHECK制約（entity/relationship/knowledge_item/claim/
# event/observation）に収まらないカテゴリ（internal_report/scenario_hypothesis）はTrace記録の
# 対象外とする（スキーマ変更はPhase2のスコープ外、Phase3での検討事項として完了報告に明記する）。

# weekly_monitoring向けConfig（Kill Switch、item35）。既存config.json方式
# （config_utils.load_config、geo_intelligence.weekly_monitoring.*）を優先し、専用のConfig基盤は
# 新設しない。config.jsonが無いDocker/Azure Web App等の実行環境向けに、GEO_WEEKLY_MONITORING_
# ENABLED/GEO_WEEKLY_MONITORING_MAX_ITEMS環境変数もフォールバックとして受け付ける。
def _weekly_monitoring_config() -> dict:
    return _config.get("geo_intelligence", {}).get("weekly_monitoring", {})


def weekly_monitoring_enabled() -> bool:
    if "enabled" in _weekly_monitoring_config():
        return bool(_weekly_monitoring_config()["enabled"])
    env = os.environ.get("GEO_WEEKLY_MONITORING_ENABLED")
    if env is not None:
        return env.strip().lower() not in ("false", "0", "")
    return True


def weekly_monitoring_max_items() -> int:
    if "max_items" in _weekly_monitoring_config():
        return int(_weekly_monitoring_config()["max_items"])
    return int(os.environ.get("GEO_WEEKLY_MONITORING_MAX_ITEMS", "10"))


class GeoIntelligenceError(Exception):
    """Geo Intelligence Agent内部処理の失敗。Failure Isolationのため、呼び出し側
    （process_request）がこれを捕捉し、例外を外へ伝播させずstatus='failed'のResponseへ変換する。"""


def normalize_request(request: dict) -> dict:
    """Requestの正規化。request_typeにより必須項目が異なる:
    user_question: question必須（既存、後方互換を維持）。
    weekly_monitoring: questionは不要（未指定ならScope情報から内部用の説明文を合成する）。
    表記ゆれ解消はresolve_entities_and_issues側でEntity解決という形で行う。"""
    if not request.get("requester_app"):
        raise GeoIntelligenceError("requester_appは必須です")
    request_type = request.get("request_type", "user_question")

    question = (request.get("question") or "").strip()
    period_start = request.get("period_start")
    period_end = request.get("period_end")
    sustainability_themes = list(request.get("sustainability_themes") or [])
    priority_geographies = list(request.get("priority_geographies") or [])
    business_context = request.get("business_context")

    if request_type == "weekly_monitoring":
        if not question:
            # questionは内部の監査ログ・FTS検索用の説明文として合成する（外部からの入力は不要）。
            scope_desc = "、".join(priority_geographies) or "指定地域"
            theme_desc = "、".join(sustainability_themes) or "指定テーマ"
            question = (f"[Weekly Monitoring {period_start or '?'}〜{period_end or '?'}] "
                       f"{scope_desc}における{theme_desc}関連の地政学動向探索")
    else:
        if not question:
            raise GeoIntelligenceError("questionは必須です")

    return {
        "request_id": request.get("request_id") or str(uuid.uuid4()),
        "requester_app": request["requester_app"],
        "request_type": request_type,
        "question": question,
        "country_region": list(request.get("country_region") or []),
        "themes": list(request.get("themes") or []),
        "theme_id": request.get("theme_id"),
        "context": request.get("context"),
        "time_horizon": request.get("time_horizon"),
        "source_refs": list(request.get("source_refs") or []),
        "analysis_requested": request.get("analysis_requested", True),
        "requested_at": request.get("requested_at") or datetime.now(timezone.utc).isoformat(),
        "period_start": period_start,
        "period_end": period_end,
        "sustainability_themes": sustainability_themes,
        "priority_geographies": priority_geographies,
        "business_context": business_context,
        # Phase B-B: Cross-Department Inquiry Receiver向け（他部門Department Intelligence AI
        # からのuser_question問い合わせ）。未指定なら空のまま素通りし、process_requestの
        # 分岐にも影響しない（既存user_question呼び出し元の挙動は変わらない）。
        "source_department": request.get("source_department"),
        "source_workflow": request.get("source_workflow"),
        "freshness_requirement": request.get("freshness_requirement"),
    }


def _build_query_text(normalized: dict) -> str:
    parts = [normalized["question"], *normalized["country_region"], *normalized["themes"]]
    return " ".join(p for p in parts if p)


def _fts_search_terms(normalized: dict) -> list:
    """FTS横断検索（search_knowledge_items/search_claims）に渡す語のリスト。質問文全体と
    country_region/themesの各語を別々に検索する（_build_query_textで1本の複合クエリへ結合すると
    言語混在・長い複合語で0件になることがあるため。実DB E2Eで確認済みの制約への対応）。"""
    return [t for t in [normalized["question"], *normalized["country_region"], *normalized["themes"]] if t]


def resolve_entities_and_issues(client, normalized: dict) -> dict:
    """country_region/themesの各語を既存geo_search_service.search_entities（変更しない）で
    Entityへ解決し、そのEntityが紐づく既存Issue（geo_entity_issues経由）を集める。ここで
    得たissue_idsを使って、Issue単位の精密なRetrieval（Foundation/Precedent/Internal Expert
    Assessment/Reference Claims等）を後段で呼び出せるようにする。

    Geo Chat Bridge向け: 呼び出し元がtheme_idを把握している場合（例: テーマページ埋め込みの
    チャット）、country_region/themesのテキストマッチだけに頼らず、geo_issues.theme_id経由でも
    issue_idsを直接解決し、entity経由で得たissue_idsへ合流させる（テーマの日本語名がEntityの
    canonical_nameとテキスト一致するとは限らないため、より確実な経路として追加する。
    entity解決自体は変更しない）。"""
    entities_by_id = {}
    for term in [*normalized["country_region"], *normalized["themes"]]:
        result = geo_search_service.search_entities(client, term, log_trace=False)
        for r in result["results"]:
            entities_by_id[r["entity"]["entity_id"]] = r["entity"]

    issue_ids = set()
    if entities_by_id:
        links = client.select("geo_entity_issues", {
            "select": "issue_id", "entity_id": f"in.({','.join(entities_by_id)})",
        })
        issue_ids |= {link["issue_id"] for link in links}

    if normalized.get("theme_id"):
        theme_issues = client.select("geo_issues", {
            "select": "issue_id", "theme_id": f"eq.{normalized['theme_id']}",
        })
        theme_issue_ids = {i["issue_id"] for i in theme_issues}
        issue_ids |= theme_issue_ids
        if theme_issue_ids:
            # theme_id経由で得たissue_idsについては、逆方向（既存geo_retrieval_service.
            # get_relevant_entities、Foundation Pack等でも使われる既存関数）でも
            # Entityを補完する。これにより、テーマ名とEntity名がテキスト一致しない場合でも
            # stakeholder/dynamicsが正しく解決される。
            for entity in geo_retrieval_service.get_relevant_entities(client, sorted(theme_issue_ids)):
                entities_by_id.setdefault(entity["entity_id"], entity)

    return {"entities": list(entities_by_id.values()), "issue_ids": sorted(issue_ids)}


def _resolve_relationships_assets_dependencies(client, entities: list) -> list:
    """既存geo_relationship_service.list_relationships_for_entity（変更しない）でRelationshipを
    集める。Asset/Dependencyには専用の「Entityから引く」公開関数が無いため、単純な直接select
    （geo_foundation_pack_service._assets_and_dependenciesと同じ絞り込み条件）で補う。
    spec上「relationship」1カテゴリに集約し、各要素にkindを付けて区別する
    （Phase3-0のassets_dependencies_leverageと同じ考え方）。"""
    if not entities:
        return []
    entity_ids = [e["entity_id"] for e in entities]

    seen = {}
    for rel in geo_relationship_service.list_relationships_for_entities(client, entity_ids):
        seen[rel["relationship_id"]] = {**rel, "kind": "relationship"}
    relationships = list(seen.values())

    assets = client.select("geo_assets", {
        "select": "*", "entity_id": f"in.({','.join(entity_ids)})", "is_demo": "eq.false",
    })
    dependencies = client.select("geo_dependencies", {
        "select": "*", "entity_id": f"in.({','.join(entity_ids)})", "is_demo": "eq.false",
    })
    return relationships + [{**a, "kind": "asset"} for a in assets] + [{**d, "kind": "dependency"} for d in dependencies]


def _filter_latest_report_documents(client, candidate_doc_ids: list) -> set:
    """Report Versioning（Closeout Phase3）Cleanup: 候補document_idのうち、そのReportの
    report_identity_idにおけるglobal latest versionに属するものだけを残す（旧Versionが
    Retrieval・週次候補へ混入し続けるバグの修正）。1つのdocument_idが複数のgeo_reports行
    （メタデータのみのVersion）に共有され得るため、document_idとgeo_reports行を1:1と
    仮定しない。identityごとのSELECTループは行わず、常に高々2回のSELECTで完結する。
    report_identity_idが無い行（migration未適用環境）は後方互換で無条件に有効とする。"""
    if not candidate_doc_ids:
        return set()

    candidate_reports = client.select("geo_reports", {
        "select": "document_id,report_identity_id,version",
        "document_id": f"in.({','.join(candidate_doc_ids)})",
    })
    if not candidate_reports:
        return set()

    identity_ids = sorted({r["report_identity_id"] for r in candidate_reports
                           if r.get("report_identity_id")})
    max_version_by_identity = {}
    if identity_ids:
        all_versions = client.select("geo_reports", {
            "select": "report_identity_id,version",
            "report_identity_id": f"in.({','.join(identity_ids)})",
        })
        for r in all_versions:
            identity = r["report_identity_id"]
            version = r.get("version") or 1
            if version > max_version_by_identity.get(identity, 0):
                max_version_by_identity[identity] = version

    kept = set()
    for r in candidate_reports:
        identity = r.get("report_identity_id")
        if identity is None:
            kept.add(r["document_id"])
            continue
        if (r.get("version") or 1) == max_version_by_identity.get(identity):
            kept.add(r["document_id"])
    return kept


def _resolve_internal_reports(client, issue_ids: list) -> list:
    """internal_report: 2つの既存経路を統合する（新しい「Report」専用検索は作らない）。
    (1) 既存Expert Review Loopの成果物（geo_expert_review_sessions.final_document_id）のうち、
        対象Issueに紐づくCase（geo_case_issues経由）のもの。
    (2) Phase4のReport Upload（geo_report_service.ingest_report）由来。そのReportから生成された
        Claim（origin_type='document'）が対象Issueへ実際に紐づいているかどうかで判定する
        （既存geo_claim_issuesのリンクをそのまま辿るだけで、Report専用の紐付け先は増やさない）。"""
    if not issue_ids:
        return []

    case_links = client.select("geo_case_issues", {"select": "case_id", "issue_id": f"in.({','.join(issue_ids)})"})
    case_ids = sorted({c["case_id"] for c in case_links})
    review_doc_ids = set()
    if case_ids:
        sessions = client.select("geo_expert_review_sessions", {
            "select": "*", "case_id": f"in.({','.join(case_ids)})", "is_demo": "eq.false",
        })
        review_doc_ids = {s["final_document_id"] for s in sessions if s.get("final_document_id")}

    claim_links = client.select("geo_claim_issues", {"select": "claim_id", "issue_id": f"in.({','.join(issue_ids)})"})
    claim_ids = sorted({c["claim_id"] for c in claim_links})
    report_doc_ids = set()
    if claim_ids:
        claims = client.select("geo_claims", {
            "select": "origin_id", "claim_id": f"in.({','.join(claim_ids)})", "origin_type": "eq.document",
        })
        candidate_doc_ids = sorted({c["origin_id"] for c in claims if c.get("origin_id")})
        report_doc_ids = _filter_latest_report_documents(client, candidate_doc_ids)

    doc_ids = sorted(review_doc_ids | report_doc_ids)
    if not doc_ids:
        return []
    return client.select("geo_documents", {"select": "*", "document_id": f"in.({','.join(doc_ids)})"})


def _resolve_scenario_hypotheses(client, issue_ids: list) -> list:
    """scenario_hypothesis: 既存geo_case_hypotheses（Caseに紐づく仮説）のうち、対象Issueに
    紐づくCaseのものを取得する。外部Requestは元々geo_casesを持たないため、既存Caseが
    対象Issueに紐づいていない限り空になる（推測で仮説を作らない）。"""
    if not issue_ids:
        return []
    case_links = client.select("geo_case_issues", {"select": "case_id", "issue_id": f"in.({','.join(issue_ids)})"})
    case_ids = sorted({c["case_id"] for c in case_links})
    if not case_ids:
        return []
    return client.select("geo_case_hypotheses", {"select": "*", "case_id": f"in.({','.join(case_ids)})"})


def _split_by_internal_origin(items: list) -> tuple:
    """information_class='internal_expert_assessment'（Internal Expert Learning Layerで
    Promoteされた知識）とそれ以外を分ける。既存の二軸（Phase2B-2）をそのまま利用する。"""
    internal = [i for i in items if i.get("information_class") == "internal_expert_assessment"]
    external = [i for i in items if i.get("information_class") != "internal_expert_assessment"]
    return external, internal


def retrieve_knowledge(client, normalized: dict) -> dict:
    """既存Retrieval機能のみを呼び出し、spec指定の10分類へ整理する。Issue単位の精密検索
    （resolve_entities_and_issuesで解決したissue_ids経由）と、Issueに未紐付けの情報を拾う
    FTS横断検索（geo_search_service、Phase1から継続）の両方を組み合わせ、結果をdedupeする。"""
    query_text = _build_query_text(normalized)
    resolved = resolve_entities_and_issues(client, normalized)
    entities, issue_ids = resolved["entities"], resolved["issue_ids"]

    # ---- Issue単位の精密Retrieval（既存geo_retrieval_service、無変更） ----
    foundation_raw = geo_retrieval_service.get_foundation_knowledge(client, issue_ids)
    precedent_raw = geo_retrieval_service.get_relevant_precedents(client, issue_ids)
    mechanism_raw = geo_retrieval_service.get_relevant_mechanisms(client, issue_ids)
    reasoning_rule_raw = geo_retrieval_service.get_relevant_reasoning_rules(client, issue_ids)
    contested = geo_retrieval_service.get_contested_knowledge(client, issue_ids)
    case_findings = geo_retrieval_service.get_internal_expert_assessments(client, issue_ids)
    reference_claims = geo_retrieval_service.get_reference_claims_for_issue(client, issue_ids)

    # ---- FTS横断検索（既存geo_search_service、Issueに未紐付けの情報を補完） ----
    # 実DB E2Eで判明した制約: FTSは質問文+country_region+themesを1本の複合クエリへ結合すると、
    # 言語混在（例: 日本語の質問文＋英語のcountry_region）や長い複合語で0件になることがある
    # （個別の語では正しくヒットする）。resolve_entities_and_issuesの既存Entity解決と同じく、
    # 各語を個別にクエリしてID単位でマージする（geo_search_service自体は変更しない）。
    ki_by_id, claim_by_id = {}, {}
    for term in _fts_search_terms(normalized):
        for r in geo_search_service.search_knowledge_items(
                client, term, limit=RETRIEVAL_LIMIT, log_trace=False)["results"]:
            ki_by_id.setdefault(r["knowledge_item"]["knowledge_item_id"], r)
        for r in geo_search_service.search_claims(client, term, limit=RETRIEVAL_LIMIT, log_trace=False)["results"]:
            claim_by_id.setdefault(r["claim"]["claim_id"], r)
    ki_fts = {"results": list(ki_by_id.values())}
    claims_fts = {"results": list(claim_by_id.values())}

    # foundation/precedentは、internal_expert_assessment由来のものをinternal_expert_knowledgeへ
    # 分離する（Reusable KnowledgeとInternal Expert Reasoningを混同しない、既存Phase2B-2の原則）
    foundation, foundation_internal = _split_by_internal_origin(foundation_raw)
    precedent, precedent_internal = _split_by_internal_origin(precedent_raw)
    # mechanism/reasoning_ruleにはspec上の専用カテゴリが無いため、内部Origin以外はfoundationへ
    # 合流させる（Reusableな分析部品という性質がFoundationに近いため。判断根拠は完了報告に明記）
    mechanism_external, mechanism_internal = _split_by_internal_origin(mechanism_raw)
    reasoning_external, reasoning_internal = _split_by_internal_origin(reasoning_rule_raw)
    foundation += mechanism_external + reasoning_external

    fts_items_by_id = {r["knowledge_item"]["knowledge_item_id"]: r["knowledge_item"] for r in ki_fts["results"]}
    known_ids = {i["knowledge_item_id"] for i in foundation + precedent + foundation_internal
                 + precedent_internal + mechanism_internal + reasoning_internal}
    for item_id, item in fts_items_by_id.items():
        if item_id in known_ids:
            continue
        target, target_internal = (precedent, precedent_internal) if item.get("knowledge_type") == "precedent" \
            else (foundation, foundation_internal)
        (target_internal if item.get("information_class") == "internal_expert_assessment" else target).append(item)

    # Phase4: internal_expert_knowledgeはKnowledge Item由来（Foundation/Reusableの内部Origin分）
    # とClaim由来（Report Upload等でinformation_class='internal_expert_assessment'として直接
    # 作成されたClaim）の2種類が混在するため、"kind"で判別できるようタグ付けする
    # （build_references/_digest_analysisはkindを見て分岐する。両者ともdictのspreadで元の
    # キーはすべて保持するため、knowledge_item_id/claim_idaccess自体は変更不要）。
    internal_expert_knowledge = [
        {"kind": "knowledge_item", **i}
        for i in foundation_internal + precedent_internal + mechanism_internal
        + reasoning_internal + case_findings
    ]

    observed_fact = [c for c in reference_claims if c.get("information_class") == "observed_fact"]
    actor_claim = [c for c in reference_claims if c.get("information_class") == "actor_claim"]
    external_assessment = [c for c in reference_claims if c.get("information_class") == "external_assessment"]
    internal_expert_claims = [c for c in reference_claims
                              if c.get("information_class") == "internal_expert_assessment"]
    known_claim_ids = {c["claim_id"] for c in reference_claims}
    for r in claims_fts["results"]:
        claim = r["claim"]
        if claim["claim_id"] in known_claim_ids:
            continue
        bucket = {"observed_fact": observed_fact, "actor_claim": actor_claim,
                  "external_assessment": external_assessment,
                  "internal_expert_assessment": internal_expert_claims}.get(claim.get("information_class"))
        if bucket is not None:
            bucket.append(claim)
    internal_expert_knowledge += [{"kind": "claim", **c} for c in internal_expert_claims]

    relationship = _resolve_relationships_assets_dependencies(client, entities)
    internal_report = _resolve_internal_reports(client, issue_ids)
    scenario_hypothesis = _resolve_scenario_hypotheses(client, issue_ids)

    retrieval = {
        "query_text": query_text,
        "issue_ids": issue_ids,
        "stakeholder": entities,
        "foundation": foundation,
        "precedent": precedent,
        "internal_expert_knowledge": internal_expert_knowledge,
        "observed_fact": observed_fact,
        "actor_claim": actor_claim,
        "external_assessment": external_assessment,
        "relationship": relationship,
        "internal_report": internal_report,
        "scenario_hypothesis": scenario_hypothesis,
        "contested": contested,  # Referenceには含めないが、Sufficiency/Assessmentの参考情報として保持
    }

    # Phase E: Access Policy Enforcement — Sufficiency判定より前に、必ずここでagent_sharing_policy
    # を適用する（§16: all knowledge→ACCESS FILTER→authorized knowledge→sufficiencyの順序を
    # 機械的に強制するため、retrieve_knowledgeの戻り値そのものをここで絞り込む。呼び出し側
    # （process_request/_process_user_question/handle_department_inquiry）がこの後sufficiency
    # を計算するため、ここで漏れなく効く）。
    #
    # geo_chat_ui（このダッシュボード自身の内部Chat機能）からの呼び出しのみ「地政学アプリ内部
    # 利用」としてagent_sharing_policyの対象外とする。それ以外は他Domain Agentからの外部呼び出し
    # （/api/v1/geo-intelligence/query）として扱う。Phase F: 認証済みcaller_identityがあれば
    # そちらを正とする（requester_app自己申告によるなりすまし対策、
    # geo_knowledge_access_policy.resolve_is_internal_caller参照）。
    is_internal_caller = geo_knowledge_access_policy.resolve_is_internal_caller(normalized)
    before_count = sum(len(retrieval[key]) for key in
                       ("foundation", "precedent", "contested", "internal_expert_knowledge"))
    retrieval = geo_knowledge_access_policy.filter_agent_accessible_retrieval_dict(
        retrieval, is_internal_caller=is_internal_caller,
        caller_domain=normalized.get("_authenticated_caller_domain"))
    after_count = sum(len(v) for v in (
        retrieval["foundation"], retrieval["precedent"], retrieval["contested"],
        retrieval["internal_expert_knowledge"]))
    # §18 Audit: "access_denied"（集計のみ、個別item単位のdenyログは出さない、§22コスト配慮）。
    geo_knowledge_access_policy.log_access_filter_summary(
        client, actor=normalized.get("requester_app") or "unknown", action="cross_agent_access_denied",
        total_before=before_count, total_after=after_count,
        context={"is_internal_caller": is_internal_caller, "request_id": normalized.get("request_id")})

    retrieval["retrieval_run_id"] = _record_retrieval_run(client, retrieval)
    return retrieval


def _record_retrieval_run(client, retrieval: dict) -> str:
    """既存geo_retrieval_runs/geo_retrieval_resultsのスキーマ・記録パターン（geo_search_service.py
    の_create_retrieval_run/_record_result/_complete_retrieval_runと同形）を踏襲し、Geo
    Intelligence Agentの一連の取得結果を1本のRunへ統合する。result_typeは既存CHECK制約
    （entity/relationship/knowledge_item/claim/event/observation）の範囲に収め、対応するもの
    が無いinternal_report/scenario_hypothesisは記録対象外とする（Phase3で要検討）。"""
    run = client.insert("geo_retrieval_runs", [{
        "query_text": retrieval["query_text"], "filters": {"source": "geo_intelligence_agent"},
        "retrieval_mode": "hybrid_fts",
    }])[0]
    run_id = run["retrieval_run_id"]

    rank = 0
    for entity in retrieval["stakeholder"]:
        rank += 1
        client.insert("geo_retrieval_results", [{
            "retrieval_run_id": run_id, "result_type": "entity", "result_id": entity["entity_id"],
            "rank": rank, "inclusion_reason": "matched_query", "used_in_final_context": False,
        }], prefer="return=minimal")
    internal_expert_ki, internal_expert_claims = _split_internal_expert_knowledge(retrieval)
    for item in (retrieval["foundation"] + retrieval["precedent"] + internal_expert_ki):
        rank += 1
        client.insert("geo_retrieval_results", [{
            "retrieval_run_id": run_id, "result_type": "knowledge_item",
            "result_id": item["knowledge_item_id"], "rank": rank,
            "inclusion_reason": "matched_query", "used_in_final_context": False,
        }], prefer="return=minimal")
    for claim in (retrieval["observed_fact"] + retrieval["actor_claim"] + retrieval["external_assessment"]
                  + internal_expert_claims):
        rank += 1
        client.insert("geo_retrieval_results", [{
            "retrieval_run_id": run_id, "result_type": "claim", "result_id": claim["claim_id"],
            "rank": rank, "inclusion_reason": "matched_query", "used_in_final_context": False,
        }], prefer="return=minimal")
    for rel in retrieval["relationship"]:
        rank += 1
        id_field = {"relationship": "relationship_id", "asset": "asset_id", "dependency": "dependency_id"}[rel["kind"]]
        client.insert("geo_retrieval_results", [{
            "retrieval_run_id": run_id, "result_type": "relationship", "result_id": rel[id_field],
            "rank": rank, "inclusion_reason": "matched_query", "used_in_final_context": False,
        }], prefer="return=minimal")

    client.update("geo_retrieval_runs", {"retrieval_run_id": f"eq.{run_id}"}, {
        "completed_at": datetime.now(timezone.utc).isoformat(), "retrieved_count": rank,
    })
    return run_id


_ALL_CATEGORIES = ("foundation", "precedent", "internal_expert_knowledge", "observed_fact",
                   "actor_claim", "external_assessment", "stakeholder", "relationship",
                   "internal_report", "scenario_hypothesis")


def _missing_categories(retrieval: dict) -> list:
    return [c for c in _ALL_CATEGORIES if not retrieval.get(c)]


def assess_sufficiency(retrieval: dict, normalized: dict) -> str:
    """Knowledge Sufficiency Evaluator（Phase2、Phase4でinsufficient判定の過剰な足切りを修正）。
    単純な件数だけでなく以下を総合判定する:
    (1)Referenceが1件も無ければinsufficient、(2)analysis_requestedのカバー
    [stakeholder+relationship+実質的Knowledgeの3点が揃って初めてsufficient]、
    (3)鮮度[既存geo_freshness_service.classify_knowledge_freshnessを再利用]、
    (4)Stakeholder/Dynamics情報の有無。対象Country/Region・Issue/ThemeのEntity解決自体は
    必須条件にしない（FTS経由で関連Claimが見つかっていればEntity未登録でもinsufficientには
    しない。実DB E2Eで確認した是正）。"""
    has_stakeholder = bool(retrieval["stakeholder"])
    has_dynamics = bool(retrieval["relationship"])
    substantive = retrieval["foundation"] + retrieval["precedent"] + retrieval["internal_expert_knowledge"]
    has_substantive_knowledge = bool(substantive)
    claim_count = (len(retrieval["observed_fact"]) + len(retrieval["actor_claim"])
                   + len(retrieval["external_assessment"]))
    total_references = (len(retrieval["stakeholder"]) + len(retrieval["relationship"]) + len(substantive)
                        + claim_count + len(retrieval["internal_report"]))

    # Referenceが1件も無ければ無条件でinsufficient。Entity/Issueが未登録でも、FTS経由で
    # 関連Claimが実際に見つかっていれば「関連Knowledgeがほぼ存在しない」とまでは言えないため、
    # Entity/Issue未登録だけを理由にinsufficientへ強制しない（実DB E2Eで、Entity未登録の国に
    # ついて関連Reportが正しく見つかるのにinsufficient扱いになる不整合を確認して修正）。
    # 対象Country/RegionのKnowledge有無・Issue/ThemeのKnowledge有無は、以降のsufficient判定
    # （analysis_requested時のstakeholder/dynamics要件）でhas_stakeholder/has_dynamicsとして
    # 引き続き考慮する。
    if total_references == 0:
        return "insufficient"

    fresh_items = [item for item in substantive
                   if geo_freshness_service.classify_knowledge_freshness(item)
                   in ("current", "review_due", "unknown")]
    all_stale = bool(substantive) and not fresh_items

    if normalized["analysis_requested"]:
        # 高度な分析を要求している場合、Stakeholder/Dynamics/実質的Knowledgeが揃って初めて
        # sufficientとする（検索が1件ヒットしただけでsufficientにしない）
        if has_stakeholder and has_dynamics and has_substantive_knowledge and not all_stale:
            return "sufficient"
        return "partial"

    # 分析不要（事実確認レベル）の場合はClaim/Knowledgeがあればsufficientとする
    if (claim_count > 0 or has_substantive_knowledge) and not all_stale:
        return "sufficient"
    return "partial"


def assess_confidence(retrieval: dict, sufficiency: str) -> str:
    """Confidence（high/medium/low）はKnowledge Sufficiencyとは独立した軸として算出する
    （sufficient+medium、partial+high、insufficient+low等、あらゆる組合せが成立する）。"""
    if sufficiency == "insufficient":
        return "low"
    substantive = retrieval["foundation"] + retrieval["precedent"] + retrieval["internal_expert_knowledge"]
    if substantive and retrieval["stakeholder"] and retrieval["relationship"]:
        return "high"
    if substantive or retrieval["observed_fact"] or retrieval["external_assessment"] or retrieval["actor_claim"]:
        return "medium"
    return "low"


def _resolve_source_names(client, source_ids: list) -> dict:
    source_ids = sorted({s for s in source_ids if s})
    if not source_ids:
        return {}
    rows = client.select("geo_source_registry", {
        "select": "source_id,name", "source_id": f"in.({','.join(source_ids)})",
    })
    return {r["source_id"]: r["name"] for r in rows}


def _split_internal_expert_knowledge(retrieval: dict) -> tuple:
    """internal_expert_knowledgeはKnowledge Item由来（kind='knowledge_item'）とClaim由来
    （kind='claim'、Phase4のReport Upload等）が混在するため、参照時に分けて扱う。"""
    items = retrieval["internal_expert_knowledge"]
    return ([i for i in items if i.get("kind") == "knowledge_item"],
            [i for i in items if i.get("kind") == "claim"])


def build_references(client, retrieval: dict) -> list:
    """既存のKnowledge/Claim/Documentのみを引用する（存在しない項目を捏造しない）。
    knowledge_item/claim/internal_reportをReferenceとして提示し、stakeholder/relationshipは
    構造情報としてkey_stakeholders/political_dynamicsへ直接反映する（Referenceには含めない）。"""
    internal_expert_ki, internal_expert_claims = _split_internal_expert_knowledge(retrieval)
    all_claims = (retrieval["observed_fact"] + retrieval["actor_claim"] + retrieval["external_assessment"]
                  + internal_expert_claims)
    source_names = _resolve_source_names(client, [c.get("source_id") for c in all_claims])

    references = []
    for item in retrieval["foundation"] + retrieval["precedent"] + internal_expert_ki:
        references.append({
            "reference_id": item["knowledge_item_id"], "reference_type": item.get("knowledge_type"),
            "title": item.get("title"), "source": None,
            "date": item.get("as_of_date") or item.get("updated_at"),
            "internal_external": "internal" if item.get("origin") == "internal_expert_review" else "external",
        })
    for claim in all_claims:
        text = claim.get("claim_text") or ""
        references.append({
            "reference_id": claim["claim_id"], "reference_type": claim.get("information_class"),
            "title": text[:120] + ("…" if len(text) > 120 else ""),
            "source": source_names.get(claim.get("source_id")),
            "date": claim.get("valid_from") or claim.get("created_at"),
            "internal_external": "internal" if claim.get("information_class") == "internal_expert_assessment"
                                  else "external",
        })
    for doc in retrieval["internal_report"]:
        references.append({
            "reference_id": doc["document_id"], "reference_type": "internal_report",
            "title": doc.get("title"), "source": source_names.get(doc.get("source_id")),
            "date": doc.get("published_at") or doc.get("fetched_at"), "internal_external": "internal",
        })
    return references


def _digest_analysis(retrieval: dict) -> dict:
    """Expert AIを呼ばない場合の回答生成。LLMを使わず、取得したKnowledgeをそのまま要約提示
    するに留める（推測で埋めない。insufficient/partialで内容を捏造しないための既定経路）。"""
    stakeholders = [e.get("canonical_name") for e in retrieval["stakeholder"]]

    dynamics_lines = []
    for r in retrieval["relationship"]:
        if r["kind"] == "relationship":
            dynamics_lines.append(f"- {r.get('relationship_type')}: {r.get('description') or ''}")
        else:
            label = r.get("asset_type") or r.get("dependency_type") or ""
            dynamics_lines.append(f"- [{r['kind']}] {label}: {r.get('description') or ''}")

    internal_expert_ki, internal_expert_claims = _split_internal_expert_knowledge(retrieval)
    substantive = retrieval["foundation"] + retrieval["precedent"] + internal_expert_ki
    assessment_lines = [f"- [{item.get('knowledge_type')}] {item.get('title')}: {item.get('summary') or ''}"
                        for item in substantive]
    assessment_lines += [f"- [internal_expert_assessment] {c.get('claim_text')}"
                         for c in internal_expert_claims]

    if not assessment_lines and not dynamics_lines and not stakeholders:
        geo_assessment = "関連するKnowledgeが見つからなかったため、Assessmentは生成していません。"
    else:
        geo_assessment = "既存Knowledge DBから取得した情報の要約です（Expert AIによる分析は行っていません）。"
        if assessment_lines:
            geo_assessment += "\n" + "\n".join(assessment_lines)

    return {
        "geo_assessment": geo_assessment,
        "key_stakeholders": stakeholders,
        "political_dynamics": "\n".join(dynamics_lines) if dynamics_lines else None,
        "outlook": None,
        "cross_domain_implications": None,
        "expert_response_id": None,
    }


def should_call_expert_ai(sufficiency: str, normalized: dict, azure_client) -> bool:
    """Expert AI呼び出し条件（コード上で明示的に分離。Phase3以降の調整もこの関数のみで完結する）:
    Knowledgeがsufficient、かつ呼び出し元が高度な分析を要求(analysis_requested=True)している
    場合のみ既存Expert AIを呼ぶ。Knowledge不足(partial/insufficient)をExpert AIの推測で
    穴埋めすることは禁止（呼ばない）。azure_client未設定（テスト・未接続環境）でも呼ばない。"""
    return sufficiency == "sufficient" and normalized["analysis_requested"] and azure_client is not None


def resolve_single_theme_id(client, issue_ids: list) -> str | None:
    """解決したissue_idsが単一Themeに属する場合のみそのtheme_idを使う（既存build_source_pack
    へ渡すtheme_idの精度を上げるための補助。複数Themeにまたがる場合は敢えて選ばずNone）。"""
    if not issue_ids:
        return None
    issues = client.select("geo_issues", {"select": "theme_id", "issue_id": f"in.({','.join(issue_ids)})"})
    theme_ids = {i["theme_id"] for i in issues if i.get("theme_id")}
    return theme_ids.pop() if len(theme_ids) == 1 else None


def _run_expert_ai_and_shape(client, azure_client, model: str, mode: str, theme_id: str | None,
                              query_text: str, *, profile: str = "standard") -> dict:
    """既存geo_expert_engine.run_expert_request_with_mode（一括方式/段階実行方式を
    GEO_EXPERT_EXECUTION_MODE環境変数で切替可能）を呼び、Geo Intelligence Responseの形へ
    整形する共通コア。_call_expert_ai（user_question）と_weekly_maybe_enrich_with_expert_ai
    （weekly_monitoringの上位Itemのみ）の両方から使う（Geo Intelligence専用の推論エンジンは
    作らない、既存Expert AIをそのまま利用する）。

    profile: v1.2段階実行方式でのみ意味を持つ（quick/standard/deep）。本関数が消費する
    フィールド（one_line_assessment/stakeholders/dynamics/main_scenario.description/
    business_impact）はすべてStage1所有のため、quickでも欠落しない。"""
    result = geo_expert_engine.run_expert_request_with_mode(
        client, azure_client, model, mode, theme_id, query_text, None, case_id=None, profile=profile)
    response_json = result["response"]["response_json"]

    business_impact = response_json.get("business_impact") or {}
    implication_parts = [p for p in (business_impact.get("indirect"), business_impact.get("environment_change")) if p]
    cross_domain_implications = None
    if implication_parts:
        cross_domain_implications = (
            "※以下はPolitical Dynamics観点での影響経路の記述であり、Sustainability側の最終的な"
            "対応方針を断定するものではありません（それは別途Sustainability AI側で判断されます）。\n"
            + "\n".join(implication_parts)
        )

    main_scenario = response_json.get("main_scenario") or {}
    return {
        "geo_assessment": response_json.get("one_line_assessment"),
        "key_stakeholders": [s.get("actor") for s in (response_json.get("stakeholders") or []) if s.get("actor")],
        "political_dynamics": response_json.get("dynamics"),
        "outlook": main_scenario.get("description"),
        "cross_domain_implications": cross_domain_implications,
        "expert_response_id": result["response"]["response_id"],
    }


def _call_expert_ai(client, azure_client, model: str, normalized: dict, retrieval: dict) -> dict:
    mode = EXPERT_MODE_BY_REQUEST_TYPE.get(normalized["request_type"], "case_analysis")
    theme_id = resolve_single_theme_id(client, retrieval["issue_ids"])
    # Actor Intelligence/Geo Chat Bridge: retrieval（_build_query_text）はquestion単体のまま使う
    # （contextを混ぜるとFTS/Entity解決の精度が落ちるため）。Expert AIへの依頼文にのみ、
    # 既にRetrieval完了後のこの時点でcontextを付加する（Theme Context・Conversation Context等、
    # 呼び出し元がEvidenceではなく参考情報として渡したいものをここへ入れる）。
    query_text = normalized["question"]
    if normalized.get("context"):
        query_text = f"{query_text}\n\n{normalized['context']}"
    return _run_expert_ai_and_shape(client, azure_client, model, mode, theme_id, query_text)


def generate_analysis(client, normalized: dict, retrieval: dict, sufficiency: str, confidence: str, *,
                       azure_client=None, model: str = None) -> dict:
    """回答生成。sufficient かつ analysis_requested かつ azure_client利用可能な場合のみ既存
    Expert AIを呼ぶ（should_call_expert_ai）。それ以外は取得したKnowledgeをそのまま提示する
    _digest_analysisへフォールバックする（推測で埋めない）。"""
    expert_ai_called = should_call_expert_ai(sufficiency, normalized, azure_client)
    if expert_ai_called:
        analysis = _call_expert_ai(client, azure_client, model, normalized, retrieval)
    else:
        analysis = _digest_analysis(retrieval)

    analysis["references"] = build_references(client, retrieval)
    analysis["confidence"] = confidence
    # Geo Chat Bridge向け: 実際にExpert AIブランチを通ったかどうかを、sufficiencyからの
    # 推測ではなく実行結果として呼び出し元へ返す（Escalation条件自体は変更しない）。
    analysis["expert_ai_called"] = expert_ai_called
    return analysis


def _finalize_response(client, *, request_id: str, response: dict, retrieval_run_id: str | None,
                        expert_response_id: str | None, actor_id: str, started: float) -> dict:
    """既存geo_intelligence_requests/geo_intelligence_responsesへの保存を、user_question/
    weekly_monitoring共通で行う（Weekly専用のログ基盤は新設しない。item29準拠）。
    API logging / latency logging（item10/34）はここへ集約する。"""
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    response_row = {
        "request_id": request_id,
        "response_payload": response,
        "knowledge_sufficiency": response["knowledge_sufficiency"],
        "confidence": response.get("confidence"),
        "retrieval_run_id": retrieval_run_id,
        "expert_response_id": expert_response_id,
    }
    inserted_response = client.insert("geo_intelligence_responses", [response_row])[0]

    client.update("geo_intelligence_requests", {"request_id": f"eq.{request_id}"}, {
        "status": response["status"], "completed_at": response["completed_at"],
    })
    geo_audit.log_audit(client, actor=actor_id, action="geo_intelligence_response_create",
                         target_table="geo_intelligence_responses",
                         target_id=inserted_response["response_id"],
                         detail={"request_id": request_id, "status": response["status"],
                                 "knowledge_sufficiency": response["knowledge_sufficiency"],
                                 "latency_ms": latency_ms})
    return response


def process_request(client, request: dict, *, actor_id: str = "geo_intelligence_agent",
                     azure_client=None, model: str = None, caller_identity=None) -> dict:
    """Geo Intelligence Agentのエントリポイント。normalize/idempotency/Request行保存は
    request_type共通で行い、以降の処理はrequest_typeごとに独立した関数へ分岐する
    （item32: 巨大な1関数へ条件分岐を追加し続けない）:
        user_question（source_department未指定） → _process_user_question（既存、無変更）
        user_question（source_department指定） → geo_department_intelligence_service.
            handle_department_inquiry（Phase B-B: 他部門Department Intelligence AIからの
            Cross-Department Inquiry。既存パイプラインのビルディングブロックのみを再利用し、
            _process_user_question自体は無変更のまま並行して存在する）
        weekly_monitoring → _process_weekly_monitoring（新規）
    Failure Isolation: パイプライン内で例外が起きても呼び出し元（API層）へは伝播させず、
    status='failed'のResponseとして記録・返却する（外部アプリへ不明瞭な500を返さないため）。
    azure_client/modelは既存geo_expert_engine.run_expert_request呼び出し用（未指定ならExpert AI
    は呼ばれない。API層はai_client.make_openai_clientの結果をそのまま渡す）。
    Idempotency: 呼び出し元が同一request_idを指定して再送した場合（ネットワーク再送等）、
    既に処理済みのResponseがあればパイプラインを再実行せずそのまま返す。"""
    normalized = normalize_request(request)
    request_id = normalized["request_id"]

    # Phase F: caller_identity（geo_intelligence_auth.verify_api_key経由で認証済みの呼び出し元）
    # が渡された場合、Access Policyの判定根拠はこちらを正とし、Body側の自己申告
    # requester_app/source_departmentは無視する（§4, §7: requester_app=geo_chat_ui等への
    # なりすましでの権限昇格を防ぐ）。caller_identity未指定（内部プロセス内呼び出し等、
    # HTTP境界を経由しない呼び出し）の場合は既存どおりrequester_appの自己申告を使う
    # （既存テスト・dev-no-authモードとの後方互換）。
    normalized["requester_app_self_reported"] = normalized.get("requester_app")
    normalized["_caller_authenticated"] = bool(caller_identity is not None and caller_identity.authenticated)
    normalized["_authenticated_caller_domain"] = (
        caller_identity.caller_domain if normalized["_caller_authenticated"] else None)
    if normalized["_caller_authenticated"]:
        effective_app = caller_identity.caller_app or (
            f"authenticated:{caller_identity.caller_domain}" if caller_identity.caller_domain
            else "authenticated:unknown_domain")
        if effective_app != normalized.get("requester_app"):
            geo_audit.log_audit(
                client, actor=effective_app, action="cross_agent_caller_identity_mismatch",
                target_table="geo_intelligence_requests", target_id=request_id,
                detail={"payload_requester_app": normalized.get("requester_app"),
                        "authenticated_caller_app": effective_app,
                        "authenticated_caller_domain": caller_identity.caller_domain})
        normalized["requester_app"] = effective_app
        if caller_identity.caller_domain:
            normalized["source_department"] = caller_identity.caller_domain

    existing_request = client.select("geo_intelligence_requests", {
        "select": "status", "request_id": f"eq.{request_id}", "limit": "1"})
    if existing_request and existing_request[0]["status"] in ("completed", "partial", "failed"):
        existing_response = client.select("geo_intelligence_responses", {
            "select": "response_payload", "request_id": f"eq.{request_id}",
            "order": "created_at.desc", "limit": "1"})
        if existing_response:
            return existing_response[0]["response_payload"]

    request_row = {
        "request_id": request_id,
        "requester_app": normalized["requester_app"],
        "request_type": normalized["request_type"],
        "question": normalized["question"],
        # weekly_monitoringはpriority_geographies/sustainability_themesを既存country_region/
        # themes列へ流用する（新規列を追加しない。既存Dashboardの一覧・フィルタもそのまま使える）
        "country_region": normalized["country_region"] or normalized["priority_geographies"],
        "themes": normalized["themes"] or normalized["sustainability_themes"],
        "context": normalized["context"] or normalized["business_context"],
        "time_horizon": normalized["time_horizon"],
        "request_payload": normalized,
        "status": "received",
    }
    client.insert("geo_intelligence_requests", [request_row], prefer="return=minimal")
    geo_audit.log_audit(client, actor=actor_id, action="geo_intelligence_request_create",
                         target_table="geo_intelligence_requests", target_id=request_id,
                         detail={"requester_app": normalized["requester_app"],
                                 "request_type": normalized["request_type"]})

    if normalized["request_type"] == "weekly_monitoring":
        return _process_weekly_monitoring(client, normalized, actor_id=actor_id,
                                          azure_client=azure_client, model=model)
    if normalized["request_type"] == "user_question" and normalized.get("source_department"):
        # 循環import回避のための遅延import（geo_department_intelligence_serviceは本モジュールの
        # retrieve_knowledge等を再利用するため、トップレベルでは互いを参照できない）。
        import geo_department_intelligence_service
        return geo_department_intelligence_service.handle_department_inquiry(
            client, normalized, actor_id=actor_id, azure_client=azure_client, model=model)
    return _process_user_question(client, normalized, actor_id=actor_id,
                                  azure_client=azure_client, model=model)


def _process_user_question(client, normalized: dict, *, actor_id: str, azure_client=None, model: str = None) -> dict:
    """既存user_question処理（Phase2〜4、無変更のままprocess_requestから切り出したもの）。"""
    request_id = normalized["request_id"]
    completed_at = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()
    try:
        retrieval = retrieve_knowledge(client, normalized)
        sufficiency = assess_sufficiency(retrieval, normalized)
        confidence = assess_confidence(retrieval, sufficiency)
        analysis = generate_analysis(client, normalized, retrieval, sufficiency, confidence,
                                      azure_client=azure_client, model=model)
        status = "completed" if normalized["analysis_requested"] else "partial"
        retrieval_run_id = retrieval["retrieval_run_id"]
        missing_categories = _missing_categories(retrieval)
        knowledge_gap = None
        if sufficiency != "sufficient":
            gap_result = geo_knowledge_gap_service.record_external_request_gap(
                client, request_id=request_id, normalized=normalized, sufficiency=sufficiency,
                missing_categories=missing_categories, actor_id=actor_id)
            if gap_result:
                knowledge_gap = {**gap_result, "missing_categories": missing_categories,
                                 "note": "AIはKnowledge不足を推測で補完せず、地政学担当者向けの"
                                         "Intelligence Needとして記録した。"}
            else:
                # partialだが軽微な不足のみと判定され、Gapを登録しなかった場合
                knowledge_gap = {"exists": False, "gap_id": None, "description": None,
                                  "missing_categories": missing_categories,
                                  "note": "回答品質上重要な不足ではないと判定したため、"
                                          "Intelligence Needの登録は行っていない。"}
        error = None
    except Exception as e:  # noqa: BLE001 — Failure Isolation: 内部失敗を外部へ不明瞭な500で返さない
        status = "failed"
        sufficiency = "insufficient"
        confidence = "low"
        retrieval_run_id = None
        knowledge_gap = {"exists": False, "gap_id": None, "description": None,
                          "missing_categories": list(_ALL_CATEGORIES),
                          "note": "内部処理エラーのためKnowledgeを取得できませんでした"
                                  "（Intelligence Needの登録は行っていない）。"}
        error = {"code": type(e).__name__, "message": str(e)}
        analysis = {
            "geo_assessment": f"Geo Intelligence Agent内部処理でエラーが発生しました: {type(e).__name__}: {e}",
            "key_stakeholders": [], "political_dynamics": None, "outlook": None,
            "cross_domain_implications": None, "references": [], "expert_response_id": None,
            "expert_ai_called": False,
        }

    response = {
        "request_id": request_id,
        "status": status,
        "geo_assessment": analysis.get("geo_assessment"),
        "key_stakeholders": analysis.get("key_stakeholders") or [],
        "political_dynamics": analysis.get("political_dynamics"),
        "outlook": analysis.get("outlook"),
        "cross_domain_implications": analysis.get("cross_domain_implications"),
        "references": analysis.get("references") or [],
        "confidence": confidence,
        "knowledge_sufficiency": sufficiency,
        "knowledge_gap": knowledge_gap,
        "expert_ai_called": bool(analysis.get("expert_ai_called")),
        "error": error,
        "completed_at": completed_at,
    }
    return _finalize_response(client, request_id=request_id, response=response,
                              retrieval_run_id=retrieval_run_id,
                              expert_response_id=analysis.get("expert_response_id"),
                              actor_id=actor_id, started=started)


# =========================================================================
# weekly_monitoring: 週次横断探索（Sustainability週次モニタリング向け）
#
# user_question（特定の問いへの回答）とは処理モードを明確に分ける（item3）。
# Scope Normalization → Geo Knowledge/Report Retrieval → Sustainability Relevance
# Evaluation → Dedup → Ranking → 上位Itemのみ必要ならExpert AI → Response、という
# パイプラインを、既存Retrieval機能（geo_retrieval_service/geo_search_service）の
# 呼び出し方を変えるだけで実現する（新しい検索ロジックは実装しない）。
# =========================================================================

# item10/11: Sustainability実質関連性の判定・テーマMappingは決定的なキーワード照合に留める
# （候補全件へLLM分類をかけるとExpert AI呼び出しコストの原則(item24〜26)に反するため）。
_RELEVANCE_SIGNAL_TERMS = (
    "policy", "regulat", "law", "legislat", "trade", "tariff", "sanction", "export", "import",
    "resource", "raw material", "commodity", "instability", "unrest", "election", "coup",
    "economic security", "supply chain", "conflict", "security", "military", "war",
    "state-owned", "nationaliz", "cross-border", "water", "climate", "emission", "biodiversity",
    "human rights", "政策", "規制", "法", "貿易", "関税", "制裁", "輸出", "輸入", "資源", "原材料",
    "政情不安", "選挙", "クーデター", "経済安全保障", "サプライチェーン", "紛争", "治安", "軍事",
    "戦争", "国有化", "越境", "水", "気候", "排出", "生物多様性", "人権",
)

_SUSTAINABILITY_THEME_KEYWORDS = {
    "Water": ("water", "groundwater", "irrigation", "水", "地下水", "灌漑", "取水"),
    "Climate / GHG": ("climate", "emission", "carbon", "ghg", "気候", "排出", "温室効果ガス"),
    "Packaging": ("packaging", "plastic", "包装", "プラスチック"),
    "Raw Materials": ("raw material", "resource", "mining", "commodity", "原材料", "資源", "鉱業"),
    "Biodiversity": ("biodiversity", "ecosystem", "生物多様性", "生態系"),
    "Human Rights": ("human rights", "labor", "labour", "人権", "労働"),
    "Health": ("health", "healthcare", "健康", "衛生"),
    "Human Capital": ("employment", "workforce", "雇用", "人材"),
    "Responsible Marketing": ("marketing", "advertising", "マーケティング", "広告"),
    "Disclosure": ("disclosure", "reporting", "開示", "報告"),
    "ESG / Sustainable Finance": ("esg", "sustainable finance", "green bond", "サステナブルファイナンス"),
}


def _weekly_disabled_response(normalized: dict) -> dict:
    """Kill Switch（item35）。falseの場合はweekly_monitoring Requestへ明確なdisabled応答を
    返す（user_questionには一切影響させない）。"""
    return {
        "request_id": normalized["request_id"], "status": "failed",
        "geo_assessment": None, "key_stakeholders": [], "political_dynamics": None, "outlook": None,
        "cross_domain_implications": None, "references": [], "confidence": "low",
        "knowledge_sufficiency": "insufficient", "knowledge_gap": None,
        "error": {"code": "weekly_monitoring_disabled",
                  "message": "weekly_monitoringは現在無効化されています（GEO_WEEKLY_MONITORING_ENABLED）。"},
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "period_start": normalized["period_start"], "period_end": normalized["period_end"],
        "relevant_intelligence": None,
    }


def _parse_date_str(value) -> str | None:
    """value（date/timestamp文字列）からYYYY-MM-DD部分だけを取り出す。ISO8601形式なら文字列の
    辞書順比較がそのまま日付順比較になるため、これで十分（タイムゾーンライブラリは導入しない）。"""
    if not value:
        return None
    s = str(value)
    return s[:10] if len(s) >= 10 else None


def _within_period(date_value, period_start: str | None, period_end: str | None) -> bool:
    """item9: event_date/publication date/as_of等の意味のある日時で判定する。日時情報が
    存在しないKnowledgeを勝手に「今週」扱いしない（Noneは常にFalse）。"""
    d = _parse_date_str(date_value)
    if d is None:
        return False
    if period_start and d < period_start:
        return False
    if period_end and d > period_end:
        return False
    return True


def _normalize_candidate(*, kind: str, geography: str, raw: dict) -> dict | None:
    """Knowledge Item/Claim/Documentという異種のRetrieval結果を、週次探索で共通に扱える形へ
    正規化する（date/text/reference情報を統一フォーマットに揃えるのみで、内容は一切生成しない）。"""
    if kind == "knowledge_item":
        date = raw.get("as_of_date") or raw.get("last_confirmed_at") or raw.get("updated_at")
        title = raw.get("title") or ""
        text = f"{title} {raw.get('summary') or ''}".strip()
        id_key = ("knowledge_item", raw["knowledge_item_id"])
        reference = {"reference_id": raw["knowledge_item_id"], "reference_type": raw.get("knowledge_type"),
                     "title": title, "source": None, "date": date}
    elif kind == "claim":
        date = raw.get("valid_from") or raw.get("created_at")
        text = raw.get("claim_text") or ""
        title = text[:120] + ("…" if len(text) > 120 else "")
        id_key = ("claim", raw["claim_id"])
        reference = {"reference_id": raw["claim_id"], "reference_type": raw.get("information_class"),
                     "title": title, "source": None, "date": date}
    elif kind == "document":
        date = raw.get("published_at") or raw.get("fetched_at")
        title = raw.get("title") or ""
        text = title
        id_key = ("document", raw["document_id"])
        reference = {"reference_id": raw["document_id"], "reference_type": "internal_report",
                     "title": title, "source": None, "date": date}
    else:
        return None
    if not text:
        return None
    return {"kind": kind, "geography": {geography}, "date": date, "title": title, "text": text,
            "raw": raw, "id_key": id_key, "reference": reference}


def _weekly_candidates_for_geography(client, geography: str, issue_ids: list) -> list:
    """1つのGeographyのissue_idsから、既存geo_retrieval_service（変更しない）で候補を取得する。
    Relationship/Asset/Dependency/Scenario/Hypothesisは日時を持たない構造情報のため、週次の
    「今週のIntelligence候補」プールには含めない（既存build_case_context同様の区分。必要なら
    Expert AI enrichment時にcontextとして別途使う）。"""
    if not issue_ids:
        return []
    foundation_raw = geo_retrieval_service.get_foundation_knowledge(client, issue_ids)
    precedent_raw = geo_retrieval_service.get_relevant_precedents(client, issue_ids)
    case_findings = geo_retrieval_service.get_internal_expert_assessments(client, issue_ids)
    reference_claims = geo_retrieval_service.get_reference_claims_for_issue(client, issue_ids)
    internal_reports = _resolve_internal_reports(client, issue_ids)

    candidates = []
    for item in foundation_raw + precedent_raw + case_findings:
        c = _normalize_candidate(kind="knowledge_item", geography=geography, raw=item)
        if c:
            candidates.append(c)
    for claim in reference_claims:
        c = _normalize_candidate(kind="claim", geography=geography, raw=claim)
        if c:
            candidates.append(c)
    for doc in internal_reports:
        c = _normalize_candidate(kind="document", geography=geography, raw=doc)
        if c:
            candidates.append(c)
    return candidates


def _weekly_gather_candidates(client, normalized: dict) -> dict:
    """priority_geographiesの各地域についてEntity/Issueを解決し（既存resolve_entities_and_issues
    を地域ごとに呼ぶ）、既存Retrieval機能で候補を集める。地域ごとの解決可否（coverage）は
    Sufficiency判定に使う。"""
    geographies = normalized["priority_geographies"] or normalized["country_region"]
    candidates_by_key: dict = {}
    resolved_geography_count = 0
    for geography in geographies:
        resolved = resolve_entities_and_issues(client, {"country_region": [geography], "themes": []})
        if resolved["entities"] or resolved["issue_ids"]:
            resolved_geography_count += 1
        for c in _weekly_candidates_for_geography(client, geography, resolved["issue_ids"]):
            existing = candidates_by_key.get(c["id_key"])
            if existing:
                existing["geography"] |= c["geography"]
            else:
                candidates_by_key[c["id_key"]] = c
    return {
        "candidates": list(candidates_by_key.values()),
        "total_geography_count": len(geographies),
        "resolved_geography_count": resolved_geography_count,
    }


def _is_sustainability_relevant(candidate: dict) -> bool:
    """item10: Sustainabilityテーマへ実質的な影響があり得る地政学動向かどうかの決定的な判定
    （policy/trade/tariff/sanction/resource/instability/conflict等のキーワード照合）。"""
    text_lower = candidate["text"].lower()
    return any(term in text_lower for term in _RELEVANCE_SIGNAL_TERMS)


def _matched_sustainability_themes(candidate: dict, requested_themes: list) -> list:
    """item11: Sustainability側から渡されたテーマのうち、候補の内容と実際にキーワードが
    一致したものだけを返す（要求されていないテーマを勝手に付与しない）。"""
    text_lower = candidate["text"].lower()
    matched = []
    for theme in requested_themes:
        keywords = _SUSTAINABILITY_THEME_KEYWORDS.get(theme, (theme.lower(),))
        if any(kw.lower() in text_lower for kw in keywords):
            matched.append(theme)
    return matched


def _dedupe_candidates(candidates: list) -> list:
    """item21: 同一Eventが複数Report/Knowledgeに存在する場合、1 Intelligence Item + 複数
    Referenceへ統合する。Geography重複かつテキスト類似度が高いものを同一Eventとみなす
    （既存geo_knowledge_gap_service.text_similarityの簡易Jaccardを再利用、Embeddingは導入しない）。"""
    merged: list = []
    for c in candidates:
        target = None
        for m in merged:
            if (c["geography"] & m["geography"]) and geo_knowledge_gap_service.text_similarity(
                    c["text"], m["text"]) >= 0.5:
                target = m
                break
        if target:
            target["geography"] |= c["geography"]
            target["members"].append(c)
        else:
            merged_candidate = dict(c)
            merged_candidate["members"] = [c]
            merged.append(merged_candidate)
    return merged


def _candidate_confidence(merged: dict) -> str:
    """複数Sourceで裏付けられている（members>=2）ほどConfidenceを高くする、既存
    assess_confidenceと同じ考え方の簡易版。"""
    if len(merged["members"]) >= 2:
        return "high"
    if merged["kind"] in ("knowledge_item",):
        return "medium"
    return "medium" if merged["kind"] == "claim" else "low"


def _rank_candidates(merged_candidates: list, normalized: dict) -> list:
    """item22: 単純な検索scoreだけで決めず、Sustainability relevance・Priority geography・
    Recency・Potential impact(複数Sourceでの裏付け件数)・Confidenceを考慮する。"""
    geography_priority = {g: i for i, g in enumerate(normalized["priority_geographies"] or normalized["country_region"])}
    period_end = normalized["period_end"] or ""

    def _score(m: dict) -> float:
        recency = 1.0 if (m["date"] or "") >= period_end else 0.5 if m["date"] else 0.0
        geography_rank_values = [geography_priority.get(g, len(geography_priority)) for g in m["geography"]]
        best_geography_rank = min(geography_rank_values) if geography_rank_values else len(geography_priority)
        geography_score = 1.0 - (best_geography_rank / max(len(geography_priority), 1))
        corroboration_score = min(len(m["members"]), 5) / 5
        kind_weight = {"knowledge_item": 1.0, "claim": 0.6, "document": 0.8}.get(m["kind"], 0.5)
        return recency * 0.3 + geography_score * 0.3 + corroboration_score * 0.2 + kind_weight * 0.2

    return sorted(merged_candidates, key=_score, reverse=True)


def _candidate_to_item(merged: dict, normalized: dict) -> dict:
    references = []
    seen_ref_ids = set()
    for member in merged["members"]:
        ref = member["reference"]
        if ref["reference_id"] in seen_ref_ids:
            continue
        seen_ref_ids.add(ref["reference_id"])
        references.append(ref)

    matched_themes = _matched_sustainability_themes(merged, normalized["sustainability_themes"])
    why_relevant = (f"優先地域（{', '.join(sorted(merged['geography']))}）に関連し、"
                    f"{', '.join(matched_themes) if matched_themes else 'Sustainabilityの複数テーマ'}"
                    f"へ波及し得る地政学動向として検出された（{len(merged['members'])}件のKnowledge/"
                    f"Claim/Reportから統合）。")

    return {
        "item_id": f"{merged['kind']}:{merged['id_key'][1]}",
        "title": merged["title"] or merged["text"][:80],
        "event_date": merged["date"],
        "as_of": merged["date"],
        "country_region": sorted(merged["geography"]),
        "sustainability_themes": matched_themes,
        "geo_assessment": merged["text"],
        "why_relevant": why_relevant,
        "key_stakeholders": [],
        "political_dynamics": None,
        "outlook": None,
        "references": references,
        "confidence": _candidate_confidence(merged),
        "expert_response_id": None,
    }


# item24/25: Expert AIはTop N件・十分な裏付け（複数member）がある候補にのみ使う。
WEEKLY_EXPERT_AI_TOP_N = 3
WEEKLY_EXPERT_AI_MIN_MEMBERS = 2


def _weekly_maybe_enrich_with_expert_ai(client, items: list, normalized: dict, issue_ids: list,
                                        azure_client, model: str) -> list:
    """item24〜26: 全候補にExpert AIを実行しない。Political Dynamics/Outlook統合が必要な
    上位Item（複数Sourceで裏付けのあるもの）のみ、既存Expert AIで補強する。Knowledge不足の
    Itemを補強することはしない（そもそもTop Nに残るのは十分な裏付けがある候補のみ）。"""
    if not azure_client or not normalized["analysis_requested"]:
        return items
    theme_id = resolve_single_theme_id(client, issue_ids)
    enriched_count = 0
    for item in items:
        if enriched_count >= WEEKLY_EXPERT_AI_TOP_N:
            break
        if len(item["references"]) < WEEKLY_EXPERT_AI_MIN_MEMBERS:
            continue
        query_text = f"{item['title']}。{item['why_relevant']}"
        # v1.2: 実データ確認済み（response_id 7bdcef3e-f844-4997-947d-70561d87eb85、
        # docs/geopolitical-staged-execution-pilot-results.md参照）— 本関数が読む
        # geo_assessment/key_stakeholders/political_dynamics/outlook/expert_response_idは
        # すべてStage1所有フィールドのため、quickでも欠落・劣化しない。週次バックグラウンド
        # 経路はコスト・時間を優先しquickを使う。
        shaped = _run_expert_ai_and_shape(client, azure_client, model, "case_analysis", theme_id, query_text,
                                           profile="quick")
        item["geo_assessment"] = shaped["geo_assessment"] or item["geo_assessment"]
        item["political_dynamics"] = shaped["political_dynamics"]
        item["outlook"] = shaped["outlook"]
        item["expert_response_id"] = shaped["expert_response_id"]
        if not item["key_stakeholders"]:
            item["key_stakeholders"] = shaped["key_stakeholders"]
        enriched_count += 1
    return items


def _weekly_assess_sufficiency(gathered: dict, normalized: dict) -> str:
    """item17/18: weekly_monitoring向けSufficiency判定。
    insufficient: 指定Geographyのほとんどについて検索可能なKnowledgeが無い（coverage=0、
      または非常に低い）→ 判定自体の信頼性が不足。
    partial: 一部Geography/Themeについて解決できるが、全体を尽くせていない。
    sufficient: 指定期間のGeo Knowledgeを検索可能・大半のGeographyを評価できる。"""
    total = gathered["total_geography_count"]
    resolved = gathered["resolved_geography_count"]
    coverage = (resolved / total) if total else 0.0
    if total == 0 or coverage == 0.0:
        return "insufficient"
    if coverage < 0.5:
        return "partial"
    return "sufficient"


def _process_weekly_monitoring(client, normalized: dict, *, actor_id: str, azure_client=None,
                               model: str = None) -> dict:
    """weekly_monitoring処理本体（item6のパイプライン）。Scope Normalization→Geo Knowledge/
    Report Retrieval→Sustainability Relevance Evaluation→Dedup→Ranking→上位Itemのみ必要なら
    Expert AI→Response。"""
    request_id = normalized["request_id"]
    completed_at = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()

    if not weekly_monitoring_enabled():
        response = _weekly_disabled_response(normalized)
        return _finalize_response(client, request_id=request_id, response=response,
                                  retrieval_run_id=None, expert_response_id=None,
                                  actor_id=actor_id, started=started)

    try:
        gathered = _weekly_gather_candidates(client, normalized)
        all_candidates = gathered["candidates"]

        # item9: Periodでフィルタ（日時情報の無いKnowledgeを今週扱いにしない）
        period_candidates = [c for c in all_candidates
                             if _within_period(c["date"], normalized["period_start"], normalized["period_end"])]
        # item10: Sustainability実質関連性フィルタ
        relevant_candidates = [c for c in period_candidates if _is_sustainability_relevant(c)]

        merged = _dedupe_candidates(relevant_candidates)
        ranked = _rank_candidates(merged, normalized)
        max_items = weekly_monitoring_max_items()
        top_merged = ranked[:max_items]
        dropped_count = max(0, len(ranked) - max_items)

        items = [_candidate_to_item(m, normalized) for m in top_merged]

        # issue_idsの再解決（Expert AI enrichment用theme_id解決のため。Retrieval自体はやり直さない）
        all_issue_ids = sorted({
            issue_id
            for geography in (normalized["priority_geographies"] or normalized["country_region"])
            for issue_id in resolve_entities_and_issues(client, {"country_region": [geography], "themes": []})["issue_ids"]
        })
        items = _weekly_maybe_enrich_with_expert_ai(client, items, normalized, all_issue_ids, azure_client, model)

        sufficiency = _weekly_assess_sufficiency(gathered, normalized)
        confidence = "low" if sufficiency == "insufficient" else ("high" if items else "medium")

        # item19/20: 0件だけではGapを作らない。insufficient（Geo側Knowledge不足が明確）の
        # 場合のみGap登録する（partialでは登録しない、user_questionとは異なる方針）。
        knowledge_gap = None
        if sufficiency == "insufficient":
            missing_categories = list(_ALL_CATEGORIES)
            gap_normalized = {
                "question": normalized["question"], "country_region": normalized["priority_geographies"],
                "themes": normalized["sustainability_themes"], "analysis_requested": True,
                "context": normalized["business_context"], "requester_app": normalized["requester_app"],
                "request_type": normalized["request_type"],
            }
            gap_result = geo_knowledge_gap_service.record_external_request_gap(
                client, request_id=request_id, normalized=gap_normalized, sufficiency=sufficiency,
                missing_categories=missing_categories, actor_id=actor_id)
            if gap_result:
                knowledge_gap = {**gap_result, "missing_categories": missing_categories,
                                 "note": "指定Geographyの大半についてGeo側にKnowledgeが無く、"
                                         "週次探索の信頼性が不足しているため、地政学担当者向けの"
                                         "Intelligence Needとして記録した。"}
        if knowledge_gap is None:
            knowledge_gap = {"exists": False}

        status = "completed"
        error = None
    except Exception as e:  # noqa: BLE001 — Failure Isolation（既存原則と同様、weekly側でも維持）
        status = "failed"
        sufficiency = "insufficient"
        confidence = "low"
        items = []
        knowledge_gap = {"exists": False, "gap_id": None, "description": None,
                          "missing_categories": list(_ALL_CATEGORIES),
                          "note": "内部処理エラーのためweekly_monitoringを完了できませんでした。"}
        error = {"code": type(e).__name__, "message": str(e)}
        dropped_count = 0

    response = {
        "request_id": request_id,
        "status": status,
        "geo_assessment": None, "key_stakeholders": [], "political_dynamics": None, "outlook": None,
        "cross_domain_implications": None, "references": [],
        "confidence": confidence,
        "knowledge_sufficiency": sufficiency,
        "knowledge_gap": knowledge_gap,
        "error": error,
        "completed_at": completed_at,
        "period_start": normalized["period_start"],
        "period_end": normalized["period_end"],
        "relevant_intelligence": items,
        # item23: GEO_WEEKLY_MONITORING_MAX_ITEMSによる切り捨てをsilentにしない
        "truncated_count": dropped_count,
    }
    return _finalize_response(client, request_id=request_id, response=response,
                              retrieval_run_id=None, expert_response_id=None,
                              actor_id=actor_id, started=started)
