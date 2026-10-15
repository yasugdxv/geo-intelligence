import { createFileRoute, Link } from "@tanstack/react-router";
import { Sparkles } from "lucide-react";
import { TopBar } from "@/components/top-bar";
import { GeoCard, GeoCardBody, GeoCardHeader } from "@/components/geo/geo-card";
import { GeoStatTile } from "@/components/geo/geo-stat-tile";
import { GeoStatusBadge, PRIORITY_SEVERITY } from "@/components/geo/geo-badge";
import { deriveIndicatorStatus, IndicatorStatusDot } from "@/components/geo/geo-indicator-status";
import {
  GeoTheme, useGeoEvents, useGeoExpertRequests, useGeoIndicatorObservations, useGeoIndicators, useGeoThemes,
} from "@/lib/api";
import { useIntelligenceNeeds } from "@/lib/geoIntelligenceNeedsApi";
import { useLanguage } from "@/lib/i18n";

// "/geo"はHome専用のindexルート（geo.index.tsx）。geo.*.tsx兄弟ルートの
// 暗黙の親レイアウトにはならない（TanStack Routerの.index.tsx規約により、
// このファイルが無くても他のgeo.*ルートの表示には影響しない）。
export const Route = createFileRoute("/geo/")({
  component: GeoHomePage,
});

// テーマごとの指標データ品質サマリー。ThemeStatusStrip(geo.monitoring.tsx)と同様、
// 子コンポーネントとしてテーマ数ぶん描画することでHooksルール違反を避ける
// （1コンポーネント内でテーマ数ぶんHookを直接ループ呼び出しするのは禁止のため）。
function IndicatorDot({ indicatorId }: { indicatorId: string }) {
  const { data } = useGeoIndicatorObservations(indicatorId);
  return <IndicatorStatusDot status={deriveIndicatorStatus(data?.observations?.[0])} />;
}

function HomeThemeStatusCard({ theme, lang }: { theme: GeoTheme; lang: "ja" | "en" }) {
  const { data: indicatorsData } = useGeoIndicators(theme.id);
  const indicators = (indicatorsData?.indicators ?? []).filter((i) => i.mvpEnabled);

  return (
    <Link
      to="/geo/theme/$themeId"
      params={{ themeId: theme.id }}
      className="geo-card p-4 flex flex-col gap-2 hover:bg-accent/40 transition-colors"
    >
      <div className="flex items-center justify-between">
        <span className="geo-heading text-sm truncate">
          {lang === "en" && theme.nameEn ? theme.nameEn : theme.nameJa}
        </span>
        <GeoStatusBadge severity={theme.status === "有効" ? "moderate" : "neutral"}>
          {theme.status}
        </GeoStatusBadge>
      </div>
      <div className="flex items-center gap-1 flex-wrap">
        {indicators.length === 0 ? (
          <span className="text-xs text-muted-foreground">—</span>
        ) : (
          indicators.map((i) => <IndicatorDot key={i.id} indicatorId={i.id} />)
        )}
        <span className="text-[10px] text-muted-foreground ml-1">{indicators.length} indicators</span>
      </div>
    </Link>
  );
}

