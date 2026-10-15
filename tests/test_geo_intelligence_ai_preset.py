"""Intelligence AI プリセット調査のテスト（定義・条件抽出・アダプター・根拠検証・権限・追加対話・凍結領域への非接触）。"""
from datetime import date
from pathlib import Path

import pytest

import geo_article_body_service as bodies
import geo_external_network_service as ext
import geo_intelligence_ai_preset_service as svc
import geo_intelligence_ai_preset_sources as src
from geo_ios_identity import IosIdentity
from tests._fakes import FakeSupabaseClient

HERE = Path(__file__).resolve().parent.parent


def ident(user, role="analyst"):
    return IosIdentity(identity_id=user, assigned_role=role, authenticated=True)


def rc(user=None):
    return {"user_id": user, "department": None, "is_authenticated": bool(user)}


SIGNALS = [
    {"external_signal_id": "s1", "title": "Taiwan Strait tensions rise", "summary": "PLA activity near Taiwan", "url": "https://ex.com/a",
     "source_name": "ExNews", "published_at": "2026-09-30T00:00:00", "is_demo": False, "evidence_status": "unverified", "provider": "rss"},
    {"external_signal_id": "s2", "title": "Unrelated farm news", "summary": "wheat", "url": "https://ex.com/b",
     "source_name": "ExNews", "published_at": "2026-09-30T00:00:00", "is_demo": False, "evidence_status": "unverified", "provider": "rss"},
]


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """テストでは、記事本文の取得で実ネットワークへ出ない（必要なテストだけ、個別に差し替える）。"""
    monkeypatch.setattr(bodies, "fetch_body", lambda url: {"status": "failed", "body_text": None, "char_count": 0,
                                                            "content_hash": None, "http_status": None, "error": "test"})


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(ext, "UPLOAD_ROOT", tmp_path / "up")
    monkeypatch.setattr(src.pplx, "get_settings", lambda: None)          # 既定ではWeb検索キー無し
    return FakeSupabaseClient({
        "geo_external_signals": SIGNALS,
        "geo_monitoring_alerts": [{"alert_id": "a1", "title": "Taiwan activity surge", "reason": "件数が急増", "severity": "high",
                                   "alert_type": "volume", "detected_at": "2026-10-01T00:00:00"}],
        "geo_monitoring_themes": [{"monitoring_theme_id": "m1", "display_name": "Taiwan monitoring", "monitoring_criteria": "",
                                   "latest_assessment": {"current_state": "高水準"}, "last_checked_at": "2026-10-02T00:00:00",
                                   "status": "active", "frequency": "daily"}],
        "geo_external_events": [],
        "geo_ios_identities": [{"identity_id": u, "role": "analyst", "is_active": True} for u in ("alice", "bob")],
    })


def fake_llm(monkeypatch, *, terms=("Taiwan",), analysis=None, extraction=None, plan=None, followup=None):
    calls = []

    def _call(azure, model, system, user, schema, name, max_attempts=3, **kw):
        calls.append(name)
        if name == "geo_ia_preset_search_terms":
            return {"data": {"terms": list(terms)}}
        if name == "geo_ia_preset_condition_extraction":
            return {"data": extraction or {}}
        if name == "geo_ia_preset_followup_plan":
            return {"data": plan or {"needs_new_search": False}}
        if name == "geo_ia_preset_followup":
            return {"data": followup}
        return {"data": analysis}
    monkeypatch.setattr(svc.geo_llm_common, "call_llm_structured", _call)
    return calls


# ---- 定義 ----
def test_six_presets_with_required_topic_and_sections():
    assert list(svc.PRESETS) == ["past_cases", "find_experts", "changes_since_last", "compare_views", "test_hypothesis", "early_signals"]
    for pid, p in svc.PRESETS.items():
        assert [f for f in p["fields"] if f["required"]][0]["key"] == "topic", pid
        assert p["sections"] and p["sources"] and p["instruction"]


def test_list_presets_reports_runtime_availability(db):
    out = svc.list_presets(db)
    assert len(out["presets"]) == 6
    av = out["source_availability"]
    assert av["web"]["available"] is False and "未設定" in av["web"]["reason"]
    assert av["articles"]["available"] is True
    assert av["external_network"]["available"] is True

    class NoExtTables(FakeSupabaseClient):               # 表が未作成のDB（PostgRESTは例外になる）を再現
        def select(self, table, params):
            if table.startswith("geo_external_contact"):
                raise RuntimeError("PGRST205")
            return super().select(table, params)
    av2 = svc.list_presets(NoExtTables())["source_availability"]
    assert av2["external_network"]["available"] is False and "接続できません" in av2["external_network"]["reason"]


