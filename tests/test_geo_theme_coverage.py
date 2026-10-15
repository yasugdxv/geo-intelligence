"""Coverage-aware Theme Freshness（決定論）のテスト。
「一部のSourceがfreshだからテーマ全体がfresh」にも「GDELTがstaleだから全テーマ使えない」にもならないこと、
partialでは決してstableと断定しないことを確認する。"""
from datetime import datetime, timedelta, timezone

import geo_monitoring_theme_service as mon
import geo_theme_coverage_service as cov

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
FRESH = {"status": "fresh", "last_data_at": NOW.isoformat(), "label": "最新"}
STALE = {"status": "stale", "last_data_at": "2026-10-02T10:45:00+00:00", "label": "更新停止（Stale）"}
UNKNOWN = {"status": "unknown", "last_data_at": None, "label": "取得実績なし"}


def src(sid="s1", name="日本 防衛省（報道発表）", ingest_h=6, published_d=1.0, allowed=True):
    return {"source_id": sid, "name": name, "url": "https://www.mod.go.jp/", "automation_allowed": allowed, "connector_enabled": True,
            "last_successful_ingestion_at": (NOW - timedelta(hours=ingest_h)).isoformat() if ingest_h is not None else None,
            "_published": (NOW - timedelta(days=published_d)).isoformat() if published_d is not None else None}


RULE = {"role": "primary", "freshness_basis": "published", "stale_after_days": 3, "collection_stale_days": 1}


def sf(s, rule=RULE):
    return cov.source_freshness(s, rule, s["_published"], now=NOW)


def test_source_freshness_published_basis():
    assert sf(src())["status"] == "fresh"
    assert sf(src(published_d=5))["status"] == "stale"                 # 公開元の最新記事が止まっている（WTO型）
    assert sf(src(ingest_h=40))["status"] == "stale"                    # 取得自体が止まっている
    assert sf(src(ingest_h=None))["status"] == "unknown"                # 取得実績なし
    assert sf(src(allowed=False))["status"] == "unknown"                # 未承認
    assert sf(src(published_d=None))["status"] == "stale"


def test_source_freshness_collection_basis_for_sanctions_lists():
    rule = {"role": "primary", "freshness_basis": "collection", "collection_stale_days": 2}
    assert sf(src(published_d=None), rule)["status"] == "fresh"        # 記事を持たないSourceは取得の健全性で判定
    assert sf(src(ingest_h=80, published_d=None), rule)["status"] == "stale"


def theme_fresh(broad, *sources, aux=()):
    direct = [sf(s) for s in sources] + [{**sf(s), "role": "auxiliary"} for s in aux]
    return cov.compute_theme_freshness(broad=broad, direct=direct)


def test_partial_when_direct_fresh_but_broad_stale():
    r = theme_fresh(STALE, src())
    assert r["freshness_state"] == "partial_freshness" and r["fresh_primary_source_ids"] == ["s1"]
    comps = {c["key"]: c["status"] for c in r["components"]}
    assert comps == {"primary": "fresh", "specialist": "not_configured", "broad_media": "stale"}


def test_no_fresh_data_when_no_direct_coverage_and_broad_stale():       # Hormuz型: GDELTだけに依存
    assert theme_fresh(STALE)["freshness_state"] == "no_fresh_data"
    assert theme_fresh(STALE, src(published_d=9))["freshness_state"] == "no_fresh_data"   # Directも止まっている


def test_fresh_requires_broad_fresh_and_all_primary_fresh():
    assert theme_fresh(FRESH)["freshness_state"] == "fresh"             # Direct未設定でも従来どおり
    assert theme_fresh(FRESH, src())["freshness_state"] == "fresh"
    assert theme_fresh(FRESH, src(), src("s2", "IAEA", published_d=9))["freshness_state"] == "partial_freshness"


def test_unknown_when_nothing_is_known():
    assert theme_fresh(UNKNOWN)["freshness_state"] == "unknown"
    assert theme_fresh(UNKNOWN, src(ingest_h=None))["freshness_state"] == "unknown"
    assert theme_fresh(UNKNOWN, src())["freshness_state"] == "partial_freshness"       # 取得実績のあるDirectだけ最新


def test_auxiliary_source_never_decides_freshness():                    # WTOは補助。主要Freshnessにしない
    assert theme_fresh(STALE, aux=[src("w", "WTO")])["freshness_state"] == "no_fresh_data"
    r = theme_fresh(STALE, src(), aux=[src("w", "WTO", published_d=9)])
    assert r["freshness_state"] == "partial_freshness" and r["components"][0]["auxiliary_sources"][0]["status"] == "stale"


def test_note_never_claims_theme_wide_no_change_when_partial():
    r = theme_fresh(STALE, src())
    note = cov.coverage_note("partial_freshness", r, alerts=0)
    assert "確認できる情報源" in note and "意味しません" in note and "変化なし" in note   # 「意味しない」と明示
    assert cov.coverage_note("fresh", theme_fresh(FRESH)) is None
    assert "未設定" in cov.coverage_note("no_fresh_data", theme_fresh(STALE))
    assert "新しい動きを検知" in cov.coverage_note("partial_freshness", r, alerts=1)


