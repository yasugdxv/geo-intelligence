"""Geopolitical Intelligence OS: 前提整理（Intake）Chat + Analysis Context + Session
Attachmentのテスト（ユーザー指示、2026-09-24、「1 設計」ページのIntake画面改修）。

既存test_geo_ios_learning_extraction_service.py等と同じ流儀
（FakeSupabaseClient直呼び、geo_llm_common.call_llm_structuredをmonkeypatchで
差し替え、TestClient等の新しい依存は増やさない）。
"""
import asyncio

import geo_ios_intake_api as intake_api
import geo_ios_intake_service as intake_service
import geo_llm_common
from tests._fakes import FakeSupabaseClient


def _fake_llm_result(data: dict) -> dict:
    return {"data": data, "mode": "structured_output",
            "usage": {"prompt_tokens": 100, "completion_tokens": 50}}


def _seed_session(client) -> dict:
    return client.insert("geo_ios_analysis_sessions", [{
        "title": "米国の対中半導体規制", "theme": "議会は強硬だが企業は緩和を求めている",
        "user_id": "analyst-1", "status": "active",
    }])[0]


# ---- Analysis Context（version-append） ---------------------------------------

def test_create_context_version_first_version_only_sets_given_fields():
    client = FakeSupabaseClient()
    session = _seed_session(client)
    ctx = intake_service.create_context_version(
        client, session["analysis_session_id"],
        fields={"analysis_objective": "対中半導体規制の見通し", "relevant_actors": ["議会", "半導体企業"]},
        ready_for_handoff=False, change_note="turn1", created_by="analyst-1")
    assert ctx["version"] == 1
    assert ctx["analysis_objective"] == "対中半導体規制の見通し"
    assert ctx["relevant_actors"] == ["議会", "半導体企業"]
    # version 1では指定しなかったfieldは行に含まれない（既存create_design_versionと同じ
    # 挙動——実DBではjsonb/text列のcolumn defaultがNULL/[]を埋める。FakeSupabaseClientは
    # DB column defaultを再現しないため、ここでは「キー自体が存在しないか、Noneである」ことのみ確認する）。
    assert ctx.get("core_question") is None


def test_create_context_version_carries_forward_unspecified_fields():
    """ユーザーが答えていない項目を空へ巻き戻さない（既存create_design_versionと同じ方針）。"""
    client = FakeSupabaseClient()
    session = _seed_session(client)
    intake_service.create_context_version(
        client, session["analysis_session_id"], fields={"analysis_objective": "obj1", "time_horizon": "6ヶ月"},
        ready_for_handoff=False, change_note="turn1", created_by="a")
    v2 = intake_service.create_context_version(
        client, session["analysis_session_id"], fields={"core_question": "規制はどちらへ動くか"},
        ready_for_handoff=False, change_note="turn2", created_by="a")
    assert v2["version"] == 2
    assert v2["analysis_objective"] == "obj1"  # 前versionから引き継ぎ
    assert v2["time_horizon"] == "6ヶ月"  # 前versionから引き継ぎ
    assert v2["core_question"] == "規制はどちらへ動くか"  # 新規追加


def test_create_context_version_key_actors_detail_carries_forward():
    """改善①（ユーザー指示、2026-09-24、Step2品質検証結果を受けた対応）:
    relevant_actors（機関レベル）とは別に、個人名・役職・レバレッジ・関係性・制約の
    オブジェクト配列key_actors_detailを持てること、既存フィールドと同じく
    version追記で引き継がれることを確認する。"""
    client = FakeSupabaseClient()
    session = _seed_session(client)
    v1 = intake_service.create_context_version(
        client, session["analysis_session_id"],
        fields={"key_actors_detail": [
            {"name": "ガリバフ", "role": "イラン国会議長", "controls_or_leverage": "IRGC〜左派までの交渉窓口",
             "key_relationships": "米・イスラエルとの交渉", "constraints": "体制内の複数派閥をまとめる必要"},
        ]},
        ready_for_handoff=False, change_note="turn1", created_by="a")
    assert v1["key_actors_detail"][0]["name"] == "ガリバフ"
    assert v1["key_actors_detail"][0]["role"] == "イラン国会議長"

    v2 = intake_service.create_context_version(
        client, session["analysis_session_id"], fields={"core_question": "今後の展開"},
        ready_for_handoff=False, change_note="turn2", created_by="a")
    assert v2["key_actors_detail"][0]["name"] == "ガリバフ"  # 引き継がれる


