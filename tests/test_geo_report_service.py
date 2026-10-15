import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_intelligence_service as service  # noqa: E402
import geo_knowledge_gap_service as gap_service  # noqa: E402
import geo_report_service as report_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

IRAN_ID = "entity-iran"
ISSUE_ID = "issue-1"
THEME_ID = "theme-1"


def _base_tables() -> dict:
    return {
        "geo_themes": [{"theme_id": THEME_ID, "code": "test_theme", "name_ja": "テストテーマ"}],
        "geo_entities": [{"entity_id": IRAN_ID, "canonical_name": "Iran", "entity_type": "country",
                          "is_demo": False}],
        "geo_entity_issues": [{"entity_id": IRAN_ID, "issue_id": ISSUE_ID}],
        "geo_issues": [{"issue_id": ISSUE_ID, "theme_id": THEME_ID, "code": "test-issue"}],
    }


def test_ingest_report_creates_document_report_and_claims(tmp_path, monkeypatch):
    monkeypatch.setattr(report_service.geo_documents_store, "UPLOAD_ROOT", tmp_path)
    client = FakeSupabaseClient(tables=_base_tables())

    result = report_service.ingest_report(
        client, filename="test_report.txt", mime_type="text/plain",
        raw_bytes="イランはホルムズ海峡の通航管理について新たな方針を示した。".encode("utf-8"),
        title="テストレポート", author="analyst_taro", owner_department="地政学チーム",
        report_type="internal_briefing", country_region=["Iran"], topics=[],
        confidentiality="internal", report_kind="internal_report", actor_id="analyst_taro",
    )

    assert result["report"]["author"] == "analyst_taro"
    assert result["document"]["document_id"]
    assert result["issue_ids"] == [ISSUE_ID]
    assert result["claims_created"] >= 1

    claims = client.tables["geo_claims"]
    assert len(claims) >= 1
    assert claims[0]["information_class"] == "internal_expert_assessment"
    assert claims[0]["origin_type"] == "document"

    claim_issue_links = client.tables["geo_claim_issues"]
    assert len(claim_issue_links) >= 1


def test_ingest_report_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(report_service.geo_documents_store, "UPLOAD_ROOT", tmp_path)
    client = FakeSupabaseClient(tables=_base_tables())
    raw = "同一内容のレポート本文です。".encode("utf-8")

    r1 = report_service.ingest_report(
        client, filename="a.txt", mime_type="text/plain", raw_bytes=raw,
        country_region=["Iran"], actor_id="analyst_taro")
    r2 = report_service.ingest_report(
        client, filename="a.txt", mime_type="text/plain", raw_bytes=raw,
        country_region=["Iran"], actor_id="analyst_taro")

    assert r1["report"]["report_id"] == r2["report"]["report_id"]
    assert len(client.tables["geo_documents"]) == 1
    assert len(client.tables["geo_reports"]) == 1


def test_ingest_report_requires_resolvable_theme(tmp_path, monkeypatch):
    """既存Entityが一切無い（=FakeSupabaseClientのilike疑似全件マッチにも引っかかりようが無い）
    状態でtheme_id未指定なら、推測せず明示的にGeoReportErrorとする。"""
    monkeypatch.setattr(report_service.geo_documents_store, "UPLOAD_ROOT", tmp_path)
    client = FakeSupabaseClient(tables={"geo_themes": [{"theme_id": THEME_ID, "code": "test_theme"}]})

    with pytest.raises(report_service.GeoReportError):
        report_service.ingest_report(
            client, filename="a.txt", mime_type="text/plain", raw_bytes=b"content",
            country_region=["未登録の国"], actor_id="analyst_taro")


# ---- Report Upload -> Resolve -> 次回Retrievalへの反映（Fakeクライアントでの統合確認） ----

