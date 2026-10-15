import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_cluster_feature_service as feat  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

RUN_ID = "run-1"


def _seed_run_with_narrative_master(client, priority="High", trend_role="primary"):
    client.insert("geo_report_project_assets", [{
        "id": "nm-asset-1", "content": {"rows": [{
            "sub_narrative_id": "S1-01", "priority": priority, "trend_role": trend_role,
        }]},
    }])
    client.insert("geo_report_collection_runs", [{
        "run_id": RUN_ID, "narrative_master_asset_id": "nm-asset-1",
    }])


def _seed_sentiment_cluster(client, cluster_id, **overrides):
    row = {
        "id": f"row-{cluster_id}", "run_id": RUN_ID, "sentiment_cluster_id": cluster_id,
        "signal_id": "S1", "narrative_id": "S1-01", "cluster_records": 10,
        "exception_candidate_count": 2, "requires_review": True,
        "source_distribution": "Public_Voice_Candidate:10", "language_distribution": "English:10",
        "fallback_share": 0.0, "preliminary_consistency": "High",
    }
    row.update(overrides)
    client.insert("geo_report_sentiment_clusters", [row])


def _seed_representatives(client, cluster_id, source_names):
    rows = [{
        "id": f"rep-{cluster_id}-{i}", "run_id": RUN_ID, "sentiment_cluster_id": cluster_id,
        "representative_no": i + 1, "source_name": name,
    } for i, name in enumerate(source_names)]
    client.insert("geo_report_sentiment_cluster_representatives", rows)


def test_compute_features_for_run_writes_expected_values():
    client = FakeSupabaseClient()
    _seed_run_with_narrative_master(client, priority="High", trend_role="primary")
    _seed_sentiment_cluster(client, "SC_1")
    _seed_representatives(client, "SC_1", ["Reuters", "Reuters", "AP"])

    count = feat.compute_features_for_run(client, RUN_ID, "sentiment")
    assert count == 1

    rows = client.select("geo_report_cluster_features", {
        "select": "*", "run_id": f"eq.{RUN_ID}", "cluster_type": "eq.sentiment",
    })
    assert len(rows) == 1
    row = rows[0]
    assert row["feature_version"] == "v1-no-embeddings"
    assert row["volume"] == 10
    assert row["preliminary_consistency_score"] == 1.0
    assert row["cohesion"] is None
    assert row["source_entropy"] == 0.0  # single-value distribution -> entropy 0
    assert row["fallback_share"] == 0.0
    assert row["complexity_score"] == 0.5 * (2 / 10) + 0.5 * 1.0
    assert row["representative_diversity_score"] == round(2 / 3, 4)
    assert row["narrative_priority_score"] == 1.0  # High(1.0) + primary boost(0.2) capped at 1.0


def test_compute_features_for_run_is_idempotent_on_rerun():
    client = FakeSupabaseClient()
    _seed_run_with_narrative_master(client)
    _seed_sentiment_cluster(client, "SC_1")

    feat.compute_features_for_run(client, RUN_ID, "sentiment")
    feat.compute_features_for_run(client, RUN_ID, "sentiment")

    rows = client.select("geo_report_cluster_features", {
        "select": "*", "run_id": f"eq.{RUN_ID}", "cluster_type": "eq.sentiment",
    })
    assert len(rows) == 1


def test_shannon_entropy_multi_value_distribution():
    # 50/50 split -> entropy = 1.0 bit
    assert feat._shannon_entropy("A:5|B:5") == 1.0
    assert feat._shannon_entropy("A:10") == 0.0
    assert feat._shannon_entropy("") == 0.0


def test_narrative_priority_score_defaults_when_narrative_unknown():
    client = FakeSupabaseClient()
    _seed_run_with_narrative_master(client, priority="Low", trend_role="secondary")
    _seed_sentiment_cluster(client, "SC_1", narrative_id="S1-99")  # not in narrative master

    feat.compute_features_for_run(client, RUN_ID, "sentiment")
    rows = client.select("geo_report_cluster_features", {"select": "*", "run_id": f"eq.{RUN_ID}"})
    assert rows[0]["narrative_priority_score"] == 0.5  # unknown narrative -> default medium
