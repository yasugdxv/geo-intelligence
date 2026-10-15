"""Geopolitical Intelligence OS: Situation Analysis Presentation v1（「局面を読む」表示レイヤー）。

既存Expert AI構造化出力（schemas/geo_expert_ai_response.schema.json）とAnalysis Design
（geo_ios_analysis_designs）を、人間が読みやすい7セクションへ整形するだけの純粋な
Presentation層。新しいLLM呼び出し・新しい推論は一切行わない——既存structured output内の
既存テキスト（selection_rationale/why_actor_matters等）をそのまま使うか、軽い接続語で
並べるだけで、因果関係やReframingをこの層で新規生成することはしない。

CQAS candidate生成・ranking・actor選定・retrieval・evidence weighting・absence detection・
Expert AI core prompt・Learning・Phase I-B1 evaluation baselineには一切触れない（絶対条件、
呼び出すのは既存service/response_jsonの読み取りのみ）。

raw_analysis（元データそのもの）とpresentation（表示用に整形した結果）を概念的に分離する
（§13）。本モジュールはpresentationのみを生成し、raw_analysis自体（Design/Candidate/
Expert Response）を書き換えることは一切ない——引数のdict/listも変更しない（読み取り専用）。

各presentation itemには、どのfieldから来たかを示す_source（例:
"response.key_point_assessment.primary_key_point"）を付与する（§14 Debug Traceability）。
UI側では既定でこのfieldを表示しない——strip_debug_sources()で除去してから返す
（geo_ios_situation_presentation_api.py側、debug=falseの場合）。

5つの読み方案UI再設計（2026-10-01、ユーザー指示）: 各interpretation/reading_exposure
カードへ`actor_roles`（build_actor_roles、このReadingの主要主体/周辺主体を既存
interaction_basis/event_roleから機械的に分類。新しい推論はしない、一意に決められない
場合はunclassified）と`situation_phases`（build_situation_phases、既存trajectory_
assessment/key_actor_profiles/situation_actor_assessmentを「きっかけ→観測された
行動→相手の反応→現在」へ再配置するだけ、対応fieldが無い段階は省略）を追加した。
全体には`media_comparison`（build_media_comparison、既存media_source_contextの
observed/hypothesesを媒体単位の比較表へ展開するだけ）を追加した。Evidence item
への`date`補完（enrich_evidence_dates）だけは実DB参照が必要なため、本モジュールの
他関数とは別にclientを取る——呼び出し元（API層）がbuild後に後処理として呼ぶ。
"""

import re

PRESENTATION_VERSION = "situation_analysis_v1"


def _item(value, source):
    """値と出典をペアにする。値が空（None/""/[]/{}）ならNoneを返し、呼び出し側で省略する
    （情報が無い項目を無理に埋めない、というユーザー要件を機械的に満たすためのヘルパー）。"""
    if value in (None, "", [], {}):
        return None
    return {"value": value, "_source": source}


def _text_list(values, source):
    if not values:
        return None
    return {"value": list(values), "_source": source}


def _prune(items):
    return [x for x in items if x]


# Reading Presentation Layer v1 UUID漏れ修正(ユーザー指示、目視UIレビューで発見):
# legacy自由記述field（main_scenario.supporting_evidence/weakening_information/
# contradicting_evidence等）に、evidence_id等の内部技術参照がそのまま（単独または文中に
# 混入した状態で）入ってしまうケースがある。これはReasoning側の問題（Prompt変更が必要）
# だが、今回はPresentation表示時のsanitizeのみで対応する（Prompt/Gold responseは
# 変更しない、文章の要約・言い換え・新規生成もしない）。
_UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
_KNOWN_INTERNAL_TECHNICAL_KEYS = {"foundation_pack_asset"}

# Presentation Hygiene拡張(ユーザー指示、Saudi-Israel目視レビューで発見): Evidence
# Relevanceが空のケースで、legacy自由記述fieldに"supporting_knowledge_ref:Entity.
# field_path"のような内部knowledge referenceがそのまま入るケースがある（persistent
# preference/actor profile参照、UUIDでもfoundation_pack_assetでもない別パターンの
# 技術参照）。既存のUUID/internal key除去と同じ「明確な技術参照のみ除去、文章は
# 書き換えない」原則で対応する。
_KNOWLEDGE_REF_PATTERN = re.compile(r"\bsupporting_knowledge_ref:[A-Za-z0-9_.]+", re.IGNORECASE)

# Presentation Hygiene拡張(ユーザー指示、TSMC目視レビューで発見): モデルが自由記述の
# 末尾にJSON構造記号の断片（例: "}]," "]}" "},"）をそのまま漏らすケースがある
# （Raw Reasoning出力自体の問題、Prompt側は変更しない）。除去対象は文字列**末尾**に
# 孤立して連続する構造記号（{}[],）のみ——文中の括弧・通常の句読点・URL・数値・
# Markdown・JSON自体を説明する文章までは対象にしない。
_TRAILING_JSON_FRAGMENT_PATTERN = re.compile(r"[{}\[\],]{2,6}\s*$")

# Presentation Hygiene拡張(ユーザー指示、Trajectory Canary目視/機械チェックで発見、
# 2026-09-22): Foundation Context（Situation Trajectory）の引用記法"[document_id: UUID]"
# "[document: UUID]"を、モデルがActor自由記述等の人間向け文章へそのまま持ち込むケースが
# ある。単独のUUID除去だけでは"（[document_id: ]）"のような空の括弧が残ってしまうため、
# 角括弧の引用構文全体（前後の全角/半角丸括弧を含む）を対象に除去する。
_DOCUMENT_CITATION_BRACKET_PATTERN = re.compile(
    r"[（(]?\s*\[\s*document(?:_id)?\s*:\s*[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\s*\]\s*[）)]?",
    re.IGNORECASE)


def _looks_like_bare_technical_ref(text):
    """要素全体が技術参照（UUID単体・内部key単体・knowledge_ref単体）だけの場合はTrue。"""
    stripped = (text or "").strip()
    if not stripped:
        return True
    if stripped in _KNOWN_INTERNAL_TECHNICAL_KEYS:
        return True
    if _UUID_PATTERN.fullmatch(stripped):
        return True
    # "supporting_knowledge_ref:X.Y(補足)"のように、参照の後ろに丸括弧付き補足が
    # 続くだけの形（実データで確認済み）も、行全体としては技術参照そのものとして扱う
    # （自然文の先頭がsnake_caseの内部field pathで始まることはないため、安全な判定）。
    if _KNOWLEDGE_REF_PATTERN.match(stripped):
        remainder = _KNOWLEDGE_REF_PATTERN.sub("", stripped, count=1).strip()
        if not remainder or (remainder.startswith("(") and remainder.endswith(")")):
            return True
    return False


