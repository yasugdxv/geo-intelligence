"""Expert AI Depth & Grounding Upgrade（P0修正）の決定的post-processing関数・
Quality Guard追加分のRegressionテスト。実Azure呼び出しは行わない（既存テストと同じ
MagicMock方式）。"""
import geo_expert_quality_guard as guard


def test_actor_controlled_with_empty_entity_ids_downgraded_to_unknown():
    """1. control_type=actor_controlledでcontrolling_entity_idsが空の場合、
    normalize_control_typeがunknownへ決定的に書き換えることを確認する。"""
    response = {
        "key_point_assessment": {
            "control_type": "actor_controlled", "controlling_entity_ids": [],
            "control_structure_summary": None,
            "candidate_key_points": [],
        },
    }
    adjusted = guard.normalize_control_type(response)
    assert response["key_point_assessment"]["control_type"] == "unknown"
    assert response["key_point_assessment"]["control_type_adjustment_reason"]
    assert "key_point_assessment" in adjusted


def test_actor_model_challenged_not_required_when_coverage_is_none():
    """Phase R1（Chunk6後P1）実データで発見: Actor Intelligence Packに根拠が無い
    （effective_intelligence_coverage=none）Actorについて、LLMが一般知識で
    persistent_preferenceを埋めてしまった場合まで actor_model_challenge を要求すると、
    根拠の無いChallenge文を書かせるだけになる。coverage=noneのActorは対象外とする。"""
    response = {
        "key_actor_profiles": [{
            "actor_ref": "a", "actor": "A", "persistent_preference": "一般知識で埋めた志向",
            "actor_intelligence_provenance": {"effective_intelligence_coverage": "none"},
            "actor_model_challenge": None,
        }],
    }
    result = guard._actor_model_challenged(response)
    assert result["passed"] is True


def test_actor_model_challenged_still_required_when_coverage_present():
    """coverage=full等、実際にActor/Institution Intelligenceの裏付けがある場合は
    引き続きchallengeが必須のままであることを確認する（回帰）。"""
    response = {
        "key_actor_profiles": [{
            "actor_ref": "a", "actor": "A", "persistent_preference": "根拠のある志向",
            "actor_intelligence_provenance": {"effective_intelligence_coverage": "full"},
            "actor_model_challenge": None,
        }],
    }
    result = guard._actor_model_challenged(response)
    assert result["passed"] is False


def test_general_knowledge_only_actor_assumption_cannot_be_medium_or_high():
    """2/6. general_knowledge_used=trueのActor Assessmentがconfidence=高/中のまま
    だとunsupported_actor_assumption_not_promotedが失敗することを確認する
    （Actor Intelligenceに存在しない内容の自動埋めをMedium/High昇格させない）。"""
    response = {
        "key_actor_profiles": [{
            "actor_ref": "a", "actor": "A", "confidence": "高",
            "actor_intelligence_provenance": {"general_knowledge_used": True},
        }],
    }
    result = guard._unsupported_actor_assumption_not_promoted(response)
    assert result["passed"] is False


def test_historical_pattern_alone_cannot_escalate_current_confidence():
    """3. evidence_class=historical_actor_patternのみでconfidence=高/中の場合、
    cap_confidence_by_evidence_classが決定的に低へ引き下げることを確認する
    （M9.1 Evidence Weighting Fixのevidence_class軸への拡張）。"""
    response = {
        "situation_actor_assessment": [{
            "actor_ref": "a", "confidence": "高", "evidence_class": "historical_actor_pattern",
        }],
        "media_source_context": None,
    }
    adjusted = guard.cap_confidence_by_evidence_class(response)
    assert response["situation_actor_assessment"][0]["confidence"] == "低"
    assert response["situation_actor_assessment"][0]["confidence_adjustment_reason"]
    assert adjusted


def test_weak_analogy_confidence_capped_in_main_scenario():
    """4. analogy_strength=weakを含むmain_scenarioがconfidence=高だと
    weak_analogy_not_used_as_strong_evidenceが失敗することを確認する。"""
    response = {
        "main_scenario": {
            "confidence": "高",
            "historical_analogies_used": [{
                "description": "Hormuz通航料事案からの類推", "analogy_strength": "weak",
            }],
        },
    }
    result = guard._weak_analogy_not_used_as_strong_evidence(response)
    assert result["passed"] is False


def test_actor_pattern_without_applicability_assessment_fails_guard():
    """5. relevant_decision_patternsを参照しながらpattern_applicability_assessment
    が無い場合、actor_pattern_applicability_presentが失敗することを確認する
    （Historical Patternを自動適用しないことの検証）。"""
    response = {
        "key_actor_profiles": [{
            "actor_ref": "a", "actor": "A", "confidence": "中",
            "relevant_decision_patterns": ["過去にXした傾向がある"],
            "pattern_applicability_assessment": None,
        }],
    }
    result = guard._actor_pattern_applicability_present(response)
    assert result["passed"] is False


def test_unsupported_actor_profile_auto_fill_flagged_without_general_knowledge_flag():
    """6. Actor Intelligenceが利用不可(profile_source=none_available)なのに
    persistent_preferenceが生成されておりgeneral_knowledge_usedも明示されていない
    場合、actor_intelligence_provenance_presentが失敗することを確認する
    （unsupported assumptionの自動埋め禁止）。"""
    response = {
        "key_actor_profiles": [{
            "actor_ref": "a", "actor": "A", "confidence": "中",
            "persistent_preference": "一般的にこう振る舞う",
            "actor_intelligence_provenance": {"profile_source": "none_available", "general_knowledge_used": None},
        }],
    }
    result = guard._actor_intelligence_provenance_present(response)
    assert result["passed"] is False


def test_hypothesis_register_missing_pruning_fails_guard():
    """7. hypothesis_registerが全件status=retainedのみ、または2件未満の場合、
    hypothesis_pruning_presentが失敗することを確認する（棄却・未解決の記録が
    残っているかの検証）。situation_actor_assessmentモジュールが発火せず
    hypothesis_register自体が無い場合はpassすることも確認する。"""
    all_retained = {"hypothesis_register": [
        {"hypothesis_id": "H0", "description": "surface", "status": "retained"},
        {"hypothesis_id": "H1", "description": "alt", "status": "retained"},
    ]}
    assert guard._hypothesis_pruning_present(all_retained)["passed"] is False

    too_few = {"hypothesis_register": [{"hypothesis_id": "H0", "description": "surface", "status": "retained"}]}
    assert guard._hypothesis_pruning_present(too_few)["passed"] is False

    pruned = {"hypothesis_register": [
        {"hypothesis_id": "H0", "description": "surface", "status": "retained"},
        {"hypothesis_id": "H4", "description": "wild", "status": "rejected"},
    ]}
    assert guard._hypothesis_pruning_present(pruned)["passed"] is True

    not_fired = {"hypothesis_register": None}
    assert guard._hypothesis_pruning_present(not_fired)["passed"] is True


def test_media_historical_pattern_alone_cannot_prove_current_intent():
    """8. Media hypothesisでevidence_class=historical_media_patternのみの場合、
    confidence=高/中がcap_confidence_by_evidence_classにより低へ引き下げられる
    ことを確認する（Historical Media Pattern=priorであり、Current Evidenceの
    代用にしないことの検証。既存M9.1 anonymous_sourced_hypothesis_confidence_
    cappedと相補的なチェック）。"""
    response = {
        "situation_actor_assessment": [],
        "media_source_context": {
            "media_editorial_context": [{
                "hypotheses": [{
                    "confidence": "高", "evidence_class": "historical_media_pattern",
                    "hypothesis_text": "この媒体は過去にも同様の意図的リークを行ってきた",
                }],
            }],
        },
    }
    adjusted = guard.cap_confidence_by_evidence_class(response)
    hyp = response["media_source_context"]["media_editorial_context"][0]["hypotheses"][0]
    assert hyp["confidence"] == "低"
    assert hyp["confidence_adjustment_reason"]
    assert adjusted


def test_main_scenario_evidence_class_historical_capped_to_low():
    """Evidence WeightingのMain Scenarioへの貫通: evidence_class=historical_actor_
    patternのままconfidence=高/中の場合、cap_confidence_by_evidence_classが
    main_scenarioも決定的に低へ引き下げることを確認する。main_scenario自体は
    削除されない。"""
    response = {
        "main_scenario": {
            "description": "d", "confidence": "中", "evidence_class": "historical_actor_pattern",
            "supporting_evidence": ["free text reference"],
        },
    }
    adjusted = guard.cap_confidence_by_evidence_class(response)
    assert response["main_scenario"]["confidence"] == "低"
    assert response["main_scenario"]["confidence_adjustment_reason"]
    assert response["main_scenario"]["description"] == "d"  # 削除されず保持される
    assert "main_scenario" in adjusted


