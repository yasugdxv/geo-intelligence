"""Theme Discovery「本当に探索する」拡張（チャット要件すり合わせ→Source自動提案→
Web検索取り込み→既存discover()）のテスト。既存discover()・explorer・external_signal_serviceの
ロジック自体は変更していないため、ここでは新規追加分（本モジュール固有のロジック）だけを検証する。
"""
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_llm_common  # noqa: E402
import geo_theme_discovery_live_service as live  # noqa: E402
import geo_theme_discovery_service as discovery  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

NOW = datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc)


def _source(source_id, url, **kw):
    row = {"source_id": source_id, "name": source_id, "url": url, "archived_at": None}
    row.update(kw)
    return row


# ---------------------------------------------------------------------------
# チャット（refine_brief）
# ---------------------------------------------------------------------------

def test_refine_brief_requires_message():
    try:
        live.refine_brief(object(), "m", history=[], message="  ")
        assert False
    except discovery.DiscoveryError:
        pass


def test_refine_brief_requires_llm():
    try:
        live.refine_brief(None, "m", history=[], message="台湾のニッチな動きを探して")
        assert False
    except discovery.DiscoveryError as e:
        assert "AI分析エンジン" in str(e)


def test_refine_brief_sends_full_transcript_and_returns_llm_data(monkeypatch):
    captured = {}

    def fake_call(azure_client, model, system_prompt, user_prompt, schema, schema_name, **kwargs):
        captured["user_prompt"] = user_prompt
        return {"data": {"assistant_reply": "了解しました", "ready": True,
                         "brief": {"keywords": ["Hormuz"], "exclude_mainstream": True,
                                   "region": "Iran", "period_days": 14, "summary": "ホルムズ海峡のニッチな動き"}}}
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake_call)

    history = [{"role": "user", "content": "最近の中東情勢を教えて"},
               {"role": "assistant", "content": "もう少し具体的に教えてください"}]
    result = live.refine_brief(object(), "m", history=history, message="ホルムズ海峡で一般ニュースにまだ出ていない話")
    assert result["ready"] is True
    assert result["brief"]["keywords"] == ["Hormuz"]
    assert "最近の中東情勢を教えて" in captured["user_prompt"]
    assert "ホルムズ海峡で一般ニュースにまだ出ていない話" in captured["user_prompt"]


# ---------------------------------------------------------------------------
# Web citation正規化（_normalize_web_citation）
# ---------------------------------------------------------------------------

def test_normalize_web_citation_produces_dedup_key_and_observed_timestamp():
    raw = {"url": "https://example.com/a?utm_source=x", "title": "Example Title", "domain": "example.com",
           "_fetched_at": NOW}
    row = live._normalize_web_citation(raw)
    assert row["provider"] == live.WEB_PROVIDER
    assert row["timestamp_source"] == "unknown"  # geo_external_signalsのCHECK制約が許す3値の一つ
    assert row["published_at"] == NOW.isoformat()
    assert row["dedup_key"]                      # 空でない


def test_normalize_web_citation_is_stable_for_the_same_raw_url():
    # compute_dedup_keyはcanonical_url（素のURL）を最優先する（GDELT側のnormalize_articleと同じ
    # 優先順位）。そのため同一の生URLなら同じdedup_keyになる（re-run時の重複防止）。
    a = live._normalize_web_citation({"url": "https://example.com/a?utm_source=x", "title": "T",
                                      "domain": "example.com", "_fetched_at": NOW})
    b = live._normalize_web_citation({"url": "https://example.com/a?utm_source=x", "title": "T2",
                                      "domain": "example.com", "_fetched_at": NOW})
    assert a["dedup_key"] == b["dedup_key"]
    assert a["normalized_url"] == b["normalized_url"] == "https://example.com/a"


# ---------------------------------------------------------------------------
# gather_web_signals_for_brief / run_live_discovery
# ---------------------------------------------------------------------------

def test_gather_web_signals_requires_source_ids():
    client = FakeSupabaseClient(tables={"geo_source_registry": [], "geo_source_domains": []})
    out = live.gather_web_signals_for_brief(client, object(), "m", brief={"keywords": ["x"]}, source_ids=[])
    assert out["fetched"] == 0 and out["errors"]


def test_gather_web_signals_requires_llm():
    client = FakeSupabaseClient(tables={
        "geo_source_registry": [_source("s1", "https://example.com")], "geo_source_domains": []})
    out = live.gather_web_signals_for_brief(client, None, "m", brief={"keywords": ["x"]}, source_ids=["s1"])
    assert out["fetched"] == 0 and "AI分析エンジン" in out["errors"][0]


