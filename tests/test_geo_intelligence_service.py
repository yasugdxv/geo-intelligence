import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_intelligence_service as service  # noqa: E402
from geo_intelligence_schema import GeoIntelligenceRequest  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _sample_request(**overrides) -> dict:
    request = {
        "requester_app": "sustainability_intelligence",
        "request_type": "user_question",
        "question": "台湾情勢が半導体サプライチェーンに与える影響は？",
        "country_region": ["Taiwan"],
        "themes": ["semiconductor"],
        "context": "週次レポート作成のための問い合わせ",
        "time_horizon": "3ヶ月",
        "source_refs": [],
        "analysis_requested": True,
    }
    request.update(overrides)
    return request


def _seeded_client() -> FakeSupabaseClient:
    # title/claim_textは、_sample_requestのcountry_region/themes（Taiwan/semiconductor、英語）が
    # ILIKE部分一致で実際にヒットするよう、日本語の内容に加えて英語の対応語も含める
    # （FakeSupabaseClientのilike実装は文字列の部分一致のみで、実FTS RPCの言語処理は再現しないため）。
    return FakeSupabaseClient(tables={
        "geo_knowledge_items": [{
            "knowledge_item_id": "ki-1", "knowledge_type": "country_foundation",
            "title": "台湾(Taiwan): 政治体制の基礎情報", "summary": "台湾の統治構造の概要",
            "status": "active", "retrieval_status": "available", "is_demo": False,
            "confidence": "中", "updated_at": "2026-08-01T00:00:00+00:00",
            "agent_sharing_policy": "allowed_cross_domain",  # Phase E: 既存テストの意図（既存Knowledgeで応答可能）を保つため明示
        }],
        "geo_claims": [{
            "claim_id": "cl-1", "claim_text": "台湾(Taiwan)は半導体(semiconductor)の主要生産拠点である",
            "information_class": "observed_fact", "retrieval_status": "available",
            "is_demo": False,
        }],
    })


# ---- Test 1: 正常RequestをPOST相当で処理するとrequest_id付きResponseが返る ----

def test_process_request_returns_response_with_request_id():
    client = _seeded_client()
    response = service.process_request(client, _sample_request())

    assert response["request_id"]
    assert response["status"] in ("completed", "partial", "failed")
    assert response["knowledge_sufficiency"] in ("sufficient", "partial", "insufficient")
    assert response["completed_at"]


# ---- Test 2: Request内容がDBへ保存される ----

def test_process_request_persists_request_row():
    client = _seeded_client()
    response = service.process_request(client, _sample_request())

    saved = client.tables["geo_intelligence_requests"]
    assert len(saved) == 1
    assert saved[0]["request_id"] == response["request_id"]
    assert saved[0]["requester_app"] == "sustainability_intelligence"
    assert saved[0]["request_type"] == "user_question"
    assert saved[0]["question"] == _sample_request()["question"]
    assert saved[0]["status"] == response["status"]
    assert saved[0]["request_payload"]["themes"] == ["semiconductor"]


# ---- Test 3: ResponseがDBへ保存される ----

def test_process_request_persists_response_row():
    client = _seeded_client()
    response = service.process_request(client, _sample_request())

    saved = client.tables["geo_intelligence_responses"]
    assert len(saved) == 1
    assert saved[0]["request_id"] == response["request_id"]
    assert saved[0]["response_payload"]["request_id"] == response["request_id"]
    assert saved[0]["knowledge_sufficiency"] == response["knowledge_sufficiency"]
    assert saved[0]["expert_response_id"] is None  # Phase1ではExpert AIを呼ばない


def test_process_request_retrieves_existing_knowledge_and_returns_references():
    """このfixtureはEntity/Issueに紐付いていないKnowledge Item/Claimのみだが、FTS経由で
    実際に関連Knowledgeが見つかっている（総Reference数>0）ため、Phase4で修正した
    Knowledge Sufficiency Evaluatorではpartialと判定される（Entity/Issue未登録だけを理由に
    insufficientへ強制しない。実DB E2Eで、Entity未登録の国について関連Reportが正しく
    見つかるのにinsufficient扱いになる不整合を確認して修正した）。"""
    client = _seeded_client()
    response = service.process_request(client, _sample_request())

    assert response["status"] == "completed"
    assert response["knowledge_sufficiency"] == "partial"
    ref_types = {r["reference_type"] for r in response["references"]}
    assert ref_types == {"country_foundation", "observed_fact"}
    # Traceability: retrieval_run_idが実際に記録されている
    assert len(client.tables["geo_retrieval_runs"]) == 1
    assert len(client.tables["geo_retrieval_results"]) == 2


