"""Geopolitical Intelligence OS: Learning Workbench（Learning Management）。

専門家レビュー前後のBefore/After差分から抽出されたLearning Candidate
（geo_ios_learning_extraction_service.py）を、人間が実際に「開いて・比較して・
AIと相談しながら編集し・承認・公開する」ためのワークフロー層（ユーザー指示、
2026-09-24、3回のPlan Modeレビューで確定）。

絶対条件（Plan本文より）:
- 既存Expert AI本体（geo_expert_engine.py/geo_expert_schema.py）・CQAS・
  Reading Portfolioは一切呼ばない・変更しない。
- 既存Learning Candidate抽出（geo_ios_learning_extraction_service.py）・
  Dedup判定・Human Gate #1（geo_ios_learning_candidate_service.approve_candidate/
  reject_candidate）・Human Gate #2（geo_knowledge_service.review_knowledge_item）・
  Knowledge Publication/Retrievalの経路は一切書き換えず、既存関数をそのまま呼ぶ。
- workbench_status（本モジュール独自の編集ワークフロー状態）は、既存candidate.status
  （Gate #1が参照する意味を持つ）とは別軸。状態遷移は本モジュールの専用関数のみが行い、
  汎用的な「PATCH workbench_status」は提供しない。
- Publishは既存Human Gate #2を必ず通す。DUPLICATE/CONFLICTのcandidateは常にPublish
  不可。UPDATE verdictは、New Knowledgeがactive/available化した後にのみ旧Knowledgeを
  非活性化する（partial failureから再開可能な設計）。
"""
from datetime import datetime, timezone

import geo_audit
import geo_ios_learning_candidate_service as learning_service
import geo_ios_learning_knowledge_mapper as knowledge_mapper
import geo_knowledge_service
import geo_llm_common
import geo_role_guard

WORKBENCH_STATUSES = ("pending", "editing", "ready_for_approval", "approved", "published", "rejected")
DEDUP_VERDICTS = ("NEW", "DUPLICATE", "UPDATE", "CONFLICT", "RELATED")
PUBLISH_ROLES = geo_knowledge_service.REVIEWER_ROLES  # ("expert_reviewer", "knowledge_steward")
CANDIDATE_REVIEW_ROLES = learning_service.CANDIDATE_REVIEW_ROLES  # ("knowledge_steward",)

_EDITABLE_SNAPSHOT_FIELDS = (
    "generalized_rule", "observation", "scope", "confidence", "generalization_risk",
    "generalization_risk_reason", "candidate_type", "target_knowledge_type", "dedup_verdict",
    "dedup_target_knowledge_item_id", "dedup_related_knowledge_item_ids",
    "dedup_conflict_resolution_note", "human_visibility", "agent_sharing_policy",
)


class WorkbenchError(Exception):
    """Learning Workbenchの操作エラー（前提条件違反・不正な状態遷移）。"""


def _current_snapshot(candidate: dict) -> dict:
    return {k: candidate.get(k) for k in _EDITABLE_SNAPSHOT_FIELDS}


def _require_candidate(client, candidate_id: str) -> dict:
    candidate = learning_service.get_candidate(client, candidate_id)
    if not candidate:
        raise WorkbenchError(f"candidate_idが見つかりません: {candidate_id}")
    return candidate


def _set_workbench_status(client, candidate_id: str, status: str) -> None:
    if status not in WORKBENCH_STATUSES:
        raise WorkbenchError(f"未対応のworkbench_statusです: {status}")
    client.update("geo_ios_learning_candidates", {"candidate_id": f"eq.{candidate_id}"},
                  {"workbench_status": status})


# ---- 閲覧系 ------------------------------------------------------------------

def list_revisions(client, candidate_id: str) -> list:
    return client.select("geo_ios_learning_candidate_revisions", {
        "select": "*", "candidate_id": f"eq.{candidate_id}", "order": "revision_number.asc"})


def list_turns(client, candidate_id: str) -> list:
    return client.select("geo_ios_learning_workbench_turns", {
        "select": "*", "candidate_id": f"eq.{candidate_id}", "order": "created_at.asc"})


