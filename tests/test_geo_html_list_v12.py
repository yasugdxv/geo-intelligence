"""HTML list Collector（統合幕僚監部 / Maersk）V1_2: Canonical Source・Provenance・Baseline・更新検知・Schema Guard・詳細要約・dedup。
併せて、複合条件keyword（tanker AND geography）と海域分類のテスト。"""
import types
from datetime import datetime, timezone

import pytest

import geo_connector_base as base
import geo_connector_html_list as hl
import geo_direct_collection_common as common
import geo_monitoring_theme_service as mon

JS_HTML = """<ul>
<li><a href="/js/pdf/2026/p20260916_01.pdf"><time datetime="2026-09-16">2026年09月16日</time> <span data-tag="公表">公表</span> <h5>中国海軍艦艇の動向について（ルーヤンⅢ、ジャンカイⅡ／奄美大島－横当島間南西進）</h5> </a></li>
<li><a href="/js/pdf/2026/p20260928_01.pdf"><time datetime="2026-09-28">2026年09月28日</time> <span data-tag="公表">公表</span> <h5>ロシア海軍艦艇の動向について（ステレグ／与那国島－西表島間北東進）</h5> </a></li>
</ul>"""


def maersk_html(items):
    body = "".join(
        f'<div class="p-section__news__teaser__content"><a href="/news/category/advisories" class="p-section__news__teaser__category">Advisories</a>'
        f'<a href="{u}" class="font--display-1 p-section__news__teaser__title p-section__news__teaser__content__child"><span class="p-section__news__teaser__title__line"> {t} </span></a>'
        f'<div class="p-section__news__teaser__timestamp p-section__news__teaser__content__child"> {d} </div></div>' for u, t, d in items)
    return f"<html><body><section>{body}</section></body></html>"


MAERSK_ITEMS = [("/news/articles/2026/09/29/middle-east-operational-update-50", "Middle East Operational Update 50", "2 Oct 2026"),
                ("/news/articles/2026/10/06/australia-no-shipping-instruction-no-load", "No Shipping Instruction, No Load Policy for Cargo Exporting from Australia", "6 Oct 2026"),
                ("/news/articles/2026/03/11/maersk-operations-through-strait-of-hormuz", "Maersk Operations through Strait of Hormuz", "29 Sep 2026")]
DETAIL = '<html><head><meta name="description" content="Maersk operational update on Middle East shipping disruptions due to the Strait of Hormuz crisis."></head></html>'

SRC_MOD = {"source_id": "S-MOD", "name": "日本 防衛省（報道発表）", "url": "https://www.mod.go.jp/", "automation_allowed": True, "connector_enabled": True, "credential_configured": True}
SRC_MAERSK = {"source_id": "S-MAERSK", "name": "Maersk 運航勧告", "url": "https://www.maersk.com/news/category/advisories", "automation_allowed": True,
              "connector_enabled": True, "credential_configured": True}
CFG = {"html_list_sources": {
    "mod_joint_staff": {"registry_domain": "mod.go.jp", "endpoint_name": "統合幕僚監部 HTML list", "origin_section": "統合幕僚監部", "page_url": "https://www.mod.go.jp/js/",
                        "base_url": "https://www.mod.go.jp", "parser": "mod_js_list", "language": "ja", "max_items": 100, "policy": {}},
    "maersk_advisories": {"registry_domain": "maersk.com", "endpoint_name": "Maersk Advisories HTML list", "origin_section": "Maersk News & Advisories",
                          "page_url": "https://www.maersk.com/news/category/advisories", "base_url": "https://www.maersk.com", "parser": "maersk_news_list",
                          "language": "en", "max_items": 60, "source_role": "primary_evidence", "baseline_on_first_run": True,
                          "detail": {"enabled": True, "max_per_run": 12, "delay_seconds": 0},
                          "policy": {"include_any": ["middle east", "red sea", "hormuz"], "exclude_any": []}}}}


