"""Phase B-C: Intelligence AI / Expert AI Role Separation — 非同期Research実行のテスト。

geo_research_service.run_research_requestの冪等性（同一Gapに対してExpert AIが複数回
走らないこと）と、Expert応答→candidate Knowledge作成→Human Review→Gap解決→次回reuse、
というEnd-to-Endのlifecycleを確認する。"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_intelligence_service as service  # noqa: E402
import geo_knowledge_gap_service as gap_service  # noqa: E402
import geo_knowledge_service  # noqa: E402
import geo_research_service as research  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402
from tests.test_geo_intelligence_service_phase2 import _mock_llm_client  # noqa: E402


def _department_request(**overrides) -> dict:
    request = {
        "requester_app": "sustainability_intelligence",
        "request_type": "user_question",
        "question": "EUDR延期は今どうなっている？",
        "country_region": ["EU"],
        "themes": [],
        "source_department": "sustainability",
        "source_workflow": "chat",
        "analysis_requested": True,
    }
    request.update(overrides)
    return request


def _partial_coverage_client() -> FakeSupabaseClient:
    return FakeSupabaseClient(tables={
        "geo_entities": [{"entity_id": "ent-eu", "entity_type": "organization",
                          "canonical_name": "EU", "external_ids": {}, "is_demo": False}],
        "geo_entity_issues": [{"entity_id": "ent-eu", "issue_id": "issue-eudr"}],
        "geo_knowledge_item_issues": [{"knowledge_item_id": "ki-foundation", "issue_id": "issue-eudr"}],
        "geo_knowledge_items": [{
            "knowledge_item_id": "ki-foundation", "knowledge_type": "country_foundation",
            "status": "active", "retrieval_status": "available", "is_demo": False,
            "title": "EUDRの政治的背景", "updated_at": "2026-08-01T00:00:00+00:00",
            "agent_sharing_policy": "allowed_cross_domain",
        }],
    })


def _create_gap(client, *, question="EUDR延期は今どうなっている？") -> str:
    """テスト用: Department Inquiryを1件走らせ、実際に記録されたGap（partial coverage
    なので必ず作られる）のgap_idを返す。"""
    response = service.process_request(client, _department_request(question=question))
    knowledge_gap = response["knowledge_gap"]
    assert knowledge_gap and knowledge_gap.get("exists"), "fixtureはGapが必ず作られる想定"
    return knowledge_gap["gap_id"]


# ---- Case 1/2/3相当: Department Inquiryのデフォルト非同期経路 -------------------------------

def test_sufficient_knowledge_skips_expert_and_reports_no_research(monkeypatch):
    """Case1: 既存Knowledgeで十分な場合、Expert AI同期呼び出し無し・research_status=none。"""
    azure_client = _mock_llm_client()
    client = FakeSupabaseClient(tables={
        "geo_entities": [
            {"entity_id": "ent-eu", "entity_type": "organization", "canonical_name": "EU", "is_demo": False},
            {"entity_id": "ent-member", "entity_type": "organization", "canonical_name": "EUDR", "is_demo": False},
        ],
        "geo_entity_issues": [
            {"entity_id": "ent-eu", "issue_id": "issue-eudr"},
            {"entity_id": "ent-member", "issue_id": "issue-eudr"},
        ],
        "geo_relationships": [{
            "relationship_id": "rel-1", "from_entity_id": "ent-eu", "to_entity_id": "ent-member",
            "relationship_type": "regulatory_oversight", "relationship_domain": "regulation",
            "description": "規制執行関係", "is_demo": False,
        }],
        "geo_knowledge_item_issues": [
            {"knowledge_item_id": "ki-found", "issue_id": "issue-eudr"},
            {"knowledge_item_id": "ki-prec", "issue_id": "issue-eudr"},
        ],
        "geo_knowledge_items": [
            {"knowledge_item_id": "ki-found", "knowledge_type": "country_foundation",
             "status": "active", "retrieval_status": "available", "is_demo": False,
             "title": "EUDRの政治的背景", "updated_at": "2026-06-01T00:00:00+00:00",
             "agent_sharing_policy": "allowed_cross_domain"},
            {"knowledge_item_id": "ki-prec", "knowledge_type": "precedent",
             "status": "active", "retrieval_status": "available", "is_demo": False,
             "title": "過去のEU規制導入事例", "updated_at": "2026-06-01T00:00:00+00:00",
             "agent_sharing_policy": "allowed_cross_domain"},
        ],
    })
    response = service.process_request(client, _department_request(question="EUDRの政治的背景を簡潔に教えて"),
                                       azure_client=azure_client, model="gpt-4o")

    di = response["department_intelligence"]
    assert di["research_status"] == "none"
    assert response["expert_ai_called"] is False
    azure_client.chat.completions.create.assert_not_called()


def test_partial_knowledge_answers_immediately_and_requests_research(monkeypatch):
    """Case2: 既存Knowledgeで部分回答しつつ、Expert AI同期呼び出しは0、Gap/Research Request
    は生成、HTTP応答は即時返る（＝azure_clientは呼ばれない）。"""
    azure_client = _mock_llm_client()
    client = _partial_coverage_client()

    response = service.process_request(client, _department_request(),
                                       azure_client=azure_client, model="gpt-4o")

    di = response["department_intelligence"]
    assert response["expert_ai_called"] is False
    azure_client.chat.completions.create.assert_not_called()
    assert di["unresolved_dimensions"]
    assert response["knowledge_gap"]["exists"] is True
    assert di["research_status"] == "requested"
    assert "追加のResearch Requestとして記録しました" in response["geo_assessment"]
    # 既存Foundation自体は破棄されず回答に含まれる
    assert any(r["reference_id"] == "ki-foundation" for r in response["references"])


def test_insufficient_knowledge_no_fabrication_and_requests_research():
    """Case3: 既存Knowledgeがほぼ無い場合も一般知識で埋めず、Research Requestのみ記録する。"""
    azure_client = _mock_llm_client()
    client = FakeSupabaseClient()  # Knowledge一切なし

    response = service.process_request(client, _department_request(),
                                       azure_client=azure_client, model="gpt-4o")

    di = response["department_intelligence"]
    assert response["expert_ai_called"] is False
    azure_client.chat.completions.create.assert_not_called()
    assert di["expert_response_id"] is None
    assert response["knowledge_gap"]["exists"] is True
    assert di["research_status"] == "requested"
    # 捏造Provenanceが無いこと
    assert di["provenance"] == [] or all(p["origin"] != "fresh_expert_analysis" for p in di["provenance"])


# ---- Case4: Gap → run_research_request → candidate → Human Review/Promotion → active → reuse --

def test_research_request_is_idempotent_across_repeated_calls(monkeypatch):
    """B-C item2: 同一Gapに対してrun_research_requestを複数回呼んでも、Expert AIが複数回
    走らない（2回目以降はskipped=Trueで即return）。"""
    monkeypatch.setenv("GEO_EXPERT_EXECUTION_MODE", "monolithic")
    client = _partial_coverage_client()
    gap_id = _create_gap(client)
    azure_client = _mock_llm_client()

    result1 = research.run_research_request(client, azure_client, "gpt-4o", gap_id)
    assert result1["skipped"] is False
    assert result1["status"] == "completed"
    assert azure_client.chat.completions.create.call_count == 1

    result2 = research.run_research_request(client, azure_client, "gpt-4o", gap_id)
    assert result2["skipped"] is True
    assert result2["reason"] == "already_researched"
    assert azure_client.chat.completions.create.call_count == 1  # 増えていない


def test_research_request_skips_when_already_researching(monkeypatch):
    """statusがresearching（他プロセスが処理中）ならExpert AIを呼ばない。"""
    client = _partial_coverage_client()
    gap_id = _create_gap(client)
    client.update("geo_knowledge_gaps", {"gap_id": f"eq.{gap_id}"}, {"status": "researching"})
    azure_client = _mock_llm_client()

    result = research.run_research_request(client, azure_client, "gpt-4o", gap_id)
    assert result["skipped"] is True
    assert result["reason"] == "already_researching"
    azure_client.chat.completions.create.assert_not_called()


def test_research_request_failure_reverts_status_to_open_for_retry():
    client = _partial_coverage_client()
    gap_id = _create_gap(client)
    azure_client = MagicMock()
    azure_client.chat.completions.create.side_effect = RuntimeError("azure down")

    result = research.run_research_request(client, azure_client, "gpt-4o", gap_id)
    assert result["skipped"] is False
    assert result["status"] == "failed"

    gap = client.select("geo_knowledge_gaps", {"select": "*", "gap_id": f"eq.{gap_id}"})[0]
    assert gap["status"] == "open"  # 再試行できる状態へ戻る
    assert gap.get("resolution_expert_response_id") is None


def test_full_lifecycle_gap_research_candidate_review_reuse(monkeypatch):
    """Case4（改訂版）: Gap → run_research_request → candidate Knowledge → この時点では
    通常Retrievalに混ぜない → Human Review/Promotion → active → 次回問い合わせで
    Intelligence AIがreuseする、までをEnd-to-Endで確認する。

    Real/Mock: client=FakeSupabaseClient（実DBではない）、azure_client=_mock_llm_client
    （実Azure OpenAIではない、固定応答を返すモック）。geo_department_intelligence_service/
    geo_research_service/geo_knowledge_gap_service/geo_knowledge_serviceのロジックは全て実コード。"""
    monkeypatch.setenv("GEO_EXPERT_EXECUTION_MODE", "monolithic")
    client = _partial_coverage_client()

    # ① Department Inquiry → Gap生成（Expert AI同期呼び出し無し）
    gap_id = _create_gap(client)
    assert gap_service.research_status_for_gap(client, gap_id) == "requested"

    # ② run_research_request → candidate Knowledge作成、Gapはまだresolvedにしない
    azure_client = _mock_llm_client()
    result = research.run_research_request(client, azure_client, "gpt-4o", gap_id)
    assert result["status"] == "completed"
    knowledge_item_id = result["knowledge_item_id"]
    assert result["expert_response_id"]

    gap_after_research = client.select("geo_knowledge_gaps", {"select": "*", "gap_id": f"eq.{gap_id}"})[0]
    assert gap_after_research["status"] != "resolved"  # Research完了≠Gap解決
    assert gap_after_research["resolution_expert_response_id"] == result["expert_response_id"]
    assert gap_service.research_status_for_gap(client, gap_id) == "completed"  # Researchは完了

    created_item = client.select("geo_knowledge_items", {
        "select": "*", "knowledge_item_id": f"eq.{knowledge_item_id}"})[0]
    assert created_item["status"] == "candidate"  # まだactiveではない

    # ③ この時点では通常Retrievalに混ぜない（candidateはget_foundation_knowledge等の対象外）
    response_before_review = service.process_request(
        client, _department_request(request_id="req-before-review"))
    assert knowledge_item_id not in [
        d["source_id"] for d in response_before_review["department_intelligence"]["provenance"]]

    # ④ Human Review: candidate → in_review → active（既存review_knowledge_item、無変更で再利用）
    reviewed = geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=knowledge_item_id, reviewer_id="analyst-1",
        reviewer_role="knowledge_steward", review_decision="start_review",
        review_comment="Phase B-C Research結果のレビュー", evidence_adequacy="sufficient",
        applicability_checked=True,
    )["knowledge_item"]
    assert reviewed["status"] == "in_review"
    reviewed = geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=knowledge_item_id, reviewer_id="analyst-1",
        reviewer_role="knowledge_steward", review_decision="approve_active",
        review_comment="内容を確認し承認", evidence_adequacy="sufficient",
        applicability_checked=True,
    )["knowledge_item"]
    assert reviewed["status"] == "active"

    # Phase E: 新規作成Knowledgeの既定agent_sharing_policyはgeopolitics_only（他部署からは
    # 見えない）。本テストの⑥は「他部署からの同じ問い合わせでこのKnowledgeがreuseされる」
    # ことを検証する意図のため、Stewardが明示的にCross-Domain共有可とレビュー時に判断した
    # 想定でpolicyを更新する（Phase E以前は列自体が無く、この判断ステップが存在しなかった）。
    client.update("geo_knowledge_items", {"knowledge_item_id": f"eq.{knowledge_item_id}"},
                  {"agent_sharing_policy": "allowed_cross_domain"})

    # ⑤ activeへ昇格した時点で初めてGapをresolvedにする（Research完了時点では未resolved）
    resolved_gap = gap_service.resolve_intelligence_need_from_promoted_knowledge(
        client, gap_id, knowledge_item_id=knowledge_item_id, resolved_by="analyst-1")
    assert resolved_gap["status"] == "resolved"
    assert gap_service.research_status_for_gap(client, gap_id) == "completed"

    # ⑥ 次回同じ問い合わせでIntelligence AIがactive化されたKnowledgeをreuseする
    response_after_review = service.process_request(
        client, _department_request(request_id="req-after-review"))
    di_after = response_after_review["department_intelligence"]
    assert "internal_expert_knowledge" in di_after["covered_dimensions"]
    assert knowledge_item_id in di_after["selected_source_ids"]


# ---- Phase B-D P0: Human ReviewでRejectされた場合の再調査ループ -----------------------------

def test_reopen_after_rejection_allows_new_research_and_keeps_history(monkeypatch):
    """RejectされたcandidateのままだとGapが永遠に再調査不能になる問題（B-D P0）を確認する。
    reopen_research_gap_after_rejection呼び出し後は、run_research_requestが再度Expert AIを
    呼べること、かつ過去の実行履歴（geo_expert_requests/responses、rejectされた
    knowledge_item自体）が削除されずそのまま残ることを確認する。"""
    monkeypatch.setenv("GEO_EXPERT_EXECUTION_MODE", "monolithic")
    client = _partial_coverage_client()
    gap_id = _create_gap(client)

    azure_client = _mock_llm_client()
    first = research.run_research_request(client, azure_client, "gpt-4o", gap_id)
    assert first["status"] == "completed"
    first_response_id = first["expert_response_id"]
    first_knowledge_item_id = first["knowledge_item_id"]

    # Human ReviewでReject（candidate→in_review→rejectedの2段階、既存ALLOWED_TRANSITIONS通り）
    geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=first_knowledge_item_id, reviewer_id="analyst-1",
        reviewer_role="knowledge_steward", review_decision="start_review",
        review_comment="レビュー開始", evidence_adequacy="partial", applicability_checked=True,
    )
    geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=first_knowledge_item_id, reviewer_id="analyst-1",
        reviewer_role="knowledge_steward", review_decision="reject",
        review_comment="根拠不十分のためReject", evidence_adequacy="insufficient",
        applicability_checked=True,
    )

    # Reject後、reopenするまではrun_research_requestは再実行しない（既に応答あり扱い）
    stale = research.run_research_request(client, azure_client, "gpt-4o", gap_id)
    assert stale["skipped"] is True
    assert stale["reason"] == "already_researched"
    assert azure_client.chat.completions.create.call_count == 1  # まだ1回のまま

    reopened = gap_service.reopen_research_gap_after_rejection(
        client, gap_id, knowledge_item_id=first_knowledge_item_id, reopened_by="analyst-1",
        reason="根拠不十分のため再調査")
    assert reopened["status"] == "open"
    assert reopened["resolution_expert_response_id"] is None
    assert gap_service.research_status_for_gap(client, gap_id) == "requested"

    second = research.run_research_request(client, azure_client, "gpt-4o", gap_id)
    assert second["skipped"] is False
    assert second["status"] == "completed"
    assert azure_client.chat.completions.create.call_count == 2  # 再度Expert AIが呼ばれた
    assert second["knowledge_item_id"] != first_knowledge_item_id  # 新しいcandidateが別途作られる

    # 過去履歴は削除されない
    old_item = client.select("geo_knowledge_items", {
        "select": "*", "knowledge_item_id": f"eq.{first_knowledge_item_id}"})[0]
    assert old_item["status"] == "rejected"
    old_response = client.select("geo_expert_responses", {
        "select": "*", "response_id": f"eq.{first_response_id}"})
    assert len(old_response) == 1


def test_reopen_after_rejection_requires_rejected_status():
    client = _partial_coverage_client()
    gap_id = _create_gap(client)
    azure_client = _mock_llm_client()
    result = research.run_research_request(client, azure_client, "gpt-4o", gap_id)

    import pytest
    with pytest.raises(gap_service.IntelligenceNeedResolutionError):
        gap_service.reopen_research_gap_after_rejection(
            client, gap_id, knowledge_item_id=result["knowledge_item_id"], reopened_by="analyst-1")


# ---- 本番実データで判明した不具合の回帰テスト: status="duplicate_candidate" -------------------

def test_research_status_for_duplicate_candidate_gap_is_requested_not_none():
    """research_status_for_gapがstatus="open"だけを見ていたため、重複候補としてflagされた
    （実体としては未着手の）Gapがrequested=nullのまま扱われてしまっていた不具合の回帰。"""
    client = _partial_coverage_client()
    gap_id = _create_gap(client)
    client.update("geo_knowledge_gaps", {"gap_id": f"eq.{gap_id}"}, {"status": "duplicate_candidate"})

    assert gap_service.research_status_for_gap(client, gap_id) == "requested"


def test_run_research_request_proceeds_on_duplicate_candidate_gap(monkeypatch):
    """同様に、run_research_requestがstatus="open"のみを対象にしていたため、duplicate_
    candidateなGapが永遠に非同期Researchされない不具合の回帰。"""
    monkeypatch.setenv("GEO_EXPERT_EXECUTION_MODE", "monolithic")
    client = _partial_coverage_client()
    gap_id = _create_gap(client)
    client.update("geo_knowledge_gaps", {"gap_id": f"eq.{gap_id}"}, {"status": "duplicate_candidate"})
    azure_client = _mock_llm_client()

    result = research.run_research_request(client, azure_client, "gpt-4o", gap_id)
    assert result["skipped"] is False
    assert result["status"] == "completed"
    assert result["knowledge_item_id"]
