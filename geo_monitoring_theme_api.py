"""ユーザー向けMonitoring API（/api/geo/monitoring-themes）と Theme Explorer API（/api/geo/theme-explorer）。

Theme Explorerは既存geo_theme_candidates（Freeze済み）を読み取り、絞り込み・注目度・分類を
付与して返すだけ（既存/api/geo/theme-candidates APIは無改修）。
MonitoringのAlertは決定的Ruleで判定し、reason（なぜAlertか）を必ず持つ。
Mutationは既存のverify_ios_identityで保護する。
"""
from __future__ import annotations

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ai_client import make_openai_client  # noqa: E402
from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

import geo_ios_identity
import geo_monitoring_theme_service as monitoring
import geo_source_library_api as source_api
import geo_source_library_service as lib
import geo_theme_discovery_service as discovery
import geo_theme_coverage_service
import geo_theme_explorer_service as explorer

_config = load_config()
_client = SupabaseClient(_config)
_azure_client, _model = make_openai_client(_config)

geo_monitoring_theme_router = APIRouter(prefix="/api/geo/monitoring-themes")
geo_theme_explorer_router = APIRouter(prefix="/api/geo/theme-explorer")

_guard = source_api._guard


# ---------------------------------------------------------------------------
# Theme Explorer
# ---------------------------------------------------------------------------

@geo_theme_explorer_router.get("")
def explore_themes(q: str | None = None, region: str | None = None, category: str | None = None,
                   level: str | None = None, bucket: str | None = None, new_only: bool = False,
                   period_days: int | None = None, status: str | None = None, is_demo: bool = False,
                   page: int = 1, page_size: int = 20, keyword: str | None = None, sort: str = "newest",
                   include_deleted: bool = False):
    with _guard():
        return explorer.explore(
            _client, _azure_client, _model, q=q, region=region, category=category, level=level,
            bucket=bucket, new_only=new_only, period_days=period_days, status=status, is_demo=is_demo,
            page=page, page_size=min(max(page_size, 1), 100), keyword=keyword,
            sort=sort if sort in ("newest", "oldest", "attention") else "newest", include_deleted=include_deleted)


class DiscoverBody(BaseModel):
    prompt: str
    source_ids: list[str] = []
    source_set_id: str | None = None
    period_days: int | None = None


@geo_theme_explorer_router.post("/discover")
def start_discovery(body: DiscoverBody,
                    identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    """「テーマ探索開始」: 直近の記事からTheme Candidate（提案）をその場で作るジョブを開始する（V1_1）。
    候補をDBに保存する書き込みを伴うため認証必須。進行状況は GET /discover/{job_id} で取得する。"""
    if not (body.prompt or "").strip():
        raise HTTPException(status_code=400, detail="探索したい内容を入力してください")
    job_id = discovery.start_job(
        _client, _azure_client, _model, prompt=body.prompt, source_ids=body.source_ids,
        source_set_id=body.source_set_id, period_days=body.period_days)
    return {"job_id": job_id}


@geo_theme_explorer_router.get("/discover/{job_id}")
def get_discovery(job_id: str):
    """探索ジョブの状態。steps は実際の処理段階（入力を解釈／関連記事を検索／テーマ候補を整理）に連動する。"""
    job = discovery.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="探索ジョブが見つかりません（期限切れの可能性があります）")
    return job


# ---------------------------------------------------------------------------
# Monitoring
# ---------------------------------------------------------------------------

class MonitoringBody(BaseModel):
    display_name: str | None = None
    theme_candidate_id: str | None = None
    theme_id: str | None = None
    source_set_id: str | None = None
    regions: list[str] | None = None
    actors: list[str] | None = None
    tags: list[str] | None = None
    keywords: list[str] | None = None
    monitoring_criteria: str | None = None
    sensitivity: str | None = None
    frequency: str | None = None
    status: str | None = None


def _fields(body: MonitoringBody) -> dict:
    return {k: v for k, v in body.model_dump().items() if v is not None}


@geo_monitoring_theme_router.get("")
def list_monitoring_themes(include_archived: bool = False):
    with _guard():
        return monitoring.list_monitoring_themes(_client, include_archived=include_archived)


