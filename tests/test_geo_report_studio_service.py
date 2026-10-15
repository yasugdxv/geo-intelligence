import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_processing_service as processing  # noqa: E402
import geo_report_studio_service as studio  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _master_row(signal_id, narrative_id, *, include="", is_fallback="false", active="true"):
    # Phase R1 Human Check指摘（Iran/Hormuz Calibration実データで発見）:
    # dimension_config_jsonはfallback以外のNarrativeで必須（build_master_rulesが
    # preflightで検証する）。テスト用の最小限の分類軸を既定値として持たせる。
    dimension_config_json = "{}" if is_fallback == "true" else json.dumps({
        "dummy_dimension": {"type": "single", "label": "dummy", "definition": "test",
                             "evidence_rule": "test", "allowed_values": ["a", "unknown"],
                             "unknown_value": "unknown"},
    })
    return {
        "signal_id": signal_id, "signal_name_internal": f"{signal_id} name",
        "signal_order": "1", "signal_role": "Core", "report_topic_label": f"{signal_id} topic",
        "narrative_id": narrative_id, "narrative_name": f"{narrative_id} name",
        "narrative_description": "desc", "include_keywords": include, "required_context": "",
        "exclude_keywords": "", "cluster_split_rules": "", "cluster_time_granularity": "day",
        "report_priority": "Medium", "report_section": "Key Findings", "report_order": "1",
        "continuity_role": "Watch", "max_cluster_records": "", "representative_count": "",
        "is_fallback": is_fallback, "active": active,
        "dimension_config_json": dimension_config_json,
    }


def test_get_or_create_project_is_idempotent():
    client = FakeSupabaseClient()
    first = studio.get_or_create_project(client, "iran", "イラン監視")
    second = studio.get_or_create_project(client, "iran", "イラン監視（別タイトルでも既存を返す）")
    assert first["project_id"] == second["project_id"]
    assert len(client.tables["geo_report_projects"]) == 1


def test_delete_project_removes_project_row():
    """2026-09-15追加、ユーザー要望:「過去のプロジェクトは削除できるようにしよう」。
    関連テーブルの連鎖削除自体はDB側のon delete cascadeに委ねる（FakeSupabaseClientは
    cascadeを模さないため、ここではgeo_report_projects行が消えることだけを確認する）。"""
    client = FakeSupabaseClient()
    project = studio.get_or_create_project(client, "iran", "イラン監視")

    studio.delete_project(client, project["project_id"])

    assert studio.get_project(client, project["project_id"]) is None


def test_delete_project_rejects_unknown_project_id():
    client = FakeSupabaseClient()
    try:
        studio.delete_project(client, "does-not-exist")
        assert False, "should have raised"
    except ValueError as exc:
        assert "does-not-exist" in str(exc)


def test_replace_narrative_master_round_trip():
    client = FakeSupabaseClient()
    project = studio.get_or_create_project(client, "iran", "イラン監視")
    rows = [
        _master_row("S1", "S1-N01", include="alpha"),
        _master_row("S1", "S1-N99", is_fallback="true"),
    ]
    count = studio.replace_narrative_master(client, project["project_id"], rows)
    assert count == 2

    loaded = studio.load_narrative_master_rows(client, project["project_id"])
    assert {r["narrative_id"] for r in loaded} == {"S1-N01", "S1-N99"}

    # 置き換え（前回分は消える）
    new_rows = [_master_row("S2", "S2-N01", include="beta"), _master_row("S2", "S2-N99", is_fallback="true")]
    count2 = studio.replace_narrative_master(client, project["project_id"], new_rows)
    assert count2 == 2
    loaded2 = studio.load_narrative_master_rows(client, project["project_id"])
    assert {r["signal_id"] for r in loaded2} == {"S2"}


