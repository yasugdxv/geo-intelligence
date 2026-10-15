"""Geopolitical Intelligence OS: Report Review V2 API。

POST /{analysis_session_id}/run はExpert AI呼び出しをBackgroundTasksで実行し、
即座にstatus=pendingの行を返す（既存geo_ios_report_draft_api.pyと同じパターン）。
Frontendはポーリングして完了を検知する。

既存Review Mode（review-intake variant）の「AI Review」ボタンは、Design/Situationが
存在しないreview-modeセッションに対して既存run_draft_ai_review（_fetch_design_and_situation
がDesign必須のため400）を呼んでおり、実際には常に失敗していた（調査で確認）。本APIは
その置き換えとして、Draft本文から実際に地政学的な「読み方」を再検討する。
"""
import threading

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from ai_client import make_openai_client  # noqa: E402
from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

import geo_ios_report_draft_service as draft_service
import geo_report_review_v2_service as review_v2_service

_config = load_config()
_client = SupabaseClient(_config)
_azure_client, _model = make_openai_client(_config)

geo_report_review_v2_router = APIRouter(prefix="/api/geo/report-review-v2")


# このプロセス内で実行中のレビュー（analysis_session_id）。ボタンを何度も押されても、
# 同じセッションのレビューを重ねて走らせない（結果の保存順位が衝突して失敗するため）。
_RUNNING_SESSIONS: set = set()
_RUNNING_LOCK = threading.Lock()


def _try_mark_running(analysis_session_id: str) -> bool:
    with _RUNNING_LOCK:
        if analysis_session_id in _RUNNING_SESSIONS:
            return False
        _RUNNING_SESSIONS.add(analysis_session_id)
        return True


def _unmark_running(analysis_session_id: str) -> None:
    with _RUNNING_LOCK:
        _RUNNING_SESSIONS.discard(analysis_session_id)


def _require_azure_client():
    if not _azure_client:
        raise HTTPException(status_code=503, detail="Azure OpenAI / OpenAI のAPIキーが未設定です")


def _draft_text_for_review(draft: dict) -> str:
    """Draftのsections（見出し＋本文）からReview V2抽出用の全文テキストを組み立てる。
    アップロード時の生テキストはgeo_ios_report_draftsに保持されないため、既に構造化
    済みのsectionsを連結する（run_document_importによる再構成だが、内容自体は不変）。"""
    sections = draft.get("sections") or []
    parts = []
    for section in sections:
        heading = section.get("heading") or ""
        body = section.get("body") or ""
        parts.append(f"## {heading}\n{body}" if heading else body)
    return "\n\n".join(p for p in parts if p)


def _run_report_reading_review_in_background(analysis_session_id: str, report_text: str,
                                              requested_by: str):
    try:
        review_v2_service.execute_run(
            _client, _azure_client, _model, analysis_session_id=analysis_session_id,
            report_text=report_text, actor_id=requested_by)
    except Exception:
        pass  # execute_run自体がstatus=failedを記録済み
    finally:
        _unmark_running(analysis_session_id)


class RunReportReviewV2Request(BaseModel):
    report_draft_id: str
    requested_by: str


@geo_report_review_v2_router.post("/{analysis_session_id}/run")
def run_report_review_v2(analysis_session_id: str, req: RunReportReviewV2Request,
                          background_tasks: BackgroundTasks):
    _require_azure_client()
    draft = draft_service.get_draft(_client, req.report_draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="report_draft_idが見つかりません")
    if draft.get("status") != "completed":
        raise HTTPException(status_code=400, detail="Draftの取込・生成が完了していません")

    existing = review_v2_service.start_run(
        _client, analysis_session_id, report_draft_id=req.report_draft_id,
        created_by=req.requested_by)
    if existing.get("status") == "completed":
        return existing

    report_text = _draft_text_for_review(draft)
    if not report_text.strip():
        raise HTTPException(status_code=400, detail="Draft本文が空のためレビューできません")

    # すでに実行中なら重ねて開始せず、現在の状態（実行中）をそのまま返す
    if not _try_mark_running(analysis_session_id):
        return existing
    # 前回failedからの再実行: 「実行中」に戻して、古いエラーが出続けないようにする
    try:
        existing = review_v2_service.reset_failed_run_for_retry(_client, analysis_session_id) or existing
        background_tasks.add_task(
            _run_report_reading_review_in_background, analysis_session_id, report_text,
            req.requested_by)
    except Exception:
        _unmark_running(analysis_session_id)  # 開始できなかった場合に「実行中」の印を残さない
        raise
    return existing


@geo_report_review_v2_router.get("/{analysis_session_id}")
def get_report_review_v2(analysis_session_id: str):
    run = review_v2_service.get_run(_client, analysis_session_id)
    if not run:
        raise HTTPException(status_code=404, detail="まだReview V2が実行されていません")
    return run


@geo_report_review_v2_router.get("/{analysis_session_id}/expert-reading-presentation")
def get_expert_reading_presentation(analysis_session_id: str):
    """Expert AI Reading側を既存Reading Presentation v2でそのまま表示するための
    GeoIosSituationPresentation。Report Reading側は専用の軽量表示をFrontend側で行う
    （ユーザー指示、既存7セクション構造へ無理に合わせない）。"""
    presentation = review_v2_service.get_expert_reading_presentation(_client, analysis_session_id)
    if presentation is None:
        raise HTTPException(status_code=404, detail="Expert AI Readingがまだ生成されていません")
    return presentation


class SaveHumanExpertDispositionRequest(BaseModel):
    source: str
    finding_id: str
    disposition: str
    reason: str = ""
    modification: str | None = None
    reviewed_by: str


@geo_report_review_v2_router.post("/{analysis_session_id}/dispositions")
def save_human_expert_disposition(analysis_session_id: str, req: SaveHumanExpertDispositionRequest):
    """Phase R2: Reading Review/Content Reviewの各findingに対するAgree/Modify/Reject
    判断を記録する。AI Reviewの結果をそのまま正解扱いしない（ユーザー指示）。"""
    try:
        return review_v2_service.save_human_expert_disposition(
            _client, analysis_session_id, source=req.source, finding_id=req.finding_id,
            disposition=req.disposition, reason=req.reason, modification=req.modification,
            reviewed_by=req.reviewed_by)
    except review_v2_service.HumanExpertReviewError as e:
        raise HTTPException(status_code=400, detail=str(e))
