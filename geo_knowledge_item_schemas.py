"""地政学ナレッジ＆インテリジェンスDB: geo_knowledge_items.structured_content の
knowledge_typeごとのJSON Schema定義。

sustainability_expert_common.CONTENT_GENERATION_SCHEMA と同じ発想（LLM出力・手動入力の
両方をjsonschema.validateで検証し、型ごとに必須フィールドを強制する）。

Precedent/Mechanism/Reasoning Rule/Indicator/Leverage Card等は仕様書8章のフィールドを
そのままstructured_contentのpropertiesとして定義する。専用テーブルを作らず本テーブル1本に
集約するための検証層（docs/geopolitical-data-model.md 3章）。
"""
import jsonschema

_CONFIDENCE_ENUM = ["高", "中", "低", "判断不能"]

PRECEDENT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "PrecedentCard",
    "type": "object",
    "additionalProperties": True,
    "required": [
        "precedent_title", "question_examined", "period", "geography", "actors",
        "background", "triggering_event", "actor_objectives", "instruments_used",
        "legal_and_institutional_conditions", "counterpart_responses", "outcome",
        "success_factors", "failure_factors", "applicable_conditions",
        "non_applicable_conditions", "indicators_to_watch", "sources", "confidence",
    ],
    "properties": {
        "precedent_title": {"type": "string"},
        "question_examined": {"type": "string"},
        "period": {"type": "string"},
        "geography": {"type": "string"},
        "actors": {"type": "array", "items": {"type": "string"}},
        "background": {"type": "string"},
        "triggering_event": {"type": "string"},
        "actor_objectives": {"type": "array", "items": {"type": "string"}},
        "instruments_used": {"type": "array", "items": {"type": "string"}},
        "legal_and_institutional_conditions": {"type": "string"},
        "counterpart_responses": {"type": "string"},
        "outcome": {"type": "string"},
        "success_factors": {"type": "array", "items": {"type": "string"}},
        "failure_factors": {"type": "array", "items": {"type": "string"}},
        "similarities_to_current_case": {"type": "string"},
        "differences_from_current_case": {"type": "string"},
        "applicable_conditions": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "non_applicable_conditions": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "indicators_to_watch": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": _CONFIDENCE_ENUM},
        "last_reviewed_at": {"type": ["string", "null"]},
        # Actor Intelligence Phase AI-1: この先例が特定の1 Actorの意思決定パターンの根拠として
        # 引かれる場合、その主体をentity_id起点で直接検索できるようにする（任意、後方互換）。
        # actorsは自由記述の配列のままとし、これは検索用の補助フィールドとして追加する。
        "primary_actor_entity_id": {"type": ["string", "null"]},
        # 10 Key Actors Historical Intelligence Build: Decision Episode（Persistent Actor
        # Intelligenceの根拠となる個別の過去の意思決定事例）として使う場合のオプショナル拡張。
        # 新規テーブル・新規knowledge_typeを作らず、既存precedent（先例）のstructured_content
        # をそのまま再利用する（0-4節: 既存基盤の再利用、DB migration不要）。
        # 何を言ったか→何をしたか→何を得たか→何を諦めたか→何が制約だったか、を分離するための
        # フィールド群。既存の"actors"/"instruments_used"/"legal_and_institutional_conditions"/
        # "counterpart_responses"と意味が重複するものは追加しない。
        "episode_id": {
            "type": ["string", "null"],
            "description": "このEpisodeのローカル参照ID（Pattern Candidateのevidence_episode_ids/"
                            "exception_episode_idsから参照される。knowledge_item_id確定前の"
                            "草稿段階でも安定した参照キーとして使う）",
        },
        "actor_stated_goal": {
            "type": ["string", "null"],
            "description": "Actor自身が公言した目標（発言レベル。actor_objectivesより粒度が細かく"
                            "本Episode固有）",
        },
        "likely_underlying_objective": {
            "type": ["string", "null"],
            "description": "観測された行動から推定される実際の狙い（Assessment/Hypothesisであり"
                            "Factとして扱わない）",
        },
        "options_available": {
            "type": ["array", "null"], "items": {"type": "string"},
            "description": "この局面でActorが選択可能だった他の選択肢（choice_madeとの対比のため）",
        },
        "choice_made": {"type": ["string", "null"], "description": "実際に選ばれた選択肢"},
        "observable_actions": {
            "type": ["array", "null"], "items": {"type": "string"},
            "description": "発言ではなく実際に観測された行動（statement/actionの分離、"
                            "actor_stated_goalとは別軸）",
        },
        "trade_off": {
            "type": ["string", "null"],
            "description": "何を得て何を諦めたか（交渉・妥協の構造）",
        },
        "negotiation_pattern": {
            "type": ["string", "null"],
            "description": "本Episode単体で観測された交渉パターン（複数Episode横断のPattern "
                            "Candidateとは別、集約前の生データとして残す）",
        },
        "red_line_behavior": {
            "type": ["string", "null"],
            "description": "本Episodeで公言されたRed Lineが実際に行使されたか、あるいは行使すると"
                            "見られていたが譲歩したか",
        },
        "later_reversal_or_exception": {
            "type": ["string", "null"],
            "description": "この判断が後の局面・別Episodeで覆された、または例外的だったことが"
                            "分かっている場合の記述（反例を積極的に残す。'一貫した人物像'に"
                            "無理に合わせない）",
        },
        "supporting_evidence_ids": {
            "type": ["array", "null"], "items": {"type": "string"},
            "description": "既存Evidence/Claim/DocumentのID参照（自由記述のsourcesとは別に、"
                            "IDベースで遡れるようにする）",
        },
        "contradicting_evidence_ids": {
            "type": ["array", "null"], "items": {"type": "string"},
            "description": "この解釈に対する反証・留保となるEvidence/Claim/DocumentのID参照",
        },
        "information_class": {
            "type": ["string", "null"],
            "enum": ["observed", "assessment", "hypothesis", None],
            "description": "本Episode全体の分類。observed=行動・発言として直接確認できる事実、"
                            "assessment=専門的推定、hypothesis=未検証の仮説。Actorの「本音」は"
                            "常にassessment/hypothesisでありobservedとして保存しない",
        },
    },
}

