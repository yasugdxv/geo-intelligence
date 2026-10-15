"""ナレッジ検索の自然言語検索（geo_knowledge_natural_search）のテスト。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_knowledge_natural_search as ks  # noqa: E402
import geo_llm_common  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _item(i, title, *, status="active", retrieval="available", visibility="company", ktype="actor_profile", **kw):
    return {"knowledge_item_id": f"k{i}", "knowledge_type": ktype, "title": title, "status": status,
            "retrieval_status": retrieval, "human_visibility": visibility, "is_demo": False,
            "summary": kw.pop("summary", None), "structured_content": kw.pop("sc", {"core_interests": ["関税"]}),
            "updated_at": "2026-10-01T00:00:00+00:00"}


def _client():
    return FakeSupabaseClient(tables={
        "geo_entities": [{"entity_id": "e1", "canonical_name": "Donald Trump", "name_ja": "ドナルド・トランプ",
                          "entity_type": "person", "country_code": "US", "is_demo": False},
                         {"entity_id": "e2", "canonical_name": "China", "entity_type": "country", "is_demo": False}],
        "geo_knowledge_items": [
            _item(1, "Donald Trump", sc={"core_interests": ["関税による国内産業保護"]}),
            _item(2, "候補の知識", status="candidate", retrieval="restricted"),     # 承認前
            _item(3, "社内限定", visibility="private")],                              # 閲覧権限なし
        "geo_reports": [{"report_id": "r1", "document_id": "d1", "topics": ["関税"], "country_region": ["米国"],
                         "key_actors": ["Trump"], "summary": "米国の関税政策の分析", "is_demo": False,
                         "uploaded_at": "2026-10-01T00:00:00+00:00"}],
        "geo_documents": [{"document_id": "d1", "title": "米国関税レポート"}]})


def _llm(matches):
    def fake(c, m, sp, up, schema, name, **kw):
        fake.user = up
        return {"data": {"understood": "米国の関税政策に関する資料", "matches": matches}}
    return fake


def test_natural_search_returns_entities_items_and_reports_with_reasons(monkeypatch):
    fake = _llm([{"ref": "E2", "score": 5, "reason": "関税政策の当事者"},   # 一覧は名前順: E1=China, E2=Donald Trump
                 {"ref": "K1", "score": 5, "reason": "関税を重視する利害の分析"},
                 {"ref": "R1", "score": 4, "reason": "米国の関税政策を分析したレポート"}])
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake)
    out = ks.natural_search(_client(), object(), "m", "トランプ政権の関税への姿勢が分かる資料", status=["active"])
    assert out["entities"][0]["entity"]["canonical_name"] == "Donald Trump" and out["entities"][0]["reason"]
    assert out["knowledge_items"][0]["knowledge_item"]["title"] == "Donald Trump"
    assert out["reports"][0]["report"]["document_title"] == "米国関税レポート"
    assert out["understood"] == "米国の関税政策に関する資料"
    assert "関税による国内産業保護" in fake.user and "米国関税レポート" in fake.user      # 内容がAIに渡る


def test_natural_search_never_shows_unapproved_or_private_items_to_the_ai(monkeypatch):
    fake = _llm([])
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake)
    ks.natural_search(_client(), object(), "m", "関税", status=["active", "candidate"])
    assert "候補の知識" not in fake.user and "社内限定" not in fake.user        # アクセス制御を通ったものだけ


def test_natural_search_drops_unknown_refs_duplicates_and_weak_scores(monkeypatch):
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _llm([
        {"ref": "K99", "score": 5, "reason": "存在しない"}, {"ref": "X1", "score": 5, "reason": "種類が違う"},
        {"ref": "E2", "score": 5, "reason": "ok"}, {"ref": "E2", "score": 4, "reason": "重複"},
        {"ref": "E1", "score": 2, "reason": "弱い"}, {"ref": "Kabc", "score": 5, "reason": "形式が違う"}]))
    out = ks.natural_search(_client(), object(), "m", "q", status=["active"])
    assert [e["entity"]["canonical_name"] for e in out["entities"]] == ["Donald Trump"]
    assert out["knowledge_items"] == [] and out["reports"] == []


def test_natural_search_with_nothing_to_search_does_not_call_the_ai(monkeypatch):
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", pytest.fail)
    empty = FakeSupabaseClient(tables={"geo_entities": [], "geo_knowledge_items": [], "geo_reports": [],
                                       "geo_documents": []})
    out = ks.natural_search(empty, object(), "m", "q")
    assert out["entities"] == [] and out["knowledge_items"] == [] and out["reports"] == []


def test_api_returns_503_so_the_screen_can_fall_back(monkeypatch):
    import geo_knowledge_api as api
    from fastapi import HTTPException
    monkeypatch.setattr(api, "_azure_client", object())

    def boom(*a, **k):
        raise RuntimeError("llm down")
    monkeypatch.setattr(api.geo_knowledge_natural_search, "natural_search", boom)
    anon = type("I", (), {"authenticated": False, "identity_id": None, "department": None})()
    with pytest.raises(HTTPException) as e:
        api.search_natural("q", identity=anon)
    assert e.value.status_code == 503
