"""Conversation Mode: 待ち時間を減らす仕組み（ストリーミング・並列生成）のテスト。LLM・analyzeはフェイク。"""
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import geo_conversation_mode_api as api
import geo_conversation_mode_service as svc
import geo_ios_identity
from tests.test_geo_conversation_mode import ALICE, FakeLLM, Identity, fake_analyze, install_evidence_fakes, make_db  # noqa: F401
from tests.test_geo_conversation_mode import env  # noqa: F401  (fixture)

BASE = "/api/geo/conversation-mode"


class SlowLLM(FakeLLM):
    """呼び出しごとに一定時間かかるLLM（待ち時間の短縮を、並列かどうかで確かめる）。"""

    def __init__(self, delay):
        super().__init__()
        self.delay = delay

    def __call__(self, *a, **kw):
        time.sleep(self.delay)
        return super().__call__(*a, **kw)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GEO_CONVERSATION_MODE_ENABLED", "true")
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
    c.llm, c.db = llm, db
    return c


def events(resp):
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


def test_start_stream_emits_progress_then_utterances_then_done(client):
    ev = events(client.post(f"{BASE}/start/stream", json={"mode": "gal_oyaji", "theme": "テーマ"}))
    types = [e["type"] for e in ev]
    assert types[0] == "status" and types[-1] == "done"
    assert types.count("utterance") == 2 and types.index("utterance") < types.index("done")   # できた発言を、完了より先に出す
    assert [e["speaker"] for e in ev if e["type"] == "speaker"][0] == "みか"
    done = ev[-1]["result"]
    streamed = [e["message"] for e in ev if e["type"] == "utterance"]
    assert [m["content"] for m in streamed] == [m["content"] for m in done["messages"]]       # 最終結果と同じ内容・同じ順
    assert done["persistence"]["status"] == "saved" and done["state"]["budget"]["llm_calls"] == 2


def test_turn_stream_matches_normal_turn_result(client):
    start = client.post(f"{BASE}/start", json={"mode": "consult_partner", "theme": "テーマ"}).json()
    ev = events(client.post(f"{BASE}/turn/stream", json={
        "state": start["state"], "messages": start["messages"], "action": "ask", "chip": "reverse"}))
    assert ev[-1]["type"] == "done" and [e["type"] for e in ev].count("utterance") == 2
    assert [e["speaker"] for e in ev if e["type"] == "speaker"] == ["コンサル", "東条さん"]
    assert ev[-1]["result"]["state"]["rev"] == start["state"]["rev"] + 1


def test_stream_errors_are_events_with_the_same_status_codes(client):
    start = client.post(f"{BASE}/start", json={"mode": "gal_oyaji", "theme": "テーマ"}).json()
    body = {"state": start["state"], "messages": start["messages"], "action": "continue"}
    with svc._ConversationLock(start["state"]["conversation_id"]):                           # 処理中 → 409
        err = events(client.post(f"{BASE}/turn/stream", json=body))[-1]
    assert err == {"type": "error", "status": 409, "detail": err["detail"]} and "処理中" in err["detail"]
    over = json.loads(json.dumps(body))
    over["state"]["budget"]["llm_calls"] = 999
    svc._server_calls[start["state"]["conversation_id"]] = 999
    assert events(client.post(f"{BASE}/turn/stream", json=over))[-1]["status"] == 429
    client.llm.override = lambda s, u, n: (_ for _ in ()).throw(svc.geo_llm_common.GeoLLMError("secret"))
    err = events(client.post(f"{BASE}/start/stream", json={"mode": "gal_oyaji", "theme": "別"}))[-1]
    assert err["status"] == 502 and "もう一度" in err["detail"] and "secret" not in json.dumps(err)
    bad = events(client.post(f"{BASE}/start/stream", json={"mode": "nope", "theme": "x"}))[-1]
    assert bad["status"] == 400


def test_stream_prechecks_return_http_errors(client, monkeypatch):
    monkeypatch.setattr(api, "_azure_client", None)
    assert client.post(f"{BASE}/start/stream", json={"mode": "gal_oyaji", "theme": "t"}).status_code == 503
    monkeypatch.setattr(api, "_azure_client", object())
    monkeypatch.delenv("GEO_CONVERSATION_MODE_ENABLED", raising=False)
    assert client.post(f"{BASE}/start/stream", json={"mode": "gal_oyaji", "theme": "t"}).status_code == 404
    assert client.post(f"{BASE}/turn/stream", json={"state": {}, "messages": [], "action": "continue"}).status_code == 404