def test_create_context_version_key_actors_detail_notable_actions_carries_forward():
    """改善（ユーザー指示、2026-09-24c）: key_actors_detail.notable_actionsは、
    Attachment structured_contextから運ばれてきた「具体的な行動・発言・反応」を、
    既存フィールドと同じくversion追記で欠落させずに保持できることを確認する。"""
    client = FakeSupabaseClient()
    session = _seed_session(client)
    v1 = intake_service.create_context_version(
        client, session["analysis_session_id"],
        fields={"key_actors_detail": [
            {"name": "トランプ大統領", "role": "米国大統領",
             "notable_actions": "マドゥロ大統領から嘲笑的な発言を受けた後、激怒しベネズエラ攻撃に踏み切った。"},
        ]},
        ready_for_handoff=False, change_note="turn1", created_by="a")
    assert v1["key_actors_detail"][0]["notable_actions"] == \
        "マドゥロ大統領から嘲笑的な発言を受けた後、激怒しベネズエラ攻撃に踏み切った。"

    v2 = intake_service.create_context_version(
        client, session["analysis_session_id"], fields={"core_question": "今後の展開"},
        ready_for_handoff=False, change_note="turn2", created_by="a")
    assert v2["key_actors_detail"][0]["notable_actions"] == \
        "マドゥロ大統領から嘲笑的な発言を受けた後、激怒しベネズエラ攻撃に踏み切った。"  # 引き継がれる


def test_send_intake_message_extracts_notable_actions_from_llm_response(monkeypatch):
    """LLMがcontext_updates.key_actors_detail経由でnotable_actionsを返した場合、
    Analysis Contextへ正しく反映されることを確認する（Attachment structured_contextから
    Analysis Contextへの引き継ぎ経路のうち、Intake Turn側の受け口を検証する）。"""
    client = FakeSupabaseClient()
    session = _seed_session(client)
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", lambda *a, **kw: _fake_llm_result({
        "reply_message": "ハメネイ師の言動がトランプ大統領を苛立たせる可能性がある点も記録しました。",
        "context_updates": {
            "key_actors_detail": [
                {"name": "ハメネイ師", "role": "イラン最高指導者",
                 "notable_actions": "最近の言動がトランプを苛立たせ、事態を急変させる可能性があるとされる。"},
            ],
        },
        "ready_for_handoff": False,
    }))
    result = intake_service.send_intake_message(
        client, object(), "gpt-test", session["analysis_session_id"],
        user_message="資料を読み込んで重要な人物の行動を教えて", created_by="analyst-1")
    detail = result["context"]["key_actors_detail"]
    assert detail[0]["notable_actions"] == "最近の言動がトランプを苛立たせ、事態を急変させる可能性があるとされる。"


def test_create_context_version_rejects_unknown_field():
    client = FakeSupabaseClient()
    session = _seed_session(client)
    try:
        intake_service.create_context_version(
            client, session["analysis_session_id"], fields={"not_a_real_field": "x"},
            ready_for_handoff=False, change_note="n", created_by="a")
        assert False, "should have raised"
    except intake_service.IntakeError:
        pass


def test_get_current_context_returns_latest_version():
    client = FakeSupabaseClient()
    session = _seed_session(client)
    intake_service.create_context_version(
        client, session["analysis_session_id"], fields={"analysis_objective": "v1"},
        ready_for_handoff=False, change_note="n", created_by="a")
    intake_service.create_context_version(
        client, session["analysis_session_id"], fields={"analysis_objective": "v2"},
        ready_for_handoff=True, change_note="n", created_by="a")
    current = intake_service.get_current_context(client, session["analysis_session_id"])
    assert current["version"] == 2
    assert current["analysis_objective"] == "v2"
    assert current["ready_for_handoff"] is True


# ---- Session Attachment --------------------------------------------------------

