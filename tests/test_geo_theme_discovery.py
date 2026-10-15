"""Article Discovery（テーマ探索開始 → 直近記事からTheme Candidateを提案）。SOURCE_INTELLIGENCE_V1_1。

LLMはモック、DBはFakeSupabaseClient。検証対象:
- 複数記事をまとめた候補を作り、根拠記事（タイトル/Source/公開日/元URL）が追える
- 由来(candidate_origin)の導出、AI提案はpending（自動で確定しない）
- 選択Sourceに限定／材料不足ならLLMを呼ばず候補を作らない
- 既存Candidate/Monitoring/削除済みと重複するときに新規を乱造しない
- 期間の優先順位、短期キャッシュ、実処理に連動する進行状況ジョブ
- 既存のSituation由来Candidate表示は従来どおり
"""
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_theme_discovery_service as disc  # noqa: E402
import geo_theme_explorer_service as explorer  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
PROMPT = "台湾海峡で新しく注目すべき動き"


def _ts(days=0):
    return (NOW - timedelta(days=days)).isoformat()


def _art(i, source_id, domain, title, summary="", days=1):
    return {"external_signal_id": f"a{i}", "source_id": source_id, "source_domain": domain, "source_name": domain,
            "title": title, "summary": summary, "url": f"https://{domain}/news/{i}", "published_at": _ts(days),
            "created_at": _ts(days)}


TAIWAN = [
    _art(1, "s1", "reuters.com", "PLA drills near Taiwan Strait intensify", "Chinese military exercises around Taiwan Strait rise", 1),
    _art(2, "s2", "scmp.com", "China coast guard patrols Taiwan Strait more often", "PLA and coast guard activity Taiwan Strait regular", 2),
    _art(3, "s3", "taipeitimes.com", "Taiwan reports daily PLA aircraft in Taiwan Strait", "Taiwan Strait PLA exercises now routine", 3),
    _art(4, "s2", "scmp.com", "Cross-Strait military activity becomes routine, analysts say", "Taiwan Strait PLA activity normalised", 4),
    _art(5, "s9", "gardening.example", "Spring planting tips", "tomatoes", 1),
]


def _client(articles=None, **tables):
    base = {"geo_external_signals": [dict(a) for a in (articles if articles is not None else TAIWAN)],
            "geo_theme_candidates": [], "geo_monitoring_themes": [], "geo_themes": [],
            "geo_source_registry": [{"source_id": "s1", "name": "Reuters", "url": "https://reuters.com", "archived_at": None},
                                    {"source_id": "s2", "name": "SCMP", "url": "https://scmp.com", "archived_at": None},
                                    {"source_id": "s3", "name": "Taipei Times", "url": "https://taipeitimes.com", "archived_at": None}],
            "geo_source_domains": [], "geo_source_sets": [], "geo_source_set_members": []}
    base.update(tables)
    return FakeSupabaseClient(base)


THEME = {"title": "中国が台湾周辺の軍事活動を常態化させる兆候", "short_description": "台湾海峡で人民解放軍の活動が日常化している",
         "observed_change": "単発の演習から恒常的な活動へ移っている可能性", "why_it_matters": "海運・調達リスクの前提が変わる可能性",
         "question": "常態化は今後も続くか", "actors": ["PLA", "台湾国防部"], "regions": ["台湾海峡"], "topics": ["軍事"],
         "candidate_type": "changing", "change_type": "actor_movement", "evidence_ids": [1, 2, 3]}


@pytest.fixture(autouse=True)
def _fresh_cache():
    disc.clear_cache()
    yield
    disc.clear_cache()


def _llm(monkeypatch, themes=None, *, period_days=0, no_theme_reason="", calls=None):
    calls = calls if calls is not None else []

    def fake(client, model, system, user, schema, name, **kw):
        calls.append((name, user))
        if name == "theme_search_interpretation":
            return {"data": {"keywords": ["台湾海峡"], "region": "台湾海峡", "new_only": True, "period_days": period_days}}
        if name == "source_query_expansion":
            return {"data": {"translated_query": "New developments to watch in the Taiwan Strait",
                             "key_concepts": ["PLA exercises", "Taiwan Strait"], "synonyms_and_names": ["PLA"],
                             "added_topics": [], "added_regions": [], "added_actors": [], "changed_time_range": False}}
        if name == "article_theme_discovery":
            return {"data": {"themes": themes if themes is not None else [dict(THEME)], "no_theme_reason": no_theme_reason}}
        raise AssertionError(name)

    monkeypatch.setattr(disc.geo_llm_common, "call_llm_structured", fake)
    monkeypatch.setattr(explorer.geo_llm_common, "call_llm_structured", fake)
    monkeypatch.setattr(disc.query_expansion.geo_llm_common, "call_llm_structured", fake)
    return calls


