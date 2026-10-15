"""地政学ナレッジ＆インテリジェンスDB Phase2B-2: External Reference Model単体・回帰テスト。

Source Reference Policy（Source単位でのretrieval_status自動決定）と、statusとretrieval_status
の二軸独立運用を検証する。外部APIは呼び出さない。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_case_service  # noqa: E402
import geo_claim_service  # noqa: E402
import geo_connector_base as base  # noqa: E402
import geo_knowledge_service  # noqa: E402
import geo_retrieval_service  # noqa: E402
import geo_search_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _source(reference_availability="restricted", trust_tier="D"):
    return {"source_id": "src-1", "name": "テストSource", "trust_tier": trust_tier,
            "reference_availability": reference_availability}


# ── Claimのretrieval_status初期値決定 ──────────────────────────────────

def test_document_origin_claim_defaults_restricted_when_source_not_set_available():
    client = FakeSupabaseClient(tables={"geo_source_registry": [_source(reference_availability="restricted")],
                                          "geo_documents": [{"document_id": "doc-1", "source_id": "src-1"}]})
    claim = geo_claim_service.create_claim(client, claim_text="x", information_class="external_assessment",
                                            origin_type="document", origin_id="doc-1", actor_id="t")
    assert claim["retrieval_status"] == "restricted"
    assert claim["source_id"] == "src-1"


def test_document_origin_claim_becomes_available_when_source_policy_available():
    client = FakeSupabaseClient(tables={"geo_source_registry": [_source(reference_availability="available")],
                                          "geo_documents": [{"document_id": "doc-1", "source_id": "src-1"}]})
    claim = geo_claim_service.create_claim(client, claim_text="x", information_class="external_assessment",
                                            origin_type="document", origin_id="doc-1", actor_id="t")
    assert claim["retrieval_status"] == "available"


def test_manual_and_case_origin_claims_are_always_available():
    client = FakeSupabaseClient()
    manual_claim = geo_claim_service.create_claim(client, claim_text="x", information_class="internal_hypothesis",
                                                   origin_type="manual", actor_id="t")
    assert manual_claim["retrieval_status"] == "available"
    assert manual_claim["source_id"] is None

    case_claim = geo_claim_service.create_claim(client, claim_text="y", information_class="internal_hypothesis",
                                                 origin_type="case", origin_id="case-1", actor_id="t")
    assert case_claim["retrieval_status"] == "available"


def test_event_origin_claim_resolves_source_via_evidence_regardless_of_origin_type():
    """★フィードバック1回帰: origin_typeだけでInternal/Externalを判定しない。event originでも
    証拠(geo_evidence)経由でSourceに辿り着ければSource Policyを適用する。"""
    client = FakeSupabaseClient(tables={
        "geo_source_registry": [_source(reference_availability="available")],
        "geo_documents": [{"document_id": "doc-1", "source_id": "src-1"}],
        "geo_evidence": [{"evidence_id": "ev-1", "event_id": "event-1", "document_id": "doc-1",
                           "created_at": "2026-01-01T00:00:00+00:00"}],
    })
    claim = geo_claim_service.create_claim(client, claim_text="x", information_class="observed_fact",
                                            origin_type="event", origin_id="event-1", actor_id="t")
    assert claim["source_id"] == "src-1"
    assert claim["retrieval_status"] == "available"


def test_unresolvable_source_and_non_internal_origin_stays_restricted():
    client = FakeSupabaseClient()  # geo_evidenceが無いのでevent originのsource解決は失敗する
    claim = geo_claim_service.create_claim(client, claim_text="x", information_class="observed_fact",
                                            origin_type="event", origin_id="event-missing", actor_id="t")
    assert claim["source_id"] is None
    assert claim["retrieval_status"] == "restricted"


# ── Source Reference Policyの変更・一括再同期 ──────────────────────────

def test_set_reference_availability_resyncs_only_available_and_restricted_claims():
    """★追加フィードバック2回帰: deprecated/archivedのClaimはSource側の方針転換で復活させない。"""
    client = FakeSupabaseClient(tables={"geo_source_registry": [_source(reference_availability="restricted")]})
    c1 = geo_claim_service.create_claim(client, claim_text="a", information_class="external_assessment",
                                         origin_type="manual", actor_id="t")
    client.update("geo_claims", {"claim_id": f"eq.{c1['claim_id']}"}, {"source_id": "src-1", "retrieval_status": "restricted"})
    c2 = geo_claim_service.create_claim(client, claim_text="b", information_class="external_assessment",
                                         origin_type="manual", actor_id="t")
    client.update("geo_claims", {"claim_id": f"eq.{c2['claim_id']}"}, {"source_id": "src-1", "retrieval_status": "archived"})

    base.set_reference_availability(client, source_id="src-1", reference_availability="available", actor_id="steward1")

    updated1 = client.select("geo_claims", {"select": "*", "claim_id": f"eq.{c1['claim_id']}", "limit": "1"})[0]
    updated2 = client.select("geo_claims", {"select": "*", "claim_id": f"eq.{c2['claim_id']}", "limit": "1"})[0]
    assert updated1["retrieval_status"] == "available"  # restrictedだったものは同期される
    assert updated2["retrieval_status"] == "archived"  # archivedは復活しない

    source = client.select("geo_source_registry", {"select": "*", "source_id": "eq.src-1", "limit": "1"})[0]
    assert source["reference_availability"] == "available"
    assert source["reference_availability_set_by"] == "steward1"


# ── Knowledge Item / Claim個別のretrieval_status変更（Retrieval Governance） ──

def test_set_retrieval_status_does_not_touch_lifecycle_status():
    client = FakeSupabaseClient()
    item = geo_knowledge_service.create_knowledge_item(
        client, knowledge_type="reasoning_rule", title="t", structured_content={
            "rule": "r", "rationale": "ra", "applicable_question_types": [], "required_checks": [],
            "comparison_dimensions": [], "common_failure_modes": [], "expert_source": "s",
            "approval_status": "draft",
        }, information_class="internal_hypothesis", created_by="analyst1")
    updated = geo_knowledge_service.set_retrieval_status(
        client, knowledge_item_id=item["knowledge_item_id"], retrieval_status="archived", actor_id="steward1")
    assert updated["retrieval_status"] == "archived"
    assert updated["status"] == "candidate"  # lifecycleは無変更

    logs = [log for log in client.tables["geo_audit_logs"]
            if log["action"] == "knowledge_item_retrieval_status_change"]
    assert len(logs) == 1


def test_claim_set_retrieval_status_overrides_source_policy_individually():
    client = FakeSupabaseClient(tables={"geo_source_registry": [_source(reference_availability="available")],
                                          "geo_documents": [{"document_id": "doc-1", "source_id": "src-1"}]})
    claim = geo_claim_service.create_claim(client, claim_text="x", information_class="external_assessment",
                                            origin_type="document", origin_id="doc-1", actor_id="t")
    assert claim["retrieval_status"] == "available"
    updated = geo_claim_service.set_retrieval_status(
        client, claim_id=claim["claim_id"], retrieval_status="restricted", actor_id="steward1",
        reason="この1件だけ問題あり")
    assert updated["retrieval_status"] == "restricted"

    # Source側は変更していないので、他の同一Source由来Claimには影響しない
    other = geo_claim_service.create_claim(client, claim_text="y", information_class="external_assessment",
                                            origin_type="document", origin_id="doc-1", actor_id="t")
    assert other["retrieval_status"] == "available"


# ── review_knowledge_itemの初回遷移限定初期化（二軸の真の独立性） ──────

def _reasoning_rule_content():
    return {"rule": "r", "rationale": "ra", "applicable_question_types": [], "required_checks": [],
            "comparison_dimensions": [], "common_failure_modes": [], "expert_source": "s",
            "approval_status": "draft"}


def test_review_knowledge_item_initializes_retrieval_status_only_on_first_transition():
    client = FakeSupabaseClient()
    item = geo_knowledge_service.create_knowledge_item(
        client, knowledge_type="reasoning_rule", title="t", structured_content=_reasoning_rule_content(),
        information_class="internal_hypothesis", created_by="analyst1")
    assert item["retrieval_status"] == "restricted"

    geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=item["knowledge_item_id"], reviewer_id="r1", reviewer_role="expert_reviewer",
        review_decision="start_review", review_comment="c", evidence_adequacy="partial",
        applicability_checked=False)
    result = geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=item["knowledge_item_id"], reviewer_id="r1", reviewer_role="expert_reviewer",
        review_decision="approve_active", review_comment="c", evidence_adequacy="sufficient",
        applicability_checked=True)
    assert result["knowledge_item"]["retrieval_status"] == "available"  # 初回遷移で初期化される


def test_review_knowledge_item_does_not_resync_retrieval_status_on_later_transitions():
    """★追加フィードバック2回帰: Stewardが個別設定したretrieval_statusを、無関係な後続の
    lifecycle遷移(active→deprecated等)が上書きしない。"""
    client = FakeSupabaseClient()
    item = geo_knowledge_service.create_knowledge_item(
        client, knowledge_type="reasoning_rule", title="t", structured_content=_reasoning_rule_content(),
        information_class="internal_hypothesis", created_by="analyst1")
    geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=item["knowledge_item_id"], reviewer_id="r1", reviewer_role="expert_reviewer",
        review_decision="start_review", review_comment="c", evidence_adequacy="partial",
        applicability_checked=False)
    geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=item["knowledge_item_id"], reviewer_id="r1", reviewer_role="expert_reviewer",
        review_decision="approve_active", review_comment="c", evidence_adequacy="sufficient",
        applicability_checked=True)
    # Stewardが個別にrestrictedへ手動変更（ライセンス問題等を発見した想定）
    geo_knowledge_service.set_retrieval_status(
        client, knowledge_item_id=item["knowledge_item_id"], retrieval_status="restricted", actor_id="steward1")

    # 無関係な後続レビュー操作（active→deprecated）
    geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=item["knowledge_item_id"], reviewer_id="r1", reviewer_role="expert_reviewer",
        review_decision="deprecate", review_comment="c", evidence_adequacy="sufficient",
        applicability_checked=True)

    final = geo_knowledge_service.get_knowledge_item(client, item["knowledge_item_id"])
    assert final["status"] == "deprecated"
    assert final["retrieval_status"] == "restricted"  # Stewardの設定が維持される（上書きされない）


# ── 検索: retrieval_statusのAND絞り込み・二軸維持 ──────────────────────

def test_search_excludes_archived_retrieval_status():
    client = FakeSupabaseClient()
    item = geo_knowledge_service.create_knowledge_item(
        client, knowledge_type="reasoning_rule", title="除外対象", structured_content=_reasoning_rule_content(),
        information_class="internal_hypothesis", created_by="t")
    client.update("geo_knowledge_items", {"knowledge_item_id": f"eq.{item['knowledge_item_id']}"},
                  {"status": "active", "retrieval_status": "archived"})
    result = geo_search_service.search_knowledge_items(client, "除外対象", log_trace=False)
    assert result["results"] == []

    claim = geo_claim_service.create_claim(client, claim_text="除外対象claim", information_class="observed_fact",
                                            origin_type="manual", actor_id="t")
    client.update("geo_claims", {"claim_id": f"eq.{claim['claim_id']}"}, {"retrieval_status": "archived"})
    claim_result = geo_search_service.search_claims(client, "除外対象claim", log_trace=False)
    assert claim_result["results"] == []


def test_get_reusable_and_contested_knowledge_return_separate_result_sets():
    """★フィードバック反映回帰: 一軸へ統合せず、active+availableとcontested+availableを
    別々に取得する。"""
    client = FakeSupabaseClient(tables={"geo_themes": [{"theme_id": "theme-1"}]})
    issue = client.insert("geo_issues", [{"theme_id": "theme-1", "code": "i1", "name_ja": "論点"}])[0]

    active_item = geo_knowledge_service.create_knowledge_item(
        client, knowledge_type="reasoning_rule", title="Active版", structured_content=_reasoning_rule_content(),
        information_class="internal_hypothesis", created_by="t")
    geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=active_item["knowledge_item_id"], reviewer_id="r1",
        reviewer_role="expert_reviewer", review_decision="start_review", review_comment="c",
        evidence_adequacy="partial", applicability_checked=False)
    geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=active_item["knowledge_item_id"], reviewer_id="r1",
        reviewer_role="expert_reviewer", review_decision="approve_active", review_comment="c",
        evidence_adequacy="sufficient", applicability_checked=True)
    geo_knowledge_service.link_to_issue(client, knowledge_item_id=active_item["knowledge_item_id"],
                                         issue_id=issue["issue_id"])

    contested_item = geo_knowledge_service.create_knowledge_item(
        client, knowledge_type="reasoning_rule", title="Contested版", structured_content=_reasoning_rule_content(),
        information_class="internal_hypothesis", created_by="t")
    geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=contested_item["knowledge_item_id"], reviewer_id="r1",
        reviewer_role="expert_reviewer", review_decision="start_review", review_comment="c",
        evidence_adequacy="partial", applicability_checked=False)
    geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=contested_item["knowledge_item_id"], reviewer_id="r1",
        reviewer_role="expert_reviewer", review_decision="approve_contested", review_comment="c",
        evidence_adequacy="partial", applicability_checked=True)
    geo_knowledge_service.link_to_issue(client, knowledge_item_id=contested_item["knowledge_item_id"],
                                         issue_id=issue["issue_id"])

    reusable = geo_retrieval_service.get_reusable_knowledge(client, [issue["issue_id"]])
    contested = geo_retrieval_service.get_contested_knowledge(client, [issue["issue_id"]])
    assert {i["knowledge_item_id"] for i in reusable} == {active_item["knowledge_item_id"]}
    assert {i["knowledge_item_id"] for i in contested} == {contested_item["knowledge_item_id"]}


# ── 複数の対立するExternal Referenceの共存 ─────────────────────────────

def test_multiple_conflicting_external_assessments_coexist_on_same_issue():
    client = FakeSupabaseClient(tables={"geo_themes": [{"theme_id": "theme-1"}]})
    issue = client.insert("geo_issues", [{"theme_id": "theme-1", "code": "i1", "name_ja": "論点"}])[0]

    claim_a = geo_claim_service.create_claim(client, claim_text="Source A: 仮説A", information_class="external_assessment",
                                              origin_type="manual", actor_id="t")
    claim_b = geo_claim_service.create_claim(client, claim_text="Source B: 仮説B", information_class="external_assessment",
                                              origin_type="manual", actor_id="t")
    gov_claim = geo_claim_service.create_claim(client, claim_text="政府: 主張C", information_class="actor_claim",
                                                origin_type="manual", actor_id="t")
    for claim in (claim_a, claim_b, gov_claim):
        geo_claim_service.link_claim_to_issue(client, claim_id=claim["claim_id"], issue_id=issue["issue_id"])

    result = geo_retrieval_service.get_reference_claims_for_issue(client, [issue["issue_id"]])
    ids = {c["claim_id"] for c in result}
    assert ids == {claim_a["claim_id"], claim_b["claim_id"], gov_claim["claim_id"]}  # 1つに統合されない

    only_external = geo_retrieval_service.get_reference_claims_for_issue(
        client, [issue["issue_id"]], information_classes=("external_assessment",))
    assert {c["claim_id"] for c in only_external} == {claim_a["claim_id"], claim_b["claim_id"]}
