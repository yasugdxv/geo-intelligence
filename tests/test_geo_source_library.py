"""Source Library / Source Set / Theme Explorer / Monitoring / Source Research（2026-10-05）。

DBはFakeSupabaseClient。検証対象:
- Source CRUD（soft delete）・Tag（AI/Human区別）・Domain Alias・Source↔Theme多対多
- safe domain matching（一意に確定できるものだけ、ambiguousは紐付けない）
- Source Set共有、Data Freshness Guard（停止中を「Stable」と表示しない）
- Monitoring Alert（reason必須、単一Source語HitではAlertにしない）、Theme→Monitoring引き継ぎ
- Theme Explorer「注目度」（説明可能・Priorityと呼ばない）
- Research: Reference必須、選択domain外のWeb結果は除外、根拠0件ならLLM回答を作らない
- Source削除（archive）でArticle・Source Set・Monitoringが壊れない
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_monitoring_theme_service as monitoring  # noqa: E402
import geo_source_library_service as lib  # noqa: E402
import geo_source_recommendation_service as recommend  # noqa: E402
import geo_source_research_service as research  # noqa: E402
import geo_source_set_service as sets  # noqa: E402
import geo_theme_explorer_service as explorer  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


def _iso(days=0, hours=0) -> str:
    return (NOW - timedelta(days=days, hours=hours)).isoformat()


def _src(sid, name, url, **kw):
    row = {"source_id": sid, "name": name, "url": url, "theme_id": None, "source_type": "other",
           "archived_at": None, "created_at": _iso(30), "updated_at": _iso(30)}
    row.update(kw)
    return row


def _signal(sig_id, *, source_id=None, domain="example.com", title="t", days=0, hours=0, summary=""):
    return {"external_signal_id": sig_id, "source_id": source_id, "source_domain": domain,
            "source_name": domain, "title": title, "summary": summary, "url": f"https://{domain}/{sig_id}",
            "published_at": _iso(days, hours), "created_at": _iso(days, hours), "entities": None}


# ---------------------------------------------------------------------------
# domain正規化
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("https://www.Example.com/path?q=1", "example.com"),
    ("http://user:pw@bis.gov:8080/x#y", "bis.gov"),
    ("CSIS.org.", "csis.org"),
    ("  ", None), (None, None),
    ("https://sub.agency.gov/", "sub.agency.gov"),
])
def test_normalize_domain(raw, expected):
    assert lib.normalize_domain(raw) == expected


# ---------------------------------------------------------------------------
# Source CRUD / soft delete / Tag / Domain / Theme link
# ---------------------------------------------------------------------------

def test_source_crud_and_soft_delete_keeps_articles_and_set_members():
    c = FakeSupabaseClient({"geo_source_registry": [], "geo_source_domains": [], "geo_external_signals": [],
                            "geo_source_sets": [], "geo_source_set_members": []})
    src = lib.create_source(c, {"name": "BIS", "url": "bis.gov", "media_type": "government",
                                "is_primary": True}, registered_by="user-1")
    assert src["url"] == "https://bis.gov" and src["registered_by"] == "user-1"
    assert [d["domain"] for d in c.tables["geo_source_domains"]] == ["bis.gov"]
    assert c.tables["geo_source_domains"][0]["is_primary"] is True

    updated = lib.update_source(c, src["source_id"], {"description": "輸出管理", "reliability": "high"})
    assert updated["description"] == "輸出管理"

    c.tables["geo_external_signals"].append(_signal("s1", source_id=src["source_id"], domain="bis.gov"))
    s = sets.create_source_set(c, name="半導体", source_ids=[src["source_id"]], created_by="user-1")
    lib.archive_source(c, src["source_id"])

    archived = lib.get_source(c, src["source_id"])
    assert archived["archived_at"] is not None                       # 物理削除しない
    assert c.tables["geo_external_signals"][0]["source_id"] == src["source_id"]   # Article維持
    assert len(c.tables["geo_source_set_members"]) == 1                          # Source Set維持
    assert sets.resolve_set_sources(c, s["source_set_id"])[0]["archived_at"]   # archivedと分かる
    assert lib.list_sources(c)["sources"] == []                                  # 通常一覧からは消える
    assert len(lib.list_sources(c, include_archived=True)["sources"]) == 1
    assert lib.restore_source(c, src["source_id"])["archived_at"] is None


def test_create_source_validation():
    c = FakeSupabaseClient({"geo_source_registry": []})
    with pytest.raises(lib.SourceValidationError):
        lib.create_source(c, {"url": "x.com"}, registered_by=None)           # name必須
    with pytest.raises(lib.SourceValidationError):
        lib.create_source(c, {"name": "x", "media_type": "bogus"}, registered_by=None)
    with pytest.raises(lib.SourceValidationError):
        lib.create_source(c, {"name": "x", "reliability": "super"}, registered_by=None)


def test_tags_distinguish_ai_and_human_and_confirm():
    c = FakeSupabaseClient({"geo_source_registry": [_src("s1", "BIS", "https://bis.gov")], "geo_source_tags": []})
    saved = lib.save_suggested_tags(c, "s1", [{"tag_type": "theme", "tag_value": "#ExportControl"},
                                              {"tag_type": "bad", "tag_value": "x"}])
    assert len(saved) == 1
    t = c.tables["geo_source_tags"][0]
    assert (t["origin"], t["is_confirmed"], t["tag_value"]) == ("ai", False, "ExportControl")
    # 人間が同じTagを追加 = AI提案の承認
    lib.add_tag(c, "s1", "theme", "exportcontrol", origin="human")
    assert len(c.tables["geo_source_tags"]) == 1
    assert c.tables["geo_source_tags"][0]["is_confirmed"] is True
    assert c.tables["geo_source_tags"][0]["origin"] == "human"
    h = lib.add_tag(c, "s1", "region", "China", origin="human")
    assert h["is_confirmed"] is True


def test_suggest_tags_llm_failure_does_not_raise():
    res = lib.suggest_tags(None, "m", {"name": "BIS"})
    assert res["tags"] == [] and res["error"]


def test_theme_links_many_to_many_separate_from_tags():
    c = FakeSupabaseClient({"geo_source_registry": [_src("s1", "BIS", "https://bis.gov")],
                            "geo_source_theme_links": [], "geo_source_tags": [], "geo_themes": [
                                {"theme_id": "t1", "name_ja": "米中半導体", "code": "us-cn-semi"},
                                {"theme_id": "t2", "name_ja": "AI規制", "code": "ai-reg"}]})
    lib.add_theme_link(c, "s1", "t1")
    lib.add_theme_link(c, "s1", "t2", relation_type="ai_recommended", relevance_score=0.7)
    lib.add_theme_link(c, "s1", "t1")  # 冪等
    assert len(c.tables["geo_source_theme_links"]) == 2
    assert c.tables["geo_source_tags"] == []
    detail = lib.get_source_detail(c, "s1")
    assert {l["theme"]["name"] for l in detail["theme_links"]} == {"米中半導体", "AI規制"}
    assert [s["source_id"] for s in lib.list_sources(c, theme_id="t2")["sources"]] == ["s1"]
    with pytest.raises(lib.SourceValidationError):
        lib.add_theme_link(c, "s1", "t1", relation_type="guess")


def test_list_sources_filters_and_pagination():
    rows = [_src(f"s{i}", f"Source{i:02d}", f"https://s{i}.example", media_type="think_tank" if i % 2 else "news",
                 is_primary=(i == 3)) for i in range(7)]
    c = FakeSupabaseClient({"geo_source_registry": rows, "geo_source_tags": [], "geo_external_signals": []})
    page1 = lib.list_sources(c, page=1, page_size=3)
    assert len(page1["sources"]) == 3 and page1["has_more"] is True
    page3 = lib.list_sources(c, page=3, page_size=3)
    assert len(page3["sources"]) == 1 and page3["has_more"] is False
    assert {s["source_id"] for s in lib.list_sources(c, media_type="think_tank")["sources"]} == {"s1", "s3", "s5"}
    assert [s["source_id"] for s in lib.list_sources(c, is_primary=True)["sources"]] == ["s3"]
    assert [s["source_id"] for s in lib.list_sources(c, q="source05")["sources"]] == ["s5"]


# ---------------------------------------------------------------------------
# safe domain matching
# ---------------------------------------------------------------------------

def test_resolve_source_for_domain_unique_ambiguous_unmatched():
    c = FakeSupabaseClient({
        "geo_source_registry": [_src("a", "A", "https://a.org"), _src("b", "B", "https://b.org"),
                                _src("c", "C", "https://c.org", archived_at=_iso(1))],
        "geo_source_domains": [
            {"domain_id": "1", "source_id": "a", "domain": "a.org", "is_primary": True, "match_enabled": True},
            {"domain_id": "2", "source_id": "a", "domain": "news.a.org", "is_primary": False, "match_enabled": True},
            {"domain_id": "3", "source_id": "a", "domain": "shared.org", "is_primary": False, "match_enabled": True},
            {"domain_id": "4", "source_id": "b", "domain": "shared.org", "is_primary": False, "match_enabled": True},
            {"domain_id": "5", "source_id": "c", "domain": "c.org", "is_primary": True, "match_enabled": True},
            {"domain_id": "6", "source_id": "b", "domain": "off.org", "is_primary": True, "match_enabled": False},
        ]})
    r = lib.resolve_source_for_domain(c, "https://www.A.org/x")
    assert (r["status"], r["source_id"], r["match_method"]) == ("unique", "a", "exact_domain")
    assert lib.resolve_source_for_domain(c, "news.a.org")["match_method"] == "alias_domain"
    amb = lib.resolve_source_for_domain(c, "shared.org")
    assert amb["status"] == "ambiguous" and amb["source_id"] is None          # 推測で紐付けない
    assert lib.resolve_source_for_domain(c, "unknown.org")["status"] == "unmatched"
    assert lib.resolve_source_for_domain(c, "c.org")["source_id"] is None      # archived Sourceへは紐付けない
    assert lib.resolve_source_for_domain(c, "off.org")["source_id"] is None    # match_enabled=falseは対象外
    assert lib.resolve_source_for_domain(c, "")["status"] == "no_domain"


# ---------------------------------------------------------------------------
# Data Freshness Guard
# ---------------------------------------------------------------------------

def test_freshness_guard_states():
    assert lib.compute_freshness(_iso(1), now=NOW)["status"] == "fresh"
    stale = lib.compute_freshness(_iso(26), now=NOW)
    assert stale["status"] == "stale" and "停止" in stale["label"]
    unknown = lib.compute_freshness(None, None, now=NOW)
    assert unknown["status"] == "unknown"
    # 記事日時と取得成功日時の新しい方を使う
    assert lib.compute_freshness(_iso(26), _iso(0, 2), now=NOW)["status"] == "fresh"


# ---------------------------------------------------------------------------
# Source Set共有 / Monitoring
# ---------------------------------------------------------------------------

def _monitoring_fixture(signals, *, theme_overrides=None, sources=None):
    srcs = sources or [_src("bis", "BIS", "https://bis.gov", is_primary=True),
                       _src("reu", "Reuters", "https://reuters.com", media_type="news_agency")]
    c = FakeSupabaseClient({
        "geo_source_registry": srcs,
        "geo_source_domains": [
            {"domain_id": "d1", "source_id": "bis", "domain": "bis.gov", "is_primary": True, "match_enabled": True},
            {"domain_id": "d2", "source_id": "reu", "domain": "reuters.com", "is_primary": True, "match_enabled": True}],
        "geo_source_sets": [], "geo_source_set_members": [], "geo_external_signals": signals,
        "geo_monitoring_themes": [], "geo_monitoring_alerts": [], "geo_theme_candidates": [],
        "geo_documents": []})
    ss = sets.create_source_set(c, name="米中半導体", source_ids=["bis", "reu"], created_by="u")
    theme = monitoring.create_monitoring_theme(c, {
        "display_name": "米中半導体規制", "source_set_id": ss["source_set_id"], "sensitivity": "medium",
        **(theme_overrides or {})}, created_by="u")
    return c, theme, ss


def test_monitoring_no_fresh_data_is_never_stable_or_alerting():
    # 最終取得が26日前 = GDELT停止の状況
    c, theme, _ = _monitoring_fixture([_signal("s1", source_id="bis", domain="bis.gov", days=26,
                                               title="BIS sanctions announcement")])
    result = monitoring.assess(c, theme, now=NOW)
    a = result["assessment"]
    assert a["state"] == "no_fresh_data"
    assert "判定できません" in a["summary"] and a["state"] != "stable"
    assert result["alerts_detected"] == []
    assert c.tables["geo_monitoring_alerts"] == []
    assert a["freshness"]["status"] == "stale"


def test_monitoring_fresh_feed_without_matching_articles_is_not_stable():
    # データ取得は動いている（無関係な新着あり）が、このテーマの記事は無い
    c, theme, _ = _monitoring_fixture([_signal("x1", domain="unrelated.org", hours=1)])
    a = monitoring.assess(c, theme, now=NOW)["assessment"]
    assert a["state"] == "no_new_articles" and a["state"] != "stable"


def test_monitoring_stable_when_articles_present_without_change():
    c, theme, _ = _monitoring_fixture([
        _signal("a1", source_id="reu", domain="reuters.com", days=2, title="Chip makers report earnings"),
        _signal("a2", source_id="reu", domain="reuters.com", days=3, title="Fab expansion plans"),
        _signal("fresh", domain="unrelated.org", hours=1)])
    a = monitoring.assess(c, theme, now=NOW)["assessment"]
    assert a["state"] == "stable" and a["articles_in_window"] == 2


def test_monitoring_alert_requires_multiple_sources_or_primary_and_has_reason():
    # 単一ソースの語Hitのみ → Alertにしない
    c, theme, _ = _monitoring_fixture([
        _signal("a1", source_id="reu", domain="reuters.com", hours=2, title="New sanctions rumoured")])
    assert monitoring.assess(c, theme, now=NOW)["alerts_detected"] == []

    # 一次情報Sourceからの新着 + 制裁語 → Alertになり、reasonに理由が入る
    c, theme, _ = _monitoring_fixture([
        _signal("a1", source_id="bis", domain="bis.gov", hours=2, title="BIS adds firms to Entity List"),
        _signal("a2", source_id="reu", domain="reuters.com", hours=3, title="US export controls tightened")])
    res = monitoring.assess(c, theme, now=NOW)
    types = {a["alert_type"] for a in res["alerts_detected"]}
    assert {"primary_announcement", "event_sanction"} <= types
    for a in res["alerts_detected"]:
        assert a["reason"] and a["related_signal_ids"]
    assert res["assessment"]["state"] == "changed"
    assert len(c.tables["geo_monitoring_alerts"]) == len(res["alerts_saved"]) >= 2
    # 再評価しても同じAlertを重複保存しない（fingerprint）
    before = len(c.tables["geo_monitoring_alerts"])
    monitoring.assess(c, monitoring.get_monitoring_theme(c, theme["monitoring_theme_id"]), now=NOW)
    assert len(c.tables["geo_monitoring_alerts"]) == before


def test_monitoring_volume_surge_alert():
    sigs = [_signal(f"v{i}", source_id="reu", domain="reuters.com", hours=1, title=f"Chip item {i}")
            for i in range(6)]
    c, theme, _ = _monitoring_fixture(sigs)
    res = monitoring.assess(c, theme, now=NOW)
    surge = [a for a in res["alerts_detected"] if a["alert_type"] == "volume_surge"]
    assert surge and "急増" in surge[0]["title"] and "基準" in surge[0]["reason"]


def test_theme_to_monitoring_carries_theme_source_set_actors_tags():
    cand = {"candidate_id": "cand-1", "title": "台湾海峡の緊張", "actors": ["PLA", "台湾国防部"],
            "regions": ["台湾"], "themes": ["軍事", "海峡"], "question": "演習範囲は拡大しているか"}
    c = FakeSupabaseClient({"geo_source_registry": [_src("s1", "MND", "https://mnd.gov.tw")],
                            "geo_source_sets": [], "geo_source_set_members": [], "geo_monitoring_themes": []})
    m = monitoring.create_from_candidate(c, cand, source_ids=["s1"], created_by="u")
    assert m["theme_candidate_id"] == "cand-1" and m["display_name"] == "台湾海峡の緊張"
    assert m["actors"] == ["PLA", "台湾国防部"] and m["tags"] == ["軍事", "海峡"] and m["regions"] == ["台湾"]
    members = sets.resolve_set_sources(c, m["source_set_id"])
    assert [s["source_id"] for s in members] == ["s1"]          # 同じSource Master（同一source_id）を参照
    # 既存Source Setをそのまま使う（Theme専用にならない共通Set）
    m2 = monitoring.create_from_candidate(c, {**cand, "candidate_id": "cand-2"}, source_set_id=m["source_set_id"])
    assert m2["source_set_id"] == m["source_set_id"]


def test_monitoring_list_summary_and_detail_with_references():
    c, theme, _ = _monitoring_fixture([
        _signal("a1", source_id="bis", domain="bis.gov", hours=2, title="BIS rule", ),
        _signal("a2", source_id="reu", domain="reuters.com", hours=3, title="Reuters report")])
    monitoring.assess(c, theme, now=datetime.now(timezone.utc))  # persist
    listing = monitoring.list_monitoring_themes(c)
    assert listing["summary"]["monitoring_count"] == 1
    assert "data_freshness" in listing
    detail = monitoring.get_detail(c, theme["monitoring_theme_id"])
    assert detail["watchpoints"]["sensitivity"] == "medium" and detail["sources"]
    for ref in detail["articles"]:
        assert ref["url"] and ref["article_title"] and ref["source"]


def test_source_set_edit_and_archive_do_not_break_monitoring():
    c, theme, ss = _monitoring_fixture([])
    lib.archive_source(c, "bis")
    got = monitoring.get_monitoring_theme(c, theme["monitoring_theme_id"])
    assert got["source_set_id"] == ss["source_set_id"]
    d = monitoring.get_detail(c, theme["monitoring_theme_id"], refresh=True)
    assert d is not None and len(d["sources"]) == 2                      # 参照は維持
    with pytest.raises(sets.SourceSetInUse):                       # 有効なMonitoringが参照中は既定で拒否
        sets.archive_source_set(c, ss["source_set_id"])
    sets.archive_source_set(c, ss["source_set_id"], force=True)    # force: archiveしてもMonitoringは動き続ける
    assert monitoring.get_monitoring_theme(c, theme["monitoring_theme_id"])["source_set_id"] == ss["source_set_id"]
    d2 = monitoring.get_detail(c, theme["monitoring_theme_id"], refresh=True)
    assert len(d2["sources"]) == 2 and d2["source_set"]["archived_at"]   # 監視は継続、archived表示用の情報あり


def test_monitoring_validation():
    c = FakeSupabaseClient({"geo_monitoring_themes": []})
    with pytest.raises(lib.SourceValidationError):
        monitoring.create_monitoring_theme(c, {"display_name": " "}, created_by=None)
    with pytest.raises(lib.SourceValidationError):
        monitoring.create_monitoring_theme(c, {"display_name": "x", "sensitivity": "extreme"}, created_by=None)


# ---------------------------------------------------------------------------
# Theme Explorer（注目度・分類）
# ---------------------------------------------------------------------------

def _cand(cid, **kw):
    row = {"candidate_id": cid, "title": f"Theme {cid}", "status": "NEW", "is_demo": False,
           "actors": ["A"], "regions": ["台湾"], "themes": ["半導体"], "business_relevance": ["supply_chain"],
           "signal_count": 10, "change_type": "policy_shift", "candidate_type": "changing",
           "first_seen_at": _iso(2), "latest_signal_at": _iso(1), "updated_at": _iso(1)}
    row.update(kw)
    return row


def test_attention_is_explainable_and_not_called_priority():
    att = explorer.compute_attention(_cand("1"), now=NOW)
    assert sum(b["points"] for b in att["breakdown"]) == att["score"]
    assert {b["label"] for b in att["breakdown"]} == {"業務関連性", "根拠記事数", "変化タイプ", "直近性", "新規性"}
    assert all("priority" not in k.lower() for k in att)
    stale = explorer.compute_attention(_cand("2", latest_signal_at=_iso(60), first_seen_at=_iso(90),
                                             signal_count=1, business_relevance=[]), now=NOW)
    assert stale["score"] < att["score"] and stale["level"] == "low"


def test_theme_explorer_filters_and_buckets():
    c = FakeSupabaseClient({"geo_theme_candidates": [
        _cand("new"), _cand("old", first_seen_at=_iso(60), latest_signal_at=_iso(50), status="NEW"),
        _cand("watch", first_seen_at=_iso(40), latest_signal_at=_iso(2), status="MONITORING", regions=["中東"]),
        _cand("done", status="HANDED_OFF")], "geo_external_signals": []})
    res = explorer.explore(c, None, None, now=NOW)
    by_id = {t["candidate_id"]: t for t in res["themes"]}
    assert "new" in by_id["new"]["buckets"] and by_id["old"]["buckets"] == ["past"]
    assert "ongoing" in by_id["watch"]["buckets"] and by_id["done"]["buckets"] == ["past"]
    assert res["bucket_counts"]["past"] == 2
    assert [t["candidate_id"] for t in explorer.explore(c, None, None, region="中東", now=NOW)["themes"]] == ["watch"]
    assert {t["candidate_id"] for t in explorer.explore(c, None, None, bucket="new", now=NOW)["themes"]} == {"new"}
    kw = explorer.explore(c, None, None, q="Theme watch", now=NOW)
    assert "watch" in {t["candidate_id"] for t in kw["themes"]} and kw["interpretation"]["used_llm"] is False
    assert res["data_freshness"]["status"] == "stale"      # 記事が1件も無い場合は更新停止扱い（freshにしない）


# ---------------------------------------------------------------------------
# Source Recommendation
# ---------------------------------------------------------------------------

def test_recommendation_has_reasons_and_flags_unregistered_domains():
    c = FakeSupabaseClient({
        "geo_source_registry": [
            _src("bis", "BIS", "https://bis.gov", is_primary=True, thematic_scope="輸出管理 半導体"),
            _src("zzz", "Gardening Weekly", "https://garden.example")],
        "geo_source_tags": [{"tag_id": "t", "source_id": "bis", "tag_value": "Semiconductor", "is_confirmed": True}],
        "geo_external_signals": [
            _signal("e1", source_id="bis", domain="bis.gov", days=2),
            _signal("e2", source_id=None, domain="newthinktank.org", days=2),
            _signal("e3", source_id=None, domain="newthinktank.org", days=3)],
        "geo_source_theme_links": []})
    res = recommend.recommend_sources(c, {"title": "US-China Semiconductor", "themes": ["Semiconductor"],
                                          "external_signal_ids": ["e1", "e2", "e3"]})
    names = [r["name"] for r in res["recommendations"]]
    assert names == ["BIS"]                                    # 無関係なSourceは推薦しない
    assert "記事を1件取得" in res["recommendations"][0]["reason"] and res["recommendations"][0]["reason"].endswith("。")
    assert res["unregistered_domains"][0] == {"domain": "newthinktank.org", "article_count": 2}


# ---------------------------------------------------------------------------
# Research（Reference必須・Source限定）
# ---------------------------------------------------------------------------

def _research_client(signals):
    return FakeSupabaseClient({
        "geo_source_registry": [_src("bis", "BIS", "https://www.bis.gov")],
        "geo_source_domains": [{"domain_id": "d", "source_id": "bis", "domain": "bis.gov",
                                "is_primary": True, "match_enabled": True}],
        "geo_external_signals": signals})


def test_research_without_references_does_not_generate_answer():
    c = _research_client([])
    res = research.run_source_research(c, None, "m", theme="T", query="輸出規制の動向は", source_ids=["bis"],
                                       use_web=False)
    assert res["answer"] is None and res["references"] == []
    assert any("根拠" in n for n in res["notes"])


def test_research_returns_references_with_title_source_date_url(monkeypatch):
    c = _research_client([_signal("e1", source_id="bis", domain="bis.gov", days=2, title="BIS Rule on AI chips",
                                  summary="輸出規制の新ルール")])
    res = research.run_source_research(c, None, "m", theme="T", query="輸出規制", source_ids=["bis"], use_web=False)
    assert len(res["references"]) == 1
    ref = res["references"][0]
    assert ref["article_title"] == "BIS Rule on AI chips" and ref["source"] == "bis.gov"
    assert ref["published_at"] and ref["original_url"].startswith("https://bis.gov/")
    assert res["answer"] is None and res["errors"]            # LLM無し: Referenceだけ返し、エラーを明示


def test_research_filters_web_citations_to_selected_domains(monkeypatch):
    c = _research_client([])

    def fake_external(azure, model, *, question, context, search_lens):
        assert "site:bis.gov" in question
        return {"summary": "要旨", "found": True, "citations": [
            {"url": "https://www.bis.gov/news/1", "title": "BIS news"},
            {"url": "https://media.bis.gov/x", "title": "Subdomain"},
            {"url": "https://evil.example/fake", "title": "Other domain"}]}

    monkeypatch.setattr(research.expert_research, "run_external_research", fake_external)

    class _NoLLM:  # azure_clientが「ある」扱い。回答生成だけ失敗させる
        pass

    def boom(*a, **k):
        raise research.geo_llm_common.GeoLLMError("down")

    monkeypatch.setattr(research.geo_llm_common, "call_llm_structured", boom)
    res = research.run_source_research(c, _NoLLM(), "m", theme="T", query="q", source_ids=["bis"])
    urls = {r["original_url"] for r in res["references"]}
    assert urls == {"https://www.bis.gov/news/1", "https://media.bis.gov/x"}   # 選択domain外は除外
    assert all(r["origin"] == "web" for r in res["references"])
    assert any("除外" in n for n in res["notes"])
    assert res["answer"] is None and any("回答の生成に失敗" in e for e in res["errors"])  # Referenceは残る


def test_research_requires_query_and_sources():
    c = _research_client([])
    with pytest.raises(lib.SourceValidationError):
        research.run_source_research(c, None, "m", theme="T", query=" ", source_ids=["bis"])
    with pytest.raises(lib.SourceValidationError):
        research.run_source_research(c, None, "m", theme="T", query="q", source_ids=[])


# ---------------------------------------------------------------------------
# API配線（認証・route）
# ---------------------------------------------------------------------------

def test_api_routes_registered_and_mutations_require_identity():
    import geo_monitoring_theme_api as mon_api
    import geo_source_library_api as src_api
    from fastapi.params import Depends as DependsMarker
    import geo_ios_identity

    def has_auth(route):
        return any(d.call is geo_ios_identity.verify_ios_identity for d in route.dependant.dependencies)

    for router in (src_api.geo_source_library_router, src_api.geo_source_set_router,
                   src_api.geo_source_research_router, mon_api.geo_monitoring_theme_router):
        for route in router.routes:
            if route.methods & {"POST", "PATCH", "DELETE"}:
                if route.path.endswith("/recommend"):
                    continue  # 読み取り専用のPOST
                assert has_auth(route), f"認証なしのmutation: {route.methods} {route.path}"
    paths = {r.path for r in src_api.geo_source_library_router.routes}
    assert "/api/geo/source-library/search" in paths and "/api/geo/source-library/{source_id}" in paths
    del DependsMarker


# ---------------------------------------------------------------------------
# 回帰: geo_external_signalsはcreated_atにindexが無い（2026-10-05 E2Eで500/timeout発生）
# ---------------------------------------------------------------------------

class _RecordingClient(FakeSupabaseClient):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.signal_queries = []

    def select(self, table, params):
        if table == "geo_external_signals":
            self.signal_queries.append(dict(params))
        return super().select(table, params)


def test_signal_queries_never_order_or_filter_by_unindexed_created_at():
    c = _RecordingClient({
        "geo_source_registry": [_src("bis", "BIS", "https://bis.gov")],
        "geo_source_domains": [{"domain_id": "d1", "source_id": "bis", "domain": "bis.gov",
                                "is_primary": True, "match_enabled": True}],
        "geo_source_sets": [], "geo_source_set_members": [], "geo_monitoring_themes": [], "geo_monitoring_alerts": [],
        "geo_theme_candidates": [], "geo_documents": [], "geo_source_tags": [], "geo_source_theme_links": [],
        "geo_external_signals": [_signal("s1", source_id="bis", domain="bis.gov", hours=2)]})
    ss = sets.create_source_set(c, name="x", source_ids=["bis"])
    theme = monitoring.create_monitoring_theme(c, {"display_name": "t", "source_set_id": ss["source_set_id"],
                                                  "keywords": ["chip"]}, created_by=None)
    monitoring.assess(c, theme, now=NOW, persist=False)
    lib.list_sources(c)
    lib.get_source_detail(c, "bis")
    research.run_source_research(c, None, "m", theme="T", query="chip rule", source_ids=["bis"], use_web=False)
    assert c.signal_queries
    for q in c.signal_queries:
        assert "created_at" not in str(q.get("order", "")), q
        assert "created_at" not in q, q          # created_atでの絞り込みもしない


def test_global_freshness_without_recent_signals_is_stale_not_fresh():
    c = FakeSupabaseClient({"geo_external_signals": [_signal("old", days=90)]})
    f = lib.get_global_freshness(c)
    assert f["status"] == "stale" and f["label"]


def test_research_notes_when_references_are_only_recent_padding_not_keyword_matches():
    c = _research_client([_signal("e1", source_id="bis", domain="bis.gov", days=2, title="Unrelated item", summary="x")])
    res = research.run_source_research(c, None, "m", theme="T", query="輸出規制の動向", source_ids=["bis"], use_web=False)
    assert res["references"] and res["references"][0]["keyword_matched"] is False
    assert any("直近記事" in n for n in res["notes"])          # 関連記事のように見せない
    c2 = _research_client([_signal("e2", source_id="bis", domain="bis.gov", days=2, title="輸出規制の新ルール", summary="輸出規制")])
    res2 = research.run_source_research(c2, None, "m", theme="T", query="輸出規制", source_ids=["bis"], use_web=False)
    assert res2["references"][0]["keyword_matched"] is True and not any("直近記事" in n for n in res2["notes"])


# ---------------------------------------------------------------------------
# Source Setの論理削除（DB-6）
# ---------------------------------------------------------------------------

def _set_client():
    return FakeSupabaseClient({
        "geo_source_registry": [_src("a", "A", "https://a.org"), _src("b", "B", "https://b.org")],
        "geo_source_sets": [], "geo_source_set_members": [], "geo_monitoring_themes": []})


def test_archive_source_set_hides_from_default_list_keeps_members_sources_and_detail():
    c = _set_client()
    sets.create_source_set(c, name="[TEST] keep", source_ids=["a", "b"])
    s2 = sets.create_source_set(c, name="[TEST] to archive", source_ids=["a"])
    res = sets.archive_source_set(c, s2["source_set_id"])
    assert res["source_set"]["archived_at"] and res["in_use_by"] == []
    assert [x["name"] for x in sets.list_source_sets(c)] == ["[TEST] keep"]                  # 通常一覧: archived除外
    assert {x["name"] for x in sets.list_source_sets(c, include_archived=True)} == {"[TEST] keep", "[TEST] to archive"}
    d = sets.get_source_set(c, s2["source_set_id"])                                          # ID指定ならarchived済みも取得可
    assert d and d["archived_at"] and [m["source_id"] for m in d["sources"]] == ["a"]      # memberは維持
    assert {s["source_id"] for s in c.tables["geo_source_registry"]} == {"a", "b"}          # Source自体は変更されない
    assert all(s.get("archived_at") is None for s in c.tables["geo_source_registry"])
    assert not any(t == "geo_source_sets" for t, _ in getattr(c, "deleted", []))             # 物理DELETEしていない
    assert sets.restore_source_set(c, s2["source_set_id"])["archived_at"] is None
    assert len(sets.list_source_sets(c)) == 2
    assert sets.archive_source_set(c, "nope") is None


def test_archived_source_set_cannot_be_newly_linked_to_monitoring_but_existing_link_stays():
    c = _set_client()
    s = sets.create_source_set(c, name="x", source_ids=["a"])
    th = monitoring.create_monitoring_theme(c, {"display_name": "m1", "source_set_id": s["source_set_id"]}, created_by=None)
    sets.archive_source_set(c, s["source_set_id"], force=True)
    with pytest.raises(lib.SourceValidationError):
        monitoring.create_monitoring_theme(c, {"display_name": "m2", "source_set_id": s["source_set_id"]}, created_by=None)
    other = sets.create_source_set(c, name="y", source_ids=["b"])
    th2 = monitoring.create_monitoring_theme(c, {"display_name": "m3", "source_set_id": other["source_set_id"]}, created_by=None)
    with pytest.raises(lib.SourceValidationError):                    # 別のarchived Setへの付け替えも不可
        monitoring.update_monitoring_theme(c, th2["monitoring_theme_id"], {"source_set_id": s["source_set_id"]})
    # 既に紐付いているテーマの他の項目更新は、Setがarchivedでもできる
    ok = monitoring.update_monitoring_theme(c, th["monitoring_theme_id"], {"keywords": ["k"], "source_set_id": s["source_set_id"]})
    assert ok["keywords"] == ["k"]


def test_archive_allowed_when_only_archived_monitoring_references_it():
    c = _set_client()
    s = sets.create_source_set(c, name="x", source_ids=["a"])
    th = monitoring.create_monitoring_theme(c, {"display_name": "m", "source_set_id": s["source_set_id"]}, created_by=None)
    monitoring.archive_monitoring_theme(c, th["monitoring_theme_id"])
    assert sets.archive_source_set(c, s["source_set_id"])["source_set"]["archived_at"]


def test_source_set_list_works_before_db6_migration():
    import requests

    class NoArchivedColumn(FakeSupabaseClient):
        def select(self, table, params):
            if table == "geo_source_sets" and "archived_at" in params:
                resp = requests.Response()
                resp.status_code = 400
                raise requests.HTTPError("column archived_at does not exist", response=resp)
            return super().select(table, params)

    c = NoArchivedColumn({"geo_source_sets": [{"source_set_id": "s", "name": "legacy", "updated_at": "2026-10-01"}],
                          "geo_source_set_members": []})
    assert [x["name"] for x in sets.list_source_sets(c)] == ["legacy"]


def test_source_set_delete_api_is_archive_only_and_requires_identity():
    import geo_ios_identity
    import geo_source_library_api as api
    routes = [r for r in api.geo_source_set_router.routes if r.path.endswith("/{source_set_id}") and "DELETE" in r.methods]
    assert len(routes) == 1 and routes[0].name == "archive_source_set"                      # 物理削除APIは無い
    assert any(d.call is geo_ios_identity.verify_ios_identity for d in routes[0].dependant.dependencies)


# ---------------------------------------------------------------------------
# テーマ探索: 調査プロンプト（長い自然言語）での絞り込み（V1_1: 入力UIをプロンプト型にした対応）
# ---------------------------------------------------------------------------

def test_theme_explorer_long_japanese_prompt_is_split_into_keywords_without_llm():
    kws = explorer._fallback_keywords("台湾海峡をめぐる軍事・外交面の動きのうち、まだ一般には大きく注目されていないが重要なテーマを知りたい")
    assert "軍事" in kws and "外交面" in kws and "台湾海峡" in kws
    assert "テーマ" not in kws and "知りたい" not in kws and "動き" not in kws          # 依頼表現・一般語は除く
    assert len(kws) <= 6
    assert explorer._fallback_keywords("Taiwan Strait military activity") == ["Taiwan", "Strait", "military", "activity"]


def test_theme_explorer_long_japanese_prompt_filters_candidates_without_llm():
    c = FakeSupabaseClient({"geo_theme_candidates": [
        _cand("mil", title="台湾周辺の軍事活動の変化", themes=["軍事"]),
        _cand("garden", title="園芸市場の動向", themes=["園芸"], actors=["JA"], regions=["日本"],
              business_relevance=[], question="植物の売上", why_now="春", observed_change="x", signal_summary="y")],
        "geo_external_signals": []})
    res = explorer.explore(c, None, None, q="台湾海峡をめぐる軍事・外交面の動きのうち、重要なテーマを知りたい", now=NOW)
    assert [t["candidate_id"] for t in res["themes"]] == ["mil"]
    assert res["interpretation"]["used_llm"] is False and res["interpretation"]["filters"]["keywords"]


# ---------------------------------------------------------------------------
# テーマ探索 V1_1: 並び替え・キーワード・論理削除・候補総数
# ---------------------------------------------------------------------------

def _explore_client():
    return FakeSupabaseClient({"geo_theme_candidates": [
        _cand("a", title="台湾海峡の軍事", latest_signal_at=_iso(3)),
        _cand("b", title="米中半導体の輸出管理", latest_signal_at=_iso(1)),
        _cand("c", title="ホルムズ海峡の海運", latest_signal_at=_iso(5)),
        _cand("gone", title="削除済みのテーマ", status="DISMISSED", latest_signal_at=_iso(0))],
        "geo_external_signals": []})


def test_theme_explorer_sorts_newest_first_by_default_and_oldest_on_request():
    c = _explore_client()
    assert [t["candidate_id"] for t in explorer.explore(c, None, None, now=NOW)["themes"]] == ["b", "a", "c"]
    assert [t["candidate_id"] for t in explorer.explore(c, None, None, sort="newest", now=NOW)["themes"]] == ["b", "a", "c"]
    assert [t["candidate_id"] for t in explorer.explore(c, None, None, sort="oldest", now=NOW)["themes"]] == ["c", "a", "b"]


def test_theme_explorer_keyword_is_a_simple_and_filter_without_llm():
    c = _explore_client()
    res = explorer.explore(c, None, None, keyword="海峡", now=NOW)
    assert [t["candidate_id"] for t in res["themes"]] == ["a", "c"] or {t["candidate_id"] for t in res["themes"]} == {"a", "c"}
    assert res["interpretation"] is None                                   # キーワードはLLMを使わない
    both = explorer.explore(c, None, None, keyword="海峡 軍事", now=NOW)      # 空白区切りは全語を含む(AND)
    assert [t["candidate_id"] for t in both["themes"]] == ["a"]
    assert explorer.explore(c, None, None, keyword="存在しない語", now=NOW)["total"] == 0


def test_theme_explorer_deleted_candidates_are_hidden_by_default_and_available_total_counts_only_live_ones():
    c = _explore_client()
    res = explorer.explore(c, None, None, now=NOW)
    assert "gone" not in {t["candidate_id"] for t in res["themes"]}
    assert res["available_total"] == 3                                      # 削除済みを除く総数
    narrowed = explorer.explore(c, None, None, keyword="存在しない語", now=NOW)
    assert narrowed["total"] == 0 and narrowed["available_total"] == 3      # 「候補自体が0件」と「絞り込みで0件」を区別できる
    incl = explorer.explore(c, None, None, include_deleted=True, now=NOW)
    assert "gone" in {t["candidate_id"] for t in incl["themes"]} and incl["available_total"] == 4
    assert explorer.explore(FakeSupabaseClient({"geo_theme_candidates": [], "geo_external_signals": []}),
                            None, None, now=NOW)["available_total"] == 0


def test_theme_explorer_api_keeps_v1_contract_and_adds_new_params():
    import geo_monitoring_theme_api as api
    route = next(r for r in api.geo_theme_explorer_router.routes if r.path.endswith("/theme-explorer") and "GET" in r.methods)
    names = {p.name for p in route.dependant.query_params}
    assert {"q", "region", "category", "level", "bucket", "new_only", "period_days", "page", "page_size"} <= names   # V1の契約は維持
    assert {"keyword", "sort", "include_deleted"} <= names


def test_theme_explorer_region_from_the_prompt_matches_loosely_not_exactly():
    # AIが文章から取り出す地域（台湾海峡）と候補の地域タグ（台湾）は粒度が違う。完全一致で落とさない。
    c = FakeSupabaseClient({"geo_theme_candidates": [
        _cand("tw", title="軍事活動", regions=["台湾"], themes=["軍事"]),
        _cand("me", title="海運", regions=["中東"], themes=["海運"])], "geo_external_signals": []})
    ids = lambda r: {t["candidate_id"] for t in r["themes"]}  # noqa: E731
    assert ids(explorer.explore(c, None, None, region="台湾海峡", now=NOW)) == {"tw"}      # 台湾 は 台湾海峡 に含まれる
    assert ids(explorer.explore(c, None, None, region="台湾", now=NOW)) == {"tw"}
    assert ids(explorer.explore(c, None, None, region="アジア太平洋", now=NOW)) == set()   # 無関係な地域は落とす


def test_recommendation_counts_evidence_articles_by_unique_registered_domain_even_without_source_id():
    """取り込み後に追加された記事にはsource_idが無い。根拠記事のdomainが登録Sourceに一意に対応するなら、そのSourceをおすすめする。
    ambiguousは推測で寄せない。未登録domainは未登録として示す。"""
    c = FakeSupabaseClient({
        "geo_source_registry": [_src("tt", "Taipei Times", "https://taipeitimes.com"), _src("a1", "Al Jazeera", "https://aljazeera.com"),
                                _src("a2", "www.aljazeera.com", "https://www.aljazeera.com")],
        "geo_source_domains": [
            {"domain_id": "1", "source_id": "tt", "domain": "taipeitimes.com", "is_primary": True, "match_enabled": True},
            {"domain_id": "2", "source_id": "a1", "domain": "aljazeera.com", "is_primary": True, "match_enabled": True},
            {"domain_id": "3", "source_id": "a2", "domain": "aljazeera.com", "is_primary": True, "match_enabled": True}],
        "geo_source_tags": [], "geo_source_theme_links": [],
        "geo_external_signals": [_signal("e1", source_id=None, domain="www.taipeitimes.com", days=1),
                                 _signal("e2", source_id=None, domain="aljazeera.com", days=1),
                                 _signal("e3", source_id=None, domain="odt.co.nz", days=1)]})
    res = recommend.recommend_sources(c, {"title": "Taiwan Strait", "external_signal_ids": ["e1", "e2", "e3"]})
    assert [r["name"] for r in res["recommendations"]] == ["Taipei Times"]           # 一意に確定できるものだけ
    assert "記事を1件取得" in res["recommendations"][0]["reason"]
    assert res["unregistered_domains"] == [{"domain": "odt.co.nz", "article_count": 1}]   # ambiguous(aljazeera)は未登録扱いにもしない
