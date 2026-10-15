"""Geopolitical Intelligence OS: Native Report Draft（IOS自身のDraft生成〜AI Review〜
Expert Review Freeze〜Approval）のテスト。Report Studio（geo_report_studio_*）には
一切触れない、純粋にgeo_ios_report_draft_service.pyの振る舞いのみを検証する。
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_report_draft_service as draft_service  # noqa: E402
import geo_llm_common  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _fake_import_llm_result(sections: list) -> dict:
    return {"data": {"draft_sections": sections}, "mode": "structured_output",
            "usage": {"prompt_tokens": 500, "completion_tokens": 200}}


def _ai_generated_draft(client, session_id="session-1"):
    return draft_service.create_draft_version(
        client, session_id, source="ai_generated", created_by="analyst-1")


def _complete(client, report_draft_id, sections=None):
    sections = sections or [{"heading": "Executive assessment", "body": "本文", "supporting_evidence_ids": ["ev-1"]}]
    return draft_service.mark_draft_completed(
        client, report_draft_id, sections=sections, expert_request_id="req-1", expert_response_id="resp-1")


# ---- Draft version: immutable, always increments -----------------------------

def test_create_draft_version_ai_generated_starts_pending_at_version_1():
    client = FakeSupabaseClient()
    draft = _ai_generated_draft(client)
    assert draft["version"] == 1
    assert draft["status"] == "pending"
    assert draft["parent_report_draft_id"] is None
    assert draft["sections"] is None


def test_mark_draft_completed_computes_and_stores_content_hash():
    client = FakeSupabaseClient()
    draft = _ai_generated_draft(client)
    completed = _complete(client, draft["report_draft_id"])
    assert completed["status"] == "completed"
    assert completed["content_hash"]
    # 同じsectionsなら同じhash（決定的）
    assert completed["content_hash"] == draft_service._compute_content_hash(completed["sections"])


def test_human_edit_creates_new_version_not_in_place_update():
    client = FakeSupabaseClient()
    v1 = _ai_generated_draft(client)
    _complete(client, v1["report_draft_id"])

    v2 = draft_service.create_draft_version(
        client, "session-1", source="human_edit", created_by="analyst-1",
        sections=[{"heading": "Executive assessment", "body": "修正後", "supporting_evidence_ids": []}],
        base_report_draft_id=v1["report_draft_id"])
    assert v2["version"] == 2
    assert v2["status"] == "completed"
    assert v2["parent_report_draft_id"] == v1["report_draft_id"]
    assert v2["content_hash"]

    # v1自身は変更されていない（immutable）
    v1_reloaded = draft_service.get_draft(client, v1["report_draft_id"])
    assert v1_reloaded["sections"][0]["body"] == "本文"
    assert draft_service.get_current_draft(client, "session-1")["report_draft_id"] == v2["report_draft_id"]


def test_human_edit_with_stale_base_raises_conflict():
    client = FakeSupabaseClient()
    v1 = _ai_generated_draft(client)
    _complete(client, v1["report_draft_id"])
    v2 = draft_service.create_draft_version(
        client, "session-1", source="human_edit", created_by="analyst-1",
        sections=[{"heading": "H", "body": "B", "supporting_evidence_ids": []}],
        base_report_draft_id=v1["report_draft_id"])
    assert v2["version"] == 2

    # v1をbaseにした別の編集（v2が既に存在するのに古いv1を基準にしている）はConflict
    with pytest.raises(draft_service.StaleDraftEditError):
        draft_service.create_draft_version(
            client, "session-1", source="human_edit", created_by="analyst-2",
            sections=[{"heading": "H2", "body": "B2", "supporting_evidence_ids": []}],
            base_report_draft_id=v1["report_draft_id"])


def test_human_edit_requires_sections():
    client = FakeSupabaseClient()
    with pytest.raises(draft_service.ReportDraftError):
        draft_service.create_draft_version(
            client, "session-1", source="human_edit", created_by="analyst-1",
            sections=None, base_report_draft_id=None)


def test_create_draft_version_rejects_unknown_source():
    client = FakeSupabaseClient()
    with pytest.raises(draft_service.ReportDraftError):
        draft_service.create_draft_version(
            client, "session-1", source="not-a-source", created_by="analyst-1")


def test_version_numbering_retries_on_unique_violation():
    client = FakeSupabaseClient()
    real_insert = client.insert
    call_count = {"n": 0}

    def flaky_insert(table, rows, prefer="return=representation"):
        if table == "geo_ios_report_drafts" and call_count["n"] == 0:
            call_count["n"] += 1
            resp = requests.Response()
            resp.status_code = 409
            resp._content = b'{"code":"23505"}'
            raise requests.exceptions.HTTPError(response=resp)
        return real_insert(table, rows, prefer)

    with patch.object(client, "insert", side_effect=flaky_insert):
        draft = _ai_generated_draft(client)
    assert draft["version"] == 1
    assert call_count["n"] == 1


# ---- AI Review: append-only, waive requires reason ---------------------------

def test_ai_review_attempts_are_append_only_not_upserted():
    client = FakeSupabaseClient()
    draft = _ai_generated_draft(client)
    _complete(client, draft["report_draft_id"])

    a1 = draft_service.create_ai_review_attempt(client, draft["report_draft_id"], requested_by="analyst-1")
    a2 = draft_service.create_ai_review_attempt(client, draft["report_draft_id"], requested_by="analyst-1")
    assert a1["review_attempt"] == 1
    assert a2["review_attempt"] == 2
    history = draft_service.list_ai_review_history(client, draft["report_draft_id"])
    assert [h["review_attempt"] for h in history] == [1, 2]
    assert draft_service.get_latest_ai_review(client, draft["report_draft_id"])["review_attempt"] == 2


def test_mark_ai_review_completed_assigns_finding_ids_and_open_status():
    client = FakeSupabaseClient()
    draft = _ai_generated_draft(client)
    _complete(client, draft["report_draft_id"])
    attempt = draft_service.create_ai_review_attempt(client, draft["report_draft_id"], requested_by="analyst-1")

    findings = [
        {"severity": "Critical", "finding": "Key actor missing", "category": "key_stakeholder"},
        {"severity": "Minor", "finding": "Freshness check", "category": "evidence"},
    ]
    completed = draft_service.mark_ai_review_completed(
        client, attempt["draft_ai_review_id"], findings=findings,
        expert_request_id="req-2", expert_response_id="resp-2")
    assert completed["status"] == "completed"
    assert [f["status"] for f in completed["findings"]] == ["open", "open"]
    assert completed["findings"][0]["finding_id"] and completed["findings"][1]["finding_id"]
    assert completed["findings"][0]["finding_id"] != completed["findings"][1]["finding_id"]


def test_waive_finding_requires_reason():
    client = FakeSupabaseClient()
    draft = _ai_generated_draft(client)
    _complete(client, draft["report_draft_id"])
    attempt = draft_service.create_ai_review_attempt(client, draft["report_draft_id"], requested_by="analyst-1")
    completed = draft_service.mark_ai_review_completed(
        client, attempt["draft_ai_review_id"],
        findings=[{"severity": "Critical", "finding": "X"}],
        expert_request_id="r", expert_response_id="r")
    finding_id = completed["findings"][0]["finding_id"]

    with pytest.raises(draft_service.ReportDraftError):
        draft_service.waive_finding(
            client, attempt["draft_ai_review_id"], finding_id=finding_id, waiver_reason="   ", waived_by="lead-1")

    waived = draft_service.waive_finding(
        client, attempt["draft_ai_review_id"], finding_id=finding_id,
        waiver_reason="Evidence範囲外のため採用しない", waived_by="lead-1")
    assert waived["findings"][0]["status"] == "waived"
    assert waived["findings"][0]["waiver_reason"] == "Evidence範囲外のため採用しない"
    assert waived["findings"][0]["waived_by"] == "lead-1"


# ---- Expert Review Gate（Backendで強制） --------------------------------------

def test_expert_review_gate_blocks_when_draft_not_completed():
    client = FakeSupabaseClient()
    _ai_generated_draft(client)  # pendingのまま
    with pytest.raises(draft_service.ExpertReviewGateError):
        draft_service.check_expert_review_gate(client, "session-1")


def test_expert_review_gate_blocks_when_no_ai_review_yet():
    client = FakeSupabaseClient()
    draft = _ai_generated_draft(client)
    _complete(client, draft["report_draft_id"])
    with pytest.raises(draft_service.ExpertReviewGateError):
        draft_service.check_expert_review_gate(client, "session-1")


def test_expert_review_gate_blocks_when_ai_review_pending():
    client = FakeSupabaseClient()
    draft = _ai_generated_draft(client)
    _complete(client, draft["report_draft_id"])
    draft_service.create_ai_review_attempt(client, draft["report_draft_id"], requested_by="analyst-1")
    with pytest.raises(draft_service.ExpertReviewGateError):
        draft_service.check_expert_review_gate(client, "session-1")


def test_expert_review_gate_blocks_when_ai_review_failed():
    client = FakeSupabaseClient()
    draft = _ai_generated_draft(client)
    _complete(client, draft["report_draft_id"])
    attempt = draft_service.create_ai_review_attempt(client, draft["report_draft_id"], requested_by="analyst-1")
    draft_service.mark_ai_review_failed(client, attempt["draft_ai_review_id"], error_message="timeout")
    with pytest.raises(draft_service.ExpertReviewGateError):
        draft_service.check_expert_review_gate(client, "session-1")


def test_expert_review_gate_blocks_when_open_critical_finding_remains():
    client = FakeSupabaseClient()
    draft = _ai_generated_draft(client)
    _complete(client, draft["report_draft_id"])
    attempt = draft_service.create_ai_review_attempt(client, draft["report_draft_id"], requested_by="analyst-1")
    draft_service.mark_ai_review_completed(
        client, attempt["draft_ai_review_id"], findings=[{"severity": "Critical", "finding": "X"}],
        expert_request_id="r", expert_response_id="r")
    with pytest.raises(draft_service.ExpertReviewGateError):
        draft_service.check_expert_review_gate(client, "session-1")


def test_expert_review_gate_passes_when_critical_is_waived():
    client = FakeSupabaseClient()
    draft = _ai_generated_draft(client)
    _complete(client, draft["report_draft_id"])
    attempt = draft_service.create_ai_review_attempt(client, draft["report_draft_id"], requested_by="analyst-1")
    completed = draft_service.mark_ai_review_completed(
        client, attempt["draft_ai_review_id"], findings=[{"severity": "Critical", "finding": "X"}],
        expert_request_id="r", expert_response_id="r")
    draft_service.waive_finding(
        client, attempt["draft_ai_review_id"], finding_id=completed["findings"][0]["finding_id"],
        waiver_reason="対象外", waived_by="lead-1")
    result = draft_service.check_expert_review_gate(client, "session-1")
    assert result["report_draft_id"] == draft["report_draft_id"]


def test_expert_review_gate_passes_with_only_minor_findings():
    client = FakeSupabaseClient()
    draft = _ai_generated_draft(client)
    _complete(client, draft["report_draft_id"])
    attempt = draft_service.create_ai_review_attempt(client, draft["report_draft_id"], requested_by="analyst-1")
    draft_service.mark_ai_review_completed(
        client, attempt["draft_ai_review_id"], findings=[{"severity": "Minor", "finding": "X"}],
        expert_request_id="r", expert_response_id="r")
    draft_service.check_expert_review_gate(client, "session-1")  # raiseしない


def test_build_native_review_snapshot_escapes_html():
    client = FakeSupabaseClient()
    draft = _ai_generated_draft(client)
    _complete(client, draft["report_draft_id"], sections=[
        {"heading": "<script>alert(1)</script>", "body": "本文<b>bold</b>", "supporting_evidence_ids": []},
    ])
    attempt = draft_service.create_ai_review_attempt(client, draft["report_draft_id"], requested_by="analyst-1")
    draft_service.mark_ai_review_completed(
        client, attempt["draft_ai_review_id"], findings=[], expert_request_id="r", expert_response_id="r")

    snapshot = draft_service.build_native_review_snapshot(client, "session-1")
    html = snapshot["version_snapshot"]["syntheses"][0]["final_html"]
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert snapshot["report_draft_id"] == draft["report_draft_id"]
    assert snapshot["content_hash"] == draft_service.get_draft(client, draft["report_draft_id"])["content_hash"]


# ---- Approval: 常にExpert ReviewでFreezeされた内容のみ ------------------------

def _freeze_review_cycle(client, draft_row, *, round_number, result):
    """geo_ios_review_service.request_review_cycleを模した最小限のInsert
    （review_serviceそのものへは依存しない、source_*列の整合性検証のみが目的）。"""
    row = {
        "report_project_id": "proj-1", "round_number": round_number, "reviewer": "reviewer-1",
        "requested_by": "analyst-1", "status": "completed", "result": result,
        "version_snapshot": {"syntheses": [{"final_html": "<p>ok</p>"}]},
        "reviewed_version_label": f"draft:v{draft_row['version']}",
        "source_report_draft_id": draft_row["report_draft_id"],
        "source_draft_version": draft_row["version"], "source_content_hash": draft_row["content_hash"],
    }
    return client.insert("geo_ios_review_cycles", [row])[0]


def test_get_approvable_snapshot_requires_review_for_this_exact_draft():
    client = FakeSupabaseClient()
    draft = _complete(client, _ai_generated_draft(client)["report_draft_id"])
    with pytest.raises(draft_service.ReportDraftError):
        draft_service.get_approvable_snapshot(client, "session-1")


def test_get_approvable_snapshot_rejects_when_latest_result_not_approved():
    client = FakeSupabaseClient()
    draft = _complete(client, _ai_generated_draft(client)["report_draft_id"])
    _freeze_review_cycle(client, draft, round_number=1, result="approved")
    _freeze_review_cycle(client, draft, round_number=2, result="revision_requested")
    # Review#1=approved, Review#2=revision_requested -> 最新(#2)を見るので拒否
    with pytest.raises(draft_service.ReportDraftError):
        draft_service.get_approvable_snapshot(client, "session-1")


def test_get_approvable_snapshot_approves_when_latest_result_is_approved():
    client = FakeSupabaseClient()
    draft = _complete(client, _ai_generated_draft(client)["report_draft_id"])
    _freeze_review_cycle(client, draft, round_number=1, result="revision_requested")
    _freeze_review_cycle(client, draft, round_number=2, result="approved")
    snapshot = draft_service.get_approvable_snapshot(client, "session-1")
    assert snapshot["report_draft_id"] == draft["report_draft_id"]
    assert snapshot["draft_version"] == draft["version"]


def test_new_draft_version_after_review_is_not_approvable_with_old_result():
    client = FakeSupabaseClient()
    v3 = _complete(client, _ai_generated_draft(client)["report_draft_id"])
    _freeze_review_cycle(client, v3, round_number=1, result="approved")
    # v3は承認可能
    assert draft_service.get_approvable_snapshot(client, "session-1")["draft_version"] == v3["version"]

    # v3のReview後にv4を作成（v3のReview結果はv4には適用されない）
    v4 = draft_service.create_draft_version(
        client, "session-1", source="human_edit", created_by="analyst-1",
        sections=[{"heading": "H", "body": "更新", "supporting_evidence_ids": []}],
        base_report_draft_id=v3["report_draft_id"])
    assert v4["version"] == v3["version"] + 1
    with pytest.raises(draft_service.ReportDraftError):
        draft_service.get_approvable_snapshot(client, "session-1")


# ---- run_document_import: アップロード文書のセクション分割（Expert AI Engine非経由）---

def _fake_outline_result(outline: list) -> dict:
    return {"data": {"outline": outline}, "mode": "structured_output",
            "usage": {"prompt_tokens": 500, "completion_tokens": 50}}


_DOC = "概要\nこれは概要の本文です。\n\n背景\n背景の本文です。二行目。\n\n結論\n結論の本文です。"


def test_run_document_import_slices_body_from_original_text_by_outline(monkeypatch):
    monkeypatch.setattr(
        geo_llm_common, "call_llm_structured",
        lambda *a, **kw: _fake_outline_result([
            {"heading": "概要", "start_text": "概要\nこれは概要"},
            {"heading": "背景", "start_text": "背景\n背景の本文"},
            {"heading": "結論", "start_text": "結論\n結論の本文"}]))

    sections = draft_service.run_document_import(object(), "gpt-test", raw_text=_DOC, theme="テーマ")

    assert [x["heading"] for x in sections] == ["概要", "背景", "結論"]
    # 本文は元テキストのまま（AIは本文を出力しない）。見出しの重複だけ取り除く
    assert sections[0]["body"] == "これは概要の本文です。"
    assert sections[1]["body"] == "背景の本文です。二行目。"
    assert sections[2]["body"] == "結論の本文です。"


def test_run_document_import_keeps_text_before_first_outline_item(monkeypatch):
    monkeypatch.setattr(
        geo_llm_common, "call_llm_structured",
        lambda *a, **kw: _fake_outline_result([{"heading": "背景", "start_text": "背景\n背景の本文"}]))
    sections = draft_service.run_document_import(object(), "gpt-test", raw_text=_DOC, theme="テーマ")
    assert sections[0]["heading"] == "冒頭"
    assert "これは概要の本文です。" in sections[0]["body"]


def test_run_document_import_falls_back_when_no_anchor_matches(monkeypatch):
    monkeypatch.setattr(
        geo_llm_common, "call_llm_structured",
        lambda *a, **kw: _fake_outline_result([{"heading": "存在しない", "start_text": "文書に無い文字列"}]))
    sections = draft_service.run_document_import(object(), "gpt-test", raw_text=_DOC, theme="テーマ")
    assert sections  # 空にせず、AIを使わない分割へ切り替える
    assert "".join(x["body"] for x in sections).replace("\n", "")


def test_run_document_import_falls_back_when_llm_fails(monkeypatch):
    def boom(*a, **kw):
        raise geo_llm_common.GeoLLMError("失敗", attempts=[], exception_type="X")
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", boom)
    sections = draft_service.run_document_import(object(), "gpt-test", raw_text=_DOC, theme="テーマ")
    assert sections


def test_run_document_import_skips_llm_for_very_long_text(monkeypatch):
    def must_not_call(*a, **kw):
        raise AssertionError("長すぎる本文ではAIを呼ばない")
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", must_not_call)
    long_text = "\n\n".join(f"段落{i}。" + "あ" * 400 for i in range(400))  # 約16万文字
    sections = draft_service.run_document_import(object(), "gpt-test", raw_text=long_text, theme="テーマ")
    assert len(sections) > 1
    assert all(len(x["body"]) <= 20000 for x in sections)


def test_run_document_import_passes_raw_text_and_theme_into_prompt(monkeypatch):
    captured = {}

    def fake_call(client_, model_, system_prompt, user_prompt, schema, schema_name, **kw):
        captured["user_prompt"] = user_prompt
        return _fake_outline_result([{"heading": "H", "start_text": "固有の"}])
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake_call)

    draft_service.run_document_import(
        object(), "gpt-test", raw_text="固有の文書テキストXYZ", theme="固有テーマABC")

    assert "固有の文書テキストXYZ" in captured["user_prompt"]
    assert "固有テーマABC" in captured["user_prompt"]


def test_split_text_without_ai_splits_on_heading_lines():
    body1, body2 = "背景の本文。" * 300, "結論の本文。" * 300  # 小さい章は統合されるため十分な長さにする
    text = f"第1章 背景\n{body1}\n第2章 結論\n{body2}"
    sections = draft_service.split_text_without_ai(text)
    assert [x["heading"] for x in sections] == ["第1章 背景", "第2章 結論"]
    assert sections[0]["body"] == body1


def test_split_text_without_ai_merges_tiny_sections():
    text = "\n".join(f"第{i}章 見出し\n短い本文{i}。" for i in range(1, 30))
    sections = draft_service.split_text_without_ai(text)
    assert len(sections) < 10  # 章が何十個にも増えない


def test_split_text_without_ai_handles_text_with_only_single_newlines():
    text = "\n".join("行です。" * 300 for _ in range(30))  # 見出しも空行も無い長文
    sections = draft_service.split_text_without_ai(text)
    assert len(sections) > 1
    assert all(len(x["body"]) <= 20000 for x in sections)


def test_validate_import_file_messages():
    assert draft_service.validate_import_file("a.pdf", 1000) is None
    assert draft_service.validate_import_file("a.docx", 1000) is None
    assert "対応していない" in draft_service.validate_import_file("a.xlsx", 1000)
    assert "古いWord" in draft_service.validate_import_file("a.doc", 1000)
    assert "空" in draft_service.validate_import_file("a.pdf", 0)
    assert "大きすぎ" in draft_service.validate_import_file("a.pdf", draft_service.IMPORT_MAX_FILE_BYTES + 1)


def test_validate_import_text_messages():
    assert draft_service.validate_import_text("本文") is None
    assert "スキャン" in draft_service.validate_import_text("  ")
    assert "長すぎ" in draft_service.validate_import_text("あ" * (draft_service.IMPORT_MAX_TEXT_CHARS + 1))


# ---- 'uploaded' source: DRAFT_SOURCESに含まれ、ai_generatedと同じくpendingで作成される --

def test_uploaded_source_is_valid_and_creates_pending_draft():
    client = FakeSupabaseClient()
    draft = draft_service.create_draft_version(
        client, "session-1", source="uploaded", created_by="analyst-1")
    assert draft["status"] == "pending"
    assert draft["sections"] is None
