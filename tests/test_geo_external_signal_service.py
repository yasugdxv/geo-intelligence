"""地政学ナレッジ＆インテリジェンスDB Phase1: geo_external_signal_service.pyのテスト。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_external_signal_service as svc  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


# ── compute_dedup_key: 優先順位 ─────────────────────────────────────────

def test_compute_dedup_key_prefers_canonical_url():
    key_a = svc.compute_dedup_key(canonical_url="https://example.com/a", normalized_url="https://other.com/a")
    key_b = svc.compute_dedup_key(canonical_url="https://example.com/a", normalized_url="https://different.com/z")
    assert key_a == key_b  # normalized_urlが違ってもcanonical_urlが同じなら同一キー


def test_compute_dedup_key_falls_back_to_normalized_url():
    key = svc.compute_dedup_key(normalized_url="https://example.com/a")
    assert key == svc.compute_dedup_key(normalized_url="https://example.com/a")
    assert key != svc.compute_dedup_key(normalized_url="https://example.com/b")


def test_compute_dedup_key_falls_back_to_domain_and_title():
    key = svc.compute_dedup_key(source_domain="example.com", normalized_title="taiwan strait tensions rise")
    assert key == svc.compute_dedup_key(source_domain="Example.com", normalized_title="taiwan strait tensions rise")


def test_compute_dedup_key_raises_when_nothing_available():
    try:
        svc.compute_dedup_key()
        assert False, "ValueErrorが送出されるはず"
    except ValueError:
        pass


# ── normalize_url ────────────────────────────────────────────────────────

def test_normalize_url_strips_tracking_params_and_trailing_slash():
    a = svc.normalize_url("https://Example.com/Article/?utm_source=twitter&id=1")
    b = svc.normalize_url("https://example.com/Article?id=1")
    assert a == b


# ── parse_gdelt_timeline_date ───────────────────────────────────────────

def test_parse_gdelt_timeline_date_handles_t_z_format():
    result = svc.parse_gdelt_timeline_date("20260901T081500Z")
    assert result == "2026-09-01T08:15:00+00:00"


def test_parse_gdelt_timeline_date_returns_none_for_unparseable():
    assert svc.parse_gdelt_timeline_date("not-a-date") is None
    assert svc.parse_gdelt_timeline_date(None) is None


# ── normalize_article ────────────────────────────────────────────────────

def test_normalize_article_sets_gdelt_seen_timestamp_source():
    raw = {
        "url": "https://example.com/article1", "title": "Taiwan Strait tensions",
        "seendate": "20260901T081500Z", "domain": "example.com",
        "language": "English", "sourcecountry": "Taiwan",
    }
    normalized = svc.normalize_article(raw, provider="gdelt")
    assert normalized["timestamp_source"] == "gdelt_seen"
    assert normalized["published_at"] == "2026-09-01T08:15:00+00:00"
    assert normalized["source_role"] == "discovery"
    assert normalized["evidence_status"] == "unverified"
    assert normalized["validation_status"] == "unreviewed"
    assert normalized["knowledge_promotion_status"] == "not_promoted"
    assert normalized["countries"] == ["Taiwan"]
    assert normalized["provider_record_id"] is None


# ── upsert_signal: (provider, dedup_key)ベース3分岐 ─────────────────────

def test_upsert_signal_inserts_new_then_updates_existing():
    client = FakeSupabaseClient()
    raw = {"url": "https://example.com/a", "title": "t1", "seendate": "20260901T080000Z",
           "domain": "example.com", "language": "English", "sourcecountry": "Taiwan"}
    normalized = svc.normalize_article(raw, provider="gdelt")

    row1, action1 = svc.upsert_signal(client, normalized, provider="gdelt")
    assert action1 == "inserted"
    assert len(client.tables["geo_external_signals"]) == 1

    row2, action2 = svc.upsert_signal(client, normalized, provider="gdelt")
    assert action2 == "updated"
    assert len(client.tables["geo_external_signals"]) == 1
    assert row2["external_signal_id"] == row1["external_signal_id"]


def test_upsert_signal_same_article_different_provider_creates_separate_row():
    """同一記事を将来Media Cloud等が別途観測した場合、providerが異なれば別行として残る。"""
    client = FakeSupabaseClient()
    raw = {"url": "https://example.com/a", "title": "t1", "seendate": "20260901T080000Z",
           "domain": "example.com", "language": "English", "sourcecountry": "Taiwan"}
    normalized_gdelt = svc.normalize_article(raw, provider="gdelt")
    normalized_mc = svc.normalize_article(raw, provider="media_cloud")

    svc.upsert_signal(client, normalized_gdelt, provider="gdelt")
    svc.upsert_signal(client, normalized_mc, provider="media_cloud")

    assert len(client.tables["geo_external_signals"]) == 2


# ── upsert_query_match: N:M ─────────────────────────────────────────────

def test_upsert_query_match_same_signal_multiple_queries():
    client = FakeSupabaseClient()
    svc.upsert_query_match(client, external_signal_id="sig-1", query_id="q-taiwan", ingestion_run_id="run-1")
    svc.upsert_query_match(client, external_signal_id="sig-1", query_id="q-semiconductor", ingestion_run_id="run-1")

    matches = client.tables["geo_external_signal_query_matches"]
    assert len(matches) == 2
    assert {m["query_id"] for m in matches} == {"q-taiwan", "q-semiconductor"}


def test_upsert_query_match_rematch_updates_last_matched_at():
    client = FakeSupabaseClient()
    svc.upsert_query_match(client, external_signal_id="sig-1", query_id="q-1", ingestion_run_id="run-1")
    action = svc.upsert_query_match(client, external_signal_id="sig-1", query_id="q-1", ingestion_run_id="run-2")

    assert action == "rematched"
    assert len(client.tables["geo_external_signal_query_matches"]) == 1
    assert client.tables["geo_external_signal_query_matches"][0]["ingestion_run_id"] == "run-2"


# ── upsert_metric: 冪等更新 ──────────────────────────────────────────────

def test_upsert_metric_same_key_updates_value_not_duplicates():
    client = FakeSupabaseClient()
    svc.upsert_metric(client, provider="gdelt", query_id="q-1", metric_type="coverage_volume",
                       series_key="default", bucket_at="2026-09-01T08:00:00+00:00", value=5)
    action = svc.upsert_metric(client, provider="gdelt", query_id="q-1", metric_type="coverage_volume",
                                series_key="default", bucket_at="2026-09-01T08:00:00+00:00", value=9)

    assert action == "updated"
    rows = client.tables["geo_external_signal_metrics"]
    assert len(rows) == 1
    assert rows[0]["value"] == 9


# ── summarize_signals / extract_topics ──────────────────────────────────

def test_extract_topics_uses_coverage_volume_metric_not_article_count():
    """【重要】Trending TopicsはArticleList保存件数ではなくcoverage_volume metricで算出する。
    さらにchange_pctはraw valueの単純比較ではなく、GDELTのnorm（その時間帯にGDELTが監視した
    全記事数）で正規化したcoverage share（value/norm）のcurrent/previous比較でなければならない
    （GDELT全体の監視記事総量は日によって変動するため、raw値だけの比較は「Topicの相対的な
    注目度」と「その日の全体流量」を区別できない）。
    このテストでは意図的にraw valueの伸び率(100/40=+150%)とnormalized shareの伸び率が
    異なるケースを作り、change_pctが後者（share基準）で計算されることを検証する:
      current:  value=100, norm=200  → share=0.50
      previous: value=40,  norm=400  → share=0.10
      share変化率 = (0.50-0.10)/0.10 = +400%（rawの+150%とは異なる値になるはず）"""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(days=1)).isoformat()
    older = (now - timedelta(days=10)).isoformat()

    client = FakeSupabaseClient(tables={
        "geo_external_signal_queries": [{"query_id": "q-1", "name": "Taiwan Strait"}],
        "geo_external_signal_metrics": [
            {"query_id": "q-1", "metric_type": "coverage_volume", "value": 100, "norm_value": 200, "bucket_at": recent},
            {"query_id": "q-1", "metric_type": "coverage_volume", "value": 40, "norm_value": 400, "bucket_at": older},
        ],
    })

    topics = svc.extract_topics(client, since_days=7)

    assert len(topics) == 1
    # 表示用のraw件数はそのまま残す
    assert topics[0]["current_count"] == 100
    assert topics[0]["previous_count"] == 40
    # 正規化されたcoverage share
    assert topics[0]["current_coverage_share"] == 0.5
    assert topics[0]["previous_coverage_share"] == 0.1
    # change_pctはshareベース(+400%)であり、rawベースの+150%ではない
    assert topics[0]["change_pct"] == 400.0


# ── bulk_* : 大量記事/Metricでも少ないHTTP往復で処理する ────────────────

def test_bulk_upsert_signals_inserts_new_and_updates_existing_in_one_pass():
    client = FakeSupabaseClient()
    articles = [
        {"url": f"https://example.com/{i}", "title": f"t{i}", "seendate": "20260901T080000Z",
         "domain": "example.com", "language": "English", "sourcecountry": "Taiwan"}
        for i in range(5)
    ]
    normalized = [svc.normalize_article(a, provider="gdelt") for a in articles]

    result1 = svc.bulk_upsert_signals(client, normalized, provider="gdelt")
    assert result1["inserted"] == 5
    assert result1["updated"] == 0
    assert len(client.tables["geo_external_signals"]) == 5

    result2 = svc.bulk_upsert_signals(client, normalized, provider="gdelt")
    assert result2["inserted"] == 0
    assert result2["updated"] == 5
    assert len(client.tables["geo_external_signals"]) == 5
    # signal_id_by_dedup_keyは1回目・2回目で同じexternal_signal_idを指す
    assert result1["signal_id_by_dedup_key"] == result2["signal_id_by_dedup_key"]


def test_bulk_upsert_signals_chunks_large_in_filter():
    """250件規模のdedup_keyを1回のin.()フィルタに入れるとURLが長大化するため、
    _IN_FILTER_CHUNK_SIZE件ずつ分割してSELECTすること（分割しても結果が欠落しないこと）を確認する。"""
    articles = [
        {"url": f"https://example.com/{i}", "title": f"t{i}", "seendate": "20260901T080000Z",
         "domain": "example.com", "language": "English", "sourcecountry": "Taiwan"}
        for i in range(250)
    ]
    normalized = [svc.normalize_article(a, provider="gdelt") for a in articles]
    client = FakeSupabaseClient()

    svc.bulk_upsert_signals(client, normalized, provider="gdelt")
    result2 = svc.bulk_upsert_signals(client, normalized, provider="gdelt")

    assert result2["inserted"] == 0
    assert result2["updated"] == 250
    assert len(client.tables["geo_external_signals"]) == 250


def test_bulk_upsert_query_matches_handles_multiple_signals():
    client = FakeSupabaseClient()
    result1 = svc.bulk_upsert_query_matches(
        client, external_signal_ids=["s1", "s2", "s3"], query_id="q-1", ingestion_run_id="run-1")
    assert result1 == {"matched": 3, "rematched": 0}

    result2 = svc.bulk_upsert_query_matches(
        client, external_signal_ids=["s1", "s2", "s3"], query_id="q-1", ingestion_run_id="run-2")
    assert result2 == {"matched": 0, "rematched": 3}
    assert len(client.tables["geo_external_signal_query_matches"]) == 3


def test_bulk_upsert_metrics_inserts_new_points_and_skips_existing():
    client = FakeSupabaseClient()
    points = [{"series_key": "default", "bucket_at": f"2026-09-0{i}T00:00:00+00:00", "value": i,
               "norm_value": None, "raw_metadata": {}, "ingestion_run_id": "run-1"} for i in range(1, 4)]

    result1 = svc.bulk_upsert_metrics(client, provider="gdelt", query_id="q-1",
                                       metric_type="coverage_volume", points=points)
    assert result1 == {"inserted": 3, "skipped_existing": 0}

    result2 = svc.bulk_upsert_metrics(client, provider="gdelt", query_id="q-1",
                                       metric_type="coverage_volume", points=points)
    assert result2 == {"inserted": 0, "skipped_existing": 3}
    assert len(client.tables["geo_external_signal_metrics"]) == 3


def test_bulk_upsert_metrics_chunks_large_insert_payload(monkeypatch):
    """1回のfetchで数千点になりうる（実測: source_country_coverageで7日分約6,240点）ため、
    _METRIC_INSERT_CHUNK_SIZE件ずつに分割してINSERTすること、かつ全件が保存されることを確認する。"""
    monkeypatch.setattr(svc, "_METRIC_INSERT_CHUNK_SIZE", 10)
    client = FakeSupabaseClient()
    points = [{"series_key": "default", "bucket_at": f"2026-09-01T{i:02d}:00:00+00:00", "value": i,
               "norm_value": None, "raw_metadata": {}, "ingestion_run_id": "run-1"} for i in range(23)]

    result = svc.bulk_upsert_metrics(client, provider="gdelt", query_id="q-1",
                                      metric_type="coverage_volume", points=points)

    assert result["inserted"] == 23
    assert len(client.tables["geo_external_signal_metrics"]) == 23


def test_summarize_signals_counts_from_stored_signals_not_gdelt():
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(hours=1)).isoformat()
    old = (now - timedelta(days=30)).isoformat()

    client = FakeSupabaseClient(tables={"geo_external_signals": [
        {"external_signal_id": "s1", "published_at": recent, "source_domain": "a.com", "source_country": "Taiwan"},
        {"external_signal_id": "s2", "published_at": old, "source_domain": "b.com", "source_country": "Japan"},
    ]})

    summary = svc.summarize_signals(client)

    assert summary["signals_24h"] == 1
    assert summary["signals_7d"] == 1
    assert summary["sources"] == 2
    assert summary["countries"] == 2
