"""地政学アプリCleanup: geo_relationship_service.list_relationships_for_entitiesの検証
（P1-2のBefore/After一致確認）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_relationship_service as relationship_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


class _CountingClient(FakeSupabaseClient):
    def __init__(self, tables=None):
        super().__init__(tables)
        self.select_count = 0

    def select(self, table, params):
        self.select_count += 1
        return super().select(table, params)


def _tables():
    return {
        "geo_relationships": [
            {"relationship_id": "r1", "from_entity_id": "e1", "to_entity_id": "e2", "is_demo": False},
            {"relationship_id": "r2", "from_entity_id": "e2", "to_entity_id": "e3", "is_demo": False},
            {"relationship_id": "r3", "from_entity_id": "e3", "to_entity_id": "e1", "is_demo": False},
            {"relationship_id": "r4", "from_entity_id": "e4", "to_entity_id": "e5", "is_demo": False},
        ],
    }


def test_list_relationships_for_entities_dedupes_and_covers_from_and_to():
    client = _CountingClient(_tables())
    result = relationship_service.list_relationships_for_entities(client, ["e1", "e2", "e3"])
    ids = {r["relationship_id"] for r in result}
    assert ids == {"r1", "r2", "r3"}  # r4は無関係entityのみのため含まれない
    assert len(result) == 3  # 重複なし
    # P1-2: select回数はentity件数によらず高々2回（from一括+to一括）
    assert client.select_count == 2


def test_list_relationships_for_entities_respects_include_demo():
    client = FakeSupabaseClient(tables={
        "geo_relationships": [
            {"relationship_id": "r1", "from_entity_id": "e1", "to_entity_id": "e2", "is_demo": True},
        ],
    })
    assert relationship_service.list_relationships_for_entities(client, ["e1"]) == []
    result = relationship_service.list_relationships_for_entities(client, ["e1"], include_demo=True)
    assert len(result) == 1


def test_list_relationships_for_entities_empty_input_makes_no_query():
    client = _CountingClient()
    assert relationship_service.list_relationships_for_entities(client, []) == []
    assert client.select_count == 0
