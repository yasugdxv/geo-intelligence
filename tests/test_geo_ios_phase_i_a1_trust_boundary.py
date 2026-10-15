"""Geopolitical Intelligence OS Phase I-A.1: Trust Boundary Closure（SEC-1/SEC-2）のテスト。

SEC-1（Approval Authorization）・SEC-2（Authenticated Identity/Role Resolution）が
実際にpayload由来のrole自己申告を拒否し、authenticated identityのみをauthorization根拠に
していることを確認する。既存geo_role_guard/geo_ios_*_serviceの単体テストパターン
（FakeSupabaseClient直呼び）を踏襲し、TestClient等の新しい依存は増やさない——API層の
関数もFastAPIのDependsを経由しない直接呼び出しで検証できる（既存test_geo_intelligence_
phase_f_trust_boundary.pyと同じ方針）。
"""
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_approval_api as approval_api  # noqa: E402
import geo_ios_approval_service as approval_service  # noqa: E402
import geo_ios_identity  # noqa: E402
import geo_ios_knowledge_publication_api as publication_api  # noqa: E402
import geo_ios_knowledge_publication_service as publication_service  # noqa: E402
import geo_ios_learning_api as learning_api  # noqa: E402
import geo_ios_learning_candidate_service as learning_service  # noqa: E402
import geo_report_studio_service as report_studio_service  # noqa: E402
import geo_role_guard  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _identity(role: str, identity_id: str = "test-identity") -> geo_ios_identity.IosIdentity:
    return geo_ios_identity.IosIdentity(identity_id=identity_id, assigned_role=role, authenticated=True)


# ---- geo_ios_identity: SEC-2 Authenticated Identity ---------------------------

def test_verify_ios_identity_missing_credential_denied():
    with pytest.raises(HTTPException) as exc_info:
        geo_ios_identity.verify_ios_identity(x_ios_credential=None)
    assert exc_info.value.status_code == 401


def test_verify_ios_identity_unknown_credential_denied(monkeypatch):
    monkeypatch.setattr(geo_ios_identity, "_IDENTITY_REGISTRY", {
        "real-cred": {"identity_id": "someone", "role": "approver", "domain": None}})
    with pytest.raises(HTTPException) as exc_info:
        geo_ios_identity.verify_ios_identity(x_ios_credential="not-a-real-credential")
    assert exc_info.value.status_code == 401


def test_verify_ios_identity_valid_credential_resolves_server_side_role(monkeypatch):
    monkeypatch.setattr(geo_ios_identity, "_IDENTITY_REGISTRY", {
        "approver-cred": {"identity_id": "eguchi", "role": "approver", "domain": None}})
    identity = geo_ios_identity.verify_ios_identity(x_ios_credential="approver-cred")
    assert identity.authenticated is True
    assert identity.assigned_role == "approver"
    assert identity.identity_id == "eguchi"


def test_load_identity_registry_rejects_unknown_role():
    # 2026-09-24変更、ユーザー指示「アカウント管理はconfigではなくDBでやろう」対応:
    # _load_identity_registryはconfig dictではなくSupabaseClientを受け取るようになった
    # （geo_ios_identities、is_active=trueのみ）。
    client = FakeSupabaseClient(tables={"geo_ios_identities": [
        {"credential": "c1", "identity_id": "x", "role": "super_admin_typo", "domain": None, "is_active": True},
    ]})
    registry = geo_ios_identity._load_identity_registry(client)
    assert registry == {}


def test_load_identity_registry_accepts_known_roles():
    client = FakeSupabaseClient(tables={"geo_ios_identities": [
        {"credential": "c1", "identity_id": "x", "role": "approver", "domain": None, "is_active": True},
    ]})
    registry = geo_ios_identity._load_identity_registry(client)
    assert registry["c1"]["role"] == "approver"


def test_check_role_mismatch_flags_divergence_but_authorization_uses_assigned_role_only():
    identity = _identity("approver")
    assert geo_ios_identity.check_role_mismatch(identity, "analyst") is not None
    assert geo_ios_identity.check_role_mismatch(identity, "approver") is None
    assert geo_ios_identity.check_role_mismatch(identity, None) is None


# ---- geo_role_guard: "approver" role added -------------------------------------

