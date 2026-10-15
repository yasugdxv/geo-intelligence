"""AI暫定承認（geo_knowledge_ai_review_service）のテスト。
承認条件がコードで固定されていること、出自の検証を通すこと、人の記録テーブルに触れないこと、
人があとで確認・修正できることを確認する。実際のPerplexity・AIは呼ばない。"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_knowledge_ai_review_service as svc  # noqa: E402
import geo_llm_common  # noqa: E402
import geo_perplexity_search as pplx  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

NOW = datetime(2026, 10, 8, 3, 0, tzinfo=timezone.utc)
SOURCES = [{"url": "https://www.state.gov/a", "title": "A", "date": "2026-09-01"},
           {"url": "https://www.reuters.com/b", "title": "B", "date": "2026-09-02"},
           {"url": "https://www.reuters.com/c", "title": "C", "date": None}]


def _ev(**kw):
    base = {"verdict": "approve", "evidence_adequacy": "sufficient", "confidence": "high",
            "verified_points": ["主要な主張を確認[1]"], "problems": [], "suggested_fixes": [],
            "rationale": "出典で確認できた。", "cited_source_numbers": [1, 2]}
    base.update(kw)
    return base


def _item(i="k1", status="candidate", **kw):
    row = {"knowledge_item_id": i, "knowledge_type": "actor_profile", "title": "Donald Trump", "status": status,
           "retrieval_status": "restricted", "is_demo": False, "version": 1, "as_of_date": "2026-09-07",
           "structured_content": {"entity_id": "e1", "core_interests": ["関税"]}, "origin": None, "approved_by": None}
    row.update(kw)
    return row


def _client(*items):
    return FakeSupabaseClient(tables={"geo_knowledge_items": list(items), "geo_audit_logs": [],
                                      "geo_knowledge_reviews": []})


# ---- 承認条件（コードで固定） ----

def test_decide_approves_only_when_every_condition_is_met():
    d = svc.decide(_ev(), _item(), SOURCES, now=NOW)
    assert d["eligible"] is True and d["cited_domains"] == ["reuters.com", "state.gov"]


@pytest.mark.parametrize("override,expected", [
    ({"verdict": "hold"}, "approve ではない"),
    ({"evidence_adequacy": "partial"}, "根拠が十分ではない"),
    ({"confidence": "low"}, "確信度が低い"),
    ({"problems": [{"kind": "factual_error", "text": "誤り"}]}, "問題あり"),
    ({"problems": [{"kind": "outdated", "text": "古い"}]}, "問題あり"),
    ({"problems": [{"kind": "unsupported", "text": "裏付け無し"}]}, "問題あり"),
    ({"cited_source_numbers": [2, 3]}, "2つ未満"),          # 同じドメイン(reuters)だけ
    ({"cited_source_numbers": [1]}, "2つ未満"),
    ({"cited_source_numbers": [99]}, "2つ未満"),              # 一覧に無い番号は数えない
])
def test_decide_rejects_each_unmet_condition(override, expected):
    d = svc.decide(_ev(**override), _item(), SOURCES, now=NOW)
    assert d["eligible"] is False and any(expected in r for r in d["reasons"])


def test_decide_allows_notes_like_overgeneralization_or_bias():
    ev = _ev(problems=[{"kind": "overgeneralization", "text": "言い過ぎ"}, {"kind": "bias", "text": "偏り"}])
    assert svc.decide(ev, _item(), SOURCES, now=NOW)["eligible"] is True


def test_old_as_of_date_does_not_block_approval():
    """過去の行動・歴史は、人物像や思想を知る重要な情報。時点が古いこと自体では止めない。"""
    d = svc.decide(_ev(), _item(as_of_date="2024-01-01"), SOURCES, now=NOW)
    assert d["eligible"] is True


# ---- 評価 ----

def test_evaluate_item_uses_web_sources_and_applies_the_rule(monkeypatch):
    monkeypatch.setattr(pplx, "search", lambda settings, **k: {"summary": "調査の要約", "found": True, "citations": [
        {"url": s["url"], "title": s["title"], "published_at": s["date"]} for s in SOURCES]})
    seen = {}

    def fake_llm(c, m, sp, up, schema, name, **kw):
        seen["user"] = up
        return {"data": _ev()}
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake_llm)
    out = svc.evaluate_item(object(), "gpt-x", {"key": "k"}, _item(knowledge_type="precedent"), now=NOW)
    assert out["eligible"] is True and out["mode"] == "factual" and len(out["sources"]) == 3
    assert "[1] A | state.gov | 2026-09-01" in seen["user"] and "core_interests: 関税" in seen["user"]


# ---- 反映（AI暫定承認） ----

def test_apply_marks_item_active_available_with_ai_approver_and_audit_log():
    c = _client(_item())
    res = {"knowledge_item_id": "k1", "eligible": True, "evaluation": _ev(), "sources": SOURCES,
           "cited_domains": ["reuters.com", "state.gov"]}
    out = svc.apply_ai_provisional_approval(c, res, model="gpt-x", now=NOW)
    assert out["applied"] is True
    row = c.tables["geo_knowledge_items"][0]
    assert row["status"] == "active" and row["retrieval_status"] == "available" and row["version"] == 2
    assert row["approved_by"] == "ai:gpt-x" and svc.is_ai_approved(row)
    assert "AI暫定承認" in row["confidence_rationale"] and "state.gov" in row["confidence_rationale"]
    log = c.tables["geo_audit_logs"][0]
    assert log["action"] == "knowledge_item_ai_provisional_approval" and log["actor"] == "ai:gpt-x"


def test_apply_never_writes_to_the_human_review_table():
    c = _client(_item())
    svc.apply_ai_provisional_approval(c, {"knowledge_item_id": "k1", "eligible": True, "evaluation": _ev(),
                                          "sources": SOURCES}, model="gpt-x", now=NOW)
    assert c.tables["geo_knowledge_reviews"] == []          # 人の記録は、人だけ


def test_apply_skips_items_that_are_not_eligible_or_not_candidates():
    c = _client(_item(), _item("k2", status="active"))
    assert svc.apply_ai_provisional_approval(
        c, {"knowledge_item_id": "k1", "eligible": False}, model="m", now=NOW)["applied"] is False
    out = svc.apply_ai_provisional_approval(
        c, {"knowledge_item_id": "k2", "eligible": True, "evaluation": _ev(), "sources": SOURCES}, model="m", now=NOW)
    assert out["applied"] is False and "候補ではない" in out["reason"]
    assert c.tables["geo_knowledge_items"][0]["status"] == "candidate"


def test_apply_respects_lineage_validation_like_the_human_gate():
    # origin=geo_ios_learning_candidate なのに、承認済みのLearning Candidateが無い → 承認しない
    c = _client(_item(origin="geo_ios_learning_candidate"))
    c.tables["geo_ios_learning_candidates"] = []
    out = svc.apply_ai_provisional_approval(
        c, {"knowledge_item_id": "k1", "eligible": True, "evaluation": _ev(), "sources": SOURCES}, model="m", now=NOW)
    assert out["applied"] is False and "出自" in out["reason"]
    assert c.tables["geo_knowledge_items"][0]["status"] == "candidate"


def test_record_hold_keeps_status_and_logs_the_ai_findings():
    c = _client(_item())
    svc.record_hold(c, {"knowledge_item_id": "k1", "reasons": ["根拠が十分ではない"], "evaluation": _ev(), "sources": []},
                    model="m")
    assert c.tables["geo_knowledge_items"][0]["status"] == "candidate"
    assert c.tables["geo_audit_logs"][0]["action"] == "knowledge_item_ai_review_hold"


# ---- 人があとで確認する ----

def test_human_can_confirm_an_ai_approval_and_ai_cannot():
    c = _client(_item(status="active", approved_by="ai:gpt-x", confidence_rationale="【AI暫定承認】…"))
    with pytest.raises(Exception):
        svc.confirm_by_human(c, "k1", human_id="ai:gpt-x", human_role="viewer")           # 権限なし
    out = svc.confirm_by_human(c, "k1", human_id="reviewer-1", human_role="expert_reviewer", note="内容を確認した")
    assert out["approved_by"] == "reviewer-1" and not svc.is_ai_approved(out)
    assert "人が確認済み" in out["confidence_rationale"] and "AI暫定承認" in out["confidence_rationale"]  # 元の評価も残す


def test_confirm_rejects_items_that_were_not_ai_approved():
    c = _client(_item(status="active", approved_by="reviewer-1"))
    with pytest.raises(ValueError):
        svc.confirm_by_human(c, "k1", human_id="r", human_role="expert_reviewer")


def test_candidate_items_prioritizes_foundation_types_and_skips_demo_and_approved():
    c = _client(_item("a", knowledge_type="precedent"), _item("b", knowledge_type="actor_profile"),
                _item("c", status="active"), _item("d", is_demo=True), _item("e", status="in_review", knowledge_type="institution_profile"))
    ids = [r["knowledge_item_id"] for r in svc.candidate_items(c, limit=10)]
    assert ids == ["b", "e", "a"]
    assert [r["knowledge_item_id"] for r in svc.candidate_items(c, types=["precedent"], limit=10)] == ["a"]


# ---------------------------------------------------------------------------
# 案A: 解釈中心の種類は、事実と解釈を分けて評価する
# ---------------------------------------------------------------------------

def _iev(**kw):
    base = {"facts": [{"claim": "2024年に就任", "status": "verified", "source_numbers": [1]},
                      {"claim": "関税を発動", "status": "verified", "source_numbers": [2]},
                      {"claim": "同盟国と会談", "status": "verified", "source_numbers": [1, 2]},
                      {"claim": "細かい発言の詳細", "status": "unverified", "source_numbers": []}],
            "interpretation": {"assessment": "consistent", "concerns": [], "summary": "関税を交渉の梃子とする見立ては行動と整合"},
            "outdated": False, "outdated_note": "", "confidence": "medium", "suggested_fixes": [],
            "rationale": "事実は概ね確認できた。"}
    base.update(kw)
    return base


def test_interpretive_types_are_evaluated_with_facts_and_interpretation_separately(monkeypatch):
    monkeypatch.setattr(pplx, "search", lambda settings, **k: {"summary": "s", "found": True, "citations": [
        {"url": x["url"], "title": x["title"], "published_at": x["date"]} for x in SOURCES]})
    seen = {}

    def fake_llm(c, m, sp, up, schema, name, **kw):
        seen.update(system=sp, schema=schema)
        return {"data": _iev()}
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake_llm)
    out = svc.evaluate_item(object(), "gpt-x", {"key": "k"}, _item(), now=NOW)       # actor_profile
    assert out["mode"] == "interpretive" and out["eligible"] is True
    assert "facts" in seen["schema"]["properties"] and "解釈" in seen["system"]
    assert out["cited_domains"] == ["reuters.com", "state.gov"]


def test_interpretive_decision_passes_even_if_the_interpretation_cannot_be_proven_by_sources():
    """解釈は『出典で証明できない』のが普通。事実と矛盾しなければ、承認の妨げにしない（AIの見立てとして扱う）。"""
    ev = _iev(interpretation={"assessment": "partly_consistent", "concerns": ["言い過ぎの懸念"], "summary": "概ね整合"})
    assert svc.decide_interpretive(ev, _item(), SOURCES, now=NOW)["eligible"] is True


@pytest.mark.parametrize("override,expected", [
    ({"facts": [{"claim": "回数", "status": "contradicted", "source_numbers": [1]},
                {"claim": "a", "status": "verified", "source_numbers": [1]},
                {"claim": "b", "status": "verified", "source_numbers": [2]},
                {"claim": "c", "status": "verified", "source_numbers": [2]}]}, "事実の誤り"),
    ({"facts": [{"claim": "a", "status": "verified", "source_numbers": [1]},
                {"claim": "b", "status": "unverified", "source_numbers": []}]}, "確認できた事実が少ない"),
    ({"interpretation": {"assessment": "contradicted", "concerns": [], "summary": "矛盾"}}, "整合しない"),
    ({"interpretation": {"assessment": "cannot_judge", "concerns": [], "summary": "?"}}, "整合しない"),
    ({"confidence": "low"}, "確信度が低い"),
    ({"facts": [{"claim": "a", "status": "verified", "source_numbers": [2]},
                {"claim": "b", "status": "verified", "source_numbers": [3]},
                {"claim": "c", "status": "verified", "source_numbers": [2]}]}, "2つ未満"),   # 同じドメインだけ
])
def test_interpretive_decision_blocks_each_unmet_condition(override, expected):
    d = svc.decide_interpretive(_iev(**override), _item(), SOURCES, now=NOW)
    assert d["eligible"] is False and any(expected in r for r in d["reasons"])


def test_interpretive_approval_text_labels_the_analysis_as_ai_interpretation():
    c = _client(_item())
    res = {"knowledge_item_id": "k1", "eligible": True, "mode": "interpretive", "evaluation": _iev(), "sources": SOURCES,
           "cited_source_numbers": [1, 2], "cited_domains": ["reuters.com", "state.gov"]}
    assert svc.apply_ai_provisional_approval(c, res, model="gpt-x", now=NOW)["applied"] is True
    text = c.tables["geo_knowledge_items"][0]["confidence_rationale"]
    assert "3/4件を出典で確認" in text and "AIの見立て" in text and "専門家のコメントで随時更新" in text
    assert "state.gov" in text


def test_expert_comments_are_stored_listed_and_given_to_the_next_evaluation(monkeypatch):
    c = _client(_item())
    svc.add_expert_comment(c, "k1", author_id="analyst-1", author_role="analyst", comment="2025年の発言は撤回されている")
    comments = svc.list_expert_comments(c, "k1")
    assert comments[0]["comment"] == "2025年の発言は撤回されている" and comments[0]["author"] == "analyst-1"
    assert c.tables["geo_knowledge_items"][0]["status"] == "candidate"       # コメントだけでは内容も状態も変わらない

    monkeypatch.setattr(pplx, "search", lambda settings, **k: {"summary": "s", "found": True, "citations": []})
    seen = {}

    def fake_llm(cl, m, sp, up, schema, name, **kw):
        seen["user"] = up
        return {"data": _iev()}
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake_llm)
    svc.evaluate_item(object(), "m", {"key": "k"}, _item(), now=NOW, expert_comments=comments)
    assert "専門家のコメント" in seen["user"] and "2025年の発言は撤回されている" in seen["user"]


def test_empty_expert_comment_is_rejected():
    with pytest.raises(ValueError):
        svc.add_expert_comment(_client(_item()), "k1", author_id="a", author_role="analyst", comment="  ")


def test_interpretive_outdated_flag_is_a_note_not_a_blocker():
    ev = _iev(outdated=True, outdated_note="2026年9月に調査委員会法案が通過した")
    d = svc.decide_interpretive(ev, _item(), SOURCES, now=NOW)
    assert d["eligible"] is True and "調査委員会" in d["outdated_note"]      # 最新情報は、月次更新で取り込む


# ---------------------------------------------------------------------------
# 月次の最新動向の取り込み
# ---------------------------------------------------------------------------

def test_update_needed_when_recent_developments_missing_or_stale():
    assert svc.update_needed(_item(), now=NOW) is True                                          # 未取り込み
    fresh = _item(structured_content={"recent_developments": {"as_of": "2026-10-01"}})
    assert svc.update_needed(fresh, now=NOW) is False
    stale = _item(structured_content={"recent_developments": {"as_of": "2026-08-01"}})
    assert svc.update_needed(stale, now=NOW) is True                                            # 45日超
    assert svc.update_needed(_item(knowledge_type="precedent"), now=NOW) is False              # 事実中心の種類は対象外


def _recent_llm(items=None, note="現在の姿勢に変化は確認できない", none=False):
    def fake(c, m, sp, up, schema, name, **kw):
        fake.user = up
        return {"data": {"items": items if items is not None else [
            {"date": "2026-09-20", "event": "関税を再び発動した", "bearing": "confirms_profile", "source_numbers": [1, 2]},
            {"date": "2026-09-25", "event": "出典の無い出来事", "bearing": "new_information", "source_numbers": [99]}],
            "posture_note": note, "no_notable_change": none}}
    return fake


def test_fetch_recent_developments_keeps_only_events_tied_to_real_sources(monkeypatch):
    monkeypatch.setattr(pplx, "search", lambda settings, **k: {"summary": "直近の動き", "found": True, "citations": [
        {"url": x["url"], "title": x["title"], "published_at": x["date"]} for x in SOURCES]})
    fake = _recent_llm()
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake)
    out = svc.fetch_recent_developments(object(), "gpt-x", {"key": "k"}, _item(status="active"), now=NOW)
    rec = out["recent"]
    assert [e["event"] for e in rec["items"]] == ["関税を再び発動した"]          # 出典番号が実在しない出来事は載せない
    assert rec["items"][0]["sources"] == ["https://www.state.gov/a", "https://www.reuters.com/b"]
    assert rec["as_of"] == "2026-10-08" and rec["generated_by"] == "ai:gpt-x" and "AIによる月次更新" in rec["label"]
    assert "core_interests" in fake.user                                      # 基本プロファイルを踏まえて評価する


def test_apply_recent_developments_adds_a_section_without_touching_the_base_profile():
    base = {"entity_id": "e1", "core_interests": ["関税"], "decision_patterns": ["梃子として使う"]}
    c = _client(_item(status="active", retrieval_status="available", structured_content=dict(base)))
    res = {"knowledge_item_id": "k1", "recent": {"as_of": "2026-10-08", "items": [{"event": "x"}], "posture_note": "n"},
           "sources": SOURCES}
    out = svc.apply_recent_developments(c, res, model="gpt-x")
    assert out["applied"] is True
    content = c.tables["geo_knowledge_items"][0]["structured_content"]
    assert {k: content[k] for k in base} == base                              # 基本の人物像は、一切変わらない
    assert content["recent_developments"]["as_of"] == "2026-10-08"
    log = c.tables["geo_audit_logs"][0]
    assert log["action"] == "knowledge_item_recent_update" and log["detail"]["previous"] is None


def test_second_update_keeps_the_previous_snapshot_in_the_audit_log():
    c = _client(_item(status="active", retrieval_status="available",
                      structured_content={"recent_developments": {"as_of": "2026-09-08", "items": [{"event": "旧"}]}}))
    svc.apply_recent_developments(c, {"knowledge_item_id": "k1", "recent": {"as_of": "2026-10-08", "items": []},
                                      "sources": []}, model="m")
    assert c.tables["geo_audit_logs"][0]["detail"]["previous"]["items"] == [{"event": "旧"}]   # 前の状態が残る


def test_apply_recent_developments_skips_unapproved_items():
    c = _client(_item(status="candidate"))
    out = svc.apply_recent_developments(c, {"knowledge_item_id": "k1", "recent": {"as_of": "x"}}, model="m")
    assert out["applied"] is False
    assert "recent_developments" not in c.tables["geo_knowledge_items"][0]["structured_content"]


def test_recent_update_candidates_are_active_interpretive_items_that_need_an_update():
    c = _client(_item("a", status="active", retrieval_status="available"),
                _item("b", status="active", retrieval_status="available",
                      structured_content={"recent_developments": {"as_of": "2026-10-05"}}),
                _item("c", status="active", retrieval_status="available", knowledge_type="precedent"),
                _item("d", status="candidate"))
    assert [r["knowledge_item_id"] for r in svc.recent_update_candidates(c, now=NOW)] == ["a"]
    assert {r["knowledge_item_id"] for r in svc.recent_update_candidates(c, only_stale=False, now=NOW)} == {"a", "b"}


# ---------------------------------------------------------------------------
# 評価の安定化（2回評価・形式エラーのやり直し）
# ---------------------------------------------------------------------------

def _web(monkeypatch):
    monkeypatch.setattr(pplx, "search", lambda settings, **k: {"summary": "s", "found": True, "citations": [
        {"url": x["url"], "title": x["title"], "published_at": x["date"]} for x in SOURCES]})


def test_split_runs_trigger_a_third_run_and_majority_decides(monkeypatch):
    _web(monkeypatch)
    answers = iter([_ev(), _ev(confidence="low"), _ev()])       # ○ × → 3回目○ → 2/3で通る
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", lambda *a, **k: {"data": next(answers)})
    out = svc.evaluate_item(object(), "m", {"key": "k"}, _item(knowledge_type="precedent"), now=NOW)
    assert out["eligible"] is True and [r["eligible"] for r in out["runs"]] == [True, False, True]


def test_split_runs_with_failing_third_run_are_not_approved(monkeypatch):
    _web(monkeypatch)
    answers = iter([_ev(), _ev(confidence="low"), _ev(confidence="low")])      # ○ × × → 1/3
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", lambda *a, **k: {"data": next(answers)})
    out = svc.evaluate_item(object(), "m", {"key": "k"}, _item(knowledge_type="precedent"), now=NOW)
    assert out["eligible"] is False and "評価が回によって分かれた（1/3回" in out["reasons"][0]


def test_two_failing_runs_do_not_trigger_a_third_run(monkeypatch):
    _web(monkeypatch)
    calls = {"n": 0}

    def llm(*a, **k):
        calls["n"] += 1
        return {"data": _ev(confidence="low")}
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", llm)
    out = svc.evaluate_item(object(), "m", {"key": "k"}, _item(knowledge_type="precedent"), now=NOW)
    assert out["eligible"] is False and calls["n"] == 2


def test_item_passes_when_both_runs_agree(monkeypatch):
    _web(monkeypatch)
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", lambda *a, **k: {"data": _ev()})
    out = svc.evaluate_item(object(), "m", {"key": "k"}, _item(knowledge_type="precedent"), now=NOW)
    assert out["eligible"] is True and len(out["runs"]) == 2


def test_a_run_that_fails_is_retried_once_then_counted_against_approval(monkeypatch):
    _web(monkeypatch)
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise geo_llm_common.GeoLLMError("形式エラー", attempts=[], exception_type="X")   # 1回目の1度目だけ失敗
        return {"data": _ev()}
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", flaky)
    out = svc.evaluate_item(object(), "m", {"key": "k"}, _item(knowledge_type="precedent"), now=NOW)
    assert out["eligible"] is True and calls["n"] == 3                  # 失敗1回＋再試行＋2回目

    def always_fails(*a, **k):
        raise geo_llm_common.GeoLLMError("形式エラー", attempts=[], exception_type="X")
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", always_fails)
    with pytest.raises(RuntimeError):
        svc.evaluate_item(object(), "m", {"key": "k"}, _item(knowledge_type="precedent"), now=NOW)


def test_evaluation_schemas_tolerate_extra_keys_from_the_ai():
    import jsonschema
    ev = _iev()
    ev["interpretation"]["assessment_extra"] = "x"          # 余計な項目
    ev["notes"] = "余計な最上位項目"
    jsonschema.validate(ev, svc._INTERPRETIVE_SCHEMA)       # 以前は Additional properties not allowed で失敗
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"facts": []}, svc._INTERPRETIVE_SCHEMA)   # 必須項目の検証は、そのまま


# ---------------------------------------------------------------------------
# 精度向上: 事実の抜き出し → まとまりごとの検索 → 判定 → 「誤り」の再確認
# ---------------------------------------------------------------------------

CLAIMS = ["2024年に就任した", "関税を発動した", "同盟国と会談した", "声明を発表した", "法案に署名した"]


def _facts_flow(monkeypatch, *, judged, recheck=None, searches=None):
    """pplx と LLM を、段階ごとに差し替える。judged=判定のfacts、recheck=再確認の判定。"""
    searches = searches if searches is not None else []

    def fake_search(settings, **k):
        searches.append(k["question"])
        return {"summary": "要約", "found": True, "citations": [
            {"url": x["url"], "title": x["title"], "published_at": x["date"]} for x in SOURCES]}
    monkeypatch.setattr(pplx, "search", fake_search)
    seen = {"names": []}

    def fake_llm(c, m, sp, up, schema, name, **kw):
        seen["names"].append(name)
        seen.setdefault("users", {})[name] = up
        seen.setdefault("systems", {})[name] = sp
        if name == "knowledge_fact_extraction":
            return {"data": {"facts": [{"claim": c} for c in CLAIMS]}}
        if name == "knowledge_ai_review":
            return {"data": _iev(facts=judged)}
        if name == "knowledge_claim_recheck":
            return {"data": recheck}
        raise AssertionError(name)
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake_llm)
    return seen, searches


def _fact(claim, status, nums=(1,)):
    return {"claim": claim, "status": status, "source_numbers": list(nums)}


GOOD = [_fact(CLAIMS[0], "verified", (1,)), _fact(CLAIMS[1], "verified", (2,)), _fact(CLAIMS[2], "verified", (1, 2)),
        _fact(CLAIMS[3], "verified", (1,)), _fact(CLAIMS[4], "unverified", ())]


def test_facts_are_extracted_first_then_searched_in_separate_chunks(monkeypatch):
    seen, searches = _facts_flow(monkeypatch, judged=GOOD)
    out = svc._evaluate_once(object(), "m", {"key": "k"}, _item(), now=NOW)
    assert seen["names"][0] == "knowledge_fact_extraction" and out["extracted_fact_count"] == 5
    assert len(searches) == 2                                   # 5件 → 3件＋2件の2回に分けて検索
    assert "1. 2024年に就任した" in searches[0] and "4. " not in searches[0] and "声明を発表した" in searches[1]
    assert all(c in seen["users"]["knowledge_ai_review"] for c in CLAIMS)   # 抜き出した事実が、判定にそのまま渡る
    assert "評価・解釈・パターン" in seen["systems"]["knowledge_fact_extraction"]    # 評価や解釈は事実から除く
    assert out["eligible"] is True and out["mode"] == "interpretive"


def test_a_contradicted_claim_is_rechecked_alone_and_not_confirmed_becomes_unverified(monkeypatch):
    judged = [_fact(CLAIMS[0], "verified", (1,)), _fact(CLAIMS[1], "verified", (2,)), _fact(CLAIMS[2], "verified", (1,)),
              _fact(CLAIMS[3], "verified", (2,)), _fact(CLAIMS[4], "contradicted", (1,))]
    seen, searches = _facts_flow(monkeypatch, judged=judged,
                                 recheck={"verdict": "unclear", "note": "出典に記述が見つからない", "source_numbers": []})
    out = svc._evaluate_once(object(), "m", {"key": "k"}, _item(), now=NOW)
    assert "knowledge_claim_recheck" in seen["names"] and len(searches) == 3       # 2回のまとまり検索＋再確認1回
    assert "法案に署名した" in searches[-1] and "1. " not in searches[-1]            # 再確認は、その1件だけ
    last = out["evaluation"]["facts"][-1]
    assert last["status"] == "unverified" and out["confirmed_errors"] == []          # 確認できない≠誤り
    assert out["eligible"] is True and out["rechecks"][0]["verdict"] == "unclear"


def test_a_recheck_that_confirms_the_error_blocks_approval(monkeypatch):
    judged = [_fact(c, "verified", (1,)) for c in CLAIMS[:4]] + [_fact(CLAIMS[4], "contradicted", (2,))]
    _facts_flow(monkeypatch, judged=judged, recheck={"verdict": "confirmed_error", "note": "出典は反対を述べる", "source_numbers": [1]})
    out = svc._evaluate_once(object(), "m", {"key": "k"}, _item(), now=NOW)
    assert out["eligible"] is False and out["confirmed_errors"] == [CLAIMS[4]]
    assert any("事実の誤り" in r for r in out["reasons"])


def test_a_recheck_that_supports_the_claim_turns_it_into_verified_with_new_sources(monkeypatch):
    judged = [_fact(c, "verified", (1,)) for c in CLAIMS[:2]] + [_fact(CLAIMS[2], "contradicted", (1,)),
                                                                 _fact(CLAIMS[3], "unverified", ()), _fact(CLAIMS[4], "unverified", ())]
    _facts_flow(monkeypatch, judged=judged, recheck={"verdict": "supported", "note": "裏付けあり", "source_numbers": [1, 2]})
    out = svc._evaluate_once(object(), "m", {"key": "k"}, _item(), now=NOW)
    f = out["evaluation"]["facts"][2]
    assert f["status"] == "verified" and all(1 <= n <= len(out["sources"]) for n in f["source_numbers"])


def test_confirmed_error_in_any_run_blocks_even_a_majority(monkeypatch):
    bad = [_fact(c, "verified", (1,)) for c in CLAIMS[:4]] + [_fact(CLAIMS[4], "contradicted", (2,))]
    seq = iter([GOOD, bad, GOOD])                     # ○ × ○（2/3）だが、2回目に確定した誤りがある
    rechecks = iter([{"verdict": "confirmed_error", "note": "n", "source_numbers": [1]}])

    def fake_search(settings, **k):
        return {"summary": "s", "found": True, "citations": [{"url": x["url"], "title": x["title"], "published_at": x["date"]} for x in SOURCES]}
    monkeypatch.setattr(pplx, "search", fake_search)

    def fake_llm(c, m, sp, up, schema, name, **kw):
        if name == "knowledge_fact_extraction":
            return {"data": {"facts": [{"claim": c} for c in CLAIMS]}}
        if name == "knowledge_ai_review":
            return {"data": _iev(facts=next(seq))}
        return {"data": next(rechecks)}
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", fake_llm)
    out = svc.evaluate_item(object(), "m", {"key": "k"}, _item(), now=NOW)
    assert out["eligible"] is False and "再確認で確定" in out["reasons"][0]


def test_interpretation_stays_ai_view_and_is_not_extracted_as_fact():
    assert "『〜という評価がある』" in svc._FACTS_PROMPT and "項目自体の構成" in svc._FACTS_PROMPT
    assert "確認できないことは、誤りではない" in svc._RECHECK_PROMPT
