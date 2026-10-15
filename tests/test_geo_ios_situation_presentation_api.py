"""Geopolitical Intelligence OS: Situation Analysis Presentation APIのテスト。

既存Analysis Session/Scenario Candidate/Analysis Design/Expert AI Responseを読み取り、
新しいLLM呼び出しなしでpresentationを組み立てられること、debug=falseで_sourceが
隠れること、raw_analysisとpresentationが分離されていることを確認する。
"""
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_analysis_session_service as session_service  # noqa: E402
import geo_ios_situation_presentation_api as presentation_api  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _seed_session_with_candidates(client, *, response_json: dict):
    session = session_service.create_session(client, title="テスト案件", user_id="analyst-1", theme="今回何を調べるべきか")
    client.tables.setdefault("geo_expert_responses", []).append({
        "response_id": "resp-1", "response_json": response_json,
    })
    candidates = session_service.record_scenario_candidates(
        client, session["analysis_session_id"],
        candidates=[
            {"title": "主シナリオ", "content": {
                "origin": "main_scenario", "why_this_matters": "理由",
                "source_cqas": {"response_id": "resp-1"},
                "raw": {"description": "主シナリオの説明", "confidence": "中",
                        "selection_rationale": "理由", "supporting_evidence": ["証拠A"]},
            }},
            {"title": "代替シナリオ", "content": {
                "origin": "alternative_scenario", "why_this_matters": "分岐条件",
                "source_cqas": {"response_id": "resp-1"},
                "raw": {"description": "代替シナリオの説明", "confidence": "低",
                        "weakening_conditions": ["材料Y"]},
            }},
        ],
        actor_id="analyst-1")
    session_service.create_design_version(
        client, session["analysis_session_id"],
        fields={"investigation_question": "今回何を調べるべきか", "watch_points": ["設計側の監視点"]},
        change_note="初版", created_by="analyst-1")
    return session, candidates


def _minimal_response_json():
    return {
        "one_line_assessment": "一言見立て",
        "key_point_assessment": {"primary_key_point": "主要な蝶番", "selection_rationale": "根拠",
                                  "hinge_structure": {"primary_hinge": "変数A"}},
        "hypothesis_register": [], "weakening_information": None, "contradicting_evidence": [],
        "retraction_conditions": ["撤回条件1"], "key_actor_profiles": [],
        "monitoring_indicators": ["監視指標1"], "identified_knowledge_gaps": [], "additional_research": [],
    }


