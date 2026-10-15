// 地政学ナレッジ＆インテリジェンスDB Phase1のAPIクライアント。
// 既存 lib/api.ts とは意図的に分離する（新規エンドポイントが多く、既存ファイルが
// 既に1000行を超えているため。ホスト設定(API_BASE)のみ重複させ、既存ファイルは無改変にする）。
import { API_BASE as API_BASE_URL } from "@/lib/apiBase";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getIosAuthHeaders } from "@/lib/geoIosAuthHeader";

const API_BASE = API_BASE_URL;

// 2026-09-24修正: このファイルのほとんどのendpointは認証不要だが、review_knowledge_item
// （POST /items/{id}/review、Human Gate #2）だけはgeo_ios_identity.verify_ios_identity
// 必須。他endpointにヘッダーが付いても無害なため、fetchJson全体に一括で付与する
// （geoIosReviewApi.ts等と同じ取り残されバグの修正）。
async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...getIosAuthHeaders(), ...init?.headers },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `API error ${res.status}`);
  }
  return res.json();
}

function postJson<T>(path: string, body: unknown): Promise<T> {
  return fetchJson<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function patchJson<T>(path: string, body: unknown): Promise<T> {
  return fetchJson<T>(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export interface GeoEntity {
  entity_id: string;
  entity_type: string;
  canonical_name: string;
  name_en?: string | null;
  name_ja?: string | null;
  country_code?: string | null;
  status: string;
  is_demo: boolean;
  local_name?: string | null;
  abbreviations?: string[] | null;
  former_names?: string[] | null;
  active_from?: string | null;
  active_to?: string | null;
}

export interface GeoRelationship {
  relationship_id: string;
  from_entity_id: string;
  to_entity_id: string;
  relationship_domain: string;
  relationship_type: string;
  description?: string | null;
  confidence: string;
}

export interface GeoIssue {
  issue_id: string;
  theme_id: string;
  code: string;
  name_ja: string;
  name_en?: string | null;
}

export function useGeoIssues(themeId: string) {
  const usp = new URLSearchParams();
  if (themeId) usp.set("theme_id", themeId);
  return useQuery({
    queryKey: ["geoIssues", themeId],
    queryFn: () => fetchJson<{ issues: GeoIssue[] }>(`/api/geo/knowledge/issues?${usp.toString()}`),
    enabled: !!themeId,
  });
}

export interface GeoRelationshipGraph {
  nodes: GeoEntity[];
  edges: GeoRelationship[];
}

export function useRelationshipGraph(issueId: string, includeDemo = false) {
  const usp = new URLSearchParams();
  if (includeDemo) usp.set("include_demo", "true");
  return useQuery({
    queryKey: ["geoRelationshipGraph", issueId, includeDemo],
    queryFn: () =>
      fetchJson<GeoRelationshipGraph>(
        `/api/geo/knowledge/issues/${issueId}/relationship-graph?${usp.toString()}`,
      ),
    enabled: !!issueId,
  });
}

export interface GeoKnowledgeItem {
  knowledge_item_id: string;
  knowledge_type: string;
  title: string;
  summary?: string | null;
  structured_content: Record<string, unknown>;
  status: string;
  confidence: string;
  information_class: string;
  version: number;
  is_demo: boolean;
  // Phase2B-2: retrieval_statusはstatus（lifecycle）と独立した二軸
  retrieval_status?: string;
  origin?: string | null;
  support_count?: number;
  usage_count?: number;
  last_confirmed_at?: string | null;
  last_used_at?: string | null;
  as_of_date?: string | null;
  /** approved_byが ai:<モデル名> のとき、AI暫定承認（人は未確認） */
  approved_by?: string | null;
  confidence_rationale?: string | null;
}

export interface GeoExpertComment {
  author: string | null;
  comment: string;
  author_role?: string | null;
  created_at?: string | null;
}

/** 解釈（意図・パターン・立場の分析）を含む種類。分析の部分はAIの見立てとして扱う */
export const INTERPRETIVE_KNOWLEDGE_TYPES = [
  "actor_profile",
  "relationship_assessment",
  "leverage_card",
  "mechanism",
  "reasoning_rule",
  "historical_path_dependency",
];

export function isAiApproved(item: { approved_by?: string | null }): boolean {
  return (item.approved_by ?? "").startsWith("ai:");
}

export interface GeoCase {
  case_id: string;
  case_title: string;
  original_question: string;
  status: string;
  as_of_date: string;
}

export interface GeoHypothesis {
  hypothesis_id: string;
  case_id: string;
  hypothesis_statement: string;
  hypothesis_type: "main" | "alternative" | "rejected" | "superseded";
  status: string;
  confidence: string;
}

export interface GeoKnowledgeGap {
  gap_id: string;
  case_id: string;
  gap_category: "deterministic" | "analytical_candidate";
  gap_type: string;
  gap_description: string;
  why_needed?: string | null;
  priority: string;
  status: string;
}

// ── Development-only role control ────────────────────────────────────
// このactor_role値は認証されておらず、本番運用では使用しないこと
// （docs/geopolitical-operations-playbook.md 3章）。
export const GEO_KNOWLEDGE_ROLES = [
  "viewer",
  "analyst",
  "expert_reviewer",
  "knowledge_steward",
  "administrator",
] as const;
export type GeoKnowledgeRole = (typeof GEO_KNOWLEDGE_ROLES)[number];

// ── Entities ──────────────────────────────────────────────────────────

export function useGeoKnowledgeEntities(
  params: { entityType?: string; includeDemo?: boolean } = {},
) {
  const usp = new URLSearchParams();
  if (params.entityType) usp.set("entity_type", params.entityType);
  if (params.includeDemo) usp.set("include_demo", "true");
  return useQuery({
    queryKey: ["geoKnowledgeEntities", params],
    queryFn: () =>
      fetchJson<{ entities: GeoEntity[] }>(`/api/geo/knowledge/entities?${usp.toString()}`),
  });
}

export function useCreateGeoEntity() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      entity_type: string;
      canonical_name: string;
      actor_id: string;
      country_code?: string;
    }) => postJson<GeoEntity>("/api/geo/knowledge/entities", body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["geoKnowledgeEntities"] }),
  });
}

