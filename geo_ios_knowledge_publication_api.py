"""Geopolitical Intelligence OS Phase E: Knowledge Publication API（§18）。

「Publish to Intelligence Library」相当の最小限の操作のみ。UI大改修はしない——Approval後に
human_visibility/agent_sharing_policyを明示指定してPublishできれば十分（§18, §19）。

Phase I-A.1 SEC-2: publisher_roleは認証済みIosIdentity（X-IOS-Credential）からのみ解決する。
request payload側のpublisher_roleは互換性のため受け取るが、audit用のmismatch検知にのみ使う。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ai_client import make_openai_client  # noqa: E402
from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

import geo_ios_identity
import geo_ios_knowledge_publication_service as publication_service
import geo_ios_learning_extraction_service as learning_extraction_service
import geo_ios_review_service as review_service
import geo_role_guard

_config = load_config()
_client = SupabaseClient(_config)
_azure_client, _model = make_openai_client(_config)

geo_ios_knowledge_publication_router = APIRouter(prefix="/api/geo/ios/knowledge-publication")


class PublishReportStudioRequest(BaseModel):
    report_project_id: str
    approval_event_id: str
    # P1 Identity Attribution Closure（2026-09-23）: 互換性のため受け取るのみ。実際に記録する
    # publisherは認証済みidentity.identity_id（audit/mismatch detection専用）。
    publisher: str
    publisher_role: str | None = None  # 互換性のため受け取るのみ。authorizationには使わない。
    human_visibility: str | None = None
    agent_sharing_policy: str | None = None
    # Phase 2E §8: DEPARTMENT_ONLY/SHD_ONLY/GROUP_SHAREABLEのいずれか。指定時はhuman_visibility/
    # agent_sharing_policyより優先される（Direct Report RegistrationのUIと同じLabel/Resolver）。
    confidentiality_label: str | None = None


@geo_ios_knowledge_publication_router.post("/report-studio")
def publish_report_studio(req: PublishReportStudioRequest, identity: geo_ios_identity.IosIdentity = Depends(
        geo_ios_identity.verify_ios_identity)):
    """Route A: Report Studio → Expert Review → Approval → Publish → Official Knowledge。

    P1 Identity Attribution Closure（ユーザー指示、2026-09-23、Mechanical E2E Pilot後の
    Known P1対応）: 記録するpublisherもreq.publisher（自己申告の表示名）ではなく認証済み
    identity.identity_idを正規の値として使う。Route B（publish_external、外部で既に
    Publishされた内容の代理登録）はこの対象外——そちらはpublisher/approverとも別人の
    可能性があるため、従来通りcaller指定必須のまま。"""
    mismatch = geo_ios_identity.check_role_mismatch(identity, req.publisher_role)
    identity_mismatch = geo_ios_identity.check_identity_mismatch(identity, req.publisher)
    try:
        result = publication_service.publish_approved_report(
            _client, report_project_id=req.report_project_id, approval_event_id=req.approval_event_id,
            publisher=identity.identity_id, publisher_role=identity.assigned_role,
            human_visibility=req.human_visibility, agent_sharing_policy=req.agent_sharing_policy,
            confidentiality_label=req.confidentiality_label)
    except (publication_service.PublicationError, geo_role_guard.RoleDeniedError) as e:
        raise HTTPException(status_code=403 if isinstance(e, geo_role_guard.RoleDeniedError) else 400,
                             detail=str(e))
    warnings = {}
    if mismatch:
        warnings["_role_mismatch_warning"] = mismatch
    if identity_mismatch:
        warnings["_publisher_identity_mismatch_warning"] = identity_mismatch
    return {**result, **warnings} if warnings else result


class PublishExternalRequest(BaseModel):
    report_id: str
    publisher: str
    approver: str
    publisher_role: str | None = None  # 互換性のため受け取るのみ。authorizationには使わない。
    human_visibility: str | None = None
    agent_sharing_policy: str | None = None
    confidentiality_label: str | None = None
    approval_date: str | None = None
    source_file: str | None = None
    uploaded_by: str | None = None


@geo_ios_knowledge_publication_router.post("/external")
def publish_external(req: PublishExternalRequest, identity: geo_ios_identity.IosIdentity = Depends(
        geo_ios_identity.verify_ios_identity)):
    """Route B: Report Studioに紐づかない完全外部Report（§20）。"""
    mismatch = geo_ios_identity.check_role_mismatch(identity, req.publisher_role)
    try:
        result = publication_service.publish_external_approved_report(
            _client, report_id=req.report_id, publisher=req.publisher, publisher_role=identity.assigned_role,
            human_visibility=req.human_visibility, agent_sharing_policy=req.agent_sharing_policy,
            confidentiality_label=req.confidentiality_label,
            approver=req.approver, approval_date=req.approval_date, source_file=req.source_file,
            uploaded_by=req.uploaded_by)
    except (publication_service.PublicationError, geo_role_guard.RoleDeniedError) as e:
        raise HTTPException(status_code=403 if isinstance(e, geo_role_guard.RoleDeniedError) else 400,
                             detail=str(e))
    return {**result, "_role_mismatch_warning": mismatch} if mismatch else result


@geo_ios_knowledge_publication_router.get("/report-studio/{report_project_id}/current")
def get_current_published_knowledge(report_project_id: str):
    item = publication_service.get_active_knowledge_for_report_project(_client, report_project_id)
    if not item:
        raise HTTPException(status_code=404, detail="このreport_project_idにPublish済みKnowledgeはありません")
    return item


class ExtractLearningRequest(BaseModel):
    extraction_model: str | None = None
    prompt_version: str | None = None
    require_published: bool = True
    actor_id: str = "geo_learning_extraction"


@geo_ios_knowledge_publication_router.post("/report-studio/{report_project_id}/extract-learning")
def extract_learning(report_project_id: str, req: ExtractLearningRequest):
    """Phase G §16: 既定はPublished Official Knowledge後の実行を要求する（手動先行実行は
    require_published=falseで可能）。

    Learning Workbench対応（2026-09-24、Plan A章）: 呼び出しインターフェース（Project単位）
    は維持しつつ、内部ではReview Cycleごとに1回ずつextract_learning_candidates(...,
    review_cycle_id=...)を呼ぶループへ変更する——Round跨ぎでBefore/Afterが混在しないように
    するため（1 Round＝1回のLLM抽出呼び出し）。review_cycleが1件も無いProjectでは従来どおり
    Project全体を対象とする呼び出し（review_cycle_id省略）にフォールバックする（後方互換）。"""
    if not _azure_client:
        raise HTTPException(status_code=503, detail="Azure OpenAI / OpenAI のAPIキーが未設定です")
    try:
        review_cycles = review_service.list_review_cycles(_client, report_project_id)
        if not review_cycles:
            return learning_extraction_service.extract_learning_candidates(
                _client, _azure_client, _model, report_project_id=report_project_id,
                extraction_model=req.extraction_model, prompt_version=req.prompt_version,
                require_published=req.require_published, actor_id=req.actor_id)

        results_by_cycle = {}
        all_candidates = []
        total_deduplicated, total_conflict = 0, 0
        any_idempotent_reuse = False
        for cycle in review_cycles:
            cycle_id = cycle["review_cycle_id"]
            result = learning_extraction_service.extract_learning_candidates(
                _client, _azure_client, _model, report_project_id=report_project_id,
                extraction_model=req.extraction_model, prompt_version=req.prompt_version,
                require_published=req.require_published, actor_id=req.actor_id,
                review_cycle_id=cycle_id)
            results_by_cycle[cycle_id] = result
            all_candidates.extend(result.get("candidates", []))
            total_deduplicated += result.get("deduplicated_count", 0)
            total_conflict += result.get("conflict_count", 0)
            any_idempotent_reuse = any_idempotent_reuse or result.get("idempotent_reuse", False)
        return {
            "candidates": all_candidates, "idempotent_reuse": any_idempotent_reuse,
            "deduplicated_count": total_deduplicated, "conflict_count": total_conflict,
            "results_by_review_cycle": results_by_cycle,
        }
    except learning_extraction_service.LearningExtractionError as e:
        raise HTTPException(status_code=400, detail=str(e))
