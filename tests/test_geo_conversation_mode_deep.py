"""Conversation Mode: 会話の根拠を深くする仕組み（Web調査・Expert AIの基盤知識）のテスト。外部のWeb検索・AIの選定はフェイク。
開始は社内資料の検索だけで速く始め、Web調査・基盤知識は裏で取得して、できた時点から次の発言に取り込む。"""
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import geo_conversation_mode_api as api
import geo_conversation_mode_service as svc
import geo_ios_identity
from tests.test_geo_conversation_mode import ALICE, FakeLLM, Identity, install_evidence_fakes, make_db  # noqa: F401
from tests.test_geo_conversation_mode import env  # noqa: F401  (fixture)

THEME = "ホルムズ海峡"
WEB_SUMMARY = "Web要約の本文: 10月に商船への攻撃が再び増えた"
KI_TEXT = "先例の要約: 核合意離脱後に緊張が高まった"
FOUNDATION_TEXT = "基盤文脈の本文: 2019年にタンカー攻撃があった"


class Calls:
    def __init__(self):
        self.web = []
        self.knowledge = []
        self.foundation = []


@pytest.fixture
def deep(monkeypatch):
    """裏の取得を有効にし、Web検索・AIの選定・基盤文脈をフェイクにする。"""
    import geo_intelligence_ai_preset_sources as sources
    import geo_knowledge_natural_search as ns
    llm = FakeLLM()
    monkeypatch.setenv("GEO_CONVERSATION_MODE_ENABLED", "true")
    monkeypatch.setenv("GEO_CONVERSATION_MODE_WEB_SEARCH_ENABLED", "true")
    monkeypatch.setattr(svc.geo_llm_common, "call_llm_structured", llm)
    install_evidence_fakes(monkeypatch)
    monkeypatch.setenv("GEO_CONVERSATION_MODE_ENRICH", "true")          # install_evidence_fakes が止めたものを、このテストでは有効に戻す
    calls = Calls()
    state = {"web_delay": 0.0, "web_status": "searched"}

    def search_web(ctx):
        calls.web.append(ctx)
        time.sleep(state["web_delay"])
        if state["web_status"] != "searched":
            return {"status": state["web_status"], "reason": "no key", "items": []}
        return {"status": "searched", "reason": None, "items": [
            {"id": "web_summary", "title": "要約", "url": None, "snippet": WEB_SUMMARY},
            {"id": "https://example.com/a", "title": "記事A", "url": "https://example.com/a", "date": "2026-10-14"},
            {"id": "https://example.com/b", "title": "記事B", "url": "https://example.com/b", "date": "2026-10-13"},
            {"id": "x", "title": "URLなし", "url": None}]}
    monkeypatch.setattr(sources, "search_web", search_web)

    def natural_search(client, azure, model, text, **kw):
        calls.knowledge.append(text)
        return {"understood": text, "entities": [], "reports": [], "knowledge_items": [
            {"knowledge_item": {"knowledge_item_id": "ki1", "title": "イラン核合意離脱", "knowledge_type": "precedent", "summary": KI_TEXT},
             "score": 4, "reason": "先例として合う"}]}
    monkeypatch.setattr(ns, "natural_search", natural_search)
    monkeypatch.setattr(svc, "_gather_foundation", lambda client, theme: (calls.foundation.append(theme) or {"ref_id": "F1", "text": FOUNDATION_TEXT}))
    for cache in (svc._KNOWLEDGE_CACHE, svc._FOUNDATION_CACHE, svc._ENRICH):
        cache.clear()
    db = make_db()
    db.tables["geo_knowledge_items"] = [{
        "knowledge_item_id": "ki1", "knowledge_type": "precedent", "status": "active", "retrieval_status": "available",
        "human_visibility": "company", "agent_sharing_policy": "geopolitics_only", "is_demo": False}]
    return llm, db, calls, state


