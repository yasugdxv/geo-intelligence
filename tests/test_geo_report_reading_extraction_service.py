import geo_report_reading_extraction_service as extraction_service


def test_extract_report_reading_calls_llm_common_directly_not_expert_engine(monkeypatch):
    """Expert AI Core（geo_expert_engine）を一切経由せず、既存geo_llm_common.
    call_llm_structuredのみを呼ぶ（geo_ios_review_import_service/
    geo_report_service.extract_report_metadata_with_aiと同じ既存パターン）。"""
    import geo_expert_engine

    called_engine = {"count": 0}
    monkeypatch.setattr(
        geo_expert_engine, "run_expert_request_with_mode",
        lambda *a, **kw: called_engine.update(count=called_engine["count"] + 1))

    captured = {}

    def fake_call_llm_structured(client, model, system_prompt, user_prompt, schema, schema_name, **kwargs):
        captured["schema_name"] = schema_name
        captured["schema"] = schema
        return {"data": {
            "analysis_theme": "米中半導体規制", "current_situation": "規制強化が続いている。",
            "question": "規制はどこまで強化されるか。", "report_context": "2026年9月時点の分析。",
            "cue": "新たな輸出規制の発表。", "key_actors": ["US", "China"],
            "actor_objectives": [{"actor": "US", "objective": "技術優位の維持"}],
            "relationships": [{"between": "US-China", "description": "対立"}],
            "assumptions": ["規制は段階的に強化される"],
            "report_primary_reading": "規制は今後も強化される方向にある。",
            "causal_logic": "技術覇権競争が規制強化を駆動している。",
            "direction_of_travel": "強化方向", "scenario": "追加規制の発表",
            "switch_condition": None, "supporting_evidence": ["直近の規制発表"],
            "weakening_evidence": [], "missing_evidence": [], "missing_reading": None,
            "business_impact": None,
            "not_explicit_fields": ["switch_condition", "missing_reading", "business_impact"],
        }}

    monkeypatch.setattr(extraction_service.geo_llm_common, "call_llm_structured", fake_call_llm_structured)

    result = extraction_service.extract_report_reading(
        azure_client=object(), model="gpt-test", raw_text="サンプルレポート本文")

    assert called_engine["count"] == 0
    assert captured["schema_name"] == "geo_report_reading_extraction_v1"
    assert captured["schema"]["additionalProperties"] is False
    assert result["report_primary_reading"] == "規制は今後も強化される方向にある。"
    assert result["key_actors"] == ["US", "China"]
    assert "switch_condition" in result["not_explicit_fields"]
    assert "missing_reading" in result["not_explicit_fields"]
    assert "business_impact" in result["not_explicit_fields"]


def test_extract_report_reading_schema_requires_all_new_v2_fields():
    """Review V2で追加したfield（switch_condition/business_impact/missing_reading/
    report_primary_reading）がスキーマのrequiredに含まれている（nullは許容するが、
    フィールド自体の省略は許さない——no-hallucination規律の一部）。"""
    required = set(extraction_service.REPORT_READING_SCHEMA["required"])
    for field in ("switch_condition", "business_impact", "missing_reading", "report_primary_reading"):
        assert field in required


def test_extract_report_reading_schema_allows_null_for_core_string_fields():
    """実運用で、モデルがanalysis_theme/current_situation/question/report_contextに
    対して（指示に反して）nullを返しschema検証に失敗する事象を確認した（2026-10-03）。
    他のoptional文字列項目と同じくnullを許容するようスキーマを緩め、頑健性を上げる
    （呼び出し側は既にNone安全な.get()アクセスのため、追加のコード変更は不要）。"""
    properties = extraction_service.REPORT_READING_SCHEMA["properties"]
    for field in ("analysis_theme", "current_situation", "question", "report_context"):
        assert properties[field]["type"] == ["string", "null"]
