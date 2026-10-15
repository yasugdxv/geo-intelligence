// Report Studio「Design」領域（geo_report_design_api.py）のAPIクライアント。
// geoReportStudioApi.tsとは別ファイルにする（既存ファイルの分割方針を踏襲。Design領域は
// 独立したエンドポイント群のため）。バックエンドのrequest/response構造をそのまま型へ写し、
// フロント側でversion/asset_id等を独自に組み立てない
// （publish_design/create_run系はバックエンドのレスポンスをそのまま使う）。
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

function putJson<T>(path: string, body: unknown): Promise<T> {
  return fetchJson<T>(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function patchJson<T>(path: string, body: unknown): Promise<T> {
  return fetchJson<T>(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function deleteRequest<T>(path: string): Promise<T> {
  return fetchJson<T>(path, { method: "DELETE" });
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

export type GeoReportAssetType = "narrative_master" | "report_template";
export type GeoReportAssetStatus = "draft" | "published" | "superseded";

export interface GeoReportDesignState {
  project_id: string;
  report_design: Record<string, unknown>;
  signals: Array<Record<string, unknown>>;
  queries: Array<Record<string, unknown>>;
  updated_at?: string;
  updated_by?: string | null;
  // 2026-09-15追加: Design Chatの「提案→FIX」ワークフロー。非nullの間は
  // チャットで提案済みだがまだFIXされていない変更があることを示す。
  pending_report_design?: Record<string, unknown> | null;
  pending_signals?: Array<Record<string, unknown>> | null;
  pending_queries?: Array<Record<string, unknown>> | null;
}

// 2026-09-16追加: FIX確定後にダウンロードできるアセットの相対パスURL一覧
// （キーはreport_design/signals/queriesは常に、narrative_master/report_templateは
// 実際に存在する場合のみ含まれる）。
export type GeoReportFixDesignDownloads = Partial<
  Record<"report_design" | "signals" | "queries" | "narrative_master" | "report_template", string>
>;

export interface GeoReportFixDesignResult {
  design_state: GeoReportDesignState;
  narrative_master_generation: GeoReportNarrativeMasterGenerationResult | null;
  downloads: GeoReportFixDesignDownloads;
  // 2026-09-16追加: primary_graph_signals/common_primary_graph_start等が未設定の
  // 場合の早期警告（空配列なら問題なし）。
  report_config_warnings: string[];
}

export interface GeoReportProjectAsset {
  id: string;
  project_id: string;
  asset_type: GeoReportAssetType;
  status: GeoReportAssetStatus;
  version: number | null;
  supersedes_id: string | null;
  content: Record<string, unknown>;
  content_format: string;
  source: string;
  row_count: number | null;
  published_at: string | null;
  published_by: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export type NarrativeMasterRow = Record<string, string>;

export interface NarrativeMasterFieldDiff {
  old: string;
  new: string;
}

export interface NarrativeMasterDiff {
  added: Array<{ signal_id: string; narrative_id: string }>;
  removed: Array<{ signal_id: string; narrative_id: string }>;
  changed: Array<{
    signal_id: string;
    narrative_id: string;
    fields: Record<string, NarrativeMasterFieldDiff>;
  }>;
  unchanged_count: number;
}

export type ReportTemplateComponentType =
  | "narrative_block"
  | "metric_grid"
  | "stacked_bar_chart"
  | "scenario_cards"
  | "comparison_table"
  | "example_quotes"
  | "data_limits_note";

export interface ReportTemplateSection {
  section_id: string;
  title: string;
  order: number;
  component_type: ReportTemplateComponentType;
  required_fields: string[];
  component_config?: Record<string, unknown>;
  example_markup?: string;
}

export interface ReportTemplateContent {
  style_css: string;
  shell_html: string;
  sections: ReportTemplateSection[];
}

export interface GeoReportDesignVersion {
  id: string;
  project_id: string;
  version: number;
  report_design: Record<string, unknown>;
  signals: Array<Record<string, unknown>>;
  queries: Array<Record<string, unknown>>;
  narrative_master_asset_id: string;
  report_template_asset_id: string;
  expert_adapter_type: string;
  expert_knowledge_version: string;
  published_at: string;
  published_by: string;
}

export interface GeoReportDesignChatTurn {
  id: string;
  project_id: string;
  turn_no: number;
  role: "user" | "assistant";
  content: string;
  applied_diff: Record<string, unknown> | null;
  llm_usage: Record<string, unknown> | null;
  latency_ms: number | null;
  created_at: string;
}

export interface GeoReportNarrativeMasterGenerationResult {
  signals_generated: string[];
  row_count: number;
  errors: string[];
}

export interface GeoReportTemplateChatGenerationResult {
  asset_id: string;
  section_count: number;
}

export interface GeoReportDesignChatResult {
  assistant_reply: string;
  clarifying_questions: string[];
  mentioned_entity_names: string[];
  design_state: GeoReportDesignState;
  applied_diff: Record<string, unknown>;
  narrative_master_generation: GeoReportNarrativeMasterGenerationResult | null;
  report_template_generation: GeoReportTemplateChatGenerationResult | null;
}

// 2026-09-16追加: チャットへのファイル添付（.csv → Narrative Master、
// .html/.htm → Report Template Extraction）の結果。
export interface GeoReportDesignChatUploadResult {
  kind: "narrative_master" | "report_template" | "unsupported";
  success: boolean;
  message?: string;
  assistant_reply?: string;
  row_count?: number;
  signal_ids?: string[];
  section_count?: number;
  asset_id?: string;
  leakage_scan?: { passed: boolean; leaked_items: string[] };
}

export interface GeoReportTemplateExtractionResult {
  template: ReportTemplateContent;
  leakage_scan: { passed: boolean; leaked_items: string[] };
}

export interface GeoReportPinnedRun {
  run_id: string;
  project_id: string;
  design_version_id: string | null;
  narrative_master_asset_id: string | null;
  report_template_asset_id: string | null;
  prompt1_contract_version: string | null;
  prompt2_contract_version: string | null;
  prompt1_instruction_fingerprint: string | null;
  prompt2_instruction_fingerprint: string | null;
  preprocess_contract_version: string | null;
  aggregation_contract_version: string | null;
  renderer_version: string | null;
  application_build_revision: string | null;
  baseline_run_id: string | null;
  baseline_prompt2_execution_id: string | null;
  previous_continuity_artifact_id: string | null;
  run_mode: "full" | "incremental";
  status: string;
  // Run作成直後のレスポンスにのみ含まれる（DBへは保存されない）。Design側で
  // primary_graph_signals/common_primary_graph_start/final_graph_ruleが
  // 未設定・不正なままRunを作った場合の警告——放置するとAggregation実行時に
  // 初めて失敗が判明する（Phase 5 E2E検証で発覚、UIが警告を表示していなかった）。
  prompt2_config_warnings?: string[];
}

// ---------------------------------------------------------------------------
// Design State
// ---------------------------------------------------------------------------

export function useGeoReportDesignState(projectId: string) {
  return useQuery({
    queryKey: ["geoReportDesignState", projectId],
    queryFn: () =>
      fetchJson<{ design_state: GeoReportDesignState }>(
        `/api/geo/report-studio/projects/${projectId}/design/state`,
      ),
    enabled: !!projectId,
  });
}

export function useUpdateGeoReportDesignState(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: {
      report_design?: Record<string, unknown>;
      signals?: Array<Record<string, unknown>>;
      queries?: Array<Record<string, unknown>>;
      updated_by?: string;
    }) =>
      putJson<{ design_state: GeoReportDesignState }>(
        `/api/geo/report-studio/projects/${projectId}/design/state`,
        args,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["geoReportDesignState", projectId] }),
  });
}

// Design StateのqueriesをそのままCurrent契約のbs_query_plan.csvへserializeして返す
// URL（変換ロジック無し、Babel Street投入用）。<a download>で使う想定。
export function geoReportQueryPlanExportUrl(projectId: string): string {
  return `${API_BASE}/api/geo/report-studio/projects/${projectId}/design/queries/export`;
}

// 2026-09-16追加（Report Studio UI全面見直し）: 確定済み（FIX済み）のReport Design/
// Signalをダウンロードする、ステートレスなURL。FIX直後のmutation結果（一時state、
// リロードで消える）ではなく、常にこのURLを使うことで、リロード後もダウンロード
// リンクを再現できる（hasFixedDesignがtrueの間はいつでも有効）。
export function geoReportReportDesignDownloadUrl(projectId: string): string {
  return `${API_BASE}/api/geo/report-studio/projects/${projectId}/design/report-design/download`;
}
export function geoReportSignalsDownloadUrl(projectId: string): string {
  return `${API_BASE}/api/geo/report-studio/projects/${projectId}/design/signals/download`;
}

// ---------------------------------------------------------------------------
// Project Assets（Narrative Master / Report Template 共通）
// ---------------------------------------------------------------------------

export function useGeoReportAssetDraft(projectId: string, assetType: GeoReportAssetType) {
  return useQuery({
    queryKey: ["geoReportAssetDraft", projectId, assetType],
    queryFn: () =>
      fetchJson<{ asset: GeoReportProjectAsset }>(
        `/api/geo/report-studio/projects/${projectId}/assets/${assetType}/draft`,
      ),
    enabled: !!projectId,
    retry: false,
  });
}

export function useGeoReportAssetPublished(projectId: string, assetType: GeoReportAssetType) {
  return useQuery({
    queryKey: ["geoReportAssetPublished", projectId, assetType],
    queryFn: () =>
      fetchJson<{ asset: GeoReportProjectAsset }>(
        `/api/geo/report-studio/projects/${projectId}/assets/${assetType}/published`,
      ),
    enabled: !!projectId,
    retry: false,
  });
}

export function useGeoReportAssetHistory(projectId: string, assetType: GeoReportAssetType) {
  return useQuery({
    queryKey: ["geoReportAssetHistory", projectId, assetType],
    queryFn: () =>
      fetchJson<{ history: GeoReportProjectAsset[] }>(
        `/api/geo/report-studio/projects/${projectId}/assets/${assetType}/history`,
      ),
    enabled: !!projectId,
  });
}

// ---------------------------------------------------------------------------
// Narrative Master: validate / upload / download / diff
// ---------------------------------------------------------------------------

export function useValidateNarrativeMaster(projectId: string) {
  return useMutation({
    mutationFn: (rows: NarrativeMasterRow[]) =>
      postJson<{ valid: boolean; errors: string[] }>(
        `/api/geo/report-studio/projects/${projectId}/assets/narrative-master/validate`,
        { rows },
      ),
  });
}

export function useUploadNarrativeMasterAsset(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return postForm<{ asset: GeoReportProjectAsset }>(
        `/api/geo/report-studio/projects/${projectId}/assets/narrative-master/upload`,
        form,
      );
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoReportAssetDraft", projectId, "narrative_master"] });
      qc.invalidateQueries({ queryKey: ["geoReportAssetHistory", projectId, "narrative_master"] });
    },
  });
}

