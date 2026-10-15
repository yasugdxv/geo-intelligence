// Phase 2G-2: Theme Discovery UI用のAPIクライアント。
// Backend（geo_theme_candidate_service.py/geo_theme_candidate_api.py、Phase 2G-1で実装
// 済み・Freeze対象外だがCore Generation/Fingerprint/Lifecycleロジックは変更しない）を
// そのまま呼ぶだけで、Frontendでは独自の権限判定・生成ロジックを一切持たない。
//
// Phase 2G-5（Expert AI Handoff）: HandoffのMapping/Atomicity/Idempotencyロジックは
// すべてBackend（geo_theme_candidate_handoff_service.py）側にあり、Frontendは
// /handoff-preview・/handoffを呼ぶだけ。
import { API_BASE as API_BASE_URL } from "@/lib/apiBase";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getIosAuthHeaders } from "@/lib/geoIosAuthHeader";

const API_BASE = API_BASE_URL;

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

export type GeoThemeCandidateStatus =
  "NEW" | "SHORTLISTED" | "DISMISSED" | "MONITORING" | "HANDED_OFF";
export type GeoThemeCandidateType =
  "emerging" | "changing" | "re_emerging" | "structural" | "cross_market";
export type GeoThemeChangeType =
  | "actor_movement"
  | "policy_shift"
  | "relationship_change"
  | "institutionalization"
  | "signal_spike"
  | "recurrence"
  | "cross_region_pattern";

export interface GeoThemeCandidate {
  candidate_id: string;
  candidate_fingerprint: string;
  title: string;
  signal_summary: string;
  observed_change: string;
  why_now: string;
  question: string;
  actors: string[];
  regions: string[];
  themes: string[];
  business_relevance: string[];
  candidate_type: GeoThemeCandidateType;
  change_type: GeoThemeChangeType;
  source_episode_cluster_ids: string[];
  source_situation_link_ids: string[];
  source_external_signal_ids: string[];
  source_external_event_ids: string[];
  first_seen_at: string;
  latest_signal_at: string;
  signal_count: number;
  status: GeoThemeCandidateStatus;
  status_updated_at: string | null;
  status_updated_by: string | null;
  is_demo: boolean;
  created_at: string;
  updated_at: string;
}

export interface GeoThemeCandidateEvidence {
  candidate_id: string;
  episode_clusters: Array<{
    episode_cluster_id: string;
    actor_entity_id: string;
    trajectory: string;
    member_count: number;
    first_event_date: string | null;
    last_event_date: string | null;
    representative_counterparties: string[];
    representative_geo: string[];
  }>;
  situation_links: Array<{
    cluster_link_id: string;
    link_type: string;
    link_score: number;
    link_reasons: Array<{ signal: string; value: unknown }>;
  }>;
  external_signals: Array<{
    external_signal_id: string;
    title: string | null;
    url: string | null;
    published_at: string | null;
    source_domain: string | null;
  }>;
  external_events: Array<{
    external_event_id: string;
    actor1_name: string | null;
    actor2_name: string | null;
    event_code: string | null;
    event_date: string | null;
    action_geo_full_name: string | null;
  }>;
}

// Phase 2G-3: Internal Coverage（Runtime計算、geo_theme_candidatesへは保存しない）。
export interface GeoThemeCandidateCoverage {
  coverage: {
    accessible_report_count: number;
    accessible_knowledge_count: number;
    approved_report_count: number;
    in_review_report_count: number;
    uploaded_only_report_count: number;
  };
  recency: {
    latest_report_date: string | null;
    latest_approved_report_date: string | null;
  };
  similar_internal_intelligence: Array<{
    source_ref: string;
    source_type: "report" | "knowledge_item";
    title: string | null;
    date: string | null;
    review_status: string | null;
    lexical_relevance: number | null;
    snippet: string | null;
  }>;
  key_actors_already_covered: string[];
  potential_coverage_gaps: string[];
  existing_analysis_themes: Array<{ claim: string; claim_type: string; source_refs: string[] }>;
  source_refs: unknown[];
  stage: "metadata_only" | "with_analysis";
  warnings: string[];
}

export function useThemeCandidateInternalCoverage(
  candidateId: string,
  params: { includeAnalysis?: boolean; enabled?: boolean } = {},
) {
  const usp = new URLSearchParams();
  if (params.includeAnalysis) usp.set("include_analysis", "true");
  const qs = usp.toString();
  return useQuery({
    queryKey: ["geoThemeCandidateInternalCoverage", candidateId, params.includeAnalysis ?? false],
    queryFn: () =>
      fetchJson<GeoThemeCandidateCoverage>(
        `/api/geo/theme-candidates/${candidateId}/internal-coverage${qs ? `?${qs}` : ""}`,
      ),
    enabled: !!candidateId && (params.enabled ?? true),
  });
}

