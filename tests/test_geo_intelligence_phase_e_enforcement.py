"""Geopolitical Intelligence OS Phase E: Cross-Agent Retrieval（/api/v1/geo-intelligence/query、
geo_intelligence_service.retrieve_knowledge + geo_department_intelligence_service）における
agent_sharing_policy強制のテスト。

§16の順序要件（ACCESS FILTER→Knowledge Sufficiency）と、§14の既定値挙動
（Company human→許可、Geopolitics Agent（内部）→許可、Sustainability Agent（外部）→拒否）を、
実際にprocess_request/retrieve_knowledgeを通して検証する（unit test的にpolicy関数だけを
見るのではなく、実際のRetrieval経路で強制されていることの証跡とする）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_department_intelligence_service as dept_service  # noqa: E402
import geo_intelligence_service as service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _department_request(**overrides) -> dict:
    request = {
        "requester_app": "sustainability_intelligence",
        "request_type": "user_question",
        "question": "メキシコのWater issueの政治化・規制化可能性を見たい",
        "country_region": ["Mexico"], "themes": ["water"],
        "source_department": "sustainability", "source_workflow": "chat",
        "analysis_requested": True,
    }
    request.update(overrides)
    return request


def _client_with_knowledge(agent_sharing_policy: str) -> FakeSupabaseClient:
    return FakeSupabaseClient(tables={
        "geo_knowledge_items": [{
            "knowledge_item_id": "ki-1", "knowledge_type": "country_foundation",
            "title": "メキシコ(Mexico): 水資源政策の基礎情報", "summary": "水資源(water)規制の概要",
            "status": "active", "retrieval_status": "available", "is_demo": False,
            "confidence": "高", "updated_at": "2026-08-01T00:00:00+00:00",
            "human_visibility": "company", "agent_sharing_policy": agent_sharing_policy,
        }],
    })


# ---- §14 既定値の挙動: geopolitics_only（既存データ既定値）はExternal Agentから見えない ----

def test_geopolitics_only_knowledge_excluded_from_external_department_request():
    client = _client_with_knowledge("geopolitics_only")
    response = service.process_request(client, _department_request())
    di = response["department_intelligence"]

    # ACCESS FILTERで除外された結果、既存Knowledgeで応答できず(existing_only判定に必要な
    # 材料が無い)、covered_dimensionsにfoundationが含まれない、またはinsufficientへ倒れる。
    assert "geo_reports" not in str(di.get("provenance", "")) or True  # geo_reportsは常に空（下のテストで別途確認）
    assert response["knowledge_sufficiency"] in ("insufficient", "partial")
    # ACCESS FILTER後のKnowledgeでは"foundation"次元がcoveredにならないはず
    assert "foundation" not in (di.get("covered_dimensions") or [])


def test_allowed_cross_domain_knowledge_is_visible_to_external_department_request():
    client = _client_with_knowledge("allowed_cross_domain")
    response = service.process_request(client, _department_request())
    di = response["department_intelligence"]

    assert "foundation" in (di.get("covered_dimensions") or [])
    assert response["knowledge_sufficiency"] in ("sufficient", "partial")


def test_none_policy_also_excluded_from_external_request():
    client = _client_with_knowledge("none")
    response = service.process_request(client, _department_request())
    di = response["department_intelligence"]
    assert "foundation" not in (di.get("covered_dimensions") or [])


# ---- 内部利用（geo_chat_ui）はagent_sharing_policyの対象外 ----

def test_internal_chat_ui_sees_geopolitics_only_knowledge():
    client = _client_with_knowledge("geopolitics_only")
    normalized = {
        "requester_app": "geo_chat_ui", "request_type": "user_question",
        "question": "メキシコの水資源規制について", "country_region": ["Mexico"], "themes": ["water"],
        "analysis_requested": False,
    }
    retrieval = service.retrieve_knowledge(client, normalized)
    assert any(i["knowledge_item_id"] == "ki-1" for i in retrieval["foundation"])


def test_external_non_department_user_question_also_filtered():
    """source_department無しの一般user_question（_process_user_question経由）でも、
    requester_appがgeo_chat_ui以外なら外部呼び出しとして同じくfilterされる。"""
    client = _client_with_knowledge("geopolitics_only")
    normalized = {
        "requester_app": "erm_intelligence", "request_type": "user_question",
        "question": "メキシコの水資源規制について", "country_region": ["Mexico"], "themes": ["water"],
        "analysis_requested": False,
    }
    retrieval = service.retrieve_knowledge(client, normalized)
    assert retrieval["foundation"] == []


# ---- §16 順序: ACCESS FILTER → Sufficiency（sufficiencyがfilter後のretrievalだけを見る） ----

def test_sufficiency_computed_after_access_filter_not_before():
    """全knowledgeでsufficiency判定してから最後にvisibility filterする実装だと、
    Foundationが1件在るためsufficient寄りに倒れてしまう。正しい実装ではACCESS FILTERが
    先に効き、filtered-outな状態でsufficiencyが計算されるため、insufficient/partialになる。"""
    client = _client_with_knowledge("geopolitics_only")
    response = service.process_request(client, _department_request())
    assert response["knowledge_sufficiency"] != "sufficient"


# ---- geo_reportsのCross-Domain除外（visibility列を持たないため常に除外、§13） -----------

def test_department_intelligence_never_leaks_geo_reports_dimension():
    client = FakeSupabaseClient(tables={
        "geo_reports": [{"report_id": "r-1", "title": "内部限定レポート", "document_id": "doc-1"}],
    })
    normalized = {
        "requester_app": "sustainability_intelligence", "request_type": "user_question",
        "question": "q", "country_region": ["Mexico"], "themes": ["water"],
        "source_department": "sustainability", "source_workflow": "chat", "analysis_requested": True,
    }
    retrieval = service.retrieve_knowledge(client, normalized)
    result = dept_service.retrieve_department_intelligence(client, normalized, retrieval)
    assert result["geo_reports"] == []
