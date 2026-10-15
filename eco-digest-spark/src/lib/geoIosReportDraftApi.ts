// Geopolitical Intelligence OS: Native Report Draft（IOS自身のDraft生成〜AI Review）
// APIクライアント。バックエンドは/api/geo/ios/report-drafts（geo_ios_report_draft_api.py）に
// 接続する。Draft生成・AI Review実行はExpert AI呼び出しをBackgroundTasksで実行するため、
// POST直後はstatus=pendingで返る——完了はポーリングで検知する（既存
// geoIosConsultationApi.tsと同じパターン）。
import { API_BASE as API_BASE_URL } from "@/lib/apiBase";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

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

export interface GeoIosDraftSection {
  heading: string;
  body: string;
  supporting_evidence_ids?: string[] | null;
}

export type GeoIosDraftSource = "ai_generated" | "human_edit" | "uploaded";
export type GeoIosDraftStatus = "pending" | "completed" | "failed";

export interface GeoIosReportDraft {
  report_draft_id: string;
  analysis_session_id: string;
  version: number;
  parent_report_draft_id: string | null;
  source: GeoIosDraftSource;
  status: GeoIosDraftStatus;
  sections: GeoIosDraftSection[] | null;
  content_hash: string | null;
  change_note: string | null;
  created_by: string;
  error_message: string | null;
  expert_request_id: string | null;
  expert_response_id: string | null;
  created_at: string;
  completed_at: string | null;
}

export type GeoIosFindingSeverity = "Critical" | "Major" | "Minor";
export type GeoIosFindingStatus = "open" | "waived";

export interface GeoIosDraftFinding {
  finding_id: string;
  category: string | null;
  severity: GeoIosFindingSeverity;
  finding: string;
  recommendation: string | null;
  evidence_ids: string[] | null;
  status: GeoIosFindingStatus;
  waiver_reason: string | null;
  waived_by: string | null;
  waived_at: string | null;
}

export interface GeoIosDraftAiReview {
  draft_ai_review_id: string;
  report_draft_id: string;
  review_attempt: number;
  status: GeoIosDraftStatus;
  findings: GeoIosDraftFinding[] | null;
  error_message: string | null;
  requested_by: string;
  created_at: string;
  completed_at: string | null;
}

export function useCurrentDraft(sessionId: string) {
  return useQuery({
    queryKey: ["geoIosReportDraftCurrent", sessionId],
    queryFn: () => fetchJson<GeoIosReportDraft>(`/api/geo/ios/report-drafts/current?analysis_session_id=${sessionId}`),
    enabled: !!sessionId,
    retry: false,
    refetchInterval: (query) => (query.state.data?.status === "pending" ? 4000 : false),
  });
}

export function useDraftHistory(sessionId: string) {
  return useQuery({
    queryKey: ["geoIosReportDraftHistory", sessionId],
    queryFn: () => fetchJson<{ drafts: GeoIosReportDraft[] }>(`/api/geo/ios/report-drafts?analysis_session_id=${sessionId}`),
    enabled: !!sessionId,
  });
}

export function useCreateDraft(sessionId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      created_by: string; source: GeoIosDraftSource;
      sections?: GeoIosDraftSection[]; base_report_draft_id?: string | null; change_note?: string;
    }) =>
      fetchJson<GeoIosReportDraft>("/api/geo/ios/report-drafts", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ analysis_session_id: sessionId, ...body }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["geoIosReportDraftCurrent", sessionId] });
      queryClient.invalidateQueries({ queryKey: ["geoIosReportDraftHistory", sessionId] });
    },
  });
}

// チャット指示によるDraft書き換え。in-place更新ではなく既存createDraftと同じ
// immutable versioningで新しいpending Versionを作り、ポーリング(useCurrentDraft)で
// 完了を検知する。
//
// 2026-09-23変更、ユーザー指示「AIで編集するところに追加資料をアップする機能が
// いるね」: JSON bodyからmultipart/form-dataへ変更し、既存useImportDraftと同じ
// パターンでオプショナルな添付ファイルを送れるようにした（Content-Type:
// application/jsonヘッダーは付けない——FormDataを渡すとブラウザが正しいboundary付き
// Content-Typeを自動設定するため）。
export function useAiEditDraft(sessionId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { actor_id: string; instruction: string; file?: File }) => {
      const form = new FormData();
      form.set("actor_id", body.actor_id);
      form.set("instruction", body.instruction);
      if (body.file) form.set("file", body.file);
      return fetchJson<GeoIosReportDraft>(`/api/geo/ios/report-drafts/${sessionId}/ai-edit`, {
        method: "POST", body: form,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["geoIosReportDraftCurrent", sessionId] });
      queryClient.invalidateQueries({ queryKey: ["geoIosReportDraftHistory", sessionId] });
    },
  });
}

// 文書アップロードによるDraft取込。multipart/form-dataのため、他のmutationと違い
// Content-Type: application/jsonヘッダーは付けない(fetchにFormDataを渡すとブラウザが
// 正しいboundary付きContent-Typeを自動設定する)。
export function useImportDraft(sessionId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { created_by: string; file: File }) => {
      const form = new FormData();
      form.set("created_by", body.created_by);
      form.set("file", body.file);
      return fetchJson<GeoIosReportDraft>(`/api/geo/ios/report-drafts/${sessionId}/import`, {
        method: "POST", body: form,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["geoIosReportDraftCurrent", sessionId] });
      queryClient.invalidateQueries({ queryKey: ["geoIosReportDraftHistory", sessionId] });
    },
  });
}

export function useLatestAiReview(reportDraftId: string | undefined) {
  return useQuery({
    queryKey: ["geoIosDraftAiReviewLatest", reportDraftId],
    queryFn: () => fetchJson<GeoIosDraftAiReview>(`/api/geo/ios/report-drafts/${reportDraftId}/ai-review/latest`),
    enabled: !!reportDraftId,
    retry: false,
    refetchInterval: (query) => (query.state.data?.status === "pending" ? 4000 : false),
  });
}

export function useRequestAiReview(reportDraftId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { requested_by: string }) =>
      fetchJson<GeoIosDraftAiReview>(`/api/geo/ios/report-drafts/${reportDraftId}/ai-review`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["geoIosDraftAiReviewLatest", reportDraftId] });
    },
  });
}

export function useWaiveFinding(reportDraftId: string | undefined, draftAiReviewId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { finding_id: string; waiver_reason: string; waived_by: string }) =>
      fetchJson<GeoIosDraftAiReview>(
        `/api/geo/ios/report-drafts/${reportDraftId}/ai-review/${draftAiReviewId}/waive`,
        { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["geoIosDraftAiReviewLatest", reportDraftId] });
    },
  });
}
