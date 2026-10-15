"""地政学ナレッジ＆インテリジェンスDB Phase2A: Actor Episode Candidate生成。

【最重要原則】GDELT Eventは`Actor1 → action → Actor2`というCAMEO構造そのものがaction/
counterpartyの意味論を持つ。したがってActor Episode Candidateは必ずEvent（Actor1Name/
Actor2Name）を起点に生成し、GKG Personsに名前が登場しただけのActorへaction/counterpartyを
帰属させない（「記事にTrumpが登場した」≠「そのEventの実行主体がTrumpである」の誤帰属防止）。

Actor Intelligence本体（Core Profile/Decision Pattern/Current Posture/Issue Position）は
一切更新しない。本ファイルはgeo_entities/geo_knowledge_itemsへのwrite関数を一切importしない。
"""
import hashlib
from datetime import datetime, timezone

from geo_actor_intelligence_service import ACTOR_ENTITY_TYPES
from geo_entity_resolution import find_entity_matches
from geo_external_signal_service import _select_in_chunked

_ALLOWED_TRANSITIONS = {
    "candidate": {"reviewed", "accepted", "rejected"},
    "reviewed": {"accepted", "rejected"},
}


def _is_actor_eligible(entity_row: dict) -> bool:
    """既存`geo_actor_intelligence_service.ACTOR_ENTITY_TYPES`をそのまま再利用する。
    「Actorとして扱ってよいentity type か」の判定であり、「Actor Intelligence Profileが
    充足しているか(active actor_profileの有無)」とは無関係（両者は独立の概念）。"""
    return entity_row.get("entity_type") in ACTOR_ENTITY_TYPES


def _resolve_actor_role(client, event_row: dict, role: str):
    """roleは'actor1'または'actor2'。一致1件・actor-eligibleの場合のみ
    {"entity_id","match_method"}を返す。それ以外はNoneを返す（呼び出し元がskip系カウント）。"""
    name = event_row.get(f"{role}_name")
    if not name:
        return None, "no_match"
    matches = find_entity_matches(client, name)
    if len(matches) == 0:
        return None, "no_match"
    if len(matches) > 1:
        return None, "ambiguous"

    match = matches[0]
    entity_rows = client.select("geo_entities", {
        "select": "entity_id,entity_type", "entity_id": f"eq.{match['entity_id']}", "limit": "1",
    })
    if not entity_rows or not _is_actor_eligible(entity_rows[0]):
        return None, "ineligible_actor"

    return {"entity_id": match["entity_id"], "match_method": match["match_type"]}, None


def compute_fingerprint(actor_entity_id: str, provider_event_id: str, actor_role: str) -> str:
    basis = f"actor:{actor_entity_id}:{provider_event_id}:{actor_role}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _derive_theme_id_for_event(client, external_event_id: str):
    """既存N:M経路から導出するderived cache。distinctが1件の場合のみ値を返し、
    0件または複数件ならNoneを返す（authoritativeな分類ではない）。"""
    link_rows = client.select("geo_external_event_signal_links", {
        "select": "external_signal_id", "external_event_id": f"eq.{external_event_id}",
    })
    signal_ids = list({r["external_signal_id"] for r in link_rows})
    if not signal_ids:
        return None
    match_rows = _select_in_chunked(
        client, "geo_external_signal_query_matches", select="query_id",
        in_column="external_signal_id", in_values=signal_ids)
    query_ids = list({r["query_id"] for r in match_rows})
    if not query_ids:
        return None
    query_rows = _select_in_chunked(
        client, "geo_external_signal_queries", select="theme_id",
        in_column="query_id", in_values=query_ids)
    theme_ids = {r["theme_id"] for r in query_rows if r.get("theme_id")}
    if len(theme_ids) == 1:
        return next(iter(theme_ids))
    return None


