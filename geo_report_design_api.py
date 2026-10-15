"""Report Studio「Design」領域 APIルーター（/api/geo/report-studio/projects/{project_id}/...）。

既存api_server.pyの変更は「このモジュールのimportとinclude_routerの2行追加」のみ
（geo_report_studio_api.py分離時と同じ方針）。

geo_report_project_assets_service（Narrative Master/Report Template Asset・Design
State・Atomic Publish）・geo_report_run_provisioning_service（Run作成時のSnapshot
固定）を、UIから呼べるHTTPエンドポイントとして薄く公開する。**ビジネスロジックは
ここで再実装しない**——バリデーション・Diff・Publish・Pinの実処理は全てservice層の
関数を呼ぶだけにする。

Note: `POST /projects/{project_id}/runs/design-pinned` は暫定エンドポイント。
Phase D（Current Pipeline Preprocessingの書き直し）が完了するまでの間、CSV
前処理を含まずDesign/Prompt/Config/ContinuityのPinのみを行う（geo_report_studio_api.py
の既存`POST /projects/{project_id}/runs`——CSVアップロード＋旧Shell Parity前処理
——とは別の、まだ統合されていない経路）。Phase D完了後、両者を1つの
Run作成フローへ統合する。

Design Chat（`geo_report_design_chat_service`）もここで公開する。Narrative
Master/Report Template生成・Template Extractionの結果はいずれも保存はせず返すのみ
——UIが内容を確認してから明示的にsave/upload系エンドポイントを呼ぶ2段階にする
（AIの生成物を無条件に永続化しない）。
"""

import json

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from starlette.responses import PlainTextResponse

from ai_client import make_openai_client  # noqa: E402
from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

import geo_report_artifact_storage_service as artifact_storage
import geo_report_design_chat_service as design_chat
import geo_report_project_assets_service as assets
import geo_report_run_provisioning_service as provisioning
import geo_report_run_upload_cache_service as upload_cache
import geo_report_studio_service as studio

_config = load_config()
_client = SupabaseClient(_config)
_azure_client, _model = make_openai_client(_config)

_SCHEMA_OK, _SCHEMA_ERROR = studio.check_schema(_client)
if not _SCHEMA_OK:
    print(f"[geo_report_design_api] WARNING: Report Studio Design disabled — {_SCHEMA_ERROR}", flush=True)


def _require_schema_ok():
    if not _SCHEMA_OK:
        raise HTTPException(status_code=503, detail=_SCHEMA_ERROR)


geo_report_design_router = APIRouter(
    prefix="/api/geo/report-studio", dependencies=[Depends(_require_schema_ok)])

_ASSET_TYPES = ("narrative_master", "report_template")