// ── Relationships ─────────────────────────────────────────────────────

/** エンティティの「中身」: 紐づく論点・関連するナレッジ項目（承認前の項目は件数のみ） */
export interface GeoEntityContext {
  issues: { issue_id: string; code: string; name_ja: string; name_en?: string | null; description?: string | null }[];
  knowledge_items: GeoKnowledgeItem[];
  issue_knowledge_items: GeoKnowledgeItem[];
  pending_count: number;
}

export function useGeoEntityContext(entityId: string) {
  return useQuery({
    queryKey: ["geoEntityContext", entityId],
    queryFn: () => fetchJson<GeoEntityContext>(`/api/geo/knowledge/entities/${entityId}/context`),
    enabled: !!entityId,
  });
}

export function useGeoKnowledgeRelationships(entityId: string) {
  return useQuery({
    queryKey: ["geoKnowledgeRelationships", entityId],
    queryFn: () =>
      fetchJson<{ relationships: GeoRelationship[] }>(
        `/api/geo/knowledge/relationships?entity_id=${entityId}`,
      ),
    enabled: !!entityId,
  });
}

// ── Knowledge Items / Candidates ──────────────────────────────────────

export function useGeoKnowledgeItems(
  params: { knowledgeType?: string; status?: string; includeDemo?: boolean } = {},
) {
  const usp = new URLSearchParams();
  if (params.knowledgeType) usp.set("knowledge_type", params.knowledgeType);
  if (params.status) usp.set("status", params.status);
  if (params.includeDemo) usp.set("include_demo", "true");
  return useQuery({
    queryKey: ["geoKnowledgeItems", params],
    queryFn: () =>
      fetchJson<{ items: GeoKnowledgeItem[] }>(`/api/geo/knowledge/items?${usp.toString()}`),
  });
}

// Phase 2F-2: Intelligence AI Search ResultsのPreview panel用。既存
// GET /api/geo/knowledge/items/{id}（Phase 2E enforcement済み）をそのまま呼ぶだけで、
// 新しい権限ロジックは持たない。
export function useKnowledgeItemDetail(knowledgeItemId: string) {
  return useQuery({
    queryKey: ["geoKnowledgeItemDetail", knowledgeItemId],
    queryFn: () => fetchJson<GeoKnowledgeItem>(`/api/geo/knowledge/items/${knowledgeItemId}`),
    enabled: !!knowledgeItemId,
  });
}

export function useGeoKnowledgeCandidates() {
  return useQuery({
    queryKey: ["geoKnowledgeCandidates"],
    queryFn: () => fetchJson<{ candidates: GeoKnowledgeItem[] }>("/api/geo/knowledge/candidates"),
  });
}

export function useReviewGeoKnowledgeItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      knowledgeItemId,
      ...body
    }: {
      knowledgeItemId: string;
      reviewer_id: string;
      reviewer_role: GeoKnowledgeRole;
      review_decision: string;
      review_comment: string;
      evidence_adequacy: string;
      applicability_checked: boolean;
    }) => postJson(`/api/geo/knowledge/items/${knowledgeItemId}/review`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["geoKnowledgeCandidates"] });
      queryClient.invalidateQueries({ queryKey: ["geoKnowledgeItems"] });
    },
  });
}

