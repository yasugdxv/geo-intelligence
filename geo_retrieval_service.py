"""地政学ナレッジ＆インテリジェンスDB: Retrieval Layer（既存Source Packと新Foundation
Packの共通接続点、docs/geopolitical-knowledge-architecture.md 6章）。

Phase 1では geo_foundation_pack_service がこのモジュールを使用する。既存
geo_expert_engine.build_source_pack は変更しない。Phase2でgeo_expert_engineを
このRetrieval Layerへ移行する（同じget_live_source_packを経由させる）予定。

EntityとKnowledge Item/Relationshipの関連付けは、直接のFKではなく共通のIssue（issue_id）
タグ付けを介して行う（geo_entity_issues/geo_relationship_issues/geo_knowledge_item_issues/
geo_case_issuesがそれぞれ同じissue_idを共有することで、案件の対象Issueに関連する
Entity/Relationship/Knowledgeを横断的に取得できる設計）。
"""
import geo_expert_engine
import geo_relationship_service

FOUNDATION_KNOWLEDGE_TYPES = (
    "country_foundation", "actor_profile", "party_profile",
    "relationship_assessment", "historical_path_dependency",
)
REUSABLE_KNOWLEDGE_TYPES = ("precedent", "mechanism", "reasoning_rule", "indicator", "leverage_card")


def get_live_source_pack(client, theme_id: str, query_text: str) -> dict:
    """既存geo_expert_engine.build_source_packをそのまま呼ぶ（変更しない）。"""
    return geo_expert_engine.build_source_pack(client, theme_id, query_text)


def _knowledge_items_for_issues(client, issue_ids: list, knowledge_types: tuple,
                                 include_demo: bool = False) -> list:
    if not issue_ids:
        return []
    links = client.select("geo_knowledge_item_issues", {
        "select": "knowledge_item_id", "issue_id": f"in.({','.join(issue_ids)})",
    })
    item_ids = sorted({link["knowledge_item_id"] for link in links})
    if not item_ids:
        return []
    params = {
        "select": "*", "knowledge_item_id": f"in.({','.join(item_ids)})",
        # Phase2B-2: status（lifecycle）とretrieval_status（AIが参照してよいか）は独立した二軸。
        # ReusableとしてExpert Engine/検索へ渡すには両方を満たす必要がある（一軸への統合はしない）
        "status": "eq.active", "retrieval_status": "eq.available",
        "knowledge_type": f"in.({','.join(knowledge_types)})",
    }
    if not include_demo:
        params["is_demo"] = "eq.false"
    return client.select("geo_knowledge_items", params)


def get_foundation_knowledge(client, issue_ids: list, *, include_demo: bool = False) -> list:
    """active化されたcountry_foundation/actor_profile/party_profile/relationship_assessment/
    historical_path_dependencyを、Caseの対象Issueに紐づく範囲で取得する。"""
    return _knowledge_items_for_issues(client, issue_ids, FOUNDATION_KNOWLEDGE_TYPES, include_demo)


def get_reusable_knowledge(client, issue_ids: list, *, knowledge_types: tuple = None,
                            include_demo: bool = False) -> list:
    """precedent/mechanism/reasoning_rule/indicator/leverage_cardを取得する。"""
    types = knowledge_types or REUSABLE_KNOWLEDGE_TYPES
    return _knowledge_items_for_issues(client, issue_ids, types, include_demo)


def get_contested_knowledge(client, issue_ids: list, *, include_demo: bool = False) -> list:
    """statusがcontestedのKnowledge Item（対立する見方として明示するため別枠で返す）。
    引き続きReusable（active）とは別関数・別セクションとして取得する（一軸への統合はしない）。"""
    if not issue_ids:
        return []
    links = client.select("geo_knowledge_item_issues", {
        "select": "knowledge_item_id", "issue_id": f"in.({','.join(issue_ids)})",
    })
    item_ids = sorted({link["knowledge_item_id"] for link in links})
    if not item_ids:
        return []
    params = {"select": "*", "knowledge_item_id": f"in.({','.join(item_ids)})",
              "status": "eq.contested", "retrieval_status": "eq.available"}
    if not include_demo:
        params["is_demo"] = "eq.false"
    return client.select("geo_knowledge_items", params)