def _require_project(project_id: str) -> dict:
    project = studio.get_project(_client, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project_idが見つかりません")
    return project


def _require_asset_type(asset_type: str) -> str:
    if asset_type not in _ASSET_TYPES:
        raise HTTPException(status_code=400, detail=f"asset_typeは{_ASSET_TYPES}のいずれかである必要があります")
    return asset_type


# ---------------------------------------------------------------------------
# Design State（Report Design / Signals / Queries）
# ---------------------------------------------------------------------------

@geo_report_design_router.get("/projects/{project_id}/design/state")
def get_design_state(project_id: str):
    _require_project(project_id)
    return {"design_state": assets.get_design_state(_client, project_id)}


class UpdateDesignStateRequest(BaseModel):
    report_design: dict | None = None
    signals: list | None = None
    queries: list | None = None
    updated_by: str | None = None


@geo_report_design_router.put("/projects/{project_id}/design/state")
def update_design_state(project_id: str, req: UpdateDesignStateRequest):
    _require_project(project_id)
    state = assets.save_design_state(
        _client, project_id, report_design=req.report_design, signals=req.signals,
        queries=req.queries, updated_by=req.updated_by,
    )
    return {"design_state": state}


@geo_report_design_router.get("/projects/{project_id}/design/queries/export")
def export_bs_query_plan(project_id: str):
    """Design StateのqueriesをそのままCurrent契約のbs_query_plan.csvへserializeする
    （変換ロジック無し、Babel Street側への投入用）。"""
    _require_project(project_id)
    design_state = assets.get_design_state(_client, project_id)
    csv_text = assets.build_bs_query_plan_csv(design_state.get("queries") or [])
    return PlainTextResponse(
        csv_text, media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="bs_query_plan.csv"'},
    )


@geo_report_design_router.get("/projects/{project_id}/design/report-design/download")
def download_report_design(project_id: str):
    """確定済み（FIX済み）のReport Design本文をJSONダウンロードする
    （2026-09-16、チャット中心UXの「FIX後のダウンロードリンク」用）。"""
    _require_project(project_id)
    design_state = assets.get_design_state(_client, project_id)
    text = json.dumps(design_state.get("report_design") or {}, ensure_ascii=False, indent=2)
    return PlainTextResponse(
        text, media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="report_design.json"'},
    )


@geo_report_design_router.get("/projects/{project_id}/design/signals/download")
def download_signals(project_id: str):
    """確定済み（FIX済み）のSignal一覧をJSONダウンロードする（2026-09-16）。"""
    _require_project(project_id)
    design_state = assets.get_design_state(_client, project_id)
    text = json.dumps(design_state.get("signals") or [], ensure_ascii=False, indent=2)
    return PlainTextResponse(
        text, media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="signals.json"'},
    )


# ---------------------------------------------------------------------------
# Project Assets（Narrative Master / Report Template 共通: draft/published/history）
# ---------------------------------------------------------------------------

@geo_report_design_router.get("/projects/{project_id}/assets/{asset_type}/draft")
def get_asset_draft(project_id: str, asset_type: str):
    _require_project(project_id)
    _require_asset_type(asset_type)
    asset = assets.get_draft(_client, project_id, asset_type)
    if not asset:
        raise HTTPException(status_code=404, detail=f"{asset_type}のDraftがありません")
    return {"asset": asset}


@geo_report_design_router.get("/projects/{project_id}/assets/{asset_type}/published")
def get_asset_published(project_id: str, asset_type: str):
    _require_project(project_id)
    _require_asset_type(asset_type)
    asset = assets.get_published(_client, project_id, asset_type)
    if not asset:
        raise HTTPException(status_code=404, detail=f"{asset_type}のPublished版がありません")
    return {"asset": asset}


@geo_report_design_router.get("/projects/{project_id}/assets/{asset_type}/history")
def get_asset_history(project_id: str, asset_type: str):
    _require_project(project_id)
    _require_asset_type(asset_type)
    return {"history": assets.list_history(_client, project_id, asset_type)}


# ---------------------------------------------------------------------------
# Narrative Master: validate / upload / download / diff
# ---------------------------------------------------------------------------

class ValidateNarrativeMasterRequest(BaseModel):
    rows: list


@geo_report_design_router.post("/projects/{project_id}/assets/narrative-master/validate")
def validate_narrative_master(project_id: str, req: ValidateNarrativeMasterRequest):
    _require_project(project_id)
    errors = assets.validate_narrative_master(req.rows)
    return {"valid": not errors, "errors": errors}


@geo_report_design_router.post("/projects/{project_id}/assets/narrative-master/upload")
async def upload_narrative_master_asset(project_id: str, file: UploadFile = File(...)):
    _require_project(project_id)
    raw_bytes = await file.read()
    try:
        csv_text = raw_bytes.decode("utf-8-sig")
        asset = assets.upload_narrative_master_csv(_client, project_id, csv_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"asset": asset}


@geo_report_design_router.post("/projects/{project_id}/assets/narrative-master/save")
def save_narrative_master_asset(project_id: str, req: ValidateNarrativeMasterRequest):
    """Design Chatの「Signal別に生成」（AIで生成）が返すrowsをDraftへ保存する唯一の入口。
    以前はgenerate結果を保存する経路が無く、生成してもDraftへ反映されずPublishできない
    というE2E completion blockerになっていた（Phase 5で発覚）。"""
    _require_project(project_id)
    try:
        asset = assets.save_narrative_master_draft(_client, project_id, req.rows, source="chat_ai")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"asset": asset}


@geo_report_design_router.get("/projects/{project_id}/assets/narrative-master/download")
def download_narrative_master_asset(project_id: str, status: str = Query("draft", pattern="^(draft|published)$")):
    _require_project(project_id)
    try:
        csv_text = assets.download_narrative_master_csv(_client, project_id, status=status)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return PlainTextResponse(
        csv_text, media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="signal_narrative_master_{status}.csv"'},
    )


