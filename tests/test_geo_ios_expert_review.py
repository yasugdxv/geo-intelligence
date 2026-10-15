"""Expert Review（読み方UI改善、2026-10-04）のRegression Test。

- 決定論的View（geo_ios_expert_review_view）: 視点ラベル・Stakeholder Tag・内部用語の除去・
  既存presentationキーを壊さないこと・証拠の意味づけを捏造しないこと
- Presentation LLM Transformation（geo_ios_reading_presentation_transform_service）: 出力検証
  （新Actor/数値/論文調/メタ書き出し/長さ）・キャッシュ（同じ入力で再生成しない、GETではLLMを
  呼ばない）・失敗時Fallback
- API: GETはLLMを呼ばない／POSTで一度だけ生成／失敗しても既存presentationを壊さない
実LLMは一切呼ばない（fakeを注入）。cacheはtmp_pathへ向ける（tests/conftest.py）。
"""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_expert_review_view as view  # noqa: E402
import geo_ios_reading_presentation_transform_service as transform  # noqa: E402
import geo_ios_situation_presentation_api as presentation_api  # noqa: E402
import geo_llm_common  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402
from tests.test_geo_ios_situation_presentation_api import (  # noqa: E402
    _minimal_response_json, _seed_session_with_candidates)


# ---- 決定論的View -------------------------------------------------------------------

def test_clean_text_removes_internal_terms_without_rewriting_meaning():
    assert view.clean_text("as_of=2026-09-03。米国の動きが鍵になる。") == "米国の動きが鍵になる。"
    assert "as_of" not in view.clean_text("as_of 2026-09-03、提供資料の範囲では、台湾政府は動いている。")
    assert view.clean_text("as_of 2026-09-03、提供資料の範囲では、台湾政府は動いている。") == "台湾政府は動いている。"
    cleaned = view.clean_text("hypothesis_registerのH0（全面封鎖ではない）は有力。Evidence Fit: 資料が支える。")
    assert "hypothesis_register" not in cleaned and "H0" not in cleaned and "Evidence Fit" not in cleaned
    assert "全面封鎖ではない" in cleaned and "資料が支える" in cleaned
    assert "11111111" not in view.clean_text("根拠 [document_id: 11111111-2222-3333-4444-555555555555] です")


def test_clean_text_keeps_product_names_like_h200_but_removes_hypothesis_ids():
    assert view.clean_text("H200級を条件付きで認める。") == "H200級を条件付きで認める。"
    assert view.clean_text("H20とH200の扱い") == "H20とH200の扱い"
    assert "H0" not in view.clean_text("H0仮説どおりの線引き") and "H1" not in view.clean_text("H1が有力")


def test_fallback_headline_drops_scenario_label_prefix():
    card = _card(title=None, description={"value": "代替シナリオ1：核拡散懸念が強まりパッケージは縮小される。"})
    headline, comment = view.fallback_headline_and_comment(card)
    assert not headline.startswith("代替シナリオ") and "核拡散懸念" in headline


def test_has_forbidden_term_detects_internal_vocabulary():
    assert view.has_forbidden_term("H0 の話")
    assert not view.has_forbidden_term("H200級の扱い")  # 製品名は内部用語ではない
    assert view.has_forbidden_term("hypothesis_register を見る")
    assert view.has_forbidden_term("Primary Reading が")
    assert not view.has_forbidden_term("イラン政権は封鎖カードを温存している。")


def test_short_actor_name_drops_parenthetical_notes():
    assert view.short_actor_name("イラン政権（最高指導者＋IRGCを含む統合主体として暫定扱い）") == "イラン政権"
    assert view.short_actor_name("Mohammed bin Salman") == "Mohammed bin Salman"
    assert view.short_actor_name(None) == ""


def test_first_sentences_truncates_long_text_at_a_comma_with_ellipsis():
    text = "あ" * 40 + "、" + "い" * 100 + "。次の文。"
    out = view.first_sentences(text, 1, 60)
    assert out.endswith("…") and len(out) <= 62
    assert view.first_sentences("一文目。二文目。三文目。", 2, 100) == "一文目。二文目。"