def _cluster_calls(calls):
    return [c for c in calls if c[0] == "article_theme_discovery"]


# --- A: 妥当な候補を作る／C: 根拠記事を追える ---------------------------------------------------

def test_discovery_bundles_multiple_articles_into_one_pending_candidate_with_traceable_evidence(monkeypatch):
    calls = _llm(monkeypatch)
    c = _client()
    res = disc.discover(c, object(), "gpt-x", prompt=PROMPT, now=NOW)
    assert len(res["candidates"]) == 1 and res["message"] is None
    cand = res["candidates"][0]
    assert cand["title"] == THEME["title"] and cand["status"] == "NEW"
    assert cand["candidate_origin"] == "article_discovery" and cand["candidate_origin_label"] == "記事から発見"
    assert cand["proposal_state"] == "pending"                                   # 自動で確定しない
    assert cand["generation_model"] == "article_discovery/gpt-x"
    assert len(cand["source_external_signal_ids"]) == 3                          # 1記事1テーマではない
    ev = cand["evidence_articles"]
    assert len(ev) == 3 and all(e["title"] and e["source"] and e["published_at"] and e["url"].startswith("https://") for e in ev)
    assert "gardening" not in str(ev)                                            # 無関係な記事は根拠にしない
    # 保存された行から、既存のEvidence/Handoffがそのまま辿れる（source_external_signal_idsに根拠を持つ）
    saved = c.tables["geo_theme_candidates"][0]
    assert set(saved["source_external_signal_ids"]) == {"a1", "a2", "a3"} and saved["signal_count"] == 3
    assert saved["first_seen_at"] == _ts(3) and saved["latest_signal_at"] == _ts(1)
    # LLMへ渡すのは関連記事だけ（無関係な記事を渡さない）
    user = _cluster_calls(calls)[0][1]
    assert "PLA drills near Taiwan Strait" in user and "Spring planting" not in user


def test_candidate_origin_is_derived_from_generation_model_and_situation_stays_default():
    assert explorer.candidate_origin({"generation_model": "article_discovery/gpt-4o"}) == "article_discovery"
    assert explorer.candidate_origin({"generation_model": "monitoring/rule"}) == "monitoring"
    assert explorer.candidate_origin({"generation_model": "manual/user"}) == "manual"
    assert explorer.candidate_origin({"generation_model": "gpt-4o"}) == "situation"        # 従来のSituation由来(V1のsync)
    assert explorer.candidate_origin({"generation_model": None}) == "situation"
    assert explorer.proposal_state({"generation_model": "gpt-4o", "status": "NEW"}) == "n/a"
    assert explorer.proposal_state({"generation_model": "article_discovery/x", "status": "NEW"}) == "pending"
    assert explorer.proposal_state({"generation_model": "article_discovery/x", "status": "MONITORING"}) == "decided"


# --- B: 選択Sourceに限定 ---------------------------------------------------------------------

def test_selected_sources_limit_retrieval_and_nothing_outside_is_used(monkeypatch):
    calls = _llm(monkeypatch, themes=[{**THEME, "evidence_ids": [1, 2]}])
    c = _client(geo_source_domains=[
        {"domain_id": "d1", "source_id": "s2", "domain": "scmp.com", "is_primary": True, "match_enabled": True},
        {"domain_id": "d2", "source_id": "s3", "domain": "taipeitimes.com", "is_primary": True, "match_enabled": True}])
    res = disc.discover(c, object(), "m", prompt=PROMPT, source_ids=["s2", "s3"], now=NOW)
    assert res["source_scope"] == {"mode": "selected", "source_count": 2}
    user = _cluster_calls(calls)[0][1]
    assert "scmp.com" in user and "taipeitimes.com" in user
    assert "reuters.com" not in user and "PLA drills near Taiwan Strait intensify" not in user   # 選択外(s1)は使わない
    for cand in res["candidates"]:
        assert {e["source"] for e in cand["evidence_articles"]} <= {"scmp.com", "taipeitimes.com"}


