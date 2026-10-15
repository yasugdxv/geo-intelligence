import type { ChatMsg } from "@/lib/api";
import type { PresetContext, PresetId } from "@/lib/geoIntelligencePresetApi";

// 地政学専門家AIの会話履歴（ブラウザのlocalStorageのみに保存。バックエンドには送らない）。
// 既存のchatHistory.ts（サスティナAI用、キー verdant_chat_conversations）とは
// 別のlocalStorageキーを持つ独立した実装（会話履歴が混ざらないようにするため）。
export interface GeoChatConversation {
  id: string;
  title: string;
  themeId?: string;
  messages: ChatMsg[];
  updatedAt: string;
  // Intelligence AIプリセット調査の状態（会話を再開したとき、選択中のプリセット・調査条件・参照資料の
  // 参照IDを復元するため）。本文は持たず、参照IDと検索条件だけ。プリセットを使わない会話では無い。
  preset?: { presetId: PresetId | null; context: PresetContext | null };
}

const KEY = "verdant_geo_chat_conversations";
const MAX_CONVERSATIONS = 50;

export function listGeoConversations(): GeoChatConversation[] {
  try {
    const raw = localStorage.getItem(KEY);
    const list: GeoChatConversation[] = raw ? JSON.parse(raw) : [];
    return [...list].sort((a, b) => +new Date(b.updatedAt) - +new Date(a.updatedAt));
  } catch {
    return [];
  }
}

export function saveGeoConversation(conv: GeoChatConversation): void {
  const list = listGeoConversations().filter((c) => c.id !== conv.id);
  list.unshift(conv);
  try {
    localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX_CONVERSATIONS)));
  } catch {
    // localStorageが使えない/容量超過の場合は履歴保存を諦める（会話自体は継続できる）
  }
}

export function deleteGeoConversation(id: string): void {
  const list = listGeoConversations().filter((c) => c.id !== id);
  try {
    localStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    // noop
  }
}

export function makeGeoConversationId(): string {
  return `geo_conv_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

export function titleFromGeoMessages(messages: ChatMsg[]): string {
  const firstUser = messages.find((m) => m.role === "user");
  if (!firstUser) return "";
  const text = firstUser.content.trim();
  return text.length > 40 ? `${text.slice(0, 40)}…` : text;
}
