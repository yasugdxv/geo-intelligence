"""地政学インテリジェンス基盤: 地政学専門家AIのQuality Guard（指示書14.6章）。

最低限、以下を検査する:
1. 重要主張にclaim_typeがあるか
2. Fact/Official Claim/ObservationにEvidence(source_ref)があるか
3. 主シナリオに選択理由（支持根拠）があるか
4. 最も強い反論があるか
5. 撤回条件があるか
6. 不明な主体を創作していないか（Source Pack本文・Case Context本文中に言及が無い主体名が
   無いか、簡易チェック）
7. 自社影響までの因果経路があるか（direct/indirect/environment_changeのいずれか、
   または関連性が無い旨が明記されているか）
8. 関連性が弱い場合に無理に重大化していないか
9. Prompt/Knowledge/Model/Source Pack版が保存されているか

v1.2(Session2)で以下を追加する（いずれもmode指定時のみ実質必須化。情報不足時に
Key Actor/Audienceを無理に捏造させないよう、条件分岐で合格を許容する）:
10. Evidence IDを捏造していないか（known_ids未指定時はスキップ）
11. モード別のKey Stakeholder-first必須項目（theme_design/case_analysis/
    research_design/draft_review・outline_review/executive_brief）

Quality Guard自体は承認しない（「Quality Guard自身の評価だけで重大回答を承認しない」）。
あくまでhuman reviewのための判定材料を提供するのみで、review_statusは別途human-review待ち
のままにする。v1.2必須項目が欠落した場合の再生成判断はgeo_expert_engine.run_expert_request
側の責務とする（本モジュールはあくまで判定のみ、再生成は行わない）。
"""

import re

EVIDENCE_REQUIRED_CLAIM_TYPES = {"fact", "official_claim", "observation"}

# v1.2必須項目チェックの対象モード
_KEY_STAKEHOLDER_MODES = {"theme_design", "case_analysis"}
_ROOT_PREMISE_MODES = {"draft_review", "outline_review"}

_ROOT_PREMISE_KEYS = (
    "key_stakeholder_validity", "persistent_preference_grounding",
    "objective_hierarchy_separation", "preference_action_gap_analysis",
    "observed_action_not_treated_as_ultimate_objective",
)


def _check(name: str, passed: bool, detail: str = "") -> dict:
    return {"name": name, "passed": bool(passed), "detail": detail}


def _collect_evidence_ids(response: dict) -> set:
    """v1.2で追加したEvidence ID系フィールドから、レスポンス中で使われている全IDを集める。
    persistent_preference_evidence[]はDB外の過去事実を根拠にすることが多く、
    claim_id/evidence_idがnullでもevent_or_behavior+sourceがあれば許容する（3章）ため、
    ここではnullでない値のみ集める（nullは捏造チェックの対象外）。"""
    ids = set()

    def add_all(values):
        for v in (values or []):
            if v:
                ids.add(v)

    # Phase 6C-0: claims[].source_refはfact_claims_have_source_ref_checkで非null
    # 判定のみ行われ、捏造検出（evidence_ids_not_fabricated）の対象に一度も含まれて
    # いなかった（監査で発見）。Fact/Official Claim/Observationという最も厳格な根拠付け
    # が期待される主張種別が対象なので、ここに追加する。
    for c in response.get("claims") or []:
        if c.get("source_ref"):
            ids.add(c["source_ref"])

    for kap in response.get("key_actor_profiles") or []:
        add_all(kap.get("evidence_ids"))
        for ev in kap.get("persistent_preference_evidence") or []:
            if ev.get("claim_id"):
                ids.add(ev["claim_id"])
            if ev.get("evidence_id"):
                ids.add(ev["evidence_id"])
        add_all((kap.get("action_substance") or {}).get("evidence_ids"))

    add_all((response.get("key_stakeholder_assessment") or {}).get("evidence_ids"))

    for aa in response.get("audience_analysis") or []:
        add_all((aa.get("action_substance") or {}).get("evidence_ids"))

    rpc = response.get("root_premise_check") or {}
    for key in _ROOT_PREMISE_KEYS:
        add_all((rpc.get(key) or {}).get("evidence_ids"))

    for rf in response.get("review_findings") or []:
        add_all(rf.get("evidence_ids"))

    for saa in response.get("situation_actor_assessment") or []:
        add_all(saa.get("evidence_ids"))
        add_all(saa.get("media_salience_basis"))

    media_context = response.get("media_source_context") or {}
    for mec in media_context.get("media_editorial_context") or []:
        for obs in mec.get("observed") or []:
            add_all(obs.get("evidence_ids"))
        for hyp in mec.get("hypotheses") or []:
            add_all(hyp.get("supporting_evidence_ids"))
            add_all(hyp.get("contradicting_evidence_ids"))
        for oaa in mec.get("omitted_actor_analysis") or []:
            add_all(oaa.get("supporting_evidence_ids"))
    for nd in media_context.get("narrative_deviation") or []:
        add_all(nd.get("evidence_ids"))

    for iq in response.get("investigation_questions") or []:
        add_all(iq.get("answer_evidence_ids"))

    # Phase R1 Chunk2: evidence_relevance[].evidence_idも捏造検出の対象に含める。
    # Phase R1 Chunk4 P0 carry-over: key_point_assessment.supporting_evidence_ids
    # （トップレベル＋candidate_key_points[]）は元々evidenceIdList（strict ID配列、
    # main_scenario.supporting_evidenceのような自由記述混在ではない）にもかかわらず
    # 捏造検出の対象外だった（実E2Eでinstitution_BIS_mandate等の架空IDが素通りする
    # ことを確認済み）。ここで対象に含める。
    kpa = response.get("key_point_assessment") or {}
    add_all(kpa.get("supporting_evidence_ids"))
    for cand in kpa.get("candidate_key_points") or []:
        add_all(cand.get("supporting_evidence_ids"))
    for er in kpa.get("evidence_relevance") or []:
        if er.get("evidence_id"):
            ids.add(er["evidence_id"])
    main = response.get("main_scenario") or {}
    for er in main.get("evidence_relevance") or []:
        if er.get("evidence_id"):
            ids.add(er["evidence_id"])

    return ids


def _clean_evidence_id_array(container: dict, key: str, known_ids: set, removed: list, path: str) -> None:
    values = container.get(key)
    if not values:
        return
    kept = []
    for v in values:
        if v and v not in known_ids:
            removed.append(f"{path}.{key}={v}")
        else:
            kept.append(v)
    container[key] = kept


def _clean_scalar_id(container: dict, key: str, known_ids: set, removed: list, path: str) -> None:
    v = container.get(key)
    if v and v not in known_ids:
        removed.append(f"{path}.{key}={v}")
        container[key] = None


def _clean_evidence_ref_array(container: dict, key: str, known_ids: set, removed: list, path: str) -> None:
    """Phase R1 Chunk2: evidence_relevance[]のような{evidence_id, ...}オブジェクト配列版。
    _clean_evidence_id_array（スカラーID配列専用）とは別に、evidence_idキーだけを
    known_idsと照合して除去する。"""
    values = container.get(key)
    if not values:
        return
    kept = []
    for v in values:
        eid = v.get("evidence_id") if isinstance(v, dict) else None
        if eid and eid not in known_ids:
            removed.append(f"{path}.{key}.evidence_id={eid}")
        else:
            kept.append(v)
    container[key] = kept


_FULL_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def _clean_uuid_shaped_legacy_array(container: dict, key: str, known_ids: set, removed: list, path: str) -> None:
    """Post-Freeze Integrity Audit Finding(Phase R3再生成時発見、2026-09-22)対応:
    main_scenario.supporting_evidence/weakening_evidenceは自由記述混在のため
    scrub_fabricated_evidence_idsの走査対象から除外されてきたが、実データ監査で
    このfieldに捏造UUID（実在する2文書のprefix/suffixを組み合わせたもの）が
    混入したまま新旧両responseに残存していたことが判明した（invalid provenance=0
    というRelease Gate判定を覆す発見）。要素が完全にUUID形状（_FULL_UUID_PATTERN）の
    場合のみknown_idsと照合し、自由記述文（UUID形状でない要素）は対象外のまま
    従来方針を維持する——self-reportされた自由記述の意味内容を書き換えないため。"""
    values = container.get(key)
    if not values:
        return
    kept = []
    for v in values:
        if isinstance(v, str) and _FULL_UUID_PATTERN.match(v) and v not in known_ids:
            removed.append(f"{path}.{key}={v}")
        else:
            kept.append(v)
    container[key] = kept


def scrub_fabricated_evidence_ids(response: dict, known_ids: set) -> list:
    """段階実行(Staged Execution) Phase A: known_idsに存在しないEvidence/Claim IDを
    レスポンスから検出し、その場でnull（配列なら該当要素を除去）に書き換える
    （修復のためにLLMを再度呼び出さない。responseを直接変更する）。
    戻り値: 除去した"field_path=id"の一覧（可視化のためquality_evaluationへ記録する）。
    _collect_evidence_idsと同じフィールドを走査するが、収集ではなく除去を行う点が異なる。"""
    removed = []
    for i, kap in enumerate(response.get("key_actor_profiles") or []):
        path = f"key_actor_profiles[{i}]"
        _clean_evidence_id_array(kap, "evidence_ids", known_ids, removed, path)
        for j, ev in enumerate(kap.get("persistent_preference_evidence") or []):
            ev_path = f"{path}.persistent_preference_evidence[{j}]"
            _clean_scalar_id(ev, "claim_id", known_ids, removed, ev_path)
            _clean_scalar_id(ev, "evidence_id", known_ids, removed, ev_path)
        action_substance = kap.get("action_substance")
        if action_substance:
            _clean_evidence_id_array(action_substance, "evidence_ids", known_ids, removed, f"{path}.action_substance")

    ksa = response.get("key_stakeholder_assessment")
    if ksa:
        _clean_evidence_id_array(ksa, "evidence_ids", known_ids, removed, "key_stakeholder_assessment")

    for i, aa in enumerate(response.get("audience_analysis") or []):
        action_substance = aa.get("action_substance")
        if action_substance:
            _clean_evidence_id_array(action_substance, "evidence_ids", known_ids, removed,
                                      f"audience_analysis[{i}].action_substance")

    rpc = response.get("root_premise_check") or {}
    for key in _ROOT_PREMISE_KEYS:
        item = rpc.get(key)
        if item:
            _clean_evidence_id_array(item, "evidence_ids", known_ids, removed, f"root_premise_check.{key}")

    for i, rf in enumerate(response.get("review_findings") or []):
        _clean_evidence_id_array(rf, "evidence_ids", known_ids, removed, f"review_findings[{i}]")

    for i, saa in enumerate(response.get("situation_actor_assessment") or []):
        path = f"situation_actor_assessment[{i}]"
        _clean_evidence_id_array(saa, "evidence_ids", known_ids, removed, path)
        _clean_evidence_id_array(saa, "media_salience_basis", known_ids, removed, path)

    # Tier1(Eguchi Expert AI Final v1.0対応、2026-09-06修正): directional_perceptions[]の
    # evidence_idsが他フィールドと同じ捏造スクラブの走査対象から漏れていた（機能試験で発見、
    # 実文書2件のIDを組み合わせた捏造IDが無検出のまま最終出力に残っていた）。他のEvidence ID
    # フィールドと同じ決定的処理（observer/target actor_ref等は削除せず、evidence_idsのみ
    # スクラブする）へ揃える。
    for i, dp in enumerate(response.get("directional_perceptions") or []):
        _clean_evidence_id_array(dp, "evidence_ids", known_ids, removed, f"directional_perceptions[{i}]")

    media_context = response.get("media_source_context") or {}
    for i, mec in enumerate(media_context.get("media_editorial_context") or []):
        path = f"media_source_context.media_editorial_context[{i}]"
        for j, obs in enumerate(mec.get("observed") or []):
            _clean_evidence_id_array(obs, "evidence_ids", known_ids, removed, f"{path}.observed[{j}]")
        for j, hyp in enumerate(mec.get("hypotheses") or []):
            hyp_path = f"{path}.hypotheses[{j}]"
            _clean_evidence_id_array(hyp, "supporting_evidence_ids", known_ids, removed, hyp_path)
            _clean_evidence_id_array(hyp, "contradicting_evidence_ids", known_ids, removed, hyp_path)
        for j, oaa in enumerate(mec.get("omitted_actor_analysis") or []):
            _clean_evidence_id_array(oaa, "supporting_evidence_ids", known_ids, removed,
                                      f"{path}.omitted_actor_analysis[{j}]")
    for i, nd in enumerate(media_context.get("narrative_deviation") or []):
        _clean_evidence_id_array(nd, "evidence_ids", known_ids, removed,
                                  f"media_source_context.narrative_deviation[{i}]")

    for i, iq in enumerate(response.get("investigation_questions") or []):
        _clean_evidence_id_array(iq, "answer_evidence_ids", known_ids, removed, f"investigation_questions[{i}]")

    kpa = response.get("key_point_assessment")
    if kpa:
        # Phase R1 Chunk4 P0 carry-over: supporting_evidence_ids（トップレベル＋
        # candidate_key_points[]）はevidenceIdList（strict ID配列）であるにもかかわらず
        # 従来スクラブ対象外だった。ここで対象に含める（main_scenario.supporting_evidence
        # も自由記述混在のため従来対象外だったが、UUID形状要素のみを対象にする形で
        # 2026-09-22に対象へ含めた——下記main_scenario節参照）。
        _clean_evidence_id_array(kpa, "supporting_evidence_ids", known_ids, removed, "key_point_assessment")
        for i, cand in enumerate(kpa.get("candidate_key_points") or []):
            _clean_evidence_id_array(cand, "supporting_evidence_ids", known_ids, removed,
                                      f"key_point_assessment.candidate_key_points[{i}]")
        _clean_evidence_ref_array(kpa, "evidence_relevance", known_ids, removed, "key_point_assessment")
    main = response.get("main_scenario")
    if main:
        _clean_evidence_ref_array(main, "evidence_relevance", known_ids, removed, "main_scenario")
        # Phase 10-B(Weakening/Contradicting Evidence Structured Binding、ユーザー指示):
        # evidence_relevance（支持側）と対になる新設フィールド。同じ捏造スクラブを適用する。
        _clean_evidence_ref_array(main, "weakening_evidence_relevance", known_ids, removed, "main_scenario")
        # Post-Freeze Integrity Audit Finding(Phase R3再生成時発見、2026-09-22): 上記の
        # 構造化フィールドとは別に残るレガシー自由記述リスト（main_scenario.
        # supporting_evidence、string配列）も、UUID形状の要素に限り同じ捏造スクラブ
        # 対象へ含める（_clean_uuid_shaped_legacy_array参照）。weakening_information/
        # contradicting_evidenceはstring型（配列ではない）のため対象外——このFinding
        # で実際に確認された不具合はsupporting_evidenceのみ、他fieldへの拡張は
        # 実証されていない推測での変更となるため見送る。
        _clean_uuid_shaped_legacy_array(main, "supporting_evidence", known_ids, removed, "main_scenario")

    # Phase 10-B: hypothesis_register[]のsupporting_evidence_ids/contradicting_evidence_ids
    # （既存フィールド）とweakening_evidence_relevance（新設）は、これまで本関数の走査対象に
    # 含まれていなかった（Referential Integrity Guardの既存の抜け）。他のEvidence IDフィールド
    # と同じ決定的スクラブへ揃える。
    for i, h in enumerate(response.get("hypothesis_register") or []):
        path = f"hypothesis_register[{i}]"
        _clean_evidence_id_array(h, "supporting_evidence_ids", known_ids, removed, path)
        _clean_evidence_id_array(h, "contradicting_evidence_ids", known_ids, removed, path)
        _clean_evidence_ref_array(h, "weakening_evidence_relevance", known_ids, removed, path)

    return removed


def resolve_claim_source_ref_referential_integrity(response: dict, known_ids: set, *,
                                                     stage1_visible_entity_ids: set | None = None) -> list:
    """Phase 6D-2(Claim source_ref Referential Integrity、ユーザー指示): Phase 6C-0で
    検出可能になった「claims[].source_refがevidence registry(known_ids)に実在しない」
    問題（Fact/Official Claim/Observationという最も厳格な根拠付けが期待される主張種別が
    対象）を、評価側(Quality Guard)だけでなくProduction Output側でも解消する。

    手順（ユーザー指定の通り）: claim.source_ref → known_ids(実在するEvidence Registry、
    document_ids/event_ids/evidence_ids＋case_context由来)照合 → validならretain →
    invalidならunresolved/null。

    禁止事項（ユーザー指定の通り、いずれも遵守）:
    - 推測によるEvidence ID生成はしない（rebindは行わず、常にnull化のみ）。
    - entity_id等の別namespace IDをsource_refとして採用しない——known_idsにはそもそも
      entity_idを含めていない（既存のknown_ids算出方法をそのまま踏襲）ため、entity_idは
      構造的に「valid」判定され得ない。stage1_visible_entity_ids指定時は、unresolved_
      reasonで「別namespaceのIDだった」ことを診断情報として明示する（採用はしない、
      あくまで原因の可視化）。
    - invalid refを削除しただけでgrounded扱いにしない——claim自体のgrounded状態を
      勝手に格上げすることはしない（本関数はsource_refのnull化のみ行い、claim_type等
      他フィールドには触れない。根拠なきclaimとして残る状態は、既存の
      fact_claims_have_source_ref Guardが評価する）。

    決定的な一意rebind（ユーザー指定の"deterministicで一意にresolve可能ならrebind"）は、
    今回は実装していない——claims[]には、Entity Rebind（6A-4）における「名前」に相当する、
    IDの代替候補を安全に一意特定できる材料（内容ベースの照合手段）が現状存在しないため。
    無理な推測によるrebindは禁止事項に反するので、確実な根拠がない限りunresolvedへ倒す。

    戻り値: 元のsource_refが非nullだが無効だったclaimのみを対象とした解決結果のリスト
    （有効だったclaims、source_ref自体が元からnullだったclaimsは対象外——6A-4の
    entity_reference_resolutionsと同じ「異常のみ記録する」方針に揃える）。各要素は
    {claim_text, original_ref, validity, resolution_method, resolved_ref,
    unresolved_reason}。"""
    resolutions = []
    for claim in response.get("claims") or []:
        original_ref = claim.get("source_ref")
        if not original_ref:
            continue
        if original_ref in known_ids:
            continue
        claim["source_ref"] = None
        if stage1_visible_entity_ids is not None and original_ref in stage1_visible_entity_ids:
            resolution_method = "cross_namespace_id_rejected"
            unresolved_reason = ("original_refはEvidence Registry(document/event/evidence)ではなく"
                                  "Entity ID空間に属するIDでした（namespace混同、採用不可）")
        else:
            resolution_method = "not_found_in_evidence_registry"
            unresolved_reason = "known_ids(Evidence Registry)に存在しないIDです（捏造の可能性）"
        resolutions.append({
            "claim_text": claim.get("text"),
            "original_ref": original_ref,
            "validity": "invalid",
            "resolution_method": resolution_method,
            "resolved_ref": None,
            "unresolved_reason": unresolved_reason,
        })
    return resolutions


def _key_point_assessment_present_check(response: dict) -> str:
    """Session2 Reasoning Upgrade: key_point_assessment.primary_key_pointが空でないかを
    検査する。各v1.2必須項目チェックから共通で呼び出す（primary_key_point自体は
    Stage1 Schema上required文字列だが、空文字での埋め合わせを防ぐため実質チェックする）。
    問題なければ空文字、問題があれば理由文字列を返す。"""
    kpa = response.get("key_point_assessment") or {}
    if not (kpa.get("primary_key_point") or "").strip():
        return "key_point_assessment.primary_key_pointが空です"
    return ""


