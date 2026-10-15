"""Phase 2F-3: Cross-report Grounded AI Analysis。

§15 Hallucination Guard（存在しないref_idの引用を落とす／sourceが無ければLLMを呼ばず
insufficient_evidence）、§11 No Duplicate Source Inflation / §8 Version Awareness
（同一lineageの重複排除、approved優先）、§16 Security（Unauthorized sourceがcontext/
source_refsのどこにも現れないこと、selected sourceのPermission再検証）を検証する。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_intelligence_ai_analysis_service as analysis_service  # noqa: E402
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


def _knowledge_item(item_id: str, **overrides) -> dict:
    row = {
        "knowledge_item_id": item_id, "knowledge_type": "approved_report",
        "title": "Hormuz Approved Report", "summary": "承認済みレポート", "status": "active",
        "retrieval_status": "available", "is_demo": False, "created_by": "steward-1",
        "human_visibility": "company", "agent_sharing_policy": "custom", "allowed_org_units": [],
        "allowed_domains": [], "source_report_id": None,
        "structured_content": {"source_report_id": None, "approval_date": "2026-09-10"},
    }
    row.update(overrides)
    return row


def _tables(reports=None, knowledge_items=None, documents=None, review_sessions=None) -> dict:
    return {
        "geo_reports": reports or [], "geo_knowledge_items": knowledge_items or [],
        "geo_documents": documents or [], "geo_themes": [],
        "geo_expert_review_sessions": review_sessions or [],
    }


def _fake_llm(answer="整理結果", findings=None, insufficient_evidence=False, coverage=None,
              temporal_notes=None, warnings=None):
    findings = findings if findings is not None else []
    coverage = coverage or {"regions": [], "themes": [], "key_actors": []}

    def _call(azure_client, model, system_prompt, user_prompt, schema, schema_name, **kwargs):
        _call.last_user_prompt = user_prompt
        return {"data": {
            "answer": answer, "insufficient_evidence": insufficient_evidence, "findings": findings,
            "coverage": coverage, "temporal_notes": temporal_notes, "warnings": warnings or [],
        }}
    _call.last_user_prompt = None
    return _call


# ---- §15 Hallucination Guard ------------------------------------------------------

def test_analyze_returns_insufficient_evidence_without_calling_llm_when_no_eligible_sources(monkeypatch):
    client = FakeSupabaseClient(tables=_tables(reports=[
        _report("r1", allowed_org_units=["sustainability"]),  # 別部門、permission不合格
    ]))

    def _raise_if_called(*a, **kw):
        raise AssertionError("Sourceが無いのにLLMが呼ばれた")
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _raise_if_called)

    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    result = analysis_service.analyze(client, azure_client=object(), model="gpt", query_text="Hormuz",
                                       requester_context=ctx)
    assert result["insufficient_evidence"] is True
    assert result["findings"] == []
    assert result["source_refs"] == []


def test_analyze_drops_findings_citing_unknown_ref_id(monkeypatch):
    client = FakeSupabaseClient(tables=_tables(reports=[
        _report("r1", allowed_org_units=["geopolitics"]),
    ]))

    def _fake_rpc(fn_name, params=None):
        if fn_name == "search_geo_reports":
            return [{"report_id": "r1", "text_score": 0.5, "snippet": "Hormuz snippet"}]
        return []
    client.rpc = _fake_rpc

    fake_llm = _fake_llm(findings=[
        {"claim": "捏造されたsourceからの主張", "claim_type": "source_supported", "source_refs": ["S99"]},
        {"claim": "正当な主張", "claim_type": "source_supported", "source_refs": ["S1"]},
    ])
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake_llm)

    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    result = analysis_service.analyze(client, azure_client=object(), model="gpt", query_text="Hormuz",
                                       requester_context=ctx)

    assert len(result["findings"]) == 1
    assert result["findings"][0]["claim"] == "正当な主張"
    assert any("finding_dropped" in w for w in result["warnings"])


# ---- §11 No Duplicate Source Inflation / §8 Version Awareness ---------------------

def test_analyze_dedupes_report_and_its_own_knowledge_item_into_one_lineage(monkeypatch):
    client = FakeSupabaseClient(tables=_tables(
        reports=[_report("r1", allowed_org_units=["geopolitics"], review_status="approved")],
        knowledge_items=[_knowledge_item(
            "k1", human_visibility="company", source_report_id="r1",
            structured_content={"source_report_id": "r1", "approval_date": "2026-09-10"})],
    ))

    def _fake_rpc(fn_name, params=None):
        if fn_name == "search_geo_reports":
            return [{"report_id": "r1", "text_score": 0.5, "snippet": None}]
        if fn_name == "search_geo_knowledge_items":
            return [{"knowledge_item_id": "k1", "text_score": 0.9, "snippet": None}]
        return []
    client.rpc = _fake_rpc

    fake_llm = _fake_llm()
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake_llm)

    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    analysis_service.analyze(client, azure_client=object(), model="gpt", query_text="Hormuz",
                              requester_context=ctx)

    # 同一Report由来のReport-hit/Knowledge-hitが1つのref（=1 context source）に統合されている
    assert fake_llm.last_user_prompt.count("[S1]") >= 1
    assert "[S2]" not in fake_llm.last_user_prompt


def test_analyze_prefers_approved_version_over_uploaded_only_version():
    entries = [
        {"source_type": "report", "report_id": "r1v1", "report_identity_id": "identity-1",
         "review_status": "uploaded_only", "version": 1, "text_score": 0.5, "date": "2026-01-01"},
        {"source_type": "report", "report_id": "r1v2", "report_identity_id": "identity-1",
         "review_status": "approved", "version": 2, "text_score": 0.3, "date": "2026-02-01"},
    ]
    client = FakeSupabaseClient(tables=_tables())
    deduped = analysis_service._dedupe_by_lineage(client, entries)
    assert len(deduped) == 1
    assert deduped[0]["report_id"] == "r1v2"


# ---- §16 Security -------------------------------------------------------------

def _security_client():
    return FakeSupabaseClient(tables=_tables(reports=[
        _report("r-own-dept", allowed_org_units=["geopolitics"], summary="味方部門の秘密分析"),
        _report("r-other-dept", allowed_org_units=["other-dept"], summary="他部門の極秘分析"),
    ], knowledge_items=[
        _knowledge_item("k-own-dept", human_visibility="custom", agent_sharing_policy="none",
                         allowed_org_units=["geopolitics"], title="味方部門Knowledge"),
        _knowledge_item("k-other-dept", human_visibility="custom", agent_sharing_policy="none",
                         allowed_org_units=["other-dept"], title="他部門Knowledge"),
    ]))


def _rpc_all(client):
    def _fake_rpc(fn_name, params=None):
        if fn_name == "search_geo_reports":
            return [{"report_id": rid, "text_score": 0.5, "snippet": None}
                     for rid in ("r-own-dept", "r-other-dept")]
        if fn_name == "search_geo_knowledge_items":
            return [{"knowledge_item_id": kid, "text_score": 0.5, "snippet": None}
                     for kid in ("k-own-dept", "k-other-dept")]
        return []
    client.rpc = _fake_rpc


def test_security_1_and_2_same_department_only_reaches_context(monkeypatch):
    client = _security_client()
    _rpc_all(client)
    fake_llm = _fake_llm()
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake_llm)

    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    analysis_service.analyze(client, azure_client=object(), model="gpt", query_text="Hormuz",
                              requester_context=ctx)

    prompt = fake_llm.last_user_prompt
    assert "味方部門の秘密分析" in prompt or "味方部門Knowledge" in prompt
    assert "他部門の極秘分析" not in prompt
    assert "他部門Knowledge" not in prompt


def test_security_3_unauthorized_selected_source_is_rejected():
    client = _security_client()
    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    entries, unauthorized_count = analysis_service._resolve_selected_sources(
        client, [
            {"source_type": "report", "id": "r-own-dept"},
            {"source_type": "report", "id": "r-other-dept"},
        ], requester_context=ctx, is_internal_caller=True, caller_domain=None, include_demo=False)
    assert [e["report_id"] for e in entries] == ["r-own-dept"]
    assert unauthorized_count == 1


def test_security_4_source_refs_never_contains_unauthorized_title(monkeypatch):
    client = _security_client()
    _rpc_all(client)
    fake_llm = _fake_llm(findings=[
        {"claim": "整理", "claim_type": "source_supported", "source_refs": ["S1", "S2"]},
    ])
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake_llm)

    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    result = analysis_service.analyze(client, azure_client=object(), model="gpt", query_text="Hormuz",
                                       requester_context=ctx)
    titles = [s.get("title") for s in result["source_refs"]]
    assert "他部門の極秘分析" not in titles
    assert "他部門Knowledge" not in titles


def test_security_5_anonymous_is_fail_closed(monkeypatch):
    client = _security_client()
    _rpc_all(client)

    def _raise_if_called(*a, **kw):
        raise AssertionError("anonymousなのにLLMが呼ばれた")
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _raise_if_called)

    ctx = policy.build_requester_context(user_id=None, department=None, is_authenticated=False)
    result = analysis_service.analyze(client, azure_client=object(), model="gpt", query_text="Hormuz",
                                       requester_context=ctx)
    assert result["insufficient_evidence"] is True


def test_security_6_both_report_and_knowledge_item_permission_enforced(monkeypatch):
    client = _security_client()
    _rpc_all(client)
    fake_llm = _fake_llm()
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake_llm)

    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    analysis_service.analyze(client, azure_client=object(), model="gpt", query_text="Hormuz",
                              requester_context=ctx)
    prompt = fake_llm.last_user_prompt
    assert "report" in prompt or "knowledge_item" in prompt
    assert "他部門" not in prompt


def test_security_7_selected_source_permission_rechecked_per_request():
    client = _security_client()
    refs = [{"source_type": "report", "id": "r-other-dept"}]

    ctx_geo = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    entries_geo, _ = analysis_service._resolve_selected_sources(
        client, refs, requester_context=ctx_geo, is_internal_caller=True, caller_domain=None,
        include_demo=False)
    assert entries_geo == []

    ctx_other = policy.build_requester_context(user_id="u2", department="other-dept", is_authenticated=True)
    entries_other, _ = analysis_service._resolve_selected_sources(
        client, refs, requester_context=ctx_other, is_internal_caller=True, caller_domain=None,
        include_demo=False)
    assert [e["report_id"] for e in entries_other] == ["r-other-dept"]
