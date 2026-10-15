"""Report Studio「Design」領域: ChatGPT Fallback（Phase F）APIルーター
（/api/geo/report-studio/projects/{project_id}/runs/{run_id}/...）。

既存api_server.pyの変更は「このモジュールのimportとinclude_routerの2行追加」のみ。
API層ではビジネスロジックを再実装せず、`geo_report_prompt_export_service`を呼ぶだけにする
（Phase B/D/Eと同じ方針）。
"""

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, File
from starlette.concurrency import run_in_threadpool

import geo_report_prompt_export_service as export_svc
import geo_report_studio_service as studio
from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

_config = load_config()
_client = SupabaseClient(_config)

_SCHEMA_OK, _SCHEMA_ERROR = studio.check_schema(_client)
if not _SCHEMA_OK:
    print(f"[geo_report_prompt_export_api] WARNING: Report Studio ChatGPT Fallback disabled — "
          f"{_SCHEMA_ERROR}", flush=True)


def _require_schema_ok():
    if not _SCHEMA_OK:
        raise HTTPException(status_code=503, detail=_SCHEMA_ERROR)


geo_report_prompt_export_router = APIRouter(
    prefix="/api/geo/report-studio", dependencies=[Depends(_require_schema_ok)])


def _require_run(project_id: str, run_id: str) -> dict:
    try:
        run = export_svc.get_run(_client, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if run["project_id"] != project_id:
        raise HTTPException(status_code=400, detail="run_idがこのprojectと一致しません")
    return run


@geo_report_prompt_export_router.get("/projects/{project_id}/runs/{run_id}/final-report")
def get_final_report(project_id: str, run_id: str):
    """Runのcanonical_report_execution_idが指すFinal Report HTMLを返す
    （Application AI/ChatGPT Import/Report Editのどれで確定したかを問わない共通の読み出し口）。"""
    _require_run(project_id, run_id)
    try:
        html = export_svc.get_final_report_html(_client, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"html": html}


@geo_report_prompt_export_router.get("/projects/{project_id}/runs/{run_id}/prompt1/export")
def export_prompt1_package(project_id: str, run_id: str):
    _require_run(project_id, run_id)
    try:
        zip_bytes = export_svc.export_prompt1_package(_client, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return Response(
        content=zip_bytes, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="prompt1_package_{run_id}.zip"'})


@geo_report_prompt_export_router.post("/projects/{project_id}/runs/{run_id}/prompt1/import")
async def import_prompt1_package(project_id: str, run_id: str, file: UploadFile = File(...)):
    """import_prompt1_resultは行単位DB書き込みで数十分かかることがある完全同期の
    重い処理。2026-09-16発見: run_in_threadpoolを挟まないとこの間サーバー全体
    （他の全リクエスト、プロジェクト一覧取得等）がブロックされる
    （run_preprocess_endpoint等で過去に直した不具合と同じパターン）。"""
    _require_run(project_id, run_id)
    zip_bytes = await file.read()
    try:
        return await run_in_threadpool(export_svc.import_prompt1_result, _client, run_id, zip_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@geo_report_prompt_export_router.get("/projects/{project_id}/runs/{run_id}/prompt2/export")
def export_prompt2_package(project_id: str, run_id: str):
    _require_run(project_id, run_id)
    try:
        zip_bytes = export_svc.export_prompt2_package(_client, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return Response(
        content=zip_bytes, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="prompt2_package_{run_id}.zip"'})


@geo_report_prompt_export_router.post("/projects/{project_id}/runs/{run_id}/prompt2/import")
async def import_prompt2_package(project_id: str, run_id: str, file: UploadFile = File(...)):
    """import_prompt1_packageと同じ理由でrun_in_threadpool必須（2026-09-16）。"""
    _require_run(project_id, run_id)
    zip_bytes = await file.read()
    try:
        return await run_in_threadpool(export_svc.import_prompt2_result, _client, run_id, zip_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# Combined ChatGPT Session（Prompt1+Prompt2を1セッションで通しで実行、2026-09-16追加）
# ---------------------------------------------------------------------------

@geo_report_prompt_export_router.get("/projects/{project_id}/runs/{run_id}/combined/export")
def export_combined_package(project_id: str, run_id: str):
    """Preprocess完了直後（Canonical Prompt1不要）に呼べる、Prompt1+Prompt2を
    1セッションで通しで実行するためのCombined Export。"""
    _require_run(project_id, run_id)
    try:
        zip_bytes = export_svc.export_combined_prompt1_prompt2_package(_client, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return Response(
        content=zip_bytes, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="combined_package_{run_id}.zip"'})


@geo_report_prompt_export_router.post("/projects/{project_id}/runs/{run_id}/combined/import")
async def import_combined_package(project_id: str, run_id: str,
                                     prompt1_file: UploadFile = File(...),
                                     prompt2_file: UploadFile = File(...)):
    """Combined Sessionの結果（prompt1_output.zip・prompt2_output.zip）を続けて取り込み、
    両方を自動でCanonical採用する。import_prompt1_package/import_prompt2_packageと
    同じ理由でrun_in_threadpool必須。"""
    _require_run(project_id, run_id)
    prompt1_zip_bytes = await prompt1_file.read()
    prompt2_zip_bytes = await prompt2_file.read()
    try:
        return await run_in_threadpool(
            export_svc.import_combined_prompt1_prompt2_result, _client, run_id,
            prompt1_zip_bytes, prompt2_zip_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
