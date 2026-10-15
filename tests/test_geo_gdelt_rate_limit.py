"""Phase D.1: GDELT News DOC 429対策（全体レート制御・排他・429の扱い）のテスト。

実GDELT APIは呼び出さない（requests.getをモック）。状態ファイルはtests/conftest.pyで各テスト専用の
一時ディレクトリに切り替わる（本番のlogs/には触れない）。
"""
import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_connector_gdelt as gdelt  # noqa: E402
import geo_gdelt_rate_limit as rl  # noqa: E402
import geo_scheduler_service as svc  # noqa: E402
import geo_scheduler_tick as tick  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _resp(status=200, payload=None, headers=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.text = text or json.dumps(payload or {})
    r.json.return_value = payload or {}
    r.headers = headers or {}
    return r


class Clock:
    """time.sleepを実際には待たず、待った秒数を記録する（待ち時間そのものを検証できる）。"""

    def __init__(self, monkeypatch):
        self.sleeps = []
        monkeypatch.setattr(time, "sleep", lambda s: self.sleeps.append(s))


# ---- 最低呼び出し間隔 -------------------------------------------------------------------

def test_minimum_interval_is_enforced_between_consecutive_requests(monkeypatch):
    clock = Clock(monkeypatch)
    rl.wait_turn(5)                        # 1回目: 待たない
    assert clock.sleeps == []
    waited = rl.wait_turn(5)               # 2回目: 6秒（公式5秒+余裕）以上の間隔を空ける
    assert waited >= rl.MIN_INTERVAL_SECONDS - 0.2
    assert rl.MIN_INTERVAL_SECONDS == 6.0


def test_configured_interval_below_minimum_is_raised_to_minimum(monkeypatch):
    Clock(monkeypatch)
    rl.wait_turn(0)
    assert rl.wait_turn(0) >= rl.MIN_INTERVAL_SECONDS - 0.2   # config.jsonで0や5を指定しても6秒を守る


def test_concurrent_threads_never_share_a_slot(monkeypatch):
    """複数スレッドが同時に呼んでも、予約された送信時刻は全て最低間隔以上に離れる。"""
    Clock(monkeypatch)
    slots, lock = [], threading.Lock()

    def worker():
        wait = rl.wait_turn(6)
        with lock:
            slots.append(time.time() + wait)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    slots.sort()
    gaps = [b - a for a, b in zip(slots, slots[1:])]
    assert len(slots) == 8 and min(gaps) >= rl.MIN_INTERVAL_SECONDS - 0.3


def test_separate_processes_share_the_same_interval(tmp_path):
    """別プロセスでも、同じ状態ファイルで間隔が守られる（従来のプロセス内変数では守れなかった点）。"""
    code = ("import time, sys; sys.path.insert(0, r'%s'); import geo_gdelt_rate_limit as rl; "
            "rl.MIN_INTERVAL_SECONDS = 1.0; rl.JITTER_SECONDS = 0.0; rl.wait_turn(1.0); print(time.time())"
            % Path(__file__).parent.parent)
    env = {**__import__("os").environ, "GEO_GDELT_STATE_DIR": str(tmp_path / "shared_state")}
    procs = [subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, text=True) for _ in range(3)]
    times = sorted(float(p.communicate(timeout=60)[0].strip().splitlines()[-1]) for p in procs)
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert min(gaps) >= 0.85, gaps          # 3プロセスが同時に叩かず、1秒おきに順番に送信


# ---- Retry-After / 429 -------------------------------------------------------------------