def get_workbench_item(client, candidate_id: str) -> dict:
    """詳細画面向け。revision履歴が1件も無い（Workbench導入前から存在する）candidateは、
    現在の列値からrevision_number=1を遅延生成する（Migration Backfillとは別処理、
    参照時の遅延補完。GETの副作用としてgeneralization_risk assessmentのようなLLM呼び出しは
    行わない——3回目Planレビュー②）。"""
    candidate = _require_candidate(client, candidate_id)
    revisions = list_revisions(client, candidate_id)
    if not revisions:
        seeded = learning_service.record_revision(
            client, candidate_id, revised_by="ai", revision_snapshot=_current_snapshot(candidate),
            change_summary="Backfilled from pre-Workbench state",
            created_by="geo_learning_workbench_backfill")
        revisions = [seeded]
    turns = list_turns(client, candidate_id)
    return {"candidate": candidate, "revisions": revisions, "turns": turns}


def list_workbench_candidates(client, *, workbench_status: str = None, candidate_type: str = None,
                               limit: int = 100) -> list:
    params = {"select": "*", "order": "created_at.desc", "limit": str(limit)}
    if workbench_status:
        params["workbench_status"] = f"eq.{workbench_status}"
    if candidate_type:
        params["candidate_type"] = f"eq.{candidate_type}"
    return client.select("geo_ios_learning_candidates", params)


def get_sibling_candidates(client, review_cycle_id: str, current_candidate_id: str) -> dict:
    """詳細画面の文脈ヘッダー用: 同じsource_review_cycle_idを持つ他candidateの件数と、
    現在位置の前後（created_at順）を返す。新規ストレージは使わない。"""
    if not review_cycle_id:
        return {"total": 0, "siblings": [], "previous_candidate_id": None, "next_candidate_id": None}
    siblings = client.select("geo_ios_learning_candidates", {
        "select": "candidate_id,candidate_type,generalized_rule,created_at",
        "source_review_cycle_id": f"eq.{review_cycle_id}", "order": "created_at.asc"})
    ids = [s["candidate_id"] for s in siblings]
    prev_id, next_id = None, None
    if current_candidate_id in ids:
        idx = ids.index(current_candidate_id)
        prev_id = ids[idx - 1] if idx > 0 else None
        next_id = ids[idx + 1] if idx < len(ids) - 1 else None
    return {"total": len(siblings), "siblings": siblings,
            "previous_candidate_id": prev_id, "next_candidate_id": next_id}


def get_candidate_lineage(client, candidate_id: str) -> dict:
    """Backward Trace: candidateからReview/Approval/Knowledge Itemまでの既存FKを
    そのまま辿って返す読み取り専用API。新規ストレージは使わない。"""
    candidate = _require_candidate(client, candidate_id)
    lineage = {"candidate": candidate}

    review_cycle_id = candidate.get("source_review_cycle_id")
    if review_cycle_id:
        rows = client.select("geo_ios_review_cycles", {
            "select": "*", "review_cycle_id": f"eq.{review_cycle_id}", "limit": "1"})
        lineage["review_cycle"] = rows[0] if rows else None

    review_comment_id = candidate.get("source_review_comment_id")
    if review_comment_id:
        rows = client.select("geo_ios_review_comments", {
            "select": "*", "review_comment_id": f"eq.{review_comment_id}", "limit": "1"})
        lineage["review_comment"] = rows[0] if rows else None

    knowledge_item_id = candidate.get("approved_knowledge_item_id")
    if knowledge_item_id:
        lineage["knowledge_item"] = geo_knowledge_service.get_knowledge_item(client, knowledge_item_id)

    trigger_context = candidate.get("trigger_context") or {}
    report_project_id = trigger_context.get("source_report_project_id")
    if report_project_id:
        rows = client.select("geo_report_projects", {
            "select": "project_id,project_code,project_title",
            "project_id": f"eq.{report_project_id}", "limit": "1"})
        lineage["report_project"] = rows[0] if rows else None

    lineage["after_resolution_method"] = trigger_context.get("after_resolution_method")
    lineage["revisions"] = list_revisions(client, candidate_id)
    lineage["turns"] = list_turns(client, candidate_id)
    return lineage