# ---- Monitoring assess への統合（記事収集とCoverageを差し替え）----
THEME = {"monitoring_theme_id": "t1", "display_name": "台湾", "keywords": ["Taiwan Strait"], "sensitivity": "medium"}


def run_assess(monkeypatch, *, freshness, coverage, articles):
    monkeypatch.setattr(mon.lib, "get_global_freshness", lambda c, **k: freshness)
    monkeypatch.setattr(mon, "collect_articles", lambda c, t, since, limit=300: {"articles": articles, "sources": [
        {"source_id": "s1", "name": "防衛省", "is_primary": True, "source_tier": "一次"}]})
    monkeypatch.setattr(mon.coverage_service, "compute_theme_freshness_for", lambda c, s, **k: coverage)
    return mon.assess(None, THEME, now=NOW, persist=False)["assessment"]


def test_assess_partial_without_alerts_is_partial_coverage_never_stable(monkeypatch):
    coverage = theme_fresh(STALE, src())
    old_gdelt = [{"external_signal_id": f"g{i}", "source_id": "gd", "title": "old", "summary": "", "published_at": (NOW - timedelta(days=5)).isoformat()} for i in range(9)]
    a = run_assess(monkeypatch, freshness=STALE, coverage=coverage, articles=old_gdelt)
    assert a["freshness_state"] == "partial_freshness" and a["state"] == "partial_coverage"
    assert a["state"] != "stable" and "意味しません" in a["coverage_note"] and a["summary"] == a["coverage_note"]


def test_assess_no_fresh_data_stays_no_fresh_data(monkeypatch):
    a = run_assess(monkeypatch, freshness=STALE, coverage=theme_fresh(STALE), articles=[])
    assert a["state"] == "no_fresh_data" and a["freshness_state"] == "no_fresh_data"


def test_assess_fresh_keeps_existing_stable_logic(monkeypatch):
    arts = [{"external_signal_id": "a1", "source_id": "x", "title": "news", "summary": "", "published_at": (NOW - timedelta(days=2)).isoformat()}]
    a = run_assess(monkeypatch, freshness=FRESH, coverage=theme_fresh(FRESH), articles=arts)
    assert a["state"] == "stable" and a["freshness_state"] == "fresh" and a["coverage_note"] is None


def test_assess_partial_alert_from_fresh_direct_source_is_reported_as_alert(monkeypatch):
    coverage = theme_fresh(STALE, src())
    arts = [{"external_signal_id": "m1", "source_id": "s1", "source_name": "防衛省", "title": "新規発表", "summary": "",
             "published_at": (NOW - timedelta(hours=2)).isoformat()}]
    a = run_assess(monkeypatch, freshness=STALE, coverage=coverage, articles=arts)
    assert a["state"] in ("watch", "changed") and "新しい動きを検知" in a["coverage_note"]


def test_direct_articles_need_theme_keyword_when_matched_only_by_source():
    """Source Setに入れただけで全発表がテーマの記事にならない（無関係な発表でAlertを出さない）。"""
    from tests._fakes import FakeSupabaseClient
    set_rows = [{"source_set_id": "set", "source_id": "s1", "sort_order": 0}]
    reg = [{"source_id": "s1", "name": "防衛省", "url": "https://www.mod.go.jp/", "archived_at": None}]
    sigs = [{"external_signal_id": "hit", "source_id": "s1", "provider": "rss", "title": "Taiwan Strait transit", "summary": "", "published_at": NOW.isoformat()},
            {"external_signal_id": "miss", "source_id": "s1", "provider": "rss", "title": "日米合同委員会", "summary": "", "published_at": NOW.isoformat()},
            {"external_signal_id": "gd", "source_id": "s1", "provider": "gdelt", "title": "other", "summary": "", "published_at": NOW.isoformat()}]
    client = FakeSupabaseClient(tables={"geo_source_set_members": set_rows, "geo_source_registry": reg, "geo_external_signals": sigs,
                                        "geo_source_domains": []})
    out = mon.collect_articles(client, {**THEME, "source_set_id": "set"}, since=NOW - timedelta(days=7))
    ids = {a["external_signal_id"] for a in out["articles"]}
    assert "hit" in ids and "miss" not in ids and "gd" in ids


# ---- Coverage別 Live Intelligence Status ----
def d(status, role="primary", name="X（y）"):
    return {"source_id": "i", "name": name, "role": role, "status": status, "reason": ""}


def test_area_status_ready_warning_blocked():
    assert cov.classify_area(broad=FRESH, direct=[d("fresh")])["status"] == "READY"
    assert cov.classify_area(broad=STALE, direct=[d("fresh")])["status"] == "WARNING"      # 一部だけ最新
    assert cov.classify_area(broad=FRESH, direct=[d("fresh"), d("stale")])["status"] == "WARNING"
    assert cov.classify_area(broad=STALE, direct=[d("stale")])["status"] == "BLOCKED"
    assert cov.classify_area(broad=STALE, direct=[])["status"] == "BLOCKED"                 # Middle East Maritime型
    assert cov.classify_area(broad=STALE, direct=[], broad_only=True)["status"] == "BLOCKED"
    assert cov.classify_area(broad=FRESH, direct=[], broad_only=True)["status"] == "READY"
    # 補助Source(auxiliary)だけが最新でも主要判定にしない
    assert cov.classify_area(broad=STALE, direct=[d("fresh", role="auxiliary")])["status"] == "BLOCKED"