def start(db, mode="gal_oyaji", identity=None, ctx=ALICE, emit=None, **kw):
    return svc.start(db, object(), "m", identity=identity or Identity(), requester_context=ctx, mode=mode, theme=THEME,
                     theme_source=kw.pop("theme_source", "recommended"), emit=emit, **kw)


def turn(db, res, action="continue", ctx=ALICE, emit=None, **kw):
    return svc.turn(db, object(), "m", identity=Identity(), requester_context=ctx, state=res["state"],
                    messages=res["messages"], action=action, emit=emit, **kw)


def wait(res):
    svc._ENRICH[res["state"]["conversation_id"]].result(timeout=10)


# ---- 開始は速く、深い取得は裏で ----
def test_start_does_not_wait_for_deep_gathering(deep):
    llm, db, calls, state = deep
    state["web_delay"] = 0.8
    t0 = time.monotonic()
    res = start(db)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.6                                              # Web調査（0.8秒）を待たずに、開始が終わる
    ev = res["state"]["evidence"]
    assert ev["enrichment"] == "pending" and not ev.get("knowledge") and not ev["web"].get("items")
    assert len(res["messages"]) == 2                                  # 社内資料だけで、最初の発言は出る
    wait(res)


def test_enrichment_is_merged_on_next_operation_with_status_and_budget(deep):
    llm, db, calls, state = deep
    res = start(db)
    wait(res)
    events = []
    before_calls = res["state"]["budget"]["llm_calls"]
    r2 = turn(db, res, "refresh", emit=events.append)                  # AIを呼ばず、取り込みだけ
    ev = r2["state"]["evidence"]
    assert ev["enrichment"] == "done"
    assert [i["ref_id"] for i in ev["web"]["items"]] == ["W1", "W2"] and ev["web"]["summary"] == WEB_SUMMARY   # URLのない項目は除く
    assert ev["knowledge"][0]["ref_id"] == "K1" and ev["knowledge"][0]["text"] == KI_TEXT
    assert ev["foundation"] == {"ref_id": "F1", "text": FOUNDATION_TEXT}
    assert r2["state"]["budget"]["llm_calls"] == before_calls + 1 and r2["state"]["budget"]["web_calls"] == 1   # 基盤知識の選定のAI呼び出し1回
    assert any(e["type"] == "status" and "取り込みました" in e["text"] and "Web 2件" in e["text"] for e in events)
    assert len(llm.calls) == 2                                         # refresh では、発言のAIを呼ばない


def test_next_utterances_use_web_knowledge_and_foundation(deep):
    llm, db, calls, state = deep
    res = start(db)
    wait(res)
    n = len(llm.calls)
    turn(db, res, "continue")
    prompt = "\n".join(c["user"] for c in llm.calls[n:])
    for must in (WEB_SUMMARY, "未検証", "W1", "記事A", "K1", KI_TEXT, "基盤文脈 F1", FOUNDATION_TEXT):
        assert must in prompt


def test_ledger_facts_need_internal_or_approved_knowledge_refs_web_is_unverified(deep):
    llm, db, calls, state = deep
    res = start(db)
    wait(res)
    res = turn(db, res, "refresh")
    seq = iter([("evidence", ["K1"]), ("evidence", ["W1"]), ("evidence", ["WS"]), ("evidence", ["F1"])])
    llm.override = lambda s, u, n: (lambda b, r: {"text": "文。", "basis": b, "ref_ids": r, "caveat": None})(*next(seq)) \
        if n == "conversation_mode_utterance" else None
    r2 = turn(db, res, "continue")
    r3 = turn(db, r2, "continue")
    facts = [i["ref_ids"] for i in r3["state"]["ledger"]["facts"]]
    assert ["K1"] in facts and ["F1"] in facts                          # 承認済みナレッジ・基盤文脈は、事実として数える
    assert ["W1"] not in facts and ["WS"] not in facts                  # Webだけの出典は、未検証なので事実にしない
    assert any(i["ref_ids"] == ["W1"] for i in r3["state"]["ledger"]["assumptions"])


