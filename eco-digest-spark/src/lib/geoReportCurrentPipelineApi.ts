// Report Studio「Design」領域: Current Pipeline実行（geo_report_current_pipeline_api.py、
// Phase D: Preprocess＋Prompt1 Application AI）のAPIクライアント。
// geoReportDesignApi.tsとは別ファイルにする（既存の分割方針を踏襲）。
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

export type GeoReportExecutionStage =
  "preprocess" | "prompt1" | "aggregation" | "prompt2" | "report_edit";

export type GeoReportExecutionMethod = "application_ai" | "chatgpt_import" | "python";

export type GeoReportExecutionStatus = "pending" | "running" | "completed" | "failed" | "stale";

export interface GeoReportExecution {
  id: string;
  run_id: string;
  stage: GeoReportExecutionStage;
  execution_method: GeoReportExecutionMethod;
  status: GeoReportExecutionStatus;
  prompt_contract_version: string | null;
  input_collection_artifact_id: string | null;
  input_preprocess_execution_id: string | null;
  input_prompt1_execution_id: string | null;
  input_aggregation_execution_id: string | null;
  input_prompt2_base_input_artifact_id: string | null;
  input_continuity_artifact_id: string | null;
  parent_execution_id: string | null;
  progress: Record<string, unknown> | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export type Prompt1BatchStage =
  "prompt1_analysis_cluster" | "prompt1_sentiment_cluster" | "prompt1_targeted_stance";

export type GeoReportExecutionBatchStatus =
  "pending" | "running" | "completed" | "failed" | "skipped";

export interface GeoReportExecutionBatch {
  id: string;
  run_id: string;
  execution_id: string;
  stage: string;
  batch_no: number;
  item_ids: string[];
  status: GeoReportExecutionBatchStatus;
  model_used: string | null;
  escalated: boolean;
  retry_count: number;
  error_message: string | null;
  result_artifact_ref: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface RunPreprocessResult {
  execution_id: string;
  total_records: number;
  nonduplicate_records: number;
  analysis_cluster_count: number;
  sentiment_cluster_count: number;
  artifact_count: number;
}

export interface PlanPrompt1Result {
  execution_id: string;
  batch_counts: Record<Prompt1BatchStage, number>;
}

export interface RunFullPipelineResult {
  preprocess_execution_id: string;
  prompt1_execution_id: string;
  aggregation_execution_id: string;
  prompt2_execution_id: string;
  total_records: number;
  nonduplicate_records: number;
}

export interface RerunPrompt1ThroughPrompt2Result {
  prompt1_execution_id: string;
  aggregation_execution_id: string;
  prompt2_execution_id: string;
}

export interface RunAggregationThroughPrompt2Result {
  aggregation_execution_id: string;
  prompt2_execution_id: string;
}

export type GeoReportCanonicalStage = "prompt1" | "prompt2";

export interface SetCanonicalExecutionResult {
  run_id: string;
  stage: GeoReportCanonicalStage;
  canonical_execution_id: string;
  staled_execution_ids: string[];
}

// ---------------------------------------------------------------------------
// Preprocess
// ---------------------------------------------------------------------------

// 2026-09-16変更（Report Studio UI全面見直し）: runIdをhook生成時に固定する形から
// mutate呼び出し時に渡す形へ変更した。CreateRun直後にそのRunへ直接Preprocessを
// 呼ぶ場面で、Reactの状態更新（onSelectRun等）の反映を待たずmutateAsyncの戻り値を
// そのターン内のローカル変数として直接使えるようにするため（runIdをhookの外から
// 都度渡せないと、作成直後のrunIdをこの呼び出しへ渡す手段が無かった）。
export function useRunPreprocess(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      runId,
      files,
      resumeExecutionId,
    }: {
      runId: string;
      // 2026-09-17変更、ユーザー指摘「途中まで実行してたのを再開するのに新しく
      // CSVをアップロードするの？別のファイルだとデータが混ざるリスクがあるよね」:
      // resumeExecutionId付きの再開では、サーバー側が最初のアップロード時に
      // キャッシュした元のCSVを自動的に再利用するため、filesは不要（省略可）。
      files?: File[];
      // 2026-09-16追加、ユーザー要望「途中で保存して失敗したらそこから再開できないの」:
      // 以前失敗したPreprocess Executionのidを渡すと、バックエンドがそのExecutionを
      // 再利用し、既に完了済みのフェーズ・chunkをスキップして続きから再開する。
      resumeExecutionId?: string;
    }) => {
      const form = new FormData();
      (files ?? []).forEach((f) => form.append("files", f));
      if (resumeExecutionId) form.append("resume_execution_id", resumeExecutionId);
      return postForm<RunPreprocessResult>(
        `/api/geo/report-studio/projects/${projectId}/runs/${runId}/preprocess`,
        form,
      );
    },
    onSuccess: (_data, { runId }) => {
      qc.invalidateQueries({ queryKey: ["geoReportRunExecutions", projectId, runId] });
      // 2026-09-17追加、ユーザー報告「処理は終わったみたいだけど、ダウンロードリンクが
      // でてこない」: run.canonical_preprocess_execution_id（Run行側の列、ChatGPT用ZIP
      // セクション表示の判定に使う`preprocessDone`の根拠）が、Preprocess成功時にDB側では
      // 更新されるのにこのRun一覧クエリのキャッシュは無効化されていなかったため、画面上は
      // 古いまま（未完了扱い）に見えていた。useSetCanonicalExecutionで既に対応済みの
      // 同種の不具合と同じ原因。
      qc.invalidateQueries({ queryKey: ["geoReportRuns", projectId] });
    },
  });
}

