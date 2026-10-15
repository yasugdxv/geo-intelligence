"""Geopolitical Intelligence OS Phase G: Learning Candidate → Knowledge Type Mapperのテスト。
§22(自由入力させない/一箇所に集約)、§44(Promotion二重Gate維持)を検証する。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_learning_candidate_service as learning_service  # noqa: E402
import geo_ios_learning_knowledge_mapper as mapper  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _make_candidate(client, candidate_type: str, **overrides) -> dict:
    fields = {
        "candidate_type": candidate_type,
        "observation": "Expertは議会を独立主体として分析すべきと指摘した",
        "generalized_rule": "米国政策分析では議会（上院/下院）を政権と独立したActorとして扱う",
        "extraction_model": "gpt-test",
        "trigger_context": {"expert_source": "steward-1"},
        "failure_pattern_example": "政権のみを分析しActorが漏れる",
        "scope": "domain_general",
        "confidence": "中",
    }
    fields.update(overrides)
    return learning_service.create_candidate(client, **fields)


@pytest.mark.parametrize("candidate_type", list(mapper.REASONING_RULE_CANDIDATE_TYPES.keys()))
def test_reasoning_rule_family_maps_to_reasoning_rule_knowledge_type(candidate_type):
    client = FakeSupabaseClient()
    candidate = _make_candidate(client, candidate_type)
    knowledge_type, content = mapper.map_candidate_to_knowledge(candidate)
    assert knowledge_type == "reasoning_rule"
    assert content["rule_category"] == mapper.REASONING_RULE_CANDIDATE_TYPES[candidate_type]
    assert content["rule"] == candidate["generalized_rule"]
    assert content["common_failure_modes"] == [candidate["failure_pattern_example"]]


def test_actor_intelligence_requires_target_entity_id():
    client = FakeSupabaseClient()
    candidate = _make_candidate(client, "actor_intelligence")
    with pytest.raises(mapper.MappingError):
        mapper.map_candidate_to_knowledge(candidate)  # target_entity_id未指定
    knowledge_type, content = mapper.map_candidate_to_knowledge(candidate, target_entity_id="ent-mexico")
    assert knowledge_type == "actor_profile_update"
    assert content["target_entity_id"] == "ent-mexico"


def test_media_intelligence_and_monitoring_indicator_not_auto_mappable():
    client = FakeSupabaseClient()
    media_candidate = _make_candidate(client, "media_intelligence")
    with pytest.raises(mapper.MappingError):
        mapper.map_candidate_to_knowledge(media_candidate)

    indicator_candidate = _make_candidate(client, "monitoring_indicator")
    with pytest.raises(mapper.MappingError):
        mapper.map_candidate_to_knowledge(indicator_candidate)


def test_promote_via_mapper_enforces_double_gate():
    """candidate(status=candidate) → approve → promote_via_mapperで初めてKnowledge Item
    (status=candidate)を作る。Knowledge自体はまだactiveではない（二重Gate、§21/§44）。"""
    client = FakeSupabaseClient()
    candidate = _make_candidate(client, "reasoning_rule")

    with pytest.raises(learning_service.LearningCandidateError):
        learning_service.promote_via_mapper(client, candidate["candidate_id"], created_by="steward-1", created_by_role="knowledge_steward")

    learning_service.approve_candidate(client, candidate["candidate_id"], reviewed_by="steward-1", reviewer_role="knowledge_steward")
    knowledge_item = learning_service.promote_via_mapper(
        client, candidate["candidate_id"], created_by="steward-1", created_by_role="knowledge_steward")
    assert knowledge_item["status"] == "candidate"  # まだactiveではない
    assert knowledge_item["knowledge_type"] == "reasoning_rule"

    active_only = client.select("geo_knowledge_items", {"select": "*", "status": "eq.active"})
    assert active_only == []


def test_promote_via_mapper_rejects_unsupported_type_even_when_approved():
    client = FakeSupabaseClient()
    candidate = _make_candidate(client, "media_intelligence")
    learning_service.approve_candidate(client, candidate["candidate_id"], reviewed_by="steward-1", reviewer_role="knowledge_steward")
    with pytest.raises(mapper.MappingError):
        learning_service.promote_via_mapper(client, candidate["candidate_id"], created_by="steward-1", created_by_role="knowledge_steward")
    assert client.tables.get("geo_knowledge_items", []) == []
