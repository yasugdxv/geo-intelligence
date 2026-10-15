"""geo_llm_common.py: Schema検証前の文字列配列正規化（_coerce_string_arrays_to_strings）の
テスト。Chunk6実ブラインドテストで発見した既存バグ（LLMがstring型フィールドへ文字列配列を
返し、Schema検証が失敗して未捕捉例外になる）の回帰防止。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_llm_common as llm  # noqa: E402


def test_coerce_string_array_field_joins_into_single_string():
    schema = {
        "type": "object",
        "properties": {
            "weakening_information": {"type": ["string", "null"]},
        },
    }
    data = {"weakening_information": ["理由A", "理由B"]}
    llm._coerce_string_arrays_to_strings(data, schema, schema)
    assert data["weakening_information"] == "理由A\n理由B"


def test_coerce_does_not_touch_genuine_array_field():
    schema = {
        "type": "object",
        "properties": {
            "monitoring_indicators": {"type": "array", "items": {"type": "string"}},
        },
    }
    data = {"monitoring_indicators": ["指標A", "指標B"]}
    llm._coerce_string_arrays_to_strings(data, schema, schema)
    assert data["monitoring_indicators"] == ["指標A", "指標B"]


def test_coerce_does_not_touch_already_correct_string_field():
    schema = {"type": "object", "properties": {"note": {"type": ["string", "null"]}}}
    data = {"note": "既に文字列"}
    llm._coerce_string_arrays_to_strings(data, schema, schema)
    assert data["note"] == "既に文字列"


def test_coerce_handles_nested_object_via_ref():
    schema = {
        "type": "object",
        "properties": {"main_scenario": {"$ref": "#/definitions/main_scenario"}},
        "definitions": {
            "main_scenario": {
                "type": "object",
                "properties": {"description": {"type": ["string", "null"]}},
            },
        },
    }
    data = {"main_scenario": {"description": ["文A", "文B"]}}
    llm._coerce_string_arrays_to_strings(data, schema, schema)
    assert data["main_scenario"]["description"] == "文A\n文B"


def test_coerce_handles_array_of_objects():
    schema = {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {
                "type": "object",
                "properties": {"text": {"type": ["string", "null"]}},
            }},
        },
    }
    data = {"items": [{"text": ["a", "b"]}, {"text": "c"}]}
    llm._coerce_string_arrays_to_strings(data, schema, schema)
    assert data["items"][0]["text"] == "a\nb"
    assert data["items"][1]["text"] == "c"


def test_coerce_ignores_mixed_type_list_leaving_validation_to_fail_as_before():
    schema = {"type": "object", "properties": {"note": {"type": ["string", "null"]}}}
    data = {"note": ["文字列", 123]}
    llm._coerce_string_arrays_to_strings(data, schema, schema)
    assert data["note"] == ["文字列", 123]


def test_coerce_flattens_list_of_name_description_dicts_into_one_string():
    # 実際に起きた失敗: type=["string","null"]のscenarioへ、複数シナリオが配列で返った
    import jsonschema
    schema = {"type": "object", "properties": {"scenario": {"type": ["string", "null"]}},
              "required": ["scenario"]}
    data = {"scenario": [{"name": "シナリオA", "description": "説明A"},
                         {"name": "シナリオB", "description": "説明B"}]}
    llm._coerce_string_arrays_to_strings(data, schema, schema)
    assert data["scenario"] == "シナリオA：説明A\nシナリオB：説明B"
    jsonschema.validate(data, schema)  # 以前はここで "is not of type 'string', 'null'"


def test_coerce_leaves_dict_lists_with_non_string_values_untouched():
    schema = {"type": "object", "properties": {"scenario": {"type": ["string", "null"]}}}
    data = {"scenario": [{"name": "A", "score": 3}]}
    llm._coerce_string_arrays_to_strings(data, schema, schema)
    assert data["scenario"] == [{"name": "A", "score": 3}]  # 意味が変わる変換はしない
