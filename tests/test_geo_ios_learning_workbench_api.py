"""Geopolitical Intelligence OS: Learning Workbench APIのテスト。

既存test_geo_ios_phase_i_a1_trust_boundary.py/test_geo_ios_consultation_api.pyと同じ方針:
FastAPIのDependsを経由しない直接関数呼び出しで検証する（TestClient等の新しい依存は
増やさない）。role/identityはpayload自己申告ではなく認証済みidentityから解決される
ことを確認する。BackgroundTasksへ実際にタスクが登録されること・登録された関数の
内部処理自体はgeo_ios_learning_workbench_service側の20テストで既に検証済みのため、
ここではAPI層の責務（認可・エラーマッピング・BackgroundTasksへの委譲）に絞る。
"""
import sys
from pathlib import Path

from fastapi import BackgroundTasks, HTTPException
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_identity  # noqa: E402
import geo_ios_learning_workbench_api as workbench_api  # noqa: E402
import geo_ios_learning_candidate_service as learning_service  # noqa: E402
import geo_ios_learning_workbench_service as workbench_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _identity(role: str, identity_id: str = "test-identity") -> geo_ios_identity.IosIdentity:
    return geo_ios_identity.IosIdentity(identity_id=identity_id, assigned_role=role, authenticated=True)


def _seed_candidate(client, **overrides):
    row = dict(
        candidate_type="reasoning_rule", observation="obs", generalized_rule="rule",
        extraction_model="gpt-test", scope="report_specific", confidence="中")
    row.update(overrides)
    return learning_service.create_candidate(client, **row)


def _advance_to_ready_for_approval(client, candidate_id):
    workbench_service.record_human_edit(
        client, candidate_id, edited_fields={"dedup_verdict": "NEW"}, actor_id="analyst-1")
    return workbench_service.mark_ready_for_approval(client, candidate_id, actor_id="analyst-1")


# ---- 一覧/詳細/lineage/siblings（role制限なし） --------------------------------