def _card(**overrides):
    card = {
        "is_main": False, "reading_role": "ALTERNATIVE", "title": "元のタイトル",
        "description": {"value": "代替の説明。もう一文。"},
        "selection_rationale": None, "why_this_matters": None,
        "what_was_doubted": None, "what_was_seen": None, "hypothesis_formed": None,
        "key_actors": None, "actor_roles": [], "situation_phases": None,
        "supporting_evidence": [], "weakening_evidence": [], "supporting_points": [], "weak_points": [],
        "outlook": None,
    }
    card.update(overrides)
    return card


def test_perspective_is_classified_from_saved_doubt_and_never_forces_contrarian():
    main = view.classify_perspective(_card(is_main=True, reading_role="MAIN"))
    assert main["label"] == "本線"
    actor = view.classify_perspective(_card(what_was_doubted={"value": "別の意図・狙いがあるのではないか。理由。"}))
    assert actor["label"] == "人物から見る" and "actor" in actor["tags"]
    struct = view.classify_perspective(_card(what_was_doubted={"value": "別の構造が働いているのではないか。"}))
    assert struct["label"] == "構造から見る"
    rel = view.classify_perspective(_card(what_was_doubted={"value": "相手・見せたい相手が違うのではないか。"}))
    assert rel["label"] == "関係性から見る"
    # 疑いの分類が無い代替Readingに「逆方向から見る」は付けない（UIのために逆張りを作らない）
    unclassified = view.classify_perspective(_card())
    assert unclassified["label"] == "別の見方" and "contrarian" not in unclassified["tags"]


def test_stakeholders_always_include_primary_but_others_only_when_mentioned():
    actors = [{"actor": "イラン政権（統合主体）"}, {"actor": "米政府（政権＋官僚機構）"}]
    card = _card(
        description={"value": "イラン政権は封鎖カードを温存する。"},
        actor_roles=[{"actor": "イラン政権（統合主体）", "role": "primary"},
                     {"actor": "湾岸諸国（サウジ・UAE）", "role": "unclassified"},
                     {"actor": "中国", "role": "unclassified"}])
    names = [s["name"] for s in view.build_stakeholders(card, actors)]
    assert names == ["イラン政権"]  # 本文に出ない湾岸諸国・中国・米政府は付けない（顔ぶれを全カード共通にしない）
    names2 = [s["name"] for s in view.build_stakeholders(card, actors, extra_text="湾岸諸国の迂回路が鍵だと思います。")]
    assert names2 == ["イラン政権", "湾岸諸国"]


def test_stakeholders_never_invent_names_absent_from_saved_data():
    card = _card(description={"value": "Trumpは負担分担を狙う。"}, actor_roles=[], key_actors=None)
    assert view.build_stakeholders(card, []) == []


def test_evidence_meaning_comes_only_from_saved_rationale():
    card = _card(
        supporting_evidence=[
            {"summary": "証拠の要約。", "rationale": "この読みを支える理由。", "actor": "米政府", "date": "2026-09-01T00:00:00"},
            {"summary": "理由の無い証拠。", "rationale": None, "actor": None, "date": None},
        ])
    review = view.build_card_review(card, key_point={}, actors=[], monitoring=[])
    assert review["supporting"][0] == {
        "actor": "米政府", "what": "証拠の要約。", "meaning": "この読みを支える理由。",
        "meaning_origin": "saved", "date": "2026-09-01"}
    assert review["supporting"][1]["meaning"] is None  # 意味づけを捏造しない


