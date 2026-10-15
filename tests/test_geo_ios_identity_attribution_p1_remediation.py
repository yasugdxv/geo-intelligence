"""P1 Identity Attribution Closure（ユーザー指示、2026-09-23、Mechanical E2E Pilot後の
Known P1対応）の回帰テスト。

対象: approver（Approval Route A）/ reviewed_by（Learning Candidate Gate #1 approve/reject）/
created_by（Knowledge Item promote、Review Comment）/ publisher（Publication Route A）の
5つのdisplay-name系フィールドが、request payload由来の自己申告値ではなく認証済み
identity.identity_idへ正しくbindingされていることを確認する。Route B
（approve_via_upload/publish_external、外部で既に行われた操作の代理登録）は意図的に
対象外——そちらはpayload値こそが正しいattributionであるため、変更していないことも
併せて確認する。

既存tests/test_geo_ios_review_platform_p0_remediation.pyの流儀（FakeSupabaseClient直呼び、
TestClient等の新しい依存は増やさない）を踏襲する。
"""
import geo_ios_approval_api as approval_api
import geo_ios_identity
import geo_ios_knowledge_publication_api as publication_api
import geo_ios_learning_api as learning_api
import geo_ios_review_api as review_api
import geo_report_studio_service as report_studio_service
from tests._fakes import FakeSupabaseClient


def _identity(role: str, identity_id: str = "real-identity") -> geo_ios_identity.IosIdentity:
    return geo_ios_identity.IosIdentity(
        identity_id=identity_id, assigned_role=role, domain=None, authenticated=True)


# ---- geo_ios_identity.check_identity_mismatch（単体） -----------------------------

def test_check_identity_mismatch_flags_divergence():
    identity = _identity("approver", identity_id="real-approver")
    assert geo_ios_identity.check_identity_mismatch(identity, "claimed-someone-else") is not None
    assert geo_ios_identity.check_identity_mismatch(identity, "real-approver") is None
    assert geo_ios_identity.check_identity_mismatch(identity, None) is None


# ---- Approval Route A: approverはauthenticated identityへbindingされる -----------

def test_approve_in_app_binds_approver_to_authenticated_identity_not_payload(monkeypatch):
    client = FakeSupabaseClient(tables={
        "geo_report_collection_runs": [
            {"run_id": "run-p1", "project_id": "rp-p1", "started_at": "2026-01-01T00:00:00Z"},
        ],
        "geo_ios_review_cycles": [
            {"review_cycle_id": "cyc-p1", "report_project_id": "rp-p1", "round_number": 1,
             "reviewed_version_label": "run:run-p1", "result": "approved", "status": "completed"},
        ],
    })
    monkeypatch.setattr(approval_api, "_client", client)
    req = approval_api.RecordApprovalRequest(
        report_project_id="rp-p1", approval_status="approved", approver="claimed-display-name",
        version_snapshot={"run_id": "manual", "syntheses": [{"final_html": "<p>x</p>"}]})
    identity = _identity("approver", identity_id="real-approver-id")
    result = approval_api.approve_in_app(req, identity)
    assert result["approver"] == "real-approver-id"
    assert result["approver"] != "claimed-display-name"
    assert result.get("_approver_identity_mismatch_warning")
    assert "claimed-display-name" in result["_approver_identity_mismatch_warning"]


def test_approve_via_upload_route_b_keeps_payload_approver_unchanged(monkeypatch):
    """Route B（Upload）はP1修正の対象外——別プロセスで承認済みの内容を代理登録するため、
    payload由来のapproverこそが正しいattributionであり、呼び出し者のidentityで上書きしては
    ならない（geo_ios_approval_service.approve_via_uploadのdocstring参照）。"""
    client = FakeSupabaseClient()
    monkeypatch.setattr(approval_api, "_client", client)
    req = approval_api.RecordUploadApprovalRequest(
        report_project_id="rp-upload-p1", approver="external-approver-name")
    identity = _identity("approver", identity_id="uploader-id")
    result = approval_api.approve_via_upload(req, identity)
    assert result["approver"] == "external-approver-name"


# ---- Learning Candidate Gate #1: reviewed_byはauthenticated identityへbindingされる ----

def _seed_candidate(client):
    return client.insert("geo_ios_learning_candidates", [{
        "candidate_type": "report_design_pattern", "observation": "o", "generalized_rule": "g",
        "extraction_model": "m", "status": "candidate",
    }])[0]


