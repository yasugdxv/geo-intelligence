"""Phase 2F-4: Intelligence AI True Multi-turn Chat。

既存Retrieval/Analysis Core（INTELLIGENCE_AI_GROUNDED_ANALYSIS_V1、Freeze済み）は無変更で
呼ばれることと、各turnで現在のIdentityを使ってPermission/Retrievalが再評価される
（前turnの結果を信用しない）ことを検証する。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_intelligence_ai_chat_service as chat_service  # noqa: E402
import geo_knowledge_access_policy as policy  # noqa: E402
import geo_llm_common  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _report(report_id: str, **overrides) -> dict:
    row = {
        "report_id": report_id, "document_id": f"doc-{report_id}", "author": "analyst",
        "owner_department": "geopolitics", "report_type": "assessment",
        "country_region": ["Iran"], "topics": ["Hormuz"], "confidentiality": "internal",
        "report_kind": "internal_report", "uploaded_by": "owner-1", "version": 1,
        "supersedes_id": None, "report_identity_id": report_id, "is_demo": False,
        "human_visibility": "custom", "agent_sharing_policy": "none",
        "allowed_org_units": ["geopolitics"], "allowed_domains": [],
        "key_actors": ["Iran"], "keywords": ["Hormuz"], "summary": "Hormuz関連の分析",
        "business_context": None, "uploaded_at": "2026-09-01T00:00:00+00:00",
    }
    row.update(overrides)
    return row


def _tables(reports=None) -> dict:
    return {
        "geo_reports": reports or [], "geo_knowledge_items": [], "geo_documents": [],
        "geo_themes": [], "geo_expert_review_sessions": [],
    }


def _fake_analysis_llm(resolved_query_capture=None, answer="整理結果"):
    """call_llm_structuredは2種類の呼び出しに使われる（query resolution / analysis本体）。
    schema_nameで区別し、それぞれ妥当なdataを返す。"""
    def _call(azure_client, model, system_prompt, user_prompt, schema, schema_name, **kwargs):
        if schema_name == "geo_intelligence_ai_chat_query_resolution_v1":
            if resolved_query_capture is not None:
                resolved_query_capture["user_prompt"] = user_prompt
            return {"data": {"resolved_query": "Hormuz 迂回"}}
        return {"data": {
            "answer": answer, "insufficient_evidence": False, "findings": [],
            "coverage": {"regions": [], "themes": [], "key_actors": []},
            "temporal_notes": None, "warnings": [],
        }}
    return _call


def test_resolve_current_query_skips_llm_when_history_empty(monkeypatch):
    def _raise_if_called(*a, **kw):
        raise AssertionError("historyが空なのにquery resolutionのLLMが呼ばれた")
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _raise_if_called)

    resolved = chat_service.resolve_current_query(
        azure_client=object(), model="gpt", history=[], message="Hormuzについて教えて")
    assert resolved == "Hormuzについて教えて"


def test_resolve_current_query_calls_llm_when_history_present(monkeypatch):
    captured = {}

    def _fake(azure_client, model, system_prompt, user_prompt, schema, schema_name, **kwargs):
        captured["user_prompt"] = user_prompt
        captured["system_prompt"] = system_prompt
        return {"data": {"resolved_query": "Hormuz 迂回ルート"}}
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _fake)

    resolved = chat_service.resolve_current_query(
        azure_client=object(), model="gpt",
        history=[{"role": "user", "content": "Hormuzについて教えて"},
                 {"role": "assistant", "content": "Hormuzは..."}],
        message="それについてもっと詳しく")
    assert resolved == "Hormuz 迂回ルート"
    assert "それについてもっと詳しく" in captured["user_prompt"]
    assert "Hormuzについて教えて" in captured["user_prompt"]


def test_chat_calls_frozen_analyze_with_resolved_query_and_current_identity(monkeypatch):
    client = FakeSupabaseClient(tables=_tables(reports=[
        _report("r1", allowed_org_units=["geopolitics"]),
    ]))

    def _fake_rpc(fn_name, params=None):
        if fn_name == "search_geo_reports":
            return [{"report_id": "r1", "text_score": 0.5, "snippet": None}]
        return []
    client.rpc = _fake_rpc

    captured = {}
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _fake_analysis_llm(captured))

    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    result = chat_service.chat(
        client, azure_client=object(), model="gpt",
        history=[{"role": "user", "content": "Hormuzについて教えて"},
                 {"role": "assistant", "content": "Hormuzは..."}],
        message="それについてもっと詳しく", requester_context=ctx)

    assert result["resolved_query"] == "Hormuz 迂回"
    assert result["insufficient_evidence"] is False
    assert "それについてもっと詳しく" in captured["user_prompt"]


def test_chat_does_not_reuse_previous_turn_permission_different_identity_per_turn(monkeypatch):
    """§絶対条件: 会話が続いていても、turnごとに渡されたrequester_contextで毎回Permissionを
    再評価する（同じhistoryでも、部門が違えばcontextに入るSourceが変わる）。"""
    client = FakeSupabaseClient(tables=_tables(reports=[
        _report("r-geo", allowed_org_units=["geopolitics"]),
    ]))

    def _fake_rpc(fn_name, params=None):
        if fn_name == "search_geo_reports":
            return [{"report_id": "r-geo", "text_score": 0.5, "snippet": None}]
        return []
    client.rpc = _fake_rpc
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _fake_analysis_llm())

    history = [{"role": "user", "content": "前のturn"}, {"role": "assistant", "content": "前の回答"}]

    ctx_same_dept = policy.build_requester_context(
        user_id="u1", department="geopolitics", is_authenticated=True)
    result_same = chat_service.chat(client, azure_client=object(), model="gpt",
                                     history=history, message="続き", requester_context=ctx_same_dept)
    assert len(result_same["source_refs"]) == 1

    ctx_other_dept = policy.build_requester_context(
        user_id="u2", department="other-dept", is_authenticated=True)
    result_other = chat_service.chat(client, azure_client=object(), model="gpt",
                                      history=history, message="続き", requester_context=ctx_other_dept)
    assert result_other["insufficient_evidence"] is True
    assert result_other["source_refs"] == []


def test_chat_empty_history_first_turn_uses_message_as_is(monkeypatch):
    client = FakeSupabaseClient(tables=_tables(reports=[
        _report("r1", allowed_org_units=["geopolitics"]),
    ]))

    def _fake_rpc(fn_name, params=None):
        if fn_name == "search_geo_reports":
            return [{"report_id": "r1", "text_score": 0.5, "snippet": None}]
        return []
    client.rpc = _fake_rpc
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _fake_analysis_llm())

    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    result = chat_service.chat(client, azure_client=object(), model="gpt",
                                history=[], message="Hormuzについて教えて", requester_context=ctx)
    assert result["resolved_query"] == "Hormuzについて教えて"
