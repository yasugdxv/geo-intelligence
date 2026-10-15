// Phase E: Daily Intelligence Brief（「今日どこを見るべきか」）のAPIクライアント。
// 新しい分析AIは呼ばない。既存のOperations/Monitoring/Theme Candidate集計を束ねて返すだけ。
// 手動更新（refresh）はDB再取得のみで、Collectionの実行はトリガーしない。
import { API_BASE as API_BASE_URL } from "@/lib/apiBase";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getIosAuthHeaders } from "@/lib/geoIosAuthHeader";

const API_BASE = API_BASE_URL;

const LAST_SEEN_KEY = "geo_daily_brief_last_seen_at";

/** 前回Daily Briefを確認した時刻（ブラウザのlocalStorageのみ、サーバには保存しない）。 */
export function getLastSeenAt(): string | null {
  try {
    return localStorage.getItem(LAST_SEEN_KEY);
  } catch {
    return null;
  }
}

export function markSeenNow(): void {
  try {
    localStorage.setItem(LAST_SEEN_KEY, new Date().toISOString());
  } catch {
    // localStorageが使えない環境では、次回も「直近24時間」フォールバックになるだけ
  }
}

export interface BriefEvidence {
  title: string | null;
  url: string | null;
  source: string | null;
  published_at: string | null;
}

export interface BriefPoint {
  category: "alert" | "new_theme" | "theme_sync" | "review" | "operations";
  severity: "high" | "medium" | "low";
  text: string;
}

export interface BriefAlertItem {
  alert_id: string;
  monitoring_theme_id: string;
  theme_name: string | null;
  severity: "low" | "medium" | "high" | null;
  title: string | null;
  reason: string | null;
  detected_at: string | null;
  is_new_since_last_visit: boolean;
  related_theme_candidate_id: string | null;
  evidence: BriefEvidence[];
}

export interface BriefChanges {
  unacknowledged_count: number;
  high_count: number;
  items: BriefAlertItem[];
}

export interface BriefNewThemeItem {
  candidate_id: string;
  title: string;
  question: string | null;
  origin: "article_discovery" | "monitoring" | "manual" | "situation";
  origin_label: string;
  actors: string[];
  regions: string[];
  created_at: string | null;
  latest_signal_at: string | null;
  is_new_since_last_visit: boolean;
  evidence: BriefEvidence[];
}

export interface BriefNewThemes {
  total_new: number;
  by_origin: Record<string, number>;
  items: BriefNewThemeItem[];
  suppressed_as_duplicate: number;
}

export interface BriefReview {
  pending: number;
  high_evidence_count: number;
  oldest_waiting_days: number | null;
  summary_text: string;
}

export interface BriefThemeSync {
  pending: number;
  retry: number;
  summary_text: string;
}

export interface BriefOperationsNoticeItem {
  kind: string;
  detail: string | null;
}

export interface BriefOperationsNotice {
  has_issue: boolean;
  summary_text: string;
  items: BriefOperationsNoticeItem[];
}

export interface DailyBrief {
  version: string;
  generated_at: string;
  is_demo: boolean;
  window_since: string;
  window_is_fallback: boolean;
  points: BriefPoint[];
  manager_summary: string[];
  changes: BriefChanges;
  new_themes: BriefNewThemes;
  review: BriefReview;
  theme_sync: BriefThemeSync;
  operations_notice: BriefOperationsNotice;
  is_empty: boolean;
  fingerprint: string;
  errors: Record<string, string>;
}

export const DAILY_BRIEF_KEY = ["geoDailyBrief"] as const;

export function useDailyBrief() {
  return useQuery({
    queryKey: DAILY_BRIEF_KEY,
    queryFn: async () => {
      const since = getLastSeenAt();
      const url = new URL(`${API_BASE}/api/geo/daily-brief`);
      if (since) url.searchParams.set("since", since);
      const res = await fetch(url.toString(), { headers: { ...getIosAuthHeaders() } });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `API error ${res.status}`);
      }
      return (await res.json()) as DailyBrief;
    },
    refetchInterval: 60000,
  });
}

/** 手動更新ボタン用。DBを読み直すだけ（Collectionの実行トリガーではない）。 */
export function useRefreshDailyBrief() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: DAILY_BRIEF_KEY });
}
