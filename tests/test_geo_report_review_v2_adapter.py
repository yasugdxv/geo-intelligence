"""Report Review V2: Expert AI Reading Review Adapter（run_report_reading_comparison_review）
の実接続テスト。geo_expert_engine.run_expert_request_with_mode（既存Expert AIの唯一の
Public Entry Point）はmonkeypatchで差し替え、実Azure呼び出しは行わない——Adapter層が
既存draft_reviewモードを無改修で転用していること、Expert AI Core自体へは新しいmode/
schemaを一切追加していないことを検証する。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_expert_engine  # noqa: E402
import geo_ios_expert_ai_adapter as adapter  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _fake_engine_response(client, *, request_id: str, response_id: str, response_json: dict,
                          model: str = "gpt-test", prompt_version: str = "geo-expert-v1.2",
                          knowledge_version: str = "kb-2026-09-16"):
    client.tables.setdefault("geo_expert_responses", []).append({
        "response_id": response_id, "request_id": request_id, "response_json": response_json,
        "model": model, "prompt_version": prompt_version, "knowledge_version": knowledge_version,
    })
    return {
        "request": {"request_id": request_id},
        "response": {
            "response_id": response_id, "response_json": response_json, "model": model,
            "prompt_version": prompt_version, "knowledge_version": knowledge_version,
        },
        "quality_evaluation": {"checks": [], "passed_count": 0, "failed_count": 0},
        "execution_metadata": {},
    }


def test_run_report_reading_comparison_review_reuses_existing_draft_review_mode(monkeypatch):
    """新しいExpert AIモードを追加せず、既存draft_reviewモードをそのまま使っていることを
    呼び出し引数から確認する。"""
    client = FakeSupabaseClient()
    captured = {}

    def fake_run_expert_request_with_mode(client_, azure_, model_, mode, theme_id, query_text,
                                           draft_text, *, profile, execution_mode,
                                           actor_intelligence_enabled=None):
        captured["mode"] = mode
        captured["draft_text"] = draft_text
        captured["query_text"] = query_text
        return _fake_engine_response(
            client, request_id="req-rv2", response_id="resp-rv2",
            response_json={"review_findings": [
                {"severity": "Major", "finding": "report_readingはEvidence不足"},
            ]})

    monkeypatch.setattr(geo_expert_engine, "run_expert_request_with_mode",
                         fake_run_expert_request_with_mode)

    report_reading = {
        "report_primary_reading": "規制は緩和される方向にある。",
        "supporting_evidence": ["業界団体の声明"], "weakening_evidence": [],
    }
    expert_reading = {"candidates": [{"content": {"origin": "main_scenario",
                                                    "why_this_matters": "規制は強化される方向にある。"}}]}

    result = adapter.run_report_reading_comparison_review(
        client, azure_client=object(), model="gpt-test",
        report_reading=report_reading, expert_reading=expert_reading, actor_id="reviewer-1")

    assert captured["mode"] == "draft_review"
    assert "report_reading" in captured["draft_text"]
    assert "expert_ai_reading" in captured["draft_text"]
    assert result["findings"][0]["finding"] == "report_readingはEvidence不足"
    assert result["request_id"] == "req-rv2"
    assert result["response_id"] == "resp-rv2"


def test_run_report_reading_comparison_review_does_not_leak_unexplicit_content_instruction(monkeypatch):
    """report_readingに明記されていない内容を補完しないよう、query_textにno-hallucination
    指示が含まれていることを確認する（§Review V2絶対条件の継承）。"""
    client = FakeSupabaseClient()
    captured = {}

    def fake_run_expert_request_with_mode(client_, azure_, model_, mode, theme_id, query_text,
                                           draft_text, *, profile, execution_mode,
                                           actor_intelligence_enabled=None):
        captured["query_text"] = query_text
        return _fake_engine_response(
            client, request_id="req-rv2b", response_id="resp-rv2b",
            response_json={"review_findings": []})

    monkeypatch.setattr(geo_expert_engine, "run_expert_request_with_mode",
                         fake_run_expert_request_with_mode)

    adapter.run_report_reading_comparison_review(
        client, azure_client=object(), model="gpt-test",
        report_reading={"report_primary_reading": "X"}, expert_reading={"candidates": []},
        actor_id="reviewer-1")

    assert "明記されていない" in captured["query_text"]


def test_run_report_content_review_reuses_existing_draft_review_mode(monkeypatch):
    """Content Review（Phase R1）も新しいモードを追加せず、既存draft_reviewモードを
    転用していることを確認する。Reading Reviewとは別の入力（report_text本文）を渡す。"""
    client = FakeSupabaseClient()
    captured = {}

    def fake_run_expert_request_with_mode(client_, azure_, model_, mode, theme_id, query_text,
                                           draft_text, *, profile, execution_mode,
                                           actor_intelligence_enabled=None):
        captured["mode"] = mode
        captured["draft_text"] = draft_text
        captured["query_text"] = query_text
        return _fake_engine_response(
            client, request_id="req-content", response_id="resp-content",
            response_json={"review_findings": [
                {"severity": "Major", "finding": "本文のEvidenceが不足している"},
            ]})

    monkeypatch.setattr(geo_expert_engine, "run_expert_request_with_mode",
                         fake_run_expert_request_with_mode)

    result = adapter.run_report_content_review(
        client, azure_client=object(), model="gpt-test",
        report_text="レポート本文全体のテキスト。",
        report_reading={"report_primary_reading": "X"},
        expert_reading={"candidates": []}, actor_id="reviewer-1")

    assert captured["mode"] == "draft_review"
    assert "report_text" in captured["draft_text"]
    assert "レポート本文全体のテキスト" in captured["draft_text"]
    assert "重要な修正" in captured["query_text"]
    assert "Evidence不足" in captured["query_text"]
    assert result["findings"][0]["finding"] == "本文のEvidenceが不足している"
    assert result["request_id"] == "req-content"
    assert result["response_id"] == "resp-content"
