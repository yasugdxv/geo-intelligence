import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_artifact_compression as compression  # noqa: E402
import geo_report_current_pipeline_persistence_service as persistence  # noqa: E402
import geo_report_project_assets_service as assets  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

_INPUT_FIELDS = ["documentId", "timestamp", "documentName", "documentText", "sourceUrl"]


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


def _seed_run_with_narrative_master(client, project_id="proj-1", run_id="run-1"):
    master_asset_id = "nm-asset-1"
    client.insert("geo_report_project_assets", [{
        "id": master_asset_id, "project_id": project_id, "asset_type": "narrative_master",
        "status": "published", "content": {"rows": [
            _master_row("S1", "S1-01", include="alpha"), _master_row("S1", "S1-99", is_fallback="true"),
        ]},
    }])
    client.insert("geo_report_collection_runs", [{
        "run_id": run_id, "project_id": project_id, "narrative_master_asset_id": master_asset_id,
    }])
    return master_asset_id


def test_start_update_complete_execution_lifecycle():
    client = FakeSupabaseClient()
    client.insert("geo_report_collection_runs", [{"run_id": "run-1", "project_id": "proj-1"}])
    execution_id = persistence.start_preprocess_execution(client, "run-1")
    execution = persistence.get_execution(client, execution_id)
    assert execution["status"] == "running"
    assert execution["stage"] == "preprocess"
    assert execution["progress"]["files_processed"] == 0

    persistence.update_preprocess_progress(client, execution_id, files_processed=2, files_total=5,
                                             current_file="S1_x.csv")
    execution2 = persistence.get_execution(client, execution_id)
    assert execution2["progress"] == {
        "phase": "collecting_files", "files_processed": 2, "files_total": 5, "current_file": "S1_x.csv",
    }

    persistence.complete_execution(client, execution_id)
    execution3 = persistence.get_execution(client, execution_id)
    assert execution3["status"] == "completed"
    assert execution3["completed_at"] is not None


def test_run_and_persist_preprocess_requires_pinned_narrative_master():
    client = FakeSupabaseClient()
    client.insert("geo_report_collection_runs", [{"run_id": "run-1", "project_id": "proj-1"}])
    try:
        persistence.run_and_persist_preprocess(client, "run-1", [])
        assert False, "should have raised"
    except ValueError as exc:
        assert "narrative_master_asset_id" in str(exc)
    # narrative_master未固定の場合、Execution行が1つも作られない（部分状態を残さない）
    assert client.tables.get("geo_report_executions", []) == []


def test_run_and_persist_preprocess_end_to_end(tmp_path):
    client = FakeSupabaseClient()
    _seed_run_with_narrative_master(client)

    rows = [
        {"documentId": "r1", "timestamp": "2026-08-10T00:00:00Z", "documentName": "x",
         "documentText": "alpha content here", "sourceUrl": "https://news.example.com/x1"},
        {"documentId": "r2", "timestamp": "2026-08-11T00:00:00Z", "documentName": "y",
         "documentText": "unrelated content", "sourceUrl": "https://news.example.com/x2"},
    ]
    input_path = tmp_path / "S1_sample.csv"
    _write_input_csv(input_path, rows)

    outcome = persistence.run_and_persist_preprocess(client, "run-1", [str(input_path)])

    execution = persistence.get_execution(client, outcome["execution_id"])
    assert execution["status"] == "completed"
    assert execution["progress"]["files_processed"] == 1
    assert execution["progress"]["files_total"] == 1

    run = persistence.get_run(client, "run-1")
    assert run["canonical_preprocess_execution_id"] == outcome["execution_id"]

    analysis_rows = client.select("geo_report_analysis_clusters", {"select": "*", "run_id": "eq.run-1"})
    sentiment_rows = client.select("geo_report_sentiment_clusters", {"select": "*", "run_id": "eq.run-1"})
    assert len(analysis_rows) >= 1
    assert len(sentiment_rows) >= 1
    assert analysis_rows[0]["run_id"] == "run-1"
    assert analysis_rows[0]["signal_id"] == "S1"

    analysis_reps = client.select(
        "geo_report_analysis_cluster_representatives", {"select": "*", "run_id": "eq.run-1"})
    assert len(analysis_reps) >= 1

    record_rows = client.select("geo_report_records", {"select": "*", "run_id": "eq.run-1"})
    assert len(record_rows) == 2

    artifacts = client.select("geo_report_run_artifacts", {"select": "*", "run_id": "eq.run-1"})
    assert len(artifacts) == 12      # 2026-10-04: narrative_master_lint.csv / record_id_manifest.csv を追加
    file_names = {a["file_name"] for a in artifacts}
    assert file_names == {
        "cluster_labeling_input.csv", "cluster_representatives.csv", "cluster_manifest.csv",
        "preprocess_audit_summary.csv", "sentiment_cluster_labeling_input.csv",
        "sentiment_cluster_representatives.csv", "sentiment_exception_review_candidates.csv",
        "sentiment_cluster_classification_spec.md", "monitoring_scope.csv", "report_config.yaml",
        "narrative_master_lint.csv", "record_id_manifest.csv",
    }
    for a in artifacts:
        assert a["execution_id"] == outcome["execution_id"]
        assert a["artifact_type"] == "prompt1_input"
        assert a["sha256"]


