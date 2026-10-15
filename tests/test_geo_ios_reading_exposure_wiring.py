"""READING_EXPOSURE_LAYER_V1のStep2 API/UI配線（2026-09-24）の横断テスト。

Freeze済みgeo_ios_reading_exposure_composer.compose_reading_exposure()自体のselection
logicは既にtests/test_geo_ios_reading_exposure_composer.pyで検証済み（本テストでは
再検証しない）。ここでは、それを呼び出す配線側——(1) geo_ios_analysis_session_service.
get_or_build_reading_exposure（永続化・べき等性）、(2) geo_ios_situation_presentation_
service.build_reading_exposure（Step2表示への整形・内部status文字列の自然文翻訳）、
(3) geo_ios_expert_ai_adapter.build_design_from_candidate修正（選択されたcandidate
自身の内容がDraftへ反映されること）、(4) geo_ios_situation_presentation_apiのBackward
Compatibility fallback——のみを検証する。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_analysis_session_service as session_service  # noqa: E402
import geo_ios_expert_ai_adapter as expert_ai_adapter  # noqa: E402
import geo_ios_situation_presentation_api as presentation_api  # noqa: E402
import geo_ios_situation_presentation_service as presentation_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _response_json_with_frontier_and_portfolio_material():
    return {
        "one_line_assessment": "一言見立て",
        "key_point_assessment": {"primary_key_point": "主要な蝶番", "selection_rationale": "根拠",
                                  "hinge_structure": {"primary_hinge": "変数A"}},
        "main_scenario": {
            "description": "主シナリオの説明はここに書かれる長い文章です", "confidence": "中",
            "selection_rationale": "理由", "supporting_evidence": ["証拠A"],
            "assumptions": ["前提1"],
        },
        "weakening_information": None, "contradicting_evidence": [],
        "retraction_conditions": ["撤回条件1"], "key_actor_profiles": [],
        "monitoring_indicators": ["監視指標1"], "identified_knowledge_gaps": [], "additional_research": [],
        "hypothesis_register": [
            {"hypothesis_id": "H4-1", "hypothesis_type": "H4_frame_breaking", "status": "rejected",
             "description": "実はそもそもの見立ての枠組み自体が違うのではないかという長い仮説の説明文をここに書く",
             "reason": "既存の前提が崩れている可能性を示す十分に長い理由の説明文をここに書く",
             "supporting_evidence_ids": ["ev-1"]},
        ],
        "claims": [{"source_ref": "ev-1", "text": "根拠claim本文"}],
    }


def _seed_session_with_frontier_material(client):
    session = session_service.create_session(
        client, title="テスト案件", user_id="analyst-1", theme="今回何を調べるべきか")
    response_json = _response_json_with_frontier_and_portfolio_material()
    client.tables.setdefault("geo_expert_responses", []).append({
        "response_id": "resp-1", "response_json": response_json,
    })
    candidates = session_service.record_scenario_candidates(
        client, session["analysis_session_id"],
        candidates=[
            {"title": "主シナリオ", "content": {
                "origin": "main_scenario", "why_this_matters": "理由", "key_actors": ["Actor X"],
                "source_cqas": {"response_id": "resp-1"}, "raw": response_json["main_scenario"],
            }},
            {"title": "Wildcardの読み", "content": {
                "origin": "reading_portfolio_wildcard_non_obvious", "why_this_matters": "着眼点: 別の兆候",
                "source_cqas": {"response_id": "resp-1"},
                "raw": {"title": "Wildcardの読み", "reading": "第三国の動きが局面を一変させる可能性",
                        "key_cue": "同盟国の動向", "supporting_reasons": ["同盟国報道"],
                        "weakening_information": ["公式否定"], "switch_condition": "同盟国が発表する"},
            }},
        ],
        actor_id="analyst-1")
    return session, response_json, candidates


# ---- geo_ios_analysis_session_service.get_or_build_reading_exposure --------------

def test_materializes_frontier_with_real_scenario_candidate_id():
    client = FakeSupabaseClient()
    session, _, _ = _seed_session_with_frontier_material(client)
    sid = session["analysis_session_id"]

    result = session_service.get_or_build_reading_exposure(client, sid, actor_id="analyst-1")

    roles = [r["reading_role"] for r in result]
    assert "MAIN" in roles
    assert "FRONTIER" in roles
    frontier = next(r for r in result if r["reading_role"] == "FRONTIER")
    assert frontier["scenario_candidate_id"] is not None
    persisted = session_service.list_scenario_candidates(client, sid)
    assert any(c["scenario_candidate_id"] == frontier["scenario_candidate_id"] for c in persisted)
    # Sの却下判定(rejected)は上書きしない——rejectedのままexploratory=Trueで返す
    assert frontier["original_hypothesis_status"] == "rejected"
    assert frontier["exploratory"] is True


def test_is_idempotent_no_duplicate_rows_on_second_call():
    client = FakeSupabaseClient()
    session, _, _ = _seed_session_with_frontier_material(client)
    sid = session["analysis_session_id"]

    first = session_service.get_or_build_reading_exposure(client, sid, actor_id="analyst-1")
    before_count = len(session_service.list_scenario_candidates(client, sid))
    second = session_service.get_or_build_reading_exposure(client, sid, actor_id="analyst-1")
    after_count = len(session_service.list_scenario_candidates(client, sid))

    assert before_count == after_count
    assert sorted(r["scenario_candidate_id"] for r in first) == sorted(
        r["scenario_candidate_id"] for r in second)


def test_top1_content_unchanged_from_original_candidate():
    client = FakeSupabaseClient()
    session, _, candidates = _seed_session_with_frontier_material(client)
    sid = session["analysis_session_id"]

    result = session_service.get_or_build_reading_exposure(client, sid, actor_id="analyst-1")
    main_reading = next(r for r in result if r["reading_role"] == "MAIN")
    original_main = next(c for c in candidates if c["content"]["origin"] == "main_scenario")
    assert main_reading["scenario_candidate_id"] == original_main["scenario_candidate_id"]
    assert main_reading["content"]["raw"]["description"] == original_main["content"]["raw"]["description"]


def test_returns_empty_list_when_no_candidates():
    client = FakeSupabaseClient()
    session = session_service.create_session(client, title="候補なし", user_id="analyst-1", theme="テーマ")
    result = session_service.get_or_build_reading_exposure(
        client, session["analysis_session_id"], actor_id="analyst-1")
    assert result == []


# ---- geo_ios_situation_presentation_service.build_reading_exposure ---------------

def test_translates_internal_status_string_to_natural_language():
    client = FakeSupabaseClient()
    session, response_json, _ = _seed_session_with_frontier_material(client)
    sid = session["analysis_session_id"]
    items = session_service.get_or_build_reading_exposure(client, sid, actor_id="analyst-1")

    cards = presentation_service.build_reading_exposure(
        reading_exposure_items=items, response_json=response_json)
    frontier_card = next(c for c in cards if c["reading_role"] == "FRONTIER")
    assert frontier_card["why_not_main"] is not None
    assert "rejected" not in frontier_card["why_not_main"]["value"]
    assert "採用しなかった" in frontier_card["why_not_main"]["value"]
    main_card = next(c for c in cards if c["reading_role"] == "MAIN")
    assert main_card["why_not_main"] is None


def test_build_reading_exposure_empty_when_no_items():
    assert presentation_service.build_reading_exposure(reading_exposure_items=[], response_json={}) == []


# ---- geo_ios_situation_presentation_api: wiring + Backward Compatibility ---------

def test_api_response_includes_reading_exposure_key(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(presentation_api, "_client", client)
    session, _, _ = _seed_session_with_frontier_material(client)

    result = presentation_api.get_situation_presentation(session["analysis_session_id"])
    assert "reading_exposure" in result["presentation"]
    roles = [r["reading_role"] for r in result["presentation"]["reading_exposure"]]
    assert "FRONTIER" in roles


def test_api_falls_back_to_interpretations_when_reading_exposure_build_fails(monkeypatch):
    """Backward Compatibility要件: Reading Exposure Composer配線が失敗しても、既存の
    interpretationsは今まで通り返し、失敗はaudit logへ記録する。"""
    client = FakeSupabaseClient()
    monkeypatch.setattr(presentation_api, "_client", client)
    session, _, _ = _seed_session_with_frontier_material(client)

    def _boom(*a, **kw):
        raise RuntimeError("reading exposure boom")

    monkeypatch.setattr(session_service, "get_or_build_reading_exposure", _boom)

    result = presentation_api.get_situation_presentation(session["analysis_session_id"])
    assert result["presentation"]["reading_exposure"] == []
    assert len(result["presentation"]["interpretations"]) == 2
    failures = [row for row in client.tables.get("geo_audit_logs", [])
                if row["action"] == "reading_exposure_build_failure"]
    assert len(failures) == 1


# ---- geo_ios_expert_ai_adapter.build_design_from_candidate（CRITICAL BUG修正）----

def test_build_design_from_candidate_uses_selected_frontier_content_not_main_scenario():
    """回帰テスト: 修正前はどのcandidateを選んでもresponse_json["main_scenario"]しか
    見ておらず、Frontier/Exploratoryを選んでもDraftはmain_scenario内容のままだった。
    修正後は選択されたcandidate自身の内容（status・weakening evidence等の文脈を含む）
    がDesignへ反映されることを確認する。"""
    client = FakeSupabaseClient()
    session, response_json, _ = _seed_session_with_frontier_material(client)
    sid = session["analysis_session_id"]

    items = session_service.get_or_build_reading_exposure(client, sid, actor_id="analyst-1")
    frontier = next(r for r in items if r["reading_role"] == "FRONTIER")
    frontier_candidate = next(
        c for c in session_service.list_scenario_candidates(client, sid)
        if c["scenario_candidate_id"] == frontier["scenario_candidate_id"])

    fields = expert_ai_adapter.build_design_from_candidate(
        client, theme=session["theme"], scenario_candidate=frontier_candidate)

    assert fields["hypotheses"][0] != response_json["main_scenario"]["description"]
    assert "そもそもの見立ての枠組み自体が違う" in fields["hypotheses"][0]
    # Sが本線として選ばなかった探索的な読みであることの文脈が、確定事実化されずassumptionsへ残る
    assert any("採用しなかった" in a for a in fields["assumptions"])


def test_build_design_from_candidate_main_scenario_unchanged_behavior():
    """MAIN選択時は、修正前と同じくmain_scenario自身の内容がDesignへ反映されること
    （regression: main_scenario選択の既存挙動を壊していないこと）。"""
    client = FakeSupabaseClient()
    session, response_json, candidates = _seed_session_with_frontier_material(client)

    main_candidate = next(c for c in candidates if c["content"]["origin"] == "main_scenario")
    fields = expert_ai_adapter.build_design_from_candidate(
        client, theme=session["theme"], scenario_candidate=main_candidate)

    assert fields["hypotheses"] == [response_json["main_scenario"]["description"]]
    assert fields["known_evidence"] == response_json["main_scenario"]["supporting_evidence"]
    assert fields["assumptions"] == response_json["main_scenario"]["assumptions"]


# ---- geo_ios_situation_presentation_api: response_id直接route（Gold Case評価用）----
# 2026-09-24発見の5件表示バグ: このrouteはIOS Scenario Candidateが無いケース向けの
# _synthesize_candidates_from_response()フォールバック（main_scenario/alternative_
# scenariosのみ合成）しか持っておらず、reading_exposureが常に空になっていた
# （Hormuz Tollで4件=main1+alternative3のみ表示され、Reading Portfolio/hypothesis_
# register由来のFRONTIER/EXPLORATORYが一切出なかった）。同じresponse_idを参照する
# 既存Sessionがあれば、その既存データ・既存機構を再利用するよう修正した。

def test_response_id_route_reuses_existing_session_reading_exposure(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(presentation_api, "_client", client)
    session, response_json, _ = _seed_session_with_frontier_material(client)
    candidates = session_service.list_scenario_candidates(client, session["analysis_session_id"])
    response_id = candidates[0]["content"]["source_cqas"]["response_id"]

    result = presentation_api.get_situation_presentation_by_response_id(response_id)

    re_list = result["presentation"]["reading_exposure"]
    roles = [r["reading_role"] for r in re_list]
    assert "FRONTIER" in roles
    assert "EXPLORATORY" in roles
    assert len(re_list) >= 3  # このfixtureはMAIN+FRONTIER+EXPLORATORYの最低3件を含む


def test_response_id_route_without_existing_session_falls_back_to_synthesize(monkeypatch):
    """response_idに紐づくIOS Sessionが存在しない場合は、従来通りsynthesizeフォールバック
    のまま（regressionなし）——reading_exposureは空、interpretationsはmain_scenario/
    alternative_scenariosのみから合成される。"""
    client = FakeSupabaseClient()
    monkeypatch.setattr(presentation_api, "_client", client)
    response_json = {
        "main_scenario": {"description": "主シナリオ", "confidence": "中"},
        "alternative_scenarios": [{"description": "代替1"}, {"description": "代替2"}],
        "hypothesis_register": [], "key_actor_profiles": [],
    }
    client.tables.setdefault("geo_expert_responses", []).append({
        "response_id": "resp-standalone", "response_json": response_json,
    })

    result = presentation_api.get_situation_presentation_by_response_id("resp-standalone")

    assert result["presentation"]["reading_exposure"] == []
    assert len(result["presentation"]["interpretations"]) == 3