# ---- Wave 2: 種類別Coverage（Hormuz / Middle East Maritime）----
def test_categories_show_kinds_separately_and_missing_kinds_are_explicit():
    cats = {"official_maritime_advisory": {"label": "Official / Maritime Advisory", "domains": []},
            "military_security": {"label": "Military / Security", "domains": ["cmf"]},
            "specialist_media": {"label": "Specialist Media", "domains": ["media"]},
            "broad_media": {"label": "Broad Media", "broad": True}}
    known = {"cmf": {"name": "CMF", "role": "primary", "status": "fresh", "reason": "x"},
             "media": {"name": "Specialist", "role": "specialist", "status": "stale", "reason": "y"}}
    out = {c["key"]: c for c in cov.classify_categories(cats, broad=STALE, direct_for=lambda d: known[d])}
    assert out["official_maritime_advisory"]["status"] == "not_configured"      # 未設定は「最新」にしない
    assert out["military_security"]["status"] == "fresh"
    assert out["specialist_media"]["status"] == "stale"                          # 専門メディアの鮮度は別枠で見せる
    assert out["broad_media"]["status"] == "stale"
    assert cov.classify_categories({"b": {"label": "B", "broad": True}}, broad=FRESH, direct_for=None)[0]["status"] == "fresh"


def test_specialist_role_does_not_decide_theme_freshness():
    """専門・報道Source（role=specialist）が最新でも、一次情報が無ければテーマをpartialにしない（一次と同じ重みにしない）。"""
    media = {**sf(src("m", "Iran International")), "role": "specialist"}
    r = cov.compute_theme_freshness(broad=STALE, direct=[media])
    assert r["freshness_state"] == "no_fresh_data"


# ---- V1_2: Shipping / Commercial（Maersk）は一次と同じ重みにしない ----
def test_commercial_role_is_shown_as_its_own_component_but_never_decides_freshness():
    maersk = {**sf(src("m", "Maersk 運航勧告")), "role": "commercial"}
    only = cov.compute_theme_freshness(broad=STALE, direct=[maersk])
    assert only["freshness_state"] == "no_fresh_data"                                           # 企業情報だけが最新でもテーマはfreshにならない
    comps = {c["key"]: c for c in only["components"]}
    assert comps["specialist"]["status"] == "fresh" and comps["specialist"]["sources"][0]["name"] == "Maersk 運航勧告"   # ただし種類としては見える
    both = cov.compute_theme_freshness(broad=STALE, direct=[sf(src()), maersk])
    assert both["freshness_state"] == "partial_freshness" and both["fresh_primary_source_ids"] == ["s1"]


def test_pilot_evaluable_follows_coverage_status(monkeypatch):
    class C:
        def select(self, table, params): return []
    monkeypatch.setattr(cov.lib, "get_global_freshness", lambda c, **k: STALE)
    monkeypatch.setattr(cov.lib, "get_coverage_freshness", lambda c, **k: {"sources": []})
    monkeypatch.setattr(cov.direct_common, "resolve_source", lambda c, d: {**src("x"), "source_id": d})
    cfgd = {"coverage_areas": {"a.example": {"role": "primary", "freshness_basis": "collection", "collection_stale_days": 1}},
            "live_intelligence_areas": {"ok": {"label": "OK", "direct": ["a.example"]}, "none": {"label": "None", "direct": []},
                                        "broad": {"label": "Broad", "direct": [], "broad_only": True}}}
    out = {a["key"]: a for a in cov.build_live_intelligence_status(C(), direct_config=cfgd)["areas"]}
    assert out["ok"]["pilot_evaluable"] is True and out["ok"]["status"] == "WARNING"
    assert out["none"]["pilot_evaluable"] is False and out["none"]["status"] == "BLOCKED"       # 直接Source無し=未評価
    assert out["broad"]["pilot_evaluable"] is False                                             # GDELT停止中のBroad Mediaは評価しない


def test_region_breakdown_only_for_middle_east_maritime_themes():
    arts = [{"title": "UKMTO Attack: Strait of Hormuz (#1/26)", "summary": ""}, {"title": "UKMTO Advisory: Gulf of Oman (#2/26)", "summary": ""},
            {"title": "UKMTO Suspicious Activity: Gulf of Aden (#3/26)", "summary": ""}, {"title": "unrelated", "summary": ""}]
    out = mon._region_breakdown({"tags": ["ホルムズ", "海運"]}, arts)
    assert out == {"hormuz": 1, "gulf_of_oman": 1, "wider_middle_east": 1, "unclassified": 1}
    assert mon._region_breakdown({"tags": ["台湾有事"]}, arts) is None
