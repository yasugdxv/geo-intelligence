import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_representative_strategy_service as repstrat  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

RUN_ID = "run-1"


def test_save_current_reservoir_variants_copies_production_representatives():
    client = FakeSupabaseClient()
    client.insert("geo_report_sentiment_cluster_representatives", [
        {"id": "r1", "run_id": RUN_ID, "sentiment_cluster_id": "SC_1", "record_id": "rec-1"},
        {"id": "r2", "run_id": RUN_ID, "sentiment_cluster_id": "SC_1", "record_id": "rec-2"},
    ])

    count = repstrat.save_current_reservoir_variants(client, RUN_ID, "sentiment", ["SC_1"])
    assert count == 1

    rows = client.select("geo_report_representative_selection_variants", {
        "select": "*", "run_id": f"eq.{RUN_ID}", "strategy": "eq.current_reservoir",
    })
    assert len(rows) == 1
    assert set(rows[0]["representative_record_ids"]) == {"rec-1", "rec-2"}


def _records_with_embeddings():
    # Cluster of 6 records: 4 tightly clustered near [1,0], 2 outliers near [0,1]
    # with divergent preliminary hints, one flagged as an existing exception.
    records = [
        {"record_id": "r1", "source_class_preliminary": "Public_Voice_Candidate", "attitude_hint": "positive", "is_exception": False},
        {"record_id": "r2", "source_class_preliminary": "Public_Voice_Candidate", "attitude_hint": "positive", "is_exception": False},
        {"record_id": "r3", "source_class_preliminary": "Public_Voice_Candidate", "attitude_hint": "positive", "is_exception": False},
        {"record_id": "r4", "source_class_preliminary": "Public_Voice_Candidate", "attitude_hint": "positive", "is_exception": False},
        {"record_id": "r5", "source_class_preliminary": "Media", "attitude_hint": "unknown", "is_exception": True},
        {"record_id": "r6", "source_class_preliminary": "Media", "attitude_hint": "unknown", "is_exception": False},
    ]
    embeddings = {
        "r1": np.array([1.0, 0.01]), "r2": np.array([0.99, 0.02]),
        "r3": np.array([1.0, -0.01]), "r4": np.array([0.98, 0.0]),
        "r5": np.array([0.0, 1.0]), "r6": np.array([0.05, 0.95]),
    }
    return records, embeddings


def test_select_typical_diverse_boundary_picks_five_distinct_roles():
    records, embeddings = _records_with_embeddings()
    selection = repstrat.select_typical_diverse_boundary(records, embeddings)
    assert len(selection) == 5
    roles = [s["role"] for s in selection]
    assert roles == ["typical", "typical", "exception", "diverse", "boundary"]
    ids = [s["record_id"] for s in selection]
    assert len(set(ids)) == 5  # no duplicate picks

    # typical picks should come from the dense majority cluster
    assert set(ids[:2]).issubset({"r1", "r2", "r3", "r4"})
    # exception role should surface the record explicitly flagged is_exception
    exception_pick = next(s for s in selection if s["role"] == "exception")
    assert exception_pick["record_id"] == "r5"


def test_select_typical_diverse_boundary_skips_records_without_embeddings():
    records = [
        {"record_id": "r1", "source_class_preliminary": "A", "attitude_hint": "x", "is_exception": False},
        {"record_id": "r2", "source_class_preliminary": "A", "attitude_hint": "x", "is_exception": False},
    ]
    embeddings = {"r1": np.array([1.0, 0.0])}  # r2 has no embedding
    selection = repstrat.select_typical_diverse_boundary(records, embeddings)
    assert all(s["record_id"] != "r2" for s in selection)


def test_save_typical_diverse_boundary_variant_is_idempotent():
    client = FakeSupabaseClient()
    selection = [{"record_id": "r1", "role": "typical"}]
    repstrat.save_typical_diverse_boundary_variant(client, RUN_ID, "sentiment", "SC_1", selection)
    repstrat.save_typical_diverse_boundary_variant(client, RUN_ID, "sentiment", "SC_1", selection)

    rows = client.select("geo_report_representative_selection_variants", {
        "select": "*", "run_id": f"eq.{RUN_ID}", "strategy": "eq.typical_diverse_boundary_v1",
    })
    assert len(rows) == 1
    assert rows[0]["representative_record_ids"] == ["r1"]
