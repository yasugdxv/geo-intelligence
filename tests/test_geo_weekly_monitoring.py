"""地政学インテリジェンス基盤: Geo Intelligence API Contract拡張（weekly_monitoring）。
Test 1〜15 + E2E + Sustainability Contract Test + Regression。
"""
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_intelligence_service as service  # noqa: E402
from geo_intelligence_schema import GeoIntelligenceRequest  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

INDIA_ID = "entity-india"
CHINA_ID = "entity-china"
ISSUE_INDIA = "issue-india"
ISSUE_CHINA = "issue-china"


def _base_weekly_request(**overrides) -> dict:
    req = {
        "requester_app": "sustainability", "request_type": "weekly_monitoring",
        "period_start": "2026-08-17", "period_end": "2026-08-23",
        "sustainability_themes": ["Water", "Raw Materials"], "priority_geographies": ["India"],
        "business_context": "Weekly Sustainability Intelligence monitoring", "analysis_requested": True,
    }
    req.update(overrides)
    return req


def _claim(claim_id, text, valid_from, information_class="observed_fact"):
    return {"claim_id": claim_id, "claim_text": text, "information_class": information_class,
            "retrieval_status": "available", "is_demo": False, "valid_from": valid_from, "source_id": None}


def _india_tables(*claims, extra_entity_issue=None) -> dict:
    tables = {
        "geo_entities": [{"entity_id": INDIA_ID, "canonical_name": "India", "entity_type": "country",
                          "is_demo": False}],
        "geo_entity_issues": [{"entity_id": INDIA_ID, "issue_id": ISSUE_INDIA}],
        "geo_claims": list(claims),
        "geo_claim_issues": [{"claim_id": c["claim_id"], "issue_id": ISSUE_INDIA} for c in claims],
    }
    if extra_entity_issue:
        tables["geo_entities"].append(extra_entity_issue[0])
        tables["geo_entity_issues"].append(extra_entity_issue[1])
    return tables


# ---- Test 1: Request Type ----

def test_case1_weekly_monitoring_request_type_validates():
    req = GeoIntelligenceRequest(**_base_weekly_request())
    assert req.request_type.value == "weekly_monitoring"
    assert req.question is None


# ---- Test 2: 既存user_question regression ----

def test_case2_existing_user_question_unaffected():
    client = FakeSupabaseClient(tables={
        "geo_entities": [{"entity_id": INDIA_ID, "canonical_name": "India", "entity_type": "country",
                          "is_demo": False}],
    })
    response = service.process_request(client, {
        "requester_app": "sustainability_intelligence", "request_type": "user_question",
        "question": "What is happening in India?", "country_region": ["India"], "analysis_requested": False,
    })
    assert response.get("relevant_intelligence") is None  # user_questionでは省略/Noneのまま
    assert response["status"] in ("completed", "partial")


# ---- Test 3: 複数Relevant Intelligence ----

def test_case3_multiple_relevant_intelligence_items():
    client = FakeSupabaseClient(tables=_india_tables(
        _claim("c1", "India water regulation tightened amid political pressure", "2026-08-18"),
        _claim("c2", "India raw material export tariff policy announced", "2026-08-19"),
    ))
    response = service.process_request(client, _base_weekly_request())
    assert response["status"] == "completed"
    assert len(response["relevant_intelligence"]) == 2


# ---- Test 4: 0件正常 ----

def test_case4_zero_results_is_normal_sufficient():
    client = FakeSupabaseClient(tables=_india_tables(
        _claim("c1", "Unrelated cultural festival held in India", "2026-08-18"),
    ))
    response = service.process_request(client, _base_weekly_request())
    assert response["relevant_intelligence"] == []
    assert response["knowledge_sufficiency"] == "sufficient"
    assert response["knowledge_gap"]["exists"] is False


# ---- Test 5: partial ----

def test_case5_partial_when_some_geography_unresolved_but_items_returned(monkeypatch):
    """FakeSupabaseClientのilike疑似実装は常に全件マッチするため、実在しない地域名でも
    誤ってEntityが解決してしまう（実Postgresでは正しくフィルタされる、既知の制約）。
    ここではresolve_entities_and_issuesを差し替え、Indiaのみ解決・他2地域は未解決という
    状況を確定的に再現する。"""
    client = FakeSupabaseClient(tables=_india_tables(
        _claim("c1", "India water regulation tightened amid political pressure", "2026-08-18"),
    ))
    original = service.resolve_entities_and_issues

    def fake_resolve(client_, normalized_):
        if normalized_["country_region"] == ["India"]:
            return original(client_, normalized_)
        return {"entities": [], "issue_ids": []}

    monkeypatch.setattr(service, "resolve_entities_and_issues", fake_resolve)
    response = service.process_request(client, _base_weekly_request(
        priority_geographies=["India", "Nonexistent Country A", "Nonexistent Country B"]))
    assert response["knowledge_sufficiency"] == "partial"
    assert len(response["relevant_intelligence"]) == 1


