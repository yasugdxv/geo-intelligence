"""Report Studio: Synthesis Service（Prompt2相当）+ 既存Expert AI Core統合 + Expert Review

Narrative Master・Validated Classification（validity_status=PASSのgeo_report_clusters）・
Evidence/Counterevidence（representatives/record_level_exceptions）・
Comparison/Visualization config・Continuity contextから、
central_assessment/findings/drivers/counterforces/implications/watchpointsを構造化出力する。

既存Expert AI Core（Actor Intelligence/Media Intelligence）を再利用するが、
Report StudioのNarrativeは既存geo_cases/geo_issuesに紐づかない（Case化すると
Report StudioのNarrativeが既存Expert AIのCase一覧と混同されるため、あえて避ける）。
そのため`build_actor_intelligence_pack`のCase起点Selection（select_key_actors、
geo_case_issues前提）は使わず、Narrative Masterの`actor_names`列から`geo_entities`を
名前一致で解決し、entity_id起点の関数（get_actor_profile_item_by_entity_id/
_relevant_precedents_for_entity/_relevant_relationships_between）のみを使って
既存`_format_actor_intelligence_block`と同じpack形状を構築し、同じformatterで整形する
（Actor Intelligence自体のフォーマット・トリム・token予算ロジックは重複実装しない）。

**Observed EvidenceとExpert Contextの分離が本サービスの核**: 各Finding等は
`provenance.evidence_basis`（observed/expert_context/mixed）を必ず明示し、
`evidence_basis=observed`の項目にはexpert_context_refsを一切持たせない
（Expert AIの一般知識がObserved Evidenceへ逆流するのを防ぐ、Quality Guardで検証）。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Optional

import geo_actor_intelligence_service as actor_intel
import geo_llm_common
import geo_media_intelligence_service as media_intel
import geo_report_classification_service as classify
import geo_report_processing_service as processing
from geo_expert_engine import _format_actor_intelligence_block, _format_media_intelligence_block

SYNTHESIS_SCHEMA_NAME = "geo_report_narrative_synthesis"
REVIEW_SCHEMA_NAME = "geo_report_expert_review"

_CONTINUITY_STATES = [
    "New", "Continuing", "Expanding", "Intensifying", "Stable", "Fragmenting",
    "Weakening", "Dormant", "Resolved", "Reframed", "Insufficient evidence",
]

_REVIEW_AXES = [
    "superficial_interpretation", "actor_objective_inconsistency",
    "insufficient_counter_hypotheses", "missing_counterevidence",
    "media_framing_dependence", "causal_leap",
]

_CHANGE_TYPES = ["Intensified", "Weakened", "Reframed", "Stable", "New", "Resolved", "Insufficient_evidence"]
_WATCHPOINT_STATUSES = ["Triggered", "Possible_trigger", "Not_observed", "Evidence_insufficient"]

_SYNTHESIS_SYSTEM_PROMPT = """あなたはReport Studioの意思決定レポート統合エンジン（Prompt2相当）です。
以下を厳守してください。

1. 「Observed Evidence（この収集データで実際に確認された内容）」と「Expert Context
   （Actor Intelligence・Media Intelligence等、Expert AIが一般に保持している背景知識）」
   を絶対に混同しない。各項目には必ずprovenance.evidence_basisを明示し、
   evidence_basis="observed"の項目にexpert_context_refsを入れてはならない
   （Expert Contextの知識で「本文にない事実」を埋めることを禁止する）。
2. Expert ContextはObserved Evidenceの解釈・重み付けの補助としてのみ使う
   （Actor/Mediaの一般的傾向から、収集データに無い具体的事実・数値・発言を作らない）。
3. Observed Evidence自体が疎な場合は、無理に厚みを出さず正直にfindingsを絞る、
   またはconfidenceを下げる。件数を稼ぐための水増しをしない。
3.5. 各項目のprovenance.counterevidence_record_idsには、その主張と矛盾する・留保をつける
   観測記事があれば必ず挙げること（無理に探さなくてよいが、実際に矛盾する記事があるのに
   省略しない）。
