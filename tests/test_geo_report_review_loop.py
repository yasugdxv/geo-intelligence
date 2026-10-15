"""地政学アプリClooseout Phase4: Report Review Loop（既存Expert Review LoopのReport拡張）の検証。"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_connector_base as base  # noqa: E402
import geo_expert_review_service as review_service  # noqa: E402
import geo_retrieval_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _mock_llm_client(response: dict) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(response)))],
    )
    return client


def _base_client():
    return FakeSupabaseClient(tables={"geo_themes": [{"theme_id": "theme-1"}]})


def _start_report_session(client, tmp_path, monkeypatch, *, report_id="report-v2-1"):
    monkeypatch.setattr(base, "RAW_SNAPSHOT_ROOT", tmp_path)
    return review_service.start_review_session(
        client, theme_id="theme-1", case_id=None, report_id=report_id,
        draft_text="v1のReport本文", final_text="レビュー後のv2 Report本文",
        reviewer_id="analyst1", reviewer_label="Senior Analyst", review_type="report_review",
    )


# ---- exactly-one制約 ----

def test_start_review_session_requires_exactly_one_of_case_id_or_report_id(tmp_path, monkeypatch):
    client = _base_client()
    monkeypatch.setattr(base, "RAW_SNAPSHOT_ROOT", tmp_path)
    with pytest.raises(ValueError):
        review_service.start_review_session(
            client, theme_id="theme-1", case_id=None, report_id=None,
            draft_text="d", final_text="f", reviewer_id="analyst1")


def test_start_review_session_rejects_both_case_id_and_report_id(tmp_path, monkeypatch):
    client = _base_client()
    monkeypatch.setattr(base, "RAW_SNAPSHOT_ROOT", tmp_path)
    with pytest.raises(ValueError):
        review_service.start_review_session(
            client, theme_id="theme-1", case_id="case-1", report_id="report-1",
            draft_text="d", final_text="f", reviewer_id="analyst1")


def test_start_review_session_accepts_report_id_only(tmp_path, monkeypatch):
    client = _base_client()
    session = _start_report_session(client, tmp_path, monkeypatch)
    assert session["report_id"] == "report-v2-1"
    assert session["case_id"] is None
    assert session["review_type"] == "report_review"


# ---- Delta Extraction・Promoteは既存ロジックがReportでも同じく動く ----

def test_extract_deltas_works_for_report_session(tmp_path, monkeypatch):
    client = _base_client()
    session = _start_report_session(client, tmp_path, monkeypatch)
    response = {"changes": [{
        "change_type": "main_hypothesis_change", "reusability_class": "case_specific_assessment",
        "reasoning_summary": "レビューで判明した新しい分析観点", "confidence": "中",
        "draft_excerpt": "d", "final_excerpt": "f", "grounding_note_ids": [],
    }]}
    azure_client = _mock_llm_client(response)

    result = review_service.extract_deltas(client, azure_client, "gpt-4o", session["review_session_id"],
                                            actor_id="analyst1")

    assert len(result["changes"]) == 1


def test_promote_report_specific_assessment_creates_report_finding(tmp_path, monkeypatch):
    """既存test_promote_case_specific_assessment_creates_case_findingと対称的な、
    Report版のPromoteテスト。report_finding（v2のreport_idを指す）が作られること。"""
    client = _base_client()
    session = _start_report_session(client, tmp_path, monkeypatch, report_id="report-v2-xyz")
    response = {"changes": [{
        "change_type": "main_hypothesis_change", "reusability_class": "case_specific_assessment",
        "reasoning_summary": "本Reportのレビューで判明した新しい知見", "confidence": "中",
        "draft_excerpt": "d", "final_excerpt": "f", "grounding_note_ids": [],
    }]}
    azure_client = _mock_llm_client(response)
    extraction = review_service.extract_deltas(client, azure_client, "gpt-4o", session["review_session_id"],
                                                actor_id="analyst1")
    change = extraction["changes"][0]

    result = review_service.promote_change(client, change_id=change["change_id"], reviewer_id="reviewer1")

    assert result["knowledge_item"]["knowledge_type"] == "report_finding"
    assert result["knowledge_item"]["status"] == "active"
    assert result["knowledge_item"]["structured_content"]["report_id"] == "report-v2-xyz"


def test_promoted_report_finding_is_retrievable_via_existing_issue_based_retrieval(tmp_path, monkeypatch):
    """地政学アプリClooseout Phase5の核心要件: Promote後、report_findingが既存の
    geo_retrieval_service.get_internal_expert_assessments（Issue単位のRetrieval、次回Expert AI/
    Intelligence AIが使う経路）から実際に見つかること。Session→Report→Claim→Issueの既存経路で
    Issue紐付けが行われることを検証する（新しいRetrieval関数は作らない）。"""
    client = _base_client()
    client.tables["geo_issues"] = [{"issue_id": "issue-1", "theme_id": "theme-1", "code": "i1"}]
    client.tables["geo_reports"] = [{"report_id": "report-v2-xyz", "document_id": "doc-v2-xyz",
                                      "version": 2, "report_identity_id": "report-v1-xyz"}]
    client.tables["geo_claims"] = [{"claim_id": "claim-from-report", "origin_type": "document",
                                     "origin_id": "doc-v2-xyz", "claim_text": "レポートの引用"}]
    client.tables["geo_claim_issues"] = [{"claim_id": "claim-from-report", "issue_id": "issue-1"}]

    session = _start_report_session(client, tmp_path, monkeypatch, report_id="report-v2-xyz")
    response = {"changes": [{
        "change_type": "main_hypothesis_change", "reusability_class": "case_specific_assessment",
        "reasoning_summary": "本Reportのレビューで判明した新しい知見", "confidence": "中",
        "draft_excerpt": "d", "final_excerpt": "f", "grounding_note_ids": [],
    }]}
    azure_client = _mock_llm_client(response)
    extraction = review_service.extract_deltas(client, azure_client, "gpt-4o", session["review_session_id"],
                                                actor_id="analyst1")
    change = extraction["changes"][0]

    review_service.promote_change(client, change_id=change["change_id"], reviewer_id="reviewer1")

    found = geo_retrieval_service.get_internal_expert_assessments(client, ["issue-1"], include_demo=True)
    assert any(f["knowledge_type"] == "report_finding" for f in found)


def test_promote_reusable_reasoning_still_works_for_report_session(tmp_path, monkeypatch):
    """reusable_reasoning（mechanism/precedent/reasoning_rule）はCase/Report区別なく
    既存ロジックのまま動作すること。"""
    client = _base_client()
    session = _start_report_session(client, tmp_path, monkeypatch)
    response = {"changes": [{
        "change_type": "mechanism_added", "reusability_class": "reusable_reasoning",
        "reasoning_summary": "Reportレビューから見出した再利用可能なMechanism", "confidence": "中",
        "draft_excerpt": "d", "final_excerpt": "f", "grounding_note_ids": [],
    }]}
    azure_client = _mock_llm_client(response)
    extraction = review_service.extract_deltas(client, azure_client, "gpt-4o", session["review_session_id"],
                                                actor_id="analyst1")
    change = extraction["changes"][0]

    result = review_service.promote_change(client, change_id=change["change_id"], reviewer_id="reviewer1")

    assert result["knowledge_item"]["knowledge_type"] == "mechanism"