# ---- 条件 ----
def test_normalize_defaults_and_invalid_values_dropped():
    c = svc.normalize_conditions("past_cases", {"topic": " 台湾 ", "period": "10y", "scope": "bogus", "date_from": "2022/01/01"})
    assert c["topic"] == "台湾" and c["period"] == "all" and c["scope"] == "all" and c["date_from"] is None
    assert svc.normalize_conditions("find_experts", {})["include_external_network"] is True       # 専門家検索は初期ON
    assert svc.normalize_conditions("past_cases", {})["include_external_network"] is False


def test_missing_required_and_run_blocked(db, monkeypatch):
    assert svc.missing_required("past_cases", svc.normalize_conditions("past_cases", {})) == ["topic"]
    with pytest.raises(svc.PresetError):
        svc.run_preset(db, None, "m", identity=None, requester_context=rc(), preset_id="past_cases", conditions={})
    with pytest.raises(svc.PresetError):
        svc.run_preset(db, None, "m", identity=None, requester_context=rc(), preset_id="nope", conditions={"topic": "x"})


def test_period_to_date_from():
    t = date(2026, 10, 14)
    assert svc.period_to_date_from({"period": "3y"}, t) == "2023-10-14"
    assert svc.period_to_date_from({"period": "all"}, t) is None
    assert svc.period_to_date_from({"period": "3y", "date_from": "2022-01-01"}, t) == "2022-01-01"     # 明示の日付が優先


def test_extract_conditions_understands_free_text_and_does_not_reask(monkeypatch):
    fake_llm(monkeypatch, extraction={"topic": "台湾情勢", "period": None, "date_from": "2022-01-01", "scope": "experts"})
    out = svc.extract_conditions(object(), "m", "past_cases", "2022年以降の台湾情勢について専門家レポートを探して", None)
    c = out["conditions"]
    assert (c["topic"], c["date_from"], c["scope"]) == ("台湾情勢", "2022-01-01", "experts")
    assert out["missing"] == []
    assert "調査テーマ" in out["assistant_message"] and "を教えて" not in out["assistant_message"]     # 読み取った内容を示し、再質問しない
    assert c["period"] == "all"                                                                        # 任意項目は初期値


def test_extract_keeps_existing_values_and_asks_only_missing(monkeypatch):
    fake_llm(monkeypatch, extraction={"topic": None, "period": "5y", "date_from": None, "scope": None})
    out = svc.extract_conditions(object(), "m", "past_cases", "期間は5年で", {"topic": "台湾"})
    assert out["conditions"]["topic"] == "台湾" and out["conditions"]["period"] == "5y"
    fake_llm(monkeypatch, extraction={"topic": None, "period": None, "date_from": None, "scope": None})
    out = svc.extract_conditions(object(), "m", "past_cases", "お願いします", None)
    assert out["missing"] == ["topic"] and "調査テーマ" in out["assistant_message"]


def test_extract_without_llm_uses_text_as_topic_only_when_empty():
    assert svc.extract_conditions(None, "m", "past_cases", "台湾情勢", None)["conditions"]["topic"] == "台湾情勢"
    assert svc.extract_conditions(None, "m", "past_cases", "追加", {"topic": "元"})["conditions"]["topic"] == "元"


# ---- アダプター ----
def base_ctx(db, **kw):
    return {"client": db, "identity": None, "requester_context": rc(), "terms": ["Taiwan"], "date_from": None, "region": None,
            "knowledge_group": "general", "web_question": "Taiwan の最近の動向", "period_days": None, **kw}


def test_articles_and_monitoring_adapters(db):
    r = src.run_adapter("articles", base_ctx(db))
    assert r["status"] == "searched" and [i["id"] for i in r["items"]] == ["s1"]
    assert r["items"][0]["url"] == "https://ex.com/a" and r["items"][0]["date"] == "2026-09-30"
    m = src.run_adapter("monitoring", base_ctx(db))
    assert {i["source_type"] for i in m["items"]} == {"monitoring_theme", "monitoring_alert"}
    assert src.run_adapter("articles", base_ctx(db, date_from="2026-10-05"))["items"] == []        # 期間で絞れる


def test_tokens_split_phrases_and_drop_short_words():
    assert src.tokens(["中国 経済圧力 台湾", "China economic coercion", "a"]) == ["中国", "経済圧力", "台湾", "China", "economic", "coercion"]
    assert src.tokens([""]) == [] and len(src.tokens([f"word{i}x" for i in range(20)])) == 10