# ---- Generalization Risk Assessment（GETの副作用にしない、専用POST経由でのみ起動） ----

_RISK_SYSTEM_PROMPT = (
    "あなたは地政学分析組織のLearning Governance Analystです。1件のLearning Candidate"
    "（再利用可能なルール・知見として提案された内容）について、これを他のCase/Actorへ"
    "一般化した場合のリスクを判定してください。判定基準:\n"
    "- low: 広く様々な状況に安全に適用できる（Evidenceが一般的な構造・力学を示している）\n"
    "- medium: 一定の前提条件下でのみ有効、適用範囲を明示すれば安全に使える\n"
    "- high: 特定のActor・特定の局面に強く依存しており、他へ一般化するとミスリードの"
    "リスクが高い\n"
    "reasonには、なぜその判定になったかを、Evidenceの範囲を超えて断定しない形で"
    "簡潔に説明してください。"
)

_RISK_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "GeneralizationRiskAssessment",
    "type": "object",
    "additionalProperties": False,
    "required": ["generalization_risk", "generalization_risk_reason"],
    "properties": {
        "generalization_risk": {"type": "string", "enum": ["low", "medium", "high"]},
        "generalization_risk_reason": {"type": "string"},
    },
}


def assess_generalization_risk(client, azure_client, model, candidate_id: str, *,
                                actor_id: str = "geo_learning_workbench") -> dict:
    """candidateのgeneralization_risk/_reasonをAIが初期提案する（revision_number=2、
    revision_number=1はextract_learning_candidates自身が既に書き込み済み）。Frontendの
    専用POSTからBackgroundTasksで呼ばれる想定——GETの副作用としては呼ばない
    （3回目Planレビュー②）。既存extract_learning_candidatesのPrompt/Schemaは変更しない
    （別関数として追加するだけ）。"""
    candidate = _require_candidate(client, candidate_id)
    user_prompt = (
        "以下のLearning Candidateについて、generalization_riskを判定してください。"
        "JSON以外は出力しないでください。\n\n"
        f"candidate_type: {candidate.get('candidate_type')}\n"
        f"observation: {candidate.get('observation')}\n"
        f"generalized_rule: {candidate.get('generalized_rule')}\n"
        f"scope: {candidate.get('scope')}\n"
        f"trigger_context: {candidate.get('trigger_context')}\n"
    )
    result = geo_llm_common.call_llm_structured(
        azure_client, model, _RISK_SYSTEM_PROMPT, user_prompt, _RISK_SCHEMA,
        "geo_ios_learning_workbench_risk_assessment")
    data = result["data"]
    client.update("geo_ios_learning_candidates", {"candidate_id": f"eq.{candidate_id}"}, {
        "generalization_risk": data["generalization_risk"],
        "generalization_risk_reason": data["generalization_risk_reason"],
    })
    candidate = learning_service.get_candidate(client, candidate_id)
    learning_service.record_revision(
        client, candidate_id, revised_by="ai", revision_snapshot=_current_snapshot(candidate),
        change_summary="Generalization risk assessment", created_by=actor_id)
    geo_audit.log_audit(
        client, actor=actor_id, action="learning_workbench_risk_assessed",
        target_table="geo_ios_learning_candidates", target_id=candidate_id,
        detail={"generalization_risk": data["generalization_risk"]})
    return candidate


# ---- 編集Chat（軽量LLM呼び出し、Expert AI本体は経由しない） -------------------------

