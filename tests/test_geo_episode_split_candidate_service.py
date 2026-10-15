"""地政学ナレッジ＆インテリジェンスDB Phase2B-2B: geo_episode_split_candidate_service.pyのテスト。

Evidence Edge Qualification（missing値の扱い）、evidence group→topic group統合、
artifact_group判定（件数閾値＋base_code/country異質性）、二層fingerprint（proposal_fingerprint
カスケード）、freeze、sibling一括更新、parent Cluster rejectedとの競合防止、is_demo authoritative
設定、raw_metadata analysis snapshotを検証する。
"""
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_episode_split_candidate_service as svc  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


class _SplitFakeClient(FakeSupabaseClient):
    """geo_episode_split_candidate_apply_replace / geo_episode_split_candidate_update_status
    RPCの実際のPL/pgSQL挙動（snapshot比較・parent Cluster FOR UPDATE・derived metadata更新・
    sibling一括UPDATE）をPythonで再現するFake。"""

    def rpc(self, fn_name, params=None):
        if fn_name == "geo_episode_split_candidate_apply_replace":
            return self._apply_replace(params or {})
        if fn_name == "geo_episode_split_candidate_update_status":
            return self._update_status(params or {})
        return super().rpc(fn_name, params)

    def _apply_replace(self, p):
        parent_cluster_id = p["p_parent_cluster_id"]
        clusters = self.tables.get("geo_episode_clusters", [])
        cluster = next((c for c in clusters if c["episode_cluster_id"] == parent_cluster_id), None)
        if cluster is None:
            raise svc.SplitCandidateValidationError(
                f"split_candidate_parent_cluster_not_found: {parent_cluster_id}")
        if cluster["cluster_status"] == "rejected":
            raise svc.SplitCandidateParentRejected(
                f"split_candidate_parent_rejected: cannot write split candidates for rejected parent "
                f"{parent_cluster_id}")

        rows = self.tables.get("geo_episode_split_candidates", [])
        current_frozen = sorted(
            r["fingerprint"] for r in rows
            if r["parent_cluster_id"] == parent_cluster_id and r["candidate_status"] in ("reviewed", "accepted", "rejected"))
        current_candidate = sorted(
            r["fingerprint"] for r in rows
            if r["parent_cluster_id"] == parent_cluster_id and r["candidate_status"] == "candidate")

        def canon(lst):
            return sorted(lst or [])

        if (canon(p.get("p_expected_frozen_fingerprints")) != current_frozen
                or canon(p.get("p_expected_candidate_fingerprints")) != current_candidate):
            raise svc.SplitCandidateConflict("split_candidate_conflict: snapshot changed since read, retry")

        new_rows = p.get("p_new_rows") or []
        new_fingerprints = {r["fingerprint"] for r in new_rows}

        other_parent_rows = [r for r in rows if r["parent_cluster_id"] != parent_cluster_id]
        kept_this_parent = [
            r for r in rows
            if r["parent_cluster_id"] == parent_cluster_id
            and (r["candidate_status"] in ("reviewed", "accepted", "rejected") or r["fingerprint"] in new_fingerprints)
        ]
        deleted = len([
            r for r in rows
            if r["parent_cluster_id"] == parent_cluster_id and r["candidate_status"] == "candidate"
            and r["fingerprint"] not in new_fingerprints
        ])
        self.tables["geo_episode_split_candidates"] = other_parent_rows + kept_this_parent

        current_rows_by_fp = {r["fingerprint"]: r for r in kept_this_parent}
        inserted = 0
        now_iso = datetime.now(timezone.utc).isoformat()
        for row in new_rows:
            fp = row["fingerprint"]
            if fp in current_frozen:
                continue
            if fp in current_candidate:
                existing_row = current_rows_by_fp[fp]
                existing_row.update({
                    "candidate_type": row["candidate_type"],
                    "dominant_event_base_code": row.get("dominant_event_base_code"),
                    "representative_geo": row.get("representative_geo") or [],
                    "representative_counterparties": row.get("representative_counterparties") or [],
                    "separation_evidence": row.get("separation_evidence") or [],
                    "cross_cluster_affinity": row.get("cross_cluster_affinity") or [],
                    "raw_metadata": row.get("raw_metadata"),
                })
                continue
            new_row = {
                "split_candidate_id": self._next_id("split_candidate_id"),
                "parent_cluster_id": parent_cluster_id,
                "candidate_type": row["candidate_type"],
                "sibling_group_key": row["sibling_group_key"],
                "fingerprint": fp,
                "member_candidate_ids": row.get("member_candidate_ids") or [],
                "dominant_event_base_code": row.get("dominant_event_base_code"),
                "representative_geo": row.get("representative_geo") or [],
                "representative_counterparties": row.get("representative_counterparties") or [],
                "separation_evidence": row.get("separation_evidence") or [],
                "cross_cluster_affinity": row.get("cross_cluster_affinity") or [],
                "candidate_status": "candidate",
                "reviewed_at": None, "reviewed_by": None, "review_notes": None,
                "raw_metadata": row.get("raw_metadata"),
                "is_demo": cluster["is_demo"],
                "created_at": now_iso, "updated_at": now_iso,
            }
            self.tables.setdefault("geo_episode_split_candidates", []).append(new_row)
            current_rows_by_fp[fp] = new_row
            inserted += 1

        return {"inserted": inserted, "deleted": deleted}

    def _update_status(self, p):
        split_candidate_id = p["p_split_candidate_id"]
        new_status = p["p_new_status"]
        rows = self.tables.get("geo_episode_split_candidates", [])
        row = next((r for r in rows if r["split_candidate_id"] == split_candidate_id), None)
        if row is None:
            raise ValueError(f"split_candidate_not_found: {split_candidate_id}")

        sibling_key = row["sibling_group_key"]
        siblings = [r for r in rows if r["sibling_group_key"] == sibling_key]
        statuses = {r["candidate_status"] for r in siblings}
        if len(statuses) != 1:
            raise ValueError(
                f"split_candidate_sibling_status_mismatch: sibling_group_key {sibling_key} "
                f"has statuses {sorted(statuses)}")
        current_status = next(iter(statuses))

        allowed = svc._ALLOWED_TRANSITIONS.get(current_status, set())
        if new_status not in allowed:
            raise ValueError(f"split_candidate_invalid_transition: {current_status} -> {new_status}")

        clusters = self.tables.get("geo_episode_clusters", [])
        cluster = next((c for c in clusters if c["episode_cluster_id"] == row["parent_cluster_id"]), None)
        if cluster is None:
            raise ValueError(f"split_candidate_parent_cluster_not_found: {row['parent_cluster_id']}")

        if new_status == "accepted":
            if cluster["cluster_status"] != "accepted":
                raise ValueError(
                    f"split_candidate_parent_not_accepted: parent {row['parent_cluster_id']} "
                    f"status={cluster['cluster_status']}")
        else:
            if cluster["cluster_status"] not in ("reviewed", "accepted", "rejected"):
                raise ValueError(
                    f"split_candidate_parent_not_frozen: parent {row['parent_cluster_id']} "
                    f"status={cluster['cluster_status']}")

        now_iso = datetime.now(timezone.utc).isoformat()
        updated = 0
        for r in siblings:
            r["candidate_status"] = new_status
            r["reviewed_by"] = p.get("p_reviewed_by")
            r["review_notes"] = p.get("p_review_notes")
            r["reviewed_at"] = now_iso
            updated += 1
        if updated == 0:
            raise ValueError(f"split_candidate_update_no_rows_affected: sibling_group_key {sibling_key}")
        return {"updated_rows": updated, "sibling_group_key": sibling_key}


