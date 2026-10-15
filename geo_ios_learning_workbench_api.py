"""Geopolitical Intelligence OS: Learning Workbench API。

Plan（kind-napping-hare.md）C章のとおり、全mutating endpointは
Depends(geo_ios_identity.verify_ios_identity)必須。reviewed_by/roleはpayload由来では
なく認証済みidentityから取る（既存geo_ios_learning_api.pyと同じcheck_role_mismatch/
check_identity_mismatch監査パターンを踏襲、payloadの値はaudit用のみ）。

generalization risk assessmentとAI編集ChatはBackgroundTasksで実行する（既存
geo_ios_consultation_api.pyと同じpending/completed/failedポーリング方式）。
"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from ai_client import make_openai_client  # noqa: E402
from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

import geo_ios_identity
import geo_ios_learning_workbench_service as workbench_service
import geo_role_guard

_config = load_config()
_client = SupabaseClient(_config)
_azure_client, _model = make_openai_client(_config)

geo_ios_learning_workbench_router = APIRouter(prefix="/api/geo/ios/learning-workbench")


def _require_azure_client():
    if not _azure_client:
        raise HTTPException(status_code=503, detail="Azure OpenAI / OpenAI のAPIキーが未設定です")


def _handle(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except geo_role_guard.RoleDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except workbench_service.WorkbenchError as e:
        raise HTTPException(status_code=400, detail=str(e))


@geo_ios_learning_workbench_router.get("")
def list_workbench_candidates(workbench_status: str | None = None, candidate_type: str | None = None,
                               limit: int = 100):
    return {"candidates": workbench_service.list_workbench_candidates(
        _client, workbench_status=workbench_status, candidate_type=candidate_type, limit=limit)}


@geo_ios_learning_workbench_router.get("/{candidate_id}")
def get_workbench_item(candidate_id: str):
    return _handle(workbench_service.get_workbench_item, _client, candidate_id)


@geo_ios_learning_workbench_router.get("/{candidate_id}/lineage")
def get_candidate_lineage(candidate_id: str):
    return _handle(workbench_service.get_candidate_lineage, _client, candidate_id)


@geo_ios_learning_workbench_router.get("/{candidate_id}/siblings")
def get_sibling_candidates(candidate_id: str, review_cycle_id: str):
    return workbench_service.get_sibling_candidates(_client, review_cycle_id, candidate_id)


class ActorRequest(BaseModel):
    actor_id: str


def _run_risk_assessment_in_background(candidate_id: str, *, actor_id: str):
    try:
        workbench_service.assess_generalization_risk(
            _client, _azure_client, _model, candidate_id, actor_id=actor_id)
    except Exception:  # noqa: BLE001 - BackgroundTaskの例外は握りつぶさない方が良いが、
        # assess_generalization_riskはWorkbenchのAI提案に過ぎずGETは常にNULLのまま返せば
        # よいため、失敗時は静かに終える（candidateのgeneralization_riskはNULLのまま
        # 残り、Frontendは再度このPOSTを呼び直せる——専用のTurn/pending行を持たない
        # シンプルなfire-and-forget設計）。
        pass


@geo_ios_learning_workbench_router.post("/{candidate_id}/assess-generalization-risk")
def assess_generalization_risk(candidate_id: str, req: ActorRequest, background_tasks: BackgroundTasks,
                                identity: geo_ios_identity.IosIdentity = Depends(
                                    geo_ios_identity.verify_ios_identity)):
    """3回目Planレビュー②: GETの副作用にしない。専用POSTからBackgroundTasksで起動する。"""
    _require_azure_client()
    _handle(workbench_service.get_workbench_item, _client, candidate_id)
    background_tasks.add_task(
        _run_risk_assessment_in_background, candidate_id, actor_id=identity.identity_id)
    return {"status": "accepted"}


class SubmitInstructionRequest(BaseModel):
    human_instruction: str
    actor_id: str | None = None


def _run_chat_revision_in_background(candidate_id: str, *, workbench_turn_id: str,
                                      human_instruction: str, actor_id: str):
    workbench_service.run_chat_revision(
        _client, _azure_client, _model, candidate_id, workbench_turn_id=workbench_turn_id,
        human_instruction=human_instruction, actor_id=actor_id)


@geo_ios_learning_workbench_router.post("/{candidate_id}/instructions")
def submit_human_instruction(candidate_id: str, req: SubmitInstructionRequest,
                              background_tasks: BackgroundTasks,
                              identity: geo_ios_identity.IosIdentity = Depends(
                                  geo_ios_identity.verify_ios_identity)):
    _require_azure_client()
    actor_id = identity.identity_id
    turn = _handle(
        workbench_service.submit_human_instruction, _client, candidate_id,
        human_instruction=req.human_instruction, actor_id=actor_id)
    background_tasks.add_task(
        _run_chat_revision_in_background, candidate_id,
        workbench_turn_id=turn["workbench_turn_id"], human_instruction=req.human_instruction,
        actor_id=actor_id)
    return turn


@geo_ios_learning_workbench_router.get("/{candidate_id}/turns")
def list_turns(candidate_id: str):
    return {"turns": workbench_service.list_turns(_client, candidate_id)}


@geo_ios_learning_workbench_router.get("/{candidate_id}/revisions")
def list_revisions(candidate_id: str):
    return {"revisions": workbench_service.list_revisions(_client, candidate_id)}


class RecordHumanEditRequest(BaseModel):
    edited_fields: dict
    actor_id: str | None = None
    change_summary: str | None = None


@geo_ios_learning_workbench_router.post("/{candidate_id}/edit")
def record_human_edit(candidate_id: str, req: RecordHumanEditRequest,
                       identity: geo_ios_identity.IosIdentity = Depends(
                           geo_ios_identity.verify_ios_identity)):
    return _handle(
        workbench_service.record_human_edit, _client, candidate_id,
        edited_fields=req.edited_fields, actor_id=identity.identity_id,
        change_summary=req.change_summary)


class RecordDedupVerdictRequest(BaseModel):
    verdict: str
    target_knowledge_item_id: str | None = None
    related_knowledge_item_ids: list[str] | None = None
    resolution_note: str | None = None
    actor_id: str | None = None


@geo_ios_learning_workbench_router.post("/{candidate_id}/dedup-verdict")
def record_dedup_verdict(candidate_id: str, req: RecordDedupVerdictRequest,
                          identity: geo_ios_identity.IosIdentity = Depends(
                              geo_ios_identity.verify_ios_identity)):
    return _handle(
        workbench_service.record_dedup_verdict, _client, candidate_id, verdict=req.verdict,
        target_knowledge_item_id=req.target_knowledge_item_id,
        related_knowledge_item_ids=req.related_knowledge_item_ids,
        resolution_note=req.resolution_note, actor_id=identity.identity_id)


@geo_ios_learning_workbench_router.post("/{candidate_id}/ready-for-approval")
def mark_ready_for_approval(candidate_id: str, req: ActorRequest,
                             identity: geo_ios_identity.IosIdentity = Depends(
                                 geo_ios_identity.verify_ios_identity)):
    return _handle(workbench_service.mark_ready_for_approval, _client, candidate_id,
                    actor_id=identity.identity_id)


@geo_ios_learning_workbench_router.post("/{candidate_id}/back-to-editing")
def mark_editing(candidate_id: str, req: ActorRequest,
                  identity: geo_ios_identity.IosIdentity = Depends(
                      geo_ios_identity.verify_ios_identity)):
    return _handle(workbench_service.mark_editing, _client, candidate_id, actor_id=identity.identity_id)


class ApproveRequest(BaseModel):
    # P1 Identity Attribution Closure方針を踏襲: reviewed_by/reviewer_roleは互換性のため
    # 受け取るが、実際に記録するのは認証済みidentityの値のみ（audit用mismatch検知にのみ使う）。
    reviewed_by: str | None = None
    reviewer_role: str | None = None
    actor_id: str | None = None


@geo_ios_learning_workbench_router.post("/{candidate_id}/approve")
def approve_in_workbench(candidate_id: str, req: ApproveRequest,
                          identity: geo_ios_identity.IosIdentity = Depends(
                              geo_ios_identity.verify_ios_identity)):
    mismatch = geo_ios_identity.check_role_mismatch(identity, req.reviewer_role)
    identity_mismatch = geo_ios_identity.check_identity_mismatch(identity, req.reviewed_by)
    result = _handle(
        workbench_service.approve_in_workbench, _client, candidate_id,
        reviewed_by=identity.identity_id, reviewer_role=identity.assigned_role,
        actor_id=req.actor_id or identity.identity_id)
    warnings = {}
    if mismatch:
        warnings["_role_mismatch_warning"] = mismatch
    if identity_mismatch:
        warnings["_reviewed_by_identity_mismatch_warning"] = identity_mismatch
    return {**result, **warnings} if warnings else result


class RejectRequest(BaseModel):
    reviewed_by: str | None = None
    reviewer_role: str | None = None
    reason: str | None = None
    actor_id: str | None = None


@geo_ios_learning_workbench_router.post("/{candidate_id}/reject")
def reject_in_workbench(candidate_id: str, req: RejectRequest,
                         identity: geo_ios_identity.IosIdentity = Depends(
                             geo_ios_identity.verify_ios_identity)):
    mismatch = geo_ios_identity.check_role_mismatch(identity, req.reviewer_role)
    identity_mismatch = geo_ios_identity.check_identity_mismatch(identity, req.reviewed_by)
    result = _handle(
        workbench_service.reject_in_workbench, _client, candidate_id,
        reviewed_by=identity.identity_id, reviewer_role=identity.assigned_role,
        reason=req.reason, actor_id=req.actor_id or identity.identity_id)
    warnings = {}
    if mismatch:
        warnings["_role_mismatch_warning"] = mismatch
    if identity_mismatch:
        warnings["_reviewed_by_identity_mismatch_warning"] = identity_mismatch
    return {**result, **warnings} if warnings else result


class PublishRequest(BaseModel):
    publisher: str | None = None
    publisher_role: str | None = None
    actor_id: str | None = None


@geo_ios_learning_workbench_router.post("/{candidate_id}/publish")
def publish_from_workbench(candidate_id: str, req: PublishRequest,
                            identity: geo_ios_identity.IosIdentity = Depends(
                                geo_ios_identity.verify_ios_identity)):
    mismatch = geo_ios_identity.check_role_mismatch(identity, req.publisher_role)
    identity_mismatch = geo_ios_identity.check_identity_mismatch(identity, req.publisher)
    result = _handle(
        workbench_service.publish_from_workbench, _client, candidate_id,
        publisher=identity.identity_id, publisher_role=identity.assigned_role,
        actor_id=req.actor_id or identity.identity_id)
    warnings = {}
    if mismatch:
        warnings["_role_mismatch_warning"] = mismatch
    if identity_mismatch:
        warnings["_publisher_identity_mismatch_warning"] = identity_mismatch
    return {**result, **warnings} if warnings else result