def get_internal_expert_assessments(client, issue_ids: list, *, include_demo: bool = False) -> list:
    """Phase2B-2: case_finding由来のInternal Expert Assessment（Case/時点依存の専門家判断）を取得する。
    Internal Expert Reasoning Rule（get_reusable_knowledgeのreasoning_rule/mechanism/precedent）とは
    明確に別物として別関数にする。status='active' AND retrieval_status='available'（二軸維持）に加え、
    information_class='internal_expert_assessment'でさらに絞り込む。

    地政学アプリClooseout Phase5: report_finding（Report Review Loop、Closeout Phase4）も
    case_findingと同じ「Case/Report・当該時点依存の専門家判断」の性質を持つため、この関数の
    対象へ含める（新しいRetrieval関数は作らず、既存フィルタの許容値を広げるだけ）。"""
    if not issue_ids:
        return []
    links = client.select("geo_knowledge_item_issues", {
        "select": "knowledge_item_id", "issue_id": f"in.({','.join(issue_ids)})",
    })
    item_ids = sorted({link["knowledge_item_id"] for link in links})
    if not item_ids:
        return []
    params = {"select": "*", "knowledge_item_id": f"in.({','.join(item_ids)})",
              "status": "eq.active", "retrieval_status": "eq.available",
              "knowledge_type": "in.(case_finding,report_finding)",
              "information_class": "eq.internal_expert_assessment"}
    if not include_demo:
        params["is_demo"] = "eq.false"
    return client.select("geo_knowledge_items", params)


def attach_case_finding_provenance(client, case_finding_items: list) -> list:
    """Phase2B-2: case_finding由来のInternal Expert Assessmentに、元Case・判断時点・Reviewerを
    付与する。derived_from_review_session_id→geo_expert_review_sessions.case_id→
    geo_cases.case_titleを解決し、各itemへoriginal_case_title/as_of_date/reviewed_byを追加する。
    いずれかが解決できない場合も、取得できた範囲でそのまま返す（欠落を理由に除外しない）。
    地政学アプリCleanup（P1-1）: item件数分の個別select（N+1）を避け、session_id集合→
    geo_expert_review_sessions一括select→case_id集合→geo_cases一括selectの2回で完結させる
    （挙動は変更しない、クエリ回数のみ削減）。"""
    session_ids = sorted({item["derived_from_review_session_id"] for item in case_finding_items
                          if item.get("derived_from_review_session_id")})
    case_id_by_session = {}
    if session_ids:
        sessions = client.select("geo_expert_review_sessions", {
            "select": "review_session_id,case_id", "review_session_id": f"in.({','.join(session_ids)})",
        })
        case_id_by_session = {s["review_session_id"]: s.get("case_id") for s in sessions
                              if s.get("case_id")}

    case_ids = sorted(set(case_id_by_session.values()))
    title_by_case = {}
    if case_ids:
        cases = client.select("geo_cases", {
            "select": "case_id,case_title", "case_id": f"in.({','.join(case_ids)})",
        })
        title_by_case = {c["case_id"]: c.get("case_title") for c in cases}

    result = []
    for item in case_finding_items:
        enriched = dict(item)
        enriched["as_of_date"] = item.get("as_of_date")
        enriched["reviewed_by"] = item.get("reviewed_by")
        session_id = item.get("derived_from_review_session_id")
        case_id = case_id_by_session.get(session_id) if session_id else None
        enriched["original_case_title"] = title_by_case.get(case_id) if case_id else None
        result.append(enriched)
    return result


def get_reference_claims_for_issue(client, issue_ids: list, *, information_classes: tuple = None,
                                    retrieval_status: str = "available", include_demo: bool = False) -> list:
    """Phase2B-2: geo_claim_issues経由でIssueに紐づくClaim（External Reference含む）を取得する。
    retrieval_status='available'既定（Source Reference Policy等で参照可能と判定されたもののみ）。
    information_classesでobserved_fact/actor_claim/external_assessment等を絞り込める。"""
    if not issue_ids:
        return []
    links = client.select("geo_claim_issues", {
        "select": "claim_id", "issue_id": f"in.({','.join(issue_ids)})",
    })
    claim_ids = sorted({link["claim_id"] for link in links})
    if not claim_ids:
        return []
    params = {"select": "*", "claim_id": f"in.({','.join(claim_ids)})"}
    if retrieval_status:
        params["retrieval_status"] = f"eq.{retrieval_status}"
    if information_classes:
        params["information_class"] = f"in.({','.join(information_classes)})"
    if not include_demo:
        params["is_demo"] = "eq.false"
    return client.select("geo_claims", params)


def get_candidate_knowledge(client, issue_ids: list) -> list:
    """statusがcandidate/in_reviewのKnowledge Item（未承認候補として本文と分けて表示するため）。
    デモの有無に関わらず候補は未承認なので常に取得する（表示側で明示的に分離する）。"""
    if not issue_ids:
        return []
    links = client.select("geo_knowledge_item_issues", {
        "select": "knowledge_item_id", "issue_id": f"in.({','.join(issue_ids)})",
    })
    item_ids = sorted({link["knowledge_item_id"] for link in links})
    if not item_ids:
        return []
    return client.select("geo_knowledge_items", {
        "select": "*", "knowledge_item_id": f"in.({','.join(item_ids)})",
        "status": "in.(candidate,in_review)",
    })