// ---------------------------------------------------------------------------
// 統合実行（Preprocess→Prompt1→Aggregation→Prompt2→Final Reportを1回で通しで実行。
// ユーザー要望2026-09-11: 「プロンプト1,2はプロセスとして分けなくていい」）
// ---------------------------------------------------------------------------

export function useRunFullPipeline(projectId: string, runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (files: File[]) => {
      const form = new FormData();
      files.forEach((f) => form.append("files", f));
      return postForm<RunFullPipelineResult>(
        `/api/geo/report-studio/projects/${projectId}/runs/${runId}/run-full-pipeline`,
        form,
      );
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoReportRunExecutions", projectId, runId] });
      qc.invalidateQueries({ queryKey: ["geoReportFinalReport", projectId, runId] });
    },
  });
}

/** CSVを再アップロードせず、既存Preprocess結果からPrompt1〜Prompt2だけをやり直す。 */
export function useRerunPrompt1ThroughPrompt2(projectId: string, runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (reportTheme?: string) =>
      postJson<RerunPrompt1ThroughPrompt2Result>(
        `/api/geo/report-studio/projects/${projectId}/runs/${runId}/rerun-prompt1-through-prompt2`,
        { report_theme: reportTheme ?? "" },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoReportRunExecutions", projectId, runId] });
      qc.invalidateQueries({ queryKey: ["geoReportFinalReport", projectId, runId] });
    },
  });
}

/**
 * 既存のCanonical Prompt1結果（Application AIまたはChatGPT Import）を使って、
 * Aggregation→Prompt2だけを実行する。大量データでPrompt1のみChatGPTへ退避し
 * （Export→ChatGPT実行→Import→Use as Current）、以降をこのアプリの
 * Application AIで続ける運用の入口（2026-09-14）。
 */
export function useRunAggregationThroughPrompt2(projectId: string, runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (reportTheme?: string) =>
      postJson<RunAggregationThroughPrompt2Result>(
        `/api/geo/report-studio/projects/${projectId}/runs/${runId}/run-aggregation-through-prompt2`,
        { report_theme: reportTheme ?? "" },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoReportRunExecutions", projectId, runId] });
      qc.invalidateQueries({ queryKey: ["geoReportFinalReport", projectId, runId] });
    },
  });
}

// ---------------------------------------------------------------------------
// Execution一覧・進捗ポーリング（全stage共通）
// ---------------------------------------------------------------------------

/**
 * 一覧内のいずれかのExecutionがrunning/pendingの間は自動でポーリングする
 * （Preprocess→Prompt1→Prompt2の統合実行は1回の長時間ブロッキングHTTPリクエストで
 * 進むため、この一覧クエリ自身がポーリングしない限りステータス表示が固まって見える。
 * `forcePoll`は、統合実行の開始直後などまだExecution行が1つも無い一瞬の間も
 * ポーリングを続けたい場合に呼び出し側から明示的に渡す）。
 */
export function useGeoReportRunExecutions(
  projectId: string,
  runId: string,
  options?: { forcePoll?: boolean; refetchIntervalMs?: number },
) {
  const refetchIntervalMs = options?.refetchIntervalMs ?? 3000;
  return useQuery({
    queryKey: ["geoReportRunExecutions", projectId, runId],
    queryFn: () =>
      fetchJson<{ executions: GeoReportExecution[] }>(
        `/api/geo/report-studio/projects/${projectId}/runs/${runId}/executions`,
      ),
    enabled: !!projectId && !!runId,
    refetchInterval: (query) => {
      if (options?.forcePoll) return refetchIntervalMs;
      const executions = query.state.data?.executions ?? [];
      const hasActive = executions.some((e) => e.status === "running" || e.status === "pending");
      return hasActive ? refetchIntervalMs : false;
    },
  });
}

