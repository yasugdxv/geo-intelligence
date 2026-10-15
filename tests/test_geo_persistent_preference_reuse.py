"""地政学専門家AI v1.2 Persistent Preference再利用サービスのテスト。

geo_persistent_preference_serviceが、既存Active actor_profileの再利用（読み取り、常時有効）と、
Candidate自動生成（Feature Flag既定無効、フィンガープリント重複防止・実質的変更判定・
未処理Candidate重複防止）を仕様通り満たすことを検証する。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_persistent_preference_service as pp  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

ENTITY_ID = "iran-entity-1"

ACTIVE_ITEM = {
    "knowledge_item_id": "ki-active-1", "title": "Iran Actor Profile", "status": "active",
    "information_class": "internal_hypothesis",
    "structured_content": {
        "entity_id": ENTITY_ID, "objectives": ["体制維持"], "core_interests": ["カード保持"],
        "persistent_preference": {
            "statement": "カード保持＋全面封鎖回避", "as_of": "2026-08-01", "confidence": "中",
            "supporting_evidence": [], "contradicting_evidence": [],
            "evidence_fingerprints": [pp._evidence_fingerprint(
                {"event_or_behavior": "過去の交渉", "date_or_period": "2026-01", "source": "既存資料"})],
        },
    },
}

FOUNDATION_PACK = {"5_objectives_core_interests": [ACTIVE_ITEM]}


def _kap(**overrides) -> dict:
    base = {
        "actor_ref": "iran", "actor": "Iran", "entity_id": ENTITY_ID, "confidence": "中",
        "persistent_preference": "カード保持＋段階的圧力", "persistent_preference_change": "revised",
        "persistent_preference_evidence": [
            {"event_or_behavior": "新しい交渉行動", "date_or_period": "2026-08", "source": "新規資料",
             "supports_or_contradicts": "supports"},
        ],
    }
    base.update(overrides)
    return base


def test_get_active_actor_profile_matches_by_entity_id():
    item = pp.get_active_actor_profile(FOUNDATION_PACK, ENTITY_ID)
    assert item is not None
    assert item["knowledge_item_id"] == "ki-active-1"


def test_get_active_actor_profile_returns_none_when_no_match():
    assert pp.get_active_actor_profile(FOUNDATION_PACK, "unknown-entity") is None


def test_format_persistent_preference_block_includes_existing_statement():
    block = pp.format_persistent_preference_block(FOUNDATION_PACK, {ENTITY_ID: "Iran"})
    assert "カード保持＋全面封鎖回避" in block
    assert "Iran" in block


def test_format_persistent_preference_block_empty_when_no_active_profile():
    assert pp.format_persistent_preference_block({"5_objectives_core_interests": []}, {}) == ""


def test_candidate_not_created_when_flag_disabled(monkeypatch):
    monkeypatch.delenv(pp.PP_CANDIDATES_ENABLED_ENV, raising=False)
    client = FakeSupabaseClient()
    response_json = {"key_actor_profiles": [_kap()]}
    created = pp.propose_persistent_preference_candidates(
        client, response_json=response_json, foundation_pack=FOUNDATION_PACK, actor_id="tester")
    assert created == []
    assert client.tables.get("geo_knowledge_items", []) == []


def test_candidate_created_when_flag_enabled_and_revised_with_new_evidence(monkeypatch):
    monkeypatch.setenv(pp.PP_CANDIDATES_ENABLED_ENV, "true")
    client = FakeSupabaseClient()
    response_json = {"key_actor_profiles": [_kap()]}
    created = pp.propose_persistent_preference_candidates(
        client, response_json=response_json, foundation_pack=FOUNDATION_PACK, actor_id="tester")
    assert len(created) == 1
    assert created[0]["status"] == "candidate"
    assert created[0]["supersedes_id"] == "ki-active-1"
    # geo_knowledge_items_origin_check（sql/2026-08-19_geo_phase2b2_external_reference.sql）が
    # 許可する値のみ使うこと（実データ検証で、独自文字列を使うと400エラーになることが判明したため
    # 回帰しないようにする）
    assert created[0]["origin"] in (
        "manual", "connector_derived", "case_candidate_extraction",
        "external_claim_extraction", "internal_expert_review",
    )
    # 既存Activeはそのまま維持される(FakeSupabaseClientの元テーブルには手を加えていない)
    assert ACTIVE_ITEM["status"] == "active"


def test_candidate_not_created_when_change_is_reinforced_not_revised(monkeypatch):
    monkeypatch.setenv(pp.PP_CANDIDATES_ENABLED_ENV, "true")
    client = FakeSupabaseClient()
    response_json = {"key_actor_profiles": [_kap(persistent_preference_change="reinforced")]}
    created = pp.propose_persistent_preference_candidates(
        client, response_json=response_json, foundation_pack=FOUNDATION_PACK, actor_id="tester")
    assert created == []


def test_candidate_not_created_when_evidence_fingerprint_already_recorded(monkeypatch):
    monkeypatch.setenv(pp.PP_CANDIDATES_ENABLED_ENV, "true")
    client = FakeSupabaseClient()
    # 既存fingerprintと同一のEvidence(重複)のみを持つケース
    duplicate_evidence = {"event_or_behavior": "過去の交渉", "date_or_period": "2026-01", "source": "既存資料",
                           "supports_or_contradicts": "supports"}
    response_json = {"key_actor_profiles": [_kap(persistent_preference_evidence=[duplicate_evidence])]}
    created = pp.propose_persistent_preference_candidates(
        client, response_json=response_json, foundation_pack=FOUNDATION_PACK, actor_id="tester")
    assert created == []


def test_candidate_not_created_when_pending_candidate_already_exists(monkeypatch):
    monkeypatch.setenv(pp.PP_CANDIDATES_ENABLED_ENV, "true")
    client = FakeSupabaseClient(tables={"geo_knowledge_items": [{
        "knowledge_item_id": "ki-pending-1", "status": "candidate", "knowledge_type": "actor_profile",
        "structured_content": {"entity_id": ENTITY_ID},
    }]})
    response_json = {"key_actor_profiles": [_kap()]}
    created = pp.propose_persistent_preference_candidates(
        client, response_json=response_json, foundation_pack=FOUNDATION_PACK, actor_id="tester")
    assert created == []


def test_candidate_not_created_when_no_active_profile_exists(monkeypatch):
    monkeypatch.setenv(pp.PP_CANDIDATES_ENABLED_ENV, "true")
    client = FakeSupabaseClient()
    response_json = {"key_actor_profiles": [_kap(entity_id="no-such-entity")]}
    created = pp.propose_persistent_preference_candidates(
        client, response_json=response_json, foundation_pack=FOUNDATION_PACK, actor_id="tester")
    assert created == []