def _cluster(cid, actor_entity_id, quad, root, is_demo=False, status="candidate", member_count=2):
    return {
        "episode_cluster_id": cid, "actor_entity_id": actor_entity_id, "quad_class": quad,
        "event_root_code": root, "is_demo": is_demo, "cluster_sequence": 1, "member_count": member_count,
        "first_event_date": "2026-09-01", "last_event_date": "2026-09-07",
        "goldstein_min": None, "goldstein_max": None, "trajectory": "insufficient_data",
        "representative_counterparties": [], "representative_geo": [],
        "cluster_status": status, "reviewed_at": None, "reviewed_by": None, "review_notes": None,
        "raw_metadata": None, "created_at": "2026-09-08T00:00:00+00:00", "updated_at": "2026-09-08T00:00:00+00:00",
    }


def _member(cluster_id, candidate_id):
    return {"cluster_member_id": f"m-{cluster_id}-{candidate_id}", "episode_cluster_id": cluster_id,
            "actor_episode_candidate_id": candidate_id}


def _candidate(cid, external_event_id, signal_ids=None, counterparty=None, is_demo=False):
    return {"actor_episode_candidate_id": cid, "external_event_id": external_event_id,
            "external_signal_ids": list(signal_ids or []), "counterparty_text": counterparty, "is_demo": is_demo}


def _event(event_id, base_code=None, geo=None, country=None, source_url=None,
           provider_event_id=None, is_demo=False):
    return {"external_event_id": event_id, "event_base_code": base_code, "action_geo_full_name": geo,
            "action_geo_country_code": country, "source_url": source_url,
            "provider_event_id": provider_event_id or f"gid-{event_id}", "is_demo": is_demo}


# ---------------------------------------------------------------------
# 純粋関数: _is_artifact_edge / _geo_key
# ---------------------------------------------------------------------

def test_geo_key_prefers_country_code_over_full_name_parse():
    assert svc._geo_key({"action_geo_country_code": "IR", "action_geo_full_name": "Tehran, Iran"}) == "IR"
    assert svc._geo_key({"action_geo_country_code": None, "action_geo_full_name": "Tehran, Tehran, Iran"}) == "Iran"
    assert svc._geo_key({"action_geo_country_code": None, "action_geo_full_name": None}) is None


def test_artifact_edge_requires_both_base_code_and_geo_conflict():
    a = {"event_base_code": "080", "action_geo_country_code": "IR"}
    b = {"event_base_code": "087", "action_geo_country_code": "US"}
    assert svc._is_artifact_edge(a, b) is True  # 両方乖離
    c = {"event_base_code": "080", "action_geo_country_code": "US"}
    assert svc._is_artifact_edge(a, c) is False  # base_codeのみ乖離
    d = {"event_base_code": "087", "action_geo_country_code": "IR"}
    assert svc._is_artifact_edge(a, d) is False  # geoのみ乖離
    e = {"event_base_code": "080", "action_geo_country_code": "IR"}
    assert svc._is_artifact_edge(a, e) is False  # 両方一致


def test_artifact_edge_treats_missing_values_as_unknown_not_conflict():
    a = {"event_base_code": "080", "action_geo_country_code": "IR"}
    missing_base = {"event_base_code": None, "action_geo_country_code": "US"}
    missing_geo = {"event_base_code": "087", "action_geo_country_code": None}
    missing_both = {"event_base_code": None, "action_geo_country_code": None}
    # missing値がある限り「不一致(conflict)」とは扱わない＝artifact_edgeにならない
    assert svc._is_artifact_edge(a, missing_base) is False
    assert svc._is_artifact_edge(a, missing_geo) is False
    assert svc._is_artifact_edge(a, missing_both) is False


# ---------------------------------------------------------------------
# 純粋関数: artifact_group判定
# ---------------------------------------------------------------------

def test_topic_group_not_classified_artifact_when_source_url_below_event_threshold():
    stats = {"u1": {"event_count": 2, "base_codes": {"080", "087"}, "countries": {"IR", "US"}}}
    events = [{"source_url": "u1"}, {"source_url": "u1"}]
    is_artifact, _ = svc._topic_group_is_artifact_group(events, stats)
    assert is_artifact is False


