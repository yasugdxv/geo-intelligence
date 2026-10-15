"""地政学ナレッジ＆インテリジェンスDB Phase2B-2A: Situation Linkage。

既存Episode Cluster（Phase2B-1）のmembership・アルゴリズムは一切変更せず、Cluster間の
関係を緩いN:Mリンクとして追加する。品質監査（member_count>=2の8 Cluster）の結果、
Cluster内部の品質はおおむね良好だが、別root_codeに分かれたClusterが同一の現実の局面
（Situation）を扱っているケースがあり、これを表現できていないことが判明した。

【最重要の設計知見（実データで検証済み）】素朴なsignal_id/geo/counterparty/theme
overlapは、母集団（is_demoスコープ内の全Cluster、全actor横断）を正しく取ると、
一見on-topicな記事・地名・counterpartyでもhub化（多くのClusterから参照される）
することが多く、判別力を失う。GDELTが1本のwire記事から複数の一見無関係な
CAMEO関係まで抽出してしまうため。一方でexternal_event_idsの共有（Actor1/Actor2視点
で文字通り同一のGDELT Eventを指している）はhub化しにくく最も精度の高いシグナルで
あるため、hub減衰の対象外としdeterministic overrideとして扱う。

DELETE/INSERTは`geo_situation_link_apply_replace`RPC（sql/2026-09-08_geo_episode_cluster_links.sql）
1回にまとめて行う。Python側からgeo_episode_cluster_linksへ直接client.delete/client.insertしない。

Actor Intelligence本体（geo_entities/geo_knowledge_items）へは一切書き込まない。
"""
from datetime import date as date_cls
from datetime import datetime, timezone

import requests

from geo_external_signal_service import _select_in_chunked

HUB_VALUE_CLUSTER_THRESHOLD = 3
SAME_SITUATION_SCORE_THRESHOLD = 8
RELATED_CONTEXT_SCORE_THRESHOLD = 3

SIGNAL_OVERLAP_WEIGHT = 3
GEO_OVERLAP_WEIGHT = 2
COUNTERPARTY_OVERLAP_WEIGHT = 1
TIME_PROXIMITY_WEIGHT = 1
TIME_PROXIMITY_WINDOW_DAYS = 14
THEME_OVERLAP_WEIGHT = 1
SHARED_EXTERNAL_EVENT_WEIGHT = 5

MAX_RECOMPUTE_RETRIES = 3

_ALLOWED_TRANSITIONS = {
    "candidate": {"reviewed", "accepted", "rejected"},
    "reviewed": {"accepted", "rejected"},
}

_FROZEN_STATUSES = ("reviewed", "accepted", "rejected")


class SituationLinkConflict(Exception):
    """同一is_demoスコープへの同時recompute、またはPython側SELECTからRPC呼び出しまでの間の
    Cluster/Link変化により、RPC内のsnapshot比較が不一致になった場合に送出する。
    recompute_situation_linksが自動的にリトライする。"""


class SituationLinkValidationError(Exception):
    """新規Linkがrejected Clusterを参照する、is_demoスコープ外のClusterを参照する等、
    Python側のペア生成ロジック自体のバグを示す。リトライしない。"""


def _bool_filter(value: bool) -> str:
    return "eq.true" if value else "eq.false"


def _call_apply_replace_rpc(client, params: dict) -> dict:
    try:
        return client.rpc("geo_situation_link_apply_replace", params)
    except (SituationLinkConflict, SituationLinkValidationError):
        raise
    except requests.exceptions.HTTPError as exc:
        message = ""
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                message = (response.json() or {}).get("message", "") or ""
            except ValueError:
                message = ""
        if message.startswith("situation_link_conflict"):
            raise SituationLinkConflict(message) from exc
        if message.startswith("situation_link_validation_error"):
            raise SituationLinkValidationError(message) from exc
        raise


def _parse_date(value: str) -> date_cls:
    return date_cls.fromisoformat(value[:10])


def _interval_gap_days(a_first: str, a_last: str, b_first: str, b_last: str) -> int:
    """2つの期間区間のgapを計算する。区間が重なっていれば0、離れていれば近い方の
    端点間の日数。単純な「どちらかの端点日付どうしの絶対差」では区間の重なりを
    正しく判定できないため、区間同士のgapとして扱う。"""
    a1, a2 = _parse_date(a_first), _parse_date(a_last)
    b1, b2 = _parse_date(b_first), _parse_date(b_last)
    if a1 <= b2 and b1 <= a2:
        return 0
    if a2 < b1:
        return (b1 - a2).days
    return (a1 - b2).days


