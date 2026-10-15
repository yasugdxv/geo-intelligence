"""地政学専門家AI v1.2(Session2): 11_AB_TEST_PLAN.mdのケース群をアプリ向けに自動テスト化したもの。

LLM出力の厳密一致テストにはしない（05_KNOWLEDGE_HYPOTHESIS_EVIDENCE_PROTOCOL.md等の指示に
従い、実際にどう書くかはLLMの生成に委ねる）。代わりに、各ケースが要求する構造・必須フィールド・
分類・ガードレールをjsonschemaとgeo_expert_quality_guardの両方で検証する
（12_EVALUATION_RUBRIC.csvの評価軸に対応）。
"""
import sys
from pathlib import Path

import jsonschema

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_expert_quality_guard as guard  # noqa: E402
import geo_expert_schema as schema  # noqa: E402

VERSIONS = {
    "prompt_version": "geo-expert-v1.2-session2", "knowledge_version": "abc123",
    "model": "gpt-4o", "source_pack_id": "sp-1",
}

BASE_RESPONSE = {
    "one_line_assessment": "見立て文",
    "key_point_assessment": {
        "candidate_key_points": [{
            "key_point": "Actor Aの意思決定が局面を規定するか", "why_it_could_be_key": "Actor Aの決定が結果を左右するため",
            "control_type": "unknown", "what_it_explains": "現状の力学", "what_it_does_not_explain": "詳細な時間軸",
        }],
        "primary_key_point": "Actor Aの意思決定が局面を規定するか", "selection_rationale": "最も説明力が高いため",
        "hinge_structure": {"primary_hinge": "Actor Aの意思決定"},
        "control_type": "unknown", "supporting_evidence_ids": None,
        "disconfirming_signal": "Actor A以外の要因で結果が変化した場合", "confidence": "中",
    },
    "claims": [{"claim_type": "fact", "text": "事実", "source_ref": "e-1"}],
    "stakeholders": [{"actor": "Actor A"}],
    "main_scenario": {"description": "主シナリオ", "confidence": "中", "supporting_evidence": ["根拠1"],
                       "selection_rationale": "他の仮説より説明力が高いため"},
    "strongest_counterargument": "最有力の反論",
    "retraction_conditions": ["撤回条件"],
    "business_impact": {"direct": None, "indirect": None, "unrelated_note": "自社への有意な接続は確認できない"},
}

SOURCE_PACK_TEXT = "Actor A / Actor B / Legislator C / Media D に関する情報。"


def _validate(response: dict) -> None:
    jsonschema.validate(response, schema.RESPONSE_SCHEMA)


# ---- Case I（前半）: Key Stakeholderが一人に定まる ----

def test_case_key_stakeholder_resolves_to_single_actor():
    response = {
        **BASE_RESPONSE,
        "key_stakeholder_assessment": {
            "determination": "single", "selected_actor_ids": ["actor-a"],
            "rationale": "最終決定権・実質的支配・拒否権のいずれもActor Aに集中しているため",
            "confidence": "高",
        },
        "key_actor_profiles": [{"actor_ref": "actor-a", "actor": "Actor A", "confidence": "高",
                                 "formal_role": "本件の最終決定権を持つ主体"}],
    }
    _validate(response)
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS, mode="case_analysis")
    assert result["failed_count"] == 0
    assert response["key_stakeholder_assessment"]["determination"] == "single"
    assert len(response["key_actor_profiles"]) >= 1


# ---- Case I（後半）/ Case B: 分散意思決定で一人に集約しない ----

def test_case_distributed_decision_structure_not_forced_to_single_actor():
    """determination=distributedは「1人へ集約しない」ことが目的であり、Key Actor Profile自体は
    0件を許容しない（01_SYSTEM_POLICY.md出力姿勢: 0件が許されるのはdetermination=unclearのみ）。
    distributed時は、分散構造上の複数ノードをそれぞれkey_actor_profilesとして示すことを確認する。"""
    response = {
        **BASE_RESPONSE,
        "key_stakeholder_assessment": {
            "determination": "distributed", "selected_actor_ids": ["faction-a", "faction-b"],
            "rationale": "連立与党内の複数会派が個別に拒否権を持ち、単一の決定者に還元できないため",
            "decision_structure": "連立与党A会派・B会派・官僚機構がそれぞれ拒否権を持つ",
            "confidence": "中",
        },
        "key_actor_profiles": [
            {"actor_ref": "faction-a", "actor": "連立与党A会派", "confidence": "中",
             "formal_role": "連立与党内の一会派として拒否権を持つ"},
            {"actor_ref": "faction-b", "actor": "連立与党B会派", "confidence": "中",
             "formal_role": "連立与党内の一会派として拒否権を持つ"},
        ],
    }
    _validate(response)
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS, mode="case_analysis")
    assert result["failed_count"] == 0
    assert response["key_stakeholder_assessment"]["determination"] == "distributed"
    assert response["key_stakeholder_assessment"]["decision_structure"]
    # 単一主体へ集約せず、分散構造上の複数ノードを別々のkey_actor_profilesとして示している
    assert len(response["key_actor_profiles"]) >= 2


