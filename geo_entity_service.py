"""地政学ナレッジ＆インテリジェンスDB: Entity CRUD・Alias管理・Merge。

Entity MergeはLLMが確定しない（絶対原則）。merge_entitiesはknowledge_steward/
administrator roleのみ実行でき、実行時は必ずgeo_audit_logsへ記録する。
"""
import geo_audit
import geo_role_guard

ENTITY_TYPES = (
    "country", "territory", "government", "ministry", "political_party", "faction",
    "person", "military", "security_service", "international_organization",
    "state_owned_enterprise", "company", "industry_group", "labor_union",
    "religious_group", "ethnic_group", "armed_group", "civil_society", "media",
    "geographic_location", "port", "strait", "canal", "military_base", "strategic_asset",
)

# Network Graph（Phase2B）向け: entity_type(24種)はcategorical色として使うには種類過多
# （dataviz skillの指針で上限7-8種）のため、少数グループへ畳み込む。詳細区分は
# ノードラベル・ホバー時ツールチップ側で表現する（docs/geopolitical-network-graph.md）。
ENTITY_TYPE_DISPLAY_CATEGORY = {
    "country": "state", "territory": "state", "government": "state", "ministry": "state",
    "political_party": "political_social", "faction": "political_social", "person": "political_social",
    "religious_group": "political_social", "ethnic_group": "political_social",
    "civil_society": "political_social", "media": "political_social", "labor_union": "political_social",
    "international_organization": "political_social",
    "military": "security", "security_service": "security", "armed_group": "security",
    "military_base": "security",
    "state_owned_enterprise": "economic", "company": "economic", "industry_group": "economic",
    "geographic_location": "geography_asset", "port": "geography_asset", "strait": "geography_asset",
    "canal": "geography_asset", "strategic_asset": "geography_asset",
}


def create_entity(client, *, entity_type: str, canonical_name: str, actor_id: str,
                   local_name: str = None, name_en: str = None, name_ja: str = None,
                   abbreviations: list = None, country_code: str = None,
                   is_demo: bool = False) -> dict:
    if entity_type not in ENTITY_TYPES:
        raise ValueError(f"未対応のentity_typeです: {entity_type}")
    row = {
        "entity_type": entity_type, "canonical_name": canonical_name,
        "local_name": local_name, "name_en": name_en, "name_ja": name_ja,
        "abbreviations": abbreviations or [], "country_code": country_code,
        "is_demo": is_demo,
    }
    created = client.insert("geo_entities", [row])[0]
    geo_audit.log_audit(client, actor=actor_id, action="entity_create",
                         target_table="geo_entities", target_id=created["entity_id"],
                         detail={"entity_type": entity_type, "canonical_name": canonical_name})
    return created


def add_alias(client, *, entity_id: str, alias_text: str, alias_type: str,
               language: str = None) -> dict:
    if alias_type not in ("local_language", "abbreviation", "former_name", "transliteration"):
        raise ValueError(f"未対応のalias_typeです: {alias_type}")
    return client.insert("geo_entity_aliases", [{
        "entity_id": entity_id, "alias_text": alias_text, "alias_type": alias_type,
        "language": language,
    }])[0]


def get_entity(client, entity_id: str) -> dict | None:
    rows = client.select("geo_entities", {"select": "*", "entity_id": f"eq.{entity_id}", "limit": "1"})
    return rows[0] if rows else None


def list_entities(client, *, entity_type: str = None, country_code: str = None,
                   include_demo: bool = False, limit: int = 100) -> list:
    params = {"select": "*", "order": "canonical_name.asc", "limit": str(limit)}
    if entity_type:
        params["entity_type"] = f"eq.{entity_type}"
    if country_code:
        params["country_code"] = f"eq.{country_code}"
    if not include_demo:
        params["is_demo"] = "eq.false"
    return client.select("geo_entities", params)


def merge_entities(client, *, primary_entity_id: str, duplicate_entity_id: str,
                    actor_id: str, actor_role: str, reason: str) -> dict:
    """duplicate_entity_idをprimary_entity_idへ統合する。参照（alias/relationship/asset/
    dependency/claim）をprimary側へ付け替え、duplicateはstatus=dissolved・
    successor_entity_id=primaryにする。LLMは提案のみでこの関数を直接呼べない
    （呼び出し元は必ず人間のRole操作を経ること）。"""
    geo_role_guard.require_role(actor_role, ("knowledge_steward",))
    if primary_entity_id == duplicate_entity_id:
        raise ValueError("primaryとduplicateが同一です")

    client.update("geo_entity_aliases", {"entity_id": f"eq.{duplicate_entity_id}"},
                   {"entity_id": primary_entity_id})
    client.update("geo_relationships", {"from_entity_id": f"eq.{duplicate_entity_id}"},
                   {"from_entity_id": primary_entity_id})
    client.update("geo_relationships", {"to_entity_id": f"eq.{duplicate_entity_id}"},
                   {"to_entity_id": primary_entity_id})
    client.update("geo_assets", {"entity_id": f"eq.{duplicate_entity_id}"},
                   {"entity_id": primary_entity_id})
    client.update("geo_dependencies", {"entity_id": f"eq.{duplicate_entity_id}"},
                   {"entity_id": primary_entity_id})
    client.update("geo_dependencies", {"counterpart_entity_id": f"eq.{duplicate_entity_id}"},
                  {"counterpart_entity_id": primary_entity_id})
    client.update("geo_claims", {"subject_entity_id": f"eq.{duplicate_entity_id}"},
                   {"subject_entity_id": primary_entity_id})
    client.update("geo_claims", {"object_entity_id": f"eq.{duplicate_entity_id}"},
                   {"object_entity_id": primary_entity_id})
    client.update("geo_entity_issues", {"entity_id": f"eq.{duplicate_entity_id}"},
                   {"entity_id": primary_entity_id})

    client.update("geo_entities", {"entity_id": f"eq.{duplicate_entity_id}"}, {
        "status": "dissolved", "successor_entity_id": primary_entity_id,
    })

    geo_audit.log_audit(client, actor=actor_id, action="entity_merge",
                         target_table="geo_entities", target_id=primary_entity_id,
                         detail={"duplicate_entity_id": duplicate_entity_id, "reason": reason,
                                 "actor_role": actor_role})
    # SupabaseClient.updateはPrefer: return=minimalのため更新後の行を返さない。
    return get_entity(client, duplicate_entity_id)
