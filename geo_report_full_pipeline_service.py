"""Report Studio「Design」領域: Preprocess→Prompt1→Aggregation→Prompt2→Finalizeを
1回の呼び出しでバックエンド側が通しで実行する統合オーケストレーション（Phase G）。

ユーザーからの明示的な要望（2026-09-11）: 「プロンプト1,2はプロセスとして分けなくていい。
CSVアップしたら前処理〜プロンプト2までバックエンドで統合処理してよい。ただし時間がかかる
ので、ステータスは可視化したい。処理後にやり直しできるようにしてほしい」。

各StageのExecution自体は従来通り`geo_report_executions`へ個別に記録され続けるため
（Provenance Chainは変えない）、UIは既存の`GET /runs/{run_id}/executions`を継続
ポーリングするだけで各Stageの進捗をそのまま可視化できる。本モジュールは「1回の呼び出しで
全Stageを順番に自動で進める」オーケストレーション層を追加するだけで、個々のStage関数
（Preprocess/Prompt1/Aggregation/Prompt2の各orchestration_service）自体は一切変更しない
——ChatGPT Fallbackや個別バッチのretryなど、Stage単位の操作は引き続きそのまま使える。

「処理後にやり直し」は、既に完了したStageの結果を保ったまま失敗したStageだけを個別に
retryできる既存の仕組み（batch retry・aggregation再実行・composition再実行）をそのまま
使う——本モジュールはRun全体をやり直す一括入口を提供するだけで、部分やり直し用の新しい
概念は導入しない。
"""

from __future__ import annotations

import geo_report_current_pipeline_persistence_service as persistence
import geo_report_project_assets_service as assets
import geo_report_prompt1_orchestration_service as prompt1_orch
import geo_report_prompt2_orchestration_service as prompt2_orch
import geo_report_run_cancellation_service as cancellation
from geo_report_run_cancellation_service import PipelineCancelled  # noqa: F401 (呼び出し元がcatchする)

DEFAULT_MAX_RETRY_ROUNDS = 2


def _get_run(client, run_id: str) -> dict:
    rows = client.select("geo_report_collection_runs", {"select": "*", "run_id": f"eq.{run_id}"})
    if not rows:
        raise ValueError(f"run_id={run_id} が見つかりません。")
    return rows[0]


def _run_prompt1_stage_to_completion(client, azure_client, model: str, run_id: str, execution_id: str,
                                       stage: str, *, max_retry_rounds: int = DEFAULT_MAX_RETRY_ROUNDS) -> None:
    """pendingが無くなるまで全件実行し、それでも残るfailedを最大max_retry_rounds回まで
    自動リトライする（実運用で見つかった、初回のみ一時的に失敗するケースを自動で
    吸収するため）。それでも残ればRun全体を止めて例外を送出する——部分的に欠落した
    Canonicalを黙って確定させない。ユーザーがキャンセルを要求した場合は、リトライを
    続けず即座にPipelineCancelledを送出する（2026-09-11）。"""
    prompt1_orch.run_all_pending_batches(client, azure_client, model, run_id, execution_id, stage)
    cancellation.raise_if_cancelled(client, run_id)
    for _ in range(max_retry_rounds):
        failed = client.select("geo_report_execution_batches", {
            "select": "*", "execution_id": f"eq.{execution_id}", "stage": f"eq.{stage}", "status": "eq.failed",
        })
        if not failed:
            return
        cancellation.raise_if_cancelled(client, run_id)
        for batch in failed:
            prompt1_orch.retry_batch(client, azure_client, model, run_id, execution_id, batch["id"])

    still_failed = client.select("geo_report_execution_batches", {
        "select": "id", "execution_id": f"eq.{execution_id}", "stage": f"eq.{stage}", "status": "eq.failed",
    })
    if still_failed:
        raise ValueError(
            f"stage={stage}で{len(still_failed)}件のバッチが{max_retry_rounds}回の自動リトライ後も"
            "失敗しています。個別にご確認・再試行ください。")


