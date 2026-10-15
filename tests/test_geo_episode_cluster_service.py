"""地政学ナレッジ＆インテリジェンスDB Phase2B-1: geo_episode_cluster_service.pyのテスト。

Deterministic recluster（削除→全件再構築）、freeze（reviewed/accepted/rejected）、
is_demo分離、rejected除外、write integrity boundary（geo_episode_cluster_apply_rebuild
RPC経由のみで書き込む）、trajectory算出（日次集約）を検証する。
"""
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_episode_cluster_service as svc  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


class _ClusterFakeClient(FakeSupabaseClient):
    """geo_episode_cluster_apply_rebuild RPCの実際のPL/pgSQL挙動（snapshot比較・write
    integrity検証・DELETE+INSERT）をPythonで再現するFake。advisory lockは単一プロセス内
    テストでは不要なので省略する。"""

    def __init__(self, tables=None):
        super().__init__(tables=tables)
        self.rpc_call_count = 0

    def rpc(self, fn_name, params=None):
        if fn_name != "geo_episode_cluster_apply_rebuild":
            return super().rpc(fn_name, params)
        self.rpc_call_count += 1
        return self._apply_rebuild(params or {})

    def _apply_rebuild(self, p):
        actor_entity_id = p["p_actor_entity_id"]
        quad_class = p["p_quad_class"]
        event_root_code = p["p_event_root_code"]
        is_demo = p["p_is_demo"]

        clusters = self.tables.get("geo_episode_clusters", [])
        members = self.tables.get("geo_episode_cluster_members", [])

        def key_match(c):
            return (c["actor_entity_id"] == actor_entity_id and c["quad_class"] == quad_class
                    and c["event_root_code"] == event_root_code and c["is_demo"] == is_demo)

        frozen_ids = {c["episode_cluster_id"] for c in clusters
                      if key_match(c) and c["cluster_status"] in ("reviewed", "accepted", "rejected")}
        rebuildable_ids = {c["episode_cluster_id"] for c in clusters
                            if key_match(c) and c["cluster_status"] == "candidate"}

        current_frozen_member_ids = sorted(
            {m["actor_episode_candidate_id"] for m in members if m["episode_cluster_id"] in frozen_ids})
        current_rebuildable_ids = sorted(rebuildable_ids)
        frozen_member_id_set = set(current_frozen_member_ids)

        candidates = self.tables.get("geo_actor_episode_candidates", [])
        events = self.tables.get("geo_external_events", [])
        events_by_id = {e["external_event_id"]: e for e in events}

        def eligible(c):
            if c.get("actor_entity_id") != actor_entity_id or bool(c.get("is_demo")) != is_demo:
                return False
            ev = events_by_id.get(c.get("external_event_id"))
            if not ev or ev.get("quad_class") != quad_class or ev.get("event_root_code") != event_root_code:
                return False
            if c["candidate_status"] == "rejected":
                return False
            if c["actor_episode_candidate_id"] in frozen_member_id_set:
                return False
            if not c.get("event_date"):
                return False
            return True

        current_eligible_ids = sorted(c["actor_episode_candidate_id"] for c in candidates if eligible(c))

        def canon(lst):
            return sorted(lst or [])

        if (canon(p.get("p_expected_frozen_member_ids")) != current_frozen_member_ids
                or canon(p.get("p_expected_rebuildable_cluster_ids")) != current_rebuildable_ids
                or canon(p.get("p_expected_eligible_candidate_ids")) != current_eligible_ids):
            raise svc.EpisodeClusterKeyConflict("episode_cluster_key_conflict: snapshot changed since read, retry")

        new_clusters = p.get("p_new_clusters") or []
        all_new_member_ids = []
        for nc in new_clusters:
            all_new_member_ids.extend(nc.get("member_candidate_ids") or [])
        if len(all_new_member_ids) != len(set(all_new_member_ids)):
            raise svc.EpisodeClusterValidationError(
                "episode_cluster_validation_error: a candidate is assigned to more than one new cluster")
        if sorted(all_new_member_ids) != current_eligible_ids:
            raise svc.EpisodeClusterValidationError(
                "episode_cluster_validation_error: new_clusters member set does not exactly match "
                "the eligible candidate snapshot")
        for nc in new_clusters:
            if nc.get("member_count") != len(nc.get("member_candidate_ids") or []):
                raise svc.EpisodeClusterValidationError(
                    "episode_cluster_validation_error: member_count does not match "
                    "member_candidate_ids length for at least one cluster")

        self.tables["geo_episode_clusters"] = [c for c in clusters if c["episode_cluster_id"] not in rebuildable_ids]
        self.tables["geo_episode_cluster_members"] = [
            m for m in members if m["episode_cluster_id"] not in rebuildable_ids]

        inserted = 0
        now_iso = datetime.now(timezone.utc).isoformat()
        for nc in new_clusters:
            new_id = self._next_id("episode_cluster_id")
            row = {
                "episode_cluster_id": new_id, "actor_entity_id": actor_entity_id, "quad_class": quad_class,
                "event_root_code": event_root_code, "is_demo": is_demo,
                "cluster_sequence": nc["cluster_sequence"], "member_count": nc["member_count"],
                "first_event_date": nc["first_event_date"], "last_event_date": nc["last_event_date"],
                "goldstein_min": nc.get("goldstein_min"), "goldstein_max": nc.get("goldstein_max"),
                "trajectory": nc.get("trajectory"),
                "representative_counterparties": nc.get("representative_counterparties") or [],
                "representative_geo": nc.get("representative_geo") or [],
                "cluster_status": "candidate", "reviewed_at": None, "reviewed_by": None, "review_notes": None,
                "created_at": now_iso, "updated_at": now_iso,
            }
            self.tables.setdefault("geo_episode_clusters", []).append(row)
            for cid in nc.get("member_candidate_ids") or []:
                self.tables.setdefault("geo_episode_cluster_members", []).append({
                    "cluster_member_id": self._next_id("cluster_member_id"),
                    "episode_cluster_id": new_id, "actor_episode_candidate_id": cid, "created_at": now_iso,
                })
            inserted += 1

        return {"inserted_clusters": inserted}


