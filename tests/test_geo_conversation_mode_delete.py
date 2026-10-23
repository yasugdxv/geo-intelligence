"""Conversation Mode: 会話削除（サーバー側）のテスト。
削除は既存の DELETE /api/geo/intelligence-ai/conversations/{id}（本人確認つき）を再利用する。
ここでは、本人の会話だけが削除でき、他人の会話IDでは何も分からず、保存先が使えないときに成功を装わないことを確認する。"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import geo_conversation_mode_service as svc
import geo_intelligence_ai_conversation_store as store
import geo_intelligence_ai_preset_api as preset_api
import geo_ios_identity
from tests._fakes import FakeSupabaseClient

MSGS = [{"role": "user", "content": "こんにちは"}]
CM_PRESET = {"kind": "conversation_mode", "mode": "gal_oyaji", "theme": "t", "state": {"mode": "gal_oyaji"}}
BASE = "/api/geo/intelligence-ai/conversations"


class Who:
    def __init__(self, identity_id):
        self.identity_id, self.authenticated, self.department = identity_id, True, "Sales"


def seed(db):
    store.put_conversation(db, "alice", "cm_alice_gal_0001", title="alice gal", messages=MSGS, preset=CM_PRESET)
    store.put_conversation(db, "alice", "cm_alice_gal_0002", title="alice gal2", messages=MSGS, preset=CM_PRESET)
    store.put_conversation(db, "alice", "cm_alice_stk_0001", title="alice stakeholder", messages=MSGS,
                           preset={**CM_PRESET, "mode": "stakeholder"})
    store.put_conversation(db, "alice", "geo_conv_1760000000000_abc123", title="alice 通常チャット", messages=MSGS, preset=None)
    store.put_conversation(db, "bob", "cm_bob_gal_000001", title="bob gal", messages=MSGS, preset=CM_PRESET)


def ids(db, owner):
    return sorted(i["id"] for i in store.list_conversations(db, owner))


@pytest.fixture
def api(monkeypatch):
    monkeypatch.delenv("GEO_CONVERSATION_MODE_ENABLED", raising=False)       # Flag OFFでも削除はできる（データの削除を妨げない）
    db = FakeSupabaseClient()
    seed(db)
    monkeypatch.setattr(preset_api, "_client", db)
    app = FastAPI()
    app.include_router(preset_api.geo_intelligence_ai_conversation_router)
    who = {"id": Who("alice")}
    app.dependency_overrides[geo_ios_identity.verify_ios_identity] = lambda: who["id"]
    c = TestClient(app)
    c.db, c.who, c.app_ = db, who, app
    return c


def test_owner_deletes_only_the_selected_conversation(api):
    r = api.delete(f"{BASE}/cm_alice_gal_0001")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert ids(api.db, "alice") == ["cm_alice_gal_0002", "cm_alice_stk_0001", "geo_conv_1760000000000_abc123"]
    assert ids(api.db, "bob") == ["cm_bob_gal_000001"]                      # 他人の会話は影響なし


def test_delete_works_with_feature_flag_off(api):
    assert not svc.is_enabled()
    assert api.delete(f"{BASE}/cm_alice_stk_0001").status_code == 200       # 他モードの会話も、指定した1件だけ


def test_other_users_conversation_cannot_be_deleted_and_response_leaks_nothing(api):
    other = api.delete(f"{BASE}/cm_bob_gal_000001")                         # aliceがbobの会話IDを指定
    missing = api.delete(f"{BASE}/cm_nonexistent0001")
    assert other.status_code == 404 and missing.status_code == 404
    assert other.json() == missing.json()                                   # 「無い」と「他人のもの」を区別できない
    assert ids(api.db, "bob") == ["cm_bob_gal_000001"]                      # 実際には消えていない
    assert "bob" not in other.text


def test_cannot_delete_by_pretending_to_be_other_via_client_supplied_fields(api):
    # 本人確認は認証済みIDだけ。クエリ・ヘッダーで owner を名乗っても無視される
    r = api.delete(f"{BASE}/cm_bob_gal_000001?owner_id=bob", headers={"X-Owner": "bob"})
    assert r.status_code == 404 and ids(api.db, "bob") == ["cm_bob_gal_000001"]


def test_second_delete_of_same_conversation_is_404(api):
    assert api.delete(f"{BASE}/cm_alice_gal_0002").status_code == 200
    assert api.delete(f"{BASE}/cm_alice_gal_0002").status_code == 404


def test_unauthenticated_request_is_rejected_and_nothing_is_deleted(api):
    api.app_.dependency_overrides.clear()                                    # 本物の verify_ios_identity（認証情報なし→401）
    r = api.delete(f"{BASE}/cm_alice_gal_0001")
    assert r.status_code == 401
    r2 = api.delete(f"{BASE}/cm_alice_gal_0001", headers={"X-IOS-Credential": "not-registered"})
    assert r2.status_code == 401
    assert "cm_alice_gal_0001" in ids(api.db, "alice")


def test_missing_table_returns_503_and_does_not_claim_success(api, monkeypatch):
    class NoTable(FakeSupabaseClient):
        def select(self, table, params):
            if table == "geo_intelligence_ai_conversations":
                raise RuntimeError("PGRST205 Could not find the table")
            return super().select(table, params)
    monkeypatch.setattr(preset_api, "_client", NoTable())
    r = api.delete(f"{BASE}/cm_alice_gal_0001")
    assert r.status_code == 503 and r.json().get("ok") is not True
    assert "未作成" in r.json()["detail"]


def test_db_error_returns_500_without_leaking_content(api, monkeypatch):
    class Broken(FakeSupabaseClient):
        def select(self, table, params):
            raise RuntimeError("boom: secret conversation text")
    monkeypatch.setattr(preset_api, "_client", Broken())
    r = api.delete(f"{BASE}/cm_alice_gal_0001")
    assert r.status_code == 500 and "secret conversation text" not in r.text and r.json().get("ok") is not True


def test_listing_still_shows_remaining_conversation_mode_rows_and_filtering_helpers(api):
    api.delete(f"{BASE}/cm_alice_gal_0001")
    items = store.list_conversations(api.db, "alice")
    cm = [i for i in items if svc.is_conversation_mode_id(i["id"])]
    assert sorted(i["id"] for i in cm) == ["cm_alice_gal_0002", "cm_alice_stk_0001"]
    assert [i["title"] for i in items if not svc.is_conversation_mode_id(i["id"])] == ["alice 通常チャット"]