def get_case_context(client, case_id: str) -> dict:
    """案件のhypothesis/gap/claim一式を取得する。"""
    hypotheses = client.select("geo_case_hypotheses", {
        "select": "*", "case_id": f"eq.{case_id}", "order": "created_at.asc",
    })
    gaps = client.select("geo_knowledge_gaps", {
        "select": "*", "case_id": f"eq.{case_id}", "order": "created_at.asc",
    })
    claims = client.select("geo_claims", {
        "select": "*", "origin_type": "eq.case", "origin_id": f"eq.{case_id}",
    })
    return {"hypotheses": hypotheses, "gaps": gaps, "claims": claims}


def get_claims_and_evidence(client, *, entity_ids: list = None, case_id: str = None) -> list:
    """entity_ids（subject_entity_id基準）またはcase_idに関連するClaimと、それぞれのEvidenceを返す。"""
    claims = []
    if entity_ids:
        claims += client.select("geo_claims", {
            "select": "*", "subject_entity_id": f"in.({','.join(entity_ids)})",
        })
    if case_id:
        claims += client.select("geo_claims", {
            "select": "*", "origin_type": "eq.case", "origin_id": f"eq.{case_id}",
        })
    seen = set()
    result = []
    for claim in claims:
        if claim["claim_id"] in seen:
            continue
        seen.add(claim["claim_id"])
        evidence = client.select("geo_claim_evidence", {"select": "*", "claim_id": f"eq.{claim['claim_id']}"})
        result.append({"claim": claim, "evidence": evidence})
    return result


# ── Phase2A追加: geo_expert_engine統合用の個別アクセサ（3.1章） ───────────

def get_issue_ids_for_case(client, case_id: str) -> list:
    """Caseに紐づくissue_idsを取得する（Actor Intelligence Phase AI-2で新設。geo_expert_engine.
    build_case_context/geo_foundation_pack_service.build_foundation_packに既にある同一ロジックを
    1箇所に集約したヘルパーだが、既存2箇所の呼び出し自体は変更しない、新設のみ）。"""
    issue_links = client.select("geo_case_issues", {"select": "issue_id", "case_id": f"eq.{case_id}"})
    return sorted({link["issue_id"] for link in issue_links})


def get_relevant_entities(client, issue_ids: list, *, include_demo: bool = False) -> list:
    """Caseの対象Issueに紐づくEntityを取得する。"""
    if not issue_ids:
        return []
    links = client.select("geo_entity_issues", {"select": "entity_id", "issue_id": f"in.({','.join(issue_ids)})"})
    entity_ids = sorted({link["entity_id"] for link in links})
    if not entity_ids:
        return []
    params = {"select": "*", "entity_id": f"in.({','.join(entity_ids)})"}
    if not include_demo:
        params["is_demo"] = "eq.false"
    return client.select("geo_entities", params)


def get_relevant_relationships(client, issue_ids: list, *, include_demo: bool = False) -> list:
    """Caseの対象Issueに紐づくRelationshipを取得する。"""
    results = []
    for issue_id in issue_ids:
        results.extend(geo_relationship_service.list_relationships_for_issue(client, issue_id))
    if not include_demo:
        results = [r for r in results if not r.get("is_demo")]
    seen, deduped = set(), []
    for r in results:
        if r["relationship_id"] not in seen:
            seen.add(r["relationship_id"])
            deduped.append(r)
    return deduped


def get_relevant_precedents(client, issue_ids: list, *, include_demo: bool = False) -> list:
    return get_reusable_knowledge(client, issue_ids, knowledge_types=("precedent",), include_demo=include_demo)


def get_relevant_mechanisms(client, issue_ids: list, *, include_demo: bool = False) -> list:
    return get_reusable_knowledge(client, issue_ids, knowledge_types=("mechanism",), include_demo=include_demo)


def get_relevant_reasoning_rules(client, issue_ids: list, *, include_demo: bool = False) -> list:
    return get_reusable_knowledge(client, issue_ids, knowledge_types=("reasoning_rule",), include_demo=include_demo)


def get_knowledge_gaps(client, case_id: str) -> list:
    return client.select("geo_knowledge_gaps", {
        "select": "*", "case_id": f"eq.{case_id}", "order": "created_at.asc",
    })
