"""地政学ナレッジ＆インテリジェンスDB Phase2B-2A: geo_situation_link_service.pyのテスト。

hub値減衰、shared_external_eventのdeterministic override、time proximity（区間gap）、
freeze（reviewed/accepted/rejected）、is_demo分離、rejected Cluster除外、write integrity
boundary（geo_situation_link_apply_replace RPC経由のみで書き込む）を検証する。
"""
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_situation_link_service as svc  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


class _LinkFakeClient(FakeSupabaseClient):
    """geo_situation_link_apply_replace RPCの実際のPL/pgSQL挙動（snapshot比較・write
    integrity検証・DELETE+INSERT）をPythonで再現するFake。"""

    def __init__(self, tables=None):
        super().__init__(tables=tables)

    def rpc(self, fn_name, params=None):
        if fn_name != "geo_situation_link_apply_replace":
            return super().rpc(fn_name, params)
        return self._apply_replace(params or {})

    def _apply_replace(self, p):
        is_demo = p["p_is_demo"]
        links = self.tables.get("geo_episode_cluster_links", [])
        clusters = self.tables.get("geo_episode_clusters", [])
        cluster_by_id = {c["episode_cluster_id"]: c for c in clusters}

        current_frozen_ids = sorted(
            l["cluster_link_id"] for l in links
            if l["link_status"] in ("reviewed", "accepted", "rejected") and l["is_demo"] == is_demo)
        current_candidate_ids = sorted(
            l["cluster_link_id"] for l in links if l["link_status"] == "candidate" and l["is_demo"] == is_demo)
        current_population_ids = sorted(
            c["episode_cluster_id"] for c in clusters if c["cluster_status"] != "rejected" and c["is_demo"] == is_demo)

        def canon(lst):
            return sorted(lst or [])

        if (canon(p.get("p_expected_frozen_link_ids")) != current_frozen_ids
                or canon(p.get("p_expected_candidate_link_ids")) != current_candidate_ids
                or canon(p.get("p_expected_population_cluster_ids")) != current_population_ids):
            raise svc.SituationLinkConflict("situation_link_conflict: snapshot changed since read, retry")

        rejected_ids = {c["episode_cluster_id"] for c in clusters if c["cluster_status"] == "rejected"}
        new_links = p.get("p_new_links") or []
        for link in new_links:
            if link["source_cluster_id"] in rejected_ids or link["target_cluster_id"] in rejected_ids:
                raise svc.SituationLinkValidationError(
                    "situation_link_validation_error: new link references a rejected cluster")
            c1 = cluster_by_id.get(link["source_cluster_id"], {})
            c2 = cluster_by_id.get(link["target_cluster_id"], {})
            if c1.get("is_demo") != is_demo or c2.get("is_demo") != is_demo or c1.get("is_demo") != c2.get("is_demo"):
                raise svc.SituationLinkValidationError(
                    "situation_link_validation_error: is_demo scope mismatch")

        self.tables["geo_episode_cluster_links"] = [
            l for l in links if not (l["link_status"] == "candidate" and l["is_demo"] == is_demo)]

        frozen_pairs = {
            (l["source_cluster_id"], l["target_cluster_id"]) for l in self.tables["geo_episode_cluster_links"]
            if l["link_status"] in ("reviewed", "accepted", "rejected")}

        inserted = inserted_same = inserted_related = skipped_frozen = 0
        now_iso = datetime.now(timezone.utc).isoformat()
        for link in new_links:
            source = min(link["source_cluster_id"], link["target_cluster_id"])
            target = max(link["source_cluster_id"], link["target_cluster_id"])
            if (source, target) in frozen_pairs:
                skipped_frozen += 1
                continue
            row = {
                "cluster_link_id": self._next_id("cluster_link_id"),
                "source_cluster_id": source, "target_cluster_id": target, "is_demo": is_demo,
                "link_type": link["link_type"], "link_score": link["link_score"],
                "link_reasons": link.get("link_reasons") or [],
                "link_status": "candidate", "reviewed_at": None, "reviewed_by": None, "review_notes": None,
                "created_at": now_iso, "updated_at": now_iso,
            }
            self.tables.setdefault("geo_episode_cluster_links", []).append(row)
            inserted += 1
            if link["link_type"] == "same_situation_candidate":
                inserted_same += 1
            else:
                inserted_related += 1

        return {"inserted": inserted, "inserted_same_situation_candidate": inserted_same,
                "inserted_related_context": inserted_related, "skipped_frozen_pair": skipped_frozen}


