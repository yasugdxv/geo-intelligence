"""Intelligence AI(会話) 自由質問のソース横断（/ask）のテスト。既存のプリセット調査エンジンの再利用が前提。

守っていること: 横断が既定／権限はソースごと／事実の欄に入れてよいのは社内・Expert AI知識の根拠だけ／
Webは同意があるときだけ／機能フラグ／既存6プリセットと追加対話の互換。
"""
import pytest

import geo_intelligence_ai_preset_service as svc
import geo_intelligence_ai_preset_sources as src
from geo_ios_identity import IosIdentity
from tests.test_geo_intelligence_ai_preset import db, no_real_network, rc, ident, fake_llm  # noqa: F401  既存のテスト部品を再利用


@pytest.fixture(autouse=True)
def flag_on(monkeypatch):
    monkeypatch.setenv("GEO_IA_CROSS_SOURCE_ENABLED", "true")


def analysis(facts=(), interp=(), counter=(), nxt=()):
    mk = lambda items: [{"text": t, "claim_type": c, "refs": r} for t, c, r in items]
    return {"summary": "要約", "insufficient_evidence": False, "warnings": [], "sections": [
        {"key": "facts", "items": mk(facts)}, {"key": "interpretation", "items": mk(interp)},
        {"key": "counterpoints", "items": mk(counter)}, {"key": "next_steps", "items": mk(nxt)}]}


def test_cross_source_is_hidden_and_does_not_change_the_six_presets(db):
    out = svc.list_presets(db)
    assert len(out["presets"]) == 6 and "cross_source" not in {p["id"] for p in out["presets"]}
    assert list(svc.PRESETS) == ["past_cases", "find_experts", "changes_since_last", "compare_views", "test_hypothesis", "early_signals"]
    assert out["cross_source_enabled"] is True


def test_flag_off_by_default_and_blocks_ask(db, monkeypatch):
    monkeypatch.delenv("GEO_IA_CROSS_SOURCE_ENABLED", raising=False)
    assert svc.cross_source_enabled() is False and svc.list_presets(db)["cross_source_enabled"] is False
    with pytest.raises(svc.PresetError):
        svc.ask(db, object(), "m", identity=ident("alice"), requester_context=rc("alice"), message="台湾情勢は？")


def test_empty_message_is_rejected(db):
    with pytest.raises(svc.PresetError):
        svc.ask(db, object(), "m", identity=ident("alice"), requester_context=rc("alice"), message="   ")


def test_all_default_sources_are_searched_in_one_question(db, monkeypatch):
    calls = fake_llm(monkeypatch, analysis=analysis(interp=[("台湾周辺で活動が増えている（報道）", "source_supported", ["E1"])]))
    out = svc.ask(db, object(), "m", identity=ident("alice"), requester_context=rc("alice"), message="台湾周辺の最近の動きを教えて")
    status = {s["source"]: s for s in out["source_status"]}
    for key in ("reports", "knowledge", "articles", "monitoring", "events", "source_library", "external_network"):
        assert key in status, key                                              # 横断が既定（選択を強いない）
    assert status["web"]["status"] == "skipped"                                # Webは同意が無いので使わない
    assert out["mode"] == "cross_source" and "geo_ia_preset_cross_source" in calls
    assert [s["key"] for s in out["sections"]] == ["facts", "interpretation", "counterpoints", "next_steps"]


def test_web_is_used_only_with_consent(db, monkeypatch):
    monkeypatch.setattr(src.pplx, "get_settings", lambda: {"key": "k", "proxies": None, "verify": True})
    seen = []
    monkeypatch.setattr(src.pplx, "search", lambda s, **kw: seen.append(kw) or {"summary": "web summary", "citations": [
        {"url": "https://web.example/a", "title": "Web記事", "published_at": "2026-10-01"}]})
    fake_llm(monkeypatch, analysis=analysis())
    svc.ask(db, object(), "m", identity=ident("alice"), requester_context=rc("alice"), message="台湾周辺の最近の動きを教えて")
    assert seen == []                                                          # 同意なし: 外部へ送らない
    svc.ask(db, object(), "m", identity=ident("alice"), requester_context=rc("alice"), message="台湾周辺の最近の動きを教えて", with_web=True)
    assert len(seen) == 1                                                      # 同意あり: 検索する


