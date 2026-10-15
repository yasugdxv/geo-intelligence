/**
 * SignalBar — 指標(Indicator)の観測値等を表す横棒。
 *
 * 【重要】色は常に単色(teal)固定。指標の偏差量に応じて赤/amber/緑を自動で
 * 出し分けることはしない（「指標のアラートは人間設定のしきい値であり自動リスク判定
 * ではない」という既存原則、geo-indicator-status.tsx参照）。重大度の色分けが必要な
 * データは GeoStatusBadge（geo-badge.tsx）側で、人間/ルールが確定させた値にのみ使う。
 */
export function GeoSignalBar({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(100, value));
  return (
    <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
      <div className="h-full bg-geo-teal rounded-full" style={{ width: `${pct}%` }} />
    </div>
  );
}