def test_geo_role_guard_has_approver_role():
    assert "approver" in geo_role_guard.ROLES


# ---- SEC-1: Approval Authorization — service-level bypass prevention ----------

def test_record_approval_event_denies_non_approver_role_even_called_directly():
    """内部関数の直接呼び出し（API層を経由しないbypass）でもservice層自体がroleを検証する。"""
    client = FakeSupabaseClient()
    for bad_role in ("analyst", "expert_reviewer", "knowledge_steward", "viewer"):
        with pytest.raises(geo_role_guard.RoleDeniedError):
            approval_service.record_approval_event(
                client, report_project_id="rp-x", approval_status="approved", approver="someone",
                approver_role=bad_role, approval_source="in_app")


def test_record_approval_event_allows_approver_role():
    client = FakeSupabaseClient()
    event = approval_service.record_approval_event(
        client, report_project_id="rp-x", approval_status="approved", approver="someone",
        approver_role="approver", approval_source="in_app")
    assert event["approval_status"] == "approved"


def test_record_approval_event_allows_administrator_override():
    client = FakeSupabaseClient()
    event = approval_service.record_approval_event(
        client, report_project_id="rp-x", approval_status="approved", approver="someone",
        approver_role="administrator", approval_source="in_app")
    assert event["approval_status"] == "approved"


def test_record_approval_event_denies_unknown_role_string():
    client = FakeSupabaseClient()
    with pytest.raises(geo_role_guard.RoleDeniedError):
        approval_service.record_approval_event(
            client, report_project_id="rp-x", approval_status="approved", approver="someone",
            approver_role="ceo", approval_source="in_app")


# ---- SEC-1: Approval Authorization — API-layer role-spoofing tests (§Security Test) ----

def _base_approval_request(approver_role_claim=None):
    return approval_api.RecordApprovalRequest(
        report_project_id="rp-spoof-test", approval_status="approved", approver="claimed-display-name",
        approver_role=approver_role_claim,
        version_snapshot={"run_id": "manual", "syntheses": [{"final_html": "<p>x</p>"}]})


def test_approve_in_app_authenticated_analyst_with_payload_approver_role_approver_denied(monkeypatch):
    monkeypatch.setattr(approval_api, "_client", FakeSupabaseClient())
    req = _base_approval_request(approver_role_claim="approver")
    identity = _identity("analyst")
    with pytest.raises(HTTPException) as exc_info:
        approval_api.approve_in_app(req, identity)
    assert exc_info.value.status_code == 403


def test_approve_in_app_authenticated_reviewer_with_payload_approver_role_knowledge_steward_denied(monkeypatch):
    monkeypatch.setattr(approval_api, "_client", FakeSupabaseClient())
    req = _base_approval_request(approver_role_claim="knowledge_steward")
    identity = _identity("expert_reviewer")
    with pytest.raises(HTTPException) as exc_info:
        approval_api.approve_in_app(req, identity)
    assert exc_info.value.status_code == 403


def test_approve_in_app_authenticated_approver_with_payload_role_analyst_allowed_and_uses_authenticated_role(monkeypatch):
    """authenticated roleが正であることの決定的証拠: payloadがanalystを騙っても、実際にDBへ
    書き込まれるのは認証済みroleそのものではなくapprover_role（承認可否判定に使われた値）が
    authorizationを通過したことであり、そのapprover_roleはidentity.assigned_role("approver")
    から来ている（req.approver_roleの"analyst"は一切使われない）。

    Review Platform P0-3 Remediation後: version_snapshotをclientから渡してもGateを
    スキップできなくなったため、Legacy(Report Studio)経路が実際にapprovable状態
    （Collection Run一致＋result==approvedのReview Cycleが存在）であることをFakeデータで
    再現した上でこのテストを実行する。"""
    client = FakeSupabaseClient(tables={
        "geo_report_collection_runs": [
            {"run_id": "run-spoof-1", "project_id": "rp-spoof-test", "started_at": "2026-01-01T00:00:00Z"},
        ],
        "geo_ios_review_cycles": [
            {"review_cycle_id": "cyc-spoof-1", "report_project_id": "rp-spoof-test", "round_number": 1,
             "reviewed_version_label": "run:run-spoof-1", "result": "approved", "status": "completed"},
        ],
    })
    monkeypatch.setattr(approval_api, "_client", client)
    req = _base_approval_request(approver_role_claim="analyst")
    identity = _identity("approver", identity_id="real-approver")
    result = approval_api.approve_in_app(req, identity)
    assert result["approval_status"] == "approved"
    assert result.get("_role_mismatch_warning")  # payloadとの食い違いはaudit用に記録される
    assert "analyst" in result["_role_mismatch_warning"] and "approver" in result["_role_mismatch_warning"]


