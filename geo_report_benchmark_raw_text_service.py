"""Report Studio → Intelligence Analysis Platform: Benchmark専用Raw Text Loader。

詳細設計: docs/report-studio/INTELLIGENCE_PLATFORM_PHASE0_2_PLAN.md（Section F）。

Current Pipeline（Production）はBabel Street Raw CSVを永続化しない設計であり
（Phase 0監査で確認済み、`collection_input` artifact typeは定義されているが未配線）、
`geo_report_records`テーブル自体にもtitle/本文列が無い。そのため、Section F
（Representative Selection Benchmark）が必要とする「クラスタに属する全recordの本文」は、
Runの元CSVファイルから都度読み込む。**Production Pipelineはこのモジュールに一切
依存しない**——Benchmark専用のローカルファイル依存として明確に切り分ける。

`record_id`（`geo_report_records.record_id`）はBabel Street CSVの`documentId`列と
そのまま一致する（実データで確認済み）。
"""

from __future__ import annotations

import csv


def load_record_texts(csv_paths: list, record_ids: set) -> dict:
    """{record_id: {"title": documentName, "text": documentText}}。
    record_idsに含まれる行だけを返す（全件をメモリに保持しない）。
    見つからなかったrecord_idはキー自体が無い（本文取得不可、呼び出し側は
    embedding対象から除外する）。"""
    found: dict = {}
    remaining = set(record_ids)
    for path in csv_paths:
        if not remaining:
            break
        with open(path, encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                doc_id = row.get("documentId")
                if doc_id in remaining:
                    found[doc_id] = {
                        "title": row.get("documentName") or "",
                        "text": row.get("documentText") or "",
                    }
                    remaining.discard(doc_id)
    return found
