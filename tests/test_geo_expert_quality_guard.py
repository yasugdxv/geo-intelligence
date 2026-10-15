import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_expert_quality_guard as guard  # noqa: E402

VERSIONS = {
    "prompt_version": "geo-expert-v1", "knowledge_version": "abc123",
    "model": "gpt-4o", "source_pack_id": "sp-1",
}

WELL_FORMED_RESPONSE = {
    "one_line_assessment": "PLAが中間線越境を続けている。",
    "claims": [
        {"claim_type": "fact", "text": "PLA機10機が越境した", "source_ref": "event-1"},
        {"claim_type": "hypothesis", "text": "圧力の常態化を狙っている可能性", "source_ref": None},
    ],
    "stakeholders": [{"actor": "PLA", "purpose": "威嚇"}],
    "main_scenario": {"description": "圧力の段階的強化", "confidence": "中", "supporting_evidence": ["越境頻度の増加"]},
    "strongest_counterargument": "訓練の一環に過ぎない可能性がある",
    "retraction_conditions": ["越境頻度が減少した場合"],
    "business_impact": {"direct": None, "indirect": "サプライチェーンの警戒感上昇", "unrelated_note": None},
}

SOURCE_PACK_TEXT = "監視テーマ: 台湾有事\n- [verified] PLA が 越境（2026-08-01）"


def test_well_formed_response_passes_all_checks():
    result = guard.run_quality_guard(WELL_FORMED_RESPONSE, SOURCE_PACK_TEXT, VERSIONS)
    assert result["failed_count"] == 0
    assert result["passed_count"] == len(result["checks"])


def test_missing_claim_type_fails():
    response = {**WELL_FORMED_RESPONSE, "claims": [{"claim_type": "", "text": "不明"}]}
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "claims_have_claim_type" in failed


def test_missing_retraction_conditions_fails():
    response = {**WELL_FORMED_RESPONSE, "retraction_conditions": []}
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "has_retraction_conditions" in failed


def test_missing_versions_fails():
    incomplete_versions = {**VERSIONS, "knowledge_version": ""}
    result = guard.run_quality_guard(WELL_FORMED_RESPONSE, SOURCE_PACK_TEXT, incomplete_versions)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "versions_recorded" in failed


def test_forced_severity_when_unrelated_is_flagged():
    response = {**WELL_FORMED_RESPONSE,
                "business_impact": {"direct": "重大な影響がある", "unrelated_note": "関連性は薄い"}}
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "no_forced_severity_when_unrelated" in failed


# ---- Phase R1（Chunk6後P1.1、Guard再設計）: stakeholder_entity_resolved /
# stakeholder_relevance_explained（stakeholders_grounded_in_source_packを置き換え） ----
#
# 設計変更の背景: 旧stakeholders_grounded_in_source_packは「Source Pack本文にActor名の
# 文字列があるか」で判定していたが、実データ検証で「日本政府」「TSMC」等、クエリの主題から
# 自明に推論される正当なActorを繰り返し誤検知した。専門家分析では「入力に明示されていない
# 重要Actorを局面構造から発見すること」自体が価値であるため、Actor Legitimacy（実在・
# 妥当性）とIssue Relevance（局面との関連説明）を分離した2つのチェックへ置き換える。

def test_stakeholder_entity_resolved_flags_missing_actor_ref():
    response = {**WELL_FORMED_RESPONSE, "key_actor_profiles": [{"actor": "架空の組織X", "actor_ref": ""}]}
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "stakeholder_entity_resolved" in failed


def test_stakeholder_entity_resolved_flags_fabricated_entity_id():
    response = {**WELL_FORMED_RESPONSE,
                "key_actor_profiles": [{"actor": "X", "actor_ref": "x", "entity_id": "fake-id"}]}
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS,
                                      stage1_visible_entity_ids={"real-id-1", "real-id-2"})
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "stakeholder_entity_resolved" in failed


def test_stakeholder_entity_resolved_passes_for_self_assigned_ref_without_entity_id():
    """Chunk6のTSMC/台湾当局のような、entity_id紐付けの無い自己命名actor_refも
    正当として扱う（Source Pack文字列一致は要求しない）。"""
    response = {**WELL_FORMED_RESPONSE, "key_actor_profiles": [{"actor": "TSMC", "actor_ref": "TSMC"}]}
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "stakeholder_entity_resolved" not in failed


def test_stakeholder_entity_resolved_passes_for_known_entity_id():
    response = {**WELL_FORMED_RESPONSE,
                "key_actor_profiles": [{"actor": "X", "actor_ref": "x", "entity_id": "real-id-1"}]}
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS,
                                      stage1_visible_entity_ids={"real-id-1"})
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "stakeholder_entity_resolved" not in failed


def test_stakeholder_relevance_explained_flags_actor_without_rationale():
    response = {**WELL_FORMED_RESPONSE,
                "key_actor_profiles": [{"actor": "日本政府", "actor_ref": "japan_gov"}]}
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "stakeholder_relevance_explained" in failed


def test_stakeholder_relevance_explained_passes_with_why_actor_matters():
    """query_entailed（クエリの主題から自明に推論される）Actorでも、why_actor_matters等が
    あれば局面との関連が説明されているとみなす——Source Pack文字列一致は要求しない。"""
    response = {**WELL_FORMED_RESPONSE, "key_actor_profiles": [{
        "actor": "日本政府", "actor_ref": "japan_gov",
        "why_actor_matters": "高市政権の経済安全保障政策を制度化・執行する国家主体だから",
    }]}
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "stakeholder_relevance_explained" not in failed


def test_stakeholder_relevance_explained_accepts_formal_role_alone():
    response = {**WELL_FORMED_RESPONSE, "key_actor_profiles": [{
        "actor": "BIS", "actor_ref": "bis", "formal_role": "EARに基づく輸出管理を所掌する商務省内の局",
    }]}
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "stakeholder_relevance_explained" not in failed


# ---- v1.2(Session2): Evidence ID捏造防止 ----