export function useThemeCandidates(params: { isDemo?: boolean } = {}) {
  const usp = new URLSearchParams();
  if (params.isDemo) usp.set("is_demo", "true");
  const qs = usp.toString();
  return useQuery({
    queryKey: ["geoThemeCandidates", params],
    queryFn: () =>
      fetchJson<{ candidates: GeoThemeCandidate[] }>(
        `/api/geo/theme-candidates${qs ? `?${qs}` : ""}`,
      ),
  });
}

export function useThemeCandidateEvidence(candidateId: string) {
  return useQuery({
    queryKey: ["geoThemeCandidateEvidence", candidateId],
    queryFn: () =>
      fetchJson<GeoThemeCandidateEvidence>(`/api/geo/theme-candidates/${candidateId}/evidence`),
    enabled: !!candidateId,
  });
}

// §Lifecycle actions: Frontendはactor_idを送らない（Backendが認証済みidentityから取る）。
export function useUpdateThemeCandidateStatus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: { candidateId: string; status: GeoThemeCandidateStatus }) =>
      fetchJson<GeoThemeCandidate>(`/api/geo/theme-candidates/${params.candidateId}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: params.status }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["geoThemeCandidates"] });
    },
  });
}

// ── Phase 2G-4: Wall-bounce Chat / Refinement Proposal ─────────────────────
export interface GeoThemeCandidateWallBounceMessage {
  role: "user" | "assistant";
  content: string;
}

export interface GeoThemeCandidateRefinementProposal {
  proposed_title: string | null;
  proposed_question: string | null;
  proposed_actors: string[];
  proposed_regions: string[];
  proposed_scope: string | null;
  investigation_questions: string[];
  rationale: string | null;
}

export interface GeoThemeCandidateWallBounceResponse {
  answer: string;
  has_proposal: boolean;
  proposal: GeoThemeCandidateRefinementProposal;
  source_refs: GeoThemeCandidateCoverage["similar_internal_intelligence"];
}

// Wall-bounceはExpert AI相談ではなくCandidateのscope/question/actors改善が目的
// （Reading/Primary/Alternativeは作らない）。既存Phase 2F-4と同じstateless multi-turn
// パターン——Frontendは会話historyを保持し毎requestで送るだけで、Backend側は
// セッションを保持しない。
export function useThemeCandidateWallBounce() {
  return useMutation({
    mutationFn: (params: {
      candidateId: string;
      message: string;
      history: GeoThemeCandidateWallBounceMessage[];
    }) =>
      fetchJson<GeoThemeCandidateWallBounceResponse>(
        `/api/geo/theme-candidates/${params.candidateId}/wall-bounce`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: params.message, history: params.history }),
        },
      ),
  });
}

export interface GeoThemeCandidateRefinementPatch {
  title?: string;
  question?: string;
  actors?: string[];
  regions?: string[];
  themes?: string[];
  business_relevance?: string[];
}

// §Human-edit ownership: 人間が「この提案を反映」を押した時だけ呼ぶ。actor_idは送らない
// （Backendが認証済みidentityから取る）。statusは変更しない（patchにstatusを含められない
// 型になっている）。
export function useApplyThemeCandidateRefinement() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: { candidateId: string; patch: GeoThemeCandidateRefinementPatch }) =>
      fetchJson<GeoThemeCandidate>(`/api/geo/theme-candidates/${params.candidateId}/refinement`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(params.patch),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["geoThemeCandidates"] });
    },
  });
}

// ── Phase C: Episode Cluster Review → Theme Candidate Sync 運用可視化 ──────
// 生成ロジック自体（build_candidate_material/generate_candidate_framing/
// create_or_merge_candidate）には一切触れず、既存パイプラインの滞留状況の可視化と
// 手動/一括実行のトリガーのみを提供する。

export interface GeoThemeCandidatePipelineStatus {
  review_pending: number;
  accepted: number;
  pending_theme_sync: number;
  theme_candidates_total: number;
  handed_off: number;
}

export function useThemeCandidatePipelineStatus(params: { isDemo?: boolean } = {}) {
  const usp = new URLSearchParams();
  if (params.isDemo) usp.set("is_demo", "true");
  const qs = usp.toString();
  return useQuery({
    queryKey: ["geoThemeCandidatePipelineStatus", params],
    queryFn: () =>
      fetchJson<GeoThemeCandidatePipelineStatus>(
        `/api/geo/theme-candidates/pipeline-status${qs ? `?${qs}` : ""}`,
      ),
    refetchInterval: 15000,
  });
}

export interface GeoPendingSyncMaterial {
  source_type: "situation_component" | "single_cluster";
  cluster_ids: string[];
  link_ids: string[];
  actors: string[];
  regions: string[];
  themes: string[];
  member_count_total: number;
  signal_count: number;
  latest_signal_at: string | null;
}

export function useThemeCandidatePendingSync(params: { isDemo?: boolean } = {}) {
  const usp = new URLSearchParams();
  if (params.isDemo) usp.set("is_demo", "true");
  const qs = usp.toString();
  return useQuery({
    queryKey: ["geoThemeCandidatePendingSync", params],
    queryFn: () =>
      fetchJson<{ pending: GeoPendingSyncMaterial[] }>(
        `/api/geo/theme-candidates/pending-sync${qs ? `?${qs}` : ""}`,
      ),
  });
}

export type GeoThemeSyncOutcome =
  "created" | "merged" | "insufficient_evidence" | "generation_failed";

export interface GeoThemeSyncResult {
  outcome: GeoThemeSyncOutcome;
  candidate_id: string | null;
  cluster_ids: string[];
}

function invalidatePipelineQueries(queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.invalidateQueries({ queryKey: ["geoThemeCandidates"] });
  queryClient.invalidateQueries({ queryKey: ["geoThemeCandidatePipelineStatus"] });
  queryClient.invalidateQueries({ queryKey: ["geoThemeCandidatePendingSync"] });
  queryClient.invalidateQueries({ queryKey: ["geo-theme-explorer"] });
  queryClient.invalidateQueries({ queryKey: ["geoOperationsSummary"] });
}

// §5: 1件のaccepted Episode Clusterから手動でTheme Candidateを生成する。
export function useSyncThemeCandidateForCluster() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: { episodeClusterId: string; isDemo?: boolean }) =>
      fetchJson<GeoThemeSyncResult>(
        `/api/geo/theme-candidates/sync-cluster/${params.episodeClusterId}${params.isDemo ? "?is_demo=true" : ""}`,
        { method: "POST" },
      ),
    onSuccess: () => invalidatePipelineQueries(queryClient),
  });
}

export interface GeoThemeBatchSyncResult {
  target_count: number;
  results: GeoThemeSyncResult[];
  counts: {
    fetched_count: number;
    inserted_count: number;
    updated_count: number;
    skipped_count: number;
    error_count: number;
  };
  sync_run_id: string;
}

// §6/§7: Theme同期待ちの全clusterをまとめて処理する。
export function useBatchSyncPendingThemeCandidates() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: { isDemo?: boolean } = {}) =>
      fetchJson<GeoThemeBatchSyncResult>(
        `/api/geo/theme-candidates/sync-pending${params.isDemo ? "?is_demo=true" : ""}`,
        { method: "POST" },
      ),
    onSuccess: () => invalidatePipelineQueries(queryClient),
  });
}

// ── Phase 2G-5: Expert AI Consultation Handoff ──────────────────────────────
export interface GeoThemeCandidateHandoffPreview {
  candidate_id: string;
  session_title: string;
  core_question: string | null;
  actors: string[];
  regions: string[];
  why_now: string | null;
  carried_investigation_questions: string[];
  existing_session_id: string | null;
}

export interface GeoThemeCandidateHandoffResult {
  analysis_session_id: string;
  created: boolean;
}

// §8 Handoff Preview: 何も作成しない確認専用の読み取り（POSTだがmutationではない——
// investigation_questionsをbodyで渡す必要があるためGETではなくPOSTにしている）。
export function useThemeCandidateHandoffPreview() {
  return useMutation({
    mutationFn: (params: { candidateId: string; investigationQuestions: string[] }) =>
      fetchJson<GeoThemeCandidateHandoffPreview>(
        `/api/geo/theme-candidates/${params.candidateId}/handoff-preview`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ investigation_questions: params.investigationQuestions }),
        },
      ),
  });
}

// §9/§12: 成功時のみCandidate status→HANDED_OFF（Backend側）。§11
// Idempotency: 既にHandoff済みなら新規作成せず既存Sessionを返す（created: false）。
export function useHandoffThemeCandidateToExpertAI() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: { candidateId: string; investigationQuestions: string[] }) =>
      fetchJson<GeoThemeCandidateHandoffResult>(
        `/api/geo/theme-candidates/${params.candidateId}/handoff`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ investigation_questions: params.investigationQuestions }),
        },
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["geoThemeCandidates"] });
    },
  });
}
