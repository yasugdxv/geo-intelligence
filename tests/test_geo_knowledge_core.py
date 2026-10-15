"""地政学ナレッジ＆インテリジェンスDB Phase1: コアサービス層の単体テスト。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_claim_service  # noqa: E402
import geo_entity_resolution  # noqa: E402
import geo_entity_service  # noqa: E402
import geo_knowledge_item_schemas as schemas  # noqa: E402
import geo_knowledge_service  # noqa: E402
import geo_role_guard  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


# ── Entity / Alias / Resolution ──────────────────────────────────────

def test_create_entity_rejects_unknown_type():
    client = FakeSupabaseClient()
    with pytest.raises(ValueError):
        geo_entity_service.create_entity(client, entity_type="not_a_type",
                                          canonical_name="X", actor_id="tester")


def test_entity_resolution_exact_and_alias_and_unresolved():
    client = FakeSupabaseClient()
    entity = geo_entity_service.create_entity(
        client, entity_type="country", canonical_name="台湾", actor_id="tester")
    geo_entity_service.add_alias(client, entity_id=entity["entity_id"], alias_text="Taiwan",
                                  alias_type="transliteration")

    exact = geo_entity_resolution.resolve_or_flag(client, "台湾")
    assert exact["status"] == "resolved"
    assert exact["entity_id"] == entity["entity_id"]

    via_alias = geo_entity_resolution.resolve_or_flag(client, "Taiwan")
    assert via_alias["status"] == "resolved"
    assert via_alias["entity_id"] == entity["entity_id"]

    unresolved = geo_entity_resolution.resolve_or_flag(client, "存在しない主体")
    assert unresolved["status"] == "unresolved"


def test_merge_entities_requires_knowledge_steward_role():
    client = FakeSupabaseClient()
    primary = geo_entity_service.create_entity(client, entity_type="country", canonical_name="A国", actor_id="t")
    duplicate = geo_entity_service.create_entity(client, entity_type="country", canonical_name="A国(表記ゆれ)", actor_id="t")

    with pytest.raises(geo_role_guard.RoleDeniedError):
        geo_entity_service.merge_entities(client, primary_entity_id=primary["entity_id"],
                                           duplicate_entity_id=duplicate["entity_id"], actor_id="t",
                                           actor_role="analyst", reason="表記ゆれ")

    result = geo_entity_service.merge_entities(client, primary_entity_id=primary["entity_id"],
                                                duplicate_entity_id=duplicate["entity_id"], actor_id="t",
                                                actor_role="knowledge_steward", reason="表記ゆれ")
    assert result["status"] == "dissolved"
    assert result["successor_entity_id"] == primary["entity_id"]


def test_merge_entities_reassigns_relationships():
    client = FakeSupabaseClient()
    primary = geo_entity_service.create_entity(client, entity_type="country", canonical_name="A国", actor_id="t")
    duplicate = geo_entity_service.create_entity(client, entity_type="country", canonical_name="A国dup", actor_id="t")
    other = geo_entity_service.create_entity(client, entity_type="country", canonical_name="B国", actor_id="t")
    client.insert("geo_relationships", [{
        "from_entity_id": duplicate["entity_id"], "to_entity_id": other["entity_id"],
        "relationship_domain": "trade", "relationship_type": "輸出入", "confidence": "中",
        "is_demo": False,
    }])

    geo_entity_service.merge_entities(client, primary_entity_id=primary["entity_id"],
                                       duplicate_entity_id=duplicate["entity_id"], actor_id="t",
                                       actor_role="knowledge_steward", reason="重複")

    rels = client.select("geo_relationships", {"select": "*", "from_entity_id": f"eq.{primary['entity_id']}"})
    assert len(rels) == 1
    assert rels[0]["to_entity_id"] == other["entity_id"]


# ── Knowledge Item Schema検証 ─────────────────────────────────────────

def test_precedent_schema_requires_applicable_conditions():
    with pytest.raises(schemas.KnowledgeContentValidationError):
        schemas.validate_structured_content("precedent", {"precedent_title": "t"})


def test_create_knowledge_item_validates_schema_before_insert():
    client = FakeSupabaseClient()
    with pytest.raises(schemas.KnowledgeContentValidationError):
        geo_knowledge_service.create_knowledge_item(
            client, knowledge_type="precedent", title="不完全な前例",
            structured_content={"precedent_title": "t"}, information_class="external_assessment",
            created_by="tester")


# ── Knowledge Status遷移 ──────────────────────────────────────────────

def _valid_precedent_content():
    return {
        "precedent_title": "t", "question_examined": "q", "period": "p", "geography": "g",
        "actors": ["A"], "background": "b", "triggering_event": "e", "actor_objectives": ["o"],
        "instruments_used": ["i"], "legal_and_institutional_conditions": "l",
        "counterpart_responses": "c", "outcome": "out", "success_factors": ["s"],
        "failure_factors": ["f"], "applicable_conditions": ["ac"],
        "non_applicable_conditions": ["nac"], "indicators_to_watch": ["iw"], "sources": ["src"],
        "confidence": "中",
    }


def test_status_transition_rejects_direct_candidate_to_active():
    client = FakeSupabaseClient()
    item = geo_knowledge_service.create_knowledge_item(
        client, knowledge_type="precedent", title="前例A", structured_content=_valid_precedent_content(),
        information_class="external_assessment", created_by="tester")

    with pytest.raises(geo_knowledge_service.InvalidStatusTransitionError):
        geo_knowledge_service.review_knowledge_item(
            client, knowledge_item_id=item["knowledge_item_id"], reviewer_id="r1",
            reviewer_role="expert_reviewer", review_decision="approve_active",
            review_comment="ok", evidence_adequacy="sufficient", applicability_checked=True)


def test_status_transition_full_lifecycle_candidate_to_active():
    client = FakeSupabaseClient()
    item = geo_knowledge_service.create_knowledge_item(
        client, knowledge_type="precedent", title="前例B", structured_content=_valid_precedent_content(),
        information_class="external_assessment", created_by="tester")

    result = geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=item["knowledge_item_id"], reviewer_id="r1",
        reviewer_role="expert_reviewer", review_decision="start_review",
        review_comment="着手", evidence_adequacy="partial", applicability_checked=False)
    assert result["knowledge_item"]["status"] == "in_review"

    result2 = geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=item["knowledge_item_id"], reviewer_id="r1",
        reviewer_role="expert_reviewer", review_decision="approve_active",
        review_comment="承認", evidence_adequacy="sufficient", applicability_checked=True)
    assert result2["knowledge_item"]["status"] == "active"
    assert result2["knowledge_item"]["approved_by"] == "r1"
    assert len(client.tables["geo_knowledge_reviews"]) == 2


def test_review_requires_expert_reviewer_or_steward_role():
    client = FakeSupabaseClient()
    item = geo_knowledge_service.create_knowledge_item(
        client, knowledge_type="precedent", title="前例C", structured_content=_valid_precedent_content(),
        information_class="external_assessment", created_by="tester")

    with pytest.raises(geo_role_guard.RoleDeniedError):
        geo_knowledge_service.review_knowledge_item(
            client, knowledge_item_id=item["knowledge_item_id"], reviewer_id="analyst1",
            reviewer_role="analyst", review_decision="start_review",
            review_comment="x", evidence_adequacy="partial", applicability_checked=False)


# ── Demo Fixture分離 ──────────────────────────────────────────────────

def test_list_entities_excludes_demo_by_default():
    client = FakeSupabaseClient()
    geo_entity_service.create_entity(client, entity_type="country", canonical_name="実データ国", actor_id="t")
    geo_entity_service.create_entity(client, entity_type="country", canonical_name="デモ国", actor_id="t", is_demo=True)

    default_result = geo_entity_service.list_entities(client)
    assert {e["canonical_name"] for e in default_result} == {"実データ国"}

    with_demo = geo_entity_service.list_entities(client, include_demo=True)
    assert {e["canonical_name"] for e in with_demo} == {"実データ国", "デモ国"}


# ── Claim / Evidence ──────────────────────────────────────────────────

def test_claim_requires_origin_id_unless_manual():
    client = FakeSupabaseClient()
    with pytest.raises(ValueError):
        geo_claim_service.create_claim(client, claim_text="x", information_class="internal_hypothesis",
                                        origin_type="event", actor_id="t")

    created = geo_claim_service.create_claim(client, claim_text="x", information_class="internal_hypothesis",
                                              origin_type="manual", actor_id="t")
    assert created["origin_type"] == "manual"


def test_link_evidence_requires_exactly_one_target():
    client = FakeSupabaseClient()
    claim = geo_claim_service.create_claim(client, claim_text="x", information_class="internal_hypothesis",
                                            origin_type="manual", actor_id="t")
    with pytest.raises(ValueError):
        geo_claim_service.link_evidence(client, claim_id=claim["claim_id"], evidence_role="supporting",
                                         actor_id="t")
    with pytest.raises(ValueError):
        geo_claim_service.link_evidence(client, claim_id=claim["claim_id"], evidence_role="supporting",
                                         actor_id="t", event_id="e-1", observation_id="o-1")

    ok = geo_claim_service.link_evidence(client, claim_id=claim["claim_id"], evidence_role="supporting",
                                          actor_id="t", event_id="e-1")
    assert ok["event_id"] == "e-1"


def test_chunk_document_is_idempotent():
    # chunk_size(1500字)を超えるよう長い段落を2つ用意し、複数chunkに分かれることを確認する
    paragraph_a = "段落1です。" * 300
    paragraph_b = "段落2です。" * 300
    client = FakeSupabaseClient(tables={
        "geo_documents": [{"document_id": "doc-1", "extracted_text": f"{paragraph_a}\n\n{paragraph_b}"}],
    })
    first = geo_claim_service.chunk_document(client, "doc-1")
    assert len(first) == 2
    second = geo_claim_service.chunk_document(client, "doc-1")
    assert second == first
    assert len(client.tables["geo_document_chunks"]) == 2
