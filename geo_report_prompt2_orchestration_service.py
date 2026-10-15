"""Report Studio「Design」領域: Prompt2 Aggregation/Composition実行オーケストレーション
（Phase E）。

`geo_report_prompt2_aggregation_service`（決定的集計）と
`geo_report_prompt2_composition_service`（Report Composition AI）を、
`geo_report_executions`（stage='aggregation'→stage='prompt2'）へ結びつける。Prompt2は
Sectionごとに1バッチ（`geo_report_execution_batches`、stage='prompt2_composition'）とし、
Prompt1と同じChunk実行・Idempotent upsert・進捗ポーリングのパターンを踏襲する。

Application AI経路のみ（`execution_method='application_ai'`、
`input_aggregation_execution_id`必須）。ChatGPT Fallback経路（Phase F）はこの
Aggregation Executionを経由しない別内部経路（kind-napping-hare.md「要件1」参照）。
"""

from __future__ import annotations

import csv
import io
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import geo_report_artifact_storage_service as artifact_storage
import geo_report_execution_canonical_service as canonical
import geo_report_project_assets_service as assets
from geo_external_signal_service import _select_all_paginated as _paginated_select
import geo_report_prompt2_aggregation_service as agg
import geo_report_prompt2_composition_service as comp
import geo_report_prompt2_output_service as output_svc
import geo_report_run_cancellation_service as cancellation

CHUNK_SIZE = 500

# geo_report_prompt1_orchestration_serviceと同じ理由(実測ベースの並列化、詳細はそちら参照)。
# SectionあたりLLM 1コールなので、ここではSection単位(=バッチ単位)で並列化する。
# 既定値もPrompt1側と同じ理由で3に統一。
BATCH_CONCURRENCY = int(os.environ.get("GEO_REPORT_BATCH_CONCURRENCY", "3"))


def _new_id() -> str:
    return str(uuid.uuid4())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _chunked(items: list, size: int = CHUNK_SIZE):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _get_run(client, run_id: str) -> dict:
    rows = client.select("geo_report_collection_runs", {"select": "*", "run_id": f"eq.{run_id}"})
    if not rows:
        raise ValueError(f"run_id={run_id} が見つかりません。")
    return rows[0]


def _artifact_row(client, run_id: str, execution_id: str, artifact_type: str, file_name: str, content_text: str,
                   media_type: str) -> dict:
    storage_fields = artifact_storage.build_artifact_storage_fields(
        client, run_id, execution_id, artifact_type, file_name, content_text.encode("utf-8"),
        content_type=media_type)
    return {
        "id": _new_id(), "run_id": run_id, "execution_id": execution_id, "execution_method": "python",
        "artifact_type": artifact_type, "file_name": file_name, "media_type": media_type,
        **storage_fields,
    }


def _dicts_to_csv_text(rows: list, columns: list = None) -> str:
    if not rows:
        columns = columns or []
        return ",".join(columns) + "\r\n" if columns else ""
    columns = columns or list(rows[0].keys())

    def _escape(value) -> str:
        s = "" if value is None else str(value)
        if any(c in s for c in (",", "\r", "\n", '"')):
            return '"' + s.replace('"', '""') + '"'
        return s

    lines = [",".join(columns)]
    for row in rows:
        lines.append(",".join(_escape(row.get(col, "")) for col in columns))
    return "\r\n".join(lines) + "\r\n"


# ---------------------------------------------------------------------------
# Aggregation Execution
# ---------------------------------------------------------------------------

def create_aggregation_execution(client, run_id: str) -> str:
    run = _get_run(client, run_id)
    if not run.get("canonical_prompt1_execution_id"):
        raise ValueError(f"run_id={run_id} はPrompt1分類が未完了です（canonical_prompt1_execution_id無し）。")
    execution_id = _new_id()
    client.insert("geo_report_executions", [{
        "id": execution_id, "run_id": run_id, "stage": "aggregation", "execution_method": "python",
        "status": "running", "input_prompt1_execution_id": run["canonical_prompt1_execution_id"],
        "started_at": _now_iso(), "progress": {"step": "starting"},
    }])
    return execution_id


