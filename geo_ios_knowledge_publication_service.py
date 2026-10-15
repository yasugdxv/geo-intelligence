"""Geopolitical Intelligence OS Phase E: Approved Report → Official Knowledge Publication。

既存geo_knowledge_service（create_knowledge_item/review_knowledge_item、状態遷移の検証込み）を
最大限再利用し、新しいActive化ロジックは作らない（絶対条件）。本モジュールは、
    (a) Publication前提条件の検証（§5）
    (b) 既存関数を正しい順序で呼ぶOrchestration
    (c) Provenance列（source_report_project_id等）・Publication State（§19）の配線
    (d) Approved Version Only（旧versionのsupersession、§8）
に責務を限定するAdapter/Orchestratorであり、Report Studio本体・Expert AI本体のコードは
一切呼び出さない（get_project/get_or_create_projectのような既存の安定した公開関数のみ使用）。

Route A: Report Studio Project（geo_report_projects）+ IOS Approval Event（geo_ios_approval_events）
Route B: 既存geo_reports（Upload型）+ 明示的なapprover/approval_date（§20、IOS Approval機構は経由しない）
"""
from datetime import datetime, timezone

import geo_ios_approval_service as approval_service
import geo_knowledge_access_policy as access_policy
import geo_knowledge_service
import geo_report_studio_service as report_studio_service
import geo_role_guard

# Publishは「これを正式にOrganizational Knowledgeとして登録する」という重い意思決定のため、
# 既存の"expert_reviewer"より上位のknowledge_steward以上のみ許可する（administratorは
# geo_role_guard.require_role内で常に許可される）。
PUBLISH_ROLES = ("knowledge_steward",)

APPROVED_REPORT_KNOWLEDGE_TYPE = "approved_report"


class PublicationError(Exception):
    """Publication前提条件違反、またはPublication処理自体のエラー（§5）。"""


def _validate_policy_values(human_visibility: str, agent_sharing_policy: str) -> None:
    if human_visibility not in access_policy.HUMAN_VISIBILITY_LEVELS:
        raise PublicationError(
            f"未対応のhuman_visibilityです: {human_visibility}（許可: {access_policy.HUMAN_VISIBILITY_LEVELS}）")
    if agent_sharing_policy not in access_policy.AGENT_SHARING_POLICIES:
        raise PublicationError(
            f"未対応のagent_sharing_policyです: {agent_sharing_policy}（許可: {access_policy.AGENT_SHARING_POLICIES}）")


def get_active_knowledge_for_report_project(client, report_project_id: str) -> dict | None:
    """§8 Approved Version Only: このReport Project由来で現在activeなapproved_report
    Knowledge Itemを返す（無ければNone）。"""
    rows = client.select("geo_knowledge_items", {
        "select": "*", "knowledge_type": f"eq.{APPROVED_REPORT_KNOWLEDGE_TYPE}",
        "source_report_project_id": f"eq.{report_project_id}", "status": "eq.active", "limit": "1"})
    return rows[0] if rows else None


def get_active_knowledge_for_report(client, report_id: str) -> dict | None:
    """Route B版のget_active_knowledge_for_report_project。"""
    rows = client.select("geo_knowledge_items", {
        "select": "*", "knowledge_type": f"eq.{APPROVED_REPORT_KNOWLEDGE_TYPE}",
        "source_report_id": f"eq.{report_id}", "status": "eq.active", "limit": "1"})
    return rows[0] if rows else None


def _summarize_snapshot(version_snapshot: dict) -> list:
    """geo_ios_report_studio_adapter.build_review_snapshotが作るversion_snapshot形状
    （{"syntheses": [{"final_html":..., "final_status":...}, ...]}）から、本文を複製せず
    要約のみ抽出する（本文はReport Studio側を正とする、§6の"新規Engineを作らない"精神）。"""
    syntheses = (version_snapshot or {}).get("syntheses") or []
    summaries = []
    for s in syntheses:
        html = s.get("final_html") or ""
        text = html if len(html) <= 400 else html[:400] + "…"
        if text:
            summaries.append(text)
    return summaries


