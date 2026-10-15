// Geopolitical External Intelligence Layer（Phase2B-2B: Episode Split / Outlier Candidate）の
// APIクライアント。geoSituationLinkApi.tsと同じfetchJson/patchJsonパターン。
// Episode Cluster内部の再分割提案（/api/geo/split-candidates）を扱う。既存Cluster membershipは
// 一切変更しない、あくまで人間向けの提案層。
import { API_BASE as API_BASE_URL } from "@/lib/apiBase";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

const API_BASE = API_BASE_URL;

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `API error ${res.status}`);
  }
  return res.json();
}

function patchJson<T>(path: string, body: unknown): Promise<T> {
  return fetchJson<T>(path, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
}

export interface GeoSplitCandidateSeparationEvidence {
  signal: string;
  value: unknown;
  [key: string]: unknown;
}

export interface GeoSplitCandidateCrossClusterAffinity {
  target_cluster_id: string;
  shared_signal_ids: string[];
  existing_situation_link_id?: string | null;
  existing_link_type?: string | null;
}

export interface GeoSplitCandidate {
  split_candidate_id: string;
  parent_cluster_id: string;
  candidate_type: "split_group" | "artifact_group" | "outlier";
  sibling_group_key: string;
  fingerprint: string;
  member_candidate_ids: string[];
  dominant_event_base_code: string | null;
  representative_geo: string[];
  representative_counterparties: string[];
  separation_evidence: GeoSplitCandidateSeparationEvidence[];
  cross_cluster_affinity: GeoSplitCandidateCrossClusterAffinity[];
  candidate_status: "candidate" | "reviewed" | "accepted" | "rejected";
  reviewed_at?: string | null;
  reviewed_by?: string | null;
  review_notes?: string | null;
}

export function useSplitCandidates(params: { parentClusterId?: string; candidateType?: string; candidateStatus?: string; limit?: number; offset?: number } = {}) {
  const usp = new URLSearchParams();
  if (params.parentClusterId) usp.set("parent_cluster_id", params.parentClusterId);
  if (params.candidateType) usp.set("candidate_type", params.candidateType);
  if (params.candidateStatus) usp.set("candidate_status", params.candidateStatus);
  usp.set("limit", String(params.limit ?? 50));
  usp.set("offset", String(params.offset ?? 0));
  return useQuery({
    queryKey: ["geoSplitCandidates", params],
    queryFn: () => fetchJson<{ split_candidates: GeoSplitCandidate[] }>(`/api/geo/split-candidates?${usp.toString()}`),
    enabled: !!params.parentClusterId,
  });
}

export function useUpdateSplitCandidateStatus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ splitCandidateId, ...body }: { splitCandidateId: string; candidate_status: string; reviewed_by: string; review_notes?: string }) =>
      patchJson<GeoSplitCandidate>(`/api/geo/split-candidates/${splitCandidateId}`, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["geoSplitCandidates"] }),
  });
}