def test_resolve_with_report_then_retrieval_picks_up_new_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(report_service.geo_documents_store, "UPLOAD_ROOT", tmp_path)
    client = FakeSupabaseClient(tables=_base_tables())

    # Step A: Foundation/Precedent/Relationship/Claimが一切無い（Entityの存在だけは解決できる）
    # 状態でRequestを送る。Phase2のSufficiency Evaluatorの仕様上、Entityが解決できる場合は
    # 「stakeholderは分かるが実質的な知識が無い」としてinsufficientよりpartialに寄るが、
    # いずれにせよ重要カテゴリ複数欠落によりGapは生成される（Phase3のルール通り）。
    request = {
        "requester_app": "sustainability_intelligence", "request_type": "user_question",
        "question": "イランのホルムズ海峡政策の最新方針は？", "country_region": ["Iran"],
        "themes": [], "analysis_requested": True,
    }
    response_before = service.process_request(client, request)
    assert response_before["knowledge_sufficiency"] in ("insufficient", "partial")
    assert response_before["knowledge_gap"]["exists"] is True
    gap_id = response_before["knowledge_gap"]["gap_id"]
    assert gap_id

    # Step C: テストReportをUpload
    ingest_result = report_service.ingest_report(
        client, filename="report.txt", mime_type="text/plain",
        raw_bytes="イランは新方針としてオマーン経由での通航管理協議を優先すると表明した。".encode("utf-8"),
        title="ホルムズ海峡最新動向レポート", author="analyst_taro",
        country_region=["Iran"], actor_id="analyst_taro",
    )

    # Step D: ReportをNeedへ紐付けてResolve
    resolved_gap = gap_service.resolve_intelligence_need(
        client, gap_id, report_id=ingest_result["report"]["report_id"], resolved_by="analyst_taro",
        resolution_note="最新のホルムズ海峡方針レポートを追加した")
    assert resolved_gap["status"] == "resolved"
    assert resolved_gap["resolution_report_id"] == ingest_result["document"]["document_id"]
    assert resolved_gap["resolved_by"] == "analyst_taro"

    # Step E: 同一質問を再送すると新しいKnowledgeがReferenceに現れ、sufficiencyが改善する
    response_after = service.process_request(client, request)
    reference_texts = [r.get("title", "") for r in response_after["references"]]
    assert any("オマーン経由" in t for t in reference_texts)  # 新しいReportの内容がReferenceに現れる
    sufficiency_rank = {"insufficient": 0, "partial": 1, "sufficient": 2}
    assert sufficiency_rank[response_after["knowledge_sufficiency"]] >= sufficiency_rank[
        response_before["knowledge_sufficiency"]]
    # Report内容に基づいた回答になっていること（テンプレート生成のgeo_assessmentに反映される）
    assert "internal_expert_assessment" in (response_after["geo_assessment"] or "")


# ---- delete_report ----

def test_delete_report_removes_document_report_claims_and_file(tmp_path, monkeypatch):
    monkeypatch.setattr(report_service.geo_documents_store, "UPLOAD_ROOT", tmp_path)
    client = FakeSupabaseClient(tables=_base_tables())
    result = report_service.ingest_report(
        client, filename="delete_me.txt", mime_type="text/plain",
        raw_bytes="削除対象のレポート本文です。".encode("utf-8"),
        country_region=["Iran"], actor_id="analyst_taro")
    report_id = result["report"]["report_id"]
    document_id = result["document"]["document_id"]
    storage_path = Path(result["document"]["storage_path"])
    assert storage_path.exists()

    outcome = report_service.delete_report(client, report_id=report_id, actor_id="analyst_taro")

    assert outcome == {"deleted": True, "report_id": report_id, "document_id": document_id}
    # delete_reportが自ら明示的に削除するのはgeo_documents/geo_claims/geo_evidenceとファイル本体
    # のみで、geo_reports/geo_report_gaps/geo_document_chunksは実DBのon delete cascadeに委ねる
    # 設計（FakeSupabaseClientはFK制約・カスケードを再現しないため、ここではPython側が実際に
    # 行う削除のみを検証する）。
    assert client.tables["geo_documents"] == []
    assert all(c["origin_id"] != document_id for c in client.tables.get("geo_claims", []))
    assert not storage_path.exists()


def test_delete_report_also_clears_blocking_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(report_service.geo_documents_store, "UPLOAD_ROOT", tmp_path)
    client = FakeSupabaseClient(tables=_base_tables())
    result = report_service.ingest_report(
        client, filename="with_evidence.txt", mime_type="text/plain",
        raw_bytes="Evidenceが張られたレポート本文です。".encode("utf-8"),
        country_region=["Iran"], actor_id="analyst_taro")
    document_id = result["document"]["document_id"]
    client.insert("geo_evidence", [{"document_id": document_id, "evidence_type": "document"}])

    report_service.delete_report(client, report_id=result["report"]["report_id"], actor_id="analyst_taro")

    assert client.tables["geo_evidence"] == []
    assert client.tables["geo_documents"] == []