# ---- Case H: Observed ActionとPersistent Preferenceが逆に見える ----

def test_case_preference_action_gap_explained_via_driving_forces():
    response = {
        **BASE_RESPONSE,
        "key_stakeholder_assessment": {
            "determination": "single", "selected_actor_ids": ["actor-a"],
            "rationale": "最終決定権を持つため", "confidence": "中",
        },
        "key_actor_profiles": [{
            "actor_ref": "actor-a", "actor": "Actor A", "confidence": "中",
            "formal_role": "本件の最終決定権を持つ主体",
            "persistent_preference": "対外協調路線を志向",
            "persistent_preference_evidence": [
                {"event_or_behavior": "過去の多国間合意への複数回の署名", "supports_or_contradicts": "supports",
                 "source": "過去のEvent記録"},
                {"event_or_behavior": "直近の強硬発言", "supports_or_contradicts": "contradicts",
                 "source": "今回の発言"},
            ],
            "observed_action": "今回は強硬な単独行動を表明",
            "preference_action_gap": "対外協調志向と今回の強硬姿勢は表面上一致しないが、国内支持基盤向けの発信と考えられる",
            "driving_forces": [{"force": "国内支持基盤", "direction": "supporting",
                                 "description": "選挙前の強硬姿勢アピール"}],
            "actor_intelligence_provenance": {
                "actor_intelligence_status": "sufficient", "profile_source": "actor_intelligence_candidate",
                "historical_episode_ids": [], "pattern_ids": [], "exception_episode_ids": [],
                "supporting_evidence_ids": [], "general_knowledge_used": False,
            },
            "actor_model_challenge": {
                "challenge_question": "対外協調路線という持続的傾向が、今回の強硬姿勢の説明として"
                                        "本当に適用できるか、それとも単発の国内向け演出に過ぎないか",
                "outcome": "inconclusive", "reasoning": None,
            },
        }],
    }
    _validate(response)
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS, mode="case_analysis")
    assert result["failed_count"] == 0
    kap = response["key_actor_profiles"][0]
    assert any(e["supports_or_contradicts"] == "supports" for e in kap["persistent_preference_evidence"])
    assert any(e["supports_or_contradicts"] == "contradicts" for e in kap["persistent_preference_evidence"])
    assert kap["preference_action_gap"]
    assert kap["driving_forces"]


# ---- Case J: 議員・政治家の支持基盤が重要 ----

def test_case_legislator_support_base_not_reduced_to_party_label():
    response = {
        **BASE_RESPONSE,
        "stakeholders": [{"actor": "Legislator C"}],
        "key_stakeholder_assessment": {
            "determination": "single", "selected_actor_ids": ["legislator-c"],
            "rationale": "本法案の可否を決める委員会の議決権を持つため", "confidence": "中",
        },
        "key_actor_profiles": [{
            "actor_ref": "legislator-c", "actor": "Legislator C", "confidence": "中",
            "formal_role": "本法案の可否を決める委員会の議決権を持つ議員",
            "driving_forces": [
                {"force": "選挙区の製造業雇用", "direction": "constraining", "description": "地元工場の雇用維持圧力"},
                {"force": "献金元の業界団体", "direction": "constraining", "description": "業界団体からの資金提供"},
            ],
        }],
    }
    _validate(response)
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS, mode="case_analysis")
    assert result["failed_count"] == 0
    forces = [f["force"] for f in response["key_actor_profiles"][0]["driving_forces"]]
    # 党派・思想ラベルではなく具体的な選挙区・産業・献金に言及していることを確認
    assert any("雇用" in f or "献金" in f or "業界" in f for f in forces)


# ---- Case K: SignalingとSubstanceのずれ ----

def test_case_signal_substance_gap_is_structured_separately():
    response = {
        **BASE_RESPONSE,
        "audience_analysis": [{
            "actor_ref": "actor-a", "action_or_statement": "制裁強化を発表",
            "primary_audience": "国内世論", "secondary_audience": "同盟国",
            "message_hypothesis": "強い姿勢を国内へ示す意図",
            "action_substance": {
                "stated_signal": "全面的な制裁強化",
                "concrete_measure": "対象は限定的な業種のみ",
                "legal_or_policy_force": "既存枠組みの運用強化に留まる",
                "assessment": "Signalは強いがSubstanceは限定的",
            },
            "confidence": "中",
        }],
    }
    _validate(response)
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS, mode="case_analysis")
    aa = response["audience_analysis"][0]
    assert aa["action_substance"]["stated_signal"] != aa["action_substance"]["concrete_measure"]
    # audience_analysisはmode別必須チェックの対象外フィールドではなく他チェックには影響しないことを確認
    assert all(c["passed"] for c in result["checks"] if c["name"] != "key_stakeholder_v12_fields_present")


