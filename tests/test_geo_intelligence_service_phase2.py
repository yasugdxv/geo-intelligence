import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_intelligence_service as service  # noqa: E402
from geo_intelligence_schema import GeoIntelligenceRequest  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

ISSUE_ID = "issue-1"
IRAN_ID = "entity-iran"
OMAN_ID = "entity-oman"


def _base_request(**overrides) -> dict:
    request = {
        "requester_app": "sustainability_intelligence",
        "request_type": "user_question",
        "question": "イランはホルムズ海峡の通航にどう対応しているか？",
        "country_region": ["Iran"],
        "themes": ["hormuz"],
        "analysis_requested": True,
    }
    request.update(overrides)
    return request


def _sufficient_tables() -> dict:
    """Stakeholder/Relationship/Foundation/Precedent/Claimが一通り揃った、sufficientになる想定のfixture"""
    return {
        "geo_entities": [
            {"entity_id": IRAN_ID, "canonical_name": "Iran", "entity_type": "country", "is_demo": False},
            {"entity_id": OMAN_ID, "canonical_name": "Oman", "entity_type": "country", "is_demo": False},
        ],
        "geo_entity_issues": [{"entity_id": IRAN_ID, "issue_id": ISSUE_ID}],
        "geo_relationships": [{
            "relationship_id": "rel-1", "from_entity_id": IRAN_ID, "to_entity_id": OMAN_ID,
            "relationship_type": "mediation_negotiation_channel", "relationship_domain": "diplomacy",
            "description": "イランとオマーンの仲介チャネル", "is_demo": False,
        }],
        "geo_knowledge_items": [
            {"knowledge_item_id": "ki-foundation", "knowledge_type": "country_foundation",
             "title": "イラン: 統治構造", "summary": "イランの統治構造の概要", "status": "active",
             "retrieval_status": "available", "is_demo": False, "confidence": "中",
             "agent_sharing_policy": "allowed_cross_domain"},
            {"knowledge_item_id": "ki-precedent", "knowledge_type": "precedent",
             "title": "過去のホルムズ海峡緊張化事例", "summary": "過去の先例の概要", "status": "active",
             "retrieval_status": "available", "is_demo": False, "confidence": "中",
             "agent_sharing_policy": "allowed_cross_domain"},
        ],
        "geo_knowledge_item_issues": [
            {"knowledge_item_id": "ki-foundation", "issue_id": ISSUE_ID},
            {"knowledge_item_id": "ki-precedent", "issue_id": ISSUE_ID},
        ],
        "geo_claims": [
            {"claim_id": "cl-fact", "claim_text": "MoUが2026年6月に署名された",
             "information_class": "observed_fact", "retrieval_status": "available", "is_demo": False},
            {"claim_id": "cl-assess", "claim_text": "IRGCが実質的な決定権を持つとの評価がある",
             "information_class": "external_assessment", "retrieval_status": "available", "is_demo": False,
             "source_id": "src-1"},
        ],
        "geo_claim_issues": [
            {"claim_id": "cl-fact", "issue_id": ISSUE_ID},
            {"claim_id": "cl-assess", "issue_id": ISSUE_ID},
        ],
        "geo_source_registry": [{"source_id": "src-1", "name": "Gulf International Forum"}],
    }