def _write_input_csv(path: Path, rows: list):
    fields = ["documentId", "timestamp", "documentName", "documentText", "sourceUrl",
              "languageDisplayName", "documentTypeName", "authorName", "authorPlaceName"]
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def test_persist_collection_run_writes_all_tables(tmp_path):
    client = FakeSupabaseClient()
    project = studio.get_or_create_project(client, "iran", "イラン監視")

    master_rows = [
        _master_row("S1", "S1-N01", include="alpha"),
        _master_row("S1", "S1-N99", is_fallback="true"),
    ]
    input_path = tmp_path / "S1_sample.csv"
    _write_input_csv(input_path, [
        {"documentId": "r1", "timestamp": "2026-08-10T00:00:00Z", "documentName": "Alpha news",
         "documentText": "Report about alpha situation.", "sourceUrl": "https://news.example.com/alpha-1",
         "languageDisplayName": "English", "documentTypeName": "News", "authorName": "", "authorPlaceName": "US"},
        {"documentId": "r2", "timestamp": "2026-08-11T00:00:00Z", "documentName": "Unrelated",
         "documentText": "nothing about the topic", "sourceUrl": "https://news.example.com/other",
         "languageDisplayName": "English", "documentTypeName": "News", "authorName": "", "authorPlaceName": "JP"},
    ])

    result = processing.run_preprocess([str(input_path)], master_rows, start_date="2026-08-01", end_date="2026-08-31")
    rules_by_signal, _, _ = processing.build_master_rules(master_rows)

    run_id = studio.persist_collection_run(
        client, project["project_id"], result, rules_by_signal,
        period_start="2026-08-01", period_end="2026-08-31",
    )

    runs = client.select("geo_report_collection_runs", {"select": "*", "run_id": f"eq.{run_id}"})
    assert len(runs) == 1
    assert runs[0]["total_records"] == 2
    assert runs[0]["nonduplicate_records"] == 2

    clusters = client.select("geo_report_clusters", {"select": "*", "run_id": f"eq.{run_id}"})
    assert len(clusters) >= 1
    assert all(c["run_id"] == run_id for c in clusters)

    representatives = client.select("geo_report_cluster_representatives", {"select": "*", "run_id": f"eq.{run_id}"})
    assert len(representatives) == 2  # r1, r2ともいずれかのクラスタの代表になる

    records = client.select("geo_report_records", {"select": "*", "run_id": f"eq.{run_id}"})
    assert {r["record_id"] for r in records} == {"r1", "r2"}

    all_reps = studio.load_all_representatives_for_run(client, run_id)
    assert {r["record_id"] for r in all_reps} == {"r1", "r2"}


def test_list_and_get_project():
    client = FakeSupabaseClient()
    studio.get_or_create_project(client, "iran", "イラン監視")
    studio.get_or_create_project(client, "taiwan", "台湾監視")
    projects = studio.list_projects(client)
    assert {p["project_code"] for p in projects} == {"iran", "taiwan"}

    by_code = studio.get_project_by_code(client, "iran")
    fetched = studio.get_project(client, by_code["project_id"])
    assert fetched["project_code"] == "iran"
    assert studio.get_project(client, "does-not-exist") is None


def test_run_listing_and_review_required_count():
    client = FakeSupabaseClient()
    project = studio.get_or_create_project(client, "iran", "イラン監視")
    master_rows = [
        _master_row("S1", "S1-N01", include="alpha"),
        _master_row("S1", "S1-N99", is_fallback="true"),
    ]
    input_path_rows = [
        {"documentId": "r1", "timestamp": "2026-08-10T00:00:00Z", "documentName": "Alpha",
         "documentText": "alpha situation", "sourceUrl": "https://news.example.com/a1",
         "languageDisplayName": "English", "documentTypeName": "News", "authorName": "", "authorPlaceName": "US"},
    ]

    def _write(tmp_path):
        p = tmp_path / "S1_x.csv"
        _write_input_csv(p, input_path_rows)
        return p

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        p = _write(Path(tmp))
        result = processing.run_preprocess([str(p)], master_rows, start_date="2026-08-01", end_date="2026-08-31")
    rules_by_signal, _, _ = processing.build_master_rules(master_rows)
    run_id = studio.persist_collection_run(client, project["project_id"], result, rules_by_signal)

    runs = studio.list_runs_for_project(client, project["project_id"])
    assert len(runs) == 1
    assert studio.get_latest_run(client, project["project_id"])["run_id"] == run_id
    assert studio.get_run(client, run_id)["run_id"] == run_id
    assert studio.count_review_required_clusters(client, run_id) == 0

    clusters = studio.load_clusters_for_run(client, run_id)
    studio.persist_cluster_classification(
        client, clusters[0]["id"], {"dimensions": {}}, {"some_check": {"passed": False, "detail": "x"}},
        "REVIEW REQUIRED",
    )
    assert studio.count_review_required_clusters(client, run_id) == 1
    assert studio.get_cluster(client, run_id, clusters[0]["cluster_id"])["id"] == clusters[0]["id"]


