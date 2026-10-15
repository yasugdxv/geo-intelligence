"""Geopolitical Intelligence OS: Workflow Board APIのTargeted Security Checkテスト。

GET /api/geo/ios/workflow-boardが新たに公開する集計面（案件title/workflow state/
reviewer/report metadata）について、
  1. authenticated IOS identityを必須とすること（missing/unknown credential→401、
     verify_ios_identityが実際にDependsで配線されていること）
  2. role別に返す案件が正しく絞り込まれること
     （expert_reviewer=自分がassignされた案件のみ／approver=approval・published段階のみ／
     knowledge_steward・administrator=全件／未対応role=fail-closedで空リスト）
を確認する。既存test_geo_ios_phase_i_a1_trust_boundary.pyの方針（TestClientは使わず、
API層の関数をFastAPIのDependsを経由しない直接呼び出しで検証する）を踏襲する。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_identity  # noqa: E402
import geo_ios_workflow_board_api as board_api  # noqa: E402
import geo_ios_workflow_board_service as board_service  # noqa: E402


def _identity(role: str, identity_id: str = "test-identity") -> geo_ios_identity.IosIdentity:
    return geo_ios_identity.IosIdentity(identity_id=identity_id, assigned_role=role, authenticated=True)


# ---- authenticated IOS identityが実際にDependsで必須になっていること ----------

def test_get_workflow_board_requires_verify_ios_identity_dependency():
    """エンドポイント関数のidentity引数のデフォルトが、実際に
    Depends(geo_ios_identity.verify_ios_identity)であることを確認する
    （missing/unknown credentialの401はverify_ios_identity自身の既存テストで
    保証済み——test_geo_ios_phase_i_a1_trust_boundary.py参照。ここではその
    verify_ios_identityが本当にこのエンドポイントへ配線されていることを保証する）。"""
    import inspect
    sig = inspect.signature(board_api.get_workflow_board)
    default = sig.parameters["identity"].default
    assert default.dependency is geo_ios_identity.verify_ios_identity


# ---- role別フィルタリング（geo_ios_workflow_board_service.filter_cases_for_identity） ----

def _sample_cases():
    return [
        {  # design段階、analyst-1が作成
            "analysis_session_id": "s-design", "title": "Design中の案件", "user_id": "analyst-1",
            "has_design": True, "has_report_project": False, "has_open_review_cycle": False,
            "latest_review_reviewer": None, "latest_review_result": None,
            "approval_status": None, "publication_status": None,
        },
        {  # expert_review段階、reviewer-aへassign済み
            "analysis_session_id": "s-review", "title": "Review中の案件", "user_id": "analyst-2",
            "has_design": True, "has_report_project": True, "has_open_review_cycle": True,
            "latest_review_reviewer": "reviewer-a", "latest_review_result": None,
            "approval_status": None, "publication_status": None,
        },
        {  # approval段階（review完了・承認待ち）
            "analysis_session_id": "s-approval", "title": "承認待ちの案件", "user_id": "analyst-1",
            "has_design": True, "has_report_project": True, "has_open_review_cycle": False,
            "latest_review_reviewer": "reviewer-a", "latest_review_result": "approved",
            "approval_status": None, "publication_status": None,
        },
        {  # published段階
            "analysis_session_id": "s-published", "title": "公開済みの案件", "user_id": "analyst-2",
            "has_design": True, "has_report_project": True, "has_open_review_cycle": False,
            "latest_review_reviewer": "reviewer-b", "latest_review_result": "approved",
            "approval_status": "approved", "publication_status": "published",
        },
    ]


def test_filter_cases_administrator_sees_all():
    cases = _sample_cases()
    result = board_service.filter_cases_for_identity(cases, _identity("administrator"))
    assert len(result) == 4


def test_filter_cases_knowledge_steward_sees_all():
    cases = _sample_cases()
    result = board_service.filter_cases_for_identity(cases, _identity("knowledge_steward"))
    assert len(result) == 4


def test_filter_cases_approver_sees_only_approval_and_published_stage():
    cases = _sample_cases()
    result = board_service.filter_cases_for_identity(cases, _identity("approver"))
    ids = {c["analysis_session_id"] for c in result}
    assert ids == {"s-approval", "s-published"}


def test_filter_cases_expert_reviewer_sees_only_own_assigned_cases():
    cases = _sample_cases()
    result = board_service.filter_cases_for_identity(cases, _identity("expert_reviewer", identity_id="reviewer-a"))
    ids = {c["analysis_session_id"] for c in result}
    assert ids == {"s-review", "s-approval"}


def test_filter_cases_expert_reviewer_sees_nothing_for_unassigned_cases():
    cases = _sample_cases()
    result = board_service.filter_cases_for_identity(cases, _identity("expert_reviewer", identity_id="reviewer-z"))
    assert result == []


def test_filter_cases_analyst_sees_only_own_created_cases():
    cases = _sample_cases()
    result = board_service.filter_cases_for_identity(cases, _identity("analyst", identity_id="analyst-1"))
    ids = {c["analysis_session_id"] for c in result}
    assert ids == {"s-design", "s-approval"}


def test_filter_cases_unrecognized_role_denied_fail_closed():
    """viewer等、Board Authorizationで未定義のroleはfail-closed（空リスト）——
    デフォルトallowにしないこと。"""
    cases = _sample_cases()
    result = board_service.filter_cases_for_identity(cases, _identity("viewer"))
    assert result == []


def test_unauthorized_caller_cannot_see_other_cases_title_or_reviewer():
    """unauthorized（=自分にassignされていない）案件のtitle/reviewer等が
    レスポンスに一切含まれないことを直接確認する。"""
    cases = _sample_cases()
    result = board_service.filter_cases_for_identity(cases, _identity("expert_reviewer", identity_id="reviewer-a"))
    titles = {c["title"] for c in result}
    assert "公開済みの案件" not in titles
    assert "Design中の案件" not in titles
