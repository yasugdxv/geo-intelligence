// Geopolitical News DB / External Intelligence Layer（Phase1）のAPIクライアント。
// geoKnowledgeApi.tsと同じfetchJson/postJson/patchJsonパターン。既存Knowledge/Documents
// のAPIとは独立したエンドポイント（/api/geo/external-signals/*）を叩く。
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

export interface GeoExternalSignal {
  external_signal_id: string;
  provider: string;
  title?: string | null;
  summary?: string | null;
  url?: string | null;
  canonical_url?: string | null;
  source_domain?: string | null;
  source_name?: string | null;
  source_country?: string | null;
  source_language?: string | null;
  published_at?: string | null;
  timestamp_source: string;
  observed_at: string;
  last_observed_at: string;
  source_role: string;
  evidence_status: string;
  validation_status: string;
  knowledge_promotion_status: string;
}

export interface GeoExternalSignalQuery {
  query_id: string;
  name: string;
  provider: string;
  query: string;
  theme_id?: string | null;
  case_id?: string | null;
  enabled: boolean;
  article_lookback: string;
  metric_lookback: string;
  max_records: number;
  last_run_at?: string | null;
}

export interface GeoExternalSignalSummary {
  signals_24h: number;
  signals_7d: number;
  sources: number;
  countries: number;
}

export interface GeoExternalSignalTopic {
  query_id: string;
  name: string;
  current_count: number;
  previous_count: number;
  // GDELTのnorm(その時間帯の全体監視記事数)で正規化したcoverage share。change_pctはこの
  // shareの前後比較であり、current_count/previous_count(raw件数、表示用)の比較ではない。
  current_coverage_share: number | null;
  previous_coverage_share: number | null;
  change_pct: number | null;
}

export function useExternalSignals(params: {
  themeId?: string; provider?: string; sourceCountry?: string; sourceDomain?: string;
  limit?: number; offset?: number;
} = {}) {
  const usp = new URLSearchParams();
  if (params.themeId) usp.set("theme_id", params.themeId);
  if (params.provider) usp.set("provider", params.provider);
  if (params.sourceCountry) usp.set("source_country", params.sourceCountry);
  if (params.sourceDomain) usp.set("source_domain", params.sourceDomain);
  usp.set("limit", String(params.limit ?? 50));
  usp.set("offset", String(params.offset ?? 0));
  return useQuery({
    queryKey: ["geoExternalSignals", params],
    queryFn: () => fetchJson<{ items: GeoExternalSignal[]; total_hint: number }>(
      `/api/geo/external-signals?${usp.toString()}`),
  });
}

export interface ExternalSignalSearchRequest {
  text?: string; date_from?: string; date_to?: string; country?: string; provider?: string;
  query_id?: string; theme_id?: string; source_domain?: string; limit?: number; offset?: number;
}

export function useExternalSignalSearch() {
  return useMutation({
    mutationFn: (body: ExternalSignalSearchRequest) =>
      postJson<{ items: GeoExternalSignal[]; total_hint: number }>("/api/geo/external-signals/search", body),
  });
}

export function useExternalSignalSummary(themeId?: string) {
  const usp = new URLSearchParams();
  if (themeId) usp.set("theme_id", themeId);
  return useQuery({
    queryKey: ["geoExternalSignalSummary", themeId],
    queryFn: () => fetchJson<GeoExternalSignalSummary>(`/api/geo/external-signals/summary?${usp.toString()}`),
  });
}

export function useExternalSignalTopics(sinceDays = 7) {
  return useQuery({
    queryKey: ["geoExternalSignalTopics", sinceDays],
    queryFn: () => fetchJson<{ topics: GeoExternalSignalTopic[] }>(
      `/api/geo/external-signals/topics?since_days=${sinceDays}`),
  });
}

export function useExternalSignalQueries(params: { themeId?: string; provider?: string } = {}) {
  const usp = new URLSearchParams();
  if (params.themeId) usp.set("theme_id", params.themeId);
  if (params.provider) usp.set("provider", params.provider);
  return useQuery({
    queryKey: ["geoExternalSignalQueries", params],
    queryFn: () => fetchJson<{ queries: GeoExternalSignalQuery[] }>(
      `/api/geo/external-signals/queries?${usp.toString()}`),
  });
}

export function useUpdateExternalSignalQuery() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ queryId, ...patch }: { queryId: string; enabled?: boolean }) =>
      patchJson<GeoExternalSignalQuery>(`/api/geo/external-signals/queries/${queryId}`, patch),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["geoExternalSignalQueries"] }),
  });
}

export function useRunExternalSignalFetch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { query_id: string; requested_by?: string }) =>
      postJson<{ ingestion_run_id: string; status: string; counts: Record<string, number> }>(
        "/api/geo/external-signals/fetch", body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["geoExternalSignals"] });
      queryClient.invalidateQueries({ queryKey: ["geoExternalSignalSummary"] });
      queryClient.invalidateQueries({ queryKey: ["geoExternalSignalTopics"] });
      queryClient.invalidateQueries({ queryKey: ["geoExternalSignalQueries"] });
    },
  });
}
