"""Report Studio: DB永続化・プロジェクト管理層（Phase1）

geo_report_processing_service.py（純粋・DB非依存のShell Parity前処理エンジン）の
結果をSupabaseへ書き込む層。LLMを使うEvidence Classification（Prompt1相当）は
含まない（Phase3で別サービスとして追加、本モジュールのclassification列は書き込み予約のみ）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

import geo_report_incremental_service as incremental
import geo_report_processing_service as processing

CHUNK_SIZE = 500

# Report Studio用に累積してきた全migration（sql/2026-09-02_geo_report_studio_*.sql）が
# 適用済みかを起動時に確認するための最小限のスキーマ要件。新しいmigrationを追加したら
# ここへ1行足すこと（DBスキーマとアプリコードの世代がズレた状態を黙って許容しない
# ための唯一のチェックポイントにする）。
REQUIRED_SCHEMA_CHECKS = [
    ("geo_report_projects", "project_id,project_code,project_title"),
    ("geo_report_narrative_master_rows", "id,project_id,signal_id,narrative_id,row_data"),
    ("geo_report_collection_runs", "run_id,project_id,baseline_run_id,run_mode,run_name,cancel_requested"),
    ("geo_report_clusters", "id,run_id,cluster_id,classification,classified_at"),
    ("geo_report_cluster_representatives", "id,run_id,cluster_id,record_id"),
    ("geo_report_records", "id,run_id,record_id,cluster_id"),
    ("geo_report_syntheses", "id,run_id,signal_id,narrative_id,final_draft,final_html"),
    # sql/2026-09-11_geo_report_studio_design_and_current_pipeline.sql（Design領域 + Current
    # Prompt1/Prompt2パイプライン移植）で追加。
    ("geo_report_project_assets", "id,project_id,asset_type,status,version,content"),
    # pending_*列はsql/2026-09-15_geo_report_design_state_pending_columns.sqlで追加
    # （Design Chatの「提案→FIX」ワークフロー、2026-09-15）。
    ("geo_report_design_state",
     "project_id,report_design,signals,queries,pending_report_design,pending_signals,pending_queries"),
    ("geo_report_design_versions", "id,project_id,version,narrative_master_asset_id,report_template_asset_id"),
    ("geo_report_design_chat_turns", "id,project_id,turn_no,role,content"),
    ("geo_report_executions", "id,run_id,stage,execution_method,status"),
    ("geo_report_sentiment_clusters", "id,run_id,sentiment_cluster_id,signal_id,narrative_id"),
    ("geo_report_sentiment_cluster_representatives", "id,run_id,sentiment_cluster_id,record_id"),
    ("geo_report_analysis_cluster_classifications", "id,execution_id,run_id,cluster_id"),
    ("geo_report_sentiment_cluster_classifications", "id,execution_id,run_id,sentiment_cluster_id"),
    ("geo_report_targeted_stance", "id,execution_id,run_id,sentiment_cluster_id,target_key"),
    ("geo_report_run_config", "run_id,config_json"),
    ("geo_report_run_artifacts", "id,run_id,artifact_type,sha256"),
    ("geo_report_execution_batches", "id,run_id,execution_id,stage,batch_no"),
    ("geo_report_execution_log", "id,execution_id,stage,latency_ms,rate_limit_retries,item_count,cached_tokens"),
    # sql/2026-09-11c_geo_report_analysis_clusters_and_records_columns.sql で追加。
    ("geo_report_analysis_clusters", "id,run_id,analysis_cluster_id,signal_id,narrative_id"),
    ("geo_report_analysis_cluster_representatives", "id,run_id,analysis_cluster_id,record_id"),
]


def check_schema(client) -> tuple:
    """Report Studioが必要とする全テーブル・全カラムが実際にSELECT可能かを検証する
    （PostgRESTには汎用のdescribe-table APIが無いため、limit=1のSELECTを試すことで
    間接的に確認する）。戻り値は(ok, detail)。okがFalseの場合、detailに未適用の
    migrationを特定するための情報を含める。"""
    missing = []
    for table, columns in REQUIRED_SCHEMA_CHECKS:
        try:
            client.select(table, {"select": columns, "limit": "1"})
        except Exception as exc:
            missing.append(f"{table}({columns}): {exc}")
    if missing:
        detail = (
            "Report Studio DBスキーマが最新migrationに追いついていません。"
            "sql/2026-09-02_geo_report_studio_*.sql を未適用の分すべて適用してください。\n"
            + "\n".join(missing)
        )
        return False, detail
    return True, ""


def _new_id() -> str:
    return str(uuid.uuid4())


def _chunked(items: list, size: int = CHUNK_SIZE):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def list_projects(client) -> list:
    return client.select("geo_report_projects", {"select": "*", "order": "updated_at.desc"})


def get_project(client, project_id: str) -> Optional[dict]:
    rows = client.select("geo_report_projects", {"select": "*", "project_id": f"eq.{project_id}"})
    return rows[0] if rows else None


def get_project_by_code(client, project_code: str) -> Optional[dict]:
    rows = client.select("geo_report_projects", {"select": "*", "project_code": f"eq.{project_code}"})
    return rows[0] if rows else None


def get_or_create_project(client, project_code: str, project_title: str, *, report_language: str = "ja") -> dict:
    rows = client.select("geo_report_projects", {"select": "*", "project_code": f"eq.{project_code}"})
    if rows:
        return rows[0]
    project_id = _new_id()
    inserted = client.insert("geo_report_projects", [{
        "project_id": project_id, "project_code": project_code, "project_title": project_title,
        "report_language": report_language, "status": "active",
    }])
    return inserted[0] if inserted else {
        "project_id": project_id, "project_code": project_code, "project_title": project_title,
        "report_language": report_language, "status": "active",
    }


def delete_project(client, project_id: str) -> None:
    """プロジェクトを完全に削除する（2026-09-15、ユーザー要望:「過去のプロジェクトは
    削除できるようにしよう」）。関連する全テーブル（geo_report_collection_runs/
    geo_report_project_assets/geo_report_design_state/geo_report_design_versions/
    geo_report_design_chat_turns/geo_report_executions等）は`on delete cascade`で
    project_idを参照しているため（sql/2026-09-02_/2026-09-11_*.sql）、
    geo_report_projects行を消すだけで関連データも全て連鎖削除される。取り消せない
    破壊的操作なので、呼び出し元（API層）で確認を取ってから呼ぶこと。"""
    rows = client.select("geo_report_projects", {"select": "project_id", "project_id": f"eq.{project_id}"})
    if not rows:
        raise ValueError(f"project_id={project_id} が見つかりません。")
    client.delete("geo_report_projects", {"project_id": f"eq.{project_id}"})


def replace_narrative_master(client, project_id: str, master_rows: list) -> int:
    """既存のNarrative Master行を全削除し、新しい行で置き換える（version管理はせず最新のみ保持）。"""
    client.delete("geo_report_narrative_master_rows", {"project_id": f"eq.{project_id}"})
    payload = []
    for row in master_rows:
        signal_id = processing.normalize_signal_key(row.get("signal_id", ""))
        narrative_id = processing.clean_value(row.get("narrative_id", ""))
        if not signal_id or not narrative_id:
            continue
        payload.append({
            "id": _new_id(), "project_id": project_id, "signal_id": signal_id, "narrative_id": narrative_id,
            "is_fallback": processing.parse_bool(row.get("is_fallback"), False),
            "active": processing.parse_bool(row.get("active"), True),
            "row_data": row,
        })
    for chunk in _chunked(payload):
        client.insert("geo_report_narrative_master_rows", chunk)
    return len(payload)


def load_narrative_master_rows(client, project_id: str) -> list:
    rows = client.select("geo_report_narrative_master_rows", {
        "select": "*", "project_id": f"eq.{project_id}", "order": "signal_id.asc",
    })
    return [r["row_data"] for r in rows]


def load_narrative_configs_by_key(client, project_id: str) -> dict:
    """{(signal_id, narrative_id): row_data} のマップ。Evidence Classification Serviceが
    Narrative固有のdimension_config_json等を引くのに使う"""
    rows = client.select("geo_report_narrative_master_rows", {
        "select": "signal_id,narrative_id,row_data", "project_id": f"eq.{project_id}",
    })
    return {(r["signal_id"], r["narrative_id"]): r["row_data"] for r in rows}


def load_clusters_for_run(client, run_id: str) -> list:
    return client.select("geo_report_clusters", {"select": "*", "run_id": f"eq.{run_id}"})


def get_cluster(client, run_id: str, cluster_id: str) -> Optional[dict]:
    rows = client.select("geo_report_clusters", {
        "select": "*", "run_id": f"eq.{run_id}", "cluster_id": f"eq.{cluster_id}",
    })
    return rows[0] if rows else None


def load_representatives_for_cluster(client, run_id: str, cluster_id: str) -> list:
    return client.select("geo_report_cluster_representatives", {
        "select": "*", "run_id": f"eq.{run_id}", "cluster_id": f"eq.{cluster_id}",
    })


def load_all_representatives_for_run(client, run_id: str) -> list:
    """run全体の代表記事一覧（Assessment/Reportタブがrecord_id→記事のlookupを作るのに使う）"""
    return client.select("geo_report_cluster_representatives", {"select": "*", "run_id": f"eq.{run_id}"})


def persist_cluster_classification(client, cluster_db_id: str, classification: dict, validity_checks: dict, validity_status: str) -> None:
    client.update("geo_report_clusters", {"id": f"eq.{cluster_db_id}"}, {
        "classification": {
            "result": classification, "validity_checks": validity_checks, "validity_status": validity_status,
        },
        "classified_at": datetime.now(timezone.utc).isoformat(),
    })


def apply_incremental_reuse(client, new_run_id: str, baseline_run_id: str) -> dict:
    """baseline_run_idの分類結果のうち、membershipが完全一致したクラスタだけを
    new_run_idの対応クラスタへコピーする（geo_report_incremental_service.
    classify_reuse_planの判定をそのままDBへ適用する）。戻り値はsummarize_reuse_plan
    と同じ形（total_clusters/reused/reprocess）。呼び出し元は"reprocess"のクラスタ
    にのみ通常のclassify（LLM呼び出し）を行うこと。"""
    baseline_records = client.select("geo_report_records", {"select": "*", "run_id": f"eq.{baseline_run_id}"})
    baseline_clusters = load_clusters_for_run(client, baseline_run_id)
    new_clusters = load_clusters_for_run(client, new_run_id)

    # load_clusters_for_runはDB保存済みの列のみ返す（base_key/record_idsは持たない）ため、
    # 比較にはgeo_report_recordsから再構築したbase_key/membershipを使う
    # （geo_report_incremental_service.derive_membership_by_cluster_idと同じロジックを共有）。
    new_records = client.select("geo_report_records", {"select": "*", "run_id": f"eq.{new_run_id}"})
    new_membership_by_cluster_id = incremental.derive_membership_by_cluster_id(new_records)

    new_cluster_rows_for_plan = [
        {
            "cluster_id": cluster["cluster_id"],
            "base_key": new_membership_by_cluster_id.get(cluster["cluster_id"], ("", frozenset()))[0] or "",
            "record_ids": sorted(new_membership_by_cluster_id.get(cluster["cluster_id"], ("", frozenset()))[1]),
        }
        for cluster in new_clusters
    ]

    plan = incremental.classify_reuse_plan(new_cluster_rows_for_plan, baseline_records, baseline_clusters)
    cluster_db_id_by_cluster_id = {c["cluster_id"]: c["id"] for c in new_clusters}

    for cluster_id, entry in plan.items():
        if entry["action"] == "reuse":
            copy_cluster_classification(
                client, cluster_db_id_by_cluster_id[cluster_id], entry["classification"],
                reused_from_cluster_id=entry["reused_from_cluster_id"])

    return incremental.summarize_reuse_plan(plan)


def copy_cluster_classification(client, cluster_db_id: str, classification_blob: dict, *, reused_from_cluster_id: str) -> None:
    """Incremental Processing: baseline runの既存classification（geo_report_clusters.
    classificationそのもの、{"result","validity_checks","validity_status"}）を、
    membershipが完全一致した新runのクラスタへそのままコピーする（LLM再呼び出し無し）。
    reused_from_cluster_idを記録し、後から「再利用されたものか新規分類か」を区別できるようにする。"""
    payload = dict(classification_blob or {})
    payload["reused_from_cluster_id"] = reused_from_cluster_id
    client.update("geo_report_clusters", {"id": f"eq.{cluster_db_id}"}, {
        "classification": payload,
        "classified_at": datetime.now(timezone.utc).isoformat(),
    })


def list_runs_for_project(client, project_id: str, *, limit: int = 20) -> list:
    return client.select("geo_report_collection_runs", {
        "select": "*", "project_id": f"eq.{project_id}", "order": "started_at.desc", "limit": str(limit),
    })


def get_latest_run(client, project_id: str) -> Optional[dict]:
    runs = list_runs_for_project(client, project_id, limit=1)
    return runs[0] if runs else None


def get_run(client, run_id: str) -> Optional[dict]:
    rows = client.select("geo_report_collection_runs", {"select": "*", "run_id": f"eq.{run_id}"})
    return rows[0] if rows else None


def count_review_required_clusters(client, run_id: str) -> int:
    clusters = client.select("geo_report_clusters", {"select": "classification", "run_id": f"eq.{run_id}"})
    return sum(
        1 for c in clusters
        if (c.get("classification") or {}).get("validity_status") == "REVIEW REQUIRED"
    )


def persist_synthesis(
    client, run_id: str, signal_id: str, narrative_id: str,
    draft: dict, draft_checks: dict, draft_status: str,
    review: Optional[dict], final_draft: dict, final_status: str, final_html: Optional[str],
    observed_record_ids, observed_cluster_ids, expert_context_ref_ids,
) -> str:
    synthesis_id = _new_id()
    client.insert("geo_report_syntheses", [{
        "id": synthesis_id, "run_id": run_id, "signal_id": signal_id, "narrative_id": narrative_id,
        "draft": draft, "draft_validity_checks": draft_checks, "draft_validity_status": draft_status,
        "review": review, "final_draft": final_draft, "final_validity_status": final_status,
        "final_html": final_html,
        "observed_record_ids": sorted(observed_record_ids), "observed_cluster_ids": sorted(observed_cluster_ids),
        "expert_context_ref_ids": sorted(x for x in expert_context_ref_ids if x),
    }])
    return synthesis_id


def list_syntheses_for_run(client, run_id: str) -> list:
    return client.select("geo_report_syntheses", {
        "select": "*", "run_id": f"eq.{run_id}", "order": "created_at.desc",
    })


def get_latest_synthesis(client, run_id: str, signal_id: str, narrative_id: str) -> Optional[dict]:
    rows = client.select("geo_report_syntheses", {
        "select": "*", "run_id": f"eq.{run_id}", "signal_id": f"eq.{signal_id}", "narrative_id": f"eq.{narrative_id}",
        "order": "created_at.desc", "limit": "1",
    })
    return rows[0] if rows else None


def get_prior_synthesis_for_narrative(client, project_id: str, signal_id: str, narrative_id: str, *, exclude_run_id: str) -> Optional[dict]:
    """このNarrativeについて、exclude_run_id以外のrunの中で最も新しいSynthesisを返す
    （Continuity比較の「前回」）。runはstarted_at降順で辿り、最初に見つかった
    Synthesisをそのまま返す（過去複数runの結果をマージしない、直前の1回のみ比較する）。"""
    runs = list_runs_for_project(client, project_id, limit=50)
    for run in runs:
        if run["run_id"] == exclude_run_id:
            continue
        synthesis = get_latest_synthesis(client, run["run_id"], signal_id, narrative_id)
        if synthesis:
            return synthesis
    return None


def list_all_syntheses_for_project(client, project_id: str, *, limit_per_run: int = 200) -> list:
    """プロジェクト内の全run・全Narrativeの全Synthesis履歴（Assessment Timeline用）。
    Historyタブでnarrative_id別に時系列表示するため、run横断でフラットに返す。"""
    runs = list_runs_for_project(client, project_id, limit=50)
    all_syntheses = []
    for run in runs:
        all_syntheses += client.select("geo_report_syntheses", {
            "select": "*", "run_id": f"eq.{run['run_id']}", "order": "created_at.desc", "limit": str(limit_per_run),
        })
    return all_syntheses


def list_latest_syntheses_for_project(client, project_id: str) -> list:
    """プロジェクトの最新runにおける、Narrativeごとの最新Synthesis一覧
    （Overview/ReportタブがKey Findings/Central Assessmentを集約表示するのに使う）"""
    latest_run = get_latest_run(client, project_id)
    if not latest_run:
        return []
    syntheses = list_syntheses_for_run(client, latest_run["run_id"])
    latest_by_narrative: dict = {}
    for s in syntheses:
        key = (s["signal_id"], s["narrative_id"])
        if key not in latest_by_narrative:
            latest_by_narrative[key] = s
    return list(latest_by_narrative.values())


def _population_summary(result: processing.PreprocessResult) -> dict:
    return {
        "by_signal": processing.build_population_table(result.population_signal, ["signal_id"]),
        "by_signal_narrative": processing.build_population_table(
            result.population_narrative, ["signal_id", "narrative_id", "narrative_name"]),
        "by_signal_date": processing.build_population_table(
            result.population_date, ["date", "signal_id", "narrative_id"]),
        "by_signal_language": processing.build_population_table(
            result.population_language, ["signal_id", "narrative_id", "language"]),
        "by_signal_country": processing.build_population_table(
            result.population_country, ["signal_id", "narrative_id", "country"]),
        "by_signal_source_bucket": processing.build_population_table(
            result.population_bucket, ["signal_id", "narrative_id", "source_bucket"]),
    }


def persist_collection_run(
    client,
    project_id: str,
    result: processing.PreprocessResult,
    rules_by_signal: dict,
    *,
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
    baseline_run_id: Optional[str] = None,
) -> str:
    """前処理結果一式（run/clusters/representatives/records）をDBへ保存し、run_idを返す。
    baseline_run_idを渡すとrun_mode='incremental'として記録する（Phase4 Continuous
    Intelligence。実際のclassification再利用適用は呼び出し元がgeo_report_incremental_
    serviceを使って別途行う、本関数はlineageの記録のみ担当する）。"""
    run_id = _new_id()
    audit_rows = processing.build_preprocess_audit_rows(result, rules_by_signal)
    input_file_summary = [
        {"source_file": f.source_file, "detected_signal": f.detected_signal, "encoding": f.encoding,
         "status": f.status, "rows": f.rows, "elapsed_seconds": f.elapsed_seconds,
         "records_per_second": f.records_per_second, "error": f.error}
        for f in result.file_audit
    ]

    client.insert("geo_report_collection_runs", [{
        "run_id": run_id, "project_id": project_id, "status": "completed",
        "period_start": period_start, "period_end": period_end,
        "input_file_summary": input_file_summary, "preprocess_audit": audit_rows,
        "population_summary": _population_summary(result),
        "total_records": result.total_records, "in_scope_records": result.in_scope_total,
        "nonduplicate_records": result.nonduplicate_total, "cluster_count": len(result.clusters),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "baseline_run_id": baseline_run_id, "run_mode": "incremental" if baseline_run_id else "full",
    }])

    cluster_rows = processing.build_cluster_rows(result.clusters)
    cluster_payload = [{
        "id": _new_id(), "run_id": run_id, "cluster_id": c["cluster_id"], "signal_id": c["signal_id"],
        "narrative_id": c["narrative_id"], "time_bucket": c["time_bucket"], "source_bucket": c["source_bucket"],
        "split_label": c["split_label"], "match_signature": c["match_signature"],
        "record_count": c["cluster_records"], "matched_share": c["matched_share"],
        "fallback_share": c["fallback_share"],
        "distributions": {
            "source": c["source_distribution"], "language": c["language_distribution"],
            "country": c["country_distribution"], "document_type": c["document_type_distribution"],
            "match": c["match_distribution"],
        },
        "report_priority": c["report_priority"], "report_section": c["report_section"],
        "report_order": c["report_order"], "continuity_role": c["continuity_role"],
        "preliminary_consistency": c["preliminary_consistency"], "requires_review": c["requires_review"],
    } for c in cluster_rows]
    for chunk in _chunked(cluster_payload):
        client.insert("geo_report_clusters", chunk)

    representative_rows = processing.build_representative_rows(result.clusters)
    representative_payload = [{
        "id": _new_id(), "run_id": run_id, "cluster_id": r["cluster_id"],
        "representative_no": r["representative_no"], "record_id": r["record_id"], "title": r["title"],
        "text_for_review": r["text_for_review"], "source_name": r["source_name"], "source_url": r["source_url"],
        "published_at": r["published_at"], "language": r["language"], "source_country": r["source_country"],
        "author_or_account": r["author_or_account"], "document_type": r["document_type"],
        "source_bucket": r["source_bucket"], "matched_terms": r["matched_terms"], "match_status": r["match_status"],
        "split_label": r["split_label"], "source_file": r["source_file"],
    } for r in representative_rows]
    for chunk in _chunked(representative_payload):
        client.insert("geo_report_cluster_representatives", chunk)

    record_payload = [{
        "id": _new_id(), "run_id": run_id, "record_id": r["record_id"], "source_file": r["source_file"],
        "timestamp_raw": r["timestamp"], "date_key": r["date_key"], "signal_id": r["signal_id"],
        "narrative_id": r["narrative_id"], "match_status": r["match_status"], "matched_terms": r["matched_terms"],
        "split_label": r["split_label"], "time_bucket": r["time_bucket"], "cluster_id": r["cluster_id"] or None,
        "is_duplicate": r["is_duplicate"], "duplicate_group_id": r["duplicate_group_id"] or None,
        "period_in_scope": r["period_in_scope"], "source_bucket": r["source_bucket"],
        "source_name": r["source_name"], "author": r["author"], "document_type": r["document_type"],
        "language": r["language"], "source_country": r["source_country"], "url": r["url"],
        "engagement_raw": r["engagement_raw"] or None, "url_hash": r["url_hash"] or None,
        "text_hash": r["text_hash"] or None,
    } for r in result.record_rows]
    for chunk in _chunked(record_payload):
        client.insert("geo_report_records", chunk)

    return run_id
