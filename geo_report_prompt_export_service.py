"""Report Studio「Design」領域: ChatGPT Fallback（Phase F）。

Current Prompt1/Prompt2の実際のファイル構成（C/E/F/G章、Phase A監査で実測確認済み）と
完全互換のZIPパッケージをExport/Importする。内側ZIPの構造・列名は一切変更しない
（kind-napping-hare.md「要件10」）。Application AI経路とChatGPT経路は、Canonical
Prompt1/Prompt2契約という共通の成果物形式でのみ合流する（内部処理経路は異なる、
G.3参照）。

Prompt2 Base Inputの一部（population_narrative_by_signal_language.csv）は、
`geo_report_records`に`language`列が無いため現時点ではヘッダのみで出力する
（Phase D未確定事項として明記。列追加は今後のマイグレーションで対応）。
"""

from __future__ import annotations

import concurrent.futures
import csv
import io
import json
import uuid
import time
import zipfile
from datetime import datetime, timezone

import geo_report_artifact_storage_service as artifact_storage
import geo_report_current_pipeline_persistence_service as persistence
import geo_report_current_pipeline_service as cp
import geo_report_execution_canonical_service as canonical
import geo_report_profile_registry as registry
import geo_report_project_assets_service as assets
import geo_report_prompt1_classification_service as classify
import geo_report_prompt1_output_service as prompt1_output
import geo_report_prompt2_output_service as prompt2_output
import geo_report_prompt2_package_validation_service as package_validation
import geo_report_run_cancellation_service as cancellation
from geo_external_signal_service import _select_all_paginated as _paginated_select
from geo_report_run_provisioning_service import load_prompt_instruction

# ---------------------------------------------------------------------------
# 静的テキスト（実際のrun.ps1が同梱するREADME、Phase A監査で全文確認済み）
# ---------------------------------------------------------------------------

README_PROMPT2_MD = """Prompt 2 receives two complementary Prompt 1 result layers.

1. Use the standard Prompt 1 files (`classified_clusters.csv`, `public_voice_examples.csv`, `continuity_change_candidates.csv`, `prompt1_summary.md`) for qualitative findings, representative voices and continuity assessment.
2. Join `sentiment_cluster_classified.csv` to `record_cluster_map_*.csv` by `sentiment_cluster_id` for record-level weekly sentiment totals.
3. Exclude every row where `is_duplicate=true`. Duplicate rows intentionally have blank cluster IDs and are retained only for audit.
4. Use record-level overrides only when Prompt 1 explicitly supplies them.
5. Do not exclude a cluster solely because preliminary_consistency is Medium or Low.
6. Final graph denominator: non-duplicate records classified as Public Voice with attitude positive, neutral, or negative and theme_relevance=relevant.
7. Use the common_primary_graph_start in monitoring_scope.csv.
8. Do not treat the preliminary weekly basis as final sentiment data.
9. If `sentiment_cluster_classified.csv` is absent, do not publish sentiment proportions or n counts; return to Prompt 1 and generate the supplemental file.
"""

PROMPT1_INPUT_REQUIRED_FILES = [
    "cluster_labeling_input.csv", "cluster_representatives.csv", "cluster_manifest.csv",
    "preprocess_audit_summary.csv", "sentiment_cluster_labeling_input.csv",
    "sentiment_cluster_representatives.csv", "sentiment_exception_review_candidates.csv",
    "sentiment_cluster_classification_spec.md", "signal_narrative_master.csv", "monitoring_scope.csv",
    "report_config.yaml",
]

PROMPT1_OUTPUT_REQUIRED_FILES = [
    "classified_clusters.csv", "public_voice_examples.csv", "continuity_change_candidates.csv",
    "narrative_master_update_candidates.csv", "sentiment_cluster_classified.csv",
    "targeted_stance_classified.csv", "prompt1_quality_audit.csv", "prompt1_summary.md",
    "classification_rule_applied.md",
]

PROMPT2_BASE_INPUT_FIXED_FILES = [
    "all_clusters.csv", "cluster_representatives.csv", "sentiment_cluster_representatives.csv",
    "weekly_trend_raw_basis.csv", "timeline_basis.csv", "source_bucket_summary.csv",
    "population_narrative_by_signal.csv", "population_narrative_by_signal_date.csv",
    "population_narrative_by_signal_language.csv", "monitoring_scope.csv", "report_config.yaml",
    "README_prompt2.md", "raw_reference_candidates.csv", "signal_narrative_master.csv",
]

# record_cluster_map_*.csvの1ファイルあたり行数（build_record_cluster_map_chunksの既定、
# Prompt 2 base ZIPの自動検証でchunk数の期待値計算にも使う）。
RECORD_MAP_CHUNK_SIZE = cp.RECORD_MAP_CHUNK_SIZE

# Prompt 2 base ZIP生成時の自動検証（2026-10-03、Preprocess潜在不具合監査 #11）を有効にするか。
# 本番は常にTrue。テストで検証対象外のシナリオ（discovery provenance等）を単純化するときだけ
# monkeypatchでFalseにする。
PROMPT2_BASE_VALIDATION_ENABLED = True

PROMPT2_OUTPUT_REQUIRED_FILES = [
    "final_report.html", "report_brief.md", "biweekly_trend_data.csv", "weekly_trend_data.csv",
    "monthly_summary_data.csv", "denominator_audit.csv", "reference_examples_used.csv",
    "continuity_context_next.json", "continuity_change_log.md", "methodology_notes.md",
]


def _new_id() -> str:
    return str(uuid.uuid4())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Prompt 1/2 ZIP生成前のReport Contract検証（2026-10-05）。Report Type・Prompt/Template profile・観測期間・
# Signal・primary_graph_signalsが解決されていて、Runに固定されたPrompt本文がprofileと一致するかを確認し、
# 不一致ならZIPを生成しない（別案件のPromptを渡すより、止める方が安全）。
REPORT_CONTRACT_VALIDATION_ENABLED = True


def _run_config_json(client, run_id: str) -> dict:
    rows = client.select("geo_report_run_config", {"select": "config_json", "run_id": f"eq.{run_id}"})
    return (rows[0].get("config_json") if rows else None) or {}


def _assert_report_contract(client, run_id: str, run: dict, *, monitoring_scope_csv=None) -> None:
    if not REPORT_CONTRACT_VALIDATION_ENABLED:
        return
    template_content = None
    if run.get("report_template_asset_id"):
        template_content = assets.get_asset_by_id(client, run["report_template_asset_id"]).get("content")
    scope_rows = (list(csv.DictReader(io.StringIO(monitoring_scope_csv))) if monitoring_scope_csv else None)
    issues = registry.validate_run_contract(
        _run_config_json(client, run_id),
        prompt1_text=_artifact_content_optional(client, run_id, "prompt_snapshot", "prompt1_instruction.md"),
        prompt2_text=_artifact_content_optional(client, run_id, "prompt_snapshot", "prompt2_instruction.md"),
        template_content=template_content, monitoring_scope_rows=scope_rows,
        run_period=(run.get("period_start"), run.get("period_end")))
    errors = registry.errors_only(issues)
    if errors:
        raise ValueError("Report Contractの検証に失敗したため、Packageを生成しません"
                         f"（run_id={run_id}）:\n" + registry.format_issues(errors))


def _run_aware_readme_note(client, run_id: str, run: dict) -> str:
    """README_prompt2.mdの末尾に付ける、このRunのReport Contract（種別・期間・Template・Signal）。
    Runにreport_kindが固定されていない旧Runでは空文字（従来のREADMEのまま）。"""
    cfg = _run_config_json(client, run_id)
    if not cfg.get("report_kind"):
        return ""
    template_hash = (cfg.get("template_hash") or "")[:12]
    lines = [
        "", "## Run context (Report Contract of this Run)", "",
        f"- Report kind: {cfg.get('report_kind')} ({cfg.get('report_title') or ''})",
        f"- Analysis contract: {cfg.get('analysis_contract')}",
        f"- Prompt 1 profile: {cfg.get('prompt1_profile')} / Prompt 2 profile: {cfg.get('prompt2_profile')}",
        f"- Template: {cfg.get('template_id')} (asset {cfg.get('template_asset_id')}, hash {template_hash})",
        f"- Current observation window: {cfg.get('observation_start_date') or '(not set)'} - "
        f"{cfg.get('observation_end_date') or '(not set)'}",
        f"- Historical trend baseline: {cfg.get('historical_trend_start_date') or '(none)'}",
        f"- Primary chart start: {cfg.get('primary_chart_start') or '(not set)'}",
        f"- Configured Signals ({len(cfg.get('signals') or [])}): {', '.join(cfg.get('signals') or [])}",
        f"- primary_graph_signals ({cfg.get('primary_graph_signals_source')}): "
        f"{', '.join(cfg.get('primary_graph_signals') or [])}",
        "",
        "Date semantics: `common_primary_graph_start` equals `primary_chart_start` and is never earlier than the "
        "current observation window start. `historical_trend_start_date` is a baseline setting, not the current "
        "report period; do not draw periods for which this package contains no data.",
        "",
    ]
    return "\n".join(lines)


