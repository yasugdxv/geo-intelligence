import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_report_edit_service as edit_svc  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

RUN_ID = "run-1"
TEMPLATE_ASSET_ID = "template-1"

_TEMPLATE_CONTENT = {
    "style_css": "<style>.x{}</style>", "shell_html": "<html><head>{style}</head><body>{sections}</body></html>",
    "sections": [
        {"section_id": "exec-summary", "title": "Executive Summary", "order": 1,
         "component_type": "narrative_block", "required_fields": ["central_conclusion"]},
        {"section_id": "georgia", "title": "Georgia", "order": 2,
         "component_type": "narrative_block", "required_fields": ["central_conclusion"]},
    ],
}

_EXEC_SUMMARY_COMPOSED = {
    "central_conclusion": "Overall stable.", "change_from_previous": "No major shift.",
    "drivers": ["driver A"], "confidence_note": "moderate",
}
_GEORGIA_COMPOSED = {
    "central_conclusion": "Georgia is close.", "change_from_previous": "Tightening.",
    "drivers": ["polling"], "confidence_note": "moderate",
}


def _seed_run_and_template(client):
    client.insert("geo_report_project_assets", [{
        "id": TEMPLATE_ASSET_ID, "content": _TEMPLATE_CONTENT,
    }])
    client.insert("geo_report_collection_runs", [{
        "run_id": RUN_ID, "report_template_asset_id": TEMPLATE_ASSET_ID,
        "canonical_prompt2_execution_id": "exec-prompt2", "canonical_report_execution_id": "exec-prompt2",
    }])


def _seed_prompt2_execution_with_composition(client, *, execution_id="exec-prompt2", aggregation_execution_id="exec-agg"):
    client.insert("geo_report_executions", [{
        "id": execution_id, "run_id": RUN_ID, "stage": "prompt2", "execution_method": "application_ai",
        "status": "completed", "input_aggregation_execution_id": aggregation_execution_id,
        "created_at": "2026-09-13T00:00:00+00:00",
    }])
    edit_svc.save_composition_result(client, RUN_ID, execution_id, "application_ai",
                                       "exec-summary", _EXEC_SUMMARY_COMPOSED)
    edit_svc.save_composition_result(client, RUN_ID, execution_id, "application_ai",
                                       "georgia", _GEORGIA_COMPOSED)


def _mock_llm_sequence(responses: list):
    """2026-09-13の設計変更: Report Editは(1)Plan呼び出し + (2)affected_section_ids
    件のSection再生成呼び出し、という複数回のLLM呼び出しになった。呼び出し順に
    dictのリストを渡す（各dictはJSONとしてそのままエンコードして返される）。"""
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        MagicMock(choices=[MagicMock(message=MagicMock(content=json.dumps(r)))], usage=None)
        for r in responses
    ]
    return client


def _plan_response(edit_level: str, affected_section_ids: list, *, reason: str = "ok", warning=None) -> dict:
    return {
        "edit_level": edit_level, "affected_section_ids": affected_section_ids,
        "reason": reason, "warning": warning,
    }


def test_request_report_edit_local_level_updates_only_affected_section():
    client = FakeSupabaseClient()
    _seed_run_and_template(client)
    _seed_prompt2_execution_with_composition(client)

    new_georgia = {**_GEORGIA_COMPOSED, "central_conclusion": "Georgia is now a toss-up."}
    azure_client = _mock_llm_sequence([
        _plan_response("local", ["georgia"], reason="Sharpened the Georgia conclusion per user request."),
        new_georgia,  # section再生成: narrative_block schemaに完全準拠したオブジェクト
    ])

    result = edit_svc.request_report_edit(client, azure_client, "gpt-test", RUN_ID, "Make Georgia more specific")

    assert result["edit_level"] == "local"
    assert result["affected_section_ids"] == ["georgia"]
    assert result["requires_prompt2_recomposition"] is False
    assert "Georgia is now a toss-up." in result["final_report_html"]
    assert "Overall stable." in result["final_report_html"]  # 未変更sectionはそのまま残る

    run = client.select("geo_report_collection_runs", {"select": "*", "run_id": f"eq.{RUN_ID}"})[0]
    assert run["canonical_report_execution_id"] == result["execution_id"]

    new_composed = edit_svc.load_composition_results(client, result["execution_id"])
    assert new_composed["exec-summary"] == _EXEC_SUMMARY_COMPOSED  # 未変更sectionもコピーされている
    assert new_composed["georgia"]["central_conclusion"] == "Georgia is now a toss-up."
    # component_type固定schemaのフィールド名がそのまま維持されている(2026-09-13不具合の再発防止)
    assert set(new_composed["georgia"].keys()) == {
        "central_conclusion", "change_from_previous", "drivers", "confidence_note"}