def test_main_scenario_current_direct_evidence_not_capped():
    """evidence_class=current_direct_evidenceの場合はconfidence=高のまま維持される
    ことを確認する（Current Evidenceが十分なら中/高を許容）。"""
    response = {
        "main_scenario": {
            "description": "d", "confidence": "高", "evidence_class": "current_direct_evidence",
            "supporting_evidence": ["e-1"],
        },
    }
    adjusted = guard.cap_confidence_by_evidence_class(response)
    assert response["main_scenario"]["confidence"] == "高"
    assert adjusted == []


def test_main_scenario_supporting_evidence_all_derived_ids_capped_even_without_evidence_class():
    """defense-in-depth: evidence_classが自己申告されていなくても、supporting_evidence
    のUUID形式の項目が全てderived_intelligence_ids（Historical Episode等）にしか
    一致しない場合は決定的に低へ引き下げることを確認する（situation_actor_assessment
    で観測されたevidence_class自己申告ミスと同種の取りこぼしを防ぐ）。"""
    response = {
        "main_scenario": {
            "description": "d", "confidence": "中",
            "supporting_evidence": ["11111111-1111-1111-1111-111111111111"],
        },
    }
    adjusted = guard.cap_confidence_by_evidence_class(
        response, {"11111111-1111-1111-1111-111111111111"})
    assert response["main_scenario"]["confidence"] == "低"
    assert "main_scenario" in adjusted


def test_main_scenario_supporting_evidence_with_current_id_not_capped():
    """supporting_evidenceにderived_intelligence_idsに含まれないUUID（Current
    Evidenceの可能性）が1件でもあれば、confidenceは引き下げられないことを確認する。"""
    response = {
        "main_scenario": {
            "description": "d", "confidence": "中",
            "supporting_evidence": [
                "11111111-1111-1111-1111-111111111111",
                "22222222-2222-2222-2222-222222222222",
            ],
        },
    }
    adjusted = guard.cap_confidence_by_evidence_class(
        response, {"11111111-1111-1111-1111-111111111111"})
    assert response["main_scenario"]["confidence"] == "中"
    assert adjusted == []


def test_main_scenario_free_text_supporting_evidence_relies_on_evidence_class_only():
    """supporting_evidenceがUUID形式でない自由記述のみの場合（実データで観測された
    'core_profile.worldview'等）、ID起点の判定材料が無いためevidence_classの自己申告
    のみに頼ることを確認する（evidence_class未設定なら誤って引き下げない）。"""
    response = {
        "main_scenario": {
            "description": "d", "confidence": "中",
            "supporting_evidence": ["core_profile.worldview"],
        },
    }
    adjusted = guard.cap_confidence_by_evidence_class(response, {"unrelated-id"})
    assert response["main_scenario"]["confidence"] == "中"
    assert adjusted == []


def test_alternative_scenario_evidence_class_historical_capped_to_low():
    """Main Scenarioと同じ原則がalternative_scenarios[]にも統一適用されることを
    確認する（共通関数cap_confidence_by_evidence_classでEvidence Weightingを統一）。"""
    response = {
        "alternative_scenarios": [{
            "description": "alt", "confidence": "高", "evidence_class": "model_general_knowledge",
        }],
    }
    adjusted = guard.cap_confidence_by_evidence_class(response)
    assert response["alternative_scenarios"][0]["confidence"] == "低"
    assert response["alternative_scenarios"][0]["confidence_adjustment_reason"]
    assert adjusted == ["alternative_scenarios[0]"]


def test_actor_intelligence_provenance_attached_deterministically_from_pack():
    """P0-1: attach_actor_intelligence_provenanceが、Actor Intelligence Packの
    実データ（entity_id一致）からhistorical_episode_ids/pattern_ids/
    exception_episode_idsを決定的に転記することを確認する（LLMの自己申告に
    依存しない）。"""
    response = {
        "key_actor_profiles": [{
            "actor_ref": "trump", "actor": "Trump", "entity_id": "e-trump", "confidence": "中",
        }],
    }
    pack = {
        "key_actors": [{
            "entity_id": "e-trump", "actor_intelligence_status": "sufficient",
            "profile_source": "actor_intelligence_candidate",
            "historical_episode_ids": ["ep-1", "ep-2"],
            "pattern_ids": ["pattern-a"],
            "exception_episode_ids": ["ep-2"],
        }],
    }
    touched = guard.attach_actor_intelligence_provenance(response, pack)
    prov = response["key_actor_profiles"][0]["actor_intelligence_provenance"]
    assert prov["historical_episode_ids"] == ["ep-1", "ep-2"]
    assert prov["pattern_ids"] == ["pattern-a"]
    assert prov["exception_episode_ids"] == ["ep-2"]
    assert prov["profile_source"] == "actor_intelligence_candidate"
    assert touched == ["trump"]


def test_actor_intelligence_provenance_none_when_pack_is_none():
    """attach_actor_intelligence_provenanceはactor_intelligence_pack=None
    （Feature Flag OFF等）の場合、responseを変更せず空リストを返すことを確認する。"""
    response = {"key_actor_profiles": [{"actor_ref": "a", "actor": "A", "confidence": "中"}]}
    touched = guard.attach_actor_intelligence_provenance(response, None)
    assert touched == []
    assert "actor_intelligence_provenance" not in response["key_actor_profiles"][0]


def test_general_knowledge_used_forced_true_when_profile_source_none_available():
    """Phase R1 Chunk1: profile_source=none_available（Packに選定されているが実DBに
    Profileが無い）の場合、LLMがgeneral_knowledge_used=falseと自己申告していても
    決定的にTrueへ強制されることを確認する（LLMの自己申告だけに依存しない）。"""
    response = {
        "key_actor_profiles": [{
            "actor_ref": "unknown_actor", "actor": "Unknown Actor", "entity_id": "e-x",
            "confidence": "中",
            "actor_intelligence_provenance": {"general_knowledge_used": False},
        }],
    }
    pack = {
        "key_actors": [{
            "entity_id": "e-x", "actor_intelligence_status": "insufficient",
            "profile_source": "none_available",
        }],
    }
    guard.attach_actor_intelligence_provenance(response, pack)
    prov = response["key_actor_profiles"][0]["actor_intelligence_provenance"]
    assert prov["general_knowledge_used"] is True
    assert prov["actor_coverage"] == "none"


def test_general_knowledge_used_forced_true_when_actor_not_in_pack_at_all():
    """Phase R1 Chunk1: そもそもActor Intelligence Packにこのactorが選定されていない
    （pack_actor is None）場合も、実質的にActor Intelligenceを使っていないため
    general_knowledge_used=Trueが強制されることを確認する。"""
    response = {
        "key_actor_profiles": [{
            "actor_ref": "not_in_pack", "actor": "Not In Pack", "entity_id": "e-nowhere",
            "confidence": "中",
        }],
    }
    pack = {"key_actors": [{"entity_id": "e-somewhere-else", "profile_source": "actor_intelligence_active"}]}
    guard.attach_actor_intelligence_provenance(response, pack)
    prov = response["key_actor_profiles"][0]["actor_intelligence_provenance"]
    assert prov["general_knowledge_used"] is True
    assert prov["actor_coverage"] == "none"
    assert prov["profile_source"] == "not_evaluated"


def test_actor_coverage_full_when_profile_source_active():
    """Phase R1 Chunk1: profile_source=actor_intelligence_activeの場合、
    actor_coverage=fullが付与され、general_knowledge_used強制は行われない
    （LLMの自己申告をそのまま保持する）。"""
    response = {
        "key_actor_profiles": [{
            "actor_ref": "trump", "actor": "Trump", "entity_id": "e-trump", "confidence": "中",
            "actor_intelligence_provenance": {"general_knowledge_used": False},
        }],
    }
    pack = {"key_actors": [{"entity_id": "e-trump", "profile_source": "actor_intelligence_active"}]}
    guard.attach_actor_intelligence_provenance(response, pack)
    prov = response["key_actor_profiles"][0]["actor_intelligence_provenance"]
    assert prov["actor_coverage"] == "full"
    assert prov["general_knowledge_used"] is False


def test_institution_coverage_prevents_forced_general_knowledge_used():
    """Phase R1 Chunk3修正: BISのようにPerson用actor_profileはnone_availableだが
    Institution Intelligenceがfullで存在する場合、Person Profile不在だけを理由に
    general_knowledge_usedを強制Trueにしないことを確認する（LLMの自己申告Falseを
    そのまま保持する）。"""
    response = {
        "key_actor_profiles": [{
            "actor_ref": "bis", "actor": "BIS", "entity_id": "bis-id", "confidence": "低",
            "relevant_decision_patterns": ["段階的な規制強化"],
            "actor_intelligence_provenance": {"general_knowledge_used": False},
        }],
    }
    actor_pack = {"key_actors": [{"entity_id": "bis-id", "profile_source": "none_available"}]}
    institution_pack = {"institutions": [{"entity_id": "bis-id", "profile_source": "institution_intelligence_active"}]}
    guard.attach_actor_intelligence_provenance(response, actor_pack, institution_pack)
    prov = response["key_actor_profiles"][0]["actor_intelligence_provenance"]
    assert prov["general_knowledge_used"] is False
    assert prov["subject_type"] == "institution"
    assert prov["person_actor_coverage"] == "none"
    assert prov["institution_coverage"] == "full"
    assert prov["effective_intelligence_coverage"] == "full"
    assert prov["actor_coverage"] == "full"


