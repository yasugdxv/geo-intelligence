"""地政学ナレッジ＆インテリジェンスDB Phase2B-2: Internal Expert Learning Layer単体テスト。

Expert Review Session→Delta Extraction→Promote/Reinforce/Keep/Rejectの一連を検証する。
外部LLM APIはモックする（既存tests/test_geo_phase2a.py等と同じ_mock_llm_client方式）。
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_connector_base as base  # noqa: E402
import geo_expert_review_service as review_service  # noqa: E402
import geo_knowledge_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _mock_llm_client(response: dict) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(response)))],
    )
    return client


def _base_client(tmp_path):
    return FakeSupabaseClient(tables={"geo_themes": [{"theme_id": "theme-1"}]})


def _start_session(client, tmp_path, monkeypatch, *, case_id="case-legacy-1", report_id=None):
    # 地政学アプリClooseout Phase4: case_id/report_idはexactly-one制約になったため、
    # このヘルパーはcase_idベースの既定値を持つ（report_idを指定する場合は
    # case_id=Noneを明示的に渡す）。
    monkeypatch.setattr(base, "RAW_SNAPSHOT_ROOT", tmp_path)
    return review_service.start_review_session(
        client, theme_id="theme-1", case_id=case_id, report_id=report_id,
        draft_text="Iran is increasingly likely to close the Strait of Hormuz.",
        final_text="Iran may gain more leverage from preserving the capability to close the strait "
                    "than from actually closing it. Capability and intent should be assessed separately.",
        reviewer_id="analyst1", reviewer_label="Senior Analyst",
    )


def test_start_review_session_creates_draft_and_final_documents(tmp_path, monkeypatch):
    client = _base_client(tmp_path)
    session = _start_session(client, tmp_path, monkeypatch)
    assert session["draft_document_id"] != session["final_document_id"]
    assert len(client.tables["geo_documents"]) == 2
    assert len(client.tables["geo_source_registry"]) == 1
    assert client.tables["geo_source_registry"][0]["name"] == review_service.INTERNAL_REVIEW_SOURCE_NAME
    assert client.tables["geo_source_registry"][0]["trust_tier"] == "E"


CHANGE_RESPONSE = {
    "changes": [
        {
            "change_type": "judgment_change", "reusability_class": "reusable_reasoning",
            "reasoning_summary": "Capability(能力)とIntent(意図)を分離して評価すべき",
            "confidence": "中",
            "draft_excerpt": "Iran is increasingly likely to close the Strait of Hormuz.",
            "final_excerpt": "Iran may gain more leverage from preserving the capability...",
            "grounding_note_ids": [],
        },
    ],
}


def test_extract_deltas_creates_changes_and_marks_session_completed(tmp_path, monkeypatch):
    client = _base_client(tmp_path)
    session = _start_session(client, tmp_path, monkeypatch)
    azure_client = _mock_llm_client(CHANGE_RESPONSE)

    result = review_service.extract_deltas(
        client, azure_client, "gpt-4o", session["review_session_id"], actor_id="analyst1")

    assert result["already_extracted"] is False
    assert len(result["changes"]) == 1
    assert result["changes"][0]["change_type"] == "judgment_change"
    updated_session = client.select("geo_expert_review_sessions", {
        "select": "*", "review_session_id": f"eq.{session['review_session_id']}", "limit": "1"})[0]
    assert updated_session["delta_extraction_status"] == "completed"


def test_extract_deltas_is_idempotent_on_second_call(tmp_path, monkeypatch):
    client = _base_client(tmp_path)
    session = _start_session(client, tmp_path, monkeypatch)
    azure_client = _mock_llm_client(CHANGE_RESPONSE)

    review_service.extract_deltas(client, azure_client, "gpt-4o", session["review_session_id"], actor_id="analyst1")
    result2 = review_service.extract_deltas(client, azure_client, "gpt-4o", session["review_session_id"], actor_id="analyst1")

    assert result2["already_extracted"] is True
    assert len(client.tables["geo_expert_review_changes"]) == 1  # 重複作成されない
    assert azure_client.chat.completions.create.call_count == 1  # 2回目はLLMを呼ばない


WORDING_ONLY_RESPONSE = {
    "changes": [
        {
            "change_type": "judgment_change", "reusability_class": "reusable_reasoning",
            "reasoning_summary": "表現の言い回しのみの変更", "confidence": "低",
            "draft_excerpt": None, "final_excerpt": None, "grounding_note_ids": [],
        },
    ],
}


def test_wording_only_change_without_any_excerpt_is_not_created(tmp_path, monkeypatch):
    client = _base_client(tmp_path)
    session = _start_session(client, tmp_path, monkeypatch)
    azure_client = _mock_llm_client(WORDING_ONLY_RESPONSE)

    result = review_service.extract_deltas(
        client, azure_client, "gpt-4o", session["review_session_id"], actor_id="analyst1")
    assert result["changes"] == []


ONE_SIDED_RESPONSE = {
    "changes": [
        {
            "change_type": "stakeholder_added", "reusability_class": "reusable_reasoning",
            "reasoning_summary": "中国を主要な需要側アクターとして追加すべき", "confidence": "高",
            "draft_excerpt": None, "final_excerpt": "China should be included as a demand-side actor.",
            "grounding_note_ids": [],
        },
    ],
}


def test_change_with_only_final_excerpt_is_stored_with_null_draft_excerpt(tmp_path, monkeypatch):
    client = _base_client(tmp_path)
    session = _start_session(client, tmp_path, monkeypatch)
    azure_client = _mock_llm_client(ONE_SIDED_RESPONSE)

    result = review_service.extract_deltas(
        client, azure_client, "gpt-4o", session["review_session_id"], actor_id="analyst1")
    assert len(result["changes"]) == 1
    assert result["changes"][0]["draft_excerpt"] is None
    assert result["changes"][0]["final_excerpt"] is not None


def test_review_note_is_used_in_prompt_and_grounding_recorded(tmp_path, monkeypatch):
    client = _base_client(tmp_path)
    session = _start_session(client, tmp_path, monkeypatch)
    note = review_service.add_review_note(
        client, review_session_id=session["review_session_id"],
        comment_text="能力と意図は別物として評価すべき、というのが今回の主眼", author="analyst1")

    response_with_grounding = {
        "changes": [{
            "change_type": "judgment_change", "reusability_class": "reusable_reasoning",
            "reasoning_summary": "能力と意図は別物として評価すべき、というのが今回の主眼",
            "confidence": "高", "draft_excerpt": "d", "final_excerpt": "f",
            "grounding_note_ids": [{"note_id": note["note_id"], "role": "explicit_reason"}],
        }],
    }
    azure_client = _mock_llm_client(response_with_grounding)
    review_service.extract_deltas(client, azure_client, "gpt-4o", session["review_session_id"], actor_id="analyst1")

    # Noteの文言がLLM推測より優先されたことを示すsystem/user promptがNoteを含んでいたことを確認
    call_kwargs = azure_client.chat.completions.create.call_args.kwargs
    prompt_text = json.dumps(call_kwargs["messages"])
    assert note["note_id"] in prompt_text or "能力と意図は別物" in prompt_text

    change = client.tables["geo_expert_review_changes"][0]
    assert change["reasoning_summary"] == "能力と意図は別物として評価すべき、というのが今回の主眼"
    grounding = [g for g in client.tables["geo_expert_review_change_notes"] if g["change_id"] == change["change_id"]]
    assert len(grounding) == 1
    assert grounding[0]["role"] == "explicit_reason"


def test_extract_deltas_ignores_hallucinated_note_id(tmp_path, monkeypatch):
    """★最終フィードバック2: Sessionに実在しないnote_idはgeo_expert_review_change_notesへ
    保存されないが、Change自体の抽出は成功する。"""
    client = _base_client(tmp_path)
    session = _start_session(client, tmp_path, monkeypatch)
    response = {
        "changes": [{
            "change_type": "judgment_change", "reusability_class": "reusable_reasoning",
            "reasoning_summary": "x", "confidence": "中", "draft_excerpt": "d", "final_excerpt": "f",
            "grounding_note_ids": [{"note_id": "nonexistent-note-id", "role": "explicit_reason"}],
        }],
    }
    azure_client = _mock_llm_client(response)
    result = review_service.extract_deltas(
        client, azure_client, "gpt-4o", session["review_session_id"], actor_id="analyst1")

    assert len(result["changes"]) == 1  # Change抽出自体は成功する
    assert client.tables.get("geo_expert_review_change_notes", []) == []  # 実在しないnote_idは保存されない


def test_extract_deltas_links_changes_to_case_issues(tmp_path, monkeypatch):
    client = _base_client(tmp_path)
    case = client.insert("geo_cases", [{"case_title": "テスト案件", "original_question": "問い"}])[0]
    issue = client.insert("geo_issues", [{"theme_id": "theme-1", "code": "i1", "name_ja": "論点"}])[0]
    client.insert("geo_case_issues", [{"case_id": case["case_id"], "issue_id": issue["issue_id"]}])

    session = _start_session(client, tmp_path, monkeypatch, case_id=case["case_id"])
    azure_client = _mock_llm_client(CHANGE_RESPONSE)
    result = review_service.extract_deltas(
        client, azure_client, "gpt-4o", session["review_session_id"], actor_id="analyst1")

    change_id = result["changes"][0]["change_id"]
    links = [l for l in client.tables["geo_expert_review_change_issues"] if l["change_id"] == change_id]
    assert {l["issue_id"] for l in links} == {issue["issue_id"]}


# ── Promote / Reinforce / Keep / Reject ─────────────────────────────────

def _create_reusable_change(client, tmp_path, monkeypatch, *, change_type="mechanism_added"):
    session = _start_session(client, tmp_path, monkeypatch)
    response = {"changes": [{
        "change_type": change_type, "reusability_class": "reusable_reasoning",
        "reasoning_summary": "海上保険料の上昇が物理的な航行途絶より先行する", "confidence": "中",
        "draft_excerpt": "d", "final_excerpt": "f", "grounding_note_ids": [],
    }]}
    azure_client = _mock_llm_client(response)
    result = review_service.extract_deltas(client, azure_client, "gpt-4o", session["review_session_id"],
                                            actor_id="analyst1")
    return result["changes"][0]


def test_promote_reusable_reasoning_change_creates_active_reasoning_rule(tmp_path, monkeypatch):
    client = _base_client(tmp_path)
    change = _create_reusable_change(client, tmp_path, monkeypatch, change_type="evidence_weight_changed")

    result = review_service.promote_change(client, change_id=change["change_id"], reviewer_id="reviewer1")

    item = result["knowledge_item"]
    assert item["status"] == "active"
    assert item["knowledge_type"] == "reasoning_rule"
    assert item["information_class"] == "internal_expert_assessment"
    assert item["origin"] == "internal_expert_review"
    assert item["derived_from_change_id"] == change["change_id"]
    assert result["change"]["disposition"] == "promoted"

    # candidate→in_review→activeの2段階遷移のため、レビューログは2件（start_review, approve_active）
    review_logs = [log for log in client.tables["geo_audit_logs"] if log["action"] == "knowledge_item_review"]
    assert len(review_logs) == 2
    assert review_logs[-1]["detail"]["new_status"] == "active"


def test_promote_change_links_supporting_evidence_chain(tmp_path, monkeypatch):
    """実DB検証で発見: Promoteされたitemにgeo_knowledge_item_claims（Evidence必須条件）が
    無いと、geo_expert_engine._has_supporting_evidence_chainにより二度とExpert Engineへ
    投入されなくなる（既存のCandidate抽出と同じ既知の落とし穴）。"""
    client = _base_client(tmp_path)
    change = _create_reusable_change(client, tmp_path, monkeypatch)

    result = review_service.promote_change(client, change_id=change["change_id"], reviewer_id="reviewer1")
    item_id = result["knowledge_item"]["knowledge_item_id"]

    links = client.select("geo_knowledge_item_claims", {
        "select": "*", "knowledge_item_id": f"eq.{item_id}", "relationship": "eq.supporting",
    })
    assert len(links) == 1
    evidence = client.select("geo_claim_evidence", {"select": "*", "claim_id": f"eq.{links[0]['claim_id']}"})
    assert len(evidence) == 1
    assert evidence[0]["evidence_role"] == "supporting"


def test_promote_change_returns_refreshed_knowledge_item_not_stale_candidate_snapshot(tmp_path, monkeypatch):
    """実DB検証で発見: create_knowledge_item()の戻り値（status=candidateのスナップショット）を
    review_knowledge_item()呼び出し後もそのまま返すと、実際のHTTP経由のSupabaseClientでは
    別リクエストのため自動反映されず、呼び出し元へstatus=candidateのまま返ってしまう
    （FakeSupabaseClientは辞書を参照共有するため元々このテストでは再現しないが、
    get_knowledge_item()で明示的に再取得するようになったことをここでも確認する）。"""
    client = _base_client(tmp_path)
    change = _create_reusable_change(client, tmp_path, monkeypatch)
    result = review_service.promote_change(client, change_id=change["change_id"], reviewer_id="reviewer1")
    assert result["knowledge_item"]["status"] == "active"
    assert result["knowledge_item"]["retrieval_status"] == "available"


def test_promote_mechanism_added_creates_mechanism_knowledge_type(tmp_path, monkeypatch):
    client = _base_client(tmp_path)
    change = _create_reusable_change(client, tmp_path, monkeypatch, change_type="mechanism_added")
    result = review_service.promote_change(client, change_id=change["change_id"], reviewer_id="reviewer1")
    assert result["knowledge_item"]["knowledge_type"] == "mechanism"


def test_promote_case_specific_assessment_creates_case_finding(tmp_path, monkeypatch):
    client = _base_client(tmp_path)
    case = client.insert("geo_cases", [{"case_title": "ホルムズ海峡分析", "original_question": "問い"}])[0]
    session = _start_session(client, tmp_path, monkeypatch, case_id=case["case_id"])
    response = {"changes": [{
        "change_type": "main_hypothesis_change", "reusability_class": "case_specific_assessment",
        "reasoning_summary": "本件では全面閉鎖より段階的威嚇がMain Hypothesisとして妥当",
        "confidence": "中", "draft_excerpt": "d", "final_excerpt": "f", "grounding_note_ids": [],
    }]}
    azure_client = _mock_llm_client(response)
    extraction = review_service.extract_deltas(client, azure_client, "gpt-4o", session["review_session_id"],
                                                 actor_id="analyst1")
    change = extraction["changes"][0]

    result = review_service.promote_change(client, change_id=change["change_id"], reviewer_id="reviewer1")
    assert result["knowledge_item"]["knowledge_type"] == "case_finding"
    assert result["knowledge_item"]["status"] == "active"


def test_keep_case_only_does_not_create_knowledge_item(tmp_path, monkeypatch):
    client = _base_client(tmp_path)
    change = _create_reusable_change(client, tmp_path, monkeypatch)
    before_count = len(client.tables.get("geo_knowledge_items", []))

    updated = review_service.keep_case_only(client, change_id=change["change_id"], reviewer_id="reviewer1")
    assert updated["disposition"] == "kept_case_only"
    assert len(client.tables.get("geo_knowledge_items", [])) == before_count


def test_reject_change_does_not_create_knowledge_item(tmp_path, monkeypatch):
    client = _base_client(tmp_path)
    change = _create_reusable_change(client, tmp_path, monkeypatch)
    before_count = len(client.tables.get("geo_knowledge_items", []))

    updated = review_service.reject_change(client, change_id=change["change_id"], reviewer_id="reviewer1")
    assert updated["disposition"] == "rejected_as_non_reusable"
    assert len(client.tables.get("geo_knowledge_items", [])) == before_count


def test_reinforce_change_increments_support_count_without_new_item(tmp_path, monkeypatch):
    client = _base_client(tmp_path)
    existing_item = geo_knowledge_service.create_knowledge_item(
        client, knowledge_type="reasoning_rule", title="既存ルール", structured_content={
            "rule": "r", "rationale": "ra", "applicable_question_types": [], "required_checks": [],
            "comparison_dimensions": [], "common_failure_modes": [], "expert_source": "s",
            "approval_status": "active",
        }, information_class="internal_expert_assessment", created_by="reviewer1")
    before_count = len(client.tables.get("geo_knowledge_items", []))

    change = _create_reusable_change(client, tmp_path, monkeypatch)
    result = review_service.reinforce_change(
        client, change_id=change["change_id"], reviewer_id="reviewer1",
        reinforced_knowledge_item_id=existing_item["knowledge_item_id"])

    assert len(client.tables.get("geo_knowledge_items", [])) == before_count  # 新規itemは作られない
    assert result["knowledge_item"]["support_count"] == 1
    assert result["knowledge_item"]["last_confirmed_at"] is not None
    assert result["change"]["disposition"] == "reinforced"
    assert result["change"]["reinforced_knowledge_item_id"] == existing_item["knowledge_item_id"]


def test_promote_change_twice_raises_not_pending_error(tmp_path, monkeypatch):
    """★最終フィードバック1: 冪等性。disposition='pending'を条件にした更新のため、
    2回目のpromoteはKnowledge Itemを二重生成せずエラーになる。"""
    client = _base_client(tmp_path)
    change = _create_reusable_change(client, tmp_path, monkeypatch)
    review_service.promote_change(client, change_id=change["change_id"], reviewer_id="reviewer1")
    item_count_after_first = len(client.tables.get("geo_knowledge_items", []))

    with pytest.raises(review_service.ExpertReviewChangeNotPendingError):
        review_service.promote_change(client, change_id=change["change_id"], reviewer_id="reviewer1")

    assert len(client.tables.get("geo_knowledge_items", [])) == item_count_after_first  # 二重生成されない


def test_reinforce_change_twice_raises_not_pending_error(tmp_path, monkeypatch):
    client = _base_client(tmp_path)
    existing_item = geo_knowledge_service.create_knowledge_item(
        client, knowledge_type="reasoning_rule", title="既存ルール", structured_content={
            "rule": "r", "rationale": "ra", "applicable_question_types": [], "required_checks": [],
            "comparison_dimensions": [], "common_failure_modes": [], "expert_source": "s",
            "approval_status": "active",
        }, information_class="internal_expert_assessment", created_by="reviewer1")
    change = _create_reusable_change(client, tmp_path, monkeypatch)

    review_service.reinforce_change(client, change_id=change["change_id"], reviewer_id="reviewer1",
                                     reinforced_knowledge_item_id=existing_item["knowledge_item_id"])
    with pytest.raises(review_service.ExpertReviewChangeNotPendingError):
        review_service.reinforce_change(client, change_id=change["change_id"], reviewer_id="reviewer1",
                                         reinforced_knowledge_item_id=existing_item["knowledge_item_id"])

    final_item = geo_knowledge_service.get_knowledge_item(client, existing_item["knowledge_item_id"])
    assert final_item["support_count"] == 1  # 2重加算されない
