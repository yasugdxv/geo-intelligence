"""Actor Intelligence Layer（Phase AI-1〜3）geo_actor_intelligence_service.pyのテスト。

Key Actor SelectionがActor Profileの保有有無から独立していること、Entity Type Filter、
1-hop Relationship展開の上限、Temporal Filter/Freshness、Seedのidempotent化（内容差分
ベースのno-op/candidate revision/--activate）を中心に検証する。
"""
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_actor_intelligence_service as ai  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _base_client(**extra_tables) -> FakeSupabaseClient:
    tables = {
        "geo_cases": [{"case_id": "case-1", "case_title": "t", "original_question": "q",
                       "as_of_date": "2026-08-15"}],
        "geo_case_issues": [{"case_id": "case-1", "issue_id": "issue-1"}],
        "geo_entity_issues": [{"issue_id": "issue-1", "entity_id": "trump-id"}],
        "geo_entities": [
            {"entity_id": "trump-id", "entity_type": "person", "canonical_name": "Donald Trump",
             "external_ids": {}, "is_demo": False},
            {"entity_id": "hormuz-id", "entity_type": "strait", "canonical_name": "Strait of Hormuz",
             "external_ids": {}, "is_demo": False},
            {"entity_id": "xi-id", "entity_type": "person", "canonical_name": "Xi Jinping",
             "external_ids": {}, "is_demo": False},
        ],
        "geo_relationships": [],
        "geo_knowledge_items": [],
        "geo_knowledge_reviews": [],
        "geo_audit_logs": [],
    }
    tables.update(extra_tables)
    return FakeSupabaseClient(tables)


# ── Actor Identity（get_or_create_actor_entity） ──────────────────────────

def test_get_or_create_actor_entity_creates_new_entity():
    client = _base_client(geo_entities=[])
    entity = ai.get_or_create_actor_entity(
        client, actor_intelligence_id="donald_trump", canonical_name="Donald Trump")
    assert entity["external_ids"]["actor_intelligence_id"] == "donald_trump"
    assert len(client.tables["geo_entities"]) == 1


def test_get_or_create_actor_entity_is_idempotent_by_actor_intelligence_id():
    client = _base_client(geo_entities=[])
    first = ai.get_or_create_actor_entity(
        client, actor_intelligence_id="donald_trump", canonical_name="Donald Trump")
    second = ai.get_or_create_actor_entity(
        client, actor_intelligence_id="donald_trump", canonical_name="Donald Trump")
    assert first["entity_id"] == second["entity_id"]
    assert len(client.tables["geo_entities"]) == 1


def test_get_or_create_actor_entity_resolves_by_canonical_name_when_id_missing():
    client = _base_client()  # trump-idはexternal_ids={}のまま存在
    entity = ai.get_or_create_actor_entity(
        client, actor_intelligence_id="donald_trump", canonical_name="Donald Trump")
    assert entity["entity_id"] == "trump-id"
    assert len(client.tables["geo_entities"]) == 3  # 新規作成されない


def test_get_or_create_actor_entity_merges_external_ids_without_overwriting():
    client = _base_client(geo_entities=[
        {"entity_id": "trump-id", "entity_type": "person", "canonical_name": "Donald Trump",
         "external_ids": {"wikidata_id": "Q22686"}, "is_demo": False},
    ])
    entity = ai.get_or_create_actor_entity(
        client, actor_intelligence_id="donald_trump", canonical_name="Donald Trump")
    assert entity["external_ids"]["wikidata_id"] == "Q22686"
    assert entity["external_ids"]["actor_intelligence_id"] == "donald_trump"


# ── Seedのidempotent化 ────────────────────────────────────────────────────

def _seed_kwargs(**overrides):
    base = dict(
        actor_intelligence_id="donald_trump", canonical_name="Donald Trump",
        core_profile={"core_objectives_summary": ["再選"], "core_interests_summary": ["支持基盤"]},
    )
    base.update(overrides)
    return base


