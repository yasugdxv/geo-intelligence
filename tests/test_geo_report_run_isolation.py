"""Run isolation（2026-10-04）のテスト。

背景: geo_report_recordsはexecution_id列を持たずrun_idでしか区別されず、保存処理はinsertのみ。同じRunで
Preprocessをやり直すと旧実行の行が残り、Prompt 2 base exportのrecord mapに混ざる（Evidence run a1cb0baf:
標本1,000行の9.7%が旧9/14ファイル由来）。また、prompt1_input artifactをrun_idだけで引くと、再実行したRunでは
旧実行のartifactを読み得る。本番の保存・export関数を使い、DBだけFakeで検証する。
"""
import csv
import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_artifact_compression as compression  # noqa: E402
import geo_report_current_pipeline_persistence_service as persistence  # noqa: E402
import geo_report_project_assets_service as assets  # noqa: E402
import geo_report_prompt2_package_validation_service as pv  # noqa: E402
import geo_report_prompt_export_service as export_svc  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

_FIELDS = ["documentId", "timestamp", "documentName", "documentText", "sourceUrl"]


@pytest.fixture(autouse=True)
def validation_on(monkeypatch):
    monkeypatch.setattr(export_svc, "PROMPT2_BASE_VALIDATION_ENABLED", True)