MECHANISM_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "MechanismCard",
    "type": "object",
    "additionalProperties": True,
    "required": [
        "mechanism_name", "mechanism_summary", "actor_objective", "causal_chain",
        "enabling_conditions", "required_assets", "target_dependencies", "constraints",
        "costs", "risks", "likely_countermeasures", "escalation_path",
        "de_escalation_path", "indicators", "confidence",
    ],
    "properties": {
        "mechanism_name": {"type": "string"},
        "mechanism_summary": {"type": "string"},
        "actor_objective": {"type": "string"},
        "causal_chain": {"type": "array", "items": {"type": "string"}},
        "enabling_conditions": {"type": "array", "items": {"type": "string"}},
        "required_assets": {"type": "array", "items": {"type": "string"}},
        "target_dependencies": {"type": "array", "items": {"type": "string"}},
        "constraints": {"type": "array", "items": {"type": "string"}},
        "costs": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "likely_countermeasures": {"type": "array", "items": {"type": "string"}},
        "escalation_path": {"type": "string"},
        "de_escalation_path": {"type": "string"},
        "precedent_links": {"type": "array", "items": {"type": "string"}},
        "indicators": {"type": "array", "items": {"type": "string"}},
        "supporting_evidence": {"type": "array", "items": {"type": "string"}},
        "contradicting_evidence": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": _CONFIDENCE_ENUM},
    },
}

REASONING_RULE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "ExpertReasoningRule",
    "type": "object",
    "additionalProperties": True,
    "required": [
        "rule", "rationale", "applicable_question_types", "required_checks",
        "comparison_dimensions", "common_failure_modes", "expert_source", "approval_status",
    ],
    "properties": {
        "rule": {"type": "string"},
        "rationale": {"type": "string"},
        "applicable_question_types": {"type": "array", "items": {"type": "string"}},
        "required_checks": {"type": "array", "items": {"type": "string"}},
        "comparison_dimensions": {"type": "array", "items": {"type": "string"}},
        "common_failure_modes": {"type": "array", "items": {"type": "string"}},
        "exceptions": {"type": "array", "items": {"type": "string"}},
        "related_precedents": {"type": "array", "items": {"type": "string"}},
        "related_mechanisms": {"type": "array", "items": {"type": "string"}},
        "expert_source": {"type": "string"},
        "approval_status": {"type": "string"},
        # Phase2B-2: geo_expert_review_service由来のReasoning Ruleを細分するための任意分類
        # （既存データとの後方互換のためrequiredには含めない）
        "rule_category": {
            "type": "string",
            "enum": ["stakeholder_selection", "evidence_weighting", "comparison",
                     "hypothesis_revision", "scenario_reasoning", "general"],
        },
    },
}

INDICATOR_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "IndicatorCard",
    "type": "object",
    "additionalProperties": True,
    "required": [
        "indicator_name", "indicator_definition", "source", "query_or_collection_method",
        "update_frequency", "direction", "interpretation", "owner",
    ],
    "properties": {
        "indicator_name": {"type": "string"},
        "indicator_definition": {"type": "string"},
        "related_actor": {"type": ["string", "null"]},
        "related_issue": {"type": ["string", "null"]},
        "related_scenario": {"type": ["string", "null"]},
        "source": {"type": "string"},
        "query_or_collection_method": {"type": "string"},
        "update_frequency": {"type": "string"},
        "baseline": {"type": ["string", "null"]},
        "threshold": {"type": ["string", "null"]},
        "direction": {"type": "string"},
        "signal_strength": {"type": ["string", "null"]},
        "interpretation": {"type": "string"},
        "false_positive_risk": {"type": ["string", "null"]},
        "false_negative_risk": {"type": ["string", "null"]},
        "owner": {"type": "string"},
        "last_observed_at": {"type": ["string", "null"]},
        "next_check_at": {"type": ["string", "null"]},
    },
}

LEVERAGE_CARD_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "LeverageCard",
    "type": "object",
    "additionalProperties": True,
    "required": [
        "holder_entity_id", "target_entity_id", "card_type", "description", "objective",
        "capability", "usability", "target_dependency", "domestic_tolerance",
        "expected_effect", "expected_cost", "escalation_risk", "evidence",
    ],
    "properties": {
        "holder_entity_id": {"type": "string"},
        "target_entity_id": {"type": "string"},
        "underlying_asset_id": {"type": ["string", "null"]},
        "card_type": {"type": "string"},
        "description": {"type": "string"},
        "objective": {"type": "string"},
        # Effective Leverage = Capability × Usability × Counterpart Dependence ×
        # Political Toleranceを単一スコアに集約せず、要素を別々に保持する（7.5章）。
        "capability": {"type": "string"},
        "usability": {"type": "string"},
        "target_dependency": {"type": "string"},
        "domestic_tolerance": {"type": "string"},
        "expected_effect": {"type": "string"},
        "expected_cost": {"type": "string"},
        "legal_constraints": {"type": ["string", "null"]},
        "political_constraints": {"type": ["string", "null"]},
        "operational_constraints": {"type": ["string", "null"]},
        "likely_countermeasures": {"type": ["string", "null"]},
        "escalation_risk": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "valid_from": {"type": ["string", "null"]},
        "valid_to": {"type": ["string", "null"]},
    },
}

