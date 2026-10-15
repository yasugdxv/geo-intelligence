"""Report Studio → Intelligence Analysis Platform: Reference Dataset（Phase 1）。

詳細設計: docs/report-studio/INTELLIGENCE_PLATFORM_PHASE0_2_PLAN.md（Section C/D）。

Current Prompt1（Strong LLM）の完了済みexecution結果を、Benchmark用の
Reference Dataset（`geo_report_reference_examples`）へ変換する。

重要な設計原則（Plan文書より）:
- "Gold Dataset"ではなく"Reference Dataset"と呼ぶ——Current Prompt1自身の出力であり、
  人手検証を経た独立したground truthではないため。
- Reference Datasetはimmutable。split（train/validation/test）はここに持たせず、
  `geo_report_benchmark_split_assignments`（別サービス、Benchmark実験属性）で管理する。
- Current Prompt1の元テーブル（`geo_report_*_classifications`/
  `geo_report_targeted_stance`）は読み取り専用として扱う——一切変更しない。
"""

from __future__ import annotations

import uuid

from geo_external_signal_service import _select_all_paginated

CHUNK_SIZE = 500

# 各分類テーブルのうち、label_json（分類結果そのもの）に含めない管理用列。
_AC_METADATA_COLUMNS = {"id", "execution_id", "run_id", "cluster_id", "created_at"}
_SC_METADATA_COLUMNS = {"id", "execution_id", "run_id", "sentiment_cluster_id", "created_at"}
_TS_METADATA_COLUMNS = {
    "id", "execution_id", "run_id", "sentiment_cluster_id", "signal_id", "target_key", "created_at",
}


def _new_id() -> str:
    return str(uuid.uuid4())


def _chunked(items: list, size: int = CHUNK_SIZE):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _label_json(row: dict, metadata_columns: set) -> dict:
    return {k: v for k, v in row.items() if k not in metadata_columns}


def _base_fields() -> dict:
    """Reference Example共通の既定値。DB列defaultに依存せず明示的に持たせる
    （テスト用FakeSupabaseClientはPostgresのcolumn defaultを再現しないため）。"""
    return {"source": "current_prompt1_strong_llm", "reference_quality": "reference_only",
            "human_reviewed": False}


def _ac_examples(run_id: str, execution_id: str, rows: list) -> list:
    return [{
        "id": _new_id(), "run_id": run_id, "execution_id": execution_id,
        "task": "ac_classification", "cluster_type": "analysis", "cluster_id": row["cluster_id"],
        "item_key": "", "label_json": _label_json(row, _AC_METADATA_COLUMNS),
        **_base_fields(),
    } for row in rows]


def _sc_examples(run_id: str, execution_id: str, rows: list) -> list:
    return [{
        "id": _new_id(), "run_id": run_id, "execution_id": execution_id,
        "task": "sc_classification", "cluster_type": "sentiment", "cluster_id": row["sentiment_cluster_id"],
        "item_key": "", "label_json": _label_json(row, _SC_METADATA_COLUMNS),
        **_base_fields(),
    } for row in rows]


def _ts_examples(run_id: str, execution_id: str, rows: list) -> list:
    examples = []
    for row in rows:
        target_key = row.get("target_key") or ""
        item_key = f"{row.get('target_type', '')}:{target_key}"
        examples.append({
            "id": _new_id(), "run_id": run_id, "execution_id": execution_id,
            "task": "targeted_stance", "cluster_type": "sentiment",
            "cluster_id": row["sentiment_cluster_id"], "item_key": item_key,
            "label_json": _label_json(row, _TS_METADATA_COLUMNS),
            **_base_fields(),
        })
    return examples


def build_reference_examples_from_execution(client, run_id: str, execution_id: str) -> dict:
    """完了済みのPrompt1 execution（AC/SC/TS classification結果）から
    `geo_report_reference_examples`へ3 task分のReference Exampleを書き込む。
    Idempotency: 同一execution_idの既存Reference Exampleを先に全削除してから
    再構築する（delete-then-insert、既存コードベースの`save_targeted_stance`と
    同じ方式——`geo_db_client.SupabaseClient.insert`がPostgRESTの`on_conflict`
    upsertパラメータを取り回せないため、composite unique制約への依存を避ける）。
    戻り値: {"ac_classification": n, "sc_classification": n, "targeted_stance": n}。"""
    ac_rows = _select_all_paginated(client, "geo_report_analysis_cluster_classifications", {
        "select": "*", "execution_id": f"eq.{execution_id}",
    })
    sc_rows = _select_all_paginated(client, "geo_report_sentiment_cluster_classifications", {
        "select": "*", "execution_id": f"eq.{execution_id}",
    })
    ts_rows = _select_all_paginated(client, "geo_report_targeted_stance", {
        "select": "*", "execution_id": f"eq.{execution_id}",
    })

    examples = (
        _ac_examples(run_id, execution_id, ac_rows)
        + _sc_examples(run_id, execution_id, sc_rows)
        + _ts_examples(run_id, execution_id, ts_rows)
    )

    client.delete("geo_report_reference_examples", {"execution_id": f"eq.{execution_id}"})
    for chunk in _chunked(examples):
        client.insert("geo_report_reference_examples", chunk, prefer="return=minimal")

    return {
        "ac_classification": len(ac_rows),
        "sc_classification": len(sc_rows),
        "targeted_stance": len(ts_rows),
    }
