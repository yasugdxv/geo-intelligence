"""Conversation Mode: ターンごとの権限再確認（Evidence Gate）のテスト。
権限が変わったあと、クライアントに残った根拠が次のターンで「使われず・表示されない」ことを確認する。
権限判定は実物（geo_knowledge_access_policy / _resolve_selected_sources）を、フェイクDBで動かす。"""
import copy

import pytest

import geo_conversation_mode_service as svc
import geo_intelligence_ai_conversation_store as store
from tests.test_geo_conversation_mode import ALICE, ANON, Identity, make_db, start, turn  # noqa: F401
from tests.test_geo_conversation_mode import env  # noqa: F401  (fixture)

R2_CLAIM = "限定資料の抜粋"          # 限定資料(S2)の抜粋の一部


def run_turn(db, res, action="continue", ctx=ALICE, identity=None, **kw):
    return svc.turn(db, object(), "m", identity=identity or Identity(), requester_context=ctx, state=res["state"],
                    messages=res["messages"], action=action, **kw)


def started(env_, mode="gal_oyaji"):
    llm, db = env_
    res = start(db, mode)                               # alice（r1 全社公開・r2 ログイン済みのみ）で開始
    assert {s["ref_id"] for s in res["state"]["evidence"]["sources"]} == {"S1", "S2"}
    assert res["access"]["revoked_count"] == 0
    return llm, db, res


def test_no_change_keeps_evidence_and_refreshes_metadata_from_db(env):
    llm, db, res = started(env)
    db.tables["geo_documents"][0]["title"] = "資料1（改題）"
    r2 = run_turn(db, res)
    assert r2["access"] == {"checked": True, "revoked_count": 0, "revoked_refs": []}
    titles = {s["ref_id"]: s["title"] for s in r2["state"]["evidence"]["sources"]}
    assert titles["S1"] == "資料1（改題）" and "S2" in titles          # 題名は毎回、今のDBの値
    assert {s["ref_id"] for s in r2["state"]["evidence"]["sources"]} == {"S1", "S2"}
    assert not r2["state"]["evidence"].get("summary_withdrawn")


def test_permission_loss_removes_evidence_before_next_turn_uses_it(env):
    llm, db, res = started(env)
    res["messages"][1]["ref_ids"] = ["S2"]                              # 2番目の発言は、限定資料(S2)を引いている
    res["messages"][1]["content"] = "限定資料の中身についての発言"
    n_calls = len(llm.calls)
    r2 = run_turn(db, res, "ask", ctx=ANON, text="続きを教えて")           # ログアウト/権限なし（匿名）で次のターン
    assert r2["access"]["revoked_count"] == 1 and r2["access"]["revoked_refs"] == ["S2"]
    ev = r2["state"]["evidence"]
    assert [s["ref_id"] for s in ev["sources"]] == ["S1"]                # 表示される根拠から消える
    assert all(R2_CLAIM not in f["claim"] for f in ev["findings"])       # S2を引く主張も消える
    assert ev["summary"] == "" and ev["summary_withdrawn"] is True and ev["key_actors"] == []
    redacted = r2["messages"][1]
    assert redacted["content"] == svc.REDACTED_TEXT and redacted["redacted"] and redacted["ref_ids"] == []
    assert r2["state"]["history_cutoff"] > 0
    # 次のAI発言の入力（プロンプト）に、取り消された根拠・それ由来の発言が一切入らない
    new_prompts = "\n".join(c["user"] for c in llm.calls[n_calls:])
    assert new_prompts
    for forbidden in ("資料2", R2_CLAIM, "社内限定の内容", "限定資料の中身についての発言"):
        assert forbidden not in new_prompts                              # 取り消された資料の題名・抜粋は、プロンプトに入らない
    assert "続きを教えて" in new_prompts                                  # ユーザー自身の発言は使う
    assert "資料1の抜粋" in new_prompts                                  # 認可されている根拠（題名・抜粋）は引き続き使う