def test_evidence_ids_not_fabricated_check_skipped_when_known_ids_none():
    result = guard.run_quality_guard(WELL_FORMED_RESPONSE, SOURCE_PACK_TEXT, VERSIONS)
    names = {c["name"] for c in result["checks"]}
    assert "evidence_ids_not_fabricated" not in names


def test_evidence_ids_not_fabricated_passes_when_ids_are_known():
    response = {**WELL_FORMED_RESPONSE, "key_actor_profiles": [{
        "actor_ref": "pla", "actor": "PLA", "confidence": "中", "evidence_ids": ["ev-1"],
    }]}
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS, known_ids={"ev-1", "event-1"})
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "evidence_ids_not_fabricated" not in failed


def test_evidence_ids_not_fabricated_flags_unknown_id():
    response = {**WELL_FORMED_RESPONSE, "key_actor_profiles": [{
        "actor_ref": "pla", "actor": "PLA", "confidence": "中", "evidence_ids": ["架空-999"],
    }]}
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS, known_ids={"ev-1"})
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "evidence_ids_not_fabricated" in failed


def test_evidence_ids_not_fabricated_flags_fabricated_claim_source_ref():
    """Phase 6C-0監査で発見: claims[].source_refはfact_claims_have_source_ref_checkで
    非null判定のみ行われ、捏造検出(_collect_evidence_ids)の対象に一度も含まれていな
    かった。Fact主張が実在しないsource_refを指しても以前は無条件でpassしていた。"""
    response = {**WELL_FORMED_RESPONSE, "claims": [
        {"claim_type": "fact", "text": "PLA機10機が越境した", "source_ref": "架空-event-999"},
    ]}
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS, known_ids={"event-1"})
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "evidence_ids_not_fabricated" in failed


def test_evidence_ids_not_fabricated_passes_when_claim_source_ref_is_known():
    response = {**WELL_FORMED_RESPONSE, "claims": [
        {"claim_type": "fact", "text": "PLA機10機が越境した", "source_ref": "event-1"},
    ]}
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS, known_ids={"event-1"})
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "evidence_ids_not_fabricated" not in failed


def test_evidence_ids_not_fabricated_allows_null_ids_for_persistent_preference_evidence():
    """persistent_preference_evidenceはDB外の過去事実を根拠にすることが多く、
    claim_id/evidence_idが両方nullでもevent_or_behavior+sourceがあれば捏造とみなさない。"""
    response = {**WELL_FORMED_RESPONSE, "key_actor_profiles": [{
        "actor_ref": "pla", "actor": "PLA", "confidence": "中",
        "persistent_preference_evidence": [{
            "event_or_behavior": "過去の越境頻度増加", "supports_or_contradicts": "supports",
            "source": "過去のEvent記録", "claim_id": None, "evidence_id": None,
        }],
    }]}
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS, known_ids={"event-1"})
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "evidence_ids_not_fabricated" not in failed


# ---- v1.2(Session2): モード別必須項目 ----

CASE_ANALYSIS_V12_RESPONSE = {
    **WELL_FORMED_RESPONSE,
    "main_scenario": {**WELL_FORMED_RESPONSE["main_scenario"], "selection_rationale": "過去水準比で有意な増加のため"},
    "key_point_assessment": {"primary_key_point": "PLAの越境頻度が段階的に引き上げられているか"},
    "key_stakeholder_assessment": {
        "determination": "single", "selected_actor_ids": ["pla"],
        "rationale": "軍事作戦の実施可否を決定できるのはPLAのため", "confidence": "中",
    },
    "key_actor_profiles": [{"actor_ref": "pla", "actor": "PLA", "confidence": "中"}],
}


def test_key_stakeholder_v12_check_passes_for_case_analysis_when_fields_present():
    result = guard.run_quality_guard(CASE_ANALYSIS_V12_RESPONSE, SOURCE_PACK_TEXT, VERSIONS, mode="case_analysis")
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "key_stakeholder_v12_fields_present" not in failed


def test_key_stakeholder_v12_check_fails_for_case_analysis_when_missing():
    result = guard.run_quality_guard(WELL_FORMED_RESPONSE, SOURCE_PACK_TEXT, VERSIONS, mode="case_analysis")
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "key_stakeholder_v12_fields_present" in failed


def test_key_stakeholder_v12_check_not_added_for_analyst_coaching():
    result = guard.run_quality_guard(WELL_FORMED_RESPONSE, SOURCE_PACK_TEXT, VERSIONS, mode="analyst_coaching")
    names = {c["name"] for c in result["checks"]}
    assert "key_stakeholder_v12_fields_present" not in names


def test_key_stakeholder_v12_check_allows_zero_key_actors_when_unclear_with_gaps():
    """determination=unclearの場合、Key Actorを特定できないことを無理に埋めさせず、
    rationale+identified_knowledge_gapsが揃っていれば合格を許容する（情報不足時の強制生成防止）。"""
    response = {
        **WELL_FORMED_RESPONSE,
        "main_scenario": {**WELL_FORMED_RESPONSE["main_scenario"], "selection_rationale": "根拠あり"},
        "key_point_assessment": {"primary_key_point": "意思決定者を特定できるかどうか"},
        "key_stakeholder_assessment": {
            "determination": "unclear", "selected_actor_ids": [],
            "rationale": "現時点でどの主体が実質的決定権を持つか判別できる情報が無い", "confidence": "低",
        },
        "key_actor_profiles": [],
        "identified_knowledge_gaps": [{"description": "意思決定者を特定するための追加情報が必要"}],
    }
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS, mode="case_analysis")
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "key_stakeholder_v12_fields_present" not in failed


def test_key_stakeholder_v12_check_fails_when_unclear_without_gaps():
    response = {
        **WELL_FORMED_RESPONSE,
        "main_scenario": {**WELL_FORMED_RESPONSE["main_scenario"], "selection_rationale": "根拠あり"},
        "key_stakeholder_assessment": {
            "determination": "unclear", "selected_actor_ids": [],
            "rationale": "判別できない", "confidence": "低",
        },
        "key_actor_profiles": [],
        "identified_knowledge_gaps": [],
    }
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS, mode="case_analysis")
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "key_stakeholder_v12_fields_present" in failed


