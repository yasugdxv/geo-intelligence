import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_artifact_compression as compression  # noqa: E402
import geo_report_current_pipeline_persistence_service as persistence  # noqa: E402
import geo_report_full_pipeline_service as full_pipeline  # noqa: E402
import geo_report_prompt1_classification_service as classify  # noqa: E402
import geo_report_prompt2_composition_service as comp  # noqa: E402
import geo_report_project_assets_service as assets  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

_INPUT_FIELDS = ["documentId", "timestamp", "documentName", "documentText", "sourceUrl"]

_ANALYSIS_RESULT = {
    "final_topic_label": "Governor race", "source_assessment": "Public Voice", "tone_gradient": "Positive",
    "voice_direction": "Supportive", "report_use_status": "include", "continuity_relevance": "ongoing",
    "matched_previous_topic_id": None, "change_signal_summary": "steady", "noise_flag": False,
    "noise_reason": "", "voice_summary": "summary", "notable_phrases": "phrase",
    "representative_quality": "High", "notes_for_prompt2": "",
}

_SENTIMENT_RESULT = {
    "theme_relevance": "relevant", "source_class": "Public Voice", "reaction_type": "Evaluation",
    "attitude": "positive", "cluster_consistency": "High", "apply_to_cluster": True,
    "exception_pattern": [], "classification_reason": "matches",
}

_TARGETED_STANCE_RESULT = {"targets": []}

_COMPOSITION_RESULT = {
    "central_conclusion": "Support is mixed", "change_from_previous": "Slight shift",
    "drivers": ["economy"], "confidence_note": "Medium confidence",
}


def _write_input_csv(path: Path, rows: list):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_INPUT_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in _INPUT_FIELDS})


def _master_row(signal_id, narrative_id, *, include="", is_fallback="false"):
    row = {
        "signal_id": signal_id, "narrative_id": narrative_id, "narrative_name": f"{narrative_id} name",
        "include_keywords": include, "is_fallback": is_fallback, "active": "true",
        "theme_id": f"{signal_id}_T", "theme_name": f"{signal_id} Theme",
    }
    return assets.sync_shell_mirror_columns(row)


def _seed_run(client, *, project_id="proj-1", run_id="run-1"):
    master_asset_id = "nm-asset-1"
    client.insert("geo_report_project_assets", [{
        "id": master_asset_id, "project_id": project_id, "asset_type": "narrative_master",
        "status": "published", "content": {"rows": [
            _master_row("S1", "S1-01", include="alpha"), _master_row("S1", "S1-99", is_fallback="true"),
        ]},
    }])
    template_asset_id = "tmpl-asset-1"
    client.insert("geo_report_project_assets", [{
        "id": template_asset_id, "project_id": project_id, "asset_type": "report_template",
        "status": "published", "content": {
            "style_css": "<style>.x{}</style>",
            "shell_html": "<html><head>{style}</head><body>{sections}</body></html>",
            "sections": [{
                "section_id": "exec-summary", "title": "Executive Summary", "order": 1,
                "component_type": "narrative_block", "required_fields": ["central_conclusion"],
            }],
        },
    }])
    client.insert("geo_report_collection_runs", [{
        "run_id": run_id, "project_id": project_id, "narrative_master_asset_id": master_asset_id,
        "report_template_asset_id": template_asset_id,
    }])
    client.insert("geo_report_run_config", [{
        "run_id": run_id, "config_json": {}, "primary_graph_signals": ["S1"],
        "common_primary_graph_start": "2026-08-01",
    }])
    return master_asset_id, template_asset_id


def _batch_stub(result_fields: dict):
    """2026-09-11 Batching最適化: classify_*_batchは複数クラスタを1回でまとめて返す。
    スタブはclusters(各要素のcluster_row.cluster_id)ごとにresult_fieldsをコピーした
    resultを{"results": [...]}として返す。"""
    def _stub(azure_client, model, clusters, **kwargs):
        results = [{"cluster_id": c["cluster_row"]["cluster_id"], **result_fields} for c in clusters]
        return {"data": {"results": results}}
    return _stub


def _patch_llm_calls(monkeypatch):
    monkeypatch.setattr(classify, "classify_analysis_clusters_batch", _batch_stub(_ANALYSIS_RESULT))
    monkeypatch.setattr(classify, "classify_sentiment_clusters_batch", _batch_stub(_SENTIMENT_RESULT))
    monkeypatch.setattr(classify, "classify_targeted_stance_batch", _batch_stub(_TARGETED_STANCE_RESULT))
    monkeypatch.setattr(comp, "compose_section",
                         lambda *a, **k: {"data": dict(_COMPOSITION_RESULT)})