def test_evidence_rows_drop_items_whose_summary_is_just_a_source_file_path():
    card = _card(supporting_evidence=[
        {"summary": "eguchi_expert_ai_final_v1_0/sources/M140.html", "rationale": None, "actor": None, "date": None},
        {"summary": "意味のある要約。", "rationale": None, "actor": None, "date": None}])
    review = view.build_card_review(card, key_point={}, actors=[], monitoring=[])
    assert [r["what"] for r in review["supporting"]] == ["意味のある要約。"]
    assert view.looks_like_technical_ref("sources/M140.html") and not view.looks_like_technical_ref("米政府の発言。")


def test_fallback_headline_and_comment_are_short_and_have_no_internal_terms():
    card = _card(title=None, description={"value": "as_of=2026-09-03。" + "説明が長い、" * 60 + "。"},
                 selection_rationale={"value": "hypothesis_registerのH0が根拠。"})
    headline, comment = view.fallback_headline_and_comment(card)
    assert headline and len(headline) <= 72
    assert not view.has_forbidden_term(headline) and not view.has_forbidden_term(comment)


def _presentation_for_view():
    return {
        "key_point": {"one_line_assessment": {"value": "as_of=2026-09-03。局面の要約。"},
                      "initial_anomaly": {"value": "違和感の文。"}},
        "interpretations": [_card(is_main=True, reading_role="MAIN", displayed=True)],
        "reading_exposure": [], "actors": [{"actor": "米政府（官僚機構）", "wants": {"value": "負担分担。"},
                                            "constraint": None, "current_move": None, "why_it_matters": {"value": "鍵。"}}],
        "relationships": None, "structural_constraints": None, "trajectory": None,
        "monitoring_signals": [{"text": "監視項目1", "why": None}],
    }


def test_attach_expert_review_only_adds_keys_and_keeps_existing_presentation():
    presentation = _presentation_for_view()
    snapshot = copy.deepcopy(presentation)
    view.attach_expert_review(presentation, {})
    overview = presentation.pop("expert_review")
    card = presentation["interpretations"][0]
    review = card.pop("expert_review")
    assert presentation == snapshot  # 既存キー・既存カード内容は一切変わらない
    assert overview["point"] == "局面の要約。" and overview["anomaly"] == "違和感の文。"
    assert overview["key_actors"][0]["name"] == "米政府"
    assert review["is_primary"] and review["comment_origin"] == "fallback"
    assert review["watch"] == ["監視項目1"]  # 本線にはresponse-levelの監視項目を添える
    layer = {l["key"]: l["present"] for l in overview["layers"]}
    assert layer["actor"] is True and layer["relationship"] is False  # 無いレイヤーを有るように見せない


def test_apply_comments_overwrites_text_and_refreshes_stakeholders_only():
    presentation = _presentation_for_view()
    view.attach_expert_review(presentation, {})
    card = presentation["interpretations"][0]
    assert card["expert_review"]["stakeholders"] == []
    applied = view.apply_comments(
        [card], [{"title": "米政府は負担分担を狙うのではないか", "comment": "米政府の立場から読む。", "pattern": "other"}],
        presentation["actors"])
    assert applied == 1
    review = card["expert_review"]
    assert review["headline"].startswith("米政府") and review["comment_origin"] == "llm"
    assert [s["name"] for s in review["stakeholders"]] == ["米政府"]
    assert view.apply_comments([card], [None], presentation["actors"]) == 0  # 失敗要素はFallbackのまま


# ---- LLM出力の検証 -------------------------------------------------------------------

_CORPUS = "イラン政権 irgc 2027 封鎖カード".lower()
_GOOD_TITLE = "イラン政権は全面封鎖より準封鎖で圧力をかけるのではないか"
_GOOD_COMMENT = (
    "イラン政権としては、封鎖カードを手放さずに温存する方が合理的だと思います。"
    "全面封鎖は自国の原油輸出を止めてしまうので、現時点では限定的な妨害にとどまるとみるのが自然です。"
    "一般には強い脅しと受け取られやすいですが、実際の行動は慎重ですね。"
    "今後は検査強化の頻度が出るかどうかが分かれ目になります。")