def test_facts_must_be_backed_by_internal_or_knowledge_lane_else_demoted(db, monkeypatch):
    # E1=記事（article lane）。事実の欄に入れても、社内・知識の根拠が無いので「見立て」へ降格する
    fake_llm(monkeypatch, analysis=analysis(
        facts=[("台湾周辺で活動が増えている", "source_supported", ["E1"]), ("根拠の無い断定", "ai_inference", [])],
        interp=[("見立て", "ai_inference", [])]))
    out = svc.ask(db, object(), "m", identity=ident("alice"), requester_context=rc("alice"), message="台湾周辺の最近の動きを教えて")
    sec = {s["key"]: s["items"] for s in out["sections"]}
    assert sec["facts"] == []
    demoted = [i for i in sec["interpretation"] if i.get("demoted")]
    assert len(demoted) == 2 and all(i["text"].startswith("（確認済みの社内根拠は無く") for i in demoted)
    assert out["lane_demoted"] == 2
    assert {c["lane"] for c in out["citations"]} == {"article"}


def test_fact_with_internal_report_stays_a_fact(db, monkeypatch):
    db.tables["geo_reports"] = [{"report_id": "r1", "title": "台湾情勢の分析レポート", "summary": "台湾周辺の情勢", "status": "published",
                                 "confidentiality_label": "PUBLIC", "created_at": "2026-09-01T00:00:00", "is_demo": False,
                                 "review_status": "reviewed"}]
    fake_llm(monkeypatch, analysis=analysis(facts=[("レポートに記載の内容", "source_supported", ["E1"])]))
    monkeypatch.setattr(svc.sources, "ADAPTERS", {**src.ADAPTERS, "reports": lambda c: {"status": "searched", "reason": None, "items": [
        {"source_type": "report", "id": "r1", "origin": "internal", "title": "台湾情勢の分析レポート", "date": "2026-09-01", "url": None,
         "snippet": "台湾周辺の情勢", "meta": {}}]}})
    out = svc.ask(db, object(), "m", identity=ident("alice"), requester_context=rc("alice"), message="台湾周辺の最近の動きを教えて")
    sec = {s["key"]: s["items"] for s in out["sections"]}
    assert [i["text"] for i in sec["facts"]] == ["レポートに記載の内容"]
    assert out["citations"][0]["lane"] == "internal" and out["lane_demoted"] == 0


def test_lane_mapping():
    assert svc.lane_of({"source_type": "report", "origin": "internal"}) == "internal"
    assert svc.lane_of({"source_type": "knowledge_item", "origin": "internal"}) == "expert_knowledge"
    assert svc.lane_of({"source_type": "contact_log", "origin": "internal"}) == "network"
    assert svc.lane_of({"source_type": "web", "origin": "web"}) == "web"
    assert svc.lane_of({"source_type": "source", "origin": "internal"}) == "source_library"
    assert svc.lane_of({"source_type": "unknown", "origin": "internal"}) == "article"


def test_anonymous_user_does_not_search_external_network_logs(db, monkeypatch):
    fake_llm(monkeypatch, analysis=analysis())
    out = svc.ask(db, object(), "m", identity=IosIdentity(authenticated=False), requester_context=rc(),
                  message="台湾周辺の最近の動きを教えて")
    assert "external_network" not in {s["source"] for s in out["source_status"] if s["status"] != "skipped"}


