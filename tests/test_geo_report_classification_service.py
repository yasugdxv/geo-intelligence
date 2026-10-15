import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_classification_service as classify  # noqa: E402

DIMENSION_CONFIG = {
    "country_focus": {
        "label": "対象国", "type": "single", "unknown_value": "Unknown",
        "allowed_values": ["US", "Iran", "China", "Unknown"],
        "definition": "この記事が主に扱う国", "evidence_rule": "本文中で明示的に主体として言及",
    },
    "response_orientation": {
        "label": "反応の方向性", "type": "multi", "unknown_value": "Unknown",
        "allowed_values": ["Supportive", "Critical", "Neutral", "Unknown"],
        "definition": "対象への反応の方向性", "evidence_rule": "評価的な文言の有無",
    },
}

NARRATIVE_ROW = {
    "report_topic_label": "各国政府の受け止め", "narrative_name": "仲介・緊張緩和優先",
    "narrative_description": "対話・仲介を重視する姿勢",
    "dimension_config_json": json.dumps(DIMENSION_CONFIG, ensure_ascii=False),
    "evidence_policy_json": json.dumps({"minimum_strength": "Medium"}, ensure_ascii=False),
    "display_rules_json": json.dumps({"translate_evidence_to_japanese": True}, ensure_ascii=False),
    "report_priority": "High",
}

REPRESENTATIVES = [
    {"record_id": "r1", "title": "Mediation push", "text_for_review": "US officials push for mediation with Iran.",
     "source_name": "Example News", "published_at": "2026-08-10T00:00:00Z", "language": "English",
     "source_bucket": "media"},
    {"record_id": "r2", "title": "Critical response", "text_for_review": "China criticizes US sanctions policy.",
     "source_name": "Example Wire", "published_at": "2026-08-11T00:00:00Z", "language": "English",
     "source_bucket": "media"},
]


def _valid_classification():
    return {
        "dimensions": {
            "country_focus": {
                "value": "US", "apply_to_cluster": "false", "confidence": "High",
                "evidence_record_ids": ["r1"], "rationale": "r1のみ米国主体",
            },
            "response_orientation": {
                "value": ["Supportive"], "apply_to_cluster": "false", "confidence": "Medium",
                "evidence_record_ids": ["r1"], "rationale": "r1が支持的",
            },
        },
        "record_level_exceptions": [
            {"dimension_id": "country_focus", "record_id": "r2", "value": "China", "rationale": "r2は中国主体"},
        ],
        "japanese_translations": [
            {"record_id": "r1", "japanese_translation": "米当局はイランとの仲介を推進している。"},
        ],
        "cluster_notes": "",
    }


def test_build_dimension_property_schema_single_and_multi():
    single_schema = classify.build_dimension_property_schema(DIMENSION_CONFIG["country_focus"])
    assert single_schema["properties"]["value"]["type"] == "string"
    assert set(single_schema["properties"]["value"]["enum"]) == {"US", "Iran", "China", "Unknown"}

    multi_schema = classify.build_dimension_property_schema(DIMENSION_CONFIG["response_orientation"])
    assert multi_schema["properties"]["value"]["type"] == "array"


def test_build_cluster_classification_schema_structure():
    schema = classify.build_cluster_classification_schema(DIMENSION_CONFIG)
    assert set(schema["properties"]["dimensions"]["required"]) == {"country_focus", "response_orientation"}
    assert schema["properties"]["dimensions"]["additionalProperties"] is False


def test_get_dimension_config_returns_configured_value_when_present():
    row = {"dimension_config_json": json.dumps(DIMENSION_CONFIG, ensure_ascii=False)}
    assert classify.get_dimension_config(row) == DIMENSION_CONFIG


def test_get_dimension_config_falls_back_to_default_when_missing():
    """Babel Street等の外部ツール由来のNarrative Masterはdimension_config_json列を
    持たないことがある。空・欠落の場合はDEFAULT_DIMENSION_CONFIGへ自動フォールバック
    し、Narrative Master CSV側にこの列を必須要求しない。"""
    for row in ({}, {"dimension_config_json": ""}, {"dimension_config_json": "{}"}):
        config = classify.get_dimension_config(row)
        assert config == classify.DEFAULT_DIMENSION_CONFIG
        # フォールバック後もschemaが空property（additionalProperties違反の原因）に
        # ならないことを確認する
        schema = classify.build_cluster_classification_schema(config)
        assert len(schema["properties"]["dimensions"]["properties"]) > 0


