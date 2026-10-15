"""Phase 2G-1: Theme Discovery — Theme Candidate生成/管理。

§Tests最低限（Cluster→Candidate、connected components→1 Candidate、fingerprint merge/
separate、Evidence外Source ID非保存、malformed LLM output fail-safe、lifecycle transition）
を検証する。既存External Signal→Actor Episode→Episode Cluster→Situation Linkパイプライン、
およびINTELLIGENCE_AI_V1（Phase 2F）は一切変更していないことは、フル回帰テストで別途確認する。
"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.params import Depends as DependsMarker  # noqa: E402

import geo_ios_identity  # noqa: E402
import geo_llm_common  # noqa: E402
import geo_theme_candidate_api  # noqa: E402
import geo_theme_candidate_service as candidate_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _cluster(cluster_id, actor_entity_id="actor-1", **overrides):
    row = {
        "episode_cluster_id": cluster_id, "actor_entity_id": actor_entity_id,
        "quad_class": 1, "event_root_code": "10", "is_demo": False,
        "member_count": 3, "first_event_date": "2026-01-01", "last_event_date": "2026-01-10",
        "goldstein_min": -5.0, "goldstein_max": -2.0, "trajectory": "escalating",
        "representative_counterparties": ["China"], "representative_geo": ["Washington"],
        "cluster_status": "accepted",
    }
    row.update(overrides)
    return row


def _member(candidate_id, episode_cluster_id, actor_entity_id="actor-1", **overrides):
    return {
        "member": {"episode_cluster_id": episode_cluster_id, "actor_episode_candidate_id": candidate_id},
        "candidate": {
            "actor_episode_candidate_id": candidate_id, "actor_entity_id": actor_entity_id,
            "matched_name_text": "WHITE HOUSE", "counterparty_text": "CHINA",
            "counterparty_entity_id": None, "observed_action": "took a restrictive trade action",
            "event_date": "2026-01-05", "theme_id": None,
            "external_signal_ids": [f"sig-{candidate_id}"], "external_event_id": f"event-{candidate_id}",
            **overrides,
        },
    }


def _tables(clusters=None, members=None, links=None, entities=None, themes=None,
            candidates=None) -> dict:
    return {
        "geo_episode_clusters": clusters or [],
        "geo_episode_cluster_members": members or [],
        "geo_actor_episode_candidates": candidates or [],
        "geo_episode_cluster_links": links or [],
        "geo_entities": entities or [{"entity_id": "actor-1", "canonical_name": "White House"},
                                      {"entity_id": "actor-2", "canonical_name": "Beijing"}],
        "geo_themes": themes or [],
        "geo_theme_candidates": [],
        "geo_external_signals": [], "geo_external_events": [],
    }


def _fake_framing_llm(title="米国対中AI半導体政策の変化", change_type="policy_shift",
                       candidate_type="changing"):
    def _call(azure_client, model, system_prompt, user_prompt, schema, schema_name, **kwargs):
        return {"data": {
            "title": title,
            "observed_change": "White Houseが対中交渉上の裁量を確保する方向へ運用が変わっている可能性がある。",
            "why_now": "直近の観測されたActor行動が増加している。",
            "question": "規制強化そのものではなく、裁量確保への運用変化なのかを分析すべきではないか？",
            "actors": ["White House", "Beijing"],
            "regions": ["Washington"],
            "themes": [],
            "candidate_type": candidate_type,
            "change_type": change_type,
            "business_relevance": ["regulatory"],
        }}
    return _call


# ---- 1. Cluster → Candidate --------------------------------------------------------

def test_single_strong_cluster_becomes_one_candidate(monkeypatch):
    m1 = _member("cand-1", "cluster-1")
    client = FakeSupabaseClient(tables=_tables(
        clusters=[_cluster("cluster-1")],
        members=[m1["member"]], candidates=[m1["candidate"]],
    ))
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _fake_framing_llm())

    result = candidate_service.sync_theme_candidates(client, azure_client=object(), model="gpt")
    assert len(result["created"]) == 1
    assert result["merged"] == []
    assert result["skipped"] == []

    candidates = candidate_service.list_candidates(client)
    assert len(candidates) == 1
    assert candidates[0]["change_type"] == "policy_shift"
    assert candidates[0]["status"] == "NEW"


def test_weak_single_cluster_is_not_candidate_worthy():
    weak_cluster = _cluster("cluster-weak", member_count=1, trajectory="stable")
    client = FakeSupabaseClient(tables=_tables(clusters=[weak_cluster]))
    materials = candidate_service.list_candidate_worthy_material(client)
    assert materials == []


# ---- 2. connected components → 1 Candidate -----------------------------------------

def test_situation_link_connected_component_becomes_one_candidate(monkeypatch):
    m1 = _member("cand-1", "cluster-1", actor_entity_id="actor-1")
    m2 = _member("cand-2", "cluster-2", actor_entity_id="actor-2")
    client = FakeSupabaseClient(tables=_tables(
        clusters=[_cluster("cluster-1", actor_entity_id="actor-1", member_count=1, trajectory="stable"),
                  _cluster("cluster-2", actor_entity_id="actor-2", member_count=1, trajectory="stable")],
        members=[m1["member"], m2["member"]], candidates=[m1["candidate"], m2["candidate"]],
        links=[{"cluster_link_id": "link-1", "source_cluster_id": "cluster-1",
                "target_cluster_id": "cluster-2", "link_status": "accepted",
                "link_type": "same_situation_candidate", "is_demo": False}],
    ))
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _fake_framing_llm())

    components = candidate_service.find_accepted_situation_components(client)
    assert len(components) == 1
    assert set(components[0]["cluster_ids"]) == {"cluster-1", "cluster-2"}

    result = candidate_service.sync_theme_candidates(client, azure_client=object(), model="gpt")
    # cluster-1/cluster-2はどちらもmember_count=1・trajectory=stableで単独では弱いsignalだが、
    # componentとして連結されているため1件のCandidateになる（それぞれ別Candidateにはならない）。
    assert len(result["created"]) == 1
    candidates = candidate_service.list_candidates(client)
    assert len(candidates) == 1
    assert set(candidates[0]["source_episode_cluster_ids"]) == {"cluster-1", "cluster-2"}


# ---- 3/4. fingerprint merge / distinct ---------------------------------------------

def test_same_fingerprint_merges_instead_of_duplicating(monkeypatch):
    m1 = _member("cand-1", "cluster-1")
    client = FakeSupabaseClient(tables=_tables(
        clusters=[_cluster("cluster-1")], members=[m1["member"]], candidates=[m1["candidate"]],
    ))
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _fake_framing_llm())

    result1 = candidate_service.sync_theme_candidates(client, azure_client=object(), model="gpt")
    assert len(result1["created"]) == 1

    result2 = candidate_service.sync_theme_candidates(client, azure_client=object(), model="gpt")
    assert result2["created"] == []
    assert len(result2["merged"]) == 1
    assert result1["created"] == result2["merged"]

    candidates = candidate_service.list_candidates(client)
    assert len(candidates) == 1  # 重複行が作られていない


def test_same_source_structure_merges_even_if_llm_reclassifies_change_type(monkeypatch):
    """実DB検証（2026-09-30）で判明: 同一Source Cluster構造でも、LLMのchange_type分類は
    turnごとに変わりうる（同一Evidenceでも'actor_movement'→'relationship_change'等）。
    change_typeをfingerprintから除外したことで、この場合もmerge対象になることを確認する
    （merge時にcandidate_type/change_typeは最新の分類へ更新される）。"""
    m1 = _member("cand-1", "cluster-1")
    client = FakeSupabaseClient(tables=_tables(
        clusters=[_cluster("cluster-1")], members=[m1["member"]], candidates=[m1["candidate"]],
    ))

    monkeypatch.setattr(geo_llm_common, "call_llm_structured",
                         _fake_framing_llm(change_type="actor_movement"))
    result1 = candidate_service.sync_theme_candidates(client, azure_client=object(), model="gpt")
    assert len(result1["created"]) == 1

    monkeypatch.setattr(geo_llm_common, "call_llm_structured",
                         _fake_framing_llm(change_type="relationship_change"))
    result2 = candidate_service.sync_theme_candidates(client, azure_client=object(), model="gpt")
    assert result2["created"] == []
    assert result2["merged"] == result1["created"]

    candidates = candidate_service.list_candidates(client)
    assert len(candidates) == 1
    assert candidates[0]["change_type"] == "relationship_change"  # 最新の分類へ更新されている


def test_distinct_actor_gives_distinct_fingerprint(monkeypatch):
    m1 = _member("cand-1", "cluster-1", actor_entity_id="actor-1")
    m2 = _member("cand-2", "cluster-2", actor_entity_id="actor-2")
    client = FakeSupabaseClient(tables=_tables(
        clusters=[_cluster("cluster-1", actor_entity_id="actor-1"),
                  _cluster("cluster-2", actor_entity_id="actor-2")],
        members=[m1["member"], m2["member"]], candidates=[m1["candidate"], m2["candidate"]],
    ))
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _fake_framing_llm())

    result = candidate_service.sync_theme_candidates(client, azure_client=object(), model="gpt")
    assert len(result["created"]) == 2
    candidates = candidate_service.list_candidates(client)
    assert len(candidates) == 2
    fingerprints = {c["candidate_fingerprint"] for c in candidates}
    assert len(fingerprints) == 2


# ---- 5. Evidence外Source IDを保存しない ---------------------------------------------

def test_material_only_includes_known_source_ids():
    m1 = _member("cand-1", "cluster-1")
    client = FakeSupabaseClient(tables=_tables(
        clusters=[_cluster("cluster-1")], members=[m1["member"]], candidates=[m1["candidate"]],
    ))
    material = candidate_service.build_candidate_material(client, cluster_ids=["cluster-1"])
    assert material["source_external_signal_ids"] == ["sig-cand-1"]
    assert material["source_external_event_ids"] == ["event-cand-1"]
    # 捏造・混入した架空IDが含まれていないことを明示的に確認
    assert "unknown-signal" not in material["source_external_signal_ids"]


# ---- 6. malformed LLM output fail-safe ---------------------------------------------

def test_malformed_llm_output_is_skipped_not_crashed(monkeypatch):
    def _raise(*a, **kw):
        raise geo_llm_common.GeoLLMError("schema検証に失敗しました", attempts=[], exception_type="ValidationError")
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _raise)

    m1 = _member("cand-1", "cluster-1")
    client = FakeSupabaseClient(tables=_tables(
        clusters=[_cluster("cluster-1")], members=[m1["member"]], candidates=[m1["candidate"]],
    ))
    result = candidate_service.sync_theme_candidates(client, azure_client=object(), model="gpt")
    assert result["created"] == []
    assert len(result["skipped"]) == 1
    assert candidate_service.list_candidates(client) == []


def test_hallucination_guard_drops_ungrounded_terms():
    kept, dropped = candidate_service._filter_grounded(
        ["White House", "Made-up Actor"], ["White House", "Beijing"])
    assert kept == ["White House"]
    assert dropped == ["Made-up Actor"]


# ---- 7. lifecycle transition --------------------------------------------------------

def test_lifecycle_transition_and_invalid_status(monkeypatch):
    m1 = _member("cand-1", "cluster-1")
    client = FakeSupabaseClient(tables=_tables(
        clusters=[_cluster("cluster-1")], members=[m1["member"]], candidates=[m1["candidate"]],
    ))
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _fake_framing_llm())
    result = candidate_service.sync_theme_candidates(client, azure_client=object(), model="gpt")
    candidate_id = result["created"][0]

    updated = candidate_service.update_candidate_status(
        client, candidate_id, "SHORTLISTED", actor_id="human-analyst")
    assert updated["status"] == "SHORTLISTED"
    assert updated["status_updated_by"] == "human-analyst"

    try:
        candidate_service.update_candidate_status(client, candidate_id, "NOT_A_STATUS", actor_id="human-analyst")
        assert False, "invalid statusでエラーにならなかった"
    except candidate_service.ThemeCandidateError:
        pass


def test_ai_generation_path_never_transitions_lifecycle(monkeypatch):
    """§22 AIは自動でSHORTLISTED/HANDED_OFFへ遷移させない——生成経路が新規作成する行の
    statusは常にNEWであることを確認する（update_candidate_statusを呼ばない構造）。"""
    m1 = _member("cand-1", "cluster-1")
    client = FakeSupabaseClient(tables=_tables(
        clusters=[_cluster("cluster-1")], members=[m1["member"]], candidates=[m1["candidate"]],
    ))
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _fake_framing_llm())
    candidate_service.sync_theme_candidates(client, azure_client=object(), model="gpt")
    candidates = candidate_service.list_candidates(client)
    assert all(c["status"] == "NEW" for c in candidates)


# ---- ① Mutation APIはauthenticated IosIdentity必須（配線確認） --------------------

def test_sync_endpoint_requires_ios_identity():
    sig = inspect.signature(geo_theme_candidate_api.sync_theme_candidates)
    found = any(
        isinstance(p.default, DependsMarker) and p.default.dependency is geo_ios_identity.verify_ios_identity
        for p in sig.parameters.values())
    assert found, "sync_theme_candidates に Depends(geo_ios_identity.verify_ios_identity) が無い"


def test_update_status_endpoint_requires_ios_identity():
    sig = inspect.signature(geo_theme_candidate_api.update_theme_candidate_status)
    found = any(
        isinstance(p.default, DependsMarker) and p.default.dependency is geo_ios_identity.verify_ios_identity
        for p in sig.parameters.values())
    assert found, "update_theme_candidate_status に Depends(geo_ios_identity.verify_ios_identity) が無い"


def test_update_status_endpoint_uses_authenticated_identity_not_client_payload():
    """actor_idはrequest bodyから取らず、認証済みidentityから取ることを確認する
    （reviewer spoofing対策と同じ考え方）。"""
    sig = inspect.signature(geo_theme_candidate_api.UpdateStatusRequest)
    assert "actor_id" not in geo_theme_candidate_api.UpdateStatusRequest.model_fields


# ---- ② sync mergeはHuman-refined Candidateのframing/statusを上書きしない -----------

def test_new_status_candidate_gets_framing_updated_on_merge(monkeypatch):
    m1 = _member("cand-1", "cluster-1")
    client = FakeSupabaseClient(tables=_tables(
        clusters=[_cluster("cluster-1")], members=[m1["member"]], candidates=[m1["candidate"]],
    ))
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _fake_framing_llm(title="初回タイトル"))
    result1 = candidate_service.sync_theme_candidates(client, azure_client=object(), model="gpt")
    candidate_id = result1["created"][0]
    assert candidate_service.get_candidate(client, candidate_id)["status"] == "NEW"

    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _fake_framing_llm(title="更新後タイトル"))
    result2 = candidate_service.sync_theme_candidates(client, azure_client=object(), model="gpt")
    assert result2["merged"] == [candidate_id]
    assert candidate_service.get_candidate(client, candidate_id)["title"] == "更新後タイトル"


def test_shortlisted_candidate_framing_is_not_overwritten_by_sync(monkeypatch):
    m1 = _member("cand-1", "cluster-1")
    client = FakeSupabaseClient(tables=_tables(
        clusters=[_cluster("cluster-1")], members=[m1["member"]], candidates=[m1["candidate"]],
    ))
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _fake_framing_llm(title="AI初回タイトル"))
    result1 = candidate_service.sync_theme_candidates(client, azure_client=object(), model="gpt")
    candidate_id = result1["created"][0]

    candidate_service.update_candidate_status(client, candidate_id, "SHORTLISTED", actor_id="human-analyst")
    before = candidate_service.get_candidate(client, candidate_id)

    monkeypatch.setattr(geo_llm_common, "call_llm_structured",
                         _fake_framing_llm(title="AIが書き換えようとしたタイトル", change_type="signal_spike"))
    result2 = candidate_service.sync_theme_candidates(client, azure_client=object(), model="gpt")
    assert result2["merged"] == [candidate_id]  # signal_count等は更新されmergeにはなる

    after = candidate_service.get_candidate(client, candidate_id)
    assert after["status"] == "SHORTLISTED"  # statusは変わらない
    assert after["title"] == before["title"] == "AI初回タイトル"  # human-facing framingは上書きされない
    assert after["observed_change"] == before["observed_change"]
    assert after["why_now"] == before["why_now"]
    assert after["question"] == before["question"]
    assert after["candidate_type"] == before["candidate_type"]
    assert after["change_type"] == before["change_type"]  # "signal_spike"へ書き換わっていない
    assert after["business_relevance"] == before["business_relevance"]
    # external evidence相当のみ更新されてよい
    assert after["signal_count"] >= before["signal_count"]


def test_dismissed_candidate_framing_and_status_not_overwritten_by_sync(monkeypatch):
    m1 = _member("cand-1", "cluster-1")
    client = FakeSupabaseClient(tables=_tables(
        clusters=[_cluster("cluster-1")], members=[m1["member"]], candidates=[m1["candidate"]],
    ))
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _fake_framing_llm(title="見送り前タイトル"))
    result1 = candidate_service.sync_theme_candidates(client, azure_client=object(), model="gpt")
    candidate_id = result1["created"][0]
    candidate_service.update_candidate_status(client, candidate_id, "DISMISSED", actor_id="human-analyst")

    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _fake_framing_llm(title="AIが書き換えようとしたタイトル"))
    candidate_service.sync_theme_candidates(client, azure_client=object(), model="gpt")

    after = candidate_service.get_candidate(client, candidate_id)
    assert after["status"] == "DISMISSED"
    assert after["title"] == "見送り前タイトル"


def test_monitoring_and_handed_off_candidates_framing_not_overwritten_by_sync(monkeypatch):
    for status in ("MONITORING", "HANDED_OFF"):
        m1 = _member(f"cand-{status}", f"cluster-{status}")
        client = FakeSupabaseClient(tables=_tables(
            clusters=[_cluster(f"cluster-{status}")], members=[m1["member"]], candidates=[m1["candidate"]],
        ))
        monkeypatch.setattr(geo_llm_common, "call_llm_structured", _fake_framing_llm(title=f"{status}前タイトル"))
        result1 = candidate_service.sync_theme_candidates(client, azure_client=object(), model="gpt")
        candidate_id = result1["created"][0]
        candidate_service.update_candidate_status(client, candidate_id, status, actor_id="human-analyst")

        monkeypatch.setattr(geo_llm_common, "call_llm_structured", _fake_framing_llm(title="書き換え試行タイトル"))
        candidate_service.sync_theme_candidates(client, azure_client=object(), model="gpt")

        after = candidate_service.get_candidate(client, candidate_id)
        assert after["status"] == status
        assert after["title"] == f"{status}前タイトル"


# ---- Phase 2G-4: Wall-bounce Refinement（Human-triggered apply） -------------------

def _minimal_candidate_row(**overrides) -> dict:
    row = {
        "candidate_id": "cand-refine-1",
        "candidate_fingerprint": "fp-refine", "title": "元のタイトル", "question": "元の問い",
        "signal_summary": "signal", "observed_change": "observed", "why_now": "why",
        "actors": ["Actor A"], "regions": ["Region A"], "themes": [], "business_relevance": [],
        "candidate_type": "emerging", "change_type": "actor_movement",
        "source_episode_cluster_ids": ["cluster-x"], "source_situation_link_ids": [],
        "source_external_signal_ids": ["sig-x"], "source_external_event_ids": ["event-x"],
        "first_seen_at": "2026-09-01T00:00:00+00:00", "latest_signal_at": "2026-09-01T00:00:00+00:00",
        "signal_count": 1, "status": "NEW", "is_demo": False,
    }
    row.update(overrides)
    return row


def _theme_candidate_tables(**overrides) -> dict:
    # _tables()の"candidates"パラメータはgeo_actor_episode_candidates（Episode Candidate）
    # を指すため混同しない——ここではgeo_theme_candidates自体を直接差し込む専用helperを使う。
    tables = _tables()
    tables.update(overrides)
    return tables


def test_apply_candidate_refinement_updates_editable_fields_only():
    client = FakeSupabaseClient(
        tables=_theme_candidate_tables(geo_theme_candidates=[_minimal_candidate_row()]))
    candidate_id = candidate_service.list_candidates(client)[0]["candidate_id"]

    updated = candidate_service.apply_candidate_refinement(
        client, candidate_id,
        patch={"title": "改善後タイトル", "actors": ["Actor A", "Actor B"]},
        actor_id="human-analyst")
    assert updated["title"] == "改善後タイトル"
    assert updated["actors"] == ["Actor A", "Actor B"]

    refetched = candidate_service.get_candidate(client, candidate_id)
    assert refetched["title"] == "改善後タイトル"
    # statusは変更されない
    assert refetched["status"] == "NEW"


def test_apply_candidate_refinement_rejects_evidence_and_status_fields():
    client = FakeSupabaseClient(
        tables=_theme_candidate_tables(geo_theme_candidates=[_minimal_candidate_row()]))
    candidate_id = candidate_service.list_candidates(client)[0]["candidate_id"]

    for forbidden_patch in (
        {"status": "SHORTLISTED"},
        {"signal_summary": "書き換え試行"},
        {"source_episode_cluster_ids": []},
        {"observed_change": "書き換え試行"},
        {"why_now": "書き換え試行"},
    ):
        try:
            candidate_service.apply_candidate_refinement(
                client, candidate_id, patch=forbidden_patch, actor_id="human-analyst")
            assert False, f"{forbidden_patch} が拒否されなかった"
        except candidate_service.ThemeCandidateError:
            pass

    unchanged = candidate_service.get_candidate(client, candidate_id)
    assert unchanged["status"] == "NEW"
    assert unchanged["signal_summary"] == "signal"
    assert unchanged["source_episode_cluster_ids"] == ["cluster-x"]


def test_apply_candidate_refinement_raises_for_missing_candidate():
    client = FakeSupabaseClient(tables=_tables())
    try:
        candidate_service.apply_candidate_refinement(
            client, "missing-id", patch={"title": "x"}, actor_id="human-analyst")
        assert False, "存在しないcandidate_idでエラーにならなかった"
    except candidate_service.ThemeCandidateError:
        pass


def test_apply_candidate_refinement_does_not_change_already_shortlisted_status(monkeypatch):
    """Wall-bounceで反映した変更もHuman-refined contentとして扱う——SHORTLISTED状態の
    Candidateへ反映しても、statusは変わらないままframingだけ更新される。"""
    client = FakeSupabaseClient(
        tables=_theme_candidate_tables(geo_theme_candidates=[_minimal_candidate_row(status="SHORTLISTED")]))
    candidate_id = candidate_service.list_candidates(client)[0]["candidate_id"]

    updated = candidate_service.apply_candidate_refinement(
        client, candidate_id, patch={"question": "改善後の問い"}, actor_id="human-analyst")
    assert updated["status"] == "SHORTLISTED"
    assert updated["question"] == "改善後の問い"