def test_institution_coverage_forces_true_when_both_person_and_institution_absent():
    """Person・Institutionのどちらも実在するIntelligenceが無い場合は、従来通り
    general_knowledge_usedが強制Trueになることを確認する（回帰）。"""
    response = {"key_actor_profiles": [{
        "actor_ref": "ghost", "actor": "Ghost", "entity_id": "ghost-id", "confidence": "低",
        "actor_intelligence_provenance": {"general_knowledge_used": False},
    }]}
    actor_pack = {"key_actors": [{"entity_id": "ghost-id", "profile_source": "none_available"}]}
    institution_pack = {"institutions": [{"entity_id": "ghost-id", "profile_source": "none_available"}]}
    guard.attach_actor_intelligence_provenance(response, actor_pack, institution_pack)
    prov = response["key_actor_profiles"][0]["actor_intelligence_provenance"]
    assert prov["general_knowledge_used"] is True
    assert prov["effective_intelligence_coverage"] == "none"


def test_institution_coverage_subject_type_person_when_not_in_institution_pack():
    """Institution Packに全く現れないactorはsubject_type=personのまま
    （既存Person Actorの挙動を変えない）ことを確認する。"""
    response = {"key_actor_profiles": [{
        "actor_ref": "trump", "actor": "Trump", "entity_id": "trump-id", "confidence": "中",
    }]}
    actor_pack = {"key_actors": [{"entity_id": "trump-id", "profile_source": "actor_intelligence_active"}]}
    guard.attach_actor_intelligence_provenance(response, actor_pack, institution_intelligence_pack=None)
    prov = response["key_actor_profiles"][0]["actor_intelligence_provenance"]
    assert prov["subject_type"] == "person"
    assert prov["institution_coverage"] == "none"
    assert prov["effective_intelligence_coverage"] == "full"


def test_institution_subject_forces_person_only_fields_to_null():
    """Phase R1 Chunk3（実E2Eで発見した追加バグ）: subject_type=institutionの場合、
    プロンプト指示だけに頼らずpersistent_preference等のPerson固有フィールドを決定的に
    nullへ強制することを確認する（LLMが非決定的にこれらを埋めてしまうケースへの対処）。"""
    response = {"key_actor_profiles": [{
        "actor_ref": "actor_BIS", "entity_id": "bis-id", "confidence": "中",
        "persistent_preference": "段階的な規制強化を好む傾向",
        "persistent_preference_evidence": [{"event_or_behavior": "x", "supports_or_contradicts": "supports"}],
        "persistent_preference_change": "reinforced",
        "actor_model_challenge": {"challenge_question": "q", "outcome": "inconclusive", "reasoning": None},
        "preference_action_gap": "一貫している",
        "strategic_relationship_preference": "協調的",
        "formal_role": "輸出管理規則の執行",  # Institution固有フィールドは維持されるべき
    }]}
    actor_pack = {"key_actors": [{"entity_id": "bis-id", "profile_source": "none_available"}]}
    institution_pack = {"institutions": [{"entity_id": "bis-id", "profile_source": "institution_intelligence_active"}]}
    guard.attach_actor_intelligence_provenance(response, actor_pack, institution_pack)
    kap = response["key_actor_profiles"][0]
    assert kap["persistent_preference"] is None
    assert kap["persistent_preference_evidence"] == []
    assert kap["persistent_preference_change"] is None
    assert kap["actor_model_challenge"] is None
    assert kap["preference_action_gap"] is None
    assert kap["strategic_relationship_preference"] is None
    assert kap["formal_role"] == "輸出管理規則の執行"  # Institution固有フィールドは無変更


def test_person_subject_keeps_persistent_preference_fields():
    """subject_type=personの場合はPerson固有フィールドを一切変更しないことを確認する
    （既存Actor Intelligenceの挙動を壊さない）。"""
    response = {"key_actor_profiles": [{
        "actor_ref": "trump", "entity_id": "trump-id", "confidence": "中",
        "persistent_preference": "取引的な二国間交渉を好む傾向",
    }]}
    actor_pack = {"key_actors": [{"entity_id": "trump-id", "profile_source": "actor_intelligence_active"}]}
    guard.attach_actor_intelligence_provenance(response, actor_pack, institution_intelligence_pack=None)
    assert response["key_actor_profiles"][0]["persistent_preference"] == "取引的な二国間交渉を好む傾向"


def test_actor_intelligence_provenance_present_no_false_positive_for_institution_grounded():
    """既存Guard_actor_intelligence_provenance_presentが、Institution Intelligence
    による裏付けをPerson Profile不在と誤認して誤検出しないことを確認する（Phase R1
    Chunk3が実E2Eで発見した意味的バグの再発防止）。"""
    response = {"key_actor_profiles": [{
        "actor_ref": "bis", "relevant_decision_patterns": ["段階的な規制強化"],
        "actor_intelligence_provenance": {
            "profile_source": "none_available", "general_knowledge_used": False,
            "effective_intelligence_coverage": "full",
        },
    }]}
    result = guard._actor_intelligence_provenance_present(response)
    assert result["passed"] is True


def test_actor_intelligence_provenance_present_still_catches_true_gap():
    """effective_intelligence_coverage=noneかつgeneral_knowledge_used未申告の場合は
    引き続き検出することを確認する（回帰）。"""
    response = {"key_actor_profiles": [{
        "actor_ref": "ghost", "relevant_decision_patterns": ["何かのパターン"],
        "actor_intelligence_provenance": {
            "profile_source": "none_available", "general_knowledge_used": False,
            "effective_intelligence_coverage": "none",
        },
    }]}
    result = guard._actor_intelligence_provenance_present(response)
    assert result["passed"] is False


def test_no_profile_actor_confidence_capped_end_to_end():
    """Phase R1 Chunk1 統合確認: profile_source=none_availableのActorが、
    attach_actor_intelligence_provenance → cap_confidence_by_general_knowledge_flag
    の順で処理された場合、evidence_idsが無ければconfidenceが自動的に低へ落ちることを
    確認する（LLMがgeneral_knowledge_used=falseと偽って高confidenceのまま出力しても
    パイプライン全体としては昇格を許さない）。"""
    response = {
        "key_actor_profiles": [{
            "actor_ref": "ghost_actor", "actor": "Ghost Actor", "entity_id": "e-ghost",
            "confidence": "高", "evidence_ids": [],
            "actor_intelligence_provenance": {"general_knowledge_used": False},
        }],
    }
    pack = {"key_actors": [{"entity_id": "e-ghost", "profile_source": "none_available"}]}
    guard.attach_actor_intelligence_provenance(response, pack)
    guard.cap_confidence_by_general_knowledge_flag(response)
    kap = response["key_actor_profiles"][0]
    assert kap["confidence"] == "低"
    assert kap["confidence_adjustment_reason"]


def test_general_knowledge_confidence_capped_when_no_current_evidence():
    """P0-1 3点修正: general_knowledge_used=trueかつevidence_idsが空のActor
    Assessmentがconfidence=高/中のままの場合、cap_confidence_by_general_knowledge_flag
    が決定的に低へ引き下げることを確認する（Guard検知のみだった状態からの補正）。"""
    response = {
        "key_actor_profiles": [{
            "actor_ref": "a", "actor": "A", "confidence": "高", "evidence_ids": None,
            "actor_intelligence_provenance": {"general_knowledge_used": True},
        }],
    }
    adjusted = guard.cap_confidence_by_general_knowledge_flag(response)
    assert response["key_actor_profiles"][0]["confidence"] == "低"
    assert response["key_actor_profiles"][0]["confidence_adjustment_reason"]
    assert adjusted == ["key_actor_profiles[0]"]


def test_general_knowledge_confidence_not_capped_when_current_evidence_present():
    """general_knowledge_used=trueでも、当該itemのevidence_ids（Current Evidence）が
    存在する場合はcap_confidence_by_general_knowledge_flagの対象外であることを確認する
    （General Knowledge使用自体を禁止するのではなく、Current Evidence無しでの昇格のみを
    防ぐ）。"""
    response = {
        "key_actor_profiles": [{
            "actor_ref": "a", "actor": "A", "confidence": "高", "evidence_ids": ["e-1"],
            "actor_intelligence_provenance": {"general_knowledge_used": True},
        }],
    }
    adjusted = guard.cap_confidence_by_general_knowledge_flag(response)
    assert response["key_actor_profiles"][0]["confidence"] == "高"
    assert adjusted == []