class MemClient:
    def __init__(self):
        self.signals, self._n = [], 0

    def select(self, table, params):
        rows = list(self.signals)
        for k, v in params.items():
            if k in ("provider", "source_id", "is_demo") and v.startswith("eq."):
                rows = [r for r in rows if str(r.get(k)).lower() == v[3:].lower()]
            elif k == "raw_metadata->>origin_section":
                rows = [r for r in rows if (r.get("raw_metadata") or {}).get("origin_section") == v[3:]]
            elif k == "dedup_key" and v.startswith("in.("):
                keys = set(v[4:-1].split(","))
                rows = [r for r in rows if r["dedup_key"] in keys]
        return [dict(r) for r in rows][: int(params.get("limit", 10000))]

    def insert(self, table, rows, prefer=""):
        for r in rows:
            self._n += 1
            self.signals.append({**r, "external_signal_id": f"s{self._n}"})
        return rows

    def update(self, table, params, patch):
        (k, v), = params.items()
        ids = set(v[4:-1].split(",")) if v.startswith("in.(") else {v[3:]}
        for r in self.signals:
            if r["external_signal_id"] in ids:
                r.update(patch)


@pytest.fixture
def env(monkeypatch):
    st = {"outcomes": [], "runs": [], "src": SRC_MOD, "client": MemClient()}
    monkeypatch.setattr(common, "resolve_source", lambda c, d: st["src"])
    monkeypatch.setattr(base, "create_ingestion_run", lambda c, **k: {"ingestion_run_id": "run"})
    monkeypatch.setattr(base, "find_or_save_raw_snapshot", lambda c, **k: ({"raw_snapshot_id": "snap"}, True))
    monkeypatch.setattr(base, "link_run_to_snapshot", lambda c, **k: {})
    monkeypatch.setattr(common, "finish_run", lambda c, run, **k: st["runs"].append(k))
    monkeypatch.setattr(common, "record_source_outcome", lambda c, row, run, *, ok, is_demo=False: st["outcomes"].append(ok))
    return st


def page(html):
    return lambda url: types.SimpleNamespace(content=html.encode("utf-8"), status_code=200, headers={"Content-Type": "text/html"})


def go(env, key, html, **kw):
    return hl.run_html_list_collection(env["client"], source_key=key, config=CFG, fetch=page(html), sleep=lambda s: None, **kw)


# ---- 統合幕僚監部（V1_1の回帰）----
def test_joint_staff_canonical_source_origin_section_and_dedup(env):
    r1 = go(env, "mod_joint_staff", JS_HTML)
    assert (r1["new"], r1["mode"]) == (2, "diff") and len(env["client"].signals) == 2
    sig = [s for s in env["client"].signals if "中国海軍艦艇" in s["title"]][0]
    md = sig["raw_metadata"]
    assert sig["source_id"] == "S-MOD" and sig["source_domain"] == "mod.go.jp" and sig["provider"] == "official"          # Canonical Source=防衛省
    assert md["origin_section"] == "統合幕僚監部" and md["collection_method"] == "html_list" and md["canonical_source_id"] == "S-MOD"
    assert md["page_url"] == "https://www.mod.go.jp/js/" and md["original_url"].endswith("p20260916_01.pdf") and md["baseline"] is False
    r2 = go(env, "mod_joint_staff", JS_HTML)
    assert (r2["new"], r2["updated"], r2["unchanged"]) == (0, 0, 2) and len(env["client"].signals) == 2


def test_legacy_rows_without_content_hash_are_not_treated_as_updates(env):
    go(env, "mod_joint_staff", JS_HTML)
    for s in env["client"].signals:
        s["raw_metadata"].pop("content_hash")                                  # V1_1で取り込まれた旧形式
    r = go(env, "mod_joint_staff", JS_HTML)
    assert (r["updated"], r["unchanged"]) == (0, 2) and all(s["raw_metadata"].get("content_hash") for s in env["client"].signals)


# ---- Maersk ----
def test_maersk_parser_uses_displayed_date_and_filter_is_deterministic():
    items = hl.parse_maersk_news_list(maersk_html(MAERSK_ITEMS), "https://www.maersk.com")
    assert [i["title"] for i in items][0] == "Middle East Operational Update 50"
    assert items[0]["published_at"].date().isoformat() == "2026-10-02"                  # URLの日付(09/29)ではなく表示日付
    assert items[2]["published_at"].date().isoformat() == "2026-09-29" and items[2]["url"].endswith("/2026/03/11/maersk-operations-through-strait-of-hormuz")
    pol = CFG["html_list_sources"]["maersk_advisories"]["policy"]
    assert [common.passes_policy(i["title"], None, pol) for i in items] == [True, False, True]


