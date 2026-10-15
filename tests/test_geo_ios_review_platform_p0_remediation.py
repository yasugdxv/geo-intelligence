"""Review Platform P0 Remediation（ユーザー指示、2026-09-22、Post-Freeze Review Platform
Audit Finding P0-1〜P0-3対応）の回帰テスト。

既存tests/test_geo_ios_phase_i_a1_trust_boundary.pyの流儀（FakeSupabaseClient直呼び、
TestClient等の新しい依存は増やさない）を踏襲する——API層のFastAPI関数を、Depends解決後の
identityを手渡しで直接呼ぶ形で検証する。

対象:
- P0-1: geo_ios_review_api.py 全endpointへのgeo_ios_identity認証境界の統合、
  submit_review_findingsの割当reviewer本人拘束。
- P0-2: Legacy（report_studio mode）Approval経路にReview Cycle Gateが無かった問題
  （geo_ios_report_studio_adapter.get_approvable_snapshot新設）。
- P0-3: client供給のversion_snapshotがGateロジック自体をスキップできた問題
  （geo_ios_approval_api.approve_in_appの無条件Gate化）。
- 既存の健全な経路（publish_approved_reportのapproval_status要求、learning extractionの
  rejected/open除外）が今回の変更後も維持されていることの回帰確認。
"""
import inspect

import pytest
from fastapi import HTTPException
from fastapi.params import Depends as DependsMarker

import geo_ios_approval_api as approval_api
import geo_ios_identity
import geo_ios_learning_extraction_service as extraction_service
import geo_ios_report_studio_adapter as report_studio_adapter
import geo_ios_review_api as review_api
from tests._fakes import FakeSupabaseClient


def _identity(role: str, identity_id: str = "test-identity") -> geo_ios_identity.IosIdentity:
    return geo_ios_identity.IosIdentity(
        identity_id=identity_id, assigned_role=role, domain=None, authenticated=True)


def _requires_ios_identity(fn) -> bool:
    """fnのシグネチャに、geo_ios_identity.verify_ios_identityへのDependsが実在するかを確認する
    （FastAPIがルーティング時にこのDependsを解決し、認証なしのリクエストを401で拒否する
    ——実際のHTTPリクエストを起こさずに済む、既存テスト方針に沿った配線確認）。"""
    for param in inspect.signature(fn).parameters.values():
        default = param.default
        if isinstance(default, DependsMarker) and default.dependency is geo_ios_identity.verify_ios_identity:
            return True
    return False


# ---- P0-1: Review router全体の認証境界（配線確認） ---------------------------------

@pytest.mark.parametrize("fn", [
    review_api.request_review_cycle,
    review_api.start_review,
    review_api.submit_review_findings,
    review_api.record_author_response,
    review_api.add_comment,
    review_api.respond_to_comment,
])
def test_review_write_endpoints_require_ios_identity(fn):
    assert _requires_ios_identity(fn), (
        f"{fn.__name__} に Depends(geo_ios_identity.verify_ios_identity) が無い"
        "（P0-1 Remediationが後退している）")


def test_verify_ios_identity_rejects_missing_credential():
    """unauthenticated review creation → reject（配線確認の裏付け、verify_ios_identity自体の挙動）。"""
    with pytest.raises(HTTPException) as exc_info:
        geo_ios_identity.verify_ios_identity(x_ios_credential=None)
    assert exc_info.value.status_code == 401


def test_verify_ios_identity_rejects_unknown_credential():
    with pytest.raises(HTTPException) as exc_info:
        geo_ios_identity.verify_ios_identity(x_ios_credential="not-a-real-credential")
    assert exc_info.value.status_code == 401


# ---- P0-1: submit_review_findingsのreviewer本人拘束 --------------------------------

def _seed_cycle_client(**overrides):
    row = {
        "review_cycle_id": "cyc-1", "report_project_id": "rp-1", "round_number": 1,
        "reviewer": "real-reviewer-id", "status": "in_review", "result": None,
        "requested_by": "requester-id",
    }
    row.update(overrides)
    return FakeSupabaseClient(tables={"geo_ios_review_cycles": [row]})


def test_submit_review_findings_forged_reviewer_field_rejected(monkeypatch):
    """forged result=approved / self-declared reviewer → reject: request bodyのreviewer文字列
    が本物のreviewerを騙っても、authenticated identityが別人ならば拒否される。"""
    client = _seed_cycle_client()
    monkeypatch.setattr(review_api, "_client", client)
    req = review_api.SubmitFindingsRequest(
        findings={"critical_findings": []}, result="approved", reviewer="real-reviewer-id")
    attacker_identity = _identity("expert_reviewer", identity_id="attacker-id")
    with pytest.raises(HTTPException) as exc_info:
        review_api.submit_review_findings("cyc-1", req, attacker_identity)
    assert exc_info.value.status_code == 403


