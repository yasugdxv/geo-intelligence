"""Reading Studio Presentation Transformation: Actor-first化（2026-09-25、ユーザー指示）の
Phase 5 Regression Test。

対象: Reading Proposalが「出来事の意味分類」ではなく「Actor Dynamics仮説」として
組み立てられるための配線（Presentation Layer prompt/schema、key_actor_profilesの
Presentation層への受け渡し、cache fingerprintのprompt version連動）。

Expert AI Core（geo_expert_engine.py）・CQAS・S Ranking・Reading Exposure選定ロジック
（geo_ios_reading_exposure_composer.py）には一切触れていないため、それらの既存テストは
このファイルでは対象としない。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_analysis_session_service as session_service  # noqa: E402
import geo_ios_reading_presentation_transform_service as transform_service  # noqa: E402
import geo_llm_common  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _sample_card(scid="sc-1", role="MAIN"):
    return {
        "scenario_candidate_id": scid,
        "reading_role": role,
        "title": "対ロ制裁法は",
        "description": {"value": "対ロ制裁法が可決された"},
        "key_actors": {"value": "Trump, 議会"},
        "why_this_matters": {"value": "重要"},
        "what_was_seen": {"value": "観測事実"},
        "what_was_doubted": {"value": "疑問点"},
        "hypothesis_formed": {"value": "仮説"},
        "selection_rationale": {"value": "選定理由"},
        "why_not_main": {"value": None},
        "outlook": {"if_correct": {"value": "正しければ"}, "weakens_if": {"value": "崩れる条件"}},
    }


def _sample_actor_profile():
    return {
        "actor": "Donald Trump / White House",
        "persistent_preference": "対外的な強さの誇示を一貫して重視する",
        "persistent_preference_evidence": "過去の複数の交渉場面での言動",
        "strategic_relationship_preference": "取引材料として関係を管理する",
        "perceived_path": "圧力を段階的に強めることで譲歩を引き出す",
        "issue_objective": "対ロ交渉における優位確保",
        "ultimate_objective": "対ロ関係の再定義",
        "driving_forces": [{"force": "議会圧力", "direction": "supporting", "description": "超党派の支持"}],
        "red_line": "同盟国との関係悪化",
        "available_leverage": "制裁の発動・緩和裁量",
        "event_role": {"role_type": "bargaining_card", "explanation": "交渉カードとして機能",
                        "intended_target_actor_ref": "Russia"},
    }


# ---- Test A: EVENT_ONLY_READING検出用スキーマ（actor_dynamics_check）が必須であること ----

def test_actor_dynamics_check_is_required_in_schema():
    item_schema = transform_service._TRANSFORM_SCHEMA["properties"]["readings"]["items"]
    assert "actor_dynamics_check" in item_schema["required"]
    check_schema = item_schema["properties"]["actor_dynamics_check"]
    assert check_schema["required"] == [
        "actor_is_grammatical_subject", "states_actor_objective_or_orientation",
        "states_counterparty_or_constraint", "is_event_only",
    ]
    assert check_schema["additionalProperties"] is False


# ---- Test A2: 13-4対応（2026-09-28、Final Expert Session Readiness Validation Phase B）
#      narrativeにorientation_and_counterparty_view/constraints_and_leverageが必須で
#      あること（Reading Detail画面で上位思想・相手観・制約・レバーを一読できるようにする） ----

def test_narrative_requires_orientation_and_constraints_structured_fields():
    narrative_schema = (
        transform_service._TRANSFORM_SCHEMA["properties"]["readings"]["items"]
        ["properties"]["narrative"]
    )
    assert "orientation_and_counterparty_view" in narrative_schema["properties"]
    assert "constraints_and_leverage" in narrative_schema["properties"]
    assert "orientation_and_counterparty_view" in narrative_schema["required"]
    assert "constraints_and_leverage" in narrative_schema["required"]


def test_system_prompt_instructs_structured_orientation_and_constraints_fields():
    prompt = transform_service._SYSTEM_PROMPT
    assert "orientation_and_counterparty_view" in prompt
    assert "constraints_and_leverage" in prompt


# ---- Test B: Actor Intelligence（key_actor_profiles）がReading個別ではなく共通の
#      参照ブロックとしてpromptへ渡ること ----

def test_actor_profiles_are_formatted_as_shared_reference_block():
    block = transform_service._format_actor_profiles_for_prompt([_sample_actor_profile()])
    assert "Donald Trump / White House" in block
    assert "persistent_preference" in block
    assert "perceived_path" in block
    assert "event_role" in block


def test_actor_profiles_empty_falls_back_to_explicit_no_data_notice():
    block = transform_service._format_actor_profiles_for_prompt([])
    assert "提供されていません" in block


def test_build_reading_presentation_transform_passes_actor_profiles_into_prompt(monkeypatch):
    captured = {}

    def fake_call_llm_structured(client, model, system_prompt, user_prompt, schema, schema_name,
                                  **kwargs):
        captured["user_prompt"] = user_prompt
        return {"data": {"readings": []}}

    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake_call_llm_structured)

    transform_service.build_reading_presentation_transform(
        azure_client=object(), model="gpt-test", situation_text="局面説明",
        question_text="問い", reading_cards=[_sample_card()],
        key_actor_profiles=[_sample_actor_profile()])

    assert "Actor Intelligence Reference" in captured["user_prompt"]
    assert "Donald Trump / White House" in captured["user_prompt"]


def test_build_reading_presentation_transform_works_without_actor_profiles(monkeypatch):
    """key_actor_profiles省略時も例外にならず、既存の生フィールドのみからprompt組み立てが
    継続できる（後方互換性、REVIEW MODE等actor_profilesを持たない経路のため）。"""
    captured = {}

    def fake_call_llm_structured(client, model, system_prompt, user_prompt, schema, schema_name,
                                  **kwargs):
        captured["user_prompt"] = user_prompt
        return {"data": {"readings": []}}

    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake_call_llm_structured)

    result = transform_service.build_reading_presentation_transform(
        azure_client=object(), model="gpt-test", situation_text="局面説明",
        question_text="問い", reading_cards=[_sample_card()])

    assert result == [None]
    assert "提供されていません" in captured["user_prompt"]


# ---- Test C: system promptがActor-firstの階層・禁止ルールを明示していること ----

def test_system_prompt_enforces_actor_first_hierarchy_and_bans_event_first_inference():
    prompt = transform_service._SYSTEM_PROMPT
    assert "Enduring orientation" in prompt
    assert "persistent_preference" in prompt
    assert "perceived_path" in prompt
    assert "event_role" in prompt
    assert "出来事主語" in prompt


# ---- Test D: cache fingerprintがpromptバージョンに連動し、旧cacheを無効化すること ----

def test_presentation_transform_fingerprint_changes_when_prompt_version_changes(monkeypatch):
    card = _sample_card()
    fp_before = session_service._presentation_transform_fingerprint(card)
    monkeypatch.setattr(transform_service, "_TRANSFORM_PROMPT_VERSION", "v3_future_change")
    fp_after = session_service._presentation_transform_fingerprint(card)
    assert fp_before != fp_after


# ---- Test E: get_or_build_reading_presentationがresponse_json由来のkey_actor_profilesを
#      build_reading_presentation_transformへそのまま橋渡しすること ----

def test_get_or_build_reading_presentation_threads_key_actor_profiles_through(monkeypatch):
    client = FakeSupabaseClient()
    card = _sample_card()
    captured = {}

    def fake_build(azure_client, model, *, situation_text, question_text, reading_cards,
                    key_actor_profiles=None):
        captured["key_actor_profiles"] = key_actor_profiles
        return [None]

    monkeypatch.setattr(transform_service, "build_reading_presentation_transform", fake_build)
    monkeypatch.setattr(session_service, "reading_presentation_transform_service", transform_service)

    session_service.get_or_build_reading_presentation(
        client, azure_client=object(), model="gpt-test", analysis_session_id="sess-1",
        reading_exposure_cards=[card], situation_text="局面", question_text="問い",
        actor_id="analyst-1", key_actor_profiles=[_sample_actor_profile()])

    assert captured["key_actor_profiles"] == [_sample_actor_profile()]


# ---- Test F: EVENT_ONLY_READINGは表示をブロックせず、非blockingでaudit logのみ行うこと ----

def test_event_only_reading_is_logged_but_not_blocked(monkeypatch):
    client = FakeSupabaseClient()
    card = _sample_card()

    def fake_build(azure_client, model, *, situation_text, question_text, reading_cards,
                    key_actor_profiles=None):
        return [{
            "index": 0, "title": "Event-onlyな読み", "reading_role": card["reading_role"],
            "actor_dynamics_check": {
                "actor_is_grammatical_subject": False,
                "states_actor_objective_or_orientation": False,
                "states_counterparty_or_constraint": False,
                "is_event_only": True,
            },
        }]

    monkeypatch.setattr(transform_service, "build_reading_presentation_transform", fake_build)
    monkeypatch.setattr(session_service, "reading_presentation_transform_service", transform_service)

    result = session_service.get_or_build_reading_presentation(
        client, azure_client=object(), model="gpt-test", analysis_session_id="sess-1",
        reading_exposure_cards=[card], situation_text="局面", question_text="問い",
        actor_id="analyst-1")

    # 表示は壊れない(結果がそのまま返る)
    assert result[card["scenario_candidate_id"]]["title"] == "Event-onlyな読み"
    # ただし非blockingでaudit logへ記録される
    audit_rows = client.tables.get("geo_audit_logs", [])
    assert any(r.get("action") == "reading_presentation_event_only_detected" for r in audit_rows)


def test_non_event_only_reading_is_not_logged(monkeypatch):
    client = FakeSupabaseClient()
    card = _sample_card()

    def fake_build(azure_client, model, *, situation_text, question_text, reading_cards,
                    key_actor_profiles=None):
        return [{
            "index": 0, "title": "Actor-firstな読み", "reading_role": card["reading_role"],
            "actor_dynamics_check": {
                "actor_is_grammatical_subject": True,
                "states_actor_objective_or_orientation": True,
                "states_counterparty_or_constraint": True,
                "is_event_only": False,
            },
        }]

    monkeypatch.setattr(transform_service, "build_reading_presentation_transform", fake_build)
    monkeypatch.setattr(session_service, "reading_presentation_transform_service", transform_service)

    session_service.get_or_build_reading_presentation(
        client, azure_client=object(), model="gpt-test", analysis_session_id="sess-1",
        reading_exposure_cards=[card], situation_text="局面", question_text="問い",
        actor_id="analyst-1")

    audit_rows = client.tables.get("geo_audit_logs", [])
    assert not any(r.get("action") == "reading_presentation_event_only_detected" for r in audit_rows)


def test_get_or_build_reading_presentation_defaults_key_actor_profiles_to_none(monkeypatch):
    """key_actor_profiles省略時（既存呼び出し元・REVIEW MODE）は明示的にNoneのまま渡り、
    Actor Intelligence Coreへの新規依存を一切増やさない。"""
    client = FakeSupabaseClient()
    card = _sample_card()
    captured = {}

    def fake_build(azure_client, model, *, situation_text, question_text, reading_cards,
                    key_actor_profiles=None):
        captured["key_actor_profiles"] = key_actor_profiles
        return [None]

    monkeypatch.setattr(transform_service, "build_reading_presentation_transform", fake_build)
    monkeypatch.setattr(session_service, "reading_presentation_transform_service", transform_service)

    session_service.get_or_build_reading_presentation(
        client, azure_client=object(), model="gpt-test", analysis_session_id="sess-1",
        reading_exposure_cards=[card], situation_text="局面", question_text="問い",
        actor_id="analyst-1")

    assert captured["key_actor_profiles"] is None