def _build_observed_action(event_row: dict) -> str:
    """event_code/action_geo等から組み立てた定型記述（事実記述のみ、人間の解釈を含まない）。"""
    parts = []
    if event_row.get("event_base_code"):
        parts.append(f"CAMEO event_base_code={event_row['event_base_code']}")
    if event_row.get("action_geo_full_name"):
        parts.append(f"場所: {event_row['action_geo_full_name']}")
    if event_row.get("goldstein_scale") is not None:
        parts.append(f"Goldstein Scale: {event_row['goldstein_scale']}")
    if event_row.get("avg_tone") is not None:
        parts.append(f"Avg Tone: {event_row['avg_tone']}")
    return " / ".join(parts) if parts else None


def generate_candidates_for_batch(client, *, events: list, external_signal_ids_by_event_id: dict,
                                   ingestion_run_id: str, is_demo: bool = False) -> dict:
    """eventsは`external_event_id`キー（内部UUID）を含む解決済みEvent行のリスト。
    Actor判定・action/counterpartyの決定はすべてevent_row（Actor1Name/Actor2Name）のみに基づく。"""
    created = 0
    evidence_added = 0
    skipped_ambiguous = 0
    skipped_no_match = 0
    skipped_ineligible_actor = 0
    affected_actor_episode_candidate_ids = []

    role_pairs = (("actor1", "actor2"), ("actor2", "actor1"))
    now_iso = datetime.now(timezone.utc).isoformat()

    candidates_to_process = []
    for event_row in events:
        external_event_id = event_row.get("external_event_id")
        provider_event_id = event_row.get("provider_event_id")
        if not external_event_id or not provider_event_id:
            continue
        for role, counterparty_role in role_pairs:
            resolved, skip_reason = _resolve_actor_role(client, event_row, role)
            if resolved is None:
                if skip_reason == "ambiguous":
                    skipped_ambiguous += 1
                elif skip_reason == "ineligible_actor":
                    skipped_ineligible_actor += 1
                else:
                    skipped_no_match += 1
                continue

            counterparty_entity, _ = _resolve_actor_role(client, event_row, counterparty_role)
            fingerprint = compute_fingerprint(resolved["entity_id"], provider_event_id, role)
            candidates_to_process.append({
                "fingerprint": fingerprint,
                "actor_entity_id": resolved["entity_id"],
                "matched_name_text": event_row.get(f"{role}_name"),
                "actor_role": role,
                "match_method": resolved["match_method"],
                "external_event_id": external_event_id,
                "observed_action": _build_observed_action(event_row),
                "counterparty_text": event_row.get(f"{counterparty_role}_name"),
                "counterparty_entity_id": counterparty_entity["entity_id"] if counterparty_entity else None,
                "event_date": event_row.get("event_date"),
                "external_signal_ids": external_signal_ids_by_event_id.get(external_event_id, []),
            })

    if not candidates_to_process:
        return {"created": created, "evidence_added": evidence_added, "skipped_ambiguous": skipped_ambiguous,
                "skipped_no_match": skipped_no_match, "skipped_ineligible_actor": skipped_ineligible_actor,
                "affected_actor_episode_candidate_ids": affected_actor_episode_candidate_ids}

    fingerprints = [c["fingerprint"] for c in candidates_to_process]
    existing_rows = _select_in_chunked(
        client, "geo_actor_episode_candidates",
        select="actor_episode_candidate_id,fingerprint,external_signal_ids,supporting_gkg_record_ids,source_count,evidence_count",
        in_column="fingerprint", in_values=fingerprints)
    existing_by_fingerprint = {r["fingerprint"]: r for r in existing_rows}

    to_insert = []
    for c in candidates_to_process:
        existing = existing_by_fingerprint.get(c["fingerprint"])
        if existing:
            merged_signal_ids = list({*(existing.get("external_signal_ids") or []), *c["external_signal_ids"]})
            client.update("geo_actor_episode_candidates",
                          {"actor_episode_candidate_id": f"eq.{existing['actor_episode_candidate_id']}"},
                          {"external_signal_ids": merged_signal_ids,
                           "source_count": len(merged_signal_ids),
                           "evidence_count": (existing.get("evidence_count") or 0) + 1,
                           "last_observed_at": now_iso})
            evidence_added += 1
            affected_actor_episode_candidate_ids.append(existing["actor_episode_candidate_id"])
            continue

        theme_id = _derive_theme_id_for_event(client, c["external_event_id"])
        to_insert.append({
            "actor_entity_id": c["actor_entity_id"], "matched_name_text": c["matched_name_text"],
            "actor_role": c["actor_role"], "match_method": c["match_method"], "fingerprint": c["fingerprint"],
            "theme_id": theme_id, "external_event_id": c["external_event_id"],
            "observed_action": c["observed_action"], "counterparty_text": c["counterparty_text"],
            "counterparty_entity_id": c["counterparty_entity_id"], "event_date": c["event_date"],
            "external_signal_ids": c["external_signal_ids"], "supporting_gkg_record_ids": [],
            "source_count": len(c["external_signal_ids"]), "evidence_count": 1,
            "candidate_status": "candidate", "ingestion_run_id": ingestion_run_id, "is_demo": is_demo,
        })

    if to_insert:
        inserted_rows = client.insert("geo_actor_episode_candidates", to_insert)
        created = len(to_insert)
        affected_actor_episode_candidate_ids.extend(
            r["actor_episode_candidate_id"] for r in (inserted_rows or []))

    return {"created": created, "evidence_added": evidence_added, "skipped_ambiguous": skipped_ambiguous,
            "skipped_no_match": skipped_no_match, "skipped_ineligible_actor": skipped_ineligible_actor,
            "affected_actor_episode_candidate_ids": affected_actor_episode_candidate_ids}