@geo_report_design_router.get("/projects/{project_id}/assets/narrative-master/diff")
def diff_narrative_master_asset(project_id: str):
    """Draft と Published の差分（Publishした場合に何が変わるか、Publish前のUI確認用）。"""
    _require_project(project_id)
    draft = assets.get_draft(_client, project_id, "narrative_master")
    published = assets.get_published(_client, project_id, "narrative_master")
    draft_rows = (draft or {}).get("content", {}).get("rows", [])
    published_rows = (published or {}).get("content", {}).get("rows", [])
    return {"diff": assets.diff_narrative_master(published_rows, draft_rows)}


# ---------------------------------------------------------------------------
# Report Template: validate / save
# ---------------------------------------------------------------------------

class ReportTemplateContentRequest(BaseModel):
    content: dict


@geo_report_design_router.post("/projects/{project_id}/assets/report-template/validate")
def validate_report_template_asset(project_id: str, req: ReportTemplateContentRequest):
    _require_project(project_id)
    errors = assets.validate_report_template(req.content)
    return {"valid": not errors, "errors": errors}


@geo_report_design_router.post("/projects/{project_id}/assets/report-template/save")
def save_report_template_asset(project_id: str, req: ReportTemplateContentRequest):
    _require_project(project_id)
    try:
        asset = assets.save_report_template_draft(_client, project_id, req.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"asset": asset}


@geo_report_design_router.get("/projects/{project_id}/assets/report-template/download")
def download_report_template_asset(project_id: str, status: str = Query("draft", pattern="^(draft|published)$")):
    """Report Templateの構造定義（style_css/shell_html/sections）をJSONダウンロードする
    （2026-09-16、チャット中心UXの「FIX後のダウンロードリンク」用、Narrative Master
    downloadと対になるパターン）。"""
    _require_project(project_id)
    asset = assets.get_draft(_client, project_id, "report_template") if status == "draft" \
        else assets.get_published(_client, project_id, "report_template")
    if not asset:
        raise HTTPException(status_code=404, detail=f"Report Template（{status}）が見つかりません。")
    text = json.dumps(asset["content"], ensure_ascii=False, indent=2)
    return PlainTextResponse(
        text, media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="report_template_{status}.json"'},
    )


@geo_report_design_router.get("/projects/{project_id}/design/report-template/preview")
def preview_report_template_draft(project_id: str, status: str = Query("draft", pattern="^(draft|published)$")):
    """保存済み（Draft/Published）のReport Templateをプレビューレンダリングして返す
    （2026-09-16、チャットで生成したテンプレートを「別タブで開けるリンク」として
    案内するためのGETエンドポイント。POST版のreport-template/previewはステートレスで
    任意contentをレンダリングするのに対し、こちらは保存済みDraftを参照するため
    静的なURLとしてチャット内に埋め込める）。"""
    _require_project(project_id)
    asset = assets.get_draft(_client, project_id, "report_template") if status == "draft" \
        else assets.get_published(_client, project_id, "report_template")
    if not asset:
        raise HTTPException(status_code=404, detail=f"Report Template（{status}）が見つかりません。")
    html = design_chat.render_template_design_preview(asset["content"])
    return PlainTextResponse(html, media_type="text/html")


# ---------------------------------------------------------------------------
# Design Publish（Atomic Publish RPCラッパー呼び出し）
# ---------------------------------------------------------------------------

class PublishDesignRequest(BaseModel):
    actor_id: str = "analyst"


