// Geopolitical Intelligence OS Phase H: Analysis Session / Scenario Candidate / Analysis Design
// APIクライアント。既存 lib/geoKnowledgeApi.ts と同じ流儀（fetchJson等を独立して持つ、
// API_BASEのみ重複させる）で新規追加する。バックエンドは/api/geo/ios/analysis-sessions/*
// （geo_ios_analysis_session_api.py、Phase B/C）にそのまま接続する——新しいAPIは作らない。
import { API_BASE as API_BASE_URL } from "@/lib/apiBase";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { GeoIosBoardCase } from "@/lib/geoIosWorkflowBoardApi";

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

export interface GeoIosAnalysisSession {
  analysis_session_id: string;
  title: string;
  theme?: string | null;
  user_id: string;
  mode?: "create" | "review";
  status: "active" | "scenario_selected" | "design_in_progress" | "handed_off" | "archived";
  selected_scenario_id?: string | null;
  linked_report_project_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface GeoIosScenarioCandidate {
  scenario_candidate_id: string;
  analysis_session_id: string;
  rank: number;
  title: string;
  content: {
    analysis_question?: string;
    why_this_matters?: string | null;
    main_lens?: string | null;
    key_actors?: string[];
    confidence?: string | null;
    source_cqas?: { request_id: string; response_id: string };
    [key: string]: unknown;
  };
  displayed: boolean;
  selected: boolean;
  generated_at: string;
}

export interface GeoIosAnalysisDesign {
  analysis_design_id: string;
  analysis_session_id: string;
  version: number;
  investigation_question?: string | null;
  scope?: string | null;
  key_actors: string[];
  analytical_lenses: string[];
  hypotheses: string[];
  alternative_hypotheses: string[];
  evidence_needed: string[];
  known_evidence: string[];
  knowledge_gaps: string[];
  watch_points: string[];
  report_structure: Record<string, unknown>;
  confidence?: string | null;
  assumptions: string[];
  change_note?: string | null;
  created_by: string;
  created_at: string;
}

// ---- Session ----------------------------------------------------------------

export function useCreateAnalysisSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      title: string;
      user_id: string;
      theme?: string;
      mode?: "create" | "review";
    }) => postJson<GeoIosAnalysisSession>("/api/geo/ios/analysis-sessions", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["geoIosAnalysisSessions"] }),
  });
}

export function useAnalysisSessions(userId?: string, mode?: "create" | "review") {
  const usp = new URLSearchParams();
  if (userId) usp.set("user_id", userId);
  if (mode) usp.set("mode", mode);
  return useQuery({
    queryKey: ["geoIosAnalysisSessions", userId, mode],
    queryFn: () =>
      fetchJson<{ sessions: GeoIosAnalysisSession[] }>(
        `/api/geo/ios/analysis-sessions?${usp.toString()}`,
      ),
  });
}

export function useAnalysisSession(sessionId: string) {
  return useQuery({
    queryKey: ["geoIosAnalysisSession", sessionId],
    queryFn: () => fetchJson<GeoIosAnalysisSession>(`/api/geo/ios/analysis-sessions/${sessionId}`),
    enabled: !!sessionId,
  });
}

// Session詳細ページのWorkflow Header（設計/ドラフト/AIレビュー/専門家レビュー/承認・
// ストック）用。Workflow Boardと同じgeo_ios_workflow_board_service._build_caseを
// 1件分だけ呼ぶBackend専用エンドポイントを参照するため、GeoIosBoardCaseと同じ形の
// レスポンスになる（型を再定義せずそのまま再利用する）。
export function useSessionWorkflowState(sessionId: string) {
  return useQuery({
    queryKey: ["geoIosSessionWorkflowState", sessionId],
    queryFn: () =>
      fetchJson<GeoIosBoardCase>(`/api/geo/ios/analysis-sessions/${sessionId}/workflow-state`),
    enabled: !!sessionId,
  });
}

// ---- Scenario Candidates ------------------------------------------------------

export function useGenerateScenarios(sessionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { actor_id: string; theme_override?: string; display_top_n?: number }) =>
      postJson<{
        candidates: GeoIosScenarioCandidate[];
        total_count: number;
        displayed_count: number;
      }>(`/api/geo/ios/analysis-sessions/${sessionId}/generate-scenarios`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoIosScenarioCandidates", sessionId] });
      qc.invalidateQueries({ queryKey: ["geoIosAnalysisSession", sessionId] });
      // 「局面を読む」画面(geo-ios-situation-analysis-view)はこの候補生成結果を
      // geoIosSituationPresentation経由で表示するため、そちらも無効化しないと
      // 再生成後も古い読み方が表示され続ける。
      qc.invalidateQueries({ queryKey: ["geoIosSituationPresentation", sessionId] });
    },
  });
}