def get_run(client, run_id: str) -> dict:
    rows = client.select("geo_report_collection_runs", {"select": "*", "run_id": f"eq.{run_id}"})
    if not rows:
        raise ValueError(f"run_id={run_id} が見つかりません。")
    return rows[0]


def _resolve_canonical_preprocess_execution_id(client, run_id: str):
    """run["canonical_prompt1_execution_id"]からgeo_report_executions.input_preprocess_
    execution_idを辿り、Canonical Prompt1が実際に分類対象として使ったPreprocess
    Executionのidを返す（Phase H、2026-09-16。discovery系artifactの取得を
    run_id単体スコープからProvenance経由へ統一するため）。

    2026-09-16追加: Canonical Prompt1がまだ確定していない場合（Prompt1/Prompt2を
    1セッションで通して行うCombined ChatGPT Export、Preprocess完了直後に呼ばれる）は、
    run["canonical_preprocess_execution_id"]（そのRun自体のCanonical Preprocess）へ
    フォールバックする——Prompt1が存在しない段階では、これが唯一の正しい参照先になる。"""
    run = get_run(client, run_id)
    prompt1_execution_id = run.get("canonical_prompt1_execution_id")
    if prompt1_execution_id:
        rows = client.select("geo_report_executions",
            {"select": "input_preprocess_execution_id", "id": f"eq.{prompt1_execution_id}"})
        if rows and rows[0].get("input_preprocess_execution_id"):
            return rows[0]["input_preprocess_execution_id"]
    return run.get("canonical_preprocess_execution_id")


def _artifact_content_for_execution(client, execution_id: str, artifact_type: str, file_name: str):
    """_artifact_content_optionalと同じ挙動だが、run_id単体ではなくexecution_idで
    絞り込む（同一Runに複数のPreprocess/Prompt1 Executionが存在する場合でも、
    指定したExecutionが実際に生成したartifactだけを取得する）。存在しなければ
    例外を投げずNoneを返す。"""
    if not execution_id:
        return None
    rows = client.select("geo_report_run_artifacts", {
        "select": "content_text,storage_bucket,storage_path", "execution_id": f"eq.{execution_id}",
        "artifact_type": f"eq.{artifact_type}", "file_name": f"eq.{file_name}",
    })
    if not rows or (rows[0].get("content_text") is None and rows[0].get("storage_path") is None):
        return None
    return artifact_storage.read_artifact_content(client, rows[0])


def _artifact_content(client, run_id: str, artifact_type: str, file_name: str) -> str:
    rows = client.select("geo_report_run_artifacts", {
        "select": "content_text,storage_bucket,storage_path", "run_id": f"eq.{run_id}",
        "artifact_type": f"eq.{artifact_type}", "file_name": f"eq.{file_name}",
    })
    if not rows or (rows[0].get("content_text") is None and rows[0].get("storage_path") is None):
        raise ValueError(f"artifact not found: run_id={run_id} artifact_type={artifact_type} file_name={file_name}")
    return artifact_storage.read_artifact_content(client, rows[0])


def get_final_report_html(client, run_id: str) -> str:
    """Runの`canonical_report_execution_id`（Application AIのfinalize、ChatGPT Import、
    将来のReport Editのいずれで確定したものでも共通）が指すExecutionのfinal_report.html
    を返す。画面リロード後もCanonical Reportを再表示できるようにする唯一の読み出し口。"""
    run = get_run(client, run_id)
    execution_id = run.get("canonical_report_execution_id")
    if not execution_id:
        raise ValueError(f"run_id={run_id} はまだFinal Reportが確定していません（canonical_report_execution_id無し）。")
    rows = client.select("geo_report_run_artifacts", {
        "select": "content_text,storage_bucket,storage_path", "execution_id": f"eq.{execution_id}",
        "artifact_type": "eq.final_report",
    })
    if not rows or (rows[0].get("content_text") is None and rows[0].get("storage_path") is None):
        raise ValueError(f"execution_id={execution_id}のfinal_report artifactが見つかりません。")
    return artifact_storage.read_artifact_content(client, rows[0])


