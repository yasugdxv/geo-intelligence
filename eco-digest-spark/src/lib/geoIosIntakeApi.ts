// Geopolitical Intelligence OS: 前提整理（Intake）Chat + Analysis Context + Session Attachment
// APIクライアント。既存lib/geoIosAnalysisSessionApi.tsと同じ流儀（fetchJson等を独立して持つ、
// API_BASEのみ重複させる）で新規追加する。バックエンドは
// /api/geo/ios/analysis-sessions/{id}/intake/*（geo_ios_intake_api.py）にそのまま接続する。
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

// key_actors_detail: ユーザー指示（2026-09-24、Step2品質検証の結果を受けた改善①）。
// relevant_actors（機関・国家レベルの文字列配列）とは別軸で、会話・添付資料本文に
// 個人名・具体的役職が登場した場合のみAIが抽出するオブジェクト配列。
export interface GeoIosKeyActorDetail {
  name: string;
  role?: string | null;
  controls_or_leverage?: string | null;
  key_relationships?: string | null;
  constraints?: string | null;
  // notable_actions: 資料に明示的に書かれている、その人物の具体的な過去の行動・発言・反応と
  // その文脈（ユーザー指示、2026-09-24c）。専門家AIが行動パターンを判断するための素材であり、
  // 前提整理チャット側で性格・行動法則へ一般化しない（source-groundedな事実のみ保持する）。
  notable_actions?: string | null;
}

export interface GeoIosAnalysisContext {
  analysis_context_id: string;
  analysis_session_id: string;
  version: number;
  analysis_objective?: string | null;
  core_question?: string | null;
  current_situation?: string | null;
  business_context?: string | null;
  time_horizon?: string | null;
  geographic_scope?: string | null;
  relevant_actors: string[];
  key_actors_detail: GeoIosKeyActorDetail[];
  known_events: string[];
  user_assumptions: string[];
  must_consider_topics: string[];
  internal_context?: string | null;
  reference_materials: string[];
  constraints: string[];
  information_gaps: string[];
  ready_for_handoff: boolean;
  change_note?: string | null;
  created_by: string;
  created_at: string;
}

export interface GeoIosIntakeTurn {
  intake_turn_id: string;
  analysis_session_id: string;
  trigger: "user_message" | "attachment_added";
  user_message: string;
  assistant_message: string;
  attachment_ids: string[];
  resulting_context_version?: number | null;
  created_by: string;
  created_at: string;
}

export interface GeoIosSessionAttachment {
  attachment_id: string;
  analysis_session_id: string;
  filename: string;
  mime_type?: string | null;
  extraction_status: "ok" | "failed" | "unsupported";
  extraction_error?: string | null;
  uploaded_by: string;
  created_at: string;
}

// ---- Analysis Context ----------------------------------------------------------

export function useIntakeContext(sessionId: string) {
  return useQuery({
    queryKey: ["geoIosIntakeContext", sessionId],
    queryFn: () =>
      fetchJson<GeoIosAnalysisContext>(
        `/api/geo/ios/analysis-sessions/${sessionId}/intake/context`,
      ),
    enabled: !!sessionId,
    retry: false,
  });
}

// ---- Intake Turn ----------------------------------------------------------------

export function useIntakeTurns(sessionId: string) {
  return useQuery({
    queryKey: ["geoIosIntakeTurns", sessionId],
    queryFn: () =>
      fetchJson<{ turns: GeoIosIntakeTurn[] }>(
        `/api/geo/ios/analysis-sessions/${sessionId}/intake/messages`,
      ),
    enabled: !!sessionId,
  });
}

function invalidateIntakeQueries(qc: ReturnType<typeof useQueryClient>, sessionId: string) {
  qc.invalidateQueries({ queryKey: ["geoIosIntakeTurns", sessionId] });
  qc.invalidateQueries({ queryKey: ["geoIosIntakeContext", sessionId] });
}

export function useSendIntakeMessage(sessionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { user_message: string; actor_id: string }) =>
      postJson<{ turn: GeoIosIntakeTurn; context: GeoIosAnalysisContext }>(
        `/api/geo/ios/analysis-sessions/${sessionId}/intake/messages`,
        body,
      ),
    onSuccess: () => invalidateIntakeQueries(qc, sessionId),
  });
}

// ---- Session Attachment ----------------------------------------------------------

export function useIntakeAttachments(sessionId: string) {
  return useQuery({
    queryKey: ["geoIosIntakeAttachments", sessionId],
    queryFn: () =>
      fetchJson<{ attachments: GeoIosSessionAttachment[] }>(
        `/api/geo/ios/analysis-sessions/${sessionId}/intake/attachments`,
      ),
    enabled: !!sessionId,
  });
}

// sessionId未確定のまま（Session作成直後、hookのライフサイクル外）でも呼べるよう、
// 素のasync関数として切り出す。Intakeページ本体のuseUploadIntakeAttachmentはこれを
// 呼ぶだけの薄いラッパー（ロジックを複製しない）。
export function uploadIntakeAttachment(
  sessionId: string,
  file: File,
  actorId: string,
): Promise<{
  attachment: GeoIosSessionAttachment;
  turn: GeoIosIntakeTurn | null;
  context: GeoIosAnalysisContext | null;
}> {
  const form = new FormData();
  form.append("file", file);
  form.append("actor_id", actorId);
  return fetchJson(`/api/geo/ios/analysis-sessions/${sessionId}/intake/attachments`, {
    method: "POST",
    body: form,
  });
}

export function useUploadIntakeAttachment(sessionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ file, actorId }: { file: File; actorId: string }) =>
      uploadIntakeAttachment(sessionId, file, actorId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoIosIntakeAttachments", sessionId] });
      invalidateIntakeQueries(qc, sessionId);
    },
  });
}