// ── Cases / Hypotheses ────────────────────────────────────────────────

export function useCreateGeoCase() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      case_title: string;
      original_question: string;
      actor_id: string;
      scope?: string;
    }) => postJson<GeoCase>("/api/geo/knowledge/cases", body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["geoKnowledgeCases"] }),
  });
}

export function useGeoCases() {
  return useQuery({
    queryKey: ["geoKnowledgeCases"],
    queryFn: () => fetchJson<{ cases: GeoCase[] }>("/api/geo/knowledge/cases"),
  });
}

export function useGeoCase(caseId: string) {
  return useQuery({
    queryKey: ["geoKnowledgeCase", caseId],
    queryFn: () => fetchJson<GeoCase>(`/api/geo/knowledge/cases/${caseId}`),
    enabled: !!caseId,
  });
}

export function useGeoHypotheses(caseId: string) {
  return useQuery({
    queryKey: ["geoKnowledgeHypotheses", caseId],
    queryFn: () =>
      fetchJson<{ hypotheses: GeoHypothesis[] }>(`/api/geo/knowledge/cases/${caseId}/hypotheses`),
    enabled: !!caseId,
  });
}

export function useCreateGeoHypothesis(caseId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      hypothesis_statement: string;
      hypothesis_type: "main" | "alternative";
      actor_id: string;
      rationale?: string;
    }) => postJson<GeoHypothesis>(`/api/geo/knowledge/cases/${caseId}/hypotheses`, body),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["geoKnowledgeHypotheses", caseId] }),
  });
}

export function useGeoCaseGaps(caseId: string) {
  return useQuery({
    queryKey: ["geoKnowledgeGaps", caseId],
    queryFn: () =>
      fetchJson<{ gaps: GeoKnowledgeGap[] }>(`/api/geo/knowledge/cases/${caseId}/gaps`),
    enabled: !!caseId,
  });
}

export function useBuildFoundationPack(caseId: string) {
  return useMutation({
    mutationFn: () =>
      postJson<Record<string, unknown>>(`/api/geo/knowledge/cases/${caseId}/foundation-pack`, {}),
  });
}

export function useDetectDeterministicGaps(caseId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (actorId: string) =>
      postJson<{ gaps: GeoKnowledgeGap[] }>(`/api/geo/knowledge/cases/${caseId}/detect-gaps`, {
        actor_id: actorId,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["geoKnowledgeGaps", caseId] }),
  });
}

export function useDetectAnalyticalGaps(caseId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (actorId: string) =>
      postJson<{ gaps: GeoKnowledgeGap[] }>(
        `/api/geo/knowledge/cases/${caseId}/detect-gaps/analytical`,
        { actor_id: actorId },
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["geoKnowledgeGaps", caseId] }),
  });
}

export function useExtractCandidates(caseId: string) {
  return useMutation({
    mutationFn: (actorId: string) =>
      postJson<{ created: GeoKnowledgeItem[]; skipped: unknown[] }>(
        `/api/geo/knowledge/cases/${caseId}/extract-candidates`,
        { actor_id: actorId },
      ),
  });
}

// ── Documents / Chunking ──────────────────────────────────────────────

export interface GeoDocumentChunk {
  chunk_id: string;
  document_id: string;
  chunk_index: number;
  quoted_excerpt: string;
}

export function useChunkDocument() {
  return useMutation({
    mutationFn: (documentId: string) =>
      postJson<{ chunks: GeoDocumentChunk[] }>(
        `/api/geo/knowledge/documents/${documentId}/chunk`,
        {},
      ),
  });
}

export function useCloseGeoCase(caseId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { actor_id: string; assessment?: string }) =>
      postJson<GeoCase>(`/api/geo/knowledge/cases/${caseId}/close`, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["geoKnowledgeCase", caseId] }),
  });
}

// ── Phase2A: Ingestion Run / Raw Snapshot / Connector ─────────────────

export interface GeoIngestionRun {
  ingestion_run_id: string;
  source_id: string;
  connector_type: string;
  status: string;
  started_at: string;
  completed_at?: string | null;
  fetched_count: number;
  inserted_count: number;
  updated_count: number;
  error_count: number;
  error_summary?: string | null;
}

export interface GeoSourceRegistryRow {
  source_id: string;
  name: string;
  connector_type: string | null;
  automation_allowed: boolean;
  connector_enabled: boolean;
  credential_configured: boolean;
  last_successful_ingestion_at?: string | null;
  last_failed_ingestion_at?: string | null;
  failure_count: number;
  // Phase2B-2: Source Reference Policy（内容の正誤判定ではなく参照可否の運用判断）
  reference_availability?: "available" | "restricted";
  reference_availability_set_by?: string | null;
  reference_availability_set_at?: string | null;
}

