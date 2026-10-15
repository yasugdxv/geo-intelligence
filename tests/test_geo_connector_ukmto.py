"""UKMTO Direct Collector（公開Web UIが使う公開JSON endpoint）のテスト。実ネットワーク・実DBなし。"""
import json
import types
from datetime import datetime, timedelta, timezone

import pytest

import geo_connector_base as base
import geo_connector_ukmto as uk
import geo_direct_collection_common as common
import geo_monitoring_theme_service as mon
import geo_theme_coverage_service as cov

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
SRC = {"source_id": "S-UKMTO", "name": "UK Maritime Trade Operations (UKMTO)", "url": "https://sccd.royalnavy.mod.uk/api/ukmto/all",
       "automation_allowed": True, "connector_enabled": True, "credential_configured": True}
CFG = {"ukmto": {"registry_domain": "sccd.royalnavy.mod.uk", "endpoint_url": "https://sccd.royalnavy.mod.uk/api/ukmto/all",
                 "original_page_url": "https://www.ukmto.org/recent-incidents", "min_interval_minutes": 30}}


def inc(number, place="Strait of Hormuz", typ="Attack", created="2026-10-05T17:56:00Z", event=None, details="UKMTO WARNING 156-26", **extra):
    d = {"incidentIssuer": "UKMTO", "incidentNumber": number, "sitecoreId": f"id-{number}", "utcDateCreated": created,
         "utcDateOfIncident": event or created, "pinColour": "Red", "incidentTypeName": typ, "incidentTypeLevel": 3,
         "locationLatitude": 26.5, "locationLongitude": 56.2, "locationLatitudeDDDMMSS": "263000N", "locationLongitudeDDDMMSS": "0561200E",
         "region": "IO", "place": place, "vesselName": "..", "vesselType": "Tanker ", "vesselUnderPirateControl": False,
         "dateVesselTaken": None, "crewHeld": 0, "otherDetails": details, "extendTickerTime": None, "hideFromTicker": False}
    d.update(extra)
    return d


def resp(data, status=200, headers=None, raw=None):
    body = raw if raw is not None else json.dumps(data).encode("utf-8")
    return types.SimpleNamespace(content=body, status_code=status, headers={"Content-Type": "application/json", **(headers or {})})


class MemClient:
    """geo_external_signalsだけをメモリに持つ最小の疑似DB。"""
    def __init__(self):
        self.signals, self._n = [], 0

    def select(self, table, params):
        rows = list(self.signals)
        for k, v in params.items():
            if k in ("provider", "source_id", "is_demo") and v.startswith("eq."):
                want = v[3:]
                rows = [r for r in rows if str(r.get(k)).lower() == want.lower()]
            elif k == "dedup_key" and v.startswith("in.("):
                keys = set(v[4:-1].split(","))
                rows = [r for r in rows if r["dedup_key"] in keys]
        return [dict(r) for r in rows][: int(params.get("limit", 10000))]

    def insert(self, table, rows, prefer="return=representation"):
        for r in rows:
            self._n += 1
            self.signals.append({**r, "external_signal_id": f"sig{self._n}"})
        return rows

    def update(self, table, params, patch):
        (k, v), = [(k, v) for k, v in params.items()]
        ids = set(v[4:-1].split(",")) if v.startswith("in.(") else {v[3:]}
        for r in self.signals:
            if r["external_signal_id"] in ids:
                r.update(patch)


@pytest.fixture
def env(monkeypatch):
    st = {"runs": [], "outcomes": [], "last_run": None}
    monkeypatch.setattr(common, "resolve_source", lambda c, d: SRC)
    monkeypatch.setattr(base, "create_ingestion_run", lambda c, **k: {"ingestion_run_id": f"run{len(st['runs']) + 1}"})
    monkeypatch.setattr(base, "complete_ingestion_run", lambda c, **k: st["runs"].append(k))
    monkeypatch.setattr(base, "find_or_save_raw_snapshot", lambda c, **k: ({"raw_snapshot_id": "snap"}, True))
    monkeypatch.setattr(base, "link_run_to_snapshot", lambda c, **k: {})
    monkeypatch.setattr(common, "record_source_outcome", lambda c, row, run, *, ok, is_demo=False: st["outcomes"].append(ok))
    monkeypatch.setattr(uk, "_last_run", lambda c, sid, *, is_demo: st["last_run"])
    st["client"] = MemClient()
    return st


def run(env, data=None, *, fetch=None, now=NOW, ignore=True):
    f = fetch or (lambda url, if_modified_since=None: resp(data))
    return uk.run_ukmto_collection(env["client"], config=CFG, fetch=f, ignore_min_interval=ignore, now=now)


