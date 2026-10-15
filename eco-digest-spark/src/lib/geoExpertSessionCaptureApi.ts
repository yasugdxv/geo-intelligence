// Expert Session Capture V1（Phase S2-S3、2026-10-02）APIクライアント。
// バックエンドは/api/geo/expert-session/*（geo_expert_session_capture_api.py）に接続する。
// 全endpointがX-IOS-Credential認証必須のため、getIosAuthHeaders()を必ず付ける
// （geoIosReviewApi.tsと同じパターン）。Reveal後のReading表示自体は、既存Phase S1 route
// （geoIosSituationPresentationApi.useSituationPresentationByResponseId）をそのまま使う
// ——このファイルはCase Run/Evaluationの状態管理のみを扱う。
import { API_BASE as API_BASE_URL } from "@/lib/apiBase";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getIosAuthHeaders } from "@/lib/geoIosAuthHeader";

const API_BASE = API_BASE_URL;

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

function postJson<T>(path: string, body: unknown = {}): Promise<T> {
  return fetchJson<T>(path, {
    method: "POST",
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

export type GeoExpertSessionCaseType = "gold" | "calibration";
export type GeoExpertSessionRunStatus =
  "not_started" | "in_progress" | "locked" | "revealed" | "completed";

export interface GeoExpertSessionExpertFirstInput {
  situation?: string;
  what_to_look_at?: string;
  key_actor?: string;
  relationship_structure?: string;
  multiple_readings?: string;
  primary_reading?: string;
  supporting_evidence?: string;
  weakening_evidence?: string;
  switch_condition?: string;
}

export interface GeoExpertSessionRubricItem {
  rating: "valid" | "needs_minor_fix" | "needs_major_fix" | null;
  comment?: string | null;
}

export type GeoExpertSessionRubricKey =
  | "situation_understanding"
  | "initial_cue"
  | "actor_structure"
  | "multiple_readings"
  | "primary_reading"
  | "evidence_weakening"
  | "switch_condition";

export interface GeoExpertSessionGapReview {
  expert_had_ai_missed?: string;
  ai_had_expert_missed?: string;
  weight_differences?: string;
  primary_reading_diff?: "same" | "different" | "partially_different" | null;
  primary_reading_diff_reason?: string;
  how_to_fix_ai?: string;
}

export interface GeoExpertSessionEvaluation {
  evaluation_id: string;
  run_id: string;
  rubric_assessments: Partial<Record<GeoExpertSessionRubricKey, GeoExpertSessionRubricItem>> | null;
  feedback_tags: string[];
  expert_comment: string | null;
  gap_review: GeoExpertSessionGapReview | null;
  submitted_at: string | null;
}

export interface GeoExpertSessionCaseView {
  case_key: string;
  case_type: GeoExpertSessionCaseType;
  title: string;
  status: GeoExpertSessionRunStatus | "unresolved";
  expert_first_input: GeoExpertSessionExpertFirstInput | null;
  locked_at: string | null;
  revealed_at: string | null;
  submitted_at: string | null;
  run_id: string | null;
  // Blind Integrity（Phase S2-S3 §9/§15）: Goldは常に存在、CalibrationはRevealed/Completed
  // の場合のみ存在する。キー自体が無いことがGateの根拠——フロントは`"response_id" in view`を
  // Reveal済みかどうかの判定には使わず、backendのstatus/revealed_atを見ること。
  response_id?: string;
  evaluation?: GeoExpertSessionEvaluation | null;
}

export interface GeoExpertSessionCaseListResponse {
  runnable: GeoExpertSessionCaseView[];
  unresolved: Array<{
    case_key: string;
    case_type: GeoExpertSessionCaseType;
    title: string;
    status: "unresolved";
  }>;
}

export function useExpertSessionCases() {
  return useQuery({
    queryKey: ["geoExpertSessionCases"],
    queryFn: () => fetchJson<GeoExpertSessionCaseListResponse>("/api/geo/expert-session/cases"),
    // 未認証（X-IOS-Credential未設定）の401を無駄にリトライしない——IdentityBoxでの
    // credential設定後、手動invalidateで即座に再取得させる設計にする。
    retry: false,
  });
}

export function useExpertSessionCase(caseKey: string) {
  return useQuery({
    queryKey: ["geoExpertSessionCase", caseKey],
    queryFn: () => fetchJson<GeoExpertSessionCaseView>(`/api/geo/expert-session/cases/${caseKey}`),
    enabled: !!caseKey,
    retry: false,
  });
}

function useInvalidateCase(caseKey: string) {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: ["geoExpertSessionCase", caseKey] });
    qc.invalidateQueries({ queryKey: ["geoExpertSessionCases"] });
  };
}

export function useStartExpertSessionCase(caseKey: string) {
  const invalidate = useInvalidateCase(caseKey);
  return useMutation({
    mutationFn: () =>
      postJson<GeoExpertSessionCaseView>(`/api/geo/expert-session/cases/${caseKey}/start`),
    onSuccess: invalidate,
  });
}

export function useSaveExpertFirstInput(caseKey: string) {
  const invalidate = useInvalidateCase(caseKey);
  return useMutation({
    mutationFn: (input: GeoExpertSessionExpertFirstInput) =>
      patchJson<GeoExpertSessionCaseView>(
        `/api/geo/expert-session/cases/${caseKey}/expert-first-input`,
        input,
      ),
    onSuccess: invalidate,
  });
}

export function useLockExpertSessionCase(caseKey: string) {
  const invalidate = useInvalidateCase(caseKey);
  return useMutation({
    mutationFn: () =>
      postJson<GeoExpertSessionCaseView>(`/api/geo/expert-session/cases/${caseKey}/lock`),
    onSuccess: invalidate,
  });
}

export function useRevealExpertSessionCase(caseKey: string) {
  const invalidate = useInvalidateCase(caseKey);
  return useMutation({
    mutationFn: () =>
      postJson<GeoExpertSessionCaseView>(`/api/geo/expert-session/cases/${caseKey}/reveal`),
    onSuccess: invalidate,
  });
}

export function useSubmitExpertSessionCase(caseKey: string) {
  const invalidate = useInvalidateCase(caseKey);
  return useMutation({
    mutationFn: () =>
      postJson<GeoExpertSessionCaseView>(`/api/geo/expert-session/cases/${caseKey}/submit`),
    onSuccess: invalidate,
  });
}

export interface GeoExpertSessionSaveEvaluationInput {
  rubric_assessments?: Partial<Record<GeoExpertSessionRubricKey, GeoExpertSessionRubricItem>>;
  feedback_tags?: string[];
  expert_comment?: string;
  gap_review?: GeoExpertSessionGapReview;
}

export function useSaveExpertSessionEvaluation(caseKey: string) {
  const invalidate = useInvalidateCase(caseKey);
  return useMutation({
    mutationFn: (input: GeoExpertSessionSaveEvaluationInput) =>
      patchJson<GeoExpertSessionEvaluation>(
        `/api/geo/expert-session/cases/${caseKey}/evaluation`,
        input,
      ),
    onSuccess: invalidate,
  });
}