def test_seed_actor_profile_creates_candidate_by_default():
    client = _base_client()
    item = ai.seed_actor_profile(client, **_seed_kwargs())
    assert item["status"] == "candidate"
    assert len(client.tables["geo_knowledge_items"]) == 1


def test_seed_actor_profile_is_noop_when_content_unchanged():
    client = _base_client()
    first = ai.seed_actor_profile(client, **_seed_kwargs())
    second = ai.seed_actor_profile(client, **_seed_kwargs())
    assert first["knowledge_item_id"] == second["knowledge_item_id"]
    assert len(client.tables["geo_knowledge_items"]) == 1


def test_seed_actor_profile_updates_existing_candidate_when_content_changes():
    client = _base_client()
    first = ai.seed_actor_profile(client, **_seed_kwargs())
    changed = ai.seed_actor_profile(client, **_seed_kwargs(coverage_notes="更新済み"))
    assert changed["knowledge_item_id"] == first["knowledge_item_id"]
    assert len(client.tables["geo_knowledge_items"]) == 1
    assert changed["structured_content"]["coverage_notes"] == "更新済み"


def test_seed_actor_profile_activate_reuses_existing_candidate():
    client = _base_client()
    ai.seed_actor_profile(client, **_seed_kwargs())
    activated = ai.seed_actor_profile(client, **_seed_kwargs(activate=True))
    assert activated["status"] == "active"
    assert len(client.tables["geo_knowledge_items"]) == 1


def test_seed_actor_profile_creates_new_candidate_version_when_active_content_changes():
    client = _base_client()
    active = ai.seed_actor_profile(client, **_seed_kwargs(activate=True))
    assert active["status"] == "active"
    revised = ai.seed_actor_profile(client, **_seed_kwargs(coverage_notes="改訂版"))
    assert revised["status"] == "candidate"
    assert revised["knowledge_item_id"] != active["knowledge_item_id"]
    assert revised["supersedes_id"] == active["knowledge_item_id"]
    assert len(client.tables["geo_knowledge_items"]) == 2


def test_seed_actor_profile_noop_when_active_content_unchanged():
    client = _base_client()
    active = ai.seed_actor_profile(client, **_seed_kwargs(activate=True))
    same = ai.seed_actor_profile(client, **_seed_kwargs())
    assert same["knowledge_item_id"] == active["knowledge_item_id"]
    assert len(client.tables["geo_knowledge_items"]) == 1


# ── get_actor_profile_item_by_entity_id ───────────────────────────────────

def test_get_actor_profile_item_by_entity_id_only_returns_active():
    client = _base_client()
    ai.seed_actor_profile(client, **_seed_kwargs())  # candidateのまま
    assert ai.get_actor_profile_item_by_entity_id(client, "trump-id") is None
    ai.seed_actor_profile(client, **_seed_kwargs(activate=True))
    found = ai.get_actor_profile_item_by_entity_id(client, "trump-id")
    assert found is not None
    assert found["structured_content"]["entity_id"] == "trump-id"


# ── P0-2 Cleanup: retrieval_statusフィルタ（他のReusable Knowledge経路との整合） ──────

def test_get_actor_profile_item_by_entity_id_retrieval_status_default_is_backward_compatible():
    # retrieval_status未指定（デフォルトNone）では、実運用でactivate時に自動付与される
    # retrieval_status="available"の値によらず、従来通り取得できる（後方互換）。
    client = _base_client()
    ai.seed_actor_profile(client, **_seed_kwargs(activate=True))
    assert ai.get_actor_profile_item_by_entity_id(client, "trump-id") is not None


def test_get_actor_profile_item_by_entity_id_excludes_restricted_when_available_required():
    client = _base_client()
    ai.seed_actor_profile(client, **_seed_kwargs(activate=True))
    # 運用者がAI参照を止めたいケース（retrieval_status=restricted）を模擬
    client.tables["geo_knowledge_items"][0]["retrieval_status"] = "restricted"

    assert ai.get_actor_profile_item_by_entity_id(
        client, "trump-id", retrieval_status="available") is None
    # デフォルト（未指定）では引き続き取得できる（後方互換の再確認）
    assert ai.get_actor_profile_item_by_entity_id(client, "trump-id") is not None