def test_synthesis_persistence_round_trip():
    client = FakeSupabaseClient()
    project = studio.get_or_create_project(client, "iran", "イラン監視")
    run_id = "run-1"
    client.tables.setdefault("geo_report_collection_runs", []).append({
        "run_id": run_id, "project_id": project["project_id"], "status": "completed",
    })

    draft = {"central_assessment": {"summary": "x"}}
    synthesis_id = studio.persist_synthesis(
        client, run_id, "S1", "S1-N01", draft, {"check1": {"passed": True, "detail": ""}}, "PASS",
        {"review_verdict": "approved", "issues_found": [], "revised_draft": None},
        draft, "PASS", "<html></html>",
        {"r1"}, {"CL-1"}, {"actor-1"},
    )
    latest = studio.get_latest_synthesis(client, run_id, "S1", "S1-N01")
    assert latest["id"] == synthesis_id
    assert latest["final_validity_status"] == "PASS"
    assert latest["observed_record_ids"] == ["r1"]

    all_for_project = studio.list_latest_syntheses_for_project(client, project["project_id"])
    assert len(all_for_project) == 1
    assert all_for_project[0]["id"] == synthesis_id


def test_get_prior_synthesis_for_narrative_excludes_current_run():
    client = FakeSupabaseClient()
    project = studio.get_or_create_project(client, "iran", "イラン監視")
    for run_id, created_at in [("run-t0", "2026-08-24T00:00:00Z"), ("run-t1", "2026-08-31T00:00:00Z")]:
        client.tables.setdefault("geo_report_collection_runs", []).append({
            "run_id": run_id, "project_id": project["project_id"], "status": "completed",
            "started_at": created_at,
        })
    studio.persist_synthesis(
        client, "run-t0", "S1", "S1-N01", {}, {}, "PASS", None, {"central_assessment": {"summary": "t0"}},
        "PASS", "<html></html>", {"r1"}, {"CL-1"}, set(),
    )
    prior = studio.get_prior_synthesis_for_narrative(
        client, project["project_id"], "S1", "S1-N01", exclude_run_id="run-t1")
    assert prior is not None
    assert prior["final_draft"]["central_assessment"]["summary"] == "t0"

    none_for_t0_itself = studio.get_prior_synthesis_for_narrative(
        client, project["project_id"], "S1", "S1-N01", exclude_run_id="run-t0")
    assert none_for_t0_itself is None


# ---------------------------------------------------------------------------
# Incremental Processing（Phase4 Continuous Intelligence）
# ---------------------------------------------------------------------------

def _run_and_classify(client, project_id, master_rows, rows, filename, *, baseline_run_id=None):
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / filename
        _write_input_csv(p, rows)
        result = processing.run_preprocess([str(p)], master_rows, start_date="2026-08-01", end_date="2026-08-31")
    rules_by_signal, _, _ = processing.build_master_rules(master_rows)
    run_id = studio.persist_collection_run(
        client, project_id, result, rules_by_signal, baseline_run_id=baseline_run_id)
    return run_id


