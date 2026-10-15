"""Geopolitical Intelligence OS Phase B: Expert Review Workflow API（§7-12、無制限ラウンド）。

既存/api/geo/expert-review/*（geo_expert_review_api.py、Internal Expert Learning Layer）とは
別の名前空間/api/geo/ios/review/*にマウントする——概念が異なるため統合しない（Phase 0監査参照）。

Review Platform P0 Remediation（ユーザー指示、2026-09-22、Post-Freeze Review Platform
Audit Finding P0-1対応）: このrouterはApproval/Learning-candidate/Knowledge Publication
router（geo_ios_identity.verify_ios_identity）と同じ認証境界に、これまで含まれていな
かった——reviewer identityがrequest body自己申告のまま受理され、`/cycles/{id}/submit`
（result="approved"を設定する）に認証が一切無く、下流のApproval Gateが信頼する
"approved"という評価結果自体を無認証で偽造できてしまっていた。全endpointへ
Depends(geo_ios_identity.verify_ios_identity)を追加し、result="approved"を確定させる
submit_review_findingsについては、認証済みidentityがそのcycleへ割り当てられた
reviewer本人（またはadministrator）であることまで追加で拘束する（役割一致だけでは
「別のexpert_reviewerが他人のcycleを承認する」ことを防げないため）。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

import geo_ios_review_service as review_service
import geo_notification_service as notification_service
import geo_ios_report_studio_adapter as report_studio_adapter
import geo_ios_analysis_session_service as session_service
import geo_ios_report_draft_service as report_draft_service
import geo_ios_identity
import geo_role_guard

_config = load_config()
_client = SupabaseClient(_config)

geo_ios_review_router = APIRouter(prefix="/api/geo/ios/review")


def _require_assigned_reviewer(cycle: dict, identity: "geo_ios_identity.IosIdentity") -> None:
    """submit_review_findings専用の拘束（P0-1対応）: 認証済みidentityが、このcycleへ実際に
    割り当てられたreviewer本人であること（またはadministrator）を要求する。role=
    expert_reviewerであるだけでは、他人のcycleを承認できてしまうため不十分。"""
    if identity.assigned_role == "administrator":
        return
    try:
        geo_role_guard.require_role(identity.assigned_role, ("expert_reviewer",))
    except geo_role_guard.RoleDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    if identity.identity_id != cycle.get("reviewer"):
        raise HTTPException(
            status_code=403,
            detail="このreview_cycleへ割り当てられたreviewer本人のみがresultを確定できます")


class RequestReviewCycleRequest(BaseModel):
    report_project_id: str
    reviewer: str
    requested_by: str
    version_label: str | None = None
    version_snapshot: dict | None = None
    analysis_session_id: str | None = None
    message: str | None = None
    due_date: str | None = None


@geo_ios_review_router.post("/cycles")
def request_review_cycle(req: RequestReviewCycleRequest, identity: geo_ios_identity.IosIdentity = Depends(
        geo_ios_identity.verify_ios_identity)):
    """Phase D 実接続: version_snapshotを呼び出し側が明示しない場合、既存Report Studio公開関数
    （get_latest_run/list_latest_syntheses_for_project、読み取り専用）経由でReport Studio自身の
    「現在の状態」を自動でスナップショットする（§8）。Report Studio内部は一切変更しない——
    geo_ios_report_studio_adapter越しにのみ触れる。

    §9 Expert Notification: 依頼作成に成功したら通知を試みる（未配線でも失敗させない、
    NotificationServiceがスタブでも呼び出し自体は成立する）。

    SEC: Reviewer Spoofing対策。req.reviewerはUI Select由来の値だが、クライアントの
    入力をそのまま信用しない——server-side geo_ios.identitiesに実在し、かつ
    role=expert_reviewerであることをここで検証する（UI Selectの選択肢制御はSecurity
    Boundaryにならない）。"""
    if geo_ios_identity.resolve_reviewer_identity(req.reviewer) is None:
        raise HTTPException(
            status_code=403,
            detail="reviewerが未登録、またはexpert_reviewer roleではありません")

    version_label, version_snapshot = req.version_label, req.version_snapshot
    source_report_draft_id = source_draft_version = source_content_hash = None
    if version_snapshot is None:
        # IOS Native Draft: report_generation_mode="native"のSessionのみ、Native Draftを
        # Freezeする経路へ分岐する。判定はSession側の明示フラグのみで行い、「Native Draftが
        # 存在するかどうか」の暗黙判定はしない（report_studio modeのSessionは、Native Draft
        # が偶然存在してもこの経路に入らない）。
        session = session_service.get_session(_client, req.analysis_session_id) \
            if req.analysis_session_id else None
        if session and session.get("report_generation_mode") == "native":
            try:
                built = report_draft_service.build_native_review_snapshot(
                    _client, req.analysis_session_id)
            except report_draft_service.ReportDraftError as e:
                raise HTTPException(status_code=400, detail=str(e))
            version_label = version_label or built["version_label"]
            version_snapshot = built["version_snapshot"]
            source_report_draft_id = built["report_draft_id"]
            source_draft_version = built["draft_version"]
            source_content_hash = built["content_hash"]
        else:
            try:
                built = report_studio_adapter.build_review_snapshot(_client, req.report_project_id)
            except report_studio_adapter.ReportStudioAdapterError as e:
                raise HTTPException(status_code=400, detail=str(e))
            version_label = version_label or built["version_label"]
            version_snapshot = built["version_snapshot"]

    cycle = review_service.request_review_cycle(
        _client, report_project_id=req.report_project_id, reviewer=req.reviewer,
        requested_by=req.requested_by, version_label=version_label,
        version_snapshot=version_snapshot, analysis_session_id=req.analysis_session_id,
        message=req.message, due_date=req.due_date,
        source_report_draft_id=source_report_draft_id, source_draft_version=source_draft_version,
        source_content_hash=source_content_hash)
    notification_service.send_review_request(
        _client, reviewer=req.reviewer, report_project_id=req.report_project_id,
        review_cycle_id=cycle["review_cycle_id"], round_number=cycle["round_number"],
        message=req.message, due_date=req.due_date)
    return cycle


@geo_ios_review_router.get("/cycles/{review_cycle_id}")
def get_review_cycle(review_cycle_id: str):
    cycle = review_service.get_review_cycle(_client, review_cycle_id)
    if not cycle:
        raise HTTPException(status_code=404, detail="review_cycle_idが見つかりません")
    return cycle


@geo_ios_review_router.get("/projects/{report_project_id}/cycles")
def list_review_cycles(report_project_id: str):
    return {"cycles": review_service.list_review_cycles(_client, report_project_id)}


@geo_ios_review_router.get("/projects/{report_project_id}/summary")
def get_review_summary(report_project_id: str):
    """Existing Report → Review → Approvalの一連の流れを、UIやE2E確認から一度に見るための
    集約ビュー（新規テーブルは持たず、既存データからの導出のみ）。

    IOS Native Draft: analysis_session_id/report_generation_modeを含めることで、
    Frontend側（Review Request Dialog）がこのSessionのReview Cycle依頼にanalysis_session_id
    を渡せるようにする（渡さないとBackend側でNative/Legacyの判定ができずreport_studio
    経路にフォールバックしてしまうため）。"""
    cycles = review_service.list_review_cycles(_client, report_project_id)
    session = session_service.get_session_by_report_project(_client, report_project_id)
    return {
        "report_project_id": report_project_id,
        "report_project": report_studio_adapter.get_report_project_summary(_client, report_project_id),
        "total_rounds": len(cycles),
        "latest_cycle": cycles[-1] if cycles else None,
        "cycles": cycles,
        "analysis_session_id": (session or {}).get("analysis_session_id"),
        "report_generation_mode": (session or {}).get("report_generation_mode"),
    }


class ActorRequest(BaseModel):
    actor_id: str


@geo_ios_review_router.post("/cycles/{review_cycle_id}/start")
def start_review(review_cycle_id: str, req: ActorRequest, identity: geo_ios_identity.IosIdentity = Depends(
        geo_ios_identity.verify_ios_identity)):
    try:
        return review_service.start_review(_client, review_cycle_id, actor_id=req.actor_id)
    except review_service.ReviewWorkflowError as e:
        raise HTTPException(status_code=400, detail=str(e))


class SubmitFindingsRequest(BaseModel):
    findings: dict
    result: str
    reviewer: str
    actor_id: str | None = None


@geo_ios_review_router.post("/cycles/{review_cycle_id}/submit")
def submit_review_findings(review_cycle_id: str, req: SubmitFindingsRequest,
                            identity: geo_ios_identity.IosIdentity = Depends(
                                geo_ios_identity.verify_ios_identity)):
    # P0-1: result="approved"を含むFinding確定は、このcycleへ割り当てられた
    # reviewer本人（認証済みidentity）のみが行える。request body上のreq.reviewerは
    # 互換性のため受け取るのみで、authorizationには使わない。
    cycle = review_service.get_review_cycle(_client, review_cycle_id)
    if not cycle:
        raise HTTPException(status_code=404, detail=f"review_cycle_idが見つかりません: {review_cycle_id}")
    _require_assigned_reviewer(cycle, identity)
    try:
        cycle = review_service.submit_review_findings(
            _client, review_cycle_id, findings=req.findings, result=req.result,
            reviewer=identity.identity_id, actor_id=req.actor_id or identity.identity_id)
    except review_service.ReviewWorkflowError as e:
        raise HTTPException(status_code=400, detail=str(e))
    notification_service.send_review_completed(
        _client, notify=cycle["requested_by"], review_cycle_id=review_cycle_id, result_value=req.result)
    return cycle


class AuthorResponseRequest(BaseModel):
    author_response: str
    actor_id: str


@geo_ios_review_router.post("/cycles/{review_cycle_id}/author-response")
def record_author_response(review_cycle_id: str, req: AuthorResponseRequest,
                            identity: geo_ios_identity.IosIdentity = Depends(
                                geo_ios_identity.verify_ios_identity)):
    try:
        return review_service.record_author_response(
            _client, review_cycle_id, author_response=req.author_response, actor_id=req.actor_id)
    except review_service.ReviewWorkflowError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---- Review Comment（§10, §12） ---------------------------------------------

class AddCommentRequest(BaseModel):
    comment: str
    # P1 Identity Attribution Closure（2026-09-23）: 互換性のため受け取るのみ。実際に記録する
    # created_byは認証済みidentity.identity_id（audit/mismatch detection専用）。
    created_by: str
    anchor_type: str | None = None
    anchor_id: str | None = None
    severity: str | None = None


@geo_ios_review_router.post("/cycles/{review_cycle_id}/comments")
def add_comment(review_cycle_id: str, req: AddCommentRequest,
                 identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    """P1 Identity Attribution Closure（ユーザー指示、2026-09-23、Mechanical E2E Pilot後の
    Known P1対応）: Commentのcreated_byもreq.created_by（自己申告の表示名）ではなく認証済み
    identity.identity_idを正規の値として使う——「誰がどの指摘をしたか」の監査上のProvenanceを
    自己申告に依存させない。"""
    identity_mismatch = geo_ios_identity.check_identity_mismatch(identity, req.created_by)
    try:
        result = review_service.add_comment(
            _client, review_cycle_id, comment=req.comment, created_by=identity.identity_id,
            anchor_type=req.anchor_type, anchor_id=req.anchor_id, severity=req.severity)
    except review_service.ReviewWorkflowError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if identity_mismatch:
        result = {**result, "_created_by_identity_mismatch_warning": identity_mismatch}
    return result


@geo_ios_review_router.get("/cycles/{review_cycle_id}/comments")
def list_comments(review_cycle_id: str):
    return {"comments": review_service.list_comments(_client, review_cycle_id)}


class RespondToCommentRequest(BaseModel):
    resolution_status: str
    author_response: str | None = None
    resolved_in_version: str | None = None
    actor_id: str


@geo_ios_review_router.post("/comments/{review_comment_id}/respond")
def respond_to_comment(review_comment_id: str, req: RespondToCommentRequest,
                        identity: geo_ios_identity.IosIdentity = Depends(
                            geo_ios_identity.verify_ios_identity)):
    try:
        return review_service.respond_to_comment(
            _client, review_comment_id, resolution_status=req.resolution_status,
            author_response=req.author_response, resolved_in_version=req.resolved_in_version,
            actor_id=req.actor_id)
    except review_service.ReviewWorkflowError as e:
        raise HTTPException(status_code=400, detail=str(e))
