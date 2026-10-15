"""Expert Session Capture V1（Phase S2-S3）のservice層テスト。

既存tests/test_geo_ios_review_platform_p0_remediation.pyの流儀（FakeSupabaseClient直呼び、
新しいtest依存は増やさない）を踏襲する。重点はBlind Integrity（Calibration Lock/Reveal Gate、
response_id非公開）とIdentity Isolation（expert間でrunが混ざらないこと）。
"""
import pytest

import geo_expert_session_capture_service as capture_service
from tests._fakes import FakeSupabaseClient


def _client():
    return FakeSupabaseClient(tables={
        "geo_expert_session_case_runs": [],
        "geo_expert_session_evaluations": [],
        "geo_audit_logs": [],
    })


# ---- Case Registry ------------------------------------------------------------

def test_case_registry_has_exactly_the_s0_confirmed_seven_runnable_cases():
    registry = capture_service.list_case_definitions()
    assert set(registry["runnable"].keys()) == {
        "gold_hormuz", "gold_us_china_semicon", "gold_nato", "gold_saudi_israel", "gold_tsmc",
        "calib_takaichi", "calib_scs",
    }
    assert set(registry["unresolved"].keys()) == {"gold_uae_saudi_qatar"}


def test_unresolved_case_has_no_response_id():
    assert "response_id" not in capture_service.UNRESOLVED_CASES["gold_uae_saudi_qatar"]


def test_get_case_definition_unknown_key_raises():
    with pytest.raises(capture_service.ExpertSessionCaptureError):
        capture_service.get_case_definition("gold_uae_saudi_qatar")  # unresolved, not in CASE_REGISTRY
    with pytest.raises(capture_service.ExpertSessionCaptureError):
        capture_service.get_case_definition("not_a_real_case")


# ---- start_run idempotency -----------------------------------------------------

def test_start_run_is_idempotent_per_identity():
    client = _client()
    first = capture_service.start_run(client, "gold_nato", expert_identity_id="expert-a")
    second = capture_service.start_run(client, "gold_nato", expert_identity_id="expert-a")
    assert first["run_id"] == second["run_id"]
    assert len(client.tables["geo_expert_session_case_runs"]) == 1


def test_start_run_identity_isolation_two_experts_same_case():
    client = _client()
    run_a = capture_service.start_run(client, "gold_nato", expert_identity_id="expert-a")
    run_b = capture_service.start_run(client, "gold_nato", expert_identity_id="expert-b")
    assert run_a["run_id"] != run_b["run_id"]
    assert capture_service.get_run(client, "gold_nato", "expert-a")["run_id"] == run_a["run_id"]
    assert capture_service.get_run(client, "gold_nato", "expert-b")["run_id"] == run_b["run_id"]


# ---- Calibration: Expert First Input / Lock ------------------------------------

def test_save_expert_first_input_rejects_on_non_calibration_case():
    client = _client()
    capture_service.start_run(client, "gold_nato", expert_identity_id="expert-a")
    with pytest.raises(capture_service.ExpertSessionCaptureError):
        capture_service.save_expert_first_input(
            client, "gold_nato", expert_identity_id="expert-a",
            expert_first_input={"situation": "x"})


def test_lock_requires_three_mandatory_fields():
    client = _client()
    capture_service.start_run(client, "calib_takaichi", expert_identity_id="expert-a")
    capture_service.save_expert_first_input(
        client, "calib_takaichi", expert_identity_id="expert-a",
        expert_first_input={"situation": "s"})
    with pytest.raises(capture_service.ExpertSessionCaptureError, match="what_to_look_at"):
        capture_service.lock_run(client, "calib_takaichi", expert_identity_id="expert-a")


def test_lock_succeeds_once_required_fields_present_and_sets_locked_at():
    client = _client()
    capture_service.start_run(client, "calib_takaichi", expert_identity_id="expert-a")
    capture_service.save_expert_first_input(
        client, "calib_takaichi", expert_identity_id="expert-a",
        expert_first_input={"situation": "s", "what_to_look_at": "w", "primary_reading": "p"})
    run = capture_service.lock_run(client, "calib_takaichi", expert_identity_id="expert-a")
    assert run["locked_at"] is not None
    assert run["status"] == "locked"


