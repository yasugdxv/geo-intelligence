"""Phase E: Daily Intelligence Brief（「今日どこを見るべきか」）のテスト。

既存のgeo_operations_service（Phase C/D）の集計をそのまま使うため、個別の集計ロジック
（Review待ち件数・Theme同期件数・Collection health等）自体はtest_geo_operations.pyで
検証済み。ここではBrief固有のロジック（選定優先順位・重複抑制・Evidence付与・
Empty State・LLMを呼ばないこと・fingerprintの変化）だけを検証する。
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_daily_brief_service as brief  # noqa: E402
import geo_llm_common  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402
from tests.test_geo_theme_candidate_service import _tables  # noqa: E402

NOW = datetime(2026, 10, 6, 0, 0, tzinfo=timezone.utc)


def _iso(**delta) -> str:
    return (NOW - timedelta(**delta)).isoformat()


def _base_tables(**extra) -> dict:
    t = _tables(clusters=extra.pop("clusters", []), members=extra.pop("members", []),
                candidates=extra.pop("candidates", []))
    t.update({
        "geo_monitoring_themes": [], "geo_monitoring_alerts": [], "geo_ingestion_runs": [],
        "geo_source_registry": [], "geo_theme_candidates": [],
        # lib.get_global_freshnessは実時刻基準でstale判定する（geo_operations_serviceの
        # 他関数と同じくnow引数を受け取らない）。固定NOWベースの_iso()だと実行日が進むほど
        # staleに倒れてしまうため、ここだけ実時刻基準で「十分新しい」ことを保証する。
        "geo_external_signals": [{"external_signal_id": "s1",
                                  "published_at": datetime.now(timezone.utc).isoformat()}],
    })
    t.update(extra)
    return t


def _candidate(cid, **kw):
    row = {"candidate_id": cid, "status": "NEW", "title": f"title-{cid}", "question": f"q-{cid}",
           "generation_model": "article_discovery/gpt", "created_at": _iso(hours=1),
           "latest_signal_at": _iso(hours=1), "actors": ["A"], "regions": ["R"],
           "source_external_signal_ids": [], "is_demo": False}
    row.update(kw)
    return row


def _alert(aid, theme_id, **kw):
    row = {"alert_id": aid, "monitoring_theme_id": theme_id, "severity": "medium",
           "title": f"alert-{aid}", "reason": "reason", "detected_at": _iso(hours=1),
           "related_signal_ids": []}
    row.update(kw)
    return row


# ---------------------------------------------------------------------------
# 空の状態
# ---------------------------------------------------------------------------

def test_empty_state_reports_no_anomaly_not_just_nothing():
    client = FakeSupabaseClient(tables=_base_tables())
    r = brief.build_brief(client, now=NOW)
    assert r["is_empty"] is True
    assert r["operations_notice"]["summary_text"] == "運用上の異常なし"
    assert r["review"]["summary_text"] == "レビュー待ちなし"
    assert r["theme_sync"]["summary_text"] == "Theme同期待ちなし"
    assert r["errors"] == {}


# ---------------------------------------------------------------------------
# 新しく見るべきテーマ（Theme Candidate、Article Discovery起源を含む）
# ---------------------------------------------------------------------------

def test_new_theme_candidate_is_included_with_origin_and_evidence():
    client = FakeSupabaseClient(tables=_base_tables(
        geo_theme_candidates=[_candidate("t1", source_external_signal_ids=["s1"])],
        geo_external_signals=[{"external_signal_id": "s1", "title": "記事タイトル",
                                "url": "https://example.com/a", "source_name": "Example",
                                "published_at": _iso(hours=3)}],
    ))
    r = brief.build_brief(client, now=NOW)
    assert r["new_themes"]["total_new"] == 1
    item = r["new_themes"]["items"][0]
    assert item["candidate_id"] == "t1" and item["origin"] == "article_discovery"
    assert item["evidence"] == [{"title": "記事タイトル", "url": "https://example.com/a",
                                 "source": "Example", "published_at": _iso(hours=3)}]
    assert any(p["category"] == "new_theme" for p in r["points"])


def test_non_new_status_candidate_is_not_surfaced():
    client = FakeSupabaseClient(tables=_base_tables(
        geo_theme_candidates=[_candidate("t1", status="HANDED_OFF")]))
    r = brief.build_brief(client, now=NOW)
    assert r["new_themes"]["items"] == []


# ---------------------------------------------------------------------------
# 重要な変化（Monitoring Alert）
# ---------------------------------------------------------------------------

def test_unacknowledged_alert_is_included_and_acknowledged_is_excluded():
    client = FakeSupabaseClient(tables=_base_tables(
        geo_monitoring_themes=[{"monitoring_theme_id": "m1", "display_name": "台湾", "status": "active"}],
        geo_monitoring_alerts=[
            _alert("a1", "m1", severity="high"),
            _alert("a2", "m1", severity="low", acknowledged_at=_iso(minutes=5)),
        ]))
    r = brief.build_brief(client, now=NOW)
    assert [a["alert_id"] for a in r["changes"]["items"]] == ["a1"]
    assert r["changes"]["high_count"] == 1
    assert any(p["category"] == "alert" and p["severity"] == "high" for p in r["points"])


# ---------------------------------------------------------------------------
# 重複抑制（同じTheme CandidateがAlertとTheme Candidate両方に出ない）
# ---------------------------------------------------------------------------

def test_duplicate_theme_is_suppressed_from_new_themes_when_already_an_alert():
    client = FakeSupabaseClient(tables=_base_tables(
        geo_theme_candidates=[_candidate("t1")],
        geo_monitoring_themes=[{"monitoring_theme_id": "m1", "display_name": "台湾",
                                "status": "active", "theme_candidate_id": "t1"}],
        geo_monitoring_alerts=[_alert("a1", "m1", severity="high")],
    ))
    r = brief.build_brief(client, now=NOW)
    assert [a["alert_id"] for a in r["changes"]["items"]] == ["a1"]
    assert r["changes"]["items"][0]["related_theme_candidate_id"] == "t1"
    assert r["new_themes"]["items"] == []                      # t1は別カードとして出ない
    assert r["new_themes"]["suppressed_as_duplicate"] == 1
    assert r["new_themes"]["total_new"] == 1                   # 件数の母数自体は維持


# ---------------------------------------------------------------------------
# レビュー待ち / Theme同期待ちサマリ
# ---------------------------------------------------------------------------

def test_review_backlog_summary_counts_high_evidence_and_oldest():
    # geo_operations_service.get_operations_summary()はnowを引数で受け取らず常に実際の
    # 壁時計時刻を使う（build_review自体はnow引数を持つが、Daily Brief側からは
    # get_operations_summaryのラッパー経由でしか呼べずnowを渡せない）。そのため
    # oldest_waiting_daysは実時刻基準になる——created_atもテスト用の固定NOWではなく
    # 実時刻基準で作る（日付境界のズレで±1日ぶれ得るため、厳密一致ではなく範囲で検証する）。
    real_now = datetime.now(timezone.utc)
    clusters = [
        {"episode_cluster_id": "c-old", "cluster_status": "candidate",
         "created_at": (real_now - timedelta(days=27)).isoformat(),
         "member_count": 6, "is_demo": False, "representative_geo": [], "quad_class": 1},
        {"episode_cluster_id": "c-low", "cluster_status": "candidate", "created_at": _iso(days=1),
         "member_count": 1, "is_demo": False, "representative_geo": [], "quad_class": 1},
    ]
    client = FakeSupabaseClient(tables=_base_tables(clusters=clusters))
    r = brief.build_brief(client, now=NOW)
    assert r["review"]["pending"] == 2
    assert r["review"]["high_evidence_count"] == 1
    assert 26 <= r["review"]["oldest_waiting_days"] <= 27
    assert "日前" in r["review"]["summary_text"]


def test_theme_sync_backlog_is_summarised_as_count_not_full_list():
    from tests.test_geo_theme_candidate_service import _cluster, _member
    m = _member("cand-1", "c-1")
    client = FakeSupabaseClient(tables=_base_tables(
        clusters=[_cluster("c-1", cluster_status="accepted")],
        members=[m["member"]], candidates=[m["candidate"]]))
    r = brief.build_brief(client, now=NOW)
    assert r["theme_sync"]["pending"] == 1
    assert isinstance(r["theme_sync"]["pending"], int)       # 一覧ではなく件数のみ
    assert "承認済み" in r["theme_sync"]["summary_text"]


# ---------------------------------------------------------------------------
# Collection異常（あるときだけ出す）
# ---------------------------------------------------------------------------

def test_collection_failure_only_shown_when_present():
    client = FakeSupabaseClient(tables=_base_tables(geo_ingestion_runs=[
        {"ingestion_run_id": "1", "connector_type": "gdelt", "source_id": None, "run_type": "cli",
         "is_demo": False, "status": "failed", "started_at": _iso(hours=2), "error_summary": "429"},
    ]))
    r = brief.build_brief(client, now=NOW)
    assert r["operations_notice"]["has_issue"] is True
    assert any(i["kind"] == "problem" and i["detail"] == "gdelt" for i in r["operations_notice"]["items"])


def test_collection_healthy_shows_no_anomaly_text():
    client = FakeSupabaseClient(tables=_base_tables())
    r = brief.build_brief(client, now=NOW)
    assert r["operations_notice"]["has_issue"] is False
    assert r["operations_notice"]["items"] == []


# ---------------------------------------------------------------------------
# 部分障害 / LLMを呼ばない / fingerprint
# ---------------------------------------------------------------------------

def test_partial_section_failure_still_returns_other_sections():
    client = FakeSupabaseClient(tables=_base_tables())

    class Broken(FakeSupabaseClient):
        def select(self, table, params):
            if table == "geo_monitoring_themes":
                raise RuntimeError("db down")
            return super().select(table, params)
    broken = Broken(tables=client.tables)
    r = brief.build_brief(broken, now=NOW)
    assert "monitoring" in r["errors"]
    assert r["review"]["summary_text"] == "レビュー待ちなし"   # 他セクションは返る


def test_build_brief_does_not_call_llm(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("Daily BriefでLLMを呼んではいけない")
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", forbidden)
    client = FakeSupabaseClient(tables=_base_tables(
        geo_theme_candidates=[_candidate("t1")],
        geo_monitoring_themes=[{"monitoring_theme_id": "m1", "display_name": "台湾", "status": "active"}],
        geo_monitoring_alerts=[_alert("a1", "m1")]))
    brief.build_brief(client, now=NOW)


def test_fingerprint_changes_when_new_alert_appears():
    base = _base_tables(geo_monitoring_themes=[
        {"monitoring_theme_id": "m1", "display_name": "台湾", "status": "active"}])
    client1 = FakeSupabaseClient(tables=base)
    fp1 = brief.build_brief(client1, now=NOW)["fingerprint"]

    tables2 = _base_tables(geo_monitoring_themes=[
        {"monitoring_theme_id": "m1", "display_name": "台湾", "status": "active"}],
        geo_monitoring_alerts=[_alert("a1", "m1")])
    client2 = FakeSupabaseClient(tables=tables2)
    fp2 = brief.build_brief(client2, now=NOW)["fingerprint"]
    assert fp1 != fp2


def test_fingerprint_stable_when_nothing_changes():
    tables = _base_tables(geo_monitoring_themes=[
        {"monitoring_theme_id": "m1", "display_name": "台湾", "status": "active"}],
        geo_monitoring_alerts=[_alert("a1", "m1")])
    fp1 = brief.build_brief(FakeSupabaseClient(tables=dict(tables)), now=NOW)["fingerprint"]
    fp2 = brief.build_brief(FakeSupabaseClient(tables=dict(tables)), now=NOW)["fingerprint"]
    assert fp1 == fp2


# ---------------------------------------------------------------------------
# 選定優先順位（今日のポイント / マネージャー向けSummary）
# ---------------------------------------------------------------------------

def test_points_prioritise_high_alert_over_new_theme_over_operations():
    client = FakeSupabaseClient(tables=_base_tables(
        geo_theme_candidates=[_candidate("t1")],
        geo_monitoring_themes=[{"monitoring_theme_id": "m1", "display_name": "台湾", "status": "active"}],
        geo_monitoring_alerts=[_alert("a1", "m1", severity="high")],
        geo_ingestion_runs=[{"ingestion_run_id": "1", "connector_type": "gdelt", "source_id": None,
                             "run_type": "cli", "is_demo": False, "status": "failed",
                             "started_at": _iso(hours=2), "error_summary": "429"}],
    ))
    r = brief.build_brief(client, now=NOW)
    categories = [p["category"] for p in r["points"]]
    assert categories[0] == "alert"
    assert "new_theme" in categories
    assert r["manager_summary"][0] == r["points"][0]["text"]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_daily_brief_api_endpoint(monkeypatch):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    import geo_daily_brief_api as api
    monkeypatch.setattr(api, "_client", FakeSupabaseClient(tables=_base_tables()))
    app = FastAPI()
    app.include_router(api.geo_daily_brief_router)
    res = TestClient(app).get("/api/geo/daily-brief")
    assert res.status_code == 200
    body = res.json()
    assert body["version"] == "daily_intelligence_brief_v1"
    assert body["is_empty"] is True
