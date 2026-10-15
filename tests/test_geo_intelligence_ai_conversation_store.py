"""Intelligence AI: 会話のサーバー保存・英語表示のテスト。"""
import pytest

import geo_intelligence_ai_conversation_store as store
import geo_intelligence_ai_preset_i18n as i18n
import geo_intelligence_ai_preset_service as svc
from tests._fakes import FakeSupabaseClient

MSGS = [{"role": "user", "content": "こんにちは"}, {"role": "assistant", "content": "はい"}]


def put(db, owner, cid, **kw):
    return store.put_conversation(db, owner, cid, title=kw.get("title", "t"), messages=kw.get("messages", MSGS), preset=kw.get("preset"))


def test_put_list_update_and_delete_own_conversation():
    db = FakeSupabaseClient()
    put(db, "alice", "conv_aaaaaa", title="最初")
    put(db, "alice", "conv_aaaaaa", title="更新後", preset={"presetId": None, "context": {"preset_id": "past_cases"}})   # 同じIDは更新
    items = store.list_conversations(db, "alice")
    assert len(items) == 1 and items[0]["title"] == "更新後" and items[0]["preset"]["context"]["preset_id"] == "past_cases"
    store.delete_conversation(db, "alice", "conv_aaaaaa")
    assert store.list_conversations(db, "alice") == []


def test_other_users_conversations_are_invisible_and_untouchable():
    db = FakeSupabaseClient()
    put(db, "alice", "conv_aaaaaa", title="alice の会話")
    assert store.list_conversations(db, "bob") == []
    with pytest.raises(store.ConversationNotFound):
        put(db, "bob", "conv_aaaaaa", title="乗っ取り")                    # 他人のIDへ書き込めない
    with pytest.raises(store.ConversationNotFound):
        store.delete_conversation(db, "bob", "conv_aaaaaa")                # 他人の会話は消せない（存在も分からない）
    assert store.list_conversations(db, "alice")[0]["title"] == "alice の会話"


def test_anonymous_and_invalid_input_rejected():
    db = FakeSupabaseClient()
    for owner in (None, ""):
        with pytest.raises(store.ConversationError):
            store.list_conversations(db, owner)
    with pytest.raises(store.ConversationError):
        put(db, "alice", "bad id!")
    with pytest.raises(store.ConversationError):
        put(db, "alice", "conv_aaaaaa", messages="not a list")
    with pytest.raises(store.ConversationError):
        put(db, "alice", "conv_aaaaaa", messages=[{"role": "assistant", "content": "x" * (store.MAX_BYTES + 1)}])


def test_keeps_only_latest_conversations_per_user(monkeypatch):
    monkeypatch.setattr(store, "MAX_CONVERSATIONS", 3)
    db = FakeSupabaseClient()
    for i in range(5):
        store.put_conversation(db, "alice", f"conv_{i:06d}", title=str(i), messages=MSGS, preset=None, updated_at=f"2026-10-0{i+1}T00:00:00")
    store.put_conversation(db, "bob", "conv_bbbbbb", title="b", messages=MSGS, preset=None)
    ids = [c["id"] for c in store.list_conversations(db, "alice")]
    assert ids == ["conv_000004", "conv_000003", "conv_000002"]                        # 古い会話から消える
    assert len(store.list_conversations(db, "bob")) == 1                               # 他の人の会話は消えない


def test_missing_table_reported_as_not_available():
    class NoTable(FakeSupabaseClient):
        def select(self, table, params):
            raise RuntimeError("PGRST205 Could not find the table")
    with pytest.raises(store.NotAvailable, match="geo_ia_extensions"):
        store.list_conversations(NoTable(), "alice")


# ---- 英語表示 ----
def test_presets_in_english_and_japanese_unchanged():
    en = {p["id"]: p for p in svc.list_presets(None, "en")["presets"]}
    ja = {p["id"]: p for p in svc.list_presets(None, "ja")["presets"]}
    assert en["past_cases"]["title"] == "Find related past cases" and ja["past_cases"]["title"] == "過去の関連事例を探す"
    assert [f["label"] for f in en["past_cases"]["fields"]][:2] == ["Research topic", "Period"]
    assert {o["label"] for o in en["past_cases"]["fields"][1]["options"]} == {"Past 3 years", "Past 5 years", "All time"}
    assert en["find_experts"]["optional_sources"][0]["label"].startswith("Search External Network")
    assert svc.list_presets(None)["presets"][0]["title"] == "過去の関連事例を探す"                      # 既定は日本語
    for pid in svc.PRESETS:                                                                              # 翻訳の抜けが無い（項目・選択肢・見出し）
        p, t = svc.PRESETS[pid], i18n.PRESETS_EN[pid]
        assert {f["key"] for f in p["fields"]} <= set(t["fields"]), pid
        assert {k for k, _ in p["sections"]} == set(t["sections"]), pid
        assert {o["key"] for o in p["optional_sources"]} <= set(t["optional"]), pid
        for f in p["fields"]:
            if f["type"] == "select":
                assert {o["value"] for o in f["options"]} == set(t["fields"][f["key"]]["options"]), (pid, f["key"])


def test_english_messages_sections_and_output_language(monkeypatch):
    out = svc.extract_conditions(None, "m", "past_cases", "Taiwan", None, "en")
    assert "I read the following conditions." in out["assistant_message"] and "All required conditions are set" in out["assistant_message"]
    out = svc.extract_conditions(None, "m", "past_cases", "", None, "en")
    assert "I still need: Research topic" in out["assistant_message"]
    with pytest.raises(svc.PresetError, match="Required fields are missing: Research topic"):
        svc.run_preset(FakeSupabaseClient(), None, "m", identity=None, requester_context={}, preset_id="past_cases", conditions={}, lang="en")
    prompts = []

    def fake(azure, model, system, user, schema, name, max_attempts=3, **kw):
        prompts.append(user)
        if name == "geo_ia_preset_search_terms":
            return {"data": {"terms": ["Taiwan"]}}
        return {"data": {"summary": "s", "insufficient_evidence": True, "warnings": [], "sections": []}}
    monkeypatch.setattr(svc.geo_llm_common, "call_llm_structured", fake)
    monkeypatch.setattr(svc.sources.pplx, "get_settings", lambda: None)
    db = FakeSupabaseClient({"geo_external_signals": [{"external_signal_id": "s1", "title": "Taiwan news", "summary": "x", "url": None, "is_demo": False,
                                                        "published_at": "2026-10-01T00:00:00", "source_name": "n", "evidence_status": "unverified", "provider": "rss"}]})
    res = svc.run_preset(db, object(), "m", identity=None, requester_context={"user_id": None, "department": None, "is_authenticated": False},
                         preset_id="early_signals", conditions={"topic": "Taiwan"}, lang="en")
    assert "English" in prompts[-1] and [s["title"] for s in res["sections"]][0] == "Notable early signs"
    res_ja = svc.run_preset(db, object(), "m", identity=None, requester_context={"user_id": None, "department": None, "is_authenticated": False},
                            preset_id="early_signals", conditions={"topic": "Taiwan"})
    assert "English" not in prompts[-1] and res_ja["sections"][0]["title"] == "注目すべき初期兆候"