def test_topic_group_not_classified_artifact_when_source_url_lacks_base_code_or_country_heterogeneity():
    # 件数閾値は満たすが、base_codeが単一（複数媒体が単に同じ話題を報じただけの正当なケース）
    stats_same_base = {"u1": {"event_count": 5, "base_codes": {"080"}, "countries": {"IR", "US", "RU"}}}
    events = [{"source_url": "u1"}, {"source_url": "u1"}, {"source_url": "u1"}]
    is_artifact, _ = svc._topic_group_is_artifact_group(events, stats_same_base)
    assert is_artifact is False

    # countryが単一の場合も同様に不採用
    stats_same_country = {"u1": {"event_count": 5, "base_codes": {"080", "087"}, "countries": {"IR"}}}
    is_artifact2, _ = svc._topic_group_is_artifact_group(events, stats_same_country)
    assert is_artifact2 is False


def test_topic_group_classified_artifact_when_all_thresholds_met():
    stats = {"u1": {"event_count": 4, "base_codes": {"080", "087"}, "countries": {"IR", "US", "UA", "RU"}}}
    events = [{"source_url": "u1"}, {"source_url": "u1"}, {"source_url": "u1"}]
    is_artifact, evidence = svc._topic_group_is_artifact_group(events, stats)
    assert is_artifact is True
    assert evidence["source_url"] == "u1"
    assert evidence["event_count"] == 4
    assert evidence["base_codes"] == ["080", "087"]


def test_topic_group_not_artifact_when_members_have_different_source_urls():
    stats = {"u1": {"event_count": 5, "base_codes": {"080", "087"}, "countries": {"IR", "US"}}}
    events = [{"source_url": "u1"}, {"source_url": "u2"}]
    is_artifact, _ = svc._topic_group_is_artifact_group(events, stats)
    assert is_artifact is False


def test_topic_group_not_artifact_when_a_member_has_no_source_url():
    stats = {"u1": {"event_count": 5, "base_codes": {"080", "087"}, "countries": {"IR", "US"}}}
    events = [{"source_url": "u1"}, {"source_url": None}]
    is_artifact, _ = svc._topic_group_is_artifact_group(events, stats)
    assert is_artifact is False


# ---------------------------------------------------------------------
# 実データ形状の再現（root08 / root01 / root05 / coherent cluster）
# ---------------------------------------------------------------------

def _root08_shape_tables():
    """IRAN root08相当: 4件Hormuz(split_group) + 3件AP roundup(artifact_group) + 1件antiwar(outlier)。
    c1(15ad7dbe相当)のEvent自身のsource_urlがAP roundup記事であることが、system-wideの
    artifact_source_stats["ap_url"]にbase_code/country異質性を与える鍵になる。"""
    cluster_id = "root08"
    return {
        "geo_episode_clusters": [_cluster(cluster_id, "iran", 2, "08", member_count=8)],
        "geo_episode_cluster_members": [
            _member(cluster_id, f"c{i}") for i in range(1, 9)],
        "geo_actor_episode_candidates": [
            _candidate("c1", "e1", signal_ids=["hormuz_a", "ap_roundup_signal"]),
            _candidate("c2", "e2", signal_ids=["hormuz_a"]),
            _candidate("c3", "e3", signal_ids=["hormuz_b"]),
            _candidate("c4", "e4", signal_ids=["hormuz_b"]),
            _candidate("c5", "e5", signal_ids=["ap_roundup_signal"]),
            _candidate("c6", "e6", signal_ids=["ap_roundup_signal"]),
            _candidate("c7", "e7", signal_ids=["ap_roundup_signal"]),
            _candidate("c8", "e8", signal_ids=[]),
        ],
        "geo_external_events": [
            _event("e1", base_code="080", geo="Strait of Hormuz, Iran", country="IR", source_url="ap_url"),
            _event("e2", base_code="080", geo="Strait of Hormuz, Iran", country="IR", source_url="hormuz_url_1"),
            _event("e3", base_code="080", geo="Gulf of Oman, Oman", country="OM", source_url="hormuz_url_2"),
            _event("e4", base_code="080", geo="Gulf of Oman, Oman", country="OM", source_url="hormuz_url_2b"),
            _event("e5", base_code="087", geo="North Carolina, United States", country="US", source_url="ap_url"),
            _event("e6", base_code="087", geo="Kyiv, Ukraine", country="UA", source_url="ap_url"),
            _event("e7", base_code="087", geo="Moscow, Russia", country="RU", source_url="ap_url"),
            _event("e8", base_code="083", geo="Tehran, Iran", country="IR", source_url="antiwar_url"),
        ],
    }


def test_root08_shape_produces_split_candidate_with_split_and_artifact_group():
    client = _SplitFakeClient(tables=_root08_shape_tables())
    result = svc.generate_split_candidates(client, is_demo=False)
    assert result["clusters_processed"] == 1
    assert result["split_group_rows"] == 1
    assert result["artifact_group_rows"] == 1
    assert result["outlier_rows"] == 1

    rows = client.tables["geo_episode_split_candidates"]
    split_group = next(r for r in rows if r["candidate_type"] == "split_group")
    artifact_group = next(r for r in rows if r["candidate_type"] == "artifact_group")
    outlier = next(r for r in rows if r["candidate_type"] == "outlier")

    assert sorted(split_group["member_candidate_ids"]) == ["c1", "c2", "c3", "c4"]
    assert sorted(artifact_group["member_candidate_ids"]) == ["c5", "c6", "c7"]
    assert outlier["member_candidate_ids"] == ["c8"]

    # split_group/artifact_groupは同一Proposal（同じsibling_group_key）に属す
    assert split_group["sibling_group_key"] == artifact_group["sibling_group_key"]
    assert outlier["sibling_group_key"] != split_group["sibling_group_key"]

    evidence_signals = {e["signal"] for e in artifact_group["separation_evidence"]}
    assert "shared_source_url_wire_roundup" in evidence_signals
    artifact_evidence = next(
        e for e in artifact_group["separation_evidence"] if e["signal"] == "shared_source_url_wire_roundup")
    assert artifact_evidence["distinct_global_event_id_count"] == 4
    assert set(artifact_evidence["distinct_event_base_codes"]) == {"080", "087"}
    assert set(artifact_evidence["distinct_countries"]) == {"IR", "US", "UA", "RU"}