def test_source_set_is_resolved_and_archived_sources_are_excluded(monkeypatch):
    calls = _llm(monkeypatch, themes=[{**THEME, "evidence_ids": [1, 2]}])
    regs = [{"source_id": "s2", "name": "SCMP", "url": "https://scmp.com", "archived_at": None},
            {"source_id": "s3", "name": "TT", "url": "https://taipeitimes.com", "archived_at": _ts(1)}]
    extra = _art(6, "s2", "scmp.com", "PLA Taiwan Strait activity at record level", "Taiwan Strait PLA exercises record", 5)
    c = _client(articles=TAIWAN + [extra], geo_source_registry=regs,
                geo_source_sets=[{"source_set_id": "set1", "name": "台湾", "archived_at": None}],
                geo_source_set_members=[{"source_set_id": "set1", "source_id": "s2", "sort_order": 0},
                                        {"source_set_id": "set1", "source_id": "s3", "sort_order": 1}],
                geo_source_domains=[{"domain_id": "d1", "source_id": "s2", "domain": "scmp.com", "is_primary": True, "match_enabled": True}])
    res = disc.discover(c, object(), "m", prompt=PROMPT, source_set_id="set1", now=NOW)
    assert res["source_scope"]["source_count"] == 1                                # archived Sourceは対象外
    assert "taipeitimes.com" not in _cluster_calls(calls)[0][1]


# --- E: 材料不足なら無理に作らない -------------------------------------------------------------

def test_insufficient_articles_means_no_llm_call_no_candidate_and_clear_message(monkeypatch):
    calls = _llm(monkeypatch)
    c = _client(articles=TAIWAN[:2] + [TAIWAN[4]])                                  # 関連記事は2件だけ
    res = disc.discover(c, object(), "m", prompt=PROMPT, now=NOW)
    assert res["candidates"] == [] and res["message"] == "該当する新しいテーマ候補は見つかりませんでした"
    assert "不足" in res["reason"] and not _cluster_calls(calls)                    # LLMを呼ばない
    assert c.tables["geo_theme_candidates"] == []


def test_llm_returning_no_themes_creates_nothing(monkeypatch):
    _llm(monkeypatch, themes=[], no_theme_reason="継続して見るべき変化としてまとまらない")
    c = _client()
    res = disc.discover(c, object(), "m", prompt=PROMPT, now=NOW)
    assert res["candidates"] == [] and res["existing_updates"] == []
    assert res["message"] == "該当する新しいテーマ候補は見つかりませんでした" and "まとまらない" in res["reason"]
    assert c.tables["geo_theme_candidates"] == []


def test_validate_themes_rejects_single_article_single_source_and_invented_ids():
    arts = [dict(a) for a in TAIWAN[:4]]
    good = {**THEME, "evidence_ids": [1, 3]}                                         # 2記事・2Source → 可
    one_article = {**THEME, "title": "一件だけ", "evidence_ids": [2]}                # 1記事1テーマ → 不可
    ghost = {**THEME, "title": "存在しない番号", "evidence_ids": [99, 100]}           # 実在しない番号 → 不可
    same_source_two = {**THEME, "title": "同一Sourceの2記事", "evidence_ids": [2, 4]}   # 1Sourceの2記事だけ → 不可
    same_source_three_arts = [dict(a, source_domain="x.com", source_name="x.com") for a in arts[:3]]
    out = disc.validate_themes([good, one_article, ghost, same_source_two], arts)
    assert [t["title"] for t in out] == [THEME["title"]]
    assert len(disc.validate_themes([{**THEME, "evidence_ids": [1, 2, 3]}], same_source_three_arts)) == 1   # 同一Sourceでも3記事なら可
    # 同じ根拠の別テーマは1件にまとめる
    dup = disc.validate_themes([good, {**good, "title": "言い換え"}], arts)
    assert len(dup) == 1


# --- D: 重複Candidateを乱造しない ---------------------------------------------------------------

