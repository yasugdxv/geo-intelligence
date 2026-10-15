// External Network v1（外部コンタクト / 面談ログ）APIクライアント。
// バックエンドは/api/geo/external-network/*（geo_external_network_api.py）。権限チェックはサーバー側で行う。
// 全endpointがX-IOS-Credential必須（geoIosReviewApi.tsと同じ）。
import { API_BASE as API_BASE_URL } from "@/lib/apiBase";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ensureIosIdentity, getIosAuthHeaders } from "@/lib/geoIosAuthHeader";

const API_BASE = API_BASE_URL;
const BASE = "/api/geo/external-network";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  await ensureIosIdentity();
  const res = await fetch(`${API_BASE}${BASE}${path}`, {
    ...init,
    headers: { ...getIosAuthHeaders(), ...init?.headers },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `API error ${res.status}`);
  }
  return res.json();
}

function jsonInit(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export interface ExtUser {
  user_id: string;
  role: string | null;
  display_name: string | null;
}

/** 表示名があれば表示名、無ければ内部ID。 */
export function userLabel(users: ExtUser[] | undefined, id: string): string {
  return users?.find((u) => u.user_id === id)?.display_name || id;
}

export function useSetMyDisplayName() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (displayName: string) =>
      request<{ user_id: string; display_name: string }>("/users/me/display-name", jsonInit("PUT", { display_name: displayName })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["extNet", "users"] }),
  });
}

/** 会話ログ（面談ログのAI整理）から集計した推定。確認済みの専門性ではない。 */
export interface ExtInferredExpertise {
  topics: { name: string; count: number }[];
  regions: { name: string; count: number }[];
  actors: { name: string; count: number }[];
  log_count: number;
  speaker_view_count: number;
  sample_views: string[];
  last_log_date: string | null;
  note: string;
}

export interface ExtContact {
  contact_id: string;
  name: string;
  affiliation_title: string | null;
  /** 登録時に人が書いた専門領域・コメント（任意）。AI推定とは別。 */
  expertise_note?: string | null;
  /** 詳細取得時のみ。見られる面談ログからの推定。 */
  inferred_expertise?: ExtInferredExpertise | null;
  memo: string | null;
  memo_hidden: boolean;
  status: "active" | "inactive";
  owner_ids: string[];
  created_by: string | null;
  created_at: string | null;
  updated_at: string | null;
  last_contact_at: string | null;
  log_count: number;
  can_edit: boolean;
}

export interface ExtLogContactRef {
  contact_id: string;
  name: string | null;
  affiliation_title: string | null;
  status: string | null;
}

export type ExtAiStatus = "not_run" | "running" | "completed" | "failed";
export type ExtFragmentType = "fact" | "expert_opinion" | "forecast" | "unverified";

export interface ExtAiItem {
  text: string;
  speaker: string | null;
  source_quote: string;
  quote_verified?: boolean;
  type?: ExtFragmentType;
  edited_by_human?: boolean;
}

export interface ExtAiContent {
  summary: string;
  key_takeaways: string[];
  expert_views: ExtAiItem[];
  supporting_points: ExtAiItem[];
  weakening_points: ExtAiItem[];
  watchpoints: { text: string; explicit: boolean }[];
  related_context: {
    countries: string[];
    topics: string[];
    actors: string[];
    situation_candidates: string[];
  };
  related_context_status: "unconfirmed";
  notice: string;
}

export interface ExtAiSummary {
  status: ExtAiStatus | null;
  source: "ai" | "edited" | null;
  content: ExtAiContent | null;
  ai_generated: ExtAiContent | null;
  edited: ExtAiContent | null;
  edited_by: string | null;
  edited_at: string | null;
  ai_updated_after_edit: boolean;
  model: string | null;
  error_message: string | null;
  updated_at: string | null;
}

export interface ExtAttachment {
  attachment_id: string;
  filename: string;
  mime_type: string | null;
  size_bytes: number;
  extraction_status: "ok" | "failed" | "unsupported";
  uploaded_by: string | null;
  created_at: string | null;
}

export interface ExtLog {
  log_id: string;
  contact_date: string;
  ai_status: ExtAiStatus;
  contacts: ExtLogContactRef[];
  participant_ids: string[];
  attachment_count: number;
  summary_snippet: string | null;
  memo_head: string | null;
  created_by: string | null;
  created_at: string | null;
  updated_at: string | null;
  can_edit: boolean;
  can_delete: boolean;
  // 詳細のみ
  memo?: string | null;
  attachments?: ExtAttachment[];
  ai_summary?: ExtAiSummary | null;
}

export interface ExtLogFilters {
  contact_id?: string;
  participant_id?: string;
  date_from?: string;
  date_to?: string;
  q?: string;
}

