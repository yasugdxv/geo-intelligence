import { createFileRoute, Link } from "@tanstack/react-router";
import { AlertTriangle, ChevronRight } from "lucide-react";
import { TopBar } from "@/components/top-bar";
import { Button } from "@/components/ui/button";
import { GeoIndicator, useGeoIndicatorObservations, useGeoIndicators, useGeoOverview, useGeoSeedTaiwanTheme, useGeoThemes } from "@/lib/api";
import { useLanguage } from "@/lib/i18n";
import { deriveIndicatorStatus, IndicatorStatusDot } from "@/components/geo/geo-indicator-status";
import { GeoCard, GeoCardBody } from "@/components/geo/geo-card";
import { GeoStatusBadge } from "@/components/geo/geo-badge";

export const Route = createFileRoute("/geo/monitoring")({
  component: GeoMonitoringPage,
});

function IndicatorDot({ indicator }: { indicator: GeoIndicator }) {
  const { data } = useGeoIndicatorObservations(indicator.id);
  return <IndicatorStatusDot status={deriveIndicatorStatus(data?.observations?.[0])} />;
}

// テーマカード上で「全体の状態」を一目で見渡せるよう、指標ごとの状態ドット（実データ/
// モック/データ不足）と、未検証Event件数を表示する簡易サマリー。
function ThemeStatusStrip({ themeId }: { themeId: string }) {
  const { t } = useLanguage();
  const { data: indicatorsData } = useGeoIndicators(themeId);
  const { data: overview } = useGeoOverview(themeId);
  const indicators = (indicatorsData?.indicators ?? []).filter((i) => i.mvpEnabled);

  return (
    <div className="space-y-1.5 pt-1 border-t border-border">
      <div className="flex flex-wrap items-center gap-1">
        {indicators.map((i) => <IndicatorDot key={i.id} indicator={i} />)}
        <span className="text-[10px] text-muted-foreground ml-1">{indicators.length} indicators</span>
      </div>
      <div className="text-[10px] text-muted-foreground">
        {t("geo.overview.unverifiedEvents")}: <span className="tabular-nums">{overview?.unverifiedEventCount ?? 0}</span>
      </div>
    </div>
  );
}

function GeoMonitoringPage() {
  const { lang, t } = useLanguage();
  const { data, isLoading } = useGeoThemes();
  // 「監視対象イベント」は有効化されているテーマのみを対象にする（無効化済み・検証用の
  // テーマまで一律に表示されてしまっていたため。/api/geo/themes自体は他画面
  // （Expert AIのテーマ選択等）でも使う共有APIのため、絞り込みはこの画面側だけで行う）。
  const themes = (data?.themes ?? []).filter((theme) => theme.status === "有効");
  const seedMutation = useGeoSeedTaiwanTheme();

  return (
    <>
      <TopBar breadcrumb={[{ label: t("nav.geoMonitoring") }]} />
      <main className="flex-1 max-w-[1440px] w-full mx-auto px-8 py-8 space-y-6">
        <section className="border-b border-border pb-5">
          <h1 className="geo-heading text-4xl">{t("geo.situations.title")}</h1>
          <p className="text-sm text-muted-foreground mt-2">{t("geo.situations.subtitle")}</p>
        </section>

        {isLoading ? (
          <p className="text-muted-foreground">...</p>
        ) : themes.length === 0 ? (
          <GeoCard>
            <GeoCardBody className="pt-6 space-y-4">
              <p className="text-sm text-muted-foreground">{t("geo.situations.empty")}</p>
              <Button onClick={() => seedMutation.mutate()} disabled={seedMutation.isPending}>
                {seedMutation.isPending ? t("geo.overview.seeding") : t("geo.overview.seedButton")}
              </Button>
            </GeoCardBody>
          </GeoCard>
        ) : (
          <div className="grid grid-cols-3 gap-4">
            {themes.map((theme) => (
              <Link
                key={theme.id}
                to="/geo/theme/$themeId"
                params={{ themeId: theme.id }}
                className="geo-card p-5 hover:bg-accent/40 transition-colors flex flex-col gap-3"
              >
                <div className="flex items-center justify-between text-muted-foreground">
                  <AlertTriangle className="h-4 w-4" />
                  <GeoStatusBadge severity={theme.status === "有効" ? "moderate" : "neutral"}>
                    {theme.status}
                  </GeoStatusBadge>
                </div>
                <div>
                  <div className="geo-heading text-lg">
                    {lang === "en" && theme.nameEn ? theme.nameEn : theme.nameJa}
                  </div>
                  {theme.description && (
                    <p className="text-xs text-muted-foreground mt-1.5 line-clamp-3">{theme.description}</p>
                  )}
                </div>
                <ThemeStatusStrip themeId={theme.id} />
                <div className="flex items-center gap-1 text-xs text-primary mt-auto">
                  {t("geo.situations.viewDashboard")} <ChevronRight className="h-3.5 w-3.5" />
                </div>
              </Link>
            ))}
          </div>
        )}
      </main>
    </>
  );
}
