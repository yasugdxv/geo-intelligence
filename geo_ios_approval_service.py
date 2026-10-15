"""Geopolitical Intelligence OS Phase B: Approval Gate（§13-14）。

Route A（アプリ内承認）・Route B（Upload）の両方をgeo_ios_approval_eventsへ記録する。Upload経路
であっても、approval_status/approver/approval_dateを明示的に指定したapproval_eventが無ければ
Approved扱いにしない（§13: 単なるUploadを自動的にApproved扱いにしないこと）。承認済みReportの
直接編集（§14 Immutability）はReport Studio側の責務であり、本モジュールはApprovalの事実の記録
（イベントソーシング）のみを担う——「現在approved扱いかどうか」はgeo_ios_approval_eventsの最新行
から都度導出する。
"""
import geo_audit
import geo_role_guard

APPROVAL_STATUSES = ("approved", "rejected")
APPROVAL_SOURCES = ("in_app", "upload")

# Phase I-A.1 SEC-1: 承認可能roleは"approver"のみ（administratorは常に許可、geo_role_guard参照）。
# expert_reviewer/knowledge_stewardはReview/Publicationは行えるが、最終承認そのものは別役割
# として分離する（職務分離、Approval Gateの絶対条件を裏付ける最小限の権限境界）。
APPROVAL_ROLES = ("approver",)


class ApprovalError(Exception):
    """Approval Eventの操作エラー。"""


def record_approval_event(client, *, report_project_id: str, approval_status: str, approver: str,
                           approver_role: str, approval_source: str, version_label: str = None,
                           version_snapshot: dict = None, notes: str = None,
                           actor_id: str = None, source_report_draft_id: str = None,
                           source_draft_version: int = None, source_content_hash: str = None) -> dict:
    """Phase I-A.1 SEC-1: approver_roleは認証済みIosIdentity.assigned_roleから呼び出し元
    （geo_ios_approval_api.py）が渡す値のみを信頼する——request payload由来のrole自己申告を
    ここで受け取ってはならない（呼び出し元API層の責務）。service層でも独立にrequire_roleを
    再検証することで、内部関数の直接呼び出しによるbypassを防止する（§3 defense-in-depth）。"""
    geo_role_guard.require_role(approver_role, APPROVAL_ROLES)
    if approval_status not in APPROVAL_STATUSES:
        raise ApprovalError(f"未対応のapproval_statusです: {approval_status}（許可: {APPROVAL_STATUSES}）")
    if approval_source not in APPROVAL_SOURCES:
        raise ApprovalError(f"未対応のapproval_sourceです: {approval_source}（許可: {APPROVAL_SOURCES}）")
    if not approver:
        raise ApprovalError("approverは必須です（Upload経由でも承認者を明示すること、§13）")

    row = {
        "report_project_id": report_project_id, "version_label": version_label,
        "version_snapshot": version_snapshot, "approval_status": approval_status,
        "approver": approver, "approval_source": approval_source, "notes": notes,
        "source_report_draft_id": source_report_draft_id,
        "source_draft_version": source_draft_version, "source_content_hash": source_content_hash,
    }
    created = client.insert("geo_ios_approval_events", [row])[0]
    geo_audit.log_audit(client, actor=actor_id or approver, action="approval_event_record",
                         target_table="geo_ios_approval_events", target_id=created["approval_event_id"],
                         detail={"report_project_id": report_project_id, "approval_status": approval_status,
                                 "approval_source": approval_source})
    return created


def approve_in_app(client, *, report_project_id: str, approver: str, approver_role: str,
                    version_label: str = None, version_snapshot: dict = None, notes: str = None,
                    actor_id: str = None) -> dict:
    return record_approval_event(
        client, report_project_id=report_project_id, approval_status="approved", approver=approver,
        approver_role=approver_role, approval_source="in_app", version_label=version_label,
        version_snapshot=version_snapshot, notes=notes, actor_id=actor_id)


def approve_via_upload(client, *, report_project_id: str, approver: str, approver_role: str,
                        version_label: str = None, version_snapshot: dict = None, notes: str = None,
                        actor_id: str = None) -> dict:
    """Route B: 別プロセスで承認済みのReportをUpload登録する場合も、approver/approval_dateを
    必須で明示させる（Uploadという行為自体は承認を意味しない）。"""
    return record_approval_event(
        client, report_project_id=report_project_id, approval_status="approved", approver=approver,
        approver_role=approver_role, approval_source="upload", version_label=version_label,
        version_snapshot=version_snapshot, notes=notes, actor_id=actor_id)


def list_approval_events(client, report_project_id: str) -> list:
    return client.select("geo_ios_approval_events", {
        "select": "*", "report_project_id": f"eq.{report_project_id}", "order": "approval_date.desc"})


def get_current_approval(client, report_project_id: str) -> dict | None:
    """最新のapproval_eventを「現在の承認状態」として返す（過去版はAudit目的でそのまま保持、§14）。"""
    events = list_approval_events(client, report_project_id)
    return events[0] if events else None


def get_approval_event(client, approval_event_id: str) -> dict | None:
    rows = client.select("geo_ios_approval_events", {
        "select": "*", "approval_event_id": f"eq.{approval_event_id}", "limit": "1"})
    return rows[0] if rows else None


def mark_published(client, approval_event_id: str, *, knowledge_item_id: str, actor_id: str) -> dict:
    """Phase E §19: ApprovalとPublicationを分離して追跡する。approval_status自体は変更しない
    （承認は既に確定済み）——publication_statusだけを進める。"""
    client.update("geo_ios_approval_events", {"approval_event_id": f"eq.{approval_event_id}"}, {
        "publication_status": "published", "published_knowledge_item_id": knowledge_item_id,
    })
    geo_audit.log_audit(client, actor=actor_id, action="approval_event_mark_published",
                         target_table="geo_ios_approval_events", target_id=approval_event_id,
                         detail={"knowledge_item_id": knowledge_item_id})
    return get_approval_event(client, approval_event_id)
