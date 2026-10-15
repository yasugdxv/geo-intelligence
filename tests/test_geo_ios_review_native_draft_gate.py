"""Geopolitical Intelligence OS: Native Draft経路のReview Cycle/Approval Gateのテスト。

report_generation_mode="native"のSessionでは、Review Cycle依頼・Approvalの両方で
Backend側がGate/Freeze整合性を強制すること（Frontendの制御をbypassしてAPIを直接叩いても
通らないこと）を確認する。report_generation_mode="report_studio"のSessionは既存経路
（geo_ios_report_studio_adapter）が無改修で使われ、この新しい分岐に一切入らないことも
併せて確認する。
"""
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_identity  # noqa: E402
import geo_ios_report_draft_service as draft_service  # noqa: E402
import geo_ios_review_api as review_api  # noqa: E402
import geo_ios_approval_api as approval_api  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

_REGISTRY = {
    "cred-reviewer": {"identity_id": "pilot_expert_reviewer", "role": "expert_reviewer", "domain": None},
}


def _seed_native_session(client, session_id="session-1"):
    client.tables.setdefault("geo_ios_analysis_sessions", []).append({
        "analysis_session_id": session_id, "title": "T", "theme": "テーマ", "user_id": "analyst-1",
        "status": "design_in_progress", "report_generation_mode": "native",
        # approve_in_appはreport_project_id→Session逆引き(get_session_by_report_project)で
        # モード判定するため、Approval系テストにはlinked_report_project_idの設定が必須。
        "linked_report_project_id": "proj-1",
    })


def _seed_report_studio_session(client, session_id="session-legacy"):
    client.tables.setdefault("geo_ios_analysis_sessions", []).append({
        "analysis_session_id": session_id, "title": "T", "theme": "テーマ", "user_id": "analyst-1",
        "status": "handed_off", "report_generation_mode": "report_studio",
        "linked_report_project_id": "proj-legacy",
    })


def _completed_draft_with_passing_gate(client, session_id="session-1"):
    draft = draft_service.create_draft_version(client, session_id, source="ai_generated", created_by="a")
    draft = draft_service.mark_draft_completed(
        client, draft["report_draft_id"],
        sections=[{"heading": "H", "body": "B", "supporting_evidence_ids": []}],
        expert_request_id="r", expert_response_id="r")
    attempt = draft_service.create_ai_review_attempt(client, draft["report_draft_id"], requested_by="a")
    draft_service.mark_ai_review_completed(
        client, attempt["draft_ai_review_id"], findings=[{"severity": "Minor", "finding": "x"}],
        expert_request_id="r", expert_response_id="r")
    return draft


def _review_cycle_request(**overrides):
    base = dict(report_project_id="proj-1", reviewer="pilot_expert_reviewer", requested_by="analyst-1",
                analysis_session_id="session-1")
    base.update(overrides)
    return review_api.RequestReviewCycleRequest(**base)


# ---- Review Cycle依頼: Gate強制 ----------------------------------------------

