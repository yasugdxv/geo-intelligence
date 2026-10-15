import { AlertTriangle, CheckCircle2, CircleDot, CircleHelp, Eye, Minus, TrendingUp } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { useLanguage } from "@/lib/i18n";
import {
  monitoringKeys,
  useCreateSourceSet,
  useUpdateMonitoringTheme,
  useUpdateSourceSet,
  type CoverageStatus,
  type MonitoringState,
  type MonitoringTheme,
  type ThemeCoverage,
  type ThemeFreshnessState,
} from "@/lib/geoSourceLibraryApi";
import { cn } from "@/lib/utils";

// モニタリング画面の共通部品。Stateの見た目はno_fresh_data(判定不能)をStableと明確に区別する。

const STATE_STYLE: Record<MonitoringState, string> = {
  no_fresh_data: "border-geo-amber/60 bg-geo-amber-soft text-geo-amber",
  partial_coverage: "border-sky-300 bg-sky-50 text-sky-800",
  no_new_articles: "border-border bg-muted text-muted-foreground",
  stable: "border-geo-green/40 bg-geo-green-soft text-geo-green",
  watch: "border-sky-300 bg-sky-100 text-sky-800",
  changed: "border-red-300 bg-red-100 text-red-800",
};

const STATE_ICON: Record<MonitoringState, typeof Eye> = {
  no_fresh_data: AlertTriangle,
  partial_coverage: CircleDot,
  no_new_articles: Minus,
  stable: CheckCircle2,
  watch: Eye,
  changed: TrendingUp,
};

/** 現在状態のバッジ。state=null は「未評価」 */
export function MonitoringStateBadge({
  state,
  className,
}: {
  state: MonitoringState | null | undefined;
  className?: string;
}) {
  const { t } = useLanguage();
  if (!state) {
    return (
      <Badge
        variant="outline"
        className={cn("gap-1 text-muted-foreground", className)}
        data-state="none"
      >
        <CircleHelp className="h-3 w-3" />
        {t("geo.monitoringThemes.notEvaluated")}
      </Badge>
    );
  }
  const Icon = STATE_ICON[state];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium",
        STATE_STYLE[state],
        className,
      )}
      data-testid="monitoring-state"
      data-state={state}
    >
      <Icon className="h-3.5 w-3.5" />
      {t(`geo.monitoringThemes.state.${state}`)}
    </span>
  );
}

/** no_fresh_dataのときの「変化なしを意味しない」注記 */
export function NoFreshDataNote({ lastDataAt }: { lastDataAt: string | null | undefined }) {
  const { t } = useLanguage();
  return (
    <p
      className="rounded-md border border-geo-amber/50 bg-geo-amber-soft px-3 py-2 text-xs text-geo-amber"
      data-testid="no-fresh-data-note"
    >
      {lastDataAt
        ? t("geo.monitoringThemes.noFreshNote", { date: lastDataAt.slice(0, 10) })
        : t("geo.monitoringThemes.noFreshNoteUnknown")}
    </p>
  );
}

const COVERAGE_SYMBOL: Record<CoverageStatus, { mark: string; tone: string }> = {
  fresh: { mark: "●", tone: "text-geo-green" },
  partial: { mark: "◐", tone: "text-sky-700" },
  stale: { mark: "▲", tone: "text-geo-amber" },
  unknown: { mark: "?", tone: "text-muted-foreground" },
  not_configured: { mark: "○", tone: "text-muted-foreground" },
};

/** Theme Freshness（Theme Stateとは別）のバッジ。partial_freshnessは「一部の情報源は最新」と明示する */
export function ThemeFreshnessBadge({
  state,
  className,
}: {
  state: ThemeFreshnessState | null | undefined;
  className?: string;
}) {
  const { t } = useLanguage();
  if (!state) return null;
  const tone =
    state === "fresh"
      ? "border-geo-green/40 bg-geo-green-soft text-geo-green"
      : state === "partial_freshness"
        ? "border-sky-300 bg-sky-50 text-sky-800"
        : "border-geo-amber/50 bg-geo-amber-soft text-geo-amber";
  return (
    <span
      className={cn("inline-flex items-center rounded-md border px-2 py-0.5 text-xs", tone, className)}
      data-testid="theme-freshness"
      data-state={state}
    >
      {t(`geo.coverage.freshness.${state}`)}
    </span>
  );
}

