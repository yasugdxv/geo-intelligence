"""Geopolitical Intelligence OS Phase G: Learning Extraction Serviceのテスト。

§38-47のテスト要件（Extraction eligibility、Before/After、Generalization、Dedup、Conflict、
Idempotency、Promotion、Retrieval、Negative Transfer、CQAS Signal）を、実LLM呼び出しは
monkeypatchで固定応答に差し替えて検証する。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_analysis_session_service as session_service  # noqa: E402
import geo_ios_approval_service as approval_service  # noqa: E402
import geo_ios_knowledge_publication_service as publication_service  # noqa: E402
import geo_ios_learning_candidate_service as learning_service  # noqa: E402
import geo_ios_learning_extraction_service as extraction_service  # noqa: E402
import geo_ios_review_service as review_service  # noqa: E402
import geo_knowledge_service  # noqa: E402
import geo_llm_common  # noqa: E402
import geo_report_studio_service as report_studio_service  # noqa: E402
import geo_retrieval_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

PUBLISHER = "steward-1"
PUBLISHER_ROLE = "knowledge_steward"


def _fake_llm_result(candidates: list) -> dict:
    return {"data": {"candidates": candidates}, "mode": "structured_output",
            "usage": {"prompt_tokens": 1200, "completion_tokens": 300}}


def _base_candidate_payload(**overrides) -> dict:
    payload = {
        "candidate_type": "attention_rule",
        "title": "議会をSenate/Houseへ分解して見る",
        "initial_ai_weakness": "初期分析は政権のみに着目し議会を一枚岩として扱っていた",
        "expert_observation": "Expertは議会をSenateとHouseへ分けて分析すべきと指摘した",
        "was_accepted_and_reflected": True,
        "final_outcome": "承認版ではSenate/House別のIncentive分析が追加された",
        "generalized_rule": "米国政策分析では議会をSenateとHouseへ分けて別個に評価する",
        "is_reusable_beyond_report": True,
        "trigger": "米国の議会が関与する政策分析",
        "exceptions": ["超党派で完全に一致している場合は分解不要"],
        "positive_example": None, "failure_pattern_example": "議会を一枚岩として扱う",
        "scope": "domain_general", "temporal_stability": "stable", "confidence": "中",
        "source_comment_indices": [0],
    }
    payload.update(overrides)
    return payload


def _seed_full_pipeline(client, *, project_code="PRJ-G1"):
    """Analysis Session → Scenario Candidates → Design → Review → Approval → Publishまでを
    Phase C-D-Eの既存サービスだけを使って一通り再現する。"""
    session = session_service.create_session(
        client, title="米中半導体規制の分析", user_id="analyst-1", theme="米国の対中半導体規制")
    sid = session["analysis_session_id"]
    # text_similarity（Jaccard on .split()）は空白区切りのASCIIトークンでのみ有意な重なりを
    # 検出できるため（既存test_geo_intelligence_phase_e_enforcement.py等と同じ流儀）、
    # 候補ごとに区別可能な英語トークンを明示的に含める。
    candidates = session_service.record_scenario_candidates(
        client, sid, candidates=[
            {"title": "政権主導 White House Executive Branch 漸進強化",
             "content": {"why_this_matters": "White House Executive Branch現状路線の継続"}},
            {"title": "議会主導 Congress Senate House 急進強化",
             "content": {"why_this_matters": "Congress Senate House権限強化観測"}},
            {"title": "同盟国連携 Allies Export Control 強化",
             "content": {"why_this_matters": "多国間 Allies Export Control輸出管理"}},
        ], display_top_n=5, model="gpt-test", prompt_version="research_design-v1",
        cqas_knowledge_version="kb-1", actor_id="analyst-1")
    session_service.select_scenario_candidate(client, candidates[0]["scenario_candidate_id"], actor_id="analyst-1")
    session_service.create_design_version(
        client, sid, fields={"investigation_question": "対中半導体規制の帰結は？", "key_actors": ["White House"]},
        change_note="初版", created_by="analyst-1")
    session_service.create_design_version(
        client, sid, fields={"key_actors": ["White House", "Senate", "House"]},
        change_note="議会を追加", created_by="analyst-1")

    project = report_studio_service.get_or_create_project(client, project_code, "テストレポート")
    pid = project["project_id"]
    session_service.link_report_project(client, sid, report_project_id=pid, actor_id="analyst-1")

    cycle = review_service.request_review_cycle(
        client, report_project_id=pid, reviewer="expert-a", requested_by="analyst-1",
        version_label="v0.1", version_snapshot={"run_id": "r1", "syntheses": [{"final_html": "<p>v1</p>"}]})
    comment_resolved = review_service.add_comment(
        client, cycle["review_cycle_id"], comment="議会をSenate/Houseへ分解すべき",
        created_by="expert-a", anchor_type="section", severity="major")
    review_service.respond_to_comment(
        client, comment_resolved["review_comment_id"], resolution_status="resolved",
        author_response="Senate/House別に分解しました", actor_id="analyst-1")
    comment_style = review_service.add_comment(
        client, cycle["review_cycle_id"], comment="表記ゆれを直してください", created_by="expert-a",
        anchor_type="general", severity="minor")
    review_service.respond_to_comment(
        client, comment_style["review_comment_id"], resolution_status="resolved",
        author_response="修正しました", actor_id="analyst-1")
    comment_rejected = review_service.add_comment(
        client, cycle["review_cycle_id"], comment="全面的に書き直すべき", created_by="expert-a",
        anchor_type="general", severity="critical")
    review_service.respond_to_comment(
        client, comment_rejected["review_comment_id"], resolution_status="rejected",
        author_response="今回は対応しません", actor_id="analyst-1")
    review_service.submit_review_findings(
        client, cycle["review_cycle_id"], findings={"critical_findings": []}, result="approved",
        reviewer="expert-a")

    approval_event = approval_service.approve_in_app(
        client, report_project_id=pid, approver="expert-a", approver_role="approver", version_label="v1.0",
        version_snapshot={"run_id": "r1", "syntheses": [{"final_html": "<p>Senate/House別に分解した最終稿</p>"}]})
    return {"session": session, "project": project, "cycle": cycle,
            "comment_resolved": comment_resolved, "comment_style": comment_style,
            "comment_rejected": comment_rejected, "approval_event": approval_event,
            "scenario_candidates": candidates}


def _publish(client, seeded):
    return publication_service.publish_approved_report(
        client, report_project_id=seeded["project"]["project_id"],
        approval_event_id=seeded["approval_event"]["approval_event_id"], publisher=PUBLISHER,
        publisher_role=PUBLISHER_ROLE, human_visibility="company", agent_sharing_policy="geopolitics_only")


# ---- §38 Extraction eligibility ----------------------------------------------

def test_eligible_comments_excludes_rejected_and_keeps_resolved():
    client = FakeSupabaseClient()
    seeded = _seed_full_pipeline(client)
    source_package = extraction_service.build_learning_source_package(
        client, seeded["project"]["project_id"])
    eligible = extraction_service.eligible_comments(source_package)
    eligible_ids = {e["comment"]["review_comment_id"] for e in eligible}
    assert seeded["comment_resolved"]["review_comment_id"] in eligible_ids
    assert seeded["comment_style"]["review_comment_id"] in eligible_ids  # resolvedなので対象
    assert seeded["comment_rejected"]["review_comment_id"] not in eligible_ids  # rejectedは除外


# ---- §16 Publish gate ---------------------------------------------------------

def test_extraction_requires_published_by_default(monkeypatch):
    client = FakeSupabaseClient()
    seeded = _seed_full_pipeline(client)  # publishしない
    monkeypatch.setattr(geo_llm_common, "call_llm_structured",
                         lambda *a, **kw: _fake_llm_result([_base_candidate_payload()]))
    with pytest.raises(extraction_service.LearningExtractionError):
        extraction_service.extract_learning_candidates(
            client, azure_client=object(), model="gpt-test",
            report_project_id=seeded["project"]["project_id"])
    # require_published=Falseなら実行できる
    result = extraction_service.extract_learning_candidates(
        client, azure_client=object(), model="gpt-test",
        report_project_id=seeded["project"]["project_id"], require_published=False)
    assert len(result["candidates"]) == 1


# ---- §39 Before/After: 受け入れられた指摘だけが候補化される ----------------------

def test_accepted_and_reflected_comment_becomes_candidate(monkeypatch):
    client = FakeSupabaseClient()
    seeded = _seed_full_pipeline(client)
    _publish(client, seeded)
    monkeypatch.setattr(geo_llm_common, "call_llm_structured",
                         lambda *a, **kw: _fake_llm_result([_base_candidate_payload()]))

    result = extraction_service.extract_learning_candidates(
        client, azure_client=object(), model="gpt-test",
        report_project_id=seeded["project"]["project_id"])
    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["candidate_type"] == "attention_rule"
    assert candidate["status"] == "candidate"  # まだHuman Review前
    tc = candidate["trigger_context"]
    assert tc["initial_ai_weakness"]
    assert tc["final_outcome"]
    assert tc["source_comment_ids"] == [seeded["comment_resolved"]["review_comment_id"]]


def test_not_accepted_or_not_reusable_does_not_create_candidate(monkeypatch):
    client = FakeSupabaseClient()
    seeded = _seed_full_pipeline(client)
    _publish(client, seeded)
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", lambda *a, **kw: _fake_llm_result([
        _base_candidate_payload(was_accepted_and_reflected=False),
        _base_candidate_payload(is_reusable_beyond_report=False, scope="report_specific"),
    ]))
    result = extraction_service.extract_learning_candidates(
        client, azure_client=object(), model="gpt-test",
        report_project_id=seeded["project"]["project_id"])
    assert result["candidates"] == []
    assert result["proposed_count"] == 2


# ---- §41 Dedup / §43 Idempotency ----------------------------------------------

def test_second_extraction_same_fingerprint_is_idempotent(monkeypatch):
    client = FakeSupabaseClient()
    seeded = _seed_full_pipeline(client)
    _publish(client, seeded)
    call_count = {"n": 0}

    def fake_call(*a, **kw):
        call_count["n"] += 1
        return _fake_llm_result([_base_candidate_payload()])

    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake_call)

    first = extraction_service.extract_learning_candidates(
        client, azure_client=object(), model="gpt-test", report_project_id=seeded["project"]["project_id"])
    second = extraction_service.extract_learning_candidates(
        client, azure_client=object(), model="gpt-test", report_project_id=seeded["project"]["project_id"])

    assert call_count["n"] == 1  # 2回目はLLMを呼ばない
    assert second["idempotent_reuse"] is True
    assert len(second["candidates"]) == len(first["candidates"]) == 1
    all_candidates = learning_service.list_candidates(client)
    assert len(all_candidates) == 1  # 無制限増殖しない


def test_duplicate_generalized_rule_from_another_report_is_deduplicated(monkeypatch):
    client = FakeSupabaseClient()
    seeded = _seed_full_pipeline(client)
    _publish(client, seeded)
    monkeypatch.setattr(geo_llm_common, "call_llm_structured",
                         lambda *a, **kw: _fake_llm_result([_base_candidate_payload()]))
    extraction_service.extract_learning_candidates(
        client, azure_client=object(), model="gpt-test", report_project_id=seeded["project"]["project_id"])

    # 別Reportから、ほぼ同一のgeneralized_ruleを持つ候補を抽出しようとする
    seeded2 = _seed_full_pipeline(client, project_code="PRJ-G2")
    _publish(client, seeded2)
    result2 = extraction_service.extract_learning_candidates(
        client, azure_client=object(), model="gpt-test", report_project_id=seeded2["project"]["project_id"],
        prompt_version="v2-different-prompt")  # fingerprint変化で再実行を許可

    assert result2["deduplicated_count"] == 1
    assert result2["candidates"] == []


# ---- §42 Conflict ----------------------------------------------------------------

def test_moderately_similar_existing_knowledge_flagged_as_conflict_not_overwritten(monkeypatch):
    client = FakeSupabaseClient()
    # 既存Active Knowledge: Executive Branchを優先すべき、というRule
    existing_rule_text = "reasoning rule Executive Branch export control implementation priority focus"
    client.insert("geo_knowledge_items", [{
        "knowledge_type": "reasoning_rule", "title": "Executive Branchによる輸出管理実施を注視",
        "structured_content": {"rule": existing_rule_text},
        "status": "active", "retrieval_status": "available", "is_demo": False,
        "confidence": "中", "information_class": "internal_hypothesis", "created_by": "steward-1",
    }])
    seeded = _seed_full_pipeline(client)
    _publish(client, seeded)
    # 既存Ruleと語彙を大きく共有しつつ主体だけが対立する新候補（中程度の類似度を狙う）
    conflicting_rule_text = "reasoning rule Congress Senate House export control implementation priority focus"
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", lambda *a, **kw: _fake_llm_result([
        _base_candidate_payload(generalized_rule=conflicting_rule_text),
    ]))
    result = extraction_service.extract_learning_candidates(
        client, azure_client=object(), model="gpt-test", report_project_id=seeded["project"]["project_id"])
    assert len(result["candidates"]) == 1
    tc = result["candidates"][0]["trigger_context"]
    assert tc["dedup_status"] == "possible_conflict_or_overlap"
    assert tc["related"]
    # 既存Active Knowledgeは自動上書きされない
    existing_items = client.select("geo_knowledge_items", {"select": "*"})
    assert any(i["title"] == "Executive Branchによる輸出管理実施を注視" and i["status"] == "active"
               for i in existing_items)


# ---- §47 CQAS Signal -----------------------------------------------------------

def test_cqas_signal_validated_when_rule_matches_selected_scenario(monkeypatch):
    client = FakeSupabaseClient()
    seeded = _seed_full_pipeline(client)
    _publish(client, seeded)
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", lambda *a, **kw: _fake_llm_result([
        _base_candidate_payload(generalized_rule="White House Executive Branchが漸進的に規制を強化する路線を注視する"),
    ]))
    result = extraction_service.extract_learning_candidates(
        client, azure_client=object(), model="gpt-test", report_project_id=seeded["project"]["project_id"])
    signal = result["candidates"][0]["trigger_context"]["cqas_signal"]
    assert signal["signal"] == "validated_candidate"


def test_cqas_signal_selection_miss_when_rule_matches_displayed_but_unselected_candidate(monkeypatch):
    client = FakeSupabaseClient()
    seeded = _seed_full_pipeline(client)
    _publish(client, seeded)
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", lambda *a, **kw: _fake_llm_result([
        _base_candidate_payload(generalized_rule="Congress Senate Houseが急進的に規制を強化する可能性を注視する"),
    ]))
    result = extraction_service.extract_learning_candidates(
        client, azure_client=object(), model="gpt-test", report_project_id=seeded["project"]["project_id"])
    signal = result["candidates"][0]["trigger_context"]["cqas_signal"]
    assert signal["signal"] == "selection_miss"


def test_cqas_signal_generation_miss_when_no_candidate_relates(monkeypatch):
    client = FakeSupabaseClient()
    seeded = _seed_full_pipeline(client)
    _publish(client, seeded)
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", lambda *a, **kw: _fake_llm_result([
        _base_candidate_payload(generalized_rule="サウジアラビアの石油政策における王室内力学を分析する"),
    ]))
    result = extraction_service.extract_learning_candidates(
        client, azure_client=object(), model="gpt-test", report_project_id=seeded["project"]["project_id"])
    signal = result["candidates"][0]["trigger_context"]["cqas_signal"]
    assert signal["signal"] == "generation_miss"


# ---- §44 Promotion二重Gate / §45 Retrieval / §46 Negative Transfer -----------------

def test_full_promotion_and_retrieval_integration_with_negative_transfer(monkeypatch):
    client = FakeSupabaseClient()
    seeded = _seed_full_pipeline(client)
    _publish(client, seeded)
    monkeypatch.setattr(geo_llm_common, "call_llm_structured",
                         lambda *a, **kw: _fake_llm_result([_base_candidate_payload()]))
    result = extraction_service.extract_learning_candidates(
        client, azure_client=object(), model="gpt-test", report_project_id=seeded["project"]["project_id"])
    candidate = result["candidates"][0]

    # Human Review: Approve → Promote（既存二重Gate、§21/§44）
    learning_service.approve_candidate(client, candidate["candidate_id"], reviewed_by=PUBLISHER, reviewer_role=PUBLISHER_ROLE)
    knowledge_item = learning_service.promote_via_mapper(
        client, candidate["candidate_id"], created_by=PUBLISHER, created_by_role=PUBLISHER_ROLE)
    assert knowledge_item["status"] == "candidate"  # まだactiveではない

    # §45 candidate only → Expert AI cannot retrieve
    relevant_now = geo_retrieval_service.get_relevant_reasoning_rules(client, ["issue-relevant"])
    assert knowledge_item["knowledge_item_id"] not in [r["knowledge_item_id"] for r in relevant_now]

    # Human Review: 既存review_knowledge_itemでactiveへ（Knowledge DB自身の二重Gate）
    geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=knowledge_item["knowledge_item_id"], reviewer_id=PUBLISHER,
        reviewer_role=PUBLISHER_ROLE, review_decision="start_review", review_comment="レビュー開始",
        evidence_adequacy="sufficient", applicability_checked=True)
    geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=knowledge_item["knowledge_item_id"], reviewer_id=PUBLISHER,
        reviewer_role=PUBLISHER_ROLE, review_decision="approve_active", review_comment="承認",
        evidence_adequacy="sufficient", applicability_checked=True)

    # active化しただけでは、issueへリンクされるまでは特定issueのRetrievalに出てこない
    # （§28 Negative Transfer: 無関係issueへ持ち出さない設計そのものの確認）
    relevant_unlinked = geo_retrieval_service.get_relevant_reasoning_rules(client, ["issue-relevant"])
    assert knowledge_item["knowledge_item_id"] not in [r["knowledge_item_id"] for r in relevant_unlinked]

    # 人間が既存の関連付けAPIで、関連あるissueへ明示的にリンクする
    geo_knowledge_service.link_to_issue(
        client, knowledge_item_id=knowledge_item["knowledge_item_id"], issue_id="issue-relevant")

    # §45: リンクしたissueでは取得できる
    relevant_after_link = geo_retrieval_service.get_relevant_reasoning_rules(client, ["issue-relevant"])
    assert knowledge_item["knowledge_item_id"] in [r["knowledge_item_id"] for r in relevant_after_link]

    # §46 Negative Transfer: リンクしていない無関係issueには出てこない
    unrelated = geo_retrieval_service.get_relevant_reasoning_rules(client, ["issue-unrelated-saudi-oil"])
    assert knowledge_item["knowledge_item_id"] not in [r["knowledge_item_id"] for r in unrelated]


def test_rejected_candidate_never_reaches_knowledge_items(monkeypatch):
    client = FakeSupabaseClient()
    seeded = _seed_full_pipeline(client)
    _publish(client, seeded)
    monkeypatch.setattr(geo_llm_common, "call_llm_structured",
                         lambda *a, **kw: _fake_llm_result([_base_candidate_payload()]))
    result = extraction_service.extract_learning_candidates(
        client, azure_client=object(), model="gpt-test", report_project_id=seeded["project"]["project_id"])
    candidate = result["candidates"][0]
    learning_service.reject_candidate(client, candidate["candidate_id"], reviewed_by=PUBLISHER, reviewer_role=PUBLISHER_ROLE)
    with pytest.raises(learning_service.LearningCandidateError):
        learning_service.promote_via_mapper(client, candidate["candidate_id"], created_by=PUBLISHER, created_by_role=PUBLISHER_ROLE)
    # _publish()によるapproved_report Knowledge Item以外、reasoning_rule等は一切作られない
    knowledge_types = [i["knowledge_type"] for i in client.tables.get("geo_knowledge_items", [])]
    assert knowledge_types == ["approved_report"]
