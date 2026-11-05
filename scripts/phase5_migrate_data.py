"""
Phase 5: 現行共有Supabase(Sustainability+Geo混在)のgeo_*データを、
新Geo専用SupabaseへREST API(PostgREST)経由で移行するスクリプト。

この環境からは直接Postgres接続がブロックされているため、source/targetいずれも
service_role keyによるREST API(HTTPS)のみで操作する(RLSの影響は受けない)。

安全設計:
- UUID/created_at/updated_at/version等は全てsourceの値をそのままtargetへ複製する
  (DB側のデフォルト値生成には一切頼らない)
- insert順はphase5_analyze_fk_order.pyが機械解析したFK依存順(TABLE_ORDER)に固定
- 冪等性: PostgRESTのPrefer: resolution=merge-duplicates(upsert)を使い、
  同一行を再実行しても上書きになるだけでエラーにしない
- 循環依存2列(geo_source_registry.last_ingestion_run_id,
  geo_sanctions_entries.latest_version_id)は全テーブル投入後に別パスでUPDATEする
- 進捗はテーブル単位でstateファイル(.phase5_state.json、gitignore対象)に記録し、
  失敗時は完了済みテーブルをスキップして再開できる
- service_role key・URLは一切print/logに出さない(テーブル名・件数のみ出力)
- verify=Falseはこのローカル検証環境(社内プロキシによるTLS中継)専用。
  本番アプリの実行時設定には一切影響しない、この移行スクリプト内だけの措置
"""
import argparse
import json
import sys
import time
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

sys.path.insert(0, str(Path(__file__).parent))
from phase5_analyze_fk_order import parse_all, topological_sort_with_cycle_breaking  # noqa: E402

REPO_ROOT = Path(__file__).parent.parent
STATE_FILE = Path(__file__).parent / ".phase5_state.json"
BATCH_SIZE = 500
REQUEST_TIMEOUT = 60

# 循環依存のため2パス投入が必要な列(phase5_analyze_fk_order.pyの解析結果に基づく固定値。
# 各tupleは (循環元テーブル, 循環元カラム, 循環元テーブル自身のPK列名))
DEFERRED_COLUMNS = [
    ("geo_source_registry", "last_ingestion_run_id", "source_id"),
    ("geo_knowledge_items", "derived_from_change_id", "knowledge_item_id"),
    ("geo_sanctions_entries", "latest_version_id", "sanctions_entry_id"),
]

# generated always as (...) stored 列(sql/2026-08-04_geo_knowledge_search_fts.sqlで
# grep済み、この3列のみ)。targetへexplicit insertするとPostgresがエラーにするため、
# payloadから除外し新DB側の生成ロジックに任せる(embeddingのような通常列は対象外)。
GENERATED_COLUMNS = {
    "geo_knowledge_items": {"search_vector"},
    "geo_claims": {"search_vector"},
    "geo_entities": {"search_vector"},
}


def _strip_generated_columns(table: str, rows: list) -> list:
    cols = GENERATED_COLUMNS.get(table)
    if not cols:
        return rows
    return [{k: v for k, v in row.items() if k not in cols} for row in rows]


def _load_credentials():
    """source=既存config.json(共有Supabase)、target=scripts/.phase5_target.json(新Geo Supabase)。
    戻り値のdictをそのままprintしないこと(url/keyを含むため)。"""
    with open(REPO_ROOT / "config.json", encoding="utf-8") as f:
        cfg = json.load(f)
    source = {
        "url": cfg["supabase"]["url"].rstrip("/"),
        "key": cfg["supabase"].get("service_role_key") or cfg["supabase"]["key"],
    }
    if source["url"].endswith("/rest/v1"):
        source["url"] = source["url"][: -len("/rest/v1")]

    with open(Path(__file__).parent / ".phase5_target.json", encoding="utf-8") as f:
        target_raw = json.load(f)
    target = {"url": target_raw["url"].rstrip("/"), "key": target_raw["key"]}
    if target["url"].endswith("/rest/v1"):
        target["url"] = target["url"][: -len("/rest/v1")]
    return source, target


def _headers(creds, prefer=None):
    h = {"apikey": creds["key"], "Authorization": f"Bearer {creds['key']}", "Content-Type": "application/json"}
    if prefer:
        h["Prefer"] = prefer
    return h


def get_exact_count(creds, table: str) -> int:
    r = requests.get(
        f"{creds['url']}/rest/v1/{table}",
        params={"select": "*", "limit": "1"},
        headers={**_headers(creds), "Prefer": "count=exact"},
        timeout=REQUEST_TIMEOUT, verify=False,
    )
    r.raise_for_status()
    content_range = r.headers.get("Content-Range", "*/0")
    return int(content_range.split("/")[-1])


def fetch_page(creds, table: str, pk_cols: list, offset: int, limit: int) -> list:
    order = ",".join(f"{c}.asc" for c in pk_cols)
    r = requests.get(
        f"{creds['url']}/rest/v1/{table}",
        params={"select": "*", "order": order, "limit": str(limit), "offset": str(offset)},
        headers=_headers(creds),
        timeout=REQUEST_TIMEOUT, verify=False,
    )
    r.raise_for_status()
    return r.json()


