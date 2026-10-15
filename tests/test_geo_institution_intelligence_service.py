"""Expert AI Phase R1 Chunk3: geo_institution_intelligence_service.pyのテスト。

Person Actorの仕組み（select_key_actors等の複雑な選定アルゴリズム）を模倣せず、
Foundation Packで既に解決済みのEntityから制度的なものだけを抽出する、というMVPの
意図的な単純化を確認する。Institution ProfileはPersonのように擬人化しない
（persistent_preference等のフィールドを一切持たない）ことも確認する。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_institution_intelligence_service as inst  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _base_client(**extra_tables) -> FakeSupabaseClient:
    tables = {"geo_knowledge_items": [], "geo_knowledge_reviews": [], "geo_entities": [], "geo_audit_logs": []}
    tables.update(extra_tables)
    return FakeSupabaseClient(tables)


_SEED_KWARGS = dict(
    institution_id="us_bis", canonical_name="Bureau of Industry and Security (BIS)",
    entity_type="ministry", mandate="輸出管理規則の執行",
    decision_authority={"description": "省令改定", "veto_players": [], "decision_process": "行政手続き"},
)


# ── institutional_entities_from_foundation_pack ───────────────────────────

def test_institutional_entities_filters_by_entity_type():
    foundation_pack = {
        "2_relevant_entities": [
            {"entity_id": "bis-id", "canonical_name": "BIS", "entity_type": "government"},
            {"entity_id": "trump-id", "canonical_name": "Donald Trump", "entity_type": "person"},
            {"entity_id": "nato-id", "canonical_name": "NATO", "entity_type": "international_organization"},
            {"entity_id": "hormuz-id", "canonical_name": "Strait of Hormuz", "entity_type": "strait"},
        ],
    }
    result = inst.institutional_entities_from_foundation_pack(foundation_pack)
    assert {e["entity_id"] for e in result} == {"bis-id", "nato-id"}


def test_institutional_entities_empty_when_foundation_pack_none():
    assert inst.institutional_entities_from_foundation_pack(None) == []


# ── get_institution_profile_item_by_entity_id ─────────────────────────────

def test_get_institution_profile_item_by_entity_id_finds_active():
    client = _base_client(geo_knowledge_items=[
        {"knowledge_item_id": "inst-1", "knowledge_type": "institution_profile", "status": "active",
         "retrieval_status": "available", "is_demo": False,
         "structured_content": {"entity_id": "bis-id", "mandate": "輸出管理規則の執行"}},
    ])
    found = inst.get_institution_profile_item_by_entity_id(client, "bis-id")
    assert found is not None
    assert found["structured_content"]["mandate"] == "輸出管理規則の執行"


def test_get_institution_profile_item_by_entity_id_none_when_absent():
    client = _base_client()
    assert inst.get_institution_profile_item_by_entity_id(client, "bis-id") is None


def test_get_institution_profile_item_by_entity_id_does_not_match_actor_profile():
    """knowledge_type=actor_profileの行を誤って拾わないことを確認する（Person/Institutionの
    格納先は同じgeo_knowledge_itemsテーブルだが、knowledge_typeで厳密に区別する）。"""
    client = _base_client(geo_knowledge_items=[
        {"knowledge_item_id": "actor-1", "knowledge_type": "actor_profile", "status": "active",
         "retrieval_status": "available", "is_demo": False,
         "structured_content": {"entity_id": "bis-id", "objectives": []}},
    ])
    assert inst.get_institution_profile_item_by_entity_id(client, "bis-id") is None


# ── build_institution_intelligence_pack ───────────────────────────────────

def test_build_institution_intelligence_pack_marks_none_available_when_no_profile():
    client = _base_client()
    entities = [{"entity_id": "bis-id", "canonical_name": "BIS", "entity_type": "government"}]
    pack = inst.build_institution_intelligence_pack(client, entities)
    assert pack["institutions"][0]["profile_source"] == "none_available"
    assert pack["institutions"][0]["mandate"] is None


def test_build_institution_intelligence_pack_includes_mandate_and_decision_authority():
    client = _base_client(geo_knowledge_items=[
        {"knowledge_item_id": "inst-1", "knowledge_type": "institution_profile", "status": "active",
         "retrieval_status": "available", "is_demo": False,
         "structured_content": {
             "entity_id": "bis-id",
             "mandate": "米国の輸出管理規則(EAR)に基づく執行",
             "decision_authority": {
                 "description": "商務省産業安全保障局が規則を発行・改定する",
                 "veto_players": ["Commerce Department"], "decision_process": "省令改定",
             },
             "constraints": ["National Defense Authorization Actの範囲内"],
             "evidence_ids": ["ev-1"],
         }},
    ])
    entities = [{"entity_id": "bis-id", "canonical_name": "BIS", "entity_type": "government"}]
    pack = inst.build_institution_intelligence_pack(client, entities)
    row = pack["institutions"][0]
    assert row["profile_source"] == "institution_intelligence_active"
    assert row["mandate"] == "米国の輸出管理規則(EAR)に基づく執行"
    assert row["decision_authority"]["veto_players"] == ["Commerce Department"]
    assert "ev-1" in pack["known_ids"]


def test_build_institution_intelligence_pack_never_includes_persistent_preference_shaped_fields():
    """擬人化防止の直接確認: Institution Packの各行がpersistent_preference/worldview等
    Person Actor固有のキーを一切持たないことを確認する。"""
    client = _base_client(geo_knowledge_items=[
        {"knowledge_item_id": "inst-1", "knowledge_type": "institution_profile", "status": "active",
         "retrieval_status": "available", "is_demo": False,
         "structured_content": {"entity_id": "nato-id", "mandate": "集団防衛"}},
    ])
    entities = [{"entity_id": "nato-id", "canonical_name": "NATO", "entity_type": "international_organization"}]
    pack = inst.build_institution_intelligence_pack(client, entities)
    row = pack["institutions"][0]
    for forbidden_key in ("persistent_preference", "worldview", "red_lines", "strategic_communication_pattern"):
        assert forbidden_key not in row


def test_build_institution_intelligence_pack_skips_entities_without_entity_id():
    client = _base_client()
    pack = inst.build_institution_intelligence_pack(client, [{"canonical_name": "no id"}])
    assert pack["institutions"] == []


# ── seed_institution_profile ──────────────────────────────────────────────

def test_seed_institution_profile_creates_entity_and_candidate_item():
    client = _base_client()
    item = inst.seed_institution_profile(client, **_SEED_KWARGS)
    assert item["status"] == "candidate"
    assert item["knowledge_type"] == "institution_profile"
    assert item["structured_content"]["mandate"] == "輸出管理規則の執行"
    entities = client.tables["geo_entities"]
    assert len(entities) == 1
    assert entities[0]["entity_type"] == "ministry"
    assert entities[0]["external_ids"]["institution_id"] == "us_bis"


def test_seed_institution_profile_rejects_non_institutional_entity_type():
    client = _base_client()
    import pytest
    with pytest.raises(ValueError):
        inst.seed_institution_profile(client, **{**_SEED_KWARGS, "entity_type": "person"})


def test_seed_institution_profile_is_idempotent_when_content_unchanged():
    client = _base_client()
    first = inst.seed_institution_profile(client, **_SEED_KWARGS)
    second = inst.seed_institution_profile(client, **_SEED_KWARGS)
    assert first["knowledge_item_id"] == second["knowledge_item_id"]
    assert len(client.tables["geo_knowledge_items"]) == 1
    assert len(client.tables["geo_entities"]) == 1


def test_seed_institution_profile_activate_flow_makes_item_active_and_retrievable():
    client = _base_client()
    item = inst.seed_institution_profile(client, activate=True, **_SEED_KWARGS)
    assert item["status"] == "active"
    found = inst.get_institution_profile_item_by_entity_id(client, item["structured_content"]["entity_id"])
    assert found is not None
    assert found["knowledge_item_id"] == item["knowledge_item_id"]
