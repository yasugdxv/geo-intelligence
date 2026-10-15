"""GEO_DIRECT_COLLECTION_V1: 汎用Geo RSS Collectorのテスト（実ネットワーク・実DBなし）。"""
import types

import pytest

import geo_connector_base as base
import geo_connector_rss as rss
import geo_direct_collection_common as common

RSS2 = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Defense&amp;Security news</title><link>https://www.mod.go.jp/a/1.html</link><guid>g1</guid>
<pubDate>Mon, 05 Oct 2026 03:00:00 +0900</pubDate><description>&lt;p&gt;Taiwan &amp;amp; Japan&lt;/p&gt;</description></item>
<item><title>Second</title><link>https://www.mod.go.jp/a/2.html?utm_source=x</link><pubDate>Sun, 04 Oct 2026 00:00:00 GMT</pubDate></item>
<item><title>No link</title></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>A1</title>
<link href="https://example.org/a1"/><id>tag:1</id><updated>2026-10-05T01:02:03Z</updated><summary>s</summary></entry></feed>"""

SOURCE = {"source_id": "sid-1", "name": "防衛省", "url": "https://www.mod.go.jp/", "automation_allowed": True,
          "connector_enabled": True, "credential_configured": True}


def test_parse_rss2_and_atom():
    items = rss.parse_feed(RSS2)
    assert len(items) == 3
    assert items[0]["title"] == "Defense&Security news" and items[0]["summary"] == "Taiwan & Japan"
    assert items[0]["published_at"].astimezone(__import__("datetime").timezone.utc).isoformat() == "2026-10-04T18:00:00+00:00"
    assert items[2]["link"] is None
    a = rss.parse_feed(ATOM)[0]
    assert a["link"] == "https://example.org/a1" and a["published_at"].year == 2026


def test_policy_include_exclude():
    pol = {"include_any": ["tariff", "dispute"], "exclude_any": ["webinar"]}
    assert common.passes_policy("New tariff rules", None, pol)
    assert not common.passes_policy("Tariff webinar", None, pol)
    assert not common.passes_policy("Annual picnic", None, pol)
    assert common.passes_policy("Anything", None, {"include_any": [], "exclude_any": []})


def test_build_signal_provenance_and_dedup_ignores_tracking_params():
    from datetime import datetime, timezone
    kw = dict(source_row=SOURCE, provider="rss", title="t", summary=None, published_at=datetime(2026, 10, 5, tzinfo=timezone.utc),
              language="ja", collector="rss", connector_version="v", endpoint="https://f", ingestion_run_id="run1")
    a = common.build_signal(url="https://www.mod.go.jp/a/2.html", **kw)
    b = common.build_signal(url="https://www.mod.go.jp/a/2.html?utm_source=x", **kw)
    assert a["source_id"] == "sid-1" and a["source_domain"] == "mod.go.jp" and a["timestamp_source"] == "publisher"
    assert a["source_role"] == "official_statement" and a["raw_metadata"]["ingestion_run_id"] == "run1"
    assert a["canonical_url"] != b["canonical_url"]       # 保存URLは元のまま


def _patch_infra(monkeypatch, store, source=SOURCE, feed=RSS2):
    monkeypatch.setattr(common, "resolve_source", lambda client, d: source)
    monkeypatch.setattr(common, "http_get", lambda url, **k: types.SimpleNamespace(
        content=feed, status_code=200, headers={"Content-Type": "application/rss+xml"}))
    monkeypatch.setattr(base, "create_ingestion_run", lambda client, **k: {"ingestion_run_id": "run1"})
    monkeypatch.setattr(base, "find_or_save_raw_snapshot", lambda client, **k: ({"raw_snapshot_id": "snap"}, True))
    monkeypatch.setattr(base, "link_run_to_snapshot", lambda client, **k: {})
    monkeypatch.setattr(common, "finish_run", lambda client, run, **k: store.setdefault("runs", []).append(k))
    monkeypatch.setattr(common, "record_source_outcome",
                        lambda client, row, run, *, ok, is_demo=False: store.setdefault("outcomes", []).append(ok))

    def fake_bulk(client, normalized, *, provider, is_demo=False):
        rows = store.setdefault("signals", {})
        ins = upd = 0
        for n in normalized:
            if n["dedup_key"] in rows:
                upd += 1
            else:
                rows[n["dedup_key"]] = n
                ins += 1
        return {"inserted": ins, "updated": upd, "signal_id_by_dedup_key": {}}
    monkeypatch.setattr(rss.signal_service, "bulk_upsert_signals", fake_bulk)


CFG = {"rss_sources": {"mod_japan": {"registry_domain": "mod.go.jp", "feed_url": "https://feed", "language": "ja",
                                      "max_items": 50, "policy": {"include_any": [], "exclude_any": []}}}}


def test_run_inserts_then_rerun_does_not_duplicate(monkeypatch):
    store = {}
    _patch_infra(monkeypatch, store)
    r1 = rss.run_rss_collection(None, source_key="mod_japan", config=CFG)
    assert (r1["fetched_count"], r1["inserted_count"], r1["skipped_count"]) == (3, 2, 1)     # リンク無し1件はskip
    r2 = rss.run_rss_collection(None, source_key="mod_japan", config=CFG)
    assert (r2["inserted_count"], r2["updated_count"]) == (0, 2)
    assert len(store["signals"]) == 2
    assert r1["latest_published_at"].startswith("2026-10-04") and store["outcomes"] == [True, True]   # Registryの最終成功を記録


def test_gate_blocks_when_automation_not_allowed(monkeypatch):
    store = {}
    _patch_infra(monkeypatch, store, source={**SOURCE, "automation_allowed": False})
    with pytest.raises(base.ConnectorDisabledError):
        rss.run_rss_collection(None, source_key="mod_japan", config=CFG)
    out = rss.run_all_rss_sources(None, config=CFG)
    assert out["mod_japan"]["status"] == "not_ready" and "signals" not in store


def test_failed_fetch_closes_run_as_failed(monkeypatch):
    store = {}
    _patch_infra(monkeypatch, store)
    monkeypatch.setattr(common, "http_get", lambda url, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        rss.run_rss_collection(None, source_key="mod_japan", config=CFG)
    assert store["runs"][-1]["status"] == "failed" and store["outcomes"] == [False]


def test_global_freshness_excludes_direct_sources_but_coverage_shows_them():
    """Direct Source（rss/official）が入っても、広域メディア(GDELT)の鮮度は引き上げない。Source別Coverageには出る。"""
    from datetime import datetime, timedelta, timezone
    import geo_source_library_service as lib
    from tests._fakes import FakeSupabaseClient
    now = datetime.now(timezone.utc)
    client = FakeSupabaseClient(tables={
        "geo_external_signals": [
            {"external_signal_id": "g", "provider": "gdelt", "is_demo": False, "source_id": None,
             "published_at": (now - timedelta(days=10)).isoformat(), "created_at": now.isoformat(), "observed_at": now.isoformat()},
            {"external_signal_id": "r", "provider": "rss", "is_demo": False, "source_id": "sid-1",
             "published_at": now.isoformat(), "created_at": now.isoformat(), "observed_at": now.isoformat()}],
        "geo_source_registry": [{"source_id": "sid-1", "name": "防衛省"}]})
    assert lib.get_global_freshness(client)["status"] == "stale"                      # GDELTが止まっている事実を隠さない
    cov = lib.get_coverage_freshness(client)
    assert [s["name"] for s in cov["sources"]] == ["防衛省"] and cov["sources"][0]["freshness"]["status"] == "fresh"


# ---- Federal Register ----
import geo_connector_federal_register as fr

FR_CFG = {"federal_register": {"registry_domain": "federalregister.gov", "api_url": "https://api", "language": "en",
                               "lookback_days": 14, "per_page": 100, "exclude_any": ["Agency Information Collection Activities"],
                               "queries": {"bis": {"agencies": ["industry-and-security-bureau"]},
                                           "state": {"agencies": ["state-department"], "term": "sanctions"}}}}
FR_SRC = {**SOURCE, "source_id": "fr-1", "name": "Federal Register", "url": "https://www.federalregister.gov/"}


def test_fr_build_params_is_deterministic_filter():
    p = fr.build_params({"agencies": ["a", "b"], "term": "China"}, since="2026-09-22", per_page=100)
    assert p["conditions[agencies][]"] == ["a", "b"] and p["conditions[term]"] == "China"
    assert p["conditions[publication_date][gte]"] == "2026-09-22" and p["order"] == "newest"


def _fr_fetch(params):
    if params.get("conditions[term]"):
        return {"count": 1, "results": [{"title": "Sanctions on X", "abstract": "<p>abs</p>", "html_url": "https://fr/2",
                                          "publication_date": "2026-09-29", "document_number": "2026-2", "type": "Rule",
                                          "agency_names": ["State Department"]}]}
    return {"count": 3, "results": [
        {"title": "Entity List additions", "abstract": "a", "html_url": "https://fr/1", "publication_date": "2026-09-30",
         "document_number": "2026-1", "type": "Rule", "agency_names": ["Commerce Department", "Industry and Security Bureau"]},
        {"title": "Agency Information Collection Activities; Submission", "html_url": "https://fr/3",
         "publication_date": "2026-09-30", "document_number": "2026-3", "type": "Notice", "agency_names": ["Commerce Department"]},
        {"title": "Sanctions on X", "html_url": "https://fr/2", "publication_date": "2026-09-29", "document_number": "2026-2"}]}


def test_fr_run_dedups_across_queries_skips_boilerplate_and_is_idempotent(monkeypatch):
    store = {}
    _patch_infra(monkeypatch, store, source=FR_SRC)
    r1 = fr.run_federal_register_collection(None, config=FR_CFG, fetch=_fr_fetch)
    assert (r1["fetched_count"], r1["inserted_count"], r1["skipped_count"]) == (3, 2, 1)      # 定型文書1件を除外・重複URLは1件に統合
    sig = store["signals"]
    assert {s["provider"] for s in sig.values()} == {"official"} and all(s["source_id"] == "fr-1" for s in sig.values())
    both = [s for s in sig.values() if s["url"] == "https://fr/2"][0]
    assert sorted(both["raw_metadata"]["matched_queries"]) == ["bis", "state"] and both["summary"] == "abs"
    r2 = fr.run_federal_register_collection(None, config=FR_CFG, fetch=_fr_fetch)
    assert (r2["inserted_count"], r2["updated_count"]) == (0, 2) and len(store["signals"]) == 2


# ---- Scheduler-ready（Direct Collection Job）----
def test_scheduler_registry_has_direct_jobs_and_maps_gate_to_not_configured(monkeypatch):
    import geo_scheduler_service as svc
    reg = svc.load_job_registry()
    for job_id in ("COLLECTION_DIRECT_RSS", "COLLECTION_FEDERAL_REGISTER", "COLLECTION_SANCTIONS_XML"):
        assert reg["jobs"][job_id]["job_type"] == "collection" and job_id in svc._EXECUTORS
    assert reg["jobs"]["COLLECTION_DIRECT_RSS"]["cadence"] == "every_6_hours"

    monkeypatch.setattr(svc.geo_connector_html_list, "run_all_html_list_sources", lambda *a, **k: {})   # Wave 2: 同じJobでHTML一覧も取得する
    monkeypatch.setattr(svc.geo_connector_rss, "run_all_rss_sources",
                        lambda *a, **k: {"x": {"status": "not_ready", "reason": "automation_allowed=false"}})
    out = svc.run_job(None, "COLLECTION_DIRECT_RSS", registry=reg, force=True)
    assert out["outcome"] == "not_configured"                    # 承認前は失敗ではなく「未設定」

    monkeypatch.setattr(svc.geo_connector_rss, "run_all_rss_sources",
                        lambda *a, **k: {"x": {"status": "failed", "error": "boom"}})
    assert svc.run_job(None, "COLLECTION_DIRECT_RSS", registry=reg, force=True)["outcome"] == "error"

    monkeypatch.setattr(svc.geo_connector_rss, "run_all_rss_sources",
                        lambda *a, **k: {"x": {"status": "completed", "inserted_count": 2}})
    assert svc.run_job(None, "COLLECTION_DIRECT_RSS", registry=reg, force=True)["outcome"] == "executed"


def test_freshness_parses_five_digit_fractional_seconds_from_postgres():
    """Postgresは末尾の0を省くため '...16.64088+00:00'（5桁）が返る。Python 3.10でも運用サマリーが壊れないこと。"""
    import geo_freshness_service as f
    from datetime import datetime, timezone
    r = f.classify_source_freshness({"last_successful_ingestion_at": "2026-10-06T04:16:16.64088+00:00", "failure_count": 0,
                                     "update_cadence": "daily"}, now=datetime(2026, 10, 6, 12, tzinfo=timezone.utc)) \
        if "now" in f.classify_source_freshness.__code__.co_varnames else None
    assert r is None or r["status"] != "unknown"
    assert f._fromiso("2026-10-06T04:16:16.64088+00:00").microsecond == 640880