def _sample_input_csv(tmp_path):
    rows = [
        {"documentId": "r1", "timestamp": "2026-08-10T00:00:00Z", "documentName": "x",
         "documentText": "alpha content here", "sourceUrl": "https://news.example.com/x1"},
        {"documentId": "r2", "timestamp": "2026-08-11T00:00:00Z", "documentName": "y",
         "documentText": "alpha again content", "sourceUrl": "https://news.example.com/x2"},
    ]
    path = tmp_path / "S1_sample.csv"
    _write_input_csv(path, rows)
    return str(path)


def test_run_full_report_pipeline_end_to_end(tmp_path, monkeypatch):
    client = FakeSupabaseClient()
    _seed_run(client)
    _patch_llm_calls(monkeypatch)

    outcome = full_pipeline.run_full_report_pipeline(
        client, azure_client=None, model="gpt-4o", run_id="run-1",
        file_paths=[_sample_input_csv(tmp_path)])

    assert outcome["total_records"] == 2
    assert outcome["preprocess_execution_id"]
    assert outcome["prompt1_execution_id"]
    assert outcome["aggregation_execution_id"]
    assert outcome["prompt2_execution_id"]

    run = client.select("geo_report_collection_runs", {"select": "*", "run_id": "eq.run-1"})[0]
    assert run["canonical_preprocess_execution_id"] == outcome["preprocess_execution_id"]
    assert run["canonical_prompt1_execution_id"] == outcome["prompt1_execution_id"]
    assert run["canonical_prompt2_execution_id"] == outcome["prompt2_execution_id"]
    assert run["canonical_report_execution_id"] == outcome["prompt2_execution_id"]

    final_report_artifacts = client.select("geo_report_run_artifacts", {
        "select": "*", "execution_id": f"eq.{outcome['prompt2_execution_id']}", "artifact_type": "eq.final_report",
    })
    assert len(final_report_artifacts) == 1
    assert "Support is mixed" in compression.decompress_text(final_report_artifacts[0]["content_text"])

    all_executions = client.select("geo_report_executions", {"select": "*", "run_id": "eq.run-1"})
    stages = {e["stage"] for e in all_executions}
    assert stages == {"preprocess", "prompt1", "aggregation", "prompt2"}
    assert all(e["status"] == "completed" for e in all_executions)


def test_run_prompt1_through_prompt2_clears_stale_cancel_flag_on_start(tmp_path, monkeypatch):
    """前回キャンセルされたRunを再実行した際、即座にまたキャンセルされないこと
    （実行開始時に必ずcancel_requestedをリセットする、2026-09-11）。"""
    client = FakeSupabaseClient()
    _seed_run(client)
    _patch_llm_calls(monkeypatch)
    persistence.run_and_persist_preprocess(client, "run-1", [_sample_input_csv(tmp_path)])
    client.update("geo_report_collection_runs", {"run_id": "eq.run-1"}, {"cancel_requested": True})

    outcome = full_pipeline.run_prompt1_through_prompt2(client, azure_client=None, model="gpt-4o", run_id="run-1")
    assert outcome["prompt2_execution_id"]


def test_run_prompt1_through_prompt2_stops_when_cancelled_mid_run(tmp_path, monkeypatch):
    """Aggregation実行中にユーザーがキャンセルを要求した場合、Prompt2 Execution作成へ
    進まずPipelineCancelledで止まること。"""
    client = FakeSupabaseClient()
    _seed_run(client)
    _patch_llm_calls(monkeypatch)
    persistence.run_and_persist_preprocess(client, "run-1", [_sample_input_csv(tmp_path)])

    real_run_aggregation = full_pipeline.prompt2_orch.run_aggregation

    def _run_aggregation_then_cancel(client_, run_id_, execution_id_):
        real_run_aggregation(client_, run_id_, execution_id_)
        client_.update("geo_report_collection_runs", {"run_id": f"eq.{run_id_}"}, {"cancel_requested": True})

    monkeypatch.setattr(full_pipeline.prompt2_orch, "run_aggregation", _run_aggregation_then_cancel)

    try:
        full_pipeline.run_prompt1_through_prompt2(client, azure_client=None, model="gpt-4o", run_id="run-1")
        assert False, "should have raised PipelineCancelled"
    except full_pipeline.PipelineCancelled:
        pass

    prompt2_executions = client.select("geo_report_executions", {"select": "*", "run_id": "eq.run-1", "stage": "eq.prompt2"})
    assert prompt2_executions == []  # Prompt2 Executionは作られていない


