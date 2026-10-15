"""Report Studio「Design」領域: Python Aggregation + Prompt2 Application AI（Phase E）
APIルーター（/api/geo/report-studio/projects/{project_id}/runs/{run_id}/...）。

既存api_server.pyの変更は「このモジュールのimportとinclude_routerの2行追加」のみ。
API層ではビジネスロジックを再実装せず、`geo_report_prompt2_aggregation_service`/
`geo_report_prompt2_orchestration_service`を呼ぶだけにする（Phase D/Fと同じ方針）。
Execution進捗ポーリングは既存の`GET /api/geo/report-studio/executions/{execution_id}`
（geo_report_current_pipeline_api.py、stage非依存の汎用エンドポイント）をそのまま使う
——ここでは重複させない。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import geo_report_project_assets_service as assets
import geo_report_prompt2_orchestration_service as orchestration
import geo_report_studio_service as studio
from ai_client import make_openai_client  # noqa: E402
from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402
from geo_report_prompt_export_service import get_run

_config = load_config()
_client = SupabaseClient(_config)
_azure_client, _model = make_openai_client(_config)

_SCHEMA_OK, _SCHEMA_ERROR = studio.check_schema(_client)
if not _SCHEMA_OK:
    print(f"[geo_report_prompt2_api] WARNING: Report Studio Prompt2 disabled — {_SCHEMA_ERROR}", flush=True)


def _require_schema_ok():
    if not _SCHEMA_OK:
        raise HTTPException(status_code=503, detail=_SCHEMA_ERROR)


geo_report_prompt2_router = APIRouter(
    prefix="/api/geo/report-studio", dependencies=[Depends(_require_schema_ok)])


def _require_run(project_id: str, run_id: str) -> dict:
    try:
        run = get_run(_client, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if run["project_id"] != project_id:
        raise HTTPException(status_code=400, detail="run_idがこのprojectと一致しません")
    return run


def _get_template_asset(run: dict) -> dict:
    template_asset = assets.get_asset_by_id(_client, run["report_template_asset_id"])
    if not template_asset:
        raise HTTPException(status_code=400, detail="このRunに固定されたReport Templateが見つかりません。")
    return template_asset


# ---------------------------------------------------------------------------
# Aggregation（Python、record_cluster_map結合〜denominator/trend集計）
# ---------------------------------------------------------------------------

@geo_report_prompt2_router.post("/projects/{project_id}/runs/{run_id}/aggregation/run")
def run_aggregation_endpoint(project_id: str, run_id: str):
    """Canonical Prompt1完了後に呼ぶ。Execution（stage='aggregation'）を作成し、同期的に
    G.1/G.2の集計まで完走する（前処理と同様、時間のかかる処理だが実行後は同期応答で
    完了する。バッチ分割はPrompt1/Prompt2 Compositionほど重くないため単一処理でよい）。"""
    _require_run(project_id, run_id)
    try:
        execution_id = orchestration.create_aggregation_execution(_client, run_id)
        result = orchestration.run_aggregation(_client, run_id, execution_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "execution_id": execution_id,
        "biweekly_period_count": len(result["biweekly_rows"]),
        "weekly_period_count": len(result["weekly_rows"]),
        "monthly_period_count": len(result["monthly_rows"]),
    }


# ---------------------------------------------------------------------------
# Prompt2 Composition（Section単位の構造化JSON生成＋Renderer）
# ---------------------------------------------------------------------------

class PlanPrompt2Request(BaseModel):
    aggregation_execution_id: str


@geo_report_prompt2_router.post("/projects/{project_id}/runs/{run_id}/prompt2/plan")
def plan_prompt2_execution(project_id: str, run_id: str, req: PlanPrompt2Request):
    """Aggregation完了後に呼ぶ。Execution（stage='prompt2', execution_method='application_ai'）を
    1つ作り、Runに固定されたReport TemplateのSection数ぶんComposition Batchを計画する。"""
    run = _require_run(project_id, run_id)
    template_asset = _get_template_asset(run)
    execution_id = orchestration.create_prompt2_execution(_client, run_id, req.aggregation_execution_id)
    batches = orchestration.plan_composition_batches(_client, run_id, execution_id, template_asset)
    return {"execution_id": execution_id, "batch_count": len(batches)}


@geo_report_prompt2_router.get("/projects/{project_id}/runs/{run_id}/prompt2/batches")
def list_prompt2_batches(project_id: str, run_id: str, execution_id: str):
    _require_run(project_id, run_id)
    batches = _client.select("geo_report_execution_batches", {
        "select": "*", "run_id": f"eq.{run_id}", "execution_id": f"eq.{execution_id}",
        "stage": "eq.prompt2_composition", "order": "batch_no.asc",
    })
    return {"batches": batches}


class RunPrompt2BatchRequest(BaseModel):
    execution_id: str


@geo_report_prompt2_router.post("/projects/{project_id}/runs/{run_id}/prompt2/run-batch")
def run_prompt2_batch(project_id: str, run_id: str, req: RunPrompt2BatchRequest):
    """pending Sectionを1件だけ処理する（UIから少しずつ繰り返し呼ぶ想定、Prompt1 Batch
    実行エンドポイントと同じ設計思想）。"""
    run = _require_run(project_id, run_id)
    template_asset = _get_template_asset(run)
    outcome = orchestration.run_next_composition_batch(
        _client, _azure_client, _model, run_id, req.execution_id, template_asset=template_asset)
    return outcome


class RunAllPrompt2BatchesRequest(BaseModel):
    execution_id: str
    max_batches: int | None = None


@geo_report_prompt2_router.post("/projects/{project_id}/runs/{run_id}/prompt2/run-all")
def run_all_prompt2_batches(project_id: str, run_id: str, req: RunAllPrompt2BatchesRequest):
    run = _require_run(project_id, run_id)
    template_asset = _get_template_asset(run)
    results = orchestration.run_all_pending_composition_batches(
        _client, _azure_client, _model, run_id, req.execution_id,
        template_asset=template_asset, max_batches=req.max_batches)
    return {"results": results}


class FinalizePrompt2Request(BaseModel):
    execution_id: str
    report_theme: str = ""


@geo_report_prompt2_router.post("/projects/{project_id}/runs/{run_id}/prompt2/finalize")
def finalize_prompt2(project_id: str, run_id: str, req: FinalizePrompt2Request):
    """全Section完了していればFinal Report(HTML)＋Canonical Output 10ファイルを組み立て、
    Runのcanonical_prompt2_execution_id/canonical_report_execution_idを確定する。
    未完了・失敗バッチが残っていれば{"completed": false}を返す（UIはリトライを促す）。"""
    _require_run(project_id, run_id)
    outcome = orchestration.finalize_prompt2_execution(
        _client, run_id, req.execution_id, report_theme=req.report_theme, model=_model)
    return outcome