def test_articles_phrase_terms_match_per_word_and_rank_by_hits(db):
    """実データで「中国 経済圧力 台湾」のようなフレーズを丸ごと部分一致させて0件になった不具合の再発防止。"""
    db.tables["geo_external_signals"] += [
        {"external_signal_id": "s3", "title": "Taiwan economic coercion by China", "summary": "tariffs and trade", "url": None,
         "source_name": "X", "published_at": "2026-09-01T00:00:00", "is_demo": False, "evidence_status": "unverified", "provider": "rss"},
        {"external_signal_id": "s4", "title": "China weather", "summary": "rain", "url": None, "source_name": "X",
         "published_at": "2026-09-29T00:00:00", "is_demo": False, "evidence_status": "unverified", "provider": "rss"}]
    r = src.search_articles(base_ctx(db, terms=["China economic coercion Taiwan", "Taiwan Strait"], tokens=None))
    ids = [i["id"] for i in r["items"]]
    assert ids[0] == "s3" and "s4" not in ids and "s2" not in ids                 # 多く一致した順。1語だけ一致の無関係記事は除く


def test_adapter_failures_are_reported_not_hidden(db, monkeypatch):
    def boom(ctx):
        raise RuntimeError("secret memo text should not leak")
    monkeypatch.setitem(src.ADAPTERS, "articles", boom)
    r = src.run_adapter("articles", base_ctx(db))
    assert r["status"] == "error" and "secret" not in r["reason"] and r["items"] == []

    def missing(ctx):
        raise RuntimeError("PGRST205 Could not find the table")
    monkeypatch.setitem(src.ADAPTERS, "articles", missing)
    assert src.run_adapter("articles", base_ctx(db))["status"] == "unavailable"


def test_web_unavailable_without_key_and_sends_topic_only(db, monkeypatch):
    assert src.run_adapter("web", base_ctx(db))["status"] == "unavailable"
    seen = {}
    monkeypatch.setattr(src.pplx, "get_settings", lambda: {"key": "k", "proxies": None, "verify": True})
    monkeypatch.setattr(src.pplx, "search", lambda s, **kw: seen.update(kw) or {
        "summary": "要約", "found": True, "citations": [{"url": "https://w.example/x", "title": "T", "published_at": "2026-10-01"}]})
    r = src.run_adapter("web", base_ctx(db))
    assert r["status"] == "searched" and all(i["origin"] == "web" for i in r["items"])
    summary, page = r["items"][0], r["items"][1]
    assert summary["meta"]["web_summary"] is True and summary["url"] is None        # 要約は、特定ページの記述とは別の根拠
    assert page["url"] == "https://w.example/x" and page["snippet"] is None
    assert seen["question"] == "Taiwan の最近の動向"                          # 送るのはテーマ文言のみ


def test_external_network_adapter_respects_permissions(db):
    c = ext.create_contact(db, ident("alice"), name="田中 太郎", affiliation_title="X大学")
    ext.create_log(db, ident("alice"), contact_ids=[c["contact_id"]], memo="台湾の半導体規制について議論した", participant_ids=["alice"])
    assert src.run_adapter("external_network", base_ctx(db))["status"] == "no_access"                       # 未ログイン
    mine = src.run_adapter("external_network", base_ctx(db, identity=ident("alice"), terms=["台湾"]))
    assert mine["status"] == "searched" and {i["source_type"] for i in mine["items"]} == {"contact_log"}
    other = src.run_adapter("external_network", base_ctx(db, identity=ident("bob"), terms=["台湾"]))
    assert other["items"] == []                                                                              # 権限のない人には出ない
    viewer = src.run_adapter("external_network", base_ctx(db, identity=ident("bob", "viewer"), terms=["台湾"]))
    assert viewer["status"] == "no_access"
    named = src.run_adapter("external_network", base_ctx(db, identity=ident("alice"), terms=["田中"]))
    assert any(i["source_type"] == "contact" and i["meta"]["registered"] for i in named["items"])


