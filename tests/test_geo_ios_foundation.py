"""Geopolitical Intelligence OS Phase B 基盤（Analysis Session/Review/Approval/Learning Candidate）
のワークフローテスト（§32）。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_analysis_session_service as session_service  # noqa: E402
import geo_ios_approval_service as approval_service  # noqa: E402
import geo_ios_learning_candidate_service as learning_service  # noqa: E402
import geo_ios_review_service as review_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


# ---- Analysis Session / Scenario Candidate（§3-4） --------------------------

def test_scenario_candidates_keep_all_but_display_only_top_n():
    client = FakeSupabaseClient()
    session = session_service.create_session(client, title="米中半導体規制の分析", user_id="analyst-1")

    candidates = [{"title": f"シナリオ{i}", "content": {"rank_hint": i}} for i in range(1, 21)]
    created = session_service.record_scenario_candidates(
        client, session["analysis_session_id"], candidates=candidates, display_top_n=5,
        actor_id="analyst-1")

    assert len(created) == 20
    all_candidates = session_service.list_scenario_candidates(client, session["analysis_session_id"])
    assert len(all_candidates) == 20  # 表示しなかった候補も削除されない

    displayed = session_service.list_scenario_candidates(
        client, session["analysis_session_id"], displayed_only=True)
    assert len(displayed) == 5
    assert [c["rank"] for c in displayed] == [1, 2, 3, 4, 5]


def test_select_scenario_candidate_is_exclusive_and_updates_session():
    client = FakeSupabaseClient()
    session = session_service.create_session(client, title="t", user_id="analyst-1")
    candidates = session_service.record_scenario_candidates(
        client, session["analysis_session_id"],
        candidates=[{"title": "A"}, {"title": "B"}], actor_id="analyst-1")

    session_service.select_scenario_candidate(client, candidates[0]["scenario_candidate_id"],
                                               actor_id="analyst-1")
    selected = session_service.select_scenario_candidate(
        client, candidates[1]["scenario_candidate_id"], actor_id="analyst-1")

    assert selected["selected"] is True
    remaining = session_service.list_scenario_candidates(client, session["analysis_session_id"])
    selected_flags = {c["scenario_candidate_id"]: c["selected"] for c in remaining}
    assert selected_flags[candidates[0]["scenario_candidate_id"]] is False
    assert selected_flags[candidates[1]["scenario_candidate_id"]] is True

    updated_session = session_service.get_session(client, session["analysis_session_id"])
    assert updated_session["selected_scenario_id"] == candidates[1]["scenario_candidate_id"]
    assert updated_session["status"] == "scenario_selected"


# ---- Analysis Design（§5） --------------------------------------------------

def test_analysis_design_versions_are_append_only_and_current_is_latest():
    client = FakeSupabaseClient()
    session = session_service.create_session(client, title="t", user_id="analyst-1")
    sid = session["analysis_session_id"]

    v1 = session_service.create_design_version(
        client, sid, fields={"investigation_question": "対中半導体規制の帰結は？",
                              "key_actors": ["White House"]},
        change_note="初版", created_by="analyst-1")
    assert v1["version"] == 1

    v2 = session_service.create_design_version(
        client, sid, fields={"key_actors": ["White House", "Senate", "House"]},
        change_note="議会をActorとして追加", created_by="analyst-1")
    assert v2["version"] == 2
    # 前回値を引き継ぐ（investigation_questionは明示的に変更していない）
    assert v2["investigation_question"] == "対中半導体規制の帰結は？"

    history = session_service.list_design_history(client, sid)
    assert [d["version"] for d in history] == [1, 2]  # v1は削除されずそのまま残る

    current = session_service.get_current_design(client, sid)
    assert current["version"] == 2
    assert current["key_actors"] == ["White House", "Senate", "House"]


# ---- Review Cycle（§8, §11） -------------------------------------------------

PROJECT_ID = "report-project-1"


def test_review_cycle_rounds_increment_without_limit_and_snapshot_is_frozen():
    client = FakeSupabaseClient()
    snapshot_v1 = {"synthesis_status": "draft", "final_html": "<p>v1</p>"}

    cycle1 = review_service.request_review_cycle(
        client, report_project_id=PROJECT_ID, reviewer="expert-a", requested_by="analyst-1",
        version_label="v0.3", version_snapshot=snapshot_v1)
    assert cycle1["round_number"] == 1

    review_service.submit_review_findings(
        client, cycle1["review_cycle_id"],
        findings={"critical_findings": ["Evidence不足"], "major_findings": [], "minor_findings": []},
        result="revision_requested", reviewer="expert-a")

    # Draftがv1からv2へ変わった後で2回目のReviewを依頼しても、1回目のsnapshotは変わらない
    snapshot_v2 = {"synthesis_status": "draft", "final_html": "<p>v2 revised</p>"}
    cycle2 = review_service.request_review_cycle(
        client, report_project_id=PROJECT_ID, reviewer="expert-a", requested_by="analyst-1",
        version_label="v0.4", version_snapshot=snapshot_v2)
    assert cycle2["round_number"] == 2

    cycle1_reloaded = review_service.get_review_cycle(client, cycle1["review_cycle_id"])
    assert cycle1_reloaded["version_snapshot"] == snapshot_v1  # 1回目のスナップショットは不変
    assert cycle1_reloaded["status"] == "completed"
    assert cycle1_reloaded["result"] == "revision_requested"

    review_service.submit_review_findings(
        client, cycle2["review_cycle_id"], findings={"critical_findings": []},
        result="approved", reviewer="expert-a")
    cycle3 = review_service.request_review_cycle(
        client, report_project_id=PROJECT_ID, reviewer="expert-b", requested_by="analyst-1")
    assert cycle3["round_number"] == 3  # 何回でもラウンドを重ねられる（§11）

    all_cycles = review_service.list_review_cycles(client, PROJECT_ID)
    assert [c["round_number"] for c in all_cycles] == [1, 2, 3]


def test_review_comment_resolution_lifecycle_keeps_history_without_delete():
    client = FakeSupabaseClient()
    cycle = review_service.request_review_cycle(
        client, report_project_id=PROJECT_ID, reviewer="expert-a", requested_by="analyst-1")
    comment = review_service.add_comment(
        client, cycle["review_cycle_id"], comment="このEvidenceは一次情報源ではない",
        created_by="expert-a", anchor_type="evidence", anchor_id="ev-12", severity="major")
    assert comment["resolved_status"] == "open"

    resolved = review_service.respond_to_comment(
        client, comment["review_comment_id"], resolution_status="resolved",
        author_response="一次情報源に差し替えました", resolved_in_version="v0.4", actor_id="analyst-1")
    assert resolved["resolved_status"] == "resolved"
    assert resolved["author_response"] == "一次情報源に差し替えました"

    still_there = review_service.list_comments(client, cycle["review_cycle_id"])
    assert len(still_there) == 1  # 削除されていない


# ---- Approval（§13-14） ------------------------------------------------------

def test_approval_requires_explicit_approver_for_both_routes_not_auto():
    client = FakeSupabaseClient()

    with pytest.raises(approval_service.ApprovalError):
        approval_service.record_approval_event(
            client, report_project_id=PROJECT_ID, approval_status="approved", approver="",
            approver_role="approver", approval_source="in_app")

    in_app = approval_service.approve_in_app(
        client, report_project_id=PROJECT_ID, approver="expert-a", approver_role="approver",
        version_label="v1.0")
    assert in_app["approval_source"] == "in_app"
    assert in_app["approval_status"] == "approved"

    uploaded = approval_service.approve_via_upload(
        client, report_project_id="report-project-2", approver="steward-1", approver_role="approver",
        version_label="v1.0")
    assert uploaded["approval_source"] == "upload"
    assert uploaded["approver"] == "steward-1"  # Uploadという行為自体は承認ではなく、approverが必須

    current = approval_service.get_current_approval(client, PROJECT_ID)
    assert current["approval_event_id"] == in_app["approval_event_id"]


# ---- Learning Candidate（§20-25） -------------------------------------------

def test_learning_candidate_is_not_auto_active_and_requires_two_human_gates():
    client = FakeSupabaseClient()
    candidate = learning_service.create_candidate(
        client, candidate_type="reasoning_rule",
        observation="Expertは政権単独ではなく議会を独立Actorとして分析していた",
        generalized_rule="米国政策分析では議会（上院/下院）を政権と独立したActorとして扱う",
        extraction_model="gpt-review-extractor-v1")
    assert candidate["status"] == "candidate"

    # candidateのままではKnowledge Itemへ昇格できない
    with pytest.raises(learning_service.LearningCandidateError):
        learning_service.promote_to_knowledge_item(
            client, candidate["candidate_id"], knowledge_type="reasoning_rule",
            structured_content=_valid_reasoning_rule_content(), information_class="internal_hypothesis",
            created_by="steward-1")

    approved = learning_service.approve_candidate(
        client, candidate["candidate_id"], reviewed_by="steward-1", reviewer_role="knowledge_steward")
    assert approved["status"] == "approved"

    knowledge_item = learning_service.promote_to_knowledge_item(
        client, candidate["candidate_id"], knowledge_type="reasoning_rule",
        structured_content=_valid_reasoning_rule_content(), information_class="internal_hypothesis",
        created_by="steward-1")
    # 既存Knowledge DBの既定どおり、生成直後はまだcandidate（Active化はしていない、二重ゲート）
    assert knowledge_item["status"] == "candidate"

    reloaded = learning_service.get_candidate(client, candidate["candidate_id"])
    assert reloaded["approved_knowledge_item_id"] == knowledge_item["knowledge_item_id"]

    active_only = client.select("geo_knowledge_items", {"select": "*", "status": "eq.active"})
    assert active_only == []  # Human Reviewでの正式なactive遷移を経ていないため一覧にも出ない


def test_learning_candidate_reject_excludes_from_promotion():
    client = FakeSupabaseClient()
    candidate = learning_service.create_candidate(
        client, candidate_type="failure_pattern", observation="obs", generalized_rule="rule",
        extraction_model="m1")
    rejected = learning_service.reject_candidate(
        client, candidate["candidate_id"], reviewed_by="steward-1", reviewer_role="knowledge_steward",
        reason="一般化しすぎ")
    assert rejected["status"] == "rejected"

    with pytest.raises(learning_service.LearningCandidateError):
        learning_service.promote_to_knowledge_item(
            client, candidate["candidate_id"], knowledge_type="reasoning_rule",
            structured_content=_valid_reasoning_rule_content(), information_class="internal_hypothesis",
            created_by="steward-1")

    candidates_list = learning_service.list_candidates(client, status="candidate")
    assert candidates_list == []  # rejected済みはcandidate一覧（Inboxの未処理分）に出ない


def _valid_reasoning_rule_content() -> dict:
    return {
        "rule": "米国政策分析では議会を政権と独立したActorとして扱う",
        "rationale": "Expert Reviewでの修正から一般化",
        "applicable_question_types": ["us_policy_analysis"],
        "required_checks": ["congress_position_checked"],
        "comparison_dimensions": ["executive_vs_legislative"],
        "common_failure_modes": ["政権のみを分析しActorが漏れる"],
        "expert_source": "steward-1",
        "approval_status": "approved",
    }