def test_research_design_v12_check_allows_empty_audience_with_recorded_gap():
    response = {
        **WELL_FORMED_RESPONSE,
        "key_point_assessment": {"primary_key_point": "PLAの越境頻度が段階的に引き上げられているか"},
        "key_stakeholder_assessment": {
            "determination": "single", "selected_actor_ids": ["pla"],
            "rationale": "軍事作戦の実施可否を決定できるのはPLAのため", "confidence": "中",
        },
        "additional_research": [{"question": "過去のPersistent Preferenceを裏付ける類似局面はあるか"}],
        "audience_analysis": [],
        "identified_knowledge_gaps": [{"description": "Audience仮説を立てるための追加情報が必要"}],
    }
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS, mode="research_design")
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "research_design_v12_fields_present" not in failed


def test_draft_review_root_premise_check_requires_critical_finding_when_overall_fail():
    response = {
        **WELL_FORMED_RESPONSE,
        "root_premise_check": {
            "key_stakeholder_validity": {"result": "fail", "assessment": "Key Stakeholderの設定が誤り"},
            "persistent_preference_grounding": {"result": "warning", "assessment": "根拠が薄い"},
            "objective_hierarchy_separation": {"result": "pass", "assessment": "分離されている"},
            "preference_action_gap_analysis": {"result": "not_assessable", "assessment": "判断不能"},
            "observed_action_not_treated_as_ultimate_objective": {"result": "pass", "assessment": "問題なし"},
            "overall_result": "fail",
        },
        "review_findings": [{"severity": "Minor", "finding": "表現を調整"}],
    }
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS, mode="draft_review")
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "draft_review_root_premise_check" in failed


def test_executive_brief_v12_check_passes_with_summary_and_rationale():
    response = {
        **WELL_FORMED_RESPONSE,
        "key_stakeholder_assessment": {
            "determination": "single", "selected_actor_ids": ["pla"],
            "rationale": "局面を動かせるのはPLAのため", "confidence": "中",
        },
        "executive_summary": {"what_changed": "越境頻度が増加", "why_it_matters": None, "what_to_watch_decide": None},
    }
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS, mode="executive_brief")
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "executive_brief_v12_fields_present" not in failed


# ---- Tier1(Eguchi Expert AI Final v1.0対応): directional_perceptions grounding ----

def test_directional_perceptions_empty_passes():
    response = {**WELL_FORMED_RESPONSE, "key_actor_profiles": [{"actor_ref": "pla", "actor": "PLA", "confidence": "中"}]}
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "directional_perceptions_grounded" not in failed
    assert "directional_perception_not_self_referential" not in failed


def test_directional_perceptions_fabricated_actor_ref_fails():
    response = {
        **WELL_FORMED_RESPONSE,
        "key_actor_profiles": [{"actor_ref": "pla", "actor": "PLA", "confidence": "中"}],
        "directional_perceptions": [{
            "observer_actor_ref": "pla", "target_actor_ref": "架空-actor",
            "perception": "台湾は単独では抵抗できないと見ている", "claim_type": "analyst_hypothesis",
            "confidence": "低",
        }],
    }
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "directional_perceptions_grounded" in failed


def test_directional_perceptions_valid_pair_passes():
    response = {
        **WELL_FORMED_RESPONSE,
        "key_actor_profiles": [
            {"actor_ref": "pla", "actor": "PLA", "confidence": "中"},
            {"actor_ref": "taiwan", "actor": "Taiwan", "confidence": "中"},
        ],
        "directional_perceptions": [{
            "observer_actor_ref": "pla", "target_actor_ref": "taiwan",
            "perception": "台湾は単独では抵抗できないと見ている", "claim_type": "inferred_from_behavior",
            "confidence": "低",
        }],
    }
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "directional_perceptions_grounded" not in failed
    assert "directional_perception_not_self_referential" not in failed


def test_directional_perceptions_self_referential_fails():
    response = {
        **WELL_FORMED_RESPONSE,
        "key_actor_profiles": [{"actor_ref": "pla", "actor": "PLA", "confidence": "中"}],
        "directional_perceptions": [{
            "observer_actor_ref": "pla", "target_actor_ref": "pla",
            "perception": "自らの立場を過大評価している", "claim_type": "analyst_hypothesis",
            "confidence": "低",
        }],
    }
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "directional_perception_not_self_referential" in failed


# ---- Phase D-1(Holistic Reasoning Architecture Review): 共有key接続性Guard（2026-09-07追加）----

def test_event_role_intended_target_absent_passes():
    response = {**WELL_FORMED_RESPONSE, "key_actor_profiles": [{"actor_ref": "pla", "actor": "PLA", "confidence": "中"}]}
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "event_role_intended_target_grounded" not in failed


def test_event_role_intended_target_fabricated_actor_ref_fails():
    response = {
        **WELL_FORMED_RESPONSE,
        "key_actor_profiles": [{
            "actor_ref": "pla", "actor": "PLA", "confidence": "中",
            "event_role": {"role_type": "signal", "explanation": "対外的圧力の誇示",
                            "intended_target_actor_ref": "架空-actor"},
        }],
    }
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "event_role_intended_target_grounded" in failed


def test_event_role_intended_target_valid_ref_passes():
    response = {
        **WELL_FORMED_RESPONSE,
        "key_actor_profiles": [
            {"actor_ref": "pla", "actor": "PLA", "confidence": "中",
             "event_role": {"role_type": "signal", "explanation": "対外的圧力の誇示",
                             "intended_target_actor_ref": "taiwan"}},
            {"actor_ref": "taiwan", "actor": "Taiwan", "confidence": "中"},
        ],
    }
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "event_role_intended_target_grounded" not in failed


def test_actor_analyst_expected_response_target_absent_passes():
    response = {**WELL_FORMED_RESPONSE, "key_actor_profiles": [{"actor_ref": "pla", "actor": "PLA", "confidence": "中"}]}
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "actor_analyst_expected_response_target_grounded" not in failed