def test_general_knowledge_confidence_not_capped_when_flag_false():
    """general_knowledge_used=falseの場合は対象外であることを確認する。"""
    response = {
        "key_actor_profiles": [{
            "actor_ref": "a", "actor": "A", "confidence": "高", "evidence_ids": None,
            "actor_intelligence_provenance": {"general_knowledge_used": False},
        }],
    }
    adjusted = guard.cap_confidence_by_general_knowledge_flag(response)
    assert response["key_actor_profiles"][0]["confidence"] == "高"
    assert adjusted == []


def test_historical_evidence_separated_from_key_point_and_confidence_capped():
    """P0-2 3点修正: key_point_assessment.supporting_evidence_idsに含まれる
    Derived Intelligence ID（historical_episode_id等）がhistorical_supporting_
    evidence_idsへ決定的に分離されることを確認する。分離の結果supporting_evidence_ids
    が空になり、かつconfidenceが高/中の場合は低へ引き下げる（Historical Patternは
    Current Proofではないため）。"""
    response = {
        "key_point_assessment": {
            "confidence": "高",
            "supporting_evidence_ids": ["hist-1"],
            "candidate_key_points": [],
        },
    }
    touched = guard.separate_historical_evidence_in_key_point(response, {"hist-1"})
    kpa = response["key_point_assessment"]
    assert kpa["supporting_evidence_ids"] is None
    assert kpa["historical_supporting_evidence_ids"] == ["hist-1"]
    assert kpa["confidence"] == "低"
    assert kpa["confidence_adjustment_reason"]
    assert touched == ["key_point_assessment"]


def test_historical_evidence_separation_keeps_confidence_when_current_evidence_remains():
    """supporting_evidence_idsにCurrent EvidenceとHistorical Evidenceが混在する場合、
    Historical分のみhistorical_supporting_evidence_idsへ分離し、Current分は
    supporting_evidence_idsに残す。Current Evidenceが残っているためconfidenceは
    引き下げない。"""
    response = {
        "key_point_assessment": {
            "confidence": "高",
            "supporting_evidence_ids": ["current-1", "hist-1"],
            "candidate_key_points": [{
                "key_point": "x", "supporting_evidence_ids": ["hist-2"],
            }],
        },
    }
    touched = guard.separate_historical_evidence_in_key_point(response, {"hist-1", "hist-2"})
    kpa = response["key_point_assessment"]
    assert kpa["supporting_evidence_ids"] == ["current-1"]
    assert kpa["historical_supporting_evidence_ids"] == ["hist-1"]
    assert kpa["confidence"] == "高"
    cand = kpa["candidate_key_points"][0]
    assert cand["supporting_evidence_ids"] is None
    assert cand["historical_supporting_evidence_ids"] == ["hist-2"]
    assert "key_point_assessment" in touched
    assert "key_point_assessment.candidate_key_points[0]" in touched


def test_historical_evidence_separation_noop_when_no_derived_ids():
    """derived_intelligence_idsが空/Noneの場合は何もしないことを確認する。"""
    response = {
        "key_point_assessment": {
            "confidence": "高", "supporting_evidence_ids": ["e-1"], "candidate_key_points": [],
        },
    }
    touched = guard.separate_historical_evidence_in_key_point(response, None)
    assert touched == []
    assert response["key_point_assessment"]["supporting_evidence_ids"] == ["e-1"]
    assert "historical_supporting_evidence_ids" not in response["key_point_assessment"]


def test_actor_evidence_ids_stripped_of_derived_intelligence_ids():
    """Neutral再実行で発見したGap対応: key_actor_profiles[].evidence_idsに紛れ込んだ
    Derived Intelligence ID（historical_episode_id等）がseparate_historical_evidence_
    in_actor_and_situation_assessmentにより決定的に除去されることを確認する
    （scrub_fabricated_evidence_idsと同じin-place除去方式、新規schema fieldは
    追加しない）。"""
    response = {
        "key_actor_profiles": [{
            "actor_ref": "a", "actor": "A", "confidence": "中",
            "evidence_ids": ["current-1", "hist-1"],
        }],
    }
    touched = guard.separate_historical_evidence_in_actor_and_situation_assessment(response, {"hist-1"})
    assert response["key_actor_profiles"][0]["evidence_ids"] == ["current-1"]
    assert touched == ["key_actor_profiles[0]:removed=['hist-1']"]


def test_actor_evidence_ids_become_none_when_only_historical():
    """evidence_idsがHistorical IDのみだった場合、除去後はNoneになることを確認する
    （後続のcap_confidence_by_general_knowledge_flagが正しく『Current Evidenceなし』
    と判定できるようにするため）。"""
    response = {
        "key_actor_profiles": [{
            "actor_ref": "a", "actor": "A", "confidence": "高", "evidence_ids": ["hist-1"],
            "actor_intelligence_provenance": {"general_knowledge_used": True},
        }],
    }
    guard.separate_historical_evidence_in_actor_and_situation_assessment(response, {"hist-1"})
    assert response["key_actor_profiles"][0]["evidence_ids"] is None
    # 除去後にcap_confidence_by_general_knowledge_flagを実行すると正しく低へ引き下がる
    adjusted = guard.cap_confidence_by_general_knowledge_flag(response)
    assert response["key_actor_profiles"][0]["confidence"] == "低"
    assert adjusted == ["key_actor_profiles[0]"]


def test_situation_actor_assessment_evidence_ids_stripped_of_derived_intelligence_ids():
    """situation_actor_assessment[].evidence_idsについても同様にDerived Intelligence
    IDが除去されることを確認する（evidence_attribution_not_conflated Guardの
    再発防止）。"""
    response = {
        "situation_actor_assessment": [{
            "actor_ref": "a", "confidence": "中", "evidence_ids": ["current-1", "hist-1"],
        }],
    }
    touched = guard.separate_historical_evidence_in_actor_and_situation_assessment(response, {"hist-1"})
    assert response["situation_actor_assessment"][0]["evidence_ids"] == ["current-1"]
    assert touched == ["situation_actor_assessment[0]:removed=['hist-1']"]


def test_actor_situation_evidence_separation_noop_when_no_derived_ids():
    """derived_intelligence_idsが空/Noneの場合は何もしないことを確認する。"""
    response = {
        "key_actor_profiles": [{"actor_ref": "a", "actor": "A", "confidence": "中", "evidence_ids": ["e-1"]}],
        "situation_actor_assessment": [{"actor_ref": "a", "confidence": "中", "evidence_ids": ["e-2"]}],
    }
    touched = guard.separate_historical_evidence_in_actor_and_situation_assessment(response, None)
    assert touched == []
    assert response["key_actor_profiles"][0]["evidence_ids"] == ["e-1"]
    assert response["situation_actor_assessment"][0]["evidence_ids"] == ["e-2"]


def test_pattern_applicability_fallback_filled_as_uncertain_when_missing():
    """P1 3点修正: relevant_decision_patternsを参照しながらpattern_applicability_
    assessmentが空の場合、ensure_pattern_applicability_assessmentがapplicability=
    'uncertain'のFallback Entryを決定的に補完することを確認する（Historical Patternを
    自動適用せず、安全側へ倒す）。補完後は_actor_pattern_applicability_presentが
    passすることも確認する。"""
    response = {
        "key_actor_profiles": [{
            "actor_ref": "a", "actor": "A", "confidence": "中",
            "relevant_decision_patterns": ["過去にXした傾向がある"],
            "pattern_applicability_assessment": None,
        }],
    }
    touched = guard.ensure_pattern_applicability_assessment(response)
    kap = response["key_actor_profiles"][0]
    assert touched == ["key_actor_profiles[0]"]
    assert len(kap["pattern_applicability_assessment"]) == 1
    entry = kap["pattern_applicability_assessment"][0]
    assert entry["pattern_summary"] == "過去にXした傾向がある"
    assert entry["applicability"] == "uncertain"
    assert entry["conclusion"]
    result = guard._actor_pattern_applicability_present(response)
    assert result["passed"] is True


def test_pattern_applicability_fallback_only_fills_missing_patterns():
    """複数Patternのうち一部だけpattern_applicability_assessmentが既に存在する場合、
    既存分は保持したまま、欠落分のみFallback Entryを追加することを確認する。"""
    response = {
        "key_actor_profiles": [{
            "actor_ref": "a", "actor": "A", "confidence": "中",
            "relevant_decision_patterns": ["Pattern1", "Pattern2"],
            "pattern_applicability_assessment": [{
                "pattern_summary": "Pattern1", "applicability": "medium",
                "conclusion": "既存の評価",
            }],
        }],
    }
    touched = guard.ensure_pattern_applicability_assessment(response)
    kap = response["key_actor_profiles"][0]
    assert touched == ["key_actor_profiles[0]"]
    summaries = {e["pattern_summary"]: e for e in kap["pattern_applicability_assessment"]}
    assert summaries["Pattern1"]["applicability"] == "medium"
    assert summaries["Pattern1"]["conclusion"] == "既存の評価"
    assert summaries["Pattern2"]["applicability"] == "uncertain"