def _run_prompt2_composition_to_completion(client, azure_client, model: str, run_id: str, execution_id: str,
                                             template_asset: dict, *,
                                             max_retry_rounds: int = DEFAULT_MAX_RETRY_ROUNDS) -> None:
    """Prompt1と同じ考え方だが、Compositionバッチには専用のretry_batchが無いため、
    failedバッチをpendingへ戻してから再度run_all_pending_composition_batchesを呼ぶ。
    ユーザーがキャンセルを要求した場合は、リトライを続けず即座にPipelineCancelledを
    送出する（2026-09-11）。"""
    prompt2_orch.run_all_pending_composition_batches(client, azure_client, model, run_id, execution_id,
                                                       template_asset=template_asset)
    cancellation.raise_if_cancelled(client, run_id)
    for _ in range(max_retry_rounds):
        failed = client.select("geo_report_execution_batches", {
            "select": "id", "execution_id": f"eq.{execution_id}", "stage": "eq.prompt2_composition",
            "status": "eq.failed",
        })
        if not failed:
            return
        cancellation.raise_if_cancelled(client, run_id)
        for batch in failed:
            client.update("geo_report_execution_batches", {"id": f"eq.{batch['id']}"}, {"status": "pending"})
        prompt2_orch.run_all_pending_composition_batches(client, azure_client, model, run_id, execution_id,
                                                           template_asset=template_asset)

    still_failed = client.select("geo_report_execution_batches", {
        "select": "id", "execution_id": f"eq.{execution_id}", "stage": "eq.prompt2_composition",
        "status": "eq.failed",
    })
    if still_failed:
        raise ValueError(
            f"Prompt2 Compositionで{len(still_failed)}件のSectionが{max_retry_rounds}回の自動リトライ後も"
            "失敗しています。個別にご確認・再試行ください。")


def run_full_report_pipeline(client, azure_client, model: str, run_id: str, file_paths: list, *,
                               report_theme: str = "") -> dict:
    """Preprocess→Prompt1〜Prompt2（run_prompt1_through_prompt2）を順番に実行する。
    途中で失敗した場合はそこで例外を送出し後続Stageへは進まない——すでに完了した
    Stageの結果（Execution・Artifact）はそのまま残るため、Run全体をやり直す必要はなく、
    失敗したStageだけ個別のAPI（batch retry・aggregation再実行等）で立て直せる。
    実行開始時に前回のcancel_requestedフラグを必ずリセットする（2026-09-11、
    前回キャンセルされたRunを再実行した際に即座にまたキャンセルされるのを防ぐ）。"""
    cancellation.clear_cancel_flag(client, run_id)
    preprocess_outcome = persistence.run_and_persist_preprocess(client, run_id, file_paths)
    cancellation.raise_if_cancelled(client, run_id)
    downstream = run_prompt1_through_prompt2(
        client, azure_client, model, run_id, report_theme=report_theme, _clear_cancel_flag=False)
    return {
        "preprocess_execution_id": preprocess_outcome["execution_id"],
        "total_records": preprocess_outcome["result"].total_records,
        "nonduplicate_records": preprocess_outcome["result"].nonduplicate_records,
        **downstream,
    }


def _run_aggregation_and_prompt2(client, azure_client, model: str, run_id: str, *,
                                    report_theme: str = "") -> dict:
    """Canonical Prompt1が既に確定している前提で、Aggregation→Prompt2（全Section）→
    Finalizeを実行する（run_prompt1_through_prompt2とrun_aggregation_through_prompt2の
    共通の末尾処理）。呼び出し元でcancel_requestedのクリア・canonical_prompt1_execution_id
    の存在チェックを済ませてから呼ぶこと。"""
    cancellation.raise_if_cancelled(client, run_id)
    aggregation_execution_id = prompt2_orch.create_aggregation_execution(client, run_id)
    prompt2_orch.run_aggregation(client, run_id, aggregation_execution_id)

    cancellation.raise_if_cancelled(client, run_id)
    run = _get_run(client, run_id)
    template_asset = assets.get_asset_by_id(client, run["report_template_asset_id"])
    if not template_asset:
        raise ValueError(f"run_id={run_id}に固定されたReport Templateが見つかりません。")

    prompt2_execution_id = prompt2_orch.create_prompt2_execution(client, run_id, aggregation_execution_id)
    prompt2_orch.plan_composition_batches(client, run_id, prompt2_execution_id, template_asset)
    _run_prompt2_composition_to_completion(client, azure_client, model, run_id, prompt2_execution_id, template_asset)
    finalize_outcome = prompt2_orch.finalize_prompt2_execution(
        client, run_id, prompt2_execution_id, report_theme=report_theme, model=model)
    if not finalize_outcome.get("completed"):
        raise ValueError(f"prompt2_execution_id={prompt2_execution_id}のFinal Reportを確定できませんでした。")

    return {
        "aggregation_execution_id": aggregation_execution_id,
        "prompt2_execution_id": prompt2_execution_id,
    }