def test_request_report_edit_never_changes_canonical_prompt2_execution_id():
    client = FakeSupabaseClient()
    _seed_run_and_template(client)
    _seed_prompt2_execution_with_composition(client)

    azure_client = _mock_llm_sequence([
        _plan_response("local", ["georgia"], reason="no-op edit"),
        _GEORGIA_COMPOSED,
    ])
    edit_svc.request_report_edit(client, azure_client, "gpt-test", RUN_ID, "tweak wording")

    run = client.select("geo_report_collection_runs", {"select": "*", "run_id": f"eq.{RUN_ID}"})[0]
    assert run["canonical_prompt2_execution_id"] == "exec-prompt2"  # 不変


def test_request_report_edit_analytical_recomposition_required_does_not_touch_canonical():
    client = FakeSupabaseClient()
    _seed_run_and_template(client)
    _seed_prompt2_execution_with_composition(client)

    azure_client = _mock_llm_sequence([
        _plan_response(
            "analytical_recomposition_required", [],
            reason="The user wants to change the central assessment, which requires Prompt2 recomposition.",
            warning="This edit changes analytical conclusions and cannot be applied via Report Edit alone."),
    ])

    result = edit_svc.request_report_edit(
        client, azure_client, "gpt-test", RUN_ID, "Change our overall assessment to bearish")

    assert result["requires_prompt2_recomposition"] is True
    assert result["final_report_html"] is None
    assert result["warning"]
    assert azure_client.chat.completions.create.call_count == 1  # section再生成は呼ばれない

    run = client.select("geo_report_collection_runs", {"select": "*", "run_id": f"eq.{RUN_ID}"})[0]
    assert run["canonical_report_execution_id"] == "exec-prompt2"  # 変更されない

    execution = client.select("geo_report_executions", {"select": "*", "id": f"eq.{result['execution_id']}"})[0]
    assert execution["status"] == "completed"
    assert execution["stage"] == "report_edit"


def test_chain_of_edits_finds_source_prompt2_execution():
    client = FakeSupabaseClient()
    _seed_run_and_template(client)
    _seed_prompt2_execution_with_composition(client)

    azure_client = _mock_llm_sequence([
        _plan_response("local", ["georgia"], reason="edit 1"),
        _GEORGIA_COMPOSED,
    ])
    first = edit_svc.request_report_edit(client, azure_client, "gpt-test", RUN_ID, "first edit")

    azure_client2 = _mock_llm_sequence([
        _plan_response("local", ["georgia"], reason="edit 2"),
        _GEORGIA_COMPOSED,
    ])
    second = edit_svc.request_report_edit(client, azure_client2, "gpt-test", RUN_ID, "second edit")
    assert second["execution_id"] != first["execution_id"]

    second_execution = client.select("geo_report_executions", {"select": "*", "id": f"eq.{second['execution_id']}"})[0]
    assert second_execution["parent_execution_id"] == first["execution_id"]

    prompt2_execution = edit_svc.find_source_prompt2_execution(client, second_execution)
    assert prompt2_execution["id"] == "exec-prompt2"


