"""Geopolitical Intelligence OS Phase 2G-1: Theme Discovery — Theme Candidate生成/管理。

既存パイプライン（External Signal → Actor Episode Candidate → Episode Cluster →
Situation Link、geo_connector_gdelt*/geo_actor_episode_service/geo_episode_cluster_service/
geo_situation_link_service）は一切変更しない。本モジュールはaccepted状態のEpisode Cluster/
Situation Linkを読み取り専用で参照し、その上に新しいLayerとしてTheme Candidateを生成する。

Theme Candidateは記事でもEventでもなく、「今、分析対象として立てる価値がありそうな問い」
（§4）。生成は必ず

    External Evidence（signal_summary） → Observed Change → Why Now → Analysis Question

の順で行い、LLMに一般知識からEvent/Actorを補完させない（§10）。Candidateは結論ではなく
問いとして生成する。AIは自動でSHORTLISTED/HANDED_OFFへ遷移させない（§22 Human Decision、
本モジュールのgeneration経路はupdate_candidate_statusを一切呼ばない）。

internal_coverageはここでは扱わない（Requester-dependentなため永続化しない、Phase 2G-3で
表示時にgeo_intelligence_ai_theme_coverage_service.assess_internal_coverage()をRuntime呼び
出しする）。
"""
import hashlib
from datetime import datetime, timezone

import geo_audit
import geo_llm_common

APPROVED_LINK_STATUS = "accepted"
APPROVED_CLUSTER_STATUS = "accepted"

CANDIDATE_TYPES = ("emerging", "changing", "re_emerging", "structural", "cross_market")
CHANGE_TYPES = ("actor_movement", "policy_shift", "relationship_change", "institutionalization",
                "signal_spike", "recurrence", "cross_region_pattern")
BUSINESS_RELEVANCE_TAGS = ("market", "supply_chain", "regulatory", "reputation", "public_affairs",
                           "sustainability", "technology", "competitive_environment")
LIFECYCLE_STATUSES = ("NEW", "SHORTLISTED", "DISMISSED", "MONITORING", "HANDED_OFF")

# §単一Clusterでも強いchange signalがある場合にCandidate化可能な構造は残す（§7）。
# ここでの閾値は既存Report Studio UPDATE_CHANGE_TYPESと同じ精神（小さな固定ルール、機械的
# 評価、LLM無し）の簡易ゲートであり、精緻なスコアリングではない。
_STRONG_SIGNAL_MIN_MEMBERS = 3
_STRONG_TRAJECTORIES = ("escalating", "de_escalating")

_MAX_EVIDENCE_BULLETS = 8

