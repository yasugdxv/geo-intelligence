// Geopolitical Intelligence OS: REVIEW MODE APIクライアント（§17-22）。
// バックエンドは/api/geo/ios/review-import/*（geo_ios_review_import_api.py）に接続する。
// 既存Attachment/File infrastructure（geo_documents_store経由）をそのまま使う——新しい
// 独自File storageのクライアントは作らない。
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

export interface GeoIosReviewImportRecord {
  review_import_id: string;
  analysis_session_id: string;
  original_filename: string | null;
  original_text: string;
  status: "pending" | "context_extracted" | "readings_ready" | "failed";
  error_message: string | null;
  extracted_context: {
    analysis_theme: string;
    current_situation: string;
    question: string;
    report_context: string;
    not_explicit_fields: string[];
  } | null;
  reconstructed_reading: {
    primary_reading: string | null;
    key_actors: string[];
    actor_objectives: { actor: string; objective: string }[];
    relationships: { between: string; description: string }[];
    cue: string | null;
    assumptions: string[];
    causal_logic: string | null;
    direction_of_travel: string | null;
    scenario: string | null;
    supporting_evidence: string[];
    weakening_evidence: string[];
    missing_evidence: string[];
    not_explicit_fields: string[];
  } | null;
  current_reading_scenario_candidate_id: string | null;
  selected_scenario_candidate_id: string | null;
  revision_strategy: "keep_and_reinforce" | "replace" | "integrate" | null;
}

export function useImportReportForReview(sessionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { created_by: string; file?: File; pastedText?: string }) => {
      const formData = new FormData();
      formData.append("created_by", body.created_by);
      if (body.file) formData.append("file", body.file);
      if (body.pastedText) formData.append("pasted_text", body.pastedText);
      return fetchJson<GeoIosReviewImportRecord>(`/api/geo/ios/review-import/${sessionId}/import`, {
        method: "POST",
        body: formData,
      });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["geoIosReviewImport", sessionId] }),
  });
}

export function useGenerateReviewReadings(sessionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { actor_id: string }) =>
      postJson<{ status: string }>(
        `/api/geo/ios/review-import/${sessionId}/generate-review-readings`,
        body,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["geoIosReviewImport", sessionId] }),
  });
}

export function useReviewImport(sessionId: string, options?: { pollWhilePending?: boolean }) {
  return useQuery({
    queryKey: ["geoIosReviewImport", sessionId],
    queryFn: () => fetchJson<GeoIosReviewImportRecord>(`/api/geo/ios/review-import/${sessionId}`),
    enabled: !!sessionId,
    refetchInterval: (query) => {
      if (!options?.pollWhilePending) return false;
      const status = query.state.data?.status;
      return status === "pending" || status === "context_extracted" ? 2000 : false;
    },
  });
}

export function useSetRevisionStrategy(sessionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      selected_scenario_candidate_id: string;
      strategy: "keep_and_reinforce" | "replace" | "integrate";
      actor_id: string;
    }) =>
      postJson<GeoIosReviewImportRecord>(
        `/api/geo/ios/review-import/${sessionId}/revision-strategy`,
        body,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoIosReviewImport", sessionId] });
      qc.invalidateQueries({ queryKey: ["geoIosSituationPresentation", sessionId] });
    },
  });
}

export function useGenerateRevisedDraft(sessionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { created_by: string }) =>
      postJson<{ report_draft_id: string; status: string }>(
        `/api/geo/ios/review-import/${sessionId}/generate-revised-draft`,
        body,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["geoIosReportDraftCurrent", sessionId] }),
  });
}
