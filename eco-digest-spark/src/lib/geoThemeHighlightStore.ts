// Phase C §8: Episode Cluster ReviewでTheme Candidateを生成した直後、「テーマ探索で確認」から
// 遷移した際にその候補を直接開けるようにする一時Store。sourcePickStore.tsと同じ設計
// （sessionStorageのみ、使えない環境では何もしない）。
const KEY = "geo_theme_highlight_candidate_id";

export function setHighlightCandidateId(candidateId: string): void {
  try {
    sessionStorage.setItem(KEY, candidateId);
  } catch {
    // sessionStorageが使えない環境では単に通常表示になるだけ
  }
}

export function consumeHighlightCandidateId(): string | null {
  try {
    const id = sessionStorage.getItem(KEY);
    if (id) sessionStorage.removeItem(KEY);
    return id;
  } catch {
    return null;
  }
}
