"""地政学インテリジェンス基盤: 地政学専門家AIのリクエスト/レスポンスJSON Schema（指示書14.5章）。

JSONを正本とし、画面表示用の自然文はフロントエンドのRendererが生成する。
schemas/geo_expert_ai_request.schema.json・geo_expert_ai_response.schema.jsonを
正本とし、ここではそれを読み込んで提供するだけにする（スキーマの二重管理を避ける）。
"""

import copy
import json
from pathlib import Path

import geo_explanatory_model_library

SCHEMAS_DIR = Path(__file__).parent / "schemas"

with open(SCHEMAS_DIR / "geo_expert_ai_request.schema.json", encoding="utf-8") as f:
    REQUEST_SCHEMA = json.load(f)

with open(SCHEMAS_DIR / "geo_expert_ai_response.schema.json", encoding="utf-8") as f:
    RESPONSE_SCHEMA = json.load(f)


# ===================================================================
# v1.2 段階実行(Staged Execution) Phase A: Stage1/Stage2 Schema構築
# ===================================================================
# RESPONSE_SCHEMA（正本）のproperties/definitionsを複製・加工して、Stage1（常に1回）
# ／Stage2（モジュール別、条件付き）用の小さいSchemaを組み立てる。RESPONSE_SCHEMA自体は
# 変更しない。Azure/OpenAIのStrict Structured Output（strict=True）に適合させるため、
# 全propertiesをrequiredにし、意味上任意の項目はnullable型（null許容）のまま残す。
# strict modeが解釈しないバリデーションキーワード（minItems等）は取り除く
# （「最低1件」等の意味的要件はSchemaではなくgeo_expert_quality_guard.py側で検査する）。

_STRICT_UNSUPPORTED_KEYS = (
    "minItems", "maxItems", "minLength", "maxLength", "pattern", "format",
    "minimum", "maximum", "minProperties", "maxProperties",
)


def _strictify(node):
    """object型のpropertiesを全required化しadditionalProperties=falseを付与する。
    配列・$ref・スカラーは再帰的に子要素だけ処理する。破壊的変更を避けるため、
    呼び出し側で必ずdeepcopyしたノードに対して使うこと。"""
    if isinstance(node, dict):
        node = {k: _strictify(v) for k, v in node.items() if k not in _STRICT_UNSUPPORTED_KEYS}
        node_type = node.get("type")
        is_object = node_type == "object" or (isinstance(node_type, list) and "object" in node_type)
        if is_object and node.get("properties"):
            node["required"] = list(node["properties"].keys())
            node["additionalProperties"] = False
        return node
    if isinstance(node, list):
        return [_strictify(v) for v in node]
    return node


def _nullable(node: dict) -> dict:
    """master schemaでは非nullable objectとして定義されているが、Stage1では出番が
    ない場合にnullを返せるようにしたいプロパティ（例: executive_brief以外のモードでの
    executive_summary）向けに、typeへ"null"を追加する。"""
    node = dict(node)
    t = node.get("type")
    if isinstance(t, list):
        if "null" not in t:
            node["type"] = [*t, "null"]
    elif t is not None and t != "null":
        node["type"] = [t, "null"]
    return node


def _strict_definitions() -> dict:
    return {name: _strictify(copy.deepcopy(d)) for name, d in RESPONSE_SCHEMA.get("definitions", {}).items()}


def _prop(name: str, *, drop_item_properties: tuple = ()) -> dict:
    """RESPONSE_SCHEMA["properties"][name]を複製し、必要なら配列item側の特定
    プロパティ（例: key_actor_profiles[].action_substanceはStage2所有なので除く）を
    取り除いてからstrict化する。"""
    node = copy.deepcopy(RESPONSE_SCHEMA["properties"][name])
    node_type = node.get("type")
    is_array = node_type == "array" or (isinstance(node_type, list) and "array" in node_type)
    if drop_item_properties and is_array:
        item_props = node.get("items", {}).get("properties", {})
        for key in drop_item_properties:
            item_props.pop(key, None)
    return _strictify(node)


def _resolve_refs(node, all_defs: dict):
    """$refをすべてall_defsの実体で置き換えて完全展開する（definitions/$refの仕組みを
    使わない）。実データ検証で、他のdefinitionをネストして$refする定義（例:
    rootPremiseItemがevidence_idsでevidenceIdListを$refする）が複数の兄弟プロパティから
    再利用されると、Azure/OpenAIのStrict Structured Outputがそのschema全体を無効と
    判定することを確認したため、$ref間接参照に頼らず常に完全展開したSchemaを渡す。"""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/definitions/"):
            name = ref.rsplit("/", 1)[-1]
            return _resolve_refs(copy.deepcopy(all_defs[name]), all_defs)
        return {k: _resolve_refs(v, all_defs) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve_refs(v, all_defs) for v in node]
    return node


def _build_schema(properties: dict) -> dict:
    all_defs = _strict_definitions()
    resolved_properties = {k: _resolve_refs(v, all_defs) for k, v in properties.items()}
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": resolved_properties,
        "required": list(resolved_properties.keys()),
        "additionalProperties": False,
    }


# ---- Stage1: 常に1回生成するコア分析 ----
# 既存互換フィールド（stakeholders/sources_and_citations/knowledge_gaps_referenced/
# relevant_precedents/relevant_mechanisms/assets_dependencies_leverage）はLLMに
# 要求せず、geo_expert_engine.py側でSource Pack/Case Context/Foundation Packから
# プログラム的に導出しマージ時に注入する（このSchemaには含めない）。
_STAGE1_ALTERNATIVE_SCENARIO_ITEM = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "confidence": {"type": ["string", "null"], "enum": ["高", "中", "低", "判断不能", None]},
        "switch_conditions": {"type": ["array", "null"], "items": {"type": "string"}},
    },
    "required": ["description", "confidence", "switch_conditions"],
    "additionalProperties": False,
}


def build_stage1_schema(*, extra_modules: tuple = (), defer_scenario: bool = False) -> dict:
    """v1.2 Stage1（Key Stakeholder-firstのコア分析、常に1回のLLM呼び出し）用Schema。
    最有力の代替シナリオ1件＋switch_conditionsをここで必須にする
    （複数の代替仮説の詳細化はStage2のdeep_scenarioモジュール側が担当、ただし
    defer_scenario=Trueの場合はこの限りではない、下記参照）。

    Phase D-3(Holistic Reasoning Architecture Review): defer_scenario=Trueの場合、
    main_scenario/alternative_scenarios/strongest_counterargumentをこのSchemaから除外する
    （Human Strategic Interaction/Competing Hypothesisの後にのみMain Scenarioを生成する
    ため、build_stage3_scenario_schema()側の専任とする——旧来のここでの生成経路は完全に
    無効化され、二重source of truthにはならない）。extra_modulesは、CallBudgetを増やさず
    この1回のLLM呼び出しへ追加で合成したいStage2モジュール名（例:
    ("audience_signal","media_context","situation_actor_assessment","directional_perception")）
    ——既存のSTAGE2_MODULES定義をそのまま再利用し、新しいSchema定義は作らない。"""
    extra_modules = list(extra_modules)
    properties = {
        "one_line_assessment": _prop("one_line_assessment"),
        "initial_anomaly": _prop("initial_anomaly"),
        "claims": _prop("claims"),
        "dynamics": _prop("dynamics"),
        "decisive_power_and_leverage": _prop("decisive_power_and_leverage"),
    }
    # Phase 7-2K-A/B(ユーザー指示): actor_candidate_selectionはkey_stakeholder_
    # assessment/key_actor_profilesより前に置く（Phase E Reasoning Order Fixと同じ
    # 理由——先にCandidate Setからmaterial agencyのある主体を選び、その結果を使って
    # Key Stakeholder/Key Actor Profileへ深掘りする、という順序をSchema property順
    # （宣言順に値を生成する）で強制する）。
    if "actor_candidate_selection" in extra_modules:
        properties.update(STAGE2_MODULES["actor_candidate_selection"])
    properties.update({
        "key_stakeholder_assessment": _prop("key_stakeholder_assessment"),
        "key_actor_profiles": _prop("key_actor_profiles", drop_item_properties=("action_substance",)),
    })
    # Phase E(Reasoning Order Fix、ユーザー指示): 生成順序をKey Human Actors→
    # directional_perceptions→situation_actor_assessment→Primary Question/Key Point→
    # hypothesis_registerの順に固定する。JSON Schemaのproperties順（Azure/OpenAIの
    # Structured Outputは宣言順に値を生成する）を、この推論順序の指示（MODE_GUIDANCE/
    # _DEEP_REASONING_SEQUENCE_NOTE）と一致させることで、Key Point Framingが先に確定し
    # Human Interaction層が後付けの空欄になる、という構造的な原因を取り除く。
    # extra_modulesのリスト順に依存せず、この関数側で明示的に順序を制御する。
    #
    # Phase F(Human Interaction Analytical Gate、ユーザー指示): Phase Eの順序修正だけでは
    # hypothesis_registerがdirectional_perceptions/situation_actor_assessmentを経由せず
    # event_role等から直接生成できてしまう（実データで確認済み）。そこでHuman Interaction
    # を「検討したら書く」から「検討したことの自己申告が必須のGate」へ格上げする:
    # discretion_assessment（Key Actors確定直後の軽量な事前判定、Human Interactionを
    # 起動するか）→directional_perceptions→situation_actor_assessment→
    # human_interaction_coverage（両者を試みた後の自己申告、GROUNDED/PARTIAL_EVIDENCE/
    # INSUFFICIENT_EVIDENCE/NOT_APPLICABLE_STRUCTURE_FIRSTを空配列のまま無言で
    # 通過させない）→key_point_assessment→hypothesis_register（各itemがinteraction_basis
    # でActor Interactionへの構造的接続を持つ）。
    if "discretion_assessment" in extra_modules:
        properties.update(STAGE2_MODULES["discretion_assessment"])
    for _module_name in ("directional_perception", "situation_actor_assessment"):
        if _module_name in extra_modules:
            properties.update(STAGE2_MODULES[_module_name])
    if "human_interaction_coverage" in extra_modules:
        properties.update(STAGE2_MODULES["human_interaction_coverage"])
    properties["key_point_assessment"] = _prop("key_point_assessment")
    # Cue→Grammar→Question Transformation Pilot（Q1、ユーザー指示、現在デフォルトOFF）:
    # hypothesis_register（Answer Angles/Storyline Candidates相当）の直前、Key Point
    # 確定の直後に置き、Question→Answer Angles→Hypothesisという推論順序をSchema
    # property順で強制する（Phase Eのordering fixと同じ手法）。
    if "reasoning_lens_application" in extra_modules:
        properties.update(STAGE2_MODULES["reasoning_lens_application"])
    # Cue→Targeted Expert Question Q2b（ユーザー指示、現在デフォルトOFF）: 15 Grammar
    # 分類は行わず、feasibility/role_choreography/premise_challengeの3軸のみに限定した
    # より狭い代替だったが、axisをLLMに自己分類させる設計自体がpremise_challengeへの
    # ラベル収束を招いたため、Q3aへ置き換えた。
    if "expert_reasoning_questions" in extra_modules:
        properties.update(STAGE2_MODULES["expert_reasoning_questions"])
    # Q3a Atomic Question Router（ユーザー指示）: axis自己分類をやめ、Q08A/Q08B/Q06A/
    # Q01Aという4つの独立したチェック（固定Schema key）へ置き換える。各チェックは
    # 互いに排他的なカテゴリではなく、それぞれ独立にApplicable/Not Applicableを判定する。
    if "expert_reasoning_checks" in extra_modules:
        properties.update(STAGE2_MODULES["expert_reasoning_checks"])
    if "hypothesis_register" in extra_modules:
        properties.update(STAGE2_MODULES["hypothesis_register"])
    # Expert Reasoning Pipeline v0.2（ユーザー指示）: Candidate Hypothesis確定の直後、
    # Deterministic Supplemental Retrievalが解決対象にするIntelligence Needsを生成する。
    if "intelligence_needs" in extra_modules:
        properties.update(STAGE2_MODULES["intelligence_needs"])
    if "model_activation" in extra_modules:
        properties.update(STAGE2_MODULES["model_activation"])
    # Trajectory Context Experiment v0.1（ユーザー指示、test-only）: Candidate Hypothesis
    # 確定の直後、Deterministic Trajectory Retrievalが解決対象にするTrajectory Assessment
    # を生成する。
    if "trajectory_assessment" in extra_modules:
        properties.update(STAGE2_MODULES["trajectory_assessment"])
    # Situation Trajectory Intelligence v1（ユーザー指示、test-only）: v0.1/v0.2の
    # trajectory_assessmentとは独立したEvidence Sufficiency判定を生成する。
    if "trajectory_information_need" in extra_modules:
        properties.update(STAGE2_MODULES["trajectory_information_need"])
    # Situation Trajectory Intelligence v1.2（ユーザー指示、test-only）: v1のBoolean Gate
    # trajectory_information_needを置き換える、Evidence Request方式。
    if "trajectory_evidence_requests" in extra_modules:
        properties.update(STAGE2_MODULES["trajectory_evidence_requests"])
    # Expert Context Architecture v1.3（ユーザー指示、test-only）: v1/v1.2のOptional
    # Self-Report Gateを置き換える、Hypothesis Evidence Requirements + Coverage Check方式。
    if "hypothesis_evidence_requirements" in extra_modules:
        properties.update(STAGE2_MODULES["hypothesis_evidence_requirements"])
    # Hypothesis Generation v1.4（ユーザー指示、test-only）: Continuity/State-transition
    # 構造。hypothesis_evidence_requirementsの直後（同じくhypothesis_register確定後）。
    if "hypothesis_state_transitions" in extra_modules:
        properties.update(STAGE2_MODULES["hypothesis_state_transitions"])
    properties.update({
        "preference_action_gap_summary": _prop("preference_action_gap_summary"),
        "identified_knowledge_gaps": _prop("identified_knowledge_gaps"),
        "weakening_information": _prop("weakening_information"),
        "contradicting_evidence": _prop("contradicting_evidence"),
        "retraction_conditions": _prop("retraction_conditions"),
        "additional_research": _prop("additional_research"),
        "business_impact": _prop("business_impact"),
        "monitoring_indicators": _prop("monitoring_indicators"),
        "unknown_or_uncertain": _prop("unknown_or_uncertain"),
        "executive_summary": _nullable(_prop("executive_summary")),
    })
    if not defer_scenario:
        properties["main_scenario"] = _prop("main_scenario")
        properties["alternative_scenarios"] = {
            "type": "array", "items": _STAGE1_ALTERNATIVE_SCENARIO_ITEM,
        }
        properties["strongest_counterargument"] = _prop("strongest_counterargument")
    for _module_name in extra_modules:
        if _module_name in ("discretion_assessment", "directional_perception",
                             "situation_actor_assessment", "human_interaction_coverage",
                             "reasoning_lens_application", "expert_reasoning_questions",
                             "expert_reasoning_checks", "hypothesis_register",
                             "intelligence_needs", "model_activation", "trajectory_assessment",
                             "trajectory_information_need", "trajectory_evidence_requests",
                             "hypothesis_evidence_requirements", "hypothesis_state_transitions",
                             "actor_candidate_selection"):
            continue
        properties.update(STAGE2_MODULES[_module_name])
    return _build_schema(properties)


def build_stage3_scenario_schema() -> dict:
    """Phase D-3(Holistic Reasoning Architecture Review): Human Strategic Interaction
    （directional_perceptions/situation_actor_assessment）とCompeting Hypothesis
    （hypothesis_register）の後に生成する、Scenario/Validation専任のSchema。
    main_scenario/strongest_counterargumentに加え、alternative_scenarios（簡易版ではなく
    Full版、旧deep_scenarioモジュールと全く同じSchema定義を再利用）とadditional_stakeholders
    をこの1回のLLM呼び出しへ合成する——deep_scenarioモジュールが担っていたalternative_
    scenarios生成の役割はこのSchemaへ一本化し、Stage1側では二度と生成しない（二重
    source of truthを作らない）。"""
    properties = {
        "main_scenario": _prop("main_scenario"),
        "strongest_counterargument": _prop("strongest_counterargument"),
    }
    properties.update(STAGE2_MODULES["deep_scenario"])
    return _build_schema(properties)


# ---- Stage2: モジュール別（条件付き、必要なものだけを1回のLLM呼び出しへ動的合成する） ----
_KEY_ACTOR_ACTION_SUBSTANCE_PATCH = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "actor_ref": {"type": "string"},
            "action_substance": copy.deepcopy(RESPONSE_SCHEMA["definitions"]["actionSubstance"]),
        },
        "required": ["actor_ref", "action_substance"],
        "additionalProperties": False,
    },
}

_ADDITIONAL_STAKEHOLDER_ITEM = copy.deepcopy(RESPONSE_SCHEMA["properties"]["stakeholders"]["items"])

# Situation Trajectory Intelligence v1.2（ユーザー指示、test-only、現在デフォルトOFF）:
# v1のBoolean Gate（trajectory_information_need.information_needed）を廃止し、CQAS自身の
# Reasoning DependencyからEvidence Requestを直接出させる方式へ置き換える。ユーザー指示
# Section4「Public response schemaには出さない。Internal / intermediate schemaのみ」に
# 従い、意図的にRESPONSE_SCHEMA（schemas/geo_expert_ai_response.schema.json）へは登録
# しない——_prop()を経由しない、この関数専用の手書きSchemaとしてのみ存在する。実行時は
# geo_expert_engine.run_expert_request_stagedがCall1直後にstage1_data.popしてresponse_
# jsonへ一切含めず、診断目的でexecution_metadataへのみ格納する。
_TRAJECTORY_V1_2_EVIDENCE_REQUEST_ITEM = {
    "type": "object",
    "properties": {
        "request_id": {"type": "string", "description": "この局面内で一意な識別子(例: TER-1)"},
        "need_type": {
            "type": "string",
            "enum": ["prior_state", "turning_point", "precedent", "narrative_state",
                     "actor_history", "relationship_change", "constraint_change"],
        },
        "priority": {
            "type": "string",
            "enum": ["P1", "P2", "P3"],
            "description": ("P1=Scenario rankingを変える可能性がある／P2=Primary vs "
                             "Alternative branchを分ける／P3=Explanatory richnessだけを"
                             "上げる"),
        },
        "reasoning_dependency": {
            "type": "string",
            "description": ("このRequestが解決しようとしている、CQAS上の具体的な依存関係"
                             "（例:『Scenario XはPrevious restraintが今も有効という前提に"
                             "依存している』）。一般論としての『過去情報があれば役立つ』は"
                             "不可。"),
        },
        "question": {
            "type": "string",
            "description": "検索・裏取りに使う具体的な問い（一般的な背景History取得の問いにしない）",
        },
        "why_it_matters": {
            "type": "string",
            "description": "このGapが埋まると、現在のCue/Question/Scenario比較のどこが変わるか",
        },
        "primary_actors": {"type": "array", "items": {"type": "string"}},
        "secondary_actors": {"type": "array", "items": {"type": "string"}},
        "core_issue": {"type": "string"},
        "relevant_dimensions": {"type": "array", "items": {"type": "string"}},
        "time_window": {"type": "string"},
        "expected_evidence": {
            "type": "array", "items": {"type": "string"},
            "description": "見つかれば解決に使えると期待する、具体的なEvidenceの種類",
        },
    },
    "required": ["request_id", "need_type", "priority", "reasoning_dependency", "question",
                 "why_it_matters", "primary_actors", "secondary_actors", "core_issue",
                 "relevant_dimensions", "time_window", "expected_evidence"],
    "additionalProperties": False,
}

# Expert Context Architecture v1.3（ユーザー指示、test-only、現在デフォルトOFF）:
# Situation Trajectory v1/v1.2のOptional Self-Report方式（「追加情報が必要か？」を
# LLMに自己判定させる）を廃止し、各Hypothesisが成立するために必要なEvidence
# Requirementsを必須の分析構造として先に列挙させ、既存Source Packとの充足度
# （Coverage Check、決定的・追加LLM呼び出し無し）で判定する方式へ置き換える。
# v1.2同様、RESPONSE_SCHEMAへは登録しない（Internal / intermediate schemaのみ、
# ユーザー指示Section5「Do not expose in user-facing response yet」）。
_EXPERT_CONTEXT_V1_3_DIMENSIONS = [
    "current_state", "current_incentive", "actor_objective", "actor_history",
    "actor_response_pattern", "relationship_state", "relationship_change",
    "prior_state", "turning_point", "precedent", "constraint", "constraint_change",
    "narrative_state", "audience_perception", "implementation_feasibility", "source_intent",
]

_HYPOTHESIS_EVIDENCE_REQUIREMENT_ITEM = {
    "type": "object",
    "properties": {
        "requirement_id": {"type": "string", "description": "例: ER-H1-1"},
        "dimension": {"type": "string", "enum": _EXPERT_CONTEXT_V1_3_DIMENSIONS},
        "importance": {
            "type": "string", "enum": ["CRITICAL", "SUPPORTING", "OPTIONAL"],
            "description": ("CRITICAL=欠けているとHypothesis viability/Scenario "
                             "ranking/Primary-vs-Alternative/Expert-edge保持を変え得る／"
                             "SUPPORTING=確信度・説明力は上がるが通常は順位を変えない／"
                             "OPTIONAL=厚みが増すだけ"),
        },
        "question": {"type": "string", "description": "このHypothesis固有の具体的な問い"},
        "supporting_evidence": {
            "type": "string",
            "description": "見つかればこのHypothesisを支持するEvidenceの具体的な内容",
        },
        "falsifying_evidence": {
            "type": "string",
            "description": "見つかればこのHypothesisを弱める/反証するEvidenceの具体的な内容",
        },
        "primary_actors": {"type": "array", "items": {"type": "string"}},
        "secondary_actors": {"type": "array", "items": {"type": "string"}},
        "core_issue": {"type": "string"},
        "time_window": {"type": "string"},
    },
    "required": ["requirement_id", "dimension", "importance", "question",
                 "supporting_evidence", "falsifying_evidence", "primary_actors",
                 "secondary_actors", "core_issue", "time_window"],
    "additionalProperties": False,
}

_HYPOTHESIS_EVIDENCE_REQUIREMENTS_GROUP_ITEM = {
    "type": "object",
    "properties": {
        "hypothesis_id": {"type": "string", "description": "hypothesis_register内のhypothesis_idと一致させる"},
        "evidence_requirements": {
            "type": "array",
            "items": _HYPOTHESIS_EVIDENCE_REQUIREMENT_ITEM,
        },
    },
    "required": ["hypothesis_id", "evidence_requirements"],
    "additionalProperties": False,
}

_HYPOTHESIS_DISCRIMINATOR_ITEM = {
    "type": "object",
    "properties": {
        "discriminator_for": {
            "type": "array", "items": {"type": "string"},
            "description": "この論点で競合するhypothesis_idのリスト（例: ['H1','H2']）",
        },
        "dimension": {"type": "string", "enum": _EXPERT_CONTEXT_V1_3_DIMENSIONS},
        "question": {"type": "string"},
        "required_evidence": {
            "type": "string",
            "description": "どちらのHypothesisが正しいかを判別できる、具体的なEvidenceの内容",
        },
        "primary_actors": {"type": "array", "items": {"type": "string"}},
        "secondary_actors": {"type": "array", "items": {"type": "string"}},
        "core_issue": {"type": "string"},
        "time_window": {"type": "string"},
    },
    "required": ["discriminator_for", "dimension", "question", "required_evidence",
                 "primary_actors", "secondary_actors", "core_issue", "time_window"],
    "additionalProperties": False,
}

# Hypothesis Generation v1.4（ユーザー指示、test-only、現在デフォルトOFF）: v1.3の
# Frozen-Call1実験（E01）で、Full Evidenceでも「現在の抑制が続く」という基本仮説
# （Continuity）は6/6で安定生成される一方、「相手側の行動・Thresholdが変化したら
# 抑制が外れ、より高いEscalationが現実味を帯びる」というState-transition仮説は
# 0/6だったことを受けた新設計。各Hypothesisに「継続すれば」だけでなく「何が変われば
# 前提が崩れるか」を必須構造として持たせる。特定Case（Hormuz/Iran/Israel等）への
# 依存は一切書かない、汎用的な仕組みとして実装する。RESPONSE_SCHEMAへは登録しない
# （v1.2/v1.3と同じくInternal / intermediate schemaのみ）。
#
# v1.4.1（ユーザー指示）: v1.4のE01_FULL 6-run測定で、Continuity/Conditional Branch/
# 具体的Thresholdは6/6で安定した一方、中核仮説（Hormuz/Leverage型）のtransition_driver_
# actorが6/6で「対象Actor自身」に偏り、Opponent/第三者/構造的要因が候補として検討されて
# いなかった（他の仮説では実際にOpponentがdriverになる例もあり、能力自体は存在した）。
# 「Opponentを優先せよ」という誘導ではなく、driver_types_considered（検討したTypeの
# 集合）とdriver_type（実際に採用したTypeの分類）を分離して明示させることで、"検討する"
# と"採用する"を切り離す（ユーザー指示: 全部を仮説化するのではなく、一度候補として
# 検討させることが目的）。
_HYPOTHESIS_TRANSITION_DRIVER_TYPES = ["focal_actor", "opponent_actor", "third_party",
                                        "domestic", "structural"]