def test_build_classification_user_prompt_includes_dimensions_and_representatives():
    prompt = classify.build_classification_user_prompt(NARRATIVE_ROW, {"cluster_id": "CL-1"}, REPRESENTATIVES)
    assert "country_focus" in prompt
    assert "response_orientation" in prompt
    assert "r1" in prompt and "r2" in prompt
    assert "翻訳指示" in prompt  # display_rules_json.translate_evidence_to_japanese=trueを反映


def test_classify_cluster_calls_llm_and_returns_structured_data():
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(_valid_classification())))],
    )
    result = classify.classify_cluster(mock_client, "gpt-4o", NARRATIVE_ROW, {"cluster_id": "CL-1"}, REPRESENTATIVES)
    assert result["data"]["dimensions"]["country_focus"]["value"] == "US"
    assert mock_client.chat.completions.create.called


def test_validity_checks_pass_on_well_formed_classification():
    checks = classify.run_classification_validity_checks(_valid_classification(), REPRESENTATIVES, NARRATIVE_ROW)
    assert classify.classification_validity_status(checks) == "PASS"
    assert checks["evidence_ids_not_fabricated"]["passed"] is True
    assert checks["dimension_without_evidence"]["passed"] is True


def test_dimension_without_evidence_detected():
    bad = _valid_classification()
    bad["dimensions"]["country_focus"]["evidence_record_ids"] = []
    checks = classify.run_classification_validity_checks(bad, REPRESENTATIVES, NARRATIVE_ROW)
    assert checks["dimension_without_evidence"]["passed"] is False
    assert classify.classification_validity_status(checks) == "REVIEW REQUIRED"


def test_fabricated_evidence_id_detected():
    bad = _valid_classification()
    bad["dimensions"]["country_focus"]["evidence_record_ids"] = ["r1", "r999-does-not-exist"]
    checks = classify.run_classification_validity_checks(bad, REPRESENTATIVES, NARRATIVE_ROW)
    assert checks["evidence_ids_not_fabricated"]["passed"] is False


def test_unsafe_cluster_propagation_detected():
    bad = _valid_classification()
    bad["dimensions"]["country_focus"]["apply_to_cluster"] = "true"
    # record_level_exceptionsはcountry_focusを指したままなので矛盾になるはず
    checks = classify.run_classification_validity_checks(bad, REPRESENTATIVES, NARRATIVE_ROW)
    assert checks["unsafe_cluster_propagation"]["passed"] is False


def test_translation_fidelity_failure_detected_when_missing():
    bad = _valid_classification()
    bad["japanese_translations"] = []  # r1が引用されているのに翻訳が無い
    checks = classify.run_classification_validity_checks(bad, REPRESENTATIVES, NARRATIVE_ROW)
    assert checks["translation_fidelity_failures"]["passed"] is False


def test_translation_not_required_when_display_rules_disabled():
    narrative_no_translation = dict(NARRATIVE_ROW)
    narrative_no_translation["display_rules_json"] = json.dumps({"translate_evidence_to_japanese": False})
    bad = _valid_classification()
    bad["japanese_translations"] = []
    checks = classify.run_classification_validity_checks(bad, REPRESENTATIVES, narrative_no_translation)
    assert checks["translation_fidelity_failures"]["passed"] is True


def test_semantic_dimension_conflict_detected():
    bad = _valid_classification()
    bad["dimensions"]["country_focus"]["apply_to_cluster"] = "uncertain"
    bad["dimensions"]["country_focus"]["evidence_record_ids"] = ["r1", "r2"]  # 全代表記事をevidence化
    checks = classify.run_classification_validity_checks(bad, REPRESENTATIVES, NARRATIVE_ROW)
    assert checks["semantic_dimension_conflicts"]["passed"] is False


def test_compute_analytical_coverage_basic():
    narrative_configs = {("S1", "S1-N04"): NARRATIVE_ROW}
    cluster_rows = [
        {"signal_id": "S1", "narrative_id": "S1-N04", "classification": {"dimensions": {
            "country_focus": {"apply_to_cluster": "true"},
            "response_orientation": {"apply_to_cluster": "false"},
        }}},
        {"signal_id": "S1", "narrative_id": "S1-N99", "classification": None},
    ]
    coverage = classify.compute_analytical_coverage(cluster_rows, narrative_configs)
    assert coverage["classified_cluster_count"] == 1
    assert coverage["total_cluster_count"] == 2
    assert coverage["high_priority_narrative_coverage"] == 1.0
    assert coverage["cluster_applicability_distribution"]["true"] == 1
    assert coverage["cluster_applicability_distribution"]["false"] == 1