def test_list_workbench_candidates_filters_by_status(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(workbench_api, "_client", client)
    c1 = _seed_candidate(client)
    _advance_to_ready_for_approval(client, c1["candidate_id"])
    _seed_candidate(client)

    result = workbench_api.list_workbench_candidates(workbench_status="ready_for_approval")
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["candidate_id"] == c1["candidate_id"]


def test_get_workbench_item_unknown_candidate_returns_400(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(workbench_api, "_client", client)
    with pytest.raises(HTTPException) as exc_info:
        workbench_api.get_workbench_item("does-not-exist")
    assert exc_info.value.status_code == 400


def test_get_candidate_lineage_returns_candidate(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(workbench_api, "_client", client)
    candidate = _seed_candidate(client)
    result = workbench_api.get_candidate_lineage(candidate["candidate_id"])
    assert result["candidate"]["candidate_id"] == candidate["candidate_id"]


def test_get_sibling_candidates_without_review_cycle_returns_empty(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(workbench_api, "_client", client)
    candidate = _seed_candidate(client)
    result = workbench_api.get_sibling_candidates(candidate["candidate_id"], review_cycle_id="")
    assert result["total"] == 0


# ---- assess-generalization-risk（GETの副作用にしない、専用POST+BackgroundTasks） ----

def test_assess_generalization_risk_requires_azure_client(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(workbench_api, "_client", client)
    monkeypatch.setattr(workbench_api, "_azure_client", None)
    candidate = _seed_candidate(client)
    identity = _identity("analyst")
    with pytest.raises(HTTPException) as exc_info:
        workbench_api.assess_generalization_risk(
            candidate["candidate_id"], workbench_api.ActorRequest(actor_id="analyst-1"),
            BackgroundTasks(), identity)
    assert exc_info.value.status_code == 503


def test_assess_generalization_risk_schedules_one_background_task(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(workbench_api, "_client", client)
    monkeypatch.setattr(workbench_api, "_azure_client", object())
    candidate = _seed_candidate(client)
    identity = _identity("analyst")
    bg = BackgroundTasks()

    result = workbench_api.assess_generalization_risk(
        candidate["candidate_id"], workbench_api.ActorRequest(actor_id="analyst-1"), bg, identity)

    assert result == {"status": "accepted"}
    assert len(bg.tasks) == 1
    assert bg.tasks[0].func is workbench_api._run_risk_assessment_in_background


def test_assess_generalization_risk_unknown_candidate_returns_400(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(workbench_api, "_client", client)
    monkeypatch.setattr(workbench_api, "_azure_client", object())
    identity = _identity("analyst")
    with pytest.raises(HTTPException) as exc_info:
        workbench_api.assess_generalization_risk(
            "does-not-exist", workbench_api.ActorRequest(actor_id="analyst-1"), BackgroundTasks(), identity)
    assert exc_info.value.status_code == 400


def test_run_risk_assessment_in_background_never_raises_on_failure(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(workbench_api, "_client", client)

    def failing_assess(*args, **kwargs):
        raise RuntimeError("LLM error")
    monkeypatch.setattr(workbench_service, "assess_generalization_risk", failing_assess)

    # 例外を投げずに終わること（fire-and-forget、candidateのgeneralization_riskはNULLのまま）
    workbench_api._run_risk_assessment_in_background("some-id", actor_id="analyst-1")


# ---- 編集Chat（submit_human_instruction、BackgroundTasksへ委譲） -----------------

def test_submit_human_instruction_schedules_chat_revision_task(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(workbench_api, "_client", client)
    monkeypatch.setattr(workbench_api, "_azure_client", object())
    candidate = _seed_candidate(client)
    identity = _identity("analyst", identity_id="analyst-1")
    bg = BackgroundTasks()

    turn = workbench_api.submit_human_instruction(
        candidate["candidate_id"], workbench_api.SubmitInstructionRequest(human_instruction="もっと限定的に"),
        bg, identity)

    assert turn["status"] == "pending"
    assert turn["created_by"] == "analyst-1"
    assert len(bg.tasks) == 1
    assert bg.tasks[0].func is workbench_api._run_chat_revision_in_background


def test_submit_human_instruction_requires_azure_client(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(workbench_api, "_client", client)
    monkeypatch.setattr(workbench_api, "_azure_client", None)
    candidate = _seed_candidate(client)
    identity = _identity("analyst")
    with pytest.raises(HTTPException) as exc_info:
        workbench_api.submit_human_instruction(
            candidate["candidate_id"],
            workbench_api.SubmitInstructionRequest(human_instruction="指示"), BackgroundTasks(), identity)
    assert exc_info.value.status_code == 503


def test_submit_human_instruction_empty_instruction_returns_400(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(workbench_api, "_client", client)
    monkeypatch.setattr(workbench_api, "_azure_client", object())
    candidate = _seed_candidate(client)
    identity = _identity("analyst")
    with pytest.raises(HTTPException) as exc_info:
        workbench_api.submit_human_instruction(
            candidate["candidate_id"], workbench_api.SubmitInstructionRequest(human_instruction="   "),
            BackgroundTasks(), identity)
    assert exc_info.value.status_code == 400


# ---- 直接編集・dedup verdict（role制限なし） -----------------------------------

def test_record_human_edit_updates_candidate(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(workbench_api, "_client", client)
    candidate = _seed_candidate(client)
    identity = _identity("analyst", identity_id="analyst-1")

    result = workbench_api.record_human_edit(
        candidate["candidate_id"],
        workbench_api.RecordHumanEditRequest(edited_fields={"generalized_rule": "改訂版ルール"}),
        identity)
    assert result["generalized_rule"] == "改訂版ルール"


def test_record_dedup_verdict_update_requires_target(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(workbench_api, "_client", client)
    candidate = _seed_candidate(client)
    identity = _identity("analyst")
    with pytest.raises(HTTPException) as exc_info:
        workbench_api.record_dedup_verdict(
            candidate["candidate_id"], workbench_api.RecordDedupVerdictRequest(verdict="UPDATE"), identity)
    assert exc_info.value.status_code == 400


# ---- 状態遷移: ready-for-approval / back-to-editing（role制限なし） --------------

def test_mark_ready_for_approval_wrong_state_returns_400(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(workbench_api, "_client", client)
    candidate = _seed_candidate(client)  # まだ pending
    identity = _identity("analyst")
    with pytest.raises(HTTPException) as exc_info:
        workbench_api.mark_ready_for_approval(
            candidate["candidate_id"], workbench_api.ActorRequest(actor_id="analyst-1"), identity)
    assert exc_info.value.status_code == 400


# ---- approve/reject/publish: role authorizationがpayload無視・identity由来であること ----

def test_approve_in_workbench_denies_non_steward_role(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(workbench_api, "_client", client)
    candidate = _seed_candidate(client)
    _advance_to_ready_for_approval(client, candidate["candidate_id"])
    identity = _identity("analyst")  # knowledge_stewardではない

    with pytest.raises(HTTPException) as exc_info:
        workbench_api.approve_in_workbench(
            candidate["candidate_id"], workbench_api.ApproveRequest(reviewer_role="knowledge_steward"),
            identity)
    assert exc_info.value.status_code == 403


def test_approve_in_workbench_ignores_payload_role_uses_identity_role(monkeypatch):
    """payloadでreviewer_role='knowledge_steward'を自己申告しても、実際の認可は
    identity.assigned_role（analyst）で行われ拒否されること——role自己申告を無視する
    既存P1 Identity Attribution Closure方針の継承確認。"""
    client = FakeSupabaseClient()
    monkeypatch.setattr(workbench_api, "_client", client)
    candidate = _seed_candidate(client)
    _advance_to_ready_for_approval(client, candidate["candidate_id"])
    identity = _identity("analyst", identity_id="fake-steward")

    with pytest.raises(HTTPException) as exc_info:
        workbench_api.approve_in_workbench(
            candidate["candidate_id"],
            workbench_api.ApproveRequest(reviewed_by="fake-steward", reviewer_role="knowledge_steward"),
            identity)
    assert exc_info.value.status_code == 403


def test_approve_in_workbench_succeeds_with_correct_role_and_flags_identity_mismatch(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(workbench_api, "_client", client)
    candidate = _seed_candidate(client)
    _advance_to_ready_for_approval(client, candidate["candidate_id"])
    identity = _identity("knowledge_steward", identity_id="real-steward")

    result = workbench_api.approve_in_workbench(
        candidate["candidate_id"],
        workbench_api.ApproveRequest(reviewed_by="someone-else", reviewer_role="knowledge_steward"), identity)

    assert result["workbench_status"] == "approved"
    assert result["reviewed_by"] == "real-steward"
    assert "_reviewed_by_identity_mismatch_warning" in result


def test_publish_from_workbench_denies_non_publisher_role(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(workbench_api, "_client", client)
    candidate = _seed_candidate(client)
    _advance_to_ready_for_approval(client, candidate["candidate_id"])
    workbench_service.approve_in_workbench(
        client, candidate["candidate_id"], reviewed_by="steward-1", reviewer_role="knowledge_steward")
    identity = _identity("analyst")  # expert_reviewer/knowledge_stewardではない

    with pytest.raises(HTTPException) as exc_info:
        workbench_api.publish_from_workbench(
            candidate["candidate_id"], workbench_api.PublishRequest(), identity)
    assert exc_info.value.status_code == 403


def test_publish_from_workbench_blocked_without_visibility_settings(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(workbench_api, "_client", client)
    candidate = _seed_candidate(client)
    _advance_to_ready_for_approval(client, candidate["candidate_id"])
    workbench_service.approve_in_workbench(
        client, candidate["candidate_id"], reviewed_by="steward-1", reviewer_role="knowledge_steward")
    identity = _identity("expert_reviewer", identity_id="reviewer-1")

    with pytest.raises(HTTPException) as exc_info:
        workbench_api.publish_from_workbench(
            candidate["candidate_id"], workbench_api.PublishRequest(), identity)
    assert exc_info.value.status_code == 400


def test_reject_in_workbench_denies_non_steward_role(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(workbench_api, "_client", client)
    candidate = _seed_candidate(client)
    identity = _identity("analyst")

    with pytest.raises(HTTPException) as exc_info:
        workbench_api.reject_in_workbench(
            candidate["candidate_id"], workbench_api.RejectRequest(reason="不要"), identity)
    assert exc_info.value.status_code == 403


def test_reject_in_workbench_succeeds_with_steward_role(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(workbench_api, "_client", client)
    candidate = _seed_candidate(client)
    identity = _identity("knowledge_steward", identity_id="steward-1")

    result = workbench_api.reject_in_workbench(
        candidate["candidate_id"], workbench_api.RejectRequest(reason="不要"), identity)
    assert result["workbench_status"] == "rejected"


# ---- turns/revisions一覧 --------------------------------------------------------

def test_list_turns_and_revisions(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(workbench_api, "_client", client)
    candidate = _seed_candidate(client)
    # create_candidate単体ではrevisionは書かれない（Initial Extraction Revisionは
    # extract_learning_candidates自体が書き込む、A章）。record_human_editで1件追記する。
    workbench_service.record_human_edit(
        client, candidate["candidate_id"], edited_fields={"dedup_verdict": "NEW"}, actor_id="analyst-1")

    revisions = workbench_api.list_revisions(candidate["candidate_id"])
    assert len(revisions["revisions"]) == 1
    turns = workbench_api.list_turns(candidate["candidate_id"])
    assert turns["turns"] == []
