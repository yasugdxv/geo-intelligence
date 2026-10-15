import { Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { Bell, RefreshCw } from "lucide-react";
import { TopBar } from "@/components/top-bar";
import { DataFreshnessBadge } from "@/components/geo/data-freshness";
import { GeoStatTile } from "@/components/geo/geo-stat-tile";
import {
  MonitoringStateBadge,
  ThemeFreshnessBadge,
  useApplyMonitoringSources,
  useRelativeTime,
} from "@/components/geo/monitoring-common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useLanguage } from "@/lib/i18n";
import {
  useArchiveMonitoringTheme,
  useCheckAllMonitoring,
  useMonitoringThemes,
  useSourceSet,
  type MonitoringTheme,
} from "@/lib/geoSourceLibraryApi";
import { clearPickContext, getPickContext, type SourcePickContext } from "@/lib/sourcePickStore";

// 追加先テーマ1件に対するSource Set更新（既存Source Setとの和集合 → 無ければ新規作成して紐付け）
function PickApplyAction({
  theme,
  pickedIds,
  onDone,
}: {
  theme: MonitoringTheme;
  pickedIds: string[];
  onDone: () => void;
}) {
  const { t } = useLanguage();
  const set = useSourceSet(theme.source_set_id);
  const { apply, isPending, error } = useApplyMonitoringSources(theme);
  const ready = !theme.source_set_id || !!set.data;

  const run = async () => {
    const existing = (set.data?.source_set.sources ?? []).map((s) => s.source_id);
    const merged = Array.from(new Set([...existing, ...pickedIds]));
    try {
      await apply(merged);
      onDone();
    } catch {
      // エラーは下の error 表示で見せる
    }
  };

  return (
    <div className="space-y-1">
      <Button size="sm" onClick={run} disabled={!ready || isPending}>
        {isPending ? t("geo.monitoringThemes.pick.applying") : t("geo.monitoringThemes.pick.apply")}
      </Button>
      {error && <p className="text-xs text-destructive">{error.message}</p>}
    </div>
  );
}

// Source Library経由で来た場合の案内。既存テーマを選んでSource Setへ追加する
function PickBanner({
  ctx,
  themes,
  onClose,
}: {
  ctx: SourcePickContext;
  themes: MonitoringTheme[];
  onClose: () => void;
}) {
  const { t } = useLanguage();
  const [target, setTarget] = useState<string>("");
  const theme = themes.find((x) => x.monitoring_theme_id === target);

  return (
    <div className="geo-card p-4 space-y-3" data-testid="monitoring-pick-banner">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-medium">
          {t("geo.monitoringThemes.pick.banner", { count: ctx.selectedIds.length })}
        </p>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            clearPickContext();
            onClose();
          }}
        >
          {t("geo.monitoringThemes.pick.discard")}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">{t("geo.monitoringThemes.pick.desc")}</p>
      <div className="flex flex-wrap items-start gap-2">
        <Select value={target} onValueChange={setTarget}>
          <SelectTrigger className="w-72" aria-label={t("geo.monitoringThemes.pick.select")}>
            <SelectValue placeholder={t("geo.monitoringThemes.pick.select")} />
          </SelectTrigger>
          <SelectContent>
            {themes.map((x) => (
              <SelectItem key={x.monitoring_theme_id} value={x.monitoring_theme_id}>
                {x.display_name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {theme && (
          <PickApplyAction
            key={theme.monitoring_theme_id}
            theme={theme}
            pickedIds={ctx.selectedIds}
            onDone={() => {
              clearPickContext();
              onClose();
            }}
          />
        )}
        <Button asChild variant="outline" size="sm">
          <Link to="/geo/theme-discovery">{t("geo.monitoringThemes.openExplorer")}</Link>
        </Button>
      </div>
    </div>
  );
}

export function MonitoringListPage() {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const relTime = useRelativeTime();
  const query = useMonitoringThemes();
  const checkAll = useCheckAllMonitoring();
  const archive = useArchiveMonitoringTheme();
  const [pickCtx, setPickCtx] = useState<SourcePickContext | null>(() => {
    const ctx = getPickContext();
    return ctx && ctx.returnTo.replace(/\/$/, "") === "/geo/monitoring-themes" ? ctx : null;
  });

  const themes = (query.data?.themes ?? []).filter((x) => x.status !== "archived");
  const summary = query.data?.summary;

  const openDetail = (id: string) =>
    navigate({
      to: "/geo/monitoring-themes/$monitoringThemeId",
      params: { monitoringThemeId: id },
    });

  return (
    <>
      <TopBar breadcrumb={[{ label: t("nav.geoMonitoringThemes") }]} />
      <main className="flex-1 max-w-[1200px] w-full mx-auto px-8 py-8 space-y-6">
        <section className="border-b border-border pb-5 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="geo-heading text-4xl">{t("geo.monitoringThemes.title")}</h1>
            <p className="text-sm text-muted-foreground mt-1">
              {t("geo.monitoringThemes.subtitle")}
            </p>
          </div>
          <Button
            onClick={() => checkAll.mutate()}
            disabled={checkAll.isPending || themes.length === 0}
          >
            <RefreshCw className={`h-4 w-4 mr-1 ${checkAll.isPending ? "animate-spin" : ""}`} />
            {checkAll.isPending
              ? t("geo.monitoringThemes.checking")
              : t("geo.monitoringThemes.checkAll")}
          </Button>
        </section>

        {pickCtx && <PickBanner ctx={pickCtx} themes={themes} onClose={() => setPickCtx(null)} />}

        {query.isLoading && (
          <p className="text-sm text-muted-foreground">{t("geo.monitoringThemes.loading")}</p>
        )}

        {query.isError && (
          <div className="geo-card p-4 space-y-1" role="alert">
            <p className="text-sm text-destructive">
              {t("geo.monitoringThemes.error", { message: (query.error as Error).message })}
            </p>
            <p className="text-xs text-muted-foreground">{t("geo.monitoringThemes.errorHint")}</p>
          </div>
        )}
        {checkAll.isError && (
          <p className="text-sm text-destructive" role="alert">
            {(checkAll.error as Error).message}
          </p>
        )}
        {checkAll.isSuccess && (
          <p className="text-xs text-muted-foreground">
            {t("geo.monitoringThemes.checkedDone", { count: checkAll.data.checked })}
          </p>
        )}

        {query.data && (
          <>
            <section className="space-y-3">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <GeoStatTile
                  label={t("geo.monitoringThemes.summary.count")}
                  value={summary?.monitoring_count ?? 0}
                />
                <GeoStatTile
                  label={t("geo.monitoringThemes.summary.alerts")}
                  value={summary?.unacknowledged_alerts ?? 0}
                />
                <GeoStatTile
                  label={t("geo.monitoringThemes.summary.today")}
                  value={summary?.new_articles_today ?? 0}
                />
                <GeoStatTile
                  label={t("geo.monitoringThemes.summary.important")}
                  value={summary?.important_changes ?? 0}
                />
              </div>
              <DataFreshnessBadge freshness={query.data.data_freshness} showNote />
            </section>

            {themes.length === 0 ? (
              <div className="geo-card p-8 text-center space-y-3" data-testid="monitoring-empty">
                <p className="text-sm text-muted-foreground">{t("geo.monitoringThemes.empty")}</p>
                <Button asChild variant="outline">
                  <Link to="/geo/theme-discovery">{t("geo.monitoringThemes.openExplorer")}</Link>
                </Button>
              </div>
            ) : (
              <div className="geo-card overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("geo.monitoringThemes.col.theme")}</TableHead>
                      <TableHead>{t("geo.monitoringThemes.col.state")}</TableHead>
                      <TableHead>{t("geo.monitoringThemes.col.change")}</TableHead>
                      <TableHead>{t("geo.monitoringThemes.col.articles")}</TableHead>
                      <TableHead>{t("geo.monitoringThemes.col.updated")}</TableHead>
                      <TableHead>{t("geo.monitoringThemes.col.alert")}</TableHead>
                      <TableHead />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {themes.map((th) => {
                      const a = th.latest_assessment;
                      const state = a?.state ?? th.assessment_state ?? null;
                      // no_fresh_dataでは「変化なし」と誤読される文言を出さず、判定不能と表示する
                      const change =
                        state === "no_fresh_data"
                          ? t("geo.monitoringThemes.undeterminable")
                          : state === "partial_coverage"
                            ? (a?.coverage_note ?? a?.summary ?? "-")
                            : (a?.latest_change ?? a?.summary ?? "-");
                      const unread = th.unacknowledged_alerts ?? 0;
                      return (
                        <TableRow
                          key={th.monitoring_theme_id}
                          className="cursor-pointer"
                          onClick={() => openDetail(th.monitoring_theme_id)}
                          data-testid="monitoring-row"
                        >
                          <TableCell className="font-medium max-w-[260px]">
                            <div className="truncate">{th.display_name}</div>
                            {a?.freshness_state === "partial_freshness" ? (
                              <ThemeFreshnessBadge state={a.freshness_state} className="mt-1" />
                            ) : (
                              <DataFreshnessBadge freshness={a?.freshness} className="mt-1" />
                            )}
                          </TableCell>
                          <TableCell>
                            <MonitoringStateBadge state={state} />
                          </TableCell>
                          <TableCell className="max-w-[320px] text-xs text-muted-foreground">
                            <div className="line-clamp-2">{change}</div>
                          </TableCell>
                          <TableCell>
                            {state === "no_fresh_data" || !a
                              ? "-"
                              : t("geo.monitoringThemes.articlesCount", {
                                  count: a.new_articles_24h,
                                })}
                          </TableCell>
                          <TableCell className="text-xs whitespace-nowrap">
                            {relTime(th.last_checked_at)}
                          </TableCell>
                          <TableCell>
                            {unread > 0 ? (
                              <Badge variant="destructive" className="gap-1">
                                <Bell className="h-3 w-3" />
                                {unread}
                              </Badge>
                            ) : (
                              <span className="text-xs text-muted-foreground">0</span>
                            )}
                          </TableCell>
                          <TableCell onClick={(e) => e.stopPropagation()}>
                            <Button
                              variant="ghost"
                              size="sm"
                              disabled={archive.isPending}
                              onClick={() => {
                                if (
                                  window.confirm(
                                    t("geo.monitoringThemes.archiveConfirm", {
                                      name: th.display_name,
                                    }),
                                  )
                                )
                                  archive.mutate(th.monitoring_theme_id);
                              }}
                            >
                              {t("geo.monitoringThemes.archive")}
                            </Button>
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
            )}
          </>
        )}
      </main>
    </>
  );
}
