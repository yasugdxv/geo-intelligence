"""地政学ナレッジ＆インテリジェンスDB Phase 1: APIルーター。

api_server.pyの既存/api/geo/*（指標・専門家AI）とは独立したパス空間
（/api/geo/knowledge/*）にマウントする。既存api_server.pyの変更は
「このモジュールのimportとinclude_routerの2行追加」のみに留める。

Development-only role control: actor_role はリクエストボディの自己申告であり、
認証を伴わない。本番運用では使用しないこと（docs/geopolitical-operations-playbook.md 3章）。
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402
from ai_client import make_openai_client  # noqa: E402

import geo_candidate_extraction_service as candidate_service
import geo_case_service
import geo_claim_service
import geo_connector_base
import geo_connector_csv_excel_json as csv_connector
import geo_connector_imf
import geo_connector_manifesto
import geo_connector_ofac
import geo_connector_un_comtrade
import geo_connector_world_bank
import geo_connector_wto
import geo_entity_resolution
import geo_entity_service
import geo_expert_engine
import geo_expert_schema
import geo_foundation_pack_service
import geo_freshness_service
import geo_entity_context_service
import geo_knowledge_access_policy
import geo_knowledge_ai_review_service
import geo_knowledge_natural_search
import geo_knowledge_gap_service as gap_service
import geo_knowledge_service
import geo_metric_service
import geo_ios_identity
import geo_relationship_service
import geo_role_guard
import geo_search_service

_config = load_config()
_client = SupabaseClient(_config)
_azure_client, _model = make_openai_client(_config)

geo_knowledge_router = APIRouter(prefix="/api/geo/knowledge")


def _require_azure():
    if not _azure_client:
        raise HTTPException(status_code=503, detail="Azure OpenAI / OpenAI のAPIキーが未設定です")


def _wrap_value_error(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except geo_role_guard.RoleDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except geo_knowledge_service.InvalidStatusTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except geo_connector_base.ConnectorDisabledError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Entities ──────────────────────────────────────────────────────────

class EntityCreateRequest(BaseModel):
    entity_type: str
    canonical_name: str
    actor_id: str
    local_name: str | None = None
    name_en: str | None = None
    name_ja: str | None = None
    abbreviations: list[str] = []
    country_code: str | None = None
    is_demo: bool = False


@geo_knowledge_router.post("/entities")
def create_entity(req: EntityCreateRequest):
    return _wrap_value_error(geo_entity_service.create_entity, _client, entity_type=req.entity_type,
                              canonical_name=req.canonical_name, actor_id=req.actor_id,
                              local_name=req.local_name, name_en=req.name_en, name_ja=req.name_ja,
                              abbreviations=req.abbreviations, country_code=req.country_code,
                              is_demo=req.is_demo)


@geo_knowledge_router.get("/entities")
def list_entities(entity_type: str = "", country_code: str = "", include_demo: bool = False):
    return {"entities": geo_entity_service.list_entities(
        _client, entity_type=entity_type or None, country_code=country_code or None,
        include_demo=include_demo)}


@geo_knowledge_router.get("/entities/{entity_id}")
def get_entity(entity_id: str):
    entity = geo_entity_service.get_entity(_client, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="entity_idが見つかりません")
    return entity


@geo_knowledge_router.get("/entities/{entity_id}/context")
def get_entity_context(entity_id: str,
                       identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.get_optional_ios_identity)):
    """エンティティの「中身」: 紐づく論点・関連するナレッジ項目。内容を返すのは、承認済み・参照可能・
    閲覧権限のある項目だけ（承認前の項目は件数のみ）。"""
    if not geo_entity_service.get_entity(_client, entity_id):
        raise HTTPException(status_code=404, detail="entity_idが見つかりません")
    ctx = geo_knowledge_access_policy.build_requester_context(
        user_id=identity.identity_id if identity.authenticated else None,
        department=identity.department if identity.authenticated else None,
        is_authenticated=identity.authenticated)
    return geo_entity_context_service.get_entity_context(_client, entity_id, requester_context=ctx)


@geo_knowledge_router.get("/entities/resolve")
def resolve_entity(name_text: str):
    return geo_entity_resolution.resolve_or_flag(_client, name_text)


class EntityMergeRequest(BaseModel):
    primary_entity_id: str
    duplicate_entity_id: str
    actor_id: str
    actor_role: str
    reason: str


@geo_knowledge_router.post("/entities/merge")
def merge_entities(req: EntityMergeRequest):
    return _wrap_value_error(geo_entity_service.merge_entities, _client,
                              primary_entity_id=req.primary_entity_id,
                              duplicate_entity_id=req.duplicate_entity_id, actor_id=req.actor_id,
                              actor_role=req.actor_role, reason=req.reason)


class EntityIssueLinkRequest(BaseModel):
    issue_id: str


@geo_knowledge_router.post("/entities/{entity_id}/issues")
def link_entity_issue(entity_id: str, req: EntityIssueLinkRequest):
    return _client.insert("geo_entity_issues", [{"entity_id": entity_id, "issue_id": req.issue_id}])[0]


# ── Relationships ─────────────────────────────────────────────────────

class RelationshipCreateRequest(BaseModel):
    from_entity_id: str
    to_entity_id: str
    relationship_domain: str
    relationship_type: str
    actor_id: str
    description: str | None = None
    dependency_direction: str | None = None
    dependency_level: str | None = None
    leverage_direction: str | None = None
    confidence: str = "判断不能"
    valid_from: str | None = None
    valid_to: str | None = None
    is_demo: bool = False


@geo_knowledge_router.post("/relationships")
def create_relationship(req: RelationshipCreateRequest):
    return _wrap_value_error(geo_relationship_service.create_relationship, _client, **req.model_dump())


@geo_knowledge_router.get("/relationships")
def list_relationships(entity_id: str = "", issue_id: str = "", include_demo: bool = False):
    if entity_id:
        return {"relationships": geo_relationship_service.list_relationships_for_entity(
            _client, entity_id, include_demo=include_demo)}
    if issue_id:
        return {"relationships": geo_relationship_service.list_relationships_for_issue(_client, issue_id)}
    raise HTTPException(status_code=400, detail="entity_idまたはissue_idを指定してください")


class RelationshipIssueLinkRequest(BaseModel):
    issue_id: str


@geo_knowledge_router.post("/relationships/{relationship_id}/issues")
def link_relationship_issue(relationship_id: str, req: RelationshipIssueLinkRequest):
    return _client.insert("geo_relationship_issues", [{
        "relationship_id": relationship_id, "issue_id": req.issue_id,
    }])[0]


@geo_knowledge_router.get("/issues/{issue_id}/relationship-graph")
def get_relationship_graph(issue_id: str, include_demo: bool = False):
    """Network Graph（Phase2B、Relationship Explorer）向け。node=Entity, edge=Relationshipを
    DB側RPC1回で返す（geo_relationship_service.get_relationship_graph_for_issue参照）。"""
    return geo_relationship_service.get_relationship_graph_for_issue(_client, issue_id, include_demo=include_demo)


# ── Assets / Dependencies ─────────────────────────────────────────────

class AssetCreateRequest(BaseModel):
    entity_id: str
    asset_type: str
    description: str
    quantitative_value: dict | None = None
    confidence: str = "判断不能"
    is_demo: bool = False


@geo_knowledge_router.post("/assets")
def create_asset(req: AssetCreateRequest):
    return _client.insert("geo_assets", [req.model_dump()])[0]


class DependencyCreateRequest(BaseModel):
    entity_id: str
    dependency_type: str
    description: str
    counterpart_entity_id: str | None = None
    severity: str = "判断不能"
    confidence: str = "判断不能"
    is_demo: bool = False


@geo_knowledge_router.post("/dependencies")
def create_dependency(req: DependencyCreateRequest):
    return _client.insert("geo_dependencies", [req.model_dump()])[0]


# ── Issues (Theater配下の個別論点) ─────────────────────────────────────

class IssueCreateRequest(BaseModel):
    theme_id: str
    code: str
    name_ja: str
    name_en: str | None = None
    description: str | None = None


@geo_knowledge_router.post("/issues")
def create_issue(req: IssueCreateRequest):
    return _client.insert("geo_issues", [req.model_dump()])[0]


@geo_knowledge_router.get("/issues")
def list_issues(theme_id: str = ""):
    params = {"select": "*", "order": "created_at.asc"}
    if theme_id:
        params["theme_id"] = f"eq.{theme_id}"
    return {"issues": _client.select("geo_issues", params)}


# ── Knowledge Items ───────────────────────────────────────────────────

class KnowledgeItemCreateRequest(BaseModel):
    knowledge_type: str
    title: str
    structured_content: dict
    information_class: str
    created_by: str
    summary: str | None = None
    confidence: str = "判断不能"
    confidence_rationale: str | None = None
    is_demo: bool = False


@geo_knowledge_router.post("/items")
def create_knowledge_item(req: KnowledgeItemCreateRequest):
    return _wrap_value_error(geo_knowledge_service.create_knowledge_item, _client, **req.model_dump())


@geo_knowledge_router.get("/items")
def list_knowledge_items(knowledge_type: str = "", status: str = "active",
                         retrieval_status: str = "", include_demo: bool = False,
                         identity: geo_ios_identity.IosIdentity = Depends(
                             geo_ios_identity.get_optional_ios_identity)):
    """Phase E: 既定（statusを省略、または明示的にstatus="active"）ではretrieval_status=available
    も併せて適用する（それ以外はcandidate/in_review/rejected/superseded/deprecated/archived/
    restrictedのいずれかであり、通常Retrievalへ混ぜない、絶対条件）。status="candidate"等を
    明示した呼び出し（/candidates等）では、candidateが常にretrieval_status=restrictedで作成
    される既存仕様（geo_knowledge_service.create_knowledge_item）と矛盾しないよう、
    retrieval_statusのデフォルト絞り込みは適用しない（呼び出し側が明示すれば上書き可能）。

    Phase 2E §5C/D: search_knowledge_itemsだけがhuman_visibilityを適用しており、この一覧
    endpointには一切適用されていなかった（監査で確認済みのgap）。認証済みIosIdentity
    （未ログインなら匿名）由来のrequester_contextでcan_human_accessを適用する。"""
    effective_retrieval_status = retrieval_status or ("available" if (status or "active") == "active" else "")
    items = geo_knowledge_service.list_knowledge_items(
        _client, knowledge_type=knowledge_type or None, status=status or None,
        retrieval_status=effective_retrieval_status or None, include_demo=include_demo)
    ctx = geo_knowledge_access_policy.build_requester_context(
        user_id=identity.identity_id if identity.authenticated else None,
        department=identity.department if identity.authenticated else None,
        is_authenticated=identity.authenticated)
    items = [i for i in items if geo_knowledge_access_policy.can_human_access(i, requester_context=ctx)]
    return {"items": items}


def _get_item_for_read(knowledge_item_id: str, *, include_non_retrievable: bool, actor_role: str,
                        requester_context: dict = None) -> dict | None:
    """Phase E: Direct Fetch Bypass対策。既定ではis_retrievable（status=active AND
    retrieval_status=available）な項目のみ返す。candidate/rejected/restricted等をIDで直接見る
    必要がある場合（Knowledge Review Inbox等の管理用途）は、include_non_retrievable=trueかつ
    REVIEWER_ROLES（expert_reviewer/knowledge_steward、administratorは常に許可）を持つ
    actor_roleの指定を要求する——「URL直打ちで取れてしまう」問題(§13D)を塞ぐ。

    Phase 2E §5D: 上記は既にretrieval_status/statusのBypass対策だったが、human_visibility
    は一切見ていなかった（監査で確認済みのgap）。include_non_retrievable=false（通常の閲覧
    経路）にのみcan_human_accessを追加する——include_non_retrievable=true側はReviewer
    role前提の別の信頼境界であり、レビュー目的でvisibilityを問わず見られること自体は
    従来通りでよいため変更しない。"""
    item = geo_knowledge_service.get_knowledge_item(_client, knowledge_item_id)
    if not item:
        return None
    if geo_knowledge_access_policy.is_retrievable(item):
        if not geo_knowledge_access_policy.can_human_access(item, requester_context=requester_context):
            return None
        return item
    if include_non_retrievable:
        geo_role_guard.require_role(actor_role, geo_knowledge_service.REVIEWER_ROLES)
        return item
    return None


@geo_knowledge_router.get("/items/{knowledge_item_id}")
def get_knowledge_item(knowledge_item_id: str, include_non_retrievable: bool = False, actor_role: str = "viewer",
                        identity: geo_ios_identity.IosIdentity = Depends(
                            geo_ios_identity.get_optional_ios_identity)):
    ctx = geo_knowledge_access_policy.build_requester_context(
        user_id=identity.identity_id if identity.authenticated else None,
        department=identity.department if identity.authenticated else None,
        is_authenticated=identity.authenticated)
    try:
        item = _get_item_for_read(
            knowledge_item_id, include_non_retrievable=include_non_retrievable, actor_role=actor_role,
            requester_context=ctx)
    except geo_role_guard.RoleDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    if not item:
        raise HTTPException(status_code=404, detail="knowledge_item_idが見つかりません")
    return item


class KnowledgeItemIssueLinkRequest(BaseModel):
    issue_id: str


@geo_knowledge_router.post("/items/{knowledge_item_id}/issues")
def link_knowledge_item_issue(knowledge_item_id: str, req: KnowledgeItemIssueLinkRequest):
    return geo_knowledge_service.link_to_issue(_client, knowledge_item_id=knowledge_item_id, issue_id=req.issue_id)


class KnowledgeItemReviewRequest(BaseModel):
    # 互換性のため受け取るのみ。認証済みidentityが確定した後は、authorizationにも
    # reviewer_id/reviewer_roleの記録にも一切使わない（Human Gate #2 P0 Closure参照）。
    reviewer_id: str | None = None
    reviewer_role: str | None = None
    review_decision: str
    review_comment: str
    evidence_adequacy: str
    applicability_checked: bool
    change_diff: dict | None = None


@geo_knowledge_router.post("/items/{knowledge_item_id}/review")
def review_knowledge_item(knowledge_item_id: str, req: KnowledgeItemReviewRequest,
                           identity: geo_ios_identity.IosIdentity = Depends(
                               geo_ios_identity.verify_ios_identity)):
    """Human Gate #2 P0 Closure（ユーザー指示、2026-09-23、Post-Freeze Review Platform
    Audit Finding再発防止対応）: このendpointは`geo_knowledge_api.py`モジュール冒頭の
    docstringが"Development-only role control...本番運用では使用しないこと"と明記する
    従来の認証境界（request body自己申告reviewer_role）のみで動いていたが、実際には
    api_server.pyへ本番同様にmountされ、フロントエンド（geoKnowledgeApi.ts）からも
    直接呼ばれる、Learning Candidate→Knowledgeの最終Trust Boundary（Human Gate #2）
    だった。Approval/Review/Learning-candidate routerと同じgeo_ios_identity認証境界へ
    統合し、reviewer_id/reviewer_roleは監査用のmismatch検知にのみ使う
    （authorizationの根拠にはidentity.identity_id/identity.assigned_roleのみを使う）。

    geo_knowledge_api.pyのその他のendpoint（Entity解決・Connector等）は今回のRemediation
    スコープ外——このendpointとその直接依存経路（geo_knowledge_service.review_knowledge_
    item内のlineage検証）のみを対象とする。"""
    mismatch = geo_ios_identity.check_role_mismatch(identity, req.reviewer_role)
    payload = req.model_dump()
    payload["reviewer_id"] = identity.identity_id
    payload["reviewer_role"] = identity.assigned_role
    result = _wrap_value_error(geo_knowledge_service.review_knowledge_item, _client,
                                knowledge_item_id=knowledge_item_id, **payload)
    if mismatch:
        result = {**result, "_role_mismatch_warning": mismatch}
    return result


class RetrievalStatusChangeRequest(BaseModel):
    retrieval_status: str
    actor_id: str
    reason: str | None = None


class ConfirmAiApprovalRequest(BaseModel):
    note: str = ""


@geo_knowledge_router.post("/items/{knowledge_item_id}/confirm-ai-approval")
def confirm_ai_approval(knowledge_item_id: str, req: ConfirmAiApprovalRequest,
                        identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    """AI暫定承認の項目を、人が確認し、承認者を人に置き換える。認証済みの人の権限（専門家レビュー担当・
    ナレッジ管理担当）が必要。内容に問題があれば、このendpointではなく、通常の人の承認経路
    （/items/{id}/review）で、非推奨・争いあり等へ変更する。"""
    try:
        return geo_knowledge_ai_review_service.confirm_by_human(
            _client, knowledge_item_id, human_id=identity.identity_id, human_role=identity.assigned_role,
            note=req.note)
    except geo_role_guard.RoleDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class ExpertCommentRequest(BaseModel):
    comment: str


@geo_knowledge_router.get("/items/{knowledge_item_id}/expert-comments")
def list_expert_comments(knowledge_item_id: str):
    return {"comments": geo_knowledge_ai_review_service.list_expert_comments(_client, knowledge_item_id)}


@geo_knowledge_router.post("/items/{knowledge_item_id}/expert-comments")
def add_expert_comment(knowledge_item_id: str, req: ExpertCommentRequest,
                       identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    """専門家のコメントを残す（認証済みの利用者）。内容や状態は、コメントだけでは変わらない。コメントは、
    AIの再評価の材料になる。"""
    try:
        return geo_knowledge_ai_review_service.add_expert_comment(
            _client, knowledge_item_id, author_id=identity.identity_id, author_role=identity.assigned_role,
            comment=req.comment)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@geo_knowledge_router.patch("/items/{knowledge_item_id}/retrieval-status")
def set_knowledge_item_retrieval_status(knowledge_item_id: str, req: RetrievalStatusChangeRequest):
    """Phase2B-2: Knowledge Item個別のRetrieval Governance操作。内容の正誤判定（Human Approval）
    ではなく、AIが参照してよいか（available/restricted/deprecated/archived）を管理する運用操作。
    lifecycle status（candidate/in_review/active/...）には一切触れない。"""
    return _wrap_value_error(geo_knowledge_service.set_retrieval_status, _client,
                              knowledge_item_id=knowledge_item_id, **req.model_dump())


@geo_knowledge_router.get("/candidates")
def list_knowledge_candidates(knowledge_type: str = ""):
    """未承認候補（candidate/in_review）一覧。Candidate Review Queue画面向け。"""
    candidates = geo_knowledge_service.list_knowledge_items(
        _client, knowledge_type=knowledge_type or None, status="candidate", include_demo=True)
    in_review = geo_knowledge_service.list_knowledge_items(
        _client, knowledge_type=knowledge_type or None, status="in_review", include_demo=True)
    return {"candidates": candidates + in_review}


# ── Claims / Evidence ─────────────────────────────────────────────────

class ClaimCreateRequest(BaseModel):
    claim_text: str
    information_class: str
    origin_type: str
    actor_id: str
    origin_id: str | None = None
    normalized_claim: str | None = None
    subject_entity_id: str | None = None
    predicate: str | None = None
    object_entity_id: str | None = None
    object_value: dict | None = None
    confidence: str = "判断不能"
    is_demo: bool = False


@geo_knowledge_router.post("/claims")
def create_claim(req: ClaimCreateRequest):
    return _wrap_value_error(geo_claim_service.create_claim, _client, **req.model_dump())


@geo_knowledge_router.get("/claims/{claim_id}")
def get_claim(claim_id: str):
    return _wrap_value_error(geo_claim_service.get_claim_with_evidence, _client, claim_id)


class EvidenceLinkRequest(BaseModel):
    evidence_role: str
    actor_id: str
    document_chunk_id: str | None = None
    event_id: str | None = None
    observation_id: str | None = None
    directness: str = "direct"


@geo_knowledge_router.post("/claims/{claim_id}/evidence")
def link_evidence(claim_id: str, req: EvidenceLinkRequest):
    return _wrap_value_error(geo_claim_service.link_evidence, _client, claim_id=claim_id, **req.model_dump())


@geo_knowledge_router.patch("/claims/{claim_id}/retrieval-status")
def set_claim_retrieval_status(claim_id: str, req: RetrievalStatusChangeRequest):
    """Phase2B-2: Claim個別のRetrieval Governance操作。Sourceのreference_availabilityが
    availableでも、この特定Claimだけをrestricted/deprecated/archivedにする、といった
    個別判断に使う。内容の正誤判定ではなく参照可否を管理する運用操作。"""
    return _wrap_value_error(geo_claim_service.set_retrieval_status, _client,
                              claim_id=claim_id, **req.model_dump())


class ClaimIssueLinkRequest(BaseModel):
    issue_id: str


@geo_knowledge_router.post("/claims/{claim_id}/issues")
def link_claim_issue(claim_id: str, req: ClaimIssueLinkRequest):
    """Phase2B-2: geo_claim_issues経由でIssueに紐付ける（External Reference/Reference Claimを
    Case横断でIssue単位に取得できるようにするため）。"""
    return _wrap_value_error(geo_claim_service.link_claim_to_issue, _client,
                              claim_id=claim_id, issue_id=req.issue_id)


@geo_knowledge_router.post("/documents/{document_id}/chunk")
def chunk_document(document_id: str):
    return {"chunks": _wrap_value_error(geo_claim_service.chunk_document, _client, document_id)}


# ── Cases / Hypotheses ────────────────────────────────────────────────

class CaseCreateRequest(BaseModel):
    case_title: str
    original_question: str
    actor_id: str
    scope: str | None = None
    analyst: str | None = None
    is_demo: bool = False


@geo_knowledge_router.post("/cases")
def create_case(req: CaseCreateRequest):
    return geo_case_service.create_case(_client, **req.model_dump())


@geo_knowledge_router.get("/cases")
def list_cases(status: str = "", include_demo: bool = False):
    return {"cases": geo_case_service.list_cases(_client, status=status or None, include_demo=include_demo)}


@geo_knowledge_router.get("/cases/{case_id}")
def get_case(case_id: str):
    case = geo_case_service.get_case(_client, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="case_idが見つかりません")
    return case


class CaseIssueLinkRequest(BaseModel):
    issue_id: str


@geo_knowledge_router.post("/cases/{case_id}/issues")
def link_case_issue(case_id: str, req: CaseIssueLinkRequest):
    return geo_case_service.link_case_to_issue(_client, case_id=case_id, issue_id=req.issue_id)


class CaseCloseRequest(BaseModel):
    actor_id: str
    final_output: dict | None = None
    assessment: str | None = None
    business_implications: str | None = None


@geo_knowledge_router.post("/cases/{case_id}/close")
def close_case(case_id: str, req: CaseCloseRequest):
    return geo_case_service.close_case(_client, case_id=case_id, **req.model_dump())


class CaseOutcomeRequest(BaseModel):
    actor_id: str
    actual_outcome: str
    post_case_evaluation: dict


@geo_knowledge_router.post("/cases/{case_id}/outcome")
def record_case_outcome(case_id: str, req: CaseOutcomeRequest):
    return geo_case_service.record_case_outcome(_client, case_id=case_id, **req.model_dump())


class HypothesisCreateRequest(BaseModel):
    hypothesis_statement: str
    hypothesis_type: str
    actor_id: str
    rationale: str | None = None
    assumptions: list[str] = []
    confirmation_indicators: list[str] = []
    falsification_indicators: list[str] = []
    confidence: str = "判断不能"


@geo_knowledge_router.post("/cases/{case_id}/hypotheses")
def create_hypothesis(case_id: str, req: HypothesisCreateRequest):
    return _wrap_value_error(geo_case_service.create_hypothesis, _client, case_id=case_id, **req.model_dump())


@geo_knowledge_router.get("/cases/{case_id}/hypotheses")
def list_hypotheses(case_id: str):
    return {"hypotheses": geo_case_service.list_hypotheses(_client, case_id)}


class HypothesisEvidenceRequest(BaseModel):
    claim_id: str
    stance: str


@geo_knowledge_router.post("/hypotheses/{hypothesis_id}/evidence")
def attach_hypothesis_evidence(hypothesis_id: str, req: HypothesisEvidenceRequest):
    return _wrap_value_error(geo_case_service.attach_evidence_to_hypothesis, _client,
                              hypothesis_id=hypothesis_id, **req.model_dump())


class HypothesisEvaluateRequest(BaseModel):
    status: str
    evaluation_result: str
    actor_id: str


@geo_knowledge_router.post("/hypotheses/{hypothesis_id}/evaluate")
def evaluate_hypothesis(hypothesis_id: str, req: HypothesisEvaluateRequest):
    return _wrap_value_error(geo_case_service.evaluate_hypothesis, _client,
                              hypothesis_id=hypothesis_id, **req.model_dump())


# ── Foundation Pack / Knowledge Gap / Candidate抽出 ───────────────────

@geo_knowledge_router.post("/cases/{case_id}/foundation-pack")
def build_foundation_pack(case_id: str, include_demo: bool = False):
    return _wrap_value_error(geo_foundation_pack_service.build_foundation_pack, _client, case_id,
                              include_demo=include_demo)


class DetectGapsRequest(BaseModel):
    actor_id: str


@geo_knowledge_router.post("/cases/{case_id}/detect-gaps")
def detect_deterministic_gaps(case_id: str, req: DetectGapsRequest):
    return {"gaps": _wrap_value_error(gap_service.detect_and_create_deterministic_gaps, _client,
                                       case_id, actor_id=req.actor_id)}


@geo_knowledge_router.post("/cases/{case_id}/detect-gaps/analytical")
def detect_analytical_gaps(case_id: str, req: DetectGapsRequest):
    _require_azure()
    return {"gaps": _wrap_value_error(gap_service.generate_analytical_gap_candidates, _client,
                                       _azure_client, _model, case_id, actor_id=req.actor_id)}


class ExtractCandidatesRequest(BaseModel):
    actor_id: str


@geo_knowledge_router.post("/cases/{case_id}/extract-candidates")
def extract_candidates(case_id: str, req: ExtractCandidatesRequest):
    _require_azure()
    return _wrap_value_error(candidate_service.extract_candidates_from_case, _client,
                              _azure_client, _model, case_id, actor_id=req.actor_id)


@geo_knowledge_router.get("/cases/{case_id}/gaps")
def list_gaps(case_id: str):
    return {"gaps": _client.select("geo_knowledge_gaps", {"select": "*", "case_id": f"eq.{case_id}"})}


# ── CSV/Excel/JSON手動Import ──────────────────────────────────────────

@geo_knowledge_router.post("/import/preview")
async def import_preview(file: UploadFile = File(...)):
    raw_bytes = await file.read()
    return _wrap_value_error(csv_connector.preview, raw_bytes, file.filename)


@geo_knowledge_router.post("/import/run")
async def import_run(target_table: str, column_mapping: str, actor_id: str,
                      file: UploadFile = File(...), is_demo: bool = False):
    """column_mapping はJSON文字列（例: '{"国名":"canonical_name"}'）で受け取る。"""
    import json as _json
    raw_bytes = await file.read()
    mapping = _json.loads(column_mapping)
    return _wrap_value_error(csv_connector.import_rows, _client, raw_bytes, file.filename,
                              target_table=target_table, column_mapping=mapping, actor_id=actor_id,
                              is_demo=is_demo)


# ── Audit Log（既存geo_audit_logsの参照） ─────────────────────────────

@geo_knowledge_router.get("/audit-log")
def list_audit_log(target_table: str = "", limit: int = 50):
    params = {"select": "*", "order": "created_at.desc", "limit": str(limit)}
    if target_table:
        params["target_table"] = f"eq.{target_table}"
    return {"logs": _client.select("geo_audit_logs", params)}


# ── Phase2A: Connector / Ingestion Run / Raw Snapshot ─────────────────

class RunWorldBankConnectorRequest(BaseModel):
    theme_id: str
    entity_id: str
    country_code: str
    date_range: str = "2015:2026"
    requested_by: str = "system"
    is_demo: bool = False


@geo_knowledge_router.post("/connectors/world_bank/run")
def run_world_bank_connector(req: RunWorldBankConnectorRequest):
    return _wrap_value_error(geo_connector_world_bank.run_world_bank_ingestion, _client, **req.model_dump())


class RunOfacConnectorRequest(BaseModel):
    theme_id: str
    max_records: int | None = None
    requested_by: str = "system"
    is_demo: bool = False


@geo_knowledge_router.post("/connectors/ofac/run")
def run_ofac_connector(req: RunOfacConnectorRequest):
    return _wrap_value_error(geo_connector_ofac.run_ofac_ingestion, _client, **req.model_dump())


class RunImfConnectorRequest(BaseModel):
    theme_id: str
    entity_id: str
    country_code: str
    requested_by: str = "system"
    is_demo: bool = False


@geo_knowledge_router.post("/connectors/imf/run")
def run_imf_connector(req: RunImfConnectorRequest):
    return _wrap_value_error(geo_connector_imf.run_imf_ingestion, _client, **req.model_dump())


class RunUnComtradeConnectorRequest(BaseModel):
    theme_id: str
    reporter_entity_id: str
    partner_entity_id: str | None = None
    requested_by: str = "system"
    is_demo: bool = False


@geo_knowledge_router.post("/connectors/un_comtrade/run")
def run_un_comtrade_connector(req: RunUnComtradeConnectorRequest):
    return _wrap_value_error(geo_connector_un_comtrade.run_un_comtrade_ingestion, _client, **req.model_dump())


class RunWtoConnectorRequest(BaseModel):
    theme_id: str
    entity_id: str
    requested_by: str = "system"
    is_demo: bool = False


@geo_knowledge_router.post("/connectors/wto/run")
def run_wto_connector(req: RunWtoConnectorRequest):
    return _wrap_value_error(geo_connector_wto.run_wto_ingestion, _client, **req.model_dump())


class RunManifestoConnectorRequest(BaseModel):
    theme_id: str
    max_records: int | None = None
    requested_by: str = "system"
    is_demo: bool = False


@geo_knowledge_router.post("/connectors/manifesto/run")
def run_manifesto_connector(req: RunManifestoConnectorRequest):
    return _wrap_value_error(geo_connector_manifesto.run_manifesto_ingestion, _client, **req.model_dump())


class ReferenceAvailabilityChangeRequest(BaseModel):
    reference_availability: str
    actor_id: str


@geo_knowledge_router.patch("/sources/{source_id}/reference-availability")
def set_source_reference_availability(source_id: str, req: ReferenceAvailabilityChangeRequest):
    """Phase2B-2: Source単位でReference Policyを設定する（Steward操作。内容の正誤判定ではない）。
    既存Claimのretrieval_statusのうちavailable/restrictedのものだけを一括再同期する
    （deprecated/archivedは個別の除外理由があるため復活させない）。"""
    return _wrap_value_error(geo_connector_base.set_reference_availability, _client,
                              source_id=source_id, **req.model_dump())


@geo_knowledge_router.get("/sources")
def list_sources(connector_type: str = "", limit: int = 100):
    """geo_source_registry一覧（Phase2B: connector_enabled/credential_configuredを含む）。
    フロントエンドでConnector実行ボタンのdisabled判定に使う。"""
    params = {"select": "*", "order": "name.asc", "limit": str(limit)}
    if connector_type:
        params["connector_type"] = f"eq.{connector_type}"
    return {"sources": _client.select("geo_source_registry", params)}


@geo_knowledge_router.get("/ingestion-runs")
def list_ingestion_runs(source_id: str = "", status: str = "", limit: int = 50):
    params = {"select": "*", "order": "started_at.desc", "limit": str(limit)}
    if source_id:
        params["source_id"] = f"eq.{source_id}"
    if status:
        params["status"] = f"eq.{status}"
    return {"ingestion_runs": _client.select("geo_ingestion_runs", params)}


@geo_knowledge_router.get("/ingestion-runs/{ingestion_run_id}")
def get_ingestion_run(ingestion_run_id: str):
    snapshots = _client.select("geo_ingestion_run_snapshots", {
        "select": "*", "ingestion_run_id": f"eq.{ingestion_run_id}",
    })
    run = _client.select("geo_ingestion_runs", {
        "select": "*", "ingestion_run_id": f"eq.{ingestion_run_id}", "limit": "1",
    })
    if not run:
        raise HTTPException(status_code=404, detail="ingestion_run_idが見つかりません")
    return {"ingestion_run": run[0], "raw_snapshots": snapshots}


@geo_knowledge_router.get("/raw-snapshots/{raw_snapshot_id}")
def get_raw_snapshot(raw_snapshot_id: str):
    rows = _client.select("geo_raw_snapshots", {
        "select": "*", "raw_snapshot_id": f"eq.{raw_snapshot_id}", "limit": "1",
    })
    if not rows:
        raise HTTPException(status_code=404, detail="raw_snapshot_idが見つかりません")
    return rows[0]


@geo_knowledge_router.get("/entity-match-candidates")
def list_entity_match_candidates(match_status: str = "", limit: int = 50):
    params = {"select": "*", "order": "created_at.desc", "limit": str(limit)}
    if match_status:
        params["match_status"] = f"eq.{match_status}"
    return {"candidates": _client.select("geo_entity_match_candidates", params)}


class ResolveEntityMatchCandidateRequest(BaseModel):
    matched_entity_id: str | None = None
    resolved_by: str
    actor_role: str


@geo_knowledge_router.post("/entity-match-candidates/{match_candidate_id}/resolve")
def resolve_entity_match_candidate(match_candidate_id: str, req: ResolveEntityMatchCandidateRequest):
    """Entity Match Candidateの解決は必ず人間のRole操作を経る（自動Mergeはしない）。
    ここではCandidateの解決記録のみ行い、実際のEntity統合はgeo_entity_service.merge_entities
    （別途knowledge_steward roleでの明示操作）に委ねる。"""
    geo_role_guard.require_role(req.actor_role, ("knowledge_steward",))
    _client.update("geo_entity_match_candidates", {"match_candidate_id": f"eq.{match_candidate_id}"}, {
        "matched_entity_id": req.matched_entity_id, "resolved_by": req.resolved_by,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"ok": True}


@geo_knowledge_router.get("/sanctions-entries")
def list_sanctions_entries(current_status: str = "", limit: int = 50):
    params = {"select": "*", "order": "last_seen_at.desc", "limit": str(limit)}
    if current_status:
        params["current_status"] = f"eq.{current_status}"
    return {"entries": _client.select("geo_sanctions_entries", params)}


@geo_knowledge_router.get("/sanctions-entries/{sanctions_entry_id}/versions")
def list_sanctions_entry_versions(sanctions_entry_id: str):
    return {"versions": _client.select("geo_sanctions_entry_versions", {
        "select": "*", "sanctions_entry_id": f"eq.{sanctions_entry_id}", "order": "observed_at.desc",
    })}


# ── Phase2A: Metrics ───────────────────────────────────────────────────

@geo_knowledge_router.get("/metrics/observations")
def list_metric_observations(metric_definition_id: str = "", entity_id: str = "", include_demo: bool = False):
    return {"observations": geo_metric_service.list_latest_observations(
        _client, metric_definition_id=metric_definition_id or None, entity_id=entity_id or None,
        include_demo=include_demo)}


# ── Phase2A: Hybrid Search ─────────────────────────────────────────────

@geo_knowledge_router.get("/search/entities")
def search_entities(q: str, entity_types: str = "", include_demo: bool = False, limit: int = 20):
    types = entity_types.split(",") if entity_types else None
    return _wrap_value_error(geo_search_service.search_entities, _client, q, entity_types=types,
                              include_demo=include_demo, limit=limit)


@geo_knowledge_router.get("/search/knowledge-items")
def search_knowledge_items(q: str, status: str = "active", knowledge_types: str = "",
                           include_demo: bool = False, limit: int = 20,
                           requester_user_id: str = "", requester_department: str = ""):
    """Phase E: human_visibilityを適用する（Intelligence Library/検索、§13C）。既存auth基盤が
    無いため、requester_user_id/requester_departmentは呼び出し側（フロントエンド）が把握できる
    範囲を明示で渡す想定——未指定の場合は匿名扱いとしcompany可視性のみ許可する（安全側）。"""
    status_list = status.split(",") if status else None
    types = knowledge_types.split(",") if knowledge_types else None
    result = _wrap_value_error(geo_search_service.search_knowledge_items, _client, q, status=status_list,
                              knowledge_types=types, include_demo=include_demo, limit=limit)
    ctx = geo_knowledge_access_policy.build_requester_context(
        user_id=requester_user_id or None, department=requester_department or None)
    accessible = [
        r for r in result["results"]
        if geo_knowledge_access_policy.is_retrievable(r["knowledge_item"])
        and geo_knowledge_access_policy.can_human_access(r["knowledge_item"], requester_context=ctx)
    ]
    geo_knowledge_access_policy.log_access_filter_summary(
        _client, actor=requester_user_id or "anonymous", action="knowledge_search_access_filter",
        total_before=len(result["results"]), total_after=len(accessible), context={"query": q})
    return {**result, "results": accessible}


@geo_knowledge_router.get("/search/natural")
def search_natural(q: str, status: str = "active", knowledge_types: str = "", include_demo: bool = False,
                   identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.get_optional_ios_identity)):
    """自然言語検索（エンティティ・ナレッジ項目・レポートを横断し、理由つきで返す）。
    ナレッジ項目は、通常検索と同じアクセス制御（承認済み・参照可能・human_visibility）を通ったものだけを
    AIへ渡す。AIが使えない・失敗した場合は503/500ではなく、画面側が従来のHybrid検索へ切り替えられるよう
    503を返す。"""
    _require_azure()
    ctx = geo_knowledge_access_policy.build_requester_context(
        user_id=identity.identity_id if identity.authenticated else None,
        department=identity.department if identity.authenticated else None,
        is_authenticated=identity.authenticated)
    try:
        return geo_knowledge_natural_search.natural_search(
            _client, _azure_client, _model, q, status=status.split(",") if status else None,
            knowledge_types=knowledge_types.split(",") if knowledge_types else None,
            include_demo=include_demo, requester_context=ctx)
    except Exception as exc:  # noqa: BLE001 — AIの失敗は、画面が従来検索へ切り替えられる形で返す
        raise HTTPException(status_code=503, detail=f"自然言語検索に失敗しました: {str(exc)[:120]}") from exc


@geo_knowledge_router.get("/search/claims")
def search_claims(q: str, include_demo: bool = False, limit: int = 20):
    return _wrap_value_error(geo_search_service.search_claims, _client, q, include_demo=include_demo, limit=limit)


@geo_knowledge_router.get("/retrieval-runs/{retrieval_run_id}")
def get_retrieval_run(retrieval_run_id: str):
    run = _client.select("geo_retrieval_runs", {
        "select": "*", "retrieval_run_id": f"eq.{retrieval_run_id}", "limit": "1",
    })
    if not run:
        raise HTTPException(status_code=404, detail="retrieval_run_idが見つかりません")
    results = _client.select("geo_retrieval_results", {
        "select": "*", "retrieval_run_id": f"eq.{retrieval_run_id}", "order": "rank.asc",
    })
    return {"retrieval_run": run[0], "results": results}


@geo_knowledge_router.get("/cases/{case_id}/retrieval-runs")
def list_case_retrieval_runs(case_id: str, limit: int = 10):
    return {"retrieval_runs": _client.select("geo_retrieval_runs", {
        "select": "*", "case_id": f"eq.{case_id}", "order": "started_at.desc", "limit": str(limit),
    })}


@geo_knowledge_router.get("/freshness/knowledge-items/{knowledge_item_id}")
def get_knowledge_item_freshness(knowledge_item_id: str, include_non_retrievable: bool = False,
                                 actor_role: str = "viewer"):
    try:
        item = _get_item_for_read(
            knowledge_item_id, include_non_retrievable=include_non_retrievable, actor_role=actor_role)
    except geo_role_guard.RoleDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    if not item:
        raise HTTPException(status_code=404, detail="knowledge_item_idが見つかりません")
    return {"freshness": geo_freshness_service.classify_knowledge_freshness(item)}


@geo_knowledge_router.get("/freshness/sources/{source_id}")
def get_source_freshness(source_id: str):
    rows = _client.select("geo_source_registry", {"select": "*", "source_id": f"eq.{source_id}", "limit": "1"})
    if not rows:
        raise HTTPException(status_code=404, detail="source_idが見つかりません")
    return geo_freshness_service.classify_source_freshness(rows[0])


# ── Phase2A: Expert Engine（Case統合） ─────────────────────────────────

class ExpertAnalyzeRequest(BaseModel):
    case_id: str
    theme_id: str | None = None
    mode: str = "case_analysis"
    query_text: str
    draft_text: str | None = None
    profile: str | None = None  # v1.2: quick/standard/deep。未指定時は"standard"
    execution_mode: str | None = None  # v1.2: staged/monolithic。未指定時はGEO_EXPERT_EXECUTION_MODE環境変数


@geo_knowledge_router.post("/expert/analyze")
def expert_analyze(req: ExpertAnalyzeRequest):
    _require_azure()
    if req.mode not in geo_expert_schema.MODES:
        raise HTTPException(status_code=400, detail=f"未対応のmodeです: {req.mode}")
    try:
        execution_mode = geo_expert_engine.resolve_execution_mode(req.execution_mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        result = geo_expert_engine.run_expert_request_with_mode(
            _client, _azure_client, _model, req.mode, req.theme_id, req.query_text, req.draft_text,
            case_id=req.case_id, profile=req.profile or "standard", execution_mode=execution_mode,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "requestId": result["request"]["request_id"], "responseId": result["response"]["response_id"],
        "response": result["response"]["response_json"],
        "qualityEvaluation": {
            "passedCount": result["quality_evaluation"]["passed_count"],
            "failedCount": result["quality_evaluation"]["failed_count"],
            "checks": result["quality_evaluation"]["checks"],
            "completenessStatus": result["quality_evaluation"].get("completeness_status"),
            "initialFailedChecks": result["quality_evaluation"].get("initial_failed_checks"),
            "repairAttempted": result["quality_evaluation"].get("repair_attempted"),
            "remainingFailedChecks": result["quality_evaluation"].get("remaining_failed_checks"),
        },
        "executionMetadata": {
            "executionMode": (result.get("execution_metadata") or {}).get("execution_mode"),
            "profile": (result.get("execution_metadata") or {}).get("profile"),
            "stage2Modules": ((result.get("execution_metadata") or {}).get("executed") or {}).get("stage2_modules"),
        },
    }


class ExtractExternalCandidatesRequest(BaseModel):
    claim_ids: list[str]
    actor_id: str


@geo_knowledge_router.post("/candidates/extract-from-claims")
def extract_candidates_from_claims(req: ExtractExternalCandidatesRequest):
    _require_azure()
    return _wrap_value_error(candidate_service.extract_candidates_from_external_claims, _client,
                              _azure_client, _model, req.claim_ids, actor_id=req.actor_id)