def test_other_clusters_unaffected_by_root08_artifact_edge_qualification():
    """root08専用のfixtureに、内部が完全に一体化しているcoherent clusterを追加しても、
    そちらにはsplit/outlier行が一切生成されないことを確認する（他Clusterへの影響ゼロ）。"""
    tables = _root08_shape_tables()
    tables["geo_episode_clusters"].append(_cluster("root04", "iran", 1, "04", member_count=3))
    tables["geo_episode_cluster_members"].extend([
        _member("root04", "d1"), _member("root04", "d2"), _member("root04", "d3")])
    tables["geo_actor_episode_candidates"].extend([
        _candidate("d1", "f1", signal_ids=["shared_x"]),
        _candidate("d2", "f2", signal_ids=["shared_x", "shared_y"]),
        _candidate("d3", "f3", signal_ids=["shared_y"]),
    ])
    tables["geo_external_events"].extend([
        _event("f1", base_code="010", geo="Tehran, Iran", country="IR"),
        _event("f2", base_code="010", geo="Tehran, Iran", country="IR"),
        _event("f3", base_code="010", geo="Tehran, Iran", country="IR"),
    ])
    client = _SplitFakeClient(tables=tables)
    svc.generate_split_candidates(client, is_demo=False)
    rows = client.tables["geo_episode_split_candidates"]
    assert not any(r["parent_cluster_id"] == "root04" for r in rows)
    assert any(r["parent_cluster_id"] == "root08" for r in rows)


def test_root01_shape_produces_no_split_only_outlier():
    """IRAN root01相当: 同一base_code(010)の複数evidence groupが統合されて1つのtopic group
    （meaningful component 1つ）になり、他とevidenceを共有しないmember(base=012)がoutlierになる。"""
    cluster_id = "root01"
    tables = {
        "geo_episode_clusters": [_cluster(cluster_id, "iran", 1, "01", member_count=5)],
        "geo_episode_cluster_members": [_member(cluster_id, f"c{i}") for i in range(1, 6)],
        "geo_actor_episode_candidates": [
            _candidate("c1", "e1", signal_ids=["a1"]),
            _candidate("c2", "e2", signal_ids=["a1"]),
            _candidate("c3", "e3", signal_ids=["a2"]),
            _candidate("c4", "e4", signal_ids=["a2"]),
            _candidate("c5", "e5", signal_ids=[]),  # bb46e56f相当（outlier）
        ],
        "geo_external_events": [
            _event("e1", base_code="010", geo="Tehran, Iran", country="IR"),
            _event("e2", base_code="010", geo="Tehran, Iran", country="IR"),
            _event("e3", base_code="010", geo="Tehran, Iran", country="IR"),
            _event("e4", base_code="010", geo="Tehran, Iran", country="IR"),
            _event("e5", base_code="012", geo="Tehran, Iran", country="IR"),
        ],
    }
    client = _SplitFakeClient(tables=tables)
    result = svc.generate_split_candidates(client, is_demo=False)
    assert result["split_group_rows"] == 0
    assert result["artifact_group_rows"] == 0
    assert result["outlier_rows"] == 1
    rows = client.tables["geo_episode_split_candidates"]
    outlier = rows[0]
    assert outlier["candidate_type"] == "outlier"
    assert outlier["member_candidate_ids"] == ["c5"]


def test_root05_shape_produces_no_split_only_outlier():
    """IRAN root05相当: base=050が2件で1 topic group(meaningful component 1つ)、base=051の
    1件が孤立してoutlierになる。050/051の手動互換リストが無くても正しく『splitしない』側に
    倒れることを確認する。"""
    cluster_id = "root05"
    tables = {
        "geo_episode_clusters": [_cluster(cluster_id, "iran", 1, "05", member_count=3)],
        "geo_episode_cluster_members": [_member(cluster_id, f"c{i}") for i in range(1, 4)],
        "geo_actor_episode_candidates": [
            _candidate("c1", "e1", signal_ids=["a1"]),
            _candidate("c2", "e2", signal_ids=["a1"]),
            _candidate("c3", "e3", signal_ids=[]),  # 7028813b相当（outlier）
        ],
        "geo_external_events": [
            _event("e1", base_code="050", geo="Muscat, Oman", country="OM"),
            _event("e2", base_code="050", geo="Muscat, Oman", country="OM"),
            _event("e3", base_code="051", geo="Muscat, Oman", country="OM"),
        ],
    }
    client = _SplitFakeClient(tables=tables)
    result = svc.generate_split_candidates(client, is_demo=False)
    assert result["split_group_rows"] == 0
    assert result["outlier_rows"] == 1


def test_coherent_cluster_produces_no_candidates():
    cluster_id = "root_coherent"
    tables = {
        "geo_episode_clusters": [_cluster(cluster_id, "iran", 1, "04", member_count=3)],
        "geo_episode_cluster_members": [_member(cluster_id, f"c{i}") for i in range(1, 4)],
        "geo_actor_episode_candidates": [
            _candidate("c1", "e1", signal_ids=["shared"]),
            _candidate("c2", "e2", signal_ids=["shared"]),
            _candidate("c3", "e3", signal_ids=["shared"]),
        ],
        "geo_external_events": [
            _event("e1", base_code="010", geo="Tehran, Iran", country="IR"),
            _event("e2", base_code="010", geo="Tehran, Iran", country="IR"),
            _event("e3", base_code="010", geo="Tehran, Iran", country="IR"),
        ],
    }
    client = _SplitFakeClient(tables=tables)
    result = svc.generate_split_candidates(client, is_demo=False)
    assert result["split_group_rows"] == 0
    assert result["artifact_group_rows"] == 0
    assert result["outlier_rows"] == 0
    assert client.tables.get("geo_episode_split_candidates", []) == []