4. QA・パイプラインの内部事情（validity_status・クラスタID・件数統計等）を
   summary/statement等の本文表現に出さない（それらはprovenanceフィールドに格納する）。

## Continuity（前回Synthesisとの比較、assessment_delta / watchpoint_updates）

5. assessment_deltaは、前回Synthesisの内容（central_assessment・findings・
   continuity_assessment）と今回のObserved Evidenceを**意味的に**比較して書く。
   件数の増減だけを理由にした変化判定は禁止する（「記事が20件→40件だから
   Intensified」は不可）。変化が無ければ正直にchange_type="Stable"、比較材料が
   無ければ"Insufficient_evidence"を選ぶ。前回情報が全く無い場合（初回観測）は
   assessment_deltaを空配列にする。
6. watchpoint_updatesは、前回のwatchpointsの一つ一つについて、今回のObserved
   Evidenceで実際にtrigger_conditionが観測されたかを判定する。観測されなければ
   憶測で"Triggered"にしない。前回watchpointsが無ければ空配列にする。
7. assessment_delta/watchpoint_updatesも他の項目と同じくprovenance
   （evidence_basis/observed_record_ids/expert_context_refs/
   counterevidence_record_ids）を厳格に守ること。「前回はこうだった」という
   事実は前回情報（プロンプトで明示的に渡された前回データ）を根拠とし、
   「なぜそう変化したか」の解釈がExpert Contextに基づく場合のみ
   expert_context_refsを使う。

## Draft Evidence Discipline（各findingを確定する前の自己チェック）

8. central_assessment・findings・driversの各文を確定する前に、必ず以下を自問すること：
   a. この記述はObserved Evidenceに直接裏付けられているか。
   b. 特定媒体の見出し・フレーミングを、そのまま確定した事実として扱っていないか
      （media_framing_dependence）。
   c. 時系列の一致・相関だけで因果関係を断定していないか（causal_leap）。
   d. もっともらしい対立仮説・別の解釈を検討したか（insufficient_counter_hypotheses）。
   e. Evidenceが薄い場合、断定するのではなく「不明」「観測できない」と正直に書くべきでは
      ないか。
   この自己チェックはExpert Reviewの代替ではない。Draft側で明らかな癖を先に減らし、
   Expert Reviewがより高次の論点の検証に注力できるようにするためのものであり、
   Reviewを省略してよい理由にはならない。
"""

_REVIEW_SYSTEM_PROMPT = """あなたはReport Studioの統合ドラフトに対するExpert Reviewです。
以下6軸でDraftを批判的に検証し、必要な場合のみrevised_draftを提示してください
（問題が無ければrevised_draftはnullでよい）。

- superficial_interpretation: 表面的な言い換えに留まり、なぜそうなのかの分析が無い
- actor_objective_inconsistency: Actorの目的・選好と矛盾する解釈をしている
- insufficient_counter_hypotheses: 対立仮説・反証の検討が不足している
- missing_counterevidence: 反証となり得るEvidenceが省かれている
- media_framing_dependence: 特定媒体のフレーミングにそのまま依拠している
- causal_leap: 相関・時系列の一致だけで因果を断定している