def upsert_batch(creds, table: str, pk_cols: list, rows: list) -> None:
    if not rows:
        return
    r = requests.post(
        f"{creds['url']}/rest/v1/{table}",
        params={"on_conflict": ",".join(pk_cols)},
        headers=_headers(creds, prefer="resolution=merge-duplicates,return=minimal"),
        json=rows,
        timeout=REQUEST_TIMEOUT, verify=False,
    )
    if not r.ok:
        raise RuntimeError(f"upsert failed for {table}: HTTP {r.status_code} {r.text[:500]}")


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_table_order():
    _, fk_edges, fk_detail, all_tables_seen = parse_all()
    all_tables = sorted(set(all_tables_seen), key=all_tables_seen.index)
    order, _ = topological_sort_with_cycle_breaking(all_tables, fk_edges, fk_detail)
    pk_of, _, _, _ = parse_all()
    return order, pk_of


def migrate_table(source, target, table: str, pk_cols: list, state: dict, dry_run: bool) -> dict:
    entry = state.get(table, {"status": "pending", "migrated_count": 0, "last_offset": 0})
    if entry.get("status") == "done" and not dry_run:
        print(f"[skip] {table}: 既に完了済み(migrated={entry['migrated_count']})")
        return entry

    source_count = get_exact_count(source, table)
    target_count_before = get_exact_count(target, table)
    entry["source_count"] = source_count

    if dry_run:
        print(f"[dry-run] {table}: source={source_count}件, target(現在)={target_count_before}件, PK={pk_cols}")
        return entry

    offset = entry.get("last_offset", 0)
    migrated = entry.get("migrated_count", 0)
    entry["status"] = "in_progress"

    try:
        while True:
            rows = fetch_page(source, table, pk_cols, offset, BATCH_SIZE)
            if not rows:
                break
            upsert_batch(target, table, pk_cols, _strip_generated_columns(table, rows))
            migrated += len(rows)
            offset += len(rows)
            entry["last_offset"] = offset
            entry["migrated_count"] = migrated
            save_state(state)
            print(f"  {table}: {migrated}/{source_count}件 投入済み")
            if len(rows) < BATCH_SIZE:
                break
        entry["status"] = "done"
        target_count_after = get_exact_count(target, table)
        entry["target_count_after"] = target_count_after
        print(f"[done] {table}: source={source_count}件 -> target={target_count_after}件")
    except Exception as e:
        entry["status"] = "failed"
        entry["error"] = f"{type(e).__name__}: {e}"
        print(f"[FAILED] {table}: {entry['error']}")

    state[table] = entry
    save_state(state)
    return entry


def run_deferred_fixup(source, target, dry_run: bool):
    print("\n=== 循環依存列の事後UPDATE(2パス目) ===")
    for table, col, pk_col in DEFERRED_COLUMNS:
        source_count = get_exact_count(source, table)
        offset = 0
        updated = 0
        while True:
            rows = fetch_page(source, table, [pk_col], offset, BATCH_SIZE)
            if not rows:
                break
            for row in rows:
                val = row.get(col)
                if val is None:
                    continue
                if dry_run:
                    updated += 1
                    continue
                r = requests.patch(
                    f"{target['url']}/rest/v1/{table}",
                    params={pk_col: f"eq.{row[pk_col]}"},
                    headers=_headers(target, prefer="return=minimal"),
                    json={col: val},
                    timeout=REQUEST_TIMEOUT, verify=False,
                )
                if not r.ok:
                    print(f"  [FAILED] {table}.{col} pk={row[pk_col]}: HTTP {r.status_code} {r.text[:200]}")
                else:
                    updated += 1
            offset += len(rows)
            if len(rows) < BATCH_SIZE:
                break
        action = "更新対象" if dry_run else "更新済み"
        print(f"  {table}.{col}: {updated}/{source_count}件 {action}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="件数確認・順序確認のみ、書き込みしない")
    ap.add_argument("--tables", type=str, default=None, help="カンマ区切りでテーブルを限定(Phase5Cの小規模検証用)")
    ap.add_argument("--deferred-only", action="store_true", help="循環依存列の事後UPDATEのみ実行")
    ap.add_argument("--reset-state", action="store_true", help="stateファイルを無視して最初からやり直す")
    args = ap.parse_args()

    source, target = _load_credentials()
    order, pk_of = get_table_order()

    if args.tables:
        wanted = set(args.tables.split(","))
        order = [t for t in order if t in wanted]

    state = {} if args.reset_state else load_state()

    if args.deferred_only:
        run_deferred_fixup(source, target, args.dry_run)
        return

    print(f"=== 対象テーブル数: {len(order)} (dry_run={args.dry_run}) ===\n")
    results = []
    for table in order:
        pk_cols = pk_of[table].split(",")
        entry = migrate_table(source, target, table, pk_cols, state, args.dry_run)
        results.append((table, entry))
        time.sleep(0.1)  # source/target双方への連続リクエストで過負荷にしないための小休止

    print("\n=== サマリー ===")
    for table, entry in results:
        print(f"  {table}: status={entry.get('status')} source={entry.get('source_count')} "
              f"migrated={entry.get('migrated_count')}")

    failed = [t for t, e in results if e.get("status") == "failed"]
    if failed:
        print(f"\n!!! 失敗テーブル({len(failed)}件): {failed}")
        print("再実行すると、完了済みテーブルはスキップして失敗分から再開します。")
        sys.exit(1)

    if not args.dry_run and not args.tables:
        print("\n全テーブル投入完了。次に --deferred-only で循環依存列を解決してください。")


if __name__ == "__main__":
    main()