def test_ambiguous_base_code_group_not_merged_with_others():
    """1つのevidence group内（geo一致で救済されたtrusted edge経由）に複数base_codeが混在
    した場合、dominant_event_base_codeがNoneになり、他のevidence groupと統合されず単体の
    topic groupとして扱われることを確認する。"""
    cluster_id = "root_ambiguous"
    tables = {
        "geo_episode_clusters": [_cluster(cluster_id, "iran", 1, "09", member_count=4)],
        "geo_episode_cluster_members": [_member(cluster_id, f"c{i}") for i in range(1, 5)],
        "geo_actor_episode_candidates": [
            # c1,c2はbase_codeが異なるがgeoが一致するのでtrusted edge（artifactにならない）
            _candidate("c1", "e1", signal_ids=["shared"]),
            _candidate("c2", "e2", signal_ids=["shared"]),
            # c3,c4は同一base_code=010の別evidence group（もしambiguous扱いされなければ
            # base_code=010という共通点で誤って統合されうる）
            _candidate("c3", "e3", signal_ids=["other"]),
            _candidate("c4", "e4", signal_ids=["other"]),
        ],
        "geo_external_events": [
            _event("e1", base_code="010", geo="Tehran, Iran", country="IR"),
            _event("e2", base_code="020", geo="Tehran, Iran", country="IR"),
            _event("e3", base_code="010", geo="Tehran, Iran", country="IR"),
            _event("e4", base_code="010", geo="Tehran, Iran", country="IR"),
        ],
    }
    client = _SplitFakeClient(tables=tables)
    result = svc.generate_split_candidates(client, is_demo=False)
    # {c1,c2}はambiguous(base 010/020混在)で単体のtopic group、{c3,c4}はbase=010の別topic
    # group。ambiguousな方は"010"を共有していても統合対象にならないため、meaningful component
    # は2つ（{c1,c2}と{c3,c4}）→Split Proposalが生成される。
    assert result["split_group_rows"] == 2
    rows = [r for r in client.tables["geo_episode_split_candidates"] if r["candidate_type"] == "split_group"]
    ambiguous_row = next(r for r in rows if sorted(r["member_candidate_ids"]) == ["c1", "c2"])
    assert ambiguous_row["dominant_event_base_code"] is None
    signals = {e["signal"] for e in ambiguous_row["separation_evidence"]}
    assert "ambiguous_base_code_family" in signals
    other_row = next(r for r in rows if sorted(r["member_candidate_ids"]) == ["c3", "c4"])
    assert other_row["dominant_event_base_code"] == "010"


# ---------------------------------------------------------------------
# fingerprint idempotency / proposal fingerprintのcascade / freeze
# ---------------------------------------------------------------------

def test_generate_split_candidates_is_idempotent_by_fingerprint():
    client = _SplitFakeClient(tables=_root08_shape_tables())
    svc.generate_split_candidates(client, is_demo=False)
    ids_before = {r["split_candidate_id"]: r["fingerprint"] for r in client.tables["geo_episode_split_candidates"]}

    svc.generate_split_candidates(client, is_demo=False)
    ids_after = {r["split_candidate_id"]: r["fingerprint"] for r in client.tables["geo_episode_split_candidates"]}
    assert ids_before == ids_after


def test_generate_split_candidates_preserves_frozen_rows():
    client = _SplitFakeClient(tables=_root08_shape_tables())
    svc.generate_split_candidates(client, is_demo=False)
    outlier = next(r for r in client.tables["geo_episode_split_candidates"] if r["candidate_type"] == "outlier")
    for r in client.tables["geo_episode_clusters"]:
        r["cluster_status"] = "accepted"
    svc.update_split_candidate_status(
        client, split_candidate_id=outlier["split_candidate_id"], candidate_status="reviewed",
        reviewed_by="analyst1")

    svc.generate_split_candidates(client, is_demo=False)
    outlier_after = next(r for r in client.tables["geo_episode_split_candidates"] if r["candidate_type"] == "outlier")
    assert outlier_after["split_candidate_id"] == outlier["split_candidate_id"]
    assert outlier_after["candidate_status"] == "reviewed"


def test_proposal_fingerprint_cascades_when_group_composition_changes():
    tables = _root08_shape_tables()
    client = _SplitFakeClient(tables=tables)
    svc.generate_split_candidates(client, is_demo=False)
    split_before = next(r for r in client.tables["geo_episode_split_candidates"] if r["candidate_type"] == "split_group")
    artifact_before = next(r for r in client.tables["geo_episode_split_candidates"] if r["candidate_type"] == "artifact_group")

    # split_group側のmember構成を変える（c4を、signal共有もbase_codeも変えて完全に切り離す）
    # ことでproposal_fingerprint自体を変化させる。base_codeだけ変えず信号だけ切り離すと、
    # base_code一致による再統合で結局同じtopic group構成に戻ってしまうため、両方変える。
    for c in client.tables["geo_actor_episode_candidates"]:
        if c["actor_episode_candidate_id"] == "c4":
            c["external_signal_ids"] = []
    for e in client.tables["geo_external_events"]:
        if e["external_event_id"] == "e4":
            e["event_base_code"] = "999"

    svc.generate_split_candidates(client, is_demo=False)
    split_after = next(
        (r for r in client.tables["geo_episode_split_candidates"] if r["candidate_type"] == "split_group"), None)
    artifact_after = next(
        (r for r in client.tables["geo_episode_split_candidates"] if r["candidate_type"] == "artifact_group"), None)
    assert split_after is not None and artifact_after is not None
    # split_group側のmember構成が変わったので、兄弟であるartifact_group側もidentityが
    # 切り替わる（proposal_fingerprintに連動したsibling_group_key/fingerprintの再生成）
    assert split_after["split_candidate_id"] != split_before["split_candidate_id"]
    assert artifact_after["split_candidate_id"] != artifact_before["split_candidate_id"]
    assert split_after["sibling_group_key"] == artifact_after["sibling_group_key"]