export function useSaveNarrativeMasterAsset(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (rows: NarrativeMasterRow[]) =>
      postJson<{ asset: GeoReportProjectAsset }>(
        `/api/geo/report-studio/projects/${projectId}/assets/narrative-master/save`,
        { rows },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoReportAssetDraft", projectId, "narrative_master"] });
      qc.invalidateQueries({ queryKey: ["geoReportAssetHistory", projectId, "narrative_master"] });
      qc.invalidateQueries({ queryKey: ["geoReportNarrativeMasterDiff", projectId] });
    },
  });
}

export function geoReportNarrativeMasterDownloadUrl(
  projectId: string,
  status: "draft" | "published" = "draft",
): string {
  return `${API_BASE}/api/geo/report-studio/projects/${projectId}/assets/narrative-master/download?status=${status}`;
}

// バックエンドが返す相対パス（fix_designのdownloads等）から絶対URLを組み立てる。
export function buildApiUrl(relativePath: string): string {
  return `${API_BASE}${relativePath}`;
}

export function geoReportTemplatePreviewUrl(
  projectId: string,
  status: "draft" | "published" = "draft",
): string {
  return `${API_BASE}/api/geo/report-studio/projects/${projectId}/design/report-template/preview?status=${status}`;
}

export function useNarrativeMasterDiff(projectId: string) {
  return useQuery({
    queryKey: ["geoReportNarrativeMasterDiff", projectId],
    queryFn: () =>
      fetchJson<{ diff: NarrativeMasterDiff }>(
        `/api/geo/report-studio/projects/${projectId}/assets/narrative-master/diff`,
      ),
    enabled: !!projectId,
  });
}