def test_invented_web_ref_is_downgraded_to_ungrounded(deep):
    llm, db, calls, state = deep
    llm.override = lambda s, u, n: {"text": "断定。", "basis": "evidence", "ref_ids": ["W9"], "caveat": None} if n == "conversation_mode_utterance" else None
    res = start(db)
    assert all(m["ungrounded"] for m in res["messages"])                # 存在しない出典は、根拠なしとして扱う
    wait(res)


# ---- Web調査の同意・送るもの ----
def test_web_consent_off_sends_nothing_externally(deep):
    llm, db, calls, state = deep
    res = start(db, with_web=False)
    wait(res)
    r2 = turn(db, res, "refresh")
    assert calls.web == [] and r2["state"]["evidence"]["web"]["status"] == "skipped"
    assert r2["state"]["evidence"]["knowledge"]                         # Expert AIの基盤知識は、外部送信ではないので使う


def test_web_consent_on_sends_only_the_search_query(deep):
    llm, db, calls, state = deep
    res = start(db, with_web=True, theme_source="free")                 # 自由入力のテーマでも、同意があれば検索する
    wait(res)
    assert calls.web == [{"web_question": THEME, "period_days": 30}]    # 送るのは検索語だけ（資料・会話は含まない）


def test_web_search_flag_off_disables_web_but_not_knowledge(deep, monkeypatch):
    llm, db, calls, state = deep
    monkeypatch.delenv("GEO_CONVERSATION_MODE_WEB_SEARCH_ENABLED", raising=False)
    res = start(db)
    wait(res)
    r2 = turn(db, res, "refresh")
    assert calls.web == [] and r2["state"]["evidence"]["web"]["status"] == "disabled" and r2["state"]["evidence"]["knowledge"]


def test_web_unavailable_is_reported_and_conversation_continues(deep):
    llm, db, calls, state = deep
    state["web_status"] = "unavailable"
    res = start(db)
    wait(res)
    r2 = turn(db, res, "continue")
    web = r2["state"]["evidence"]["web"]
    assert web["status"] == "unavailable" and web["reason"] == "no key" and web["items"] == []
    assert r2["new_messages"] and "Web調査: unavailable" in llm.calls[-1]["user"]


def test_run_web_parses_summary_and_pages_with_refs(deep):
    llm, db, calls, state = deep
    web = svc._run_web(THEME, "recommended", question="停戦交渉は？", consent=True)
    assert web["status"] == "searched" and web["summary"] == WEB_SUMMARY
    assert [i["ref_id"] for i in web["items"]] == ["W1", "W2"] and all(i["unverified"] for i in web["items"])
    assert calls.web[-1]["web_question"] == f"{THEME} 停戦交渉は？" and web["query"] == f"{THEME} 停戦交渉は？"
    assert svc._run_web(THEME, "free")["status"] == "skipped"            # 同意の指定が無い自由入力は、送らない（従来どおり）