def _key_stakeholder_v12_check(response: dict) -> dict:
    name = "key_stakeholder_v12_fields_present"
    key_point_issue = _key_point_assessment_present_check(response)
    if key_point_issue:
        return _check(name, False, key_point_issue)
    ksa = response.get("key_stakeholder_assessment")
    if not ksa or not (ksa.get("rationale") or "").strip():
        return _check(name, False, "key_stakeholder_assessment（rationale含む）がありません")
    main_scenario = response.get("main_scenario") or {}
    if not (main_scenario.get("selection_rationale") or "").strip():
        return _check(name, False, "main_scenario.selection_rationaleが空です")
    if ksa.get("determination") == "unclear":
        if not (response.get("identified_knowledge_gaps") or []):
            return _check(name, False,
                           "determination=unclearの場合はidentified_knowledge_gapsが1件以上必要です")
        return _check(name, True)
    if not (response.get("key_actor_profiles") or []):
        return _check(name, False,
                       f"determination={ksa.get('determination')}の場合はkey_actor_profilesが1件以上必要です")
    return _check(name, True)


def _research_design_v12_check(response: dict) -> dict:
    name = "research_design_v12_fields_present"
    key_point_issue = _key_point_assessment_present_check(response)
    if key_point_issue:
        return _check(name, False, key_point_issue)
    ksa = response.get("key_stakeholder_assessment")
    if not ksa or not (ksa.get("rationale") or "").strip():
        return _check(name, False, "key_stakeholder_assessment（rationale含む）がありません")
    if not (response.get("additional_research") or []):
        return _check(name, False, "additional_researchが空です")
    if not (response.get("audience_analysis") or []):
        if not (response.get("identified_knowledge_gaps") or []):
            return _check(name, False,
                           "audience_analysisが空の場合はidentified_knowledge_gapsに理由を記録してください")
    return _check(name, True)


def _draft_review_root_premise_check(response: dict) -> dict:
    name = "draft_review_root_premise_check"
    rpc = response.get("root_premise_check")
    if not rpc:
        return _check(name, False, "root_premise_checkがありません")
    missing = [k for k in _ROOT_PREMISE_KEYS if not (rpc.get(k) or {}).get("result")]
    if missing:
        return _check(name, False, f"root_premise_checkの項目が未評価です: {', '.join(missing)}")
    if not rpc.get("overall_result"):
        return _check(name, False, "root_premise_check.overall_resultがありません")
    if rpc.get("overall_result") == "fail":
        findings = response.get("review_findings") or []
        if not any(f.get("severity") == "Critical" for f in findings):
            return _check(name, False,
                           "overall_result=failの場合はseverity=Criticalのreview_findingsが1件以上必要です")
    return _check(name, True)


def _executive_brief_v12_check(response: dict) -> dict:
    name = "executive_brief_v12_fields_present"
    ksa = response.get("key_stakeholder_assessment")
    if not ksa or not (ksa.get("rationale") or "").strip():
        return _check(name, False, "key_stakeholder_assessment.rationaleがありません")
    summary = response.get("executive_summary") or {}
    if not any((summary.get(k) or "").strip() for k in
               ("what_changed", "why_it_matters", "what_to_watch_decide")):
        return _check(name, False, "executive_summaryが空です")
    if not (response.get("business_impact") or {}):
        return _check(name, False, "business_impactがありません")
    return _check(name, True)


def _situation_actor_assessment_grounded_check(response: dict) -> dict:
    """Session2 Reasoning Upgrade（Phase2）: situation_actor_assessment[].actor_refが
    key_actor_profiles[].actor_refの集合のいずれかと一致するか検証する（非repair対象）。"""
    name = "situation_actor_assessment_grounded"
    rows = response.get("situation_actor_assessment") or []
    if not rows:
        return _check(name, True)
    known_actor_refs = {kap.get("actor_ref") for kap in (response.get("key_actor_profiles") or [])
                        if kap.get("actor_ref")}
    unknown_refs = sorted({r.get("actor_ref") for r in rows
                          if r.get("actor_ref") and r.get("actor_ref") not in known_actor_refs})
    if unknown_refs:
        return _check(name, False,
                       f"situation_actor_assessmentにkey_actor_profilesに存在しないactor_refがあります"
                       f"（創作の可能性、要人手確認）: {', '.join(unknown_refs)}")
    return _check(name, True)


def _situation_actor_assessment_salience_confidence_consistency_check(response: dict) -> dict:
    """Session2 Reasoning Upgrade（Phase2）: salience=unknownかつconfidence=高という
    組合せを矛盾として検出する（判断材料が無いと明示していながら確信度だけ高い、
    という不整合の防止。非repair対象）。"""
    name = "situation_actor_assessment_salience_confidence_consistency"
    for i, row in enumerate(response.get("situation_actor_assessment") or []):
        if row.get("salience") == "unknown" and row.get("confidence") == "高":
            return _check(name, False,
                           f"situation_actor_assessment[{i}]: salience=unknownかつconfidence=高は矛盾しています")
    return _check(name, True)


def _situation_actor_assessment_evidence_grounded_check(response: dict) -> dict:
    """Session2 Reasoning Upgrade（Phase2、任意・推奨）: confidence=高で推論性の高い
    フィールド（estimated_real_preference/genuine_or_tactical/current_red_line/
    instrumental_value）について実質的な判断を行っている場合、evidence_idsが1件以上
    あることを検証する。Media Hypothesisには厳格なEvidence Groundingを課しながら、
    Actorの「本音」だけがEvidenceなしで高Confidence化される非対称を防ぐ（非repair対象）。
    unclear/nullで判断を留保している行は対象外（判断放棄自体は許容する）。"""
    name = "situation_actor_assessment_evidence_grounded"
    for i, row in enumerate(response.get("situation_actor_assessment") or []):
        if row.get("confidence") != "高":
            continue
        makes_inferential_judgment = (
            bool((row.get("estimated_real_preference") or "").strip()) or
            row.get("genuine_or_tactical") not in (None, "unclear") or
            bool((row.get("current_red_line") or "").strip()) or
            row.get("instrumental_value") not in (None, "unclear")
        )
        if makes_inferential_judgment and not (row.get("evidence_ids") or []):
            return _check(name, False,
                           f"situation_actor_assessment[{i}]: confidence=高で推論的判断"
                           "（estimated_real_preference/genuine_or_tactical/current_red_line/"
                           "instrumental_value）を行っていますがevidence_idsが空です")
    return _check(name, True)


def _directional_perceptions_grounded_check(response: dict) -> dict:
    """Tier1(Eguchi Expert AI Final v1.0対応): directional_perceptions[].observer_actor_ref/
    target_actor_refが、key_actor_profiles[].actor_refの集合のいずれかと一致するか検証する
    （_situation_actor_assessment_grounded_checkと同じ設計、非repair対象）。"""
    name = "directional_perceptions_grounded"
    rows = response.get("directional_perceptions") or []
    if not rows:
        return _check(name, True)
    known_actor_refs = {kap.get("actor_ref") for kap in (response.get("key_actor_profiles") or [])
                        if kap.get("actor_ref")}
    unknown_refs = sorted({
        ref for r in rows
        for ref in (r.get("observer_actor_ref"), r.get("target_actor_ref"))
        if ref and ref not in known_actor_refs
    })
    if unknown_refs:
        return _check(name, False,
                       "directional_perceptionsにkey_actor_profilesに存在しないactor_refが"
                       f"あります（創作の可能性、要人手確認）: {', '.join(unknown_refs)}")
    return _check(name, True)


def _directional_perception_not_self_referential_check(response: dict) -> dict:
    """Tier1: DirectionalPerceptionはAがBを認識するという他者認識のみを対象とする。
    observer_actor_ref==target_actor_refの自己言及行は、この配列の対象外
    （HumanModel/core_profile側で扱うべき自己認識との混同を検出する、非repair対象）。"""
    name = "directional_perception_not_self_referential"
    rows = response.get("directional_perceptions") or []
    self_referential = [i for i, r in enumerate(rows)
                        if r.get("observer_actor_ref") and r.get("observer_actor_ref") == r.get("target_actor_ref")]
    if self_referential:
        return _check(name, False,
                       f"directional_perceptions{self_referential}: observer_actor_ref=="
                       "target_actor_refの自己言及行があります(HumanModel側で扱うべき内容の可能性)")
    return _check(name, True)


def _event_role_intended_target_grounded_check(response: dict) -> dict:
    """Phase D-1(Holistic Reasoning Architecture Review): key_actor_profiles[].event_role.
    intended_target_actor_refが、key_actor_profiles[].actor_refの集合のいずれかと一致するかを
    機械的に検証する（接続性のみ。責務分離の原則により、意味評価（この一手が実際に相手を動かす
    狙い通りかどうか等）はここでは行わない。非repair対象）。"""
    name = "event_role_intended_target_grounded"
    profiles = response.get("key_actor_profiles") or []
    known_actor_refs = {kap.get("actor_ref") for kap in profiles if kap.get("actor_ref")}
    unknown_refs = sorted({
        ref for kap in profiles
        for ref in [(kap.get("event_role") or {}).get("intended_target_actor_ref")]
        if ref and ref not in known_actor_refs
    })
    if unknown_refs:
        return _check(name, False,
                       "event_role.intended_target_actor_refにkey_actor_profilesに存在しない"
                       f"actor_refがあります（創作の可能性、要人手確認）: {', '.join(unknown_refs)}")
    return _check(name, True)


def _actor_analyst_expected_response_target_grounded_check(response: dict) -> dict:
    """Phase D-1: situation_actor_assessment[].target_actor_refが、key_actor_profiles[].actor_ref
    の集合のいずれかと一致するかを機械的に検証する（接続性のみ。責務分離の原則により、
    actor_expected_responseとanalyst_expected_responseの意味的な差＝認識のズレの評価は
    ここでは行わない——それはhypothesis_register側でLLMが行う。非repair対象）。"""
    name = "actor_analyst_expected_response_target_grounded"
    rows = response.get("situation_actor_assessment") or []
    known_actor_refs = {kap.get("actor_ref") for kap in (response.get("key_actor_profiles") or [])
                        if kap.get("actor_ref")}
    unknown_refs = sorted({
        ref for r in rows
        for ref in [r.get("target_actor_ref")]
        if ref and ref not in known_actor_refs
    })
    if unknown_refs:
        return _check(name, False,
                       "situation_actor_assessment.target_actor_refにkey_actor_profilesに存在"
                       f"しないactor_refがあります（創作の可能性、要人手確認）: {', '.join(unknown_refs)}")
    return _check(name, True)


def _scenario_derived_from_hypothesis_grounded_check(response: dict) -> dict:
    """Phase D-1(絶対条件4の最小限の下地): main_scenario/alternative_scenarios[]の
    derived_from_hypothesis_idが、hypothesis_register[].hypothesis_idの集合に存在するかのみを
    機械的に検証する（存在確認のみ。retainedな仮説であることまでの検証・因果順序の検証は
    D-3実施後のD-4bで行う。非repair対象）。"""
    name = "scenario_derived_from_hypothesis_grounded"
    known_ids = {h.get("hypothesis_id") for h in (response.get("hypothesis_register") or [])
                 if h.get("hypothesis_id")}
    refs = []
    main_ref = (response.get("main_scenario") or {}).get("derived_from_hypothesis_id")
    if main_ref:
        refs.append(main_ref)
    for alt in response.get("alternative_scenarios") or []:
        alt_ref = alt.get("derived_from_hypothesis_id")
        if alt_ref:
            refs.append(alt_ref)
    unknown_refs = sorted({ref for ref in refs if ref not in known_ids})
    if unknown_refs:
        return _check(name, False,
                       "derived_from_hypothesis_idにhypothesis_registerに存在しない"
                       f"hypothesis_idがあります（創作の可能性、要人手確認）: {', '.join(unknown_refs)}")
    return _check(name, True)


def _human_interaction_chain_completeness_check(response: dict) -> dict:
    """Phase D-4b(Holistic Reasoning Architecture Review): directional_perceptions
    （AのBへの認識）→event_role.intended_target_actor_ref（その認識に基づくAの一手、誰宛か）
    →situation_actor_assessment（actor_expected_response/analyst_expected_response、
    target_actor_refで対象を共有）という鎖が、共有キーで実際にどこまで繋がっているかを
    機械的に集計する（責務分離の原則により意味評価は行わない——認識の内容が正しいか・
    重要かはhypothesis_register.reason側でLLMが判断する）。Evidence不足やstructure-first
    な局面（そもそもHuman Interactionを扱っていない）で無理な補完を強制しないため、
    COMPLETE/PARTIAL_EVIDENCE_GAP/NOT_APPLICABLE/INVALID_LINKの4状態をdetailへ明示する。
    INVALID_LINK（自己参照）のみpassed=Falseとするが、非repair対象（Repairで無理に
    書き換えさせない、要人手確認）。"""
    name = "human_interaction_chain_completeness"
    profiles = response.get("key_actor_profiles") or []
    saa_rows = response.get("situation_actor_assessment") or []
    dp_rows = response.get("directional_perceptions") or []

    invalid = []
    event_targets = {}
    for kap in profiles:
        actor_ref = kap.get("actor_ref")
        target = (kap.get("event_role") or {}).get("intended_target_actor_ref")
        if not target:
            continue
        if target == actor_ref:
            invalid.append(f"key_actor_profiles[actor_ref={actor_ref}].event_role."
                            "intended_target_actor_refが自己参照")
            continue
        event_targets[actor_ref] = target

    expected_pairs = {}
    for row in saa_rows:
        actor_ref = row.get("actor_ref")
        target = row.get("target_actor_ref")
        if not target:
            continue
        if target == actor_ref:
            invalid.append(f"situation_actor_assessment[actor_ref={actor_ref}].target_actor_ref"
                            "が自己参照")
            continue
        if row.get("actor_expected_response") and row.get("analyst_expected_response"):
            expected_pairs[(actor_ref, target)] = True

    if invalid:
        return _check(name, False, "INVALID_LINK: " + "; ".join(invalid))

    dp_pairs = [(r.get("observer_actor_ref"), r.get("target_actor_ref")) for r in dp_rows
                if r.get("observer_actor_ref") and r.get("target_actor_ref")]

    if not dp_pairs and not event_targets and not expected_pairs:
        return _check(name, True,
                       "NOT_APPLICABLE: Human Interaction関連データ（directional_perceptions/"
                       "event_role/situation_actor_assessment）が存在しないため評価対象外")

    if not dp_pairs:
        return _check(name, True,
                       "PARTIAL_EVIDENCE_GAP: directional_perceptionsが存在せず、event_role/"
                       "situation_actor_assessment単独では鎖の起点（AのBへの認識）が確認できない")

    complete = sum(1 for observer, target in dp_pairs
                   if event_targets.get(observer) == target and expected_pairs.get((observer, target)))
    if complete == len(dp_pairs):
        return _check(name, True,
                       f"COMPLETE: directional_perceptions {len(dp_pairs)}件全てでevent_role."
                       "intended_target_actor_ref・actor_expected_response・analyst_expected_"
                       "responseまで共有キーで接続を確認")
    if complete > 0:
        return _check(name, True,
                       f"PARTIAL_EVIDENCE_GAP: directional_perceptions {len(dp_pairs)}件中"
                       f"{complete}件のみ鎖が最後まで閉じている")
    return _check(name, True,
                   "PARTIAL_EVIDENCE_GAP: directional_perceptionsは存在するが、event_role."
                   "intended_target_actor_ref・actor_expected_response/analyst_expected_response"
                   "との接続が1件も確認できない")


def _scenario_derived_from_retained_hypothesis_check(response: dict, *, defer_scenario: bool = False,
                                                      scenario_fallback_used: bool = False) -> dict:
    """Phase D-4b(絶対条件4の強化): defer_scenario=Trueかつ通常生成（budget fallback等では
    ない）の場合、main_scenario.derived_from_hypothesis_idはhypothesis_register[]の中で
    status=retainedな仮説を参照することを必須とする（repair対象）。defer_scenario=Falseの
    経路、およびbudget fallback使用時は明示的な例外（NOT_APPLICABLE）とし、無理な補完は
    強制しない。hypothesis_register自体が空、またはretainedな仮説が1件も無い場合
    （Evidence不足・全仮説棄却）もPARTIAL_EVIDENCE_GAPとして区別し、INVALID_LINKとはしない。"""
    name = "scenario_derived_from_retained_hypothesis"
    if not defer_scenario or scenario_fallback_used:
        return _check(name, True,
                       "NOT_APPLICABLE: defer_scenario=False、またはbudget fallback使用のため"
                       "評価対象外（明示例外）")
    hypothesis_register = response.get("hypothesis_register") or []
    if not hypothesis_register:
        return _check(name, True,
                       "PARTIAL_EVIDENCE_GAP: hypothesis_registerが空のため（Evidence不足等）"
                       "derived_from_hypothesis_idを要求しない")
    retained_ids = {h.get("hypothesis_id") for h in hypothesis_register
                    if h.get("status") == "retained" and h.get("hypothesis_id")}
    if not retained_ids:
        return _check(name, True,
                       "PARTIAL_EVIDENCE_GAP: hypothesis_registerにretainedな仮説が1件も無い"
                       "ため（全て棄却/未解決）derived_from_hypothesis_idを要求しない")
    derived_id = (response.get("main_scenario") or {}).get("derived_from_hypothesis_id")
    if derived_id and derived_id in retained_ids:
        return _check(name, True,
                       f"COMPLETE: main_scenario.derived_from_hypothesis_id={derived_id}は"
                       "retainedな仮説を正しく参照")
    if not derived_id:
        return _check(name, False,
                       "INVALID_LINK: hypothesis_registerにretainedな仮説が存在するにも関わらず、"
                       "main_scenario.derived_from_hypothesis_idが未設定です")
    return _check(name, False,
                   f"INVALID_LINK: main_scenario.derived_from_hypothesis_id={derived_id}は"
                   "hypothesis_registerのretainedな仮説を参照していません（未知のID、または"
                   "rejected/unresolvedな仮説を参照）")


def _human_interaction_gate_present_check(response: dict) -> dict:
    """Phase F(Human Interaction Analytical Gate、ユーザー指示): discretion_assessment/
    human_interaction_coverageが出力Schemaに含まれる局面（deep×theme_design/
    case_analysisのみ）では、検討したことの自己申告を必須のGateとする——nullのまま
    無言で通過することを禁止する（repair対象）。両フィールドとも応答にキー自体が
    存在しない場合（他のprofile/modeでモジュールが発火していない場合）は評価対象外
    とする（_STAGE_OPTIONAL_DEFAULTSに両フィールドを加えていないため、モジュール
    未発火時はキー自体が存在しない、という区別がここで機能する）。"""
    name = "human_interaction_gate_present"
    if "discretion_assessment" not in response and "human_interaction_coverage" not in response:
        return _check(name, True,
                       "NOT_APPLICABLE: このprofile/modeではHuman Interaction Gate自体が"
                       "発火していません")
    da = response.get("discretion_assessment") or {}
    hic = response.get("human_interaction_coverage") or {}
    issues = []
    if not da.get("discretion_mode") or not da.get("mode_rationale"):
        issues.append("discretion_assessment.discretion_mode/mode_rationaleが未申告です")
    if not hic.get("interaction_status") or not hic.get("status_rationale"):
        issues.append("human_interaction_coverage.interaction_status/status_rationaleが"
                       "未申告です")
    if issues:
        return _check(name, False, "; ".join(issues))
    return _check(name, True)