def test_ai_history_before_revocation_is_excluded_from_prompts(env):
    llm, db, res = started(env)
    old_texts = [m["content"] for m in res["messages"] if m["role"] == "assistant"]
    llm.override = lambda s, u, n: {"text": "新しい発言です。", "basis": "unknown", "ref_ids": [], "caveat": None}
    n_calls = len(llm.calls)
    r2 = run_turn(db, res, "ask", ctx=ANON, text="今の状況は？")
    prompts = "\n".join(c["user"] for c in llm.calls[n_calls:])
    # 開始時の発言本文は、権限変更より前のAI発言なので、今後のプロンプトに入れない
    assert all(t not in prompts or t == "新しい発言です。" for t in old_texts)
    assert "今の状況は？" in prompts
    # 変更後の新しい発言は、次のターンで通常どおり履歴に入る
    r3 = run_turn(db, r2, "continue", ctx=ANON)
    assert r3["access"]["revoked_count"] == 0
    assert "新しい発言です。" in "\n".join(c["user"] for c in llm.calls[-2:])


def test_visibility_change_in_db_is_picked_up_next_turn(env):
    llm, db, res = started(env)
    db.tables["geo_reports"][0]["human_visibility"] = "private"          # S1(r1) が、他人の個人資料に変更された
    db.tables["geo_reports"][0]["uploaded_by"] = db.tables["geo_reports"][0]["created_by"] = "bob"
    r2 = run_turn(db, res)
    assert r2["access"]["revoked_refs"] == ["S1"]
    assert [s["ref_id"] for s in r2["state"]["evidence"]["sources"]] == ["S2"]


def test_forged_or_unverifiable_sources_are_dropped(env):
    llm, db, res = started(env)
    forged = copy.deepcopy(res)
    forged["state"]["evidence"]["sources"] += [
        {"ref_id": "S9", "title": "bobの個人資料", "source_type": "report", "source_key": {"source_type": "report", "id": "r3"}},
        {"ref_id": "S8", "title": "キー無しの偽装", "source_type": "report"},
        {"ref_id": "S7", "title": "存在しない資料", "source_type": "report", "source_key": {"source_type": "report", "id": "nope"}}]
    forged["state"]["evidence"]["findings"].append({"claim": "偽装の主張", "claim_type": "x", "source_refs": ["S9"]})
    r2 = run_turn(db, forged)
    assert set(r2["access"]["revoked_refs"]) == {"S7", "S8", "S9"}
    assert {s["ref_id"] for s in r2["state"]["evidence"]["sources"]} == {"S1", "S2"}      # 他人の資料は、IDを偽装しても入らない
    assert all("偽装" not in f["claim"] for f in r2["state"]["evidence"]["findings"])
    assert "bobの個人資料" not in "\n".join(c["user"] for c in llm.calls[-2:])


def test_gate_runs_on_every_action_including_pause_resume_and_refresh(env, monkeypatch):
    llm, db, res = started(env)
    seen = []
    real = svc.authorize_evidence
    monkeypatch.setattr(svc, "authorize_evidence", lambda *a, **k: (seen.append(1), real(*a, **k))[1])
    for action, kw in (("continue", {}), ("ask", {"text": "質問です"}), ("summarize", {}), ("pause", {}), ("resume", {}), ("refresh", {})):
        res = run_turn(db, res, action, **kw)
    assert len(seen) == 6


def test_refresh_reauthorizes_without_calling_llm_and_overwrites_stored_copy(env):
    llm, db, res = started(env)
    res["messages"][1]["ref_ids"] = ["S2"]
    store.put_conversation(db, "alice", res["state"]["conversation_id"], title="t", messages=res["messages"],
                           preset={"kind": "conversation_mode", "state": res["state"]})
    n_calls = len(llm.calls)
    r2 = run_turn(db, res, "refresh", ctx=ANON)                         # 履歴を開き直した（権限が失われている）
    assert len(llm.calls) == n_calls                                     # LLMは呼ばない
    assert r2["access"]["revoked_count"] == 1 and r2["new_messages"] == []
    assert r2["persistence"]["status"] == "saved"
    stored = store.list_conversations(db, "alice")[0]
    assert stored["messages"][1]["content"] == svc.REDACTED_TEXT          # サーバーの保存コピーも伏せ字になる
    assert [s["ref_id"] for s in stored["preset"]["state"]["evidence"]["sources"]] == ["S1"]
    assert stored["preset"]["state"]["evidence"]["summary"] == ""