revised_draftを出す場合は、元のDraftと同じSchemaに完全準拠すること。
Observed Evidence/Expert Contextの分離ルール（evidence_basis="observed"の項目に
expert_context_refsを入れない）は修正時も厳守すること。
"""


# ---------------------------------------------------------------------------
# Observed Evidence Context（Report Studio自身のデータのみ）
# ---------------------------------------------------------------------------

def build_prior_continuity_context(prior_synthesis: dict) -> dict:
    """geo_report_synthesesの1行（get_latest_synthesis等の戻り値）から、
    次回build_observed_evidence_blockのprior_continuity引数を作る。
    前回のcentral_assessment/continuity_assessment/watchpointsのみを渡し、
    前回のfindings本文まるごとは渡さない（token対策、Continuityの比較に
    必要な要約情報だけに絞る）。"""
    draft = prior_synthesis.get("final_draft") or {}
    continuity = draft.get("continuity_assessment") or {}
    return {
        "prior_synthesis_created_at": prior_synthesis.get("created_at"),
        "prior_central_assessment": (draft.get("central_assessment") or {}).get("summary"),
        "prior_continuity_state": continuity.get("state"),
        "prior_continuity_rationale": continuity.get("rationale"),
        "prior_watchpoints": [
            {"watchpoint": w.get("watchpoint"), "trigger_condition": w.get("trigger_condition")}
            for w in draft.get("watchpoints") or []
        ],
        "prior_observed_record_ids": prior_synthesis.get("observed_record_ids") or [],
    }


def build_observed_evidence_block(
    narrative_row_data: dict,
    classified_clusters: list,
    representatives_by_cluster: dict,
    *,
    prior_continuity: Optional[dict] = None,
) -> tuple:
    """戻り値: (prompt_text, known_record_ids, known_cluster_ids, known_prior_record_ids)。
    classified_clustersはvalidity_status=PASSのものだけを渡すこと（呼び出し元の責務）。
    known_prior_record_idsはprior_continuity.prior_observed_record_idsのset化
    （assessment_delta/watchpoint_updatesが前回記録を正当に引用できるようにする）。"""
    lines = []
    lines.append(f"report_topic_label: {narrative_row_data.get('report_topic_label', '')}")
    lines.append(f"narrative_name: {narrative_row_data.get('narrative_name', '')}")
    lines.append(f"narrative_description: {narrative_row_data.get('narrative_description', '')}")

    comparison_config = classify._parse_json_field(narrative_row_data, "comparison_config_json")
    visualization_config = classify._parse_json_field(narrative_row_data, "visualization_config_json")
    if comparison_config:
        lines.append(f"\n## Comparison設定（レポート内で比較すべき軸、値の捏造は禁止）\n{json.dumps(comparison_config, ensure_ascii=False)}")
    if visualization_config:
        lines.append(f"\n## Visualization設定\n{json.dumps(visualization_config, ensure_ascii=False)}")

    known_prior_record_ids = set()
    if prior_continuity:
        known_prior_record_ids = set(prior_continuity.get("prior_observed_record_ids") or [])
        lines.append(
            "\n## 前回までのContinuity（意味的な状態の比較用。単純な件数差分ではない。"
            "assessment_delta/watchpoint_updatesはここの情報と今回のObserved Evidenceを比較して書くこと）\n"
            f"- prior_synthesis_created_at: {prior_continuity.get('prior_synthesis_created_at')}\n"
            f"- prior_central_assessment: {prior_continuity.get('prior_central_assessment')}\n"
            f"- prior_continuity_state: {prior_continuity.get('prior_continuity_state')}\n"
            f"- prior_continuity_rationale: {prior_continuity.get('prior_continuity_rationale')}\n"
            f"- prior_watchpoints: {json.dumps(prior_continuity.get('prior_watchpoints') or [], ensure_ascii=False)}"
        )
    else:
        lines.append("\n## Continuity: 初回観測（baseline_status=Pre-observation、比較対象なし。"
                      "assessment_delta/watchpoint_updatesは空配列にすること）")

    known_record_ids = set()
    known_cluster_ids = set()

    lines.append("\n## Validated Classification（クラスタ単位、Prompt1相当のLLM分類済み・整合性検証済み）")
    for cluster in classified_clusters:
        cluster_id = cluster.get("cluster_id")
        known_cluster_ids.add(cluster_id)
        classification = (cluster.get("classification") or {}).get("result") or {}
        dimensions = classification.get("dimensions") or {}
        lines.append(f"\n### cluster_id: {cluster_id}（record_count={cluster.get('record_count')}）")
        for dim_id, entry in dimensions.items():
            lines.append(
                f"- {dim_id}: value={entry.get('value')} apply_to_cluster={entry.get('apply_to_cluster')} "
                f"confidence={entry.get('confidence')} rationale={entry.get('rationale')}"
            )
        for exc in classification.get("record_level_exceptions") or []:
            lines.append(f"  [record-level] {exc.get('dimension_id')}@{exc.get('record_id')}: {exc.get('value')} ({exc.get('rationale')})")

        reps = representatives_by_cluster.get(cluster_id, [])
        lines.append("  代表記事:")
        for rep in reps:
            rid = rep.get("record_id")
            known_record_ids.add(rid)
            lines.append(
                f"  - [record_id: {rid}] {rep.get('title') or ''} / 出典: {rep.get('source_name') or ''} "
                f"/ 公開: {rep.get('published_at') or ''}\n"
                f"    本文: {rep.get('text_for_review') or ''}"
            )

    return "\n".join(lines), known_record_ids, known_cluster_ids, known_prior_record_ids


# ---------------------------------------------------------------------------
# Expert Context（既存Expert AI Coreの再利用、entity_id起点）
# ---------------------------------------------------------------------------

def resolve_actor_entity_ids_by_name(client, actor_names: list) -> dict:
    """Narrative Masterのactor_names（表示名）をgeo_entities.canonical_nameで
    決定的に解決する（あいまい一致・LLMは使わない）。"""
    result = {}
    for name in actor_names:
        name = (name or "").strip()
        if not name:
            continue
        rows = client.select("geo_entities", {"select": "entity_id,canonical_name", "canonical_name": f"eq.{name}"})
        if rows:
            result[name] = rows[0]["entity_id"]
    return result


def build_entity_scoped_actor_intelligence_pack(
    client, entity_ids_by_name: dict, *, reference_date: Optional[date] = None,
    profile_lookup_statuses: tuple = ("active",), profile_retrieval_status: Optional[str] = "available",
) -> dict:
    """build_actor_intelligence_packと同じpack形状をCase/Issueに依存せず構築する
    （Report StudioのNarrativeには紐づくgeo_case_issuesが無いため、issue_ids=[]扱い）。"""
    reference_date = reference_date or date.today()
    entity_ids = list(entity_ids_by_name.values())
    known_ids = set()
    actor_packs = []

    for name, entity_id in entity_ids_by_name.items():
        profile_item = actor_intel.get_actor_profile_item_by_entity_id(
            client, entity_id, retrieval_status=profile_retrieval_status, statuses=profile_lookup_statuses)
        content = (profile_item.get("structured_content") or {}) if profile_item else {}
        current_posture = content.get("current_posture")
        freshness = None
        if current_posture and current_posture.get("as_of"):
            freshness = actor_intel.compute_freshness(current_posture["as_of"], reference_date=reference_date)
        decision_patterns = content.get("decision_patterns") or []
        precedents = actor_intel._relevant_precedents_for_entity(
            client, entity_id, retrieval_status=profile_retrieval_status, statuses=profile_lookup_statuses)

        for dp in decision_patterns:
            known_ids.update(dp.get("supporting_precedent_ids") or [])
            known_ids.update(dp.get("supporting_evidence_ids") or [])
            known_ids.update(dp.get("counter_evidence_ids") or [])
        for p in precedents:
            if p.get("knowledge_item_id"):
                known_ids.add(p.get("knowledge_item_id"))
        if current_posture:
            known_ids.update(current_posture.get("evidence_ids") or [])

        actor_packs.append({
            "entity_id": entity_id, "actor_intelligence_id": None, "canonical_name": name,
            "selection_reasons": ["Narrative Masterのactor_namesに明示的に記載"],
            "relevance_score": None,
            "actor_intelligence_status": actor_intel._actor_intelligence_status_for(profile_item, [], reference_date),
            "profile_source": (
                "none_available" if profile_item is None
                else "actor_intelligence_active" if profile_item.get("status") == "active"
                else "actor_intelligence_candidate"
            ),
            "pattern_ids": [dp.get("pattern_id") for dp in decision_patterns if dp.get("pattern_id")],
            "exception_episode_ids": [],
            "historical_episode_ids": [p.get("knowledge_item_id") for p in precedents if p.get("knowledge_item_id")],
            "core_profile": content.get("core_profile"),
            "persistent_preference": content.get("persistent_preference"),
            "strategic_communication_pattern": content.get("strategic_communication_pattern"),
            "decision_patterns": decision_patterns,
            "current_posture": ({**current_posture, "freshness": freshness} if current_posture else None),
            "issue_position": None,
            "relevant_precedents": [
                {"knowledge_item_id": p.get("knowledge_item_id"), "title": p.get("title")} for p in precedents
            ],
            "relevant_relationships": [],
        })

    if entity_ids:
        reference_relationships = actor_intel._relevant_relationships_between(client, entity_ids, reference_date)
        for pack in actor_packs:
            pack["relevant_relationships"] = [
                {"relationship_id": r.get("relationship_id"), "relationship_domain": r.get("relationship_domain"),
                 "relationship_type": r.get("relationship_type"), "leverage_direction": r.get("leverage_direction")}
                for r in reference_relationships
            ]

    known_ids.discard(None)
    return {
        "actor_intelligence_status": "sufficient" if any(
            p["actor_intelligence_status"] == "sufficient" for p in actor_packs) else "partial",
        "reference_date": reference_date.isoformat(),
        "key_actors": actor_packs,
        "known_ids": known_ids,
    }


def build_expert_context_block(
    client, actor_names: list, *, reference_date: Optional[date] = None,
    profile_lookup_statuses: tuple = ("active",), profile_retrieval_status: Optional[str] = "available",
    media_statuses: tuple = ("active",),
) -> tuple:
    """戻り値: (prompt_text, known_expert_context_ref_ids)。actor_names未指定/未解決の
    場合はActor Intelligence部分は空になる（Media Intelligenceのみ）。
    profile_lookup_statuses/profile_retrieval_status/media_statusesの既定は本番相当
    （status=active、Actor側はretrieval_status=availableのみ参照）。比較実験・検証時のみ
    呼び出し元が明示的に上書きする（M9 Actor×Media Integration Pilotと同じ設計）。"""
    entity_ids_by_name = resolve_actor_entity_ids_by_name(client, actor_names) if actor_names else {}
    known_refs = set()
    blocks = []

    if entity_ids_by_name:
        actor_pack = build_entity_scoped_actor_intelligence_pack(
            client, entity_ids_by_name,
            profile_lookup_statuses=profile_lookup_statuses, profile_retrieval_status=profile_retrieval_status)
        known_refs.update(actor_pack.get("known_ids") or set())
        for p in actor_pack["key_actors"]:
            known_refs.add(p["entity_id"])
        actor_text = _format_actor_intelligence_block(actor_pack)
        if actor_text:
            blocks.append(actor_text)

    media_pack = media_intel.build_media_intelligence_pack(client, statuses=media_statuses)
    known_refs.update(media_pack.get("known_ids") or set())
    media_text = _format_media_intelligence_block(media_pack)
    if media_text:
        blocks.append(media_text)

    known_refs.discard(None)
    return "\n\n".join(blocks), known_refs


# ---------------------------------------------------------------------------
# Synthesis Schema / LLM呼び出し
# ---------------------------------------------------------------------------

_PROVENANCE_SCHEMA = {
    "type": "object",
    "properties": {
        "evidence_basis": {"type": "string", "enum": ["observed", "expert_context", "mixed"]},
        "observed_record_ids": {"type": "array", "items": {"type": "string"}},
        "observed_cluster_ids": {"type": "array", "items": {"type": "string"}},
        "expert_context_refs": {"type": "array", "items": {"type": "string"}},
        "counterevidence_record_ids": {
            "type": "array", "items": {"type": "string"},
            "description": "この項目の主張と矛盾する、または留保をつける観測記事のrecord_id"
                           "（無ければ空配列。observed_record_idsと同じ既知集合からのみ選ぶ）",
        },
    },
    "required": ["evidence_basis", "observed_record_ids", "observed_cluster_ids", "expert_context_refs",
                 "counterevidence_record_ids"],
    "additionalProperties": False,
}


def _item_schema(extra_properties: dict, required_extra: list) -> dict:
    props = dict(extra_properties)
    props["provenance"] = _PROVENANCE_SCHEMA
    props["confidence"] = {"type": "string", "enum": ["High", "Medium", "Low"]}
    return {
        "type": "object",
        "properties": props,
        "required": required_extra + ["provenance", "confidence"],
        "additionalProperties": False,
    }


def build_synthesis_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "central_assessment": _item_schema({"summary": {"type": "string"}}, ["summary"]),
            "findings": {"type": "array", "items": _item_schema(
                {"finding_id": {"type": "string"}, "statement": {"type": "string"},
                 "why_it_matters": {"type": "string"}},
                ["finding_id", "statement", "why_it_matters"])},
            "drivers": {"type": "array", "items": _item_schema(
                {"driver": {"type": "string"}, "mechanism": {"type": "string"}}, ["driver", "mechanism"])},
            "counterforces": {"type": "array", "items": _item_schema(
                {"counterforce": {"type": "string"}, "mechanism": {"type": "string"}},
                ["counterforce", "mechanism"])},
            "implications": {"type": "array", "items": _item_schema(
                {"implication": {"type": "string"}, "for_whom": {"type": "string"}},
                ["implication", "for_whom"])},
            "watchpoints": {"type": "array", "items": _item_schema(
                {"watchpoint": {"type": "string"}, "trigger_condition": {"type": "string"}},
                ["watchpoint", "trigger_condition"])},
            "continuity_assessment": {
                "type": "object",
                "properties": {
                    "state": {"type": "string", "enum": _CONTINUITY_STATES},
                    "rationale": {"type": "string"},
                },
                "required": ["state", "rationale"],
                "additionalProperties": False,
            },
            "assessment_delta": {
                "type": "array",
                "description": "前回Synthesisとの意味的な比較（前回が無い＝初回観測の場合は空配列）。"
                                "件数の増減だけを理由にしない（例:『記事が20件→40件だからIntensifying』は禁止）。",
                "items": _item_schema(
                    {"item": {"type": "string"}, "change_type": {"type": "string", "enum": _CHANGE_TYPES}},
                    ["item", "change_type"])},
            "watchpoint_updates": {
                "type": "array",
                "description": "前回Synthesisのwatchpointsを今回のObserved Evidenceと照合した結果"
                                "（前回watchpointsが無ければ空配列）。",
                "items": _item_schema(
                    {"watchpoint": {"type": "string"}, "status": {"type": "string", "enum": _WATCHPOINT_STATUSES}},
                    ["watchpoint", "status"])},
        },
        "required": ["central_assessment", "findings", "drivers", "counterforces", "implications",
                     "watchpoints", "continuity_assessment", "assessment_delta", "watchpoint_updates"],
        "additionalProperties": False,
    }


def build_review_schema() -> dict:
    draft_schema = build_synthesis_schema()
    return {
        "type": "object",
        "properties": {
            "review_verdict": {"type": "string", "enum": ["approved", "revised", "needs_human_review"]},
            "issues_found": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "axis": {"type": "string", "enum": _REVIEW_AXES},
                        "target": {"type": "string"},
                        "detail": {"type": "string"},
                    },
                    "required": ["axis", "target", "detail"],
                    "additionalProperties": False,
                },
            },
            "revised_draft": {"anyOf": [draft_schema, {"type": "null"}]},
        },
        "required": ["review_verdict", "issues_found", "revised_draft"],
        "additionalProperties": False,
    }


def run_synthesis(azure_client, model: str, observed_block: str, expert_block: str, *, max_attempts: int = 2) -> dict:
    schema = build_synthesis_schema()
    user_prompt = (
        f"## Observed Evidence Context（Report Studio自身の収集・分類データ）\n{observed_block}\n\n"
        f"## Expert Context（既存Expert AI Coreの背景知識、参考情報として使ってよいが"
        f"Observed Evidenceとして扱わないこと）\n{expert_block or '（Expert Contextなし）'}\n\n"
        "上記からcentral_assessment/findings/drivers/counterforces/implications/watchpoints/"
        "continuity_assessmentを構造化出力してください。"
    )
    return geo_llm_common.call_llm_structured(
        azure_client, model, _SYNTHESIS_SYSTEM_PROMPT, user_prompt, schema, SYNTHESIS_SCHEMA_NAME,
        max_attempts=max_attempts,
    )


def run_expert_review(azure_client, model: str, draft: dict, observed_block: str, *, max_attempts: int = 2) -> dict:
    schema = build_review_schema()
    user_prompt = (
        f"## Draft\n{json.dumps(draft, ensure_ascii=False)}\n\n"
        f"## Observed Evidence Context（参照用）\n{observed_block}\n\n"
        "6軸でDraftをレビューし、review_verdictとissues_foundを返してください。"
        "修正が必要な場合のみrevised_draftを埋めてください（不要ならnull）。"
    )
    return geo_llm_common.call_llm_structured(
        azure_client, model, _REVIEW_SYSTEM_PROMPT, user_prompt, schema, REVIEW_SCHEMA_NAME,
        max_attempts=max_attempts,
    )


# ---------------------------------------------------------------------------
# Anti-Backflow Guard（決定的、非LLM）
# ---------------------------------------------------------------------------

def _iter_synthesis_items(draft: dict):
    yield "central_assessment", draft.get("central_assessment") or {}
    for key in ("findings", "drivers", "counterforces", "implications", "watchpoints",
                "assessment_delta", "watchpoint_updates"):
        for i, item in enumerate(draft.get(key) or []):
            yield f"{key}[{i}]", item


def run_synthesis_validity_checks(
    draft: dict, known_observed_record_ids: set, known_observed_cluster_ids: set, known_expert_context_ids: set,
) -> dict:
    """Prompt1のQuality Guardと同じ思想: 非repair・人間レビュー材料。
    最重要チェックは`observed_expert_context_separation`（evidence_basis=observedの項目に
    expert_context_refsが混入していないか）"""
    checks: dict = {}

    separation_violations = []
    fabricated_observed = []
    fabricated_expert = []
    missing_evidence = []

    for label, item in _iter_synthesis_items(draft):
        provenance = item.get("provenance") or {}
        basis = provenance.get("evidence_basis")
        observed_records = provenance.get("observed_record_ids") or []
        observed_clusters = provenance.get("observed_cluster_ids") or []
        expert_refs = provenance.get("expert_context_refs") or []
        counterevidence_records = provenance.get("counterevidence_record_ids") or []

        if basis == "observed" and expert_refs:
            separation_violations.append(label)
        if basis in ("observed", "mixed") and not (observed_records or observed_clusters):
            missing_evidence.append(label)
        if basis == "expert_context" and not expert_refs:
            missing_evidence.append(label)

        for rid in observed_records:
            if rid not in known_observed_record_ids:
                fabricated_observed.append((label, rid))
        for cid in observed_clusters:
            if cid not in known_observed_cluster_ids:
                fabricated_observed.append((label, cid))
        for ref in expert_refs:
            if ref not in known_expert_context_ids:
                fabricated_expert.append((label, ref))
        for rid in counterevidence_records:
            if rid not in known_observed_record_ids:
                fabricated_observed.append((label, rid))

    checks["observed_expert_context_separation"] = {
        "passed": not separation_violations,
        "detail": f"evidence_basis=observedなのにexpert_context_refsを持つ項目: {separation_violations}"
                  if separation_violations else "",
    }
    checks["provenance_evidence_present"] = {
        "passed": not missing_evidence,
        "detail": f"evidence_basisの割に根拠が空の項目: {missing_evidence}" if missing_evidence else "",
    }
    checks["observed_ids_not_fabricated"] = {
        "passed": not fabricated_observed,
        "detail": f"存在しないrecord_id/cluster_idを引用: {fabricated_observed}" if fabricated_observed else "",
    }
    checks["expert_context_refs_not_fabricated"] = {
        "passed": not fabricated_expert,
        "detail": f"存在しないExpert Context参照ID: {fabricated_expert}" if fabricated_expert else "",
    }
    return checks


def synthesis_validity_status(checks: dict) -> str:
    return "PASS" if all(c["passed"] for c in checks.values()) else "REVIEW REQUIRED"


def synthesis_auto_promote_eligible(final_status: str, review_verdict: str) -> bool:
    """Human Review Gate: run_expert_reviewがreview_verdict=="needs_human_review"と
    判定した場合、final_status(決定的Anti-Backflow Guardの結果)がPASSであっても
    Monitoring/Intelligence Needsへの自動昇格は許可しない。生成・保存(persist_synthesis)
    自体は止めない——止めるのは自動昇格のみ。final_validity_statusはこの判定を理由に
    書き換えない(Guard結果とReview判定は別軸のため混同しない)。人間が内容を確認した後は、
    既存の手動再昇格エンドポイント(repromote_synthesis)からPromoteできる。"""
    return final_status == "PASS" and review_verdict != "needs_human_review"


# ---------------------------------------------------------------------------
# 最終HTML（Prompt2の構成原則: Finding→Why it matters→Evidence→Counterevidence→
# Implication→Watchpoint。QA/パイプライン内部事情を本文へ出さない）
# ---------------------------------------------------------------------------

def render_final_html(draft: dict, narrative_row_data: dict) -> str:
    def esc(text) -> str:
        return (str(text or "")
                .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    title = esc(narrative_row_data.get("report_topic_label") or narrative_row_data.get("narrative_name"))
    central = draft.get("central_assessment") or {}
    parts = [f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title></head><body>"]
    parts.append(f"<h1>{title}</h1>")
    parts.append(f"<h2>Central Assessment</h2><p>{esc(central.get('summary'))}</p>")

    delta_items = draft.get("assessment_delta") or []
    if delta_items:
        parts.append("<h2>What Changed Since Last Assessment</h2><ul>")
        for item in delta_items:
            parts.append(f"<li><strong>[{esc(item.get('change_type'))}]</strong> {esc(item.get('item'))}</li>")
        parts.append("</ul>")

    section_specs = [
        ("findings", "Findings", "statement", "why_it_matters"),
        ("drivers", "Drivers", "driver", "mechanism"),
        ("counterforces", "Counterforces", "counterforce", "mechanism"),
        ("implications", "Implications", "implication", "for_whom"),
        ("watchpoints", "Watchpoints", "watchpoint", "trigger_condition"),
    ]
    for key, heading, main_field, sub_field in section_specs:
        items = draft.get(key) or []
        if not items:
            continue
        parts.append(f"<h2>{heading}</h2><ul>")
        for item in items:
            parts.append(f"<li><strong>{esc(item.get(main_field))}</strong><br>{esc(item.get(sub_field))}</li>")
        parts.append("</ul>")

    continuity = draft.get("continuity_assessment") or {}
    if continuity:
        parts.append(f"<h2>Continuity</h2><p>{esc(continuity.get('state'))}: {esc(continuity.get('rationale'))}</p>")

    watchpoint_updates = draft.get("watchpoint_updates") or []
    if watchpoint_updates:
        parts.append("<h2>Watchpoint Status</h2><ul>")
        for item in watchpoint_updates:
            parts.append(f"<li><strong>[{esc(item.get('status'))}]</strong> {esc(item.get('watchpoint'))}</li>")
        parts.append("</ul>")

    parts.append("</body></html>")
    return "\n".join(parts)
