// Geopolitical Intelligence OS: Consultation Turns（切り口相談/調査方針/プレレビュー）
// APIクライアント。バックエンドは/api/geo/ios/consultation-turns
// （geo_ios_consultation_api.py）に接続する。各ターンはExpert AI呼び出し（実測1〜3分）を
// BackgroundTasksで実行するため、POST直後はstatus=pendingで返る——完了はポーリングで検知
// する（ページ離脱・再訪してもDBのstatusを見るだけで進捗が分かる、フロントの一時状態には
// 依存しない）。
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

export type GeoIosConsultationMode = "framing" | "research_direction" | "pre_review";

export interface GeoIosConsultationAnswer {
  one_line_assessment: string | null;
  what_changed: string | null;
  why_it_matters: string | null;
  what_to_watch_decide: string | null;
  review_findings_count: number;
}

export interface GeoIosConsultationTurn {
  consultation_turn_id: string;
  analysis_session_id: string;
  consultation_mode: GeoIosConsultationMode;
  user_message: string;
  draft_text: string | null;
  status: "pending" | "completed" | "failed";
  expert_request_id: string | null;
  expert_response_id: string | null;
  error_message: string | null;
  created_by: string;
  created_at: string;
  completed_at: string | null;
  answer?: GeoIosConsultationAnswer;
}

export function useConsultationTurns(sessionId: string, mode: GeoIosConsultationMode) {
  return useQuery({
    queryKey: ["geoIosConsultationTurns", sessionId, mode],
    queryFn: () =>
      fetchJson<{ turns: GeoIosConsultationTurn[] }>(
        `/api/geo/ios/consultation-turns?analysis_session_id=${sessionId}&consultation_mode=${mode}`,
      ),
    enabled: !!sessionId,
    // pendingなターンが1件でもあればポーリングを続け、無ければ止める
    // （常時ポーリングして無駄な負荷をかけない）。
    refetchInterval: (query) => {
      const turns = query.state.data?.turns ?? [];
      return turns.some((t) => t.status === "pending") ? 4000 : false;
    },
  });
}

export function useCreateConsultationTurn(sessionId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      consultation_mode: GeoIosConsultationMode; user_message: string;
      draft_text?: string | null; created_by: string;
    }) =>
      fetchJson<GeoIosConsultationTurn>("/api/geo/ios/consultation-turns", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ analysis_session_id: sessionId, ...body }),
      }),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({
        queryKey: ["geoIosConsultationTurns", sessionId, variables.consultation_mode],
      });
    },
  });
}
