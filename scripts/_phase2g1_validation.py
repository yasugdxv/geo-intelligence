# Phase 2G-1 実DB検証（使い捨てスクリプト）。実DBには現時点でaccepted状態のEpisode
# Cluster/Situation Linkが存在しない（全て人間レビュー待ち）ため、既存パイプラインを一切
# 変更せず、is_demo=Trueで明確にテストと分かるaccepted状態のダミーデータを作成し、
# sync_theme_candidates()を実行して検証する。完了後にすべて削除する。
import json
import sys
import uuid

sys.path.insert(0, ".")

from config_utils import load_config
from geo_db_client import SupabaseClient
from ai_client import make_openai_client

import geo_theme_candidate_service as candidate_service

config = load_config()
client = SupabaseClient(config)
azure_client, model = make_openai_client(config)

TOKEN = "PHASE2G1VALIDATION"

created = {"entities": [], "signals": [], "events": [], "candidates": [], "clusters": [],
           "members": [], "links": [], "theme_candidates": []}
output = {}


def mk_entity(name):
    row = client.insert("geo_entities", [{
        "entity_type": "government", "canonical_name": name, "is_demo": True,
    }])[0]
    created["entities"].append(row["entity_id"])
    return row["entity_id"]


def mk_signal(suffix):
    row = client.insert("geo_external_signals", [{
        "provider": "gdelt", "title": f"{TOKEN} {suffix}", "dedup_key": f"{TOKEN}-{suffix}-{uuid.uuid4()}",
        "is_demo": True,
    }])[0]
    created["signals"].append(row["external_signal_id"])
    return row["external_signal_id"]


def mk_event(suffix, actor1_name):
    row = client.insert("geo_external_events", [{
        "provider": "gdelt", "provider_event_id": f"{TOKEN}-{suffix}-{uuid.uuid4()}",
        "event_code": "042", "event_base_code": "042", "event_root_code": "04",
        "quad_class": 1, "goldstein_scale": -5.0, "actor1_name": actor1_name,
        "event_date": "2026-09-01", "batch_timestamp": "20260901000000", "is_demo": True,
    }])[0]
    created["events"].append(row["external_event_id"])
    return row["external_event_id"]


def mk_candidate(actor_entity_id, event_id, signal_id, suffix):
    fingerprint = f"{TOKEN}-{suffix}-{uuid.uuid4()}"
    row = client.insert("geo_actor_episode_candidates", [{
        "actor_entity_id": actor_entity_id, "matched_name_text": f"{TOKEN} {suffix}",
        "actor_role": "actor1", "match_method": "exact_canonical", "fingerprint": fingerprint,
        "external_event_id": event_id, "observed_action": f"{TOKEN} {suffix} took a restrictive trade action",
        "counterparty_text": "COUNTERPARTY", "event_date": "2026-09-01",
        "external_signal_ids": [signal_id], "is_demo": True,
    }])[0]
    created["candidates"].append(row["actor_episode_candidate_id"])
    return row["actor_episode_candidate_id"]


def mk_cluster(actor_entity_id, *, member_count, trajectory, event_root_code):
    row = client.insert("geo_episode_clusters", [{
        "actor_entity_id": actor_entity_id, "quad_class": 1, "event_root_code": event_root_code,
        "is_demo": True, "member_count": member_count, "first_event_date": "2026-09-01",
        "last_event_date": "2026-09-01", "trajectory": trajectory, "cluster_status": "accepted",
    }])[0]
    created["clusters"].append(row["episode_cluster_id"])
    return row["episode_cluster_id"]


def mk_member(cluster_id, candidate_id):
    row = client.insert("geo_episode_cluster_members", [{
        "episode_cluster_id": cluster_id, "actor_episode_candidate_id": candidate_id,
    }])[0]
    created["members"].append(row["cluster_member_id"])


