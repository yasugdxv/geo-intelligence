"""Phase D: Production Scheduling — Job状態参照・Dry Run・手動実行のAPI。

GETは状態参照・Dry Run（DB変更なし）。POST（手動実行）のみ認証必須
（既存geo_ios_identity.verify_ios_identityを再利用、新しい認証基盤は作らない）。
本番OS Scheduler（cron/systemd timer）自体はここから登録・起動しない——
あくまで既存Connector実行ロジックを安全なオーケストレーション層経由で手動/
将来の定期実行から呼べるようにするだけ（docs/production-scheduling-runbook.md参照）。
"""
from fastapi import APIRouter, Depends, HTTPException

from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

import geo_ios_identity
import geo_scheduler_service as scheduler
import geo_scheduler_tick as scheduler_tick

_client = SupabaseClient(load_config())

geo_scheduler_router = APIRouter(prefix="/api/geo/scheduler")


@geo_scheduler_router.get("/health")
def get_scheduler_health(is_demo: bool = False):
    """Scheduler自体の状態（heartbeatに基づく）。heartbeatが一度も無ければ"not_configured"、
    45分以内なら"healthy"、それより古ければ"unhealthy"。Jobごとの状態・止まった実行中Jobも返す。
    /api/geo/operations/summary の collection.scheduler と同じ内容（LLMは使わない）。"""
    return scheduler_tick.scheduler_overview(_client, is_demo=is_demo)


@geo_scheduler_router.get("/jobs")
def list_job_statuses(is_demo: bool = False):
    """item4: 全JobのJob status一覧（job_id/status/last_success_at/processed_count/
    error/retry_classification等）。"""
    return {"jobs": scheduler.get_all_job_statuses(_client, is_demo=is_demo)}


@geo_scheduler_router.get("/jobs/{job_id}")
def get_job_status(job_id: str, is_demo: bool = False):
    try:
        return scheduler.get_job_status(_client, job_id, is_demo=is_demo)
    except scheduler.SchedulerError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@geo_scheduler_router.get("/jobs/{job_id}/dry-run")
def dry_run_job(job_id: str, is_demo: bool = False):
    """item21: DB変更しない。対象・予定・catch-upギャップだけを返す。"""
    try:
        return scheduler.dry_run_job(_client, job_id, is_demo=is_demo)
    except scheduler.SchedulerError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@geo_scheduler_router.post("/jobs/{job_id}/run")
def run_job(job_id: str, is_demo: bool = False,
            identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    """item20: 安全なManual Trigger。既存lock/依存関係チェックは通常通り適用する
    （危険な強制実行はforce=Trueの別経路にせず、まずは安全な既定動作のみ公開する）。"""
    try:
        return scheduler.run_job(_client, job_id, is_demo=is_demo, requested_by=identity.identity_id or "manual_trigger")
    except scheduler.SchedulerError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@geo_scheduler_router.post("/detect-stale")
def detect_stale_jobs(is_demo: bool = False,
                       identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    """item9: running状態のまま残留しているstale Jobを検出し、failedへ補正する。
    自動的に成功扱いにはしない。手動実行専用（Dashboardの「stale検知を実行」ボタン等から）。"""
    return {"marked": scheduler.detect_and_mark_stale_jobs(_client, is_demo=is_demo)}