def publish_approved_report(client, *, report_project_id: str, approval_event_id: str, publisher: str,
                             publisher_role: str, human_visibility: str = None,
                             agent_sharing_policy: str = None,
                             actor_id: str = None, confidentiality_label: str = None) -> dict:
    """Route A（§4-8, §13A-D対応の起点）。

    前提条件（§5、失敗時はKnowledgeを一切作らない）:
    1. report project exists
    2. approval event exists かつ report_project_idが一致
    3. approval event の approval_status="approved"
    4. その approval event がまだpublishされていない
    5. より新しい（同時刻含む）approved版が既にpublish済みでない（version not superseded）
    6. publisherがPUBLISH_ROLES（knowledge_steward/administrator）を持つ
    7. human_visibility / agent_sharing_policyが既定enumの範囲内

    Phase 2E §8: confidentiality_label指定時はpublish_external_approved_reportと同じ
    Resolverで解決する（Direct Report RegistrationとReview Flow Publishが同じUI Label/
    Policy Resolverへ収束する）。Report Studio Project（geo_report_projects）自体は
    Phase 2E時点でAccess Policyを持たない（geo_reportsのような1:1メタデータ拡張が無い）ため、
    §7 Report→Knowledge inheritanceの継承元チェックはRoute Aには適用しない（継承すべき
    Source Policyが存在しないため。将来Report Studio Project側にもPolicyを持たせる場合は
    ここへ同様のvalidate_publication_scope呼び出しを追加する）。"""
    actor_id = actor_id or publisher
    geo_role_guard.require_role(publisher_role, PUBLISH_ROLES)
    allowed_org_units, allowed_domains = [], []
    if confidentiality_label:
        resolved = access_policy.resolve_ui_label_to_policy(confidentiality_label)
        human_visibility = resolved["human_visibility"]
        agent_sharing_policy = resolved["agent_sharing_policy"]
        allowed_org_units = resolved["allowed_org_units"]
        allowed_domains = resolved["allowed_domains"]
    _validate_policy_values(human_visibility, agent_sharing_policy)

    project = report_studio_service.get_project(client, report_project_id)
    if not project:
        raise PublicationError(f"report_project_idが見つかりません: {report_project_id}")

    approval_event = approval_service.get_approval_event(client, approval_event_id)
    if not approval_event or approval_event["report_project_id"] != report_project_id:
        raise PublicationError(
            f"approval_event_id={approval_event_id}がreport_project_id={report_project_id}を"
            "参照していません、または存在しません")
    if approval_event["approval_status"] != "approved":
        raise PublicationError(
            f"approval_event {approval_event_id} はapproved状態ではありません"
            f"（現在: {approval_event['approval_status']}）")
    if approval_event.get("publication_status") == "published":
        raise PublicationError(f"approval_event {approval_event_id} は既にPublish済みです")

    existing = get_active_knowledge_for_report_project(client, report_project_id)
    if existing:
        existing_approval = approval_service.get_approval_event(
            client, existing.get("source_approval_event_id")) if existing.get("source_approval_event_id") else None
        if existing_approval and existing_approval["approval_date"] >= approval_event["approval_date"]:
            raise PublicationError(
                "これより新しい（または同時刻の）approved版が既にpublish済みです"
                "（version not superseded precondition）")

    structured_content = {
        "source_type": "report_studio",
        "report_title": project.get("project_title") or project.get("project_code") or report_project_id,
        "syntheses_summary": _summarize_snapshot(approval_event.get("version_snapshot")),
        "report_project_id": report_project_id,
        "run_id": (approval_event.get("version_snapshot") or {}).get("run_id"),
        "approval_event_id": approval_event_id,
        "source_report_id": None, "source_file": None,
        "approver": approval_event["approver"],
        "approval_date": approval_event["approval_date"],
        "uploaded_by": None,
    }

    knowledge_item = geo_knowledge_service.create_knowledge_item(
        # information_class: geo_knowledge_items.information_classのCHECK制約は
        # observed_fact/actor_claim/external_assessment/internal_hypothesisの4値のみ
        # （geo_claims側のinternal_expert_assessmentとは別enum）。Report Studio自身の内部分析
        # 結果という性質上、internal_hypothesisが最も近い。
        client, knowledge_type=APPROVED_REPORT_KNOWLEDGE_TYPE, title=structured_content["report_title"],
        structured_content=structured_content, information_class="internal_hypothesis",
        created_by=publisher, confidence="中",
        supersedes_id=existing["knowledge_item_id"] if existing else None,
    )
    # Publish操作自体が「既にReport Studio側の承認プロセスを経た内容を、正式Knowledgeとして
    # 登録する」という人間の意思決定であるため、既存review_knowledge_item（人間操作専用の
    # 状態遷移）を同じ人物(publisher)の操作として直ちに実行し、candidate→in_review→activeへ
    # 進める（既存ALLOWED_TRANSITIONSはcandidateからactiveへの直接遷移を許可しないため、2段階
    # 遷移を両方とも実行する。§4絶対条件「ApprovalなしでOfficial Knowledgeにしない」は、IOS
    # Approval Eventの存在検証で既に満たしている——ここでのreview_knowledge_itemはKnowledge DB
    # 自身の状態機械を迂回しないための正規の経路であり、二重の恣意的判断ではない）。
    geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=knowledge_item["knowledge_item_id"], reviewer_id=publisher,
        reviewer_role=publisher_role, review_decision="start_review",
        review_comment=f"IOS Approval Event {approval_event_id} のPublishによる登録", evidence_adequacy="sufficient",
        applicability_checked=True,
    )
    geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=knowledge_item["knowledge_item_id"], reviewer_id=publisher,
        reviewer_role=publisher_role, review_decision="approve_active",
        review_comment=f"IOS Approval Event {approval_event_id} のPublishによる正式Knowledge登録",
        evidence_adequacy="sufficient", applicability_checked=True,
    )
    client.update("geo_knowledge_items", {"knowledge_item_id": f"eq.{knowledge_item['knowledge_item_id']}"}, {
        "source_report_project_id": report_project_id, "source_approval_event_id": approval_event_id,
        "source_report_version_label": approval_event.get("version_label"),
        "human_visibility": human_visibility, "agent_sharing_policy": agent_sharing_policy,
        "allowed_org_units": allowed_org_units, "allowed_domains": allowed_domains,
    })

    if existing:
        geo_knowledge_service.review_knowledge_item(
            client, knowledge_item_id=existing["knowledge_item_id"], reviewer_id=publisher,
            reviewer_role=publisher_role, review_decision="supersede",
            review_comment=f"新Version knowledge_item_id={knowledge_item['knowledge_item_id']} のPublishにより供給",
            evidence_adequacy="sufficient", applicability_checked=True,
        )

    approval_service.mark_published(
        client, approval_event_id, knowledge_item_id=knowledge_item["knowledge_item_id"], actor_id=actor_id)

    rows = client.select("geo_knowledge_items", {
        "select": "*", "knowledge_item_id": f"eq.{knowledge_item['knowledge_item_id']}", "limit": "1"})
    return {"knowledge_item_id": knowledge_item["knowledge_item_id"], "publication_status": "published",
            "knowledge_item": rows[0]}


