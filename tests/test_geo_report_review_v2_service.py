"""Report Review V2 Orchestration Service: Report Reading Extraction→Expert AI独自
Reading生成→比較、の一連の流れの実接続テスト。geo_expert_engine.
run_expert_request_with_mode / geo_llm_common.call_llm_structuredはmonkeypatchで
差し替え、実Azure呼び出しは行わない。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_expert_engine  # noqa: E402
import geo_ios_report_draft_service as draft_service  # noqa: E402
import geo_report_review_v2_service as review_v2_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _fake_engine_response(client, *, request_id: str, response_id: str, response_json: dict,
                          model: str = "gpt-test", prompt_version: str = "geo-expert-v1.2",
                          knowledge_version: str = "kb-2026-09-16"):
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


def _fake_extracted_report_reading(**overrides) -> dict:
    base = {
        "analysis_theme": "米中半導体規制", "current_situation": "規制強化が続いている。",
        "question": "規制はどこまで強化されるか。", "report_context": "2026年9月時点の分析。",
        "cue": None, "key_actors": ["US", "China"], "actor_objectives": [], "relationships": [],
        "assumptions": [], "report_primary_reading": "規制は今後も強化される方向にある。",
        "causal_logic": None, "direction_of_travel": None, "scenario": None,
        "switch_condition": None, "supporting_evidence": ["直近の規制発表"],
        "weakening_evidence": [], "missing_evidence": [], "missing_reading": None,
        "business_impact": None,
        "not_explicit_fields": ["cue", "causal_logic", "direction_of_travel", "scenario",
                                "switch_condition", "missing_reading", "business_impact"],
    }
    base.update(overrides)
    return base


def _sample_expert_reading_response_json() -> dict:
    return {
        "main_scenario": {
            "description": "規制は緩和される方向に転じる", "confidence": "中",
            "selection_rationale": "業界団体の働きかけが強まっているため",
            "supporting_evidence": ["業界団体の声明"], "assumptions": [],
        },
        "alternative_scenarios": [],
        "stakeholders": [{"actor": "US", "purpose": "技術優位の維持"}],
    }


def _setup_extraction_fake(monkeypatch, report_reading: dict):
    captured = {}

    def fake_call_llm_structured(azure_client, model, system_prompt, user_prompt, schema, schema_name, **kwargs):
        captured["schema_name"] = schema_name
        return {"data": report_reading}

    monkeypatch.setattr(review_v2_service.extraction_service.geo_llm_common,
                         "call_llm_structured", fake_call_llm_structured)
    return captured


def _start_and_execute(client, *, analysis_session_id: str, report_text: str, actor_id: str) -> dict:
    review_v2_service.start_run(
        client, analysis_session_id, report_draft_id="draft-1", created_by=actor_id)
    return review_v2_service.execute_run(
        client, azure_client=object(), model="gpt-test", analysis_session_id=analysis_session_id,
        report_text=report_text, actor_id=actor_id)


def test_run_report_reading_review_full_pipeline(monkeypatch):
    client = FakeSupabaseClient()
    report_reading = _fake_extracted_report_reading()
    _setup_extraction_fake(monkeypatch, report_reading)

    engine_calls = []

    def fake_run_expert_request_with_mode(client_, azure_, model_, mode, theme_id, query_text,
                                           draft_text, *, profile, execution_mode,
                                           actor_intelligence_enabled=None):
        engine_calls.append(mode)
        if mode == "case_analysis":
            return _fake_engine_response(
                client, request_id="req-expert", response_id="resp-expert",
                response_json=_sample_expert_reading_response_json())
        if mode == "draft_review" and '"report_text"' in draft_text:
            return _fake_engine_response(
                client, request_id="req-content", response_id="resp-content",
                response_json={"review_findings": [
                    {"severity": "Minor", "finding": "本文の表現が不明瞭"}]})
        if mode == "draft_review":
            return _fake_engine_response(
                client, request_id="req-cmp", response_id="resp-cmp",
                response_json={"review_findings": [
                    {"severity": "Minor", "finding": "Primary Readingが逆方向"}]})
        raise AssertionError(f"想定外のmode: {mode}")

    monkeypatch.setattr(geo_expert_engine, "run_expert_request_with_mode",
                         fake_run_expert_request_with_mode)

    result = _start_and_execute(
        client, analysis_session_id="sess-1", report_text="レポート本文", actor_id="reviewer-1")

    assert engine_calls == ["case_analysis", "draft_review", "draft_review"]
    assert result["status"] == "completed"
    assert result["report_reading"]["report_primary_reading"] == "規制は今後も強化される方向にある。"
    assert result["expert_reading"]["candidates"][0]["content"]["origin"] == "main_scenario"
    assert result["comparison_findings"][0]["finding"] == "Primary Readingが逆方向"
    assert result["content_findings"][0]["finding"] == "本文の表現が不明瞭"
    # Human Expert Review（Phase R2）が参照できるよう、finding_idが付与されている
    assert result["comparison_findings"][0]["finding_id"] == "f1"
    assert result["content_findings"][0]["finding_id"] == "f1"

    candidates = client.select(
        "geo_ios_scenario_candidates", {"select": "*", "analysis_session_id": "eq.sess-1"})
    roles = sorted(c["content"]["reading_role"] for c in candidates)
    assert roles == ["EXPERT_AI_INDEPENDENT_READING", "REPORT_OWN_READING"]

    # comparison_summary: report側はnot_explicit_fieldsにある項目をNoneとして扱う
    # （本文に明記なしをそのまま伝える、報告された項目を無理に埋めない）。expert_ai側の
    # cueは、Expert AIに実際に渡したtheme文字列（_build_theme_text）そのもの
    # （Expert AIに個別の「cue」フィールドは無いため、実際に使われた入力を代替値とする）。
    summary = result["comparison_summary"]
    assert summary["cue"]["report"] is None
    assert "規制強化が続いている。" in summary["cue"]["expert_ai"]
    assert summary["primary_reading"]["report"] == "規制は今後も強化される方向にある。"
    assert summary["primary_reading"]["expert_ai"] == "規制は緩和される方向に転じる"


def test_get_expert_reading_presentation_reuses_existing_presentation_builder(monkeypatch):
    """Expert AI Reading側は既存build_situation_presentationでそのままReading
    Presentation v2形状を構築できる（designは{}、review-modeセッションに存在しない）。
    Report Reading側はここでは対象外（Frontend専用軽量カード、ユーザー指示）。"""
    client = FakeSupabaseClient()
    report_reading = _fake_extracted_report_reading()
    _setup_extraction_fake(monkeypatch, report_reading)

    def fake_run_expert_request_with_mode(client_, azure_, model_, mode, theme_id, query_text,
                                           draft_text, *, profile, execution_mode,
                                           actor_intelligence_enabled=None):
        response_json = (_sample_expert_reading_response_json() if mode == "case_analysis"
                          else {"review_findings": []})
        return _fake_engine_response(
            client, request_id="req-pres", response_id="resp-pres", response_json=response_json)

    monkeypatch.setattr(geo_expert_engine, "run_expert_request_with_mode",
                         fake_run_expert_request_with_mode)

    review_v2_service.start_run(
        client, "sess-pres", report_draft_id="draft-pres", created_by="reviewer-1")
    review_v2_service.execute_run(
        client, azure_client=object(), model="gpt-test", analysis_session_id="sess-pres",
        report_text="レポート本文", actor_id="reviewer-1")

    presentation = review_v2_service.get_expert_reading_presentation(client, "sess-pres")
    assert presentation is not None
    assert "interpretations" in presentation and "actors" in presentation
    assert presentation["key_point"] is not None


def test_get_expert_reading_presentation_returns_none_before_completion():
    client = FakeSupabaseClient()
    assert review_v2_service.get_expert_reading_presentation(client, "sess-none") is None


def test_expert_evidence_rationales_prefers_readable_rationale_over_raw_ids():
    """main_scenario.supporting_evidenceはevidence_id（UUID）の配列のため、比較表には
    evidence_relevance[].rationale（人間可読な根拠説明文）を優先して使う。"""
    main_raw = {
        "supporting_evidence": ["dad5abe7-dee3-4363-a14e-2cc473329c16"],
        "evidence_relevance": [
            {"evidence_id": "dad5abe7-dee3-4363-a14e-2cc473329c16",
             "rationale": "MATCH法分析は構造的な妥当性を補強する。"},
        ],
    }
    result = review_v2_service._expert_evidence_rationales(main_raw)
    assert result == ["MATCH法分析は構造的な妥当性を補強する。"]


def test_expert_evidence_rationales_falls_back_to_raw_ids_when_no_rationale():
    main_raw = {"supporting_evidence": ["dad5abe7-dee3-4363-a14e-2cc473329c16"], "evidence_relevance": []}
    result = review_v2_service._expert_evidence_rationales(main_raw)
    assert result == ["dad5abe7-dee3-4363-a14e-2cc473329c16"]


def test_contains_lone_surrogate_detects_known_corruption_signature():
    assert review_v2_service._contains_lone_surrogate("clean text") is False
    assert review_v2_service._contains_lone_surrogate({"a": ["ok", "has\udc83bad"]}) is True
    assert review_v2_service._contains_lone_surrogate({"a": {"b": "has\udc83bad"}}) is True


def test_verify_and_retry_write_repairs_corrupted_row():
    """調査(2026-10-03)で確認した間欠的な文字化け（lone surrogate）への防御。
    書き込み直後の読み直しで破損を検知した場合、同じ内容で1回だけ上書きして
    修復することを確認する。"""
    client = FakeSupabaseClient()
    created = client.insert("geo_ios_scenario_candidates", [{
        "analysis_session_id": "sess-corrupt", "rank": 1, "title": "t",
        "content": {"why_this_matters": "破損した文字\udc83列"},
        "displayed": True, "selected": False,
    }])[0]

    repaired = review_v2_service._verify_and_retry_write(
        client, "geo_ios_scenario_candidates", "scenario_candidate_id",
        created["scenario_candidate_id"], {"content": {"why_this_matters": "正常な文字列"}},
        actor_id="reviewer-1")

    assert repaired["content"]["why_this_matters"] == "正常な文字列"
    assert review_v2_service._contains_lone_surrogate(repaired) is False


def test_verify_and_retry_write_leaves_clean_row_untouched():
    client = FakeSupabaseClient()
    created = client.insert("geo_ios_scenario_candidates", [{
        "analysis_session_id": "sess-clean", "rank": 1, "title": "t",
        "content": {"why_this_matters": "問題ない文字列"},
        "displayed": True, "selected": False,
    }])[0]

    result = review_v2_service._verify_and_retry_write(
        client, "geo_ios_scenario_candidates", "scenario_candidate_id",
        created["scenario_candidate_id"], {"content": {"why_this_matters": "別の値になっていないこと"}},
        actor_id="reviewer-1")

    # 破損していなければ、written_fieldsで上書きせずそのまま返す
    assert result["content"]["why_this_matters"] == "問題ない文字列"


def test_run_report_reading_review_satisfies_check_expert_review_gate(monkeypatch):
    """review-modeセッションの既存バグ（Design不在によりrun_draft_ai_reviewが常に失敗し、
    check_expert_review_gateを永久に通過できない）への対応: Review V2完了後は、
    comparison_findingsが既存geo_ios_draft_ai_reviewsへ記録され、Gateが正しく（Critical
    findingが無ければ）通過できることを確認する。"""
    client = FakeSupabaseClient()
    client.tables.setdefault("geo_ios_report_drafts", []).append({
        "report_draft_id": "draft-1", "analysis_session_id": "sess-gate", "version": 1,
        "source": "uploaded", "status": "completed", "sections": [{"heading": "H", "body": "B"}],
    })
    report_reading = _fake_extracted_report_reading()
    _setup_extraction_fake(monkeypatch, report_reading)

    def fake_run_expert_request_with_mode(client_, azure_, model_, mode, theme_id, query_text,
                                           draft_text, *, profile, execution_mode,
                                           actor_intelligence_enabled=None):
        response_json = (_sample_expert_reading_response_json() if mode == "case_analysis"
                          else {"review_findings": [{"severity": "Minor", "finding": "軽微な指摘"}]})
        return _fake_engine_response(
            client, request_id="req-gate", response_id="resp-gate", response_json=response_json)

    monkeypatch.setattr(geo_expert_engine, "run_expert_request_with_mode",
                         fake_run_expert_request_with_mode)

    review_v2_service.start_run(
        client, "sess-gate", report_draft_id="draft-1", created_by="reviewer-1")
    review_v2_service.execute_run(
        client, azure_client=object(), model="gpt-test", analysis_session_id="sess-gate",
        report_text="レポート本文", actor_id="reviewer-1")

    current = draft_service.check_expert_review_gate(client, "sess-gate")  # raiseしない
    assert current["report_draft_id"] == "draft-1"
    latest_review = draft_service.get_latest_ai_review(client, "draft-1")
    assert latest_review["status"] == "completed"
    assert latest_review["findings"][0]["finding"] == "軽微な指摘"


def test_run_report_reading_review_is_not_rerun_once_completed(monkeypatch):
    """同一analysis_session_idに対して複数回呼ばれても、completed済みならExpert AIを
    再度呼ばない（べき等性、既存Reviewフローと同じ方針）。"""
    client = FakeSupabaseClient()
    report_reading = _fake_extracted_report_reading()
    _setup_extraction_fake(monkeypatch, report_reading)

    call_count = {"n": 0}

    def fake_run_expert_request_with_mode(client_, azure_, model_, mode, theme_id, query_text,
                                           draft_text, *, profile, execution_mode,
                                           actor_intelligence_enabled=None):
        call_count["n"] += 1
        response_json = (_sample_expert_reading_response_json() if mode == "case_analysis"
                          else {"review_findings": []})
        return _fake_engine_response(
            client, request_id=f"req-{call_count['n']}", response_id=f"resp-{call_count['n']}",
            response_json=response_json)

    monkeypatch.setattr(geo_expert_engine, "run_expert_request_with_mode",
                         fake_run_expert_request_with_mode)

    _start_and_execute(
        client, analysis_session_id="sess-2", report_text="レポート本文", actor_id="reviewer-1")
    first_call_count = call_count["n"]

    result_second = _start_and_execute(
        client, analysis_session_id="sess-2", report_text="レポート本文（2回目）",
        actor_id="reviewer-1")

    assert call_count["n"] == first_call_count  # Expert AIは再度呼ばれていない
    assert result_second["status"] == "completed"


def test_save_human_expert_disposition_records_agree_modify_reject():
    """Phase R2: Reading Review/Content Reviewの各findingに対するAgree/Modify/Reject
    判断を記録する。AI Reviewの結果をそのまま正解扱いしない——Agreeも含め、必ず人間の
    明示的な判断として記録される。"""
    client = FakeSupabaseClient()
    review_v2_service.start_run(
        client, "sess-r2", report_draft_id="draft-r2", created_by="reviewer-1")

    updated = review_v2_service.save_human_expert_disposition(
        client, "sess-r2", source="reading", finding_id="f1", disposition="agree",
        reason="妥当", modification=None, reviewed_by="expert-1")
    assert updated["human_expert_dispositions"]["reading:f1"]["disposition"] == "agree"

    updated = review_v2_service.save_human_expert_disposition(
        client, "sess-r2", source="content", finding_id="f2", disposition="modify",
        reason="表現を調整すべき", modification="修正後の文案。", reviewed_by="expert-1")
    assert updated["human_expert_dispositions"]["content:f2"]["disposition"] == "modify"
    assert updated["human_expert_dispositions"]["content:f2"]["modification"] == "修正後の文案。"
    # 既存のreading:f1は保持されたまま（上書きされない）
    assert updated["human_expert_dispositions"]["reading:f1"]["disposition"] == "agree"


def test_save_human_expert_disposition_requires_reason_for_modify_and_reject():
    client = FakeSupabaseClient()
    review_v2_service.start_run(
        client, "sess-r2b", report_draft_id="draft-r2b", created_by="reviewer-1")

    try:
        review_v2_service.save_human_expert_disposition(
            client, "sess-r2b", source="reading", finding_id="f1", disposition="reject",
            reason="", modification=None, reviewed_by="expert-1")
        assert False, "reasonが無い場合はraiseされるべき"
    except review_v2_service.HumanExpertReviewError:
        pass


def test_save_human_expert_disposition_rejects_unknown_source_or_disposition():
    client = FakeSupabaseClient()
    review_v2_service.start_run(
        client, "sess-r2c", report_draft_id="draft-r2c", created_by="reviewer-1")

    try:
        review_v2_service.save_human_expert_disposition(
            client, "sess-r2c", source="unknown", finding_id="f1", disposition="agree",
            reason="", modification=None, reviewed_by="expert-1")
        assert False
    except review_v2_service.HumanExpertReviewError:
        pass


def test_start_run_resets_to_pending_when_report_draft_id_changes(monkeypatch):
    """Phase R3: Revision（手直し保存で新しいreport_draft_idが生まれる）後、
    同一analysis_session_idでも新しいDraftに対して再度Review V2を実行できるよう、
    completed済みrunをpendingへリセットする（古いfindings/dispositionsは破棄）。"""
    client = FakeSupabaseClient()
    report_reading = _fake_extracted_report_reading()
    _setup_extraction_fake(monkeypatch, report_reading)

    def fake_run_expert_request_with_mode(client_, azure_, model_, mode, theme_id, query_text,
                                           draft_text, *, profile, execution_mode,
                                           actor_intelligence_enabled=None):
        response_json = (_sample_expert_reading_response_json() if mode == "case_analysis"
                          else {"review_findings": [{"severity": "Minor", "finding": "初回の指摘"}]})
        return _fake_engine_response(
            client, request_id="req-r3", response_id="resp-r3", response_json=response_json)

    monkeypatch.setattr(geo_expert_engine, "run_expert_request_with_mode",
                         fake_run_expert_request_with_mode)

    review_v2_service.start_run(
        client, "sess-revision", report_draft_id="draft-original", created_by="reviewer-1")
    review_v2_service.execute_run(
        client, azure_client=object(), model="gpt-test", analysis_session_id="sess-revision",
        report_text="レポート本文", actor_id="reviewer-1")
    review_v2_service.save_human_expert_disposition(
        client, "sess-revision", source="reading", finding_id="f1", disposition="agree",
        reason="妥当", modification=None, reviewed_by="expert-1")

    # Revision: 新しいreport_draft_idで再度start_run
    reset = review_v2_service.start_run(
        client, "sess-revision", report_draft_id="draft-revised", created_by="reviewer-1")
    assert reset["status"] == "pending"
    assert reset["report_draft_id"] == "draft-revised"
    assert reset["human_expert_dispositions"] == {}
    assert reset["comparison_findings"] is None

    # 同じreport_draft_idでの再呼び出しは従来どおり冪等（上書きしない）
    same_again = review_v2_service.start_run(
        client, "sess-revision", report_draft_id="draft-revised", created_by="reviewer-1")
    assert same_again["status"] == "pending"
    assert same_again["run_id"] == reset["run_id"]

    # 実際に再実行する: geo_ios_scenario_candidatesは(analysis_session_id, rank)に
    # unique制約があるため、前回runの行を残したままだと409 Conflictになる
    # （2026-10-04に実DBで確認したバグ）。再実行は古い行を削除してから成功すること。
    result2 = review_v2_service.execute_run(
        client, azure_client=object(), model="gpt-test", analysis_session_id="sess-revision",
        report_text="レポート本文（revision後）", actor_id="reviewer-1")
    assert result2["status"] == "completed"

    candidates = client.select(
        "geo_ios_scenario_candidates", {"select": "*", "analysis_session_id": "eq.sess-revision"})
    assert len(candidates) == 2  # 古い行が残っていない（REPORT_OWN + EXPERT_AI 1件ずつ）
    roles = sorted(c["content"]["reading_role"] for c in candidates)
    assert roles == ["EXPERT_AI_INDEPENDENT_READING", "REPORT_OWN_READING"]

    try:
        review_v2_service.save_human_expert_disposition(
            client, "sess-r2c", source="reading", finding_id="f1", disposition="unknown",
            reason="", modification=None, reviewed_by="expert-1")
        assert False
    except review_v2_service.HumanExpertReviewError:
        pass


def test_run_report_reading_review_failure_records_status_and_reraises(monkeypatch):
    client = FakeSupabaseClient()
    report_reading = _fake_extracted_report_reading()
    _setup_extraction_fake(monkeypatch, report_reading)

    def boom(*a, **kw):
        raise RuntimeError("Expert AI呼び出し失敗")

    monkeypatch.setattr(geo_expert_engine, "run_expert_request_with_mode", boom)

    try:
        _start_and_execute(
            client, analysis_session_id="sess-3", report_text="レポート本文", actor_id="reviewer-1")
        assert False, "例外が再送出されるべき"
    except Exception as e:
        assert "Expert AI呼び出しに失敗" in str(e) or "Expert AI呼び出し失敗" in str(e)

    run = review_v2_service.get_run(client, "sess-3")
    assert run["status"] == "failed"
