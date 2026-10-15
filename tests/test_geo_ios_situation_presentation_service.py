"""Geopolitical Intelligence OS: Situation Analysis Presentation v1のテスト。

既存Expert AI構造化出力（schemas/geo_expert_ai_response.schema.json）を、新しい推論を
加えずに7セクションへ整形できていることを確認する。特に:
  - 入力が空の項目は無理に埋めず省略する
  - main_scenario/alternative_scenariosで参照先fieldが異なる（weakening_information等は
    トップレベル、weakening_conditionsはalternative側のみ）ことを正しく区別する
  - _source（Debug Traceability）が正しいfield pathを指す
  - strip_debug_sourcesで_sourceのみを再帰的に除去できる
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_situation_presentation_service as presentation_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _main_candidate(**overrides):
    raw = {
        "description": "主シナリオの説明",
        "confidence": "中",
        "supporting_evidence": ["証拠A", "証拠B"],
        "selection_rationale": "他の仮説より整合的だから",
        "assumptions": ["前提1"],
        "observable_indicators": ["指標1", "指標2"],
        "derived_from_hypothesis_id": "H0",
    }
    raw.update(overrides.pop("raw_overrides", {}))
    return {
        "scenario_candidate_id": "c-main", "rank": 1, "displayed": True,
        "title": "主シナリオの説明",
        "content": {
            "why_this_matters": "他の仮説より整合的だから", "origin": "main_scenario", "raw": raw,
            "key_actors": ["Actor A", "Actor B"],
        },
        **overrides,
    }


def _alt_candidate(**overrides):
    raw = {
        "description": "代替シナリオの説明",
        "confidence": "低",
        "strengthening_conditions": ["材料X"],
        "weakening_conditions": ["材料Y"],
        "switch_conditions": ["条件Z"],
        "derived_from_hypothesis_id": None,
    }
    raw.update(overrides.pop("raw_overrides", {}))
    return {
        "scenario_candidate_id": "c-alt", "rank": 2, "displayed": True,
        "title": "代替シナリオの説明",
        "content": {
            "why_this_matters": "この分岐が有力になる条件: 条件Z", "origin": "alternative_scenario", "raw": raw,
        },
        **overrides,
    }


def _response_json(**overrides):
    base = {
        "one_line_assessment": "一言見立て",
        "key_point_assessment": {
            "primary_key_point": "主要な蝶番",
            "selection_rationale": "これが最も局面を規定するから",
            "hinge_structure": {"primary_hinge": "変数A"},
        },
        "hypothesis_register": [
            {"hypothesis_id": "H0", "status": "retained", "reason": None},
            {"hypothesis_id": "H1", "status": "unresolved", "reason": "まだ確認できていない点"},
        ],
        "weakening_information": "反対方向を示す情報",
        "contradicting_evidence": ["矛盾する根拠1"],
        "retraction_conditions": ["撤回条件1"],
        "key_actor_profiles": [
            {"actor": "Actor A", "ultimate_objective": "Aの目的", "red_line": "Aのレッドライン",
             "observed_action": "Aの現在の動き", "why_actor_matters": "Aが局面を動かせる理由"},
        ],
        "monitoring_indicators": ["監視指標1"],
        "identified_knowledge_gaps": [{"description": "ギャップ1", "recommended_action": "調査する"}],
        "additional_research": [{"question": "追加調査の問い1", "rationale": "重要だから"}],
    }
    base.update(overrides)
    return base


def _design(**overrides):
    base = {"investigation_question": "今回何を調べるべきか", "watch_points": ["設計側の監視点"]}
    base.update(overrides)
    return base


# ---- build_key_point ---------------------------------------------------------

def test_build_key_point_uses_existing_fields_without_reframing():
    result = presentation_service.build_key_point(design=_design(), response_json=_response_json())
    assert result["investigation_question"]["value"] == "今回何を調べるべきか"
    assert result["investigation_question"]["_source"] == "analysis_design.investigation_question"
    assert result["primary_key_point"]["value"] == "主要な蝶番"
    assert result["primary_hinge"]["value"] == "変数A"
    assert result["why_this_is_the_key_point"]["value"] == "これが最も局面を規定するから"


def test_build_key_point_omits_missing_fields_instead_of_fabricating():
    result = presentation_service.build_key_point(
        design=None, response_json=_response_json(key_point_assessment=None))
    assert result["investigation_question"] is None
    assert result["primary_key_point"] is None
    assert result["one_line_assessment"]["value"] == "一言見立て"


# ---- build_interpretations ---------------------------------------------------

def test_main_candidate_selection_rationale_and_supporting_points_are_separate_fields():
    """2026-09-23変更、ユーザー指摘「『この読み方が強い理由は』はまとめないで、CQASを
    それぞれのパートに分けて説明しよ」への対応検証: selection_rationale（単一の理由）と
    supporting_points（それを補強する個別材料）が別fieldとして返ることを確認する。"""
    result = presentation_service.build_interpretations(
        candidates=[_main_candidate()], response_json=_response_json())
    main = result[0]
    assert main["is_main"] is True
    assert main["selection_rationale"]["value"] == "他の仮説より整合的だから"
    supporting_texts = [r["value"] for r in main["supporting_points"]]
    assert "証拠A" in supporting_texts and "証拠B" in supporting_texts


def test_interpretation_includes_full_description_and_key_actors():
    result = presentation_service.build_interpretations(
        candidates=[_main_candidate()], response_json=_response_json())
    main = result[0]
    assert main["description"]["value"] == "主シナリオの説明"
    assert main["key_actors"]["value"] == ["Actor A", "Actor B"]


def test_interpretation_key_actors_omitted_when_absent():
    candidate = _alt_candidate()
    result = presentation_service.build_interpretations(
        candidates=[candidate], response_json=_response_json())
    assert result[0]["key_actors"] is None


def test_main_candidate_weak_points_come_from_top_level_response_fields():
    """weakening_information/contradicting_evidenceはmain_scenarioオブジェクトの中ではなく
    response_jsonのトップレベルにある——main candidateにのみ適用されることを確認する。"""
    result = presentation_service.build_interpretations(
        candidates=[_main_candidate()], response_json=_response_json())
    main = result[0]
    weak_sources = {w["_source"] for w in main["weak_points"]}
    assert "response.weakening_information" in weak_sources
    assert "response.contradicting_evidence" in weak_sources


def test_alt_candidate_weak_points_come_from_its_own_weakening_conditions_not_top_level():
    result = presentation_service.build_interpretations(
        candidates=[_alt_candidate()], response_json=_response_json())
    alt = result[0]
    assert alt["is_main"] is False
    assert len(alt["weak_points"]) == 1
    assert alt["weak_points"][0]["value"] == "材料Y"
    assert alt["weak_points"][0]["_source"] == "alternative_scenarios[].weakening_conditions"


def test_alt_candidate_supporting_points_come_from_strengthening_conditions_not_description():
    """2026-09-23変更: alternativeのdescriptionは既にtop-level description fieldとして
    返るため、supporting_pointsへは重複させない（strengthening_conditionsのみ）。
    alternativeにはselection_rationale相当が無いためNoneのままでよい。"""
    result = presentation_service.build_interpretations(
        candidates=[_alt_candidate()], response_json=_response_json())
    alt = result[0]
    assert alt["selection_rationale"] is None
    supporting_texts = [r["value"] for r in alt["supporting_points"]]
    assert supporting_texts == ["材料X"]


def test_main_candidate_unknowns_include_unresolved_hypotheses():
    result = presentation_service.build_interpretations(
        candidates=[_main_candidate()], response_json=_response_json())
    main = result[0]
    unknown_texts = [u["value"] for u in main["unknowns"]]
    assert "まだ確認できていない点" in unknown_texts


def test_alt_candidate_without_linked_hypothesis_has_no_unknowns():
    result = presentation_service.build_interpretations(
        candidates=[_alt_candidate()], response_json=_response_json())
    alt = result[0]
    assert alt["unknowns"] == []


# ---- 気づく→見る→疑う→仮説→読む（2026-09-23追加、ユーザー指示） ----------------

def test_what_was_seen_resolves_actor_names_from_interaction_basis():
    """②何を見た？ interaction_basisのactor_ref/target_actor_refが、key_actor_profiles
    の表示名へ変換された1文になることを確認する。"""
    response_json = _response_json(
        key_actor_profiles=[
            {"actor_ref": "a1", "actor": "米国政権", "confidence": "中"},
            {"actor_ref": "a2", "actor": "イラン指導部", "confidence": "中"},
        ],
        hypothesis_register=[
            {"hypothesis_id": "H0", "status": "retained", "reason": None,
             "interaction_basis": {"basis_type": "DIRECTIONAL_PERCEPTION",
                                    "actor_ref": "a1", "target_actor_ref": "a2"}},
        ],
    )
    result = presentation_service.build_interpretations(candidates=[_main_candidate()], response_json=response_json)
    assert result[0]["what_was_seen"]["value"] == "米国政権とイラン指導部の関係に注目した。"


def test_what_was_seen_uses_gap_note_for_evidence_gap():
    response_json = _response_json(hypothesis_register=[
        {"hypothesis_id": "H0", "status": "retained", "reason": None,
         "interaction_basis": {"basis_type": "EVIDENCE_GAP",
                                "gap_note": "この主体間のやり取りを示す直接情報が無い"}},
    ])
    result = presentation_service.build_interpretations(candidates=[_main_candidate()], response_json=response_json)
    assert result[0]["what_was_seen"]["value"] == "この主体間のやり取りを示す直接情報が無い"


def test_what_was_seen_is_none_when_not_applicable():
    response_json = _response_json(hypothesis_register=[
        {"hypothesis_id": "H0", "status": "retained", "reason": None,
         "interaction_basis": {"basis_type": "NOT_APPLICABLE", "gap_note": "特定の相互作用を主張していない"}},
    ])
    result = presentation_service.build_interpretations(candidates=[_main_candidate()], response_json=response_json)
    assert result[0]["what_was_seen"] is None


def test_what_was_doubted_translates_hypothesis_type_and_includes_reason():
    response_json = _response_json(hypothesis_register=[
        {"hypothesis_id": "H0", "status": "retained", "hypothesis_type": "H1_instrumental_indirect",
         "reason": "対外向けの発信の可能性を排除できない"},
    ])
    result = presentation_service.build_interpretations(candidates=[_main_candidate()], response_json=response_json)
    assert result[0]["what_was_doubted"]["value"] == \
        "別の意図・狙いがあるのではないか。対外向けの発信の可能性を排除できない"


def test_hypothesis_formed_uses_hypothesis_register_description():
    response_json = _response_json(hypothesis_register=[
        {"hypothesis_id": "H0", "status": "retained", "reason": None,
         "description": "この行動は国内向けの強硬姿勢アピールを主目的とする"},
    ])
    result = presentation_service.build_interpretations(candidates=[_main_candidate()], response_json=response_json)
    assert result[0]["hypothesis_formed"]["value"] == "この行動は国内向けの強硬姿勢アピールを主目的とする"


def test_story_fields_are_none_when_candidate_has_no_linked_hypothesis():
    """derived_from_hypothesis_idがNone（hypothesis_registerが生成されない分析
    プロファイル）の場合、見た/疑った/仮説は推測生成せずNoneのままであることを確認する
    （_alt_candidate()は既定でderived_from_hypothesis_id=None）。"""
    result = presentation_service.build_interpretations(
        candidates=[_alt_candidate()], response_json=_response_json())
    alt = result[0]
    assert alt["what_was_seen"] is None
    assert alt["what_was_doubted"] is None
    assert alt["hypothesis_formed"] is None


def test_main_candidate_outlook_uses_observable_indicators_and_retraction_conditions():
    result = presentation_service.build_interpretations(
        candidates=[_main_candidate()], response_json=_response_json())
    outlook = result[0]["outlook"]
    assert outlook["if_correct"]["value"] == ["指標1", "指標2"]
    assert outlook["weakens_if"]["value"] == ["撤回条件1"]


def test_alt_candidate_outlook_uses_switch_conditions_as_if_correct():
    """switch_conditions(主シナリオからこの代替仮説へ切り替える条件、既存フィールド)を
    if_correctとして使う——代替候補にも「これが観測されればこの読み方を支持する」という
    要素を持たせるための対応（以前はalternativeのif_correctが常にNoneだった）。"""
    result = presentation_service.build_interpretations(
        candidates=[_alt_candidate()], response_json=_response_json())
    outlook = result[0]["outlook"]
    assert outlook["if_correct"]["value"] == ["条件Z"]
    assert outlook["if_correct"]["_source"] == "alternative_scenarios[].switch_conditions"
    assert outlook["weakens_if"]["value"] == ["材料Y"]


def test_interpretations_are_ordered_by_existing_rank_not_reranked():
    """既存rank順（main=1、以降既存生成順）をそのまま維持する——presentation層で
    ranking変更は行わない。"""
    alt2 = _alt_candidate(scenario_candidate_id="c-alt2", rank=3)
    result = presentation_service.build_interpretations(
        candidates=[alt2, _main_candidate(), _alt_candidate()], response_json=_response_json())
    ranks = [r["rank"] for r in result]
    assert ranks == [1, 2, 3]


def test_undisplayed_candidate_keeps_displayed_flag_false():
    hidden = _main_candidate(scenario_candidate_id="c-hidden", rank=4, displayed=False)
    result = presentation_service.build_interpretations(
        candidates=[hidden], response_json=_response_json())
    assert result[0]["displayed"] is False


# ---- build_trajectory ---------------------------------------------------------

def test_build_trajectory_returns_none_when_absent():
    result = presentation_service.build_trajectory(response_json=_response_json())
    assert result is None


def test_build_trajectory_uses_trajectory_assessment_when_present():
    response_json = _response_json(trajectory_assessment={
        "reason": "過去の経緯が今回の局面につながっている",
        "evidence_gaps": [{"question": "いつからこの動きが始まったか"}],
    })
    result = presentation_service.build_trajectory(response_json=response_json)
    assert result["summary"]["value"] == "過去の経緯が今回の局面につながっている"
    assert result["gaps"]["value"] == ["いつからこの動きが始まったか"]


# ---- build_actors --------------------------------------------------------------

def test_build_actors_maps_known_fields():
    result = presentation_service.build_actors(response_json=_response_json())
    assert len(result) == 1
    actor = result[0]
    assert actor["actor"] == "Actor A"
    assert actor["wants"]["value"] == "Aの目的"
    assert actor["constraint"]["value"] == "Aのレッドライン"
    assert actor["current_move"]["value"] == "Aの現在の動き"


def test_build_actors_skips_actor_with_no_usable_field():
    response_json = _response_json(key_actor_profiles=[{"actor": "Actor Empty"}])
    result = presentation_service.build_actors(response_json=response_json)
    assert result == []


def test_build_actors_falls_back_to_vulnerabilities_when_no_red_line():
    response_json = _response_json(key_actor_profiles=[
        {"actor": "Actor B", "ultimate_objective": "Bの目的", "vulnerabilities": ["脆弱性1"]}])
    result = presentation_service.build_actors(response_json=response_json)
    assert result[0]["constraint"]["value"] == "脆弱性1"
    assert result[0]["constraint"]["_source"] == "key_actor_profiles[].red_line"


# ---- build_monitoring_signals（①の一部） ------------------------------------

def test_build_monitoring_signals_drops_near_duplicates():
    """実データで観測された事例（似た指標を複数生成していた）の再現テスト。
    文字bigram類似度0.3以上を重複として1件目のみ残す。"""
    response_json = _response_json(monitoring_indicators=[
        "中国海警・海軍の台湾周辺プレゼンス（月次隻数・位置）の推移（特に9/24会談前後）。",
        "中国海警・海軍の台湾周辺プレゼンス（隻数・位置）の月次データは、9/24会談前後でどう変化しているか。",
        "PLA航空sortieとADIZ進入の水準。",
    ])
    result = presentation_service.build_monitoring_signals(response_json=response_json)
    texts = [item["text"] for item in result]
    assert len(texts) == 2  # プレゼンス系は1件のみ残り、無関係なPLA項目は別途残る
    assert any("プレゼンス" in t for t in texts)
    assert any("PLA航空sortie" in t for t in texts)


# ---- build_research_needs（②） ------------------------------------------------

def test_build_research_needs_merges_two_sources_without_duplicates():
    response_json = _response_json(identified_knowledge_gaps=[{"description": "共通の論点"}])
    response_json["additional_research"] = [{"question": "共通の論点", "rationale": None}]
    result = presentation_service.build_research_needs(design=_design(), response_json=response_json)
    texts = [item["text"] for item in result]
    assert texts.count("共通の論点") == 1


def test_build_research_needs_drops_near_duplicate_items_across_sources():
    response_json = _response_json(
        identified_knowledge_gaps=[
            {"description": "9/24 Trump–Xi 会談のアジェンダ草案・リーク情報において、台湾・武器売却・"
                             "海峡通過がどのように位置づけられているか。"},
        ],
        additional_research=[
            {"question": "9/24 Trump–Xi 会談のアジェンダにおいて、AI shared risks・経済・台湾（武器売却・"
                         "海峡通過）がどのように位置づけられているか。"},
            {"question": "米議会・同盟国（日本・欧州など）の反応（台湾をディール材料にすることへの懸念・支持）。"},
        ],
    )
    result = presentation_service.build_research_needs(design=_design(), response_json=response_json)
    texts = [item["text"] for item in result]
    assert len(texts) == 2  # アジェンダ系1件(最初のみ)・議会反応1件
    assert any("アジェンダ" in t for t in texts)
    assert any("米議会" in t for t in texts)


def test_build_research_needs_falls_back_to_design_watch_points_when_response_has_none():
    response_json = _response_json(identified_knowledge_gaps=[], additional_research=[])
    result = presentation_service.build_research_needs(design=_design(), response_json=response_json)
    assert result[0]["text"] == "設計側の監視点"


# ---- Phase 10 Evidence Binding反映（supporting_evidence / weakening_evidence） ----

def test_main_candidate_supporting_evidence_uses_structured_evidence_relevance():
    response_json = _response_json(claims=[
        {"claim_type": "fact", "text": "証拠Aの本文", "source_ref": "ev-1"},
    ])
    candidate = _main_candidate(raw_overrides={
        "evidence_relevance": [
            {"evidence_id": "ev-1", "claim_relevance": "direct", "source_type": "current_news",
             "rationale": "直接支持する"},
        ],
    })
    result = presentation_service.build_interpretations(candidates=[candidate], response_json=response_json)
    ev = result[0]["supporting_evidence"]
    assert len(ev) == 1
    assert ev[0]["evidence_id"] == "ev-1"
    assert ev[0]["summary"] == "証拠Aの本文"
    assert ev[0]["claim_relevance"] == "direct"
    assert ev[0]["rationale"] == "直接支持する"
    assert ev[0]["_source"] == "main_scenario.evidence_relevance"


def test_main_candidate_weakening_evidence_uses_structured_field():
    response_json = _response_json(claims=[
        {"claim_type": "fact", "text": "反証の本文", "source_ref": "ev-2"},
    ])
    candidate = _main_candidate(raw_overrides={
        "weakening_evidence_relevance": [
            {"evidence_id": "ev-2", "relation": "WEAKENS", "claim_relevance": "structural",
             "source_type": "specialist_analysis", "rationale": "前提を弱める"},
        ],
    })
    result = presentation_service.build_interpretations(candidates=[candidate], response_json=response_json)
    ev = result[0]["weakening_evidence"]
    assert len(ev) == 1
    assert ev[0]["evidence_id"] == "ev-2"
    assert ev[0]["relation"] == "WEAKENS"
    assert ev[0]["summary"] == "反証の本文"
    assert ev[0]["_source"] == "main_scenario.weakening_evidence_relevance"


def test_interpretation_falls_back_to_legacy_fields_when_structured_evidence_absent():
    """evidence_relevance/weakening_evidence_relevanceが空（Phase 10以前のレスポンス等）でも、
    従来のsupporting_points/weak_points（自由記述fallback）は変わらず機能することを確認する。"""
    result = presentation_service.build_interpretations(
        candidates=[_main_candidate()], response_json=_response_json())
    main = result[0]
    assert main["supporting_evidence"] == []
    assert main["weakening_evidence"] == []
    supporting_texts = [r["value"] for r in main["supporting_points"]]
    assert "証拠A" in supporting_texts


def test_alt_candidate_weakening_evidence_comes_from_linked_hypothesis():
    response_json = _response_json(
        claims=[{"claim_type": "fact", "text": "H1を弱める根拠", "source_ref": "ev-3"}],
        hypothesis_register=[
            {"hypothesis_id": "H0", "status": "retained", "reason": None},
            {"hypothesis_id": "H1", "status": "unresolved", "reason": "まだ確認できていない点",
             "weakening_evidence_relevance": [
                 {"evidence_id": "ev-3", "relation": "CONTRADICTS", "claim_relevance": "direct",
                  "source_type": "current_news", "rationale": "H1と矛盾する"},
             ]},
        ],
    )
    candidate = _alt_candidate(raw_overrides={"derived_from_hypothesis_id": "H1"})
    result = presentation_service.build_interpretations(candidates=[candidate], response_json=response_json)
    ev = result[0]["weakening_evidence"]
    assert len(ev) == 1
    assert ev[0]["evidence_id"] == "ev-3"
    assert ev[0]["relation"] == "CONTRADICTS"
    assert ev[0]["_source"] == "hypothesis_register[H1].weakening_evidence_relevance"


# ---- closest_alternative marking -----------------------------------------------

def test_first_non_main_interpretation_is_marked_closest_alternative():
    alt2 = _alt_candidate(scenario_candidate_id="c-alt2", rank=3)
    result = presentation_service.build_interpretations(
        candidates=[_main_candidate(), _alt_candidate(), alt2], response_json=_response_json())
    assert result[0]["closest_alternative"] is False
    assert result[1]["closest_alternative"] is True
    assert result[2]["closest_alternative"] is False


def test_main_only_case_has_no_closest_alternative():
    result = presentation_service.build_interpretations(
        candidates=[_main_candidate()], response_json=_response_json())
    assert result[0]["closest_alternative"] is False


# ---- build_relationships --------------------------------------------------------

def test_build_relationships_returns_none_when_absent():
    result = presentation_service.build_relationships(response_json=_response_json())
    assert result is None


def test_build_relationships_uses_directional_perceptions_and_situation_actor_assessment():
    response_json = _response_json(
        directional_perceptions=[
            {"observer_actor_ref": "A", "target_actor_ref": "B", "perception": "Bを脅威とみなす",
             "claim_type": "inferred_from_behavior", "confidence": "中"},
        ],
        situation_actor_assessment=[
            {"actor_ref": "A", "issue_component": "領土問題", "salience": "high",
             "priority_rationale": "最優先課題", "effect_on_actor_objective": "直接影響",
             "instrumental_value": "intrinsic", "confidence": "中",
             "primary_usable_leverage": "経済制裁", "primary_binding_constraint": "国内世論",
             "actor_expected_response": "強く反発する", "analyst_expected_response": "限定的に譲歩する",
             "target_actor_ref": "B"},
        ],
    )
    result = presentation_service.build_relationships(response_json=response_json)
    assert result["perceptions"][0]["perception"]["value"] == "Bを脅威とみなす"
    assert result["leverage"][0]["leverage"]["value"] == "経済制裁"
    assert result["leverage"][0]["actor_expected_response"]["value"] == "強く反発する"
    assert result["leverage"][0]["analyst_expected_response"]["value"] == "限定的に譲歩する"


# ---- build_structural_constraints ------------------------------------------------

def test_build_structural_constraints_returns_none_when_absent():
    result = presentation_service.build_structural_constraints(response_json=_response_json())
    assert result is None


def test_build_structural_constraints_uses_discretion_assessment():
    response_json = _response_json(discretion_assessment={
        "discretion_mode": "STRUCTURE_DOMINANT", "mode_rationale": "法制度上の制約が支配的"})
    result = presentation_service.build_structural_constraints(response_json=response_json)
    assert result["discretion_mode"] == "STRUCTURE_DOMINANT"
    assert result["mode_rationale"]["value"] == "法制度上の制約が支配的"


# ---- build_media_context ---------------------------------------------------------

def test_build_media_context_returns_none_when_absent():
    result = presentation_service.build_media_context(response_json=_response_json())
    assert result is None


def test_build_media_context_uses_editorial_and_narrative_deviation():
    response_json = _response_json(media_source_context={
        "media_editorial_context": [{"outlet": "Outlet X", "caveat": "一次情報未確認"}],
        "narrative_deviation": [{"source": "Source Y", "deviation_from_mainstream": "主流と異なる報道",
                                  "why_it_matters": "重要な理由"}],
        "source_of_sources_note": "孫引き情報に注意",
    })
    result = presentation_service.build_media_context(response_json=response_json)
    assert result["editorial_context"][0]["outlet"] == "Outlet X"
    assert result["narrative_deviation"][0]["deviation_from_mainstream"]["value"] == "主流と異なる報道"
    assert result["source_of_sources_note"]["value"] == "孫引き情報に注意"


# ---- build_key_point: initial_anomaly ---------------------------------------------

def test_build_key_point_includes_initial_anomaly():
    response_json = _response_json(initial_anomaly="通常のパターンと異なる違和感")
    result = presentation_service.build_key_point(design=_design(), response_json=response_json)
    assert result["initial_anomaly"]["value"] == "通常のパターンと異なる違和感"
    assert result["initial_anomaly"]["_source"] == "response.initial_anomaly"


def test_build_key_point_omits_initial_anomaly_when_absent():
    result = presentation_service.build_key_point(design=_design(), response_json=_response_json())
    assert result["initial_anomaly"] is None


# ---- build_situation_presentation / strip_debug_sources -------------------------

def test_build_situation_presentation_sets_presentation_version():
    result = presentation_service.build_situation_presentation(
        design=_design(), candidates=[_main_candidate()], response_json=_response_json())
    assert result["presentation_version"] == "situation_analysis_v1"
    assert "key_point" in result and "interpretations" in result
    assert "actors" in result and "monitoring_signals" in result and "research_needs" in result
    assert "relationships" in result and "structural_constraints" in result and "media_context" in result
    assert result["relationships"] is None
    assert result["structural_constraints"] is None
    assert result["media_context"] is None


def test_strip_debug_sources_removes_all_source_keys_but_keeps_values():
    result = presentation_service.build_situation_presentation(
        design=_design(), candidates=[_main_candidate()], response_json=_response_json())
    stripped = presentation_service.strip_debug_sources(result)
    assert "_source" not in str(stripped)
    assert stripped["key_point"]["primary_key_point"]["value"] == "主要な蝶番"


def test_strip_debug_sources_does_not_mutate_original():
    result = presentation_service.build_situation_presentation(
        design=_design(), candidates=[_main_candidate()], response_json=_response_json())
    presentation_service.strip_debug_sources(result)
    assert result["key_point"]["primary_key_point"]["_source"] == \
        "response.key_point_assessment.primary_key_point"


# ---- 5つの読み方案UI再設計（2026-10-01）: build_actor_roles ----------------------
#
# 新しい推論はしない——既存interaction_basis/event_roleだけから機械的にPrimary/
# Peripheral/Unclassifiedへ分類できることを確認する。

def test_actor_roles_forced_primary_from_interaction_basis():
    response_json = _response_json(
        key_actor_profiles=[
            {"actor_ref": "a1", "actor": "米国政権"},
            {"actor_ref": "a2", "actor": "イラン指導部"},
        ],
        hypothesis_register=[
            {"hypothesis_id": "H0", "status": "retained", "reason": None,
             "interaction_basis": {"basis_type": "DIRECTIONAL_PERCEPTION",
                                    "actor_ref": "a1", "target_actor_ref": "a2"}},
        ],
    )
    candidate = _main_candidate(content={
        "why_this_matters": "x", "origin": "main_scenario",
        "raw": {"description": "d", "confidence": "中", "derived_from_hypothesis_id": "H0"},
        "key_actors": ["米国政権", "イラン指導部"],
    })
    result = presentation_service.build_interpretations(candidates=[candidate], response_json=response_json)
    roles = {r["actor"]: r["role"] for r in result[0]["actor_roles"]}
    assert roles["米国政権"] == "primary"


def test_actor_roles_own_agency_without_being_targeted_is_primary():
    profiles = [
        {"actor": "Actor A", "observed_action": "Aが行動した", "actor_ref": "a1"},
        {"actor": "Actor B", "actor_ref": "a2"},
    ]
    roles = presentation_service.build_actor_roles(
        linked_hypothesis=None, key_actors=["Actor A", "Actor B"], key_actor_profiles=profiles)
    by_name = {r["actor"]: r["role"] for r in roles}
    assert by_name["Actor A"] == "primary"
    assert by_name["Actor B"] == "unclassified"


def test_actor_roles_targeted_without_own_agency_is_peripheral():
    profiles = [
        {"actor": "Actor A", "actor_ref": "a1",
         "event_role": {"role_type": "pressure", "intended_target_actor_ref": "a2"}},
        {"actor": "Actor B", "actor_ref": "a2"},
    ]
    roles = presentation_service.build_actor_roles(
        linked_hypothesis=None, key_actors=["Actor A", "Actor B"], key_actor_profiles=profiles)
    by_name = {r["actor"]: r["role"] for r in roles}
    assert by_name["Actor A"] == "primary"
    assert by_name["Actor B"] == "peripheral"


def test_actor_roles_unclassified_when_no_profile_or_ambiguous():
    roles = presentation_service.build_actor_roles(
        linked_hypothesis=None, key_actors=["Actor Unknown"], key_actor_profiles=[])
    assert roles == [{"actor": "Actor Unknown", "role": "unclassified", "_source": None}]


def test_actor_roles_empty_when_no_key_actors():
    assert presentation_service.build_actor_roles(
        linked_hypothesis=None, key_actors=[], key_actor_profiles=[{"actor": "x"}]) == []


# ---- build_situation_phases ---------------------------------------------------

def test_situation_phases_assembles_trigger_action_reaction_current():
    response_json = {
        "trajectory_assessment": {"reason": "きっかけとなった出来事"},
        "key_actor_profiles": [
            {"actor": "Actor A", "actor_ref": "a1", "observed_action": "Aが行動した"},
        ],
        "situation_actor_assessment": [
            {"actor_ref": "a1", "actor_expected_response": "相手は強く反発すると見ている"},
        ],
    }
    description = {"value": "現在の読み", "_source": "main_scenario.description"}
    phases = presentation_service.build_situation_phases(
        response_json=response_json, primary_actor_name="Actor A", description=description)
    by_phase = {p["phase"]: p for p in phases}
    assert by_phase["trigger"]["description"]["value"] == "きっかけとなった出来事"
    assert by_phase["primary_action"]["actor"] == "Actor A"
    assert by_phase["primary_action"]["description"]["value"] == "Aが行動した"
    assert by_phase["reaction"]["description"]["value"] == "相手は強く反発すると見ている"
    assert by_phase["current"]["description"] is description


def test_situation_phases_skips_missing_stages_instead_of_forcing_four():
    phases = presentation_service.build_situation_phases(
        response_json={}, primary_actor_name=None, description=None)
    assert phases is None


def test_situation_phases_current_only_when_only_description_available():
    description = {"value": "現在の読みだけ", "_source": "x"}
    phases = presentation_service.build_situation_phases(
        response_json={}, primary_actor_name=None, description=description)
    assert len(phases) == 1
    assert phases[0]["phase"] == "current"


# ---- build_media_comparison -----------------------------------------------------

def test_build_media_comparison_returns_none_when_absent():
    assert presentation_service.build_media_comparison(response_json=_response_json()) is None


def test_build_media_comparison_surfaces_observed_and_hypotheses():
    response_json = _response_json(media_source_context={
        "media_editorial_context": [
            {"outlet": "Media A", "caveat": "編集方針に留意",
             "observed": [{"observation_type": "headline", "observation_text": "見出しで強調"}],
             "hypotheses": [{"category": "editorial_framing", "hypothesis_text": "対露圧力を強調する枠組み"}]},
        ],
    })
    result = presentation_service.build_media_comparison(response_json=response_json)
    assert len(result) == 1
    assert result[0]["outlet"] == "Media A"
    assert "見出しで強調" in result[0]["framing"]["value"]
    assert "対露圧力を強調する枠組み" in result[0]["interpretation"]["value"]


def test_build_media_comparison_skips_outlet_with_no_usable_content():
    response_json = _response_json(media_source_context={
        "media_editorial_context": [{"outlet": "Media Empty"}],
    })
    assert presentation_service.build_media_comparison(response_json=response_json) is None


# ---- Reading Report v2（2026-10-02）: Evidence item actor帰属 ----------------------

def test_supporting_evidence_items_carry_primary_actor_name():
    response_json = _response_json(
        key_actor_profiles=[
            {"actor_ref": "a1", "actor": "米国政権"},
            {"actor_ref": "a2", "actor": "イラン指導部"},
        ],
        hypothesis_register=[
            {"hypothesis_id": "H0", "status": "retained", "reason": None,
             "interaction_basis": {"basis_type": "DIRECTIONAL_PERCEPTION",
                                    "actor_ref": "a1", "target_actor_ref": "a2"}},
        ],
    )
    candidate = _main_candidate(content={
        "why_this_matters": "x", "origin": "main_scenario",
        "raw": {
            "description": "d", "confidence": "中", "derived_from_hypothesis_id": "H0",
            "evidence_relevance": [{"evidence_id": "ev-1", "claim_relevance": "direct"}],
        },
        "key_actors": ["米国政権", "イラン指導部"],
    })
    result = presentation_service.build_interpretations(candidates=[candidate], response_json=response_json)
    assert result[0]["supporting_evidence"][0]["actor"] == "米国政権"


def test_supporting_evidence_actor_is_none_without_linked_hypothesis():
    result = presentation_service.build_interpretations(
        candidates=[_alt_candidate()], response_json=_response_json())
    # _alt_candidateはderived_from_hypothesis_id=Noneのためlinked_hypothesisが無い
    for item in result[0]["supporting_evidence"]:
        assert item["actor"] is None


# ---- build_business_impact（既存response.business_impact、新規LLM無し） -----------

def test_build_business_impact_returns_none_when_absent():
    assert presentation_service.build_business_impact(response_json=_response_json()) is None


def test_build_business_impact_splits_social_and_industry():
    response_json = _response_json(business_impact={
        "direct": "エネルギー商社の決済リスク増加", "indirect": "保険料率の上昇",
        "environment_change": "対露制裁を巡る同盟国間の足並みの変化", "unrelated_note": None,
    })
    result = presentation_service.build_business_impact(response_json=response_json)
    assert result["social_environment"]["value"] == "対露制裁を巡る同盟国間の足並みの変化"
    assert "エネルギー商社の決済リスク増加" in result["industry_business"]["value"]
    assert "保険料率の上昇" in result["industry_business"]["value"]


def test_build_business_impact_returns_none_when_only_unrelated_note():
    response_json = _response_json(business_impact={
        "direct": None, "indirect": None, "environment_change": None,
        "unrelated_note": "この局面は自社事業と関連性が薄い",
    })
    assert presentation_service.build_business_impact(response_json=response_json) is None


# ---- enrich_evidence_dates（実DB参照、FakeSupabaseClientで検証） -------------------

def test_enrich_evidence_dates_resolves_via_geo_evidence_and_geo_documents():
    client = FakeSupabaseClient(tables={
        "geo_evidence": [{"evidence_id": "ev-1", "document_id": "doc-1"}],
        "geo_documents": [{"document_id": "doc-1", "published_at": "2026-01-05T00:00:00+00:00"}],
    })
    presentation = {
        "interpretations": [{"supporting_evidence": [{"evidence_id": "ev-1"}], "weakening_evidence": []}],
        "reading_exposure": [],
    }
    presentation_service.enrich_evidence_dates(client, presentation)
    assert presentation["interpretations"][0]["supporting_evidence"][0]["date"] == "2026-01-05T00:00:00+00:00"


def test_enrich_evidence_dates_leaves_blank_when_unresolvable():
    client = FakeSupabaseClient(tables={"geo_evidence": [], "geo_documents": []})
    presentation = {
        "interpretations": [{"supporting_evidence": [{"evidence_id": "ev-unknown"}], "weakening_evidence": []}],
        "reading_exposure": [],
    }
    presentation_service.enrich_evidence_dates(client, presentation)
    assert presentation["interpretations"][0]["supporting_evidence"][0]["date"] is None


def test_enrich_evidence_dates_noop_when_no_evidence_ids():
    client = FakeSupabaseClient(tables={})
    presentation = {"interpretations": [], "reading_exposure": []}
    presentation_service.enrich_evidence_dates(client, presentation)  # 例外にならないことのみ確認