def test_process_request_with_no_matching_knowledge_is_insufficient():
    client = FakeSupabaseClient()  # Knowledge一切なし
    response = service.process_request(client, _sample_request())

    assert response["knowledge_sufficiency"] == "insufficient"
    assert response["references"] == []


# ---- P0-1 Cleanup: Report Versioning導入後、旧Versionが internal_report として
#      混入し続けないこと（同一Issueのretrievalがv1→v2へ正しく置換されること）----

def _versioning_base_tables() -> dict:
    return {
        "geo_themes": [{"theme_id": "theme-v", "code": "test_theme_v", "name_ja": "テストテーマV"}],
        "geo_entities": [{"entity_id": "entity-iran-v", "canonical_name": "Iran",
                          "entity_type": "country", "is_demo": False}],
        "geo_entity_issues": [{"entity_id": "entity-iran-v", "issue_id": "issue-v"}],
        "geo_issues": [{"issue_id": "issue-v", "theme_id": "theme-v", "code": "test-issue-v"}],
    }


def test_resolve_internal_reports_excludes_stale_version_and_includes_latest(tmp_path, monkeypatch):
    import geo_report_service as report_service

    monkeypatch.setattr(report_service.geo_documents_store, "UPLOAD_ROOT", tmp_path)
    client = FakeSupabaseClient(tables=_versioning_base_tables())

    v1 = report_service.ingest_report(
        client, filename="v1.txt", mime_type="text/plain",
        raw_bytes="v1本文（旧版）".encode("utf-8"),
        country_region=["Iran"], actor_id="analyst_taro")["report"]

    v2 = report_service.create_report_version(
        client, supersedes_report_id=v1["report_id"], filename="v2.txt", mime_type="text/plain",
        raw_bytes="v2本文（新版、内容変更あり）".encode("utf-8"), actor_id="analyst_taro")["report"]

    reports = service._resolve_internal_reports(client, ["issue-v"])
    doc_ids = {r["document_id"] for r in reports}

    assert v2["document_id"] in doc_ids
    assert v1["document_id"] not in doc_ids


def test_filter_latest_report_documents_passes_through_rows_without_identity():
    # migration未適用環境（report_identity_idが無い行）との後方互換確認
    client = FakeSupabaseClient(tables={
        "geo_reports": [{"document_id": "doc-legacy", "report_identity_id": None, "version": 1}],
    })
    kept = service._filter_latest_report_documents(client, ["doc-legacy"])
    assert kept == {"doc-legacy"}


def test_filter_latest_report_documents_empty_input_makes_no_query():
    calls = []

    class _CountingClient(FakeSupabaseClient):
        def select(self, table, params):
            calls.append(table)
            return super().select(table, params)

    client = _CountingClient()
    result = service._filter_latest_report_documents(client, [])
    assert result == set()
    assert calls == []


# ---- Test 4: 不正RequestはSchema Validationで拒否される ----

def test_invalid_request_missing_required_field_rejected_by_schema():
    with pytest.raises(ValidationError):
        GeoIntelligenceRequest(question="質問のみでrequester_app/request_typeが無い")


def test_invalid_request_bad_request_type_rejected_by_schema():
    with pytest.raises(ValidationError):
        GeoIntelligenceRequest(
            requester_app="erm_app", request_type="not_a_real_type", question="質問",
        )


def test_empty_question_rejected_by_service_normalization():
    client = _seeded_client()
    with pytest.raises(service.GeoIntelligenceError):
        service.process_request(client, _sample_request(question="   "))


# ---- Failure Isolation: 内部処理が例外を送出してもstatus='failed'で応答する ----

def test_process_request_isolates_internal_failure_as_failed_status(monkeypatch):
    client = _seeded_client()

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated retrieval failure")

    monkeypatch.setattr(service, "retrieve_knowledge", _boom)
    response = service.process_request(client, _sample_request())

    assert response["status"] == "failed"
    assert response["knowledge_sufficiency"] == "insufficient"
    saved_request = client.tables["geo_intelligence_requests"][0]
    assert saved_request["status"] == "failed"
    saved_response = client.tables["geo_intelligence_responses"][0]
    assert saved_response["knowledge_sufficiency"] == "insufficient"