@geo_monitoring_theme_router.post("")
def create_monitoring_theme(body: MonitoringBody,
                            identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    with _guard():
        theme = monitoring.create_monitoring_theme(_client, _fields(body), created_by=identity.identity_id)
        return {"monitoring_theme": theme, "check": _safe_assess(theme)}


class FromCandidateBody(BaseModel):
    candidate_id: str
    source_ids: list[str] = []
    source_set_id: str | None = None
    keywords: list[str] = []
    sensitivity: str = "medium"
    frequency: str = "daily"


@geo_monitoring_theme_router.post("/from-candidate")
def create_from_candidate(body: FromCandidateBody,
                          identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    """Theme Explorerの候補 → Monitoring登録。Theme + Source Set + Actors + Tags を引き継ぐ。
    同じ候補が既にMonitoring中なら重複登録せず既存を返す。"""
    with _guard():
        existing = _client.select("geo_monitoring_themes", {
            "select": "*", "theme_candidate_id": f"eq.{body.candidate_id}", "status": "neq.archived"})
        if existing:
            return {"monitoring_theme": existing[0], "already_monitoring": True, "check": None}
        rows = _client.select("geo_theme_candidates", {"select": "*", "candidate_id": f"eq.{body.candidate_id}"})
        if not rows:
            raise HTTPException(status_code=404, detail="Theme Candidateが見つかりません")
        theme = monitoring.create_from_candidate(
            _client, rows[0], source_ids=body.source_ids, source_set_id=body.source_set_id,
            created_by=identity.identity_id,
            overrides={"keywords": body.keywords, "sensitivity": body.sensitivity, "frequency": body.frequency})
        return {"monitoring_theme": theme, "already_monitoring": False, "check": _safe_assess(theme)}


def _safe_assess(theme: dict) -> dict:
    try:
        return monitoring.assess(_client, theme, azure_client=_azure_client, model=_model)["assessment"]
    except Exception as exc:  # noqa: BLE001 — 評価失敗でも登録自体は成功させる
        return {"state": None, "error": f"初回評価に失敗しました: {exc}"}


@geo_monitoring_theme_router.post("/check-all")
def check_all(identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    with _guard():
        return monitoring.check_all(_client, azure_client=_azure_client, model=_model)


@geo_monitoring_theme_router.get("/live-intelligence-status")
def live_intelligence_status():
    """Coverage別のLive Intelligence Status（READY/WARNING/BLOCKED）。決定論・読み取り専用。"""
    return geo_theme_coverage_service.build_live_intelligence_status(_client)


@geo_monitoring_theme_router.get("/{monitoring_theme_id}")
def get_detail(monitoring_theme_id: str, refresh: bool = False):
    with _guard():
        detail = monitoring.get_detail(_client, monitoring_theme_id, refresh=refresh, azure_client=_azure_client, model=_model)
    if not detail:
        raise HTTPException(status_code=404, detail="Monitoring Themeが見つかりません")
    return detail


@geo_monitoring_theme_router.patch("/{monitoring_theme_id}")
def update_monitoring_theme(monitoring_theme_id: str, body: MonitoringBody,
                            identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    with _guard():
        theme = monitoring.update_monitoring_theme(_client, monitoring_theme_id, _fields(body))
    if not theme:
        raise HTTPException(status_code=404, detail="Monitoring Themeが見つかりません")
    return {"monitoring_theme": theme}


@geo_monitoring_theme_router.delete("/{monitoring_theme_id}")
def archive_monitoring_theme(monitoring_theme_id: str,
                             identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    with _guard():
        theme = monitoring.archive_monitoring_theme(_client, monitoring_theme_id)
    if not theme:
        raise HTTPException(status_code=404, detail="Monitoring Themeが見つかりません")
    return {"monitoring_theme": theme, "archived": True}


@geo_monitoring_theme_router.post("/alerts/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: str,
                      identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    with _guard():
        if not monitoring.acknowledge_alert(_client, alert_id, by=identity.identity_id):
            raise HTTPException(status_code=404, detail="Alertが見つかりません")
    return {"ok": True}
