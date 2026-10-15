"""GEO_DIRECT_COLLECTION_V1_1 回帰テスト: 略語の部分一致(BIS/PLA/IRGC/ADIZ)・Federal Register機関判定・統合幕僚監部・Wave 2 RSS・設定の整合性。"""
import types

import pytest

import geo_connector_base as base
import geo_connector_rss as rss
import geo_direct_collection_common as common
import geo_monitoring_theme_service as mon


def art(title, summary="", agencies=None):
    return {"title": title, "summary": summary, "agencies": agencies}


# ---- 略語の単語境界（substring false positiveの再発防止）----
@pytest.mark.parametrize("acr,bad,good", [
    ("BIS", ["Suudi Arabistana destek", "obisk v Berlinu", "bisedimet përmes Katarit", "business leaders", "ambitious plan", "bistro", "Premio Rodari vuole il bis"],
     ["BIS adds entities", "rule by the BIS.", "Commerce (BIS) said"]),
    ("PLA", ["Iran Plans Stronger Response", "Nuclear Power Plant", "Plastics Dialogue", "platební síť", "place names"], ["PLA aircraft near median line", "the PLA, said"]),
    ("IRGC", ["IRGCs", "xIRGC", "IRGCN-linked"], ["IRGC seized the tanker", "(IRGC)", "IRGC."]),
    ("ADIZ", ["ADIZs", "BADIZ"], ["PLA aircraft entered ADIZ", "Taiwan's ADIZ, again"]),
    ("TSMC", ["TSMCs plan"], ["TSMC reports", "from TSMC."]),
])
def test_uppercase_acronyms_never_match_inside_other_words(acr, bad, good):
    for t in bad:
        assert mon.keyword_match_reason(acr, art(t)) is None, (acr, t)
    for t in good:
        assert mon.keyword_match_reason(acr, art(t)) == f"word:{acr}", (acr, t)


def test_acronym_is_case_sensitive_so_lowercase_words_do_not_match():
    assert mon.keyword_match_reason("BIS", art("a bis repetita")) is None
    assert mon.keyword_match_reason("PLA", art("pla")) is None


def test_federal_register_bis_is_decided_by_agency_metadata_and_full_name():
    bis_doc = art("URAL Airlines JSC; Order Renewing", agencies=["Commerce Department", "Industry and Security Bureau"])
    other = art("Notice of OFAC Sanctions Action mentioning BIS", agencies=["Foreign Assets Control Office"])
    theme = {"keywords": ["BIS", "agency:Industry and Security Bureau", "Bureau of Industry and Security"]}
    assert mon._keyword_hit(theme, bis_doc)                      # 本文にBISが無くても機関で一致
    assert not mon._keyword_hit(theme, other)                    # 本文のBIS言及だけ（別機関）は一致しない
    assert mon.keyword_match_reason("Bureau of Industry and Security", art("The Bureau of Industry and Security issued a rule")) == "text:Bureau of Industry and Security"
    assert mon.keyword_match_reason("agency:Industry and Security Bureau", art("BIS in text")) is None


def test_pilot_theme_keywords_contain_no_unsafe_bare_acronyms():
    """Pilot Themeに入れる略語は、単語境界で判定される種類（大文字2〜4字）であり、'PLA'単独のような部分一致の温床を持たない。"""
    taiwan = ["Taiwan Strait", "台湾海峡", "人民解放軍", "ADIZ", "People's Liberation Army", "PLA Navy", "PLA Air Force", "PLA aircraft", "中国軍", "中国海軍艦艇"]
    assert "PLA" not in taiwan
    for kw in ("ADIZ", "BIS", "IRGC", "TSMC"):
        assert mon._kw_kind(kw) == "acronym"


# ---- 設定の整合性（Wave 2 / 統合幕僚監部 / UKMTO）----
def test_direct_config_integrity():
    cfg = common.load_direct_config()
    for key in ("mod_japan", "iaea", "wto", "cmf", "un_news_me", "eia", "ustr"):
        s = cfg["rss_sources"][key]
        assert s["registry_domain"] and s["feed_url"].startswith("https://") and s["language"]
        assert s["registry_domain"] in cfg["coverage_areas"], key                      # 鮮度ルールがある
    js = cfg["html_list_sources"]["mod_joint_staff"]
    assert js["registry_domain"] == cfg["rss_sources"]["mod_japan"]["registry_domain"] == "mod.go.jp"      # Canonical Source=防衛省（別Sourceにしない）
    assert js["origin_section"] == "統合幕僚監部" and js["parser"] in __import__("geo_connector_html_list").PARSERS
    uk = cfg["ukmto"]
    assert uk["registry_domain"] == "sccd.royalnavy.mod.uk" and uk["min_interval_minutes"] >= 30 and uk["endpoint_url"].startswith("https://sccd.royalnavy.mod.uk/api/ukmto/")
    hormuz = cfg["live_intelligence_areas"]["middle_east_maritime"]
    assert "sccd.royalnavy.mod.uk" in hormuz["direct"] and "sccd.royalnavy.mod.uk" in hormuz["categories"]["official_maritime_advisory"]["domains"]
    assert cfg["coverage_areas"]["sccd.royalnavy.mod.uk"]["freshness_basis"] == "collection"
    assert cfg["coverage_areas"]["news.un.org"]["role"] == "auxiliary" and cfg["coverage_areas"]["eia.gov"]["role"] == "auxiliary"   # 補助は鮮度判定に使わない
    # UKMTOはScheduler上も30分以上
    import geo_scheduler_service as svc
    job = svc.load_job_registry()["jobs"]["COLLECTION_UKMTO"]
    assert job["cadence"] == "every_30_minutes" and "COLLECTION_UKMTO" in svc._EXECUTORS


