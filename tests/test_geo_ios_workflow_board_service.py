"""Geopolitical Intelligence OS: Workflow Board Aggregation Serviceのテスト。

既存のderiveWorkflowState（フロントエンド側）が使う5フィールド
（hasDesign/hasReportProject/latestReviewResult/hasOpenReviewCycle/
approvalStatus/publicationStatus）を、Backend側で正しく導出できることを確認する。
新規テーブル・新規書き込みは無い。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_analysis_session_service as session_service  # noqa: E402
import geo_ios_approval_service as approval_service  # noqa: E402
import geo_ios_review_service as review_service  # noqa: E402
import geo_ios_workflow_board_service as board_service  # noqa: E402
import geo_report_studio_service as report_studio_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def test_list_board_cases_empty_when_no_sessions():
    client = FakeSupabaseClient()
    assert board_service.list_board_cases(client) == []


def test_session_without_report_project_shows_no_review_or_approval_fields():
    client = FakeSupabaseClient()
    session_service.create_session(client, title="T1", user_id="analyst-a", theme="テーマ1")
    cases = board_service.list_board_cases(client)
    assert len(cases) == 1
    case = cases[0]
    assert case["report_project_id"] is None
    assert case["has_report_project"] is False
    assert case["latest_review_result"] is None
    assert case["has_open_review_cycle"] is False
    assert case["approval_status"] is None
    assert case["publication_status"] is None


def test_latest_review_cycle_picks_the_highest_round_not_the_first_row():
    """list_review_cyclesはround_number.asc（昇順）で返す。Board側がcycles[0]を
    誤って使うと最古のラウンドを「最新」と誤認する——この回帰を防ぐテスト。"""
    client = FakeSupabaseClient()
    session = session_service.create_session(client, title="T2", user_id="analyst-b", theme="テーマ2")
    project = report_studio_service.get_or_create_project(client, "PRJ-BOARD-1", "テストレポート")
    session_service.link_report_project(client, session["analysis_session_id"],
                                         report_project_id=project["project_id"], actor_id="analyst-b")

    cycle1 = review_service.request_review_cycle(
        client, report_project_id=project["project_id"], reviewer="reviewer-x", requested_by="analyst-b",
        version_label="v1", version_snapshot={"run_id": "r1", "syntheses": []})
    review_service.submit_review_findings(
        client, cycle1["review_cycle_id"], findings={"major_findings": ["x"]},
        result="revision_requested", reviewer="reviewer-x")

    cycle2 = review_service.request_review_cycle(
        client, report_project_id=project["project_id"], reviewer="reviewer-x", requested_by="analyst-b",
        version_label="v2", version_snapshot={"run_id": "r2", "syntheses": []})
    review_service.submit_review_findings(
        client, cycle2["review_cycle_id"], findings={"critical_findings": []},
        result="approved", reviewer="reviewer-x")

    cases = board_service.list_board_cases(client)
    case = next(c for c in cases if c["analysis_session_id"] == session["analysis_session_id"])
    assert case["latest_review_cycle_id"] == cycle2["review_cycle_id"]
    assert case["latest_review_result"] == "approved"
    assert case["has_open_review_cycle"] is False


def test_open_review_cycle_flagged_correctly():
    client = FakeSupabaseClient()
    session = session_service.create_session(client, title="T3", user_id="analyst-c", theme="テーマ3")
    project = report_studio_service.get_or_create_project(client, "PRJ-BOARD-2", "テストレポート")
    session_service.link_report_project(client, session["analysis_session_id"],
                                         report_project_id=project["project_id"], actor_id="analyst-c")
    review_service.request_review_cycle(
        client, report_project_id=project["project_id"], reviewer="reviewer-y", requested_by="analyst-c",
        version_label="v1", version_snapshot={"run_id": "r1", "syntheses": []})

    cases = board_service.list_board_cases(client)
    case = next(c for c in cases if c["analysis_session_id"] == session["analysis_session_id"])
    assert case["has_open_review_cycle"] is True
    assert case["latest_review_result"] is None


def test_approved_and_published_case_shows_full_state():
    client = FakeSupabaseClient()
    session = session_service.create_session(client, title="T4", user_id="analyst-d", theme="テーマ4")
    project = report_studio_service.get_or_create_project(client, "PRJ-BOARD-3", "テストレポート")
    session_service.link_report_project(client, session["analysis_session_id"],
                                         report_project_id=project["project_id"], actor_id="analyst-d")
    approval_service.record_approval_event(
        client, report_project_id=project["project_id"], approval_status="approved", approver="steward-1",
        approver_role="approver", approval_source="in_app", version_label="v1",
        version_snapshot={"run_id": "r1", "syntheses": []})

    cases = board_service.list_board_cases(client)
    case = next(c for c in cases if c["analysis_session_id"] == session["analysis_session_id"])
    assert case["approval_status"] == "approved"
    # FakeSupabaseClientはDB側のcolumn default（publication_status='not_published'）を
    # 再現しないため、Fake上ではNone（実DBでは'not_published'になる、既存の他IOSテストと
    # 同じ前提）。
    assert case["publication_status"] is None
