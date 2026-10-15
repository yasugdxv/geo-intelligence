"""地政学ナレッジ＆インテリジェンスDB: Case向けFoundation Pack生成（13章、Phase1版）。

geo_retrieval_serviceのRetrieval Layerを使用する。既存geo_expert_engine.build_source_pack
（軽量Q&A用）は変更しない。

表示ルール: active=本文利用可、contested=対立見解として別枠明示、candidate/in_review=
未承認候補として別セクション、demo=include_demo=trueが明示された場合のみ含める。
すべての主要記述にEvidence ID/Citationを付与する。
"""
import geo_case_service
import geo_relationship_service
import geo_retrieval_service


def _entity_ids_for_issues(client, issue_ids: list) -> list:
    if not issue_ids:
        return []
    links = client.select("geo_entity_issues", {
        "select": "entity_id", "issue_id": f"in.({','.join(issue_ids)})",
    })
    return sorted({link["entity_id"] for link in links})


def _relationship_ids_for_issues(client, issue_ids: list) -> list:
    if not issue_ids:
        return []
    links = client.select("geo_relationship_issues", {
        "select": "relationship_id", "issue_id": f"in.({','.join(issue_ids)})",
    })
    return sorted({link["relationship_id"] for link in links})


def _assets_and_dependencies(client, entity_ids: list, include_demo: bool) -> dict:
    if not entity_ids:
        return {"assets": [], "dependencies": []}
    asset_params = {"select": "*", "entity_id": f"in.({','.join(entity_ids)})"}
    dependency_params = {"select": "*", "entity_id": f"in.({','.join(entity_ids)})"}
    if not include_demo:
        asset_params["is_demo"] = "eq.false"
        dependency_params["is_demo"] = "eq.false"
    return {
        "assets": client.select("geo_assets", asset_params),
        "dependencies": client.select("geo_dependencies", dependency_params),
    }


def _entities(client, entity_ids: list, include_demo: bool) -> list:
    if not entity_ids:
        return []
    params = {"select": "*", "entity_id": f"in.({','.join(entity_ids)})"}
    if not include_demo:
        params["is_demo"] = "eq.false"
    return client.select("geo_entities", params)


def build_foundation_pack(client, case_id: str, *, include_demo: bool = False) -> dict:
    """22項目のFoundation Packを組み立てる（13章）。"""
    case = geo_case_service.get_case(client, case_id)
    if not case:
        raise ValueError(f"case_idが見つかりません: {case_id}")

    issue_links = client.select("geo_case_issues", {"select": "issue_id", "case_id": f"eq.{case_id}"})
    issue_ids = sorted({link["issue_id"] for link in issue_links})

    entity_ids = _entity_ids_for_issues(client, issue_ids)
    entities = _entities(client, entity_ids, include_demo)

    relationship_ids = _relationship_ids_for_issues(client, issue_ids)
    relationships = []
    if relationship_ids:
        params = {"select": "*", "relationship_id": f"in.({','.join(relationship_ids)})"}
        if not include_demo:
            params["is_demo"] = "eq.false"
        relationships = client.select("geo_relationships", params)
    # Issueに未タグでも、対象EntityどうしのRelationshipは関連性が高いため補完する
    for entity_id in entity_ids:
        for rel in geo_relationship_service.list_relationships_for_entity(client, entity_id, include_demo=include_demo):
            if rel["relationship_id"] not in {r["relationship_id"] for r in relationships}:
                relationships.append(rel)

    assets_deps = _assets_and_dependencies(client, entity_ids, include_demo)

    foundation_knowledge = geo_retrieval_service.get_foundation_knowledge(
        client, issue_ids, include_demo=include_demo)
    reusable_knowledge = geo_retrieval_service.get_reusable_knowledge(
        client, issue_ids, include_demo=include_demo)
    contested_knowledge = geo_retrieval_service.get_contested_knowledge(
        client, issue_ids, include_demo=include_demo)
    candidate_knowledge = geo_retrieval_service.get_candidate_knowledge(client, issue_ids)

    case_context = geo_retrieval_service.get_case_context(client, case_id)
    claims_with_evidence = geo_retrieval_service.get_claims_and_evidence(
        client, entity_ids=entity_ids, case_id=case_id)

    reusable_by_type = {}
    for item in reusable_knowledge:
        reusable_by_type.setdefault(item["knowledge_type"], []).append(item)

    deterministic_gaps = [g for g in case_context["gaps"] if g["gap_category"] == "deterministic"]
    analytical_gaps = [g for g in case_context["gaps"] if g["gap_category"] == "analytical_candidate"]
    main_hypotheses = [h for h in case_context["hypotheses"] if h["hypothesis_type"] == "main"]
    alternative_hypotheses = [h for h in case_context["hypotheses"] if h["hypothesis_type"] == "alternative"]

    sources = sorted({
        ev["evidence_role"] for c in claims_with_evidence for ev in c["evidence"]
    })  # Phase1簡易版: evidence_role分布のみ。文書レベルの出典一覧はUI側でclaim_evidence→documentを辿って表示する

    return {
        "1_event_question_overview": {
            "case_title": case["case_title"], "original_question": case["original_question"],
            "scope": case.get("scope"), "as_of_date": case.get("as_of_date"),
        },
        "2_relevant_entities": entities,
        "3_stakeholder_map": entities,  # Phase1はEntity一覧をStakeholder Mapの基礎として流用
        "4_decision_makers_veto_players": [e for e in entities if e.get("entity_type") in (
            "government", "military", "person", "political_party")],
        "5_objectives_core_interests": [
            k for k in foundation_knowledge if k["knowledge_type"] == "actor_profile"],
        "6_relationships_and_dependencies": {
            "relationships": relationships, "dependencies": assets_deps["dependencies"],
        },
        "7_assets": assets_deps["assets"],
        "8_existing_leverage_cards": reusable_by_type.get("leverage_card", []),
        "9_vulnerabilities_and_constraints": assets_deps["dependencies"],
        "10_historical_context": [
            k for k in foundation_knowledge if k["knowledge_type"] == "historical_path_dependency"],
        "11_relevant_precedents": reusable_by_type.get("precedent", []),
        "12_relevant_mechanisms": reusable_by_type.get("mechanism", []),
        "13_relevant_reasoning_rules": reusable_by_type.get("reasoning_rule", []),
        "14_existing_claims_and_evidence": claims_with_evidence,
        "15_what_changed": None,  # Phase1では自動生成しない（Case Findingの蓄積後、Phase2で対応）
        "16_deterministic_knowledge_gaps": deterministic_gaps,
        "17_analytical_gap_candidates": analytical_gaps,
        "18_main_hypothesis": main_hypotheses,
        "19_alternative_hypotheses": alternative_hypotheses,
        "20_scenarios_and_indicators": reusable_by_type.get("indicator", []),
        "21_sources": sources,
        "22_confidence_and_caveats": {
            "contested_knowledge_count": len(contested_knowledge),
            "contested_knowledge": contested_knowledge,
            "candidate_knowledge_count": len(candidate_knowledge),
            "candidate_knowledge": candidate_knowledge,
            "note": "candidateはレビュー未承認のため本文の事実として扱わないこと。"
                    "contestedは対立する見方が存在することを示す。",
        },
    }
