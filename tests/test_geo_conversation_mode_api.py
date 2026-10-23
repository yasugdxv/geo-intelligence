"""Conversation Mode API のテスト（Flag・認証・エラー変換・通常チャットとの分離）。LLM・analyze はフェイク。"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import geo_conversation_mode_api as api
import geo_conversation_mode_service as svc
import geo_ios_identity
import geo_intelligence_ai_conversation_store as store
from tests._fakes import FakeSupabaseClient
from tests.test_geo_conversation_mode import FakeLLM, Identity, fake_analyze, fake_search, install_evidence_fakes, make_db


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GEO_CONVERSATION_MODE_ENABLED", "true")
    monkeypatch.delenv("GEO_CONVERSATION_MODE_WEB_SEARCH_ENABLED", raising=False)
    llm = FakeLLM()
    monkeypatch.setattr(svc.geo_llm_common, "call_llm_structured", llm)
    install_evidence_fakes(monkeypatch)
    fake_analyze.calls = []
    svc._server_calls.clear()
    db = make_db()
    monkeypatch.setattr(api, "_client", db)
    monkeypatch.setattr(api, "_azure_client", object())
    app = FastAPI()
    app.include_router(api.geo_conversation_mode_router)
    app.dependency_overrides[geo_ios_identity.get_optional_ios_identity] = lambda: Identity()
    c = TestClient(app)
    c.llm, c.db, c.app_ = llm, db, app
    return c


def test_flag_off_returns_disabled_and_404(client, monkeypatch):
    monkeypatch.delenv("GEO_CONVERSATION_MODE_ENABLED", raising=False)
    assert client.get("/api/geo/conversation-mode/config").json() == {"enabled": False}
    assert client.post("/api/geo/conversation-mode/themes", json={"mode": "gal_oyaji"}).status_code == 404
    assert client.post("/api/geo/conversation-mode/start", json={"mode": "gal_oyaji", "theme": "x"}).status_code == 404
    assert client.post("/api/geo/conversation-mode/turn", json={"state": {}, "messages": [], "action": "continue"}).status_code == 404
    assert client.llm.calls == []                                 # OFFならLLMを一切呼ばない


def test_config_lists_three_modes_and_chips(client):
    cfg = client.get("/api/geo/conversation-mode/config").json()
    assert cfg["enabled"] is True and cfg["web_search_enabled"] is False
    assert [m["id"] for m in cfg["modes"]] == ["gal_oyaji", "consult_partner", "stakeholder"]
    assert [c["label"] for c in cfg["chips"]] == ["それって本当？", "根拠は？", "逆の可能性は？", "当社への影響は？"]
    assert "仮想シミュレーション" in cfg["simulation_notice"]
    assert "プロセス内" in cfg["limits"]["note"] and "概算" in cfg["limits"]["note"]    # 上限が厳密でないことを明示


def test_start_turn_roundtrip_over_http(client):
    r = client.post("/api/geo/conversation-mode/start", json={"mode": "gal_oyaji", "theme": "ホルムズ海峡"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["messages"]) == 2 and body["persistence"]["status"] == "saved"
    r2 = client.post("/api/geo/conversation-mode/turn", json={
        "state": body["state"], "messages": body["messages"], "action": "ask", "chip": "reverse"})
    assert r2.status_code == 200 and r2.json()["new_messages"]
    assert r2.json()["state"]["rev"] == body["state"]["rev"] + 1


def test_error_mapping(client):
    base = "/api/geo/conversation-mode"
    assert client.post(f"{base}/start", json={"mode": "nope", "theme": "x"}).status_code == 400
    assert client.post(f"{base}/start", json={"mode": "gal_oyaji", "theme": "  "}).status_code == 400
    body = client.post(f"{base}/start", json={"mode": "gal_oyaji", "theme": "t1"}).json()
    with svc._ConversationLock(body["state"]["conversation_id"]):          # 処理中 → 409
        r = client.post(f"{base}/turn", json={"state": body["state"], "messages": body["messages"], "action": "continue"})
        assert r.status_code == 409
    body["state"]["budget"]["llm_calls"] = 999
    svc._server_calls[body["state"]["conversation_id"]] = 999
    r = client.post(f"{base}/turn", json={"state": body["state"], "messages": body["messages"], "action": "continue"})
    assert r.status_code == 429
    client.llm.override = lambda s, u, n: (_ for _ in ()).throw(svc.geo_llm_common.GeoLLMError("bad"))
    r = client.post(f"{base}/start", json={"mode": "gal_oyaji", "theme": "t2"})
    assert r.status_code == 502 and "もう一度" in r.json()["detail"]
    assert "bad" not in r.text


def test_no_ai_engine_returns_503(client, monkeypatch):
    monkeypatch.setattr(api, "_azure_client", None)
    assert client.post("/api/geo/conversation-mode/start", json={"mode": "gal_oyaji", "theme": "t"}).status_code == 503


def test_anonymous_user_gets_local_only_and_no_server_row(client):
    client.app_.dependency_overrides[geo_ios_identity.get_optional_ios_identity] = lambda: Identity(False, None)
    body = client.post("/api/geo/conversation-mode/start", json={"mode": "consult_partner", "theme": "t"}).json()
    assert body["persistence"] == {"status": "local_only", "reason": "login_required"}
    assert store.list_conversations(client.db, "alice") == []


def test_requester_context_comes_from_identity_not_request(client, monkeypatch):
    seen = {}

    def spy(c, query, **kw):
        seen.update(kw)
        return fake_search(c, query, **kw)
    monkeypatch.setattr(svc.search_service, "search", spy)
    client.post("/api/geo/conversation-mode/start", json={
        "mode": "gal_oyaji", "theme": "ホルムズ海峡", "carry": None, "requester_context": {"department": "HACK"}})
    ctx = seen["requester_context"]
    assert "HACK" not in str(ctx) and ctx                       # クライアント自己申告は無視し、認証済みIDから作る


# ---- 通常チャット履歴との混在防止 ----
def test_conversation_mode_ids_are_distinguishable_from_normal_chat_ids():
    cid = svc.new_conversation_id()
    assert svc.is_conversation_mode_id(cid) and store._ID.match(cid)
    assert not svc.is_conversation_mode_id("geo_conv_1760000000000_abc123")     # 通常チャットのID形式 (makeGeoConversationId)
    assert not svc.is_conversation_mode_id(None)


def test_normal_chat_save_and_list_unaffected_by_conversation_mode_rows(client):
    db = client.db
    store.put_conversation(db, "alice", "geo_conv_1760000000000_abc123", title="通常チャット",
                           messages=[{"role": "user", "content": "こんにちは"}], preset=None)
    client.post("/api/geo/conversation-mode/start", json={"mode": "gal_oyaji", "theme": "t"})
    items = store.list_conversations(db, "alice")
    normal = [i for i in items if not i["id"].startswith(svc.CONVERSATION_ID_PREFIX)]
    cm = [i for i in items if i["id"].startswith(svc.CONVERSATION_ID_PREFIX)]
    assert len(normal) == 1 and normal[0]["title"] == "通常チャット" and normal[0]["preset"] is None
    assert len(cm) == 1 and cm[0]["preset"]["kind"] == "conversation_mode"
    # 通常チャットの更新・削除が会話モードの行に影響しない
    store.put_conversation(db, "alice", "geo_conv_1760000000000_abc123", title="更新", messages=[{"role": "user", "content": "a"}], preset=None)
    store.delete_conversation(db, "alice", "geo_conv_1760000000000_abc123")
    assert [i["id"] for i in store.list_conversations(db, "alice")] == [cm[0]["id"]]
