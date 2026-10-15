import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_run_cancellation_service as cancel  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _seed_run(client, *, cancel_requested=False):
    client.insert("geo_report_collection_runs", [{
        "run_id": "run-1", "project_id": "proj-1", "cancel_requested": cancel_requested,
    }])


def test_is_cancel_requested_defaults_false():
    client = FakeSupabaseClient()
    _seed_run(client)
    assert cancel.is_cancel_requested(client, "run-1") is False


def test_request_cancel_sets_flag():
    client = FakeSupabaseClient()
    _seed_run(client)
    cancel.request_cancel(client, "run-1")
    assert cancel.is_cancel_requested(client, "run-1") is True


def test_clear_cancel_flag_resets():
    client = FakeSupabaseClient()
    _seed_run(client, cancel_requested=True)
    cancel.clear_cancel_flag(client, "run-1")
    assert cancel.is_cancel_requested(client, "run-1") is False


def test_raise_if_cancelled_raises_when_flagged():
    client = FakeSupabaseClient()
    _seed_run(client, cancel_requested=True)
    try:
        cancel.raise_if_cancelled(client, "run-1")
        assert False, "should have raised"
    except cancel.PipelineCancelled:
        pass


def test_raise_if_cancelled_noop_when_not_flagged():
    client = FakeSupabaseClient()
    _seed_run(client, cancel_requested=False)
    cancel.raise_if_cancelled(client, "run-1")  # 例外が出なければOK
