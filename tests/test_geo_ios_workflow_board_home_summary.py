"""Geopolitical Intelligence OS: Workflow Board Home Summary（メトリクス/タスク/再開候補）のテスト。

compute_home_summaryは既存Board case（filter_cases_for_identity適用後）を集計する
だけの純粋関数であり、新しい優先度モデル・フィールドは追加しない。既に持っている
status（latest_review_result/has_open_review_cycle/approval_status/publication_status）
のみから「要対応」「専門家レビュー中」「承認済み」を判定できていることを確認する。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_identity  # noqa: E402
import geo_ios_workflow_board_service as board_service  # noqa: E402


def _identity(role: str, identity_id: str = "analyst-1") -> geo_ios_identity.IosIdentity:
    return geo_ios_identity.IosIdentity(identity_id=identity_id, assigned_role=role, authenticated=True)


def _case(**overrides):
    base = {
        "analysis_session_id": "s-1", "title": "案件", "user_id": "analyst-1", "created_at": "2026-09-01T00:00:00Z",
        "has_design": True, "has_report_project": False, "has_open_review_cycle": False,
        "latest_review_reviewer": None, "latest_review_result": None,
        "approval_status": None, "publication_status": None,
    }
    base.update(overrides)
    return base


def test_metrics_count_in_progress_excludes_published():
    cases = [
        _case(analysis_session_id="s-1"),  # design段階
        _case(analysis_session_id="s-2", has_report_project=True, has_open_review_cycle=False,
              latest_review_result="approved", approval_status="approved", publication_status="published"),
    ]
    summary = board_service.compute_home_summary(cases, _identity("knowledge_steward"))
    assert summary["metrics"]["in_progress"] == 1
    assert summary["metrics"]["approved"] == 1


def test_needs_attention_counts_revision_requested_without_open_cycle():
    cases = [
        _case(analysis_session_id="s-1", has_report_project=True, has_open_review_cycle=False,
              latest_review_result="revision_requested"),
    ]
    summary = board_service.compute_home_summary(cases, _identity("knowledge_steward"))
    assert summary["metrics"]["needs_attention"] == 1
    assert summary["tasks"][0]["reason"] == "revision_requested"


def test_needs_attention_excludes_revision_requested_with_open_cycle():
    """修正依頼後、既に次のRoundを開始済み（Reviewer対応中）なら著者の要対応ではない。"""
    cases = [
        _case(analysis_session_id="s-1", has_report_project=True, has_open_review_cycle=True,
              latest_review_result="revision_requested"),
    ]
    summary = board_service.compute_home_summary(cases, _identity("knowledge_steward"))
    assert summary["metrics"]["needs_attention"] == 0


def test_needs_attention_counts_approval_pending():
    cases = [
        _case(analysis_session_id="s-1", has_report_project=True, has_open_review_cycle=False,
              latest_review_result="approved", approval_status=None),
    ]
    summary = board_service.compute_home_summary(cases, _identity("knowledge_steward"))
    assert summary["metrics"]["needs_attention"] == 1
    assert summary["tasks"][0]["reason"] == "approval_pending"


def test_in_expert_review_counts_open_cycle():
    cases = [
        _case(analysis_session_id="s-1", has_report_project=True, has_open_review_cycle=True),
    ]
    summary = board_service.compute_home_summary(cases, _identity("knowledge_steward"))
    assert summary["metrics"]["in_expert_review"] == 1


def test_resume_picks_most_recent_active_case_owned_by_identity():
    cases = [
        _case(analysis_session_id="s-old", user_id="analyst-1", created_at="2026-09-01T00:00:00Z"),
        _case(analysis_session_id="s-new", user_id="analyst-1", created_at="2026-09-10T00:00:00Z"),
        _case(analysis_session_id="s-other-user", user_id="analyst-2", created_at="2026-09-15T00:00:00Z"),
    ]
    summary = board_service.compute_home_summary(cases, _identity("analyst", identity_id="analyst-1"))
    assert summary["resume"]["analysis_session_id"] == "s-new"


def test_resume_excludes_published_cases():
    cases = [
        _case(analysis_session_id="s-published", user_id="analyst-1", has_report_project=True,
              has_open_review_cycle=False, latest_review_result="approved",
              approval_status="approved", publication_status="published"),
    ]
    summary = board_service.compute_home_summary(cases, _identity("analyst", identity_id="analyst-1"))
    assert summary["resume"] is None


def test_resume_is_none_when_identity_has_no_active_case():
    cases = [_case(analysis_session_id="s-1", user_id="someone-else")]
    summary = board_service.compute_home_summary(cases, _identity("analyst", identity_id="analyst-1"))
    assert summary["resume"] is None


def test_tasks_capped_at_five():
    cases = [
        _case(analysis_session_id=f"s-{i}", has_report_project=True, has_open_review_cycle=False,
              latest_review_result="revision_requested")
        for i in range(8)
    ]
    summary = board_service.compute_home_summary(cases, _identity("knowledge_steward"))
    assert len(summary["tasks"]) == 5