COUNTRY_FOUNDATION_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "CountryFoundation",
    "type": "object",
    "additionalProperties": True,
    "required": ["entity_id", "political_system", "decision_structure", "as_of_source"],
    "properties": {
        "entity_id": {"type": "string"},
        "political_system": {"type": "string"},
        "decision_structure": {"type": "string"},
        "executive_legislative_judicial_military_relations": {"type": ["string", "null"]},
        "major_parties_factions": {"type": "array", "items": {"type": "string"}},
        "key_industries": {"type": "array", "items": {"type": "string"}},
        "trade_investment_finance_dependencies": {"type": ["string", "null"]},
        "energy_food_mineral_dependencies": {"type": ["string", "null"]},
        "military_bases_alliances": {"type": ["string", "null"]},
        "historical_conflicts": {"type": "array", "items": {"type": "string"}},
        "treaties_institutions": {"type": "array", "items": {"type": "string"}},
        "geographic_chokepoints": {"type": "array", "items": {"type": "string"}},
        "as_of_source": {"type": "string"},
    },
}

# Actor Intelligence Phase AI-1: geo_claims/geo_knowledge_itemsの既存information_class
# （observed_fact/actor_claim/external_assessment/internal_hypothesis/internal_expert_assessment）
# をそのまま使う（新しい分類軸は作らない。docs/geopolitical-expert-actor-intelligence-phase-ai1-ai3.md参照）。
_ACTOR_INTELLIGENCE_INFORMATION_CLASS_ENUM = [
    "observed_fact", "actor_claim", "external_assessment",
    "internal_hypothesis", "internal_expert_assessment",
]

# core_profile配下の各カテゴリ（worldview/core_objectives等）で繰り返し使う、
# 1件のstatementの型。Fact/Inference分類・Evidence参照・信頼度・時点情報を必須にする。
_ACTOR_STATEMENT_ITEM = {
    "type": "object",
    "additionalProperties": True,
    "required": ["statement", "information_class", "confidence"],
    "properties": {
        "statement": {"type": "string"},
        "information_class": {"type": "string", "enum": _ACTOR_INTELLIGENCE_INFORMATION_CLASS_ENUM},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": _CONFIDENCE_ENUM},
        "observed_at": {"type": ["string", "null"]},
        "last_reviewed_at": {"type": ["string", "null"]},
        "reviewed_by": {"type": ["string", "null"]},
    },
}

_ACTOR_DECISION_PATTERN_ITEM = {
    "type": "object",
    "additionalProperties": True,
    "required": ["pattern", "confidence"],
    "properties": {
        "pattern": {"type": "string"},
        # 単一Precedentのみを根拠にした高confidenceのDecision Patternを作らない
        # （3章参照。コードでは強制せず、Seed/Reviewの運用ガイドラインとする）
        "supporting_precedent_ids": {"type": "array", "items": {"type": "string"}},
        "supporting_evidence_ids": {"type": "array", "items": {"type": "string"}},
        "counter_evidence_ids": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": _CONFIDENCE_ENUM},
        "last_reviewed_at": {"type": ["string", "null"]},
        # Session2 Reasoning Upgrade（Phase2A）: 通常パターンと例外パターンを同じ
        # decision_patterns[]配列内で表現する（新規トップレベルキーを作らない）。
        # pattern_role=exceptionの場合のみdefault_pattern_ref/exception_conditionsを使う。
        "pattern_id": {"type": ["string", "null"]},
        "pattern_role": {"type": ["string", "null"], "enum": ["default", "exception", None]},
        "default_pattern_ref": {
            "type": ["string", "null"],
            "description": "pattern_role=exceptionの場合、対応するdefaultパターンのpattern_id",
        },
        "exception_conditions": {
            "type": ["array", "null"], "items": {"type": "string"},
            "description": "pattern_role=exceptionの場合、defaultパターンからの逸脱がどの条件下で生じるか",
        },
    },
}

_ACTOR_CURRENT_POSTURE_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "required": ["as_of", "confidence"],
    "properties": {
        "as_of": {"type": "string", "description": "この姿勢が観測・整理された時点（date）"},
        "current_priorities": {"type": "array", "items": {"type": "string"}},
        "political_pressure": {"type": ["string", "null"]},
        "current_signals": {"type": "array", "items": {"type": "string"}},
        "current_constraints": {"type": "array", "items": {"type": "string"}},
        "current_direction": {"type": ["string", "null"]},
        "momentum": {
            "type": ["object", "null"],
            "properties": {
                "direction": {"type": ["string", "null"]},
                "strength": {"type": ["string", "null"]},
            },
        },
        "confidence": {"type": "string", "enum": _CONFIDENCE_ENUM},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
    },
}

_ACTOR_ISSUE_POSITION_ITEM = {
    "type": "object",
    "additionalProperties": True,
    "required": ["issue_id", "confidence"],
    "properties": {
        "issue_id": {"type": "string", "description": "geo_issues.issue_idと一致させる"},
        "objectives": {"type": "array", "items": {"type": "string"}},
        "preferred_outcome": {"type": ["string", "null"]},
        "red_lines": {"type": "array", "items": {"type": "string"}},
        "available_leverage": {"type": "array", "items": {"type": "string"}},
        "unavailable_or_costly_cards": {"type": "array", "items": {"type": "string"}},
        "vulnerabilities": {"type": "array", "items": {"type": "string"}},
        "constraints": {"type": "array", "items": {"type": "string"}},
        "likely_moves": {"type": "array", "items": {"type": "string"}},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": _CONFIDENCE_ENUM},
        "valid_from": {"type": ["string", "null"]},
        "valid_to": {"type": ["string", "null"]},
    },
}

