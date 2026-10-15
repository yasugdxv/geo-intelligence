// Intelligence AI プリセット調査 API クライアント（/api/geo/intelligence-ai/presets）。
// ログインなしでも使える（Chatタブと同じ）。X-IOS-Credential が保存されていれば付けるが、無くても動作し、
// 認証用のIDを自動発行することもしない。バックエンドは、ログイン状況に応じて見せてよいデータだけを検索する
// （未ログインでは、会社全体に公開されたデータのみ。外部ネットワークの面談ログはログインが必要）。
import { API_BASE as API_BASE_URL } from "@/lib/apiBase";
import { useMutation, useQuery } from "@tanstack/react-query";
import { getIosAuthHeaders, getIosCredential } from "@/lib/geoIosAuthHeader";
import { useLanguage } from "@/lib/i18n";
import type { GeoReportSummary } from "@/lib/geoKnowledgeApi";

const API_BASE = API_BASE_URL;
const BASE = "/api/geo/intelligence-ai/presets";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...getIosAuthHeaders(), ...init?.headers },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `API error ${res.status}`);
  }
  return res.json();
}

export type PresetId =
  | "past_cases"
  | "find_experts"
  | "changes_since_last"
  | "compare_views"
  | "test_hypothesis"
  | "early_signals";

export interface PresetField {
  key: string;
  label: string;
  type: "text" | "textarea" | "select" | "date";
  required: boolean;
  default: string | null;
  placeholder: string | null;
  options: { value: string; label: string }[];
}

export interface PresetDefinition {
  id: PresetId;
  title: string;
  icon: string;
  description: string;
  intro: string;
  fields: PresetField[];
  optional_sources: { key: "web" | "external_network"; label: string; default: boolean }[];
  default_sources: { key: string; label: string }[];
}

export interface SourceAvailability {
  available: boolean;
  reason: string | null;
}

export interface PresetListResponse {
  presets: PresetDefinition[];
  source_availability: Record<string, SourceAvailability>;
}

/** フォームの条件。選択式・テキストは string、検索ソースの選択は include_* の boolean。 */
export type PresetConditions = Record<string, string | boolean | null>;

export type ClaimType = "source_supported" | "cross_source_synthesis" | "ai_inference";

export interface PresetSourceStatus {
  source: string;
  label: string;
  status: "searched" | "no_access" | "unavailable" | "error" | "skipped" | "not_used";
  reason: string | null;
  count: number;
}

export interface PresetCitation {
  ref_id: string;
  source_type: string;
  source_label: string;
  origin: "internal" | "web";
  id: string;
  title: string | null;
  date: string | null;
  url: string | null;
  meta: Record<string, unknown>;
}

/** 追加対話のために会話ごとに保持する文脈。本文は持たず、参照ID（種別+ID）と検索条件だけを持つ。 */
export interface PresetContext {
  preset_id: PresetId;
  conditions: PresetConditions;
  evidence: { ref_id: string; source_type: string; id: string }[];
  web_evidence: { ref_id: string; title: string | null; url: string | null; date: string | null; snippet: string | null }[];
  next_ref: number;
}

export interface PresetRunResult {
  preset_id: PresetId;
  conditions: PresetConditions;
  search_terms: string[];
  source_status: PresetSourceStatus[];
  assumptions: string[];
  evidence_total: number;
  summary: string;
  sections: { key: string; title: string; items: { text: string; claim_type: ClaimType; refs: string[] }[] }[];
  insufficient_evidence: boolean;
  citations: PresetCitation[];
  warnings: string[];
  context: PresetContext;
}

export interface PresetFollowupResult {
  answer: string;
  claims: { text: string; claim_type: ClaimType; refs: string[] }[];
  citations: PresetCitation[];
  insufficient_evidence: boolean;
  warnings: string[];
  source_status: PresetSourceStatus[];
  context: PresetContext;
  conditions: PresetConditions;
  new_search: boolean;
}