def test_expert_first_input_rejected_after_lock_server_side():
    """§8最重要Gate: Frontend Button disabledに依存しない、server-sideでの拒否。"""
    client = _client()
    capture_service.start_run(client, "calib_takaichi", expert_identity_id="expert-a")
    capture_service.save_expert_first_input(
        client, "calib_takaichi", expert_identity_id="expert-a",
        expert_first_input={"situation": "s", "what_to_look_at": "w", "primary_reading": "p"})
    capture_service.lock_run(client, "calib_takaichi", expert_identity_id="expert-a")
    with pytest.raises(capture_service.ExpertSessionCaptureForbiddenError):
        capture_service.save_expert_first_input(
            client, "calib_takaichi", expert_identity_id="expert-a",
            expert_first_input={"situation": "changed after lock"})
    # 実際に書き換わっていないことも確認する
    run = capture_service.get_run(client, "calib_takaichi", "expert-a")
    assert run["expert_first_input"]["situation"] == "s"


def test_lock_is_idempotent_does_not_overwrite_locked_at():
    client = _client()
    capture_service.start_run(client, "calib_takaichi", expert_identity_id="expert-a")
    capture_service.save_expert_first_input(
        client, "calib_takaichi", expert_identity_id="expert-a",
        expert_first_input={"situation": "s", "what_to_look_at": "w", "primary_reading": "p"})
    first = capture_service.lock_run(client, "calib_takaichi", expert_identity_id="expert-a")
    second = capture_service.lock_run(client, "calib_takaichi", expert_identity_id="expert-a")
    assert first["locked_at"] == second["locked_at"]


# ---- Reveal Gate ---------------------------------------------------------------

def test_reveal_rejected_before_lock():
    client = _client()
    capture_service.start_run(client, "calib_takaichi", expert_identity_id="expert-a")
    with pytest.raises(capture_service.ExpertSessionCaptureForbiddenError):
        capture_service.reveal_run(client, "calib_takaichi", expert_identity_id="expert-a")


def test_reveal_succeeds_after_lock_and_is_idempotent():
    client = _client()
    capture_service.start_run(client, "calib_takaichi", expert_identity_id="expert-a")
    capture_service.save_expert_first_input(
        client, "calib_takaichi", expert_identity_id="expert-a",
        expert_first_input={"situation": "s", "what_to_look_at": "w", "primary_reading": "p"})
    capture_service.lock_run(client, "calib_takaichi", expert_identity_id="expert-a")
    first = capture_service.reveal_run(client, "calib_takaichi", expert_identity_id="expert-a")
    second = capture_service.reveal_run(client, "calib_takaichi", expert_identity_id="expert-a")
    assert first["revealed_at"] == second["revealed_at"]
    assert first["status"] == "revealed"


# ---- Blind Integrity: build_public_case_view -----------------------------------

def test_gold_case_always_exposes_response_id():
    case_def = capture_service.get_case_definition("gold_nato")
    view = capture_service.build_public_case_view("gold_nato", case_def, run=None)
    assert "response_id" in view
    assert view["response_id"] == case_def["response_id"]


def test_calibration_case_hides_response_id_before_reveal():
    case_def = capture_service.get_case_definition("calib_takaichi")
    for run in (
        None,
        {"status": "in_progress", "locked_at": None, "revealed_at": None},
        {"status": "locked", "locked_at": "2026-10-02T00:00:00Z", "revealed_at": None},
    ):
        view = capture_service.build_public_case_view("calib_takaichi", case_def, run)
        assert "response_id" not in view, f"leaked for run={run}"


def test_calibration_case_exposes_response_id_only_after_reveal():
    case_def = capture_service.get_case_definition("calib_takaichi")
    run = {"status": "revealed", "locked_at": "2026-10-02T00:00:00Z",
           "revealed_at": "2026-10-02T00:05:00Z"}
    view = capture_service.build_public_case_view("calib_takaichi", case_def, run)
    assert view["response_id"] == case_def["response_id"]


