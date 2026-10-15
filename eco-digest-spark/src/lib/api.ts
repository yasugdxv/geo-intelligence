import { API_BASE as API_BASE_URL } from "@/lib/apiBase";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { PresetFollowupResult, PresetFormState, PresetRunResult } from "@/lib/geoIntelligencePresetApi";

const API_BASE = API_BASE_URL;

export type Lang = "ja" | "en";

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `API error ${res.status}`);
  }
  return res.json();
}

export interface ChatMsg {
  role: "user" | "assistant";
  content: string;
  sources?: string[];
  // Phase1 Closeout: Intelligence AIパイプライン接続に伴う追加情報（すべてoptional）
  sufficiency?: string | null;
  expertAiCalled?: boolean;
  intelligenceNeedCreated?: boolean;
  intelligenceNeedId?: string | null;
  // Intelligence AI プリセット調査（2026-10-14）。入力フォーム・調査結果・追加回答の表示用（すべてoptional。
  // /api/geo/chat へは role/content のみ送るため、既存の通常チャットには影響しない）
  presetForm?: PresetFormState;
  presetResult?: PresetRunResult;
  presetFollowup?: PresetFollowupResult;
}

// ─── 地政学インテリジェンス基盤（geo_*） ───────────────────────────────────
// バックエンドはapi_server.pyの/api/geo/*（Phase1縦切り）。既存の競合モニタリング
// セクションとは完全に独立しており、既存の型・関数・queryKeyは一切変更しない。

export interface GeoTheme {
  id: string;
  code: string;
  nameJa: string;
  nameEn: string | null;
  description: string | null;
  status: string;
}

export interface GeoDocument {
  id: string;
  themeId: string;
  title: string | null;
  url: string | null;
  publishedAt: string | null;
  fetchedAt: string;
  language: string | null;
  mimeType: string | null;
  fetchStatus: string;
  isMock: boolean;
  extractedTextPreview: string;
}

export interface GeoEvent {
  id: string;
  themeId: string;
  actor: string | null;
  action: string | null;
  target: string | null;
  location: string | null;
  startAt: string | null;
  endAt: string | null;
  eventType: string | null;
  indicatorCode: string | null;
  confidence: number | null;
  unknownFields: Record<string, string>;
  verified: boolean;
  verifiedBy: string | null;
}

export interface GeoEvidence {
  id: string;
  eventId: string;
  claimText: string | null;
  quote: string | null;
  sourceTier: string | null;
  verificationStatus: string;
  confidence: number | null;
  documentId: string | null;
  documentTitle: string | null;
  documentUrl: string | null;
  isMock: boolean;
}

export interface GeoIndicator {
  id: string;
  themeId?: string | null;
  code: string;
  name: string;
  unit: string | null;
  description: string | null;
  mvpEnabled: boolean;
  displayOrder: number | null;
  sourceName: string | null;
  sourceUrl: string | null;
}

export interface GeoObservation {
  id: string;
  indicatorId: string;
  asOf: string;
  rawValue: number | null;
  insufficientData: boolean;
  baseline7d: number | null;
  baseline30d: number | null;
  baseline90d: number | null;
  deviation: number | null;
  confidence: number | null;
  dataQuality: string | null;
  qualitativeLabel: string | null;
  rationale: string | null;
  isMock: boolean;
}

export interface GeoOverview {
  theme: GeoTheme | null;
  mil01Observation: GeoObservation | null;
  unverifiedEventCount: number;
  recentAuditLogs: { id: string; actor: string; action: string; targetTable: string | null; createdAt: string }[];
}

export const geoThemesQueryOptions = {
  queryKey: ["geoThemes"] as const,
  queryFn: () => fetchJson<{ themes: GeoTheme[] }>("/api/geo/themes"),
  staleTime: 5 * 60_000,
};

export function useGeoThemes() {
  return useQuery(geoThemesQueryOptions);
}