_HYPOTHESIS_STATE_TRANSITION_ITEM = {
    "type": "object",
    "properties": {
        "hypothesis_id": {"type": "string", "description": "hypothesis_register内のhypothesis_idと一致させる"},
        "continuity_condition": {
            "type": "string",
            "description": "現在この仮説の前提を成立させている、具体的な制約・インセンティブ・"
                             "関係性は何か（何が変わらなければ、この仮説が成立し続けるか）",
        },
        "driver_types_considered": {
            "type": "array",
            "items": {"type": "string", "enum": _HYPOTHESIS_TRANSITION_DRIVER_TYPES},
            "description": "この仮説について、実際に検討したTransition Driverの種類"
                             "（focal_actor=対象Actor自身の構造的選択／opponent_actor=対立"
                             "する相手Actorの行動／third_party=第三者／domestic=国内・内部"
                             "要因／structural=環境的・構造的要因）。採用しなかった種類も、"
                             "検討した場合はここに含める。",
        },
        "transition_driver_type": {
            "type": "string", "enum": _HYPOTHESIS_TRANSITION_DRIVER_TYPES,
            "description": "実際に採用したtransition_driver_actorの種類分類",
        },
        "transition_driver_actor": {
            "type": "string",
            "description": "その制約を変化させ得る主体。この仮説の対象となっているActor自身とは"
                             "限らない——相手Actor・第三者・環境要因も含めて検討すること",
        },
        "transition_driver_change": {
            "type": "string",
            "description": "その主体の、どのような具体的な行動・状態変化・認識変化が、"
                             "continuity_conditionを支える前提を崩し得るか",
        },
        "transition_threshold": {
            "type": "string",
            "description": "その変化が実際に起きたと判断できる、観測可能で具体的な兆候・閾値"
                             "（『何か大きな出来事が起きれば』のような曖昧な表現は不可）",
        },
        "post_transition_hypothesis": {
            "type": "string",
            "description": "その閾値を超えた場合に、この局面がどう読み替えられるかを具体的に"
                             "述べる（『エスカレーションする』のような一般論ではなく、対象Actorが"
                             "その後どう行動する可能性が高いかまで踏み込む）",
        },
    },
    "required": ["hypothesis_id", "continuity_condition", "driver_types_considered",
                 "transition_driver_type", "transition_driver_actor",
                 "transition_driver_change", "transition_threshold", "post_transition_hypothesis"],
    "additionalProperties": False,
}

# Phase 7-2K-A/B(Deterministic Candidate Set → LLM Materiality Selection、ユーザー指示、
# test-only、現在デフォルトOFF): Phase 7-1D/7-2I/7-2Jの3種の異なるPrompt介入
# （Hypothesis reasoning段階への一般Addendum／Runtime Actor-Level Packaging／Key Actor
# Selection段階への直接instruction）がすべて0/3で失敗し、LLM自由生成によるKey Actor
# Selectionそのものに構造的な限界があると判断されたことを受けた、責務分離の再設計。
# System側（geo_expert_engine._build_deterministic_actor_candidate_set、追加LLM呼び出し
# 無し）が既存のEntity/Relationship/Actor Intelligence/Institution Intelligence/Issue
# linkageのみからCandidate Setを機械的に構築し、LLMはそのCandidate Set内からのみ
# material agencyのある主体を選択する（Candidate Set外のentity_idを作ることは禁止——
# 必要な主体が無ければactor_candidate_gapsへ記述する）。v1.2/v1.3同様、RESPONSE_SCHEMA
# へは登録しない（Internal / diagnostic専用、Public Response Schemaには出さない）。
_ACTOR_CANDIDATE_SELECTION_ITEM = {
    "type": "object",
    "properties": {
        "candidate_entity_id": {
            "type": "string",
            "description": "【Actor Candidate Set】に実在するentity_idのみ。Candidate Set"
                             "に無いentity_idを新たに作らないこと（架空のIDを作らない）。",
        },
        "materiality_reason": {
            "type": "string",
            "description": "この主体が、この局面で実際にmaterial agency（decision "
                             "authority／implementation authority／blocking power／"
                             "agenda-setting power／negotiating power／material business "
                             "influenceのいずれか）を持つと判断した具体的な理由。単なる"
                             "言及・一般的な関連性だけでは不十分。",
        },
        "role_in_situation": {
            "type": "string",
            "description": "この主体が局面内で果たしている具体的な役割",
        },
        "confidence": {"type": "string", "enum": ["高", "中", "低"]},
    },
    "required": ["candidate_entity_id", "materiality_reason", "role_in_situation", "confidence"],
    "additionalProperties": False,
}


def _stage2_module_properties() -> dict:
    return {
        "audience_signal": {
            "audience_analysis": _prop("audience_analysis"),
            "key_actor_action_substance": _strictify(copy.deepcopy(_KEY_ACTOR_ACTION_SUBSTANCE_PATCH)),
        },
        "review": {
            "root_premise_check": _prop("root_premise_check"),
            "review_findings": _prop("review_findings"),
        },
        "media_context": {
            "media_source_context": _prop("media_source_context"),
        },
        "situation_actor_assessment": {
            "situation_actor_assessment": _prop("situation_actor_assessment"),
            # Session2 Reasoning Upgrade（Phase4）: 新規Azure呼び出しを増やさず、同一Stage2
            # モジュール内でinvestigation_questionsも合成する。
            "investigation_questions": _prop("investigation_questions"),
        },
        # Phase E(Reasoning Order Fix): hypothesis_registerはsituation_actor_assessment
        # モジュールから分離し、独立したモジュール名として持つ（Azure呼び出し回数・発火条件は
        # 従来通りdeepプロファイルのみでsituation_actor_assessmentと必ず同時に発火させるが、
        # build_stage1_schema側でKey Point Assessmentより後に挿入するため、生成順序を
        # モジュール構成から独立して制御できるようにする）。
        "hypothesis_register": {
            # Expert AI Depth & Grounding Upgrade（11-14章 Competing Hypotheses /
            # Hypothesis Pruning）: 新規Azure呼び出しを増やさず、既存situation_actor_
            # assessmentモジュール（deepプロファイル限定）内で合成する。既存
            # deep_scenario/media_contextと同じ「まずdeep限定、実データ検証後に
            # standardへの昇格を検討する」導入作法に倣う。
            "hypothesis_register": _prop("hypothesis_register"),
        },
        # Cue→Grammar→Question Transformation Pilot（ユーザー指示）: hypothesis_registerと
        # 同じ導入作法（新規Azure呼び出しを増やさず、deepプロファイル限定で同一Stage1合成
        # 呼び出しへ相乗り）。build_stage1_schema側でhypothesis_registerの直前へ明示的に
        # 挿入する。
        "reasoning_lens_application": {
            "reasoning_lens_application": _prop("reasoning_lens_application"),
        },
        # Expert Reasoning Pipeline v0.2（ユーザー指示）: CALL1(Explore/Activate)側で
        # Candidate Hypothesisの具体化に必要なIntelligence Needsを列挙する。hypothesis_
        # registerの直後に置き、Deterministic Supplemental Retrieval（LLM呼び出しなし）が
        # CALL2の前にqueryを解決する。expert_reasoning_checks/Q1/Q2/Q3とは独立したモジュール。
        "intelligence_needs": {
            "intelligence_needs": _prop("intelligence_needs"),
        },
        # Explanatory Model Activation v0.3（ユーザー指示）: intelligence_needsと同じ
        # 導入作法。CALL1(Explore/Activate)側でCompact Activation Indexを提示し、
        # 展開する価値があるModel IDだけを挙げさせる（Grammar分類ではない、0〜数件）。
        "model_activation": {
            "model_activation_requests": _prop("model_activation_requests"),
        },
        # Trajectory Context Experiment v0.1（ユーザー指示、test-only、現在デフォルトOFF）:
        # intelligence_needsと同じ導入作法。CALL1(Explore)側で「現在のSnapshotだけでは
        # この局面のScenarioを適切に読めないか」を判定させ、trajectory_needed=trueの
        # 場合のみ、CALL2前にDeterministic Trajectory Retrievalを行う（追加LLM呼び出し
        # なし）。hypothesis_registerの直後に置く（Candidate Hypothesis確定後に、その
        # Hypothesisを検証する上でTrajectoryが要るかを判定する順序）。
        "trajectory_assessment": {
            "trajectory_assessment": _prop("trajectory_assessment"),
        },
        # Situation Trajectory Intelligence v1（ユーザー指示、test-only、現在デフォルトOFF）:
        # v0.1/v0.2のtrajectory_assessment（「時系列推論が必要か」というGate）とは独立した、
        # 別モジュール・別Flag。「このIssue Scopeについて、どの具体的Evidence Gapが不足して
        # いるか」というEvidence Sufficiency判定に特化する。両モジュールは同時にSchemaへ
        # 混在させない設計（call1_extra_modules組み立て時、各Flagで独立に追加/除外する）。
        # DEPRECATED_FOR_RETRIEVAL_DECISION（Expert Context Architecture v1.3、ユーザー
        # 指示Section4/49）: Evidence Ablation v1.1でE01の因果依存が実証されたにも
        # 関わらずinformation_needed=falseのまま6/6で不発火だったため、v1.3は本Fieldを
        # Retrieval実行判定に一切使わない。回帰確認が済むまで削除はしない（コードはこの
        # ままFlag OFF時は無効のまま残す）。
        "trajectory_information_need": {
            "trajectory_information_need": _prop("trajectory_information_need"),
        },
        # Situation Trajectory Intelligence v1.2（ユーザー指示、test-only、現在デフォルトOFF）:
        # trajectory_information_needのBoolean Gateを置き換える、Evidence Request方式。
        # _TRAJECTORY_V1_2_EVIDENCE_REQUEST_ITEM（RESPONSE_SCHEMA非登録の手書きSchema）を
        # そのまま配列要素として使う。v1と同時にSchemaへ混在させない設計（call1_extra_
        # modules組み立て時、各Flagで独立に追加/除外する）。
        # DEPRECATED_FOR_RETRIEVAL_DECISION（Expert Context Architecture v1.3、ユーザー
        # 指示Section4/49）: 18-run最終検証で、Ablation済みE01/E02では0/4件、逆にFull
        # EvidenceのRegression E08/E20/E22では3/3件生成という逆転傾向が確認されたため、
        # v1.3は本FieldをRetrieval実行判定に一切使わない。回帰確認が済むまで削除しない。
        "trajectory_evidence_requests": {
            "trajectory_evidence_requests": {
                "type": "array",
                "items": _strictify(copy.deepcopy(_TRAJECTORY_V1_2_EVIDENCE_REQUEST_ITEM)),
                "description": ("CQAS Reasoning上の具体的な依存関係を解くために必要な"
                                 "Historical Evidenceへの明示的なRequest（最大3件、"
                                 "優先度付き）。不要ならば空配列（これがBoolean Gateの"
                                 "代わりになる）。"),
            },
        },
        # Expert Context Architecture v1.3（ユーザー指示、test-only、現在デフォルトOFF）:
        # 「追加情報が必要か？」という自己申告（v1/v1.2）を廃止し、hypothesis_register
        # でretainedとなった各HypothesisについてEvidence Requirements（何が確認できれば
        # 支持/反証/比較できるか）を必須構造として列挙させる。競合するHypothesis間の
        # Discriminatorも別配列として持つ（Section10、最優先でRetrieval対象になる）。
        "hypothesis_evidence_requirements": {
            "hypothesis_evidence_requirements": {
                "type": "array",
                "items": _strictify(copy.deepcopy(_HYPOTHESIS_EVIDENCE_REQUIREMENTS_GROUP_ITEM)),
                "description": ("retainedとなった各hypothesis_idについて、そのHypothesis"
                                 "を支持/反証/比較評価するために確認すべきEvidence "
                                 "Requirements。Optionalな自己申告ではなく必須の分析構造"
                                 "——Hypothesisが1件も無ければ空配列でよい。"),
            },
            "hypothesis_discriminators": {
                "type": "array",
                "items": _strictify(copy.deepcopy(_HYPOTHESIS_DISCRIMINATOR_ITEM)),
                "description": ("拮抗する複数のHypothesisを判別するために必要な、"
                                 "最優先のEvidence Requirement。競合が無ければ空配列。"),
            },
        },
        # Hypothesis Generation v1.4（ユーザー指示、test-only、現在デフォルトOFF）:
        # retainedとなった各Hypothesisに「継続すればどうなるか」だけでなく「何が変われば
        # 前提が崩れるか（State-transition）」を必須構造として持たせる。v1.3のEvidence
        # Requirementsとは独立したモジュール（Retrieval判定には使わない、Call1のみで
        # 完結する）。
        "hypothesis_state_transitions": {
            "hypothesis_state_transitions": {
                "type": "array",
                "items": _strictify(copy.deepcopy(_HYPOTHESIS_STATE_TRANSITION_ITEM)),
                "description": ("retainedとなった各hypothesis_idについて、その前提を"
                                 "支える制約が何によって崩れ得るかを明示するState-"
                                 "transition構造。Hypothesisが1件も無ければ空配列でよい。"),
            },
        },
        # Phase 7-2K-A/B(Deterministic Candidate Set → LLM Materiality Selection、
        # ユーザー指示、test-only、現在デフォルトOFF): 【Actor Candidate Set】ブロック
        # （geo_expert_engine._format_actor_candidate_set_block、Runtime-only、DB永続化
        # なし）で提示された候補の中から、実際にmaterial agencyを持つ主体だけを選ばせる。
        "actor_candidate_selection": {
            "actor_candidate_selection": {
                "type": "array",
                "items": _strictify(copy.deepcopy(_ACTOR_CANDIDATE_SELECTION_ITEM)),
                "description": ("【Actor Candidate Set】に提示された候補のうち、この局面で"
                                 "実際にmaterial agencyを持つと判断した主体だけを選ぶこと。"
                                 "粒度（国家／制度／個人等）そのものでは優先順位をつけず、"
                                 "情報の豊富さでもなく、実際のagencyで判断すること。"
                                 "key_actor_profilesで深掘りする主体のentity_idは、ここで"
                                 "選んだcandidate_entity_idと一致させること（一致しない"
                                 "場合はkey_actor_profiles側のentity_idをnullのままにする）。"),
            },
            "actor_candidate_gaps": {
                "type": "array", "items": {"type": "string"},
                "description": ("【Actor Candidate Set】に候補が無いが、この局面を実際に"
                                 "動かす／止める主体が別途必要だと判断した場合、その理由を"
                                 "ここに記述する。新しいentity_idを作らないこと。"
                                 "不要ならば空配列。"),
            },
        },
        # Cue→Targeted Expert Question Q2b（ユーザー指示、現在デフォルトOFF）: reasoning_
        # lens_applicationと同じ導入作法（新規Azure呼び出しを増やさず、deepプロファイル
        # 限定で同一Stage1合成呼び出しへ相乗り）。build_stage1_schema側でhypothesis_
        # registerの直前へ挿入する。
        "expert_reasoning_questions": {
            "expert_reasoning_questions": _prop("expert_reasoning_questions"),
        },
        # Q3a Atomic Question Router（ユーザー指示）: 同じ導入作法。expert_reasoning_
        # questions(axis自己分類、単一配列)とは異なり、4つの独立したチェック（固定
        # Schema key）を持つ1つのobjectとして持つ。
        "expert_reasoning_checks": {
            "expert_reasoning_checks": _prop("expert_reasoning_checks"),
        },
        "analyst_learning": {
            "analyst_coaching_notes": _prop("analyst_coaching_notes"),
        },
        "directional_perception": {
            # Tier1(Eguchi Expert AI Final v1.0対応): situation_actor_assessmentと同じ
            # 導入作法（まずdeepプロファイル限定、実データ検証後にstandardへの昇格を検討）。
            # 新規Azure呼び出しは追加しない（既存Stage2の1回の合成呼び出しへ相乗り）。
            "directional_perceptions": _prop("directional_perceptions"),
        },
        # Phase F(Human Interaction Analytical Gate): discretion_assessment/
        # human_interaction_coverageも新規Azure呼び出しを増やさず、既存の
        # directional_perception/situation_actor_assessmentと同一Stage2合成呼び出しへ
        # 相乗りする（deepプロファイル限定、両者は必ず同時に発火させる）。build_stage1_schema
        # 側で生成順序を明示的に制御するため、独立したモジュール名として持つ。
        "discretion_assessment": {
            "discretion_assessment": _prop("discretion_assessment"),
        },
        "human_interaction_coverage": {
            "human_interaction_coverage": _prop("human_interaction_coverage"),
        },
        "deep_scenario": {
            "alternative_scenarios": _prop("alternative_scenarios"),
            "additional_stakeholders": {
                "type": "array", "items": _strictify(copy.deepcopy(_ADDITIONAL_STAKEHOLDER_ITEM)),
            },
        },
    }


STAGE2_MODULES = _stage2_module_properties()


def build_stage2_schema(module_names: list) -> dict:
    """選択された複数モジュールのpropertiesを合成し、1回のLLM呼び出しで済む
    Schemaを組み立てる（モジュール数が実Azure呼び出し回数に比例しないようにするため）。"""
    properties = {}
    for name in module_names:
        properties.update(STAGE2_MODULES[name])
    return _build_schema(properties)


# ---- プロファイル(quick/standard/deep) × mode → Stage2モジュール発火表 ----
EXPERT_PROFILES = ("quick", "standard", "deep")

# モード固有トリガー: プロファイルに関わらず常時オン
MODE_INTRINSIC_STAGE2_MODULES = {
    "draft_review": ["review"],
    "outline_review": ["review"],
    "analyst_coaching": ["analyst_learning"],
}

# プロファイル別の追加トリガー（モード固有トリガーの上に積む。quickは追加なし）
_PROFILE_ADDITIONAL_MODULES = {
    "quick": {},
    "standard": {
        # v1.2 Phase A実データ比較（Hormuz 3ケース、docs/geopolitical-staged-execution-
        # pilot-results.md）で、匿名ペア比較の敗因の一部がalternative_scenariosの
        # 強化/弱化/切替条件の深掘り不足（deep_scenario未実行）と判明したため、
        # 追加の実Azure呼び出しを増やさず（Stage2は1回の合成呼び出しのまま）
        # case_analysis+standardにdeep_scenarioを追加する。
        "case_analysis": ["audience_signal", "deep_scenario"],
        # Geopolitical Intelligence OS「周辺分析」タイル(ユーザー指示、2026-09-23):
        # research_design（IOS Analysis Sessionの既定mode、geo_ios_expert_ai_adapter.
        # DEFAULT_MODE/DEFAULT_PROFILE参照）にmedia_contextが付いていなかったため、
        # media_source_contextが常にnullとなり「メディア」タイルが表示されなかった。
        "research_design": ["media_context"],
    },
    "deep": {
        # Session2 Reasoning Upgrade: situation_actor_assessmentはdeepプロファイル限定で
        # 開始し、実データ検証（Phase5）を経てからstandardへの昇格を検討する
        # （deep_scenarioの前例に倣う）。
        # Cue→Grammar→Question Transformation Pilot(Q1、ユーザー指示): reasoning_lens_
        # applicationはBlind Benchmark実験で検証済みだが、Grammar Selection精度(Gold一致
        # 27%)・G06/G03偏重・E22回帰(Evidence不足許可の汎化疑い)が判明したため、実験完了
        # 後はデフォルトReasoningからOFFに戻す(Baseline Reasoningを基準状態として維持)。
        # モジュール自体・Schema・Promptは削除せず残す(再検証時に再度リストへ加えるだけで
        # 良いようにする)。
        # Q2b(投機的なaxis自己分類、Controlled Test v1/v2実施済み)はv1=低recall、
        # v2=文言強化でrecallは改善したがpremise_challengeへのラベル収束・precision悪化
        # が判明したため、デフォルトOFFのまま(リストから除外済み、モジュール自体は残す)。
        # Q3a(ユーザー指示): 単一fieldでのaxis自己分類をやめ、Q08A/Q08B/Q06A/Q01Aという
        # 4つの独立したAtomic Question Router(固定Schema key、LLMに分類させない)へ置き換え。
        # expert_reasoning_checksをhypothesis_register直前へ追加する。
        "theme_design": ["audience_signal", "media_context", "deep_scenario", "discretion_assessment",
                         "directional_perception", "situation_actor_assessment",
                         "human_interaction_coverage", "expert_reasoning_checks", "hypothesis_register"],
        "research_design": ["audience_signal", "media_context"],
        "case_analysis": ["audience_signal", "media_context", "deep_scenario", "discretion_assessment",
                          "directional_perception", "situation_actor_assessment",
                          "human_interaction_coverage", "expert_reasoning_checks", "hypothesis_register"],
        "outline_review": ["media_context"],
        "draft_review": ["media_context"],
        "executive_brief": ["audience_signal"],
        "analyst_coaching": ["audience_signal"],
    },
}


def resolve_stage2_modules(mode: str, profile: str = "standard") -> list:
    """このmode×profileで実行すべきStage2モジュール名のリスト（重複なし、順序安定）を返す。"""
    modules = list(MODE_INTRINSIC_STAGE2_MODULES.get(mode, []))
    for extra in _PROFILE_ADDITIONAL_MODULES.get(profile, {}).get(mode, []):
        if extra not in modules:
            modules.append(extra)
    return modules

MODES = [
    "theme_design", "research_design", "case_analysis", "outline_review",
    "draft_review", "executive_brief", "analyst_coaching", "draft_generation",
]

# 07_KNOWLEDGE_OUTPUT_TEMPLATES.md の各テンプレートのうち、モードごとに重視すべき
# セクションをプロンプトへの指示として渡す（レスポンススキーマ自体はモード共通の1つ）。
# v1.2(Session2): Key Stakeholder-firstの分析順序をモード別に反映。geo_expert_quality_guard.py
# 側でモードごとの実質必須項目を検証する（欠落した場合は最大1回の修復生成を行う。
# geo_expert_engine.py参照）。
_KEY_STAKEHOLDER_FIRST_NOTE = (
    "v1.2 Key Stakeholder-first: 全ステークホルダーを横並びで分析する前に、まず"
    "key_stakeholder_assessmentで、この局面を実際に動かす／止めるKey Stakeholderが"
    "いるかを判定すること（determination=single/multiple/distributed/unclear）。"
    "明確な場合はkey_actor_profilesへ1〜3主体程度を優先して深掘りし（上限は固定しない）、"
    "persistent_preference（過去の複数行動から仮説化する持続的志向。今回の発言だけで"
    "決めない）とpersistent_preference_evidence（支持・反証の両方）、"
    "ultimate_objective→strategic_relationship_preference→issue_objective→"
    "observed_actionの目的階層、両者が一致しない場合はpreference_action_gapを"
    "Audience・支持基盤・選挙・産業・党内関係等のdriving_forcesで説明すること。"
    "determination=unclearでKey Actorを特定できない場合はkey_actor_profilesは0件のままでよいが、"
    "rationaleとidentified_knowledge_gaps（何が分かれば特定できるか）を必ず埋めること。"
    "根拠のないKey Actor・Audienceを作らない。"
)