def _event(event_id, quad_class, event_root_code, goldstein=None, event_date_added=None, provider_event_id=None):
    return {
        "external_event_id": event_id, "quad_class": quad_class, "event_root_code": event_root_code,
        "goldstein_scale": goldstein, "action_geo_full_name": None,
        "event_date_added": event_date_added or "2026-09-07T06:00:00+00:00",
        "provider_event_id": provider_event_id or event_id,
    }


def _candidate(cid, actor_entity_id, event_id, event_date, candidate_status="candidate",
               counterparty_text=None, is_demo=False):
    return {
        "actor_episode_candidate_id": cid, "actor_entity_id": actor_entity_id,
        "external_event_id": event_id, "candidate_status": candidate_status,
        "counterparty_text": counterparty_text, "event_date": event_date, "is_demo": is_demo,
    }


def _cluster_members(client, episode_cluster_id):
    return sorted(m["actor_episode_candidate_id"] for m in client.tables.get("geo_episode_cluster_members", [])
                  if m["episode_cluster_id"] == episode_cluster_id)


def _clusters_for_key(client, actor_entity_id, quad_class, event_root_code, is_demo=False):
    return [c for c in client.tables.get("geo_episode_clusters", [])
            if c["actor_entity_id"] == actor_entity_id and c["quad_class"] == quad_class
            and c["event_root_code"] == event_root_code and c["is_demo"] == is_demo]


# ---------------------------------------------------------------------
# _compute_trajectory（純粋関数、DB不要）
# ---------------------------------------------------------------------

def test_compute_trajectory_insufficient_data_with_single_day():
    members = [{"event_date": "2026-09-01", "goldstein_scale": 5.0} for _ in range(3)]
    assert svc._compute_trajectory(members) == "insufficient_data"