WORKBENCH_REVISION_SYSTEM_PROMPT = (
    "あなたは地政学分析組織のLearning Editing Assistantです。人間の分析担当者が、"
    "抽出されたLearning Candidate（再利用可能なルール・知見の提案）を一緒に磨き上げる"
    "作業を手伝います。以下のルールを厳守してください:\n"
    "1. Reviewに無い知識・Evidenceに無い事実を勝手にLearningへ追加しないでください。\n"
    "2. Source Evidence（このCandidateのobservation/trigger_context）から離れて"
    "一般化しないでください。\n"
    "3. Case固有のFact（この案件・この時点だけの事実）と、再利用可能なRuleを明確に"
    "区別してください。\n"
    "4. 特定人物の性格そのものを断定的に評価する表現は避け、観測された行動・条件に"
    "限定してください。\n"
    "5. 「常に」「必ず」「絶対に」のような、Evidenceを超えた一般化を示す言い回しは"
    "避けてください。\n"
    "6. 不確実な場合はscope（適用範囲）を広げるのではなく狭める方向で判断してください。\n"
    "7. 人間の指示でscopeやgeneralized_ruleを変更した場合、change_summaryにその変更内容"
    "を明記してください（変更履歴として残るため）。\n"
    "人間の指示に対する短い返答（ai_response_text）と、更新後のCandidate内容の両方を"
    "返してください。"
)

_WORKBENCH_REVISION_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "WorkbenchRevisionResult",
    "type": "object",
    "additionalProperties": False,
    "required": ["ai_response_text", "generalized_rule", "observation", "scope", "confidence",
                 "change_summary"],
    "properties": {
        "ai_response_text": {"type": "string", "description": "人間への短い返答"},
        "generalized_rule": {"type": "string"},
        "observation": {"type": "string"},
        "scope": {"type": "string", "enum": [
            "report_specific", "theme_specific", "actor_specific", "domain_general", "general"]},
        "confidence": {"type": "string", "enum": ["高", "中", "低", "判断不能"]},
        "change_summary": {"type": "string", "description": "何をどう変更したか（変更が無ければ'変更なし'）"},
    },
}


def submit_human_instruction(client, candidate_id: str, *, human_instruction: str,
                              actor_id: str) -> dict:
    """人間の編集指示をpendingで記録する（同期部分）。実際のAI改訂実行は
    run_chat_revision（API層のBackgroundTasksから呼ばれる）が担う。"""
    candidate = _require_candidate(client, candidate_id)
    if not (human_instruction or "").strip():
        raise WorkbenchError("human_instructionが空です")
    base_revision_number = _allocate_base_revision_number(client, candidate_id)
    row = {
        "candidate_id": candidate_id, "human_instruction": human_instruction,
        "base_revision_number": base_revision_number, "status": "pending", "created_by": actor_id,
    }
    turn = client.insert("geo_ios_learning_workbench_turns", [row])[0]
    if candidate["workbench_status"] == "pending":
        _set_workbench_status(client, candidate_id, "editing")
    geo_audit.log_audit(
        client, actor=actor_id, action="learning_workbench_turn_submit",
        target_table="geo_ios_learning_workbench_turns", target_id=turn["workbench_turn_id"],
        detail={"candidate_id": candidate_id})
    return turn


def _allocate_base_revision_number(client, candidate_id: str) -> int:
    revisions = list_revisions(client, candidate_id)
    return revisions[-1]["revision_number"] if revisions else 0


