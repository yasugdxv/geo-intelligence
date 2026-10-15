import { cn } from "@/lib/utils";

/**
 * StatTile — ラベル+大きな数値+任意の補足を表示するKPIタイル。
 * Home KPI行、Expert AI case-stat-grid、Intelligence Needsサマリーカード等で使用。
 * 値は呼び出し側が渡した実データのみ表示する（このコンポーネント自身は集計しない）。
 */
export function GeoStatTile({
  label,
  value,
  hint,
  className,
}: {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("geo-card px-4 py-3.5", className)}>
      <div className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground">{label}</div>
      <div className="geo-heading text-2xl text-foreground mt-1">{value}</div>
      {hint && <div className="text-xs text-muted-foreground mt-0.5">{hint}</div>}
    </div>
  );
}
