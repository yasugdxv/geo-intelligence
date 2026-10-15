"""Expert Review 最終Presentation Quality Pass（2026-10-04）のRegression Test。

- Presentation Hygiene: 内部表現・壊れた断片を除きつつ、製品名・政策名・略称を誤削除しない
- 「ここまでの流れ」(flow): 検証・再試行・キャッシュ・Fallback
- Evidenceの「この読みにどう効くか」: 保存済み理由は変えず、理由が無い項目にだけ補う
- LLM経路を1本にする（新UI時は旧6段落Presentation Transformationを実行しない）
- Cacheのversion/キー/保存先
実LLMは一切呼ばない（fakeを注入）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_expert_review_view as view  # noqa: E402
import geo_ios_reading_presentation_transform_service as transform  # noqa: E402
import geo_ios_situation_presentation_api as presentation_api  # noqa: E402
import geo_llm_common  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402
from tests.test_geo_ios_expert_review import (  # noqa: E402
    _CORPUS, _GOOD_COMMENT, _GOOD_FLOW, _GOOD_TITLE, _FakeLlm, _cards_for_transform,
    _enable_llm_in_api)
from tests.test_geo_ios_situation_presentation_api import (  # noqa: E402
    _minimal_response_json, _seed_session_with_candidates)


# ---- Presentation Hygiene ----------------------------------------------------------------

_REAL_NAMES = [
    "H200級を条件付きで認める", "H20も規制対象", "NVIDIAのGPUが対象", "BISのライセンス審査", "MATCH法の審議",
    "JCPOA+αの議論", "TSMCの対中売上", "SAFE Chips Actの成立", "IRGCの演習", "UAEのHabshan-Fujairahパイプライン",
    "NSCとCommerce", "CSISレポート", "AMD MI300の扱い", "Moolenaar議員の発言", "NATOの方針", "IEAの統計",
    "IAEAの査察", "Huaweiの動き", "Mohammed bin Salmanの判断", "Benjamin Netanyahuの立場", "Trumpの発言",
    "White Houseの意向", "US-Saudiの関係", "SEMICON関連",
]


def test_hygiene_never_alters_real_product_policy_and_organization_names():
    for text in _REAL_NAMES:
        assert view.hygiene_text(text) == text, text
        assert view.clean_text(text) == text, text


def test_hygiene_removes_internal_english_labels_and_replaces_internal_terms_naturally():
    out = view.hygiene_text("Current Evidenceが不足している。Evidence Fit: 資料は強い。Preference-Action Gapは小さい。")
    assert "Current Evidence" not in out and "Evidence Fit" not in out and "Preference-Action Gap" not in out
    assert "現時点の根拠が不足している" in out and "資料は強い" in out and "志向と行動のずれ" in out
    assert view.hygiene_text("Key Stakeholderとして扱う") == "鍵となる人物・組織として扱う"


def test_hygiene_repairs_text_left_broken_by_removed_citations():
    broken = ("規制が強化されているにもかかわらず、の抜粋タイトルが示すように「需要が増える」とされる。"
              "また、で台湾総統が連携を掲げる。は、元データの欠落。")
    out = view.hygiene_text(broken)
    assert "の抜粋タイトル" not in out and "また、で" not in out and not out.endswith("元データの欠落。は、")
    assert "「需要が増える」とされる" in out and "台湾総統が連携を掲げる" in out and "元データの欠落" in out
    assert "、の" not in out and not out.startswith("は、")


def test_hygiene_repairs_orphaned_label_left_after_id_removal():
    raw = ("規制が強化されているにもかかわらず、document_id: の抜粋タイトルが示すように「需要が増える」とされる点。"
           "また、document_id: で台湾総統と日本側が協力を掲げている。")
    out = view.hygiene_text(raw)
    assert "document_id" not in out and "の抜粋" not in out
    assert out == "規制が強化されているにもかかわらず、「需要が増える」とされる点。また、台湾総統と日本側が協力を掲げている。"


def test_hygiene_handles_as_of_in_every_observed_form_and_keeps_sentence_period():
    assert view.hygiene_text("封鎖を主軸に読むべき局面である（as_of=2026-09-25）。") == "封鎖を主軸に読むべき局面である。"
    assert view.hygiene_text("as_of=2026-09-03。米・サウジの再編が鍵になる。") == "米・サウジの再編が鍵になる。"
    assert view.hygiene_text("as_of 2026-09-03、提供資料の範囲では、台湾政府が動く。") == "提供資料の範囲では、台湾政府が動く。"
    assert "as_of" not in view.hygiene_text("途中で切れた（as_o")


def test_hygiene_removes_bare_uuid_and_repairs_the_sentence_it_leaves_behind():
    raw = "規制が強化されているにもかかわらず、713a3264-ba25-4cca-807e-a0b18366c4c7の抜粋タイトルが示すように「需要が増える」とされる。"
    assert view.hygiene_text(raw) == "規制が強化されているにもかかわらず、「需要が増える」とされる。"


def test_hygiene_removes_internal_field_name_lists_but_keeps_normal_words():
    out = view.hygiene_text("supporting_evidence_ids/weakening_evidence_relevance/contradicting_evidence_idsの明示的な参照は無く、整理した。")
    assert "_" not in out and "整理した" in out
    assert view.hygiene_text("SAFE Chips ActとMATCH法") == "SAFE Chips ActとMATCH法"


def test_hygiene_translates_remaining_english_analysis_labels_naturally():
    assert view.hygiene_text("今回のObserved ActionのGapを説明する") == "今回の観測された行動のずれを説明する"
    assert view.hygiene_text("Key Point（線引きの蝶番）を判別する") == "今回の鍵（線引きの蝶番）を判別する"
    assert view.hygiene_text("H0（Surface解釈）として扱う") == "表面的な解釈として扱う"
    assert view.hygiene_text("Structuralシナリオ") == "構造面のシナリオ"
    assert view.hygiene_text("Means/封鎖準備は手段として合理的") == "封鎖準備は手段として合理的"
    assert view.hygiene_text("人物・組織の分析がinsufficientである") == "人物・組織の分析が不十分である"
    assert view.hygiene_text("reasonの記述のみに基づく") == "理由の記述のみに基づく"
    assert view.hygiene_text("Reasonable doubt") == "Reasonable doubt"  # 英単語の一部は変換しない


def test_sanitize_blanks_values_that_are_entirely_ids_but_never_names_or_products():
    node = {"relationships": {"leverage": [
        {"actor": "c47c90e6-eda1-4f56-9c3e-2b3206fde945"}, {"actor": "Taiwan_Gov"},
        {"actor": "TSMC"}, {"actor": "H200"}, {"actor": "BIS・Commerce"}]}}
    view.sanitize_presentation_in_place(node)
    assert [x["actor"] for x in node["relationships"]["leverage"]] == ["", "", "TSMC", "H200", "BIS・Commerce"]


def test_hygiene_keeps_legitimate_phrases_that_look_similar_to_broken_ones():
    for text in ("記事の抜粋では足りない。", "提供資料はタイトル・ニュース抜粋のみである。", "では、次に進む。",
                 "同盟国の反発を受けた。"):
        assert view.hygiene_text(text) == text, text


def test_hygiene_removes_file_paths_and_json_fragments_but_not_urls_domains_in_prose():
    assert "sources/M140.html" not in view.hygiene_text("根拠は eguchi_expert_ai_final_v1_0/sources/M140.html にある")
    assert view.hygiene_text("末尾の断片}]。") == "末尾の断片。"
    assert view.hygiene_text("thebulletin.orgの記事") == "thebulletin.orgの記事"


def test_sanitize_presentation_in_place_cleans_user_text_but_not_ids_or_enums():
    presentation = {
        "key_point": {"investigation_question": {"value": "# CASE — Hard Blind Input"},
                      "one_line_assessment": {"value": "Current Evidenceが不足。"}},
        "interpretations": [{"scenario_candidate_id": "Evidence", "reading_role": "MAIN",
                              "supporting_evidence": [{"evidence_id": "Evidence", "relation": "supports",
                                                       "summary": "Evidence Fit: 要約。", "rationale": None}]}],
    }
    view.sanitize_presentation_in_place(presentation)
    assert presentation["key_point"]["one_line_assessment"]["value"] == "現時点の根拠が不足。"
    assert presentation["key_point"]["investigation_question"]["value"] == "# CASE — Hard Blind Input"  # 依頼原文は対象外
    card = presentation["interpretations"][0]
    assert card["scenario_candidate_id"] == "Evidence" and card["supporting_evidence"][0]["evidence_id"] == "Evidence"
    assert card["supporting_evidence"][0]["summary"] == "要約。"


def test_future_conditions_are_never_shown_as_observed_supporting_or_contradicting_evidence():
    card = view_card = {
        "is_main": False, "reading_role": "ALTERNATIVE", "title": "t", "description": {"value": "説明。"},
        "supporting_evidence": [], "weakening_evidence": [],
        "supporting_points": [
            {"value": "条件として強まる材料。", "_source": "alternative_scenarios[].strengthening_conditions"},
            {"value": "実際に観測された発言。", "_source": "main_scenario.supporting_evidence"}],
        "weak_points": [
            {"value": "条件として弱まる材料。", "_source": "alternative_scenarios[].weakening_conditions"},
            {"value": "観測された矛盾。", "_source": "response.contradicting_evidence"}],
        "outlook": {"weakens_if": {"value": ["条件として弱まる材料。"]}},
    }
    review = view.build_card_review(card, key_point={}, actors=[], monitoring=[])
    assert [r["what"] for r in review["supporting"]] == ["実際に観測された発言。"]
    assert [r["what"] for r in review["contradicting"]] == ["観測された矛盾。"]
    assert review["break_conditions"] == ["条件として弱まる材料。"]  # 条件は「崩れる条件」側にだけ出る
    assert view_card is card


# ---- 「ここまでの流れ」 --------------------------------------------------------------------

def test_validate_flow_accepts_good_flow_and_empty_flow_but_rejects_bad_ones():
    ok, reason = transform.validate_flow(_GOOD_FLOW, _CORPUS + " " + "".join(_GOOD_FLOW).lower())
    assert ok == _GOOD_FLOW and reason is None
    assert transform.validate_flow([], "") == ([], None)  # 材料が乏しい時の正当な回答
    assert transform.validate_flow(None, "")[1] == "flow_missing"
    assert transform.validate_flow(_GOOD_FLOW[:2], "")[1] == "flow_steps=2"
    assert transform.validate_flow(_GOOD_FLOW + _GOOD_FLOW[:3], "")[1] in ("flow_steps=7", "flow_duplicate_steps")
    assert transform.validate_flow(["米政府が動いた" + "あ" * 90, "二つ目のステップです。", "三つ目のステップです。"], "")[1].startswith("flow_step_length")
    assert transform.validate_flow(["→米政府が譲歩を求める", "イラン政権が封鎖を温存する", "現在は交渉の局面にある"], "")[1] == "flow_step_has_marker"
    assert transform.validate_flow(["NVIDIAが輸出を止める", "イラン政権が封鎖を温存する", "現在は交渉の局面にある"], "イラン政権 米政府")[1].startswith("flow_new_term")
    assert transform.validate_flow(["2030年に規制が強まる", "イラン政権が封鎖を温存する", "現在は交渉の局面にある"], "")[1].startswith("flow_new_number")


class _SequenceLlm(_FakeLlm):
    """flowだけ1回目NG・2回目OKにするfake（flowの修正指示付き再試行を確認する）。"""

    def __call__(self, client, model, system, user, schema, name, **kw):
        result = super().__call__(client, model, system, user, schema, name, **kw)
        if name == "geo_ios_expert_review_comment" and self.calls == 1:
            result["data"]["flow"] = ["短い", "二つだけ"]
        return result


def test_transform_retries_flow_with_instruction_and_caches_the_fixed_flow(monkeypatch):
    presentation, cards = _cards_for_transform()
    fake = _SequenceLlm()
    prompts = []
    orig = fake.__call__

    def spy(client, model, system, user, schema, name, **kw):
        prompts.append(user)
        return orig(client, model, system, user, schema, name, **kw)

    monkeypatch.setattr(geo_llm_common, "call_llm_structured", spy)
    result = transform.get_or_build_expert_review_comments(
        object(), "m", presentation=presentation, response_json={}, cards=cards)
    assert result["flow"] == _GOOD_FLOW and fake.calls == 2
    assert "flowの修正指示" in prompts[1] and "flowの修正指示" not in prompts[0]
    again = transform.get_or_build_expert_review_comments(
        object(), "m", presentation=presentation, response_json={}, cards=cards)
    assert again["status"] == "cache_hit" and again["flow"] == _GOOD_FLOW


def test_transform_flow_unavailable_returns_none_so_ui_falls_back_and_next_post_fills_it(monkeypatch):
    presentation, cards = _cards_for_transform()
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _FakeLlm(flow=["短い", "二つだけ"]))
    result = transform.get_or_build_expert_review_comments(
        object(), "m", presentation=presentation, response_json={}, cards=cards)
    assert result["flow"] is None and result["comments"][0] is not None  # コメントは使える
    good = _FakeLlm()
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", good)
    filled = transform.get_or_build_expert_review_comments(
        object(), "m", presentation=presentation, response_json={}, cards=cards)
    assert filled["status"] == "completed" and filled["flow"] == _GOOD_FLOW and good.calls == 1


def test_inputs_use_saved_dynamics_and_trajectory_only():
    presentation, cards = _cards_for_transform()
    presentation["trajectory"] = {"summary": {"value": "保存済みの推移の説明。"}, "gaps": None}
    response_json = {"dynamics": "as_of=2026-09-03。イランは譲歩を拒み、米国は広い譲歩を求める。",
                     "executive_summary": {"what_changed": "封鎖の価値が変質した。"},
                     "preference_action_gap_summary": "志向と行動にずれがある。"}
    dyn = transform.build_expert_review_inputs(presentation, response_json, cards)["situation_dynamics"]
    assert dyn["trajectory"] == "保存済みの推移の説明。"
    assert "as_of" not in dyn["dynamics_of_the_situation"] and "譲歩を拒み" in dyn["dynamics_of_the_situation"]
    assert dyn["what_changed"] == "封鎖の価値が変質した。" and dyn["preference_vs_action"] == "志向と行動にずれがある。"


def test_apply_flow_sets_shared_flow_only_when_steps_exist():
    presentation = {"expert_review": {}}
    assert view.apply_flow(presentation, None) is False and "flow" not in presentation["expert_review"]
    assert view.apply_flow(presentation, []) is False
    assert view.apply_flow(presentation, _GOOD_FLOW) is True
    assert presentation["expert_review"]["flow"] == {"steps": _GOOD_FLOW, "origin": "llm"}


# ---- Evidenceの「この読みにどう効くか」 ---------------------------------------------------------

def _reading_with_evidence():
    return {"supporting_materials": [
        {"i": 0, "actor": "米政府", "what": "証拠0", "saved_reason": "保存済みの理由。"},
        {"i": 1, "actor": "米政府", "what": "証拠1", "saved_reason": None}],
        "contradicting_materials": [{"i": 0, "actor": None, "what": "証拠C0", "saved_reason": None}]}


def test_clean_evidence_notes_keeps_only_valid_notes_for_items_without_saved_reason():
    good = "米政府が負担を求める動きは、この読みの前提を補強する材料です。"
    item = {"evidence_notes": [
        {"side": "supporting", "i": 0, "note": good},  # 保存済み理由あり→捨てる
        {"side": "supporting", "i": 1, "note": good},  # 採用
        {"side": "supporting", "i": 9, "note": good},  # 存在しない番号→捨てる
        {"side": "contradicting", "i": 0, "note": good + "確実に証明する。"},  # 強い断定→捨てる
        {"side": "contradicting", "i": 0, "note": "短い"},  # 短すぎる→捨てる
        {"side": "supporting", "i": 1, "note": good},  # 重複→捨てる
    ]}
    notes = transform.clean_evidence_notes(item, _reading_with_evidence(), "米政府 " + good.lower())
    assert notes == [{"side": "supporting", "i": 1, "note": good}]


def test_apply_comments_adds_generated_meaning_without_touching_saved_meaning_or_evidence():
    presentation, cards = _cards_for_transform()
    review = cards[0]["expert_review"]
    review["supporting"] = [
        {"actor": "米政府", "what": "証拠0", "meaning": "保存済みの理由。", "meaning_origin": "saved", "date": None},
        {"actor": "米政府", "what": "証拠1", "meaning": None, "meaning_origin": None, "date": None}]
    note = "米政府の動きは、この読みを補強する材料です。"
    comment = {"title": _GOOD_TITLE, "comment": _GOOD_COMMENT, "pattern": "other", "evidence_notes": [
        {"side": "supporting", "i": 0, "note": "上書きされてはいけない"},
        {"side": "supporting", "i": 1, "note": note}]}
    view.apply_comments(cards, [comment], presentation["actors"])
    rows = review["supporting"]
    assert rows[0]["meaning"] == "保存済みの理由。" and rows[0]["meaning_origin"] == "saved"
    assert rows[1]["meaning"] == note and rows[1]["meaning_origin"] == "generated"
    assert [r["what"] for r in rows] == ["証拠0", "証拠1"]  # Evidence本体は不変


def test_llm_inputs_mark_which_evidence_already_has_a_saved_reason():
    presentation, cards = _cards_for_transform()
    cards[0]["expert_review"]["supporting"] = [
        {"actor": None, "what": "証拠0", "meaning": "保存済みの理由。", "meaning_origin": "saved", "date": None},
        {"actor": None, "what": "証拠1", "meaning": None, "meaning_origin": None, "date": None}]
    inputs = transform.build_expert_review_inputs(presentation, {}, cards)
    mats = inputs["readings"][0]["supporting_materials"]
    assert [(m["i"], bool(m["saved_reason"])) for m in mats] == [(0, True), (1, False)]


# ---- LLM経路を1本に / Session側の旧LLM ---------------------------------------------------------

def test_session_get_does_not_run_the_legacy_six_paragraph_llm_when_new_ui_is_used(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(presentation_api, "_client", client)
    fake = _FakeLlm()
    _enable_llm_in_api(monkeypatch, fake)
    session, _ = _seed_session_with_candidates(client, response_json=_minimal_response_json())
    sid = session["analysis_session_id"]

    presentation_api.get_situation_presentation(sid)
    presentation_api.generate_expert_review_for_session(sid)
    presentation = presentation_api.get_situation_presentation(sid)["presentation"]
    assert fake.other_calls == 0  # 旧「6段落Presentation Transformation」は実行されない
    assert fake.calls == 1  # 新Expert Review Transformationは1ケース1回
    assert presentation["expert_review"]["flow"]["steps"] == _GOOD_FLOW
    shown = view.display_cards(presentation)
    assert all(c.get("reading_presentation") is None for c in shown)


def test_session_get_still_uses_legacy_llm_only_as_fallback_when_new_view_fails(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(presentation_api, "_client", client)
    fake = _FakeLlm()
    _enable_llm_in_api(monkeypatch, fake)

    def boom(*a, **k):
        raise RuntimeError("view failed")

    monkeypatch.setattr(view, "attach_expert_review", boom)
    session, _ = _seed_session_with_candidates(client, response_json=_minimal_response_json())
    presentation = presentation_api.get_situation_presentation(session["analysis_session_id"])["presentation"]
    assert "expert_review" not in presentation  # 失敗時は新UI用の中途半端な情報を残さない
    assert all("expert_review" not in c for c in view.display_cards(presentation))
    assert fake.other_calls >= 1  # 旧UIのFallback用に旧LLMが使われる


def test_frozen_response_route_never_runs_legacy_llm(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(presentation_api, "_client", client)
    fake = _FakeLlm()
    _enable_llm_in_api(monkeypatch, fake)
    client.tables.setdefault("geo_expert_responses", []).append(
        {"response_id": "resp-frozen", "request_id": None, "response_json": {
            **_minimal_response_json(), "main_scenario": {"description": "主シナリオの説明", "confidence": "中"}}})
    result = presentation_api.get_situation_presentation_by_response_id("resp-frozen")
    assert fake.calls == 0 and fake.other_calls == 0
    assert result["presentation"]["expert_review"]["comment_status"] == "pending"


# ---- Cache ----------------------------------------------------------------------------

def test_cache_file_is_named_by_version_and_fingerprint_and_survives_new_module_state(monkeypatch, tmp_path):
    presentation, cards = _cards_for_transform()
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _FakeLlm())
    first = transform.get_or_build_expert_review_comments(
        object(), "m", presentation=presentation, response_json={}, cards=cards)
    files = list((tmp_path / "expert_review_cache").glob("*.json"))
    assert len(files) == 1
    assert files[0].name == f"{transform.EXPERT_REVIEW_PROMPT_VERSION}__{first['fingerprint']}.json"
    # 再起動相当: 新しいLLM（呼ばれたら失敗）を使っても、ディスク上のキャッシュだけで同じ結果が返る
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _FakeLlm(fail=True))
    again = transform.get_or_build_expert_review_comments(
        object(), "m", presentation=presentation, response_json={}, cards=cards)
    assert again["status"] == "cache_hit" and again["comments"] == first["comments"]


def test_bumping_prompt_version_invalidates_cache_so_writing_guide_updates_take_effect(monkeypatch):
    presentation, cards = _cards_for_transform()
    fake = _FakeLlm()
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake)
    transform.get_or_build_expert_review_comments(
        object(), "m", presentation=presentation, response_json={}, cards=cards)
    monkeypatch.setattr(transform, "EXPERT_REVIEW_PROMPT_VERSION", transform.EXPERT_REVIEW_PROMPT_VERSION + "_next")
    pending = transform.get_or_build_expert_review_comments(
        object(), "m", presentation=presentation, response_json={}, cards=cards, allow_generate=False)
    assert pending["status"] == "pending" and fake.calls == 1
    regenerated = transform.get_or_build_expert_review_comments(
        object(), "m", presentation=presentation, response_json={}, cards=cards)
    assert regenerated["status"] == "generated" and fake.calls == 2


def test_different_responses_never_share_a_cache_entry(monkeypatch):
    presentation, cards = _cards_for_transform()
    other_presentation, other_cards = _cards_for_transform()
    other_cards[0]["description"] = {"value": "別の局面の説明。"}
    other_cards[0]["expert_review"]["headline"] = "別"
    a = transform._inputs_fingerprint(transform.build_expert_review_inputs(presentation, {}, cards))
    b = transform._inputs_fingerprint(transform.build_expert_review_inputs(other_presentation, {}, other_cards))
    assert a != b
