// Geopolitical Intelligence OS: Learning Workbench APIクライアント。バックエンドは
// /api/geo/ios/learning-workbench/*（geo_ios_learning_workbench_api.py）へ接続する。
// 既存geoIosLearningApi.tsとは独立したファイル——Gate#1素のInboxとWorkbenchは別画面。
//
// 2026-09-24修正の教訓（geoIosReviewApi.ts等4ファイルが同じ穴に落ちた）を繰り返さない:
// 最初からgetIosAuthHeaders()を必ず付ける。
import { API_BASE as API_BASE_URL } from "@/lib/apiBase";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getIosAuthHeaders } from "@/lib/geoIosAuthHeader";

const API_BASE = API_BASE_URL;

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers: { ...getIosAuthHeaders(), ...init?.headers } });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `API error ${res.status}`);
  }
  return res.json();
}

function postJson<T>(path: string, body: unknown): Promise<T> {
  return fetchJson<T>(path, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
}

export type GeoIosWorkbenchStatus = "pending" | "editing" | "ready_for_approval" | "approved" | "published" | "rejected";
export type GeoIosDedupVerdict = "NEW" | "DUPLICATE" | "UPDATE" | "CONFLICT" | "RELATED";
export type GeoIosGeneralizationRisk = "low" | "medium" | "high";

export interface GeoIosWorkbenchCandidate {
  candidate_id: string;
  candidate_type: string;
  trigger_context?: {
    title?: string; trigger?: string; initial_ai_weakness?: string; final_outcome?: string;
    exceptions?: string[]; temporal_stability?: string;
    source_report_project_id?: string | null;
    after_resolution_method?: "resolved_in_version" | "next_round" | "final_approval" | "not_evaluable" | null;
  } | null;
  observation: string;
  generalized_rule: string;
  scope?: string | null;
  confidence?: string | null;
  status: "candidate" | "approved" | "rejected" | "superseded";
  workbench_status: GeoIosWorkbenchStatus;
  generalization_risk?: GeoIosGeneralizationRisk | null;
  generalization_risk_reason?: string | null;
  target_knowledge_type?: string | null;
  dedup_verdict?: GeoIosDedupVerdict | null;
  dedup_target_knowledge_item_id?: string | null;
  dedup_related_knowledge_item_ids?: string[] | null;
  dedup_conflict_resolution_note?: string | null;
  human_visibility?: string | null;
  agent_sharing_policy?: string | null;
  approved_knowledge_item_id?: string | null;
  source_review_cycle_id?: string | null;
  reviewed_by?: string | null;
  reviewed_at?: string | null;
  created_at: string;
}

export interface GeoIosWorkbenchRevision {
  revision_id: string;
  candidate_id: string;
  revision_number: number;
  revised_by: "ai" | "human";
  workbench_turn_id?: string | null;
  revision_snapshot: Record<string, unknown>;
  change_summary?: string | null;
  created_by: string;
  created_at: string;
}

export interface GeoIosWorkbenchTurn {
  workbench_turn_id: string;
  candidate_id: string;
  human_instruction: string;
  base_revision_number: number;
  status: "pending" | "completed" | "failed" | "stale";
  resulting_revision_id?: string | null;
  ai_response_text?: string | null;
  error_message?: string | null;
  created_by: string;
  created_at: string;
  completed_at?: string | null;
}

export interface GeoIosWorkbenchItem {
  candidate: GeoIosWorkbenchCandidate;
  revisions: GeoIosWorkbenchRevision[];
  turns: GeoIosWorkbenchTurn[];
}

export interface GeoIosWorkbenchSiblings {
  total: number;
  siblings: Array<{ candidate_id: string; candidate_type: string; generalized_rule: string; created_at: string }>;
  previous_candidate_id: string | null;
  next_candidate_id: string | null;
}

export interface GeoIosWorkbenchLineage {
  candidate: GeoIosWorkbenchCandidate;
  review_cycle?: { review_cycle_id: string; round_number: number; report_project_id: string; status: string } | null;
  review_comment?: Record<string, unknown> | null;
  knowledge_item?: Record<string, unknown> | null;
  report_project?: { project_id: string; project_code: string; project_title: string } | null;
  after_resolution_method?: string | null;
  revisions: GeoIosWorkbenchRevision[];
  turns: GeoIosWorkbenchTurn[];
}

// ---- 一覧/詳細/lineage/siblings ------------------------------------------------

export function useWorkbenchCandidates(workbenchStatus?: string, candidateType?: string) {
  const usp = new URLSearchParams();
  if (workbenchStatus) usp.set("workbench_status", workbenchStatus);
  if (candidateType) usp.set("candidate_type", candidateType);
  return useQuery({
    queryKey: ["geoIosWorkbenchCandidates", workbenchStatus, candidateType],
    queryFn: () => fetchJson<{ candidates: GeoIosWorkbenchCandidate[] }>(
      `/api/geo/ios/learning-workbench?${usp.toString()}`),
  });
}

export function useWorkbenchItem(candidateId: string) {
  return useQuery({
    queryKey: ["geoIosWorkbenchItem", candidateId],
    queryFn: () => fetchJson<GeoIosWorkbenchItem>(`/api/geo/ios/learning-workbench/${candidateId}`),
    enabled: !!candidateId,
    // 編集Chat実行中はpendingなturnがある間だけ短い間隔でポーリングする
    refetchInterval: (query) => {
      const turns = (query.state.data as GeoIosWorkbenchItem | undefined)?.turns ?? [];
      return turns.some((t) => t.status === "pending") ? 2500 : false;
    },
  });
}

export function useWorkbenchLineage(candidateId: string) {
  return useQuery({
    queryKey: ["geoIosWorkbenchLineage", candidateId],
    queryFn: () => fetchJson<GeoIosWorkbenchLineage>(`/api/geo/ios/learning-workbench/${candidateId}/lineage`),
    enabled: !!candidateId,
  });
}

export function useWorkbenchSiblings(candidateId: string, reviewCycleId?: string | null) {
  return useQuery({
    queryKey: ["geoIosWorkbenchSiblings", candidateId, reviewCycleId],
    queryFn: () => fetchJson<GeoIosWorkbenchSiblings>(
      `/api/geo/ios/learning-workbench/${candidateId}/siblings?review_cycle_id=${encodeURIComponent(reviewCycleId ?? "")}`),
    enabled: !!candidateId,
  });
}

function invalidateCandidate(qc: ReturnType<typeof useQueryClient>, candidateId: string) {
  qc.invalidateQueries({ queryKey: ["geoIosWorkbenchItem", candidateId] });
  qc.invalidateQueries({ queryKey: ["geoIosWorkbenchLineage", candidateId] });
  qc.invalidateQueries({ queryKey: ["geoIosWorkbenchCandidates"] });
}

// ---- Generalization Risk Assessment（GETの副作用にしない、専用POST） -----------------

export function useAssessGeneralizationRisk(candidateId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { actor_id: string }) =>
      postJson<{ status: string }>(`/api/geo/ios/learning-workbench/${candidateId}/assess-generalization-risk`, body),
    onSuccess: () => invalidateCandidate(qc, candidateId),
  });
}