/** 進捗ポーリング用。実行中(status='running')の間だけ`refetchIntervalMs`で自動再取得する。 */
export function useGeoReportExecutionStatus(
  executionId: string | null | undefined,
  refetchIntervalMs = 3000,
) {
  return useQuery({
    queryKey: ["geoReportExecutionStatus", executionId],
    queryFn: () =>
      fetchJson<{ execution: GeoReportExecution }>(
        `/api/geo/report-studio/executions/${executionId}`,
      ),
    enabled: !!executionId,
    refetchInterval: (query) =>
      query.state.data?.execution.status === "running" ? refetchIntervalMs : false,
  });
}

// ---------------------------------------------------------------------------
// Prompt1 Application AI（Analysis/Sentiment Cluster分類・Targeted Stance）
// ---------------------------------------------------------------------------

export function usePlanPrompt1Execution(projectId: string, runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      postJson<PlanPrompt1Result>(
        `/api/geo/report-studio/projects/${projectId}/runs/${runId}/prompt1/plan`,
        {},
      ),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["geoReportRunExecutions", projectId, runId] }),
  });
}

export function usePrompt1Batches(
  projectId: string,
  runId: string,
  executionId: string | null,
  stage?: Prompt1BatchStage,
) {
  return useQuery({
    queryKey: ["geoReportPrompt1Batches", projectId, runId, executionId, stage],
    queryFn: () => {
      const params = new URLSearchParams({ execution_id: executionId as string });
      if (stage) params.set("stage", stage);
      return fetchJson<{ batches: GeoReportExecutionBatch[] }>(
        `/api/geo/report-studio/projects/${projectId}/runs/${runId}/prompt1/batches?${params}`,
      );
    },
    enabled: !!projectId && !!runId && !!executionId,
  });
}

export function useRunPrompt1Batch(projectId: string, runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { executionId: string; stage: Prompt1BatchStage }) =>
      postJson<Record<string, unknown>>(
        `/api/geo/report-studio/projects/${projectId}/runs/${runId}/prompt1/run-batch`,
        { execution_id: args.executionId, stage: args.stage },
      ),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({
        queryKey: ["geoReportPrompt1Batches", projectId, runId, variables.executionId],
      });
      qc.invalidateQueries({ queryKey: ["geoReportExecutionStatus", variables.executionId] });
    },
  });
}

export function useRunAllPrompt1Batches(projectId: string, runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { executionId: string; stage: Prompt1BatchStage; maxBatches?: number }) =>
      postJson<{ results: Array<Record<string, unknown>> }>(
        `/api/geo/report-studio/projects/${projectId}/runs/${runId}/prompt1/run-all`,
        { execution_id: args.executionId, stage: args.stage, max_batches: args.maxBatches ?? null },
      ),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({
        queryKey: ["geoReportPrompt1Batches", projectId, runId, variables.executionId],
      });
      qc.invalidateQueries({ queryKey: ["geoReportExecutionStatus", variables.executionId] });
    },
  });
}

export function useRetryPrompt1Batch(projectId: string, runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { batchId: string; executionId: string; stage: Prompt1BatchStage }) =>
      postJson<Record<string, unknown>>(
        `/api/geo/report-studio/projects/${projectId}/runs/${runId}/prompt1/batches/${args.batchId}/retry`,
        { execution_id: args.executionId, stage: args.stage },
      ),
    onSuccess: (_data, variables) =>
      qc.invalidateQueries({
        queryKey: ["geoReportPrompt1Batches", projectId, runId, variables.executionId],
      }),
  });
}

export function useCompletePrompt1Execution(projectId: string, runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { executionId: string; stage: Prompt1BatchStage }) =>
      postJson<{ completed: boolean }>(
        `/api/geo/report-studio/projects/${projectId}/runs/${runId}/prompt1/complete`,
        { execution_id: args.executionId, stage: args.stage },
      ),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["geoReportRunExecutions", projectId, runId] }),
  });
}

// ---------------------------------------------------------------------------
// Canonical Execution切り替え（Application AI/ChatGPT Importどちらの結果を
// 「正式採用」にするかの唯一の入口。stage非依存で全stage共通のため、ここに置く）
// ---------------------------------------------------------------------------

export function useSetCanonicalExecution(projectId: string, runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { stage: GeoReportCanonicalStage; executionId: string }) =>
      postJson<SetCanonicalExecutionResult>(
        `/api/geo/report-studio/projects/${projectId}/runs/${runId}/canonical`,
        { stage: args.stage, execution_id: args.executionId },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoReportRunExecutions", projectId, runId] });
      qc.invalidateQueries({ queryKey: ["geoReportFinalReport", projectId, runId] });
      // 2026-09-16修正: canonical_*_execution_idはRun行（geoReportRuns）側の列だが、
      // ここが無効化されていなかったため、バックエンドは正しく更新されてもUI上は
      // 「現在採用中」バッジ・ボタン非表示が反映されず、ボタンが押せない
      // （何も変化しない）ように見えていた。
      qc.invalidateQueries({ queryKey: ["geoReportRuns", projectId] });
    },
  });
}
