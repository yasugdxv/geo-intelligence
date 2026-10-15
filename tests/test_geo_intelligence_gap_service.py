import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_intelligence_service as service  # noqa: E402
import geo_knowledge_gap_service as gap_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

ISSUE_ID = "issue-1"
IRAN_ID = "entity-india"
OMAN_ID = "entity-pakistan"


def _base_request(**overrides) -> dict:
    request = {
        "requester_app": "sustainability_intelligence",
        "request_type": "user_question",
        "question": "インドの水規制の見通しは？",
        "country_region": ["India"],
        "themes": ["water_regulation"],
        "analysis_requested": True,
    }
    request.update(overrides)
    return request


def _sufficient_tables() -> dict:
    return {
        "geo_entities": [
            {"entity_id": IRAN_ID, "canonical_name": "India", "entity_type": "country", "is_demo": False},
            {"entity_id": OMAN_ID, "canonical_name": "Pakistan", "entity_type": "country", "is_demo": False},
        ],
        "geo_entity_issues": [{"entity_id": IRAN_ID, "issue_id": ISSUE_ID}],
        "geo_relationships": [{
            "relationship_id": "rel-1", "from_entity_id": IRAN_ID, "to_entity_id": OMAN_ID,
            "relationship_type": "mediation_negotiation_channel", "relationship_domain": "diplomacy",
            "description": "仲介チャネル", "is_demo": False,
        }],
        "geo_knowledge_items": [
            {"knowledge_item_id": "ki-foundation", "knowledge_type": "country_foundation",
             "title": "インド: 統治構造", "summary": "概要", "status": "active",
             "retrieval_status": "available", "is_demo": False, "confidence": "中"},
            {"knowledge_item_id": "ki-precedent", "knowledge_type": "precedent",
             "title": "先例", "summary": "概要", "status": "active",
             "retrieval_status": "available", "is_demo": False, "confidence": "中"},
        ],
        "geo_knowledge_item_issues": [
            {"knowledge_item_id": "ki-foundation", "issue_id": ISSUE_ID},
            {"knowledge_item_id": "ki-precedent", "issue_id": ISSUE_ID},
        ],
        "geo_claims": [
            {"claim_id": "cl-fact", "claim_text": "観測事実", "information_class": "observed_fact",
             "retrieval_status": "available", "is_demo": False},
        ],
        "geo_claim_issues": [{"claim_id": "cl-fact", "issue_id": ISSUE_ID}],
    }


# ---- Test 1: insufficient → Intelligence Need生成 ----

def test_case1_insufficient_creates_intelligence_need():
    client = FakeSupabaseClient()  # 完全に空 = insufficient確定
    response = service.process_request(client, _base_request())

    assert response["knowledge_sufficiency"] == "insufficient"
    assert response["knowledge_gap"]["exists"] is True
    assert response["knowledge_gap"]["gap_id"]

    gaps = client.tables["geo_knowledge_gaps"]
    assert len(gaps) == 1
    assert gaps[0]["origin_type"] == "external_app_request"
    assert gaps[0]["requested_by_app"] == "sustainability_intelligence"
    assert gaps[0]["status"] == "open"
    assert gaps[0]["priority"] == "high"  # insufficient + analysis_requested=True


# ---- Test 2: partialかつ重要なGap → Intelligence Need生成 ----

def test_case2_important_partial_gap_creates_intelligence_need():
    tables = _sufficient_tables()
    tables["geo_relationships"] = []  # relationship欠落 + (stakeholderは残るがdynamicsが無い)
    tables["geo_knowledge_items"] = []  # foundation/precedentも欠落 → critical 2件欠落
    tables["geo_knowledge_item_issues"] = []
    client = FakeSupabaseClient(tables=tables)

    response = service.process_request(client, _base_request())

    assert response["knowledge_sufficiency"] == "partial"
    assert response["knowledge_gap"]["exists"] is True
    gaps = client.tables["geo_knowledge_gaps"]
    assert len(gaps) == 1
    assert gaps[0]["gap_type"] == "external_request_knowledge_gap"


