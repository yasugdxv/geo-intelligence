// Report Studio「Design」領域: ChatGPT Fallback Export/Import
// （geo_report_prompt_export_api.py、Phase F）のAPIクライアント。
// Export系はZIPバイナリを返すため、Narrative Master Download（geoReportDesignApi.ts）と
// 同様にURLビルダー関数として公開する（ブラウザの<a href download>で使う想定）。
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

async function postForm<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { method: "POST", body: form });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `API error ${res.status}`);
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Final Report（canonical_report_execution_idが指すHTMLの読み出し、全stage共通）
// ---------------------------------------------------------------------------

export function useGeoReportFinalReport(projectId: string, runId: string) {
  return useQuery({
    queryKey: ["geoReportFinalReport", projectId, runId],
    queryFn: () =>
      fetchJson<{ html: string }>(
        `/api/geo/report-studio/projects/${projectId}/runs/${runId}/final-report`,
      ),
    enabled: !!projectId && !!runId,
    retry: false,
  });
}

// ---------------------------------------------------------------------------
// Export（ZIPダウンロードURL）
// ---------------------------------------------------------------------------

export function geoReportPrompt1ExportUrl(projectId: string, runId: string): string {
  return `${API_BASE}/api/geo/report-studio/projects/${projectId}/runs/${runId}/prompt1/export`;
}

export function geoReportPrompt2ExportUrl(projectId: string, runId: string): string {
  return `${API_BASE}/api/geo/report-studio/projects/${projectId}/runs/${runId}/prompt2/export`;
}

// Combined Session（Prompt1+Prompt2を1セッションで通しで実行、2026-09-16追加）。
// Preprocess完了直後（Canonical Prompt1不要）に呼べる。
export function geoReportCombinedExportUrl(projectId: string, runId: string): string {
  return `${API_BASE}/api/geo/report-studio/projects/${projectId}/runs/${runId}/combined/export`;
}

// ---------------------------------------------------------------------------
// Import
// ---------------------------------------------------------------------------

export interface GeoReportPromptImportResult {
  execution_id: string;
}

export function useImportPrompt1Package(projectId: string, runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return postForm<GeoReportPromptImportResult>(
        `/api/geo/report-studio/projects/${projectId}/runs/${runId}/prompt1/import`,
        form,
      );
    },
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["geoReportRunExecutions", projectId, runId] }),
  });
}

export function useImportPrompt2Package(projectId: string, runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return postForm<GeoReportPromptImportResult>(
        `/api/geo/report-studio/projects/${projectId}/runs/${runId}/prompt2/import`,
        form,
      );
    },
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["geoReportRunExecutions", projectId, runId] }),
  });
}

// Combined Session（Prompt1+Prompt2を1セッションで通しで実行、2026-09-16追加）。
// prompt1_output.zip/prompt2_output.zipを続けて取り込み、バックエンドが両方を
// 自動でCanonical採用する（Use as Currentクリックは不要）——canonical_*_execution_id
// が変わるため、geoReportRuns（run一覧、canonical列を含む）も無効化する。
export interface GeoReportCombinedImportResult {
  prompt1_execution_id: string;
  prompt2_execution_id: string;
  section_coverage_warnings: Array<{ section_id: string; field: string; reason: string }> | null;
}

export function useImportCombinedPackage(projectId: string, runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { prompt1File: File; prompt2File: File }) => {
      const form = new FormData();
      form.append("prompt1_file", args.prompt1File);
      form.append("prompt2_file", args.prompt2File);
      return postForm<GeoReportCombinedImportResult>(
        `/api/geo/report-studio/projects/${projectId}/runs/${runId}/combined/import`,
        form,
      );
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoReportRunExecutions", projectId, runId] });
      qc.invalidateQueries({ queryKey: ["geoReportFinalReport", projectId, runId] });
      qc.invalidateQueries({ queryKey: ["geoReportRuns", projectId] });
    },
  });
}