def test_pattern_applicability_fallback_noop_when_no_patterns_referenced():
    """relevant_decision_patternsが無い場合は何もしないことを確認する。"""
    response = {
        "key_actor_profiles": [{
            "actor_ref": "a", "actor": "A", "confidence": "中",
            "relevant_decision_patterns": None,
            "pattern_applicability_assessment": None,
        }],
    }
    touched = guard.ensure_pattern_applicability_assessment(response)
    assert touched == []
    assert response["key_actor_profiles"][0]["pattern_applicability_assessment"] is None


def test_cross_intelligence_contradiction_not_silently_dropped_requires_reason():
    """既存M9チェックとの整合確認: statusがcontradicted/weak/unsupportedのmedia
    hypothesisにalternative_explanationが無い場合、失敗することを確認する。"""
    response = {
        "media_source_context": {
            "media_editorial_context": [{
                "hypotheses": [{"status": "contradicted", "alternative_explanation": None}],
            }],
        },
    }
    result = guard._cross_intelligence_contradiction_not_silently_dropped(response)
    assert result["passed"] is False


# ==================== Phase R1 Chunk2: Evidence Relevance / Source Type軸 ====================

def _kpa(confidence="中", evidence_relevance=None, supporting_evidence_ids=None):
    return {
        "primary_key_point": "x", "confidence": confidence,
        "evidence_relevance": evidence_relevance,
        "supporting_evidence_ids": supporting_evidence_ids,
    }


def test_cap_confidence_by_claim_relevance_downgrades_when_only_structural():
    """evidence_relevanceが全てstructural/peripheralのみの場合、confidence=高が
    決定的に低へ引き下げられることを確認する（evidence_class起点の調整とは別軸）。"""
    response = {"key_point_assessment": _kpa(confidence="高", evidence_relevance=[
        {"evidence_id": "e-1", "claim_relevance": "structural"},
        {"evidence_id": "e-2", "claim_relevance": "peripheral"},
    ])}
    adjusted = guard.cap_confidence_by_claim_relevance(response)
    assert response["key_point_assessment"]["confidence"] == "低"
    assert response["key_point_assessment"]["relevance_confidence_adjustment_reason"]
    assert adjusted == ["key_point_assessment"]


def test_cap_confidence_by_claim_relevance_keeps_confidence_when_direct_present():
    """direct/strongly_relevantが1件でもあれば引き下げられないことを確認する。"""
    response = {"key_point_assessment": _kpa(confidence="高", evidence_relevance=[
        {"evidence_id": "e-1", "claim_relevance": "structural"},
        {"evidence_id": "e-2", "claim_relevance": "direct"},
    ])}
    adjusted = guard.cap_confidence_by_claim_relevance(response)
    assert response["key_point_assessment"]["confidence"] == "高"
    assert adjusted == []


def test_cap_confidence_by_claim_relevance_no_op_when_relevance_not_reported():
    """evidence_relevance自体が未申告（空/None）の場合は対象外（『申告なし』と
    『申告した上で全て弱い』を区別する）ことを確認する。"""
    response = {"key_point_assessment": _kpa(confidence="高", evidence_relevance=None)}
    adjusted = guard.cap_confidence_by_claim_relevance(response)
    assert response["key_point_assessment"]["confidence"] == "高"
    assert adjusted == []


def test_cap_confidence_by_claim_relevance_applies_to_main_scenario_independently():
    """main_scenarioとkey_point_assessmentは独立して評価され、互いの
    confidence_adjustment_reasonを上書きしないことを確認する。"""
    response = {
        "key_point_assessment": _kpa(confidence="中", evidence_relevance=None),
        "main_scenario": {
            "confidence": "高",
            "evidence_relevance": [{"evidence_id": "e-1", "claim_relevance": "peripheral"}],
        },
    }
    adjusted = guard.cap_confidence_by_claim_relevance(response)
    assert response["main_scenario"]["confidence"] == "低"
    assert response["main_scenario"]["relevance_confidence_adjustment_reason"]
    assert response["key_point_assessment"]["confidence"] == "中"
    assert adjusted == ["main_scenario"]


def test_claim_evidence_semantic_relevance_present_fails_when_missing():
    """key_point_assessment.supporting_evidence_idsが非空なのにevidence_relevanceが
    未申告の場合、Guardが失敗することを確認する。"""
    response = {"key_point_assessment": _kpa(supporting_evidence_ids=["e-1"], evidence_relevance=None)}
    result = guard._claim_evidence_semantic_relevance_present_check(response)
    assert result["passed"] is False


def test_claim_evidence_semantic_relevance_present_fails_on_fabricated_relevance_id():
    """evidence_relevanceのevidence_idがsupporting_evidence_idsに実在しない場合、
    Semantic Relevance errorとして検出することを確認する。"""
    response = {"key_point_assessment": _kpa(
        supporting_evidence_ids=["e-1"],
        evidence_relevance=[{"evidence_id": "e-fabricated", "claim_relevance": "direct"}],
    )}
    result = guard._claim_evidence_semantic_relevance_present_check(response)
    assert result["passed"] is False


def test_claim_evidence_semantic_relevance_present_passes_when_consistent():
    response = {"key_point_assessment": _kpa(
        supporting_evidence_ids=["e-1"],
        evidence_relevance=[{"evidence_id": "e-1", "claim_relevance": "direct"}],
    )}
    result = guard._claim_evidence_semantic_relevance_present_check(response)
    assert result["passed"] is True


def test_claim_evidence_semantic_relevance_not_required_for_main_scenario_free_text():
    """main_scenario.supporting_evidenceは自由記述混在のため、evidence_relevance
    未申告だけでは失敗しないことを確認する（key_point_assessmentとの非対称、意図的）。"""
    response = {
        "key_point_assessment": _kpa(supporting_evidence_ids=None, evidence_relevance=None),
        "main_scenario": {"supporting_evidence": ["頻度増加"], "evidence_relevance": None},
    }
    result = guard._claim_evidence_semantic_relevance_present_check(response)
    assert result["passed"] is True


def test_direct_evidence_present_for_primary_key_point_fails_without_direct():
    """confidence=高なのにevidence_relevanceにdirect/strongly_relevantが1件も
    無い場合、失敗することを確認する（Case2 Semiconductorの誤昇格再発防止）。"""
    response = {"key_point_assessment": _kpa(confidence="高", evidence_relevance=[
        {"evidence_id": "e-1", "claim_relevance": "structural"},
    ])}
    result = guard._direct_evidence_present_for_primary_key_point_check(response)
    assert result["passed"] is False


def test_direct_evidence_present_for_primary_key_point_allows_low_confidence_abstention():
    """Phase R1原則: Direct Evidenceが無いこと自体をFailにしない。confidence=低で
    知識ギャップを明示する『保留』は、Guard・repair・response失敗のいずれにも
    引っかからない安全な回答であることを確認する（逆張りを強制しないのと同じ思想）。"""
    response = {
        "key_point_assessment": _kpa(confidence="低", evidence_relevance=[
            {"evidence_id": "e-1", "claim_relevance": "structural"},
            {"evidence_id": "e-2", "claim_relevance": "peripheral"},
        ]),
        "identified_knowledge_gaps": ["Direct Evidenceが不足しており、断定できない"],
    }
    direct_check = guard._direct_evidence_present_for_primary_key_point_check(response)
    assert direct_check["passed"] is True
    # advisory-only（repairを強制しない）であることを確認する
    assert "direct_evidence_present_for_primary_key_point" not in guard.MODE_V12_CHECK_NAMES
    # confidence=低のままcap関数を通しても、さらに引き下げられたり例外になったりしない
    adjusted = guard.cap_confidence_by_claim_relevance(response)
    assert adjusted == []
    assert response["key_point_assessment"]["confidence"] == "低"


def test_direct_evidence_present_for_primary_key_point_passes_without_relevance_reported():
    """evidence_relevance自体が未申告の場合はこのGuardの対象外（別の
    claim_evidence_semantic_relevance_present Guardが検出する）ことを確認する。"""
    response = {"key_point_assessment": _kpa(confidence="高", evidence_relevance=None)}
    result = guard._direct_evidence_present_for_primary_key_point_check(response)
    assert result["passed"] is True


def test_apply_known_source_types_overwrites_llm_self_report():
    """Phase R1 Chunk2 P0(source_type決定性): geo_source_registry起点で解決できた
    evidence_idについては、LLMが何と自己申告していても決定論的な値へ強制することを
    確認する（『LLMに"これはprimary"と言わせるだけ』を防ぐ）。"""
    response = {"key_point_assessment": _kpa(evidence_relevance=[
        {"evidence_id": "e-1", "claim_relevance": "direct", "source_type": "specialist_analysis"},
    ])}
    touched = guard.apply_known_source_types(response, {"e-1": "primary_official"})
    assert response["key_point_assessment"]["evidence_relevance"][0]["source_type"] == "primary_official"
    assert touched == ["key_point_assessment.evidence_relevance[0]"]