def _cluster(cid, actor_entity_id, quad, root, is_demo=False, status="candidate",
             first="2026-09-07", last="2026-09-07"):
    return {
        "episode_cluster_id": cid, "actor_entity_id": actor_entity_id, "quad_class": quad,
        "event_root_code": root, "is_demo": is_demo, "cluster_sequence": 1, "member_count": 1,
        "first_event_date": first, "last_event_date": last, "goldstein_min": None, "goldstein_max": None,
        "trajectory": "insufficient_data", "representative_counterparties": [], "representative_geo": [],
        "cluster_status": status, "reviewed_at": None, "reviewed_by": None, "review_notes": None,
        "raw_metadata": None, "created_at": "2026-09-08T00:00:00+00:00", "updated_at": "2026-09-08T00:00:00+00:00",
    }


def _member(cluster_id, candidate_id):
    return {"cluster_member_id": f"m-{cluster_id}-{candidate_id}", "episode_cluster_id": cluster_id,
            "actor_episode_candidate_id": candidate_id}


def _candidate(cid, external_event_id, signal_ids=None, counterparty=None, theme=None, is_demo=False):
    return {"actor_episode_candidate_id": cid, "external_event_id": external_event_id,
            "external_signal_ids": list(signal_ids or []), "counterparty_text": counterparty,
            "theme_id": theme, "is_demo": is_demo}


def _event(event_id, geo=None):
    return {"external_event_id": event_id, "action_geo_full_name": geo}


def _signals(signal_ids=(), event_ids=(), geos=(), counterparties=(), themes=(),
             first="2026-09-07", last="2026-09-07"):
    return {"signal_ids": set(signal_ids), "external_event_ids": set(event_ids), "geos": set(geos),
            "counterparties": set(counterparties), "themes": set(themes),
            "first_event_date": first, "last_event_date": last}


# ---------------------------------------------------------------------
# 純粋関数: hub値・interval gap・_compute_link
# ---------------------------------------------------------------------

def test_hub_value_is_excluded_from_scoring():
    population = {
        "c1": {"signal_ids": {"hub", "specific"}}, "c2": {"signal_ids": {"hub", "specific"}},
        "c3": {"signal_ids": {"hub"}}, "c4": {"signal_ids": {"hub"}},
    }
    hub = svc._compute_hub_values(population, "signal_ids")
    assert hub == {"hub"}


def test_interval_gap_days_overlapping_intervals_is_zero():
    assert svc._interval_gap_days("2026-09-01", "2026-09-10", "2026-09-05", "2026-09-20") == 0


def test_interval_gap_days_non_overlapping_uses_nearest_endpoints():
    assert svc._interval_gap_days("2026-09-01", "2026-09-05", "2026-09-10", "2026-09-20") == 5
    assert svc._interval_gap_days("2026-09-10", "2026-09-20", "2026-09-01", "2026-09-05") == 5


def test_related_context_from_single_specific_article():
    signals_a = _signals(signal_ids={"specific"})
    signals_b = _signals(signal_ids={"specific"})
    link = svc._compute_link("a", "b", signals_a, signals_b, set(), set(), set(), set())
    assert link["link_type"] == "related_context"
    assert link["link_score"] == svc.SIGNAL_OVERLAP_WEIGHT + svc.TIME_PROXIMITY_WEIGHT


def test_related_context_from_article_and_geo_corroboration():
    signals_a = _signals(signal_ids={"specific"}, geos={"Gulf Of Oman"})
    signals_b = _signals(signal_ids={"specific"}, geos={"Gulf Of Oman"})
    link = svc._compute_link("a", "b", signals_a, signals_b, set(), set(), set(), set())
    assert link["link_type"] == "related_context"
    assert link["link_score"] == svc.SIGNAL_OVERLAP_WEIGHT + svc.GEO_OVERLAP_WEIGHT + svc.TIME_PROXIMITY_WEIGHT