# Session2 Reasoning Upgrade（Phase2A）: core_objectivesのみに持続的な優先順位階層を持たせる
# （tradeable/non_tradeable/latent_or_possible等の局面依存分類はここに含めない。それらは
# Situation Actor Assessment側のcurrently_tradeable/current_red_lineで表現する）。
_ACTOR_OBJECTIVE_STATEMENT_ITEM = {
    **_ACTOR_STATEMENT_ITEM,
    "properties": {
        **_ACTOR_STATEMENT_ITEM["properties"],
        "tier": {
            "type": ["string", "null"], "enum": ["primary", "secondary", None],
            "description": "持続的な優先順位の階層（primary/secondary）。局面ごとに変わる交換可能性・"
                            "現在のRed Line該当性はここに含めない",
        },
        "tier_rationale": {"type": ["string", "null"]},
    },
}

# Session2 Reasoning Upgrade（Phase2A）: geo_persistent_preference_service.pyが実際に書き込む
# 形状をそのまま正式プロパティ化する（新規保存先を作らない）。
_PERSISTENT_PREFERENCE_SCHEMA = {
    "type": ["object", "null"],
    "additionalProperties": True,
    "properties": {
        "statement": {"type": ["string", "null"]},
        "as_of": {"type": ["string", "null"]},
        "confidence": {"type": ["string", "null"], "enum": [*_CONFIDENCE_ENUM, None]},
        "supporting_evidence": {"type": "array", "items": {"type": "object"}},
        "contradicting_evidence": {"type": "array", "items": {"type": "object"}},
        "evidence_fingerprints": {"type": "array", "items": {"type": "string"}},
    },
}

# Phase G(Directional Relationship Intelligence、ユーザー指示): 既存geo_relationships
# （客観的・持続的な関係属性、dependency/leverage/trend等）は変更せず、observerの主観的な
# 「相手をどう見ているか」を、既存Actor Intelligence拡張作法（geo_knowledge_items
# knowledge_type=actor_profileのstructured_contentへのオプショナルキー追加）で表現する。
# 1件のitemはobserverのactor_profile item（＝observer自身）に所属し、target_entity_idで
# 相手を指す（A→BとB→Aを同一itemへ混在させない。既存directional_perceptions出力Schema
# ——observer_actor_ref/target_actor_ref/perception/claim_type/confidence/issue_scope/
# valid_from/valid_to——とほぼ同型にすることで、Retrieval→Prompt→Output(directional_
# perceptions)の変換を単純に保つ）。承認ライフサイクル（candidate/in_review/active等）は
# 既存Actor Intelligenceと同じくKnowledge Item単位（配列要素ごとの個別statusは持たない）。
_DIRECTIONAL_RELATIONSHIP_PERCEPTION_ITEM = {
    "type": "object",
    "additionalProperties": True,
    "required": ["target_entity_id", "perception", "claim_type", "confidence", "information_class"],
    "properties": {
        "target_entity_id": {"type": "string"},
        "issue_scope": {"type": ["string", "null"]},
        "perception": {"type": "string",
                       "description": "observer(このactor_profileの主体)がtargetについて持つ"
                                      "（と分析者が評価する）主観的認識。targetの客観的な実態の記述にしないこと"},
        "claim_type": {
            "type": "string",
            "enum": ["stated", "inferred_from_behavior", "inferred_from_source", "analyst_hypothesis"],
            "description": "根拠の性質。stated=observerが直接そう発言/inferred_from_behavior=observerの"
                           "行動から推定/inferred_from_source=第三者情報源からの推定/analyst_hypothesis="
                           "根拠が薄い分析者仮説",
        },
        "confidence": {"type": "string", "enum": _CONFIDENCE_ENUM},
        "information_class": {"type": "string", "enum": _ACTOR_INTELLIGENCE_INFORMATION_CLASS_ENUM},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "source_note": {
            "type": ["string", "null"],
            "description": "evidence_idsとしてこのDBにclaim_id化されていない出典の暫定的な明記"
                           "（人手レビュー時の追跡用。正式なEvidence参照はevidence_idsを優先する）",
        },
        "valid_from": {"type": ["string", "null"]},
        "valid_to": {"type": ["string", "null"]},
        "reviewed_by": {"type": ["string", "null"]},
        "last_reviewed_at": {"type": ["string", "null"]},
    },
}

# Session2 Reasoning Upgrade（Phase2A）: Schema/Read対応のみ（自動候補生成・自動昇格は
# Phase6でpropose_actor_structure_candidatesとして追加する。ここでは追加しない）。
_STRATEGIC_COMMUNICATION_PATTERN_SCHEMA = {
    "type": ["object", "null"],
    "additionalProperties": True,
    "required": ["confidence"],
    "properties": {
        "typical_channels": {"type": "array", "items": {"type": "string"}},
        "signaling_tendencies": {"type": "array", "items": {"type": "string"}},
        "domestic_vs_international_messaging_gap": {"type": ["string", "null"]},
        "ambiguity_or_deniability_tendency": {"type": ["string", "null"]},
        "trial_balloon_history": {"type": "array", "items": {"type": "string"}},
        "information_channel_relationship_ids": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": _CONFIDENCE_ENUM},
    },
}

