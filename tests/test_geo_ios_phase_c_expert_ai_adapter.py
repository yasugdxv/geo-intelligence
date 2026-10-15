"""Geopolitical Intelligence OS Phase C: Expert AI → Analysis Session → CQAS → Report Design →
Report Studio Handoffの実接続テスト。

geo_expert_engine.run_expert_request_with_mode（既存Expert AIの唯一のPublic Entry Point）は
monkeypatchで差し替え、実Azure呼び出しは行わない——Adapter/Service側のロジックのみを検証する。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_expert_engine  # noqa: E402
import geo_ios_analysis_session_service as session_service  # noqa: E402
import geo_ios_expert_ai_adapter as adapter  # noqa: E402
import geo_ios_reading_portfolio_service as reading_portfolio_service  # noqa: E402
import geo_llm_common  # noqa: E402
import geo_report_studio_service as report_studio_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _fake_engine_response(client, *, request_id: str, response_id: str, response_json: dict,
                          model: str = "gpt-test", prompt_version: str = "geo-expert-v1.2",
                          knowledge_version: str = "kb-2026-09-16"):
    """run_expert_request_with_modeの戻り値・副作用（geo_expert_responsesへの永続化）を模す。"""
    client.tables.setdefault("geo_expert_responses", []).append({
        "response_id": response_id, "request_id": request_id, "response_json": response_json,
        "model": model, "prompt_version": prompt_version, "knowledge_version": knowledge_version,
    })
    return {
        "request": {"request_id": request_id},
        "response": {
            "response_id": response_id, "response_json": response_json, "model": model,
            "prompt_version": prompt_version, "knowledge_version": knowledge_version,
        },
        "quality_evaluation": {"checks": [], "passed_count": 0, "failed_count": 0},
        "execution_metadata": {},
    }


def _sample_response_json(n_alternatives: int = 2) -> dict:
    return {
        "main_scenario": {
            "description": "米政権は議会と歩調を合わせつつ規制を段階的に強化する",
            "confidence": "中", "selection_rationale": "現時点の議会動向から最も蓋然性が高いため",
            "supporting_evidence": ["商務省規則改正案", "上院公聴会記録"],
            "assumptions": ["中間選挙前に大きな路線転換はない"],
        },
        "alternative_scenarios": [
            {"description": f"代替シナリオ{i}: 規制強化が加速する", "confidence": "低",
             "switch_conditions": [f"条件{i}A", f"条件{i}B"]}
            for i in range(1, n_alternatives + 1)
        ],
        "stakeholders": [
            {"actor": "White House", "purpose": "対中優位の維持"},
            {"actor": "Senate Commerce Committee", "purpose": "国内産業保護"},
        ],
        "hypothesis_register": [
            {"hypothesis_id": "H1", "hypothesis_type": "main",
             "description": "議会は輸出規制の主導権を強めつつある", "confidence": "中"},
            {"hypothesis_id": "H2", "hypothesis_type": "alternative",
             "description": "政権主導のまま変化は小さい", "confidence": "低"},
        ],
        "identified_knowledge_gaps": [
            {"description": "NVIDIA以外の主要企業のロビー動向が未確認", "gap_category": "actor",
             "priority": "medium", "recommended_action": "追加調査"},
        ],
        "monitoring_indicators": ["商務省の新規則公表", "上院公聴会の開催"],
    }


def test_generate_scenario_candidates_retains_all_and_ranks_main_first(monkeypatch):
    client = FakeSupabaseClient()
    response_json = _sample_response_json(n_alternatives=2)
    monkeypatch.setattr(geo_expert_engine, "run_expert_request_with_mode",
                         lambda *a, **kw: _fake_engine_response(
                             client, request_id="req-1", response_id="resp-1", response_json=response_json))

    result = adapter.generate_scenario_candidates(
        client, azure_client=object(), model="gpt-test", theme="米国の対中半導体・AI輸出規制の今後",
        actor_id="analyst-1")

    assert len(result["candidates"]) == 3  # main 1件 + alternative 2件、全件保持
    assert result["candidates"][0]["content"]["origin"] == "main_scenario"
    assert result["candidates"][1]["content"]["origin"] == "alternative_scenario"
    # 2026-09-25変更、ユーザー指示: Reading生成はcase_analysis/deepを既定にし、
    # source_cqasへmode/profile/operationを記録するようになった。
    assert result["candidates"][0]["content"]["source_cqas"] == {
        "request_id": "req-1", "response_id": "resp-1",
        "mode": "case_analysis", "profile": "deep", "operation": "reading_generation",
        "actor_intelligence_enabled": True,
    }
    assert result["candidates"][0]["content"]["key_actors"] == ["White House", "Senate Commerce Committee"]
    assert result["model"] == "gpt-test" and result["cqas_knowledge_version"] == "kb-2026-09-16"


# ---- Reading生成専用mode/profile（2026-09-25追加、ユーザー指示） ---------------
#
# Step1画面のonHandoffもStep2の再生成ボタンも、バックエンドではgenerate_scenario_candidates
# という同一関数を呼ぶ（「呼び出し元の画面」ではなく「何を生成する処理か」で判定する、という
# ユーザー指示のとおり）。ここでは関数単体でmode/profileの既定値・override優先順位・他の
# 5種類の操作（Consultation/RefineDesign/Draft生成・編集・レビュー）が共有DEFAULT_MODE/
# DEFAULT_PROFILEのまま影響を受けないことを確認する。

def _capture_call_kwargs(captured: dict, client, *, request_id="req-cap", response_id="resp-cap",
                          response_json=None):
    """run_expert_request_with_modeへ実際に渡されたmode/profileを捕捉するfake。"""
    response_json = response_json or _sample_response_json(n_alternatives=1)

    def _fake(client_, azure_client, model, mode, theme_id, query_text, draft_text, *,
              profile=None, execution_mode=None, actor_intelligence_enabled=None):
        captured["mode"] = mode
        captured["profile"] = profile
        captured["theme_id"] = theme_id
        captured["actor_intelligence_enabled"] = actor_intelligence_enabled
        return _fake_engine_response(
            client, request_id=request_id, response_id=response_id, response_json=response_json)
    return _fake


def test_generate_scenario_candidates_defaults_to_case_analysis_deep(monkeypatch):
    """onHandoff/Step2再生成のどちらから呼ばれても、呼び出し元がmode/profileを指定しない
    限りcase_analysis/deepが使われる（Frontier候補の材料であるhypothesis_registerが
    初回生成から有効になる、という今回の目的そのもの）。"""
    client = FakeSupabaseClient()
    captured = {}
    monkeypatch.setattr(geo_expert_engine, "run_expert_request_with_mode",
                         _capture_call_kwargs(captured, client))

    adapter.generate_scenario_candidates(
        client, azure_client=object(), model="gpt-test", theme="テーマ", actor_id="analyst-1")

    assert captured["mode"] == "case_analysis"
    assert captured["profile"] == "deep"
    # Actor-first化 Step2（2026-09-25、ユーザー指示Option 3）: Reading生成はoperation-scoped
    # overrideでactor_intelligence_enabled=Trueを常に明示する（global env flagは変更しない）。
    assert captured["actor_intelligence_enabled"] is True


def test_generate_scenario_candidates_explicit_override_still_wins(monkeypatch):
    """診断・評価用途（Blind Retrospective Diagnostic等）が明示的にmode/profileを渡す
    既存の呼び方は、新しい既定値に上書きされず引き続き尊重される。"""
    client = FakeSupabaseClient()
    captured = {}
    monkeypatch.setattr(geo_expert_engine, "run_expert_request_with_mode",
                         _capture_call_kwargs(captured, client))

    adapter.generate_scenario_candidates(
        client, azure_client=object(), model="gpt-test", theme="テーマ", actor_id="analyst-1",
        mode="research_design", profile="standard")

    assert captured["mode"] == "research_design"
    assert captured["profile"] == "standard"


def test_resolve_mode_shared_default_unchanged():
    """他の5種類の操作（Consultation/RefineDesign/Draft生成・編集・レビュー）が使う
    共有DEFAULT_MODEは、Reading生成専用defaultの導入後も"research_design"のまま。"""
    assert adapter._resolve_mode(None) == "research_design"
    assert adapter.DEFAULT_MODE == "research_design"
    assert adapter.DEFAULT_PROFILE == "standard"


def test_resolve_mode_reading_generation_default_is_explicit_opt_in():
    """READING_GENERATION_MODEはgenerate_scenario_candidates専用のdefaultとして明示的に
    渡した場合のみ適用される（_resolve_mode単体の既定動作は変えない）。"""
    assert adapter.READING_GENERATION_MODE == "case_analysis"
    assert adapter.READING_GENERATION_PROFILE == "deep"
    assert adapter._resolve_mode(None, default=adapter.READING_GENERATION_MODE) == "case_analysis"


def test_refine_design_unaffected_by_reading_generation_default(monkeypatch):
    """Design Refine（相談チャットでのDesign改訂）は、Reading生成専用defaultの影響を受けず、
    共有DEFAULT_MODE/DEFAULT_PROFILEのまま呼ばれる。"""
    client = FakeSupabaseClient()
    captured = {}
    monkeypatch.setattr(geo_expert_engine, "run_expert_request_with_mode",
                         _capture_call_kwargs(captured, client))

    current_design = {
        "investigation_question": "既存の問い", "scope": None, "key_actors": [], "analytical_lenses": [],
        "hypotheses": [], "alternative_hypotheses": [], "evidence_needed": [], "known_evidence": [],
        "knowledge_gaps": [], "watch_points": [], "report_structure": {}, "confidence": None,
        "assumptions": [],
    }
    adapter.refine_design(
        client, azure_client=object(), model="gpt-test", current_design=current_design,
        user_message="Key Actorを追加して", theme="テーマ", actor_id="analyst-1")

    assert captured["mode"] == "research_design"
    assert captured["profile"] == "standard"
    # Actor-first化 Step2: RefineDesignはactor_intelligence_enabled overrideを渡さない
    # （Noneのまま、geo_expert_engine側のglobal env flagに委ねる既存挙動を維持）。
    assert captured["actor_intelligence_enabled"] is None


def _fake_portfolio_llm_result(readings: list) -> dict:
    return {"data": {"readings": readings}, "mode": "structured_output",
            "usage": {"prompt_tokens": 100, "completion_tokens": 50}}


def test_generate_scenario_candidates_appends_reading_portfolio_readings(monkeypatch):
    """改善②（ユーザー指示、2026-09-24、Step2品質検証結果を受けた対応）: CQAS本体
    （main_scenario/alternative_scenarios）はそのまま保持したうえで、Reading Portfolio
    （別の因果構造/前提反転/Wildcard）が追加のcandidateとして正しく合流することを確認する。"""
    client = FakeSupabaseClient()
    response_json = _sample_response_json(n_alternatives=1)
    monkeypatch.setattr(geo_expert_engine, "run_expert_request_with_mode",
                         lambda *a, **kw: _fake_engine_response(
                             client, request_id="req-1", response_id="resp-1", response_json=response_json))
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", lambda *a, **kw: _fake_portfolio_llm_result([
        {"slot": "alternative_causal_structure", "insufficient_material": False,
         "title": "別の因果構造の読み", "reading": "実は議会ではなく政権主導が背景",
         "key_cue": "商務省規則改正案の起草元", "key_actors_and_structure": "政権中枢",
         "supporting_reasons": ["起草プロセスの主体"], "weakening_information": ["議会証言の頻度"],
         "switch_condition": "起草者の所属が判明する"},
        {"slot": "premise_reversal", "insufficient_material": False,
         "title": "前提反転の読み", "reading": "議会の強硬姿勢自体が過大評価されている可能性",
         "key_cue": "公聴会の出席率の低さ", "key_actors_and_structure": "議会内の温度差",
         "supporting_reasons": ["出席率データ"], "weakening_information": ["主要議員の発言頻度"],
         "switch_condition": "主要法案の採決結果"},
        {"slot": "wildcard_non_obvious", "insufficient_material": False,
         "title": "Wildcardの読み", "reading": "第三国の動きが局面を一変させる可能性",
         "key_cue": "同盟国の輸出管理動向", "key_actors_and_structure": "同盟国政府",
         "supporting_reasons": ["同盟国の規制検討報道"], "weakening_information": ["公式否定"],
         "switch_condition": "同盟国が独自規制を発表する"},
    ]))

    result = adapter.generate_scenario_candidates(
        client, azure_client=object(), model="gpt-test", theme="米国の対中半導体・AI輸出規制の今後",
        actor_id="analyst-1")

    assert len(result["candidates"]) == 5  # main 1 + alternative 1 + portfolio 3
    origins = [c["content"]["origin"] for c in result["candidates"]]
    assert origins == [
        "main_scenario", "alternative_scenario",
        "reading_portfolio_alternative_causal_structure",
        "reading_portfolio_premise_reversal",
        "reading_portfolio_wildcard_non_obvious",
    ]
    premise_candidate = result["candidates"][3]
    assert premise_candidate["title"] == "前提反転の読み"
    assert "着眼点: 公聴会の出席率の低さ" in premise_candidate["content"]["why_this_matters"]
    assert "切替条件: 主要法案の採決結果" in premise_candidate["content"]["why_this_matters"]
    assert premise_candidate["content"]["main_lens"] == "議会内の温度差"


def test_generate_scenario_candidates_skips_insufficient_material_readings(monkeypatch):
    """材料不足の枠は捏造せずスキップする（ユーザー指示）——空のcandidateを作らない。"""
    client = FakeSupabaseClient()
    response_json = _sample_response_json(n_alternatives=1)
    monkeypatch.setattr(geo_expert_engine, "run_expert_request_with_mode",
                         lambda *a, **kw: _fake_engine_response(
                             client, request_id="req-1", response_id="resp-1", response_json=response_json))
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", lambda *a, **kw: _fake_portfolio_llm_result([
        {"slot": "alternative_causal_structure", "insufficient_material": False,
         "title": "別の因果構造の読み", "reading": "r", "key_cue": "c", "key_actors_and_structure": "a",
         "supporting_reasons": [], "weakening_information": [], "switch_condition": "s"},
        {"slot": "premise_reversal", "insufficient_material": True,
         "insufficient_material_reason": "反転を裏付ける材料が資料に無い"},
        {"slot": "wildcard_non_obvious", "insufficient_material": True,
         "insufficient_material_reason": "Wildcardを支持する具体的な兆候が無い"},
    ]))

    result = adapter.generate_scenario_candidates(
        client, azure_client=object(), model="gpt-test", theme="theme", actor_id="analyst-1")

    assert len(result["candidates"]) == 3  # main 1 + alternative 1 + portfolio 1（2件は材料不足でスキップ）
    assert result["candidates"][2]["content"]["origin"] == "reading_portfolio_alternative_causal_structure"


def test_generate_scenario_candidates_include_reading_portfolio_false_skips_layer(monkeypatch):
    client = FakeSupabaseClient()
    response_json = _sample_response_json(n_alternatives=2)
    monkeypatch.setattr(geo_expert_engine, "run_expert_request_with_mode",
                         lambda *a, **kw: _fake_engine_response(
                             client, request_id="req-1", response_id="resp-1", response_json=response_json))

    def _should_not_be_called(*a, **kw):
        raise AssertionError("include_reading_portfolio=Falseの場合、呼ばれてはいけない")

    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _should_not_be_called)

    result = adapter.generate_scenario_candidates(
        client, azure_client=object(), model="gpt-test", theme="theme", actor_id="analyst-1",
        include_reading_portfolio=False)

    assert len(result["candidates"]) == 3  # main 1 + alternative 2、Portfolio層は完全にスキップ


def test_reading_portfolio_failure_does_not_lose_cqas_candidates(monkeypatch):
    """Reading Portfolio呼び出しが失敗しても、既にCQASから得られた候補は失わない
    （§19 Failure Handlingと同じ「壊れても既存の成果を失わない」方針）。"""
    client = FakeSupabaseClient()
    response_json = _sample_response_json(n_alternatives=1)
    monkeypatch.setattr(geo_expert_engine, "run_expert_request_with_mode",
                         lambda *a, **kw: _fake_engine_response(
                             client, request_id="req-1", response_id="resp-1", response_json=response_json))

    def _boom(*a, **kw):
        raise RuntimeError("portfolio azure timeout")

    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _boom)

    result = adapter.generate_scenario_candidates(
        client, azure_client=object(), model="gpt-test", theme="theme", actor_id="analyst-1")

    assert len(result["candidates"]) == 2  # main 1 + alternative 1（Portfolio失敗分は無し）
    failures = [row for row in client.tables.get("geo_audit_logs", [])
                if row["action"] == "ios_reading_portfolio_failure"]
    assert len(failures) == 1


def test_generate_additional_readings_filters_insufficient_material_directly(monkeypatch):
    """geo_ios_reading_portfolio_service.generate_additional_readings自体の単体テスト。"""
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", lambda *a, **kw: _fake_portfolio_llm_result([
        {"slot": "alternative_causal_structure", "insufficient_material": False, "title": "t1", "reading": "r1",
         "key_cue": "c1", "key_actors_and_structure": "a1", "supporting_reasons": [],
         "weakening_information": [], "switch_condition": "s1"},
        {"slot": "premise_reversal", "insufficient_material": True,
         "insufficient_material_reason": "材料不足"},
        {"slot": "wildcard_non_obvious", "insufficient_material": False, "title": "t3", "reading": "r3",
         "key_cue": "c3", "key_actors_and_structure": "a3", "supporting_reasons": [],
         "weakening_information": [], "switch_condition": "s3"},
    ]))
    readings = reading_portfolio_service.generate_additional_readings(
        object(), "gpt-test", context_and_materials_text="context",
        main_scenario={"description": "main", "selection_rationale": "r"}, alternative_scenarios=[])
    assert [r["slot"] for r in readings] == ["alternative_causal_structure", "wildcard_non_obvious"]


def test_generate_scenarios_failure_leaves_session_and_candidates_untouched(monkeypatch):
    client = FakeSupabaseClient()
    session = session_service.create_session(client, title="t", user_id="analyst-1", theme="theme")

    def _boom(*a, **kw):
        raise RuntimeError("azure timeout")

    monkeypatch.setattr(geo_expert_engine, "run_expert_request_with_mode", _boom)

    with pytest.raises(adapter.ExpertAIAdapterError):
        adapter.generate_scenario_candidates(
            client, azure_client=object(), model="gpt-test", theme="theme", actor_id="analyst-1")

    # Sessionはそのまま残り、candidateは一切書き込まれない
    reloaded = session_service.get_session(client, session["analysis_session_id"])
    assert reloaded["status"] == "active"
    assert session_service.list_scenario_candidates(client, session["analysis_session_id"]) == []
    # failure audit
    failures = [row for row in client.tables.get("geo_audit_logs", [])
                if row["action"] == "ios_expert_ai_adapter_failure"]
    assert len(failures) == 1


def test_full_e2e_theme_to_report_studio_handoff_with_provenance(monkeypatch):
    client = FakeSupabaseClient()

    # ---- Theme Input → Analysis Session ----
    session = session_service.create_session(
        client, title="米中半導体規制の分析", user_id="analyst-1", theme="米国の対中半導体・AI輸出規制の今後")
    sid = session["analysis_session_id"]

    # ---- Expert AI → CQAS / Scenario Candidates（7件生成、Top5のみ表示） ----
    response_json_1 = _sample_response_json(n_alternatives=6)  # main 1 + alt 6 = 7候補
    monkeypatch.setattr(geo_expert_engine, "run_expert_request_with_mode",
                         lambda *a, **kw: _fake_engine_response(
                             client, request_id="req-1", response_id="resp-1", response_json=response_json_1))
    generated = adapter.generate_scenario_candidates(
        client, azure_client=object(), model="gpt-test", theme=session["theme"], actor_id="analyst-1")
    created = session_service.record_scenario_candidates(
        client, sid, candidates=generated["candidates"], display_top_n=5, model=generated["model"],
        prompt_version=generated["prompt_version"], cqas_knowledge_version=generated["cqas_knowledge_version"],
        actor_id="analyst-1")
    assert len(created) == 7
    displayed = session_service.list_scenario_candidates(client, sid, displayed_only=True)
    assert len(displayed) == 5  # Top5のみ表示、全件はDBに保持済み

    # ---- Scenario Selection ----
    selected = session_service.select_scenario_candidate(client, created[0]["scenario_candidate_id"],
                                                          actor_id="analyst-1")
    assert selected["selected"] is True
    session_after_select = session_service.get_session(client, sid)
    assert session_after_select["selected_scenario_id"] == created[0]["scenario_candidate_id"]

    # ---- Report Design生成（新規LLM呼び出し無し、保存済みresp-1の再取得のみ） ----
    # READING_EXPOSURE_LAYER_V1配線（2026-09-24）に伴うbuild_design_from_candidate修正:
    # 選択されたcandidate自身のcontent.rawからhypothesesを組み立てるようになったため
    # （修正前はどのcandidateを選んでもresponse_json["main_scenario"]しか見ないバグが
    # あった）、ここではcreated[0]（main_scenario）自身のdescriptionが期待値になる
    # （hypothesis_registerの"main"タイプ由来の値ではない）。
    design_v1 = expert_ai_adapter_generate_design(client, sid)
    assert design_v1["version"] == 1
    assert "White House" in design_v1["key_actors"]
    assert design_v1["hypotheses"] == ["米政権は議会と歩調を合わせつつ規制を段階的に強化する"]

    # ---- Chat-based Refinement（v1 → v2、v1は不変） ----
    response_json_2 = dict(response_json_1)
    response_json_2["stakeholders"] = response_json_1["stakeholders"] + [
        {"actor": "NVIDIA", "purpose": "対中売上の確保"}]
    monkeypatch.setattr(geo_expert_engine, "run_expert_request_with_mode",
                         lambda *a, **kw: _fake_engine_response(
                             client, request_id="req-2", response_id="resp-2", response_json=response_json_2))

    current_design = session_service.get_current_design(client, sid)
    refined = adapter.refine_design(
        client, azure_client=object(), model="gpt-test", current_design=current_design,
        user_message="NVIDIAをKey Actorに追加して", theme=session["theme"], actor_id="analyst-1")
    design_v2 = session_service.create_design_version(
        client, sid, fields=refined["updated_fields"],
        change_note="Chat refinement: NVIDIAをKey Actorに追加して", created_by="analyst-1")
    assert design_v2["version"] == 2
    assert "NVIDIA" in design_v2["key_actors"]
    assert "changed_fields" in refined["change_summary"]

    history = session_service.list_design_history(client, sid)
    assert [d["version"] for d in history] == [1, 2]
    assert "NVIDIA" not in history[0]["key_actors"]  # v1は上書きされていない

    # ---- Create Report → 既存Report Studio Project作成 ----
    project = report_studio_service.get_or_create_project(client, f"ios-{sid[:8]}", design_v2["investigation_question"])
    updated_session = session_service.link_report_project(
        client, sid, report_project_id=project["project_id"], actor_id="analyst-1")
    assert updated_session["status"] == "handed_off"
    assert updated_session["linked_report_project_id"] == project["project_id"]

    # ---- Provenance: Report Project → Session → Selected Scenario → CQAS Run → All Candidates → Design History ----
    traced_session = session_service.get_session_by_report_project(client, project["project_id"])
    assert traced_session["analysis_session_id"] == sid
    traced_selected_id = traced_session["selected_scenario_id"]
    all_candidates = session_service.list_scenario_candidates(client, sid)
    traced_candidate = next(c for c in all_candidates if c["scenario_candidate_id"] == traced_selected_id)
    assert traced_candidate["content"]["source_cqas"]["response_id"] == "resp-1"
    assert len(all_candidates) == 7  # Handoff後も全候補が保持されたまま
    assert len(session_service.list_design_history(client, sid)) == 2

    # ---- Re-selection after handoff requires explicit confirmation ----
    with pytest.raises(session_service.AnalysisSessionError):
        session_service.select_scenario_candidate(client, created[1]["scenario_candidate_id"], actor_id="analyst-1")
    reconfirmed = session_service.select_scenario_candidate(
        client, created[1]["scenario_candidate_id"], actor_id="analyst-1", confirm_after_handoff=True)
    assert reconfirmed["selected"] is True
    assert session_service.get_session(client, sid)["status"] == "handed_off"  # statusは巻き戻らない


# ---- _short_title（2026-09-23追加、ユーザー指摘「タイトルは短くしよう」） -----------

def test_short_title_cuts_at_first_sentence_boundary_within_budget():
    description = "短期的には合意なき膠着が続く。このシナリオでは双方が全面戦争を避ける。"
    assert adapter._short_title(description) == "短期的には合意なき膠着が続く"


def test_short_title_falls_back_to_comma_when_no_early_period():
    description = "短期的には合意なき膠着が続き、ホルムズ海峡の航行は完全封鎖ではないものの断続的な妨害が続くシナリオを主軸とする。"
    assert adapter._short_title(description) == "短期的には合意なき膠着が続き"


def test_short_title_hard_truncates_with_ellipsis_when_no_boundary_within_budget():
    description = "あ" * 60  # 句読点が無い60文字
    result = adapter._short_title(description)
    assert result == "あ" * 45 + "…"


def test_short_title_returns_short_description_unchanged():
    assert adapter._short_title("短い説明") == "短い説明"


def test_short_title_returns_empty_string_for_empty_description():
    assert adapter._short_title("") == ""


def expert_ai_adapter_generate_design(client, analysis_session_id: str) -> dict:
    """テスト内ヘルパー: API層のgenerate-designエンドポイントと同じ手順をService/Adapter直接呼び出しで再現する。"""
    session = session_service.get_session(client, analysis_session_id)
    candidates = session_service.list_scenario_candidates(client, analysis_session_id)
    selected = next(c for c in candidates if c["scenario_candidate_id"] == session["selected_scenario_id"])
    fields = adapter.build_design_from_candidate(client, theme=session["theme"], scenario_candidate=selected)
    return session_service.create_design_version(
        client, analysis_session_id, fields=fields, change_note="初版生成", created_by="analyst-1")