def test_no_link_when_score_below_threshold():
    signals_a = _signals()
    signals_b = _signals()
    link = svc._compute_link("a", "b", signals_a, signals_b, set(), set(), set(), set())
    assert link is None


def test_hub_values_are_excluded_when_computing_link_overlap():
    signals_a = _signals(signal_ids={"hub"}, geos={"Tehran"}, counterparties={"THE US"})
    signals_b = _signals(signal_ids={"hub"}, geos={"Tehran"}, counterparties={"THE US"})
    link = svc._compute_link("a", "b", signals_a, signals_b, {"hub"}, {"Tehran"}, {"THE US"}, set())
    assert link is None  # 全てhub除外され、time_proximityの1点だけでは閾値未満


def test_same_situation_candidate_from_shared_external_event_and_geo():
    signals_a = _signals(event_ids={"e1", "e2"}, geos={"Kyiv", "North Carolina"})
    signals_b = _signals(event_ids={"e1", "e2"}, geos={"Kyiv", "North Carolina"})
    link = svc._compute_link("iran08", "us08", signals_a, signals_b, set(), set(), set(), set())
    assert link["link_type"] == "same_situation_candidate"
    reasons = {r["signal"] for r in link["link_reasons"]}
    assert "shared_external_event" in reasons


def test_shared_external_event_is_not_hub_dampened():
    signals_a = _signals(event_ids={"e1"})
    signals_b = _signals(event_ids={"e1"})
    # e1はhub_valuesに含まれていても(通常はexternal_event_idsは_compute_hub_valuesの対象外
    # だが、仮に渡ってしまっても_compute_linkはshared_eventsをhub_valuesでフィルタしない)
    link = svc._compute_link("a", "b", signals_a, signals_b, set(), set(), set(), set())
    assert link["link_type"] == "same_situation_candidate"


def test_geo_only_evidence_does_not_create_related_context():
    """AP roundup記事由来のgeo-onlyノイズ回帰テスト: geo重複1件+time_proximityのみでは
    score=3(閾値以上)でもLinkを作らない（Quality Guard）。"""
    signals_a = _signals(geos={"Moscow, Moskva, Russia"})
    signals_b = _signals(geos={"Moscow, Moskva, Russia"})
    link = svc._compute_link("a", "b", signals_a, signals_b, set(), set(), set(), set())
    assert link is None


def test_two_independent_non_time_dimensions_without_article_still_creates_link():
    """記事重複が無くても、time以外の独立したdimensionが2種類（geo+counterparty）あれば
    Quality Guardを通過してLinkが作られることを確認する。"""
    signals_a = _signals(geos={"Gulf Of Oman"}, counterparties={"IRANIAN"})
    signals_b = _signals(geos={"Gulf Of Oman"}, counterparties={"IRANIAN"})
    link = svc._compute_link("a", "b", signals_a, signals_b, set(), set(), set(), set())
    assert link is not None
    assert link["link_type"] == "related_context"


def test_shared_external_event_forces_same_situation_even_with_zero_other_overlap():
    signals_a = _signals(event_ids={"e1"})
    signals_b = _signals(event_ids={"e1"})
    link = svc._compute_link("a", "b", signals_a, signals_b, set(), set(), set(), set())
    assert link is not None
    assert link["link_type"] == "same_situation_candidate"
    assert link["link_score"] < svc.SAME_SITUATION_SCORE_THRESHOLD  # 通常閾値未満でもoverrideで確定


# ---------------------------------------------------------------------
# write integrity boundary（RPC経由のみで書き込む）
# ---------------------------------------------------------------------