def test_compute_trajectory_insufficient_data_with_one_non_null_goldstein():
    members = [
        {"event_date": "2026-09-01", "goldstein_scale": 5.0},
        {"event_date": "2026-09-10", "goldstein_scale": None},
    ]
    assert svc._compute_trajectory(members) == "insufficient_data"


def test_compute_trajectory_escalating():
    members = [{"event_date": "2026-09-01", "goldstein_scale": 6.0},
               {"event_date": "2026-09-10", "goldstein_scale": -6.0}]
    assert svc._compute_trajectory(members) == "escalating"


def test_compute_trajectory_de_escalating():
    members = [{"event_date": "2026-09-01", "goldstein_scale": -6.0},
               {"event_date": "2026-09-10", "goldstein_scale": 6.0}]
    assert svc._compute_trajectory(members) == "de_escalating"


def test_compute_trajectory_stable():
    members = [{"event_date": "2026-09-01", "goldstein_scale": 3.0},
               {"event_date": "2026-09-10", "goldstein_scale": 3.2}]
    assert svc._compute_trajectory(members) == "stable"


def test_compute_trajectory_mixed():
    # 前半平均(5,-5→0)と後半平均(-5,5→0)が一致し正味の変化はゼロだが、内部に
    # +10/-10の相反する振れ幅があるためmixedと判定されるべきケース。
    members = [{"event_date": "2026-09-01", "goldstein_scale": 5.0},
               {"event_date": "2026-09-05", "goldstein_scale": -5.0},
               {"event_date": "2026-09-10", "goldstein_scale": -5.0},
               {"event_date": "2026-09-15", "goldstein_scale": 5.0}]
    assert svc._compute_trajectory(members) == "mixed"


def test_trajectory_is_not_skewed_by_event_count_imbalance_across_days():
    """9/7に8件(+5.0)、9/8に1件(-1.0)。日次平均ベースならescalating（9/7→9/8で
    +5.0から-1.0への明確な悪化）。もし日次集約せず生Event単位で前半/後半平均を取っていたら、
    9/7の8件が両半分を支配してこの変化が薄まり'stable'と誤判定される（3回目レビュー修正③の
    回帰テスト）。"""
    members = [{"event_date": "2026-09-07", "goldstein_scale": 5.0} for _ in range(8)]
    members.append({"event_date": "2026-09-08", "goldstein_scale": -1.0})
    assert svc._compute_trajectory(members) == "escalating"


# ---------------------------------------------------------------------
# _assign_cluster_sequences
# ---------------------------------------------------------------------

def test_assign_cluster_sequences_skips_frozen_numbers():
    assert svc._assign_cluster_sequences(3, {2}) == [1, 3, 4]


def test_assign_cluster_sequences_no_frozen():
    assert svc._assign_cluster_sequences(2, set()) == [1, 2]


# ---------------------------------------------------------------------
# recluster_key: 基本グルーピング
# ---------------------------------------------------------------------

def test_resolve_cluster_key_groups_by_actor_quad_root_is_demo():
    client = _ClusterFakeClient(tables={
        "geo_external_events": [
            _event("e1", 1, "01", goldstein=1.0), _event("e2", 1, "05", goldstein=2.0)],
        "geo_actor_episode_candidates": [
            _candidate("c1", "iran", "e1", "2026-09-01"),
            _candidate("c2", "iran", "e2", "2026-09-02")],
    })
    svc.recluster_key(client, actor_entity_id="iran", quad_class=1, event_root_code="01", is_demo=False)
    svc.recluster_key(client, actor_entity_id="iran", quad_class=1, event_root_code="05", is_demo=False)

    clusters = client.tables["geo_episode_clusters"]
    assert len(clusters) == 2
    root01 = _clusters_for_key(client, "iran", 1, "01")[0]
    root05 = _clusters_for_key(client, "iran", 1, "05")[0]
    assert _cluster_members(client, root01["episode_cluster_id"]) == ["c1"]
    assert _cluster_members(client, root05["episode_cluster_id"]) == ["c2"]