def test_knowledge_adapter_filters_by_permission_before_and_after_search(db):
    def row(i, vis, owner="alice"):
        return {"knowledge_item_id": i, "knowledge_type": "precedent", "status": "active", "retrieval_status": "available",
                "human_visibility": vis, "agent_sharing_policy": "geopolitics_only", "created_by": owner, "is_demo": False,
                "title": f"事例{i}", "summary": "要約", "created_at": "2026-01-01T00:00:00", "structured_content": {}}
    db.tables["geo_knowledge_items"] = [row("k_pub", "company"), row("k_priv", "private", owner="alice")]
    # RPCは（実装ミスを想定して）eligible外のIDも返す。二重チェックで落ちること
    db.rpc_responses = {"search_geo_knowledge_items": [
        {"knowledge_item_id": "k_pub", "text_score": 0.5, "snippet": "s"}, {"knowledge_item_id": "k_priv", "text_score": 0.9, "snippet": "secret"}]}
    seen = src.search_knowledge(base_ctx(db, requester_context=rc("bob")))
    assert [i["id"] for i in seen["items"]] == ["k_pub"]                    # 他人のprivateは検索にも根拠にも出ない
    own = src.search_knowledge(base_ctx(db, requester_context=rc("alice")))
    assert {i["id"] for i in own["items"]} == {"k_pub", "k_priv"}
    anon = src.search_knowledge(base_ctx(db, requester_context=rc()))
    assert "k_priv" not in {i["id"] for i in anon["items"]}


# ---- 調査の実行 ----
ANALYSIS = {"summary": "要約", "insufficient_evidence": False, "warnings": [], "sections": [
    {"key": "early_signs", "items": [
        {"text": "台湾周辺で活動が増えている", "claim_type": "source_supported", "refs": ["E1"]},
        {"text": "存在しない資料に基づく主張", "claim_type": "source_supported", "refs": ["E99"]},
        {"text": "AIの推論", "claim_type": "ai_inference", "refs": []}]},
    {"key": "observed_facts", "items": []}, {"key": "possible_changes", "items": []}, {"key": "withheld", "items": []},
    {"key": "watchpoints", "items": []}]}


def test_run_preset_validates_refs_and_reports_source_status(db, monkeypatch):
    calls = fake_llm(monkeypatch, analysis=ANALYSIS)
    out = svc.run_preset(db, object(), "m", identity=ident("alice"), requester_context=rc("alice"), preset_id="early_signals",
                         conditions={"topic": "台湾周辺の軍事活動", "include_web": True})
    sec = {s["key"]: s["items"] for s in out["sections"]}
    assert [i["text"] for i in sec["early_signs"]] == ["台湾周辺で活動が増えている", "AIの推論"]     # 存在しない参照の主張は落ちる
    assert any("除外" in w for w in out["warnings"])
    assert {c["ref_id"] for c in out["citations"]} == {"E1"} or out["citations"]                    # 使った根拠だけが出典になる
    status = {s["source"]: s for s in out["source_status"]}
    assert status["articles"]["status"] == "searched" and status["articles"]["count"] == 1
    assert status["web"]["status"] == "unavailable"                                                   # キー無し＝検索していない
    assert status["external_network"]["status"] == "skipped"
    assert status["expert_ai"]["status"] == "not_used"                                                # 凍結した専門家AIは呼んでいない
    assert "geo_ia_preset_early_signals" in calls


def test_run_preset_without_any_evidence_skips_llm(db, monkeypatch):
    calls = fake_llm(monkeypatch, terms=("zzzz",), analysis=ANALYSIS)
    out = svc.run_preset(db, object(), "m", identity=None, requester_context=rc(), preset_id="early_signals", conditions={"topic": "存在しない話題"})
    assert out["insufficient_evidence"] is True and out["sections"] == [] and "geo_ia_preset_early_signals" not in calls
    assert "見つかりませんでした" in out["summary"]


def test_run_preset_without_azure_returns_search_only_notice(db):
    out = svc.run_preset(db, None, "m", identity=None, requester_context=rc(), preset_id="early_signals", conditions={"topic": "Taiwan"})
    assert out["insufficient_evidence"] is True and "azure_openai_not_configured" in out["warnings"] and out["citations"]


def test_changes_preset_states_baseline_assumption(db, monkeypatch):
    fake_llm(monkeypatch, analysis={"summary": "", "insufficient_evidence": True, "warnings": [], "sections": []})
    out = svc.run_preset(db, object(), "m", identity=None, requester_context=rc(), preset_id="changes_since_last", conditions={"topic": "Taiwan"})
    assert out["assumptions"] and "基準時点" in out["assumptions"][0]
    out = svc.run_preset(db, object(), "m", identity=None, requester_context=rc(), preset_id="changes_since_last",
                         conditions={"topic": "Taiwan", "baseline_date": "2026-06-01"})
    assert "2026-06-01" in out["assumptions"][0] and "ユーザー指定" in out["assumptions"][0]


