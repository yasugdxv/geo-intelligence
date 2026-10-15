"""Keyword判定（部分一致の誤検知防止: BIS/PLA）と、公開HTML一覧Collector（統合幕僚監部）のテスト。"""
import types
from datetime import datetime, timedelta, timezone

import pytest

import geo_connector_base as base
import geo_connector_html_list as hl
import geo_direct_collection_common as common
import geo_monitoring_theme_service as mon

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


# ---- Keyword判定 ----
def art(title, summary="", agencies=None):
    return {"title": title, "summary": summary, "agencies": agencies}


def test_acronym_requires_word_boundary_not_substring():
    for bad in ("Suudi Arabistana yeni Patriot", "Ukrajinski minister odpovedal obisk", "Irani konfirmon bisedimet", "Premio Rodari vuole il bis",
                "Sanktionen gegen Irans Vereinigte Arabische Emirate", "business leaders meet", "ambitious plan", "a bistro opened"):
        assert mon.keyword_match_reason("BIS", art(bad)) is None, bad
    for good in ("BIS adds entities to the Entity List", "The BIS rule", "Commerce (BIS) announced", "BIS, the bureau", "export by BIS."):
        assert mon.keyword_match_reason("BIS", art(good)) == "word:BIS", good


def test_pla_substring_false_positives_are_gone():
    for bad in ("Iran Plans Stronger Response", "Nuclear Power Plant Operation", "Plastics Dialogue", "platební síť"):
        assert mon.keyword_match_reason("PLA", art(bad)) is None
    assert mon.keyword_match_reason("PLA", art("PLA aircraft crossed the median line")) == "word:PLA"
    assert mon.keyword_match_reason("PLA Navy", art("PLA Navy drills near Taiwan")) == "text:PLA Navy"      # 複数語は通常の語


def test_agency_keyword_matches_structured_agency_only():
    bis = art("URAL Airlines JSC; Order Renewing", agencies=["Commerce Department", "Industry and Security Bureau"])
    other = art("Notice of OFAC Sanctions Action", agencies=["Foreign Assets Control Office"])
    assert mon.keyword_match_reason("agency:Industry and Security Bureau", bis) == "agency:Industry and Security Bureau"
    assert mon.keyword_match_reason("agency:Industry and Security Bureau", other) is None
    assert mon.keyword_match_reason("agency:Industry and Security Bureau", art("BIS in text but no metadata")) is None   # 機関名は本文で判定しない


def test_federal_register_articles_ignore_acronym_text_matching():
    """機関メタデータのある記事（Federal Register）は、'BIS'の本文一致ではなくagencyで判定する。"""
    fr_text_only = art("Notice mentioning BIS in passing", agencies=["Treasury Department"])
    assert mon.keyword_match_reason("BIS", fr_text_only) is None
    assert mon.keyword_match_reason("export controls", art("New export controls", agencies=["Treasury Department"])) == "text:export controls"


def test_theme_hit_combines_kinds():
    theme = {"keywords": ["BIS", "agency:Industry and Security Bureau", "Bureau of Industry and Security", "semiconductor"]}
    assert mon._keyword_hit(theme, art("x", agencies=["Industry and Security Bureau"]))
    assert mon._keyword_hit(theme, art("Bureau of Industry and Security issues rule"))
    assert not mon._keyword_hit(theme, art("Arabistana business"))


def test_collect_filters_acronym_substring_rows_from_db_superset():
    from tests._fakes import FakeSupabaseClient
    rows = [{"external_signal_id": "tp", "provider": "gdelt", "title": "BIS adds entities", "summary": "", "source_id": None, "published_at": NOW.isoformat()},
            {"external_signal_id": "fp", "provider": "gdelt", "title": "Suudi Arabistana destek", "summary": "", "source_id": None, "published_at": NOW.isoformat()}]
    client = FakeSupabaseClient(tables={"geo_external_signals": rows, "geo_source_registry": [], "geo_source_set_members": [], "geo_source_domains": []})
    theme = {"monitoring_theme_id": "t", "source_set_id": None, "theme_candidate_id": None, "keywords": ["BIS"]}
    out = mon.collect_articles(client, theme, since=NOW - timedelta(days=7))
    # 疑似DBはilike(部分一致)を適用しないため両方返る＝DBはilikeで上位集合を返す想定。Python側の単語境界で確定する
    ids = {a["external_signal_id"] for a in out["articles"]}
    assert "tp" in ids and "fp" not in ids