# ---- 取得の失敗・結果の消失 ----
def test_failures_in_gathering_do_not_stop_the_conversation(deep, monkeypatch):
    import geo_knowledge_natural_search as ns
    llm, db, calls, state = deep
    monkeypatch.setattr(ns, "natural_search", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(svc, "_gather_foundation", lambda c, t: (_ for _ in ()).throw(RuntimeError("slow")))
    res = start(db)
    wait(res)
    r2 = turn(db, res, "continue")
    ev = r2["state"]["evidence"]
    assert ev["enrichment"] == "done" and {"knowledge:RuntimeError", "foundation:RuntimeError"} <= set(ev["enrichment_errors"])
    assert ev["web"]["status"] == "searched" and r2["new_messages"]    # Webは取れた。会話は続く


def test_lost_enrichment_result_is_marked_unavailable(deep):
    llm, db, calls, state = deep
    res = start(db)
    wait(res)
    svc._ENRICH.clear()                                                  # 別のプロセス・再起動で、結果が失われた
    r2 = turn(db, res, "continue")
    assert r2["state"]["evidence"]["enrichment"] == "unavailable" and r2["new_messages"]


def test_enrichment_not_started_when_disabled(deep, monkeypatch):
    llm, db, calls, state = deep
    monkeypatch.setenv("GEO_CONVERSATION_MODE_ENRICH", "false")
    res = start(db)
    assert "enrichment" not in res["state"]["evidence"] and calls.web == [] and calls.knowledge == []


def test_knowledge_selection_is_cached_per_theme(deep):
    llm, db, calls, state = deep
    wait(start(db))
    wait(start(db))
    assert len(calls.knowledge) == 1                                     # 同じテーマの2回目は、AIの選定を繰り返さない
    r = start(db)
    wait(r)
    assert turn(db, r, "refresh")["state"]["budget"]["llm_calls"] == 2   # キャッシュのときは、選定のAI呼び出しを数えない


# ---- 権限（基盤知識も、毎ターン今の権限で確かめる）----
def test_knowledge_revoked_is_dropped_and_redacted_next_turn(deep):
    llm, db, calls, state = deep
    res = start(db)
    wait(res)
    res = turn(db, res, "refresh")
    res["messages"][1].update({"ref_ids": ["K1"], "content": "先例Kを引いた発言", "basis": "evidence"})
    db.tables["geo_knowledge_items"][0].update({"human_visibility": "private", "created_by": "bob"})   # 権限が変わった
    n = len(llm.calls)
    r2 = turn(db, res, "ask", text="続きを教えて")
    assert r2["access"]["revoked_refs"] == ["K1"]
    assert r2["state"]["evidence"]["knowledge"] == []
    assert r2["messages"][1]["content"] == svc.REDACTED_TEXT
    prompts = "\n".join(c["user"] for c in llm.calls[n:])
    assert KI_TEXT not in prompts and "先例Kを引いた発言" not in prompts
    assert "W1" in prompts                                              # 公開のWebは、権限の影響を受けない


def test_unverifiable_knowledge_is_dropped(deep):
    llm, db, calls, state = deep
    res = start(db)
    wait(res)
    res = turn(db, res, "refresh")
    res["state"]["evidence"]["knowledge"].append({"ref_id": "K9", "title": "偽装", "kind": "precedent", "text": "偽",
                                                  "source_key": {"source_type": "foundation_knowledge", "id": "nope"}})
    r2 = turn(db, res, "continue")
    assert [k["ref_id"] for k in r2["state"]["evidence"]["knowledge"]] == ["K1"] and "K9" in r2["access"]["revoked_refs"]


# ---- 「Webで調べる」 ----
def test_research_action_searches_web_with_user_text_and_continues(deep):
    llm, db, calls, state = deep
    res = start(db)
    wait(res)
    res = turn(db, res, "refresh")
    calls.web.clear()
    n = len(llm.calls)
    r2 = turn(db, res, "research", text="停戦交渉の最新は？")
    assert calls.web[0]["web_question"] == f"{THEME} 停戦交渉の最新は？"       # 利用者が入力した文だけが、検索語に加わる
    # 同じページ（URL）は足さない。新しいページが返ったときだけ、出典番号を続きから振る
    assert [i["ref_id"] for i in r2["state"]["evidence"]["web"]["items"]] == ["W1", "W2"]
    assert r2["messages"][len(res["messages"])]["role"] == "user"
    assert r2["new_messages"] and "利用者が調べたい点: 停戦交渉の最新は？" in llm.calls[n]["user"]
    assert r2["state"]["budget"]["web_calls"] == 2


def test_research_without_text_uses_only_the_theme(deep):
    llm, db, calls, state = deep
    res = start(db)
    wait(res)
    calls.web.clear()
    r2 = turn(db, res, "research")
    assert calls.web[0]["web_question"] == THEME
    assert all(m["role"] == "assistant" for m in r2["new_messages"])


def test_research_requires_web_flag_and_reports_failures(deep, monkeypatch):
    llm, db, calls, state = deep
    res = start(db)
    wait(res)
    state["web_status"] = "error"
    with pytest.raises(svc.ConversationModeError, match="Web調査ができませんでした: no key"):
        turn(db, res, "research")
    monkeypatch.delenv("GEO_CONVERSATION_MODE_WEB_SEARCH_ENABLED", raising=False)
    with pytest.raises(svc.ConversationModeError, match="Web調査は、利用できません"):
        turn(db, res, "research")


def test_web_items_are_capped_and_numbering_continues(deep, monkeypatch):
    import geo_intelligence_ai_preset_sources as sources
    llm, db, calls, state = deep
    counter = {"n": 0}

    def fresh_pages(ctx):                                              # 検索のたびに、新しいページを2件返す
        counter["n"] += 1
        n = counter["n"]
        return {"status": "searched", "reason": None, "items": [
            {"id": "web_summary", "snippet": f"要約{n}"},
            {"id": f"u{n}a", "title": f"記事{n}A", "url": f"https://example.com/{n}/a", "date": "2026-10-14"},
            {"id": f"u{n}b", "title": f"記事{n}B", "url": f"https://example.com/{n}/b", "date": "2026-10-13"}]}
    monkeypatch.setattr(sources, "search_web", fresh_pages)
    res = start(db)
    wait(res)
    res = turn(db, res, "refresh")
    for _ in range(6):
        res = turn(db, res, "research")
    items = res["state"]["evidence"]["web"]["items"]
    assert len(items) == svc.MAX_WEB_ITEMS and items[-1]["ref_id"] == "W14"          # 上限を超えた古いものから消え、番号は続く
    assert res["state"]["evidence"]["web"]["summary"] == "要約7"                      # 要約は最新のもの


# ---- Expert AIの知識を、役に渡す ----
def test_consultant_and_colleague_get_explanatory_models_gal_does_not(deep):
    llm, db, calls, state = deep
    st = {"mode": "consult_partner", "stakeholders": []}
    for sid in ("consultant", "partner"):
        prompt = svc._role_for(st, sid)["prompt"]
        assert "分析の引き出し" in prompt and "EM01" in prompt           # Expert AIの説明モデルの索引
    assert "分析の引き出し" not in svc._role_for(st, "gal")["prompt"]


def test_actor_profile_from_expert_ai_is_attached_only_when_accessible(deep, monkeypatch):
    import geo_actor_intelligence_service as actors
    llm, db, calls, state = deep
    item = {"knowledge_item_id": "p1", "knowledge_type": "actor_profile", "status": "active", "retrieval_status": "available",
            "human_visibility": "company", "summary": "習近平の方針: 技術的自立を重視する", "structured_content": {"entity_id": "e1"}}
    monkeypatch.setattr(actors, "get_actor_profile_item_by_entity_id", lambda c, eid, **kw: dict(item))
    found = svc._lookup_actor(db, "イラン", ALICE)
    assert found and found["expert_ai_profile"] == "習近平の方針: 技術的自立を重視する"
    item["human_visibility"] = "private"
    item["created_by"] = "bob"
    assert "expert_ai_profile" not in svc._lookup_actor(db, "イラン", ALICE)   # アクセスできないプロファイルは、渡さない
    monkeypatch.setattr(actors, "get_actor_profile_item_by_entity_id", lambda c, eid, **kw: (_ for _ in ()).throw(RuntimeError("x")))
    assert "expert_ai_profile" not in svc._lookup_actor(db, "イラン", ALICE)   # 取得の失敗は、会議を止めない


# ---- API（ストリーミング）----
def test_stream_api_supports_research_and_reports_config(deep, monkeypatch):
    llm, db, calls, state = deep
    monkeypatch.setattr(api, "_client", db)
    monkeypatch.setattr(api, "_azure_client", object())
    app = FastAPI()
    app.include_router(api.geo_conversation_mode_router)
    app.dependency_overrides[geo_ios_identity.get_optional_ios_identity] = lambda: Identity()
    c = TestClient(app)
    cfg = c.get("/api/geo/conversation-mode/config").json()
    assert cfg["web_search_enabled"] is True and cfg["knowledge_enabled"] is True
    body = c.post("/api/geo/conversation-mode/start", json={"mode": "gal_oyaji", "theme": THEME, "theme_source": "recommended", "with_web": True}).json()
    svc._ENRICH[body["state"]["conversation_id"]].result(timeout=10)
    resp = c.post("/api/geo/conversation-mode/turn/stream", json={"state": body["state"], "messages": body["messages"], "action": "research",
                                                                  "text": "最新は？"})
    events = [json.loads(line) for line in resp.text.splitlines() if line.strip()]
    assert events[0]["type"] == "status" and "Web" in events[0]["text"] or any("Web" in e.get("text", "") for e in events if e["type"] == "status")
    assert events[-1]["type"] == "done" and events[-1]["result"]["state"]["evidence"]["web"]["status"] == "searched"


# ---- 仕上げ: 重複除外・整形・出典番号の除去 ----
def test_web_pages_are_deduplicated_by_url_across_searches(deep):
    llm, db, calls, state = deep
    res = start(db)
    wait(res)
    res = turn(db, res, "refresh")
    r2 = turn(db, res, "research")                                       # 同じ検索語 → 同じページが返る
    items = r2["state"]["evidence"]["web"]["items"]
    assert [i["ref_id"] for i in items] == ["W1", "W2"]                  # 新しいページが無いので、出典番号は増えない
    assert r2["state"]["budget"]["web_calls"] == 2 and r2["new_messages"]


def test_web_summary_is_cleaned_of_markdown_and_citation_numbers():
    raw = "## 見出し\n**重要**: 攻撃が増加[1][2]\n- 一つ目[3]\n- 二つ目"
    cleaned = svc._clean_web_summary(raw)
    assert "**" not in cleaned and "[1]" not in cleaned and "##" not in cleaned and "\n" not in cleaned
    assert "重要: 攻撃が増加" in cleaned and "・一つ目" in cleaned and "・二つ目" in cleaned


def test_knowledge_text_drops_internal_ids_and_field_names():
    raw = "f34571d3-4742-41b7-8648-5b3ee13b7720 中国共産党の一党統治体制の維持 高 actor_claim 台湾を不可分の一部と位置づけ 中 external_assessment 核心的利益"
    cleaned = svc._clean_knowledge_text(raw)
    assert "f34571d3" not in cleaned and "actor_claim" not in cleaned and "external_assessment" not in cleaned
    assert "中国共産党の一党統治体制の維持" in cleaned and "台湾を不可分の一部と位置づけ" in cleaned and "核心的利益" in cleaned


def test_duplicate_knowledge_items_are_listed_once(deep, monkeypatch):
    import geo_knowledge_natural_search as ns
    llm, db, calls, state = deep
    same = {"knowledge_item_id": "ki1", "title": "Xi Jinping", "knowledge_type": "actor_profile", "summary": "同じ内容"}
    other = {"knowledge_item_id": "ki2", "title": "Xi Jinping", "knowledge_type": "actor_profile", "summary": "同じ内容"}
    monkeypatch.setattr(ns, "natural_search", lambda *a, **k: {"knowledge_items": [
        {"knowledge_item": same, "score": 5, "reason": "r"}, {"knowledge_item": other, "score": 4, "reason": "r"}]})
    svc._KNOWLEDGE_CACHE.clear()
    items, _ = svc._gather_knowledge(db, None, "m", "別のテーマ", ALICE)
    assert [i["ref_id"] for i in items] == ["K1"]


def test_ref_numbers_are_removed_from_utterance_text(deep):
    llm, db, calls, state = deep
    res = start(db)
    wait(res)
    res = turn(db, res, "refresh")
    llm.override = lambda s, u, n: {"text": "攻撃が増えています（W1、W2、2026-10-14）。先例（K1）もあります。要約WSの通り。", "basis": "evidence",
                                    "ref_ids": ["W1", "K1"], "caveat": None} if n == "conversation_mode_utterance" else None
    r2 = turn(db, res, "continue")
    text = r2["new_messages"][0]["content"]
    for token in ("W1", "W2", "K1", "WS"):
        assert token not in text
    assert "攻撃が増えています" in text and "先例" in text and "2026-10-14" in text
    assert r2["new_messages"][0]["ref_ids"] == ["W1", "K1"]              # 出典は、チップ用に ref_ids に残る
    assert "（）" not in text and "()" not in text


def test_strip_ref_marks_only_touches_known_refs():
    out = svc._strip_ref_marks("S&P500とW3Cの話。出典（S1、K1）。", {"S1", "K1"})
    assert "S&P500" in out and "W3C" in out and "S1" not in out and "K1" not in out


# ---- 掛け合い（人格と議論のダイナミクス）----
def _state(mode, calls=0):
    return {"mode": mode, "budget": {"llm_calls": calls}, "stakeholders": []}


def test_partner_opening_must_ask_a_probing_question_not_just_agree():
    plan = svc.plan_opening(_state("consult_partner"))
    assert [p[0] for p in plan] == ["consultant", "partner"]
    assert "答えにくい質問" in plan[1][1] and "質問の型" in plan[1][1]


def test_probe_type_changes_between_turns():
    types = {svc.plan_opening(_state("consult_partner", n))[1][1] for n in range(len(svc._PROBES))}
    assert len(types) == len(svc._PROBES)


def test_consultant_must_answer_directly_and_choose_hold_revise_or_limit():
    plan = svc.plan_continue(_state("consult_partner"), "partner")
    assert [p[0] for p in plan] == ["consultant", "partner"]            # 質問→回答→次の突っ込み
    assert "直接答える" in plan[0][1] and "読みを修正" in plan[0][1] and "限界" in plan[0][1]
    assert "さらに突っ込む" in plan[1][1]
    after_consultant = svc.plan_continue(_state("consult_partner"), "consultant")
    assert [p[0] for p in after_consultant] == ["consultant", "partner"]


def test_ask_to_consultant_is_followed_by_partner_probe():
    plan = svc.plan_ask(_state("consult_partner"), None)
    assert [p[0] for p in plan] == ["consultant", "partner"] and "答えにくい質問" in plan[1][1]
    plan = svc.plan_ask(_state("consult_partner"), "partner")
    assert [p[0] for p in plan] == ["partner", "consultant"] and "直接答える" in plan[1][1]


def test_gal_oyaji_banter_alternates_and_varies():
    first = svc.plan_continue(_state("gal_oyaji", 0), "oyaji")
    assert [p[0] for p in first] == ["gal", "oyaji"]
    assert len({svc.plan_continue(_state("gal_oyaji", n), "oyaji")[0][1] for n in range(len(svc._GAL_BEATS))}) == len(svc._GAL_BEATS)
    assert [p[0] for p in svc.plan_continue(_state("gal_oyaji", 0), "gal")] == ["oyaji", "gal"]


def test_roles_set_comedy_and_probing_personalities_with_safety_limits():
    assert "ツッコ" in svc._ROLES["gal"]["prompt"] and "ネタにしたり" in svc._ROLES["gal"]["prompt"]
    assert "拗ねる" in svc._ROLES["oyaji"]["prompt"] and "本筋" in svc._ROLES["oyaji"]["prompt"]
    assert "『なるほど、同意だ』だけで終わらない" in svc._ROLES["partner"]["prompt"]
    assert "(b)" in svc._ROLES["consultant"]["prompt"] and "粘る" in svc._ROLES["consultant"]["prompt"]