def test_candidate_row_derived_metadata_refreshed_on_regen_when_fingerprint_unchanged():
    client = _SplitFakeClient(tables=_root08_shape_tables())
    svc.generate_split_candidates(client, is_demo=False)
    outlier = next(r for r in client.tables["geo_episode_split_candidates"] if r["candidate_type"] == "outlier")
    split_id_before = outlier["split_candidate_id"]

    # cross_cluster_affinityが変化する状況を作る（他Clusterが同じsignalを持つようになる）
    client.tables["geo_episode_clusters"].append(_cluster("root08_other", "iran", 1, "10", member_count=1))
    client.tables["geo_episode_cluster_members"].append(_member("root08_other", "z1"))
    client.tables["geo_actor_episode_candidates"].append(
        _candidate("z1", "ez1", signal_ids=[]))
    client.tables["geo_external_events"].append(
        _event("ez1", base_code="099", geo="Nowhere", country="XX"))
    # antiwar(c8)のsignal_idsは空のままなのでaffinityは変化しないが、fingerprintが不変な
    # candidate行に対してもraw_metadataが再計算・UPDATEされることを確認する
    svc.generate_split_candidates(client, is_demo=False)
    outlier_after = next(r for r in client.tables["geo_episode_split_candidates"] if r["candidate_type"] == "outlier")
    assert outlier_after["split_candidate_id"] == split_id_before
    assert outlier_after["raw_metadata"]["algorithm_version"] == svc.ALGORITHM_VERSION


# ---------------------------------------------------------------------
# raw_metadata analysis snapshot
# ---------------------------------------------------------------------

def test_raw_metadata_contains_algorithm_version_and_artifact_source_evaluation():
    client = _SplitFakeClient(tables=_root08_shape_tables())
    svc.generate_split_candidates(client, is_demo=False)
    rows = client.tables["geo_episode_split_candidates"]
    for r in rows:
        assert r["raw_metadata"]["algorithm_version"] == svc.ALGORITHM_VERSION
        assert isinstance(r["raw_metadata"]["parent_cluster_member_composition"], list)
    artifact_group = next(r for r in rows if r["candidate_type"] == "artifact_group")
    assert artifact_group["raw_metadata"]["artifact_source_evaluation"]["qualifies_as_artifact"] is True
    split_group = next(r for r in rows if r["candidate_type"] == "split_group")
    assert split_group["raw_metadata"]["artifact_source_evaluation"] is None


# ---------------------------------------------------------------------
# cross-cluster affinity（outlier）
# ---------------------------------------------------------------------

def test_outlier_cross_cluster_affinity_matches_existing_situation_link():
    tables = {
        "geo_episode_clusters": [
            _cluster("root01", "iran", 1, "01", member_count=2),
            _cluster("root13", "iran", 3, "13", member_count=1),
        ],
        "geo_episode_cluster_members": [
            _member("root01", "c1"), _member("root01", "c2"), _member("root13", "c3")],
        "geo_actor_episode_candidates": [
            _candidate("c1", "e1", signal_ids=["a1"]),
            _candidate("c2", "e2", signal_ids=["shared_with_root13"]),
            _candidate("c3", "e3", signal_ids=["shared_with_root13"]),
        ],
        "geo_external_events": [
            _event("e1", base_code="010", geo="Tehran, Iran", country="IR"),
            _event("e2", base_code="012", geo="Tehran, Iran", country="IR"),
            _event("e3", base_code="012", geo="Tehran, Iran", country="IR"),
        ],
        "geo_episode_cluster_links": [{
            "cluster_link_id": "link1", "source_cluster_id": "root01", "target_cluster_id": "root13",
            "is_demo": False, "link_type": "related_context", "link_score": 3, "link_reasons": [],
            "link_status": "candidate", "reviewed_at": None, "reviewed_by": None, "review_notes": None,
            "raw_metadata": None, "created_at": "x", "updated_at": "x",
        }],
    }
    client = _SplitFakeClient(tables=tables)
    svc.generate_split_candidates(client, is_demo=False)
    # root01のc1(base=010)・c2(base=012)はどちらも他とevidenceを共有しないためどちらも
    # outlierになる。ここではroot13とsignalを共有するc2側のcross_cluster_affinityを検証する。
    outlier = next(
        r for r in client.tables["geo_episode_split_candidates"]
        if r["candidate_type"] == "outlier" and r["member_candidate_ids"] == ["c2"])
    affinity = outlier["cross_cluster_affinity"]
    assert len(affinity) == 1
    assert affinity[0]["target_cluster_id"] == "root13"
    assert affinity[0]["existing_situation_link_id"] == "link1"
    assert affinity[0]["existing_link_type"] == "related_context"


# ---------------------------------------------------------------------
# is_demo authoritative（apply_replace RPC）
# ---------------------------------------------------------------------

def test_apply_replace_sets_is_demo_from_parent_cluster_not_from_payload():
    client = _SplitFakeClient(tables={
        "geo_episode_clusters": [_cluster("c1", "iran", 1, "01", is_demo=True, member_count=2)],
        "geo_episode_split_candidates": [],
    })
    params = {
        "p_parent_cluster_id": "c1", "p_expected_frozen_fingerprints": [], "p_expected_candidate_fingerprints": [],
        "p_new_rows": [{
            "fingerprint": "fp1", "candidate_type": "outlier", "sibling_group_key": "sg1",
            "member_candidate_ids": ["m1"], "dominant_event_base_code": None,
            "representative_geo": [], "representative_counterparties": [], "separation_evidence": [],
            "cross_cluster_affinity": [], "raw_metadata": None,
        }],
    }
    client.rpc("geo_episode_split_candidate_apply_replace", params)
    row = client.tables["geo_episode_split_candidates"][0]
    assert row["is_demo"] is True


def test_apply_replace_raises_when_parent_cluster_missing():
    client = _SplitFakeClient(tables={"geo_episode_clusters": [], "geo_episode_split_candidates": []})
    params = {
        "p_parent_cluster_id": "does-not-exist", "p_expected_frozen_fingerprints": [],
        "p_expected_candidate_fingerprints": [], "p_new_rows": [],
    }
    try:
        client.rpc("geo_episode_split_candidate_apply_replace", params)
        assert False, "split_candidate_validation_errorが送出されるはず"
    except svc.SplitCandidateValidationError:
        pass