def test_apply_incremental_reuse_reuses_unchanged_cluster():
    client = FakeSupabaseClient()
    project = studio.get_or_create_project(client, "iran", "イラン監視")
    master_rows = [
        _master_row("S1", "S1-N01", include="alpha"),
        _master_row("S1", "S1-N99", is_fallback="true"),
    ]
    row = {"documentId": "r1", "timestamp": "2026-08-10T00:00:00Z", "documentName": "Alpha",
           "documentText": "alpha situation report", "sourceUrl": "https://news.example.com/a1",
           "languageDisplayName": "English", "documentTypeName": "News", "authorName": "", "authorPlaceName": "US"}

    baseline_run_id = _run_and_classify(client, project["project_id"], master_rows, [row], "S1_base.csv")
    baseline_clusters = studio.load_clusters_for_run(client, baseline_run_id)
    studio.persist_cluster_classification(
        client, baseline_clusters[0]["id"], {"dimensions": {"foo": "bar"}}, {"c": {"passed": True, "detail": ""}}, "PASS")

    new_run_id = _run_and_classify(
        client, project["project_id"], master_rows, [row], "S1_t1.csv", baseline_run_id=baseline_run_id)

    summary = studio.apply_incremental_reuse(client, new_run_id, baseline_run_id)
    assert summary["reused"] == summary["total_clusters"]
    assert summary["reprocess"] == 0

    new_clusters = studio.load_clusters_for_run(client, new_run_id)
    reused_cluster = new_clusters[0]
    assert reused_cluster["classification"]["result"]["dimensions"] == {"foo": "bar"}
    assert reused_cluster["classification"]["reused_from_cluster_id"] == baseline_clusters[0]["cluster_id"]
    assert reused_cluster["classified_at"] is not None


def test_apply_incremental_reuse_reprocesses_changed_membership():
    client = FakeSupabaseClient()
    project = studio.get_or_create_project(client, "iran", "イラン監視")
    master_rows = [
        _master_row("S1", "S1-N01", include="alpha"),
        _master_row("S1", "S1-N99", is_fallback="true"),
    ]
    row1 = {"documentId": "r1", "timestamp": "2026-08-10T00:00:00Z", "documentName": "Alpha",
            "documentText": "alpha situation report about r1", "sourceUrl": "https://news.example.com/a1",
            "languageDisplayName": "English", "documentTypeName": "News", "authorName": "", "authorPlaceName": "US"}
    row2 = {"documentId": "r2", "timestamp": "2026-08-10T00:00:00Z", "documentName": "Alpha",
            "documentText": "alpha situation report about r2", "sourceUrl": "https://news.example.com/a2",
            "languageDisplayName": "English", "documentTypeName": "News", "authorName": "", "authorPlaceName": "US"}

    baseline_run_id = _run_and_classify(client, project["project_id"], master_rows, [row1], "S1_base.csv")
    baseline_clusters = studio.load_clusters_for_run(client, baseline_run_id)
    studio.persist_cluster_classification(
        client, baseline_clusters[0]["id"], {"dimensions": {"foo": "bar"}}, {}, "PASS")

    # r2が新規合流 → membership変化、reuseしてはいけない
    new_run_id = _run_and_classify(
        client, project["project_id"], master_rows, [row1, row2], "S1_t1.csv", baseline_run_id=baseline_run_id)

    summary = studio.apply_incremental_reuse(client, new_run_id, baseline_run_id)
    assert summary["reused"] == 0
    assert summary["reprocess"] == summary["total_clusters"]

    new_clusters = studio.load_clusters_for_run(client, new_run_id)
    assert all(c.get("classification") is None for c in new_clusters)


# ---------------------------------------------------------------------------
# Schema Version Check
# ---------------------------------------------------------------------------

class _AllOkClient:
    def select(self, table, params):
        return []


class _MissingColumnClient:
    """geo_report_collection_runsのbaseline_run_id/run_mode列が無い旧schemaを模す
    （実際にはPostgRESTが400を返すため、その代わりに例外を送出する）。"""
    def select(self, table, params):
        if table == "geo_report_collection_runs" and "baseline_run_id" in params.get("select", ""):
            raise RuntimeError("400 Client Error: column baseline_run_id does not exist")
        return []


def test_check_schema_passes_when_all_tables_reachable():
    ok, detail = studio.check_schema(_AllOkClient())
    assert ok is True
    assert detail == ""


def test_check_schema_fails_with_actionable_detail_when_column_missing():
    ok, detail = studio.check_schema(_MissingColumnClient())
    assert ok is False
    assert "geo_report_collection_runs" in detail
    assert "sql/2026-09-02_geo_report_studio_" in detail
