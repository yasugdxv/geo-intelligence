"""ソースの自然言語検索（意味検索）のテスト。名称ではなく、特徴カード（説明・話題・地域・媒体）で探すこと、
AIが作れない番号を捨てること、失敗時にキーワード検索へ切り替えることを確認する。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_llm_common  # noqa: E402
import geo_source_search_service as search  # noqa: E402
import geo_source_semantic_search as sem  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _src(i, name, desc, topics="", region="", media="news", langs=None, **kw):
    return {"source_id": f"s{i}", "name": name, "url": f"https://{name.lower().replace(' ', '')}.example", "media_type": media,
            "description": desc, "thematic_scope": topics, "geographic_scope": region, "region": region or None,
            "languages": langs or ["en"], "is_primary": False, "archived_at": None, "source_type": "other", **kw}


def _client():
    return FakeSupabaseClient(tables={"geo_source_registry": [
        _src(1, "BIS Press", "【AI生成・要確認】米国商務省産業安全保障局の公式発表。", "輸出管理、エンティティリスト", "米国", "government"),
        _src(2, "Taipei Times", "台湾の英字紙。", "台湾政治、半導体産業", "台湾"),
        _src(3, "nationalsecurity.news", "極右・陰謀論寄りで信頼性が低いと評価される。", "移民、国家安全保障", "米国"),
        _src(4, "Old Archived", "古い", "", "", archived_at="2026-01-01T00:00:00+00:00")],
        "geo_source_tags": [], "geo_external_signals": []})


def _llm(matches, concepts=("半導体", "輸出規制")):
    def fake(client_, model_, sp, up, schema, name, **kw):
        fake.seen = (sp, up)
        return {"data": {"understood": "米国の半導体輸出規制の一次情報", "key_concepts": list(concepts), "matches": matches}}
    return fake


def test_semantic_search_finds_sources_by_features_not_names(monkeypatch):
    # 依頼の語（半導体・輸出規制）は、BIS Pressという名称に含まれない。特徴で当てる
    fake = _llm([{"n": 1, "score": 5, "reason": "輸出管理の公式発表を出す政府機関", "caution": ""},
                 {"n": 2, "score": 3, "reason": "台湾の半導体産業の報道", "caution": ""}])
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake)
    out = sem.semantic_search(_client(), object(), "m", "米国の半導体輸出規制の一次情報")
    assert [s["name"] for s in out["sources"]] == ["BIS Press", "Taipei Times"]
    assert out["sources"][0]["match_reason"] and out["sources"][0]["match_score"] == 5
    assert out["interpretation"]["mode"] == "semantic" and out["interpretation"]["filters"]["keywords"] == ["半導体", "輸出規制"]
    sp, up = fake.seen
    assert "輸出管理、エンティティリスト" in up and "米国商務省" in up      # 特徴カードの中身がAIに渡る
    assert "Old Archived" not in up                                      # アーカイブ済みは渡さない


def test_semantic_search_drops_unknown_numbers_duplicates_and_weak_matches(monkeypatch):
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _llm([
        {"n": 99, "score": 5, "reason": "存在しない", "caution": ""},       # 一覧に無い番号
        {"n": 1, "score": 5, "reason": "ok", "caution": ""},
        {"n": 1, "score": 4, "reason": "重複", "caution": ""},
        {"n": 2, "score": 2, "reason": "弱い", "caution": ""}]))            # score 2以下
    out = sem.semantic_search(_client(), object(), "m", "輸出規制")
    assert [s["name"] for s in out["sources"]] == ["BIS Press"]


def test_semantic_search_carries_credibility_caution_and_ai_mark(monkeypatch):
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _llm([
        {"n": 3, "score": 4, "reason": "国家安全保障を扱う", "caution": "極右・陰謀論寄りで信頼性が低い"},
        {"n": 1, "score": 5, "reason": "公式", "caution": ""}]))
    out = sem.semantic_search(_client(), object(), "m", "安全保障")
    by = {s["name"]: s for s in out["sources"]}
    assert "信頼性が低い" in by["nationalsecurity.news"]["match_caution"]
    assert "AI生成" in by["BIS Press"]["match_caution"]                  # 【AI生成・要確認】の印は注意として残す


def test_semantic_search_applies_ui_filters_before_ranking(monkeypatch):
    fake = _llm([{"n": 1, "score": 5, "reason": "x", "caution": ""}])
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake)
    sem.semantic_search(_client(), object(), "m", "q", extra_filters={"media_type": "government"})
    assert "Taipei Times" not in fake.seen[1] and "BIS Press" in fake.seen[1]


def test_search_sources_uses_semantic_first(monkeypatch):
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _llm([{"n": 1, "score": 5, "reason": "x", "caution": ""}]))
    out = search.search_sources(_client(), object(), "m", "輸出規制")
    assert out["interpretation"]["mode"] == "semantic" and out["sources"][0]["name"] == "BIS Press"


def test_search_sources_falls_back_to_keyword_search_when_semantic_fails(monkeypatch):
    def boom(*a, **k):
        raise geo_llm_common.GeoLLMError("down", attempts=[], exception_type="X")
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", boom)
    out = search.search_sources(_client(), object(), "m", "BIS")
    assert "意味検索に失敗" in (out["interpretation"].get("error") or "")     # 切り替えたことを画面に出せる
    assert any(s["name"] == "BIS Press" for s in out["sources"])             # キーワードでは当たる


def test_search_sources_without_llm_uses_keyword_search():
    out = search.search_sources(_client(), None, "m", "BIS")
    assert any(s["name"] == "BIS Press" for s in out["sources"])


def test_search_sources_semantic_has_no_second_page(monkeypatch):
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", pytest.fail)
    out = search.search_sources(_client(), object(), "m", "輸出規制", page=2)
    assert out["sources"] == [] and out["has_more"] is False