def _strip_embedded_technical_refs(text):
    """文中に混入したUUID・内部key・末尾の孤立したJSON構造断片・Foundation Context
    引用記法を除去する（文章自体は書き換えない、要約・言い換え・補完はしない）。"""
    cleaned = _DOCUMENT_CITATION_BRACKET_PATTERN.sub("", text)
    cleaned = _UUID_PATTERN.sub("", cleaned)
    for key in _KNOWN_INTERNAL_TECHNICAL_KEYS:
        cleaned = cleaned.replace(key, "")
    cleaned = _KNOWLEDGE_REF_PATTERN.sub("", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    cleaned = _TRAILING_JSON_FRAGMENT_PATTERN.sub("", cleaned).rstrip()
    cleaned = re.sub(r"[、,]\s*$", "", cleaned).strip()
    return cleaned


def _sanitize_legacy_text_items(items):
    """legacy自由記述fieldをreasons/weak_points等の人間向け本文として使う直前に通す。
    要素全体が技術参照だけのものは除外し、文中に混入した技術参照は除去する。"""
    if isinstance(items, str):
        items = [items]
    cleaned_items = []
    for raw_text in items or []:
        if not isinstance(raw_text, str):
            continue
        if _looks_like_bare_technical_ref(raw_text):
            continue
        cleaned = _strip_embedded_technical_refs(raw_text)
        if cleaned:
            cleaned_items.append(cleaned)
    return cleaned_items


# ---- §3 今回のポイント -------------------------------------------------------

def build_key_point(*, design, response_json):
    """既存investigation_question/one_line_assessment/key_point_assessmentから表示するのみ。
    入力（investigation_question）と同じ内容しかない場合も、無理にReframingせずそのまま表示
    する（H4=Investigation Question Reframingの発見をUIで隠さないための絶対条件）。

    Reading Presentation Layer v1(ユーザー指示): initial_anomaly（AIが最初に注目した
    違和感）をSituation Summary直下に置けるよう追加した。既存フィールドの追加表示のみで、
    新しい判定・推論は行わない。"""
    design = design or {}
    kpa = response_json.get("key_point_assessment") or {}
    hinge = kpa.get("hinge_structure") or {}
    return {
        "investigation_question": _item(
            design.get("investigation_question"), "analysis_design.investigation_question"),
        "one_line_assessment": _item(
            response_json.get("one_line_assessment"), "response.one_line_assessment"),
        "initial_anomaly": _item(
            response_json.get("initial_anomaly"), "response.initial_anomaly"),
        "primary_key_point": _item(
            kpa.get("primary_key_point"), "response.key_point_assessment.primary_key_point"),
        "primary_hinge": _item(
            hinge.get("primary_hinge"), "response.key_point_assessment.hinge_structure.primary_hinge"),
        "why_this_is_the_key_point": _item(
            kpa.get("selection_rationale"), "response.key_point_assessment.selection_rationale"),
    }


# ---- §4-5 あり得る見方 / なぜそう考える？ / §8 この先どうなる？ ------------------
#
# Reading Presentation Layer v1(ユーザー指示、Phase 10 Evidence Binding反映): supporting_
# evidence/weakening_evidenceは、Phase 10で新設したID付き構造化field（main_scenario /
# hypothesis_register[].evidence_relevance・weakening_evidence_relevance）をPrimary
# Sourceとする。旧来の自由記述（supporting_evidence/weakening_information/
# contradicting_evidence/strengthening_conditions/weakening_conditions）は、構造化データが
# 空の場合のみfallbackとして使う（reasons/weak_pointsとして従来通り残す。互換性のため）。

def _evidence_summary_lookup(claims):
    """evidence_id(claims[].source_ref) → claim本文の対応表。Evidence Relevanceは
    evidence_idしか持たないため、画面表示用の短い要約文をclaimsから逆引きする
    （新しい要約文は生成しない、既存claim.textをそのまま使う）。

    Post-Freeze Integrity Audit Finding(Phase R3再生成時発見、2026-09-22)対応:
    claims[].textに"[document_id: UUID]"形式の技術参照がそのまま混入するケースが
    見つかった（N1 Takaichi再生成で7件確認、main/alternativeカード双方のsupporting_
    evidence.summaryとして実際にPresentation出力へ到達していた）。build_actors()等と
    同じsanitizerをここで一度だけ適用し、この逆引き表を経由する全ての消費箇所
    （_structured_evidence_items/_evidence_items_from_idsのsummary）へ一括で及ぼす。"""
    lookup = {}
    for c in claims or []:
        ref = c.get("source_ref")
        if ref and ref not in lookup:
            text = c.get("text")
            lookup[ref] = _strip_embedded_technical_refs(text) if text else text
    return lookup


def _structured_evidence_items(entries, evidence_summaries, source, actor=None):
    items = []
    for e in entries or []:
        eid = e.get("evidence_id")
        if not eid:
            continue
        items.append({
            "evidence_id": eid,
            "summary": evidence_summaries.get(eid),
            "relation": e.get("relation"),
            "claim_relevance": e.get("claim_relevance"),
            "source_type": e.get("source_type"),
            "rationale": e.get("rationale"),
            # Reading Report v2（2026-10-02、ユーザー指示）: evidence_relevance自体には
            # actor fieldが無いため、このReading自身の主要主体（interaction_basis.actor_ref
            # 由来、build_actor_rolesと同じ決定論的な値）を帰属として付与するだけ——新しい
            # Actor判定・新しい事実の生成は行わない。解決できない場合はNoneのまま。
            "actor": actor,
            "_source": source,
        })
    return items


def _evidence_items_from_ids(evidence_ids, evidence_summaries, source, actor=None):
    """Post-Freeze Integrity Audit Finding CRITICAL-4/I-1(2026-09-22発見)対応:
    代替解釈(Alternative Reading)カードのsupporting_evidenceが、実データには有効な
    hypothesis_register[].supporting_evidence_idsが存在するにもかかわらず、常に空配列
    で表示されていた問題への対応。supporting_evidence_idsはID配列のみでclaim_relevance/
    rationale等の詳細を持たないため、_structured_evidence_items（evidence_relevance
    形式専用）とは別に、IDのみからでもevidence_id/summaryだけは表示できる最小限の
    adapterを用意する。新しいEvidence Relevance判定・要約生成は行わない——既に
    main_scenario側で確立済みのevidence_summaries逆引きをそのまま使うだけ。"""
    items = []
    for eid in evidence_ids or []:
        if not eid:
            continue
        items.append({
            "evidence_id": eid,
            "summary": evidence_summaries.get(eid),
            "relation": None,
            "claim_relevance": None,
            "source_type": None,
            "rationale": None,
            "actor": actor,
            "_source": source,
        })
    return items


def _hypothesis_for_candidate(hypothesis_register, derived_from_hypothesis_id):
    if not derived_from_hypothesis_id:
        return None
    for h in hypothesis_register or []:
        if h.get("hypothesis_id") == derived_from_hypothesis_id:
            return h
    return None


# 2026-09-23追加、ユーザー指示「気づく→見る→疑う→仮説→読む、という専門家の思考ストーリー
# として見せる」への対応: hypothesis_register[].hypothesis_typeは内部enum値のため、
# そのままUIへ出さず「何を疑ったか」として人間向けの短い文へ翻訳する。legacy値
# （surface/actor_intent_alternative等）と新値（H0_straightforward等）の両方に対応する
# （既存レスポンスとの後方互換のため）。新しい疑いの理由をここで作文するのではなく、
# 既存のhypothesis_typeという分類そのものを訳すだけ。
_HYPOTHESIS_TYPE_LABELS = {
    "surface": "表面的な説明で十分か",
    "H0_straightforward": "表面的な説明で十分か",
    "actor_intent_alternative": "別の意図・狙いがあるのではないか",
    "H1_instrumental_indirect": "別の意図・狙いがあるのではないか",
    "structural_alternative": "別の構造が働いているのではないか",
    "H2_counterparty_audience_shift": "相手・見せたい相手が違うのではないか",
    "actor_model_breaking": "このActor像の前提自体が違うのではないか",
    "H3_constraint_domestic_driver": "国内事情・制約が動かしているのではないか",
    "frame_breaking": "そもそもの見立ての枠組み自体が違うのではないか",
    "H4_frame_breaking": "そもそもの見立ての枠組み自体が違うのではないか",
}


def _actor_display_name(actor_ref, key_actor_profiles):
    if not actor_ref:
        return None
    for p in key_actor_profiles or []:
        if p.get("actor_ref") == actor_ref:
            return p.get("actor")
    return None


def _what_was_seen(linked_hypothesis, key_actor_profiles):
    """②何を見た？ hypothesis_register[].interaction_basisから、このReadingが
    どのActor/関係性（またはEvidence Gap）を根拠にしたかを短い文にする。新しい分析・
    新しい判断はしない——interaction_basisが既に持つactor_ref/target_actor_ref/
    basis_typeを、key_actor_profilesで人間が読める名前へ変換し、軽い接続語（
    「〜の関係に注目した」）で繋ぐだけ（モジュール全体の方針＝既存テキストの整形のみ、
    と同じ扱い）。"""
    if not linked_hypothesis:
        return None
    basis = linked_hypothesis.get("interaction_basis") or {}
    basis_type = basis.get("basis_type")
    hyp_id = linked_hypothesis.get("hypothesis_id")
    source = f"hypothesis_register[{hyp_id}].interaction_basis"
    if basis_type == "EVIDENCE_GAP":
        note = basis.get("gap_note")
        cleaned = _strip_embedded_technical_refs(note) if note else None
        return _item(cleaned, source)
    if not basis_type or basis_type == "NOT_APPLICABLE":
        return None
    actor_name = _actor_display_name(basis.get("actor_ref"), key_actor_profiles)
    target_name = _actor_display_name(basis.get("target_actor_ref"), key_actor_profiles)
    if basis_type == "EVENT_ROLE":
        if not actor_name:
            return None
        return _item(f"{actor_name}の今回の行動が持つ意味に注目した。", source)
    # DIRECTIONAL_PERCEPTION / SITUATION_ACTOR_ASSESSMENT
    if actor_name and target_name:
        text = f"{actor_name}と{target_name}の関係に注目した。"
    elif actor_name:
        text = f"{actor_name}の動きに注目した。"
    else:
        return None
    return _item(text, source)


def _what_was_doubted(linked_hypothesis):
    """③何を疑った？ hypothesis_type（分類）とreason（自由記述）を人間向けの1文に
    まとめる。「Question」という技術用語では見せず、「〜のではないか」という疑いの
    形で表現する（ユーザー指示）。"""
    if not linked_hypothesis:
        return None
    label = _HYPOTHESIS_TYPE_LABELS.get(linked_hypothesis.get("hypothesis_type"))
    reason_items = _sanitize_legacy_text_items(linked_hypothesis.get("reason"))
    reason = reason_items[0] if reason_items else None
    if not label and not reason:
        return None
    text = label or ""
    if reason:
        text = f"{text}。{reason}" if text else reason
    hyp_id = linked_hypothesis.get("hypothesis_id")
    return _item(text, f"hypothesis_register[{hyp_id}].hypothesis_type+reason")


def _hypothesis_formed(linked_hypothesis):
    """④どんな仮説を置いた？ hypothesis_register[].description（仮説段階の記述）を
    そのまま使う。最終的なMain/Alternative Scenario.description（⑤だからどう読む）とは
    別物——こちらは仮説を立てた時点の記述、あちらは絞り込んだ後の最終的な読み。"""
    if not linked_hypothesis:
        return None
    text = linked_hypothesis.get("description")
    cleaned = _strip_embedded_technical_refs(text) if text else None
    if not cleaned:
        return None
    hyp_id = linked_hypothesis.get("hypothesis_id")
    return _item(cleaned, f"hypothesis_register[{hyp_id}].description")


# ---- §Actor Role Classification（Primary/Peripheral、決定論的・新規推論なし） --------
#
# ユーザー指示(2026-10-01、5つの読み方案UI再設計): 「この局面を誰が作っているか」を
# 新しいLLM判定で決めない。既存のinteraction_basis（このReading自体がどのActorの
# 相互作用を根拠にしているか）とevent_role（このActorが自分の行動として記述されて
# いるか、他Actorの行動の対象として記述されているか）という、Expert AIが既に出力した
# fieldだけから機械的に分類する。一意に決められない場合はunclassifiedのまま返す
# （無理に推測しない、が絶対条件）。

def _actor_has_own_agency_signal(profile: dict) -> bool:
    """このActorが自分自身の行動・立場として記述されているか。"""
    event_role = profile.get("event_role") or {}
    return bool(profile.get("observed_action") or event_role.get("role_type") or profile.get("perceived_path"))


def _actor_is_targeted_by_another(actor_ref: str, key_actor_profiles: list) -> bool:
    return any(
        (p.get("event_role") or {}).get("intended_target_actor_ref") == actor_ref
        for p in key_actor_profiles or [])


def _forced_primary_actor_name(linked_hypothesis, key_actor_profiles: list) -> str | None:
    """このReading自体が根拠にしているActor（interaction_basis.actor_ref）の表示名。
    build_actor_rolesとEvidence actor帰属（Reading Report v2）の両方から共通で使う——
    同じ決定論的な値を2箇所で別々に再計算しない。"""
    if not linked_hypothesis:
        return None
    basis = linked_hypothesis.get("interaction_basis") or {}
    actor_ref = basis.get("actor_ref")
    return _actor_display_name(actor_ref, key_actor_profiles) if actor_ref else None


def build_actor_roles(*, linked_hypothesis, key_actors: list, key_actor_profiles: list) -> list:
    """key_actors（このReadingのkey_actor名一覧）を、既存fieldのみからPrimary/
    Peripheral/Unclassifiedへ分類する。linked_hypothesis.interaction_basis.actor_ref
    （このReading自体が根拠にしているActor）が最も強いPrimary signal。それ以外は
    event_role.role_type/observed_action（自分の行動として記述）とevent_role.
    intended_target_actor_ref（他Actorの行動の対象として記述）の有無で判定する。"""
    key_actor_profiles = key_actor_profiles or []
    if not key_actors:
        return []
    forced_primary_name = _forced_primary_actor_name(linked_hypothesis, key_actor_profiles)
    forced_primary_hyp_id = linked_hypothesis.get("hypothesis_id") if linked_hypothesis else None
    profile_by_name = {p.get("actor"): p for p in key_actor_profiles if p.get("actor")}

    roles = []
    for name in key_actors:
        if name and name == forced_primary_name:
            roles.append({
                "actor": name, "role": "primary",
                "_source": f"hypothesis_register[{forced_primary_hyp_id}].interaction_basis.actor_ref",
            })
            continue
        profile = profile_by_name.get(name)
        if not profile:
            roles.append({"actor": name, "role": "unclassified", "_source": None})
            continue
        actor_ref = profile.get("actor_ref")
        has_own_agency = _actor_has_own_agency_signal(profile)
        is_targeted = _actor_is_targeted_by_another(actor_ref, key_actor_profiles) if actor_ref else False
        if has_own_agency and not is_targeted:
            role, source = "primary", "key_actor_profiles[].observed_action/event_role/perceived_path"
        elif is_targeted and not has_own_agency:
            role, source = "peripheral", "key_actor_profiles[].event_role.intended_target_actor_ref"
        else:
            role, source = "unclassified", None
        roles.append({"actor": name, "role": role, "_source": source})
    return roles


# ---- §Situation Phases（Actor-firstの段階再配置、新規LLM生成なし） -----------------
#
# ユーザー指示(2026-10-01): 新しい状況推移をAIに書かせない。既存のtrajectory_assessment/
# key_actor_profiles/situation_actor_assessment/directional_perceptionsという、既に
# Expert AIが出力した内容を「きっかけ→観測された行動→相手の反応・認識→現在」という
# 固定ラベルへ再配置するだけ。対応するfieldが無い段階は省略する（4段階に無理に揃えない）。

def build_situation_phases(*, response_json, primary_actor_name, description) -> list | None:
    response_json = response_json or {}
    phases = []

    ta = response_json.get("trajectory_assessment") or {}
    trigger_text = ta.get("reason") or response_json.get("initial_anomaly")
    trigger_source = "response.trajectory_assessment.reason" if ta.get("reason") else "response.initial_anomaly"
    if not trigger_text:
        kpa = response_json.get("key_point_assessment") or {}
        trigger_text = kpa.get("primary_key_point")
        trigger_source = "response.key_point_assessment.primary_key_point"
    trigger_text = _strip_embedded_technical_refs(trigger_text) if trigger_text else None
    if trigger_text:
        phases.append({"phase": "trigger", "actor": None,
                        "description": _item(trigger_text, trigger_source)})

    profile = None
    if primary_actor_name:
        for p in response_json.get("key_actor_profiles") or []:
            if p.get("actor") == primary_actor_name:
                profile = p
                break
    if profile and profile.get("observed_action"):
        action_text = _strip_embedded_technical_refs(profile["observed_action"])
        if action_text:
            phases.append({
                "phase": "primary_action", "actor": primary_actor_name,
                "description": _item(action_text, "key_actor_profiles[].observed_action"),
            })

    if profile:
        actor_ref = profile.get("actor_ref")
        reaction_text, reaction_source = None, None
        for saa in response_json.get("situation_actor_assessment") or []:
            if saa.get("actor_ref") == actor_ref:
                reaction_text = saa.get("actor_expected_response") or saa.get("analyst_expected_response")
                reaction_source = "situation_actor_assessment[].actor_expected_response"
                break
        if not reaction_text:
            for dp in response_json.get("directional_perceptions") or []:
                if actor_ref and (dp.get("observer_actor_ref") == actor_ref or dp.get("target_actor_ref") == actor_ref):
                    reaction_text = dp.get("perception")
                    reaction_source = "directional_perceptions[].perception"
                    break
        reaction_text = _strip_embedded_technical_refs(reaction_text) if reaction_text else None
        if reaction_text:
            phases.append({"phase": "reaction", "actor": None,
                            "description": _item(reaction_text, reaction_source)})

    if description:
        phases.append({"phase": "current", "actor": None, "description": description})

    return phases or None


def _build_one_interpretation(candidate, *, hypothesis_register, weakening_information,
                               contradicting_evidence, retraction_conditions, evidence_summaries,
                               key_actor_profiles, response_json=None):
    content = candidate.get("content") or {}
    raw = content.get("raw") or {}
    is_main = content.get("origin") == "main_scenario"
    linked_hypothesis = _hypothesis_for_candidate(hypothesis_register, raw.get("derived_from_hypothesis_id"))

    # Phase 10 Evidence Binding(ユーザー指示、Primary Source): mainカードはmain_scenario
    # 自身のevidence_relevance/weakening_evidence_relevance（Promoteされた最終形）を使う。
    # alternativeカードはmain_scenarioにこれらが無いため、対応するhypothesis_register[]の
    # weakening_evidence_relevance（構造あり）を使う。
    #
    # Post-Freeze Integrity Audit Finding CRITICAL-4/I-1(2026-09-22発見)対応: 以前は
    # supporting_evidenceを常に空配列にしていた（supporting_evidence_idsがID配列のみで
    # claim_relevance/rationale等の詳細を持たないため）。しかし実データ監査で、8ケース
    # 全件・代替カード19件全件で実際に有効なsupporting_evidence_idsが存在するにも
    # かかわらずUI上「代替解釈には根拠が無い」ように見えることが判明した——Primary
    # Readingとの間でレビュアーの確信度判断を歪めうる非対称性のため、_evidence_items_
    # from_idsで最小限（evidence_id/summaryのみ、relation/claim_relevance/source_type/
    # rationaleはNone）の構造化Evidenceとして表示する。
    # Reading Report v2（2026-10-02）: Evidence itemへのactor帰属は、build_actor_rolesと
    # 同じ決定論的な主要主体名を使う（新しいActor判定はしない）。
    primary_actor_name = _forced_primary_actor_name(linked_hypothesis, key_actor_profiles)
    if is_main:
        supporting_evidence = _structured_evidence_items(
            raw.get("evidence_relevance"), evidence_summaries, "main_scenario.evidence_relevance",
            actor=primary_actor_name)
        weakening_evidence = _structured_evidence_items(
            raw.get("weakening_evidence_relevance"), evidence_summaries,
            "main_scenario.weakening_evidence_relevance", actor=primary_actor_name)
    else:
        supporting_evidence = _evidence_items_from_ids(
            (linked_hypothesis or {}).get("supporting_evidence_ids"), evidence_summaries,
            f"hypothesis_register[{raw.get('derived_from_hypothesis_id')}].supporting_evidence_ids",
            actor=primary_actor_name)
        weakening_evidence = _structured_evidence_items(
            (linked_hypothesis or {}).get("weakening_evidence_relevance"), evidence_summaries,
            f"hypothesis_register[{raw.get('derived_from_hypothesis_id')}].weakening_evidence_relevance",
            actor=primary_actor_name)

    # Reading Presentation Layer v1 UUID漏れ修正(ユーザー指示): Phase 10で定義した
    # Source of Truthに従い、構造化Evidence（supporting_evidence/weakening_evidence）が
    # 1件以上あるカードでは、対応するlegacy自由記述fieldをreasons/weak_pointsへ重複表示
    # しない。構造化データが無い場合のみlegacy fieldをfallback表示し、その際も要素全体が
    # 内部技術参照（UUID/foundation_pack_asset等）だけのものは除外し、文中に混入した
    # 技術参照は除去する（文章自体の要約・言い換え・新規生成はしない）。
    # 2026-09-23変更、ユーザー指摘「『この読み方が強い理由は』はまとめないで、CQASを
    # それぞれのパートに分けて説明しよ」: 従来はselection_rationale（この読みを主軸に
    # 選んだ理由そのもの）とsupporting_evidence等（それを補強する個別の材料）という、
    # 由来の異なるテキストを1つのreasons配列へ混在させていた。CQAS由来の各partを
    # 区別して見せられるよう、selection_rationale（単一の理由）とsupporting_points
    # （それを補強する個別材料のリスト）の2つのfieldへ分ける。alternativeの
    # descriptionは既にtop-level description fieldとして返しているため、こちらへは
    # 含めない（重複表示を避ける）。
    selection_rationale = None
    supporting_points = []
    if is_main:
        selection_rationale = _item(raw.get("selection_rationale"), "main_scenario.selection_rationale")
        if not supporting_evidence:
            for e in _sanitize_legacy_text_items(raw.get("supporting_evidence")):
                supporting_points.append(_item(e, "main_scenario.supporting_evidence"))
    else:
        for cond in _sanitize_legacy_text_items(raw.get("strengthening_conditions")):
            supporting_points.append(_item(cond, "alternative_scenarios[].strengthening_conditions"))

    weak_points = []
    if is_main:
        if not weakening_evidence:
            for e in _sanitize_legacy_text_items(weakening_information):
                weak_points.append(_item(e, "response.weakening_information"))
            for e in _sanitize_legacy_text_items(contradicting_evidence):
                weak_points.append(_item(e, "response.contradicting_evidence"))
    else:
        for cond in _sanitize_legacy_text_items(raw.get("weakening_conditions")):
            weak_points.append(_item(cond, "alternative_scenarios[].weakening_conditions"))

    unknowns = []
    if linked_hypothesis and linked_hypothesis.get("status") == "unresolved" and linked_hypothesis.get("reason"):
        for e in _sanitize_legacy_text_items(linked_hypothesis["reason"]):
            unknowns.append(_item(
                e, f"hypothesis_register[{linked_hypothesis.get('hypothesis_id')}].reason"))
    if is_main:
        # 主シナリオのカードには、個別のhypothesisへ紐付かない全体のunresolved仮説も添える
        # （現時点で「まだ分からないこと」の全体像は、主シナリオの下にまとめて見せるのが自然）。
        for h in hypothesis_register or []:
            if h is linked_hypothesis:
                continue
            if h.get("status") == "unresolved" and h.get("reason"):
                for e in _sanitize_legacy_text_items(h["reason"]):
                    unknowns.append(_item(e, f"hypothesis_register[{h.get('hypothesis_id')}].reason"))

    outlook = None
    # UUID漏れ修正(ユーザー指示、目視UIレビューで発見): observable_indicators/
    # switch_conditions/weakening_conditions/retraction_conditionsにも出典document_idが
    # 埋め込まれるケースがあるため、Switch Condition表示前にサニタイズする。
    if is_main:
        if_correct = _text_list(
            _sanitize_legacy_text_items(raw.get("observable_indicators")), "main_scenario.observable_indicators")
        weakens_if = _text_list(
            _sanitize_legacy_text_items(retraction_conditions), "response.retraction_conditions")
    else:
        # switch_conditions(主シナリオからこの代替仮説へ切り替える条件、既存フィールド)は、
        # 「これが観測されればこの読み方を支持する」というmain_scenario.observable_indicators
        # と同じ役割を果たす実データ。以前はここで使われずalternativeのif_correctは常にNone
        # だったため、「各読み方をサポートする要素」が主シナリオにしか無い状態だった。
        if_correct = _text_list(
            _sanitize_legacy_text_items(raw.get("switch_conditions")), "alternative_scenarios[].switch_conditions")
        weakens_if = _text_list(
            _sanitize_legacy_text_items(raw.get("weakening_conditions")), "alternative_scenarios[].weakening_conditions")
    if if_correct or weakens_if:
        outlook = {"if_correct": if_correct, "weakens_if": weakens_if}

    confidence_source = "main_scenario.confidence" if is_main else "alternative_scenarios[].confidence"
    # Reading Presentation Layer v1 P1-1修正(ユーザー指示): titleはIOS scenario_candidates
    # 由来の明示titleを最優先し、無ければderived_from_hypothesis_id（例: "H0"/"H0+H4"、
    # モデル自身が付与した既存ラベル）を使う。どちらも無ければNoneのまま返し、新しい
    # semantic titleをこの層で生成しない——フロント側が汎用ラベル（Primary Reading/
    # Alternative Reading N）にフォールバックする。
    title = candidate.get("title") or raw.get("derived_from_hypothesis_id")
    description = _item(
        raw.get("description"), "main_scenario.description" if is_main else "alternative_scenarios[].description")
    actor_roles = build_actor_roles(
        linked_hypothesis=linked_hypothesis, key_actors=content.get("key_actors") or [],
        key_actor_profiles=key_actor_profiles)
    primary_actor_name = next((r["actor"] for r in actor_roles if r["role"] == "primary"), None)
    situation_phases = build_situation_phases(
        response_json=response_json or {}, primary_actor_name=primary_actor_name, description=description)
    return {
        "scenario_candidate_id": candidate.get("scenario_candidate_id"),
        "rank": candidate.get("rank"),
        "displayed": candidate.get("displayed", False),
        "is_main": is_main,
        "title": title,
        "description": description,
        "key_actors": _text_list(content.get("key_actors"), "scenario_candidate.content.key_actors"),
        "actor_roles": actor_roles,
        "situation_phases": situation_phases,
        "why_this_matters": _item(content.get("why_this_matters"), "scenario_candidate.content.why_this_matters"),
        "confidence": _item(raw.get("confidence"), confidence_source),
        "selection_rationale": selection_rationale,
        "supporting_points": _prune(supporting_points),
        "weak_points": _prune(weak_points),
        "unknowns": _prune(unknowns),
        "outlook": outlook,
        # 2026-09-23追加、ユーザー指示「気づく→見る→疑う→仮説→読む」のストーリー化:
        # derived_from_hypothesis_idが無い（hypothesis_registerが生成されない分析
        # プロファイルの）場合はlinked_hypothesisがNoneとなり、以下3つは全てNoneに
        # なる——推測で埋めず、フロント側がそのステップ自体を自然に省略すること。
        "what_was_seen": _what_was_seen(linked_hypothesis, key_actor_profiles),
        "what_was_doubted": _what_was_doubted(linked_hypothesis),
        "hypothesis_formed": _hypothesis_formed(linked_hypothesis),
        # Phase 10 Evidence Binding(ユーザー指示): ID付き構造化Evidence。空の場合は
        # フロント側でweak_points/supporting_points（自由記述fallback）を使うこと。
        "supporting_evidence": supporting_evidence,
        "weakening_evidence": weakening_evidence,
        "closest_alternative": False,
    }


def build_interpretations(*, candidates, response_json):
    """CQAS candidates（main_scenario1件＋alternative_scenarios0件以上）をそのまま使う。
    新しい候補生成・ranking変更は行わない——既存rank（rank1=main、以降は既存生成順）の
    まま並べる。全candidate（displayed=false含む）を返し、`displayed`フラグで初期表示/
    「他の見方も見る」を呼び出し側（API/フロント）が判断する（既存Scenario Cardの
    表示パターンと同じ流儀）。"""
    hypothesis_register = response_json.get("hypothesis_register") or []
    weakening_information = response_json.get("weakening_information")
    contradicting_evidence = response_json.get("contradicting_evidence") or []
    retraction_conditions = response_json.get("retraction_conditions") or []
    evidence_summaries = _evidence_summary_lookup(response_json.get("claims"))
    # 2026-09-23追加: 「②何を見た？」（interaction_basisのactor_ref/target_actor_refを
    # 人間が読める名前へ変換する）に使う。新規判定・新規生成はしない、既存
    # key_actor_profiles[].actorをそのまま参照するだけ。
    key_actor_profiles = response_json.get("key_actor_profiles") or []

    interpretations = [
        _build_one_interpretation(
            c, hypothesis_register=hypothesis_register, weakening_information=weakening_information,
            contradicting_evidence=contradicting_evidence, retraction_conditions=retraction_conditions,
            evidence_summaries=evidence_summaries, key_actor_profiles=key_actor_profiles,
            response_json=response_json)
        for c in sorted(candidates, key=lambda c: c.get("rank") or 0)
    ]

    # Reading Presentation Layer v1(ユーザー指示): Primary Reading比較UI（Why Primary/
    # Closest Alternative/Why not yet）向けに、mainの直後（rank順で最初のalternative）を
    # closest_alternativeとして印を付ける。新しい比較ロジックは作らない——既存のrank順序
    # （モデル自身の生成順）をそのまま「最も近い代替」の判定に使うのみ。この印はフロント側が
    # Primary/Closest Alternativeの2枚を並べて表示するためのもので、各カード自体のreasons/
    # weak_points/outlookは変更しない。
    non_main = [i for i in interpretations if not i["is_main"]]
    if non_main:
        non_main[0]["closest_alternative"] = True

    return interpretations


# ---- Reading Exposure（READING_EXPOSURE_LAYER_V1配線、2026-09-24） -----------
#
# geo_ios_reading_exposure_composer.compose_reading_exposure()（Freeze済み、selection
# logic・スコアリングには一切触れない）が計算済みのFrontier/Exploratory Readingを、
# Step2の既存Reading Card表示形式へ整形するだけの薄い層。S Rank#1-3（MAIN/ALTERNATIVE）
# は既存_build_one_interpretation()をそのまま再利用し内容を変更しない。ここで新しく
# 行うのはFRONTIER/EXPLORATORY用の薄い変換と、内部status文字列（retained/unresolved/
# rejected）を自然文へ翻訳することだけ（_HYPOTHESIS_TYPE_LABELSと同じ方針）。

_WHY_NOT_MAIN_STATUS_LABELS = {
    "retained": "Sは本線には選ばなかったものの、現時点でも検討の対象として保持している読み方です。",
    "unresolved": "Sの中でもまだ結論が出ておらず、追加の確認が必要な読み方です。",
    "rejected": "Sは検討した上で、現時点では採用しなかった読み方です。",
}


def _why_not_main_label(item):
    """original_hypothesis_status（内部enum）をユーザーへそのまま見せず自然文へ翻訳する。
    hypothesis_register由来でない場合（Reading Portfolio/Expert Exploration生成）は
    Sの却下判定そのものを経ていないため、'rejected'であるかのような表現にはしない。"""
    status = item.get("original_hypothesis_status")
    if status in _WHY_NOT_MAIN_STATUS_LABELS:
        return _WHY_NOT_MAIN_STATUS_LABELS[status]
    if item.get("reading_role") == "FRONTIER":
        return "Sは本線としては選ばなかった、追加で検討する価値のある読み方です。"
    if item.get("source") == "user_requested_direction":
        return "ユーザーが指定した観点から、追加で生成された読み方です。"
    return "Sの本線選定とは別の系統から、分析の幅を広げるために追加された読み方です。"


def _exploratory_evidence_items(basis, evidence_summaries, source):
    """supporting_basis/weakening_basisはID配列（hypothesis_register由来）のことも
    自由記述文のリスト（Reading Portfolio/Expert Exploration生成由来）のこともある。
    IDらしき値のみevidence_summariesで解決し、それ以外は自由記述としてそのまま使う
    ——新しい要約はここで生成しない。"""
    items = []
    for entry in basis or []:
        if not isinstance(entry, str):
            continue
        if entry in evidence_summaries:
            items.append({
                "evidence_id": entry, "summary": evidence_summaries.get(entry),
                "relation": None, "claim_relevance": None, "source_type": None,
                "rationale": None, "actor": None, "_source": source,
            })
        else:
            cleaned = _strip_embedded_technical_refs(entry)
            if cleaned and not _looks_like_bare_technical_ref(cleaned):
                items.append({
                    "evidence_id": None, "summary": cleaned,
                    "relation": None, "claim_relevance": None, "source_type": None,
                    "rationale": None, "actor": None, "_source": source,
                })
    return items


def _build_exploratory_interpretation(item, *, evidence_summaries, response_json=None):
    """FRONTIER/EXPLORATORY用の薄いCard変換。Composerが既に計算済みの内容（title/
    content.why_this_matters/supporting_basis/weakening_basis/missing_evidence/
    original_hypothesis_status）をそのまま整形するだけで、hypothesis_register/Reading
    Portfolioの生データから新たに再判定しない（重複実装を避ける）。_build_one_interpretation
    内部のderived_from_hypothesis_idベースの解析ロジックは、raw構造が異なるこの種の
    Readingにはそのまま適用できないため、意図的に別関数にしている。"""
    content = item.get("content") or {}
    scenario_candidate_id = item.get("scenario_candidate_id")
    source = f"reading_exposure[{scenario_candidate_id}]"
    supporting_evidence = _exploratory_evidence_items(
        item.get("supporting_basis"), evidence_summaries, f"{source}.supporting_basis")
    weakening_evidence = _exploratory_evidence_items(
        item.get("weakening_basis"), evidence_summaries, f"{source}.weakening_basis")
    unknowns = []
    missing_evidence = item.get("missing_evidence")
    if missing_evidence:
        unknowns.append(_item(missing_evidence, f"{source}.missing_evidence"))

    description = _item(content.get("why_this_matters"), f"{source}.content.why_this_matters")
    # key_actorsをこのCard自体が持たないため（Composerが付与しない）、actor_roles分類も
    # 常に空になる——無理にkey_actor_profiles全体から推測しない。
    actor_roles = build_actor_roles(
        linked_hypothesis=None, key_actors=[], key_actor_profiles=(response_json or {}).get("key_actor_profiles"))
    situation_phases = build_situation_phases(
        response_json=response_json or {}, primary_actor_name=None, description=description)

    return {
        "scenario_candidate_id": scenario_candidate_id,
        "rank": item.get("rank"),
        "displayed": item.get("displayed", True),
        "is_main": False,
        "reading_role": item.get("reading_role"),
        "exploratory": item.get("exploratory", True),
        "title": item.get("title"),
        "description": description,
        "key_actors": None,
        "actor_roles": actor_roles,
        "situation_phases": situation_phases,
        "why_this_matters": _item(content.get("why_this_matters"), f"{source}.content.why_this_matters"),
        "confidence": None,
        "selection_rationale": None,
        "supporting_points": [],
        "weak_points": [],
        "unknowns": _prune(unknowns),
        "outlook": None,
        "what_was_seen": None,
        "what_was_doubted": None,
        "hypothesis_formed": None,
        "supporting_evidence": supporting_evidence,
        "weakening_evidence": weakening_evidence,
        "closest_alternative": False,
        "why_not_main": _item(_why_not_main_label(item), f"{source}.original_hypothesis_status"),
    }


def _build_review_interpretation(item, *, evidence_summaries, response_json=None):
    """REVIEW MODE（§19-20、2026-09-25追加）専用の薄いCard変換。既存レポートから
    再構成したCurrent Reading、またはExpert AIが生成したAlternative Readingを、Step2の
    既存Reading Card表示形式へ整形するだけ。CQAS/hypothesis_registerは一切参照しない
    （REVIEW MODEにはS/CQASの実行自体が無い）——_build_exploratory_interpretationと
    構造は似せつつ、why_not_mainの文言のみREVIEW MODE向けに変える。"""
    content = item.get("content") or {}
    scenario_candidate_id = item.get("scenario_candidate_id")
    source = f"review_reading[{scenario_candidate_id}]"
    is_current = content.get("origin") == "review_current_reading"

    supporting_evidence = _exploratory_evidence_items(
        content.get("supporting_basis"), evidence_summaries, f"{source}.supporting_basis")
    weakening_evidence = _exploratory_evidence_items(
        content.get("weakening_basis"), evidence_summaries, f"{source}.weakening_basis")
    unknowns = []
    missing_evidence = content.get("missing_evidence")
    if missing_evidence:
        unknowns.append(_item(missing_evidence, f"{source}.missing_evidence"))

    description = _item(content.get("why_this_matters"), f"{source}.content.why_this_matters")
    # REVIEW MODEはresponse_json自体が常に空（CQAS/hypothesis_register不参照のため）、
    # key_actor_profiles由来のPrimary/Peripheral判定はできない——key_actorsの一覧自体は
    # 表示するが、全てunclassifiedとして返る。
    actor_roles = build_actor_roles(
        linked_hypothesis=None, key_actors=content.get("key_actors") or [], key_actor_profiles=None)
    situation_phases = build_situation_phases(
        response_json=response_json or {}, primary_actor_name=None, description=description)

    return {
        "scenario_candidate_id": scenario_candidate_id,
        "rank": item.get("rank"),
        "displayed": item.get("displayed", True),
        "is_main": is_current,
        "reading_role": item.get("reading_role"),
        "exploratory": not is_current,
        "title": item.get("title"),
        "description": description,
        "key_actors": _text_list(content.get("key_actors"), f"{source}.content.key_actors"),
        "actor_roles": actor_roles,
        "situation_phases": situation_phases,
        "why_this_matters": _item(content.get("why_this_matters"), f"{source}.content.why_this_matters"),
        "confidence": None,
        "selection_rationale": None,
        "supporting_points": [],
        "weak_points": [],
        "unknowns": _prune(unknowns),
        "outlook": None,
        "what_was_seen": None,
        "what_was_doubted": None,
        "hypothesis_formed": None,
        "supporting_evidence": supporting_evidence,
        "weakening_evidence": weakening_evidence,
        "closest_alternative": False,
        "why_not_main": None if is_current else _item(
            "Expert AIが、現在の読みとは別の観点から提案した追加の読み方です。",
            f"{source}.origin"),
    }


def build_reading_exposure(*, reading_exposure_items, response_json):
    """READING_EXPOSURE_LAYER_V1が構成した最大5件のReadingを、Step2表示用に整形する
    （順序＝Composerのexposure_rank、Source of Truth。ここで再rankingしない）。
    MAIN/ALTERNATIVE（S Rank#1-3）は既存_build_one_interpretation()をそのまま再利用し
    Top1-3の内容・順序を一切変更しない。reading_exposure_itemsが空（Composerが呼ばれ
    なかった、または1件も返さなかった）場合は空リストを返す——呼び出し元(API)が
    interpretations（既存表示）へフォールバックすること。"""
    if not reading_exposure_items:
        return []
    hypothesis_register = response_json.get("hypothesis_register") or []
    weakening_information = response_json.get("weakening_information")
    contradicting_evidence = response_json.get("contradicting_evidence") or []
    retraction_conditions = response_json.get("retraction_conditions") or []
    evidence_summaries = _evidence_summary_lookup(response_json.get("claims"))
    key_actor_profiles = response_json.get("key_actor_profiles") or []

    results = []
    for item in sorted(reading_exposure_items, key=lambda r: r.get("exposure_rank") or 999):
        role = item.get("reading_role")
        origin = (item.get("content") or {}).get("origin")
        if role in ("MAIN", "ALTERNATIVE") and origin in ("main_scenario", "alternative_scenario"):
            candidate = {
                "scenario_candidate_id": item.get("scenario_candidate_id"),
                "rank": item.get("rank"), "displayed": item.get("displayed", True),
                "title": item.get("title"), "content": item.get("content"),
            }
            card = _build_one_interpretation(
                candidate, hypothesis_register=hypothesis_register,
                weakening_information=weakening_information,
                contradicting_evidence=contradicting_evidence,
                retraction_conditions=retraction_conditions,
                evidence_summaries=evidence_summaries, key_actor_profiles=key_actor_profiles,
                response_json=response_json)
            card["reading_role"] = role
            card["exploratory"] = False
            card["why_not_main"] = None
        elif role == "CURRENT" or origin in ("review_current_reading", "review_alternative_reading"):
            # REVIEW MODE（§19-20、2026-09-25追加）: CQAS/hypothesis_registerを一切
            # 参照しない薄いbuilder。既存Reading Cardの表示形式へ整形するだけ。
            card = _build_review_interpretation(
                item, evidence_summaries=evidence_summaries, response_json=response_json)
        else:
            card = _build_exploratory_interpretation(
                item, evidence_summaries=evidence_summaries, response_json=response_json)
        card["selected"] = item.get("selected", False)
        # exposure_rank伝播漏れ修正（2026-09-24発見）: _build_one_interpretation/
        # _build_exploratory_interpretationのどちらもexposure_rankを返さないため、
        # 最終カードには常に無かった（並び順自体はここでのsortで正しいが、API出力の
        # exposure_rank fieldが常にnullになっていた）。元のitemが持つComposer由来の
        # 値をそのまま転記する。
        card["exposure_rank"] = item.get("exposure_rank")
        results.append(card)
    return results


# ---- §6 ここまでの流れ -------------------------------------------------------

def build_trajectory(*, response_json):
    """trajectory_assessment/trajectory_information_needが存在し、かつ内容がある場合のみ表示
    する（実験的機能フラグ未有効時はどちらもnullのため、その場合はセクション自体を非表示に
    する——UI表示名は「Trajectory」ではなく「ここまでの流れ」）。"""
    ta = response_json.get("trajectory_assessment") or {}
    tin = response_json.get("trajectory_information_need") or {}

    summary = ta.get("reason") or tin.get("information_needed")
    raw_gaps = ta.get("evidence_gaps") or tin.get("evidence_gaps") or []
    gaps = [g.get("question") if isinstance(g, dict) else g for g in raw_gaps]
    gaps = [g for g in gaps if g]

    if not summary and not gaps:
        return None

    source = "response.trajectory_assessment" if ta.get("reason") else "response.trajectory_information_need"
    return {
        "summary": _item(summary, f"{source}.reason" if ta.get("reason") else f"{source}.information_needed"),
        "gaps": _text_list(gaps, f"{source}.evidence_gaps"),
    }


# ---- §7 見るべき人・動き -----------------------------------------------------

def build_actors(*, response_json):
    """既存key_actor_profilesのみを使う。新しいActor選定・Retrievalは行わない。該当情報が
    無いActorは無理に埋めず、意味のあるfieldが1つも無ければそのActor自体を省略する。

    Presentation Hygiene拡張(ユーザー指示、Trajectory Canary目視/機械チェックで発見、
    2026-09-22): current_move等のActor自由記述に、Foundation Context由来の
    "[document_id: UUID]"のような技術参照がそのまま混入するケースがある
    （Trajectory ContextとActor Intelligenceが同じCall1入力から情報を合成する際、
    モデルがFoundation Context側の引用記法を誤って持ち込んだと見られる）。既存の
    UUID/内部key sanitizer（_strip_embedded_technical_refs）を、reasons/weak_points等
    と同じ方針でActor自由記述fieldにも適用する。"""
    actors = []
    for profile in response_json.get("key_actor_profiles") or []:
        wants = profile.get("ultimate_objective") or profile.get("issue_objective")
        constraint = profile.get("red_line")
        if not constraint:
            vulnerabilities = profile.get("vulnerabilities") or []
            constraint = vulnerabilities[0] if vulnerabilities else None
        current_move = profile.get("observed_action")
        why_it_matters = profile.get("why_actor_matters")
        if not (wants or constraint or current_move or why_it_matters):
            continue
        wants = _strip_embedded_technical_refs(wants) if wants else wants
        constraint = _strip_embedded_technical_refs(constraint) if constraint else constraint
        current_move = _strip_embedded_technical_refs(current_move) if current_move else current_move
        why_it_matters = _strip_embedded_technical_refs(why_it_matters) if why_it_matters else why_it_matters
        actors.append({
            "actor": profile.get("actor"),
            "wants": _item(wants, "key_actor_profiles[].ultimate_objective"),
            "constraint": _item(constraint, "key_actor_profiles[].red_line"),
            "current_move": _item(current_move, "key_actor_profiles[].observed_action"),
            "why_it_matters": _item(why_it_matters, "key_actor_profiles[].why_actor_matters"),
        })
    return actors


# ---- §7b Relationship / Leverage ---------------------------------------------
# Reading Presentation Layer v1(ユーザー指示): Supporting Analysisの追加項目。データが
# 存在する場合のみ表示し、無い場合はセクション自体を省略する（Presentation側で補完しない）。

def build_relationships(*, response_json):
    """directional_perceptions（AがBについて持つ主観的認識）とsituation_actor_assessment
    （Actor×Issue Componentのleverage/制約）から、Relationship/leverageの構造化記述を
    そのまま取り出す。新しい関係性推論・Counterparty予測は行わない。

    Post-Freeze Integrity Audit Finding F-3(2026-09-22発見)対応: build_actors()と同じ
    Foundation Context由来の技術参照混入リスクが、この関数の自由記述field（perception/
    leverage/constraint/actor_expected_response/analyst_expected_response）には未対応
    のまま残っていた。現8ケースでは中身がクリーン（situation_actor_assessment/
    directional_perceptions自体が空）なため顕在化していないが、生きた経路のため
    build_actors()と同じsanitizerを適用する。"""
    perceptions = []
    for dp in response_json.get("directional_perceptions") or []:
        perception = dp.get("perception")
        perception = _strip_embedded_technical_refs(perception) if perception else perception
        perceptions.append({
            "observer": dp.get("observer_actor_ref"),
            "target": dp.get("target_actor_ref"),
            "perception": _item(perception, "directional_perceptions[].perception"),
            "claim_type": dp.get("claim_type"),
            "confidence": dp.get("confidence"),
        })

    leverage = []
    for saa in response_json.get("situation_actor_assessment") or []:
        leverage_text = saa.get("primary_usable_leverage")
        constraint_text = saa.get("primary_binding_constraint")
        actor_expected = saa.get("actor_expected_response")
        analyst_expected = saa.get("analyst_expected_response")
        if not (leverage_text or constraint_text or actor_expected or analyst_expected):
            continue
        leverage_text = _strip_embedded_technical_refs(leverage_text) if leverage_text else leverage_text
        constraint_text = _strip_embedded_technical_refs(constraint_text) if constraint_text else constraint_text
        actor_expected = _strip_embedded_technical_refs(actor_expected) if actor_expected else actor_expected
        analyst_expected = _strip_embedded_technical_refs(analyst_expected) if analyst_expected else analyst_expected
        leverage.append({
            "actor": saa.get("actor_ref"),
            "issue_component": saa.get("issue_component"),
            "leverage": _item(leverage_text, "situation_actor_assessment[].primary_usable_leverage"),
            "constraint": _item(constraint_text, "situation_actor_assessment[].primary_binding_constraint"),
            "actor_expected_response": _item(
                actor_expected, "situation_actor_assessment[].actor_expected_response"),
            "analyst_expected_response": _item(
                analyst_expected, "situation_actor_assessment[].analyst_expected_response"),
            "target_actor": saa.get("target_actor_ref"),
        })

    if not perceptions and not leverage:
        return None
    return {"perceptions": perceptions, "leverage": leverage}


# ---- §7c Structural Constraints ------------------------------------------------

def build_structural_constraints(*, response_json):
    """discretion_assessmentが存在する場合のみ表示する（deepプロファイル限定fieldのため、
    存在しない局面の方が多い）。この局面がHuman Discretion Dominant/Structure Dominant/
    Mixedのどれかを、既存の自己申告のまま表示する。

    Post-Freeze Integrity Audit Finding F-3(2026-09-22発見)対応: mode_rationaleは
    8ケース全件で実際に populate される生きた経路のため、build_actors()と同じ
    sanitizerを適用する。"""
    da = response_json.get("discretion_assessment") or {}
    mode = da.get("discretion_mode")
    rationale = da.get("mode_rationale")
    if not mode and not rationale:
        return None
    rationale = _strip_embedded_technical_refs(rationale) if rationale else rationale
    return {
        "discretion_mode": mode,
        "mode_rationale": _item(rationale, "discretion_assessment.mode_rationale"),
    }


# ---- §7d Media / Perception ----------------------------------------------------

def build_media_context(*, response_json):
    """media_source_contextが存在する場合のみ表示する。編集判断・主流ナラティブとの差分を
    そのまま表示し、新しいMedia解釈は行わない。"""
    msc = response_json.get("media_source_context") or {}
    editorial = []
    for e in msc.get("media_editorial_context") or []:
        outlet = e.get("outlet")
        caveat = e.get("caveat")
        if not outlet and not caveat:
            continue
        editorial.append({"outlet": outlet, "caveat": _item(caveat, "media_source_context.media_editorial_context[].caveat")})

    narrative_deviation = []
    for nd in msc.get("narrative_deviation") or []:
        deviation = nd.get("deviation_from_mainstream")
        why_it_matters = nd.get("why_it_matters")
        if not deviation and not why_it_matters:
            continue
        narrative_deviation.append({
            "source": nd.get("source"),
            "deviation_from_mainstream": _item(
                deviation, "media_source_context.narrative_deviation[].deviation_from_mainstream"),
            "why_it_matters": _item(
                why_it_matters, "media_source_context.narrative_deviation[].why_it_matters"),
        })

    source_note = _item(
        msc.get("source_of_sources_note"), "media_source_context.source_of_sources_note")

    if not editorial and not narrative_deviation and not source_note:
        return None
    return {
        "editorial_context": editorial,
        "narrative_deviation": narrative_deviation,
        "source_of_sources_note": source_note,
    }


# ---- §Scenario Impact（既存response.business_impact、新規LLM無し） -----------------
#
# ユーザー指示(2026-10-02、Reading Report v2): 「この読み方で見えるシナリオ」の社会・
# 業界影響には、既存だが従来Presentation層で一切使っていなかったresponse.business_impact
# （direct/indirect/environment_change、09_KNOWLEDGE_BUSINESS_IMPACT_MODEL.md準拠）を
# 使う。これはPrimary Storyline（MAIN Reading）専用のresponse-level fieldであり、
# 各Reading固有のデータではない——呼び出し元（API/フロント）はMAIN Readingの時だけ
# 表示し、他Readingへ使い回して架空の企業影響をでっち上げない。

def build_business_impact(*, response_json):
    bi = response_json.get("business_impact") or {}
    if bi.get("unrelated_note") and not (bi.get("direct") or bi.get("indirect")):
        return None
    social = _item(bi.get("environment_change"), "response.business_impact.environment_change")
    industry_parts = [p for p in (bi.get("direct"), bi.get("indirect")) if p]
    industry = _text_list(industry_parts, "response.business_impact.direct+indirect")
    if not social and not industry:
        return None
    return {"social_environment": social, "industry_business": industry}


# ---- §11B 周辺Actor・メディアの動き（媒体比較、既存だが従来build_media_contextが ----
#      捨てていたfieldを使うだけ、新規LLM無し） --------------------------------------
#
# ユーザー指示(2026-10-01、5つの読み方案UI再設計): media_source_context.media_editorial_
# context[].observed[]/hypotheses[]はExpert AIが既に出力しているが、従来のbuild_media_
# context()はoutlet/caveatしか表示していなかった（観測種別・個別仮説は捨てていた）。
# ここでは新しいMedia解釈をAIにさせず、既に出力済みの詳細を媒体単位の比較表へ展開する
# だけ。Emphasized Actor/Dateに相当するfieldはExpert AI出力に存在しないため、この層でも
# 補わない（空欄のまま、無理に推測しない）。

def build_media_comparison(*, response_json):
    msc = response_json.get("media_source_context") or {}
    rows = []
    for e in msc.get("media_editorial_context") or []:
        outlet = e.get("outlet")
        if not outlet:
            continue
        observed = e.get("observed") or []
        framing_texts = _sanitize_legacy_text_items(
            [o.get("observation_text") for o in observed if isinstance(o, dict) and o.get("observation_text")])
        hypotheses = e.get("hypotheses") or []
        interpretation_texts = _sanitize_legacy_text_items(
            [h.get("hypothesis_text") for h in hypotheses if isinstance(h, dict) and h.get("hypothesis_text")])
        caveat_item = _item(e.get("caveat"), f"media_source_context.media_editorial_context[outlet={outlet}].caveat")
        framing = _text_list(
            framing_texts, f"media_source_context.media_editorial_context[outlet={outlet}].observed[].observation_text")
        interpretation = _text_list(
            interpretation_texts,
            f"media_source_context.media_editorial_context[outlet={outlet}].hypotheses[].hypothesis_text")
        if not framing and not interpretation and not caveat_item:
            continue
        rows.append({
            "outlet": outlet, "framing": framing or caveat_item, "interpretation": interpretation,
        })
    return rows or None


# ---- §9 次に見るもの ---------------------------------------------------------

def _char_bigrams(text: str) -> set:
    t = (text or "").strip()
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _text_similarity(a: str, b: str) -> float:
    """簡易Jaccard類似度（文字bigram集合）。日本語は分かち書きされていないため、
    既存geo_knowledge_gap_service.text_similarity（空白区切りトークン、英語向け）は
    ここでは使えず、文字bigramで近似する。Embedding/pgvectorは導入しない、決定的な
    近似という既存方針(docs/geopolitical-retrieval-design.md)を維持する。"""
    ba, bb = _char_bigrams(a), _char_bigrams(b)
    if not ba or not bb:
        return 0.0
    return len(ba & bb) / len(ba | bb)


# monitoring_indicators/identified_knowledge_gaps/additional_researchはそれぞれ独立して
# 生成されるため、表現は違っても同じ論点を指す項目が混在しやすい（実データで確認済み:
# 「9/24 Trump-Xi会談のアジェンダで台湾・武器売却・海峡通過がどう位置づけられるか」を
# 異なる言い回しで3回生成、等）。文字bigram類似度0.3以上を実質的な重複とみなして
# 1件目のみ残す（新規LLM呼び出し・要約生成はしない、既存文言をそのまま使うか捨てるかのみ）。
_NEAR_DUPLICATE_THRESHOLD = 0.3


def _new_deduped_collector():
    """近似重複(文字bigram類似度0.3以上)を弾きながら項目を集めるヘルパー。
    build_monitoring_signals/build_research_needsの両方で使う共通ロジック。"""
    items = []
    seen_texts = []

    def add(text, source, why=None):
        # UUID漏れ修正(ユーザー指示、目視UIレビューで発見): identified_knowledge_gaps[].
        # recommended_action等、"why"に出典document_idが埋め込まれるケースがあるため、
        # ここでも表示時のみ技術参照を除去する（文章自体の要約・言い換えはしない）。
        text = _strip_embedded_technical_refs(text) if text else text
        if not text:
            return
        why = _strip_embedded_technical_refs(why) if why else why
        why = why or None
        if any(_text_similarity(text, existing) >= _NEAR_DUPLICATE_THRESHOLD for existing in seen_texts):
            return
        seen_texts.append(text)
        items.append({"text": text, "why": why, "_source": source})

    return items, add


def build_monitoring_signals(*, response_json):
    """①(の一部)「各読み方をサポートする要素」のうち、特定の読み方1件に紐付かない
    局面全体のmonitoring_indicatorsのみを扱う。読み方ごとの観測指標（if_correct、
    main_scenario.observable_indicators/alternative_scenarios.switch_conditions）は
    build_interpretationsの各interpretation.outlook.if_correctに既にある——
    ここでは重複させず、フロント側で両方を①として並べて見せる。"""
    items, add = _new_deduped_collector()
    for indicator in response_json.get("monitoring_indicators") or []:
        add(indicator, "response.monitoring_indicators")
    return items


def build_research_needs(*, design, response_json):
    """②「より調査すべき要素」。identified_knowledge_gaps / additional_researchを統合する。
    重複は落とすが、既存テキストの書き換え・要約生成は行わない（既存文言をそのまま使う）。"""
    items, add = _new_deduped_collector()

    for gap in response_json.get("identified_knowledge_gaps") or []:
        add(gap.get("description"), "response.identified_knowledge_gaps[].description",
            why=gap.get("recommended_action"))
    for research in response_json.get("additional_research") or []:
        add(research.get("question"), "response.additional_research[].question",
            why=research.get("rationale"))

    if not items and design:
        for point in (design or {}).get("watch_points") or []:
            add(point, "analysis_design.watch_points")

    return items


# ---- 全体組み立て -------------------------------------------------------------

def build_situation_presentation(*, design, candidates, response_json, reading_exposure_items=None):
    """7セクションのうち「自由に聞く」で既に代替可能なチャット導線は含めない（フロント側の
    モード切替で対応）。presentation_versionを固定文字列として含める（§13 Evaluation
    Integrity: I-B1 Evaluation Snapshotが参照する既存raw fieldはこの関数の入力にも出力にも
    一切書き換えられない）。

    reading_exposure（READING_EXPOSURE_LAYER_V1配線、2026-09-24追加）: 既存interpretations
    は無変更のまま残し、追加のkeyとしてのみ持たせる（既存フロント/評価が読むinterpretations
    への後方互換を壊さない）。呼び出し元がreading_exposure_itemsを渡さない場合は空リスト
    になり、フロント側がinterpretationsへフォールバックする。"""
    return {
        "presentation_version": PRESENTATION_VERSION,
        "key_point": build_key_point(design=design, response_json=response_json),
        "interpretations": build_interpretations(candidates=candidates, response_json=response_json),
        "reading_exposure": build_reading_exposure(
            reading_exposure_items=reading_exposure_items or [], response_json=response_json),
        "trajectory": build_trajectory(response_json=response_json),
        "actors": build_actors(response_json=response_json),
        "relationships": build_relationships(response_json=response_json),
        "structural_constraints": build_structural_constraints(response_json=response_json),
        "media_context": build_media_context(response_json=response_json),
        "media_comparison": build_media_comparison(response_json=response_json),
        "business_impact": build_business_impact(response_json=response_json),
        "monitoring_signals": build_monitoring_signals(response_json=response_json),
        "research_needs": build_research_needs(design=design, response_json=response_json),
    }


# ---- §10 Evidence Date補完（実DB参照、新規LLM無し） --------------------------------
#
# ユーザー指示(2026-10-01): evidence_relevanceにはdate fieldが無いが、新しいLLM入力field
# は増やさない。既存geo_evidence.document_id → geo_documents.published_atという実DB経路
# （geo_expert_engine._evidence_source_typesと同じ考え方、別の資料から決定論的に解決する）
# から補完するだけ。解決できないevidence_idはdate=Noneのまま（無理に推測しない）。
# この関数だけがclientを必要とする——build_situation_presentation自体は引き続き
# client非依存の純粋関数のまま、呼び出し元(API層)がbuild後にこの関数で後処理する。

def enrich_evidence_dates(client, presentation: dict) -> None:
    all_items = []
    for interp in presentation.get("interpretations") or []:
        all_items += (interp.get("supporting_evidence") or []) + (interp.get("weakening_evidence") or [])
    for card in presentation.get("reading_exposure") or []:
        all_items += (card.get("supporting_evidence") or []) + (card.get("weakening_evidence") or [])

    evidence_ids = sorted({it["evidence_id"] for it in all_items if it.get("evidence_id")})
    if not evidence_ids:
        return

    evidence_rows = client.select("geo_evidence", {
        "select": "evidence_id,document_id", "evidence_id": f"in.({','.join(evidence_ids)})"})
    document_id_by_evidence_id = {
        r["evidence_id"]: r.get("document_id") for r in evidence_rows if r.get("document_id")}
    document_ids = sorted({d for d in document_id_by_evidence_id.values() if d})

    date_by_document_id = {}
    if document_ids:
        doc_rows = client.select("geo_documents", {
            "select": "document_id,published_at,created_at",
            "document_id": f"in.({','.join(document_ids)})"})
        for d in doc_rows:
            date_by_document_id[d["document_id"]] = d.get("published_at") or d.get("created_at")

    for it in all_items:
        eid = it.get("evidence_id")
        doc_id = document_id_by_evidence_id.get(eid) if eid else None
        it["date"] = date_by_document_id.get(doc_id) if doc_id else None


def strip_debug_sources(node):
    """_source（Developer/debug mode専用のtechnical label）を再帰的に除去したコピーを返す。
    元のnodeは変更しない（raw_analysisとの分離を保つため、破壊的な操作にしない）。"""
    if isinstance(node, dict):
        return {k: strip_debug_sources(v) for k, v in node.items() if k != "_source"}
    if isinstance(node, list):
        return [strip_debug_sources(v) for v in node]
    return node