ACTOR_PROFILE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "ActorProfile",
    "type": "object",
    "additionalProperties": True,
    "required": ["entity_id", "objectives", "core_interests"],
    "properties": {
        "entity_id": {"type": "string"},
        "objectives": {"type": "array", "items": {"type": "string"}},
        "core_interests": {"type": "array", "items": {"type": "string"}},
        "red_lines": {"type": "array", "items": {"type": "string"}},
        "constraints": {"type": "array", "items": {"type": "string"}},
        "veto_power": {"type": ["string", "null"]},
        # Actor Intelligence Phase AI-1（既存フィールドはそのまま、以下すべてオプショナル追加）。
        # actor_intelligence_status（sufficient/partial/insufficient）はここには持たせない
        # （ケース依存であり、Situationごとにgeo_actor_intelligence_service側で都度算出する。
        # docs/geopolitical-expert-actor-intelligence-phase-ai1-ai3.md参照）。
        "default_importance": {"type": ["string", "null"], "enum": ["high", "medium", "low", None]},
        "needs_enrichment": {
            "type": ["array", "null"], "items": {"type": "string"},
            "description": "このProfile自体のデータ品質メモ（例: current_postureの根拠が薄い）。ケース依存の充足度とは別物",
        },
        "coverage_notes": {"type": ["string", "null"]},
        "core_profile": {
            "type": ["object", "null"],
            "additionalProperties": True,
            "properties": {
                "worldview": {"type": "array", "items": _ACTOR_STATEMENT_ITEM},
                "core_objectives": {"type": "array", "items": _ACTOR_OBJECTIVE_STATEMENT_ITEM},
                "decision_preferences": {"type": "array", "items": _ACTOR_STATEMENT_ITEM},
                "red_lines": {"type": "array", "items": _ACTOR_STATEMENT_ITEM},
                "preferred_instruments": {"type": "array", "items": _ACTOR_STATEMENT_ITEM},
                "structural_constraints": {"type": "array", "items": _ACTOR_STATEMENT_ITEM},
            },
        },
        "decision_patterns": {"type": ["array", "null"], "items": _ACTOR_DECISION_PATTERN_ITEM},
        "current_posture": {
            **_ACTOR_CURRENT_POSTURE_SCHEMA, "type": ["object", "null"],
        },
        "issue_positions": {"type": ["array", "null"], "items": _ACTOR_ISSUE_POSITION_ITEM},
        # Session2 Reasoning Upgrade（Phase2A）: Schema/Read対応のみ。自動候補生成・昇格は
        # Phase6（propose_actor_structure_candidates）で追加する。
        "persistent_preference": _PERSISTENT_PREFERENCE_SCHEMA,
        "strategic_communication_pattern": _STRATEGIC_COMMUNICATION_PATTERN_SCHEMA,
        # Phase G(Directional Relationship Intelligence、ユーザー指示): このActor(observer)が
        # 他のActor(target)をどう見ているかの主観的認識。既存geo_relationships（客観的・
        # 持続的な関係属性）とは別物であり、そちらは変更しない。
        "directional_relationship_perceptions": {
            "type": ["array", "null"], "items": _DIRECTIONAL_RELATIONSHIP_PERCEPTION_ITEM,
        },
    },
}

# ==================== Expert AI Phase R1 Chunk3: Institution Intelligence（MVP） ====================
# Person Actor（ACTOR_PROFILE_SCHEMA）では説明できない制度的意思決定主体（BIS/US Congress/
# NATO等）を、正しく「制度」として表現するための最小構造。ACTOR_PROFILE_SCHEMAと同じ
# entity_id起点・同じgeo_knowledge_items格納パターンを再利用するが、Personに固有の
# フィールド（persistent_preference/worldview/red_lines等、「志向」「性格」に相当するもの）
# は一切持たせない。「BISの思想」「NATOの性格」のような擬人化を防ぐことが本Schemaの目的。

_INSTITUTION_STAKEHOLDER_ITEM = {
    "type": "object",
    "additionalProperties": True,
    "required": ["name_or_role"],
    "properties": {
        "name_or_role": {"type": "string", "description": "組織内の主体（例: 委員会、部局、加盟国代表団）。個人名は書かない——制度内の役割・単位として記述する"},
        "position_or_influence": {"type": ["string", "null"], "description": "この主体が意思決定に対して持つ役割・拒否権・影響力（「意図」「性格」ではなく手続き上の位置づけ）"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
    },
}

_INSTITUTION_EXTERNAL_DEPENDENCY_ITEM = {
    "type": "object",
    "additionalProperties": True,
    "required": ["dependency"],
    "properties": {
        "dependency": {"type": "string", "description": "この制度が依存する外部の資源・権限・協力（例: 加盟国の軍事能力提供、他国政府の執行協力）"},
        "description": {"type": ["string", "null"]},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
    },
}

_INSTITUTION_EXCEPTION_ITEM = {
    "type": "object",
    "additionalProperties": True,
    "required": ["description"],
    "properties": {
        "description": {"type": "string", "description": "通常の意思決定手続き・historical_decision_patternsが当てはまらなかった具体的な事例"},
        "precedent_ids": {"type": "array", "items": {"type": "string"}},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": _CONFIDENCE_ENUM},
    },
}

INSTITUTION_PROFILE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "InstitutionProfile",
    "type": "object",
    "additionalProperties": True,
    "required": ["entity_id", "mandate", "decision_authority"],
    "properties": {
        "entity_id": {"type": "string", "description": "geo_entities.entity_id（entity_type=government/ministry/military/security_service/international_organization等）"},
        "mandate": {
            "type": "string",
            "description": "この制度の正式な権限・所掌範囲（何をする権限を持つ組織か。「何を望んでいるか」ではなく「何をする権限があるか」を書く）",
        },
        "decision_authority": {
            "type": "object",
            "additionalProperties": True,
            "required": ["description"],
            "properties": {
                "description": {"type": "string", "description": "最終的に何が決定されるか、誰が/何が形式的な決定権を持つか"},
                "veto_players": {
                    "type": "array", "items": {"type": "string"},
                    "description": "拒否権・実質的な阻止力を持つ主体（個人ではなく制度上の主体、例: 特定加盟国、特定委員会）",
                },
                "decision_process": {"type": ["string", "null"], "description": "多数決/全会一致/委任等、意思決定の手続き"},
            },
        },
        "constraints": {
            "type": "array", "items": {"type": "string"},
            "description": "法的・手続き的・資源的制約（予算、任期、条約上の制限等）",
        },
        "internal_stakeholders": {"type": "array", "items": _INSTITUTION_STAKEHOLDER_ITEM},
        "external_dependencies": {"type": "array", "items": _INSTITUTION_EXTERNAL_DEPENDENCY_ITEM},
        "historical_decision_patterns": {
            "type": ["array", "null"], "items": _ACTOR_DECISION_PATTERN_ITEM,
            "description": "過去の意思決定の反復パターン（手続き的・構造的な傾向。単一Precedentのみを根拠にした高confidenceは避ける——ACTOR_PROFILE_SCHEMAのdecision_patternsと同じ規律）",
        },
        "known_exceptions": {"type": ["array", "null"], "items": _INSTITUTION_EXCEPTION_ITEM},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "coverage_notes": {"type": ["string", "null"]},
    },
}