def test_relevant_precedents_for_entity_excludes_restricted_when_available_required():
    client = _base_client(geo_knowledge_items=[
        {"knowledge_item_id": "prec-1", "knowledge_type": "precedent", "status": "active",
         "retrieval_status": "restricted", "is_demo": False,
         "structured_content": {"primary_actor_entity_id": "trump-id"}},
    ])
    assert ai._relevant_precedents_for_entity(client, "trump-id") == [
        client.tables["geo_knowledge_items"][0]]
    assert ai._relevant_precedents_for_entity(
        client, "trump-id", retrieval_status="available") == []


# ── Actor Entity Type Filter / Candidate Actor取得 ────────────────────────

def test_get_candidate_actor_entities_excludes_non_actor_entity_types():
    client = _base_client()
    client.tables["geo_entity_issues"].append({"issue_id": "issue-1", "entity_id": "hormuz-id"})
    candidates = ai.get_candidate_actor_entities(client, "case-1")
    ids = {e["entity_id"] for e in candidates}
    assert "trump-id" in ids
    assert "hormuz-id" not in ids


def test_get_candidate_actor_entities_expands_1hop_relationship():
    client = _base_client()
    client.tables["geo_relationships"].append({
        "relationship_id": "rel-1", "from_entity_id": "trump-id", "to_entity_id": "xi-id",
        "relationship_domain": "diplomacy", "relationship_type": "rivalry",
        "leverage_direction": "trump_over_xi", "confidence": "中", "is_demo": False,
    })
    candidates = ai.get_candidate_actor_entities(client, "case-1")
    ids = {e["entity_id"] for e in candidates}
    assert "xi-id" in ids  # issue-1に直接タグ付けされていないが1-hopで拾われる


def test_candidate_relationship_expansion_respects_top_k(monkeypatch):
    monkeypatch.setattr(ai, "CANDIDATE_RELATIONSHIP_EXPANSION_TOP_K", 1)
    client = _base_client()
    client.tables["geo_entities"].append(
        {"entity_id": "putin-id", "entity_type": "person", "canonical_name": "Vladimir Putin",
         "external_ids": {}, "is_demo": False})
    client.tables["geo_relationships"].extend([
        {"relationship_id": "rel-1", "from_entity_id": "trump-id", "to_entity_id": "xi-id",
         "relationship_domain": "diplomacy", "relationship_type": "rivalry",
         "leverage_direction": "trump_over_xi", "confidence": "高", "is_demo": False},
        {"relationship_id": "rel-2", "from_entity_id": "trump-id", "to_entity_id": "putin-id",
         "relationship_domain": "diplomacy", "relationship_type": "negotiation",
         "leverage_direction": None, "confidence": "低", "is_demo": False},
    ])
    candidates = ai.get_candidate_actor_entities(client, "case-1")
    ids = {e["entity_id"] for e in candidates}
    # top_k=1なので、leverage_direction+confidenceが高いrel-1側（xi-id）のみ拾われる
    assert "xi-id" in ids
    assert "putin-id" not in ids


# ── Key Actor Selection: Profileの有無から独立していること ────────────────

def test_select_key_actors_independent_of_profile_existence():
    client = _base_client()
    client.tables["geo_entity_issues"].append({"issue_id": "issue-1", "entity_id": "xi-id"})
    ai.seed_actor_profile(client, **_seed_kwargs(activate=True))  # trump-idのみProfileあり

    result = ai.select_key_actors(client, "case-1", max_actors=5)
    by_entity = {ka["entity_id"]: ka for ka in result["key_actors"]}
    assert "trump-id" in by_entity and "xi-id" in by_entity
    # 同じissue_linkedかつ同じentity_type(person)なら、Profile有無に関わらずスコアは同じ
    assert by_entity["trump-id"]["relevance_score"] == by_entity["xi-id"]["relevance_score"]
    assert by_entity["trump-id"]["actor_intelligence_available"] is True
    assert by_entity["xi-id"]["actor_intelligence_available"] is False