def list_actor_episode_candidates(client, *, actor_entity_id: str = None, candidate_status: str = None,
                                   limit: int = 50, offset: int = 0) -> dict:
    params = {"select": "*", "order": "last_observed_at.desc", "limit": str(limit), "offset": str(offset)}
    if actor_entity_id:
        params["actor_entity_id"] = f"eq.{actor_entity_id}"
    if candidate_status:
        params["candidate_status"] = f"eq.{candidate_status}"
    return {"candidates": client.select("geo_actor_episode_candidates", params)}


def update_actor_episode_candidate_status(client, *, candidate_id: str, candidate_status: str,
                                           reviewed_by: str, review_notes: str = None) -> dict:
    """許容遷移: candidate→{reviewed,accepted,rejected}, reviewed→{accepted,rejected}。
    accepted/rejectedは終端。Accept時もActor Core Profile/Decision Pattern等への書き込みは
    一切行わない（単純なUPDATEのみ）。"""
    rows = client.select("geo_actor_episode_candidates", {
        "select": "candidate_status", "actor_episode_candidate_id": f"eq.{candidate_id}", "limit": "1",
    })
    if not rows:
        raise ValueError(f"actor_episode_candidate_idが見つかりません: {candidate_id}")
    current_status = rows[0]["candidate_status"]
    allowed = _ALLOWED_TRANSITIONS.get(current_status, set())
    if candidate_status not in allowed:
        raise ValueError(f"許容されない状態遷移です: {current_status} -> {candidate_status}")

    now_iso = datetime.now(timezone.utc).isoformat()
    client.update("geo_actor_episode_candidates", {"actor_episode_candidate_id": f"eq.{candidate_id}"}, {
        "candidate_status": candidate_status, "reviewed_by": reviewed_by,
        "review_notes": review_notes, "reviewed_at": now_iso,
    })
    result_rows = client.select("geo_actor_episode_candidates", {
        "select": "*", "actor_episode_candidate_id": f"eq.{candidate_id}", "limit": "1",
    })
    return result_rows[0] if result_rows else None
