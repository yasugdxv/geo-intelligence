"""地政学インテリジェンス基盤: geo_intelligence_service.py が v1.2段階実行方式（既定）と
組み合わさっても正しく動作することを検証する。

Phase Bレビューで、既存のいくつかのテスト（test_geo_intelligence_service_phase2.py /
test_geo_weekly_monitoring.py / test_geo_phase4_final_e2e.py）が、既定の実行方式が段階実行に
変わったことで挙動が変わり、`GEO_EXPERT_EXECUTION_MODE=monolithic`へ固定する形で対応した。
それらは「一括方式での挙動」を検証したまま残る一方、同じ関心事（十分性ゲート・週次Top N補強・
business_impactのマージ）が段階実行方式（既定）でも成立することを、本ファイルで別途検証する
（単にテストを通すためだけの固定ではないことを示す）。
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_intelligence_service as service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402
from tests.test_geo_intelligence_service_phase2 import _base_request, _sufficient_tables  # noqa: E402
from tests.test_geo_weekly_monitoring import _base_weekly_request, _india_tables, _claim  # noqa: E402

STAGE1_CASE_ANALYSIS = {
    "one_line_assessment": "見立て文（段階実行）。", "initial_anomaly": None,
    "claims": [{"claim_type": "fact", "text": "水資源規制が強化された", "source_ref": "e-1"}],
    "dynamics": "背景の力学。", "decisive_power_and_leverage": None,
    "key_point_assessment": {
        "candidate_key_points": [{
            "key_point": "水資源規制が強化されるか", "why_it_could_be_key": "政治的圧力の帰趨を左右するため",
            "control_type": "institutionally_constrained", "controlling_entity_ids": None,
            "control_structure_summary": "インド政府の規制権限に規定される",
            "what_it_explains": "規制強化の傾向", "what_it_does_not_explain": "実施時期",
            "supporting_evidence_ids": None, "control_type_adjustment_reason": None,
            "historical_supporting_evidence_ids": None, "supporting_knowledge_refs": None,
        }],
        "primary_key_point": "水資源規制が強化されるか", "selection_rationale": "規制強化データが最も一貫して説明する",
        "rejected_candidates": None,
        "hinge_structure": {"primary_hinge": "インド政府の方針", "secondary_hinges": None},
        "control_type": "institutionally_constrained", "controlling_entity_ids": None,
        "control_structure_summary": "インド政府の規制権限に規定される",
        "supporting_evidence_ids": ["e-1"], "contradicting_evidence_ids": None,
        "disconfirming_signal": "規制強化の動きが撤回された場合", "confidence": "中",
        "control_type_adjustment_reason": None,
        "evidence_class": None, "historical_supporting_evidence_ids": None,
        "confidence_adjustment_reason": None,
        "focus_selection": None,
        "evidence_relevance": [{"evidence_id": "e-1", "claim_relevance": "direct",
                                 "source_type": "current_news", "rationale": None}],
        "relevance_confidence_adjustment_reason": None,
        "supporting_knowledge_refs": None,
    },
    "key_stakeholder_assessment": {
        "determination": "single", "selected_actor_ids": ["india"],
        "rationale": "規制の最終決定権を持つため", "decision_structure": None,
        "confidence": "中", "evidence_ids": None,
    },
    "key_actor_profiles": [{
        "actor_ref": "india", "actor": "India", "entity_id": None, "formal_role": None,
        "decision_power": None, "ability_to_move": None, "ability_to_block": None,
        "ultimate_objective": "水資源管理の強化", "strategic_relationship_preference": None,
        "persistent_preference": "段階的な規制強化", "persistent_preference_evidence": [],
        "issue_objective": None, "observed_action": None, "preference_action_gap": None,
        "perceived_path": None, "event_role": None,
        "primary_audience": None, "secondary_audience": None, "driving_forces": [],
        "red_line": None, "confidence": "中", "evidence_ids": None,
        "persistent_preference_change": None,
        "why_actor_matters": None, "available_leverage": None,
        "unavailable_or_costly_cards": None, "vulnerabilities": None,
        "relevant_decision_patterns": None,
        "actor_intelligence_provenance": {
            "actor_intelligence_status": "sufficient", "profile_source": "actor_intelligence_candidate",
            "historical_episode_ids": [], "pattern_ids": [], "exception_episode_ids": [],
            "supporting_evidence_ids": [], "general_knowledge_used": False,
        },
        "actor_model_challenge": {
            "challenge_question": "段階的な規制強化という傾向が今回の規制動向にも本当に当てはまるか",
            "outcome": "inconclusive", "reasoning": None,
        },
        "pattern_applicability_assessment": None,
        "confidence_adjustment_reason": None,
    }],
    "preference_action_gap_summary": None, "identified_knowledge_gaps": [],
    "main_scenario": {
        "description": "規制強化の継続", "confidence": "中", "supporting_evidence": ["x"],
        "selection_rationale": "根拠あり", "assumptions": None, "observable_indicators": None,
        "historical_analogies_used": None, "evidence_class": None, "confidence_adjustment_reason": None,
        "evidence_relevance": None, "weakening_evidence_relevance": None,
        "relevance_confidence_adjustment_reason": None,
        "derived_from_hypothesis_id": None,
    },
    "alternative_scenarios": [{"description": "緩和シナリオ", "confidence": "低", "switch_conditions": ["条件"]}],
    "strongest_counterargument": "反論", "weakening_information": None,
    "contradicting_evidence": None, "retraction_conditions": ["撤回条件"],
    "additional_research": [],
    "business_impact": {"direct": "サントリーは即座に取水を停止すべき", "indirect": "規制強化の政策圧力が高まる可能性がある",
                        "unrelated_note": None, "environment_change": "調達戦略の見直しが必要になりうる"},
    "monitoring_indicators": ["規制動向"], "unknown_or_uncertain": None, "executive_summary": None,
}

STAGE2_AUDIENCE_DEEP = {
    "audience_analysis": [],
    "key_actor_action_substance": [],
    "alternative_scenarios": [{"description": "緩和シナリオ", "confidence": "低",
                               "strengthening_conditions": None, "weakening_conditions": None,
                               "switch_conditions": ["条件"],
                               "evidence_class": None, "confidence_adjustment_reason": None,
                               "derived_from_hypothesis_id": None}],
    "additional_stakeholders": [],
}


def _resp(content: dict) -> MagicMock:
    return MagicMock(choices=[MagicMock(message=MagicMock(content=json.dumps(content)))], usage=None)


def _staged_mock_client() -> MagicMock:
    """case_analysis+standard(既定)はaudience_signal+deep_scenarioが1回のStage2呼び出しへ
    合成されるため、Stage1用・Stage2用の2つのレスポンスを順番に返す。"""
    c = MagicMock()
    c.chat.completions.create.side_effect = [_resp(STAGE1_CASE_ANALYSIS), _resp(STAGE2_AUDIENCE_DEEP)]
    return c


# ---- test_case5_expert_ai_called_only_when_sufficient_and_analysis_requested の段階実行版 ----

def test_staged_execution_expert_ai_called_when_sufficient_and_analysis_requested():
    client = FakeSupabaseClient(tables=_sufficient_tables())
    azure_client = _staged_mock_client()

    response = service.process_request(client, _base_request(analysis_requested=True),
                                        azure_client=azure_client, model="gpt-4o")

    # 段階実行(既定standard)ではcase_analysisモードはaudience_signal+deep_scenarioが発火するため
    # Stage1+Stage2の合計2回になる(一括方式の1回とは異なる。これは仕様通り)
    assert azure_client.chat.completions.create.call_count == 2
    assert response["key_stakeholders"] == ["India"]
    assert response["political_dynamics"] == "背景の力学。"


# ---- test_expert_ai_cross_domain_implications_excludes_direct_prescriptive_field の段階実行版 ----

def test_staged_execution_cross_domain_implications_excludes_direct_prescriptive_field():
    client = FakeSupabaseClient(tables=_sufficient_tables())
    azure_client = _staged_mock_client()

    response = service.process_request(client, _base_request(), azure_client=azure_client, model="gpt-4o")

    assert response["cross_domain_implications"] is not None
    assert "取水を停止すべき" not in response["cross_domain_implications"]
    assert "規制強化の政策圧力" in response["cross_domain_implications"]


# ---- test_scenario2_sufficient_and_advanced_analysis_uses_expert_ai の段階実行版 ----

def test_staged_execution_scenario2_sufficient_and_advanced_analysis_uses_expert_ai():
    client = FakeSupabaseClient(tables=_sufficient_tables())
    azure_client = _staged_mock_client()

    response = service.process_request(client, _base_request(analysis_requested=True),
                                        azure_client=azure_client, model="gpt-4o")

    assert response["knowledge_sufficiency"] == "sufficient"
    saved_response = client.tables["geo_intelligence_responses"][0]
    assert saved_response["expert_response_id"] is not None


# ---- test_case12_expert_ai_used_only_for_top_items_with_corroboration の段階実行版 ----
# 週次バックグラウンド経路は既にprofile="quick"へ切り替え済み（実データ確認後）。
# quickはcase_analysisでStage2モジュールを発火しないため、Stage1のみ・1回で完結するはず。

def test_staged_execution_weekly_quick_uses_exactly_one_call_for_top_item():
    azure_client = MagicMock()
    azure_client.chat.completions.create.return_value = _resp(STAGE1_CASE_ANALYSIS)

    client = FakeSupabaseClient(tables=_india_tables(
        _claim("c1", "India water regulation policy tightened amid political pressure", "2026-08-18"),
        _claim("c2", "India water regulation policy tightened amid pressure groundwater", "2026-08-19"),
    ))
    response = service.process_request(client, _base_weekly_request(),
                                       azure_client=azure_client, model="gpt-4o")
    assert len(response["relevant_intelligence"]) == 1
    # profile="quick"のためStage2は発火せずStage1のみ・1回で完結する
    azure_client.chat.completions.create.assert_called_once()
    assert response["relevant_intelligence"][0]["outlook"] == "規制強化の継続"