/** 情報源のカバレッジ（一次情報／専門Source／広域Media）。個別Sourceは展開して確認する */
export function CoveragePanel({
  coverage,
  note,
}: {
  coverage: ThemeCoverage | null | undefined;
  note?: string | null;
}) {
  const { t } = useLanguage();
  if (!coverage) return null;
  const partial = coverage.freshness_state === "partial_freshness";
  return (
    <div
      className="space-y-2 rounded-md border border-border bg-muted/30 px-3 py-2"
      data-testid="coverage-panel"
      data-freshness={coverage.freshness_state}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium">{t("geo.coverage.title")}</span>
        <ThemeFreshnessBadge state={coverage.freshness_state} />
      </div>
      {note && (
        <p
          className={cn("text-xs", partial ? "text-sky-900" : "text-geo-amber")}
          data-testid="coverage-note"
        >
          {note}
        </p>
      )}
      <ul className="space-y-1 text-xs">
        {coverage.components.map((c) => {
          const sym = COVERAGE_SYMBOL[c.status];
          const all = [...c.sources, ...(c.auxiliary_sources ?? [])];
          return (
            <li key={c.key} data-testid={`coverage-${c.key}`} data-status={c.status}>
              <div className="flex items-center gap-2">
                <span className={cn("w-4 text-center", sym.tone)}>{sym.mark}</span>
                <span className="font-medium">{t(`geo.coverage.comp.${c.key}`)}</span>
                <span className="text-muted-foreground">{t(`geo.coverage.status.${c.status}`)}</span>
              </div>
              {all.length > 0 && (
                <details className="ml-6 mt-0.5">
                  <summary className="cursor-pointer text-muted-foreground">
                    {t("geo.coverage.details", { n: all.length })}
                  </summary>
                  <ul className="mt-1 space-y-0.5">
                    {all.map((s) => (
                      <li key={s.source_id} className="text-muted-foreground">
                        {s.status === "fresh" ? "●" : s.status === "stale" ? "▲" : "?"} {s.name}
                        {s.role === "auxiliary" ? ` ${t("geo.coverage.auxiliary")}` : ""} — {s.reason}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

const REGION_ORDER = ["hormuz", "gulf_of_oman", "persian_gulf", "red_sea", "wider_middle_east", "unclassified"];

/** 関連記事の海域別の内訳（Hormuz / Gulf of Oman / Persian Gulf / Red Sea / Wider Middle East）。DB変更なし・ruleで分類 */
export function RegionBreakdown({ breakdown }: { breakdown: Record<string, number> | null | undefined }) {
  const { t } = useLanguage();
  if (!breakdown || Object.keys(breakdown).length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs" data-testid="region-breakdown">
      <span className="text-muted-foreground">{t("geo.coverage.region.title")}</span>
      {REGION_ORDER.filter((k) => breakdown[k]).map((k) => (
        <span
          key={k}
          className="inline-flex items-center gap-1 rounded-md border border-border bg-muted/40 px-1.5 py-0.5"
          data-region={k}
        >
          {t(`geo.coverage.region.${k}`)}
          <span className="font-medium">{breakdown[k]}</span>
        </span>
      ))}
    </div>
  );
}

/** 相対時間表示（分/時間/日） */
export function useRelativeTime() {
  const { t } = useLanguage();
  return (iso: string | null | undefined): string => {
    if (!iso) return t("geo.monitoringThemes.never");
    const ms = Date.now() - new Date(iso).getTime();
    if (Number.isNaN(ms)) return iso;
    const min = Math.floor(ms / 60000);
    if (min < 1) return t("geo.monitoringThemes.time.now");
    if (min < 60) return t("geo.monitoringThemes.time.minutes", { n: min });
    const hours = Math.floor(min / 60);
    if (hours < 24) return t("geo.monitoringThemes.time.hours", { n: hours });
    return t("geo.monitoringThemes.time.days", { n: Math.floor(hours / 24) });
  };
}

/**
 * Source Setを source_id 配列で置き換える共通処理。
 * source_set_id があれば更新、無ければ新規Source Setを作ってMonitoring Themeへ紐付ける。
 */
export function useApplyMonitoringSources(
  theme: Pick<MonitoringTheme, "monitoring_theme_id" | "display_name" | "source_set_id">,
) {
  const updateSet = useUpdateSourceSet(theme.source_set_id ?? "");
  const createSet = useCreateSourceSet();
  const updateTheme = useUpdateMonitoringTheme(theme.monitoring_theme_id);
  const qc = useQueryClient();

  const apply = async (ids: string[]) => {
    if (theme.source_set_id) {
      await updateSet.mutateAsync({ source_ids: ids });
      // Detailのsources一覧を最新化するため、Monitoringのキャッシュを無効化する
      await qc.invalidateQueries({ queryKey: monitoringKeys.all });
    } else {
      const created = await createSet.mutateAsync({
        name: `${theme.display_name} Sources`,
        source_ids: ids,
      });
      await updateTheme.mutateAsync({ source_set_id: created.source_set.source_set_id });
    }
  };
  return {
    apply,
    isPending: updateSet.isPending || createSet.isPending || updateTheme.isPending,
    error: (updateSet.error ?? createSet.error ?? updateTheme.error) as Error | null,
  };
}
