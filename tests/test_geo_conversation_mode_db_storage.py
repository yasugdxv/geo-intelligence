"""Conversation Mode: DB保存まわり（保存済み呼び出し数を正にする・保存APIの書き込み制限・実DB確認スクリプト）のテスト。
実DBには触れない（フェイクDB）。"""
import importlib.util
import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import geo_conversation_mode_service as svc
import geo_intelligence_ai_conversation_store as store
import geo_intelligence_ai_preset_api as preset_api
import geo_ios_identity
from tests._fakes import FakeSupabaseClient
from tests.test_geo_conversation_mode import ALICE, FakeLLM, Identity, fake_analyze, make_db, start  # noqa: F401
from tests.test_geo_conversation_mode import env  # noqa: F401  (fixture)

_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "geo_conversation_mode_db_check.py")
_spec = importlib.util.spec_from_file_location("geo_conversation_mode_db_check", _SCRIPT)
db_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(db_check)


def run_turn(db, res, action="continue", identity=None, ctx=ALICE):
    return svc.turn(db, object(), "m", identity=identity or Identity(), requester_context=ctx, state=res["state"],
                    messages=res["messages"], action=action)


# ---- 保存済みの呼び出し数を正にする ----
def test_stored_budget_survives_restart_and_client_rewind_for_logged_in_user(env):
    llm, db = env
    res = start(db)                                                      # aliceで開始→サーバーに保存される
    assert res["persistence"]["status"] == "saved"
    cid = res["state"]["conversation_id"]
    saved = store.list_conversations(db, "alice")[0]["preset"]["state"]["budget"]["llm_calls"]
    assert saved == res["state"]["budget"]["llm_calls"] == 2
    # 保存された数を上限直前にする（サーバー側の保存。クライアントが書いたものではない）
    row = db.tables["geo_intelligence_ai_conversations"][0]
    row["preset"]["state"]["budget"]["llm_calls"] = svc.MAX_LLM_CALLS_PER_CONVERSATION - 1
    svc._server_calls.clear()                                            # サーバー再起動（プロセス内の数が消える）
    # クライアントが持つ状態は、本物のDBと同じくJSONで切り離した別物にして、数を巻き戻す
    # （フェイクDBは辞書を共有するため、そのまま書き換えると保存済みの値まで変わってしまう）
    client_res = json.loads(json.dumps(res))
    client_res["state"]["budget"]["llm_calls"] = 0
    assert db.tables["geo_intelligence_ai_conversations"][0]["preset"]["state"]["budget"]["llm_calls"] == svc.MAX_LLM_CALLS_PER_CONVERSATION - 1
    with pytest.raises(svc.BudgetExceeded):                              # それでも保存済みの数で止まる
        run_turn(db, client_res, "continue")


def test_anonymous_user_still_only_has_the_in_process_estimate(env):
    llm, db = env
    anon = Identity(False, None)
    res = start(db, identity=anon)
    svc._server_calls.clear()
    client_res = json.loads(json.dumps(res))
    client_res["state"]["budget"]["llm_calls"] = 0                       # 匿名は保存されないので、巻き戻せてしまう
    assert run_turn(db, client_res, "continue", identity=anon, ctx=ALICE)["new_messages"]    # 匿名は巻き戻せる（既知の限界・§3.2）


def test_stored_budget_is_read_only_for_the_owner(env):
    llm, db = env
    res = start(db)
    bob_row_budget = svc._stored_budget(db, Identity(True, "bob"), res["state"]["conversation_id"])
    assert bob_row_budget == {}                                          # 他人の会話の使用量は読まない
    assert svc._stored_budget(db, Identity(False, None), res["state"]["conversation_id"]) == {}


def test_missing_table_does_not_stop_the_conversation(env, monkeypatch):
    llm, db = env

    class NoTable(FakeSupabaseClient):
        def select(self, table, params):
            if table == "geo_intelligence_ai_conversations":
                raise RuntimeError("PGRST205 Could not find the table")
            return super().select(table, params)
    nt = NoTable(db.tables)
    res = start(nt)
    assert res["persistence"]["reason"] == "table_missing" and run_turn(nt, res)["new_messages"]


