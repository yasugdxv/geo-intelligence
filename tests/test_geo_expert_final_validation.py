"""geo_expert_engine._validate_final_response: 最終応答のマスターSchema検証。

「最低1件」(minItems)の配列が空なだけなら失敗にせず不完全として続行し、それ以外の
不適合（型違いなど）は従来どおり例外にすることを確認する。
実際の失敗: レポートの読み比べ(draft_review)でAIがretraction_conditionsを空で返し、
Expert AI呼び出し全体が "[] should be non-empty" で失敗していた。
"""
import copy
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import jsonschema
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_expert_engine as engine  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402
from tests.test_geo_expert_staged_execution import (  # noqa: E402
    BASE_TABLES, THEME_ID, _DEEP_STAGE1_CALL1_RESPONSE)


def _staged_run(stage1_response):
    """既存テストと同じ4回呼び出し（Stage2が失敗しフォールバックで埋まる）で、
    有効な最終応答を作る。"""
    client = FakeSupabaseClient(tables=BASE_TABLES)
    azure_client = MagicMock()
    azure_client.chat.completions.create.side_effect = [
        MagicMock(choices=[MagicMock(message=MagicMock(content="not json"))], usage=None),
        MagicMock(choices=[MagicMock(message=MagicMock(content="still not json"))], usage=None),
        MagicMock(choices=[MagicMock(message=MagicMock(content=json.dumps(stage1_response)))], usage=None),
        MagicMock(choices=[MagicMock(message=MagicMock(content="stage2 not json either"))], usage=None),
    ]
    return engine.run_expert_request_staged(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "クエリ", profile="deep")


def test_valid_response_has_no_empty_required_arrays():
    result = _staged_run(_DEEP_STAGE1_CALL1_RESPONSE)
    assert engine._validate_final_response(result["response"]["response_json"]) == []


def test_empty_retraction_conditions_is_soft_not_an_error():
    response = copy.deepcopy(_staged_run(_DEEP_STAGE1_CALL1_RESPONSE)["response"]["response_json"])
    response["retraction_conditions"] = []
    # 例外にならず、空だった項目のパスが返る
    assert engine._validate_final_response(response) == ["retraction_conditions"]


def test_real_schema_violation_still_raises():
    response = copy.deepcopy(_staged_run(_DEEP_STAGE1_CALL1_RESPONSE)["response"]["response_json"])
    response["main_scenario"] = "文字列（本来はオブジェクト）"
    with pytest.raises(jsonschema.ValidationError):
        engine._validate_final_response(response)


def test_empty_array_together_with_real_violation_still_raises():
    response = copy.deepcopy(_staged_run(_DEEP_STAGE1_CALL1_RESPONSE)["response"]["response_json"])
    response["retraction_conditions"] = []
    response["main_scenario"] = "文字列（本来はオブジェクト）"
    with pytest.raises(jsonschema.ValidationError):
        engine._validate_final_response(response)


def test_staged_run_completes_as_incomplete_when_llm_returns_empty_retraction_conditions():
    stage1 = copy.deepcopy(_DEEP_STAGE1_CALL1_RESPONSE)
    stage1["retraction_conditions"] = []
    result = _staged_run(stage1)  # 以前は "[] should be non-empty" で例外
    assert result["response"]["response_json"]["retraction_conditions"] == []
    assert result["quality_evaluation"]["completeness_status"] != "complete"