def test_apply_known_source_types_forces_unknown_when_unresolvable():
    """decision経路で解決できないevidence_id（登録済みSourceに紐付かない等）は、
    LLMの自己申告に関わらずunknownへ強制されることを確認する。"""
    response = {"key_point_assessment": _kpa(evidence_relevance=[
        {"evidence_id": "e-unregistered", "claim_relevance": "direct", "source_type": "primary_official"},
    ])}
    guard.apply_known_source_types(response, {})
    assert response["key_point_assessment"]["evidence_relevance"][0]["source_type"] == "unknown"


def test_apply_known_source_types_applies_to_main_scenario_too():
    response = {"main_scenario": {
        "evidence_relevance": [{"evidence_id": "e-2", "claim_relevance": "direct", "source_type": None}],
    }}
    guard.apply_known_source_types(response, {"e-2": "current_news"})
    assert response["main_scenario"]["evidence_relevance"][0]["source_type"] == "current_news"


def test_scrub_fabricated_evidence_ids_removes_fabricated_key_point_supporting_ids():
    """Phase R1 Chunk4 P0 carry-over（実E2Eで発見: institution_BIS_mandate等の架空ID）:
    key_point_assessment.supporting_evidence_ids（トップレベル＋candidate_key_points[]）が
    known_idsに存在しないIDを除去することを確認する（main_scenario.supporting_evidenceは
    自由記述混在のため対象にしない、既存方針のまま）。"""
    response = {
        "key_point_assessment": {
            "supporting_evidence_ids": ["e-1", "institution_BIS_mandate"],
            "candidate_key_points": [
                {"key_point": "k1", "supporting_evidence_ids": ["e-1", "institution_BIS_decision_authority"]},
            ],
        },
    }
    removed = guard.scrub_fabricated_evidence_ids(response, known_ids={"e-1"})
    assert response["key_point_assessment"]["supporting_evidence_ids"] == ["e-1"]
    assert response["key_point_assessment"]["candidate_key_points"][0]["supporting_evidence_ids"] == ["e-1"]
    assert any("institution_BIS_mandate" in r for r in removed)
    assert any("institution_BIS_decision_authority" in r for r in removed)


# ── Tier1(Eguchi Expert AI Final v1.0対応、2026-09-06修正): directional_perceptions ──
# 機能試験で、実文書2件のIDを組み合わせた捏造evidence_idが無検出のまま最終出力に残る
# ことが判明した（scrub_fabricated_evidence_idsの走査対象からdirectional_perceptionsが
# 漏れていた）。valid/fabricated/混在の3パターンで修正を確認する。

def test_scrub_fabricated_evidence_ids_removes_fabricated_directional_perception_id():
    response = {
        "directional_perceptions": [
            {"observer_actor_ref": "trump", "target_actor_ref": "xi",
             "perception": "p", "claim_type": "analyst_hypothesis", "confidence": "低",
             "evidence_ids": ["e-1", "fabricated-blended-id"]},
        ],
    }
    removed = guard.scrub_fabricated_evidence_ids(response, known_ids={"e-1"})
    assert response["directional_perceptions"][0]["evidence_ids"] == ["e-1"]
    assert any("fabricated-blended-id" in r for r in removed)


def test_scrub_fabricated_evidence_ids_keeps_valid_directional_perception_ids():
    response = {
        "directional_perceptions": [
            {"observer_actor_ref": "trump", "target_actor_ref": "xi",
             "perception": "p", "claim_type": "stated", "confidence": "中",
             "evidence_ids": ["e-1", "e-2"]},
        ],
    }
    removed = guard.scrub_fabricated_evidence_ids(response, known_ids={"e-1", "e-2"})
    assert response["directional_perceptions"][0]["evidence_ids"] == ["e-1", "e-2"]
    assert removed == []


def test_scrub_fabricated_evidence_ids_does_not_remove_directional_perception_itself():
    """evidence_idsのみをスクラブし、observer/target_actor_ref等のperception自体は
    削除しないことを確認する（DirectionalPerceptionという意味的オブジェクト自体を
    Evidence Groundingの副作用で消してはいけない）。"""
    response = {
        "directional_perceptions": [
            {"observer_actor_ref": "trump", "target_actor_ref": "xi",
             "perception": "本人の認識", "claim_type": "analyst_hypothesis", "confidence": "低",
             "evidence_ids": ["fabricated-only"]},
        ],
    }
    guard.scrub_fabricated_evidence_ids(response, known_ids=set())
    dp = response["directional_perceptions"][0]
    assert dp["evidence_ids"] == []
    assert dp["observer_actor_ref"] == "trump"
    assert dp["target_actor_ref"] == "xi"
    assert dp["perception"] == "本人の認識"


def test_collect_evidence_ids_includes_key_point_supporting_evidence_ids():
    response = {"key_point_assessment": {"supporting_evidence_ids": ["e-1", "fake-id"]}}
    # evidence_ids_not_fabricated Guardは_collect_evidence_ids経由で検出するため、
    # run_quality_guard相当の直接呼び出しで確認する。
    ids = guard._collect_evidence_ids(response)
    assert "fake-id" in ids and "e-1" in ids


# ── Phase R1 Chunk4 P0: supporting_knowledge_refs（Evidence ≠ Knowledge） ─────

def test_supporting_knowledge_refs_grounded_passes_for_valid_actor_ref():
    response = {
        "key_actor_profiles": [{"actor_ref": "trump", "entity_id": "trump-id"}],
        "key_point_assessment": {
            "supporting_knowledge_refs": [{"type": "actor_intelligence", "ref": "trump", "field": "persistent_preference"}],
        },
    }
    result = guard._supporting_knowledge_refs_grounded_check(response)
    assert result["passed"] is True


def test_supporting_knowledge_refs_grounded_fails_for_fabricated_actor_ref():
    response = {
        "key_actor_profiles": [{"actor_ref": "trump", "entity_id": "trump-id"}],
        "key_point_assessment": {
            "supporting_knowledge_refs": [{"type": "actor_intelligence", "ref": "not_a_real_actor", "field": None}],
        },
    }
    result = guard._supporting_knowledge_refs_grounded_check(response)
    assert result["passed"] is False


def test_supporting_knowledge_refs_grounded_validates_institution_refs_by_subject_type():
    """type=institution_intelligenceのrefは、subject_type=institutionのentity_idのみ
    有効とすることを確認する（Person Actorのentity_idを誤って引用した場合を検出）。"""
    response = {
        "key_actor_profiles": [
            {"actor_ref": "trump", "entity_id": "trump-id",
             "actor_intelligence_provenance": {"subject_type": "person"}},
            {"actor_ref": "actor_BIS", "entity_id": "bis-id",
             "actor_intelligence_provenance": {"subject_type": "institution"}},
        ],
        "key_point_assessment": {
            "supporting_knowledge_refs": [{"type": "institution_intelligence", "ref": "bis-id", "field": "mandate"}],
        },
    }
    assert guard._supporting_knowledge_refs_grounded_check(response)["passed"] is True

    response["key_point_assessment"]["supporting_knowledge_refs"][0]["ref"] = "trump-id"
    assert guard._supporting_knowledge_refs_grounded_check(response)["passed"] is False


def test_scrub_fabricated_evidence_ids_removes_fabricated_relevance_entries():
    """Phase R1 Chunk2: evidence_relevance[].evidence_idもknown_ids照合の対象に
    含まれ、捏造IDのエントリが除去されることを確認する。"""
    response = {
        "key_point_assessment": {
            "evidence_relevance": [
                {"evidence_id": "e-1", "claim_relevance": "direct"},
                {"evidence_id": "e-fabricated", "claim_relevance": "direct"},
            ],
        },
    }
    removed = guard.scrub_fabricated_evidence_ids(response, known_ids={"e-1"})
    assert response["key_point_assessment"]["evidence_relevance"] == [
        {"evidence_id": "e-1", "claim_relevance": "direct"},
    ]
    assert any("e-fabricated" in r for r in removed)


def test_scrub_fabricated_evidence_ids_removes_fabricated_main_scenario_weakening_entries():
    """Phase 10-B(Weakening/Contradicting Evidence Structured Binding): main_scenario.
    weakening_evidence_relevance[].evidence_idもevidence_relevance（支持側）と同じ
    捏造スクラブの対象になることを確認する。"""
    response = {
        "main_scenario": {
            "weakening_evidence_relevance": [
                {"evidence_id": "e-1", "relation": "WEAKENS", "claim_relevance": "direct"},
                {"evidence_id": "e-fabricated", "relation": "CONTRADICTS", "claim_relevance": "direct"},
            ],
        },
    }
    removed = guard.scrub_fabricated_evidence_ids(response, known_ids={"e-1"})
    assert response["main_scenario"]["weakening_evidence_relevance"] == [
        {"evidence_id": "e-1", "relation": "WEAKENS", "claim_relevance": "direct"},
    ]
    assert any("e-fabricated" in r for r in removed)


