"""地政学アプリCleanup: geo_retrieval_service.pyの検証（P1-1のBefore/After一致確認）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_retrieval_service as retrieval  # noqa: E402
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
        "geo_expert_review_sessions": [
            {"review_session_id": "s1", "case_id": "case-1"},
            {"review_session_id": "s2", "case_id": "case-2"},
        ],
        "geo_cases": [
            {"case_id": "case-1", "case_title": "Case One"},
            {"case_id": "case-2", "case_title": "Case Two"},
        ],
    }


def test_attach_case_finding_provenance_resolves_case_title_and_dates():
    client = _CountingClient(_tables())
    items = [
        {"knowledge_item_id": "k1", "derived_from_review_session_id": "s1",
         "as_of_date": "2026-08-01", "reviewed_by": "analyst_a"},
        {"knowledge_item_id": "k2", "derived_from_review_session_id": "s2"},
        {"knowledge_item_id": "k3", "derived_from_review_session_id": "s1"},
    ]
    result = retrieval.attach_case_finding_provenance(client, items)

    assert result[0]["original_case_title"] == "Case One"
    assert result[0]["as_of_date"] == "2026-08-01"
    assert result[0]["reviewed_by"] == "analyst_a"
    assert result[1]["original_case_title"] == "Case Two"
    assert result[2]["original_case_title"] == "Case One"
    # P1-1: select回数はitem件数(3)によらず高々2回（session一括+case一括）
    assert client.select_count == 2


def test_attach_case_finding_provenance_handles_unresolvable_session_gracefully():
    client = _CountingClient(_tables())
    items = [
        {"knowledge_item_id": "k1", "derived_from_review_session_id": "missing-session"},
        {"knowledge_item_id": "k2"},  # derived_from_review_session_idすら無い
    ]
    result = retrieval.attach_case_finding_provenance(client, items)

    assert result[0]["original_case_title"] is None
    assert result[1]["original_case_title"] is None


def test_attach_case_finding_provenance_handles_session_without_case_id():
    client = _CountingClient({
        "geo_expert_review_sessions": [{"review_session_id": "s3", "case_id": None}],
        "geo_cases": [],
    })
    items = [{"knowledge_item_id": "k1", "derived_from_review_session_id": "s3"}]
    result = retrieval.attach_case_finding_provenance(client, items)

    assert result[0]["original_case_title"] is None


def test_attach_case_finding_provenance_empty_input_makes_no_query():
    client = _CountingClient()
    assert retrieval.attach_case_finding_provenance(client, []) == []
    assert client.select_count == 0
