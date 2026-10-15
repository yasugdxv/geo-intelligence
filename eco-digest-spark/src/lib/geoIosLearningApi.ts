// Geopolitical Intelligence OS Phase H: Learning Candidate Review Inbox / Issue Link Suggestion /
// CQAS Signal APIクライアント。バックエンドは/api/geo/ios/learning-candidates/*
// （geo_ios_learning_api.py、Phase B/G/H）にそのまま接続する。
import { API_BASE as API_BASE_URL } from "@/lib/apiBase";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getIosAuthHeaders } from "@/lib/geoIosAuthHeader";

const API_BASE = API_BASE_URL;

// 2026-09-24修正: geo_ios_learning_api.pyの全endpointはDepends(verify_ios_identity)必須だが、
// このAPIクライアントはX-IOS-Credentialヘッダーを一切付けていなかった
// （geoIosReviewApi.tsと同じ取り残されバグ）。
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

export interface GeoIosLearningCandidate {
  candidate_id: string;
  candidate_type: string;
  trigger_context?: {
    title?: string; trigger?: string; initial_ai_weakness?: string; final_outcome?: string;
    exceptions?: string[]; temporal_stability?: string;
    cqas_signal?: { signal: string; similarity: number; closest_candidate_id: string | null } | null;
    dedup_status?: string; related?: unknown[];
    source_report_project_id?: string | null;
  } | null;
  observation: string;
  generalized_rule: string;
  failure_pattern_example?: string | null;
  positive_example?: string | null;
  scope?: string | null;
  confidence?: string | null;
  extraction_model: string;
  status: "candidate" | "approved" | "rejected" | "superseded";
  approved_knowledge_item_id?: string | null;
  reviewed_by?: string | null;
  reviewed_at?: string | null;
  created_at: string;
}

export function useLearningCandidates(status?: string, candidateType?: string) {
  const usp = new URLSearchParams();
  if (status) usp.set("status", status);
  if (candidateType) usp.set("candidate_type", candidateType);
  return useQuery({
    queryKey: ["geoIosLearningCandidates", status, candidateType],
    queryFn: () => fetchJson<{ candidates: GeoIosLearningCandidate[] }>(
      `/api/geo/ios/learning-candidates?${usp.toString()}`),
  });
}

export function useLearningCandidate(candidateId: string) {
  return useQuery({
    queryKey: ["geoIosLearningCandidate", candidateId],
    queryFn: () => fetchJson<GeoIosLearningCandidate>(`/api/geo/ios/learning-candidates/${candidateId}`),
    enabled: !!candidateId,
  });
}

export function useApproveCandidate(candidateId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { reviewed_by: string; reviewer_role: string; edited_generalized_rule?: string }) =>
      postJson<GeoIosLearningCandidate>(`/api/geo/ios/learning-candidates/${candidateId}/approve`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoIosLearningCandidate", candidateId] });
      qc.invalidateQueries({ queryKey: ["geoIosLearningCandidates"] });
    },
  });
}

export function useRejectCandidate(candidateId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { reviewed_by: string; reviewer_role: string; reason?: string }) =>
      postJson<GeoIosLearningCandidate>(`/api/geo/ios/learning-candidates/${candidateId}/reject`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoIosLearningCandidate", candidateId] });
      qc.invalidateQueries({ queryKey: ["geoIosLearningCandidates"] });
    },
  });
}

export function usePromoteCandidate(candidateId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { created_by: string; created_by_role: string; target_entity_id?: string }) =>
      postJson<{ knowledge_item_id: string; status: string; knowledge_type: string }>(
        `/api/geo/ios/learning-candidates/${candidateId}/promote`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["geoIosLearningCandidate", candidateId] }),
  });
}

export interface GeoIosIssueSuggestion {
  issue_id: string; name_ja: string; name_en?: string | null; score: number;
}

export function useSuggestIssues(candidateId: string) {
  return useQuery({
    queryKey: ["geoIosSuggestIssues", candidateId],
    queryFn: () => fetchJson<{ suggestions: GeoIosIssueSuggestion[] }>(
      `/api/geo/ios/learning-candidates/${candidateId}/suggest-issues`),
    enabled: !!candidateId,
  });
}

export function useLinkIssue(candidateId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { issue_id: string }) =>
      postJson<{ knowledge_item_id: string; issue_id: string }>(
        `/api/geo/ios/learning-candidates/${candidateId}/link-issue`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["geoIosLearningCandidate", candidateId] }),
  });
}

export interface GeoIosCqasSignalSummary {
  counts: Record<"generation_miss" | "ranking_miss" | "selection_miss" | "validated_candidate", number>;
  total_with_signal: number;
  drilldown: Array<{
    candidate_id: string; candidate_type: string; generalized_rule: string; signal: string | null;
    similarity: number | null; closest_candidate_id: string | null; status: string;
    source_report_project_id: string | null;
  }>;
}

export function useCqasSignalSummary() {
  return useQuery({
    queryKey: ["geoIosCqasSignalSummary"],
    queryFn: () => fetchJson<GeoIosCqasSignalSummary>("/api/geo/ios/learning-candidates/cqas-signals/summary"),
  });
}
