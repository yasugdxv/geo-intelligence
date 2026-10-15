"""REVIEW MODE（§17-22）とExpert AI Core呼び出し経路の分離を確認するテスト
（2026-09-25追加、ユーザー指示）。

CREATE MODEのReading生成（generate_scenario_candidates）はcase_analysis/deepを既定に
変更したが、REVIEW MODEのAlternative Reading生成は「今回は現状維持」（geo_llm_common.
call_llm_structuredの直接呼び出しのまま）という明示判断のため、Expert AI Core
（geo_expert_engine.py経由のtheme_idベースRetrieval）を一切経由しないことを確認する。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_expert_engine  # noqa: E402
import geo_ios_reading_exposure_composer as reading_exposure_composer  # noqa: E402
import geo_ios_review_import_service as review_import_service  # noqa: E402
import geo_llm_common  # noqa: E402


def test_review_import_service_does_not_import_expert_ai_engine():
    """モジュールレベルでgeo_expert_engine/geo_ios_expert_ai_adapterを一切参照しない
    （import自体が無い＝構造的にExpert AI Core Retrievalへ到達し得ないことの保証）。"""
    import geo_ios_review_import_service as mod
    module_globals = vars(mod)
    assert "geo_expert_engine" not in module_globals
    assert "expert_ai_adapter" not in module_globals
    assert "geo_ios_expert_ai_adapter" not in module_globals


def test_reconstruct_existing_reading_calls_llm_common_directly(monkeypatch):
    """Existing Reading Reconstructionは、geo_expert_engine.run_expert_request_with_mode
    を一切呼ばず、geo_llm_common.call_llm_structuredのみを呼ぶ。"""
    called_engine = {"count": 0}
    monkeypatch.setattr(geo_expert_engine, "run_expert_request_with_mode",
                         lambda *a, **kw: called_engine.update(count=called_engine["count"] + 1))

    def fake_call_llm_structured(client, model, system_prompt, user_prompt, schema, schema_name,
                                  **kwargs):
        return {"data": {
            "primary_reading": "本文からの読み", "key_actors": [], "actor_objectives": [],
            "relationships": [], "cue": None, "assumptions": [], "causal_logic": None,
            "direction_of_travel": None, "scenario": None, "supporting_evidence": [],
            "weakening_evidence": [], "missing_evidence": [], "not_explicit_fields": [],
        }}
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake_call_llm_structured)

    review_import_service.reconstruct_existing_reading(
        azure_client=object(), model="gpt-test", raw_text="サンプルレポート本文")

    assert called_engine["count"] == 0


def test_generate_alternative_readings_for_review_does_not_call_expert_engine(monkeypatch):
    """REVIEW MODEのAlternative Reading生成（generate_directed_exploration_reading経由）は
    Expert AI Engineを呼ばず、theme_idベースのDB全体Retrievalが発生しないことを保証する。"""
    called_engine = {"count": 0}
    monkeypatch.setattr(geo_expert_engine, "run_expert_request_with_mode",
                         lambda *a, **kw: called_engine.update(count=called_engine["count"] + 1))

    def fake_call_llm_structured(client, model, system_prompt, user_prompt, schema, schema_name,
                                  **kwargs):
        return {"data": {
            "insufficient_material": False, "title": "新しい読み", "reading": "本文に基づく代替の読み",
            "exploration_angle": "premise_challenge", "key_cue": "着眼点",
            "key_actors_and_structure": "Actor構造", "supporting_reasons": ["理由1"],
            "weakening_information": ["反証1"], "switch_condition": "条件",
        }}
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake_call_llm_structured)

    result = review_import_service.generate_alternative_readings_for_review(
        azure_client=object(), model="gpt-test",
        report_context={"current_situation": "状況説明", "analysis_theme": "テーマ"},
        current_reading_title="現在の読み", current_reading_why_this_matters="現在の読みの要旨",
        count=1)

    assert called_engine["count"] == 0
    assert len(result) == 1


def test_generate_directed_exploration_reading_has_no_mode_profile_concept():
    """generate_directed_exploration_readingのシグネチャにmode/profileパラメータが
    無いこと（Expert AI Coreのmode/profile概念を一切持ち込んでいないことの静的確認）。"""
    import inspect
    sig = inspect.signature(reading_exposure_composer.generate_directed_exploration_reading)
    assert "mode" not in sig.parameters
    assert "profile" not in sig.parameters
