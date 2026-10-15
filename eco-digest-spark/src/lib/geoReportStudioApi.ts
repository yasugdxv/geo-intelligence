// Report Studio（Prompt0/1/2内製化）のAPIクライアント。
// 既存 lib/api.ts とは意図的に分離する（geoKnowledgeApi.ts等と同じ理由: 新規エンドポイントが
// 多く、既存ファイルが既に1000行を超えているため。ホスト設定(API_BASE)のみ重複させる）。
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

async function postForm<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { method: "POST", body: form });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `API error ${res.status}`);
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// 型
// ---------------------------------------------------------------------------

export interface GeoReportProject {
  project_id: string; project_code: string; project_title: string;
  report_language: string; status: string; created_at: string; updated_at: string;
}

export interface GeoReportCollectionRun {
  run_id: string; project_id: string; status: string;
  run_name?: string | null; cancel_requested?: boolean;
  period_start: string | null; period_end: string | null;
  input_file_summary: Array<{ source_file: string; detected_signal: string; status: string; rows: number; error: string }>;
  preprocess_audit: Array<{ signal_id: string; total_records: number; nonduplicate_records: number;
    matched_records: number; fallback_records: number; fallback_share: number; qa_status: string }>;
  population_summary: Record<string, unknown>;
  total_records: number; in_scope_records: number; nonduplicate_records: number; cluster_count: number;
  started_at: string; completed_at: string | null;
  // Design Pin（design-pinned Runのみ設定される。旧Collectionの未Pin Runはnull）
  design_version_id?: string | null;
  narrative_master_asset_id?: string | null;
  report_template_asset_id?: string | null;
  baseline_run_id?: string | null;
  previous_continuity_artifact_id?: string | null;
  // Canonical Execution（画面の進捗はここから導出する。UI独自の状態を作らない）
  canonical_preprocess_execution_id?: string | null;
  canonical_prompt1_execution_id?: string | null;
  canonical_prompt2_execution_id?: string | null;
  canonical_report_execution_id?: string | null;
  // Prompt Contract Version（2026-09-16追加、Phase H）。evidence_intelligence_v1の
  // RunではApplication AI経路のUIを無効化するために使う（geo-report-pipeline-panel.tsx参照）。
  prompt1_contract_version?: string | null;
  prompt2_contract_version?: string | null;
}

export interface GeoReportProjectSummary {
  project: GeoReportProject;
  latest_run: GeoReportCollectionRun | null;
  review_required_count: number;
}

export interface GeoReportClusterClassification {
  result: {
    dimensions: Record<string, { value: unknown; apply_to_cluster: string; confidence: string; rationale: string;
      evidence_record_ids: string[] }>;
    record_level_exceptions: Array<{ dimension_id: string; record_id: string; value: string; rationale: string }>;
    japanese_translations: Array<{ record_id: string; japanese_translation: string }>;
    cluster_notes: string;
  };
  validity_checks: Record<string, { passed: boolean; detail: string }>;
  validity_status: "PASS" | "REVIEW REQUIRED";
}

export interface GeoReportCluster {
  id: string; run_id: string; cluster_id: string; signal_id: string; narrative_id: string;
  time_bucket: string | null; source_bucket: string | null; split_label: string | null;
  record_count: number; matched_share: number | null; fallback_share: number | null;
  report_priority: string | null; report_section: string | null; continuity_role: string | null;
  preliminary_consistency: string | null; requires_review: boolean;
  classification: GeoReportClusterClassification | null; classified_at: string | null;
}

export interface GeoReportRepresentative {
  id: string; run_id: string; cluster_id: string; representative_no: number; record_id: string;
  title: string | null; text_for_review: string | null; source_name: string | null; source_url: string | null;
  published_at: string | null; language: string | null; source_country: string | null;
  author_or_account: string | null; document_type: string | null; source_bucket: string | null;
}

export interface GeoReportSynthesisProvenance {
  evidence_basis: "observed" | "expert_context" | "mixed";
  observed_record_ids: string[]; observed_cluster_ids: string[]; expert_context_refs: string[];
}
export interface GeoReportSynthesisItem {
  provenance: GeoReportSynthesisProvenance; confidence: string; [key: string]: unknown;
}
export type GeoReportChangeType =
  "Intensified" | "Weakened" | "Reframed" | "Stable" | "New" | "Resolved" | "Insufficient_evidence";
