"""地政学ナレッジ＆インテリジェンスDB Phase2A: 単体・統合テスト。

外部APIは呼び出さない（requests.getをモックする）。実APIを使うテストは
GEO_LIVE_CONNECTOR_TEST環境変数が明示設定された場合のみ実行する想定（本ファイルには含めない、
Mock Testのみ）。
"""
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_candidate_extraction_service as candidate_service  # noqa: E402
import geo_case_service  # noqa: E402
import geo_claim_service  # noqa: E402
import geo_connector_base as base  # noqa: E402
import geo_connector_ofac  # noqa: E402
import geo_connector_world_bank  # noqa: E402
import geo_entity_service  # noqa: E402
import geo_expert_engine  # noqa: E402
import geo_freshness_service  # noqa: E402
import geo_knowledge_service  # noqa: E402
import geo_metric_service  # noqa: E402
import geo_search_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _mock_llm_client(response: dict) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(response)))],
    )
    return client


# ── Raw Snapshot / Ingestion Run ──────────────────────────────────────

def test_raw_snapshot_dedup_by_content_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "RAW_SNAPSHOT_ROOT", tmp_path)
    client = FakeSupabaseClient()
    snapshot1, is_new1 = base.find_or_save_raw_snapshot(client, source_id="src-1", raw_bytes=b"hello")
    snapshot2, is_new2 = base.find_or_save_raw_snapshot(client, source_id="src-1", raw_bytes=b"hello")
    assert is_new1 is True
    assert is_new2 is False
    assert snapshot1["raw_snapshot_id"] == snapshot2["raw_snapshot_id"]
    assert len(client.tables["geo_raw_snapshots"]) == 1


def test_ingestion_run_lifecycle():
    client = FakeSupabaseClient()
    run = base.create_ingestion_run(client, source_id="src-1", connector_type="world_bank")
    assert run["status"] == "running"

    base.complete_ingestion_run(client, ingestion_run_id=run["ingestion_run_id"], status="completed",
                                 counts={"fetched_count": 3, "inserted_count": 3, "error_count": 0})
    updated = base.get_ingestion_run(client, run["ingestion_run_id"])
    assert updated["status"] == "completed"
    assert updated["fetched_count"] == 3


def test_check_automation_allowed_rejects_disabled_source():
    with pytest.raises(base.ConnectorDisabledError):
        base.check_automation_allowed({"name": "test", "automation_allowed": False})
    base.check_automation_allowed({"name": "test", "automation_allowed": True})  # 例外なし


# ── Metric revision ────────────────────────────────────────────────────

def test_metric_observation_same_value_no_new_revision():
    client = FakeSupabaseClient()
    metric_def = geo_metric_service.create_metric_definition(
        client, source_id="src-1", metric_code="SP.POP.TOTL", metric_name="Population")
    first = geo_metric_service.upsert_metric_observation(
        client, metric_definition_id=metric_def["metric_definition_id"], entity_id="entity-1",
        period="2024", observed_value=1000.0, retrieved_at="2026-01-01T00:00:00+00:00")
    second = geo_metric_service.upsert_metric_observation(
        client, metric_definition_id=metric_def["metric_definition_id"], entity_id="entity-1",
        period="2024", observed_value=1000.0, retrieved_at="2026-02-01T00:00:00+00:00")
    assert first["revision_id"] == 1
    assert second["metric_observation_id"] == first["metric_observation_id"]
    assert len(client.tables["geo_metric_observations"]) == 1


def test_metric_observation_changed_value_creates_new_revision():
    client = FakeSupabaseClient()
    metric_def = geo_metric_service.create_metric_definition(
        client, source_id="src-1", metric_code="SP.POP.TOTL", metric_name="Population")
    geo_metric_service.upsert_metric_observation(
        client, metric_definition_id=metric_def["metric_definition_id"], entity_id="entity-1",
        period="2024", observed_value=1000.0, retrieved_at="2026-01-01T00:00:00+00:00")
    revised = geo_metric_service.upsert_metric_observation(
        client, metric_definition_id=metric_def["metric_definition_id"], entity_id="entity-1",
        period="2024", observed_value=1050.0, retrieved_at="2026-03-01T00:00:00+00:00")
    assert revised["revision_id"] == 2
    assert len(client.tables["geo_metric_observations"]) == 2  # 旧値は削除せず残る