WELL_FORMED_EXPERT_RESPONSE = {
    "one_line_assessment": "イランは実効支配継続の方向。",
    "claims": [{"claim_type": "fact", "text": "MoUが署名された", "source_ref": "r-1"}],
    "stakeholders": [{"actor": "Iran"}, {"actor": "架空の国XYZ"}],
    "main_scenario": {"description": "グレーゾーン支配の継続", "confidence": "中", "supporting_evidence": ["攻撃再開"],
                       "selection_rationale": "過去の類似局面と一致するため"},
    "strongest_counterargument": "軟化する可能性もある",
    "retraction_conditions": ["攻撃が完全に停止した場合"],
    "business_impact": {"direct": "サントリーは即座に取水を停止すべき", "indirect": "規制強化の政策圧力が高まる可能性がある",
                        "unrelated_note": None, "environment_change": "調達戦略の見直しが必要になりうる"},
    "sources_and_citations": [{"result_id": "架空のreference-999", "result_type": "document",
                               "citation_label": "存在しない出典"}],
    # Session2: case_analysisモードの必須項目（geo_expert_quality_guard._key_stakeholder_v12_check）
    "key_point_assessment": {
        "candidate_key_points": [{
            "key_point": "イランが実効支配を継続するか", "why_it_could_be_key": "海峡管理の実質的な帰趨を左右するため",
            "control_type": "unknown", "what_it_explains": "現状のグレーゾーン継続傾向", "what_it_does_not_explain": "停止時期",
        }],
        "primary_key_point": "イランが実効支配を継続するか", "selection_rationale": "過去の類似局面と一致するため",
        "hinge_structure": {"primary_hinge": "イラン政府の方針"},
        "control_type": "unknown", "supporting_evidence_ids": None,
        "disconfirming_signal": "攻撃が完全に停止した場合", "confidence": "中",
    },
    # v1.2: case_analysisモードの必須項目（geo_expert_quality_guard._key_stakeholder_v12_check）
    "key_stakeholder_assessment": {
        "determination": "single", "selected_actor_ids": ["iran"],
        "rationale": "実効支配の継続可否を決定できるのはイラン政府のため", "confidence": "中",
    },
    "key_actor_profiles": [{"actor_ref": "iran", "actor": "Iran", "confidence": "中"}],
}


def _mock_llm_client(response: dict = None) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(response or WELL_FORMED_EXPERT_RESPONSE)))],
    )
    return client


# ---- Case 1: Knowledge十分 ----

def test_case1_sufficient_knowledge_has_references_and_assessment():
    client = FakeSupabaseClient(tables=_sufficient_tables())
    response = service.process_request(client, _base_request())

    assert response["knowledge_sufficiency"] == "sufficient"
    assert response["references"]
    assert response["geo_assessment"]
    assert response["key_stakeholders"]
    assert response["political_dynamics"]


# ---- Case 2: 部分的 ----

def test_case2_partial_knowledge_reports_missing_categories():
    tables = _sufficient_tables()
    # Relationshipを取り除き、Stakeholder/Dynamicsの一部が欠けた状態にする
    tables["geo_relationships"] = []
    client = FakeSupabaseClient(tables=tables)

    response = service.process_request(client, _base_request())

    assert response["knowledge_sufficiency"] == "partial"
    assert response["knowledge_gap"] is not None
    assert "relationship" in response["knowledge_gap"]["missing_categories"]
    # 回答可能な部分（Foundation/Claim等）は提示される
    assert response["references"]


# ---- Case 3: Knowledgeなし ----

def test_case3_no_knowledge_is_insufficient_and_does_not_fabricate():
    client = FakeSupabaseClient()  # 完全に空
    response = service.process_request(client, _base_request(
        question="存在しない架空の国XYZの情勢は？", country_region=["架空の国XYZ"], themes=[]))

    assert response["knowledge_sufficiency"] == "insufficient"
    assert response["references"] == []
    assert response["knowledge_gap"] is not None
    assert set(response["knowledge_gap"]["missing_categories"]) == set(service._ALL_CATEGORIES)
    # 推測による詳細な回答本文を生成していないこと（stub的な断り文言のみ）
    assert "見つからなかった" in response["geo_assessment"]


# ---- Case 4: Referenceなし（LLMが架空Referenceを生成しない） ----

def test_case4_references_are_never_fabricated_by_expert_ai():
    client = FakeSupabaseClient(tables=_sufficient_tables())
    azure_client = _mock_llm_client()  # 架空のreference-999/架空の国XYZを含む応答を返すモック

    response = service.process_request(client, _base_request(), azure_client=azure_client, model="gpt-4o")

    reference_ids = {r["reference_id"] for r in response["references"]}
    assert "架空のreference-999" not in reference_ids
    # referencesは実際にretrieveしたKnowledge/Claimのみ（LLM応答のsources_and_citationsは無視される）
    assert reference_ids <= {"ki-foundation", "ki-precedent", "cl-fact", "cl-assess"}