function GeoHomePage() {
  const { lang, t } = useLanguage();
  const themesQuery = useGeoThemes();
  const themes = themesQuery.data?.themes ?? [];

  // 全テーマ横断のverifiedイベントを1回で取得（themeId未指定）。テーマ数ぶんの
  // Hookループにはしない。
  const eventsQuery = useGeoEvents({ verified: true });
  const recentEvents = [...(eventsQuery.data?.events ?? [])]
    .filter((e) => e.startAt)
    .sort((a, b) => (a.startAt! < b.startAt! ? 1 : -1))
    .slice(0, 6);
  const themeNameById = new Map(themes.map((th) => [th.id, lang === "en" && th.nameEn ? th.nameEn : th.nameJa]));

  // 全リクエスト横断（themeId未指定）、軽量一覧のみ（本文取得のN+1は避ける。
  // queryText自体が実データとして意味のある表示内容になる）。
  const expertRequestsQuery = useGeoExpertRequests();
  const latestIntelligence = [...(expertRequestsQuery.data?.requests ?? [])]
    .sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1))
    .slice(0, 5);

  const needsQuery = useIntelligenceNeeds();
  const priorityNeeds = [...(needsQuery.data?.needs ?? [])]
    .sort((a, b) => (a.priority === b.priority ? 0 : a.priority === "high" ? -1 : b.priority === "high" ? 1 : 0))
    .slice(0, 5);

  return (
    <>
      <TopBar breadcrumb={[{ label: t("nav.geoHome") }]} />
      <main className="flex-1 max-w-[1440px] w-full mx-auto px-8 py-8 space-y-6">
        <section className="border-b border-border pb-5">
          <h1 className="geo-heading text-4xl">{t("nav.geoHome")}</h1>
        </section>

        <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-6 items-start">
          <div className="space-y-6 min-w-0">
            <GeoCard>
              <GeoCardHeader title="Recent Verified Events" />
              <GeoCardBody>
                {recentEvents.length === 0 ? (
                  <p className="text-sm text-muted-foreground">—</p>
                ) : (
                  <ul className="space-y-2.5">
                    {recentEvents.map((e) => (
                      <li key={e.id} className="text-sm flex items-baseline justify-between gap-3">
                        <span className="truncate">
                          <span className="text-muted-foreground">{themeNameById.get(e.themeId) ?? ""}: </span>
                          {e.actor ?? "?"} — {e.action ?? e.eventType ?? "?"}
                        </span>
                        <span className="text-xs text-muted-foreground flex-shrink-0">{e.startAt?.slice(0, 10)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </GeoCardBody>
            </GeoCard>

            <div>
              <h2 className="geo-heading text-base mb-3">{t("nav.geoMonitoring")}</h2>
              {themesQuery.isLoading ? (
                <p className="text-sm text-muted-foreground">...</p>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                  {themes.map((theme) => (
                    <HomeThemeStatusCard key={theme.id} theme={theme} lang={lang} />
                  ))}
                </div>
              )}
            </div>

            <GeoCard>
              <GeoCardHeader title="Latest Intelligence" />
              <GeoCardBody>
                {latestIntelligence.length === 0 ? (
                  <p className="text-sm text-muted-foreground">—</p>
                ) : (
                  <ul className="space-y-2.5">
                    {latestIntelligence.map((r) => (
                      <li key={r.requestId} className="text-sm flex items-baseline justify-between gap-3">
                        <span className="truncate">
                          <span className="text-xs text-muted-foreground uppercase mr-1.5">{r.mode}</span>
                          {r.queryText}
                        </span>
                        <span className="text-xs text-muted-foreground flex-shrink-0">{r.createdAt.slice(0, 10)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </GeoCardBody>
            </GeoCard>
          </div>

          <div className="space-y-6">
            <Link
              to="/geo/intelligence-ai"
              className="geo-card p-4 flex items-center gap-3 hover:bg-accent/40 transition-colors bg-geo-navy text-white border-0"
            >
              <Sparkles className="h-5 w-5 text-geo-teal flex-shrink-0" />
              <div>
                <div className="geo-heading text-sm">Ask Intelligence AI</div>
                <div className="text-xs text-white/70 mt-0.5">{t("nav.geoIntelligenceAI")}</div>
              </div>
            </Link>

            <GeoCard>
              <GeoCardHeader title={t("nav.geoIntelligenceNeeds")} />
              <GeoCardBody className="space-y-2">
                {priorityNeeds.length === 0 ? (
                  <p className="text-sm text-muted-foreground">—</p>
                ) : (
                  priorityNeeds.map((n) => (
                    <Link
                      key={n.gap_id}
                      to="/geo/intelligence-needs/$id"
                      params={{ id: n.gap_id }}
                      className="block rounded-md border border-border px-3 py-2 hover:bg-accent/40 transition-colors"
                    >
                      <div className="flex items-center justify-between gap-2 mb-1">
                        <GeoStatusBadge severity={PRIORITY_SEVERITY[n.priority] ?? "neutral"}>
                          {n.priority}
                        </GeoStatusBadge>
                        <span className="text-[10px] text-muted-foreground">{n.requested_by_app ?? "-"}</span>
                      </div>
                      <p className="text-sm line-clamp-2">{n.question ?? "-"}</p>
                    </Link>
                  ))
                )}
              </GeoCardBody>
            </GeoCard>

            <GeoStatTile label="Themes" value={themes.length} />
          </div>
        </div>
      </main>
    </>
  );
}