# ---- parse / Schema Guard ----
def test_parse_ok_empty_and_new_fields():
    items, new = uk.parse_payload(json.dumps([inc(1)]).encode())
    assert len(items) == 1 and new == set()
    assert uk.parse_payload(b"[]") == ([], set())                                   # 正当な空応答
    _, new = uk.parse_payload(json.dumps([inc(1, brandNewField="x")]).encode())
    assert new == {"brandNewField"}                                                 # 新fieldはwarning（失敗にしない）


@pytest.mark.parametrize("raw,exc", [
    (b"<html>oops</html>", uk.MalformedPayload), (b"", uk.MalformedPayload), (b"{not json", uk.MalformedPayload),
    (json.dumps({"incidents": []}).encode(), uk.SchemaChanged),                     # listでなくなった（構造変更）
    (json.dumps([inc(1)][0]).encode(), uk.SchemaChanged),
    (json.dumps([{k: v for k, v in inc(1).items() if k != "place"}]).encode(), uk.SchemaChanged),            # 必須field欠落
    (json.dumps([inc(1, incidentNumber="abc")]).encode(), uk.SchemaChanged),        # 型の不一致
    (json.dumps([inc(1, utcDateOfIncident="not-a-date")]).encode(), uk.SchemaChanged),
    (json.dumps(["str"]).encode(), uk.SchemaChanged)])
def test_schema_guard_rejects_broken_payloads(raw, exc):
    with pytest.raises(exc):
        uk.parse_payload(raw)


def test_natural_key_and_title():
    assert uk.natural_key(inc(156)) == "UKMTO-156-2026"
    assert uk.incident_title(inc(156)) == "UKMTO Attack: Strait of Hormuz (#156/26)"
    assert uk.content_hash(inc(1)) == uk.content_hash(inc(1, extendTickerTime="x", hideFromTicker=True))   # 表示制御fieldはversionに使わない
    assert uk.content_hash(inc(1)) != uk.content_hash(inc(1, details="changed"))


# ---- Baseline / 新規 / 更新 / 冪等 ----
def test_baseline_then_idempotent_then_new_then_updated(env):
    first = [inc(150), inc(151, place="Gulf of Aden", typ="Suspicious Activity"), inc(152, details="detail A")]
    r1 = run(env, first)
    sigs = env["client"].signals
    assert (r1["mode"], r1["baseline"], r1["new"], r1["hormuz_incidents"]) == ("baseline", True, 3, 2) and len(sigs) == 3
    assert all(s["raw_metadata"]["baseline"] is True and s["source_id"] == "S-UKMTO" and s["provider"] == "official" for s in sigs)
    md = sigs[0]["raw_metadata"]
    assert md["collection_method"] == "public_json_endpoint" and md["endpoint_documented"] is False and md["natural_key"] == "UKMTO-150-2026"
    assert md["payload_hash"] and md["original_page_url"] == "https://www.ukmto.org/recent-incidents" and md["latitude"] == 26.5
    assert sigs[0]["url"].startswith("https://www.ukmto.org/recent-incidents#UKMTO-150-2026")

    r2 = run(env, first)                                                            # 同じデータを再取得: 増えない
    assert (r2["mode"], r2["new"], r2["updated"], r2["unchanged"]) == ("diff", 0, 0, 3) and len(sigs) == 3

    r3 = run(env, first + [inc(153, created="2026-10-06T09:00:00Z")])                # 新規1件（Baselineではない）
    assert (r3["new"], r3["unchanged"], r3["baseline"]) == (1, 3, False) and len(sigs) == 4
    assert sigs[-1]["raw_metadata"]["baseline"] is False

    upd = [inc(150), inc(151, place="Gulf of Aden", typ="Suspicious Activity"), inc(152, details="detail B"), inc(153, created="2026-10-06T09:00:00Z")]
    r4 = run(env, upd)                                                              # 同じ事案番号の内容更新: 新規ではなく更新
    assert (r4["new"], r4["updated"]) == (0, 1) and len(sigs) == 4
    u = [s for s in sigs if s["raw_metadata"]["natural_key"] == "UKMTO-152-2026"][0]
    assert u["raw_metadata"]["version"] == 2 and len(u["raw_metadata"]["previous_hashes"]) == 1 and u["summary"] == "detail B"
    assert u["raw_metadata"]["baseline"] is False and u["raw_metadata"]["updated_detected_at"]
    assert run(env, upd)["updated"] == 0                                            # 更新後の再取得で再度versionが増えない
    assert [s for s in sigs if s["raw_metadata"]["natural_key"] == "UKMTO-152-2026"][0]["raw_metadata"]["version"] == 2


def test_volatile_field_change_is_not_an_update(env):
    run(env, [inc(150)])
    assert run(env, [inc(150, hideFromTicker=True, extendTickerTime="2026-10-07")])["updated"] == 0