# Session2 Reasoning Upgrade: key_point_assessmentは全モード・常時生成（Stage1所有）。
# key_stakeholder_assessmentより先に行う分析ステップであり、Reasoning Loopの起点として
# 常時・静的にsystem promptへ含める（_ACTOR_INTELLIGENCE_NOTEと同じ理由）。
_KEY_POINT_NOTE = (
    "Key Point Selection: key_stakeholder_assessmentで個別Actorを検討する前に、まず"
    "key_point_assessmentで「この局面を規定する蝶番（Key Point）は何か」を検討すること。"
    "最初に思いついたもっともらしい候補をそのまま採用しない。candidate_key_pointsへ"
    "2〜4件の候補を比較し、それぞれについてcontrol_type（actor_controlled/"
    "institutionally_constrained/structural/distributed/mixed/unknown、Key Pointを"
    "『誰が』ではなく『何が』動かしているかの分類）をまず判定した上で、Primaryを選んだ"
    "理由（selection_rationale）と他候補を棄却した理由（rejected_candidates、1〜3件）を"
    "明示すること。地政学のKey Pointは必ずしも単一Actorの意思で動くとは限らない"
    "（選挙結果・司法判断・原油価格・制度上の期限・軍事的能力・複数組織の相互拘束等）ため、"
    "Actorを無理に登場させず、control_type=institutionally_constrained/structuralの場合は"
    "controlling_entity_idsを空のままにしcontrol_structure_summaryで説明すること。"
    "control_type=actor_controlled/distributed/mixedの場合のみ、Foundation/Case Context"
    "上のEntity一覧に実在するentity_id（架空のIDを作らない）をcontrolling_entity_idsへ"
    "記入すること（key_actor_profiles[].actor_refとは別の識別子であり、まだKey Actorとして"
    "採用していない主体も参照できる）。hinge_structureでは局面が依存する条件を"
    "primary_hinge（単一の中心条件）とsecondary_hinges（補助的な条件、0〜3件）に分け、"
    "単一変数への過剰な単純化を避けること。primary_key_pointの選定根拠となる"
    "supporting_evidence_idsは必須（confidence=高の場合は特に1件以上）とし、"
    "disconfirming_signal（何を観測したらKey Pointの置き方自体を誤ったと判断するか）を"
    "必ず記述すること。disconfirming_signalは『分析の出発点（Key Pointの置き方）』に"
    "対する反証条件であり、main_scenario.retraction_conditions（主シナリオ自体の撤回"
    "条件）・alternative_scenarios.switch_conditions（主シナリオから代替仮説への切替"
    "条件）・focus_selection.discriminating_observation（Primary Focus選定の識別点）"
    "とは対象レベルが異なる——同じ『何を見れば結論が変わるか』という問いを、局面の"
    "出発点/Primary Focus/主シナリオ/代替仮説というそれぞれ別のレベルで問うものであり、"
    "重複して同じ内容を書く必要はない。\n"
    "Phase D-2(Holistic Reasoning Architecture Review): candidate_key_pointsは"
    "『この局面を規定する蝶番は何か（What matters）』の比較検討に限定すること。"
    "なぜそのActorがその行動を取るのか（Why Actor Acts）・今回の出来事の本当の意味は"
    "何か（What Event Means）という動機・解釈の競合はここでは扱わず、"
    "hypothesis_register（H0-H4の仮説競合、深いプロファイルでのみ生成）側の責務と"
    "する。候補間の違いはcontrol_typeの分類・Evidence・Actor Modelとの整合を中心に"
    "比較すること。\n"
    "Anti-overthinking rule: Direct Evidenceが最も多く支持し、Alternativeが追加の"
    "未証明の仮定を要し、Actorの実際の行動と整合し、反証が弱い場合は、Surface"
    "解釈（最も普通の説明）をそのままBest-fitとして採用してよい——非自明な結論を"
    "出すこと自体は目標ではない。primary_key_pointは、比較した候補のいずれもEvidence"
    "で十分に支持"
    "できない場合、無理にどれかを選ばず文字列\"unresolved\"としてよい（この場合"
    "selection_rationaleに何が分かれば決められるかを記述し、identified_knowledge_"
    "gapsへも反映すること）。"
)

# Focus Selection / Attention Prioritization（Session1・2＋過去Expert Review再分析、
# 2026-09-06追加）: key_point_assessmentは全モード・常時生成のため、_KEY_POINT_NOTEと同じ
# 理由で常時・静的にsystem promptへ含める。新しいFrame taxonomyではなく、既存
# key_point_assessment内に置くFocus Selection Layer——「複数のFrameがどれも成立し得る中で、
# 今この局面で何を最も見るべきか」を選ぶロジックを明示させる。
_FOCUS_SELECTION_NOTE = (
    "\nFocus Selection（Attention Prioritization）: 『Primary Key Point=唯一正しいFrame』"
    "にはしないこと。他の候補が正しくても、今この局面で次のActor行動・交渉・均衡・Scenario"
    "を読むためにdecision-sensitiveな論点をPrimary Focusとして選ぶ、という別の軸である。"
    "focus_selectionは以下のFocus Trigger（Phase D-2改訂: 各Triggerは観測された事実"
    "そのものではなく、『局面のどの思考Lensを起動するか』を選ぶものである。例えば"
    "『Congress』という語が出てきたこと自体はCue/Observationであり、"
    "behavior_driving_forceというLensを起動するかどうかは別の判断である——Triggerを"
    "選ぶことは結論を出すことではない）のうち、局面に意味のあるものだけ選択的に使うこと"
    "（固定チェックリストではない、通常2〜3個以内に絞る、全件を毎回生成・適用しない、"
    "該当しなければtrigger_types自体をnullのままにしてよい）:\n"
    "model_or_norm_mismatch(通常のActor model/institutional norm/prior patternと今回の"
    "観測が合わない。問い:本来こうのはずなのになぜ今回は違うのか)／"
    "decision_power(次の結果を実際に動かす・止める主体・決定点がある。問い:結局誰がこれを"
    "決められるのか)／higher_order_objective(目前の政策・措置がより上位の目的の手段かも"
    "しれない。問い:さらに上位でこのActorは何を実現したいのか)／"
    "behavior_driving_force(Actor labelだけでは立場を説明できない。問い:何がこの人に"
    "その行動を取らせているのか。constituency/election/donor/industry/faction/"
    "bureaucracy等)／audience_substance_gap(発言・政策が誰かへの見せ方であり実質と異なる"
    "可能性。問い:誰に見せたいのか、実際の措置・強制性はどれほど強いのか)／"
    "premise_or_constraint_change(過去の行動を説明した前提が現在も成立しているか。問い:"
    "その通説を成立させていた条件は今も残っているか)／"
    "implementation_or_feasibility(合意・予算・発言と実行可能性を分離。問い:実際に何に"
    "使うのか、人員・能力・物流・制度・資金は足りるか)／"
    "causal_chain_break(Actorの戦略が結果に至る途中の因果リンク。問い:本人はその行動を"
    "取るか、相手には応じる能力・意思があるか、相手が応じれば最終結果は本当に変わるか)／"
    "phase_or_time_horizon_shift(original objectiveとcurrent priority、short-term event"
    "とlong-term decision horizonのずれ。問い:今はBig Flowのどの段階か、当初の目的と"
    "今の最優先は同じか)／discriminating_hinge(複数解釈に共通する観測ではなく予想が"
    "分かれる箇所。問い:AとBで次に違うことが起こるのはどこか。該当する場合は"
    "discriminating_observationへその分岐点を記述すること)／"
    "media_or_source_anomaly(mainstreamとの差、selection/placement/prominence等。問い:"
    "なぜ今これなのか、なぜこの扱いなのか。anomalyはtruthではなくinvestigation trigger"
    "として扱うこと——観測した違和感をそのまま結論にしない)。\n"
    "Primary Focusは『最もEvidenceが多いもの』『質問文に最も近いもの』『最もドラマチック"
    "なもの』だけで選ばないこと。why_now（他の候補ではなくなぜ今これを見るか）と"
    "decision_sensitive_outcome（この論点の判断が変わればActor action/negotiation path/"
    "equilibrium/dominant flow/scenarioの見立てのうち何が変わるか）を必ず記述すること。"
    "selection_rationaleでは、candidate_key_pointsが複数ある場合、他のFrameも成立し得る"
    "こと・なぜそれらではなく今はこの論点をPrimary Focusにするか・どのdownstream "
    "judgmentが変わるかを明示すること。rejected_candidatesは『False/Wrong』ではなく"
    "『Not primary for the current situation』として扱うこと——他の局面・他の時点では"
    "Primaryになり得る候補として書くこと。\n"
    "Anomalyの扱い: 通説・Actor modelと合わない観測を見つけても自動的にFrameを反転させない。"
    "以下を区別すること: (1)source/evidence error（情報自体の誤り）、(2)missing "
    "situational context（単に背景情報が不足している）、(3)changed constraint/priority"
    "（前提条件が変化した）、(4)Human Model revision candidate（既存Actor理解の更新候補、"
    "ただし本Caseの中で確定させず候補のまま残す）、(5)genuine reframe（本当にFrame自体を"
    "疑うべき場合）。mainstreamと異なる情報・Mediaの強いprominence・Actor modelとの不一致は、"
    "結論ではなくFocus/Investigation Triggerとして扱うこと。\n"
    "Stage2/Candidate GenerationはこのPrimary Focusを参照すること。候補はActor→Goal→"
    "Perceived Path→Event Role→Expected Response→Desired Stateとして生成し、なぜこの局面で"
    "そのlinkを重点的に見るのかがfocus_selectionと整合していること。ただし"
    "actor_expected_response（本人の期待）とanalyst_expected_response（分析者の実際の"
    "予測）は、focus_selectionの追加によっても絶対に再統合・混在させないこと。"
)

# Cue→Targeted Expert Question Q2b（ユーザー指示、Controlled Test）: Q2a(investigation_
# questionsへの相乗り)はNegative Result（0/80発火）と判定され、Baselineへ戻した。
# investigation_questionsは「Evidence Gapを埋めるために何を追加調査すべきか」という
# 役割が強すぎ、解釈的な問いが吸収されてしまうため、独立フィールド
# expert_reasoning_questionsとして分離する。ただしQ1(reasoning_lens_application、15
# Grammar分類・answer_angles生成・hypothesis_registerタグ付け必須)より大幅に小さくする:
# 3軸のみ・rationaleの長い説明もタグ付け要求もしない。
_EXPERT_REASONING_QUESTION_NOTE = (
    "\nExpert Reasoning Question（局面をどう捉えるかという解釈的な問い。Evidence Gap"
    "収集が目的のinvestigation_questionsとは別の役割であり、両者を混同しないこと）: "
    "出力Schemaにexpert_reasoning_questionsが含まれる場合、以下3軸のいずれかについて"
    "明確なTrigger条件を満たす場合のみ、合計0〜2件を生成すること。Triggerに合致する"
    "ものが無ければ0件のままでよく、無理に追加しないこと（この項目を意識する前の"
    "既存Reasoningをそのまま通してよい）。以下の各軸は、条件をすべて厳密に満たす場合"
    "だけを待つのではなく、局面がその軸の『問題の型』に該当するとみなせるなら積極的に"
    "検討すること（過度に慎重になり過ぎて0件に倒れないよう注意する）。\n"
    "question_axis=feasibility: 金額・年限・組織名・軍事的意図など、決定・合意・"
    "指示が具体的な見出しとして示されているが、それを実際に実行する主体の規模・"
    "専門性・人員・体制・調達方法・予算内訳のいずれかが本文で具体化されていない場合。"
    "『防衛費を増やす』『占拠を検討している』『2027年までに準備を整えよ』のように、"
    "意図・目標水準は明確だが実行段階の中身が伴わない見出しは典型例。全ての実行"
    "要素が欠けている必要はなく、主要な1つでも欠ければ発火してよい。問い方の例:"
    "『実行主体は実際にそれを行う能力・体制を持つか、具体的に何が必要か』。\n"
    "question_axis=role_choreography: 同一issueについて関連する2名以上のActorが"
    "局面に登場し（同盟・同一政権内・同一ブロックだけでなく、OPEC/カルテル等の"
    "共同体メンバーシップ、上司・部下関係、同じ交渉チーム内の関係も含む）、そのうち"
    "少なくとも1名の発言・トーンと、別の1名（または同一Actorの言葉と行動）の間に"
    "見かけ上の不一致がある場合。もう一方のActorが明示的な固有名詞で登場していなくても"
    "（例: 『同盟国』『関係国』のような集合的言及でも）、役割分担の可能性が具体的に"
    "想定できるなら発火してよい。問い方の例:『両者の差は意図された役割分担か、それとも"
    "純粋な不一致か』。\n"
    "question_axis=premise_challenge: 観測された結果・差分を説明する単一の要因"
    "（『専門家の一致した見方』『既知の前提条件』『量的な差（備蓄量等）』のいずれか）が"
    "示されているが、(a)その要因だけでは観測された差・結果を十分に説明しきれない"
    "残差が疑われる、または(b)その前提が依拠する条件が局面の悪化・エスカレーション"
    "によって崩れる可能性がある、のいずれかに該当する場合。問い方の例:『この前提・"
    "説明変数だけで結果を説明しきれるか、局面が悪化しても成立し続けるか』。\n"
    "triggerには、上記のうちどの条件を満たしたかを局面固有の根拠で1文にまとめること"
    "（Trigger条件やGrammar名をそのまま書き写さない）。evidence_statusは、現在の"
    "Evidenceでこの問いに実質的な回答が可能ならsufficient、答えられないなら"
    "unresolvedとすること——unresolvedの場合、このQuestionだけを未解決のまま残し、"
    "hypothesis_register/main_scenarioの生成を止めたり簡略化したりしないこと。"
)

# Q3a Atomic Question Router（ユーザー指示）: Q2b(axis自己分類、単一配列)はv1で低recall、
# v2で文言強化するとrecallは上がったがpremise_challengeへのラベル収束・precision悪化を
# 招いた。原因はLLMに「どのaxisか」を分類させたこと自体にあると判断し、Q3aでは分類を
# やめ、4つの独立したチェック（固定Schema key、それぞれ自分自身のApplicable/Not
# Applicableだけを判定する）へ置き換える。互いに排他的なカテゴリとして扱わない
# （複数が同時にapplicable=trueになってもよい）。
_EXPERT_REASONING_CHECKS_NOTE = (
    "\nExpert Reasoning Checks（Q3a、局面をどう捉えるかという解釈的な問い。Evidence Gap"
    "収集が目的のinvestigation_questionsとは別の役割であり、両者を混同しないこと）: "
    "出力Schemaにexpert_reasoning_checksが含まれる場合、以下7つのチェックそれぞれを"
    "独立に判定すること。**どれか1つを『選ぶ』分類問題ではない**——7つとも順に検討し、"
    "各々が単独でTrigger条件を満たすかどうかだけを判定すること（2026-09-24追加の"
    "higher_order_objective_check/structural_benefit_check/internal_blocking_"
    "checkも、既存4チェックと同格の独立チェックであり、新しい分類体系ではない）。"
    "7つのうちいくつがapplicable=trueになってもよいが、局面に本当に効くもの以外は"
    "無理にtrueにしないこと（合計でapplicable=trueは多くとも3件程度を目安とする——"
    "4件以上該当しそうな場合は、trigger_basisが最も具体的に示せる3件に絞ること）。\n"
    "各チェックでapplicable=trueとする場合、trigger_basisへ局面固有の具体的根拠を"
    "1〜2個必ず示すこと。根拠を具体的な言葉で示せない場合はapplicable=falseとし、"
    "無理にtrueにしないこと（『なんとなく関係がありそう』は根拠にならない）。\n"
    "feasibility_check（Q08A Implementation Feasibility）: Action/commitmentが"
    "具体化しており、その実現に必要な人員・能力・専門性・資源・時間等が成否を左右する"
    "が、それらが本文で不明または不足している場合のみapplicable=true。Question基本形:"
    "『このActorがそれを望んでいるかではなく、実際に実行できるのか。何が実行上の"
    "ボトルネックか』。\n"
    "capability_vs_intent_check（Q08B Capability vs Intent）: 軍事能力・準備・期限・"
    "配備等についての記述が存在し、それが実際の実行意思・開始時期として誤読され得る"
    "場合のみapplicable=true。Question基本形:『これは実行意思を示しているのか、それとも"
    "実行可能な能力を整える目標・期限なのか』。feasibility_checkと同時にapplicable="
    "trueになってよい（実行主体の能力そのものが不明な場合はfeasibility、期限・配備の"
    "記述を意図と読み違えるリスクがある場合はcapability_vs_intentであり、両方成立する"
    "局面もある）。\n"
    "role_choreography_check（Q06A Role Choreography）: 2名以上の具体的Actorが局面に"
    "存在し、同一政権・同盟・陣営など協調可能な関係にあり、同一Counterparty/Issueに"
    "対して異なるトーンまたは行動を取っている場合のみapplicable=true。trigger_basisに"
    "は該当する2名以上のActor名を含めること。単独Actorの言行不一致だけでは"
    "applicable=falseとすること。Question基本形:『これは本当の内部対立なのか、それとも"
    "相手を動かすための意図的な役割分担なのか』。\n"
    "premise_invalidation_check（Q01A Premise Invalidation）: 現在の主要な見立てが"
    "特定可能な前提X（1文で名指しできる具体的な前提）に依存しており、現在の局面変化Y"
    "（同じく1文で名指しできる具体的な変化）がその前提Xを無効化し得る場合のみ"
    "applicable=true。trigger_basisにはXとYそれぞれを具体的に示すこと。XまたはYの"
    "どちらかでも『前提が変わるかもしれない』というだけの漠然とした感覚しか示せない"
    "場合はapplicable=falseとすること。Question基本形:『この見立てはXを前提としている"
    "が、Yが起きた今もXは成立するか』。\n"
    "higher_order_objective_check（Q06B Higher-order Objective、2026-09-24追加）: "
    "提示された目的（政策目的・二国間関係・国内政治目的・戦略的位置取り等）が複数"
    "並列で提示されている、または単一の目的のみが提示されているが、それがより上位の"
    "目的（例: 個別政策 → 二国間関係 → 国内政治目的 → 戦略的位置取り、という階層の"
    "いずれか）の下位手段である可能性が本文で検討されていない場合のみapplicable="
    "true。trigger_basisには、並列提示されている複数目的、またはより上位の目的が"
    "存在し得ることを示す具体的根拠を示すこと。『何か上位目的がありそう』という"
    "漠然とした感覚だけではapplicable=falseとすること。Question基本形:『この目的は"
    "独立した最終目標か、それともより上位の目的（関係維持・国内政治・戦略的位置"
    "取り等）のための下位要素か』。\n"
    "structural_benefit_check（Q03A Second-order Structural Benefit、2026-09-24"
    "追加）: 措置の直接的な対象・一次効果（誰に対して何をするか）は明示されている"
    "が、価格・競争力・物流・資源・市場構造等を通じた二次的な受益者・力学が本文で"
    "検討されていない場合のみapplicable=true。trigger_basisには、直接効果とは別の"
    "経路（価格上昇・競争力変化・供給構造の変化等）で得をする、または損をする主体が"
    "存在し得ることを示す具体的根拠を示すこと。一次効果の記述をそのまま二次的受益者"
    "として言い換えるだけの場合はapplicable=falseとすること。Question基本形:"
    "『直接効果の受益者ではなく、価格・競争力・物流・資源・市場構造等を通じて間接的"
    "に得をする主体は誰か』。\n"
    "internal_blocking_check（Q12A Internal Blocking/Reversal、2026-09-24追加）: "
    "政策・発言が提示された後、撤回・弱体化・変更されたという観測が本文にある場合の"
    "みapplicable=true（撤回・弱体化・変更の観測が無い局面では発火しない）。"
    "trigger_basisには、提示から撤回・弱体化・変更へ至った具体的な事実経過を示す"
    "こと。**押し返した可能性がある具体的な主体（制度・国内連合・支持基盤・官僚"
    "機構・安全保障機構・産業界等）は、Evidence/Retrieval/本文中の記述に基づく"
    "場合のみquestion・trigger_basisへ書くこと——根拠が無い場合は主体を創作せず、"
    "questionを『どの主体が押し返したか』という一般形のまま残し、hypothesis_"
    "register側でunresolvedとして扱うこと**（Evidence Substitution Guardと同じ"
    "規律。一般知識で紋切り型の主体を埋めない）。Question基本形:『提示から撤回・"
    "弱体化へ至った過程で、どの主体が押し返した可能性があるか』。\n"
    "evidence_statusは、上記7チェックいずれもapplicable=trueの場合のみ設定する: "
    "現在のEvidenceでこの問いに実質的な回答が可能ならsufficient、答えられないなら"
    "unresolvedとすること——unresolvedの場合、このQuestionだけを未解決のまま残し、"
    "hypothesis_register/main_scenarioの生成を止めたり簡略化したりしないこと。"
    "applicable=falseの場合、trigger_basis/question/evidence_statusはすべてnullの"
    "ままでよい。"
)

MODE_GUIDANCE = {
    "theme_design": (
        "「テーマ設計」の観点を重視する: レポート化する価値、中心仮説、主要な違和感、"
        "ステークホルダーと力学、検証する論点、反証条件、自社への接続、推奨構成、"
        "採用しない論点を明確にする。\n" + _KEY_STAKEHOLDER_FIRST_NOTE +
        " main_scenario.selection_rationale（なぜこれを主軸に置くか）を必ず記述する。"
    ),
    "research_design": (
        "「調査設計」の観点を重視する: additional_researchに調査問い・関連仮説・"
        "推奨情報源を具体的に列挙し、monitoring_indicatorsに専門家への質問・"
        "モニタリング指標を含める。\n" + _KEY_STAKEHOLDER_FIRST_NOTE +
        " additional_researchには、Key StakeholderのPersistent Preferenceを検証・"
        "反証するための具体的な調査項目（過去の類似局面・交渉行動等）を1件以上含めること。"
        "Audience仮説を根拠付きで示せない場合はaudience_analysisを空配列のままにし、"
        "その代わりidentified_knowledge_gapsに追加調査事項として記録すること。"
    ),
    "case_analysis": (
        "「標準ケース分析」の観点を重視する: 一言見立て、最初の違和感、"
        "ステークホルダーと目的・制約、背景の力学、主シナリオ、代替シナリオ、"
        "反証条件・不足情報、企業への影響経路、次に確認すること、担当者への学びを"
        "すべて埋める。\n" + _KEY_STAKEHOLDER_FIRST_NOTE +
        " audience_analysisには今回のKey Actorの発言・行動それぞれについてPrimary/"
        "Secondary Audienceとaction_substance（Signal/Substanceの区別）を記述する"
        "（根拠が無ければ空配列でよい）。main_scenario.selection_rationaleを必ず記述する。\n"
        "Phase3-0: 主要stakeholderについては、purpose/constraints_fearsに加えて"
        "position（本件における現在の立場・具体的にどちらへ動いているか）、"
        "leverage（本件で行使可能な交渉力・てこ）、ability_to_move_or_block"
        "（結果を動かす力・阻止する力）も可能な範囲で具体的に記述すること。"
        "案件全体としてdecisive_power_and_leverage（結果を決定づける力が"
        "最終的に誰にあるか）の見立てを示すこと。strongest_counterargument"
        "（反論の要約）とは別に、contradicting_evidenceに主シナリオと矛盾する"
        "根拠を複数列挙すること。これらの情報がSource Pack/Context中に無い場合は、"
        "無理に創作せずnullまたは「不明」と明記すること。"
    ),
    "outline_review": (
        "「構成案レビュー」の観点を重視する: 冒頭に中心見立てがあるか、Key Stakeholderが"
        "いるのに全主体を横並びから始めていないか、Key Stakeholderの上位目的・Persistent "
        "Preferenceが示されているか、Preference-Action GapがAudience・Driving Forceで"
        "説明されているか、見出しが時系列説明だけになっていないか、主要主体と内部勢力の分解、"
        "相手側の鏡像分析、背景の力学、調査項目と中心仮説のつながり、自社影響の妥当性、"
        "役員に何を考えてもらうかを、review_findings（severity=Critical/Major/Minor）として"
        "具体的に指摘する。"
    ),
    "draft_review": (
        "「ドラフトレビュー」の観点を重視する。最初に必ずroot_premise_checkを行うこと"
        "（06_KNOWLEDGE_REVIEW_OS.md）: (1)key_stakeholder_validity=Key Stakeholderの設定は"
        "正しいか、(2)persistent_preference_grounding=Persistent Preferenceが今回の発言だけ"
        "でなく複数の過去行動から根拠づけられているか、(3)objective_hierarchy_separation="
        "Ultimate Objective/Strategic Relationship Preference/Issue Objective/Observed Action"
        "が混同されていないか、(4)preference_action_gap_analysis=不一致がある場合にGapが"
        "検討されているか、(5)observed_action_not_treated_as_ultimate_objective=今回行動を"
        "そのまま上位目的とみなしていないか。5項目それぞれにresult(pass/warning/fail/"
        "not_assessable)とassessmentを記入し、overall_resultを付ける。overall_result=failの"
        "場合はスタートラインの誤りとしてreview_findingsにseverity=Criticalの指摘を"
        "少なくとも1件含めること。\n"
        "Root Premise Checkの後、見立て・ステークホルダー・証拠・反証・Audience/Signal・"
        "政治経済・企業接続・表現の各観点でCritical/Major/Minorを意識してreview_findingsへ"
        "指摘し、最優先の修正点を先に示す。良い点も残す。断定表現は機械的に弱めない"
        "（弱めるべきは根拠のない断定のみ。根拠ある主シナリオは維持し、根拠・留保・"
        "反証条件を補うよう指摘すること）。"
    ),
    "executive_brief": (
        "「役員向け要約」の観点を重視する: executive_summary（What changed/Why it "
        "matters/What to watch or decide）を中心に、詳細な思考過程は前面に出さず、"
        "結論を支える最低限の力学のみを示す。key_stakeholder_assessment.rationaleに"
        "「Who can move it」（局面を動かす主体とその上位目的を一言で）を記述し、"
        "business_impactで影響経路・時間軸・判断時期を明確にする。"
    ),
    "analyst_coaching": (
        "「担当者育成」の観点を重視する: analyst_coaching_notesに、最初に注目すべき"
        "言葉・行動、Key Stakeholderは誰だったか／なぜ一人に集約できなかったか、"
        "表面的な読み方になりやすい箇所、Persistent Preferenceを置く根拠、上位目的と"
        "局面目的の違い、見落としていたAudience・Driving Force、見落としていた"
        "ステークホルダー、必要だった基礎知識、仮説の作り方・反証方法、他テーマへの"
        "転用点を具体的に書く。結論を先に示し、その後に学びを短く添える"
        "（すべての思考ステップを毎回説明しない）。"
    ),
    "draft_generation": (
        "「IOS Native Report Draft生成」の観点を重視する。このモードの主な責務は"
        "draft_sectionsのみ——query_textで与えられるAnalysis Design（investigation_"
        "question/scope/hypotheses/alternative_hypotheses/known_evidence/evidence_"
        "needed/knowledge_gaps/key_actors）と局面を読む出力（main_scenario/"
        "alternative_scenarios/key_actor_profiles/monitoring_indicators）を根拠に、"
        "レポート本文をセクション単位の地の文として構成する。新しい分析・新しい"
        "主張は行わない——与えられたHypothesis/Evidenceの範囲を超えて新事実を"
        "創作しないこと。不確実な点は断定せず、仮説として明示する。\n"
        "draft_sectionsの構成は以下を基本とする（局面の内容次第で見出しは調整して"
        "よいが、Executive assessment/Key actors & constraints/Main scenario/"
        "Business implications/Indicators to watchの5観点は含めること）: "
        "(1) Executive assessment — 一言見立てと、この局面が重要な理由。"
        "(2) Key actors & constraints — 主要主体とその目的・制約。"
        "(3) Main scenario — 与えられたmain_scenarioの要約（新しいシナリオを作らない）。"
        "(4) Alternative scenarios — 与えられたalternative_scenariosがあれば要約。"
        "(5) Business implications — 与えられた情報から読み取れる自社への影響経路"
        "（確定事項と仮説を明確に分ける。無ければ「現時点で確認できる情報なし」と"
        "明記する）。"
        "(6) Indicators to watch — 与えられたmonitoring_indicators/watch_pointsを"
        "整理して提示する。\n"
        "各sectionには、そのsectionが根拠とした`known_evidence`のIDを"
        "supporting_evidence_idsとして残すこと（存在しないIDは創作しない。根拠が"
        "無い場合は空配列でよい）。\n"
        "one_line_assessment/claims/stakeholders/main_scenario/retraction_conditions"
        "等の共通必須フィールドは、新しく分析し直すのではなく、与えられたDesign/"
        "Situationの内容をそのまま要約・転記する形で満たしてよい（このモードの主目的"
        "はdraft_sectionsの執筆であり、CQAS分析のやり直しではない）。"
    ),
}

