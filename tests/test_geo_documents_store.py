import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_documents_store as store  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

THEME = {"theme_id": "t-1", "code": "taiwan_strait"}


def test_compute_content_hash_is_stable():
    assert store.compute_content_hash(b"hello") == store.compute_content_hash(b"hello")
    assert store.compute_content_hash(b"hello") != store.compute_content_hash(b"world")


def test_extract_text_plain_text():
    result = store.extract_text(b"PLA aircraft crossed the median line.", "note.txt")
    assert result["ok"] is True
    assert "median line" in result["text"]


def test_save_document_writes_file_and_dedups(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "UPLOAD_ROOT", tmp_path)
    client = FakeSupabaseClient()
    raw = b"Taiwan MND reported PLA sorties near the median line today."

    first = store.save_document(
        client, theme_id=THEME["theme_id"], theme_code=THEME["code"], source_id=None,
        filename="report.txt", mime_type="text/plain", raw_bytes=raw, title="MND report",
    )
    assert first["is_new"] is True
    assert first["document"]["extracted_text"]
    saved_path = Path(first["document"]["storage_path"])
    assert saved_path.exists()

    second = store.save_document(
        client, theme_id=THEME["theme_id"], theme_code=THEME["code"], source_id=None,
        filename="report_copy.txt", mime_type="text/plain", raw_bytes=raw, title="duplicate upload",
    )
    assert second["is_new"] is False
    assert second["document"]["document_id"] == first["document"]["document_id"]


# ==================== Backfill（Corpus Coverage Audit v1回帰） ====================
# qualify_and_persist_citations経由で保存されたexternal_research文書は
# extracted_text=titleのみのstub。既存document_idをUPDATEするBackfill経路
# （INSERTしない、dry_run既定、Discovery provenanceを壊さない）の回帰テスト。

class _FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200, content_type: str = "text/html"):
        self.content = content
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}")


def _stub_row(**overrides):
    row = {
        "document_id": "doc-stub-1", "theme_id": THEME["theme_id"], "title": "Some Headline Only",
        "url": "https://example.gov/article", "canonical_url": "https://example.gov/article",
        "published_at": None, "extracted_text": "Some Headline Only",
        "content_hash": "hash-of-url-string", "research_origin": "external_research",
        "fetch_status": "成功", "is_mock": False,
    }
    row.update(overrides)
    return row


def test_backfill_stub_document_dry_run_does_not_write(monkeypatch):
    client = FakeSupabaseClient(tables={"geo_documents": [_stub_row()]})
    monkeypatch.setattr(store, "requests", type("R", (), {
        "get": staticmethod(lambda *a, **k: _FakeResponse(b"<html>fake</html>")),
        "RequestException": Exception,
    }))
    monkeypatch.setattr(store, "extract_text", lambda raw, filename, mime_type="": {
        "ok": True, "error": None, "title": "Real Article Title",
        "text": "This is the genuine full article body text, well over eighty characters long for sure.",
        "language": None, "published_at": "2026-09-01",
    })

    result = store.backfill_stub_document(client, "doc-stub-1", proxies={}, verify=True, dry_run=True)

    assert result["status"] == "dry_run"
    assert client.updated == []
    row = client.tables["geo_documents"][0]
    assert row["extracted_text"] == "Some Headline Only"  # 元のstubのまま


def test_backfill_stub_document_live_update_preserves_provenance(monkeypatch):
    client = FakeSupabaseClient(tables={"geo_documents": [_stub_row()]})
    monkeypatch.setattr(store, "requests", type("R", (), {
        "get": staticmethod(lambda *a, **k: _FakeResponse(b"<html>fake</html>")),
        "RequestException": Exception,
    }))
    monkeypatch.setattr(store, "extract_text", lambda raw, filename, mime_type="": {
        "ok": True, "error": None, "title": "Real Article Title",
        "text": "This is the genuine full article body text, well over eighty characters long for sure.",
        "language": None, "published_at": "2026-09-01",
    })

    result = store.backfill_stub_document(client, "doc-stub-1", proxies={}, verify=True, dry_run=False)

    assert result["status"] == "updated"
    row = client.tables["geo_documents"][0]
    # Discovery provenanceは維持
    assert row["document_id"] == "doc-stub-1"
    assert row["canonical_url"] == "https://example.gov/article"
    assert row["research_origin"] == "external_research"
    assert row["title"] == "Some Headline Only"
    # 本文・派生情報のみ更新
    assert row["extracted_text"] == "This is the genuine full article body text, well over eighty characters long for sure."
    assert row["content_hash"] != "hash-of-url-string"
    assert row["published_at"] == "2026-09-01"
    assert row["response_headers"]["backfill_status"] == "success"
    assert row["response_headers"]["pre_backfill_content_hash"] == "hash-of-url-string"


