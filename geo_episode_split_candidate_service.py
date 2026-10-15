"""地政学ナレッジ＆インテリジェンスDB Phase2B-2B: Episode Split / Outlier Candidate。

既存Episode Cluster（Phase2B-1）のmembership・アルゴリズムは一切変更せず、Cluster内部に
「複数の局面が混在している疑い」を検出し、Human Review向けの提案層として提示する。

【確定した本番アルゴリズム】
1. Evidence Edge Qualification: shared signalが繋ぐ2 Candidateのevent_base_codeとgeo(country)
   が両方乖離する場合、そのsignal edgeをinternal connectivityから除外したconnected components
   （＝evidence group）を求める。
2. 複数のevidence groupが同一のevent_base_codeで統一されている場合、1つのtopic groupに統合する
   （手動geo/base_code互換リストは使わない、機械的に導出可能な規則のみ）。
3. member_count>=2のtopic groupをmeaningful componentとし、1つのClusterにmeaningful component
   が2つ以上あれば、その全体を1つのSplit Proposalとして提示する（sibling_group_key単位）。
4. 各meaningful componentは、内部連結がsystem-wideに閾値以上のGlobalEventID・base_code・country
   の異質性を持つ単一source_url（wire-roundup記事の機械的分割、Bridging Member Diagnosticsで
   実証済みのPattern B）にのみ由来する場合はcandidate_type=artifact_group（Cleanup対象）、
   そうでなければcandidate_type=split_groupとする。
5. member_count==1のtopic group（孤立member）はcandidate_type=outlierとして1件ずつ提示する。

DELETE/INSERT/UPDATEは`geo_episode_split_candidate_apply_replace`RPC
（sql/2026-09-08_geo_episode_split_candidates.sql）1回にまとめて行う。Human Review状態更新は
`geo_episode_split_candidate_update_status`RPC1回にまとめて行う。Python側からgeo_episode_split_
candidatesへ直接client.insert/update/deleteしない。

既存Cluster membershipへの実際の分割実行（Phase2B-2C相当）は行わない。acceptedになっても
Actor Intelligence本体（geo_entities/geo_knowledge_items）へは一切書き込まない。
"""
import hashlib
import uuid as uuid_lib

import requests

import geo_situation_link_service as link_svc
from geo_external_signal_service import _select_in_chunked, _select_all_paginated

ALGORITHM_VERSION = "v1"  # fingerprintに含める。アルゴリズム変更時はここを上げる。

ARTIFACT_SOURCE_EVENT_THRESHOLD = 3
ARTIFACT_SOURCE_MIN_BASE_CODES = 2
ARTIFACT_SOURCE_MIN_COUNTRIES = 2

MAX_REPLACE_RETRIES = 3

_ALLOWED_TRANSITIONS = {
    "candidate": {"reviewed", "accepted", "rejected"},
    "reviewed": {"accepted", "rejected"},
}
_FROZEN_STATUSES = ("reviewed", "accepted", "rejected")

_UPDATE_STATUS_KNOWN_ERROR_PREFIXES = (
    "split_candidate_not_found",
    "split_candidate_sibling_status_mismatch",
    "split_candidate_invalid_transition",
    "split_candidate_parent_not_accepted",
    "split_candidate_parent_not_frozen",
    "split_candidate_parent_cluster_not_found",
    "split_candidate_update_no_rows_affected",
)


class SplitCandidateConflict(Exception):
    """同一parent_cluster_idへの同時regenerate、またはPython側SELECTからRPC呼び出しまでの間の
    行変化により、RPC内のsnapshot比較が不一致になった場合に送出する。
    generate_split_candidatesが自動的にリトライする。"""


class SplitCandidateParentRejected(Exception):
    """RPC呼び出し時点でparent Clusterが既にrejected状態だった場合に送出する。リトライせず、
    そのClusterの処理をスキップする正常系の一種として扱う（並行してparentがrejectされた＝
    そのClusterはもう対象外になったということ）。"""


class SplitCandidateValidationError(Exception):
    """parent_cluster_idが存在しない等、呼び出し元のバグを示す。リトライしない。"""


def _bool_filter(value: bool) -> str:
    return "eq.true" if value else "eq.false"


def _country_token(geo: str) -> str:
    if not geo:
        return None
    token = geo.split(",")[-1].strip()
    return token or None