# ---- Wave 2 RSS（既存の汎用Collector・設定のみ）----
EIA_RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Crude oil prices and refinery margins generally increased in the third quarter</title><link>https://www.eia.gov/todayinenergy/detail.php?id=1</link><pubDate>Mon, 05 Oct 2026 14:00:00 GMT</pubDate></item>
<item><title>U.S. electricity use for electric vehicles increasing at a slower pace</title><link>https://www.eia.gov/todayinenergy/detail.php?id=2</link><pubDate>Wed, 30 Sep 2026 14:00:00 GMT</pubDate></item>
<item><title>Strait of Hormuz transit volumes of crude oil and LNG</title><link>https://www.eia.gov/todayinenergy/detail.php?id=3</link><pubDate>Fri, 02 Oct 2026 14:00:00 GMT</pubDate></item>
</channel></rss>"""


def _infra(monkeypatch, store, src, feed):
    monkeypatch.setattr(common, "resolve_source", lambda c, d: src)
    monkeypatch.setattr(common, "http_get", lambda url, **k: types.SimpleNamespace(content=feed, status_code=200, headers={"Content-Type": "application/rss+xml"}))
    monkeypatch.setattr(base, "create_ingestion_run", lambda c, **k: {"ingestion_run_id": "r"})
    monkeypatch.setattr(base, "find_or_save_raw_snapshot", lambda c, **k: ({"raw_snapshot_id": "s"}, True))
    monkeypatch.setattr(base, "link_run_to_snapshot", lambda c, **k: {})
    monkeypatch.setattr(common, "finish_run", lambda c, run, **k: None)
    monkeypatch.setattr(common, "record_source_outcome", lambda *a, **k: None)

    def bulk(c, sigs, *, provider, is_demo=False):
        rows = store.setdefault("signals", {}); ins = upd = 0
        for n in sigs:
            if n["dedup_key"] in rows: upd += 1
            else: rows[n["dedup_key"]] = n; ins += 1
        return {"inserted": ins, "updated": upd, "signal_id_by_dedup_key": {}}
    monkeypatch.setattr(rss.signal_service, "bulk_upsert_signals", bulk)


def test_eia_geo_policy_filters_non_energy_security_items_and_is_idempotent(monkeypatch):
    store = {}
    src = {"source_id": "S-EIA", "name": "EIA", "url": "https://www.eia.gov/", "automation_allowed": True, "connector_enabled": True, "credential_configured": True}
    _infra(monkeypatch, store, src, EIA_RSS)
    r1 = rss.run_rss_collection(None, source_key="eia")                    # 実際のconfig/geo_direct_sources.yamlを使う
    titles = sorted(s["title"][:20] for s in store["signals"].values())
    assert r1["inserted_count"] == 2 and r1["skipped_count"] == 1 and not any("electric vehicles" in s["title"] for s in store["signals"].values())
    assert all(s["source_id"] == "S-EIA" and s["provider"] == "rss" and s["raw_metadata"]["source_key"] == "eia" for s in store["signals"].values())
    r2 = rss.run_rss_collection(None, source_key="eia")
    assert (r2["inserted_count"], r2["updated_count"]) == (0, 2) and len(store["signals"]) == 2


@pytest.mark.parametrize("key,domain", [("cmf", "combinedmaritimeforces.com"), ("un_news_me", "news.un.org"), ("ustr", "ustr.gov")])
def test_wave2_rss_sources_collect_everything_with_provenance(monkeypatch, key, domain):
    store = {}
    feed = (b'<?xml version="1.0"?><rss version="2.0"><channel><item><title>A one</title><link>https://%b/a1</link><pubDate>Mon, 05 Oct 2026 10:00:00 GMT</pubDate></item>'
            b'<item><title>B two</title><link>https://%b/a2</link><pubDate>Sun, 04 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>') % (domain.encode(), domain.encode())
    src = {"source_id": f"S-{key}", "name": key, "url": f"https://{domain}/", "automation_allowed": True, "connector_enabled": True, "credential_configured": True}
    _infra(monkeypatch, store, src, feed)
    r = rss.run_rss_collection(None, source_key=key)
    assert r["inserted_count"] == 2 and r["skipped_count"] == 0
    assert all(s["source_id"] == f"S-{key}" and s["source_domain"] == domain and s["raw_metadata"]["collector"] == "rss" for s in store["signals"].values())
    assert rss.run_rss_collection(None, source_key=key)["inserted_count"] == 0
