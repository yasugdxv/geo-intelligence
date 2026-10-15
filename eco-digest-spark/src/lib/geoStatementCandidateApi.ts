// Geopolitical External Intelligence Layer（Phase2B-3A-1: Statement Candidate）のAPIクライアント。
// geoSplitCandidateApi.tsと同じfetchJson/patchJsonパターン。GKG V2.1QUOTATIONS由来の引用・声明
// 候補（/api/geo/statement-candidates）を扱う。speaker identityはHuman Reviewでのみ確定する。
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

export interface GeoStatementSpeakerCandidate {
  name: string;
  speaker_type: "person" | "organization" | "unknown";
  source: "allnames" | "persons" | "organizations";
  distance_chars: number;
  role_text?: string | null;
}

export interface GeoStatementCandidate {
  statement_candidate_id: string;
  external_signal_id: string;
  annotation_id: string;
  quote_offset: number;
  quote_length: number;
  quote_text: string;
  statement_kind: "direct_quote_candidate" | "attributed_statement_candidate" | "quotation_fragment";
  speaker_candidates: GeoStatementSpeakerCandidate[];
  speaker_attribution_status: "explicit" | "proximate_name" | "ambiguous" | "unknown";
  episode_link_status: "linked" | "ambiguous" | "unlinked";
  linked_actor_episode_candidate_ids: string[];
  episode_cluster_id?: string | null;
  episode_evidence_status?: "normal" | "split_group" | "outlier_pending" | "unresolved_hold" | null;
  split_candidate_id?: string | null;
  candidate_status: "candidate" | "reviewed" | "accepted" | "rejected";
  rejection_reason?: "interpretive_fragment" | "other" | null;
  confirmed_statement_kind?: "direct_quote" | "attributed_statement" | null;
  confirmed_statement_text?: string | null;
  confirmed_speaker_name?: string | null;
  confirmed_speaker_type?: "person" | "organization" | null;
  reviewed_at?: string | null;
  reviewed_by?: string | null;
  review_notes?: string | null;
}

export function useStatementCandidates(params: { externalSignalId?: string; episodeLinkStatus?: string; statementKind?: string; candidateStatus?: string; limit?: number; offset?: number } = {}) {
  const usp = new URLSearchParams();
  if (params.externalSignalId) usp.set("external_signal_id", params.externalSignalId);
  if (params.episodeLinkStatus) usp.set("episode_link_status", params.episodeLinkStatus);
  if (params.statementKind) usp.set("statement_kind", params.statementKind);
  if (params.candidateStatus) usp.set("candidate_status", params.candidateStatus);
  usp.set("limit", String(params.limit ?? 50));
  usp.set("offset", String(params.offset ?? 0));
  return useQuery({
    queryKey: ["geoStatementCandidates", params],
    queryFn: () => fetchJson<{ statement_candidates: GeoStatementCandidate[] }>(`/api/geo/statement-candidates?${usp.toString()}`),
    enabled: !!params.externalSignalId,
  });
}

export function useUpdateStatementCandidateStatus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ statementCandidateId, ...body }: {
      statementCandidateId: string; candidate_status: string; reviewed_by: string; review_notes?: string;
      rejection_reason?: string; confirmed_statement_kind?: string; confirmed_statement_text?: string;
      confirmed_speaker_name?: string; confirmed_speaker_type?: string;
    }) => patchJson<GeoStatementCandidate>(`/api/geo/statement-candidates/${statementCandidateId}`, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["geoStatementCandidates"] }),
  });
}