def test_validate_accepts_a_well_formed_review_comment():
    assert transform.validate_expert_review_item(
        {"title": _GOOD_TITLE, "comment": _GOOD_COMMENT}, _CORPUS) is None


def test_validate_rejects_new_facts_paper_style_meta_openings_and_bad_lengths():
    v = transform.validate_expert_review_item
    assert v(None, _CORPUS) == "missing"
    assert v({"title": _GOOD_TITLE, "comment": _GOOD_COMMENT + "NVIDIAも動く。"}, _CORPUS).startswith("comment_new_term")
    assert v({"title": _GOOD_TITLE, "comment": _GOOD_COMMENT + "2030年に実現する。"}, _CORPUS).startswith("comment_new_number")
    assert v({"title": _GOOD_TITLE, "comment": _GOOD_COMMENT.replace("自然です", "総合的に見て自然です")},
             _CORPUS).startswith("comment_paper_style")
    assert v({"title": _GOOD_TITLE, "comment": "ここでは" + _GOOD_COMMENT}, _CORPUS) == "comment_meta_opening"
    assert v({"title": _GOOD_TITLE, "comment": _GOOD_COMMENT.replace("自然です", "間違いなくそうです")},
             _CORPUS).startswith("comment_too_assertive")
    assert v({"title": _GOOD_TITLE, "comment": "短い。"}, _CORPUS).startswith("comment_length")
    assert v({"title": _GOOD_TITLE, "comment": _GOOD_COMMENT * 3}, _CORPUS).startswith("comment_length")
    assert v({"title": _GOOD_TITLE, "comment": _GOOD_COMMENT * 2}, _CORPUS).startswith("comment_sentences")
    assert v({"title": _GOOD_TITLE, "comment": _GOOD_COMMENT.replace("現時点", "hypothesis_register")},
             _CORPUS) is not None
    assert v({"title": "短", "comment": _GOOD_COMMENT}, _CORPUS).startswith("title_length")


# ---- Transformation（キャッシュ・失敗時Fallback） -------------------------------------------

def _cards_for_transform():
    presentation = _presentation_for_view()
    view.attach_expert_review(presentation, {})
    return presentation, presentation["interpretations"]


_GOOD_FLOW = [
    "イラン政権は封鎖カードを温存したまま交渉に臨んでいる",
    "米政府は広い譲歩を求め、合意形成に時間がかかる構図になった",
    "湾岸諸国の迂回路整備が封鎖の破壊力を相対的に弱めつつある",
    "現在は封鎖を脅しとして使い続ける局面にある",
]


class _FakeLlm:
    """geo_llm_common.call_llm_structuredの差し替え。呼ばれた回数を数え、固定の良い出力を返す。
    他のLLM呼び出し（旧Presentation Transformation等）はother_callsに記録する（二重LLM検出用）。"""

    def __init__(self, comment=_GOOD_COMMENT, fail=False, flow=None, notes=None):
        self.calls, self.comment, self.fail = 0, comment, fail
        self.flow = _GOOD_FLOW if flow is None else flow
        self.notes = notes or {}
        self.other_calls = 0

    def __call__(self, client, model, system, user, schema, name, **kw):
        if name != "geo_ios_expert_review_comment":
            self.other_calls += 1
            return {"data": {"readings": []}}
        self.calls += 1
        if self.fail:
            raise RuntimeError("llm down")
        count = user.count("## Reading index=")
        return {"data": {"flow": self.flow, "readings": [
            {"index": i, "title": _GOOD_TITLE, "comment": self.comment, "pattern": "other",
             "evidence_notes": self.notes.get(i, [])}
            for i in range(count)]}}


def _ground_corpus(monkeypatch):
    """検証のcorpusに、fakeコメント中のラテン語/数字が含まれなくてもよい内容（日本語のみ）を使う。"""
    return monkeypatch


