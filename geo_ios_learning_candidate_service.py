"""Geopolitical Intelligence OS Phase B: Learning Candidate Pipeline（§20-25）。

§24（最重要制約）: Expert commentから自動でActive Knowledgeへ昇格させない。本モジュールが提供する
のは、
    Learning Extraction → Candidate → Human Review → Approve/Reject
までであり、「Approve」はLearning Candidate自身のstatusを進めるだけで、既存geo_knowledge_items
のActive化ロジック（geo_knowledge_service.create_knowledge_item/review_knowledge_item、状態遷移
の検証込み）を勝手に迂回・複製しない。Approved Learning CandidateをActive Knowledgeへ実際に反映
するにはpromote_to_knowledge_item()を別途明示的に呼ぶ必要があり、その結果生成されるKnowledge Item
自体も既存仕様どおりstatus="candidate"から始まり、既存のreview_knowledge_item（人間の権限を持つ
Reviewerのみ）を経て初めてactiveになる——二重のHuman Gateを機械的に強制する。

既存geo_expert_review_changes（Internal Expert Learning Layer）のdisposition状態機械（pending→
promoted等）は変更しない。Expert Review由来のLearning Candidateはsource_expert_review_change_id
で既存changeへ参照を持てるが、それは追跡用のリンクであり、既存changeの遷移ロジックには影響しない。
"""
from datetime import datetime, timezone

import geo_audit
import geo_knowledge_service
import geo_role_guard
import geo_ios_learning_knowledge_mapper as knowledge_mapper

# Phase G §36: Candidate Review権限。Expert ReviewerだからといってKnowledge Steward権限を
# 自動付与しない——Learning Candidateの承認/却下は「将来の組織Knowledgeにするかどうか」の
# 判断であり、既存Knowledge Promotion（PUBLISH_ROLES、geo_ios_knowledge_publication_service）
# と同じ権限バーで統一する。
CANDIDATE_REVIEW_ROLES = ("knowledge_steward",)

CANDIDATE_TYPES = (
    "cqas_question_pattern", "scenario_pattern", "reasoning_rule", "attention_rule",
    "failure_pattern", "evidence_rule", "source_preference", "actor_intelligence",
    "media_intelligence", "report_design_pattern", "intelligence_need_pattern",
    "monitoring_indicator",  # Phase G §7で追加
)
CANDIDATE_STATUSES = ("candidate", "approved", "rejected", "superseded")


class LearningCandidateError(Exception):
    """Learning Candidateの操作エラー。"""


def create_candidate(client, *, candidate_type: str, observation: str, generalized_rule: str,
                      extraction_model: str, trigger_context: dict = None,
                      failure_pattern_example: str = None, positive_example: str = None,
                      scope: str = None, confidence: str = None, source_report_id: str = None,
                      source_review_cycle_id: str = None, source_review_comment_id: str = None,
                      source_expert_review_change_id: str = None, actor_id: str = "geo_learning_extraction") -> dict:
    if candidate_type not in CANDIDATE_TYPES:
        raise LearningCandidateError(f"未対応のcandidate_typeです: {candidate_type}（許可: {CANDIDATE_TYPES}）")
    row = {
        "candidate_type": candidate_type, "trigger_context": trigger_context,
        "observation": observation, "generalized_rule": generalized_rule,
        "failure_pattern_example": failure_pattern_example, "positive_example": positive_example,
        "scope": scope, "confidence": confidence, "source_report_id": source_report_id,
        "source_review_cycle_id": source_review_cycle_id,
        "source_review_comment_id": source_review_comment_id,
        "source_expert_review_change_id": source_expert_review_change_id,
        "extraction_model": extraction_model, "status": "candidate",
        # workbench_status（2026-09-24追加）はDB列側もdefault 'pending'を持つが、ここでも
        # 明示する（FakeSupabaseClientはDB既定値を再現しないため、テスト・実DB双方で
        # 一貫させる。既存created_at等と同じ理由）。
        "workbench_status": "pending",
    }
    created = client.insert("geo_ios_learning_candidates", [row])[0]
    geo_audit.log_audit(client, actor=actor_id, action="learning_candidate_create",
                         target_table="geo_ios_learning_candidates", target_id=created["candidate_id"],
                         detail={"candidate_type": candidate_type})
    return created


def get_candidate(client, candidate_id: str) -> dict | None:
    rows = client.select("geo_ios_learning_candidates", {
        "select": "*", "candidate_id": f"eq.{candidate_id}", "limit": "1"})
    return rows[0] if rows else None


def list_candidates(client, *, status: str = None, candidate_type: str = None, limit: int = 100) -> list:
    """Expert Knowledge Review Inbox向け一覧（§25）。既定は全status（Inboxは主にcandidateを見るが、
    Approve/Reject履歴の監査閲覧にも使えるようstatusは任意フィルタとする）。"""
    params = {"select": "*", "order": "created_at.desc", "limit": str(limit)}
    if status:
        params["status"] = f"eq.{status}"
    if candidate_type:
        params["candidate_type"] = f"eq.{candidate_type}"
    return client.select("geo_ios_learning_candidates", params)