@geo_report_design_router.post("/projects/{project_id}/design/publish")
def publish_design(project_id: str, req: PublishDesignRequest):
    _require_project(project_id)
    try:
        result = assets.publish_design(_client, project_id, req.actor_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # assets.publish_designはRPC結果本体にreport_config_warningsを混ぜて返すため、
    # ここでトップレベルの別キーへ分離する（design_version自体は素のDesign Version
    # レコードのまま返したい、2026-09-16）。
    report_config_warnings = result.pop("report_config_warnings", [])
    return {"design_version": result, "report_config_warnings": report_config_warnings}


@geo_report_design_router.get("/projects/{project_id}/design/versions")
def list_design_versions(project_id: str):
    _require_project(project_id)
    rows = _client.select("geo_report_design_versions", {
        "select": "*", "project_id": f"eq.{project_id}", "order": "version.desc",
    })
    return {"versions": rows}


@geo_report_design_router.delete("/projects/{project_id}/design")
def reset_design(project_id: str):
    """このprojectのDesign（Draft State・Narrative Master/Report Template Asset・
    Published Version履歴・Design Chat会話ログ）を全て削除し、未設計状態へ戻す
    （2026-09-14、ユーザー要望: 「デザインを消したり、新規始める方法がない」）。
    Runは削除しない——Pinned Runが残っている場合は409で拒否し、対象Run名を返す。"""
    _require_project(project_id)
    try:
        assets.reset_design(_client, project_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"reset": True}


# ---------------------------------------------------------------------------
# Create Run（暫定: Design/Prompt/Config/ContinuityのPinのみ、CSV前処理は含まない）
# ---------------------------------------------------------------------------

class CreateDesignPinnedRunRequest(BaseModel):
    baseline_run_id: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    application_build_revision: str | None = None
    run_name: str | None = None


@geo_report_design_router.post("/projects/{project_id}/runs/design-pinned")
def create_design_pinned_run(project_id: str, req: CreateDesignPinnedRunRequest):
    _require_project(project_id)
    if req.baseline_run_id:
        baseline = studio.get_run(_client, req.baseline_run_id)
        if not baseline or baseline["project_id"] != project_id:
            raise HTTPException(status_code=400, detail="baseline_run_idがこのプロジェクトのrunと一致しません")
    try:
        run = provisioning.create_run_with_pinned_snapshot(
            _client, project_id, baseline_run_id=req.baseline_run_id,
            period_start=req.period_start, period_end=req.period_end,
            application_build_revision=req.application_build_revision,
            run_name=req.run_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"run": run}


class RenameRunRequest(BaseModel):
    run_name: str


@geo_report_design_router.patch("/projects/{project_id}/runs/{run_id}")
def rename_run(project_id: str, run_id: str, req: RenameRunRequest):
    """Runの表示名を変更する（2026-09-11、UUIDのままだと画面上でどのRunか
    判別できないというユーザー報告への対応）。"""
    _require_project(project_id)
    run = studio.get_run(_client, run_id)
    if not run or run["project_id"] != project_id:
        raise HTTPException(status_code=404, detail="runが見つかりません")
    name = req.run_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="run_nameを空にすることはできません")
    _client.update("geo_report_collection_runs", {"run_id": f"eq.{run_id}"}, {"run_name": name})
    return {"run": studio.get_run(_client, run_id)}


@geo_report_design_router.delete("/projects/{project_id}/runs/{run_id}")
def delete_run(project_id: str, run_id: str):
    """Runとその配下データ（Execution/バッチ/クラスタ/成果物等）を削除する
    （2026-09-11、ユーザー報告「削除ができない」への対応。子テーブルは全てon delete
    cascadeなので、このRun行の削除だけで配下データも一括で消える）。

    2026-09-17追加、ユーザー指摘「データは大きくなりやすいので、分析が終わったら
    消すなどしないとすぐストレージが一杯になるはず」への対応: DB側のon delete
    cascadeは`geo_report_run_artifacts`等のDB行を消すだけで、Supabase Storage側の
    実体（DBとは別システム）は対象外のため、従来はRunを削除してもStorage
    objectが永久に残り続けていた（`delete_artifact_storage_object`自体は存在した
    がどこからも呼ばれていなかった）。Run行を消す前に、Storage実体と、
    Preprocessの元CSVを一時キャッシュしていたローカルディレクトリ（geo_report_
    run_upload_cache_service参照）を先に削除する。"""
    _require_project(project_id)
    run = studio.get_run(_client, run_id)
    if not run or run["project_id"] != project_id:
        raise HTTPException(status_code=404, detail="runが見つかりません")
    artifact_storage.delete_all_run_artifacts_storage(_client, run_id)
    upload_cache.clear_run_upload_dir(run_id)
    _client.delete("geo_report_collection_runs", {"run_id": f"eq.{run_id}"})
    return {"deleted": True, "run_id": run_id}


## キャンセル要求API（POST .../runs/{run_id}/cancel）は、実行系エンドポイントと
## 同居させるため geo_report_current_pipeline_api.py 側に定義する（このファイルとは
## 同じURLプレフィックスを共有しているため、ここでは重複定義しない）。


