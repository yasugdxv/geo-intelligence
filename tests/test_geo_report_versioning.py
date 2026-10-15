"""地政学アプリClooseout Phase3: Report Update/Versioningの検証。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_service as report_service  # noqa: E402
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


def _ingest_v1(client, tmp_path, monkeypatch, *, text="v1本文", **kwargs):
    monkeypatch.setattr(report_service.geo_documents_store, "UPLOAD_ROOT", tmp_path)
    kwargs.setdefault("author", "analyst_taro")
    return report_service.ingest_report(
        client, filename="v1.txt", mime_type="text/plain", raw_bytes=text.encode("utf-8"),
        country_region=["Iran"], actor_id="analyst_taro", **kwargs,
    )


def test_ingest_report_v1_is_self_consistent_root_version(tmp_path, monkeypatch):
    client = FakeSupabaseClient(tables=_base_tables())
    result = _ingest_v1(client, tmp_path, monkeypatch)

    report = result["report"]
    assert report["version"] == 1
    assert report["supersedes_id"] is None
    assert report["report_identity_id"] == report["report_id"]


def test_create_report_version_from_latest_succeeds(tmp_path, monkeypatch):
    client = FakeSupabaseClient(tables=_base_tables())
    v1 = _ingest_v1(client, tmp_path, monkeypatch)["report"]

    result = report_service.create_report_version(
        client, supersedes_report_id=v1["report_id"], filename="v2.txt", mime_type="text/plain",
        raw_bytes="v2本文（内容変更あり）".encode("utf-8"), actor_id="analyst_taro")

    v2 = result["report"]
    assert v2["version"] == 2
    assert v2["supersedes_id"] == v1["report_id"]
    assert v2["report_identity_id"] == v1["report_identity_id"]
    assert v2["document_id"] != v1["document_id"]


def test_create_report_version_from_non_latest_is_rejected(tmp_path, monkeypatch):
    client = FakeSupabaseClient(tables=_base_tables())
    v1 = _ingest_v1(client, tmp_path, monkeypatch)["report"]
    report_service.create_report_version(
        client, supersedes_report_id=v1["report_id"], filename="v2.txt", mime_type="text/plain",
        raw_bytes="v2本文".encode("utf-8"), actor_id="analyst_taro")

    with pytest.raises(report_service.ReportVersionConflictError):
        report_service.create_report_version(
            client, supersedes_report_id=v1["report_id"], filename="v3-from-v1.txt", mime_type="text/plain",
            raw_bytes="v1から分岐しようとしたversion".encode("utf-8"), actor_id="analyst_taro")


def test_create_report_version_with_no_change_is_rejected(tmp_path, monkeypatch):
    client = FakeSupabaseClient(tables=_base_tables())
    v1 = _ingest_v1(client, tmp_path, monkeypatch, text="全く同じ本文")

    with pytest.raises(report_service.ReportVersionNoChangeError):
        report_service.create_report_version(
            client, supersedes_report_id=v1["report"]["report_id"], filename="v1-copy.txt",
            mime_type="text/plain", raw_bytes="全く同じ本文".encode("utf-8"), actor_id="analyst_taro")


def test_create_report_version_bypasses_ingest_report_idempotent_shortcut(tmp_path, monkeypatch):
    """create_report_versionがingest_reportをそのまま呼んでいたら、document_idの冪等ショート
    カットにより新版行が作られない不具合になる。新版行が実際に作られることを確認する。"""
    client = FakeSupabaseClient(tables=_base_tables())
    v1 = _ingest_v1(client, tmp_path, monkeypatch)["report"]

    report_service.create_report_version(
        client, supersedes_report_id=v1["report_id"], filename="v2.txt", mime_type="text/plain",
        raw_bytes="新しい内容".encode("utf-8"), actor_id="analyst_taro")

    assert len(client.tables["geo_reports"]) == 2


def test_get_latest_and_list_report_versions(tmp_path, monkeypatch):
    client = FakeSupabaseClient(tables=_base_tables())
    v1 = _ingest_v1(client, tmp_path, monkeypatch)["report"]
    v2 = report_service.create_report_version(
        client, supersedes_report_id=v1["report_id"], filename="v2.txt", mime_type="text/plain",
        raw_bytes="v2本文".encode("utf-8"), actor_id="analyst_taro")["report"]

    latest = report_service.get_latest_report_version(client, v1["report_identity_id"])
    assert latest["report_id"] == v2["report_id"]

    versions = report_service.list_report_versions(client, v1["report_identity_id"])
    assert [v["version"] for v in versions] == [1, 2]


def test_diff_report_versions_shows_text_and_metadata_changes(tmp_path, monkeypatch):
    client = FakeSupabaseClient(tables=_base_tables())
    v1 = _ingest_v1(client, tmp_path, monkeypatch, text="旧本文の内容", author="taro")["report"]
    v2 = report_service.create_report_version(
        client, supersedes_report_id=v1["report_id"], filename="v2.txt", mime_type="text/plain",
        raw_bytes="新本文の内容".encode("utf-8"), author="hanako", actor_id="analyst_taro")["report"]

    diff = report_service.diff_report_versions(client, v1["report_id"], v2["report_id"])

    assert any("旧本文の内容" in line for line in diff["text_diff"])
    assert any("新本文の内容" in line for line in diff["text_diff"])
    assert diff["metadata_diff"]["author"] == {"from": "taro", "to": "hanako"}


def test_list_reports_shows_latest_version_only(tmp_path, monkeypatch):
    client = FakeSupabaseClient(tables=_base_tables())
    v1 = _ingest_v1(client, tmp_path, monkeypatch)["report"]
    report_service.create_report_version(
        client, supersedes_report_id=v1["report_id"], filename="v2.txt", mime_type="text/plain",
        raw_bytes="v2本文".encode("utf-8"), actor_id="analyst_taro")

    reports = report_service.list_reports(client)

    assert len(reports) == 1
    assert reports[0]["version"] == 2


def test_ingest_report_idempotency_still_works_after_versioning_columns(tmp_path, monkeypatch):
    """通常のReport Upload（再アップロード時の冪等no-op）が、report_identity_id NOT NULL化後も
    壊れていないことを確認する。"""
    client = FakeSupabaseClient(tables=_base_tables())
    raw = "同一内容".encode("utf-8")
    monkeypatch.setattr(report_service.geo_documents_store, "UPLOAD_ROOT", tmp_path)
    r1 = report_service.ingest_report(client, filename="a.txt", mime_type="text/plain", raw_bytes=raw,
                                      country_region=["Iran"], actor_id="analyst_taro")
    r2 = report_service.ingest_report(client, filename="a.txt", mime_type="text/plain", raw_bytes=raw,
                                      country_region=["Iran"], actor_id="analyst_taro")

    assert r1["report"]["report_id"] == r2["report"]["report_id"]
    assert len(client.tables["geo_reports"]) == 1
    assert r1["report"]["version"] == 1
    assert r1["report"]["report_identity_id"] == r1["report"]["report_id"]