def _require_candidate(client, candidate_id: str) -> dict:
    candidate = get_candidate(client, candidate_id)
    if not candidate:
        raise LearningCandidateError(f"candidate_idが見つかりません: {candidate_id}")
    return candidate


def approve_candidate(client, candidate_id: str, *, reviewed_by: str, reviewer_role: str,
                       edited_generalized_rule: str = None, actor_id: str = None) -> dict:
    """Approve / Edit & Approve（§25）。edited_generalized_ruleを渡すと、Human Reviewでの
    修正結果として generalized_rule を上書きする（Edit & Approve）。この時点ではまだ
    Active Knowledgeにはならない——promote_to_knowledge_item()の明示呼び出しが必要。"""
    geo_role_guard.require_role(reviewer_role, CANDIDATE_REVIEW_ROLES)
    candidate = _require_candidate(client, candidate_id)
    if candidate["status"] != "candidate":
        raise LearningCandidateError(
            f"status={candidate['status']}からはapprove_candidateできません（candidateのみ許可）")

    patch = {"status": "approved", "reviewed_by": reviewed_by,
              "reviewed_at": datetime.now(timezone.utc).isoformat()}
    if edited_generalized_rule:
        patch["generalized_rule"] = edited_generalized_rule
    client.update("geo_ios_learning_candidates", {"candidate_id": f"eq.{candidate_id}"}, patch)
    geo_audit.log_audit(client, actor=actor_id or reviewed_by, action="learning_candidate_approve",
                         target_table="geo_ios_learning_candidates", target_id=candidate_id,
                         detail={"edited": bool(edited_generalized_rule)})
    return get_candidate(client, candidate_id)


def reject_candidate(client, candidate_id: str, *, reviewed_by: str, reviewer_role: str, reason: str = None,
                      actor_id: str = None) -> dict:
    geo_role_guard.require_role(reviewer_role, CANDIDATE_REVIEW_ROLES)
    candidate = _require_candidate(client, candidate_id)
    if candidate["status"] != "candidate":
        raise LearningCandidateError(
            f"status={candidate['status']}からはreject_candidateできません（candidateのみ許可）")
    client.update("geo_ios_learning_candidates", {"candidate_id": f"eq.{candidate_id}"}, {
        "status": "rejected", "reviewed_by": reviewed_by,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    })
    geo_audit.log_audit(client, actor=actor_id or reviewed_by, action="learning_candidate_reject",
                         target_table="geo_ios_learning_candidates", target_id=candidate_id,
                         detail={"reason": reason})
    return get_candidate(client, candidate_id)


def promote_to_knowledge_item(client, candidate_id: str, *, knowledge_type: str,
                               structured_content: dict, information_class: str, created_by: str,
                               summary: str = None, confidence: str = None,
                               supersedes_id: str = None, human_visibility: str = None,
                               agent_sharing_policy: str = None) -> dict:
    """approved状態のLearning Candidateを、既存Knowledge DBの状態機械へ橋渡しする。生成される
    Knowledge Itemはgeo_knowledge_service.create_knowledge_itemの既定どおりstatus="candidate"
    から始まり、以後は既存review_knowledge_item（人間の権限検証込み）を経なければactiveにならない
    ——本関数はActive化を一切行わない。knowledge_type/structured_contentは既存
    geo_knowledge_item_schemasの型別スキーマに従う必要があるため、呼び出し側（人間の判断を経た
    API操作）が明示的に指定する。

    supersedes_id/human_visibility/agent_sharing_policy（2026-09-24追加、Learning Workbench
    のdedup_verdict=UPDATE・公開設定対応）: そのままgeo_knowledge_service.create_knowledge_item
    へ渡すだけ（本関数・呼び出し先とも既存の承認境界・状態機械は変更しない）。"""
    candidate = _require_candidate(client, candidate_id)
    if candidate["status"] != "approved":
        raise LearningCandidateError(
            f"status={candidate['status']}からはpromote_to_knowledge_itemできません（approvedのみ許可）")
    if candidate.get("approved_knowledge_item_id"):
        raise LearningCandidateError("既にKnowledge Itemへ昇格済みのCandidateです")

    knowledge_item = geo_knowledge_service.create_knowledge_item(
        client, knowledge_type=knowledge_type, title=candidate["generalized_rule"][:200],
        structured_content=structured_content, information_class=information_class,
        created_by=created_by, summary=summary or candidate["observation"],
        confidence=confidence or candidate.get("confidence") or "判断不能",
        origin="geo_ios_learning_candidate", supersedes_id=supersedes_id,
        human_visibility=human_visibility, agent_sharing_policy=agent_sharing_policy,
    )
    client.update("geo_ios_learning_candidates", {"candidate_id": f"eq.{candidate_id}"},
                  {"approved_knowledge_item_id": knowledge_item["knowledge_item_id"]})
    geo_audit.log_audit(client, actor=created_by, action="learning_candidate_promote",
                         target_table="geo_ios_learning_candidates", target_id=candidate_id,
                         detail={"knowledge_item_id": knowledge_item["knowledge_item_id"]})
    return knowledge_item


