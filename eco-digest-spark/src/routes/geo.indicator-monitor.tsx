import { createFileRoute, Link } from "@tanstack/react-router";
import { TopBar } from "@/components/top-bar";
import { Badge } from "@/components/ui/badge";
import { IndicatorAlertBadge, IndicatorFreshnessBadge } from "@/components/geo/geo-freshness-badge";
import { DeviationIndicator } from "@/components/geo/geo-indicator-status";
import { useGeoIndicatorMonitor, useGeoThemes } from "@/lib/api";
import { useLanguage } from "@/lib/i18n";

export const Route = createFileRoute("/geo/indicator-monitor")({
  component: GeoIndicatorMonitorPage,
});

function GeoIndicatorMonitorPage() {
  const { lang, t } = useLanguage();
  const monitorQuery = useGeoIndicatorMonitor();
  const themesQuery = useGeoThemes();
  const rows = monitorQuery.data?.indicators ?? [];
  const themeById = new Map((themesQuery.data?.themes ?? []).map((th) => [th.id, th]));

  return (
    <>
      <TopBar breadcrumb={[{ label: t("nav.geoIndicatorMonitor") }]} />
      <main className="flex-1 max-w-[1200px] w-full mx-auto px-8 py-8 space-y-6">
        <section className="border-b border-border pb-5">
          <h1 className="text-editorial text-4xl">{t("nav.geoIndicatorMonitor")}</h1>
          <p className="text-sm text-muted-foreground mt-2">
            全テーマ横断でmvp_enabled指標の最新値・乖離・鮮度・Alertを一覧表示します。
            Alertは人がgeo_indicator_rule_versions.rule_definition.thresholdsへ明示的に設定した
            閾値との比較結果であり、自動リスク判定ではありません（詳細: docs/geopolitical-indicator-monitor.md）。
          </p>
        </section>

        {monitorQuery.isLoading ? (
          <p className="text-sm text-muted-foreground">...</p>
        ) : rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">mvp_enabled指標が登録されていません。</p>
        ) : (
          <div className="space-y-2">
            {rows.map((row) => {
              const theme = row.themeId ? themeById.get(row.themeId) : undefined;
              const themeName = theme ? (lang === "en" && theme.nameEn ? theme.nameEn : theme.nameJa) : null;
              return (
                <Link key={row.id} to="/geo/theme/$themeId/indicator/$indicatorId"
                      params={{ themeId: row.themeId ?? "", indicatorId: row.id }}
                      className="card-paper rounded-lg p-4 flex items-center justify-between gap-4 hover:bg-sidebar-accent/20 transition-colors">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-editorial text-base truncate">{row.name}</span>
                      {themeName && <Badge variant="outline" className="shrink-0">{themeName}</Badge>}
                    </div>
                    <p className="text-xs text-muted-foreground">{row.code}</p>
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    <div className="text-right">
                      <div className="tabular-nums text-sm">
                        {row.latestObservation?.insufficientData
                          ? "データ不足"
                          : row.latestObservation?.qualitativeLabel ?? row.latestObservation?.rawValue ?? "—"}
                      </div>
                      <DeviationIndicator deviation={row.latestObservation?.deviation ?? null} />
                    </div>
                    <IndicatorFreshnessBadge freshness={row.freshness} />
                    <IndicatorAlertBadge alert={row.latestAlert} />
                  </div>
                </Link>
              );
            })}
          </div>
        )}
      </main>
    </>
  );
}