def test_empty_legitimate_response_is_distinct_from_parser_broken(env):
    r = run(env, [])
    assert r["mode"] == "empty" and r["status"] == "completed" and env["client"].signals == [] and env["outcomes"] == [True]
    run(env, [inc(150)])
    r2 = run(env, [])
    assert r2["status"] == "completed_with_warnings" and "空応答" in r2["warnings"][0] and len(env["client"].signals) == 1     # 既存は消さない


def test_new_unknown_field_is_a_warning_not_failure(env):
    r = run(env, [inc(150, brandNewField="x")])
    assert r["status"] == "completed_with_warnings" and "brandNewField" in r["warnings"][0]


# ---- 障害（空成功にしない）----
def boom(exc):
    def f(url, if_modified_since=None):
        raise exc
    return f


@pytest.mark.parametrize("exc,kind", [(uk.FetchTimeout("t"), "timeout"), (uk.HttpStatusError("HTTP 503"), "http_error"),
                                      (uk.FetchConnectionError("c"), "connection_error")])
def test_fetch_failures_are_failed_runs_with_kind(env, exc, kind):
    with pytest.raises(uk.UkmtoError) as ei:
        run(env, fetch=boom(exc))
    assert env["runs"][-1]["status"] == "failed" and f"[{kind}]" in env["runs"][-1]["error_summary"] and ei.value.failure_kind == kind
    assert env["outcomes"] == [False] and env["client"].signals == []


@pytest.mark.parametrize("raw,kind", [(b"<html/>", "malformed_json"), (b'{"data": []}', "schema_changed"),
                                      (json.dumps([{"incidentNumber": 1}]).encode(), "schema_changed")])
def test_malformed_and_schema_change_are_failures_never_empty_success(env, raw, kind):
    with pytest.raises(uk.UkmtoError):
        run(env, fetch=lambda url, if_modified_since=None: resp(None, raw=raw))
    assert env["runs"][-1]["status"] == "failed" and f"[{kind}]" in env["runs"][-1]["error_summary"]
    assert env["outcomes"] == [False] and env["client"].signals == []


def test_real_fetch_maps_requests_errors(monkeypatch):
    import requests
    monkeypatch.setattr(uk.requests, "get", lambda *a, **k: (_ for _ in ()).throw(requests.Timeout("slow")))
    with pytest.raises(uk.FetchTimeout):
        uk.fetch_endpoint("https://x")
    monkeypatch.setattr(uk.requests, "get", lambda *a, **k: types.SimpleNamespace(status_code=503, content=b"", headers={}))
    with pytest.raises(uk.HttpStatusError):
        uk.fetch_endpoint("https://x")
    monkeypatch.setattr(uk.requests, "get", lambda *a, **k: types.SimpleNamespace(status_code=304, content=b"", headers={}))
    assert uk.fetch_endpoint("https://x", if_modified_since="Tue, 06 Oct 2026 08:03:29 GMT").status_code == 304


# ---- 取得頻度 / 条件付きGET / Gate ----
def test_min_interval_blocks_more_frequent_collection(env):
    env["last_run"] = {"ingestion_run_id": "r0", "started_at": (NOW - timedelta(minutes=10)).isoformat(), "status": "completed", "source_vintage": None}
    called = []
    r = run(env, fetch=lambda u, if_modified_since=None: called.append(1) or resp([inc(1)]), ignore=False)
    assert r["status"] == "skipped_too_soon" and called == [] and env["runs"] == []
    env["last_run"]["started_at"] = (NOW - timedelta(minutes=31)).isoformat()
    assert run(env, fetch=lambda u, if_modified_since=None: resp([inc(1)]), ignore=False)["status"] == "completed"
    env["last_run"] = {"ingestion_run_id": "r1", "started_at": (NOW - timedelta(minutes=5)).isoformat(), "status": "failed", "source_vintage": None}
    assert run(env, fetch=lambda u, if_modified_since=None: resp([inc(1)]), ignore=False)["status"] == "skipped_too_soon"     # 失敗後も連打しない


def test_conditional_get_uses_last_modified_and_handles_304(env):
    env["last_run"] = {"ingestion_run_id": "r0", "started_at": (NOW - timedelta(hours=2)).isoformat(), "status": "completed",
                       "source_vintage": "Tue, 06 Oct 2026 08:03:29 GMT"}
    seen = {}

    def f(url, if_modified_since=None):
        seen["ims"] = if_modified_since
        return resp(None, status=304, raw=b"", headers={"Last-Modified": "Tue, 06 Oct 2026 08:03:29 GMT"})
    r = run(env, fetch=f, ignore=False)
    assert seen["ims"] == "Tue, 06 Oct 2026 08:03:29 GMT" and r["mode"] == "not_modified" and r["status"] == "completed" and env["outcomes"] == [True]