def test_add_attachment_text_file_extracts_text(tmp_path, monkeypatch):
    monkeypatch.setattr(intake_service, "INTAKE_UPLOAD_ROOT", tmp_path)
    client = FakeSupabaseClient()
    session = _seed_session(client)
    attachment = intake_service.add_attachment(
        client, session["analysis_session_id"], filename="memo.txt", mime_type="text/plain",
        raw_bytes="規制強化のリスクが高い".encode("utf-8"), uploaded_by="analyst-1")
    assert attachment["extraction_status"] == "ok"
    assert "規制強化" in attachment["extracted_text"]
    assert attachment["filename"] == "memo.txt"


def test_add_attachment_pptx_is_unsupported_but_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(intake_service, "INTAKE_UPLOAD_ROOT", tmp_path)
    client = FakeSupabaseClient()
    session = _seed_session(client)
    attachment = intake_service.add_attachment(
        client, session["analysis_session_id"], filename="slides.pptx",
        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        raw_bytes=b"\x00\x01fakepptxbytes", uploaded_by="analyst-1")
    assert attachment["extraction_status"] == "unsupported"
    assert attachment["extracted_text"] is None


def test_chunk_attachment_text_short_text_is_single_chunk():
    text = "短い本文です。"
    assert intake_service._chunk_attachment_text(text) == [text]


def test_chunk_attachment_text_splits_long_text_at_newline_boundary():
    """改善（ユーザー指示、2026-09-24b、Hungary Blind Testで発覚した2000文字打ち切りへの対応）:
    chunk_charsを超える本文は、改行位置で区切って分割される（文の途中で切れる頻度を下げる）。"""
    text = ("段落1の内容です。" + "あ" * 20 + "\n") * 5
    chunks = intake_service._chunk_attachment_text(text, chunk_chars=50)
    assert len(chunks) > 1
    assert "".join(chunks) == text  # 分割しても情報は失われない（結合すれば原文と一致）


def test_generate_attachment_structured_context_merges_across_chunks(tmp_path, monkeypatch):
    """本文がchunk_charsを超える場合、断片ごとの抽出結果を統合Callで1つにまとめ、
    structured_contextへ保存することを確認する。"""
    monkeypatch.setattr(intake_service, "INTAKE_UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(intake_service, "_ATTACHMENT_CHUNK_CHARS", 30)
    client = FakeSupabaseClient()
    session = _seed_session(client)
    attachment = intake_service.add_attachment(
        client, session["analysis_session_id"], filename="report.txt", mime_type="text/plain",
        raw_bytes=("前半：オルバン首相が敗北した。\n後半：プーチン大統領は同盟国を失った。").encode("utf-8"),
        uploaded_by="analyst-1")

    calls = []

    def fake_call(*a, **kw):
        calls.append(a[5])  # schema_name
        if a[5] == intake_service._ATTACHMENT_STRUCTURED_CONTEXT_SCHEMA_NAME and len(calls) <= 2:
            # 断片ごとの抽出呼び出し（1回目=前半、2回目=後半）
            if "オルバン" in a[3]:
                return _fake_llm_result({
                    "key_actors": [{"name": "オルバン", "role": "首相", "controls_or_leverage": "",
                                    "key_relationships": "", "constraints": "", "notable_actions": "敗北"}],
                    "relevant_facts_or_events": ["オルバン首相が敗北した"],
                })
            return _fake_llm_result({
                "key_actors": [{"name": "プーチン", "role": "大統領", "controls_or_leverage": "",
                                "key_relationships": "", "constraints": "", "notable_actions": "同盟国を失った"}],
                "relevant_facts_or_events": ["プーチン大統領が同盟国を失った"],
            })
        # 統合Call
        return _fake_llm_result({
            "key_actors": [
                {"name": "オルバン", "role": "首相", "controls_or_leverage": "", "key_relationships": "",
                 "constraints": "", "notable_actions": "敗北"},
                {"name": "プーチン", "role": "大統領", "controls_or_leverage": "", "key_relationships": "",
                 "constraints": "", "notable_actions": "同盟国を失った"},
            ],
            "relevant_facts_or_events": ["オルバン首相が敗北した", "プーチン大統領が同盟国を失った"],
        })

    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake_call)
    result = intake_service.generate_attachment_structured_context(
        client, object(), "gpt-test", attachment["attachment_id"])

    assert len(calls) == 3  # 断片2回 + 統合1回
    names = {a["name"] for a in result["structured_context"]["key_actors"]}
    assert names == {"オルバン", "プーチン"}  # 後半に登場するプーチンも欠落しない


