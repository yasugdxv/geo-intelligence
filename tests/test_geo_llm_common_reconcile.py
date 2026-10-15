"""geo_llm_common.reconcile_batch_llm_results のテスト（2026-09-11、Prompt1
バッチ化最適化: 1バッチ=複数クラスタを1回のLLM呼び出しで処理する際、missing/duplicate/
unknown/invalidを個別に検出し、正常なアイテムを失わずにアイテム単位で再試行できる
ようにするための検証ロジック）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_llm_common as llm  # noqa: E402

_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "enum": ["High", "Medium", "Low"]},
    },
    "required": ["label"],
    "additionalProperties": False,
}


def test_all_items_valid_returns_no_problems():
    result = llm.reconcile_batch_llm_results(
        ["A", "B"],
        [{"cluster_id": "A", "label": "High"}, {"cluster_id": "B", "label": "Low"}],
        _ITEM_SCHEMA,
    )
    assert result["valid"] == {"A": {"label": "High"}, "B": {"label": "Low"}}
    assert result["missing"] == []
    assert result["duplicate"] == []
    assert result["unknown"] == []
    assert result["invalid"] == []


def test_missing_item_detected():
    result = llm.reconcile_batch_llm_results(
        ["A", "B"], [{"cluster_id": "A", "label": "High"}], _ITEM_SCHEMA)
    assert result["missing"] == ["B"]
    assert "A" in result["valid"]


def test_duplicate_item_detected_and_first_occurrence_kept():
    result = llm.reconcile_batch_llm_results(
        ["A"],
        [{"cluster_id": "A", "label": "High"}, {"cluster_id": "A", "label": "Low"}],
        _ITEM_SCHEMA,
    )
    assert result["duplicate"] == ["A"]
    assert result["valid"]["A"] == {"label": "High"}


def test_unknown_id_not_in_input_is_reported_and_ignored():
    result = llm.reconcile_batch_llm_results(
        ["A"],
        [{"cluster_id": "A", "label": "High"}, {"cluster_id": "ZZZ_invented", "label": "High"}],
        _ITEM_SCHEMA,
    )
    assert result["unknown"] == ["ZZZ_invented"]
    assert result["valid"] == {"A": {"label": "High"}}


def test_schema_invalid_item_detected_without_losing_other_valid_items():
    result = llm.reconcile_batch_llm_results(
        ["A", "B"],
        [{"cluster_id": "A", "label": "High"}, {"cluster_id": "B", "label": "NotAnEnumValue"}],
        _ITEM_SCHEMA,
    )
    assert result["invalid"] == ["B"]
    assert result["valid"] == {"A": {"label": "High"}}


def test_item_without_id_field_is_silently_skipped_and_counts_as_missing():
    result = llm.reconcile_batch_llm_results(
        ["A"], [{"label": "High"}], _ITEM_SCHEMA)
    assert result["missing"] == ["A"]
    assert result["valid"] == {}


def test_non_dict_items_in_results_are_ignored():
    result = llm.reconcile_batch_llm_results(
        ["A"], ["not a dict", {"cluster_id": "A", "label": "High"}], _ITEM_SCHEMA)
    assert result["valid"] == {"A": {"label": "High"}}