def _human_interaction_status_consistency_check(response: dict) -> dict:
    """Phase F(Human Interaction Analytical Gate、ユーザー指示): human_interaction_
    coverage.interaction_statusの自己申告が、実データと矛盾しないかを検証する（repair
    対象）。「GROUNDEDと書いただけで中身が無い」を検出する——directional_perceptions/
    situation_actor_assessment自体のgrounding（actor_refの実在性）は既存の
    _directional_perceptions_grounded_check/_situation_actor_assessment_grounded_check
    が別途担うため、ここでは自己申告と実データの整合性のみを見る。"""
    name = "human_interaction_status_consistency"
    if "human_interaction_coverage" not in response:
        return _check(name, True, "NOT_APPLICABLE: このprofile/modeでは評価対象外です")
    status = (response.get("human_interaction_coverage") or {}).get("interaction_status")
    if not status:
        return _check(name, True,
                       "NOT_APPLICABLE: interaction_status自体は_human_interaction_gate_"
                       "present側で検証済みのため、ここでは重複評価しない")
    dp_present = bool(response.get("directional_perceptions"))
    saa_present = bool(response.get("situation_actor_assessment"))
    gaps_present = bool(response.get("identified_knowledge_gaps"))
    discretion_mode = (response.get("discretion_assessment") or {}).get("discretion_mode")

    if status == "GROUNDED":
        if not (dp_present or saa_present):
            return _check(name, False,
                           "GROUNDEDと自己申告していますが、directional_perceptions/"
                           "situation_actor_assessmentが両方とも空です")
        return _check(name, True)
    if status == "PARTIAL_EVIDENCE":
        if not (dp_present or saa_present):
            return _check(name, False,
                           "PARTIAL_EVIDENCEと自己申告していますが、directional_perceptions/"
                           "situation_actor_assessmentが両方とも空です")
        if not gaps_present:
            return _check(name, False,
                           "PARTIAL_EVIDENCEと自己申告していますが、identified_knowledge_gaps"
                           "に欠落分の記録がありません")
        return _check(name, True)
    if status == "INSUFFICIENT_EVIDENCE":
        if not gaps_present:
            return _check(name, False,
                           "INSUFFICIENT_EVIDENCEと自己申告していますが、identified_"
                           "knowledge_gapsに何が欠けているかの記録がありません")
        return _check(name, True)
    if status == "NOT_APPLICABLE_STRUCTURE_FIRST":
        if discretion_mode != "STRUCTURE_DOMINANT":
            return _check(name, False,
                           "NOT_APPLICABLE_STRUCTURE_FIRSTと自己申告していますが、"
                           f"discretion_assessment.discretion_mode={discretion_mode}で"
                           "STRUCTURE_DOMINANTではありません")
        return _check(name, True)
    return _check(name, True)


def _hypothesis_interaction_basis_grounded_check(response: dict) -> dict:
    """Phase F(Human Interaction Analytical Gate、ユーザー指示): hypothesis_register[].
    interaction_basisのactor_ref/target_actor_refが、対応する配列（directional_
    perceptions/situation_actor_assessment）の実在レコードと一致する組であるかを機械的に
    検証する（参照整合性のみ、repair対象）。basis_type=EVIDENCE_GAP/NOT_APPLICABLEの
    場合はgap_noteが必須（理由なき省略を防ぐ）。"""
    name = "hypothesis_interaction_basis_grounded"
    dp_pairs = {(r.get("observer_actor_ref"), r.get("target_actor_ref"))
                for r in (response.get("directional_perceptions") or [])}
    saa_pairs = {(r.get("actor_ref"), r.get("target_actor_ref"))
                 for r in (response.get("situation_actor_assessment") or [])}
    issues = []
    for h in response.get("hypothesis_register") or []:
        basis = h.get("interaction_basis")
        if not basis:
            continue
        hid = h.get("hypothesis_id") or "?"
        basis_type = basis.get("basis_type")
        pair = (basis.get("actor_ref"), basis.get("target_actor_ref"))
        if basis_type == "DIRECTIONAL_PERCEPTION" and pair not in dp_pairs:
            issues.append(f"{hid}: interaction_basis(DIRECTIONAL_PERCEPTION)の{pair}が"
                           "directional_perceptionsに存在しません（創作の可能性、要人手確認）")
        elif basis_type == "SITUATION_ACTOR_ASSESSMENT" and pair not in saa_pairs:
            issues.append(f"{hid}: interaction_basis(SITUATION_ACTOR_ASSESSMENT)の{pair}が"
                           "situation_actor_assessmentに存在しません（創作の可能性、要人手確認）")
        elif basis_type in ("EVIDENCE_GAP", "NOT_APPLICABLE") and not basis.get("gap_note"):
            issues.append(f"{hid}: interaction_basis.basis_type={basis_type}なのに"
                           "gap_noteが空です")
    if issues:
        return _check(name, False, "; ".join(issues))
    return _check(name, True)


_INTERACTION_DEPENDENT_HYPOTHESIS_TYPES = frozenset({
    "H1_instrumental_indirect", "H2_counterparty_audience_shift", "H4_frame_breaking",
})


def _hypothesis_interaction_basis_missing_check(response: dict) -> dict:
    """Phase F(Human Interaction Analytical Gate、ユーザー指示): hypothesis_registerが
    directional_perceptions/situation_actor_assessmentを経由せず、event_role等から直接
    生成されている疑いを検出する（advisory・非repair対象——Repairにすると、Guardを通す
    ためだけにモデルがInteractionを捏造するリスクがあるため、実データで発火頻度を見て
    からRepair昇格を検討する）。H1/H2/H4はhypothesis_typeで判定するが、H3は
    hypothesis_typeで一律判定せず、reason/description中にkey_actor_profilesの実在
    actor名への言及があるか（＝特定の人物間相互作用を主張している疑いがあるか）という
    ヒューリスティックで判定する（意味理解の代用であり完全ではない、advisoryに留める理由）。"""
    name = "hypothesis_interaction_basis_missing"
    status = (response.get("human_interaction_coverage") or {}).get("interaction_status")
    if status not in ("GROUNDED", "PARTIAL_EVIDENCE"):
        return _check(name, True,
                       "NOT_APPLICABLE: human_interaction_coverage.interaction_statusが"
                       "GROUNDED/PARTIAL_EVIDENCEではないため評価対象外です")
    actor_names = {kap.get("actor") for kap in (response.get("key_actor_profiles") or [])
                   if kap.get("actor")}
    suspicious = []
    for h in response.get("hypothesis_register") or []:
        htype = h.get("hypothesis_type")
        basis = h.get("interaction_basis") or {}
        basis_type = basis.get("basis_type")
        hid = h.get("hypothesis_id") or "?"
        if htype in _INTERACTION_DEPENDENT_HYPOTHESIS_TYPES:
            if basis_type in (None, "EVENT_ROLE", "NOT_APPLICABLE"):
                suspicious.append(f"{hid}({htype}): interaction_basisがevent_role止まりか"
                                   "未設定で、Human Interactionを迂回している疑いがあります")
        elif htype == "H3_constraint_domestic_driver" and basis_type == "NOT_APPLICABLE":
            text = f"{h.get('description') or ''} {h.get('reason') or ''}"
            mentioned = [name for name in actor_names if name and name in text]
            if mentioned:
                suspicious.append(f"{hid}(H3): NOT_APPLICABLEとしていますが、reason/"
                                   f"descriptionに特定の主体({', '.join(mentioned)})への"
                                   "言及があり、Human Interactionを主張している疑いが"
                                   "あります")
    if suspicious:
        return _check(name, False, "; ".join(suspicious))
    return _check(name, True)


_CONTROL_TYPES_REQUIRING_DECISION_MAKER = frozenset({"actor_controlled", "distributed", "mixed"})


def _control_point_decision_maker_resolution_check(response: dict) -> dict:
    """Phase D-4b(Holistic Reasoning Architecture Review): 既存の_key_point_controllers_
    covered_check（prose text-matching、advisoryのまま維持・後方互換）を置き換えるのでは
    なく補完する形で、controlling_entity_idsとkey_actor_profiles[].entity_idという
    共通の正典entity_id空間同士を比較する（Canonical Entity Resolution）。control_type
    （actor_controlled/distributed/mixed）が人間の裁量を含意する場合にのみ発火し、
    structural/institutionally_constrained/unknownの場合は評価対象外とする
    （Test Dの構造優位ケースで誤検出しないため）。COMPLETE/PARTIAL_EVIDENCE_GAP/
    NOT_APPLICABLE/INVALID_LINKを区別する（非repair対象、無理な補完は強制しない）。"""
    name = "control_point_decision_maker_resolution"
    kpa = response.get("key_point_assessment") or {}
    control_type = kpa.get("control_type")
    controlling_entity_ids = [e for e in (kpa.get("controlling_entity_ids") or []) if e]
    if control_type not in _CONTROL_TYPES_REQUIRING_DECISION_MAKER or not controlling_entity_ids:
        return _check(name, True,
                       "NOT_APPLICABLE: control_typeが人間の裁量を含意しない、または"
                       "controlling_entity_idsが空のため評価対象外")
    profiled_entity_ids = {kap.get("entity_id") for kap in (response.get("key_actor_profiles") or [])
                           if kap.get("entity_id")}
    covered = [e for e in controlling_entity_ids if e in profiled_entity_ids]
    if len(covered) == len(controlling_entity_ids):
        return _check(name, True,
                       f"COMPLETE: controlling_entity_ids全件({len(controlling_entity_ids)}件)に"
                       "対応するkey_actor_profilesが存在")
    if covered:
        return _check(name, True,
                       f"PARTIAL_EVIDENCE_GAP: controlling_entity_ids {len(controlling_entity_ids)}件"
                       f"中{len(covered)}件のみkey_actor_profilesに対応（Key Actor深掘りの優先順位"
                       "付けによる正当な範囲の可能性）")
    return _check(name, False,
                   f"INVALID_LINK: control_type={control_type}が人間の裁量を含意するにも関わらず、"
                   f"controlling_entity_ids({', '.join(controlling_entity_ids)})に対応するkey_actor_"
                   "profilesが1件も存在しません（Key Point DiscoveryにActor Discoveryが追従して"
                   "いない可能性）")


_MEDIA_INFERENCE_LANGUAGE_MARKERS = (
    "意図的に", "意図して", "狙って", "仕掛けた", "戦略的に示唆", "示唆する意図", "リークした",
    "リークさせた", "トライアルバルーン", "信号を送る意図", "見せつける狙い",
)


def _media_observed_fields_free_of_inference_language(response: dict) -> dict:
    """Session2 Reasoning Upgrade（Phase3）: observed[]（Observable、意図を語れない事実表現）に
    断定的な意図言及語が混入していないかヒューリスティック検査する（非repair対象）。"""
    name = "media_observed_fields_free_of_inference_language"
    media_context = response.get("media_source_context") or {}
    for i, mec in enumerate(media_context.get("media_editorial_context") or []):
        for j, obs in enumerate(mec.get("observed") or []):
            text = obs.get("observation_text") or ""
            hit = next((m for m in _MEDIA_INFERENCE_LANGUAGE_MARKERS if m in text), None)
            if hit:
                return _check(name, False,
                               f"media_editorial_context[{i}].observed[{j}]に意図言及語"
                               f"「{hit}」が含まれています（Observableは事実表現に限定し、意図はhypotheses側で書く）")
    return _check(name, True)


def _media_editorial_hypotheses_not_uniformly_confirmatory(response: dict) -> dict:
    """Session2 Reasoning Upgrade（Phase3）: 全hypothesesがconfidence=高かつ
    status=supportedのみで、alternative_explanation/contradicting_evidence_idsが
    皆無という「全会一致の陰謀論的解釈」パターンを検出する（非repair対象）。"""
    name = "media_editorial_hypotheses_not_uniformly_confirmatory"
    media_context = response.get("media_source_context") or {}
    all_hypotheses = [
        hyp for mec in (media_context.get("media_editorial_context") or [])
        for hyp in (mec.get("hypotheses") or [])
    ]
    if len(all_hypotheses) < 2:
        return _check(name, True)
    uniformly_confirmatory = all(
        h.get("confidence") == "高" and h.get("status") == "supported"
        and not h.get("alternative_explanation") and not (h.get("contradicting_evidence_ids") or [])
        for h in all_hypotheses
    )
    if uniformly_confirmatory:
        return _check(name, False,
                       "media_editorial_context.hypothesesが全件confidence=高・status=supportedで"
                       "対立解釈・反証が皆無です（陰謀論的な全会一致パターンの可能性）")
    return _check(name, True)


def _media_beneficiary_not_treated_as_intent_proof(response: dict) -> dict:
    """Session2 Reasoning Upgrade（Phase3）: 「Who benefits? はintentの証明ではない」。
    question_type=who_benefitsのhypothesisが、質的な裏付け（corroboration_basis）無しに
    confidence=高・status=supportedへ格上げされていないか検査する（非repair対象）。"""
    name = "media_beneficiary_not_treated_as_intent_proof"
    media_context = response.get("media_source_context") or {}
    for i, mec in enumerate(media_context.get("media_editorial_context") or []):
        for j, hyp in enumerate(mec.get("hypotheses") or []):
            if hyp.get("question_type") != "who_benefits":
                continue
            if hyp.get("confidence") == "高" and hyp.get("status") in ("supported", "partially_supported"):
                basis = hyp.get("corroboration_basis")
                if basis in (None, "unclear", "single_source_only"):
                    return _check(name, False,
                                   f"media_editorial_context[{i}].hypotheses[{j}]: who_benefitsのみを"
                                   "根拠にconfidence=高・status=supported/partially_supportedへ"
                                   "格上げしています（受益者＝意図の証明にはならない）")
    return _check(name, True)


def _media_hypothesis_evidence_grounded_check(response: dict) -> dict:
    """Session2 Reasoning Upgrade（Phase3）: status=supported/partially_supported、または
    confidence=高の場合、supporting_evidence_idsが1件以上であることを全hypotheses共通で
    要求する（匿名ソース起源のhypothesisだけでなく一般契約として適用。非repair対象）。"""
    name = "media_hypothesis_evidence_grounded"
    media_context = response.get("media_source_context") or {}
    for i, mec in enumerate(media_context.get("media_editorial_context") or []):
        for j, hyp in enumerate(mec.get("hypotheses") or []):
            needs_evidence = hyp.get("status") in ("supported", "partially_supported") or hyp.get("confidence") == "高"
            if needs_evidence and not (hyp.get("supporting_evidence_ids") or []):
                return _check(name, False,
                               f"media_editorial_context[{i}].hypotheses[{j}]: status="
                               f"{hyp.get('status')}・confidence={hyp.get('confidence')}ですが"
                               "supporting_evidence_idsが空です")
    return _check(name, True)


def _anonymous_sourced_hypothesis_confidence_capped(response: dict) -> dict:
    """Session2 Reasoning Upgrade（Phase3）: confidence=高のhypothesisは、corroboration_basisが
    質的な裏付け種別であり、かつcorroboration_target=hypothesis_itself（出来事の発生ではなく
    意図・source/origin主張自体が裏付けられている）でない限り許容しない。evidence件数のみで
    confidenceを上げること・出来事の確認と意図の確認の混同を防ぐ（非repair対象）。"""
    name = "anonymous_sourced_hypothesis_confidence_capped"
    qualitative_basis = {
        "independent_corroboration", "official_confirmation",
        "behavioral_corroboration", "documentary_corroboration",
    }
    media_context = response.get("media_source_context") or {}
    for i, mec in enumerate(media_context.get("media_editorial_context") or []):
        for j, hyp in enumerate(mec.get("hypotheses") or []):
            if hyp.get("confidence") != "高":
                continue
            basis_ok = hyp.get("corroboration_basis") in qualitative_basis
            target_ok = hyp.get("corroboration_target") == "hypothesis_itself"
            if not (basis_ok and target_ok):
                return _check(name, False,
                               f"media_editorial_context[{i}].hypotheses[{j}]: confidence=高ですが"
                               f"corroboration_basis={hyp.get('corroboration_basis')}・"
                               f"corroboration_target={hyp.get('corroboration_target')}であり、"
                               "質的裏付け＋hypothesis_itself裏付けの条件を満たしていません")
    return _check(name, True)


def _media_omitted_actor_analysis_grounded(response: dict, *, stage1_visible_entity_ids: set | None) -> dict:
    """Session2 Reasoning Upgrade（Phase3）: omitted_actor_analysis[].expected_actor_entity_idが
    stage1_visible_entity_idsの部分集合であることを検証する（key_point_assessmentの
    controlling_entity_idsと同じ理由・同じ判定方式。非repair対象）。"""
    name = "media_omitted_actor_analysis_grounded"
    if stage1_visible_entity_ids is None:
        return _check(name, True)
    media_context = response.get("media_source_context") or {}
    fabricated = []
    for i, mec in enumerate(media_context.get("media_editorial_context") or []):
        for j, oaa in enumerate(mec.get("omitted_actor_analysis") or []):
            eid = oaa.get("expected_actor_entity_id")
            if eid and eid not in stage1_visible_entity_ids:
                fabricated.append(f"media_editorial_context[{i}].omitted_actor_analysis[{j}]={eid}")
    if fabricated:
        return _check(name, False,
                       "omitted_actor_analysis.expected_actor_entity_idにStage1へ提示していない"
                       f"entity_idがあります（創作の可能性、要人手確認）: {', '.join(fabricated)}")
    return _check(name, True)


# ==================== Media Intelligence Build（M8.5）====================
# 上記の_media_*系関数はすべてresponse_json（Expert Engineの実出力）を対象にする。
# 以下は同じObserved/Hypothesis分離・Evidence Grounding・矛盾保持の思想を、
# media_profile/media_episode/media_pattern knowledge_item自体のstructured_contentへ
# 直接適用する新規エントリポイント（response_jsonとは別のデータ形状のため関数を分離、
# 既存_media_*関数のロジックは変更しない）。

_QUALITATIVE_CORROBORATION_BASIS = {
    "independent_corroboration", "official_confirmation",
    "behavioral_corroboration", "documentary_corroboration",
}


def _media_kb_observed_free_of_inference_language(structured_content: dict) -> dict:
    """media_episode.observable_framing（Observedのみ）に意図言及語が混入していないか検査する。"""
    name = "media_kb_observed_free_of_inference_language"
    text = structured_content.get("observable_framing") or ""
    hit = next((m for m in _MEDIA_INFERENCE_LANGUAGE_MARKERS if m in text), None)
    if hit:
        return _check(name, False,
                       f"observable_framingに意図言及語「{hit}」が含まれています"
                       "（Observableは事実表現に限定し、意図はhypothesized_information_role側で書く）")
    return _check(name, True)


def _media_kb_hypothesis_corroboration_consistency(structured_content: dict) -> dict:
    """media_episode.confidence=高で、かつhypothesized_information_role（Hypothesis）を
    記載している場合、corroboration_basisが質的裏付け種別かつcorroboration_target=
    hypothesis_itselfでない限り許容しない（既存_anonymous_sourced_hypothesis_confidence_
    cappedと同じ契約）。"""
    name = "media_kb_hypothesis_corroboration_consistency"
    if structured_content.get("confidence") != "高":
        return _check(name, True)
    if not structured_content.get("hypothesized_information_role"):
        return _check(name, True)
    basis_ok = structured_content.get("corroboration_basis") in _QUALITATIVE_CORROBORATION_BASIS
    target_ok = structured_content.get("corroboration_target") == "hypothesis_itself"
    if not (basis_ok and target_ok):
        return _check(name, False,
                       f"confidence=高でHypothesisを記載していますが、corroboration_basis="
                       f"{structured_content.get('corroboration_basis')}・corroboration_target="
                       f"{structured_content.get('corroboration_target')}であり質的裏付け条件を"
                       "満たしていません")
    return _check(name, True)


def _media_kb_pattern_evidence_grounded(structured_content: dict) -> dict:
    """media_pattern.evidence_episode_idsが1件以上であることを検証する
    （スキーマのminItems:1と重複するが、DB上の実データに対する明示的な二重チェックとして持つ）。"""
    name = "media_kb_pattern_evidence_grounded"
    if not (structured_content.get("evidence_episode_ids") or []):
        return _check(name, False, "evidence_episode_idsが空です（複数Historical Episodeからのみ"
                                    "Pattern Candidateを作るという依頼書Section22の原則に反します）")
    return _check(name, True)


def _media_kb_pattern_exception_has_rationale(structured_content: dict) -> dict:
    """media_pattern.pattern_role=exceptionの場合、exception_conditionsまたは
    default_pattern_refのいずれかで『何に対する例外か』を明示していることを要求する
    （矛盾を無言で保持するだけにしない）。"""
    name = "media_kb_pattern_exception_has_rationale"
    if structured_content.get("pattern_role") == "exception":
        if not (structured_content.get("exception_conditions") or []) and not structured_content.get("default_pattern_ref"):
            return _check(name, False,
                           "pattern_role=exceptionですがexception_conditions/default_pattern_ref"
                           "のいずれも空です（何に対する例外かを明示してください）")
    return _check(name, True)