# ---- Gap Review Gate ------------------------------------------------------------

def test_gap_review_rejected_before_reveal():
    client = _client()
    capture_service.start_run(client, "calib_takaichi", expert_identity_id="expert-a")
    capture_service.save_expert_first_input(
        client, "calib_takaichi", expert_identity_id="expert-a",
        expert_first_input={"situation": "s", "what_to_look_at": "w", "primary_reading": "p"})
    capture_service.lock_run(client, "calib_takaichi", expert_identity_id="expert-a")
    with pytest.raises(capture_service.ExpertSessionCaptureForbiddenError):
        capture_service.save_evaluation(
            client, "calib_takaichi", expert_identity_id="expert-a",
            gap_review={"how_to_fix_ai": "too early"})


def test_gap_review_allowed_after_reveal_and_persists():
    client = _client()
    capture_service.start_run(client, "calib_takaichi", expert_identity_id="expert-a")
    capture_service.save_expert_first_input(
        client, "calib_takaichi", expert_identity_id="expert-a",
        expert_first_input={"situation": "s", "what_to_look_at": "w", "primary_reading": "p"})
    capture_service.lock_run(client, "calib_takaichi", expert_identity_id="expert-a")
    capture_service.reveal_run(client, "calib_takaichi", expert_identity_id="expert-a")
    capture_service.save_evaluation(
        client, "calib_takaichi", expert_identity_id="expert-a",
        gap_review={"how_to_fix_ai": "add more evidence"})
    run = capture_service.get_run(client, "calib_takaichi", "expert-a")
    evaluation = capture_service.get_evaluation(client, run["run_id"])
    assert evaluation["gap_review"]["how_to_fix_ai"] == "add more evidence"


def test_evaluation_partial_updates_merge_not_overwrite():
    client = _client()
    capture_service.start_run(client, "gold_nato", expert_identity_id="expert-a")
    capture_service.save_evaluation(
        client, "gold_nato", expert_identity_id="expert-a",
        rubric_assessments={"situation_understanding": {"rating": "valid"}})
    capture_service.save_evaluation(
        client, "gold_nato", expert_identity_id="expert-a",
        rubric_assessments={"initial_cue": {"rating": "needs_minor_fix"}})
    run = capture_service.get_run(client, "gold_nato", "expert-a")
    evaluation = capture_service.get_evaluation(client, run["run_id"])
    assert evaluation["rubric_assessments"]["situation_understanding"]["rating"] == "valid"
    assert evaluation["rubric_assessments"]["initial_cue"]["rating"] == "needs_minor_fix"


# ---- Submit ----------------------------------------------------------------------

def test_calibration_submit_rejected_before_revealed():
    client = _client()
    capture_service.start_run(client, "calib_takaichi", expert_identity_id="expert-a")
    with pytest.raises(capture_service.ExpertSessionCaptureError):
        capture_service.submit_run(client, "calib_takaichi", expert_identity_id="expert-a")


def test_calibration_submit_succeeds_after_revealed():
    client = _client()
    capture_service.start_run(client, "calib_takaichi", expert_identity_id="expert-a")
    capture_service.save_expert_first_input(
        client, "calib_takaichi", expert_identity_id="expert-a",
        expert_first_input={"situation": "s", "what_to_look_at": "w", "primary_reading": "p"})
    capture_service.lock_run(client, "calib_takaichi", expert_identity_id="expert-a")
    capture_service.reveal_run(client, "calib_takaichi", expert_identity_id="expert-a")
    run = capture_service.submit_run(client, "calib_takaichi", expert_identity_id="expert-a")
    assert run["status"] == "completed"
    assert run["submitted_at"] is not None


def test_gold_submit_does_not_require_lock_or_reveal():
    client = _client()
    capture_service.start_run(client, "gold_nato", expert_identity_id="expert-a")
    run = capture_service.submit_run(client, "gold_nato", expert_identity_id="expert-a")
    assert run["status"] == "completed"
