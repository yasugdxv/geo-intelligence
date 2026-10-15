"""Human Gate #2 P0 Closure（ユーザー指示、2026-09-23、Post-Freeze Review Platform
Audit Finding再発防止対応）の回帰テスト。

対象: geo_knowledge_api.py `/items/{id}/review`（Learning Candidate/Report
Publicationが最終的にKnowledge Item を"active"化する最後のTrust Boundary）と、
geo_knowledge_service.review_knowledge_item内のlineage検証
（_validate_active_lineage）。

既存tests/test_geo_ios_phase_i_a1_trust_boundary.py・test_geo_ios_review_platform_
p0_remediation.pyの流儀（FakeSupabaseClient直呼び、TestClient等は使わない）を踏襲する。
"""
import inspect

import pytest
from fastapi import HTTPException
from fastapi.params import Depends as DependsMarker

import geo_ios_identity
import geo_knowledge_api
import geo_knowledge_service
import geo_role_guard
from tests._fakes import FakeSupabaseClient


def _identity(role: str, identity_id: str = "test-identity") -> geo_ios_identity.IosIdentity:
    return geo_ios_identity.IosIdentity(
        identity_id=identity_id, assigned_role=role, domain=None, authenticated=True)


def _valid_precedent_content():
    return {
        "precedent_title": "t", "question_examined": "q", "period": "p", "geography": "g",
        "actors": ["A"], "background": "b", "triggering_event": "e", "actor_objectives": ["o"],
        "instruments_used": ["i"], "legal_and_institutional_conditions": "l",
        "counterpart_responses": "c", "outcome": "out", "success_factors": ["s"],
        "failure_factors": ["f"], "applicable_conditions": ["ac"],
        "non_applicable_conditions": ["nac"], "indicators_to_watch": ["iw"], "sources": ["src"],
        "confidence": "中",
    }


# ---- 認証境界（配線確認） ----------------------------------------------------------

def test_review_knowledge_item_endpoint_requires_ios_identity():
    """unauthenticated Gate #2 accept → reject（配線確認）。"""
    sig = inspect.signature(geo_knowledge_api.review_knowledge_item)
    found = False
    for param in sig.parameters.values():
        default = param.default
        if isinstance(default, DependsMarker) and default.dependency is geo_ios_identity.verify_ios_identity:
            found = True
    assert found, "review_knowledge_item に Depends(geo_ios_identity.verify_ios_identity) が無い"


def test_verify_ios_identity_rejects_missing_credential_for_gate2():
    with pytest.raises(HTTPException) as exc_info:
        geo_ios_identity.verify_ios_identity(x_ios_credential=None)
    assert exc_info.value.status_code == 401


# ---- forged reviewer/role・unauthorized authenticated user → reject ----------------

def test_review_knowledge_item_forged_role_ignored_uses_authenticated_role(monkeypatch):
    """forged reviewer/role → reject: request body上のreviewer_role="knowledge_steward"を
    騙っても、authenticated identityがviewerであれば403になる（payloadは一切信用しない）。"""
    client = FakeSupabaseClient()
    item = geo_knowledge_service.create_knowledge_item(
        client, knowledge_type="precedent", title="前例X", structured_content=_valid_precedent_content(),
        information_class="external_assessment", created_by="tester")
    monkeypatch.setattr(geo_knowledge_api, "_client", client)

    req = geo_knowledge_api.KnowledgeItemReviewRequest(
        reviewer_id="claimed-steward", reviewer_role="knowledge_steward",
        review_decision="start_review", review_comment="x", evidence_adequacy="partial",
        applicability_checked=False)
    unauthorized_identity = _identity("viewer", identity_id="attacker-id")
    with pytest.raises(HTTPException) as exc_info:
        geo_knowledge_api.review_knowledge_item(item["knowledge_item_id"], req, unauthorized_identity)
    assert exc_info.value.status_code == 403