def test_rebuild_writes_only_via_apply_replace_rpc():
    client = FakeSupabaseClient(tables={
        "geo_episode_clusters": [_cluster("c1", "iran", 1, "01"), _cluster("c2", "iran", 1, "05")],
        "geo_episode_cluster_members": [_member("c1", "cand1"), _member("c2", "cand2")],
        "geo_actor_episode_candidates": [
            _candidate("cand1", "e1", signal_ids=["s1"]), _candidate("cand2", "e2", signal_ids=["s1"])],
        "geo_external_events": [_event("e1"), _event("e2")],
    })
    client.rpc = MagicMock(return_value={
        "inserted": 1, "inserted_same_situation_candidate": 0, "inserted_related_context": 1,
        "skipped_frozen_pair": 0})
    svc.recompute_situation_links(client, is_demo=False)

    client.rpc.assert_called_once()
    assert client.rpc.call_args[0][0] == "geo_situation_link_apply_replace"
    assert "geo_episode_cluster_links" not in client.inserted


def test_apply_replace_failure_leaves_no_partial_python_side_writes():
    client = FakeSupabaseClient(tables={
        "geo_episode_clusters": [_cluster("c1", "iran", 1, "01")],
        "geo_episode_cluster_members": [], "geo_actor_episode_candidates": [], "geo_external_events": [],
    })
    client.rpc = MagicMock(side_effect=svc.SituationLinkConflict("situation_link_conflict: retry"))
    try:
        svc.recompute_situation_links(client, is_demo=False)
        assert False, "ValueError（リトライ上限超過）が送出されるはず"
    except ValueError:
        pass
    assert client.rpc.call_count == svc.MAX_RECOMPUTE_RETRIES
    assert "geo_episode_cluster_links" not in client.inserted


def test_recompute_retries_on_optimistic_conflict():
    client = FakeSupabaseClient(tables={
        "geo_episode_clusters": [_cluster("c1", "iran", 1, "01")],
        "geo_episode_cluster_members": [], "geo_actor_episode_candidates": [], "geo_external_events": [],
    })
    client.rpc = MagicMock(side_effect=[
        svc.SituationLinkConflict("situation_link_conflict: retry"),
        {"inserted": 0, "inserted_same_situation_candidate": 0, "inserted_related_context": 0,
         "skipped_frozen_pair": 0},
    ])
    result = svc.recompute_situation_links(client, is_demo=False)
    assert client.rpc.call_count == 2
    assert result["candidate_links_created"] == 0


# ---------------------------------------------------------------------
# End-to-end（_LinkFakeClient）: freeze / is_demo / rejected / recompute
# ---------------------------------------------------------------------

def test_end_to_end_same_situation_and_related_context_real_data_shape():
    """実データのIRAN root08×UNITED STATES root08（shared_external_event）、
    IRAN root01×root11（signal 1件のみ）相当を再現する。"""
    client = _LinkFakeClient(tables={
        "geo_episode_clusters": [
            _cluster("iran08", "iran", 2, "08"), _cluster("us08", "us", 2, "08"),
            _cluster("iran01", "iran", 1, "01"), _cluster("iran11", "iran", 3, "11"),
        ],
        "geo_episode_cluster_members": [
            _member("iran08", "c_iran08"), _member("us08", "c_us08"),
            _member("iran01", "c_iran01"), _member("iran11", "c_iran11"),
        ],
        "geo_actor_episode_candidates": [
            _candidate("c_iran08", "e_shared1", signal_ids=[]),
            _candidate("c_us08", "e_shared1", signal_ids=[]),
            _candidate("c_iran01", "e_a", signal_ids=["presstv_article"]),
            _candidate("c_iran11", "e_b", signal_ids=["presstv_article"]),
        ],
        "geo_external_events": [
            _event("e_shared1", geo="Kyiv"), _event("e_a", geo="Tehran"), _event("e_b", geo="Tehran"),
        ],
    })
    result = svc.recompute_situation_links(client, is_demo=False)
    links = client.tables["geo_episode_cluster_links"]
    assert result["candidate_links_created"] == 2

    def _pair_type(a, b):
        for l in links:
            if {l["source_cluster_id"], l["target_cluster_id"]} == {a, b}:
                return l["link_type"]
        return None

    assert _pair_type("iran08", "us08") == "same_situation_candidate"
    assert _pair_type("iran01", "iran11") == "related_context"