def test_approve_via_upload_role_spoofing_denied(monkeypatch):
    monkeypatch.setattr(approval_api, "_client", FakeSupabaseClient())
    req = approval_api.RecordUploadApprovalRequest(
        report_project_id="rp-upload-spoof", approver="claimed", approver_role="approver")
    identity = _identity("analyst")
    with pytest.raises(HTTPException) as exc_info:
        approval_api.approve_via_upload(req, identity)
    assert exc_info.value.status_code == 403


# ---- §4 Learning Candidate authorization regression + spoofing ----------------

def _seed_approved_candidate(client):
    return client.insert("geo_ios_learning_candidates", [{
        "candidate_type": "report_design_pattern", "observation": "o", "generalized_rule": "g",
        "extraction_model": "m", "status": "candidate",
    }])[0]


def test_approve_candidate_via_api_denies_non_knowledge_steward_authenticated_role(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(learning_api, "_client", client)
    candidate = _seed_approved_candidate(client)
    req = learning_api.ApproveCandidateRequest(reviewed_by="x", reviewer_role="knowledge_steward")
    identity = _identity("analyst")  # payloadは正しいroleを騙るが、authenticated roleはanalyst
    with pytest.raises(HTTPException) as exc_info:
        learning_api.approve_candidate(candidate["candidate_id"], req, identity)
    assert exc_info.value.status_code == 403


def test_approve_candidate_via_api_allows_authenticated_knowledge_steward(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(learning_api, "_client", client)
    candidate = _seed_approved_candidate(client)
    req = learning_api.ApproveCandidateRequest(reviewed_by="x")
    identity = _identity("knowledge_steward")
    result = learning_api.approve_candidate(candidate["candidate_id"], req, identity)
    assert result["status"] == "approved"


# ---- §4 Publication authorization regression + spoofing -----------------------

def test_publish_report_studio_via_api_denies_non_knowledge_steward_authenticated_role(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(publication_api, "_client", client)
    project = report_studio_service.get_or_create_project(client, "PRJ-SEC1", "テスト")
    event = approval_service.record_approval_event(
        client, report_project_id=project["project_id"], approval_status="approved", approver="a",
        approver_role="approver", approval_source="in_app", version_label="run:r1",
        version_snapshot={"run_id": "r1", "syntheses": [{"final_html": "<p>x</p>", "final_status": "PASS"}]})
    req = publication_api.PublishReportStudioRequest(
        report_project_id=project["project_id"], approval_event_id=event["approval_event_id"],
        publisher="p", publisher_role="knowledge_steward", human_visibility="company",
        agent_sharing_policy="geopolitics_only")
    identity = _identity("approver")  # approverはPublishの権限ではない(PUBLISH_ROLES=knowledge_steward)
    with pytest.raises(HTTPException) as exc_info:
        publication_api.publish_report_studio(req, identity)
    assert exc_info.value.status_code == 403


def test_publish_report_studio_via_api_allows_authenticated_knowledge_steward(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(publication_api, "_client", client)
    project = report_studio_service.get_or_create_project(client, "PRJ-SEC1B", "テスト")
    event = approval_service.record_approval_event(
        client, report_project_id=project["project_id"], approval_status="approved", approver="a",
        approver_role="approver", approval_source="in_app", version_label="run:r1",
        version_snapshot={"run_id": "r1", "syntheses": [{"final_html": "<p>x</p>", "final_status": "PASS"}]})
    req = publication_api.PublishReportStudioRequest(
        report_project_id=project["project_id"], approval_event_id=event["approval_event_id"],
        publisher="p", human_visibility="company", agent_sharing_policy="geopolitics_only")
    identity = _identity("knowledge_steward")
    result = publication_api.publish_report_studio(req, identity)
    assert result["knowledge_item_id"]