def test_scope_reports_limits_adapters(db, monkeypatch):
    fake_llm(monkeypatch, analysis={"summary": "", "insufficient_evidence": True, "warnings": [], "sections": []})
    out = svc.run_preset(db, None, "m", identity=None, requester_context=rc(), preset_id="past_cases",
                         conditions={"topic": "Taiwan", "scope": "reports"})
    assert {s["source"] for s in out["source_status"] if s["status"] == "searched"} == {"reports"}


# ---- 追加対話 ----
FOLLOW = {"answer": "最も参考になるのは…", "insufficient_evidence": False, "warnings": [],
          "claims": [{"text": "活動が増加", "claim_type": "source_supported", "refs": ["E1"]},
                     {"text": "架空", "claim_type": "source_supported", "refs": ["E50"]}]}


def test_followup_uses_prior_refs_and_keeps_numbering(db, monkeypatch):
    fake_llm(monkeypatch, analysis=ANALYSIS)
    first = svc.run_preset(db, object(), "m", identity=ident("alice"), requester_context=rc("alice"), preset_id="early_signals",
                           conditions={"topic": "台湾周辺の軍事活動"})
    ctx = first["context"]
    s1_ref = next(e["ref_id"] for e in ctx["evidence"] if e["id"] == "s1")
    assert ctx["next_ref"] > 1 and all("snippet" not in e for e in ctx["evidence"])                  # 本文は保持しない
    follow = {**FOLLOW, "claims": [{**FOLLOW["claims"][0], "refs": [s1_ref]}, FOLLOW["claims"][1]]}
    calls = fake_llm(monkeypatch, followup=follow)
    out = svc.followup(db, object(), "m", identity=ident("alice"), requester_context=rc("alice"), message="この中で最も参考になるのは？",
                       context=ctx, history=[{"role": "user", "content": "x"}])
    assert out["answer"].startswith("最も参考") and [c["text"] for c in out["claims"]] == ["活動が増加"]
    assert out["citations"][0]["ref_id"] == s1_ref and out["citations"][0]["id"] == "s1" and out["new_search"] is False
    assert "geo_ia_preset_followup" in calls and any("除外" in w for w in out["warnings"])


def test_followup_can_search_again_with_patched_conditions(db, monkeypatch):
    fake_llm(monkeypatch, analysis=ANALYSIS)
    ctx = svc.run_preset(db, object(), "m", identity=None, requester_context=rc(), preset_id="early_signals", conditions={"topic": "Taiwan"})["context"]
    seen_terms = []
    monkeypatch.setattr(svc.sources, "ADAPTERS", {**src.ADAPTERS, "articles": lambda c: seen_terms.append(list(c["terms"])) or {
        "status": "searched", "reason": None, "items": [{"source_type": "article", "id": "s9", "origin": "internal", "title": "New", "date": "2026-10-10",
                                                          "url": None, "snippet": "x", "meta": {}}]}})
    fake_llm(monkeypatch, plan={"needs_new_search": True, "extra_terms": ["Japan", "日本"]},
             followup={"answer": "広げた結果", "insufficient_evidence": False, "warnings": [],
                       "claims": [{"text": "新しい記事", "claim_type": "source_supported", "refs": [f"E{ctx['next_ref']}"]}]})
    out = svc.followup(db, object(), "m", identity=None, requester_context=rc(), message="もう少し対象地域を広げて", context=ctx, history=[])
    assert out["new_search"] is True and out["citations"][0]["id"] == "s9"
    assert "Japan" in seen_terms[-1] and "日本" in seen_terms[-1] and "Taiwan" in seen_terms[-1]       # 元の語に、広げる語を足して検索
    assert out["context"]["next_ref"] == ctx["next_ref"] + 1 and any(e["id"] == "s9" for e in out["context"]["evidence"])


def test_followup_drops_refs_the_user_can_no_longer_access(db, monkeypatch):
    c = ext.create_contact(db, ident("alice"), name="田中")
    log = ext.create_log(db, ident("alice"), contact_ids=[c["contact_id"]], memo="機密性のある面談メモです。台湾について。", participant_ids=["alice"])
    ctx = {"preset_id": "compare_views", "conditions": {"topic": "台湾"}, "next_ref": 2, "web_evidence": [],
           "evidence": [{"ref_id": "E1", "source_type": "contact_log", "id": log["log_id"]}]}
    fake_llm(monkeypatch, followup={"answer": "a", "insufficient_evidence": True, "warnings": [], "claims": []})
    out = svc.followup(db, object(), "m", identity=ident("bob"), requester_context=rc("bob"), message="詳しく", context=ctx, history=[])
    assert out["citations"] == [] and "権限" in out["answer"]            # bobには見えない資料は、追加対話にも漏れない（LLMにも渡さない）