def test_overlapping_existing_candidate_becomes_an_update_not_a_new_candidate(monkeypatch):
    _llm(monkeypatch)
    existing = {"candidate_id": "cand-old", "title": "台湾周辺での軍事的緊張の高まり", "status": "SHORTLISTED",
                "source_external_signal_ids": ["a1", "a2"], "generation_model": "gpt-4o"}
    c = _client(geo_theme_candidates=[existing])
    res = disc.discover(c, object(), "m", prompt=PROMPT, now=NOW)
    assert res["candidates"] == [] and len(c.tables["geo_theme_candidates"]) == 1       # 新規を作らない
    assert len(res["existing_updates"]) == 1
    up = res["existing_updates"][0]
    assert up["kind"] == "existing_update" and up["existing"]["id"] == "cand-old" and up["has_new_articles"]
    assert {a["external_signal_id"] for a in up["new_articles"]} == {"a3"}                # 新しい動き=既存に無い記事
    assert up["new_articles"][0]["url"] and up["new_articles"][0]["source"]
    assert res["message"] is None                                                         # 「既存に新しい動き」があるので0件扱いにしない


def test_similar_title_to_a_monitoring_theme_is_reported_as_existing_not_created(monkeypatch):
    _llm(monkeypatch)
    c = _client(geo_monitoring_themes=[{"monitoring_theme_id": "m1", "display_name": "中国が台湾周辺の軍事活動を常態化", "status": "active",
                                        "theme_candidate_id": None}])
    res = disc.discover(c, object(), "m", prompt=PROMPT, now=NOW)
    assert res["candidates"] == [] and res["existing_updates"][0]["existing"]["type"] == "monitoring"
    assert c.tables["geo_theme_candidates"] == []


def test_dismissed_candidate_is_not_resurrected(monkeypatch):
    _llm(monkeypatch)
    gone = {"candidate_id": "cand-del", "title": "台湾海峡の軍事活動", "status": "DISMISSED",
            "source_external_signal_ids": ["a1", "a2", "a3"], "generation_model": "article_discovery/x"}
    c = _client(geo_theme_candidates=[gone])
    res = disc.discover(c, object(), "m", prompt=PROMPT, now=NOW)
    assert res["candidates"] == [] and res["stats"]["skipped_deleted"] == 1
    assert len(c.tables["geo_theme_candidates"]) == 1 and res["message"] == "該当する新しいテーマ候補は見つかりませんでした"


def test_repeating_the_same_exploration_does_not_duplicate_candidates(monkeypatch):
    _llm(monkeypatch)
    c = _client()
    disc.discover(c, object(), "m", prompt=PROMPT, now=NOW, use_cache=False)
    res2 = disc.discover(c, object(), "m", prompt=PROMPT, now=NOW, use_cache=False)       # キャッシュ無しでもfingerprint/重なりで重複しない
    assert len(c.tables["geo_theme_candidates"]) == 1 and res2["candidates"] == []
    assert len(res2["existing_updates"]) == 1 and not res2["existing_updates"][0]["has_new_articles"]


# --- 期間 / キャッシュ -------------------------------------------------------------------------

def test_period_precedence_explicit_then_prompt_then_default_30(monkeypatch):
    _llm(monkeypatch, themes=[], period_days=0)
    c = _client()
    assert disc.discover(c, object(), "m", prompt=PROMPT, now=NOW, use_cache=False)["period_days"] == 30      # 既定
    _llm(monkeypatch, themes=[], period_days=7)
    assert disc.discover(c, object(), "m", prompt=PROMPT, now=NOW, use_cache=False)["period_days"] == 7        # 入力の期間指定を優先
    assert disc.discover(c, object(), "m", prompt=PROMPT, period_days=14, now=NOW, use_cache=False)["period_days"] == 14
    assert disc.discover(c, object(), "m", prompt=PROMPT, period_days=9999, now=NOW, use_cache=False)["period_days"] == 180   # 上限


def test_old_articles_outside_the_period_are_not_used(monkeypatch):
    calls = _llm(monkeypatch, themes=[])
    old = [dict(a, published_at=_ts(100), created_at=_ts(100)) for a in TAIWAN[:4]]
    res = disc.discover(_client(articles=old), object(), "m", prompt=PROMPT, now=NOW)
    assert res["stats"]["articles_considered"] == 0 and not _cluster_calls(calls)