def test_generate_attachment_structured_context_skips_unsupported_extraction(tmp_path, monkeypatch):
    monkeypatch.setattr(intake_service, "INTAKE_UPLOAD_ROOT", tmp_path)
    client = FakeSupabaseClient()
    session = _seed_session(client)
    attachment = intake_service.add_attachment(
        client, session["analysis_session_id"], filename="slides.pptx",
        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        raw_bytes=b"\x00\x01fakepptxbytes", uploaded_by="analyst-1")

    def fail_if_called(*a, **kw):
        raise AssertionError("unsupported添付ではLLMを呼び出すべきではない")

    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fail_if_called)
    result = intake_service.generate_attachment_structured_context(
        client, object(), "gpt-test", attachment["attachment_id"])
    assert result.get("structured_context") is None


def test_format_attachments_for_prompt_prefers_structured_context_over_truncated_text():
    """改善（ユーザー指示、2026-09-24b）: structured_contextがある場合は、たとえ
    extracted_textが2000文字を超えていても、先頭打ち切りではなく構造化要約（後半の
    人物も含む）を使うことを確認する。"""
    attachment = {
        "filename": "report.txt",
        "extraction_status": "ok",
        "extracted_text": "あ" * 3000,
        "structured_context": {
            "key_actors": [{"name": "プーチン", "role": "大統領", "notable_actions": "同盟国を失った"}],
            "relevant_facts_or_events": ["後半にしか出てこない事実"],
        },
    }
    formatted = intake_service._format_attachments_for_prompt([attachment])
    assert "プーチン" in formatted
    assert "後半にしか出てこない事実" in formatted
    assert "あ" * 100 not in formatted  # 打ち切りfallback（先頭抜粋）は使われていない


def test_format_attachments_for_prompt_falls_back_to_truncated_text_without_structured_context():
    attachment = {"filename": "report.txt", "extraction_status": "ok", "extracted_text": "本文" * 1500}
    formatted = intake_service._format_attachments_for_prompt([attachment])
    assert "構造化要約未生成" in formatted
    assert "本文" in formatted


def test_list_attachments_returns_session_scoped_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(intake_service, "INTAKE_UPLOAD_ROOT", tmp_path)
    client = FakeSupabaseClient()
    session1 = _seed_session(client)
    session2 = client.insert("geo_ios_analysis_sessions", [{
        "title": "別件", "theme": "t", "user_id": "u", "status": "active"}])[0]
    intake_service.add_attachment(
        client, session1["analysis_session_id"], filename="a.txt", mime_type="text/plain",
        raw_bytes=b"text a", uploaded_by="u")
    intake_service.add_attachment(
        client, session2["analysis_session_id"], filename="b.txt", mime_type="text/plain",
        raw_bytes=b"text b", uploaded_by="u")
    attachments = intake_service.list_attachments(client, session1["analysis_session_id"])
    assert len(attachments) == 1
    assert attachments[0]["filename"] == "a.txt"


# ---- Intake Turn（LLM呼び出しはmonkeypatchで差し替え） --------------------------

def test_send_intake_message_creates_turn_and_context_version(monkeypatch):
    client = FakeSupabaseClient()
    session = _seed_session(client)
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", lambda *a, **kw: _fake_llm_result({
        "reply_message": "特に想定している時間軸はありますか？",
        "context_updates": {"analysis_objective": "対中半導体規制の見通し", "relevant_actors": ["議会", "半導体企業"]},
        "ready_for_handoff": False,
    }))
    result = intake_service.send_intake_message(
        client, azure_client=object(), model="gpt-test", analysis_session_id=session["analysis_session_id"],
        user_message="米国の対中半導体規制が今後どうなるか見たい。議会は強硬だが企業は緩和を求めている。",
        created_by="analyst-1")
    assert result["turn"]["assistant_message"] == "特に想定している時間軸はありますか？"
    assert result["turn"]["trigger"] == "user_message"
    assert result["turn"]["resulting_context_version"] == 1
    assert result["context"]["analysis_objective"] == "対中半導体規制の見通し"

    turns = intake_service.list_turns(client, session["analysis_session_id"])
    assert len(turns) == 1


