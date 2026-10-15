import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_reassessment_service as reassess  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _base_client(**extra_tables) -> FakeSupabaseClient:
    tables = {"geo_audit_logs": []}
    tables.update(extra_tables)
    return FakeSupabaseClient(tables)


def _mock_llm_client(response: dict) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(response)))],
    )
    return client


REPORT_STUDIO_GAP = {
    "gap_id": "gap-1", "requested_by_app": "report_studio",
    "source_context": {"app": "report_studio", "project_id": "proj-1", "run_id": "run-1",
                        "signal_id": "S1", "narrative_id": "S1-N01", "synthesis_id": "synth-1"},
}

PRIOR_SYNTHESIS = {
    "id": "synth-1",
    "final_draft": {
        "central_assessment": {"summary": "強硬姿勢を継続中"},
        "continuity_assessment": {"state": "Continuing", "rationale": "..."},
    },
}


# ---------------------------------------------------------------------------
# is_report_studio_sourced
# ---------------------------------------------------------------------------

def test_is_report_studio_sourced_true_when_all_refs_present():
    assert reassess.is_report_studio_sourced(REPORT_STUDIO_GAP) is True


def test_is_report_studio_sourced_false_when_not_requested_by_report_studio():
    gap = {**REPORT_STUDIO_GAP, "requested_by_app": "sustainability"}
    assert reassess.is_report_studio_sourced(gap) is False


def test_is_report_studio_sourced_false_when_missing_run_id():
    gap = {**REPORT_STUDIO_GAP, "source_context": {**REPORT_STUDIO_GAP["source_context"], "run_id": None}}
    assert reassess.is_report_studio_sourced(gap) is False


def test_is_report_studio_sourced_false_when_no_source_context():
    gap = {"gap_id": "gap-2", "requested_by_app": "report_studio"}
    assert reassess.is_report_studio_sourced(gap) is False


# ---------------------------------------------------------------------------
# run_materiality_check
# ---------------------------------------------------------------------------

def test_run_materiality_check_returns_llm_judgement():
    mock_client = _mock_llm_client({
        "materially_changes_assessment": True, "rationale": "新しい公式声明が確認された", "confidence": "高",
    })
    result = reassess.run_materiality_check(
        mock_client, "gpt-4o", prior_central_assessment="強硬姿勢を継続中",
        prior_continuity_state="Continuing", new_evidence_summary="大統領が検証条件を撤回")
    assert result["materially_changes_assessment"] is True
    assert mock_client.chat.completions.create.called


# ---------------------------------------------------------------------------
# run_reassessment_gate — オーケストレーション
# ---------------------------------------------------------------------------

def test_run_reassessment_gate_skips_when_no_prior_synthesis():
    client = _base_client()
    mock_llm = _mock_llm_client({"materially_changes_assessment": True, "rationale": "x", "confidence": "高"})

    result = reassess.run_reassessment_gate(
        client, mock_llm, "gpt-4o", gap=REPORT_STUDIO_GAP, new_evidence_summary="x",
        get_latest_synthesis=lambda *a, **k: None,
        run_synthesize_narrative=lambda *a, **k: {"synthesis_id": "should-not-be-called"},
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "no_prior_synthesis"
    assert not mock_llm.chat.completions.create.called
    assert len(client.tables["geo_audit_logs"]) == 1
    assert client.tables["geo_audit_logs"][0]["action"] == "report_studio_reassessment_skipped"


def test_run_reassessment_gate_continuity_only_when_not_material():
    client = _base_client()
    mock_llm = _mock_llm_client({
        "materially_changes_assessment": False, "rationale": "件数が増えただけ", "confidence": "中",
    })
    resynth_calls = []

    result = reassess.run_reassessment_gate(
        client, mock_llm, "gpt-4o", gap=REPORT_STUDIO_GAP, new_evidence_summary="同種の記事が数件追加",
        get_latest_synthesis=lambda *a, **k: PRIOR_SYNTHESIS,
        run_synthesize_narrative=lambda *a, **k: resynth_calls.append(a) or {"synthesis_id": "new-synth"},
    )
    assert result["status"] == "continuity_only"
    assert result["rationale"] == "件数が増えただけ"
    assert resynth_calls == []  # フルSynthesisは呼ばれない
    actions = [row["action"] for row in client.tables["geo_audit_logs"]]
    assert "report_studio_reassessment_no_material_change" in actions


def test_run_reassessment_gate_resynthesizes_when_material():
    client = _base_client()
    mock_llm = _mock_llm_client({
        "materially_changes_assessment": True, "rationale": "検証条件を撤回する公式声明", "confidence": "高",
    })
    resynth_calls = []

    def fake_run_synthesize_narrative(run_id, signal_id, narrative_id):
        resynth_calls.append((run_id, signal_id, narrative_id))
        return {"synthesis_id": "new-synth-id", "final_validity_status": "PASS"}

    result = reassess.run_reassessment_gate(
        client, mock_llm, "gpt-4o", gap=REPORT_STUDIO_GAP, new_evidence_summary="大統領が検証条件を撤回すると発表",
        get_latest_synthesis=lambda *a, **k: PRIOR_SYNTHESIS,
        run_synthesize_narrative=fake_run_synthesize_narrative,
    )
    assert result["status"] == "resynthesized"
    assert result["synthesis_id"] == "new-synth-id"
    assert result["final_validity_status"] == "PASS"
    assert resynth_calls == [("run-1", "S1", "S1-N01")]
    actions = [row["action"] for row in client.tables["geo_audit_logs"]]
    assert "report_studio_reassessment_resynthesized" in actions


def test_run_reassessment_gate_catches_errors_without_raising():
    client = _base_client()

    def boom(*a, **k):
        raise RuntimeError("azure timeout")

    result = reassess.run_reassessment_gate(
        client, MagicMock(), "gpt-4o", gap=REPORT_STUDIO_GAP, new_evidence_summary="x",
        get_latest_synthesis=boom,
        run_synthesize_narrative=lambda *a, **k: {"synthesis_id": "unused"},
    )
    assert result["status"] == "error"
    assert "azure timeout" in result["error"]
    actions = [row["action"] for row in client.tables["geo_audit_logs"]]
    assert "report_studio_reassessment_error" in actions


# ---------------------------------------------------------------------------
# build_new_evidence_summary
# ---------------------------------------------------------------------------

def test_build_new_evidence_summary_combines_note_title_and_excerpt():
    summary = reassess.build_new_evidence_summary(
        resolution_note="大統領府が検証条件を撤回",
        document_title="Presidential Statement on Verification",
        document_text="Full text of the statement..." + "x" * 3000,
        char_budget=50,
    )
    assert "大統領府が検証条件を撤回" in summary
    assert "Presidential Statement on Verification" in summary
    assert len(summary) < 3200  # char_budgetで本文が切り詰められている


def test_build_new_evidence_summary_empty_when_nothing_provided():
    assert reassess.build_new_evidence_summary(
        resolution_note=None, document_title=None, document_text=None) == ""