def test_run_and_persist_preprocess_marks_execution_failed_on_error(tmp_path):
    client = FakeSupabaseClient()
    _seed_run_with_narrative_master(client)
    # 全レコードが不正な(record無し)状態を再現するため、ファイルが存在しないパスを渡す
    try:
        persistence.run_and_persist_preprocess(client, "run-1", [str(tmp_path / "does_not_exist.csv")])
        assert False, "should have raised"
    except Exception:
        pass
    executions = client.select("geo_report_executions", {"select": "*", "run_id": "eq.run-1"})
    assert len(executions) == 1
    assert executions[0]["status"] == "failed"
    # canonical_preprocess_execution_idは更新されない
    run = persistence.get_run(client, "run-1")
    assert run.get("canonical_preprocess_execution_id") is None


def test_run_and_persist_preprocess_marks_execution_failed_when_persistence_phase_fails(tmp_path, monkeypatch):
    """2026-09-15追加: 社内プロキシの瞬断でcp.run_preprocess成功後の永続化フェーズ
    （Cluster/Representative/Record/Artifact保存）が失敗した場合の再現テスト。修正前は
    ここで例外が起きてもexecutionがstatus='running'のまま放置され、ユーザーには
    ファイル処理は完了しているのに「ハングしている」としか見えなかった。"""
    client = FakeSupabaseClient()
    _seed_run_with_narrative_master(client)

    rows = [{"documentId": "r1", "timestamp": "2026-08-10T00:00:00Z", "documentName": "x",
             "documentText": "alpha content here", "sourceUrl": "https://news.example.com/x1"}]
    input_path = tmp_path / "S1_sample.csv"
    _write_input_csv(input_path, rows)

    def _boom(*a, **k):
        raise ConnectionError("simulated proxy hiccup during cluster persistence")

    monkeypatch.setattr(persistence, "_save_analysis_clusters", _boom)

    try:
        persistence.run_and_persist_preprocess(client, "run-1", [str(input_path)])
        assert False, "should have raised"
    except ConnectionError:
        pass

    executions = client.select("geo_report_executions", {"select": "*", "run_id": "eq.run-1"})
    assert len(executions) == 1
    assert executions[0]["status"] == "failed"
    assert executions[0]["completed_at"] is not None
    run = persistence.get_run(client, "run-1")
    assert run.get("canonical_preprocess_execution_id") is None


# ---------------------------------------------------------------------------
# analysis_mode別のtopic/final_graph_rule自動導出（2026-09-15追加、外部監査指摘:
# Prompt1 Packageにreport_title=public_opinion_reportとPublic Voice 3-way
# final_graph_ruleが残り、Design（evidence_intelligence）とのcontract mismatchが
# あった。呼び出し元がtopic/final_graph_ruleを省略した場合、Runへsnapshotされた
# geo_report_run_config.config_json.analysis_modeから自動導出する）
# ---------------------------------------------------------------------------

