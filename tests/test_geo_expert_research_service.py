"""Expert AI Phase R1 Chunk4: geo_expert_research_service.pyのテスト。

Research Sufficiency Gate（決定的）、External Research Adapter（Web検索、実Azure
呼び出しはMagicMockで代替）、Evidence Qualification（source_type/temporal_status
分類・永続化）を検証する。Web Searchが常にResearch Sufficiency Gateの後にのみ
起動すること、Evidence Qualificationを通過したものだけが永続化されることを
中心に確認する。
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_expert_research_service as research  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _base_client(**extra_tables) -> FakeSupabaseClient:
    tables = {"geo_documents": [], "geo_source_registry": []}
    tables.update(extra_tables)
    return FakeSupabaseClient(tables)


# ---------------------------------------------------------------------------
# assess_research_sufficiency
# ---------------------------------------------------------------------------

def test_sufficiency_insufficient_when_no_direct_evidence():
    result = research.assess_research_sufficiency({"document_ids": [], "evidence_ids": []})
    assert result["current_evidence"] == "insufficient"
    assert result["overall"] == "insufficient"
    assert result["direct_evidence_count"] == 0
    assert result["gaps"]


def test_sufficiency_partial_when_exactly_one_direct_item():
    result = research.assess_research_sufficiency({"document_ids": ["d-1"], "evidence_ids": []})
    assert result["current_evidence"] == "partial"


def test_sufficiency_sufficient_when_two_or_more_direct_items():
    result = research.assess_research_sufficiency({"document_ids": ["d-1", "d-2"], "evidence_ids": []})
    assert result["current_evidence"] == "sufficient"
    assert result["gaps"] == []


def test_sufficiency_counts_documents_and_evidence_ids_deduplicated():
    """document_idsとevidence_idsは異なるID空間だが、同じIDが重複して数えられない
    （setで合算する）ことを確認する。"""
    result = research.assess_research_sufficiency({"document_ids": ["x"], "evidence_ids": ["x"]})
    assert result["direct_evidence_count"] == 1
    assert result["current_evidence"] == "partial"


def test_sufficiency_institutional_coverage_full_when_active_profile_present():
    pack = {"institutions": [{"profile_source": "institution_intelligence_active"}]}
    result = research.assess_research_sufficiency({"document_ids": [], "evidence_ids": []}, pack)
    assert result["institutional_coverage"] == "sufficient"


def test_sufficiency_institutional_coverage_insufficient_when_no_profile():
    pack = {"institutions": [{"profile_source": "none_available"}]}
    result = research.assess_research_sufficiency({"document_ids": [], "evidence_ids": []}, pack)
    assert result["institutional_coverage"] == "insufficient"
    assert any("institutional_coverage" in g for g in result["gaps"])


# ---------------------------------------------------------------------------
# Phase R1 Chunk6後P0-1: Named Entity Coverage
# ---------------------------------------------------------------------------

def test_sufficiency_downgrades_when_named_entity_not_mentioned_in_evidence_text():
    """件数はsufficient閾値を満たしていても、クエリが名指ししたTSMCという固有名詞が
    Evidence本文に一切出現しない場合はpartialへ引き下げる（TSMCケースの実際の
    問題の再現防止）。"""
    source_pack = {
        "document_ids": ["d-1", "d-2"], "evidence_ids": [],
        "text": "半導体・輸出管理に関する一般的な記事。台湾の貿易政策について。",
    }
    result = research.assess_research_sufficiency(
        source_pack, query_text="台湾のTSMCおよび貿易・産業当局が対中半導体輸出規制にどう対応しているか")
    assert result["current_evidence"] == "partial"
    assert "TSMC" in result["named_entity_coverage_gaps"]
    assert any("named_entity_coverage_gap" in g for g in result["gaps"])


def test_sufficiency_stays_sufficient_when_named_entity_is_mentioned():
    source_pack = {
        "document_ids": ["d-1", "d-2"], "evidence_ids": [],
        "text": "TSMCが対中輸出許可の運用を厳格化しているとの報道。",
    }
    result = research.assess_research_sufficiency(
        source_pack, query_text="台湾のTSMCおよび貿易・産業当局が対中半導体輸出規制にどう対応しているか")
    assert result["current_evidence"] == "sufficient"
    assert result["named_entity_coverage_gaps"] == []


def test_sufficiency_named_entity_check_is_case_insensitive():
    source_pack = {"document_ids": ["d-1", "d-2"], "evidence_ids": [], "text": "tsmcの生産動向について。"}
    result = research.assess_research_sufficiency(source_pack, query_text="TSMCの対応について")
    assert result["named_entity_coverage_gaps"] == []


def test_sufficiency_no_query_text_keeps_existing_count_only_behavior():
    """query_text未指定時は既存の件数のみの判定を維持する（後方互換）。"""
    result = research.assess_research_sufficiency({"document_ids": ["d-1", "d-2"], "evidence_ids": []})
    assert result["current_evidence"] == "sufficient"
    assert result["named_entity_coverage_gaps"] == []


def test_sufficiency_ignores_expert_ai_prompt_vocabulary_as_named_entity():
    """実データで発見: ユーザーが依頼文中にExpert AI自身の出力語彙（Key Point/Main
    Scenario/Observable Indicators等）を英語で書くと、これらが固有名詞として誤抽出され
    named_entity_coverage_gapsがノイズだらけになっていた。stopwordとして除外することを
    確認する。"""
    source_pack = {"document_ids": ["d-1", "d-2"], "evidence_ids": [], "text": "何らかの分析本文"}
    query = ("中国によるレアアース輸出管理について、Key Point、Main Scenario、"
             "Alternative Scenario、Observable Indicatorsを整理してください。")
    result = research.assess_research_sufficiency(source_pack, query_text=query)
    assert result["named_entity_coverage_gaps"] == []
    assert result["current_evidence"] == "sufficient"


def test_sufficiency_does_not_downgrade_already_insufficient_case_via_named_entity():
    """既にinsufficientな場合は、named entity gapがあってもさらに悪化させない
    （partialへの引き下げはsufficient→partialの1方向のみ）。"""
    result = research.assess_research_sufficiency(
        {"document_ids": [], "evidence_ids": []}, query_text="TSMCについて")
    assert result["current_evidence"] == "insufficient"


# ---------------------------------------------------------------------------
# classify_source_type_from_url / classify_temporal_status（Evidence Qualification）
# ---------------------------------------------------------------------------

def test_classify_source_type_official_domains():
    assert research.classify_source_type_from_url("https://www.bis.doc.gov/some-rule") == "official_site"
    assert research.classify_source_type_from_url("https://nato.int/statement") == "official_site"


def test_classify_source_type_news_domains():
    assert research.classify_source_type_from_url("https://www.reuters.com/technology/x") == "news"


def test_classify_source_type_defaults_to_other_when_unknown():
    assert research.classify_source_type_from_url("https://some-random-blog.example.com/post") == "other"


def test_classify_source_type_does_not_trust_requested_lens():
    """Search Lensとしてprimary_officialを要求していても、実際のドメインが
    Reutersであればnewsとして分類されることを確認する（Evidence Qualificationの核心
    ——要求したLensをそのまま結果の分類として信用しない）。"""
    # classify_source_type_from_urlはLens自体を引数に取らない設計そのものが、
    # 「要求と実際を混同しない」ことを構造的に保証している。
    assert research.classify_source_type_from_url("https://www.reuters.com/x") == "news"


def test_classify_temporal_status_unknown_when_no_published_at():
    assert research.classify_temporal_status(None) == "unknown"


def test_classify_temporal_status_current_when_recent():
    from datetime import datetime, timezone, timedelta
    recent = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    assert research.classify_temporal_status(recent) == "current"


def test_classify_temporal_status_historical_when_old():
    from datetime import datetime, timezone, timedelta
    old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    assert research.classify_temporal_status(old) == "historical"


# ---------------------------------------------------------------------------
# get_or_create_domain_source
# ---------------------------------------------------------------------------

def test_get_or_create_domain_source_creates_new_row():
    client = _base_client()
    source = research.get_or_create_domain_source(
        client, theme_id="theme-1", domain="bis.doc.gov", source_type="official_site")
    assert source["name"] == "bis.doc.gov"
    assert source["source_type"] == "official_site"
    assert len(client.tables["geo_source_registry"]) == 1


def test_get_or_create_domain_source_is_idempotent_per_theme():
    client = _base_client()
    first = research.get_or_create_domain_source(
        client, theme_id="theme-1", domain="bis.doc.gov", source_type="official_site")
    second = research.get_or_create_domain_source(
        client, theme_id="theme-1", domain="bis.doc.gov", source_type="official_site")
    assert first["source_id"] == second["source_id"]
    assert len(client.tables["geo_source_registry"]) == 1


# ---------------------------------------------------------------------------
# qualify_and_persist_citations
# ---------------------------------------------------------------------------

def test_qualify_and_persist_citations_inserts_new_document():
    client = _base_client()
    citations = [{"url": "https://www.reuters.com/technology/some-article", "title": "Some Article"}]
    persisted = research.qualify_and_persist_citations(client, theme_id="theme-1", citations=citations)
    assert len(persisted) == 1
    assert persisted[0]["research_origin"] == "external_research"
    assert persisted[0]["evidence_class"] == "current_direct_evidence"
    assert len(client.tables["geo_documents"]) == 1
    assert len(client.tables["geo_source_registry"]) == 1  # reuters.comが新規登録される


def test_qualify_and_persist_citations_dedups_by_url_content_hash():
    """同一URLは既存content_hash一意制約と同じロジックで重複永続化されないことを
    確認する（同一テーマ・同一URLの再検索でも重複が増えない）。"""
    client = _base_client()
    citations = [{"url": "https://www.reuters.com/technology/some-article", "title": "Some Article"}]
    research.qualify_and_persist_citations(client, theme_id="theme-1", citations=citations)
    second = research.qualify_and_persist_citations(client, theme_id="theme-1", citations=citations)
    assert second == []
    assert len(client.tables["geo_documents"]) == 1


def test_qualify_and_persist_citations_skips_citations_without_url():
    client = _base_client()
    persisted = research.qualify_and_persist_citations(client, theme_id="theme-1", citations=[{"title": "no url"}])
    assert persisted == []


# ---------------------------------------------------------------------------
# run_external_research（実Azure呼び出しをMagicMockで代替）
# ---------------------------------------------------------------------------

def _mock_responses_client(output_text: str, citations: list) -> MagicMock:
    """responses.create(...)の戻り値をmockする。output_text経由でJSON抽出、
    output[].content[].annotations[].type=url_citation経由でcitations抽出、という
    article_analyzer.pyと同じ構造を模す。"""
    annotations = [
        MagicMock(type="url_citation", url=c["url"], title=c.get("title"))
        for c in citations
    ]
    content_item = MagicMock(annotations=annotations)
    output_item = MagicMock(type="message", content=[content_item])
    resp = MagicMock(output_text=output_text, output=[output_item], usage=None)
    client = MagicMock()
    client.responses.create.return_value = resp
    return client


def test_run_external_research_forces_web_search_tool():
    azure_client = _mock_responses_client(
        '検索の結果、○○が確認できました。\n```json\n{"summary": "○○", "found_relevant_information": true}\n```',
        [{"url": "https://www.bis.doc.gov/rule", "title": "BIS Rule"}],
    )
    result = research.run_external_research(azure_client, "gpt-4o", question="q", context="c")
    call_kwargs = azure_client.responses.create.call_args.kwargs
    assert call_kwargs["tools"] == [{"type": "web_search_preview"}]
    assert call_kwargs["tool_choice"] == "required"
    assert result["found"] is True
    assert result["citations"] == [{"url": "https://www.bis.doc.gov/rule", "title": "BIS Rule"}]


def test_run_external_research_only_trusts_url_citation_annotations():
    """本文中に自由記述URLが含まれていても、annotations(url_citation)以外は
    citationsに含まれないことを確認する（Web検索ツールの既知の幻覚URL問題への対策）。"""
    azure_client = _mock_responses_client(
        '本文中にhttps://fake-hallucinated-url.example.comと書いてあるかもしれない。\n'
        '```json\n{"summary": "x", "found_relevant_information": true}\n```',
        [],  # annotationsは空（本文中にURL文字列があってもcitationsとしては拾わない）
    )
    result = research.run_external_research(azure_client, "gpt-4o", question="q", context="c")
    assert result["citations"] == []


# ---------------------------------------------------------------------------
# run_research_cycle（Research Sufficiency Gateとの結線）
# ---------------------------------------------------------------------------

def test_run_research_cycle_skips_search_when_sufficient():
    """Evidence十分ならWeb検索しない（Chunk4 Stop Gate 1）。"""
    client = _base_client()
    azure_client = MagicMock()
    result = research.run_research_cycle(
        client, azure_client, "gpt-4o", theme_id="theme-1", query_text="q",
        sufficiency={"current_evidence": "sufficient"})
    assert result["triggered"] is False
    assert result["search_calls_used"] == 0
    assert not azure_client.responses.create.called


def test_run_research_cycle_searches_when_insufficient_and_persists_qualified_evidence():
    client = _base_client()
    azure_client = _mock_responses_client(
        '```json\n{"summary": "x", "found_relevant_information": true}\n```',
        [{"url": "https://www.bis.doc.gov/rule", "title": "BIS Rule"}],
    )
    result = research.run_research_cycle(
        client, azure_client, "gpt-4o", theme_id="theme-1", query_text="半導体規制",
        sufficiency={"current_evidence": "insufficient"})
    assert result["triggered"] is True
    assert result["search_calls_used"] == 1
    assert len(result["documents_added"]) == 1
    assert result["stop_condition"] == "sufficient_comparison"
    assert len(client.tables["geo_documents"]) == 1


def test_run_research_cycle_evidence_unobtainable_when_no_citations_found():
    """検索してもEvidence不足ならinsufficientのままReasoningできる（Chunk4
    Stop Gate 4）——例外にせず、stop_conditionとして正しく記録されることを確認する。"""
    client = _base_client()
    azure_client = _mock_responses_client(
        '```json\n{"summary": "見つかりませんでした", "found_relevant_information": false}\n```', [])
    result = research.run_research_cycle(
        client, azure_client, "gpt-4o", theme_id="theme-1", query_text="q",
        sufficiency={"current_evidence": "insufficient"})
    assert result["triggered"] is True
    assert result["documents_added"] == []
    assert result["stop_condition"] == "evidence_unobtainable"


def test_run_research_cycle_handles_external_research_failure_gracefully():
    """External Research自体が例外を起こしても、Reasoning自体は継続できるよう
    triggered=Trueかつdocuments_added=[]で正常に戻ることを確認する（例外を伝播させない）。"""
    client = _base_client()
    azure_client = MagicMock()
    azure_client.responses.create.side_effect = RuntimeError("network error")
    result = research.run_research_cycle(
        client, azure_client, "gpt-4o", theme_id="theme-1", query_text="q",
        sufficiency={"current_evidence": "insufficient"})
    assert result["triggered"] is True
    assert result["documents_added"] == []
    assert result["stop_condition"] == "evidence_unobtainable"
    assert "error" in result