def test_case4_expert_ai_fabricated_stakeholder_is_not_filtered_but_references_stay_grounded():
    """Expert AIのstakeholders自体は既存run_expert_requestの出力をそのまま使う仕様のため
    （Phase2はExpert AI自体の出力検証はQuality Guard側の責務）、ここではreferencesが
    Expert AIの自由記述に汚染されず、DBから実際に取得したKnowledgeのみを指すことを確認する。"""
    client = FakeSupabaseClient(tables=_sufficient_tables())
    azure_client = _mock_llm_client()

    response = service.process_request(client, _base_request(), azure_client=azure_client, model="gpt-4o")
    for ref in response["references"]:
        assert ref["reference_id"] in {"ki-foundation", "ki-precedent", "cl-fact", "cl-assess"}


# ---- Case 5: Expert AI呼び出し条件 ----

def test_case5_expert_ai_called_only_when_sufficient_and_analysis_requested(monkeypatch):
    # v1.2段階実行方式は既定でStage1+Stage2の複数回呼び出しになりうるため、本テストが検証したい
    # 「Expert AIが呼ばれるか否か」とは無関係な呼び出し回数の差異を避けるため一括方式に固定する
    monkeypatch.setenv("GEO_EXPERT_EXECUTION_MODE", "monolithic")
    client = FakeSupabaseClient(tables=_sufficient_tables())
    azure_client = _mock_llm_client()

    service.process_request(client, _base_request(analysis_requested=True), azure_client=azure_client, model="gpt-4o")

    azure_client.chat.completions.create.assert_called_once()


def test_case5_expert_ai_not_called_when_analysis_not_requested():
    client = FakeSupabaseClient(tables=_sufficient_tables())
    azure_client = _mock_llm_client()

    service.process_request(client, _base_request(analysis_requested=False), azure_client=azure_client, model="gpt-4o")

    azure_client.chat.completions.create.assert_not_called()


def test_case5_expert_ai_not_called_when_knowledge_partial():
    tables = _sufficient_tables()
    tables["geo_relationships"] = []  # partial化
    client = FakeSupabaseClient(tables=tables)
    azure_client = _mock_llm_client()

    response = service.process_request(client, _base_request(analysis_requested=True),
                                        azure_client=azure_client, model="gpt-4o")

    assert response["knowledge_sufficiency"] == "partial"
    azure_client.chat.completions.create.assert_not_called()


def test_case5_expert_ai_not_called_when_knowledge_insufficient_even_with_analysis_requested():
    client = FakeSupabaseClient()  # 空
    azure_client = _mock_llm_client()

    response = service.process_request(client, _base_request(
        question="存在しない架空の国XYZの情勢は？", country_region=["架空の国XYZ"], themes=[],
        analysis_requested=True), azure_client=azure_client, model="gpt-4o")

    assert response["knowledge_sufficiency"] == "insufficient"
    azure_client.chat.completions.create.assert_not_called()


def test_case5_expert_ai_not_called_without_azure_client():
    client = FakeSupabaseClient(tables=_sufficient_tables())
    response = service.process_request(client, _base_request(analysis_requested=True))
    assert response["expert_response_id"] is None if "expert_response_id" in response else True
    saved_response = client.tables["geo_intelligence_responses"][0]
    assert saved_response["expert_response_id"] is None


# ---- Cross-domain Implications: 断定的表現を含む部分(direct)は使わない ----

def test_expert_ai_cross_domain_implications_excludes_direct_prescriptive_field(monkeypatch):
    monkeypatch.setenv("GEO_EXPERT_EXECUTION_MODE", "monolithic")
    client = FakeSupabaseClient(tables=_sufficient_tables())
    azure_client = _mock_llm_client()

    response = service.process_request(client, _base_request(), azure_client=azure_client, model="gpt-4o")

    assert response["cross_domain_implications"] is not None
    assert "取水を停止すべき" not in response["cross_domain_implications"]
    assert "規制強化の政策圧力" in response["cross_domain_implications"]


# ---- Schema validation ----

def test_invalid_request_missing_required_field_rejected_by_schema():
    with pytest.raises(ValidationError):
        GeoIntelligenceRequest(question="質問のみ")


def test_confidence_and_sufficiency_are_independent_axes():
    tables = _sufficient_tables()
    tables["geo_relationships"] = []
    client = FakeSupabaseClient(tables=tables)
    response = service.process_request(client, _base_request())

    assert response["knowledge_sufficiency"] == "partial"
    assert response["confidence"] in ("high", "medium", "low")