export type GeoReportWatchpointStatus = "Triggered" | "Possible_trigger" | "Not_observed" | "Evidence_insufficient";

export interface GeoReportSynthesisDraft {
  central_assessment: GeoReportSynthesisItem & { summary: string };
  findings: Array<GeoReportSynthesisItem & { finding_id: string; statement: string; why_it_matters: string }>;
  drivers: Array<GeoReportSynthesisItem & { driver: string; mechanism: string }>;
  counterforces: Array<GeoReportSynthesisItem & { counterforce: string; mechanism: string }>;
  implications: Array<GeoReportSynthesisItem & { implication: string; for_whom: string }>;
  watchpoints: Array<GeoReportSynthesisItem & { watchpoint: string; trigger_condition: string }>;
  continuity_assessment: { state: string; rationale: string };
  assessment_delta: Array<GeoReportSynthesisItem & { item: string; change_type: GeoReportChangeType }>;
  watchpoint_updates: Array<GeoReportSynthesisItem & { watchpoint: string; status: GeoReportWatchpointStatus }>;
}
export interface GeoReportSynthesis {
  id: string; run_id: string; signal_id: string; narrative_id: string;
  draft: GeoReportSynthesisDraft;
  draft_validity_checks: Record<string, { passed: boolean; detail: string }>;
  draft_validity_status: string;
  review: { review_verdict: string; issues_found: Array<{ axis: string; target: string; detail: string }>;
    revised_draft: GeoReportSynthesisDraft | null } | null;
  final_draft: GeoReportSynthesisDraft;
  final_validity_status: string;
  final_html: string | null;
  observed_record_ids: string[]; observed_cluster_ids: string[]; expert_context_ref_ids: string[];
  created_at: string;
}

// ---------------------------------------------------------------------------
// Projects
// ---------------------------------------------------------------------------

export function useGeoReportProjects() {
  return useQuery({
    queryKey: ["geoReportProjects"],
    queryFn: () => fetchJson<{ projects: GeoReportProjectSummary[] }>("/api/geo/report-studio/projects"),
  });
}

export function useGeoReportProject(projectId: string) {
  return useQuery({
    queryKey: ["geoReportProject", projectId],
    queryFn: () => fetchJson<GeoReportProjectSummary>(`/api/geo/report-studio/projects/${projectId}`),
    enabled: !!projectId,
  });
}

export interface GeoReportOverview {
  project: GeoReportProject; latest_run: GeoReportCollectionRun | null;
  narrative_count: number; review_required_count: number; syntheses: GeoReportSynthesis[];
}

export function useGeoReportOverview(projectId: string) {
  return useQuery({
    queryKey: ["geoReportOverview", projectId],
    queryFn: () => fetchJson<GeoReportOverview>(`/api/geo/report-studio/projects/${projectId}/overview`),
    enabled: !!projectId,
  });
}

export function useGeoReportAssessmentTimeline(projectId: string) {
  return useQuery({
    queryKey: ["geoReportAssessmentTimeline", projectId],
    queryFn: () => fetchJson<{ syntheses: GeoReportSynthesis[] }>(
      `/api/geo/report-studio/projects/${projectId}/assessment-timeline`),
    enabled: !!projectId,
  });
}

export function useCreateGeoReportProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { project_code: string; project_title: string; report_language?: string }) =>
      postJson<{ project: GeoReportProject }>("/api/geo/report-studio/projects", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["geoReportProjects"] }),
  });
}

