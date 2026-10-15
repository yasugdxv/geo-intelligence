"""Report Studio「Design」領域: Current Pipeline実行のDB永続化・進捗管理（Phase D）。

`geo_report_current_pipeline_service.run_preprocess()`の結果を、Execution/Cluster/
Representative/Record正規化テーブルへ保存し、Prompt1 Input契約11ファイルを
`geo_report_run_artifacts`へスナップショットする。Preprocess自体を
`geo_report_executions`（stage='preprocess'）として記録し、ファイル単位で
`progress`列を更新する——前処理は時間がかかるため、UIが別リクエストでポーリングして
状況を見られるようにするため（本プロジェクトにバックグラウンドジョブ基盤が無く、
同期実行中でもDBへの定期書き込みで進捗を可視化する設計にする）。
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone

import requests

import geo_report_artifact_storage_service as artifact_storage
import geo_report_current_pipeline_service as cp
import geo_report_master_lint_service as master_lint
import geo_report_prompt2_package_validation_service as package_validation

CHUNK_SIZE = 500

# 2026-09-16追加、ユーザー要望「途中で保存して失敗したらそこから再開できないの」:
# Preprocessの永続化フェーズの実行順（run_and_persist_preprocess内の呼び出し順と
# 一致させる。フロントエンドのgeo-report-pipeline-panel.tsx PREPROCESS_PHASE_ORDER
# と同じ並び）。失敗時に記録されたprogress.phase/progress.files_processedを
# チェックポイントとして使い、再実行時に「既に完了したフェーズは丸ごとスキップ」
# 「失敗したフェーズは途中のchunkから再開」する。
PREPROCESS_PHASE_ORDER = [
    "collecting_files",
    "saving_analysis_clusters",
    "saving_analysis_representatives",
    "saving_sentiment_clusters",
    "saving_sentiment_representatives",
    "saving_record_map",
    "building_artifacts",
]


def _resume_point(phase_name: str, checkpoint_phase, checkpoint_done: int):
    """(skip_entirely, resume_from_chunk) を返す。checkpoint_phaseがNone（新規実行、
    または再開対象では無い実行）の場合は常に(False, 0)——通常の最初から実行する。"""
    if not checkpoint_phase:
        return False, 0
    try:
        checkpoint_index = PREPROCESS_PHASE_ORDER.index(checkpoint_phase)
        phase_index = PREPROCESS_PHASE_ORDER.index(phase_name)
    except ValueError:
        return False, 0
    if phase_index < checkpoint_index:
        return True, 0  # 前回既に完了したフェーズ、再実行不要
    if phase_index == checkpoint_index:
        return False, checkpoint_done  # 失敗直前まで進んだchunkの続きから
    return False, 0  # まだ到達していなかったフェーズ、通常通り最初から


def _compute_file_fingerprint(file_paths: list) -> str:
    """アップロードされたCSV群の同一性を検証するための指紋（ファイル名+内容ハッシュ）。

    2026-09-17追加、ユーザー指摘「途中から再開する時に別のCSVをアップロードしたら
    データが混ざるのでは」への対応: resume_execution_idによる再開時、クラスタリング
    （cp.run_preprocess）は毎回渡されたCSVからやり直す一方、既に完了済みの永続化
    フェーズは丸ごとスキップする設計のため、前回と異なるCSVを渡すと「スキップした
    フェーズは古いCSVのデータ、その先のフェーズは新しいCSVのデータ」という矛盾した
    状態でRunが完成してしまう。最初の実行時にこの指紋をprogressへ記録しておき、
    再開時に不一致なら実行を拒否する（run_and_persist_preprocess参照）。"""
    entries = []
    for path in file_paths:
        with open(path, "rb") as fh:
            content_hash = hashlib.sha256(fh.read()).hexdigest()
        entries.append((os.path.basename(path), content_hash))
    entries.sort()
    return hashlib.sha256(repr(entries).encode("utf-8")).hexdigest()


def _new_id() -> str:
    return str(uuid.uuid4())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _chunked(items: list, size: int = CHUNK_SIZE):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _insert_chunks_with_progress(client, table: str, payload: list, *, execution_id: str, phase: str,
                                   resume_from_chunk: int = 0, file_fingerprint: str = None) -> None:
    """chunk単位でinsertしつつ、chunkごとにgeo_report_executions.progressを更新する
    （2026-09-15追加。永続化フェーズが見えないと「進捗が動いていない」＝ハングと
    区別できないため）。payloadが空でも、そのphaseへ到達したこと自体をprogressへ
    反映する（done=0, total=0）。

    2026-09-16追加、ユーザー要望「途中で保存して失敗したらそこから再開できないの」:
    resume_from_chunkを指定すると、その番号までのchunkは既に前回の実行で挿入済み
    として扱い、そこから続けて挿入する（前回成功した分を再送しない——失敗直前まで
    進んだ大量チャンクを全部やり直す無駄を避ける）。

    file_fingerprint（2026-09-17追加）はupdate_preprocess_progressがprogress列を
    まるごと上書きする実装のため、呼び出し毎に明示的に渡し続けて消えないようにする。"""
    chunks = list(_chunked(payload))
    total = len(chunks)
    if total == 0:
        update_preprocess_progress(client, execution_id, files_processed=0, files_total=0, phase=phase,
                                     file_fingerprint=file_fingerprint)
        return
    resume_from_chunk = max(0, min(resume_from_chunk, total))
    if resume_from_chunk > 0:
        update_preprocess_progress(
            client, execution_id, files_processed=resume_from_chunk, files_total=total, phase=phase,
            file_fingerprint=file_fingerprint)
    for i in range(resume_from_chunk, total):
        try:
            client.insert(table, chunks[i])
        except requests.HTTPError as exc:
            # 2026-10-05: 通信断などで、insertは成功したのに応答が届かず自動再送され、同じidの再送が
            # 409(一意制約違反)になることがある。idはchunkごとにランダム採番なので、409は「このchunkは
            # 既に入っている」ことを意味する。そのまま次へ進む（失敗扱いにして再開すると、再開側が
            # 新しいidで同じ行を入れ直して二重登録になるため）。取りこぼしは最終の件数照合で検出する。
            if getattr(exc.response, "status_code", None) != 409:
                raise
        update_preprocess_progress(client, execution_id, files_processed=i + 1, files_total=total, phase=phase,
                                     file_fingerprint=file_fingerprint)


def get_run(client, run_id: str) -> dict:
    rows = client.select("geo_report_collection_runs", {"select": "*", "run_id": f"eq.{run_id}"})
    if not rows:
        raise ValueError(f"run_id={run_id} が見つかりません。")
    return rows[0]


# ---------------------------------------------------------------------------
# Execution進捗管理
# ---------------------------------------------------------------------------

def start_preprocess_execution(client, run_id: str, *, input_collection_artifact_id: str = None) -> str:
    execution_id = _new_id()
    client.insert("geo_report_executions", [{
        "id": execution_id, "run_id": run_id, "stage": "preprocess", "execution_method": "python",
        "status": "running", "input_collection_artifact_id": input_collection_artifact_id,
        "started_at": _now_iso(), "progress": {"files_processed": 0, "files_total": 0, "current_file": None},
    }])
    return execution_id


def update_preprocess_progress(client, execution_id: str, *, files_processed: int, files_total: int,
                                 current_file: str = None, phase: str = "collecting_files",
                                 file_fingerprint: str = None) -> None:
    """phaseは"collecting_files"（ファイル読み込み・クラスタリング中、既定値）に加え、
    2026-09-15追加で永続化フェーズ（"saving_analysis_clusters"/
    "saving_analysis_representatives"/"saving_sentiment_clusters"/
    "saving_sentiment_representatives"/"saving_record_map"/"building_artifacts"）も
    取り得る。files_processed/files_totalはphase内での単位（ファイル数、またはDB
    insert chunk数）——キー名は既存フロントとの後方互換のため変えない。

    根拠: files_processed=files_totalに達した後の永続化フェーズ（Cluster/Record/
    Artifact保存、chunk単位insert多数）に進捗更新が一切無く、社内プロキシの瞬断で
    ここが失敗してもexecutionがstatus='running'のまま更新されず、ユーザーには
    「6/6のままハングしている」としか見えなかった（2026-09-15実機報告）。

    file_fingerprint（2026-09-17追加）が指定された場合はprogressへ含める。この関数は
    progress列をまるごと上書きするため、resume検証用の指紋を実行の最初から最後まで
    残すには、呼び出し元が毎回同じ値を渡し続ける必要がある（run_and_persist_preprocess
    参照）。"""
    progress = {"phase": phase, "files_processed": files_processed, "files_total": files_total,
                "current_file": current_file}
    if file_fingerprint is not None:
        progress["file_fingerprint"] = file_fingerprint
    client.update("geo_report_executions", {"id": f"eq.{execution_id}"}, {"progress": progress})


def complete_execution(client, execution_id: str, *, status: str = "completed") -> None:
    client.update("geo_report_executions", {"id": f"eq.{execution_id}"}, {
        "status": status, "completed_at": _now_iso(),
    })


def get_execution(client, execution_id: str) -> dict:
    rows = client.select("geo_report_executions", {"select": "*", "id": f"eq.{execution_id}"})
    if not rows:
        raise ValueError(f"execution_id={execution_id} が見つかりません。")
    return rows[0]


# ---------------------------------------------------------------------------
# Cluster/Representative/Record永続化
# ---------------------------------------------------------------------------

def _save_analysis_clusters(client, run_id: str, execution_id: str, analysis_clusters: dict, *,
                              resume_from_chunk: int = 0, file_fingerprint: str = None) -> None:
    rows = cp.build_cluster_labeling_rows(analysis_clusters)
    payload = []
    for row in rows:
        payload.append({
            "id": _new_id(), "run_id": run_id, "analysis_cluster_id": row["cluster_id"],
            "signal_id": row["signal_id"], "signal_name": row["signal_name"],
            "narrative_id": row["narrative_id"], "narrative_name": row["narrative_name"],
            "report_topic_label": row["report_topic_label"], "theme_id": row["theme_id"],
            "theme_name": row["theme_name"], "trend_role": row["trend_role"],
            "trend_start_date": row["trend_start_date"], "main_narrative": row["main_narrative"],
            "week_start": row["week_start"], "week_end": row["week_end"],
            "cluster_records": row["cluster_records"], "eligible_records": row["eligible_records"],
            "duplicate_records": row["duplicate_records"],
            "source_class_preliminary": row["source_class_preliminary"], "source_bucket": row["source_bucket"],
            "reaction_type_hint": row["reaction_type_hint"], "attitude_hint": row["attitude_hint"],
            "cluster_cue": row["cluster_cue"], "match_signature": row["match_signature"],
            "exception_candidate_count": row["exception_candidate_count"],
            "source_distribution": row["source_distribution"], "reaction_distribution": row["reaction_distribution"],
            "attitude_distribution": row["attitude_distribution"], "language_distribution": row["language_distribution"],
            "fallback_share": row["fallback_share"], "preliminary_consistency": row["preliminary_consistency"],
            "requires_review": row["requires_review"],
        })
    _insert_chunks_with_progress(client, "geo_report_analysis_clusters", payload,
                                   execution_id=execution_id, phase="saving_analysis_clusters",
                                   resume_from_chunk=resume_from_chunk, file_fingerprint=file_fingerprint)


def _save_sentiment_clusters(client, run_id: str, execution_id: str, sentiment_clusters: dict, *,
                               resume_from_chunk: int = 0, file_fingerprint: str = None) -> None:
    rows = cp.build_cluster_labeling_rows(sentiment_clusters)
    payload = []
    for row in rows:
        payload.append({
            "id": _new_id(), "run_id": run_id, "sentiment_cluster_id": row["cluster_id"],
            "signal_id": row["signal_id"], "signal_name": row["signal_name"],
            "narrative_id": row["narrative_id"], "narrative_name": row["narrative_name"],
            "report_topic_label": row["report_topic_label"], "theme_id": row["theme_id"],
            "theme_name": row["theme_name"], "trend_role": row["trend_role"],
            "trend_start_date": row["trend_start_date"], "main_narrative": row["main_narrative"],
            "week_start": row["week_start"], "week_end": row["week_end"],
            "cluster_records": row["cluster_records"], "eligible_records": row["eligible_records"],
            "duplicate_records": row["duplicate_records"],
            "source_class_preliminary": row["source_class_preliminary"], "source_bucket": row["source_bucket"],
            "reaction_type_hint": row["reaction_type_hint"], "attitude_hint": row["attitude_hint"],
            "cluster_cue": row["cluster_cue"], "match_signature": row["match_signature"],
            "exception_candidate_count": row["exception_candidate_count"],
            "source_distribution": row["source_distribution"], "reaction_distribution": row["reaction_distribution"],
            "attitude_distribution": row["attitude_distribution"], "language_distribution": row["language_distribution"],
            "fallback_share": row["fallback_share"], "preliminary_consistency": row["preliminary_consistency"],
            "requires_review": row["requires_review"],
        })
    _insert_chunks_with_progress(client, "geo_report_sentiment_clusters", payload,
                                   execution_id=execution_id, phase="saving_sentiment_clusters",
                                   resume_from_chunk=resume_from_chunk, file_fingerprint=file_fingerprint)


def _save_analysis_cluster_representatives(client, run_id: str, execution_id: str, analysis_clusters: dict, *,
                                              resume_from_chunk: int = 0, file_fingerprint: str = None) -> None:
    rows = cp.build_cluster_representative_rows(analysis_clusters)
    payload = [{
        "id": _new_id(), "run_id": run_id, "analysis_cluster_id": row["cluster_id"],
        "representative_no": row["representative_no"], "record_id": row["record_id"], "title": row["title"],
        "text_for_review": row["text_for_review"], "source_name": row["source_name"], "domain": row["domain"],
        "url": row["url"], "published_at": row["published_at"], "language_group": row["language_group"],
        "author": row["author"], "document_type": row["documentTypeName"], "source_bucket": row["source_bucket"],
        "source_class_preliminary": row["source_class_preliminary"], "reaction_type_hint": row["reaction_type_hint"],
        "attitude_hint": row["attitude_hint"], "matched_terms": row["matched_terms"],
        "match_status": row["match_status"], "source_file": row["source_file"],
    } for row in rows]
    _insert_chunks_with_progress(client, "geo_report_analysis_cluster_representatives", payload,
                                   execution_id=execution_id, phase="saving_analysis_representatives",
                                   resume_from_chunk=resume_from_chunk, file_fingerprint=file_fingerprint)


def _save_sentiment_cluster_representatives(client, run_id: str, execution_id: str, sentiment_clusters: dict, *,
                                               resume_from_chunk: int = 0, file_fingerprint: str = None) -> None:
    rows = cp.build_cluster_representative_rows(sentiment_clusters)
    payload = [{
        "id": _new_id(), "run_id": run_id, "sentiment_cluster_id": row["cluster_id"],
        "representative_no": row["representative_no"], "record_id": row["record_id"], "title": row["title"],
        "text_for_review": row["text_for_review"], "source_name": row["source_name"], "domain": row["domain"],
        "url": row["url"], "published_at": row["published_at"], "language_group": row["language_group"],
        "author": row["author"], "document_type": row["documentTypeName"], "source_bucket": row["source_bucket"],
        "source_class_preliminary": row["source_class_preliminary"], "reaction_type_hint": row["reaction_type_hint"],
        "attitude_hint": row["attitude_hint"], "matched_terms": row["matched_terms"],
        "match_status": row["match_status"], "source_file": row["source_file"],
    } for row in rows]
    _insert_chunks_with_progress(client, "geo_report_sentiment_cluster_representatives", payload,
                                   execution_id=execution_id, phase="saving_sentiment_representatives",
                                   resume_from_chunk=resume_from_chunk, file_fingerprint=file_fingerprint)


def _save_record_map(client, run_id: str, execution_id: str, record_map_rows: list, *,
                       resume_from_chunk: int = 0, file_fingerprint: str = None) -> None:
    payload = [{
        "id": _new_id(), "run_id": run_id, "record_id": row["record_id"], "source_file": row["source_file"],
        "timestamp_raw": row["timestamp"], "date_key": row["date_key"], "week_start": row["week_start"],
        "week_end": row["week_end"], "signal_id": row["signal_source"], "theme_id": row["theme_id"],
        "theme_name": row["theme_name"], "trend_role": row["trend_role"],
        "trend_start_date": row["trend_start_date"], "narrative_id": row["narrative_id"],
        "narrative_name": row["narrative_name"], "match_status": row["match_status"],
        "matched_terms": row["matched_terms"], "source_bucket": row["source_bucket"],
        "source_class_preliminary": row["source_class_preliminary"],
        "reaction_type_hint": row["reaction_type_hint"], "attitude_hint": row["attitude_hint"],
        "cluster_cue": row["cluster_cue"], "match_signature": row["match_signature"],
        "analysis_cluster_id": row["analysis_cluster_id"], "sentiment_cluster_id": row["sentiment_cluster_id"],
        "is_duplicate": row["is_duplicate"], "duplicate_group_id": row["duplicate_group_id"],
        "url_hash": row["url_hash"], "text_hash": row["text_hash"],
    } for row in record_map_rows]
    _insert_chunks_with_progress(client, "geo_report_records", payload,
                                   execution_id=execution_id, phase="saving_record_map",
                                   resume_from_chunk=resume_from_chunk, file_fingerprint=file_fingerprint)


# ---------------------------------------------------------------------------
# Prompt1 Input契約（C章11ファイル）のArtifact化
# ---------------------------------------------------------------------------

def _artifact_row(client, run_id: str, execution_id: str, file_name: str, content_text: str,
                    media_type: str) -> dict:
    # Prompt1入力CSV(cluster_representatives.csv等)はレコード数に比例して肥大化するため、
    # 一定サイズを超える場合は自動的にSupabase Storageへ保存する（Phase 3正式対応、
    # geo_report_artifact_storage_service参照）。小さいものはgzip圧縮してcontent_textへ
    # 保存する（既存の応急処置を存続、読み込み側はdecompress_text/read_artifact_contentで
    # 透過的に復号する）。
    storage_fields = artifact_storage.build_artifact_storage_fields(
        client, run_id, execution_id, "prompt1_input", file_name, content_text.encode("utf-8"),
        content_type=media_type)
    return {
        "id": _new_id(), "run_id": run_id, "execution_id": execution_id, "execution_method": "python",
        "artifact_type": "prompt1_input", "file_name": file_name, "media_type": media_type,
        **storage_fields,
    }


def build_prompt1_input_artifacts(client, run_id: str, execution_id: str, result: cp.PreprocessResult, *,
                                    topic: str, report_id: str, primary_graph_signals: list,
                                    final_graph_rule: str, discovery_signal_ids: set = None,
                                    analysis_mode: str = None, prompt1_contract_version: str = None,
                                    run_period_start: str = None, run_period_end: str = None,
                                    run_config: dict = None) -> list:
    """C章11ファイルぶんのArtifact行を組み立てる（DB保存はしない、呼び出し元がinsertする。
    大容量ファイルはこの時点でSupabase Storageへアップロード済みになる——clientはそのため
    だけに使う）。

    discovery_signal_ids（2026-09-16追加、#4）が指定され、かつresult.discovery_
    candidatesが非空の場合、discovery_candidates.csv/discovery_candidate_
    representatives.csvを追加で含める（discovery-mode Signalが無い、または
    コーパスが0件のRunでは生成しない——ファイルを無意味に増やさない）。"""
    created_at = _now_iso()
    analysis_labeling_csv = cp.rows_to_csv_text(
        cp.build_cluster_labeling_rows(result.analysis_clusters, result.flagged_terms), cp.CLUSTER_LABELING_COLUMNS)
    analysis_rep_csv = cp.rows_to_csv_text(
        cp.build_cluster_representative_rows(result.analysis_clusters), cp.CLUSTER_REPRESENTATIVE_COLUMNS)
    sentiment_labeling_csv = cp.rows_to_csv_text(
        cp.build_cluster_labeling_rows(result.sentiment_clusters, result.flagged_terms), cp.CLUSTER_LABELING_COLUMNS)
    sentiment_rep_csv = cp.rows_to_csv_text(
        cp.build_cluster_representative_rows(result.sentiment_clusters), cp.CLUSTER_REPRESENTATIVE_COLUMNS)
    exception_csv = cp.rows_to_csv_text(
        cp.build_exception_review_rows(result.sentiment_clusters), cp.EXCEPTION_REVIEW_COLUMNS)
    manifest = cp.build_cluster_manifest(
        result, topic, created_at=created_at,
        record_map_chunks=max(1, -(-len(result.record_map_rows) // cp.RECORD_MAP_CHUNK_SIZE)))
    audit_csv = _dicts_to_csv_text(cp.build_preprocess_audit_rows(result, discovery_signal_ids))
    scope_csv = _dicts_to_csv_text(cp.build_monitoring_scope_rows(
        result, run_period_start=run_period_start, run_period_end=run_period_end))
    config_yaml = cp.build_report_config_yaml(
        result, report_id=report_id, topic=topic, primary_graph_signals=primary_graph_signals,
        final_graph_rule=final_graph_rule, created_at=created_at, analysis_mode=analysis_mode,
        analysis_contract=(run_config or {}).get("analysis_contract")
        or cp.resolve_analysis_contract(analysis_mode, prompt1_contract_version),
        run_config=run_config)
    spec_md = _SENTIMENT_CLASSIFICATION_SPEC_MD

    artifacts = [
        ("cluster_labeling_input.csv", analysis_labeling_csv, "text/csv"),
        ("cluster_representatives.csv", analysis_rep_csv, "text/csv"),
        ("cluster_manifest.csv", _dicts_to_csv_text([manifest]), "text/csv"),
        ("preprocess_audit_summary.csv", audit_csv, "text/csv"),
        ("sentiment_cluster_labeling_input.csv", sentiment_labeling_csv, "text/csv"),
        ("sentiment_cluster_representatives.csv", sentiment_rep_csv, "text/csv"),
        ("sentiment_exception_review_candidates.csv", exception_csv, "text/csv"),
        ("sentiment_cluster_classification_spec.md", spec_md, "text/markdown"),
        ("monitoring_scope.csv", scope_csv, "text/csv"),
        ("report_config.yaml", config_yaml, "application/yaml"),
        # Narrative master lint（2026-10-04）。Prompt 1/2のZIPには入れない（QA用のartifact）。
        ("narrative_master_lint.csv", cp.rows_to_csv_text(
            cp.build_master_lint_rows(result), master_lint.LINT_CSV_COLUMNS), "text/csv"),
        # 今回のexecutionが作ったrecord集合（Run isolation、2026-10-04）。export時にDBの行と厳密比較し、
        # 旧実行の行の混入(stale)・欠落(missing)があればZIP生成を失敗させる。executionのartifactなので
        # 同じRunで再実行しても混ざらない。
        ("record_id_manifest.csv", cp.rows_to_csv_text(
            result.record_map_rows, package_validation.RECORD_MANIFEST_COLUMNS), "text/csv"),
    ]
    if result.period_start or result.period_end:
        # 分析期間で絞り込んだRunの記録（Signal別の期間内/期間外の件数）。期間指定なしのRunには作らない。
        period_rows = [
            {"signal_source": signal, "period_start": result.period_start, "period_end": result.period_end,
             "records_in_period": result.signal_total.get(signal, 0),
             "records_out_of_period": result.out_of_period_by_signal.get(signal, 0)}
            for signal in sorted(set(result.signal_total) | set(result.out_of_period_by_signal))]
        artifacts.append(("period_filter.csv", _dicts_to_csv_text(period_rows), "text/csv"))
    if result.discovery_candidates:
        discovery_csv = cp.rows_to_csv_text(result.discovery_candidates, cp.DISCOVERY_CANDIDATE_COLUMNS)
        discovery_rep_csv = cp.rows_to_csv_text(
            result.discovery_candidate_representatives, cp.DISCOVERY_CANDIDATE_REPRESENTATIVE_COLUMNS)
        artifacts.append(("discovery_candidates.csv", discovery_csv, "text/csv"))
        artifacts.append(("discovery_candidate_representatives.csv", discovery_rep_csv, "text/csv"))
    return [_artifact_row(client, run_id, execution_id, name, text, media_type)
             for name, text, media_type in artifacts]


def _dicts_to_csv_text(rows: list) -> str:
    if not rows:
        return ""
    columns = list(rows[0].keys())
    return cp.rows_to_csv_text(rows, columns)


_SENTIMENT_CLASSIFICATION_SPEC_MD = (
    "# Prompt 1 supplemental sentiment-cluster output\n\n"
    "This specification supplements the project Prompt 1 instructions. "
    "It does not replace the standard Prompt 1 outputs.\n\n"
    "## Notes on preprocessing hints (mechanical, not semantic)\n\n"
    "- `preliminary_consistency` is a mechanical review-priority hint, NOT a measure of semantic "
    "consistency. `Low` is assigned when any of these holds: fallback_share > 0.25, cluster_cue = general, "
    "more than 100 eligible records, or at least one exception candidate. It is almost always `Low` "
    "whenever the cluster contains a single fallback / mixed / unknown-attitude / multi-reaction record. "
    "Do not exclude or down-weight a cluster because of it.\n"
    "- `sentiment_exception_review_candidates.csv`: `review_reason` lists the specific triggers "
    "(`fallback_match`, `mixed_attitude`, `unknown_attitude`, `missing_text`, `multiple_reaction`; "
    "pipe-separated). `unknown_attitude` only means no polarity/reaction words were found.\n"
    "- `review_text_status` (representatives) distinguishes why `text_for_review` is empty: "
    "`retweet_only` (the raw record has text, but it is a pure retweet), `attachment_or_shared_only`, "
    "`missing_body`, `metadata_only`, `title_missing`. `Fact_Only_Candidate` is assigned to any empty "
    "text and does not mean the record lacks content.\n"
    "- Cluster-level `source_class_preliminary` / `reaction_type_hint` / `attitude_hint` of analysis "
    "clusters are the majority value of the cluster; the per-record truth is in `source_distribution` / "
    "`reaction_distribution` / `attitude_distribution`.\n"
)


# ---------------------------------------------------------------------------
# メインオーケストレーション
# ---------------------------------------------------------------------------

# analysis_mode別のreport_title/final_graph_rule既定値（2026-09-15追加）。
# 呼び出し元が明示的にtopic/final_graph_ruleを渡さない場合、Runへsnapshotされた
# analysis_modeから導出する——以前は常に"public_opinion_report"＋Public Voice
# 3-wayルールが固定で使われており、Evidence IntelligenceのRunでもPrompt1
# Packageにこの世論モニタリング向けの値が残るcontract mismatchがあった。
_DEFAULT_TOPIC_BY_ANALYSIS_MODE = {
    "public_voice_monitor": "public_opinion_report",
    "evidence_intelligence": "evidence_intelligence_report",
}
_DEFAULT_FINAL_GRAPH_RULE_TEXT_BY_ANALYSIS_MODE = {
    "public_voice_monitor": "source_class=Public Voice; "
                             "attitude=positive|neutral|negative; duplicate=false; theme_relevance=relevant",
    "evidence_intelligence": "not_applicable "
                              "(evidence_intelligence mode treats all actor categories as first-class "
                              "evidence and does not use Public Voice sentiment as the primary denominator)",
}


def run_and_persist_preprocess(client, run_id: str, file_paths: list, *,
                                 topic: str = None,
                                 primary_graph_signals: list = None,
                                 final_graph_rule: str = None,
                                 discovery_signal_ids: set = None,
                                 input_collection_artifact_id: str = None,
                                 resume_execution_id: str = None) -> dict:
    """Preprocessを実行し、Cluster/Representative/Record/Artifactを永続化し、
    Run.canonical_preprocess_execution_idを更新する。進捗はgeo_report_executions.progressへ
    ファイル単位で書き込まれる（UIが/executions/{id}を別リクエストでポーリングできる）。

    topic/primary_graph_signals/final_graph_rule/discovery_signal_idsを省略（None）
    した場合、Runへsnapshotされたgeo_report_run_config.config_json
    （analysis_mode/primary_graph_signals/discovery_signal_ids）から自動導出する
    （2026-09-15/16追加）。呼び出し元が明示的に値を渡した場合はそれを優先する。

    resume_execution_id（2026-09-16追加、ユーザー要望「途中で保存して失敗したら
    そこから再開できないの」）: 同一runの、以前status='failed'になった
    Preprocess Executionのidを渡すと、新しいExecutionを作らずそれを再利用し、
    そのExecutionが記録していたprogress（phase/files_processed）をチェックポイントとして、
    既に完了済みのフェーズは丸ごとスキップ、失敗直前のフェーズは途中のchunkから
    再開する（クラスタリング自体は決定的で軽いため毎回やり直すが、遅くて失敗しがちな
    ネットワーク経由の一括insertは、既に成功した分を再送しない）。

    2026-09-17追加、ユーザー指摘「再開時に別のCSVを渡したらデータが混ざるのでは」:
    file_pathsの指紋（_compute_file_fingerprint）を最初の実行時にprogressへ記録し、
    再開時に前回と異なるfile_pathsが渡された場合はValueErrorで拒否する
    （呼び出し元＝geo_report_current_pipeline_api.pyは、通常は再開時に前回アップロード
    済みのファイルをキャッシュから自動再利用するため、この検証に実際に引っかかるのは
    キャッシュが失われて手動で再アップロードしたファイルが前回と違う場合のみの想定）。"""
    run = get_run(client, run_id)
    narrative_master_asset_id = run.get("narrative_master_asset_id")
    if not narrative_master_asset_id:
        raise ValueError(
            f"run_id={run_id} にnarrative_master_asset_idが固定されていません。"
            "先にRunのPin（design-pinned等）を完了させてください。")
    asset_rows = client.select("geo_report_project_assets", {
        "select": "content", "id": f"eq.{narrative_master_asset_id}",
    })
    if not asset_rows:
        raise ValueError(f"narrative_master_asset_id={narrative_master_asset_id} が見つかりません。")
    master_rows = asset_rows[0]["content"].get("rows", [])

    # analysis_mode（Runへsnapshot済み）はreport_config.yamlの`analysis_mode`/`analysis_contract`へ
    # 明記するため、呼び出し元が他の値を全て明示した場合でも取得する（2026-10-03、#10）。
    run_config_rows = client.select("geo_report_run_config", {"select": "config_json", "run_id": f"eq.{run_id}"})
    run_config_json = (run_config_rows[0].get("config_json") if run_config_rows else None) or {}
    analysis_mode = run_config_json.get("analysis_mode") or None
    if topic is None or primary_graph_signals is None or final_graph_rule is None or discovery_signal_ids is None:
        analysis_mode = analysis_mode or "public_voice_monitor"
        if topic is None:
            topic = run_config_json.get("report_title") or _DEFAULT_TOPIC_BY_ANALYSIS_MODE.get(
                analysis_mode, "public_opinion_report")
        if primary_graph_signals is None:
            primary_graph_signals = run_config_json.get("primary_graph_signals") or []
        if final_graph_rule is None:
            final_graph_rule = _DEFAULT_FINAL_GRAPH_RULE_TEXT_BY_ANALYSIS_MODE.get(
                analysis_mode, _DEFAULT_FINAL_GRAPH_RULE_TEXT_BY_ANALYSIS_MODE["public_voice_monitor"])
        if discovery_signal_ids is None:
            discovery_signal_ids = set(run_config_json.get("discovery_signal_ids") or [])

    checkpoint_phase = None
    checkpoint_done = 0
    existing_fingerprint = None
    if resume_execution_id:
        existing = get_execution(client, resume_execution_id)
        if existing["run_id"] != run_id:
            raise ValueError(f"resume_execution_id={resume_execution_id} はrun_id={run_id}のExecutionではありません。")
        if existing["stage"] != "preprocess":
            raise ValueError(f"resume_execution_id={resume_execution_id} はpreprocess stageのExecutionではありません。")
        execution_id = resume_execution_id
        existing_progress = existing.get("progress") or {}
        checkpoint_phase = existing_progress.get("phase")
        checkpoint_done = existing_progress.get("files_processed") or 0
        existing_fingerprint = existing_progress.get("file_fingerprint")
    else:
        execution_id = start_preprocess_execution(
            client, run_id, input_collection_artifact_id=input_collection_artifact_id)

    # file_fingerprintはtryブロックの中で計算する（このクロージャの呼び出しはtry内の
    # cp.run_preprocess経由でしか起きないため、代入前に参照される心配は無い）。
    # 2026-09-17追加時、file_paths自体が存在しない/読み込めないケースを想定していない
    # テストがtry外での計算により「executionが1件も作られない」形で壊れたため、
    # 既存の「関数全体をtry/exceptで囲み、どの段階の失敗でもfailedへ確実に遷移させる」
    # 方針に合わせてtry内に置く。
    file_fingerprint = None

    def _progress_cb(file_name, done, total):
        update_preprocess_progress(client, execution_id, files_processed=done, files_total=total,
                                     current_file=file_name, phase="collecting_files",
                                     file_fingerprint=file_fingerprint)

    # 2026-09-15修正: 従来はcp.run_preprocess呼び出しだけをtry/exceptで囲んでおり、
    # それ以降の永続化フェーズ（Cluster/Representative/Record/Artifact保存、chunk単位
    # insertが多数）で例外が起きてもexecutionがstatus='running'のまま放置されていた。
    # 社内プロキシの瞬断でこの永続化フェーズのinsertが失敗すると、ユーザーには
    # 「ファイル処理は完了しているのにハングしている」としか見えず、原因の切り分けが
    # できなかった（実機報告、2026-09-15）。関数全体を囲み、どの段階で失敗しても
    # 確実にfailedへ遷移させる。
    try:
        file_fingerprint = _compute_file_fingerprint(file_paths)
        if existing_fingerprint and existing_fingerprint != file_fingerprint:
            raise ValueError(
                "再開に使われたCSVが、最初にこのAnalysisで使ったCSVと一致しません。"
                "既に保存済みのデータと矛盾する可能性があるため、安全のため実行を中止しました。"
                "前回と同じCSVで再実行するか、別データを使いたい場合は「+ New Analysis」から新規に開始してください。")
        if resume_execution_id:
            # 再開時はstatusをrunningへ戻す（画面のRecovery Actionからも「実行中」として
            # 見えるようにする）。過去の失敗理由(error)はこの再開の結果で上書きされるまで
            # 残しておいても実害は無いため、明示的な削除はしない。指紋検証を通過した
            # 場合のみここへ到達する（不一致ならこの手前で例外が飛び、statusは
            # "failed"のまま変化しない）。
            client.update("geo_report_executions", {"id": f"eq.{execution_id}"}, {
                "status": "running", "completed_at": None,
            })
        # クラスタリング自体（cp.run_preprocess）は決定的かつ比較的軽量なため、再開時も
        # 常にやり直す（同じfile_pathsからならchunk順も同じになるため、resume_from_chunk
        # による続きからの再開が安全に成立する）。再開時、progress表示は一瞬
        # "collecting_files"へ戻って見えるが、これは想定通り（すぐ各フェーズの
        # スキップ/再開へ進む）。
        result = cp.run_preprocess(file_paths, master_rows, on_file_processed=_progress_cb,
                                     discovery_signal_ids=discovery_signal_ids,
                                     period_start=run.get("period_start"), period_end=run.get("period_end"))

        skip, resume_from = _resume_point("saving_analysis_clusters", checkpoint_phase, checkpoint_done)
        if not skip:
            _save_analysis_clusters(client, run_id, execution_id, result.analysis_clusters,
                                      resume_from_chunk=resume_from, file_fingerprint=file_fingerprint)
        skip, resume_from = _resume_point("saving_analysis_representatives", checkpoint_phase, checkpoint_done)
        if not skip:
            _save_analysis_cluster_representatives(client, run_id, execution_id, result.analysis_clusters,
                                                      resume_from_chunk=resume_from, file_fingerprint=file_fingerprint)
        skip, resume_from = _resume_point("saving_sentiment_clusters", checkpoint_phase, checkpoint_done)
        if not skip:
            _save_sentiment_clusters(client, run_id, execution_id, result.sentiment_clusters,
                                       resume_from_chunk=resume_from, file_fingerprint=file_fingerprint)
        skip, resume_from = _resume_point("saving_sentiment_representatives", checkpoint_phase, checkpoint_done)
        if not skip:
            _save_sentiment_cluster_representatives(client, run_id, execution_id, result.sentiment_clusters,
                                                       resume_from_chunk=resume_from, file_fingerprint=file_fingerprint)
        skip, resume_from = _resume_point("saving_record_map", checkpoint_phase, checkpoint_done)
        if not skip:
            _save_record_map(client, run_id, execution_id, result.record_map_rows,
                               resume_from_chunk=resume_from, file_fingerprint=file_fingerprint)

        artifact_rows = build_prompt1_input_artifacts(
            client, run_id, execution_id, result, topic=topic, report_id=run_id,
            primary_graph_signals=primary_graph_signals or [], final_graph_rule=final_graph_rule,
            discovery_signal_ids=discovery_signal_ids, analysis_mode=analysis_mode,
            prompt1_contract_version=run.get("prompt1_contract_version"),
            run_period_start=run.get("period_start"), run_period_end=run.get("period_end"),
            run_config=run_config_json)
        skip, resume_from = _resume_point("building_artifacts", checkpoint_phase, checkpoint_done)
        if not skip:
            _insert_chunks_with_progress(client, "geo_report_run_artifacts", artifact_rows,
                                           execution_id=execution_id, phase="building_artifacts",
                                           resume_from_chunk=resume_from, file_fingerprint=file_fingerprint)
    except Exception as exc:
        # 2026-09-16追加、ユーザー報告「前処理が失敗してる」: 従来はfailedへ遷移する
        # だけで失敗理由を一切残していなかった。サーバーの標準出力（uvicornの既定の
        # 例外ログ）には出るが、プロセス再起動を挟むと失われ、後から原因を特定できない
        # （実際にこの調査で再現した）。progressへ理由を残し、DBから常に確認できるようにする。
        try:
            current = get_execution(client, execution_id)
            progress = dict(current.get("progress") or {})
            progress["error"] = str(exc)[:2000]
            client.update("geo_report_executions", {"id": f"eq.{execution_id}"}, {"progress": progress})
        except Exception:
            pass  # 失敗理由の記録自体が失敗しても、元の例外の伝播は妨げない
        complete_execution(client, execution_id, status="failed")
        raise

    complete_execution(client, execution_id, status="completed")
    client.update("geo_report_collection_runs", {"run_id": f"eq.{run_id}"}, {
        "canonical_preprocess_execution_id": execution_id,
    })

    return {"execution_id": execution_id, "result": result, "artifact_count": len(artifact_rows)}
