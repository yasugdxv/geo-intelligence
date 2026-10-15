// Geopolitical Intelligence OS Phase H: Expert Review Workflow APIクライアント。
// バックエンドは/api/geo/ios/review/*（geo_ios_review_api.py、Phase D）にそのまま接続する。
import { API_BASE as API_BASE_URL } from "@/lib/apiBase";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getIosAuthHeaders } from "@/lib/geoIosAuthHeader";

const API_BASE = API_BASE_URL;

// 2026-09-24修正、ユーザー報告「Request Expert Reviewが『無効または未指定の
// X-IOS-Credentialです』で失敗する」への対応: このファイルのfetchJsonがX-IOS-Credential
// ヘッダーを一切付けていなかった（geo_ios_review_api.pyの全endpointはPhase I-A.1
// P0 RemediationでDepends(verify_ios_identity)必須になっている一方、このAPIクライアント
// だけそれ以前のまま取り残されていた——geoIosIdentityApi.ts/geoIosWorkflowBoardApi.tsは
// 既に対応済みだった）。
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

export interface GeoIosReviewCycle {
  review_cycle_id: string;
  report_project_id: string;
  analysis_session_id?: string | null;
  round_number: number;
  reviewer: string;
  message?: string | null;
  reviewed_version_label?: string | null;
  version_snapshot?: { run_id?: string; syntheses?: Array<{ final_html?: string; final_status?: string }> } | null;
  requested_by: string;
  requested_at: string;
  due_date?: string | null;
  status: "requested" | "in_review" | "completed" | "superseded";
  findings?: Record<string, unknown> | null;
  author_response?: string | null;
  result?: "approved" | "revision_requested" | "rejected" | null;
  completed_at?: string | null;
}

export interface GeoIosReviewComment {
  review_comment_id: string;
  review_cycle_id: string;
  report_project_id: string;
  anchor_type?: "section" | "paragraph" | "claim" | "evidence" | "general" | null;
  anchor_id?: string | null;
  comment: string;
  severity?: "critical" | "major" | "minor" | null;
  created_by: string;
  created_at: string;
  resolved_status: "open" | "resolved" | "partially_resolved" | "rejected" | "not_applicable";
  author_response?: string | null;
  resolved_in_version?: string | null;
}

export function useRequestReviewCycle(reportProjectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      reviewer: string; requested_by: string; message?: string; due_date?: string;
      // IOS Native Draft: 渡さないとBackend側でNative/Legacyの判定ができず、Native
      // SessionでもReport Studio(legacy)経路にフォールバックしてしまう。
      analysis_session_id?: string | null;
    }) =>
      postJson<GeoIosReviewCycle>("/api/geo/ios/review/cycles", { report_project_id: reportProjectId, ...body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["geoIosReviewCycles", reportProjectId] }),
  });
}

export function useReviewCycles(reportProjectId: string) {
  return useQuery({
    queryKey: ["geoIosReviewCycles", reportProjectId],
    queryFn: () => fetchJson<{ cycles: GeoIosReviewCycle[] }>(
      `/api/geo/ios/review/projects/${reportProjectId}/cycles`),
    enabled: !!reportProjectId,
  });
}

export function useReviewSummary(reportProjectId: string) {
  return useQuery({
    queryKey: ["geoIosReviewSummary", reportProjectId],
    queryFn: () => fetchJson<{
      report_project_id: string;
      report_project: { project_id: string; project_code: string; project_title: string; status: string } | null;
      total_rounds: number;
      latest_cycle: GeoIosReviewCycle | null;
      cycles: GeoIosReviewCycle[];
      analysis_session_id: string | null;
      report_generation_mode: "native" | "report_studio" | null;
    }>(`/api/geo/ios/review/projects/${reportProjectId}/summary`),
    enabled: !!reportProjectId,
  });
}

export function useReviewCycle(reviewCycleId: string) {
  return useQuery({
    queryKey: ["geoIosReviewCycle", reviewCycleId],
    queryFn: () => fetchJson<GeoIosReviewCycle>(`/api/geo/ios/review/cycles/${reviewCycleId}`),
    enabled: !!reviewCycleId,
  });
}

export function useComments(reviewCycleId: string) {
  return useQuery({
    queryKey: ["geoIosReviewComments", reviewCycleId],
    queryFn: () => fetchJson<{ comments: GeoIosReviewComment[] }>(
      `/api/geo/ios/review/cycles/${reviewCycleId}/comments`),
    enabled: !!reviewCycleId,
  });
}

export function useAddComment(reviewCycleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { comment: string; created_by: string; anchor_type?: string; anchor_id?: string; severity?: string }) =>
      postJson<GeoIosReviewComment>(`/api/geo/ios/review/cycles/${reviewCycleId}/comments`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["geoIosReviewComments", reviewCycleId] }),
  });
}

export function useRespondToComment(reviewCycleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ reviewCommentId, ...body }: {
      reviewCommentId: string; resolution_status: string; author_response?: string;
      resolved_in_version?: string; actor_id: string;
    }) => postJson<GeoIosReviewComment>(`/api/geo/ios/review/comments/${reviewCommentId}/respond`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["geoIosReviewComments", reviewCycleId] }),
  });
}

export function useSubmitReviewFindings(reviewCycleId: string, reportProjectId?: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { findings: Record<string, unknown>; result: string; reviewer: string }) =>
      postJson<GeoIosReviewCycle>(`/api/geo/ios/review/cycles/${reviewCycleId}/submit`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoIosReviewCycle", reviewCycleId] });
      if (reportProjectId) {
        qc.invalidateQueries({ queryKey: ["geoIosReviewCycles", reportProjectId] });
        qc.invalidateQueries({ queryKey: ["geoIosReviewSummary", reportProjectId] });
      }
    },
  });
}

export function useStartReview(reviewCycleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { actor_id: string }) =>
      postJson<GeoIosReviewCycle>(`/api/geo/ios/review/cycles/${reviewCycleId}/start`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["geoIosReviewCycle", reviewCycleId] }),
  });
}
