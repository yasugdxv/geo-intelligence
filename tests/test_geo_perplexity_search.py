"""Perplexity検索（geo_perplexity_search）と、テーマ探索への組み込みのテスト。
実際のPerplexity（有料・外部送信）は呼ばない。HTTPはすべて模擬。キーの値はテストにも出さない。
"""
import sys
from pathlib import Path
from datetime import datetime, timezone

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_expert_research_service as expert_research  # noqa: E402
import geo_perplexity_search as pplx  # noqa: E402
import geo_theme_discovery_service as discovery  # noqa: E402
import geo_theme_discovery_web_service as web  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

NOW = datetime(2026, 10, 8, 3, 0, tzinfo=timezone.utc)
SETTINGS = {"key": "TEST-KEY-NOT-REAL", "proxies": {}, "verify": False}


class _Resp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code, self._data, self.text = status, data, text

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


_OK = {"choices": [{"message": {"content": "要約です"}}],
       "search_results": [
           {"title": "記事A", "url": "https://a.com/1", "date": "2026-09-23"},
           {"title": "記事B", "url": "https://b.com/2", "date": None},
           {"title": "重複", "url": "https://a.com/1", "date": "2026-09-23"}],
       "citations": ["https://a.com/1", "https://b.com/2"]}


def _post_returning(resp, captured=None):
    def fake_post(url, **kw):
        if captured is not None:
            captured.update(kw, url=url)
        return resp
    return fake_post


def test_search_returns_citations_with_dates_deduped_and_summary(monkeypatch):
    cap = {}
    monkeypatch.setattr(requests, "post", _post_returning(_Resp(200, _OK), cap))
    out = pplx.search(SETTINGS, question="Q", period_days=30)
    assert [c["url"] for c in out["citations"]] == ["https://a.com/1", "https://b.com/2"]
    assert out["citations"][0]["published_at"] == "2026-09-23" and out["citations"][1]["published_at"] is None
    assert out["summary"] == "要約です"
    assert cap["json"]["search_recency_filter"] == "month"
    assert cap["headers"]["Authorization"] == f"Bearer {SETTINGS['key']}"


def test_search_limits_to_given_domains(monkeypatch):
    cap = {}
    monkeypatch.setattr(requests, "post", _post_returning(_Resp(200, _OK), cap))
    pplx.search(SETTINGS, question="Q", domains=["b.com", "a.com"])
    assert cap["json"]["search_domain_filter"] == ["a.com", "b.com"]


def test_search_uses_plain_citations_when_search_results_missing(monkeypatch):
    monkeypatch.setattr(requests, "post", _post_returning(
        _Resp(200, {"choices": [], "citations": ["https://a.com/1"]})))
    out = pplx.search(SETTINGS, question="Q")
    assert out["citations"] == [{"url": "https://a.com/1", "title": None, "published_at": None}]


def test_search_raises_on_http_error_without_leaking_key(monkeypatch):
    monkeypatch.setattr(requests, "post", _post_returning(_Resp(401, None, text="unauthorized")))
    with pytest.raises(pplx.PerplexityError) as e:
        pplx.search(SETTINGS, question="Q")
    assert SETTINGS["key"] not in str(e.value)