PARTY_PROFILE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "PartyProfile",
    "type": "object",
    "additionalProperties": True,
    "required": ["party_entity_id", "foreign_policy_position", "source", "as_of_date"],
    "properties": {
        "party_entity_id": {"type": "string"},
        "leader": {"type": ["string", "null"]},
        "factions": {"type": "array", "items": {"type": "string"}},
        "manifesto": {"type": ["string", "null"]},
        "foreign_policy_position": {"type": "string"},
        "security_position": {"type": ["string", "null"]},
        "economic_position": {"type": ["string", "null"]},
        "energy_position": {"type": ["string", "null"]},
        "immigration_position": {"type": ["string", "null"]},
        "technology_position": {"type": ["string", "null"]},
        "support_regions": {"type": "array", "items": {"type": "string"}},
        "urban_rural_split": {"type": ["string", "null"]},
        "income_groups": {"type": ["string", "null"]},
        "age_groups": {"type": ["string", "null"]},
        "industrial_base": {"type": ["string", "null"]},
        "religious_base": {"type": ["string", "null"]},
        "ethnic_base": {"type": ["string", "null"]},
        "labor_support": {"type": ["string", "null"]},
        "industry_support": {"type": ["string", "null"]},
        "election_results": {"type": ["string", "null"]},
        "seat_count": {"type": ["string", "null"]},
        "vote_share": {"type": ["string", "null"]},
        "turnout": {"type": ["string", "null"]},
        "coalition_options": {"type": ["string", "null"]},
        "funding_base": {"type": ["string", "null"]},
        "source": {"type": "string"},
        "as_of_date": {"type": "string"},
    },
}

RELATIONSHIP_ASSESSMENT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "RelationshipAssessment",
    "type": "object",
    "additionalProperties": True,
    "required": ["relationship_id", "assessment", "confidence"],
    "properties": {
        "relationship_id": {"type": "string"},
        "assessment": {"type": "string"},
        "trend_rationale": {"type": ["string", "null"]},
        "confidence": {"type": "string", "enum": _CONFIDENCE_ENUM},
    },
}

HISTORICAL_PATH_DEPENDENCY_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "HistoricalPathDependency",
    "type": "object",
    "additionalProperties": True,
    "required": [
        "historical_event", "actors", "what_happened", "current_policy_effect",
        "situations_where_relevant",
    ],
    "properties": {
        "historical_event": {"type": "string"},
        "actors": {"type": "array", "items": {"type": "string"}},
        "what_happened": {"type": "string"},
        "collective_memory_or_narrative": {"type": ["string", "null"]},
        "affected_interest": {"type": ["string", "null"]},
        "current_policy_effect": {"type": "string"},
        "situations_where_relevant": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "situations_where_not_relevant": {"type": "array", "items": {"type": "string"}},
        "supporting_sources": {"type": "array", "items": {"type": "string"}},
    },
}

FOUNDATION_UPDATE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "FoundationUpdate",
    "type": "object",
    "additionalProperties": True,
    "required": ["target_knowledge_item_id", "change_summary", "why_reusable"],
    "properties": {
        "target_knowledge_item_id": {"type": ["string", "null"]},
        "change_summary": {"type": "string"},
        "why_reusable": {"type": "string"},
    },
}

CASE_FINDING_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "CaseFinding",
    "type": "object",
    "additionalProperties": True,
    "required": ["case_id", "finding_summary", "why_reusable"],
    "properties": {
        "case_id": {"type": "string"},
        "finding_summary": {"type": "string"},
        "why_reusable": {"type": "string"},
    },
}

# 地政学アプリClooseout Phase4: Report Review → Knowledge Learning Loop。CASE_FINDING_SCHEMAと
# 対になる型（Caseの代わりにReportに紐づくcase_specific_assessment相当のknowledge）。
REPORT_FINDING_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "ReportFinding",
    "type": "object",
    "additionalProperties": True,
    "required": ["report_id", "finding_summary", "why_reusable"],
    "properties": {
        "report_id": {"type": "string", "description": "レビュー確定後の新Version（v2）のgeo_reports.report_id"},
        "finding_summary": {"type": "string"},
        "why_reusable": {"type": "string"},
    },
}

# Geopolitical Intelligence OS Phase E: Approved Report全体のPublication記録。
# report_findingが「Reportから得た再利用可能な教訓の要約」であるのに対し、こちらは
# 「承認されたReportそのものが正式Knowledgeとして公開された」という事実＋要約＋Provenanceを
# 保持する（本文全体は複製しない——本文はReport Studio/geo_reports側が正とする）。
APPROVED_REPORT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "ApprovedReport",
    "type": "object",
    "additionalProperties": True,
    "required": ["source_type", "report_title", "syntheses_summary"],
    "properties": {
        "source_type": {"type": "string", "enum": ["report_studio", "external_approved_report"]},
        "report_title": {"type": "string"},
        "syntheses_summary": {
            "type": "array", "items": {"type": "string"},
            "description": "Route A: Narrativeごとの最終html/draftの要約。Route B: 外部Reportの要約。",
        },
        "report_project_id": {"type": ["string", "null"], "description": "Route Aのみ"},
        "run_id": {"type": ["string", "null"], "description": "Route Aのみ、発生源run_id"},
        "approval_event_id": {"type": ["string", "null"], "description": "Route Aのみ"},
        "source_report_id": {"type": ["string", "null"], "description": "Route Bのみ、geo_reports.report_id"},
        "source_file": {"type": ["string", "null"], "description": "Route Bのみ、外部Uploadファイル参照"},
        "approver": {"type": "string"},
        "approval_date": {"type": "string"},
        "uploaded_by": {"type": ["string", "null"], "description": "Route Bのみ"},
    },
}

