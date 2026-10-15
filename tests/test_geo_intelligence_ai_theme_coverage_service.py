"""Phase 2F-6: Theme Discovery Internal Intelligence Bridge。

§16 Security Test（No Hidden-corpus Leakage含む）と§17 Functional Validation Cases
（Case A/B/C相当、Unauthorized Reportを混ぜても結果へ影響しないこと）を検証する。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_intelligence_ai_theme_coverage_service as coverage_service  # noqa: E402
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
        "key_actors": ["Iran", "US"], "keywords": ["Hormuz"], "summary": "Hormuz関連の分析",
        "business_context": None, "uploaded_at": "2026-09-01T00:00:00+00:00",
    }
    row.update(overrides)
    return row


def _tables(reports=None, knowledge_items=None) -> dict:
    return {
        "geo_reports": reports or [], "geo_knowledge_items": knowledge_items or [],
        "geo_documents": [], "geo_themes": [], "geo_expert_review_sessions": [],
    }


def _rpc_returning(report_ids: list):
    def _fake_rpc(fn_name, params=None):
        if fn_name == "search_geo_reports":
            eligible = set(params.get("p_eligible_ids") or [])
            return [{"report_id": rid, "text_score": 0.5, "snippet": None}
                     for rid in report_ids if rid in eligible]
        return []
    return _fake_rpc


THEME_CANDIDATE = {
    "theme_title": "Hormuz",
    "keywords": ["Hormuz", "China"],
    "actors": ["US", "China"],
    "regions": ["Iran"],
}


# ---- §16 Security Tests -----------------------------------------------------------

def test_security_1_same_department_caller_sees_accessible_reports():
    client = FakeSupabaseClient(tables=_tables(reports=[
        _report("r1", allowed_org_units=["geopolitics"], review_status="approved"),
    ]))
    client.rpc = _rpc_returning(["r1"])
    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    result = coverage_service.assess_internal_coverage(
        client, azure_client=None, model="gpt", theme_candidate=THEME_CANDIDATE, requester_context=ctx)
    assert result["coverage"]["accessible_report_count"] == 1


def test_security_2_other_department_report_never_counted():
    client = FakeSupabaseClient(tables=_tables(reports=[
        _report("r-own", allowed_org_units=["geopolitics"]),
        _report("r-other", allowed_org_units=["other-dept"], title="他部門限定"),
    ]))
    client.rpc = _rpc_returning(["r-own", "r-other"])
    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    result = coverage_service.assess_internal_coverage(
        client, azure_client=None, model="gpt", theme_candidate=THEME_CANDIDATE, requester_context=ctx)
    assert result["coverage"]["accessible_report_count"] == 1
    titles = [s["title"] for s in result["similar_internal_intelligence"]]
    assert "他部門限定" not in titles


def test_security_3_unauthorized_only_gives_zero_coverage():
    client = FakeSupabaseClient(tables=_tables(reports=[
        _report("r-other", allowed_org_units=["other-dept"]),
    ]))
    client.rpc = _rpc_returning(["r-other"])
    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    result = coverage_service.assess_internal_coverage(
        client, azure_client=None, model="gpt", theme_candidate=THEME_CANDIDATE, requester_context=ctx)
    assert result["coverage"]["accessible_report_count"] == 0
    assert result["coverage"]["accessible_knowledge_count"] == 0
    assert result["similar_internal_intelligence"] == []


def test_security_4_no_hidden_corpus_warning_leak():
    """Unauthorized Reportが存在しても、その存在を示唆する文言を一切返さない。"""
    client = FakeSupabaseClient(tables=_tables(reports=[
        _report("r-other", allowed_org_units=["other-dept"]),
    ]))
    client.rpc = _rpc_returning(["r-other"])
    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    result = coverage_service.assess_internal_coverage(
        client, azure_client=None, model="gpt", theme_candidate=THEME_CANDIDATE, requester_context=ctx)
    assert result["warnings"] == []


def test_security_5_anonymous_fail_closed():
    client = FakeSupabaseClient(tables=_tables(reports=[
        _report("r1", allowed_org_units=["geopolitics"]),
    ]))
    client.rpc = _rpc_returning(["r1"])
    ctx = policy.build_requester_context(user_id=None, department=None, is_authenticated=False)
    result = coverage_service.assess_internal_coverage(
        client, azure_client=None, model="gpt", theme_candidate=THEME_CANDIDATE, requester_context=ctx)
    assert result["coverage"]["accessible_report_count"] == 0


def test_security_6_source_refs_only_from_accessible():
    client = FakeSupabaseClient(tables=_tables(reports=[
        _report("r-own", allowed_org_units=["geopolitics"]),
        _report("r-other", allowed_org_units=["other-dept"]),
    ]))
    client.rpc = _rpc_returning(["r-own", "r-other"])
    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    result = coverage_service.assess_internal_coverage(
        client, azure_client=None, model="gpt", theme_candidate=THEME_CANDIDATE, requester_context=ctx)
    refs = [s["source_ref"] for s in result["similar_internal_intelligence"]]
    assert refs == ["r-own"]


def test_security_7_agent_caller_uses_can_agent_access():
    client = FakeSupabaseClient(tables=_tables(reports=[
        _report("r1", allowed_org_units=["geopolitics"], agent_sharing_policy="custom",
                allowed_domains=["partner-domain"]),
    ]))
    client.rpc = _rpc_returning(["r1"])
    result_denied = coverage_service.assess_internal_coverage(
        client, azure_client=None, model="gpt", theme_candidate=THEME_CANDIDATE,
        is_internal_caller=False, caller_domain="unrelated-domain")
    assert result_denied["coverage"]["accessible_report_count"] == 0

    result_allowed = coverage_service.assess_internal_coverage(
        client, azure_client=None, model="gpt", theme_candidate=THEME_CANDIDATE,
        is_internal_caller=False, caller_domain="partner-domain")
    assert result_allowed["coverage"]["accessible_report_count"] == 1


# ---- §17 Functional Validation Cases ------------------------------------------------

def test_case_a_existing_analysis_present():
    client = FakeSupabaseClient(tables=_tables(
        reports=[
            _report("r-approved", allowed_org_units=["geopolitics"],
                    uploaded_at="2026-08-14T00:00:00+00:00"),
            _report("r-other-dept", allowed_org_units=["other-dept"]),
        ],
        knowledge_items=[
            {"knowledge_item_id": "k1", "knowledge_type": "approved_report", "status": "active",
             "source_report_id": "r-approved"},
        ],
    ))
    client.rpc = _rpc_returning(["r-approved", "r-other-dept"])
    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    result = coverage_service.assess_internal_coverage(
        client, azure_client=None, model="gpt", theme_candidate=THEME_CANDIDATE, requester_context=ctx)
    assert result["coverage"]["accessible_report_count"] == 1
    assert result["coverage"]["approved_report_count"] == 1
    assert result["recency"]["latest_report_date"] == "2026-08-14T00:00:00+00:00"
    assert result["recency"]["latest_approved_report_date"] == "2026-08-14T00:00:00+00:00"


def test_case_b_old_analysis_only_returns_raw_date_no_threshold_judgment():
    client = FakeSupabaseClient(tables=_tables(reports=[
        _report("r-old", allowed_org_units=["geopolitics"], review_status="uploaded_only",
                uploaded_at="2025-01-01T00:00:00+00:00"),
        _report("r-other-dept", allowed_org_units=["other-dept"]),
    ]))
    client.rpc = _rpc_returning(["r-old", "r-other-dept"])
    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    result = coverage_service.assess_internal_coverage(
        client, azure_client=None, model="gpt", theme_candidate=THEME_CANDIDATE, requester_context=ctx)
    assert result["recency"]["latest_report_date"] == "2025-01-01T00:00:00+00:00"
    assert result["recency"]["latest_approved_report_date"] is None
    # Phase 2F-6では「古い/新しい」の閾値判定は行わない（raw dateのみ）
    assert "stale" not in result
    assert "is_outdated" not in result


def test_case_c_no_internal_coverage_returns_empty_without_hallucination():
    client = FakeSupabaseClient(tables=_tables(reports=[
        _report("r-other-dept", allowed_org_units=["other-dept"]),
    ]))
    client.rpc = _rpc_returning(["r-other-dept"])
    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    result = coverage_service.assess_internal_coverage(
        client, azure_client=None, model="gpt", theme_candidate=THEME_CANDIDATE, requester_context=ctx)
    assert result["coverage"]["accessible_report_count"] == 0
    assert result["similar_internal_intelligence"] == []
    assert result["existing_analysis_themes"] == []
    assert result["stage"] == "metadata_only"
    # 候補側のkeywords/actors/regionsが全てgapとして返る（架空のcoverageを主張しない）
    assert set(result["potential_coverage_gaps"]) == {"Hormuz", "China", "US", "Iran"}


# ---- Stage 2 (include_analysis) ----------------------------------------------------

def test_stage2_reuses_existing_analyze_findings_verbatim(monkeypatch):
    client = FakeSupabaseClient(tables=_tables(reports=[
        _report("r1", allowed_org_units=["geopolitics"]),
    ]))
    client.rpc = _rpc_returning(["r1"])

    def _fake_llm(azure_client, model, system_prompt, user_prompt, schema, schema_name, **kwargs):
        return {"data": {
            "answer": "整理結果", "insufficient_evidence": False,
            "findings": [{"claim": "White Houseの裁量が主論点", "claim_type": "source_supported",
                          "source_refs": ["S1"]}],
            "coverage": {"regions": ["Iran"], "themes": [], "key_actors": ["US"]},
            "temporal_notes": None, "warnings": [],
        }}
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _fake_llm)

    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    result = coverage_service.assess_internal_coverage(
        client, azure_client=object(), model="gpt", theme_candidate=THEME_CANDIDATE,
        requester_context=ctx, include_analysis=True)
    assert result["stage"] == "with_analysis"
    assert result["existing_analysis_themes"] == [
        {"claim": "White Houseの裁量が主論点", "claim_type": "source_supported", "source_refs": ["S1"]}
    ]
    assert len(result["source_refs"]) == 1


def test_metadata_only_mode_never_calls_llm(monkeypatch):
    client = FakeSupabaseClient(tables=_tables(reports=[
        _report("r1", allowed_org_units=["geopolitics"]),
    ]))
    client.rpc = _rpc_returning(["r1"])

    def _raise_if_called(*a, **kw):
        raise AssertionError("Stage 1（include_analysis=False）なのにLLMが呼ばれた")
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _raise_if_called)

    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    result = coverage_service.assess_internal_coverage(
        client, azure_client=object(), model="gpt", theme_candidate=THEME_CANDIDATE,
        requester_context=ctx, include_analysis=False)
    assert result["stage"] == "metadata_only"