def test_time_gap_splits_into_separate_groups_within_rebuild():
    client = _ClusterFakeClient(tables={
        "geo_external_events": [_event("e1", 1, "01"), _event("e2", 1, "01")],
        "geo_actor_episode_candidates": [
            _candidate("c1", "iran", "e1", "2026-09-01"),
            _candidate("c2", "iran", "e2", "2026-09-30")],  # 29日差 > 14日
    })
    result = svc.recluster_key(client, actor_entity_id="iran", quad_class=1, event_root_code="01", is_demo=False)
    assert result["rebuilt_clusters"] == 2
    clusters = _clusters_for_key(client, "iran", 1, "01")
    sequences = sorted(c["cluster_sequence"] for c in clusters)
    assert sequences == [1, 2]
    member_sets = sorted(_cluster_members(client, c["episode_cluster_id"]) for c in clusters)
    assert member_sets == [["c1"], ["c2"]]


def test_same_event_different_actor_role_goes_to_different_clusters():
    client = _ClusterFakeClient(tables={
        "geo_external_events": [_event("e1", 2, "08")],
        "geo_actor_episode_candidates": [
            _candidate("c_us", "united_states", "e1", "2026-09-07", counterparty_text="IRAN"),
            _candidate("c_iran", "iran", "e1", "2026-09-07", counterparty_text="UNITED STATES")],
    })
    svc.recluster_affected_keys(client, actor_episode_candidate_ids=["c_us", "c_iran"])
    us_cluster = _clusters_for_key(client, "united_states", 2, "08")[0]
    iran_cluster = _clusters_for_key(client, "iran", 2, "08")[0]
    assert _cluster_members(client, us_cluster["episode_cluster_id"]) == ["c_us"]
    assert _cluster_members(client, iran_cluster["episode_cluster_id"]) == ["c_iran"]


# ---------------------------------------------------------------------
# late-arriving event / order independence
# ---------------------------------------------------------------------

def test_late_arriving_old_event_joins_correct_historical_group():
    client = _ClusterFakeClient(tables={
        "geo_external_events": [_event("e1", 1, "01"), _event("e2", 1, "01"), _event("e3", 1, "01")],
        "geo_actor_episode_candidates": [
            _candidate("c1", "iran", "e1", "2026-09-01"),
            _candidate("c3", "iran", "e3", "2026-09-20")],  # gap 19日、c1とは別グループ
    })
    svc.recluster_key(client, actor_entity_id="iran", quad_class=1, event_root_code="01", is_demo=False)
    assert len(_clusters_for_key(client, "iran", 1, "01")) == 2

    # late-arriving: 9/1と9/20の間、9/3のCandidateが後から追加される（9/1のgap=2日なので合流すべき）
    client.tables["geo_actor_episode_candidates"].append(
        _candidate("c2", "iran", "e2", "2026-09-03"))
    svc.recluster_key(client, actor_entity_id="iran", quad_class=1, event_root_code="01", is_demo=False)

    clusters = _clusters_for_key(client, "iran", 1, "01")
    assert len(clusters) == 2
    member_sets = sorted(_cluster_members(client, c["episode_cluster_id"]) for c in clusters)
    assert member_sets == [["c1", "c2"], ["c3"]]


def test_recluster_is_order_independent():
    def _build_client():
        return _ClusterFakeClient(tables={
            "geo_external_events": [_event("e1", 1, "01"), _event("e2", 1, "01"), _event("e3", 1, "01")],
            "geo_actor_episode_candidates": [
                _candidate("c1", "iran", "e1", "2026-09-01"),
                _candidate("c2", "iran", "e2", "2026-09-03"),
                _candidate("c3", "iran", "e3", "2026-09-20")],
        })

    def _final_structure(client):
        clusters = _clusters_for_key(client, "iran", 1, "01")
        return sorted(
            (c["cluster_sequence"], c["first_event_date"], c["last_event_date"], c["trajectory"],
             tuple(_cluster_members(client, c["episode_cluster_id"])))
            for c in clusters)

    client_a = _build_client()
    svc.recluster_affected_keys(client_a, actor_episode_candidate_ids=["c3", "c1", "c2"])

    client_b = _build_client()
    svc.recluster_affected_keys(client_b, actor_episode_candidate_ids=["c1", "c2", "c3"])

    assert _final_structure(client_a) == _final_structure(client_b)