def run_aggregation(client, run_id: str, execution_id: str) -> dict:
    """geo_report_recordsとgeo_report_sentiment_cluster_classifications（Canonical
    Prompt1のexecution_id）を結合し、3-way分母フィルタ・隔週次/週次/月次集計・
    denominator_auditを計算する。CSVはgeo_report_run_artifacts
    （artifact_type='aggregation_output'）へ保存する。

    関数全体をtry/exceptで包み、どの段階で例外が起きてもexecutionを必ず'failed'へ
    更新する（Phase 5のE2E検証で発覚したBLOCKER: 一部のバリデーションエラーだけが
    status更新せずraiseしており、該当ExecutionがRun全体を巻き込んで'running'の
    まま止まり続け、UIが完了も失敗も表示できなくなっていた）。"""
    try:
        run = _get_run(client, run_id)
        prompt1_execution_id = run["canonical_prompt1_execution_id"]

        config_rows = client.select("geo_report_run_config", {"select": "*", "run_id": f"eq.{run_id}"})
        if not config_rows:
            raise ValueError(f"run_id={run_id} にgeo_report_run_configがありません。")
        config = config_rows[0]
        primary_graph_signals = config.get("primary_graph_signals") or []
        common_start = agg.parse_date(config.get("common_primary_graph_start"))
        if not primary_graph_signals or common_start is None:
            raise ValueError(f"run_id={run_id} のreport_configにprimary_graph_signals/"
                              "common_primary_graph_startが設定されていません。")

        records = _select_all(client, "geo_report_records", {"run_id": f"eq.{run_id}"})
        classification_rows = _select_all(client, "geo_report_sentiment_cluster_classifications", {
            "execution_id": f"eq.{prompt1_execution_id}",
        })
        classifications = {r["sentiment_cluster_id"]: r for r in classification_rows}
        topic_rows = _select_all(client, "geo_report_sentiment_clusters", {"run_id": f"eq.{run_id}"})
        report_topic_label_by_signal = {}
        for row in topic_rows:
            if row.get("signal_id") and row.get("report_topic_label"):
                report_topic_label_by_signal.setdefault(row["signal_id"], row["report_topic_label"])

        client.update("geo_report_executions", {"id": f"eq.{execution_id}"}, {
            "progress": {"step": "joining"},
        })
        joined = agg.join_records_with_sentiment_classification(records, classifications)

        biweekly_rows = agg.build_biweekly_trend_rows(
            joined, primary_graph_signals=primary_graph_signals, common_primary_graph_start=common_start,
            report_topic_label_by_signal=report_topic_label_by_signal)
        weekly_rows = agg.build_weekly_trend_rows(
            joined, primary_graph_signals=primary_graph_signals, common_primary_graph_start=common_start,
            report_topic_label_by_signal=report_topic_label_by_signal)
        monthly_rows = agg.build_monthly_summary_rows(
            joined, primary_graph_signals=primary_graph_signals, common_primary_graph_start=common_start,
            report_topic_label_by_signal=report_topic_label_by_signal)
        denominator_rows = agg.build_denominator_audit_rows(
            records, joined, primary_graph_signals=primary_graph_signals, common_primary_graph_start=common_start)

        artifacts = [
            _artifact_row(client, run_id, execution_id, "aggregation_output", "biweekly_trend_data.csv",
                          _dicts_to_csv_text(biweekly_rows), "text/csv"),
            _artifact_row(client, run_id, execution_id, "aggregation_output", "weekly_trend_data.csv",
                          _dicts_to_csv_text(weekly_rows), "text/csv"),
            _artifact_row(client, run_id, execution_id, "aggregation_output", "monthly_summary_data.csv",
                          _dicts_to_csv_text(monthly_rows), "text/csv"),
            _artifact_row(client, run_id, execution_id, "aggregation_output", "denominator_audit.csv",
                          _dicts_to_csv_text(denominator_rows), "text/csv"),
        ]
        for chunk in _chunked(artifacts):
            client.insert("geo_report_run_artifacts", chunk)

        client.update("geo_report_executions", {"id": f"eq.{execution_id}"}, {
            "status": "completed", "completed_at": _now_iso(), "progress": {"step": "done"},
        })
        return {
            "execution_id": execution_id, "biweekly_rows": biweekly_rows, "weekly_rows": weekly_rows,
            "monthly_rows": monthly_rows, "denominator_rows": denominator_rows,
        }
    except Exception:
        client.update("geo_report_executions", {"id": f"eq.{execution_id}"}, {
            "status": "failed", "completed_at": _now_iso(),
        })
        raise