def write_csv(path: Path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def make_rows(prefix: str, n: int, day="2026-09-10"):
    return [{"documentId": f"{prefix}{i}", "timestamp": f"{day}T10:00:00Z", "documentName": f"{prefix} title {i}",
             "documentText": f"alpha content {prefix} number{i}x here", "sourceUrl": f"https://news.example.com/{prefix}{i}"}
            for i in range(n)]


def master_row(narrative_id, *, include="", fallback="false"):
    return assets.sync_shell_mirror_columns({
        "signal_id": "S1", "narrative_id": narrative_id, "narrative_name": f"{narrative_id} name",
        "include_keywords": include, "is_fallback": fallback, "active": "true", "theme_id": "S1_T",
        "theme_name": "S1 Theme", "trend_role": "primary", "trend_start_date": "2026-07-01",
        "main_narrative": "Main", "stance_default": "Unknown"})


def seed_run(client, run_id="run-1"):
    client.insert("geo_report_project_assets", [{
        "id": "nm-1", "project_id": "proj-1", "asset_type": "narrative_master", "status": "published",
        "content": {"rows": [master_row("S1-01", include="alpha"), master_row("S1-99", fallback="true")]}}])
    client.insert("geo_report_collection_runs", [{
        "run_id": run_id, "project_id": "proj-1", "narrative_master_asset_id": "nm-1"}])


def preprocess(client, tmp_path, name, rows, run_id="run-1"):
    path = tmp_path / f"S1_{name}.csv"
    write_csv(path, rows)
    return persistence.run_and_persist_preprocess(
        client, run_id, [str(path)], topic="public_opinion_report", primary_graph_signals=["S1"],
        final_graph_rule="source_class=Public Voice; duplicate=false", discovery_signal_ids=set())


def artifact_text(client, execution_id, file_name):
    rows = client.select("geo_report_run_artifacts", {
        "select": "*", "execution_id": f"eq.{execution_id}", "file_name": f"eq.{file_name}"})
    return compression.decompress_text(rows[0]["content_text"])


def test_single_execution_exports_and_record_manifest_matches_exactly(tmp_path):
    client = FakeSupabaseClient()
    seed_run(client)
    outcome = preprocess(client, tmp_path, "new", make_rows("n", 30))
    manifest = artifact_text(client, outcome["execution_id"], pv.RECORD_MANIFEST_FILE)
    assert len(list(csv.DictReader(io.StringIO(manifest)))) == 30
    zip_bytes = export_svc.build_prompt2_base_input_zip(client, "run-1")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        rows = list(csv.DictReader(io.StringIO(zf.read("record_cluster_map_001.csv").decode("utf-8"))))
    assert len(rows) == 30
    assert export_svc.build_prompt1_input_zip(client, "run-1")


def test_rerunning_preprocess_in_the_same_run_makes_export_fail_with_stale_rows(tmp_path):
    """Evidence run a1cb0baf の再現: 旧ファイルの行がDBに残った状態で、新しい入力でPreprocessをやり直す。"""
    client = FakeSupabaseClient()
    seed_run(client)
    preprocess(client, tmp_path, "old", make_rows("old", 20, day="2026-09-01"))
    preprocess(client, tmp_path, "new", make_rows("new", 30))
    assert len(client.tables["geo_report_records"]) == 50           # 旧20 + 新30（insertのみで旧行は消えない）
    with pytest.raises(ValueError) as exc:
        export_svc.build_prompt2_base_input_zip(client, "run-1")
    message = str(exc.value)
    assert "stale" in message and "20件" in message and "S1_old.csv" in message
    assert "新しいRun" in message


def test_missing_records_make_export_fail(tmp_path):
    client = FakeSupabaseClient()
    seed_run(client)
    preprocess(client, tmp_path, "new", make_rows("n", 30))
    del client.tables["geo_report_records"][:3]                       # 3件欠落
    with pytest.raises(ValueError) as exc:
        export_svc.build_prompt2_base_input_zip(client, "run-1")
    assert "missing(欠落)=3件" in str(exc.value)


def test_stale_and_missing_are_reported_separately_by_the_comparison():
    expected = pv.counter_from_manifest_csv(
        "record_id,signal_source,source_file\r\nr1,S1,a.csv\r\nr2,S1,a.csv\r\nr2,S2,a.csv\r\n")
    exported = pv.counter_from_db_records([
        {"record_id": "r1", "signal_id": "S1", "source_file": "a.csv"},
        {"record_id": "r9", "signal_id": "S1", "source_file": "old.csv"},
        {"record_id": "r2", "signal_id": "S1", "source_file": "a.csv"}])
    result = pv.compare_record_sets(expected, exported)
    assert not result["ok"]
    assert (result["stale_total"], result["missing_total"]) == (1, 1)
    assert result["stale_examples"] == [["r9", "S1", "old.csv"]]
    assert result["missing_examples"] == [["r2", "S2", "a.csv"]]       # 同じrecord_idでもSignalが違えば別のrecord


def test_same_record_id_in_two_signals_is_not_a_false_stale():
    expected = pv.counter_from_manifest_csv("record_id,signal_source,source_file\r\nr1,S1,a.csv\r\nr1,S2,b.csv\r\n")
    exported = pv.counter_from_db_records([
        {"record_id": "r1", "signal_id": "S2", "source_file": "b.csv"},
        {"record_id": "r1", "signal_id": "S1", "source_file": "a.csv"}])
    assert pv.compare_record_sets(expected, exported)["ok"]


def test_artifacts_are_read_from_the_canonical_preprocess_execution(tmp_path):
    """再実行したRunには旧実行のartifactも残る。run_idだけで引くと旧実行を読み得るため、canonical executionを使う。"""
    client = FakeSupabaseClient()
    seed_run(client)
    first = preprocess(client, tmp_path, "old", make_rows("old", 20))
    second = preprocess(client, tmp_path, "new", make_rows("new", 30))
    assert first["execution_id"] != second["execution_id"]
    run = persistence.get_run(client, "run-1")
    assert run["canonical_preprocess_execution_id"] == second["execution_id"]
    # 旧実行のartifactが先頭に来るように並べ替えても、canonical(新)を読む
    client.tables["geo_report_run_artifacts"].sort(key=lambda r: r["execution_id"] != first["execution_id"])
    text = export_svc._prompt1_input_artifact(client, "run-1", "cluster_manifest.csv", execution_id=second["execution_id"])
    manifest = list(csv.DictReader(io.StringIO(text)))[0]
    assert manifest["total_records"] == "30"
    zip_bytes = export_svc.build_prompt1_input_zip(client, "run-1")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        rows = list(csv.DictReader(io.StringIO(zf.read("cluster_manifest.csv").decode("utf-8"))))
    assert rows[0]["total_records"] == "30"


def test_prompt1_input_count_validation_detects_artifacts_from_another_execution():
    files = {
        "cluster_manifest.csv": "total_records,nonduplicate_records\r\n30,30\r\n",
        "cluster_labeling_input.csv": "cluster_id,eligible_records\r\nAC_1,20\r\n",
        "sentiment_cluster_labeling_input.csv": "cluster_id,eligible_records\r\nSC_1,30\r\n"}
    issues = pv.validate_prompt1_input_counts(files)
    assert [i["code"] for i in issues] == ["prompt1_count_mismatch:cluster_labeling_input.csv"]


def test_legacy_run_without_record_manifest_is_not_rejected_by_the_set_check(tmp_path):
    """record_id_manifest.csvが無い旧Runは集合比較できない（False）。件数・source_file等の検証は別途働く。"""
    client = FakeSupabaseClient()
    seed_run(client)
    outcome = preprocess(client, tmp_path, "new", make_rows("n", 10))
    client.tables["geo_report_run_artifacts"] = [
        a for a in client.tables["geo_report_run_artifacts"] if a["file_name"] != pv.RECORD_MANIFEST_FILE]
    records = export_svc._fetch_all_records(client, "run-1")
    assert export_svc._assert_record_set_matches_execution(client, "run-1", records, outcome["execution_id"]) is False
    assert export_svc._assert_record_set_matches_execution(client, "run-1", records, None) is False


# ---------------------------------------------------------------------------
# 実DB規模でのrecord取得の耐性（2026-10-04）: geo_report_recordsの(run_id, id)に索引が無く、select=*/limit=5000は
# statement timeout（HTTP 500, code 57014）で、同じクエリが成功したり失敗したりした。
# ---------------------------------------------------------------------------

class FlakyRecordsClient(FakeSupabaseClient):
    def __init__(self, fail_first_n_calls_per_page=2, status=500, **kw):
        super().__init__(**kw)
        self.fail_n, self.status = fail_first_n_calls_per_page, status
        self.calls = []                      # (limit, select) の履歴
        self._fails_for_cursor = {}

    def select(self, table, params):
        if table == "geo_report_records":
            import types
            import requests
            self.calls.append((params.get("limit"), params.get("select")))
            cursor = params.get("id")
            done = self._fails_for_cursor.get(cursor, 0)
            if done < self.fail_n:
                self._fails_for_cursor[cursor] = done + 1
                raise requests.exceptions.HTTPError(response=types.SimpleNamespace(status_code=self.status))
        return super().select(table, params)


def test_fetch_all_records_retries_statement_timeouts_and_shrinks_the_page(monkeypatch):
    monkeypatch.setattr(export_svc, "RECORD_FETCH_PARALLEL_RANGES", 1)
    monkeypatch.setattr(export_svc.time, "sleep", lambda s: None)
    client = FlakyRecordsClient(fail_first_n_calls_per_page=3, max_rows=1000)
    client.tables["geo_report_records"] = [
        {"id": f"rec-{i:06d}", "run_id": "run-1", "record_id": f"r{i}"} for i in range(2500)]
    records = export_svc._fetch_all_records(client, "run-1")
    assert len(records) == 2500 and len({r["id"] for r in records}) == 2500
    limits = [int(l) for l, _ in client.calls]
    assert limits[0] == 1000 and min(limits) < 1000          # 2回失敗するとページサイズを半分にする


def test_fetch_all_records_requests_only_needed_columns_and_does_not_retry_4xx(monkeypatch):
    monkeypatch.setattr(export_svc, "RECORD_FETCH_PARALLEL_RANGES", 1)
    monkeypatch.setattr(export_svc.time, "sleep", lambda s: None)
    client = FlakyRecordsClient(fail_first_n_calls_per_page=0, max_rows=1000)
    client.tables["geo_report_records"] = [{"id": "a", "run_id": "run-1", "record_id": "r"}]
    export_svc._fetch_all_records(client, "run-1")
    select = client.calls[0][1]
    assert select != "*" and select.startswith("id,record_id,") and "signal_id" in select and "source_file" in select
    bad = FlakyRecordsClient(fail_first_n_calls_per_page=5, status=400)
    bad.tables["geo_report_records"] = [{"id": "a", "run_id": "run-1"}]
    import requests
    with pytest.raises(requests.exceptions.HTTPError):
        export_svc._fetch_all_records(bad, "run-1")
    assert len(bad.calls) == 1                                  # 4xxは再送しても直らないので即座に送出


def test_fetch_all_records_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr(export_svc, "RECORD_FETCH_PARALLEL_RANGES", 1)
    monkeypatch.setattr(export_svc.time, "sleep", lambda s: None)
    client = FlakyRecordsClient(fail_first_n_calls_per_page=10**6)
    client.tables["geo_report_records"] = [{"id": "a", "run_id": "run-1"}]
    import requests
    with pytest.raises(requests.exceptions.HTTPError):
        export_svc._fetch_all_records(client, "run-1")
    assert len(client.calls) == export_svc.RECORD_FETCH_MAX_ATTEMPTS


def test_parallel_range_fetch_returns_every_record_exactly_once_across_all_uuid_ranges():
    """実DB対策: uuidのid空間を16分割して並列取得。範囲の境界・最後の範囲（上限なし）・サーバ上限1000件でも
    欠落・重複が無いこと。"""
    import uuid
    rng = __import__("random").Random(5)
    ids = {str(uuid.UUID(int=rng.getrandbits(128))) for _ in range(5000)}
    ids |= {"00000000-0000-0000-0000-000000000000", "ffffffff-ffff-ffff-ffff-ffffffffffff",
            "0fffffff-ffff-ffff-ffff-ffffffffffff", "10000000-0000-0000-0000-000000000000"}   # 範囲の境界
    client = FakeSupabaseClient(max_rows=1000)
    client.tables["geo_report_records"] = [{"id": i, "run_id": "run-1", "record_id": i[:8]} for i in sorted(ids)]
    client.tables["geo_report_records"] += [{"id": i, "run_id": "other-run", "record_id": "x"} for i in list(ids)[:500]]
    records = export_svc._fetch_all_records(client, "run-1")
    assert len(records) == len(ids) and {r["id"] for r in records} == ids


def test_parallel_and_sequential_fetch_give_the_same_records(monkeypatch):
    client = FakeSupabaseClient(max_rows=1000)
    client.tables["geo_report_records"] = [{"id": f"rec-{i:05d}", "run_id": "run-1", "record_id": f"r{i}"} for i in range(2300)]
    parallel = {r["id"] for r in export_svc._fetch_all_records(client, "run-1")}
    monkeypatch.setattr(export_svc, "RECORD_FETCH_PARALLEL_RANGES", 1)
    sequential = {r["id"] for r in export_svc._fetch_all_records(client, "run-1")}
    assert parallel == sequential and len(parallel) == 2300