def test_gather_web_signals_filters_citations_outside_selected_domains_and_ingests_allowed(monkeypatch):
    client = FakeSupabaseClient(tables={
        "geo_source_registry": [_source("s1", "https://example.com")], "geo_source_domains": [],
        "geo_external_signals": []})

    def fake_research(azure_client, model, *, question, context, search_lens):
        return {"summary": "sum", "found": True, "citations": [
            {"title": "In-domain", "url": "https://example.com/a"},
            {"title": "Out-of-domain", "url": "https://not-allowed.com/b"},
        ]}
    import geo_expert_research_service as expert_research
    monkeypatch.setattr(expert_research, "run_external_research", fake_research)

    out = live.gather_web_signals_for_brief(
        client, object(), "m", brief={"keywords": ["x"]}, source_ids=["s1"], now=NOW)
    assert out["fetched"] == 1 and out["inserted"] == 1 and out["errors"] == []
    assert len(client.tables["geo_external_signals"]) == 1
    assert client.tables["geo_external_signals"][0]["url"] == "https://example.com/a"
    assert client.tables["geo_external_signals"][0]["provider"] == live.WEB_PROVIDER


def test_gather_web_signals_records_error_without_raising_on_research_failure(monkeypatch):
    client = FakeSupabaseClient(tables={
        "geo_source_registry": [_source("s1", "https://example.com")], "geo_source_domains": [],
        "geo_external_signals": []})

    def boom(*a, **k):
        raise RuntimeError("search failed")
    import geo_expert_research_service as expert_research
    monkeypatch.setattr(expert_research, "run_external_research", boom)

    out = live.gather_web_signals_for_brief(
        client, object(), "m", brief={"keywords": ["x"]}, source_ids=["s1"], now=NOW)
    assert out["fetched"] == 0 and out["errors"]


def test_run_live_discovery_requires_brief_summary():
    client = FakeSupabaseClient(tables={})
    try:
        live.run_live_discovery(client, object(), "m", brief={"summary": ""}, source_ids=[], include_web=False)
        assert False
    except discovery.DiscoveryError:
        pass


def test_run_live_discovery_skips_web_gathering_when_include_web_false(monkeypatch):
    called = {"n": 0}

    def forbidden(*a, **k):
        called["n"] += 1
        raise AssertionError("include_web=Falseの時はWeb検索してはいけない")
    monkeypatch.setattr(live, "gather_web_signals_for_brief", forbidden)

    client = FakeSupabaseClient(tables={"geo_external_signal_queries": []})
    out = live.run_live_discovery(
        client, object(), "m", brief={"summary": "ホルムズ海峡"}, source_ids=["s1"], include_web=False)
    assert called["n"] == 0
    assert "job_id" in out and out["web"] is None


def test_run_live_discovery_with_web_starts_web_theme_job_not_article_job(monkeypatch):
    """include_web=true（既定）はWeb探索（ソース外も含む・記事数の足切りなし）を開始し、
    従来の記事だけのdiscover()jobは開始しない。"""
    import geo_theme_discovery_web_service as web_service
    calls = []

    def fake_start_web_job(client, azure_client, model, **params):
        calls.append(("web", params["brief"]["summary"], params["source_ids"]))
        return "web-job-1"

    def forbidden(*a, **k):
        raise AssertionError("Web探索の時に従来のdiscover jobを開始してはいけない")

    monkeypatch.setattr(web_service, "start_web_job", fake_start_web_job)
    monkeypatch.setattr(discovery, "start_job", forbidden)
    out = live.run_live_discovery(
        FakeSupabaseClient(tables={}), object(), "m",
        brief={"summary": "ホルムズ海峡のニッチな動き", "period_days": 14}, source_ids=["s1"], include_web=True)
    assert out == {"job_id": "web-job-1", "web": None}
    assert calls == [("web", "ホルムズ海峡のニッチな動き", ["s1"])]


def test_live_run_api_defaults_to_web_search_on():
    import geo_theme_discovery_live_api as live_api
    assert live_api.RunBody(brief={"summary": "x"}).include_web is True


def test_chat_prompt_guides_like_an_expert_and_brief_carries_actors_and_angle():
    p = live._SYSTEM_PROMPT
    for word in ("主要アクター", "先行指標", "制度上の制約", "過去の類似事例", "日本企業", "選択肢"):
        assert word in p
    brief = live.BRIEF_SCHEMA["properties"]["brief"]
    assert {"actors", "angle"} <= set(brief["properties"]) and {"actors", "angle"} <= set(brief["required"])


def test_refine_brief_allows_one_correction_retry_and_turns_llm_failure_into_a_readable_error(monkeypatch):
    seen = {}

    def boom(client_, model_, sp, up, schema, name, **kw):
        seen["max_attempts"] = kw.get("max_attempts")
        raise geo_llm_common.GeoLLMError("brief is not of type object", attempts=[], exception_type="ValidationError")
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", boom)
    import pytest
    with pytest.raises(discovery.DiscoveryError) as e:
        live.refine_brief(object(), "m", history=[], message="ウクライナの動き")
    assert seen["max_attempts"] == 3                      # 訂正の再試行を許可
    assert "もう一度" in str(e.value)                       # 500ではなく、画面に出せる文
