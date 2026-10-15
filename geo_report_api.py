"""地政学インテリジェンス基盤 Phase4: Internal Report Upload APIルーター。

既存/api/geo/documents/upload（api_server.py）と同じUploadFile+Formパターンを踏襲する。
既存api_server.pyの変更は「このモジュールのimportとinclude_routerの2行追加」のみ
（既存geo_expert_review_api.py分離時と同じ方針）。
"""
import json
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402
from ai_client import make_openai_client  # noqa: E402

import geo_ios_identity
import geo_knowledge_access_policy
import geo_report_service

_config = load_config()
_client = SupabaseClient(_config)
_azure_client, _model = make_openai_client(_config)

geo_report_router = APIRouter(prefix="/api/geo/reports")


def _requester_context(identity: geo_ios_identity.IosIdentity) -> dict:
    """Phase 2E: 認証済みIosIdentity（未ログインならauthenticated=False）から
    geo_knowledge_access_policy.build_requester_contextを組み立てる。department/user_idは
    クライアント自己申告ではなく、必ずこのidentity（server-side解決値）由来のみを使う。"""
    return geo_knowledge_access_policy.build_requester_context(
        user_id=identity.identity_id if identity.authenticated else None,
        department=identity.department if identity.authenticated else None,
        is_authenticated=identity.authenticated,
    )


@geo_report_router.post("")
async def upload_report(
    file: UploadFile = File(...),
    title: str = Form(""),
    author: str = Form(""),
    owner_department: str = Form(""),
    report_type: str = Form(""),
    country_region: str = Form("[]"),
    topics: str = Form("[]"),
    confidentiality: str = Form("internal"),
    report_kind: str = Form("internal_report"),
    as_of: str = Form(""),
    theme_id: str = Form(""),
    linked_gap_ids: str = Form("[]"),
    actor_id: str = Form("analyst"),
    # Phase 2D（レポート登録）追加項目。省略時は従来通り（Upload Reportダイアログ等の
    # 既存呼び出し元は変更不要）。
    key_actors: str = Form("[]"),
    keywords: str = Form("[]"),
    summary: str = Form(""),
    business_context: str = Form(""),
    registration_source: str = Form(""),
    # Phase 2E（Confidentiality & Retrieval Access Control）追加項目。UI Labelの1つ
    # （DEPARTMENT_ONLY/SHD_ONLY/GROUP_SHAREABLE）。/geo/report-registrationは必須で送る。
    # 旧Upload Reportダイアログ（geo.knowledge-search.tsx）は当面未送信のままでよく、その場合
    # DB既定値（human_visibility='custom'+allowed_org_units=[]、fail-closed）のまま登録される。
    confidentiality_label: str = Form(""),
):
    if confidentiality_label and confidentiality_label not in geo_knowledge_access_policy.UI_LABELS:
        raise HTTPException(
            status_code=400,
            detail=f"未対応のconfidentiality_labelです: {confidentiality_label}"
                   f"（許可: {geo_knowledge_access_policy.UI_LABELS}）")
    try:
        country_region_list = json.loads(country_region) if country_region else []
        topics_list = json.loads(topics) if topics else []
        linked_gap_ids_list = json.loads(linked_gap_ids) if linked_gap_ids else []
        key_actors_list = json.loads(key_actors) if key_actors else []
        keywords_list = json.loads(keywords) if keywords else []
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=400,
            detail=f"country_region/topics/linked_gap_ids/key_actors/keywordsのJSON解析に失敗: {e}")

    raw_bytes = await file.read()
    try:
        result = geo_report_service.ingest_report(
            _client, filename=file.filename or "report", mime_type=file.content_type or "",
            raw_bytes=raw_bytes, title=title, author=author or None,
            owner_department=owner_department or None, report_type=report_type or None,
            country_region=country_region_list, topics=topics_list,
            confidentiality=confidentiality, report_kind=report_kind,
            as_of=as_of or None, theme_id=theme_id or None,
            linked_gap_ids=linked_gap_ids_list, actor_id=actor_id,
            key_actors=key_actors_list, keywords=keywords_list,
            summary=summary or None, business_context=business_context or None,
            registration_source=registration_source or None,
            confidentiality_label=confidentiality_label or None,
        )
    except geo_report_service.GeoReportError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "report": result["report"], "document": result["document"], "issue_ids": result["issue_ids"],
        "claims_created": result["claims_created"], "chunks_skipped": result["chunks_skipped"],
    }


@geo_report_router.get("")
def list_reports(gap_id: str | None = None,
                  identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.get_optional_ios_identity)):
    reports = geo_report_service.list_reports(
        _client, gap_id=gap_id, requester_context=_requester_context(identity))
    return {"reports": reports}


# 固定パス（/search, /analyze, 後日Phase3で/diff等）は、FastAPI/Starletteがルート定義順で
# マッチするため、必ず可変パス（/{report_id}）より前に定義する（"search"等の文字列が
# report_idとして誤マッチするのを防ぐ）。
@geo_report_router.get("/search")
def search_reports(q: str, include_demo: bool = False,
                    identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.get_optional_ios_identity)):
    reports = geo_report_service.search_reports(
        _client, q, include_demo=include_demo, requester_context=_requester_context(identity))
    return {"reports": reports}


@geo_report_router.post("/analyze")
async def analyze_report(file: UploadFile = File(...)):
    """Phase 2D §ファイル解析→AI Metadata Extraction。DBへは何も書き込まない
    （Register前のプレビュー専用。確定はPOST /api/geo/reportsで別途行う）。"""
    if not _azure_client:
        raise HTTPException(status_code=503, detail="Azure OpenAI / OpenAI のAPIキーが未設定です")
    raw_bytes = await file.read()
    try:
        return geo_report_service.analyze_report_upload(
            _azure_client, _model, filename=file.filename or "report",
            mime_type=file.content_type or "", raw_bytes=raw_bytes)
    except geo_report_service.GeoReportError as e:
        raise HTTPException(status_code=400, detail=str(e))