def test_request_review_cycle_native_blocked_when_gate_not_passed(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(geo_ios_identity, "_IDENTITY_REGISTRY", _REGISTRY)
    monkeypatch.setattr(review_api, "_client", client)
    _seed_native_session(client)
    draft_service.create_draft_version(client, "session-1", source="ai_generated", created_by="a")  # pendingのまま

    with pytest.raises(HTTPException) as exc_info:
        review_api.request_review_cycle(_review_cycle_request())
    assert exc_info.value.status_code == 400
    assert client.tables.get("geo_ios_review_cycles", []) == []


def test_request_review_cycle_native_succeeds_and_freezes_source_fields(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(geo_ios_identity, "_IDENTITY_REGISTRY", _REGISTRY)
    monkeypatch.setattr(review_api, "_client", client)
    _seed_native_session(client)
    draft = _completed_draft_with_passing_gate(client)

    cycle = review_api.request_review_cycle(_review_cycle_request())
    assert cycle["source_report_draft_id"] == draft["report_draft_id"]
    assert cycle["source_draft_version"] == draft["version"]
    assert cycle["source_content_hash"] == draft["content_hash"]
    assert cycle["version_snapshot"]["syntheses"][0]["final_html"]


def test_request_review_cycle_report_studio_session_never_reaches_native_branch(monkeypatch):
    """report_generation_mode=report_studioのSessionは、Native Draftが(誤って)存在しても
    既存Report Studio Adapter経路のまま——build_native_review_snapshotを一切呼ばない。"""
    client = FakeSupabaseClient()
    monkeypatch.setattr(geo_ios_identity, "_IDENTITY_REGISTRY", _REGISTRY)
    monkeypatch.setattr(review_api, "_client", client)
    _seed_report_studio_session(client, "session-legacy")

    called = {"native": False}
    monkeypatch.setattr(
        draft_service, "build_native_review_snapshot",
        lambda *a, **k: called.update(native=True) or {})

    with pytest.raises(HTTPException) as exc_info:
        # Report Studio側にget_latest_runが無い（テスト対象外）ため、既存Legacy経路が
        # ReportStudioAdapterError→HTTPException(400)を返すことを確認するだけで十分
        # ——Native分岐に入っていないことがcalled["native"]で分かる。
        review_api.request_review_cycle(_review_cycle_request(
            report_project_id="proj-legacy", analysis_session_id="session-legacy"))
    assert exc_info.value.status_code == 400
    assert called["native"] is False


# ---- Approval: Freezeされた内容のみ ------------------------------------------

class _FakeIdentity:
    def __init__(self, identity_id="approver-1", role="approver"):
        self.identity_id = identity_id
        self.assigned_role = role


def _approval_request(**overrides):
    base = dict(report_project_id="proj-1", approval_status="approved", approver="approver-1")
    base.update(overrides)
    return approval_api.RecordApprovalRequest(**base)


def test_approve_in_app_native_blocked_without_matching_review(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(approval_api, "_client", client)
    _seed_native_session(client)
    _completed_draft_with_passing_gate(client)  # Reviewはまだ依頼していない

    with pytest.raises(HTTPException) as exc_info:
        approval_api.approve_in_app(_approval_request(), identity=_FakeIdentity())
    assert exc_info.value.status_code == 400
    assert client.tables.get("geo_ios_approval_events", []) == []


def test_approve_in_app_native_succeeds_when_current_draft_matches_approved_review(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(geo_ios_identity, "_IDENTITY_REGISTRY", _REGISTRY)
    monkeypatch.setattr(review_api, "_client", client)
    monkeypatch.setattr(approval_api, "_client", client)
    _seed_native_session(client)
    draft = _completed_draft_with_passing_gate(client)
    cycle = review_api.request_review_cycle(_review_cycle_request())
    from geo_ios_review_service import REVIEW_RESULTS  # noqa: F401  (資料: approvedが正規result値であることの明示)
    client.update("geo_ios_review_cycles", {"review_cycle_id": f"eq.{cycle['review_cycle_id']}"},
                  {"result": "approved", "status": "completed"})

    result = approval_api.approve_in_app(_approval_request(), identity=_FakeIdentity())
    assert result["approval_status"] == "approved"
    assert result["source_report_draft_id"] == draft["report_draft_id"]
    assert result["source_content_hash"] == draft["content_hash"]


def test_approve_in_app_native_blocked_after_new_draft_version_created(monkeypatch):
    """Draft v1をExpert Reviewでapproved後にv2を作成すると、v2はまだReviewを通っていないため
    Approval不可（v1のReview結果をv2へ流用しない）。"""
    client = FakeSupabaseClient()
    monkeypatch.setattr(geo_ios_identity, "_IDENTITY_REGISTRY", _REGISTRY)
    monkeypatch.setattr(review_api, "_client", client)
    monkeypatch.setattr(approval_api, "_client", client)
    _seed_native_session(client)
    v1 = _completed_draft_with_passing_gate(client)
    cycle = review_api.request_review_cycle(_review_cycle_request())
    client.update("geo_ios_review_cycles", {"review_cycle_id": f"eq.{cycle['review_cycle_id']}"},
                  {"result": "approved", "status": "completed"})

    draft_service.create_draft_version(
        client, "session-1", source="human_edit", created_by="a",
        sections=[{"heading": "H2", "body": "更新", "supporting_evidence_ids": []}],
        base_report_draft_id=v1["report_draft_id"])

    with pytest.raises(HTTPException) as exc_info:
        approval_api.approve_in_app(_approval_request(), identity=_FakeIdentity())
    assert exc_info.value.status_code == 400