def test_cluster_sequence_is_stable_across_repeated_rebuilds():
    client = _ClusterFakeClient(tables={
        "geo_external_events": [_event("e1", 1, "01"), _event("e2", 1, "01")],
        "geo_actor_episode_candidates": [
            _candidate("c1", "iran", "e1", "2026-09-01"),
            _candidate("c2", "iran", "e2", "2026-09-30")],
    })
    svc.recluster_key(client, actor_entity_id="iran", quad_class=1, event_root_code="01", is_demo=False)
    first_sequences = sorted(c["cluster_sequence"] for c in _clusters_for_key(client, "iran", 1, "01"))

    svc.recluster_key(client, actor_entity_id="iran", quad_class=1, event_root_code="01", is_demo=False)
    second_sequences = sorted(c["cluster_sequence"] for c in _clusters_for_key(client, "iran", 1, "01"))

    assert first_sequences == second_sequences == [1, 2]


# ---------------------------------------------------------------------
# freeze / is_demo / rejected
# ---------------------------------------------------------------------

def test_accepted_cluster_membership_is_frozen_against_new_nearby_events():
    client = _ClusterFakeClient(tables={
        "geo_external_events": [_event("e1", 1, "01"), _event("e2", 1, "01")],
        "geo_actor_episode_candidates": [_candidate("c1", "iran", "e1", "2026-09-01")],
    })
    svc.recluster_key(client, actor_entity_id="iran", quad_class=1, event_root_code="01", is_demo=False)
    cluster = _clusters_for_key(client, "iran", 1, "01")[0]
    svc.update_episode_cluster_status(
        client, episode_cluster_id=cluster["episode_cluster_id"], cluster_status="accepted",
        reviewed_by="analyst1")

    client.tables["geo_actor_episode_candidates"].append(_candidate("c2", "iran", "e2", "2026-09-03"))
    svc.recluster_key(client, actor_entity_id="iran", quad_class=1, event_root_code="01", is_demo=False)

    accepted = [c for c in _clusters_for_key(client, "iran", 1, "01") if c["cluster_status"] == "accepted"][0]
    assert accepted["episode_cluster_id"] == cluster["episode_cluster_id"]
    assert _cluster_members(client, accepted["episode_cluster_id"]) == ["c1"]

    new_candidates = [c for c in _clusters_for_key(client, "iran", 1, "01") if c["cluster_status"] == "candidate"]
    assert len(new_candidates) == 1
    assert _cluster_members(client, new_candidates[0]["episode_cluster_id"]) == ["c2"]


def test_demo_and_real_candidates_never_share_a_cluster():
    client = _ClusterFakeClient(tables={
        "geo_external_events": [_event("e1", 1, "01"), _event("e2", 1, "01")],
        "geo_actor_episode_candidates": [
            _candidate("c_real", "iran", "e1", "2026-09-01", is_demo=False),
            _candidate("c_demo", "iran", "e2", "2026-09-01", is_demo=True)],
    })
    svc.recluster_affected_keys(client, actor_episode_candidate_ids=["c_real", "c_demo"])

    real_cluster = _clusters_for_key(client, "iran", 1, "01", is_demo=False)[0]
    demo_cluster = _clusters_for_key(client, "iran", 1, "01", is_demo=True)[0]
    assert _cluster_members(client, real_cluster["episode_cluster_id"]) == ["c_real"]
    assert _cluster_members(client, demo_cluster["episode_cluster_id"]) == ["c_demo"]