def _zip_files(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            zf.writestr(name, data)
    return buf.getvalue()


def _unzip_files(zip_bytes: bytes) -> dict:
    files = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if name.endswith("/"):
                continue
            files[name] = zf.read(name)
    return files


def _parse_csv_bytes(data: bytes) -> list:
    text = data.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def _rows_to_csv_text(rows: list, columns: list) -> str:
    return cp.rows_to_csv_text(rows, columns)


# ---------------------------------------------------------------------------
# Prompt1 Package Export
# ---------------------------------------------------------------------------

def _artifact_content_optional(client, run_id: str, artifact_type: str, file_name: str):
    """discovery_candidates.csv等、discovery_mode Signalが無い/コーパスが0件のRunでは
    そもそも生成されない任意artifact向け。存在しなければ例外を投げずNoneを返す。"""
    rows = client.select("geo_report_run_artifacts", {
        "select": "content_text,storage_bucket,storage_path", "run_id": f"eq.{run_id}",
        "artifact_type": f"eq.{artifact_type}", "file_name": f"eq.{file_name}",
    })
    if not rows or (rows[0].get("content_text") is None and rows[0].get("storage_path") is None):
        return None
    return artifact_storage.read_artifact_content(client, rows[0])


def _prompt1_input_artifact(client, run_id: str, name: str, *, execution_id=None) -> str:
    """Preprocessの成果物(artifact_type='prompt1_input')を読む。canonical Preprocess executionが分かる場合は
    そのexecutionのartifactを使う（2026-10-04、Run isolation）。従来は`run_id`だけで引いていたため、同じRunで
    Preprocessをやり直すと旧実行のartifactが複数あり、先頭1件（任意）を読んでいた。executionが無い旧Run、
    またはそのexecutionに該当artifactが無い場合は、従来どおりrun単位で読む（後方互換）。"""
    if execution_id:
        content = _artifact_content_for_execution(client, execution_id, "prompt1_input", name)
        if content is not None:
            return content
    return _artifact_content(client, run_id, "prompt1_input", name)


def _assert_record_set_matches_execution(client, run_id: str, records: list, execution_id) -> bool:
    """export対象のrecord集合（DBのgeo_report_records、run_id単位）が、今回のPreprocess executionが作った
    期待集合（record_id_manifest.csv）と一致することを検証する。stale（旧実行の行）またはmissing（欠落）が
    1件でもあればValueError（silent warningで続行しない）。期待集合を持たない旧Runは検証できないためFalseを返す
    （呼び出し側の件数・source_file検証は引き続き働く）。"""
    if not execution_id:
        return False
    text = _artifact_content_for_execution(client, execution_id, "prompt1_input",
                                            package_validation.RECORD_MANIFEST_FILE)
    if text is None:
        return False
    result = package_validation.compare_record_sets(
        package_validation.counter_from_manifest_csv(text), package_validation.counter_from_db_records(records))
    if not result["ok"]:
        raise ValueError(package_validation.describe_record_set_mismatch(result))
    return True


# discovery_mode Signalが有効かつコーパスが0件でない場合のみ生成される任意ファイル
# （2026-09-16追加、#4）。固定11ファイル契約(C章)には含めず、存在する場合だけZIPへ足す。
PROMPT1_INPUT_OPTIONAL_FILES = ["discovery_candidates.csv", "discovery_candidate_representatives.csv"]


def build_prompt1_input_zip(client, run_id: str) -> bytes:
    """C章11ファイルの内側ZIP。10ファイルはPreprocess時点で既にgeo_report_run_artifacts
    （artifact_type='prompt1_input'）へ保存済み——ここから読み戻すだけ。
    signal_narrative_master.csvだけはRunの固定Narrative Master Assetから都度生成する
    （Assetが正本のため、Artifactとして二重保存しない）。

    discovery_candidates.csv/discovery_candidate_representatives.csv（discovery_mode
    Signal向け、任意）は固定11ファイルとは別に、存在する場合のみ追加で同梱する
    （2026-09-16修正: 従来ここでの読み出し漏れにより、Preprocessで生成済みでも
    ZIPに入らずPrompt1がS6のtopic discoveryを完了できない不具合があった）。"""
    run = get_run(client, run_id)
    narrative_master_asset = assets.get_asset_by_id(client, run["narrative_master_asset_id"])
    master_csv = assets.narrative_master_rows_to_csv_text(narrative_master_asset["content"].get("rows", []))

    execution_id = _resolve_canonical_preprocess_execution_id(client, run_id)
    files = {name: _prompt1_input_artifact(client, run_id, name, execution_id=execution_id)
              for name in PROMPT1_INPUT_REQUIRED_FILES if name != "signal_narrative_master.csv"}
    files["signal_narrative_master.csv"] = master_csv
    _assert_report_contract(client, run_id, run, monitoring_scope_csv=files.get("monitoring_scope.csv"))
    for name in PROMPT1_INPUT_OPTIONAL_FILES:
        content = (_artifact_content_for_execution(client, execution_id, "prompt1_input", name)
                   if execution_id else None)
        if content is None:
            content = _artifact_content_optional(client, run_id, "prompt1_input", name)
        if content is not None:
            files[name] = content
    if PROMPT2_BASE_VALIDATION_ENABLED:
        issues = package_validation.validate_prompt1_input_counts(files)
        if package_validation.has_errors(issues):
            raise ValueError("Prompt 1 inputの自動検証に失敗したため、Packageを生成しません"
                             f"（run_id={run_id}）:\n" + package_validation.format_issues(issues))
    return _zip_files(files)


def export_prompt1_package(client, run_id: str) -> bytes:
    """外側ZIP = 内側prompt1_input_<run_id>.zip（構造無変更）＋Runにpinされた
    Prompt1指示文（prompt1_instruction.md）。"""
    inner_zip = build_prompt1_input_zip(client, run_id)
    instruction_text = _artifact_content(client, run_id, "prompt_snapshot", "prompt1_instruction.md")
    return _zip_files({
        f"prompt1_input_{run_id}.zip": inner_zip,
        "prompt1_instruction.md": instruction_text,
    })


# ---------------------------------------------------------------------------
# Prompt1 Package Import
# ---------------------------------------------------------------------------

def import_prompt1_result(client, run_id: str, zip_bytes: bytes, *, actor_id: str = "chatgpt_import") -> dict:
    """prompt1_output_<timestamp>.zip（E章9ファイル）を取り込む。
    classified_clusters.csv/sentiment_cluster_classified.csv/targeted_stance_classified.csvを
    DB正規化テーブルへ保存し（geo_report_prompt1_orchestration_serviceの保存関数を再利用、
    Application AI経路と同じCanonical契約テーブルへ合流させる）、9ファイル全てをそのまま
    geo_report_run_artifacts（artifact_type='prompt1_output'）へ保存する。"""
    files = _unzip_files(zip_bytes)
    files_text = {name: (data.decode("utf-8-sig") if isinstance(data, (bytes, bytearray)) else data)
                  for name, data in files.items()}
    missing = [f for f in PROMPT1_OUTPUT_REQUIRED_FILES if f not in files]
    if missing:
        raise ValueError(f"Prompt1 Outputに必須ファイルがありません: {missing}")

    # Import時のContract Validation（新規、2026-09-16、Phase H）: 不正なOutputを
    # DB保存・Canonical採用させる前に、prompt1_contract_versionに応じたschemaを
    # 検証する。discovery_expectedはこのRunにdiscovery_mode Signalが設定されて
    # いるか（geo_report_run_config.config_json.discovery_signal_idsの有無）で判定する。
    run = get_run(client, run_id)
    prompt1_contract_version = run.get("prompt1_contract_version") or "v4.3"
    config_rows = client.select("geo_report_run_config", {"select": "config_json", "run_id": f"eq.{run_id}"})
    discovery_signal_ids = ((config_rows[0]["config_json"].get("discovery_signal_ids")
                              if config_rows else None) or [])
    schema_problems = prompt1_output.validate_prompt1_output_schema(
        files_text, prompt1_contract_version, discovery_expected=bool(discovery_signal_ids))
    if schema_problems:
        raise ValueError(f"Prompt1 Outputがcontract validationに失敗しました: {schema_problems}")

    execution_id = _new_id()
    client.insert("geo_report_executions", [{
        "id": execution_id, "run_id": run_id, "stage": "prompt1", "execution_method": "chatgpt_import",
        "status": "running", "started_at": _now_iso(),
    }])

    # 2026-09-16修正: この関数は元々1行ずつselect→insert/updateしていたため実データ
    # 規模（数千行）では数十分かかっていた（ユーザー報告:「ChatGPTで処理済みのものを
    # アップロードしただけなのに、なぜPrompt1が動いているのか」——実際には分類作業を
    # やり直しているのではなく、DBへの保存だけで長時間かかっていた）。
    # execution_idはこの関数の直前で新規発行したものであり、既存行が存在し得ないため
    # （同じexecution_idへの2回目の保存は無い）、select-checkを伴うupsertではなく、
    # 直接の一括insertへ変更する（この点だけがApplication AI経路のsave_*関数
    # （バッチ再試行等でupsertが必要）と異なる、ChatGPT Import専用の最適化）。
    _BATCH_INSERT_CHUNK_SIZE = 500

    def _bulk_insert_with_progress(table: str, payloads: list, *, phase: str) -> None:
        total = len(payloads)
        persistence.update_preprocess_progress(
            client, execution_id, files_processed=0, files_total=total, phase=phase)
        for i in range(0, total, _BATCH_INSERT_CHUNK_SIZE):
            cancellation.raise_if_cancelled(client, run_id)
            client.insert(table, payloads[i:i + _BATCH_INSERT_CHUNK_SIZE])
            persistence.update_preprocess_progress(
                client, execution_id, files_processed=min(i + _BATCH_INSERT_CHUNK_SIZE, total),
                files_total=total, phase=phase)

    try:
        sentiment_rows = _parse_csv_bytes(files["sentiment_cluster_classified.csv"])
        sentiment_payloads = [{
            "id": _new_id(), "execution_id": execution_id, "run_id": run_id,
            "sentiment_cluster_id": row["sentiment_cluster_id"],
            "theme_relevance": row.get("theme_relevance"), "source_class": row.get("source_class"),
            "reaction_type": row.get("reaction_type"), "attitude": row.get("attitude"),
            # evidence_intelligence_v1のsentiment_cluster_classified.csvはPublic Voice版と
            # 列構成が異なり、source_class/reaction_type/attitudeの代わりにこの2列を持つ
            # （2026-09-16追加。CSVに無ければNone＝該当列は書き込まれない）。
            "actor_category": row.get("actor_category"), "evidence_relation": row.get("evidence_relation"),
            "cluster_consistency": row.get("cluster_consistency"),
            "apply_to_cluster": row.get("apply_to_cluster", "").lower() == "true",
            "exception_pattern": classify.exception_pattern_to_text(row.get("exception_pattern", "")),
            "classification_reason": row.get("classification_reason", ""),
            # 2026-09-16修正: 空文字だとDBのCHECK制約(available/unavailable/ambiguous/
            # not_applicable)に違反する。evidence_intelligence_v1のCSVにはこの列自体が
            # 無いため、欠落時はNULL（制約対象外）にする。
            "targeted_stance_status": row.get("targeted_stance_status") or None,
            "targeted_stance_count": int(row.get("targeted_stance_count") or 0),
        } for row in sentiment_rows]
        _bulk_insert_with_progress("geo_report_sentiment_cluster_classifications", sentiment_payloads,
                                     phase="importing_sentiment_classifications")

        classified_rows = _parse_csv_bytes(files["classified_clusters.csv"])
        classified_payloads = []
        for row in classified_rows:
            data = {k: v for k, v in row.items() if k not in ("cluster_id", "signal_id", "report_topic_label")}
            data["noise_flag"] = str(row.get("noise_flag", "")).lower() == "true"
            classified_payloads.append({
                "id": _new_id(), "execution_id": execution_id, "run_id": run_id,
                "cluster_id": row["cluster_id"], **data,
            })
        _bulk_insert_with_progress("geo_report_analysis_cluster_classifications", classified_payloads,
                                     phase="importing_analysis_classifications")

        stance_rows = _parse_csv_bytes(files["targeted_stance_classified.csv"])
        targets_by_cluster: dict = {}
        for row in stance_rows:
            targets_by_cluster.setdefault(row["sentiment_cluster_id"], []).append({
                "target_type": row.get("target_type"), "target_name": row.get("target_name"),
                "target_normalized_id": row.get("target_normalized_id") or None,
                "stance_polarity": row.get("stance_polarity"), "stance_strength": row.get("stance_strength"),
                "stance_confidence": row.get("stance_confidence"), "stance_evidence": row.get("stance_evidence"),
                "stance_basis": row.get("stance_basis"),
                "stance_cluster_consistency": row.get("stance_cluster_consistency"),
                "apply_stance_to_cluster": str(row.get("apply_stance_to_cluster", "")).lower() == "true",
                "stance_exception_pattern": row.get("stance_exception_pattern", ""),
                "stance_reason": row.get("stance_reason", ""),
            })
        stance_payloads = []
        for cluster_id, targets in targets_by_cluster.items():
            # save_targeted_stanceと同じdedupロジック（同一cluster内の同一target_key
            # 重複を先勝ちで排除、unique(execution_id, sentiment_cluster_id, target_type,
            # target_key)制約違反を防ぐ）。新規execution_idのためdelete-then-insertの
            # deleteは不要（既存行が無い）。
            seen_keys = set()
            for t in targets:
                target_key = t.get("target_normalized_id") or t.get("target_name")
                dedup_key = (t["target_type"], target_key)
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)
                stance_payloads.append({
                    "id": _new_id(), "execution_id": execution_id, "run_id": run_id,
                    "sentiment_cluster_id": cluster_id, "signal_id": t.get("signal_id"),
                    "target_type": t["target_type"], "target_name": t["target_name"],
                    "target_normalized_id": t.get("target_normalized_id"),
                    "stance_polarity": t["stance_polarity"], "stance_strength": t["stance_strength"],
                    "stance_confidence": t["stance_confidence"], "stance_evidence": t["stance_evidence"],
                    "stance_basis": t["stance_basis"],
                    "stance_cluster_consistency": t["stance_cluster_consistency"],
                    "apply_stance_to_cluster": t["apply_stance_to_cluster"],
                    "stance_exception_pattern": t.get("stance_exception_pattern", ""),
                    "stance_reason": t["stance_reason"],
                })
        _bulk_insert_with_progress("geo_report_targeted_stance", stance_payloads,
                                     phase="importing_targeted_stance")
    except Exception as exc:
        client.update("geo_report_executions", {"id": f"eq.{execution_id}"}, {
            "status": "failed", "completed_at": _now_iso(),
        })
        raise ValueError(f"Prompt1 Outputの取り込みに失敗しました: {exc}")

    artifact_rows = []
    for file_name, data in files.items():
        text = data.decode("utf-8-sig") if isinstance(data, (bytes, bytearray)) else data
        media_type = "text/markdown" if file_name.endswith(".md") else "text/csv"
        storage_fields = artifact_storage.build_artifact_storage_fields(
            client, run_id, execution_id, "prompt1_output", file_name, text.encode("utf-8"),
            content_type=media_type)
        artifact_rows.append({
            "id": _new_id(), "run_id": run_id, "execution_id": execution_id, "execution_method": "chatgpt_import",
            "artifact_type": "prompt1_output", "file_name": file_name, "media_type": media_type,
            **storage_fields,
        })
    for i in range(0, len(artifact_rows), 500):
        client.insert("geo_report_run_artifacts", artifact_rows[i:i + 500])

    client.update("geo_report_executions", {"id": f"eq.{execution_id}"}, {
        "status": "completed", "completed_at": _now_iso(),
    })
    return {"execution_id": execution_id}