# Actor Intelligence Phase AI-3: staged経路ではstage1_system_promptがcase_idブロックより
# 前に構築されるため、「Packがある場合のみ動的に追記する」ことはできない。そのため
# _KEY_STAKEHOLDER_FIRST_NOTEと同じく常時・静的にsystem promptへ含め、文言自体を
# 「Packが提供されている場合は」という条件付き表現にする（承認済みPlan5章参照）。
_ACTOR_INTELLIGENCE_NOTE = (
    "\nActor Intelligence: 今回のuser prompt中に【Actor Intelligence Pack】が提供されて"
    "いる場合は、それをKey Stakeholder判定の最優先の材料として使う（提供されていない"
    "場合は本項を無視してよい）。Packのselection_reasons/relevance_scoreを踏まえ、この"
    "Actorがなぜ局面を動かせる／止められるのかをwhy_actor_mattersとして自分の言葉で"
    "説明すること（Retrieval側は理由の種別のみを渡しており、意味づけ・文章化はここで"
    "行う）。Core Objective（構造的・長期的）とCurrent Objective（今回の局面固有）を"
    "区別し、Red Line・使えるカード（available_leverage）・使えない/高くつくカード"
    "（unavailable_or_costly_cards）・脆弱性（vulnerabilities）を特定すること。過去の"
    "Decision Pattern（relevant_decision_patterns）は根拠（Evidence）が伴う場合のみ使う。"
    "Fact（observed_fact等）とInference（ai_inference等）を混同しない。current_postureに"
    "freshness.status=staleが付いている場合、それを現在の断定的事実として扱わず不確実性・"
    "更新の必要性を明示する。Actor単体でなくActor間の相互作用（relevant_relationships）も"
    "検討し、現在のDirection of Travelがなぜ生じているか、それを覆す証拠が何かを示す。\n"
    "Phase D-2(Holistic Reasoning Architecture Review): 【Actor Intelligence Pack】は"
    "candidate prior（検討候補の材料）であり、結論・確定事項ではない。Actor "
    "Intelligenceが豊富に提供されている主体を、それだけの理由でdecisive actor・Key "
    "Actorとみなさないこと。Key Stakeholder/Key Actorの選定は、あくまでKey Point/"
    "Control Point Discoveryの判断に基づいて行うこと——Packに情報が無い主体を選ぶこと、"
    "Packに情報が豊富でも局面に無関係なら選ばないこと、いずれも妨げられない。"
)

# Phase F改訂(Human Lens / Quality Signal、ユーザー指示): discretion_assessmentが
# Stage2 Schemaに含まれる場合（deepプロファイルのみ）のみ意味を持つ指示。Key Actors
# 特定の直後、Directional Perception/Situation Actor Assessmentの生成を試みる前に
# 行う軽量な事前判定（Reasoning Frameworkの「STRUCTURE-FIRST EXCEPTION」）。当初
# 「必須Gate」として設計したが、目的は推論の正しさの強制ではなく、不確実な事実から
# 最も説明力・意思決定関連性のある読み筋(Storyline)を作ることであるため、
# Human Interactionは推奨されるレンズ（観測すべき手がかり）として位置づけ、これを
# 理由にStoryline生成自体を止めてはならない。
_DISCRETION_MODE_NOTE = (
    "\nDiscretion Mode（Phase F改訂: Human Lens）: 出力Schemaにdiscretion_assessment"
    "が含まれる場合、Key Actorsを特定した直後、Directional Perception/Situation "
    "Actor Assessmentを試みる前に、この局面がHUMAN_DISCRETION_DOMINANT（少数の人物の"
    "裁量・思惑・相互作用がこの局面を動かす）か、STRUCTURE_DOMINANT（物理的現実・"
    "法制度・技術的不可能性・資源制約が支配的で人間の裁量は二次的）か、MIXED（両方"
    "併存）かを判定すること。STRUCTURE_DOMINANTの場合、Directional Perception/"
    "Situation Actor Assessmentは空配列のままでよく、human_interaction_coverage."
    "interaction_statusはNOT_APPLICABLE_STRUCTURE_FIRSTとしてよい。"
    "HUMAN_DISCRETION_DOMINANT/MIXEDの場合は、Directional Perception/Situation "
    "Actor Assessmentを人間模様として局面を読むための推奨レンズとして検討すること——"
    "ただし、これらが薄い・書ききれないという理由だけでHypothesis/Storyline生成を"
    "止めたり、件数を減らしたりしないこと（Human Interactionが薄いことは品質シグナル"
    "として観測されるが、生成の前提条件ではない）。discretion_assessment.discretion_"
    "modeは軽量な事前スクリーニングであり、key_point_assessment.control_type"
    "（Human Interaction/Hypothesis確定後の精緻な分類）とは役割が異なる——両者が"
    "食い違ってもよい（例: 事前にはSTRUCTURE_DOMINANTと見えたが、分析を進めると"
    "特定人物の裁量が重要だったと判明する、という局面自体があり得る。無理に一致させ"
    "ないこと）。"
)

# Session2 Reasoning Upgrade（Phase2）: situation_actor_assessmentがStage2 Schemaに
# 含まれる場合（deepプロファイルのみ）のみ意味を持つ指示。モジュールが発火していない
# リクエストではこのフィールド自体が出力対象に含まれないため、本文言があっても無害
# （_ACTOR_INTELLIGENCE_NOTEと同じ理由で常時・静的にsystem promptへ含める）。
_SITUATION_ACTOR_ASSESSMENT_NOTE = (
    "\nSituation Actor Assessment: 出力Schemaにsituation_actor_assessmentが含まれる場合、"
    "Key Actor × Issue Componentの組合せごとに、この局面固有の評価を行うこと（Persistent "
    "Actor Profileの恒常的な傾向とは別に、今回の局面でのみ成り立つ評価）。全Actor×全"
    "Componentの完全直積を作らず、Key Pointの説明に必要な組合せのみを選び、配列全体で"
    "12〜15行程度を上限の目安とすること。salienceは判断材料が無い場合、無理にhigh/"
    "medium/lowをつけず`unknown`を使うこと（confidence=高との組合せは矛盾として扱われる）。"
    "instrumental_valueでは、そのComponentをActorが本当に欲しているのか(intrinsic)、"
    "別の目的（交渉材料・国内向け信号・抑止等）のための道具として使っているだけか"
    "(instrumental/negotiating_card/domestic_signal/deterrence)を区別すること。"
    "stated_preference（表明された選好）とestimated_real_preference（実際の選好の推定）は"
    "区別し、genuine_or_tacticalでその立場がどこまで本気かを判定すること。"
    "primary_binding_constraint/primary_usable_leverageは複数列挙せず、最重要な1つに絞る"
    "こと。推論的な判断（estimated_real_preference/genuine_or_tactical/current_red_line/"
    "instrumental_value）についてconfidence=高とする場合はevidence_idsを伴わせること。"
)

# Session2 Reasoning Upgrade（Phase3）: media_source_contextがStage2 Schemaに含まれる場合
# （media_contextモジュール、deepプロファイルのみ）のみ意味を持つ指示。
_MEDIA_REASONING_NOTE = (
    "\nMedia/Information Reasoning: 出力Schemaにmedia_editorial_contextが含まれる場合、"
    "Observable（observed、意図を一切語れない事実表現）とHypothesis（hypotheses、編集意図・"
    "情報源シグナリング・受け手反応の仮説）を厳格に分離すること。「Actor Xへの言及が本文に"
    "存在しない」はobservedに書けるが、「意図的に省いた」はhypotheses側でのみ書くこと。"
    "**「Who benefits? はintentの証明ではない」**——利益を受けるActorがいるという事実だけから"
    "「そのActorが仕掛けた」と推定しないこと。匿名情報源（anonymous_unspecified）による"
    "hypothesisにconfidence=高をつける場合は、corroboration_basisが質的な裏付け"
    "（independent_corroboration等）を示し、かつcorroboration_targetがhypothesis_itself"
    "（出来事の発生ではなく、意図・source/originの主張自体が裏付けられている）である必要が"
    "ある。同一リークの複数媒体転載を「独立裏取り」と誤認しないこと。全てのhypothesesを"
    "confidence=高・status=supportedで埋めず、対立解釈や反証が無いか検討すること"
    "（不確実性の余地なく全て確信的に断定される「陰謀論的」パターンを避ける）。"
    "media_editorial_contextはPrimary Key Point（key_point_assessment.primary_key_point）"
    "との関係を中心に分析すること（両者を構造的にリンクするIDは無いため、本文中の記述で"
    "関係を説明する）。"
)

# Media Intelligence Build（M9）: 【Media Intelligence Pack】がuser prompt中に提供されて
# いる場合のみ意味を持つ指示（提供されていない場合は本項を無視してよい）。既存
# _MEDIA_REASONING_NOTEはmedia_editorial_context自体のObserved/Hypothesis分離を扱うが、
# 本ノートはPackのMedia Pattern Candidates/Media Profilesをどう参照するかを扱う（別関心事）。
_MEDIA_INTELLIGENCE_PACK_NOTE = (
    "\nMedia Intelligence Pack: 今回のuser prompt中に【Media Intelligence Pack】が提供されて"
    "いる場合、そこに含まれるMedia Pattern Candidates（複数の過去局面から導出された媒体の"
    "傾向）とMedia Profiles（媒体単位の比較的安定したIntelligence）を、今回の報道解釈の"
    "参照材料として使うこと。Patternは『過去に繰り返し観測された傾向』であり、今回の具体的な"
    "記事・報道の確定した観測ではない——Patternをそのまま今回の事実であるかのように書かず、"
    "media_editorial_context側では`outlet`に「(想定)」等その旨を明記し、`caveat`に一般的な"
    "Patternからの適用である旨を記すこと。Patternが今回の局面へ実際に当てはまるかどうかは"
    "自明ではない——`applies`/`partially_applies`/`does_not_apply`/`uncertain`のいずれに"
    "相当するかを、既存の`status`（supported/partially_supported/weak/unsupported/"
    "contradicted/unknown）で表現すること（Pattern自体を新しいSchemaフィールドで判定"
    "し直す必要はない）。pattern_role=exceptionのPatternが提供されている場合、それが"
    "示す『既定Patternが成り立たない条件』も踏まえること。"
)

# Media Intelligence Build（M9）: Actor Intelligence PackとMedia Intelligence Packが
# 両方とも提供されている場合のみ意味を持つ、両者を相互参照させるための指示。新しい
# Schemaフィールドは追加せず、既存のsituation_actor_assessment（estimated_real_preference/
# genuine_or_tactical/media_salience_signal/media_salience_basis/alternative_interpretation）・
# media_editorial_context・key_point_assessmentの中で表現させる。
_ACTOR_MEDIA_CROSS_REFERENCE_NOTE = (
    "\nActor × Media Cross-reference: 【Actor Intelligence Pack】と【Media Intelligence "
    "Pack】が両方提供されている場合、単純に別々のセクションとして並べるのではなく、"
    "相互に参照し合うこと。少なくとも次を検討すること: (1) 現在の公式な発言・立場は、"
    "そのActorのpersistent_preferenceと整合するか。(2) 使われている発信チャネルは、その"
    "Actorのstrategic_communication_patternと整合するか（普段と異なるChannelを使っている"
    "場合、それ自体がSignalかもしれない）。(3) その媒体の報道は、Media Profileの"
    "historical_framing_characteristics/typical_source_typesと整合する、いつも通りの"
    "振る舞いか、それとも異常な逸脱か。(4) 観測された行動（observable_actions相当）は、"
    "公式発言を裏付けるか、それとも乖離があるか。(5) 乖離がある場合、それは最終目的の"
    "変化ではなく、単なる交渉上のポジション（negotiating position）である可能性はないか。"
    "(6) 他に合理的な代替説明（alternative_interpretation）はないか。"
    "ActorのPattern単独、MediaのPattern単独では説明が弱い場合でも、両者を掛け合わせると"
    "より説得力のある説明になることがある——その場合は、Actor側の示唆・Media側の示唆・"
    "現在の行動の3点を明示的につなげてestimated_real_preference/alternative_"
    "interpretationを記述すること（例:「Actor Patternは選好Aを示唆するが、Media Patternは"
    "解釈Bを示唆し、現在の行動はCと整合する。したがってAだけでは不十分、Bはもっともらしいが"
    "Cと矛盾する部分があり、総合評価はD」という形の三段階の統合を意識する）。Actor/Media"
    "いずれかのPatternのみで説明が完結する場合は、無理に両方を関連づけようとしないこと"
    "（存在しない関係を作らない）。"
    "\n重要な区別: 『Actorが過去にXする傾向がある』という観測は、『Actorは今回もXするという"
    "確定した事実』を意味しない——過去のPattern（Actor/Media双方）はあくまで参照材料であり、"
    "今回のEvidenceによる裏付けが無い限り、Pattern単独を根拠にconfidence=高とはしないこと。"
    "Actor IntelligenceとMedia Intelligenceが示唆する解釈が矛盾する場合、一方を黙って"
    "無視せず、alternative_interpretation・contradicting_evidence_ids等で両方を明示し、"
    "どちらがより説得力があるかの判断根拠を示すこと。"
    "\nM9.1 Evidence Weighting: Historical Actor Pattern・Historical Media Patternは"
    "『もっともらしさの文脈（contextual prior/plausibility）』であり、『今回の主張を"
    "裏付ける独立したCurrent Evidence』ではない。この3つを混同しないこと: "
    "(a) historical_pattern（Actor/Media双方の過去傾向）、(b) current claim（今回の"
    "匿名情報源等による具体的な主張そのもの）、(c) current independent corroboration"
    "（今回のCaseにおける、別系統の実名情報源・公式行動・実際の行動・物的証拠・独立した"
    "文書等）。**Actor Patternが示唆する解釈とMedia Patternが示唆する解釈が同じ方向を"
    "指しているという理由だけで confidence を高くしないこと**——これは2つの"
    "Historical Prior（過去の傾向という参照材料）が一致しただけであり、2つの独立した"
    "Current Evidenceが一致したことにはならない。匿名情報源に基づくhypothesisで"
    "confidence=高とする場合は、(c)current independent corroborationが必要であり、"
    "(a)のみ・または(a)と(b)の組合せのみでは不十分（corroboration_target=underlying_"
    "event_onlyまでしか裏付けられていない場合はconfidence=高にしないこと、既存の"
    "corroboration_basis/corroboration_targetの契約を参照）。"
)

# Session2 Reasoning Upgrade（Phase4）: investigation_questionsがStage2 Schemaに含まれる場合
# （situation_actor_assessmentモジュール、deepプロファイルのみ）のみ意味を持つ指示。
_INVESTIGATION_QUESTION_NOTE = (
    "\nInvestigation Questions: 出力Schemaにinvestigation_questionsが含まれる場合、"
    "Reasoning中に生じた問いをanalytical_question（Reasoning内で処理する分析的な問い）/"
    "evidence_check（既存Evidenceで答えられるか確認する問い）/knowledge_gap（外部情報が"
    "真に不足している問い）に区別すること。can_current_evidence_answer=trueの場合は"
    "answer_if_yesに回答自体を書き、answer_evidence_idsへ根拠を1件以上示すこと（根拠なく"
    "『答えられる』と自己申告しないこと）。can_current_evidence_answer=falseの場合は"
    "answer_if_yesを必ずnullにすること（この場合は決定的処理でadditional_researchへ自動的に"
    "反映されるため、ここで無理に回答を書かないこと）。固定質問リストを機械的に全件実行せず、"
    "rationaleでKey Point/Key Actor/Persistent Preference/Preference-Action Gap/"
    "Knowledge Gapのいずれかを名指しで参照すること。3〜5件程度を目安とする。\n"
    "Question Stability Guard（CQAS v1、Prompt attention安定化のためのMinimal Fix）: "
    "この局面にDecision-relevantなCue、または未解決の分析的緊張（Key Point/Key Actor/"
    "Persistent Preference/Preference-Action Gap/Knowledge Gapのいずれかに関する、まだ"
    "解消されていない問い）が存在する場合、それに直接対応するinvestigation_questionsを"
    "少なくとも1件は生成すること。investigation_questionsを空配列のまま返してよいのは、"
    "そうした未解決の分析的問いが本当に存在しない場合のみである——他の出力（hypothesis_"
    "register等）が長くなったことを理由に、この項目だけを省略しないこと。"
)

# Cue→Targeted Expert Question（Q2a、ユーザー指示）: Cue→Grammar→Question Transformation
# Pilot(Q1、reasoning_lens_application)を実験として一旦終了し、15 Grammarの分類選択は
# 行わない、より狭い代替アプローチ。過去Expert Corpus実例による裏付け(Evidence
# provenance)が比較的強いG08/G06/G01の3種＋既存Preference-Action Gap検出への条件付き
# 追加としてのG12のみを対象とする（G13/G07は本人実例未確認のためHOLD）。新規Schema
# field は追加せず、既存investigation_questions（situation_actor_assessmentモジュール、
# deepプロファイルのみ、新規Azure呼び出しなし）へ相乗りする。
_TARGETED_EXPERT_QUESTION_NOTE = (
    "\nTargeted Expert Question（Cue→Targeted Expert Question）: investigation_"
    "questionsの通常生成に加えて、以下4つの明確なTrigger条件のいずれかを満たす場合に"
    "限り、Targeted Expert Questionを追加できる。機械的に毎回4種すべてを試さないこと、"
    "**合計0〜2問まで**——Triggerに合致するものが無ければ0問のままでよく、無理に追加"
    "しないこと（Triggerが無い局面では、この項目を意識する前の既存Reasoningをそのまま"
    "通してよい）。各Targeted Questionはrationaleの冒頭で、どのTrigger種別に基づくかを"
    "「[G08]」「[G06]」「[G01]」「[G12]」のいずれかで明記すること（該当しない通常の"
    "investigation_questionsにはこのタグを付けない）。\n"
    "[G08] Implementation Feasibility Trigger: 意図・決定・合意が見出しとして示されて"
    "いるが、それを実行する主体の具体的な規模・専門性・体制・調達方法が本文に一切示され"
    "ていない場合。Ask: 実行主体は実際にそれを行う能力を持つか、具体的に何が必要か。\n"
    "[G06] Role Choreography Trigger: 同一issueについて、既に役割分担の関係（同盟・"
    "同一政権内・同一ブロック内等）があるとみなせる2名以上のActorが、異なるトーン・"
    "行動を見せている場合。単独Actorの言行不一致だけでは発火しないこと——もう一方の"
    "相方Actorがkey_actor_profiles等で局面に明示されていることを条件とする。Ask: "
    "両者の差は意図された役割分担か、それとも純粋な不一致か。\n"
    "[G01] Premise Challenge Trigger: 『専門家は皆この見方で一致している』のような"
    "コンセンサス的前提が示されており、かつその前提が依拠する条件が、局面の悪化・"
    "エスカレーションによって崩れる可能性がある場合。Ask: この前提は局面が悪化しても"
    "成立し続けるか。\n"
    "[G12] Beneficiary Question（既存Preference-Action Gapへの条件付き追加、独立した"
    "Grammarとしては扱わない）: key_actor_profiles[].preference_action_gapが埋まって"
    "いる場合（Actor自身のPersistent Preferenceと今回のObserved Actionが一致しない"
    "場合）に限り、追加で最大1問だけ検討してよい。Ask: このActor自身の利害と矛盾する"
    "なら、この結果を最も望む・最も得る主体は他に誰か。preference_action_gapが空、"
    "またはActor自身の利害と行動が整合している局面では発火しないこと。\n"
    "Evidence不足の扱い: Targeted Questionを立てたが現在のEvidenceでは答えられない"
    "場合、can_current_evidence_answer=falseとしてそのQuestionだけをunresolvedの"
    "まま残すこと（question_type=knowledge_gapとし、answer_if_yesはnullのまま）。"
    "これを理由にhypothesis_register/main_scenarioの生成を止めたり簡略化したりしては"
    "ならない——Targeted Questionはあくまで補助的な検討材料であり、Hypothesis/"
    "Scenario生成全体の前提条件ではない。"
)

# Expert AI Depth & Grounding Upgrade: 内部Reasoning工程の整理（20 stepの要約）。
# 最終回答をこの20項目全てで冗長に埋める必要はない——重要なのはThinking内部で仮説を
# 競わせることであり、ユーザー向け出力は17章のOutput Philosophyに従い簡潔にする。
_DEEP_REASONING_SEQUENCE_NOTE = (
    "\nDeep Reasoning Sequence（Phase F改訂: Human Lens / Storyline）: 以下の"
    "内部工程を意識してReasoningすること（全てを冗長に書き出す必要はないが、飛ばさない"
    "こと。特に、先にKey Point/Framingを決めてからKey Actor・Human Interactionを後付けで"
    "埋める、という順序を取らないこと）: (1)Facts/Signals：何が起きているかを正確に"
    "押さえる (2)Surface Interpretation：普通に読むと何が起きているか (3)Hidden "
    "Assumptions：その読みが暗黙に前提としているもの (4)Key Actors：この局面を動かせる"
    "／止められる少数の人物を先に特定する (4b)Discretion Mode：この局面がHuman "
    "Interaction分析を要するか、物理的・法制度的・技術的・資源的な低裁量構造が支配的かを"
    "判定する (5)Actor Intelligence Retrieval (6)Human Lens/Directional Perception："
    "特定したActor同士が互いをどう認識しているか（推奨されるレンズであり、薄くても"
    "後続を止めない） (7)Human Lens/Situation Actor Assessment：Actor×Issue "
    "Componentごとのこの局面固有の評価・本人の期待(actor_expected_response)と分析者の"
    "予測(analyst_expected_response)の分離 (7b)Human Interaction Coverage：(6)(7)を"
    "試みた結果をGROUNDED/PARTIAL_EVIDENCE/INSUFFICIENT_EVIDENCE/NOT_APPLICABLE_"
    "STRUCTURE_FIRSTのいずれかで言語化する（Quality Signal） (8)Key Point Candidates："
    "ここまでのFacts/Human Lensを踏まえて、この局面の蝶番が何かを判定する "
    "(9)Control Structure (10)Actor Pattern Applicability Test (11)Actor Pattern "
    "Failure Test (12)Media/Information Intelligence Test (13)Storyline Generation/"
    "Competing Hypotheses：この局面はこういう話ではないか、という読み筋を複数生成する"
    "（あればinteraction_basisでActor Interactionへ接続する） (14)Frame-Breaking "
    "Hypothesis/Wildcard (15)Structural Alternative (16)Storyline Selection/"
    "Evidence Competition：Evidence Fit→Actor Model Fit→Explanatory Power→Decision "
    "Relevance→Narrative Salienceの順で比較する (17)Contradicting Evidence "
    "(18)Hypothesis Pruning (19)Primary Storyline選定 (20)Executive Implication/"
    "Watch：経営は何に備えるべきか、何が観測されたら読み筋を変更するか "
    "(21)Disconfirming Signals (22)Knowledge Gaps。目指すのは『逆張りをたくさん言う"
    "AI』でも『Human Interactionを埋めることが目的化したAI』でもなく、不確実な事実"
    "から最も説明力・意思決定関連性のある読み筋を作ることである。Conventional "
    "Readingを一度疑い、Actor/Media/Structureの前提そのものを壊す仮説まで探索し、"
    "Evidenceで競争させ、間違った仮説を棄却した上で最も説明力の高いStorylineへ"
    "戻ってくる。深い分析に見えることより、根拠のない深読みをしないことを優先する。"
    "EvidenceがなければUnknownで止まることは失敗ではなく正しい挙動である。"
    "Directional Perception/Situation Actor Assessmentは、根拠のあるActorペア／"
    "Componentを特定できない場合は空配列でよく、その状態はhuman_interaction_"
    "coverageで言語化するとよいが、これを理由にStoryline生成自体を止めたり簡略化"
    "したりしないこと。"
)

