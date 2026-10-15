import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_official_crawler as crawler  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

THEME_ID = "theme-1"


def test_ensure_source_is_idempotent():
    client = FakeSupabaseClient()
    source = {"name": "テストソース", "url": "https://example.com/a",
              "source_type": "official_site", "source_tier": "一次"}

    first = crawler.ensure_source(client, THEME_ID, source)
    second = crawler.ensure_source(client, THEME_ID, source)

    assert first["source_id"] == second["source_id"]
    assert len(client.tables["geo_source_registry"]) == 1


def test_crawl_source_creates_events_and_observation(monkeypatch):
    client = FakeSupabaseClient(tables={
        "geo_indicator_rule_versions": [{
            "rule_version_id": "rule-1", "indicator_id": "ind-1", "version": 1,
            "rule_definition": {"method": "event_count_window", "window_days": 30},
        }],
    })
    indicator = {"indicator_id": "ind-1", "indicator_code": "MIL-01", "theme_id": THEME_ID}
    indicator_by_code = {"MIL-01": indicator}
    indicator_choices = [{"code": "MIL-01", "name": "テスト指標"}]

    fake_document = {"document_id": "doc-1", "theme_id": THEME_ID, "extracted_text": "テスト本文", "is_mock": False}
    monkeypatch.setattr(crawler.store, "fetch_and_save_document",
                         lambda *a, **kw: {"document": fake_document, "is_new": True})
    monkeypatch.setattr(crawler.extractor, "extract_events",
                         lambda *a, **kw: [{
                             "actor": "PLA", "action": "越境", "indicator_code": "MIL-01",
                             "claim_text": "PLA機が越境した", "quote": "quote", "confidence": 0.9,
                         }])

    source = {
        "name": "テストソース", "url": "https://example.com/a",
        "source_type": "official_site", "source_tier": "一次",
        "jobs": [{"method": "event_count_window", "indicator_code": "MIL-01"}],
    }

    results = crawler.crawl_source(
        client, MagicMock(), "gpt-4o", THEME_ID, "taiwan_strait",
        indicator_choices, indicator_by_code, source, {}, True,
    )

    assert len(client.tables.get("geo_events", [])) == 1
    assert client.tables["geo_events"][0]["indicator_code"] == "MIL-01"
    assert len(client.tables.get("geo_evidence", [])) == 1
    assert results[-1]["ok"] is True
    assert results[-1]["raw_value"] == 1.0


def test_crawl_source_reports_fetch_error(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(crawler.store, "fetch_and_save_document",
                         lambda *a, **kw: {"error": "ConnectionError: timed out", "http_status": None})

    source = {"name": "テストソース", "url": "https://example.com/a",
              "source_type": "official_site", "source_tier": "一次", "jobs": []}

    results = crawler.crawl_source(
        client, MagicMock(), "gpt-4o", THEME_ID, "taiwan_strait", [], {}, source, {}, True,
    )

    assert results == [{"source": "テストソース", "ok": False, "error": "ConnectionError: timed out"}]
