"""Expert AI Phase R1 Chunk3: Institution Intelligence（MVP）。

Person Actor（geo_actor_intelligence_service.py）では説明できない制度的意思決定主体
（BIS/US Congress/NATO等）を、正しく「制度」として表現するための最小構造。

意図的にPerson Actorの仕組みを模倣し過ぎない:
- 巨大なInstitution DB・候補自動生成・専用UIは作らない（MVPスコープ外）。
- Person Actorの`select_key_actors`（Case-scoped relevance-scoring）に相当する複雑な
  選定アルゴリズムは持たない。呼び出し元（geo_expert_engine.py）がFoundation Packで
  既に解決済みのEntity一覧から、entity_type（government/ministry/military/
  security_service/international_organization/state_owned_enterprise）が制度的な
  ものだけを渡す、というシンプルな設計にする。
- Personに固有のフィールド（persistent_preference/worldview/red_line等、「志向」
  「性格」に相当するもの）は一切扱わない。「BISの思想」「NATOの性格」のような擬人化を
  防ぐことが本サービスの目的そのもの。

geo_knowledge_items（knowledge_type="institution_profile"）への格納・entity_id起点の
取得パターンは、geo_actor_intelligence_service.get_actor_profile_item_by_entity_idと
意図的に同型にする（新しいストレージ方式を作らない）。
"""
from __future__ import annotations

import hashlib
import json

import geo_entity_service
import geo_knowledge_service

# geo_entities.entity_type（sql/2026-08-04_geo_knowledge_core.sql）のうち、Person/
# Company/GeographyではなくInstitutional Actorとして扱う値。
INSTITUTIONAL_ENTITY_TYPES = {
    "government", "ministry", "military", "security_service",
    "international_organization", "state_owned_enterprise",
}

# Institution Packのtoken対策（Actor Packのchar budgetと同じ思想、最小構造のためActorより
# 小さい上限にする）。
INSTITUTION_PACK_CHAR_BUDGET = 3000


def get_institution_profile_item_by_entity_id(
    client, entity_id: str, *, retrieval_status: str = None, statuses: tuple = ("active",),
) -> dict | None:
    """knowledge_type=institution_profileの中からentity_id一致を1件返す
    （geo_actor_intelligence_service.get_actor_profile_item_by_entity_idと同型）。
    statusesの先頭から順に見つかった最初の1件を返す（activeを優先）。"""
    if not entity_id:
        return None
    for status in statuses:
        candidates = geo_knowledge_service.list_knowledge_items(
            client, knowledge_type="institution_profile", status=status,
            retrieval_status=retrieval_status, include_demo=True, limit=200)
        for item in candidates:
            content = item.get("structured_content") or {}
            if content.get("entity_id") == entity_id:
                return item
    return None


def institutional_entities_from_foundation_pack(foundation_pack: dict | None) -> list:
    """foundation_pack["2_relevant_entities"]（既にCase Contextとして解決済みのEntity
    一覧）から、entity_type=INSTITUTIONAL_ENTITY_TYPESのものだけを抽出する。
    Person Actorのような専用selectionアルゴリズムを新設せず、既に解決済みの情報を
    再利用する（MVPの意図的な単純化）。"""
    if not foundation_pack:
        return []
    entities = foundation_pack.get("2_relevant_entities") or []
    return [e for e in entities if e.get("entity_type") in INSTITUTIONAL_ENTITY_TYPES]


def _trim_to_char_budget(institution_packs: list, budget: int) -> list:
    """Actor Packと同じ考え方: JSON文字列化した総文字数がbudgetを超える場合、
    後方（relevance/優先度の低いもの）から間引く。呼び出し順＝優先順とする。"""
    import json
    trimmed = list(institution_packs)
    while trimmed and len(json.dumps(trimmed, ensure_ascii=False, default=str)) > budget:
        if len(trimmed) > 1:
            trimmed.pop()
        else:
            break
    return trimmed