# ---- Case M: 主流ナラティブと異なる情報源 ----

def test_case_narrative_deviation_treated_as_investigation_trigger_not_truth():
    response = {
        **BASE_RESPONSE,
        "media_source_context": {
            "narrative_deviation": [{
                "source": "Media D", "deviation_from_mainstream": "主流報道と異なる見立てを提示",
                "why_it_matters": "早期警戒指標になりうる",
                "verification_method": "一次情報・独立情報源での裏取りが必要",
                "current_assessment": "現時点では未検証のInvestigation Triggerとして扱う",
            }],
            "source_of_sources_note": "本件で継続参照する情報源の候補であり、正しさの証明ではない",
        },
    }
    _validate(response)
    deviation = response["media_source_context"]["narrative_deviation"][0]
    # 「主流と異なる」ことだけで真実として採用していない（verification_methodが必須の裏取り手段を持つ）ことを確認
    assert deviation["verification_method"]
    assert "未検証" in deviation["current_assessment"] or "Trigger" in deviation["current_assessment"]


# ---- Case F: 情報不足・矛盾がある案件（推測で埋めない） ----

def test_case_insufficient_information_returns_structured_gaps_not_guesses():
    response = {
        **BASE_RESPONSE,
        "unknown_or_uncertain": "現時点でKey Stakeholderを一意に特定できるだけの一次情報が無い",
        "key_stakeholder_assessment": {
            "determination": "unclear", "selected_actor_ids": [],
            "rationale": "入手できた情報からは意思決定権の所在を判別できない", "confidence": "判断不能",
        },
        "key_actor_profiles": [],
        "identified_knowledge_gaps": [
            {"description": "意思決定機関の内部手続きに関する追加情報が必要",
             "gap_category": "analytical_candidate", "recommended_action": "一次資料の追加収集"},
        ],
    }
    _validate(response)
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS, mode="case_analysis")
    assert result["failed_count"] == 0
    assert response["key_actor_profiles"] == []
    assert response["identified_knowledge_gaps"]


# ---- Case G: 主張が弱まりやすいドラフトレビュー ----

def test_case_draft_review_does_not_mechanically_hedge_well_grounded_scenario():
    response = {
        **BASE_RESPONSE,
        "root_premise_check": {
            "key_stakeholder_validity": {"result": "pass", "assessment": "妥当"},
            "persistent_preference_grounding": {"result": "pass", "assessment": "複数の過去事例で裏付けられている"},
            "objective_hierarchy_separation": {"result": "pass", "assessment": "分離されている"},
            "preference_action_gap_analysis": {"result": "pass", "assessment": "説明されている"},
            "observed_action_not_treated_as_ultimate_objective": {"result": "pass", "assessment": "問題なし"},
            "overall_result": "pass",
        },
        "review_findings": [{
            "severity": "Minor", "category": "scenario",
            "finding": "主シナリオの記述は根拠十分であり、断定を弱める必要はない。反証条件を補うに留める",
            "recommendation": "撤回条件をより具体的な観測指標で補強する",
        }],
    }
    _validate(response)
    result = guard.run_quality_guard(response, SOURCE_PACK_TEXT, VERSIONS, mode="draft_review")
    assert result["failed_count"] == 0
    # overall_result=passの場合はCritical指摘が無くても合格する（無理に問題を作らない）
    assert response["root_premise_check"]["overall_result"] == "pass"


# ---- Foundation Pack既存情報の利用確認 ----

def test_case_key_actor_entity_id_references_foundation_pack_entity():
    response = {
        **BASE_RESPONSE,
        "key_stakeholder_assessment": {
            "determination": "single", "selected_actor_ids": ["actor-a"],
            "rationale": "最終決定権を持つため", "confidence": "中",
        },
        "key_actor_profiles": [{
            "actor_ref": "actor-a", "actor": "Actor A", "entity_id": "entity-actor-a", "confidence": "中",
        }],
    }
    _validate(response)
    # entity_idがgeo_entities.entity_idと一致する場合のみ設定するフィールドであることの構造確認
    assert response["key_actor_profiles"][0]["entity_id"] == "entity-actor-a"


# ---- Case Library個別仮説を確定事実として再利用しない ----

def test_case_library_guardrail_present_in_system_policy_and_case_library_files():
    import geo_expert_common
    knowledge = geo_expert_common.load_geo_expert_knowledge()
    system_policy = knowledge["files"]["01_SYSTEM_POLICY.md"]
    case_library = knowledge["files"]["08_KNOWLEDGE_CASE_LIBRARY.md"]
    assert "08_KNOWLEDGE_CASE_LIBRARY" in system_policy
    assert "Trump" in system_policy
    assert "仮説" in case_library
