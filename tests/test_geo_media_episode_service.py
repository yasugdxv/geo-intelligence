"""地政学ナレッジ＆インテリジェンスDB Phase2A: geo_media_episode_service.pyのテスト。

Media Safety: domain対応表に無いdomainはCandidate化しない、GKG document単位のfingerprint、
related_external_event_idsは補助参照でありEvent別のtone帰属をしないこと、bias/editorial
intent等の列がスキーマに存在しないことを検証する。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_media_episode_service as svc  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _annotation(record_id="rec-1", signal_id="s1", tone=-2.0):
    return {
        "provider_record_id": record_id, "external_signal_id": signal_id, "annotation_id": f"ann-{record_id}",
        "document_identifier": f"https://reuters.com/{record_id}",
        "persons": [{"name": "Donald Trump", "char_offset": "10"}],
        "organizations": [], "themes": ["TAX_FNCACT"], "tone": tone, "source_common_name": "reuters.com",
    }


def test_match_media_source_known_domain():
    assert svc.match_media_source("reuters.com") == "reuters"
    assert svc.match_media_source("www.reuters.com") == "reuters"


def test_generate_candidates_skips_unmatched_domain():
    """domain対応表に無いdomain(例: redstate.com)はCandidate化しない。"""
    client = FakeSupabaseClient()
    ann = _annotation()
    ann["document_identifier"] = "https://redstate.com/rec-1"
    signals_by_id = {"s1": {"source_domain": "redstate.com", "title": "t", "published_at": None}}
    result = svc.generate_candidates_for_batch(
        client, annotations=[ann], signals_by_id=signals_by_id, ingestion_run_id="run-1")
    assert result["created"] == 0
    assert result["skipped_unmatched_domain"] == 1
    assert client.tables.get("geo_media_episode_candidates", []) == []


def test_generate_candidates_creates_for_matched_domain():
    client = FakeSupabaseClient()
    ann = _annotation()
    signals_by_id = {"s1": {"source_domain": "reuters.com", "title": "Test headline", "published_at": "2026-09-01T00:00:00+00:00"}}
    result = svc.generate_candidates_for_batch(
        client, annotations=[ann], signals_by_id=signals_by_id, ingestion_run_id="run-1")
    assert result["created"] == 1
    row = client.tables["geo_media_episode_candidates"][0]
    assert row["media_source_id"] == "reuters"
    assert row["headline"] == "Test headline"
    assert row["tone"] == -2.0


def test_compute_fingerprint_is_document_level():
    """同一媒体×異なるGKGRECORDIDは別candidateになる（2回目レビュー④の回帰テスト）。"""
    client = FakeSupabaseClient()
    ann1 = _annotation(record_id="rec-1", signal_id="s1")
    ann2 = _annotation(record_id="rec-2", signal_id="s2")
    signals_by_id = {"s1": {"source_domain": "reuters.com", "title": "A", "published_at": None},
                      "s2": {"source_domain": "reuters.com", "title": "B", "published_at": None}}
    result = svc.generate_candidates_for_batch(
        client, annotations=[ann1, ann2], signals_by_id=signals_by_id, ingestion_run_id="run-1")
    assert result["created"] == 2
    rows = client.tables["geo_media_episode_candidates"]
    assert rows[0]["fingerprint"] != rows[1]["fingerprint"]


def test_generate_candidates_idempotent_same_document_not_duplicated():
    client = FakeSupabaseClient()
    ann = _annotation()
    signals_by_id = {"s1": {"source_domain": "reuters.com", "title": "t", "published_at": None}}
    svc.generate_candidates_for_batch(client, annotations=[ann], signals_by_id=signals_by_id, ingestion_run_id="run-1")
    result2 = svc.generate_candidates_for_batch(client, annotations=[ann], signals_by_id=signals_by_id, ingestion_run_id="run-2")
    assert result2["created"] == 0
    assert len(client.tables["geo_media_episode_candidates"]) == 1


def test_domain_resolution_prefers_signal_source_domain_over_gkg():
    """Signal側source_domainとGKG側source_common_nameが異なる場合にSignal側が優先される
    （3回目レビュー推奨①の回帰テスト）。"""
    client = FakeSupabaseClient()
    ann = _annotation()
    ann["source_common_name"] = "some-other-site.com"  # GKG側は未収録domain
    signals_by_id = {"s1": {"source_domain": "reuters.com", "title": "t", "published_at": None}}  # Signal側は既知domain
    result = svc.generate_candidates_for_batch(
        client, annotations=[ann], signals_by_id=signals_by_id, ingestion_run_id="run-1")
    assert result["created"] == 1
    assert client.tables["geo_media_episode_candidates"][0]["media_source_id"] == "reuters"


def test_domain_resolution_falls_back_to_gkg_when_signal_domain_missing():
    client = FakeSupabaseClient()
    ann = _annotation()
    ann["source_common_name"] = "reuters.com"
    signals_by_id = {"s1": {"source_domain": None, "title": "t", "published_at": None}}
    result = svc.generate_candidates_for_batch(
        client, annotations=[ann], signals_by_id=signals_by_id, ingestion_run_id="run-1")
    assert result["created"] == 1


def test_related_event_ids_are_auxiliary_not_tone_attribution():
    """related_external_event_idsに値が入っていてもtone列はdocument自体の値のままで
    Event別に変化しないことを確認する。"""
    client = FakeSupabaseClient()
    ann = _annotation(tone=-5.0)
    signals_by_id = {"s1": {"source_domain": "reuters.com", "title": "t", "published_at": None}}
    result = svc.generate_candidates_for_batch(
        client, annotations=[ann], signals_by_id=signals_by_id,
        related_event_ids_by_signal_id={"s1": ["e1", "e2", "e3"]}, ingestion_run_id="run-1")
    assert result["created"] == 1
    row = client.tables["geo_media_episode_candidates"][0]
    assert row["related_external_event_ids"] == ["e1", "e2", "e3"]
    assert row["tone"] == -5.0  # 複数Eventが関連していてもdocument自体のtoneのまま


def test_media_episode_candidate_schema_has_no_bias_columns():
    """bias/editorial_intent/pro_x等の列がテーブル定義に存在しないことを構造的に確認する。"""
    import re
    sql_path = Path(__file__).parent.parent / "sql" / "2026-09-08_geo_external_events.sql"
    sql_text = sql_path.read_text(encoding="utf-8")
    match = re.search(r"create table public\.geo_media_episode_candidates \((.*?)\);", sql_text, re.DOTALL)
    assert match, "geo_media_episode_candidatesのDDLが見つかりません"
    ddl_body = match.group(1).lower()
    for forbidden in ("bias", "editorial_intent", "pro_china", "anti_us", "agenda"):
        assert forbidden not in ddl_body, f"禁止語'{forbidden}'がDDLに含まれています"


def test_update_media_episode_candidate_status_does_not_touch_media_intelligence():
    client = FakeSupabaseClient(tables={"geo_media_episode_candidates": [
        {"media_episode_candidate_id": "c1", "candidate_status": "candidate"},
    ]})
    svc.update_media_episode_candidate_status(
        client, candidate_id="c1", candidate_status="accepted", reviewed_by="analyst1")
    assert client.tables["geo_media_episode_candidates"][0]["candidate_status"] == "accepted"
    assert not any(t == "geo_knowledge_items" for t, _, _ in client.updated)
