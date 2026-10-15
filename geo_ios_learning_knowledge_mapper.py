"""Geopolitical Intelligence OS Phase G §22: Learning Candidate → Knowledge Type Mapper。

Candidate Typeごとに「どの既存Knowledge Type / Schemaへ変換するか」を1箇所へ集約する。
API/UIにknowledge_type/structured_contentを自由入力させない（§22絶対条件）——promote操作は
このMapperの出力のみを使う。

設計判断（Phase G監査で確認した既存schemaの制約に基づく）:
- reasoning_rule系8種（cqas_question_pattern/scenario_pattern/reasoning_rule/attention_rule/
  failure_pattern/evidence_rule/source_preference/report_design_pattern/
  intelligence_need_pattern）は、既存REASONING_RULE_SCHEMA（geo_knowledge_item_schemas.py）が
  rule/rationale/required_checks/common_failure_modes/rule_categoryを既に持ち、Learning
  Candidateの内容から誠実に（捏造せず）埋められるため、knowledge_type="reasoning_rule"へ
  自動マッピングする。rule_categoryはcandidate_typeごとに既存enum値へ対応させる
  （新しいenum値は追加しない）。
- actor_intelligenceはknowledge_type="actor_profile_update"（既存、target_entity_id/
  change_summary/why_reusable/source_metric_or_claimが必須）。ただしtarget_entity_idは
  Learning Candidate自身のschemaに無く、システムが推測してはならない情報のため、promote時に
  人間が明示的に指定する必要がある（自由入力ではなく、この1フィールドのみの限定入力）。
- media_intelligenceはknowledge_type="media_pattern"の必須項目evidence_episode_ids
  （minItems:1、実在するMedia Episodeを1件以上参照する必要がある）を、Report Reviewという
  文脈からは誠実に埋められない（存在しないEpisode IDを捏造することになる）ため、Phase Gでは
  自動マッピング対象外とする——Candidateとして保持されるのみで、promote_to_knowledge_itemは
  拒否する（§25「推測でallowしない」と同じ判断基準）。
- monitoring_indicatorはknowledge_type="indicator"のsource/query_or_collection_method/owner
  等、Learning Candidateからは分からない運用情報が必須のため、同様にPhase Gでは自動マッピング
  対象外とする。
"""

REASONING_RULE_CANDIDATE_TYPES = {
    "cqas_question_pattern": "stakeholder_selection",
    "scenario_pattern": "scenario_reasoning",
    "reasoning_rule": "general",
    "attention_rule": "general",
    "failure_pattern": "general",
    "evidence_rule": "evidence_weighting",
    "source_preference": "evidence_weighting",
    "report_design_pattern": "general",
    "intelligence_need_pattern": "general",
}

# candidate_type -> 対応するgeo_knowledge_items.knowledge_type。Noneは「Phase Gでは
# 自動マッピング非対応」を意味する（media_intelligence/monitoring_indicator）。
CANDIDATE_TYPE_TO_KNOWLEDGE_TYPE = {
    **{t: "reasoning_rule" for t in REASONING_RULE_CANDIDATE_TYPES},
    "actor_intelligence": "actor_profile_update",
    "media_intelligence": None,
    "monitoring_indicator": None,
}

NOT_AUTO_MAPPABLE_REASON = {
    "media_intelligence": (
        "media_pattern知識型はevidence_episode_ids（実在するMedia Episode、1件以上）を必須と"
        "するが、Report Reviewからの学習ではEpisode IDを誠実に特定できないため、Phase Gでは"
        "自動promoteの対象外です。必要な場合は既存のMedia Episode Candidate経路から別途登録"
        "してください。"
    ),
    "monitoring_indicator": (
        "indicator知識型はsource/query_or_collection_method/owner等の運用情報を必須とするが、"
        "Learning Candidateはこれらを知り得ないため、Phase Gでは自動promoteの対象外です。"
        "必要な場合は人間が既存/api/geo/knowledge/itemsから手動でIndicator Cardを作成して"
        "ください。"
    ),
}


class MappingError(Exception):
    """Candidate → Knowledge Type/structured_contentへの変換に関するエラー。"""


def _build_reasoning_rule_content(candidate: dict) -> dict:
    trigger_context = candidate.get("trigger_context") or {}
    rule_category = REASONING_RULE_CANDIDATE_TYPES[candidate["candidate_type"]]
    exceptions = trigger_context.get("exceptions") or []
    return {
        "rule": candidate["generalized_rule"],
        "rationale": candidate["observation"],
        "applicable_question_types": trigger_context.get("applicable_question_types") or [],
        "required_checks": trigger_context.get("required_checks") or [],
        "comparison_dimensions": trigger_context.get("comparison_dimensions") or [],
        "common_failure_modes": (
            [candidate["failure_pattern_example"]] if candidate.get("failure_pattern_example") else []),
        "exceptions": exceptions,
        "related_precedents": [],
        "related_mechanisms": [],
        "expert_source": trigger_context.get("expert_source") or "geo_ios_learning_extraction",
        "approval_status": "human_approved",
        "rule_category": rule_category,
    }


def _build_actor_profile_update_content(candidate: dict, *, target_entity_id: str) -> dict:
    if not target_entity_id:
        raise MappingError(
            "actor_intelligence候補のpromoteにはtarget_entity_id（対象Entity）の指定が必須です"
            "（システムが推測してはならない情報のため、人間が明示してください）")
    trigger_context = candidate.get("trigger_context") or {}
    evidence = trigger_context.get("evidence_chain") or candidate.get("observation")
    return {
        "target_entity_id": target_entity_id,
        "change_summary": candidate["generalized_rule"],
        "why_reusable": candidate["observation"],
        "source_metric_or_claim": str(evidence),
    }


def map_candidate_to_knowledge(candidate: dict, *, target_entity_id: str = None) -> tuple:
    """candidate(dict, geo_ios_learning_candidatesの行)を(knowledge_type, structured_content)
    へ変換する。自動マッピング非対応のcandidate_typeにはMappingErrorを送出する。"""
    candidate_type = candidate["candidate_type"]
    knowledge_type = CANDIDATE_TYPE_TO_KNOWLEDGE_TYPE.get(candidate_type)
    if knowledge_type is None:
        reason = NOT_AUTO_MAPPABLE_REASON.get(
            candidate_type, f"candidate_type={candidate_type}は自動マッピング非対応です。")
        raise MappingError(reason)

    if knowledge_type == "reasoning_rule":
        return knowledge_type, _build_reasoning_rule_content(candidate)
    if knowledge_type == "actor_profile_update":
        return knowledge_type, _build_actor_profile_update_content(candidate, target_entity_id=target_entity_id)
    raise MappingError(f"未対応のknowledge_typeマッピングです: {knowledge_type}")  # pragma: no cover