def _normalize_pair(cluster_id_a: str, cluster_id_b: str):
    return tuple(sorted((cluster_id_a, cluster_id_b)))


def _collect_cluster_signals(client, clusters: list) -> dict:
    """各Clusterについて所属Candidateをjoinし、signal_ids/external_event_ids/geos/
    counterparties/themes/first_event_date/last_event_dateを集計する。"""
    cluster_ids = [c["episode_cluster_id"] for c in clusters]
    member_rows = _select_in_chunked(
        client, "geo_episode_cluster_members", select="episode_cluster_id,actor_episode_candidate_id",
        in_column="episode_cluster_id", in_values=cluster_ids) if cluster_ids else []

    candidate_ids = list({m["actor_episode_candidate_id"] for m in member_rows})
    candidates = _select_in_chunked(
        client, "geo_actor_episode_candidates",
        select="actor_episode_candidate_id,external_event_id,external_signal_ids,counterparty_text,theme_id",
        in_column="actor_episode_candidate_id", in_values=candidate_ids) if candidate_ids else []
    cand_by_id = {c["actor_episode_candidate_id"]: c for c in candidates}

    event_ids = list({c["external_event_id"] for c in candidates if c.get("external_event_id")})
    events = _select_in_chunked(
        client, "geo_external_events", select="external_event_id,action_geo_full_name",
        in_column="external_event_id", in_values=event_ids) if event_ids else []
    event_by_id = {e["external_event_id"]: e for e in events}

    members_by_cluster = {}
    for m in member_rows:
        members_by_cluster.setdefault(m["episode_cluster_id"], []).append(m["actor_episode_candidate_id"])

    signals = {}
    for c in clusters:
        cid = c["episode_cluster_id"]
        signal_ids, event_id_set, geos, cps, themes = set(), set(), set(), set(), set()
        for mid in members_by_cluster.get(cid, []):
            cand = cand_by_id.get(mid, {})
            signal_ids.update(cand.get("external_signal_ids") or [])
            if cand.get("external_event_id"):
                event_id_set.add(cand["external_event_id"])
            if cand.get("counterparty_text"):
                cps.add(cand["counterparty_text"])
            if cand.get("theme_id"):
                themes.add(cand["theme_id"])
            ev = event_by_id.get(cand.get("external_event_id"), {})
            if ev.get("action_geo_full_name"):
                geos.add(ev["action_geo_full_name"])
        signals[cid] = {
            "signal_ids": signal_ids, "external_event_ids": event_id_set, "geos": geos,
            "counterparties": cps, "themes": themes,
            "first_event_date": c.get("first_event_date"), "last_event_date": c.get("last_event_date"),
        }
    return signals


def _compute_hub_values(population_signals: dict, field: str) -> set:
    """引数で渡された母集団Cluster群の中でfieldの値ごとの出現Cluster数を数え、
    HUB_VALUE_CLUSTER_THRESHOLDを超えるものを返す。将来temporal cohort単位の頻度へ
    切り替える場合もpopulation_signalsを差し替えるだけで対応できる。"""
    counts = {}
    for sig in population_signals.values():
        for value in sig[field]:
            counts[value] = counts.get(value, 0) + 1
    return {value for value, n in counts.items() if n > HUB_VALUE_CLUSTER_THRESHOLD}


def _overlap_score(set_a: set, set_b: set, hub_values: set):
    overlap = (set_a - hub_values) & (set_b - hub_values)
    return len(overlap), overlap