export function useCreateGeoTheme() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (params: { nameJa: string; nameEn?: string; description?: string }) => {
      const res = await fetch(`${API_BASE}/api/geo/themes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name_ja: params.nameJa, name_en: params.nameEn || null, description: params.description || null,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `API error ${res.status}`);
      }
      return res.json() as Promise<{ theme: GeoTheme }>;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["geoThemes"] });
    },
  });
}

export function useGeoSeedTaiwanTheme() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const res = await fetch(`${API_BASE}/api/geo/themes/seed/taiwan`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `API error ${res.status}`);
      }
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["geoThemes"] });
      queryClient.invalidateQueries({ queryKey: ["geoOverview"] });
    },
  });
}

export function geoDocumentsQueryOptions(themeId: string = "") {
  return {
    queryKey: ["geoDocuments", themeId] as const,
    queryFn: () => fetchJson<{ documents: GeoDocument[] }>(`/api/geo/documents?theme_id=${themeId}`),
    staleTime: 30_000,
  };
}

export function useGeoDocuments(themeId: string = "") {
  return useQuery(geoDocumentsQueryOptions(themeId));
}

export function useGeoUploadDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (params: {
      file: File; themeId: string; title?: string; url?: string; publishedAt?: string; isMock?: boolean;
    }) => {
      const form = new FormData();
      form.set("file", params.file);
      form.set("theme_id", params.themeId);
      form.set("title", params.title ?? "");
      form.set("url", params.url ?? "");
      form.set("published_at", params.publishedAt ?? "");
      form.set("is_mock", String(params.isMock ?? false));
      const res = await fetch(`${API_BASE}/api/geo/documents/upload`, { method: "POST", body: form });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `API error ${res.status}`);
      }
      return res.json() as Promise<{ document: GeoDocument; isNew: boolean }>;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["geoDocuments"] });
    },
  });
}

export function useGeoExtractEvents() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (documentId: string) => {
      const res = await fetch(`${API_BASE}/api/geo/documents/${documentId}/extract-events`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `API error ${res.status}`);
      }
      return res.json() as Promise<{ eventsCreated: number }>;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["geoEvents"] });
      queryClient.invalidateQueries({ queryKey: ["geoEvidence"] });
    },
  });
}

export function geoEventsQueryOptions(params: { themeId?: string; verified?: boolean } = {}) {
  const usp = new URLSearchParams();
  if (params.themeId) usp.set("theme_id", params.themeId);
  if (params.verified !== undefined) usp.set("verified", String(params.verified));
  return {
    queryKey: ["geoEvents", params.themeId ?? "", params.verified ?? ""] as const,
    queryFn: () => fetchJson<{ events: GeoEvent[] }>(`/api/geo/events?${usp.toString()}`),
    staleTime: 30_000,
  };
}

export function useGeoEvents(params: { themeId?: string; verified?: boolean } = {}) {
  return useQuery(geoEventsQueryOptions(params));
}

export function useGeoVerifyEvent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ eventId, verified, verifiedBy }: { eventId: string; verified: boolean; verifiedBy?: string }) => {
      const res = await fetch(`${API_BASE}/api/geo/events/${eventId}/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ verified, verified_by: verifiedBy ?? "analyst" }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `API error ${res.status}`);
      }
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["geoEvents"] });
      queryClient.invalidateQueries({ queryKey: ["geoOverview"] });
    },
  });
}

export function geoEvidenceQueryOptions(eventId: string = "") {
  return {
    queryKey: ["geoEvidence", eventId] as const,
    queryFn: () => fetchJson<{ evidence: GeoEvidence[] }>(`/api/geo/evidence?event_id=${eventId}`),
    staleTime: 30_000,
  };
}

export function useGeoEvidence(eventId: string = "") {
  return useQuery(geoEvidenceQueryOptions(eventId));
}