def _media_kb_profile_has_limitations_documented(structured_content: dict) -> dict:
    """media_profile.known_limitations/do_not_use_forが両方空の場合を検出する
    （依頼書Section8: Source Qualityを単一Scoreにせず限界を明記する原則。
    『万能で信頼できるSource』という暗黙の主張を防ぐ）。"""
    name = "media_kb_profile_has_limitations_documented"
    if not (structured_content.get("known_limitations") or []) and not (structured_content.get("do_not_use_for") or []):
        return _check(name, False,
                       "known_limitations・do_not_use_forが両方空です（Sourceの限界を"
                       "明記しない場合、万能なSourceという誤った印象を与えるリスクがあります）")
    return _check(name, True)


# ==================== M9 Actor×Media Integration Pilot ====================
# 以下3つはresponse_json（Expert Engine実出力）を対象とする（既存_media_*系と同じ形状）。
# Azure Callを増やさない（決定的Pythonチェックのみ、既存run_quality_guardの枠内で使う）。

_PATTERN_DETERMINISM_MARKERS = (
    "過去のパターンから明らかに", "間違いなく", "必ず", "確実に", "パターン通り必ず",
    "一貫して〜なので今回も確実に",
)


def _historical_pattern_not_asserted_as_certain_fact(response: dict) -> dict:
    """M9: 『過去にXする傾向がある』(historical pattern)を『今回も確実にXする』(current
    fact)であるかのように断定していないか検査する。既存_situation_actor_assessment_
    evidence_grounded_check/_anonymous_sourced_hypothesis_confidence_cappedと同じ
    Evidence Discipline思想を、決定論的な言い切り表現の直接検出という形で補完する
    （非repair対象）。"""
    name = "historical_pattern_not_asserted_as_certain_fact"
    texts = []
    for saa in response.get("situation_actor_assessment") or []:
        texts.append(("situation_actor_assessment", saa.get("estimated_real_preference")))
        texts.append(("situation_actor_assessment", saa.get("likely_reaction")))
    media_context = response.get("media_source_context") or {}
    for mec in media_context.get("media_editorial_context") or []:
        for hyp in mec.get("hypotheses") or []:
            texts.append(("media_hypothesis", hyp.get("hypothesis_text")))
    for field_name, text in texts:
        if not text:
            continue
        hit = next((m for m in _PATTERN_DETERMINISM_MARKERS if m in text), None)
        if hit:
            return _check(name, False,
                           f"{field_name}に断定的表現「{hit}」が見つかりました。過去のPattern"
                           "（Actor/Media双方）は参照材料であり、今回のEvidenceによる裏付けが"
                           "無い限り確定した現在の事実として言い切らないでください")
    return _check(name, True)


def _cross_intelligence_contradiction_not_silently_dropped(response: dict) -> dict:
    """M9: Actor Intelligence由来の解釈とMedia Intelligence由来の解釈が矛盾する場合に、
    一方を黙って消していないか検査する。具体的には、contradicted/weak/unsupportedと
    判定したhypothesis・alternative_interpretationについて、alternative_explanation/
    alternative_interpretation自体が空でないことを要求する（矛盾を検出したのに理由を
    書かないという抜け道を防ぐ。非repair対象）。"""
    name = "cross_intelligence_contradiction_not_silently_dropped"
    media_context = response.get("media_source_context") or {}
    for i, mec in enumerate(media_context.get("media_editorial_context") or []):
        for j, hyp in enumerate(mec.get("hypotheses") or []):
            if hyp.get("status") in ("contradicted", "weak", "unsupported") and not hyp.get("alternative_explanation"):
                return _check(name, False,
                               f"media_editorial_context[{i}].hypotheses[{j}]: status="
                               f"{hyp.get('status')}ですがalternative_explanationが空です"
                               "（矛盾を検出したなら、その理由・代替説明を明記してください）")
    for i, saa in enumerate(response.get("situation_actor_assessment") or []):
        if saa.get("alternative_interpretation_status") in ("weak", "unsupported", "contradicted") \
                and not saa.get("alternative_interpretation"):
            return _check(name, False,
                           f"situation_actor_assessment[{i}]: alternative_interpretation_status="
                           f"{saa.get('alternative_interpretation_status')}ですが"
                           "alternative_interpretationが空です")
    return _check(name, True)


def _evidence_attribution_not_conflated(response: dict, *, derived_intelligence_ids: set | None) -> dict:
    """M9: Actor/Media Pattern・Historical Episode・Profileのknowledge_item_id（＝
    複数Caseから導出されたDerived Intelligence）が、あたかも今回のCase固有のRaw Evidence
    であるかのように各種evidence_idsフィールドへ引用されていないか検査する。
    derived_intelligence_idsは呼び出し元がActor/Media Intelligence Packのknown_idsから
    渡す（Pack自体が無い場合はNoneを渡し、本チェックは無条件passとする。非repair対象）。"""
    name = "evidence_attribution_not_conflated"
    if not derived_intelligence_ids:
        return _check(name, True)
    conflated = []
    kpa = response.get("key_point_assessment") or {}
    for eid in (kpa.get("supporting_evidence_ids") or []):
        if eid in derived_intelligence_ids:
            conflated.append(f"key_point_assessment.supporting_evidence_ids={eid}")
    for i, saa in enumerate(response.get("situation_actor_assessment") or []):
        for eid in (saa.get("evidence_ids") or []):
            if eid in derived_intelligence_ids:
                conflated.append(f"situation_actor_assessment[{i}].evidence_ids={eid}")
    media_context = response.get("media_source_context") or {}
    for i, mec in enumerate(media_context.get("media_editorial_context") or []):
        for j, hyp in enumerate(mec.get("hypotheses") or []):
            for eid in (hyp.get("supporting_evidence_ids") or []):
                if eid in derived_intelligence_ids:
                    conflated.append(f"media_editorial_context[{i}].hypotheses[{j}].supporting_evidence_ids={eid}")
    if conflated:
        return _check(name, False,
                       "Derived Intelligence（Actor/Media PatternやHistorical Episode自体の"
                       f"knowledge_item_id）が生Evidenceとして引用されています: {', '.join(conflated)}"
                       "（PatternやEpisodeそのものはEvidenceではなく参照材料です）")
    return _check(name, True)


# ==================== M9.1 Evidence Weighting Fix ====================
# ユーザー指摘: Actor Historical Pattern / Media Historical Patternは「もっともらしさの
# 文脈(contextual prior/plausibility)」であり、現在の匿名情報源仮説を裏付ける
# 「独立したCurrent Evidence」ではない。両者が一致したという理由だけでconfidenceを
# 高く保つことを、LLMの自己申告に委ねず決定的Pythonコードで是正する
# （scrub_fabricated_evidence_idsと同じ「LLMを再度呼ばずresponseを直接書き換える」方式、
# 追加Azure Callなし）。

_CONFIDENCE_ADJUSTMENT_REASON = (
    "Historical actor/media patterns provide contextual plausibility but do not "
    "independently corroborate the current anonymous-source hypothesis."
)


def apply_confidence_weighting_fix(response: dict) -> list:
    """media_editorial_context.hypotheses[]のうち、confidence=高でありながら
    corroboration_basisが質的裏付け種別でない、またはcorroboration_targetが
    hypothesis_itselfでないもの（＝既存_anonymous_sourced_hypothesis_confidence_
    cappedが違反として検出する条件と完全に同じ）を、その場でconfidence=中へ
    決定的に引き下げ、confidence_adjustment_reasonを記録する。
    戻り値: 引き下げたhypothesisの場所（field_path）の一覧。"""
    adjusted = []
    qualitative_basis = {
        "independent_corroboration", "official_confirmation",
        "behavioral_corroboration", "documentary_corroboration",
    }
    media_context = response.get("media_source_context") or {}
    for i, mec in enumerate(media_context.get("media_editorial_context") or []):
        for j, hyp in enumerate(mec.get("hypotheses") or []):
            if hyp.get("confidence") != "高":
                continue
            basis_ok = hyp.get("corroboration_basis") in qualitative_basis
            target_ok = hyp.get("corroboration_target") == "hypothesis_itself"
            if not (basis_ok and target_ok):
                hyp["confidence"] = "中"
                hyp["confidence_adjustment_reason"] = _CONFIDENCE_ADJUSTMENT_REASON
                adjusted.append(f"media_editorial_context[{i}].hypotheses[{j}]")
    return adjusted


# ==================== Expert AI Depth & Grounding Upgrade（P0修正） ====================
# 以下3つは決定的post-processing関数（scrub_fabricated_evidence_ids/apply_confidence_
# weighting_fixと同じ「LLMを再呼び出さずresponseを直接書き換える」方式、追加Azure Callなし）。

# Phase R1 Chunk1 Actor Coverage Fix: profile_sourceがこの集合に該当する場合、そのActorの
# Actor Intelligenceは実質ゼロである（DB上にProfileが無い、またはPack自体に選定されて
# いない）。この場合はLLMの自己申告(general_knowledge_used)を待たず、決定的にTrueへ強制する
# ——「Actor Intelligence皆無なのにPersistent Preference等をProfile風に出力してしまう」問題は
# LLMの自己申告だけに頼ると見逃されるため（新規5ケースのブラインドテストで確認された残課題）。
_NO_ACTOR_PROFILE_SOURCES = {"none_available", "not_evaluated"}

_ACTOR_COVERAGE_BY_PROFILE_SOURCE = {
    "actor_intelligence_active": "full",
    "actor_intelligence_candidate": "partial",
    "none_available": "none",
    "not_evaluated": "none",
}


def _actor_coverage_for_profile_source(profile_source: str | None) -> str:
    return _ACTOR_COVERAGE_BY_PROFILE_SOURCE.get(profile_source, "none")


# Phase R1 Chunk3 fix: Institution Intelligence Packの決定的coverage判定（Person用
# _ACTOR_COVERAGE_BY_PROFILE_SOURCEと並行する、別の語彙のprofile_source値）。
_INSTITUTION_COVERAGE_BY_PROFILE_SOURCE = {
    "institution_intelligence_active": "full",
    "institution_intelligence_candidate": "partial",
    "none_available": "none",
}

_COVERAGE_RANK = {"none": 0, "partial": 1, "full": 2}


def _institution_coverage_for_profile_source(profile_source: str | None) -> str:
    return _INSTITUTION_COVERAGE_BY_PROFILE_SOURCE.get(profile_source, "none")


# Phase R1 Chunk3: subject_type=institutionのkey_actor_profiles[]エントリからは、
# Person Actor固有の「志向」「性格」「本音」に相当するフィールドを決定的に除去する
# （persistent_preference_evidenceのみ配列型のため空配列、他はnull）。Institution固有の
# フィールド（formal_role/decision_power/driving_forces等、手続き的・構造的な内容を
# 表す既存フィールド）はそのまま許容する——これらはPerson/Institution両方に意味を成す
# 中立的なフィールドであるため対象外とする。
_PERSON_ONLY_ACTOR_FIELDS = (
    "persistent_preference", "persistent_preference_evidence", "persistent_preference_change",
    "actor_model_challenge", "preference_action_gap", "strategic_relationship_preference",
)


def _combine_coverage(*coverages: str) -> str:
    """Person/Institution等、複数のIntelligence源のcoverageのうち最も強いものを返す
    （どちらか一方でも実在するIntelligenceに裏付けられているなら、general_knowledge_used
    を強制しない、というPhase R1 Chunk3の判定に使う）。"""
    return max(coverages, key=lambda c: _COVERAGE_RANK.get(c, 0), default="none")


def attach_actor_intelligence_provenance(
    response: dict, actor_intelligence_pack: dict | None,
    institution_intelligence_pack: dict | None = None,
) -> list:
    """P0-1 Actor Grounding Fix: key_actor_profiles[]の各itemへ、Actor Intelligence
    Pack（Person）とInstitution Intelligence Packの実データから決定的にactor_
    intelligence_provenanceを設定する（LLMの自己申告に頼らない）。

    Phase R1 Chunk1で拡張: general_knowledge_usedは原則LLMの自己申告を保持するが、
    Person用profile_sourceが_NO_ACTOR_PROFILE_SOURCES（Actor Intelligence皆無と
    判明している）の場合のみ、LLMが何を書いたかに関わらずTrueへ強制する。

    Phase R1 Chunk3で修正: 上記の強制は「Person Intelligence皆無」だけでなく
    「Institution Intelligenceも皆無」の場合に限定する。BISのような制度的Subject
    （key_actor_profiles[]にはPerson Actorと同じ配列で現れる）は、Person用
    actor_profileが無くてもInstitution Profileが存在すれば実在するIntelligenceに
    裏付けられているとみなし、Person Profile不在だけを理由にgeneral_knowledge_used
    を強制しない（Institution Intelligenceを追加した意味が消えてしまうため）。
    person_actor_coverage/institution_coverage/effective_intelligence_coverage/
    subject_typeを決定的に付与し、Person/Institutionどちらの裏付けかを人間・Quality
    Guardが判別できるようにする。confidence自体は本関数では変更しない
    （Current Evidence/Evidence Class/Claim Relevanceは既存の別Guard/cap関数の
    役割のまま）。
    actor_intelligence_pack・institution_intelligence_packが両方Noneの場合は何もしない。
    戻り値: 設定したactor_refの一覧。"""
    if actor_intelligence_pack is None and institution_intelligence_pack is None:
        return []
    by_actor_ref = {}
    for ka in (actor_intelligence_pack or {}).get("key_actors") or []:
        # selection側のactor_refはkey_actor_profiles[].actor_refと直接一致しない場合がある
        # ため、entity_idベースで突き合わせる（key_point_assessment同様の設計）。
        eid = ka.get("entity_id")
        if eid:
            by_actor_ref[eid] = ka
    by_institution = {}
    for institution in (institution_intelligence_pack or {}).get("institutions") or []:
        eid = institution.get("entity_id")
        if eid:
            by_institution[eid] = institution

    touched = []
    for kap in response.get("key_actor_profiles") or []:
        entity_id = kap.get("entity_id")
        pack_actor = by_actor_ref.get(entity_id) if entity_id else None
        pack_institution = by_institution.get(entity_id) if entity_id else None
        existing = kap.get("actor_intelligence_provenance") or {}
        llm_general_knowledge_used = existing.get("general_knowledge_used")

        person_profile_source = pack_actor.get("profile_source") if pack_actor is not None else "not_evaluated"
        person_coverage = _actor_coverage_for_profile_source(person_profile_source)

        institution_profile_source = pack_institution.get("profile_source") if pack_institution is not None else None
        institution_coverage = (
            _institution_coverage_for_profile_source(institution_profile_source)
            if pack_institution is not None else "none"
        )

        subject_type = "institution" if pack_institution is not None else "person"
        effective_coverage = _combine_coverage(person_coverage, institution_coverage)

        # Person・Institutionのどちらか一方でも実在するIntelligenceに裏付けられていれば
        # （effective_coverage != "none"）、LLMの自己申告をそのまま保持する。両方とも
        # 皆無の場合のみ強制Trueにする（従来のChunk1ロジックをInstitutionまで拡張）。
        general_knowledge_used = True if effective_coverage == "none" else llm_general_knowledge_used

        kap["actor_intelligence_provenance"] = {
            "actor_intelligence_status": (
                pack_actor.get("actor_intelligence_status") if pack_actor is not None else "not_evaluated"),
            "profile_source": person_profile_source,
            "historical_episode_ids": (pack_actor.get("historical_episode_ids") or []) if pack_actor is not None else [],
            "pattern_ids": (pack_actor.get("pattern_ids") or []) if pack_actor is not None else [],
            "exception_episode_ids": (pack_actor.get("exception_episode_ids") or []) if pack_actor is not None else [],
            "supporting_evidence_ids": existing.get("supporting_evidence_ids") or [],
            "general_knowledge_used": general_knowledge_used,
            "subject_type": subject_type,
            "person_actor_coverage": person_coverage,
            "institution_coverage": institution_coverage,
            "effective_intelligence_coverage": effective_coverage,
            # actor_coverageはPhase R1 Chunk1で導入した名前をそのまま維持する後方互換
            # エイリアス（値はeffective_intelligence_coverageと同じ、Person/Institution
            # 横断のcoverageを表す）。
            "actor_coverage": effective_coverage,
        }

        # Phase R1 Chunk3: subject_type=institutionの場合、Person固有フィールド
        # （「志向」「性格」「本音」に相当するもの）を決定的にnullへ強制する。プロンプト
        # 指示（_format_institution_intelligence_blockの注意書き）だけでは、実データE2Eで
        # LLMがpersistent_preference/preference_action_gapを埋めてしまうケースが確認された
        # ため（非決定的、毎回起きるとは限らない）、Guard/prompt頼みにせず決定的に除去する
        # （追加Azure Callなし）。
        if subject_type == "institution":
            for person_only_key in _PERSON_ONLY_ACTOR_FIELDS:
                if kap.get(person_only_key) is not None:
                    kap[person_only_key] = None if person_only_key != "persistent_preference_evidence" else []

        touched.append(kap.get("actor_ref"))
    return touched


_CONTROL_TYPE_ADJUSTMENT_REASON = (
    "control_type declared a controlling actor/structure but the required "
    "controlling_entity_ids / control_structure_summary evidence was empty; "
    "downgraded to 'unknown' to avoid an unsupported control-structure claim."
)


def _normalize_one_control_type_item(item: dict) -> bool:
    """1件のkey_point_assessment相当オブジェクト（トップレベルまたはcandidate_key_
    points[]の1件）を検証し、契約違反ならcontrol_type='unknown'へ書き換える。
    戻り値: 書き換えたかどうか。"""
    control_type = item.get("control_type")
    controlling_ids = item.get("controlling_entity_ids") or []
    summary = item.get("control_structure_summary")
    violated = False
    if control_type == "actor_controlled" and not controlling_ids:
        violated = True
    elif control_type == "distributed" and len(controlling_ids) < 2 and not summary:
        violated = True
    elif control_type == "mixed" and controlling_ids and not summary:
        violated = True
    elif control_type in ("institutionally_constrained", "structural") and not summary:
        violated = True
    if violated:
        item["control_type"] = "unknown"
        item["control_type_adjustment_reason"] = _CONTROL_TYPE_ADJUSTMENT_REASON
        return True
    return False


def normalize_control_type(response: dict) -> list:
    """P0-2 Control Structure Fix: key_point_assessment.control_typeおよび各
    candidate_key_points[].control_typeについて、controlling_entity_ids/
    control_structure_summaryの契約（既存_key_point_control_type_consistency_checkと
    同じ条件）に違反する場合、'unknown'へ決定的に書き換える。不整合を最終出力まで
    残さない（追加Azure Callなし）。戻り値: 書き換えたfield_pathの一覧。"""
    adjusted = []
    kpa = response.get("key_point_assessment") or {}
    if kpa and _normalize_one_control_type_item(kpa):
        adjusted.append("key_point_assessment")
    for i, cand in enumerate(kpa.get("candidate_key_points") or []):
        if _normalize_one_control_type_item(cand):
            adjusted.append(f"key_point_assessment.candidate_key_points[{i}]")
    return adjusted


_WEAK_EVIDENCE_CLASSES = {
    "historical_actor_pattern", "historical_media_pattern",
    "model_general_knowledge", "unsupported_assumption",
}
_EVIDENCE_CLASS_ADJUSTMENT_REASON = (
    "evidence_class indicates only historical-pattern/general-knowledge support "
    "(no current direct/indirect evidence); confidence capped at low per the "
    "Expert AI Depth & Grounding Upgrade evidence-weighting contract."
)