def _select_all(client, table: str, params: dict) -> list:
    # PostgRESTのdb-max-rows既定値(1000件)により、ページングなしのselect()は実データ規模
    # （geo_report_records等は1 Runで万単位になりうる）で黙って先頭1000件に切り詰められる
    # （2026-09-12、Prompt1側の同種バグ発覚を受けて確認・修正）。_select_all_paginatedへ委譲する。
    query = {"select": "*", **params}
    return _paginated_select(client, table, query)


_NUMERIC_SUFFIXES = ("_n", "_share", "_rate")


def _coerce_numeric_fields(row: dict) -> dict:
    """CSVは値を全て文字列として保存する。件数(_n)・比率(_share/_rate)列は下流で算術に
    使う（例: render_stacked_bar_chart_svgのbar_width = share * width）ため、読み戻し時に
    int/floatへ復元する（空文字列はNoneのまま、qa_notes等の非数値列は素通しする）。"""
    out = dict(row)
    for key, value in row.items():
        if not any(key.endswith(suffix) for suffix in _NUMERIC_SUFFIXES):
            continue
        if value in (None, ""):
            out[key] = None
            continue
        try:
            out[key] = int(value) if key.endswith("_n") else float(value)
        except (TypeError, ValueError):
            pass
    return out


def _load_aggregation_artifact_rows(client, aggregation_execution_id: str, file_name: str) -> list:
    """aggregation_output artifactを読み戻し、CSVテキストをlist[dict]へ変換する
    （件数・比率列はint/floatへ復元する）。"""
    rows = client.select("geo_report_run_artifacts", {
        "select": "content_text,storage_bucket,storage_path", "execution_id": f"eq.{aggregation_execution_id}",
        "artifact_type": "eq.aggregation_output", "file_name": f"eq.{file_name}",
    })
    if not rows or (not rows[0].get("content_text") and not rows[0].get("storage_path")):
        return []
    return [_coerce_numeric_fields(r) for r in _parse_csv_text(artifact_storage.read_artifact_content(client, rows[0]))]


def _parse_csv_text(text: str) -> list:
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


# ---------------------------------------------------------------------------
# Prompt2 (Composition) Execution
# ---------------------------------------------------------------------------

def create_prompt2_execution(client, run_id: str, aggregation_execution_id: str, *,
                               execution_method: str = "application_ai") -> str:
    execution_id = _new_id()
    client.insert("geo_report_executions", [{
        "id": execution_id, "run_id": run_id, "stage": "prompt2", "execution_method": execution_method,
        "status": "running", "input_aggregation_execution_id": aggregation_execution_id,
        "started_at": _now_iso(), "progress": {"sections_completed": 0, "sections_total": 0},
    }])
    return execution_id


def plan_composition_batches(client, run_id: str, execution_id: str, template_asset: dict) -> list:
    sections = template_asset["content"].get("sections", [])
    payload = [{
        "id": _new_id(), "run_id": run_id, "execution_id": execution_id, "stage": "prompt2_composition",
        "batch_no": i + 1, "item_ids": [s["section_id"]], "status": "pending",
    } for i, s in enumerate(sorted(sections, key=lambda s: s.get("order", 0)))]
    for chunk in _chunked(payload):
        client.insert("geo_report_execution_batches", chunk)
    _update_prompt2_progress(client, execution_id)
    return payload