def test_scrub_fabricated_evidence_ids_removes_fabricated_hypothesis_register_ids():
    """Phase 10-B: hypothesis_register[].supporting_evidence_ids/contradicting_evidence_ids
    （既存フィールド）は従来scrub_fabricated_evidence_idsの走査対象外だった
    （Referential Integrity Guardの既存の抜け）。weakening_evidence_relevance新設に
    合わせてこの3フィールドすべてをスクラブ対象へ揃えたことを確認する。"""
    response = {
        "hypothesis_register": [
            {
                "hypothesis_id": "H0",
                "supporting_evidence_ids": ["e-1", "e-fabricated-1"],
                "contradicting_evidence_ids": ["e-fabricated-2"],
                "weakening_evidence_relevance": [
                    {"evidence_id": "e-1", "relation": "WEAKENS", "claim_relevance": "structural"},
                    {"evidence_id": "e-fabricated-3", "relation": "CONTRADICTS", "claim_relevance": "direct"},
                ],
            },
        ],
    }
    removed = guard.scrub_fabricated_evidence_ids(response, known_ids={"e-1"})
    h0 = response["hypothesis_register"][0]
    assert h0["supporting_evidence_ids"] == ["e-1"]
    assert h0["contradicting_evidence_ids"] == []
    assert h0["weakening_evidence_relevance"] == [
        {"evidence_id": "e-1", "relation": "WEAKENS", "claim_relevance": "structural"},
    ]
    assert any("e-fabricated-1" in r for r in removed)
    assert any("e-fabricated-2" in r for r in removed)
    assert any("e-fabricated-3" in r for r in removed)


# ---------------------------------------------------------------------------
# Phase R1 Chunk5: Key Point Discovery / Frame-Breaking強化
# ---------------------------------------------------------------------------

def test_key_point_candidates_homogeneous_control_type_flagged():
    """3件以上の候補が全て同一control_typeの場合、比較検討が形だけになっている
    可能性をadvisoryで検出する。"""
    response = {
        "key_point_assessment": {
            "candidate_key_points": [
                {"key_point": "a", "control_type": "structural"},
                {"key_point": "b", "control_type": "structural"},
                {"key_point": "c", "control_type": "structural"},
            ],
        },
    }
    result = guard._key_point_candidates_not_homogeneous_check(response)
    assert result["passed"] is False


def test_key_point_candidates_diverse_control_type_passes():
    response = {
        "key_point_assessment": {
            "candidate_key_points": [
                {"key_point": "a", "control_type": "structural"},
                {"key_point": "b", "control_type": "actor_controlled"},
                {"key_point": "c", "control_type": "institutionally_constrained"},
            ],
        },
    }
    result = guard._key_point_candidates_not_homogeneous_check(response)
    assert result["passed"] is True


def test_key_point_candidates_two_entries_not_flagged_regardless_of_control_type():
    """候補が2件しか無い場合は、正当に少数の場合もあるためhomogeneous判定の対象外とする。"""
    response = {
        "key_point_assessment": {
            "candidate_key_points": [
                {"key_point": "a", "control_type": "structural"},
                {"key_point": "b", "control_type": "structural"},
            ],
        },
    }
    result = guard._key_point_candidates_not_homogeneous_check(response)
    assert result["passed"] is True


def test_key_point_selected_without_evidence_while_rejected_has_evidence_flagged():
    response = {
        "key_point_assessment": {
            "primary_key_point": "novel take",
            "supporting_evidence_ids": None,
            "candidate_key_points": [
                {"key_point": "novel take", "control_type": "structural", "supporting_evidence_ids": None},
                {"key_point": "boring take", "control_type": "actor_controlled",
                 "supporting_evidence_ids": ["e-1"]},
            ],
            "rejected_candidates": [{"key_point": "boring take", "why_not_primary": "..."}],
        },
    }
    result = guard._key_point_selection_not_unsupported_novelty_check(response)
    assert result["passed"] is False


def test_key_point_selected_with_own_evidence_not_flagged_even_if_rejected_also_has_evidence():
    response = {
        "key_point_assessment": {
            "primary_key_point": "grounded take",
            "supporting_evidence_ids": ["e-2"],
            "candidate_key_points": [
                {"key_point": "grounded take", "control_type": "structural", "supporting_evidence_ids": ["e-2"]},
                {"key_point": "boring take", "control_type": "actor_controlled",
                 "supporting_evidence_ids": ["e-1"]},
            ],
            "rejected_candidates": [{"key_point": "boring take", "why_not_primary": "..."}],
        },
    }
    result = guard._key_point_selection_not_unsupported_novelty_check(response)
    assert result["passed"] is True


# ---------------------------------------------------------------------------
# Phase R1 Human Check指摘（P1.2）: key_point_controllers_covered /
# primary_key_point_evidence_coverage
# ---------------------------------------------------------------------------

def test_key_point_controllers_covered_flags_missing_congress():
    """実際のCase2（サウジ・米・イスラエル）バグ再現: control_structure_summaryが
    『米議会』『規制機関』を明記しているのに、key_actor_profilesにMBS/Netanyahuしか
    いない場合を検出する。"""
    response = {
        "key_point_assessment": {
            "control_structure_summary": "米大統領府が交渉を主導する一方、条約・安全保障保証・"
                                          "原子力協力の多くは議会承認や規制機関の許認可に依存し、"
                                          "米国内の制度・政治構造がパッケージの上限を決める。",
            "hinge_structure": {"primary_hinge": "米国がサウジへの安全保障保証を議会・世論の許容範囲内でどこまで承認できるか"},
        },
        "key_actor_profiles": [
            {"actor": "Mohammed bin Salman", "formal_role": "サウジの実質的最高指導者"},
            {"actor": "Benjamin Netanyahu", "formal_role": "イスラエル首相"},
        ],
    }
    result = guard._key_point_controllers_covered_check(response)
    assert result["passed"] is False
    assert "議会" in result["detail"]


def test_key_point_controllers_covered_flags_missing_insurers_and_banks():
    """実際のCase3（インド・ロシア原油）バグ再現: 保険会社・銀行が本文に明記されているのに
    Key Actorに無い場合を検出する。"""
    response = {
        "key_point_assessment": {
            "control_structure_summary": "米国財務省（OFAC）やEUの制裁当局が定めるルールとガイダンス、"
                                          "保険会社・銀行のコンプライアンス判断が、ロシア産原油の"
                                          "取引条件を制度的に規定している。",
        },
        "key_actor_profiles": [
            {"actor": "インド政府"}, {"actor": "米国政府"}, {"actor": "EU"},
        ],
    }
    result = guard._key_point_controllers_covered_check(response)
    assert result["passed"] is False
    assert "保険会社" in result["detail"] or "銀行" in result["detail"]


def test_key_point_controllers_covered_passes_when_actor_present():
    """2026-09-19修正後: 名前一致だけでなく因果的役割（driving_forces等）も必要。"""
    response = {
        "key_point_assessment": {
            "control_structure_summary": "米議会の承認が必要である。",
        },
        "key_actor_profiles": [
            {"actor": "米議会", "formal_role": "条約承認権を持つ立法府",
             "driving_forces": ["条約承認権を通じた拒否権行使"]},
        ],
    }
    result = guard._key_point_controllers_covered_check(response)
    assert result["passed"] is True


def test_key_point_controllers_covered_passes_when_no_institutional_term():
    response = {
        "key_point_assessment": {"control_structure_summary": "両国の首脳間の個人的な信頼関係が重要である。"},
        "key_actor_profiles": [],
    }
    result = guard._key_point_controllers_covered_check(response)
    assert result["passed"] is True


# ---- Phase 6C-0 Regression Fixtures A-D（ユーザー指示、Quality Guard Integrity Audit）----

def test_key_point_controllers_covered_fixture_a_unrelated_mention_fails():
    """A: 無関係な文脈に「議会」がある → controllers_covered FAIL。
    US-China半導体Caseの実例回帰テスト: Trumpのwhy_actor_mattersに「議会」という語が
    無関係な文脈で出現しただけでは、Congress自体はKey Actorとしてモデル化されていない。"""
    response = {
        "key_point_assessment": {
            "control_structure_summary": "議会が制定したMATCH法の下で、商務省が輸出管理の"
                                          "詳細を執行する制度枠組みが規制強度を規定している。",
        },
        "key_actor_profiles": [
            {"actor": "Donald Trump", "formal_role": "米国大統領",
             "why_actor_matters": "議会・官僚・同盟国の意向を調整しつつ対中政策を主導するため",
             "driving_forces": ["支持基盤への訴求"]},
        ],
    }
    result = guard._key_point_controllers_covered_check(response)
    assert result["passed"] is False
    assert "議会" in result["detail"]


def test_key_point_controllers_covered_fixture_b_actor_without_causal_role_fails():
    """B: Congressがactorとして出るだけで、causal roleが無い → FAIL。"""
    response = {
        "key_point_assessment": {"control_structure_summary": "議会の承認が必要である。"},
        "key_actor_profiles": [
            {"actor": "米国議会", "formal_role": "立法府"},  # driving_forces等の因果フィールドなし
        ],
    }
    result = guard._key_point_controllers_covered_check(response)
    assert result["passed"] is False


