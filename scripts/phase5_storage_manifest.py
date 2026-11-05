"""
Phase 5 付随タスク: geo_documents.storage_path が指すファイル実体の監査(移行はしない)。

DBデータ移行(Phase 5D)では storage_path の文字列(ローカルパス)自体はそのまま
複製済みだが、ファイル実体はこのマシンのローカルディスク上にあるままで、
DB移行とは独立している。本スクリプトは:
  - geo_documents(source=共有Supabase)を全件取得
  - 各storage_pathについてローカルファイルの存在/サイズ/sha256を確認
  - CSVで一覧化 + サマリーを表示
Azure Blob/Supabase Storageへの実移行はここでは行わない(Phase 6以降の別タスク)。
"""
import csv
import hashlib
import json
import sys
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

REPO_ROOT = Path(__file__).parent.parent
OUT_CSV = Path(__file__).parent / "phase5_storage_manifest.csv"


def _load_source_credentials():
    with open(REPO_ROOT / "config.json", encoding="utf-8") as f:
        cfg = json.load(f)
    url = cfg["supabase"]["url"].rstrip("/")
    if url.endswith("/rest/v1"):
        url = url[: -len("/rest/v1")]
    key = cfg["supabase"].get("service_role_key") or cfg["supabase"]["key"]
    return {"url": url, "key": key}


def fetch_all_documents(creds):
    headers = {"apikey": creds["key"], "Authorization": f"Bearer {creds['key']}"}
    rows, offset, batch = [], 0, 1000
    while True:
        r = requests.get(
            f"{creds['url']}/rest/v1/geo_documents",
            params={
                "select": "document_id,storage_path,mime_type,title,url,fetched_at",
                "order": "document_id.asc", "limit": str(batch), "offset": str(offset),
            },
            headers=headers, timeout=60, verify=False,
        )
        r.raise_for_status()
        page = r.json()
        rows.extend(page)
        if len(page) < batch:
            break
        offset += batch
    return rows


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    creds = _load_source_credentials()
    docs = fetch_all_documents(creds)

    total = len(docs)
    with_path = sum(1 for d in docs if d.get("storage_path"))
    without_path = total - with_path
    exists_true = 0
    exists_false = 0
    total_size = 0

    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "document_id", "storage_path", "file_exists", "file_size", "sha256",
            "mime_type", "original_filename", "title", "url", "fetched_at",
        ])
        for d in docs:
            storage_path = d.get("storage_path") or ""
            file_exists = False
            file_size = ""
            sha256_hex = ""
            original_filename = Path(storage_path).name if storage_path else ""
            if storage_path:
                p = Path(storage_path)
                if p.exists() and p.is_file():
                    file_exists = True
                    exists_true += 1
                    try:
                        file_size = p.stat().st_size
                        total_size += file_size
                        sha256_hex = sha256_of(p)
                    except OSError as e:
                        sha256_hex = f"ERROR:{type(e).__name__}"
                else:
                    exists_false += 1
            writer.writerow([
                d["document_id"], storage_path, file_exists, file_size, sha256_hex,
                d.get("mime_type") or "", original_filename, d.get("title") or "",
                d.get("url") or "", d.get("fetched_at") or "",
            ])

    print("=== geo_documents Storage Manifest ===")
    print(f"出力先: {OUT_CSV}")
    print(f"geo_documents総数: {total}")
    print(f"storage_pathあり件数: {with_path}")
    print(f"storage_pathなし件数: {without_path}")
    print(f"file_exists=true件数: {exists_true}")
    print(f"file_exists=false件数: {exists_false}")
    print(f"総ファイル容量: {total_size:,} bytes ({total_size / 1024 / 1024:.2f} MB)")


if __name__ == "__main__":
    main()
