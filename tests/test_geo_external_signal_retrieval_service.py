"""地政学ナレッジ＆インテリジェンスDB Phase1: geo_external_signal_retrieval_service.pyのテスト。

Expert AI向けExternal Signal Retrieverが、Knowledge Retrieverとは独立に、必要時のみ・
theme/case優先経路→keyword fallbackの順で動作することを確認する。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_external_signal_retrieval_service as retrieval  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


# ── needs_external_signal_lookup ────────────────────────────────────────

def test_needs_external_signal_lookup_true_for_freshness_keywords():
    assert retrieval.needs_external_signal_lookup("台湾海峡の最近の動きは？")
    assert retrieval.needs_external_signal_lookup("What is the latest on Iran sanctions?")
    assert retrieval.needs_external_signal_lookup("直近の状況を教えて")


def test_needs_external_signal_lookup_false_without_keyword_or_flag():
    assert not retrieval.needs_external_signal_lookup("台湾の歴史的経緯を説明して")
    assert not retrieval.needs_external_signal_lookup("")


def test_needs_external_signal_lookup_true_when_freshness_requirement_flag_set():
    assert retrieval.needs_external_signal_lookup("背景を教えて", freshness_requirement=True)


# ── search_external_signals: theme/case優先、keyword fallback ──────────

def test_search_external_signals_prefers_theme_over_keyword_match():
    """theme_idが分かっている場合、query_matches経由の結果を返し、title/summary ILIKEは使わない
    （GDELT記事は英語/中国語主体で日本語質問文とは一致しないため）。"""
    client = FakeSupabaseClient(tables={
        "geo_external_signal_queries": [{"query_id": "q-1", "theme_id": "theme-taiwan", "case_id": None}],
        "geo_external_signal_query_matches": [
            {"match_id": "m-1", "external_signal_id": "sig-1", "query_id": "q-1",
             "last_matched_at": "2026-09-05T00:00:00+00:00"},
        ],
        "geo_external_signals": [
            {"external_signal_id": "sig-1", "title": "Taiwan Strait tensions rise",
             "published_at": "2026-09-05T00:00:00+00:00"},
        ],
    })

    results = retrieval.search_external_signals(
        client, "台湾海峡の最近の動きは？", theme_id="theme-taiwan", limit=10)

    assert len(results) == 1
    assert results[0]["external_signal_id"] == "sig-1"


def test_search_external_signals_falls_back_to_keyword_when_no_query_match():
    """theme_id指定だが該当Query/Signalが無い場合のみ、title/summary ILIKE fallbackへ落ちる。"""
    client = FakeSupabaseClient(tables={
        "geo_external_signal_queries": [],  # このthemeに紐づくQueryが無い
        "geo_external_signals": [
            {"external_signal_id": "sig-2", "title": "Semiconductor export controls tightened",
             "summary": None, "published_at": "2026-09-05T00:00:00+00:00"},
        ],
    })

    results = retrieval.search_external_signals(
        client, "semiconductor", theme_id="theme-unrelated", limit=10)

    assert len(results) == 1
    assert results[0]["external_signal_id"] == "sig-2"


def test_search_external_signals_without_theme_or_case_uses_keyword_directly():
    client = FakeSupabaseClient(tables={
        "geo_external_signals": [
            {"external_signal_id": "sig-3", "title": "Ukraine ceasefire talks", "summary": None,
             "published_at": "2026-09-05T00:00:00+00:00"},
        ],
    })

    results = retrieval.search_external_signals(client, "ceasefire", limit=10)

    assert len(results) == 1
    assert results[0]["external_signal_id"] == "sig-3"


def test_search_external_signals_preserves_discovery_unverified_markers():
    """返却Signalのsource_role=discovery/evidence_status=unverifiedはフィルタされず素通りする。"""
    client = FakeSupabaseClient(tables={
        "geo_external_signals": [
            {"external_signal_id": "sig-4", "title": "Iran sanctions news", "summary": None,
             "published_at": "2026-09-05T00:00:00+00:00",
             "source_role": "discovery", "evidence_status": "unverified"},
        ],
    })

    results = retrieval.search_external_signals(client, "Iran sanctions", limit=10)

    assert results[0]["source_role"] == "discovery"
    assert results[0]["evidence_status"] == "unverified"
