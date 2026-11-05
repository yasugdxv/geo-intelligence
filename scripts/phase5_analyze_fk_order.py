"""
Phase 5A: sql/配下のGeo専用31ファイルを静的解析し、
(1) 各テーブルの主キー列名
(2) テーブル間FK依存グラフ(自己参照は除く)
(3) FK依存を満たす安全なinsert順序(トポロジカルソート)
を導出する。実DBへは一切接続しない、純粋なテキスト解析。

自己参照FK(例: geo_report_collection_runs.baseline_run_id、
geo_documents.superseded_by)はinsert順序制約にはならない
(移行スクリプト側でNULL初期化→事後UPDATEで解決する前提)。
"""
import re
from pathlib import Path
from collections import defaultdict, deque

SQL_DIR = Path(__file__).parent.parent / "sql"

# 適用順（Phase4で実証済みの順序。ファイル名の日付だけでは同日内順序を保証できないため、
# Phase4実施時に確定した正しい順序をそのままここに固定する）
FILES_IN_ORDER = [
    "2026-08-01_geo_shared_infra.sql",
    "2026-08-02_geo_core_schema.sql",
    "2026-08-03_geo_expert_ai_schema.sql",
    "2026-08-03_geo_indicator_tagging.sql",
    "2026-08-03_geo_observation_rationale.sql",
    "2026-08-04_geo_knowledge_core.sql",
    "2026-08-04_geo_knowledge_pgvector_optional.sql",
    "2026-08-04_geo_knowledge_search_fts.sql",
    "2026-08-05_geo_phase2a_freshness.sql",
    "2026-08-05_geo_phase2a_ingestion.sql",
    "2026-08-05_geo_phase2a_metrics.sql",
    "2026-08-05_geo_phase2a_retrieval_trace.sql",
    "2026-08-06_geo_phase2b_indicator_alerts.sql",
    "2026-08-06_geo_phase2b_relationship_graph.sql",
    "2026-08-06_geo_phase2b_source_gating.sql",
    "2026-08-19_geo_phase2b2_external_reference.sql",
    "2026-08-21_geo_intelligence_agent.sql",
    "2026-08-21_geo_intelligence_needs.sql",
    "2026-08-21_geo_report_upload.sql",
    "2026-08-26_fix_weekly_monitoring_request_type_constraint.sql",
    "2026-08-26_pin_function_search_path.sql",
    "2026-08-29_geo_report_review_loop.sql",
    "2026-08-29_geo_report_versioning.sql",
    "2026-08-31_geo_media_intelligence_knowledge_types.sql",
    "2026-09-02_geo_report_studio_schema.sql",
    "2026-09-02_geo_report_studio_synthesis_schema.sql",
    "2026-09-02_geo_report_studio_continuity_schema.sql",
    "2026-09-02_geo_report_studio_deep_link_schema.sql",
    "2026-09-02_geo_institution_intelligence_knowledge_type.sql",
    "2026-09-03_geo_documents_research_qualification.sql",
    "2026-09-04_geo_async_research_link.sql",
]

CREATE_TABLE_RE = re.compile(r"create table\s+public\.(\w+)\s*\(", re.IGNORECASE)
PK_COLUMN_RE = re.compile(r"^\s*(\w+)\s+\w+.*\bprimary key\b", re.IGNORECASE | re.MULTILINE)
PK_TABLE_CONSTRAINT_RE = re.compile(r"primary key\s*\(([\w,\s]+)\)", re.IGNORECASE)
FK_REF_RE = re.compile(r"(\w+)\s+\w+(?:\[\])?\s+(?:not null\s+)?references\s+public\.(\w+)\s*\(\s*(\w+)\s*\)", re.IGNORECASE)
ALTER_STMT_RE = re.compile(r"alter table\s+public\.(\w+)([^;]*);", re.IGNORECASE)
ADD_CONSTRAINT_FK_RE = re.compile(
    r"add constraint\s+\w+\s+foreign key\s*\((\w+)\)\s+references\s+public\.(\w+)",
    re.IGNORECASE,
)
ALTER_ADD_COL_FK_RE = re.compile(
    r"alter table\s+public\.(\w+)[^;]*?add column\s+(?:if not exists\s+)?(\w+)\s+\w+(?:\[\])?\s*references\s+public\.(\w+)\s*\(\s*(\w+)\s*\)",
    re.IGNORECASE,
)


