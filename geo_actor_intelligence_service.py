"""地政学インテリジェンス基盤: Actor Intelligence Layer（Phase AI-1〜3）。

既存のEntity/Knowledge/Relationship/Claim DBを正本として再利用し、新規テーブル・カラム・
CHECK制約は一切追加しない（DBマイグレーションなし。承認済みPlan参照）。

設計上の要点（詳細はdocs/geopolitical-expert-actor-intelligence-phase-ai1-ai3.md）:
- Actor Identity: geo_entitiesをそのまま使う。安定ID（"donald_trump"等）はexternal_ids
  （jsonb, 既存カラム）へ保存する。正本の取得APIはentity_idベース
  （get_actor_profile_item_by_entity_id）。actor_intelligence_idはSeed/名前解決専用。
- Core Profile/Decision Pattern/Current Posture/Issue Position: geo_knowledge_items
  (knowledge_type=actor_profile)のstructured_contentへオプショナルキーとして追加
  （geo_knowledge_item_schemas.ACTOR_PROFILE_SCHEMA参照）。
- actor_intelligence_status（sufficient/partial/insufficient）は永続化しない。
  「Actor Profileそのものの充実度」と「あるSituationに対する充足度」は別物であり、
  Situation・reference_date・当該issue_idに対するTemporal/Issue Relevance/Evidence
  Coverageを見て、select_key_actors/build_actor_intelligence_pack側でその都度算出する。
- Key Actor Selectionは、Actor Profileの有無とは独立に行う（情報が豊富なActorだけが
  選ばれる偏りを避けるため）。Candidate Actor取得はRetrieval/Knowledge Service層のみを
  呼び、geo_expert_engine（Expert Engine側が本モジュールを呼ぶ側）は呼ばない。
"""
import hashlib
import json
import os
from datetime import date, datetime, timezone

import geo_entity_service
import geo_knowledge_service
import geo_relationship_service
import geo_retrieval_service

# ── Feature Flag（geo_expert_engine.pyから参照される既定値。ここでは定数のみ定義） ──
ACTOR_MAX_ACTORS_ENV = "GEO_EXPERT_ACTOR_MAX_ACTORS"
DEFAULT_MAX_ACTORS = 5

# Actorとして扱えるentity_type（geo_entity_service.ENTITY_TYPESの実際の定義に合わせる。
# 新しいentity_typeは追加しない）。地名・施設・品目・制度等の非意思決定主体は除外する。
ACTOR_ENTITY_TYPES = frozenset({
    "country", "government", "ministry", "political_party", "faction", "person",
    "military", "security_service", "international_organization",
    "state_owned_enterprise", "company", "armed_group",
})

# Candidate Actor探索・Pack構築のBudget（新規環境変数は増やさず、コード定数として管理する）
CANDIDATE_RELATIONSHIP_EXPANSION_TOP_K = 5  # 1-hop Relationship展開時、起点entity1つあたりの上限
ACTOR_PACK_CHAR_BUDGET = 6000
ACTOR_PACK_PER_ACTOR_CHAR_BUDGET = 1200
ACTOR_PACK_PRECEDENT_TOP_K = 2
ACTOR_PACK_RELATIONSHIP_TOP_K = 3
ACTOR_PACK_DECISION_PATTERN_TOP_K = 2

_FRESHNESS_CURRENT_DAYS = 30
_FRESHNESS_AGING_DAYS = 90


def default_max_actors() -> int:
    raw = os.environ.get(ACTOR_MAX_ACTORS_ENV, "").strip()
    if not raw:
        return DEFAULT_MAX_ACTORS
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_MAX_ACTORS


# ===================================================================
# Actor Identity（Phase AI-1）
# ===================================================================

def resolve_entity_id_for_actor_intelligence_id(client, actor_intelligence_id: str) -> str | None:
    """actor_intelligence_id（"donald_trump"等）→ entity_id の名前解決専用ヘルパー
    （Seed/CLI用途限定。正本APIではない）。"""
    for entity in geo_entity_service.list_entities(client, include_demo=True, limit=1000):
        if (entity.get("external_ids") or {}).get("actor_intelligence_id") == actor_intelligence_id:
            return entity["entity_id"]
    return None


def get_or_create_actor_entity(client, *, actor_intelligence_id: str, canonical_name: str,
                                entity_type: str = "person", actor_id: str = "actor_intelligence_seed") -> dict:
    """Entity重複を防ぐため、以下の順で解決する:
    1. 渡されたentity_typeでbounded select → external_ids.actor_intelligence_idが一致するものを探す
    2. 一致しなければcanonical_name一致（同名Entityが既にactor_intelligence_id未設定で存在する場合）
    3. どちらにも該当しなければgeo_entity_service.create_entityで新規作成する
    解決後、external_idsは既存値を取得してmergeしてから更新する（上書きしない）。"""
    existing = None
    for entity in geo_entity_service.list_entities(client, entity_type=entity_type, include_demo=True, limit=1000):
        if (entity.get("external_ids") or {}).get("actor_intelligence_id") == actor_intelligence_id:
            existing = entity
            break

    if existing is None:
        for entity in geo_entity_service.list_entities(client, entity_type=entity_type, include_demo=True, limit=1000):
            if entity.get("canonical_name") == canonical_name:
                existing = entity
                break

    if existing is None:
        existing = geo_entity_service.create_entity(
            client, entity_type=entity_type, canonical_name=canonical_name, actor_id=actor_id,
        )

    current_external_ids = dict(existing.get("external_ids") or {})
    if current_external_ids.get("actor_intelligence_id") != actor_intelligence_id:
        current_external_ids["actor_intelligence_id"] = actor_intelligence_id
        client.update("geo_entities", {"entity_id": f"eq.{existing['entity_id']}"},
                      {"external_ids": current_external_ids})
        existing = dict(existing)
        existing["external_ids"] = current_external_ids
    return existing