// 2026-09-15追加、ユーザー要望:「過去のプロジェクトは削除できるようにしよう」。
// 関連するRun/Design/Asset/Executionは全てDB側のon delete cascadeで連鎖削除される
// ため、フロントは確認ダイアログを経由してこのAPIを呼ぶだけでよい。
export function useDeleteGeoReportProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (projectId: string) =>
      fetchJson<{ deleted: boolean }>(`/api/geo/report-studio/projects/${projectId}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["geoReportProjects"] }),
  });
}

// ---------------------------------------------------------------------------
// Narrative Master
// ---------------------------------------------------------------------------

export function useGeoReportNarrativeMaster(projectId: string) {
  return useQuery({
    queryKey: ["geoReportNarrativeMaster", projectId],
    queryFn: () => fetchJson<{ rows: Record<string, string>[] }>(
      `/api/geo/report-studio/projects/${projectId}/narrative-master`),
    enabled: !!projectId,
  });
}

export function useUploadNarrativeMaster(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return postForm<{ rows_imported: number }>(
        `/api/geo/report-studio/projects/${projectId}/narrative-master`, form);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["geoReportNarrativeMaster", projectId] }),
  });
}

// ---------------------------------------------------------------------------
// Collection Runs
// ---------------------------------------------------------------------------

export function useGeoReportRuns(projectId: string) {
  return useQuery({
    queryKey: ["geoReportRuns", projectId],
    queryFn: () => fetchJson<{ runs: GeoReportCollectionRun[] }>(
      `/api/geo/report-studio/projects/${projectId}/runs`),
    enabled: !!projectId,
  });
}

export function useCreateGeoReportRun(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { files: File[]; startDate?: string; endDate?: string }) => {
      const form = new FormData();
      args.files.forEach((f) => form.append("files", f));
      if (args.startDate) form.append("start_date", args.startDate);
      if (args.endDate) form.append("end_date", args.endDate);
      return postForm<{ run_id: string }>(`/api/geo/report-studio/projects/${projectId}/runs`, form);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoReportRuns", projectId] });
      qc.invalidateQueries({ queryKey: ["geoReportProjects"] });
      qc.invalidateQueries({ queryKey: ["geoReportOverview", projectId] });
    },
  });
}

// ---------------------------------------------------------------------------
// Clusters / Classification
// ---------------------------------------------------------------------------

export function useGeoReportClusters(runId: string, narrativeId?: string) {
  const usp = new URLSearchParams();
  if (narrativeId) usp.set("narrative_id", narrativeId);
  return useQuery({
    queryKey: ["geoReportClusters", runId, narrativeId ?? ""],
    queryFn: () => fetchJson<{ clusters: GeoReportCluster[] }>(
      `/api/geo/report-studio/runs/${runId}/clusters?${usp.toString()}`),
    enabled: !!runId,
  });
}

export function useGeoReportAllRepresentatives(runId: string) {
  return useQuery({
    queryKey: ["geoReportAllRepresentatives", runId],
    queryFn: () => fetchJson<{ representatives: GeoReportRepresentative[] }>(
      `/api/geo/report-studio/runs/${runId}/representatives`),
    enabled: !!runId,
  });
}

export function useGeoReportClusterDetail(runId: string, clusterId: string) {
  return useQuery({
    queryKey: ["geoReportClusterDetail", runId, clusterId],
    queryFn: () => fetchJson<{ cluster: GeoReportCluster; representatives: GeoReportRepresentative[] }>(
      `/api/geo/report-studio/runs/${runId}/clusters/${clusterId}`),
    enabled: !!runId && !!clusterId,
  });
}

export function useClassifyCluster(runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (clusterId: string) =>
      postJson(`/api/geo/report-studio/runs/${runId}/clusters/${clusterId}/classify`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["geoReportClusters", runId] }),
  });
}

export function useClassifyNext(runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { narrativeId?: string; maxClusters?: number }) =>
      postJson<{ classified: Array<{ cluster_id: string; validity_status: string }>; remaining_unclassified: number }>(
        `/api/geo/report-studio/runs/${runId}/classify-next`,
        { narrative_id: args.narrativeId || null, max_clusters: args.maxClusters ?? 5 }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["geoReportClusters", runId] }),
  });
}

export function useGeoReportCoverage(runId: string) {
  return useQuery({
    queryKey: ["geoReportCoverage", runId],
    queryFn: () => fetchJson<Record<string, unknown>>(`/api/geo/report-studio/runs/${runId}/coverage`),
    enabled: !!runId,
  });
}

// ---------------------------------------------------------------------------
// Synthesis
// ---------------------------------------------------------------------------

export function useGeoReportSyntheses(runId: string) {
  return useQuery({
    queryKey: ["geoReportSyntheses", runId],
    queryFn: () => fetchJson<{ syntheses: GeoReportSynthesis[] }>(`/api/geo/report-studio/runs/${runId}/syntheses`),
    enabled: !!runId,
  });
}

export function useSynthesizeNarrative(runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { signalId: string; narrativeId: string }) =>
      postJson<{ synthesis_id: string; final_html: string }>(
        `/api/geo/report-studio/runs/${runId}/narratives/${args.signalId}/${args.narrativeId}/synthesize`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["geoReportSyntheses", runId] }),
  });
}
