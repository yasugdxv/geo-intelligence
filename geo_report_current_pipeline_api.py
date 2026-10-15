"""Report Studio「Design」領域: Current Pipeline実行 APIルーター
（/api/geo/report-studio/projects/{project_id}/runs/{run_id}/...）。

既存api_server.pyの変更は「このモジュールのimportとinclude_routerの2行追加」のみ。

`geo_report_current_pipeline_persistence_service`（Preprocess実行＋DB永続化＋進捗管理）を
UIから呼べるHTTPエンドポイントとして薄く公開する。前処理は時間がかかるため、
`POST .../preprocess`は同期的に完了まで待つが、別リクエストで`GET .../executions`や
`GET /executions/{execution_id}`をポーリングすれば途中経過（処理済みファイル数等）が見える
（ユーザー要望、2026-09-11。本プロジェクトにバックグラウンドジョブ基盤が無いため、
同期実行中の別リクエスト読み取りで進捗可視化を実現する）。
"""

import os
import tempfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from ai_client import make_openai_client  # noqa: E402
from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

import geo_report_current_pipeline_persistence_service as persistence
import geo_report_execution_canonical_service as canonical
import geo_report_full_pipeline_service as full_pipeline
import geo_report_prompt1_orchestration_service as orchestration
import geo_report_run_cancellation_service as run_cancellation
import geo_report_run_upload_cache_service as upload_cache
import geo_report_studio_service as studio
from geo_report_run_cancellation_service import PipelineCancelled

_config = load_config()
_client = SupabaseClient(_config)
_azure_client, _model = make_openai_client(_config)

_PROMPT1_STAGES = ("prompt1_analysis_cluster", "prompt1_sentiment_cluster", "prompt1_targeted_stance")

_SCHEMA_OK, _SCHEMA_ERROR = studio.check_schema(_client)
if not _SCHEMA_OK:
    print(f"[geo_report_current_pipeline_api] WARNING: Report Studio Current Pipeline disabled — "
          f"{_SCHEMA_ERROR}", flush=True)


def _require_schema_ok():
    if not _SCHEMA_OK:
        raise HTTPException(status_code=503, detail=_SCHEMA_ERROR)


geo_report_current_pipeline_router = APIRouter(
    prefix="/api/geo/report-studio", dependencies=[Depends(_require_schema_ok)])


