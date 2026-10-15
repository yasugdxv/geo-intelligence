"""Phase 2F-1: Intelligence AI — Permission-filtered Hybrid Report/Knowledge Search。

§3絶対条件（Identity→Permission Evaluation→Eligible Corpus→Retrieval）と、
§28 Security Testの一部（Unauthorized ReportがRPCへ渡るp_eligible_idsにも、
最終resultsにも一切現れないこと）を検証する。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_intelligence_ai_search_service as search_service  # noqa: E402
import geo_knowledge_access_policy as policy  # noqa: E402
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
        "allowed_domains": [], "structured_content": {"source_report_id": None, "approval_date": "2026-09-10"},
    }
    row.update(overrides)
    return row


def _base_tables(reports=None, knowledge_items=None, documents=None, themes=None,
                  review_sessions=None) -> dict:
    tables = {
        "geo_reports": reports or [], "geo_knowledge_items": knowledge_items or [],
        "geo_documents": documents or [], "geo_themes": themes or [],
        "geo_expert_review_sessions": review_sessions or [],
    }
    return tables


# ---- resolve_eligible_*_ids ------------------------------------------------------

def test_resolve_eligible_report_ids_department_match():
    client = FakeSupabaseClient(tables=_base_tables(reports=[
        _report("r1", allowed_org_units=["geopolitics"]),
        _report("r2", allowed_org_units=["sustainability"]),
    ]))
    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    eligible = search_service.resolve_eligible_report_ids(client, requester_context=ctx)
    assert eligible == {"r1"}


def test_resolve_eligible_report_ids_internal_caller_gets_all():
    client = FakeSupabaseClient(tables=_base_tables(reports=[
        _report("r1", allowed_org_units=["geopolitics"]),
        _report("r2", allowed_org_units=["sustainability"]),
    ]))
    eligible = search_service.resolve_eligible_report_ids(client, is_internal_caller=True)
    assert eligible == {"r1", "r2"}


def test_resolve_eligible_report_ids_cross_domain_agent_denied_by_default():
    client = FakeSupabaseClient(tables=_base_tables(reports=[_report("r1")]))
    eligible = search_service.resolve_eligible_report_ids(
        client, is_internal_caller=False, caller_domain="sustainability")
    assert eligible == set()


def test_resolve_eligible_knowledge_item_ids_only_approved_report_type():
    client = FakeSupabaseClient(tables=_base_tables(knowledge_items=[
        _knowledge_item("k1", knowledge_type="approved_report", human_visibility="company"),
        _knowledge_item("k2", knowledge_type="actor_profile", human_visibility="company"),
    ]))
    ctx = policy.build_requester_context(user_id="u1", is_authenticated=True)
    eligible = search_service.resolve_eligible_knowledge_item_ids(client, requester_context=ctx)
    assert eligible == {"k1"}


def test_resolve_eligible_knowledge_item_ids_excludes_non_retrievable():
    client = FakeSupabaseClient(tables=_base_tables(knowledge_items=[
        _knowledge_item("k1", status="active", retrieval_status="available"),
        _knowledge_item("k2", status="candidate", retrieval_status="restricted"),
    ]))
    ctx = policy.build_requester_context(user_id="u1", is_authenticated=True)
    eligible = search_service.resolve_eligible_knowledge_item_ids(client, requester_context=ctx)
    assert eligible == {"k1"}


# ---- search() 統合 ---------------------------------------------------------------

def test_search_never_calls_rpc_when_no_eligible_reports_or_items(monkeypatch):
    """§3絶対条件: eligible corpusが空ならretrieval（RPC呼び出し）自体を行わない。"""
    client = FakeSupabaseClient(tables=_base_tables(
        reports=[_report("r1", allowed_org_units=["sustainability"])],
        knowledge_items=[_knowledge_item("k1", human_visibility="custom", allowed_org_units=["sustainability"])],
    ))

    def _raise_if_called(*a, **kw):
        raise AssertionError("eligible corpusが空なのにRPCが呼ばれた")
    monkeypatch.setattr(client, "rpc", _raise_if_called)

    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    result = search_service.search(client, "Hormuz", requester_context=ctx)
    assert result == {"results": [], "report_count": 0, "knowledge_item_count": 0}


def test_search_passes_only_eligible_ids_to_rpc():
    """§3絶対条件: RPCへ渡すp_eligible_idsは、権限の無いreport_idを一切含まない。"""
    client = FakeSupabaseClient(tables=_base_tables(
        reports=[
            _report("r-allowed", allowed_org_units=["geopolitics"]),
            _report("r-denied", allowed_org_units=["sustainability"]),
        ],
    ))
    captured = {}

    def _fake_rpc(fn_name, params=None):
        captured[fn_name] = params
        if fn_name == "search_geo_reports":
            return [{"report_id": "r-allowed", "text_score": 0.9}]
        return []
    client.rpc = _fake_rpc

    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    result = search_service.search(client, "Hormuz", requester_context=ctx)

    assert captured["search_geo_reports"]["p_eligible_ids"] == ["r-allowed"]
    assert "r-denied" not in captured["search_geo_reports"]["p_eligible_ids"]
    assert result["report_count"] == 1
    assert result["results"][0]["report_id"] == "r-allowed"


def test_search_defense_in_depth_drops_unauthorized_id_even_if_rpc_returns_it():
    """RPCが（実装バグ等で）eligible外のIDを万一返しても、Pythonの二重チェックで
    最終resultsから除外される（§28 Security Test相当、defense-in-depth）。"""
    client = FakeSupabaseClient(tables=_base_tables(
        reports=[_report("r-allowed", allowed_org_units=["geopolitics"])],
    ))

    def _fake_rpc(fn_name, params=None):
        if fn_name == "search_geo_reports":
            # r-not-eligibleはeligible_report_idsに含まれていないIDを意図的に混入
            return [{"report_id": "r-allowed", "text_score": 0.9},
                    {"report_id": "r-not-eligible", "text_score": 0.95}]
        return []
    client.rpc = _fake_rpc

    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    result = search_service.search(client, "Hormuz", requester_context=ctx)

    ids = [r["report_id"] for r in result["results"]]
    assert "r-not-eligible" not in ids
    assert ids == ["r-allowed"]


def test_search_combines_reports_and_knowledge_items_with_source_type():
    client = FakeSupabaseClient(tables=_base_tables(
        reports=[_report("r1", allowed_org_units=["geopolitics"])],
        knowledge_items=[_knowledge_item("k1", human_visibility="company")],
    ))

    def _fake_rpc(fn_name, params=None):
        if fn_name == "search_geo_reports":
            return [{"report_id": "r1", "text_score": 0.5}]
        if fn_name == "search_geo_knowledge_items":
            return [{"knowledge_item_id": "k1", "text_score": 0.9}]
        return []
    client.rpc = _fake_rpc

    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    result = search_service.search(client, "Hormuz", requester_context=ctx)

    assert result["report_count"] == 1
    assert result["knowledge_item_count"] == 1
    source_types = {r["source_type"] for r in result["results"]}
    assert source_types == {"report", "knowledge_item"}
    # スコア順（k1=0.9がr1=0.5より先）
    assert result["results"][0]["source_type"] == "knowledge_item"


def test_search_passes_through_snippet_from_rpc_for_reports_and_knowledge_items():
    """Phase 2F-2: snippetはts_headline（RPC側）の結果をそのまま通すだけで、
    新しいLLM呼び出しやranking変更を伴わないことを確認する。"""
    client = FakeSupabaseClient(tables=_base_tables(
        reports=[_report("r1", allowed_org_units=["geopolitics"])],
        knowledge_items=[_knowledge_item("k1", human_visibility="company")],
    ))

    def _fake_rpc(fn_name, params=None):
        if fn_name == "search_geo_reports":
            return [{"report_id": "r1", "text_score": 0.5, "snippet": "\x02Hormuz\x03 risk analysis"}]
        if fn_name == "search_geo_knowledge_items":
            return [{"knowledge_item_id": "k1", "text_score": 0.9, "snippet": "\x02Hormuz\x03 approved brief"}]
        return []
    client.rpc = _fake_rpc

    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    result = search_service.search(client, "Hormuz", requester_context=ctx)

    snippets = {r["report_id" if r["source_type"] == "report" else "knowledge_item_id"]: r["snippet"]
                for r in result["results"]}
    assert snippets["r1"] == "\x02Hormuz\x03 risk analysis"
    assert snippets["k1"] == "\x02Hormuz\x03 approved brief"


def test_knowledge_item_result_review_status_is_always_approved():
    """approved_report knowledge_typeは既存Publication Route A/B(Human Gate #2)経由でしか
    作られないため、review_statusは常に"approved"。"""
    entry = search_service.knowledge_item_result(_knowledge_item("k1"))
    assert entry["review_status"] == "approved"


# ---- Phase 2F-3 §17: Backend Metadata Filter ------------------------------------

def test_resolve_eligible_report_ids_region_filter_applies_after_permission():
    client = FakeSupabaseClient(tables=_base_tables(reports=[
        _report("r-match", allowed_org_units=["geopolitics"], country_region=["Iran"]),
        _report("r-no-region-match", allowed_org_units=["geopolitics"], country_region=["China"]),
        _report("r-no-permission", allowed_org_units=["sustainability"], country_region=["Iran"]),
    ]))
    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    eligible = search_service.resolve_eligible_report_ids(
        client, requester_context=ctx, filters={"region": "Iran"})
    assert eligible == {"r-match"}


def test_resolve_eligible_report_ids_key_actor_filter():
    client = FakeSupabaseClient(tables=_base_tables(reports=[
        _report("r1", allowed_org_units=["geopolitics"], key_actors=["Iran", "US"]),
        _report("r2", allowed_org_units=["geopolitics"], key_actors=["China"]),
    ]))
    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    eligible = search_service.resolve_eligible_report_ids(
        client, requester_context=ctx, filters={"key_actor": "US"})
    assert eligible == {"r1"}


def test_resolve_eligible_report_ids_date_range_filter():
    client = FakeSupabaseClient(tables=_base_tables(reports=[
        _report("r-jan", allowed_org_units=["geopolitics"], uploaded_at="2026-01-15T00:00:00+00:00"),
        _report("r-jul", allowed_org_units=["geopolitics"], uploaded_at="2026-07-15T00:00:00+00:00"),
    ]))
    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    eligible = search_service.resolve_eligible_report_ids(
        client, requester_context=ctx, filters={"date_from": "2026-06-01", "date_to": "2026-08-01"})
    assert eligible == {"r-jul"}


def test_resolve_eligible_report_ids_theme_filter_joins_documents_and_themes():
    client = FakeSupabaseClient(tables=_base_tables(
        reports=[
            _report("r-hormuz", allowed_org_units=["geopolitics"], document_id="doc-hormuz"),
            _report("r-taiwan", allowed_org_units=["geopolitics"], document_id="doc-taiwan"),
        ],
        documents=[
            {"document_id": "doc-hormuz", "theme_id": "theme-a"},
            {"document_id": "doc-taiwan", "theme_id": "theme-b"},
        ],
        themes=[
            {"theme_id": "theme-a", "name_ja": "ホルムズ"},
            {"theme_id": "theme-b", "name_ja": "台湾"},
        ],
    ))
    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    eligible = search_service.resolve_eligible_report_ids(
        client, requester_context=ctx, filters={"theme": "ホルムズ"})
    assert eligible == {"r-hormuz"}


def test_resolve_eligible_report_ids_review_status_filter_derives_status():
    client = FakeSupabaseClient(tables=_base_tables(
        reports=[
            _report("r-approved", allowed_org_units=["geopolitics"]),
            _report("r-uploaded-only", allowed_org_units=["geopolitics"]),
        ],
        knowledge_items=[
            _knowledge_item("k1", knowledge_type="approved_report", status="active",
                             **{"source_report_id": "r-approved"}),
        ],
    ))
    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    eligible = search_service.resolve_eligible_report_ids(
        client, requester_context=ctx, filters={"review_status": "approved"})
    assert eligible == {"r-approved"}


def test_resolve_eligible_report_ids_confidentiality_label_filter():
    client = FakeSupabaseClient(tables=_base_tables(reports=[
        _report("r-dept", allowed_org_units=["geopolitics"],
                human_visibility="custom", agent_sharing_policy="none"),
        _report("r-group", allowed_org_units=["geopolitics"],
                human_visibility="company", agent_sharing_policy="custom"),
    ]))
    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    eligible = search_service.resolve_eligible_report_ids(
        client, requester_context=ctx, filters={"confidentiality_label": "GROUP_SHAREABLE"})
    assert eligible == {"r-group"}


def test_resolve_eligible_report_ids_source_type_filter_knowledge_item_returns_empty():
    client = FakeSupabaseClient(tables=_base_tables(reports=[
        _report("r1", allowed_org_units=["geopolitics"]),
    ]))
    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    eligible = search_service.resolve_eligible_report_ids(
        client, requester_context=ctx, filters={"source_type": "knowledge_item"})
    assert eligible == set()


def test_resolve_eligible_knowledge_item_ids_region_filter_excludes_all():
    """Knowledge Itemはregion列を持たないため、regionフィルタ指定時は
    「データが無いので一致不明」として除外する（誤って一致扱いにしない）。"""
    client = FakeSupabaseClient(tables=_base_tables(knowledge_items=[
        _knowledge_item("k1", human_visibility="company"),
    ]))
    ctx = policy.build_requester_context(user_id="u1", is_authenticated=True)
    eligible = search_service.resolve_eligible_knowledge_item_ids(
        client, requester_context=ctx, filters={"region": "Iran"})
    assert eligible == set()


def test_resolve_eligible_knowledge_item_ids_source_type_filter_report_returns_empty():
    client = FakeSupabaseClient(tables=_base_tables(knowledge_items=[
        _knowledge_item("k1", human_visibility="company"),
    ]))
    ctx = policy.build_requester_context(user_id="u1", is_authenticated=True)
    eligible = search_service.resolve_eligible_knowledge_item_ids(
        client, requester_context=ctx, filters={"source_type": "report"})
    assert eligible == set()


def test_search_with_filters_restricts_p_eligible_ids_passed_to_rpc():
    """search()にfiltersを渡すと、permission適用後のeligible集合が既にfilterで絞られた
    ものになり、RPCへはその絞られたIDだけが渡る（Retrieval結果を後から絞るのではない）。"""
    client = FakeSupabaseClient(tables=_base_tables(reports=[
        _report("r-iran", allowed_org_units=["geopolitics"], country_region=["Iran"]),
        _report("r-china", allowed_org_units=["geopolitics"], country_region=["China"]),
    ]))
    captured = {}

    def _fake_rpc(fn_name, params=None):
        captured[fn_name] = params
        if fn_name == "search_geo_reports":
            return [{"report_id": "r-iran", "text_score": 0.5, "snippet": None}]
        return []
    client.rpc = _fake_rpc

    ctx = policy.build_requester_context(user_id="u1", department="geopolitics", is_authenticated=True)
    result = search_service.search(client, "Hormuz", requester_context=ctx, filters={"region": "Iran"})

    assert captured["search_geo_reports"]["p_eligible_ids"] == ["r-iran"]
    assert result["report_count"] == 1


def test_search_blank_query_returns_empty_without_touching_db():
    client = FakeSupabaseClient()

    def _raise_if_called(*a, **kw):
        raise AssertionError("空クエリなのにDBへ問い合わせた")
    client.select = _raise_if_called
    client.rpc = _raise_if_called

    result = search_service.search(client, "   ", is_internal_caller=True)
    assert result == {"results": [], "report_count": 0, "knowledge_item_count": 0}
