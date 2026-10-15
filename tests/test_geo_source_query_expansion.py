"""Source Research: Query Expansion（日本語Query→英語記事）、Guard、matched/supplementary区別。

新しいResearch Engineは作らず既存の内部検索の前段に検索語展開を足しただけであること、
ユーザーの問い（原文）は変更しないこと、問いを広げないGuardが効くこと、選択Source制約が
維持されることを検証する。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_source_query_expansion as qe  # noqa: E402
import geo_source_research_service as research  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

JA = "中国の台湾周辺での軍事活動の変化"


def _llm(**over):
    data = {"translated_query": "Changes in Chinese military activity around Taiwan",
            "key_concepts": ["military activity", "PLA exercises"],
            "synonyms_and_names": ["People's Liberation Army", "PLA"],
            "added_topics": [], "added_regions": [], "added_actors": [], "changed_time_range": False}
    data.update(over)
    return lambda *a, **k: {"data": data}


# --- 展開レイヤー ------------------------------------------------------------------

def test_original_terms_split_japanese_by_particles_and_keep_ascii():
    t = qe.original_terms("中国の台湾周辺での軍事活動の変化 PLA")
    assert "軍事活動" in t and "PLA" in t and "台湾周辺" in t


def test_english_query_does_not_use_llm():
    called = []
    r = qe.expand_query(object(), "m", "PLA military activity around Taiwan")
    assert r["used_llm"] is False and r["translated"] is None and not called
    assert {x["kind"] for x in r["search_terms"]} == {"original"}


def test_japanese_query_without_ai_falls_back_to_original_terms_with_note():
    r = qe.expand_query(None, "m", JA)
    assert r["used_llm"] is False and r["error"] and r["search_terms"]
    assert {x["kind"] for x in r["search_terms"]} == {"original"}


def test_expansion_keeps_origin_kinds_and_preserves_original_query(monkeypatch):
    monkeypatch.setattr(qe.geo_llm_common, "call_llm_structured", _llm())
    r = qe.expand_query(object(), "m", JA)
    kinds = {x["kind"] for x in r["search_terms"]}
    assert kinds == {"original", "translated", "expanded"}
    assert r["original"] == JA                                   # 問い自体は変更しない
    assert r["translated"].startswith("Changes in Chinese military")
    assert "PLA exercises" in r["expanded_terms"] and "PLA" in r["expanded_terms"]
    assert any(x["term"] == "taiwan" and x["kind"] == "translated" for x in r["search_terms"])
    assert r["guard"]["dropped"] == []


def test_guard_drops_terms_that_change_year_region_or_are_sentences(monkeypatch):
    monkeypatch.setattr(qe.geo_llm_common, "call_llm_structured", _llm(
        key_concepts=["military activity 2027", "Japan defense policy"],
        synonyms_and_names=["PLA", "a very long expression that is really a whole sentence of words"]))
    r = qe.expand_query(object(), "m", JA)
    assert r["expanded_terms"] == ["PLA"]
    reasons = {d["term"]: d["reason"] for d in r["guard"]["dropped"]}
    assert "年・数字" in reasons["military activity 2027"]
    assert "地域" in reasons["Japan defense policy"]
    assert any("長すぎる" in v for v in reasons.values())


@pytest.mark.parametrize("flag", [{"added_regions": ["Japan"]}, {"added_actors": ["US Navy"]},
                                  {"added_topics": ["tariffs"]}, {"changed_time_range": True}])
def test_llm_self_reported_additions_discard_all_expanded_terms(monkeypatch, flag):
    monkeypatch.setattr(qe.geo_llm_common, "call_llm_structured", _llm(**flag))
    r = qe.expand_query(object(), "m", JA)
    assert r["expanded_terms"] == []                              # 展開語は全て破棄
    assert r["guard"]["dropped"] and all("自己申告" in d["reason"] for d in r["guard"]["dropped"])
    assert r["translated"]                                        # 翻訳そのものは通常のGuardを通れば残る


def test_translation_with_new_year_is_discarded(monkeypatch):
    monkeypatch.setattr(qe.geo_llm_common, "call_llm_structured", _llm(translated_query="Chinese military activity around Taiwan in 2030"))
    r = qe.expand_query(object(), "m", JA)
    assert r["translated"] is None and any("翻訳を破棄" in d["reason"] for d in r["guard"]["dropped"])


def test_llm_failure_falls_back_without_stopping_research(monkeypatch):
    def boom(*a, **k):
        raise qe.geo_llm_common.GeoLLMError("down")

    monkeypatch.setattr(qe.geo_llm_common, "call_llm_structured", boom)
    r = qe.expand_query(object(), "m", JA)
    assert r["error"] and r["used_llm"] is False and r["search_terms"]


# --- Research統合 --------------------------------------------------------------------

def _sig(i, source_id, domain, title, summary="", days_ago=1):
    from datetime import datetime, timedelta, timezone
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    return {"external_signal_id": f"s{i}", "source_id": source_id, "source_domain": domain, "source_name": domain,
            "title": title, "summary": summary, "url": f"https://{domain}/{i}", "published_at": ts, "created_at": ts}


def _client(signals):
    return FakeSupabaseClient({
        "geo_source_registry": [{"source_id": "sel", "name": "Selected", "url": "https://selected.example", "archived_at": None},
                                {"source_id": "oth", "name": "Other", "url": "https://other.example", "archived_at": None}],
        "geo_source_domains": [{"domain_id": "d1", "source_id": "sel", "domain": "selected.example",
                                "is_primary": True, "match_enabled": True}],
        "geo_external_signals": signals})


def test_japanese_query_retrieves_english_articles_via_translated_terms(monkeypatch):
    monkeypatch.setattr(qe.geo_llm_common, "call_llm_structured", _llm())
    seen_prompts = []

    def llm_router(client, model, system, user, schema, name, **kw):
        if name == "source_query_expansion":
            return _llm()(client, model, system, user, schema, name)
        seen_prompts.append(user)
        return {"data": {"answer": "回答 [1]", "used_references": [1]}}

    monkeypatch.setattr(qe.geo_llm_common, "call_llm_structured", llm_router)
    monkeypatch.setattr(research.geo_llm_common, "call_llm_structured", llm_router)
    c = _client([
        _sig(1, "sel", "selected.example", "PLA exercises around Taiwan intensify", "Chinese military activity near Taiwan rose"),
        _sig(2, "sel", "selected.example", "Gardening tips for spring", "Tomatoes"),
        _sig(3, "oth", "other.example", "PLA exercises around Taiwan, other source", "Chinese military activity"),   # 選択外
    ])
    res = research.run_source_research(c, object(), "m", theme="T", query=JA, source_ids=["sel"], use_web=False)
    assert res["query"] == JA                                              # 問い（原文）は保持
    ex = res["query_expansion"]
    assert ex["original"] == JA and ex["translated"] and {"translated", "expanded"} <= {t["kind"] for t in ex["search_terms"]}
    refs = res["references"]
    matched = [r for r in refs if r["match_type"] == "matched"]
    assert [r["external_signal_id"] for r in matched] == ["s1"]            # 英語記事が日本語Queryで取れた
    assert {t["kind"] for t in matched[0]["matched_terms"]} >= {"translated"}   # 一致した語の出自が分かる
    assert all(r["external_signal_id"] != "s3" for r in refs)              # selected Source制約: 選択外は返さない
    for r in refs:                                                         # Referenceの必須項目
        assert r["article_title"] and r["source"] and r["published_at"] and r["original_url"].startswith("https://")
    assert res["answer"] and res["answer"]["used_references"] == [1]


def test_supplementary_articles_are_marked_and_not_given_to_the_answer_llm(monkeypatch):
    prompts = []

    def llm_router(client, model, system, user, schema, name, **kw):
        if name == "source_query_expansion":
            return _llm()(client, model, system, user, schema, name)
        prompts.append(user)
        return {"data": {"answer": "回答 [1]", "used_references": [1]}}

    monkeypatch.setattr(qe.geo_llm_common, "call_llm_structured", llm_router)
    monkeypatch.setattr(research.geo_llm_common, "call_llm_structured", llm_router)
    c = _client([_sig(1, "sel", "selected.example", "PLA exercises around Taiwan intensify", "Chinese military activity"),
                 _sig(2, "sel", "selected.example", "Quarterly earnings of a bank", "profit up", days_ago=2),
                 _sig(3, "sel", "selected.example", "Football results", "goals", days_ago=3)])
    res = research.run_source_research(c, object(), "m", theme="T", query=JA, source_ids=["sel"], use_web=False)
    refs = res["references"]
    assert [r["match_type"] for r in refs] == ["matched", "supplementary", "supplementary"]   # matchedが先、補完は後
    assert refs[0]["index"] == 1 and all(r["matched_terms"] == [] for r in refs[1:])
    assert any("補完" in n and "直接関連するEvidenceではありません" in n for n in res["notes"])
    assert "Quarterly earnings" not in prompts[0] and "Football" not in prompts[0]           # 補完はLLMへ渡さない
    assert "PLA exercises" in prompts[0]


def test_no_matched_articles_means_no_answer_but_supplementary_references_are_shown(monkeypatch):
    def llm_router(client, model, system, user, schema, name, **kw):
        if name == "source_query_expansion":
            return _llm()(client, model, system, user, schema, name)
        raise AssertionError("回答LLMは呼ばれないはず")

    monkeypatch.setattr(qe.geo_llm_common, "call_llm_structured", llm_router)
    monkeypatch.setattr(research.geo_llm_common, "call_llm_structured", llm_router)
    c = _client([_sig(1, "sel", "selected.example", "Quarterly earnings of a bank", "profit up")])
    res = research.run_source_research(c, object(), "m", theme="T", query=JA, source_ids=["sel"], use_web=False)
    assert res["answer"] is None
    assert [r["match_type"] for r in res["references"]] == ["supplementary"]
    assert res["references"][0]["original_url"]                              # Referenceは残る
    assert any("直接関連する記事が見つからなかった" in n for n in res["notes"])


def test_web_search_uses_translated_query_but_keeps_original_in_context(monkeypatch):
    def llm_router(client, model, system, user, schema, name, **kw):
        if name == "source_query_expansion":
            return _llm()(client, model, system, user, schema, name)
        return {"data": {"answer": "x", "used_references": []}}

    monkeypatch.setattr(qe.geo_llm_common, "call_llm_structured", llm_router)
    captured = {}

    def fake_external(azure, model, *, question, context, search_lens):
        captured.update(question=question, context=context)
        return {"summary": "要旨", "found": True, "citations": [
            {"url": "https://selected.example/news/1", "title": "In-domain"},
            {"url": "https://evil.example/x", "title": "Out of domain"}]}

    monkeypatch.setattr(research.expert_research, "run_external_research", fake_external)
    res = research.run_source_research(_client([]), object(), "m", theme="T", query=JA, source_ids=["sel"])
    assert "Changes in Chinese military activity around Taiwan" in captured["question"]
    assert "site:selected.example" in captured["question"]
    assert JA in captured["context"]                                         # 原文の問いはcontextに保持
    assert [r["original_url"] for r in res["references"]] == ["https://selected.example/news/1"]   # 選択domain外は除外


# --- 2026-10-05 実データで見つかった緩さの回帰テスト ------------------------------------

def test_two_generic_country_names_alone_do_not_make_an_article_directly_relevant(monkeypatch):
    def llm_router(client, model, system, user, schema, name, **kw):
        if name == "source_query_expansion":
            return _llm()(client, model, system, user, schema, name)
        return {"data": {"answer": "回答 [1]", "used_references": [1]}}

    monkeypatch.setattr(qe.geo_llm_common, "call_llm_structured", llm_router)
    c = _client([
        # "china"+"taiwan"の2語だけ一致 → 軍事活動の記事ではない → supplementary
        _sig(1, "sel", "selected.example", "China invasion of Taiwan unlikely in 2027", "analyst view"),
        # 軍事活動の語まで一致 → matched
        _sig(2, "sel", "selected.example", "PLA military activities around Taiwan rise", "China sends more aircraft"),
    ])
    res = research.run_source_research(c, object(), "m", theme="T", query=JA, source_ids=["sel"], use_web=False)
    by_id = {r["external_signal_id"]: r["match_type"] for r in res["references"]}
    assert by_id == {"s2": "matched", "s1": "supplementary"}


def test_web_summary_is_labeled_as_not_article_text_for_the_answer_llm(monkeypatch):
    prompts = []

    def llm_router(client, model, system, user, schema, name, **kw):
        if name == "source_query_expansion":
            return _llm()(client, model, system, user, schema, name)
        prompts.append(user)
        return {"data": {"answer": "x [1]", "used_references": [1]}}

    monkeypatch.setattr(qe.geo_llm_common, "call_llm_structured", llm_router)
    monkeypatch.setattr(research.expert_research, "run_external_research", lambda *a, **k: {
        "summary": "Web要約", "found": True, "citations": [{"url": "https://selected.example/n/1", "title": "T"}]})
    research.run_source_research(_client([]), object(), "m", theme="T", query=JA, source_ids=["sel"])
    assert "記事本文ではない" in prompts[0]