def publish_external_approved_report(client, *, report_id: str, publisher: str, publisher_role: str,
                                      approver: str, human_visibility: str = None,
                                      agent_sharing_policy: str = None,
                                      approval_date: str = None, source_file: str = None,
                                      uploaded_by: str = None, actor_id: str = None,
                                      confidentiality_label: str = None) -> dict:
    """Route B（§20）: Report Studioに紐づかない完全外部Report。既存geo_reports（Upload型）を
    そのまま利用し、Analysis Session/CQAS provenanceは要求しない。IOS Approval機構
    （geo_ios_approval_events）は経由せず、approver/approval_dateを呼び出し側が直接明示する
    ——Uploadという行為自体をApprovedとみなさない、という絶対条件をここでも維持する。

    Phase 2E §8/§7: confidentiality_label（DEPARTMENT_ONLY/SHD_ONLY/GROUP_SHAREABLE）を渡すと、
    access_policy.resolve_ui_label_to_policyで解決した値がhuman_visibility/
    agent_sharing_policy引数より優先される（Direct Report RegistrationとReview Flow
    Publishが同じLabel/Resolverへ収束する、§8絶対条件）。省略時は既存どおり生のenum値を使う
    （Phase 2C以前からのPublish画面との後方互換）。いずれの経路でも、Publish先Knowledgeの
    公開範囲はSource Report自身の公開範囲より広げられない（§7 Report→Knowledge inheritance、
    Reportのpolicyがgeo_reportsに保存されていない/legacyな場合はderive_ui_label_from_policy
    がNone→DEPARTMENT_ONLY相当として扱われ、最も安全側の判定になる）。"""
    actor_id = actor_id or publisher
    geo_role_guard.require_role(publisher_role, PUBLISH_ROLES)
    if not approver:
        raise PublicationError("approverは必須です（Uploadという行為自体をApprovedとみなさない）")

    reports = client.select("geo_reports", {"select": "*", "report_id": f"eq.{report_id}", "limit": "1"})
    if not reports:
        raise PublicationError(f"report_idが見つかりません: {report_id}")
    report = reports[0]

    allowed_org_units, allowed_domains = [], []
    if confidentiality_label:
        resolved = access_policy.resolve_ui_label_to_policy(
            confidentiality_label, owner_department=report.get("owner_department"))
        human_visibility = resolved["human_visibility"]
        agent_sharing_policy = resolved["agent_sharing_policy"]
        allowed_org_units = resolved["allowed_org_units"]
        allowed_domains = resolved["allowed_domains"]
    _validate_policy_values(human_visibility, agent_sharing_policy)
    try:
        access_policy.validate_publication_scope(
            report_policy=report, knowledge_policy={
                "human_visibility": human_visibility, "agent_sharing_policy": agent_sharing_policy})
    except ValueError as e:
        raise PublicationError(str(e)) from e

    existing = get_active_knowledge_for_report(client, report_id)
    approval_date = approval_date or datetime.now(timezone.utc).isoformat()
    if existing and existing["structured_content"].get("approval_date", "") >= approval_date:
        raise PublicationError("これより新しい（または同時刻の）approved版が既にpublish済みです")

    structured_content = {
        "source_type": "external_approved_report",
        "report_title": report.get("title") or report.get("report_identity_id") or report_id,
        "syntheses_summary": [report.get("summary")] if report.get("summary") else [],
        "report_project_id": None, "run_id": None, "approval_event_id": None,
        "source_report_id": report_id, "source_file": source_file,
        "approver": approver, "approval_date": approval_date, "uploaded_by": uploaded_by,
    }

    knowledge_item = geo_knowledge_service.create_knowledge_item(
        client, knowledge_type=APPROVED_REPORT_KNOWLEDGE_TYPE, title=structured_content["report_title"],
        structured_content=structured_content, information_class="external_assessment",
        created_by=publisher, confidence="中",
        supersedes_id=existing["knowledge_item_id"] if existing else None,
    )
    geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=knowledge_item["knowledge_item_id"], reviewer_id=publisher,
        reviewer_role=publisher_role, review_decision="start_review",
        review_comment=f"外部承認済みReport（report_id={report_id}）の登録", evidence_adequacy="sufficient",
        applicability_checked=True,
    )
    geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=knowledge_item["knowledge_item_id"], reviewer_id=publisher,
        reviewer_role=publisher_role, review_decision="approve_active",
        review_comment=f"外部承認済みReport（report_id={report_id}）のPublish", evidence_adequacy="sufficient",
        applicability_checked=True,
    )
    client.update("geo_knowledge_items", {"knowledge_item_id": f"eq.{knowledge_item['knowledge_item_id']}"}, {
        "source_report_id": report_id, "human_visibility": human_visibility,
        "agent_sharing_policy": agent_sharing_policy,
        "allowed_org_units": allowed_org_units, "allowed_domains": allowed_domains,
    })
    if existing:
        geo_knowledge_service.review_knowledge_item(
            client, knowledge_item_id=existing["knowledge_item_id"], reviewer_id=publisher,
            reviewer_role=publisher_role, review_decision="supersede",
            review_comment=f"新Version knowledge_item_id={knowledge_item['knowledge_item_id']} のPublishにより供給",
            evidence_adequacy="sufficient", applicability_checked=True,
        )

    rows = client.select("geo_knowledge_items", {
        "select": "*", "knowledge_item_id": f"eq.{knowledge_item['knowledge_item_id']}", "limit": "1"})
    return {"knowledge_item_id": knowledge_item["knowledge_item_id"], "publication_status": "published",
            "knowledge_item": rows[0]}