def test_same_query_is_served_from_short_cache_but_recomputed_after_a_delete(monkeypatch):
    calls = _llm(monkeypatch)
    c = _client()
    first = disc.discover(c, object(), "m", prompt=PROMPT, now=NOW)
    second = disc.discover(c, object(), "m", prompt=PROMPT, now=NOW)
    assert second["from_cache"] is True and len(_cluster_calls(calls)) == 1                 # LLMは1回だけ
    assert [x["candidate_id"] for x in second["candidates"]] == [x["candidate_id"] for x in first["candidates"]]
    c.tables["geo_theme_candidates"][0]["status"] = "DISMISSED"                              # ユーザーが削除
    third = disc.discover(c, object(), "m", prompt=PROMPT, now=NOW)
    assert third["from_cache"] is False                                                      # 削除を反映するため再計算


# --- 進行状況（実処理に連動）／エラー -------------------------------------------------------------

def test_job_steps_follow_the_real_processing_stages(monkeypatch):
    _llm(monkeypatch)
    c = _client()
    seen = []
    orig = disc.cluster_with_llm

    def spy(*a, **k):
        # 「テーマ候補を整理」段階に入った時点で、前の2段階は完了・この段階がrunning
        job = disc._JOBS[job_id_holder["id"]]
        seen.append([(s["key"], s["state"]) for s in job["steps"]])
        return orig(*a, **k)

    monkeypatch.setattr(disc, "cluster_with_llm", spy)
    job_id_holder = {}
    job_id = disc.start_job(c, object(), "m", prompt=PROMPT)
    job_id_holder["id"] = job_id
    for _ in range(100):
        job = disc.get_job(job_id)
        if job["status"] != "running":
            break
        time.sleep(0.05)
    assert job["status"] == "done"
    assert [s["state"] for s in job["steps"]] == ["done", "done", "done"]
    assert [s["label"] for s in job["steps"]] == ["入力を解釈", "関連記事を検索", "テーマ候補を整理"]
    if seen:
        assert seen[0] == [("interpret", "done"), ("search", "done"), ("cluster", "running")]
    assert len(job["result"]["candidates"]) == 1
    assert disc.get_job("nope") is None


def test_job_reports_a_readable_error_when_ai_is_unavailable():
    c = _client()
    job_id = disc.start_job(c, None, "m", prompt=PROMPT)                                    # AI未設定
    for _ in range(100):
        job = disc.get_job(job_id)
        if job["status"] != "running":
            break
        time.sleep(0.05)
    assert job["status"] == "error" and "AI" in job["error"]
    assert c.tables["geo_theme_candidates"] == []


def test_empty_prompt_is_rejected():
    with pytest.raises(disc.DiscoveryError):
        disc.discover(_client(), object(), "m", prompt="  ")


# --- F: Situation由来Candidateも従来どおり表示 / G: 既存Actionの構造維持 ---------------------------------

def test_situation_candidates_still_listed_with_origin_and_evidence_and_new_ones_appear_alongside(monkeypatch):
    _llm(monkeypatch)
    situation = {"candidate_id": "sit1", "title": "米中半導体規制の運用変化", "status": "NEW", "is_demo": False, "actors": ["BIS"],
                 "regions": ["米国"], "themes": ["半導体"], "business_relevance": ["supply_chain"], "candidate_type": "changing",
                 "change_type": "policy_shift", "signal_count": 1, "source_external_signal_ids": ["a5"],
                 "first_seen_at": _ts(5), "latest_signal_at": _ts(1), "updated_at": _ts(1), "generation_model": "gpt-4o",
                 "question": "q", "why_now": "w", "observed_change": "o", "signal_summary": "s"}
    c = _client(geo_theme_candidates=[situation])
    disc.discover(c, object(), "m", prompt=PROMPT, now=NOW)
    listing = explorer.explore(c, None, None, now=NOW)
    by_id = {t["candidate_id"]: t for t in listing["themes"]}
    assert len(by_id) == 2 and listing["available_total"] == 2
    sit = by_id["sit1"]
    assert sit["candidate_origin"] == "situation" and sit["candidate_origin_label"] == "Situationから発見" and sit["proposal_state"] == "n/a"
    assert [e["external_signal_id"] for e in sit["evidence_articles"]] == ["a5"]             # Situation由来にも根拠記事を出せる
    new = next(t for t in listing["themes"] if t["candidate_origin"] == "article_discovery")
    assert new["proposal_state"] == "pending" and len(new["evidence_articles"]) == 3


