import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_prompt2_orchestration_service as orch  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _mock_azure_client(response_data: dict):
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(response_data)))],
    )
    return client


def _seed_run(client, *, prompt1_done=True, run_id="run-1", project_id="proj-1",
              report_template_asset_id="tmpl-1"):
    client.insert("geo_report_collection_runs", [{
        "run_id": run_id, "project_id": project_id,
        "canonical_prompt1_execution_id": "exec-prompt1-1" if prompt1_done else None,
        "report_template_asset_id": report_template_asset_id,
        "prompt1_contract_version": "v4.3", "prompt2_contract_version": "v4.3",
    }])


def _seed_run_config(client, run_id="run-1", *, primary_graph_signals=("S1",),
                      common_primary_graph_start="2026-07-01"):
    client.insert("geo_report_run_config", [{
        "run_id": run_id, "config_json": {}, "primary_graph_signals": list(primary_graph_signals),
        "common_primary_graph_start": common_primary_graph_start,
    }])


def _seed_records_and_classifications(client, run_id="run-1"):
    client.insert("geo_report_records", [
        {"id": "rec-1", "run_id": run_id, "signal_id": "S1", "is_duplicate": False,
         "date_key": "2026-08-10", "sentiment_cluster_id": "SC_1", "week_start": "2026-08-10",
         "week_end": "2026-08-16"},
        {"id": "rec-2", "run_id": run_id, "signal_id": "S1", "is_duplicate": False,
         "date_key": "2026-08-11", "sentiment_cluster_id": "SC_2", "week_start": "2026-08-10",
         "week_end": "2026-08-16"},
    ])
    client.insert("geo_report_sentiment_cluster_classifications", [
        {"id": "cls-1", "execution_id": "exec-prompt1-1", "run_id": run_id, "sentiment_cluster_id": "SC_1",
         "theme_relevance": "relevant", "source_class": "Public Voice", "attitude": "positive",
         "apply_to_cluster": True},
        {"id": "cls-2", "execution_id": "exec-prompt1-1", "run_id": run_id, "sentiment_cluster_id": "SC_2",
         "theme_relevance": "relevant", "source_class": "Public Voice", "attitude": "negative",
         "apply_to_cluster": True},
    ])
    client.insert("geo_report_sentiment_clusters", [
        {"id": "sc-1", "run_id": run_id, "sentiment_cluster_id": "SC_1", "signal_id": "S1",
         "report_topic_label": "Arizona Governor"},
    ])


def _template_asset():
    return {
        "id": "tmpl-1", "content": {
            "style_css": "<style>.x{}</style>",
            "shell_html": "<html><head>{style}</head><body>{sections}</body></html>",
            "sections": [
                {"section_id": "exec-summary", "title": "Executive Summary", "order": 1,
                 "component_type": "narrative_block", "required_fields": ["central_conclusion"]},
                {"section_id": "trend", "title": "Voice Trend", "order": 2,
                 "component_type": "stacked_bar_chart", "required_fields": ["interpretation"]},
            ],
        },
    }


def _seed_template_asset(client):
    asset = _template_asset()
    client.insert("geo_report_project_assets", [{
        "id": asset["id"], "project_id": "proj-1", "asset_type": "report_template", "status": "published",
        "content": asset["content"],
    }])


_NARRATIVE_RESPONSE = {"central_conclusion": "Support is mixed", "change_from_previous": "Slight shift",
                        "drivers": ["economy"], "confidence_note": "Medium confidence"}
_STACKED_BAR_RESPONSE = {"interpretation": "Positive share rising", "notable_shifts": ["week 2"]}


def test_create_aggregation_execution_requires_completed_prompt1():
    client = FakeSupabaseClient()
    _seed_run(client, prompt1_done=False)
    try:
        orch.create_aggregation_execution(client, "run-1")
        assert False, "should have raised"
    except ValueError as exc:
        assert "Prompt1" in str(exc)


def test_run_aggregation_end_to_end_stores_artifacts_and_completes():
    client = FakeSupabaseClient()
    _seed_run(client)
    _seed_run_config(client)
    _seed_records_and_classifications(client)

    execution_id = orch.create_aggregation_execution(client, "run-1")
    result = orch.run_aggregation(client, "run-1", execution_id)

    assert len(result["biweekly_rows"]) == 1
    row = result["biweekly_rows"][0]
    assert row["positive_n"] == 1
    assert row["negative_n"] == 1
    assert row["report_topic_label"] == "Arizona Governor"

    artifacts = client.select("geo_report_run_artifacts", {
        "select": "*", "execution_id": f"eq.{execution_id}", "artifact_type": "eq.aggregation_output",
    })
    file_names = {a["file_name"] for a in artifacts}
    assert file_names == {"biweekly_trend_data.csv", "weekly_trend_data.csv", "monthly_summary_data.csv",
                           "denominator_audit.csv"}

    execution = client.select("geo_report_executions", {"select": "*", "id": f"eq.{execution_id}"})[0]
    assert execution["status"] == "completed"


