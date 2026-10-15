"""地政学ナレッジ＆インテリジェンスDB Phase2A: Metric定義・観測値の管理。

改訂値は削除せずrevision_idで並存させる（8.3章）。同一metric_definition_id×entity_id×periodに
対して観測値が実際に変わった場合のみ新revisionを作る（値が同一なら重複挿入しない）。
"""
import geo_audit


def create_metric_definition(client, *, source_id: str, metric_code: str, metric_name: str,
                              description: str = None, unit: str = None, frequency: str = None,
                              methodology: str = None, geographic_scope: str = None,
                              source_url: str = None) -> dict:
    existing = client.select("geo_metric_definitions", {
        "select": "*", "source_id": f"eq.{source_id}", "metric_code": f"eq.{metric_code}", "limit": "1",
    })
    if existing:
        return existing[0]
    return client.insert("geo_metric_definitions", [{
        "source_id": source_id, "metric_code": metric_code, "metric_name": metric_name,
        "description": description, "unit": unit, "frequency": frequency, "methodology": methodology,
        "geographic_scope": geographic_scope, "source_url": source_url,
    }])[0]


def get_metric_definition(client, *, source_id: str, metric_code: str) -> dict | None:
    rows = client.select("geo_metric_definitions", {
        "select": "*", "source_id": f"eq.{source_id}", "metric_code": f"eq.{metric_code}", "limit": "1",
    })
    return rows[0] if rows else None


def upsert_metric_observation(client, *, metric_definition_id: str, entity_id: str, period: str,
                               observed_value: float, retrieved_at: str, raw_snapshot_id: str = None,
                               counterpart_entity_id: str = None, estimate_or_actual: str = "actual",
                               source_vintage: str = None, is_demo: bool = False) -> dict:
    """既存observationがあり値が同一ならそのまま返す（重複挿入しない）。値が異なれば
    revision_idを増やした新しい行をinsertする（旧行は削除しない）。"""
    existing = client.select("geo_metric_observations", {
        "select": "*", "metric_definition_id": f"eq.{metric_definition_id}",
        "entity_id": f"eq.{entity_id}", "period": f"eq.{period}", "order": "revision_id.desc", "limit": "1",
    })
    if existing and existing[0].get("observed_value") == observed_value:
        return existing[0]

    next_revision = (existing[0]["revision_id"] + 1) if existing else 1
    return client.insert("geo_metric_observations", [{
        "metric_definition_id": metric_definition_id, "entity_id": entity_id,
        "counterpart_entity_id": counterpart_entity_id, "period": period, "observed_value": observed_value,
        "estimate_or_actual": estimate_or_actual, "source_vintage": source_vintage,
        "revision_id": next_revision, "retrieved_at": retrieved_at, "raw_snapshot_id": raw_snapshot_id,
        "is_demo": is_demo,
    }])[0]


def list_latest_observations(client, *, metric_definition_id: str = None, entity_id: str = None,
                              include_demo: bool = False, limit: int = 100) -> list:
    """metric_definition_id×entity_idごとの最新revisionのみを返す（Phase2Aの簡易実装:
    全件取得後Python側で最新revisionだけ残す。件数が少ない前提のため許容する）。"""
    params = {"select": "*", "order": "period.desc", "limit": str(limit)}
    if metric_definition_id:
        params["metric_definition_id"] = f"eq.{metric_definition_id}"
    if entity_id:
        params["entity_id"] = f"eq.{entity_id}"
    if not include_demo:
        params["is_demo"] = "eq.false"
    rows = client.select("geo_metric_observations", params)

    latest_by_key = {}
    for row in rows:
        key = (row["metric_definition_id"], row["entity_id"], row["period"])
        if key not in latest_by_key or row["revision_id"] > latest_by_key[key]["revision_id"]:
            latest_by_key[key] = row
    return list(latest_by_key.values())


def get_observation(client, metric_observation_id: str) -> dict | None:
    rows = client.select("geo_metric_observations", {
        "select": "*", "metric_observation_id": f"eq.{metric_observation_id}", "limit": "1",
    })
    return rows[0] if rows else None
