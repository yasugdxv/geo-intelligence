"""地政学専門家AI v1.2 段階実行(Staged Execution)のテスト。

geo_expert_engine.run_expert_request_staged（既存run_expert_requestとは独立に並存する
段階実行方式、戻り値の形はrun_expert_requestと統一されておりresponse_json等は
result["response"]["response_json"]に入る）が、以下を仕様通り満たすことをモックLLMで
検証する:
1. Stage1/Stage2モジュールのSchema合成が意図通りのproperties/requiredになっていること
2. プロファイル×モードのモジュール発火表通りにモジュールが選ばれること
3. 共有Call Budgetが実HTTP呼び出し回数を正しく数え、3回に達したら追加の修復を行わず
   incompleteで返すこと
4. Stage1・Stage2それぞれのSchemaにスコープした部分修復が、フルSchemaではなく該当
   スコープのみを対象にしていること
5. 最終マージ（stakeholders導出・action_substanceパッチ・alternative_scenariosの
   Stage2上書き・Evidence IDスクラビング）が正しく行われること
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import jsonschema
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_expert_engine as engine  # noqa: E402
import geo_expert_quality_guard as guard  # noqa: E402
import geo_expert_schema as schema  # noqa: E402
import geo_llm_common  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

THEME_ID = "theme-1"
BASE_TABLES = {
    "geo_themes": [{"theme_id": THEME_ID, "name_ja": "台湾有事", "code": "taiwan_strait"}],
    "geo_events": [{
        "event_id": "e-1", "theme_id": THEME_ID, "actor": "PLA", "action": "越境",
        "verified": True, "created_at": "2026-08-01T00:00:00+00:00",
    }],
}

STAGE1_RESPONSE = {
    "one_line_assessment": "PLAが越境を続けている。", "initial_anomaly": None,
    "claims": [{"claim_type": "fact", "text": "PLA機が越境した", "source_ref": "e-1"}],
    "dynamics": None, "decisive_power_and_leverage": None,
    "key_point_assessment": {
        "candidate_key_points": [{
            "key_point": "PLAの越境が段階的に常態化するか", "why_it_could_be_key": "頻度増加の主因",
            "control_type": "institutionally_constrained", "controlling_entity_ids": None,
            "control_structure_summary": "PLAの作戦方針・上級部隊の指揮系統に規定される",
            "what_it_explains": "越境頻度の増加傾向", "what_it_does_not_explain": "個別の停止条件",
            "supporting_evidence_ids": None, "control_type_adjustment_reason": None,
            "historical_supporting_evidence_ids": None, "supporting_knowledge_refs": None,
        }],
        "primary_key_point": "PLAの越境が段階的に常態化するか", "selection_rationale": "頻度データが最も一貫して説明する",
        "rejected_candidates": None,
        "hinge_structure": {"primary_hinge": "PLAの作戦方針", "secondary_hinges": None},
        "control_type": "institutionally_constrained", "controlling_entity_ids": None,
        "control_structure_summary": "PLAの作戦方針・上級部隊の指揮系統に規定される",
        "supporting_evidence_ids": ["e-1"], "contradicting_evidence_ids": None,
        "disconfirming_signal": "越境頻度が明確に減少に転じた場合", "confidence": "中",
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
        "determination": "single", "selected_actor_ids": ["pla"],
        "rationale": "軍事作戦の実施可否を決定できるのはPLAのため", "decision_structure": None,
        "confidence": "中", "evidence_ids": None,
    },
    "key_actor_profiles": [{
        "actor_ref": "pla", "actor": "PLA", "entity_id": None,
        "formal_role": "台湾海峡での軍事的圧力行使を担う実行部隊",
        "decision_power": None, "ability_to_move": None, "ability_to_block": None,
        "ultimate_objective": "圧力強化", "strategic_relationship_preference": None,
        "persistent_preference": None, "persistent_preference_evidence": [],
        "issue_objective": None, "observed_action": None, "preference_action_gap": None,
        "perceived_path": None, "event_role": None,
        "primary_audience": None, "secondary_audience": None, "driving_forces": [],
        "red_line": None, "confidence": "中", "evidence_ids": None,
        "persistent_preference_change": None,
        "why_actor_matters": None, "available_leverage": None,
        "unavailable_or_costly_cards": None, "vulnerabilities": None,
        "relevant_decision_patterns": None,
        "actor_intelligence_provenance": None, "pattern_applicability_assessment": None,
        "actor_model_challenge": {
            "challenge_question": "PLAの越境が本当に段階的常態化を意図したものか、単発の運用上の"
                                    "偶然である可能性はないか",
            "outcome": "inconclusive", "reasoning": None,
        },
        "confidence_adjustment_reason": None,
    }],
    "preference_action_gap_summary": None, "identified_knowledge_gaps": [],
    "main_scenario": {
        "description": "圧力の段階的強化", "confidence": "中", "supporting_evidence": ["頻度増加"],
        "selection_rationale": "過去水準比で有意な増加のため", "assumptions": None, "observable_indicators": None,
        "historical_analogies_used": None, "evidence_class": None, "confidence_adjustment_reason": None,
        "evidence_relevance": None, "weakening_evidence_relevance": None,
        "relevance_confidence_adjustment_reason": None,
        "derived_from_hypothesis_id": None,
    },
    "alternative_scenarios": [{"description": "訓練の一環", "confidence": "低", "switch_conditions": ["頻度が減少"]}],
    "strongest_counterargument": "訓練の一環の可能性", "weakening_information": None,
    "contradicting_evidence": None, "retraction_conditions": ["頻度が減少した場合"],
    "additional_research": [],
    "business_impact": {"direct": None, "indirect": "警戒感の上昇", "environment_change": None, "unrelated_note": None},
    "monitoring_indicators": ["越境頻度"], "unknown_or_uncertain": None, "executive_summary": None,
}


def _resp(content) -> MagicMock:
    m = MagicMock(choices=[MagicMock(message=MagicMock(content=json.dumps(content)))])
    m.usage = None
    return m


def _mock_client(*contents) -> MagicMock:
    c = MagicMock()
    c.chat.completions.create.side_effect = [_resp(x) for x in contents]
    return c


# ---- 1. Schema合成 ----

def test_stage1_schema_is_strict_all_required_and_valid():
    stage1 = schema.build_stage1_schema()
    jsonschema.Draft7Validator.check_schema(stage1)
    assert set(stage1["required"]) == set(stage1["properties"].keys())
    assert stage1["additionalProperties"] is False


def test_stage1_schema_excludes_stage2_owned_fields():
    stage1 = schema.build_stage1_schema()
    for owned_elsewhere in ("audience_analysis", "media_source_context", "root_premise_check",
                             "review_findings", "analyst_coaching_notes",
                             "stakeholders", "sources_and_citations", "knowledge_gaps_referenced",
                             "relevant_precedents", "relevant_mechanisms", "assets_dependencies_leverage"):
        assert owned_elsewhere not in stage1["properties"]


def test_stage1_key_actor_profiles_excludes_action_substance():
    stage1 = schema.build_stage1_schema()
    kap_item_props = stage1["properties"]["key_actor_profiles"]["items"]["properties"]
    assert "action_substance" not in kap_item_props


def test_stage1_requires_exactly_one_best_alternative_scenario_with_switch_conditions():
    stage1 = schema.build_stage1_schema()
    alt_item = stage1["properties"]["alternative_scenarios"]["items"]
    assert set(alt_item["required"]) == {"description", "confidence", "switch_conditions"}


# ---- Tier1(Eguchi Expert AI Final v1.0対応): perceived_path/event_role/directional_perception ----

def test_stage1_key_actor_profiles_includes_perceived_path_and_event_role():
    stage1 = schema.build_stage1_schema()
    kap_item_props = stage1["properties"]["key_actor_profiles"]["items"]["properties"]
    assert "perceived_path" in kap_item_props
    assert "event_role" in kap_item_props
    assert set(kap_item_props["event_role"]["required"]) == {
        "role_type", "explanation", "intended_target_actor_ref"}


def test_directional_perception_module_only_fires_for_deep_profile():
    assert "directional_perception" not in schema.resolve_stage2_modules("case_analysis", "quick")
    assert "directional_perception" not in schema.resolve_stage2_modules("case_analysis", "standard")
    assert "directional_perception" in schema.resolve_stage2_modules("case_analysis", "deep")
    assert "directional_perception" in schema.resolve_stage2_modules("theme_design", "deep")


def test_directional_perception_stage2_module_schema_is_strict_and_valid():
    s = schema.build_stage2_schema(["directional_perception"])
    jsonschema.Draft7Validator.check_schema(s)
    assert "directional_perceptions" in s["properties"]
    item_props = s["properties"]["directional_perceptions"]["items"]["properties"]
    assert {"observer_actor_ref", "target_actor_ref", "perception", "claim_type", "confidence"} <= set(item_props)


def test_situation_actor_assessment_includes_actor_and_analyst_expected_response():
    s = schema.build_stage2_schema(["situation_actor_assessment"])
    item_props = s["properties"]["situation_actor_assessment"]["items"]["properties"]
    assert "actor_expected_response" in item_props
    assert "analyst_expected_response" in item_props
    assert "likely_reaction" in item_props  # 後方互換のため残す
    assert "target_actor_ref" in item_props  # Phase D-1: 両フィールドで共有する接続キー


# ---- Focus Selection / Attention Prioritization（2026-09-06追加）----

def test_stage1_key_point_assessment_includes_focus_selection():
    stage1 = schema.build_stage1_schema()
    kpa_props = stage1["properties"]["key_point_assessment"]["properties"]
    assert "focus_selection" in kpa_props
    fs_props = kpa_props["focus_selection"]["properties"]
    assert {"trigger_types", "why_now", "decision_sensitive_outcome",
            "discriminating_observation"} <= set(fs_props)


def test_focus_selection_trigger_types_enum_matches_design():
    stage1 = schema.build_stage1_schema()
    fs_props = stage1["properties"]["key_point_assessment"]["properties"]["focus_selection"]["properties"]
    enum_values = set(fs_props["trigger_types"]["items"]["enum"])
    assert enum_values == {
        "model_or_norm_mismatch", "decision_power", "higher_order_objective",
        "behavior_driving_force", "audience_substance_gap", "premise_or_constraint_change",
        "implementation_or_feasibility", "causal_chain_break", "phase_or_time_horizon_shift",
        "discriminating_hinge", "media_or_source_anomaly", "other",
    }


def test_stage2_module_schemas_are_strict_and_valid():
    for name in schema.STAGE2_MODULES:
        s = schema.build_stage2_schema([name])
        jsonschema.Draft7Validator.check_schema(s)
        assert set(s["required"]) == set(s["properties"].keys())


def test_stage2_combined_module_schema_is_union_of_selected_modules():
    combined = schema.build_stage2_schema(["audience_signal", "review"])
    assert set(combined["properties"].keys()) == {
        "audience_analysis", "key_actor_action_substance", "root_premise_check", "review_findings",
    }


# ---- 2. プロファイル×モード ルーティング表 ----

def test_draft_review_and_outline_review_always_require_review_module_regardless_of_profile():
    for profile in schema.EXPERT_PROFILES:
        assert schema.resolve_stage2_modules("draft_review", profile) == ["review"] or \
            "review" in schema.resolve_stage2_modules("draft_review", profile)
        assert "review" in schema.resolve_stage2_modules("outline_review", profile)


def test_analyst_coaching_always_requires_analyst_learning_module_regardless_of_profile():
    for profile in schema.EXPERT_PROFILES:
        assert "analyst_learning" in schema.resolve_stage2_modules("analyst_coaching", profile)


def test_quick_profile_never_adds_optional_modules_beyond_mode_intrinsic():
    for mode in schema.MODES:
        modules = schema.resolve_stage2_modules(mode, "quick")
        assert modules == schema.MODE_INTRINSIC_STAGE2_MODULES.get(mode, [])


def test_standard_profile_triggers_audience_signal_and_deep_scenario_for_case_analysis():
    # v1.2 Phase A実データ比較で判明した代替仮説管理の不足を補うため、追加の実Azure呼び出しを
    # 増やさずcase_analysis+standardにdeep_scenarioを追加した
    # (docs/geopolitical-staged-execution-pilot-results.md参照)。
    assert set(schema.resolve_stage2_modules("case_analysis", "standard")) == {"audience_signal", "deep_scenario"}
    # Geopolitical Intelligence OS「周辺分析」タイル(ユーザー指示、2026-09-23):
    # research_design（IOS Analysis Sessionの既定mode/profile）にmedia_contextを追加した
    # ——media_source_contextが常にnullで「メディア」タイルが出せなかったため。
    assert schema.resolve_stage2_modules("research_design", "standard") == ["media_context"]
    assert schema.resolve_stage2_modules("executive_brief", "standard") == []


def test_deep_profile_triggers_more_modules_than_standard_for_case_analysis():
    standard_modules = set(schema.resolve_stage2_modules("case_analysis", "standard"))
    deep_modules = set(schema.resolve_stage2_modules("case_analysis", "deep"))
    assert standard_modules.issubset(deep_modules)
    assert len(deep_modules) > len(standard_modules)


# ---- 3. 共有Call Budget ----

def test_quick_profile_case_analysis_uses_exactly_one_call_when_stage1_succeeds():
    client = FakeSupabaseClient(tables=BASE_TABLES)
    azure_client = _mock_client(STAGE1_RESPONSE)
    result = engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "PLAの越境状況を分析して", profile="quick")
    assert azure_client.chat.completions.create.call_count == 1
    assert result["quality_evaluation"]["completeness_status"] == "complete"
    assert result["execution_metadata"]["executed"]["stage2_modules"] == []


def test_standard_profile_case_analysis_uses_two_calls_when_stage2_triggered():
    client = FakeSupabaseClient(tables=BASE_TABLES)
    stage2_combined = {
        "audience_analysis": [{"actor_ref": "pla", "action_or_statement": "越境継続",
                                "primary_audience": "台湾世論", "secondary_audience": None,
                                "message_hypothesis": None, "action_substance": None, "confidence": "中"}],
        "key_actor_action_substance": [],
        "alternative_scenarios": [{"description": "訓練の一環", "confidence": "低",
                                    "strengthening_conditions": None, "weakening_conditions": None,
                                    "switch_conditions": ["頻度が減少"],
                                    "evidence_class": None, "confidence_adjustment_reason": None,
                                    "derived_from_hypothesis_id": None}],
        "additional_stakeholders": [],
    }
    azure_client = _mock_client(STAGE1_RESPONSE, stage2_combined)
    result = engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "PLAの越境状況を分析して", profile="standard")
    assert azure_client.chat.completions.create.call_count == 2
    assert set(result["execution_metadata"]["executed"]["stage2_modules"]) == {"audience_signal", "deep_scenario"}
    assert result["response"]["response_json"]["audience_analysis"]


def test_call_budget_caps_total_real_calls_at_three_even_when_stage1_needs_recovery():
    client = FakeSupabaseClient(tables=BASE_TABLES)
    # 初回(1回消費)失敗→リカバリ(max_attempts=2、最大2回消費)失敗、合計3回で打ち止め
    azure_client = _mock_client("not json", "still not json", "still bad")
    azure_client.chat.completions.create.side_effect = [
        MagicMock(choices=[MagicMock(message=MagicMock(content="not json"))], usage=None),
        MagicMock(choices=[MagicMock(message=MagicMock(content="still not json"))], usage=None),
        MagicMock(choices=[MagicMock(message=MagicMock(content="still bad"))], usage=None),
    ]
    try:
        engine.run_expert_request_staged(
            client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "クエリ", profile="standard")
        assert False, "GeoLLMErrorが発生するはず"
    except geo_llm_common.GeoLLMError:
        pass
    assert azure_client.chat.completions.create.call_count == 3


def test_budget_exhausted_after_stage1_recovery_skips_repair_but_stage2_gets_last_slot():
    """Phase 7-1C-R2でCallBudget(total)を3→4へ拡張したことの再確認（ユーザー指示
    「Important edge case」の一種）: Stage1が2回のリカバリを要してbudgetを3消費した
    場合でも、total=4の下では最後の1枠がStage2（このテストのdraft_reviewはdefer_scenario
    対象外＝Stage2 Recovery無変更の既存経路）に回る。そのStage2呼び出しも失敗すれば、
    残りbudgetが0になりRepairは実施できないままであることを確認する（total=3時代は
    Stage2自体が0枠で呼ばれなかったが、total=4ではStage2は呼ばれた上で失敗する、という
    違いを明示的に検証する）。"""
    client = FakeSupabaseClient(tables=BASE_TABLES)
    azure_client = MagicMock()
    azure_client.chat.completions.create.side_effect = [
        MagicMock(choices=[MagicMock(message=MagicMock(content="not json"))], usage=None),
        MagicMock(choices=[MagicMock(message=MagicMock(content="still not json"))], usage=None),
        MagicMock(choices=[MagicMock(message=MagicMock(content=json.dumps(STAGE1_RESPONSE)))], usage=None),
        MagicMock(choices=[MagicMock(message=MagicMock(content="stage2 not json"))], usage=None),
    ]
    result = engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "draft_review", THEME_ID, "PLAの越境をレビューして",
        draft_text="draft", profile="standard")
    assert azure_client.chat.completions.create.call_count == 4
    assert result["quality_evaluation"]["completeness_status"] == "incomplete_budget_exhausted"
    stages_run = [m["stage"] for m in result["execution_metadata"]["stage_metrics"]]
    assert any(s.startswith("stage2") for s in stages_run)
    assert not any(s.startswith("repair") for s in stages_run)
    assert result["execution_metadata"]["repair_skipped_due_to_budget"] is True
    assert result["execution_metadata"]["calls_used"] == 4
    assert result["execution_metadata"]["call_budget_remaining"] == 0


# ---- 4. 部分修復のスコープ ----

def test_repair_targets_stage2_schema_only_when_review_module_fails():
    client = FakeSupabaseClient(tables=BASE_TABLES)
    fixed_review = {
        "root_premise_check": {
            "key_stakeholder_validity": {"result": "pass", "assessment": "妥当", "evidence_ids": None},
            "persistent_preference_grounding": {"result": "warning", "assessment": "根拠が1件のみ", "evidence_ids": None},
            "objective_hierarchy_separation": {"result": "pass", "assessment": "妥当", "evidence_ids": None},
            "preference_action_gap_analysis": {"result": "pass", "assessment": "妥当", "evidence_ids": None},
            "observed_action_not_treated_as_ultimate_objective": {"result": "pass", "assessment": "妥当", "evidence_ids": None},
            "overall_result": "warning",
        },
        "review_findings": [{"severity": "Minor", "category": None, "finding": "根拠を補強", "recommendation": None, "evidence_ids": None}],
    }
    # Stage2初回はSchema検証失敗(不正なJSON)→swallowされStage1のみで継続→
    # draft_review_root_premise_checkが失敗→repairはstage2 schemaのみを対象に実行
    azure_client = MagicMock()
    azure_client.chat.completions.create.side_effect = [
        MagicMock(choices=[MagicMock(message=MagicMock(content=json.dumps(STAGE1_RESPONSE)))], usage=None),
        MagicMock(choices=[MagicMock(message=MagicMock(content="not valid json"))], usage=None),
        MagicMock(choices=[MagicMock(message=MagicMock(content=json.dumps(fixed_review)))], usage=None),
    ]
    result = engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "draft_review", THEME_ID, "PLAの越境をレビューして",
        draft_text="draft", profile="standard")
    assert azure_client.chat.completions.create.call_count == 3
    assert result["quality_evaluation"]["repair_attempted"] is True
    assert result["quality_evaluation"]["completeness_status"] == "complete"
    assert result["response"]["response_json"]["root_premise_check"]["overall_result"] == "warning"
    # Stage1由来のone_line_assessmentは修復呼び出しの影響を受けず維持されている
    assert result["response"]["response_json"]["one_line_assessment"] == STAGE1_RESPONSE["one_line_assessment"]


def test_repair_triggers_and_targets_stage1_when_event_role_intended_target_fails():
    """Phase D-4a: event_role_intended_target_groundedがMODE_V12_CHECK_NAMESへ昇格
    されたことにより、event_role.intended_target_actor_refの捏造がRepairをトリガーし、
    stage1 schemaのみを対象に再生成されることを確認する（参照整合性のみ、意味評価は
    行わない——修復後の値がnullでも他のactor_refでも通す設計自体は変えない）。"""
    client = FakeSupabaseClient(tables=BASE_TABLES)
    tainted = json.loads(json.dumps(STAGE1_RESPONSE))
    tainted["key_actor_profiles"][0]["event_role"] = {
        "role_type": "signal", "explanation": "対外的圧力の誇示", "intended_target_actor_ref": "架空-actor",
    }
    fixed = json.loads(json.dumps(tainted))
    fixed["key_actor_profiles"][0]["event_role"]["intended_target_actor_ref"] = None
    azure_client = _mock_client(tainted, fixed)
    result = engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "PLAの越境について", profile="quick")
    assert azure_client.chat.completions.create.call_count == 2
    assert result["quality_evaluation"]["repair_attempted"] is True
    assert result["quality_evaluation"]["completeness_status"] == "complete"
    assert result["response"]["response_json"]["key_actor_profiles"][0]["event_role"]["intended_target_actor_ref"] is None


def test_repair_triggers_and_targets_stage1_when_scenario_derived_from_hypothesis_fails():
    """Phase D-4a: scenario_derived_from_hypothesis_groundedの昇格により、
    main_scenario.derived_from_hypothesis_idの捏造（quickプロファイルではhypothesis_
    register自体が存在しないため、どんな値でも未知参照になる）がRepairをトリガーし、
    stage1 schemaのみを対象に再生成されることを確認する。"""
    client = FakeSupabaseClient(tables=BASE_TABLES)
    tainted = json.loads(json.dumps(STAGE1_RESPONSE))
    tainted["main_scenario"]["derived_from_hypothesis_id"] = "H9-架空"
    fixed = json.loads(json.dumps(tainted))
    fixed["main_scenario"]["derived_from_hypothesis_id"] = None
    azure_client = _mock_client(tainted, fixed)
    result = engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "PLAの越境について", profile="quick")
    assert azure_client.chat.completions.create.call_count == 2
    assert result["quality_evaluation"]["repair_attempted"] is True
    assert result["quality_evaluation"]["completeness_status"] == "complete"
    assert result["response"]["response_json"]["main_scenario"]["derived_from_hypothesis_id"] is None


def test_v12_check_stage_routes_actor_analyst_expected_response_target_to_stage1():
    """Phase D-4a→D-3更新: situation_actor_assessment（target_actor_refを含む）は
    Phase D-3以降、発火する局面では常にCall1（stage1役割）側で生成されるため、
    参照整合性チェックの修復対象もstage1が正しい（D-4a時点ではStage2所有だったための
    暫定マッピングをD-3のCall境界再編に合わせて更新済み）。"""
    assert guard.MODE_V12_CHECK_STAGE["actor_analyst_expected_response_target_grounded"] == "stage1"
    assert guard.MODE_V12_CHECK_STAGE["event_role_intended_target_grounded"] == "stage1"
    assert guard.MODE_V12_CHECK_STAGE["scenario_derived_from_hypothesis_grounded"] == "stage1"
    assert {"event_role_intended_target_grounded", "actor_analyst_expected_response_target_grounded",
            "scenario_derived_from_hypothesis_grounded"} <= guard.MODE_V12_CHECK_NAMES


# ---- 5. マージロジック ----

def test_merge_derives_stakeholders_from_key_actor_profiles():
    client = FakeSupabaseClient(tables=BASE_TABLES)
    azure_client = _mock_client(STAGE1_RESPONSE)
    result = engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "クエリ", profile="quick")
    stakeholders = result["response"]["response_json"]["stakeholders"]
    assert len(stakeholders) == 1
    assert stakeholders[0]["actor"] == "PLA"
    assert stakeholders[0]["purpose"] == "圧力強化"  # ultimate_objectiveから導出


def test_merge_patches_action_substance_from_stage2_into_key_actor_profiles():
    client = FakeSupabaseClient(tables=BASE_TABLES)
    stage2 = {
        "audience_analysis": [],
        "key_actor_action_substance": [{
            "actor_ref": "pla",
            "action_substance": {
                "stated_signal": "強い姿勢", "concrete_measure": "限定的", "target_scope": None,
                "legal_or_policy_force": None, "implementation_status": None, "enforcement_status": None,
                "discretion": None, "evidence_ids": None, "assessment": "Signalが強くSubstanceは限定的",
            },
        }],
        "alternative_scenarios": [{"description": "訓練の一環", "confidence": "低",
                                    "strengthening_conditions": None, "weakening_conditions": None,
                                    "switch_conditions": ["頻度が減少"],
                                    "evidence_class": None, "confidence_adjustment_reason": None,
                                    "derived_from_hypothesis_id": None}],
        "additional_stakeholders": [],
    }
    azure_client = _mock_client(STAGE1_RESPONSE, stage2)
    result = engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "クエリ", profile="standard")
    kap = result["response"]["response_json"]["key_actor_profiles"][0]
    assert kap["action_substance"]["stated_signal"] == "強い姿勢"


# ---- Phase D-3(Holistic Reasoning Architecture Review): deep×case_analysisは
# situation_actor_assessment(hypothesis_register/directional_perceptions等)が発火する
# ため、main_scenario/alternative_scenarios/strongest_counterargument生成をCall1
# （旧Stage1、defer_scenario=Trueの間はaudience_signal/media_context/
# situation_actor_assessment/directional_perceptionを合成）の後段のCall2（Scenario専任）
# へ遅延させる。Call1のFixtureはSTAGE1_RESPONSEからmain_scenario/alternative_scenarios/
# strongest_counterargumentを除き、合成される4モジュール分のフィールドを追加したもの。
_DEEP_STAGE1_CALL1_RESPONSE = {
    k: v for k, v in STAGE1_RESPONSE.items()
    if k not in ("main_scenario", "alternative_scenarios", "strongest_counterargument")
}
_DEEP_STAGE1_CALL1_RESPONSE = {
    **_DEEP_STAGE1_CALL1_RESPONSE,
    # Phase 6C-0: STAGE1_RESPONSEのclaims[]はsource_ref="e-1"(fact claim)を持つが、
    # このfixture群のquery_text("クエリ")はkeyword一致しないためsource_packのevent_ids
    # に"e-1"は含まれず、known_idsとしては未知のまま(fabrication扱い)になる。この一連の
    # テストはCall1/Call2のorchestration・repair挙動が焦点でclaims内容は対象外なので、
    # evidence_ids_not_fabricated判定に影響しないhypothesis型に差し替える。
    "claims": [{"claim_type": "hypothesis", "text": "圧力の常態化を狙っている可能性", "source_ref": None}],
    "audience_analysis": [], "key_actor_action_substance": [],
    "media_source_context": {"media_editorial_context": None, "narrative_deviation": None,
                              "source_of_sources_note": None},
    "situation_actor_assessment": [], "investigation_questions": [],
    "expert_reasoning_checks": {
        "feasibility_check": {"applicable": False, "trigger_basis": None, "question": None, "evidence_status": None},
        "capability_vs_intent_check": {"applicable": False, "trigger_basis": None, "question": None, "evidence_status": None},
        "role_choreography_check": {"applicable": False, "trigger_basis": None, "question": None, "evidence_status": None},
        "premise_invalidation_check": {"applicable": False, "trigger_basis": None, "question": None, "evidence_status": None},
        "higher_order_objective_check": {"applicable": False, "trigger_basis": None, "question": None, "evidence_status": None},
        "structural_benefit_check": {"applicable": False, "trigger_basis": None, "question": None, "evidence_status": None},
        "internal_blocking_check": {"applicable": False, "trigger_basis": None, "question": None, "evidence_status": None},
    },
    "hypothesis_register": [
        {"hypothesis_id": "H0", "description": "訓練の一環ではなく圧力の段階的強化",
         "status": "retained", "hypothesis_type": "H0_straightforward", "reason": None,
         "supporting_evidence_ids": None, "contradicting_evidence_ids": None,
         "weakening_evidence_relevance": None, "confidence": "中",
         "interaction_basis": {"basis_type": "EVIDENCE_GAP", "actor_ref": None,
                                "target_actor_ref": None, "gap_note": "テスト用固定応答"}},
        {"hypothesis_id": "H1", "description": "越境自体が目的ではなく国内向け示威が主因",
         "status": "rejected", "hypothesis_type": "H3_constraint_domestic_driver", "reason": None,
         "supporting_evidence_ids": None, "contradicting_evidence_ids": None,
         "weakening_evidence_relevance": None, "confidence": "低",
         "interaction_basis": {"basis_type": "NOT_APPLICABLE", "actor_ref": None,
                                "target_actor_ref": None, "gap_note": "テスト用固定応答"}},
    ],
    "directional_perceptions": [],
    "identified_knowledge_gaps": [
        {"description": "テスト用固定応答", "gap_category": None, "priority": None,
         "recommended_action": None},
    ],
    "discretion_assessment": {"discretion_mode": "HUMAN_DISCRETION_DOMINANT",
                               "mode_rationale": "テスト用固定応答"},
    "human_interaction_coverage": {"interaction_status": "INSUFFICIENT_EVIDENCE",
                                    "status_rationale": "テスト用固定応答"},
}
_DEEP_STAGE2_SCENARIO_RESPONSE = {
    "main_scenario": {
        "description": "圧力の段階的強化", "confidence": "中", "supporting_evidence": ["頻度増加"],
        "selection_rationale": "H0が最も一貫して説明する", "assumptions": None,
        "observable_indicators": None, "historical_analogies_used": None, "evidence_class": None,
        "confidence_adjustment_reason": None, "evidence_relevance": None,
        "weakening_evidence_relevance": None,
        "relevance_confidence_adjustment_reason": None, "derived_from_hypothesis_id": "H0",
    },
    "strongest_counterargument": "訓練の一環の可能性",
    "alternative_scenarios": [
        {"description": "シナリオA", "confidence": "中", "strengthening_conditions": ["x"],
         "weakening_conditions": ["y"], "switch_conditions": ["z"],
         "evidence_class": None, "confidence_adjustment_reason": None,
         "derived_from_hypothesis_id": None},
        {"description": "シナリオB", "confidence": "低", "strengthening_conditions": None,
         "weakening_conditions": None, "switch_conditions": None,
         "evidence_class": None, "confidence_adjustment_reason": None,
         "derived_from_hypothesis_id": None},
    ],
    "additional_stakeholders": [],
}


def test_deep_profile_defers_main_scenario_to_call2_after_hypothesis_register():
    """Phase D-3: deep×case_analysisでは、main_scenarioがCall1(Stage1+4モジュール合成)
    ではなくCall2(Scenario専任)からのみ供給されること、Call1にはmain_scenario/
    alternative_scenarios/strongest_counterargumentが一切要求されないこと（二重source of
    truthにならないこと）、呼び出し回数がdefer_scenario導入前と変わらず2回のままである
    ことを確認する。"""
    client = FakeSupabaseClient(tables=BASE_TABLES)
    azure_client = _mock_client(_DEEP_STAGE1_CALL1_RESPONSE, _DEEP_STAGE2_SCENARIO_RESPONSE)
    result = engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "クエリ", profile="deep")
    assert azure_client.chat.completions.create.call_count == 2
    response_json = result["response"]["response_json"]
    assert response_json["main_scenario"]["description"] == "圧力の段階的強化"
    assert response_json["main_scenario"]["derived_from_hypothesis_id"] == "H0"
    alt = response_json["alternative_scenarios"]
    assert len(alt) == 2
    assert alt[0]["description"] == "シナリオA"
    assert response_json["hypothesis_register"][0]["hypothesis_id"] == "H0"
    # Call1(Stage1)自体はmain_scenario/alternative_scenarios/strongest_counterargumentを
    # 一切要求しない(旧経路が残っていないことの直接確認)
    stage1_schema = schema.build_stage1_schema(
        extra_modules=("audience_signal", "media_context", "situation_actor_assessment",
                       "directional_perception"),
        defer_scenario=True)
    assert "main_scenario" not in stage1_schema["properties"]
    assert "alternative_scenarios" not in stage1_schema["properties"]
    assert "strongest_counterargument" not in stage1_schema["properties"]


# ---- Phase 7-2K-A/B/C(ユーザー指示): Deterministic Candidate Set → LLM Materiality
# Selection。既定OFF、deep+defer_scenario限定。

def test_actor_candidate_selection_flag_off_by_default_no_module_no_leak():
    """flag未設定(既定OFF)ではactor_candidate_selection moduleが一切schemaに現れず、
    既存の呼び出し回数・挙動に影響しないことを確認する。"""
    client = FakeSupabaseClient(tables=BASE_TABLES)
    azure_client = _mock_client(_DEEP_STAGE1_CALL1_RESPONSE, _DEEP_STAGE2_SCENARIO_RESPONSE)
    result = engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "クエリ", profile="deep")
    assert azure_client.chat.completions.create.call_count == 2
    assert result["execution_metadata"]["actor_candidate_set"] == []
    assert result["execution_metadata"]["actor_candidate_selection_diagnostics"] is None
    assert "actor_candidate_selection" not in result["response"]["response_json"]
    assert "actor_candidate_gaps" not in result["response"]["response_json"]


def test_actor_candidate_selection_flag_on_validates_and_pops_fields(monkeypatch):
    """flag ONの場合、LLM出力のactor_candidate_selectionがReferential Integrity検証され
    （Candidate Set外のcandidate_entity_idをhallucinatedとして検出）、Public Response
    Schema(response_json)には一切漏れず、test-only telemetryとしてのみexecution_metadata
    へ残ることを確認する（v1.2/v1.3のtrajectory系フィールドと同じ設計）。"""
    monkeypatch.setenv(engine.ACTOR_CANDIDATE_SELECTION_ENABLED_ENV, "1")
    client = FakeSupabaseClient(tables=BASE_TABLES)
    stage1_with_selection = dict(_DEEP_STAGE1_CALL1_RESPONSE)
    # BASE_TABLESにはgeo_entities等が無いため、Candidate Setは空になる
    # （=以下のcandidate_entity_idはいずれも実在候補外、hallucination検出の直接確認）。
    stage1_with_selection["actor_candidate_selection"] = [
        {"candidate_entity_id": "pla", "materiality_reason": "作戦の実施主体",
         "role_in_situation": "実行部隊", "confidence": "高"},
        {"candidate_entity_id": "fabricated-nonexistent-id",
         "materiality_reason": "テスト用の不正なID", "role_in_situation": "x", "confidence": "低"},
    ]
    stage1_with_selection["actor_candidate_gaps"] = []
    azure_client = _mock_client(stage1_with_selection, _DEEP_STAGE2_SCENARIO_RESPONSE)
    result = engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "クエリ", profile="deep")
    assert azure_client.chat.completions.create.call_count == 2
    exec_meta = result["execution_metadata"]
    diagnostics = exec_meta["actor_candidate_selection_diagnostics"]
    assert diagnostics is not None
    assert diagnostics["candidate_set_size"] == 0
    assert set(diagnostics["hallucinated_candidate_ids"]) == {"pla", "fabricated-nonexistent-id"}
    # test-only fieldはPublic Response Schemaへ一切漏れない
    assert "actor_candidate_selection" not in result["response"]["response_json"]
    assert "actor_candidate_gaps" not in result["response"]["response_json"]


def test_deep_profile_repair_fixes_scenario_not_derived_from_retained_hypothesis():
    """Phase D-4b: main_scenario.derived_from_hypothesis_idがhypothesis_registerの
    retainedな仮説を参照していない場合(scenario_derived_from_retained_hypothesis=
    INVALID_LINK)、Repairがstage2(Scenario)のみを対象に発火し、3回目の呼び出しで
    修正されることを確認する（絶対条件4の強制）。"""
    client = FakeSupabaseClient(tables=BASE_TABLES)
    tainted_scenario = json.loads(json.dumps(_DEEP_STAGE2_SCENARIO_RESPONSE))
    tainted_scenario["main_scenario"]["derived_from_hypothesis_id"] = None
    fixed_scenario = json.loads(json.dumps(_DEEP_STAGE2_SCENARIO_RESPONSE))
    azure_client = _mock_client(_DEEP_STAGE1_CALL1_RESPONSE, tainted_scenario, fixed_scenario)
    result = engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "クエリ", profile="deep")
    assert azure_client.chat.completions.create.call_count == 3
    assert result["quality_evaluation"]["repair_attempted"] is True
    assert result["quality_evaluation"]["completeness_status"] == "complete"
    assert result["response"]["response_json"]["main_scenario"]["derived_from_hypothesis_id"] == "H0"


def test_deep_profile_scenario_call_falls_back_when_budget_exhausted_after_stage1_recovery():
    """Phase D-3(Phase 7-1C-R2でtotal=3→4に伴い再確認、ユーザー指示Fixture F): Stage1が
    2回のリカバリを要してbudgetを3消費した場合、CallBudget(total=4)の下では最後の1枠が
    Stage2の初回呼び出しに使える（total=3時代は0枠だったため、この点が旧仕様と異なる）。
    その最後の1枠でのStage2呼び出しも失敗した場合、Stage2 Recoveryへ回す予算がもう
    残っていないためRECOVERY_SKIPPED_BUDGETとなり、5回目の呼び出しは行われず
    （ユーザー指示「予防的にmax=5へは拡張しない」）、既存の決定的フォールバックで
    main_scenario等の必須フィールドが埋められることを確認する。"""
    client = FakeSupabaseClient(tables=BASE_TABLES)
    azure_client = MagicMock()
    azure_client.chat.completions.create.side_effect = [
        MagicMock(choices=[MagicMock(message=MagicMock(content="not json"))], usage=None),
        MagicMock(choices=[MagicMock(message=MagicMock(content="still not json"))], usage=None),
        MagicMock(choices=[MagicMock(message=MagicMock(content=json.dumps(_DEEP_STAGE1_CALL1_RESPONSE)))], usage=None),
        MagicMock(choices=[MagicMock(message=MagicMock(content="stage2 not json either"))], usage=None),
    ]
    result = engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "クエリ", profile="deep")
    assert azure_client.chat.completions.create.call_count == 4
    assert result["response"]["response_json"]["main_scenario"]["confidence"] == "判断不能"
    jsonschema.validate(result["response"]["response_json"], schema.RESPONSE_SCHEMA)
    exec_meta = result["execution_metadata"]
    assert exec_meta["stage2_recovery_status"] == "RECOVERY_SKIPPED_BUDGET"
    assert exec_meta["recovery_skipped_due_to_budget"] is True
    assert exec_meta["calls_used"] == 4
    assert exec_meta["call_budget_remaining"] == 0
    # budgetが0のため、失敗したv12 checkがあってもRepairも実施できない
    assert exec_meta["repair_skipped_due_to_budget"] is True
    assert result["quality_evaluation"]["completeness_status"] == "incomplete_budget_exhausted"


# ---- Phase 7-1C-R2: Stage2 Recovery（ユーザー指示、Fixture A-G）----
# Stage2 scenario生成の初回失敗のうち再試行に意味がある種類（JSON parse失敗・Schema検証
# 失敗・transient transport/timeout系）だけを対象に、Stage1と同じパターンで1回だけ
# 追加リトライする。Reasoning Prompt/Schema/Actor数/Controller Packaging/Hypothesis数/
# rich intelligence/deep profile/Situation Contextは一切変更しない（同じ入力の再送のみ）。

def test_fixture_a_stage2_recovers_on_second_attempt_after_malformed_json():
    """Fixture A: Stage2 attempt1=malformed JSON, attempt2=valid JSON → recovered result,
    stub不使用。"""
    client = FakeSupabaseClient(tables=BASE_TABLES)
    azure_client = MagicMock()
    azure_client.chat.completions.create.side_effect = [
        _resp(_DEEP_STAGE1_CALL1_RESPONSE),
        MagicMock(choices=[MagicMock(message=MagicMock(content="not json"))], usage=None),
        _resp(_DEEP_STAGE2_SCENARIO_RESPONSE),
    ]
    result = engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "クエリ", profile="deep")
    assert azure_client.chat.completions.create.call_count == 3
    assert result["execution_metadata"]["stage2_recovery_status"] == "RECOVERED_SECOND_ATTEMPT"
    response_json = result["response"]["response_json"]
    assert response_json["main_scenario"]["description"] == "圧力の段階的強化"
    assert response_json["main_scenario"]["confidence"] != "判断不能"
    stage2_stage_metrics = [s for s in result["execution_metadata"]["stage_metrics"]
                             if s["stage"] == "stage2_scenario_recovery"]
    assert len(stage2_stage_metrics) == 1
    assert stage2_stage_metrics[0]["success"] is True
    assert stage2_stage_metrics[0]["recovery_reason"] == "JSONDecodeError"


def test_fixture_b_stage2_recovery_fails_twice_falls_back_to_stub():
    """Fixture B: attempt1=malformed, attempt2=malformed → existing stub fallback。
    Stage2側の初回+Recoveryで2回消費(budget残り2/4)のため、既存のRepair機構が残り予算で
    stage2側を対象にもう一度だけ試みる（無変更の既存挙動）——それも失敗する場合を4回目
    として明示的に与え、それでもstage2_recovery_status自体は最初の2回（初回+Recovery）
    の結果であるFAILED_AFTER_RECOVERYのまま変わらないことを確認する。"""
    client = FakeSupabaseClient(tables=BASE_TABLES)
    azure_client = MagicMock()
    azure_client.chat.completions.create.side_effect = [
        _resp(_DEEP_STAGE1_CALL1_RESPONSE),
        MagicMock(choices=[MagicMock(message=MagicMock(content="not json"))], usage=None),
        MagicMock(choices=[MagicMock(message=MagicMock(content="still not json"))], usage=None),
        MagicMock(choices=[MagicMock(message=MagicMock(content="repair also not json"))], usage=None),
    ]
    result = engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "クエリ", profile="deep")
    assert azure_client.chat.completions.create.call_count == 4
    assert result["execution_metadata"]["stage2_recovery_status"] == "FAILED_AFTER_RECOVERY"
    assert result["response"]["response_json"]["main_scenario"]["confidence"] == "判断不能"
    jsonschema.validate(result["response"]["response_json"], schema.RESPONSE_SCHEMA)


class _SimulatedAPITimeoutError(Exception):
    """テスト用のtransient transport error。_is_recoverable_stage2_failureは
    type(exc).__name__のみで判定するため、実際のopenai.APITimeoutErrorを構築する
    必要はない——クラス名を"APITimeoutError"に一致させる。"""


_SimulatedAPITimeoutError.__name__ = "APITimeoutError"


def test_fixture_c_stage2_recovers_after_transient_timeout_error():
    """Fixture C: attempt1=timeout/transient error, attempt2=valid → recovery success。"""
    client = FakeSupabaseClient(tables=BASE_TABLES)
    azure_client = MagicMock()
    azure_client.chat.completions.create.side_effect = [
        _resp(_DEEP_STAGE1_CALL1_RESPONSE),
        _SimulatedAPITimeoutError("simulated transient timeout"),
        _resp(_DEEP_STAGE2_SCENARIO_RESPONSE),
    ]
    result = engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "クエリ", profile="deep")
    assert azure_client.chat.completions.create.call_count == 3
    assert result["execution_metadata"]["stage2_recovery_status"] == "RECOVERED_SECOND_ATTEMPT"
    assert result["response"]["response_json"]["main_scenario"]["confidence"] != "判断不能"


def test_fixture_d_normal_stage2_success_does_not_retry():
    """Fixture D: normal Stage2 success → retryしない、total callsは従来通り2。"""
    client = FakeSupabaseClient(tables=BASE_TABLES)
    azure_client = _mock_client(_DEEP_STAGE1_CALL1_RESPONSE, _DEEP_STAGE2_SCENARIO_RESPONSE)
    result = engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "クエリ", profile="deep")
    assert azure_client.chat.completions.create.call_count == 2
    exec_meta = result["execution_metadata"]
    assert exec_meta["stage2_recovery_status"] == "SUCCESS_FIRST_ATTEMPT"
    assert exec_meta["calls_used"] == 2
    assert exec_meta["call_budget_remaining"] == 2  # total=4のうち2消費
    assert exec_meta["recovery_skipped_due_to_budget"] is False
    assert exec_meta["repair_skipped_due_to_budget"] is False


class _SimulatedContentPolicyError(Exception):
    """テスト用: content policy等、同一入力の再送に意味がない決定的な拒否をシミュレートする。
    _STAGE2_RECOVERABLE_EXCEPTION_TYPESに含まれない名前を使い、リトライされないことを確認する。"""


_SimulatedContentPolicyError.__name__ = "BadRequestError"


def test_stage2_nonrecoverable_failure_is_not_retried():
    """Retry対象外（content policy/決定的invalid request等）の失敗は無条件リトライせず、
    即座にstubへフォールバックすることを確認する（ユーザー指示「同一入力再実行に意味が
    ないfailureを無条件retryしない」）。"""
    client = FakeSupabaseClient(tables=BASE_TABLES)
    azure_client = MagicMock()
    azure_client.chat.completions.create.side_effect = [
        _resp(_DEEP_STAGE1_CALL1_RESPONSE),
        _SimulatedContentPolicyError("simulated content policy rejection"),
        # Stage2 Recoveryは行われないが(禁止事項)、残budgetで既存のRepair機構は
        # 別途stage2側を対象に試みる（無変更の既存挙動、Recovery禁止とは独立）——
        # ここではそれも失敗させ、Recovery不発火だけを厳密に確認する。
        MagicMock(choices=[MagicMock(message=MagicMock(content="repair also not json"))], usage=None),
    ]
    result = engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "クエリ", profile="deep")
    assert azure_client.chat.completions.create.call_count == 3
    assert result["execution_metadata"]["stage2_recovery_status"] == "STUB_FALLBACK"
    assert result["response"]["response_json"]["main_scenario"]["confidence"] == "判断不能"


def test_fixture_g_stage2_recovery_does_not_cause_duplicate_persistence():
    """Fixture G: Recoveryによるduplicate persistence/side effectなし。stage2_dataが
    Recovery経由で得られても、後続のマージ・永続化は通常経路と同じく1回だけ行われる
    ことを確認する。"""
    client = FakeSupabaseClient(tables=BASE_TABLES)
    azure_client = MagicMock()
    azure_client.chat.completions.create.side_effect = [
        _resp(_DEEP_STAGE1_CALL1_RESPONSE),
        MagicMock(choices=[MagicMock(message=MagicMock(content="not json"))], usage=None),
        _resp(_DEEP_STAGE2_SCENARIO_RESPONSE),
    ]
    result = engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "クエリ", profile="deep")
    assert result["execution_metadata"]["stage2_recovery_status"] == "RECOVERED_SECOND_ATTEMPT"
    for table in ("geo_expert_requests", "geo_source_packs", "geo_expert_responses",
                  "geo_quality_evaluations"):
        assert len(client.tables.get(table, [])) == 1, f"{table}への書き込みが1件ではありません"
    audit_rows = client.tables.get("geo_audit_logs", [])
    metadata_rows = [r for r in audit_rows if r["action"] == "expert_request_execution_metadata"]
    assert len(metadata_rows) == 1


def test_evidence_id_fabrication_is_scrubbed_not_repaired():
    client = FakeSupabaseClient(tables=BASE_TABLES)
    tainted = json.loads(json.dumps(STAGE1_RESPONSE))
    tainted["key_stakeholder_assessment"]["evidence_ids"] = ["架空-999"]
    tainted["key_actor_profiles"][0]["evidence_ids"] = ["e-1", "架空-888"]
    azure_client = _mock_client(tainted)
    result = engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "PLAの越境について", profile="quick")
    # 捏造ID除去のためにLLMは再度呼ばれない(1回のみ)
    assert azure_client.chat.completions.create.call_count == 1
    assert result["response"]["response_json"]["key_stakeholder_assessment"]["evidence_ids"] == []
    assert result["response"]["response_json"]["key_actor_profiles"][0]["evidence_ids"] == ["e-1"]
    assert len(result["quality_evaluation"]["removed_fabricated_ids"]) == 2


def test_claim_source_ref_fabrication_is_nulled_and_recorded_not_repaired():
    """Phase 6D-2回帰: claims[].source_refが実在しないIDを指す場合、Production Output
    側でもnull化され（評価Guardの検出だけで終わらせない）、telemetryへ記録されること。
    捏造ID除去のためにLLMは再度呼ばれない（scrub_fabricated_evidence_idsと同じ設計）。"""
    client = FakeSupabaseClient(tables=BASE_TABLES)
    tainted = json.loads(json.dumps(STAGE1_RESPONSE))
    tainted["claims"] = [
        {"claim_type": "fact", "text": "PLA機が越境した", "source_ref": "e-1"},
        {"claim_type": "official_claim", "text": "架空の主張", "source_ref": "架空-999"},
    ]
    azure_client = _mock_client(tainted)
    result = engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "PLAの越境について", profile="quick")
    assert azure_client.chat.completions.create.call_count == 1
    claims = result["response"]["response_json"]["claims"]
    assert claims[0]["source_ref"] == "e-1"
    assert claims[1]["source_ref"] is None
    resolutions = result["execution_metadata"]["claim_source_ref_resolutions"]
    assert len(resolutions) == 1
    assert resolutions[0]["original_ref"] == "架空-999"
    assert resolutions[0]["resolution_method"] == "not_found_in_evidence_registry"


def test_final_response_validates_against_master_response_schema():
    client = FakeSupabaseClient(tables=BASE_TABLES)
    azure_client = _mock_client(STAGE1_RESPONSE)
    result = engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "クエリ", profile="quick")
    jsonschema.validate(result["response"]["response_json"], schema.RESPONSE_SCHEMA)


# ---- 6. DB永続化（Phase B: run_expert_requestと同じテーブルへ保存する） ----

def test_staged_execution_persists_to_same_tables_as_monolithic():
    client = FakeSupabaseClient(tables=BASE_TABLES)
    azure_client = _mock_client(STAGE1_RESPONSE)
    result = engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "クエリ", profile="quick")
    assert len(client.tables["geo_expert_requests"]) == 1
    assert len(client.tables["geo_source_packs"]) == 1
    assert len(client.tables["geo_expert_responses"]) == 1
    assert len(client.tables["geo_quality_evaluations"]) == 1
    assert result["response"]["prompt_version"] == engine.STAGED_PROMPT_VERSION
    # 実行メタデータはgeo_quality_evaluations.checksへ混在させず、既存geo_audit_logsへ記録する
    audit_rows = client.tables.get("geo_audit_logs", [])
    metadata_rows = [r for r in audit_rows if r["action"] == "expert_request_execution_metadata"]
    assert len(metadata_rows) == 1
    assert metadata_rows[0]["detail"]["execution_mode"] == "staged"
    # Quality Guardの生の判定結果配列(checks)そのものは実行メタデータ側に混在させない
    # （geo_quality_evaluations.checksと二重管理しない。detail内の"checks"はトップレベル
    # キーとしてのみ存在しないことを確認する。initial_failed_checks_detail等の派生情報は許容）
    assert "checks" not in metadata_rows[0]["detail"]


# ---- 6.5. persist=False（評価Harness用dry-run、2026-09-06追加）----
# 2026-09-06にdocstringが「DBへの保存は行わない」と誤って記載していたにもかかわらず、
# 実際には常にDBへ書き込んでいたことが判明した（Eguchi Expert AI Final v1.0 Tier1検証中に
# geo_cases.final_outputを誤って上書きするインシデントが発生）。persist=Falseはこの再発を
# 防ぐための明示的なdry-runオプションであり、insert/update呼び出しが0回であることを
# 決定的に保証する。

def test_persist_false_writes_zero_rows_to_all_tables():
    client = FakeSupabaseClient(tables=BASE_TABLES)
    azure_client = _mock_client(STAGE1_RESPONSE)
    result = engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "クエリ",
        profile="quick", persist=False)
    for table in ("geo_expert_requests", "geo_source_packs", "geo_expert_responses",
                  "geo_quality_evaluations", "geo_audit_logs", "geo_retrieval_runs",
                  "geo_retrieval_results", "geo_cases"):
        assert len(client.tables.get(table, [])) == 0, f"{table}に書き込みが発生しています"
    # 戻り値の形はpersist=Trueと同一に保つ（呼び出し元コードの分岐を増やさない）
    assert result["response"]["prompt_version"] == engine.STAGED_PROMPT_VERSION
    assert "response_id" in result["response"]
    assert "request_id" in result["request"]
    assert "evaluation_id" in result["quality_evaluation"]


def test_persist_false_does_not_update_existing_case_final_output():
    tables = {**BASE_TABLES, "geo_cases": [{
        "case_id": "case-1", "case_title": "t", "original_question": "q",
        "as_of_date": "2026-08-15", "final_output": {"one_line_assessment": "元の内容"},
    }], "geo_case_issues": [], "geo_entity_issues": [], "geo_entities": [],
        "geo_relationships": [], "geo_knowledge_items": [], "geo_knowledge_reviews": []}
    client = FakeSupabaseClient(tables=tables)
    azure_client = _mock_client(STAGE1_RESPONSE)
    engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "クエリ",
        case_id="case-1", profile="quick", persist=False)
    case_row = client.tables["geo_cases"][0]
    assert case_row["final_output"] == {"one_line_assessment": "元の内容"}


# ---- 6b. persist_case_side_effects=False（Reading Presentation Layer v1、ユーザー指示）----
# docs/expert-ai-validation/eguchi_db_write_incident_2026-09-06/incident_report.mdの
# インシデント（評価run実行によりgeo_cases.final_outputが実際に上書きされ、手動復元が
# 必要になった）と同種の事故を、Gold Case UI検証用のpersist=True runで再発させないための
# 回帰テスト。geo_expert_responses等はDBへ実際にinsertされる（response_idがUI Presentation
# APIから参照できる必要があるため）一方、case_idに紐づく副作用は一切発生しないことを確認する。

def test_persist_true_with_case_side_effects_disabled_still_inserts_response_row():
    client = FakeSupabaseClient(tables=BASE_TABLES)
    azure_client = _mock_client(STAGE1_RESPONSE)
    result = engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "クエリ",
        profile="quick", persist=True, persist_case_side_effects=False)
    assert len(client.tables.get("geo_expert_requests", [])) == 1
    assert len(client.tables.get("geo_expert_responses", [])) == 1
    assert len(client.tables.get("geo_quality_evaluations", [])) == 1
    assert result["response"]["response_id"] == client.tables["geo_expert_responses"][0]["response_id"]


def test_persist_true_with_case_side_effects_disabled_does_not_update_case_final_output():
    tables = {**BASE_TABLES, "geo_cases": [{
        "case_id": "case-1", "case_title": "t", "original_question": "q",
        "as_of_date": "2026-08-15", "final_output": {"one_line_assessment": "元の内容（上書きされてはならない）"},
    }], "geo_case_issues": [], "geo_entity_issues": [], "geo_entities": [],
        "geo_relationships": [], "geo_knowledge_items": [], "geo_knowledge_reviews": []}
    client = FakeSupabaseClient(tables=tables)
    azure_client = _mock_client(STAGE1_RESPONSE)
    engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "クエリ",
        case_id="case-1", profile="quick", persist=True, persist_case_side_effects=False)
    case_row = client.tables["geo_cases"][0]
    assert case_row["final_output"] == {"one_line_assessment": "元の内容（上書きされてはならない）"}


def test_persist_true_with_case_side_effects_disabled_writes_zero_retrieval_trace_rows():
    tables = {**BASE_TABLES, "geo_cases": [{
        "case_id": "case-1", "case_title": "t", "original_question": "q",
        "as_of_date": "2026-08-15", "final_output": None,
    }], "geo_case_issues": [], "geo_entity_issues": [], "geo_entities": [],
        "geo_relationships": [], "geo_knowledge_items": [{"knowledge_item_id": "k-1", "usage_count": 0}],
        "geo_knowledge_reviews": []}
    client = FakeSupabaseClient(tables=tables)
    azure_client = _mock_client(STAGE1_RESPONSE)
    engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "クエリ",
        case_id="case-1", profile="quick", persist=True, persist_case_side_effects=False)
    assert len(client.tables.get("geo_retrieval_runs", [])) == 0
    assert len(client.tables.get("geo_retrieval_results", [])) == 0
    assert client.tables["geo_knowledge_items"][0]["usage_count"] == 0


def test_persist_case_side_effects_defaults_to_true_preserving_existing_behavior():
    """既存呼び出し元（persist_case_side_effects未指定）の挙動は変わらないことを確認する
    （デフォルトTrueで、persist=Trueのときcase_id側の副作用も従来通り発生する）。"""
    tables = {**BASE_TABLES, "geo_cases": [{
        "case_id": "case-1", "case_title": "t", "original_question": "q",
        "as_of_date": "2026-08-15", "final_output": {"one_line_assessment": "旧内容"},
    }], "geo_case_issues": [], "geo_entity_issues": [], "geo_entities": [],
        "geo_relationships": [], "geo_knowledge_items": [], "geo_knowledge_reviews": []}
    client = FakeSupabaseClient(tables=tables)
    azure_client = _mock_client(STAGE1_RESPONSE)
    engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "クエリ",
        case_id="case-1", profile="quick", persist=True)
    case_row = client.tables["geo_cases"][0]
    assert case_row["final_output"] != {"one_line_assessment": "旧内容"}


# ---- 7. Feature Flag / 実行方式パラメータでの切り替え ----

def test_resolve_execution_mode_defaults_to_staged(monkeypatch):
    monkeypatch.delenv(engine.EXECUTION_MODE_ENV, raising=False)
    assert engine.resolve_execution_mode() == "staged"


def test_resolve_execution_mode_env_var_allows_rollback_to_monolithic(monkeypatch):
    monkeypatch.setenv(engine.EXECUTION_MODE_ENV, "monolithic")
    assert engine.resolve_execution_mode() == "monolithic"


def test_resolve_execution_mode_explicit_param_overrides_env(monkeypatch):
    monkeypatch.setenv(engine.EXECUTION_MODE_ENV, "monolithic")
    assert engine.resolve_execution_mode("staged") == "staged"


def test_resolve_execution_mode_rejects_unknown_value():
    try:
        engine.resolve_execution_mode("bogus")
        assert False, "ValueErrorが発生するはず"
    except ValueError:
        pass


def test_dispatcher_routes_to_monolithic_when_execution_mode_is_monolithic(monkeypatch):
    client = FakeSupabaseClient(tables=BASE_TABLES)
    azure_client = _mock_client(_monolithic_fixture())
    result = engine.run_expert_request_with_mode(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "PLAの越境状況を分析して",
        execution_mode="monolithic")
    assert result["execution_metadata"]["execution_mode"] == "monolithic"


def test_dispatcher_routes_to_staged_by_default(monkeypatch):
    monkeypatch.delenv(engine.EXECUTION_MODE_ENV, raising=False)
    client = FakeSupabaseClient(tables=BASE_TABLES)
    azure_client = _mock_client(STAGE1_RESPONSE)
    result = engine.run_expert_request_with_mode(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "PLAの越境状況を分析して")
    assert result["execution_metadata"]["execution_mode"] == "staged"


def _monolithic_fixture() -> dict:
    return {
        "one_line_assessment": "見立て", "claims": [{"claim_type": "fact", "text": "x", "source_ref": "e-1"}],
        "stakeholders": [{"actor": "PLA"}],
        "main_scenario": {"description": "d", "confidence": "中", "supporting_evidence": ["x"]},
        "strongest_counterargument": "y", "retraction_conditions": ["z"],
    }


# ---- 8. Knowledge投入量の削減（Core／モード別／必要時のみの分割） ----

def test_stage1_knowledge_excludes_review_os_and_analyst_development_for_case_analysis():
    files = engine._knowledge_files_for_stage1("case_analysis")
    assert engine._REVIEW_OS_FILE not in files
    assert engine._ANALYST_DEVELOPMENT_FILE not in files
    for core in engine._KNOWLEDGE_CORE_FILES:
        assert core in files


def test_stage1_knowledge_includes_review_os_for_draft_review():
    files = engine._knowledge_files_for_stage1("draft_review")
    assert engine._REVIEW_OS_FILE in files
    assert engine._ANALYST_DEVELOPMENT_FILE not in files


def test_stage1_knowledge_includes_analyst_development_for_analyst_coaching():
    files = engine._knowledge_files_for_stage1("analyst_coaching")
    assert engine._ANALYST_DEVELOPMENT_FILE in files
    assert engine._REVIEW_OS_FILE not in files


def test_stage2_knowledge_includes_review_os_only_when_review_module_present():
    assert engine._REVIEW_OS_FILE in engine._knowledge_files_for_stage2(["review"])
    assert engine._REVIEW_OS_FILE not in engine._knowledge_files_for_stage2(["audience_signal"])


def test_stage2_knowledge_includes_analyst_development_only_when_analyst_learning_present():
    assert engine._ANALYST_DEVELOPMENT_FILE in engine._knowledge_files_for_stage2(["analyst_learning"])
    assert engine._ANALYST_DEVELOPMENT_FILE not in engine._knowledge_files_for_stage2(["deep_scenario"])


def test_build_knowledge_block_with_include_files_reduces_character_count():
    import geo_expert_common
    full = geo_expert_common.build_knowledge_block()
    reduced = geo_expert_common.build_knowledge_block(engine._knowledge_files_for_stage1("case_analysis"))
    assert len(reduced) < len(full)
    # 本文中の他ファイルへの言及(例: 01_SYSTEM_POLICY.mdからの相互参照)は残ってよいため、
    # そのファイル自体のセクション見出しが無いことだけを確認する
    assert "### 06_KNOWLEDGE_REVIEW_OS.md" not in reduced
    assert "### 10_KNOWLEDGE_ANALYST_DEVELOPMENT.md" not in reduced


def test_build_knowledge_block_without_include_files_matches_monolithic_behavior():
    import geo_expert_common
    assert geo_expert_common.build_knowledge_block() == geo_expert_common.build_knowledge_block(None)


# ---- 5. Call-level timeout override + telemetry（2026-09-18、ユーザー指示） ----

def test_stage1_call_uses_extended_timeout_override():
    """Stage1初回呼び出しは、client既定timeout(180秒)ではなく
    engine._EXPERT_LARGE_SCHEMA_TIMEOUT_SECONDSをcall-level overrideとして渡す
    （全システム共通のai_client.py側既定値は変更しない、ユーザー指示の設計）。
    Stage1呼び出し自体（1回目の実Azure呼び出し）にtimeoutが渡っていることのみを見る
    （後続のStage2/Repairが発火するかどうかはmode/quality guardの判定次第でこのテストの
    関心事ではない）。"""
    client = FakeSupabaseClient(tables=BASE_TABLES)
    azure_client = _mock_client(STAGE1_RESPONSE)

    engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "executive_brief", THEME_ID, "クエリ", profile="standard")

    assert azure_client.chat.completions.create.call_count >= 1
    _, kwargs = azure_client.chat.completions.create.call_args_list[0]
    assert kwargs["timeout"] == engine._EXPERT_LARGE_SCHEMA_TIMEOUT_SECONDS


def test_stage1_failure_telemetry_includes_exception_type_and_azure_call_attempts():
    """Stage1が最終的に失敗した場合、raiseされたGeoLLMErrorにexception_type/attempts
    （試行ごとの記録）が残ることを確認する（ユーザー指示#9、telemetry。run_expert_request_
    staged内部のstage_metricsも同じ値をこのerrorから拾って記録する、_record参照）。"""
    client = FakeSupabaseClient(tables=BASE_TABLES)
    azure_client = _mock_client("not json", "still not json", "still bad")

    with pytest.raises(geo_llm_common.GeoLLMError) as excinfo:
        engine.run_expert_request_staged(
            client, azure_client, "gpt-4o", "executive_brief", THEME_ID, "クエリ", profile="standard")

    assert excinfo.value.exception_type == "ValidationError"
    assert len(excinfo.value.attempts) >= 1
    assert all("attempt_label" in a and "exception_type" in a for a in excinfo.value.attempts)