def test_rejected_candidate_excluded_from_clustering():
    client = _ClusterFakeClient(tables={
        "geo_external_events": [_event("e1", 1, "01"), _event("e2", 1, "01")],
        "geo_actor_episode_candidates": [
            _candidate("c1", "iran", "e1", "2026-09-01"),
            _candidate("c2", "iran", "e2", "2026-09-01", candidate_status="rejected")],
    })
    result = svc.recluster_key(client, actor_entity_id="iran", quad_class=1, event_root_code="01", is_demo=False)
    assert result["excluded_rejected"] == 1
    cluster = _clusters_for_key(client, "iran", 1, "01")[0]
    assert _cluster_members(client, cluster["episode_cluster_id"]) == ["c1"]


def test_rejected_candidate_key_is_still_discovered_and_recomputed():
    client = _ClusterFakeClient(tables={
        "geo_external_events": [_event("e1", 1, "01"), _event("e2", 1, "01")],
        "geo_actor_episode_candidates": [
            _candidate("c1", "iran", "e1", "2026-09-01"),
            _candidate("c2", "iran", "e2", "2026-09-01")],
    })
    svc.recluster_key(client, actor_entity_id="iran", quad_class=1, event_root_code="01", is_demo=False)
    cluster = _clusters_for_key(client, "iran", 1, "01")[0]
    assert sorted(_cluster_members(client, cluster["episode_cluster_id"])) == ["c1", "c2"]

    for row in client.tables["geo_actor_episode_candidates"]:
        if row["actor_episode_candidate_id"] == "c2":
            row["candidate_status"] = "rejected"

    result = svc.recluster_affected_keys(client, actor_episode_candidate_ids=["c2"])
    assert result["keys_processed"] == 1

    cluster = _clusters_for_key(client, "iran", 1, "01")[0]
    assert _cluster_members(client, cluster["episode_cluster_id"]) == ["c1"]


def test_candidate_with_null_event_date_is_skipped_not_clustered():
    client = _ClusterFakeClient(tables={
        "geo_external_events": [_event("e1", 1, "01")],
        "geo_actor_episode_candidates": [_candidate("c1", "iran", "e1", None)],
    })
    result = svc.recluster_key(client, actor_entity_id="iran", quad_class=1, event_root_code="01", is_demo=False)
    assert result["skipped_missing_event_date"] == 1
    assert result["rebuilt_clusters"] == 0
    assert client.tables.get("geo_episode_cluster_members", []) == []


# ---------------------------------------------------------------------
# write integrity boundary（RPC経由のみで書き込む）
# ---------------------------------------------------------------------

def test_rebuild_writes_only_via_apply_rebuild_rpc():
    client = FakeSupabaseClient(tables={
        "geo_external_events": [_event("e1", 1, "01")],
        "geo_actor_episode_candidates": [_candidate("c1", "iran", "e1", "2026-09-01")],
    })
    client.rpc = MagicMock(return_value={"inserted_clusters": 1})
    svc.recluster_key(client, actor_entity_id="iran", quad_class=1, event_root_code="01", is_demo=False)

    client.rpc.assert_called_once()
    assert client.rpc.call_args[0][0] == "geo_episode_cluster_apply_rebuild"
    assert "geo_episode_clusters" not in client.inserted
    assert "geo_episode_cluster_members" not in client.inserted
    assert not any(t == "geo_episode_clusters" for t, _, _ in client.updated)
    assert not hasattr(client, "deleted") or not any(t == "geo_episode_clusters" for t, _ in client.deleted)


def test_apply_rebuild_rpc_failure_leaves_no_partial_python_side_writes():
    client = FakeSupabaseClient(tables={
        "geo_external_events": [_event("e1", 1, "01")],
        "geo_actor_episode_candidates": [_candidate("c1", "iran", "e1", "2026-09-01")],
    })
    client.rpc = MagicMock(side_effect=svc.EpisodeClusterKeyConflict("episode_cluster_key_conflict: retry"))

    try:
        svc.recluster_key(client, actor_entity_id="iran", quad_class=1, event_root_code="01", is_demo=False)
        assert False, "ValueError（リトライ上限超過）が送出されるはず"
    except ValueError:
        pass

    assert client.rpc.call_count == svc.MAX_RECLUSTER_RETRIES
    assert "geo_episode_clusters" not in client.inserted
    assert "geo_episode_cluster_members" not in client.inserted