_COMPETING_HYPOTHESES_NOTE = (
    "\nCompeting Hypotheses / Hypothesis Register（Phase D-2改訂）: 出力Schemaに"
    "hypothesis_registerが含まれる場合、以下のtaxonomyで仮説を生成し記録すること。"
    "hypothesis_typeはH0_straightforward/H1_instrumental_indirect/"
    "H2_counterparty_audience_shift/H3_constraint_domestic_driver/H4_frame_breaking"
    "の新taxonomyのみを使うこと——surface/actor_intent_alternative/"
    "structural_alternative/actor_model_breaking/frame_breakingは過去レスポンス互換"
    "のためのlegacy値であり、新規生成では使わないこと:\n"
    "H0_straightforward(最も普通の説明。表向きの目的がそのまま実際の目的)／"
    "H1_instrumental_indirect(表向きの行動・目的が、より上位の目的のための手段・間接的"
    "な経路である可能性。preference_action_gap（本人のPersistent Preferenceと今回の"
    "Observed Actionの不一致）が観測される場合は特にこの仮説を検討すること)／"
    "H2_counterparty_audience_shift(この行動が本当に動かしたい・見せたい相手が、表向き"
    "の対象とは異なる可能性。key_actor_profiles[].event_role.intended_target_actor_ref"
    "が示す対象と表向きの対象が食い違う場合は特にこの仮説を検討すること)／"
    "H3_constraint_domestic_driver(対外的な行動に見えるが、実際には国内政治・制度上の"
    "制約・支持基盤が主因である可能性)／H4_frame_breaking(質問・既存Narrative・既存"
    "Actor Intelligence自体の前提が誤っている可能性——既存Actor Modelが今回のCaseに"
    "適用できない場合の検討も、ここへ統合して行う。旧actor_model_challengeが個別に"
    "担っていた検討はここで行い、独立フィールドとしては新規に書き込まない)。\n"
    "H4_frame_breakingを検討する際は、単に『前提を疑う』だけでなく、既存Actor "
    "Intelligence（Historical Pattern/Persistent Preference）が今回のCaseに適用でき"
    "ない具体的な理由をreasonへ記述すること。H4は面白い結論を出すためではなく探索空間を"
    "広げるために使い、必ずEvidence Competitionを通し、Evidenceが無ければ"
    "status=rejectedとして棄却すること（棄却した仮説も削除せず記録として残す）。各仮説"
    "にstatus(retained/rejected/unresolved)を必ず付け、全件がretainedのみにならない"
    "ようにすること（少なくとも1件は棄却または未解決として扱う）。質問文がAlternative"
    "を与えていない場合でも、H1〜H4は自力で生成すること（Neutral Promptでの検証対象）。\n"
    "Ranking（reasonフィールドでの評価）: 各仮説のreasonでは、以下7つの観点を"
    "明示的に参照して評価すること——(1)Evidence Fit(evidence_relevance/"
    "claim_relevanceが示す、この仮説を直接支持するEvidenceの強さ)、(2)Actor Model "
    "Fit(persistent_preference/relevant_decision_patternsとの整合。Historical "
    "Patternがこの仮説を支持するか、既存Actor Modelでは説明できないか)、"
    "(3)Relationship Fit(directional_perceptionsが示す、対象Actor間の認識・力学と"
    "この仮説が整合するか)、(4)Means/Timing Fit(perceived_path/event_roleが示す手段・"
    "タイミングの選択と、この仮説が想定する動機が整合するか)、(5)Counterparty Response "
    "Fit(situation_actor_assessment[].actor_expected_responseとanalyst_expected_"
    "responseが、同一のtarget_actor_refについて意味的に食い違う＝認識のズレがあるか、"
    "ズレがある場合それ自体がこの仮説にとって重要な材料か否かを判断すること——ズレの"
    "有無・重要性の評価はこのreason内でLLMが行うものであり、Guard側は"
    "actor_expected_response/analyst_expected_responseが同一のtarget_actor_refを"
    "共有しているかという接続性のみを機械的に検証する、意味比較は行わない)、"
    "(6)Decision Relevance(今この局面で経営・意思決定者が備えるべき判断にとって"
    "この仮説がどれだけ重要か。Evidence上は成立しても意思決定に関係が薄い仮説は"
    "優先順位を下げてよい)、(7)Narrative Salience(この仮説を人間模様として語った"
    "とき、理解・記憶・意思決定準備を助けるか。例えば『米中政策には複数の力学が"
    "あります』より『TrumpはXiとの取引余地を残したい。一方、議会強硬派はその自由を"
    "縛ろうとしている。Xiはその亀裂を見ている』の方が経営者には伝わりやすい——ただし"
    "Narrativeを面白く・分かりやすくするためにEvidenceを曲げたり、根拠のない具体性を"
    "足したりしてはならない。(1)〜(3)Evidence/Plausibility/Explanatory Powerが"
    "同程度の複数仮説が拮抗する場合にのみ、(6)(7)を優先順位付けの決め手として使う"
    "こと（Evidence Fitが弱い仮説をNarrative Salienceだけで格上げしない）。\n"
    "Interaction Basis（Phase F改訂: Human Lens）: H0以外の各仮説について、"
    "interaction_basisで根拠とするActor Interactionへの構造的接続を示すことが望ましい"
    "（必須のGateではなく、Human Interactionをどれだけ読み込めたかを可視化する"
    "Quality Signal）。basis_type=DIRECTIONAL_PERCEPTION/SITUATION_ACTOR_ASSESSMENT"
    "を選ぶ場合、actor_ref/target_actor_refはdirectional_perceptions/situation_"
    "actor_assessmentの実在レコードと一致する組にすること（存在しない組を作らない）。"
    "event_role（行動の機能分類）はDirectional Perception/Situation Actor "
    "Assessmentの代替として意味が薄いため、特にH1_instrumental_indirect/"
    "H2_counterparty_audience_shift/H4_frame_breakingでは、対応するdirectional_"
    "perceptions/situation_actor_assessmentの実在レコードがあれば優先してそれを"
    "使うこと。ただし、これらのレコードが薄い・存在しない場合でも、そのことを理由に"
    "仮説自体の生成を止めたり質を落としたりしないこと——Facts/Signals・制度的制約・"
    "観測可能な指標等、他の根拠でHypothesisを組み立ててよい。H3_constraint_"
    "domestic_driverは、hypothesis_typeで一律にinteraction_basisの要否を決めない"
    "こと——この仮説が具体的な人物・支持基盤・議会・Audience等、特定の主体との"
    "相互作用を主張している場合はinteraction_basisで示し（例: 『議会の特定議員団への"
    "配慮』はHuman Interactionである）、純粋な制度上の制約・能力上の限界・物理的"
    "制約のみが理由の場合はbasis_type=NOT_APPLICABLEを使ってよい（gap_noteで、なぜ"
    "特定の人物間相互作用を主張しないかを説明すること）。human_interaction_coverage."
    "interaction_statusがPARTIAL_EVIDENCE/INSUFFICIENT_EVIDENCEの場合は、basis_"
    "type=EVIDENCE_GAPを使ってよい（相互作用データの不在自体をこの仮説の論点にする"
    "場合。gap_noteに何が欠けているかを記述すること）。\n"
    "Primary Storylineの選定: retainedとした仮説のうち最も筋が良いもの1つを、"
    "main_scenario.derived_from_hypothesis_idで指し示すこと。これは『唯一の正解』"
    "ではなく『現時点で入手できているEvidenceの範囲で、最も説明力・蓋然性・意思決定"
    "関連性のバランスが良い読み筋(Primary Storyline)』という位置づけであり、新しい"
    "情報が入れば変わり得る。他のretained/unresolvedな仮説はAlternative Storyline"
    "（拮抗する別の読み筋）やWildcard（低確率だが重要な読み筋、典型的にはH4）として"
    "alternative_scenariosへ反映すること。main_scenarioを確定する際は、(1)Primary "
    "Storylineとする仮説を1つ特定する→(2)その仮説が依拠するActor Interaction"
    "（あれば。directional_perceptions/event_role/actor_expected_response等）や"
    "Facts/制度的制約を特定する→(3)そこからmain_scenarioを導出し、main_scenario."
    "derived_from_hypothesis_idへその仮説のhypothesis_idを記録する→(4)拮抗する"
    "仮説があれば、採用した場合に何が変わるかをalternative_scenarios（該当する"
    "derived_from_hypothesis_idも記録）へ反映する、という順序を実際に踏むこと。"
    "business_impact（Executive Implication、この読み筋なら経営は何に備えるべきか）"
    "とmonitoring_indicators（Watch、何が観測されたらPrimary Storylineを変更すべき"
    "か）を、選定したPrimary Storylineと整合させること。\n"
    "Phase 8-4(Question-to-Hypothesis Handoff、ユーザー指示): 出力Schemaに"
    "expert_reasoning_checksが含まれ、その7チェック（feasibility_check／"
    "capability_vs_intent_check／role_choreography_check／premise_invalidation_"
    "check／higher_order_objective_check／structural_benefit_check／internal_"
    "blocking_check）のいずれかが既にapplicable=trueとして生成されている場合、その"
    "内容がこの仮説集合にとってmaterialかどうかを一度検討すること——具体的には、その"
    "問いが(a)仮説を分岐させる（複数の異なる仮説を生む）、(b)既存の仮説が前提と"
    "する見立てに挑戦する、(c)ある仮説の説明力を弱める、(d)拮抗する仮説群を"
    "判別する材料（discriminator）として働く、のいずれかに該当するかを見ること。"
    "materialに働く場合のみ、該当する仮説のreason／interaction_basisや、"
    "拮抗する仮説間の判別根拠へ反映すること。すべてのexpert_reasoning_checksを"
    "機械的にHypothesis化する必要はなく、既に生成済みの仮説集合や判別に実質的な"
    "変化をもたらさない場合は、そのままでよい（無理に仮説を追加・修正しない）。\n"
    "2026-09-24追加チェックとhypothesis_type taxonomyの対応（新しいtypeは増やさず、"
    "既存H0-H4taxonomy内のreasonへ反映するcoverageとして扱う）: "
    "higher_order_objective_checkがmaterialな場合は、上位目的の階層をH1_"
    "instrumental_indirectのreason（表向きの目的がより上位の目的の手段である"
    "可能性）へ反映すること——上位目的の候補が複数ある場合、階層の異なる複数の"
    "H1系仮説として書き分けてよい。structural_benefit_checkがmaterialな場合も"
    "同様にH1_instrumental_indirectのreasonへ、直接効果の対象ではなく二次的な"
    "受益者を反映すること。internal_blocking_checkがmaterialな場合は、H3_"
    "constraint_domestic_driverのreason（国内政治・制度上の制約・支持基盤が"
    "主因である可能性）へ、押し返した可能性のある主体（Evidence/Retrieval根拠が"
    "ある場合のみ具体名、無ければ不明のまま）を反映すること。premise_"
    "invalidation_checkがmaterialな場合は、既存の指示通りH4_frame_breakingの"
    "reasonへ、前提X・局面変化Yを明示的に反映すること（H4は『質問・既存Narrative・"
    "既存Actor Intelligence自体の前提が誤っている可能性』を担う唯一のtypeであり、"
    "premise_invalidation_checkの自然な受け皿である）。これらはあくまでreasonへの"
    "反映であり、新しいhypothesis_typeを作らない・Gold固有の主体名を書き込まない"
    "ことを厳守する。\n"
    "Phase 10-A(Supporting Evidence Binding、ユーザー指示): claims[].source_ref、"
    "hypothesis_register[].supporting_evidence_ids、main_scenario.evidence_"
    "relevanceは、そのclaim／仮説／主シナリオの主張を直接支持するmaterial evidence"
    "が実際にSource Pack／Case Context上に存在する場合、そのevidence_id（document_"
    "id/event_id/claim_id等の実在ID）へ結合すること。局面全体には関連するが、その"
    "claim／仮説自体の直接の根拠ではない背景情報（Context Only）を、結合先を空に"
    "しないためだけにsupporting evidenceとして扱わないこと。結合可能な実在IDが無い"
    "場合はnullまたは空配列のままでよく、存在しないIDを創作しない。\n"
    "Phase 10-B(Weakening/Contradicting Evidence Structured Binding、ユーザー"
    "指示): main_scenario.weakening_evidence_relevance及びhypothesis_register[]."
    "weakening_evidence_relevanceは、その読み・仮説を弱める（relation=WEAKENS）"
    "または直接反証する（relation=CONTRADICTS）material evidenceが実際に存在する"
    "場合にのみ結合すること。同じevidence_idが、ある仮説ではsupporting_evidence_"
    "ids側、別の仮説ではweakening_evidence_relevance側に入ってよい——Evidenceの"
    "意味的な方向（支持／反証）は仮説ごとに独立して判断する。該当するEvidenceが"
    "存在しない場合は空配列のままでよく、反証Evidenceを全仮説・全シナリオへ機械的"
    "に付与しないこと。一般知識のみに基づく推測的な反論（Evidence IDを伴わない"
    "もの）をここへ入れず、それはreasonやalternative_scenariosの記述範囲で扱う。"
    "supporting_evidence_ids側の内容をそのままweakening_evidence_relevance側へ"
    "複製しないこと（両者は異なる意味的判断の結果でなければならない）。"
)

_ACTOR_PATTERN_APPLICABILITY_NOTE = (
    "\nActor Pattern Applicability Test（P0-3、6-7章）: relevant_decision_patternsで"
    "Historical Patternを参照したら自動適用しないこと。key_actor_profiles[]の"
    "pattern_applicability_assessmentへ、そのPatternについて(a)過去にどんな行動"
    "パターンがあったか(pattern_summary)(b)今回と共通する条件(current_similarities)"
    "(c)今回と異なる条件(current_differences)(d)過去にこのPatternが崩れた例外"
    "(exception_evidence_ids)(e)適用可能性(applicability: high/medium/low/"
    "uncertain)(f)結論(conclusion、『Historical Patternは今回の解釈の蓋然性を上げるが"
    "独立した証明にはならない』という区別を明示)を記録すること。Main Scenarioの根拠に"
    "Historical Patternを使う場合は必須。『既存Actor Intelligence自体が今回のCaseに"
    "適用できない場合はどうなるか』という問いは、Key Actorごとに内部で検討すること"
    "——ただしPhase D-2改訂により、この検討はhypothesis_registerのH4_frame_breaking"
    "仮説として記録すること（actor_model_challengeはdeprecatedのため、新規には"
    "書き込まないこと）。"
)

_EVIDENCE_SUBSTITUTION_NOTE = (
    "\nEvidence Substitution Guard（P0-4、8-9章）: Evidence不足を、モデル一般知識・"
    "Actorの紋切り型イメージ・弱い類推・もっともらしい地政学的ナラティブで埋めないこと。"
    "situation_actor_assessment[]/media hypothesesのevidence_classへ、根拠の性質"
    "(current_direct_evidence/current_indirect_evidence/historical_actor_pattern/"
    "historical_media_pattern/structural_reference/model_general_knowledge/"
    "unsupported_assumption)を明示すること。historical_actor_pattern/historical_"
    "media_pattern/model_general_knowledge/unsupported_assumptionのみを根拠とする"
    "場合、confidenceは高・中にできない（決定的処理で自動的に低へ引き下げられる）。"
    "Actor Intelligenceに存在しない内容を一般知識で自動的にActor Profileとして"
    "埋めた場合はkey_actor_profiles[].actor_intelligence_provenance."
    "general_knowledge_used=trueを明示すること。Evidence不足の場合の望ましい順序: "
    "Evidence不足の自覚→confidence引き下げ→unsupported_actor_assumptionの明記→"
    "knowledge_gapとして記録→additional_researchへ反映。Evidence不足時に無理に"
    "回答を完成させることより、Unknownで止まることを優先する。"
)

_WEAK_ANALOGY_NOTE = (
    "\nWeak Analogy Guard（10章）: main_scenarioのsupporting_evidenceで過去の別事案"
    "（特に別ドメインへのcross-domain analogy、例: Hormuz海峡通航料事案→半導体輸出"
    "規制のような類推）を使う場合、historical_analogies_usedへsame_actor/"
    "same_domain/same_institutional_structure/same_leverage_mechanism/"
    "same_constraintsを評価しanalogy_strength(strong/medium/weak)を明示すること。"
    "Cross-domain analogyは原則analogy_strength=weakまたはmediumから評価を開始し、"
    "weakのanalogyだけではMain Scenarioのconfidenceを高・中へ引き上げる根拠にしない"
    "こと（illustrative only、決定的処理で検出対象）。"
)

# Tier1(Eguchi Expert AI Final v1.0対応): key_actor_profiles[].perceived_path/event_role
# はStage1所有（常時・全プロファイルで出力対象）のため常時・静的に含める
# （_ACTOR_INTELLIGENCE_NOTEと同じ理由）。
_PERCEIVED_PATH_EVENT_ROLE_NOTE = (
    "\nActor Flow: perceived_path / event_role（Tier1、ActorFlow）: key_actor_profiles[]の"
    "perceived_pathには、そのActor本人がultimate_objective/issue_objectiveをどうすれば"
    "実現できると考えているか（本人の因果的信念、誰をどう動かすつもりか）を記述すること。"
    "分析者がその経路の実効性を保証する記述にしないこと——本人の想定と、分析者が見る"
    "実効性は別に扱う（実効性自体はaction_substance等の既存フィールドで論じる）。"
    "event_roleには、今回の出来事がそのperceived_path上で何の役割を果たすと解釈するか"
    "を、role_type（固定enumではなく自由記述、例: goal/means/bargaining_card/signal/"
    "constraint/obstacle/concession/institutionalization等に近い語を選ぶ）と"
    "explanation（判断根拠）で記述すること。『圧力そのものが目的なのか、別の目的の"
    "ための手段なのか』を区別するのがこの項目の狙いである。加えて、Phase D-2"
    "(Holistic Reasoning Architecture Review): この一手（役割）が誰に対する手段・"
    "シグナルかを特定できる場合、intended_target_actor_refへkey_actor_profiles[]."
    "actor_refで記入すること（例: bargaining_card/signalであれば、それが向けられて"
    "いる相手）。特定の相手を想定しない、または相手を特定できない場合はnullのままで"
    "よい。根拠が薄い場合は無理に埋めず、いずれもnullのままにしてよい。\n"
    "Phase F(Human Interaction Analytical Gate): event_roleは、観測された行動が"
    "どういう機能（役割）を果たすかの分類であり、Aの主観的認識(directional_"
    "perceptions)やAの期待(situation_actor_assessment)そのものではない。event_role."
    "role_type/intended_target_actor_refを埋めたことをもって、Directional "
    "Perception/Situation Actor Assessmentの検討を済ませた・省略してよいとは"
    "みなさないこと（3つは別の問いに答えている: event_role=この行動は何のための"
    "ものか、directional_perceptions=Aは相手をどう見ているか、situation_actor_"
    "assessment=Aは相手の反応をどう期待し、分析者はどう予測するか）。"
)

# Tier1(Eguchi Expert AI Final v1.0対応): directional_perceptionsがStage2 Schemaに含まれる
# 場合（directional_perceptionモジュール、deepプロファイルのみ）のみ意味を持つ指示。
_DIRECTIONAL_PERCEPTION_NOTE = (
    "\nDirectional Perception（Tier1）: 出力Schemaにdirectional_perceptionsが含まれる場合、"
    "AがBについて持つ（と分析者が評価する）帰属付きの主観的認識を記録すること。これは"
    "Bの客観的な実態・能力そのものの記述ではない——observer_actor_ref(A)/"
    "target_actor_ref(B)を明確に区別し、A→Bの認識をB→Aの認識へ反転させたり、Aの認識を"
    "Bの実際の属性としてHumanModel(core_profile等)側へ書き写したりしないこと。1件の"
    "A→B認識と対応するB→A認識が両方成り立つ場合は別々の2行として記録すること（相互に"
    "転記・複製しない）。claim_typeでこの認識の根拠の性質を区別すること: stated(Aが"
    "直接そう発言している)/inferred_from_behavior(Aの行動から推定)/inferred_from_source"
    "(第三者情報源からの推定)/analyst_hypothesis(根拠が薄い分析者仮説、confidenceは"
    "低にすること)。issue_scopeでこの認識が及ぶ争点・領域を明示し、無限定な人格特性へ"
    "一般化しないこと。key_actor_profiles[]に登録されていない主体をobserver_actor_ref/"
    "target_actor_refに使わないこと（架空のactor_refを作らない）。根拠のあるペアを"
    "特定できない場合は空配列のままでよい。【Actor Intelligence Pack】が提供されている"
    "場合、各Actorのrelevant_relationships（相手・方向(この主体→相手/相手→この主体)・"
    "dependency_direction・leverage_direction・confidenceを含む、既存Relationship DB"
    "からの決定的な取得結果）を、directional_perceptionsを組み立てる際の根拠材料として"
    "使うこと——ゼロから推測するのではなく、蓄積された関係情報がある場合はそれを踏まえ、"
    "claim_typeをinferred_from_source寄りにできる。ただしrelevant_relationshipsは"
    "客観的な関係の分類であり、それ自体はperception（Aの主観的認識）ではない——そこから"
    "『Aは自身のBに対するleverageをどう認識しているか』等を推論する場合はclaim_typeを"
    "analyst_hypothesisとし、推論過程を示すこと。"
)

# Tier1(Eguchi Expert AI Final v1.0対応): situation_actor_assessmentがStage2 Schemaに
# 含まれる場合（deepプロファイルのみ）のみ意味を持つ指示。likely_reaction一本化による
# 本人の期待と分析者の予測の混同を防ぐ。
_ACTOR_ANALYST_RESPONSE_SEPARATION_NOTE = (
    "\nActor Expectation / Analyst Prediction Separation（Tier1）: 出力Schemaに"
    "situation_actor_assessmentが含まれる場合、actor_expected_response（このActor自身が"
    "相手はこう反応するはずだと考えている、という本人の期待・想定）と、"
    "analyst_expected_response（相互作用・制度・能力・資源の制約を踏まえて分析者が実際に"
    "起こると予測する反応）を分けて記述すること。両者を同じlikely_reactionとして混在させ"
    "ないこと（likely_reactionは後方互換のため残すが、新規に書く場合は上記2フィールドへ"
    "分けること）。本人の期待と分析者の予測が一致しない場合、その落差自体を重要な分析"
    "対象として扱うこと（一致しないことは異常ではない）。Phase D-2"
    "(Holistic Reasoning Architecture Review): target_actor_refへ、"
    "actor_expected_response/analyst_expected_responseが『誰の反応』についての"
    "ものかを、key_actor_profiles[].actor_refで記入すること（両フィールドで同じ相手を"
    "指すため1つのtarget_actor_refを共有する。この行のactor_refとは別の主体を指す）。"
    "特定できない場合はnullのままでよい。この落差（認識のズレ）が重要な場合は、"
    "hypothesis_registerのreasonでCounterparty Response Fitとして評価すること"
    "（本フィールド自体には評価結果を書かない、Guardは接続性のみを機械的に検証する）。"
)