def test_run_and_persist_preprocess_derives_topic_from_evidence_intelligence_analysis_mode(tmp_path):
    client = FakeSupabaseClient()
    _seed_run_with_narrative_master(client)
    client.insert("geo_report_run_config", [{
        "run_id": "run-1", "config_json": {"analysis_mode": "evidence_intelligence"},
    }])
    rows = [{"documentId": "r1", "timestamp": "2026-08-10T00:00:00Z", "documentName": "x",
             "documentText": "alpha content here", "sourceUrl": "https://news.example.com/x1"}]
    input_path = tmp_path / "S1_sample.csv"
    _write_input_csv(input_path, rows)

    persistence.run_and_persist_preprocess(client, "run-1", [str(input_path)])

    artifacts = client.select("geo_report_run_artifacts", {
        "select": "*", "run_id": "eq.run-1", "file_name": "eq.report_config.yaml",
    })
    config_yaml = compression.decompress_text(artifacts[0]["content_text"])
    assert "report_title: evidence_intelligence_report" in config_yaml
    assert "final_graph_rule: not_applicable" in config_yaml


def test_run_and_persist_preprocess_defaults_topic_to_public_voice_monitor_when_no_run_config(tmp_path):
    """geo_report_run_configが無いRun（Snapshot未固定、または既存プロジェクトとの
    後方互換）では、従来通りpublic_opinion_report/Public Voice 3-wayルールになる。"""
    client = FakeSupabaseClient()
    _seed_run_with_narrative_master(client)
    rows = [{"documentId": "r1", "timestamp": "2026-08-10T00:00:00Z", "documentName": "x",
             "documentText": "alpha content here", "sourceUrl": "https://news.example.com/x1"}]
    input_path = tmp_path / "S1_sample.csv"
    _write_input_csv(input_path, rows)

    persistence.run_and_persist_preprocess(client, "run-1", [str(input_path)])

    artifacts = client.select("geo_report_run_artifacts", {
        "select": "*", "run_id": "eq.run-1", "file_name": "eq.report_config.yaml",
    })
    config_yaml = compression.decompress_text(artifacts[0]["content_text"])
    assert "report_title: public_opinion_report" in config_yaml
    assert "source_class=Public Voice" in config_yaml


def test_insert_chunks_with_progress_resume_from_chunk_skips_already_inserted_chunks():
    """2026-09-16追加、ユーザー要望「途中で保存して失敗したらそこから再開できないの」:
    resume_from_chunkを指定すると、その番号までのchunkは再送せず、続きのchunkだけ
    insertされることを確認する。"""
    client = FakeSupabaseClient()
    client.insert("geo_report_collection_runs", [{"run_id": "run-1", "project_id": "proj-1"}])
    execution_id = persistence.start_preprocess_execution(client, "run-1")

    payload = [{"id": f"row-{i}"} for i in range(persistence.CHUNK_SIZE * 3)]  # 3 chunks分
    persistence._insert_chunks_with_progress(
        client, "geo_report_records", payload, execution_id=execution_id, phase="saving_record_map",
        resume_from_chunk=2,
    )

    inserted = client.select("geo_report_records", {"select": "*"})
    assert len(inserted) == persistence.CHUNK_SIZE  # 最後の1chunk分のみ挿入された
    assert inserted[0]["id"] == f"row-{persistence.CHUNK_SIZE * 2}"

    execution = persistence.get_execution(client, execution_id)
    assert execution["progress"]["files_processed"] == 3
    assert execution["progress"]["files_total"] == 3


def test_resume_point_skips_completed_phases_and_resumes_partial_phase():
    skip, resume_from = persistence._resume_point("saving_analysis_clusters", "saving_record_map", 5)
    assert skip is True  # analysis_clustersはsaving_record_mapより前なので完了済みとして扱う

    skip, resume_from = persistence._resume_point("saving_record_map", "saving_record_map", 5)
    assert skip is False
    assert resume_from == 5  # 失敗直前まで進んだchunkの続きから

    skip, resume_from = persistence._resume_point("building_artifacts", "saving_record_map", 5)
    assert skip is False
    assert resume_from == 0  # まだ到達していなかったフェーズは通常通り最初から

    skip, resume_from = persistence._resume_point("saving_record_map", None, 0)
    assert (skip, resume_from) == (False, 0)  # 新規実行（チェックポイント無し）は常に通常通り


