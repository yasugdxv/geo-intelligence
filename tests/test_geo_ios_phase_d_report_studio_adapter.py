"""Geopolitical Intelligence OS Phase D: IOS Review/ApprovalをReport Studio（既存・実データ）へ
実接続するAdapterのE2Eテスト。

Existing Report → Review Request → Immutable Review Snapshot → Expert Comment →
Author Response → Revision → Optional Additional Review Round → Approval →
Approved Version固定、という一連の流れを、Report Studio既存の公開関数
（get_or_create_project/persist_collection_run相当のテーブル直挿入/list_latest_syntheses_for_project）
のみを使って検証する。Report Studio本体のロジック・スキーマは一切変更しない。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_approval_service as approval_service  # noqa: E402
import geo_ios_report_studio_adapter as adapter  # noqa: E402
import geo_ios_review_service as review_service  # noqa: E402
import geo_report_studio_service as report_studio_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _seed_run_with_synthesis(client, project_id: str, *, run_id: str, final_html: str,
                              final_status: str = "PASS"):
    """test_geo_report_studio_service.pyと同じ流儀（Report Studio既存テーブルへ直接行を積む）で、
    「Report Studio側で1回collectionが回り、Synthesisが確定した」状態を用意する。"""
    client.tables.setdefault("geo_report_collection_runs", []).append({
        "run_id": run_id, "project_id": project_id, "status": "completed",
        "started_at": f"2026-09-{16 + len(client.tables.get('geo_report_collection_runs', []))}T00:00:00+00:00",
    })
    client.tables.setdefault("geo_report_syntheses", []).append({
        "synthesis_id": f"synth-{run_id}", "run_id": run_id, "signal_id": "sig-1",
        "narrative_id": "narr-1", "final_status": final_status, "final_html": final_html,
        "final_draft": {"summary": final_html}, "created_at": "2026-09-16T00:00:00+00:00",
    })


def test_adapter_raises_when_no_run_exists_yet():
    client = FakeSupabaseClient()
    project = report_studio_service.get_or_create_project(client, "PRJ-1", "テストプロジェクト")
    with pytest.raises(adapter.ReportStudioAdapterError):
        adapter.build_review_snapshot(client, project["project_id"])


def test_full_lifecycle_existing_report_to_approved_version_fixed():
    client = FakeSupabaseClient()
    project = report_studio_service.get_or_create_project(client, "PRJ-2", "米中半導体規制レポート")
    project_id = project["project_id"]

    # ---- Existing Report（Report Studio側で既に1回runが回っている） ----
    _seed_run_with_synthesis(client, project_id, run_id="run-1", final_html="<p>v1 draft</p>")

    # ---- Review Request → Immutable Review Snapshot（version_snapshotは自動構築） ----
    snap1 = adapter.build_review_snapshot(client, project_id)
    assert snap1["version_snapshot"]["run_id"] == "run-1"

    cycle1 = review_service.request_review_cycle(
        client, report_project_id=project_id, reviewer="expert-a", requested_by="analyst-1",
        version_label=snap1["version_label"], version_snapshot=snap1["version_snapshot"])
    assert cycle1["round_number"] == 1
    assert cycle1["version_snapshot"]["run_id"] == "run-1"

    # ---- Expert Comment ----
    comment = review_service.add_comment(
        client, cycle1["review_cycle_id"], comment="Congressの動きへの言及が無い",
        created_by="expert-a", anchor_type="section", anchor_id="body", severity="major")

    # ---- Review完了（revision_requested） ----
    review_service.submit_review_findings(
        client, cycle1["review_cycle_id"],
        findings={"critical_findings": [], "major_findings": ["Congress言及の欠落"]},
        result="revision_requested", reviewer="expert-a")

    # ---- Author Response ----
    review_service.record_author_response(
        client, cycle1["review_cycle_id"], author_response="Congressの章を追加しました",
        actor_id="analyst-1")
    review_service.respond_to_comment(
        client, comment["review_comment_id"], resolution_status="resolved",
        author_response="Congressの章を追加しました", resolved_in_version="run-2",
        actor_id="analyst-1")

    # ---- Revision（Report Studio側でauthorが実際に改訂し、新runが積まれる想定） ----
    _seed_run_with_synthesis(client, project_id, run_id="run-2",
                              final_html="<p>v2 revised with Congress section</p>")

    # 依頼時点のSnapshot（v1）は、Report Studio側の状態が変わった後も不変であること
    cycle1_reloaded = review_service.get_review_cycle(client, cycle1["review_cycle_id"])
    assert cycle1_reloaded["version_snapshot"]["run_id"] == "run-1"
    assert cycle1_reloaded["version_snapshot"]["syntheses"][0]["final_html"] == "<p>v1 draft</p>"

    # ---- Optional Additional Review Round（v2に対する2回目のレビュー、snapshotは自動でrun-2） ----
    snap2 = adapter.build_review_snapshot(client, project_id)
    assert snap2["version_snapshot"]["run_id"] == "run-2"

    cycle2 = review_service.request_review_cycle(
        client, report_project_id=project_id, reviewer="expert-a", requested_by="analyst-1",
        version_label=snap2["version_label"], version_snapshot=snap2["version_snapshot"])
    assert cycle2["round_number"] == 2

    review_service.submit_review_findings(
        client, cycle2["review_cycle_id"], findings={"critical_findings": []},
        result="approved", reviewer="expert-a")

    all_cycles = review_service.list_review_cycles(client, project_id)
    assert [c["round_number"] for c in all_cycles] == [1, 2]
    assert all_cycles[0]["result"] == "revision_requested"
    assert all_cycles[1]["result"] == "approved"

    # ---- Approval → Approved Version固定（in_app、承認時点のReport Studio状態=run-2を固定） ----
    approval_snapshot = adapter.build_review_snapshot(client, project_id)
    approved_event = approval_service.approve_in_app(
        client, report_project_id=project_id, approver="expert-a", approver_role="approver",
        version_label=approval_snapshot["version_label"],
        version_snapshot=approval_snapshot["version_snapshot"])
    assert approved_event["approval_status"] == "approved"
    assert approved_event["approval_source"] == "in_app"
    assert approved_event["version_snapshot"]["run_id"] == "run-2"

    # ---- Report Studio側がさらに先へ進んでも(run-3)、Approved Versionは固定されたまま ----
    _seed_run_with_synthesis(client, project_id, run_id="run-3", final_html="<p>v3 further edits</p>")
    current_approval = approval_service.get_current_approval(client, project_id)
    assert current_approval["approval_event_id"] == approved_event["approval_event_id"]
    assert current_approval["version_snapshot"]["run_id"] == "run-2"  # run-3に引きずられない


def test_review_api_summary_shape_matches_expected_contract():
    """geo_ios_review_api.get_review_summaryが返す形（API層のロジックはservice関数の組み合わせの
    みで完結するため、ここではservice層を直接呼んで契約を確認する）。"""
    client = FakeSupabaseClient()
    project = report_studio_service.get_or_create_project(client, "PRJ-3", "テスト2")
    project_id = project["project_id"]
    _seed_run_with_synthesis(client, project_id, run_id="run-1", final_html="<p>x</p>")

    snap = adapter.build_review_snapshot(client, project_id)
    review_service.request_review_cycle(
        client, report_project_id=project_id, reviewer="expert-a", requested_by="analyst-1",
        version_label=snap["version_label"], version_snapshot=snap["version_snapshot"])

    cycles = review_service.list_review_cycles(client, project_id)
    summary = adapter.get_report_project_summary(client, project_id)
    assert summary["project_code"] == "PRJ-3"
    assert len(cycles) == 1