# Phase F改訂(Human Lens / Quality Signal、ユーザー指示): human_interaction_coverageが
# Stage2 Schemaに含まれる場合（deepプロファイルのみ）のみ意味を持つ指示。Directional
# Perception/Situation Actor Assessmentの生成を試みた後の自己申告——これは「観測」の
# ためのSignalであり、後続のHypothesis/Storyline生成を止めるGateではない。
_HUMAN_INTERACTION_COVERAGE_NOTE = (
    "\nHuman Interaction Coverage（Phase F改訂: Human Lens）: 出力Schemaに"
    "human_interaction_coverageが含まれる場合、Directional Perception/Situation "
    "Actor Assessmentの生成を試みた結果を、以下4状態のいずれかで自己申告すること"
    "（検討せずnull・空配列のまま無言で済ませず、状態として言語化すること。ただし"
    "この申告自体はHypothesis/Storyline生成の前提条件ではなく、AIがどの程度『人で"
    "読んだか』を可視化するためのQuality Signalである）: GROUNDED(directional_"
    "perceptions/situation_actor_assessmentのいずれかに根拠十分な内容がある)／"
    "PARTIAL_EVIDENCE(一部のみ根拠がある。何が欠けているかをidentified_knowledge_"
    "gapsへ記録するとよい)／INSUFFICIENT_EVIDENCE(検討したが根拠を見出せなかった。"
    "directional_perceptions/situation_actor_assessmentが空でもよい。何のActorペア・"
    "認識・期待についての情報が欠けているかをidentified_knowledge_gapsへ記録できると"
    "なおよい)／NOT_APPLICABLE_STRUCTURE_FIRST(discretion_assessment.discretion_"
    "mode=STRUCTURE_DOMINANTの場合のみ選択可能)。identified_knowledge_gapsに記録した"
    "欠落のうち、外部情報で解消できる可能性があるものは、investigation_questions"
    "（question_type=knowledge_gap）へ、それを確認するために何を調べるべきかを記述"
    "すること（identified_knowledge_gaps=何が分からないか、investigation_questions="
    "それを解消するために何を調べるか、という役割分担であり、両者を混同しないこと）。"
    "Human Interactionが薄い局面（INSUFFICIENT_EVIDENCE/NOT_APPLICABLE_STRUCTURE_"
    "FIRST）でも、そのことを理由にhypothesis_register/main_scenarioの生成自体を"
    "省略したり簡略化したりしないこと——その場合はFacts/Signals・制度的制約・"
    "観測可能な指標を軸にStorylineを組み立てればよい。"
)

# Cue→Grammar→Question Transformation Pilot（Blind Benchmark v0.1、ユーザー指示）:
# 30例Blind Benchmarkの結果、Cue検知（initial_anomaly/key_point_assessment）自体は
# 概ね機能している一方（Full/Partial=約9割）、そこから専門家らしい具体的な問い
# （Question）・競合する読み筋（Answer Angle）への変換で失速することが分かった
# （Question Full一致率は2割程度）。またQuestionで分岐した事例の大半は、問いの言い回しが
# 弱いのではなく、そもそも局面に合ったLens（Grammar）を選べていないことが原因だった。
# 本Noteはこの変換ステップを明示化する——新しいHypothesis taxonomyではなく、
# hypothesis_register(H0-H4)を生成する直前に置く「良い問いを作るための中間レンズ」
# として位置づける。H0-H4のtaxonomy自体・Storyline Selection・Actor/Relationship
# Intelligence・既存Guardは変更しない。
_REASONING_LENS_NOTE = (
    "\nReasoning Lens Application（Cue→Grammar→Question Transformation Pilot）: "
    "出力Schemaにreasoning_lens_applicationが含まれる場合、key_point_assessment/"
    "initial_anomalyで捉えた違和感から、hypothesis_registerを生成する前に、以下15種の"
    "Reasoning Lens（Grammar、江口氏の実例からReverse Engineeringされた思考操作）のうち"
    "局面に効くものだけを選んで適用すること。**固定枠ではない——普通の局面で特に裏読みが"
    "要らないと判断すれば0件でよい。**毎回2〜4件を機械的に埋めないこと。目安はPrimary "
    "Question 1件＋必要ならSecondary 1〜2件程度（多ければ良いわけではない、この局面で"
    "本当に効く問いだけを選ぶこと）。\n"
    "G01 Premise Challenge（Trigger: 既存説明が平時・単一要因の前提に依存／Ask: その前提は"
    "今も成立するか）／G02 Actor Model Violation（Trigger: 行動が既知の利益・支持基盤・"
    "人物像と噛み合わない／Ask: この人が本当にこんなことをするのか）／G03 Incentive "
    "Consistency（Trigger: Surface説明がActorの利害と矛盾／Ask: 誰の利害に適うか、誰が"
    "一番得するか）／G04 Theater / Audience Shift（Trigger: 行動が過度に公開的・演出的／"
    "Ask: 本当に表向きの相手向けか）／G05 Political Survival / Support-base / "
    "Relationship Constraint（Trigger: 選挙・後継競争・支持基盤・政治家との信頼に接続／"
    "Ask: 本人の政治生命・支持基盤・評判・記憶から見ると何が合理的か）／G06 Means-Goal / "
    "Higher-order Objective / Role Choreography（Trigger: 見えている行動を目的として"
    "読むと不自然、複数Actorの役割分担が疑われる／Ask: これは目的か手段か、さらに上位の"
    "Goalは何か、誰と役割分担しているか）／G07 Timing / Preparation / Sequence "
    "Inference（Trigger: 内容と時期・準備時間・発言と行動の順序が噛み合わない／Ask: なぜ"
    "今か、いつから準備していたか）／G08 Implementation Feasibility / Capability vs "
    "Intent（Trigger: 意図・目標は語られるが能力・人員・手段の話が抜けている／Ask: 本当に"
    "できるか、これは意図か能力整備の期限か）／G09 Absence / Omission / Inaction "
    "Signal（Trigger: いるはずの人がいない、執行すべきなのに動かない／Ask: なぜ無い・"
    "いない・止めないのか）／G10 Source Intent / Frame Inversion（Trigger: Sourceの説明が"
    "特定Actorの利害と綺麗に一致、または世間の因果認識と逆に見える／Ask: このSourceは"
    "なぜこの説明をしたいのか）／G11 Domestic Hinge / Micro-to-Macro Cascade（Trigger: "
    "一見ローカルな選挙・人事・州政治が国際局面の継続条件を変えうる／Ask: どの国内Eventが"
    "国際Storylineの蝶番か、その先の連鎖は）／G12 Win-win / Blocker Mapping（Trigger: "
    "2者dealが成立し得る一方、第三者に強い損失がある／Ask: 誰がこのdealを壊したいか、"
    "能力はあるか）／G13 Precedent / Analogy / Actor Learning（Trigger: 現在の判断の"
    "直前に大きな成功・失敗がある、または類似した過去事例が存在／Ask: その過去経験は"
    "Actorの期待・自信・リスク許容度をどう変えたか）／G14 Power / Dependency "
    "Reversal（Trigger: 表面上強いActorが実は相手の協力・資源・許可を必要としている／"
    "Ask: 実際には誰が誰を必要としているか、拒否権・依存はどちらにあるか）／G15 "
    "Revealed Concern / Meta-signal（Trigger: Actorが特定の質問・確認・懸念を繰り返す／"
    "Ask: それを聞くこと自体、何を不確実・重要と見ていることを示すか）。\n"
    "各適用について、trigger_matchでこのLensを選んだ具体的な観測根拠を示し、"
    "investigation_questionはGrammarのAsk型をそのまま書き写さず、今回の局面固有の"
    "問いとして書き下すこと。angle_status=angles_availableの場合はanswer_anglesへ"
    "具体的で局面固有の競合する読み筋を2〜4件書くこと（Grammarカタログの抽象カテゴリ名を"
    "そのまま書かない）。**G13/G08/G12は特に、問いを立てることと、その問いに答えることを"
    "分けてよい**——過去の類似事例や実行能力の具体的な中身がBlind Inputに無い場合、"
    "無理に類推・数字を捏造せず、angle_status=evidence_insufficientとしてanswer_anglesを"
    "空のままにし、investigation_questionだけを残すこと（その場合、何が分かれば答えられる"
    "かをidentified_knowledge_gaps/additional_researchへ反映するとよい）。\n"
    "直後に生成するhypothesis_register（H0-H4taxonomyは変更しない）は、ここで立てた"
    "investigation_question/answer_anglesを直接の出発点にすること。各hypothesis_"
    "register[].reasonの冒頭に、対応するreasoning_lens_application[].grammar_idを"
    "角括弧で機械的に追跡できる形で必ず記すこと（例:「[G06] Evidence Fit: ...」）。"
    "reasoning_lens_applicationのどのLensにも対応しない仮説（reasoning_lens_application"
    "で拾わなかった側面から追加生成したH0-H4）の場合は「[NONE]」と書くこと（この場合の"
    "追加生成自体は妨げられない、このLensは唯一の仮説源ではない）。"
    "reasoning_lens_applicationで拾わなかった側面からH0-H4を追加生成すること自体は"
    "妨げられない——このLensは出発点であり、唯一の仮説源ではない。"
)

# Phase D-2(Holistic Reasoning Architecture Review): hypothesis_registerのH1-H4生成に
# 直結する少数のOperatorへ再構成（旧10種・具体例なしの版から縮小・具体化）。
# candidate_key_pointsはWhat matters専用に責務を戻したため、本Operatorはhypothesis_
# register生成にのみ適用する（新規Operator別LLM callやDB tableは作らない）。
_CANDIDATE_GENERATION_OPERATORS_NOTE = (
    "\nCandidate Generation Operators（Phase D-2改訂）: hypothesis_registerのH1〜H4を"
    "生成する際、以下の少数の変換型（Operator）を局面に有効なものだけ選んで適用すること"
    "——毎回全型を機械的に適用しない（H0は変換不要、表向きの説明そのものである）:\n"
    "Goal/Means→H1_instrumental_indirect（表向きの目的を、より上位の目的のための手段と"
    "読み替える。例: 『輸出規制の強化』という措置は、それ自体が目的ではなく対中抑止と"
    "いう上位目的の道具である可能性を考える）／Counterparty Shift→"
    "H2_counterparty_audience_shift（この行動が働きかけたい・見せたい本当の相手を、"
    "表向きの対象とは別だと読み替える。例: 対中強硬発言は、実際には国内議会・産業界"
    "向けのシグナルである可能性を考える）／Changed Constraint→"
    "H3_constraint_domestic_driver（対外的行動に見える動きを、国内政治・制度上の制約・"
    "支持基盤が主因だと読み替える。例: 強硬姿勢の継続は選挙・党内関係上の要請が主因で"
    "ある可能性を考える）／Frame-Break Check→H4_frame_breaking（質問・既存Narrative・"
    "既存Actor Intelligence自体の前提を疑う。例: 過去のDecision Patternが成立していた"
    "条件（同盟関係・経済状況・指導部構成等）が既に変化していないか考える）。\n"
    "各候補(hypothesis_register[].reason)は、単なる動機の羅列ではなく、Actor（誰が）→"
    "Goal（何のために、key_actor_profiles[].ultimate_objective/issue_objective）→"
    "Perceived Path（どう実現できると考えているか、perceived_path）→Event Role（今回の"
    "出来事の位置づけ、event_role.role_type/intended_target_actor_ref）→Expected "
    "Response（相手の反応、situation_actor_assessment[].actor_expected_response/"
    "analyst_expected_response、同一target_actor_refで対応づけること）→Desired State"
    "（得たい着地、situation_actor_assessment.desired_outcome）というつながった読みとして"
    "記述すること。複数の解釈がある場合はhypothesis_register側でこのつながりの違いと"
    "して表現し、単一の目的をProfileへ固定して書き込まないこと。"
)

# Expert Reasoning Core v0.1（ユーザー指示）: Session1/2・過去メールのReverse Engineeringで
# 支持されたReasoning習慣を、既存Schema（hypothesis_register/key_point_assessment/
# main_scenario/alternative_scenarios/monitoring_indicators）を変更せず、Prompt層のみで
# 本線Reasoningへ反映する。Feature Flag（GEO_EXPERT_REASONING_CORE_ENABLED、既定OFF）が
# ONの場合のみ、geo_expert_engine._build_system_promptがこのNoteを末尾へ追加する
# （hypothesis_register/situation_actor_assessmentが発火するprofile×mode組合せのみ、
# 現状deep×theme_design/case_analysis）。MODE_GUIDANCEの静的連結には含めない
# （Baseline OFFとの比較を可能にするため）。
#
# 最重要原則（Q1/Q2/Q3aの失敗から）: 以下はGrammar/axisの分類器ではない。「今回どれに
# 該当するか」を選ばせるのではなく、局面にCueがある場合にのみ独立に使える思考ツールの
# 一覧として提示する。0個の使用が正常であり、該当しないものを無理に使わないこと。
REASONING_CORE_V1_NOTE = (
    "\n\n===== Expert Reasoning Core v0.1 =====\n"
    "以下は、局面にCueがある場合にのみ独立に使える思考ツール(Reasoning Operation)の"
    "一覧である。**分類器ではない**——「このケースはR3」のように1つを選ぶのではなく、"
    "該当するCueがあるものだけを、複数でも0個でも自由に使ってよい。"
    "hypothesis_register(H0-H4taxonomyは変更しない)・key_point_assessment・"
    "main_scenario・alternative_scenarios・monitoring_indicatorsという既存Schemaへ"
    "そのまま反映すること。新しいField・新しいラベルは作らない。\n"
    "\n--- Reasoning Operations（該当する場合のみ使う） ---\n"
    "R1 Premise Invalidation: 表面的結論Cが前提Xに依存し、局面変化YがXを崩し得る場合、"
    "『その結論は今も成立する前提に基づくか』を確認する。genericな反対意見を作る為には"
    "使わない。\n"
    "R2 Incentive Contradiction → Beneficiary Search: 名目Actorの行動がその利害と明確に"
    "矛盾する場合、本当に本人の意思か・誰が得するか・誰が阻止したいかまでActor探索を"
    "広げる。Evidence無しに具体Actorをfact扱いしない(hypothesis_registerのconfidence/"
    "evidence_idsで根拠の強さを正直に示す)。\n"
    "R3 Words vs Actions: 発言と行動が矛盾する場合、rhetoric/actual behavior/audience/"
    "tactical signaling/role choreographyを分離する。行動を強いsignal候補として扱って"
    "よいが、機械的に『行動が常に真実』とはしない。\n"
    "R4 Role Choreography: 複数Actorが同一政権・同盟・coalition・cartel等の協調可能な"
    "関係にあり、同一Issue/Counterpartyへ異なる姿勢を見せる場合、genuine conflictだけ"
    "でなく意図的な役割分担もCandidateにする。Relationship contextが無ければ無理に"
    "生成しない。\n"
    "R5 Implementation Feasibility: 意図・政策・計画が提示されていても、人員・専門能力・"
    "Logistics・Resources・Authority・予算・物理的能力・工程が成否を左右する場合、"
    "motiveより先に『そもそも実行できるか』をHard Constraintとして確認する。\n"
    "R6 Capability ≠ Intent: 能力整備・準備期限・配備目標等を、政治的意思・実行時期と"
    "自動的に同一視しない。Capability/Readiness/Intent/Decision/Executionを分ける。\n"
    "R7 Timing / Preparation-time: ContentよりTimingの方が異常な場合、なぜ今か・この"
    "行動にはどれだけ準備時間が必要だったか・観測時点より前にDecisionされていた"
    "可能性は無いかを検討する。\n"
    "R8 Absence / Comparator: 『存在しないこと』をsignalにするには、Expected baseline・"
    "比較可能なActor/Case・通常期待される行動、のいずれかが必要。単なる欠落だけで"
    "意味を付与しない。\n"
    "R9 Source Intent / Signal Separation: Sourceに政治的利害・機関としての立場・"
    "client寄りの視点・編集方針・希望的観測がある場合でも、Source全体を捨てない。"
    "Sourceの解釈と、観測可能な基底のsignalを分けて扱う。\n"
    "R10 Actor Model Violation: 観測行動が既存Actor Modelと矛盾しても、単発でCore "
    "Actor Profileを書き換える結論にしない。まず(1)Source/事実誤り(2)局面固有の制約"
    "(3)Current posture/issue position変化(4)Relationship/Audience効果、の順で検討"
    "し、それでも説明できない場合のみ(5)Core Actor Model自体の修正候補として"
    "H4_frame_breakingで扱う。\n"
    "R11 Dependency / Effective Power: 形式上の上下関係だけでPowerを判断しない。誰が"
    "誰を必要としているか・誰が代替手段を持つか・誰が拒否権/leverageを持つかを見る。\n"
    "R12 Counterparty Reaction / Achievement Path: Key Actorが『やりたいこと』だけで"
    "Storylineを作らない。Actor Aが目標Xの為にBを動かす必要があり、BがYという行動を"
    "取り、YがC/対象条件に影響する、という経路が現実的に成立するかを見る。相手側の"
    "合理的反応が成立しないならそのStorylineの優先度を下げる。\n"
    "R13 Political Equilibrium / Multi-objective Compromise: 複数Actorが対立する局面"
    "で、『誰が勝ったか』だけでなく、異なるUltimate Objectiveを持つ複数Actorが同じ"
    "Actionへ一時的に合流した可能性も検討する。\n"
    "\n--- Question Priority（固定順位ではなく優先度の目安） ---\n"
    "1. Hard Gate: その答えがNOならStoryline自体が成立しない問い(実行可能か・"
    "Authorityがあるか・前提が成立するか・必要Actorを実際に動かせるか)を最優先する。\n"
    "2. Causal Proximity: Observed Cue/違和感を最も直接説明する問いを、背景説明より"
    "重くする。\n"
    "3. Storyline Leverage: その答えが変わればPrimary Storyline自体が変わるか。"
    "変わらないならSecondaryへ落としてよい。\n"
    "4. Discriminating Power: 複数Storylineのどちらでも同じ答えになる問いは、Primary"
    "診断としては弱い。\n"
    "\n--- Answer Angle生成(Cueの型に応じて検討する変換候補、taxonomyとして出力しない) ---\n"
    "Incentive conflict→stated motive/hidden constraint/different actor/"
    "beneficiary・blocker／Words≠Actions→words本音/actions本音/theater/role "
    "choreography/audience segmentation／Absence→deliberate avoidance/alternative "
    "channel/incapacity/low priority/noise／Timing anomaly→prior preparation/"
    "hidden or deleted plan/audience timing/coercive signaling／Actor model "
    "mismatch→source error/situation change/relationship constraint/posture "
    "shift/core actor change／Source bias→genuine assessment/desired outcome/"
    "client framing/narrative shaping/underlying observable signal／Capability "
    "evidence→readiness/deterrence/bureaucratic milestone/actual intent。\n"
    "\n--- Hypothesis Pruning(hypothesis_register[].statusをrejectedにする判断材料) ---\n"
    "A.Feasibility(物理・制度・能力的に成立するか)／B.Actor Incentive(Actorの目的・"
    "制約と整合するか)／C.Observed Behavior(実際の行動を説明できるか)／"
    "D.Counterparty Reaction(相手側の合理的反応まで成立するか)／E.Cue Resolution"
    "(最初の違和感を説明できるか)／F.Evidence Compatibility(Evidenceと大きく矛盾"
    "しないか)／G.Assumption Burden(未証明の追加仮定を大量に要しないか)。\n"
    "\n--- Composition Logic ---\n"
    "異なる因果レイヤー(例: Actor motive+国内Audience+Relationship leverage+Timing"
    "制約)を説明する複数の要素は、1つのhypothesis(またはmain_scenario)へ統合して"
    "よい(Composition)。一方、同じ現象について競合する説明(例: genuine conflict vs "
    "coordinated theater)は、無理に1つへ合成せず、別々のhypothesis/"
    "alternative_scenariosとして残すこと(Competing)。この判別に絶対のルールは無い"
    "——迷う場合は無理に統合しない方を選ぶ。\n"
    "\n--- Storyline Selection（main_scenario選定時に総合する要素） ---\n"
    "Primary Storylineは『Key Actorが最も望む未来』ではなく、現在の観測を最も一貫して"
    "説明し現実的に進行し得る読みである。(1)Hard constraintsを通るか(2)Actorの目的・"
    "制約を説明するか(3)Counterpartyの反応まで説明するか(4)Cue/違和感を解消するか"
    "(5)Observed evidenceと整合するか(6)Explanatory Compression(より少ない追加仮定"
    "で多くを説明できるか)(7)他候補と異なるWatchpoint/discriminatorを持てるか、を"
    "総合する。Narrative salience/executive usefulnessはTie-breakerとしてのみ使い、"
    "Evidenceより上位に置かない。\n"
    "\n--- Hold Logic ---\n"
    "Primaryを無理に1つ作らないこと。(a)2つ以上のStorylineが十分成立し(b)現Evidence"
    "がどちらとも整合し(c)両者を識別するDiscriminatorが未観測、の場合は、判断不能/"
    "Hold/competing framesを正常な結果として扱ってよい(main_scenario.confidenceを"
    "低くするだけでなく、alternative_scenariosへ拮抗する複数案を残し、"
    "monitoring_indicatorsに識別点を明記する)。Confidenceが低いこととHoldは同義では"
    "ない——Holdは『現情報では識別できない』という分析状態そのものである。\n"
    "\n--- Bold Hypothesisの扱い ---\n"
    "Candidate生成のEvidence閾値と、Primaryへ昇格する閾値は別にしてよい。Actor利害と"
    "表面的説明が強く矛盾する場合、別Actor/false flag等の大胆なCandidateを生成する"
    "こと自体は妨げない。ただし、(1)factとして書かない(2)evidence_status/"
    "confidenceを正直に示す(3)supporting/weakening双方のevidenceを持たせる(4)弱い"
    "evidenceならstatus=unresolvedまたはalternativeとして保持する(5)Primary昇格には"
    "上記Storyline Selectionのより高い整合性を要求する。大胆な仮説を考えないAIにも、"
    "大胆な仮説を事実扱いするAIにもしないこと。\n"
    "\n--- Belief Update(既存Actor Modelとの矛盾) ---\n"
    "新しいEvidenceが既存Actor Modelと矛盾しても、一度の矛盾でCore Profileの書き換え"
    "を主張しないこと。R10と同じ順序(Source/事実誤り→局面固有の制約→Current "
    "posture/issue position変化→Relationship/Audience効果→反復する矛盾ならCore "
    "Actor Model更新候補)で検討し、反復的な矛盾が無い限りは局面固有の説明(situation-"
    "specific constraint等)に留めること。\n"
    "\n--- 絶対にやってはいけないこと(Negative Control) ---\n"
    "全ケースでPremise Invalidationを出す／全ケースでBeneficiary Searchを行う／2 "
    "Actorがいれば必ずRole Choreographyにする／Sourceが偏っていたら情報全体を捨てる"
    "／Feasibilityを全ケースで最優先する／Human motiveを常にStructural constraint"
    "より優先する／必ずPrimary Storylineを1つに決める／Bold hypothesisをfact化する"
    "／すべてのReasoning Operationを毎回使う。**0個または数個だけOperationが効く"
    "状態が正常である。**"
)

# Expert Reasoning Pipeline v0.2（ユーザー指示）: v0.1の単一Noteを、Call1(Explore/Activate)
# 用とCall2(Reassess/Prune/Compose/Decide)用の2つの短いNoteへ分割する。GEO_EXPERT_
# REASONING_PIPELINE_V2_ENABLEDがONの場合のみ、geo_expert_engine._build_system_promptが
# stage引数に応じてどちらか一方だけを追加する（v0.1のNoteとは同時適用しない）。
# 最重要原則（v0.1と同じ、Q1/Q2/Q3aの失敗から）: R1-R13のような固定taxonomyやGrammar/axis
# 分類器を再導入しない。Cueが無ければ0件で正常。ケース固有の文言・Gold前提のハードコードは
# しない。
REASONING_PIPELINE_V2_EXPLORE_NOTE = (
    "\n\n===== Expert Reasoning Pipeline v0.2: Explore / Activate (Call1) =====\n"
    "この呼び出しの目的は、局面を鵜呑みにせず違和感を特定し、その違和感を具体的な"
    "Candidate Hypothesisとintelligence_needsへ展開することである。\n"
    "1. Surface Reading: まず表面的に何が起きているかをそのまま捉える。\n"
    "2. Cue/Anomaly: 通常期待される展開・Actorの利害・過去のPatternと比べて何が"
    "不自然か、initial_anomalyとして具体的に述べる（違和感が無ければ無理に作らない）。\n"
    "3. Reasoning Activation: 上記のCueが、(a)Actorの利害と行動の不一致(b)実行能力・"
    "リソース面の疑問(c)複数Actor間のトーン・役割の差(d)現在の見立てが依存する前提の"
    "動揺(e)期待される兆候の不在、のいずれかを連想させる場合、そのつながりを頭の中で"
    "認識してよい。ただしこれをラベル・分類名としてどこにも出力しないこと——"
    "「このケースはX型」と選ぶのではなく、該当すれば自然にhypothesis_registerの"
    "reasonへ反映されるだけでよい。0個該当が正常であり、無理に全種類を毎回連想しない。\n"
    "4. Concrete Expansion: 連想した内容を局面非依存の一般論のまま終わらせず、今回の"
    "具体的なActor名・行動・時期・Evidenceへ結び付けて具体化する。\n"
    "5. Candidate Hypotheses: hypothesis_registerへ3〜5件（H0含む）の異なる読みを"
    "記録する。件数を機械的に揃える必要は無い——真に異なる読みが2件しか無ければ2件で"
    "よい。\n"
    "6. Intelligence Needs: 上記の判断に、既知の一般論だけでは埋まらない具体的な"
    "Actor/Relationship/類似事例/構造的事実があれば、intelligence_needsへ列挙する"
    "（query は照会に使える具体的な名詞句にする）。無ければ空配列のままでよい——"
    "件数を埋めるために存在しないNeedを作らないこと。\n"
    "\n--- この呼び出しでやってはいけないこと(Negative Control) ---\n"
    "毎回同じ件数のCue/Hypothesis/Needを生成する／2名以上のActorが出てきたら自動的に"
    "Role Choreographyや隠れたActor・裏の計画を疑う／Sourceの偏りを理由に情報全体を"
    "捨てる／intelligence_needsを埋めるためだけに具体性の無いqueryを作る。"
)

