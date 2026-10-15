"""Actor Intelligence Layer（Phase AI-3）geo_expert_engine.pyへの組み込みのOFF/ON統合テスト。

Feature Flag（GEO_EXPERT_ACTOR_INTELLIGENCE_ENABLED）OFF/ONで、staged/monolithic両方の
プロンプトへのPack注入・actor_intelligence_statusのレスポンス反映・known_idsへの合流が
仕様通り行われることを、モックLLM・FakeSupabaseClientで検証する（実Azure呼び出しはしない）。

承認済みPlanの3ケース（Ukraine/Russia、US-China/Taiwan、North Korea/US）に対応する
最小限のFake DBを用意する。
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_actor_intelligence_service as ai  # noqa: E402
import geo_expert_engine as engine  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402
from tests.test_geo_expert_staged_execution import STAGE1_RESPONSE, _resp  # noqa: E402


def _resp_stage1_for(actor_ref: str, actor_name: str) -> dict:
    data = json.loads(json.dumps(STAGE1_RESPONSE))  # deep copy
    data["key_actor_profiles"][0]["actor_ref"] = actor_ref
    data["key_actor_profiles"][0]["actor"] = actor_name
    data["key_stakeholder_assessment"]["selected_actor_ids"] = [actor_ref]
    return data


def _mock_client(*contents) -> MagicMock:
    c = MagicMock()
    c.chat.completions.create.side_effect = [_resp(x) for x in contents]
    return c


def _client_with_case(*, case_id: str, issue_id: str, primary_entity_id: str,
                       primary_canonical_name: str, seed_active_profile: bool) -> FakeSupabaseClient:
    tables = {
        "geo_cases": [{"case_id": case_id, "case_title": "t", "original_question": "q",
                       "as_of_date": "2026-08-15"}],
        "geo_case_issues": [{"case_id": case_id, "issue_id": issue_id}],
        "geo_entity_issues": [{"issue_id": issue_id, "entity_id": primary_entity_id}],
        "geo_entities": [{
            "entity_id": primary_entity_id, "entity_type": "person",
            "canonical_name": primary_canonical_name, "external_ids": {}, "is_demo": False,
        }],
        "geo_relationships": [], "geo_knowledge_items": [], "geo_knowledge_reviews": [],
        "geo_audit_logs": [], "geo_events": [], "geo_documents": [],
    }
    client = FakeSupabaseClient(tables)
    if seed_active_profile:
        ai.seed_actor_profile(
            client, actor_intelligence_id=f"actor-{primary_entity_id}",
            canonical_name=primary_canonical_name,
            core_profile={"core_objectives_summary": ["x"], "core_interests_summary": ["y"]},
            current_posture={"as_of": "2026-08-10", "confidence": "中", "current_priorities": ["圧力継続"]},
            issue_positions=[{"issue_id": issue_id, "confidence": "中", "objectives": ["現状維持"]}],
            activate=True,
        )
    return client


CASES = [
    ("case-ukraine", "issue-ukraine", "trump-id", "Donald Trump", "trump"),
    ("case-taiwan", "issue-taiwan", "xi-id", "Xi Jinping", "xi"),
    ("case-nk", "issue-nk", "kim-id", "Kim Jong Un", "kim"),
]


def _run_staged(client, actor_ref, actor_name, *, flag_on: bool, monkeypatch, case_id):
    if flag_on:
        monkeypatch.setenv(engine.ACTOR_INTELLIGENCE_ENABLED_ENV, "true")
    else:
        monkeypatch.delenv(engine.ACTOR_INTELLIGENCE_ENABLED_ENV, raising=False)
    azure_client = _mock_client(_resp_stage1_for(actor_ref, actor_name))
    result = engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", None, "分析してください", case_id=case_id)
    sent_messages = azure_client.chat.completions.create.call_args[1]["messages"]
    user_prompt = sent_messages[1]["content"]
    return result, user_prompt


def test_actor_intelligence_off_by_default_no_pack_in_prompt(monkeypatch):
    case_id, issue_id, entity_id, name, ref = CASES[0]
    client = _client_with_case(case_id=case_id, issue_id=issue_id, primary_entity_id=entity_id,
                                primary_canonical_name=name, seed_active_profile=True)
    result, user_prompt = _run_staged(client, ref, name, flag_on=False, monkeypatch=monkeypatch, case_id=case_id)
    assert "Actor Intelligence Pack" not in user_prompt
    assert "actor_intelligence_status" not in result["response"]["response_json"]


def test_actor_intelligence_on_injects_pack_and_status_for_each_named_case(monkeypatch):
    for case_id, issue_id, entity_id, name, ref in CASES:
        client = _client_with_case(case_id=case_id, issue_id=issue_id, primary_entity_id=entity_id,
                                    primary_canonical_name=name, seed_active_profile=True)
        result, user_prompt = _run_staged(client, ref, name, flag_on=True, monkeypatch=monkeypatch, case_id=case_id)
        assert "Actor Intelligence Pack" in user_prompt, f"failed for {name}"
        assert name in user_prompt
        response_json = result["response"]["response_json"]
        assert response_json["actor_intelligence_status"] == "sufficient", f"failed for {name}"


def test_actor_intelligence_on_but_no_profile_yields_insufficient(monkeypatch):
    case_id, issue_id, entity_id, name, ref = CASES[0]
    client = _client_with_case(case_id=case_id, issue_id=issue_id, primary_entity_id=entity_id,
                                primary_canonical_name=name, seed_active_profile=False)
    result, user_prompt = _run_staged(client, ref, name, flag_on=True, monkeypatch=monkeypatch, case_id=case_id)
    response_json = result["response"]["response_json"]
    assert response_json["actor_intelligence_status"] == "insufficient"


def test_actor_intelligence_known_ids_include_precedent_for_scrubbing(monkeypatch):
    case_id, issue_id, entity_id, name, ref = CASES[0]
    client = _client_with_case(case_id=case_id, issue_id=issue_id, primary_entity_id=entity_id,
                                primary_canonical_name=name, seed_active_profile=True)
    from geo_knowledge_service import create_knowledge_item, review_knowledge_item
    precedent = create_knowledge_item(
        client, knowledge_type="precedent", title="先例A",
        structured_content={
            "precedent_title": "先例A", "question_examined": "q", "period": "2020", "geography": "g",
            "actors": [name], "background": "b", "triggering_event": "e", "actor_objectives": [],
            "instruments_used": [], "legal_and_institutional_conditions": "l",
            "counterpart_responses": "c", "outcome": "o", "success_factors": [], "failure_factors": [],
            "applicable_conditions": ["x"], "non_applicable_conditions": ["y"], "indicators_to_watch": [],
            "sources": [], "confidence": "中", "primary_actor_entity_id": entity_id,
        },
        information_class="internal_hypothesis", created_by="test",
    )
    review_knowledge_item(client, knowledge_item_id=precedent["knowledge_item_id"], reviewer_id="test",
                          reviewer_role="knowledge_steward", review_decision="start_review",
                          review_comment="t", evidence_adequacy="sufficient", applicability_checked=True)
    review_knowledge_item(client, knowledge_item_id=precedent["knowledge_item_id"], reviewer_id="test",
                          reviewer_role="knowledge_steward", review_decision="approve_active",
                          review_comment="t", evidence_adequacy="sufficient", applicability_checked=True)

    monkeypatch.setenv(engine.ACTOR_INTELLIGENCE_ENABLED_ENV, "true")
    pack = ai.build_actor_intelligence_pack(client, case_id)
    assert precedent["knowledge_item_id"] in pack["known_ids"]