def run_chat_revision(client, azure_client, model, candidate_id: str, *, workbench_turn_id: str,
                       human_instruction: str, actor_id: str) -> None:
    """BackgroundTasksから呼ばれる。例外は内部で捕捉しturnをfailedにする
    （FastAPIのBackgroundTaskは例外を呼び出し元へ伝播しないため、放置するとturnが
    pendingのまま残ってしまう——既存geo_ios_consultation_apiと同じ方針）。"""
    try:
        candidate = _require_candidate(client, candidate_id)
        user_prompt = (
            "以下のLearning Candidateの現在の内容と、人間からの編集指示です。"
            "JSON以外は出力しないでください。\n\n"
            f"現在のcandidate_type: {candidate.get('candidate_type')}\n"
            f"現在のobservation: {candidate.get('observation')}\n"
            f"現在のgeneralized_rule: {candidate.get('generalized_rule')}\n"
            f"現在のscope: {candidate.get('scope')}\n"
            f"現在のconfidence: {candidate.get('confidence')}\n"
            f"trigger_context（Source Evidence）: {candidate.get('trigger_context')}\n\n"
            f"人間からの編集指示: {human_instruction}\n"
        )
        result = geo_llm_common.call_llm_structured(
            azure_client, model, WORKBENCH_REVISION_SYSTEM_PROMPT, user_prompt,
            _WORKBENCH_REVISION_SCHEMA, "geo_ios_learning_workbench_revision")
        data = result["data"]

        # 競合検知（楽観ロック）: この指示を送った時点のbase_revision_numberと、現在の
        # 最新revision_numberを比較する。不一致ならAI処理中に別の編集が先に入ったため、
        # 新しいrevisionは書かずturnをstaleにするだけで終える（自動上書きしない）。
        turn = client.select("geo_ios_learning_workbench_turns", {
            "select": "*", "workbench_turn_id": f"eq.{workbench_turn_id}", "limit": "1"})[0]
        current_latest = _allocate_base_revision_number(client, candidate_id)
        if current_latest != turn["base_revision_number"]:
            client.update("geo_ios_learning_workbench_turns",
                          {"workbench_turn_id": f"eq.{workbench_turn_id}"},
                          {"status": "stale", "ai_response_text": data["ai_response_text"],
                           "completed_at": datetime.now(timezone.utc).isoformat()})
            return

        client.update("geo_ios_learning_candidates", {"candidate_id": f"eq.{candidate_id}"}, {
            "generalized_rule": data["generalized_rule"], "observation": data["observation"],
            "scope": data["scope"], "confidence": data["confidence"],
        })
        candidate = learning_service.get_candidate(client, candidate_id)
        revision = learning_service.record_revision(
            client, candidate_id, revised_by="ai", revision_snapshot=_current_snapshot(candidate),
            change_summary=data["change_summary"], created_by=actor_id,
            workbench_turn_id=workbench_turn_id)
        client.update("geo_ios_learning_workbench_turns",
                      {"workbench_turn_id": f"eq.{workbench_turn_id}"},
                      {"status": "completed", "ai_response_text": data["ai_response_text"],
                       "resulting_revision_id": revision["revision_id"],
                       "completed_at": datetime.now(timezone.utc).isoformat()})
    except Exception as e:  # noqa: BLE001 - BackgroundTaskの例外は握りつぶさず必ずturnへ記録する
        client.update("geo_ios_learning_workbench_turns",
                      {"workbench_turn_id": f"eq.{workbench_turn_id}"},
                      {"status": "failed", "error_message": f"{type(e).__name__}: {e}"[:2000],
                       "completed_at": datetime.now(timezone.utc).isoformat()})


def record_human_edit(client, candidate_id: str, *, edited_fields: dict, actor_id: str,
                       change_summary: str = None) -> dict:
    """チャットを介さない直接編集。非同期ではないためbase_revision_numberは不要
    （即時に新revisionを追記する）。edited_fieldsは_EDITABLE_SNAPSHOT_FIELDSの
    部分集合のみ受け付ける。"""
    candidate = _require_candidate(client, candidate_id)
    patch = {k: v for k, v in edited_fields.items() if k in _EDITABLE_SNAPSHOT_FIELDS}
    if not patch:
        raise WorkbenchError("edited_fieldsに有効な項目がありません")
    if "dedup_verdict" in patch and patch["dedup_verdict"] not in DEDUP_VERDICTS:
        raise WorkbenchError(f"未対応のdedup_verdictです: {patch['dedup_verdict']}")
    client.update("geo_ios_learning_candidates", {"candidate_id": f"eq.{candidate_id}"}, patch)
    candidate = learning_service.get_candidate(client, candidate_id)
    learning_service.record_revision(
        client, candidate_id, revised_by="human", revision_snapshot=_current_snapshot(candidate),
        change_summary=change_summary or "Human edit", created_by=actor_id)
    if candidate["workbench_status"] == "pending":
        _set_workbench_status(client, candidate_id, "editing")
    geo_audit.log_audit(
        client, actor=actor_id, action="learning_workbench_human_edit",
        target_table="geo_ios_learning_candidates", target_id=candidate_id,
        detail={"edited_keys": sorted(patch.keys())})
    return candidate