def test_select_key_actors_status_insufficient_when_no_profiles():
    client = _base_client()
    result = ai.select_key_actors(client, "case-1")
    assert result["actor_intelligence_status"] == "insufficient"


# ── Phase D-5(Holistic Reasoning Architecture Review): Foundation Pack由来の
# Control/Leverageシグナルの追加 ──────────────────────────────────────────

def test_foundation_pack_control_leverage_entity_ids_extracts_assets_and_dependencies():
    foundation_pack = {
        "7_assets": [{"entity_id": "gov-id", "asset_type": "port"}],
        "9_vulnerabilities_and_constraints": [{"entity_id": "company-id", "dependency_type": "supply_chain"}],
    }
    ids = ai._foundation_pack_control_leverage_entity_ids(foundation_pack)
    assert ids == {"gov-id", "company-id"}


def test_foundation_pack_control_leverage_entity_ids_empty_when_no_pack():
    assert ai._foundation_pack_control_leverage_entity_ids(None) == set()
    assert ai._foundation_pack_control_leverage_entity_ids({}) == set()


def test_score_actor_relevance_adds_foundation_pack_control_leverage_bonus():
    entity = {"entity_id": "gov-id", "entity_type": "government"}
    score_without, reasons_without = ai.score_actor_relevance(
        entity, issue_linked_ids=set(), relationship_backed_ids=set())
    score_with, reasons_with = ai.score_actor_relevance(
        entity, issue_linked_ids=set(), relationship_backed_ids=set(),
        foundation_pack_control_ids={"gov-id"})
    assert score_with == score_without + 1.0
    assert "foundation_pack_control_or_leverage" in reasons_with
    assert "foundation_pack_control_or_leverage" not in reasons_without


def test_score_actor_relevance_does_not_add_bonus_for_unrelated_entity():
    entity = {"entity_id": "person-id", "entity_type": "person"}
    score, reasons = ai.score_actor_relevance(
        entity, issue_linked_ids=set(), relationship_backed_ids=set(),
        foundation_pack_control_ids={"gov-id"})  # personはこのIDに含まれない
    assert "foundation_pack_control_or_leverage" not in reasons


# ── Actor Runtime Activation（Phase D、ユーザー指示）: Issue Linkが無くても
# Runtime Candidateから機能すること ──────────────────────────────────────

def test_get_candidate_actor_entities_includes_extra_candidates_without_issue_link():
    """xi-idはissue_linkedではないが、extra_candidate_entity_idsで渡すと候補に入る
    （DB永続化はしない、この呼び出しの中だけ）。"""
    client = _base_client(geo_entity_issues=[{"issue_id": "issue-1", "entity_id": "trump-id"}])
    candidates = ai.get_candidate_actor_entities(
        client, "case-1", extra_candidate_entity_ids={"xi-id"})
    ids = {e["entity_id"] for e in candidates}
    assert "xi-id" in ids


def test_get_candidate_actor_entities_extra_candidates_also_get_1hop_expansion():
    """Runtime Candidateも既存の1-hop Relationship展開の対象になる（既存ロジック変更なし、
    候補母集団の入力を増やすだけ）。"""
    client = _base_client(geo_entities=[
        {"entity_id": "xi-id", "entity_type": "person", "canonical_name": "Xi Jinping",
         "external_ids": {}, "is_demo": False},
        {"entity_id": "putin-id", "entity_type": "person", "canonical_name": "Vladimir Putin",
         "external_ids": {}, "is_demo": False},
    ], geo_case_issues=[], geo_entity_issues=[], geo_relationships=[
        {"relationship_id": "rel-1", "from_entity_id": "xi-id", "to_entity_id": "putin-id",
         "relationship_domain": "diplomacy", "relationship_type": "alignment",
         "leverage_direction": None, "confidence": "中", "is_demo": False},
    ])
    candidates = ai.get_candidate_actor_entities(
        client, "case-1", extra_candidate_entity_ids={"xi-id"})
    ids = {e["entity_id"] for e in candidates}
    assert "xi-id" in ids and "putin-id" in ids