def build_institution_intelligence_pack(
    client, institutional_entities: list, *,
    profile_lookup_statuses: tuple = ("active",),
    profile_retrieval_status: str | None = "available",
) -> dict:
    """token予算内のInstitution Intelligence Packを組み立てる。institutional_entitiesは
    呼び出し元が渡す{entity_id, canonical_name, entity_type}のリスト
    （institutional_entities_from_foundation_packの戻り値をそのまま渡すことを想定）。
    Actor Intelligence Packと同じ思想でprofile_sourceを決定的に付与する
    （none_available/institution_intelligence_active/institution_intelligence_candidate）。
    """
    known_ids = set()
    institution_packs = []
    for entity in institutional_entities:
        entity_id = entity.get("entity_id")
        if not entity_id:
            continue
        profile_item = get_institution_profile_item_by_entity_id(
            client, entity_id, retrieval_status=profile_retrieval_status,
            statuses=profile_lookup_statuses)
        content = (profile_item.get("structured_content") or {}) if profile_item else {}

        if profile_item is None:
            profile_source = "none_available"
        elif profile_item.get("status") == "active":
            profile_source = "institution_intelligence_active"
        else:
            profile_source = "institution_intelligence_candidate"

        known_ids.update(content.get("evidence_ids") or [])
        for dp in content.get("historical_decision_patterns") or []:
            known_ids.update(dp.get("supporting_precedent_ids") or [])
            known_ids.update(dp.get("supporting_evidence_ids") or [])
            known_ids.update(dp.get("counter_evidence_ids") or [])
        for stakeholder in content.get("internal_stakeholders") or []:
            known_ids.update(stakeholder.get("evidence_ids") or [])
        for dep in content.get("external_dependencies") or []:
            known_ids.update(dep.get("evidence_ids") or [])
        for exc in content.get("known_exceptions") or []:
            known_ids.update(exc.get("evidence_ids") or [])

        institution_packs.append({
            "entity_id": entity_id,
            "canonical_name": entity.get("canonical_name"),
            "entity_type": entity.get("entity_type"),
            "profile_source": profile_source,
            "mandate": content.get("mandate"),
            "decision_authority": content.get("decision_authority"),
            "constraints": content.get("constraints"),
            "internal_stakeholders": content.get("internal_stakeholders"),
            "external_dependencies": content.get("external_dependencies"),
            "historical_decision_patterns": content.get("historical_decision_patterns"),
            "known_exceptions": content.get("known_exceptions"),
        })

    institution_packs = _trim_to_char_budget(institution_packs, INSTITUTION_PACK_CHAR_BUDGET)
    known_ids.discard(None)

    return {"institutions": institution_packs, "known_ids": known_ids}


# ===================================================================
# Seed（手動投入のみ、MVP）: geo_actor_intelligence_service.seed_actor_profileと
# 同型のidempotent Seed。候補自動生成・大規模DB投入は行わない——実装者/レビュアーが
# 個別に確認した最小限のInstitution Profileだけを手動で投入する運用を想定する。
# ===================================================================

def get_or_create_institution_entity(client, *, institution_id: str, canonical_name: str,
                                      entity_type: str, actor_id: str = "institution_intelligence_seed") -> dict:
    """geo_actor_intelligence_service.get_or_create_actor_entityと同型。external_ids.
    institution_idで既存Entityへの紐付けを行い、無ければ新規作成する。"""
    if entity_type not in INSTITUTIONAL_ENTITY_TYPES:
        raise ValueError(
            f"entity_type={entity_type}はInstitutionとして扱えません"
            f"（許容: {sorted(INSTITUTIONAL_ENTITY_TYPES)}）")
    existing = None
    for entity in geo_entity_service.list_entities(client, entity_type=entity_type, include_demo=True, limit=1000):
        if (entity.get("external_ids") or {}).get("institution_id") == institution_id:
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
    if current_external_ids.get("institution_id") != institution_id:
        current_external_ids["institution_id"] = institution_id
        client.update("geo_entities", {"entity_id": f"eq.{existing['entity_id']}"},
                      {"external_ids": current_external_ids})
        existing = dict(existing)
        existing["external_ids"] = current_external_ids
    return existing