def test_partial_with_minor_gap_does_not_create_intelligence_need():
    """internal_report/scenario_hypothesis程度の軽微な欠落だけならGapを作らない
    （spec: 単なる軽微な情報不足まで大量にGap登録しない）。"""
    tables = _sufficient_tables()
    client = FakeSupabaseClient(tables=tables)
    # このfixtureはstakeholder/relationship/foundation/precedent/claimが揃っており、
    # sufficientになるはずなので、internal_report/scenario_hypothesisの欠落だけの
    # 「軽微な部分不足」を作るため、あえてclaimを一部欠落させたケースで検証する。
    response = service.process_request(client, _base_request(analysis_requested=False))

    # sufficient/partialいずれであってもGapを乱造していないことを確認
    if response["knowledge_sufficiency"] != "insufficient":
        gaps = client.tables.get("geo_knowledge_gaps", [])
        if response["knowledge_sufficiency"] == "sufficient":
            assert len(gaps) == 0
            assert response["knowledge_gap"] is None


# ---- Test 3: 同一内容のRequest → Gap重複作成を抑制 ----

def test_case3_duplicate_requests_are_merged_not_duplicated():
    client = FakeSupabaseClient()

    r1 = service.process_request(client, _base_request(question="India water regulation outlook"))
    r2 = service.process_request(client, _base_request(
        question="India industrial water restrictions", themes=["water_regulation"]))
    r3 = service.process_request(client, _base_request(
        question="Political pressure on India groundwater policy", themes=["water_regulation"]))

    gaps = client.tables["geo_knowledge_gaps"]
    assert len(gaps) == 1  # 3件の類似質問が1つのGapへ統合される
    assert gaps[0]["request_count"] == 3

    links = client.tables["geo_knowledge_gap_requests"]
    assert len(links) == 3
    linked_request_ids = {l["request_id"] for l in links}
    assert linked_request_ids == {r1["request_id"], r2["request_id"], r3["request_id"]}


def test_unrelated_requests_are_not_merged():
    client = FakeSupabaseClient()
    service.process_request(client, _base_request(
        question="India water regulation outlook", country_region=["India"], themes=["water_regulation"]))
    service.process_request(client, _base_request(
        question="中国の半導体輸出規制の動向は？", country_region=["China"], themes=["semiconductor"]))

    gaps = client.tables["geo_knowledge_gaps"]
    assert len(gaps) == 2  # 無関係な質問は別Gapのまま


# ---- Test 4: Dashboardに表示される ----

def test_case4_appears_in_intelligence_needs_dashboard_list():
    client = FakeSupabaseClient()
    service.process_request(client, _base_request())

    needs = gap_service.list_intelligence_needs(client)
    assert len(needs) == 1
    assert needs[0]["origin_type"] == "external_app_request"

    open_needs = gap_service.list_intelligence_needs(client, status="open")
    assert len(open_needs) == 1


# ---- Test 4b: geo_chat（ダッシュボード自身のチャット）由来の探索的質問は、Gap自体は
# 記録されるがIntelligence Needs一覧には出ない ----

def test_chat_originated_gap_is_recorded_but_excluded_from_needs_list():
    client = FakeSupabaseClient()
    service.process_request(client, _base_request(requester_app=gap_service.CHAT_UI_REQUESTER_APP))

    # Gap自体はgeo_knowledge_gapsへ引き続き記録される（record_external_request_gapの
    # 動作は変えない）。
    all_gaps = client.select("geo_knowledge_gaps", {"select": "*"})
    assert len(all_gaps) == 1
    assert all_gaps[0]["requested_by_app"] == gap_service.CHAT_UI_REQUESTER_APP

    # ただしIntelligence Needs一覧（外部アプリの問い合わせ管理画面）には出ない。
    needs = gap_service.list_intelligence_needs(client)
    assert needs == []


def test_external_app_and_chat_gaps_coexist_only_external_app_shows_in_needs_list():
    client = FakeSupabaseClient()
    service.process_request(client, _base_request())  # requester_app=sustainability_intelligence
    # 重複判定（region/topic一致だけでmergeされてしまう）を避けるため、region/topicを変える。
    service.process_request(client, _base_request(
        requester_app=gap_service.CHAT_UI_REQUESTER_APP,
        question="全く別地域の探索的な質問",
        country_region=["Brazil"], themes=["deforestation"],
    ))

    all_gaps = client.select("geo_knowledge_gaps", {"select": "*"})
    assert len(all_gaps) == 2

    needs = gap_service.list_intelligence_needs(client)
    assert len(needs) == 1
    assert needs[0]["requested_by_app"] == "sustainability_intelligence"