def _require_run(run_id: str) -> dict:
    try:
        return persistence.get_run(_client, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


def _require_no_active_execution(run_id: str) -> None:
    """同一Runに対して既にrunning/pendingのExecutionがある状態で、新しい
    Preprocess/Full Pipeline/Rerun実行を開始させない（多重送信ガード）。

    2026-09-14発覚: run-full-pipelineは長時間ブロッキングする1本のHTTPリクエスト
    のため、ブラウザ/開発用プロキシ側のタイムアウトで「Failed to fetch」に見えると
    ユーザーが再度アップロードボタンを押してしまい、同一run_idに対してPreprocessが
    2重に走った。Preprocessの保存処理は既存データの削除・重複排除をせず単純追加
    insertのため、両方が完了するとgeo_report_recordsのレコードが丸ごと重複する
    （実際にこの事故が発生し、該当Runを削除して作り直す事後対応が必要になった）。
    バックエンド側でも防止できるよう、新規実行の受付時に必ずこのチェックを通す。"""
    active = _client.select("geo_report_executions", {
        "select": "id,stage,status", "run_id": f"eq.{run_id}", "status": "in.(running,pending)",
    })
    if active:
        stages = ", ".join(sorted({e["stage"] for e in active}))
        raise HTTPException(
            status_code=409,
            detail=f"このRunは既に実行中です（{stages}）。完了または中断を待ってから再実行してください。",
        )


@geo_report_current_pipeline_router.post("/projects/{project_id}/runs/{run_id}/preprocess")
async def run_preprocess_endpoint(project_id: str, run_id: str,
                                    files: list[UploadFile] | None = File(default=None),
                                    resume_execution_id: str | None = Form(None)):
    """resume_execution_id（2026-09-16追加、ユーザー要望「途中で保存して失敗したら
    そこから再開できないの」）: 同一runの以前失敗したPreprocess Executionのidを
    渡すと、そのExecutionを再利用し、既に完了したフェーズ・chunkをスキップして
    続きから再開する（geo_report_current_pipeline_persistence_service.
    run_and_persist_preprocessのdocstring参照）。

    2026-09-17追加、ユーザー指摘「途中まで実行してたのを再開するのに新しくCSVを
    アップロードするの？別のファイルだとデータが混ざるリスクがあるよね」: 最初の
    （resume_execution_id無しの）アップロード時に受け取った生CSVを、Run単位の
    キャッシュディレクトリ（OS一時領域下）へ保存しておく。resume_execution_id付きの
    再実行では、ブラウザから改めてファイルが送られてこなくてもこのキャッシュを
    自動的に再利用するため、ユーザーは同じCSVを毎回選び直す必要がなく、かつ
    「うっかり別のCSVを選んでデータが混ざる」事故も構造的に起こらない
    （persistence側のfile_fingerprint検証は、このキャッシュが失われて手動で
    再アップロードするフォールバック時の安全網）。"""
    run = _require_run(run_id)
    if run["project_id"] != project_id:
        raise HTTPException(status_code=400, detail="run_idがこのprojectと一致しません")
    _require_no_active_execution(run_id)

    if resume_execution_id:
        # 再開時は、キャッシュ済みの元ファイルを優先する（「再開＝前回と同じ入力の
        # 続きから」という契約を、UIの実装ミス等で誤って壊さないようにするため）。
        # キャッシュが失われている場合（サーバー再起動等）のみ、フォールバックとして
        # 今回ブラウザから添付されたファイルを使う——その場合でも、persistence側の
        # file_fingerprint検証が前回のチェックポイントと突き合わせるため、うっかり
        # 別のCSVを選んでも実行前に拒否される（データが混ざることはない）。
        saved_paths = upload_cache.cached_upload_paths(run_id)
        if not saved_paths:
            if not files:
                raise HTTPException(
                    status_code=400,
                    detail="再開に使う元のCSVが見つかりません（サーバー再起動等でキャッシュが失われた"
                           "可能性があります）。お手数ですがCSVを選択してもう一度アップロードしてください。",
                )
            upload_dir = upload_cache.run_upload_dir(run_id)
            os.makedirs(upload_dir, exist_ok=True)
            saved_paths = []
            for f in files:
                raw_bytes = await f.read()
                path = os.path.join(upload_dir, f.filename or "input.csv")
                with open(path, "wb") as fh:
                    fh.write(raw_bytes)
                saved_paths.append(path)
        try:
            outcome = await run_in_threadpool(
                persistence.run_and_persist_preprocess, _client, run_id, saved_paths,
                resume_execution_id=resume_execution_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    else:
        if not files:
            raise HTTPException(status_code=400, detail="CSVファイルを選択してください。")
        upload_cache.clear_run_upload_dir(run_id)
        upload_dir = upload_cache.run_upload_dir(run_id)
        os.makedirs(upload_dir, exist_ok=True)
        saved_paths = []
        for f in files:
            raw_bytes = await f.read()
            path = os.path.join(upload_dir, f.filename or "input.csv")
            with open(path, "wb") as fh:
                fh.write(raw_bytes)
            saved_paths.append(path)
        try:
            # 同期関数を直接awaitなしで呼ぶとasyncハンドラのイベントループそのものを
            # ブロックし、この処理が終わるまでサーバー全体が他のリクエスト（実行状況の
            # ポーリングやRun一覧取得等）に応答できなくなる（2026-09-11実機検証で発覚。
            # 「Designが未Publish」「実行履歴なし」という誤表示は、実際にはPublish/Run
            # データが失われたのではなく、このAPIが busy な間はポーリングそのものが
            # ハングしていたことが原因だった）。run_in_threadpoolで別スレッドへ逃がす。
            outcome = await run_in_threadpool(
                persistence.run_and_persist_preprocess, _client, run_id, saved_paths,
                resume_execution_id=None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    # ここに到達するのは成功時のみ（失敗時は上のexcept内でHTTPExceptionをraiseして
    # 関数を抜けている）。成功したRunはこの後Recovery Actionの対象から外れる
    # （フロント側でstatus="completed"のexecutionがあれば非表示になる）ため、
    # キャッシュはもう不要——次にこのrun_idへ新規アップロードする機会が来た時に
    # 混同しないよう、ここで確実に消しておく。
    upload_cache.clear_run_upload_dir(run_id)

    return {
        "execution_id": outcome["execution_id"],
        "total_records": outcome["result"].total_records,
        "nonduplicate_records": outcome["result"].nonduplicate_records,
        "analysis_cluster_count": len(outcome["result"].analysis_clusters),
        "sentiment_cluster_count": len(outcome["result"].sentiment_clusters),
        "artifact_count": outcome["artifact_count"],
    }


@geo_report_current_pipeline_router.post("/projects/{project_id}/runs/{run_id}/run-full-pipeline")
async def run_full_pipeline_endpoint(project_id: str, run_id: str, files: list[UploadFile] = File(...)):
    """CSVアップロードから前処理→Prompt1（3 stage全バッチ）→Aggregation→Prompt2（全Section）→
    Final Report確定までを1リクエストで通しで実行する（ユーザー要望2026-09-11:
    「プロンプト1,2はプロセスとして分けなくていい、バックエンドで統合処理してよい」）。
    各StageのExecutionは従来通り個別に記録されるため、別リクエストで`GET .../executions`を
    ポーリングすれば途中経過がそのまま見える。失敗したStageだけを個別のAPI
    （Prompt1バッチretry・Aggregation再実行等）で後から立て直すこともできる。"""
    run = _require_run(run_id)
    if run["project_id"] != project_id:
        raise HTTPException(status_code=400, detail="run_idがこのprojectと一致しません")
    _require_no_active_execution(run_id)

    with tempfile.TemporaryDirectory() as tmp_dir:
        saved_paths = []
        for f in files:
            raw_bytes = await f.read()
            path = os.path.join(tmp_dir, f.filename or "input.csv")
            with open(path, "wb") as fh:
                fh.write(raw_bytes)
            saved_paths.append(path)
        try:
            # run_preprocess_endpointと同じ理由でrun_in_threadpool必須（このパイプラインは
            # 数十分〜数時間かかり得るため、asyncハンドラ内で直接同期呼び出しするとその間
            # サーバー全体が他リクエストに応答できなくなる）。
            outcome = await run_in_threadpool(
                full_pipeline.run_full_report_pipeline, _client, _azure_client, _model, run_id, saved_paths)
        except PipelineCancelled as exc:
            return {"cancelled": True, "message": str(exc)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    return outcome


class RerunReportGenerationRequest(BaseModel):
    report_theme: str = ""


@geo_report_current_pipeline_router.post("/projects/{project_id}/runs/{run_id}/rerun-prompt1-through-prompt2")
def rerun_prompt1_through_prompt2_endpoint(project_id: str, run_id: str, req: RerunReportGenerationRequest):
    """CSVを再アップロードせず、既存のPreprocess結果からPrompt1〜Prompt2だけを
    新しいExecutionとして再実行する（ユーザー要望2026-09-11「処理やり直し」の入口）。
    新しいCanonicalが確定すると、古いAggregation/Prompt2 Executionは自動でstale化される。"""
    run = _require_run(run_id)
    if run["project_id"] != project_id:
        raise HTTPException(status_code=400, detail="run_idがこのprojectと一致しません")
    _require_no_active_execution(run_id)
    try:
        return full_pipeline.run_prompt1_through_prompt2(
            _client, _azure_client, _model, run_id, report_theme=req.report_theme)
    except PipelineCancelled as exc:
        return {"cancelled": True, "message": str(exc)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@geo_report_current_pipeline_router.post(
    "/projects/{project_id}/runs/{run_id}/run-aggregation-through-prompt2")
def run_aggregation_through_prompt2_endpoint(project_id: str, run_id: str, req: RerunReportGenerationRequest):
    """既存のCanonical Prompt1結果（Application AIまたはChatGPT Import、どちらでもよい）を
    使って、Aggregation→Prompt2だけを新しいExecutionとして実行する。大量データで
    Prompt1のみChatGPTへ退避し（Prompt1 Package Export→ChatGPT実行→Import→
    Use as Current）、以降のAggregation/Prompt2 CompositionはこのAPI経由でアプリの
    Application AIに任せたい運用（2026-09-14、ユーザー要望）の入口。"""
    run = _require_run(run_id)
    if run["project_id"] != project_id:
        raise HTTPException(status_code=400, detail="run_idがこのprojectと一致しません")
    _require_no_active_execution(run_id)
    try:
        return full_pipeline.run_aggregation_through_prompt2(
            _client, _azure_client, _model, run_id, report_theme=req.report_theme)
    except PipelineCancelled as exc:
        return {"cancelled": True, "message": str(exc)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@geo_report_current_pipeline_router.post("/projects/{project_id}/runs/{run_id}/cancel")
def cancel_run_endpoint(project_id: str, run_id: str):
    """実行中のPipeline処理へキャンセルを要求する（geo_report_design_api.pyの
    cancel_runと同じ処理。Pipelineタブが呼ぶAPIをこのモジュールへ集約するため
    ここにも同じエンドポイントを公開する——geo_report_current_pipeline_api.tsの
    既存呼び出し方針（このrouter配下で完結させる）に合わせる）。"""
    run = _require_run(run_id)
    if run["project_id"] != project_id:
        raise HTTPException(status_code=400, detail="run_idがこのprojectと一致しません")
    run_cancellation.request_cancel(_client, run_id)
    return {"cancel_requested": True}


@geo_report_current_pipeline_router.get("/projects/{project_id}/runs/{run_id}/executions")
def list_run_executions(project_id: str, run_id: str):
    run = _require_run(run_id)
    if run["project_id"] != project_id:
        raise HTTPException(status_code=400, detail="run_idがこのprojectと一致しません")
    executions = _client.select("geo_report_executions", {
        "select": "*", "run_id": f"eq.{run_id}", "order": "created_at.desc",
    })
    return {"executions": executions}


@geo_report_current_pipeline_router.get("/executions/{execution_id}")
def get_execution_status(execution_id: str):
    """進捗ポーリング用。前処理中は`progress`（files_processed/files_total/current_file）が、
    Prompt1分類中は`progress`（batches_completed/batches_failed/batches_total）が随時更新される。"""
    try:
        return {"execution": persistence.get_execution(_client, execution_id)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


class SetCanonicalExecutionRequest(BaseModel):
    stage: str
    execution_id: str


@geo_report_current_pipeline_router.post("/projects/{project_id}/runs/{run_id}/canonical")
def set_canonical_execution(project_id: str, run_id: str, req: SetCanonicalExecutionRequest):
    """Application AI/ChatGPT Fallbackどちらの実行結果を「正式採用（Canonical）」にするかを
    切り替える唯一の入り口（UIの「Use as Current」ボタンから呼ぶ）。切り替えに伴い、
    古い入力のまま残っている下流Execution（Aggregation/Prompt2）を自動でstale化する。"""
    run = _require_run(run_id)
    if run["project_id"] != project_id:
        raise HTTPException(status_code=400, detail="run_idがこのprojectと一致しません")
    try:
        return canonical.set_canonical_execution(_client, run_id, req.stage, req.execution_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# Prompt1 Application AI（Analysis/Sentiment Cluster分類・Targeted Stance）
# ---------------------------------------------------------------------------

def _require_stage(stage: str) -> str:
    if stage not in _PROMPT1_STAGES:
        raise HTTPException(status_code=400, detail=f"stageは{_PROMPT1_STAGES}のいずれかである必要があります")
    return stage


@geo_report_current_pipeline_router.post("/projects/{project_id}/runs/{run_id}/prompt1/plan")
def plan_prompt1_execution(project_id: str, run_id: str):
    """Preprocess完了後に呼ぶ。Execution（stage='prompt1'）を1つ作り、Analysis Cluster/
    Sentiment Clusterの2種類のバッチを計画する（実行はしない、computeは/run-batch・
    /run-allで少しずつ進める）。Targeted Stanceは2026-09-12改訂によりSC final
    classification結果に依存するため、SC stage完了後に別途/prompt1/plan-targeted-stance
    を呼ぶ（Prompt1 §4.5.8: theme_relevance=relevant & source_class=Public Voice &
    apply_to_cluster=trueの確定結果と、明示target検出を条件にするため）。"""
    run = _require_run(run_id)
    if run["project_id"] != project_id:
        raise HTTPException(status_code=400, detail="run_idがこのprojectと一致しません")
    try:
        execution_id = orchestration.create_prompt1_execution(_client, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    analysis_batches = orchestration.plan_analysis_cluster_batches(_client, run_id, execution_id)
    sentiment_batches = orchestration.plan_sentiment_cluster_batches(_client, run_id, execution_id)
    return {
        "execution_id": execution_id,
        "batch_counts": {
            "prompt1_analysis_cluster": len(analysis_batches),
            "prompt1_sentiment_cluster": len(sentiment_batches),
        },
    }


class PlanTargetedStanceRequest(BaseModel):
    execution_id: str


@geo_report_current_pipeline_router.post(
    "/projects/{project_id}/runs/{run_id}/prompt1/plan-targeted-stance")
def plan_prompt1_targeted_stance(project_id: str, run_id: str, req: PlanTargetedStanceRequest):
    """Sentiment Cluster stageが全件completed（failedを含まない）になってから呼ぶ。
    同一executionのSC final classification結果を使ってTargeted Stance対象を絞り込み
    計画する（2026-09-12改訂、本ファイルdocstring・plan_prompt1_execution参照）。"""
    run = _require_run(run_id)
    if run["project_id"] != project_id:
        raise HTTPException(status_code=400, detail="run_idがこのprojectと一致しません")
    sc_batches = _client.select("geo_report_execution_batches", {
        "select": "status", "execution_id": f"eq.{req.execution_id}",
        "stage": "eq.prompt1_sentiment_cluster",
    })
    if not sc_batches or any(b["status"] not in ("completed", "skipped") for b in sc_batches):
        raise HTTPException(
            status_code=400,
            detail="Sentiment Cluster stageが完了していません。Targeted Stanceの対象は"
                   "SC final classification結果に依存するため、SC完了後に呼んでください。")
    stance_batches = orchestration.plan_targeted_stance_batches(_client, run_id, req.execution_id)
    return {"execution_id": req.execution_id, "batch_count": len(stance_batches)}


@geo_report_current_pipeline_router.get("/projects/{project_id}/runs/{run_id}/prompt1/batches")
def list_prompt1_batches(project_id: str, run_id: str, execution_id: str, stage: str | None = None):
    run = _require_run(run_id)
    if run["project_id"] != project_id:
        raise HTTPException(status_code=400, detail="run_idがこのprojectと一致しません")
    params = {"select": "*", "run_id": f"eq.{run_id}", "execution_id": f"eq.{execution_id}",
              "order": "batch_no.asc"}
    if stage:
        params["stage"] = f"eq.{_require_stage(stage)}"
    return {"batches": _client.select("geo_report_execution_batches", params)}


class RunBatchRequest(BaseModel):
    execution_id: str
    stage: str


@geo_report_current_pipeline_router.post("/projects/{project_id}/runs/{run_id}/prompt1/run-batch")
def run_prompt1_batch(project_id: str, run_id: str, req: RunBatchRequest):
    """pendingバッチを1件だけ処理する（UIから少しずつ繰り返し呼ぶ想定、既存
    geo_report_studio_api.pyのclassify-nextと同じ設計思想）。"""
    run = _require_run(run_id)
    if run["project_id"] != project_id:
        raise HTTPException(status_code=400, detail="run_idがこのprojectと一致しません")
    stage = _require_stage(req.stage)
    outcome = orchestration.run_next_pending_batch(
        _client, _azure_client, _model, run_id, req.execution_id, stage)
    return outcome


class RunAllBatchesRequest(BaseModel):
    execution_id: str
    stage: str
    max_batches: int | None = None


@geo_report_current_pipeline_router.post("/projects/{project_id}/runs/{run_id}/prompt1/run-all")
def run_all_prompt1_batches(project_id: str, run_id: str, req: RunAllBatchesRequest):
    run = _require_run(run_id)
    if run["project_id"] != project_id:
        raise HTTPException(status_code=400, detail="run_idがこのprojectと一致しません")
    stage = _require_stage(req.stage)
    results = orchestration.run_all_pending_batches(
        _client, _azure_client, _model, run_id, req.execution_id, stage, max_batches=req.max_batches)
    return {"results": results}


@geo_report_current_pipeline_router.post("/projects/{project_id}/runs/{run_id}/prompt1/batches/{batch_id}/retry")
def retry_prompt1_batch(project_id: str, run_id: str, batch_id: str, req: RunBatchRequest):
    run = _require_run(run_id)
    if run["project_id"] != project_id:
        raise HTTPException(status_code=400, detail="run_idがこのprojectと一致しません")
    try:
        outcome = orchestration.retry_batch(_client, _azure_client, _model, run_id, req.execution_id, batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return outcome


@geo_report_current_pipeline_router.post("/projects/{project_id}/runs/{run_id}/prompt1/complete")
def complete_prompt1_execution(project_id: str, run_id: str, req: RunBatchRequest):
    """全バッチが完了していればExecutionをcompletedにしCanonical Prompt1を確定する。
    未完了・失敗バッチが残っていればcompleted:falseを返す（UIはリトライを促す）。"""
    run = _require_run(run_id)
    if run["project_id"] != project_id:
        raise HTTPException(status_code=400, detail="run_idがこのprojectと一致しません")
    completed = orchestration.complete_prompt1_execution_if_done(_client, run_id, req.execution_id)
    return {"completed": completed}