def test_maersk_baseline_then_dedup_then_update_with_version(env):
    env["src"] = SRC_MAERSK
    r1 = go(env, "maersk_advisories", maersk_html(MAERSK_ITEMS), fetch_detail=lambda u: DETAIL)
    sigs = env["client"].signals
    assert (r1["mode"], r1["new"], r1["skipped_count"], r1["detail_fetched"]) == ("baseline", 2, 1, 2) and len(sigs) == 2
    s = sigs[0]; md = s["raw_metadata"]
    assert s["source_id"] == "S-MAERSK" and s["source_role"] == "primary_evidence" and md["baseline"] is True       # Alert対象外のbaseline
    assert md["origin_section"] == "Maersk News & Advisories" and md["collection_method"] == "html_list" and md["page_url"].endswith("/news/category/advisories")
    assert md["original_url"].startswith("https://www.maersk.com/news/articles/") and md["content_hash"] and md["payload_hash"]
    assert "Strait of Hormuz" in s["summary"] and md["summary_source"] == "detail_meta_description"

    r2 = go(env, "maersk_advisories", maersk_html(MAERSK_ITEMS), fetch_detail=lambda u: DETAIL)
    assert (r2["mode"], r2["new"], r2["updated"], r2["unchanged"], r2["detail_fetched"]) == ("diff", 0, 0, 2, 0) and len(sigs) == 2     # 再取得で増えず、詳細も取り直さない

    upd = [("/news/articles/2026/09/29/middle-east-operational-update-50", "Middle East Operational Update 50", "9 Oct 2026")] + MAERSK_ITEMS[1:]
    r3 = go(env, "maersk_advisories", maersk_html(upd), fetch_detail=lambda u: DETAIL.replace("crisis", "crisis UPDATED"))
    assert (r3["new"], r3["updated"]) == (0, 1) and len(sigs) == 2                       # 同じURLの更新は新規ではなく更新
    u = [x for x in sigs if "update-50" in x["raw_metadata"]["original_url"]][0]
    assert u["raw_metadata"]["version"] == 2 and len(u["raw_metadata"]["previous_hashes"]) == 1 and u["raw_metadata"]["baseline"] is False
    assert u["published_at"].startswith("2026-10-09") and "UPDATED" in u["summary"]

    new_item = MAERSK_ITEMS + [("/news/articles/2026/10/10/red-sea-update", "Red Sea Routing Update", "10 Oct 2026")]
    r4 = go(env, "maersk_advisories", maersk_html(upd + new_item[3:]), fetch_detail=lambda u: DETAIL)
    assert (r4["new"], r4["mode"]) == (1, "diff") and [x for x in sigs if "red-sea-update" in x["raw_metadata"]["original_url"]][0]["raw_metadata"]["baseline"] is False


def test_maersk_detail_failure_still_saves_list_fields(env):
    env["src"] = SRC_MAERSK
    def bad(url): raise RuntimeError("boom")
    r = go(env, "maersk_advisories", maersk_html(MAERSK_ITEMS), fetch_detail=bad)
    assert r["new"] == 2 and r["detail_failed"] == 2 and all(s["summary"] is None and s["title"] for s in env["client"].signals)     # 要約は推測で埋めない


# ---- HTML Schema Guard ----
def test_missing_markers_is_schema_changed_and_empty_parse_is_parse_failed_never_success(env):
    env["src"] = SRC_MAERSK
    with pytest.raises(hl.SchemaChanged):
        go(env, "maersk_advisories", "<html><body>全く別の構造</body></html>")
    assert env["runs"][-1]["status"] == "failed" and "[schema_changed]" in env["runs"][-1]["error_summary"] and env["outcomes"] == [False]
    broken = '<html><div class="p-section__news__teaser__content">壊れた</div></html>'
    with pytest.raises(hl.ParseFailed):
        go(env, "maersk_advisories", broken)
    assert "[parse_failed]" in env["runs"][-1]["error_summary"] and env["client"].signals == []
    env["src"] = SRC_MOD
    with pytest.raises(hl.SchemaChanged):
        go(env, "mod_joint_staff", "<html>変わった</html>")