def test_get_candidate_actor_entities_works_without_case_id():
    """case_id=Noneでも動作する（get_issue_ids_for_caseを呼ばずcurated側は空、
    extra_candidate_entity_idsだけで候補を構成できる）。"""
    client = _base_client(geo_case_issues=[], geo_entity_issues=[])
    candidates = ai.get_candidate_actor_entities(
        client, None, extra_candidate_entity_ids={"xi-id"})
    ids = {e["entity_id"] for e in candidates}
    assert ids == {"xi-id"}


def test_resolve_reference_date_with_none_case_id_skips_query_and_falls_back_to_today():
    """回帰（2026-09-24発見・修正）: case_id=Noneのままf"eq.{case_id}"を組み立てると、
    実DB（PostgREST）ではgeo_cases.case_id(uuid列)への不正な値"eq.None"として
    400 Bad Requestになる（FakeSupabaseClientは型検証をしないため気づけなかった）。
    select_key_actors自身のdocstringが明記する「case_id=Noneでも動作する」という契約を
    満たすため、case_idが無い場合はgeo_casesへのクエリ自体を行わないことを確認する。"""
    client = _base_client(geo_case_issues=[], geo_entity_issues=[])

    def _raise_if_geo_cases(table, params):
        if table == "geo_cases":
            raise AssertionError("case_id=Noneでgeo_casesへのクエリを実行してはいけません")
        return FakeSupabaseClient.select(client, table, params)

    client.select = _raise_if_geo_cases  # type: ignore[method-assign]
    # _resolve_reference_dateの契約は「UTC基準の今日」。期待値もUTCで組み立て、実行環境の
    # ローカルタイムゾーン（JST等）に依存させない。UTC日付境界をまたぐ実行に備え、呼び出しの
    # 前後のUTC日付のどちらかと一致すればよい（Product logicは変更しない、test hygieneのみ）。
    utc_before = datetime.now(timezone.utc).date()
    result = ai._resolve_reference_date(client, None)
    utc_after = datetime.now(timezone.utc).date()
    assert result in (utc_before, utc_after)


def test_score_actor_relevance_situation_relevant_lower_than_issue_linked():
    entity = {"entity_id": "xi-id", "entity_type": "person"}
    score_runtime, reasons_runtime = ai.score_actor_relevance(
        entity, issue_linked_ids=set(), relationship_backed_ids=set(),
        runtime_resolved_ids={"xi-id"})
    score_curated, reasons_curated = ai.score_actor_relevance(
        entity, issue_linked_ids={"xi-id"}, relationship_backed_ids=set(),
        runtime_resolved_ids={"xi-id"})
    assert "situation_relevant" in reasons_runtime
    assert "issue_linked" in reasons_curated
    assert "situation_relevant" not in reasons_curated  # curatedが優先、二重加点しない
    assert score_curated > score_runtime  # curated(3.0) > situation_relevant(2.0)


def test_select_key_actors_and_pack_work_with_runtime_candidates_and_no_case_id():
    """case_id=Noneでも、Runtime Candidateだけでkey_actors/Actor Intelligence Packが
    構築できる（Foundation Context Resolverの前提を検証する回帰）。"""
    client = _base_client(geo_case_issues=[], geo_entity_issues=[])
    selection = ai.select_key_actors(client, None, extra_candidate_entity_ids={"trump-id"})
    ids = {ka["entity_id"] for ka in selection["key_actors"]}
    assert "trump-id" in ids

    pack = ai.build_actor_intelligence_pack(
        client, None, extra_candidate_entity_ids={"trump-id"})
    assert any(ka["entity_id"] == "trump-id" for ka in pack["key_actors"])


