// Theme Discovery「本当に探索する」拡張のAPIクライアント（2026-10-07）。
// チャットで要件をすり合わせ → 既存の情報ソース推薦（useRecommendSources、新規APIなし）→
// Web検索つき探索実行。実行後のjob_idは既存のuseDiscoveryJob（geoSourceLibraryApi）で
// そのままポーリングできる。
import { API_BASE as API_BASE_URL } from "@/lib/apiBase";
import { useMutation } from "@tanstack/react-query";
import { getIosAuthHeaders } from "@/lib/geoIosAuthHeader";

const API_BASE = API_BASE_URL;

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getIosAuthHeaders() },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(typeof data.detail === "string" ? data.detail : `API error ${res.status}`);
  }
  return res.json();
}

export interface DiscoveryChatTurn {
  role: "user" | "assistant";
  content: string;
}

export interface DiscoveryBrief {
  keywords: string[];
  exclude_mainstream: boolean;
  region: string | null;
  period_days: number;
  summary: string;
  /** 主要アクター（専門家のガイドで確認した内容） */
  actors?: string[];
  /** 最も知りたい角度（アクターの意図/制度上の制約/先行指標など） */
  angle?: string | null;
}

export interface DiscoveryChatReply {
  assistant_reply: string;
  brief: DiscoveryBrief;
  ready: boolean;
}

export function useDiscoveryChat() {
  return useMutation({
    mutationFn: (body: { history: DiscoveryChatTurn[]; message: string }) =>
      post<DiscoveryChatReply>("/api/geo/theme-explorer/live/chat", body),
  });
}

export interface LiveDiscoveryWebResult {
  fetched: number;
  inserted: number;
  updated: number;
  errors: string[];
}

export interface LiveDiscoveryRunResult {
  job_id: string;
  web: LiveDiscoveryWebResult | null;
}

export function useStartLiveDiscovery() {
  return useMutation({
    mutationFn: (body: { brief: DiscoveryBrief; source_ids: string[]; include_web: boolean }) =>
      post<LiveDiscoveryRunResult>("/api/geo/theme-explorer/live/run", body),
  });
}
