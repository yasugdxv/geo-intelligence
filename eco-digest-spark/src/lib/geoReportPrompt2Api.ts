// Report Studio「Design」領域: Python Aggregation + Prompt2 Application AI
// （geo_report_prompt2_api.py、Phase E）のAPIクライアント。
// Execution進捗ポーリングはgeoReportCurrentPipelineApi.tsのuseGeoReportExecutionStatusを
// 共用する（stage非依存の汎用エンドポイントのため、ここでは重複させない）。
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

// ---------------------------------------------------------------------------
// 型
// ---------------------------------------------------------------------------

export interface RunAggregationResult {
  execution_id: string;
  biweekly_period_count: number;
  weekly_period_count: number;
  monthly_period_count: number;
}

export interface PlanPrompt2Result {
  execution_id: string;
  batch_count: number;
}

export interface Prompt2CompositionBatch {
  id: string;
  run_id: string;
  execution_id: string;
  stage: "prompt2_composition";
  batch_no: number;
  item_ids: string[];
  status: "pending" | "running" | "completed" | "failed" | "skipped";
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface FinalizePrompt2Result {
  completed: boolean;
  final_report_html?: string;
}

// ---------------------------------------------------------------------------
// Aggregation
// ---------------------------------------------------------------------------

export function useRunAggregation(projectId: string, runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      postJson<RunAggregationResult>(
        `/api/geo/report-studio/projects/${projectId}/runs/${runId}/aggregation/run`,
        {},
      ),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["geoReportRunExecutions", projectId, runId] }),
  });
}

// ---------------------------------------------------------------------------
// Prompt2 Composition
// ---------------------------------------------------------------------------

export function usePlanPrompt2Execution(projectId: string, runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (aggregationExecutionId: string) =>
      postJson<PlanPrompt2Result>(
        `/api/geo/report-studio/projects/${projectId}/runs/${runId}/prompt2/plan`,
        { aggregation_execution_id: aggregationExecutionId },
      ),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["geoReportRunExecutions", projectId, runId] }),
  });
}

export function usePrompt2Batches(projectId: string, runId: string, executionId: string | null) {
  return useQuery({
    queryKey: ["geoReportPrompt2Batches", projectId, runId, executionId],
    queryFn: () =>
      fetchJson<{ batches: Prompt2CompositionBatch[] }>(
        `/api/geo/report-studio/projects/${projectId}/runs/${runId}/prompt2/batches?execution_id=${executionId}`,
      ),
    enabled: !!projectId && !!runId && !!executionId,
  });
}

export function useRunPrompt2Batch(projectId: string, runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (executionId: string) =>
      postJson<Record<string, unknown>>(
        `/api/geo/report-studio/projects/${projectId}/runs/${runId}/prompt2/run-batch`,
        { execution_id: executionId },
      ),
    onSuccess: (_data, executionId) => {
      qc.invalidateQueries({
        queryKey: ["geoReportPrompt2Batches", projectId, runId, executionId],
      });
      qc.invalidateQueries({ queryKey: ["geoReportExecutionStatus", executionId] });
    },
  });
}

export function useRunAllPrompt2Batches(projectId: string, runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { executionId: string; maxBatches?: number }) =>
      postJson<{ results: Array<Record<string, unknown>> }>(
        `/api/geo/report-studio/projects/${projectId}/runs/${runId}/prompt2/run-all`,
        { execution_id: args.executionId, max_batches: args.maxBatches ?? null },
      ),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({
        queryKey: ["geoReportPrompt2Batches", projectId, runId, variables.executionId],
      });
      qc.invalidateQueries({ queryKey: ["geoReportExecutionStatus", variables.executionId] });
    },
  });
}

export function useFinalizePrompt2(projectId: string, runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { executionId: string; reportTheme?: string }) =>
      postJson<FinalizePrompt2Result>(
        `/api/geo/report-studio/projects/${projectId}/runs/${runId}/prompt2/finalize`,
        { execution_id: args.executionId, report_theme: args.reportTheme ?? "" },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoReportRunExecutions", projectId, runId] });
      qc.invalidateQueries({ queryKey: ["geoReportFinalReport", projectId, runId] });
    },
  });
}