def test_run_aggregation_requires_run_config():
    client = FakeSupabaseClient()
    _seed_run(client)
    execution_id = orch.create_aggregation_execution(client, "run-1")
    try:
        orch.run_aggregation(client, "run-1", execution_id)
        assert False, "should have raised"
    except ValueError as exc:
        assert "geo_report_run_config" in str(exc)
    execution = client.select("geo_report_executions", {"select": "*", "id": f"eq.{execution_id}"})[0]
    assert execution["status"] == "failed"


def test_full_prompt2_composition_and_finalize_flow():
    client = FakeSupabaseClient()
    _seed_run(client)
    _seed_run_config(client)
    _seed_records_and_classifications(client)
    _seed_template_asset(client)

    agg_execution_id = orch.create_aggregation_execution(client, "run-1")
    orch.run_aggregation(client, "run-1", agg_execution_id)

    prompt2_execution_id = orch.create_prompt2_execution(client, "run-1", agg_execution_id)
    template_asset = _template_asset()
    batches = orch.plan_composition_batches(client, "run-1", prompt2_execution_id, template_asset)
    assert len(batches) == 2

    # 未完了のためfinalizeはFalse
    outcome = orch.finalize_prompt2_execution(client, "run-1", prompt2_execution_id)
    assert outcome == {"completed": False}

    azure_narrative = _mock_azure_client(_NARRATIVE_RESPONSE)
    azure_stacked = _mock_azure_client(_STACKED_BAR_RESPONSE)

    # exec-summaryセクションを処理（narrative_block）
    outcome1 = orch.run_next_composition_batch(
        client, azure_narrative, "gpt-4o", "run-1", prompt2_execution_id, template_asset=template_asset)
    assert outcome1["status"] == "completed"
    # trendセクションを処理（stacked_bar_chart）
    outcome2 = orch.run_next_composition_batch(
        client, azure_stacked, "gpt-4o", "run-1", prompt2_execution_id, template_asset=template_asset)
    assert outcome2["status"] == "completed"

    composition_artifacts = client.select("geo_report_run_artifacts", {
        "select": "*", "execution_id": f"eq.{prompt2_execution_id}", "artifact_type": "eq.composition_result",
    })
    assert len(composition_artifacts) == 2

    final_outcome = orch.finalize_prompt2_execution(client, "run-1", prompt2_execution_id, report_theme="T")
    assert final_outcome["completed"] is True
    final_html = final_outcome["final_report_html"]
    assert "Support is mixed" in final_html
    assert "<svg" in final_html
    assert final_html.index("Executive Summary") < final_html.index("Voice Trend")  # order順

    prompt2_output_artifacts = client.select("geo_report_run_artifacts", {
        "select": "*", "execution_id": f"eq.{prompt2_execution_id}", "artifact_type": "eq.prompt2_output",
    })
    assert {a["file_name"] for a in prompt2_output_artifacts} == {
        "report_brief.md", "biweekly_trend_data.csv", "weekly_trend_data.csv", "monthly_summary_data.csv",
        "denominator_audit.csv", "reference_examples_used.csv", "continuity_context_next.json",
        "continuity_change_log.md", "methodology_notes.md",
    }
    final_report_artifacts = client.select("geo_report_run_artifacts", {
        "select": "*", "execution_id": f"eq.{prompt2_execution_id}", "artifact_type": "eq.final_report",
    })
    assert len(final_report_artifacts) == 1

    run = client.select("geo_report_collection_runs", {"select": "*", "run_id": "eq.run-1"})[0]
    assert run["canonical_prompt2_execution_id"] == prompt2_execution_id
    assert run["canonical_report_execution_id"] == prompt2_execution_id


def test_composition_batch_idempotency_does_not_duplicate_artifact():
    client = FakeSupabaseClient()
    _seed_run(client)
    _seed_run_config(client)
    _seed_records_and_classifications(client)
    _seed_template_asset(client)

    agg_execution_id = orch.create_aggregation_execution(client, "run-1")
    orch.run_aggregation(client, "run-1", agg_execution_id)
    prompt2_execution_id = orch.create_prompt2_execution(client, "run-1", agg_execution_id)
    template_asset = _template_asset()
    batches = orch.plan_composition_batches(client, "run-1", prompt2_execution_id, template_asset)
    batch_id = batches[0]["id"]

    azure_client = _mock_azure_client(_NARRATIVE_RESPONSE)
    orch.run_next_composition_batch(
        client, azure_client, "gpt-4o", "run-1", prompt2_execution_id, template_asset=template_asset)
    # 同じバッチを再度pendingへ戻して再処理(retry相当)
    client.update("geo_report_execution_batches", {"id": f"eq.{batch_id}"}, {"status": "pending"})
    orch.run_next_composition_batch(
        client, azure_client, "gpt-4o", "run-1", prompt2_execution_id, template_asset=template_asset)

    artifacts = client.select("geo_report_run_artifacts", {
        "select": "*", "execution_id": f"eq.{prompt2_execution_id}", "artifact_type": "eq.composition_result",
        "file_name": "eq.exec-summary.json",
    })
    assert len(artifacts) == 1  # upsertなので重複しない
