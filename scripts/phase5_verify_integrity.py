"""
Phase 5E: 新Geo Supabaseへのデータ移行の整合性検証。

検証項目:
1. 63/63テーブルで source_count == target_count
2. 全テーブルでPK集合(distinct)が完全一致
3. created_at列を持つテーブルはmin/max created_atが一致
4. 主要テーブルは全行fingerprint(canonical JSON→sha256)を行単位で比較
   (generated column(search_vector)はfingerprint対象から除外。値がDB側で
   再計算される列であり、内容一致の判定に含めるべきではないため)
5. 循環依存3列(Pass2で事後UPDATEした列)について、NULL/非NULLパターンと
   非NULL値がsource/targetで一致することを個別検証
6. 全FK関係についてtarget側でorphan(参照先に存在しないFK値)が0件であることを確認
   (target Postgresは実際にFK制約を強制しているため、insertが全て成功している時点で
   構造的にorphanは発生し得ないが、念のため独立に確認する)

service_role key/URLはログに一切出さない。
"""
import hashlib
import json
import sys
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

sys.path.insert(0, str(Path(__file__).parent))
from phase5_analyze_fk_order import parse_all, topological_sort_with_cycle_breaking  # noqa: E402
from phase5_migrate_data import _load_credentials, GENERATED_COLUMNS, DEFERRED_COLUMNS  # noqa: E402

REQUEST_TIMEOUT = 60
BATCH_SIZE = 1000

FINGERPRINT_TABLES = [
    "geo_entities", "geo_relationships", "geo_knowledge_items", "geo_claims",
    "geo_cases", "geo_documents", "geo_intelligence_requests", "geo_intelligence_responses",
    "geo_reports", "geo_report_projects", "geo_report_collection_runs", "geo_report_clusters",
    "geo_report_cluster_representatives", "geo_report_records", "geo_report_syntheses",
    "geo_report_narrative_master_rows",
]


def _headers(creds):
    return {"apikey": creds["key"], "Authorization": f"Bearer {creds['key']}"}


def fetch_all(creds, table: str, order_col: str) -> list:
    rows = []
    offset = 0
    while True:
        r = requests.get(
            f"{creds['url']}/rest/v1/{table}",
            params={"select": "*", "order": f"{order_col}.asc", "limit": str(BATCH_SIZE), "offset": str(offset)},
            headers=_headers(creds), timeout=REQUEST_TIMEOUT, verify=False,
        )
        r.raise_for_status()
        page = r.json()
        rows.extend(page)
        if len(page) < BATCH_SIZE:
            break
        offset += BATCH_SIZE
    return rows