def promote_via_mapper(client, candidate_id: str, *, created_by: str, created_by_role: str,
                        target_entity_id: str = None, knowledge_type_override: str = None,
                        supersedes_id: str = None, human_visibility: str = None,
                        agent_sharing_policy: str = None) -> dict:
    """Phase G §22: knowledge_type/structured_contentを呼び出し側（API/UI）に自由入力させず、
    geo_ios_learning_knowledge_mapperが一元的に導出したものだけを使ってpromoteする。
    target_entity_idはactor_intelligence候補のみ必須（Mapper自身が判断できない、人間が
    明示すべき唯一の情報）。マッピング不能なcandidate_type（media_intelligence/
    monitoring_indicator）はMappingErrorを送出し、Knowledgeは作らない。

    knowledge_type_override（2026-09-24追加、Learning Workbenchのtarget_knowledge_type
    対応）: 指定時はMapperが導出したknowledge_typeより優先する（人間がWorkbenchで
    single-select上書きした場合）。structured_contentはMapperの出力をそのまま使う——
    knowledge_typeを変えてもcontentの組み立てロジック自体は変更しない、人間が選んだ型の
    Schemaにcontentが適合しない場合はgeo_knowledge_item_schemas側のバリデーションで
    素直にエラーになる（Workbench UIは既存Mapperが対応するcandidate_type分のみ
    候補として見せる想定）。

    §36: Knowledge Promotion権限（CANDIDATE_REVIEW_ROLES=knowledge_steward）を要求する
    （Expert ReviewerだからといってKnowledge Steward権限を自動付与しない）。"""
    geo_role_guard.require_role(created_by_role, CANDIDATE_REVIEW_ROLES)
    candidate = _require_candidate(client, candidate_id)
    knowledge_type, structured_content = knowledge_mapper.map_candidate_to_knowledge(
        candidate, target_entity_id=target_entity_id)
    return promote_to_knowledge_item(
        client, candidate_id, knowledge_type=knowledge_type_override or knowledge_type,
        structured_content=structured_content,
        information_class="internal_hypothesis", created_by=created_by,
        supersedes_id=supersedes_id, human_visibility=human_visibility,
        agent_sharing_policy=agent_sharing_policy)


# ---- Learning Workbench: 版履歴（2026-09-24追加） -----------------------------
#
# geo_ios_learning_extraction_service.py（Initial Extraction）と
# geo_ios_learning_workbench_service.py（それ以降の全編集）の両方から使う共有プリミティブ
# のため、両方が既にimportしているこのモジュールへ置く（循環importを避ける）。

def _allocate_next_revision(client, candidate_id: str) -> int:
    """candidateの現在の最大revision_numberの次番号を返す。unique(candidate_id,
    revision_number)制約があるため、呼び出し側（record_revision）はinsert時の
    unique_violationを1回だけretryする防御も持つ（同時書き込みの競合安全性、
    3回目Planレビュー③）。"""
    rows = client.select("geo_ios_learning_candidate_revisions", {
        "select": "revision_number", "candidate_id": f"eq.{candidate_id}",
        "order": "revision_number.desc", "limit": "1"})
    return (rows[0]["revision_number"] + 1) if rows else 1


def record_revision(client, candidate_id: str, *, revised_by: str, revision_snapshot: dict,
                     change_summary: str = None, created_by: str,
                     workbench_turn_id: str = None) -> dict:
    """geo_ios_learning_candidate_revisionsへ1行追記する。revision_number=1は常に
    Initial Extraction（geo_ios_learning_extraction_service.extract_learning_candidates
    自身がcandidate insert直後に呼ぶ）。以後はgeo_ios_learning_workbench_service経由。
    unique制約違反（同時実行での採番衝突）が起きた場合は1回だけ採番をやり直す。"""
    if revised_by not in ("ai", "human"):
        raise LearningCandidateError(f"未対応のrevised_byです: {revised_by}")
    for attempt in range(2):
        revision_number = _allocate_next_revision(client, candidate_id)
        row = {
            "candidate_id": candidate_id, "revision_number": revision_number,
            "revised_by": revised_by, "workbench_turn_id": workbench_turn_id,
            "revision_snapshot": revision_snapshot, "change_summary": change_summary,
            "created_by": created_by,
        }
        try:
            return client.insert("geo_ios_learning_candidate_revisions", [row])[0]
        except Exception as exc:  # noqa: BLE001 - PostgRESTのunique_violationを広く受ける
            if attempt == 0 and "unique" in str(exc).lower():
                continue
            raise
    raise LearningCandidateError(f"revision採番に失敗しました（candidate_id={candidate_id}）")