def test_reviewed_link_is_frozen_against_recompute():
    client = _LinkFakeClient(tables={
        "geo_episode_clusters": [
            _cluster("c1", "iran", 1, "01", status="accepted"), _cluster("c2", "iran", 1, "05", status="accepted")],
        "geo_episode_cluster_members": [_member("c1", "cand1"), _member("c2", "cand2")],
        "geo_actor_episode_candidates": [
            _candidate("cand1", "e1", signal_ids=["s1"]), _candidate("cand2", "e2", signal_ids=["s1"])],
        "geo_external_events": [_event("e1"), _event("e2")],
    })
    svc.recompute_situation_links(client, is_demo=False)
    link = client.tables["geo_episode_cluster_links"][0]
    svc.update_situation_link_status(
        client, cluster_link_id=link["cluster_link_id"], link_status="accepted", reviewed_by="analyst1")

    svc.recompute_situation_links(client, is_demo=False)
    links_after = client.tables["geo_episode_cluster_links"]
    assert len(links_after) == 1
    assert links_after[0]["cluster_link_id"] == link["cluster_link_id"]
    assert links_after[0]["link_status"] == "accepted"


def test_rejected_cluster_excluded_from_new_link_generation():
    client = _LinkFakeClient(tables={
        "geo_episode_clusters": [
            _cluster("c1", "iran", 1, "01", status="rejected"), _cluster("c2", "iran", 1, "05")],
        "geo_episode_cluster_members": [_member("c1", "cand1"), _member("c2", "cand2")],
        "geo_actor_episode_candidates": [
            _candidate("cand1", "e1", signal_ids=["s1"]), _candidate("cand2", "e2", signal_ids=["s1"])],
        "geo_external_events": [_event("e1"), _event("e2")],
    })
    result = svc.recompute_situation_links(client, is_demo=False)
    assert result["candidate_links_created"] == 0
    assert client.tables.get("geo_episode_cluster_links", []) == []


def test_cross_demo_link_is_never_created():
    client = _LinkFakeClient(tables={
        "geo_episode_clusters": [
            _cluster("c1", "iran", 1, "01", is_demo=False), _cluster("c2", "iran", 1, "05", is_demo=True)],
        "geo_episode_cluster_members": [_member("c1", "cand1"), _member("c2", "cand2")],
        "geo_actor_episode_candidates": [
            _candidate("cand1", "e1", signal_ids=["s1"], is_demo=False),
            _candidate("cand2", "e2", signal_ids=["s1"], is_demo=True)],
        "geo_external_events": [_event("e1"), _event("e2")],
    })
    result_real = svc.recompute_situation_links(client, is_demo=False)
    result_demo = svc.recompute_situation_links(client, is_demo=True)
    assert result_real["candidate_links_created"] == 0
    assert result_demo["candidate_links_created"] == 0


def test_recompute_is_scoped_by_is_demo():
    client = _LinkFakeClient(tables={
        "geo_episode_clusters": [
            _cluster("c1", "iran", 1, "01", is_demo=False), _cluster("c2", "iran", 1, "05", is_demo=False),
            _cluster("d1", "iran", 1, "01", is_demo=True), _cluster("d2", "iran", 1, "05", is_demo=True),
        ],
        "geo_episode_cluster_members": [
            _member("c1", "cand1"), _member("c2", "cand2"), _member("d1", "dcand1"), _member("d2", "dcand2")],
        "geo_actor_episode_candidates": [
            _candidate("cand1", "e1", signal_ids=["s1"], is_demo=False),
            _candidate("cand2", "e2", signal_ids=["s1"], is_demo=False),
            _candidate("dcand1", "de1", signal_ids=["ds1"], is_demo=True),
            _candidate("dcand2", "de2", signal_ids=["ds1"], is_demo=True),
        ],
        "geo_external_events": [_event("e1"), _event("e2"), _event("de1"), _event("de2")],
    })
    svc.recompute_situation_links(client, is_demo=False)
    real_links_before = list(client.tables["geo_episode_cluster_links"])

    svc.recompute_situation_links(client, is_demo=True)
    real_links_after = [l for l in client.tables["geo_episode_cluster_links"] if not l["is_demo"]]
    assert real_links_before == real_links_after