def test_select_key_actors_foundation_pack_signal_boosts_institution_not_unrelated_person():
    """代表ケース: structure-firstな局面を想定し、Foundation Pack上でAsset/Dependencyに
    紐づく政府主体（gov-id）が、同じissue_linked・同じスコアだった人物主体（person-id、
    Asset/Dependencyには一切登場しない）より順位が上がることを確認する。人物であること
    自体がボーナスを生むわけではない（structure-firstケースで人物を不必要に昇格させない）。"""
    client = _base_client(
        geo_entities=[
            {"entity_id": "gov-id", "entity_type": "government", "canonical_name": "Ministry",
             "external_ids": {}, "is_demo": False},
            {"entity_id": "person-id", "entity_type": "person", "canonical_name": "Advisor",
             "external_ids": {}, "is_demo": False},
        ],
        geo_entity_issues=[
            {"issue_id": "issue-1", "entity_id": "gov-id"},
            {"issue_id": "issue-1", "entity_id": "person-id"},
        ],
    )
    foundation_pack = {"7_assets": [{"entity_id": "gov-id", "asset_type": "port"}],
                       "9_vulnerabilities_and_constraints": []}

    without_signal = ai.select_key_actors(client, "case-1", max_actors=5)
    by_entity_without = {ka["entity_id"]: ka for ka in without_signal["key_actors"]}
    assert by_entity_without["gov-id"]["relevance_score"] == by_entity_without["person-id"]["relevance_score"]

    with_signal = ai.select_key_actors(client, "case-1", max_actors=5, foundation_pack=foundation_pack)
    by_entity_with = {ka["entity_id"]: ka for ka in with_signal["key_actors"]}
    assert by_entity_with["gov-id"]["relevance_score"] > by_entity_with["person-id"]["relevance_score"]
    assert by_entity_with["person-id"]["relevance_score"] == by_entity_without["person-id"]["relevance_score"]
    assert "foundation_pack_control_or_leverage" in by_entity_with["gov-id"]["selection_reasons"]
    assert "foundation_pack_control_or_leverage" not in by_entity_with["person-id"]["selection_reasons"]


# ── Freshness / Temporal Filter ───────────────────────────────────────────

def test_compute_freshness_thresholds():
    ref = date(2026, 8, 15)
    assert ai.compute_freshness("2026-08-01", reference_date=ref)["status"] == "current"
    assert ai.compute_freshness("2026-06-01", reference_date=ref)["status"] == "aging"
    assert ai.compute_freshness("2026-01-01", reference_date=ref)["status"] == "stale"


def test_actor_intelligence_status_partial_when_issue_position_out_of_temporal_range():
    profile_item = {"structured_content": {
        "core_profile": {"core_objectives": [{"statement": "x", "information_class": "observed_fact",
                                                "confidence": "中"}]},
        "current_posture": {"as_of": "2026-08-01", "confidence": "中"},
        "issue_positions": [{"issue_id": "issue-1", "confidence": "中",
                              "valid_from": "2027-01-01", "valid_to": None}],
    }}
    status = ai._actor_intelligence_status_for(profile_item, ["issue-1"], date(2026, 8, 15))
    assert status == "partial"  # issue_positionが未来日付でTemporal Filterに落ちる


def test_actor_intelligence_status_sufficient_when_all_present_and_valid():
    profile_item = {"structured_content": {
        "core_profile": {"core_objectives": [{"statement": "x", "information_class": "observed_fact",
                                                "confidence": "中"}]},
        "current_posture": {"as_of": "2026-08-01", "confidence": "中"},
        "issue_positions": [{"issue_id": "issue-1", "confidence": "中",
                              "valid_from": None, "valid_to": None}],
    }}
    status = ai._actor_intelligence_status_for(profile_item, ["issue-1"], date(2026, 8, 15))
    assert status == "sufficient"


def test_actor_intelligence_status_insufficient_when_no_profile():
    assert ai._actor_intelligence_status_for(None, ["issue-1"], date(2026, 8, 15)) == "insufficient"


# ── Actor Intelligence Pack構築 ───────────────────────────────────────────