REASONING_PIPELINE_V2_DECIDE_NOTE = (
    "\n\n===== Expert Reasoning Pipeline v0.2: Reassess / Prune / Compose / Decide (Call2) =====\n"
    "この呼び出しでは、Call1のCandidate HypothesisをDeterministic Supplemental "
    "Retrieval（提示されていれば）の結果を踏まえて再評価し、Main/Alternative Scenarioへ"
    "絞り込む。Top1に無理に決め切ることを目的にしない。\n"
    "1. Candidate Reassessment: Supplemental Retrievalで新たに見つかった内容は"
    "reasonへ反映し、見つからなかった場合はその不在自体をEvidence不足として正直に"
    "扱う（fabricationしない）。\n"
    "2. Hard Pruning: 以下のいずれかに該当する候補はstatus=rejectedにしてよい——"
    "A.Feasibility(物理・制度・能力的に成立するか)／B.Actor Incentive(目的・制約と"
    "整合するか)／C.Observed Behavior(実際の行動を説明できるか)／D.Counterparty "
    "Reaction(相手側の合理的反応まで成立するか)／E.Cue Resolution(最初の違和感を"
    "説明できるか)／F.Evidence Compatibility(Evidenceと大きく矛盾しないか)／"
    "G.Assumption Burden(未証明の追加仮定を大量に要しないか)。\n"
    "3. Top 3〜5 Shortlist: Pruning後に残った候補のうち、互いに識別可能な読みを"
    "3〜5件程度まで残す（数を合わせるために弱い候補を無理に残さない）。\n"
    "4. Composition: 異なる因果レイヤーを説明する要素は1つの仮説へ統合してよいが"
    "（Composition）、同じ現象への競合する説明（例: 本物の対立 vs 意図的な演出）は"
    "無理に1つへ合成せず別々に残す（Competing）。\n"
    "5. Ranking or Hold: Primaryは『最も望まれる未来』ではなく、観測を最も一貫して"
    "説明し現実的に進行し得る読みである。2つ以上のStorylineが十分成立し、識別する"
    "Evidenceが無い場合は、無理にPrimaryを1つ作らずHold（main_scenario.confidenceを"
    "低くし、拮抗する複数案をalternative_scenariosへ残す）としてよい——ただしHoldは"
    "『判断を避けるための既定値』ではなく、識別Evidenceが無いという分析結果として"
    "使うこと。\n"
    "6. Discriminator/Watchpoint: monitoring_indicatorsへ、どの観測があれば"
    "Primary/Alternativeの優劣が入れ替わるかを具体的に記す。\n"
    "\n--- この呼び出しでやってはいけないこと(Negative Control) ---\n"
    "識別材料が無いまま機械的にPrimaryを1つ選ぶ／逆に判断を避けるためだけに毎回"
    "Holdにする／Recallを上げる目的でShortlistへ弱い候補を水増しする／Supplemental "
    "Retrievalで見つからなかったことを無視してfactとして書く。"
)

# Decision Calculus（ユーザー指示、Production統合 v1.0）: v0.2 Explore Noteを壊さず末尾へ
# 小さなAddendumとして追加する（Call2へは追加しない、新規Schema field無し）。E01(Hormuz)の
# test-only実験で、複数のBenefit/Cost/Constraint/Leverageが独立したExplanationのまま
# 並列化される問題を確認し、これらを一つのAction Choiceの得失として統合させることで、
# Gold同型の仮説（「失うものがなくなれば、これまで高コストだったActionが再び合理的な
# 選択肢になり得る」）がReasoning Spaceへ自然に入ることを確認した。E06(Timing)/E20
# (Capability≠Intent)でRegression PASS済み。State-dependent Reweightingは明示せず
# （Decision Calculusのみで自然に生成されたため、追加Ruleを足さない）。特定の結論・
# 特定Actionを強制しない、数値Utilityを使わない、Selection/Prioritizationには関与しない
# （候補生成のみ）。
DECISION_CALCULUS_EXPLORE_ADDENDUM = (
    "\n\n===== Decision Calculus (Addendum) =====\n"
    "重要なActorが複数の異なるActionの間で選択している局面では、個々のMotive・Cost・"
    "Constraint・Riskを別々のExplanationとして並べるだけで終わらせないこと。\n"
    "該当する場合は次を行う——\n"
    "1. そのActorが実際に選ぼうとしている具体的なDecisionを特定する。\n"
    "2. 現実的なAction Alternativesを特定する。\n"
    "3. 各Alternativeに伴う主要なBenefit・Cost・Constraint・Risk・Leverage効果・"
    "Option Valueを比較する。\n"
    "4. それらのTrade-offを、そのActorのHigher-order Objectiveに照らして評価する。\n"
    "これらは独立したExplanationの寄せ集めではなく、一つのDecision Calculusの構成要素と"
    "して扱うこと。\n"
    "人工的な数値スコアを付けないこと。特定のAction・結論を強制しないこと。\n"
    "\n--- この局面で無関係なら使わないこと(Negative Control) ---\n"
    "Action Choiceが重要でないケース（例: Timingの整合性そのものが核心、または"
    "Capability と Intent の区別そのものが核心）で、無理にAction Alternativesの比較を"
    "作り出さない／毎回のケースでDecision Matrixを義務的に生成しない。"
)

# Trajectory Context Experiment v0.1（ユーザー指示、test-only、GEO_EXPERT_TRAJECTORY_ENABLED
# 既定OFF）: v0.2 Explore Noteを壊さず末尾へAddendumとして追加する（Call2へは別のAddendumを
# 追加、新規LLM呼び出し無し）。目的はTimelineを作ることではなく、この局面が現在の
# Snapshotだけでは読めないかを判定し、必要な場合のみ「何が変わったか(what_changed)」を
# 抽出することである。特定の個別事例（特定の国・人物・出来事）をここに書かない——
# 汎用Operatorとしてのみ機能させる。
TRAJECTORY_NEED_DETECTION_EXPLORE_ADDENDUM = (
    "\n\n===== Trajectory Need Detection (Addendum, test-only, v0.2) =====\n"
    "Do not treat every situation as an isolated current-state snapshot by default. "
    "Consider whether this situation can be adequately read from its current snapshot "
    "alone, or whether understanding how it got here is necessary.\n"
    "Trajectory reasoning is likely needed when one or more of the following apply:\n"
    "A. Escalation/De-escalation: the situation has been moving through graduated "
    "steps (military action, sanctions, negotiation-to-threat, a red line being "
    "crossed), and the next plausible step depends on that progression.\n"
    "B. Why now?: a sudden policy change, public statement, conflict, or cooperation "
    "cannot be adequately explained by the current event alone.\n"
    "C. Prior Narrative/Perception: understanding the meaning of the current action "
    "requires knowing how the actor was perceived, or what narrative had accumulated, "
    "immediately before it.\n"
    "D. Actor Behavior History: judging whether the current behavior is ordinary or "
    "unusual for this actor requires comparing it against that actor's past behavior.\n"
    "If none of these apply, set trajectory_reasoning_needed=false and leave the other "
    "fields null — do not force a trajectory where the current snapshot is already "
    "sufficient.\n"
    "If true, list every mode among escalation/narrative/actor_behavior that applies "
    "into trajectory_modes (one or more — do not use a combined 'mixed' value), and "
    "name the single most central one as primary_mode.\n"
    "IMPORTANT — this is a separate question from whether you need to retrieve more "
    "evidence: a case can require trajectory reasoning (working out what changed and "
    "where the situation now stands) using only the evidence you already have. Decide "
    "existing_evidence_sufficient on its own merits. Only if it is false, list the "
    "concrete evidence_gaps (specific missing facts, dates, or actions — not a vague "
    "'need more context'), set supplemental_retrieval_needed=true, choose a "
    "lookback_window that fits this case (do not default to a fixed period such as "
    "one year), and write retrieval_questions where each question maps to one of the "
    "evidence_gaps — not generic background-history questions.\n"
    "\n--- Do not do the following (Negative Control) ---\n"
    "Setting trajectory_reasoning_needed=true for every case as a habit. Requesting "
    "supplemental retrieval merely because trajectory reasoning is needed, without a "
    "concrete evidence gap. Using 'mixed' instead of listing actual modes. Requesting "
    "a fixed lookback window regardless of the case. Writing retrieval_questions that "
    "are too abstract to search on, or that do not map to any evidence_gap. Treating "
    "trajectory_reasoning_needed=false as a failure — most cases may not need it."
)

# Trajectory Reasoning（ユーザー指示、Call2/decide側、v0.2）: trajectory_assessment.
# trajectory_reasoning_needed=trueの場合、run_expert_request_staged側でstage2_system_
# prompt末尾へ動的に追加する（trajectory_reasoning_needed=falseの場合は一切追加しない、
# 常時ONの固定Noteではない点がDecision Calculusとの違い）。v0.1からの訂正: Supplemental
# Retrievalが実行されたか(existing_evidence_sufficient=falseの場合のみ)に関わらず、
# 既にPromptにある通常のEvidenceだけでも本Reasoningは適用できるよう、文言を
# Retrieval非依存にした。
TRAJECTORY_REASONING_DECIDE_ADDENDUM = (
    "\n\n===== Trajectory Reasoning (Addendum, test-only, v0.2) =====\n"
    "Do not treat the current situation as an isolated snapshot. Using the evidence "
    "already available to you — the source material provided, and, only if a "
    "TRAJECTORY CONTEXT block appears below, the additional retrieved candidates in "
    "it (raw, unfiltered material — some may not be material) — reconstruct only the "
    "materially relevant progression.\n"
    "Ask:\n"
    "1. What was the earlier baseline?\n"
    "2. Which specific facts materially changed the situation (a state change, a "
    "step-up in actor commitment, a change in constraints/incentives/red lines, new "
    "precedent value, or relevance to the next plausible step)? Set aside facts that "
    "are merely on the same topic but do not meet any of these.\n"
    "3. What changed at each retained turning point?\n"
    "4. What state has the situation now reached?\n"
    "5. What next move or state has become more plausible because of that "
    "progression?\n"
    "Do not confuse chronological sequence with causality — a later event is not "
    "automatically caused by an earlier one. Use only evidence-supported turning "
    "points. If the retrieved material does not establish a meaningful trajectory, "
    "say so explicitly and do not force one.\n"
    "When comparing scenarios, evaluate plausibility both under the current state "
    "and under the near-term state that the observed trajectory supports moving "
    "toward — without treating that near-term state as certain.\n"
    "A scenario that is not the most conventional reading should still be retained "
    "(not merely as 'most probable' optimization, and not as contrarianism either) "
    "when: its causal chain is coherent, its required trigger is plausible, the "
    "trajectory supports movement toward that trigger, actor behavior is compatible "
    "with it, evidence does not contradict it, and it would matter materially if "
    "true. Do not force such a scenario into Primary merely because it is less "
    "conventional."
)

TRAJECTORY_ESCALATION_MODE_ADDENDUM = (
    "\n--- Escalation mode ---\n"
    "Also consider the opponent's next plausible escalation step, and what response "
    "pattern becomes plausible for the focal actor if that occurs. Ground this in "
    "actor-specific evidence — a response doctrine, proportionality pattern, red "
    "line, retaliation pattern, or escalation precedent actually documented for this "
    "actor. Do not infer a response pattern from nationality, religion, or culture "
    "alone."
)

TRAJECTORY_NARRATIVE_MODE_ADDENDUM = (
    "\n--- Narrative mode ---\n"
    "Consider what perception of the actor existed immediately before the current "
    "action, whether that perception was politically or strategically costly, and "
    "whether a private (non-public) version of the same action could have solved "
    "that perception problem as effectively. Ask what making the action public "
    "specifically changes, who is expected to observe it, and what interpretation it "
    "strengthens or weakens. Always check the counterfactual: if the same "
    "disagreement or action had remained private, would the strategic effect still "
    "exist? Do not conclude that an action is staged for public effect merely "
    "because it was made public."
)

TRAJECTORY_ACTOR_BEHAVIOR_MODE_ADDENDUM = (
    "\n--- Actor-behavior mode ---\n"
    "When the current behavior appears self-damaging, unexpected, or politically "
    "costly, compare two branches rather than assuming either: (A) intentional/"
    "staged/coordinated — what benefit could justify the apparent cost, could the "
    "seemingly harmed counterpart actually tolerate or support it, and does this "
    "actor have a documented history or capability consistent with staged behavior; "
    "(B) unplanned/impulsive/miscalculated — if uncoordinated, who loses, who gains "
    "relatively, and what relationship, support, or political capital could be "
    "damaged. Use actor-specific evidence for this judgment; do not assume that a "
    "generally capable or sophisticated actor's behavior must therefore be "
    "coordinated."
)

# CQAS Temporal Integration v0.3（ユーザー指示、test-only、GEO_EXPERT_CQAS_TEMPORAL_ENABLED
# 既定OFF）: v0.1/v0.2のtrajectory_assessment Gate（Call1の中間判定がtrueの場合のみ
# Reasoning Addendumを足す設計）をやめ、"何が変わったか"という観点をCue/Question/
# Selectionという既存のCQAS出力へ、中間ゲート無しで常時（Flag ON時は毎回）織り込む。
# 新規Schema fieldは一切追加しない。過剰発火の抑止は、外部のGateではなくAddendum自身の
# Negative Control文言（Decision Calculusと同じ設計）に委ねる。
CQAS_TEMPORAL_EXPLORE_ADDENDUM = (
    "\n\n===== CQAS Temporal Integration (Addendum, test-only) =====\n"
    "When identifying the most decision-relevant anomaly (Cue), consider both:\n"
    "A. Snapshot anomaly — what is unusual in the current state alone.\n"
    "B. Change anomaly — what is materially different from the previous relevant "
    "state: which assumption, constraint, relationship, perception, or precedent has "
    "changed, and does that change alter what becomes plausible next.\n"
    "A change is not a Cue merely because it happened later in time. Treat it as a "
    "Cue only if it materially changes interpretation or scenario plausibility — do "
    "not manufacture a change-based Cue when the situation is essentially static.\n"
    "When a material-change Cue is selected, let it shape your investigation "
    "questions and hypotheses: why now, what changed, which prior assumption no "
    "longer holds, what threshold was crossed, what becomes possible now that was "
    "less plausible before, what the next plausible step is if the current "
    "progression continues. Do not generate these questions when the case is "
    "essentially static — most cases will not need them.\n"
    "Do not introduce 'trajectory' or 'change over time' as a new category of "
    "hypothesis or reasoning family of its own. Route change-driven reasoning "
    "through the existing hypothesis/reasoning apparatus you already use — for "
    "example, a material escalation is naturally read through timing/sequencing and "
    "actor-response reasoning; a narrative shift is read through audience/"
    "perception-oriented hypotheses; an actor behaving unexpectedly is read through "
    "actor-model consistency and incentive checks; a changed constraint is read "
    "through premise-challenge and incentive-consistency reasoning. Change/temporal "
    "reasoning is context that helps you pick the right existing angle — it is not "
    "itself a new angle.\n"
    "\n--- Do not do the following (Negative Control) ---\n"
    "Adding a 'what changed' narrative to every case as a habit. Treating a later "
    "event as automatically caused by an earlier one. Forcing a change-based Cue "
    "merely because some time has passed since an earlier fact."
)

CQAS_TEMPORAL_DECIDE_ADDENDUM = (
    "\n\n===== CQAS Temporal Integration - Selection (Addendum, test-only) =====\n"
    "For every retained scenario, compare (a) its fit to the current state as "
    "described, and (b) its fit if the materially-supported near-term state "
    "transition (identified via any change-anomaly Cue from Call1) actually occurs. "
    "Ask explicitly: is this scenario ranked low only because the current state is "
    "assumed to persist unchanged? Has the evidence already weakened that "
    "assumption? Would a plausible next threshold materially change the ranking? Do "
    "not promote a scenario merely because it is dramatic or non-consensus with "
    "respect to the obvious reading.\n"
    "When a scenario differs from the most conventional reading, retain it (rather "
    "than dropping it) only if: its causal chain is coherent, a material change or "
    "threshold in the evidence actually supports it, actor behavior is compatible "
    "with it, there is no material contradiction, and it would matter for "
    "decision-making if true. It does not need to become Primary — in your "
    "reasoning, note whether you are treating it as the base/conventional reading, a "
    "genuine alternative, a less-conventional-but-evidence-supported reading worth "
    "retaining, or a low-confidence watch item, and say why.\n"
    "\n--- Do not do the following (Negative Control) ---\n"
    "Manufacturing a near-term-transition argument for a scenario that lacks one. "
    "Promoting a scenario for being interesting rather than supported. A case with "
    "no material change in the evidence should be evaluated exactly as it would be "
    "without this addendum."
)

# Situation Trajectory Intelligence v1（ユーザー指示、test-only、GEO_EXPERT_TRAJECTORY_V1_
# ENABLED既定OFF）: CQAS Temporal v0.3（trajectory_assessmentとは無関係、常時織り込み）とは
# 別レイヤー。「時系列推論をするか」ではなく「局面を理解する上でどのIssue Scopeについて、
# どんなEvidence Gapが不足しているか」をCall1に判定させ（trajectory_information_need）、
# 見つかったGapに応じてEvidence Sufficiency Retrieval（GEO_EXPERT_TRAJECTORY_V1_RETRIEVAL_
# ENABLEDが別途ONの場合のみ実行）を行うかどうかを分離する。Call2側は、Retrievalの成否に
# 関わらず常に「Trajectory Context v1」という推論フレーム（baseline state→turning
# points→current state→next threshold）を、その時点で利用可能なEvidence（既存Source Pack
# のみ、またはそれにRetrieval結果を加えたもの）に対して適用する。新規Public Schema
# fieldは追加しない——Trajectory Context v1 object自体はfree text reasoningとして
# main_scenario/selection_rationale等の既存フィールドに現れる。
TRAJECTORY_V1_ISSUE_SCOPE_EXPLORE_ADDENDUM = (
    "\n\n===== Situation Trajectory Intelligence v1 - Issue Scope (Addendum, test-only) =====\n"
    "Situation Trajectory is not a timeline. It is the set of material state changes "
    "that altered the premises, constraints, or available choices in this situation.\n"
    "Ask: does understanding what changed in this situation over time — not just its "
    "current snapshot — materially help you interpret it? If the current evidence "
    "already lets you reason about what changed and where things now stand, set "
    "information_needed=false and leave the other fields null; most cases will "
    "already have enough for this from the evidence you were given.\n"
    "If true, define an issue_scope that states what past information would actually "
    "matter: the core_issue (a concrete phrase, not an abstract theme), the primary "
    "and secondary actors involved, the relevant_dimensions of the issue (e.g. "
    "'military coordination', 'domestic political perception', 'public criticism' — "
    "concrete, not generic), and a time_horizon that fits this specific case (do not "
    "default to a fixed period).\n"
    "Then, only for genuinely missing information, list evidence_gaps — each with a "
    "gap_type (prior_state / turning_point / precedent / narrative_state / "
    "constraint_change / relationship_change), a concrete searchable question, and "
    "why filling it would change your reading of the current Cue, Question, or "
    "Scenario comparison. Do not write generic research questions like 'find recent "
    "information about X' or 'find Y's history' — every gap must name the specific "
    "thing that is missing.\n"
    "\n--- Do not do the following (Negative Control) ---\n"
    "Setting information_needed=true as a habit. Writing an issue_scope for a case "
    "that does not need one. Listing evidence_gaps that are not concrete enough to "
    "search on, or that do not map to a specific interpretive question."
)

TRAJECTORY_V1_REASONING_DECIDE_ADDENDUM = (
    "\n\n===== Situation Trajectory Intelligence v1 - Reasoning (Addendum, test-only) =====\n"
    "Using the evidence available to you (the source material provided, and, only if "
    "a TRAJECTORY V1 CONTEXT block appears below, the additional retrieved "
    "candidates in it), construct the following reasoning internally and let it "
    "inform your Cue, Question, Scenario, and Selection reasoning — do not just "
    "restate it as a separate section:\n"
    "1. Baseline state: what was the earlier relevant state of this situation?\n"
    "2. Turning points: which specific facts changed at least one of — (A) the "
    "overall state of the situation, (B) an actor's demonstrated willingness to go "
    "further than before, (C) a constraint, incentive, capability, or cost, (D) a "
    "relevant actor relationship, (E) how the actor or situation was publicly "
    "understood, (F) whether a previously-uncertain action became demonstrably "
    "possible, or (G) the space of plausible next moves? A candidate fact is not a "
    "turning point merely because it happened earlier and is on the same topic — "
    "reject it as background if it meets none of A-G. For every turning point you "
    "do retain, state what changed and why it matters for this case; do not use the "
    "event description itself as the 'what changed' — say what shifted because of "
    "it.\n"
    "3. Current state: given the retained turning points, where does the situation "
    "now stand?\n"
    "4. Next plausible threshold: what would the next material state change be, and "
    "what does it depend on?\n"
    "Then use this to evaluate scenarios: for every retained scenario, compare its "
    "fit to the current state against its fit if the next plausible threshold is "
    "crossed. Ask whether a scenario is ranked low only because the current state is "
    "assumed to persist, and whether the evidence has already weakened that "
    "assumption. A scenario whose present-day probability may not be highest can "
    "still be retained as EXPERT_EDGE/ALTERNATIVE (not necessarily Primary) when a "
    "supported state transition would make it materially more plausible and missing "
    "it would matter for decision-making — do not reward it merely for being novel "
    "or dramatic, and do not treat a projected next state as a fact.\n"
    "If the available evidence — retrieved or not — does not establish a meaningful "
    "trajectory, say so explicitly and reason from the current snapshot alone; zero "
    "turning points is an acceptable outcome.\n"
    "\n--- Do not do the following (Negative Control) ---\n"
    "Treating chronological sequence as proof of causality. Treating a capability "
    "change as proof of intent. Treating a precedent as proof of inevitability. "
    "Treating a public action as proof of staging. Treating a generally capable "
    "actor's behavior as proof of coordination. Inferring an actor's response from "
    "nationality, religion, or culture instead of actor-specific evidence. Inflating "
    "a scenario's probability merely because it describes a more dramatic future "
    "state."
)

# Situation Trajectory Intelligence v1.2（ユーザー指示、test-only、GEO_EXPERT_TRAJECTORY_
# V1_2_ENABLED既定OFF）: Evidence Ablation v1.1で、E01においてAblation済みEvidenceが
# 実際にmain hypothesisを変えることが実証されたにもかかわらず、v1のBoolean Gate
# （trajectory_information_need.information_needed）は6/6のRunでfalseのままだった。
# v1.2はこのBoolean Gateを廃止し、「Historical Contextが必要か？」をLLMに自己判定
# させるのではなく、CQAS自身のReasoning Dependency（Cue/Question/Hypothesisの中で、
# 実際に過去情報に依存している具体的な論点）から直接Evidence Requestを出させる。
TRAJECTORY_V1_2_EVIDENCE_REQUEST_EXPLORE_ADDENDUM = (
    "\n\n===== Situation Trajectory Intelligence v1.2 - Evidence Request (Addendum, "
    "test-only) =====\n"
    "Do not ask yourself 'is historical information needed?' as a yes/no question. "
    "Instead, examine your own Cue (initial_anomaly), Question (investigation_"
    "questions), and Hypotheses (hypothesis_register) for concrete places where your "
    "reasoning actually depends on something about the past that you do not currently "
    "know — not 'history in general might help', but a specific, nameable dependency "
    "such as: a scenario assumes a prior constraint or restraint still holds; a "
    "branch between two hypotheses turns on whether this actor has done something "
    "like this before; a reading of 'public/strategic communication' depends on what "
    "the prior public narrative or perception was.\n"
    "For each such dependency that is real and specific, emit one item in "
    "trajectory_evidence_requests. If there is no such dependency, return an empty "
    "array — an empty array is the normal, expected outcome for most cases and is "
    "not a failure to search hard enough.\n"
    "For every request, provide: a request_id; a need_type (prior_state / "
    "turning_point / precedent / narrative_state / actor_history / "
    "relationship_change / constraint_change); a priority (P1 = could change which "
    "scenario ranks first; P2 = could change whether a hypothesis is retained as a "
    "primary vs. alternative branch; P3 = would only add explanatory texture without "
    "changing any ranking or branch); a reasoning_dependency stating the specific "
    "place in your own reasoning that depends on this (not a general justification "
    "for why history matters); a concrete, searchable question; why_it_matters "
    "(what would change in your Cue/Question/Scenario comparison if this were "
    "answered); the primary/secondary actors, core_issue, relevant_dimensions, and "
    "time_window that scope the request; and expected_evidence describing concretely "
    "what kind of evidence would answer it.\n"
    "Do not generate more than 3 requests. If you find more than 3 candidate "
    "dependencies, keep only the ones that would most change your Scenario or "
    "Selection reasoning (favor P1, then P2) and drop the rest.\n"
    "\n--- Do not do the following (Negative Control) ---\n"
    "Writing a request just because a case involves geopolitics or long-running "
    "actors — most cases can be reasoned about entirely from the evidence you were "
    "given. Writing a generic research question ('find X's history', 'find recent "
    "news about Y') instead of naming the specific reasoning dependency it resolves. "
    "Writing a request whose reasoning_dependency restates the question instead of "
    "naming an actual place in your Cue/Question/Hypothesis reasoning that depends "
    "on it. Marking something P1 merely because it is interesting rather than "
    "because it would actually change a ranking."
)