def test_send_intake_message_second_turn_merges_onto_first(monkeypatch):
    client = FakeSupabaseClient()
    session = _seed_session(client)
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", lambda *a, **kw: _fake_llm_result({
        "reply_message": "承知しました。", "context_updates": {"time_horizon": "半年くらい"},
        "ready_for_handoff": False,
    }))
    intake_service.send_intake_message(
        client, object(), "gpt-test", session["analysis_session_id"],
        user_message="半年くらい", created_by="analyst-1")

    monkeypatch.setattr(geo_llm_common, "call_llm_structured", lambda *a, **kw: _fake_llm_result({
        "reply_message": "今回の分析結果は、どのような判断や業務で使う予定ですか？",
        "context_updates": {"business_context": "事業リスクの先読み"}, "ready_for_handoff": False,
    }))
    result = intake_service.send_intake_message(
        client, object(), "gpt-test", session["analysis_session_id"],
        user_message="事業リスクの先読みに使う", created_by="analyst-1")
    context = result["context"]
    assert context["version"] == 2
    assert context["time_horizon"] == "半年くらい"  # 1ターン目から引き継ぎ
    assert context["business_context"] == "事業リスクの先読み"  # 今回追加


def test_send_intake_message_extracts_key_actors_detail_from_llm_response(monkeypatch):
    """改善①（ユーザー指示、2026-09-24）: LLMが資料本文から個人名・役職・レバレッジ・
    関係性・制約を抽出した場合、context_updates.key_actors_detail経由でAnalysis Contextへ
    正しく反映されることを確認する（担当者への手入力要求ではなくAI抽出であることの
    end-to-end確認）。"""
    client = FakeSupabaseClient()
    session = _seed_session(client)
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", lambda *a, **kw: _fake_llm_result({
        "reply_message": "この資料からガリバフ議長が主要な交渉窓口であることが分かりました。",
        "context_updates": {
            "relevant_actors": ["イラン政府", "米国政府"],
            "key_actors_detail": [
                {"name": "ガリバフ", "role": "イラン国会議長",
                 "controls_or_leverage": "IRGC〜左派テクノクラートまでを束ねる交渉窓口",
                 "key_relationships": "米・イスラエルとの交渉", "constraints": "体制内派閥の合意形成"},
            ],
        },
        "ready_for_handoff": False,
    }))
    result = intake_service.send_intake_message(
        client, object(), "gpt-test", session["analysis_session_id"],
        user_message="資料を読み込んで重要な人物を教えて", created_by="analyst-1")
    detail = result["context"]["key_actors_detail"]
    assert len(detail) == 1
    assert detail[0]["name"] == "ガリバフ"
    assert detail[0]["controls_or_leverage"] == "IRGC〜左派テクノクラートまでを束ねる交渉窓口"


def test_send_intake_message_empty_message_rejected(monkeypatch):
    client = FakeSupabaseClient()
    session = _seed_session(client)
    try:
        intake_service.send_intake_message(
            client, object(), "gpt-test", session["analysis_session_id"], user_message="   ", created_by="a")
        assert False, "should have raised"
    except intake_service.IntakeError:
        pass


def test_send_attachment_added_turn_uses_synthetic_message_and_records_attachment_id(tmp_path, monkeypatch):
    monkeypatch.setattr(intake_service, "INTAKE_UPLOAD_ROOT", tmp_path)
    client = FakeSupabaseClient()
    session = _seed_session(client)
    attachment = intake_service.add_attachment(
        client, session["analysis_session_id"], filename="report.pdf", mime_type="application/pdf",
        raw_bytes=b"dummy", uploaded_by="analyst-1")
    # add_attachmentがdummy(非PDF実体)を保存しているためextraction自体は失敗しうるが、
    # send_attachment_added_turn自体の配線（synthetic message/attachment_ids記録）を検証する
    # のが目的なので、抽出成否には依存しない。
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", lambda *a, **kw: _fake_llm_result({
        "reply_message": "この資料を見ると輸出規制の条件が重要そうです。分析対象に含めますか？",
        "context_updates": {"reference_materials": ["report.pdf: 輸出規制の条件"]}, "ready_for_handoff": False,
    }))
    result = intake_service.send_attachment_added_turn(
        client, object(), "gpt-test", session["analysis_session_id"], attachment=attachment, created_by="analyst-1")
    assert result["turn"]["trigger"] == "attachment_added"
    assert "report.pdf" in result["turn"]["user_message"]
    assert result["turn"]["attachment_ids"] == [attachment["attachment_id"]]
    assert result["context"]["reference_materials"] == ["report.pdf: 輸出規制の条件"]


