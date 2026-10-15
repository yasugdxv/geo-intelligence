"""Phase 2G-5: Theme Discovery → Expert AI Consultation Handoff。

Validation Cases A〜F（Preview、Human-refined内容の引き継ぎ、未反映proposalの非引き継ぎ、
Double-click Idempotency、Intake作成失敗時のAtomicity、別部門Report非漏洩）を検証する。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_intake_service as intake_service  # noqa: E402
import geo_knowledge_access_policy as policy  # noqa: E402
import geo_theme_candidate_handoff_service as handoff_service  # noqa: E402
import geo_theme_candidate_service as candidate_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _candidate_row(**overrides) -> dict:
    row = {
        "candidate_id": "cand-1", "candidate_fingerprint": "fp-1",
        "title": "PHASE2G5 White House", "question": "規制強化は裁量確保への運用変化なのか？",
        "signal_summary": "複数Actorが同時期に関連行動", "observed_change": "規制運用が変化している",
        "why_now": "直近で複数行動が観測されているため", "actors": ["White House"],
        "regions": ["Washington"], "themes": ["regulation"], "business_relevance": ["regulatory"],
        "candidate_type": "changing", "change_type": "policy_shift",
        "source_episode_cluster_ids": [], "source_situation_link_ids": [],
        "source_external_signal_ids": ["sig-1"], "source_external_event_ids": [],
        "first_seen_at": "2026-09-01T00:00:00+00:00", "latest_signal_at": "2026-09-01T00:00:00+00:00",
        "signal_count": 1, "status": "NEW",
    }
    row.update(overrides)
    return row


def _report(report_id: str, **overrides) -> dict:
    row = {
        "report_id": report_id, "document_id": f"doc-{report_id}", "author": "analyst",
        "owner_department": "geopolitics", "report_type": "assessment",
        "country_region": ["Washington"], "topics": ["PHASE2G5"], "confidentiality": "internal",
        "report_kind": "internal_report", "uploaded_by": "owner-1", "version": 1,
        "supersedes_id": None, "report_identity_id": report_id, "is_demo": False,
        "human_visibility": "custom", "agent_sharing_policy": "none",
        "allowed_org_units": ["geopolitics"], "allowed_domains": [],
        "key_actors": ["White House"], "keywords": ["PHASE2G5"], "summary": "White House関連の分析",
        "business_context": None, "uploaded_at": "2026-09-01T00:00:00+00:00",
    }
    row.update(overrides)
    return row


def _tables(candidates=None, reports=None, sessions=None) -> dict:
    return {
        "geo_theme_candidates": candidates or [], "geo_reports": reports or [],
        "geo_knowledge_items": [], "geo_documents": [], "geo_themes": [],
        "geo_expert_review_sessions": [], "geo_episode_clusters": [], "geo_episode_cluster_links": [],
        "geo_external_signals": [{"external_signal_id": "sig-1", "title": "PHASE2G5 signal",
                                   "url": None, "published_at": "2026-08-30T00:00:00+00:00",
                                   "source_domain": "example.com"}],
        "geo_external_events": [],
        "geo_ios_analysis_sessions": sessions or [], "geo_ios_analysis_contexts": [],
    }


def _mock_search_rpc(client: FakeSupabaseClient, report_ids: list) -> None:
    """search_geo_reportsだけを固定応答へ差し替える。geo_handoff_create_session_if_absent等
    他のRPC名はFakeSupabaseClient本来の実装（元のbound method）へそのまま委譲する——
    client.rpc自体を丸ごと上書きすると、Handoffが使うSession作成RPCまで壊れてしまうため。"""
    original_rpc = client.rpc

    def _fake_rpc(fn_name, params=None):
        if fn_name == "search_geo_reports":
            eligible = set((params or {}).get("p_eligible_ids") or [])
            return [{"report_id": rid, "text_score": 0.5, "snippet": None}
                     for rid in report_ids if rid in eligible]
        return original_rpc(fn_name, params)
    client.rpc = _fake_rpc


_CTX = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)


# ---- Case A: 基本のHandoff ---------------------------------------------------------

def test_handoff_creates_session_and_context_with_mapped_fields():
    client = FakeSupabaseClient(tables=_tables(candidates=[_candidate_row()]))
    result = handoff_service.handoff_theme_candidate_to_expert_ai(
        client, azure_client=object(), model="gpt", candidate_id="cand-1",
        actor_id="human-1", user_id="human-1", requester_context=_CTX)

    assert result["created"] is True
    session = result["session"]
    assert session["title"] == "PHASE2G5 White House"
    assert session["source_theme_candidate_id"] == "cand-1"

    context = intake_service.get_current_context(client, session["analysis_session_id"])
    assert context["core_question"] == "規制強化は裁量確保への運用変化なのか？"
    assert context["relevant_actors"] == ["White House"]
    assert context["must_consider_topics"] == ["regulation"]
    assert "Washington" in context["geographic_scope"]
    assert "規制運用が変化している" in context["current_situation"]
    assert context["analysis_objective"] == "直近で複数行動が観測されているため"
    assert any(m["source_id"] == "sig-1" for m in context["reference_materials"])

    candidate = candidate_service.get_candidate(client, "cand-1")
    assert candidate["status"] == "HANDED_OFF"


def test_build_handoff_preview_creates_nothing():
    client = FakeSupabaseClient(tables=_tables(candidates=[_candidate_row()]))
    preview = handoff_service.build_handoff_preview(
        client, azure_client=object(), model="gpt", candidate_id="cand-1", requester_context=_CTX)

    assert preview["core_question"] == "規制強化は裁量確保への運用変化なのか？"
    assert preview["existing_session_id"] is None
    assert client.tables["geo_ios_analysis_sessions"] == []
    assert candidate_service.get_candidate(client, "cand-1")["status"] == "NEW"


# ---- Case B/C: investigation_questionsはHuman明示選択時のみ引き継ぐ -----------------

def test_handoff_carries_explicit_investigation_questions_into_information_gaps():
    client = FakeSupabaseClient(tables=_tables(candidates=[_candidate_row()]))
    result = handoff_service.handoff_theme_candidate_to_expert_ai(
        client, azure_client=object(), model="gpt", candidate_id="cand-1",
        actor_id="human-1", user_id="human-1", requester_context=_CTX,
        investigation_questions=["議会動向を確認する"])
    context = intake_service.get_current_context(client, result["session"]["analysis_session_id"])
    assert context["information_gaps"] == ["議会動向を確認する"]


def test_handoff_without_explicit_investigation_questions_leaves_information_gaps_empty():
    client = FakeSupabaseClient(tables=_tables(candidates=[_candidate_row()]))
    result = handoff_service.handoff_theme_candidate_to_expert_ai(
        client, azure_client=object(), model="gpt", candidate_id="cand-1",
        actor_id="human-1", user_id="human-1", requester_context=_CTX)
    context = intake_service.get_current_context(client, result["session"]["analysis_session_id"])
    # FakeSupabaseClientはPostgresのcolumn default（'[]'::jsonb）を再現しないため、
    # fieldsに含まれないjsonb列は行に存在しない——実DBではdefaultで[]になる。
    assert context.get("information_gaps", []) == []


def test_handoff_uses_current_db_framing_not_unapplied_wallbounce_proposal():
    """§6: WallbounceのAI proposalが未反映の場合、HandoffはDBに保存済みのCandidate
    framing（title/question等）だけを使う——proposal文言はどこにも混ざらない。"""
    client = FakeSupabaseClient(tables=_tables(candidates=[_candidate_row()]))
    result = handoff_service.handoff_theme_candidate_to_expert_ai(
        client, azure_client=object(), model="gpt", candidate_id="cand-1",
        actor_id="human-1", user_id="human-1", requester_context=_CTX)
    context = intake_service.get_current_context(client, result["session"]["analysis_session_id"])
    assert context["core_question"] == "規制強化は裁量確保への運用変化なのか？"
    assert "未反映" not in context["core_question"]


# ---- Case D: Double-click / Idempotency --------------------------------------------

def test_handoff_is_idempotent_returns_existing_session_on_second_call():
    client = FakeSupabaseClient(tables=_tables(candidates=[_candidate_row()]))
    first = handoff_service.handoff_theme_candidate_to_expert_ai(
        client, azure_client=object(), model="gpt", candidate_id="cand-1",
        actor_id="human-1", user_id="human-1", requester_context=_CTX)
    second = handoff_service.handoff_theme_candidate_to_expert_ai(
        client, azure_client=object(), model="gpt", candidate_id="cand-1",
        actor_id="human-1", user_id="human-1", requester_context=_CTX)

    assert first["created"] is True
    assert second["created"] is False
    assert second["session"]["analysis_session_id"] == first["session"]["analysis_session_id"]
    assert len(client.tables["geo_ios_analysis_sessions"]) == 1


# ---- Case E: Intake作成失敗時のAtomicity -------------------------------------------

def test_handoff_rolls_back_session_when_context_creation_fails(monkeypatch):
    client = FakeSupabaseClient(tables=_tables(candidates=[_candidate_row()]))

    def _raise(*a, **kw):
        raise intake_service.IntakeError("擬似的な作成失敗")
    monkeypatch.setattr(intake_service, "create_context_version", _raise)

    try:
        handoff_service.handoff_theme_candidate_to_expert_ai(
            client, azure_client=object(), model="gpt", candidate_id="cand-1",
            actor_id="human-1", user_id="human-1", requester_context=_CTX)
        assert False, "Intake作成失敗時にエラーにならなかった"
    except handoff_service.ThemeCandidateHandoffError:
        pass

    assert client.tables["geo_ios_analysis_sessions"] == []
    assert candidate_service.get_candidate(client, "cand-1")["status"] == "NEW"


# ---- Case F: 別部門Internal Reportの非漏洩 -----------------------------------------

def test_security_different_department_report_never_leaks_into_internal_context():
    client = FakeSupabaseClient(tables=_tables(
        candidates=[_candidate_row()],
        reports=[_report("r-other", allowed_org_units=["other-dept"], summary="他部門の極秘分析",
                          key_actors=["White House"], country_region=["Washington"],
                          keywords=["PHASE2G5"])],
    ))
    _mock_search_rpc(client, ["r-other"])
    result = handoff_service.handoff_theme_candidate_to_expert_ai(
        client, azure_client=object(), model="gpt", candidate_id="cand-1",
        actor_id="human-1", user_id="human-1", requester_context=_CTX)
    context = intake_service.get_current_context(client, result["session"]["analysis_session_id"])
    assert "他部門の極秘分析" not in context["internal_context"]
    assert "他部門" not in str(context["reference_materials"])


# ---- Concurrent Idempotency（2026-10-01修正: 逐次チェックだけでは防げないrace対策） ----

def test_concurrent_handoff_requests_create_only_one_session():
    """§Concurrent Idempotency: 同一candidateへの本当に同時のHandoff request（Double-click）
    でも、session_service.find_or_create_session_for_theme_candidateが使う
    geo_handoff_create_session_if_absent RPC（advisory lockでcheck-and-createを直列化）
    により、Sessionが1件しか作られないことをthreading.Barrierで実際の競合を起こして確認する。"""
    import threading

    client = FakeSupabaseClient(tables=_tables(candidates=[_candidate_row()]))
    barrier = threading.Barrier(2)
    results = [None, None]
    errors = []

    def _run(i):
        barrier.wait()  # 2スレッドが本当に同時にfind_or_create_session_for_theme_candidateへ入る
        try:
            results[i] = handoff_service.handoff_theme_candidate_to_expert_ai(
                client, azure_client=object(), model="gpt", candidate_id="cand-1",
                actor_id="human-1", user_id="human-1", requester_context=_CTX)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=_run, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"並行Handoffで例外が発生した: {errors}"
    assert all(r is not None for r in results)

    session_ids = {r["session"]["analysis_session_id"] for r in results}
    assert len(session_ids) == 1, f"並行Handoffで異なるsession_idが返った: {session_ids}"

    created_flags = sorted(r["created"] for r in results)
    assert created_flags == [False, True], "片方だけcreated=True、もう片方created=Falseになるはず"

    sessions_for_candidate = [
        r for r in client.tables["geo_ios_analysis_sessions"]
        if r.get("source_theme_candidate_id") == "cand-1"]
    assert len(sessions_for_candidate) == 1, "Session行が複数作られてしまった（race再発）"

    contexts_for_session = [
        r for r in client.tables["geo_ios_analysis_contexts"]
        if r.get("analysis_session_id") == sessions_for_candidate[0]["analysis_session_id"]]
    assert len(contexts_for_session) == 1, "orphan/重複Intake Contextが作られた"

    assert candidate_service.get_candidate(client, "cand-1")["status"] == "HANDED_OFF"


def test_missing_candidate_raises():
    client = FakeSupabaseClient(tables=_tables())
    try:
        handoff_service.handoff_theme_candidate_to_expert_ai(
            client, azure_client=object(), model="gpt", candidate_id="missing",
            actor_id="human-1", user_id="human-1")
        assert False, "存在しないcandidate_idでエラーにならなかった"
    except handoff_service.ThemeCandidateHandoffError:
        pass