def test_approve_candidate_binds_reviewed_by_to_authenticated_identity_not_payload(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(learning_api, "_client", client)
    candidate = _seed_candidate(client)
    req = learning_api.ApproveCandidateRequest(reviewed_by="claimed-steward-name")
    identity = _identity("knowledge_steward", identity_id="real-steward-id")
    result = learning_api.approve_candidate(candidate["candidate_id"], req, identity)
    assert result["reviewed_by"] == "real-steward-id"
    assert result.get("_reviewed_by_identity_mismatch_warning")


def test_reject_candidate_binds_reviewed_by_to_authenticated_identity_not_payload(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(learning_api, "_client", client)
    candidate = _seed_candidate(client)
    req = learning_api.RejectCandidateRequest(reviewed_by="claimed-steward-name")
    identity = _identity("knowledge_steward", identity_id="real-steward-id")
    result = learning_api.reject_candidate(candidate["candidate_id"], req, identity)
    assert result["reviewed_by"] == "real-steward-id"
    assert result.get("_reviewed_by_identity_mismatch_warning")


# ---- Learning Candidate Promote: created_byはauthenticated identityへbindingされる ----

def test_promote_to_knowledge_item_binds_created_by_to_authenticated_identity_not_payload(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(learning_api, "_client", client)
    candidate = client.insert("geo_ios_learning_candidates", [{
        "candidate_type": "evidence_rule", "observation": "o", "generalized_rule": "g",
        "extraction_model": "m", "status": "approved",
        "trigger_context": {"reasoning_step": "evidence_weighting"},
    }])[0]
    req = learning_api.PromoteCandidateRequest(created_by="claimed-someone-else")
    identity = _identity("knowledge_steward", identity_id="real-steward-id")
    result = learning_api.promote_to_knowledge_item(candidate["candidate_id"], req, identity)
    assert result["created_by"] == "real-steward-id"
    assert result.get("_created_by_identity_mismatch_warning")


# ---- Publication Route A: publisherはauthenticated identityへbindingされる ----------

def test_publish_report_studio_binds_publisher_to_authenticated_identity_not_payload(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(publication_api, "_client", client)
    project = report_studio_service.get_or_create_project(client, "PRJ-P1", "テスト")
    event = client.insert("geo_ios_approval_events", [{
        "report_project_id": project["project_id"], "approval_status": "approved",
        "approver": "a", "approval_source": "in_app", "version_label": "run:r1",
        "version_snapshot": {"run_id": "r1", "syntheses": [{"final_html": "<p>x</p>", "final_status": "PASS"}]},
        "publication_status": "not_published",
    }])[0]
    req = publication_api.PublishReportStudioRequest(
        report_project_id=project["project_id"], approval_event_id=event["approval_event_id"],
        publisher="claimed-someone-else", human_visibility="company",
        agent_sharing_policy="geopolitics_only")
    identity = _identity("knowledge_steward", identity_id="real-steward-id")
    result = publication_api.publish_report_studio(req, identity)
    assert result["knowledge_item"]["created_by"] == "real-steward-id"
    assert result.get("_publisher_identity_mismatch_warning")


# ---- Review Comment: created_byはauthenticated identityへbindingされる -------------

def _seed_cycle_client(**overrides):
    row = {
        "review_cycle_id": "cyc-p1", "report_project_id": "rp-1", "round_number": 1,
        "reviewer": "real-reviewer-id", "status": "in_review", "result": None,
        "requested_by": "requester-id",
    }
    row.update(overrides)
    return FakeSupabaseClient(tables={"geo_ios_review_cycles": [row]})


def test_add_comment_binds_created_by_to_authenticated_identity_not_payload(monkeypatch):
    client = _seed_cycle_client()
    monkeypatch.setattr(review_api, "_client", client)
    req = review_api.AddCommentRequest(comment="c", created_by="claimed-someone-else")
    identity = _identity("expert_reviewer", identity_id="real-reviewer-id")
    result = review_api.add_comment("cyc-p1", req, identity)
    assert result["created_by"] == "real-reviewer-id"
    assert result.get("_created_by_identity_mismatch_warning")


def test_add_comment_no_mismatch_warning_when_payload_matches_identity(monkeypatch):
    client = _seed_cycle_client()
    monkeypatch.setattr(review_api, "_client", client)
    req = review_api.AddCommentRequest(comment="c", created_by="real-reviewer-id")
    identity = _identity("expert_reviewer", identity_id="real-reviewer-id")
    result = review_api.add_comment("cyc-p1", req, identity)
    assert result["created_by"] == "real-reviewer-id"
    assert "_created_by_identity_mismatch_warning" not in result
