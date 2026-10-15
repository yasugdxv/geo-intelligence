"""Report Studio APIルーター（/api/geo/report-studio/*）。

既存api_server.pyの変更は「このモジュールのimportとinclude_routerの2行追加」のみ
（既存geo_expert_review_api.py/geo_intelligence_gap_api.py分離時と同じ方針）。

geo_report_processing_service（Shell Parity前処理エンジン）・
geo_report_studio_service（DB永続化）・geo_report_classification_service（Prompt1相当）・
geo_report_synthesis_service（Prompt2相当+Expert AI統合+Expert Review）を、
UIから呼べるHTTPエンドポイントとして薄く公開する。バックグラウンドジョブ基盤が
既存プロジェクトに無いため、Classification/Synthesisは同期呼び出し（1クラスタ/1
Narrativeずつ）とし、大量クラスタの一括処理はUI側から少数ずつ繰り返し呼ぶ設計にする
（既存プロジェクトの「新規インフラを作らない」方針を踏襲）。

**Schema Version Check**: サーバー起動時（本モジュールのimport時）に一度だけ
geo_report_studio_service.check_schema()でDBスキーマが最新migration
（sql/2026-09-02_geo_report_studio_*.sql）に追いついているか確認する。未適用の場合は
起動ログへ警告を出したうえで、本ルーター配下の全エンドポイントが503を返す
（DBスキーマとアプリコードの世代がズレた状態を黙って許容しない。他機能
（sustainability/competitor監視等）は道連れにせず、Report Studioだけを明示的に止める）。
"""
import json
import os
import tempfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402
from ai_client import make_openai_client  # noqa: E402

import geo_report_classification_service as classify_service
import geo_report_monitoring_service as monitoring_service
import geo_report_processing_service as processing
import geo_report_studio_service as studio
import geo_report_synthesis_service as synthesis_service

_config = load_config()
_client = SupabaseClient(_config)
_azure_client, _model = make_openai_client(_config)

_SCHEMA_OK, _SCHEMA_ERROR = studio.check_schema(_client)
if not _SCHEMA_OK:
    print(f"[geo_report_studio_api] WARNING: Report Studio disabled — {_SCHEMA_ERROR}", flush=True)


def _require_schema_ok():
    if not _SCHEMA_OK:
        raise HTTPException(status_code=503, detail=_SCHEMA_ERROR)


geo_report_studio_router = APIRouter(
    prefix="/api/geo/report-studio", dependencies=[Depends(_require_schema_ok)])


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def _project_summary(project: dict) -> dict:
    latest_run = studio.get_latest_run(_client, project["project_id"])
    review_required_count = (
        studio.count_review_required_clusters(_client, latest_run["run_id"]) if latest_run else 0
    )
    return {"project": project, "latest_run": latest_run, "review_required_count": review_required_count}


@geo_report_studio_router.get("/projects")
def list_projects():
    projects = studio.list_projects(_client)
    return {"projects": [_project_summary(p) for p in projects]}


class CreateProjectRequest(BaseModel):
    project_code: str
    project_title: str
    report_language: str = "ja"


@geo_report_studio_router.post("/projects")
def create_project(req: CreateProjectRequest):
    existing = studio.get_project_by_code(_client, req.project_code)
    if existing:
        raise HTTPException(status_code=400, detail=f"project_code '{req.project_code}' は既に使用されています")
    project = studio.get_or_create_project(
        _client, req.project_code, req.project_title, report_language=req.report_language)
    return {"project": project}