# Phase3: Report Versioning。/diffも固定パスのため/{report_id}より前に定義する。
@geo_report_router.get("/diff")
def diff_report_versions(from_report_id: str, to_report_id: str):
    try:
        return geo_report_service.diff_report_versions(_client, from_report_id, to_report_id)
    except geo_report_service.GeoReportError as e:
        raise HTTPException(status_code=400, detail=str(e))


@geo_report_router.get("/{report_id}")
def get_report(report_id: str,
               identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.get_optional_ios_identity)):
    report = geo_report_service.get_report_for_caller(
        _client, report_id, requester_context=_requester_context(identity))
    if not report:
        raise HTTPException(status_code=404, detail="report_idが見つかりません")
    return report


@geo_report_router.delete("/{report_id}")
def delete_report(report_id: str, actor_id: str = "analyst"):
    try:
        return geo_report_service.delete_report(_client, report_id=report_id, actor_id=actor_id)
    except geo_report_service.GeoReportError as e:
        raise HTTPException(status_code=400, detail=str(e))


@geo_report_router.get("/{report_id}/download")
def download_report(
        report_id: str,
        identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.get_optional_ios_identity)):
    report = geo_report_service.get_report_for_caller(
        _client, report_id, requester_context=_requester_context(identity))
    if not report or not report.get("document"):
        raise HTTPException(status_code=404, detail="report_idが見つかりません")
    document = report["document"]
    storage_path = document.get("storage_path")
    if not storage_path or not Path(storage_path).exists():
        raise HTTPException(status_code=404, detail="原本ファイルが見つかりません")
    filename = (document.get("title") or "report") + Path(storage_path).suffix
    return FileResponse(storage_path, media_type=document.get("mime_type") or "application/octet-stream",
                         filename=filename)


@geo_report_router.get("/{report_id}/versions")
def list_report_versions(
        report_id: str,
        identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.get_optional_ios_identity)):
    report = geo_report_service.get_report_for_caller(
        _client, report_id, requester_context=_requester_context(identity))
    if not report:
        raise HTTPException(status_code=404, detail="report_idが見つかりません")
    return {"versions": geo_report_service.list_report_versions(_client, report["report_identity_id"])}


@geo_report_router.get("/{report_id}/latest")
def get_latest_report_version(
        report_id: str,
        identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.get_optional_ios_identity)):
    report = geo_report_service.get_report_for_caller(
        _client, report_id, requester_context=_requester_context(identity))
    if not report:
        raise HTTPException(status_code=404, detail="report_idが見つかりません")
    latest = geo_report_service.get_latest_report_version(_client, report["report_identity_id"])
    if not latest:
        raise HTTPException(status_code=404, detail="latest versionが見つかりません")
    return latest


@geo_report_router.post("/{report_id}/versions")
async def create_report_version(
    report_id: str,
    file: UploadFile = File(...),
    author: str = Form(""),
    owner_department: str = Form(""),
    report_type: str = Form(""),
    country_region: str = Form(""),
    topics: str = Form(""),
    confidentiality: str = Form(""),
    report_kind: str = Form(""),
    as_of: str = Form(""),
    actor_id: str = Form("analyst"),
):
    try:
        country_region_list = json.loads(country_region) if country_region else None
        topics_list = json.loads(topics) if topics else None
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"country_region/topicsのJSON解析に失敗: {e}")

    raw_bytes = await file.read()
    try:
        result = geo_report_service.create_report_version(
            _client, supersedes_report_id=report_id, filename=file.filename or "report",
            mime_type=file.content_type or "", raw_bytes=raw_bytes,
            author=author or None, owner_department=owner_department or None,
            report_type=report_type or None, country_region=country_region_list, topics=topics_list,
            confidentiality=confidentiality or None, report_kind=report_kind or None,
            as_of=as_of or None, actor_id=actor_id,
        )
    except geo_report_service.ReportVersionConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except geo_report_service.ReportVersionNoChangeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except geo_report_service.GeoReportError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "report": result["report"], "document": result["document"],
        "claims_created": result["claims_created"], "chunks_skipped": result["chunks_skipped"],
    }


# 地政学アプリClooseout Phase5: Phase3(Versioning)とPhase4(Report Review Loop)の接続。
# Report Detailの「レビュー版登録/Review開始」から呼ばれる。
@geo_report_router.post("/{report_id}/review")
async def start_report_review(
    report_id: str,
    file: UploadFile = File(...),
    reviewer_id: str = Form(...),
    actor_id: str = Form("analyst"),
):
    if not _azure_client:
        raise HTTPException(status_code=503, detail="Azure OpenAI / OpenAI のAPIキーが未設定です")
    raw_bytes = await file.read()
    try:
        result = geo_report_service.start_report_review_and_version(
            _client, _azure_client, _model, report_id=report_id,
            reviewed_filename=file.filename or "reviewed_report",
            reviewed_mime_type=file.content_type or "", reviewed_raw_bytes=raw_bytes,
            reviewer_id=reviewer_id, actor_id=actor_id,
        )
    except geo_report_service.ReportVersionConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except geo_report_service.ReportVersionNoChangeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except (geo_report_service.GeoReportError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "report": result["version"]["report"], "document": result["version"]["document"],
        "review_session": result["review_session"], "changes": result["extraction"]["changes"],
    }
