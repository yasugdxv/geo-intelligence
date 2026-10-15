"""Geopolitical Intelligence OS Phase E: Knowledge Publication Serviceのテスト。
§25 Publication/Versionの要件（approved→knowledge、draft/review-only→reject、
v1 active→v2 approved→v1 superseded/v2 active）を検証する。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_approval_service as approval_service  # noqa: E402
import geo_ios_knowledge_publication_service as publication_service  # noqa: E402
import geo_report_studio_service as report_studio_service  # noqa: E402
import geo_role_guard  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

PUBLISHER = "steward-1"
PUBLISHER_ROLE = "knowledge_steward"


def _seed_project_with_approval(client, *, project_code="PRJ-E1", approval_status="approved",
                                 approval_date=None) -> tuple:
    project = report_studio_service.get_or_create_project(client, project_code, "テストレポート")
    event = approval_service.record_approval_event(
        client, report_project_id=project["project_id"], approval_status=approval_status,
        approver="expert-a", approver_role="approver", approval_source="in_app", version_label="run:r1",
        version_snapshot={"run_id": "r1", "syntheses": [{"final_html": "<p>本文</p>", "final_status": "PASS"}]})
    if approval_date:
        client.update("geo_ios_approval_events", {"approval_event_id": f"eq.{event['approval_event_id']}"},
                      {"approval_date": approval_date})
        event = approval_service.get_approval_event(client, event["approval_event_id"])
    return project, event


def test_publish_rejects_insufficient_role():
    client = FakeSupabaseClient()
    project, event = _seed_project_with_approval(client)
    with pytest.raises(geo_role_guard.RoleDeniedError):
        publication_service.publish_approved_report(
            client, report_project_id=project["project_id"], approval_event_id=event["approval_event_id"],
            publisher="analyst-1", publisher_role="analyst", human_visibility="company",
            agent_sharing_policy="geopolitics_only")
    # 権限不足で失敗した場合、Knowledgeは一切作られない
    assert client.tables.get("geo_knowledge_items", []) == []


def test_publish_rejects_non_approved_event():
    client = FakeSupabaseClient()
    project, event = _seed_project_with_approval(client, approval_status="rejected")
    with pytest.raises(publication_service.PublicationError):
        publication_service.publish_approved_report(
            client, report_project_id=project["project_id"], approval_event_id=event["approval_event_id"],
            publisher=PUBLISHER, publisher_role=PUBLISHER_ROLE, human_visibility="company",
            agent_sharing_policy="geopolitics_only")
    assert client.tables.get("geo_knowledge_items", []) == []


def test_publish_rejects_unknown_project():
    client = FakeSupabaseClient()
    project, event = _seed_project_with_approval(client)
    with pytest.raises(publication_service.PublicationError):
        publication_service.publish_approved_report(
            client, report_project_id="does-not-exist", approval_event_id=event["approval_event_id"],
            publisher=PUBLISHER, publisher_role=PUBLISHER_ROLE, human_visibility="company",
            agent_sharing_policy="geopolitics_only")


def test_publish_rejects_invalid_visibility_values():
    client = FakeSupabaseClient()
    project, event = _seed_project_with_approval(client)
    with pytest.raises(publication_service.PublicationError):
        publication_service.publish_approved_report(
            client, report_project_id=project["project_id"], approval_event_id=event["approval_event_id"],
            publisher=PUBLISHER, publisher_role=PUBLISHER_ROLE, human_visibility="not_a_real_level",
            agent_sharing_policy="geopolitics_only")
    assert client.tables.get("geo_knowledge_items", []) == []


def test_publish_success_creates_active_knowledge_item_with_provenance():
    client = FakeSupabaseClient()
    project, event = _seed_project_with_approval(client)
    result = publication_service.publish_approved_report(
        client, report_project_id=project["project_id"], approval_event_id=event["approval_event_id"],
        publisher=PUBLISHER, publisher_role=PUBLISHER_ROLE, human_visibility="department",
        agent_sharing_policy="allowed_cross_domain")

    item = result["knowledge_item"]
    assert item["knowledge_type"] == "approved_report"
    assert item["status"] == "active"
    assert item["retrieval_status"] == "available"
    assert item["source_report_project_id"] == project["project_id"]
    assert item["source_approval_event_id"] == event["approval_event_id"]
    assert item["human_visibility"] == "department"
    assert item["agent_sharing_policy"] == "allowed_cross_domain"

    updated_event = approval_service.get_approval_event(client, event["approval_event_id"])
    assert updated_event["publication_status"] == "published"
    assert updated_event["published_knowledge_item_id"] == item["knowledge_item_id"]


def test_publish_same_event_twice_rejected():
    client = FakeSupabaseClient()
    project, event = _seed_project_with_approval(client)
    publication_service.publish_approved_report(
        client, report_project_id=project["project_id"], approval_event_id=event["approval_event_id"],
        publisher=PUBLISHER, publisher_role=PUBLISHER_ROLE, human_visibility="company",
        agent_sharing_policy="geopolitics_only")
    with pytest.raises(publication_service.PublicationError):
        publication_service.publish_approved_report(
            client, report_project_id=project["project_id"], approval_event_id=event["approval_event_id"],
            publisher=PUBLISHER, publisher_role=PUBLISHER_ROLE, human_visibility="company",
            agent_sharing_policy="geopolitics_only")


def test_v2_approved_publish_supersedes_v1_and_only_v2_active():
    client = FakeSupabaseClient()
    project, event_v1 = _seed_project_with_approval(client, approval_date="2026-09-16T00:00:00+00:00")
    result_v1 = publication_service.publish_approved_report(
        client, report_project_id=project["project_id"], approval_event_id=event_v1["approval_event_id"],
        publisher=PUBLISHER, publisher_role=PUBLISHER_ROLE, human_visibility="company",
        agent_sharing_policy="geopolitics_only")
    k1_id = result_v1["knowledge_item_id"]

    # v2: 新しいRun/Approval（v1より後の承認日時）
    event_v2 = approval_service.record_approval_event(
        client, report_project_id=project["project_id"], approval_status="approved", approver="expert-a",
        approver_role="approver", approval_source="in_app", version_label="run:r2",
        version_snapshot={"run_id": "r2", "syntheses": [{"final_html": "<p>改訂版</p>", "final_status": "PASS"}]})
    client.update("geo_ios_approval_events", {"approval_event_id": f"eq.{event_v2['approval_event_id']}"},
                  {"approval_date": "2026-09-17T00:00:00+00:00"})
    event_v2 = approval_service.get_approval_event(client, event_v2["approval_event_id"])

    result_v2 = publication_service.publish_approved_report(
        client, report_project_id=project["project_id"], approval_event_id=event_v2["approval_event_id"],
        publisher=PUBLISHER, publisher_role=PUBLISHER_ROLE, human_visibility="company",
        agent_sharing_policy="geopolitics_only")
    k2_id = result_v2["knowledge_item_id"]

    k1 = next(r for r in client.tables["geo_knowledge_items"] if r["knowledge_item_id"] == k1_id)
    k2 = next(r for r in client.tables["geo_knowledge_items"] if r["knowledge_item_id"] == k2_id)
    assert k1["status"] == "superseded"
    assert k2["status"] == "active"
    assert k2["supersedes_id"] == k1_id

    # v1は物理削除されない（Audit目的で保持、§8）
    assert k1_id in [r["knowledge_item_id"] for r in client.tables["geo_knowledge_items"]]
    active_only = publication_service.get_active_knowledge_for_report_project(client, project["project_id"])
    assert active_only["knowledge_item_id"] == k2_id  # 通常Retrievalはv2のみ


def test_publish_older_approval_after_newer_already_published_rejected():
    client = FakeSupabaseClient()
    project, event_new = _seed_project_with_approval(
        client, project_code="PRJ-E2", approval_date="2026-09-17T00:00:00+00:00")
    publication_service.publish_approved_report(
        client, report_project_id=project["project_id"], approval_event_id=event_new["approval_event_id"],
        publisher=PUBLISHER, publisher_role=PUBLISHER_ROLE, human_visibility="company",
        agent_sharing_policy="geopolitics_only")

    event_old = approval_service.record_approval_event(
        client, report_project_id=project["project_id"], approval_status="approved", approver="expert-a",
        approver_role="approver", approval_source="in_app", version_label="run:r0")
    client.update("geo_ios_approval_events", {"approval_event_id": f"eq.{event_old['approval_event_id']}"},
                  {"approval_date": "2026-09-15T00:00:00+00:00"})
    event_old = approval_service.get_approval_event(client, event_old["approval_event_id"])

    with pytest.raises(publication_service.PublicationError):
        publication_service.publish_approved_report(
            client, report_project_id=project["project_id"], approval_event_id=event_old["approval_event_id"],
            publisher=PUBLISHER, publisher_role=PUBLISHER_ROLE, human_visibility="company",
            agent_sharing_policy="geopolitics_only")


# ---- Route B（外部Upload、§20） ---------------------------------------------

def test_publish_external_requires_approver():
    client = FakeSupabaseClient()
    report = client.insert("geo_reports", [{"title": "外部レポート", "document_id": "doc-1"}])[0]
    with pytest.raises(publication_service.PublicationError):
        publication_service.publish_external_approved_report(
            client, report_id=report["report_id"], publisher=PUBLISHER, publisher_role=PUBLISHER_ROLE,
            human_visibility="company", agent_sharing_policy="geopolitics_only", approver="")


def test_publish_external_success_and_supersession():
    client = FakeSupabaseClient()
    report = client.insert("geo_reports", [{"title": "外部レポートv1", "document_id": "doc-1"}])[0]
    result1 = publication_service.publish_external_approved_report(
        client, report_id=report["report_id"], publisher=PUBLISHER, publisher_role=PUBLISHER_ROLE,
        human_visibility="company", agent_sharing_policy="allowed_cross_domain", approver="manager-1",
        approval_date="2026-09-16T00:00:00+00:00", uploaded_by="uploader-1")
    assert result1["knowledge_item"]["status"] == "active"
    assert result1["knowledge_item"]["source_report_id"] == report["report_id"]

    result2 = publication_service.publish_external_approved_report(
        client, report_id=report["report_id"], publisher=PUBLISHER, publisher_role=PUBLISHER_ROLE,
        human_visibility="company", agent_sharing_policy="allowed_cross_domain", approver="manager-1",
        approval_date="2026-09-17T00:00:00+00:00", uploaded_by="uploader-1")

    k1 = next(r for r in client.tables["geo_knowledge_items"]
              if r["knowledge_item_id"] == result1["knowledge_item_id"])
    assert k1["status"] == "superseded"
    assert result2["knowledge_item"]["status"] == "active"


# ---- Phase 2E §7: Report → Knowledge inheritance（scope escalation禁止） -----------

def test_publish_external_rejects_scope_wider_than_source_report():
    """DEPARTMENT_ONLY（human_visibility=custom, agent_sharing_policy=none）のReportを、
    GROUP_SHAREABLE（human_visibility=company）としてPublishしようとすると拒否される。"""
    client = FakeSupabaseClient()
    report = client.insert("geo_reports", [{
        "title": "部内限りレポート", "document_id": "doc-1",
        "human_visibility": "custom", "agent_sharing_policy": "none",
        "allowed_org_units": ["geopolitics"], "allowed_domains": [],
    }])[0]
    with pytest.raises(publication_service.PublicationError, match="広い"):
        publication_service.publish_external_approved_report(
            client, report_id=report["report_id"], publisher=PUBLISHER, publisher_role=PUBLISHER_ROLE,
            confidentiality_label="GROUP_SHAREABLE", approver="manager-1")


def test_publish_external_allows_scope_equal_or_narrower_than_source_report():
    """同じDEPARTMENT_ONLY、またはより狭い範囲へのPublishは許可される。"""
    client = FakeSupabaseClient()
    report = client.insert("geo_reports", [{
        "title": "グループ配信可レポート", "document_id": "doc-1",
        "human_visibility": "company", "agent_sharing_policy": "custom", "allowed_domains": [],
    }])[0]
    result = publication_service.publish_external_approved_report(
        client, report_id=report["report_id"], publisher=PUBLISHER, publisher_role=PUBLISHER_ROLE,
        confidentiality_label="DEPARTMENT_ONLY", approver="manager-1")
    assert result["knowledge_item"]["status"] == "active"


def test_publish_external_via_confidentiality_label_sets_allowed_lists():
    """confidentiality_label経由のPublishは、allowed_org_units/allowed_domainsも
    Knowledge Item側へ書き込む（human_visibility/agent_sharing_policyだけでなく）。
    DEPARTMENT_ONLYはSource Reportのowner_departmentをそのまま引き継ぐ。"""
    client = FakeSupabaseClient()
    report = client.insert("geo_reports", [{
        "title": "部内限りレポート", "document_id": "doc-1", "owner_department": "geopolitics",
        "human_visibility": "custom", "agent_sharing_policy": "none",
        "allowed_org_units": ["geopolitics"], "allowed_domains": [],
    }])[0]
    result = publication_service.publish_external_approved_report(
        client, report_id=report["report_id"], publisher=PUBLISHER, publisher_role=PUBLISHER_ROLE,
        confidentiality_label="DEPARTMENT_ONLY", approver="manager-1")
    item = result["knowledge_item"]
    assert item["human_visibility"] == "custom"
    assert item["allowed_org_units"] == ["geopolitics"]
