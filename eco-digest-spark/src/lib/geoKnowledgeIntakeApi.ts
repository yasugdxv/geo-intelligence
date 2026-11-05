// ナレッジ追加（チャットで依頼 → AIが調査・登録 → 人が確認）のAPIクライアント。
// バックエンドは /api/geo/knowledge-intake/*（geo_knowledge_intake_api.py）。権限チェックはサーバー側で行う。
import { API_BASE } from "@/lib/apiBase";
import { ensureIosIdentity, getIosAuthHeaders } from "@/lib/geoIosAuthHeader";

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  await ensureIosIdentity(); // 画面を開いた直後でも、資格情報（またはSSO）が決まってから呼ぶ
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...getIosAuthHeaders(), ...init?.headers },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `API error ${res.status}`);
  }
  return res.json();
}

const postJson = <T>(path: string, body: unknown) =>
  fetchJson<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

export interface IntakeCard {
  name_en: string | null;
  name_ja: string | null;
  entity_type: string | null;
  country_code: string | null;
  focus: string | null;
}

export interface IntakeDuplicate {
  entity_id: string;
  entity_type: string;
  canonical_name: string;
  name_ja?: string | null;
  profile_item_id: string | null;
  profile_status: string | null;
}

export interface IntakeChatResponse {
  reply: string;
  ready: boolean;
  card: IntakeCard;
  duplicates: IntakeDuplicate[];
}

export interface IntakeSource {
  n: number;
  url: string;
  title: string | null;
  date: string | null;
}

export interface IntakeJob {
  job_id: string;
  status: "running" | "done" | "error";
  stage: string;
  error: string | null;
  result: {
    knowledge_item_id: string;
    title: string;
    entity_created: boolean;
    sources: IntakeSource[];
    review: { approved: boolean; reason: string; reasons: string[] } | null;
  } | null;
}

export interface IntakeItem {
  knowledge_item_id: string;
  title: string;
  status: string;
  confidence: string | null;
  created_at: string | null;
  created_by: string;
  approval: "human" | "ai" | "none";
  approved_by: string | null;
  objectives: string[];
  core_interests: string[];
  sources: IntakeSource[];
}

export const EMPTY_CARD: IntakeCard = {
  name_en: null,
  name_ja: null,
  entity_type: null,
  country_code: null,
  focus: null,
};

export const intakeChat = (messages: { role: string; content: string }[], card: IntakeCard) =>
  postJson<IntakeChatResponse>("/api/geo/knowledge-intake/chat", { messages, card });

export const startIntakeJob = (card: IntakeCard) =>
  postJson<{ job_id: string }>("/api/geo/knowledge-intake/jobs", { card });

export const getIntakeJob = (jobId: string) =>
  fetchJson<IntakeJob>(`/api/geo/knowledge-intake/jobs/${jobId}`);

export const listIntakeItems = () =>
  fetchJson<{ items: IntakeItem[] }>("/api/geo/knowledge-intake/items");

export const confirmAiApproval = (itemId: string, note: string) =>
  postJson<unknown>(`/api/geo/knowledge/items/${itemId}/confirm-ai-approval`, { note });