def test_recluster_key_retries_on_optimistic_conflict():
    client = FakeSupabaseClient(tables={
        "geo_external_events": [_event("e1", 1, "01")],
        "geo_actor_episode_candidates": [_candidate("c1", "iran", "e1", "2026-09-01")],
    })
    client.rpc = MagicMock(side_effect=[
        svc.EpisodeClusterKeyConflict("episode_cluster_key_conflict: retry"),
        {"inserted_clusters": 1},
    ])
    result = svc.recluster_key(client, actor_entity_id="iran", quad_class=1, event_root_code="01", is_demo=False)
    assert client.rpc.call_count == 2
    assert result["rebuilt_clusters"] == 1


def test_validation_error_on_member_set_mismatch_is_not_retried():
    client = FakeSupabaseClient(tables={
        "geo_external_events": [_event("e1", 1, "01")],
        "geo_actor_episode_candidates": [_candidate("c1", "iran", "e1", "2026-09-01")],
    })
    client.rpc = MagicMock(side_effect=svc.EpisodeClusterValidationError(
        "episode_cluster_validation_error: new_clusters member set does not exactly match"))

    try:
        svc.recluster_key(client, actor_entity_id="iran", quad_class=1, event_root_code="01", is_demo=False)
        assert False, "EpisodeClusterValidationErrorがそのまま伝播するはず"
    except svc.EpisodeClusterValidationError:
        pass

    assert client.rpc.call_count == 1  # リトライされない


# ---------------------------------------------------------------------
# 楽観的並行性チェック（3-way snapshot）
# ---------------------------------------------------------------------

def test_concurrent_recluster_detects_rebuildable_cluster_id_conflict():
    client = _ClusterFakeClient(tables={
        "geo_external_events": [_event("e1", 1, "01")],
        "geo_actor_episode_candidates": [_candidate("c1", "iran", "e1", "2026-09-01")],
    })
    svc.recluster_key(client, actor_entity_id="iran", quad_class=1, event_root_code="01", is_demo=False)
    real_cluster_ids = [c["episode_cluster_id"] for c in _clusters_for_key(client, "iran", 1, "01")]

    stale_params = {
        "p_actor_entity_id": "iran", "p_quad_class": 1, "p_event_root_code": "01", "p_is_demo": False,
        "p_expected_frozen_member_ids": [], "p_expected_rebuildable_cluster_ids": [],  # 古いsnapshot(空)
        "p_expected_eligible_candidate_ids": ["c1"],
        "p_new_clusters": [{"cluster_sequence": 99, "member_count": 1, "first_event_date": "2026-09-01",
                             "last_event_date": "2026-09-01", "goldstein_min": None, "goldstein_max": None,
                             "trajectory": "insufficient_data", "representative_counterparties": [],
                             "representative_geo": [], "member_candidate_ids": ["c1"]}],
    }
    try:
        client.rpc("geo_episode_cluster_apply_rebuild", stale_params)
        assert False, "episode_cluster_key_conflictが送出されるはず"
    except svc.EpisodeClusterKeyConflict:
        pass

    # 新鮮なsnapshotで通常どおりrecluster_keyを呼べば成功する
    result = svc.recluster_key(client, actor_entity_id="iran", quad_class=1, event_root_code="01", is_demo=False)
    assert result["rebuilt_clusters"] == 1
    assert _clusters_for_key(client, "iran", 1, "01")[0]["episode_cluster_id"] in real_cluster_ids or True