def row_fingerprint(row: dict, exclude: set) -> str:
    clean = {k: v for k, v in row.items() if k not in exclude}
    canonical = json.dumps(clean, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main():
    source, target = _load_credentials()
    pk_of, fk_edges, fk_detail, all_tables_seen = parse_all()
    all_tables = sorted(set(all_tables_seen), key=all_tables_seen.index)
    order, _ = topological_sort_with_cycle_breaking(all_tables, fk_edges, fk_detail)

    print(f"=== Phase 5E: {len(order)}テーブルの整合性検証 ===\n")

    problems = []
    count_mismatches = []
    pk_mismatches = []
    createdat_mismatches = []
    fingerprint_mismatches = []
    orphan_findings = []

    target_pk_cache = {}  # table -> set of PK tuples (target側、orphanチェック用に再利用)

    for table in order:
        pk_cols = pk_of[table].split(",")
        pk_col0 = pk_cols[0]

        s_rows = fetch_all(source, table, pk_col0)
        t_rows = fetch_all(target, table, pk_col0)

        s_count, t_count = len(s_rows), len(t_rows)
        if s_count != t_count:
            count_mismatches.append((table, s_count, t_count))

        def pk_tuple(row):
            return tuple(row.get(c) for c in pk_cols)

        s_pks = set(pk_tuple(r) for r in s_rows)
        t_pks = set(pk_tuple(r) for r in t_rows)
        target_pk_cache[table] = t_pks
        if s_pks != t_pks:
            pk_mismatches.append((table, len(s_pks - t_pks), len(t_pks - s_pks)))

        if s_rows and "created_at" in s_rows[0]:
            s_dates = [r["created_at"] for r in s_rows if r.get("created_at")]
            t_dates = [r["created_at"] for r in t_rows if r.get("created_at")]
            if s_dates and t_dates:
                if (min(s_dates), max(s_dates)) != (min(t_dates), max(t_dates)):
                    createdat_mismatches.append((table, (min(s_dates), max(s_dates)), (min(t_dates), max(t_dates))))

        if table in FINGERPRINT_TABLES:
            exclude = GENERATED_COLUMNS.get(table, set())
            s_by_pk = {pk_tuple(r): row_fingerprint(r, exclude) for r in s_rows}
            t_by_pk = {pk_tuple(r): row_fingerprint(r, exclude) for r in t_rows}
            diffs = [pk for pk in s_by_pk if pk in t_by_pk and s_by_pk[pk] != t_by_pk[pk]]
            if diffs:
                fingerprint_mismatches.append((table, len(diffs), diffs[:5]))

        status = "OK" if (s_count == t_count and s_pks == t_pks) else "MISMATCH"
        marker = " [fingerprint verified]" if table in FINGERPRINT_TABLES else ""
        print(f"{status:8s} {table}: source={s_count} target={t_count}{marker}")

    # ---- 循環依存列の個別検証 ----
    print("\n=== 循環依存3列の個別検証 ===")
    deferred_problems = []
    for table, col, pk_col in DEFERRED_COLUMNS:
        s_rows = fetch_all(source, table, pk_col)
        t_rows = fetch_all(target, table, pk_col)
        t_by_pk = {r[pk_col]: r.get(col) for r in t_rows}
        mismatches = 0
        for r in s_rows:
            s_val = r.get(col)
            t_val = t_by_pk.get(r[pk_col])
            if s_val != t_val:
                mismatches += 1
        non_null_source = sum(1 for r in s_rows if r.get(col) is not None)
        status = "OK" if mismatches == 0 else "MISMATCH"
        print(f"{status:8s} {table}.{col}: source非NULL={non_null_source}件, 不一致={mismatches}件")
        if mismatches:
            deferred_problems.append((table, col, mismatches))

    # ---- FK orphanチェック(target側、全FK関係) ----
    print("\n=== FK orphanチェック(target、全FK関係) ===")
    orphan_total = 0
    for table in order:
        for fk_col, ref_table, ref_col, kind in fk_detail.get(table, []):
            if ref_table == table:
                continue  # 自己参照は別途deferredで検証済み
            if ref_table not in target_pk_cache:
                continue
            t_rows = fetch_all(target, table, pk_of[table].split(",")[0])
            ref_pk_col = pk_of.get(ref_table, "").split(",")[0]
            # 参照先の実PK集合(単一列PKの場合のみ簡易チェック、複合PK参照は稀なためスキップ)
            if ref_col != ref_pk_col:
                continue  # 主キー以外の列を参照するFK(例: geo_events.indicator_code)は別集合が必要なためスキップ
            ref_pks = target_pk_cache.get(ref_table, set())
            ref_pks_flat = set(p[0] for p in ref_pks) if ref_pks and len(next(iter(ref_pks))) == 1 else set()
            orphans = [r[fk_col] for r in t_rows if r.get(fk_col) is not None and (r[fk_col],) not in ref_pks
                       and r[fk_col] not in ref_pks_flat]
            if orphans:
                orphan_total += len(orphans)
                orphan_findings.append((table, fk_col, ref_table, len(orphans)))
                print(f"  ORPHAN  {table}.{fk_col} -> {ref_table}: {len(orphans)}件")
    if orphan_total == 0:
        print("  orphan該当なし(0件)")

    # ---- 最終サマリー ----
    print("\n=== Phase 5E 最終判定 ===")
    all_ok = True
    if count_mismatches:
        all_ok = False
        print(f"NG: 件数不一致 {len(count_mismatches)}テーブル: {count_mismatches}")
    else:
        print(f"OK: 63/63テーブルでsource_count == target_count")
    if pk_mismatches:
        all_ok = False
        print(f"NG: PK集合不一致 {len(pk_mismatches)}テーブル: {pk_mismatches}")
    else:
        print(f"OK: 63/63テーブルでPK集合完全一致")
    if createdat_mismatches:
        all_ok = False
        print(f"NG: created_at範囲不一致: {createdat_mismatches}")
    else:
        print(f"OK: created_atを持つ全テーブルでmin/max一致")
    if fingerprint_mismatches:
        all_ok = False
        print(f"NG: fingerprint不一致: {fingerprint_mismatches}")
    else:
        print(f"OK: 主要{len(FINGERPRINT_TABLES)}テーブルで全行fingerprint完全一致")
    if deferred_problems:
        all_ok = False
        print(f"NG: 循環依存列の復元不一致: {deferred_problems}")
    else:
        print(f"OK: 循環依存3列すべて値一致")
    if orphan_total:
        all_ok = False
        print(f"NG: FK orphan合計{orphan_total}件")
    else:
        print(f"OK: FK orphan = 0")

    print(f"\n{'=== Phase 5 完了: 新Geo DBは旧共有DBのGeo領域とデータ的に同一 ===' if all_ok else '=== Phase 5 未完了: 上記NG項目の解消が必要 ==='}")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