TRAJECTORY_V1_2_REASONING_DECIDE_ADDENDUM = (
    "\n\n===== Situation Trajectory Intelligence v1.2 - Reasoning (Addendum, "
    "test-only) =====\n"
    "Using the evidence available to you (the source material provided, and, only if "
    "a TRAJECTORY V1.2 EVIDENCE block appears below, the additional retrieved "
    "material in it), construct the following reasoning internally and let it inform "
    "your Cue, Question, Scenario, and Selection reasoning — do not just restate it "
    "as a separate section:\n"
    "1. Baseline state: what was the earlier relevant state of this situation?\n"
    "2. Turning points: which specific facts changed at least one of — (A) the "
    "overall state of the situation, (B) an actor's demonstrated willingness to go "
    "further than before, (C) a constraint, incentive, capability, or cost, (D) a "
    "relevant actor relationship, (E) how the actor or situation was publicly "
    "understood, (F) whether a previously-uncertain action became demonstrably "
    "possible, or (G) the space of plausible next moves? A candidate fact is not a "
    "turning point merely because it happened earlier and is on the same topic — "
    "reject it as background if it meets none of A-G. For every turning point you "
    "do retain, state what changed and why it matters for this case; do not use the "
    "event description itself as the 'what changed' — say what shifted because of "
    "it.\n"
    "3. Current state: given the retained turning points, where does the situation "
    "now stand?\n"
    "4. Next plausible threshold: what would the next material state change be, and "
    "what does it depend on?\n"
    "If you generated any trajectory_evidence_requests earlier and a TRAJECTORY "
    "V1.2 EVIDENCE block below addresses one, use it explicitly as part of 'what "
    "changed' — do not merely cite it as a source without saying what it changes "
    "about your reading. If a request was not addressed by any retrieved evidence, "
    "say so and reason from the current snapshot alone for that specific point.\n"
    "Then use this to evaluate scenarios: for every retained scenario, compare its "
    "fit to the current state against its fit if the next plausible threshold is "
    "crossed. Ask whether a scenario is ranked low only because the current state is "
    "assumed to persist, and whether the evidence has already weakened that "
    "assumption. A scenario whose present-day probability may not be highest can "
    "still be retained as EXPERT_EDGE/ALTERNATIVE (not necessarily Primary) when a "
    "supported state transition would make it materially more plausible and missing "
    "it would matter for decision-making — do not reward it merely for being novel "
    "or dramatic, and do not treat a projected next state as a fact.\n"
    "If the available evidence — retrieved or not — does not establish a meaningful "
    "trajectory, say so explicitly and reason from the current snapshot alone; zero "
    "turning points is an acceptable outcome.\n"
    "\n--- Do not do the following (Negative Control) ---\n"
    "Treating chronological sequence as proof of causality. Treating a capability "
    "change as proof of intent. Treating a precedent as proof of inevitability. "
    "Treating a public action as proof of staging. Treating a generally capable "
    "actor's behavior as proof of coordination. Inferring an actor's response from "
    "nationality, religion, or culture instead of actor-specific evidence. Inflating "
    "a scenario's probability merely because it describes a more dramatic future "
    "state."
)

# Expert Context Architecture v1.3（ユーザー指示、test-only、GEO_EXPERT_EXPERT_CONTEXT_
# V1_3_ENABLED既定OFF）: v1/v1.2で確認された問題——「追加情報が必要か？」をLLMに自己
# 判定させる方式は、Evidence Ablation v1.1でE01の因果依存が実証された状況でも発火せず
# （v1: information_needed=false 6/6）、v1.2のOptional Evidence Requestでも、Ablated
# なE01/E02では0/4件生成、逆にFull EvidenceのRegression（E08/E20/E22）では3/3件生成
# という逆転傾向が確認された——を受け、Optional Self-Reportそのものを廃止する。代わりに
# 「各Hypothesisが成立するために何を確認すべきか」を必須の分析構造として先に列挙させ、
# 既存Source Packとの充足度を決定的なCoverage Check（追加LLM呼び出し無し、
# geo_expert_engine._expert_context_v1_3_coverage_check）で判定し、Retrieval要否は
# Coverage結果からのみ決める（LLMへ「必要か」を再度問わない）。
EXPERT_CONTEXT_V1_3_REQUIREMENTS_EXPLORE_ADDENDUM = (
    "\n\n===== Expert Context Architecture v1.3 - Hypothesis Evidence Requirements "
    "(Addendum, test-only) =====\n"
    "For every hypothesis you retained in hypothesis_register, do not ask yourself "
    "whether you personally feel you need more information. Instead, define what "
    "evidence would actually need to be checked for that hypothesis to be properly "
    "evaluated against its competitors — this is mandatory analytical structure, not "
    "an optional request.\n"
    "For each hypothesis, list its evidence_requirements. Each requirement must be "
    "specific to that hypothesis's actual assumption — not a generic 'need more "
    "history about X' or 'need more context about Y'. State: the dimension (one of: "
    + ", ".join(_EXPERT_CONTEXT_V1_3_DIMENSIONS) + "); the importance — CRITICAL "
    "(missing this could change hypothesis viability, scenario ranking, primary vs. "
    "alternative, or expert-edge retention), SUPPORTING (improves confidence/"
    "explanation but would not usually reverse a ranking), or OPTIONAL (adds "
    "richness only); a concrete question; supporting_evidence describing what would "
    "actually support this hypothesis if found; and falsifying_evidence describing "
    "what would actually weaken or falsify it if found — do not only look for "
    "confirming evidence, actively state what would count against the hypothesis "
    "too. Also give the actors, core_issue, and time_window that scope the "
    "requirement.\n"
    "Then, where two or more retained hypotheses genuinely compete, identify the "
    "discriminator between them: the single piece of evidence whose presence or "
    "absence would most directly settle which hypothesis is better supported. List "
    "these as hypothesis_discriminators (discriminator_for naming the competing "
    "hypothesis_ids, plus dimension/question/required_evidence/actors/core_issue/"
    "time_window). Discriminators are the highest-priority requirements.\n"
    "Most hypotheses will share some requirements — that is expected, do not force "
    "artificial distinctness. If a hypothesis is simple enough to need no dedicated "
    "check beyond what its Cue/Question already established, return an empty "
    "evidence_requirements list for it; that is a normal outcome, not a shortfall.\n"
    "\n--- Do not do the following (Negative Control) ---\n"
    "Writing a requirement as a generic research prompt ('find more about X's "
    "history', 'get more context on Y') instead of naming the specific assumption "
    "the hypothesis depends on. Marking a requirement CRITICAL merely because it is "
    "interesting rather than because missing it could actually change a ranking. "
    "Only listing supporting_evidence and leaving falsifying_evidence generic or "
    "empty. Inventing requirements about topics not actually present in this case's "
    "Cue, Question, or Hypotheses."
)

EXPERT_CONTEXT_V1_3_REASONING_DECIDE_ADDENDUM = (
    "\n\n===== Expert Context Architecture v1.3 - Reasoning (Addendum, test-only) "
    "=====\n"
    "You earlier defined Evidence Requirements for your retained hypotheses and "
    "their discriminators. Each has since been checked against the available "
    "evidence (Coverage: SATISFIED / PARTIALLY_SATISFIED / MISSING) and, for "
    "requirements routed to historical/trajectory context, unsatisfied CRITICAL (or "
    "discriminator) requirements were used to retrieve additional material — if a "
    "block below is headed EXPERT CONTEXT V1.3 EVIDENCE, use it explicitly.\n"
    "For any requirement addressed by retrieved evidence, do the following instead "
    "of merely citing it: 1. Baseline state — what was the relevant earlier state? "
    "2. Is there an actual Turning Point — a fact that changed at least one of: (A) "
    "the overall state of the situation, (B) an actor's demonstrated willingness to "
    "go further than before, (C) a constraint, incentive, capability, or cost, (D) a "
    "relevant actor relationship, (E) how the actor or situation was publicly "
    "understood, (F) whether a previously-uncertain action became demonstrably "
    "possible, or (G) the space of plausible next moves? If the retrieved evidence "
    "only confirms continuity with the prior state (nothing changed), say so "
    "explicitly — 'No Material Change' is a valid and expected finding, not a "
    "shortfall; continuity can matter for Scenario Selection just as much as "
    "change. 3. If a genuine Turning Point exists, state what changed and why it "
    "is material to this case's hypothesis comparison — do not use the event "
    "description itself as the 'what changed'. 4. Current state implication. 5. "
    "Next plausible threshold, if any.\n"
    "Use this to re-evaluate your retained hypotheses and discriminators: does the "
    "coverage result (satisfied, partially satisfied, or still missing) shift which "
    "hypothesis is best supported, or which scenario should be Primary vs. "
    "Alternative vs. Expert-edge vs. Hold? A hypothesis is not automatically wrong "
    "merely because a requirement stayed unsatisfied — say so and reason from what "
    "you have.\n"
    "\n--- Do not do the following (Negative Control) ---\n"
    "Treating chronological sequence as proof of causality. Treating a capability "
    "change as proof of intent. Treating a precedent as proof of inevitability. "
    "Treating a public action as proof of staging. Treating a generally capable "
    "actor's behavior as proof of coordination. Inferring an actor's response from "
    "nationality, religion, or culture instead of actor-specific evidence. Inflating "
    "a scenario's probability merely because it describes a more dramatic future "
    "state. Forcing a Turning Point out of evidence that only confirms continuity."
)

# Hypothesis Generation v1.4（ユーザー指示、test-only、GEO_EXPERT_HYPOTHESIS_GENERATION_
# V1_4_ENABLED既定OFF）: v1.3のFrozen-Call1実験（E01）で、Full Evidenceでも「現在の
# 抑制が続く」というContinuity Hypothesisは6/6で安定生成される一方、「相手側の行動・
# Thresholdが変化したら抑制が外れ、より高いEscalationが現実味を帯びる」という
# State-transition Hypothesisは0/6だった。この差を埋めるため、各Hypothesisに
# Continuity/Transition構造を必須で持たせる。特定Caseへの依存を書かない、汎用的な
# 仕組みとして設計する。
#
# v1.4.1（ユーザー指示）: v1.4のE01_FULL 6-run測定で、Continuity/Conditional Branch/
# 具体的Thresholdは6/6で安定した一方、中核仮説のtransition_driver_actorが6/6で
# 「対象Actor自身」に偏った（他の仮説ではOpponentがdriverになる例が3/6あり、能力自体は
# 存在した）。「Opponentを優先せよ」という誘導は入れない——それは単に偏りの方向を
# 逆転させるだけである。代わりに、driver_types_considered（検討したTypeの集合、順序
# 不問）とtransition_driver_type（実際に採用したTypeの分類）を分離して要求することで、
# 「検討する」ことと「採用する」ことを切り離す。全Typeを無理に仮説化させるのではなく、
# 候補として検討したうえでEvidenceに最も支持されるものを採用させる。
HYPOTHESIS_GENERATION_V1_4_STATE_TRANSITION_EXPLORE_ADDENDUM = (
    "\n\n===== Hypothesis Generation v1.4 - Continuity vs. State-Transition "
    "(Addendum, test-only) =====\n"
    "For each hypothesis you retained in hypothesis_register, you have so far "
    "described what currently seems to be happening. Now also make explicit what "
    "keeps that reading stable, and what would make it stop being stable.\n"
    "For each one, first state the continuity_condition — the specific "
    "constraint, incentive, or relationship that currently holds this "
    "hypothesis's premise in place.\n"
    "Then, before deciding what could break that constraint, briefly consider "
    "each of these candidate driver types and record which ones you actually "
    "considered in driver_types_considered: focal_actor (the actor this "
    "hypothesis is about changing its own structural situation or calculus); "
    "opponent_actor (a rival or counterparty whose own action or state change "
    "could alter the focal actor's incentives, constraints, or response "
    "threshold — this is a distinct mechanism from the focal actor changing "
    "itself: ask 'could a move by the other side change what the focal actor's "
    "best response is?'); third_party (an actor not directly party to the "
    "central relationship, e.g. a mediator, ally, or bystander state); domestic "
    "(internal political/social factors within the focal actor's own polity); "
    "structural (an environmental, economic, or technological factor with no "
    "single actor driving it). You do not have to name a driver for every type — "
    "only report, in transition_driver_type and transition_driver_actor, "
    "whichever single type is actually best supported by the evidence for this "
    "specific hypothesis. Different hypotheses in the same case do not have to "
    "share the same driver type.\n"
    "Then state: transition_driver_change — specifically what that actor doing, "
    "or a specific state of theirs changing, would weaken the constraint; "
    "transition_threshold — a concrete, checkable marker that would tell you the "
    "change has actually happened, not a vague trigger; and "
    "post_transition_hypothesis — what this situation would concretely look "
    "like once that threshold is crossed, stated as a specific reading of how "
    "the focal actor would likely act, not a generic 'things would escalate'.\n"
    "A currently-dominant, stable-looking hypothesis can still have a real "
    "transition condition — do not skip this because the current reading feels "
    "solid. If a hypothesis genuinely has no meaningful transition condition "
    "(its premise would hold regardless of any actor's plausible next move), you "
    "may state that explicitly instead of inventing one.\n"
    "\n--- Do not do the following (Negative Control) ---\n"
    "Writing a generic wildcard ('if some major event happens, everything could "
    "change') instead of naming a specific actor, a specific change, and a "
    "specific observable threshold. Defaulting transition_driver_type to "
    "focal_actor as a habit without having actually weighed opponent_actor as a "
    "candidate — a real analysis often finds the constraint depends at least "
    "partly on someone else's move. Equally, do not overcorrect by forcing "
    "opponent_actor onto every hypothesis regardless of evidence — a "
    "focal_actor- or structural-driven transition is sometimes genuinely the "
    "best-supported answer, and forcing variety for its own sake is its own "
    "failure mode. Writing post_transition_hypothesis as a restatement of "
    "'escalation happens' without saying what the focal actor would concretely "
    "do differently. Inventing a transition condition for a hypothesis that "
    "does not actually have one."
)

HYPOTHESIS_GENERATION_V1_4_DECIDE_ADDENDUM = (
    "\n\n===== Hypothesis Generation v1.4 - Reasoning (Addendum, test-only) =====\n"
    "You earlier identified, for your retained hypotheses, what currently holds "
    "each one's premise in place (continuity_condition) and what would have to "
    "change for that premise to stop holding (transition_driver_actor / "
    "transition_driver_change / transition_threshold / post_transition_"
    "hypothesis). Check whether the evidence available to you suggests the "
    "situation is closer to that transition threshold than the continuity "
    "reading assumes — if so, the post_transition_hypothesis is a legitimate "
    "candidate for an EXPERT_EDGE or ALTERNATIVE scenario (not necessarily "
    "Primary) even if the continuity reading still looks more likely today; do "
    "not discard it merely for being less probable at the current moment. If the "
    "evidence instead confirms the continuity condition still holds, say so "
    "explicitly and do not force the transition scenario into prominence.\n"
    "\n--- Do not do the following (Negative Control) ---\n"
    "Promoting a post_transition_hypothesis to Primary merely because it is more "
    "dramatic. Treating a stated transition_threshold as already crossed without "
    "evidence that it has been. Ignoring the state-transition structure entirely "
    "and reasoning only from the continuity condition."
)

# Explanatory Model Activation v0.3（ユーザー指示）: v0.2のExplore/Decide Noteを壊さず、
# その末尾へ小さなAddendumとして追加する（v0.1のような5,000字超級のNote新設は行わない）。
# GEO_EXPERT_EXPLANATORY_MODEL_ACTIVATION_ENABLEDがONの場合のみ、geo_expert_engine.
# _build_system_promptがstage引数に応じてこのAddendumをv0.2 Noteの後ろへ連結する。
# Reasoning Operation(Premise Challenge/Feasibility/Timing等、診断用の確認observation)
# とExplanatory Model(違和感を具体的な因果説明候補へ変換するテンプレート)を混同しない
# ——ここで渡すのは後者のみ。Grammar分類ではない: 0件・複数件どちらも正常、1件を必ず
# 選ばせない、Model IDそのものをPrimaryへ機械的に接続しない。
EXPLANATORY_MODEL_ACTIVATION_EXPLORE_ADDENDUM = (
    "\n\n===== Explanatory Model Activation (Addendum) =====\n"
    "以下はExplanatory Model Activation Index——過去の実例から抽出された、再利用可能な"
    "因果説明テンプレートの圧縮版一覧である。**分類器ではない**——「このCueはどれか」を"
    "1つ選ぶのではなく、今回のCue/違和感を説明する上で後段で具体的に展開する価値が"
    "あるものだけを、0個でも複数でも自由にmodel_activation_requestsへ挙げてよい"
    "（目安上限3〜4件、無理に埋めない）。Modelそのものをhypothesis_registerの結論に"
    "機械的に接続しない——あくまで候補生成のための道具であり、最終的な採否は通常通り"
    "Evidence・Actor Fit等で判断する。\n"
    "\n--- Activation Index ---\n"
    + geo_explanatory_model_library.build_activation_index() + "\n"
    "\n--- 絶対にやってはいけないこと(Negative Control) ---\n"
    "毎回同じModelをActivateする／全ケースで同数のModelをActivateする／Public "
    "disagreementを見た瞬間に常にEM01(Theater)を選ぶ／複数Actorがいるだけで常に"
    "EM02(Role Differentiation)を選ぶ／Incentive矛盾を見た瞬間に常にEM03を選び、"
    "未確認のBeneficiaryをfact扱いする／Timing異常を見た瞬間に常にEM08を選び、"
    "存在しない計画を断定する。"
)

EXPLANATORY_MODEL_ACTIVATION_DECIDE_ADDENDUM = (
    "\n\n===== Explanatory Model Activation (Addendum) =====\n"
    "Call1でmodel_activation_requestsに挙げられたModelについて、Full Model Card"
    "（Trigger/Explanatory move/Instantiate prompts/Required context/Disqualifiers）が"
    "提示されている場合、以下の順序で使うこと。\n"
    "1. Activate: 提示されたCardのTrigger/Disqualifiersが今回の局面に本当に当てはまるか"
    "再確認する（当てはまらなければそのCardは使わない）。\n"
    "2. Instantiate: Cardの説明を一般論のまま終わらせず、Instantiate promptsに沿って"
    "今回の具体的なActor・Mechanism・Causal Pathまで展開する。「別の主体が関係している"
    "可能性」のような抽象的な言い回しで終わらせず、具体的なActor候補・関係・"
    "実行可能性まで踏み込む（ただしEvidence無しに断定せず、Candidate/hypothesisとして"
    "明示すること）。\n"
    "3. Merge: こうして生まれた新しいCandidateを、Call1由来のCandidate・Supplemental "
    "Retrieval由来のCandidateとマージしてから、通常のPruning（Feasibility/Actor Fit/"
    "Observed Behavior/Counterparty Reaction/Evidence Compatibility/Cue Resolution/"
    "Assumption Burden）を行う。Activated Modelを見た瞬間にPrimaryへ直接接続しない——"
    "Pruning前のCandidateの一つとして平等に扱う。\n"
    "Model IDや今回使ったModel名を最終Outputの文章に出す必要は無い（内部の候補生成"
    "手段であり、成果物としてのHypothesis/Scenarioの記述に混ぜない）。"
)

# Selector-Free Model Sweep実験（ユーザー指示、v0.3のAblation）: v0.3のSelector
# (Call1がmodel_activation_requestsで事前に1〜数件だけ選ぶ)を今回だけ外し、ACTIVE 6件+
# EXPERIMENTAL 2件、計8件のFull Model Cardを全てCall2へ渡す。目的は、「EM04がLibraryに
# あるのにE01で出ない」等の未発火が、Selector(選抜ロジック)の問題なのか、Model Card/
# Instantiation自体の問題なのかを切り分けること。Call1側はmodel_activation_requestsを
# 生成させない(v0.2のExplore Noteのみ、v0.3 Explore Addendumは付けない)——
# geo_expert_engine._build_system_prompt側でstage="explore"の場合にこのAddendumを
# 追加しないよう制御する。
EXPLANATORY_MODEL_SWEEP_DECIDE_ADDENDUM = (
    "\n\n===== Explanatory Model Sweep (Addendum, 1回限りのAblation実験) =====\n"
    "以下はExplanatory Model Library全件(ACTIVE 6件+EXPERIMENTAL 2件、Full Card)である。"
    "今回はCall1側で事前選抜を行っていない——**全てのCardについて、以下の4点を内部的に"
    "確認すること**。\n"
    "1. このModelは今回のCue/違和感を直接説明できるか？\n"
    "2. Required contextに相当する情報が今回の局面に存在するか？\n"
    "3. Disqualifiersのいずれかに該当しないか？\n"
    "4. Current Actor/Relationship/Mechanismへ具体的に展開できるか？\n"
    "上記が全てYESの場合のみ、Instantiate by askingに沿って具体的なCandidate "
    "Hypothesisへ展開すること。1つでもNOなら、そのModelについては**何も生成しない**"
    "（無理にCandidateを作らない）。合うModelが0件であっても正常である。\n"
    "EXPERIMENTAL Tierとして明示されているCardは、適用してよいが、そのCardが適用できた"
    "という理由だけでPrimaryへ昇格させないこと（Candidate生成のThresholdとPrimary昇格の"
    "Thresholdは別——通常のPruning/Evidence評価を必ず経ること）。\n"
    "Model IDや今回使ったModel名を最終Outputの文章に出す必要は無い。\n"
    "\n--- Model Library(Full) ---\n"
    + geo_explanatory_model_library.build_full_sweep_text() + "\n"
    "\n--- 絶対にやってはいけないこと(Negative Control) ---\n"
    "8件全てから無理やり1つずつCandidateを作る／条件を満たさないModelでもcue関連度が"
    "高そうというだけで適用する／EXPERIMENTAL Cardの適用を理由にそのCandidateだけを"
    "特別扱いでPrimaryにする。"
)

# Phase E(Reasoning Order Fix、ユーザー指示): プロンプト内のNote登場順を、
# Schema property順・Deep Reasoning Sequenceと同じ「Key Human Actors→
# Directional Perception→Situation Actor Assessment→Primary Question/Key Point→
# Hypothesis Register」の順に一致させる。既存のNote本文は削除・短縮せず、
# 連結順序のみを変更する。
for _mode_key in MODE_GUIDANCE:
    MODE_GUIDANCE[_mode_key] = (_DEEP_REASONING_SEQUENCE_NOTE + "\n"
                                 + MODE_GUIDANCE[_mode_key]
                                 + _ACTOR_INTELLIGENCE_NOTE
                                 + _DISCRETION_MODE_NOTE
                                 + _PERCEIVED_PATH_EVENT_ROLE_NOTE + _DIRECTIONAL_PERCEPTION_NOTE
                                 + _SITUATION_ACTOR_ASSESSMENT_NOTE
                                 + _ACTOR_ANALYST_RESPONSE_SEPARATION_NOTE
                                 + _INVESTIGATION_QUESTION_NOTE
                                 + _HUMAN_INTERACTION_COVERAGE_NOTE
                                 + _KEY_POINT_NOTE + _FOCUS_SELECTION_NOTE
                                 + _EXPERT_REASONING_CHECKS_NOTE
                                 + _COMPETING_HYPOTHESES_NOTE + _CANDIDATE_GENERATION_OPERATORS_NOTE
                                 + _ACTOR_PATTERN_APPLICABILITY_NOTE
                                 + _EVIDENCE_SUBSTITUTION_NOTE + _WEAK_ANALOGY_NOTE
                                 + _MEDIA_REASONING_NOTE + _MEDIA_INTELLIGENCE_PACK_NOTE
                                 + _ACTOR_MEDIA_CROSS_REFERENCE_NOTE)