def _geo_key(event: dict) -> str:
    return event.get("action_geo_country_code") or _country_token(event.get("action_geo_full_name"))


def _is_artifact_edge(event_a: dict, event_b: dict) -> bool:
    """missing値を不一致と扱わない。「証拠が無い(unknown)」と「証拠が矛盾する(conflict)」を
    明確に区別する。片方でも値が欠けていればそのedgeはartifact扱いにしない。"""
    base_a, base_b = event_a.get("event_base_code"), event_b.get("event_base_code")
    geo_a, geo_b = _geo_key(event_a), _geo_key(event_b)
    base_conflict = base_a is not None and base_b is not None and base_a != base_b
    geo_conflict = geo_a is not None and geo_b is not None and geo_a != geo_b
    return base_conflict and geo_conflict


def _compute_evidence_groups(members: list, signal_ids_by_candidate_id: dict,
                              event_by_candidate_id: dict) -> list:
    """external_signal_ids共有かつnot _is_artifact_edge(...)をedgeとするunion-find。"""
    parent = {m: m for m in members}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    signal_to_members = {}
    for cid in members:
        for sid in signal_ids_by_candidate_id.get(cid, ()):
            signal_to_members.setdefault(sid, []).append(cid)

    for cids in signal_to_members.values():
        for i in range(len(cids)):
            for j in range(i + 1, len(cids)):
                a, b = cids[i], cids[j]
                if not _is_artifact_edge(event_by_candidate_id.get(a, {}), event_by_candidate_id.get(b, {})):
                    union(a, b)

    groups = {}
    for cid in members:
        groups.setdefault(find(cid), []).append(cid)
    return list(groups.values())


def _topic_group_base_code(group_ids: list, event_by_candidate_id: dict):
    """evidence group内のevent_base_codeが単一値ならその値を返す。0件ならunknown（None,
    is_ambiguous=False）、2件以上ならambiguous（None, is_ambiguous=True）。"""
    codes = sorted({event_by_candidate_id[cid].get("event_base_code")
                     for cid in group_ids if event_by_candidate_id[cid].get("event_base_code")})
    if len(codes) == 1:
        return codes[0], False, codes
    return None, len(codes) >= 2, codes


def _merge_into_topic_groups(evidence_groups: list, event_by_candidate_id: dict) -> list:
    """base_codeがNoneでなく同一のevidence groupどうしのみ統合する。base_code(ambiguous/unknown
    問わずNone)のevidence groupは単体のtopic groupとして扱い、他とは統合しない。"""
    topic_groups = []
    by_base_code = {}
    for group in evidence_groups:
        base_code, is_ambiguous, distinct_codes = _topic_group_base_code(group, event_by_candidate_id)
        if base_code is None:
            topic_groups.append({
                "base_code": None, "member_candidate_ids": sorted(group),
                "is_ambiguous": is_ambiguous, "distinct_base_codes": distinct_codes,
            })
        else:
            by_base_code.setdefault(base_code, []).extend(group)
    for base_code, ids in by_base_code.items():
        topic_groups.append({
            "base_code": base_code, "member_candidate_ids": sorted(ids),
            "is_ambiguous": False, "distinct_base_codes": [base_code],
        })
    return topic_groups


def _compute_artifact_source_stats(client, *, is_demo: bool) -> dict:
    """is_demoスコープ全体で、Eventのsource_urlごとに{event_count, base_codes, countries}を
    集計する。geo_situation_link_service._compute_hub_valuesと同じ「system-wide集計→閾値で
    線引き」という設計思想の移植（Cluster内ではなくGlobalEventID×source_url単位で計算する
    点のみ異なる）。"""
    events = _select_all_paginated(client, "geo_external_events", {
        "select": "provider_event_id,event_base_code,action_geo_full_name,action_geo_country_code,source_url",
        "is_demo": _bool_filter(is_demo),
    })
    raw = {}
    for e in events:
        url = e.get("source_url")
        if not url:
            continue
        entry = raw.setdefault(url, {"event_ids": set(), "base_codes": set(), "countries": set()})
        if e.get("provider_event_id"):
            entry["event_ids"].add(e["provider_event_id"])
        if e.get("event_base_code"):
            entry["base_codes"].add(e["event_base_code"])
        geo = _geo_key(e)
        if geo:
            entry["countries"].add(geo)
    return {url: {"event_count": len(v["event_ids"]), "base_codes": v["base_codes"], "countries": v["countries"]}
            for url, v in raw.items()}


