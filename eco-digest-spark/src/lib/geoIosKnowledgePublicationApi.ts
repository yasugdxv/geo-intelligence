// Geopolitical Intelligence OS Phase H: Approval / Knowledge Publication / Learning Extraction
// APIクライアント。バックエンドは/api/geo/ios/approval/*・/api/geo/ios/knowledge-publication/*
// （Phase D/E/G）にそのまま接続する。
import { API_BASE as API_BASE_URL } from "@/lib/apiBase";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getIosAuthHeaders } from "@/lib/geoIosAuthHeader";

const API_BASE = API_BASE_URL;

// 2026-09-24修正: geo_ios_approval_api.py/geo_ios_knowledge_publication_api.pyの全endpointは
// Depends(verify_ios_identity)必須だが、このAPIクライアントはX-IOS-Credentialヘッダーを
// 一切付けていなかった（geoIosReviewApi.tsと同じ取り残されバグ）。
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

function postJson<T>(path: string, body: unknown): Promise<T> {
  return fetchJson<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export interface GeoIosApprovalEvent {
  approval_event_id: string;
  report_project_id: string;
  version_label?: string | null;
  approval_status: "approved" | "rejected";
  approver: string;
  approval_date: string;
  approval_source: "in_app" | "upload";
  notes?: string | null;
  publication_status: "not_published" | "pending" | "published";
  published_knowledge_item_id?: string | null;
}

export function useApprovalEvents(reportProjectId: string) {
  return useQuery({
    queryKey: ["geoIosApprovalEvents", reportProjectId],
    queryFn: () =>
      fetchJson<{ events: GeoIosApprovalEvent[] }>(
        `/api/geo/ios/approval/projects/${reportProjectId}`,
      ),
    enabled: !!reportProjectId,
  });
}

export function useCurrentApproval(reportProjectId: string) {
  return useQuery({
    queryKey: ["geoIosCurrentApproval", reportProjectId],
    queryFn: () =>
      fetchJson<GeoIosApprovalEvent>(`/api/geo/ios/approval/projects/${reportProjectId}/current`),
    enabled: !!reportProjectId,
    retry: false,
  });
}

export function useApproveInApp(reportProjectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      approval_status: "approved" | "rejected";
      approver: string;
      notes?: string;
    }) =>
      postJson<GeoIosApprovalEvent>("/api/geo/ios/approval/in-app", {
        report_project_id: reportProjectId,
        ...body,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoIosApprovalEvents", reportProjectId] });
      qc.invalidateQueries({ queryKey: ["geoIosCurrentApproval", reportProjectId] });
    },
  });
}

export interface GeoIosPublicationResult {
  knowledge_item_id: string;
  publication_status: string;
  knowledge_item: { knowledge_item_id: string; title: string; status: string };
}

export function usePublishReportStudio(reportProjectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      approval_event_id: string;
      publisher: string;
      publisher_role: string;
      // Phase 2E: confidentiality_label指定時はhuman_visibility/agent_sharing_policyより
      // 優先される（両方省略不可、どちらか一方は必須）。
      human_visibility?: string;
      agent_sharing_policy?: string;
      confidentiality_label?: string;
    }) =>
      postJson<GeoIosPublicationResult>("/api/geo/ios/knowledge-publication/report-studio", {
        report_project_id: reportProjectId,
        ...body,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoIosApprovalEvents", reportProjectId] });
      qc.invalidateQueries({ queryKey: ["geoIosCurrentPublishedKnowledge", reportProjectId] });
    },
  });
}

export function useCurrentPublishedKnowledge(reportProjectId: string) {
  return useQuery({
    queryKey: ["geoIosCurrentPublishedKnowledge", reportProjectId],
    queryFn: () =>
      fetchJson<{
        knowledge_item_id: string;
        title: string;
        status: string;
        human_visibility: string;
        agent_sharing_policy: string;
      }>(`/api/geo/ios/knowledge-publication/report-studio/${reportProjectId}/current`),
    enabled: !!reportProjectId,
    retry: false,
  });
}

export interface GeoIosExtractLearningResult {
  candidates: Array<{ candidate_id: string; candidate_type: string }>;
  idempotent_reuse: boolean;
  deduplicated_count: number;
  conflict_count: number;
  proposed_count?: number;
  reason?: string;
}

export function useExtractLearning(reportProjectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { require_published?: boolean; actor_id?: string } = {}) =>
      postJson<GeoIosExtractLearningResult>(
        `/api/geo/ios/knowledge-publication/report-studio/${reportProjectId}/extract-learning`,
        body,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["geoIosLearningCandidates"] }),
  });
}
