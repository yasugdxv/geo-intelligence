// Source Selector →「情報ソースDBで詳しく探す」→ Source Detail →「このソースをテーマ探索に追加」で
// 元の画面（Theme Explorer / Monitoring / Research Studio）のSource Setへ戻すための一時Store。
// sessionStorageのみ使用（サーバー保存しない）。使えない環境では何もしない（機能が無効になるだけ）。
const KEY = "geo_source_pick_context";

export interface SourcePickContext {
  /** 戻り先のパス（例: /geo/theme-discovery） */
  returnTo: string;
  /** 戻り先でSource Selectorを再度開く対象（Theme候補ID等）。無ければ開かない */
  contextId?: string;
  /** 表示用ラベル（例: 「米中半導体規制」のSource選択中） */
  label: string;
  /** これまでに選択済みのsource_id */
  selectedIds: string[];
}

export function getPickContext(): SourcePickContext | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as SourcePickContext) : null;
  } catch {
    return null;
  }
}

export function setPickContext(ctx: SourcePickContext): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(ctx));
  } catch {
    // sessionStorageが使えない環境では選択モードを開始しない
  }
}

export function addPick(sourceId: string): SourcePickContext | null {
  const ctx = getPickContext();
  if (!ctx) return null;
  if (!ctx.selectedIds.includes(sourceId)) ctx.selectedIds = [...ctx.selectedIds, sourceId];
  setPickContext(ctx);
  return ctx;
}

export function clearPickContext(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // noop
  }
}
