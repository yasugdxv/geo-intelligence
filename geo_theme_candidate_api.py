"""Geopolitical Intelligence OS Phase 2G-1: Theme Candidate CRUD/API。

Theme Discovery UI（Phase 2G-2）・Internal Coverage表示（2G-3）・Wall-bounce（2G-4）・
Expert AI Handoff（2G-5）を含む。ここではCandidateの生成トリガー・一覧・詳細・
Evidence Trace・Lifecycle遷移・Expert AI Handoffを公開する。

Mutation保護（2026-09-30追加）: create/update/status change/delete等のmutationは既存
geo_ios_identity.verify_ios_identity（Pilot Identity boundary、X-IOS-Credential必須）で
保護する。新しい認証基盤（Entra ID/Login UI等）は作らない。GET系（list/detail/evidence）は
既存の他Intelligence AI系エンドポイントと同じ方針（未認証でも閲覧可、Permissionは別途
Internal Coverage表示時にのみ効く——2G-1ではCoverage自体を返さないため今回は実質フィルタ
無し）のまま変更しない。

Internal Coverage（Phase 2G-3追加）: geo_theme_candidatesへcoverageを一切永続化しない。
Candidate Detail表示のたびに、本requestのcurrent requester identityでINTELLIGENCE_AI_V1
の既存assess_internal_coverage()をRuntime呼び出しするだけ（Stable Core無変更、新しい
Permission/検索ロジックはここでは一切作らない）。

Wall-bounce（Phase 2G-4追加）: /wall-bounceはExpert AI相談ではない（Reading/Primary/
Alternativeは作らない）。既存/api/geo/chat・/api/geo/intelligence-ai/chatと同じ
ステートレスMulti-turnパターン（毎request全履歴をクライアントが送る）。GETと同じ
get_optional_ios_identity（未認証でも使えるが、Internal CoverageはPermissionで絞られる）。
/refinementはAI提案を人間が明示的に適用するmutationのため、statusと同じく
verify_ios_identity必須。

Expert AI Handoff（Phase 2G-5追加）: /handoff-previewは何も作成しない確認専用の読み取り
（wall-bounce/internal-coverageと同じget_optional_ios_identity）。/handoffはSession+Intake
Context作成＋Candidate status→HANDED_OFF遷移を伴うmutationのため、statusと同じく
verify_ios_identity必須——Frontendのuser_id/actor_id自己申告は一切信用せず、認証済み
identityのみを使う。Expert AI Core（geo_expert_engine.py）・CQAS・Reading生成には
一切触れない（Bridge本体はgeo_theme_candidate_handoff_service.py）。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402
from ai_client import make_openai_client  # noqa: E402

import geo_ios_identity
import geo_intelligence_ai_theme_coverage_service as theme_coverage_service
import geo_knowledge_access_policy
import geo_theme_candidate_handoff_service as handoff_service
import geo_theme_candidate_pipeline_service as pipeline_service
import geo_theme_candidate_service as candidate_service
import geo_theme_candidate_wallbounce_service as wallbounce_service

_config = load_config()
_client = SupabaseClient(_config)
_azure_client, _model = make_openai_client(_config)

geo_theme_candidate_router = APIRouter(prefix="/api/geo/theme-candidates")


@geo_theme_candidate_router.post("/sync")
def sync_theme_candidates(is_demo: bool = False,
                           identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    """accepted Episode Cluster/Situation Linkからcheap判定→LLM framingでCandidateを
    生成/mergeする（§27 Cost Control、全Signalへ毎回LLMをかけない設計は既にService層で
    実施済み）。mutation操作のためauthenticated IosIdentity必須。"""
    if not _azure_client:
        raise HTTPException(status_code=503, detail="AI分析エンジンが設定されていないため実行できません。")
    return candidate_service.sync_theme_candidates(_client, _azure_client, _model, is_demo=is_demo)


@geo_theme_candidate_router.get("/pipeline-status")
def get_theme_candidate_pipeline_status(is_demo: bool = False):
    """Phase C §3/§15: Episode Review～Theme Candidate～Handoffの滞留件数。
    単なるDB件数一覧ではなく「次に何をすべきか」を示す区分で返す。"""
    return pipeline_service.get_pipeline_status(_client, is_demo=is_demo)


@geo_theme_candidate_router.get("/pending-sync")
def list_pending_theme_sync(is_demo: bool = False):
    """Phase C §4: acceptedだがTheme Candidate未生成のEpisode Cluster（単独／
    situation linkのcomponentグループ）一覧。判定は既存データの関係から決定論的に行い、
    新しいLLM判定は行わない。"""
    return {"pending": pipeline_service.list_pending_sync_material(_client, is_demo=is_demo)}


@geo_theme_candidate_router.get("/sync-runs")
def list_theme_candidate_sync_runs(is_demo: bool = False, limit: int = 50):
    """Phase C §11: Sync実行履歴。新規tableは追加せず既存geo_ingestion_runsを再利用する。"""
    return {"runs": pipeline_service.list_sync_runs(_client, is_demo=is_demo, limit=limit)}


@geo_theme_candidate_router.post("/sync-cluster/{episode_cluster_id}")
def sync_theme_candidate_for_cluster(
        episode_cluster_id: str, is_demo: bool = False,
        identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    """Phase C §5: 指定1件のaccepted Episode ClusterからTheme Candidateを生成する。
    既存sync_theme_candidatesの生成ロジック（build_candidate_material/
    generate_candidate_framing/create_or_merge_candidate）をそのまま再利用するだけで、
    新しいTheme生成ロジックは作らない。"""
    if not _azure_client:
        raise HTTPException(status_code=503, detail="AI分析エンジンが設定されていないため実行できません。")
    try:
        return pipeline_service.sync_cluster(
            _client, _azure_client, _model, episode_cluster_id=episode_cluster_id,
            requested_by=identity.identity_id, is_demo=is_demo)
    except pipeline_service.PipelineError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@geo_theme_candidate_router.post("/sync-pending")
def batch_sync_pending_theme_candidates(
        is_demo: bool = False,
        identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    """Phase C §6/§7: Theme同期待ちの全clusterをまとめて処理する。1件の失敗が他を止めない。"""
    if not _azure_client:
        raise HTTPException(status_code=503, detail="AI分析エンジンが設定されていないため実行できません。")
    return pipeline_service.batch_sync_pending(
        _client, _azure_client, _model, requested_by=identity.identity_id, is_demo=is_demo)


@geo_theme_candidate_router.get("")
def list_theme_candidates(status: str = None, is_demo: bool = False, limit: int = 100):
    return {"candidates": candidate_service.list_candidates(
        _client, status=status, is_demo=is_demo, limit=limit)}


@geo_theme_candidate_router.get("/{candidate_id}")
def get_theme_candidate(candidate_id: str):
    candidate = candidate_service.get_candidate(_client, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="指定されたTheme Candidateが見つかりません")
    return candidate


@geo_theme_candidate_router.get("/{candidate_id}/evidence")
def get_theme_candidate_evidence(candidate_id: str):
    evidence = candidate_service.get_candidate_evidence(_client, candidate_id)
    if not evidence:
        raise HTTPException(status_code=404, detail="指定されたTheme Candidateが見つかりません")
    return evidence


def _requester_context(identity: geo_ios_identity.IosIdentity) -> dict:
    """geo_intelligence_ai_api.pyと同じ最も安全なパターン——認証済みIosIdentity（未ログイン
    なら匿名）由来のみを使い、クライアント自己申告のdepartment等は一切受け取らない。"""
    return geo_knowledge_access_policy.build_requester_context(
        user_id=identity.identity_id if identity.authenticated else None,
        department=identity.department if identity.authenticated else None,
        is_authenticated=identity.authenticated,
    )


@geo_theme_candidate_router.get("/{candidate_id}/internal-coverage")
def get_theme_candidate_internal_coverage(
        candidate_id: str, include_analysis: bool = False,
        identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.get_optional_ios_identity)):
    """Phase 2G-3: 「社内では何が既に分かっているか」をRuntime計算する。geo_theme_candidates
    へは一切保存しない（Requester-dependentなため、毎回本requestのidentityで計算し直す）。
    include_analysis=True時のみINTELLIGENCE_AI_V1のStage 2（既存analyze()、mode="coverage"）
    を追加で呼ぶ——既定（Stage 1、LLM呼び出し無し）のまま一覧表示等では絶対に自動実行しない
    （呼び出し元はCandidate Detailを開いた時だけこのendpointを叩く設計、§Cost Control）。"""
    candidate = candidate_service.get_candidate(_client, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="指定されたTheme Candidateが見つかりません")
    theme_candidate = candidate_service.candidate_to_theme_candidate_input(candidate)
    include_analysis = include_analysis and bool(_azure_client)
    return theme_coverage_service.assess_internal_coverage(
        _client, _azure_client, _model, theme_candidate,
        requester_context=_requester_context(identity), include_analysis=include_analysis)


class UpdateStatusRequest(BaseModel):
    status: str


@geo_theme_candidate_router.patch("/{candidate_id}/status")
def update_theme_candidate_status(
        candidate_id: str, req: UpdateStatusRequest,
        identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    """§22 Human Decision: この操作は常に人間（Frontend UI操作）起点で呼ばれる想定であり、
    AI生成パイプライン（/sync）からは呼ばれない。authenticated IosIdentity必須——actor_idは
    クライアント申告値を信用せず、認証済みidentityから取る（reviewer spoofing対策と同じ
    考え方）。"""
    try:
        return candidate_service.update_candidate_status(
            _client, candidate_id, req.status, actor_id=identity.identity_id)
    except candidate_service.ThemeCandidateError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


class WallBounceMessage(BaseModel):
    role: str
    content: str


class WallBounceRequest(BaseModel):
    message: str
    # 既存/api/geo/intelligence-ai/chatのChatMsg[]と同じ、role/contentのみのプレーン
    # テキスト。Source本文・Permission結果・identityをhistoryに含めさせない。
    history: list[WallBounceMessage] = []


@geo_theme_candidate_router.post("/{candidate_id}/wall-bounce")
def wall_bounce_chat(
        candidate_id: str, req: WallBounceRequest,
        identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.get_optional_ios_identity)):
    """Phase 2G-4: Theme Candidateについて人間と壁打ちする（Reading/Primary/Alternativeは
    作らない、Expert AI相談とは別物）。External Evidence/Internal Coverage/Permissionを
    毎turn本requestのidentityでゼロから再評価する（§絶対条件）。"""
    if not _azure_client:
        return {
            "answer": "AI分析エンジンが設定されていないため実行できません。",
            "has_proposal": False,
            "proposal": {
                "proposed_title": None, "proposed_question": None, "proposed_actors": [],
                "proposed_regions": [], "proposed_scope": None, "investigation_questions": [],
                "rationale": None,
            },
            "source_refs": [],
        }
    try:
        return wallbounce_service.chat(
            _client, _azure_client, _model, candidate_id,
            history=[m.model_dump() for m in req.history], message=req.message,
            requester_context=_requester_context(identity))
    except candidate_service.ThemeCandidateError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


class RefinementRequest(BaseModel):
    title: str | None = None
    question: str | None = None
    actors: list[str] | None = None
    regions: list[str] | None = None
    themes: list[str] | None = None
    business_relevance: list[str] | None = None


@geo_theme_candidate_router.patch("/{candidate_id}/refinement")
def apply_theme_candidate_refinement(
        candidate_id: str, req: RefinementRequest,
        identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    """Phase 2G-4 §Human-edit ownership: Wall-bounceのproposalをHumanが明示的に「反映」した
    時だけ呼ばれる。mutationのためauthenticated IosIdentity必須、actor_idはクライアント
    申告値を信用せず認証済みidentityから取る。statusは変更しない（Request schema自体に
    statusフィールドが無い）。"""
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    try:
        return candidate_service.apply_candidate_refinement(
            _client, candidate_id, patch=patch, actor_id=identity.identity_id)
    except candidate_service.ThemeCandidateError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


class HandoffRequest(BaseModel):
    # §7: Wall-bounceのinvestigation_questionsは非永続のため、Handoff時に人間が明示的に
    # 選択したものだけをここで受け取る（未反映のAI proposalを自動的に混ぜない）。
    investigation_questions: list[str] = []


@geo_theme_candidate_router.post("/{candidate_id}/handoff-preview")
def preview_theme_candidate_handoff(
        candidate_id: str, req: HandoffRequest,
        identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.get_optional_ios_identity)):
    """§8: 何も作成しない確認専用の読み取り。既にHandoff済みSessionがあればその
    analysis_session_idも返す（人間がPreview時点でDouble-handoffかどうか判断できる）。"""
    try:
        return handoff_service.build_handoff_preview(
            _client, _azure_client, _model, candidate_id,
            requester_context=_requester_context(identity),
            investigation_questions=req.investigation_questions)
    except handoff_service.ThemeCandidateHandoffError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@geo_theme_candidate_router.post("/{candidate_id}/handoff")
def handoff_theme_candidate(
        candidate_id: str, req: HandoffRequest,
        identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    """§9/§13: Session＋初期Intake Context作成→成功時のみCandidate status→HANDED_OFF。
    user_id/actor_idはクライアント自己申告を信用せず、認証済みidentityから取る。
    §11 Idempotency: 既存Sessionがあれば新規作成せずそれを返す（Double-click対策）。"""
    if not _azure_client:
        raise HTTPException(status_code=503, detail="AI分析エンジンが設定されていないため実行できません。")
    try:
        result = handoff_service.handoff_theme_candidate_to_expert_ai(
            _client, _azure_client, _model, candidate_id,
            actor_id=identity.identity_id, user_id=identity.identity_id,
            requester_context=_requester_context(identity),
            investigation_questions=req.investigation_questions)
    except handoff_service.ThemeCandidateHandoffError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"analysis_session_id": result["session"]["analysis_session_id"], "created": result["created"]}