# Phase2A: 外部データ（World Bank/OFAC等）由来のKnowledge Candidate種別（11章拡張）

ACTOR_PROFILE_UPDATE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "ActorProfileUpdate",
    "type": "object",
    "additionalProperties": True,
    "required": ["target_entity_id", "change_summary", "why_reusable", "source_metric_or_claim"],
    "properties": {
        "target_entity_id": {"type": "string"},
        "change_summary": {"type": "string"},
        "why_reusable": {"type": "string"},
        "source_metric_or_claim": {"type": "string", "description": "根拠となったMetric Observationまたはclaim_idの説明"},
    },
}

RELATIONSHIP_UPDATE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "RelationshipUpdate",
    "type": "object",
    "additionalProperties": True,
    "required": ["from_entity_id", "to_entity_id", "change_summary", "why_reusable"],
    "properties": {
        "from_entity_id": {"type": "string"},
        "to_entity_id": {"type": "string"},
        "change_summary": {"type": "string"},
        "why_reusable": {"type": "string"},
        "source_metric_or_claim": {"type": ["string", "null"]},
    },
}

DEPENDENCY_UPDATE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "DependencyUpdate",
    "type": "object",
    "additionalProperties": True,
    "required": ["entity_id", "dependency_type", "change_summary", "why_reusable"],
    "properties": {
        "entity_id": {"type": "string"},
        "dependency_type": {"type": "string"},
        "change_summary": {"type": "string"},
        "why_reusable": {"type": "string"},
        "source_metric_or_claim": {"type": ["string", "null"]},
    },
}

SANCTIONS_UPDATE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "SanctionsUpdate",
    "type": "object",
    "additionalProperties": True,
    "required": ["external_record_id", "change_summary", "why_reusable", "as_of_fetched_at"],
    "properties": {
        "external_record_id": {"type": "string"},
        "matched_entity_id": {"type": ["string", "null"]},
        "change_summary": {"type": "string"},
        "why_reusable": {"type": "string"},
        "as_of_fetched_at": {"type": "string", "description": "取得日時点を明示する（現在も有効とは断定しない）"},
    },
}

# Media Intelligence Build（M5）: Stable Media Profile（媒体単位の比較的安定した
# Intelligence）。記事一覧・ニュース要約DBにしない（依頼書Section10）。単発Episodeから
# Patternを確定しない、という原則を守るため、Profile自身はEpisode/Pattern IDを持たず、
# 参照は逆方向（Episode.source_id / Pattern.evidence_episode_ids）のみとする。
# 媒体の多軸メタデータ（media_type/lens/country_or_region等）はfile-based
# docs/media-intelligence/media_source_registry.jsonが正本であり、ここでは重複させない
# （source_idで参照するのみ）。
MEDIA_PROFILE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "MediaProfile",
    "type": "object",
    "additionalProperties": True,
    "required": ["source_id", "source_name", "confidence"],
    "properties": {
        "source_id": {"type": "string", "description": "docs/media-intelligence/media_source_registry.jsonのsource_idと一致させる"},
        "source_name": {"type": "string"},
        "ownership_or_governance": {"type": ["string", "null"]},
        "institutional_perspective": {
            "type": ["string", "null"],
            "description": "biasという単純属性ではなく『何を見るために有効か』を表す一文（依頼書Section7）",
        },
        "primary_audience": {"type": ["string", "null"]},
        "domestic_or_international": {"type": ["string", "null"]},
        "coverage_strengths": {"type": "array", "items": {"type": "string"}},
        "government_military_business_access": {"type": ["string", "null"]},
        "typical_source_types": {
            "type": "array", "items": {"type": "string"},
            "description": "例: government/military/diplomatic/party/business/intelligence/local_witness/ngo（依頼書Section14）",
        },
        "named_vs_anonymous_tendency": {"type": ["string", "null"]},
        "historical_framing_characteristics": {"type": ["string", "null"]},
        "strategic_signaling_relevance": {"type": ["string", "null"]},
        "known_limitations": {"type": "array", "items": {"type": "string"}},
        "use_for": {"type": "array", "items": {"type": "string"}},
        "do_not_use_for": {"type": "array", "items": {"type": "string"}},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": _CONFIDENCE_ENUM},
        "last_reviewed_at": {"type": ["string", "null"]},
    },
}