def test_link_evidence_metric_observation_target():
    client = FakeSupabaseClient()
    claim = geo_claim_service.create_claim(client, claim_text="x", information_class="observed_fact",
                                            origin_type="manual", actor_id="t")
    evidence = geo_claim_service.link_evidence(client, claim_id=claim["claim_id"], evidence_role="supporting",
                                                actor_id="t", metric_observation_id="metric-obs-1")
    assert evidence["metric_observation_id"] == "metric-obs-1"
    with pytest.raises(ValueError):
        geo_claim_service.link_evidence(client, claim_id=claim["claim_id"], evidence_role="supporting",
                                         actor_id="t", metric_observation_id="metric-obs-1", event_id="e-1")


# ── World Bank Connector ───────────────────────────────────────────────

def _wb_payload(indicator_code: str, values: list) -> bytes:
    metadata = {"page": 1, "pages": 1, "per_page": 1000, "total": len(values)}
    data = [{"indicator": {"id": indicator_code}, "country": {"id": "JP"}, "countryiso3code": "JPN",
             "date": str(v[0]), "value": v[1]} for v in values]
    return json.dumps([metadata, data]).encode("utf-8")


def test_world_bank_connector_creates_metric_observation_and_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "RAW_SNAPSHOT_ROOT", tmp_path)
    client = FakeSupabaseClient(tables={
        "geo_themes": [{"theme_id": "theme-1"}],
    })

    def fake_get(url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        # Population(SP.POP.TOTL)のみ値ありとし、残りは空データにしてテストを軽くする
        if "SP.POP.TOTL" in url:
            resp.content = _wb_payload("SP.POP.TOTL", [(2024, 125000000.0)])
        else:
            resp.content = _wb_payload("X", [])
        resp.json.return_value = json.loads(resp.content)
        return resp

    with patch("geo_connector_world_bank.requests.get", side_effect=fake_get):
        result = geo_connector_world_bank.run_world_bank_ingestion(
            client, theme_id="theme-1", entity_id="entity-jp", country_code="JP", is_demo=True)

    assert result["status"] in ("completed", "completed_with_warnings")
    assert len(client.tables.get("geo_metric_observations", [])) == 1
    assert len(client.tables.get("geo_documents", [])) == 1
    assert len(result["claim_ids"]) == 1
    evidence_rows = client.select("geo_claim_evidence", {"select": "*", "claim_id": f"eq.{result['claim_ids'][0]}"})
    evidence_targets = {("document_chunk" if e.get("document_chunk_id") else
                         "metric_observation" if e.get("metric_observation_id") else "other") for e in evidence_rows}
    assert evidence_targets == {"document_chunk", "metric_observation"}


# ── OFAC Connector ─────────────────────────────────────────────────────

SDN_HEADER_FREE_ROW = '"1234","ACME SANCTIONED CO","-0- [SDGT]","SDGT","","","","","","","",""\n'
ALT_ROW = '"1234","1","aka","ACME ALIAS CO",""\n'
ADD_ROW = '"1234","1","123 Main St","Anytown, NY 10001","United States",""\n'


def _patch_ofac_requests(monkeypatch, sdn_bytes: bytes, alt_bytes: bytes = b"", add_bytes: bytes = b""):
    def fake_get(url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        if "sdn" in url:
            resp.content = sdn_bytes
        elif "add" in url:
            resp.content = add_bytes
        else:
            resp.content = alt_bytes
        return resp
    monkeypatch.setattr(geo_connector_ofac.requests, "get", fake_get)


def test_ofac_connector_added_then_unchanged_then_amended(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "RAW_SNAPSHOT_ROOT", tmp_path)
    client = FakeSupabaseClient(tables={"geo_themes": [{"theme_id": "theme-1"}]})

    _patch_ofac_requests(monkeypatch, SDN_HEADER_FREE_ROW.encode(), ALT_ROW.encode(), ADD_ROW.encode())
    run1 = geo_connector_ofac.run_ofac_ingestion(client, theme_id="theme-1", is_demo=True)
    assert run1["status"] == "completed"
    entries = client.tables["geo_sanctions_entries"]
    assert len(entries) == 1
    assert entries[0]["current_status"] == "active"
    versions_after_run1 = client.tables["geo_sanctions_entry_versions"]
    assert len(versions_after_run1) == 1
    assert versions_after_run1[0]["change_type"] == "added"
    assert len(client.tables["geo_entity_match_candidates"]) == 1

    # Run2: 内容が同一 -> 新versionを作らずlast_seen_atのみ更新
    run2 = geo_connector_ofac.run_ofac_ingestion(client, theme_id="theme-1", is_demo=True)
    assert len(client.tables["geo_sanctions_entry_versions"]) == 1  # 増えない
    assert client.tables["geo_entity_match_candidates"] and len(client.tables["geo_entity_match_candidates"]) == 1  # 増えない

    # Run3: remarksが変わる -> amendedとして新versionを作り、旧versionのvalid_toを更新
    changed_row = '"1234","ACME SANCTIONED CO","-0- [SDGT]","SDGT","","","","","","","","changed remarks"\n'
    _patch_ofac_requests(monkeypatch, changed_row.encode(), ALT_ROW.encode(), ADD_ROW.encode())
    run3 = geo_connector_ofac.run_ofac_ingestion(client, theme_id="theme-1", is_demo=True)
    versions_after_run3 = client.tables["geo_sanctions_entry_versions"]
    assert len(versions_after_run3) == 2
    change_types = sorted(v["change_type"] for v in versions_after_run3)
    assert change_types == ["added", "amended"]
    old_version = [v for v in versions_after_run3 if v["change_type"] == "added"][0]
    assert old_version["valid_to"] is not None  # 直前versionが締められている
    assert len(client.tables["geo_entity_match_candidates"]) == 2  # amended分のみ増える


def test_ofac_delisted_only_on_completed_run(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "RAW_SNAPSHOT_ROOT", tmp_path)
    client = FakeSupabaseClient(tables={"geo_themes": [{"theme_id": "theme-1"}]})
    _patch_ofac_requests(monkeypatch, SDN_HEADER_FREE_ROW.encode(), ALT_ROW.encode(), ADD_ROW.encode())
    geo_connector_ofac.run_ofac_ingestion(client, theme_id="theme-1", is_demo=True)

    # max_records指定（部分取得扱い）ではdelisted判定を行わない
    _patch_ofac_requests(monkeypatch, b"", b"", b"")
    partial_result = geo_connector_ofac.run_ofac_ingestion(client, theme_id="theme-1", max_records=0, is_demo=True)
    assert partial_result["status"] == "completed_with_warnings"
    assert client.tables["geo_sanctions_entries"][0]["current_status"] == "active"

    # max_records未指定（全件取得完了扱い）でようやくdelisted判定が行われる
    full_result = geo_connector_ofac.run_ofac_ingestion(client, theme_id="theme-1", is_demo=True)
    assert full_result["status"] == "completed"
    assert client.tables["geo_sanctions_entries"][0]["current_status"] == "delisted"


def test_ofac_connector_stores_addresses_and_detects_address_only_change(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "RAW_SNAPSHOT_ROOT", tmp_path)
    client = FakeSupabaseClient(tables={"geo_themes": [{"theme_id": "theme-1"}]})

    _patch_ofac_requests(monkeypatch, SDN_HEADER_FREE_ROW.encode(), ALT_ROW.encode(), ADD_ROW.encode())
    geo_connector_ofac.run_ofac_ingestion(client, theme_id="theme-1", is_demo=True)
    first_version = client.tables["geo_sanctions_entry_versions"][0]
    assert first_version["addresses"] == [{
        "address": "123 Main St", "city_state_province_postal_code": "Anytown, NY 10001",
        "country": "United States", "remarks": "",
    }]

    # 名前・program・remarksは同一だが住所のみ変わる -> hashに住所を含めているためamendedとして新versionを作る
    changed_add_row = '"1234","1","456 Other Ave","Anytown, NY 10001","United States",""\n'
    _patch_ofac_requests(monkeypatch, SDN_HEADER_FREE_ROW.encode(), ALT_ROW.encode(), changed_add_row.encode())
    geo_connector_ofac.run_ofac_ingestion(client, theme_id="theme-1", is_demo=True)
    versions = client.tables["geo_sanctions_entry_versions"]
    assert len(versions) == 2
    assert versions[1]["change_type"] == "amended"
    assert versions[1]["addresses"][0]["address"] == "456 Other Ave"


def test_ofac_connector_continues_after_per_record_error(tmp_path, monkeypatch):
    """Phase A（Collection Reliability、2026-10-05）: SDNリスト処理中に1レコードだけ
    ネットワーク起因などの例外が起きても、プロセス全体をクラッシュさせず残りのレコードの処理を
    継続すること。実DBでOFAC Run全体が未捕捉例外でクラッシュし、geo_ingestion_runsが
    status=runningのまま復旧不能で残留する事象を2回（プロキシDNS失敗・Supabase ReadTimeout）
    確認したための回帰テスト。"""
    monkeypatch.setattr(base, "RAW_SNAPSHOT_ROOT", tmp_path)
    client = FakeSupabaseClient(tables={"geo_themes": [{"theme_id": "theme-1"}]})

    row1 = '"1111","FIRST SANCTIONED CO","-0- [SDGT]","SDGT","","","","","","","",""\n'
    row2 = '"2222","SECOND SANCTIONED CO","-0- [SDGT]","SDGT","","","","","","","",""\n'
    _patch_ofac_requests(monkeypatch, (row1 + row2).encode(), b"", b"")

    original_resolve = geo_connector_ofac.geo_entity_resolution.resolve_or_flag

    def flaky_resolve(client_, name):
        if name == "FIRST SANCTIONED CO":
            raise RuntimeError("simulated transient network failure")
        return original_resolve(client_, name)

    monkeypatch.setattr(geo_connector_ofac.geo_entity_resolution, "resolve_or_flag", flaky_resolve)

    result = geo_connector_ofac.run_ofac_ingestion(client, theme_id="theme-1")

    assert result["status"] == "completed_with_warnings"
    assert result["counts"]["error_count"] == 1
    # entry/version作成はresolve_or_flag呼び出しより前なので、例外発生レコードもentry自体は
    # 残る（ロールバックはしない）。例外以降のmatch_candidate/claim作成のみ1件目はスキップされる。
    entries = client.tables["geo_sanctions_entries"]
    assert {e["external_record_id"] for e in entries} == {"1111", "2222"}
    assert len(result["claim_ids"]) == 1  # 1件目はresolve_or_flagで中断されclaimが作られない
    run_row = base.get_ingestion_run(client, result["ingestion_run_id"])
    assert run_row["error_summary"] and "1111" in run_row["error_summary"]


def test_ofac_connector_is_demo_does_not_update_source_registry_freshness(tmp_path, monkeypatch):
    """Phase A（Collection Reliability、2026-10-04）: is_demo=Trueのテスト実行は、
    geo_source_registry.last_successful_ingestion_at/last_ingestion_run_id（実運用のFreshness/
    Staleness判定が参照する値）を更新しないこと。実DBで、is_demoテスト実行がgeo_external_signal_queries
    .last_run_atを書き換えてしまった事象（GDELT）と同じパターンが他Connectorにもないか確認するための
    回帰テスト。"""
    monkeypatch.setattr(base, "RAW_SNAPSHOT_ROOT", tmp_path)
    client = FakeSupabaseClient(tables={"geo_themes": [{"theme_id": "theme-1"}]})
    _patch_ofac_requests(monkeypatch, SDN_HEADER_FREE_ROW.encode(), ALT_ROW.encode(), ADD_ROW.encode())

    result = geo_connector_ofac.run_ofac_ingestion(client, theme_id="theme-1", is_demo=True)

    assert result["status"] == "completed"
    source_row = client.tables["geo_source_registry"][0]
    assert source_row.get("last_successful_ingestion_at") is None
    assert source_row.get("last_ingestion_run_id") is None


# ── Freshness ────────────────────────────────────────────────────────

def test_classify_knowledge_freshness_stale_and_current():
    stale = geo_freshness_service.classify_knowledge_freshness(
        {"status": "active", "as_of_date": "2026-01-01", "stale_after_days": 90}, today=date(2026, 8, 4))
    current = geo_freshness_service.classify_knowledge_freshness(
        {"status": "active", "as_of_date": "2026-08-01", "stale_after_days": 90}, today=date(2026, 8, 4))
    superseded = geo_freshness_service.classify_knowledge_freshness({"status": "superseded"})
    unknown = geo_freshness_service.classify_knowledge_freshness({"status": "active", "as_of_date": None})
    assert stale == "stale"
    assert current == "current"
    assert superseded == "superseded"
    assert unknown == "unknown"


def test_classify_source_freshness_overdue_and_failing():
    now = datetime(2026, 8, 4, tzinfo=timezone.utc)
    overdue = geo_freshness_service.classify_source_freshness(
        {"update_cadence": "daily", "last_successful_ingestion_at": "2026-07-01T00:00:00+00:00", "failure_count": 0},
        now=now)
    failing = geo_freshness_service.classify_source_freshness(
        {"update_cadence": "daily", "last_successful_ingestion_at": "2026-08-01T00:00:00+00:00", "failure_count": 3},
        now=now)
    unknown = geo_freshness_service.classify_source_freshness({"failure_count": 0}, now=now)
    assert overdue["status"] == "overdue"
    assert failing["status"] == "failing"
    assert unknown["status"] == "unknown"


# ── Hybrid Search ──────────────────────────────────────────────────────

def test_search_entities_combines_exact_match_and_fts():
    client = FakeSupabaseClient()
    entity = geo_entity_service.create_entity(client, entity_type="country", canonical_name="台湾", actor_id="t")
    client.rpc_responses = {"search_geo_entities": [
        {"entity_id": entity["entity_id"], "canonical_name": "台湾", "entity_type": "country",
         "is_demo": False, "text_score": 0.8},
    ]}
    result = geo_search_service.search_entities(client, "台湾", log_trace=False)
    assert len(result["results"]) == 1
    assert result["results"][0]["entity_score"] == geo_search_service.EXACT_MATCH_SCORE
    assert result["results"][0]["text_score"] == 0.8


def test_search_knowledge_items_active_status_filter():
    client = FakeSupabaseClient()
    item = geo_knowledge_service.create_knowledge_item(
        client, knowledge_type="precedent", title="テスト前例", structured_content={
            "precedent_title": "t", "question_examined": "q", "period": "p", "geography": "g",
            "actors": ["A"], "background": "b", "triggering_event": "e", "actor_objectives": ["o"],
            "instruments_used": ["i"], "legal_and_institutional_conditions": "l",
            "counterpart_responses": "c", "outcome": "out", "success_factors": ["s"],
            "failure_factors": ["f"], "applicable_conditions": ["ac"], "non_applicable_conditions": ["nac"],
            "indicators_to_watch": ["iw"], "sources": ["src"], "confidence": "中",
        }, information_class="external_assessment", created_by="t")
    # Phase2B-2: retrieval_status（AIが参照してよいか）はstatusと独立した二軸。ここでは
    # review_knowledge_itemを介さず直接テストを組み立てているため、availableも明示的に揃える
    client.update("geo_knowledge_items", {"knowledge_item_id": f"eq.{item['knowledge_item_id']}"},
                  {"status": "active", "retrieval_status": "available"})
    client.rpc_responses = {"search_geo_knowledge_items": [
        {"knowledge_item_id": item["knowledge_item_id"], "title": "テスト前例", "summary": None,
         "knowledge_type": "precedent", "status": "active", "is_demo": False, "text_score": 0.5},
    ]}
    result = geo_search_service.search_knowledge_items(client, "テスト", log_trace=False)
    assert len(result["results"]) == 1
    assert result["results"][0]["knowledge_item"]["knowledge_item_id"] == item["knowledge_item_id"]


# ── Expert Engine統合 ───────────────────────────────────────────────────

def _setup_case_with_issue(client, *, with_evidence: bool):
    issue = client.insert("geo_issues", [{"theme_id": "theme-1", "code": "test-issue", "name_ja": "テスト論点"}])[0]
    case = geo_case_service.create_case(client, case_title="テスト案件", original_question="問い", actor_id="analyst1")
    geo_case_service.link_case_to_issue(client, case_id=case["case_id"], issue_id=issue["issue_id"])

    item = geo_knowledge_service.create_knowledge_item(
        client, knowledge_type="precedent", title="Evidence付き前例", structured_content={
            "precedent_title": "t", "question_examined": "q", "period": "p", "geography": "g",
            "actors": ["A"], "background": "b", "triggering_event": "e", "actor_objectives": ["o"],
            "instruments_used": ["i"], "legal_and_institutional_conditions": "l",
            "counterpart_responses": "c", "outcome": "out", "success_factors": ["s"],
            "failure_factors": ["f"], "applicable_conditions": ["ac"], "non_applicable_conditions": ["nac"],
            "indicators_to_watch": ["iw"], "sources": ["src"], "confidence": "中",
        }, information_class="external_assessment", created_by="analyst1")
    # Phase2B-2: retrieval_status（AIが参照してよいか）はstatusと独立した二軸。ここでは
    # review_knowledge_itemを介さず直接テストを組み立てているため、availableも明示的に揃える
    client.update("geo_knowledge_items", {"knowledge_item_id": f"eq.{item['knowledge_item_id']}"},
                  {"status": "active", "retrieval_status": "available"})
    geo_knowledge_service.link_to_issue(client, knowledge_item_id=item["knowledge_item_id"], issue_id=issue["issue_id"])

    if with_evidence:
        claim = geo_claim_service.create_claim(client, claim_text="根拠claim", information_class="external_assessment",
                                                origin_type="manual", actor_id="analyst1")
        chunk = client.insert("geo_document_chunks", [{
            "document_id": "doc-1", "chunk_index": 0, "quoted_excerpt": "x", "content_hash": "h1",
        }])[0]
        geo_claim_service.link_evidence(client, claim_id=claim["claim_id"], evidence_role="supporting",
                                         actor_id="analyst1", document_chunk_id=chunk["chunk_id"])
        client.insert("geo_knowledge_item_claims", [{
            "knowledge_item_id": item["knowledge_item_id"], "claim_id": claim["claim_id"], "relationship": "supporting",
        }])

    return case, issue, item


def test_build_case_context_excludes_knowledge_without_evidence_chain():
    client = FakeSupabaseClient(tables={"geo_themes": [{"theme_id": "theme-1"}]})
    case, issue, item = _setup_case_with_issue(client, with_evidence=False)

    context = geo_expert_engine.build_case_context(client, case["case_id"])
    included_ids = {i["knowledge_item_id"] for i in context["included"]}
    excluded_ids = {e["result_id"] for e in context["excluded"]}
    assert item["knowledge_item_id"] not in included_ids
    assert item["knowledge_item_id"] in excluded_ids


def test_build_case_context_includes_knowledge_with_evidence_chain():
    client = FakeSupabaseClient(tables={"geo_themes": [{"theme_id": "theme-1"}]})
    case, issue, item = _setup_case_with_issue(client, with_evidence=True)

    context = geo_expert_engine.build_case_context(client, case["case_id"])
    included_ids = {i["knowledge_item_id"] for i in context["included"]}
    assert item["knowledge_item_id"] in included_ids


WELL_FORMED_RESPONSE = {
    "one_line_assessment": "テスト見立て",
    "claims": [{"claim_type": "fact", "text": "x"}],
    "stakeholders": [{"actor": "A"}],
    "main_scenario": {"description": "d", "confidence": "中"},
    "strongest_counterargument": "反論",
    "retraction_conditions": ["条件"],
}


def test_run_expert_request_with_case_id_persists_final_output_and_trace():
    client = FakeSupabaseClient(tables={"geo_themes": [{"theme_id": "theme-1", "name_ja": "テーマ"}]})
    case, issue, item = _setup_case_with_issue(client, with_evidence=True)
    azure_client = _mock_llm_client(WELL_FORMED_RESPONSE)

    result = geo_expert_engine.run_expert_request(
        client, azure_client, "gpt-4o", "case_analysis", "theme-1", "分析して", case_id=case["case_id"])

    assert result["response"]["response_json"]["one_line_assessment"] == "テスト見立て"
    updated_case = geo_case_service.get_case(client, case["case_id"])
    assert updated_case["final_output"]["one_line_assessment"] == "テスト見立て"
    assert len(client.tables["geo_retrieval_runs"]) == 1
    assert client.tables["geo_retrieval_runs"][0]["retrieval_mode"] == "expert_context"
    assert len(client.tables["geo_retrieval_results"]) >= 1


def test_run_expert_request_without_case_id_is_backward_compatible():
    """theme_idのみの既存呼び出しはgeo_retrieval_runsへ何も記録しない（後方互換）。"""
    client = FakeSupabaseClient(tables={
        "geo_themes": [{"theme_id": "theme-1", "name_ja": "テーマ"}],
    })
    azure_client = _mock_llm_client(WELL_FORMED_RESPONSE)
    geo_expert_engine.run_expert_request(client, azure_client, "gpt-4o", "case_analysis", "theme-1", "分析して")
    assert "geo_retrieval_runs" not in client.tables or len(client.tables["geo_retrieval_runs"]) == 0


# ── Candidate抽出（外部Claim由来） ──────────────────────────────────────

def _actor_profile_update_candidate():
    return {
        "candidate_type": "actor_profile_update", "proposed_title": "A国の対外依存に関する更新",
        "proposed_summary": "統計データに基づく更新", "structured_content": {
            "target_entity_id": "entity-a", "change_summary": "輸出依存度が上昇",
            "why_reusable": "他案件でも参照可能", "source_metric_or_claim": "Trade % of GDP",
        },
        "why_reusable": "他案件でも参照可能", "applicable_conditions": ["同種の分析"],
        "non_applicable_conditions": [], "confidence": "中", "confidence_rationale": "統計に基づく",
        "information_class": "observed_fact",
    }


def test_extract_candidates_from_external_claims_links_knowledge_item_claims():
    client = FakeSupabaseClient()
    claim = geo_claim_service.create_claim(client, claim_text="A国の輸出依存度が上昇した",
                                            information_class="observed_fact", origin_type="manual", actor_id="t")
    azure_client = _mock_llm_client({"candidates": [_actor_profile_update_candidate()]})

    result = candidate_service.extract_candidates_from_external_claims(
        client, azure_client, "gpt-4o", [claim["claim_id"]], actor_id="analyst1")

    assert len(result["created"]) == 1
    created_item = result["created"][0]
    assert created_item["status"] == "candidate"
    links = client.select("geo_knowledge_item_claims", {
        "select": "*", "knowledge_item_id": f"eq.{created_item['knowledge_item_id']}",
    })
    assert len(links) == 1
    assert links[0]["claim_id"] == claim["claim_id"]
