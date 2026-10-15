"""Report Studio v2 Phase 3: 大容量ArtifactのSupabase Storage正式対応。

`geo_report_run_artifacts.content_text`は実測で25MBまでは成功・30MBでSupabase
ゲートウェイが520エラーになることが判明していた（2026-09-11、gzip+base64応急処置の
記録）。本モジュールは、一定サイズを超えるartifactは自動的にStorageへ保存し、
DBには参照（storage_bucket/storage_path）とsha256/size_bytesのみを残す設計へ
正式移行する（`geo_report_run_artifacts_storage_xor_text` check制約は元々この
排他構造を前提に設計済み——SQL変更は不要）。

設計方針（artifact_typeのホワイトリストではなくサイズで振り分ける）: ユーザー要件が
挙げた「Storage行きの対象例」（collection_input/preprocess ZIP/prompt1・2 input・
output ZIP/record_cluster_map/final_report.html）は、実質的にどれも大きいファイルで
あり、「小さいもの」（prompt snapshot/continuity JSON/section単位のcomposition_
result等）は自然に閾値未満になる。artifact_type列挙を保守し続ける必要がなく、
将来artifact_typeが増えても振り分けロジックの変更が不要という利点がある。

後方互換性: 既存のgzip圧縮content_text artifactはそのまま読める
（`read_artifact_content`はstorage_path優先、content_textはフォールバック、
既存の`decompress_text`のマーカー判定と組み合わせて透過的に復号する）。
既存Runへのmigrationは必須にしない。
"""

from __future__ import annotations

import gzip
import hashlib

import geo_report_artifact_compression as compression

# gzipストリームの先頭2バイト（マジックナンバー）。Storageへ保存したartifactがgzip
# 圧縮済みかどうかの判定に使う（2026-09-17追加）。合法なUTF-8テキストがこの2バイト
# 列で始まることは実質無い（\x1f\x8bは有効なUTF-8先頭バイトとして出現し得ない）ため、
# 既存の非圧縮Storageオブジェクト（後方互換）との判別に安全に使える。
_GZIP_MAGIC = b"\x1f\x8b"

STORAGE_BUCKET = "geo-report-artifacts"

# この閾値(バイト、圧縮前の実データサイズ基準)を超えるartifactはStorageへ保存する。
# 実測でSupabaseのINSERT/UPDATEペイロードが30MBで520エラーになることを確認済み
# （2026-09-11）——安全マージンを取って1MBを閾値にする。
STORAGE_SIZE_THRESHOLD_BYTES = 1_000_000

# 2026-09-17追加、ユーザー指摘「分割した方がいいのでは」への対応: 圧縮後のバイト列が
# この閾値を超える場合、1回のPOST/GETではなく複数の小さいオブジェクトへ分割して
# アップロード/ダウンロードする。実運用でcluster_representatives.csv（22万行超）の
# ような巨大artifactを1回のPOSTで社内プロキシ経由で送ると、gzip圧縮・timeout延長・
# リトライ増加後もなお度々失敗した——1リクエストの転送量そのものを減らすことで、
# 個々のリクエストが失敗する確率を下げ、失敗しても該当チャンクだけ再送すればよく
# なる（巨大な全体を毎回リトライする無駄を避けられる）。
# チャンクサイズは小さすぎるとリクエスト数（＝アップロード後の整合性確認による
# 再ダウンロードも含めると往復回数は倍）が増えすぎて逆に遅く・不安定になりかねない
# ため（ユーザー指摘、2026-09-17）、5MBにして往復回数を抑えつつ、従来の単一
# アップロード（圧縮後15〜25MB程度と推定）よりは十分小さくする。
STORAGE_CHUNK_SIZE_BYTES = 5_000_000
# storage_path文字列にこのマーカーが含まれる場合、チャンク分割されたartifactである
# ことを表す（`{path}#chunks={チャンク数}`という形式。DBスキーマ変更をせずに
# 分割の有無・数を表現するため、storage_path列自体にエンコードする）。
_CHUNK_COUNT_MARKER = "#chunks="


