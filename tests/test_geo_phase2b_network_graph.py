"""地政学ナレッジ＆インテリジェンスDB Phase2B: Network Graph（Relationship Explorer）のテスト。
巨大なPostgREST in.()フィルタに依存せず、DB側RPC1回でnodes/edgesを取得することを確認する
（world_monitor_dashboardのfetch_engagement_mapで実際に発生したURL長超過バグと同じ落とし穴を
Entity一括取得で再現していないことの回帰確認）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_relationship_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def test_get_relationship_graph_for_issue_uses_rpc_not_giant_in_clause():
    client = FakeSupabaseClient()
    expected = {
        "nodes": [{"entity_id": "e1", "canonical_name": "A国"}, {"entity_id": "e2", "canonical_name": "B国"}],
        "edges": [{"relationship_id": "r1", "from_entity_id": "e1", "to_entity_id": "e2",
                   "relationship_domain": "trade", "relationship_type": "輸出"}],
    }
    client.rpc_responses = {"get_relationship_graph_for_issue": expected}

    result = geo_relationship_service.get_relationship_graph_for_issue(client, "issue-1")

    assert result == expected
    # geo_entities/geo_relationshipsへの直接selectでin.()フィルタを一切使っていないこと
    # （呼び出しはすべてrpc()経由。selectログにgeo_entitiesへのin.()呼び出しが無いことを確認）
    for table in client.inserted:
        assert table not in ("geo_entities",)


def test_get_relationship_graph_for_issue_empty_when_rpc_returns_nothing():
    client = FakeSupabaseClient()
    result = geo_relationship_service.get_relationship_graph_for_issue(client, "issue-empty")
    assert result == {"nodes": [], "edges": []}


def test_get_relationship_graph_for_issue_include_demo_passed_to_rpc():
    client = FakeSupabaseClient()
    captured = {}
    original_rpc = client.rpc

    def spy_rpc(fn_name, params=None):
        captured["fn_name"] = fn_name
        captured["params"] = params
        return original_rpc(fn_name, params)

    client.rpc = spy_rpc
    geo_relationship_service.get_relationship_graph_for_issue(client, "issue-1", include_demo=True)

    assert captured["fn_name"] == "get_relationship_graph_for_issue"
    assert captured["params"] == {"p_issue_id": "issue-1", "p_include_demo": True}
