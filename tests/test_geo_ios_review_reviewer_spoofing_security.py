"""Geopolitical Intelligence OS: Review Request Reviewer Spoofing対策のテスト。

Review Cycle作成時（POST /api/geo/ios/review/cycles）にclientから送られるreviewer
identity_idを、server-side geo_ios.identitiesに実在し、role=expert_reviewerで
あることをBackend側でも検証すること（UI SelectだけをSecurity Boundaryにしない）。
required test matrix:
  registered expert_reviewer → ALLOW
  registered analystをreviewer指定 → DENY
  arbitrary string → DENY
  unknown identity → DENY
"""
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_identity  # noqa: E402
import geo_ios_review_api as review_api  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

_REGISTRY = {
    "cred-reviewer": {"identity_id": "pilot_expert_reviewer", "role": "expert_reviewer", "domain": None},
    "cred-analyst": {"identity_id": "pilot_analyst", "role": "analyst", "domain": None},
}


def _request(reviewer: str) -> review_api.RequestReviewCycleRequest:
    return review_api.RequestReviewCycleRequest(
        report_project_id="rp-spoof-test", reviewer=reviewer, requested_by="analyst-1",
        version_label="v1", version_snapshot={"run_id": "manual", "syntheses": [{"final_html": "<p>x</p>"}]})


# ---- geo_ios_identity.resolve_reviewer_identity（単体） -----------------------

def test_resolve_reviewer_identity_registered_expert_reviewer_allowed(monkeypatch):
    monkeypatch.setattr(geo_ios_identity, "_IDENTITY_REGISTRY", _REGISTRY)
    assert geo_ios_identity.resolve_reviewer_identity("pilot_expert_reviewer") is not None


def test_resolve_reviewer_identity_registered_analyst_denied(monkeypatch):
    """identityとしては実在するが、role=expert_reviewerではないので拒否する。"""
    monkeypatch.setattr(geo_ios_identity, "_IDENTITY_REGISTRY", _REGISTRY)
    assert geo_ios_identity.resolve_reviewer_identity("pilot_analyst") is None


def test_resolve_reviewer_identity_arbitrary_string_denied(monkeypatch):
    monkeypatch.setattr(geo_ios_identity, "_IDENTITY_REGISTRY", _REGISTRY)
    assert geo_ios_identity.resolve_reviewer_identity("expert-a") is None


def test_resolve_reviewer_identity_unknown_identity_denied(monkeypatch):
    monkeypatch.setattr(geo_ios_identity, "_IDENTITY_REGISTRY", _REGISTRY)
    assert geo_ios_identity.resolve_reviewer_identity("nobody-registered") is None


def test_resolve_reviewer_identity_none_denied(monkeypatch):
    monkeypatch.setattr(geo_ios_identity, "_IDENTITY_REGISTRY", _REGISTRY)
    assert geo_ios_identity.resolve_reviewer_identity(None) is None


# ---- geo_ios_review_api.request_review_cycle（API層、直接呼び出し） -----------

def test_request_review_cycle_registered_expert_reviewer_allowed(monkeypatch):
    monkeypatch.setattr(geo_ios_identity, "_IDENTITY_REGISTRY", _REGISTRY)
    monkeypatch.setattr(review_api, "_client", FakeSupabaseClient())
    cycle = review_api.request_review_cycle(_request("pilot_expert_reviewer"))
    assert cycle["reviewer"] == "pilot_expert_reviewer"


def test_request_review_cycle_registered_analyst_as_reviewer_denied(monkeypatch):
    monkeypatch.setattr(geo_ios_identity, "_IDENTITY_REGISTRY", _REGISTRY)
    monkeypatch.setattr(review_api, "_client", FakeSupabaseClient())
    with pytest.raises(HTTPException) as exc_info:
        review_api.request_review_cycle(_request("pilot_analyst"))
    assert exc_info.value.status_code == 403


def test_request_review_cycle_arbitrary_string_denied(monkeypatch):
    monkeypatch.setattr(geo_ios_identity, "_IDENTITY_REGISTRY", _REGISTRY)
    monkeypatch.setattr(review_api, "_client", FakeSupabaseClient())
    with pytest.raises(HTTPException) as exc_info:
        review_api.request_review_cycle(_request("expert-a"))
    assert exc_info.value.status_code == 403


def test_request_review_cycle_unknown_identity_denied(monkeypatch):
    monkeypatch.setattr(geo_ios_identity, "_IDENTITY_REGISTRY", _REGISTRY)
    monkeypatch.setattr(review_api, "_client", FakeSupabaseClient())
    with pytest.raises(HTTPException) as exc_info:
        review_api.request_review_cycle(_request("nobody-registered"))
    assert exc_info.value.status_code == 403


def test_request_review_cycle_denial_happens_before_any_write(monkeypatch):
    """DENYの場合、geo_ios_review_cyclesへの行が一切作られないこと（fail-fast）。"""
    monkeypatch.setattr(geo_ios_identity, "_IDENTITY_REGISTRY", _REGISTRY)
    client = FakeSupabaseClient()
    monkeypatch.setattr(review_api, "_client", client)
    with pytest.raises(HTTPException):
        review_api.request_review_cycle(_request("expert-a"))
    assert client.tables.get("geo_ios_review_cycles", []) == []
