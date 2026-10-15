import { Badge } from "@/components/ui/badge";
import { GeoIndicatorAlert, GeoIndicatorFreshness } from "@/lib/api";
import { useLanguage } from "@/lib/i18n";

const FRESHNESS_LABEL_JA: Record<GeoIndicatorFreshness["status"], string> = {
  current: "最新", review_due_soon: "更新目安間近", overdue: "更新遅延", unknown: "鮮度不明",
};
const FRESHNESS_LABEL_EN: Record<GeoIndicatorFreshness["status"], string> = {
  current: "Current", review_due_soon: "Review due soon", overdue: "Overdue", unknown: "Unknown",
};
const FRESHNESS_VARIANT: Record<GeoIndicatorFreshness["status"], "secondary" | "outline" | "destructive"> = {
  current: "secondary", review_due_soon: "outline", overdue: "destructive", unknown: "outline",
};

export function IndicatorFreshnessBadge({ freshness }: { freshness: GeoIndicatorFreshness }) {
  const { lang } = useLanguage();
  const label = (lang === "en" ? FRESHNESS_LABEL_EN : FRESHNESS_LABEL_JA)[freshness.status];
  return (
    <Badge variant={FRESHNESS_VARIANT[freshness.status]} title={
      freshness.days_since_last_observation != null
        ? `${freshness.days_since_last_observation}日前の観測値`
        : undefined
    }>
      {label}
    </Badge>
  );
}

// Alertの重大性は「指標の計算方式・データ品質」ではなく、人がgeo_indicator_rule_versions.
// rule_definition.thresholdsへ明示的に設定した閾値との比較結果である（決定的にコードが判定、
// LLM推測ではない）。既存のgeo-indicator-status.tsxが危険度色を使わない方針（指示書の絶対
// 原則3・7.1、未校正ルールの重大性を安易に色で断定しない）を踏まえ、このバッジは「人が明示的に
// 設定した閾値に基づくアラート」であることが分かるようラベルを明記し、データ品質ドットとは
// 視覚的に区別する（別コンポーネント・別色系統）。
export function IndicatorAlertBadge({ alert }: { alert: GeoIndicatorAlert | null }) {
  if (!alert) return null;
  return (
    <Badge variant={alert.severity === "critical" ? "destructive" : "outline"}
           title="人が設定した閾値との比較によるアラート（自動リスク判定ではありません）">
      {alert.severity === "critical" ? "Critical" : "Warning"}
    </Badge>
  );
}
