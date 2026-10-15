"""Geopolitical Intelligence OS Phase B: Approval Gate API（§13-14、Route A / Route B）。

Phase I-A.1 SEC-1: approval_status="approved"にできるのは認証済みIosIdentity
（X-IOS-Credential、geo_ios_identity.verify_ios_identity）が解決したserver-side role
のみで判定する。request payload側のapprover_roleは互換性のため受け取るが、audit用の
mismatch検知にのみ使い、authorizationの根拠には一切使わない（§1絶対条件）。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

import geo_ios_approval_service as approval_service
import geo_ios_identity
import geo_ios_report_studio_adapter as report_studio_adapter
import geo_ios_analysis_session_service as session_service
import geo_ios_report_draft_service as report_draft_service
import geo_role_guard

_config = load_config()
_client = SupabaseClient(_config)

geo_ios_approval_router = APIRouter(prefix="/api/geo/ios/approval")


class RecordApprovalRequest(BaseModel):
    report_project_id: str
    approval_status: str
    # P1 Identity Attribution Closure（2026-09-23）: in-app操作（Route A）では互換性のため
    # 受け取るのみで、実際に記録するapproverは認証済みidentity.identity_idを使う
    # （audit/mismatch detection専用、authorizationには使わない）。
    approver: str
    approver_role: str | None = None
    version_label: str | None = None
    version_snapshot: dict | None = None
    notes: str | None = None
    actor_id: str | None = None


@geo_ios_approval_router.post("/in-app")
def approve_in_app(req: RecordApprovalRequest, identity: geo_ios_identity.IosIdentity = Depends(
        geo_ios_identity.verify_ios_identity)):
    """Route A: アプリ上でExpert/Approverが承認する。version_label/version_snapshotは常に
    サーバー側で（Native: geo_ios_report_draft_service.get_approvable_snapshot、Legacy:
    report_studio_adapter.get_approvable_snapshot経由で）再計算する——いずれも「Freezeされた
    内容に一致する、result==approvedのReview Cycleが存在する場合のみ」返す設計（§13:
    Approvalは常にExpert ReviewでFreezeされた内容のみを見る）。Route B（Upload）は対象外
    ——外部で既に承認された内容をReport Studioの"今の"状態で上書きしてはならないため、
    Upload側は常にcaller指定必須のままとする（geo_ios_approval_service.approve_via_upload）。

    Review Platform P0 Remediation（ユーザー指示、2026-09-22、Post-Freeze Review Platform
    Audit Finding P0-3対応）: 以前はreq.version_snapshotが非None（空dict含む）の場合、
    上記Gateロジック自体を丸ごとスキップし、クライアントが送った値をそのままApproval
    Eventへ書き込んでいた——認証さえ通れば、実際のReview結果を一切参照せずに任意の内容を
    承認扱いにできる経路だった。req.version_label/req.version_snapshotは互換性のため
    Requestモデルに残すが、Gate判断・実際に記録する内容のSource of Truthとしては一切
    使わない（常にサーバー側再計算値を使う）。

    P1 Identity Attribution Closure（ユーザー指示、2026-09-23、Mechanical E2E Pilot後の
    Known P1対応）: 記録するapproverもreq.approver（自己申告の表示名）ではなく認証済み
    identity.identity_idを正規の値として使う——approver_roleと同じ扱いへ揃えた。req.approver
    は互換性のためRequestモデルに残すが、mismatch検知にのみ使う。"""
    # SEC-1: authorizationを他の処理（Report Studio状態の読み取り等）より先に確定する
    # ——未認可の呼び出し者に、Collection Runの有無等の内部状態を先に開示しない（fail-fast）。
    try:
        geo_role_guard.require_role(identity.assigned_role, approval_service.APPROVAL_ROLES)
    except geo_role_guard.RoleDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    mismatch = geo_ios_identity.check_role_mismatch(identity, req.approver_role)
    approver_mismatch = geo_ios_identity.check_identity_mismatch(identity, req.approver)

    # P0-3: req.version_label/req.version_snapshotの値は一切参照しない（互換性のため
    # Requestモデルからは受け取るが、Gate判断にも実記録にも使わない）。常にサーバー側で
    # 「今のContent」を再構築し、それに一致するresult==approvedのReview Cycleが存在する
    # 場合のみ、そのCycleが保持するversion_snapshotを承認対象として使う。
    source_report_draft_id = source_draft_version = source_content_hash = None
    session = session_service.get_session_by_report_project(_client, req.report_project_id)
    if session and session.get("report_generation_mode") == "native":
        try:
            built = report_draft_service.get_approvable_snapshot(
                _client, session["analysis_session_id"])
        except report_draft_service.ReportDraftError as e:
            raise HTTPException(status_code=400, detail=str(e))
        version_label = built["version_label"]
        version_snapshot = built["version_snapshot"]
        source_report_draft_id = built["report_draft_id"]
        source_draft_version = built["draft_version"]
        source_content_hash = built["content_hash"]
    else:
        try:
            built = report_studio_adapter.get_approvable_snapshot(_client, req.report_project_id)
        except report_studio_adapter.ReportStudioAdapterError as e:
            raise HTTPException(status_code=400, detail=str(e))
        version_label = built["version_label"]
        version_snapshot = built["version_snapshot"]

    try:
        result = approval_service.record_approval_event(
            _client, report_project_id=req.report_project_id, approval_status=req.approval_status,
            approver=identity.identity_id, approver_role=identity.assigned_role, approval_source="in_app",
            version_label=version_label, version_snapshot=version_snapshot, notes=req.notes,
            actor_id=req.actor_id or identity.identity_id,
            source_report_draft_id=source_report_draft_id, source_draft_version=source_draft_version,
            source_content_hash=source_content_hash)
    except (approval_service.ApprovalError, geo_role_guard.RoleDeniedError) as e:
        raise HTTPException(status_code=403 if isinstance(e, geo_role_guard.RoleDeniedError) else 400,
                             detail=str(e))
    if mismatch:
        result = {**result, "_role_mismatch_warning": mismatch}
    if approver_mismatch:
        result = {**result, "_approver_identity_mismatch_warning": approver_mismatch}
    return result


class RecordUploadApprovalRequest(BaseModel):
    report_project_id: str
    approver: str
    approver_role: str | None = None  # 互換性のため受け取るのみ。authorizationには使わない。
    version_label: str | None = None
    version_snapshot: dict | None = None
    notes: str | None = None
    actor_id: str | None = None


@geo_ios_approval_router.post("/upload")
def approve_via_upload(req: RecordUploadApprovalRequest, identity: geo_ios_identity.IosIdentity = Depends(
        geo_ios_identity.verify_ios_identity)):
    """Route B: 別プロセスで承認済みのReportをUpload登録する場合。approverを必須で明示させる
    （§13: 単なるUploadを自動的にApproved扱いにしない）。"""
    mismatch = geo_ios_identity.check_role_mismatch(identity, req.approver_role)
    try:
        result = approval_service.approve_via_upload(
            _client, report_project_id=req.report_project_id, approver=req.approver,
            approver_role=identity.assigned_role, version_label=req.version_label,
            version_snapshot=req.version_snapshot, notes=req.notes,
            actor_id=req.actor_id or identity.identity_id)
    except (approval_service.ApprovalError, geo_role_guard.RoleDeniedError) as e:
        raise HTTPException(status_code=403 if isinstance(e, geo_role_guard.RoleDeniedError) else 400,
                             detail=str(e))
    if mismatch:
        result = {**result, "_role_mismatch_warning": mismatch}
    return result


@geo_ios_approval_router.get("/projects/{report_project_id}")
def list_approval_events(report_project_id: str):
    return {"events": approval_service.list_approval_events(_client, report_project_id)}


@geo_ios_approval_router.get("/projects/{report_project_id}/current")
def get_current_approval(report_project_id: str):
    current = approval_service.get_current_approval(_client, report_project_id)
    if not current:
        raise HTTPException(status_code=404, detail="このreport_project_idにApproval Eventはありません")
    return current
