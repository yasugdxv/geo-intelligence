"""Geopolitical Intelligence OS: GET /api/geo/ios/workflow-board/home のテスト。

既存get_workflow_boardと同じauthenticated identity必須・role別フィルタを適用した上で
集計するだけであることを確認する（新しいAuthorizationロジックは追加しない）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_identity  # noqa: E402
import geo_ios_workflow_board_api as board_api  # noqa: E402


def _identity(role: str, identity_id: str = "analyst-1") -> geo_ios_identity.IosIdentity:
    return geo_ios_identity.IosIdentity(identity_id=identity_id, assigned_role=role, authenticated=True)


def test_get_workflow_board_home_requires_verify_ios_identity_dependency():
    import inspect
    sig = inspect.signature(board_api.get_workflow_board_home)
    default = sig.parameters["identity"].default
    assert default.dependency is geo_ios_identity.verify_ios_identity


def test_get_workflow_board_home_returns_metrics_tasks_resume_shape(monkeypatch):
    monkeypatch.setattr(board_api.board_service, "list_board_cases", lambda client: [
        {
            "analysis_session_id": "s-1", "title": "案件1", "user_id": "analyst-1",
            "created_at": "2026-09-01T00:00:00Z", "has_design": True, "has_report_project": False,
            "has_open_review_cycle": False, "latest_review_reviewer": None, "latest_review_result": None,
            "approval_status": None, "publication_status": None,
        },
    ])
    result = board_api.get_workflow_board_home(_identity("analyst", identity_id="analyst-1"))
    assert set(result.keys()) == {"metrics", "tasks", "resume"}
    assert result["metrics"]["in_progress"] == 1
    assert result["resume"]["analysis_session_id"] == "s-1"


def test_get_workflow_board_home_respects_role_filtering(monkeypatch):
    """expert_reviewerには自分がassignされていないcaseは見えない——homeのmetricsにも
    含まれない（get_workflow_boardと同じfilter_cases_for_identityを経由するため）。"""
    monkeypatch.setattr(board_api.board_service, "list_board_cases", lambda client: [
        {
            "analysis_session_id": "s-1", "title": "他人の案件", "user_id": "analyst-2",
            "created_at": "2026-09-01T00:00:00Z", "has_design": True, "has_report_project": True,
            "has_open_review_cycle": True, "latest_review_reviewer": "someone-else",
            "latest_review_result": None, "approval_status": None, "publication_status": None,
        },
    ])
    result = board_api.get_workflow_board_home(_identity("expert_reviewer", identity_id="reviewer-a"))
    assert result["metrics"]["in_expert_review"] == 0
    assert result["tasks"] == []
    assert result["resume"] is None
