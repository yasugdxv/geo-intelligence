# -*- coding: utf-8 -*-
"""Corpus Coverage Audit v1（stub発見）を受けたBackfillの最小Live試験用CLI。

geo_documents_store.backfill_stub_document()を、指定したdocument_id 1件ずつに
対して実行する。既定はdry_run（DBを一切書き換えない）。--liveを付けた場合のみ
実際にUPDATEする。88件の一括処理は行わない——ユーザー指示通り、まず少数の
document_idで個別にGateを確認してから拡張する設計。

使い方:
    python scripts/backfill_stub_documents.py <document_id> [<document_id> ...]
    python scripts/backfill_stub_documents.py <document_id> [<document_id> ...] --live
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config_utils  # noqa: E402
import geo_db_client  # noqa: E402
import geo_documents_store as store  # noqa: E402

# WindowsのコンソールはデフォルトでUTF-8以外のコードページのことがあり、
# 取得した実文書本文（多言語・特殊文字を含む）をそのままprintするとクラッシュ
# する（cp932でエンコードできない文字、実際にen-dashで発生）。結果はUTF-8の
# ログファイルへ書き、コンソールにはASCIIのみの要約を出す。
OUT_PATH = Path(__file__).parent / "backfill_run_log.txt"


def _gate_row(label: str, ok: bool, detail: str = "") -> str:
    mark = "PASS" if ok else "FAIL"
    return f"  {label:<22} {mark:<5} {detail}"


def run_one(client, document_id: str, proxies: dict, verify: bool, dry_run: bool, log: list) -> dict:
    result = store.backfill_stub_document(client, document_id, proxies=proxies, verify=verify, dry_run=dry_run)
    log.append(f"\n=== {document_id} ===")
    log.append(f"status: {result['status']}")

    if result["status"] in ("not_found", "fetch_failed"):
        log.append(f"  detail: {result.get('error') or result.get('status')}")
        return result

    if result["status"] == "rejected":
        log.append(f"  reasons: {result.get('reasons')}")
        if result.get("duplicate_of"):
            log.append(f"  duplicate_of: {result['duplicate_of']}")
        return result

    diff = result["diff"]
    gates = result["gates"]
    log.append(_gate_row("Fetch", True, "本文取得成功"))
    log.append(_gate_row("Update", result["status"] == "updated",
                          "既存document_idをUPDATE、新規行なし" if result["status"] == "updated" else "(dry_run、未書換)"))
    log.append(_gate_row("Text", gates["text_substantive"],
                          f"before_len={diff['extracted_text']['before_len']} -> after_len={diff['extracted_text']['after_len']}"))
    log.append(_gate_row("Date", gates["published_at_resolved"],
                          f"before={diff['published_at']['before']} -> after={diff['published_at']['after']}"))
    log.append(_gate_row("Provenance", True, "canonical_url/research_origin維持"))
    log.append(_gate_row("Hash", diff["content_hash"]["before"] != diff["content_hash"]["after"],
                          f"{diff['content_hash']['before']} -> {diff['content_hash']['after']}"))
    log.append(_gate_row("Duplicate", gates["not_duplicate"], "新規重複行なし"))
    foundation_eligible = gates["text_substantive"] and gates["not_duplicate"]
    log.append(_gate_row("Foundation eligibility", foundation_eligible,
                          "" if foundation_eligible else "published_at未解決でも本文採用は可、date欄で要確認"))
    log.append(f"  text_preview: {result.get('text_preview', '')[:200]}")
    return result


def main():
    args = sys.argv[1:]
    live = "--live" in args
    document_ids = [a for a in args if a != "--live"]
    if not document_ids:
        print(__doc__)
        sys.exit(1)

    cfg = config_utils.load_config()
    client = geo_db_client.SupabaseClient(cfg)
    proxies = config_utils.make_proxies(cfg)
    verify = cfg.get("ssl", {}).get("verify", True)

    log = [f"mode: {'LIVE' if live else 'DRY-RUN'}"]
    print(f"mode: {'LIVE (DB write)' if live else 'DRY-RUN (no DB write)'}")
    results = []
    for document_id in document_ids:
        try:
            results.append(run_one(client, document_id, proxies, verify, dry_run=not live, log=log))
        except Exception as e:
            results.append({"document_id": document_id, "status": f"error:{type(e).__name__}"})
            log.append(f"\n=== {document_id} ===\nERROR: {type(e).__name__}: {e}")
        print(f"  {document_id}: {results[-1]['status']}")

    log.append("\n=== summary ===")
    for r in results:
        log.append(f"  {r['document_id']}: {r['status']}")

    OUT_PATH.write_text("\n".join(log), encoding="utf-8")
    print(f"\nFull log (UTF-8): {OUT_PATH}")


if __name__ == "__main__":
    main()
