"""地政学アプリClooseout Phase2: Intelligence LibraryへのReport統合（検索・一覧表示）の検証。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_service as report_service  # noqa: E402
import geo_search_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

THEME_ID = "theme-1"


def _base_tables() -> dict:
    return {
        "geo_themes": [{"theme_id": THEME_ID, "code": "test_theme", "name_ja": "テストテーマ"}],
        "geo_entities": [{"entity_id": "entity-iran", "canonical_name": "Iran", "entity_type": "country",
                          "is_demo": False}],
        "geo_entity_issues": [{"entity_id": "entity-iran", "issue_id": "issue-1"}],
        "geo_issues": [{"issue_id": "issue-1", "theme_id": THEME_ID, "code": "test-issue"}],
    }


def _ingest(client, tmp_path, monkeypatch, *, filename, text, **kwargs):
    monkeypatch.setattr(report_service.geo_documents_store, "UPLOAD_ROOT", tmp_path)
    return report_service.ingest_report(
        client, filename=filename, mime_type="text/plain", raw_bytes=text.encode("utf-8"),
        country_region=["Iran"], actor_id="analyst_taro", **kwargs,
    )


def test_search_reports_matches_by_author(tmp_path, monkeypatch):
    client = FakeSupabaseClient(tables=_base_tables())
    _ingest(client, tmp_path, monkeypatch, filename="a.txt", text="内容A",
           author="yamada_analyst", title="レポートA")

    results = geo_search_service.search_reports(client, "yamada", include_demo=True)

    assert len(results) == 1
    assert results[0]["author"] == "yamada_analyst"


def test_search_reports_matches_by_report_type(tmp_path, monkeypatch):
    client = FakeSupabaseClient(tables=_base_tables())
    _ingest(client, tmp_path, monkeypatch, filename="a.txt", text="内容A",
           report_type="quarterly_briefing", title="レポートA")

    results = geo_search_service.search_reports(client, "quarterly", include_demo=True)

    assert len(results) == 1
    assert results[0]["report_type"] == "quarterly_briefing"


def test_search_reports_matches_by_document_title(tmp_path, monkeypatch):
    client = FakeSupabaseClient(tables=_base_tables())
    _ingest(client, tmp_path, monkeypatch, filename="a.txt", text="内容A",
           title="ホルムズ海峡最新動向レポート")

    results = geo_search_service.search_reports(client, "ホルムズ", include_demo=True)

    assert len(results) == 1


def test_search_reports_does_not_match_non_report_documents(tmp_path, monkeypatch):
    """geo_reportsに紐づかないgeo_documents（World Bank等の他Connector由来）は
    titleが一致しても誤って混入しないこと。"""
    client = FakeSupabaseClient(tables=_base_tables())
    _ingest(client, tmp_path, monkeypatch, filename="a.txt", text="内容A", title="無関係タイトル")
    # 他Connector由来の、Reportではない文書（geo_reports行が無い）
    client.tables["geo_documents"].append({
        "document_id": "doc-worldbank-1", "theme_id": THEME_ID, "title": "ホルムズ海峡 GDP統計",
        "is_demo": False,
    })

    results = geo_search_service.search_reports(client, "ホルムズ", include_demo=True)

    assert results == []


def test_list_reports_attaches_document_title_and_theme_name(tmp_path, monkeypatch):
    client = FakeSupabaseClient(tables=_base_tables())
    _ingest(client, tmp_path, monkeypatch, filename="a.txt", text="内容A", title="レポートA")

    reports = report_service.list_reports(client)

    assert len(reports) == 1
    assert reports[0]["document_title"] == "レポートA"
    assert reports[0]["theme_name"] == "テストテーマ"


def test_report_service_search_reports_attaches_document_summaries(tmp_path, monkeypatch):
    client = FakeSupabaseClient(tables=_base_tables())
    _ingest(client, tmp_path, monkeypatch, filename="a.txt", text="内容A",
           author="yamada_analyst", title="レポートA")

    results = report_service.search_reports(client, "yamada", include_demo=True)

    assert len(results) == 1
    assert results[0]["document_title"] == "レポートA"