def build_storage_path(run_id: str, execution_id: str | None, artifact_type: str, file_name: str) -> str:
    exec_part = execution_id or "system"
    return f"{run_id}/{exec_part}/{artifact_type}/{file_name}"


def _chunk_object_path(base_path: str, index: int) -> str:
    return f"{base_path}/chunk_{index:04d}"


def _upload_bytes_maybe_chunked(client, bucket: str, base_path: str, data: bytes, *, content_type: str) -> str:
    """dataを（必要なら分割して）Storageへアップロードし、実際に保存した
    storage_path（分割時は`{base_path}#chunks={n}`）を返す。"""
    if len(data) <= STORAGE_CHUNK_SIZE_BYTES:
        client.storage_upload(bucket, base_path, data, content_type=content_type)
        return base_path
    chunks = [data[i:i + STORAGE_CHUNK_SIZE_BYTES] for i in range(0, len(data), STORAGE_CHUNK_SIZE_BYTES)]
    for i, chunk in enumerate(chunks):
        client.storage_upload(bucket, _chunk_object_path(base_path, i), chunk, content_type=content_type)
    return f"{base_path}{_CHUNK_COUNT_MARKER}{len(chunks)}"


def _download_bytes_maybe_chunked(client, bucket: str, storage_path: str) -> bytes:
    """storage_pathが分割形式（`#chunks=`マーカー付き）ならチャンクを順に
    ダウンロードして結合する。そうでなければ従来通り単一オブジェクトを読む。"""
    if _CHUNK_COUNT_MARKER in storage_path:
        base_path, _, count_str = storage_path.partition(_CHUNK_COUNT_MARKER)
        return b"".join(
            client.storage_download(bucket, _chunk_object_path(base_path, i))
            for i in range(int(count_str))
        )
    return client.storage_download(bucket, storage_path)


def _delete_bytes_maybe_chunked(client, bucket: str, storage_path: str) -> None:
    if _CHUNK_COUNT_MARKER in storage_path:
        base_path, _, count_str = storage_path.partition(_CHUNK_COUNT_MARKER)
        for i in range(int(count_str)):
            client.storage_delete(bucket, _chunk_object_path(base_path, i))
    else:
        client.storage_delete(bucket, storage_path)


def build_artifact_storage_fields(client, run_id: str, execution_id: str | None, artifact_type: str,
                                     file_name: str, content_bytes: bytes, *,
                                     content_type: str = "application/octet-stream",
                                     force_storage: bool = False) -> dict:
    """content_bytesのサイズに応じてStorage保存/content_text直接保存(gzip圧縮込み)を
    振り分け、`geo_report_run_artifacts`行へマージすべきフィールド一式を返す
    （storage_bucket/storage_path/content_text のうちどちらか一方の組み合わせ、
    どちらのケースでもsha256/size_bytesは常に圧縮・エンコード前の実データ基準）。
    force_storage=Trueの場合はサイズに関わらずStorageへ保存する（呼び出し元が
    artifact_typeから「常に大容量になりうる」と分かっている場合に使う）。"""
    sha256 = hashlib.sha256(content_bytes).hexdigest()
    size_bytes = len(content_bytes)
    if force_storage or size_bytes > STORAGE_SIZE_THRESHOLD_BYTES:
        # 2026-09-17追加、実運用で発生した事象への対応: Storageへの大容量アップロードが
        # 社内プロキシ経由で"write operation timed out"になる事象が発生した（timeoutを
        # 240秒へ延長しても解消しなかった）。CSVはテキストのためgzip圧縮効率が高く
        # （実測でおおむね1/5〜1/10程度になる）、転送量そのものを減らして時間切れの
        # 可能性を下げる。sha256/size_bytesは常に圧縮前の実データ基準のまま変えない
        # （整合性検証・表示の意味を保つため）。
        path = build_storage_path(run_id, execution_id, artifact_type, file_name)
        compressed = gzip.compress(content_bytes)
        stored_path = _upload_bytes_maybe_chunked(
            client, STORAGE_BUCKET, path, compressed, content_type="application/gzip")
        # upload後にhash一致を確認する（3.2 Integrity要件）。ダウンロードした圧縮済み
        # バイト列を解凍してから、元データのsha256と比較する。解凍自体に失敗する
        # （＝ダウンロードされたバイト列がそもそも壊れている）場合も整合性エラーとして
        # 扱う。
        try:
            downloaded = gzip.decompress(_download_bytes_maybe_chunked(client, STORAGE_BUCKET, stored_path))
        except OSError:
            raise ValueError(f"Storage upload整合性エラー: {stored_path}のhashが一致しません。")
        if hashlib.sha256(downloaded).hexdigest() != sha256:
            raise ValueError(f"Storage upload整合性エラー: {stored_path}のhashが一致しません。")
        return {
            "storage_bucket": STORAGE_BUCKET, "storage_path": stored_path, "content_text": None,
            "sha256": sha256, "size_bytes": size_bytes,
        }
    return {
        "storage_bucket": None, "storage_path": None,
        "content_text": compression.compress_text(content_bytes.decode("utf-8")),
        "sha256": sha256, "size_bytes": size_bytes,
    }