def test_submit_review_findings_wrong_role_rejected(monkeypatch):
    client = _seed_cycle_client()
    monkeypatch.setattr(review_api, "_client", client)
    req = review_api.SubmitFindingsRequest(
        findings={}, result="approved", reviewer="real-reviewer-id")
    wrong_role_identity = _identity("analyst", identity_id="real-reviewer-id")
    with pytest.raises(HTTPException) as exc_info:
        review_api.submit_review_findings("cyc-1", req, wrong_role_identity)
    assert exc_info.value.status_code == 403


def test_submit_review_findings_assigned_reviewer_passes(monkeypatch):
    """valid authenticated approved review → pass。"""
    client = _seed_cycle_client()
    monkeypatch.setattr(review_api, "_client", client)
    req = review_api.SubmitFindingsRequest(
        findings={"critical_findings": []}, result="approved", reviewer="ignored-payload-value")
    real_identity = _identity("expert_reviewer", identity_id="real-reviewer-id")
    result = review_api.submit_review_findings("cyc-1", req, real_identity)
    assert result["result"] == "approved"
    assert result["status"] == "completed"
    # authenticated identityが記録される。request body由来の値は使わない。
    assert result["reviewer"] == "real-reviewer-id"


def test_submit_review_findings_administrator_override_allowed(monkeypatch):
    client = _seed_cycle_client()
    monkeypatch.setattr(review_api, "_client", client)
    req = review_api.SubmitFindingsRequest(findings={}, result="approved", reviewer="anyone")
    admin_identity = _identity("administrator", identity_id="admin-1")
    result = review_api.submit_review_findings("cyc-1", req, admin_identity)
    assert result["result"] == "approved"


# ---- P0-2 / P0-3: Approval Gate（Legacy経路のGate、version_snapshotバイパス） -------

def _seed_approval_client(*, run_id="run-1", cycle_label=None, cycle_result=None, extra_run=None):
    tables = {
        "geo_report_collection_runs": [
            {"run_id": run_id, "project_id": "rp-1", "started_at": "2026-01-01T00:00:00Z"},
        ],
    }
    if extra_run:
        tables["geo_report_collection_runs"].append(extra_run)
    if cycle_label is not None:
        tables["geo_ios_review_cycles"] = [{
            "review_cycle_id": "cyc-1", "report_project_id": "rp-1", "round_number": 1,
            "reviewed_version_label": cycle_label, "result": cycle_result, "status": "completed",
        }]
    return FakeSupabaseClient(tables=tables)


def test_approve_in_app_no_review_cycle_rejected(monkeypatch):
    """no-review approval → reject（Legacy経路、P0-2）。"""
    client = _seed_approval_client()  # geo_ios_review_cyclesを一切seedしない
    monkeypatch.setattr(approval_api, "_client", client)
    req = approval_api.RecordApprovalRequest(
        report_project_id="rp-1", approval_status="approved", approver="someone")
    identity = _identity("approver")
    with pytest.raises(HTTPException) as exc_info:
        approval_api.approve_in_app(req, identity)
    assert exc_info.value.status_code == 400


def test_approve_in_app_rejected_review_not_approvable(monkeypatch):
    """rejected reviewからpublication → reject（承認自体がresult=rejectedでは通らない、P0-2）。"""
    client = _seed_approval_client(cycle_label="run:run-1", cycle_result="rejected")
    monkeypatch.setattr(approval_api, "_client", client)
    req = approval_api.RecordApprovalRequest(
        report_project_id="rp-1", approval_status="approved", approver="someone")
    identity = _identity("approver")
    with pytest.raises(HTTPException) as exc_info:
        approval_api.approve_in_app(req, identity)
    assert exc_info.value.status_code == 400


def test_approve_in_app_revision_requested_review_not_approvable(monkeypatch):
    client = _seed_approval_client(cycle_label="run:run-1", cycle_result="revision_requested")
    monkeypatch.setattr(approval_api, "_client", client)
    req = approval_api.RecordApprovalRequest(
        report_project_id="rp-1", approval_status="approved", approver="someone")
    identity = _identity("approver")
    with pytest.raises(HTTPException) as exc_info:
        approval_api.approve_in_app(req, identity)
    assert exc_info.value.status_code == 400