def test_review_knowledge_item_unauthorized_authenticated_role_rejected(monkeypatch):
    """unauthorized authenticated user → reject: 認証は通るがrole=analystはREVIEWER_ROLES
    （expert_reviewer/knowledge_steward）に含まれない。"""
    client = FakeSupabaseClient()
    item = geo_knowledge_service.create_knowledge_item(
        client, knowledge_type="precedent", title="前例Y", structured_content=_valid_precedent_content(),
        information_class="external_assessment", created_by="tester")
    monkeypatch.setattr(geo_knowledge_api, "_client", client)

    req = geo_knowledge_api.KnowledgeItemReviewRequest(
        review_decision="start_review", review_comment="x", evidence_adequacy="partial",
        applicability_checked=False)
    analyst_identity = _identity("analyst", identity_id="analyst-id")
    with pytest.raises(HTTPException) as exc_info:
        geo_knowledge_api.review_knowledge_item(item["knowledge_item_id"], req, analyst_identity)
    assert exc_info.value.status_code == 403


def test_review_knowledge_item_authenticated_expert_reviewer_passes(monkeypatch):
    """valid authenticated reviewer + eligible candidate → pass（lineage不要な通常itemの回帰）。"""
    client = FakeSupabaseClient()
    item = geo_knowledge_service.create_knowledge_item(
        client, knowledge_type="precedent", title="前例Z", structured_content=_valid_precedent_content(),
        information_class="external_assessment", created_by="tester")
    monkeypatch.setattr(geo_knowledge_api, "_client", client)

    req = geo_knowledge_api.KnowledgeItemReviewRequest(
        review_decision="start_review", review_comment="着手", evidence_adequacy="partial",
        applicability_checked=False)
    identity = _identity("expert_reviewer", identity_id="real-reviewer")
    result = geo_knowledge_api.review_knowledge_item(item["knowledge_item_id"], req, identity)
    assert result["knowledge_item"]["status"] == "in_review"
    assert result["knowledge_item"]["reviewed_by"] == "real-reviewer"  # payload由来ではなく認証済みidentity


# ---- Lineage Validation（geo_knowledge_service._validate_active_lineage） ----------

def _learning_candidate_derived_item(client, *, candidate_status: str = "approved",
                                      link_to_item: bool = True):
    item = geo_knowledge_service.create_knowledge_item(
        client, knowledge_type="precedent", title="Learning由来", structured_content=_valid_precedent_content(),
        information_class="internal_hypothesis", created_by="tester",
        origin="geo_ios_learning_candidate")
    # in_reviewまで進める（active遷移の前提）
    geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=item["knowledge_item_id"], reviewer_id="r1",
        reviewer_role="expert_reviewer", review_decision="start_review",
        review_comment="着手", evidence_adequacy="partial", applicability_checked=False)
    if link_to_item:
        client.tables.setdefault("geo_ios_learning_candidates", []).append({
            "candidate_id": "cand-1", "status": candidate_status,
            "approved_knowledge_item_id": item["knowledge_item_id"],
        })
    return item


def test_active_transition_rejects_nonexistent_candidate_lineage():
    """nonexistent candidate → reject: origin=geo_ios_learning_candidateだが、対応する
    Learning Candidate行が一切存在しない。"""
    client = FakeSupabaseClient()
    item = _learning_candidate_derived_item(client, link_to_item=False)
    with pytest.raises(geo_knowledge_service.LineageValidationError):
        geo_knowledge_service.review_knowledge_item(
            client, knowledge_item_id=item["knowledge_item_id"], reviewer_id="r1",
            reviewer_role="knowledge_steward", review_decision="approve_active",
            review_comment="承認", evidence_adequacy="sufficient", applicability_checked=True)