export function useGeoSources(connectorType?: string) {
  const usp = new URLSearchParams();
  if (connectorType) usp.set("connector_type", connectorType);
  return useQuery({
    queryKey: ["geoSources", connectorType],
    queryFn: () =>
      fetchJson<{ sources: GeoSourceRegistryRow[] }>(
        `/api/geo/knowledge/sources?${usp.toString()}`,
      ),
  });
}

export function useIngestionRuns(params: { sourceId?: string; status?: string } = {}) {
  const usp = new URLSearchParams();
  if (params.sourceId) usp.set("source_id", params.sourceId);
  if (params.status) usp.set("status", params.status);
  return useQuery({
    queryKey: ["geoIngestionRuns", params],
    queryFn: () =>
      fetchJson<{ ingestion_runs: GeoIngestionRun[] }>(
        `/api/geo/knowledge/ingestion-runs?${usp.toString()}`,
      ),
  });
}

export function useIngestionRunDetail(ingestionRunId: string) {
  return useQuery({
    queryKey: ["geoIngestionRun", ingestionRunId],
    queryFn: () =>
      fetchJson<{ ingestion_run: GeoIngestionRun; raw_snapshots: unknown[] }>(
        `/api/geo/knowledge/ingestion-runs/${ingestionRunId}`,
      ),
    enabled: !!ingestionRunId,
  });
}

export function useRunWorldBankConnector() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      theme_id: string;
      entity_id: string;
      country_code: string;
      date_range?: string;
      requested_by?: string;
      is_demo?: boolean;
    }) =>
      postJson<{ ingestion_run_id: string; status: string }>(
        "/api/geo/knowledge/connectors/world_bank/run",
        body,
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["geoIngestionRuns"] }),
  });
}

export function useRunOfacConnector() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      theme_id: string;
      max_records?: number;
      requested_by?: string;
      is_demo?: boolean;
    }) =>
      postJson<{ ingestion_run_id: string; status: string }>(
        "/api/geo/knowledge/connectors/ofac/run",
        body,
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["geoIngestionRuns"] }),
  });
}

// ── Phase2B: 追加Connector（IMF / UN Comtrade / WTO / Manifesto Project） ──

export function useRunImfConnector() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      theme_id: string;
      entity_id: string;
      country_code: string;
      requested_by?: string;
      is_demo?: boolean;
    }) =>
      postJson<{ ingestion_run_id: string; status: string }>(
        "/api/geo/knowledge/connectors/imf/run",
        body,
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["geoIngestionRuns"] }),
  });
}

export function useRunUnComtradeConnector() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      theme_id: string;
      reporter_entity_id: string;
      partner_entity_id?: string;
      requested_by?: string;
      is_demo?: boolean;
    }) =>
      postJson<{ ingestion_run_id: string; status: string }>(
        "/api/geo/knowledge/connectors/un_comtrade/run",
        body,
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["geoIngestionRuns"] }),
  });
}

export function useRunWtoConnector() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      theme_id: string;
      entity_id: string;
      requested_by?: string;
      is_demo?: boolean;
    }) =>
      postJson<{ ingestion_run_id: string; status: string }>(
        "/api/geo/knowledge/connectors/wto/run",
        body,
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["geoIngestionRuns"] }),
  });
}

export function useRunManifestoConnector() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      theme_id: string;
      max_records?: number;
      requested_by?: string;
      is_demo?: boolean;
    }) =>
      postJson<{ ingestion_run_id: string; status: string }>(
        "/api/geo/knowledge/connectors/manifesto/run",
        body,
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["geoIngestionRuns"] }),
  });
}

// ── Phase2A: Hybrid Search ─────────────────────────────────────────────

export interface GeoSearchEntityResult {
  entity: GeoEntity;
  entity_score: number;
  text_score: number;
  final_score: number;
}
export interface GeoSearchKnowledgeItemResult {
  knowledge_item: GeoKnowledgeItem;
  text_score: number;
  source_score: number;
  recency_score: number;
  approval_score: number;
  final_score: number;
}
export interface GeoSearchClaimResult {
  claim: {
    claim_id: string;
    claim_text: string;
    information_class: string;
    retrieval_status?: string;
    source_id?: string | null;
  };
  text_score: number;
  final_score: number;
}

export function useSearchKnowledgeItems(
  query: string,
  params: { status?: string; includeDemo?: boolean } = {},
) {
  const usp = new URLSearchParams({ q: query });
  if (params.status) usp.set("status", params.status);
  if (params.includeDemo) usp.set("include_demo", "true");
  return useQuery({
    queryKey: ["geoSearchKnowledgeItems", query, params],
    queryFn: () =>
      fetchJson<{ results: GeoSearchKnowledgeItemResult[] }>(
        `/api/geo/knowledge/search/knowledge-items?${usp.toString()}`,
      ),
    enabled: query.trim().length > 0,
  });
}