// ---------------------------------------------------------------------------
// Report Template: validate / save
// ---------------------------------------------------------------------------

export function useValidateReportTemplate(projectId: string) {
  return useMutation({
    mutationFn: (content: ReportTemplateContent) =>
      postJson<{ valid: boolean; errors: string[] }>(
        `/api/geo/report-studio/projects/${projectId}/assets/report-template/validate`,
        { content },
      ),
  });
}

export function useSaveReportTemplateAsset(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (content: ReportTemplateContent) =>
      postJson<{ asset: GeoReportProjectAsset }>(
        `/api/geo/report-studio/projects/${projectId}/assets/report-template/save`,
        { content },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoReportAssetDraft", projectId, "report_template"] });
      qc.invalidateQueries({ queryKey: ["geoReportAssetHistory", projectId, "report_template"] });
    },
  });
}

// ---------------------------------------------------------------------------
// Design Publish
// ---------------------------------------------------------------------------

export function useGeoReportDesignVersions(projectId: string) {
  return useQuery({
    queryKey: ["geoReportDesignVersions", projectId],
    queryFn: () =>
      fetchJson<{ versions: GeoReportDesignVersion[] }>(
        `/api/geo/report-studio/projects/${projectId}/design/versions`,
      ),
    enabled: !!projectId,
  });
}