export interface PresetExtractResult {
  conditions: PresetConditions;
  missing: string[];
  recognized: string[];
  assistant_message: string;
}

/** チャットの1メッセージに付く、入力フォームの状態（会話履歴にそのまま保存される）。 */
export interface PresetFormState {
  presetId: PresetId;
  conditions: PresetConditions;
  /** 自由入力から条件を読み取るたびに増やす。フォームが入力欄を再初期化する合図。 */
  version: number;
  status: "editing" | "running" | "done" | "superseded";
}

export function usePresets() {
  const { lang } = useLanguage();
  return useQuery({
    queryKey: ["geoIaPresets", lang],
    queryFn: () => request<PresetListResponse>(`?lang=${lang}`),
    retry: false,
    staleTime: 60_000,
  });
}

export function useExtractPresetConditions() {
  const { lang } = useLanguage();
  return useMutation({
    mutationFn: (v: { preset_id: PresetId; message: string; conditions: PresetConditions }) =>
      request<PresetExtractResult>("/extract", { method: "POST", body: JSON.stringify({ ...v, lang }) }),
  });
}

export function useRunPreset() {
  const { lang } = useLanguage();
  return useMutation({
    mutationFn: (v: { preset_id: PresetId; conditions: PresetConditions }) =>
      request<PresetRunResult>("/run", { method: "POST", body: JSON.stringify({ ...v, lang }) }),
  });
}

export function usePresetFollowup() {
  const { lang } = useLanguage();
  return useMutation({
    mutationFn: (v: { message: string; context: PresetContext; history: { role: string; content: string }[] }) =>
      request<PresetFollowupResult>("/followup", { method: "POST", body: JSON.stringify({ ...v, lang }) }),
  });
}

/** 引用から開くレポート詳細（既存の GET /api/geo/reports/{id}。閲覧者の権限で取得される）。 */
export function useReportDetail(reportId: string) {
  return useQuery({
    queryKey: ["geoIaReportDetail", reportId],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/geo/reports/${reportId}`, { headers: getIosAuthHeaders() });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body.detail === "string" ? body.detail : `API error ${res.status}`);
      }
      return (await res.json()) as GeoReportSummary;
    },
    enabled: !!reportId,
    retry: false,
  });
}

// ---- 会話のサーバー保存（ログイン中のみ。本人の会話だけ。テーブル未作成・未ログインでも、画面はブラウザ内保存で動く） ----
export interface ServerConversation {
  id: string;
  title: string;
  messages: unknown[];
  preset: unknown;
  updatedAt: string;
}

function convHeaders(): HeadersInit | null {
  return getIosCredential() ? { "Content-Type": "application/json", ...getIosAuthHeaders() } : null;
}

const CONV = `${API_BASE}/api/geo/intelligence-ai/conversations`;

/** サーバーの会話一覧。未ログイン・未作成・失敗は空配列（エラーにしない）。 */
export async function fetchServerConversations(): Promise<ServerConversation[]> {
  const headers = convHeaders();
  if (!headers) return [];
  try {
    const res = await fetch(CONV, { headers });
    if (!res.ok) return [];
    return ((await res.json()).items ?? []) as ServerConversation[];
  } catch {
    return [];
  }
}

export async function saveServerConversation(c: ServerConversation): Promise<void> {
  const headers = convHeaders();
  if (!headers) return;
  try {
    await fetch(`${CONV}/${encodeURIComponent(c.id)}`, {
      method: "PUT",
      headers,
      body: JSON.stringify({ title: c.title, messages: c.messages, preset: c.preset ?? null, updatedAt: c.updatedAt }),
    });
  } catch {
    // ネットワーク不通等は無視（ブラウザ内には保存済み）
  }
}

export async function deleteServerConversation(id: string): Promise<void> {
  const headers = convHeaders();
  if (!headers) return;
  try {
    await fetch(`${CONV}/${encodeURIComponent(id)}`, { method: "DELETE", headers });
  } catch {
    // 無視
  }
}