function qs(params: Record<string, string | boolean | undefined>): string {
  const p = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== "" && v !== false) p.set(k, String(v));
  });
  const s = p.toString();
  return s ? `?${s}` : "";
}

export const attachmentDownloadPath = (logId: string, attachmentId: string) =>
  `${BASE}/logs/${logId}/attachments/${attachmentId}`;

/** 認可つきダウンロード。公開URLは無く、credentialヘッダー付きfetchで取得してから保存する。 */
export async function downloadAttachment(logId: string, att: ExtAttachment): Promise<void> {
  await ensureIosIdentity();
  const res = await fetch(`${API_BASE}${attachmentDownloadPath(logId, att.attachment_id)}`, {
    headers: getIosAuthHeaders(),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `API error ${res.status}`);
  }
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = att.filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function useExtUsers() {
  return useQuery({
    queryKey: ["extNet", "users"],
    queryFn: () => request<{ items: ExtUser[]; me: string }>("/users"),
    retry: false,
  });
}

export function useExtContacts(filters: { q?: string; owner_id?: string; include_inactive?: boolean }) {
  return useQuery({
    queryKey: ["extNet", "contacts", filters],
    queryFn: () => request<{ items: ExtContact[] }>(`/contacts${qs(filters)}`),
    retry: false,
  });
}

export function useExtContact(contactId: string) {
  return useQuery({
    queryKey: ["extNet", "contact", contactId],
    queryFn: () => request<ExtContact & { logs: ExtLog[] }>(`/contacts/${contactId}`),
    enabled: !!contactId,
    retry: false,
  });
}

export interface ExtContactInput {
  name?: string;
  affiliation_title?: string;
  expertise_note?: string;
  memo?: string;
  owner_ids?: string[];
}

export function useSaveExtContact() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ contactId, body }: { contactId?: string; body: ExtContactInput }) =>
      contactId
        ? request<ExtContact>(`/contacts/${contactId}`, jsonInit("PATCH", body))
        : request<ExtContact>("/contacts", jsonInit("POST", body)),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["extNet"] }),
  });
}

export function useDeactivateExtContact() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (contactId: string) =>
      request<ExtContact>(`/contacts/${contactId}/deactivate`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["extNet"] }),
  });
}

export function useExtLogs(filters: ExtLogFilters) {
  return useQuery({
    queryKey: ["extNet", "logs", filters],
    queryFn: () => request<{ items: ExtLog[] }>(`/logs${qs({ ...filters })}`),
    retry: false,
  });
}

export function useExtLog(logId: string) {
  return useQuery({
    queryKey: ["extNet", "log", logId],
    queryFn: () => request<ExtLog>(`/logs/${logId}`),
    enabled: !!logId,
    retry: false,
  });
}

export interface ExtLogCreateInput {
  contact_ids: string[];
  contact_date: string;
  participant_ids: string[];
  memo: string;
  files: File[];
}

export function useCreateExtLog() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: ExtLogCreateInput) => {
      const fd = new FormData();
      input.contact_ids.forEach((c) => fd.append("contact_ids", c));
      input.participant_ids.forEach((p) => fd.append("participant_ids", p));
      fd.append("contact_date", input.contact_date);
      if (input.memo.trim()) fd.append("memo", input.memo);
      input.files.forEach((f) => fd.append("files", f));
      return request<ExtLog>("/logs", { method: "POST", body: fd });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["extNet"] }),
  });
}

export function useUpdateExtLog(logId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      contact_ids?: string[];
      contact_date?: string;
      participant_ids?: string[];
      memo?: string;
    }) => request<ExtLog>(`/logs/${logId}`, jsonInit("PATCH", body)),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["extNet"] }),
  });
}

export function useDeleteExtLog() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (logId: string) => request<{ ok: boolean }>(`/logs/${logId}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["extNet"] }),
  });
}

export function useAddExtAttachment(logId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return request<ExtAttachment>(`/logs/${logId}/attachments`, { method: "POST", body: fd });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["extNet"] }),
  });
}

export function useDeleteExtAttachment(logId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (attachmentId: string) =>
      request<{ ok: boolean }>(`/logs/${logId}/attachments/${attachmentId}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["extNet"] }),
  });
}

export function useRunExtAiSummary(logId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => request<ExtLog>(`/logs/${logId}/ai-summary/run`, { method: "POST" }),
    // 失敗時もサーバー側でai_status=failedが記録されるため、成否に関わらず再取得する
    onSettled: () => qc.invalidateQueries({ queryKey: ["extNet"] }),
  });
}

export function useSaveExtAiEdit(logId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (edited: ExtAiContent) => request<ExtLog>(`/logs/${logId}/ai-summary`, jsonInit("PUT", { edited })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["extNet"] }),
  });
}

export function useAdoptExtAi(logId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => request<ExtLog>(`/logs/${logId}/ai-summary/adopt-ai`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["extNet"] }),
  });
}