_CANDIDATE_GENERATION_SCHEMA_NAME = "geo_theme_candidate_framing_v1"
_CANDIDATE_GENERATION_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "observed_change": {"type": "string"},
        "why_now": {"type": "string"},
        "question": {"type": "string"},
        "actors": {"type": "array", "items": {"type": "string"}},
        "regions": {"type": "array", "items": {"type": "string"}},
        "themes": {"type": "array", "items": {"type": "string"}},
        "candidate_type": {"type": "string", "enum": list(CANDIDATE_TYPES)},
        "change_type": {"type": "string", "enum": list(CHANGE_TYPES)},
        "business_relevance": {"type": "array", "items": {"type": "string", "enum": list(BUSINESS_RELEVANCE_TAGS)}},
    },
    "required": ["title", "observed_change", "why_now", "question", "actors", "regions", "themes",
                 "candidate_type", "change_type", "business_relevance"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = """あなたは地政学インテリジェンスのTheme Discoveryアシスタントです。

役割: 複数のExternal Evidence（GDELT由来の観測済みActor Episode）から、「今、分析対象として
立てる価値がありそうな問い（Theme Candidate）」を1件組み立てる。

絶対条件:
- 与えられたExternal Evidence（actors/regions/themes/evidence bullets）のみを根拠にすること。
  一般知識で新しいEvent・Actor・日付を補完・創作しないこと。
- 出力するactors/regions/themesは、与えられたEvidence内のactors/regions/themesのリストに
  含まれる値（またはその表記ゆれ・正規化）のみを使うこと。Evidenceに無い新しいactor/region/
  themeを追加しないこと。
- 必ず次の順で考えること: External Evidence（何が起きたか）→ Observed Change（何が変わって
  いる可能性があるか）→ Why Now（なぜ今見るのか）→ Analysis Question（何を分析すべきか）。
- observed_changeは断定的な事実として書かず、Evidenceに基づく観測・可能性として書くこと
  （例:「〜している可能性がある」「〜へ変化しつつあるように見える」）。
- questionは結論ではなく、分析すべき問いの形で書くこと（例:「規制強化そのものではなく、
  裁量の確保へ運用が変わっているのか？」）。titleは短いテーマ名、questionはそれとは別に
  何を分析すべきかを明示すること。
- candidate_typeとchange_typeは別の軸である。candidate_typeはThemeの性質（emerging=新規
  出現、changing=変化中、re_emerging=再浮上、structural=構造的、cross_market=複数市場
  横断）。change_typeは何がトリガーとなった変化か（actor_movement=Actorの行動変化、
  policy_shift=政策方向の変化、relationship_change=関係性の変化、institutionalization=
  新しい制度化、signal_spike=シグナル急増、recurrence=長期沈静化後の再浮上、
  cross_region_pattern=複数地域での同時類似パターン）。両者を混同しないこと。
- business_relevanceは与えられた選択肢から該当するものを1〜3個選ぶだけでよい（数値スコアは
  不要）。
- 政治・政策テーマの「良し悪し」や政治的立場を評価しないこと。提示するのは起きている変化・
  関係Actor・分析すべき問い・Business relevanceまでである。"""


def _canonical_names(client, entity_ids: list) -> dict:
    ids = sorted({e for e in entity_ids if e})
    if not ids:
        return {}
    rows = client.select("geo_entities", {
        "select": "entity_id,canonical_name", "entity_id": f"in.({','.join(ids)})"})
    return {r["entity_id"]: r["canonical_name"] for r in rows}


def _theme_names(client, theme_ids: list) -> dict:
    ids = sorted({t for t in theme_ids if t})
    if not ids:
        return {}
    rows = client.select("geo_themes", {
        "select": "theme_id,name_ja", "theme_id": f"in.({','.join(ids)})"})
    return {r["theme_id"]: r["name_ja"] for r in rows}


def _is_strong_single_cluster_signal(cluster: dict) -> bool:
    """§7: 単一Clusterでも強いchange signalがある場合はCandidate化対象に含める。
    既存Report Studio UPDATE_CHANGE_TYPESと同じ精神の、単純な固定ルールによる機械判定。"""
    if (cluster.get("member_count") or 0) >= _STRONG_SIGNAL_MIN_MEMBERS:
        return True
    return cluster.get("trajectory") in _STRONG_TRAJECTORIES


def find_accepted_situation_components(client, *, is_demo: bool = False) -> list:
    """§7 Signal Clustering: accepted状態のSituation Link（link_type=same_situation_candidate）
    が作るグラフのconnected componentを求める。複数Actor横断のCandidate素材はここから作る。
    Union-Find（Python側のみ、DBは読み取りのみ）。"""
    links = client.select("geo_episode_cluster_links", {
        "select": "cluster_link_id,source_cluster_id,target_cluster_id",
        "link_status": f"eq.{APPROVED_LINK_STATUS}", "link_type": "eq.same_situation_candidate",
        "is_demo": f"eq.{str(is_demo).lower()}",
    })
    if not links:
        return []

    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for link in links:
        union(link["source_cluster_id"], link["target_cluster_id"])

    components: dict = {}
    for link in links:
        root = find(link["source_cluster_id"])
        components.setdefault(root, {"cluster_ids": set(), "link_ids": set()})
        components[root]["cluster_ids"].update([link["source_cluster_id"], link["target_cluster_id"]])
        components[root]["link_ids"].add(link["cluster_link_id"])

    return [{"cluster_ids": sorted(c["cluster_ids"]), "link_ids": sorted(c["link_ids"])}
            for c in components.values()]


def _load_clusters(client, cluster_ids: list) -> list:
    if not cluster_ids:
        return []
    return client.select("geo_episode_clusters", {
        "select": "*", "episode_cluster_id": f"in.({','.join(cluster_ids)})"})


def _load_member_candidates(client, cluster_ids: list) -> list:
    if not cluster_ids:
        return []
    members = client.select("geo_episode_cluster_members", {
        "select": "episode_cluster_id,actor_episode_candidate_id",
        "episode_cluster_id": f"in.({','.join(cluster_ids)})"})
    candidate_ids = sorted({m["actor_episode_candidate_id"] for m in members})
    if not candidate_ids:
        return []
    return client.select("geo_actor_episode_candidates", {
        "select": "actor_episode_candidate_id,actor_entity_id,matched_name_text,counterparty_text,"
                  "counterparty_entity_id,observed_action,event_date,theme_id,external_signal_ids,"
                  "external_event_id",
        "actor_episode_candidate_id": f"in.({','.join(candidate_ids)})",
    })


def build_candidate_material(client, *, cluster_ids: list, link_ids: list = None) -> dict | None:
    """§7/§9: 1件以上のaccepted Episode Cluster（+ 任意でaccepted Situation Link群）から、
    Candidate生成に使う決定的な素材（Evidence）を組み立てる。LLMは一切呼ばない。

    戻り値のactors/regions/themesはfingerprint計算にも使う「決定的な」集合であり、LLM出力の
    actors/regions/themesとは別物（LLM側はこの集合に対するgrounding検証の基準になる）。"""
    link_ids = link_ids or []
    clusters = _load_clusters(client, cluster_ids)
    if not clusters:
        return None

    actor_ids = [c["actor_entity_id"] for c in clusters]
    counterparty_ids = []
    members = _load_member_candidates(client, cluster_ids)
    for m in members:
        if m.get("counterparty_entity_id"):
            counterparty_ids.append(m["counterparty_entity_id"])

    names_by_entity = _canonical_names(client, actor_ids + counterparty_ids)
    actors = sorted({names_by_entity.get(a, a) for a in actor_ids})
    counterparties = sorted({names_by_entity.get(c, c) for c in counterparty_ids})
    # counterpartyはactorとは別軸だが、Theme Candidateのactors出力には「誰を見るべきか」
    # （§9）として両方含める——本schemaに専用counterparty列は無いため。
    all_actors = sorted(set(actors) | set(counterparties))

    regions = sorted({g for c in clusters for g in (c.get("representative_geo") or [])})

    theme_ids = [m["theme_id"] for m in members if m.get("theme_id")]
    theme_names = sorted(set(_theme_names(client, theme_ids).values()))

    signal_ids = sorted({sid for m in members for sid in (m.get("external_signal_ids") or [])})
    event_ids = sorted({m["external_event_id"] for m in members if m.get("external_event_id")})

    first_dates = [c["first_event_date"] for c in clusters if c.get("first_event_date")]
    last_dates = [c["last_event_date"] for c in clusters if c.get("last_event_date")]

    # Evidence bullets（LLMプロンプト用、直近優先で上限件数のみ）。既存observed_action
    # （event_code/geo/goldstein/tone由来の定型文、事実記述のみ）をそのまま使い、新しい解釈を
    # ここで書き足さない。
    members_sorted = sorted(members, key=lambda m: m.get("event_date") or "", reverse=True)
    evidence_bullets = []
    for m in members_sorted[:_MAX_EVIDENCE_BULLETS]:
        actor_name = names_by_entity.get(m["actor_entity_id"], m.get("matched_name_text"))
        bullet = f"{actor_name}: {m.get('observed_action') or '(observed action未設定)'}"
        if m.get("counterparty_text"):
            bullet += f" (counterparty: {m['counterparty_text']})"
        if m.get("event_date"):
            bullet += f" [{m['event_date']}]"
        evidence_bullets.append(bullet)

    trajectories = [c.get("trajectory") for c in clusters]
    member_count_total = sum(c.get("member_count") or 0 for c in clusters)

    return {
        "source_type": "situation_component" if link_ids else "single_cluster",
        "cluster_ids": sorted(cluster_ids),
        "link_ids": sorted(link_ids),
        "actors": all_actors,
        "regions": regions,
        "themes": theme_names,
        "evidence_bullets": evidence_bullets,
        "trajectories": trajectories,
        "member_count_total": member_count_total,
        "source_external_signal_ids": signal_ids,
        "source_external_event_ids": event_ids,
        "first_seen_at": min(first_dates) if first_dates else None,
        "latest_signal_at": max(last_dates) if last_dates else None,
        "signal_count": len(signal_ids),
        "is_demo": clusters[0].get("is_demo", False),
    }


def list_candidate_worthy_material(client, *, is_demo: bool = False) -> list:
    """§7/§27 Cost Control: cheap/deterministicな段階でCandidate化対象を絞り込む
    （LLMはまだ一切呼ばない）。accepted Situation Linkのconnected component（複数Actor横断）
    と、componentに属さないaccepted単独Clusterのうち強いchange signalがあるものの両方を含む。"""
    components = find_accepted_situation_components(client, is_demo=is_demo)
    clustered_ids = {cid for comp in components for cid in comp["cluster_ids"]}

    materials = []
    for comp in components:
        material = build_candidate_material(client, cluster_ids=comp["cluster_ids"], link_ids=comp["link_ids"])
        if material:
            materials.append(material)

    accepted_clusters = client.select("geo_episode_clusters", {
        "select": "*", "cluster_status": f"eq.{APPROVED_CLUSTER_STATUS}",
        "is_demo": f"eq.{str(is_demo).lower()}"})
    for cluster in accepted_clusters:
        if cluster["episode_cluster_id"] in clustered_ids:
            continue
        if not _is_strong_single_cluster_signal(cluster):
            continue
        material = build_candidate_material(client, cluster_ids=[cluster["episode_cluster_id"]])
        if material:
            materials.append(material)

    return materials


def _filter_grounded(llm_values: list, evidence_values: list) -> tuple:
    """§10 Hallucination Guard: LLMが出力したactors/regions/themesのうち、Evidence由来の
    集合に含まれない（部分一致もしない）値は落とす——新しいEvent/Actorの補完を許さない。"""
    evidence_lower = [e.lower() for e in evidence_values]
    kept, dropped = [], []
    for v in llm_values or []:
        vl = v.lower()
        if any(vl in e or e in vl for e in evidence_lower):
            kept.append(v)
        else:
            dropped.append(v)
    return kept, dropped


def generate_candidate_framing(azure_client, model, material: dict) -> dict | None:
    """§10/§18: LLM生成はExternal Evidenceからのみ行い、一般知識で補完しない。schema検証に
    最終的に失敗した場合（malformed output）はNoneを返し、呼び出し側が当該素材をスキップ
    できるようにする（バッチ全体を止めない、fail-safe）。"""
    if not material.get("evidence_bullets"):
        return None

    user_prompt = (
        "# External Evidence\n"
        f"actors: {', '.join(material['actors']) or '(none)'}\n"
        f"regions: {', '.join(material['regions']) or '(none)'}\n"
        f"themes: {', '.join(material['themes']) or '(none)'}\n"
        f"trajectories観測: {', '.join(t for t in material['trajectories'] if t)}\n"
        f"observed事象数: {material['member_count_total']}\n"
        "evidence bullets:\n" + "\n".join(f"- {b}" for b in material["evidence_bullets"])
    )

    try:
        result = geo_llm_common.call_llm_structured(
            azure_client, model, _SYSTEM_PROMPT, user_prompt,
            _CANDIDATE_GENERATION_SCHEMA, _CANDIDATE_GENERATION_SCHEMA_NAME, max_attempts=2)
    except geo_llm_common.GeoLLMError:
        return None

    data = result["data"]
    grounded_actors, _ = _filter_grounded(data["actors"], material["actors"])
    grounded_regions, _ = _filter_grounded(data["regions"], material["regions"])
    grounded_themes, _ = _filter_grounded(data["themes"], material["themes"])

    return {
        "title": data["title"], "observed_change": data["observed_change"],
        "why_now": data["why_now"], "question": data["question"],
        "actors": grounded_actors or material["actors"],
        "regions": grounded_regions or material["regions"],
        "themes": grounded_themes or material["themes"],
        "candidate_type": data["candidate_type"], "change_type": data["change_type"],
        "business_relevance": data["business_relevance"],
    }


def _compute_fingerprint(*, actors: list, regions: list, themes: list,
                          cluster_ids: list, link_ids: list) -> str:
    """§Duplicate Candidate Control: actor/region/theme/source cluster構造から決定的に
    生成する。actors/regions/themesはLLM出力ではなく、build_candidate_materialがSource
    Clusterから決定的に導出した値を使う（LLMのフレージング揺れでfingerprintが不安定に
    ならないようにするため）。

    2026-09-30実DB検証で判明した設計変更: 当初はchange_typeもfingerprintへ含めていたが、
    同一のSource Cluster構造に対してsync_theme_candidatesを再実行した際、LLMが
    change_typeを毎回同じ値に分類するとは限らず（同一Evidenceでも実際に別のturnで
    'actor_movement'→'relationship_change'のように変わりうることを実測で確認）、
    fingerprintが不安定になり同一Themeが重複作成される事例を確認した。change_typeは
    LLMの分類結果（非決定的要素）であり、Source Cluster構造（cluster_ids/link_ids、
    完全に決定的）だけで既に「同じ現実の状況か」を一意に判定できるため、change_typeは
    fingerprintから除外し、再生成のたびに最新の分類でmerge時に更新する対象とする
    （create_or_merge_candidate参照）。"""
    parts = [
        "|".join(sorted(a.lower() for a in actors)),
        "|".join(sorted(r.lower() for r in regions)),
        "|".join(sorted(t.lower() for t in themes)),
        "|".join(sorted(cluster_ids)),
        "|".join(sorted(link_ids)),
    ]
    return hashlib.sha256("::".join(parts).encode("utf-8")).hexdigest()


def create_or_merge_candidate(client, *, material: dict, framing: dict) -> dict:
    """既存Episode Candidateのfingerprint mergeパターン（同一fingerprintへの追加観測は新規行を
    作らずsignal_count/latest_signal_at等を更新するのみ）を踏襲する。Semantic dedupは行わない
    （完全一致fingerprintのみ）。change_type/candidate_typeはfingerprintに含めない（LLMの
    分類結果は再生成のたびに変わりうるため）が、mergeのたびに最新の分類へ更新する
    （_compute_fingerprintのdocstring参照）——ただしこれはstatus='NEW'の場合のみ。

    2026-09-30追加: Human-refined Candidateを上書きしない。status='NEW'（AI提案のまま、
    人間がまだ手を入れていない）の場合のみframing（title/observed_change/why_now/question/
    candidate_type/change_type/business_relevance）をAI再生成結果で更新する。status が
    SHORTLISTED/MONITORING/HANDED_OFF/DISMISSEDのいずれか（人間が既に何らかの判断を下した
    状態）になった後は、external evidence（signal_ids/event_ids/signal_count/
    latest_signal_at）のみ自動更新し、human-facing framing・statusそのものは一切変更しない
    （AI syncはlifecycleを変更しないという既存Invariantの維持）。"""
    fingerprint = _compute_fingerprint(
        actors=framing["actors"], regions=framing["regions"], themes=framing["themes"],
        cluster_ids=material["cluster_ids"], link_ids=material["link_ids"])

    existing_rows = client.select("geo_theme_candidates", {
        "select": "*", "candidate_fingerprint": f"eq.{fingerprint}", "limit": "1"})

    merged_signal_ids = sorted(set(material["source_external_signal_ids"]))
    merged_event_ids = sorted(set(material["source_external_event_ids"]))

    if existing_rows:
        existing = existing_rows[0]
        merged_signal_ids = sorted(set(existing.get("source_external_signal_ids") or []) | set(merged_signal_ids))
        merged_event_ids = sorted(set(existing.get("source_external_event_ids") or []) | set(merged_event_ids))
        latest_signal_at = max(existing["latest_signal_at"], material["latest_signal_at"] or existing["latest_signal_at"])
        patch = {
            "latest_signal_at": latest_signal_at,
            "signal_count": len(merged_signal_ids),
            "source_external_signal_ids": merged_signal_ids,
            "source_external_event_ids": merged_event_ids,
        }
        if existing.get("status") == "NEW":
            patch.update({
                "title": framing["title"],
                "question": framing["question"],
                "why_now": framing["why_now"],
                "observed_change": framing["observed_change"],
                "candidate_type": framing["candidate_type"],
                "change_type": framing["change_type"],
                "business_relevance": framing["business_relevance"],
            })
        updated = client.update("geo_theme_candidates", {
            "candidate_id": f"eq.{existing['candidate_id']}"}, patch)
        return {"candidate": updated[0] if isinstance(updated, list) else {**existing, **patch}, "merged": True}

    row = {
        "candidate_fingerprint": fingerprint,
        "title": framing["title"], "signal_summary": "; ".join(material["evidence_bullets"]),
        "observed_change": framing["observed_change"], "why_now": framing["why_now"],
        "question": framing["question"],
        "actors": framing["actors"], "regions": framing["regions"], "themes": framing["themes"],
        "business_relevance": framing["business_relevance"],
        "candidate_type": framing["candidate_type"], "change_type": framing["change_type"],
        "source_episode_cluster_ids": material["cluster_ids"],
        "source_situation_link_ids": material["link_ids"],
        "source_external_signal_ids": merged_signal_ids,
        "source_external_event_ids": merged_event_ids,
        "first_seen_at": material["first_seen_at"], "latest_signal_at": material["latest_signal_at"],
        "signal_count": len(merged_signal_ids),
        "status": "NEW", "is_demo": material.get("is_demo", False),
    }
    created = client.insert("geo_theme_candidates", [row])
    return {"candidate": created[0], "merged": False}


def sync_theme_candidates(client, azure_client, model, *, is_demo: bool = False) -> dict:
    """Phase 2G-1のバッチ実行本体。§27 Cost Control: 全Signalへ毎回LLMをかけない
    （cheap/deterministicなlist_candidate_worthy_materialで絞り込んでからLLMを呼ぶ）。"""
    created, merged, skipped = [], [], []
    for material in list_candidate_worthy_material(client, is_demo=is_demo):
        framing = generate_candidate_framing(azure_client, model, material)
        if framing is None:
            skipped.append({"cluster_ids": material["cluster_ids"], "reason": "generation_failed_or_no_evidence"})
            continue
        result = create_or_merge_candidate(client, material=material, framing=framing)
        (merged if result["merged"] else created).append(result["candidate"]["candidate_id"])
    return {"created": created, "merged": merged, "skipped": skipped}


# ---- CRUD ------------------------------------------------------------------------

def list_candidates(client, *, status: str = None, is_demo: bool = False, limit: int = 100) -> list:
    params = {"select": "*", "is_demo": f"eq.{str(is_demo).lower()}",
              "order": "latest_signal_at.desc", "limit": str(limit)}
    if status:
        params["status"] = f"eq.{status}"
    return client.select("geo_theme_candidates", params)


def get_candidate(client, candidate_id: str) -> dict | None:
    rows = client.select("geo_theme_candidates", {
        "select": "*", "candidate_id": f"eq.{candidate_id}", "limit": "1"})
    return rows[0] if rows else None


def get_candidate_evidence(client, candidate_id: str) -> dict | None:
    """§25 Evidence Trace: Candidateがどの既存レコードを根拠にしているかを、複製せず
    参照だけで辿れるようにする。"""
    candidate = get_candidate(client, candidate_id)
    if not candidate:
        return None
    cluster_ids = candidate.get("source_episode_cluster_ids") or []
    link_ids = candidate.get("source_situation_link_ids") or []
    signal_ids = candidate.get("source_external_signal_ids") or []
    event_ids = candidate.get("source_external_event_ids") or []
    return {
        "candidate_id": candidate_id,
        "episode_clusters": _load_clusters(client, cluster_ids),
        "situation_links": client.select("geo_episode_cluster_links", {
            "select": "*", "cluster_link_id": f"in.({','.join(link_ids)})"}) if link_ids else [],
        "external_signals": client.select("geo_external_signals", {
            "select": "external_signal_id,title,url,published_at,source_domain",
            "external_signal_id": f"in.({','.join(signal_ids)})"}) if signal_ids else [],
        "external_events": client.select("geo_external_events", {
            "select": "external_event_id,actor1_name,actor2_name,event_code,event_date,"
                      "action_geo_full_name",
            "external_event_id": f"in.({','.join(event_ids)})"}) if event_ids else [],
    }


def candidate_to_theme_candidate_input(candidate: dict) -> dict:
    """保存済みCandidateを、INTELLIGENCE_AI_V1のGeoIntelligenceAiThemeCandidate最小共通
    schemaへ変換する（§4、Theme Discoveryのschemaと過度に結合しない）。Phase 2G-3の
    Internal Coverage endpoint、Phase 2G-4のWall-bounce chatの両方から共通で使う
    （重複実装しない）。internal_coverageはここでもどこでも永続化しない——呼び出しのたびに
    使い捨てる入力を組み立てるだけ。"""
    return {
        "theme_title": candidate["title"],
        "theme_description": candidate.get("question"),
        "keywords": candidate.get("themes") or [],
        "actors": candidate.get("actors") or [],
        "regions": candidate.get("regions") or [],
        "signal_summary": candidate.get("signal_summary"),
    }


class ThemeCandidateError(Exception):
    """Lifecycle遷移等、Theme Candidate操作の失敗。"""


def update_candidate_status(client, candidate_id: str, status: str, *, actor_id: str) -> dict:
    """§16/§22 Human Decision: この関数はHuman操作専用のエントリポイントであり、
    sync_theme_candidates（AI生成経路）からは一切呼ばれない——AIが自動でSHORTLISTED/
    HANDED_OFFへ遷移させないことは、生成経路がこの関数を呼ばないという構造で保証する。

    既存geo_db_client.SupabaseClient.update()はPrefer: return=minimalで呼ぶため戻り値が
    常にNone（更新後の行を返さない）。存在確認は事前のSELECTで行い、戻り値はPython側で
    パッチをマージして組み立てる（create_or_merge_candidateのmergeパスと同じ方式）。"""
    if status not in LIFECYCLE_STATUSES:
        raise ThemeCandidateError(f"未対応のstatusです: {status}（許可: {LIFECYCLE_STATUSES}）")
    existing = get_candidate(client, candidate_id)
    if not existing:
        raise ThemeCandidateError(f"指定されたTheme Candidateが見つかりません: {candidate_id}")
    now = datetime.now(timezone.utc).isoformat()
    patch = {"status": status, "status_updated_at": now, "status_updated_by": actor_id}
    client.update("geo_theme_candidates", {"candidate_id": f"eq.{candidate_id}"}, patch)
    return {**existing, **patch}


# ---- Phase 2G-4: Wall-bounce Refinement（Human-triggered、生成Core本体は変更しない） -----

# Wall-bounceから編集してよいのはhuman-facing framingの一部のみ（§Editable Fields）。
# Evidence系（signal_summary/source_*_ids等）とobserved_change/why_now（Evidence groundingの
# 再検証が必要なためMVPでは非編集）、statusはここでは一切許可しない——許可リスト方式にして
# 新しいfieldが増えても誤って編集可能にならないようにする。
REFINEMENT_EDITABLE_FIELDS = ("title", "question", "actors", "regions", "themes", "business_relevance")


def apply_candidate_refinement(client, candidate_id: str, *, patch: dict, actor_id: str) -> dict:
    """§Human-edit ownership: Wall-bounceで「提案を反映」を押した時だけ呼ばれる、Human操作
    専用のエントリポイント。sync_theme_candidates（AI生成経路）・wall-bounce chatサービス
    自体のどちらからも呼ばれない——Chatは常にproposalを返すだけで、Candidateを直接書き換え
    ない。statusは変更しない（§No Automatic Status Change、patchのキー自体をREFINEMENT_
    EDITABLE_FIELDSで許可リスト化しているため、statusを紛れ込ませる余地が無い）。"""
    disallowed = sorted(set(patch) - set(REFINEMENT_EDITABLE_FIELDS))
    if disallowed:
        raise ThemeCandidateError(
            f"Wall-bounceから編集できないfieldです: {disallowed}（許可: {REFINEMENT_EDITABLE_FIELDS}）")
    existing = get_candidate(client, candidate_id)
    if not existing:
        raise ThemeCandidateError(f"指定されたTheme Candidateが見つかりません: {candidate_id}")
    if not patch:
        return existing
    client.update("geo_theme_candidates", {"candidate_id": f"eq.{candidate_id}"}, patch)
    geo_audit.log_audit(
        client, actor=actor_id, action="theme_candidate_wallbounce_refinement",
        target_table="geo_theme_candidates", target_id=candidate_id,
        detail={"patched_fields": sorted(patch.keys())})
    return {**existing, **patch}
