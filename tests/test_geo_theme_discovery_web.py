"""Web Theme Discovery（geo_theme_discovery_web_service）のテスト。

ユーザー指示（2026-10-07）: テーマ探索であって記事探索ではない。Web検索はソースに限定せず、
ソース外も探す。記事数の足切りをしない。日付不明は日付不明のまま扱う。
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_expert_research_service as expert_research  # noqa: E402
import geo_llm_common  # noqa: E402
import geo_theme_discovery_service as discovery  # noqa: E402
import geo_theme_discovery_web_service as web  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

NOW = datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _no_real_perplexity(monkeypatch):
    """テストで実際のPerplexity（有料・外部送信）を呼ばない。既定はAzure版の模擬に固定する。"""
    monkeypatch.setattr(web, "_provider_settings", lambda: None)
BRIEF = {"summary": "ホルムズ海峡周辺の海運・保険の動きを探す", "keywords": ["ホルムズ", "保険"],
         "region": None, "period_days": 30, "exclude_mainstream": False}


def _client(**extra):
    tables = {"geo_source_registry": [{"source_id": "s1", "name": "S1", "url": "https://example.com",
                                       "archived_at": None}],
              "geo_source_domains": [], "geo_external_signals": [], "geo_theme_candidates": [],
              "geo_monitoring_themes": [], "geo_themes": []}
    tables.update(extra)
    return FakeSupabaseClient(tables=tables)


def _items(n):
    return [{"external_signal_id": f"sig-{i}", "title": f"見出し{i}", "summary": "", "url": f"https://x.com/{i}",
             "source_name": "x.com", "source_domain": "x.com", "published_at": None, "from_web": True}
            for i in range(1, n + 1)]


# ---------------------------------------------------------------------------
# 検証: 記事数の足切りをしない／URLの無い根拠は使わない／存在しない番号は無視
# ---------------------------------------------------------------------------

def test_validate_accepts_two_evidence_from_same_domain_no_domain_requirement():
    """従来のArticle Discoveryは『2ドメイン以上、または3記事以上』を要求したが、Web探索では要求しない。"""
    out = web.validate_web_themes(
        [{"title": "海運保険料が構造的に上がる兆候", "short_description": "d", "evidence_ids": [1, 2]}], _items(3))
    assert len(out) == 1 and len(out[0]["evidence"]) == 2


def test_validate_rejects_single_evidence_theme():
    assert web.validate_web_themes([{"title": "T", "short_description": "d", "evidence_ids": [1]}], _items(3)) == []


def test_validate_ignores_nonexistent_numbers_and_evidence_without_url():
    items = _items(2)
    items[1]["url"] = ""   # URLの無い根拠は使わない
    out = web.validate_web_themes([{"title": "T", "short_description": "d", "evidence_ids": [1, 2, 99]}], items)
    assert out == []       # 実在しURLのある根拠が1件だけ→テーマにしない


def test_validate_does_not_use_fetch_time_as_publication_date():
    out = web.validate_web_themes([{"title": "T", "short_description": "d", "evidence_ids": [1, 2]}], _items(2))
    # 日付不明の根拠だけのテーマは、最新日として「いま」を入れるが、根拠記事側の公開日は持たない
    assert all(a["published_at"] is None for a in out[0]["evidence"])


def test_merge_items_dedups_by_id_and_title_and_caps():
    web_items = _items(2)
    stored = [{"external_signal_id": "sig-1", "title": "別の見出し", "published_at": "2026-10-01T00:00:00+00:00"},
              {"external_signal_id": "st-9", "title": "見出し2", "published_at": "2026-10-01T00:00:00+00:00"},
              {"external_signal_id": "st-10", "title": "新しい見出し", "published_at": "2026-10-02T00:00:00+00:00"}]
    merged = web.merge_items(web_items, stored)
    assert [a["external_signal_id"] for a in merged] == ["sig-1", "sig-2", "st-10"]


# ---------------------------------------------------------------------------
# Web収集: ソースに限定しない検索を必ず行い、選択Sourceがあれば追加でdomain内検索
# ---------------------------------------------------------------------------

def test_collect_web_items_searches_open_web_and_tags_selected_source_hits(monkeypatch):
    questions = []

    def fake_research(azure_client, model, *, question, context, search_lens):
        questions.append(question)
        if "site:" in question:   # 選択ドメイン内の検索
            return {"summary": "s2", "found": True, "citations": [
                {"title": "選択ソース内", "url": "https://example.com/a"},
                {"title": "範囲外（捨てる）", "url": "https://elsewhere.com/z"}]}
        return {"summary": "s1", "found": True, "citations": [
            {"title": "ソース外の報道", "url": "https://other-news.com/b"},
            {"title": "選択ソース内", "url": "https://example.com/a"}]}
    monkeypatch.setattr(expert_research, "run_external_research", fake_research)

    client = _client()
    out = web.collect_web_items(client, object(), "m", brief=BRIEF, domains={"example.com"},
                                is_demo=False, now=NOW)
    urls = {a["url"] for a in out["items"]}
    assert "https://other-news.com/b" in urls            # ソース外も対象
    assert "https://example.com/a" in urls
    assert "https://elsewhere.com/z" not in urls         # domain内検索の範囲外は捨てる
    assert any("site:example.com" in q for q in questions)
    assert len([q for q in questions if "site:" not in q]) >= 2   # ソースに限定しない検索を複数回行う
    in_sel = {a["url"]: a["in_selected_sources"] for a in out["items"]}
    assert in_sel["https://example.com/a"] is True and in_sel["https://other-news.com/b"] is False
    assert all(a["published_at"] is None for a in out["items"])   # 取得時刻を公開日として見せない
    # 保存は既存のbulk upsert（provider分離）。timestamp_sourceはunknown
    rows = client.tables["geo_external_signals"]
    assert rows and all(r["provider"] == "source_discovery_web" and r["timestamp_source"] == "unknown" for r in rows)


def test_collect_web_items_without_selected_sources_does_only_open_search(monkeypatch):
    questions = []

    def fake_research(azure_client, model, *, question, context, search_lens):
        questions.append(question)
        return {"summary": "", "found": True, "citations": [{"title": "t", "url": "https://a.com/1"}]}
    monkeypatch.setattr(expert_research, "run_external_research", fake_research)
    out = web.collect_web_items(_client(), object(), "m", brief=BRIEF, domains=set(), is_demo=False, now=NOW)
    assert len(questions) >= 2 and not any("site:" in q for q in questions)
    assert len(out["items"]) == 1   # 同じURLは1件にまとめる


def test_collect_web_items_records_search_failure_without_raising(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("search down")
    monkeypatch.setattr(expert_research, "run_external_research", boom)
    out = web.collect_web_items(_client(), object(), "m", brief=BRIEF, domains=set(), is_demo=False, now=NOW)
    assert out["items"] == [] and out["stats"]["errors"]


# ---------------------------------------------------------------------------
# 探索の本体
# ---------------------------------------------------------------------------

def _patch_research(monkeypatch, citations):
    monkeypatch.setattr(expert_research, "run_external_research",
                        lambda *a, **k: {"summary": "web要約", "found": True, "citations": citations})
    monkeypatch.setattr(discovery, "retrieve_articles", lambda *a, **k: [])


def test_discover_web_creates_pending_web_origin_candidate(monkeypatch):
    _patch_research(monkeypatch, [{"title": "保険料の上昇", "url": "https://a.com/1"},
                                  {"title": "船舶の迂回が常態化", "url": "https://b.com/2"}])
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", lambda *a, **k: {"data": {
        "no_theme_reason": "", "themes": [{
            "title": "ホルムズ周辺の海運コストが構造的に上がる兆候", "short_description": "d",
            "evidence_ids": [1, 2]}]}})
    client = _client()
    steps = []
    out = web.discover_web(client, object(), "m", brief=BRIEF, source_ids=[], progress=steps.append, now=NOW)

    assert steps == ["interpret", "search", "cluster"]
    assert len(out["candidates"]) == 1 and out["message"] is None
    row = client.tables["geo_theme_candidates"][0]
    assert row["generation_model"] == "web_discovery/m" and row["status"] == "NEW"   # 自動確定しない
    cand = out["candidates"][0]
    assert cand["candidate_origin"] == "web_discovery" and cand["candidate_origin_label"] == "Webから発見"
    assert cand["proposal_state"] == "pending"
    # 根拠は実在URL。日付は不明のまま（date_known=False）
    assert {e["url"] for e in cand["evidence_articles"]} == {"https://a.com/1", "https://b.com/2"}
    assert all(e["date_known"] is False for e in cand["evidence_articles"])
    assert out["web"]["fetched"] == 2


def test_discover_web_does_not_gate_on_article_count(monkeypatch):
    """従来は『関連記事3件未満なら材料不足』でLLMを呼ばなかった。Web探索は2件の根拠でも整理する。"""
    _patch_research(monkeypatch, [{"title": "A", "url": "https://a.com/1"}, {"title": "B", "url": "https://b.com/2"}])
    called = {"n": 0}
    monkeypatch.setattr(web, "plan_queries", lambda *a, **k: ["問い"])

    def fake_llm(*a, **k):
        called["n"] += 1
        return {"data": {"no_theme_reason": "", "themes": []}}
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake_llm)
    out = web.discover_web(_client(), object(), "m", brief=BRIEF, source_ids=[], now=NOW)
    assert called["n"] == 1 and out["message"] == discovery.NO_RESULT_MESSAGE


def test_discover_web_returns_no_result_when_fewer_than_two_sources_found(monkeypatch):
    _patch_research(monkeypatch, [{"title": "A", "url": "https://a.com/1"}])
    monkeypatch.setattr(web, "plan_queries", lambda *a, **k: ["問い"])
    monkeypatch.setattr(geo_llm_common, "call_llm_structured",
                        lambda *a, **k: pytest.fail("根拠が2件未満ならLLMを呼ばない"))
    out = web.discover_web(_client(), object(), "m", brief=BRIEF, source_ids=[], now=NOW)
    assert out["message"] == discovery.NO_RESULT_MESSAGE and "2件未満" in out["reason"]
    assert out["candidates"] == []


def test_discover_web_requires_brief_and_llm():
    with pytest.raises(discovery.DiscoveryError):
        web.discover_web(_client(), object(), "m", brief={"summary": ""}, source_ids=[])
    with pytest.raises(discovery.DiscoveryError):
        web.discover_web(_client(), None, "m", brief=BRIEF, source_ids=[])


def test_discover_web_reports_existing_theme_instead_of_duplicating(monkeypatch):
    _patch_research(monkeypatch, [{"title": "保険料の上昇", "url": "https://a.com/1"},
                                  {"title": "船舶の迂回が常態化", "url": "https://b.com/2"}])
    title = "ホルムズ周辺の海運コストが構造的に上がる兆候"
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", lambda *a, **k: {"data": {
        "no_theme_reason": "", "themes": [{"title": title, "short_description": "d", "evidence_ids": [1, 2]}]}})
    client = _client(geo_theme_candidates=[{
        "candidate_id": "c-old", "title": title, "status": "NEW", "source_external_signal_ids": [],
        "generation_model": "article_discovery/m"}])
    out = web.discover_web(client, object(), "m", brief=BRIEF, source_ids=[], now=NOW)
    assert out["candidates"] == [] and len(out["existing_updates"]) == 1   # 乱造しない


def test_build_web_queries_makes_distinct_angles_and_caps():
    qs = web.build_web_queries({"summary": "ジョージア州の選挙", "keywords": ["ジョージア", "選挙", "投票", "資金", "広告"],
                                "region": "米国南部"})
    assert 2 <= len(qs) <= web.MAX_QUERIES and len(set(qs)) == len(qs)
    assert qs[0] == "ジョージア州の選挙"
    assert web.build_web_queries({"summary": "", "keywords": []}) == []


def test_collect_web_items_merges_results_of_multiple_queries(monkeypatch):
    n = {"i": 0}

    def fake_research(azure_client, model, *, question, context, search_lens):
        n["i"] += 1
        return {"summary": "", "found": True, "citations": [
            {"title": f"記事{n['i']}", "url": f"https://site{n['i']}.com/a"}]}
    monkeypatch.setattr(expert_research, "run_external_research", fake_research)
    out = web.collect_web_items(_client(), object(), "m", brief=BRIEF, domains=set(), is_demo=False, now=NOW)
    assert len(out["items"]) == n["i"] >= 2   # 検索ごとの結果が合算される


# ---------------------------------------------------------------------------
# 調査計画（plan_queries）→ 問いごとの並列Web検索 → 整理
# ---------------------------------------------------------------------------

def test_plan_queries_uses_llm_questions_with_lens_deduped_and_capped(monkeypatch):
    qs = [{"lens": "precedent", "question": f"問い{i}"} for i in range(20)] + [{"lens": "precedent", "question": "問い0"}]
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", lambda *a, **k: {"data": {"questions": qs}})
    out = web.plan_queries(object(), "m", BRIEF)
    assert [q["question"] for q in out] == [f"問い{i}" for i in range(web.MAX_QUERIES)]
    assert all(q["lens"] == "precedent" for q in out)


def test_plan_queries_falls_back_to_mechanical_queries_when_llm_fails(monkeypatch):
    def boom(*a, **k):
        raise geo_llm_common.GeoLLMError("失敗", attempts=[], exception_type="X")
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", boom)
    out = web.plan_queries(object(), "m", BRIEF)
    assert [q["question"] for q in out] == web.build_web_queries(BRIEF) and all(q["lens"] is None for q in out)


def test_collect_web_items_runs_one_search_per_planned_question_and_merges(monkeypatch):
    asked = []

    def fake_research(azure_client, model, *, question, context, search_lens):
        asked.append(question)
        i = len(asked)
        return {"summary": "", "found": True, "citations": [
            {"title": f"記事{i}-a", "url": f"https://s{i}.com/a"}, {"title": f"記事{i}-b", "url": f"https://s{i}.com/b"}]}
    monkeypatch.setattr(expert_research, "run_external_research", fake_research)
    planned = [f"計画した問い{i}" for i in range(10)]
    out = web.collect_web_items(_client(), object(), "m", brief=BRIEF, domains=set(), is_demo=False,
                                now=NOW, queries=planned)
    assert len(asked) == 10 and all(any(p_ in q_ for q_ in asked) for p_ in planned)
    assert len(out["items"]) == 20 and out["stats"]["queries"] == 10


def test_discover_web_plans_then_searches_then_clusters(monkeypatch):
    calls = []

    def fake_llm(client_, model_, system_prompt, user_prompt, schema, schema_name, **kw):
        calls.append(schema_name)
        if schema_name == "web_theme_discovery_plan":
            return {"data": {"questions": [{"lens": "actor_intent", "question": "問いA"},
                                           {"lens": "leading_indicator", "question": "問いB"},
                                           {"lens": "counter_view", "question": "問いC"}]}}
        return {"data": {"no_theme_reason": "", "themes": [{
            "title": "ジョージア州で有権者登録の運動が拡大する兆候", "short_description": "d", "evidence_ids": [1, 2]}]}}
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake_llm)
    n = {"i": 0}

    def fake_research(azure_client, model, *, question, context, search_lens):
        n["i"] += 1
        return {"summary": "要約", "found": True, "citations": [{"title": f"T{n['i']}", "url": f"https://x{n['i']}.com/1"}]}
    monkeypatch.setattr(expert_research, "run_external_research", fake_research)
    monkeypatch.setattr(discovery, "retrieve_articles", lambda *a, **k: [])
    out = web.discover_web(_client(), object(), "m", brief=BRIEF, source_ids=[], now=NOW)

    assert calls == ["web_theme_discovery_plan", "web_theme_discovery"]   # 計画→整理（検索は並列）
    assert n["i"] == 3 and out["searched_topics"] == [
        "【アクターの意図と能力】問いA", "【先行指標】問いB", "【反対の見方】問いC"]
    assert out["expert"]["lenses"] == ["actor_intent", "counter_view", "leading_indicator"]
    assert len(out["candidates"]) == 1 and out["web"]["queries"] == 3


def test_discover_web_no_result_message_says_how_many_questions_were_searched(monkeypatch):
    monkeypatch.setattr(geo_llm_common, "call_llm_structured",
                        lambda *a, **k: {"data": {"questions": [{"lens": "precedent", "question": "問いA"},
                                                                 {"lens": "precedent", "question": "問いB"}]}})
    monkeypatch.setattr(expert_research, "run_external_research",
                        lambda *a, **k: {"summary": "", "found": False, "citations": []})
    monkeypatch.setattr(discovery, "retrieve_articles", lambda *a, **k: [])
    out = web.discover_web(_client(), object(), "m", brief=BRIEF, source_ids=[], now=NOW)
    assert out["candidates"] == [] and "2個の問い" in out["reason"]


def test_plan_queries_tells_the_planner_todays_date_so_it_does_not_use_wrong_years(monkeypatch):
    seen = {}

    def fake_llm(client_, model_, system_prompt, user_prompt, schema, schema_name, **kw):
        seen["system"], seen["user"] = system_prompt, user_prompt
        return {"data": {"questions": ["問い"]}}
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake_llm)
    web.plan_queries(object(), "m", BRIEF, now=datetime(2026, 10, 8, tzinfo=timezone.utc))
    assert "今日の日付: 2026-10-08" in seen["user"]
    assert "今日の日付』を基準" in seen["system"]