// 自然言語検索（エンティティ・ナレッジ項目・レポートを横断し、「合う理由」つきで返す）。
// 失敗（503等）のときは、画面側が従来のHybrid検索へ切り替える。
export interface NaturalSearchResponse {
  understood: string;
  entities: { entity: GeoEntity; score: number; reason: string }[];
  knowledge_items: { knowledge_item: GeoKnowledgeItem; score: number; reason: string }[];
  reports: { report: GeoReportSummary; score: number; reason: string }[];
}

export function useNaturalKnowledgeSearch(
  query: string,
  params: { status?: string; knowledgeType?: string; includeDemo?: boolean } = {},
) {
  const usp = new URLSearchParams({ q: query });
  if (params.status) usp.set("status", params.status);
  if (params.knowledgeType) usp.set("knowledge_types", params.knowledgeType);
  if (params.includeDemo) usp.set("include_demo", "true");
  return useQuery({
    queryKey: ["geoNaturalKnowledgeSearch", query, params],
    queryFn: () => fetchJson<NaturalSearchResponse>(`/api/geo/knowledge/search/natural?${usp.toString()}`),
    enabled: query.trim().length > 0,
    retry: false,
    staleTime: 60_000,
  });
}

export function useSearchEntities(query: string, params: { includeDemo?: boolean } = {}) {
  const usp = new URLSearchParams({ q: query });
  if (params.includeDemo) usp.set("include_demo", "true");
  return useQuery({
    queryKey: ["geoSearchEntities", query, params],
    queryFn: () =>
      fetchJson<{ results: GeoSearchEntityResult[] }>(
        `/api/geo/knowledge/search/entities?${usp.toString()}`,
      ),
    enabled: query.trim().length > 0,
  });
}

// ── Phase2 Closeout: Intelligence LibraryへのReport統合 ────────────────

// Phase 2D（レポート登録）: uploaded_only/in_review/approvedの3値のみ導出可能
// （geo_report_service.derive_report_review_status参照。revision_required/stopped/
// rejectedはgeo_reports起点データには対応する既存概念が無いため導出しない）。
export type GeoReportReviewStatus = "uploaded_only" | "in_review" | "approved";

// Phase 2E（Confidentiality & Retrieval Access Control）: 部内限り/SHD限り/グループ配信可。
// Direct Report Registration（geo.report-registration.tsx）とReview Flow Publish
// （geo.ios.review.$reportProjectId.tsx）の両方がこの同じ3値を使う（§8、別々の実装にしない）。
// 内部Policy（human_visibility/agent_sharing_policy/allowed_org_units/allowed_domains）への
// 変換はBackend（geo_knowledge_access_policy.resolve_ui_label_to_policy）が一元的に行う——
// フロントエンドはLabel文字列だけを扱い、生のenum値を組み立てない。
export type GeoConfidentialityLabel = "DEPARTMENT_ONLY" | "SHD_ONLY" | "GROUP_SHAREABLE";
export const CONFIDENTIALITY_LABELS: GeoConfidentialityLabel[] = [
  "DEPARTMENT_ONLY",
  "SHD_ONLY",
  "GROUP_SHAREABLE",
];

export interface GeoReportSummary {
  report_id: string;
  document_id: string;
  author?: string | null;
  owner_department?: string | null;
  report_type?: string | null;
  confidentiality?: string | null;
  report_kind?: string | null;
  uploaded_at?: string | null;
  is_demo?: boolean;
  document_title?: string | null;
  theme_name?: string | null;
  // Report Versioning（Closeout Phase3）
  version?: number;
  supersedes_id?: string | null;
  report_identity_id?: string;
  // Phase 2D（レポート登録）
  country_region?: string[];
  topics?: string[];
  key_actors?: string[];
  keywords?: string[];
  summary?: string | null;
  business_context?: string | null;
  registration_source?: string | null;
  review_status?: GeoReportReviewStatus;
  confidentiality_label?: GeoConfidentialityLabel | null;
}

export function useGeoReports(params: { gapId?: string } = {}) {
  const usp = new URLSearchParams();
  if (params.gapId) usp.set("gap_id", params.gapId);
  const qs = usp.toString();
  return useQuery({
    queryKey: ["geoReports", params],
    queryFn: () =>
      fetchJson<{ reports: GeoReportSummary[] }>(`/api/geo/reports${qs ? `?${qs}` : ""}`),
  });
}

