// Intelligence AI「会話モード」API クライアント（/api/geo/conversation-mode）。
// 会話の状態(state)とメッセージは画面が持ち、毎回サーバーへ送る（既存の /chat と同じステートレス方式）。
// ログイン中はサーバー（既存の会話保存テーブル）にも保存される。未ログイン・テーブル未作成のときはブラウザ内のみ
// （サーバーが persistence.status = "local_only" で知らせる）。通常チャットの履歴とは別のlocalStorageキーを使う。
import { useQuery } from "@tanstack/react-query";
import { API_BASE } from "@/lib/apiBase";
import { getIosAuthHeaders, getIosCredential } from "@/lib/geoIosAuthHeader";

const BASE = "/api/geo/conversation-mode";
const KEY = "verdant_geo_conversation_mode_v1";
const MAX_LOCAL = 30;
/** 通常チャットの履歴と見分けるための会話ID接頭辞（バックエンドの CONVERSATION_ID_PREFIX と同じ） */
export const CM_ID_PREFIX = "cm_";

export type CmModeId = "gal_oyaji" | "consult_partner" | "stakeholder";
export type CmAction =
  "ask" | "continue" | "summarize" | "pause" | "resume" | "refresh" | "research";
export type CmBasis = "evidence" | "inference" | "assumption" | "question" | "unknown";

export interface CmModeInfo {
  id: CmModeId;
  name: string;
  tagline: string;
  icon: string;
}
export interface CmChip {
  id: string;
  label: string;
}
export interface CmConfig {
  enabled: boolean;
  web_search_enabled?: boolean;
  knowledge_enabled?: boolean;
  modes?: CmModeInfo[];
  chips?: CmChip[];
  limits?: {
    max_utterances_per_op: number;
    max_llm_calls: number;
    max_messages: number;
    max_user_chars: number;
    /** 上限の性質の注意書き（サーバー側・プロセス内の概算であることなど） */
    note?: string;
  };
  simulation_notice?: string;
}

export interface CmMessage {
  role: "user" | "assistant";
  kind?: "user" | "utterance" | "summary";
  speaker_id?: string;
  speaker?: string;
  role_label?: string;
  content: string;
  basis?: CmBasis;
  ref_ids?: string[];
  ungrounded?: boolean;
  caveat?: string | null;
  simulation?: boolean;
  chip?: string | null;
  /** 権限が変更されたため、サーバーが本文を伏せ字にした発言 */
  redacted?: boolean;
  ts: string;
}

export interface CmEvidenceSource {
  ref_id: string;
  title?: string | null;
  date?: string | null;
  source_type?: string | null;
  review_status?: string | null;
}
export interface CmWeb {
  status: string;
  reason?: string | null;
  checked_at?: string | null;
  /** Web検索AIによる要約（複数ページのまとめ・未検証）。出典番号は WS */
  summary?: string | null;
  /** 外部へ送った検索語 */
  query?: string | null;
  /** ページ。出典番号は W1, W2… */
  items: { ref_id?: string; title?: string; url?: string; date?: string | null }[];
}
/** Expert AIの基盤知識（承認済みのナレッジ。先例・人物や制度のプロファイルなど）。出典番号は K1… */
export interface CmKnowledge {
  ref_id: string;
  title?: string;
  kind?: string;
  text?: string;
  reason?: string;
}
export interface CmEvidence {
  summary?: string;
  summary_withdrawn?: boolean;
  /** 社内資料の検索に使った語と、その語で当たった件数 */
  query_used?: string;
  search_hits?: number;
  insufficient?: boolean;
  as_of?: string;
  warnings?: string[];
  findings?: { claim: string; claim_type?: string; source_refs: string[] }[];
  sources?: CmEvidenceSource[];
  web?: CmWeb;
  knowledge?: CmKnowledge[];
  /** Expert AIの基盤文脈（この状況の過去の経緯）。出典番号は F1 */
  foundation?: { ref_id: string; text: string } | null;
  /** 裏での取得（Web調査・基盤知識）の状態: pending=取得中 / done=取り込み済み / unavailable=取り込めない */
  enrichment?: "pending" | "done" | "unavailable";
}
export interface CmLedgerItem {
  text: string;
  speaker?: string;
  ref_ids?: string[];
  simulated?: boolean;
}
export interface CmStakeholder {
  id: string;
  name: string;
  grounding: "db_and_evidence" | "evidence" | "ai_inference";
  db_entity_id?: string | null;
}

