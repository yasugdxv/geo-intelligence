"""地政学ナレッジ＆インテリジェンスDB Phase2A: GDELT Event/Mentions/GKG raw feed
Connectorのテスト。実GDELT APIは呼び出さない（requests.getをモックする）。

Event/Mentionsの1行は本セッション内で実際にGDELT raw feed(2026-09-07 06:00バッチ)から
取得した実データをそのまま使う（列位置の検証に架空データを使わない）。
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import config_utils  # noqa: E402
import geo_connector_gdelt_stream as stream  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

# 実データ(2026-09-07 06:00バッチ、export.CSV / mentions.CSV)そのまま
REAL_EVENT_LINE = (
    "1321915473\t20260831\t202608\t2026\t2026.6603\t\t\t\t\t\t\t\t\t\t\tUSA\tUNITED STATES\tUSA"
    "\t\t\t\t\t\t\t\t1\t112\t112\t11\t3\t-2.0\t2\t1\t2\t-7.2992700729927\t0\t\t\t\t\t\t\t\t3"
    "\tMckeesport, Pennsylvania, United States\tUS\tUSPA\tPA003\t40.3478\t-79.8642\t1192915\t2"
    "\tPennsylvania, United States\tUS\tUSPA\t\t40.5773\t-77.264\tPA\t20260907234500"
    "\thttps://www.wgal.com/article/former-pennsylvania-priest-accused-stealing-750000-waives-hearing/73634428"
)
REAL_MENTION_LINE = (
    "1321878344\t20260907171500\t20260907234500\t1\tnzcity.co.nz"
    "\thttps://home.nzcity.co.nz/news/article.aspx?id=453593\t4\t828\t777\t814\t1\t60\t1940\t2.1021021021021\t\t"
)
# GKGは27列。実データ形式(V1Themes;区切り、V2EnhancedPersons"Name,offset"形式)を模した固定fixture。
# 手動でのtab数え間違いを避けるため、27要素のリストをインデックス指定で組み立ててからjoinする。
_gkg_fields = [""] * 27
_gkg_fields[stream.GKG_COL_GKGRECORDID] = "20260907234500-0"
_gkg_fields[stream.GKG_COL_V21DATE] = "20260907234500"
_gkg_fields[stream.GKG_COL_V2SOURCECOLLECTIONIDENTIFIER] = "1"
_gkg_fields[stream.GKG_COL_V2SOURCECOMMONNAME] = "redstate.com"
_gkg_fields[stream.GKG_COL_V2DOCUMENTIDENTIFIER] = "https://redstate.com/example-article"
_gkg_fields[stream.GKG_COL_V1THEMES] = "TAX_FNCACT;LEADER"
_gkg_fields[stream.GKG_COL_V2ENHANCEDPERSONS] = "Donald Trump,4051;Vladimir Putin,120"
_gkg_fields[stream.GKG_COL_V2ENHANCEDORGANIZATIONS] = "National Republican Senatorial Committee,1358"
_gkg_fields[stream.GKG_COL_V15TONE] = "-0.92,1.29,2.21,3.51,19.96,0.55,990"
GKG_LINE = "\t".join(_gkg_fields)


def _stub_no_config(monkeypatch):
    monkeypatch.setattr(config_utils, "load_config", lambda: {})


def _mock_response(status_code=200, content=b""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)
    return resp


# ── 列定数の実データ検証 ─────────────────────────────────────────────

def test_parse_event_rows_extracts_named_columns_from_real_data():
    parsed, skipped = stream.parse_event_rows(REAL_EVENT_LINE.encode("utf-8"))
    assert skipped == 0
    assert len(parsed) == 1
    row = parsed[0]
    assert row["provider_event_id"] == "1321915473"
    assert row["event_date"] == "2026-08-31"
    assert row["actor2_name"] == "UNITED STATES"
    assert row["actor2_country_code"] == "USA"
    assert row["event_code"] == "112"
    assert row["goldstein_scale"] == -2.0
    assert row["num_mentions"] == 2
    assert row["avg_tone"] == -7.2992700729927
    assert row["action_geo_full_name"] == "Pennsylvania, United States"
    assert row["event_date_added"] == "2026-09-07T23:45:00+00:00"
    assert row["source_url"].startswith("https://www.wgal.com/")


def test_parse_mention_rows_extracts_named_columns_from_real_data():
    parsed, skipped = stream.parse_mention_rows(REAL_MENTION_LINE.encode("utf-8"))
    assert skipped == 0
    assert len(parsed) == 1
    row = parsed[0]
    assert row["provider_event_id"] == "1321878344"
    assert row["event_time_date"] == "20260907171500"
    assert row["is_web"] is True
    assert row["mention_identifier"] == "https://home.nzcity.co.nz/news/article.aspx?id=453593"
    assert row["confidence"] == 60


def test_parse_mention_rows_non_web_type_is_flagged():
    fields = REAL_MENTION_LINE.split("\t")
    fields[stream.MENTION_COL_MENTIONTYPE] = "4"  # 4=非WEB(citation等)
    non_web_line = "\t".join(fields)
    parsed, _ = stream.parse_mention_rows(non_web_line.encode("utf-8"))
    assert parsed[0]["is_web"] is False


def test_parse_gkg_rows_parses_persons_organizations_and_tone():
    parsed, skipped = stream.parse_gkg_rows(GKG_LINE.encode("utf-8"))
    assert skipped == 0
    row = parsed[0]
    assert row["provider_record_id"] == "20260907234500-0"
    assert row["is_web"] is True
    assert row["source_common_name"] == "redstate.com"
    assert row["document_identifier"] == "https://redstate.com/example-article"
    assert {"name": "Donald Trump", "char_offset": "4051"} in row["persons"]
    assert {"name": "Vladimir Putin", "char_offset": "120"} in row["persons"]
    assert row["organizations"][0]["name"] == "National Republican Senatorial Committee"
    assert row["tone"] == -0.92
    assert row["tone_word_count"] == 990


def test_get_latest_batch_manifest_parses_lastupdate_txt(monkeypatch):
    body = (
        "28521 hash1 http://data.gdeltproject.org/gdeltv2/20260907234500.export.CSV.zip\n"
        "57099 hash2 http://data.gdeltproject.org/gdeltv2/20260907234500.mentions.CSV.zip\n"
        "2378404 hash3 http://data.gdeltproject.org/gdeltv2/20260907234500.gkg.csv.zip\n"
    )
    monkeypatch.setattr(stream.requests, "get", lambda *a, **k: _mock_response(200, body.encode("utf-8")))
    manifest = stream.get_latest_batch_manifest(request_interval_seconds=0)
    assert manifest["batch_timestamp"] == "20260907234500"
    assert manifest["event"].endswith(".export.CSV.zip")


def test_download_with_retry_on_5xx_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, proxies, verify, timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            return _mock_response(500, b"server error")
        return _mock_response(200, b"OK")

    monkeypatch.setattr(stream.requests, "get", fake_get)
    monkeypatch.setattr(stream.time, "sleep", lambda s: None)
    content = stream._download_with_retry("http://example.com/x.zip", request_interval_seconds=0)
    assert calls["n"] == 3
    assert content == b"OK"


def test_download_with_retry_404_raises_file_not_found(monkeypatch):
    monkeypatch.setattr(stream.requests, "get", lambda *a, **k: _mock_response(404))
    try:
        stream._download_with_retry("http://example.com/missing.zip", request_interval_seconds=0)
        assert False, "FileNotFoundErrorが送出されるはず"
    except FileNotFoundError:
        pass


# ── Checkpoint（差分方式） ───────────────────────────────────────────

def test_list_expected_batch_timestamps_15min_steps():
    ts = stream.list_expected_batch_timestamps("20260907060000", "20260907063000")
    assert ts == ["20260907060000", "20260907061500", "20260907063000"]


def test_list_completed_batch_timestamps_includes_terminal_warnings_only_batch():
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [
        {"connector_type": "gdelt_stream", "status": "completed_with_warnings", "is_demo": False,
         "request_parameters": {"batch_timestamp": "20260907060000", "retryable_pending_count": 0}},
        {"connector_type": "gdelt_stream", "status": "completed_with_warnings", "is_demo": False,
         "request_parameters": {"batch_timestamp": "20260907061500", "retryable_pending_count": 3}},
    ]})
    completed = stream.list_completed_batch_timestamps(client, since="20260907000000", until="20260907235959")
    assert "20260907060000" in completed
    assert "20260907061500" not in completed


def test_list_completed_batch_timestamps_one_success_among_multiple_runs_counts_as_complete():
    """同一batch_timestampにfailed行とcompleted行の両方が存在する場合、そのbatch_timestampが
    完了集合に入り再実行対象へ戻らないこと（6回目レビュー②の回帰テスト）。"""
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [
        {"connector_type": "gdelt_stream", "status": "failed", "is_demo": False,
         "request_parameters": {"batch_timestamp": "20260907060000"}},
        {"connector_type": "gdelt_stream", "status": "completed", "is_demo": False,
         "request_parameters": {"batch_timestamp": "20260907060000"}},
    ]})
    completed = stream.list_completed_batch_timestamps(client, since="20260907000000", until="20260907235959")
    assert "20260907060000" in completed

    missing = stream.list_missing_batch_timestamps(client, since="20260907060000", until="20260907060000")
    assert missing == []


def test_list_missing_batch_timestamps_excludes_retryable_warnings():
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [
        {"connector_type": "gdelt_stream", "status": "completed", "is_demo": False,
         "request_parameters": {"batch_timestamp": "20260907060000"}},
        {"connector_type": "gdelt_stream", "status": "completed_with_warnings", "is_demo": False,
         "request_parameters": {"batch_timestamp": "20260907061500", "retryable_pending_count": 2}},
    ]})
    missing = stream.list_missing_batch_timestamps(client, since="20260907060000", until="20260907063000")
    assert missing == ["20260907061500", "20260907063000"]


def test_list_completed_batch_timestamps_is_demo_scoped():
    """productionのmissing計算がdemo実行のcompleted batchを参照してはならない（逆方向も同様、
    11回目レビュー修正①の直接回帰テスト）。"""
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [
        {"connector_type": "gdelt_stream", "status": "completed", "is_demo": True,
         "request_parameters": {"batch_timestamp": "20260907060000"}},
    ]})
    missing_prod = stream.list_missing_batch_timestamps(
        client, since="20260907060000", until="20260907060000", is_demo=False)
    assert missing_prod == ["20260907060000"]
    missing_demo = stream.list_missing_batch_timestamps(
        client, since="20260907060000", until="20260907060000", is_demo=True)
    assert missing_demo == []


# ── Event Resolution（別batch解決・run全体共有のcache/budget） ────────

def test_group_event_ids_by_event_time():
    mentions = [
        {"provider_event_id": "111", "event_time_date": "20260907060000"},
        {"provider_event_id": "222", "event_time_date": "20260907060000"},
        {"provider_event_id": "333", "event_time_date": "20260907043000"},
    ]
    grouped = stream.group_event_ids_by_event_time(mentions)
    assert grouped["20260907060000"] == {"111", "222"}
    assert grouped["20260907043000"] == {"333"}


def test_resolve_events_by_event_time_downloads_different_batch_than_mention(monkeypatch):
    """MentionのEventTimeDateがMention自身のbatchと異なるケースを固定し、別batchの
    export.CSV.zipが取得されることを確認する（2回目レビュー①の回帰テスト、最重要）。"""
    downloaded_urls = []

    def fake_download(kind, batch_timestamp, **kwargs):
        downloaded_urls.append((kind, batch_timestamp))
        assert kind == "event"
        assert batch_timestamp == "20260907043000"  # Mention batch(060000)とは異なる
        return REAL_EVENT_LINE.replace("1321915473", "999").encode("utf-8")

    monkeypatch.setattr(stream, "download_batch_file", fake_download)
    cache, budget = {}, 20
    result = stream.resolve_events_by_event_time(
        None, event_time_date_to_ids={"20260907043000": {"999"}},
        mention_batch_timestamp="20260907060000", proxies={}, verify=True, request_interval_seconds=0,
        event_resolution_lookback_days=7, event_batch_cache=cache, budget_remaining=budget)
    assert downloaded_urls == [("event", "20260907043000")]
    assert "999" in result["event_row_by_id"]
    assert result["event_row_by_id"]["999"]["_event_batch_timestamp"] == "20260907043000"


def test_resolve_events_lookback_is_relative_to_mention_batch_timestamp_not_wallclock(monkeypatch):
    """mention_batch_timestampを過去日付に固定してもlookback判定が同じ結果になることを確認する
    （3回目レビュー②の回帰テスト、historical replayでの決定論性）。"""
    monkeypatch.setattr(stream, "download_batch_file", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("lookback外のEventTimeDateはdownloadされないはず")))
    cache, budget = {}, 20
    # mention_batch_timestampが2026-09-07で、EventTimeDateがlookback(7日)を超えて古い(2026-08-01)
    result = stream.resolve_events_by_event_time(
        None, event_time_date_to_ids={"20260801000000": {"1"}},
        mention_batch_timestamp="20260907060000", proxies={}, verify=True, request_interval_seconds=0,
        event_resolution_lookback_days=7, event_batch_cache=cache, budget_remaining=budget)
    assert "1" in result["unresolved_event_ids_terminal"]
    assert result["budget_consumed"] == 0


def test_resolve_events_classifies_retryable_vs_terminal(monkeypatch):
    call_count = {"n": 0}

    def fake_download(kind, batch_timestamp, **kwargs):
        call_count["n"] += 1
        if batch_timestamp == "20260907050000":
            raise RuntimeError("simulated HTTP failure")
        return REAL_EVENT_LINE.replace("1321915473", "888").encode("utf-8")

    monkeypatch.setattr(stream, "download_batch_file", fake_download)
    cache, budget = {}, 20
    result = stream.resolve_events_by_event_time(
        None, event_time_date_to_ids={
            "20260907050000": {"111"},   # download失敗 -> retryable
            "20260907043000": {"888", "999"},  # download成功、888は存在・999は存在しない -> terminal
        },
        mention_batch_timestamp="20260907060000", proxies={}, verify=True, request_interval_seconds=0,
        event_resolution_lookback_days=7, event_batch_cache=cache, budget_remaining=budget)
    assert "111" in result["unresolved_event_ids_retryable"]
    assert "999" in result["unresolved_event_ids_terminal"]
    assert "888" in result["event_row_by_id"]


def test_resolve_events_shares_cache_across_calls_within_same_run(monkeypatch):
    """run全体で共有されるevent_batch_cacheにより、同じEventTimeDateが複数回参照されても
    2回目以降はdownloadされないこと（4回目レビュー①の回帰テスト）。"""
    call_count = {"n": 0}

    def fake_download(kind, batch_timestamp, **kwargs):
        call_count["n"] += 1
        return REAL_EVENT_LINE.replace("1321915473", "777").encode("utf-8")

    monkeypatch.setattr(stream, "download_batch_file", fake_download)
    shared_cache = {}
    budget = 20
    result1 = stream.resolve_events_by_event_time(
        None, event_time_date_to_ids={"20260907043000": {"777"}},
        mention_batch_timestamp="20260907060000", proxies={}, verify=True, request_interval_seconds=0,
        event_resolution_lookback_days=7, event_batch_cache=shared_cache, budget_remaining=budget)
    budget -= result1["budget_consumed"]
    result2 = stream.resolve_events_by_event_time(
        None, event_time_date_to_ids={"20260907043000": {"777"}},
        mention_batch_timestamp="20260907061500", proxies={}, verify=True, request_interval_seconds=0,
        event_resolution_lookback_days=7, event_batch_cache=shared_cache, budget_remaining=budget)
    assert call_count["n"] == 1  # 2回目はcache命中で再downloadしない
    assert "777" in result2["event_row_by_id"]


def test_resolve_events_respects_run_level_budget_across_multiple_event_time_dates(monkeypatch):
    """max_event_resolution_batches_per_runがMention batchごとにリセットされず、
    複数の異なるEventTimeDateをまたいで共有されること（4回目レビュー①の回帰テスト）。"""
    def fake_download(kind, batch_timestamp, **kwargs):
        return REAL_EVENT_LINE.replace("1321915473", "1").encode("utf-8")

    monkeypatch.setattr(stream, "download_batch_file", fake_download)
    cache = {}
    result = stream.resolve_events_by_event_time(
        None, event_time_date_to_ids={
            "20260907040000": {"1"}, "20260907041500": {"1"}, "20260907043000": {"1"},
        },
        mention_batch_timestamp="20260907060000", proxies={}, verify=True, request_interval_seconds=0,
        event_resolution_lookback_days=7, event_batch_cache=cache, budget_remaining=2)  # 予算2件のみ
    assert result["budget_consumed"] == 2
    assert len(result["unresolved_event_ids_retryable"]) == 1  # 3件目は予算切れでretryable


# ── Contiguous watermark / is_demo分離 / maturity clamp（Operationalization） ──

def _freeze_now(monkeypatch, fixed_now):
    """stream.datetime.now(timezone.utc)を固定時刻に差し替える（サブクラス化してstrptime等
    他のクラスメソッドは実物のdatetimeのまま使えるようにする）。"""
    frozen = type("_FrozenDatetime", (stream.datetime,), {})
    frozen.now = classmethod(lambda cls, tz=None: fixed_now)
    monkeypatch.setattr(stream, "datetime", frozen)
    return frozen


def test_align_to_batch_grid_rounds_down_to_15min_boundary():
    assert stream._align_to_batch_grid("20260907060732") == "20260907060000"
    assert stream._align_to_batch_grid("20260907061459") == "20260907060000"
    assert stream._align_to_batch_grid("20260907061500") == "20260907061500"


def test_compute_completed_through_stops_at_first_non_terminal_batch():
    """00:00がterminal、00:15がterminalでない、00:30がterminalというfixtureで、
    最大terminal batchではなく連続terminal区間の末尾（00:00）を返すこと
    （9回目レビュー修正①の直接回帰テスト）。"""
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [
        {"connector_type": "gdelt_stream", "status": "completed", "is_demo": False,
         "request_parameters": {"batch_timestamp": "20260907000000"}},
        {"connector_type": "gdelt_stream", "status": "completed_with_warnings", "is_demo": False,
         "request_parameters": {"batch_timestamp": "20260907001500", "retryable_pending_count": 1}},
        {"connector_type": "gdelt_stream", "status": "completed", "is_demo": False,
         "request_parameters": {"batch_timestamp": "20260907003000"}},
    ]})
    watermark = stream.compute_completed_through(
        client, baseline="20260906234500", horizon="20260907010000", is_demo=False)
    assert watermark == "20260907000000"

    # 00:15をterminalへ更新すると、初めて00:30まで前進する
    client.tables["geo_ingestion_runs"][1]["request_parameters"]["retryable_pending_count"] = 0
    watermark2 = stream.compute_completed_through(
        client, baseline="20260906234500", horizon="20260907010000", is_demo=False)
    assert watermark2 == "20260907003000"


def test_resolve_incremental_since_resumes_from_72h_old_watermark_not_24h_fallback(monkeypatch):
    """72時間停止後のcatch-up: since未指定時にresolve_incremental_sinceがcompleted_through+15分
    から再開すること（now-DEFAULT_LOOKBACK_HOURSではないこと、8回目レビュー修正①の直接回帰
    テスト）。"""
    fixed_now = stream.datetime(2026, 9, 10, 0, 0, 0, tzinfo=stream.timezone.utc)
    _freeze_now(monkeypatch, fixed_now)
    watermark_72h_ago = "20260907000000"  # fixed_nowの72時間前
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [
        {"connector_type": "gdelt_stream_catchup", "status": "completed", "is_demo": False,
         "started_at": "2026-09-07T00:05:00+00:00",
         "request_parameters": {"completed_through": watermark_72h_ago, "checkpoint_scope": "incremental"}},
    ]})
    since = stream.resolve_incremental_since(client, is_demo=False)
    assert since == "20260907001500"  # 72h前のwatermark+15分（now-24hではない）


def test_bootstrap_baseline_does_not_skip_unterminated_first_batch(monkeypatch):
    """prior watermarkが存在しないbootstrap時、first_expected_batchが未terminalなら
    watermarkがそれを飛び越えないこと（9回目レビュー修正②の直接回帰テスト）。"""
    fixed_now = stream.datetime(2026, 9, 7, 6, 7, 0, tzinfo=stream.timezone.utc)  # 15分境界でない
    _freeze_now(monkeypatch, fixed_now)
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": []})
    baseline = stream._bootstrap_baseline(stream.DEFAULT_LOOKBACK_HOURS, now=fixed_now)
    first_expected_batch = stream._align_to_batch_grid(
        (fixed_now - stream.timedelta(hours=stream.DEFAULT_LOOKBACK_HOURS)).strftime("%Y%m%d%H%M%S"))
    assert baseline == stream._baseline_before(first_expected_batch)

    # first_expected_batchがまだterminalでない(run無し)ため、compute_completed_throughは
    # baselineのまま前進しない
    result = stream.compute_completed_through(client, baseline=baseline,
                                               horizon=fixed_now.strftime("%Y%m%d%H%M%S"), is_demo=False)
    assert result == baseline

    # first_expected_batchをterminalにすると、初めて前進する
    client.insert("geo_ingestion_runs", [{"connector_type": "gdelt_stream", "status": "completed",
                                           "is_demo": False,
                                           "request_parameters": {"batch_timestamp": first_expected_batch}}])
    result2 = stream.compute_completed_through(client, baseline=baseline,
                                                horizon=fixed_now.strftime("%Y%m%d%H%M%S"), is_demo=False)
    assert result2 == first_expected_batch


def test_is_demo_isolation_for_resolve_incremental_since(monkeypatch):
    """is_demo=trueの完了状態がproduction側のresolve_incremental_sinceに影響しないこと
    （逆方向も同様、10回目レビュー修正①の直接回帰テスト）。"""
    fixed_now = stream.datetime(2026, 9, 7, 6, 0, 0, tzinfo=stream.timezone.utc)
    _freeze_now(monkeypatch, fixed_now)
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [
        {"connector_type": "gdelt_stream", "status": "completed", "is_demo": True,
         "request_parameters": {"batch_timestamp": "20260907000000"}},
        {"connector_type": "gdelt_stream", "status": "completed", "is_demo": True,
         "request_parameters": {"batch_timestamp": "20260907001500"}},
    ]})
    # production側はdemoの完了状態を一切見ない -> 24hのfallback baselineのまま
    since_prod = stream.resolve_incremental_since(client, is_demo=False)
    expected_bootstrap = stream._bootstrap_baseline(stream.DEFAULT_LOOKBACK_HOURS, now=fixed_now)
    dt = stream.datetime.strptime(expected_bootstrap, "%Y%m%d%H%M%S").replace(tzinfo=stream.timezone.utc)
    expected_since_prod = (dt + stream.timedelta(minutes=stream.BATCH_INTERVAL_MINUTES)).strftime("%Y%m%d%H%M%S")
    assert since_prod == expected_since_prod


def test_explicit_until_is_clamped_to_maturity_cutoff(monkeypatch):
    """明示until=nowでも通常運用ではmaturity cutoffでclampされること、bypassすればされない
    こと（9回目レビュー修正③の直接回帰テスト）。"""
    fixed_now = stream.datetime(2026, 9, 7, 6, 0, 0, tzinfo=stream.timezone.utc)
    _freeze_now(monkeypatch, fixed_now)
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": []})
    now_str = fixed_now.strftime("%Y%m%d%H%M%S")
    missing_clamped = stream.list_missing_batch_timestamps(
        client, since="20260907000000", until=now_str, batch_maturity_hours=6,
        bypass_maturity_clamp=False, is_demo=False)
    assert now_str not in missing_clamped
    assert max(missing_clamped) == "20260907000000"  # maturity_cutoff=00:00なので、それ以降は対象外

    missing_bypassed = stream.list_missing_batch_timestamps(
        client, since="20260907000000", until=now_str, batch_maturity_hours=6,
        bypass_maturity_clamp=True, is_demo=False)
    assert now_str in missing_bypassed


def test_ad_hoc_completed_through_uses_explicit_range_not_fallback_24h():
    """explicit since/untilのcompleted_throughがfallback 24hではなく指定range自身から計算
    されること（11回目レビュー修正②の直接回帰テスト）。"""
    since = "20260301000000"  # 6ヶ月前相当
    until = "20260303000000"
    expected = stream.list_expected_batch_timestamps(since, until)
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [
        {"connector_type": "gdelt_stream", "status": "completed", "is_demo": False,
         "request_parameters": {"batch_timestamp": bt}}
        for bt in expected
    ]})
    baseline = stream._baseline_before(since)
    watermark = stream.compute_completed_through(client, baseline=baseline, horizon=until, is_demo=False)
    assert watermark == until  # 24h fallback由来の値ではなく、指定range自身のcontiguous frontier


# ── run_gdelt_structured_ingestion / run_gdelt_stream_catchup（orchestrator） ──

def _stub_downstream_services(monkeypatch, *, recompute_calls: list = None):
    """run_gdelt_structured_ingestionが呼ぶevent/actor/media/cluster/link各serviceを
    無害なno-opへ差し替える（このテストファイルの関心はcheckpoint/404/status判定ロジックで
    あり、下流Candidate生成の実装詳細ではないため）。"""
    import geo_external_event_service as event_svc
    import geo_actor_episode_service as actor_svc
    import geo_media_episode_service as media_svc
    import geo_episode_cluster_service as cluster_svc
    import geo_situation_link_service as link_svc

    monkeypatch.setattr(event_svc, "match_signals_for_urls", lambda client, urls: {})
    monkeypatch.setattr(event_svc, "bulk_upsert_events",
                         lambda client, rows: {"inserted": 0, "updated": 0, "event_id_by_provider_event_id": {}})
    monkeypatch.setattr(event_svc, "bulk_upsert_event_signal_links", lambda client, rows: {"inserted": 0})
    monkeypatch.setattr(event_svc, "bulk_upsert_annotations", lambda client, rows: {"inserted": 0})
    monkeypatch.setattr(actor_svc, "generate_candidates_for_batch",
                         lambda client, **kw: {"affected_actor_episode_candidate_ids": []})
    monkeypatch.setattr(media_svc, "generate_candidates_for_batch", lambda client, **kw: {})
    monkeypatch.setattr(cluster_svc, "recluster_affected_keys", lambda client, **kw: None)

    def _recompute(client, **kw):
        if recompute_calls is not None:
            recompute_calls.append(kw)
        return {}
    monkeypatch.setattr(link_svc, "recompute_situation_links", _recompute)


def test_recent_both_404_is_retryable_not_permanent_gap(monkeypatch):
    """maturity cutoff未満のbatchで両方404の場合、completed_with_warnings＋
    retryable_pending_count>=1（batch_gap=false）で記録されmissing対象に残ること
    （8回目レビュー修正②の直接回帰テスト）。その後正常取得できればcompletedへ遷移する。"""
    _stub_no_config(monkeypatch)
    _stub_downstream_services(monkeypatch)
    fixed_now = stream.datetime(2026, 9, 7, 6, 30, 0, tzinfo=stream.timezone.utc)
    _freeze_now(monkeypatch, fixed_now)
    batch_timestamp = "20260907060000"  # now-30分、maturity cutoff(既定6h)未満

    def fake_download_404(kind, bt, **kwargs):
        raise FileNotFoundError(f"not found: {kind} {bt}")
    monkeypatch.setattr(stream, "download_batch_file", fake_download_404)

    client = FakeSupabaseClient()
    # bypass_maturity_clamp=Trueで、意図的にまだmatureでないこのbatchを実際に試行する
    # （通常運用のmaturity clampはlist側でこのbatchをそもそも対象外にしてしまうため）。
    result = stream.run_gdelt_structured_ingestion(
        client, since=batch_timestamp, until=batch_timestamp, max_batches=1,
        bypass_maturity_clamp=True, is_demo=False)
    batch = result["batches"][0]
    assert batch["status"] == "completed_with_warnings"
    run_row = client.tables["geo_ingestion_runs"][0]
    assert run_row["request_parameters"]["batch_gap"] is False
    assert run_row["request_parameters"]["retryable_pending_count"] >= 1

    missing = stream.list_missing_batch_timestamps(
        client, since=batch_timestamp, until=batch_timestamp, bypass_maturity_clamp=True, is_demo=False)
    assert missing == [batch_timestamp]

    # 後続runで正常取得できればcompletedへ遷移する
    def fake_download_ok(kind, bt, **kwargs):
        if kind == "mentions":
            return b""
        return b""
    monkeypatch.setattr(stream, "download_batch_file", fake_download_ok)
    result2 = stream.run_gdelt_structured_ingestion(
        client, since=batch_timestamp, until=batch_timestamp, max_batches=1,
        bypass_maturity_clamp=True, is_demo=False)
    assert result2["batches"][0]["status"] == "completed"
    missing2 = stream.list_missing_batch_timestamps(
        client, since=batch_timestamp, until=batch_timestamp, bypass_maturity_clamp=True, is_demo=False)
    assert missing2 == []


def test_one_sided_404_stays_retryable_not_falsely_completed(monkeypatch):
    """MentionsのみまたはGKGのみが404の場合、誤ってcompletedにならないこと
    （8回目レビュー修正②の核心、直接回帰テスト）。"""
    _stub_no_config(monkeypatch)
    _stub_downstream_services(monkeypatch)
    batch_timestamp = "20260101000000"  # 十分古い（maturity cutoff超）

    def fake_download(kind, bt, **kwargs):
        if kind == "mentions":
            raise FileNotFoundError("mentions missing")
        return b""  # gkgは成功（0行）
    monkeypatch.setattr(stream, "download_batch_file", fake_download)

    client = FakeSupabaseClient()
    result = stream.run_gdelt_structured_ingestion(
        client, since=batch_timestamp, until=batch_timestamp, max_batches=1, is_demo=False)
    batch = result["batches"][0]
    assert batch["status"] == "completed_with_warnings"  # completedへ誤って倒れない
    run_row = client.tables["geo_ingestion_runs"][0]
    assert run_row["request_parameters"]["batch_gap"] is False
    assert run_row["request_parameters"]["retryable_pending_count"] >= 1


def test_permanent_mature_gap_marked_completed_and_excluded_forever(monkeypatch):
    """maturity cutoffを超えたbatchで両方404の場合のみstatus=completed＋batch_gap=trueで
    記録され、以後missing対象から恒久的に外れること（直接回帰テスト）。"""
    _stub_no_config(monkeypatch)
    _stub_downstream_services(monkeypatch)
    batch_timestamp = "20260101000000"  # 十分古い

    def fake_download_404(kind, bt, **kwargs):
        raise FileNotFoundError("gap")
    monkeypatch.setattr(stream, "download_batch_file", fake_download_404)

    client = FakeSupabaseClient()
    result = stream.run_gdelt_structured_ingestion(
        client, since=batch_timestamp, until=batch_timestamp, max_batches=1, is_demo=False)
    batch = result["batches"][0]
    assert batch["status"] == "completed"
    run_row = client.tables["geo_ingestion_runs"][0]
    assert run_row["request_parameters"]["batch_gap"] is True

    missing = stream.list_missing_batch_timestamps(
        client, since=batch_timestamp, until=batch_timestamp, bypass_maturity_clamp=True, is_demo=False)
    assert missing == []


def test_recompute_situation_links_called_once_per_run_not_per_batch(monkeypatch):
    """recompute_situation_linksが複数batch処理時に1回だけ呼ばれること
    （catch-up効率化の直接回帰テスト）。"""
    _stub_no_config(monkeypatch)
    recompute_calls = []
    _stub_downstream_services(monkeypatch, recompute_calls=recompute_calls)

    def fake_download_404(kind, bt, **kwargs):
        raise FileNotFoundError("gap")
    monkeypatch.setattr(stream, "download_batch_file", fake_download_404)

    client = FakeSupabaseClient()
    stream.run_gdelt_structured_ingestion(
        client, since="20260101000000", until="20260101003000", max_batches=3, is_demo=False)
    assert len(recompute_calls) == 1


def test_run_gdelt_stream_catchup_stops_on_no_progress(monkeypatch):
    """同一batchが繰り返し失敗し続ける場合、max_calls回連打せずno_progressで停止すること
    （直接回帰テスト）。"""
    _stub_no_config(monkeypatch)

    def fake_structured_ingestion(client, **kw):
        return {"batches": []}  # missing batchを一切解決しない
    monkeypatch.setattr(stream, "run_gdelt_structured_ingestion", fake_structured_ingestion)
    monkeypatch.setattr(stream, "list_missing_batch_timestamps",
                         lambda client, **kw: ["20260101000000"])  # 常に同じbatchが残り続ける

    client = FakeSupabaseClient()
    result = stream.run_gdelt_stream_catchup(
        client, since="20260101000000", until="20260101000000", max_calls=10, is_demo=False)
    assert result["calls_made"] == 1
    assert result["stopped_reason"] == "no_progress"


def test_run_gdelt_stream_catchup_survives_mid_call_crash(monkeypatch):
    """Phase A（Collection Reliability、2026-10-05）: run_gdelt_structured_ingestion呼び出し中に
    未捕捉例外（社内プロキシ/Supabase側の一時的なネットワーク障害）が起きても、catchup全体を
    クラッシュさせず、stopped_reason="error"として既存のwatermark再計算・summary run記録まで
    正常に完了すること。実DBでこの未捕捉クラッシュによりgeo_ingestion_runsがstatus=running
    のまま復旧不能で残留する事象を確認したための回帰テスト。"""
    _stub_no_config(monkeypatch)

    def flaky_structured_ingestion(client, **kw):
        raise RuntimeError("simulated transient network failure")

    monkeypatch.setattr(stream, "run_gdelt_structured_ingestion", flaky_structured_ingestion)
    monkeypatch.setattr(stream, "list_missing_batch_timestamps",
                         lambda client, **kw: ["20260101000000"])

    client = FakeSupabaseClient()
    result = stream.run_gdelt_stream_catchup(
        client, since="20260101000000", until="20260101000000", max_calls=10, is_demo=False)

    assert result["calls_made"] == 0
    assert result["stopped_reason"] == "error"
    assert result["last_error"] and "simulated transient network failure" in result["last_error"]
    summary_rows = [r for r in client.tables["geo_ingestion_runs"]
                    if r["connector_type"] == stream.CATCHUP_SUMMARY_CONNECTOR_TYPE]
    assert len(summary_rows) == 1
    assert summary_rows[0]["status"] == "completed_with_warnings"
    assert summary_rows[0]["error_summary"] and "simulated transient network failure" in summary_rows[0]["error_summary"]


def test_run_gdelt_stream_catchup_promotion_incomplete_catchup_rejected(monkeypatch):
    """不完全なcatch-up（max_calls到達、進捗はあるが完了しない）はpromoteされないこと
    （直接回帰テスト）。"""
    _stub_no_config(monkeypatch)
    state = {"remaining": 5}

    def fake_structured_ingestion(client, **kw):
        state["remaining"] = max(0, state["remaining"] - 1)
        return {"batches": []}

    def fake_missing(client, **kw):
        return [f"batch-{i}" for i in range(state["remaining"])]

    monkeypatch.setattr(stream, "run_gdelt_structured_ingestion", fake_structured_ingestion)
    monkeypatch.setattr(stream, "list_missing_batch_timestamps", fake_missing)

    client = FakeSupabaseClient()
    result = stream.run_gdelt_stream_catchup(
        client, since="20260101000000", until="20260101003000", max_calls=2,
        promote_to_checkpoint=True, is_demo=False)
    assert result["stopped_reason"] == "max_calls_reached"
    assert result["remaining_batch_count"] > 0
    assert result["checkpoint_scope"] == "adhoc"
    assert result["promotion_rejected_reason"] == "incomplete_catchup"


def test_run_gdelt_stream_catchup_promotion_bootstrap_when_no_existing_watermark(monkeypatch):
    """既存watermarkが無い場合（Initial Load初回）は、そのrange自身がbootstrap checkpoint
    としてpromoteされること（直接回帰テスト）。"""
    _stub_no_config(monkeypatch)
    since, until = "20260301000000", "20260301003000"
    expected = stream.list_expected_batch_timestamps(since, until)
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [
        {"connector_type": "gdelt_stream", "status": "completed", "is_demo": False,
         "request_parameters": {"batch_timestamp": bt}}
        for bt in expected
    ]})
    monkeypatch.setattr(stream, "run_gdelt_structured_ingestion",
                         lambda client, **kw: {"batches": []})

    result = stream.run_gdelt_stream_catchup(
        client, since=since, until=until, promote_to_checkpoint=True, is_demo=False)
    assert result["stopped_reason"] == "caught_up"
    assert result["checkpoint_scope"] == "incremental"
    assert result["completed_through"] == until
    assert result["promotion_rejected_reason"] is None


def test_run_gdelt_stream_catchup_promotion_rejects_gap_between_existing_and_new_range(monkeypatch):
    """既存watermarkと新rangeの間にgapがある場合、promotionがcheckpoint_gapで拒否されること
    （純粋な後退のケースも同じ理由で拒否されることを含む、直接回帰テスト）。"""
    _stub_no_config(monkeypatch)
    monkeypatch.setattr(stream, "run_gdelt_structured_ingestion", lambda client, **kw: {"batches": []})

    since, until = "20260301100000", "20260301103000"
    expected = stream.list_expected_batch_timestamps(since, until)
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [
        # 定常watermarkは20260301090000（このrangeより1時間前）まで確定済み。20260301090000と
        # 20260301100000の間は一切fetchされていない（gap）。
        {"connector_type": "gdelt_stream_catchup", "status": "completed", "is_demo": False,
         "started_at": "2026-03-01T09:05:00+00:00",
         "request_parameters": {"completed_through": "20260301090000", "checkpoint_scope": "incremental"}},
    ] + [
        {"connector_type": "gdelt_stream", "status": "completed", "is_demo": False,
         "request_parameters": {"batch_timestamp": bt}}
        for bt in expected
    ]})
    result = stream.run_gdelt_stream_catchup(
        client, since=since, until=until, promote_to_checkpoint=True, is_demo=False)
    assert result["stopped_reason"] == "caught_up"
    assert result["checkpoint_scope"] == "adhoc"
    assert result["promotion_rejected_reason"] == "checkpoint_gap"
    # 定常watermarkは巻き戻らない
    assert stream._find_last_catchup_watermark(client, is_demo=False) == "20260301090000"