def test_apply_replace_rejects_write_when_parent_cluster_already_rejected():
    client = _SplitFakeClient(tables={
        "geo_episode_clusters": [_cluster("c1", "iran", 1, "01", status="rejected", member_count=2)],
        "geo_episode_split_candidates": [],
    })
    params = {
        "p_parent_cluster_id": "c1", "p_expected_frozen_fingerprints": [], "p_expected_candidate_fingerprints": [],
        "p_new_rows": [],
    }
    try:
        client.rpc("geo_episode_split_candidate_apply_replace", params)
        assert False, "split_candidate_parent_rejectedが送出されるはず"
    except svc.SplitCandidateParentRejected:
        pass
    assert client.tables.get("geo_episode_split_candidates", []) == []


def test_apply_replace_detects_fingerprint_snapshot_conflict():
    client = _SplitFakeClient(tables={
        "geo_episode_clusters": [_cluster("c1", "iran", 1, "01", member_count=2)],
        "geo_episode_split_candidates": [],
    })
    params = {
        "p_parent_cluster_id": "c1", "p_expected_frozen_fingerprints": [],
        "p_expected_candidate_fingerprints": ["stale-fp"],  # 古いsnapshot、実際には空
        "p_new_rows": [],
    }
    try:
        client.rpc("geo_episode_split_candidate_apply_replace", params)
        assert False, "split_candidate_conflictが送出されるはず"
    except svc.SplitCandidateConflict:
        pass


def test_generate_split_candidates_skips_cluster_when_parent_rejected_concurrently():
    client = FakeSupabaseClient(tables={
        "geo_episode_clusters": [_cluster("c1", "iran", 1, "01", member_count=2)],
        "geo_episode_cluster_members": [_member("c1", "cand1"), _member("c1", "cand2")],
        "geo_actor_episode_candidates": [
            _candidate("cand1", "e1", signal_ids=["s1"]), _candidate("cand2", "e2", signal_ids=["s1"])],
        "geo_external_events": [_event("e1", base_code="010"), _event("e2", base_code="010")],
    })
    client.rpc = MagicMock(side_effect=svc.SplitCandidateParentRejected("split_candidate_parent_rejected: c1"))
    result = svc.generate_split_candidates(client, is_demo=False)
    assert result["clusters_processed"] == 0
    assert result["clusters_skipped_parent_rejected"] == 1


def test_generate_split_candidates_retries_on_conflict_then_succeeds():
    client = FakeSupabaseClient(tables={
        "geo_episode_clusters": [_cluster("c1", "iran", 1, "01", member_count=2)],
        "geo_episode_cluster_members": [_member("c1", "cand1"), _member("c1", "cand2")],
        "geo_actor_episode_candidates": [
            _candidate("cand1", "e1", signal_ids=["s1"]), _candidate("cand2", "e2", signal_ids=["s1"])],
        "geo_external_events": [_event("e1", base_code="010"), _event("e2", base_code="010")],
    })
    client.rpc = MagicMock(side_effect=[
        svc.SplitCandidateConflict("split_candidate_conflict: retry"),
        {"inserted": 0, "deleted": 0},
    ])
    result = svc.generate_split_candidates(client, is_demo=False)
    assert client.rpc.call_count == 2
    assert result["clusters_processed"] == 1


# ---------------------------------------------------------------------
# Human Review状態機械 / freezeライフサイクル / sibling一括更新
# ---------------------------------------------------------------------

def test_split_group_siblings_share_status_after_single_patch():
    client = _SplitFakeClient(tables=_root08_shape_tables())
    svc.generate_split_candidates(client, is_demo=False)
    split_group = next(r for r in client.tables["geo_episode_split_candidates"] if r["candidate_type"] == "split_group")
    artifact_group = next(r for r in client.tables["geo_episode_split_candidates"] if r["candidate_type"] == "artifact_group")

    for c in client.tables["geo_episode_clusters"]:
        c["cluster_status"] = "accepted"
    svc.update_split_candidate_status(
        client, split_candidate_id=split_group["split_candidate_id"], candidate_status="accepted",
        reviewed_by="analyst1")

    rows = client.tables["geo_episode_split_candidates"]
    split_after = next(r for r in rows if r["split_candidate_id"] == split_group["split_candidate_id"])
    artifact_after = next(r for r in rows if r["split_candidate_id"] == artifact_group["split_candidate_id"])
    assert split_after["candidate_status"] == "accepted"
    assert artifact_after["candidate_status"] == "accepted"


def test_outlier_status_update_does_not_affect_other_rows():
    client = _SplitFakeClient(tables=_root08_shape_tables())
    svc.generate_split_candidates(client, is_demo=False)
    outlier = next(r for r in client.tables["geo_episode_split_candidates"] if r["candidate_type"] == "outlier")
    split_group = next(r for r in client.tables["geo_episode_split_candidates"] if r["candidate_type"] == "split_group")

    for c in client.tables["geo_episode_clusters"]:
        c["cluster_status"] = "accepted"
    svc.update_split_candidate_status(
        client, split_candidate_id=outlier["split_candidate_id"], candidate_status="accepted",
        reviewed_by="analyst1")

    rows = client.tables["geo_episode_split_candidates"]
    split_after = next(r for r in rows if r["split_candidate_id"] == split_group["split_candidate_id"])
    assert split_after["candidate_status"] == "candidate"


def test_transition_blocked_when_parent_cluster_is_still_candidate():
    client = _SplitFakeClient(tables=_root08_shape_tables())
    svc.generate_split_candidates(client, is_demo=False)
    outlier = next(r for r in client.tables["geo_episode_split_candidates"] if r["candidate_type"] == "outlier")
    try:
        svc.update_split_candidate_status(
            client, split_candidate_id=outlier["split_candidate_id"], candidate_status="reviewed",
            reviewed_by="analyst1")
        assert False, "ValueErrorが送出されるはず"
    except ValueError:
        pass


