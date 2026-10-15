"""地政学ナレッジ＆インテリジェンスDB Phase2A: geo_actor_episode_service.pyのテスト。

Actor Safety: GDELT Eventを起点にのみCandidateを生成し、GKG Personsへの単純な登場だけでは
生成しないこと、Actor eligibility(entity_type)判定、Idempotency(Evidence蓄積)を検証する。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_actor_episode_service as svc  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _event_row(event_id="e1", actor1="TRUMP", actor2="CHINA", provider_event_id="111"):
    return {
        "provider_event_id": provider_event_id, "external_event_id": event_id,
        "actor1_name": actor1, "actor2_name": actor2,
        "event_code": "112", "event_base_code": "112", "event_date": "2026-09-01",
        "action_geo_full_name": "Washington", "goldstein_scale": -2.0, "avg_tone": -3.0,
    }


def _entities_table(*, trump_type="person", china_type="country"):
    return {"geo_entities": [
        {"entity_id": "trump-id", "canonical_name": "TRUMP", "entity_type": trump_type},
        {"entity_id": "china-id", "canonical_name": "CHINA", "entity_type": china_type},
    ]}


def test_generate_candidates_creates_for_matched_actor1_and_actor2():
    client = FakeSupabaseClient(tables=_entities_table())
    result = svc.generate_candidates_for_batch(
        client, events=[_event_row()], external_signal_ids_by_event_id={"e1": ["s1"]},
        ingestion_run_id="run-1")
    assert result["created"] == 2  # actor1(TRUMP)とactor2(CHINA)両方
    assert result["skipped_no_match"] == 0
    rows = client.tables["geo_actor_episode_candidates"]
    roles = {r["actor_role"]: r for r in rows}
    assert roles["actor1"]["actor_entity_id"] == "trump-id"
    assert roles["actor1"]["counterparty_entity_id"] == "china-id"
    assert roles["actor1"]["counterparty_text"] == "CHINA"
    assert roles["actor2"]["actor_entity_id"] == "china-id"


def test_generate_candidates_skips_ambiguous_matches():
    """Actor1Nameが同名2件seedであいまい一致時に候補を作らない。"""
    client = FakeSupabaseClient(tables={"geo_entities": [
        {"entity_id": "trump-1", "canonical_name": "TRUMP", "entity_type": "person"},
        {"entity_id": "trump-2", "canonical_name": "TRUMP", "entity_type": "person"},
    ]})
    result = svc.generate_candidates_for_batch(
        client, events=[_event_row(actor2="")], external_signal_ids_by_event_id={},
        ingestion_run_id="run-1")
    assert result["created"] == 0
    assert result["skipped_ambiguous"] == 1


def test_generate_candidates_skips_no_match():
    """actor1(TRUMP)は一致0件、actor2(空文字=不在)も一致0件扱いとなり、
    どちらもskipped_no_matchとしてカウントされる(2件)。"""
    client = FakeSupabaseClient(tables={"geo_entities": []})
    result = svc.generate_candidates_for_batch(
        client, events=[_event_row(actor2="")], external_signal_ids_by_event_id={},
        ingestion_run_id="run-1")
    assert result["created"] == 0
    assert result["skipped_no_match"] == 2


def test_ineligible_actor_match_is_skipped_not_defaulted_to_eligible():
    """_is_actor_eligibleが偽を返すentityへの一致がCandidate化されず
    skipped_ineligible_actorとしてカウントされる（5回目レビュー②の回帰テスト）。
    'geographic_location'はACTOR_ENTITY_TYPESに含まれない非actor型。"""
    client = FakeSupabaseClient(tables={"geo_entities": [
        {"entity_id": "loc-id", "canonical_name": "TRUMP", "entity_type": "geographic_location"},
    ]})
    result = svc.generate_candidates_for_batch(
        client, events=[_event_row(actor2="")], external_signal_ids_by_event_id={},
        ingestion_run_id="run-1")
    assert result["created"] == 0
    assert result["skipped_ineligible_actor"] == 1
    assert client.tables.get("geo_actor_episode_candidates", []) == []


def test_gkg_person_only_mention_does_not_create_or_attribute_action():
    """GKG PersonsにActorが登場するがEventのActor1/Actor2ではないケースで、そのActorへ
    action/counterpartyが付与されない（誤帰属防止の回帰テスト）。generate_candidates_for_batch
    はevents(Event Actor1/Actor2起点)のみを入力に取り、GKG Persons情報は一切受け取らない
    構造そのものが誤帰属を構造的に防止していることを、Actor1/Actor2に含まれないentityが
    候補化されないことで確認する。"""
    client = FakeSupabaseClient(tables={"geo_entities": [
        {"entity_id": "trump-id", "canonical_name": "TRUMP", "entity_type": "person"},
        # "Putin"はGKG Personsにのみ登場する想定(Event Actor1/Actor2には無い)
        {"entity_id": "putin-id", "canonical_name": "PUTIN", "entity_type": "person"},
    ]})
    result = svc.generate_candidates_for_batch(
        client, events=[_event_row(actor1="TRUMP", actor2="")],
        external_signal_ids_by_event_id={"e1": ["s1"]}, ingestion_run_id="run-1")
    assert result["created"] == 1
    rows = client.tables["geo_actor_episode_candidates"]
    assert len(rows) == 1
    assert rows[0]["actor_entity_id"] == "trump-id"
    # PutinはEventのActor1/Actor2に登場しないため一切Candidate化されない
    assert all(r["actor_entity_id"] != "putin-id" for r in rows)


def test_generate_candidates_dedup_adds_evidence_not_new_row():
    """同一fingerprintへの2回目はexternal_signal_idsへ追記のみ、新規行を作らない。"""
    client = FakeSupabaseClient(tables=_entities_table())
    svc.generate_candidates_for_batch(
        client, events=[_event_row(actor2="")], external_signal_ids_by_event_id={"e1": ["s1"]},
        ingestion_run_id="run-1")
    result2 = svc.generate_candidates_for_batch(
        client, events=[_event_row(actor2="")], external_signal_ids_by_event_id={"e1": ["s2"]},
        ingestion_run_id="run-2")
    assert result2["created"] == 0
    assert result2["evidence_added"] == 1
    rows = client.tables["geo_actor_episode_candidates"]
    assert len(rows) == 1
    assert set(rows[0]["external_signal_ids"]) == {"s1", "s2"}
    assert rows[0]["evidence_count"] == 2


def test_derive_theme_id_returns_none_when_multiple_themes():
    """Eventにlinkした複数SignalのQueryが異なるthemeに属する場合theme_idがNULLになる。"""
    client = FakeSupabaseClient(tables={
        **_entities_table(),
        "geo_external_event_signal_links": [
            {"external_event_id": "e1", "external_signal_id": "s1"},
            {"external_event_id": "e1", "external_signal_id": "s2"},
        ],
        "geo_external_signal_query_matches": [
            {"external_signal_id": "s1", "query_id": "q1"},
            {"external_signal_id": "s2", "query_id": "q2"},
        ],
        "geo_external_signal_queries": [
            {"query_id": "q1", "theme_id": "theme-a"},
            {"query_id": "q2", "theme_id": "theme-b"},
        ],
    })
    svc.generate_candidates_for_batch(
        client, events=[_event_row(actor2="")], external_signal_ids_by_event_id={"e1": ["s1", "s2"]},
        ingestion_run_id="run-1")
    row = client.tables["geo_actor_episode_candidates"][0]
    assert row["theme_id"] is None


def test_derive_theme_id_returns_single_theme_when_unambiguous():
    client = FakeSupabaseClient(tables={
        **_entities_table(),
        "geo_external_event_signal_links": [{"external_event_id": "e1", "external_signal_id": "s1"}],
        "geo_external_signal_query_matches": [{"external_signal_id": "s1", "query_id": "q1"}],
        "geo_external_signal_queries": [{"query_id": "q1", "theme_id": "theme-a"}],
    })
    svc.generate_candidates_for_batch(
        client, events=[_event_row(actor2="")], external_signal_ids_by_event_id={"e1": ["s1"]},
        ingestion_run_id="run-1")
    row = client.tables["geo_actor_episode_candidates"][0]
    assert row["theme_id"] == "theme-a"


def test_update_actor_episode_candidate_status_does_not_touch_actor_intelligence():
    """AcceptしてもActor Core Profile等（geo_entities/geo_knowledge_items）への
    書き込みが発生しないこと（Actor Safety）。"""
    client = FakeSupabaseClient(tables={
        **_entities_table(),
        "geo_actor_episode_candidates": [
            {"actor_episode_candidate_id": "c1", "candidate_status": "candidate"},
        ],
    })
    svc.update_actor_episode_candidate_status(
        client, candidate_id="c1", candidate_status="accepted", reviewed_by="analyst1")
    assert client.tables["geo_actor_episode_candidates"][0]["candidate_status"] == "accepted"
    # geo_entities/geo_knowledge_itemsへのupdateが一切発生していないこと
    assert not any(t == "geo_entities" for t, _, _ in client.updated)
    assert not any(t == "geo_knowledge_items" for t, _, _ in client.updated)


def test_update_actor_episode_candidate_status_rejects_invalid_transition():
    client = FakeSupabaseClient(tables={"geo_actor_episode_candidates": [
        {"actor_episode_candidate_id": "c1", "candidate_status": "accepted"},
    ]})
    try:
        svc.update_actor_episode_candidate_status(
            client, candidate_id="c1", candidate_status="rejected", reviewed_by="analyst1")
        assert False, "ValueErrorが送出されるはず"
    except ValueError:
        pass
