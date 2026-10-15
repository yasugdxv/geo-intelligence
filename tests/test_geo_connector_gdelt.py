"""地政学ナレッジ＆インテリジェンスDB Phase1: GDELT DOC 2.0 Connectorのテスト。
実GDELT APIは呼び出さない（requests.getをモックする）。
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import config_utils  # noqa: E402
import geo_connector_gdelt  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _stub_no_config(monkeypatch):
    monkeypatch.setattr(config_utils, "load_config", lambda: {})


def _mock_response(status_code=200, payload=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text or json.dumps(payload or {})
    resp.json.return_value = payload or {}
    return resp


def test_search_articles_builds_artlist_params(monkeypatch):
    captured = {}

    def fake_get(url, params, proxies, verify, timeout):
        captured["params"] = params
        return _mock_response(200, {"articles": [{"url": "https://example.com/a", "title": "t"}]})

    monkeypatch.setattr(geo_connector_gdelt.requests, "get", fake_get)
    articles = geo_connector_gdelt.search_articles(
        "Taiwan Strait", timespan="7d", maxrecords=100, request_interval_seconds=0)

    assert captured["params"]["mode"] == "artlist"
    assert captured["params"]["query"] == "Taiwan Strait"
    assert captured["params"]["timespan"] == "7d"
    assert captured["params"]["maxrecords"] == "100"
    assert articles == [{"url": "https://example.com/a", "title": "t"}]


def test_timeline_modes_use_correct_gdelt_mode_strings(monkeypatch):
    """GDELT公式仕様: TimelineTone(=timelinetone)はToneChart(分布ヒストグラム)とは別モード。
    TimelineSourceCountryは媒体所在国別Coverageの専用モード。誤ったmode文字列を使うと
    無関係なデータが返るため、パラメータ組み立てを厳密に確認する。"""
    captured_modes = []

    def fake_get(url, params, proxies, verify, timeout):
        captured_modes.append(params["mode"])
        return _mock_response(200, {"timeline": []})

    monkeypatch.setattr(geo_connector_gdelt.requests, "get", fake_get)
    geo_connector_gdelt.get_coverage_timeline("x", request_interval_seconds=0)
    geo_connector_gdelt.get_tone_timeline("x", request_interval_seconds=0)
    geo_connector_gdelt.get_source_country_timeline("x", request_interval_seconds=0)

    assert captured_modes == ["timelinevolraw", "timelinetone", "timelinesourcecountry"]


def test_search_articles_by_source_country_appends_filter(monkeypatch):
    captured = {}

    def fake_get(url, params, proxies, verify, timeout):
        captured["query"] = params["query"]
        return _mock_response(200, {"articles": []})

    monkeypatch.setattr(geo_connector_gdelt.requests, "get", fake_get)
    geo_connector_gdelt.search_articles_by_source_country(
        "Taiwan Strait", "TW", request_interval_seconds=0)

    assert captured["query"] == "Taiwan Strait sourcecountry:TW"


def test_request_retries_on_429_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, params, proxies, verify, timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            return _mock_response(429, text="rate limited")
        return _mock_response(200, {"articles": []})

    monkeypatch.setattr(geo_connector_gdelt.requests, "get", fake_get)
    monkeypatch.setattr(geo_connector_gdelt.time, "sleep", lambda s: None)
    articles = geo_connector_gdelt.search_articles("x", request_interval_seconds=0)

    assert calls["n"] == 3
    assert articles == []


def test_request_gives_up_after_max_retries(monkeypatch):
    def fake_get(url, params, proxies, verify, timeout):
        return _mock_response(500, text="server error")

    monkeypatch.setattr(geo_connector_gdelt.requests, "get", fake_get)
    monkeypatch.setattr(geo_connector_gdelt.time, "sleep", lambda s: None)

    try:
        geo_connector_gdelt.search_articles("x", request_interval_seconds=0)
        assert False, "RuntimeErrorが送出されるはず"
    except RuntimeError as e:
        assert "GDELT" in str(e)


def test_run_gdelt_ingestion_disabled_by_config(monkeypatch):
    monkeypatch.setattr(config_utils, "load_config",
                         lambda: {"geo_external_signals": {"gdelt": {"enabled": False}}})
    client = FakeSupabaseClient()
    query_row = {"query_id": "q-1", "query": "Taiwan Strait", "article_lookback": "7d",
                 "metric_lookback": "30d", "max_records": 250}

    try:
        geo_connector_gdelt.run_gdelt_ingestion(client, query_row=query_row)
        assert False, "GdeltDisabledErrorが送出されるはず"
    except geo_connector_gdelt.GdeltDisabledError:
        pass
    # 無効時はIngestion Run自体を作らない
    assert client.tables.get("geo_ingestion_runs", []) == []


def test_run_gdelt_ingestion_end_to_end_with_fake_client(monkeypatch):
    """1記事＋3種のtimeline pointを取得し、geo_external_signals/query_matches/metrics
    へ正しく保存されることを確認する（GDELT APIはモック）。"""
    _stub_no_config(monkeypatch)

    def fake_get(url, params, proxies, verify, timeout):
        mode = params.get("mode")
        if mode == "artlist":
            return _mock_response(200, {"articles": [{
                "url": "https://example.com/article1", "title": "Taiwan Strait tensions",
                "seendate": "20260901T081500Z", "domain": "example.com",
                "language": "English", "sourcecountry": "Taiwan",
            }]})
        return _mock_response(200, {"timeline": [{
            "series": "Article Count" if mode != "timelinesourcecountry" else "Taiwan Volume Intensity",
            "data": [{"date": "20260901T080000Z", "value": 5, "norm": 1000}],
        }]})

    monkeypatch.setattr(geo_connector_gdelt.requests, "get", fake_get)
    monkeypatch.setattr(geo_connector_gdelt.time, "sleep", lambda s: None)

    client = FakeSupabaseClient()
    query_row = client.insert("geo_external_signal_queries", [{
        "name": "Taiwan Strait", "provider": "gdelt", "query": "Taiwan Strait",
        "article_lookback": "7d", "metric_lookback": "30d", "max_records": 250,
    }])[0]

    result = geo_connector_gdelt.run_gdelt_ingestion(client, query_row=query_row)

    assert result["status"] in ("completed", "completed_with_warnings")
    assert result["counts"]["inserted_count"] == 1
    signals = client.tables["geo_external_signals"]
    assert len(signals) == 1
    assert signals[0]["source_role"] == "discovery"
    assert signals[0]["evidence_status"] == "unverified"

    matches = client.tables["geo_external_signal_query_matches"]
    assert len(matches) == 1
    assert matches[0]["query_id"] == query_row["query_id"]

    metrics = client.tables["geo_external_signal_metrics"]
    metric_types = {m["metric_type"] for m in metrics}
    assert metric_types == {"coverage_volume", "tone", "source_country_coverage"}


def test_run_gdelt_ingestion_is_demo_does_not_update_query_last_run_at(monkeypatch):
    """Phase A（Collection Reliability、2026-10-04）: is_demo=Trueのテスト実行は、
    geo_external_signal_queries.last_run_at（実運用のFreshness/Staleness判定が参照する値）
    を更新しないこと。実DBで、is_demoテスト実行がlast_run_atを書き換えてしまう事象を
    確認したための回帰テスト。"""
    _stub_no_config(monkeypatch)

    def fake_get(url, params, proxies, verify, timeout):
        mode = params.get("mode")
        if mode == "artlist":
            return _mock_response(200, {"articles": [{
                "url": "https://example.com/article1", "title": "Taiwan Strait tensions",
                "seendate": "20260901T081500Z", "domain": "example.com",
                "language": "English", "sourcecountry": "Taiwan",
            }]})
        return _mock_response(200, {"timeline": [{
            "series": "Article Count", "data": [{"date": "20260901T080000Z", "value": 5, "norm": 1000}],
        }]})

    monkeypatch.setattr(geo_connector_gdelt.requests, "get", fake_get)
    monkeypatch.setattr(geo_connector_gdelt.time, "sleep", lambda s: None)

    client = FakeSupabaseClient()
    query_row = client.insert("geo_external_signal_queries", [{
        "name": "Taiwan Strait", "provider": "gdelt", "query": "Taiwan Strait",
        "article_lookback": "7d", "metric_lookback": "30d", "max_records": 250,
        "last_run_at": None,
    }])[0]

    result = geo_connector_gdelt.run_gdelt_ingestion(client, query_row=query_row, is_demo=True)

    assert result["status"] in ("completed", "completed_with_warnings")
    refreshed = client.select(
        "geo_external_signal_queries", {"select": "*", "query_id": f"eq.{query_row['query_id']}"})[0]
    assert refreshed["last_run_at"] is None


def test_run_gdelt_ingestion_is_demo_flags_inserted_signals(monkeypatch):
    """Phase A（Collection Reliability、2026-10-04）: is_demo=Trueのテスト実行で新規挿入される
    geo_external_signalsは、is_demo=trueとして保存されること。実DBで、is_demo=Trueのテスト実行
    （gdelt-batch等）が本物の記事データを1,520件、is_demoフラグなし（本番同然）で
    geo_external_signalsへ挿入してしまっていた事象（bulk_upsert_signalsがis_demoを一切
    受け取っていなかったことが原因）の再発防止テスト。"""
    _stub_no_config(monkeypatch)

    def fake_get(url, params, proxies, verify, timeout):
        mode = params.get("mode")
        if mode == "artlist":
            return _mock_response(200, {"articles": [{
                "url": "https://example.com/article1", "title": "Taiwan Strait tensions",
                "seendate": "20260901T081500Z", "domain": "example.com",
                "language": "English", "sourcecountry": "Taiwan",
            }]})
        return _mock_response(200, {"timeline": [{
            "series": "Article Count", "data": [{"date": "20260901T080000Z", "value": 5, "norm": 1000}],
        }]})

    monkeypatch.setattr(geo_connector_gdelt.requests, "get", fake_get)
    monkeypatch.setattr(geo_connector_gdelt.time, "sleep", lambda s: None)

    client = FakeSupabaseClient()
    query_row = client.insert("geo_external_signal_queries", [{
        "name": "Taiwan Strait", "provider": "gdelt", "query": "Taiwan Strait",
        "article_lookback": "7d", "metric_lookback": "30d", "max_records": 250,
        "last_run_at": None,
    }])[0]

    result = geo_connector_gdelt.run_gdelt_ingestion(client, query_row=query_row, is_demo=True)

    assert result["status"] in ("completed", "completed_with_warnings")
    signals = client.tables["geo_external_signals"]
    assert len(signals) == 1
    assert signals[0]["is_demo"] is True


def test_run_gdelt_doc_catchup_derives_run_type_from_requested_by(monkeypatch):
    """Phase D（Production Scheduling、2026-10-06）: run_gdelt_doc_catchupが
    requested_by="batch"（Schedulerの既定値）で呼ばれた場合、window単位の実行行
    （connector_type="gdelt"）・catchup summary行（connector_type="gdelt_doc_catchup"）の
    両方でrun_type="scheduled_external"がgeo_ingestion_runsへ正しく記録されること。
    以前はrun_gdelt_ingestion呼び出し側でrun_typeが一切渡されておらず、常にdefaultの
    "manual"（summary行は"cli"固定）になっており、「本当にSchedulerが動いたか」を
    事後に判別できなかった（run_type="scheduled_external"の履歴が原理的に作られない
    バグ）。requested_by="cli"の既存挙動（run_type="cli"）は変えない。"""
    _stub_no_config(monkeypatch)

    def fake_get(url, params, proxies, verify, timeout):
        mode = params.get("mode")
        if mode == "artlist":
            return _mock_response(200, {"articles": []})
        return _mock_response(200, {"timeline": []})

    monkeypatch.setattr(geo_connector_gdelt.requests, "get", fake_get)
    monkeypatch.setattr(geo_connector_gdelt.time, "sleep", lambda s: None)

    client = FakeSupabaseClient()
    query_row = client.insert("geo_external_signal_queries", [{
        "name": "Taiwan Strait", "provider": "gdelt", "query": "Taiwan Strait",
        "article_lookback": "7d", "metric_lookback": "30d", "max_records": 250,
        "last_run_at": None,
    }])[0]

    geo_connector_gdelt.run_gdelt_doc_catchup(
        client, query_row=query_row, since="20260101000000", until="20260101010000",
        window_hours=24, requested_by="batch")

    runs = client.tables["geo_ingestion_runs"]
    window_runs = [r for r in runs if r["connector_type"] == "gdelt"]
    summary_runs = [r for r in runs if r["connector_type"] == "gdelt_doc_catchup"]
    assert window_runs and all(r["run_type"] == "scheduled_external" for r in window_runs)
    assert summary_runs and all(r["run_type"] == "scheduled_external" for r in summary_runs)


def test_run_gdelt_doc_catchup_cli_requested_by_keeps_run_type_cli(monkeypatch):
    """既存のCLI呼び出し（requested_by="cli"）はrun_type="cli"のまま変わらないこと。"""
    _stub_no_config(monkeypatch)

    def fake_get(url, params, proxies, verify, timeout):
        mode = params.get("mode")
        if mode == "artlist":
            return _mock_response(200, {"articles": []})
        return _mock_response(200, {"timeline": []})

    monkeypatch.setattr(geo_connector_gdelt.requests, "get", fake_get)
    monkeypatch.setattr(geo_connector_gdelt.time, "sleep", lambda s: None)

    client = FakeSupabaseClient()
    query_row = client.insert("geo_external_signal_queries", [{
        "name": "Taiwan Strait", "provider": "gdelt", "query": "Taiwan Strait",
        "article_lookback": "7d", "metric_lookback": "30d", "max_records": 250,
        "last_run_at": None,
    }])[0]

    geo_connector_gdelt.run_gdelt_doc_catchup(
        client, query_row=query_row, since="20260101000000", until="20260101010000",
        window_hours=24, requested_by="cli")

    runs = client.tables["geo_ingestion_runs"]
    assert all(r["run_type"] == "cli" for r in runs)


def test_run_gdelt_ingestion_idempotent_second_run_no_duplicate(monkeypatch):
    """同一Queryを2回実行しても記事・timeline pointが増殖しないこと（Dedup/冪等性）。"""
    _stub_no_config(monkeypatch)

    def fake_get(url, params, proxies, verify, timeout):
        mode = params.get("mode")
        if mode == "artlist":
            return _mock_response(200, {"articles": [{
                "url": "https://example.com/article1", "title": "Taiwan Strait tensions",
                "seendate": "20260901T081500Z", "domain": "example.com",
                "language": "English", "sourcecountry": "Taiwan",
            }]})
        return _mock_response(200, {"timeline": [{
            "series": "Article Count",
            "data": [{"date": "20260901T080000Z", "value": 5, "norm": 1000}],
        }]})

    monkeypatch.setattr(geo_connector_gdelt.requests, "get", fake_get)
    monkeypatch.setattr(geo_connector_gdelt.time, "sleep", lambda s: None)

    client = FakeSupabaseClient()
    query_row = client.insert("geo_external_signal_queries", [{
        "name": "Taiwan Strait", "provider": "gdelt", "query": "Taiwan Strait",
        "article_lookback": "7d", "metric_lookback": "30d", "max_records": 250,
    }])[0]

    first = geo_connector_gdelt.run_gdelt_ingestion(client, query_row=query_row)
    second = geo_connector_gdelt.run_gdelt_ingestion(client, query_row=query_row)

    assert first["counts"]["inserted_count"] == 1
    assert second["counts"]["inserted_count"] == 0
    assert second["counts"]["updated_count"] == 1
    assert len(client.tables["geo_external_signals"]) == 1
    assert len(client.tables["geo_external_signal_query_matches"]) == 1
    # metricsもbucket_at単位で重複せず、1件のまま値のみ更新される
    coverage_points = [m for m in client.tables["geo_external_signal_metrics"]
                        if m["metric_type"] == "coverage_volume"]
    assert len(coverage_points) == 1


def test_run_enabled_external_signal_queries_filters_by_provider(monkeypatch):
    """provider='gdelt' AND enabled=trueのQueryのみ対象にする（将来他providerへの誤配送防止）。
    gdelt-batchはrun_gdelt_ingestion直接呼び出しではなくrun_gdelt_doc_catchup（checkpoint
    ベースのcatch-up）を呼ぶ（13回目レビュー修正①）。"""
    _stub_no_config(monkeypatch)
    monkeypatch.setattr(geo_connector_gdelt, "run_gdelt_doc_catchup",
                         lambda client, *, query_row, **kw: {"query_id": query_row["query_id"],
                                                               "status": "completed"})

    client = FakeSupabaseClient(tables={"geo_external_signal_queries": [
        {"query_id": "q-1", "name": "A", "provider": "gdelt", "enabled": True},
        {"query_id": "q-2", "name": "B", "provider": "gdelt", "enabled": False},
        {"query_id": "q-3", "name": "C", "provider": "media_cloud", "enabled": True},
    ]})

    results = geo_connector_gdelt.run_enabled_external_signal_queries(client)

    assert [r["query_id"] for r in results] == ["q-1"]


# ── since/until対応 ──────────────────────────────────────────────────

def test_search_articles_uses_startdatetime_enddatetime_when_since_until_given(monkeypatch):
    captured = {}

    def fake_get(url, params, proxies, verify, timeout):
        if params.get("mode") == "artlist":
            captured["params"] = params
        return _mock_response(200, {"articles": []})

    monkeypatch.setattr(geo_connector_gdelt.requests, "get", fake_get)
    monkeypatch.setattr(geo_connector_gdelt.time, "sleep", lambda s: None)
    _stub_no_config(monkeypatch)

    client = FakeSupabaseClient()
    query_row = client.insert("geo_external_signal_queries", [{
        "name": "Taiwan Strait", "provider": "gdelt", "query": "Taiwan Strait",
        "article_lookback": "7d", "metric_lookback": "30d", "max_records": 250,
    }])[0]
    geo_connector_gdelt.run_gdelt_ingestion(
        client, query_row=query_row, since="20260301000000", until="20260302000000")

    assert captured["params"]["startdatetime"] == "20260301000000"
    assert captured["params"]["enddatetime"] == "20260302000000"
    assert "timespan" not in captured["params"]


def test_run_gdelt_ingestion_without_since_until_uses_article_lookback(monkeypatch):
    """既存挙動の非破壊確認: since/until未指定時はarticle_lookbackベースのtimespanが使われる。"""
    captured = {}

    def fake_get(url, params, proxies, verify, timeout):
        if params.get("mode") == "artlist":
            captured["params"] = params
        return _mock_response(200, {"articles": []})

    monkeypatch.setattr(geo_connector_gdelt.requests, "get", fake_get)
    monkeypatch.setattr(geo_connector_gdelt.time, "sleep", lambda s: None)
    _stub_no_config(monkeypatch)

    client = FakeSupabaseClient()
    query_row = client.insert("geo_external_signal_queries", [{
        "name": "Taiwan Strait", "provider": "gdelt", "query": "Taiwan Strait",
        "article_lookback": "7d", "metric_lookback": "30d", "max_records": 250,
    }])[0]
    geo_connector_gdelt.run_gdelt_ingestion(client, query_row=query_row)

    assert captured["params"]["timespan"] == "7d"
    assert "startdatetime" not in captured["params"]


def test_run_gdelt_ingestion_reports_article_result_count_not_persisted_to_db(monkeypatch):
    """article_result_countはgeo_ingestion_runsに実列を持たないため、戻り値にのみ含まれ
    DBのpatchには混入しないこと（12回目レビュー修正②の関連回帰テスト）。"""
    _stub_no_config(monkeypatch)

    def fake_get(url, params, proxies, verify, timeout):
        if params.get("mode") == "artlist":
            return _mock_response(200, {"articles": [{"url": "https://example.com/a", "title": "t",
                                                        "seendate": "20260901T081500Z", "domain": "example.com"}]})
        return _mock_response(200, {"timeline": []})

    monkeypatch.setattr(geo_connector_gdelt.requests, "get", fake_get)
    monkeypatch.setattr(geo_connector_gdelt.time, "sleep", lambda s: None)

    client = FakeSupabaseClient()
    query_row = client.insert("geo_external_signal_queries", [{
        "name": "Taiwan Strait", "provider": "gdelt", "query": "Taiwan Strait",
        "article_lookback": "7d", "metric_lookback": "30d", "max_records": 250,
    }])[0]
    result = geo_connector_gdelt.run_gdelt_ingestion(client, query_row=query_row)

    assert result["article_result_count"] == 1
    assert "article_result_count" not in client.tables["geo_ingestion_runs"][0]


# ── DOC window分割 ───────────────────────────────────────────────────

def test_fetch_window_recursive_splits_saturated_window(monkeypatch):
    """あるwindowでarticle_result_countがmaxrecordsに達する場合、中点で2分割して再取得
    すること（9回目レビュー修正②の直接回帰テスト）。"""
    calls = []

    def fake_run_gdelt_ingestion(client, *, query_row, since, until, requested_by, is_demo, fetch_metrics=True, run_type="manual"):
        calls.append((since, until))
        # 最初のwindow全体(4時間)はarticle_result_count=250(飽和)、それを分割した各半分(2時間)
        # は非飽和とする
        window_hours = geo_connector_gdelt._minutes_between(since, until) / 60
        count = 250 if window_hours >= 4 else 100
        return {"article_result_count": count, "ingestion_run_id": f"run-{len(calls)}"}

    monkeypatch.setattr(geo_connector_gdelt, "run_gdelt_ingestion", fake_run_gdelt_ingestion)
    query_row = {"query_id": "q-1", "query": "x", "max_records": 250}
    results = geo_connector_gdelt._fetch_window_recursive(
        None, query_row=query_row, since="20260301000000", until="20260301040000",
        min_window_minutes=15, requested_by="cli", is_demo=False)

    assert len(calls) == 3  # 元window + 分割後2件
    assert len(results) == 2
    assert all(not r["saturated_window"] for r in results)


def test_fetch_window_recursive_marks_saturated_at_min_window(monkeypatch):
    """min_window_minutesに達してもなお飽和する場合はsaturated_window=Trueとして返し、
    それ以上は分割しないこと。"""
    def fake_run_gdelt_ingestion(client, *, query_row, since, until, requested_by, is_demo, fetch_metrics=True, run_type="manual"):
        return {"article_result_count": 250, "ingestion_run_id": "run-x"}

    monkeypatch.setattr(geo_connector_gdelt, "run_gdelt_ingestion", fake_run_gdelt_ingestion)
    query_row = {"query_id": "q-1", "query": "x", "max_records": 250}
    results = geo_connector_gdelt._fetch_window_recursive(
        None, query_row=query_row, since="20260301000000", until="20260301001500",
        min_window_minutes=15, requested_by="cli", is_demo=False)

    assert len(results) == 1
    assert results[0]["saturated_window"] is True


def test_fetch_window_recursive_does_not_split_unsaturated_window(monkeypatch):
    calls = []

    def fake_run_gdelt_ingestion(client, *, query_row, since, until, requested_by, is_demo, fetch_metrics=True, run_type="manual"):
        calls.append((since, until))
        return {"article_result_count": 10, "ingestion_run_id": "run-x"}

    monkeypatch.setattr(geo_connector_gdelt, "run_gdelt_ingestion", fake_run_gdelt_ingestion)
    query_row = {"query_id": "q-1", "query": "x", "max_records": 250}
    results = geo_connector_gdelt._fetch_window_recursive(
        None, query_row=query_row, since="20260301000000", until="20260301040000",
        min_window_minutes=15, requested_by="cli", is_demo=False)

    assert len(calls) == 1  # 不要な追加リクエストを発生させない
    assert results[0]["saturated_window"] is False


def test_fetch_window_recursive_uses_article_result_count_not_fetched_count(monkeypatch):
    """API結果件数(article_result_count)とDB dedup後件数(fetched_count)が乖離していても、
    正しくarticle_result_countを見て飽和判定すること（12回目レビュー修正②の直接回帰テスト）。"""
    def fake_run_gdelt_ingestion(client, *, query_row, since, until, requested_by, is_demo, fetch_metrics=True, run_type="manual"):
        # APIは上限まで返したが、DB側は既存重複が多く実質のfetched_countは小さい
        return {"article_result_count": 250, "counts": {"fetched_count": 30},
                "ingestion_run_id": "run-x"}

    monkeypatch.setattr(geo_connector_gdelt, "run_gdelt_ingestion", fake_run_gdelt_ingestion)
    query_row = {"query_id": "q-1", "query": "x", "max_records": 250}
    results = geo_connector_gdelt._fetch_window_recursive(
        None, query_row=query_row, since="20260301000000", until="20260301001500",
        min_window_minutes=15, requested_by="cli", is_demo=False)

    assert results[0]["saturated_window"] is True  # fetched_count=30だけを見ていたら飽和を見逃す


# ── per-query completed_through checkpoint（Historical/Incremental共用） ──

def test_resolve_doc_incremental_since_uses_query_checkpoint_not_fixed_7d():
    """DOC connectorが10日停止しても、直近7日ではなくqueryのcompleted_through checkpoint
    (10日前)から再開すること（13回目レビュー修正①の直接回帰テスト）。"""
    query_row = {"query_id": "q-1", "provider": "gdelt", "article_lookback": "7d"}
    ten_days_ago = "20260301000000"
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [
        {"connector_type": "gdelt_doc_catchup", "status": "completed", "is_demo": False,
         "started_at": "2026-03-01T00:05:00+00:00",
         "request_parameters": {"query_id": "q-1", "completed_through": ten_days_ago,
                                 "checkpoint_scope": "incremental"}},
    ]})
    since = geo_connector_gdelt.resolve_doc_incremental_since(client, query_row=query_row, is_demo=False)
    assert since == ten_days_ago  # "直近7日"ではなく、10日前のcheckpointそのもの


def test_resolve_doc_incremental_since_falls_back_to_article_lookback_when_no_checkpoint():
    query_row = {"query_id": "q-new", "provider": "gdelt", "article_lookback": "7d"}
    client = FakeSupabaseClient()
    since = geo_connector_gdelt.resolve_doc_incremental_since(client, query_row=query_row, is_demo=False)
    from datetime import datetime, timedelta, timezone
    expected = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y%m%d")
    assert since.startswith(expected[:8])  # 日付部分が一致（秒単位までは要求しない）


def test_multiple_enabled_queries_have_independent_checkpoints():
    """複数enabled queryがそれぞれ独立したcheckpointを持つこと（13回目レビュー修正①の
    直接回帰テスト）。"""
    query_a = {"query_id": "q-a", "provider": "gdelt", "article_lookback": "7d"}
    query_b = {"query_id": "q-b", "provider": "gdelt", "article_lookback": "7d"}
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [
        {"connector_type": "gdelt_doc_catchup", "status": "completed", "is_demo": False,
         "started_at": "2026-03-01T00:00:00+00:00",
         "request_parameters": {"query_id": "q-a", "completed_through": "20260301000000",
                                 "checkpoint_scope": "incremental"}},
        {"connector_type": "gdelt_doc_catchup", "status": "completed", "is_demo": False,
         "started_at": "2026-06-01T00:00:00+00:00",
         "request_parameters": {"query_id": "q-b", "completed_through": "20260601000000",
                                 "checkpoint_scope": "incremental"}},
    ]})
    since_a = geo_connector_gdelt.resolve_doc_incremental_since(client, query_row=query_a, is_demo=False)
    since_b = geo_connector_gdelt.resolve_doc_incremental_since(client, query_row=query_b, is_demo=False)
    assert since_a == "20260301000000"
    assert since_b == "20260601000000"


def test_one_query_completion_does_not_advance_other_query_checkpoint(monkeypatch):
    """あるqueryのcatch-up完了が別queryのcheckpointに影響しないこと（13回目レビュー修正①の
    直接回帰テスト）。"""
    _stub_no_config(monkeypatch)

    def fake_fetch_window_recursive(client, *, query_row, since, until, min_window_minutes,
                                     requested_by, is_demo, run_type="manual"):
        return [{"since": since, "until": until, "article_result_count": 1,
                 "saturated_window": False, "ingestion_run_id": "run-x"}]
    monkeypatch.setattr(geo_connector_gdelt, "_fetch_window_recursive", fake_fetch_window_recursive)

    query_a = {"query_id": "q-a", "provider": "gdelt", "article_lookback": "7d"}
    query_b = {"query_id": "q-b", "provider": "gdelt", "article_lookback": "7d"}
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [
        {"connector_type": "gdelt_doc_catchup", "status": "completed", "is_demo": False,
         "started_at": "2026-03-01T00:00:00+00:00",
         "request_parameters": {"query_id": "q-a", "completed_through": "20260301000000",
                                 "checkpoint_scope": "incremental"}},
    ]})

    since_b_before = geo_connector_gdelt.resolve_doc_incremental_since(client, query_row=query_b, is_demo=False)
    geo_connector_gdelt.run_gdelt_doc_catchup(
        client, query_row=query_a, since=None, until="20260302000000", is_demo=False)
    since_b_after = geo_connector_gdelt.resolve_doc_incremental_since(client, query_row=query_b, is_demo=False)

    assert since_b_after == since_b_before  # query Aの完了はquery Bのcheckpointに影響しない
    since_a_after = geo_connector_gdelt.resolve_doc_incremental_since(client, query_row=query_a, is_demo=False)
    assert since_a_after == "20260302000000"  # query A自身のcheckpointは前進している


def test_promote_to_checkpoint_rejects_older_range_regression(monkeypatch):
    """既存checkpoint=9/8の状態で6/1〜7/1という古いrangeを後からpromoteしても、
    checkpointが7/1へ巻き戻らないこと（14回目レビュー修正の直接回帰テスト）。"""
    _stub_no_config(monkeypatch)
    query_id = "q-1"
    query_row = {"query_id": query_id, "provider": "gdelt", "article_lookback": "7d"}

    def fake_fetch_window_recursive(client, *, query_row, since, until, min_window_minutes,
                                     requested_by, is_demo, run_type="manual"):
        return [{"since": since, "until": until, "article_result_count": 1,
                 "saturated_window": False, "ingestion_run_id": "run-x"}]
    monkeypatch.setattr(geo_connector_gdelt, "_fetch_window_recursive", fake_fetch_window_recursive)

    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [
        {"connector_type": "gdelt_doc_catchup", "status": "completed", "is_demo": False,
         "started_at": "2026-09-08T00:00:00+00:00",
         "request_parameters": {"query_id": query_id, "completed_through": "20260908000000",
                                 "checkpoint_scope": "incremental"}},
    ]})
    result = geo_connector_gdelt.run_gdelt_doc_catchup(
        client, query_row=query_row, since="20260601000000", until="20260701000000",
        promote_to_checkpoint=True, is_demo=False)

    assert result["checkpoint_scope"] == "adhoc"
    assert result["promotion_rejected_reason"] == "would_regress_checkpoint"
    assert geo_connector_gdelt._find_last_doc_watermark(
        client, query_id=query_id, is_demo=False) == "20260908000000"  # 定常checkpointは巻き戻らない


def test_promote_to_checkpoint_noop_when_completed_through_equals_existing(monkeypatch):
    """completed_throughが既存watermarkと同値の場合はno-op（前進しないが拒否でもない）。"""
    _stub_no_config(monkeypatch)
    query_id = "q-1"
    query_row = {"query_id": query_id, "provider": "gdelt", "article_lookback": "7d"}

    def fake_fetch_window_recursive(client, *, query_row, since, until, min_window_minutes,
                                     requested_by, is_demo, run_type="manual"):
        return [{"since": since, "until": until, "article_result_count": 1,
                 "saturated_window": False, "ingestion_run_id": "run-x"}]
    monkeypatch.setattr(geo_connector_gdelt, "_fetch_window_recursive", fake_fetch_window_recursive)

    existing_watermark = "20260908000000"
    client = FakeSupabaseClient(tables={"geo_ingestion_runs": [
        {"connector_type": "gdelt_doc_catchup", "status": "completed", "is_demo": False,
         "started_at": "2026-09-08T00:00:00+00:00",
         "request_parameters": {"query_id": query_id, "completed_through": existing_watermark,
                                 "checkpoint_scope": "incremental"}},
    ]})
    result = geo_connector_gdelt.run_gdelt_doc_catchup(
        client, query_row=query_row, since="20260907000000", until=existing_watermark,
        window_hours=24, promote_to_checkpoint=True, is_demo=False)

    assert result["completed_through"] == existing_watermark
    assert result["checkpoint_scope"] == "adhoc"
    assert result["promotion_rejected_reason"] is None  # no-op: 拒否理由も無いが前進もしない