def _update_prompt2_progress(client, execution_id: str) -> None:
    batches = client.select("geo_report_execution_batches", {"select": "status", "execution_id": f"eq.{execution_id}"})
    total = len(batches)
    completed = len([b for b in batches if b["status"] in ("completed", "skipped")])
    failed = len([b for b in batches if b["status"] == "failed"])
    client.update("geo_report_executions", {"id": f"eq.{execution_id}"}, {
        "progress": {"sections_completed": completed, "sections_failed": failed, "sections_total": total},
    })


def _build_shared_composition_context(client, run_id: str, aggregation_execution_id: str) -> dict:
    """全Section共通のコンテキスト（実際のPrompt2がChatGPTへ全入力ファイルをまとめて渡す
    のと同じ思想——Sectionごとに個別に絞り込まず、集計結果全体を渡してAIに関連部分を
    判断させる）。"""
    return {
        "biweekly_trend": _load_aggregation_artifact_rows(client, aggregation_execution_id, "biweekly_trend_data.csv"),
        "denominator_audit": _load_aggregation_artifact_rows(client, aggregation_execution_id, "denominator_audit.csv"),
    }


def _upsert_composition_result(client, execution_id: str, run_id: str, section_id: str, composed: dict) -> None:
    content_text = json.dumps(composed, ensure_ascii=False)
    existing = client.select("geo_report_run_artifacts", {
        "select": "id", "execution_id": f"eq.{execution_id}", "artifact_type": "eq.composition_result",
        "file_name": f"eq.{section_id}.json",
    })
    storage_fields = artifact_storage.build_artifact_storage_fields(
        client, run_id, execution_id, "composition_result", f"{section_id}.json",
        content_text.encode("utf-8"), content_type="application/json")
    if existing:
        client.update("geo_report_run_artifacts", {"id": f"eq.{existing[0]['id']}"}, storage_fields)
    else:
        client.insert("geo_report_run_artifacts", [{
            "id": _new_id(), "run_id": run_id, "execution_id": execution_id, "execution_method": "application_ai",
            "artifact_type": "composition_result", "file_name": f"{section_id}.json", "media_type": "application/json",
            **storage_fields,
        }])


def _get_next_pending_batch(client, execution_id: str, stage: str) -> dict:
    rows = client.select("geo_report_execution_batches", {
        "select": "*", "execution_id": f"eq.{execution_id}", "stage": f"eq.{stage}",
        "status": "eq.pending", "order": "batch_no.asc", "limit": "1",
    })
    return rows[0] if rows else None


def _mark_batch(client, batch_id: str, status: str, *, error_message: str = None) -> None:
    patch = {"status": status}
    if status == "running":
        patch["started_at"] = _now_iso()
    if status in ("completed", "failed"):
        patch["completed_at"] = _now_iso()
    if error_message is not None:
        patch["error_message"] = error_message
    client.update("geo_report_execution_batches", {"id": f"eq.{batch_id}"}, patch)


def _process_composition_batch(client, azure_client, model: str, run_id: str, execution_id: str, batch: dict, *,
                                 template_asset: dict) -> dict:
    if cancellation.is_cancel_requested(client, run_id):
        return {"processed": True, "batch_id": batch["id"], "status": "cancelled"}

    _mark_batch(client, batch["id"], "running")

    execution_rows = client.select("geo_report_executions", {"select": "*", "id": f"eq.{execution_id}"})
    aggregation_execution_id = execution_rows[0]["input_aggregation_execution_id"]
    section_id = batch["item_ids"][0]
    section_def = next(
        (s for s in template_asset["content"]["sections"] if s["section_id"] == section_id), None)
    if section_def is None:
        _mark_batch(client, batch["id"], "failed", error_message=f"section_id={section_id} がTemplateに見つかりません。")
        _update_prompt2_progress(client, execution_id)
        return {"processed": True, "batch_id": batch["id"], "status": "failed"}

    try:
        python_data = _build_shared_composition_context(client, run_id, aggregation_execution_id)
        result = comp.compose_section(azure_client, model, section_def, python_data)
        _upsert_composition_result(client, execution_id, run_id, section_id, result["data"])
    except Exception as exc:
        _mark_batch(client, batch["id"], "failed", error_message=str(exc))
        _update_prompt2_progress(client, execution_id)
        return {"processed": True, "batch_id": batch["id"], "status": "failed", "error": str(exc)}

    _mark_batch(client, batch["id"], "completed")
    _update_prompt2_progress(client, execution_id)
    return {"processed": True, "batch_id": batch["id"], "status": "completed"}


