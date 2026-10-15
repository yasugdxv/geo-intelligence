"""地政学ナレッジ＆インテリジェンスDB Phase2B-1: Event / Episode Clustering。

Actor Episode Candidate（Phase2A）をactor_entity_id×quad_class×event_root_code×is_demo×
time proximityでdeterministicにグルーピングし、Actorごとの局面（Episode Cluster）を作る。
embedding/LLMは使わずルールベースのみで構築する。

【最重要原則】cluster_status='candidate'のClusterのみ`recluster_key`による自動再構築
（削除→全件再構築）の対象。同一keyのCandidateを毎回全件取得してevent_date昇順→gapで
分割し直すため、投入順やlate-arriving Eventによらず常に同じCluster構造になる。
reviewed/accepted/rejectedになったClusterはmembershipがfreezeされ、以後の自動再構築
対象から除外される。

DELETE/INSERTは`geo_episode_cluster_apply_rebuild`RPC（sql/2026-09-08_geo_episode_clusters.sql）
1回にまとめて行う。Python側からgeo_episode_clusters/geo_episode_cluster_membersへ直接
client.delete/client.insertしない（write integrity boundaryをRPC側に一本化する）。

Actor Intelligence本体（geo_entities/geo_knowledge_items）へは一切書き込まない。
"""
from datetime import date as date_cls
from datetime import datetime, timezone
from statistics import mean

import requests

from geo_external_signal_service import _select_in_chunked, _select_all_paginated

CLUSTER_TIME_GAP_DAYS = 14
GOLDSTEIN_TRAJECTORY_THRESHOLD = 2.0
GOLDSTEIN_SWING_THRESHOLD = 1.0
MAX_RECLUSTER_RETRIES = 3

_ALLOWED_TRANSITIONS = {
    "candidate": {"reviewed", "accepted", "rejected"},
    "reviewed": {"accepted", "rejected"},
}

_FROZEN_STATUSES = ("reviewed", "accepted", "rejected")


class EpisodeClusterKeyConflict(Exception):
    """同一keyへの同時recluster、またはPython側SELECTからRPC呼び出しまでの間の
    Candidate追加/rejected化により、RPC内のsnapshot比較が不一致になった場合に送出する。
    recluster_keyが自動的にリトライする（データの不整合ではなく、単なる競合なので）。"""


class EpisodeClusterValidationError(Exception):
    """p_new_clustersのmember集合がeligible candidate snapshotと一致しない等、
    Python側のグルーピングロジック自体のバグを示す。リトライしない。"""


def _bool_filter(value: bool) -> str:
    return "eq.true" if value else "eq.false"


def _call_apply_rebuild_rpc(client, params: dict) -> dict:
    """geo_episode_cluster_apply_rebuild RPCを呼び、PostgRESTのHTTPError内の
    メッセージからepisode_cluster_key_conflict/episode_cluster_validation_errorを
    判別してPython例外へ変換する。FakeSupabaseClient等がこれらの例外を直接送出する
    テストでは、このtry/exceptをそのまま素通りする（re-raiseするだけ）。"""
    try:
        return client.rpc("geo_episode_cluster_apply_rebuild", params)
    except (EpisodeClusterKeyConflict, EpisodeClusterValidationError):
        raise
    except requests.exceptions.HTTPError as exc:
        message = ""
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                message = (response.json() or {}).get("message", "") or ""
            except ValueError:
                message = ""
        if message.startswith("episode_cluster_key_conflict"):
            raise EpisodeClusterKeyConflict(message) from exc
        if message.startswith("episode_cluster_validation_error"):
            raise EpisodeClusterValidationError(message) from exc
        raise


def _resolve_cluster_key(event: dict, is_demo: bool):
    """(quad_class, event_root_code, is_demo)を返す。quad_class/event_root_codeが
    欠損しているEventはNoneを返す（呼び出し元がskipped_missing_cameo_codeとしてカウント）。"""
    quad_class = event.get("quad_class")
    event_root_code = event.get("event_root_code")
    if quad_class is None or not event_root_code:
        return None
    return (quad_class, event_root_code, is_demo)


def _parse_date(value: str) -> date_cls:
    return date_cls.fromisoformat(value[:10])