def test_run_full_report_pipeline_honors_cancel_requested_during_preprocess(tmp_path, monkeypatch):
    """CSVアップロード直後の自動フルパイプライン実行(run_full_report_pipeline)で、
    Preprocess実行中にユーザーがキャンセルを要求した場合、Prompt1が開始されず
    PipelineCancelledで止まること。かつては`run_prompt1_through_prompt2`の冒頭で
    無条件に`clear_cancel_flag`が呼ばれ、このキャンセル要求が握り潰されていた
    不具合の再現テスト（2026-09-15修正）。"""
    client = FakeSupabaseClient()
    _seed_run(client)
    _patch_llm_calls(monkeypatch)

    real_run_and_persist = full_pipeline.persistence.run_and_persist_preprocess

    def _preprocess_then_cancel(client_, run_id_, file_paths_):
        outcome = real_run_and_persist(client_, run_id_, file_paths_)
        client_.update("geo_report_collection_runs", {"run_id": f"eq.{run_id_}"}, {"cancel_requested": True})
        return outcome

    monkeypatch.setattr(full_pipeline.persistence, "run_and_persist_preprocess", _preprocess_then_cancel)

    try:
        full_pipeline.run_full_report_pipeline(
            client, azure_client=None, model="gpt-4o", run_id="run-1",
            file_paths=[_sample_input_csv(tmp_path)])
        assert False, "should have raised PipelineCancelled"
    except full_pipeline.PipelineCancelled:
        pass

    prompt1_executions = client.select(
        "geo_report_executions", {"select": "*", "run_id": "eq.run-1", "stage": "eq.prompt1"})
    assert prompt1_executions == []  # Prompt1 Executionは作られていない


def test_run_full_report_pipeline_stops_and_raises_if_prompt1_batch_keeps_failing(tmp_path, monkeypatch):
    client = FakeSupabaseClient()
    _seed_run(client)
    _patch_llm_calls(monkeypatch)

    def _always_fail(*a, **k):
        raise ValueError("simulated persistent classification failure")

    monkeypatch.setattr(classify, "classify_analysis_clusters_batch", _always_fail)

    try:
        full_pipeline.run_full_report_pipeline(
            client, azure_client=None, model="gpt-4o", run_id="run-1",
            file_paths=[_sample_input_csv(tmp_path)])
        assert False, "should have raised"
    except ValueError as exc:
        assert "prompt1_analysis_cluster" in str(exc)

    # Preprocessは完了したままcanonicalに残る（Run全体を巻き戻さない）
    run = client.select("geo_report_collection_runs", {"select": "*", "run_id": "eq.run-1"})[0]
    assert run.get("canonical_preprocess_execution_id") is not None
    assert run.get("canonical_prompt1_execution_id") is None
    # AggregationやPrompt2のExecutionは一切作られていない（後続Stageへ進まない）
    aggregation_executions = client.select("geo_report_executions", {
        "select": "id", "run_id": "eq.run-1", "stage": "eq.aggregation",
    })
    assert aggregation_executions == []


def test_run_full_report_pipeline_auto_retries_transient_failure(tmp_path, monkeypatch):
    """実運用で見つかった「初回だけ一時的に失敗する」ケースの再現——2回目の
    call_llm_structured相当の呼び出しで成功すれば、Run全体は完了まで進む。"""
    client = FakeSupabaseClient()
    _seed_run(client)
    _patch_llm_calls(monkeypatch)

    calls = {"count": 0}
    real_result = dict(_ANALYSIS_RESULT)

    def _fail_once_then_succeed(*a, **k):
        calls["count"] += 1
        if calls["count"] == 1:
            raise ValueError("transient error")
        return {"data": dict(real_result)}

    monkeypatch.setattr(classify, "classify_analysis_cluster", _fail_once_then_succeed)

    outcome = full_pipeline.run_full_report_pipeline(
        client, azure_client=None, model="gpt-4o", run_id="run-1",
        file_paths=[_sample_input_csv(tmp_path)])

    assert outcome["prompt2_execution_id"]
    run = client.select("geo_report_collection_runs", {"select": "*", "run_id": "eq.run-1"})[0]
    assert run["canonical_prompt1_execution_id"] == outcome["prompt1_execution_id"]


def test_run_prompt1_through_prompt2_redo_without_reuploading_csv_requires_completed_preprocess():
    client = FakeSupabaseClient()
    _seed_run(client)
    try:
        full_pipeline.run_prompt1_through_prompt2(client, azure_client=None, model="gpt-4o", run_id="run-1")
        assert False, "should have raised"
    except ValueError as exc:
        assert "Preprocess" in str(exc)