def record_dedup_verdict(client, candidate_id: str, *, verdict: str, target_knowledge_item_id: str = None,
                          related_knowledge_item_ids: list = None, resolution_note: str = None,
                          actor_id: str) -> dict:
    """Dedup最終判断を人間が確定する（B章2、3回目Planレビュー⑥）。CONFLICTから他verdictへ
    変更する場合はresolution_noteが既に非空であることを要求する。UPDATEはtarget_knowledge_
    item_idを必須とする。"""
    candidate = _require_candidate(client, candidate_id)
    if verdict not in DEDUP_VERDICTS:
        raise WorkbenchError(f"未対応のdedup_verdictです: {verdict}")
    if candidate.get("dedup_verdict") == "CONFLICT" and verdict != "CONFLICT":
        if not (candidate.get("dedup_conflict_resolution_note") or resolution_note or "").strip():
            raise WorkbenchError(
                "CONFLICTから変更するにはdedup_conflict_resolution_noteが必要です")
    if verdict == "UPDATE" and not target_knowledge_item_id:
        raise WorkbenchError("dedup_verdict=UPDATEにはtarget_knowledge_item_idが必須です")

    patch = {"dedup_verdict": verdict}
    if target_knowledge_item_id is not None:
        patch["dedup_target_knowledge_item_id"] = target_knowledge_item_id
    if related_knowledge_item_ids is not None:
        patch["dedup_related_knowledge_item_ids"] = related_knowledge_item_ids
    if resolution_note is not None:
        patch["dedup_conflict_resolution_note"] = resolution_note
    client.update("geo_ios_learning_candidates", {"candidate_id": f"eq.{candidate_id}"}, patch)
    candidate = learning_service.get_candidate(client, candidate_id)
    learning_service.record_revision(
        client, candidate_id, revised_by="human", revision_snapshot=_current_snapshot(candidate),
        change_summary=f"Dedup verdict set to {verdict}", created_by=actor_id)
    return candidate


# ---- 状態遷移（Service層で強制、UIからの任意state書き換えは不可） --------------------

def mark_ready_for_approval(client, candidate_id: str, *, actor_id: str) -> dict:
    candidate = _require_candidate(client, candidate_id)
    if candidate["workbench_status"] not in ("editing",):
        raise WorkbenchError(
            f"workbench_status={candidate['workbench_status']}からはready_for_approvalへ"
            "遷移できません（editingのみ許可）")
    _set_workbench_status(client, candidate_id, "ready_for_approval")
    return learning_service.get_candidate(client, candidate_id)


def mark_editing(client, candidate_id: str, *, actor_id: str) -> dict:
    candidate = _require_candidate(client, candidate_id)
    if candidate["workbench_status"] not in ("ready_for_approval",):
        raise WorkbenchError(
            f"workbench_status={candidate['workbench_status']}からはeditingへ戻せません"
            "（ready_for_approvalのみ許可）")
    _set_workbench_status(client, candidate_id, "editing")
    return learning_service.get_candidate(client, candidate_id)


def approve_in_workbench(client, candidate_id: str, *, reviewed_by: str, reviewer_role: str,
                          actor_id: str = None) -> dict:
    geo_role_guard.require_role(reviewer_role, CANDIDATE_REVIEW_ROLES)
    candidate = _require_candidate(client, candidate_id)
    if candidate["workbench_status"] != "ready_for_approval":
        raise WorkbenchError(
            f"workbench_status={candidate['workbench_status']}からはapprove_in_workbench"
            "できません（ready_for_approvalのみ許可）")
    learning_service.approve_candidate(
        client, candidate_id, reviewed_by=reviewed_by, reviewer_role=reviewer_role,
        actor_id=actor_id or reviewed_by)
    _set_workbench_status(client, candidate_id, "approved")
    candidate = learning_service.get_candidate(client, candidate_id)
    learning_service.record_revision(
        client, candidate_id, revised_by="human", revision_snapshot=_current_snapshot(candidate),
        change_summary="Approved", created_by=reviewed_by)
    return candidate