def test_search_raises_on_connection_error(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("proxy down")
    monkeypatch.setattr(requests, "post", boom)
    with pytest.raises(pplx.PerplexityError):
        pplx.search(SETTINGS, question="Q")


def test_get_settings_prefers_env_and_hides_missing_key(monkeypatch):
    monkeypatch.setattr(pplx, "load_config", lambda: {"api_keys": {"perplexity": "CFG-KEY"}, "ssl": {"verify": False}})
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    assert pplx.get_settings()["key"] == "CFG-KEY" and pplx.get_settings()["verify"] is False
    monkeypatch.setenv("PERPLEXITY_API_KEY", "ENV-KEY")
    assert pplx.get_settings()["key"] == "ENV-KEY"
    monkeypatch.delenv("PERPLEXITY_API_KEY")
    monkeypatch.setattr(pplx, "load_config", lambda: {"api_keys": {}})
    assert pplx.get_settings() is None


def test_get_settings_survives_unreadable_config(monkeypatch):
    def broken():
        raise ValueError("bad json")
    monkeypatch.setattr(pplx, "load_config", broken)
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    assert pplx.get_settings() is None     # 例外にせず、Azure版へ切り替えられる


# ---------------------------------------------------------------------------
# テーマ探索への組み込み
# ---------------------------------------------------------------------------

BRIEF = {"summary": "ジョージア州の中間選挙", "keywords": ["ジョージア", "選挙"], "region": None, "period_days": 30}


def _client():
    return FakeSupabaseClient(tables={
        "geo_source_registry": [], "geo_source_domains": [], "geo_external_signals": [],
        "geo_theme_candidates": [], "geo_monitoring_themes": [], "geo_themes": []})


def test_collect_uses_perplexity_keeps_publication_dates_and_saves_them(monkeypatch):
    monkeypatch.setattr(web, "_provider_settings", lambda: SETTINGS)
    monkeypatch.setattr(pplx, "search", lambda settings, **k: {"summary": "s", "found": True, "citations": [
        {"url": "https://a.com/1", "title": "A", "published_at": "2026-09-23"},
        {"url": "https://b.com/2", "title": "B", "published_at": None}]})
    monkeypatch.setattr(expert_research, "run_external_research",
                        lambda *a, **k: pytest.fail("Perplexityが成功したらAzure版を呼ばない"))
    client = _client()
    out = web.collect_web_items(client, object(), "m", brief=BRIEF, domains=set(), is_demo=False, now=NOW,
                                queries=["問い1"])
    by_url = {a["url"]: a for a in out["items"]}
    assert by_url["https://a.com/1"]["published_at"] == "2026-09-23"
    assert by_url["https://b.com/2"]["published_at"] is None            # 日付不明のまま
    assert out["stats"]["via"] == {"perplexity": 2}
    rows = {r["url"]: r for r in client.tables["geo_external_signals"]}
    assert rows["https://a.com/1"]["timestamp_source"] == "publisher"
    assert rows["https://a.com/1"]["published_at"] == "2026-09-23"
    assert rows["https://b.com/2"]["timestamp_source"] == "unknown"


def test_collect_falls_back_to_azure_when_perplexity_fails_and_says_so(monkeypatch):
    monkeypatch.setattr(web, "_provider_settings", lambda: SETTINGS)

    def fail(*a, **k):
        raise pplx.PerplexityError("HTTP 429")
    monkeypatch.setattr(pplx, "search", fail)
    monkeypatch.setattr(expert_research, "run_external_research", lambda *a, **k: {
        "summary": "", "found": True, "citations": [{"title": "A", "url": "https://a.com/1"}]})
    out = web.collect_web_items(_client(), object(), "m", brief=BRIEF, domains=set(), is_demo=False, now=NOW,
                                queries=["問い1", "問い2"])
    assert out["stats"]["via"] == {"azure": 2}
    assert len(out["stats"]["errors"]) == 1 and "Azure OpenAI版" in out["stats"]["errors"][0]   # 同じ注記は1回だけ


def test_collect_limits_perplexity_to_selected_domains(monkeypatch):
    monkeypatch.setattr(web, "_provider_settings", lambda: SETTINGS)
    seen = []

    def fake_search(settings, *, question, domains=None, period_days=None, instruction=None):
        seen.append((question, domains))
        return {"summary": "", "found": True, "citations": [
            {"url": "https://example.com/in", "title": "in", "published_at": None},
            {"url": "https://other.com/out", "title": "out", "published_at": None}]}
    monkeypatch.setattr(pplx, "search", fake_search)
    out = web.collect_web_items(_client(), object(), "m", brief=BRIEF, domains={"example.com"}, is_demo=False,
                                now=NOW, queries=["問い1"])
    dom_calls = [d for q, d in seen if d]
    assert dom_calls == [["example.com"]]
    in_sel = {a["url"]: a["in_selected_sources"] for a in out["items"]}
    assert in_sel["https://example.com/in"] is True and in_sel["https://other.com/out"] is False


def test_provider_settings_can_be_forced_to_azure(monkeypatch):
    monkeypatch.setattr(pplx, "get_settings", lambda: SETTINGS)
    monkeypatch.setenv("THEME_SEARCH_PROVIDER", "azure")
    assert web._provider_settings() is None
    monkeypatch.delenv("THEME_SEARCH_PROVIDER")
    assert web._provider_settings() == SETTINGS


def test_search_retries_on_429_then_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr(pplx.time, "sleep", lambda sec: sleeps.append(sec))
    seq = [_Resp(429, None, text="rate limit"), _Resp(429, None, text="rate limit"), _Resp(200, _OK)]
    monkeypatch.setattr(requests, "post", lambda *a, **k: seq.pop(0))
    out = pplx.search(SETTINGS, question="Q")
    assert len(out["citations"]) == 2 and len(sleeps) == 2 and seq == []


def test_search_gives_up_after_max_attempts_on_429(monkeypatch):
    monkeypatch.setattr(pplx.time, "sleep", lambda sec: None)
    calls = {"n": 0}

    def post(*a, **k):
        calls["n"] += 1
        return _Resp(429, None, text="rate limit")
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(pplx.PerplexityError):
        pplx.search(SETTINGS, question="Q")
    assert calls["n"] == pplx.MAX_ATTEMPTS


def test_search_does_not_retry_on_401(monkeypatch):
    calls = {"n": 0}

    def post(*a, **k):
        calls["n"] += 1
        return _Resp(401, None, text="unauthorized")
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(pplx.PerplexityError):
        pplx.search(SETTINGS, question="Q")
    assert calls["n"] == 1
