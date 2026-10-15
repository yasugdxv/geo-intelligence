"""Report Studio: geo_report_run_artifacts.content_text の透過的gzip圧縮ヘルパー。

2026-09-11実機検証で、Prompt1入力CSV等（cluster_representatives.csv等、レコード数に
比例して肥大化する）をcontent_textへ無圧縮でINSERTすると、Supabase側のゲートウェイが
30MB付近で520エラーを返して失敗することを実測確認した（25MBまでは成功）。

本格対応（Plan「kind-napping-hare.md」J.3で元々想定していたSupabase Storageへの移行）は
別途行う前提の応急処置として、content_textへ書き込む前にgzip圧縮+base64エンコードし、
読み込み側で透過的に復号する。`decompress_text`は非圧縮の既存データに対しては
そのまま元の文字列を返す（マーカー文字列が無ければ何もしない）ため、どのartifact_typeの
content_textに対しても安全に適用できる。
"""

from __future__ import annotations

import base64
import gzip

_MARKER = "GZB64:"


def compress_text(text: str) -> str:
    """content_textへ書き込む直前に適用する。sha256/size_bytesは圧縮前のtextから
    計算すること（元データの実サイズ・内容検証としての意味を保つため）。"""
    raw = gzip.compress(text.encode("utf-8"))
    return _MARKER + base64.b64encode(raw).decode("ascii")


def decompress_text(stored):
    """content_textを読み込んだ直後に適用する。マーカーが無い（圧縮前に書かれた
    既存データ、または他のartifact_typeの非圧縮データ）場合はそのまま返す。"""
    if not isinstance(stored, str) or not stored.startswith(_MARKER):
        return stored
    raw = base64.b64decode(stored[len(_MARKER):])
    return gzip.decompress(raw).decode("utf-8")