def test_normalize_report_to_composition_only_runs_once_for_chatgpt_import():
    client = FakeSupabaseClient()
    _seed_run_and_template(client)
    # ChatGPT Import経路: composition_resultを保存せず、final_report.htmlのみ存在する。
    client.insert("geo_report_executions", [{
        "id": "exec-prompt2", "run_id": RUN_ID, "stage": "prompt2", "execution_method": "chatgpt_import",
        "status": "completed", "input_aggregation_execution_id": "exec-agg",
        "created_at": "2026-09-13T00:00:00+00:00",
    }])
    client.insert("geo_report_run_artifacts", [{
        "id": "art-html", "run_id": RUN_ID, "execution_id": "exec-prompt2", "execution_method": "chatgpt_import",
        "artifact_type": "final_report", "file_name": "final_report.html",
        "content_text": "<html><body><section>Exec Summary: Overall stable.</section></body></html>",
        "sha256": "x", "size_bytes": 10, "media_type": "text/html",
    }])

    normalize_response = {"sections": {
        "exec-summary": _EXEC_SUMMARY_COMPOSED, "georgia": _GEORGIA_COMPOSED,
    }}
    azure_client = _mock_llm_sequence([
        normalize_response,
        _plan_response("local", ["georgia"]),
        _GEORGIA_COMPOSED,
    ])

    result = edit_svc.request_report_edit(client, azure_client, "gpt-test", RUN_ID, "tweak georgia")
    assert azure_client.chat.completions.create.call_count == 3  # normalize(1) + plan(1) + section(1)
    assert result["requires_prompt2_recomposition"] is False

    # exec-prompt2の下にcomposition_resultが保存済みのため、以後はnormalizeを呼ばない
    composed = edit_svc.load_composition_results(client, "exec-prompt2")
    assert set(composed.keys()) == {"exec-summary", "georgia"}


def test_load_report_edit_history_returns_chain_with_instructions():
    client = FakeSupabaseClient()
    _seed_run_and_template(client)
    _seed_prompt2_execution_with_composition(client)

    azure_client = _mock_llm_sequence([
        _plan_response("local", ["georgia"], reason="sharpen wording"),
        _GEORGIA_COMPOSED,
    ])
    result = edit_svc.request_report_edit(client, azure_client, "gpt-test", RUN_ID, "sharpen georgia wording")

    history = edit_svc.load_report_edit_history(client, RUN_ID)
    stages = [(h["stage"], h["execution_id"]) for h in history]
    assert ("prompt2", "exec-prompt2") in stages
    assert ("report_edit", result["execution_id"]) in stages

    edit_entry = next(h for h in history if h["execution_id"] == result["execution_id"])
    assert edit_entry["edit_instruction"]["instruction"] == "sharpen georgia wording"
    assert edit_entry["is_current_report"] is True
    assert edit_entry["is_current_prompt2"] is False

    prompt2_entry = next(h for h in history if h["execution_id"] == "exec-prompt2")
    assert prompt2_entry["is_current_prompt2"] is True
    assert prompt2_entry["is_current_report"] is False  # Editで上書きされたため


def test_revert_report_to_execution_updates_canonical_report_only():
    client = FakeSupabaseClient()
    _seed_run_and_template(client)
    _seed_prompt2_execution_with_composition(client)

    azure_client = _mock_llm_sequence([
        _plan_response("local", ["georgia"]),
        _GEORGIA_COMPOSED,
    ])
    edit_svc.request_report_edit(client, azure_client, "gpt-test", RUN_ID, "edit")

    edit_svc.revert_report_to_execution(client, RUN_ID, "exec-prompt2")

    run = client.select("geo_report_collection_runs", {"select": "*", "run_id": f"eq.{RUN_ID}"})[0]
    assert run["canonical_report_execution_id"] == "exec-prompt2"
    assert run["canonical_prompt2_execution_id"] == "exec-prompt2"  # revert先がprompt2自身のため不変のまま


def test_regenerate_section_for_edit_uses_component_type_schema_not_template_required_fields():
    """2026-09-13の不具合再発防止: Template Asset側のrequired_fields(Project固有の
    カスタムフィールド名)がSection再生成のprompt/schemaに一切混入しないことを確認する。"""
    section_def = {
        "section_id": "exec-summary", "title": "Executive Summary", "component_type": "narrative_block",
        "required_fields": ["core_hypothesis", "exec_metric_1_label"],  # 標準schemaとは無関係なダミー値
    }
    azure_client = _mock_llm_sequence([_EXEC_SUMMARY_COMPOSED])

    result = edit_svc._regenerate_section_for_edit(
        azure_client, "gpt-test", section_def, _EXEC_SUMMARY_COMPOSED, "shorten this")

    assert set(result.keys()) == {"central_conclusion", "change_from_previous", "drivers", "confidence_note"}
    call_kwargs = azure_client.chat.completions.create.call_args
    user_message = call_kwargs.kwargs["messages"][-1]["content"]
    assert "core_hypothesis" not in user_message
    assert "exec_metric_1_label" not in user_message