export function geoIndicatorsQueryOptions(themeId: string = "") {
  return {
    queryKey: ["geoIndicators", themeId] as const,
    queryFn: () => fetchJson<{ indicators: GeoIndicator[] }>(`/api/geo/indicators?theme_id=${themeId}`),
    staleTime: 60_000,
  };
}

export function useGeoIndicators(themeId: string = "") {
  return useQuery(geoIndicatorsQueryOptions(themeId));
}

export function geoIndicatorObservationsQueryOptions(indicatorId: string) {
  return {
    queryKey: ["geoIndicatorObservations", indicatorId] as const,
    queryFn: () => fetchJson<{ observations: GeoObservation[] }>(`/api/geo/indicators/${indicatorId}/observations`),
    enabled: !!indicatorId,
    staleTime: 30_000,
  };
}

export function useGeoIndicatorObservations(indicatorId: string) {
  return useQuery(geoIndicatorObservationsQueryOptions(indicatorId));
}

export interface GeoIndicatorFreshness {
  status: "current" | "review_due_soon" | "overdue" | "unknown";
  days_since_last_observation: number | null;
}

export interface GeoIndicatorAlert {
  alert_id: string; indicator_id: string; rule_version_id: string; observation_id: string;
  severity: "warning" | "critical"; triggered_at: string;
  acknowledged_at?: string | null; acknowledged_by?: string | null;
}

export interface GeoIndicatorMonitorRow extends GeoIndicator {
  latestObservation: GeoObservation | null;
  freshness: GeoIndicatorFreshness;
  latestAlert: GeoIndicatorAlert | null;
}

export function geoIndicatorMonitorQueryOptions() {
  return {
    queryKey: ["geoIndicatorMonitor"] as const,
    queryFn: () => fetchJson<{ indicators: GeoIndicatorMonitorRow[] }>("/api/geo/indicators/monitor"),
    staleTime: 30_000,
  };
}

export function useGeoIndicatorMonitor() {
  return useQuery(geoIndicatorMonitorQueryOptions());
}

export function useGeoRecalculateIndicator() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (indicatorId: string) => {
      const res = await fetch(`${API_BASE}/api/geo/indicators/${indicatorId}/recalculate`, {
        method: "POST",
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `API error ${res.status}`);
      }
      return res.json() as Promise<{ observation: GeoObservation }>;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["geoIndicatorObservations"] });
      queryClient.invalidateQueries({ queryKey: ["geoOverview"] });
      queryClient.invalidateQueries({ queryKey: ["geoIndicatorMonitor"] });
    },
  });
}

export function useGeoCrawlOfficialSources() {
  return useMutation({
    mutationFn: async () => {
      const res = await fetch(`${API_BASE}/api/geo/official-sources/crawl`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `API error ${res.status}`);
      }
      return res.json() as Promise<{ results: { source: string; indicator?: string; ok: boolean; error?: string }[] }>;
    },
  });
}

export function geoOverviewQueryOptions(themeId: string = "") {
  return {
    queryKey: ["geoOverview", themeId] as const,
    queryFn: () => fetchJson<GeoOverview>(`/api/geo/overview?theme_id=${themeId}`),
    staleTime: 30_000,
  };
}

export function useGeoOverview(themeId: string = "") {
  return useQuery(geoOverviewQueryOptions(themeId));
}

// ─── 地政学専門家AI本格版（7モード） ───────────────────────────────────
// バックエンドはgeo_expert_engine.pyのSource Pack作成→構造化LLM呼び出し→Quality Guard→保存。
// responseフィールドの中身(GeoExpertResponseJson)はLLMのJSON Schema出力をそのまま返すため、
// 他のAPIレスポンスと違いsnake_caseのまま扱う（schemas/geo_expert_ai_response.schema.jsonが正本）。

