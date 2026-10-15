// Geopolitical External Intelligence Layer（Phase2B-2A: Situation Linkage）のAPIクライアント。
// geoEpisodeClusterApi.tsと同じfetchJson/patchJsonパターン。
// Episode Cluster間のN:Mリンク（/api/geo/situation-links）を扱う。
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

export interface GeoSituationLinkReason {
  signal: string;
  value: unknown;
}

export interface GeoSituationLink {
  cluster_link_id: string;
  source_cluster_id: string;
  target_cluster_id: string;
  is_demo: boolean;
  link_type: "same_situation_candidate" | "related_context";
  link_score: number;
  link_reasons: GeoSituationLinkReason[];
  link_status: "candidate" | "reviewed" | "accepted" | "rejected";
  reviewed_at?: string | null;
  reviewed_by?: string | null;
  review_notes?: string | null;
}

export function useSituationLinks(params: { clusterId?: string; linkStatus?: string; linkType?: string; limit?: number; offset?: number } = {}) {
  const usp = new URLSearchParams();
  if (params.clusterId) usp.set("cluster_id", params.clusterId);
  if (params.linkStatus) usp.set("link_status", params.linkStatus);
  if (params.linkType) usp.set("link_type", params.linkType);
  usp.set("limit", String(params.limit ?? 50));
  usp.set("offset", String(params.offset ?? 0));
  return useQuery({
    queryKey: ["geoSituationLinks", params],
    queryFn: () => fetchJson<{ links: GeoSituationLink[] }>(`/api/geo/situation-links?${usp.toString()}`),
    enabled: !!params.clusterId,
  });
}

export function useUpdateSituationLinkStatus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ clusterLinkId, ...body }: { clusterLinkId: string; link_status: string; reviewed_by: string; review_notes?: string }) =>
      patchJson<GeoSituationLink>(`/api/geo/situation-links/${clusterLinkId}`, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["geoSituationLinks"] }),
  });
}
