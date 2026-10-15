"""Report Studio v2 Phase 1: Report Edit Chat APIルーター
（/api/geo/report-studio/projects/{project_id}/runs/{run_id}/report-edit/...）。

既存api_server.pyの変更は「このモジュールのimportとinclude_routerの2行追加」のみ
（Phase D/E/Fと同じ方針）。API層ではビジネスロジックを再実装せず、
`geo_report_report_edit_service`を呼ぶだけにする。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import geo_report_report_edit_service as edit_svc
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
    print(f"[geo_report_report_edit_api] WARNING: Report Studio Report Edit disabled — {_SCHEMA_ERROR}", flush=True)


def _require_schema_ok():
    if not _SCHEMA_OK:
        raise HTTPException(status_code=503, detail=_SCHEMA_ERROR)


geo_report_report_edit_router = APIRouter(
    prefix="/api/geo/report-studio", dependencies=[Depends(_require_schema_ok)])


def _require_run(project_id: str, run_id: str) -> dict:
    try:
        run = get_run(_client, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if run["project_id"] != project_id:
        raise HTTPException(status_code=400, detail="run_idがこのprojectと一致しません")
    return run


class RequestReportEditRequest(BaseModel):
    instruction: str


@geo_report_report_edit_router.post("/projects/{project_id}/runs/{run_id}/report-edit")
def request_report_edit_endpoint(project_id: str, run_id: str, req: RequestReportEditRequest):
    """自然言語指示を受け、影響を受けるSectionのみ再生成してFinal Reportを更新する。
    edit_level='analytical_recomposition_required'の場合はcanonicalを更新せず、
    Prompt2 Recompositionが必要である旨をfinal_report_html=null/warning付きで返す。"""
    _require_run(project_id, run_id)
    if not req.instruction.strip():
        raise HTTPException(status_code=400, detail="instructionが空です。")
    try:
        result = edit_svc.request_report_edit(_client, _azure_client, _model, run_id, req.instruction)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


@geo_report_report_edit_router.get("/projects/{project_id}/runs/{run_id}/report-edit/history")
def get_report_edit_history_endpoint(project_id: str, run_id: str):
    """このRunのPrompt2/Report Edit Executionの連鎖（revision履歴）を返す。"""
    _require_run(project_id, run_id)
    return {"history": edit_svc.load_report_edit_history(_client, run_id)}


class UseAsCurrentReportRequest(BaseModel):
    execution_id: str


@geo_report_report_edit_router.post("/projects/{project_id}/runs/{run_id}/report-edit/use-as-current")
def use_report_execution_as_current_endpoint(project_id: str, run_id: str, req: UseAsCurrentReportRequest):
    """過去のPrompt2/Report Edit Executionをcanonical_report_execution_idへ再指定する
    （破壊的上書きではない——そのExecutionのfinal_report artifactはそのまま残る）。"""
    _require_run(project_id, run_id)
    try:
        result = edit_svc.revert_report_to_execution(_client, run_id, req.execution_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result
