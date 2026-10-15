"""地政学ナレッジ＆インテリジェンスDB: CSV/Excel/JSON手動Import Connector（15.2章）。

自動収集は行わない。アップロードされたファイルをpandasで読み込み、column mappingに
従って対象テーブル（entities/relationships/assets/dependencies）へ挿入する。
1行ごとに必須項目を検証し、失敗した行はrejected_rowsとして返す（黙って無視しない）。
"""
import io

import pandas as pd

import geo_audit

REQUIRED_COLUMNS_BY_TARGET = {
    "geo_entities": ("entity_type", "canonical_name"),
    "geo_relationships": ("from_entity_id", "to_entity_id", "relationship_domain", "relationship_type"),
    "geo_assets": ("entity_id", "asset_type", "description"),
    "geo_dependencies": ("entity_id", "dependency_type", "description"),
}
ALLOWED_TARGETS = tuple(REQUIRED_COLUMNS_BY_TARGET.keys())


def read_tabular_file(raw_bytes: bytes, filename: str) -> pd.DataFrame:
    """CSV/Excel/JSONを読み込みDataFrameを返す（拡張子で判定）。"""
    lower = (filename or "").lower()
    if lower.endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(raw_bytes))
    if lower.endswith(".json"):
        return pd.read_json(io.BytesIO(raw_bytes))
    return pd.read_csv(io.BytesIO(raw_bytes))


def preview(raw_bytes: bytes, filename: str, limit: int = 20) -> dict:
    """先頭limit行のプレビューと列名一覧を返す（column mapping画面用）。"""
    df = read_tabular_file(raw_bytes, filename)
    return {
        "columns": list(df.columns), "row_count": len(df),
        "preview_rows": df.head(limit).fillna("").to_dict(orient="records"),
    }


def import_rows(client, raw_bytes: bytes, filename: str, *, target_table: str,
                 column_mapping: dict, actor_id: str, is_demo: bool = False) -> dict:
    """column_mapping: {source_column: target_column} でDataFrameの列名を対象テーブルの
    列名へ読み替えたうえで、必須項目を満たす行のみinsertする。
    戻り値: {"imported": [...], "rejected": [{"row_index":..., "errors": [...]}]}"""
    if target_table not in ALLOWED_TARGETS:
        raise ValueError(f"import対象として許可されていないテーブルです: {target_table}")

    df = read_tabular_file(raw_bytes, filename)
    df = df.rename(columns=column_mapping)
    required = REQUIRED_COLUMNS_BY_TARGET[target_table]

    imported, rejected = [], []
    valid_rows = []
    row_meta = []
    for idx, row in df.iterrows():
        row_dict = row.dropna().to_dict()
        missing = [col for col in required if not row_dict.get(col)]
        if missing:
            rejected.append({"row_index": int(idx), "errors": [f"必須項目が空です: {col}" for col in missing]})
            continue
        row_dict["is_demo"] = is_demo
        valid_rows.append(row_dict)
        row_meta.append(int(idx))

    if valid_rows:
        imported = client.insert(target_table, valid_rows)

    geo_audit.log_audit(client, actor=actor_id, action="csv_excel_json_import",
                         target_table=target_table, target_id=None,
                         detail={"filename": filename, "imported_count": len(imported),
                                 "rejected_count": len(rejected)})
    return {"imported": imported, "rejected": rejected}