_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _scenario_has_only_derived_evidence(supporting_evidence, derived_intelligence_ids) -> bool | None:
    """main_scenario/alternative_scenariosのsupporting_evidence（自由記述混在の配列、
    厳密なevidenceIdList型ではない）のうち、UUID形式に見える項目だけをIDとして扱い、
    derived_intelligence_ids（Actor/Media Intelligence由来のHistorical Episode等）に
    一致しないUUIDが1件でもあればCurrent Evidenceありと推定してFalseを返す。
    UUID形式の項目が1件も無い場合（自由記述のみ、または空）は判定材料が無いため
    Noneを返す（evidence_classの自己申告のみに頼る）。"""
    if not supporting_evidence:
        return None
    uuid_like = [e for e in supporting_evidence if isinstance(e, str) and _UUID_RE.match(e.strip())]
    if not uuid_like:
        return None
    if not derived_intelligence_ids:
        return False
    return all(u in derived_intelligence_ids for u in uuid_like)


def cap_confidence_by_evidence_class(response: dict, derived_intelligence_ids=None) -> list:
    """9章 Confidence Weighting: evidence_classが_WEAK_EVIDENCE_CLASSESのみの場合、
    confidence=高/中を'低'へ決定的に引き下げる（M9.1 apply_confidence_weighting_fixと
    同じ設計、evidence_class軸への拡張）。situation_actor_assessment[]・
    media_editorial_context.hypotheses[]・key_point_assessment・main_scenario・
    alternative_scenarios[]（Evidence Weighting原則をMain/Alternative Scenarioまで
    貫通、新規Reasoning module追加ではなく既存原則の拡張）の5箇所を対象にする。
    main_scenario/alternative_scenariosはsupporting_evidenceが自由記述混在のため、
    evidence_classの自己申告に加え、UUID形式の項目がderived_intelligence_ids
    （Historical Episode等）にしか一致しない場合も同様に決定的補正する
    （_scenario_has_only_derived_evidence、defense-in-depth）。confidenceを
    引き下げてもオブジェクト自体は削除しない（low+関連仮説のunresolved+
    knowledge_gapとして保持）。"""
    adjusted = []
    kpa = response.get("key_point_assessment") or {}
    ec = kpa.get("evidence_class")
    if ec in _WEAK_EVIDENCE_CLASSES and kpa.get("confidence") in ("高", "中"):
        kpa["confidence"] = "低"
        kpa["confidence_adjustment_reason"] = _EVIDENCE_CLASS_ADJUSTMENT_REASON
        adjusted.append("key_point_assessment")
    for i, saa in enumerate(response.get("situation_actor_assessment") or []):
        ec = saa.get("evidence_class")
        if ec in _WEAK_EVIDENCE_CLASSES and saa.get("confidence") in ("高", "中"):
            saa["confidence"] = "低"
            saa["confidence_adjustment_reason"] = _EVIDENCE_CLASS_ADJUSTMENT_REASON
            adjusted.append(f"situation_actor_assessment[{i}]")
    media_context = response.get("media_source_context") or {}
    for i, mec in enumerate(media_context.get("media_editorial_context") or []):
        for j, hyp in enumerate(mec.get("hypotheses") or []):
            ec = hyp.get("evidence_class")
            if ec in _WEAK_EVIDENCE_CLASSES and hyp.get("confidence") in ("高", "中"):
                hyp["confidence"] = "低"
                hyp["confidence_adjustment_reason"] = _EVIDENCE_CLASS_ADJUSTMENT_REASON
                adjusted.append(f"media_editorial_context[{i}].hypotheses[{j}]")

    main = response.get("main_scenario") or {}
    if main:
        ec = main.get("evidence_class")
        only_derived = _scenario_has_only_derived_evidence(
            main.get("supporting_evidence"), derived_intelligence_ids)
        if (ec in _WEAK_EVIDENCE_CLASSES or only_derived is True) and main.get("confidence") in ("高", "中"):
            main["confidence"] = "低"
            main["confidence_adjustment_reason"] = _EVIDENCE_CLASS_ADJUSTMENT_REASON
            adjusted.append("main_scenario")
    for i, alt in enumerate(response.get("alternative_scenarios") or []):
        ec = alt.get("evidence_class")
        # alternative_scenariosにはsupporting_evidence相当のID配列が無いため、
        # ID-basedの補助チェックはevidence_classの自己申告のみに頼る。
        if ec in _WEAK_EVIDENCE_CLASSES and alt.get("confidence") in ("高", "中"):
            alt["confidence"] = "低"
            alt["confidence_adjustment_reason"] = _EVIDENCE_CLASS_ADJUSTMENT_REASON
            adjusted.append(f"alternative_scenarios[{i}]")
    return adjusted


_STRONG_CLAIM_RELEVANCE = {"direct", "strongly_relevant"}
_RELEVANCE_CONFIDENCE_ADJUSTMENT_REASON = (
    "evidence_relevance reported only structural/peripheral claim_relevance (no "
    "direct/strongly_relevant evidence for this specific claim); confidence capped "
    "at low per the Phase R1 claim-relevance discipline (evidence_class describes "
    "the evidence's nature, this is a separate axis for how directly it supports "
    "THIS claim — a current, official, direct-evidence-class item can still be "
    "only structurally relevant to a specific claim)."
)


def cap_confidence_by_claim_relevance(response: dict) -> list:
    """Phase R1 Chunk2: key_point_assessment/main_scenarioのevidence_relevance[]が
    自己申告されている場合、全件がstructural/peripheralのみ（direct/strongly_relevant
    が皆無）であるにもかかわらずconfidence=高/中のままなら、決定的に'低'へ引き下げる
    （cap_confidence_by_evidence_classと同じ設計、追加Azure Callなし）。evidence_class
    起点の調整とは別軸のため、書き込み先はconfidence_adjustment_reasonではなく
    relevance_confidence_adjustment_reasonに分離する（互いの理由を上書きしない）。
    evidence_relevance自体が空/未申告の場合は対象外とする（『申告が無い』ことと
    『申告した上で全て弱い』ことは区別する——前者はdirect_evidence_present_for_
    primary_key_point Guardの検出対象）。"""
    adjusted = []

    def _only_weak(entries) -> bool:
        if not entries:
            return False
        return not any(e.get("claim_relevance") in _STRONG_CLAIM_RELEVANCE for e in entries)

    kpa = response.get("key_point_assessment") or {}
    if _only_weak(kpa.get("evidence_relevance")) and kpa.get("confidence") in ("高", "中"):
        kpa["confidence"] = "低"
        kpa["relevance_confidence_adjustment_reason"] = _RELEVANCE_CONFIDENCE_ADJUSTMENT_REASON
        adjusted.append("key_point_assessment")

    main = response.get("main_scenario") or {}
    if _only_weak(main.get("evidence_relevance")) and main.get("confidence") in ("高", "中"):
        main["confidence"] = "低"
        main["relevance_confidence_adjustment_reason"] = _RELEVANCE_CONFIDENCE_ADJUSTMENT_REASON
        adjusted.append("main_scenario")

    return adjusted


# ---- Post-Freeze Integrity Audit R2 Hotfix(ユーザー指示、2026-09-22): Evidence Integrity Guard ----
# Phase R3再生成の検証で、US-China再生成responseにおいて「本文が存在しないtitle-only
# スタブ文書」を根拠に具体的な事実（GAO報告書の"BISの人員・IT・監視能力に課題がある"等）
# が捏造される事例が実データで確認された（TSMC再生成では同じスタブ文書群を保守的に
# 扱えていたため、決定論的なものではなく発生し得るリスクとして扱う）。これはAIの
# 「読みが甘い」というReasoning品質の問題ではなく、専門家セッション前に除去すべき
# Evidence Integrity問題と位置づける。Reasoning Prompt自体は変更せず、生成後の
# 決定的後処理（既存のscrub_fabricated_evidence_ids等と同じアーキテクチャ）として実装する。

_STUB_EVIDENCE_DISCLAIMER = (
    "（本文未取得の情報源のため、具体的内容はここに記載しない——タイトル・出典情報のみ既知）"
)


def cap_evidence_from_stub_documents(response: dict, stub_document_ids: set) -> list:
    """Title-only Evidence Guard: stub_document_ids（本文が空/タイトルと同一/極端に短い
    document_idの集合、呼び出し元がgeo_expert_engine._stub_document_idsで決定的に算出）を
    根拠とした具体的主張を、生成後に機械的に封じ込める。

    対象:
    (1) main_scenario/key_point_assessment.evidence_relevance・weakening_evidence_relevance
        のうちevidence_idがstub文書のもの: rationaleを開示せず、claim_relevanceを
        最弱区分(peripheral)へ強制する（Evidenceとしての参照自体は残すが、具体的内容の
        主張根拠としては使わせない）。
    (2) claims[]のうちsource_refがstub文書のもの: text（具体的主張本文）を開示せず、
        定型の注記へ置き換える（schema上textは必須のためnullにはしない）。
    (3) hypothesis_register[]のうち、supporting_evidence_idsが1件以上あり、かつ全てが
        stub文書のみで構成される場合: confidence=高/中を低へ強制する（cap_confidence_
        by_evidence_class等の既存パターンと同じ設計）。

    既知の限界（正直な申告）: key_actor_profiles等、明示的なsource_ref/evidence_idを
    持たない自由記述fieldへの間接的なstub由来の混入は、本Guardの構造的走査範囲外であり
    検知できない——claims[]・evidence_relevanceという「参照が明示された経路」のみを塞ぐ。"""
    if not stub_document_ids:
        return []
    touched = []

    def _demote_evidence_ref_array(container, key, path):
        for i, e in enumerate(container.get(key) or []):
            eid = e.get("evidence_id") if isinstance(e, dict) else None
            if eid in stub_document_ids:
                if e.get("rationale"):
                    e["rationale"] = None
                if e.get("claim_relevance") != "peripheral":
                    e["claim_relevance"] = "peripheral"
                touched.append(f"{path}.{key}[{i}]")

    kpa = response.get("key_point_assessment") or {}
    _demote_evidence_ref_array(kpa, "evidence_relevance", "key_point_assessment")
    main = response.get("main_scenario") or {}
    _demote_evidence_ref_array(main, "evidence_relevance", "main_scenario")
    _demote_evidence_ref_array(main, "weakening_evidence_relevance", "main_scenario")

    for i, c in enumerate(response.get("claims") or []):
        if c.get("source_ref") in stub_document_ids and c.get("text") != _STUB_EVIDENCE_DISCLAIMER:
            c["text"] = _STUB_EVIDENCE_DISCLAIMER
            touched.append(f"claims[{i}]")

    for i, h in enumerate(response.get("hypothesis_register") or []):
        support_ids = h.get("supporting_evidence_ids") or []
        if support_ids and all(sid in stub_document_ids for sid in support_ids) and h.get("confidence") in ("高", "中"):
            h["confidence"] = "低"
            h["stub_evidence_confidence_adjustment_reason"] = (
                "supporting_evidence_ids resolve only to title-only stub document(s) with no "
                "real body text; confidence capped at low per Evidence Integrity Guard."
            )
            touched.append(f"hypothesis_register[{i}]")

    return touched


_CLAIM_YEAR_PATTERN = re.compile(r"(20\d{2})")
_TEMPORAL_UNGROUNDED_DISCLAIMER = (
    "（引用元本文に一致する日付の記載が確認できないため、具体的な日付の主張はここに記載しない）"
)


def enforce_temporal_grounding_in_claims(response: dict, document_texts: dict) -> list:
    """Temporal Grounding Guard: claims[].source_refがdocumentを指す場合、claims[].text中の
    西暦年（例:「2026年」）が、実際にその文書のextracted_text（本文そのもの。published_at/
    fetched_at等のmetadata日付は含めない）に出現するかを機械的に確認する。一致する年が
    1つも無い場合、その年の主張はSource本文で裏付けられないと判断し、text全体を定型の
    注記へ置き換える（部分的な日付だけを削る精密な処理は行わない——決定的・保守的に
    「主張全体を保留する」という単純な設計に留める）。

    document_texts: document_id(source_refとして現れ得るもの) -> extracted_text（実本文）
    の辞書。event_id/evidence_id等documentでないsource_refは対象外（document_textsに
    キーが無ければ何もしない）——構造化されたevent日付フィールドとは別の検証経路のため。

    既知の限界: 年（4桁西暦）のみを見る決定的チェックであり、月日の精度までは検証しない
    （US-Chinaで実際に確認された事例は年単位で誤っていたため、この粒度で実害を捕捉できる）。"""
    if not document_texts:
        return []
    touched = []
    for i, c in enumerate(response.get("claims") or []):
        ref = c.get("source_ref")
        if ref not in document_texts:
            continue
        text = c.get("text") or ""
        years_in_claim = set(_CLAIM_YEAR_PATTERN.findall(text))
        if not years_in_claim:
            continue
        source_text = document_texts.get(ref) or ""
        years_in_source = set(_CLAIM_YEAR_PATTERN.findall(source_text))
        if years_in_claim & years_in_source:
            continue
        if text != _TEMPORAL_UNGROUNDED_DISCLAIMER:
            c["text"] = _TEMPORAL_UNGROUNDED_DISCLAIMER
            touched.append(f"claims[{i}]")
    return touched


def apply_known_source_types(response: dict, evidence_source_types: dict | None) -> list:
    """Phase R1 Chunk2: evidence_relevance[].source_typeをLLMの自己申告に依存させない。
    evidence_source_types（build_source_packがgeo_source_registry.source_typeから決定論的に
    導出したevidence_id→source_typeマップ）に該当evidence_idがあれば、LLMが何を書いたかに
    関わらずその値へ強制上書きする。マップに無いevidence_id（登録済みSourceに紐付かない、
    自由記述由来等）は'unknown'へ強制する——『LLMに「これはprimary」と言わせるだけ』を
    防ぐため、検証できないsource_typeは常にunknownとして扱う（追加Azure Callなし）。"""
    evidence_source_types = evidence_source_types or {}
    touched = []

    def _apply(container: dict, path: str):
        entries = container.get("evidence_relevance") or []
        for i, e in enumerate(entries):
            eid = e.get("evidence_id")
            resolved = evidence_source_types.get(eid, "unknown")
            if e.get("source_type") != resolved:
                e["source_type"] = resolved
                touched.append(f"{path}.evidence_relevance[{i}]")

    kpa = response.get("key_point_assessment")
    if kpa:
        _apply(kpa, "key_point_assessment")
    main = response.get("main_scenario")
    if main:
        _apply(main, "main_scenario")
    return touched


_GENERAL_KNOWLEDGE_CONFIDENCE_ADJUSTMENT_REASON = (
    "actor_intelligence_provenance.general_knowledge_used=true and this case has no "
    "current-case evidence_ids backing the assessment; confidence capped at low per "
    "the P0-1 General Knowledge Confidence Cap (a model-general-knowledge-only actor "
    "assumption must not be promoted to medium/high confidence)."
)


def cap_confidence_by_general_knowledge_flag(response: dict) -> list:
    """P0-1（今回の3点修正 1番目）: key_actor_profiles[]の各itemについて、
    actor_intelligence_provenance.general_knowledge_used=trueかつ当該itemの
    evidence_ids（今回のCase固有のCurrent Evidence）が空であるにもかかわらず
    confidence=高/中のままの場合、決定的にconfidence='低'へ引き下げる。
    既存の_unsupported_actor_assumption_not_promoted Guardは検出のみ（非repair対象）
    だったが、本関数はcap_confidence_by_evidence_classと同じ設計でこれを補正する
    （追加Azure Callなし）。evidence_idsが存在する場合（Current Evidenceに裏付けられた
    General Knowledge補完）は対象外とする——General Knowledge使用自体を禁止するのではなく、
    Current Evidenceが無いまま昇格することだけを防ぐ。"""
    adjusted = []
    for i, kap in enumerate(response.get("key_actor_profiles") or []):
        prov = kap.get("actor_intelligence_provenance") or {}
        if prov.get("general_knowledge_used") is not True:
            continue
        if kap.get("evidence_ids") or []:
            continue
        if kap.get("confidence") in ("高", "中"):
            kap["confidence"] = "低"
            kap["confidence_adjustment_reason"] = _GENERAL_KNOWLEDGE_CONFIDENCE_ADJUSTMENT_REASON
            adjusted.append(f"key_actor_profiles[{i}]")
    return adjusted


def separate_historical_evidence_in_key_point(response: dict, derived_intelligence_ids) -> list:
    """P0-2（今回の3点修正 2番目）: key_point_assessment（トップレベル＋
    candidate_key_points[]の各件）のsupporting_evidence_idsに、Actor/Media
    Intelligence Packが導出したDerived Intelligence ID（historical_episode_id等、
    今回のCase固有のRaw Evidenceではないもの）が紛れていないかを検査し、決定的に
    historical_supporting_evidence_idsへ退避する（既存_evidence_attribution_not_
    conflated Guardが検出していた問題の根本修正、追加Azure Callなし）。
    分離の結果supporting_evidence_idsが空になり、かつconfidence=高/中の場合は
    Historical PatternをCurrent Proofとして扱わないという原則に従い低へ引き下げる
    （トップレベルのkey_point_assessmentのみ。candidate_key_points[]にはconfidence
    フィールドが無いためID分離のみ行う）。derived_intelligence_idsが空/Noneの場合は
    何もしない。"""
    if not derived_intelligence_ids:
        return []
    touched = []

    def _separate_ids(item: dict) -> list:
        current = item.get("supporting_evidence_ids") or []
        historical = [e for e in current if e in derived_intelligence_ids]
        if not historical:
            return []
        remaining = [e for e in current if e not in derived_intelligence_ids]
        item["supporting_evidence_ids"] = remaining or None
        existing_hist = item.get("historical_supporting_evidence_ids") or []
        merged_hist = list(dict.fromkeys(list(existing_hist) + historical))
        item["historical_supporting_evidence_ids"] = merged_hist
        return remaining

    kpa = response.get("key_point_assessment") or {}
    if kpa:
        remaining = _separate_ids(kpa)
        if kpa.get("historical_supporting_evidence_ids"):
            touched.append("key_point_assessment")
            if not remaining and kpa.get("confidence") in ("高", "中"):
                kpa["confidence"] = "低"
                kpa["confidence_adjustment_reason"] = (
                    "supporting_evidence_ids consisted solely of Derived Intelligence "
                    "IDs (historical episodes / actor-intelligence-derived evidence) "
                    "with no remaining current-case evidence after separation; "
                    "confidence capped at low (historical pattern is prior/"
                    "plausibility, not current proof)."
                )
        for i, cand in enumerate(kpa.get("candidate_key_points") or []):
            _separate_ids(cand)
            if cand.get("historical_supporting_evidence_ids"):
                touched.append(f"key_point_assessment.candidate_key_points[{i}]")
    return touched


def separate_historical_evidence_in_actor_and_situation_assessment(
        response: dict, derived_intelligence_ids) -> list:
    """P0-2追加修正（Neutral再実行で発見したGap対応）: key_point_assessmentだけでなく
    key_actor_profiles[].evidence_idsとsituation_actor_assessment[].evidence_idsに
    紛れ込んだDerived Intelligence ID（historical_episode_id等）も同様に決定的に
    除去する。既存scrub_fabricated_evidence_idsと同じ『in-place除去、新規schema
    field追加なし、戻り値で報告』方式を踏襲する（新規フィールドを増やしたくない、
    というユーザー方針に合わせる）。

    これを`cap_confidence_by_general_knowledge_flag`より前に実行することで、
    『evidence_idsが非空だからCurrent Evidenceがある』という誤判定
    （実際にはHistorical Episode IDのみが入っていた）を防ぐ。同時に
    `evidence_attribution_not_conflated`Guard（既存M9チェック）が同じ箇所を
    再度Failさせることも防ぐ。derived_intelligence_idsが空/Noneの場合は
    何もしない。戻り値: 除去が発生した箇所と除去したIDのdetail一覧。"""
    if not derived_intelligence_ids:
        return []
    touched = []
    for i, kap in enumerate(response.get("key_actor_profiles") or []):
        ids = kap.get("evidence_ids") or []
        historical = [e for e in ids if e in derived_intelligence_ids]
        if historical:
            kap["evidence_ids"] = [e for e in ids if e not in derived_intelligence_ids] or None
            touched.append(f"key_actor_profiles[{i}]:removed={historical}")
    for i, saa in enumerate(response.get("situation_actor_assessment") or []):
        ids = saa.get("evidence_ids") or []
        historical = [e for e in ids if e in derived_intelligence_ids]
        if historical:
            saa["evidence_ids"] = [e for e in ids if e not in derived_intelligence_ids] or None
            touched.append(f"situation_actor_assessment[{i}]:removed={historical}")
    return touched