def test_backfill_stub_document_preserves_existing_published_at(monkeypatch):
    client = FakeSupabaseClient(tables={"geo_documents": [_stub_row(published_at="2026-01-01")]})
    monkeypatch.setattr(store, "requests", type("R", (), {
        "get": staticmethod(lambda *a, **k: _FakeResponse(b"<html>fake</html>")),
        "RequestException": Exception,
    }))
    monkeypatch.setattr(store, "extract_text", lambda raw, filename, mime_type="": {
        "ok": True, "error": None, "title": "Real Article Title",
        "text": "This is the genuine full article body text, well over eighty characters long for sure.",
        "language": None, "published_at": "2026-09-01",
    })

    store.backfill_stub_document(client, "doc-stub-1", proxies={}, verify=True, dry_run=False)

    row = client.tables["geo_documents"][0]
    assert row["published_at"] == "2026-01-01"  # 既存値を上書きしない


def test_backfill_stub_document_rejects_when_fetched_text_still_stub(monkeypatch):
    client = FakeSupabaseClient(tables={"geo_documents": [_stub_row()]})
    monkeypatch.setattr(store, "requests", type("R", (), {
        "get": staticmethod(lambda *a, **k: _FakeResponse(b"<html>fake</html>")),
        "RequestException": Exception,
    }))
    monkeypatch.setattr(store, "extract_text", lambda raw, filename, mime_type="": {
        "ok": True, "error": None, "title": "Some Headline Only",
        "text": "Some Headline Only", "language": None, "published_at": None,
    })

    result = store.backfill_stub_document(client, "doc-stub-1", proxies={}, verify=True, dry_run=False)

    assert result["status"] == "rejected"
    assert "fetched_text_still_stub" in result["reasons"]
    assert client.updated == []
    assert client.tables["geo_documents"][0]["extracted_text"] == "Some Headline Only"


def test_backfill_stub_document_rejects_duplicate_content_hash(monkeypatch):
    client = FakeSupabaseClient(tables={"geo_documents": [
        _stub_row(),
        {"document_id": "doc-other", "content_hash": "will-match-fetched-hash",
         "extracted_text": "Already have this one for real.", "title": "Other doc"},
    ]})
    monkeypatch.setattr(store, "requests", type("R", (), {
        "get": staticmethod(lambda *a, **k: _FakeResponse(b"<html>fake</html>")),
        "RequestException": Exception,
    }))
    monkeypatch.setattr(store, "extract_text", lambda raw, filename, mime_type="": {
        "ok": True, "error": None, "title": "Real Article Title",
        "text": "This is the genuine full article body text, well over eighty characters long for sure.",
        "language": None, "published_at": "2026-09-01",
    })
    monkeypatch.setattr(store, "compute_content_hash", lambda raw: "will-match-fetched-hash")

    result = store.backfill_stub_document(client, "doc-stub-1", proxies={}, verify=True, dry_run=False)

    assert result["status"] == "rejected"
    assert "duplicate_of_existing_document" in result["reasons"]
    assert result["duplicate_of"] == "doc-other"
    assert client.updated == []


def test_backfill_stub_document_fetch_failure_leaves_stub_untouched(monkeypatch):
    client = FakeSupabaseClient(tables={"geo_documents": [_stub_row()]})

    class _ConnectionError(Exception):
        response = None  # 実際のrequests.RequestExceptionと同じくresponse属性を持つ

    class _RaisingRequests:
        RequestException = _ConnectionError

        @staticmethod
        def get(*a, **k):
            raise _ConnectionError("boom")

    monkeypatch.setattr(store, "requests", _RaisingRequests)

    result = store.backfill_stub_document(client, "doc-stub-1", proxies={}, verify=True, dry_run=False)

    assert result["status"] == "fetch_failed"
    assert client.updated == []
    assert client.tables["geo_documents"][0]["extracted_text"] == "Some Headline Only"


def test_backfill_stub_document_no_url_rejected():
    client = FakeSupabaseClient(tables={"geo_documents": [_stub_row(url=None, canonical_url=None)]})
    result = store.backfill_stub_document(client, "doc-stub-1", proxies={}, verify=True, dry_run=True)
    assert result == {"status": "rejected", "document_id": "doc-stub-1", "reasons": ["no_url"]}


def test_backfill_stub_document_not_found():
    client = FakeSupabaseClient(tables={"geo_documents": []})
    result = store.backfill_stub_document(client, "missing-id", proxies={}, verify=True, dry_run=True)
    assert result == {"status": "not_found", "document_id": "missing-id"}
