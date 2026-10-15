import gzip
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_artifact_compression as compression  # noqa: E402
import geo_report_artifact_storage_service as storage  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

RUN_ID = "run-1"
EXECUTION_ID = "exec-1"


def test_small_content_goes_to_content_text_not_storage():
    client = FakeSupabaseClient()
    content = b"small json content"

    fields = storage.build_artifact_storage_fields(
        client, RUN_ID, EXECUTION_ID, "prompt_snapshot", "prompt1.md", content)

    assert fields["storage_path"] is None
    assert fields["storage_bucket"] is None
    assert compression.decompress_text(fields["content_text"]) == "small json content"
    assert fields["size_bytes"] == len(content)
    assert not getattr(client, "storage_objects", {})  # Storageへは一切書き込まれない


def test_large_content_goes_to_storage_not_content_text():
    client = FakeSupabaseClient()
    content = b"x" * (storage.STORAGE_SIZE_THRESHOLD_BYTES + 1)

    fields = storage.build_artifact_storage_fields(
        client, RUN_ID, EXECUTION_ID, "final_report", "final_report.html", content)

    assert fields["content_text"] is None
    assert fields["storage_bucket"] == storage.STORAGE_BUCKET
    assert fields["storage_path"] == f"{RUN_ID}/{EXECUTION_ID}/final_report/final_report.html"
    assert fields["size_bytes"] == len(content)
    # 2026-09-17変更: Storageへは転送量削減のためgzip圧縮して保存する
    # （sha256/size_bytesは圧縮前の実データ基準のまま）。
    stored = client.storage_objects[(storage.STORAGE_BUCKET, fields["storage_path"])]
    assert gzip.decompress(stored) == content


def test_large_compressed_content_is_split_into_chunks_and_reassembled_on_read():
    """2026-09-17追加、ユーザー提案「分割した方がいいのでは」への対応検証:
    圧縮後もSTORAGE_CHUNK_SIZE_BYTESを超える場合、複数のStorage objectへ分割
    アップロードされ、storage_pathに`#chunks=`マーカーが付き、読み込み側は
    透過的に結合できることを確認する（乱数バイト列はgzipでほぼ圧縮できないため、
    圧縮後サイズを確実にチャンク閾値超えにできる）。"""
    client = FakeSupabaseClient()
    content = os.urandom(int(storage.STORAGE_CHUNK_SIZE_BYTES * 2.5))

    fields = storage.build_artifact_storage_fields(
        client, RUN_ID, EXECUTION_ID, "prompt1_input", "huge.csv", content)

    assert storage._CHUNK_COUNT_MARKER in fields["storage_path"]
    chunk_count = int(fields["storage_path"].split(storage._CHUNK_COUNT_MARKER)[1])
    assert chunk_count >= 2
    for i in range(chunk_count):
        key = (storage.STORAGE_BUCKET, storage._chunk_object_path(fields["storage_path"].split("#")[0], i))
        assert key in client.storage_objects
        assert len(client.storage_objects[key]) <= storage.STORAGE_CHUNK_SIZE_BYTES

    # 実データはランダムバイト列でUTF-8として不正な場合があるため、read_artifact_content
    # ではなく解凍後バイト列を直接比較する。
    reassembled = storage._download_bytes_maybe_chunked(client, storage.STORAGE_BUCKET, fields["storage_path"])
    assert gzip.decompress(reassembled) == content

    deleted_before = len(client.storage_objects)
    storage.delete_artifact_storage_object(client, fields)
    assert len(client.storage_objects) == deleted_before - chunk_count


def test_force_storage_bypasses_size_threshold():
    client = FakeSupabaseClient()
    content = b"tiny but forced"

    fields = storage.build_artifact_storage_fields(
        client, RUN_ID, EXECUTION_ID, "prompt1_input", "cluster_labeling_input.csv", content,
        force_storage=True)

    assert fields["content_text"] is None
    assert fields["storage_path"] is not None


def test_read_artifact_content_prefers_storage_path():
    client = FakeSupabaseClient()
    content = b"y" * (storage.STORAGE_SIZE_THRESHOLD_BYTES + 1)
    fields = storage.build_artifact_storage_fields(
        client, RUN_ID, EXECUTION_ID, "prompt2_base_input", "records.zip", content)
    row = {**fields, "run_id": RUN_ID, "execution_id": EXECUTION_ID}

    text = storage.read_artifact_content(client, row)
    assert text == content.decode("utf-8")