def test_parse_retry_after_forms():
    assert rl.parse_retry_after("7") == 7.0
    assert rl.parse_retry_after(12) == 12.0
    future = (datetime.now(timezone.utc) + timedelta(seconds=40)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert 30 <= rl.parse_retry_after(future) <= 41
    assert rl.parse_retry_after("999999") == rl.MAX_RETRY_AFTER_SECONDS      # 異常に大きい値は上限
    assert rl.parse_retry_after("abc") is None and rl.parse_retry_after(None) is None
    assert rl.parse_retry_after(MagicMock()) is None


def test_429_with_retry_after_is_respected_and_shared_across_processes(monkeypatch):
    clock = Clock(monkeypatch)
    calls = {"n": 0}

    def fake_get(url, params, proxies, verify, timeout):
        calls["n"] += 1
        return _resp(429, headers={"Retry-After": "20"}) if calls["n"] == 1 else _resp(200, {"articles": []})

    monkeypatch.setattr(gdelt.requests, "get", fake_get)
    assert gdelt.search_articles("x", request_interval_seconds=0) == []
    assert calls["n"] == 2
    assert max(clock.sleeps) >= 19.5                         # Retry-After(20秒)を待ってから再試行
    assert rl.get_state()["last_429_age_seconds"] is not None  # 429の記録は共有状態にも残る


def test_429_without_retry_after_uses_backoff_not_fast_retry(monkeypatch):
    clock = Clock(monkeypatch)
    n = {"c": 0}

    def fake_get(url, params, proxies, verify, timeout):
        n["c"] += 1
        return _resp(429, text="rate limited") if n["c"] < 3 else _resp(200, {"articles": []})

    monkeypatch.setattr(gdelt.requests, "get", fake_get)
    gdelt.search_articles("x", request_interval_seconds=0)
    assert n["c"] == 3
    assert sum(1 for s in clock.sleeps if s >= 29) >= 2      # 429のたびに30秒以上（5秒おきの高速再試行をしない）


def test_429_stops_at_max_retries_then_enters_long_cooldown(monkeypatch):
    Clock(monkeypatch)
    n = {"c": 0}

    def fake_get(url, params, proxies, verify, timeout):
        n["c"] += 1
        return _resp(429, text="Please limit requests to one every 5 seconds")

    monkeypatch.setattr(gdelt.requests, "get", fake_get)
    try:
        gdelt.search_articles("x", request_interval_seconds=0)
        assert False
    except RuntimeError as e:
        assert "GDELT status=429" in str(e)
    assert n["c"] == rl.RATE_LIMIT_POLICY["max_429_retries"] == 3      # 無限に・大量に再試行しない（従来は5回）
    assert rl.get_state()["cooldown_remaining_seconds"] >= 590          # 全体で10分休む


def test_during_cooldown_calls_fail_fast_without_hitting_gdelt(monkeypatch):
    Clock(monkeypatch)
    rl.register_429(None, consecutive_429=rl.RATE_LIMIT_POLICY["max_429_retries"])
    called = []
    monkeypatch.setattr(gdelt.requests, "get", lambda *a, **k: called.append(1) or _resp(200, {"articles": []}))
    try:
        gdelt.search_articles("x", request_interval_seconds=0)
        assert False
    except rl.GdeltCooldownError as e:
        assert "GDELT status=429" in str(e)
        assert svc.classify_error(str(e)) == "retryable"        # Schedulerはbackoffして再試行する
    assert called == []                                          # クールダウン中はGDELTへ送らない


def test_5xx_and_network_errors_still_retry_with_backoff(monkeypatch):
    Clock(monkeypatch)
    n = {"c": 0}

    def fake_get(url, params, proxies, verify, timeout):
        n["c"] += 1
        if n["c"] == 1:
            raise gdelt.requests.ConnectionError("boom")
        return _resp(503, text="x") if n["c"] == 2 else _resp(200, {"articles": []})

    monkeypatch.setattr(gdelt.requests, "get", fake_get)
    assert gdelt.search_articles("x", request_interval_seconds=0) == []
    assert n["c"] == 3


# ---- 排他（定期・catch-up・手動の同時実行防止） --------------------------------------------

def test_second_collection_is_refused_while_one_is_running_and_reentry_is_allowed():
    with rl.collection_lease():
        with rl.collection_lease():                  # 同じスレッドの入れ子（batch→catch-up→1 query）は通す
            assert rl.lease_status()["held"] is True
        result = {}

        def other():
            try:
                with rl.collection_lease():
                    result["ok"] = True
            except rl.GdeltBusyError:
                result["busy"] = True
        t = threading.Thread(target=other)
        t.start()
        t.join()
        assert result == {"busy": True}              # 別スレッド（別Job/手動）は実行できない
    assert rl.lease_status()["held"] is False        # 終われば解放される
    with rl.collection_lease():
        pass


def test_lease_from_a_dead_process_becomes_stale_and_is_reclaimed():
    import os
    rl.state_dir().mkdir(parents=True, exist_ok=True)
    p = rl._lease_path()
    p.write_text("gdelt_doc pid=1 (落ちたプロセスの残り)")
    old = time.time() - rl.LEASE_STALE_SECONDS - 10
    os.utime(p, (old, old))
    with rl.collection_lease():                      # 30分以上更新が無い印は無効（永久にskipしない）
        assert rl.lease_status()["held"] is True


def test_catchup_running_blocks_scheduled_collection_and_manual_trigger(monkeypatch):
    """catch-up実行中に、定期実行（run_enabled_external_signal_queries）や手動実行（run_gdelt_ingestion）が
    並行してGDELTへ来ない。"""
    client = FakeSupabaseClient(tables={"geo_external_signal_queries": []})
    errors = {}

    def contender(name, fn):
        try:
            fn()
        except rl.GdeltBusyError:
            errors[name] = "busy"

    with rl.collection_lease():                      # catch-up（またはどれかの収集）が実行中
        ts = [threading.Thread(target=contender, args=("scheduled", lambda: gdelt.run_enabled_external_signal_queries(client))),
              threading.Thread(target=contender, args=("manual", lambda: gdelt.run_gdelt_ingestion(client, query_row={"query_id": "q", "query": "x"}))),
              threading.Thread(target=contender, args=("catchup2", lambda: gdelt.run_gdelt_doc_catchup_all_enabled(client, since="20260101000000", until="20260102000000")))]
        [t.start() for t in ts]
        [t.join() for t in ts]
    assert errors == {"scheduled": "busy", "manual": "busy", "catchup2": "busy"}


def test_scheduler_skips_instead_of_failing_when_another_collection_is_running(monkeypatch):
    def busy(client, job, **kw):
        raise rl.GdeltBusyError("別のGDELT DOC収集が実行中です")
    monkeypatch.setattr(svc, "_EXECUTORS", {"COLLECTION_GDELT_DOC": busy})
    registry = {"jobs": {"COLLECTION_GDELT_DOC": {"job_type": "collection", "connector_type": "gdelt", "cadence": "daily"}},
                "manual_only_jobs": {}}
    res = svc.run_job(FakeSupabaseClient(tables={"geo_ingestion_runs": []}), "COLLECTION_GDELT_DOC", registry=registry)
    assert res["outcome"] == "skipped" and res["retryable"] is True      # 失敗として数えない


def test_tick_does_not_start_doc_job_while_lease_is_held_and_writes_no_job_row(monkeypatch):
    registry = {"defaults": {}, "jobs": {"COLLECTION_GDELT_DOC": {
        "job_type": "collection", "connector_type": "gdelt", "cadence": "daily"}}, "manual_only_jobs": {}}
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": []})
    with rl.collection_lease():
        res = tick.execute_job(client, "COLLECTION_GDELT_DOC", registry, now=datetime.now(timezone.utc), trigger="tick")
    assert res["outcome"] == "skipped" and "locked" in res["reason"]
    assert client.tables["geo_ingestion_runs"] == []


# ---- 重複防止（同じ期間の再実行） ----------------------------------------------------------

def test_rerunning_the_same_window_does_not_duplicate_signals(monkeypatch):
    import config_utils
    monkeypatch.setattr(config_utils, "load_config", lambda: {})
    Clock(monkeypatch)

    def fake_get(url, params, proxies, verify, timeout):
        if params.get("mode") == "artlist":
            return _resp(200, {"articles": [{"url": "https://example.com/a1", "title": "Taiwan Strait", "seendate": "20260901T081500Z",
                                              "domain": "example.com", "language": "English", "sourcecountry": "Taiwan"}]})
        return _resp(200, {"timeline": []})

    monkeypatch.setattr(gdelt.requests, "get", fake_get)
    client = FakeSupabaseClient()
    q = client.insert("geo_external_signal_queries", [{"name": "T", "provider": "gdelt", "query": "Taiwan Strait",
                                                       "article_lookback": "7d", "metric_lookback": "30d", "max_records": 250}])[0]
    for _ in range(3):
        gdelt.run_gdelt_ingestion(client, query_row=q, since="20260901000000", until="20260902000000", fetch_metrics=False)
    assert len(client.tables["geo_external_signals"]) == 1
    assert len(client.tables["geo_external_signal_query_matches"]) == 1


def test_operations_overview_exposes_gdelt_rate_state():
    ov = tick.scheduler_overview(FakeSupabaseClient(tables={"geo_ingestion_runs": []}))
    assert ov["gdelt_doc"]["min_interval_seconds"] == 6.0 and ov["gdelt_doc"]["collection_running"] is False


# ---- 失敗した窓でcheckpointを進めない（記事鮮度が回復しなかった根本原因の回帰テスト） ------------

def _seed_incremental_checkpoint(client, query_id, completed_through):
    client.insert("geo_ingestion_runs", [{
        "source_id": None, "connector_type": "gdelt_doc_catchup", "run_type": "cli", "status": "completed",
        "is_demo": False, "started_at": (datetime.now(timezone.utc) - timedelta(days=4)).isoformat(),
        "request_parameters": {"query_id": query_id, "completed_through": completed_through,
                               "checkpoint_scope": "incremental"}}])


def test_failed_window_does_not_advance_checkpoint_and_retry_resumes_from_it(monkeypatch):
    import config_utils
    monkeypatch.setattr(config_utils, "load_config", lambda: {})
    Clock(monkeypatch)
    client = FakeSupabaseClient()
    q = client.insert("geo_external_signal_queries", [{"name": "T", "provider": "gdelt", "query": "Taiwan Strait",
                                                       "article_lookback": "7d", "metric_lookback": "30d", "max_records": 250}])[0]
    w0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=3)
    fmt = lambda d: d.strftime("%Y%m%d%H%M%S")  # noqa: E731
    _seed_incremental_checkpoint(client, q["query_id"], fmt(w0))
    failing_start = fmt(w0 + timedelta(days=1))
    state = {"fail": True}
    seen_starts = []

    def fake_get(url, params, proxies, verify, timeout):
        start = params.get("startdatetime")
        seen_starts.append(start)
        if state["fail"] and start == failing_start:
            return _resp(429, text="Please limit requests to one every 5 seconds")
        return _resp(200, {"articles": [{"url": f"https://example.com/{start}", "title": f"t{start}", "seendate": start[:8] + "T081500Z",
                                         "domain": "example.com", "language": "English", "sourcecountry": "Taiwan"}]})

    monkeypatch.setattr(gdelt.requests, "get", fake_get)
    first = gdelt.run_gdelt_doc_catchup(client, query_row=q, since=None, requested_by="batch")
    assert first["status"] == "failed" and len(first["failed_windows"]) == 1
    assert first["completed_through"] == failing_start                     # 失敗した窓の手前で止まる（飛び越えない）
    assert gdelt._find_last_doc_watermark(client, query_id=q["query_id"], is_demo=False) == failing_start
    n_after_first = len(client.tables["geo_external_signals"])

    # 429が解消した後の再実行: 失敗した窓から再開し、最後まで進む。取得済みの窓は重複しない
    state["fail"] = False
    rl._locked_update(lambda s: s.update(cooldown_until=0))               # クールダウン明け
    second = gdelt.run_gdelt_doc_catchup(client, query_row=q, since=None, requested_by="batch")
    assert second["status"] == "completed" and second["failed_windows"] == []
    assert gdelt._find_last_doc_watermark(client, query_id=q["query_id"], is_demo=False) > failing_start
    assert failing_start in seen_starts[seen_starts.index(failing_start) + 1:]   # 失敗した窓が再取得された
    signals = client.tables["geo_external_signals"]
    assert len({s["url"] for s in signals}) == len(signals) and len(signals) > n_after_first


