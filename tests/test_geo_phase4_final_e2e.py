"""地政学インテリジェンス基盤 Phase4: Final E2E Test（Scenario 1〜8）。"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_intelligence_service as service  # noqa: E402
from geo_intelligence_schema import GeoIntelligenceRequest  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

ISSUE_ID = "issue-1"
IRAN_ID = "entity-iran"
OMAN_ID = "entity-oman"


def _base_request(**overrides) -> dict:
    request = {
        "requester_app": "sustainability_intelligence", "request_type": "user_question",
        "question": "イランはホルムズ海峡の通航にどう対応しているか？",
        "country_region": ["Iran"], "themes": ["hormuz"], "analysis_requested": True,
    }
    request.update(overrides)
    return request


def _sufficient_tables() -> dict:
    return {
        "geo_entities": [
            {"entity_id": IRAN_ID, "canonical_name": "Iran", "entity_type": "country", "is_demo": False},
            {"entity_id": OMAN_ID, "canonical_name": "Oman", "entity_type": "country", "is_demo": False},
        ],
        "geo_entity_issues": [{"entity_id": IRAN_ID, "issue_id": ISSUE_ID}],
        "geo_relationships": [{
            "relationship_id": "rel-1", "from_entity_id": IRAN_ID, "to_entity_id": OMAN_ID,
            "relationship_type": "mediation_negotiation_channel", "relationship_domain": "diplomacy",
            "description": "仲介チャネル", "is_demo": False,
        }],
        "geo_knowledge_items": [
            {"knowledge_item_id": "ki-foundation", "knowledge_type": "country_foundation",
             "title": "イラン: 統治構造", "summary": "概要", "status": "active",
             "retrieval_status": "available", "is_demo": False, "confidence": "中",
             "agent_sharing_policy": "allowed_cross_domain"},
            {"knowledge_item_id": "ki-precedent", "knowledge_type": "precedent",
             "title": "先例", "summary": "概要", "status": "active",
             "retrieval_status": "available", "is_demo": False, "confidence": "中",
             "agent_sharing_policy": "allowed_cross_domain"},
        ],
        "geo_knowledge_item_issues": [
            {"knowledge_item_id": "ki-foundation", "issue_id": ISSUE_ID},
            {"knowledge_item_id": "ki-precedent", "issue_id": ISSUE_ID},
        ],
        "geo_claims": [
            {"claim_id": "cl-fact", "claim_text": "MoUが2026年6月に署名された",
             "information_class": "observed_fact", "retrieval_status": "available", "is_demo": False},
        ],
        "geo_claim_issues": [{"claim_id": "cl-fact", "issue_id": ISSUE_ID}],
    }


WELL_FORMED_EXPERT_RESPONSE = {
    "one_line_assessment": "イランは実効支配継続の方向。",
    "claims": [{"claim_type": "fact", "text": "MoUが署名された", "source_ref": "r-1"}],
    "stakeholders": [{"actor": "Iran"}],
    "main_scenario": {"description": "グレーゾーン支配の継続", "confidence": "中", "supporting_evidence": ["攻撃再開"],
                       "selection_rationale": "過去の類似局面と一致するため"},
    "strongest_counterargument": "軟化する可能性もある",
    "retraction_conditions": ["攻撃が完全に停止した場合"],
    "business_impact": {"direct": None, "indirect": "政策圧力が高まる可能性がある",
                        "unrelated_note": None, "environment_change": None},
    # Session2: case_analysisモードの必須項目（geo_expert_quality_guard._key_stakeholder_v12_check）
    "key_point_assessment": {
        "candidate_key_points": [{
            "key_point": "イランが実効支配を継続するか", "why_it_could_be_key": "海峡管理の実質的な帰趨を左右するため",
            "control_type": "unknown", "what_it_explains": "現状のグレーゾーン継続傾向", "what_it_does_not_explain": "停止時期",
        }],
        "primary_key_point": "イランが実効支配を継続するか", "selection_rationale": "過去の類似局面と一致するため",
        "hinge_structure": {"primary_hinge": "イラン政府の方針"},
        "control_type": "unknown", "supporting_evidence_ids": None,
        "disconfirming_signal": "攻撃が完全に停止した場合", "confidence": "中",
    },
    # v1.2: case_analysisモードの必須項目（geo_expert_quality_guard._key_stakeholder_v12_check）
    "key_stakeholder_assessment": {
        "determination": "single", "selected_actor_ids": ["iran"],
        "rationale": "実効支配の継続可否を決定できるのはイラン政府のため", "confidence": "中",
    },
    "key_actor_profiles": [{"actor_ref": "iran", "actor": "Iran", "confidence": "中"}],
}


def _mock_llm_client() -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(WELL_FORMED_EXPERT_RESPONSE)))])
    return client


# ---- Scenario 1: Knowledgeあり → 通常回答（Expert AI無し） ----

def test_scenario1_sufficient_knowledge_normal_response():
    client = FakeSupabaseClient(tables=_sufficient_tables())
    response = service.process_request(client, _base_request(analysis_requested=False))

    assert response["status"] == "partial"  # analysis_requested=Falseの既定挙動
    assert response["knowledge_sufficiency"] == "sufficient"
    assert response["references"]
    assert response["expert_response_id"] if "expert_response_id" in response else True


# ---- Scenario 2: Knowledgeあり＋高度分析 → Expert AI利用 ----

def test_scenario2_sufficient_and_advanced_analysis_uses_expert_ai(monkeypatch):
    # v1.2段階実行方式は既定でStage1+Stage2の複数回呼び出しになりうるため、本テストの関心事
    # （sufficient判定時にExpert AIが1回使われるE2Eフロー）とは無関係な呼び出し回数の差異を
    # 避けるため一括方式に固定する
    monkeypatch.setenv("GEO_EXPERT_EXECUTION_MODE", "monolithic")
    client = FakeSupabaseClient(tables=_sufficient_tables())
    azure_client = _mock_llm_client()

    response = service.process_request(client, _base_request(analysis_requested=True),
                                        azure_client=azure_client, model="gpt-4o")

    azure_client.chat.completions.create.assert_called_once()
    assert response["knowledge_sufficiency"] == "sufficient"
    saved_response = client.tables["geo_intelligence_responses"][0]
    assert saved_response["expert_response_id"] is not None


# ---- Scenario 3: Knowledge一部不足 → partial＋Gap ----

def test_scenario3_partial_knowledge_creates_gap():
    tables = _sufficient_tables()
    tables["geo_relationships"] = []
    tables["geo_knowledge_items"] = []
    tables["geo_knowledge_item_issues"] = []
    client = FakeSupabaseClient(tables=tables)

    response = service.process_request(client, _base_request())

    assert response["knowledge_sufficiency"] == "partial"
    assert response["knowledge_gap"]["exists"] is True
    assert len(client.tables["geo_knowledge_gaps"]) == 1


# ---- Scenario 4: Knowledgeなし → insufficient＋Intelligence Need ----

def test_scenario4_no_knowledge_insufficient_and_intelligence_need():
    client = FakeSupabaseClient()
    response = service.process_request(client, _base_request(
        question="架空の国XYZの情勢は？", country_region=["架空の国XYZ"], themes=[]))

    assert response["knowledge_sufficiency"] == "insufficient"
    assert response["knowledge_gap"]["exists"] is True
    gaps = client.tables["geo_knowledge_gaps"]
    assert gaps[0]["origin_type"] == "external_app_request"
    assert gaps[0]["priority"] == "high"


# ---- Scenario 5: Report Upload → Gap Resolve → 再質問で回答可能 ----
# 詳細なフルフローはtests/test_geo_report_service.pyで検証済み（Step A〜Eの完全なE2E）。
# ここでは「Resolve後の再質問でreferencesが改善する」という中核部分のみ再確認する。

def test_scenario5_report_upload_resolve_then_requery(tmp_path, monkeypatch):
    import geo_knowledge_gap_service as gap_service
    import geo_report_service as report_service
    monkeypatch.setattr(report_service.geo_documents_store, "UPLOAD_ROOT", tmp_path)

    client = FakeSupabaseClient(tables={
        "geo_themes": [{"theme_id": "theme-1", "code": "test_theme", "name_ja": "x"}],
        "geo_entities": [{"entity_id": IRAN_ID, "canonical_name": "Iran", "entity_type": "country",
                          "is_demo": False}],
        "geo_entity_issues": [{"entity_id": IRAN_ID, "issue_id": ISSUE_ID}],
        "geo_issues": [{"issue_id": ISSUE_ID, "theme_id": "theme-1", "code": "test-issue"}],
    })
    request = _base_request(themes=[])
    before = service.process_request(client, request)
    gap_id = before["knowledge_gap"]["gap_id"]

    ingest = report_service.ingest_report(
        client, filename="r.txt", mime_type="text/plain",
        raw_bytes="イランはオマーン経由の新協議枠組みを提示した。".encode("utf-8"),
        country_region=["Iran"], actor_id="analyst_taro")
    gap_service.resolve_intelligence_need(
        client, gap_id, report_id=ingest["report"]["report_id"], resolved_by="analyst_taro")

    after = service.process_request(client, request)
    assert any("オマーン経由" in r.get("title", "") for r in after["references"])


# ---- Scenario 6: 不正Request → validation error ----

def test_scenario6_invalid_request_rejected_by_schema():
    with pytest.raises(ValidationError):
        GeoIntelligenceRequest(question="質問のみ")  # requester_app/request_type欠如


# ---- Scenario 7: 内部処理失敗 → structured error ----

def test_scenario7_internal_failure_returns_structured_error(monkeypatch):
    client = FakeSupabaseClient(tables=_sufficient_tables())

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(service, "retrieve_knowledge", _boom)
    response = service.process_request(client, _base_request())

    assert response["status"] == "failed"
    assert response["error"] is not None
    assert response["error"]["code"] == "RuntimeError"
    assert "simulated failure" in response["error"]["message"]


# ---- idempotency: 同一request_idの再送は再処理せず既存Responseを返す ----

def test_idempotent_retry_returns_existing_response_without_reprocessing():
    client = FakeSupabaseClient(tables=_sufficient_tables())
    request = _base_request(request_id="fixed-request-id-1")

    r1 = service.process_request(client, request)
    r2 = service.process_request(client, request)

    assert r1 == r2
    assert len(client.tables["geo_intelligence_requests"]) == 1
    assert len(client.tables["geo_intelligence_responses"]) == 1


# ---- Expert AIはKnowledge不足を補完しない（item9原則の再確認） ----

def test_expert_ai_never_invoked_to_fill_insufficient_or_partial_knowledge():
    azure_client = _mock_llm_client()

    tables_partial = _sufficient_tables()
    tables_partial["geo_relationships"] = []
    tables_partial["geo_knowledge_items"] = []
    tables_partial["geo_knowledge_item_issues"] = []
    client_partial = FakeSupabaseClient(tables=tables_partial)
    resp_partial = service.process_request(client_partial, _base_request(),
                                            azure_client=azure_client, model="gpt-4o")
    assert resp_partial["knowledge_sufficiency"] == "partial"

    client_insufficient = FakeSupabaseClient()
    resp_insufficient = service.process_request(client_insufficient, _base_request(
        country_region=["架空の国XYZ"], themes=[]), azure_client=azure_client, model="gpt-4o")
    assert resp_insufficient["knowledge_sufficiency"] == "insufficient"

    azure_client.chat.completions.create.assert_not_called()
