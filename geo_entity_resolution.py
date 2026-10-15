"""地政学ナレッジ＆インテリジェンスDB: Entity resolution（canonical/alias照合）。

LLMだけでentity mergeを確定しない（13章・絶対原則）。本モジュールはルールベースの
完全一致・略称・旧名称照合のみを行い、あいまい一致は候補として返すに留め、
実際のmerge確定はgeo_entity_service.merge_entities（人間のRole操作）が担う。
"""


def _normalize(text: str) -> str:
    return (text or "").strip().lower()


def find_entity_matches(client, name_text: str) -> list:
    """name_textに一致するEntityを canonical_name → alias の順で探す。
    戻り値: [{"entity_id":..., "canonical_name":..., "match_type": "exact_canonical"|"alias", "alias_type": str|None}]
    完全一致のみを扱う（あいまい一致・表記ゆれの自動吸収はPhase2以降）。"""
    normalized = _normalize(name_text)
    if not normalized:
        return []

    matches = []
    canonical_rows = client.select("geo_entities", {
        "select": "entity_id,canonical_name",
        "canonical_name": f"ilike.{name_text}",
    })
    for row in canonical_rows:
        if _normalize(row["canonical_name"]) == normalized:
            matches.append({
                "entity_id": row["entity_id"], "canonical_name": row["canonical_name"],
                "match_type": "exact_canonical", "alias_type": None,
            })

    alias_rows = client.select("geo_entity_aliases", {
        "select": "entity_id,alias_text,alias_type",
        "alias_text": f"ilike.{name_text}",
    })
    matched_entity_ids = {m["entity_id"] for m in matches}
    for row in alias_rows:
        if _normalize(row["alias_text"]) == normalized and row["entity_id"] not in matched_entity_ids:
            entity_rows = client.select("geo_entities", {
                "select": "entity_id,canonical_name", "entity_id": f"eq.{row['entity_id']}", "limit": "1",
            })
            if entity_rows:
                matches.append({
                    "entity_id": entity_rows[0]["entity_id"],
                    "canonical_name": entity_rows[0]["canonical_name"],
                    "match_type": "alias", "alias_type": row["alias_type"],
                })
                matched_entity_ids.add(row["entity_id"])

    return matches


def resolve_or_flag(client, name_text: str) -> dict:
    """name_textの解決状況を返す。
    - 一致0件: {"status": "unresolved", "candidates": []}
    - 一致1件: {"status": "resolved", "entity_id": ..., "candidates": [...]}
    - 一致2件以上: {"status": "ambiguous", "candidates": [...]}（人間が選択する必要がある）"""
    matches = find_entity_matches(client, name_text)
    if not matches:
        return {"status": "unresolved", "candidates": []}
    if len(matches) == 1:
        return {"status": "resolved", "entity_id": matches[0]["entity_id"], "candidates": matches}
    return {"status": "ambiguous", "candidates": matches}