def test_run_intake_turn_with_no_context_updates_still_creates_context_version_for_ready_flag(monkeypatch):
    """context_updatesが空でも、ready_for_handoffの変化はversionとして記録する
    （UIの「準備状況」表示が最新版を見るだけで済むようにするため）。"""
    client = FakeSupabaseClient()
    session = _seed_session(client)
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", lambda *a, **kw: _fake_llm_result({
        "reply_message": "分析に必要な前提は概ね揃いました。この内容で専門家AIに読み方を提案させます。",
        "context_updates": {}, "ready_for_handoff": True,
    }))
    result = intake_service.send_intake_message(
        client, object(), "gpt-test", session["analysis_session_id"], user_message="以上で大丈夫です",
        created_by="analyst-1")
    assert result["context"]["ready_for_handoff"] is True


# ---- API層（JSONエンドポイントのみ。multipart uploadはservice層のテストで代替） -----

def test_api_get_current_context_404_when_not_yet_created(monkeypatch):
    client = FakeSupabaseClient()
    session = _seed_session(client)
    monkeypatch.setattr(intake_api, "_client", client)
    try:
        intake_api.get_current_context(session["analysis_session_id"])
        assert False, "should have raised 404"
    except Exception as e:  # noqa: BLE001 — HTTPExceptionのstatus_codeのみ確認
        assert getattr(e, "status_code", None) == 404


def test_api_send_intake_message_returns_turn_and_context(monkeypatch):
    client = FakeSupabaseClient()
    session = _seed_session(client)
    monkeypatch.setattr(intake_api, "_client", client)
    monkeypatch.setattr(intake_api, "_azure_client", object())
    monkeypatch.setattr(intake_api, "_model", "gpt-test")
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", lambda *a, **kw: _fake_llm_result({
        "reply_message": "承知しました。", "context_updates": {"analysis_objective": "obj"},
        "ready_for_handoff": False,
    }))
    req = intake_api.SendIntakeMessageRequest(user_message="分析したいことがあります", actor_id="analyst-1")
    result = intake_api.send_intake_message(session["analysis_session_id"], req)
    assert result["turn"]["assistant_message"] == "承知しました。"
    assert result["context"]["analysis_objective"] == "obj"


class _FakeUploadFile:
    def __init__(self, content: bytes, filename: str, content_type: str):
        self._content = content
        self.filename = filename
        self.content_type = content_type

    async def read(self):
        return self._content


def test_api_upload_attachment_returns_flat_turn_and_context(tmp_path, monkeypatch):
    """アップロード直後の自動Turn結果は{"turn": ..., "context": ...}の2重ネストではなく、
    送信時と同じ形（トップレベルにturn/contextを持つ）で返す（回帰: 2026-09-24に発見・修正、
    以前はresponse["turn"]が実際には{"turn":..., "context":...}という中身だった）。"""
    monkeypatch.setattr(intake_service, "INTAKE_UPLOAD_ROOT", tmp_path)
    client = FakeSupabaseClient()
    session = _seed_session(client)
    monkeypatch.setattr(intake_api, "_client", client)
    monkeypatch.setattr(intake_api, "_azure_client", object())
    monkeypatch.setattr(intake_api, "_model", "gpt-test")
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", lambda *a, **kw: _fake_llm_result({
        "reply_message": "この資料を見ると規制緩和のロビー活動が重要そうです。含めますか？",
        "context_updates": {"reference_materials": ["memo.txt: ロビー活動の詳細"]},
        "ready_for_handoff": False,
    }))
    result = asyncio.run(intake_api.upload_session_attachment(
        session["analysis_session_id"], actor_id="analyst-1",
        file=_FakeUploadFile(b"memo body", "memo.txt", "text/plain")))
    assert result["attachment"]["filename"] == "memo.txt"
    assert result["turn"]["trigger"] == "attachment_added"
    assert result["turn"]["assistant_message"].startswith("この資料を見ると")
    assert result["context"]["reference_materials"] == ["memo.txt: ロビー活動の詳細"]


