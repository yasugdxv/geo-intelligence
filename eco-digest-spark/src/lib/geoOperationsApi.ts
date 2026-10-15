// Phase C: インテリジェンス運用（Pipeline Health）のAPIクライアント。
// 表示用のSummaryを1本で取得するだけ（LLMは呼ばない）。Theme同期の実行は既存の
// geoThemeCandidateApi（sync-cluster / sync-pending）をそのまま使う。
import { API_BASE as API_BASE_URL } from "@/lib/apiBase";
import { useQuery } from "@tanstack/react-query";
import { getIosAuthHeaders } from "@/lib/geoIosAuthHeader";

const API_BASE = API_BASE_URL;

export type OpsLevel = "ok" | "attention" | "stopped" | "unknown";

export interface OpsState {
  level: OpsLevel;
  label: string;
  detail: string;
}

export interface OpsAction {
  key: string;
  severity: "high" | "medium" | "low";
  count: number;
  title: string;
  detail: string;
  cta: { kind: "link" | "anchor" | "action"; label: string; to: string };
}

export interface OpsReviewItem {
  episode_cluster_id: string;
  actor: string;
  regions: string[];
  episode_type: string;
  evidence_count: number;
  last_event_date: string | null;
  waiting_since: string | null;
}

export interface OpsReview {
  total_clusters: number;
  by_status: Record<string, number>;
  pending: number;
  accepted: number;
  rejected: number;
  oldest_waiting_at: string | null;
  oldest_waiting_days: number | null;
  items: OpsReviewItem[];
  last_review: { reviewed_at: string; reviewed_by: string | null; status: string } | null;
}

export interface OpsPendingSync {
  cluster_ids: string[];
  source_type: "single_cluster" | "situation_component" | null;
  actors: string[];
  regions: string[];
  evidence_count: number;
  latest_signal_at: string | null;
  retry: boolean;
  last_error: string | null;
  last_error_at: string | null;
}

export interface OpsSyncRun {
  run_id: string;
  started_at: string;
  completed_at: string | null;
  status: string;
  mode: "single" | "batch" | null;
  requested_by: string | null;
  created: number;
  merged: number;
  no_candidate: number;
  failed: number;
  error_summary: string | null;
}

export interface OpsThemeSync {
  pending_count: number;
  retry_count: number;
  pending: OpsPendingSync[];
  accepted_not_eligible: number;
  recent_runs: OpsSyncRun[];
}

export interface OpsThemeCandidates {
  total: number;
  active: number;
  by_status: Record<string, number>;
  by_origin: Record<string, number>;
  new_recent: number;
  recent_days: number;
}

export interface OpsAlert {
  alert_id: string;
  monitoring_theme_id: string;
  theme_name: string | null;
  severity: "low" | "medium" | "high" | null;
  title: string | null;
  reason: string | null;
  detected_at: string | null;
}

export interface OpsFreshness {
  status: string;
  last_data_at: string | null;
  days_since: number | null;
  label: string;
}

export interface OpsMonitoring {
  active_themes: number;
  unacknowledged_alerts: number;
  high_alerts: number;
  alerts: OpsAlert[];
  data_freshness: OpsFreshness;
}

export interface OpsCollection {
  data_freshness: OpsFreshness;
  auto_sources: number;
  fresh_sources: number;
  never_run_sources: number;
  problem_sources: Array<{
    kind: "stale" | "failed";
    name: string;
    source_id: string;
    last_success_at: string | null;
    hours_since_success: number | null;
    failure_count: number;
  }>;
  running: Array<{
    connector: string;
    started_at: string;
    hours_running: number | null;
    too_long: boolean;
    stale?: boolean;
  }>;
  failed_runs: Array<{ connector: string; at: string; message: string | null }>;
  scheduler: OpsScheduler;
  stale_running?: Array<{
    job_id: string;
    connector_type: string;
    ingestion_run_id: string;
    started_at: string;
    running_hours: number;
    stale_after_hours: number;
  }>;
}

export type OpsJobState =
  | "scheduled"
  | "due"
  | "running"
  | "stale"
  | "backoff"
  | "exhausted"
  | "blocked"
  | "not_configured"
  | "disabled";

export interface OpsSchedulerJob {
  job_id: string;
  group: string | null;
  cadence: string | null;
  state: OpsJobState;
  reason: string;
  last_success_at: string | null;
  next_due_at: string | null;
  consecutive_failures: number;
  retry_classification: string | null;
  next_retry_at: string | null;
  dedicated: boolean;
}

export interface OpsScheduler {
  configured: boolean;
  healthy?: boolean;
  status?: "healthy" | "unhealthy" | "not_configured";
  last_heartbeat_at?: string | null;
  heartbeat_age_minutes?: number | null;
  healthy_within_minutes?: number;
  last_run_at: string | null;
  last_any_run_at: string | null;
  next_expected_at: string | null;
  note: string;
  jobs?: OpsSchedulerJob[];
  gdelt_doc?: {
    cooldown_remaining_seconds: number;
    last_request_age_seconds: number | null;
    last_429_age_seconds: number | null;
    collection_running: boolean;
    min_interval_seconds: number;
  } | null;
}

export interface OperationsSummary {
  version: string;
  generated_at: string;
  health: Record<"collection" | "episode" | "review" | "theme_sync" | "monitoring", OpsState>;
  action_queue: OpsAction[];
  daily: {
    review_pending: number | null;
    theme_sync_pending: number | null;
    new_themes: number | null;
    unacknowledged_alerts: number | null;
    stale_sources: number | null;
  };
  review: OpsReview | null;
  theme_sync: OpsThemeSync | null;
  theme_candidates: OpsThemeCandidates | null;
  monitoring: OpsMonitoring | null;
  collection: OpsCollection | null;
  errors: Record<string, string>;
}

export const OPERATIONS_SUMMARY_KEY = ["geoOperationsSummary"] as const;

export function useOperationsSummary() {
  return useQuery({
    queryKey: OPERATIONS_SUMMARY_KEY,
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/geo/operations/summary`, {
        headers: { ...getIosAuthHeaders() },
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `API error ${res.status}`);
      }
      return (await res.json()) as OperationsSummary;
    },
    refetchInterval: 60000, // 重い処理は無い（DBの状態集計のみ）。1分ごとに自動更新
  });
}