def test_get_situation_presentation_builds_from_existing_data_only(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(presentation_api, "_client", client)
    session, _ = _seed_session_with_candidates(client, response_json=_minimal_response_json())

    result = presentation_api.get_situation_presentation(session["analysis_session_id"])

    assert result["presentation_version"] == "situation_analysis_v1"
    assert result["presentation"]["key_point"]["primary_key_point"]["value"] == "主要な蝶番"
    assert len(result["presentation"]["interpretations"]) == 2


def test_debug_false_strips_source_fields(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(presentation_api, "_client", client)
    session, _ = _seed_session_with_candidates(client, response_json=_minimal_response_json())

    result = presentation_api.get_situation_presentation(session["analysis_session_id"], debug=False)
    assert "_source" not in str(result["presentation"])


def test_debug_true_keeps_source_fields(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(presentation_api, "_client", client)
    session, _ = _seed_session_with_candidates(client, response_json=_minimal_response_json())

    result = presentation_api.get_situation_presentation(session["analysis_session_id"], debug=True)
    assert "_source" in str(result["presentation"])


def test_raw_analysis_and_presentation_are_separate_top_level_keys(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(presentation_api, "_client", client)
    session, _ = _seed_session_with_candidates(client, response_json=_minimal_response_json())

    result = presentation_api.get_situation_presentation(session["analysis_session_id"])
    assert set(result.keys()) >= {"analysis_session_id", "presentation_version", "raw_analysis", "presentation"}
    assert result["raw_analysis"]["expert_response_id"] == "resp-1"


def test_merge_reading_presentation_threads_key_actor_profiles_from_response_json(monkeypatch):
    """Actor-first化（2026-09-25）: response_json.key_actor_profilesが、Presentation
    Transformation Layer（get_or_build_reading_presentation）へそのまま橋渡しされること。
    Expert AI Core自体（response_jsonの生成方法）には一切触れていない——既に永続化済みの
    response_jsonを読むだけ。"""
    client = FakeSupabaseClient()
    monkeypatch.setattr(presentation_api, "_client", client)
    monkeypatch.setattr(presentation_api, "_azure_client", object())
    monkeypatch.setattr(presentation_api, "_model", "gpt-test")
    response_json = _minimal_response_json()
    response_json["key_actor_profiles"] = [{"actor": "Trump", "persistent_preference": "強さの誇示"}]
    session, _ = _seed_session_with_candidates(client, response_json=response_json)

    captured = {}

    def fake_get_or_build(client_, azure_client, model, analysis_session_id, reading_exposure_cards,
                           *, situation_text, question_text, actor_id, key_actor_profiles=None):
        captured["key_actor_profiles"] = key_actor_profiles
        return {}

    monkeypatch.setattr(session_service, "get_or_build_reading_presentation", fake_get_or_build)

    # 2026-10-04: LLM経路を1本にするため、新Expert Review UIが使われる場合は旧Presentation
    # Transformation（reading_presentation）を実行しない。旧層は、新UI用Viewの構築に失敗した
    # 場合（旧UIへのFallback）にだけ使われる——その経路でも従来どおりActor情報が渡ることを確認する。
    import geo_ios_expert_review_view as expert_review_view

    def view_fails(*args, **kwargs):
        raise RuntimeError("expert review view unavailable")

    monkeypatch.setattr(expert_review_view, "attach_expert_review", view_fails)

    presentation_api.get_situation_presentation(session["analysis_session_id"])

    assert captured["key_actor_profiles"] == [{"actor": "Trump", "persistent_preference": "強さの誇示"}]


def test_legacy_presentation_transformation_is_skipped_when_expert_review_ui_is_used(monkeypatch):
    """2026-10-04: 新Expert Review UIが使われる（expert_reviewが付く）Sessionでは、旧6段落
    Presentation Transformation（別のLLM呼び出し）を実行しない（二重LLM・待ち時間・文章不整合の防止）。"""
    client = FakeSupabaseClient()
    monkeypatch.setattr(presentation_api, "_client", client)
    monkeypatch.setattr(presentation_api, "_azure_client", object())
    monkeypatch.setattr(presentation_api, "_model", "gpt-test")
    session, _ = _seed_session_with_candidates(client, response_json=_minimal_response_json())

    called = []
    monkeypatch.setattr(session_service, "get_or_build_reading_presentation",
                        lambda *a, **k: called.append(1) or {})

    result = presentation_api.get_situation_presentation(session["analysis_session_id"])

    assert called == []
    assert "expert_review" in result["presentation"]


def test_unknown_session_id_returns_404(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(presentation_api, "_client", client)
    with pytest.raises(HTTPException) as exc_info:
        presentation_api.get_situation_presentation("does-not-exist")
    assert exc_info.value.status_code == 404


def test_session_without_candidates_returns_404(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(presentation_api, "_client", client)
    session = session_service.create_session(client, title="候補なし", user_id="analyst-1", theme="テーマ")
    with pytest.raises(HTTPException) as exc_info:
        presentation_api.get_situation_presentation(session["analysis_session_id"])
    assert exc_info.value.status_code == 404


# ---- 新設route: /api/geo/situation-presentation/{response_id} ------------------
# Reading Presentation Layer v1(ユーザー指示): IOSのAnalysis Session/Scenario Candidateを
# 経由しない、response_id直接指定の汎用route。表示ロジック（presentation_service）は
# 既存IOS routeと共通で、入口のみ異なることを確認する。

def _seed_response_only(client, *, response_json: dict, request_id: str = "req-1",
                         response_id: str = "resp-1", query_text: str = "今回何を調べるべきか"):
    client.tables.setdefault("geo_expert_requests", []).append({
        "request_id": request_id, "query_text": query_text,
    })
    client.tables.setdefault("geo_expert_responses", []).append({
        "response_id": response_id, "request_id": request_id, "response_json": response_json,
    })


def test_get_situation_presentation_by_response_id_builds_from_main_and_alternative_scenarios(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(presentation_api, "_client", client)
    response_json = _minimal_response_json()
    response_json["main_scenario"] = {
        "description": "主シナリオの説明", "confidence": "中", "selection_rationale": "理由",
        "supporting_evidence": ["証拠A"],
    }
    response_json["alternative_scenarios"] = [
        {"description": "代替シナリオの説明", "confidence": "低", "weakening_conditions": ["材料Y"]},
    ]
    _seed_response_only(client, response_json=response_json)

    result = presentation_api.get_situation_presentation_by_response_id("resp-1")

    assert result["response_id"] == "resp-1"
    assert result["presentation_version"] == "situation_analysis_v1"
    assert result["presentation"]["key_point"]["investigation_question"]["value"] == "今回何を調べるべきか"
    assert len(result["presentation"]["interpretations"]) == 2
    assert result["presentation"]["interpretations"][0]["is_main"] is True
    assert result["presentation"]["interpretations"][1]["closest_alternative"] is True


def test_get_situation_presentation_by_response_id_unknown_response_id_returns_404(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(presentation_api, "_client", client)
    with pytest.raises(HTTPException) as exc_info:
        presentation_api.get_situation_presentation_by_response_id("does-not-exist")
    assert exc_info.value.status_code == 404


def test_get_situation_presentation_by_response_id_missing_main_scenario_returns_404(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(presentation_api, "_client", client)
    _seed_response_only(client, response_json=_minimal_response_json())
    with pytest.raises(HTTPException) as exc_info:
        presentation_api.get_situation_presentation_by_response_id("resp-1")
    assert exc_info.value.status_code == 404


def test_does_not_mutate_stored_response_json(monkeypatch):
    """Presentation層がraw_analysis（既存geo_expert_responses.response_json）を書き換えて
    いないことを確認する（§13 raw_analysis/presentation分離の絶対条件）。"""
    client = FakeSupabaseClient()
    monkeypatch.setattr(presentation_api, "_client", client)
    response_json = _minimal_response_json()
    session, _ = _seed_session_with_candidates(client, response_json=response_json)

    presentation_api.get_situation_presentation(session["analysis_session_id"])

    stored = [r for r in client.tables["geo_expert_responses"] if r["response_id"] == "resp-1"][0]
    assert stored["response_json"] == _minimal_response_json()