def _compute_link(cluster_a_id: str, cluster_b_id: str, signals_a: dict, signals_b: dict,
                   hub_signals: set, hub_geos: set, hub_counterparties: set, hub_themes: set):
    signal_n, signal_overlap = _overlap_score(signals_a["signal_ids"], signals_b["signal_ids"], hub_signals)
    geo_n, geo_overlap = _overlap_score(signals_a["geos"], signals_b["geos"], hub_geos)
    cp_n, cp_overlap = _overlap_score(signals_a["counterparties"], signals_b["counterparties"], hub_counterparties)
    theme_n, theme_overlap = _overlap_score(signals_a["themes"], signals_b["themes"], hub_themes)
    shared_events = signals_a["external_event_ids"] & signals_b["external_event_ids"]  # hub減衰なし

    within_window = False
    if all((signals_a["first_event_date"], signals_a["last_event_date"],
            signals_b["first_event_date"], signals_b["last_event_date"])):
        gap_days = _interval_gap_days(signals_a["first_event_date"], signals_a["last_event_date"],
                                       signals_b["first_event_date"], signals_b["last_event_date"])
        within_window = gap_days <= TIME_PROXIMITY_WINDOW_DAYS

    score = (SIGNAL_OVERLAP_WEIGHT * signal_n + GEO_OVERLAP_WEIGHT * min(geo_n, 1) +
             COUNTERPARTY_OVERLAP_WEIGHT * min(cp_n, 1) + THEME_OVERLAP_WEIGHT * min(theme_n, 1) +
             (SHARED_EXTERNAL_EVENT_WEIGHT if shared_events else 0) +
             (TIME_PROXIMITY_WEIGHT if within_window else 0))

    if shared_events:
        # deterministic override: 文字通り同一のGDELT Eventを共有している場合、geo等の
        # 副次的な裏付けの有無に関わらず無条件でsame_situation_candidateとする。
        link_type = "same_situation_candidate"
    else:
        # Quality Guard: geo+time_proximityのみ（AP roundup記事が複数の無関係な地域へ
        # 言及することで生じるノイズ）でLinkを成立させない。スコア閾値を満たしても、
        # (a) non-hub linked article overlapが1件以上あるか、(b) time以外の独立した
        # evidence dimension（signal/geo/counterparty/theme）が2種類以上無い限りLinkを
        # 作らない（実データのIRAN root08×UNITED STATES root04等のgeo-only誤検知の回帰対策）。
        has_signal_evidence = signal_n >= 1
        independent_dimension_count = sum(
            1 for n in (signal_n, geo_n, cp_n, theme_n) if n >= 1)
        if not (has_signal_evidence or independent_dimension_count >= 2):
            return None
        if score >= SAME_SITUATION_SCORE_THRESHOLD:
            link_type = "same_situation_candidate"
        elif score >= RELATED_CONTEXT_SCORE_THRESHOLD:
            link_type = "related_context"
        else:
            return None

    source_id, target_id = _normalize_pair(cluster_a_id, cluster_b_id)
    reasons = []
    if signal_overlap:
        reasons.append({"signal": "linked_article_overlap", "value": sorted(signal_overlap)})
    if geo_overlap:
        reasons.append({"signal": "geo_overlap", "value": sorted(geo_overlap)})
    if cp_overlap:
        reasons.append({"signal": "counterparty_overlap", "value": sorted(cp_overlap)})
    if theme_overlap:
        reasons.append({"signal": "theme_overlap", "value": sorted(theme_overlap)})
    if shared_events:
        reasons.append({"signal": "shared_external_event", "value": sorted(shared_events)})
    if within_window:
        reasons.append({"signal": "time_proximity", "value": True})

    return {"source_cluster_id": source_id, "target_cluster_id": target_id,
            "link_type": link_type, "link_score": score, "link_reasons": reasons}


def _recompute_situation_links_once(client, *, is_demo: bool) -> dict:
    population = client.select("geo_episode_clusters", {
        "select": "*", "cluster_status": "neq.rejected", "is_demo": _bool_filter(is_demo),
    })
    population_ids = sorted(c["episode_cluster_id"] for c in population)
    cluster_signals = _collect_cluster_signals(client, population)

    hub_signals = _compute_hub_values(cluster_signals, "signal_ids")
    hub_geos = _compute_hub_values(cluster_signals, "geos")
    hub_counterparties = _compute_hub_values(cluster_signals, "counterparties")
    hub_themes = _compute_hub_values(cluster_signals, "themes")

    existing_links = client.select("geo_episode_cluster_links", {
        "select": "*", "is_demo": _bool_filter(is_demo),
    })
    expected_frozen_link_ids = sorted(
        l["cluster_link_id"] for l in existing_links if l["link_status"] in _FROZEN_STATUSES)
    expected_candidate_link_ids = sorted(
        l["cluster_link_id"] for l in existing_links if l["link_status"] == "candidate")

    new_links = []
    for i in range(len(population_ids)):
        for j in range(i + 1, len(population_ids)):
            a_id, b_id = population_ids[i], population_ids[j]
            link = _compute_link(a_id, b_id, cluster_signals[a_id], cluster_signals[b_id],
                                  hub_signals, hub_geos, hub_counterparties, hub_themes)
            if link:
                new_links.append(link)

    rpc_result = _call_apply_replace_rpc(client, {
        "p_is_demo": is_demo,
        "p_expected_frozen_link_ids": expected_frozen_link_ids,
        "p_expected_candidate_link_ids": expected_candidate_link_ids,
        "p_expected_population_cluster_ids": population_ids,
        "p_new_links": new_links,
    })

    return {
        "candidate_links_created": rpc_result["inserted"],
        "same_situation_candidate_count": rpc_result["inserted_same_situation_candidate"],
        "related_context_count": rpc_result["inserted_related_context"],
        "skipped_frozen_pair": rpc_result["skipped_frozen_pair"],
        "frozen_links_preserved": len(expected_frozen_link_ids),
    }


