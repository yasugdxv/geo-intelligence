// Geopolitical Intelligence OS: Workflow Board APIクライアント。
// バックエンドは/api/geo/ios/workflow-board（geo_ios_workflow_board_api.py）に接続する。
import { API_BASE as API_BASE_URL } from "@/lib/apiBase";
import { useQuery } from "@tanstack/react-query";
import { getIosAuthHeaders } from "@/lib/geoIosAuthHeader";

const API_BASE = API_BASE_URL;

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers: { ...getIosAuthHeaders(), ...init?.headers } });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `API error ${res.status}`);
  }
  return res.json();
}

export interface GeoIosBoardCase {
  analysis_session_id: string;
  title: string;
  theme: string | null;
  user_id: string;
  created_at: string;
  report_project_id: string | null;
  latest_review_cycle_id: string | null;
  latest_review_reviewer: string | null;
  latest_review_result: "approved" | "revision_requested" | "rejected" | null;
  has_open_review_cycle: boolean;
  approval_status: "approved" | "rejected" | null;
  publication_status: "not_published" | "pending" | "published" | null;
  has_design: boolean;
  has_report_project: boolean;
  // IOS Native Draft: report_generation_modeが唯一のSource of Truth。
  report_generation_mode: "native" | "report_studio";
  has_native_draft: boolean;
  has_ai_review_completed: boolean;
}

export function useWorkflowBoard() {
  return useQuery({
    queryKey: ["geoIosWorkflowBoard"],
    queryFn: () => fetchJson<{ cases: GeoIosBoardCase[] }>("/api/geo/ios/workflow-board"),
  });
}

export interface GeoIosHomeTask {
  analysis_session_id: string;
  title: string | null;
  reason: "revision_requested" | "approval_pending";
}

export interface GeoIosHomeSummary {
  metrics: {
    in_progress: number;
    needs_attention: number;
    in_expert_review: number;
    approved: number;
  };
  tasks: GeoIosHomeTask[];
  resume: GeoIosBoardCase | null;
}

export function useWorkflowBoardHome() {
  return useQuery({
    queryKey: ["geoIosWorkflowBoardHome"],
    queryFn: () => fetchJson<GeoIosHomeSummary>("/api/geo/ios/workflow-board/home"),
  });
}