def test_active_transition_rejects_ineligible_rejected_candidate():
    """rejected/ineligible candidate → reject: Learning Candidate自体がstatus=rejected
    （Human Gate #1を通過していない）。"""
    client = FakeSupabaseClient()
    item = _learning_candidate_derived_item(client, candidate_status="rejected")
    with pytest.raises(geo_knowledge_service.LineageValidationError):
        geo_knowledge_service.review_knowledge_item(
            client, knowledge_item_id=item["knowledge_item_id"], reviewer_id="r1",
            reviewer_role="knowledge_steward", review_decision="approve_active",
            review_comment="承認", evidence_adequacy="sufficient", applicability_checked=True)


def test_active_transition_rejects_candidate_pointing_elsewhere():
    """lineage不足candidate → reject: 実在するLearning Candidateはあるが、
    approved_knowledge_item_idが別のitemを指している（このitemとは無関係）。"""
    client = FakeSupabaseClient()
    item = _learning_candidate_derived_item(client, link_to_item=False)
    client.tables.setdefault("geo_ios_learning_candidates", []).append({
        "candidate_id": "cand-2", "status": "approved", "approved_knowledge_item_id": "some-other-item-id",
    })
    with pytest.raises(geo_knowledge_service.LineageValidationError):
        geo_knowledge_service.review_knowledge_item(
            client, knowledge_item_id=item["knowledge_item_id"], reviewer_id="r1",
            reviewer_role="knowledge_steward", review_decision="approve_active",
            review_comment="承認", evidence_adequacy="sufficient", applicability_checked=True)


def test_active_transition_passes_with_valid_learning_candidate_lineage():
    """valid authenticated reviewer + eligible candidate → pass（Learning Candidate経由）。
    accept → provenance付きKnowledge生成: originがそのまま保持され、追跡可能であることも確認。"""
    client = FakeSupabaseClient()
    item = _learning_candidate_derived_item(client, candidate_status="approved")
    result = geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=item["knowledge_item_id"], reviewer_id="r1",
        reviewer_role="knowledge_steward", review_decision="approve_active",
        review_comment="承認", evidence_adequacy="sufficient", applicability_checked=True)
    assert result["knowledge_item"]["status"] == "active"
    assert result["knowledge_item"]["origin"] == "geo_ios_learning_candidate"


def test_active_transition_rejects_incomplete_report_publication_lineage():
    """lineage不足candidate → reject（Report Publication経由）: source_report_project_id
    のみ設定され、source_approval_event_idが欠落している不完全なlineage。"""
    client = FakeSupabaseClient()
    item = geo_knowledge_service.create_knowledge_item(
        client, knowledge_type="precedent", title="Report由来", structured_content=_valid_precedent_content(),
        information_class="external_assessment", created_by="tester")
    client.update("geo_knowledge_items", {"knowledge_item_id": f"eq.{item['knowledge_item_id']}"},
                  {"source_report_project_id": "rp-1"})
    geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=item["knowledge_item_id"], reviewer_id="r1",
        reviewer_role="expert_reviewer", review_decision="start_review",
        review_comment="着手", evidence_adequacy="partial", applicability_checked=False)
    with pytest.raises(geo_knowledge_service.LineageValidationError):
        geo_knowledge_service.review_knowledge_item(
            client, knowledge_item_id=item["knowledge_item_id"], reviewer_id="r1",
            reviewer_role="knowledge_steward", review_decision="approve_active",
            review_comment="承認", evidence_adequacy="sufficient", applicability_checked=True)


def test_active_transition_rejects_report_publication_lineage_pointing_to_unapproved_event():
    """lineage不足candidate → reject（Report Publication経由）: source_approval_event_idが
    実在するが、approval_status=approvedではない（rejected/存在しないevent等）。"""
    client = FakeSupabaseClient(tables={
        "geo_ios_approval_events": [{
            "approval_event_id": "evt-1", "report_project_id": "rp-1", "approval_status": "rejected",
        }],
    })
    item = geo_knowledge_service.create_knowledge_item(
        client, knowledge_type="precedent", title="Report由来2", structured_content=_valid_precedent_content(),
        information_class="external_assessment", created_by="tester")
    client.update("geo_knowledge_items", {"knowledge_item_id": f"eq.{item['knowledge_item_id']}"},
                  {"source_report_project_id": "rp-1", "source_approval_event_id": "evt-1"})
    geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=item["knowledge_item_id"], reviewer_id="r1",
        reviewer_role="expert_reviewer", review_decision="start_review",
        review_comment="着手", evidence_adequacy="partial", applicability_checked=False)
    with pytest.raises(geo_knowledge_service.LineageValidationError):
        geo_knowledge_service.review_knowledge_item(
            client, knowledge_item_id=item["knowledge_item_id"], reviewer_id="r1",
            reviewer_role="knowledge_steward", review_decision="approve_active",
            review_comment="承認", evidence_adequacy="sufficient", applicability_checked=True)