def read_artifact_content(client, artifact_row: dict) -> str:
    """既存artifact readerパターン(storage_path優先、content_textはgzip圧縮の
    透過的復号込み)を1関数へ統一する。呼び出し元は`decompress_text`を個別に
    呼ぶ必要がなくなる。

    2026-09-17追加: Storageへ保存するartifactもgzip圧縮するようにしたため、
    マジックナンバーで判定して透過的に解凍する（この変更より前にStorageへ保存
    された既存オブジェクトは非圧縮のままなので、後方互換のためマジックナンバーが
    無ければそのままUTF-8として扱う）。storage_pathがチャンク分割形式
    （`#chunks=`マーカー付き）の場合は、全チャンクを結合してから解凍する。"""
    if artifact_row.get("storage_path"):
        raw = _download_bytes_maybe_chunked(
            client, artifact_row.get("storage_bucket") or STORAGE_BUCKET, artifact_row["storage_path"])
        if raw[:2] == _GZIP_MAGIC:
            raw = gzip.decompress(raw)
        return raw.decode("utf-8")
    return compression.decompress_text(artifact_row.get("content_text"))


def delete_artifact_storage_object(client, artifact_row: dict) -> None:
    """artifact行を削除する前に、対応するStorage objectがあれば削除する
    （content_text行の場合は何もしない）。チャンク分割されたartifactの場合は
    全チャンクを削除する。"""
    storage_path = artifact_row.get("storage_path")
    if storage_path:
        _delete_bytes_maybe_chunked(client, artifact_row.get("storage_bucket") or STORAGE_BUCKET, storage_path)


def delete_all_run_artifacts_storage(client, run_id: str) -> int:
    """そのrunに属する`geo_report_run_artifacts`のうちStorageへ保存されている分の
    Storage objectを全て削除する。戻り値は削除件数。

    2026-09-17追加、ユーザー指摘「データは大きくなりやすいので、分析が終わったら
    消すなどしないとすぐストレージが一杯になるはず」への対応: `geo_report_design_api.
    delete_run`はRunのDB行を削除するだけで、子テーブル（`geo_report_run_artifacts`
    含む）はon delete cascadeでDB行は消えるが、実体であるSupabase Storage
    object（DBとは別システム）はDB cascadeの対象外のため、従来はRunを削除しても
    Storageへは永久に残り続けていた（`delete_artifact_storage_object`関数自体は
    存在していたが、どこからも呼ばれていなかった）。Run削除の直前に必ずこれを呼び、
    DB行を消す前にStorage実体を先に削除する。"""
    rows = client.select("geo_report_run_artifacts", {
        "select": "storage_bucket,storage_path", "run_id": f"eq.{run_id}",
        "storage_path": "not.is.null",
    })
    for row in rows:
        delete_artifact_storage_object(client, row)
    return len(rows)