def test_followup_requires_context_and_llm(db):
    with pytest.raises(svc.PresetError):
        svc.followup(db, object(), "m", identity=None, requester_context=rc(), message="x", context={}, history=[])
    with pytest.raises(svc.PresetError):
        svc.followup(db, None, "m", identity=None, requester_context=rc(), message="x", context={"preset_id": "past_cases"}, history=[])


# ---- 凍結領域・既存機能への非接触 ----
def test_new_modules_do_not_touch_frozen_expert_ai():
    for name in ("geo_intelligence_ai_preset_service.py", "geo_intelligence_ai_preset_sources.py", "geo_intelligence_ai_preset_api.py"):
        text = (HERE / name).read_text(encoding="utf-8")
        for banned in ("geo_expert_engine", "geo_expert_session", "geo_expert_schema", "run_expert_request", "geo_expert_responses"):
            assert banned not in text, (name, banned)
        assert "geo_external_contact" not in text                          # 外部ネットワークの表は、専用サービス経由でのみ読む


# ---- 記事の本文取得 ----
LONG_BODY = ("台湾海峡での軍事演習が増えている。" * 5) + "半導体の輸出規制が強化される見通しだと専門家は述べた。" + ("その他の背景説明。" * 200)


def ok_fetch(url):
    return {"status": "ok", "body_text": LONG_BODY, "char_count": len(LONG_BODY), "content_hash": "h", "http_status": 200, "error": None}


def test_safe_url_blocks_internal_addresses():
    for bad in ("file:///etc/passwd", "ftp://example.com/x", "http://127.0.0.1/x", "http://10.0.0.5/x", "http://192.168.1.1/", "http://[::1]/"):
        assert bodies._safe_url(bad) is False, bad


def test_best_passage_centers_on_keyword_and_trims():
    out = bodies.best_passage(LONG_BODY, ["半導体"], 300)
    assert "半導体" in out and len(out) <= 304
    assert bodies.best_passage("短い本文", ["x"], 300) == "短い本文"


def test_ensure_bodies_fetches_caches_and_does_not_refetch(db, monkeypatch):
    calls = []
    monkeypatch.setattr(bodies, "fetch_body", lambda url: calls.append(url) or ok_fetch(url))
    items = [{"id": "s1", "url": "https://ex.com/a"}]
    first = bodies.ensure_bodies(db, items)
    assert first["s1"]["status"] == "ok" and first["s1"]["cached"] is False and len(calls) == 1
    assert db.tables["geo_external_signal_bodies"][0]["status"] == "ok"
    second = bodies.ensure_bodies(db, items)
    assert second["s1"]["cached"] is True and len(calls) == 1                      # 2回目は取得しない


def test_ensure_bodies_records_failures_and_limits_fetches(db, monkeypatch):
    calls = []

    def fake(url):
        calls.append(url)
        return {"status": "blocked", "body_text": None, "char_count": 0, "content_hash": None, "http_status": 403, "error": "拒否"}
    monkeypatch.setattr(bodies, "fetch_body", fake)
    items = [{"id": f"s{i}", "url": f"https://ex.com/{i}"} for i in range(6)]
    out = bodies.ensure_bodies(db, items, max_fetch=2)
    assert len(calls) == 2 and sum(1 for v in out.values() if v["status"] == "blocked") == 2
    assert sum(1 for v in out.values() if v["status"] == "not_fetched") == 4          # 上限を超えた分は取得しない
    again = bodies.ensure_bodies(db, items[:2], max_fetch=2)
    assert len(calls) == 2 and again["s0"]["status"] == "blocked"                     # 拒否された記事は、一定期間は再取得しない


def test_ensure_bodies_works_without_cache_table(db, monkeypatch):
    class NoTable(FakeSupabaseClient):
        def select(self, table, params):
            if table == "geo_external_signal_bodies":
                raise RuntimeError("PGRST205")
            return super().select(table, params)

        def insert(self, table, rows, prefer="x"):
            if table == "geo_external_signal_bodies":
                raise RuntimeError("PGRST205")
            return super().insert(table, rows)
    monkeypatch.setattr(bodies, "fetch_body", ok_fetch)
    out = bodies.ensure_bodies(NoTable(), [{"id": "s1", "url": "https://ex.com/a"}])
    assert out["s1"]["status"] == "ok"                                                 # SQL未適用でも、本文は今回の調査に使える