def test_api_upload_attachment_generates_structured_context_before_turn(tmp_path, monkeypatch):
    """改善（ユーザー指示、2026-09-24b）: アップロード直後のTurn発生より前に、構造化要約が
    生成されていること（＝Turn自体が2000文字打ち切りではなく構造化要約を見て応答すること）
    をAPI層のend-to-endで確認する。"""
    monkeypatch.setattr(intake_service, "INTAKE_UPLOAD_ROOT", tmp_path)
    client = FakeSupabaseClient()
    session = _seed_session(client)
    monkeypatch.setattr(intake_api, "_client", client)
    monkeypatch.setattr(intake_api, "_azure_client", object())
    monkeypatch.setattr(intake_api, "_model", "gpt-test")

    def fake_call(*a, **kw):
        schema_name = a[5]
        if schema_name == intake_service._ATTACHMENT_STRUCTURED_CONTEXT_SCHEMA_NAME:
            return _fake_llm_result({
                "key_actors": [{"name": "ガリバフ", "role": "イラン国会議長"}],
                "relevant_facts_or_events": ["交渉窓口になっている"],
            })
        return _fake_llm_result({
            "reply_message": "承知しました。", "context_updates": {}, "ready_for_handoff": False,
        })

    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake_call)
    result = asyncio.run(intake_api.upload_session_attachment(
        session["analysis_session_id"], actor_id="analyst-1",
        file=_FakeUploadFile(b"memo body", "memo.txt", "text/plain")))
    assert result["attachment"]["structured_context"]["key_actors"][0]["name"] == "ガリバフ"


def test_api_upload_attachment_survives_structured_context_llm_failure(tmp_path, monkeypatch):
    """構造化要約の生成に失敗しても、既に保存済みの添付資料・その後のTurn発生自体は
    失われない（既存のsend_attachment_added_turn失敗時と同じ「壊れても既存の成果を
    失わない」方針）。"""
    monkeypatch.setattr(intake_service, "INTAKE_UPLOAD_ROOT", tmp_path)
    client = FakeSupabaseClient()
    session = _seed_session(client)
    monkeypatch.setattr(intake_api, "_client", client)
    monkeypatch.setattr(intake_api, "_azure_client", object())
    monkeypatch.setattr(intake_api, "_model", "gpt-test")

    def fake_call(*a, **kw):
        schema_name = a[5]
        if schema_name == intake_service._ATTACHMENT_STRUCTURED_CONTEXT_SCHEMA_NAME:
            raise geo_llm_common.GeoLLMError("Azure呼び出し失敗（テスト）")
        return _fake_llm_result({
            "reply_message": "承知しました。", "context_updates": {}, "ready_for_handoff": False,
        })

    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake_call)
    result = asyncio.run(intake_api.upload_session_attachment(
        session["analysis_session_id"], actor_id="analyst-1",
        file=_FakeUploadFile(b"memo body", "memo.txt", "text/plain")))
    assert result["attachment"]["filename"] == "memo.txt"
    assert result["attachment"].get("structured_context") is None
    assert result["turn"]["assistant_message"] == "承知しました。"


def test_api_upload_attachment_without_azure_client_skips_turn_but_saves_file(tmp_path, monkeypatch):
    monkeypatch.setattr(intake_service, "INTAKE_UPLOAD_ROOT", tmp_path)
    client = FakeSupabaseClient()
    session = _seed_session(client)
    monkeypatch.setattr(intake_api, "_client", client)
    monkeypatch.setattr(intake_api, "_azure_client", None)
    result = asyncio.run(intake_api.upload_session_attachment(
        session["analysis_session_id"], actor_id="analyst-1",
        file=_FakeUploadFile(b"memo body", "memo.txt", "text/plain")))
    assert result["attachment"]["filename"] == "memo.txt"
    assert result["turn"] is None
    assert result["context"] is None


def test_api_send_intake_message_without_azure_client_returns_503(monkeypatch):
    client = FakeSupabaseClient()
    session = _seed_session(client)
    monkeypatch.setattr(intake_api, "_client", client)
    monkeypatch.setattr(intake_api, "_azure_client", None)
    req = intake_api.SendIntakeMessageRequest(user_message="hello", actor_id="analyst-1")
    try:
        intake_api.send_intake_message(session["analysis_session_id"], req)
        assert False, "should have raised 503"
    except Exception as e:  # noqa: BLE001
        assert getattr(e, "status_code", None) == 503