def _find_pending_institution_profile_item(client, entity_id: str) -> dict | None:
    for status in ("candidate", "in_review"):
        for item in geo_knowledge_service.list_knowledge_items(
                client, knowledge_type="institution_profile", status=status, include_demo=True, limit=200):
            content = item.get("structured_content") or {}
            if content.get("entity_id") == entity_id:
                return item
    return None


def _canonicalize_fingerprint(structured_content: dict) -> str:
    normalized = json.dumps(structured_content, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def seed_institution_profile(
    client, *, institution_id: str, canonical_name: str, entity_type: str,
    mandate: str, decision_authority: dict, constraints: list = None,
    internal_stakeholders: list = None, external_dependencies: list = None,
    historical_decision_patterns: list = None, known_exceptions: list = None,
    coverage_notes: str = None, created_by: str = "institution_intelligence_seed_script",
    activate: bool = False, reviewer_id: str = "institution_intelligence_seed_script",
) -> dict:
    """Institution Profileをcandidateとして作成/更新する（内容差分ベースでidempotent、
    geo_actor_intelligence_service.seed_actor_profileと同じ設計）。擬人化を防ぐため、
    persistent_preference等のPerson固有フィールドはそもそも受け付けない。"""
    entity = get_or_create_institution_entity(
        client, institution_id=institution_id, canonical_name=canonical_name,
        entity_type=entity_type, actor_id=created_by)
    entity_id = entity["entity_id"]

    structured_content = {
        "entity_id": entity_id,
        "mandate": mandate,
        "decision_authority": decision_authority,
        "constraints": constraints or [],
        "internal_stakeholders": internal_stakeholders or [],
        "external_dependencies": external_dependencies or [],
        "historical_decision_patterns": historical_decision_patterns or [],
        "known_exceptions": known_exceptions or [],
        "evidence_ids": [],
        "coverage_notes": coverage_notes,
    }
    new_fingerprint = _canonicalize_fingerprint(structured_content)

    active_item = get_institution_profile_item_by_entity_id(client, entity_id)
    pending_item = _find_pending_institution_profile_item(client, entity_id)

    if pending_item is not None:
        existing_fingerprint = _canonicalize_fingerprint(pending_item.get("structured_content") or {})
        if existing_fingerprint == new_fingerprint:
            item = pending_item
        else:
            client.update("geo_knowledge_items", {"knowledge_item_id": f"eq.{pending_item['knowledge_item_id']}"},
                          {"structured_content": structured_content})
            item = geo_knowledge_service.get_knowledge_item(client, pending_item["knowledge_item_id"])
    elif active_item is not None:
        existing_fingerprint = _canonicalize_fingerprint(active_item.get("structured_content") or {})
        if existing_fingerprint == new_fingerprint:
            item = active_item
        else:
            item = geo_knowledge_service.create_knowledge_item(
                client, knowledge_type="institution_profile", title=canonical_name,
                structured_content=structured_content, information_class="internal_hypothesis",
                created_by=created_by, status="candidate", supersedes_id=active_item["knowledge_item_id"],
                origin="case_candidate_extraction",
            )
    else:
        item = geo_knowledge_service.create_knowledge_item(
            client, knowledge_type="institution_profile", title=canonical_name,
            structured_content=structured_content, information_class="internal_hypothesis",
            created_by=created_by, status="candidate", origin="case_candidate_extraction",
        )

    if activate and item.get("status") in ("candidate", "in_review"):
        if item["status"] == "candidate":
            item = geo_knowledge_service.review_knowledge_item(
                client, knowledge_item_id=item["knowledge_item_id"], reviewer_id=reviewer_id,
                reviewer_role="knowledge_steward", review_decision="start_review",
                review_comment="Institution Intelligence Seed: review開始", evidence_adequacy="sufficient",
                applicability_checked=True,
            )["knowledge_item"]
        item = geo_knowledge_service.review_knowledge_item(
            client, knowledge_item_id=item["knowledge_item_id"], reviewer_id=reviewer_id,
            reviewer_role="knowledge_steward", review_decision="approve_active",
            review_comment="Institution Intelligence Seed: --activate指定によりactive化",
            evidence_adequacy="sufficient", applicability_checked=True,
        )["knowledge_item"]

    return item