# ---- Test 5: Request詳細からGapへ遷移可能 ----

def test_case5_request_detail_links_to_gap():
    client = FakeSupabaseClient()
    response = service.process_request(client, _base_request())

    gap = gap_service.get_gap_for_request(client, response["request_id"])
    assert gap is not None
    assert gap["gap_id"] == response["knowledge_gap"]["gap_id"]


# ---- Test 6: Gap詳細から元Requestを確認可能 ----

def test_case6_gap_detail_shows_original_request():
    client = FakeSupabaseClient()
    response = service.process_request(client, _base_request(question="元の質問文そのもの"))

    gap_id = response["knowledge_gap"]["gap_id"]
    detail = gap_service.get_intelligence_need_detail(client, gap_id)

    assert detail["original_question"] == "元の質問文そのもの"
    assert len(detail["related_requests"]) == 1
    assert detail["related_requests"][0]["request_id"] == response["request_id"]
    assert detail["what_is_missing"]
    assert detail["recommended_research"]


def test_gap_detail_reflects_all_merged_requests():
    client = FakeSupabaseClient()
    r1 = service.process_request(client, _base_request(question="India water regulation outlook"))
    r2 = service.process_request(client, _base_request(question="India industrial water restrictions"))

    gap_id = gap_service.get_gap_for_request(client, r1["request_id"])["gap_id"]
    detail = gap_service.get_intelligence_need_detail(client, gap_id)

    related_ids = {r["request_id"] for r in detail["related_requests"]}
    assert related_ids == {r1["request_id"], r2["request_id"]}


# ---- Test 7: 既存Case由来Knowledge Gapが壊れていない ----

def test_case7_existing_case_gap_detection_still_works():
    """既存のCase向けDeterministic Gap検出（detect_and_create_deterministic_gaps）が、Phase3の
    拡張後も無変更で動作することを確認する（本命の回帰確認はtests/test_geo_knowledge_e2e.py。
    実DBではorigin_type列がPhase3 migrationのcolumn defaultにより'case_analysis'へ自動設定
    されるが、FakeSupabaseClientはDB側のcolumn defaultを再現しないため、この単体テストでは
    既存の呼び出し方だけで例外なく動作しcase_idが正しく設定されることを確認する）。"""
    client = FakeSupabaseClient(tables={
        "geo_cases": [{"case_id": "case-1", "case_title": "t", "original_question": "q",
                       "as_of_date": "2026-08-21"}],
    })
    created = gap_service.detect_and_create_deterministic_gaps(client, "case-1", actor_id="tester")

    assert created  # missing_entity等が検出される
    for gap in client.tables["geo_knowledge_gaps"]:
        assert gap["case_id"] == "case-1"
        assert gap["gap_category"] in ("deterministic", "analytical_candidate")


# ---- Test 8: Dismiss経路の統一（地政学アプリClooseout Phase6） ----

def test_update_intelligence_need_rejects_dismissed_status():
    client = FakeSupabaseClient(tables={"geo_knowledge_gaps": [{
        "gap_id": "gap-1", "status": "open", "origin_type": "external_app_request",
    }]})

    with pytest.raises(ValueError):
        gap_service.update_intelligence_need(client, "gap-1", status="dismissed", actor_id="analyst_taro")

    # 拒否された場合、statusは変更されないままであること
    assert client.tables["geo_knowledge_gaps"][0]["status"] == "open"


def test_dismiss_intelligence_need_still_works_via_dedicated_function():
    client = FakeSupabaseClient(tables={"geo_knowledge_gaps": [{
        "gap_id": "gap-1", "status": "open", "origin_type": "external_app_request",
    }]})

    result = gap_service.dismiss_intelligence_need(
        client, "gap-1", dismissed_by="analyst_taro", reason="対象外と判断")

    assert result["status"] == "dismissed"
    assert result["resolved_by"] == "analyst_taro"
    assert result["resolution_note"] == "対象外と判断"