# ---- Test 6: insufficient ----

def test_case6_insufficient_when_no_geography_resolves():
    client = FakeSupabaseClient()
    response = service.process_request(client, _base_weekly_request(priority_geographies=["架空の国XYZ"]))
    assert response["knowledge_sufficiency"] == "insufficient"
    assert response["knowledge_gap"]["exists"] is True
    gaps = client.tables["geo_knowledge_gaps"]
    assert gaps[0]["requested_by_app"] == "sustainability"
    assert gaps[0]["origin_type"] == "external_app_request"


# ---- Test 7: 重複Knowledge統合 ----

def test_case7_duplicate_events_merged_with_multiple_references():
    client = FakeSupabaseClient(tables=_india_tables(
        _claim("c1", "India water regulation policy tightened amid political pressure over groundwater", "2026-08-18"),
        _claim("c2", "India water regulation policy tightened amid political pressure over groundwater use", "2026-08-19"),
    ))
    response = service.process_request(client, _base_weekly_request())
    assert len(response["relevant_intelligence"]) == 1
    assert len(response["relevant_intelligence"][0]["references"]) == 2
    assert response["relevant_intelligence"][0]["confidence"] == "high"


# ---- Test 8: Period ----

def test_case8_out_of_period_knowledge_excluded():
    client = FakeSupabaseClient(tables=_india_tables(
        _claim("c1", "India water regulation policy news from last year", "2025-01-01"),
    ))
    response = service.process_request(client, _base_weekly_request())
    assert response["relevant_intelligence"] == []


def test_case8b_no_date_knowledge_not_treated_as_this_week():
    client = FakeSupabaseClient(tables=_india_tables(
        _claim("c1", "India water regulation policy with unknown date", None),
    ))
    response = service.process_request(client, _base_weekly_request())
    assert response["relevant_intelligence"] == []


# ---- Test 9: Theme（無関係なGeo Eventを返さない） ----

def test_case9_irrelevant_geo_event_excluded_by_theme_relevance():
    client = FakeSupabaseClient(tables=_india_tables(
        _claim("c1", "India national cricket team wins friendly match", "2026-08-18"),
    ))
    response = service.process_request(client, _base_weekly_request())
    assert response["relevant_intelligence"] == []


# ---- Test 10: Geography（Priority geographyのRanking反映） ----

def test_case10_priority_geography_reflected_in_ranking(monkeypatch):
    """FakeSupabaseClientのilike疑似実装は常に全件マッチするため、'India'のみで検索しても
    Chinaまで誤って解決されてしまう（実Postgresでは正しくフィルタされる、既知の制約）。
    resolve_entities_and_issuesを差し替え、地域ごとに正しいEntityだけが解決される状況を
    確定的に再現する。"""
    client = FakeSupabaseClient(tables={
        "geo_entities": [
            {"entity_id": INDIA_ID, "canonical_name": "India", "entity_type": "country", "is_demo": False},
            {"entity_id": CHINA_ID, "canonical_name": "China", "entity_type": "country", "is_demo": False},
        ],
        "geo_entity_issues": [
            {"entity_id": INDIA_ID, "issue_id": ISSUE_INDIA},
            {"entity_id": CHINA_ID, "issue_id": ISSUE_CHINA},
        ],
        "geo_claims": [
            _claim("c1", "China raw material export tariff policy announced", "2026-08-18"),
            _claim("c2", "India raw material export tariff policy announced", "2026-08-18"),
        ],
        "geo_claim_issues": [
            {"claim_id": "c1", "issue_id": ISSUE_CHINA},
            {"claim_id": "c2", "issue_id": ISSUE_INDIA},
        ],
    })
    issue_by_geo = {"India": ISSUE_INDIA, "China": ISSUE_CHINA}
    entity_by_geo = {"India": INDIA_ID, "China": CHINA_ID}

    def fake_resolve(client_, normalized_):
        geo = normalized_["country_region"][0] if normalized_["country_region"] else None
        if geo in issue_by_geo:
            return {"entities": [{"entity_id": entity_by_geo[geo], "canonical_name": geo}],
                    "issue_ids": [issue_by_geo[geo]]}
        return {"entities": [], "issue_ids": []}

    monkeypatch.setattr(service, "resolve_entities_and_issues", fake_resolve)
    response = service.process_request(client, _base_weekly_request(priority_geographies=["India", "China"]))
    countries = [item["country_region"] for item in response["relevant_intelligence"]]
    assert countries[0] == ["India"]  # priority_geographiesの先頭(India)が上位


