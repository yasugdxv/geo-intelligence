"""地政学ナレッジ＆インテリジェンスDB Phase1: E2Eテスト。

Case: 「第三国による国際海峡の管理、航行保護、通行料徴収の実現可能性」
承認済みPlan（docs/geopolitical-implementation-plan.md）記載の24手順を検証する。
実Supabase接続は行わず、tests._fakes.FakeSupabaseClientとMagicMockのLLMクライアントで完結させる。
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_candidate_extraction_service as candidate_service  # noqa: E402
import geo_case_service  # noqa: E402
import geo_claim_service  # noqa: E402
import geo_entity_service  # noqa: E402
import geo_foundation_pack_service  # noqa: E402
import geo_knowledge_gap_service as gap_service  # noqa: E402
import geo_knowledge_service  # noqa: E402
import geo_retrieval_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _mock_llm_client(response: dict) -> MagicMock:
    """test_geo_expert_engine.pyと同じパターン: chat.completions.createのJSON応答をモックする。"""
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(response)))],
    )
    return client


def _precedent_candidate():
    return {
        "candidate_type": "precedent", "proposed_title": "スエズ運河国際管理の前例",
        "proposed_summary": "運河は自然海峡と異なり人工的な管理制度が存在する",
        "structured_content": {
            "precedent_title": "スエズ運河国際管理の前例", "question_examined": "国際管理下での通行料徴収",
            "period": "1888-1956", "geography": "スエズ運河", "actors": ["エジプト", "スエズ運河会社"],
            "background": "コンスタンティノープル条約による国際管理", "triggering_event": "運河開通",
            "actor_objectives": ["航行の自由確保"], "instruments_used": ["条約", "国際管理会社"],
            "legal_and_institutional_conditions": "1888年コンスタンティノープル条約",
            "counterpart_responses": "沿岸国の同意を前提とした運用", "outcome": "長期にわたり機能",
            "success_factors": ["条約による法的裏付け"], "failure_factors": ["1956年国有化で管理体制が崩壊"],
            "indicators_to_watch": ["沿岸国の主権主張の強まり"], "sources": ["コンスタンティノープル条約"],
            "confidence": "中",
        },
        "why_reusable": "運河と自然海峡の制度的差異を理解する上で参照可能",
        "applicable_conditions": ["人工運河であり条約による国際管理の先例がある場合"],
        "non_applicable_conditions": ["自然海峡で沿岸国の同意が無い場合"],
        "confidence": "中", "confidence_rationale": "歴史的文献に基づく",
        "information_class": "external_assessment",
    }


def _mechanism_candidate():
    return {
        "candidate_type": "mechanism", "proposed_title": "航行保護費用の第三者提供メカニズム",
        "proposed_summary": "第三国が安全保障を提供し対価を得る構造",
        "structured_content": {
            "mechanism_name": "航行保護費用の第三者提供", "mechanism_summary": "第三国が護衛を提供し費用を徴収する",
            "actor_objective": "海峡通航の安全確保と費用回収", "causal_chain": ["護衛提供", "費用請求", "利用国の受容"],
            "enabling_conditions": ["沿岸国の同意", "利用国の費用負担合意"],
            "required_assets": ["海軍力", "監視能力"], "target_dependencies": ["海上輸送への依存"],
            "constraints": ["国際法上の航行の自由原則"], "costs": ["艦艇運用コスト"],
            "risks": ["沿岸国主権との衝突"], "likely_countermeasures": ["独自パトロールの強化"],
            "escalation_path": "沿岸国の反発拡大", "de_escalation_path": "多国間枠組みへの移行",
            "indicators": ["護衛要請件数"], "confidence": "低",
        },
        "why_reusable": "類似の海峡・チョークポイント事案に応用可能",
        "applicable_conditions": ["沿岸国が単独で安全確保できない場合"],
        "non_applicable_conditions": ["沿岸国が十分な海軍力を持つ場合"],
        "confidence": "低", "confidence_rationale": "類推に基づく仮説段階",
        "information_class": "internal_hypothesis",
    }


def _reasoning_rule_candidate():
    return {
        "candidate_type": "reasoning_rule", "proposed_title": "運河と自然海峡を区別するルール",
        "proposed_summary": "管理能力と法的管理権を混同しない",
        "structured_content": {
            "rule": "人工運河（条約による国際管理の先例あり）と自然海峡（沿岸国主権が原則）を区別して評価する",
            "rationale": "法的地位が全く異なるため同一の実現可能性評価はできない",
            "applicable_question_types": ["海峡管理", "航行保護", "通行料徴収"],
            "required_checks": ["対象が運河か自然海峡か", "沿岸国の同意の有無"],
            "comparison_dimensions": ["管理能力", "法的管理権", "軍事的保護能力", "料金徴収能力"],
            "common_failure_modes": ["運河の前例を自然海峡にそのまま適用する誤り"],
            "expert_source": "案件分析からの抽出", "approval_status": "pending",
        },
        "why_reusable": "海峡・チョークポイント関連の全案件で再利用可能な判断基準",
        "applicable_conditions": ["海峡・運河の管理可能性を検討する全案件"],
        "non_applicable_conditions": [],
        "confidence": "高", "confidence_rationale": "国際法上明確に区別される概念のため",
        "information_class": "observed_fact",
    }


def test_strait_tolling_case_end_to_end():
    client = FakeSupabaseClient(tables={
        "geo_themes": [{"theme_id": "theme-1", "name_ja": "海洋チョークポイント", "code": "chokepoints"}],
    })

    # 2. Entity登録
    strait = geo_entity_service.create_entity(client, entity_type="strait", canonical_name="某国際海峡",
                                               actor_id="analyst1")
    coastal_state = geo_entity_service.create_entity(client, entity_type="country", canonical_name="沿岸国A",
                                                       actor_id="analyst1")
    third_party = geo_entity_service.create_entity(client, entity_type="country", canonical_name="第三国B",
                                                     actor_id="analyst1")

    # Issue（Theater配下の個別論点）を作成し、Case・Entityを同じIssueへ紐付ける
    issue = client.insert("geo_issues", [{
        "theme_id": "theme-1", "code": "strait_tolling", "name_ja": "海峡管理・通行料徴収",
    }])[0]

    # 1. Case作成
    case = geo_case_service.create_case(
        client, case_title="第三国による国際海峡の管理、航行保護、通行料徴収の実現可能性",
        original_question="海峡の管理と航行保護を第三国が担い、通行料または安全保障費用を徴収する構想には"
                           "どのような前例があり、どの条件で実現可能または不可能になるか",
        actor_id="analyst1")
    geo_case_service.link_case_to_issue(client, case_id=case["case_id"], issue_id=issue["issue_id"])
    for entity in (strait, coastal_state, third_party):
        client.insert("geo_entity_issues", [{"entity_id": entity["entity_id"], "issue_id": issue["issue_id"]}])

    # 3. Relationship登録
    geo_entity_service_rel = client.insert("geo_relationships", [{
        "from_entity_id": third_party["entity_id"], "to_entity_id": coastal_state["entity_id"],
        "relationship_domain": "security", "relationship_type": "安全保障提供の可能性",
        "dependency_direction": "to_depends_on_from", "confidence": "低", "is_demo": False,
    }])[0]
    client.insert("geo_relationship_issues", [{
        "relationship_id": geo_entity_service_rel["relationship_id"], "issue_id": issue["issue_id"],
    }])

    # 4. Document登録（geo_documents_store.pyは対象外、ここではfake tableへ直接投入）
    document = client.insert("geo_documents", [{
        "theme_id": "theme-1", "title": "コンスタンティノープル条約 解説",
        "extracted_text": "スエズ運河は1888年のコンスタンティノープル条約により国際管理された。" * 50,
    }])[0]
    chunks = geo_claim_service.chunk_document(client, document["document_id"])
    assert len(chunks) >= 1

    # 5. Claim抽出
    claim = geo_claim_service.create_claim(
        client, claim_text="運河は条約による国際管理の前例があるが、自然海峡には同様の前例が乏しい",
        information_class="external_assessment", origin_type="document",
        origin_id=document["document_id"], actor_id="analyst1", subject_entity_id=strait["entity_id"])

    # 6. Evidence紐付け
    evidence = geo_claim_service.link_evidence(
        client, claim_id=claim["claim_id"], evidence_role="supporting", actor_id="analyst1",
        document_chunk_id=chunks[0]["chunk_id"])
    assert evidence["document_chunk_id"] == chunks[0]["chunk_id"]

    # 7. Foundation Pack生成
    pack = geo_foundation_pack_service.build_foundation_pack(client, case["case_id"])
    assert pack["1_event_question_overview"]["case_title"] == case["case_title"]
    assert {e["entity_id"] for e in pack["2_relevant_entities"]} == {
        strait["entity_id"], coastal_state["entity_id"], third_party["entity_id"]}
    assert len(pack["6_relationships_and_dependencies"]["relationships"]) == 1
    assert len(pack["14_existing_claims_and_evidence"]) == 1

    # 8. Deterministic Gap生成
    deterministic_gaps = gap_service.detect_and_create_deterministic_gaps(
        client, case["case_id"], actor_id="analyst1")
    assert any(g["gap_type"] == "missing_precedent" for g in deterministic_gaps)
    assert any(g["gap_type"] == "missing_actor_profile" for g in deterministic_gaps)

    # 9. Analytical Gap Candidate生成（LLMモック）
    analytical_azure = _mock_llm_client({"gaps": [{
        "gap_type": "legal_institutional_gap",
        "gap_description": "自然海峡における第三国管理の法的根拠が未確認",
        "why_needed": "UNCLOS上の通過通航制度との整合性確認が必要",
        "related_hypothesis_statement": None,
        "available_evidence": "運河の条約管理の前例",
        "missing_evidence": "自然海峡における同種の前例",
        "recommended_source_categories": ["国際法文献", "UNCLOS解説"],
        "recommended_queries": ["UNCLOS 通過通航 第三国 管理"],
        "priority": "high", "uncertainty": "前例が乏しいため判断保留",
    }]})
    analytical_gaps = gap_service.generate_analytical_gap_candidates(
        client, analytical_azure, "gpt-4o", case["case_id"], actor_id="analyst1")
    assert len(analytical_gaps) == 1
    assert analytical_gaps[0]["gap_category"] == "analytical_candidate"

    # 10. Main Hypothesis登録
    main_hypothesis = geo_case_service.create_hypothesis(
        client, case_id=case["case_id"], actor_id="analyst1", hypothesis_type="main",
        hypothesis_statement="自然海峡では沿岸国の同意なき第三国管理は法的正当性を欠き実現困難である",
        rationale="UNCLOS上の主権原則と前例の乏しさによる")
    geo_case_service.attach_evidence_to_hypothesis(
        client, hypothesis_id=main_hypothesis["hypothesis_id"], claim_id=claim["claim_id"],
        stance="supporting")

    # 11. Alternative Hypothesis登録
    alt_hypothesis = geo_case_service.create_hypothesis(
        client, case_id=case["case_id"], actor_id="analyst1", hypothesis_type="alternative",
        hypothesis_statement="沿岸国が同意し多国間枠組みを経れば限定的に実現し得る",
        rationale="運河の前例が示す条約による正当化の可能性")

    # 12-14. Precedent/Mechanism/Reasoning Rule Candidate生成（LLMモック、1回の呼び出しで3種提案）
    candidate_azure = _mock_llm_client({"candidates": [
        _precedent_candidate(), _mechanism_candidate(), _reasoning_rule_candidate(),
    ]})
    extraction_result = candidate_service.extract_candidates_from_case(
        client, candidate_azure, "gpt-4o", case["case_id"], actor_id="analyst1")
    assert extraction_result["skipped"] == []
    created_by_type = {c["knowledge_type"]: c for c in extraction_result["created"]}
    assert set(created_by_type) == {"precedent", "mechanism", "reasoning_rule"}
    for item in extraction_result["created"]:
        assert item["status"] == "candidate"

    precedent_item = created_by_type["precedent"]
    for item in extraction_result["created"]:
        geo_knowledge_service.link_to_issue(client, knowledge_item_id=item["knowledge_item_id"],
                                             issue_id=issue["issue_id"])

    # 18. Candidateが Active Knowledgeとして使用されない（レビュー前は取得されない）
    reusable_before = geo_retrieval_service.get_reusable_knowledge(client, [issue["issue_id"]])
    assert reusable_before == []

    # 15. Reviewerによるin_review化
    review1 = geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=precedent_item["knowledge_item_id"], reviewer_id="reviewer1",
        reviewer_role="expert_reviewer", review_decision="start_review", review_comment="精査開始",
        evidence_adequacy="partial", applicability_checked=False)
    assert review1["knowledge_item"]["status"] == "in_review"

    # 16. Reviewerによるactive化
    review2 = geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=precedent_item["knowledge_item_id"], reviewer_id="reviewer1",
        reviewer_role="expert_reviewer", review_decision="approve_active", review_comment="承認",
        evidence_adequacy="sufficient", applicability_checked=True)
    assert review2["knowledge_item"]["status"] == "active"

    # 17. Active Knowledgeが次のCase検索で取得される
    next_case = geo_case_service.create_case(
        client, case_title="別の海峡での類似検討", original_question="別件", actor_id="analyst2")
    geo_case_service.link_case_to_issue(client, case_id=next_case["case_id"], issue_id=issue["issue_id"])
    reusable_after = geo_retrieval_service.get_reusable_knowledge(client, [issue["issue_id"]])
    assert len(reusable_after) == 1
    assert reusable_after[0]["knowledge_item_id"] == precedent_item["knowledge_item_id"]

    # 19. Contested Knowledgeが対立情報として表示される
    mechanism_item = created_by_type["mechanism"]
    geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=mechanism_item["knowledge_item_id"], reviewer_id="reviewer1",
        reviewer_role="expert_reviewer", review_decision="start_review", review_comment="精査開始",
        evidence_adequacy="partial", applicability_checked=False)
    geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=mechanism_item["knowledge_item_id"], reviewer_id="reviewer1",
        reviewer_role="expert_reviewer", review_decision="approve_contested", review_comment="異論あり",
        evidence_adequacy="partial", applicability_checked=True)
    contested = geo_retrieval_service.get_contested_knowledge(client, [issue["issue_id"]])
    assert len(contested) == 1
    assert contested[0]["knowledge_item_id"] == mechanism_item["knowledge_item_id"]

    # 20. Rejected KnowledgeがFoundation Packへ混入しない
    reasoning_item = created_by_type["reasoning_rule"]
    geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=reasoning_item["knowledge_item_id"], reviewer_id="reviewer1",
        reviewer_role="expert_reviewer", review_decision="start_review", review_comment="精査開始",
        evidence_adequacy="partial", applicability_checked=False)
    geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=reasoning_item["knowledge_item_id"], reviewer_id="reviewer1",
        reviewer_role="expert_reviewer", review_decision="reject", review_comment="根拠不十分",
        evidence_adequacy="insufficient", applicability_checked=False)
    geo_knowledge_service.link_to_issue(client, knowledge_item_id=reasoning_item["knowledge_item_id"],
                                         issue_id=issue["issue_id"])
    pack_after_reject = geo_foundation_pack_service.build_foundation_pack(client, next_case["case_id"])
    reusable_types_in_pack = {i["knowledge_item_id"] for i in pack_after_reject["13_relevant_reasoning_rules"]}
    assert reasoning_item["knowledge_item_id"] not in reusable_types_in_pack

    # 21. Demo Fixtureがデフォルトで除外される
    demo_precedent = geo_knowledge_service.create_knowledge_item(
        client, knowledge_type="precedent", title="デモ用の前例", structured_content={
            **_precedent_candidate()["structured_content"], "precedent_title": "デモ用の前例",
            "applicable_conditions": ["デモ用"], "non_applicable_conditions": ["デモ用"],
        }, information_class="internal_hypothesis", created_by="analyst1", is_demo=True)
    geo_knowledge_service.link_to_issue(client, knowledge_item_id=demo_precedent["knowledge_item_id"],
                                         issue_id=issue["issue_id"])
    # Phase2B-2: retrieval_status（AIが参照してよいか）はstatusと独立した二軸。ここでは
    # review_knowledge_itemを介さず直接テストを組み立てているため、availableも明示的に揃える
    client.update("geo_knowledge_items", {"knowledge_item_id": f"eq.{demo_precedent['knowledge_item_id']}"},
                  {"status": "active", "retrieval_status": "available"})
    reusable_default = geo_retrieval_service.get_reusable_knowledge(client, [issue["issue_id"]])
    assert demo_precedent["knowledge_item_id"] not in {i["knowledge_item_id"] for i in reusable_default}
    reusable_with_demo = geo_retrieval_service.get_reusable_knowledge(client, [issue["issue_id"]], include_demo=True)
    assert demo_precedent["knowledge_item_id"] in {i["knowledge_item_id"] for i in reusable_with_demo}

    # 22. ClaimからEvidence・Source Documentへ遡れる
    claim_with_evidence = geo_claim_service.get_claim_with_evidence(client, claim["claim_id"])
    assert len(claim_with_evidence["evidence"]) == 1
    traced_chunk = client.select("geo_document_chunks", {
        "select": "*", "chunk_id": f"eq.{claim_with_evidence['evidence'][0]['document_chunk_id']}", "limit": "1",
    })[0]
    assert traced_chunk["document_id"] == document["document_id"]

    # 23. Status変更がAudit Logへ記録される
    review_logs = [log for log in client.tables["geo_audit_logs"] if log["action"] == "knowledge_item_review"]
    assert len(review_logs) >= 4  # precedent(2) + mechanism(2) + reasoning_rule(2)のうち少なくとも4件

    # 24. 旧versionが保持される（statusは上書きだが、versionが増分し、履歴はgeo_knowledge_reviewsに残る）
    final_precedent = geo_knowledge_service.get_knowledge_item(client, precedent_item["knowledge_item_id"])
    assert final_precedent["version"] == 3  # create(1) → start_review(2) → approve_active(3)
    history = client.select("geo_knowledge_reviews", {
        "select": "*", "knowledge_item_id": f"eq.{precedent_item['knowledge_item_id']}",
    })
    assert [h["new_status"] for h in history] == ["in_review", "active"]