def test_run_and_persist_preprocess_resumes_skipping_already_completed_phases(tmp_path, monkeypatch):
    """failed実行のprogress（phase="saving_record_map"）をチェックポイントとして
    resume_execution_idで再実行すると、それより前のフェーズ（analysis_clusters等）は
    丸ごとスキップされ、同一execution_idが再利用されることを確認する。"""
    client = FakeSupabaseClient()
    _seed_run_with_narrative_master(client)

    rows = [{"documentId": "r1", "timestamp": "2026-08-10T00:00:00Z", "documentName": "x",
             "documentText": "alpha content here", "sourceUrl": "https://news.example.com/x1"}]
    input_path = tmp_path / "S1_sample.csv"
    _write_input_csv(input_path, rows)

    # 前回の失敗実行を模擬する（saving_record_mapフェーズへ到達した直後、そのフェーズの
    # chunkはまだ0件しか進んでいない状態で失敗したと仮定する——それより前のフェーズ
    # 〈analysis_clusters等〉は完了済みとして丸ごとスキップされるはず）。
    failed_execution_id = persistence.start_preprocess_execution(client, "run-1")
    persistence.update_preprocess_progress(
        client, failed_execution_id, files_processed=0, files_total=0, phase="saving_record_map")
    client.update("geo_report_executions", {"id": f"eq.{failed_execution_id}"}, {
        "status": "failed", "completed_at": "2026-09-16T00:00:00+00:00",
    })

    analysis_clusters_calls = []
    monkeypatch.setattr(
        persistence, "_save_analysis_clusters",
        lambda *a, **k: analysis_clusters_calls.append((a, k)),
    )

    outcome = persistence.run_and_persist_preprocess(
        client, "run-1", [str(input_path)], resume_execution_id=failed_execution_id)

    assert outcome["execution_id"] == failed_execution_id  # 新しいExecutionは作られない
    assert analysis_clusters_calls == []  # saving_record_mapより前なのでスキップされる

    execution = persistence.get_execution(client, failed_execution_id)
    assert execution["status"] == "completed"

    record_rows = client.select("geo_report_records", {"select": "*", "run_id": "eq.run-1"})
    assert len(record_rows) == 1  # チェックポイントのフェーズ自体はresume_from_chunk=0で通常通り実行される

    executions = client.select("geo_report_executions", {"select": "*", "run_id": "eq.run-1"})
    assert len(executions) == 1  # 新規Executionが増えていない


def test_run_and_persist_preprocess_rejects_resume_with_mismatched_csv(tmp_path):
    """2026-09-17追加、ユーザー指摘「再開時に別のCSVを渡したらデータが混ざるのでは」への
    対応検証: 前回の実行で記録されたfile_fingerprintと、再開時に渡されたfile_pathsの
    指紋が一致しない場合、実行を開始せずValueErrorで拒否することを確認する。"""
    client = FakeSupabaseClient()
    _seed_run_with_narrative_master(client)

    rows_a = [{"documentId": "r1", "timestamp": "2026-08-10T00:00:00Z", "documentName": "x",
               "documentText": "alpha content here", "sourceUrl": "https://news.example.com/x1"}]
    rows_b = [{"documentId": "r2", "timestamp": "2026-08-11T00:00:00Z", "documentName": "y",
               "documentText": "different content entirely", "sourceUrl": "https://news.example.com/y1"}]
    path_a = tmp_path / "S1_original.csv"
    path_b = tmp_path / "S1_different.csv"
    _write_input_csv(path_a, rows_a)
    _write_input_csv(path_b, rows_b)

    original_fingerprint = persistence._compute_file_fingerprint([str(path_a)])
    failed_execution_id = persistence.start_preprocess_execution(client, "run-1")
    persistence.update_preprocess_progress(
        client, failed_execution_id, files_processed=0, files_total=0, phase="saving_record_map",
        file_fingerprint=original_fingerprint)
    client.update("geo_report_executions", {"id": f"eq.{failed_execution_id}"}, {
        "status": "failed", "completed_at": "2026-09-16T00:00:00+00:00",
    })

    try:
        persistence.run_and_persist_preprocess(
            client, "run-1", [str(path_b)], resume_execution_id=failed_execution_id)
        assert False, "should have raised on CSV mismatch"
    except ValueError as exc:
        assert "一致しません" in str(exc)

    record_rows = client.select("geo_report_records", {"select": "*", "run_id": "eq.run-1"})
    assert record_rows == []  # 拒否されたので何も保存されない

    execution = persistence.get_execution(client, failed_execution_id)
    assert execution["status"] == "failed"
    assert "一致しません" in execution["progress"]["error"]


