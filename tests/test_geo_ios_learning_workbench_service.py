"""Geopolitical Intelligence OS: Learning Workbench Serviceのテスト。

3回のPlan Modeレビューで確定した仕様（状態遷移のService層強制、Dedup verdict 5値の
実動作、Publishの冪等性・partial failure再開可能性、Revision楽観ロック）を、
FakeSupabaseClientで検証する。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_learning_candidate_service as learning_service  # noqa: E402
import geo_ios_learning_workbench_service as workbench_service  # noqa: E402
import geo_knowledge_service  # noqa: E402
import geo_llm_common  # noqa: E402
import geo_role_guard  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

STEWARD = "steward-1"
STEWARD_ROLE = "knowledge_steward"
REVIEWER = "reviewer-1"
REVIEWER_ROLE = "expert_reviewer"


def _valid_reasoning_rule_content(**overrides) -> dict:
    content = {
        "rule": "旧ルール", "rationale": "旧根拠", "applicable_question_types": ["general"],
        "required_checks": [], "comparison_dimensions": [], "common_failure_modes": [],
        "expert_source": "test-fixture", "approval_status": "human_approved",
        "rule_category": "general",
    }
    content.update(overrides)
    return content


def _seed_candidate(client, **overrides) -> dict:
    row = learning_service.create_candidate(
        client, candidate_type="reasoning_rule", observation="初期AIは議会を一枚岩として扱っていた",
        generalized_rule="米国政策分析では議会をSenateとHouseへ分けて評価する",
        extraction_model="gpt-test", trigger_context={"title": "t", "source_comment_ids": []},
        scope="domain_general", confidence="中", actor_id="geo_learning_extraction")
    candidate_id = row["candidate_id"]
    learning_service.record_revision(
        client, candidate_id, revised_by="ai", revision_snapshot=workbench_service._current_snapshot(row),
        change_summary="Initial extraction", created_by="geo_learning_extraction")
    if overrides:
        client.update("geo_ios_learning_candidates", {"candidate_id": f"eq.{candidate_id}"}, overrides)
        row = learning_service.get_candidate(client, candidate_id)
    return row


def _advance_to_ready_for_approval(client, candidate_id: str) -> None:
    workbench_service.record_human_edit(
        client, candidate_id, edited_fields={"observation": "更新済み"}, actor_id="analyst-1")
    workbench_service.mark_ready_for_approval(client, candidate_id, actor_id="analyst-1")


def _approve(client, candidate_id: str) -> dict:
    _advance_to_ready_for_approval(client, candidate_id)
    return workbench_service.approve_in_workbench(
        client, candidate_id, reviewed_by=STEWARD, reviewer_role=STEWARD_ROLE)


# ---- 状態遷移 ------------------------------------------------------------------

def test_record_human_edit_transitions_pending_to_editing_and_writes_revision():
    client = FakeSupabaseClient()
    candidate = _seed_candidate(client)
    assert candidate["workbench_status"] == "pending"

    updated = workbench_service.record_human_edit(
        client, candidate["candidate_id"], edited_fields={"scope": "actor_specific"},
        actor_id="analyst-1")
    assert updated["workbench_status"] == "editing"
    assert updated["scope"] == "actor_specific"
    revisions = workbench_service.list_revisions(client, candidate["candidate_id"])
    assert [r["revision_number"] for r in revisions] == [1, 2]
    assert revisions[-1]["revised_by"] == "human"


def test_mark_ready_for_approval_requires_editing_status():
    client = FakeSupabaseClient()
    candidate = _seed_candidate(client)  # まだpending
    with pytest.raises(workbench_service.WorkbenchError):
        workbench_service.mark_ready_for_approval(client, candidate["candidate_id"], actor_id="analyst-1")


def test_approve_in_workbench_requires_ready_for_approval_status():
    client = FakeSupabaseClient()
    candidate = _seed_candidate(client)
    with pytest.raises(workbench_service.WorkbenchError):
        workbench_service.approve_in_workbench(
            client, candidate["candidate_id"], reviewed_by=STEWARD, reviewer_role=STEWARD_ROLE)


def test_approve_in_workbench_requires_knowledge_steward_role():
    client = FakeSupabaseClient()
    candidate = _seed_candidate(client)
    _advance_to_ready_for_approval(client, candidate["candidate_id"])
    with pytest.raises(geo_role_guard.RoleDeniedError):
        workbench_service.approve_in_workbench(
            client, candidate["candidate_id"], reviewed_by=REVIEWER, reviewer_role=REVIEWER_ROLE)


def test_approve_in_workbench_sets_both_status_axes():
    client = FakeSupabaseClient()
    candidate = _seed_candidate(client)
    approved = _approve(client, candidate["candidate_id"])
    assert approved["workbench_status"] == "approved"
    assert approved["status"] == "approved"  # 既存Gate#1のstatus列も連動している


def test_reject_in_workbench_blocked_after_published(monkeypatch):
    client = FakeSupabaseClient()
    candidate = _seed_candidate(client, dedup_verdict="NEW",
                                 human_visibility="company", agent_sharing_policy="geopolitics_only")
    _approve(client, candidate["candidate_id"])
    workbench_service.publish_from_workbench(
        client, candidate["candidate_id"], publisher=REVIEWER, publisher_role=REVIEWER_ROLE)
    with pytest.raises(workbench_service.WorkbenchError):
        workbench_service.reject_in_workbench(
            client, candidate["candidate_id"], reviewed_by=STEWARD, reviewer_role=STEWARD_ROLE)


# ---- Dedup Verdict --------------------------------------------------------------

def test_record_dedup_verdict_update_requires_target():
    client = FakeSupabaseClient()
    candidate = _seed_candidate(client)
    with pytest.raises(workbench_service.WorkbenchError):
        workbench_service.record_dedup_verdict(
            client, candidate["candidate_id"], verdict="UPDATE", actor_id="analyst-1")


def test_record_dedup_verdict_conflict_requires_resolution_note_to_change():
    client = FakeSupabaseClient()
    candidate = _seed_candidate(client)
    workbench_service.record_dedup_verdict(
        client, candidate["candidate_id"], verdict="CONFLICT", actor_id="analyst-1")
    with pytest.raises(workbench_service.WorkbenchError):
        workbench_service.record_dedup_verdict(
            client, candidate["candidate_id"], verdict="NEW", actor_id="analyst-1")
    # noteを付ければ変更できる
    updated = workbench_service.record_dedup_verdict(
        client, candidate["candidate_id"], verdict="NEW", resolution_note="精査した結果別物と判断",
        actor_id="analyst-1")
    assert updated["dedup_verdict"] == "NEW"


# ---- Publish ----------------------------------------------------------------

def test_publish_blocked_when_dedup_verdict_duplicate_or_conflict():
    client = FakeSupabaseClient()
    for verdict in ("DUPLICATE", "CONFLICT"):
        candidate = _seed_candidate(
            client, dedup_verdict=verdict, human_visibility="company",
            agent_sharing_policy="geopolitics_only")
        _approve(client, candidate["candidate_id"])
        with pytest.raises(workbench_service.WorkbenchError):
            workbench_service.publish_from_workbench(
                client, candidate["candidate_id"], publisher=REVIEWER, publisher_role=REVIEWER_ROLE)


def test_publish_blocked_without_visibility_settings():
    client = FakeSupabaseClient()
    candidate = _seed_candidate(client, dedup_verdict="NEW")  # visibility未設定
    _approve(client, candidate["candidate_id"])
    with pytest.raises(workbench_service.WorkbenchError):
        workbench_service.publish_from_workbench(
            client, candidate["candidate_id"], publisher=REVIEWER, publisher_role=REVIEWER_ROLE)


def test_publish_requires_reviewer_role():
    client = FakeSupabaseClient()
    candidate = _seed_candidate(client, dedup_verdict="NEW",
                                 human_visibility="company", agent_sharing_policy="geopolitics_only")
    _approve(client, candidate["candidate_id"])
    with pytest.raises(geo_role_guard.RoleDeniedError):
        workbench_service.publish_from_workbench(
            client, candidate["candidate_id"], publisher="nobody", publisher_role="analyst")


def test_publish_new_verdict_reaches_active_and_available():
    client = FakeSupabaseClient()
    candidate = _seed_candidate(client, dedup_verdict="NEW",
                                 human_visibility="private", agent_sharing_policy="none")
    _approve(client, candidate["candidate_id"])
    published = workbench_service.publish_from_workbench(
        client, candidate["candidate_id"], publisher=REVIEWER, publisher_role=REVIEWER_ROLE)
    assert published["workbench_status"] == "published"
    knowledge_item = geo_knowledge_service.get_knowledge_item(client, published["approved_knowledge_item_id"])
    assert knowledge_item["status"] == "active"
    assert knowledge_item["retrieval_status"] == "available"
    assert knowledge_item["human_visibility"] == "private"
    assert knowledge_item["agent_sharing_policy"] == "none"


def test_publish_is_idempotent_does_not_create_second_knowledge_item():
    """workbench_status='published'は終端であり、通常の運用では再度publishできない
    （state machineとして正しい）。冪等性が効くのは、Step Aは成功したがworkbench_statusが
    何らかの理由でapprovedのまま留まった場合の再試行——それをapproved_knowledge_item_id
    が既に設定された状態からもう一度publishを呼ぶことで再現する。"""
    client = FakeSupabaseClient()
    candidate = _seed_candidate(client, dedup_verdict="NEW",
                                 human_visibility="company", agent_sharing_policy="geopolitics_only")
    _approve(client, candidate["candidate_id"])
    first = workbench_service.publish_from_workbench(
        client, candidate["candidate_id"], publisher=REVIEWER, publisher_role=REVIEWER_ROLE)
    assert first["workbench_status"] == "published"
    # partial failureでworkbench_statusがapprovedのまま留まったケースを模す
    # （approved_knowledge_item_idは既に確定済み）。
    client.update("geo_ios_learning_candidates", {"candidate_id": f"eq.{candidate['candidate_id']}"},
                  {"workbench_status": "approved"})
    second = workbench_service.publish_from_workbench(
        client, candidate["candidate_id"], publisher=REVIEWER, publisher_role=REVIEWER_ROLE)
    assert first["approved_knowledge_item_id"] == second["approved_knowledge_item_id"]
    all_items = client.select("geo_knowledge_items", {"select": "knowledge_item_id"})
    assert len(all_items) == 1


def test_publish_update_verdict_supersedes_old_knowledge_after_new_active():
    client = FakeSupabaseClient()
    old_item = geo_knowledge_service.create_knowledge_item(
        client, knowledge_type="reasoning_rule",
        structured_content=_valid_reasoning_rule_content(),
        information_class="internal_hypothesis", created_by=STEWARD, title="旧ルール")
    client.update("geo_knowledge_items", {"knowledge_item_id": f"eq.{old_item['knowledge_item_id']}"},
                  {"status": "active", "retrieval_status": "available"})

    candidate = _seed_candidate(
        client, dedup_verdict="UPDATE", dedup_target_knowledge_item_id=old_item["knowledge_item_id"],
        human_visibility="company", agent_sharing_policy="geopolitics_only")
    _approve(client, candidate["candidate_id"])
    published = workbench_service.publish_from_workbench(
        client, candidate["candidate_id"], publisher=REVIEWER, publisher_role=REVIEWER_ROLE)

    new_item = geo_knowledge_service.get_knowledge_item(client, published["approved_knowledge_item_id"])
    assert new_item["status"] == "active"
    assert new_item["supersedes_id"] == old_item["knowledge_item_id"]
    refreshed_old = geo_knowledge_service.get_knowledge_item(client, old_item["knowledge_item_id"])
    assert refreshed_old["status"] == "superseded"
    assert refreshed_old["retrieval_status"] == "deprecated"


def test_publish_update_verdict_new_activation_failure_leaves_old_untouched(monkeypatch):
    client = FakeSupabaseClient()
    old_item = geo_knowledge_service.create_knowledge_item(
        client, knowledge_type="reasoning_rule",
        structured_content=_valid_reasoning_rule_content(),
        information_class="internal_hypothesis", created_by=STEWARD, title="旧ルール")
    client.update("geo_knowledge_items", {"knowledge_item_id": f"eq.{old_item['knowledge_item_id']}"},
                  {"status": "active", "retrieval_status": "available"})
    candidate = _seed_candidate(
        client, dedup_verdict="UPDATE", dedup_target_knowledge_item_id=old_item["knowledge_item_id"],
        human_visibility="company", agent_sharing_policy="geopolitics_only")
    _approve(client, candidate["candidate_id"])

    original_review = geo_knowledge_service.review_knowledge_item

    def _fail_on_approve_active(client_, **kwargs):
        if kwargs.get("review_decision") == "approve_active":
            raise RuntimeError("simulated activation failure")
        return original_review(client_, **kwargs)

    monkeypatch.setattr(geo_knowledge_service, "review_knowledge_item", _fail_on_approve_active)
    with pytest.raises(RuntimeError):
        workbench_service.publish_from_workbench(
            client, candidate["candidate_id"], publisher=REVIEWER, publisher_role=REVIEWER_ROLE)

    refreshed_old = geo_knowledge_service.get_knowledge_item(client, old_item["knowledge_item_id"])
    assert refreshed_old["status"] == "active"  # Newが失敗した間はOldに一切触れない
    refreshed_candidate = learning_service.get_candidate(client, candidate["candidate_id"])
    assert refreshed_candidate["workbench_status"] == "approved"  # publishedへは進まない


# ---- get_workbench_item / Backfill ----------------------------------------------

def test_get_workbench_item_backfills_revision_for_pre_workbench_candidate():
    client = FakeSupabaseClient()
    row = learning_service.create_candidate(
        client, candidate_type="reasoning_rule", observation="obs", generalized_rule="rule",
        extraction_model="gpt-test", trigger_context={}, scope="general", confidence="中",
        actor_id="geo_learning_extraction")
    # record_revisionを呼ばない＝Workbench導入前の既存candidateを模す
    item = workbench_service.get_workbench_item(client, row["candidate_id"])
    assert len(item["revisions"]) == 1
    assert item["revisions"][0]["change_summary"] == "Backfilled from pre-Workbench state"


# ---- 編集Chat（楽観ロック） -------------------------------------------------------

def _fake_llm_result(**overrides) -> dict:
    data = {"ai_response_text": "了解しました、修正しました", "generalized_rule": "更新後のルール",
            "observation": "更新後の観察", "scope": "domain_general", "confidence": "中",
            "change_summary": "人間の指示に従いscopeを維持した"}
    data.update(overrides)
    return {"data": data, "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def test_run_chat_revision_applies_update_and_completes_turn(monkeypatch):
    client = FakeSupabaseClient()
    candidate = _seed_candidate(client)
    turn = workbench_service.submit_human_instruction(
        client, candidate["candidate_id"], human_instruction="もっと具体的にして",
        actor_id="analyst-1")
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", lambda *a, **kw: _fake_llm_result())

    workbench_service.run_chat_revision(
        client, azure_client=object(), model="gpt-test", candidate_id=candidate["candidate_id"],
        workbench_turn_id=turn["workbench_turn_id"], human_instruction="もっと具体的にして",
        actor_id="analyst-1")

    refreshed_turn = client.select("geo_ios_learning_workbench_turns", {
        "select": "*", "workbench_turn_id": f"eq.{turn['workbench_turn_id']}"})[0]
    assert refreshed_turn["status"] == "completed"
    refreshed_candidate = learning_service.get_candidate(client, candidate["candidate_id"])
    assert refreshed_candidate["generalized_rule"] == "更新後のルール"


def test_run_chat_revision_detects_staleness_and_does_not_overwrite(monkeypatch):
    client = FakeSupabaseClient()
    candidate = _seed_candidate(client)
    turn = workbench_service.submit_human_instruction(
        client, candidate["candidate_id"], human_instruction="もっと具体的にして",
        actor_id="analyst-1")
    # AI完了前に別の直接編集が先に入る（競合）
    workbench_service.record_human_edit(
        client, candidate["candidate_id"], edited_fields={"observation": "先に人間が編集した"},
        actor_id="analyst-2")

    monkeypatch.setattr(geo_llm_common, "call_llm_structured", lambda *a, **kw: _fake_llm_result())
    workbench_service.run_chat_revision(
        client, azure_client=object(), model="gpt-test", candidate_id=candidate["candidate_id"],
        workbench_turn_id=turn["workbench_turn_id"], human_instruction="もっと具体的にして",
        actor_id="analyst-1")

    refreshed_turn = client.select("geo_ios_learning_workbench_turns", {
        "select": "*", "workbench_turn_id": f"eq.{turn['workbench_turn_id']}"})[0]
    assert refreshed_turn["status"] == "stale"
    refreshed_candidate = learning_service.get_candidate(client, candidate["candidate_id"])
    assert refreshed_candidate["observation"] == "先に人間が編集した"  # 上書きされていない


def test_run_chat_revision_marks_failed_on_llm_error(monkeypatch):
    client = FakeSupabaseClient()
    candidate = _seed_candidate(client)
    turn = workbench_service.submit_human_instruction(
        client, candidate["candidate_id"], human_instruction="もっと具体的にして",
        actor_id="analyst-1")

    def _boom(*a, **kw):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _boom)
    workbench_service.run_chat_revision(
        client, azure_client=object(), model="gpt-test", candidate_id=candidate["candidate_id"],
        workbench_turn_id=turn["workbench_turn_id"], human_instruction="もっと具体的にして",
        actor_id="analyst-1")

    refreshed_turn = client.select("geo_ios_learning_workbench_turns", {
        "select": "*", "workbench_turn_id": f"eq.{turn['workbench_turn_id']}"})[0]
    assert refreshed_turn["status"] == "failed"
    assert "LLM down" in refreshed_turn["error_message"]


# ---- Generalization Risk Assessment ---------------------------------------------

def test_assess_generalization_risk_writes_second_revision(monkeypatch):
    client = FakeSupabaseClient()
    candidate = _seed_candidate(client)
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", lambda *a, **kw: {
        "data": {"generalization_risk": "medium", "generalization_risk_reason": "特定局面に依存"},
        "usage": {}})
    updated = workbench_service.assess_generalization_risk(
        client, azure_client=object(), model="gpt-test", candidate_id=candidate["candidate_id"],
        actor_id="geo_learning_workbench")
    assert updated["generalization_risk"] == "medium"
    revisions = workbench_service.list_revisions(client, candidate["candidate_id"])
    assert [r["revision_number"] for r in revisions] == [1, 2]
    assert revisions[-1]["change_summary"] == "Generalization risk assessment"