def test_actor_analyst_expected_response_target_fabricated_actor_ref_fails():
    response = {
        **WELL_FORMED_RESPONSE,
        "key_actor_profiles": [{"actor_ref": "pla", "actor": "PLA", "confidence": "中"}],
        "situation_actor_assessment": [{
            "issue_component": "越境頻度", "actor_ref": "pla", "salience": "high",
            "priority_rationale": "r", "effect_on_actor_objective": "e",
            "instrumental_value": "instrumental", "confidence": "中",
            "actor_expected_response": "台湾は静観するはずだと考えている",
            "analyst_expected_response": "実際には抗議声明を出すと予測する",
            "target_actor_ref": "架空-actor",
        }],
    }
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "actor_analyst_expected_response_target_grounded" in failed


def test_actor_analyst_expected_response_target_valid_ref_passes():
    response = {
        **WELL_FORMED_RESPONSE,
        "key_actor_profiles": [
            {"actor_ref": "pla", "actor": "PLA", "confidence": "中"},
            {"actor_ref": "taiwan", "actor": "Taiwan", "confidence": "中"},
        ],
        "situation_actor_assessment": [{
            "issue_component": "越境頻度", "actor_ref": "pla", "salience": "high",
            "priority_rationale": "r", "effect_on_actor_objective": "e",
            "instrumental_value": "instrumental", "confidence": "中",
            "actor_expected_response": "台湾は静観するはずだと考えている",
            "analyst_expected_response": "実際には抗議声明を出すと予測する",
            "target_actor_ref": "taiwan",
        }],
    }
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "actor_analyst_expected_response_target_grounded" not in failed