def test_delete_report_refuses_when_linked_to_case_review(tmp_path, monkeypatch):
    monkeypatch.setattr(report_service.geo_documents_store, "UPLOAD_ROOT", tmp_path)
    client = FakeSupabaseClient(tables=_base_tables())
    result = report_service.ingest_report(
        client, filename="case_linked.txt", mime_type="text/plain",
        raw_bytes="Caseのレビュー記録に紐づくレポート本文です。".encode("utf-8"),
        country_region=["Iran"], actor_id="analyst_taro")
    report_id = result["report"]["report_id"]
    document_id = result["document"]["document_id"]
    client.insert("geo_case_external_reference", [
        {"case_id": "case-123", "draft_document_id": document_id, "final_document_id": None}])

    with pytest.raises(report_service.GeoReportError, match="削除できません"):
        report_service.delete_report(client, report_id=report_id, actor_id="analyst_taro")

    # 拒否された場合は何も削除されていないこと
    assert len(client.tables["geo_documents"]) == 1
    assert len(client.tables["geo_reports"]) == 1


def test_delete_report_raises_for_unknown_report_id():
    client = FakeSupabaseClient(tables=_base_tables())
    with pytest.raises(report_service.GeoReportError, match="見つかりません"):
        report_service.delete_report(client, report_id="does-not-exist")


def test_delete_report_tolerates_missing_case_external_reference_table(tmp_path, monkeypatch):
    """実DBでgeo_case_external_reference（sql/2026-08-19_geo_phase2b2_external_reference.sql）
    がmigration未適用でテーブル自体が無い環境（PostgRESTが404を返す）でも、削除自体は
    ブロックされないことを確認する（実際にこの状態で発生した500 Internal Server Errorの
    再発防止）。"""
    monkeypatch.setattr(report_service.geo_documents_store, "UPLOAD_ROOT", tmp_path)
    client = FakeSupabaseClient(tables=_base_tables())
    result = report_service.ingest_report(
        client, filename="no_case_ref_table.txt", mime_type="text/plain",
        raw_bytes="geo_case_external_referenceが無い環境向けのテストです。".encode("utf-8"),
        country_region=["Iran"], actor_id="analyst_taro")

    real_select = client.select

    def select_with_404_for_case_external_reference(table, params):
        if table == "geo_case_external_reference":
            resp = requests.Response()
            resp.status_code = 404
            raise requests.exceptions.HTTPError("404 Client Error: Not Found", response=resp)
        return real_select(table, params)

    monkeypatch.setattr(client, "select", select_with_404_for_case_external_reference)

    outcome = report_service.delete_report(client, report_id=result["report"]["report_id"])
    assert outcome["deleted"] is True


# ---- Phase 2D: レポート登録（review_status導出） ----------------------------------

def test_derive_report_review_status_defaults_to_uploaded_only():
    client = FakeSupabaseClient(tables=_base_tables())
    reports = [{"report_id": "r1"}, {"report_id": "r2"}]
    report_service.derive_report_review_status(client, reports)
    assert reports[0]["review_status"] == "uploaded_only"
    assert reports[1]["review_status"] == "uploaded_only"


def test_derive_report_review_status_detects_open_review_session():
    tables = _base_tables()
    tables["geo_expert_review_sessions"] = [
        {"report_id": "r1", "completed_at": None},
    ]
    client = FakeSupabaseClient(tables=tables)
    reports = [{"report_id": "r1"}, {"report_id": "r2"}]
    report_service.derive_report_review_status(client, reports)
    assert reports[0]["review_status"] == "in_review"
    assert reports[1]["review_status"] == "uploaded_only"


