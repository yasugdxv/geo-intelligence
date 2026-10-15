"""Geopolitical Intelligence OS Phase B/C: Analysis Session / Scenario Candidate / Analysis Design API。

/api/geo/ios/* という新しい名前空間にマウントする（既存/api/geo/expert-review等とは独立）。
既存api_server.pyへの変更は「このモジュールのimportとinclude_routerの2行追加」のみ
（既存geo_intelligence_gap_api.py分離時と同じ方針）。

Phase C: generate-scenarios/generate-design/refine-designは既存Expert AI
（geo_expert_engine.run_expert_request_with_mode、/api/geo/expert/requestsと同じEntry Point）を
geo_ios_expert_ai_adapter経由でのみ呼び出す。create-reportは既存Report Studio
（report_studio_service.get_or_create_project）をそのまま利用する。いずれもAdapter越しの
呼び出しに限定し、Expert AI本体・Report Studio本体のコードは変更しない。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ai_client import make_openai_client  # noqa: E402
from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

import geo_ios_analysis_session_service as session_service
import geo_ios_expert_ai_adapter as expert_ai_adapter
import geo_ios_workflow_board_service as workflow_board_service
import geo_report_studio_service as report_studio_service

_config = load_config()
_client = SupabaseClient(_config)
_azure_client, _model = make_openai_client(_config)

geo_ios_analysis_session_router = APIRouter(prefix="/api/geo/ios/analysis-sessions")


def _require_azure_client():
    if not _azure_client:
        raise HTTPException(status_code=503, detail="Azure OpenAI / OpenAI のAPIキーが未設定です")


_PROJECT_TITLE_MAX_LEN = 40


def _short_project_title(text: str) -> str:
    """Report Project表題の機械的短縮（新規LLM呼び出しなし）。geo_ios_expert_ai_adapter.
    _short_titleと同じ方針: 文/読点の区切りが短縮上限内にあればそこで切り、無ければ
    上限で単純に切ってellipsisを付ける。"""
    if not text:
        return text
    for sep in ("。", "、"):
        idx = text.find(sep)
        if 0 < idx <= _PROJECT_TITLE_MAX_LEN:
            return text[:idx]
    if len(text) <= _PROJECT_TITLE_MAX_LEN:
        return text
    return text[:_PROJECT_TITLE_MAX_LEN] + "…"


class CreateSessionRequest(BaseModel):
    title: str
    user_id: str
    theme: str | None = None
    # §17（2026-09-25追加）: Report Studioの2入口（新しく分析する/既存レポートをレビュー
    # する）を区別する。既存呼び出し元は省略時"create"のまま（既存動作を変えない）。
    mode: str = "create"


@geo_ios_analysis_session_router.post("")
def create_session(req: CreateSessionRequest):
    try:
        return session_service.create_session(
            _client, title=req.title, user_id=req.user_id, theme=req.theme, mode=req.mode)
    except session_service.AnalysisSessionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@geo_ios_analysis_session_router.get("")
def list_sessions(user_id: str | None = None, status: str | None = None, mode: str | None = None):
    return {"sessions": session_service.list_sessions(_client, user_id=user_id, status=status, mode=mode)}


@geo_ios_analysis_session_router.get("/{analysis_session_id}")
def get_session(analysis_session_id: str):
    session = session_service.get_session(_client, analysis_session_id)
    if not session:
        raise HTTPException(status_code=404, detail="analysis_session_idが見つかりません")
    return session


@geo_ios_analysis_session_router.get("/{analysis_session_id}/workflow-state")
def get_session_workflow_state(analysis_session_id: str):
    """Session詳細ページのWorkflow Header（設計/ドラフト/AIレビュー/専門家レビュー/
    承認・ストック）表示用。Workflow Board（/api/geo/ios/workflow-board）と同じ
    geo_ios_workflow_board_service._build_caseを1件分だけ呼ぶだけで、Board側は
    identity認可越しに全件集計するのに対しこちらは認可不要な単一Session参照
    （既存のSession詳細ページ自体が認可を要求していないのに合わせる）。"""
    case = workflow_board_service.get_case_for_session(_client, analysis_session_id)
    if not case:
        raise HTTPException(status_code=404, detail="analysis_session_idが見つかりません")
    return case


@geo_ios_analysis_session_router.get("/by-report-project/{report_project_id}")
def get_session_by_report_project(report_project_id: str):
    """§15 Provenance: Report Project → Analysis Sessionの逆引き。"""
    session = session_service.get_session_by_report_project(_client, report_project_id)
    if not session:
        raise HTTPException(status_code=404, detail="このreport_project_idに紐づくAnalysis Sessionはありません")
    return session


class UpdateSessionStatusRequest(BaseModel):
    status: str
    actor_id: str


@geo_ios_analysis_session_router.patch("/{analysis_session_id}/status")
def update_session_status(analysis_session_id: str, req: UpdateSessionStatusRequest):
    try:
        return session_service.update_session_status(
            _client, analysis_session_id, status=req.status, actor_id=req.actor_id)
    except session_service.AnalysisSessionError as e:
        raise HTTPException(status_code=400, detail=str(e))


class LinkReportProjectRequest(BaseModel):
    report_project_id: str
    actor_id: str


@geo_ios_analysis_session_router.post("/{analysis_session_id}/link-report-project")
def link_report_project(analysis_session_id: str, req: LinkReportProjectRequest):
    """§6 Create Report: Analysis SessionをReport Studioの既存Report Projectへ引き継ぐ。
    Report Studio自体のProject作成はこのAPIの責務ではない（既存Report Studio APIで作成済みの
    project_idを渡すこと）。"""
    try:
        return session_service.link_report_project(
            _client, analysis_session_id, report_project_id=req.report_project_id, actor_id=req.actor_id)
    except session_service.AnalysisSessionError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---- Scenario Candidate（§3） -----------------------------------------------

class ScenarioCandidateInput(BaseModel):
    title: str
    content: dict = {}


class RecordScenarioCandidatesRequest(BaseModel):
    candidates: list[ScenarioCandidateInput]
    display_top_n: int = 5
    model: str | None = None
    prompt_version: str | None = None
    cqas_knowledge_version: str | None = None
    actor_id: str


@geo_ios_analysis_session_router.post("/{analysis_session_id}/scenario-candidates")
def record_scenario_candidates(analysis_session_id: str, req: RecordScenarioCandidatesRequest):
    try:
        candidates = [c.model_dump() for c in req.candidates]
        return {"candidates": session_service.record_scenario_candidates(
            _client, analysis_session_id, candidates=candidates, display_top_n=req.display_top_n,
            model=req.model, prompt_version=req.prompt_version,
            cqas_knowledge_version=req.cqas_knowledge_version, actor_id=req.actor_id)}
    except session_service.AnalysisSessionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@geo_ios_analysis_session_router.get("/{analysis_session_id}/scenario-candidates")
def list_scenario_candidates(analysis_session_id: str, displayed_only: bool = False):
    return {"candidates": session_service.list_scenario_candidates(
        _client, analysis_session_id, displayed_only=displayed_only)}


class SelectScenarioCandidateRequest(BaseModel):
    actor_id: str
    confirm_after_handoff: bool = False


@geo_ios_analysis_session_router.post("/scenario-candidates/{scenario_candidate_id}/select")
def select_scenario_candidate(scenario_candidate_id: str, req: SelectScenarioCandidateRequest):
    """§8: Report作成済み後の選択変更にはconfirm_after_handoff=trueを明示すること。"""
    try:
        return session_service.select_scenario_candidate(
            _client, scenario_candidate_id, actor_id=req.actor_id,
            confirm_after_handoff=req.confirm_after_handoff)
    except session_service.AnalysisSessionError as e:
        raise HTTPException(status_code=400, detail=str(e))


class GenerateScenariosRequest(BaseModel):
    actor_id: str
    theme_override: str | None = None
    display_top_n: int = 5
    mode: str | None = None
    profile: str | None = None
    execution_mode: str | None = None


@geo_ios_analysis_session_router.post("/{analysis_session_id}/generate-scenarios")
def generate_scenarios(analysis_session_id: str, req: GenerateScenariosRequest):
    """Phase C §3, §5: 既存Expert AI（geo_expert_engine.run_expert_request_with_mode）を
    geo_ios_expert_ai_adapter経由で1回呼び出し、全候補を保存する（表示は上位display_top_n件）。
    §19 Failure Handling: Expert AI呼び出しが失敗した場合、Sessionはそのまま保持し、候補は
    一切書き込まない（successの場合のみrecord_scenario_candidatesを呼ぶ）。"""
    _require_azure_client()
    session = session_service.get_session(_client, analysis_session_id)
    if not session:
        raise HTTPException(status_code=404, detail="analysis_session_idが見つかりません")
    theme = req.theme_override or session.get("theme") or session["title"]

    try:
        generated = expert_ai_adapter.generate_scenario_candidates(
            _client, _azure_client, _model, theme=theme, mode=req.mode, profile=req.profile,
            execution_mode=req.execution_mode, actor_id=req.actor_id)
    except expert_ai_adapter.ExpertAIAdapterError as e:
        raise HTTPException(status_code=502, detail=str(e))

    created = session_service.record_scenario_candidates(
        _client, analysis_session_id, candidates=generated["candidates"],
        display_top_n=req.display_top_n, model=generated["model"],
        prompt_version=generated["prompt_version"],
        cqas_knowledge_version=generated["cqas_knowledge_version"], actor_id=req.actor_id)
    return {
        "candidates": created, "displayed_count": min(req.display_top_n, len(created)),
        "total_count": len(created), "source_cqas": {
            "request_id": generated["request_id"], "response_id": generated["response_id"]},
    }


# ---- Analysis Design（§5） --------------------------------------------------

class CreateDesignVersionRequest(BaseModel):
    fields: dict
    change_note: str
    created_by: str


@geo_ios_analysis_session_router.post("/{analysis_session_id}/design-versions")
def create_design_version(analysis_session_id: str, req: CreateDesignVersionRequest):
    try:
        return session_service.create_design_version(
            _client, analysis_session_id, fields=req.fields, change_note=req.change_note,
            created_by=req.created_by)
    except session_service.AnalysisSessionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@geo_ios_analysis_session_router.get("/{analysis_session_id}/design-versions")
def list_design_history(analysis_session_id: str):
    return {"versions": session_service.list_design_history(_client, analysis_session_id)}


@geo_ios_analysis_session_router.get("/{analysis_session_id}/design-versions/current")
def get_current_design(analysis_session_id: str):
    design = session_service.get_current_design(_client, analysis_session_id)
    if not design:
        raise HTTPException(status_code=404, detail="Analysis Designがまだ作成されていません")
    return design


class GenerateDesignRequest(BaseModel):
    created_by: str


@geo_ios_analysis_session_router.post("/{analysis_session_id}/generate-design")
def generate_design(analysis_session_id: str, req: GenerateDesignRequest):
    """Phase C §9: 選択済みScenario CandidateのCQAS Run内容を、既存Expert AI応答
    （geo_expert_responses、再取得のみ・新規LLM呼び出しなし）からAnalysis Designへmappingし、
    version 1として保存する。"""
    session = session_service.get_session(_client, analysis_session_id)
    if not session:
        raise HTTPException(status_code=404, detail="analysis_session_idが見つかりません")
    if not session.get("selected_scenario_id"):
        raise HTTPException(status_code=400, detail="先にシナリオを選択してください（select-scenario）")

    candidates = session_service.list_scenario_candidates(_client, analysis_session_id)
    selected = next((c for c in candidates if c["scenario_candidate_id"] == session["selected_scenario_id"]), None)
    if not selected:
        raise HTTPException(status_code=404, detail="選択されたscenario_candidateが見つかりません")

    theme = session.get("theme") or session["title"]
    try:
        fields = expert_ai_adapter.build_design_from_candidate(_client, theme=theme, scenario_candidate=selected)
    except expert_ai_adapter.ExpertAIAdapterError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return session_service.create_design_version(
        _client, analysis_session_id, fields=fields,
        change_note="選択したScenario CandidateのCQAS Runから初版を生成", created_by=req.created_by)


class RefineDesignRequest(BaseModel):
    user_message: str
    created_by: str
    mode: str | None = None
    profile: str | None = None
    execution_mode: str | None = None


@geo_ios_analysis_session_router.post("/{analysis_session_id}/refine-design")
def refine_design(analysis_session_id: str, req: RefineDesignRequest):
    """Phase C §11: refine_analysis_design相当。現Designを上書きせず新versionとして追記する
    （§10）。§19: Expert AI呼び出し失敗時はversionを追加しない。"""
    _require_azure_client()
    session = session_service.get_session(_client, analysis_session_id)
    if not session:
        raise HTTPException(status_code=404, detail="analysis_session_idが見つかりません")
    current_design = session_service.get_current_design(_client, analysis_session_id)
    if not current_design:
        raise HTTPException(status_code=404, detail="Analysis Designがまだ作成されていません（先にgenerate-design）")

    theme = session.get("theme") or session["title"]
    try:
        refined = expert_ai_adapter.refine_design(
            _client, _azure_client, _model, current_design=current_design, user_message=req.user_message,
            theme=theme, mode=req.mode, profile=req.profile, execution_mode=req.execution_mode,
            actor_id=req.created_by)
    except expert_ai_adapter.ExpertAIAdapterError as e:
        raise HTTPException(status_code=502, detail=str(e))

    change_note = f"Chat refinement: {req.user_message}"
    new_version = session_service.create_design_version(
        _client, analysis_session_id, fields=refined["updated_fields"], change_note=change_note,
        created_by=req.created_by)
    return {"design_version": new_version, "change_summary": refined["change_summary"]}


class CreateReportRequest(BaseModel):
    actor_id: str
    project_code: str | None = None
    report_language: str = "ja"


@geo_ios_analysis_session_router.post("/{analysis_session_id}/create-report")
def create_report(analysis_session_id: str, req: CreateReportRequest):
    """Phase C §13-14: 既存Report Studio（report_studio_service.get_or_create_project、
    Report Studio本体は変更しない）でReport Projectを作成/取得し、Analysis Sessionへ
    紐付ける（session_service.link_report_project、Phase Bから既存）。"""
    session = session_service.get_session(_client, analysis_session_id)
    if not session:
        raise HTTPException(status_code=404, detail="analysis_session_idが見つかりません")
    design = session_service.get_current_design(_client, analysis_session_id)
    # REVIEW MODE（Phase 2C、レビューフロー独立化）: レビュー対象レポートをそのまま
    # レビューする経路であり、Analysis Design（Reading Studioの「読み方提案」）自体を
    # 生成しない（geo_ios_review_import_serviceはgeo_ios_analysis_designsへ一切書き込まない）。
    # CREATE MODEはこれまで通りdesign必須のまま維持する。
    if not design and session.get("mode") != "review":
        raise HTTPException(status_code=400, detail="Analysis Designがまだ作成されていません（先にgenerate-design）")

    project_code = req.project_code or f"ios-{analysis_session_id[:8]}"
    # 2026-09-24修正、ユーザー指摘「(Expert Review画面の)タイトルも短くした版にすべき」:
    # session["title"]はIntake画面で必須入力される、まさにこの用途向けの短い表題フィールド
    # だが、従来はdesign.investigation_question（未生成なら空）→session["theme"]（分析
    # 依頼文そのもの、数百字になりうる自由記述）→session["title"]の順で選んでいたため、
    # investigation_questionが無い場合に長大なtheme全文がReport Projectの表題として
    # そのまま使われていた。session["title"]を最優先にし、どの値が選ばれても
    # _short_project_titleで機械的に短縮する（Reading Cardタイトル短縮と同じ方針、
    # 新規LLM呼び出しは追加しない）。
    project_title = _short_project_title(
        session["title"] or (design or {}).get("investigation_question")
        or session.get("theme") or project_code)
    project = report_studio_service.get_or_create_project(
        _client, project_code, project_title, report_language=req.report_language)

    updated_session = session_service.link_report_project(
        _client, analysis_session_id, report_project_id=project["project_id"], actor_id=req.actor_id)
    return {"report_project": project, "analysis_session": updated_session}