def test_gate_blocks_unapproved_source(env, monkeypatch):
    monkeypatch.setattr(common, "resolve_source", lambda c, d: {**SRC_MAERSK, "automation_allowed": False})
    out = hl.run_all_html_list_sources(env["client"], config=CFG, only=["maersk_advisories"])
    assert out["maersk_advisories"]["status"] == "not_ready" and env["client"].signals == []


# ---- Hormuz relevance: tanker AND geography / 海域分類 ----
TANKER_RULE = "all:tanker;Hormuz|Persian Gulf|Arabian Gulf|Gulf of Oman|Oman|UAE|Iran|Iranian|Fujairah|Muscat|Arabian Sea|Red Sea|Bab el Mandeb"


def a(title, summary=""):
    return {"title": title, "summary": summary}


def test_tanker_alone_is_not_hormuz_relevant_but_tanker_with_geography_is():
    assert mon.keyword_match_reason(TANKER_RULE, a("UKMTO Suspicious Activity: Gulf of Aden", "a tanker reported a suspicious approach")) is None
    assert mon.keyword_match_reason(TANKER_RULE, a("Tanker in der Ostsee")) is None
    assert mon.keyword_match_reason(TANKER_RULE, a("Iranian oil tankers stranded off the coast")) == "all:tanker+Iranian"       # 複数形・形容詞
    assert mon.keyword_match_reason(TANKER_RULE, a("UKMTO Attack: Southern Arabian Gulf", "tanker struck")) == "all:tanker+Arabian Gulf"
    assert mon.keyword_match_reason(TANKER_RULE, a("Roman tanker", "a woman on board")) is None                                  # Oman/woman等の部分一致をしない
    assert mon.keyword_match_reason(TANKER_RULE, a("Hormuz traffic")) is None                                                    # AND条件: tanker無し


def test_all_kind_in_theme_hit_and_region_classification():
    theme = {"keywords": ["Strait of Hormuz", "Gulf of Oman", TANKER_RULE]}
    assert mon._keyword_hit(theme, a("UKMTO Advisory: Gulf of Oman (#109/26)"))
    assert not mon._keyword_hit(theme, a("UKMTO Suspicious Activity: Gulf of Aden", "tanker"))
    rules = [{"key": "hormuz", "terms": ["Strait of Hormuz", "Hormuz"]}, {"key": "gulf_of_oman", "terms": ["Gulf of Oman"]},
             {"key": "persian_gulf", "terms": ["Persian Gulf", "Arabian Gulf"]}, {"key": "red_sea", "terms": ["Red Sea", "Bab el Mandeb"]},
             {"key": "wider_middle_east", "terms": ["Gulf of Aden", "Oman", "Iran"]}]
    cls = lambda t: mon.classify_maritime_region(a(t), rules)
    assert cls("UKMTO Attack: Strait of Hormuz (#156/26)") == "hormuz" and cls("UKMTO Advisory: Gulf of Oman (#109/26)") == "gulf_of_oman"
    assert cls("UKMTO Attack: Southern Arabian Gulf") == "persian_gulf" and cls("UKMTO Attack: Bab el Mandeb") == "red_sea"
    assert cls("UKMTO Suspicious Activity: Gulf of Aden") == "wider_middle_east" and cls("something else") is None


def test_real_config_hormuz_scope_regions():
    import geo_direct_collection_common as dc
    keys = [r["key"] for r in dc.load_direct_config()["maritime_regions"]]
    assert keys == ["hormuz", "gulf_of_oman", "persian_gulf", "red_sea", "wider_middle_east"]
    assert mon.classify_maritime_region(a("UKMTO Advisory: Gulf of Oman (#109/26)")) == "gulf_of_oman"
    assert mon.classify_maritime_region(a("UKMTO Suspicious Activity: Gulf of Aden")) == "wider_middle_east"