def parse_all():
    pk_of = {}  # table -> pk column(s) (comma-joined if composite)
    fk_edges = defaultdict(set)  # table -> set of tables it depends on (excluding self)
    fk_detail = defaultdict(list)  # table -> list of (fk_column, ref_table, ref_column)
    all_tables_seen = []

    full_text_by_file = {}
    for fname in FILES_IN_ORDER:
        full_text_by_file[fname] = (SQL_DIR / fname).read_text(encoding="utf-8")

    for fname in FILES_IN_ORDER:
        text = full_text_by_file[fname]
        # CREATE TABLE blocks: split roughly by statement to scope column-level FK parsing per table
        for m in CREATE_TABLE_RE.finditer(text):
            table = m.group(1)
            all_tables_seen.append(table)
            start = m.end()
            # 対応する閉じ括弧まで(ネスト考慮せず、次の ");" までの簡易抽出で十分)
            end = text.find(");", start)
            body = text[start:end] if end != -1 else text[start:start + 4000]

            # 主キー(単一列: "xxx uuid primary key default ...")
            pk_match = PK_COLUMN_RE.search(body)
            if pk_match:
                pk_of[table] = pk_match.group(1)
            else:
                # 複合主キー: "primary key (a, b)"
                comp = PK_TABLE_CONSTRAINT_RE.search(body)
                if comp:
                    pk_of[table] = comp.group(1).replace(" ", "")

            for fk_m in FK_REF_RE.finditer(body):
                fk_col, ref_table, ref_col = fk_m.group(1), fk_m.group(2), fk_m.group(3)
                if ref_table != table:  # 自己参照は依存グラフに含めない
                    fk_edges[table].add(ref_table)
                fk_detail[table].append((fk_col, ref_table, ref_col, "inline"))

        # ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY(以降のmigrationでの遅延FK追加)。
        # 1つのALTER TABLE文にadd constraintが複数カンマ区切りで続くケース
        # (例: geo_knowledge_itemsへのderived_from_review_session_id/derived_from_change_id
        # の同時追加)に対応するため、文単位で区切ってから中の全add constraintを拾う。
        for stmt_m in ALTER_STMT_RE.finditer(text):
            table, stmt_body = stmt_m.group(1), stmt_m.group(2)
            for fk_m in ADD_CONSTRAINT_FK_RE.finditer(stmt_body):
                fk_col, ref_table = fk_m.group(1), fk_m.group(2)
                if ref_table != table:
                    fk_edges[table].add(ref_table)
                fk_detail[table].append((fk_col, ref_table, "?", "alter_add_constraint"))

        # ALTER TABLE ... ADD COLUMN ... uuid references ...(Phase2B-2以降の追加パターン)
        for m in ALTER_ADD_COL_FK_RE.finditer(text):
            table, fk_col, ref_table, ref_col = m.group(1), m.group(2), m.group(3), m.group(4)
            if ref_table != table:
                fk_edges[table].add(ref_table)
            fk_detail[table].append((fk_col, ref_table, ref_col, "alter_add_column"))

    return pk_of, fk_edges, fk_detail, all_tables_seen


def topological_sort_with_cycle_breaking(all_tables, fk_edges, fk_detail):
    """Kahn法を拡張し、循環依存で行き詰まった場合は「残っている中で未解決依存が
    最も少ないテーブル」を強制的に確定させ、そのテーブルへ向かう未解決のFK列を
    "deferred"(初回insertはNULL、全テーブル投入後にUPDATEで埋める)として記録する。
    これにより、bootstrap的な相互参照(例: geo_source_registry.last_ingestion_run_id
    → geo_ingestion_runs、geo_ingestion_runs.source_id → geo_source_registry)を
    データ移行の観点で安全に解決する。"""
    indegree = {t: 0 for t in all_tables}
    incoming = defaultdict(set)  # table -> set of tables it still waits on
    dependents = defaultdict(list)
    for table, deps in fk_edges.items():
        for dep in deps:
            if dep not in indegree:
                continue
            indegree[table] += 1
            incoming[table].add(dep)
            dependents[dep].append(table)

    order_priority = {t: i for i, t in enumerate(all_tables)}
    result = []
    deferred_columns = []  # (table, fk_column, ref_table) のリスト
    remaining_set = set(all_tables)

    while remaining_set:
        ready = sorted([t for t in remaining_set if indegree[t] == 0], key=lambda t: order_priority[t])
        if ready:
            t = ready[0]
        else:
            # 循環: 残存テーブルの中で「未解決依存数が最少」のものを強制確定する
            t = min(remaining_set, key=lambda x: (indegree[x], order_priority[x]))
            unresolved_deps = incoming[t] & remaining_set
            for dep in unresolved_deps:
                for fk_col, ref_table, ref_col, kind in fk_detail.get(t, []):
                    if ref_table == dep:
                        deferred_columns.append((t, fk_col, ref_table))
            indegree[t] = 0  # 強制的に解決済み扱いにする

        result.append(t)
        remaining_set.discard(t)
        for dep_t in dependents[t]:
            if dep_t in remaining_set:
                indegree[dep_t] = max(0, indegree[dep_t] - 1)

    return result, deferred_columns


if __name__ == "__main__":
    pk_of, fk_edges, fk_detail, all_tables_seen = parse_all()
    all_tables = sorted(set(all_tables_seen), key=all_tables_seen.index)

    print(f"=== テーブル数: {len(all_tables)} ===\n")

    missing_pk = [t for t in all_tables if t not in pk_of]
    if missing_pk:
        print("!!! 主キー未検出のテーブル(要手動確認):", missing_pk, "\n")

    order, deferred_columns = topological_sort_with_cycle_breaking(all_tables, fk_edges, fk_detail)

    print(f"=== FK依存を満たす安全なinsert順序({len(order)}テーブル) ===")
    for i, t in enumerate(order, 1):
        pk = pk_of.get(t, "???")
        deps = sorted(fk_edges.get(t, []))
        print(f"{i:2d}. {t}  (PK={pk})" + (f"  depends_on={deps}" if deps else ""))

    if deferred_columns:
        print(f"\n=== 循環依存のため2パス投入(NULL初期化→事後UPDATE)が必要な列({len(deferred_columns)}件) ===")
        for table, col, ref_table in deferred_columns:
            print(f"  {table}.{col}  ->  {ref_table}")

    # Python辞書としても出力(次のスクリプトへそのまま貼り付けられるように)
    print("\n=== Python形式(次スクリプト用) ===")
    print("TABLE_ORDER = [")
    for t in order:
        print(f"    {t!r},")
    print("]")
    print("\nPK_COLUMNS = {")
    for t in order:
        print(f"    {t!r}: {pk_of.get(t, 'UNKNOWN')!r},")
    print("}")