export function useSearchReports(query: string, params: { includeDemo?: boolean } = {}) {
  const usp = new URLSearchParams({ q: query });
  if (params.includeDemo) usp.set("include_demo", "true");
  return useQuery({
    queryKey: ["geoSearchReports", query, params],
    queryFn: () =>
      fetchJson<{ reports: GeoReportSummary[] }>(`/api/geo/reports/search?${usp.toString()}`),
    enabled: query.trim().length > 0,
  });
}

// Intelligence Needの解決とは無関係な、一般的なReport Upload（Knowledge蓄積のみが目的）。
// gap解決に紐づけるuseAddResolutionReport（geoIntelligenceNeedsApi.ts）とは別物——
// linked_gap_idsは付けない（バックエンドgeo_report_api.pyのlinked_gap_ids既定値"[]"のまま）。
export function useUploadReport() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (params: {
      file: File;
      title?: string;
      author?: string;
      ownerDepartment?: string;
      reportType?: string;
      countryRegion?: string[];
      topics?: string[];
      confidentiality?: string;
      reportKind?: string;
      themeId?: string;
      actorId?: string;
      // Phase 2D（レポート登録）追加項目。省略可（既存Upload Reportダイアログは未指定のまま）。
      keyActors?: string[];
      keywords?: string[];
      summary?: string;
      businessContext?: string;
      registrationSource?: string;
      // Phase 2E: 3つのUI Label（DEPARTMENT_ONLY/SHD_ONLY/GROUP_SHAREABLE）のいずれか。
      confidentialityLabel?: GeoConfidentialityLabel;
    }) => {
      const form = new FormData();
      form.append("file", params.file);
      form.append("title", params.title ?? "");
      form.append("author", params.author ?? "");
      form.append("owner_department", params.ownerDepartment ?? "");
      form.append("report_type", params.reportType ?? "");
      form.append("country_region", JSON.stringify(params.countryRegion ?? []));
      form.append("topics", JSON.stringify(params.topics ?? []));
      form.append("confidentiality", params.confidentiality ?? "internal");
      form.append("report_kind", params.reportKind ?? "internal_report");
      form.append("theme_id", params.themeId ?? "");
      form.append("actor_id", params.actorId ?? "analyst");
      form.append("key_actors", JSON.stringify(params.keyActors ?? []));
      form.append("keywords", JSON.stringify(params.keywords ?? []));
      form.append("summary", params.summary ?? "");
      form.append("business_context", params.businessContext ?? "");
      form.append("registration_source", params.registrationSource ?? "");
      form.append("confidentiality_label", params.confidentialityLabel ?? "");
      const res = await fetch(`${API_BASE}/api/geo/reports`, { method: "POST", body: form });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `API error ${res.status}`);
      }
      return res.json() as Promise<{ report: GeoReportSummary; document: Record<string, unknown> }>;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["geoReports"] });
    },
  });
}

// Phase 2D §ファイル解析→AI Metadata Extraction。DBへは何も書き込まない
// （プレビュー専用、確定はuseUploadReportで別途行う）。
export interface GeoReportAnalyzedMetadata {
  title: string | null;
  report_date: string | null;
  region: string[];
  topics: string[];
  key_actors: string[];
  author: string | null;
  organization: string | null;
  report_type: string | null;
  language: string | null;
  keywords: string[];
  summary: string | null;
  business_context: string | null;
  not_explicit_fields: string[];
}

export function useAnalyzeReport() {
  return useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(`${API_BASE}/api/geo/reports/analyze`, {
        method: "POST",
        body: form,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `API error ${res.status}`);
      }
      return res.json() as Promise<{
        metadata: GeoReportAnalyzedMetadata;
        extracted_text_length: number;
      }>;
    },
  });
}

export interface GeoReportDocument {
  document_id: string;
  title?: string | null;
  extracted_text?: string | null;
  mime_type?: string | null;
  storage_path?: string | null;
  fetch_status?: string | null;
}

export interface GeoReportFull extends GeoReportSummary {
  document: GeoReportDocument | null;
}

// Report本文（extracted_text）表示用。一覧のGeoReportSummaryには本文を含めていないため
// （一覧が重くなるのを避ける）、詳細を開いたときだけ個別に取得する。
export function useReportDetail(reportId: string) {
  return useQuery({
    queryKey: ["geoReportDetail", reportId],
    queryFn: () => fetchJson<GeoReportFull>(`/api/geo/reports/${reportId}`),
    enabled: !!reportId,
  });
}

export function geoReportDownloadUrl(reportId: string): string {
  return `${API_BASE}/api/geo/reports/${reportId}/download`;
}