def test_approve_in_app_stale_version_review_rejected(monkeypatch):
    """review済み旧versionのapproval → reject: Review Cycleが承認したのは古いrunで、
    現在のCollection Runはそれより新しいものへ進んでいる場合、拒否される（P0-2）。"""
    client = _seed_approval_client(
        run_id="run-old", cycle_label="run:run-old", cycle_result="approved",
        extra_run={"run_id": "run-new", "project_id": "rp-1", "started_at": "2026-02-01T00:00:00Z"})
    monkeypatch.setattr(approval_api, "_client", client)
    req = approval_api.RecordApprovalRequest(
        report_project_id="rp-1", approval_status="approved", approver="someone")
    identity = _identity("approver")
    with pytest.raises(HTTPException) as exc_info:
        approval_api.approve_in_app(req, identity)
    assert exc_info.value.status_code == 400


def test_approve_in_app_client_supplied_version_snapshot_does_not_bypass_gate(monkeypatch):
    """version_snapshot only approval → reject（P0-3の核心回帰テスト）: クライアントが
    version_snapshot/version_labelを明示的に送っても、実際のReview Cycleが存在しない/
    approvedでない限り拒否される——以前はここでGateロジック自体がスキップされていた。"""
    client = _seed_approval_client()  # 承認可能なReview Cycleを一切seedしない
    monkeypatch.setattr(approval_api, "_client", client)
    req = approval_api.RecordApprovalRequest(
        report_project_id="rp-1", approval_status="approved", approver="attacker",
        version_label="forged-label", version_snapshot={"forged": "content", "run_id": "fake"})
    identity = _identity("approver")
    with pytest.raises(HTTPException) as exc_info:
        approval_api.approve_in_app(req, identity)
    assert exc_info.value.status_code == 400


def test_approve_in_app_valid_approved_review_passes(monkeypatch):
    """valid authenticated approved review → pass（Legacy経路）。"""
    client = _seed_approval_client(cycle_label="run:run-1", cycle_result="approved")
    monkeypatch.setattr(approval_api, "_client", client)
    req = approval_api.RecordApprovalRequest(
        report_project_id="rp-1", approval_status="approved", approver="someone")
    identity = _identity("approver")
    result = approval_api.approve_in_app(req, identity)
    assert result["approval_status"] == "approved"


def test_report_studio_adapter_get_approvable_snapshot_rejects_no_cycle():
    client = FakeSupabaseClient(tables={
        "geo_report_collection_runs": [
            {"run_id": "run-1", "project_id": "rp-1", "started_at": "2026-01-01T00:00:00Z"}],
    })
    with pytest.raises(report_studio_adapter.ReportStudioAdapterError):
        report_studio_adapter.get_approvable_snapshot(client, "rp-1")


def test_report_studio_adapter_get_approvable_snapshot_passes_when_approved():
    client = FakeSupabaseClient(tables={
        "geo_report_collection_runs": [
            {"run_id": "run-1", "project_id": "rp-1", "started_at": "2026-01-01T00:00:00Z"}],
        "geo_ios_review_cycles": [{
            "review_cycle_id": "cyc-1", "report_project_id": "rp-1", "round_number": 1,
            "reviewed_version_label": "run:run-1", "result": "approved", "status": "completed"}],
    })
    built = report_studio_adapter.get_approvable_snapshot(client, "rp-1")
    assert built["version_label"] == "run:run-1"


# ---- 既存の健全な経路の回帰確認（rejected/unapprovedからlearning candidateが作られない） ----

def test_eligible_comments_excludes_rejected_and_open():
    """rejected/unapproved dataからlearning candidate → reject（既存の健全な挙動の回帰確認）。"""
    source_package = {
        "review_cycles": [{
            "cycle": {"review_cycle_id": "cyc-1"},
            "comments": [
                {"review_comment_id": "c-open", "resolved_status": "open"},
                {"review_comment_id": "c-rejected", "resolved_status": "rejected"},
                {"review_comment_id": "c-na", "resolved_status": "not_applicable"},
                {"review_comment_id": "c-resolved", "resolved_status": "resolved"},
                {"review_comment_id": "c-partial", "resolved_status": "partially_resolved"},
            ],
        }],
    }
    eligible = extraction_service.eligible_comments(source_package)
    eligible_ids = {e["comment"]["review_comment_id"] for e in eligible}
    assert eligible_ids == {"c-resolved", "c-partial"}