def _topic_group_is_artifact_group(group_events: list, artifact_source_stats: dict):
    """全memberのEventが同一のsource_urlを共有し、かつそのsource_urlがGlobalEventID件数・
    base_code種類数・country種類数の3条件全てで閾値以上（=wire-roundup記事の機械的分割の
    特徴）を満たす場合のみartifact_groupとみなす。件数閾値だけでは正当な多重ソース報道
    （同一話題を複数媒体が報じただけ）まで誤検知するため、base_code/countryの異質性も必須とする。"""
    urls = [ev.get("source_url") for ev in group_events]
    if any(u is None for u in urls):
        return False, None
    distinct_urls = set(urls)
    if len(distinct_urls) != 1:
        return False, None
    url = next(iter(distinct_urls))
    stats = artifact_source_stats.get(url)
    if not stats:
        return False, None
    if (stats["event_count"] >= ARTIFACT_SOURCE_EVENT_THRESHOLD
            and len(stats["base_codes"]) >= ARTIFACT_SOURCE_MIN_BASE_CODES
            and len(stats["countries"]) >= ARTIFACT_SOURCE_MIN_COUNTRIES):
        return True, {
            "source_url": url, "event_count": stats["event_count"],
            "base_codes": sorted(stats["base_codes"]), "countries": sorted(stats["countries"]),
            "qualifies_as_artifact": True,
            "min_events": ARTIFACT_SOURCE_EVENT_THRESHOLD,
            "min_base_codes": ARTIFACT_SOURCE_MIN_BASE_CODES,
            "min_countries": ARTIFACT_SOURCE_MIN_COUNTRIES,
        }
    return False, None


def _build_separation_evidence(group: dict, artifact_stats: dict) -> list:
    evidence = []
    if group["is_ambiguous"]:
        evidence.append({"signal": "ambiguous_base_code_family", "value": group["distinct_base_codes"]})
    elif group["base_code"]:
        evidence.append({"signal": "base_code_family", "value": group["base_code"]})
    if artifact_stats:
        evidence.append({
            "signal": "shared_source_url_wire_roundup", "value": artifact_stats["source_url"],
            "distinct_global_event_id_count": artifact_stats["event_count"],
            "distinct_event_base_codes": artifact_stats["base_codes"],
            "distinct_countries": artifact_stats["countries"],
        })
    return evidence


def _compute_cross_cluster_affinity(client, candidate_id: str, parent_cluster_id: str,
                                     hub_signals: set, *, is_demo: bool) -> list:
    """該当memberのexternal_signal_ids（hub除外後）を持つ他のClusterを探し、既存
    geo_episode_cluster_linksにparent_cluster_idとの間のLinkが既にあればその情報を添えて返す。
    hub_signalsはgeo_situation_link_service._compute_hub_valuesの結果をそのまま渡す
    （重複実装しない）。"""
    cand_rows = client.select("geo_actor_episode_candidates", {
        "select": "external_signal_ids", "actor_episode_candidate_id": f"eq.{candidate_id}", "limit": "1",
    })
    if not cand_rows:
        return []
    own_signals = set(cand_rows[0].get("external_signal_ids") or []) - hub_signals
    if not own_signals:
        return []

    other_clusters = [
        c for c in client.select("geo_episode_clusters", {
            "select": "*", "is_demo": _bool_filter(is_demo), "cluster_status": "neq.rejected",
        }) if c["episode_cluster_id"] != parent_cluster_id
    ]
    if not other_clusters:
        return []
    cluster_signals = link_svc._collect_cluster_signals(client, other_clusters)

    affinity = []
    for c in other_clusters:
        cid = c["episode_cluster_id"]
        shared = own_signals & cluster_signals[cid]["signal_ids"]
        if not shared:
            continue
        source, target = sorted((parent_cluster_id, cid))
        existing = client.select("geo_episode_cluster_links", {
            "select": "*", "source_cluster_id": f"eq.{source}", "target_cluster_id": f"eq.{target}", "limit": "1",
        })
        entry = {"target_cluster_id": cid, "shared_signal_ids": sorted(shared)}
        if existing:
            entry["existing_situation_link_id"] = existing[0]["cluster_link_id"]
            entry["existing_link_type"] = existing[0]["link_type"]
        affinity.append(entry)
    return affinity