export function useDeleteReport() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: { reportId: string; actorId?: string }) => {
      const usp = new URLSearchParams({ actor_id: params.actorId ?? "analyst" });
      return fetchJson<{ deleted: boolean }>(
        `/api/geo/reports/${params.reportId}?${usp.toString()}`,
        { method: "DELETE" },
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["geoReports"] });
      queryClient.invalidateQueries({ queryKey: ["geoSearchReports"] });
    },
  });
}

// ── 地政学アプリClooseout Phase3/5: Report Versioning・Report Review Loop ──

export function useReportVersions(reportId: string) {
  return useQuery({
    queryKey: ["geoReportVersions", reportId],
    queryFn: () =>
      fetchJson<{ versions: GeoReportSummary[] }>(`/api/geo/reports/${reportId}/versions`),
    enabled: !!reportId,
  });
}

export interface GeoReportDiff {
  report_a: GeoReportSummary;
  report_b: GeoReportSummary;
  text_diff: string[];
  metadata_diff: Record<string, { from: unknown; to: unknown }>;
}

export function useReportDiff(fromReportId: string, toReportId: string) {
  return useQuery({
    queryKey: ["geoReportDiff", fromReportId, toReportId],
    queryFn: () =>
      fetchJson<GeoReportDiff>(
        `/api/geo/reports/diff?from_report_id=${fromReportId}&to_report_id=${toReportId}`,
      ),
    enabled: !!fromReportId && !!toReportId,
  });
}

export function useStartReportReview(reportId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (params: { file: File; reviewerId: string; actorId?: string }) => {
      const form = new FormData();
      form.append("file", params.file);
      form.append("reviewer_id", params.reviewerId);
      form.append("actor_id", params.actorId ?? "analyst");
      const res = await fetch(`${API_BASE}/api/geo/reports/${reportId}/review`, {
        method: "POST",
        body: form,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `API error ${res.status}`);
      }
      return res.json() as Promise<{
        report: GeoReportSummary;
        document: Record<string, unknown>;
        review_session: GeoExpertReviewSession;
        changes: GeoExpertReviewChange[];
      }>;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["geoReports"] });
      queryClient.invalidateQueries({ queryKey: ["geoReportVersions", reportId] });
    },
  });
}

// ── Phase2A: Expert Engine（Case統合） ─────────────────────────────────

export interface GeoExpertAnalyzeResult {
  requestId: string;
  responseId: string;
  response: Record<string, unknown>;
  qualityEvaluation: { passedCount: number; failedCount: number; checks: unknown[] };
}

export function useExpertAnalyzeCase(caseId: string) {
  return useMutation({
    mutationFn: (body: {
      theme_id?: string;
      mode?: string;
      query_text: string;
      draft_text?: string;
    }) =>
      postJson<GeoExpertAnalyzeResult>("/api/geo/knowledge/expert/analyze", {
        case_id: caseId,
        ...body,
      }),
  });
}

export interface GeoRetrievalResult {
  result_type: string;
  result_id: string;
  rank?: number;
  final_score?: number;
  inclusion_reason?: string;
  exclusion_reason?: string;
  used_in_final_context: boolean;
}

export interface GeoRetrievalRun {
  retrieval_run_id: string;
  case_id?: string | null;
  retrieval_mode: string;
  retrieved_count: number;
}

export function useCaseRetrievalRuns(caseId: string) {
  return useQuery({
    queryKey: ["geoCaseRetrievalRuns", caseId],
    queryFn: () =>
      fetchJson<{ retrieval_runs: GeoRetrievalRun[] }>(
        `/api/geo/knowledge/cases/${caseId}/retrieval-runs`,
      ),
    enabled: !!caseId,
  });
}

export function useRetrievalRunDetail(retrievalRunId: string) {
  return useQuery({
    queryKey: ["geoRetrievalRunDetail", retrievalRunId],
    queryFn: () =>
      fetchJson<{ retrieval_run: GeoRetrievalRun; results: GeoRetrievalResult[] }>(
        `/api/geo/knowledge/retrieval-runs/${retrievalRunId}`,
      ),
    enabled: !!retrievalRunId,
  });
}

// ── Phase2A: External Candidate抽出 ────────────────────────────────────

export function useExtractCandidatesFromClaims() {
  return useMutation({
    mutationFn: (body: { claim_ids: string[]; actor_id: string }) =>
      postJson<{ created: GeoKnowledgeItem[]; skipped: unknown[] }>(
        "/api/geo/knowledge/candidates/extract-from-claims",
        body,
      ),
  });
}

// ── Phase2B-2: Retrieval Governance（内容の正誤判定ではなく参照可否の運用操作） ──

export function useSetKnowledgeItemRetrievalStatus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      knowledgeItemId,
      ...body
    }: {
      knowledgeItemId: string;
      retrieval_status: string;
      actor_id: string;
      reason?: string;
    }) =>
      patchJson<GeoKnowledgeItem>(
        `/api/geo/knowledge/items/${knowledgeItemId}/retrieval-status`,
        body,
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["geoKnowledgeItems"] }),
  });
}