// v1.2 段階実行(Staged Execution): 実行プロファイル。既定はstandard（通常利用者は
// 意識しなくてよい）。quick/deepは詳細設定内でのみ選べる。
export const GEO_EXPERT_PROFILES = ["quick", "standard", "deep"] as const;
export type GeoExpertProfile = (typeof GEO_EXPERT_PROFILES)[number];

export const GEO_EXPERT_MODES = [
  "theme_design", "research_design", "case_analysis", "outline_review",
  "draft_review", "executive_brief", "analyst_coaching",
] as const;
export type GeoExpertMode = (typeof GEO_EXPERT_MODES)[number];

export interface GeoExpertClaim {
  claim_type: "fact" | "official_claim" | "observation" | "inference" | "hypothesis" | "illustration";
  text: string;
  source_ref?: string | null;
}

export interface GeoExpertStakeholder {
  actor: string;
  purpose?: string | null;
  priority?: string | null;
  constraints_fears?: string | null;
  red_lines?: string | null;
  key_relationships?: string | null;
  confidence?: string | null;
  // Phase3-0: バックエンドschemaには既に存在していたが型定義から漏れていたフィールド
  // （Actor Intelligence Phase AI-3の調査で発見、表示ギャップの修正）
  position?: string | null;
  leverage?: string | null;
  ability_to_move_or_block?: string | null;
}

export interface GeoExpertMainScenario {
  description: string;
  confidence: string;
  supporting_evidence?: string[];
  // v1.2(Session2)
  selection_rationale?: string | null;
  assumptions?: string[] | null;
  observable_indicators?: string[] | null;
}

export interface GeoExpertBusinessImpact {
  direct?: string | null;
  indirect?: string | null;
  environment_change?: string | null;
  unrelated_note?: string | null;
}

// v1.2(Session2): Signal（見せているメッセージ）とSubstance（実質的な政策強度）を分ける
export interface GeoExpertActionSubstance {
  stated_signal?: string | null;
  concrete_measure?: string | null;
  target_scope?: string | null;
  legal_or_policy_force?: string | null;
  implementation_status?: string | null;
  enforcement_status?: string | null;
  discretion?: string | null;
  evidence_ids?: string[] | null;
  assessment?: string | null;
}

// v1.2(Session2): この局面を実際に動かす／止めるKey Stakeholderの判定
export interface GeoExpertKeyStakeholderAssessment {
  determination: "single" | "multiple" | "distributed" | "unclear";
  selected_actor_ids: string[];
  rationale: string;
  decision_structure?: string | null;
  confidence: string;
  evidence_ids?: string[] | null;
}

export interface GeoExpertPersistentPreferenceEvidence {
  event_or_behavior: string;
  date_or_period?: string | null;
  claim_id?: string | null;
  evidence_id?: string | null;
  source?: string | null;
  supports_or_contradicts: "supports" | "contradicts";
  relevance?: string | null;
  caveat?: string | null;
}

export interface GeoExpertDrivingForce {
  force: string;
  direction: "supporting" | "constraining";
  description?: string | null;
}

// v1.2(Session2): Key Stakeholderの深掘りプロファイル（目安1〜3件）
export interface GeoExpertKeyActorProfile {
  actor_ref: string;
  actor: string;
  entity_id?: string | null;
  formal_role?: string | null;
  decision_power?: string | null;
  ability_to_move?: string | null;
  ability_to_block?: string | null;
  ultimate_objective?: string | null;
  strategic_relationship_preference?: string | null;
  persistent_preference?: string | null;
  persistent_preference_evidence?: GeoExpertPersistentPreferenceEvidence[];
  issue_objective?: string | null;
  observed_action?: string | null;
  preference_action_gap?: string | null;
  primary_audience?: string | null;
  secondary_audience?: string | null;
  driving_forces?: GeoExpertDrivingForce[];
  action_substance?: GeoExpertActionSubstance | null;
  red_line?: string | null;
  confidence: string;
  evidence_ids?: string[] | null;
  // v1.2 Phase B
  persistent_preference_change?: "reinforced" | "revised" | null;
  // Actor Intelligence Phase AI-3
  why_actor_matters?: string | null;
  available_leverage?: string[] | null;
  unavailable_or_costly_cards?: string[] | null;
  vulnerabilities?: string[] | null;
  relevant_decision_patterns?: string[] | null;
}

