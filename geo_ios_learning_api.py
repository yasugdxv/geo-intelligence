"""Geopolitical Intelligence OS Phase B/G: Learning Candidate Review Inbox API（§20-25）。

Phase I-A.1 SEC-2: approve/reject/promoteのroleは認証済みIosIdentity（X-IOS-Credential）
からのみ解決する。request payload側のreviewer_role/created_by_roleは互換性のため受け取るが、
audit用のmismatch検知にのみ使う（geo_ios_approval_api.pyと同じ方針）。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

import geo_ios_identity
import geo_ios_learning_candidate_service as learning_service
import geo_ios_learning_issue_suggestion_service as issue_suggestion_service
import geo_ios_learning_knowledge_mapper as knowledge_mapper
import geo_knowledge_service
import geo_role_guard

_config = load_config()
_client = SupabaseClient(_config)

geo_ios_learning_router = APIRouter(prefix="/api/geo/ios/learning-candidates")


class CreateCandidateRequest(BaseModel):
    candidate_type: str
    observation: str
    generalized_rule: str
    extraction_model: str
    trigger_context: dict | None = None
    failure_pattern_example: str | None = None
    positive_example: str | None = None
    scope: str | None = None
    confidence: str | None = None
    source_report_id: str | None = None
    source_review_cycle_id: str | None = None
    source_review_comment_id: str | None = None
    source_expert_review_change_id: str | None = None


@geo_ios_learning_router.post("")
def create_candidate(req: CreateCandidateRequest):
    try:
        return learning_service.create_candidate(
            _client, candidate_type=req.candidate_type, observation=req.observation,
            generalized_rule=req.generalized_rule, extraction_model=req.extraction_model,
            trigger_context=req.trigger_context, failure_pattern_example=req.failure_pattern_example,
            positive_example=req.positive_example, scope=req.scope, confidence=req.confidence,
            source_report_id=req.source_report_id, source_review_cycle_id=req.source_review_cycle_id,
            source_review_comment_id=req.source_review_comment_id,
            source_expert_review_change_id=req.source_expert_review_change_id)
    except learning_service.LearningCandidateError as e:
        raise HTTPException(status_code=400, detail=str(e))


@geo_ios_learning_router.get("")
def list_candidates(status: str | None = None, candidate_type: str | None = None):
    return {"candidates": learning_service.list_candidates(
        _client, status=status, candidate_type=candidate_type)}


@geo_ios_learning_router.get("/{candidate_id}")
def get_candidate(candidate_id: str):
    candidate = learning_service.get_candidate(_client, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="candidate_idが見つかりません")
    return candidate


class ApproveCandidateRequest(BaseModel):
    # P1 Identity Attribution Closure（2026-09-23）: 互換性のため受け取るのみ。実際に記録する
    # reviewed_byは認証済みidentity.identity_id（audit/mismatch detection専用）。
    reviewed_by: str
    reviewer_role: str | None = None  # 互換性のため受け取るのみ。authorizationには使わない。
    edited_generalized_rule: str | None = None
    actor_id: str | None = None


@geo_ios_learning_router.post("/{candidate_id}/approve")
def approve_candidate(candidate_id: str, req: ApproveCandidateRequest, identity: geo_ios_identity.IosIdentity = Depends(
        geo_ios_identity.verify_ios_identity)):
    mismatch = geo_ios_identity.check_role_mismatch(identity, req.reviewer_role)
    identity_mismatch = geo_ios_identity.check_identity_mismatch(identity, req.reviewed_by)
    try:
        result = learning_service.approve_candidate(
            _client, candidate_id, reviewed_by=identity.identity_id, reviewer_role=identity.assigned_role,
            edited_generalized_rule=req.edited_generalized_rule, actor_id=req.actor_id or identity.identity_id)
    except (learning_service.LearningCandidateError, geo_role_guard.RoleDeniedError) as e:
        raise HTTPException(status_code=403 if isinstance(e, geo_role_guard.RoleDeniedError) else 400,
                             detail=str(e))
    warnings = {}
    if mismatch:
        warnings["_role_mismatch_warning"] = mismatch
    if identity_mismatch:
        warnings["_reviewed_by_identity_mismatch_warning"] = identity_mismatch
    return {**result, **warnings} if warnings else result


class RejectCandidateRequest(BaseModel):
    # P1 Identity Attribution Closure（2026-09-23）: 互換性のため受け取るのみ。実際に記録する
    # reviewed_byは認証済みidentity.identity_id（audit/mismatch detection専用）。
    reviewed_by: str
    reviewer_role: str | None = None  # 互換性のため受け取るのみ。authorizationには使わない。
    reason: str | None = None
    actor_id: str | None = None


@geo_ios_learning_router.post("/{candidate_id}/reject")
def reject_candidate(candidate_id: str, req: RejectCandidateRequest, identity: geo_ios_identity.IosIdentity = Depends(
        geo_ios_identity.verify_ios_identity)):
    mismatch = geo_ios_identity.check_role_mismatch(identity, req.reviewer_role)
    identity_mismatch = geo_ios_identity.check_identity_mismatch(identity, req.reviewed_by)
    try:
        result = learning_service.reject_candidate(
            _client, candidate_id, reviewed_by=identity.identity_id, reviewer_role=identity.assigned_role,
            reason=req.reason, actor_id=req.actor_id or identity.identity_id)
    except (learning_service.LearningCandidateError, geo_role_guard.RoleDeniedError) as e:
        raise HTTPException(status_code=403 if isinstance(e, geo_role_guard.RoleDeniedError) else 400,
                             detail=str(e))
    warnings = {}
    if mismatch:
        warnings["_role_mismatch_warning"] = mismatch
    if identity_mismatch:
        warnings["_reviewed_by_identity_mismatch_warning"] = identity_mismatch
    return {**result, **warnings} if warnings else result


class PromoteCandidateRequest(BaseModel):
    # P1 Identity Attribution Closure（2026-09-23）: 互換性のため受け取るのみ。実際に記録する
    # created_byは認証済みidentity.identity_id（audit/mismatch detection専用）。
    created_by: str
    created_by_role: str | None = None  # 互換性のため受け取るのみ。authorizationには使わない。
    target_entity_id: str | None = None  # actor_intelligence候補のみ必須（Mapper参照）


@geo_ios_learning_router.post("/{candidate_id}/promote")
def promote_to_knowledge_item(candidate_id: str, req: PromoteCandidateRequest, identity: geo_ios_identity.IosIdentity = Depends(
        geo_ios_identity.verify_ios_identity)):
    """Phase G §22: knowledge_type/structured_contentの自由入力は受け付けない——
    geo_ios_learning_knowledge_mapperが候補のcandidate_typeから一元的に導出する。
    approved状態のCandidateを既存Knowledge DBのcandidate状態へ橋渡しする（Active化はしない、
    既存review_knowledge_itemを別途経由する必要がある）。

    P1 Identity Attribution Closure（ユーザー指示、2026-09-23、Mechanical E2E Pilot後の
    Known P1対応）: 生成するKnowledge Itemのcreated_byもreq.created_by（自己申告の表示名）
    ではなく認証済みidentity.identity_idを正規の値として使う。"""
    mismatch = geo_ios_identity.check_role_mismatch(identity, req.created_by_role)
    identity_mismatch = geo_ios_identity.check_identity_mismatch(identity, req.created_by)
    try:
        result = learning_service.promote_via_mapper(
            _client, candidate_id, created_by=identity.identity_id, created_by_role=identity.assigned_role,
            target_entity_id=req.target_entity_id)
    except (learning_service.LearningCandidateError, knowledge_mapper.MappingError,
            geo_role_guard.RoleDeniedError) as e:
        raise HTTPException(status_code=403 if isinstance(e, geo_role_guard.RoleDeniedError) else 400,
                             detail=str(e))
    except Exception as e:  # noqa: BLE001 — 既存schemas.validate_structured_contentの検証エラー等
        raise HTTPException(status_code=400, detail=str(e))
    warnings = {}
    if mismatch:
        warnings["_role_mismatch_warning"] = mismatch
    if identity_mismatch:
        warnings["_created_by_identity_mismatch_warning"] = identity_mismatch
    return {**result, **warnings} if warnings else result


@geo_ios_learning_router.get("/{candidate_id}/suggest-issues")
def suggest_issues(candidate_id: str, limit: int = 5):
    """Phase H §28-29: スコア付きIssue候補を返すのみ。自動リンクはしない
    （リンクはPOST /{candidate_id}/link-issueを人間が明示的に呼ぶ）。"""
    try:
        return {"suggestions": issue_suggestion_service.suggest_related_issues(
            _client, candidate_id, limit=limit)}
    except learning_service.LearningCandidateError as e:
        raise HTTPException(status_code=404, detail=str(e))


class LinkIssueRequest(BaseModel):
    issue_id: str


@geo_ios_learning_router.post("/{candidate_id}/link-issue")
def link_issue(candidate_id: str, req: LinkIssueRequest):
    """§29: Human Confirmationを経た明示的なリンクのみ許可する（Suggestion自体はリンクしない）。
    候補がまだpromote済み（approved_knowledge_item_idを持つ）である必要がある。"""
    candidate = learning_service.get_candidate(_client, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="candidate_idが見つかりません")
    if not candidate.get("approved_knowledge_item_id"):
        raise HTTPException(status_code=400, detail="このCandidateはまだKnowledge Itemへpromoteされていません")
    return geo_knowledge_service.link_to_issue(
        _client, knowledge_item_id=candidate["approved_knowledge_item_id"], issue_id=req.issue_id)


@geo_ios_learning_router.get("/cqas-signals/summary")
def cqas_signals_summary(limit: int = 500):
    """Phase H §30-31: CQAS Signal集計（generation_miss/ranking_miss/selection_miss/
    validated_candidate）。CQAS ranking algorithm自体は変更しない、記録済みSignalの集計のみ。"""
    return issue_suggestion_service.summarize_cqas_signals(_client, limit=limit)