def test_run_aggregation_through_prompt2_requires_canonical_prompt1():
    """Canonical Prompt1が未確定のRunに対しては、Aggregation以降だけの実行を拒否する
    （2026-09-14、ChatGPTでPrompt1のみ処理しApplication AIでAggregation以降を続ける
    運用の入口）。"""
    client = FakeSupabaseClient()
    _seed_run(client)
    try:
        full_pipeline.run_aggregation_through_prompt2(client, azure_client=None, model="gpt-4o", run_id="run-1")
        assert False, "should have raised"
    except ValueError as exc:
        assert "Canonical Prompt1" in str(exc)


def test_run_aggregation_through_prompt2_uses_existing_canonical_prompt1_without_rerunning_it(tmp_path, monkeypatch):
    """既存のCanonical Prompt1（Application AI・ChatGPT Importいずれでもよい）を
    そのまま使い、新しいPrompt1 Executionを作らずにAggregation→Prompt2だけを
    実行できること。"""
    client = FakeSupabaseClient()
    _seed_run(client)
    _patch_llm_calls(monkeypatch)

    first = full_pipeline.run_full_report_pipeline(
        client, azure_client=None, model="gpt-4o", run_id="run-1",
        file_paths=[_sample_input_csv(tmp_path)])

    outcome = full_pipeline.run_aggregation_through_prompt2(
        client, azure_client=None, model="gpt-4o", run_id="run-1")

    assert "prompt1_execution_id" not in outcome
    assert outcome["aggregation_execution_id"] != first["aggregation_execution_id"]
    assert outcome["prompt2_execution_id"] != first["prompt2_execution_id"]

    prompt1_executions = client.select("geo_report_executions", {
        "select": "id", "run_id": "eq.run-1", "stage": "eq.prompt1",
    })
    assert len(prompt1_executions) == 1  # Prompt1は再実行されていない

    run = client.select("geo_report_collection_runs", {"select": "*", "run_id": "eq.run-1"})[0]
    assert run["canonical_prompt1_execution_id"] == first["prompt1_execution_id"]  # 不変
    assert run["canonical_prompt2_execution_id"] == outcome["prompt2_execution_id"]
    assert run["canonical_report_execution_id"] == outcome["prompt2_execution_id"]

    # 注: propagate_stalenessのstale化は「Prompt1 Canonicalが切り替わった時」
    # （changed_stage="prompt1"）にのみAggregation/Prompt2を対象とする設計のため、
    # Prompt1を変えずにAggregation/Prompt2だけ作り直した今回のケースでは、旧
    # Aggregation/Prompt2 Executionはcompletedのまま残る（Canonical自体は正しく
    # 新しい方へ切り替わっているため実害はないが、Compose画面の一覧には旧Executionが
    # 引き続き"Alternative"として表示され続ける——同一Prompt1から複数回レポートを
    # 作り直す運用のUX改善余地として別途記録する）。


def test_run_prompt1_through_prompt2_redo_creates_new_canonical_and_stales_old_downstream(tmp_path, monkeypatch):
    """「処理やり直し」——CSVを再アップロードせず、Prompt1〜Prompt2だけ再実行しても
    新しいCanonicalが確定し、旧Aggregation/Prompt2 Executionはstale化される。"""
    client = FakeSupabaseClient()
    _seed_run(client)
    _patch_llm_calls(monkeypatch)

    first = full_pipeline.run_full_report_pipeline(
        client, azure_client=None, model="gpt-4o", run_id="run-1",
        file_paths=[_sample_input_csv(tmp_path)])

    second = full_pipeline.run_prompt1_through_prompt2(client, azure_client=None, model="gpt-4o", run_id="run-1")

    assert second["prompt1_execution_id"] != first["prompt1_execution_id"]
    assert second["prompt2_execution_id"] != first["prompt2_execution_id"]

    run = client.select("geo_report_collection_runs", {"select": "*", "run_id": "eq.run-1"})[0]
    assert run["canonical_prompt1_execution_id"] == second["prompt1_execution_id"]
    assert run["canonical_prompt2_execution_id"] == second["prompt2_execution_id"]

    old_aggregation = client.select("geo_report_executions", {
        "select": "*", "id": f"eq.{first['aggregation_execution_id']}",
    })[0]
    old_prompt2 = client.select("geo_report_executions", {
        "select": "*", "id": f"eq.{first['prompt2_execution_id']}",
    })[0]
    assert old_aggregation["status"] == "stale"
    assert old_prompt2["status"] == "stale"

    # Preprocessは1回しか実行されていない（CSV再アップロード無しで再実行できている証拠）
    preprocess_executions = client.select("geo_report_executions", {
        "select": "id", "run_id": "eq.run-1", "stage": "eq.preprocess",
    })
    assert len(preprocess_executions) == 1