export function useScenarioCandidates(sessionId: string, displayedOnly = false) {
  const usp = new URLSearchParams();
  if (displayedOnly) usp.set("displayed_only", "true");
  return useQuery({
    queryKey: ["geoIosScenarioCandidates", sessionId, displayedOnly],
    queryFn: () =>
      fetchJson<{ candidates: GeoIosScenarioCandidate[] }>(
        `/api/geo/ios/analysis-sessions/${sessionId}/scenario-candidates?${usp.toString()}`,
      ),
    enabled: !!sessionId,
  });
}

export function useSelectScenarioCandidate(sessionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      scenarioCandidateId,
      actorId,
      confirmAfterHandoff,
    }: {
      scenarioCandidateId: string;
      actorId: string;
      confirmAfterHandoff?: boolean;
    }) =>
      postJson<GeoIosScenarioCandidate>(
        `/api/geo/ios/analysis-sessions/scenario-candidates/${scenarioCandidateId}/select`,
        { actor_id: actorId, confirm_after_handoff: confirmAfterHandoff ?? false },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoIosScenarioCandidates", sessionId] });
      qc.invalidateQueries({ queryKey: ["geoIosAnalysisSession", sessionId] });
      qc.invalidateQueries({ queryKey: ["geoIosSessionWorkflowState", sessionId] });
      // 2026-09-23追加、ユーザー報告「読み方A,B,Cでボックスの選択が効いていない」:
      // 選択自体（selected_scenario_candidate_id）はサーバー側で正しく更新されるが、
      // 画面側はgeo-ios-situation-analysis-view.tsxのeffectiveSelectedIdがこの
      // useSituationPresentation（別クエリ）のrawAnalysis.selected_scenario_candidate_id
      // を見て選択状態を描画しているため、このクエリを無効化していないとクリックしても
      // 表示が変わらないように見えていた。
      qc.invalidateQueries({ queryKey: ["geoIosSituationPresentation", sessionId] });
    },
  });
}

// ---- Analysis Design -----------------------------------------------------------

export function useCurrentDesign(sessionId: string) {
  return useQuery({
    queryKey: ["geoIosCurrentDesign", sessionId],
    queryFn: () =>
      fetchJson<GeoIosAnalysisDesign>(
        `/api/geo/ios/analysis-sessions/${sessionId}/design-versions/current`,
      ),
    enabled: !!sessionId,
    retry: false,
  });
}

export function useDesignHistory(sessionId: string) {
  return useQuery({
    queryKey: ["geoIosDesignHistory", sessionId],
    queryFn: () =>
      fetchJson<{ versions: GeoIosAnalysisDesign[] }>(
        `/api/geo/ios/analysis-sessions/${sessionId}/design-versions`,
      ),
    enabled: !!sessionId,
  });
}

export function useGenerateDesign(sessionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { created_by: string }) =>
      postJson<GeoIosAnalysisDesign>(
        `/api/geo/ios/analysis-sessions/${sessionId}/generate-design`,
        body,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoIosCurrentDesign", sessionId] });
      qc.invalidateQueries({ queryKey: ["geoIosDesignHistory", sessionId] });
      qc.invalidateQueries({ queryKey: ["geoIosSessionWorkflowState", sessionId] });
    },
  });
}

export interface RefineDesignResult {
  design_version: GeoIosAnalysisDesign;
  change_summary: { user_message: string; changed_fields: string[] };
}

export function useRefineDesign(sessionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { user_message: string; created_by: string }) =>
      postJson<RefineDesignResult>(
        `/api/geo/ios/analysis-sessions/${sessionId}/refine-design`,
        body,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoIosCurrentDesign", sessionId] });
      qc.invalidateQueries({ queryKey: ["geoIosDesignHistory", sessionId] });
    },
  });
}

// ---- Handoff -----------------------------------------------------------------

export interface CreateReportResult {
  report_project: { project_id: string; project_code: string; project_title: string };
  analysis_session: GeoIosAnalysisSession;
}

export function useCreateReport(sessionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { actor_id: string; project_code?: string }) =>
      postJson<CreateReportResult>(
        `/api/geo/ios/analysis-sessions/${sessionId}/create-report`,
        body,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoIosAnalysisSession", sessionId] });
      qc.invalidateQueries({ queryKey: ["geoIosSessionWorkflowState", sessionId] });
    },
  });
}

export function useAnalysisSessionByReportProject(reportProjectId: string) {
  return useQuery({
    queryKey: ["geoIosAnalysisSessionByReportProject", reportProjectId],
    queryFn: () =>
      fetchJson<GeoIosAnalysisSession>(
        `/api/geo/ios/analysis-sessions/by-report-project/${reportProjectId}`,
      ),
    enabled: !!reportProjectId,
    retry: false,
  });
}