def test_key_point_controllers_covered_fixture_c_actor_with_causal_role_passes():
    """C: Congressが制約・制度化・blocking actor等として局面メカニズムに組み込まれている
    → PASS。"""
    response = {
        "key_point_assessment": {"control_structure_summary": "議会の予算承認が制約となる。"},
        "key_actor_profiles": [
            {"actor": "米国議会", "formal_role": "立法府",
             "ability_to_block": "予算・条約承認権を通じた拒否権"},
        ],
    }
    result = guard._key_point_controllers_covered_check(response)
    assert result["passed"] is True


def test_key_point_controllers_covered_fixture_d_generic_government_does_not_substitute():
    """D: generic "government"だけでCongressを代替 → PASSにしない。"""
    response = {
        "key_point_assessment": {"control_structure_summary": "議会の承認が最終的な鍵となる。"},
        "key_actor_profiles": [
            {"actor": "米国政府", "formal_role": "行政府", "driving_forces": ["対外交渉の一元化"]},
        ],
    }
    result = guard._key_point_controllers_covered_check(response)
    assert result["passed"] is False
    assert "議会" in result["detail"]


def test_key_point_controllers_covered_alias_granularity_passes():
    """Phase 6D-1回帰: TSMC実データで発見した偽陰性の再現防止。control_structure_
    summary側の要約表現「台湾当局」は、Actor自身のより具体的なlabel「台湾の貿易・
    産業当局（経済部国際貿易署など）」の部分文字列にならないが、同一施設を指す
    別粒度の表記として正しくPASSしなければならない（causal role fieldsが充実して
    いる場合）。"""
    response = {
        "key_point_assessment": {"control_structure_summary": "台湾当局の対応が焦点となる。"},
        "key_actor_profiles": [
            {"actor": "台湾の貿易・産業当局（経済部国際貿易署など）",
             "formal_role": "台湾の貿易政策・産業振興を担う",
             "driving_forces": [{"force": "対米協力", "direction": "supporting"}]},
        ],
    }
    result = guard._key_point_controllers_covered_check(response)
    assert result["passed"] is True


def test_key_point_controllers_covered_alias_still_requires_causal_role():
    """粒度差の許容が、6C-0で導入した「因果的役割を持つこと」の要件までは緩めない
    ことを確認する（Fixture Bの精神を粒度差許容ロジックにも適用）。"""
    response = {
        "key_point_assessment": {"control_structure_summary": "台湾当局の対応が焦点となる。"},
        "key_actor_profiles": [
            {"actor": "台湾の貿易・産業当局（経済部国際貿易署など）",
             "formal_role": "台湾の貿易政策・産業振興を担う"},
        ],
    }
    result = guard._key_point_controllers_covered_check(response)
    assert result["passed"] is False


def test_key_point_controllers_covered_alias_prefix_mismatch_still_fails():
    """粒度差許容ロジックが、異なる国・主体の同種機関まで取り違えないことを確認する
    （例: 「米議会」に対し「日本の国会（議会）」は代替にならない）。"""
    response = {
        "key_point_assessment": {"control_structure_summary": "米議会の承認が鍵となる。"},
        "key_actor_profiles": [
            {"actor": "日本の国会（議会）", "formal_role": "日本の立法府",
             "driving_forces": ["法案審議"]},
        ],
    }
    result = guard._key_point_controllers_covered_check(response)
    assert result["passed"] is False


def test_key_point_controllers_covered_generic_still_fails_after_alias_fix():
    """Phase 6C-0 Fixture Dの回帰確認: alias/粒度差許容ロジックを追加した後も、
    generic「government」がCongress等の特定制度名の代わりにはならないこと。
    （generic labelにはinstitution-type接尾語自体が含まれないため必ず不一致になる）"""
    response = {
        "key_point_assessment": {"control_structure_summary": "議会の承認が最終的な鍵となる。"},
        "key_actor_profiles": [
            {"actor": "米国政府", "formal_role": "行政府", "driving_forces": ["対外交渉の一元化"]},
        ],
    }
    result = guard._key_point_controllers_covered_check(response)
    assert result["passed"] is False


def test_key_point_controllers_covered_unrelated_controlling_entity_id_does_not_substitute():
    """Phase 6D-1回帰: 実データ検証で発見した落とし穴の直接再現。control_structure_
    summaryが2制度（議会/BIS相当）を名指しし、controlling_entity_idsは無関係な一方
    （BIS相当）のIDのみを含む場合、そのIDがkey_actor_profilesへ解決できても、
    別の欠落主体（議会）の代わりにはならない（canonical entity_idを一切coverage
    根拠に使わない設計になっていることの確認）。"""
    response = {
        "key_point_assessment": {
            "control_structure_summary": "議会が法令を制定し、規制機関が運用を具体化する。",
            "controlling_entity_ids": ["entity-regulator-1"],
        },
        "key_actor_profiles": [
            {"actor": "連邦規制機関", "formal_role": "輸出規制の運用主体", "entity_id": "entity-regulator-1",
             "driving_forces": ["規制運用"]},
        ],
    }
    result = guard._key_point_controllers_covered_check(response)
    assert result["passed"] is False
    assert "議会" in result["detail"]


def test_primary_key_point_evidence_coverage_flags_weak_main_scenario():
    """実際のCase4（グリーンランド）バグ再現: main_scenario.evidence_class=model_
    general_knowledgeのままMain Scenarioが構築されているケースを可視化する。"""
    response = {
        "key_point_assessment": {"evidence_class": "structural_reference"},
        "main_scenario": {"evidence_class": "model_general_knowledge"},
    }
    result = guard._primary_key_point_evidence_coverage_check(response)
    assert result["passed"] is False
    assert "main_scenario" in result["detail"]


def test_primary_key_point_evidence_coverage_passes_with_direct_evidence():
    response = {
        "key_point_assessment": {"evidence_class": "current_direct_evidence"},
        "main_scenario": {"evidence_class": "current_indirect_evidence"},
    }
    result = guard._primary_key_point_evidence_coverage_check(response)
    assert result["passed"] is True


# ---------------------------------------------------------------------------
# Phase R1（Chunk6後P0-2）: Temporal Hedge Language Guard
# ---------------------------------------------------------------------------

def test_temporal_hedge_detects_return_to_office_phrase_in_primary_key_point():
    """実際に発生したNATOケースのバグ再現: 現職であるにもかかわらず『再登場』という
    投機的表現がprimary_key_pointに使われているケースを検出する。"""
    response = {
        "key_point_assessment": {
            "primary_key_point": "米国のNATO関与の強弱（特にTrump再登場の可能性）が、欧州側の防衛自立議論を規定している",
        },
    }
    result = guard._temporal_hedge_language_check(response)
    assert result["passed"] is False
    assert "primary_key_point" in result["detail"]


def test_temporal_hedge_detects_as_of_string_leaking_into_prose():
    """実際に発生したEUケースのバグ再現: 内部フィールド名らしき'as_of'という文字列と
    古い日付が、one_line_assessmentという自由文へ混入しているケースを検出する。"""
    response = {
        "one_line_assessment": "as_of 2024-06頃の公開情報に基づけば、EUの対ロシア経済制裁は大枠維持・自動延長を基本線とする。",
    }
    result = guard._temporal_hedge_language_check(response)
    assert result["passed"] is False
    assert "one_line_assessment" in result["detail"]


def test_temporal_hedge_passes_when_no_suspicious_phrase_present():
    response = {
        "key_point_assessment": {"primary_key_point": "EU理事会における制裁延長決定のコンセンサス要件"},
        "main_scenario": {"description": "現在の局面は制度的な合意形成プロセスに規定されている。"},
        "one_line_assessment": "現時点でEUの制裁延長は理事会のコンセンサス形成に依存している。",
    }
    result = guard._temporal_hedge_language_check(response)
    assert result["passed"] is True


def test_temporal_hedge_checks_candidate_key_points_too():
    response = {
        "key_point_assessment": {
            "primary_key_point": "正常な表現",
            "candidate_key_points": [
                {"key_point": "米国のincoming administrationが政策を左右する"},
            ],
        },
    }
    result = guard._temporal_hedge_language_check(response)
    assert result["passed"] is False
    assert "candidate_key_points[0]" in result["detail"]


def test_key_point_unresolved_primary_does_not_trigger_novelty_flag():
    """primary_key_point='unresolved'（Chunk5で許容した保守的フォールバック）は、
    候補として存在しないためnovelty guardの対象外として素通しされることを確認する。"""
    response = {
        "key_point_assessment": {
            "primary_key_point": "unresolved",
            "candidate_key_points": [
                {"key_point": "a", "control_type": "structural", "supporting_evidence_ids": None},
                {"key_point": "b", "control_type": "actor_controlled", "supporting_evidence_ids": ["e-1"]},
            ],
            "rejected_candidates": [{"key_point": "b", "why_not_primary": "..."}],
        },
    }
    result = guard._key_point_selection_not_unsupported_novelty_check(response)
    assert result["passed"] is True