export interface GeoExpertAudienceAnalysis {
  actor_ref: string;
  action_or_statement: string;
  primary_audience?: string | null;
  secondary_audience?: string | null;
  message_hypothesis?: string | null;
  action_substance?: GeoExpertActionSubstance | null;
  confidence?: string | null;
}

export interface GeoExpertIdentifiedKnowledgeGap {
  description: string;
  gap_category?: "deterministic" | "analytical_candidate" | null;
  priority?: string | null;
  recommended_action?: string | null;
}

export interface GeoExpertRootPremiseItem {
  result: "pass" | "warning" | "fail" | "not_assessable";
  assessment: string;
  evidence_ids?: string[] | null;
}

// v1.2(Session2): draft_review/outline_reviewで最初に行うRoot Premise Check
export interface GeoExpertRootPremiseCheck {
  key_stakeholder_validity: GeoExpertRootPremiseItem;
  persistent_preference_grounding: GeoExpertRootPremiseItem;
  objective_hierarchy_separation: GeoExpertRootPremiseItem;
  preference_action_gap_analysis: GeoExpertRootPremiseItem;
  observed_action_not_treated_as_ultimate_objective: GeoExpertRootPremiseItem;
  overall_result: "pass" | "warning" | "fail";
}

export interface GeoExpertReviewFinding {
  severity: "Critical" | "Major" | "Minor";
  category?: string | null;
  finding: string;
  recommendation?: string | null;
  evidence_ids?: string[] | null;
}

export interface GeoExpertResponseJson {
  one_line_assessment: string;
  initial_anomaly?: string | null;
  claims: GeoExpertClaim[];
  stakeholders: GeoExpertStakeholder[];
  dynamics?: string | null;
  decisive_power_and_leverage?: string | null;
  main_scenario: GeoExpertMainScenario;
  alternative_scenarios?: {
    description: string; confidence?: string | null;
    strengthening_conditions?: string[] | null; weakening_conditions?: string[] | null;
    switch_conditions?: string[] | null;
  }[];
  strongest_counterargument: string;
  weakening_information?: string | null;
  contradicting_evidence?: string[] | null;
  retraction_conditions: string[];
  additional_research?: { question: string; related_hypothesis?: string | null; recommended_sources?: string | null }[];
  business_impact?: GeoExpertBusinessImpact;
  monitoring_indicators?: string[];
  analyst_coaching_notes?: string[];
  executive_summary?: { what_changed?: string | null; why_it_matters?: string | null; what_to_watch_decide?: string | null };
  unknown_or_uncertain?: string | null;
  // v1.2(Session2追加分。すべてoptional/nullable、既存フィールドは変更しない)
  key_stakeholder_assessment?: GeoExpertKeyStakeholderAssessment | null;
  key_actor_profiles?: GeoExpertKeyActorProfile[] | null;
  audience_analysis?: GeoExpertAudienceAnalysis[] | null;
  preference_action_gap_summary?: string | null;
  identified_knowledge_gaps?: GeoExpertIdentifiedKnowledgeGap[] | null;
  root_premise_check?: GeoExpertRootPremiseCheck | null;
  review_findings?: GeoExpertReviewFinding[] | null;
  // Actor Intelligence Phase AI-3: ケース単位のActor Intelligence充足度（コード側で算出、
  // Feature Flag無効時・case_idなしの呼び出しではnull）
  actor_intelligence_status?: "sufficient" | "partial" | "insufficient" | null;
}

