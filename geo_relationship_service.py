"""地政学ナレッジ＆インテリジェンスDB: Relationship Edge CRUD。

「同盟国/敵対国」の単純な一分類にせず、relationship_domain×dependency×leverageの
組み合わせで多次元に表現する（docs/geopolitical-data-model.md 2.3章）。
"""
import geo_audit

RELATIONSHIP_DOMAINS = (
    "diplomacy", "security", "military", "trade", "investment", "finance", "energy",
    "technology", "information", "political_support", "social", "legal", "logistics",
)


def create_relationship(client, *, from_entity_id: str, to_entity_id: str,
                         relationship_domain: str, relationship_type: str, actor_id: str,
                         description: str = None, dependency_direction: str = None,
                         dependency_level: str = None, leverage_direction: str = None,
                         confidence: str = "判断不能", valid_from: str = None,
                         valid_to: str = None, is_demo: bool = False) -> dict:
    if relationship_domain not in RELATIONSHIP_DOMAINS:
        raise ValueError(f"未対応のrelationship_domainです: {relationship_domain}")
    row = {
        "from_entity_id": from_entity_id, "to_entity_id": to_entity_id,
        "relationship_domain": relationship_domain, "relationship_type": relationship_type,
        "description": description, "dependency_direction": dependency_direction,
        "dependency_level": dependency_level, "leverage_direction": leverage_direction,
        "confidence": confidence, "valid_from": valid_from, "valid_to": valid_to,
        "is_demo": is_demo,
    }
    created = client.insert("geo_relationships", [row])[0]
    geo_audit.log_audit(client, actor=actor_id, action="relationship_create",
                         target_table="geo_relationships", target_id=created["relationship_id"],
                         detail={"from_entity_id": from_entity_id, "to_entity_id": to_entity_id,
                                 "relationship_domain": relationship_domain})
    return created


def list_relationships_for_entity(client, entity_id: str, *, include_demo: bool = False) -> list:
    params_from = {"select": "*", "from_entity_id": f"eq.{entity_id}"}
    params_to = {"select": "*", "to_entity_id": f"eq.{entity_id}"}
    if not include_demo:
        params_from["is_demo"] = "eq.false"
        params_to["is_demo"] = "eq.false"
    from_rows = client.select("geo_relationships", params_from)
    to_rows = client.select("geo_relationships", params_to)
    seen = {r["relationship_id"] for r in from_rows}
    combined = list(from_rows) + [r for r in to_rows if r["relationship_id"] not in seen]
    return combined


def list_relationships_for_entities(client, entity_ids: list, *, include_demo: bool = False) -> list:
    """地政学アプリCleanup（P1-2）: list_relationships_for_entityの複数entity版。
    entityごとのループselectを避け、from/toそれぞれ1回のin.()一括selectで完結させる
    （既存list_relationships_for_entityは変更しない、呼び出し元を本関数へ差し替えるだけ）。"""
    if not entity_ids:
        return []
    params_from = {"select": "*", "from_entity_id": f"in.({','.join(entity_ids)})"}
    params_to = {"select": "*", "to_entity_id": f"in.({','.join(entity_ids)})"}
    if not include_demo:
        params_from["is_demo"] = "eq.false"
        params_to["is_demo"] = "eq.false"
    from_rows = client.select("geo_relationships", params_from)
    to_rows = client.select("geo_relationships", params_to)
    seen = {r["relationship_id"] for r in from_rows}
    combined = list(from_rows) + [r for r in to_rows if r["relationship_id"] not in seen]
    return combined


def list_relationships_for_issue(client, issue_id: str) -> list:
    links = client.select("geo_relationship_issues", {"select": "relationship_id", "issue_id": f"eq.{issue_id}"})
    relationship_ids = [link["relationship_id"] for link in links]
    if not relationship_ids:
        return []
    return client.select("geo_relationships", {
        "select": "*", "relationship_id": f"in.({','.join(relationship_ids)})",
    })


def get_relationship_graph_for_issue(client, issue_id: str, *, include_demo: bool = False) -> dict:
    """Network Graph（Relationship Explorer）向け。node=Entity, edge=Relationshipを
    DB側RPC（get_relationship_graph_for_issue、sql/2026-08-06_geo_phase2b_relationship_graph.sql）
    経由で1回の呼び出しで取得する。PostgRESTの素朴なin.()フィルタでEntityを一括取得しない
    （world_monitor_dashboardのfetch_engagement_mapで発生した巨大in.()バグと同じ落とし穴を回避）。"""
    result = client.rpc("get_relationship_graph_for_issue", {
        "p_issue_id": issue_id, "p_include_demo": include_demo,
    })
    if isinstance(result, dict) and "nodes" in result and "edges" in result:
        return result
    return {"nodes": [], "edges": []}