def test_doc_job_reports_failure_so_scheduler_backs_off(monkeypatch):
    """窓の取得失敗はJobの失敗（retryable）としてSchedulerに伝わる（以前は「完了」と記録されていた）。"""
    res = {"outcome": "executed", "result": {"query_results": [
        {"query_id": "q1", "status": "failed", "error_summary": "GDELT status=429: Please limit requests"}]}}
    s = tick.summarize_outcome(res)
    assert s["status"] == "failed" and s["retry"] == "retryable"


def test_failed_catchup_summary_is_recorded_as_failed_but_progress_is_kept(monkeypatch):
    """失敗した窓があるcatch-upのサマリー行は'failed'（Schedulerが成功と見なさない）。ただし、そこまでの
    進捗(completed_through)は次回の開始点として保持される。"""
    import config_utils
    monkeypatch.setattr(config_utils, "load_config", lambda: {})
    Clock(monkeypatch)
    client = FakeSupabaseClient()
    q = client.insert("geo_external_signal_queries", [{"name": "T", "provider": "gdelt", "query": "Taiwan Strait",
                                                       "article_lookback": "7d", "metric_lookback": "30d", "max_records": 250}])[0]
    w0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=2)
    _seed_incremental_checkpoint(client, q["query_id"], w0.strftime("%Y%m%d%H%M%S"))
    monkeypatch.setattr(gdelt.requests, "get", lambda *a, **k: _resp(429, text="Please limit requests"))
    res = gdelt.run_gdelt_doc_catchup(client, query_row=q, since=None, requested_by="batch")
    summary = [r for r in client.tables["geo_ingestion_runs"] if r["connector_type"] == "gdelt_doc_catchup"][-1]
    assert res["status"] == "failed" and summary["status"] == "failed"
    assert gdelt._find_last_doc_watermark(client, query_id=q["query_id"], is_demo=False) == w0.strftime("%Y%m%d%H%M%S")
    # Schedulerから見て「成功」ではない → 次のtickで（backoffに従って）再試行される
    registry = {"defaults": {}, "jobs": {"J": {"job_type": "collection", "connector_type": "gdelt",
                                               "catchup_connector_type": "gdelt_doc_catchup", "cadence": "daily"}}}
    ev = tick.evaluate_job("J", registry["jobs"]["J"], registry, tick.load_recent_runs(client, now=datetime.now(timezone.utc)),
                           now=datetime.now(timezone.utc))
    assert ev["state"] == "due"