export interface GeoReportPublishDesignResult {
  design_version: GeoReportDesignVersion;
  // 2026-09-16追加: primary_graph_signals/common_primary_graph_start等が未設定の
  // 場合の早期警告（空配列なら問題なし）。
  report_config_warnings: string[];
}

export function usePublishGeoReportDesign(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (actorId?: string) =>
      postJson<GeoReportPublishDesignResult>(
        `/api/geo/report-studio/projects/${projectId}/design/publish`,
        { actor_id: actorId ?? "analyst" },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoReportDesignVersions", projectId] });
      qc.invalidateQueries({ queryKey: ["geoReportAssetDraft", projectId, "narrative_master"] });
      qc.invalidateQueries({
        queryKey: ["geoReportAssetPublished", projectId, "narrative_master"],
      });
      qc.invalidateQueries({ queryKey: ["geoReportAssetDraft", projectId, "report_template"] });
      qc.invalidateQueries({ queryKey: ["geoReportAssetPublished", projectId, "report_template"] });
      qc.invalidateQueries({ queryKey: ["geoReportNarrativeMasterDiff", projectId] });
    },
  });
}

// Design全体（Draft/Published/Narrative Master/Report Template/Chat履歴）を削除し
// 未設計状態へ戻す。Pin済みのRunが残っている場合はサーバー側が409＋対象Run名を返す
// （fetchJsonがdetailをError.messageへ格納するので、呼び出し側はisError時にそのまま表示する）。
export function useResetDesign(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      deleteRequest<{ reset: boolean }>(`/api/geo/report-studio/projects/${projectId}/design`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoReportDesignState", projectId] });
      qc.invalidateQueries({ queryKey: ["geoReportDesignVersions", projectId] });
      qc.invalidateQueries({ queryKey: ["geoReportDesignChatTurns", projectId] });
      qc.invalidateQueries({ queryKey: ["geoReportAssetDraft", projectId, "narrative_master"] });
      qc.invalidateQueries({
        queryKey: ["geoReportAssetPublished", projectId, "narrative_master"],
      });
      qc.invalidateQueries({ queryKey: ["geoReportAssetHistory", projectId, "narrative_master"] });
      qc.invalidateQueries({ queryKey: ["geoReportAssetDraft", projectId, "report_template"] });
      qc.invalidateQueries({ queryKey: ["geoReportAssetPublished", projectId, "report_template"] });
      qc.invalidateQueries({ queryKey: ["geoReportAssetHistory", projectId, "report_template"] });
      qc.invalidateQueries({ queryKey: ["geoReportNarrativeMasterDiff", projectId] });
    },
  });
}

