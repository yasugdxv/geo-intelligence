import { GeoObservation } from "@/lib/api";
import { useLanguage } from "@/lib/i18n";

// 指標の状態は「データ品質」（未取得/モック/実データ）のみを色で示す。指標の閾値は
// 「案・校正前」（geo_indicator_rule_versions.is_draft=true）であり、LLM/コードが
// 重大性を判定しているわけではないため、危険度を示すred/amber/green等の判定色は使わない
// （指示書の絶対原則3・7.1に基づく）。増減の向きは色ではなく▲▼と数値で示す。
export type IndicatorDataStatus = "insufficient" | "mock" | "live";

export function deriveIndicatorStatus(obs: GeoObservation | undefined | null): IndicatorDataStatus {
  if (!obs || obs.insufficientData) return "insufficient";
  if (obs.isMock) return "mock";
  return "live";
}

export const STATUS_DOT_CLASS: Record<IndicatorDataStatus, string> = {
  insufficient: "bg-muted-foreground/30",
  mock: "bg-accent-sky",
  live: "bg-accent-leaf",
};

export function IndicatorStatusDot({ status }: { status: IndicatorDataStatus }) {
  const { t } = useLanguage();
  return (
    <span
      className={`inline-block h-2 w-2 rounded-full shrink-0 ${STATUS_DOT_CLASS[status]}`}
      title={t(`geo.status.${status}`)}
    />
  );
}

export function DeviationIndicator({ deviation }: { deviation: number | null }) {
  if (deviation === null || Math.abs(deviation) < 0.05) return null;
  const up = deviation > 0;
  return (
    <span className={`text-xs tabular-nums ${up ? "text-accent-earth" : "text-accent-sky"}`}>
      {up ? "▲" : "▼"} {Math.abs(deviation).toFixed(1)}
    </span>
  );
}