def reject_in_workbench(client, candidate_id: str, *, reviewed_by: str, reviewer_role: str,
                         reason: str = None, actor_id: str = None) -> dict:
    geo_role_guard.require_role(reviewer_role, CANDIDATE_REVIEW_ROLES)
    candidate = _require_candidate(client, candidate_id)
    if candidate["workbench_status"] not in ("pending", "editing", "ready_for_approval"):
        raise WorkbenchError(
            f"workbench_status={candidate['workbench_status']}からはreject_in_workbench"
            "できません（published/rejected/approvedからは拒否できません）")
    learning_service.reject_candidate(
        client, candidate_id, reviewed_by=reviewed_by, reviewer_role=reviewer_role, reason=reason,
        actor_id=actor_id or reviewed_by)
    _set_workbench_status(client, candidate_id, "rejected")
    candidate = learning_service.get_candidate(client, candidate_id)
    learning_service.record_revision(
        client, candidate_id, revised_by="human", revision_snapshot=_current_snapshot(candidate),
        change_summary=f"Rejected: {reason}" if reason else "Rejected", created_by=reviewed_by)
    return candidate


# ---- Publish（Human Gate #2を必ず通す、冪等・再開可能） ----------------------------

def publish_from_workbench(client, candidate_id: str, *, publisher: str, publisher_role: str,
                            actor_id: str = None) -> dict:
    """approved状態のcandidateをKnowledge Publicationする。dedup_verdictに応じた検証・
    冪等なStep A（New Knowledge active化）・再開可能なStep B（UPDATE時の旧Knowledge
    非活性化）を行い、両方揃って初めてworkbench_status='published'へ遷移する
    （3回のPlan Modeレビューで確定した仕様、C章）。"""
    geo_role_guard.require_role(publisher_role, PUBLISH_ROLES)
    candidate = _require_candidate(client, candidate_id)
    actor_id = actor_id or publisher

    if candidate["workbench_status"] != "approved":
        raise WorkbenchError(
            f"workbench_status={candidate['workbench_status']}からはpublishできません"
            "（approvedのみ許可）")

    verdict = candidate.get("dedup_verdict")
    if verdict in (None, "DUPLICATE", "CONFLICT"):
        raise WorkbenchError(
            f"dedup_verdict={verdict}のためpublishできません"
            "（NEW/UPDATE/RELATEDのいずれかへ変更してください）")
    if verdict == "UPDATE" and not candidate.get("dedup_target_knowledge_item_id"):
        raise WorkbenchError("dedup_verdict=UPDATEですがdedup_target_knowledge_item_idが未設定です")
    if not candidate.get("human_visibility") or not candidate.get("agent_sharing_policy"):
        raise WorkbenchError(
            "human_visibility/agent_sharing_policyが未設定です（公開設定を入力してください）")

    # --- Step A: New Knowledge Itemをactive/available化する（冪等） -----------------
    knowledge_item_id = candidate.get("approved_knowledge_item_id")
    if not knowledge_item_id:
        # learning_service.promote_via_mapperは既存Gate#1と同じCANDIDATE_REVIEW_ROLES=
        # ("knowledge_steward",)を内部で要求するため使わない——publish_from_workbenchは
        # 既に上でPUBLISH_ROLES=("expert_reviewer","knowledge_steward")を検証済みであり、
        # expert_reviewerがpublishしようとするとpromote_via_mapper内のrole検証で
        # 二重にrejectされてしまう。Mapperのロジックだけを再利用し、role検証を伴わない
        # promote_to_knowledge_itemを直接呼ぶ（authorizationは本関数の冒頭1箇所に集約する）。
        supersedes_id = candidate.get("dedup_target_knowledge_item_id") if verdict == "UPDATE" else None
        knowledge_type, structured_content = knowledge_mapper.map_candidate_to_knowledge(candidate)
        knowledge_item = learning_service.promote_to_knowledge_item(
            client, candidate_id, knowledge_type=candidate.get("target_knowledge_type") or knowledge_type,
            structured_content=structured_content, information_class="internal_hypothesis",
            created_by=publisher, supersedes_id=supersedes_id,
            human_visibility=candidate["human_visibility"],
            agent_sharing_policy=candidate["agent_sharing_policy"])
        knowledge_item_id = knowledge_item["knowledge_item_id"]
    knowledge_item = geo_knowledge_service.get_knowledge_item(client, knowledge_item_id)
    if knowledge_item["status"] == "candidate":
        # review_knowledge_itemは{"knowledge_item": ..., "review": ...}を返す（行そのものではない）。
        knowledge_item = geo_knowledge_service.review_knowledge_item(
            client, knowledge_item_id=knowledge_item_id, reviewer_id=publisher,
            reviewer_role=publisher_role, review_decision="start_review",
            review_comment="Learning Workbench publish", evidence_adequacy="sufficient",
            applicability_checked=True)["knowledge_item"]
    if knowledge_item["status"] == "in_review":
        knowledge_item = geo_knowledge_service.review_knowledge_item(
            client, knowledge_item_id=knowledge_item_id, reviewer_id=publisher,
            reviewer_role=publisher_role, review_decision="approve_active",
            review_comment="Learning Workbench publish", evidence_adequacy="sufficient",
            applicability_checked=True)["knowledge_item"]
    if knowledge_item["status"] != "active":
        raise WorkbenchError(
            f"New Knowledge Item（{knowledge_item_id}）がactiveになりませんでした"
            f"（status={knowledge_item['status']}）。再度Publishを実行してください"
            "（New側は再作成されません）。")

    # --- Step B: UPDATE verdictのみ、旧Knowledgeを非活性化する（Step A成功後にのみ実行、
    #     再開可能） --------------------------------------------------------------
    if verdict == "UPDATE":
        old_item_id = candidate["dedup_target_knowledge_item_id"]
        old_item = geo_knowledge_service.get_knowledge_item(client, old_item_id)
        if not old_item:
            raise WorkbenchError(f"置き換え対象のKnowledge Itemが見つかりません: {old_item_id}")
        if old_item["status"] != "superseded":
            if old_item["status"] != "active":
                raise WorkbenchError(
                    f"置き換え対象のKnowledge Item（{old_item_id}）がactiveではありません"
                    f"（status={old_item['status']}）。New側は既にactive化済みです——"
                    "旧Knowledgeの状態を確認してから再度Publishを実行してください。")
            geo_knowledge_service.review_knowledge_item(
                client, knowledge_item_id=old_item_id, reviewer_id=publisher,
                reviewer_role=publisher_role, review_decision="supersede",
                review_comment=f"Superseded by Learning Workbench publish of candidate {candidate_id}",
                evidence_adequacy="sufficient", applicability_checked=True)
            # review_knowledge_itemのretrieval_status自動初期化は「candidate/in_reviewから
            # 初めて抜ける瞬間」のみに限定されている（Phase2B-2、二軸の独立性を保つ設計）ため、
            # active→supersededのような後続遷移ではretrieval_statusへ一切触れない。旧
            # Knowledgeを実際にRetrievalから除外するには、この明示呼び出しが必須
            # （置き換え検証時に発見・修正、当初のPlanは既存挙動の誤読があった）。
            geo_knowledge_service.set_retrieval_status(
                client, knowledge_item_id=old_item_id, retrieval_status="deprecated",
                actor_id=publisher,
                reason=f"Superseded by Learning Workbench publish of candidate {candidate_id}")

    # --- 両ステップが揃って初めてpublishedへ ----------------------------------------
    candidate = learning_service.get_candidate(client, candidate_id)
    learning_service.record_revision(
        client, candidate_id, revised_by="human", revision_snapshot=_current_snapshot(candidate),
        change_summary="Published", created_by=publisher)
    _set_workbench_status(client, candidate_id, "published")
    geo_audit.log_audit(
        client, actor=publisher, action="learning_workbench_publish",
        target_table="geo_ios_learning_candidates", target_id=candidate_id,
        detail={"knowledge_item_id": knowledge_item_id, "dedup_verdict": verdict})
    return learning_service.get_candidate(client, candidate_id)