def _compute_trajectory(eligible_members: list) -> str:
    """eligible_membersはevent_dateが非null保証済み。まずevent_dateごとにgoldstein_scale
    の日次平均を作り（Event件数の多寡に判定が引っ張られないようにする）、その日次series
    に対してのみescalating/de_escalating/stable/mixedの判定を行う。"""
    daily_goldstein: dict = {}
    for m in eligible_members:
        goldstein = m.get("goldstein_scale")
        if goldstein is not None:
            daily_goldstein.setdefault(m["event_date"], []).append(goldstein)

    if len(daily_goldstein) < 2:
        return "insufficient_data"

    dates_sorted = sorted(daily_goldstein.keys())
    daily_means = [mean(daily_goldstein[d]) for d in dates_sorted]

    n = len(daily_means)
    half = n // 2
    avg_first = mean(daily_means[:half])
    avg_last = mean(daily_means[n - half:])
    delta = avg_last - avg_first

    diffs = [daily_means[i + 1] - daily_means[i] for i in range(n - 1)]
    has_upswing = any(d >= GOLDSTEIN_SWING_THRESHOLD for d in diffs)
    has_downswing = any(d <= -GOLDSTEIN_SWING_THRESHOLD for d in diffs)

    if delta <= -GOLDSTEIN_TRAJECTORY_THRESHOLD:
        return "escalating"
    if delta >= GOLDSTEIN_TRAJECTORY_THRESHOLD:
        return "de_escalating"
    if has_upswing and has_downswing:
        return "mixed"
    return "stable"


def _assign_cluster_sequences(n_groups: int, frozen_sequences: set) -> list:
    """frozen Clusterが使用中のsequence番号をskipし、1から決定論的に割り当てる。
    「既存最大値+1」方式は同じ入力でも再実行のたびに増え続けるため採用しない。"""
    assigned = []
    used = set(frozen_sequences)
    next_seq = 1
    for _ in range(n_groups):
        while next_seq in used:
            next_seq += 1
        assigned.append(next_seq)
        used.add(next_seq)
        next_seq += 1
    return assigned


def _split_into_groups(eligible_sorted: list) -> list:
    """event_date昇順にソート済みのeligible Candidateを、隣接するevent_dateの差が
    CLUSTER_TIME_GAP_DAYSを超える箇所で分割する。"""
    groups = []
    current_group = []
    prev_date = None
    for m in eligible_sorted:
        d = _parse_date(m["event_date"])
        if current_group and (d - prev_date).days > CLUSTER_TIME_GAP_DAYS:
            groups.append(current_group)
            current_group = []
        current_group.append(m)
        prev_date = d
    if current_group:
        groups.append(current_group)
    return groups


