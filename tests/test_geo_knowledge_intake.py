"""ナレッジ追加（geo_knowledge_intake_service）のテスト。実AI・実Web検索・実DBは使わない。"""
import pytest

import geo_knowledge_intake_service as svc
from tests._fakes import FakeSupabaseClient

SOURCES = [{"n": 1, "url": "https://a.example/1", "title": "A", "date": "2026-01-01"},
           {"n": 2, "url": "https://b.example/2", "title": "B", "date": None}]


def _st(text, nums, cls="observed_fact", conf="中"):
    return {"statement": text, "information_class": cls, "confidence": conf, "source_numbers": nums}


def _profile(**over):
    base = {"objectives": [_st("EUとの関係を再定義する", [1])], "core_interests": [_st("国内の支持基盤の維持", [2])],
            "red_lines": [], "constraints": [], "worldview": [], "decision_preferences": [],
            "coverage_notes": "出典が少ない", "overall_confidence": "中"}
    base.update(over)
    return base


CARD = {"name_en": "Test Leader", "name_ja": "テスト首相", "entity_type": "person", "country_code": "XX", "focus": "対EU"}


@pytest.fixture
def stubs(monkeypatch):
    monkeypatch.setattr(svc, "find_duplicates", lambda client, card: [])
    monkeypatch.setattr(svc.pplx, "search", lambda settings, **kw: {
        "summary": "要約", "citations": [{"url": s["url"], "title": s["title"], "published_at": s["date"]} for s in SOURCES]})
    calls = {}

    def fake_llm(client, model, system, user, schema, schema_name, **kw):
        calls[schema_name] = user
        if schema_name == "geo_knowledge_intake_profile":
            return {"data": calls.get("profile_override") or _profile()}
        return {"data": {"reply": "どの視点を重視しますか？", "ready": False,
                         "card": {"name_en": "Test Leader", "name_ja": None, "entity_type": "person",
                                  "country_code": None, "focus": None}}}
    monkeypatch.setattr(svc.geo_llm_common, "call_llm_structured", fake_llm)
    return calls


def test_出典番号が無い_範囲外の主張は捨てる():
    items = [_st("出典あり", [1]), _st("出典なし", []), _st("範囲外", [9]), _st("空", [1])]
    items[3]["statement"] = "  "
    kept = svc._statements_to_content(items, SOURCES)
    assert [k["statement"] for k in kept] == ["出典あり"]
    assert kept[0]["evidence_ids"] == ["https://a.example/1"]


def test_会話はカードを更新し_名前と種類が揃っていなければreadyにならない(stubs):
    out = svc.chat(object(), object(), "m", [{"role": "user", "content": "テスト首相を追加したい"}], None)
    assert out["ready"] is False and out["card"]["name_en"] == "Test Leader" and out["card"]["entity_type"] == "person"
    assert "視点" in out["reply"]


def test_会話の最後がユーザー発言でなければエラー(stubs):
    with pytest.raises(svc.IntakeError):
        svc.chat(object(), object(), "m", [{"role": "assistant", "content": "こんにちは"}], None)


def test_登録はcandidateでAI作成として残り_出典を持つ(stubs):
    client = FakeSupabaseClient({"geo_entities": [], "geo_knowledge_items": [], "geo_audit_log": []})
    stages = []
    out = svc.register_candidate(client, object(), "m", CARD, "user1", settings={}, progress=stages.append)
    item = out["item"]
    assert item["status"] == "candidate" and item["created_by"] == "ai:intake:user1"
    assert item["knowledge_type"] == "actor_profile" and item.get("approved_by") in (None, "")
    content = item["structured_content"]
    assert content["objectives"] == ["EUとの関係を再定義する"]
    assert content["intake"]["sources"][0]["url"] == "https://a.example/1"
    assert content["entity_id"] == out["entity"]["entity_id"] and out["entity_created"] is True
    assert stages == ["調べています（Web検索）", "整理しています", "登録しています"]


def test_出典に基づく内容が作れなければ登録しない(stubs):
    stubs["profile_override"] = _profile(objectives=[_st("出典なし", [])], core_interests=[])
    client = FakeSupabaseClient({"geo_entities": [], "geo_knowledge_items": [], "geo_audit_log": []})
    with pytest.raises(svc.IntakeError):
        svc.register_candidate(client, object(), "m", CARD, "user1", settings={})
    assert client.tables["geo_knowledge_items"] == [] and client.tables["geo_entities"] == []


def test_検索結果が空なら登録しない(stubs, monkeypatch):
    monkeypatch.setattr(svc.pplx, "search", lambda settings, **kw: {"summary": "x", "citations": []})
    client = FakeSupabaseClient({"geo_entities": [], "geo_knowledge_items": []})
    with pytest.raises(svc.IntakeError):
        svc.register_candidate(client, object(), "m", CARD, "user1", settings={})


def test_Actorでない種類や名前なしはジョブを始めない(monkeypatch):
    monkeypatch.setattr(svc.pplx, "get_settings", lambda: {"key": "x"})
    with pytest.raises(svc.IntakeError):
        svc.start_job(object(), object(), "m", {"name_en": "X", "entity_type": "media"}, "u")
    with pytest.raises(svc.IntakeError):
        svc.start_job(object(), object(), "m", {"entity_type": "person"}, "u")


def test_ジョブは本人にだけ見え_AI評価の結果が付く(stubs, monkeypatch):
    monkeypatch.setattr(svc.pplx, "get_settings", lambda: {"key": "x"})
    monkeypatch.setattr(svc, "review_candidate", lambda *a, **k: {"approved": True, "reason": "AI暫定承認", "reasons": []})
    client = FakeSupabaseClient({"geo_entities": [], "geo_knowledge_items": [], "geo_audit_log": []})
    job_id = svc.start_job(client, object(), "m", CARD, "user1")
    import time
    for _ in range(100):
        job = svc.get_job(job_id, "user1")
        if job["status"] != "running":
            break
        time.sleep(0.05)
    assert job["status"] == "done" and job["result"]["review"]["approved"] is True
    assert svc.get_job(job_id, "someone-else") is None


def test_AI評価に失敗しても登録は残りジョブは完了する(stubs, monkeypatch):
    monkeypatch.setattr(svc.pplx, "get_settings", lambda: {"key": "x"})

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(svc, "review_candidate", boom)
    client = FakeSupabaseClient({"geo_entities": [], "geo_knowledge_items": [], "geo_audit_log": []})
    job_id = svc.start_job(client, object(), "m", CARD, "user1")
    import time
    for _ in range(100):
        job = svc.get_job(job_id, "user1")
        if job["status"] != "running":
            break
        time.sleep(0.05)
    assert job["status"] == "done" and job["result"]["review"]["approved"] is False
    assert len(client.tables["geo_knowledge_items"]) == 1