# ---- 汎用の保存API(PUT)から、会話モードの会話を書き込ませない ----
@pytest.fixture
def conv_api(monkeypatch):
    db = FakeSupabaseClient()
    monkeypatch.setattr(preset_api, "_client", db)
    app = FastAPI()
    app.include_router(preset_api.geo_intelligence_ai_conversation_router)
    app.dependency_overrides[geo_ios_identity.verify_ios_identity] = lambda: Identity()
    c = TestClient(app)
    c.db = db
    return c


def test_generic_put_cannot_write_conversation_mode_rows(conv_api):
    forged = {"title": "t", "messages": [{"role": "user", "content": "x"}],
              "preset": {"kind": "conversation_mode", "state": {"budget": {"llm_calls": 0}}}}
    r = conv_api.put("/api/geo/intelligence-ai/conversations/cm_forged_000001", json=forged)
    assert r.status_code == 400 and "サーバー側で保存" in r.json()["detail"]
    assert store.list_conversations(conv_api.db, "alice") == []          # 何も書かれていない


def test_generic_put_still_works_for_normal_chat_and_delete_for_both(conv_api):
    body = {"title": "通常", "messages": [{"role": "user", "content": "x"}], "preset": None}
    assert conv_api.put("/api/geo/intelligence-ai/conversations/geo_conv_1760000000000_abc123", json=body).status_code == 200
    store.put_conversation(conv_api.db, "alice", "cm_server_000001", title="cm", messages=[{"role": "user", "content": "y"}], preset=None)
    assert conv_api.delete("/api/geo/intelligence-ai/conversations/cm_server_000001").status_code == 200   # 削除は会話モードのIDでもできる
    assert [i["id"] for i in store.list_conversations(conv_api.db, "alice")] == ["geo_conv_1760000000000_abc123"]


# ---- 実DB確認スクリプト（フェイクDBで手順を検証。実DBには触れない）----
def test_db_check_schema_states():
    assert db_check.check_schema(FakeSupabaseClient({"geo_intelligence_ai_conversations": []})) == {"table": "exists", "columns": "ok"}

    class NoTable(FakeSupabaseClient):
        def select(self, table, params):
            raise RuntimeError("PGRST205 Could not find the table")
    assert db_check.check_schema(NoTable())["table"] == "missing"

    class BadColumn(FakeSupabaseClient):
        def select(self, table, params):
            raise RuntimeError("400 Client Error: Bad Request column does not exist")
    assert db_check.check_schema(BadColumn())["columns"] == "mismatch"


def test_db_check_selfcheck_passes_and_leaves_no_row():
    db = FakeSupabaseClient({"geo_intelligence_ai_conversations": []})
    steps = db_check.selfcheck_write(db)
    assert steps["all_ok"] is True and steps["test_row_removed"] == "ok"
    assert db.tables["geo_intelligence_ai_conversations"] == []


def test_db_check_selfcheck_cleans_up_even_on_failure():
    class Flaky(FakeSupabaseClient):
        def update(self, table, params, patch):
            raise RuntimeError("boom")
    db = Flaky({"geo_intelligence_ai_conversations": []})
    steps = db_check.selfcheck_write(db)
    assert steps["all_ok"] is False and "error" in steps and steps["test_row_removed"] == "ok"
    assert db.tables["geo_intelligence_ai_conversations"] == []


def test_db_check_script_refuses_real_write_without_confirmation(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["x", "--write-selfcheck"])
    assert db_check.main() == 2
    assert "refused" in capsys.readouterr().out


# ---- 社内資料の検索語（長いテーマ文では既存の検索が外れるため、語に分けて一番当たるものを使う）----
def test_search_terms_split_theme_into_words_longest_first():
    terms = svc.search_terms("ホルムズ海峡・イラン情勢と海上輸送リスク")
    assert "ホルムズ海峡" in terms and "イラン情勢" in terms and "海上輸送リスク" in terms
    assert terms == sorted(terms, key=len, reverse=True) and len(terms) <= svc.MAX_SEARCH_TERMS


def test_actor_terms_drop_generic_suffix():
    assert "イラン" in svc.actor_terms("ホルムズ海峡・イラン情勢と海上輸送リスク")
    assert "ホルムズ海峡" in svc.actor_terms("ホルムズ海峡・イラン情勢と海上輸送リスク")