def test_ledger_and_stakeholder_profiles_are_withdrawn(env):
    llm, db, res = started(env, "stakeholder")
    st = res["state"]
    st["ledger"]["facts"].append({"text": "S2由来の事実", "ref_ids": ["S2"]})
    st["ledger"]["assumptions"].append({"text": "S2由来の推測", "ref_ids": ["S2"]})
    st["stakeholders"][0]["ref_ids"] = ["S2"]
    st["stakeholders"][0]["goals"] = "限定資料に書かれた目標"
    r2 = run_turn(db, res, ctx=ANON)
    led = r2["state"]["ledger"]
    assert all("S2" not in (i.get("ref_ids") or []) for k in led for i in led[k])
    p = r2["state"]["stakeholders"][0]
    assert p["goals"] == svc.WITHDRAWN_TEXT and p["grounding"] == "ai_inference" and p["ref_ids"] == []
    assert "限定資料に書かれた目標" not in "\n".join(c["user"] + c["system"] for c in llm.calls[-3:])


def test_all_sources_revoked_still_works_with_insufficient_notice(env):
    llm, db, res = started(env)
    db.tables["geo_reports"] = [dict(r, human_visibility="private", uploaded_by="bob", created_by="bob") for r in db.tables["geo_reports"]]
    r2 = run_turn(db, res)
    assert r2["state"]["evidence"]["sources"] == [] and r2["state"]["evidence"]["insufficient"] is True
    assert r2["new_messages"]                                            # 会話は続けられる（根拠不足として）
    assert "根拠が不足" in llm.calls[-1]["user"]


def test_carried_evidence_on_mode_switch_is_reauthorized(env):
    llm, db, res = started(env)
    st = res["state"]
    carry = {"facts": st["ledger"]["facts"], "open_questions": st["ledger"]["open_questions"], "evidence": st["evidence"],
             "theme": st["theme"], "parent_id": st["conversation_id"]}
    n_before = len(llm.calls)
    out = svc.start(db, object(), "m", identity=Identity(False, None), requester_context=ANON, mode="consult_partner",
                    theme=st["theme"], carry=carry)                      # 引き継ぎ先は匿名（権限が弱い）
    assert out["access"]["revoked_refs"] == ["S2"]
    assert [s["ref_id"] for s in out["state"]["evidence"]["sources"]] == ["S1"]
    assert "資料2" not in "\n".join(c["user"] for c in llm.calls[n_before:])


def test_api_returns_access_and_refresh(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import geo_conversation_mode_api as api
    import geo_ios_identity
    from tests.test_geo_conversation_mode import FakeLLM, install_evidence_fakes
    monkeypatch.setenv("GEO_CONVERSATION_MODE_ENABLED", "true")
    llm, db = FakeLLM(), make_db()
    monkeypatch.setattr(svc.geo_llm_common, "call_llm_structured", llm)
    install_evidence_fakes(monkeypatch)
    monkeypatch.setattr(api, "_client", db)
    monkeypatch.setattr(api, "_azure_client", object())
    app = FastAPI()
    app.include_router(api.geo_conversation_mode_router)
    who = {"id": Identity()}
    app.dependency_overrides[geo_ios_identity.get_optional_ios_identity] = lambda: who["id"]
    c = TestClient(app)
    body = c.post("/api/geo/conversation-mode/start", json={"mode": "gal_oyaji", "theme": "ホルムズ海峡"}).json()
    assert body["access"]["revoked_count"] == 0
    who["id"] = Identity(False, None)                                    # 次のリクエストは匿名
    r = c.post("/api/geo/conversation-mode/turn", json={"state": body["state"], "messages": body["messages"], "action": "refresh"})
    assert r.status_code == 200 and r.json()["access"]["revoked_refs"] == ["S2"]
    assert [s["ref_id"] for s in r.json()["state"]["evidence"]["sources"]] == ["S1"]