def _recluster_key_once(client, *, actor_entity_id: str, quad_class: int,
                         event_root_code: str, is_demo: bool) -> dict:
    existing_clusters = client.select("geo_episode_clusters", {
        "select": "*", "actor_entity_id": f"eq.{actor_entity_id}", "quad_class": f"eq.{quad_class}",
        "event_root_code": f"eq.{event_root_code}", "is_demo": _bool_filter(is_demo),
    })
    frozen_clusters = [c for c in existing_clusters if c["cluster_status"] in _FROZEN_STATUSES]
    rebuildable_clusters = [c for c in existing_clusters if c["cluster_status"] == "candidate"]
    frozen_sequences = {c["cluster_sequence"] for c in frozen_clusters}

    frozen_cluster_ids = [c["episode_cluster_id"] for c in frozen_clusters]
    frozen_member_rows = _select_in_chunked(
        client, "geo_episode_cluster_members", select="actor_episode_candidate_id",
        in_column="episode_cluster_id", in_values=frozen_cluster_ids) if frozen_cluster_ids else []
    frozen_member_id_set = {r["actor_episode_candidate_id"] for r in frozen_member_rows}
    expected_frozen_member_ids = sorted(frozen_member_id_set)
    expected_rebuildable_cluster_ids = sorted(c["episode_cluster_id"] for c in rebuildable_clusters)

    candidate_rows = client.select("geo_actor_episode_candidates", {
        "select": "actor_episode_candidate_id,external_event_id,candidate_status,counterparty_text,event_date",
        "actor_entity_id": f"eq.{actor_entity_id}", "is_demo": _bool_filter(is_demo),
    })
    event_ids = list({c["external_event_id"] for c in candidate_rows if c.get("external_event_id")})
    events = _select_in_chunked(
        client, "geo_external_events",
        select="external_event_id,quad_class,event_root_code,goldstein_scale,action_geo_full_name,"
               "event_date_added,provider_event_id",
        in_column="external_event_id", in_values=event_ids) if event_ids else []
    events_by_id = {e["external_event_id"]: e for e in events}

    excluded_rejected = 0
    skipped_missing_event_date = 0
    eligible = []
    for c in candidate_rows:
        event = events_by_id.get(c.get("external_event_id"))
        if not event:
            continue
        if event.get("quad_class") != quad_class or event.get("event_root_code") != event_root_code:
            continue
        if c["candidate_status"] == "rejected":
            excluded_rejected += 1
            continue
        if c["actor_episode_candidate_id"] in frozen_member_id_set:
            continue
        if not c.get("event_date"):
            skipped_missing_event_date += 1
            continue
        eligible.append({
            "actor_episode_candidate_id": c["actor_episode_candidate_id"],
            "event_date": c["event_date"],
            "event_date_added": event.get("event_date_added") or "",
            "provider_event_id": event.get("provider_event_id") or "",
            "goldstein_scale": event.get("goldstein_scale"),
            "counterparty_text": c.get("counterparty_text"),
            "action_geo_full_name": event.get("action_geo_full_name"),
        })

    expected_eligible_candidate_ids = sorted(m["actor_episode_candidate_id"] for m in eligible)

    eligible_sorted = sorted(
        eligible, key=lambda m: (m["event_date"], m["event_date_added"], m["provider_event_id"]))
    groups = _split_into_groups(eligible_sorted)
    sequences = _assign_cluster_sequences(len(groups), frozen_sequences)

    new_clusters_payload = []
    for group, seq in zip(groups, sequences):
        dates = [m["event_date"] for m in group]
        goldsteins = [m["goldstein_scale"] for m in group if m["goldstein_scale"] is not None]
        counterparties = sorted({m["counterparty_text"] for m in group if m.get("counterparty_text")})
        geos = sorted({m["action_geo_full_name"] for m in group if m.get("action_geo_full_name")})
        new_clusters_payload.append({
            "cluster_sequence": seq,
            "member_count": len(group),
            "first_event_date": min(dates),
            "last_event_date": max(dates),
            "goldstein_min": min(goldsteins) if goldsteins else None,
            "goldstein_max": max(goldsteins) if goldsteins else None,
            "trajectory": _compute_trajectory(group),
            "representative_counterparties": counterparties,
            "representative_geo": geos,
            "member_candidate_ids": [m["actor_episode_candidate_id"] for m in group],
        })

    _call_apply_rebuild_rpc(client, {
        "p_actor_entity_id": actor_entity_id, "p_quad_class": quad_class,
        "p_event_root_code": event_root_code, "p_is_demo": is_demo,
        "p_expected_frozen_member_ids": expected_frozen_member_ids,
        "p_expected_rebuildable_cluster_ids": expected_rebuildable_cluster_ids,
        "p_expected_eligible_candidate_ids": expected_eligible_candidate_ids,
        "p_new_clusters": new_clusters_payload,
    })

    return {
        "frozen_clusters_preserved": len(frozen_clusters),
        "rebuilt_clusters": len(new_clusters_payload),
        "clustered_candidates": len(eligible),
        "excluded_rejected": excluded_rejected,
        "skipped_missing_event_date": skipped_missing_event_date,
    }


def recluster_key(client, *, actor_entity_id: str, quad_class: int,
                   event_root_code: str, is_demo: bool = False) -> dict:
    """指定keyについて、cluster_status='candidate'のClusterのみを対象に、そのkeyに属する
    『eligibleなCandidate全件』からClusterを毎回ゼロから再構築する。reviewed/accepted/
    rejectedのClusterとそのmembershipには一切触れない（freeze）。episode_cluster_key_conflict
    発生時はMAX_RECLUSTER_RETRIES回まで自動リトライする。"""
    last_error = None
    for attempt in range(MAX_RECLUSTER_RETRIES):
        try:
            return _recluster_key_once(
                client, actor_entity_id=actor_entity_id, quad_class=quad_class,
                event_root_code=event_root_code, is_demo=is_demo)
        except EpisodeClusterKeyConflict as exc:
            last_error = exc
            continue
    raise ValueError(
        f"recluster_key: episode_cluster_key_conflictが{MAX_RECLUSTER_RETRIES}回連続で発生しました "
        f"(actor_entity_id={actor_entity_id}, quad_class={quad_class}, "
        f"event_root_code={event_root_code}, is_demo={is_demo}): {last_error}")