# Media Intelligence Build（M6）: Historical Media Episode（依頼書Section12のSchema例に
# 準拠）。observable_framingは意図を語らないObservedカテゴリ、hypothesized_information_role
# はHypothesisカテゴリとして明確に分離する（既存media_editorial_context.observed/hypotheses
# と同じ原則、依頼書Section13）。
MEDIA_EPISODE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "MediaEpisode",
    "type": "object",
    "additionalProperties": True,
    "required": ["media_episode_id", "source_id", "event", "period_start", "confidence"],
    "properties": {
        "media_episode_id": {"type": "string", "description": "Pattern候補のevidence_episode_ids/exception_episode_idsから参照されるローカル参照ID"},
        "source_id": {"type": "string", "description": "media_source_registry.jsonのsource_id"},
        "event": {"type": "string"},
        "period_start": {"type": "string"},
        "period_end": {"type": ["string", "null"]},
        "headline_or_frame": {"type": ["string", "null"]},
        "source_selection": {"type": ["string", "null"]},
        "source_ordering": {"type": ["string", "null"]},
        "named_vs_anonymous": {
            "type": ["string", "null"],
            "enum": ["named", "anonymous_official", "anonymous_unspecified", "unattributed", None],
        },
        "actors_emphasized": {"type": "array", "items": {"type": "string"}},
        "actors_omitted": {"type": "array", "items": {"type": "string"}},
        "observable_framing": {
            "type": ["string", "null"],
            "description": "Observedのみ。意図・目的を語らない事実表現に限定する（例:『Actor Xへの言及が本文中に存在しない』は可、『意図的に省いた』は不可、後者はhypothesized_information_role側へ）",
        },
        "hypothesized_information_role": {
            "type": ["string", "null"],
            "description": "Hypothesis。編集意図・情報源シグナリング・受け手反応の仮説はすべてここに置く",
        },
        "later_confirmed_or_not": {"type": ["string", "null"]},
        "alternative_explanation": {"type": ["string", "null"]},
        "corroboration_basis": {
            "type": ["string", "null"],
            "enum": ["independent_corroboration", "official_confirmation", "behavioral_corroboration",
                     "documentary_corroboration", "single_source_only", "unclear", None],
            "description": "既存media_editorial_context.hypotheses[].corroboration_basisと同じ思想（依頼書Section15）",
        },
        "corroboration_target": {
            "type": ["string", "null"],
            "enum": ["hypothesis_itself", "underlying_event_only", "unclear", None],
        },
        "supporting_evidence_ids": {"type": "array", "items": {"type": "string"}},
        "contradicting_evidence_ids": {"type": "array", "items": {"type": "string"}},
        "source_urls_or_citations": {
            "type": "array", "items": {"type": "string"},
            "description": "既存geo_claims/geo_documentsのIDがまだ存在しない新規調査の場合の暫定的な出典明記（Actor Historical Episode構築時のprecedent.sourcesと同じ扱い、正式なEvidence ID化はGate2以降の課題）",
        },
        "confidence": {"type": "string", "enum": _CONFIDENCE_ENUM},
    },
}

# Media Intelligence Build（M8）: Media Pattern Candidate（依頼書Section20のSchema例に準拠）。
# 複数Historical Episodeからのみ生成し、単発記事からは確定しない（依頼書Section22）。
# pattern_id/pattern_role/default_pattern_ref/exception_conditionsは既存Actor
# decision_patternsの矛盾保持パターン（Session2 Reasoning Upgrade Phase2A）と同型にする。
MEDIA_PATTERN_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "MediaPatternCandidate",
    "type": "object",
    "additionalProperties": True,
    "required": ["pattern", "when_it_applies", "evidence_episode_ids", "confidence"],
    "properties": {
        "pattern": {"type": "string"},
        "when_it_applies": {"type": "string"},
        "typical_source_pattern": {"type": ["string", "null"]},
        "typical_framing_pattern": {"type": ["string", "null"]},
        "evidence_episode_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "exception_episode_ids": {"type": "array", "items": {"type": "string"}},
        "pattern_id": {"type": ["string", "null"]},
        "pattern_role": {"type": ["string", "null"], "enum": ["default", "exception", None]},
        "default_pattern_ref": {"type": ["string", "null"]},
        "exception_conditions": {"type": ["array", "null"], "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": _CONFIDENCE_ENUM},
        "last_reviewed_at": {"type": ["string", "null"]},
    },
}

SCHEMAS_BY_KNOWLEDGE_TYPE = {
    "precedent": PRECEDENT_SCHEMA,
    "mechanism": MECHANISM_SCHEMA,
    "reasoning_rule": REASONING_RULE_SCHEMA,
    "indicator": INDICATOR_SCHEMA,
    "leverage_card": LEVERAGE_CARD_SCHEMA,
    "country_foundation": COUNTRY_FOUNDATION_SCHEMA,
    "actor_profile": ACTOR_PROFILE_SCHEMA,
    "institution_profile": INSTITUTION_PROFILE_SCHEMA,
    "party_profile": PARTY_PROFILE_SCHEMA,
    "relationship_assessment": RELATIONSHIP_ASSESSMENT_SCHEMA,
    "historical_path_dependency": HISTORICAL_PATH_DEPENDENCY_SCHEMA,
    "foundation_update": FOUNDATION_UPDATE_SCHEMA,
    "case_finding": CASE_FINDING_SCHEMA,
    "report_finding": REPORT_FINDING_SCHEMA,
    "approved_report": APPROVED_REPORT_SCHEMA,
    "actor_profile_update": ACTOR_PROFILE_UPDATE_SCHEMA,
    "relationship_update": RELATIONSHIP_UPDATE_SCHEMA,
    "dependency_update": DEPENDENCY_UPDATE_SCHEMA,
    "sanctions_update": SANCTIONS_UPDATE_SCHEMA,
    "media_profile": MEDIA_PROFILE_SCHEMA,
    "media_episode": MEDIA_EPISODE_SCHEMA,
    "media_pattern": MEDIA_PATTERN_SCHEMA,
}

KNOWLEDGE_TYPES = tuple(SCHEMAS_BY_KNOWLEDGE_TYPE.keys())


class KnowledgeContentValidationError(ValueError):
    pass


def validate_structured_content(knowledge_type: str, structured_content: dict) -> None:
    """knowledge_typeに対応するJSON Schemaでstructured_contentを検証する。
    未知のknowledge_typeやSchema違反はKnowledgeContentValidationErrorを送出する。"""
    schema = SCHEMAS_BY_KNOWLEDGE_TYPE.get(knowledge_type)
    if schema is None:
        raise KnowledgeContentValidationError(f"未対応のknowledge_typeです: {knowledge_type}")
    try:
        jsonschema.validate(structured_content, schema)
    except jsonschema.ValidationError as e:
        raise KnowledgeContentValidationError(f"structured_contentがSchemaに違反しています: {e.message}") from e