// ---- 編集Chat -------------------------------------------------------------------

export function useSubmitHumanInstruction(candidateId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { human_instruction: string }) =>
      postJson<GeoIosWorkbenchTurn>(`/api/geo/ios/learning-workbench/${candidateId}/instructions`, body),
    onSuccess: () => invalidateCandidate(qc, candidateId),
  });
}

// ---- 直接編集・Dedup verdict -----------------------------------------------------

export function useRecordHumanEdit(candidateId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { edited_fields: Record<string, unknown>; change_summary?: string }) =>
      postJson<GeoIosWorkbenchCandidate>(`/api/geo/ios/learning-workbench/${candidateId}/edit`, body),
    onSuccess: () => invalidateCandidate(qc, candidateId),
  });
}

export function useRecordDedupVerdict(candidateId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      verdict: GeoIosDedupVerdict; target_knowledge_item_id?: string;
      related_knowledge_item_ids?: string[]; resolution_note?: string;
    }) => postJson<GeoIosWorkbenchCandidate>(`/api/geo/ios/learning-workbench/${candidateId}/dedup-verdict`, body),
    onSuccess: () => invalidateCandidate(qc, candidateId),
  });
}

// ---- 状態遷移 ---------------------------------------------------------------------

export function useMarkReadyForApproval(candidateId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { actor_id: string }) =>
      postJson<GeoIosWorkbenchCandidate>(`/api/geo/ios/learning-workbench/${candidateId}/ready-for-approval`, body),
    onSuccess: () => invalidateCandidate(qc, candidateId),
  });
}

export function useMarkEditing(candidateId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { actor_id: string }) =>
      postJson<GeoIosWorkbenchCandidate>(`/api/geo/ios/learning-workbench/${candidateId}/back-to-editing`, body),
    onSuccess: () => invalidateCandidate(qc, candidateId),
  });
}

export function useApproveInWorkbench(candidateId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { reviewed_by?: string; reviewer_role?: string }) =>
      postJson<GeoIosWorkbenchCandidate>(`/api/geo/ios/learning-workbench/${candidateId}/approve`, body),
    onSuccess: () => invalidateCandidate(qc, candidateId),
  });
}

export function useRejectInWorkbench(candidateId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { reviewed_by?: string; reviewer_role?: string; reason?: string }) =>
      postJson<GeoIosWorkbenchCandidate>(`/api/geo/ios/learning-workbench/${candidateId}/reject`, body),
    onSuccess: () => invalidateCandidate(qc, candidateId),
  });
}

export function usePublishFromWorkbench(candidateId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { publisher?: string; publisher_role?: string }) =>
      postJson<GeoIosWorkbenchCandidate>(`/api/geo/ios/learning-workbench/${candidateId}/publish`, body),
    onSuccess: () => invalidateCandidate(qc, candidateId),
  });
}