def mk_link(cluster_id_a, cluster_id_b):
    source, target = sorted([cluster_id_a, cluster_id_b])
    row = client.insert("geo_episode_cluster_links", [{
        "source_cluster_id": source, "target_cluster_id": target, "is_demo": True,
        "link_type": "same_situation_candidate", "link_score": 9.0,
        "link_reasons": [{"signal": "test_fixture", "value": ["phase2g1"]}],
        "link_status": "accepted",
    }])[0]
    created["links"].append(row["cluster_link_id"])


try:
    # ---- Case: 単一Clusterでも強いsignal（member_count>=3 & escalating）→ 1 Candidate ----
    actor_a = mk_entity(f"{TOKEN} Ministry")
    event_a = mk_event("A", f"{TOKEN} Ministry")
    signal_a = mk_signal("A")
    cand_a = mk_candidate(actor_a, event_a, signal_a, "A")
    cluster_a = mk_cluster(actor_a, member_count=3, trajectory="escalating", event_root_code="04")
    mk_member(cluster_a, cand_a)

    # ---- Case: 弱い単独Cluster x2をSituation Linkでconnected component化 → 1 Candidate ----
    actor_b = mk_entity(f"{TOKEN} Agency")
    actor_c = mk_entity(f"{TOKEN} Council")
    event_b = mk_event("B", f"{TOKEN} Agency")
    event_c = mk_event("C", f"{TOKEN} Council")
    signal_b = mk_signal("B")
    signal_c = mk_signal("C")
    cand_b = mk_candidate(actor_b, event_b, signal_b, "B")
    cand_c = mk_candidate(actor_c, event_c, signal_c, "C")
    cluster_b = mk_cluster(actor_b, member_count=1, trajectory="stable", event_root_code="05")
    cluster_c = mk_cluster(actor_c, member_count=1, trajectory="stable", event_root_code="06")
    mk_member(cluster_b, cand_b)
    mk_member(cluster_c, cand_c)
    mk_link(cluster_b, cluster_c)

    components = candidate_service.find_accepted_situation_components(client, is_demo=True)
    output["components_found"] = components

    result1 = candidate_service.sync_theme_candidates(client, azure_client, model, is_demo=True)
    output["sync_run_1"] = result1
    created["theme_candidates"].extend(result1["created"])

    all_candidates = candidate_service.list_candidates(client, is_demo=True)
    output["candidates_after_run_1"] = all_candidates

    # 2回目実行 → merge（重複作成されないこと）を確認
    result2 = candidate_service.sync_theme_candidates(client, azure_client, model, is_demo=True)
    output["sync_run_2"] = result2
    created["theme_candidates"].extend(result2["created"])  # 万一新規作成された場合もcleanup対象にする

    # Evidence Traceの確認（複製ではなく既存テーブルへの参照であること）
    if result1["created"]:
        output["evidence_sample"] = candidate_service.get_candidate_evidence(client, result1["created"][0])

    with open("C:/Users/269811/AppData/Local/Temp/claude_2g1_validation_results.json", "w",
              encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print("VALIDATION_DONE")

finally:
    for cid in created["theme_candidates"]:
        client.delete("geo_theme_candidates", {"candidate_id": f"eq.{cid}"})
    for lid in created["links"]:
        client.delete("geo_episode_cluster_links", {"cluster_link_id": f"eq.{lid}"})
    for mid in created["members"]:
        client.delete("geo_episode_cluster_members", {"cluster_member_id": f"eq.{mid}"})
    for clid in created["clusters"]:
        client.delete("geo_episode_clusters", {"episode_cluster_id": f"eq.{clid}"})
    for cid in created["candidates"]:
        client.delete("geo_actor_episode_candidates", {"actor_episode_candidate_id": f"eq.{cid}"})
    for eid in created["events"]:
        client.delete("geo_external_events", {"external_event_id": f"eq.{eid}"})
    for sid in created["signals"]:
        client.delete("geo_external_signals", {"external_signal_id": f"eq.{sid}"})
    for enid in created["entities"]:
        client.delete("geo_entities", {"entity_id": f"eq.{enid}"})
    print("CLEANUP_DONE", {k: len(v) for k, v in created.items()})