_PATTERN_APPLICABILITY_FALLBACK_CONCLUSION = (
    "Applicability was not assessed by the model for this pattern; defaulting to "
    "'uncertain' per the Actor Pattern Applicability requirement (a referenced "
    "Historical Pattern must not be silently auto-applied without an explicit "
    "current-case applicability judgement)."
)


def ensure_pattern_applicability_assessment(response: dict) -> list:
    """P1（今回の3点修正 3番目）: key_actor_profiles[]の各itemについて、
    relevant_decision_patternsで参照されたPatternのうちpattern_applicability_
    assessmentで評価されていないものがあれば、applicability='uncertain'の
    Fallback Entryを決定的に補完する（追加Azure Callなし。LLMへ差し戻さない）。
    既存の_actor_pattern_applicability_present Guardは検出のみ（非repair対象）
    だったが、本関数は『Historical Patternを自動適用しない』という原則を、
    LLMが適用性評価を書き忘れた場合でも安全側（uncertain）へ倒す形で保証する。"""
    touched = []
    for i, kap in enumerate(response.get("key_actor_profiles") or []):
        patterns = kap.get("relevant_decision_patterns") or []
        if not patterns:
            continue
        assessed = list(kap.get("pattern_applicability_assessment") or [])
        assessed_summaries = {a.get("pattern_summary") for a in assessed if a.get("pattern_summary")}
        missing = [p for p in patterns if p not in assessed_summaries]
        if not missing:
            continue
        for p in missing:
            assessed.append({
                "pattern_id": None,
                "pattern_summary": p,
                "current_similarities": [],
                "current_differences": [],
                "exception_evidence_ids": [],
                "applicability": "uncertain",
                "conclusion": _PATTERN_APPLICABILITY_FALLBACK_CONCLUSION,
            })
        kap["pattern_applicability_assessment"] = assessed
        touched.append(f"key_actor_profiles[{i}]")
    return touched


# ==================== Expert AI Depth & Grounding Upgrade（18章 Quality Guard追加） ====================

def _actor_intelligence_provenance_present(response: dict) -> dict:
    """Actor/Institution Intelligenceがどちらも利用不可
    （effective_intelligence_coverage=none）であるにもかかわらず、persistent_
    preference/relevant_decision_patternsのような実質的なAssessmentが生成されており、
    general_knowledge_usedが明示されていない場合を検出する（非repair対象）。

    Phase R1 Chunk3修正: 判定はPerson専用のprofile_source単体ではなく
    effective_intelligence_coverage（Person/Institution横断）で行う——BISのような
    Institution Subjectは、Person用actor_profileが無くてもInstitution Intelligenceが
    あれば実在するIntelligenceに裏付けられているとみなし、誤検出しない
    （effective_intelligence_coverageが無い場合はprofile_sourceのみで後方互換に判定する）。"""
    name = "actor_intelligence_provenance_present"
    for i, kap in enumerate(response.get("key_actor_profiles") or []):
        prov = kap.get("actor_intelligence_provenance") or {}
        effective_coverage = prov.get("effective_intelligence_coverage")
        if effective_coverage is None:
            # 後方互換: effective_intelligence_coverageが無い（旧形式の呼び出し元・テスト）
            # 場合はPerson用profile_sourceのみで判定する。
            no_coverage = prov.get("profile_source") in ("none_available", "not_evaluated", None)
        else:
            no_coverage = effective_coverage == "none"
        has_rich_assessment = bool(kap.get("persistent_preference")) or bool(kap.get("relevant_decision_patterns"))
        if no_coverage and has_rich_assessment and prov.get("general_knowledge_used") is not True:
            return _check(name, False,
                           f"key_actor_profiles[{i}]: Actor/Institution Intelligenceが"
                           "どちらも利用不可なのにpersistent_preference/relevant_decision_"
                           "patternsが生成されており、general_knowledge_usedが明示されて"
                           "いません")
    return _check(name, True)


def _actor_pattern_applicability_present(response: dict) -> dict:
    """relevant_decision_patternsでHistorical Patternを参照した場合、
    pattern_applicability_assessmentが存在することを要求する（非repair対象）。"""
    name = "actor_pattern_applicability_present"
    for i, kap in enumerate(response.get("key_actor_profiles") or []):
        if (kap.get("relevant_decision_patterns") or []) and not (kap.get("pattern_applicability_assessment") or []):
            return _check(name, False,
                           f"key_actor_profiles[{i}]: relevant_decision_patternsを参照して"
                           "いますがpattern_applicability_assessmentがありません（Historical"
                           "Patternを自動適用しないこと）")
    return _check(name, True)


def _unsupported_actor_assumption_not_promoted(response: dict) -> dict:
    """general_knowledge_used=trueのActor Assessmentがconfidence=高/中へ昇格して
    いないか検出する（非repair対象）。"""
    name = "unsupported_actor_assumption_not_promoted"
    for i, kap in enumerate(response.get("key_actor_profiles") or []):
        prov = kap.get("actor_intelligence_provenance") or {}
        if prov.get("general_knowledge_used") is True and kap.get("confidence") in ("高", "中"):
            return _check(name, False,
                           f"key_actor_profiles[{i}]: general_knowledge_used=trueなのに"
                           f"confidence={kap.get('confidence')}です（unsupported assumptionを"
                           "Medium/High confidenceへ昇格させないこと）")
    return _check(name, True)


def _weak_analogy_not_used_as_strong_evidence(response: dict) -> dict:
    """main_scenario.historical_analogies_usedにanalogy_strength=weakが含まれる
    にもかかわらずmain_scenario.confidence=高になっていないか検出する（非repair対象）。"""
    name = "weak_analogy_not_used_as_strong_evidence"
    main_scenario = response.get("main_scenario") or {}
    analogies = main_scenario.get("historical_analogies_used") or []
    has_weak = any(a.get("analogy_strength") == "weak" for a in analogies)
    if has_weak and main_scenario.get("confidence") == "高":
        return _check(name, False,
                       "main_scenario: analogy_strength=weakなHistorical Analogyが含まれて"
                       "いるにもかかわらずconfidence=高です（Weak Analogyはillustrative only、"
                       "強いSupporting Evidenceとして扱わないこと）")
    return _check(name, True)


_GENERIC_NARRATIVE_MARKERS = (
    "一般的に", "通常このような場合", "典型的には", "よくあるパターンとして",
)


def _evidence_substitution_detected(response: dict) -> dict:
    """situation_actor_assessment[]/media hypothesesで、evidence_classが
    model_general_knowledge/unsupported_assumptionでありながらconfidence=高/中の
    ままになっていないか（cap_confidence_by_evidence_classの取りこぼしが無いか）を
    二重チェックする。加えて、一般化された物言いのマーカーをヒューリスティック検出する
    （非repair対象）。"""
    name = "evidence_substitution_detected"
    weak = {"model_general_knowledge", "unsupported_assumption"}
    for i, saa in enumerate(response.get("situation_actor_assessment") or []):
        if saa.get("evidence_class") in weak and saa.get("confidence") in ("高", "中"):
            return _check(name, False,
                           f"situation_actor_assessment[{i}]: evidence_class="
                           f"{saa.get('evidence_class')}なのにconfidence={saa.get('confidence')}"
                           "のままです")
        text = saa.get("estimated_real_preference") or ""
        hit = next((m for m in _GENERIC_NARRATIVE_MARKERS if m in text), None)
        if hit and not (saa.get("evidence_ids") or []):
            return _check(name, False,
                           f"situation_actor_assessment[{i}]: estimated_real_preferenceに"
                           f"一般化表現「{hit}」が含まれ、evidence_idsも空です（Evidence不足を"
                           "一般的なナラティブで埋めていないか要確認）")
    return _check(name, True)


def _hypothesis_pruning_present(response: dict) -> dict:
    """hypothesis_registerが複数仮説を持ち、全件がretainedのみになっていないか
    （棄却・未解決の記録が残っているか）を検証する（非repair対象）。"""
    name = "hypothesis_pruning_present"
    if response.get("hypothesis_register") is None:
        # situation_actor_assessmentモジュール（deepプロファイル限定）が発火して
        # いない場合はhypothesis_register自体が存在しない。その場合は対象外として
        # passする（quick/standardプロファイルを不当にincompleteと判定しない）。
        return _check(name, True)
    register = response.get("hypothesis_register") or []
    if len(register) < 2:
        return _check(name, False,
                       f"hypothesis_registerが{len(register)}件しかありません（H0〜H4の"
                       "競合仮説を複数検討し、棄却・未解決も含めて記録してください）")
    if all((h.get("status") == "retained") for h in register):
        return _check(name, False,
                       "hypothesis_registerの全件がstatus=retainedです（Evidence Competitionを"
                       "経て少なくとも1件は棄却または未解決として扱われるはずです）")
    return _check(name, True)


def _actor_model_challenged(response: dict) -> dict:
    """Key Actorについて最低1件、actor_model_challenge（既存Actor Intelligence自体が
    今回のCaseに適用できない可能性の検討）が存在することを検証する（非repair対象）。
    persistent_preference/relevant_decision_patternsを伴う『実質的な』Actor
    Assessmentが1件も無い場合（actor_ref/actor/confidenceのみの最小限プロファイル
    しか無い場合）は対象外とする——挑戦すべき実質的な主張がそもそも無いため。

    Phase R1（Chunk6後P1）: persistent_preference/relevant_decision_patterns自体は
    Actor Intelligence Packに根拠が無くてもLLMが一般知識で埋めてしまうことがあるため
    （その場合はcap_confidence_by_general_knowledge_flag等の既存機構が別途対応する）、
    「実質的」の判定基準にactor_intelligence_provenance.effective_intelligence_
    coverage（Chunk1/3で導入した決定的な実grounding指標）も加える。coverage=noneの
    Actorにまでchallenge必須を課すと、根拠の無いProfileについて根拠の無いChallenge文を
    書かせるだけになり、Guardのための作文を助長するため対象から除外する。"""
    name = "actor_model_challenged"
    profiles = response.get("key_actor_profiles") or []
    substantial = []
    for kap in profiles:
        if not (kap.get("persistent_preference") or kap.get("relevant_decision_patterns")):
            continue
        provenance = kap.get("actor_intelligence_provenance") or {}
        coverage = provenance.get("effective_intelligence_coverage") or provenance.get("actor_coverage")
        if coverage in (None, "none"):
            continue
        substantial.append(kap)
    if not substantial:
        return _check(name, True)
    if not any(kap.get("actor_model_challenge") for kap in substantial):
        return _check(name, False,
                       "persistent_preference/relevant_decision_patternsを伴う実質的な"
                       "Actor Assessmentがあるにもかかわらず、全件でactor_model_challenge"
                       "が空です（既存Actor Intelligence自体が今回のCaseに適用できない"
                       "可能性を、最低1名について検討してください）")
    return _check(name, True)


def validate_media_knowledge_item(knowledge_type: str, structured_content: dict) -> list:
    """media_profile/media_episode/media_patternのstructured_contentへ、既存Quality Guardと
    同じ思想のヒューリスティックチェックを適用する（response_json向けrun_quality_guardとは別の
    knowledge_item向けエントリポイント。Human Review前の機械的な一次チェックとして使う）。"""
    if knowledge_type == "media_episode":
        return [
            _media_kb_observed_free_of_inference_language(structured_content),
            _media_kb_hypothesis_corroboration_consistency(structured_content),
        ]
    if knowledge_type == "media_pattern":
        return [
            _media_kb_pattern_evidence_grounded(structured_content),
            _media_kb_pattern_exception_has_rationale(structured_content),
        ]
    if knowledge_type == "media_profile":
        return [_media_kb_profile_has_limitations_documented(structured_content)]
    return []


def _investigation_question_answer_grounded(response: dict) -> dict:
    """Session2 Reasoning Upgrade（Phase4）: investigation_questions[]の双方向契約を検証する。
    can_current_evidence_answer=trueならanswer_if_yesが非nullかつanswer_evidence_idsが
    1件以上、falseならanswer_if_yesが必ずnullであること（trueと自己申告しつつ回答本文も
    Evidenceも書かない、という抜け道を防ぐ。非repair対象）。"""
    name = "investigation_question_answer_grounded"
    for i, q in enumerate(response.get("investigation_questions") or []):
        can_answer = q.get("can_current_evidence_answer")
        answer = q.get("answer_if_yes")
        if can_answer:
            if not (answer or "").strip() or not (q.get("answer_evidence_ids") or []):
                return _check(name, False,
                               f"investigation_questions[{i}]: can_current_evidence_answer=trueですが"
                               "answer_if_yesが空、またはanswer_evidence_idsが空です")
        else:
            if answer:
                return _check(name, False,
                               f"investigation_questions[{i}]: can_current_evidence_answer=falseですが"
                               "answer_if_yesが非nullです")
    return _check(name, True)


def _key_point_evidence_grounded_check(response: dict) -> dict:
    """Session2 Reasoning Upgrade: key_point_assessment.confidence=高の場合、
    supporting_evidence_idsが1件以上あることを検証する（非repair対象）。"""
    name = "key_point_evidence_grounded"
    kpa = response.get("key_point_assessment") or {}
    if kpa.get("confidence") == "高" and not (kpa.get("supporting_evidence_ids") or []):
        return _check(name, False,
                       "key_point_assessment.confidence=高ですがsupporting_evidence_idsが空です")
    return _check(name, True)


def _claim_relevance_ids_subset_of_cited(evidence_relevance, cited_ids) -> bool:
    if not evidence_relevance:
        return True
    cited = set(cited_ids or [])
    return all(e.get("evidence_id") in cited for e in evidence_relevance)


def _claim_evidence_semantic_relevance_present_check(response: dict) -> dict:
    """Phase R1 Chunk2: key_point_assessment.supporting_evidence_ids（strictな
    evidenceIdList、$ref先はID専用配列）が非空の場合、evidence_relevanceも申告されて
    いることを検証する（Evidence IDが正しいだけではConfidence昇格を許さない、という
    Chunk2のP0原則。非repair対象）。main_scenario.supporting_evidenceは自由記述混在の
    配列（IDとは限らない、例:「頻度増加」のような説明文）のため、非空であることを
    理由に必須化はしない——evidence_relevanceが申告されている場合のみ、そのIDが
    実在するかを検証する。evidence_relevance内のevidence_idがsupporting_evidence_ids
    に実在しない場合も検出する（Semantic Relevance error）。"""
    name = "claim_evidence_semantic_relevance_present"
    kpa = response.get("key_point_assessment") or {}
    kpa_ids = kpa.get("supporting_evidence_ids") or []
    kpa_relevance = kpa.get("evidence_relevance")
    if kpa_ids and not kpa_relevance:
        return _check(name, False,
                       "key_point_assessment.supporting_evidence_idsが非空ですが"
                       "evidence_relevanceが申告されていません")
    if not _claim_relevance_ids_subset_of_cited(kpa_relevance, kpa_ids):
        return _check(name, False,
                       "key_point_assessment.evidence_relevanceにsupporting_evidence_ids"
                       "に存在しないevidence_idが含まれています")

    main = response.get("main_scenario") or {}
    main_ids = main.get("supporting_evidence") or []
    main_relevance = main.get("evidence_relevance")
    if not _claim_relevance_ids_subset_of_cited(main_relevance, main_ids):
        return _check(name, False,
                       "main_scenario.evidence_relevanceにsupporting_evidenceに"
                       "存在しないevidence_idが含まれています")
    return _check(name, True)


def _supporting_knowledge_refs_grounded_check(response: dict) -> dict:
    """Phase R1 Chunk4 P0 carry-over: key_point_assessment.supporting_knowledge_refs
    （Evidenceではなく、Actor/Institution/Media Intelligence Packを根拠に引用するための
    専用フィールド）のrefが、実在するactor_ref/entity_idを指しているかを検証する
    （非repair対象）。type=media_intelligenceはcross-check可能な一覧が本レスポンス内に
    無いため、ref非空のみ検証する（将来Media Intelligence Packのknown_idsが渡された
    場合に強化する余地あり）。"""
    name = "supporting_knowledge_refs_grounded"
    known_actor_refs = {kap.get("actor_ref") for kap in response.get("key_actor_profiles") or [] if kap.get("actor_ref")}
    known_institution_entity_ids = {
        kap.get("entity_id") for kap in response.get("key_actor_profiles") or []
        if kap.get("entity_id") and (kap.get("actor_intelligence_provenance") or {}).get("subject_type") == "institution"
    }
    kpa = response.get("key_point_assessment") or {}
    refs = list(kpa.get("supporting_knowledge_refs") or [])
    for cand in kpa.get("candidate_key_points") or []:
        refs += list(cand.get("supporting_knowledge_refs") or [])
    for r in refs:
        ref_type, ref_value = r.get("type"), r.get("ref")
        if not ref_value:
            return _check(name, False, "supporting_knowledge_refsにrefが空の項目があります")
        if ref_type == "actor_intelligence" and ref_value not in known_actor_refs:
            return _check(name, False,
                           f"supporting_knowledge_refs: type=actor_intelligenceのref={ref_value}が"
                           "key_actor_profiles[].actor_refに実在しません")
        if ref_type == "institution_intelligence" and ref_value not in known_institution_entity_ids:
            return _check(name, False,
                           f"supporting_knowledge_refs: type=institution_intelligenceのref={ref_value}が"
                           "subject_type=institutionのkey_actor_profiles[].entity_idに実在しません")
    return _check(name, True)


def _direct_evidence_present_for_primary_key_point_check(response: dict) -> dict:
    """Phase R1 Chunk2 P0: key_point_assessment/main_scenarioのconfidence=高の場合、
    evidence_relevanceに少なくとも1件claim_relevance=direct/strongly_relevantが
    存在することを検証する（Structural/Peripheral Evidenceのみでmedium/high
    confidenceへ昇格することを防ぐ、Case2 Semiconductorのような誤昇格の再発防止。
    非repair対象——決定的な引き下げ自体はcap_confidence_by_claim_relevanceが行う）。"""
    name = "direct_evidence_present_for_primary_key_point"
    kpa = response.get("key_point_assessment") or {}
    if kpa.get("confidence") == "高":
        relevance = kpa.get("evidence_relevance") or []
        if relevance and not any(e.get("claim_relevance") in _STRONG_CLAIM_RELEVANCE for e in relevance):
            return _check(name, False,
                           "key_point_assessment.confidence=高ですがevidence_relevanceに"
                           "direct/strongly_relevantが1件もありません")
    main = response.get("main_scenario") or {}
    if main.get("confidence") == "高":
        relevance = main.get("evidence_relevance") or []
        if relevance and not any(e.get("claim_relevance") in _STRONG_CLAIM_RELEVANCE for e in relevance):
            return _check(name, False,
                           "main_scenario.confidence=高ですがevidence_relevanceに"
                           "direct/strongly_relevantが1件もありません")
    return _check(name, True)


def _key_point_candidates_not_homogeneous_check(response: dict) -> dict:
    """Phase R1 Chunk5: candidate_key_pointsが3件以上あるのに全件同一control_typeの
    場合、比較検討が形だけになっている（Surface/Actor/Structural等の異なる説明タイプを
    実質的に競わせていない）可能性を検出する（advisory、non-repair対象。『Key Pointが
    正しいが一般的』問題——実際には別の切り口を検討していないケースの兆候）。"""
    name = "key_point_candidates_not_homogeneous"
    kpa = response.get("key_point_assessment") or {}
    candidates = kpa.get("candidate_key_points") or []
    if len(candidates) >= 3:
        control_types = {c.get("control_type") for c in candidates if c.get("control_type")}
        if len(control_types) <= 1:
            return _check(name, False,
                           f"candidate_key_pointsが{len(candidates)}件ありますが、"
                           "全件control_typeが同一で実質的な比較検討の痕跡がありません")
    return _check(name, True)