export interface CmState {
  v: number;
  conversation_id: string;
  mode: CmModeId;
  theme: string;
  theme_source: "recommended" | "free";
  status: "active" | "paused";
  rev: number;
  evidence: CmEvidence;
  stakeholders: CmStakeholder[];
  ledger: { facts: CmLedgerItem[]; assumptions: CmLedgerItem[]; open_questions: CmLedgerItem[] };
  budget: { llm_calls: number; analyze_calls: number; web_calls: number };
  history_cutoff?: number;
  last_speaker: string | null;
  parent_id: string | null;
}
export interface CmPersistence {
  status: "saved" | "local_only";
  reason: string | null;
}
/** 毎リクエストの権限の再確認の結果（取り消された根拠の件数。題名などは含まない） */
export interface CmAccess {
  checked: boolean;
  revoked_count: number;
  revoked_refs: string[];
}
export interface CmResult {
  access?: CmAccess;
  state: CmState;
  messages: CmMessage[];
  new_messages: CmMessage[];
  persistence: CmPersistence;
}
export interface CmCarry {
  facts: CmLedgerItem[];
  open_questions: CmLedgerItem[];
  evidence: CmEvidence;
  theme: string;
  parent_id: string;
}
export interface CmTheme {
  title: string;
  source: "monitoring_themes" | "fallback";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...getIosAuthHeaders(), ...init?.headers },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const err = new Error(
      typeof body.detail === "string" ? body.detail : `API error ${res.status}`,
    ) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  return res.json();
}