def test_global_freshness_ignores_demo_signals():
    """検証用(is_demo=true)の記事が、記事データの鮮度（fresh/stale）を引き上げない。"""
    import geo_source_library_service as lib
    now = datetime.now(timezone.utc)
    client = FakeSupabaseClient(tables={"geo_external_signals": [
        {"external_signal_id": "demo", "provider": "gdelt", "is_demo": True, "published_at": now.isoformat(), "created_at": now.isoformat()},
        {"external_signal_id": "real", "provider": "gdelt", "is_demo": False, "published_at": (now - timedelta(days=10)).isoformat(),
         "created_at": (now - timedelta(days=10)).isoformat()}]})
    f = lib.get_global_freshness(client)
    assert f["status"] == "stale" and f["days_since"] >= 9


def test_tick_does_not_start_doc_job_during_429_cooldown(monkeypatch):
    """クールダウン中は起動しても即失敗するだけ。Jobを起動せず、失敗行も増やさない。"""
    registry = {"defaults": {}, "jobs": {"COLLECTION_GDELT_DOC": {
        "job_type": "collection", "connector_type": "gdelt", "cadence": "daily"}}, "manual_only_jobs": {}}
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": []})
    rl.register_429(None, consecutive_429=rl.RATE_LIMIT_POLICY["max_429_retries"])      # 10分のクールダウン
    res = tick.execute_job(client, "COLLECTION_GDELT_DOC", registry, now=datetime.now(timezone.utc), trigger="tick")
    assert res["outcome"] == "skipped" and "クールダウン" in res["reason"]
    assert client.tables["geo_ingestion_runs"] == []


def test_manual_trigger_api_returns_409_when_another_collection_is_running(monkeypatch):
    """手動実行（API）は、別のDOC収集が実行中なら二重に叩かず409「現在実行中です」を返す。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import geo_external_signal_api as api
    client = FakeSupabaseClient(tables={"geo_external_signal_queries": [
        {"query_id": "q1", "provider": "gdelt", "query": "x", "name": "t"}]})
    monkeypatch.setattr(api, "_client", client)
    app = FastAPI()
    app.include_router(api.geo_external_signal_router)
    with rl.collection_lease():                      # 定期実行またはcatch-upが実行中
        # 別スレッドから叩く（同一スレッドの入れ子は再入として通るため）
        out = {}
        t = threading.Thread(target=lambda: out.update(r=TestClient(app).post("/api/geo/external-signals/fetch", json={"query_id": "q1"})))
        t.start()
        t.join()
    assert out["r"].status_code == 409 and "現在実行中" in out["r"].json()["detail"]