def test_transform_generates_once_then_serves_from_cache(monkeypatch):
    presentation, cards = _cards_for_transform()
    fake = _FakeLlm()
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake)
    first = transform.get_or_build_expert_review_comments(
        object(), "m", presentation=presentation, response_json={}, cards=cards)
    assert first["status"] == "generated" and fake.calls == 1
    assert first["comments"][0]["title"] == _GOOD_TITLE
    assert first["flow"] == _GOOD_FLOW  # 流れも同じ1回のLLM呼び出しで得る（LLM経路は1本）
    second =transform.get_or_build_expert_review_comments(
        object(), "m", presentation=presentation, response_json={}, cards=cards)
    assert second["status"] == "cache_hit" and fake.calls == 1  # 同じ入力ではLLMを再度呼ばない
    assert second["comments"] == first["comments"] and second["flow"] == _GOOD_FLOW  # 同じ入力なら同じ出力（安定）


def test_transform_fills_only_missing_readings_and_keeps_cached_good_ones(monkeypatch):
    presentation, cards = _cards_for_transform()
    inputs = transform.build_expert_review_inputs(presentation, {}, cards)
    transform._save_cache(transform._inputs_fingerprint(inputs), [None], {}, flow=_GOOD_FLOW)
    fake = _FakeLlm()
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake)
    # GETはキャッシュ済み(欠けあり)でもLLMを呼ばない
    got = transform.get_or_build_expert_review_comments(
        object(), "m", presentation=presentation, response_json={}, cards=cards, allow_generate=False)
    assert got["status"] == "cache_hit" and fake.calls == 0
    filled = transform.get_or_build_expert_review_comments(
        object(), "m", presentation=presentation, response_json={}, cards=cards)
    assert filled["status"] == "completed" and fake.calls == 1 and filled["comments"][0]["title"] == _GOOD_TITLE
    # 補完後はキャッシュが完全になり、以後は再生成しない
    again = transform.get_or_build_expert_review_comments(
        object(), "m", presentation=presentation, response_json={}, cards=cards)
    assert again["status"] == "cache_hit" and fake.calls == 1


def test_transform_get_path_never_calls_llm_when_cache_is_missing(monkeypatch):
    presentation, cards = _cards_for_transform()
    fake = _FakeLlm()
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake)
    result = transform.get_or_build_expert_review_comments(
        object(), "m", presentation=presentation, response_json={}, cards=cards, allow_generate=False)
    assert result["status"] == "pending" and result["comments"] == [None] and fake.calls == 0


def test_transform_llm_failure_propagates_and_is_not_cached(monkeypatch):
    presentation, cards = _cards_for_transform()
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _FakeLlm(fail=True))
    try:
        transform.get_or_build_expert_review_comments(
            object(), "m", presentation=presentation, response_json={}, cards=cards)
        raise AssertionError("expected RuntimeError")
    except RuntimeError:
        pass
    ok = _FakeLlm()
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", ok)
    result = transform.get_or_build_expert_review_comments(
        object(), "m", presentation=presentation, response_json={}, cards=cards)
    assert result["status"] == "generated" and ok.calls == 1  # 失敗はキャッシュされず次回再試行される


def test_transform_rejected_output_becomes_none_so_caller_uses_fallback(monkeypatch):
    presentation, cards = _cards_for_transform()
    fake = _FakeLlm(comment="ここでは" + _GOOD_COMMENT)  # 再試行でも同じ理由で棄却される
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake)
    result = transform.get_or_build_expert_review_comments(
        object(), "m", presentation=presentation, response_json={}, cards=cards)
    assert result["comments"] == [None] and 0 in result["failures"]
    assert fake.calls == 1 + transform._EXPERT_REVIEW_MAX_RETRIES  # 初回＋検証NG分だけの再試行


