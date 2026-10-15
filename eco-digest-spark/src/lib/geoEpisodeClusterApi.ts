// Geopolitical External Intelligence Layer（Phase2B-1: Event/Episode Clustering）のAPIクライアント。
// geoExternalEventApi.tsと同じfetchJson/postJson/patchJsonパターン。
// Actor Episode Cluster（/api/geo/episode-clusters）を扱う。
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

export interface GeoEpisodeCluster {
  episode_cluster_id: string;
  actor_entity_id: string;
  quad_class: number;
  event_root_code: string;
  is_demo: boolean;
  cluster_sequence: number;
  member_count: number;
  first_event_date?: string | null;
  last_event_date?: string | null;
  goldstein_min?: number | null;
  goldstein_max?: number | null;
  trajectory: "insufficient_data" | "stable" | "escalating" | "de_escalating" | "mixed";
  representative_counterparties: string[];
  representative_geo: string[];
  cluster_status: "candidate" | "reviewed" | "accepted" | "rejected";
  reviewed_at?: string | null;
  reviewed_by?: string | null;
  review_notes?: string | null;
}

export function useEpisodeClusters(params: { actorEntityId?: string; clusterStatus?: string; limit?: number; offset?: number } = {}) {
  const usp = new URLSearchParams();
  if (params.actorEntityId) usp.set("actor_entity_id", params.actorEntityId);
  if (params.clusterStatus) usp.set("cluster_status", params.clusterStatus);
  usp.set("limit", String(params.limit ?? 50));
  usp.set("offset", String(params.offset ?? 0));
  return useQuery({
    queryKey: ["geoEpisodeClusters", params],
    queryFn: () => fetchJson<{ clusters: GeoEpisodeCluster[] }>(`/api/geo/episode-clusters?${usp.toString()}`),
  });
}

export function useEpisodeClusterMembers(episodeClusterId: string, enabled = true) {
  return useQuery({
    queryKey: ["geoEpisodeClusterMembers", episodeClusterId],
    queryFn: () => fetchJson<{ members: unknown[] }>(`/api/geo/episode-clusters/${episodeClusterId}/members`),
    enabled: enabled && !!episodeClusterId,
  });
}

export function useUpdateEpisodeClusterStatus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ episodeClusterId, ...body }: { episodeClusterId: string; cluster_status: string; reviewed_by: string; review_notes?: string }) =>
      patchJson<GeoEpisodeCluster>(`/api/geo/episode-clusters/${episodeClusterId}`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["geoEpisodeClusters"] });
      // Phase C: Accept/Reject/Mark ReviewedはEpisode Pipelineの滞留件数（レビュー待ち/
      // 承認済み/Theme同期待ち）にも影響するため、Theme Candidate側のPipeline表示も
      // 連動して更新する。
      queryClient.invalidateQueries({ queryKey: ["geoThemeCandidatePipelineStatus"] });
      queryClient.invalidateQueries({ queryKey: ["geoThemeCandidatePendingSync"] });
      queryClient.invalidateQueries({ queryKey: ["geoOperationsSummary"] });
    },
  });
}
