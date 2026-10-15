import { cn } from "@/lib/utils";
import { Badge, type BadgeProps } from "@/components/ui/badge";

/**
 * GeoStatusBadge — 人間/ルールが設定したカテゴリカルなフィールド（Intelligence Needsの
 * priority、Caseのstatus等）専用の色分けバッジ。
 *
 * 【重要】指標(Indicator)の偏差・しきい値のような「AIやコードが自動算出した値」には
 * 絶対に使わないこと（geo-indicator-status.tsx/geo-freshness-badge.tsxの既存原則:
 * 指標のアラートは人間設定のしきい値であり自動リスク判定ではない、という明文化された
 * 方針に反するため）。severityは必ず人間/業務ルールが既に確定させた値にのみ適用する。
 */
export type GeoSeverity = "high" | "elevated" | "moderate" | "low" | "neutral" | "ai";

const SEVERITY_CLASSES: Record<GeoSeverity, string> = {
  high: "border-transparent bg-geo-red-soft text-geo-red",
  elevated: "border-transparent bg-geo-amber-soft text-geo-amber",
  moderate: "border-transparent bg-geo-teal-soft text-geo-teal",
  low: "border-transparent bg-geo-green-soft text-geo-green",
  neutral: "border-transparent bg-muted text-muted-foreground",
  ai: "border-transparent bg-geo-violet-soft text-geo-violet",
};

// Intelligence Needsのpriority(high/medium/low)からGeoSeverityへのマッピング
// （geo.intelligence-needs.tsxの旧PRIORITY_VARIANTをここへ集約）
export const PRIORITY_SEVERITY: Record<string, GeoSeverity> = {
  high: "high",
  medium: "elevated",
  low: "low",
};

export function GeoStatusBadge({
  severity,
  className,
  ...props
}: BadgeProps & { severity: GeoSeverity }) {
  return (
    <Badge
      variant="outline"
      className={cn(SEVERITY_CLASSES[severity], className)}
      {...props}
    />
  );
}
