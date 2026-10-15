"""地政学ナレッジ＆インテリジェンスDB Phase2A: geo_external_event_service.pyのテスト。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_external_event_service as svc  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def test_match_signals_for_urls_exact_then_normalized_fallback():
    client = FakeSupabaseClient(tables={"geo_external_signals": [
        {"external_signal_id": "s1", "url": "https://example.com/a", "canonical_url": None,
         "normalized_url": "https://example.com/a"},
        {"external_signal_id": "s2", "url": "https://example.com/b?utm_source=x", "canonical_url": None,
         "normalized_url": "https://example.com/b"},
    ]})
    matches = svc.match_signals_for_urls(client, ["https://example.com/a", "https://example.com/b?utm_source=y"])
    assert matches["https://example.com/a"]["external_signal_id"] == "s1"
    assert matches["https://example.com/a"]["link_method"] == "gdelt_mention_exact_url"
    assert matches["https://example.com/b?utm_source=y"]["external_signal_id"] == "s2"
    assert matches["https://example.com/b?utm_source=y"]["link_method"] == "gdelt_mention_normalized_url"


def test_match_signals_for_urls_no_match_returns_empty():
    client = FakeSupabaseClient(tables={"geo_external_signals": []})
    matches = svc.match_signals_for_urls(client, ["https://unknown.com/x"])
    assert matches == {}


def test_bulk_upsert_events_idempotent():
    client = FakeSupabaseClient()
    normalized = [{"provider": "gdelt", "provider_event_id": "111", "num_mentions": 2, "avg_tone": -1.0,
                   "source_role": "event_data", "evidence_status": "unverified",
                   "batch_timestamp": "20260907060000", "ingestion_run_id": "run-1"}]

    result1 = svc.bulk_upsert_events(client, normalized)
    assert result1["inserted"] == 1
    assert result1["updated"] == 0
    assert len(client.tables["geo_external_events"]) == 1

    normalized[0]["num_mentions"] = 5  # 再取得で値が変わったケース(スナップショット更新)
    result2 = svc.bulk_upsert_events(client, normalized)
    assert result2["inserted"] == 0
    assert result2["updated"] == 1
    assert len(client.tables["geo_external_events"]) == 1
    assert client.tables["geo_external_events"][0]["num_mentions"] == 5


def test_bulk_upsert_event_signal_links_picks_highest_confidence_mention():
    """同一(event,signal)ペアに複数Mentionがある場合confidence最大を採用し、
    同点ならmention_time_date早い方を採用する（3回目レビュー推奨②の回帰テスト）。"""
    client = FakeSupabaseClient()
    links = [
        {"external_event_id": "e1", "external_signal_id": "s1", "link_method": "gdelt_mention_exact_url",
         "mention_time_date": "2026-09-07T06:00:00+00:00", "confidence": 40, "mention_doc_tone": 1.0},
        {"external_event_id": "e1", "external_signal_id": "s1", "link_method": "gdelt_mention_exact_url",
         "mention_time_date": "2026-09-07T06:15:00+00:00", "confidence": 80, "mention_doc_tone": 2.0},
        {"external_event_id": "e1", "external_signal_id": "s1", "link_method": "gdelt_mention_exact_url",
         "mention_time_date": "2026-09-07T06:30:00+00:00", "confidence": 80, "mention_doc_tone": 3.0},
    ]
    result = svc.bulk_upsert_event_signal_links(client, links)
    assert result["inserted"] == 1
    rows = client.tables["geo_external_event_signal_links"]
    assert len(rows) == 1
    # confidence=80が2件同点 -> mention_time_dateが早い方(06:15)が採用される
    assert rows[0]["confidence"] == 80
    assert rows[0]["mention_time_date"] == "2026-09-07T06:15:00+00:00"


def test_bulk_upsert_event_signal_links_does_not_overwrite_existing():
    client = FakeSupabaseClient(tables={"geo_external_event_signal_links": [
        {"link_id": "l1", "external_event_id": "e1", "external_signal_id": "s1", "confidence": 10},
    ]})
    result = svc.bulk_upsert_event_signal_links(client, [
        {"external_event_id": "e1", "external_signal_id": "s1", "link_method": "gdelt_mention_exact_url",
         "mention_time_date": None, "confidence": 99, "mention_doc_tone": None},
    ])
    assert result["inserted"] == 0
    assert client.tables["geo_external_event_signal_links"][0]["confidence"] == 10  # 上書きしない


def test_bulk_upsert_annotations_dedup_by_provider_record_id():
    client = FakeSupabaseClient()
    normalized = [{"provider": "gdelt", "provider_record_id": "rec-1", "external_signal_id": "s1",
                   "themes": [], "persons": [], "organizations": [], "locations": [],
                   "batch_timestamp": "20260907060000", "ingestion_run_id": "run-1"}]
    result1 = svc.bulk_upsert_annotations(client, normalized)
    assert result1["inserted"] == 1
    result2 = svc.bulk_upsert_annotations(client, normalized)
    assert result2["inserted"] == 0
    assert len(client.tables["geo_external_signal_annotations"]) == 1


def test_get_related_event_ids_for_signal():
    client = FakeSupabaseClient(tables={"geo_external_event_signal_links": [
        {"external_event_id": "e1", "external_signal_id": "s1"},
        {"external_event_id": "e2", "external_signal_id": "s1"},
        {"external_event_id": "e3", "external_signal_id": "s2"},
    ]})
    result = svc.get_related_event_ids_for_signal(client, "s1")
    assert set(result) == {"e1", "e2"}


def test_search_recent_events_for_theme_uses_nm_path():
    client = FakeSupabaseClient(tables={
        "geo_external_signal_queries": [{"query_id": "q1", "theme_id": "theme-taiwan"}],
        "geo_external_signal_query_matches": [{"query_id": "q1", "external_signal_id": "s1"}],
        "geo_external_event_signal_links": [{"query_id": "q1", "external_event_id": "e1", "external_signal_id": "s1"}],
        "geo_external_events": [{"external_event_id": "e1", "event_date": "2026-09-01"},
                                 {"external_event_id": "e2", "event_date": "2026-09-02"}],
    })
    result = svc.search_recent_events_for_theme(client, theme_id="theme-taiwan", limit=10)
    assert [r["external_event_id"] for r in result] == ["e1"]


def test_search_recent_events_for_theme_no_theme_returns_all():
    client = FakeSupabaseClient(tables={"geo_external_events": [
        {"external_event_id": "e1", "event_date": "2026-09-01"},
    ]})
    result = svc.search_recent_events_for_theme(client, limit=10)
    assert len(result) == 1
