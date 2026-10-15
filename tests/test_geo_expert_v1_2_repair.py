"""地政学専門家AI v1.2(Session2): Quality Guard修復ループのテスト。

geo_expert_engine.run_expert_requestは、モード別v1.2必須項目チェック
（geo_expert_quality_guard.MODE_V12_CHECK_NAMES）が失敗した場合のみ、最大1回の修復生成を
行う。1リクエストあたりの実Azure OpenAI API呼び出しは、通常生成（最大2回）＋修復生成
（最大1回、内部リトライ無効化）の合計最大3回に制御されることを確認する
（無限リトライしないこと、モード別必須項目が既に揃っている場合は修復を行わないことも含む）。
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_expert_engine as engine  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

THEME_ID = "theme-1"

BASE_TABLES = {
    "geo_themes": [{"theme_id": THEME_ID, "name_ja": "台湾有事", "code": "taiwan_strait"}],
    "geo_events": [{
        "event_id": "e-1", "theme_id": THEME_ID, "actor": "PLA", "action": "越境",
        "verified": True, "created_at": "2026-08-01T00:00:00+00:00",
    }],
}

RESPONSE_MISSING_V12_FIELDS = {
    "one_line_assessment": "PLAが中間線越境を続けている。",
    "claims": [{"claim_type": "fact", "text": "PLA機が越境した", "source_ref": "e-1"}],
    "stakeholders": [{"actor": "PLA"}],
    "main_scenario": {"description": "圧力の段階的強化", "confidence": "中", "supporting_evidence": ["頻度増加"]},
    "strongest_counterargument": "訓練の一環の可能性",
    "retraction_conditions": ["頻度が減少した場合"],
    "business_impact": {"direct": None, "indirect": "警戒感の上昇", "unrelated_note": None},
}

RESPONSE_WITH_V12_FIELDS = {
    **RESPONSE_MISSING_V12_FIELDS,
    "main_scenario": {
        **RESPONSE_MISSING_V12_FIELDS["main_scenario"],
        "selection_rationale": "越境頻度が過去水準を上回っており説明力が高いため",
    },
    "key_point_assessment": {
        "candidate_key_points": [{
            "key_point": "PLAの越境頻度が段階的に引き上げられているか", "why_it_could_be_key": "頻度増加の主因",
            "control_type": "unknown", "what_it_explains": "越境頻度の増加傾向", "what_it_does_not_explain": "個別の停止条件",
        }],
        "primary_key_point": "PLAの越境頻度が段階的に引き上げられているか", "selection_rationale": "頻度データが最も一貫して説明する",
        "hinge_structure": {"primary_hinge": "PLAの作戦方針"},
        "control_type": "unknown", "supporting_evidence_ids": None,
        "disconfirming_signal": "越境頻度が明確に減少に転じた場合", "confidence": "中",
    },
    "key_stakeholder_assessment": {
        "determination": "single", "selected_actor_ids": ["pla"],
        "rationale": "軍事作戦の実施可否を決定できるのはPLAのため", "confidence": "中",
    },
    "key_actor_profiles": [{"actor_ref": "pla", "actor": "PLA", "confidence": "中",
                             "formal_role": "台湾海峡での軍事的圧力行使を担う実行部隊"}],
}


def _resp(content: str, usage=None):
    m = MagicMock(choices=[MagicMock(message=MagicMock(content=content))])
    m.usage = usage
    return m


def test_no_repair_when_v12_checks_already_pass():
    """v1.2必須項目が最初から揃っている場合は修復生成を行わない（実API呼び出しは1回のみ）。"""
    client = FakeSupabaseClient(tables=BASE_TABLES)
    azure_client = MagicMock()
    azure_client.chat.completions.create.return_value = _resp(json.dumps(RESPONSE_WITH_V12_FIELDS))

    result = engine.run_expert_request(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "PLAの越境状況を分析して",
    )

    assert azure_client.chat.completions.create.call_count == 1
    assert result["quality_evaluation"]["repair_attempted"] is False
    assert result["quality_evaluation"]["completeness_status"] == "complete"
    assert result["quality_evaluation"]["initial_failed_checks"] == []
    assert result["quality_evaluation"]["remaining_failed_checks"] == []


def test_repair_runs_once_and_succeeds_when_v12_fields_missing_then_supplied():
    """v1.2必須項目が欠落 → 1回だけ修復生成 → 補われて合格するケース（実API呼び出しは2回）。"""
    client = FakeSupabaseClient(tables=BASE_TABLES)
    azure_client = MagicMock()
    azure_client.chat.completions.create.side_effect = [
        _resp(json.dumps(RESPONSE_MISSING_V12_FIELDS)),
        _resp(json.dumps(RESPONSE_WITH_V12_FIELDS)),
    ]

    result = engine.run_expert_request(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "PLAの越境状況を分析して",
    )

    assert azure_client.chat.completions.create.call_count == 2
    assert "key_stakeholder_v12_fields_present" in result["quality_evaluation"]["initial_failed_checks"]
    assert result["quality_evaluation"]["repair_attempted"] is True
    assert result["quality_evaluation"]["completeness_status"] == "complete"
    assert result["quality_evaluation"]["remaining_failed_checks"] == []
    assert result["quality_evaluation"]["failed_count"] == 0
    assert result["response"]["response_json"]["key_stakeholder_assessment"]["determination"] == "single"


def test_repair_is_attempted_at_most_once_even_if_still_failing():
    """修復後もv1.2必須項目が欠落したままの場合、追加のリトライは行わず
    incomplete_after_repairとして報告する（無限リトライしないことの確認）。"""
    client = FakeSupabaseClient(tables=BASE_TABLES)
    azure_client = MagicMock()
    azure_client.chat.completions.create.side_effect = [
        _resp(json.dumps(RESPONSE_MISSING_V12_FIELDS)),
        _resp(json.dumps(RESPONSE_MISSING_V12_FIELDS)),
    ]

    result = engine.run_expert_request(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "PLAの越境状況を分析して",
    )

    # 通常生成1回＋修復生成1回=2回で打ち止め（3回目は呼ばれない）
    assert azure_client.chat.completions.create.call_count == 2
    assert result["quality_evaluation"]["repair_attempted"] is True
    assert result["quality_evaluation"]["completeness_status"] == "incomplete_after_repair"
    assert "key_stakeholder_v12_fields_present" in result["quality_evaluation"]["remaining_failed_checks"]


def test_total_real_api_calls_capped_at_three_including_schema_fallback_and_repair():
    """通常生成が構造化出力の解析に失敗し手動JSON fallbackへ回った場合（実質2回）でも、
    修復生成は1回のみに制御され、合計で実API呼び出しが3回を超えないことを確認する。"""
    client = FakeSupabaseClient(tables=BASE_TABLES)
    azure_client = MagicMock()
    azure_client.chat.completions.create.side_effect = [
        _resp("これはJSONではありません"),  # 構造化出力の解析失敗 → 手動JSON fallbackへ
        _resp(json.dumps(RESPONSE_MISSING_V12_FIELDS)),  # 手動JSON fallback（Schema検証は通る）
        _resp(json.dumps(RESPONSE_MISSING_V12_FIELDS)),  # Quality Guard修復（1回のみ、それでも失敗）
    ]

    result = engine.run_expert_request(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "PLAの越境状況を分析して",
    )

    assert azure_client.chat.completions.create.call_count == 3
    assert result["quality_evaluation"]["repair_attempted"] is True
    assert result["quality_evaluation"]["completeness_status"] == "incomplete_after_repair"


def test_repair_not_triggered_for_mode_without_v12_required_checks():
    """analyst_coachingはモード別v1.2必須項目チェックの対象外のため、
    key_stakeholder_assessmentが無くても修復生成は行わない。"""
    client = FakeSupabaseClient(tables=BASE_TABLES)
    azure_client = MagicMock()
    azure_client.chat.completions.create.return_value = _resp(json.dumps({
        **RESPONSE_MISSING_V12_FIELDS,
        "analyst_coaching_notes": ["最初に注目すべき言葉"],
    }))

    result = engine.run_expert_request(
        client, azure_client, "gpt-4o", "analyst_coaching", THEME_ID, "この分析から何を学ぶべきか",
    )

    assert azure_client.chat.completions.create.call_count == 1
    assert result["quality_evaluation"]["repair_attempted"] is False