def _key_point_selection_not_unsupported_novelty_check(response: dict) -> dict:
    """Phase R1 Chunk5: primary_key_pointがEvidence根拠を一切持たないのに、
    rejected_candidatesの中にEvidence根拠を持つ候補がある場合、より地味だが根拠のある
    候補を差し置いて根拠のない候補をnoveltyのためだけに選んでいないかを検出する
    （advisory、non-repair対象）。"""
    name = "key_point_not_selected_for_novelty"
    kpa = response.get("key_point_assessment") or {}
    candidates = kpa.get("candidate_key_points") or []
    if not candidates:
        return _check(name, True)

    def _has_evidence(entry: dict) -> bool:
        return bool(entry.get("supporting_evidence_ids") or entry.get("historical_supporting_evidence_ids"))

    primary_text = kpa.get("primary_key_point")
    primary_candidate = next((c for c in candidates if c.get("key_point") == primary_text), None)
    if primary_candidate is None or _has_evidence(primary_candidate) or _has_evidence(kpa):
        return _check(name, True)
    rejected_texts = {rc.get("key_point") for rc in (kpa.get("rejected_candidates") or [])}
    evidenced_rejected = [c for c in candidates
                          if c.get("key_point") in rejected_texts and _has_evidence(c)]
    if evidenced_rejected:
        return _check(name, False,
                       "primary_key_pointにEvidence根拠が無い一方、棄却された候補に"
                       "Evidence根拠のあるものがあります（noveltyのための選択でないか要確認）")
    return _check(name, True)


_FOCUS_SELECTION_TRIGGER_TYPES = {
    "model_or_norm_mismatch", "decision_power", "higher_order_objective",
    "behavior_driving_force", "audience_substance_gap", "premise_or_constraint_change",
    "implementation_or_feasibility", "causal_chain_break", "phase_or_time_horizon_shift",
    "discriminating_hinge", "media_or_source_anomaly", "other",
}

_ANOMALY_CONCLUSIVE_LANGUAGE_MARKERS = (
    "であることが確定した", "であることが判明した", "は事実である", "は真実である",
    "であることは間違いない", "が証明された",
)


def _focus_selection_why_now_present_check(response: dict) -> dict:
    """Focus Selection / Attention Prioritization（2026-09-06追加）: focus_selectionが
    存在する場合、why_now（他の候補ではなくなぜ今これを見るか）とdecision_sensitive_outcome
    （判断が変われば何が変わるか）が空でないことを検証する（advisory、non-repair対象、
    trigger_types自体は該当なしでnullのままでよいためこのチェック対象外）。"""
    name = "focus_selection_why_now_present"
    fs = (response.get("key_point_assessment") or {}).get("focus_selection")
    if not fs or not fs.get("trigger_types"):
        return _check(name, True)
    missing = []
    if not (fs.get("why_now") or "").strip():
        missing.append("why_now")
    if not (fs.get("decision_sensitive_outcome") or "").strip():
        missing.append("decision_sensitive_outcome")
    if missing:
        return _check(name, False,
                       f"focus_selection.trigger_typesが設定されているのに{', '.join(missing)}"
                       "が空です")
    return _check(name, True)


def _focus_selection_trigger_types_within_enum_check(response: dict) -> dict:
    """Focus Selection（2026-09-06追加）: trigger_typesが定義済みenumの値のみで構成されて
    いるかを検証する（Azure Structured Output strict modeで通常は防がれるが、手動構築された
    responseや将来のstrict=False経路向けの二重の安全網、advisory・non-repair対象）。"""
    name = "focus_selection_trigger_types_within_enum"
    fs = (response.get("key_point_assessment") or {}).get("focus_selection") or {}
    trigger_types = fs.get("trigger_types") or []
    invalid = sorted(set(trigger_types) - _FOCUS_SELECTION_TRIGGER_TYPES)
    if invalid:
        return _check(name, False,
                       f"focus_selection.trigger_typesに未定義の値があります: {', '.join(invalid)}")
    return _check(name, True)


def _focus_selection_not_conflated_with_anomaly_conclusion_check(response: dict) -> dict:
    """Focus Selection（2026-09-06追加）: trigger_types=media_or_source_anomaly／
    model_or_norm_mismatchを使う場合、discriminating_observation/why_nowが「これが真実だ」
    という断定調の記述になっていないかをヒューリスティックに検出する（Anomalyはtruthではなく
    investigation triggerとして扱う、という設計原則の逸脱検出。advisory、non-repair対象、
    誤検知を避けるため強い断定語のみを対象とする）。"""
    name = "focus_selection_anomaly_not_treated_as_conclusion"
    fs = (response.get("key_point_assessment") or {}).get("focus_selection") or {}
    trigger_types = set(fs.get("trigger_types") or [])
    if not (trigger_types & {"media_or_source_anomaly", "model_or_norm_mismatch"}):
        return _check(name, True)
    text = f"{fs.get('why_now') or ''} {fs.get('discriminating_observation') or ''}"
    hit = next((m for m in _ANOMALY_CONCLUSIVE_LANGUAGE_MARKERS if m in text), None)
    if hit:
        return _check(name, False,
                       f"focus_selectionのanomaly系triggerで断定的表現「{hit}」が含まれています"
                       "（Anomalyは調査すべき手がかりであり、それ自体を結論にしないこと）")
    return _check(name, True)


# Phase R1（Chunk6後P0-2）: Temporal Consistency Guard。実データレビューで、
# 「Trump再登場の可能性」（現職であるにもかかわらず就任前提の投機的表現を使う）や
# 「as_of 2024-06頃の公開情報に基づけば」（内部フィールド名らしき文字列が自由文へ
# 混入し、しかも今回のリクエスト時点と食い違う古い日付を挙げる）といった、Reasoning
# の前提時点がズレている表現が実際に確認された。根本原因はExternal Research由来の
# Documentのpublished_atが常にNone（Web検索ツールが構造化日付を返さないため）で
# temporal_status=unknownとなり、LLMが「いつの情報か分からない」を自身の学習データ
# 時点の一般知識で埋めてしまうことだと考えられる。本Guardは「いつ現職か」を判定する
# 権威あるデータを持たないため、正誤を決定的に判定するのではなく、時点混乱の可能性が
# 高い表現パターン（advisory、非repair対象）を検出し人手確認を促すに留める。
_TEMPORAL_HEDGE_PATTERNS = [
    re.compile(r"再登場"), re.compile(r"再任の可能性"), re.compile(r"復帰の可能性"),
    re.compile(r"就任予定"), re.compile(r"就任すれば"), re.compile(r"as_of", re.IGNORECASE),
    re.compile(r"incoming administration", re.IGNORECASE),
    re.compile(r"return(?:s|ed)? to office", re.IGNORECASE),
    re.compile(r"president-elect", re.IGNORECASE),
    re.compile(r"if\s+\S+\s+(?:wins|returns|takes office)", re.IGNORECASE),
]


def _temporal_hedge_language_check(response: dict) -> dict:
    """Phase R1 Chunk6後P0-2（advisory）: primary_key_point/candidate_key_points/
    main_scenario.description/one_line_assessmentに、時点混乱の可能性が高い表現
    （再登場・就任予定・as_of・incoming administration等）が無いかを検出する。"""
    name = "temporal_hedge_language_not_present"
    fields = []
    kpa = response.get("key_point_assessment") or {}
    if kpa.get("primary_key_point"):
        fields.append(("key_point_assessment.primary_key_point", kpa["primary_key_point"]))
    for i, c in enumerate(kpa.get("candidate_key_points") or []):
        if c.get("key_point"):
            fields.append((f"key_point_assessment.candidate_key_points[{i}].key_point", c["key_point"]))
    main = response.get("main_scenario") or {}
    if main.get("description"):
        fields.append(("main_scenario.description", main["description"]))
    if response.get("one_line_assessment"):
        fields.append(("one_line_assessment", response["one_line_assessment"]))

    hits = []
    for field_name, text in fields:
        for pattern in _TEMPORAL_HEDGE_PATTERNS:
            if pattern.search(text):
                hits.append(f"{field_name}に時点混乱の可能性がある表現（/{pattern.pattern}/）")
    if hits:
        return _check(name, False, "; ".join(hits))
    return _check(name, True)


# Phase R1 Human Check指摘（P1.2、2026-09-03）: 新規5ケースの人手評価で、Key Point
# Discovery自体の質は高い一方、2つの独立した接地不足パターンが実データで発見された。
# いずれも大改修ではなく既存ロジックの延長（決定的ヒューリスティック、advisory・
# 非repair対象）として追加する。

# パターンA（Case2サウジ・米・イスラエル/Case3インド・ロシア原油で発見）: Primary Key
# Pointのcontrol_structure_summary/hinge_structureが「米議会」「保険会社」「銀行」
# 「規制機関」等、局面を直接動かす制度的主体を明記しているにもかかわらず、それらが
# key_actor_profilesに一件も現れない——Key Point Discoveryの質は高いのにActor
# Discoveryが追従していない状態。厳密な意味理解は行わず、機関種別を示す接尾語を含む
# 語がcontrol_structure_summary側に現れるがactor側に一切現れない場合のみを検出する
# （偽陽性を抑えるため、一般的すぎる語（政府・軍等、既存Actor名にも頻出する語）は
# そのままでは誤検出になりにくいが、稀な機関種別のみを対象とする決定的な照合）。
_CONTROLLER_INSTITUTION_SUFFIXES = (
    "議会", "委員会", "裁判所", "保険会社", "銀行", "財務省", "国防総省", "商務省",
    "規制機関", "規制当局", "当局", "大統領府", "中央銀行", "艦隊", "海警局",
)
_CONTROLLER_INSTITUTION_PATTERN = re.compile(
    "[一-龥々A-Za-z]{0,6}(?:" + "|".join(_CONTROLLER_INSTITUTION_SUFFIXES) + ")"
)


def _split_controller_term(term: str) -> tuple:
    """termを(prefix修飾語, institution-type接尾語)に分解する。例:
    "台湾当局"→("台湾","当局")、"米議会"→("米","議会")、"議会"→("","議会")。
    _CONTROLLER_INSTITUTION_PATTERNの正規表現マッチ全体がtermなので、末尾に一致する
    接尾語を後方から特定するだけで済む（複数接尾語が部分文字列関係にある場合は
    最長一致を優先する。例："規制当局"のtermに対し"当局"ではなく"規制当局"を接尾語
    とする）。"""
    for suffix in sorted(_CONTROLLER_INSTITUTION_SUFFIXES, key=len, reverse=True):
        if term.endswith(suffix):
            return term[: -len(suffix)], suffix
    return term, ""


def _controller_term_matches_identity(term: str, own_identity: str) -> bool:
    """Phase 6D-1(ユーザー指示、Controller Guard False Negative修正): 完全な部分文字列
    一致だけでなく、「同一施設・制度を指す別粒度・別表記」（accepted alias / equivalent
    representation）も許容する。ただし単純なsubstring方式へ後退させない——判定は
    必ず (a)institution-type接尾語（議会/当局/委員会等、種別そのもの）の一致、かつ
    (b)修飾語(prefix)が存在する場合はそれも一致、の両方を要求する。この2条件により、
    "台湾当局"(control_structure_summary側の要約表現)が"台湾の貿易・産業当局（経済部
    国際貿易署など）"(Actor自身のより具体的なlabel)を正しくカバーする一方、"government"
    のような一般名詞が特定の制度名（Congress等）の代わりになることは引き続き防ぐ
    （一般名詞にはinstitution-type接尾語自体が含まれないため、(a)で必ず不一致になる）。"""
    if term in own_identity:
        return True
    prefix, suffix = _split_controller_term(term)
    if not suffix:
        return False
    if suffix not in own_identity:
        return False
    return not prefix or prefix in own_identity


def _controller_actor_has_causal_role(profile: dict) -> bool:
    """Phase 6C-0(Quality Guard Integrity Audit、ユーザー指示): 名前が一致するだけでなく、
    そのActorがHypothesis/Key Point/局面メカニズムの中で実際に役割を持つことを、
    構造化フィールドの非空判定のみで決定的に確認する（free-text本文の量・有無は見ない）。
    いずれか1つでも実質的な内容を持てばTrueとする。"""
    return bool(
        profile.get("event_role")
        or profile.get("driving_forces")
        or profile.get("ability_to_move")
        or profile.get("ability_to_block")
        or profile.get("available_leverage")
        or profile.get("red_line")
    )


def _key_point_controllers_covered_check(response: dict) -> dict:
    """パターンA: Primary Key Pointの制御構造（control_structure_summary/hinge_
    structure.primary_hinge）が名指しした制度的主体が、key_actor_profilesに一件も
    現れない場合を検出する（advisory）。

    2026-09-19修正（Phase 6C-0、ユーザー指示、Quality Guard Integrity Audit）: 以前は
    全Actorのactor/formal_role/why_actor_matters文字列を1本に連結し、その連結テキスト
    全体に対する単純な部分文字列一致で判定していた。この実装は、ある1名のActorの
    why_actor_matters（自由記述）に用語が無関係な文脈で出現しただけでもPASSしてしまう
    （実データ監査で確認済み: US-China半導体CaseでTrumpのwhy_actor_matters中の「議会・
    官僚・同盟国の意向を調整しつつ」という一文により、Congress自体はKey Actorとして
    一切モデル化されていないにもかかわらずPASSしていた）。用語一致を対象Actor自身の
    識別フィールド（actor/formal_role）に限定し、構造化フィールドでの因果的役割も
    要求するよう修正した。

    2026-09-19追加修正（Phase 6D-1、ユーザー指示、Controller Guard False Negative
    修正）: 上記6C-0修正は偽陽性を除去した一方、TSMC Caseで新たな偽陰性を生んだ
    （control_structure_summary側の要約表現「台湾当局」が、Actor自身のより具体的な
    label「台湾の貿易・産業当局（経済部国際貿易署など）」の部分文字列にならない
    ——実際にはdriving_forces/event_role/ability_to_move/ability_to_block/
    available_leverage/red_line全てが充実した、実質的にモデル化済みのActorだった）。
    目的はsubstring matchへ戻すことではなく、「同一制度を異なる粒度・表記で正しく
    モデル化している場合は表記差だけでFAILにしない」こと。そのため:
    (1) canonical判定は完全一致（従来通り）または_controller_term_matches_identityに
        よるalias/粒度差許容一致（institution-type接尾語＋修飾語prefixの一致、
        generic名詞では接尾語自体が無いため一致しない——「政府」が「議会」の代わりに
        ならない、というFixture Dの要請は引き続き満たす）。
    (2) canonical entity_id経路（controlling_entity_ids）はcoverage根拠として
        **採用しない**——実装を試みたが実データ検証で却下した。理由: US-China半導体
        Caseはcontrol_structure_summaryで「議会」「BIS」の2制度を名指ししながら
        controlling_entity_idsはBIS 1件のみ（hinge_structure.primary_hingeの
        操作的主体のみを指す設計）。「未解決termが1件・controlling_entity_idsも
        1件」という数の一致だけで対応付けると見かけ上1:1に見えるが、実際にはその
        1件はBISのIDであり無関係な「議会」の欠落を誤ってPASSにしてしまう
        （6C-0で除去した偽陽性の再発）。schemaにterm単位でのID対応付けが存在
        しない以上、数の一致は意味的対応を保証しない——安全側に倒し、自由文照合
        （exact + alias）のみをcoverage根拠とする。
    (3) それでも見つからない場合はFAILとする。この場合でも、対応するcanonical
        entityがDB自体に存在しない（Data Readiness Limitation）のか、存在するのに
        Reasoningが取りこぼした（true Reasoning Weakness）のかは、本Guard単体では
        判別できない（DBアクセスを持たない決定的関数のため）。この判別は既存の
        診断プロセス（Phase 4/5,6Cで実施してきたのと同様、失敗詳細をもとにした
        Case別のEntity実在確認）に委ねる——detailに用語自体を明記するのはその
        ためである。"""
    name = "key_point_controllers_covered"
    kpa = response.get("key_point_assessment") or {}
    text = " ".join(filter(None, [
        kpa.get("control_structure_summary"),
        (kpa.get("hinge_structure") or {}).get("primary_hinge"),
    ]))
    if not text:
        return _check(name, True)
    terms = sorted(set(_CONTROLLER_INSTITUTION_PATTERN.findall(text)))
    if not terms:
        return _check(name, True)
    profiles = response.get("key_actor_profiles") or []
    modeled_terms = set()
    for p in profiles:
        own_identity = " ".join(filter(None, [p.get("actor"), p.get("formal_role")]))
        if not own_identity:
            continue
        for t in terms:
            if _controller_term_matches_identity(t, own_identity) and _controller_actor_has_causal_role(p):
                modeled_terms.add(t)
    missing = [t for t in terms if t not in modeled_terms]
    if missing:
        return _check(name, False,
                       "Primary Key Pointの制御構造が名指しした主体が、独立したKey Actor"
                       "として因果的役割（driving_forces/event_role/ability_to_move等）"
                       f"込みでモデル化されていません: {', '.join(missing)}"
                       "（canonical entityがData Readiness上そもそも存在しないのか、"
                       "存在するのにReasoningが取りこぼしたのかは本Guard単体では判別"
                       "不可——別途Case別のEntity実在確認が必要）")
    return _check(name, True)


# パターンB（Case4グリーンランドで発見）: main_scenario.evidence_class=model_general_
# knowledgeのままMain Scenarioが構築されるケースで、confidence自体は既存
# cap_confidence_by_evidence_classで正しく低へ引き下げられているが、その事実が
# confidence値の中に埋没し見落とされやすい。独立したadvisory信号として可視化する
# （新しいResearchループを追加する大改修ではなく、既存の決定的処理が既に持っている
# evidence_class判定結果を、別チェックとして表面化するだけの軽量な追加）。
_GENERAL_KNOWLEDGE_ONLY_EVIDENCE_CLASSES = {"model_general_knowledge", "unsupported_assumption"}


def _primary_key_point_evidence_coverage_check(response: dict) -> dict:
    """パターンB: key_point_assessment/main_scenarioのevidence_classがmodel_general_
    knowledge/unsupported_assumptionにとどまっている場合を検出する（advisory）。"""
    name = "primary_key_point_evidence_coverage"
    kpa = response.get("key_point_assessment") or {}
    main = response.get("main_scenario") or {}
    weak = []
    if kpa.get("evidence_class") in _GENERAL_KNOWLEDGE_ONLY_EVIDENCE_CLASSES:
        weak.append(f"key_point_assessment.evidence_class={kpa.get('evidence_class')}")
    if main.get("evidence_class") in _GENERAL_KNOWLEDGE_ONLY_EVIDENCE_CLASSES:
        weak.append(f"main_scenario.evidence_class={main.get('evidence_class')}")
    if weak:
        return _check(name, False,
                       "Primary Key Point/Main Scenarioの根拠がmodel_general_knowledge/"
                       "unsupported_assumptionにとどまっています（Research不足の可能性）: "
                       + ", ".join(weak))
    return _check(name, True)


_KEY_POINT_STRUCTURAL_CONTROL_TYPES = {"institutionally_constrained", "structural"}


