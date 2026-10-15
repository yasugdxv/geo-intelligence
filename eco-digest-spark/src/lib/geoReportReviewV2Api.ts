import { API_BASE as API_BASE_URL } from "@/lib/apiBase";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { GeoIosSituationPresentation } from "@/lib/geoIosSituationPresentationApi";

const API_BASE = API_BASE_URL;

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const err = new Error(body.detail || `API error ${res.status}`) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  return res.json();
}

export interface GeoReportReviewV2ComparisonFinding {
  finding_id?: string | null;
  category?: string | null;
  severity?: string | null;
  finding?: string | null;
  recommendation?: string | null;
  evidence_ids?: string[] | null;
}

export interface GeoReportReviewV2ReportReading {
  analysis_theme?: string | null;
  current_situation?: string | null;
  question?: string | null;
  report_context?: string | null;
  cue?: string | null;
  key_actors?: string[];
  actor_objectives?: Array<{ actor: string; objective: string }>;
  relationships?: Array<{ between: string; description: string }>;
  report_primary_reading?: string | null;
  scenario?: string | null;
  switch_condition?: string | null;
  supporting_evidence?: string[];
  weakening_evidence?: string[];
  missing_evidence?: string[];
  missing_reading?: string | null;
  business_impact?: string | null;
  not_explicit_fields: string[];
}

export interface GeoReportReviewV2ComparisonRow {
  report: string | null;
  expert_ai: string | null;
}

export interface GeoReportReviewV2ComparisonSummary {
  cue: GeoReportReviewV2ComparisonRow;
  key_actors: GeoReportReviewV2ComparisonRow;
  primary_reading: GeoReportReviewV2ComparisonRow;
  supporting_evidence: GeoReportReviewV2ComparisonRow;
  weakening_or_switch_condition: GeoReportReviewV2ComparisonRow;
}

export type GeoReportReviewV2Disposition = "agree" | "modify" | "reject";

export interface GeoReportReviewV2DispositionRecord {
  source: "reading" | "content";
  finding_id: string;
  disposition: GeoReportReviewV2Disposition;
  reason: string;
  modification: string | null;
  reviewed_by: string;
  reviewed_at: string;
}

export interface GeoReportReviewV2Run {
  run_id: string;
  analysis_session_id: string;
  report_draft_id: string | null;
  status: "pending" | "completed" | "failed";
  error_message: string | null;
  report_reading: GeoReportReviewV2ReportReading | null;
  report_reading_scenario_candidate_id: string | null;
  expert_reading: { candidates: Array<{ title: string; content: Record<string, unknown> }> } | null;
  comparison_findings: GeoReportReviewV2ComparisonFinding[] | null;
  comparison_summary: GeoReportReviewV2ComparisonSummary | null;
  content_findings: GeoReportReviewV2ComparisonFinding[] | null;
  human_expert_dispositions: Record<string, GeoReportReviewV2DispositionRecord>;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export function useReportReviewV2(sessionId: string | undefined) {
  return useQuery({
    queryKey: ["geoReportReviewV2", sessionId],
    queryFn: () => fetchJson<GeoReportReviewV2Run>(`/api/geo/report-review-v2/${sessionId}`),
    enabled: !!sessionId,
    retry: false,
    refetchInterval: (query) => (query.state.data?.status === "pending" ? 4000 : false),
  });
}

export function useRunReportReviewV2(sessionId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { report_draft_id: string; requested_by: string }) =>
      fetchJson<GeoReportReviewV2Run>(`/api/geo/report-review-v2/${sessionId}/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["geoReportReviewV2", sessionId] });
    },
  });
}

export function useSaveHumanExpertDisposition(sessionId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      source: "reading" | "content";
      finding_id: string;
      disposition: GeoReportReviewV2Disposition;
      reason?: string;
      modification?: string | null;
      reviewed_by: string;
    }) =>
      fetchJson<GeoReportReviewV2Run>(`/api/geo/report-review-v2/${sessionId}/dispositions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["geoReportReviewV2", sessionId] });
    },
  });
}

// Expert AI Reading側は既存Reading Presentation v2（ReadingPresentationV2Report）で
// そのまま表示する。Report Reading側はExtraction結果しか持たず構造が異なるため対象外
// （専用の軽量カードで表示する、ユーザー指示）。
export function useExpertReadingPresentation(sessionId: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["geoReportReviewV2ExpertPresentation", sessionId],
    queryFn: () =>
      fetchJson<GeoIosSituationPresentation>(
        `/api/geo/report-review-v2/${sessionId}/expert-reading-presentation`,
      ),
    enabled: !!sessionId && enabled,
    retry: false,
  });
}
