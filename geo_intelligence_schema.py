"""地政学インテリジェンス基盤 Phase1: Geo Intelligence Agent Request/Response Schema。

外部アプリ（Sustainability Intelligence/ERM/SCM/PA等、Phase1時点ではまだ未接続）から
地政学Knowledgeを問い合わせるための正規化されたSchema。既存のGeopolitical Expert AI
（geo_expert_schema.py、Stakeholder/Objective/Constraint等を用いた高度分析）とは別物で、
Geo Intelligence AgentはExpert AIの外部からの直接呼び出し窓口にはしない。

request_type/knowledge_sufficiency/statusはstr継承のEnumにし、将来の値追加は
このファイルへの追記のみで完結するようにする（呼び出し側のロジック変更は不要）。
"""
from enum import Enum

from pydantic import BaseModel, Field


class GeoIntelligenceRequestType(str, Enum):
    weekly_article = "weekly_article"
    user_question = "user_question"
    # 週次横断探索（Sustainability週次モニタリング向け）。user_questionの「特定の問いへの回答」
    # とは異なり、「指定期間・Sustainabilityテーマ・重点地域をScopeとしてGeo側の既存Knowledge/
    # Report/Intelligenceを横断検索し、関連する地政学動向を複数件返す」処理モード。
    weekly_monitoring = "weekly_monitoring"


class KnowledgeSufficiency(str, Enum):
    sufficient = "sufficient"
    partial = "partial"
    insufficient = "insufficient"


class GeoIntelligenceStatus(str, Enum):
    completed = "completed"
    partial = "partial"
    failed = "failed"


class GeoIntelligenceRequest(BaseModel):
    """外部アプリからのRequest。request_id/requested_atは未指定であればAgent側で補完する
    （normalize_request参照）。

    request_type=user_question: question必須（既存、後方互換を維持）。
    request_type=weekly_monitoring: questionは不要（省略可）。代わりにperiod_start/period_end/
    sustainability_themes/priority_geographies/business_contextで週次探索のScopeを指定する
    （必須項目チェックはPydanticではなくgeo_intelligence_service.normalize_request側で
    request_typeごとに行う）。"""
    request_id: str | None = None
    requester_app: str
    request_type: GeoIntelligenceRequestType
    question: str | None = None
    country_region: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    # Geo Chat Bridge向け（Intelligence AI画面の接続）: 呼び出し元が既にtheme_idを把握している場合、
    # country_region/themesのテキストマッチだけに頼らず、geo_issues.theme_id経由で直接issue_idsを
    # 解決できるようにする（resolve_entities_and_issuesでcountry_region/themes解決に追加する形、
    # 既存の解決方法は変更しない）。省略時は既存動作のまま。
    theme_id: str | None = None
    context: str | None = None
    time_horizon: str | None = None
    source_refs: list[str] = Field(default_factory=list)
    analysis_requested: bool = True
    requested_at: str | None = None
    # weekly_monitoring専用（user_questionでは未使用）
    period_start: str | None = None
    period_end: str | None = None
    sustainability_themes: list[str] = Field(default_factory=list)
    priority_geographies: list[str] = Field(default_factory=list)
    business_context: str | None = None
    # Phase B-B: Cross-Department Inquiry Receiver（他部門Department Intelligence AIからの
    # user_question問い合わせ向け）。省略時（既存呼び出し元）は挙動を一切変えない
    # （source_department未指定ならgeo_department_intelligence_serviceへは分岐しない）。
    source_department: str | None = None
    source_workflow: str | None = None
    freshness_requirement: str | None = None


class GeoIntelligenceResponse(BaseModel):
    """外部アプリへのResponse。knowledge_sufficiency/geo_assessment等の本ロジックはPhase1では
    stubであり、Phase2以降で実装を差し替える前提のフィールドを含む（詳細は
    geo_intelligence_service.pyのdocstring参照）。"""
    request_id: str
    status: GeoIntelligenceStatus
    geo_assessment: str | None = None
    key_stakeholders: list[str] = Field(default_factory=list)
    political_dynamics: str | None = None
    outlook: str | None = None
    cross_domain_implications: str | None = None
    references: list[dict] = Field(default_factory=list)
    confidence: str | None = None
    knowledge_sufficiency: KnowledgeSufficiency
    knowledge_gap: dict | None = None
    # Geo Chat Bridge向け（Intelligence AI画面の接続）: sufficiencyからの推測ではなく、実際に
    # Expert AIブランチ（generate_analysis→should_call_expert_ai）を通ったかどうかを示す。
    expert_ai_called: bool = False
    error: dict | None = None
    completed_at: str | None = None
    # weekly_monitoring専用（user_questionではNone/省略のまま、既存Contractを壊さない）
    period_start: str | None = None
    period_end: str | None = None
    relevant_intelligence: list[dict] | None = None
    truncated_count: int | None = None
    # Phase B-B: Cross-Department Inquiry Receiver向けoptional metadata。source_department
    # 未指定のRequest（既存呼び出し元）では常にNoneのまま（既存clientは無視して動く）。
    department_intelligence: dict | None = None