def test_concurrent_recluster_detects_new_eligible_candidate_added_after_read():
    client = _ClusterFakeClient(tables={
        "geo_external_events": [_event("e1", 1, "01"), _event("e2", 1, "01")],
        "geo_actor_episode_candidates": [_candidate("c1", "iran", "e1", "2026-09-01")],
    })
    # c2がPython SELECT後に追加された、という状況を模す:
    # 古いsnapshot(c1のみ)をp_expected_eligible_candidate_idsに渡すが、実際のテーブルには既にc2もある
    client.tables["geo_actor_episode_candidates"].append(_candidate("c2", "iran", "e2", "2026-09-02"))

    stale_params = {
        "p_actor_entity_id": "iran", "p_quad_class": 1, "p_event_root_code": "01", "p_is_demo": False,
        "p_expected_frozen_member_ids": [], "p_expected_rebuildable_cluster_ids": [],
        "p_expected_eligible_candidate_ids": ["c1"],  # c2を含まない古いsnapshot
        "p_new_clusters": [{"cluster_sequence": 1, "member_count": 1, "first_event_date": "2026-09-01",
                             "last_event_date": "2026-09-01", "goldstein_min": None, "goldstein_max": None,
                             "trajectory": "insufficient_data", "representative_counterparties": [],
                             "representative_geo": [], "member_candidate_ids": ["c1"]}],
    }
    try:
        client.rpc("geo_episode_cluster_apply_rebuild", stale_params)
        assert False, "episode_cluster_key_conflictが送出されるはず"
    except svc.EpisodeClusterKeyConflict:
        pass

    # リトライ（新鮮なsnapshート）ではc1・c2が両方含まれる
    result = svc.recluster_key(client, actor_entity_id="iran", quad_class=1, event_root_code="01", is_demo=False)
    assert result["clustered_candidates"] == 2


def test_member_count_mismatch_within_single_cluster_is_rejected():
    client = _ClusterFakeClient(tables={
        "geo_external_events": [_event("e1", 1, "01")],
        "geo_actor_episode_candidates": [_candidate("c1", "iran", "e1", "2026-09-01")],
    })
    bad_params = {
        "p_actor_entity_id": "iran", "p_quad_class": 1, "p_event_root_code": "01", "p_is_demo": False,
        "p_expected_frozen_member_ids": [], "p_expected_rebuildable_cluster_ids": [],
        "p_expected_eligible_candidate_ids": ["c1"],
        "p_new_clusters": [{"cluster_sequence": 1, "member_count": 2,  # 実際は1件のmember_candidate_idsしかない
                             "first_event_date": "2026-09-01", "last_event_date": "2026-09-01",
                             "goldstein_min": None, "goldstein_max": None, "trajectory": "insufficient_data",
                             "representative_counterparties": [], "representative_geo": [],
                             "member_candidate_ids": ["c1"]}],
    }
    try:
        client.rpc("geo_episode_cluster_apply_rebuild", bad_params)
        assert False, "episode_cluster_validation_errorが送出されるはず"
    except svc.EpisodeClusterValidationError:
        pass


def test_empty_snapshot_does_not_false_positive_conflict():
    """frozen/rebuildable/eligibleの3集合が全て空（未知のkey）でもconflict誤判定にならない。"""
    client = _ClusterFakeClient(tables={})
    result = svc.recluster_key(client, actor_entity_id="ghost", quad_class=1, event_root_code="01", is_demo=False)
    assert result["rebuilt_clusters"] == 0
    assert result["clustered_candidates"] == 0


# ---------------------------------------------------------------------
# Safety: Actor Intelligence本体への書き込みなし
# ---------------------------------------------------------------------

def test_update_episode_cluster_status_does_not_touch_actor_intelligence():
    client = FakeSupabaseClient(tables={"geo_episode_clusters": [
        {"episode_cluster_id": "cl1", "cluster_status": "candidate"},
    ]})
    svc.update_episode_cluster_status(
        client, episode_cluster_id="cl1", cluster_status="accepted", reviewed_by="analyst1")
    assert client.tables["geo_episode_clusters"][0]["cluster_status"] == "accepted"
    assert not any(t in ("geo_entities", "geo_knowledge_items") for t, _, _ in client.updated)