def test_transform_inputs_contain_only_saved_content_and_no_internal_terms():
    presentation, cards = _cards_for_transform()
    cards[0]["description"] = {"value": "as_of=2026-09-03。hypothesis_registerのH0は有力。"}
    inputs = transform.build_expert_review_inputs(
        presentation, {"key_actor_profiles": [{"actor": "米政府（官僚機構）", "persistent_preference": "負担分担を重視する。"}]},
        cards)
    dumped = str(inputs)
    assert "as_of" not in dumped and "hypothesis_register" not in dumped
    assert inputs["actors"][0]["actor"] == "米政府"
    assert inputs["readings"][0]["perspective"] == "本線"


# ---- API --------------------------------------------------------------------------

def _enable_llm_in_api(monkeypatch, fake):
    monkeypatch.setattr(presentation_api, "EXPERT_REVIEW_LLM_ENABLED", True)
    monkeypatch.setattr(presentation_api, "_azure_client", object())
    monkeypatch.setattr(presentation_api, "_model", "m")
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake)


def test_api_get_adds_deterministic_expert_review_and_never_calls_llm(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(presentation_api, "_client", client)
    fake = _FakeLlm()
    _enable_llm_in_api(monkeypatch, fake)
    session, _ = _seed_session_with_candidates(client, response_json=_minimal_response_json())

    result = presentation_api.get_situation_presentation(session["analysis_session_id"])
    presentation = result["presentation"]
    assert fake.calls == 0  # 画面表示（GET）ではLLMを呼ばない
    assert presentation["expert_review"]["comment_status"] == "pending"
    cards = presentation["interpretations"]
    assert all(c["expert_review"]["comment_origin"] == "fallback" for c in cards)
    assert cards[0]["expert_review"]["is_primary"] is True
    assert "_source" not in str(presentation)  # 既存のdebug除去の約束を守る


def test_api_post_generates_once_and_following_get_serves_cached_comments(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(presentation_api, "_client", client)
    fake = _FakeLlm()
    _enable_llm_in_api(monkeypatch, fake)
    session, _ = _seed_session_with_candidates(client, response_json=_minimal_response_json())
    sid = session["analysis_session_id"]

    posted = presentation_api.generate_expert_review_for_session(sid)
    assert posted["status"] == "generated" and fake.calls == 1
    again = presentation_api.generate_expert_review_for_session(sid)
    assert again["status"] == "cache_hit" and fake.calls == 1

    presentation = presentation_api.get_situation_presentation(sid)["presentation"]
    assert fake.calls == 1
    assert presentation["expert_review"]["comment_status"] == "cache_hit"
    shown = view.display_cards(presentation)  # フロントが表示元にするカード（reading_exposure優先）
    assert [c["expert_review"]["headline"] for c in shown] == [_GOOD_TITLE] * len(shown) and shown
    assert all(c["expert_review"]["comment_origin"] == "llm" for c in shown)


def test_api_llm_failure_keeps_existing_presentation_and_uses_fallback(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(presentation_api, "_client", client)
    _enable_llm_in_api(monkeypatch, _FakeLlm(fail=True))
    session, _ = _seed_session_with_candidates(client, response_json=_minimal_response_json())

    posted = presentation_api.generate_expert_review_for_session(session["analysis_session_id"])
    assert posted["status"] == "fallback"
    presentation = presentation_api.get_situation_presentation(session["analysis_session_id"])["presentation"]
    assert presentation["key_point"]["primary_key_point"]["value"] == "主要な蝶番"
    assert len(presentation["interpretations"]) == 2
    assert all(c["expert_review"]["comment_origin"] == "fallback" for c in presentation["interpretations"])


def test_api_works_without_llm_configured(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(presentation_api, "_client", client)
    monkeypatch.setattr(presentation_api, "EXPERT_REVIEW_LLM_ENABLED", True)
    monkeypatch.setattr(presentation_api, "_azure_client", None)
    session, _ = _seed_session_with_candidates(client, response_json=_minimal_response_json())
    presentation = presentation_api.get_situation_presentation(session["analysis_session_id"])["presentation"]
    assert presentation["interpretations"][0]["expert_review"]["comment_origin"] == "fallback"
    assert "comment_status" not in presentation["expert_review"]
