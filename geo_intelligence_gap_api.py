"""地政学インテリジェンス基盤 Phase3: Intelligence Needs（External Request由来Knowledge Gap）
Dashboard向けAPIルーター。

既存/api/geo/knowledge/*（Case向けgeo_knowledge_gaps）とは独立したパス空間
（/api/geo/intelligence-needs/*）にマウントする。既存api_server.pyの変更は「このモジュールの
importとinclude_routerの2行追加」のみ（既存geo_expert_review_api.py分離時と同じ方針）。

外部アプリ（Sustainability等）はこのAPIを呼ばない。Geo Intelligence Agent（/api/v1/
geo-intelligence/*）が内部でgeo_knowledge_gap_serviceを呼び出してGapを生成し、Geo Dashboard
（このルーター経由）が地政学担当者向けの管理画面としてそれを表示・更新する。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402
from ai_client import make_openai_client  # noqa: E402

import geo_knowledge_gap_service as gap_service
import geo_report_reassessment_service as reassessment_service
import geo_report_studio_api as report_studio_api
import geo_report_studio_service as report_studio_service

_config = load_config()
_client = SupabaseClient(_config)
_azure_client, _model = make_openai_client(_config)

geo_intelligence_gap_router = APIRouter(prefix="/api/geo/intelligence-needs")


@geo_intelligence_gap_router.get("")
def list_intelligence_needs(status: str | None = None):
    return {"needs": gap_service.list_intelligence_needs(_client, status=status)}


@geo_intelligence_gap_router.get("/{gap_id}")
def get_intelligence_need(gap_id: str):
    detail = gap_service.get_intelligence_need_detail(_client, gap_id)
    if not detail:
        raise HTTPException(status_code=404, detail="gap_idが見つかりません")
    return detail


class UpdateIntelligenceNeedRequest(BaseModel):
    status: str | None = None
    assigned_to: str | None = None
    actor_id: str = "analyst"


@geo_intelligence_gap_router.patch("/{gap_id}")
def update_intelligence_need(gap_id: str, req: UpdateIntelligenceNeedRequest):
    try:
        return gap_service.update_intelligence_need(
            _client, gap_id, status=req.status, assigned_to=req.assigned_to, actor_id=req.actor_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@geo_intelligence_gap_router.get("/by-request/{request_id}")
def get_gap_for_request(request_id: str):
    """Request詳細画面から「このRequestが紐づいたIntelligence Need」へ遷移するための逆引き。"""
    gap = gap_service.get_gap_for_request(_client, request_id)
    if not gap:
        raise HTTPException(status_code=404, detail="このrequest_idに紐づくIntelligence Needはありません")
    return gap


class ResolveIntelligenceNeedRequest(BaseModel):
    report_id: str
    resolved_by: str
    resolution_note: str | None = None
    actor_id: str = "analyst"


def _trigger_report_studio_reassessment(resolved_gap: dict, req: ResolveIntelligenceNeedRequest) -> dict:
    """Phase6 Closed-loop Reassessment: Report Studio起源のIntelligence Needが解消された
    タイミングで、発生源Reportの再評価要否を判定する（geo_report_reassessment_service）。
    根拠ReportのTitle/本文冒頭とAnalystのresolution_noteをMateriality Check用の材料にする。"""
    document_title, document_text = None, None
    reports = _client.select("geo_reports", {"select": "*", "report_id": f"eq.{req.report_id}", "limit": "1"})
    if reports:
        docs = _client.select("geo_documents", {
            "select": "title,extracted_text", "document_id": f"eq.{reports[0]['document_id']}", "limit": "1"})
        if docs:
            document_title = docs[0].get("title")
            document_text = docs[0].get("extracted_text")

    new_evidence_summary = reassessment_service.build_new_evidence_summary(
        resolution_note=req.resolution_note, document_title=document_title, document_text=document_text)
    return reassessment_service.run_reassessment_gate(
        _client, _azure_client, _model, gap=resolved_gap, new_evidence_summary=new_evidence_summary,
        get_latest_synthesis=report_studio_service.get_latest_synthesis,
        run_synthesize_narrative=report_studio_api.run_synthesize_narrative,
    )


@geo_intelligence_gap_router.post("/{gap_id}/resolve")
def resolve_intelligence_need(gap_id: str, req: ResolveIntelligenceNeedRequest):
    """Reportとの紐付けを必須とするresolve（Add Resolution Report相当の導線）。Report
    Studio起源のNeedの場合、resolve自体は独立して成立させたうえで、Reassessment Gate
    （geo_report_reassessment_service）による発生源Reportの再評価要否判定を追加で行う
    （Gateが失敗してもresolve自体は失敗させない — run_reassessment_gate内で握りつぶす）。"""
    try:
        resolved_gap = gap_service.resolve_intelligence_need(
            _client, gap_id, report_id=req.report_id, resolved_by=req.resolved_by,
            resolution_note=req.resolution_note, actor_id=req.actor_id)
    except gap_service.IntelligenceNeedResolutionError as e:
        raise HTTPException(status_code=400, detail=str(e))

    reassessment_result = None
    if reassessment_service.is_report_studio_sourced(resolved_gap):
        reassessment_result = _trigger_report_studio_reassessment(resolved_gap, req)

    return {**resolved_gap, "report_studio_reassessment": reassessment_result}


class DismissIntelligenceNeedRequest(BaseModel):
    dismissed_by: str
    reason: str | None = None
    actor_id: str = "analyst"


@geo_intelligence_gap_router.post("/{gap_id}/dismiss")
def dismiss_intelligence_need(gap_id: str, req: DismissIntelligenceNeedRequest):
    """調査不要と判断した場合の例外的な遷移（Reportとの紐付けを要求しない）。"""
    return gap_service.dismiss_intelligence_need(
        _client, gap_id, dismissed_by=req.dismissed_by, reason=req.reason, actor_id=req.actor_id)