def test_pick_query_uses_the_term_that_hits_most_and_falls_back(monkeypatch):
    hits = {"ホルムズ海峡": 2, "イラン情勢": 0}
    monkeypatch.setattr(svc.search_service, "search",
                        lambda c, q, **kw: {"results": [{"source_type": "report", "report_id": f"{q}{i}"} for i in range(hits.get(q, 0))]})
    assert svc._pick_query(None, ["イラン情勢", "ホルムズ海峡"], {}, "全文") == ("ホルムズ海峡", 2)
    assert svc._pick_query(None, ["イラン情勢"], {}, "全文") == ("全文", 0)          # どれも当たらなければテーマ全文で試す

    def boom(c, q, **kw):
        raise RuntimeError("rpc failed")
    monkeypatch.setattr(svc.search_service, "search", boom)
    assert svc._pick_query(None, ["ホルムズ海峡"], {}, "全文") == ("全文", 0)          # 検索の失敗は0件扱いで会話を止めない


def test_build_evidence_uses_search_results_directly_without_calling_the_ai(monkeypatch):
    """根拠は、検索結果（題名・日付・抜粋）から直接つくる。分析のAI呼び出しはしない（遅く、ときどき形式不正で失敗したため）。"""
    hit = {"source_type": "report", "report_id": "r1", "title": "紅海・ホルムズ海峡リスク", "date": "2026-09-01T00:00:00",
           "snippet": "  抜粋の\n本文  ", "key_actors": ["イラン", "米国"], "review_status": "reviewed"}
    monkeypatch.setattr(svc.search_service, "search", lambda c, q, **kw: {"results": [hit] if q == "ホルムズ海峡" else []})

    def forbidden(*a, **k):
        raise AssertionError("分析のAI呼び出しはしない")
    monkeypatch.setattr(svc.analysis_service, "analyze", forbidden)
    monkeypatch.setattr(svc.geo_llm_common, "call_llm_structured", forbidden)
    ev = svc.build_evidence(None, None, "m", theme="ホルムズ海峡・イラン情勢と海上輸送リスク", question="", requester_context={})
    assert ev["query_used"] == "ホルムズ海峡" and ev["search_hits"] == 1 and ev["insufficient"] is False
    src = ev["sources"][0]
    assert src["ref_id"] == "S1" and src["title"] == "紅海・ホルムズ海峡リスク" and src["date"] == "2026-09-01"
    assert src["snippet"] == "抜粋の 本文" and src["source_key"] == {"source_type": "report", "id": "r1"}
    assert ev["key_actors"] == ["イラン", "米国"] and ev["summary"] == "" and ev["findings"] == []


def test_build_evidence_with_no_hits_is_marked_insufficient(monkeypatch):
    monkeypatch.setattr(svc.search_service, "search", lambda c, q, **kw: {"results": []})
    ev = svc.build_evidence(None, None, "m", theme="まったく資料のないテーマ", question="", requester_context={})
    assert ev["sources"] == [] and ev["insufficient"] is True and ev["search_hits"] == 0


def test_build_evidence_merges_several_terms_without_duplicates(monkeypatch):
    results = {"ホルムズ海峡": [{"source_type": "report", "report_id": "r1", "title": "A"}],
               "イラン情勢": [{"source_type": "report", "report_id": "r1", "title": "A"}, {"source_type": "report", "report_id": "r2", "title": "B"}]}
    monkeypatch.setattr(svc.search_service, "search", lambda c, q, **kw: {"results": results.get(q, [])})
    ev = svc.build_evidence(None, None, "m", theme="ホルムズ海峡・イラン情勢", question="", requester_context={})
    assert [s["source_key"]["id"] for s in ev["sources"]] == ["r2", "r1"] or [s["source_key"]["id"] for s in ev["sources"]] == ["r1", "r2"]
    assert len(ev["sources"]) == 2 and {s["ref_id"] for s in ev["sources"]} == {"S1", "S2"}


def test_db_actor_lookup_prefers_exact_name_and_adds_relationships():
    db = FakeSupabaseClient({
        "geo_entities": [{"entity_id": "e1", "canonical_name": "Iran", "name_ja": "イランの外務大臣", "entity_type": "ministry", "is_demo": False},
                         {"entity_id": "e2", "canonical_name": "Iran", "name_ja": "イラン", "entity_type": "country", "is_demo": False}],
        "geo_relationships": [{"from_entity_id": "e2", "relationship_domain": "security", "relationship_type": "rivalry", "description": "対立"}]})
    found = svc._db_actor_context(db, ["イラン", "イラン情勢と海上輸送"])
    assert [f["name"] for f in found] == ["イラン"] and found[0]["relationships"][0]["relationship_type"] == "rivalry"