# ---------------------------------------------------------------------------
# Prompt2 Base Input（14固定ファイル＋record_cluster_map_*.csv N個）
# ---------------------------------------------------------------------------

# PostgRESTのdb-max-rows（Supabase既定1000件）に合わせる。これより大きく指定しても1回に返るのは1000件。
RECORD_FETCH_PAGE_SIZE = 1000
RECORD_FETCH_MAX_ATTEMPTS = 8
RECORD_FETCH_MIN_PAGE_SIZE = 100
# geo_report_recordsはidがランダムなuuidで、(run_id, id)の索引が無い。実DB(2026-10-04)では1ページ(1000件)が
# 平均十数秒〜数十秒かかり、US(21万件)で80分超の見込みだった。uuidのid空間を16分割して並列に取得する
# （各範囲は独立したキーセットページング）。順序は呼び出し元が使わない。1にすると従来の逐次取得。
RECORD_FETCH_PARALLEL_RANGES = 16
RECORD_FETCH_WORKERS = 8


def _select_records_page(client, params: dict, page_size: int) -> tuple:
    """geo_report_recordsの1ページを取得する。5xx（実DBでは`canceling statement due to statement timeout`、
    code 57014）は一過性のことが多い（同じクエリが次の試行で成功する、2026-10-04確認）ため、待ってから再試行し、
    2回失敗したらページサイズを半分にして負荷を下げる。戻り値は(page, 次回以降のpage_size)。
    根本対策はgeo_report_records(run_id, id)の複合索引（スキーマ変更のため要承認、
    docs/report-studio/2026-10-04_preprocess_phase2/PROPOSED_execution_id_migration.sqlに提案）。"""
    last_exc = None
    for attempt in range(RECORD_FETCH_MAX_ATTEMPTS):
        try:
            return client.select("geo_report_records", {**params, "limit": str(page_size)}), page_size
        except Exception as exc:  # noqa: BLE001 — 5xx以外（4xx等、再送しても直らない）は即座に送出
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is None or status < 500:
                raise
            last_exc = exc
            if attempt >= 1:
                page_size = max(RECORD_FETCH_MIN_PAGE_SIZE, page_size // 2)
            time.sleep(min(30, 2 * (attempt + 1)))
    raise last_exc


def _uuid_prefix_ranges(n: int) -> list:
    """uuidのid空間を先頭1桁(16進)で分割した[(下限, 上限)]。上限は排他的、最後の範囲だけ最大値までを含む。"""
    assert n == 16
    bounds = [f"{i:x}0000000-0000-0000-0000-000000000000" for i in range(16)]
    ranges = []
    for i, lo in enumerate(bounds):
        hi = bounds[i + 1] if i + 1 < len(bounds) else None
        ranges.append((lo, hi))
    return ranges


def _fetch_records_in_range(client, run_id: str, lo, hi, select_columns: str) -> list:
    records = []
    page_size = RECORD_FETCH_PAGE_SIZE
    last_id = None
    condition = f"(id.gte.{lo},id.lt.{hi})" if hi else f"(id.gte.{lo})"      # 最後の範囲は上限なし
    while True:
        params = {"select": select_columns, "run_id": f"eq.{run_id}", "order": "id.asc", "and": condition}
        if last_id is not None:
            params["id"] = f"gt.{last_id}"
        page, page_size = _select_records_page(client, params, page_size)
        if not page:
            break
        records.extend(page)
        last_id = page[-1]["id"]
    return records


def _fetch_all_records(client, run_id: str) -> list:
    """2026-09-17変更、ユーザー報告「ダウンロードしたらinternal server errorが出た」
    への対応: 従来は`record_id`（索引の無いtext列）でorder+OFFSETページングして
    いたが、694,000件規模のRunでSupabase側が500を返す事象が繰り返し発生した
    （索引が無い列でのソート＋大きなOFFSETは件数が増えるほど重くなる）。
    `id`（主キー、索引済みのuuid）を使ったキーセットページング（`id=gt.前回最後のid`）
    に変更する——件数に関わらず各ページの取得コストがほぼ一定になる。record_idの
    昇順である必要は無い（呼び出し元は集計・chunk分割にしか使わず、順序に依存しない）。

    2026-09-17追加: 69万件規模だと1000件/ページでは694回もの逐次往復が必要になり、
    1回あたりの遅延（社内プロキシ経由）が積み重なって現実的な時間で終わらなかった。
    1ページの中身はJSON行データで、Storageの大容量ファイルほど重くはないため、
    ページサイズを増やして往復回数を減らす方を優先する。"""
    if RECORD_FETCH_PARALLEL_RANGES > 1:
        select_columns = ",".join(["id"] + list(_RECORD_MAP_DB_TO_CSV))
        ranges = _uuid_prefix_ranges(RECORD_FETCH_PARALLEL_RANGES)
        with concurrent.futures.ThreadPoolExecutor(max_workers=RECORD_FETCH_WORKERS) as pool:
            parts = list(pool.map(lambda r: _fetch_records_in_range(client, run_id, r[0], r[1], select_columns), ranges))
        return [record for part in parts for record in part]
    records = []
    page_size = RECORD_FETCH_PAGE_SIZE
    last_id = None
    # 必要な列だけ取得する（select=*はexport/検証で使わない列まで運び、実DBではstatement timeoutの一因になる）
    select_columns = ",".join(["id"] + list(_RECORD_MAP_DB_TO_CSV))
    while True:
        params = {
            "select": select_columns, "run_id": f"eq.{run_id}", "order": "id.asc", "limit": str(page_size),
        }
        if last_id is not None:
            params["id"] = f"gt.{last_id}"
        page, page_size = _select_records_page(client, params, page_size)
        if not page:
            break
        records.extend(page)
        last_id = page[-1]["id"]
        # 2026-10-03修正（Preprocess潜在不具合監査 #11）: 従来はここで
        # `if len(page) < page_size: break`としていたが、PostgRESTのdb-max-rows（Supabase既定
        # 1000件）により、limit=5000を指定しても1回に返るのは1000件に頭打ちになる。
        # 1000 < 5000で最初のページの直後にbreakし、US中間選挙(209,976件)・Evidence(391,909件)の
        # record_cluster_map_001.csvが1,000行しか無く、population/weekly/source_bucketの派生
        # CSVも全て同じ1,000件分（0.5%/0.26%）から再集計されていた（2026-09-16の過去Runは全件で正常）。
        # サーバ側の上限を知る必要が無いよう「空ページが返るまで」続ける
        # （最後に空ページ取得で1往復余分に増えるだけ）。
    return records


_RECORD_MAP_DB_TO_CSV = {
    "record_id": "record_id", "timestamp_raw": "timestamp", "date_key": "date_key",
    "week_start": "week_start", "week_end": "week_end", "signal_id": "signal_source",
    "theme_id": "theme_id", "theme_name": "theme_name", "trend_role": "trend_role",
    "trend_start_date": "trend_start_date", "source_file": "source_file", "source_bucket": "source_bucket",
    "source_class_preliminary": "source_class_preliminary", "reaction_type_hint": "reaction_type_hint",
    "attitude_hint": "attitude_hint", "narrative_id": "narrative_id", "narrative_name": "narrative_name",
    "match_status": "match_status", "matched_terms": "matched_terms", "cluster_cue": "cluster_cue",
    "match_signature": "match_signature", "analysis_cluster_id": "analysis_cluster_id",
    "sentiment_cluster_id": "sentiment_cluster_id", "is_duplicate": "is_duplicate",
    "duplicate_group_id": "duplicate_group_id", "url_hash": "url_hash", "text_hash": "text_hash",
}


def build_record_cluster_map_chunks(records: list, *, chunk_size: int = RECORD_MAP_CHUNK_SIZE) -> list:
    """DB行(geo_report_records)をCurrent契約の27列CSVへ変換し、chunk_size件ずつ分割する。"""
    csv_rows = []
    for r in records:
        csv_row = {csv_col: r.get(db_col, "") for db_col, csv_col in _RECORD_MAP_DB_TO_CSV.items()}
        csv_rows.append(csv_row)
    chunks = []
    for i in range(0, len(csv_rows), chunk_size):
        chunks.append(csv_rows[i:i + chunk_size])
    if not chunks:
        chunks = [[]]
    return [_rows_to_csv_text(chunk, cp.RECORD_MAP_COLUMNS) for chunk in chunks]


def _build_population_and_trend_csvs(records: list) -> dict:
    """population_narrative_by_signal*.csv・weekly_trend_raw_basis.csv・
    source_bucket_summary.csvを、永続化済みgeo_report_recordsから再集計する
    （PreprocessResultはRun作成時点で使い捨てのため、Export時はDBから再導出する）。
    population_narrative_by_signal_language.csvはgeo_report_recordsにlanguage列が
    無いため、現時点ではヘッダのみ（Phase D未確定事項として明記）。"""
    population_narrative: dict = {}
    population_date: dict = {}
    weekly_counts: dict = {}
    source_bucket_counts: dict = {}

    for r in records:
        if r.get("is_duplicate"):
            continue
        signal_id = r.get("signal_id", "")
        narrative_id = r.get("narrative_id", "")
        narrative_name = r.get("narrative_name", "")
        date_key = r.get("date_key", "")
        source_class = r.get("source_class_preliminary", "")
        source_bucket = r.get("source_bucket", "")
        attitude_hint = r.get("attitude_hint", "")
        week_start = r.get("week_start", "")
        week_end = r.get("week_end", "")
        theme_id = r.get("theme_id", "")
        theme_name = r.get("theme_name", "")
        trend_role = r.get("trend_role", "")

        key1 = f"{signal_id}|{narrative_id}|{narrative_name}"
        population_narrative[key1] = population_narrative.get(key1, 0) + 1
        key2 = f"{date_key}|{signal_id}|{narrative_id}"
        population_date[key2] = population_date.get(key2, 0) + 1
        if week_start and week_start != "unknown":
            key3 = f"{week_start}|{week_end}|{signal_id}|{theme_id}|{theme_name}|{trend_role}|{source_class}|{attitude_hint}"
            weekly_counts[key3] = weekly_counts.get(key3, 0) + 1
        key4 = f"{signal_id}|{source_bucket}|{source_class}"
        source_bucket_counts[key4] = source_bucket_counts.get(key4, 0) + 1

    pop_rows = []
    for key, count in sorted(population_narrative.items()):
        signal_id, narrative_id, narrative_name = key.split("|", 2)
        pop_rows.append({"signal_source": signal_id, "narrative_id": narrative_id,
                          "narrative_name": narrative_name, "records": count})

    date_rows = []
    for key, count in sorted(population_date.items()):
        date_key, signal_id, narrative_id = key.split("|", 2)
        date_rows.append({"date": date_key, "signal_source": signal_id, "narrative_id": narrative_id,
                          "records": count})

    weekly_rows = []
    for key, count in sorted(weekly_counts.items()):
        parts = key.split("|", 7)
        weekly_rows.append({
            "week_start": parts[0], "week_end": parts[1], "signal_source": parts[2], "theme_id": parts[3],
            "theme_name": parts[4], "trend_role": parts[5], "source_class_preliminary": parts[6],
            "attitude_hint": parts[7], "records": count,
            "note": "Preliminary audit basis. Final trends require Prompt 1 cluster labels and record map join.",
        })

    bucket_rows = []
    for key, count in sorted(source_bucket_counts.items()):
        signal_id, source_bucket, source_class = key.split("|", 2)
        bucket_rows.append({"signal_source": signal_id, "source_bucket": source_bucket,
                            "source_class_preliminary": source_class, "nonduplicate_records": count})

    return {
        "population_narrative_by_signal.csv": _rows_to_csv_text(
            pop_rows, ["signal_source", "narrative_id", "narrative_name", "records"]),
        "population_narrative_by_signal_date.csv": _rows_to_csv_text(
            date_rows, ["date", "signal_source", "narrative_id", "records"]),
        "population_narrative_by_signal_language.csv": _rows_to_csv_text(
            [], ["signal_source", "narrative_id", "language_group", "records"]),
        "weekly_trend_raw_basis.csv": _rows_to_csv_text(
            weekly_rows, ["week_start", "week_end", "signal_source", "theme_id", "theme_name", "trend_role",
                          "source_class_preliminary", "attitude_hint", "records", "note"]),
        "source_bucket_summary.csv": _rows_to_csv_text(
            bucket_rows, ["signal_source", "source_bucket", "source_class_preliminary", "nonduplicate_records"]),
    }


def build_prompt2_base_input_zip(client, run_id: str) -> bytes:
    run = get_run(client, run_id)
    narrative_master_asset = assets.get_asset_by_id(client, run["narrative_master_asset_id"])
    master_csv = assets.narrative_master_rows_to_csv_text(narrative_master_asset["content"].get("rows", []))

    execution_id = _resolve_canonical_preprocess_execution_id(client, run_id)
    all_clusters_csv = _prompt1_input_artifact(client, run_id, "cluster_labeling_input.csv", execution_id=execution_id)
    cluster_reps_csv = _prompt1_input_artifact(client, run_id, "cluster_representatives.csv", execution_id=execution_id)
    sentiment_reps_csv = _prompt1_input_artifact(
        client, run_id, "sentiment_cluster_representatives.csv", execution_id=execution_id)
    monitoring_scope_csv = _prompt1_input_artifact(client, run_id, "monitoring_scope.csv", execution_id=execution_id)
    report_config_yaml = _prompt1_input_artifact(client, run_id, "report_config.yaml", execution_id=execution_id)
    _assert_report_contract(client, run_id, run, monitoring_scope_csv=monitoring_scope_csv)

    records = _fetch_all_records(client, run_id)
    if PROMPT2_BASE_VALIDATION_ENABLED:
        _assert_record_set_matches_execution(client, run_id, records, execution_id)
    derived = _build_population_and_trend_csvs(records)
    record_map_chunks = build_record_cluster_map_chunks(records)

    files = {
        "all_clusters.csv": all_clusters_csv,
        "cluster_representatives.csv": cluster_reps_csv,
        "sentiment_cluster_representatives.csv": sentiment_reps_csv,
        "timeline_basis.csv": derived["weekly_trend_raw_basis.csv"],
        **derived,
        "monitoring_scope.csv": monitoring_scope_csv,
        "report_config.yaml": report_config_yaml,
        "README_prompt2.md": README_PROMPT2_MD + _run_aware_readme_note(client, run_id, run),
        "raw_reference_candidates.csv": sentiment_reps_csv,
        "signal_narrative_master.csv": master_csv,
    }
    for i, chunk_text in enumerate(record_map_chunks, start=1):
        files[f"record_cluster_map_{i:03d}.csv"] = chunk_text

    # discovery_mode Signal向けの生Discovery Artifact（2026-09-16追加、Phase H）。
    # Canonical Prompt1が実際に分類対象として使ったPreprocess Execution
    # （run_id単体スコープではない）から取得する——同一Runで複数のPreprocess
    # Executionが存在する場合でも、Canonical Prompt1が参照したものだけを使う。
    config_rows = client.select("geo_report_run_config", {"select": "config_json", "run_id": f"eq.{run_id}"})
    discovery_signal_ids = ((config_rows[0]["config_json"].get("discovery_signal_ids")
                              if config_rows else None) or [])
    preprocess_execution_id = _resolve_canonical_preprocess_execution_id(client, run_id)
    for name in PROMPT1_INPUT_OPTIONAL_FILES:
        content = (_artifact_content_for_execution(client, preprocess_execution_id, "prompt1_input", name)
                   if preprocess_execution_id else None)
        if content is not None:
            files[name] = content
        elif discovery_signal_ids:
            raise ValueError(
                f"discovery_mode Signal({discovery_signal_ids})が設定されているのに"
                f"{name}がCanonical Prompt1が参照するPreprocess Executionの"
                "Artifactに見つかりません。")
    zip_bytes = _zip_files(files)
    if PROMPT2_BASE_VALIDATION_ENABLED:
        _validate_prompt2_base_package_or_raise(
            client, run_id, run, files, zip_bytes, discovery_signal_ids=discovery_signal_ids,
            analysis_mode=(config_rows[0]["config_json"].get("analysis_mode") if config_rows else None))
    return zip_bytes


def _validate_prompt2_base_package_or_raise(client, run_id: str, run: dict, files: dict, zip_bytes: bytes, *,
                                              discovery_signal_ids: list, analysis_mode) -> None:
    """生成したPrompt 2 base ZIPを自動検証し、errorが1件でもあればValueErrorで生成を失敗させる
    （不完全なZIPをCompleteとして返さない）。warningはここでは握りつぶさず、必要なら呼び出し側が
    package_validation.validate_prompt2_base_files()を直接呼んで確認できる。"""
    manifest = {}
    manifest_text = None
    execution_id = _resolve_canonical_preprocess_execution_id(client, run_id)
    if execution_id:
        manifest_text = _artifact_content_for_execution(client, execution_id, "prompt1_input", "cluster_manifest.csv")
    if manifest_text is None:
        manifest_text = _artifact_content_optional(client, run_id, "prompt1_input", "cluster_manifest.csv")
    if manifest_text:
        rows = _parse_csv_bytes(manifest_text.encode("utf-8") if isinstance(manifest_text, str) else manifest_text)
        manifest = rows[0] if rows else {}
    issues, _ = package_validation.validate_zip_integrity(zip_bytes)
    issues += package_validation.validate_prompt2_base_files(
        files, required_files=PROMPT2_BASE_INPUT_FIXED_FILES, manifest=manifest,
        record_map_chunk_size=RECORD_MAP_CHUNK_SIZE, discovery_signal_ids=discovery_signal_ids,
        expected_analysis_contract=cp.resolve_analysis_contract(analysis_mode, run.get("prompt1_contract_version")))
    if package_validation.has_errors(issues):
        raise ValueError(
            "Prompt 2 base inputの自動検証に失敗したため、Packageを生成しません"
            f"（run_id={run_id}）:\n" + package_validation.format_issues(issues))


# ---------------------------------------------------------------------------
# Prompt1 Output ZIP再構成（Application AI経路 or 既存ChatGPT取り込み結果から）
# ---------------------------------------------------------------------------

def build_prompt1_output_zip_for_run(client, run_id: str, prompt1_execution_id: str) -> bytes:
    """Canonical Prompt1 Execution（Application AIまたはChatGPT Import）からE章9ファイルを
    再構成する。ChatGPT Importの場合は取り込み時に保存済みの生ファイルをそのまま使う
    （二重に組み立て直さない）。Application AIの場合はDB正規化テーブルから組み立てる。"""
    existing = client.select("geo_report_run_artifacts", {
        "select": "file_name,content_text,storage_bucket,storage_path", "execution_id": f"eq.{prompt1_execution_id}",
        "artifact_type": "eq.prompt1_output",
    })
    if existing:
        files = {row["file_name"]: artifact_storage.read_artifact_content(client, row) for row in existing}
        missing = [f for f in PROMPT1_OUTPUT_REQUIRED_FILES if f not in files]
        if not missing:
            return _zip_files(files)

    # PostgRESTのdb-max-rows既定値(1000件)により、ページングなしのselect()は実データ規模
    # （本Runだけでanalysis cluster 1,477件・representatives 4,211件）で黙って先頭1000件に
    # 切り詰められる（2026-09-12発覚、Prompt1/Prompt2側の同種バグと合わせて修正）。
    analysis_cluster_rows = _paginated_select(client, "geo_report_analysis_clusters",
                                                {"select": "*", "run_id": f"eq.{run_id}"})
    analysis_cluster_rows = [{**r, "cluster_id": r["analysis_cluster_id"]} for r in analysis_cluster_rows]
    analysis_classifications = {
        row["cluster_id"]: row for row in _paginated_select(client, "geo_report_analysis_cluster_classifications", {
            "select": "*", "execution_id": f"eq.{prompt1_execution_id}",
        })
    }
    sentiment_classifications = {
        row["sentiment_cluster_id"]: row
        for row in _paginated_select(client, "geo_report_sentiment_cluster_classifications", {
            "select": "*", "execution_id": f"eq.{prompt1_execution_id}",
        })
    }
    stance_rows = _paginated_select(client, "geo_report_targeted_stance", {
        "select": "*", "execution_id": f"eq.{prompt1_execution_id}",
    })
    targeted_stance_by_cluster: dict = {}
    for row in stance_rows:
        targeted_stance_by_cluster.setdefault(row["sentiment_cluster_id"], []).append(row)

    classified_clusters_rows = prompt1_output.build_classified_clusters_rows(
        analysis_cluster_rows, analysis_classifications)
    representatives_by_cluster: dict = {}
    for rep in _paginated_select(client, "geo_report_analysis_cluster_representatives",
                                   {"select": "*", "run_id": f"eq.{run_id}"}):
        representatives_by_cluster.setdefault(rep["analysis_cluster_id"], []).append(rep)
    public_voice_rows = prompt1_output.build_public_voice_examples_rows(
        analysis_cluster_rows, analysis_classifications, representatives_by_cluster, targeted_stance_by_cluster)
    sentiment_classified_rows = prompt1_output.build_sentiment_cluster_classified_rows(
        sentiment_classifications, targeted_stance_by_cluster)
    sentiment_cluster_stats = {
        row["sentiment_cluster_id"]: row
        for row in _paginated_select(client, "geo_report_sentiment_clusters", {"select": "*", "run_id": f"eq.{run_id}"})
    }
    # geo_report_sentiment_cluster_classificationsにはsignal_id列が無いため、
    # signal_idは常にPreprocess事実テーブル(geo_report_sentiment_clusters)側から引く。
    signal_by_cluster = {sid: row.get("signal_id", "") for sid, row in sentiment_cluster_stats.items()}
    targeted_stance_rows = prompt1_output.build_targeted_stance_classified_rows(
        targeted_stance_by_cluster, signal_by_cluster)
    audit_rows = _compute_prompt1_audit_from_db(sentiment_cluster_stats, sentiment_classifications,
                                                  targeted_stance_by_cluster)

    files = {
        "classified_clusters.csv": _rows_to_csv_text(classified_clusters_rows,
                                                       prompt1_output.CLASSIFIED_CLUSTERS_COLUMNS),
        "public_voice_examples.csv": _rows_to_csv_text(public_voice_rows,
                                                         prompt1_output.PUBLIC_VOICE_EXAMPLES_COLUMNS),
        "continuity_change_candidates.csv": _rows_to_csv_text([],
                                                                 prompt1_output.CONTINUITY_CHANGE_CANDIDATES_COLUMNS),
        "narrative_master_update_candidates.csv": _rows_to_csv_text(
            [], prompt1_output.NARRATIVE_MASTER_UPDATE_CANDIDATES_COLUMNS),
        "sentiment_cluster_classified.csv": _rows_to_csv_text(sentiment_classified_rows,
                                                                 prompt1_output.SENTIMENT_CLUSTER_CLASSIFIED_COLUMNS),
        "targeted_stance_classified.csv": _rows_to_csv_text(targeted_stance_rows,
                                                               prompt1_output.TARGETED_STANCE_CLASSIFIED_COLUMNS),
        "prompt1_quality_audit.csv": _rows_to_csv_text(audit_rows, prompt1_output.PROMPT1_QUALITY_AUDIT_COLUMNS),
        "prompt1_summary.md": prompt1_output.build_prompt1_summary_md(audit_rows, classified_clusters_rows, []),
        "classification_rule_applied.md": prompt1_output.build_classification_rule_applied_md("v4.3"),
    }
    return _zip_files(files)


def _compute_prompt1_audit_from_db(sentiment_cluster_stats: dict, sentiment_classifications: dict,
                                     targeted_stance_by_cluster: dict) -> list:
    import geo_report_current_pipeline_service as _cp
    clusters = {}
    for cluster_id, row in sentiment_cluster_stats.items():
        rule = _cp.Rule(
            signal_source=row.get("signal_id", ""), signal_name=row.get("signal_name", ""),
            theme_id=row.get("theme_id", ""), theme_name=row.get("theme_name", ""),
            trend_role=row.get("trend_role", ""), trend_start_date=row.get("trend_start_date", ""),
            main_narrative=row.get("main_narrative", ""), sub_narrative=row.get("narrative_name", ""),
            sub_narrative_id=row.get("narrative_id", ""), stance_default="",
        )
        clusters[cluster_id] = _cp.ClusterStats(
            cluster_id=cluster_id, kind="sentiment", rule=rule, week_start=row.get("week_start", ""),
            week_end=row.get("week_end", ""), source_class_preliminary=row.get("source_class_preliminary", ""),
            source_bucket=row.get("source_bucket", ""), reaction_type_hint=row.get("reaction_type_hint", ""),
            attitude_hint=row.get("attitude_hint", ""), cluster_cue=row.get("cluster_cue", ""),
            match_signature=row.get("match_signature", ""), representative_limit=8,
            record_count=row.get("cluster_records", 0), duplicate_count=row.get("duplicate_records", 0),
            fallback_count=int(round((row.get("fallback_share") or 0) * (row.get("cluster_records") or 0))),
        )
    return classify.compute_prompt1_quality_audit(clusters, sentiment_classifications, targeted_stance_by_cluster)


# ---------------------------------------------------------------------------
# Prompt2 Package Export / Import
# ---------------------------------------------------------------------------

def _discovery_preflight_check(client, run_id: str, prompt1_execution_id: str) -> None:
    """discovery_mode Signalが存在するRunで、Prompt2 Export前にCanonical Prompt1
    Outputのdiscovery分類が完了・1:1 Contractを満たしているかを検証する
    （2026-09-16追加、Phase H）。問題があればValueErrorでPackage生成自体を拒否する
    （Provenance/Coverage validation。Import時のContract Validationとは別のGate
    ——Canonical切り替え後に別のPreprocess Executionを参照するようになった場合の
    不整合はImport時には捉えられないため、Export時にも必ず検証する）。"""
    config_rows = client.select("geo_report_run_config", {"select": "config_json", "run_id": f"eq.{run_id}"})
    discovery_signal_ids = ((config_rows[0]["config_json"].get("discovery_signal_ids")
                              if config_rows else None) or [])
    if not discovery_signal_ids:
        return

    prompt1_output_rows = client.select("geo_report_run_artifacts", {
        "select": "file_name,content_text,storage_bucket,storage_path",
        "execution_id": f"eq.{prompt1_execution_id}", "artifact_type": "eq.prompt1_output",
    })
    prompt1_output_files = {row["file_name"]: row for row in prompt1_output_rows}
    required_discovery_outputs = {"discovery_candidates_classified.csv", "discovery_evidence_examples.csv"}
    missing = required_discovery_outputs - prompt1_output_files.keys()
    if missing:
        raise ValueError(
            f"discovery_mode Signal({discovery_signal_ids})が設定されていますが、"
            f"Canonical Prompt1 Outputに{sorted(missing)}が見つかりません"
            "（Prompt1 Discovery classification incomplete）。Prompt2 Packageは生成しません。")

    preprocess_execution_id = _resolve_canonical_preprocess_execution_id(client, run_id)
    raw_discovery_csv = (_artifact_content_for_execution(
        client, preprocess_execution_id, "prompt1_input", "discovery_candidates.csv")
        if preprocess_execution_id else None)
    if raw_discovery_csv is not None:
        raw_rows = _parse_csv_bytes(raw_discovery_csv.encode("utf-8"))
        classified_text = artifact_storage.read_artifact_content(
            client, prompt1_output_files["discovery_candidates_classified.csv"])
        classified_rows = _parse_csv_bytes(classified_text.encode("utf-8"))
        raw_ids_list = [row["discovery_cluster_id"] for row in raw_rows]
        classified_ids_list = [row["discovery_cluster_id"] for row in classified_rows]
        raw_ids, classified_ids = set(raw_ids_list), set(classified_ids_list)
        problems = []
        if len(raw_ids_list) != len(raw_ids):
            problems.append("discovery_candidates.csv内にdiscovery_cluster_idの重複がある")
        if len(classified_ids_list) != len(classified_ids):
            problems.append("discovery_candidates_classified.csv内にdiscovery_cluster_idの重複がある")
        missing_in_classified = raw_ids - classified_ids
        if missing_in_classified:
            problems.append(f"rawにあるがclassifiedに無いID: {sorted(missing_in_classified)[:5]}"
                             f"（計{len(missing_in_classified)}件）")
        extra_in_classified = classified_ids - raw_ids
        if extra_in_classified:
            problems.append(f"classifiedにのみ存在するID（raw側に無い）: "
                             f"{sorted(extra_in_classified)[:5]}（計{len(extra_in_classified)}件）")
        if problems:
            raise ValueError(
                "discovery_candidates_classified.csvが1:1 Contractを満たしていません: "
                + " / ".join(problems) + "（Prompt2 Packageは生成しません）。")


def export_prompt2_package(client, run_id: str) -> bytes:
    run = get_run(client, run_id)
    prompt1_execution_id = run.get("canonical_prompt1_execution_id")
    if not prompt1_execution_id:
        raise ValueError(f"run_id={run_id} はCanonical Prompt1が未確定です。")

    _discovery_preflight_check(client, run_id, prompt1_execution_id)

    prompt1_output_zip = build_prompt1_output_zip_for_run(client, run_id, prompt1_execution_id)
    prompt2_base_input_zip = build_prompt2_base_input_zip(client, run_id)
    template_asset = assets.get_asset_by_id(client, run["report_template_asset_id"])
    template_html = _render_template_preview_html(template_asset["content"])
    template_json = json.dumps(
        sorted(template_asset["content"].get("sections", []), key=lambda s: s.get("order", 0)),
        ensure_ascii=False, indent=2)
    instruction_text = _artifact_content(client, run_id, "prompt_snapshot", "prompt2_instruction.md")

    files = {
        f"prompt1_output_{run_id}.zip": prompt1_output_zip,
        f"prompt2_base_input_{run_id}.zip": prompt2_base_input_zip,
        "report_template.html": template_html,
        "report_template.json": template_json,
        "prompt2_instruction.md": instruction_text,
    }
    if run.get("previous_continuity_artifact_id"):
        continuity_rows = client.select("geo_report_run_artifacts", {
            "select": "content_text,storage_bucket,storage_path", "id": f"eq.{run['previous_continuity_artifact_id']}",
        })
        if continuity_rows:
            files["previous_continuity_context.json"] = artifact_storage.read_artifact_content(
                client, continuity_rows[0])
    return _zip_files(files)


def _render_template_preview_html(template_content: dict) -> str:
    """ChatGPTへ渡す参考情報としてのTemplateプレビュー（章構成・スタイルの見本、
    visual/style reference only）。実データは含めない（Template Asset自体が
    Placeholder化済みのため安全）。

    2026-09-16修正: required_fieldsが含まれていなかった実バグを修正——Source of
    Truthとして機能するために必須の情報（各Sectionがどのフィールドを要求するか）が
    従来ChatGPTへ渡っていなかった。ただし本Sectionの構造・required_fieldsの
    確定仕様としての比較基準は常に`report_template.json`（export_prompt2_package参照）
    側であり、この関数の出力（HTML）は視覚的な参考情報に留める。"""
    sections_html = "".join(
        f'<section data-section-id="{s.get("section_id")}"><h2>{s.get("title")}</h2>'
        f'<p>component_type: {s.get("component_type")}</p>'
        f'<p>required_fields: {", ".join(s.get("required_fields", []))}</p>'
        f'{s.get("example_markup", "")}</section>'
        for s in sorted(template_content.get("sections", []), key=lambda s: s.get("order", 0))
    )
    shell_html = template_content.get("shell_html", "<html><head>{style}</head><body>{sections}</body></html>")
    return (shell_html
            .replace("{style}", template_content.get("style_css", ""))
            .replace("{sections}", sections_html))


def import_prompt2_result(client, run_id: str, zip_bytes: bytes) -> dict:
    """prompt2_output_<timestamp>.zip（G章10ファイル）を取り込む。
    ChatGPT Prompt2はAggregation Executionを経由しない別内部経路（G.3）——
    input_aggregation_execution_id=NULL。final_report.htmlはartifact_type='final_report'、
    残り9ファイルは'prompt2_output'として保存する。

    P0の既知の制限: Storage未配線（`geo_report_run_artifacts`は現状content_textのみで
    運用しており、ZIPバイナリを`storage_path`へ置く経路がまだ無い）のため、
    `input_prompt2_base_input_artifact_id`はNULLのまま残す（Prompt2 Base Input自体は
    `build_prompt2_base_input_zip`で常に決定的に再構成できるため、実行結果の正しさには
    影響しない。厳密な「どの版のBase Inputを渡したか」の証跡はStorage配線後に追加する）。"""
    files = _unzip_files(zip_bytes)
    missing = [f for f in PROMPT2_OUTPUT_REQUIRED_FILES if f not in files]
    if missing:
        raise ValueError(f"Prompt2 Outputに必須ファイルがありません: {missing}")

    run = get_run(client, run_id)  # runの存在確認（run_id誤りを早期に検出する）
    files_text = {name: (data.decode("utf-8-sig") if isinstance(data, (bytes, bytearray)) else data)
                  for name, data in files.items()}
    prompt2_contract_version = run.get("prompt2_contract_version") or "v4.3"

    # Import時のschema validation（2026-09-16追加、Phase H）: v4.3と
    # evidence_intelligence_v1のschemaを混在させない。
    schema_problems = prompt2_output.validate_prompt2_output_schema(files_text, prompt2_contract_version)
    if schema_problems:
        raise ValueError(f"Prompt2 Outputがcontract validationに失敗しました: {schema_problems}")

    # Section Coverage検証（2026-09-16追加、Phase H）: evidence_intelligence_v1の
    # Runのみ強制する（v4.3のfinal_report.htmlはdata-section-id/data-fieldマーカー
    # 契約を前提に作られていないため、v4.3では呼ばない）。比較基準は常にRun-fixed
    # Template（report_template_asset_id、report_template.jsonと同じ構造化データ）で
    # あり、report_template.htmlを再パースしない。
    section_coverage_warnings = None
    if prompt2_contract_version == "evidence_intelligence_v1" and "final_report.html" in files_text:
        template_asset = assets.get_asset_by_id(client, run["report_template_asset_id"])
        template_sections = template_asset["content"].get("sections", [])
        coverage = prompt2_output.validate_section_coverage(files_text["final_report.html"], template_sections)
        if coverage["missing_sections"]:
            raise ValueError(
                f"final_report.htmlにSectionが丸ごと欠落しています: {coverage['missing_sections']}"
                "（data-section-id属性が見つかりません）。")
        section_coverage_warnings = coverage["warnings"]

    execution_id = _new_id()
    client.insert("geo_report_executions", [{
        "id": execution_id, "run_id": run_id, "stage": "prompt2", "execution_method": "chatgpt_import",
        "status": "running", "input_aggregation_execution_id": None,
        "input_prompt2_base_input_artifact_id": None, "started_at": _now_iso(),
    }])

    artifact_rows = []
    for file_name, data in files.items():
        text = data.decode("utf-8-sig") if isinstance(data, (bytes, bytearray)) else data
        artifact_type = "final_report" if file_name == "final_report.html" else "prompt2_output"
        media_type = "text/html" if file_name.endswith(".html") else (
            "application/json" if file_name.endswith(".json") else (
                "text/markdown" if file_name.endswith(".md") else "text/csv"))
        storage_fields = artifact_storage.build_artifact_storage_fields(
            client, run_id, execution_id, artifact_type, file_name, text.encode("utf-8"),
            content_type=media_type)
        artifact_rows.append({
            "id": _new_id(), "run_id": run_id, "execution_id": execution_id, "execution_method": "chatgpt_import",
            "artifact_type": artifact_type, "file_name": file_name, "media_type": media_type,
            **storage_fields,
        })

    if section_coverage_warnings is not None:
        audit_text = json.dumps(section_coverage_warnings, ensure_ascii=False, indent=2)
        storage_fields = artifact_storage.build_artifact_storage_fields(
            client, run_id, execution_id, "prompt2_output", "section_coverage_audit.json",
            audit_text.encode("utf-8"), content_type="application/json")
        artifact_rows.append({
            "id": _new_id(), "run_id": run_id, "execution_id": execution_id, "execution_method": "chatgpt_import",
            "artifact_type": "prompt2_output", "file_name": "section_coverage_audit.json",
            "media_type": "application/json", **storage_fields,
        })

    for i in range(0, len(artifact_rows), 500):
        client.insert("geo_report_run_artifacts", artifact_rows[i:i + 500])

    client.update("geo_report_executions", {"id": f"eq.{execution_id}"}, {
        "status": "completed", "completed_at": _now_iso(),
    })
    return {"execution_id": execution_id, "section_coverage_warnings": section_coverage_warnings}


# ---------------------------------------------------------------------------
# Combined ChatGPT Session（Prompt1＋Prompt2を1セッションで完結、2026-09-16追加）
#
# 背景（ユーザー要望）: Prompt1がChatGPT必須なのは生レコード規模（数十万件）による
# コスト・時間の制約——ここは動かせない。一方Prompt2はクラスタ単位（数千件）の
# 小規模データであり、Application AI化自体は技術的に可能だが、evidence_intelligence
# 用のPython分類・集計・Section組み立てコードをまだ書いていない（実装量の問題）。
# その本格対応（Application AI化）に先立ち、まず今すぐレポートを出せるようにする
# ためのブリッジとして、Preprocess完了直後の時点で「Prompt1 Input＋Prompt2 Base
# Input＋両方のInstruction＋Template」を1つのZIPにまとめ、ChatGPTの1セッションで
# Prompt1→Prompt2まで通しで実行してもらう。これにより往復は「Export 1回→ChatGPT
# 1セッション→Import 1回（Prompt1/Prompt2結果を続けて取り込み、両方自動でCanonical
# 採用）」まで圧縮される（従来はPrompt1 Export/Import/採用→Prompt2 Export/Import/
# 採用の計6ステップだった）。
# ---------------------------------------------------------------------------

COMBINED_SESSION_README_MD = """# Combined Session: Prompt 1 → Prompt 2（同一セッション内で通しで実行）

このZIPには、Prompt 1とPrompt 2の両方に必要な入力とInstructionが同梱されています。
**1つのChatGPTセッション内で、以下の順に両方の指示文を実行してください。**

## 手順

1. `prompt1_instruction.md`の指示に従い、`prompt1_input_<run_id>.zip`を分析し、
   `prompt1_output_<timestamp>.zip`を生成する（Prompt 1の最終成果物）。
2. 同じセッション内で、`prompt2_instruction.md`の指示に従い、手順1で生成した
   Prompt 1の結果（`classified_clusters.csv`等）と、同梱の`prompt2_base_input_
   <run_id>.zip`・`report_template.json`（Section構造の確定仕様）・
   `report_template.html`（見た目参考）・`previous_continuity_context.json`
   （存在する場合）を使い、`prompt2_output_<timestamp>.zip`を生成する
   （Prompt 2の最終成果物）。

## 重要

- Prompt 1の成果物（`prompt1_output_*.zip`）とPrompt 2の成果物
  （`prompt2_output_*.zip`）を、**それぞれ別のZIPファイルとして**両方
  ダウンロードできる状態にしてください（両方をこのアプリへ再アップロードします）。
- Prompt 2はPrompt 1自身が本セッションで確定させた分類結果のみを根拠とし、
  それ以外の推測を混ぜないでください。
- 各Instructionが個別に定めるQAゲート・最終品質チェックは、このCombined Session
  でも省略せず両方満たしてください。
"""


def export_combined_prompt1_prompt2_package(client, run_id: str) -> bytes:
    """Preprocess完了直後（Canonical Prompt1は不要）に呼べる、Prompt1＋Prompt2を
    1セッションで通しで実行するためのCombined Export。"""
    run = get_run(client, run_id)
    if not run.get("canonical_preprocess_execution_id"):
        raise ValueError(f"run_id={run_id} はPreprocessが未完了です。Combined Exportは"
                          "Preprocess完了後にのみ実行できます。")

    prompt1_input_zip = build_prompt1_input_zip(client, run_id)
    prompt2_base_input_zip = build_prompt2_base_input_zip(client, run_id)
    template_asset = assets.get_asset_by_id(client, run["report_template_asset_id"])
    template_html = _render_template_preview_html(template_asset["content"])
    template_json = json.dumps(
        sorted(template_asset["content"].get("sections", []), key=lambda s: s.get("order", 0)),
        ensure_ascii=False, indent=2)
    prompt1_instruction_text = _artifact_content(client, run_id, "prompt_snapshot", "prompt1_instruction.md")
    prompt2_instruction_text = _artifact_content(client, run_id, "prompt_snapshot", "prompt2_instruction.md")

    files = {
        f"prompt1_input_{run_id}.zip": prompt1_input_zip,
        f"prompt2_base_input_{run_id}.zip": prompt2_base_input_zip,
        "report_template.html": template_html,
        "report_template.json": template_json,
        "prompt1_instruction.md": prompt1_instruction_text,
        "prompt2_instruction.md": prompt2_instruction_text,
        "README_combined_session.md": COMBINED_SESSION_README_MD,
    }
    if run.get("previous_continuity_artifact_id"):
        continuity_rows = client.select("geo_report_run_artifacts", {
            "select": "content_text,storage_bucket,storage_path", "id": f"eq.{run['previous_continuity_artifact_id']}",
        })
        if continuity_rows:
            files["previous_continuity_context.json"] = artifact_storage.read_artifact_content(
                client, continuity_rows[0])
    return _zip_files(files)


def import_combined_prompt1_prompt2_result(client, run_id: str, prompt1_zip_bytes: bytes,
                                              prompt2_zip_bytes: bytes) -> dict:
    """Combined Sessionの結果（prompt1_output.zip・prompt2_output.zip）を続けて取り込み、
    両方を自動でCanonical採用する（従来別々に必要だった「Use as Current」クリックを
    このフローでは不要にする——Combined Exportは元々「まとめて1回で完結させる」ことが
    目的のため）。Prompt1のImportが失敗した場合はPrompt2のImportを行わない。"""
    prompt1_result = import_prompt1_result(client, run_id, prompt1_zip_bytes)
    canonical.set_canonical_execution(client, run_id, "prompt1", prompt1_result["execution_id"])

    prompt2_result = import_prompt2_result(client, run_id, prompt2_zip_bytes)
    canonical.set_canonical_execution(client, run_id, "prompt2", prompt2_result["execution_id"])

    return {
        "prompt1_execution_id": prompt1_result["execution_id"],
        "prompt2_execution_id": prompt2_result["execution_id"],
        "section_coverage_warnings": prompt2_result["section_coverage_warnings"],
    }