def test_transition_to_accepted_requires_parent_cluster_accepted():
    client = _SplitFakeClient(tables=_root08_shape_tables())
    svc.generate_split_candidates(client, is_demo=False)
    outlier = next(r for r in client.tables["geo_episode_split_candidates"] if r["candidate_type"] == "outlier")

    for c in client.tables["geo_episode_clusters"]:
        c["cluster_status"] = "reviewed"
    try:
        svc.update_split_candidate_status(
            client, split_candidate_id=outlier["split_candidate_id"], candidate_status="accepted",
            reviewed_by="analyst1")
        assert False, "ValueError（parentがreviewedのみではaccepted不可）が送出されるはず"
    except ValueError:
        pass

    for c in client.tables["geo_episode_clusters"]:
        c["cluster_status"] = "accepted"
    result = svc.update_split_candidate_status(
        client, split_candidate_id=outlier["split_candidate_id"], candidate_status="accepted",
        reviewed_by="analyst1")
    assert result["candidate_status"] == "accepted"


def test_update_status_rpc_detects_stale_split_candidate_id():
    client = _SplitFakeClient(tables=_root08_shape_tables())
    svc.generate_split_candidates(client, is_demo=False)
    try:
        svc.update_split_candidate_status(
            client, split_candidate_id="does-not-exist", candidate_status="reviewed", reviewed_by="analyst1")
        assert False, "ValueErrorが送出されるはず"
    except ValueError as exc:
        assert "split_candidate_not_found" in str(exc)


def test_update_status_rpc_detects_sibling_status_mismatch():
    client = _SplitFakeClient(tables=_root08_shape_tables())
    svc.generate_split_candidates(client, is_demo=False)
    rows = client.tables["geo_episode_split_candidates"]
    split_group = next(r for r in rows if r["candidate_type"] == "split_group")
    artifact_group = next(r for r in rows if r["candidate_type"] == "artifact_group")
    # 異常系: 兄弟行のstatusを直接食い違わせる
    artifact_group["candidate_status"] = "rejected"
    try:
        svc.update_split_candidate_status(
            client, split_candidate_id=split_group["split_candidate_id"], candidate_status="reviewed",
            reviewed_by="analyst1")
        assert False, "ValueErrorが送出されるはず"
    except ValueError as exc:
        assert "split_candidate_sibling_status_mismatch" in str(exc)


def test_purge_candidate_rows_when_parent_cluster_rejected():
    client = _SplitFakeClient(tables=_root08_shape_tables())
    svc.generate_split_candidates(client, is_demo=False)
    outlier = next(r for r in client.tables["geo_episode_split_candidates"] if r["candidate_type"] == "outlier")
    for c in client.tables["geo_episode_clusters"]:
        c["cluster_status"] = "accepted"
    svc.update_split_candidate_status(
        client, split_candidate_id=outlier["split_candidate_id"], candidate_status="accepted", reviewed_by="a1")

    purged = svc.purge_candidate_rows_for_rejected_parent(client, parent_cluster_id="root08")
    rows = client.tables["geo_episode_split_candidates"]
    # accepted状態のoutlierは残る。candidate状態だったsplit_group/artifact_groupは削除される
    assert purged == 2
    assert len(rows) == 1
    assert rows[0]["candidate_status"] == "accepted"


def test_update_split_candidate_status_does_not_touch_clusters_or_actor_intelligence():
    client = _SplitFakeClient(tables=_root08_shape_tables())
    svc.generate_split_candidates(client, is_demo=False)
    outlier = next(r for r in client.tables["geo_episode_split_candidates"] if r["candidate_type"] == "outlier")
    for c in client.tables["geo_episode_clusters"]:
        c["cluster_status"] = "accepted"
    svc.update_split_candidate_status(
        client, split_candidate_id=outlier["split_candidate_id"], candidate_status="accepted", reviewed_by="a1")
    assert not any(
        t in ("geo_episode_clusters", "geo_episode_cluster_members", "geo_entities", "geo_knowledge_items")
        for t, _, _ in client.updated)


# ---------------------------------------------------------------------
# スキーマ構造テスト
# ---------------------------------------------------------------------

def test_geo_episode_split_candidates_schema_has_candidate_type_check():
    sql_path = Path(__file__).parent.parent / "sql" / "2026-09-08_geo_episode_split_candidates.sql"
    sql_text = sql_path.read_text(encoding="utf-8")
    assert "check (candidate_type in ('split_group', 'artifact_group', 'outlier'))" in sql_text


def test_apply_replace_and_update_status_use_same_lock_key_expression():
    """geo_episode_split_candidate_apply_replaceとgeo_episode_split_candidate_update_statusが
    同じadvisory lockのnamespace/キーを使うことをSQL本文で確認する（6回目レビュー修正②）。"""
    sql_path = Path(__file__).parent.parent / "sql" / "2026-09-08_geo_episode_split_candidates.sql"
    sql_text = sql_path.read_text(encoding="utf-8")
    lock_expr = "hashtextextended('geo_episode_split_candidates:' || "
    assert sql_text.count(lock_expr) == 2


def test_apply_replace_locks_parent_cluster_row_for_update_and_checks_rejected():
    sql_path = Path(__file__).parent.parent / "sql" / "2026-09-08_geo_episode_split_candidates.sql"
    sql_text = sql_path.read_text(encoding="utf-8")
    apply_replace_start = sql_text.index("create or replace function public.geo_episode_split_candidate_apply_replace")
    apply_replace_end = sql_text.index("create or replace function public.geo_episode_split_candidate_update_status")
    body = sql_text[apply_replace_start:apply_replace_end]
    assert "from geo_episode_clusters" in body and "for update" in body
    assert "cluster_status = 'rejected'" in body


def test_update_status_locks_parent_cluster_row_for_update():
    sql_path = Path(__file__).parent.parent / "sql" / "2026-09-08_geo_episode_split_candidates.sql"
    sql_text = sql_path.read_text(encoding="utf-8")
    update_status_start = sql_text.index("create or replace function public.geo_episode_split_candidate_update_status")
    body = sql_text[update_status_start:]
    assert "from geo_episode_clusters" in body and "for update" in body