def test_read_artifact_content_falls_back_to_content_text():
    client = FakeSupabaseClient()
    row = {"storage_path": None, "content_text": compression.compress_text("hello world")}
    assert storage.read_artifact_content(client, row) == "hello world"


def test_read_artifact_content_passes_through_uncompressed_legacy_content_text():
    client = FakeSupabaseClient()
    # 2026-09-11以前に保存された非圧縮のcontent_text(マーカー無し)も引き続き読める。
    row = {"storage_path": None, "content_text": "legacy plain text"}
    assert storage.read_artifact_content(client, row) == "legacy plain text"


def test_delete_artifact_storage_object_removes_storage_object_only_when_present():
    client = FakeSupabaseClient()
    content = b"z" * (storage.STORAGE_SIZE_THRESHOLD_BYTES + 1)
    fields = storage.build_artifact_storage_fields(
        client, RUN_ID, EXECUTION_ID, "collection_input", "raw.zip", content)
    row = {**fields}

    storage.delete_artifact_storage_object(client, row)
    assert (storage.STORAGE_BUCKET, fields["storage_path"]) not in client.storage_objects

    # content_text行(storage_path無し)に対しては何もしない(例外を出さない)
    storage.delete_artifact_storage_object(client, {"storage_path": None, "content_text": "x"})


def test_delete_all_run_artifacts_storage_removes_only_storage_backed_rows_for_that_run():
    """2026-09-17追加、ユーザー指摘「データは大きくなりやすいのですぐストレージが
    一杯になるはず」への対応検証: Run削除時にStorage実体もまとめて消せることを
    確認する（content_text行や他Runの行は対象外のまま残る）。"""
    client = FakeSupabaseClient()
    big_content = b"x" * (storage.STORAGE_SIZE_THRESHOLD_BYTES + 1)
    fields_a = storage.build_artifact_storage_fields(
        client, RUN_ID, EXECUTION_ID, "prompt1_input", "a.csv", big_content)
    fields_b = storage.build_artifact_storage_fields(
        client, RUN_ID, EXECUTION_ID, "prompt1_input", "b.csv", big_content)
    small_fields = storage.build_artifact_storage_fields(
        client, RUN_ID, EXECUTION_ID, "prompt_snapshot", "small.md", b"small")
    other_run_fields = storage.build_artifact_storage_fields(
        client, "other-run", EXECUTION_ID, "prompt1_input", "c.csv", big_content)

    client.insert("geo_report_run_artifacts", [
        {"id": "a1", "run_id": RUN_ID, "file_name": "a.csv", **fields_a},
        {"id": "a2", "run_id": RUN_ID, "file_name": "b.csv", **fields_b},
        {"id": "a3", "run_id": RUN_ID, "file_name": "small.md", **small_fields},
        {"id": "a4", "run_id": "other-run", "file_name": "c.csv", **other_run_fields},
    ])

    deleted_count = storage.delete_all_run_artifacts_storage(client, RUN_ID)

    assert deleted_count == 2
    assert (storage.STORAGE_BUCKET, fields_a["storage_path"]) not in client.storage_objects
    assert (storage.STORAGE_BUCKET, fields_b["storage_path"]) not in client.storage_objects
    # 他Runのstorage objectは消えない
    assert (storage.STORAGE_BUCKET, other_run_fields["storage_path"]) in client.storage_objects


def test_build_artifact_storage_fields_raises_on_integrity_mismatch(monkeypatch):
    client = FakeSupabaseClient()
    content = b"a" * (storage.STORAGE_SIZE_THRESHOLD_BYTES + 1)

    # storage_downloadが破損したバイト列を返す状況(アップロード破損)を再現する。
    monkeypatch.setattr(client, "storage_download", lambda bucket, path: b"corrupted")

    try:
        storage.build_artifact_storage_fields(client, RUN_ID, EXECUTION_ID, "final_report", "f.html", content)
        assert False, "should have raised"
    except ValueError as exc:
        assert "整合性" in str(exc)