def run_prompt1_through_prompt2(client, azure_client, model: str, run_id: str, *,
                                  report_theme: str = "", _clear_cancel_flag: bool = True) -> dict:
    """既存のPreprocess結果（`canonical_preprocess_execution_id`）を使って、
    Prompt1（3 stage全バッチ）→Aggregation→Prompt2（全Section）→Finalizeだけを
    新しいExecutionとして実行する。CSVの再アップロードは不要——「処理やり直し」
    （ユーザー要望2026-09-11）の入口として、Preprocessに触れず何度でも呼び直せる。
    新しいCanonicalが確定すると、`geo_report_execution_canonical_service`が古い
    下流Execution（前回のAggregation/Prompt2）を自動でstale化する。
    実行開始時に前回のcancel_requestedフラグを必ずリセットする（2026-09-11、
    run_full_report_pipeline経由でなく本関数を直接「処理やり直し」の入口として
    呼んだ場合でも同様にリセットする必要があるため）。ただし`run_full_report_pipeline`
    経由（CSVアップロード直後の自動フルパイプライン実行）で呼ばれた場合は
    `_clear_cancel_flag=False`を渡し、ここでリセットしない——Preprocess実行中に
    ユーザーが要求したキャンセルが、Prompt1開始の瞬間に握り潰されてしまう不具合が
    あったため（2026-09-15修正）。"""
    if _clear_cancel_flag:
        cancellation.clear_cancel_flag(client, run_id)
    cancellation.raise_if_cancelled(client, run_id)
    run = _get_run(client, run_id)
    if not run.get("canonical_preprocess_execution_id"):
        raise ValueError(f"run_id={run_id}はまだPreprocessが完了していません。先にCSVをアップロードしてください。")

    prompt1_execution_id = prompt1_orch.create_prompt1_execution(client, run_id)
    prompt1_orch.plan_analysis_cluster_batches(client, run_id, prompt1_execution_id)
    prompt1_orch.plan_sentiment_cluster_batches(client, run_id, prompt1_execution_id)
    _run_prompt1_stage_to_completion(
        client, azure_client, model, run_id, prompt1_execution_id, "prompt1_analysis_cluster")
    _run_prompt1_stage_to_completion(
        client, azure_client, model, run_id, prompt1_execution_id, "prompt1_sentiment_cluster")
    # Targeted Stanceは2026-09-12改訂によりSC final classification結果（theme_relevance/
    # source_class/apply_to_cluster）に依存するため、SC stage完了後にここで初めて計画する
    # （旧: AC/SC/TSを実行前に一括計画していたが、TSはPreprocessの暫定labelだけでは
    # 判定できなくなったため順序を変更した）。
    prompt1_orch.plan_targeted_stance_batches(client, run_id, prompt1_execution_id)
    _run_prompt1_stage_to_completion(
        client, azure_client, model, run_id, prompt1_execution_id, "prompt1_targeted_stance")
    if not prompt1_orch.complete_prompt1_execution_if_done(client, run_id, prompt1_execution_id):
        raise ValueError(f"prompt1_execution_id={prompt1_execution_id}を完了として確定できませんでした。")

    downstream = _run_aggregation_and_prompt2(client, azure_client, model, run_id, report_theme=report_theme)
    return {"prompt1_execution_id": prompt1_execution_id, **downstream}


def run_aggregation_through_prompt2(client, azure_client, model: str, run_id: str, *,
                                       report_theme: str = "") -> dict:
    """既存のCanonical Prompt1結果（Application AI・ChatGPT Importどちらでもよい）を
    使って、Aggregation→Prompt2（全Section）→Finalizeだけを新しいExecutionとして
    実行する。大量データでPrompt1だけChatGPTへ退避し、Aggregation以降はアプリの
    Application AIで続けたい運用（2026-09-14、ユーザー要望）の入口。
    実行開始時に前回のcancel_requestedフラグを必ずリセットする。"""
    cancellation.clear_cancel_flag(client, run_id)
    run = _get_run(client, run_id)
    if not run.get("canonical_prompt1_execution_id"):
        raise ValueError(
            f"run_id={run_id}はまだCanonical Prompt1が確定していません。"
            "先にPrompt1（Application AIまたはChatGPT Import）を完了させてください。")
    return _run_aggregation_and_prompt2(client, azure_client, model, run_id, report_theme=report_theme)