// ---------------------------------------------------------------------------
// Create Run（暫定: Design/Prompt/Config/ContinuityのPinのみ。Phase D完了までの経路）
// ---------------------------------------------------------------------------

export function useCreateDesignPinnedRun(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (
      args: {
        baselineRunId?: string;
        periodStart?: string;
        periodEnd?: string;
        runName?: string;
      } = {},
    ) =>
      postJson<{ run: GeoReportPinnedRun }>(
        `/api/geo/report-studio/projects/${projectId}/runs/design-pinned`,
        {
          baseline_run_id: args.baselineRunId ?? null,
          period_start: args.periodStart ?? null,
          period_end: args.periodEnd ?? null,
          run_name: args.runName ?? null,
        },
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["geoReportRuns", projectId] }),
  });
}

// Run名の変更・削除・処理のキャンセル（2026-09-11、ユーザー報告「Run名がUUIDで判別
// 不能」「削除できない」「アボートできる方がいい」への対応）。
export function useRenameRun(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { runId: string; runName: string }) =>
      patchJson<{ run: GeoReportPinnedRun }>(
        `/api/geo/report-studio/projects/${projectId}/runs/${args.runId}`,
        { run_name: args.runName },
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["geoReportRuns", projectId] }),
  });
}

export function useDeleteRun(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) =>
      deleteRequest<{ deleted: boolean; run_id: string }>(
        `/api/geo/report-studio/projects/${projectId}/runs/${runId}`,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["geoReportRuns", projectId] }),
  });
}

export function useCancelRun(projectId: string, runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      postJson<{ cancel_requested: boolean }>(
        `/api/geo/report-studio/projects/${projectId}/runs/${runId}/cancel`,
        {},
      ),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["geoReportRunExecutions", projectId, runId] }),
  });
}

// ---------------------------------------------------------------------------
// Design Chat
// ---------------------------------------------------------------------------

export function useGeoReportDesignChatTurns(projectId: string) {
  return useQuery({
    queryKey: ["geoReportDesignChatTurns", projectId],
    queryFn: () =>
      fetchJson<{ turns: GeoReportDesignChatTurn[] }>(
        `/api/geo/report-studio/projects/${projectId}/design/chat`,
      ),
    enabled: !!projectId,
  });
}

export function useSendDesignChatMessage(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (message: string) =>
      postJson<GeoReportDesignChatResult>(
        `/api/geo/report-studio/projects/${projectId}/design/chat`,
        { message },
      ),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["geoReportDesignChatTurns", projectId] });
      qc.invalidateQueries({ queryKey: ["geoReportDesignState", projectId] });
      if (result.narrative_master_generation) {
        qc.invalidateQueries({ queryKey: ["geoReportAssetDraft", projectId, "narrative_master"] });
        qc.invalidateQueries({
          queryKey: ["geoReportAssetHistory", projectId, "narrative_master"],
        });
        qc.invalidateQueries({ queryKey: ["geoReportNarrativeMasterDiff", projectId] });
      }
      if (result.report_template_generation) {
        qc.invalidateQueries({ queryKey: ["geoReportAssetDraft", projectId, "report_template"] });
        qc.invalidateQueries({ queryKey: ["geoReportAssetHistory", projectId, "report_template"] });
      }
    },
  });
}