def test_apply_replace_detects_population_snapshot_conflict():
    client = _LinkFakeClient(tables={
        "geo_episode_clusters": [_cluster("c1", "iran", 1, "01")],
        "geo_episode_cluster_members": [], "geo_actor_episode_candidates": [], "geo_external_events": [],
    })
    stale_params = {
        "p_is_demo": False, "p_expected_frozen_link_ids": [], "p_expected_candidate_link_ids": [],
        "p_expected_population_cluster_ids": [],  # 古いsnapshot(空)、実際にはc1が存在する
        "p_new_links": [],
    }
    try:
        client.rpc("geo_situation_link_apply_replace", stale_params)
        assert False, "situation_link_conflictが送出されるはず"
    except svc.SituationLinkConflict:
        pass


def test_apply_replace_rejects_new_link_referencing_rejected_cluster():
    client = _LinkFakeClient(tables={
        "geo_episode_clusters": [
            _cluster("c1", "iran", 1, "01", status="rejected"), _cluster("c2", "iran", 1, "05")],
        "geo_episode_cluster_members": [], "geo_actor_episode_candidates": [], "geo_external_events": [],
    })
    bad_params = {
        "p_is_demo": False, "p_expected_frozen_link_ids": [], "p_expected_candidate_link_ids": [],
        "p_expected_population_cluster_ids": ["c2"],
        "p_new_links": [{"source_cluster_id": "c1", "target_cluster_id": "c2",
                          "link_type": "related_context", "link_score": 3, "link_reasons": []}],
    }
    try:
        client.rpc("geo_situation_link_apply_replace", bad_params)
        assert False, "situation_link_validation_errorが送出されるはず"
    except svc.SituationLinkValidationError:
        pass


def test_apply_replace_skips_pair_colliding_with_frozen_link():
    client = _LinkFakeClient(tables={
        "geo_episode_clusters": [
            _cluster("c1", "iran", 1, "01", status="accepted"), _cluster("c2", "iran", 1, "05", status="accepted")],
        "geo_episode_cluster_members": [], "geo_actor_episode_candidates": [], "geo_external_events": [],
        "geo_episode_cluster_links": [{
            "cluster_link_id": "existing-frozen", "source_cluster_id": "c1", "target_cluster_id": "c2",
            "is_demo": False, "link_type": "related_context", "link_score": 4, "link_reasons": [],
            "link_status": "accepted", "reviewed_at": "x", "reviewed_by": "analyst1", "review_notes": None,
            "raw_metadata": None, "created_at": "x", "updated_at": "x",
        }],
    })
    params = {
        "p_is_demo": False, "p_expected_frozen_link_ids": ["existing-frozen"],
        "p_expected_candidate_link_ids": [], "p_expected_population_cluster_ids": ["c1", "c2"],
        "p_new_links": [{"source_cluster_id": "c1", "target_cluster_id": "c2",
                          "link_type": "same_situation_candidate", "link_score": 9, "link_reasons": []}],
    }
    result = client.rpc("geo_situation_link_apply_replace", params)
    assert result["skipped_frozen_pair"] == 1
    assert result["inserted"] == 0
    links = client.tables["geo_episode_cluster_links"]
    assert len(links) == 1
    assert links[0]["cluster_link_id"] == "existing-frozen"
    assert links[0]["link_status"] == "accepted"


# ---------------------------------------------------------------------
# Human Review状態機械 / freezeライフサイクル
# ---------------------------------------------------------------------