def test_build_actor_intelligence_pack_includes_freshness_and_reasons():
    client = _base_client()
    ai.seed_actor_profile(client, **_seed_kwargs(
        activate=True,
        current_posture={"as_of": "2026-01-01", "confidence": "中"},
        issue_positions=[{"issue_id": "issue-1", "confidence": "中"}],
    ))
    pack = ai.build_actor_intelligence_pack(client, "case-1")
    trump_pack = next(a for a in pack["key_actors"] if a["entity_id"] == "trump-id")
    assert trump_pack["current_posture"]["freshness"]["status"] == "stale"
    assert "issue_linked" in trump_pack["selection_reasons"]
    assert pack["actor_intelligence_status"] in ("sufficient", "partial")


def test_trim_to_char_budget_shrinks_when_over_budget():
    packs = [{
        "entity_id": f"e{i}",
        "relevant_precedents": [{"knowledge_item_id": f"p{i}-{j}", "title": "x" * 200} for j in range(5)],
        "relevant_relationships": [],
    } for i in range(3)]
    trimmed = ai._trim_to_char_budget(packs, char_budget=500)
    import json
    assert len(json.dumps(trimmed, ensure_ascii=False)) <= 500 or len(trimmed) <= 1


# ── Tier1(Eguchi Expert AI Final v1.0対応): DirectionalPerceptionの入力側接続 ──────

def test_relevant_relationships_include_direction_and_counterpart():
    client = _base_client()
    client.tables["geo_relationships"].append({
        "relationship_id": "rel-1", "from_entity_id": "trump-id", "to_entity_id": "xi-id",
        "relationship_domain": "trade", "relationship_type": "rivalry",
        "dependency_direction": "xi_over_trump", "leverage_direction": "trump_over_xi",
        "confidence": "中", "is_demo": False,
    })
    ai.seed_actor_profile(client, **_seed_kwargs())
    pack = ai.build_actor_intelligence_pack(client, "case-1")

    trump_pack = next(a for a in pack["key_actors"] if a["entity_id"] == "trump-id")
    trump_rels = trump_pack["relevant_relationships"]
    assert len(trump_rels) == 1
    assert trump_rels[0]["direction"] == "from"
    assert trump_rels[0]["counterpart_entity_id"] == "xi-id"
    assert trump_rels[0]["counterpart_name"] == "Xi Jinping"
    assert trump_rels[0]["dependency_direction"] == "xi_over_trump"
    assert trump_rels[0]["leverage_direction"] == "trump_over_xi"

    xi_pack = next(a for a in pack["key_actors"] if a["entity_id"] == "xi-id")
    xi_rels = xi_pack["relevant_relationships"]
    assert len(xi_rels) == 1
    assert xi_rels[0]["direction"] == "to"
    assert xi_rels[0]["counterpart_entity_id"] == "trump-id"
    assert xi_rels[0]["counterpart_name"] == "Donald Trump"


def test_relevant_relationships_excludes_relationships_not_involving_this_actor():
    client = _base_client()
    client.tables["geo_entities"].append(
        {"entity_id": "putin-id", "entity_type": "person", "canonical_name": "Vladimir Putin",
         "external_ids": {}, "is_demo": False})
    client.tables["geo_relationships"].extend([
        {"relationship_id": "rel-1", "from_entity_id": "trump-id", "to_entity_id": "xi-id",
         "relationship_domain": "diplomacy", "relationship_type": "rivalry",
         "leverage_direction": "trump_over_xi", "confidence": "高", "is_demo": False},
        {"relationship_id": "rel-2", "from_entity_id": "xi-id", "to_entity_id": "putin-id",
         "relationship_domain": "diplomacy", "relationship_type": "alignment",
         "leverage_direction": None, "confidence": "中", "is_demo": False},
    ])
    ai.seed_actor_profile(client, **_seed_kwargs())
    pack = ai.build_actor_intelligence_pack(client, "case-1")
    trump_pack = next(a for a in pack["key_actors"] if a["entity_id"] == "trump-id")
    # rel-2(xi-putin)はtrump-idを含まないため、trumpのrelevant_relationshipsには出ない
    rel_ids = {r["relationship_id"] for r in trump_pack["relevant_relationships"]}
    assert rel_ids == {"rel-1"}