# ---- Test 11: Max Items ----

def test_case11_max_items_limit_respected(monkeypatch):
    monkeypatch.setattr(service, "weekly_monitoring_max_items", lambda: 2)
    claims = [_claim(f"c{i}", f"India water regulation policy update number {i}", "2026-08-18")
              for i in range(5)]
    # 完全一致だとdedupeで1件になってしまうため、少しずつ内容を変える
    for i, c in enumerate(claims):
        c["claim_text"] = f"India water resource regulation policy variant {i} tariff trade"
    client = FakeSupabaseClient(tables=_india_tables(*claims))
    response = service.process_request(client, _base_weekly_request())
    assert len(response["relevant_intelligence"]) <= 2
    assert response["truncated_count"] >= 0


# ---- Test 12: Expert AI（Top itemのみ） ----

def test_case12_expert_ai_used_only_for_top_items_with_corroboration(monkeypatch):
    import json
    from unittest.mock import MagicMock

    # v1.2段階実行方式は既定でStage1のSchemaが厳格化(strict Structured Output)されており、
    # 本テストの簡易フィクスチャはそれを満たさない。本テストの関心事（Top N件のみ補強する
    # という週次ロジック）とは無関係なため一括方式に固定する。
    monkeypatch.setenv("GEO_EXPERT_EXECUTION_MODE", "monolithic")

    well_formed = {
        "one_line_assessment": "統合された見立て。", "claims": [], "stakeholders": [{"actor": "India"}],
        "main_scenario": {"description": "outlook", "confidence": "中", "supporting_evidence": ["x"],
                           "selection_rationale": "根拠あり"},
        "strongest_counterargument": "反論", "retraction_conditions": ["条件"],
        "business_impact": {"direct": None, "indirect": None, "unrelated_note": None, "environment_change": None},
        # Session2: case_analysisモードの必須項目（geo_expert_quality_guard._key_stakeholder_v12_check）
        "key_point_assessment": {
            "candidate_key_points": [{
                "key_point": "水資源規制が強化されるか", "why_it_could_be_key": "政治的圧力の帰趨を左右するため",
                "control_type": "unknown", "what_it_explains": "規制強化の傾向", "what_it_does_not_explain": "実施時期",
            }],
            "primary_key_point": "水資源規制が強化されるか", "selection_rationale": "根拠あり",
            "hinge_structure": {"primary_hinge": "インド政府の方針"},
            "control_type": "unknown", "supporting_evidence_ids": None,
            "disconfirming_signal": "規制強化の動きが撤回された場合", "confidence": "中",
        },
        # v1.2: case_analysisモードの必須項目（geo_expert_quality_guard._key_stakeholder_v12_check）
        "key_stakeholder_assessment": {
            "determination": "single", "selected_actor_ids": ["india"],
            "rationale": "水資源規制の最終決定権を持つため", "confidence": "中",
        },
        "key_actor_profiles": [{"actor_ref": "india", "actor": "India", "confidence": "中"}],
    }
    azure_client = MagicMock()
    azure_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(well_formed)))])

    client = FakeSupabaseClient(tables=_india_tables(
        _claim("c1", "India water regulation policy tightened amid political pressure", "2026-08-18"),
        _claim("c2", "India water regulation policy tightened amid pressure groundwater", "2026-08-19"),
    ))
    response = service.process_request(client, _base_weekly_request(),
                                       azure_client=azure_client, model="gpt-4o")
    assert len(response["relevant_intelligence"]) == 1
    assert len(response["relevant_intelligence"][0]["references"]) == 2  # 裏付け2件→Expert AI対象
    azure_client.chat.completions.create.assert_called_once()
    assert response["relevant_intelligence"][0]["political_dynamics"] == "outlook" or \
        response["relevant_intelligence"][0]["outlook"] == "outlook"


def test_case12b_expert_ai_not_used_for_single_source_items(monkeypatch):
    import json
    from unittest.mock import MagicMock
    azure_client = MagicMock()
    azure_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps({
            "one_line_assessment": "x", "claims": [], "stakeholders": [],
            "main_scenario": {"description": "x", "confidence": "中", "supporting_evidence": []},
            "strongest_counterargument": "x", "retraction_conditions": [],
        })))])
    client = FakeSupabaseClient(tables=_india_tables(
        _claim("c1", "India water regulation policy tightened amid political pressure", "2026-08-18"),
    ))
    response = service.process_request(client, _base_weekly_request(),
                                       azure_client=azure_client, model="gpt-4o")
    azure_client.chat.completions.create.assert_not_called()