def recompute_situation_links(client, *, is_demo: bool = False) -> dict:
    """指定is_demoスコープについて、cluster_status<>'rejected'の全Cluster母集団から
    Situation Linkのcandidate状態行を毎回ゼロから再構築する。reviewed/accepted/rejected
    のLinkとそのmembershipには一切触れない（freeze）。situation_link_conflict発生時は
    MAX_RECOMPUTE_RETRIES回まで自動リトライする。"""
    last_error = None
    for _ in range(MAX_RECOMPUTE_RETRIES):
        try:
            return _recompute_situation_links_once(client, is_demo=is_demo)
        except SituationLinkConflict as exc:
            last_error = exc
            continue
    raise ValueError(
        f"recompute_situation_links: situation_link_conflictが{MAX_RECOMPUTE_RETRIES}回連続で"
        f"発生しました (is_demo={is_demo}): {last_error}")


def list_situation_links(client, *, cluster_id: str = None, link_status: str = None,
                          link_type: str = None, limit: int = 50, offset: int = 0) -> dict:
    params = {"select": "*", "order": "created_at.desc", "limit": str(limit), "offset": str(offset)}
    if link_status:
        params["link_status"] = f"eq.{link_status}"
    if link_type:
        params["link_type"] = f"eq.{link_type}"
    if cluster_id:
        params["or"] = f"(source_cluster_id.eq.{cluster_id},target_cluster_id.eq.{cluster_id})"
    return {"links": client.select("geo_episode_cluster_links", params)}


def update_situation_link_status(client, *, cluster_link_id: str, link_status: str,
                                  reviewed_by: str, review_notes: str = None) -> dict:
    """許容遷移: candidate→{reviewed,accepted,rejected}, reviewed→{accepted,rejected}。
    加えてfreeze要件: candidateからreviewed/rejectedへはsource/target両Clusterが
    freeze済み（reviewed/accepted/rejectedのいずれか）である場合のみ、acceptedへは
    source/target両Clusterがともにacceptedである場合のみ許可する。status更新のみで
    Cluster membership・Actor Intelligence本体（geo_entities/geo_knowledge_items）には
    一切触れない。"""
    rows = client.select("geo_episode_cluster_links", {
        "select": "*", "cluster_link_id": f"eq.{cluster_link_id}", "limit": "1",
    })
    if not rows:
        raise ValueError(f"cluster_link_idが見つかりません: {cluster_link_id}")
    link = rows[0]
    current_status = link["link_status"]
    allowed = _ALLOWED_TRANSITIONS.get(current_status, set())
    if link_status not in allowed:
        raise ValueError(f"許容されない状態遷移です: {current_status} -> {link_status}")

    if link_status in _FROZEN_STATUSES:
        endpoint_ids = [link["source_cluster_id"], link["target_cluster_id"]]
        clusters = _select_in_chunked(
            client, "geo_episode_clusters", select="episode_cluster_id,cluster_status",
            in_column="episode_cluster_id", in_values=endpoint_ids)
        status_by_id = {c["episode_cluster_id"]: c["cluster_status"] for c in clusters}
        endpoint_statuses = [status_by_id.get(cid) for cid in endpoint_ids]
        if link_status == "accepted":
            if not all(s == "accepted" for s in endpoint_statuses):
                raise ValueError("両端のClusterがacceptedでない限りacceptedへ遷移できません")
        else:
            if not all(s in _FROZEN_STATUSES for s in endpoint_statuses):
                raise ValueError("両端のClusterがfreeze済みでない限りreviewed/rejectedへ遷移できません")

    now_iso = datetime.now(timezone.utc).isoformat()
    client.update("geo_episode_cluster_links", {"cluster_link_id": f"eq.{cluster_link_id}"}, {
        "link_status": link_status, "reviewed_by": reviewed_by,
        "review_notes": review_notes, "reviewed_at": now_iso,
    })
    result_rows = client.select("geo_episode_cluster_links", {
        "select": "*", "cluster_link_id": f"eq.{cluster_link_id}", "limit": "1",
    })
    return result_rows[0] if result_rows else None
