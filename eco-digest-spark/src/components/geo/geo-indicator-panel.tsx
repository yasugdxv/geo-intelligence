import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { GeoIndicator, useGeoIndicatorObservations, useGeoRecalculateIndicator } from "@/lib/api";
import { useLanguage } from "@/lib/i18n";
import { GeoIndicatorTrendChart } from "./geo-indicator-trend-chart";
import { DeviationIndicator, deriveIndicatorStatus, IndicatorStatusDot } from "./geo-indicator-status";

// 「指標ステータス一覧」。以前はコンパクトなヒートマップ風グリッドと、下段の詳細カードに
// 内容が分かれていて同じ値を重複表示していたため、1つのカードに統合した。各カードには
// 指標の説明（何を測定し、台湾有事の評価においてこの数値が何を意味するか）を明記し、
// 単なる数値の羅列にならないようにする。色はデータ品質のみを示し、危険度の判定は
// しない（geo-indicator-status.tsxのコメント参照）。「詳細を見る」から、この指標の
// 根拠となった検知イベント・Evidence・文書を関連付けて見られる詳細ページへ遷移する。
function IndicatorPanelCard({ themeId, indicator }: { themeId: string; indicator: GeoIndicator }) {
  const { t } = useLanguage();
  const { data, isLoading } = useGeoIndicatorObservations(indicator.id);
  const observations = data?.observations ?? [];
  const obs = observations[0];
  const status = deriveIndicatorStatus(obs);
  const recalcMutation = useGeoRecalculateIndicator();

  return (
    <div className="card-paper rounded-lg p-5 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <IndicatorStatusDot status={status} />
          <h2 className="text-editorial text-base truncate">{indicator.code}: {indicator.name}</h2>
        </div>
        <Button
          size="sm" variant="outline"
          onClick={() => recalcMutation.mutate(indicator.id)}
          disabled={recalcMutation.isPending}
        >
          {recalcMutation.isPending ? t("geo.overview.recalculating") : t("geo.overview.recalcButton")}
        </Button>
      </div>

      {indicator.description && (
        <p className="text-xs text-muted-foreground leading-relaxed">{indicator.description}</p>
      )}

      {isLoading ? (
        <p className="text-sm text-muted-foreground">...</p>
      ) : !obs ? (
        <p className="text-sm text-muted-foreground">{t("geo.overview.noObservation")}</p>
      ) : obs.insufficientData ? (
        <Badge variant="secondary">{t("geo.overview.insufficientData")}</Badge>
      ) : (
        <div className="space-y-2">
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className="text-3xl font-semibold tabular-nums">
              {obs.qualitativeLabel ?? obs.rawValue}
            </span>
            {indicator.unit && !obs.qualitativeLabel && (
              <span className="text-xs text-muted-foreground">{indicator.unit}</span>
            )}
            <DeviationIndicator deviation={obs.deviation} />
            {obs.isMock && <Badge variant="outline">{t("geo.overview.mockBadge")}</Badge>}
          </div>
          <GeoIndicatorTrendChart observations={observations} />
          <div className="text-xs text-muted-foreground space-x-3 tabular-nums">
            {obs.baseline30d !== null && <span>baseline30d: {obs.baseline30d.toFixed(1)}</span>}
            {obs.confidence !== null && <span>confidence: {obs.confidence.toFixed(2)}</span>}
          </div>
          <Badge variant="outline" className="text-[10px]">{t("geo.overview.draftBadge")}</Badge>
        </div>
      )}

      <Link
        to="/geo/theme/$themeId/indicator/$indicatorId"
        params={{ themeId, indicatorId: indicator.id }}
        className="flex items-center gap-1 text-xs text-primary hover:underline w-fit"
      >
        {t("geo.overview.viewDetail")} <ChevronRight className="h-3.5 w-3.5" />
      </Link>
    </div>
  );
}

export function GeoIndicatorPanel({ themeId, indicators }: { themeId: string; indicators: GeoIndicator[] }) {
  const { t } = useLanguage();
  if (indicators.length === 0) return null;
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {indicators.map((i) => <IndicatorPanelCard key={i.id} themeId={themeId} indicator={i} />)}
      </div>
      <div className="flex items-center gap-4 text-[10px] text-muted-foreground">
        <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-accent-leaf inline-block" />{t("geo.status.live")}</span>
        <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-accent-sky inline-block" />{t("geo.status.mock")}</span>
        <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-muted-foreground/30 inline-block" />{t("geo.status.insufficient")}</span>
      </div>
    </div>
  );
}