def test_short_followup_question_borrows_previous_user_message_only():
    hist = [{"role": "user", "content": "台湾海峡の軍事的緊張について教えて"}, {"role": "assistant", "content": "AIの長い回答" * 20}]
    t = svc._topic_from("日本企業への影響は？", hist)
    assert "台湾海峡の軍事的緊張" in t and "日本企業への影響は？" in t and "AIの長い回答" not in t
    assert svc._topic_from("台湾海峡の軍事的緊張が日本企業に与える影響を教えて", hist) == "台湾海峡の軍事的緊張が日本企業に与える影響を教えて"


def test_followup_works_with_the_cross_source_context(db, monkeypatch):
    fake_llm(monkeypatch, analysis=analysis(interp=[("報道では増加", "source_supported", ["E1"])]),
             followup={"answer": "続きの回答", "claims": [{"text": "報道では増加", "claim_type": "source_supported", "refs": ["E1"]}],
                       "insufficient_evidence": False, "warnings": []})
    first = svc.ask(db, object(), "m", identity=ident("alice"), requester_context=rc("alice"), message="台湾周辺の最近の動きを教えて")
    assert first["context"]["preset_id"] == "cross_source"
    out = svc.followup(db, object(), "m", identity=ident("alice"), requester_context=rc("alice"), message="もう少し詳しく",
                       context=first["context"], history=[])
    assert out["answer"] == "続きの回答" and out["context"]["preset_id"] == "cross_source"


def test_search_terms_are_normalized_to_space_separated_words():
    assert svc._normalize_term("Strait_of_Hormuz") == "Strait of Hormuz"
    assert svc._normalize_term("UnitedStates") == "United States"
    assert svc._normalize_term("Japanese_companies") == "Japanese companies"
    assert svc._normalize_term("NATO") == "NATO" and svc._normalize_term("台湾") == "台湾" and svc._normalize_term(" Taiwan ") == "Taiwan"


def test_plan_terms_applies_normalization(monkeypatch):
    fake_llm(monkeypatch, terms=("Strait_of_Hormuz", "イラン", "UnitedStates"))
    assert svc.plan_terms(object(), "m", "cross_source", {"topic": "ホルムズ海峡"}) == ["Strait of Hormuz", "イラン", "United States"]


def test_demotion_note_matches_the_lane(db, monkeypatch):
    """情報ソースの登録情報（source_library）由来の主張を降格するときは、『報道・Web等』ではなく、登録情報だと書く。"""
    monkeypatch.setattr(svc.sources, "ADAPTERS", {**src.ADAPTERS, "source_library": lambda c: {"status": "searched", "reason": None, "items": [
        {"source_type": "source", "id": "src1", "origin": "internal", "title": "UKMTO", "date": None, "url": None,
         "snippet": "海事の公式情報源", "meta": {}}]}, "articles": lambda c: {"status": "searched", "reason": None, "items": [
        {"source_type": "article", "id": "a1", "origin": "internal", "title": "Taiwan news", "date": "2026-10-01", "url": "https://ex.com/a",
         "snippet": "x", "meta": {}}]}})
    # E1=記事(articles が先に並ぶとは限らないため、参照は両方を指定)、E2=情報ソース
    fake_llm(monkeypatch, analysis=analysis(facts=[("UKMTOは海事情報を発表している", "source_supported", ["E1", "E2"])]))
    out = svc.ask(db, object(), "m", identity=ident("alice"), requester_context=rc("alice"), message="台湾周辺の最近の動きを教えて")
    lanes = {c["ref_id"]: c["lane"] for c in out["citations"]}
    interp = [i for s in out["sections"] if s["key"] == "interpretation" for i in s["items"] if i.get("demoted")]
    assert len(interp) == 1
    if set(lanes.values()) == {"source_library"}:
        assert interp[0]["text"].startswith("（情報ソースの登録情報であり、出来事の根拠ではない）")
    else:                                                   # 記事と情報ソースが混ざる主張は、報道等の注記
        assert interp[0]["text"].startswith("（確認済みの社内根拠は無く、報道・Web等に基づく）")