def test_articles_adapter_uses_body_excerpt_and_reports_status(db, monkeypatch):
    monkeypatch.setattr(bodies, "fetch_body", ok_fetch)
    r = src.search_articles(base_ctx(db, terms=["Taiwan"], tokens=None))
    top = r["items"][0]
    assert top["meta"]["body_status"] == "ok" and "半導体" in top["snippet"] and len(top["snippet"]) > 300
    monkeypatch.setattr(bodies, "fetch_body", lambda url: {"status": "blocked", "body_text": None, "char_count": 0, "content_hash": None,
                                                           "http_status": 403, "error": "x"})
    db.tables["geo_external_signal_bodies"] = []
    r2 = src.search_articles(base_ctx(db, terms=["Taiwan"], tokens=None))
    assert r2["items"][0]["meta"]["body_status"] == "blocked" and r2["items"][0]["snippet"] == "PLA activity near Taiwan"   # 要約のまま


def test_articles_get_source_profile_from_source_library(db):
    db.tables["geo_external_signals"][0]["source_id"] = "src1"
    db.tables["geo_source_registry"] = [{"source_id": "src1", "name": "ExNews", "media_type": "think_tank", "is_primary": False,
                                         "reliability": "立場に偏りあり"}]
    r = src.search_articles(base_ctx(db, terms=["Taiwan"], tokens=None))
    assert r["items"][0]["meta"]["source_profile"] == {"name": "ExNews", "media_type": "think_tank", "is_primary": False, "reliability": "立場に偏りあり"}


# ---- Source Library ----
def test_source_library_adapter_returns_information_sources_not_facts(db, monkeypatch):
    import geo_source_search_service as ss
    monkeypatch.setattr(ss, "search_sources", lambda *a, **k: {"interpretation": {"mode": "semantic"}, "sources": [
        {"source_id": "s1", "name": "台湾大陸委員会", "url": "https://mac.example", "description": "【AI生成・要確認】台湾政府の公式発表",
         "match_reason": "台湾政府の一次情報", "media_type": "government", "is_primary": True, "reliability": None, "match_caution": ""}]})
    r = src.run_adapter("source_library", base_ctx(db, topic="台湾"))
    assert r["status"] == "searched" and r["items"][0]["source_type"] == "source" and r["items"][0]["meta"]["information_source"] is True
    assert "出来事の根拠ではありません" in r["reason"] and "AI生成" not in r["items"][0]["snippet"]


def test_presets_using_source_library_and_prompt_rule():
    for pid in ("changes_since_last", "test_hypothesis", "early_signals"):
        assert "source_library" in svc.PRESETS[pid]["sources"]
    assert "source=source" in svc._SYSTEM_RULES and "body_status" in svc._SYSTEM_RULES


# ---- 専門家検索（登録情報と会話ログ推定を分ける） ----
def test_expert_search_separates_registered_and_inferred(db):
    ext.create_contact(db, ident("alice"), name="田中 太郎", affiliation_title="X大学", expertise_note="台湾の半導体産業・経済安全保障")
    other = ext.create_contact(db, ident("alice"), name="鈴木 花子", affiliation_title="Y研究所")
    log = ext.create_log(db, ident("alice"), contact_ids=[other["contact_id"]], memo="農業政策と中東情勢について議論した。鈴木氏は…", participant_ids=["alice"])
    db.tables["geo_external_contact_log_ai_summaries"] = [{"summary_id": "x", "log_id": log["log_id"], "status": "completed", "edited": None,
        "ai_generated": {"related_context": {"countries": ["イラン"], "topics": ["中東情勢", "農業"], "actors": []},
                         "expert_views": [{"speaker": "鈴木 花子", "text": "制裁は長期化する", "type": "forecast"}]}}]
    hits = ext.search_experts(db, ident("alice"), ["半導体", "中東情勢"])
    by_name = {h["contact"]["name"]: h for h in hits}
    assert by_name["田中 太郎"]["registered_hits"] == ["半導体"] and by_name["田中 太郎"]["inferred"] is None       # 登録情報だけで一致
    suzuki = by_name["鈴木 花子"]
    assert suzuki["registered_hits"] == [] and suzuki["inferred_hits"] == ["中東情勢"]                            # 会話ログ推定だけで一致
    assert suzuki["inferred"]["speaker_view_count"] == 1 and suzuki["inferred"]["note"] == ext.INFERRED_NOTE
    assert by_name["田中 太郎"]["score"] > suzuki["score"]                                                        # 登録情報の一致を重く扱う
    snippet = src._contact_snippet(suzuki["contact"], suzuki["inferred"])
    assert "【登録情報】" in snippet and "【会話ログからのAI推定・未確認】" in snippet and "制裁は長期化する" in snippet