def _compute_proposal_fingerprint(parent_cluster_id: str, meaningful_groups: list) -> str:
    group_strs = sorted(",".join(sorted(g)) for g in meaningful_groups)
    digest_input = f"{ALGORITHM_VERSION}:{parent_cluster_id}:" + "|".join(group_strs)
    return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()


def _derive_uuid_from_hash(digest: str) -> str:
    return str(uuid_lib.UUID(hex=digest[:32]))


def _compute_group_fingerprint(proposal_fingerprint: str, group_member_ids: list) -> str:
    digest_input = f"{proposal_fingerprint}:{','.join(sorted(group_member_ids))}"
    return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()


def _compute_outlier_fingerprint(parent_cluster_id: str, member_candidate_id: str) -> str:
    digest_input = f"{ALGORITHM_VERSION}:{parent_cluster_id}:outlier:{member_candidate_id}"
    return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()


def _build_split_candidates_for_cluster(client, cluster: dict, hub_signals: set,
                                         artifact_source_stats: dict, *, is_demo: bool) -> list:
    cluster_id = cluster["episode_cluster_id"]
    member_rows = client.select("geo_episode_cluster_members", {
        "select": "actor_episode_candidate_id", "episode_cluster_id": f"eq.{cluster_id}",
    })
    candidate_ids = [r["actor_episode_candidate_id"] for r in member_rows]
    if len(candidate_ids) < 2:
        return []

    candidates = _select_in_chunked(
        client, "geo_actor_episode_candidates",
        select="actor_episode_candidate_id,external_event_id,external_signal_ids,counterparty_text",
        in_column="actor_episode_candidate_id", in_values=candidate_ids)
    event_ids = list({c["external_event_id"] for c in candidates if c.get("external_event_id")})
    events = _select_in_chunked(
        client, "geo_external_events",
        select="external_event_id,event_base_code,action_geo_full_name,action_geo_country_code,source_url",
        in_column="external_event_id", in_values=event_ids) if event_ids else []
    event_by_id = {e["external_event_id"]: e for e in events}

    members = []
    event_by_candidate_id = {}
    signal_ids_by_candidate_id = {}
    counterparty_by_candidate_id = {}
    for c in candidates:
        cid = c["actor_episode_candidate_id"]
        members.append(cid)
        event_by_candidate_id[cid] = event_by_id.get(c.get("external_event_id"), {})
        signal_ids_by_candidate_id[cid] = set(c.get("external_signal_ids") or [])
        counterparty_by_candidate_id[cid] = c.get("counterparty_text")

    evidence_groups = _compute_evidence_groups(members, signal_ids_by_candidate_id, event_by_candidate_id)
    topic_groups = _merge_into_topic_groups(evidence_groups, event_by_candidate_id)
    meaningful = [g for g in topic_groups if len(g["member_candidate_ids"]) >= 2]
    singles = [g for g in topic_groups if len(g["member_candidate_ids"]) == 1]

    parent_member_composition = [
        {"member_candidate_ids": g["member_candidate_ids"], "base_code": g["base_code"],
         "is_ambiguous": g["is_ambiguous"]}
        for g in topic_groups
    ]

    rows = []

    if len(meaningful) >= 2:
        proposal_fingerprint = _compute_proposal_fingerprint(
            cluster_id, [g["member_candidate_ids"] for g in meaningful])
        sibling_group_key = _derive_uuid_from_hash(proposal_fingerprint)
        for g in meaningful:
            group_ids = g["member_candidate_ids"]
            group_events = [event_by_candidate_id[cid] for cid in group_ids]
            is_artifact, artifact_stats = _topic_group_is_artifact_group(group_events, artifact_source_stats)
            candidate_type = "artifact_group" if is_artifact else "split_group"
            fingerprint = _compute_group_fingerprint(proposal_fingerprint, group_ids)
            geos = sorted({ev.get("action_geo_full_name") for ev in group_events if ev.get("action_geo_full_name")})
            cps = sorted({counterparty_by_candidate_id[cid] for cid in group_ids if counterparty_by_candidate_id[cid]})
            rows.append({
                "fingerprint": fingerprint, "candidate_type": candidate_type,
                "sibling_group_key": sibling_group_key, "member_candidate_ids": group_ids,
                "dominant_event_base_code": g["base_code"], "representative_geo": geos,
                "representative_counterparties": cps,
                "separation_evidence": _build_separation_evidence(g, artifact_stats),
                "cross_cluster_affinity": [],
                "raw_metadata": {
                    "algorithm_version": ALGORITHM_VERSION,
                    "parent_cluster_member_composition": parent_member_composition,
                    "artifact_source_evaluation": artifact_stats,
                },
            })

    for g in singles:
        cid = g["member_candidate_ids"][0]
        fingerprint = _compute_outlier_fingerprint(cluster_id, cid)
        sibling_group_key = _derive_uuid_from_hash(fingerprint)
        ev = event_by_candidate_id.get(cid, {})
        geos = [ev["action_geo_full_name"]] if ev.get("action_geo_full_name") else []
        cps = [counterparty_by_candidate_id[cid]] if counterparty_by_candidate_id.get(cid) else []
        affinity = _compute_cross_cluster_affinity(client, cid, cluster_id, hub_signals, is_demo=is_demo)
        rows.append({
            "fingerprint": fingerprint, "candidate_type": "outlier",
            "sibling_group_key": sibling_group_key, "member_candidate_ids": [cid],
            "dominant_event_base_code": g["base_code"], "representative_geo": geos,
            "representative_counterparties": cps, "separation_evidence": [],
            "cross_cluster_affinity": affinity,
            "raw_metadata": {
                "algorithm_version": ALGORITHM_VERSION,
                "parent_cluster_member_composition": parent_member_composition,
                "artifact_source_evaluation": None,
            },
        })

    return rows