def test_run_and_persist_preprocess_explicit_topic_overrides_analysis_mode_derivation(tmp_path):
    client = FakeSupabaseClient()
    _seed_run_with_narrative_master(client)
    client.insert("geo_report_run_config", [{
        "run_id": "run-1", "config_json": {"analysis_mode": "evidence_intelligence"},
    }])
    rows = [{"documentId": "r1", "timestamp": "2026-08-10T00:00:00Z", "documentName": "x",
             "documentText": "alpha content here", "sourceUrl": "https://news.example.com/x1"}]
    input_path = tmp_path / "S1_sample.csv"
    _write_input_csv(input_path, rows)

    persistence.run_and_persist_preprocess(client, "run-1", [str(input_path)], topic="custom_topic")

    artifacts = client.select("geo_report_run_artifacts", {
        "select": "*", "run_id": "eq.run-1", "file_name": "eq.report_config.yaml",
    })
    config_yaml = compression.decompress_text(artifacts[0]["content_text"])
    assert "report_title: custom_topic" in config_yaml


# ---------------------------------------------------------------------------
# Preprocess潜在不具合監査（2026-10-03）: analysis_contract / effective start / manifest / spec説明
# ---------------------------------------------------------------------------

def _artifact_text(client, file_name):
    rows = client.select("geo_report_run_artifacts", {"select": "*", "file_name": f"eq.{file_name}"})
    return compression.decompress_text(rows[0]["content_text"])


def test_preprocess_artifacts_state_analysis_contract_effective_start_and_hint_semantics(tmp_path):
    client = FakeSupabaseClient()
    _seed_run_with_narrative_master(client)
    client.update("geo_report_collection_runs", {"run_id": "eq.run-1"}, {
        "period_start": "2026-08-01", "prompt1_contract_version": "evidence_intelligence_v1"})
    client.insert("geo_report_run_config", [{"run_id": "run-1", "config_json": {
        "analysis_mode": "evidence_intelligence", "primary_graph_signals": [], "discovery_signal_ids": []}}])
    rows = [
        {"documentId": "r1", "timestamp": "2026-08-10T00:00:00Z", "documentName": "x",
         "documentText": "alpha content here", "sourceUrl": "https://news.example.com/x1"},
        {"documentId": "r2", "timestamp": "2026-08-11T00:00:00Z", "documentName": "y",
         "documentText": "unrelated content", "sourceUrl": "https://news.example.com/x2"},
    ]
    input_path = tmp_path / "S1_sample.csv"
    _write_input_csv(input_path, rows)
    persistence.run_and_persist_preprocess(client, "run-1", [str(input_path)])

    config_yaml = _artifact_text(client, "report_config.yaml")
    assert "analysis_mode: evidence_intelligence\n" in config_yaml
    assert "analysis_contract: evidence_intelligence_v1\n" in config_yaml
    assert "report_title: evidence_intelligence_report" in config_yaml

    scope = list(csv.DictReader(_artifact_text(client, "monitoring_scope.csv").splitlines()))
    assert scope[0]["configured_start_date"] == ""
    assert scope[0]["effective_start_date"] == "2026-08-01"            # Run作成時のperiod_startを使う
    assert scope[0]["effective_start_source"] == "run_period_start"

    manifest = list(csv.DictReader(_artifact_text(client, "cluster_manifest.csv").splitlines()))[0]
    assert manifest["record_map_chunks"] == "1"                          # 2件 -> 1 chunk（件数に応じた実chunk数）

    spec = _artifact_text(client, "sentiment_cluster_classification_spec.md")
    assert "mechanical review-priority hint" in spec and "review_text_status" in spec


def test_preprocess_report_config_omits_contract_when_run_has_no_analysis_mode(tmp_path):
    """run_configが無い旧Runでも従来どおり動き、推測でanalysis_contractを書かない（互換性）。"""
    client = FakeSupabaseClient()
    _seed_run_with_narrative_master(client)
    input_path = tmp_path / "S1_sample.csv"
    _write_input_csv(input_path, [{"documentId": "r1", "timestamp": "2026-08-10T00:00:00Z",
                                    "documentName": "x", "documentText": "alpha", "sourceUrl": "https://a.example/x"}])
    persistence.run_and_persist_preprocess(client, "run-1", [str(input_path)], topic="t",
                                            primary_graph_signals=[], final_graph_rule="r",
                                            discovery_signal_ids=set())
    config_yaml = _artifact_text(client, "report_config.yaml")
    assert "analysis_contract" not in config_yaml and "analysis_mode" not in config_yaml
