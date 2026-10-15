import { Button } from "@/components/ui/button";
import { useGeoCrawlOfficialSources, useGeoIndicators, useGeoOverview } from "@/lib/api";
import { useLanguage } from "@/lib/i18n";
import { GeoIndicatorPanel } from "./geo-indicator-panel";

export function GeoOverviewTab({ themeId }: { themeId: string }) {
  const { t } = useLanguage();
  const { data: overview } = useGeoOverview(themeId);
  const { data: indicatorsData, isLoading } = useGeoIndicators(themeId);
  const indicators = (indicatorsData?.indicators ?? []).filter((i) => i.mvpEnabled);
  const crawlMutation = useGeoCrawlOfficialSources();

  if (isLoading) return <p className="text-muted-foreground">...</p>;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="text-sm text-muted-foreground">
          {t("geo.overview.unverifiedEvents")}: <span className="tabular-nums font-medium text-foreground">{overview?.unverifiedEventCount ?? 0}</span>
        </div>
        <Button size="sm" onClick={() => crawlMutation.mutate()} disabled={crawlMutation.isPending}>
          {crawlMutation.isPending ? t("geo.overview.crawling") : t("geo.overview.crawlButton")}
        </Button>
      </div>
      {crawlMutation.data && (
        <div className="card-paper rounded-lg p-3 text-xs space-y-1">
          {crawlMutation.data.results.map((r, i) => (
            <div key={i} className={r.ok ? "text-muted-foreground" : "text-destructive"}>
              {r.ok ? "✓" : "✗"} {r.source}{r.indicator ? ` (${r.indicator})` : ""}{r.error ? `: ${r.error}` : ""}
            </div>
          ))}
        </div>
      )}

      <div className="card-paper rounded-lg p-5 space-y-3">
        <h2 className="text-editorial text-lg">{t("geo.overview.heatmapTitle")}</h2>
        <GeoIndicatorPanel themeId={themeId} indicators={indicators} />
      </div>

      <div className="card-paper rounded-lg p-5 space-y-2">
        <h2 className="text-editorial text-lg">{t("geo.overview.recentAudit")}</h2>
        <ul className="text-sm divide-y divide-border">
          {(overview?.recentAuditLogs ?? []).map((a) => (
            <li key={a.id} className="py-2 flex items-center justify-between">
              <span>{a.actor} — {a.action}{a.targetTable ? ` (${a.targetTable})` : ""}</span>
              <span className="text-xs text-muted-foreground">{a.createdAt}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
