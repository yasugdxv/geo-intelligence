// Geopolitical External Intelligence Layer（Phase2A）のAPIクライアント。
// geoExternalSignalApi.tsと同じfetchJson/postJson/patchJsonパターン。
// External Event / Actor Episode Candidate / Media Episode Candidateを扱う
// （/api/geo/external-events, /api/geo/actor-episode-candidates,
//  /api/geo/media-episode-candidates）。
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

function postJson<T>(path: string, body: unknown): Promise<T> {
  return fetchJson<T>(path, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
}

function patchJson<T>(path: string, body: unknown): Promise<T> {
  return fetchJson<T>(path, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
}

export interface GeoExternalEvent {
  external_event_id: string;
  provider: string;
  provider_event_id: string;
  event_code?: string | null;
  event_base_code?: string | null;
  event_root_code?: string | null;
  goldstein_scale?: number | null;
  avg_tone?: number | null;
  actor1_name?: string | null;
  actor2_name?: string | null;
  action_geo_full_name?: string | null;
  event_date?: string | null;
  event_date_added?: string | null;
  source_url?: string | null;
  source_role: string;
  evidence_status: string;
}

export interface GeoActorEpisodeCandidate {
  actor_episode_candidate_id: string;
  actor_entity_id: string;
  matched_name_text: string;
  actor_role: "actor1" | "actor2";
  external_event_id: string;
  observed_action?: string | null;
  counterparty_text?: string | null;
  event_date?: string | null;
  external_signal_ids: string[];
  source_count: number;
  evidence_count: number;
  confidence: string;
  candidate_status: "candidate" | "reviewed" | "accepted" | "rejected";
  reviewed_at?: string | null;
  reviewed_by?: string | null;
  review_notes?: string | null;
}

export interface GeoMediaEpisodeCandidate {
  media_episode_candidate_id: string;
  media_source_id: string;
  source_domain: string;
  headline?: string | null;
  tone?: number | null;
  related_external_event_ids: string[];
  confidence: string;
  candidate_status: "candidate" | "reviewed" | "accepted" | "rejected";
  reviewed_at?: string | null;
  reviewed_by?: string | null;
  review_notes?: string | null;
}

export function useExternalEvents(params: { eventRootCode?: string; limit?: number; offset?: number } = {}) {
  const usp = new URLSearchParams();
  if (params.eventRootCode) usp.set("event_root_code", params.eventRootCode);
  usp.set("limit", String(params.limit ?? 20));
  usp.set("offset", String(params.offset ?? 0));
  return useQuery({
    queryKey: ["geoExternalEvents", params],
    queryFn: () => fetchJson<{ events: GeoExternalEvent[] }>(`/api/geo/external-events?${usp.toString()}`),
  });
}

export function useEventSignals(externalEventId: string) {
  return useQuery({
    queryKey: ["geoExternalEventSignals", externalEventId],
    queryFn: () => fetchJson<{ signals: unknown[]; links: unknown[] }>(
      `/api/geo/external-events/${externalEventId}/signals`),
    enabled: !!externalEventId,
  });
}

export function useIngestExternalEvents() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { since?: string; until?: string; max_batches?: number; requested_by?: string }) =>
      postJson<{ batches: unknown[] }>("/api/geo/external-events/ingest", body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["geoExternalEvents"] });
      queryClient.invalidateQueries({ queryKey: ["geoActorEpisodeCandidates"] });
      queryClient.invalidateQueries({ queryKey: ["geoMediaEpisodeCandidates"] });
    },
  });
}

export function useActorEpisodeCandidates(params: { candidateStatus?: string; limit?: number; offset?: number } = {}) {
  const usp = new URLSearchParams();
  if (params.candidateStatus) usp.set("candidate_status", params.candidateStatus);
  usp.set("limit", String(params.limit ?? 50));
  usp.set("offset", String(params.offset ?? 0));
  return useQuery({
    queryKey: ["geoActorEpisodeCandidates", params],
    queryFn: () => fetchJson<{ candidates: GeoActorEpisodeCandidate[] }>(
      `/api/geo/actor-episode-candidates?${usp.toString()}`),
  });
}

export function useUpdateActorEpisodeCandidateStatus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ candidateId, ...body }: { candidateId: string; candidate_status: string; reviewed_by: string; review_notes?: string }) =>
      patchJson<GeoActorEpisodeCandidate>(`/api/geo/actor-episode-candidates/${candidateId}`, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["geoActorEpisodeCandidates"] }),
  });
}

export function useMediaEpisodeCandidates(params: { candidateStatus?: string; limit?: number; offset?: number } = {}) {
  const usp = new URLSearchParams();
  if (params.candidateStatus) usp.set("candidate_status", params.candidateStatus);
  usp.set("limit", String(params.limit ?? 50));
  usp.set("offset", String(params.offset ?? 0));
  return useQuery({
    queryKey: ["geoMediaEpisodeCandidates", params],
    queryFn: () => fetchJson<{ candidates: GeoMediaEpisodeCandidate[] }>(
      `/api/geo/media-episode-candidates?${usp.toString()}`),
  });
}

export function useUpdateMediaEpisodeCandidateStatus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ candidateId, ...body }: { candidateId: string; candidate_status: string; reviewed_by: string; review_notes?: string }) =>
      patchJson<GeoMediaEpisodeCandidate>(`/api/geo/media-episode-candidates/${candidateId}`, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["geoMediaEpisodeCandidates"] }),
  });
}