# ---- HTML list Collector（統合幕僚監部）----
JS_HTML = """<ul>
<li><a href="/js/pdf/2026/p20260916_01.pdf" target="_blank" rel="noreferrer"><time datetime="2026-09-16">2026年09月16日</time> <span data-tag="公表">公表</span> <h5>中国海軍艦艇の動向について（ルーヤンⅢ、ジャンカイⅡ／奄美大島－横当島間南西進）</h5> </a></li>
<li><a href="/js/pdf/2026/p20260928_01.pdf"><time datetime="2026-09-28">2026年09月28日</time> <span data-tag="公表">公表</span> <h5>ロシア海軍艦艇の動向について（ステレグ／与那国島－西表島間北東進）</h5> </a></li>
<li><a href="/js/press/x.html"><time datetime="2026-10-02">2026年10月02日</time> <h5>千葉県における土砂災害に係る災害派遣について</h5> </a></li>
</ul>"""
SRC = {"source_id": "S-MOD", "name": "日本 防衛省（報道発表）", "url": "https://www.mod.go.jp/", "automation_allowed": True, "connector_enabled": True,
       "credential_configured": True}
CFG = {"html_list_sources": {"mod_joint_staff": {
    "registry_domain": "mod.go.jp", "endpoint_name": "統合幕僚監部 HTML list", "origin_section": "統合幕僚監部", "page_url": "https://www.mod.go.jp/js/",
    "base_url": "https://www.mod.go.jp", "parser": "mod_js_list", "language": "ja", "max_items": 100, "policy": {}}}}


def test_parse_mod_js_list():
    items = hl.parse_mod_js_list(JS_HTML, "https://www.mod.go.jp")
    assert len(items) == 3 and items[0]["title"].startswith("中国海軍艦艇の動向について")
    assert items[0]["url"] == "https://www.mod.go.jp/js/pdf/2026/p20260916_01.pdf" and items[0]["published_at"].date().isoformat() == "2026-09-16"
    assert items[2]["tag"] is None


def _patch(monkeypatch, store, html=JS_HTML):
    monkeypatch.setattr(common, "resolve_source", lambda c, d: SRC)
    monkeypatch.setattr(base, "create_ingestion_run", lambda c, **k: {"ingestion_run_id": "run1"})
    monkeypatch.setattr(base, "find_or_save_raw_snapshot", lambda c, **k: ({"raw_snapshot_id": "snap"}, True))
    monkeypatch.setattr(base, "link_run_to_snapshot", lambda c, **k: {})
    monkeypatch.setattr(common, "finish_run", lambda c, run, **k: store.setdefault("runs", []).append(k))
    monkeypatch.setattr(common, "record_source_outcome", lambda c, row, run, *, ok, is_demo=False: store.setdefault("outcomes", []).append(ok))

    def bulk(c, sigs, *, provider, is_demo=False):
        rows = store.setdefault("signals", {}); ins = upd = 0
        for n in sigs:
            if n["dedup_key"] in rows: upd += 1
            else: rows[n["dedup_key"]] = n; ins += 1
        return {"inserted": ins, "updated": upd, "signal_id_by_dedup_key": {}}
    monkeypatch.setattr(hl.signal_service, "bulk_upsert_signals", bulk)
    return lambda url: types.SimpleNamespace(content=html.encode("utf-8"), status_code=200, headers={"Content-Type": "text/html"})


def test_html_list_zero_items_is_failure_not_silent_success(monkeypatch):
    store = {}
    fetch = _patch(monkeypatch, store, html="<html>構造が変わった</html>")
    with pytest.raises(RuntimeError):
        hl.run_html_list_collection(None, source_key="mod_joint_staff", config=CFG, fetch=fetch)
    assert store["runs"][-1]["status"] == "failed" and store["outcomes"] == [False]


def test_html_list_gate_blocks_unapproved_source(monkeypatch):
    store = {}
    fetch = _patch(monkeypatch, store)
    monkeypatch.setattr(common, "resolve_source", lambda c, d: {**SRC, "automation_allowed": False})
    out = hl.run_all_html_list_sources(None, config=CFG)
    assert out["mod_joint_staff"]["status"] == "not_ready" and "signals" not in store