// チャットへのファイル添付（2026-09-16、ユーザー要望:「デザインパートはチャットUIだけで、
// ファイルアップできる形にしよう。それであれば、途中のものもそこから始められる」）。
// .csv → Narrative Master、.html/.htm → Report Template Extractionとして扱われる。
export function useUploadDesignChatFile(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return postForm<GeoReportDesignChatUploadResult>(
        `/api/geo/report-studio/projects/${projectId}/design/chat/upload`,
        form,
      );
    },
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["geoReportDesignChatTurns", projectId] });
      if (result.kind === "narrative_master") {
        qc.invalidateQueries({ queryKey: ["geoReportAssetDraft", projectId, "narrative_master"] });
        qc.invalidateQueries({
          queryKey: ["geoReportAssetHistory", projectId, "narrative_master"],
        });
        qc.invalidateQueries({ queryKey: ["geoReportNarrativeMasterDiff", projectId] });
      }
      if (result.kind === "report_template") {
        qc.invalidateQueries({ queryKey: ["geoReportAssetDraft", projectId, "report_template"] });
        qc.invalidateQueries({ queryKey: ["geoReportAssetHistory", projectId, "report_template"] });
      }
    },
  });
}

// Design Chatの「FIX」ボタン用（2026-09-15）。チャットで提案された
// report_design/signals/queriesを確定し、確定Signal全件分のNarrative Masterを
// 自動生成・保存する。
export function useFixDesign(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      postJson<GeoReportFixDesignResult>(
        `/api/geo/report-studio/projects/${projectId}/design/fix`,
        {},
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoReportDesignState", projectId] });
      qc.invalidateQueries({ queryKey: ["geoReportAssetDraft", projectId, "narrative_master"] });
      qc.invalidateQueries({ queryKey: ["geoReportAssetHistory", projectId, "narrative_master"] });
      qc.invalidateQueries({ queryKey: ["geoReportNarrativeMasterDiff", projectId] });
    },
  });
}

// Report Templateプレビュー（2026-09-15）。渡したcontentをそのままレンダリングした
// HTML文字列を返すステートレスなエンドポイント（DBへは何も保存・参照しない）。
export function usePreviewReportTemplate(projectId: string) {
  return useMutation({
    mutationFn: async (content: ReportTemplateContent): Promise<string> => {
      const res = await fetch(
        `${API_BASE}/api/geo/report-studio/projects/${projectId}/design/report-template/preview`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content }),
        },
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `API error ${res.status}`);
      }
      return res.text();
    },
  });
}

export function useGenerateNarrativeMasterForSignal(projectId: string) {
  return useMutation({
    mutationFn: (signalId: string) =>
      postJson<{ rows: NarrativeMasterRow[] }>(
        `/api/geo/report-studio/projects/${projectId}/design/narrative-master/generate`,
        { signal_id: signalId },
      ),
  });
}

// 2026-09-14追加: 登録済みの全Signal分を一括生成し、既存Draftへ自動保存する
// （Signal別に1件ずつ生成する手間を無くす導線）。サーバー側が保存まで完結させるため、
// Narrative Master Draftのクエリをそのままinvalidateすればよい。
export function useGenerateNarrativeMasterForAllSignals(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      postJson<GeoReportNarrativeMasterGenerationResult>(
        `/api/geo/report-studio/projects/${projectId}/design/narrative-master/generate-all`,
        {},
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoReportAssetDraft", projectId, "narrative_master"] });
      qc.invalidateQueries({ queryKey: ["geoReportAssetHistory", projectId, "narrative_master"] });
      qc.invalidateQueries({ queryKey: ["geoReportNarrativeMasterDiff", projectId] });
    },
  });
}

export function useGenerateReportTemplate(projectId: string) {
  return useMutation({
    mutationFn: () =>
      postJson<{ template: ReportTemplateContent }>(
        `/api/geo/report-studio/projects/${projectId}/design/report-template/generate`,
        {},
      ),
  });
}

export function useExtractReportTemplate(projectId: string) {
  return useMutation({
    mutationFn: (finalReportHtml: string) =>
      postJson<GeoReportTemplateExtractionResult>(
        `/api/geo/report-studio/projects/${projectId}/design/report-template/extract`,
        { final_report_html: finalReportHtml },
      ),
  });
}
