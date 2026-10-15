"""Phase 2G-4: Theme Candidate Wall-bounce Chat。

Validation Cases（1〜5のうちChat/Proposal部分）と§Security（別部門Reportが
answer/proposal/source_refsのどこにも出ないこと、turnごとにPermissionを再評価すること）、
malformed LLM output fail-safeを検証する。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_knowledge_access_policy as policy  # noqa: E402
import geo_llm_common  # noqa: E402
import geo_theme_candidate_service as candidate_service  # noqa: E402
import geo_theme_candidate_wallbounce_service as wallbounce_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _candidate_row(**overrides) -> dict:
    row = {
        "candidate_id": "cand-1", "candidate_fingerprint": "fp-1",
        "title": "PHASE2G4 White House", "question": "規制強化そのものではなく運用変化なのか？",
        "signal_summary": "複数Actorが同時期に関連行動", "observed_change": "...", "why_now": "...",
        "actors": ["White House"], "regions": ["Washington"], "themes": [],
        "business_relevance": ["regulatory"], "candidate_type": "changing", "change_type": "policy_shift",
        "source_episode_cluster_ids": [], "source_situation_link_ids": [],
        "source_external_signal_ids": [], "source_external_event_ids": [],
        "first_seen_at": "2026-09-01T00:00:00+00:00", "latest_signal_at": "2026-09-01T00:00:00+00:00",
        "signal_count": 1, "status": "NEW",
    }
    row.update(overrides)
    return row


def _report(report_id: str, **overrides) -> dict:
    row = {
        "report_id": report_id, "document_id": f"doc-{report_id}", "author": "analyst",
        "owner_department": "geopolitics", "report_type": "assessment",
        "country_region": ["Washington"], "topics": ["PHASE2G4"], "confidentiality": "internal",
        "report_kind": "internal_report", "uploaded_by": "owner-1", "version": 1,
        "supersedes_id": None, "report_identity_id": report_id, "is_demo": False,
        "human_visibility": "custom", "agent_sharing_policy": "none",
        "allowed_org_units": ["geopolitics"], "allowed_domains": [],
        "key_actors": ["White House"], "keywords": ["PHASE2G4"], "summary": "White House関連の分析",
        "business_context": None, "uploaded_at": "2026-09-01T00:00:00+00:00",
    }
    row.update(overrides)
    return row


def _tables(candidates=None, reports=None) -> dict:
    return {
        "geo_theme_candidates": candidates or [], "geo_reports": reports or [],
        "geo_knowledge_items": [], "geo_documents": [], "geo_themes": [],
        "geo_expert_review_sessions": [], "geo_episode_clusters": [], "geo_episode_cluster_links": [],
        "geo_external_signals": [], "geo_external_events": [],
    }


def _fake_llm(answer="整理しました", has_proposal=False, proposal=None):
    proposal = proposal or {
        "proposed_title": None, "proposed_question": None, "proposed_actors": [],
        "proposed_regions": [], "proposed_scope": None, "investigation_questions": [], "rationale": None,
    }

    def _call(azure_client, model, system_prompt, user_prompt, schema, schema_name, **kwargs):
        _call.last_user_prompt = user_prompt
        return {"data": {"answer": answer, "has_proposal": has_proposal, "proposal": proposal}}
    _call.last_user_prompt = None
    return _call


def _rpc_returning(report_ids: list):
    def _fake_rpc(fn_name, params=None):
        if fn_name == "search_geo_reports":
            eligible = set(params.get("p_eligible_ids") or [])
            return [{"report_id": rid, "text_score": 0.5, "snippet": None}
                     for rid in report_ids if rid in eligible]
        return []
    return _fake_rpc


# ---- 基本動作 -----------------------------------------------------------------------

def test_chat_raises_for_missing_candidate():
    client = FakeSupabaseClient(tables=_tables())
    try:
        wallbounce_service.chat(client, azure_client=object(), model="gpt", candidate_id="missing",
                                 history=[], message="このテーマは広すぎない？")
        assert False, "存在しないcandidate_idでエラーにならなかった"
    except candidate_service.ThemeCandidateError:
        pass


def test_chat_first_turn_includes_candidate_context_in_prompt(monkeypatch):
    client = FakeSupabaseClient(tables=_tables(candidates=[_candidate_row()]))
    fake_llm = _fake_llm()
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake_llm)

    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    result = wallbounce_service.chat(client, azure_client=object(), model="gpt", candidate_id="cand-1",
                                      history=[], message="このテーマは広すぎない？",
                                      requester_context=ctx)
    assert result["answer"] == "整理しました"
    assert result["has_proposal"] is False
    assert "PHASE2G4 White House" in fake_llm.last_user_prompt
    assert "このテーマは広すぎない？" in fake_llm.last_user_prompt


def test_chat_returns_structured_proposal_when_present(monkeypatch):
    client = FakeSupabaseClient(tables=_tables(candidates=[_candidate_row()]))
    proposal = {
        "proposed_title": "米国側起点のAI半導体政策シフト", "proposed_question": "米国の裁量確保か？",
        "proposed_actors": ["White House"], "proposed_regions": ["Washington"],
        "proposed_scope": "米国側に限定", "investigation_questions": ["議会動向を確認"],
        "rationale": "中国側よりも米国側の運用変化が観測の中心であるため",
    }
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _fake_llm(has_proposal=True, proposal=proposal))

    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    result = wallbounce_service.chat(client, azure_client=object(), model="gpt", candidate_id="cand-1",
                                      history=[], message="中国側ではなく米国側から見るべき？",
                                      requester_context=ctx)
    assert result["has_proposal"] is True
    assert result["proposal"]["proposed_scope"] == "米国側に限定"
    assert result["proposal"]["investigation_questions"] == ["議会動向を確認"]


def test_malformed_llm_output_is_fail_safe(monkeypatch):
    def _raise(*a, **kw):
        raise geo_llm_common.GeoLLMError("schema検証失敗", attempts=[], exception_type="ValidationError")
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _raise)
    client = FakeSupabaseClient(tables=_tables(candidates=[_candidate_row()]))

    result = wallbounce_service.chat(client, azure_client=object(), model="gpt", candidate_id="cand-1",
                                      history=[], message="追加で何を確認すべき？")
    assert result["has_proposal"] is False
    assert result["answer"]  # 空文字ではない、何らかの安全な既定メッセージが返る


# ---- §Security: Internal Coverage由来のUnauthorized漏洩防止 ------------------------

def test_security_different_department_report_never_leaks_into_answer_or_proposal(monkeypatch):
    client = FakeSupabaseClient(tables=_tables(
        candidates=[_candidate_row()],
        reports=[
            _report("r-own", allowed_org_units=["geopolitics"], summary="味方部門の内部分析"),
            _report("r-other", allowed_org_units=["other-dept"], summary="他部門の極秘分析",
                     key_actors=["White House"], country_region=["Washington"], keywords=["PHASE2G4"]),
        ],
    ))
    client.rpc = _rpc_returning(["r-own", "r-other"])
    fake_llm = _fake_llm()
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake_llm)

    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    result = wallbounce_service.chat(client, azure_client=object(), model="gpt", candidate_id="cand-1",
                                      history=[], message="既存社内分析との差は？", requester_context=ctx)

    assert "他部門の極秘分析" not in fake_llm.last_user_prompt
    refs_titles = [s.get("title") for s in result["source_refs"]]
    assert "他部門の極秘分析" not in refs_titles
    assert not any("他部門" in str(v) for v in result.values())


def test_security_each_turn_reevaluates_permission_for_current_identity(monkeypatch):
    """§絶対条件: 同じCandidate・同じ会話historyでも、turnごとにrequester_contextを再評価する
    （前turnのInternal Coverage結果を引き継がない）。"""
    client = FakeSupabaseClient(tables=_tables(
        candidates=[_candidate_row()],
        reports=[_report("r-geo", allowed_org_units=["geopolitics"])],
    ))
    client.rpc = _rpc_returning(["r-geo"])
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _fake_llm())

    history = [{"role": "user", "content": "前のturn"}, {"role": "assistant", "content": "前の回答"}]

    ctx_same = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    result_same = wallbounce_service.chat(client, azure_client=object(), model="gpt", candidate_id="cand-1",
                                           history=history, message="続き", requester_context=ctx_same)
    assert len(result_same["source_refs"]) == 1

    ctx_other = policy.build_requester_context(user_id="u2", department="other-dept", is_authenticated=True)
    result_other = wallbounce_service.chat(client, azure_client=object(), model="gpt", candidate_id="cand-1",
                                            history=history, message="続き", requester_context=ctx_other)
    assert result_other["source_refs"] == []