def _call_apply_replace_rpc(client, params: dict) -> dict:
    try:
        return client.rpc("geo_episode_split_candidate_apply_replace", params)
    except (SplitCandidateConflict, SplitCandidateParentRejected, SplitCandidateValidationError):
        raise
    except requests.exceptions.HTTPError as exc:
        message = ""
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                message = (response.json() or {}).get("message", "") or ""
            except ValueError:
                message = ""
        if message.startswith("split_candidate_conflict"):
            raise SplitCandidateConflict(message) from exc
        if message.startswith("split_candidate_parent_rejected"):
            raise SplitCandidateParentRejected(message) from exc
        if message.startswith("split_candidate_parent_cluster_not_found"):
            raise SplitCandidateValidationError(message) from exc
        raise


def generate_split_candidates(client, *, is_demo: bool = False) -> dict:
    """cluster_status<>'rejected' AND member_count>=2の全Cluster（is_demoスコープ）を対象に、
    candidate状態のSplit/Outlier行を毎回ゼロから再構築する。reviewed/accepted/rejectedの行と
    そのmembershipには一切触れない（freeze）。split_candidate_conflict発生時はCluster単位で
    MAX_REPLACE_RETRIES回まで自動リトライする。split_candidate_parent_rejectedはリトライせず、
    そのClusterをスキップして次のClusterへ進む（正常系）。"""
    population = client.select("geo_episode_clusters", {
        "select": "*", "cluster_status": "neq.rejected", "is_demo": _bool_filter(is_demo),
    })
    hub_signals = link_svc._compute_hub_values(link_svc._collect_cluster_signals(client, population), "signal_ids")
    artifact_source_stats = _compute_artifact_source_stats(client, is_demo=is_demo)
    target_clusters = [c for c in population if (c.get("member_count") or 0) >= 2]

    clusters_processed = 0
    clusters_skipped_parent_rejected = 0
    split_group_rows = artifact_group_rows = outlier_rows = 0
    inserted_total = deleted_total = 0

    for cluster in target_clusters:
        cluster_id = cluster["episode_cluster_id"]
        for _attempt in range(MAX_REPLACE_RETRIES):
            new_rows = _build_split_candidates_for_cluster(
                client, cluster, hub_signals, artifact_source_stats, is_demo=is_demo)
            existing = client.select("geo_episode_split_candidates", {
                "select": "candidate_status,fingerprint", "parent_cluster_id": f"eq.{cluster_id}",
            })
            expected_frozen = sorted(r["fingerprint"] for r in existing if r["candidate_status"] in _FROZEN_STATUSES)
            expected_candidate = sorted(r["fingerprint"] for r in existing if r["candidate_status"] == "candidate")
            try:
                rpc_result = _call_apply_replace_rpc(client, {
                    "p_parent_cluster_id": cluster_id,
                    "p_expected_frozen_fingerprints": expected_frozen,
                    "p_expected_candidate_fingerprints": expected_candidate,
                    "p_new_rows": new_rows,
                })
            except SplitCandidateConflict:
                continue
            except SplitCandidateParentRejected:
                clusters_skipped_parent_rejected += 1
                break
            clusters_processed += 1
            inserted_total += rpc_result.get("inserted", 0)
            deleted_total += rpc_result.get("deleted", 0)
            split_group_rows += sum(1 for r in new_rows if r["candidate_type"] == "split_group")
            artifact_group_rows += sum(1 for r in new_rows if r["candidate_type"] == "artifact_group")
            outlier_rows += sum(1 for r in new_rows if r["candidate_type"] == "outlier")
            break
        else:
            raise ValueError(
                f"generate_split_candidates: split_candidate_conflictが{MAX_REPLACE_RETRIES}回連続で"
                f"発生しました (parent_cluster_id={cluster_id})")

    return {
        "clusters_processed": clusters_processed,
        "clusters_skipped_parent_rejected": clusters_skipped_parent_rejected,
        "split_group_rows": split_group_rows,
        "artifact_group_rows": artifact_group_rows,
        "outlier_rows": outlier_rows,
        "inserted": inserted_total,
        "deleted": deleted_total,
    }