# ---- Test 13: Knowledge不足をExpert AIで補完しない ----

def test_case13_expert_ai_not_used_when_insufficient():
    from unittest.mock import MagicMock
    azure_client = MagicMock()
    client = FakeSupabaseClient()
    response = service.process_request(client, _base_weekly_request(priority_geographies=["架空の国XYZ"]),
                                       azure_client=azure_client, model="gpt-4o")
    assert response["knowledge_sufficiency"] == "insufficient"
    azure_client.chat.completions.create.assert_not_called()


# ---- Test 14: Traceability ----

def test_case14_traceability_request_to_gap():
    client = FakeSupabaseClient()
    response = service.process_request(client, _base_weekly_request(priority_geographies=["架空の国XYZ"]))
    saved_request = client.tables["geo_intelligence_requests"][0]
    assert saved_request["request_id"] == response["request_id"]
    saved_response = client.tables["geo_intelligence_responses"][0]
    assert saved_response["request_id"] == response["request_id"]
    gap = client.tables["geo_knowledge_gaps"][0]
    gap_link = client.tables["geo_knowledge_gap_requests"][0]
    assert gap_link["request_id"] == response["request_id"]
    assert gap_link["gap_id"] == gap["gap_id"]


# ---- Test 15: 既存Geo Intelligence APIリグレッション ----

def test_case15_regression_full_user_question_suite_still_passes():
    """既存test_geo_intelligence_service*.pyがこのファイル内では実行されないため、
    ここでは代表的な既存フローが引き続き動作することだけ確認する
    （フルリグレッションはpytest tests/ 全体で検証）。"""
    client = FakeSupabaseClient(tables={
        "geo_entities": [{"entity_id": INDIA_ID, "canonical_name": "India", "entity_type": "country",
                          "is_demo": False}],
    })
    response = service.process_request(client, {
        "requester_app": "x", "request_type": "user_question", "question": "test question",
        "country_region": ["India"], "analysis_requested": True,
    })
    assert "knowledge_sufficiency" in response


# ---- Config Kill Switch ----

def test_weekly_monitoring_disabled_returns_clear_disabled_response(monkeypatch):
    monkeypatch.setattr(service, "weekly_monitoring_enabled", lambda: False)
    client = FakeSupabaseClient(tables=_india_tables(
        _claim("c1", "India water regulation policy tightened amid political pressure", "2026-08-18"),
    ))
    response = service.process_request(client, _base_weekly_request())
    assert response["status"] == "failed"
    assert response["error"]["code"] == "weekly_monitoring_disabled"


def test_weekly_monitoring_disabled_does_not_affect_user_question(monkeypatch):
    monkeypatch.setattr(service, "weekly_monitoring_enabled", lambda: False)
    client = FakeSupabaseClient(tables={
        "geo_entities": [{"entity_id": INDIA_ID, "canonical_name": "India", "entity_type": "country",
                          "is_demo": False}],
    })
    response = service.process_request(client, {
        "requester_app": "x", "request_type": "user_question", "question": "test",
        "country_region": ["India"], "analysis_requested": False,
    })
    assert response["status"] in ("completed", "partial")


# ---- Invalid request ----

def test_invalid_weekly_request_missing_requester_app_rejected():
    with pytest.raises(ValidationError):
        GeoIntelligenceRequest(request_type="weekly_monitoring")


# ---- E2E: A重要/B無関係/C同一Event別Reference/D期間外 ----

def test_e2e_scope_a_relevant_b_irrelevant_c_duplicate_d_out_of_period():
    client = FakeSupabaseClient(tables=_india_tables(
        _claim("a", "India groundwater regulation policy tightened amid political pressure", "2026-08-18"),
        _claim("b", "India national holiday celebration announced", "2026-08-19"),
        _claim("c", "India groundwater regulation policy tightened amid political pressure again", "2026-08-20"),
        _claim("d", "India groundwater regulation policy news last year", "2025-01-01"),
    ))
    response = service.process_request(client, _base_weekly_request())

    assert len(response["relevant_intelligence"]) == 1  # A+C統合
    assert len(response["relevant_intelligence"][0]["references"]) == 2
    all_ref_ids = {r["reference_id"] for r in response["relevant_intelligence"][0]["references"]}
    assert all_ref_ids == {"a", "c"}
    assert "b" not in all_ref_ids and "d" not in all_ref_ids
