// Report Studio v2 Phase 1: Report Edit Chat（自然言語によるFinal Report部分編集）の
// APIクライアント（geo_report_report_edit_api.py）。既存のgeoReportPromptExportApi.tsと
// 同じ自己完結パターン（ファイルごとにfetchJson等を持つ）を踏襲する。
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
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export interface GeoReportEditResult {
  execution_id: string;
  edit_level: "local" | "multi_section" | "analytical_recomposition_required";
  affected_section_ids: string[];
  final_report_html: string | null;
  reason: string | null;
  warning: string | null;
  requires_prompt2_recomposition: boolean;
}

export interface GeoReportEditHistoryEntry {
  execution_id: string;
  stage: "prompt2" | "report_edit";
  status: string;
  created_at: string;
  parent_execution_id: string | null;
  edit_instruction: {
    instruction: string;
    edit_level: string;
    affected_section_ids: string[];
    reason: string | null;
    warning: string | null;
  } | null;
  is_current_report: boolean;
  is_current_prompt2: boolean;
}

export function useRequestReportEdit(projectId: string, runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (instruction: string) =>
      postJson<GeoReportEditResult>(
        `/api/geo/report-studio/projects/${projectId}/runs/${runId}/report-edit`,
        { instruction },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoReportFinalReport", projectId, runId] });
      qc.invalidateQueries({ queryKey: ["geoReportEditHistory", projectId, runId] });
    },
  });
}

export function useGeoReportEditHistory(projectId: string, runId: string) {
  return useQuery({
    queryKey: ["geoReportEditHistory", projectId, runId],
    queryFn: () =>
      fetchJson<{ history: GeoReportEditHistoryEntry[] }>(
        `/api/geo/report-studio/projects/${projectId}/runs/${runId}/report-edit/history`,
      ),
    enabled: !!projectId && !!runId,
  });
}

export function useUseReportExecutionAsCurrent(projectId: string, runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (executionId: string) =>
      postJson<{ run_id: string; stage: string; canonical_execution_id: string }>(
        `/api/geo/report-studio/projects/${projectId}/runs/${runId}/report-edit/use-as-current`,
        { execution_id: executionId },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoReportFinalReport", projectId, runId] });
      qc.invalidateQueries({ queryKey: ["geoReportEditHistory", projectId, runId] });
    },
  });
}
