// 地政学インテリジェンス基盤 Phase3: Intelligence Needs（External Request由来Knowledge Gap）
// DashboardのAPIクライアント。既存geoKnowledgeApi.tsとは意図的に分離する
// （Case向けgeo_knowledge_gapsとは別の管理画面のため）。
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
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// Report Studio起源の場合、geo_report_monitoring_service.create_gap_from_report_studioが
// 書き込むDeep Link用メタデータ（sql/2026-09-02_geo_report_studio_deep_link_schema.sql）。
export interface GeoIntelligenceNeedSourceContext {
  app?: string;
  project_id?: string;
  run_id?: string;
  signal_id?: string;
  narrative_id?: string;
  synthesis_id?: string;
}

export interface GeoIntelligenceNeed {
  gap_id: string;
  origin_type: string;
  requested_by_app?: string | null;
  request_type?: string | null;
  question?: string | null;
  country_region: string[];
  topic: string[];
  business_context?: string | null;
  missing_knowledge?: string[] | null;
  priority: "high" | "medium" | "low";
  status: string;
  assigned_to?: string | null;
  request_count: number;
  created_at: string;
  resolved_at?: string | null;
  duplicate_of_gap_id?: string | null;
  source_context?: GeoIntelligenceNeedSourceContext | null;
}

export interface GeoIntelligenceRequestRecord {
  request_id: string;
  requester_app: string;
  request_type: string;
  question: string;
  status: string;
  created_at: string;
}

export interface GeoIntelligenceNeedDetail {
  gap: GeoIntelligenceNeed;
  original_question: string | null;
  related_requests: GeoIntelligenceRequestRecord[];
  business_context: string | null;
  what_we_know: Record<string, unknown>[];
  what_is_missing: string[];
  related_existing_knowledge: Record<string, unknown>[];
  recommended_research: string[];
  priority: string;
  assigned_to: string | null;
  status: string;
  resolution: { resolved_at: string | null; resolution_report_id: string | null };
}

export function useIntelligenceNeeds(status?: string) {
  const usp = new URLSearchParams();
  if (status) usp.set("status", status);
  const qs = usp.toString();
  return useQuery({
    queryKey: ["geoIntelligenceNeeds", status ?? "all"],
    queryFn: () =>
      fetchJson<{ needs: GeoIntelligenceNeed[] }>(
        `/api/geo/intelligence-needs${qs ? `?${qs}` : ""}`,
      ),
  });
}

export function useIntelligenceNeedDetail(gapId: string) {
  return useQuery({
    queryKey: ["geoIntelligenceNeed", gapId],
    queryFn: () => fetchJson<GeoIntelligenceNeedDetail>(`/api/geo/intelligence-needs/${gapId}`),
    enabled: !!gapId,
  });
}

export function useUpdateIntelligenceNeed() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: {
      gapId: string;
      status?: string;
      assigned_to?: string;
      actor_id?: string;
    }) =>
      patchJson(`/api/geo/intelligence-needs/${params.gapId}`, {
        status: params.status,
        assigned_to: params.assigned_to,
        actor_id: params.actor_id ?? "analyst",
      }),
    onSuccess: (_data, params) => {
      qc.invalidateQueries({ queryKey: ["geoIntelligenceNeeds"] });
      qc.invalidateQueries({ queryKey: ["geoIntelligenceNeed", params.gapId] });
    },
  });
}

export interface GeoReport {
  report_id: string;
  document_id: string;
  author?: string | null;
  report_type?: string | null;
}

async function uploadReportFile(params: {
  file: File;
  countryRegion: string[];
  topics: string[];
  author?: string;
  themeId?: string;
  gapId: string;
  actorId: string;
}): Promise<{ report: GeoReport; document: Record<string, unknown> }> {
  const form = new FormData();
  form.append("file", params.file);
  form.append("country_region", JSON.stringify(params.countryRegion));
  form.append("topics", JSON.stringify(params.topics));
  form.append("author", params.author ?? "");
  form.append("theme_id", params.themeId ?? "");
  form.append("linked_gap_ids", JSON.stringify([params.gapId]));
  form.append("actor_id", params.actorId);
  const res = await fetch(`${API_BASE}/api/geo/reports`, { method: "POST", body: form });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `API error ${res.status}`);
  }
  return res.json();
}

// Add Resolution Report: Report Uploadとgeo_intelligence-needs/{gap_id}/resolveを1つの操作として
// 連結する（「単にstatusだけ変え、Reportと紐付かない状態にはしない」というPhase4の原則に合わせ、
// UI操作としても「Reportを追加してResolveする」を一体の導線にする）。
export function useAddResolutionReport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (params: {
      gapId: string;
      file: File;
      countryRegion: string[];
      topics: string[];
      author?: string;
      themeId?: string;
      resolvedBy: string;
      resolutionNote?: string;
      actorId?: string;
    }) => {
      const uploaded = await uploadReportFile({
        file: params.file,
        countryRegion: params.countryRegion,
        topics: params.topics,
        author: params.author,
        themeId: params.themeId,
        gapId: params.gapId,
        actorId: params.actorId ?? "analyst",
      });
      return fetchJson(`/api/geo/intelligence-needs/${params.gapId}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          report_id: uploaded.report.report_id,
          resolved_by: params.resolvedBy,
          resolution_note: params.resolutionNote,
          actor_id: params.actorId ?? "analyst",
        }),
      });
    },
    onSuccess: (_data, params) => {
      qc.invalidateQueries({ queryKey: ["geoIntelligenceNeeds"] });
      qc.invalidateQueries({ queryKey: ["geoIntelligenceNeed", params.gapId] });
    },
  });
}

export function useDismissIntelligenceNeed() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: {
      gapId: string;
      dismissedBy: string;
      reason?: string;
      actorId?: string;
    }) =>
      fetchJson(`/api/geo/intelligence-needs/${params.gapId}/dismiss`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          dismissed_by: params.dismissedBy,
          reason: params.reason,
          actor_id: params.actorId ?? "analyst",
        }),
      }),
    onSuccess: (_data, params) => {
      qc.invalidateQueries({ queryKey: ["geoIntelligenceNeeds"] });
      qc.invalidateQueries({ queryKey: ["geoIntelligenceNeed", params.gapId] });
    },
  });
}

export function useGapForRequest(requestId: string) {
  return useQuery({
    queryKey: ["geoIntelligenceGapForRequest", requestId],
    queryFn: () =>
      fetchJson<GeoIntelligenceNeed>(`/api/geo/intelligence-needs/by-request/${requestId}`),
    enabled: !!requestId,
    retry: false,
  });
}