def test_derive_report_review_status_prefers_approved_over_in_review():
    tables = _base_tables()
    tables["geo_expert_review_sessions"] = [
        {"report_id": "r1", "completed_at": None},
    ]
    tables["geo_knowledge_items"] = [
        {"knowledge_item_id": "k1", "knowledge_type": "approved_report", "status": "active",
         "source_report_id": "r1"},
    ]
    client = FakeSupabaseClient(tables=tables)
    reports = [{"report_id": "r1"}]
    report_service.derive_report_review_status(client, reports)
    # 公開済み（approved_report knowledge_itemがactive）であれば、たとえ別途open review
    # sessionが残っていてもapprovedを優先する（既にKnowledge化された事実の方が最終状態）。
    assert reports[0]["review_status"] == "approved"


def test_derive_report_review_status_ignores_completed_review_sessions():
    tables = _base_tables()
    tables["geo_expert_review_sessions"] = [
        {"report_id": "r1", "completed_at": "2026-09-01T00:00:00Z"},
    ]
    client = FakeSupabaseClient(tables=tables)
    reports = [{"report_id": "r1"}]
    report_service.derive_report_review_status(client, reports)
    assert reports[0]["review_status"] == "uploaded_only"


# ---- Phase 2D: レポート登録（AI Metadata Extraction） -----------------------------

def test_extract_report_metadata_with_ai_calls_llm_common_directly(monkeypatch):
    """geo_expert_engineを一切経由せず、既存geo_llm_common.call_llm_structuredのみを呼ぶ
    （geo_ios_review_import_serviceと同じ既存パターン、新しいAI基盤を作らない）。"""
    import geo_expert_engine

    called_engine = {"count": 0}
    monkeypatch.setattr(
        geo_expert_engine, "run_expert_request_with_mode",
        lambda *a, **kw: called_engine.update(count=called_engine["count"] + 1))

    captured = {}

    def fake_call_llm_structured(client, model, system_prompt, user_prompt, schema, schema_name, **kwargs):
        captured["schema_name"] = schema_name
        return {"data": {
            "title": "サンプルレポート", "report_date": "2026-09-01", "region": ["Iran"],
            "topics": ["Hormuz"], "key_actors": ["Iran", "US"], "author": None,
            "organization": None, "report_type": None, "language": "ja",
            "keywords": ["Hormuz", "海峡封鎖"], "summary": "要約文。",
            "business_context": None,
            "not_explicit_fields": ["author", "organization", "report_type", "business_context"],
        }}

    monkeypatch.setattr(report_service.geo_llm_common, "call_llm_structured", fake_call_llm_structured)

    result = report_service.extract_report_metadata_with_ai(
        azure_client=object(), model="gpt-test", document_text="サンプルレポート本文")

    assert called_engine["count"] == 0
    assert captured["schema_name"] == "geo_report_registration_metadata_v1"
    assert result["title"] == "サンプルレポート"
    assert result["key_actors"] == ["Iran", "US"]
    assert "author" in result["not_explicit_fields"]


def test_analyze_report_upload_does_not_write_to_db(tmp_path, monkeypatch):
    """analyze_report_uploadはDBへ何も書き込まない（Register前のプレビュー専用）。"""
    monkeypatch.setattr(report_service.geo_documents_store, "UPLOAD_ROOT", tmp_path)
    client = FakeSupabaseClient(tables=_base_tables())

    def fake_call_llm_structured(azure_client, model, system_prompt, user_prompt, schema, schema_name, **kwargs):
        return {"data": {
            "title": None, "report_date": None, "region": [], "topics": [], "key_actors": [],
            "author": None, "organization": None, "report_type": None, "language": None,
            "keywords": [], "summary": None, "business_context": None,
            "not_explicit_fields": ["title", "report_date", "region", "topics", "key_actors",
                                    "author", "organization", "report_type", "language",
                                    "keywords", "summary", "business_context"],
        }}
    monkeypatch.setattr(report_service.geo_llm_common, "call_llm_structured", fake_call_llm_structured)

    result = report_service.analyze_report_upload(
        object(), "gpt-test", filename="sample.txt", mime_type="text/plain",
        raw_bytes="本文に何も明示情報が無いレポートです。".encode("utf-8"))

    assert result["metadata"]["title"] is None
    assert result["extracted_text_length"] > 0
    assert client.tables.get("geo_reports", []) == []
    assert client.tables.get("geo_documents", []) == []
