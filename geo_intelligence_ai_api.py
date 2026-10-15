"""Geopolitical Intelligence OS Phase 2F-1/2F-3/2F-4: Intelligence AI API（Function A: Report/
Knowledge Search、Function C: Cross-report Grounded AI Analysis、Function D: True Multi-turn
Chat）。

人間ユーザー向け（/geo/intelligence-ai）のエントリポイントのみを今回公開する。
Cross-Domain Agent向け（他部署AIからの問い合わせ）は既存geo_department_intelligence_
serviceが別途担っており（Phase 2F Audit確認済み）、本APIでは扱わない。
geo_intelligence_ai_search_service.search()/geo_intelligence_ai_analysis_service.analyze()
自体はis_internal_caller/caller_domainも受け付けるため、将来Theme Discovery（Phase 2G）等
から内部関数として直接呼べる。

Phase 2F-4（/chat）: INTELLIGENCE_AI_GROUNDED_ANALYSIS_V1（docs/intelligence-ai-grounded-
analysis-v1-freeze-manifest.md）で確定したPermission/Retrieval/Analysis Coreは無変更のまま、
Multi-turn Chatレイヤーとして上に積む。既存/api/geo/chat（Expert AI、別システム）と同じ
「毎request全履歴をクライアントが送るステートレスBackend」パターンを踏襲し、Backend側に
会話セッションを保持しない——各requestのrequester_contextは常にその場で組み立てる
（historyの中にPermission結果やidentityを混ぜて送らせない設計）。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402
from ai_client import make_openai_client  # noqa: E402

import geo_ios_identity
import geo_intelligence_ai_analysis_service as analysis_service
import geo_intelligence_ai_chat_service as chat_service
import geo_intelligence_ai_search_service as search_service
import geo_intelligence_ai_theme_coverage_service as theme_coverage_service
import geo_knowledge_access_policy

_config = load_config()
_client = SupabaseClient(_config)
_azure_client, _model = make_openai_client(_config)

geo_intelligence_ai_router = APIRouter(prefix="/api/geo/intelligence-ai")


class GeoIntelligenceAiSearchRequest(BaseModel):
    query: str
    include_demo: bool = False
    limit: int = 20
    # Phase 2F-3 §0: date_from/date_to/region/theme/key_actor/review_status/source_type/
    # confidentiality_labelのいずれか（省略可）。Permission判定合格後にEligible Corpusを
    # 絞り込む（geo_intelligence_ai_search_service.resolve_eligible_*_ids参照）。
    filters: dict | None = None


def _requester_context(identity: geo_ios_identity.IosIdentity) -> dict:
    """Permission EvaluationはRetrievalより前（§3絶対条件）。requester_contextは認証済み
    IosIdentity（未ログインなら匿名）由来のみを使う——クライアント自己申告のdepartment等は
    一切受け取らない（Phase 2E以前のsearch_knowledge_itemsクエリパラメータ方式は今回は
    踏襲しない、§14 新しいpermissionロジックを作らないのと同じ理由で既存の最も安全な
    パターンへ揃える）。"""
    return geo_knowledge_access_policy.build_requester_context(
        user_id=identity.identity_id if identity.authenticated else None,
        department=identity.department if identity.authenticated else None,
        is_authenticated=identity.authenticated,
    )


@geo_intelligence_ai_router.post("/search")
def search_reports_and_knowledge(
        req: GeoIntelligenceAiSearchRequest,
        identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.get_optional_ios_identity)):
    """Phase 2F-1 Function A: 自然言語でReport/Knowledge Itemを横断検索する。"""
    return search_service.search(
        _client, req.query, requester_context=_requester_context(identity),
        include_demo=req.include_demo, limit=req.limit, filters=req.filters)


class GeoIntelligenceAiAnalyzeRequest(BaseModel):
    query: str
    mode: str = "summary"
    filters: dict | None = None
    # §13 Selected Source Analysis: [{"source_type": "report"|"knowledge_item", "id": "..."}]。
    # 渡されたIDはPermission再検証を必ず経る（geo_intelligence_ai_analysis_service.
    # _resolve_selected_sources、§16 Frontendからの申告を信用しない）。
    selected_source_refs: list[dict] | None = None
    include_demo: bool = False
    limit: int = 50


@geo_intelligence_ai_router.post("/analyze")
def analyze_cross_report(
        req: GeoIntelligenceAiAnalyzeRequest,
        identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.get_optional_ios_identity)):
    """Phase 2F-3 Function C: Permission-filteredな複数Internal Report/Knowledgeを根拠に
    横断分析する（Summary/Compare/Temporal/Coverage/Reviewed）。一般知識・外部情報での
    穴埋めは行わない（§3 Internal-only Grounded Mode）。"""
    if not _azure_client:
        return {
            "answer": "AI分析エンジンが設定されていないため実行できません。",
            "insufficient_evidence": True, "findings": [],
            "coverage": {"regions": [], "themes": [], "key_actors": []}, "temporal_notes": None,
            "warnings": ["azure_openai_not_configured"], "source_refs": [], "retrieval_metadata": {},
        }
    return analysis_service.analyze(
        _client, _azure_client, _model, req.query, mode=req.mode, filters=req.filters,
        selected_source_refs=req.selected_source_refs, requester_context=_requester_context(identity),
        include_demo=req.include_demo, limit=req.limit)


class GeoIntelligenceAiChatMessage(BaseModel):
    role: str
    content: str


class GeoIntelligenceAiChatRequest(BaseModel):
    message: str
    # Frontendが保持する会話履歴（既存/api/geo/chatのChatMsg[]と同じ、role/contentのみの
    # プレーンテキスト）。Source本文・permission結果・identityをhistoryに含めさせない
    # ——それらは本requestのidentityから毎回ゼロで再評価する。
    history: list[GeoIntelligenceAiChatMessage] = []
    mode: str = "summary"
    filters: dict | None = None
    include_demo: bool = False
    limit: int = 50


@geo_intelligence_ai_router.post("/chat")
def chat_cross_report(
        req: GeoIntelligenceAiChatRequest,
        identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.get_optional_ios_identity)):
    """Phase 2F-4 Function D: True Multi-turn Chat。会話履歴から現在の質問を解決したうえで、
    既存Analysis Service（無変更）を本requestのidentityで毎回呼び直す（§絶対条件:
    前turnのPermission結果を信用しない）。"""
    if not _azure_client:
        return {
            "answer": "AI分析エンジンが設定されていないため実行できません。",
            "insufficient_evidence": True, "findings": [],
            "coverage": {"regions": [], "themes": [], "key_actors": []}, "temporal_notes": None,
            "warnings": ["azure_openai_not_configured"], "source_refs": [], "retrieval_metadata": {},
            "resolved_query": req.message,
        }
    return chat_service.chat(
        _client, _azure_client, _model,
        history=[m.model_dump() for m in req.history], message=req.message, mode=req.mode,
        filters=req.filters, requester_context=_requester_context(identity),
        include_demo=req.include_demo, limit=req.limit)


class GeoIntelligenceAiThemeCandidate(BaseModel):
    # Phase 2F-6 §4: Theme Discoveryの将来schemaと過度に結合しない最小共通schema。
    # theme_title以外はすべて省略可。
    theme_title: str
    theme_description: str | None = None
    keywords: list[str] | None = None
    actors: list[str] | None = None
    regions: list[str] | None = None
    signal_summary: str | None = None
    date_from: str | None = None
    date_to: str | None = None


class GeoIntelligenceAiInternalCoverageRequest(BaseModel):
    theme_candidate: GeoIntelligenceAiThemeCandidate
    # §12 Token/Cost Control: 既定はStage 1（metadata-only、LLM呼び出し無し）。
    # Stage 2（既存analyze()、mode="coverage"）はcaller（Theme Discovery側）が必要な
    # Candidateだけ明示的にTrueにする。
    include_analysis: bool = False
    similar_limit: int = 5
    include_demo: bool = False


@geo_intelligence_ai_router.post("/internal-coverage")
def internal_coverage(
        req: GeoIntelligenceAiInternalCoverageRequest,
        identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.get_optional_ios_identity)):
    """Phase 2F-6: Theme Discovery Internal Intelligence Bridge。「このThemeは社内で
    どれくらい既に分析されているか」だけを返すIntelligence Memory API——Themeの採否・
    優先順位はここでは判断しない（§15、Phase 2G側の責務）。"""
    theme_candidate = req.theme_candidate.model_dump()
    include_analysis = req.include_analysis and bool(_azure_client)
    return theme_coverage_service.assess_internal_coverage(
        _client, _azure_client, _model, theme_candidate,
        requester_context=_requester_context(identity), include_demo=req.include_demo,
        include_analysis=include_analysis, similar_limit=req.similar_limit)