def run_next_composition_batch(client, azure_client, model: str, run_id: str, execution_id: str, *,
                                 template_asset: dict) -> dict:
    batch = _get_next_pending_batch(client, execution_id, "prompt2_composition")
    if not batch:
        return {"processed": False}
    return _process_composition_batch(
        client, azure_client, model, run_id, execution_id, batch, template_asset=template_asset)


def _list_pending_batches(client, execution_id: str, stage: str) -> list:
    return client.select("geo_report_execution_batches", {
        "select": "*", "execution_id": f"eq.{execution_id}", "stage": f"eq.{stage}",
        "status": "eq.pending", "order": "batch_no.asc",
    })


def run_all_pending_composition_batches(client, azure_client, model: str, run_id: str, execution_id: str, *,
                                          template_asset: dict, max_batches: int = None) -> list:
    """pending中のSectionバッチを(Section単位=バッチ単位でLLM呼び出しが独立しているため)
    まとめて並列実行する。Prompt1と異なりバッチ内アイテムが常に1件なので、
    バッチをまたいで並列化する（BATCH_CONCURRENCYを共有）。"""
    pending = _list_pending_batches(client, execution_id, "prompt2_composition")
    if max_batches is not None:
        pending = pending[:max_batches]
    if not pending:
        return []
    results = []
    with ThreadPoolExecutor(max_workers=min(BATCH_CONCURRENCY, len(pending)) or 1) as pool:
        futures = [
            pool.submit(_process_composition_batch, client, azure_client, model, run_id, execution_id, batch,
                        template_asset=template_asset)
            for batch in pending
        ]
        for future in as_completed(futures):
            results.append(future.result())
    return results


# ---------------------------------------------------------------------------
# Prompt2完了処理（Final Report組み立て・Canonical Output 10ファイル）
# ---------------------------------------------------------------------------

