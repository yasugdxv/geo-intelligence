"""テーマ探索の専門家の視点層（geo_theme_discovery_expert と、その探索への組み込み）のテスト。"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_expert_research_service as expert_research  # noqa: E402
import geo_llm_common  # noqa: E402
import geo_theme_discovery_expert as expert  # noqa: E402
import geo_theme_discovery_service as discovery  # noqa: E402
import geo_theme_discovery_web_service as web  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

NOW = datetime(2026, 10, 8, 3, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _no_real_perplexity(monkeypatch):
    monkeypatch.setattr(web, "_provider_settings", lambda: None)


def _kb_client(items=None, issues=None, entities=None):
    return FakeSupabaseClient(tables={
        "geo_knowledge_items": items or [], "geo_issues": issues or [], "geo_entities": entities or [],
        "geo_source_registry": [], "geo_source_domains": [], "geo_external_signals": [],
        "geo_theme_candidates": [], "geo_monitoring_themes": [], "geo_themes": []})


def _item(title, ktype="actor_profile", status="active", retrieval="available", demo=False, **sc):
    return {"knowledge_item_id": f"k-{title}", "knowledge_type": ktype, "title": title, "status": status,
            "retrieval_status": retrieval, "is_demo": demo, "as_of_date": "2026-09-07",
            "structured_content": {"core_interests": ["関税による国内産業保護"], **sc}}


BRIEF = {"summary": "トランプ政権の関税が日本企業に与える影響", "keywords": ["関税", "トランプ"], "region": None,
         "period_days": 30}


def test_lens_guide_covers_expert_viewpoints():
    keys = {l["key"] for l in expert.EXPERT_LENSES}
    assert {"actor_intent", "institutional_constraint", "precedent", "leading_indicator",
            "counter_view", "business_exposure"} <= keys
    assert "先行指標" in expert.lens_guide_text()
    assert "日本企業への影響" in expert.search_instruction("business_exposure")
    assert expert.search_instruction(None)       # lens無しでも共通の指示を返す


def test_gather_returns_only_active_available_non_demo_items():
    client = _kb_client(items=[
        _item("Donald Trump"),                                  # 使える
        _item("Trump候補", status="candidate"),                 # 承認前は使わない
        _item("Trump制限", retrieval="restricted"),             # 参照制限は使わない
        _item("Trumpデモ", demo=True)])                         # デモは使わない
    out = expert.gather_expert_context(client, BRIEF)
    assert out["has_support"] is True
    assert [k["title"] for k in out["knowledge"]] == ["Donald Trump"]
    assert "仮説の材料" in out["text"] and "Donald Trump" in out["text"]


def test_gather_matches_issues_and_reports_no_support_when_nothing_relevant():
    client = _kb_client(
        items=[_item("Xi Jinping")],
        issues=[{"issue_id": "i1", "name_ja": "米中半導体・AI輸出規制", "name_en": "", "description": "規制", "status": "active"}])
    out = expert.gather_expert_context(client, {"summary": "半導体の輸出規制", "keywords": ["半導体"], "period_days": 30})
    assert [i["name"] for i in out["issues"]] == ["米中半導体・AI輸出規制"] and out["has_support"] is True

    none = expert.gather_expert_context(
        client, {"summary": "ジョージア州の教育委員会選挙", "keywords": ["教育委員会"], "period_days": 30})
    assert none["has_support"] is False and none["text"] == ""      # 無い知識は作らない


def test_gather_survives_database_errors():
    class Broken:
        def select(self, *a, **k):
            raise RuntimeError("db down")
    out = expert.gather_expert_context(Broken(), BRIEF)
    assert out["has_support"] is False and out["text"] == ""


def test_plan_prompt_includes_lenses_and_expert_knowledge(monkeypatch):
    seen = {}

    def fake_llm(client_, model_, system_prompt, user_prompt, schema, schema_name, **kw):
        seen.update(system=system_prompt, user=user_prompt, schema=schema)
        return {"data": {"questions": [{"lens": "leading_indicator", "question": "Q"}]}}
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake_llm)
    ctx = {"text": "【知識DBの裏付け】- [actor_profile] Donald Trump: …", "has_support": True}
    out = web.plan_queries(object(), "m", BRIEF, now=NOW, expert_context=ctx)
    assert out == [{"question": "Q", "lens": "leading_indicator"}]
    assert "先行指標" in seen["system"] and "アクターの意図と能力" in seen["system"]
    assert "知識DBの裏付け" in seen["user"] and "今日の日付: 2026-10-08" in seen["user"]
    lens_enum = seen["schema"]["properties"]["questions"]["items"]["properties"]["lens"]["enum"]
    assert "counter_view" in lens_enum


def test_search_instruction_is_passed_per_lens_to_azure_search(monkeypatch):
    seen = []

    def fake_research(azure_client, model, *, question, context, search_lens):
        seen.append(context)
        return {"summary": "", "found": True, "citations": []}
    monkeypatch.setattr(expert_research, "run_external_research", fake_research)
    web._search_web(object(), "m", question="Q", context="C。", lens="leading_indicator")
    assert "先行指標" in seen[0] and "観測可能" in seen[0] or "数値と日付" in seen[0]


def test_cluster_uses_expert_schema_and_carries_expert_fields_to_the_candidate_row(monkeypatch):
    captured = {}

    def fake_llm(client_, model_, system_prompt, user_prompt, schema, schema_name, **kw):
        captured.update(system=system_prompt, user=user_prompt, schema=schema)
        return {"data": {"no_theme_reason": "", "themes": [{
            "title": "関税が日本の飲料輸出の採算を変える兆候", "short_description": "d", "evidence_ids": [1, 2],
            "known_context": "過去の関税局面と同様に価格転嫁が先行するが、今回は例外措置が無い点が違う",
            "leading_indicators": ["USTRの官報告示", "主要小売の価格改定発表"],
            "business_impact": "米国向け飲料の粗利が圧迫される", "contradicts_knowledge": True}]}}
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake_llm)
    items = [{"external_signal_id": f"s{i}", "title": f"見出し{i}", "summary": "", "url": f"https://x.com/{i}",
              "source_domain": "x.com", "published_at": None} for i in (1, 2)]
    ctx = {"text": "【知識DBの裏付け】- [actor_profile] Donald Trump", "has_support": True}
    themes, _ = web.cluster_web_themes(object(), "m", "p", items, [], expert_context=ctx)

    assert "known_context" in captured["schema"]["properties"]["themes"]["items"]["properties"]
    assert "知識DBの裏付け" in captured["user"] and "地政学の専門家としての追加ルール" in captured["system"]
    th = themes[0]
    assert th["contradicts_knowledge"] is True and th["leading_indicators"] == ["USTRの官報告示", "主要小売の価格改定発表"]
    row = web._row_for_web(th, "m")
    assert "既知の前提と矛盾" in row["signal_summary"] and "先行指標" in row["signal_summary"]
    assert row["business_relevance"] == ["米国向け飲料の粗利が圧迫される"]


def test_cluster_says_no_support_when_knowledge_db_has_nothing(monkeypatch):
    captured = {}
    monkeypatch.setattr(geo_llm_common, "call_llm_structured",
                        lambda c, m, sp, up, sc, sn, **kw: captured.update(user=up) or {"data": {"themes": [], "no_theme_reason": ""}})
    web.cluster_web_themes(object(), "m", "p", [], [], expert_context={"text": "", "has_support": False})
    assert "該当なし" in captured["user"]


def test_discover_web_reports_expert_support_and_lenses(monkeypatch):
    client = _kb_client(items=[_item("Donald Trump")])
    seen = {}

    def fake_llm(c, m, sp, up, sc, sn, **kw):
        if sn == "web_theme_discovery_plan":
            seen["plan_user"] = up
            return {"data": {"questions": [{"lens": "precedent", "question": "過去の関税局面"},
                                           {"lens": "leading_indicator", "question": "官報告示"}]}}
        return {"data": {"no_theme_reason": "", "themes": []}}
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake_llm)
    monkeypatch.setattr(expert_research, "run_external_research", lambda *a, **k: {
        "summary": "", "found": True, "citations": [{"title": "A", "url": "https://a.com/1"}, {"title": "B", "url": "https://b.com/2"}]})
    monkeypatch.setattr(discovery, "retrieve_articles", lambda *a, **k: [])
    out = web.discover_web(client, object(), "m", brief=BRIEF, source_ids=[], now=NOW)
    assert out["expert"]["has_support"] is True and out["expert"]["knowledge"][0]["title"] == "Donald Trump"
    assert out["expert"]["lenses"] == ["leading_indicator", "precedent"]
    assert "Donald Trump" in seen["plan_user"]
