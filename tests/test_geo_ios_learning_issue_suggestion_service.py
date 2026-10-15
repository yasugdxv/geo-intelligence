"""Geopolitical Intelligence OS Phase H §28-31: Issue Link Suggestion / CQAS Signal集計のテスト。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_learning_candidate_service as learning_service  # noqa: E402
import geo_ios_learning_issue_suggestion_service as suggestion_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _seed_issues(client):
    return client.insert("geo_issues", [
        {"theme_id": "theme-1", "code": "US-CHN-TECH", "name_ja": "米中技術覇権競争",
         "name_en": "US-China Technology Competition Congress Senate House", "status": "有効"},
        {"theme_id": "theme-1", "code": "US-TRADE", "name_ja": "米国通商政策",
         "name_en": "US Trade Policy tariffs", "status": "有効"},
        {"theme_id": "theme-2", "code": "SAUDI-OIL", "name_ja": "サウジ石油政策",
         "name_en": "Saudi Arabia Oil Royal Family", "status": "有効"},
    ])


def test_suggest_related_issues_ranks_by_similarity():
    client = FakeSupabaseClient()
    _seed_issues(client)
    candidate = learning_service.create_candidate(
        client, candidate_type="attention_rule",
        observation="Expertは議会をSenate/Houseへ分解すべきと指摘",
        generalized_rule="Congress Senate House を独立Actorとして扱う",
        extraction_model="m1", trigger_context={"title": "Congress Senate House Attention Rule",
                                                 "trigger": "US Congress Senate House policy"})
    suggestions = suggestion_service.suggest_related_issues(client, candidate["candidate_id"], limit=5)
    assert suggestions
    assert suggestions[0]["name_ja"] == "米中技術覇権競争"
    assert all(s["score"] >= suggestion_service.SUGGESTION_MIN_SIMILARITY for s in suggestions)
    # 無関係なSaudi石油Issueは出てこない（十分な語彙の重なりが無い）
    assert "サウジ石油政策" not in [s["name_ja"] for s in suggestions]


def test_suggest_related_issues_unknown_candidate_raises():
    client = FakeSupabaseClient()
    _seed_issues(client)
    try:
        suggestion_service.suggest_related_issues(client, "does-not-exist")
        assert False, "should have raised"
    except learning_service.LearningCandidateError:
        pass


def test_suggestion_never_creates_a_link_itself():
    """§29絶対条件: Suggestion自体はgeo_knowledge_item_issuesへ何も書き込まない。"""
    client = FakeSupabaseClient()
    _seed_issues(client)
    candidate = learning_service.create_candidate(
        client, candidate_type="attention_rule", observation="obs",
        generalized_rule="Congress Senate House を独立Actorとして扱う", extraction_model="m1")
    suggestion_service.suggest_related_issues(client, candidate["candidate_id"])
    assert client.tables.get("geo_knowledge_item_issues", []) == []


def test_summarize_cqas_signals_counts_by_type():
    client = FakeSupabaseClient()
    for signal in ("generation_miss", "generation_miss", "ranking_miss", "selection_miss",
                   "validated_candidate", "validated_candidate", "validated_candidate"):
        learning_service.create_candidate(
            client, candidate_type="attention_rule", observation="obs", generalized_rule="rule",
            extraction_model="m1", trigger_context={"cqas_signal": {"signal": signal, "similarity": 0.5}})
    # cqas_signal無しの候補（analysis_session紐付け無しのケース）も混在させて無視されることを確認
    learning_service.create_candidate(
        client, candidate_type="reasoning_rule", observation="obs2", generalized_rule="rule2",
        extraction_model="m1")

    summary = suggestion_service.summarize_cqas_signals(client)
    assert summary["counts"] == {
        "generation_miss": 2, "ranking_miss": 1, "selection_miss": 1, "validated_candidate": 3}
    assert summary["total_with_signal"] == 7
    assert len(summary["drilldown"]) == 7