@geo_report_studio_router.get("/projects/{project_id}")
def get_project(project_id: str):
    project = studio.get_project(_client, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project_idが見つかりません")
    return _project_summary(project)


@geo_report_studio_router.delete("/projects/{project_id}")
def delete_project(project_id: str):
    """プロジェクトを完全に削除する（2026-09-15、ユーザー要望:「過去のプロジェクトは
    削除できるようにしよう」）。関連するRun/Design/Asset/Executionは全てDB側の
    on delete cascadeで連鎖削除される。取り消せないため、フロント側で確認ダイアログを
    経由してから呼ぶこと。"""
    try:
        studio.delete_project(_client, project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"deleted": True}


@geo_report_studio_router.get("/projects/{project_id}/overview")
def get_project_overview(project_id: str):
    project = studio.get_project(_client, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project_idが見つかりません")
    latest_run = studio.get_latest_run(_client, project_id)
    syntheses = studio.list_latest_syntheses_for_project(_client, project_id)
    narrative_rows = studio.load_narrative_master_rows(_client, project_id)
    review_required_count = (
        studio.count_review_required_clusters(_client, latest_run["run_id"]) if latest_run else 0
    )
    return {
        "project": project,
        "latest_run": latest_run,
        "narrative_count": len(narrative_rows),
        "review_required_count": review_required_count,
        "syntheses": syntheses,
    }


@geo_report_studio_router.get("/projects/{project_id}/narrative-master")
def get_narrative_master(project_id: str):
    rows = studio.load_narrative_master_rows(_client, project_id)
    return {"rows": rows}


@geo_report_studio_router.post("/projects/{project_id}/narrative-master")
async def upload_narrative_master(project_id: str, file: UploadFile = File(...)):
    project = studio.get_project(_client, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project_idが見つかりません")
    raw_bytes = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        tmp.write(raw_bytes)
        tmp_path = tmp.name
    try:
        master_rows = processing.load_narrative_master_csv(tmp_path)
        count = studio.replace_narrative_master(_client, project_id, master_rows)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Narrative Masterの読み込みに失敗: {e}")
    finally:
        os.unlink(tmp_path)
    return {"rows_imported": count}


@geo_report_studio_router.get("/projects/{project_id}/runs")
def list_runs(project_id: str, limit: int = 20):
    return {"runs": studio.list_runs_for_project(_client, project_id, limit=limit)}


@geo_report_studio_router.get("/projects/{project_id}/assessment-timeline")
def get_assessment_timeline(project_id: str):
    """Historyタブ用: プロジェクト内の全Synthesis履歴（narrative_id別の時系列比較に使う）。"""
    return {"syntheses": studio.list_all_syntheses_for_project(_client, project_id)}


@geo_report_studio_router.post("/projects/{project_id}/runs")
async def create_run(
    project_id: str,
    files: list[UploadFile] = File(...),
    start_date: str = Form(""),
    end_date: str = Form(""),
    baseline_run_id: str = Form(""),
):
    project = studio.get_project(_client, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project_idが見つかりません")
    master_rows = studio.load_narrative_master_rows(_client, project_id)
    if not master_rows:
        raise HTTPException(status_code=400, detail="先にNarrative Masterを投入してください")

    baseline_run = None
    if baseline_run_id:
        baseline_run = studio.get_run(_client, baseline_run_id)
        if not baseline_run or baseline_run["project_id"] != project_id:
            raise HTTPException(status_code=400, detail="baseline_run_idがこのプロジェクトのrunと一致しません")

    with tempfile.TemporaryDirectory() as tmp_dir:
        saved_paths = []
        for f in files:
            raw_bytes = await f.read()
            path = os.path.join(tmp_dir, f.filename or "input.csv")
            with open(path, "wb") as fh:
                fh.write(raw_bytes)
            saved_paths.append(path)
        try:
            result = processing.run_preprocess(
                saved_paths, master_rows, start_date=start_date or None, end_date=end_date or None)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        rules_by_signal, _, _ = processing.build_master_rules(master_rows)
        run_id = studio.persist_collection_run(
            _client, project_id, result, rules_by_signal,
            period_start=start_date or None, period_end=end_date or None,
            baseline_run_id=baseline_run_id or None,
        )

    reuse_summary = None
    if baseline_run:
        reuse_summary = studio.apply_incremental_reuse(_client, run_id, baseline_run_id)

    return {"run_id": run_id, "reuse_summary": reuse_summary}


@geo_report_studio_router.get("/runs/{run_id}")
def get_run(run_id: str):
    run = studio.get_run(_client, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run_idが見つかりません")
    return {"run": run, "review_required_count": studio.count_review_required_clusters(_client, run_id)}


# ---------------------------------------------------------------------------
# Clusters / Classification
# ---------------------------------------------------------------------------

@geo_report_studio_router.get("/runs/{run_id}/clusters")
def list_clusters(run_id: str, narrative_id: str | None = None):
    clusters = studio.load_clusters_for_run(_client, run_id)
    if narrative_id:
        clusters = [c for c in clusters if c["narrative_id"] == narrative_id]
    return {"clusters": clusters}


@geo_report_studio_router.get("/runs/{run_id}/representatives")
def list_all_representatives(run_id: str):
    return {"representatives": studio.load_all_representatives_for_run(_client, run_id)}


@geo_report_studio_router.get("/runs/{run_id}/clusters/{cluster_id}")
def get_cluster(run_id: str, cluster_id: str):
    cluster = studio.get_cluster(_client, run_id, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="cluster_idが見つかりません")
    representatives = studio.load_representatives_for_cluster(_client, run_id, cluster_id)
    return {"cluster": cluster, "representatives": representatives}


@geo_report_studio_router.post("/runs/{run_id}/clusters/{cluster_id}/classify")
def classify_cluster(run_id: str, cluster_id: str):
    run = studio.get_run(_client, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run_idが見つかりません")
    cluster = studio.get_cluster(_client, run_id, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="cluster_idが見つかりません")

    narrative_configs = studio.load_narrative_configs_by_key(_client, run["project_id"])
    narrative_row = narrative_configs.get((cluster["signal_id"], cluster["narrative_id"]))
    if not narrative_row:
        raise HTTPException(status_code=400, detail="このクラスタに対応するNarrative Master行が見つかりません")

    representatives = studio.load_representatives_for_cluster(_client, run_id, cluster_id)
    result = classify_service.classify_cluster(_azure_client, _model, narrative_row, cluster, representatives)
    checks = classify_service.run_classification_validity_checks(result["data"], representatives, narrative_row)
    status = classify_service.classification_validity_status(checks)
    studio.persist_cluster_classification(_client, cluster["id"], result["data"], checks, status)
    return {"classification": result["data"], "validity_checks": checks, "validity_status": status,
            "usage": result.get("usage")}


class ClassifyNextRequest(BaseModel):
    narrative_id: str | None = None
    max_clusters: int = 5


@geo_report_studio_router.post("/runs/{run_id}/classify-next")
def classify_next(run_id: str, req: ClassifyNextRequest):
    """未分類クラスタを先頭からmax_clusters件だけ分類する（バックグラウンドジョブ基盤が
    無いため、UI側が『Classify Next N』ボタンを繰り返し押す設計。1クラスタ=1 Azure呼び出し）。"""
    run = studio.get_run(_client, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run_idが見つかりません")
    narrative_configs = studio.load_narrative_configs_by_key(_client, run["project_id"])
    clusters = studio.load_clusters_for_run(_client, run_id)
    if req.narrative_id:
        clusters = [c for c in clusters if c["narrative_id"] == req.narrative_id]
    unclassified = [c for c in clusters if not c.get("classification")]

    results = []
    for cluster in unclassified[: max(req.max_clusters, 0)]:
        narrative_row = narrative_configs.get((cluster["signal_id"], cluster["narrative_id"]))
        if not narrative_row:
            continue
        representatives = studio.load_representatives_for_cluster(_client, run_id, cluster["cluster_id"])
        result = classify_service.classify_cluster(_azure_client, _model, narrative_row, cluster, representatives)
        checks = classify_service.run_classification_validity_checks(result["data"], representatives, narrative_row)
        status = classify_service.classification_validity_status(checks)
        studio.persist_cluster_classification(_client, cluster["id"], result["data"], checks, status)
        results.append({"cluster_id": cluster["cluster_id"], "validity_status": status})

    return {"classified": results, "remaining_unclassified": len(unclassified) - len(results)}


@geo_report_studio_router.get("/runs/{run_id}/coverage")
def get_coverage(run_id: str):
    run = studio.get_run(_client, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run_idが見つかりません")
    narrative_configs = studio.load_narrative_configs_by_key(_client, run["project_id"])
    cluster_rows = studio.load_clusters_for_run(_client, run_id)
    coverage = classify_service.compute_analytical_coverage(
        [{**c, "classification": (c.get("classification") or {}).get("result")} for c in cluster_rows],
        narrative_configs,
    )
    return coverage


# ---------------------------------------------------------------------------
# Synthesis / Expert Review
# ---------------------------------------------------------------------------

@geo_report_studio_router.get("/runs/{run_id}/syntheses")
def list_syntheses(run_id: str):
    return {"syntheses": studio.list_syntheses_for_run(_client, run_id)}


@geo_report_studio_router.get("/runs/{run_id}/syntheses/{signal_id}/{narrative_id}")
def get_synthesis(run_id: str, signal_id: str, narrative_id: str):
    synthesis = studio.get_latest_synthesis(_client, run_id, signal_id, narrative_id)
    if not synthesis:
        raise HTTPException(status_code=404, detail="このNarrativeのSynthesisはまだありません")
    return synthesis


@geo_report_studio_router.post("/runs/{run_id}/narratives/{signal_id}/{narrative_id}/synthesize")
def synthesize_narrative(run_id: str, signal_id: str, narrative_id: str):
    return run_synthesize_narrative(run_id, signal_id, narrative_id)


def run_synthesize_narrative(run_id: str, signal_id: str, narrative_id: str) -> dict:
    """/synthesizeエンドポイントの本体。geo_report_reassessment_service（Phase6 Closed-loop
    Reassessment）がIntelligence Need解消をトリガーに同じフルSynthesis+Expert Reviewパスを
    呼べるよう、HTTPルートから薄く分離する（ロジックの複製を避ける）。"""
    run = studio.get_run(_client, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run_idが見つかりません")
    narrative_configs = studio.load_narrative_configs_by_key(_client, run["project_id"])
    narrative_row = narrative_configs.get((signal_id, narrative_id))
    if not narrative_row:
        raise HTTPException(status_code=404, detail="Narrative Master行が見つかりません")

    clusters = studio.load_clusters_for_run(_client, run_id)
    classified = [
        c for c in clusters
        if c["signal_id"] == signal_id and c["narrative_id"] == narrative_id
        and (c.get("classification") or {}).get("validity_status") == "PASS"
    ]
    if not classified:
        raise HTTPException(
            status_code=400,
            detail="validity_status=PASSの分類済みクラスタがありません（先にClassifyしてください）")

    reps_by_cluster = {
        c["cluster_id"]: studio.load_representatives_for_cluster(_client, run_id, c["cluster_id"])
        for c in classified
    }

    prior_synthesis = studio.get_prior_synthesis_for_narrative(
        _client, run["project_id"], signal_id, narrative_id, exclude_run_id=run_id)
    prior_continuity = (
        synthesis_service.build_prior_continuity_context(prior_synthesis) if prior_synthesis else None
    )

    observed_block, known_records, known_clusters, known_prior_records = synthesis_service.build_observed_evidence_block(
        narrative_row, classified, reps_by_cluster, prior_continuity=prior_continuity)
    known_records_for_checks = known_records | known_prior_records

    actor_names = (narrative_row.get("actor_names") or "").split("|")
    expert_block, known_expert_refs = synthesis_service.build_expert_context_block(_client, actor_names)

    synthesis_result = synthesis_service.run_synthesis(_azure_client, _model, observed_block, expert_block)
    draft = synthesis_result["data"]
    draft_checks = synthesis_service.run_synthesis_validity_checks(
        draft, known_records_for_checks, known_clusters, known_expert_refs)
    draft_status = synthesis_service.synthesis_validity_status(draft_checks)

    review_result = synthesis_service.run_expert_review(_azure_client, _model, draft, observed_block)
    review = review_result["data"]
    final_draft = review["revised_draft"] if (review["review_verdict"] == "revised" and review["revised_draft"]) else draft
    final_checks = synthesis_service.run_synthesis_validity_checks(
        final_draft, known_records_for_checks, known_clusters, known_expert_refs)
    final_status = synthesis_service.synthesis_validity_status(final_checks)
    final_html = synthesis_service.render_final_html(final_draft, narrative_row)

    synthesis_id = studio.persist_synthesis(
        _client, run_id, signal_id, narrative_id,
        draft, draft_checks, draft_status, review, final_draft, final_status, final_html,
        known_records, known_clusters, known_expert_refs,
    )

    # Phase5 Intelligence Loop Integration: validity_status=PASSの結果のみMonitoring/
    # Intelligence Needsへ昇格する（REVIEW REQUIREDな内容を自動でMonitoringへ流さない、
    # 安全側の設計）。Human Review Gate: Expert Reviewがneeds_human_reviewと判定した
    # 場合は生成・保存は継続するが自動昇格だけを止める(synthesis_auto_promote_eligible)。
    # 人間が確認した後は既存の手動再昇格エンドポイント(repromote_synthesis)から
    # Promoteできる。
    promotion_summary = None
    if synthesis_service.synthesis_auto_promote_eligible(final_status, review["review_verdict"]):
        project = studio.get_project(_client, run["project_id"])
        synthesis_row = {"id": synthesis_id, "run_id": run_id, "signal_id": signal_id,
                          "narrative_id": narrative_id, "final_draft": final_draft}
        promotion_summary = monitoring_service.promote_synthesis(
            _client, project=project, narrative_row_data=narrative_row, synthesis=synthesis_row)

    return {
        "synthesis_id": synthesis_id, "draft_validity_status": draft_status,
        "review_verdict": review["review_verdict"], "final_validity_status": final_status,
        "final_html": final_html, "promotion_summary": promotion_summary,
    }


# ---------------------------------------------------------------------------
# Phase5: Intelligence Loop Integration（Monitoring / Intelligence Needsへの手動再昇格）
# ---------------------------------------------------------------------------

@geo_report_studio_router.post("/runs/{run_id}/syntheses/{signal_id}/{narrative_id}/promote")
def repromote_synthesis(run_id: str, signal_id: str, narrative_id: str):
    """既存のSynthesis結果を再評価してMonitoring/Intelligence Needsへ再昇格する
    （昇格ルール変更後の再適用、または自動昇格が失敗した場合の手動リトライ用）。
    validity_status=PASSのSynthesisにのみ適用する。"""
    run = studio.get_run(_client, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run_idが見つかりません")
    synthesis = studio.get_latest_synthesis(_client, run_id, signal_id, narrative_id)
    if not synthesis:
        raise HTTPException(status_code=404, detail="このNarrativeのSynthesisはまだありません")
    if synthesis["final_validity_status"] != "PASS":
        raise HTTPException(status_code=400, detail="validity_status=PASSのSynthesisのみ昇格できます")

    narrative_configs = studio.load_narrative_configs_by_key(_client, run["project_id"])
    narrative_row = narrative_configs.get((signal_id, narrative_id))
    project = studio.get_project(_client, run["project_id"])
    summary = monitoring_service.promote_synthesis(
        _client, project=project, narrative_row_data=narrative_row, synthesis=synthesis)
    return {"promotion_summary": summary}