export interface GeoExpertQualityCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface GeoExpertResult {
  requestId: string;
  responseId: string;
  mode: string;
  themeId: string | null;
  queryText: string;
  createdAt: string;
  response: GeoExpertResponseJson;
  promptVersion: string;
  knowledgeVersion: string;
  model: string;
  qualityEvaluation: {
    checks: GeoExpertQualityCheck[];
    passedCount: number;
    failedCount: number;
    // v1.2(Session2): 修復ループの結果。生成直後のレスポンスのみ値を持つ（request_idで後から
    // 再取得した場合はDBに対応カラムが無いためnull。geo_expert_engine.run_expert_request参照）
    completenessStatus?: "complete" | "incomplete_after_repair" | null;
    initialFailedChecks?: string[] | null;
    repairAttempted?: boolean | null;
    remainingFailedChecks?: string[] | null;
  };
  // v1.2 段階実行(Staged Execution)
  executionMetadata?: {
    executionMode?: "staged" | "monolithic" | null;
    profile?: GeoExpertProfile | null;
    stage2Modules?: string[] | null;
  } | null;
}

export function useGeoSubmitExpertRequest() {
  return useMutation({
    mutationFn: async (params: {
      mode: GeoExpertMode; themeId?: string; queryText: string; draftText?: string;
      profile?: GeoExpertProfile;
    }) => {
      const res = await fetch(`${API_BASE}/api/geo/expert/requests`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode: params.mode, theme_id: params.themeId ?? null,
          query_text: params.queryText, draft_text: params.draftText ?? null,
          profile: params.profile ?? "standard",
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `API error ${res.status}`);
      }
      return res.json() as Promise<GeoExpertResult>;
    },
  });
}

export function geoExpertRequestsQueryOptions(themeId: string = "") {
  return {
    queryKey: ["geoExpertRequests", themeId] as const,
    queryFn: () => fetchJson<{
      requests: { requestId: string; mode: string; themeId: string | null; queryText: string; createdAt: string }[];
    }>(`/api/geo/expert/requests?theme_id=${themeId}`),
    staleTime: 15_000,
  };
}

export function useGeoExpertRequests(themeId: string = "") {
  return useQuery(geoExpertRequestsQueryOptions(themeId));
}

export function geoExpertRequestQueryOptions(requestId: string) {
  return {
    queryKey: ["geoExpertRequest", requestId] as const,
    queryFn: () => fetchJson<GeoExpertResult>(`/api/geo/expert/requests/${requestId}`),
    enabled: !!requestId,
  };
}

export function useGeoExpertRequest(requestId: string) {
  return useQuery(geoExpertRequestQueryOptions(requestId));
}

export function useGeoSubmitExpertFeedback() {
  return useMutation({
    mutationFn: async ({ responseId, rating, comment }: { responseId: string; rating: string; comment?: string }) => {
      const res = await fetch(`${API_BASE}/api/geo/expert/responses/${responseId}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rating, comment: comment ?? "" }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `API error ${res.status}`);
      }
      return res.json();
    },
  });
}

// 地政学専門家AI（MVP軽量版）。既存のuseChatSend(/api/chat、サスティナAI)とは別の
// エンドポイント(/api/geo/chat)を叩く独立したフック。
export function useGeoChatSend() {
  return useMutation({
    mutationFn: async ({
      message,
      history,
      themeId,
      lang,
    }: {
      message: string;
      history: ChatMsg[];
      themeId?: string;
      lang?: Lang;
    }) => {
      const res = await fetch(`${API_BASE}/api/geo/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          history: history.map(({ role, content }) => ({ role, content })),
          theme_id: themeId ?? "",
          lang: lang ?? "ja",
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `API error ${res.status}`);
      }
      return res.json() as Promise<{
        reply: string;
        sources: string[];
        sufficiency?: string | null;
        expert_ai_called?: boolean;
        intelligence_need_created?: boolean;
        intelligence_need_id?: string | null;
      }>;
    },
  });
}