def test_scenario_derived_from_hypothesis_absent_passes():
    result = guard.run_quality_guard(WELL_FORMED_RESPONSE, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "scenario_derived_from_hypothesis_grounded" not in failed


def test_scenario_derived_from_hypothesis_fabricated_id_fails():
    response = {
        **WELL_FORMED_RESPONSE,
        "main_scenario": {**WELL_FORMED_RESPONSE["main_scenario"], "derived_from_hypothesis_id": "H9-架空"},
        "hypothesis_register": [{"hypothesis_id": "H0", "description": "surface", "status": "retained"}],
    }
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "scenario_derived_from_hypothesis_grounded" in failed


def test_scenario_derived_from_hypothesis_valid_id_passes():
    response = {
        **WELL_FORMED_RESPONSE,
        "main_scenario": {**WELL_FORMED_RESPONSE["main_scenario"], "derived_from_hypothesis_id": "H0"},
        "hypothesis_register": [{"hypothesis_id": "H0", "description": "surface", "status": "retained"}],
    }
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "scenario_derived_from_hypothesis_grounded" not in failed


# ---- Focus Selection / Attention Prioritization（2026-09-06追加）----

def _wf_with_key_point(**kpa_overrides):
    kpa = {
        "candidate_key_points": [], "primary_key_point": "p", "selection_rationale": "r",
        "hinge_structure": {"primary_hinge": "h"}, "control_type": "actor_controlled",
        "supporting_evidence_ids": ["event-1"], "disconfirming_signal": "d", "confidence": "中",
    }
    kpa.update(kpa_overrides)
    return {**WELL_FORMED_RESPONSE, "key_point_assessment": kpa}


def test_focus_selection_absent_passes():
    response = _wf_with_key_point()
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "focus_selection_why_now_present" not in failed


def test_focus_selection_without_trigger_types_does_not_require_why_now():
    response = _wf_with_key_point(focus_selection={"trigger_types": None, "why_now": None,
                                                     "decision_sensitive_outcome": None})
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "focus_selection_why_now_present" not in failed


def test_focus_selection_with_trigger_types_requires_why_now_and_decision_sensitive_outcome():
    response = _wf_with_key_point(focus_selection={
        "trigger_types": ["decision_power"], "why_now": None, "decision_sensitive_outcome": None,
    })
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "focus_selection_why_now_present" in failed


def test_focus_selection_invalid_trigger_type_fails_enum_check():
    response = _wf_with_key_point(focus_selection={
        "trigger_types": ["this_is_not_a_real_trigger"], "why_now": "x", "decision_sensitive_outcome": "y",
    })
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "focus_selection_trigger_types_within_enum" in failed


def test_focus_selection_anomaly_conclusive_language_fails():
    response = _wf_with_key_point(focus_selection={
        "trigger_types": ["media_or_source_anomaly"],
        "why_now": "この報道の扱いの偏りは意図的な世論操作であることが確定した。",
        "decision_sensitive_outcome": "y",
    })
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "focus_selection_anomaly_not_treated_as_conclusion" in failed


def test_focus_selection_anomaly_investigative_language_passes():
    response = _wf_with_key_point(focus_selection={
        "trigger_types": ["media_or_source_anomaly"],
        "why_now": "この報道の扱いの偏りは意図的な世論操作の可能性があり、追加調査が必要。",
        "decision_sensitive_outcome": "y",
    })
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "focus_selection_anomaly_not_treated_as_conclusion" not in failed


# A. prior norm mismatch: 通常理解と新観測の衝突がfocus triggerになり得る
def test_focus_selection_A_prior_norm_mismatch_is_valid_trigger():
    response = _wf_with_key_point(focus_selection={
        "trigger_types": ["model_or_norm_mismatch"],
        "why_now": "従来のActor Modelでは説明できない行動が観測されたため。",
        "decision_sensitive_outcome": "この不一致の解釈次第でMain Scenarioの前提が変わる。",
    })
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert not (failed & {"focus_selection_why_now_present", "focus_selection_trigger_types_within_enum",
                          "focus_selection_anomaly_not_treated_as_conclusion"})


# B. higher-order objective: issue-level actionだけでPrimary Focusを決めない
def test_focus_selection_B_higher_order_objective_beyond_issue_level():
    response = _wf_with_key_point(focus_selection={
        "trigger_types": ["higher_order_objective"],
        "why_now": "今回の措置は、争点そのものではなくより上位の対外交渉力確保が目的である可能性。",
        "decision_sensitive_outcome": "上位目的が正しければ、今回の措置単体の成否とは別に交渉が続く。",
    })
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "focus_selection_why_now_present" not in failed


# C. implementation constraint: 予算額だけでなく用途・人員・能力が結果を左右する
def test_focus_selection_C_implementation_feasibility_beyond_budget_figure():
    response = _wf_with_key_point(focus_selection={
        "trigger_types": ["implementation_or_feasibility"],
        "why_now": "予算は承認されたが、実施に必要な人員・専門能力・物流が確保できるかが未確認。",
        "decision_sensitive_outcome": "実施能力が無ければ、合意の実効性そのものが変わる。",
    })
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "focus_selection_why_now_present" not in failed


# D. changed premise: 過去patternの前提が失われた場合、既存のEvidence Class規律
#    （cap_confidence_by_evidence_class）はfocus_selectionの有無に関わらず変わらず機能する
def test_focus_selection_D_changed_premise_does_not_bypass_existing_evidence_class_discipline():
    response = {
        "situation_actor_assessment": [{
            "actor_ref": "a", "confidence": "高", "evidence_class": "historical_actor_pattern",
        }],
        "key_point_assessment": {
            "candidate_key_points": [], "primary_key_point": "p", "selection_rationale": "r",
            "hinge_structure": {"primary_hinge": "h"}, "control_type": "actor_controlled",
            "supporting_evidence_ids": ["event-1"], "disconfirming_signal": "d", "confidence": "中",
            "focus_selection": {
                "trigger_types": ["premise_or_constraint_change"],
                "why_now": "従来この行動を説明していた前提条件が失われた可能性がある。",
                "decision_sensitive_outcome": "前提が崩れていれば過去パターンをそのまま適用できない。",
            },
        },
    }
    adjusted = guard.cap_confidence_by_evidence_class(response)
    assert response["situation_actor_assessment"][0]["confidence"] == "低"
    assert len(adjusted) == 1


# E. discriminating hinge: A/B両方で同じ観測はPrimary discriminatorにしない
#    （discriminating_observationが空でもwhy_now/decision_sensitive_outcomeが埋まっていれば
#    focus_selection_why_now_presentは失敗しない——discriminating_observationは必須要件外）
def test_focus_selection_E_discriminating_hinge_does_not_require_discriminating_observation():
    response = _wf_with_key_point(focus_selection={
        "trigger_types": ["discriminating_hinge"],
        "why_now": "候補AとBは大半の観測を同程度に説明するため、分岐点だけが判断材料になる。",
        "decision_sensitive_outcome": "分岐点の観測次第でAかBかが決まる。",
        "discriminating_observation": None,
    })
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "focus_selection_why_now_present" not in failed


# F. multiple valid frames: alternative frameをFalse扱いせず、not-primary-nowとして保持できる
def test_focus_selection_F_rejected_candidates_not_penalized_as_false():
    response = _wf_with_key_point(
        candidate_key_points=[
            {"key_point": "k1", "why_it_could_be_key": "w1", "control_type": "actor_controlled",
             "what_it_explains": "e1", "what_it_does_not_explain": "n1"},
            {"key_point": "k2", "why_it_could_be_key": "w2", "control_type": "structural",
             "what_it_explains": "e2", "what_it_does_not_explain": "n2"},
            {"key_point": "k3", "why_it_could_be_key": "w3", "control_type": "distributed",
             "what_it_explains": "e3", "what_it_does_not_explain": "n3"},
        ],
        primary_key_point="k1",
        rejected_candidates=[{"key_point": "k2", "why_not_primary": "他のFrameも成立し得るが、今の局面ではk1の方がdecision-sensitive"}],
        focus_selection={
            "trigger_types": ["decision_power"],
            "why_now": "k2/k3も妥当な解釈だが、今回の判断に直結するのはk1の論点である。",
            "decision_sensitive_outcome": "k1の評価が変わればActorの次の行動予測が変わる。",
        },
    )
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert not (failed & {"focus_selection_why_now_present", "focus_selection_trigger_types_within_enum",
                          "focus_selection_anomaly_not_treated_as_conclusion",
                          "key_point_candidates_not_homogeneous"})


# ---- Phase D-4b(Holistic Reasoning Architecture Review): Human Interaction chain
# completeness / Hypothesis→Scenario provenance / Control Point→Decision Maker
# canonical resolution（2026-09-07追加）----

# -- human_interaction_chain_completeness --

def test_human_interaction_chain_not_applicable_when_no_data():
    result = guard._human_interaction_chain_completeness_check({})
    assert result["passed"] is True
    assert "NOT_APPLICABLE" in result["detail"]


def test_human_interaction_chain_partial_evidence_gap_when_only_directional_perception():
    response = {
        "key_actor_profiles": [{"actor_ref": "a"}, {"actor_ref": "b"}],
        "directional_perceptions": [{"observer_actor_ref": "a", "target_actor_ref": "b"}],
        "situation_actor_assessment": [],
    }
    result = guard._human_interaction_chain_completeness_check(response)
    assert result["passed"] is True
    assert "PARTIAL_EVIDENCE_GAP" in result["detail"]


def test_human_interaction_chain_complete_when_all_links_connect():
    response = {
        "key_actor_profiles": [
            {"actor_ref": "a", "event_role": {"role_type": "signal", "explanation": "e",
                                               "intended_target_actor_ref": "b"}},
            {"actor_ref": "b"},
        ],
        "directional_perceptions": [{"observer_actor_ref": "a", "target_actor_ref": "b"}],
        "situation_actor_assessment": [{
            "actor_ref": "a", "target_actor_ref": "b",
            "actor_expected_response": "静観するはず", "analyst_expected_response": "抗議するはず",
        }],
    }
    result = guard._human_interaction_chain_completeness_check(response)
    assert result["passed"] is True
    assert "COMPLETE" in result["detail"]


def test_human_interaction_chain_partial_when_only_some_pairs_complete():
    response = {
        "key_actor_profiles": [
            {"actor_ref": "a", "event_role": {"role_type": "signal", "explanation": "e",
                                               "intended_target_actor_ref": "b"}},
            {"actor_ref": "c"},
        ],
        "directional_perceptions": [
            {"observer_actor_ref": "a", "target_actor_ref": "b"},
            {"observer_actor_ref": "c", "target_actor_ref": "a"},
        ],
        "situation_actor_assessment": [{
            "actor_ref": "a", "target_actor_ref": "b",
            "actor_expected_response": "静観するはず", "analyst_expected_response": "抗議するはず",
        }],
    }
    result = guard._human_interaction_chain_completeness_check(response)
    assert result["passed"] is True
    assert "PARTIAL_EVIDENCE_GAP" in result["detail"]


def test_human_interaction_chain_invalid_link_on_event_role_self_reference():
    response = {
        "key_actor_profiles": [{"actor_ref": "a", "event_role": {
            "role_type": "signal", "explanation": "e", "intended_target_actor_ref": "a"}}],
    }
    result = guard._human_interaction_chain_completeness_check(response)
    assert result["passed"] is False
    assert "INVALID_LINK" in result["detail"]


def test_human_interaction_chain_invalid_link_on_situation_actor_assessment_self_reference():
    response = {
        "situation_actor_assessment": [{"actor_ref": "a", "target_actor_ref": "a",
                                         "actor_expected_response": "x", "analyst_expected_response": "y"}],
    }
    result = guard._human_interaction_chain_completeness_check(response)
    assert result["passed"] is False
    assert "INVALID_LINK" in result["detail"]


# -- scenario_derived_from_retained_hypothesis --

def test_scenario_derived_from_retained_hypothesis_not_applicable_when_not_deferred():
    result = guard._scenario_derived_from_retained_hypothesis_check({}, defer_scenario=False)
    assert result["passed"] is True
    assert "NOT_APPLICABLE" in result["detail"]


def test_scenario_derived_from_retained_hypothesis_not_applicable_when_fallback_used():
    result = guard._scenario_derived_from_retained_hypothesis_check(
        {}, defer_scenario=True, scenario_fallback_used=True)
    assert result["passed"] is True
    assert "NOT_APPLICABLE" in result["detail"]


def test_scenario_derived_from_retained_hypothesis_partial_gap_when_register_empty():
    result = guard._scenario_derived_from_retained_hypothesis_check(
        {"hypothesis_register": []}, defer_scenario=True)
    assert result["passed"] is True
    assert "PARTIAL_EVIDENCE_GAP" in result["detail"]


def test_scenario_derived_from_retained_hypothesis_partial_gap_when_none_retained():
    response = {"hypothesis_register": [{"hypothesis_id": "H0", "description": "d", "status": "rejected"}]}
    result = guard._scenario_derived_from_retained_hypothesis_check(response, defer_scenario=True)
    assert result["passed"] is True
    assert "PARTIAL_EVIDENCE_GAP" in result["detail"]


def test_scenario_derived_from_retained_hypothesis_invalid_link_when_missing():
    response = {
        "hypothesis_register": [{"hypothesis_id": "H0", "description": "d", "status": "retained"}],
        "main_scenario": {"description": "d", "confidence": "中"},
    }
    result = guard._scenario_derived_from_retained_hypothesis_check(response, defer_scenario=True)
    assert result["passed"] is False
    assert "INVALID_LINK" in result["detail"]


def test_scenario_derived_from_retained_hypothesis_invalid_link_when_referencing_rejected():
    response = {
        "hypothesis_register": [
            {"hypothesis_id": "H0", "description": "d", "status": "retained"},
            {"hypothesis_id": "H1", "description": "d", "status": "rejected"},
        ],
        "main_scenario": {"description": "d", "confidence": "中", "derived_from_hypothesis_id": "H1"},
    }
    result = guard._scenario_derived_from_retained_hypothesis_check(response, defer_scenario=True)
    assert result["passed"] is False
    assert "INVALID_LINK" in result["detail"]


def test_scenario_derived_from_retained_hypothesis_complete_when_referencing_retained():
    response = {
        "hypothesis_register": [{"hypothesis_id": "H0", "description": "d", "status": "retained"}],
        "main_scenario": {"description": "d", "confidence": "中", "derived_from_hypothesis_id": "H0"},
    }
    result = guard._scenario_derived_from_retained_hypothesis_check(response, defer_scenario=True)
    assert result["passed"] is True
    assert "COMPLETE" in result["detail"]


# -- Phase F(Human Interaction Analytical Gate) --

def test_human_interaction_gate_present_not_applicable_when_module_not_fired():
    result = guard._human_interaction_gate_present_check({})
    assert result["passed"] is True
    assert "NOT_APPLICABLE" in result["detail"]


def test_human_interaction_gate_present_fails_when_fired_but_null():
    response = {"discretion_assessment": None, "human_interaction_coverage": None}
    result = guard._human_interaction_gate_present_check(response)
    assert result["passed"] is False


def test_human_interaction_gate_present_fails_when_subfields_missing():
    response = {
        "discretion_assessment": {"discretion_mode": "HUMAN_DISCRETION_DOMINANT", "mode_rationale": None},
        "human_interaction_coverage": {"interaction_status": "GROUNDED", "status_rationale": ""},
    }
    result = guard._human_interaction_gate_present_check(response)
    assert result["passed"] is False


def test_human_interaction_gate_present_passes_when_fully_populated():
    response = {
        "discretion_assessment": {"discretion_mode": "HUMAN_DISCRETION_DOMINANT", "mode_rationale": "r"},
        "human_interaction_coverage": {"interaction_status": "GROUNDED", "status_rationale": "r"},
    }
    result = guard._human_interaction_gate_present_check(response)
    assert result["passed"] is True


def test_human_interaction_status_consistency_not_applicable_when_module_not_fired():
    result = guard._human_interaction_status_consistency_check({})
    assert result["passed"] is True
    assert "NOT_APPLICABLE" in result["detail"]


def test_human_interaction_status_consistency_fails_when_grounded_but_empty():
    response = {"human_interaction_coverage": {"interaction_status": "GROUNDED"}}
    result = guard._human_interaction_status_consistency_check(response)
    assert result["passed"] is False


def test_human_interaction_status_consistency_passes_when_grounded_with_data():
    response = {
        "human_interaction_coverage": {"interaction_status": "GROUNDED"},
        "directional_perceptions": [{"observer_actor_ref": "a", "target_actor_ref": "b"}],
    }
    result = guard._human_interaction_status_consistency_check(response)
    assert result["passed"] is True


def test_human_interaction_status_consistency_fails_when_partial_evidence_without_gaps():
    response = {
        "human_interaction_coverage": {"interaction_status": "PARTIAL_EVIDENCE"},
        "directional_perceptions": [{"observer_actor_ref": "a", "target_actor_ref": "b"}],
        "identified_knowledge_gaps": [],
    }
    result = guard._human_interaction_status_consistency_check(response)
    assert result["passed"] is False


def test_human_interaction_status_consistency_fails_when_insufficient_evidence_without_gaps():
    response = {
        "human_interaction_coverage": {"interaction_status": "INSUFFICIENT_EVIDENCE"},
        "identified_knowledge_gaps": [],
    }
    result = guard._human_interaction_status_consistency_check(response)
    assert result["passed"] is False


def test_human_interaction_status_consistency_passes_when_insufficient_evidence_with_gaps():
    response = {
        "human_interaction_coverage": {"interaction_status": "INSUFFICIENT_EVIDENCE"},
        "identified_knowledge_gaps": [{"description": "何が欠けているか"}],
    }
    result = guard._human_interaction_status_consistency_check(response)
    assert result["passed"] is True


def test_human_interaction_status_consistency_fails_when_not_applicable_but_mode_mismatched():
    response = {
        "human_interaction_coverage": {"interaction_status": "NOT_APPLICABLE_STRUCTURE_FIRST"},
        "discretion_assessment": {"discretion_mode": "HUMAN_DISCRETION_DOMINANT"},
    }
    result = guard._human_interaction_status_consistency_check(response)
    assert result["passed"] is False


def test_human_interaction_status_consistency_passes_when_not_applicable_and_structure_dominant():
    response = {
        "human_interaction_coverage": {"interaction_status": "NOT_APPLICABLE_STRUCTURE_FIRST"},
        "discretion_assessment": {"discretion_mode": "STRUCTURE_DOMINANT"},
    }
    result = guard._human_interaction_status_consistency_check(response)
    assert result["passed"] is True


def test_hypothesis_interaction_basis_grounded_passes_when_no_hypotheses():
    result = guard._hypothesis_interaction_basis_grounded_check({})
    assert result["passed"] is True


def test_hypothesis_interaction_basis_grounded_fails_on_fabricated_pair():
    response = {
        "directional_perceptions": [{"observer_actor_ref": "a", "target_actor_ref": "b"}],
        "hypothesis_register": [{
            "hypothesis_id": "H1", "description": "d", "status": "retained",
            "interaction_basis": {"basis_type": "DIRECTIONAL_PERCEPTION",
                                   "actor_ref": "a", "target_actor_ref": "z", "gap_note": None},
        }],
    }
    result = guard._hypothesis_interaction_basis_grounded_check(response)
    assert result["passed"] is False


def test_hypothesis_interaction_basis_grounded_passes_on_real_pair():
    response = {
        "directional_perceptions": [{"observer_actor_ref": "a", "target_actor_ref": "b"}],
        "hypothesis_register": [{
            "hypothesis_id": "H1", "description": "d", "status": "retained",
            "interaction_basis": {"basis_type": "DIRECTIONAL_PERCEPTION",
                                   "actor_ref": "a", "target_actor_ref": "b", "gap_note": None},
        }],
    }
    result = guard._hypothesis_interaction_basis_grounded_check(response)
    assert result["passed"] is True


def test_hypothesis_interaction_basis_grounded_fails_when_gap_note_missing():
    response = {
        "hypothesis_register": [{
            "hypothesis_id": "H3", "description": "d", "status": "retained",
            "interaction_basis": {"basis_type": "NOT_APPLICABLE",
                                   "actor_ref": None, "target_actor_ref": None, "gap_note": None},
        }],
    }
    result = guard._hypothesis_interaction_basis_grounded_check(response)
    assert result["passed"] is False


def test_hypothesis_interaction_basis_missing_not_applicable_when_status_insufficient():
    response = {
        "human_interaction_coverage": {"interaction_status": "INSUFFICIENT_EVIDENCE"},
        "hypothesis_register": [{
            "hypothesis_id": "H1", "description": "d", "status": "retained",
            "hypothesis_type": "H1_instrumental_indirect",
            "interaction_basis": {"basis_type": "EVENT_ROLE"},
        }],
    }
    result = guard._hypothesis_interaction_basis_missing_check(response)
    assert result["passed"] is True
    assert "NOT_APPLICABLE" in result["detail"]


def test_hypothesis_interaction_basis_missing_flags_h1_bypassing_via_event_role():
    response = {
        "human_interaction_coverage": {"interaction_status": "GROUNDED"},
        "hypothesis_register": [{
            "hypothesis_id": "H1", "description": "d", "status": "retained",
            "hypothesis_type": "H1_instrumental_indirect",
            "interaction_basis": {"basis_type": "EVENT_ROLE"},
        }],
    }
    result = guard._hypothesis_interaction_basis_missing_check(response)
    assert result["passed"] is False


def test_hypothesis_interaction_basis_missing_passes_when_h1_uses_directional_perception():
    response = {
        "human_interaction_coverage": {"interaction_status": "GROUNDED"},
        "hypothesis_register": [{
            "hypothesis_id": "H1", "description": "d", "status": "retained",
            "hypothesis_type": "H1_instrumental_indirect",
            "interaction_basis": {"basis_type": "DIRECTIONAL_PERCEPTION",
                                   "actor_ref": "a", "target_actor_ref": "b"},
        }],
    }
    result = guard._hypothesis_interaction_basis_missing_check(response)
    assert result["passed"] is True


def test_hypothesis_interaction_basis_missing_flags_h3_mentioning_actor_but_not_applicable():
    response = {
        "human_interaction_coverage": {"interaction_status": "GROUNDED"},
        "key_actor_profiles": [{"actor_ref": "a", "actor": "議会重鎮X"}],
        "hypothesis_register": [{
            "hypothesis_id": "H3", "description": "議会重鎮Xの支持基盤への配慮が主因",
            "status": "retained", "hypothesis_type": "H3_constraint_domestic_driver",
            "interaction_basis": {"basis_type": "NOT_APPLICABLE"},
        }],
    }
    result = guard._hypothesis_interaction_basis_missing_check(response)
    assert result["passed"] is False


def test_hypothesis_interaction_basis_missing_passes_when_h3_purely_structural():
    response = {
        "human_interaction_coverage": {"interaction_status": "GROUNDED"},
        "key_actor_profiles": [{"actor_ref": "a", "actor": "議会重鎮X"}],
        "hypothesis_register": [{
            "hypothesis_id": "H3", "description": "予算法上の期限が主因",
            "status": "retained", "hypothesis_type": "H3_constraint_domestic_driver",
            "interaction_basis": {"basis_type": "NOT_APPLICABLE"},
        }],
    }
    result = guard._hypothesis_interaction_basis_missing_check(response)
    assert result["passed"] is True


# -- control_point_decision_maker_resolution --

def test_control_point_resolution_not_applicable_when_structural():
    response = {"key_point_assessment": {"control_type": "structural",
                                          "controlling_entity_ids": ["e-1"]}}
    result = guard._control_point_decision_maker_resolution_check(response)
    assert result["passed"] is True
    assert "NOT_APPLICABLE" in result["detail"]


def test_control_point_resolution_not_applicable_when_no_controlling_entities():
    response = {"key_point_assessment": {"control_type": "actor_controlled", "controlling_entity_ids": []}}
    result = guard._control_point_decision_maker_resolution_check(response)
    assert result["passed"] is True
    assert "NOT_APPLICABLE" in result["detail"]


def test_control_point_resolution_complete_when_all_covered():
    response = {
        "key_point_assessment": {"control_type": "actor_controlled", "controlling_entity_ids": ["e-1"]},
        "key_actor_profiles": [{"actor_ref": "a", "entity_id": "e-1"}],
    }
    result = guard._control_point_decision_maker_resolution_check(response)
    assert result["passed"] is True
    assert "COMPLETE" in result["detail"]


def test_control_point_resolution_partial_when_some_covered():
    response = {
        "key_point_assessment": {"control_type": "distributed", "controlling_entity_ids": ["e-1", "e-2"]},
        "key_actor_profiles": [{"actor_ref": "a", "entity_id": "e-1"}],
    }
    result = guard._control_point_decision_maker_resolution_check(response)
    assert result["passed"] is True
    assert "PARTIAL_EVIDENCE_GAP" in result["detail"]


def test_control_point_resolution_invalid_link_when_none_covered():
    response = {
        "key_point_assessment": {"control_type": "mixed", "controlling_entity_ids": ["e-1"]},
        "key_actor_profiles": [{"actor_ref": "a", "entity_id": "e-9"}],
    }
    result = guard._control_point_decision_maker_resolution_check(response)
    assert result["passed"] is False


# ---- Phase 6D-2: Claim source_ref Referential Integrity ----

def test_resolve_claim_source_ref_retains_valid_ref():
    response = {"claims": [{"claim_type": "fact", "text": "x", "source_ref": "event-1"}]}
    resolutions = guard.resolve_claim_source_ref_referential_integrity(response, known_ids={"event-1"})
    assert resolutions == []
    assert response["claims"][0]["source_ref"] == "event-1"


def test_resolve_claim_source_ref_ignores_null_ref():
    response = {"claims": [{"claim_type": "hypothesis", "text": "x", "source_ref": None}]}
    resolutions = guard.resolve_claim_source_ref_referential_integrity(response, known_ids={"event-1"})
    assert resolutions == []
    assert response["claims"][0]["source_ref"] is None


def test_resolve_claim_source_ref_nulls_unknown_fabricated_ref():
    response = {"claims": [{"claim_type": "fact", "text": "PLA機が越境した", "source_ref": "架空-999"}]}
    resolutions = guard.resolve_claim_source_ref_referential_integrity(response, known_ids={"event-1"})
    assert response["claims"][0]["source_ref"] is None
    assert resolutions == [{
        "claim_text": "PLA機が越境した", "original_ref": "架空-999", "validity": "invalid",
        "resolution_method": "not_found_in_evidence_registry", "resolved_ref": None,
        "unresolved_reason": "known_ids(Evidence Registry)に存在しないIDです（捏造の可能性）",
    }]


def test_resolve_claim_source_ref_rejects_cross_namespace_entity_id():
    """US-China半導体Caseの実データ回帰: claims[].source_refがentity_id(Xi Jinpingの
    entity_id)を誤用していた実例。known_idsとして採用せず、原因をentity_id混同として
    識別した上でnull化する。"""
    response = {"claims": [{"claim_type": "official_claim", "text": "x",
                             "source_ref": "f34571d3-4742-41b7-8648-5b3ee13b7720"}]}
    resolutions = guard.resolve_claim_source_ref_referential_integrity(
        response, known_ids={"event-1"},
        stage1_visible_entity_ids={"f34571d3-4742-41b7-8648-5b3ee13b7720"})
    assert response["claims"][0]["source_ref"] is None
    assert resolutions[0]["resolution_method"] == "cross_namespace_id_rejected"


def test_resolve_claim_source_ref_never_guesses_a_replacement():
    """禁止事項の確認: invalidなsource_refに対し、known_ids内の別IDへ推測で
    rebindすることは一切行わない（常にNoneへ倒す）。"""
    response = {"claims": [{"claim_type": "fact", "text": "x", "source_ref": "架空-999"}]}
    resolutions = guard.resolve_claim_source_ref_referential_integrity(
        response, known_ids={"event-1", "event-2", "event-3"})
    assert response["claims"][0]["source_ref"] is None
    assert resolutions[0]["resolved_ref"] is None