def recluster_affected_keys(client, *, actor_episode_candidate_ids: list = None) -> dict:
    """与えられたCandidate群からdistinctなkey集合を求め、各keyについてrecluster_keyを
    1回ずつ呼ぶ。key discovery段階ではcandidate_statusによるrejected除外を行わない
    （既にrejected化済みのCandidateが渡されても、そのCandidateが属していたkeyは正しく
    特定されrecluster対象になる。rejectedの除外はrecluster_key内部でのみ行う）。
    actor_episode_candidate_ids=Noneの場合は全Actor Episode Candidateから求める
    （初回バックフィル用）。"""
    if actor_episode_candidate_ids is None:
        candidate_rows = _select_all_paginated(client, "geo_actor_episode_candidates", {
            "select": "actor_episode_candidate_id,actor_entity_id,external_event_id,is_demo",
        })
    else:
        candidate_rows = _select_in_chunked(
            client, "geo_actor_episode_candidates",
            select="actor_episode_candidate_id,actor_entity_id,external_event_id,is_demo",
            in_column="actor_episode_candidate_id", in_values=actor_episode_candidate_ids)

    event_ids = list({c["external_event_id"] for c in candidate_rows if c.get("external_event_id")})
    events = _select_in_chunked(
        client, "geo_external_events", select="external_event_id,quad_class,event_root_code",
        in_column="external_event_id", in_values=event_ids) if event_ids else []
    events_by_id = {e["external_event_id"]: e for e in events}

    keys = set()
    for c in candidate_rows:
        event = events_by_id.get(c.get("external_event_id"))
        if not event:
            continue
        key = _resolve_cluster_key(event, bool(c.get("is_demo")))
        if key is None:
            continue
        quad_class, event_root_code, is_demo = key
        keys.add((c["actor_entity_id"], quad_class, event_root_code, is_demo))

    results = []
    for actor_entity_id, quad_class, event_root_code, is_demo in sorted(keys):
        results.append(recluster_key(
            client, actor_entity_id=actor_entity_id, quad_class=quad_class,
            event_root_code=event_root_code, is_demo=is_demo))

    return {
        "keys_processed": len(results),
        "total_rebuilt_clusters": sum(r["rebuilt_clusters"] for r in results),
        "total_clustered_candidates": sum(r["clustered_candidates"] for r in results),
    }


def list_episode_clusters(client, *, actor_entity_id: str = None, cluster_status: str = None,
                           limit: int = 50, offset: int = 0) -> dict:
    params = {"select": "*", "order": "last_event_date.desc", "limit": str(limit), "offset": str(offset)}
    if actor_entity_id:
        params["actor_entity_id"] = f"eq.{actor_entity_id}"
    if cluster_status:
        params["cluster_status"] = f"eq.{cluster_status}"
    return {"clusters": client.select("geo_episode_clusters", params)}


def get_episode_cluster_members(client, episode_cluster_id: str) -> list:
    member_rows = client.select("geo_episode_cluster_members", {
        "select": "actor_episode_candidate_id", "episode_cluster_id": f"eq.{episode_cluster_id}",
    })
    candidate_ids = [r["actor_episode_candidate_id"] for r in member_rows]
    if not candidate_ids:
        return []
    return _select_in_chunked(
        client, "geo_actor_episode_candidates", select="*",
        in_column="actor_episode_candidate_id", in_values=candidate_ids)


def update_episode_cluster_status(client, *, episode_cluster_id: str, cluster_status: str,
                                   reviewed_by: str, review_notes: str = None) -> dict:
    """許容遷移: candidate→{reviewed,accepted,rejected}, reviewed→{accepted,rejected}。
    status更新のみでmembership（geo_episode_cluster_members）には一切触れない。
    Actor Intelligence本体（geo_entities/geo_knowledge_items）へは一切書き込まない。"""
    rows = client.select("geo_episode_clusters", {
        "select": "cluster_status", "episode_cluster_id": f"eq.{episode_cluster_id}", "limit": "1",
    })
    if not rows:
        raise ValueError(f"episode_cluster_idが見つかりません: {episode_cluster_id}")
    current_status = rows[0]["cluster_status"]
    allowed = _ALLOWED_TRANSITIONS.get(current_status, set())
    if cluster_status not in allowed:
        raise ValueError(f"許容されない状態遷移です: {current_status} -> {cluster_status}")

    now_iso = datetime.now(timezone.utc).isoformat()
    client.update("geo_episode_clusters", {"episode_cluster_id": f"eq.{episode_cluster_id}"}, {
        "cluster_status": cluster_status, "reviewed_by": reviewed_by,
        "review_notes": review_notes, "reviewed_at": now_iso,
    })
    result_rows = client.select("geo_episode_clusters", {
        "select": "*", "episode_cluster_id": f"eq.{episode_cluster_id}", "limit": "1",
    })
    return result_rows[0] if result_rows else None