def test_gate_blocks_unapproved_source(env, monkeypatch):
    monkeypatch.setattr(common, "resolve_source", lambda c, d: {**SRC, "automation_allowed": False})
    with pytest.raises(base.ConnectorDisabledError):
        run(env, [inc(1)])
    assert env["client"].signals == []


# ---- Monitoring: BaselineはAlert対象にしない / Coverage ----
THEME = {"monitoring_theme_id": "t1", "display_name": "Hormuz", "keywords": ["Strait of Hormuz", "Hormuz"], "sensitivity": "medium"}
HOURS = (NOW - timedelta(hours=3)).isoformat()


def assess_with(monkeypatch, articles):
    monkeypatch.setattr(mon.lib, "get_global_freshness", lambda c, **k: {"status": "stale", "last_data_at": "2026-10-02", "label": "stale"})
    monkeypatch.setattr(mon, "collect_articles", lambda c, t, since, limit=300: {"articles": articles, "sources": [
        {"source_id": "S-UKMTO", "name": "UKMTO", "is_primary": True, "source_tier": "一次"}]})
    direct = [cov.source_freshness({**SRC, "last_successful_ingestion_at": (NOW - timedelta(minutes=20)).isoformat()},
                                   {"role": "primary", "freshness_basis": "collection", "collection_stale_days": 0.125}, None, now=NOW)]
    monkeypatch.setattr(mon.coverage_service, "compute_theme_freshness_for",
                        lambda c, s, **k: cov.compute_theme_freshness(broad={"status": "stale", "last_data_at": None}, direct=direct))
    return mon.assess(None, THEME, now=NOW, persist=False)


def art(i, baseline):
    return {"external_signal_id": f"u{i}", "source_id": "S-UKMTO", "title": "UKMTO Attack: Strait of Hormuz", "summary": "tanker attacked missile",
            "published_at": HOURS, "baseline": baseline}


def test_baseline_incidents_never_create_alerts_but_new_ones_can(monkeypatch):
    a = assess_with(monkeypatch, [art(i, True) for i in range(6)])
    assert a["assessment"]["freshness_state"] == "partial_freshness" and a["alerts_detected"] == []
    assert a["assessment"]["state"] == "partial_coverage" and a["assessment"]["articles_in_window"] == 6        # 関連記事には数える
    b = assess_with(monkeypatch, [art(i, False) for i in range(6)])
    assert b["alerts_detected"] and b["assessment"]["state"] in ("watch", "changed")                           # 新規ならAlert policyへ渡る


def test_ukmto_failure_makes_official_coverage_stale_even_if_others_are_fresh():
    ok = {**SRC, "last_successful_ingestion_at": (NOW - timedelta(minutes=20)).isoformat()}
    rule = {"role": "primary", "freshness_basis": "collection", "collection_stale_days": 0.125}
    assert cov.source_freshness(ok, rule, None, now=NOW)["status"] == "fresh"
    failed = {**ok, "last_failed_ingestion_at": (NOW - timedelta(minutes=2)).isoformat()}                      # 直近の取得が失敗
    assert cov.source_freshness(failed, rule, None, now=NOW)["status"] == "stale"
    old = {**ok, "last_successful_ingestion_at": (NOW - timedelta(hours=5)).isoformat()}                      # 3時間以上止まっている
    assert cov.source_freshness(old, rule, None, now=NOW)["status"] == "stale"
    cmf = {"source_id": "c", "name": "CMF", "role": "primary", "status": "fresh", "reason": ""}
    cats = cov.classify_categories({"official_maritime_advisory": {"label": "Official", "domains": ["u"]},
                                    "military_security": {"label": "Military", "domains": ["c"]}}, broad={"status": "stale"},
                                   direct_for=lambda d: {"u": cov.source_freshness(failed, rule, None, now=NOW), "c": cmf}[d])
    st = {c["key"]: c["status"] for c in cats}
    assert st == {"official_maritime_advisory": "stale", "military_security": "fresh"}                        # CMFがfreshでもUKMTOはfreshにならない


def test_postgres_five_digit_fraction_in_last_run_does_not_break_the_interval_guard(env):
    """Postgresが返す '.80129'（小数秒5桁）をPython 3.10でも読める。以前は最小間隔ガードがValueErrorで落ちた（Pilot事前準備で発覚）。"""
    assert uk._parse_ts("2026-10-06T08:09:17.80129+00:00").microsecond == 801290
    env["last_run"] = {"ingestion_run_id": "r0", "started_at": "2026-10-06T11:55:17.80129+00:00", "status": "completed", "source_vintage": None}
    assert run(env, [inc(1)], ignore=False)["status"] == "skipped_too_soon"