def test_expert_inference_only_uses_logs_the_user_can_see(db):
    c = ext.create_contact(db, ident("alice"), name="田中", owner_ids=["alice"])
    log = ext.create_log(db, ident("alice"), contact_ids=[c["contact_id"]], memo="機密の面談。台湾の話。", participant_ids=["alice"])
    db.tables["geo_external_contact_log_ai_summaries"] = [{"summary_id": "x", "log_id": log["log_id"], "status": "completed", "edited": None,
        "ai_generated": {"related_context": {"countries": ["台湾"], "topics": ["半導体"], "actors": []}, "expert_views": []}}]
    assert all(h["inferred"] is None for h in ext.search_experts(db, ident("bob"), ["半導体"]))
    assert ext.get_contact(db, ident("bob"), c["contact_id"])["inferred_expertise"] is None                      # 見えない面談の推定は漏れない
    assert ext.get_contact(db, ident("alice"), c["contact_id"])["inferred_expertise"]["topics"][0]["name"] == "半導体"


def test_expertise_note_saved_searchable_and_missing_column_message(db):
    c = ext.create_contact(db, ident("alice"), name="田中", expertise_note="  台湾の半導体  ")
    assert c["expertise_note"] == "台湾の半導体"
    assert [x["name"] for x in ext.list_contacts(db, ident("alice"), q="半導体")] == ["田中"]                      # 専門領域コメントでも検索できる
    c2 = ext.update_contact(db, ident("alice"), c["contact_id"], expertise_note="中東")
    assert c2["expertise_note"] == "中東"
    assert ext.create_contact(db, ident("alice"), name="記載なし")["expertise_note"] is None                       # 任意項目

    class NoColumn(FakeSupabaseClient):
        def insert(self, table, rows, prefer="x"):
            if table == "geo_external_contacts" and "expertise_note" in rows[0]:
                raise RuntimeError("Could not find the 'expertise_note' column")
            return super().insert(table, rows)
    nc = NoColumn({"geo_ios_identities": [{"identity_id": "alice", "role": "analyst", "is_active": True}]})
    assert ext.create_contact(nc, ident("alice"), name="A")["name"] == "A"                                         # 書かなければ、SQL未適用でも登録できる
    with pytest.raises(ext.ExternalNetworkError, match="geo_ia_extensions"):
        ext.create_contact(nc, ident("alice"), name="B", expertise_note="x")


def test_reports_need_multiple_word_hits_when_query_has_many_words(db, monkeypatch):
    """1語（「米国」だけ等）が合っただけの無関係なレポートを、根拠にしない（実データで、無関係なレポートが前回分析に選ばれた不具合）。"""
    def rep(i, title):
        return {"source_type": "report", "report_id": i, "title": title, "summary": "s", "date": "2026-09-01", "review_status": "approved",
                "region": [], "author": None, "confidentiality_label": None, "text_score": 0.5, "snippet": None}
    by_term = {"米国": [rep("r1", "米国の半導体規制"), rep("r2", "米国の気候政策")], "中国": [rep("r1", "米国の半導体規制")],
               "China": [rep("r1", "米国の半導体規制")], "半導体": [rep("r1", "米国の半導体規制")]}
    monkeypatch.setattr(src.search_service, "search", lambda client, term, **kw: {"results": by_term.get(term, [])})
    r = src.search_reports(base_ctx(db, terms=["米国", "中国", "China", "半導体"], tokens=None))
    assert [i["id"] for i in r["items"]] == ["r1"]                                     # 4語中4語に一致したものだけ。1語だけのr2は除く
    r2 = src.search_reports(base_ctx(db, terms=["米国"], tokens=None))                  # 語が1つだけの検索は、1語の一致で足りる
    assert {i["id"] for i in r2["items"]} == {"r1", "r2"}


def test_reports_keep_single_hit_results_when_nothing_matches_strongly(db, monkeypatch):
    """日本語のレポートは英語の検索語に当たらない。強く一致するものが無いときは、1語の一致でも残す（全滅させない）。"""
    def rep(i):
        return {"source_type": "report", "report_id": i, "title": i, "summary": "s", "date": "2026-09-01", "review_status": "approved",
                "region": [], "author": None, "confidentiality_label": None, "text_score": 0.5, "snippet": None}
    by_term = {"米国": [rep("r1"), rep("r2")]}
    monkeypatch.setattr(src.search_service, "search", lambda client, term, **kw: {"results": by_term.get(term, [])})
    r = src.search_reports(base_ctx(db, terms=["米国", "UnitedStates", "関税", "tariffs"], tokens=None))
    assert {i["id"] for i in r["items"]} == {"r1", "r2"}