# ===================================================================
# Actor Profile取得（正本API、entity_idベース）
# ===================================================================

def get_actor_profile_item_by_entity_id(client, entity_id: str, *,
                                         retrieval_status: str = None,
                                         statuses: tuple = ("active",)) -> dict | None:
    """knowledge_type=actor_profile, status=activeの中からentity_id一致を1件返す
    （geo_persistent_preference_service.get_active_actor_profileと同じ「候補をPythonで
    スキャンする」方式を、Foundation Packが無い文脈でも使えるよう汎用化）。
    人物以外のentity_idでも同じ関数で動く（将来のNATO/IRGC等の組織Actor拡張を想定）。
    地政学アプリCleanup（P0-2）: retrieval_statusは呼び出し元が明示した場合のみ絞り込む
    （既定Noneは後方互換。AI/Packへ実際に渡す呼び出し箇所は"available"を指定し、他の
    Reusable Knowledge取得経路と同じstatus/retrieval_status二軸フィルタへ揃える）。
    M9 Actor×Media Integration Pilot: 既定statuses=("active",)は本番相当のまま変更しない。
    比較実験（run_expert_request_staged等、DB非保存）でのみ、呼び出し元が明示的に
    statuses=("active","candidate")等へ上書きする（Human Review未了Candidateの
    本番混入は防ぐ、Media Intelligence Pack側の既存設計と同じ考え方）。優先順位:
    statusesの先頭から順に見つかった最初の1件を返す（activeを優先）。"""
    if not entity_id:
        return None
    for status in statuses:
        candidates = geo_knowledge_service.list_knowledge_items(
            client, knowledge_type="actor_profile", status=status,
            retrieval_status=retrieval_status, include_demo=True, limit=500)
        for item in candidates:
            content = item.get("structured_content") or {}
            if content.get("entity_id") == entity_id:
                return item
    return None


def _find_pending_actor_profile_item(client, entity_id: str) -> dict | None:
    """candidate/in_review状態のactor_profileアイテムをentity_idで探す（idempotent Seed用）。"""
    for status in ("candidate", "in_review"):
        for item in geo_knowledge_service.list_knowledge_items(
                client, knowledge_type="actor_profile", status=status, include_demo=True, limit=500):
            content = item.get("structured_content") or {}
            if content.get("entity_id") == entity_id:
                return item
    return None