def test_active_transition_passes_with_valid_report_publication_lineage():
    """valid authenticated reviewer + eligible candidate → pass（Report Publication経由）。"""
    client = FakeSupabaseClient(tables={
        "geo_ios_approval_events": [{
            "approval_event_id": "evt-2", "report_project_id": "rp-2", "approval_status": "approved",
        }],
    })
    item = geo_knowledge_service.create_knowledge_item(
        client, knowledge_type="precedent", title="Report由来3", structured_content=_valid_precedent_content(),
        information_class="external_assessment", created_by="tester")
    client.update("geo_knowledge_items", {"knowledge_item_id": f"eq.{item['knowledge_item_id']}"},
                  {"source_report_project_id": "rp-2", "source_approval_event_id": "evt-2"})
    geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=item["knowledge_item_id"], reviewer_id="r1",
        reviewer_role="expert_reviewer", review_decision="start_review",
        review_comment="着手", evidence_adequacy="partial", applicability_checked=False)
    result = geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=item["knowledge_item_id"], reviewer_id="r1",
        reviewer_role="knowledge_steward", review_decision="approve_active",
        review_comment="承認", evidence_adequacy="sufficient", applicability_checked=True)
    assert result["knowledge_item"]["status"] == "active"


def test_reject_decision_does_not_activate_knowledge():
    """reject → Knowledge非生成/非Active: reject決定は必ずstatus=rejectedへ遷移し、
    activeにはならない。"""
    client = FakeSupabaseClient()
    item = geo_knowledge_service.create_knowledge_item(
        client, knowledge_type="precedent", title="却下対象", structured_content=_valid_precedent_content(),
        information_class="external_assessment", created_by="tester")
    geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=item["knowledge_item_id"], reviewer_id="r1",
        reviewer_role="expert_reviewer", review_decision="start_review",
        review_comment="着手", evidence_adequacy="partial", applicability_checked=False)
    result = geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=item["knowledge_item_id"], reviewer_id="r1",
        reviewer_role="expert_reviewer", review_decision="reject",
        review_comment="不採用", evidence_adequacy="insufficient", applicability_checked=False)
    assert result["knowledge_item"]["status"] == "rejected"
    assert result["knowledge_item"]["status"] != "active"


def test_normal_precedent_without_lineage_still_reachable_active():
    """既存の他経路（Entity解決・Connector等、本Remediationのスコープ外）は無検証のまま
    通ることの回帰確認——lineage検証が過剰に既存フローを壊していないことを保証する。"""
    client = FakeSupabaseClient()
    item = geo_knowledge_service.create_knowledge_item(
        client, knowledge_type="precedent", title="通常item", structured_content=_valid_precedent_content(),
        information_class="external_assessment", created_by="tester")
    geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=item["knowledge_item_id"], reviewer_id="r1",
        reviewer_role="expert_reviewer", review_decision="start_review",
        review_comment="着手", evidence_adequacy="partial", applicability_checked=False)
    result = geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=item["knowledge_item_id"], reviewer_id="r1",
        reviewer_role="expert_reviewer", review_decision="approve_active",
        review_comment="承認", evidence_adequacy="sufficient", applicability_checked=True)
    assert result["knowledge_item"]["status"] == "active"
