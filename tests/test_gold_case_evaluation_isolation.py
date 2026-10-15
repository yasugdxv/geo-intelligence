"""Phase 6A-0/6B: eval_harness/gold_case_evaluation_isolation.pyのテスト。

Gold Case品質評価専用のEvaluation Isolation層が、geo_case_hypotheses（Call1への
唯一確認済みのGold-contaminating経路）だけを遮断し、他の全テーブル・全メソッド
（Foundation/Reusable/Contested Context, Claims, Knowledge Gaps, Actor/Relationship/
Media Intelligence等）は無変更で通過させることを確認する。Production Behavior
（geo_expert_engine.run_expert_request_staged自体）は一切変更しない設計であることも、
本番実行経路を通した統合テストで確認する。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "eval_harness"))

import geo_expert_engine as engine  # noqa: E402
from gold_case_evaluation_isolation import (  # noqa: E402
    GoldCaseEvaluationIsolationClient, run_expert_request_staged_gold_isolated,
)
from tests._fakes import FakeSupabaseClient  # noqa: E402


def test_select_geo_case_hypotheses_returns_empty_without_touching_real_client():
    real = FakeSupabaseClient(tables={
        "geo_case_hypotheses": [{"hypothesis_id": "h1", "case_id": "case-1",
                                  "hypothesis_statement": "leaked gold text"}],
    })
    isolated = GoldCaseEvaluationIsolationClient(real)
    rows = isolated.select("geo_case_hypotheses", {"select": "*", "case_id": "eq.case-1"})
    assert rows == []


def test_select_other_tables_pass_through_unchanged():
    real = FakeSupabaseClient(tables={
        "geo_cases": [{"case_id": "case-1", "case_title": "t"}],
        "geo_claims": [{"claim_id": "c1", "origin_type": "case", "origin_id": "case-1"}],
    })
    isolated = GoldCaseEvaluationIsolationClient(real)
    assert isolated.select("geo_cases", {"select": "*"}) == real.tables["geo_cases"]
    assert isolated.select("geo_claims", {"select": "*"}) == real.tables["geo_claims"]


def test_non_select_methods_delegate_to_real_client():
    real = FakeSupabaseClient(tables={"geo_audit_logs": []})
    isolated = GoldCaseEvaluationIsolationClient(real)
    isolated.insert("geo_audit_logs", [{"actor": "test", "action": "noop"}])
    assert len(real.tables["geo_audit_logs"]) == 1


def test_run_expert_request_staged_gold_isolated_removes_existing_hypotheses_section():
    """case_context["hypotheses"]経由で「【既存の仮説】」がCall1 user_promptへ注入される
    経路が、isolationあり/なしでどう変わるかを、実際にrun_expert_request_stagedを通して
    確認する（Call1直前で例外を出させ、実際に構築されたuser_promptを検査する）。"""
    import json
    from unittest.mock import MagicMock

    tables = {
        "geo_cases": [{"case_id": "case-1", "case_title": "t", "original_question": "q",
                       "as_of_date": "2026-08-15"}],
        "geo_case_issues": [{"case_id": "case-1", "issue_id": "issue-1"}],
        "geo_case_hypotheses": [{
            "hypothesis_id": "h1", "case_id": "case-1", "hypothesis_type": "main",
            "hypothesis_statement": "GOLD_LEAK_MARKER_TEXT", "rationale": None,
            "supporting_claim_ids": [], "contradicting_claim_ids": [], "assumptions": [],
            "confirmation_indicators": [], "falsification_indicators": [], "confidence": "中",
            "status": "open", "created_at": "2026-08-15T00:00:00+00:00",
        }],
        "geo_knowledge_gaps": [], "geo_claims": [], "geo_entities": [], "geo_entity_issues": [],
        "geo_relationships": [], "geo_knowledge_items": [], "geo_events": [], "geo_documents": [],
    }

    captured_prompts = []

    def _capturing_call_llm_structured(*args, **kwargs):
        # 呼び出し引数(system_prompt, user_prompt)を記録し、Azure呼び出しを一切行わず打ち切る
        captured_prompts.append(args[3] if len(args) > 3 else kwargs.get("user_prompt"))
        raise RuntimeError("stop before any real Azure call")

    orig = engine.geo_llm_common.call_llm_structured
    engine.geo_llm_common.call_llm_structured = _capturing_call_llm_structured
    try:
        real_client = FakeSupabaseClient(tables=tables)
        isolated_client = GoldCaseEvaluationIsolationClient(real_client)
        try:
            engine.run_expert_request_staged(
                isolated_client, MagicMock(), "gpt-4o", "case_analysis", None, "query",
                case_id="case-1", profile="standard", persist=False)
        except RuntimeError:
            pass
        assert captured_prompts, "call_llm_structuredが呼ばれていません"
        assert "GOLD_LEAK_MARKER_TEXT" not in captured_prompts[0]
        assert "既存の仮説" not in captured_prompts[0]

        captured_prompts.clear()
        try:
            engine.run_expert_request_staged(
                real_client, MagicMock(), "gpt-4o", "case_analysis", None, "query",
                case_id="case-1", profile="standard", persist=False)
        except RuntimeError:
            pass
        assert "GOLD_LEAK_MARKER_TEXT" in captured_prompts[0]
    finally:
        engine.geo_llm_common.call_llm_structured = orig