def _canonicalize_fingerprint(structured_content: dict) -> str:
    """structured_contentの内容フィンガープリント（キー順序を揃えてハッシュ化）。
    Seedのidempotent化で「内容が変わっていなければ何もしない」判定に使う。"""
    normalized = json.dumps(structured_content, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# ===================================================================
# Seed（Phase AI-1）: 既定candidateのみ。--activateで初めてactive化する
# ===================================================================

def seed_actor_profile(client, *, actor_intelligence_id: str, canonical_name: str,
                        entity_type: str = "person", core_profile: dict = None,
                        decision_patterns: list = None, current_posture: dict = None,
                        issue_positions: list = None, default_importance: str = None,
                        needs_enrichment: list = None, coverage_notes: str = None,
                        created_by: str = "seed_script", activate: bool = False,
                        reviewer_id: str = "seed_script") -> dict:
    """Actor Profileをcandidateとして作成/更新する（内容差分ベースでidempotent）。
    actor_intelligence_status（sufficient/partial/insufficient）はここでは持たない
    （ケース依存であり、build_actor_intelligence_pack側で都度算出するため）。"""
    entity = get_or_create_actor_entity(
        client, actor_intelligence_id=actor_intelligence_id, canonical_name=canonical_name,
        entity_type=entity_type, actor_id=created_by)
    entity_id = entity["entity_id"]

    structured_content = {
        "entity_id": entity_id,
        "objectives": (core_profile or {}).get("core_objectives_summary", []),
        "core_interests": (core_profile or {}).get("core_interests_summary", []),
        "default_importance": default_importance,
        "needs_enrichment": needs_enrichment or [],
        "coverage_notes": coverage_notes,
        "core_profile": core_profile,
        "decision_patterns": decision_patterns or [],
        "current_posture": current_posture,
        "issue_positions": issue_positions or [],
    }
    new_fingerprint = _canonicalize_fingerprint(structured_content)

    active_item = get_actor_profile_item_by_entity_id(client, entity_id)
    pending_item = _find_pending_actor_profile_item(client, entity_id)

    if pending_item is not None:
        existing_fingerprint = _canonicalize_fingerprint(pending_item.get("structured_content") or {})
        if existing_fingerprint == new_fingerprint:
            item = pending_item  # no-op: 内容変更なし
        else:
            client.update("geo_knowledge_items", {"knowledge_item_id": f"eq.{pending_item['knowledge_item_id']}"},
                          {"structured_content": structured_content})
            item = geo_knowledge_service.get_knowledge_item(client, pending_item["knowledge_item_id"])
    elif active_item is not None:
        existing_fingerprint = _canonicalize_fingerprint(active_item.get("structured_content") or {})
        if existing_fingerprint == new_fingerprint:
            item = active_item  # no-op: 内容変更なし、新版を作らない
        else:
            item = geo_knowledge_service.create_knowledge_item(
                client, knowledge_type="actor_profile", title=canonical_name,
                structured_content=structured_content, information_class="internal_hypothesis",
                created_by=created_by, status="candidate", supersedes_id=active_item["knowledge_item_id"],
                origin="case_candidate_extraction",
            )
    else:
        item = geo_knowledge_service.create_knowledge_item(
            client, knowledge_type="actor_profile", title=canonical_name,
            structured_content=structured_content, information_class="internal_hypothesis",
            created_by=created_by, status="candidate", origin="case_candidate_extraction",
        )

    if activate and item.get("status") in ("candidate", "in_review"):
        if item["status"] == "candidate":
            item = geo_knowledge_service.review_knowledge_item(
                client, knowledge_item_id=item["knowledge_item_id"], reviewer_id=reviewer_id,
                reviewer_role="knowledge_steward", review_decision="start_review",
                review_comment="Actor Intelligence Seed: review開始", evidence_adequacy="sufficient",
                applicability_checked=True,
            )["knowledge_item"]
        item = geo_knowledge_service.review_knowledge_item(
            client, knowledge_item_id=item["knowledge_item_id"], reviewer_id=reviewer_id,
            reviewer_role="knowledge_steward", review_decision="approve_active",
            review_comment="Actor Intelligence Seed: --activate指定によりactive化",
            evidence_adequacy="sufficient", applicability_checked=True,
        )["knowledge_item"]

    return item


# ===================================================================
# Situation-to-Actor Retrieval（Phase AI-2）
# ===================================================================

_RELEVANT_ENTITY_NAME_FIELDS = ["canonical_name", "name_en", "name_ja", "local_name"]
_RELEVANT_ENTITY_MIN_NAME_LENGTH = 2


def _entity_mentioned_in_text(entity: dict, text_lower: str) -> bool:
    """Phase 6A-2(Relationship Relevance Precision、ユーザー指示): entityの名前フィールド
    いずれかがtext_lower（呼び出し元が既に小文字化した結合テキスト）に部分文字列として
    現れるか。geo_expert_engine.resolve_runtime_actor_candidatesの判定ロジックと同じ方式
    （Entity数が少ないため全件を文字列一致で判定する、既存の設計を踏襲）。"""
    if not text_lower:
        return False
    for field in _RELEVANT_ENTITY_NAME_FIELDS:
        name = entity.get(field)
        if name and len(name) >= _RELEVANT_ENTITY_MIN_NAME_LENGTH and name.lower() in text_lower:
            return True
    return False


def get_candidate_actor_entities(client, case_id: str | None,
                                  *, extra_candidate_entity_ids: set | None = None,
                                  situation_text: str | None = None) -> list:
    """候補母集団のunion（既存のIssueタグ付けの完全性に依存しすぎない）:
    (a) Issue-linked entities, (b) Case-linked（geo_casesの実際のスキーマを確認した結果、
    Issue経由以外の直接entity紐付けは存在しない。よって(a)と同一の信号として扱う。
    Case Contextに登場するActorも同様で、既存geo_foundation_pack_service.build_foundation_pack
    自体がissue-tag+1-hop Relationship補完以上のテキストベースEntity抽出を行っていないため、
    現状のDB/既存コードでは(a)+(c)を超える独立した信号は存在しない）, (c) 1-hop Relationship
    （起点entityにつきCANDIDATE_RELATIONSHIP_EXPANSION_TOP_K件に制限）。
    Actor Entity Type Filterを適用してから返す。Retrieval/Knowledge Service層のみを呼び、
    geo_expert_engineには依存しない。

    extra_candidate_entity_ids（ユーザー指示Phase D、Actor Runtime Activation）: Issue Link
    以外の経路（query/Case明示のActor、Source Pack由来のActor等、呼び出し元
    geo_expert_engine.resolve_runtime_actor_candidatesが解決）で見つかったentity_idの集合。
    DBへは一切永続化せず、この関数呼び出しの中だけでIssue-linked候補と合流させ、以降の
    1-hop Relationship展開・スコアリングを同じ経路に通す（既存ロジックは変更しない、
    候補母集団の入力を増やすだけ）。case_id=Noneでも動作する
    （get_issue_ids_for_caseは未紐付きcase_idに対し安全に空リストを返す既存挙動）。

    situation_text（ユーザー指示Phase 6A-2、Relationship Relevance Precision）: query_text+
    source_pack本文を結合し小文字化したテキスト。2026-09-19実データ監査（TSMC Case）で、
    Runtime解決された「China」から1-hop展開した「Iran」（TSMCの局面とは無関係、Hormuz
    クラスタ由来の既存Relationshipのみを根拠に混入）がKey Actorへ昇格する実例を確認した
    ——「DB上で1-hopだから採用」を無条件に許すと、Runtime解決された「General/多く登場する
    国」の1-hop隣接Entityが無関係に混入しやすい。situation_textを指定した場合のみ、
    1-hop展開で見つかった隣接Entityは、その名前がsituation_textに明示されている場合に
    限り候補へ昇格させる。Noneの場合（既定）は既存の無条件1-hop展開を維持する
    （既存呼び出し元・既存テストとの完全な後方互換、Issue-linkedな起点からの展開は
    人間がCurationしたCase/Issueに紐づく1-hopのため、この監査の対象外）。"""
    issue_ids = geo_retrieval_service.get_issue_ids_for_case(client, case_id) if case_id else []
    issue_linked = geo_retrieval_service.get_relevant_entities(client, issue_ids, include_demo=True)

    by_id = {e["entity_id"]: e for e in issue_linked}

    if extra_candidate_entity_ids:
        for entity_id in extra_candidate_entity_ids:
            if entity_id in by_id:
                continue
            entity = geo_entity_service.get_entity(client, entity_id)
            if entity:
                by_id[entity_id] = entity

    for entity_id in list(by_id.keys()):
        relationships = geo_relationship_service.list_relationships_for_entity(
            client, entity_id, include_demo=True)

        def _rank_key(rel):
            leverage = 1 if rel.get("leverage_direction") else 0
            confidence_rank = {"高": 3, "中": 2, "低": 1, "判断不能": 0}.get(rel.get("confidence"), 0)
            return (leverage, confidence_rank)

        relationships = sorted(relationships, key=_rank_key, reverse=True)
        relationships = relationships[:CANDIDATE_RELATIONSHIP_EXPANSION_TOP_K]

        neighbor_ids = set()
        for rel in relationships:
            neighbor_ids.add(rel.get("from_entity_id"))
            neighbor_ids.add(rel.get("to_entity_id"))
        neighbor_ids.discard(entity_id)
        neighbor_ids.discard(None)
        neighbor_ids -= set(by_id.keys())

        for neighbor_id in neighbor_ids:
            neighbor = geo_entity_service.get_entity(client, neighbor_id)
            if not neighbor:
                continue
            if situation_text is not None and not _entity_mentioned_in_text(neighbor, situation_text):
                continue
            by_id[neighbor_id] = neighbor

    actor_type_filtered = [e for e in by_id.values() if e.get("entity_type") in ACTOR_ENTITY_TYPES]
    return actor_type_filtered


def _foundation_pack_control_leverage_entity_ids(foundation_pack: dict | None) -> set:
    """Phase D-5(Holistic Reasoning Architecture Review): Foundation Pack上の構造的な
    Control/Leverage関連Case Data（7_assets/9_vulnerabilities_and_constraints、
    いずれもgeo_assets/geo_dependenciesのentity_id参照）に登場する主体のentity_id集合を
    返す。Actor Intelligence（Core Profile/Decision Pattern/persistent_preference等）の
    充実度・情報量は一切参照しない——あくまでCase固有の構造データ上の事実のみを見る、
    entity_type別の重み付けもしない中立なシグナル（Person/Institutionを問わない。
    structure-firstな局面で人物を不必要に昇格させないための設計）。"""
    if not foundation_pack:
        return set()
    ids = set()
    for asset in foundation_pack.get("7_assets") or []:
        entity_id = asset.get("entity_id")
        if entity_id:
            ids.add(entity_id)
    for dependency in foundation_pack.get("9_vulnerabilities_and_constraints") or []:
        entity_id = dependency.get("entity_id")
        if entity_id:
            ids.add(entity_id)
    return ids


def score_actor_relevance(entity: dict, *, issue_linked_ids: set, relationship_backed_ids: set,
                           foundation_pack_control_ids: set | None = None,
                           runtime_resolved_ids: set | None = None) -> tuple:
    """決定的スコアリング（LLM不使用）。Actor Profile/Issue Positionの保有有無は
    原則スコアに含めない（Key Actor選定はIntelligence保有と独立させる）。既存の重み
    （issue_linked=3.0/veto_power等/high_leverage_relationship=1.5/foundation_pack_control=1.0）
    は一切変更しない。

    runtime_resolved_ids（ユーザー指示Phase D、Actor Runtime Activation）: Issue Linkが
    無くても、query/Case明示のActorやSource Pack由来のActorとして解決された新しい信号。
    Human-curated（issue_linked）より低い重みとし、既存のissue_linked/high_leverage_
    relationshipという2つの既存加点とは独立した第3の加点源として追加する
    （既存ロジックの改変ではなく、新しい候補母集団に対する新しい加点ルールの追加）。"""
    reasons = []
    score = 0.0

    if entity["entity_id"] in issue_linked_ids:
        reasons.append("issue_linked")
        score += 3.0
    elif runtime_resolved_ids and entity["entity_id"] in runtime_resolved_ids:
        reasons.append("situation_relevant")
        score += 2.0

    entity_type = entity.get("entity_type")
    if entity_type in ("government", "military", "person", "political_party"):
        reasons.append("veto_power")
        score += 2.0
    elif entity_type in ("security_service", "international_organization", "state_owned_enterprise",
                         "armed_group", "company", "faction", "country", "ministry"):
        score += 1.0

    if entity["entity_id"] in relationship_backed_ids and entity["entity_id"] not in issue_linked_ids:
        reasons.append("high_leverage_relationship")
        score += 1.5

    # Phase D-5: Foundation Pack上のAsset/Dependency（Control/Leverage関連の構造的
    # Case Data）にentity_idとして登場するかを追加シグナルとして加点する（issue_linked/
    # high_leverage_relationshipより弱い重み。Actor Intelligence Packのcandidate prior
    # 原則と同じ理由で、これも既存Actor selectionの正典を置き換えるものではなく、
    # 既存スコアへの1つの追加入力に留める）。
    if foundation_pack_control_ids and entity["entity_id"] in foundation_pack_control_ids:
        reasons.append("foundation_pack_control_or_leverage")
        score += 1.0

    if not reasons:
        reasons.append("entity_type_weight")

    return score, reasons


def select_key_actors(client, case_id: str | None, *, max_actors: int = None, reference_date: date = None,
                       profile_lookup_statuses: tuple = ("active",),
                       profile_retrieval_status: str | None = "available",
                       foundation_pack: dict | None = None,
                       extra_candidate_entity_ids: set | None = None,
                       situation_text: str | None = None) -> dict:
    """「誰が重要か」をActor Profileの有無と無関係に選び、選ばれたActorについてのみ
    Actor Intelligenceの充足度（sufficient/partial/insufficient）をその都度算出する。
    M9 Actor×Media Integration Pilot: profile_lookup_statuses/profile_retrieval_statusは
    既定で本番相当（activeかつretrieval_status=availableのみ）のまま変更しない。
    比較実験でのみ呼び出し元が明示的に上書きする（例: ("active","candidate")・None）。
    foundation_pack: Phase D-5。呼び出し元（geo_expert_engine）が既に構築済みの
    Foundation Packをそのまま渡す（新規DB取得・新規Retrievalフローは追加しない）。
    Noneの場合は従来通りFoundation Pack由来の追加シグナルなしでスコアリングする
    （既存呼び出し元との完全な後方互換）。
    extra_candidate_entity_ids: Actor Runtime Activation（Phase D）。case_id=Noneでも
    動作する。Issue Linkが有る場合はcurated（issue_linked）が優先され続け、Runtime候補は
    それを上書きしない（既存の重み・既存のTop-N選定ロジックは無変更）。"""
    limit = max_actors or default_max_actors()
    issue_ids = geo_retrieval_service.get_issue_ids_for_case(client, case_id) if case_id else []
    issue_linked_ids = {e["entity_id"] for e in geo_retrieval_service.get_relevant_entities(
        client, issue_ids, include_demo=True)}

    candidates = get_candidate_actor_entities(
        client, case_id, extra_candidate_entity_ids=extra_candidate_entity_ids,
        situation_text=situation_text)
    relationship_backed_ids = {e["entity_id"] for e in candidates} - issue_linked_ids
    foundation_pack_control_ids = _foundation_pack_control_leverage_entity_ids(foundation_pack)
    runtime_resolved_ids = set(extra_candidate_entity_ids or ())

    scored = []
    for entity in candidates:
        score, reasons = score_actor_relevance(
            entity, issue_linked_ids=issue_linked_ids, relationship_backed_ids=relationship_backed_ids,
            foundation_pack_control_ids=foundation_pack_control_ids,
            runtime_resolved_ids=runtime_resolved_ids)
        scored.append((score, entity, reasons))
    scored.sort(key=lambda t: (-t[0], t[1].get("canonical_name") or ""))
    top = scored[:limit]

    ref_date = reference_date or _resolve_reference_date(client, case_id)

    key_actors = []
    statuses = []
    for score, entity, reasons in top:
        profile_item = get_actor_profile_item_by_entity_id(
            client, entity["entity_id"], retrieval_status=profile_retrieval_status,
            statuses=profile_lookup_statuses)
        actor_status = _actor_intelligence_status_for(profile_item, issue_ids, ref_date)
        statuses.append(actor_status)
        external_ids = entity.get("external_ids") or {}
        key_actors.append({
            "entity_id": entity["entity_id"],
            "actor_intelligence_id": external_ids.get("actor_intelligence_id"),
            "canonical_name": entity.get("canonical_name"),
            "relevance_score": score,
            "selection_reasons": reasons,
            "actor_intelligence_available": profile_item is not None,
            "actor_intelligence_status": actor_status,
        })

    if not statuses:
        case_status = "insufficient"
    elif all(s == "sufficient" for s in statuses):
        case_status = "sufficient"
    elif all(s == "insufficient" for s in statuses):
        case_status = "insufficient"
    else:
        case_status = "partial"

    return {"actor_intelligence_status": case_status, "key_actors": key_actors, "reference_date": ref_date.isoformat()}


# ===================================================================
# 分析基準日（reference_date）とTemporal評価
# ===================================================================

def _resolve_reference_date(client, case_id: str | None) -> date:
    # case_id=None（Case未紐付けのAd-hoc実行、例: geo_ios Intake→generate-scenarios経由）
    # では、そもそも参照すべきgeo_cases行が無い——f"eq.{case_id}"がリテラル文字列
    # "eq.None"になり、PostgRESTがuuid列への不正な値として400 Bad Requestを返す不具合を
    # 2026-09-24発見・修正。呼び出し元の docstring が明記する「case_id=Noneでも動作する」
    # という契約を満たすため、クエリ自体を行わず既存のfallback（現在日時）へ直接進む。
    if not case_id:
        return datetime.now(timezone.utc).date()
    rows = client.select("geo_cases", {"select": "as_of_date", "case_id": f"eq.{case_id}", "limit": "1"})
    if rows and rows[0].get("as_of_date"):
        return _parse_date(rows[0]["as_of_date"])
    return datetime.now(timezone.utc).date()


def _parse_date(value) -> date:
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def compute_freshness(as_of, *, reference_date: date) -> dict:
    as_of_date = _parse_date(as_of)
    age_days = (reference_date - as_of_date).days
    if age_days <= _FRESHNESS_CURRENT_DAYS:
        status = "current"
    elif age_days <= _FRESHNESS_AGING_DAYS:
        status = "aging"
    else:
        status = "stale"
    return {"status": status, "age_days": age_days}


def _within_reference_date(valid_from, valid_to, reference_date: date) -> bool:
    """reference_dateより未来のvalid_from、reference_dateより過去のvalid_toを持つものは
    Packに含めない（分析基準日より後の情報を先読みして使わないため）。"""
    if valid_from and _parse_date(valid_from) > reference_date:
        return False
    if valid_to and _parse_date(valid_to) < reference_date:
        return False
    return True


def _issue_position_for(profile_content: dict, issue_ids: list, reference_date: date) -> dict | None:
    for position in profile_content.get("issue_positions") or []:
        if position.get("issue_id") not in issue_ids:
            continue
        if _within_reference_date(position.get("valid_from"), position.get("valid_to"), reference_date):
            return position
    return None


def _actor_intelligence_status_for(profile_item: dict | None, issue_ids: list, reference_date: date) -> str:
    """Profileが存在するだけではsufficientにしない。Temporal/Issue Relevanceでフィルタした
    「その場で有効なデータ」の有無を見て判定する。"""
    if profile_item is None:
        return "insufficient"
    content = profile_item.get("structured_content") or {}

    has_core_profile = bool(content.get("core_profile"))
    issue_position = _issue_position_for(content, issue_ids, reference_date)
    current_posture = content.get("current_posture")
    posture_valid = False
    if current_posture and current_posture.get("as_of"):
        posture_as_of = _parse_date(current_posture["as_of"])
        posture_valid = posture_as_of <= reference_date

    if has_core_profile and issue_position and posture_valid:
        return "sufficient"
    if has_core_profile or issue_position or posture_valid:
        return "partial"
    return "insufficient"


# ===================================================================
# Actor Intelligence Pack生成（Phase AI-2/AI-3）
# ===================================================================

def _relevant_precedents_for_entity(client, entity_id: str, *, retrieval_status: str = None,
                                     statuses: tuple = ("active",)) -> list:
    """M9/Expert AI Depth Upgrade: statusesは既定("active",)のまま後方互換
    （本番相当）。比較実験でのみ呼び出し元が("active","candidate")等へ上書きする
    （10 Actor Historical Intelligence Buildのprecedentは全件status=candidateのため、
    既定のままだとテストモードでもhistorical_episode_idsが常に空になってしまう）。"""
    precedents = []
    for status in statuses:
        precedents += geo_knowledge_service.list_knowledge_items(
            client, knowledge_type="precedent", status=status,
            retrieval_status=retrieval_status, include_demo=True, limit=200)
    matched = [
        p for p in precedents
        if (p.get("structured_content") or {}).get("primary_actor_entity_id") == entity_id
    ]
    return matched[:ACTOR_PACK_PRECEDENT_TOP_K]


def _directional_relationship_perceptions_for(content: dict, *, observer_entity_id: str,
                                               pair_entity_ids: set, reference_date: date) -> list:
    """Phase G(Directional Relationship Intelligence、ユーザー指示): observerのactor_profile
    structured_content内のdirectional_relationship_perceptions[]から、(a)target_entity_idが
    このPackのKey Actor同士（pair_entity_ids）に含まれる、(b)自己参照でない、(c)reference_date
    時点でvalidity範囲内、の3条件を満たすものだけを返す（Active/Candidateの別はKnowledge Item
    単位で既にget_actor_profile_item_by_entity_id側のstatuses絞り込みが担うため、ここでは
    配列要素ごとの追加status判定はしない）。Issueベースの絞り込みは、既存core_profile.
    core_objectives等と同様に行わない（issue_scopeは参照材料としてPromptへそのまま渡し、
    局面との関連性判断はLLM側に委ねる——新規Issue紐付けテーブルは追加しない）。"""
    items = content.get("directional_relationship_perceptions") or []
    result = []
    for item in items:
        target_entity_id = item.get("target_entity_id")
        if not target_entity_id or target_entity_id == observer_entity_id:
            continue
        if target_entity_id not in pair_entity_ids:
            continue
        if not _within_reference_date(item.get("valid_from"), item.get("valid_to"), reference_date):
            continue
        result.append(item)
    return result


def _relevant_relationships_between(client, entity_ids: list, reference_date: date) -> list:
    seen = set()
    relationships = []
    for entity_id in entity_ids:
        for rel in geo_relationship_service.list_relationships_for_entity(client, entity_id, include_demo=True):
            if rel["relationship_id"] in seen:
                continue
            if rel.get("from_entity_id") in entity_ids and rel.get("to_entity_id") in entity_ids:
                if _within_reference_date(rel.get("valid_from"), rel.get("valid_to"), reference_date):
                    seen.add(rel["relationship_id"])
                    relationships.append(rel)
    return relationships[:ACTOR_PACK_RELATIONSHIP_TOP_K]


def _trim_to_char_budget(actor_packs: list, char_budget: int) -> list:
    """build_case_contextと同じ「リスト末尾から削る」方式。各ActorのPrecedent/Relationship
    末尾から順に削り、それでも超過する場合はActor自体を末尾から削る。"""
    def _size(packs) -> int:
        return len(json.dumps(packs, ensure_ascii=False))

    trimmed = [dict(p) for p in actor_packs]
    while _size(trimmed) > char_budget and trimmed:
        shrunk = False
        for p in reversed(trimmed):
            if p.get("relevant_precedents"):
                p["relevant_precedents"] = p["relevant_precedents"][:-1]
                shrunk = True
                break
            if p.get("relevant_relationships"):
                p["relevant_relationships"] = p["relevant_relationships"][:-1]
                shrunk = True
                break
        if shrunk:
            continue
        if len(trimmed) > 1:
            trimmed.pop()
        else:
            break
    return trimmed


def build_actor_intelligence_pack(client, case_id: str | None, *, max_actors: int = None,
                                   profile_lookup_statuses: tuple = ("active",),
                                   profile_retrieval_status: str | None = "available",
                                   foundation_pack: dict | None = None,
                                   extra_candidate_entity_ids: set | None = None,
                                   situation_text: str | None = None) -> dict:
    """Situationにスコープされた、token予算内のActor Intelligence Packを組み立てる。
    Evidence本文は含めずID参照のみ（token対策）。selection_reasons/relevance_scoreを含む。
    M9 Actor×Media Integration Pilot: profile_lookup_statuses/profile_retrieval_statusは
    既定で本番相当のまま変更しない。比較実験でのみ呼び出し元が明示的に上書きする。
    foundation_pack: Phase D-5、select_key_actorsへそのまま渡す（後方互換のためNone既定）。
    extra_candidate_entity_ids: Actor Runtime Activation（Phase D、ユーザー指示）。
    case_id=NoneでもRuntime候補だけでActor Intelligenceが機能する。既存のRelationship/
    Directional Relationship Perception取得（下記_relevant_relationships_between/
    _directional_relationship_perceptions_for）はentity_ids（=key_actors）から導出される
    ため、Runtime解決されたActorについても自動的に対象になる（Phase E、追加コード不要）。
    situation_text: Phase 6A-2（Relationship Relevance Precision）、get_candidate_actor_
    entitiesへそのまま伝播する。Noneの場合は既存の無条件1-hop展開を維持する。"""
    issue_ids = geo_retrieval_service.get_issue_ids_for_case(client, case_id) if case_id else []
    selection = select_key_actors(client, case_id, max_actors=max_actors,
                                   profile_lookup_statuses=profile_lookup_statuses,
                                   profile_retrieval_status=profile_retrieval_status,
                                   foundation_pack=foundation_pack,
                                   situation_text=situation_text,
                                   extra_candidate_entity_ids=extra_candidate_entity_ids)
    reference_date = _parse_date(selection["reference_date"])

    entity_ids = [ka["entity_id"] for ka in selection["key_actors"]]
    known_ids = set()
    # Tier1(Eguchi Expert AI Final v1.0対応): directional_perceptions[]がA→Bの帰属付き
    # 認識を根拠付きで生成できるよう、relevant_relationshipsの相手Actor名を解決するための
    # 参照表（key_actor同士の関係のみを対象とするため、相手も必ずこの中に存在する）。
    actor_name_by_entity_id = {ka["entity_id"]: ka.get("canonical_name") for ka in selection["key_actors"]}

    actor_packs = []
    for key_actor in selection["key_actors"]:
        entity_id = key_actor["entity_id"]
        profile_item = get_actor_profile_item_by_entity_id(
            client, entity_id, retrieval_status=profile_retrieval_status,
            statuses=profile_lookup_statuses)
        content = (profile_item.get("structured_content") or {}) if profile_item else {}

        issue_position = _issue_position_for(content, issue_ids, reference_date)
        current_posture = content.get("current_posture")
        freshness = None
        if current_posture and current_posture.get("as_of"):
            freshness = compute_freshness(current_posture["as_of"], reference_date=reference_date)

        decision_patterns = (content.get("decision_patterns") or [])[:ACTOR_PACK_DECISION_PATTERN_TOP_K]
        precedents = _relevant_precedents_for_entity(
            client, entity_id, retrieval_status=profile_retrieval_status, statuses=profile_lookup_statuses)
        relationships = _relevant_relationships_between(client, entity_ids, reference_date)
        directional_relationship_perceptions = _directional_relationship_perceptions_for(
            content, observer_entity_id=entity_id, pair_entity_ids=set(entity_ids),
            reference_date=reference_date)

        for dp in decision_patterns:
            known_ids.update(dp.get("supporting_precedent_ids") or [])
            known_ids.update(dp.get("supporting_evidence_ids") or [])
            known_ids.update(dp.get("counter_evidence_ids") or [])
        for p in precedents:
            known_ids.add(p.get("knowledge_item_id"))
        if issue_position:
            known_ids.update(issue_position.get("evidence_ids") or [])
        if current_posture:
            known_ids.update(current_posture.get("evidence_ids") or [])

        # Expert AI Depth & Grounding Upgrade（P0-1）: Runtime Provenance用に決定的に
        # 導出する（LLMの自己申告に頼らない）。
        pattern_ids = [dp.get("pattern_id") for dp in decision_patterns if dp.get("pattern_id")]
        exception_episode_ids = []
        for dp in decision_patterns:
            if dp.get("pattern_role") == "exception":
                exception_episode_ids += (dp.get("supporting_precedent_ids") or [])
        historical_episode_ids = [p.get("knowledge_item_id") for p in precedents if p.get("knowledge_item_id")]
        if profile_item is None:
            profile_source = "none_available"
        elif profile_item.get("status") == "active":
            profile_source = "actor_intelligence_active"
        else:
            profile_source = "actor_intelligence_candidate"

        actor_packs.append({
            "entity_id": entity_id,
            "actor_intelligence_id": key_actor.get("actor_intelligence_id"),
            "canonical_name": key_actor.get("canonical_name"),
            "selection_reasons": key_actor.get("selection_reasons"),
            "relevance_score": key_actor.get("relevance_score"),
            "actor_intelligence_status": key_actor.get("actor_intelligence_status"),
            "profile_source": profile_source,
            "pattern_ids": pattern_ids,
            "exception_episode_ids": exception_episode_ids,
            "historical_episode_ids": historical_episode_ids,
            "core_profile": content.get("core_profile"),
            # Session2 Reasoning Upgrade（Phase2A）: Expert Packからread可能にするのみ
            # （自動候補生成・昇格はPhase6）。
            "persistent_preference": content.get("persistent_preference"),
            "strategic_communication_pattern": content.get("strategic_communication_pattern"),
            "decision_patterns": decision_patterns,
            "current_posture": ({**current_posture, "freshness": freshness} if current_posture else None),
            "issue_position": issue_position,
            "relevant_precedents": [
                {"knowledge_item_id": p.get("knowledge_item_id"), "title": p.get("title")} for p in precedents
            ],
            # Tier1(Eguchi Expert AI Final v1.0対応): このActor(entity_id)を含む関係のみに絞り、
            # direction（この主体からみて相手が起点(from)か終点(to)か）とcounterpart（相手の
            # entity_id/名前）を明示する。以前はentity_ids内の全関係（この主体を含まないものも）が
            # 一律に付与され、方向・相手も欠落していたため、DirectionalPerceptionの根拠として
            # 使えなかった（directional_perceptionsは案件内で新規生成、ここはその入力材料）。
            "relevant_relationships": [
                {
                    "relationship_id": r.get("relationship_id"),
                    "relationship_domain": r.get("relationship_domain"),
                    "relationship_type": r.get("relationship_type"),
                    "dependency_direction": r.get("dependency_direction"),
                    "leverage_direction": r.get("leverage_direction"),
                    "confidence": r.get("confidence"),
                    "direction": "from" if r.get("from_entity_id") == entity_id else "to",
                    "counterpart_entity_id": (
                        r.get("to_entity_id") if r.get("from_entity_id") == entity_id else r.get("from_entity_id")
                    ),
                    "counterpart_name": actor_name_by_entity_id.get(
                        r.get("to_entity_id") if r.get("from_entity_id") == entity_id else r.get("from_entity_id")
                    ),
                }
                for r in relationships
                if entity_id in (r.get("from_entity_id"), r.get("to_entity_id"))
            ],
            # Phase G(Directional Relationship Intelligence、ユーザー指示): observer=この
            # Actor自身のtarget(相手)への主観的認識。既存geo_relationships（客観的・持続的な
            # 関係属性）とは別データ源（geo_knowledge_items knowledge_type=actor_profileの
            # structured_content）であり、混同しないよう別キーにする。
            "directional_relationship_perceptions": [
                {**item, "target_canonical_name": actor_name_by_entity_id.get(item.get("target_entity_id"))}
                for item in directional_relationship_perceptions
            ],
        })

    actor_packs = _trim_to_char_budget(actor_packs, ACTOR_PACK_CHAR_BUDGET)
    known_ids.discard(None)

    return {
        "actor_intelligence_status": selection["actor_intelligence_status"],
        "reference_date": selection["reference_date"],
        "key_actors": actor_packs,
        "known_ids": known_ids,
    }