def _key_point_control_type_consistency_check(response: dict) -> dict:
    """Session2 Reasoning Upgrade: control_typeとcontrolling_entity_ids/
    control_structure_summaryの整合性契約を検証する（非repair対象）。
    candidate_key_points[]の各候補にも同じ契約を適用する。"""
    name = "key_point_control_type_consistency"
    kpa = response.get("key_point_assessment") or {}

    def _violation(control_type, controlling_ids, summary, label: str) -> str:
        if control_type == "actor_controlled" and not controlling_ids:
            return f"{label}: control_type=actor_controlledですがcontrolling_entity_idsが空です"
        if control_type == "distributed" and len(controlling_ids or []) < 2 and not summary:
            return (f"{label}: control_type=distributedですがcontrolling_entity_idsが2件未満かつ"
                     "control_structure_summaryもありません")
        if control_type == "mixed" and controlling_ids and not summary:
            return f"{label}: control_type=mixedでcontrolling_entity_idsがある場合はcontrol_structure_summaryが必須です"
        if control_type in _KEY_POINT_STRUCTURAL_CONTROL_TYPES and not summary:
            return f"{label}: control_type={control_type}ですがcontrol_structure_summaryがありません"
        return ""

    issue = _violation(kpa.get("control_type"), kpa.get("controlling_entity_ids"),
                        kpa.get("control_structure_summary"), "key_point_assessment")
    if issue:
        return _check(name, False, issue)
    for i, cand in enumerate(kpa.get("candidate_key_points") or []):
        issue = _violation(cand.get("control_type"), cand.get("controlling_entity_ids"),
                            cand.get("control_structure_summary"), f"candidate_key_points[{i}]")
        if issue:
            return _check(name, False, issue)
    return _check(name, True)


def _key_point_actor_refs_grounded_check(response: dict, *, stage1_visible_entity_ids: set | None) -> dict:
    """Session2 Reasoning Upgrade: key_point_assessment.controlling_entity_idsが、実際に
    Stage1 Promptへ提示したEntity集合(stage1_visible_entity_ids)の部分集合であることを
    検証する（known_entity_idsではなくこちらを使う。DBには存在するがLLMに見せていない
    UUIDの捏造・偶然一致を検出するため）。stage1_visible_entity_ids未指定時はスキップする
    （既存呼び出し元との後方互換）。非repair対象。"""
    name = "key_point_actor_refs_grounded"
    if stage1_visible_entity_ids is None:
        return _check(name, True)
    kpa = response.get("key_point_assessment") or {}
    controlling_ids = kpa.get("controlling_entity_ids") or []
    fabricated = sorted(i for i in controlling_ids if i and i not in stage1_visible_entity_ids)
    if fabricated:
        return _check(name, False,
                       "key_point_assessment.controlling_entity_idsにStage1へ提示していないentity_idが"
                       f"あります（創作の可能性、要人手確認）: {', '.join(fabricated)}")
    return _check(name, True)


_MODE_V12_CHECKS = {
    "theme_design": _key_stakeholder_v12_check,
    "case_analysis": _key_stakeholder_v12_check,
    "research_design": _research_design_v12_check,
    "draft_review": _draft_review_root_premise_check,
    "outline_review": _draft_review_root_premise_check,
    "executive_brief": _executive_brief_v12_check,
}

# geo_expert_engine.run_expert_requestが「修復生成が必要か」を判定するために参照する、
# モード別v1.2必須項目チェックの名前一覧（このチェックのみが失敗時に最大1回の修復生成対象となる。
# evidence_ids_not_fabricated等の他チェックの失敗では修復生成を行わない）
MODE_V12_CHECK_NAMES = frozenset({
    "key_stakeholder_v12_fields_present", "research_design_v12_fields_present",
    "draft_review_root_premise_check", "executive_brief_v12_fields_present",
    # Phase D-4a(Holistic Reasoning Architecture Review): D-1で追加した3つの接続性
    # Guard（参照整合性のみ、意味評価・non-null必須化・chain全体のcompleteness判定は
    # 行わない）をRepair対象へ昇格する。derived_from_hypothesis_id/target参照の
    # 必須化・chain全体のcompleteness判定はD-3完了後のD-4bで扱う。
    "event_role_intended_target_grounded",
    "actor_analyst_expected_response_target_grounded",
    "scenario_derived_from_hypothesis_grounded",
    # Phase D-4b: defer_scenario=Trueかつ通常生成時、main_scenario.derived_from_
    # hypothesis_idがretainedな仮説を参照することを必須とする（絶対条件4の強化。
    # budget fallback・defer_scenario=Falseは明示例外でNOT_APPLICABLE扱いのため
    # 誤ってrepairを起動しない）。human_interaction_chain_completeness/
    # control_point_decision_maker_resolutionは、Evidence不足・structure-firstの
    # 正当な状態(PARTIAL_EVIDENCE_GAP/NOT_APPLICABLE)まで含めてrepairしてしまわない
    # よう、意図的にMODE_V12_CHECK_NAMESへは加えない（advisoryのまま）。
    "scenario_derived_from_retained_hypothesis",
    # Phase F改訂(Human Lens / Quality Signal、ユーザー指示): 当初human_interaction_
    # gate_present/human_interaction_status_consistency/hypothesis_interaction_basis_
    # groundedをRepair対象としていたが、目的が「Human Interactionを正しく埋められたか」
    # という推論の正しさの強制に寄りすぎており、本来の目的（不確実な事実から最も説明力・
    # 意思決定関連性のある読み筋を作ること）とズレると判断し、Repairから外して全て
    # advisoryへ格下げした（Human Interactionは推奨レンズであり必須Gateではない。
    # 詳細はdocs/expert-reference-benchmark/human_interaction_gate_design.md §改訂参照）。
})

# 段階実行(Staged Execution) Phase A: 各v1.2必須チェックが検査するフィールドが
# Stage1所有かStage2モジュール所有かのマッピング。部分修復（geo_expert_engine.
# run_expert_request_staged）が「どちらのSchemaだけを対象に再生成するか」を
# 決めるために使う（フルSchemaを再生成しないため）。
MODE_V12_CHECK_STAGE = {
    "key_stakeholder_v12_fields_present": "stage1",       # key_stakeholder_assessment/main_scenario/key_actor_profiles/identified_knowledge_gaps
    "research_design_v12_fields_present": "stage1",       # key_stakeholder_assessment/additional_research/identified_knowledge_gaps
    "executive_brief_v12_fields_present": "stage1",       # key_stakeholder_assessment/executive_summary/business_impact
    "draft_review_root_premise_check": "stage2",          # root_premise_check/review_findings（reviewモジュール所有）
    # Phase D-4a/D-3: event_role.intended_target_actor_ref/key_actor_profiles[].actor_refは
    # いずれもStage1（Call1）所有のためstage1。derived_from_hypothesis_id
    # （main_scenario/alternative_scenarios）が参照するhypothesis_id自体はStage2
    # （situation_actor_assessmentモジュール）所有だが、修復対象は参照元のmain_scenario/
    # alternative_scenariosであるため、静的既定値はstage1のまま維持する——ただしPhase D-3
    # でdefer_scenario=Trueの局面（main_scenario自体がCall2/Scenario所有になる）では、
    # geo_expert_engine.run_expert_request_staged側でこの1件のみ動的にstage2へ上書きする
    # （MODE_V12_CHECK_STAGEはprofile/mode文脈を持たない静的マッピングのため、ここでは
    # defer_scenario=False、すなわちmain_scenarioがStage1所有のままの経路を既定とする）。
    # target_actor_ref（situation_actor_assessment）はPhase D-3により、発火する局面
    # （deep×situation_actor_assessment発火時）では常にCall1（stage1役割）側で生成される
    # よう再配線されたため、stage1が正しい既定値になった。
    "event_role_intended_target_grounded": "stage1",
    "scenario_derived_from_hypothesis_grounded": "stage1",
    "actor_analyst_expected_response_target_grounded": "stage1",
    # Phase D-4b: main_scenarioの所有stageと同じ理由（defer_scenario=Trueの局面のみ
    # geo_expert_engine側でstage2へ動的上書き、静的既定値はstage1）。
    "scenario_derived_from_retained_hypothesis": "stage1",
}

# Phase R1（Chunk6後P1.1、Guard再設計）: stakeholders_grounded_in_source_packは
# 「Source Pack本文にActor名の文字列が無ければ創作扱い」という設計だったが、実データ検証で
# 「日本政府」「中国政府」「TSMC」等、クエリの主題から自明に推論される正当なActorまで
# 誤検知し続けることが判明した（grounding sourceを広げても・括弧書きの言い換えを吸収しても
# 解消しなかった）。根本原因は設計思想そのもの——専門家分析ではむしろ「入力に明示されて
# いない重要Actorを局面構造から発見すること」自体が価値であり、Stakeholder Discovery ≠
# Evidence Groundingという前提の欠落だった。そのため単一チェックを廃止し、独立した3つの
# 問い（Actor Legitimacy / Issue Relevance / Claim Grounding）に分解する:
#   A. stakeholder_entity_resolved: 主体が安定したactor_refを持ち、entity_id指定時は
#      それが捏造されていないか（Actorの実在・妥当性——緩く見る）
#   B. stakeholder_relevance_explained: なぜこの局面のStakeholderなのかが説明されているか
#      （why_actor_matters等——Actor自体がSource Pack文字列に無くてもよい）
#   C. Actorについての具体的な主張（persistent_preference等）へのEvidence Discipline
#      は、既存のcap_confidence_by_general_knowledge_flag/cap_confidence_by_evidence_
#      class/unsupported_actor_assumption_not_promoted/actor_pattern_applicability_
#      assessment等が既に担っているため、新規チェックは追加しない（重複を避ける）。
_STAKEHOLDER_RELEVANCE_FIELDS = ("why_actor_matters", "ability_to_move", "ability_to_block", "formal_role")


def _stakeholder_entity_resolved_check(response: dict, *, stage1_visible_entity_ids: set | None) -> dict:
    """A. Actor Legitimacy: 各key_actor_profilesが安定したactor_refを持つこと
    （他フィールド——audience_analysis/key_stakeholder_assessment.selected_actor_ids/
    supporting_knowledge_refs等——から参照可能な識別子であることの最低要件）、
    entity_id指定時はそれがStage1へ実際に提示したEntity集合の一部であること
    （捏造検出）を検証する。Source Pack本文への文字列一致は要求しない。"""
    name = "stakeholder_entity_resolved"
    profiles = response.get("key_actor_profiles") or []
    issues = []
    for kap in profiles:
        actor_label = kap.get("actor") or kap.get("actor_ref") or "?"
        if not kap.get("actor_ref"):
            issues.append(f"{actor_label}: actor_refが空です")
            continue
        entity_id = kap.get("entity_id")
        if entity_id and stage1_visible_entity_ids is not None and entity_id not in stage1_visible_entity_ids:
            issues.append(f"{actor_label}: entity_id={entity_id}がStage1へ提示していないEntityです（捏造の可能性）")
    if issues:
        return _check(name, False, "; ".join(issues))
    return _check(name, True)


def _stakeholder_relevance_explained_check(response: dict) -> dict:
    """B. Issue Relevance: 各key_actor_profilesについて、なぜこの局面のStakeholderなのか
    （why_actor_matters/ability_to_move/ability_to_block/formal_roleのいずれか）が
    説明されていることを検証する。query_entailed/relationship_expansion由来の
    （＝Source Pack本文に文字列としては現れない）発見的Actorも、関連性さえ説明できれば
    正当として扱う——ActorをSourceから機械的に発見することではなく、選定理由を
    示せることを求める。"""
    name = "stakeholder_relevance_explained"
    profiles = response.get("key_actor_profiles") or []
    missing = [kap.get("actor") or kap.get("actor_ref") or "?"
               for kap in profiles
               if not any(kap.get(f) for f in _STAKEHOLDER_RELEVANCE_FIELDS)]
    if missing:
        return _check(name, False,
                       "局面との関連性（why_actor_matters等）が説明されていない主体があります: "
                       + ", ".join(missing))
    return _check(name, True)


def run_quality_guard(response: dict, source_pack_text: str, versions: dict, *,
                       case_context_text: str = "", known_ids: set | None = None,
                       mode: str | None = None,
                       stage1_visible_entity_ids: set | None = None,
                       derived_intelligence_ids: set | None = None,
                       defer_scenario: bool = False,
                       scenario_fallback_used: bool = False) -> dict:
    """response: geo_expert_ai_response.schema.json準拠のdict
    source_pack_text: Source Packの本文（Phase R1 Chunk6後P1.1のGuard再設計で、唯一の
        利用者だったstakeholder groundingの文字列一致チェックを廃止したため、現時点では
        どのチェックからも参照されていない。呼び出し元との互換のため引数自体は残す）
    versions: {"prompt_version":..., "knowledge_version":..., "model":..., "source_pack_id":...}
    case_context_text: 同上の理由で現時点では未使用（呼び出し元との互換のため引数は残す）
    known_ids: v1.2 Evidence ID捏造防止チェック用の既知ID集合（source_pack/case_context由来）。
        Noneの場合はチェック自体をスキップする（既存呼び出し元との後方互換のため）
    mode: v1.2 モード別必須項目チェックの対象モード。Noneまたは対象外モードの場合は追加しない
    stage1_visible_entity_ids: Session2 Reasoning Upgrade。実際にStage1 Promptへ提示した
        entity_id集合（Foundation Pack一般Entity一覧 ∪ Actor Intelligence Pack）。
        key_point_assessment.controlling_entity_idsの捏造検出に使う。Noneの場合は
        当該チェックをスキップする（既存呼び出し元との後方互換のため）
    defer_scenario: Phase D-3。main_scenario生成をHuman Strategic Interaction/Competing
        Hypothesisの後段（Call2）へ遅延させた局面かどうか。scenario_derived_from_
        retained_hypothesisの判定に使う（Noneではなくbool、既定False=既存呼び出し元との
        後方互換）。
    scenario_fallback_used: Phase D-3。Call2（Scenario）がbudget枯渇・生成失敗により
        決定的フォールバック（_SCENARIO_FALLBACK_STUB）で埋められた場合True。
        scenario_derived_from_retained_hypothesisの明示例外に使う。
    """
    checks = []

    claims = response.get("claims") or []
    missing_claim_type = [c for c in claims if not c.get("claim_type")]
    checks.append(_check(
        "claims_have_claim_type",
        not missing_claim_type,
        "claim_typeが空の主張があります" if missing_claim_type else "",
    ))

    evidence_claims = [c for c in claims if c.get("claim_type") in EVIDENCE_REQUIRED_CLAIM_TYPES]
    missing_source_ref = [c for c in evidence_claims if not c.get("source_ref")]
    checks.append(_check(
        "fact_claims_have_source_ref",
        not missing_source_ref,
        "Fact/Official Claim/Observationに根拠参照(source_ref)が無い主張があります"
        if missing_source_ref else "",
    ))

    main_scenario = response.get("main_scenario") or {}
    checks.append(_check(
        "main_scenario_has_supporting_evidence",
        bool(main_scenario.get("supporting_evidence")),
        "主シナリオに支持根拠がありません" if not main_scenario.get("supporting_evidence") else "",
    ))

    has_counterargument = bool((response.get("strongest_counterargument") or "").strip())
    checks.append(_check(
        "has_strongest_counterargument", has_counterargument,
        "" if has_counterargument else "最も強い反論が空です",
    ))

    has_retraction = bool(response.get("retraction_conditions"))
    checks.append(_check(
        "has_retraction_conditions", has_retraction,
        "" if has_retraction else "撤回条件がありません",
    ))

    checks.append(_stakeholder_entity_resolved_check(
        response, stage1_visible_entity_ids=stage1_visible_entity_ids))
    checks.append(_stakeholder_relevance_explained_check(response))

    business_impact = response.get("business_impact") or {}
    has_impact_path = any(business_impact.get(k) for k in ("direct", "indirect", "environment_change"))
    has_unrelated_note = bool(business_impact.get("unrelated_note"))
    checks.append(_check(
        "business_impact_path_or_unrelated_noted",
        has_impact_path or has_unrelated_note,
        "" if (has_impact_path or has_unrelated_note) else "自社影響の経路も、関連性が無い旨の説明もありません",
    ))

    forced_severity = has_unrelated_note and bool(business_impact.get("direct"))
    checks.append(_check(
        "no_forced_severity_when_unrelated",
        not forced_severity,
        "関連性が弱いとしつつ直接影響を主張しており矛盾しています" if forced_severity else "",
    ))

    versions_ok = all(versions.get(k) for k in ("prompt_version", "knowledge_version", "model", "source_pack_id"))
    checks.append(_check(
        "versions_recorded", versions_ok,
        "" if versions_ok else "Prompt/Knowledge/Model/Source Pack版のいずれかが未記録です",
    ))

    if known_ids is not None:
        used_ids = _collect_evidence_ids(response)
        fabricated = sorted(i for i in used_ids if i not in known_ids)
        checks.append(_check(
            "evidence_ids_not_fabricated",
            not fabricated,
            "Source Pack/Case Context/Foundation Packに存在しないEvidence/Claim IDが使われています"
            f"（創作の可能性、要人手確認）: {', '.join(fabricated)}" if fabricated else "",
        ))

    mode_check_fn = _MODE_V12_CHECKS.get(mode or "")
    if mode_check_fn is not None:
        checks.append(mode_check_fn(response))

    checks.append(_key_point_evidence_grounded_check(response))
    checks.append(_claim_evidence_semantic_relevance_present_check(response))
    checks.append(_direct_evidence_present_for_primary_key_point_check(response))
    checks.append(_supporting_knowledge_refs_grounded_check(response))
    checks.append(_key_point_candidates_not_homogeneous_check(response))
    checks.append(_key_point_selection_not_unsupported_novelty_check(response))
    checks.append(_focus_selection_why_now_present_check(response))
    checks.append(_focus_selection_trigger_types_within_enum_check(response))
    checks.append(_focus_selection_not_conflated_with_anomaly_conclusion_check(response))
    checks.append(_temporal_hedge_language_check(response))
    checks.append(_key_point_controllers_covered_check(response))
    checks.append(_control_point_decision_maker_resolution_check(response))
    checks.append(_primary_key_point_evidence_coverage_check(response))
    checks.append(_key_point_control_type_consistency_check(response))
    checks.append(_key_point_actor_refs_grounded_check(
        response, stage1_visible_entity_ids=stage1_visible_entity_ids))
    checks.append(_situation_actor_assessment_grounded_check(response))
    checks.append(_situation_actor_assessment_salience_confidence_consistency_check(response))
    checks.append(_situation_actor_assessment_evidence_grounded_check(response))
    checks.append(_directional_perceptions_grounded_check(response))
    checks.append(_directional_perception_not_self_referential_check(response))
    checks.append(_event_role_intended_target_grounded_check(response))
    checks.append(_actor_analyst_expected_response_target_grounded_check(response))
    checks.append(_scenario_derived_from_hypothesis_grounded_check(response))
    checks.append(_human_interaction_chain_completeness_check(response))
    checks.append(_scenario_derived_from_retained_hypothesis_check(
        response, defer_scenario=defer_scenario, scenario_fallback_used=scenario_fallback_used))
    checks.append(_human_interaction_gate_present_check(response))
    checks.append(_human_interaction_status_consistency_check(response))
    checks.append(_hypothesis_interaction_basis_grounded_check(response))
    checks.append(_hypothesis_interaction_basis_missing_check(response))
    checks.append(_media_observed_fields_free_of_inference_language(response))
    checks.append(_media_editorial_hypotheses_not_uniformly_confirmatory(response))
    checks.append(_media_beneficiary_not_treated_as_intent_proof(response))
    checks.append(_media_hypothesis_evidence_grounded_check(response))
    checks.append(_anonymous_sourced_hypothesis_confidence_capped(response))
    checks.append(_media_omitted_actor_analysis_grounded(
        response, stage1_visible_entity_ids=stage1_visible_entity_ids))
    checks.append(_investigation_question_answer_grounded(response))
    checks.append(_historical_pattern_not_asserted_as_certain_fact(response))
    checks.append(_cross_intelligence_contradiction_not_silently_dropped(response))
    checks.append(_evidence_attribution_not_conflated(response, derived_intelligence_ids=derived_intelligence_ids))
    checks.append(_actor_intelligence_provenance_present(response))
    checks.append(_actor_pattern_applicability_present(response))
    checks.append(_unsupported_actor_assumption_not_promoted(response))
    checks.append(_weak_analogy_not_used_as_strong_evidence(response))
    checks.append(_evidence_substitution_detected(response))
    checks.append(_hypothesis_pruning_present(response))
    checks.append(_actor_model_challenged(response))

    passed_count = sum(1 for c in checks if c["passed"])
    return {
        "checks": checks,
        "passed_count": passed_count,
        "failed_count": len(checks) - passed_count,
    }