def finalize_prompt2_execution(client, run_id: str, execution_id: str, *,
                                 report_theme: str = "", model: str = "") -> dict:
    """全Sectionの構成が完了していればFinal Reportを組み立て、Canonical Output 10ファイルを
    保存し、Runのcanonical_prompt2_execution_idを確定する。未完了・失敗バッチが残っていれば
    {"completed": False}を返す。"""
    batches = client.select("geo_report_execution_batches", {"select": "status", "execution_id": f"eq.{execution_id}"})
    if not batches or any(b["status"] in ("pending", "running", "failed") for b in batches):
        return {"completed": False}

    run = _get_run(client, run_id)
    execution = client.select("geo_report_executions", {"select": "*", "id": f"eq.{execution_id}"})[0]
    aggregation_execution_id = execution["input_aggregation_execution_id"]

    template_asset = assets.get_asset_by_id(client, run["report_template_asset_id"])
    composition_rows = client.select("geo_report_run_artifacts", {
        "select": "*", "execution_id": f"eq.{execution_id}", "artifact_type": "eq.composition_result",
    })
    section_composed_by_id = {
        row["file_name"].removesuffix(".json"): json.loads(artifact_storage.read_artifact_content(client, row))
        for row in composition_rows
    }

    biweekly_rows = _load_aggregation_artifact_rows(client, aggregation_execution_id, "biweekly_trend_data.csv")
    weekly_rows = _load_aggregation_artifact_rows(client, aggregation_execution_id, "weekly_trend_data.csv")
    monthly_rows = _load_aggregation_artifact_rows(client, aggregation_execution_id, "monthly_summary_data.csv")
    denominator_rows = _load_aggregation_artifact_rows(client, aggregation_execution_id, "denominator_audit.csv")

    section_html_by_id = {}
    exec_summary_section_id = None
    for section_def in template_asset["content"]["sections"]:
        section_id = section_def["section_id"]
        composed = section_composed_by_id.get(section_id)
        if composed is None:
            continue
        chart_svg = None
        if section_def.get("component_type") == "stacked_bar_chart":
            chart_svg = comp.render_stacked_bar_chart_svg(biweekly_rows)
        section_html_by_id[section_id] = comp.render_report_section(section_def, composed, chart_svg=chart_svg)
        if section_def.get("component_type") == "narrative_block" and exec_summary_section_id is None:
            exec_summary_section_id = section_id

    final_report_html = comp.render_final_report(template_asset["content"], section_html_by_id)

    continuity_context_next = output_svc.build_continuity_context_next(
        [], report_theme=report_theme, current_period=_now_iso())
    report_brief_md = output_svc.build_report_brief_md(
        section_composed_by_id, executive_summary_section_id=exec_summary_section_id)
    config_rows = client.select("geo_report_run_config", {"select": "*", "run_id": f"eq.{run_id}"})
    config = config_rows[0] if config_rows else {}
    methodology_notes_md = output_svc.build_methodology_notes_md(
        prompt1_contract_version=run.get("prompt1_contract_version") or "",
        prompt2_contract_version=run.get("prompt2_contract_version") or "",
        common_primary_graph_start=str(config.get("common_primary_graph_start") or ""),
        primary_graph_signals=config.get("primary_graph_signals") or [], model=model)
    continuity_change_log_md = output_svc.build_continuity_change_log_md(
        continuity_context_next, has_baseline=bool(run.get("baseline_run_id")))
    reference_examples_rows = []  # Phase D側でPublic Voice Examplesの引用選定配線が済み次第拡充する

    output_artifacts = [
        _artifact_row(client, run_id, execution_id, "final_report", "final_report.html", final_report_html,
                      "text/html"),
        _artifact_row(client, run_id, execution_id, "prompt2_output", "report_brief.md", report_brief_md,
                      "text/markdown"),
        _artifact_row(client, run_id, execution_id, "prompt2_output", "biweekly_trend_data.csv",
                      _dicts_to_csv_text(biweekly_rows), "text/csv"),
        _artifact_row(client, run_id, execution_id, "prompt2_output", "weekly_trend_data.csv",
                      _dicts_to_csv_text(weekly_rows), "text/csv"),
        _artifact_row(client, run_id, execution_id, "prompt2_output", "monthly_summary_data.csv",
                      _dicts_to_csv_text(monthly_rows), "text/csv"),
        _artifact_row(client, run_id, execution_id, "prompt2_output", "denominator_audit.csv",
                      _dicts_to_csv_text(denominator_rows), "text/csv"),
        _artifact_row(client, run_id, execution_id, "prompt2_output", "reference_examples_used.csv",
                      _dicts_to_csv_text(reference_examples_rows, output_svc.REFERENCE_EXAMPLES_USED_COLUMNS),
                      "text/csv"),
        _artifact_row(client, run_id, execution_id, "prompt2_output", "continuity_context_next.json",
                      output_svc.serialize_continuity_context_next(continuity_context_next), "application/json"),
        _artifact_row(client, run_id, execution_id, "prompt2_output", "continuity_change_log.md",
                      continuity_change_log_md, "text/markdown"),
        _artifact_row(client, run_id, execution_id, "prompt2_output", "methodology_notes.md",
                      methodology_notes_md, "text/markdown"),
    ]
    for chunk in _chunked(output_artifacts):
        client.insert("geo_report_run_artifacts", chunk)

    client.update("geo_report_executions", {"id": f"eq.{execution_id}"}, {
        "status": "completed", "completed_at": _now_iso(),
    })
    canonical.set_canonical_execution(client, run_id, "prompt2", execution_id)
    return {"completed": True, "final_report_html": final_report_html}