def list_split_candidates(client, *, parent_cluster_id: str = None, candidate_type: str = None,
                           candidate_status: str = None, limit: int = 50, offset: int = 0) -> dict:
    params = {"select": "*", "order": "created_at.desc", "limit": str(limit), "offset": str(offset)}
    if parent_cluster_id:
        params["parent_cluster_id"] = f"eq.{parent_cluster_id}"
    if candidate_type:
        params["candidate_type"] = f"eq.{candidate_type}"
    if candidate_status:
        params["candidate_status"] = f"eq.{candidate_status}"
    return {"split_candidates": client.select("geo_episode_split_candidates", params)}


def _call_update_status_rpc(client, params: dict) -> dict:
    try:
        return client.rpc("geo_episode_split_candidate_update_status", params)
    except requests.exceptions.HTTPError as exc:
        message = ""
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                message = (response.json() or {}).get("message", "") or ""
            except ValueError:
                message = ""
        if message.startswith(_UPDATE_STATUS_KNOWN_ERROR_PREFIXES):
            raise ValueError(message) from exc
        raise


def update_split_candidate_status(client, *, split_candidate_id: str, candidate_status: str,
                                   reviewed_by: str, review_notes: str = None) -> dict:
    """geo_episode_split_candidate_update_status RPCを1回呼ぶ薄いラッパー。RPC側が存在確認・
    sibling status一致確認・parent Cluster freeze検証・遷移可否検証・sibling一括UPDATEを
    1トランザクションで行う（Python側はSELECT+PostgREST bulk updateを自前で組み立てない）。"""
    _call_update_status_rpc(client, {
        "p_split_candidate_id": split_candidate_id, "p_new_status": candidate_status,
        "p_reviewed_by": reviewed_by, "p_review_notes": review_notes,
    })
    rows = client.select("geo_episode_split_candidates", {
        "select": "*", "split_candidate_id": f"eq.{split_candidate_id}", "limit": "1",
    })
    return rows[0] if rows else None


def purge_candidate_rows_for_rejected_parent(client, *, parent_cluster_id: str) -> int:
    """parent Clusterがrejectedになった際に呼ばれる。candidate_status='candidate'かつ
    parent_cluster_id一致の行を削除する（reviewed/accepted/rejected状態の行は残す）。"""
    existing = client.select("geo_episode_split_candidates", {
        "select": "split_candidate_id", "parent_cluster_id": f"eq.{parent_cluster_id}",
        "candidate_status": "eq.candidate",
    })
    count = len(existing)
    if count:
        client.delete("geo_episode_split_candidates", {
            "parent_cluster_id": f"eq.{parent_cluster_id}", "candidate_status": "eq.candidate",
        })
    return count