def test_link_transition_blocked_when_either_endpoint_cluster_is_still_candidate():
    client = FakeSupabaseClient(tables={
        "geo_episode_clusters": [
            _cluster("c1", "iran", 1, "01", status="accepted"), _cluster("c2", "iran", 1, "05", status="candidate")],
        "geo_episode_cluster_links": [{
            "cluster_link_id": "l1", "source_cluster_id": "c1", "target_cluster_id": "c2",
            "is_demo": False, "link_type": "related_context", "link_score": 4, "link_reasons": [],
            "link_status": "candidate", "reviewed_at": None, "reviewed_by": None, "review_notes": None,
        }],
    })
    try:
        svc.update_situation_link_status(client, cluster_link_id="l1", link_status="reviewed", reviewed_by="analyst1")
        assert False, "ValueErrorが送出されるはず"
    except ValueError:
        pass


def test_link_transition_to_accepted_requires_both_endpoints_accepted():
    client = FakeSupabaseClient(tables={
        "geo_episode_clusters": [
            _cluster("c1", "iran", 1, "01", status="reviewed"), _cluster("c2", "iran", 1, "05", status="reviewed")],
        "geo_episode_cluster_links": [{
            "cluster_link_id": "l1", "source_cluster_id": "c1", "target_cluster_id": "c2",
            "is_demo": False, "link_type": "related_context", "link_score": 4, "link_reasons": [],
            "link_status": "candidate", "reviewed_at": None, "reviewed_by": None, "review_notes": None,
        }],
    })
    try:
        svc.update_situation_link_status(client, cluster_link_id="l1", link_status="accepted", reviewed_by="analyst1")
        assert False, "ValueError（両端reviewedのみではaccepted不可）が送出されるはず"
    except ValueError:
        pass

    for c in client.tables["geo_episode_clusters"]:
        c["cluster_status"] = "accepted"
    result = svc.update_situation_link_status(client, cluster_link_id="l1", link_status="accepted", reviewed_by="analyst1")
    assert result["link_status"] == "accepted"


def test_link_transition_allowed_when_both_endpoint_clusters_are_frozen():
    client = FakeSupabaseClient(tables={
        "geo_episode_clusters": [
            _cluster("c1", "iran", 1, "01", status="reviewed"), _cluster("c2", "iran", 1, "05", status="rejected")],
        "geo_episode_cluster_links": [{
            "cluster_link_id": "l1", "source_cluster_id": "c1", "target_cluster_id": "c2",
            "is_demo": False, "link_type": "related_context", "link_score": 4, "link_reasons": [],
            "link_status": "candidate", "reviewed_at": None, "reviewed_by": None, "review_notes": None,
        }],
    })
    result = svc.update_situation_link_status(client, cluster_link_id="l1", link_status="rejected", reviewed_by="analyst1")
    assert result["link_status"] == "rejected"


def test_update_situation_link_status_does_not_touch_clusters_or_actor_intelligence():
    client = FakeSupabaseClient(tables={
        "geo_episode_clusters": [
            _cluster("c1", "iran", 1, "01", status="accepted"), _cluster("c2", "iran", 1, "05", status="accepted")],
        "geo_episode_cluster_links": [{
            "cluster_link_id": "l1", "source_cluster_id": "c1", "target_cluster_id": "c2",
            "is_demo": False, "link_type": "related_context", "link_score": 4, "link_reasons": [],
            "link_status": "candidate", "reviewed_at": None, "reviewed_by": None, "review_notes": None,
        }],
    })
    svc.update_situation_link_status(client, cluster_link_id="l1", link_status="accepted", reviewed_by="analyst1")
    assert not any(t in ("geo_episode_clusters", "geo_entities", "geo_knowledge_items") for t, _, _ in client.updated)


# ---------------------------------------------------------------------
# スキーマ構造テスト
# ---------------------------------------------------------------------

def test_geo_episode_cluster_links_schema_enforces_canonical_order():
    import re
    sql_path = Path(__file__).parent.parent / "sql" / "2026-09-08_geo_episode_cluster_links.sql"
    sql_text = sql_path.read_text(encoding="utf-8")
    match = re.search(r"create table public\.geo_episode_cluster_links \((.*?)\n\);", sql_text, re.DOTALL)
    assert match, "geo_episode_cluster_linksのDDLが見つかりません"
    ddl_body = match.group(1)
    assert "check (source_cluster_id < target_cluster_id)" in ddl_body
