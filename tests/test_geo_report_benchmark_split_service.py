import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_benchmark_split_service as splitsvc  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

RUN_ID = "run-1"


def _seed_sentiment_cluster(client, cluster_id, narrative_id, week_start):
    client.insert("geo_report_sentiment_clusters", [{
        "id": f"row-{cluster_id}", "run_id": RUN_ID, "sentiment_cluster_id": cluster_id,
        "narrative_id": narrative_id, "week_start": week_start,
    }])


def _seed_record(client, record_id, cluster_id, *, duplicate_group_id=None, url_hash=None, text_hash=None):
    client.insert("geo_report_records", [{
        "id": f"rec-{record_id}", "run_id": RUN_ID, "record_id": record_id,
        "sentiment_cluster_id": cluster_id, "duplicate_group_id": duplicate_group_id,
        "url_hash": url_hash, "text_hash": text_hash,
    }])


def test_build_leakage_groups_connects_clusters_sharing_duplicate_group():
    client = FakeSupabaseClient()
    _seed_record(client, "r1", "SC_1", duplicate_group_id="dup-A")
    _seed_record(client, "r2", "SC_2", duplicate_group_id="dup-A")  # shares dup-A with SC_1
    _seed_record(client, "r3", "SC_3", duplicate_group_id="dup-B")  # isolated

    groups = splitsvc.build_leakage_groups(client, RUN_ID, "sentiment", ["SC_1", "SC_2", "SC_3"])
    assert groups["SC_1"] == groups["SC_2"]
    assert groups["SC_3"] != groups["SC_1"]


def test_assign_narrative_holdout_split_keeps_leakage_group_together():
    client = FakeSupabaseClient()
    for cid, nid in [("SC_1", "N1"), ("SC_2", "N2"), ("SC_3", "N3"), ("SC_4", "N4"), ("SC_5", "N5")]:
        _seed_sentiment_cluster(client, cid, nid, "2026-08-01")
    # SC_2 (narrative N2) and SC_3 (narrative N3, different narrative) share a duplicate group
    _seed_record(client, "r1", "SC_2", duplicate_group_id="dup-X")
    _seed_record(client, "r2", "SC_3", duplicate_group_id="dup-X")

    result = splitsvc.assign_narrative_holdout_split(
        client, RUN_ID, "sc_classification", "sentiment",
        ["SC_1", "SC_2", "SC_3", "SC_4", "SC_5"], random_seed=1)

    # whichever split SC_2 landed in, SC_3 (leakage-linked) must match
    assert result["SC_2"]["split"] == result["SC_3"]["split"]
    assert set(result.keys()) == {"SC_1", "SC_2", "SC_3", "SC_4", "SC_5"}
    for cid, info in result.items():
        assert info["split"] in ("train", "validation", "test")


def test_assign_temporal_split_orders_by_week_and_flags_straddling_leakage():
    client = FakeSupabaseClient()
    weeks = ["2026-07-01", "2026-07-08", "2026-07-15", "2026-07-22", "2026-07-29",
             "2026-08-05", "2026-08-12", "2026-08-19", "2026-08-26", "2026-09-02"]
    cluster_ids = [f"SC_{i}" for i in range(10)]
    for cid, wk in zip(cluster_ids, weeks):
        _seed_sentiment_cluster(client, cid, "N1", wk)

    result = splitsvc.assign_temporal_split(client, RUN_ID, "sc_classification", "sentiment", cluster_ids)
    splits_seen = {info["split"] for info in result.values()}
    assert splits_seen == {"train", "validation", "test"}
    # earliest week cluster should be train, latest should be test
    assert result["SC_0"]["split"] == "train"
    assert result["SC_9"]["split"] == "test"


def test_assign_temporal_split_flags_leakage_across_boundary():
    client = FakeSupabaseClient()
    weeks = ["2026-07-01"] * 4 + ["2026-09-01"] * 4
    cluster_ids = [f"SC_{i}" for i in range(8)]
    for cid, wk in zip(cluster_ids, weeks):
        _seed_sentiment_cluster(client, cid, "N1", wk)
    # SC_3 (early week, would land in train) shares a duplicate group with SC_4 (late week, test)
    _seed_record(client, "r1", "SC_3", duplicate_group_id="dup-Y")
    _seed_record(client, "r2", "SC_4", duplicate_group_id="dup-Y")

    result = splitsvc.assign_temporal_split(client, RUN_ID, "sc_classification", "sentiment", cluster_ids)
    assert result["SC_3"]["excluded_due_to_temporal_leakage"] is True
    assert result["SC_4"]["excluded_due_to_temporal_leakage"] is True


def test_save_split_assignments_is_idempotent_on_rerun():
    client = FakeSupabaseClient()
    assignments = {"SC_1": {"split": "train", "group_key": "SC_1"}}
    splitsvc.save_split_assignments(client, "bench-1", RUN_ID, "sc_classification", "sentiment",
                                       "temporal", assignments)
    splitsvc.save_split_assignments(client, "bench-1", RUN_ID, "sc_classification", "sentiment",
                                       "temporal", assignments)
    rows = client.select("geo_report_benchmark_split_assignments", {
        "select": "*", "benchmark_run_id": "eq.bench-1", "split_scheme": "eq.temporal",
    })
    assert len(rows) == 1