export function useSetClaimRetrievalStatus() {
  return useMutation({
    mutationFn: ({
      claimId,
      ...body
    }: {
      claimId: string;
      retrieval_status: string;
      actor_id: string;
      reason?: string;
    }) => patchJson(`/api/geo/knowledge/claims/${claimId}/retrieval-status`, body),
  });
}

export function useSetSourceReferenceAvailability() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      sourceId,
      ...body
    }: {
      sourceId: string;
      reference_availability: "available" | "restricted";
      actor_id: string;
    }) =>
      patchJson<GeoSourceRegistryRow>(
        `/api/geo/knowledge/sources/${sourceId}/reference-availability`,
        body,
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["geoSources"] }),
  });
}

// ── Phase2B-2: Internal Expert Learning Layer（Expert Review Session） ───

export interface GeoExpertReviewSession {
  review_session_id: string;
  case_id?: string | null;
  draft_document_id: string;
  final_document_id: string;
  reviewer_id: string;
  reviewer_label?: string | null;
  review_type: string;
  delta_extraction_status: "pending" | "completed" | "failed";
}

export interface GeoExpertReviewNote {
  note_id: string;
  review_session_id: string;
  note_type: string;
  anchor_excerpt?: string | null;
  comment_text: string;
  author?: string | null;
}

export interface GeoExpertReviewChange {
  change_id: string;
  review_session_id: string;
  change_type: string;
  reusability_class: "case_specific_assessment" | "reusable_reasoning";
  draft_excerpt?: string | null;
  final_excerpt?: string | null;
  reasoning_summary: string;
  confidence: string;
  disposition:
    "pending" | "promoted" | "kept_case_only" | "rejected_as_non_reusable" | "reinforced";
  promoted_knowledge_item_id?: string | null;
  reinforced_knowledge_item_id?: string | null;
}

export function useStartReviewSession() {
  return useMutation({
    mutationFn: (body: {
      theme_id: string;
      reviewer_id: string;
      final_text: string;
      case_id?: string;
      draft_text?: string;
      draft_expert_response_id?: string;
      reviewer_label?: string;
    }) => postJson<GeoExpertReviewSession>("/api/geo/expert-review/sessions", body),
  });
}

export function useAddReviewNote(reviewSessionId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { comment_text: string; author?: string }) =>
      postJson<GeoExpertReviewNote>(
        `/api/geo/expert-review/sessions/${reviewSessionId}/notes`,
        body,
      ),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["geoExpertReviewNotes", reviewSessionId] }),
  });
}

export function useReviewNotes(reviewSessionId: string) {
  return useQuery({
    queryKey: ["geoExpertReviewNotes", reviewSessionId],
    queryFn: () =>
      fetchJson<{ notes: GeoExpertReviewNote[] }>(
        `/api/geo/expert-review/sessions/${reviewSessionId}/notes`,
      ),
    enabled: !!reviewSessionId,
  });
}

export function useExtractDeltas(reviewSessionId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (actorId: string) =>
      postJson<{ changes: GeoExpertReviewChange[]; already_extracted: boolean }>(
        `/api/geo/expert-review/sessions/${reviewSessionId}/extract-deltas`,
        { actor_id: actorId },
      ),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["geoExpertReviewChanges", reviewSessionId] }),
  });
}

export function useReviewChanges(reviewSessionId: string) {
  return useQuery({
    queryKey: ["geoExpertReviewChanges", reviewSessionId],
    queryFn: () =>
      fetchJson<{ changes: GeoExpertReviewChange[] }>(
        `/api/geo/expert-review/sessions/${reviewSessionId}/changes`,
      ),
    enabled: !!reviewSessionId,
  });
}

function useChangeDispositionMutation(reviewSessionId: string, path: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ changeId, ...body }: { changeId: string; [key: string]: unknown }) =>
      postJson(`/api/geo/expert-review/changes/${changeId}/${path}`, body),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["geoExpertReviewChanges", reviewSessionId] }),
  });
}

export function usePromoteChange(reviewSessionId: string) {
  return useChangeDispositionMutation(reviewSessionId, "promote");
}

export function useReinforceChange(reviewSessionId: string) {
  return useChangeDispositionMutation(reviewSessionId, "reinforce");
}

export function useKeepCaseOnly(reviewSessionId: string) {
  return useChangeDispositionMutation(reviewSessionId, "keep-case-only");
}

export function useRejectChange(reviewSessionId: string) {
  return useChangeDispositionMutation(reviewSessionId, "reject");
}