# ---- 並列生成 ----
def timed_start(monkeypatch, mode, delay=0.5):
    llm = SlowLLM(delay)
    monkeypatch.setenv("GEO_CONVERSATION_MODE_ENABLED", "true")
    monkeypatch.setattr(svc.geo_llm_common, "call_llm_structured", llm)

    install_evidence_fakes(monkeypatch)
    svc._server_calls.clear()
    t0 = time.monotonic()
    res = svc.start(make_db(), object(), "m", identity=Identity(False, None), requester_context=ALICE, mode=mode, theme="ホルムズ海峡")
    return res, time.monotonic() - t0, llm


def test_start_has_no_ai_call_for_evidence_so_first_utterance_comes_right_after_the_search(monkeypatch):
    res, elapsed, llm = timed_start(monkeypatch, "gal_oyaji")
    assert [m["speaker"] for m in res["messages"]] == ["みか", "中曽根さん"]
    assert len(llm.calls) == 2                                   # 開始のAI呼び出しは発言の2回だけ（以前は、分析のAI呼び出しが加わっていた）
    assert elapsed < 1.4                                         # 発言2回（各0.5秒）+ 余裕。直列で分析も呼ぶなら、さらに長くなる
    assert "資料1の抜粋" in llm.calls[0]["user"]                  # 最初の発言から、取得した資料を読んで話せる


def test_stakeholder_opening_statements_are_generated_in_parallel_but_shown_in_order(monkeypatch):
    res, elapsed, llm = timed_start(monkeypatch, "stakeholder")
    names = [p["name"] for p in res["state"]["stakeholders"]]
    assert [m["speaker"] for m in res["messages"]] == names                         # 表示は、主体の順のまま
    assert 2 <= len(res["messages"]) <= 4
    assert elapsed < 0.5 + 0.5 * 2 + 0.3                                            # 主体整理0.5 + 冒頭（並列）0.5 + 余裕。直列なら 0.5 + 0.5×人数
    assert res["state"]["budget"]["llm_calls"] == 1 + 2 * len(names)                # 主体の選定1 + 主体ごとの整理N + 冒頭の発言N。回数は並列でも先に数える


def test_parallel_openings_respect_the_call_budget(monkeypatch, env):
    llm, db = env
    st = svc._new_state(conversation_id="cm_budget_000001", mode="stakeholder", theme="t", theme_source="free", carry=None)
    st["stakeholders"] = [{"id": f"actor{i}", "name": f"主体{i}", "grounding": "ai_inference", "ref_ids": [], "goals": "g", "interests": "i",
                           "constraints": "c", "known_policies": "p", "relations": "r", "negotiation_position": "n", "flip_conditions": "f"}
                          for i in range(3)]
    st["budget"]["llm_calls"] = svc.MAX_LLM_CALLS_PER_CONVERSATION - 2          # 残り2回では、3人分は生成できない
    with pytest.raises(svc.BudgetExceeded):
        svc._generate_parallel(object(), "m", st, [], svc.plan_opening(st))
    assert len(llm.calls) == 0                                                  # 上限を超える呼び出しは1回も行われない


def test_emit_failure_does_not_break_the_conversation(env):
    llm, db = env

    def broken(_event):
        raise RuntimeError("client disconnected")
    res = svc.start(db, object(), "m", identity=Identity(), requester_context=ALICE, mode="consult_partner", theme="t", emit=broken)
    assert len(res["messages"]) == 2


def test_utterances_are_clipped_per_role_to_keep_the_chat_short(env):
    """モデルが目安を超えて長く書いても、役ごとの上限で、文の区切りで切る（LINEのように短く保つ）。"""
    llm, db = env
    long_text = "これは長い発言の一文です。" * 40                      # 約480字
    llm.override = lambda s, u, n: {"text": long_text, "basis": "unknown", "ref_ids": [], "caveat": None} if n == "conversation_mode_utterance" else None
    res = svc.start(db, object(), "m", identity=Identity(False, None), requester_context=ALICE, mode="gal_oyaji", theme="ホルムズ海峡")
    gal, oyaji = res["messages"]
    assert len(gal["content"]) <= svc.ROLE_CHAR_LIMIT["gal"] and len(oyaji["content"]) <= svc.ROLE_CHAR_LIMIT["oyaji"]
    assert gal["content"].endswith("。")                              # 文の途中では切らない
    summary = svc.turn(db, object(), "m", identity=Identity(False, None), requester_context=ALICE, state=res["state"],
                       messages=res["messages"], action="summarize")["new_messages"][0]
    assert len(summary["content"]) > svc.ROLE_CHAR_LIMIT["oyaji"]      # まとめは、長くてよい（上限は別）