# ── Phase 6A-2(Relationship Relevance Precision、ユーザー指示): situation_textを指定
# した場合のみ、1-hop展開で見つかった隣接Entityをsituation_relevanceでフィルタする ──

def test_entity_mentioned_in_text_matches_canonical_name_case_insensitively():
    entity = {"canonical_name": "Iran", "name_en": None, "name_ja": None, "local_name": None}
    assert ai._entity_mentioned_in_text(entity, "china and iran discussed oil trade")
    assert not ai._entity_mentioned_in_text(entity, "china and the gulf states")


def test_entity_mentioned_in_text_false_when_no_text():
    entity = {"canonical_name": "Iran"}
    assert not ai._entity_mentioned_in_text(entity, "")
    assert not ai._entity_mentioned_in_text(entity, None)


def test_get_candidate_actor_entities_default_keeps_unconditional_1hop_expansion():
    """situation_text未指定（既定）では、既存の無条件1-hop展開を維持する
    （既存呼び出し元・既存テストとの後方互換）。"""
    client = _base_client()
    client.tables["geo_relationships"].append({
        "relationship_id": "rel-1", "from_entity_id": "trump-id", "to_entity_id": "xi-id",
        "relationship_domain": "diplomacy", "relationship_type": "rivalry",
        "leverage_direction": "trump_over_xi", "confidence": "中", "is_demo": False,
    })
    candidates = ai.get_candidate_actor_entities(client, "case-1")
    ids = {e["entity_id"] for e in candidates}
    assert "xi-id" in ids


def test_get_candidate_actor_entities_situation_text_excludes_unmentioned_1hop_neighbor():
    """situation_textを指定すると、1-hop展開で見つかったが局面のテキストに一切登場しない
    隣接Entity（TSMC Caseで実際に確認されたChina→Iran contaminationの再現）は除外される。"""
    client = _base_client()
    client.tables["geo_relationships"].append({
        "relationship_id": "rel-1", "from_entity_id": "trump-id", "to_entity_id": "xi-id",
        "relationship_domain": "diplomacy", "relationship_type": "rivalry",
        "leverage_direction": "trump_over_xi", "confidence": "中", "is_demo": False,
    })
    candidates = ai.get_candidate_actor_entities(
        client, "case-1", situation_text="trump announced new tariffs on steel imports")
    ids = {e["entity_id"] for e in candidates}
    assert "xi-id" not in ids  # "xi"/"xi jinping"はsituation_textに登場しない


def test_get_candidate_actor_entities_situation_text_keeps_mentioned_1hop_neighbor():
    client = _base_client()
    client.tables["geo_relationships"].append({
        "relationship_id": "rel-1", "from_entity_id": "trump-id", "to_entity_id": "xi-id",
        "relationship_domain": "diplomacy", "relationship_type": "rivalry",
        "leverage_direction": "trump_over_xi", "confidence": "中", "is_demo": False,
    })
    candidates = ai.get_candidate_actor_entities(
        client, "case-1", situation_text="trump and xi jinping discussed semiconductor export controls")
    ids = {e["entity_id"] for e in candidates}
    assert "xi-id" in ids  # "xi jinping"がsituation_textに明示されている


def test_get_candidate_actor_entities_situation_text_does_not_filter_issue_linked_or_extra_candidates():
    """situation_textによるフィルタは1-hop展開された隣接Entityのみに適用する。
    Issue-linkedな起点・extra_candidate_entity_idsで明示的に渡された候補自体は
    situation_textの有無にかかわらず常に残る（既存の候補判定基準を変更しない）。"""
    client = _base_client()
    candidates = ai.get_candidate_actor_entities(
        client, "case-1", extra_candidate_entity_ids={"xi-id"},
        situation_text="something entirely unrelated")
    ids = {e["entity_id"] for e in candidates}
    assert "trump-id" in ids  # issue-linked起点
    assert "xi-id" in ids  # extra_candidate_entity_idsで明示的に渡された候補