# ---------------------------------------------------------------------------
# Design Chat
# ---------------------------------------------------------------------------

@geo_report_design_router.get("/projects/{project_id}/design/chat")
def get_design_chat_turns(project_id: str):
    _require_project(project_id)
    return {"turns": design_chat.list_turns(_client, project_id)}


class DesignChatMessageRequest(BaseModel):
    message: str


@geo_report_design_router.post("/projects/{project_id}/design/chat")
def post_design_chat_message(project_id: str, req: DesignChatMessageRequest):
    _require_project(project_id)
    try:
        result = design_chat.process_chat_turn(
            _client, _azure_client, _model, project_id, req.message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


@geo_report_design_router.post("/projects/{project_id}/design/fix")
def fix_design(project_id: str):
    """Design Chatの「FIX」ボタン用（2026-09-15）。チャットで提案された
    report_design/signals/queriesを確定し、確定Signal全件分のNarrative Masterを
    自動生成・保存する。"""
    _require_project(project_id)
    try:
        result = design_chat.fix_design(_client, _azure_client, _model, project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


@geo_report_design_router.post("/projects/{project_id}/design/chat/upload")
async def upload_design_chat_file(project_id: str, file: UploadFile = File(...)):
    """Design Chatへのファイル添付用（2026-09-16、ユーザー要望:「デザインパートは
    チャットUIだけで、ファイルアップできる形にしよう。それであれば、途中のものも
    そこから始められる」）。.csvはNarrative Master、.html/.htmはReport Template
    Extractionとして扱う。"""
    _require_project(project_id)
    raw_bytes = await file.read()
    result = design_chat.handle_chat_file_upload(
        _client, _azure_client, _model, project_id, file.filename or "upload", raw_bytes)
    return result


class GenerateNarrativeMasterRequest(BaseModel):
    signal_id: str


@geo_report_design_router.post("/projects/{project_id}/design/narrative-master/generate")
def generate_narrative_master(project_id: str, req: GenerateNarrativeMasterRequest):
    _require_project(project_id)
    try:
        rows = design_chat.generate_narrative_master_rows_for_signal(
            _client, _azure_client, _model, project_id, req.signal_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"rows": rows}


@geo_report_design_router.post("/projects/{project_id}/design/narrative-master/generate-all")
def generate_narrative_master_for_all_signals(project_id: str):
    """Narrative Masterカードの「全Signal一括生成」ボタン用（2026-09-14追加）。
    Design Stateに登録されている全Signal分をまとめて生成し、既存Draftへ自動保存する
    （Signal 1件ずつの手動生成やDesign Chatでの自然言語依頼を使わなくても、
    画面から直接一括生成できるようにする導線）。"""
    _require_project(project_id)
    try:
        result = design_chat.generate_narrative_master_for_all_signals(_client, _azure_client, _model, project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


@geo_report_design_router.post("/projects/{project_id}/design/report-template/generate")
def generate_report_template(project_id: str):
    _require_project(project_id)
    template = design_chat.generate_report_template(_client, _azure_client, _model, project_id)
    return {"template": template}


class ExtractReportTemplateRequest(BaseModel):
    final_report_html: str


@geo_report_design_router.post("/projects/{project_id}/design/report-template/extract")
def extract_report_template(project_id: str, req: ExtractReportTemplateRequest):
    _require_project(project_id)
    result = design_chat.extract_template_from_report(
        _client, _azure_client, _model, req.final_report_html)
    return result


class ReportTemplatePreviewRequest(BaseModel):
    content: dict


@geo_report_design_router.post("/projects/{project_id}/design/report-template/preview")
def preview_report_template(project_id: str, req: ReportTemplatePreviewRequest):
    """Report Templateの構成（保存前のものでよい）をプレビューHTMLとして返す
    （2026-09-15、ユーザー要望:「テンプレートもチャット上でHTMLリンクをだして、
    内容やり取りしたあとに保存する」）。ステートレス——渡されたcontentをそのまま
    レンダリングするだけで、DBへは何も保存・参照しない。"""
    _require_project(project_id)
    html = design_chat.render_template_design_preview(req.content)
    return PlainTextResponse(html, media_type="text/html")