/** モード選択を出すかどうか。無効・通信失敗のときは enabled=false 扱い（通常モードだけの従来画面）。 */
export function useConversationModeConfig() {
  return useQuery<CmConfig>({
    queryKey: ["geo-conversation-mode-config"],
    queryFn: async () => {
      try {
        return await request<CmConfig>("/config");
      } catch {
        return { enabled: false };
      }
    },
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
}

/** ストリーミング（NDJSON）で届くイベント。できた発言から順に届き、最後に done（通常のAPIと同じ結果）が届く。 */
export type CmStreamEvent =
  | { type: "status"; text: string }
  | { type: "speaker"; speaker: string }
  | { type: "speakers"; speakers: string[] }
  | { type: "utterance"; message: CmMessage }
  | { type: "done"; result: CmResult }
  | { type: "error"; status: number; detail: string };

async function streamRequest(
  path: string,
  body: unknown,
  onEvent: (e: CmStreamEvent) => void,
): Promise<CmResult> {
  const res = await fetch(`${API_BASE}${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getIosAuthHeaders() },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const e = new Error(
      typeof err.detail === "string" ? err.detail : `API error ${res.status}`,
    ) as Error & { status?: number };
    e.status = res.status;
    throw e;
  }
  if (!res.body) throw new Error("このブラウザは、発言を順に受け取る方式に対応していません");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let result: CmResult | null = null;
  const handle = (line: string) => {
    if (!line.trim()) return;
    const ev = JSON.parse(line) as CmStreamEvent;
    if (ev.type === "error") {
      const e = new Error(ev.detail) as Error & { status?: number };
      e.status = ev.status;
      throw e;
    }
    if (ev.type === "done") result = ev.result;
    onEvent(ev);
  };
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let i = buf.indexOf("\n");
    while (i >= 0) {
      handle(buf.slice(0, i));
      buf = buf.slice(i + 1);
      i = buf.indexOf("\n");
    }
  }
  handle(buf);
  if (!result) throw new Error("会話の応答が途中で切れました。もう一度お試しください");
  return result;
}

export const fetchCmThemes = (mode: CmModeId) =>
  request<{ themes: CmTheme[]; source: string }>("/themes", {
    method: "POST",
    body: JSON.stringify({ mode }),
  });

export const startCm = (body: {
  mode: CmModeId;
  theme: string;
  theme_source: "recommended" | "free";
  carry?: CmCarry | null;
}) => request<CmResult>("/start", { method: "POST", body: JSON.stringify(body) });

export const turnCm = (body: {
  state: CmState;
  messages: CmMessage[];
  action: CmAction;
  text?: string;
  chip?: string;
  addressee?: string;
}) => request<CmResult>("/turn", { method: "POST", body: JSON.stringify(body) });

/** 通常チャットのサーバー履歴に混ざっている会話モードの行を見分ける（通常チャット側で除外するのに使う）。 */
export function isConversationModeRecord(r: { id: string; preset?: unknown }): boolean {
  if (typeof r.id === "string" && r.id.startsWith(CM_ID_PREFIX)) return true;
  const p = r.preset as { kind?: string } | null | undefined;
  return !!p && typeof p === "object" && p.kind === "conversation_mode";
}

// ---- ブラウザ内の会話履歴（通常チャットとは別キー） ----
export interface CmStored {
  id: string;
  title: string;
  updatedAt: string;
  state: CmState;
  messages: CmMessage[];
}

export function listCmLocal(): CmStored[] {
  try {
    const raw = localStorage.getItem(KEY);
    const list: CmStored[] = raw ? JSON.parse(raw) : [];
    return [...list].sort((a, b) => +new Date(b.updatedAt) - +new Date(a.updatedAt));
  } catch {
    return [];
  }
}

export function saveCmLocal(state: CmState, messages: CmMessage[], title: string): void {
  const list = listCmLocal().filter((c) => c.id !== state.conversation_id);
  list.unshift({
    id: state.conversation_id,
    title,
    updatedAt: new Date().toISOString(),
    state,
    messages,
  });
  try {
    localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX_LOCAL)));
  } catch {
    // 容量超過・利用不可のときは保存を諦める（会話自体は継続できる）
  }
}

export type CmLocalDeleteResult = "deleted" | "not_found" | "error";

/**
 * ブラウザ内の会話コピーを、IDで指定した1件だけ削除する（キー全体・他の会話・通常チャットの履歴には触れない）。
 * 保存内容が読めない・配列でないときは、何も書き換えずに "error" を返す（壊れたデータを空で上書きしない）。
 * 該当が無いときは書き込まない（"not_found"）。書き込んだあと、実際に消えたかを読み直して確かめる。
 */
export function deleteCmLocal(id: string): CmLocalDeleteResult {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return "not_found";
    const list: unknown = JSON.parse(raw);
    if (!Array.isArray(list)) return "error";
    const next = list.filter(
      (c) => !(c && typeof c === "object" && (c as { id?: string }).id === id),
    );
    if (next.length === list.length) return "not_found";
    localStorage.setItem(KEY, JSON.stringify(next));
    const after: unknown = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    const stillThere =
      Array.isArray(after) &&
      after.some((c) => c && typeof c === "object" && (c as { id?: string }).id === id);
    return stillThere ? "error" : "deleted";
  } catch {
    return "error";
  }
}

export type CmServerDeleteStatus =
  "deleted" | "not_found" | "unavailable" | "login_required" | "error";
export interface CmServerDeleteResult {
  status: CmServerDeleteStatus;
  /** サーバーが返した文言（会話の中身は含まない） */
  detail?: string;
}

/**
 * サーバー上の自分の会話を削除する（既存の DELETE /api/geo/intelligence-ai/conversations/{id} を再利用）。
 * 結果を区別して返す（削除できたかのように見せない）:
 *   deleted=削除した / not_found=該当なし（無い・他人のもの。サーバーは区別しない） /
 *   unavailable=サーバー保存が利用できない（保存先が未作成など） / login_required=未ログインのため送っていない / error=失敗
 */
export async function deleteCmServer(id: string): Promise<CmServerDeleteResult> {
  if (!getIosCredential()) return { status: "login_required" };
  try {
    const res = await fetch(
      `${API_BASE}/api/geo/intelligence-ai/conversations/${encodeURIComponent(id)}`,
      {
        method: "DELETE",
        headers: { ...getIosAuthHeaders() },
      },
    );
    if (res.ok) return { status: "deleted" };
    const body = await res.json().catch(() => ({}));
    const detail = typeof body.detail === "string" ? body.detail : undefined;
    if (res.status === 404) return { status: "not_found", detail };
    if (res.status === 503) return { status: "unavailable", detail };
    if (res.status === 401) return { status: "login_required", detail };
    return { status: "error", detail };
  } catch {
    return { status: "error" };
  }
}

/** サーバー保存の行（既存の会話保存API）から、会話モードの会話だけ取り出す。 */
export function storedFromServer(r: {
  id: string;
  title: string;
  updatedAt: string;
  messages: unknown[];
  preset?: unknown;
}): CmStored | null {
  const p = r.preset as { kind?: string; state?: CmState } | null | undefined;
  if (!p || p.kind !== "conversation_mode" || !p.state) return null;
  return {
    id: r.id,
    title: r.title,
    updatedAt: r.updatedAt,
    state: p.state,
    messages: r.messages as CmMessage[],
  };
}

/** startCm と同じ処理を、できた発言から順に onEvent へ渡しながら実行する。 */
export const startCmStream = (
  body: {
    mode: CmModeId;
    theme: string;
    theme_source: "recommended" | "free";
    carry?: CmCarry | null;
    /** Web調査に同意するか（テーマの題名を、外部のWeb検索に送る） */
    with_web?: boolean;
  },
  onEvent: (e: CmStreamEvent) => void,
) => streamRequest("/start/stream", body, onEvent);

/** turnCm と同じ処理を、できた発言から順に onEvent へ渡しながら実行する。 */
export const turnCmStream = (
  body: {
    state: CmState;
    messages: CmMessage[];
    action: CmAction;
    text?: string;
    chip?: string;
    addressee?: string;
  },
  onEvent: (e: CmStreamEvent) => void,
) => streamRequest("/turn/stream", body, onEvent);