def test_article_candidates_work_with_existing_evidence_and_handoff_inputs(monkeypatch):
    """既存のEvidence取得/Handoffは source_external_signal_ids を使う。記事由来の候補もそのまま辿れる。"""
    import geo_theme_candidate_service as cs
    _llm(monkeypatch)
    c = _client()
    res = disc.discover(c, object(), "m", prompt=PROMPT, now=NOW)
    cid = res["candidates"][0]["candidate_id"]
    evidence = cs.get_candidate_evidence(c, cid)
    assert {s["external_signal_id"] for s in evidence["external_signals"]} == {"a1", "a2", "a3"}
    assert cs.update_candidate_status(c, cid, "SHORTLISTED", actor_id="u")["status"] == "SHORTLISTED"   # 却下/確定などの既存lifecycle


def test_discover_api_requires_identity_and_job_lookup_404():
    import geo_ios_identity
    import geo_monitoring_theme_api as api
    post = next(r for r in api.geo_theme_explorer_router.routes if r.path.endswith("/discover") and "POST" in r.methods)
    assert any(d.call is geo_ios_identity.verify_ios_identity for d in post.dependant.dependencies)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        api.get_discovery("no-such-job")
    assert ei.value.status_code == 404


def test_theme_with_only_the_minimum_fields_is_accepted_with_defaults():
    """モデルがcandidate_type等を省略しても探索全体を失敗させず、既定値で補う。"""
    arts = [dict(a) for a in TAIWAN[:4]]
    minimal = {"title": "台湾周辺の活動の常態化", "short_description": "常態化している", "evidence_ids": [1, 3]}
    out = disc.validate_themes([minimal], arts)
    assert len(out) == 1 and out[0]["candidate_type"] == "emerging" and out[0]["change_type"] == "signal_spike"
    assert out[0]["actors"] == [] and out[0]["question"] == "" and out[0]["why_it_matters"] == ""
    row = disc._row_for(out[0], "m")                       # 保存行は必須列(NOT NULL)をすべて埋める
    for col in ("title", "signal_summary", "observed_change", "why_now", "question"):
        assert row[col]
    assert row["candidate_type"] in disc.CANDIDATE_TYPES and row["change_type"] in disc.CHANGE_TYPES


def test_generic_words_are_not_used_as_search_terms():
    expansion = {"search_terms": [{"term": "recently", "kind": "translated"}, {"term": "notable", "kind": "translated"},
                                  {"term": "taiwan", "kind": "translated"}, {"term": "strait", "kind": "translated"},
                                  {"term": "PLA", "kind": "expanded"}]}
    terms = disc.build_search_terms({"keywords": ["台湾海峡"], "region": None}, expansion)
    assert {t["term"] for t in terms} == {"taiwan", "strait", "PLA", "台湾海峡"}
    req = disc.discovery_required(terms)
    assert req["english"] == 1 and req["original"] == 1          # 探索は一般語を除いた検索語が1語でも一致すれば候補（Researchの最大3語より広く集め、精度はスコア順・LLM・検証で担保）


def test_short_acronyms_match_whole_words_only_not_inside_other_words():
    terms = [{"term": "PLA", "kind": "expanded"}, {"term": "strait", "kind": "translated"}, {"term": "台湾海峡", "kind": "original"}]
    hit = lambda title, summary="": [h["term"] for h in disc.term_hits({"title": title, "summary": summary}, terms)]  # noqa: E731
    assert hit("Spring planting tips", "tomatoes") == []                       # "PLA" は "planting" に一致しない
    assert hit("PLA drills continue") == ["PLA"]
    assert hit("The PLA's exercises", "straits closed") == ["PLA", "strait"]  # 複数形・所有格は許容
    assert hit("台湾海峡の緊張") == ["台湾海峡"]                                 # 日本語は部分一致
    assert hit("Unplanned events") == []
