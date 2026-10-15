import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { TopBar } from "@/components/top-bar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  useExternalSignals, useExternalSignalSearch, useExternalSignalSummary, useExternalSignalTopics,
  useExternalSignalQueries, useUpdateExternalSignalQuery, useRunExternalSignalFetch,
  type GeoExternalSignal,
} from "@/lib/geoExternalSignalApi";
import { useExternalEvents } from "@/lib/geoExternalEventApi";
import { useLanguage } from "@/lib/i18n";

export const Route = createFileRoute("/geo/news-signals")({
  component: GeoNewsSignalsPage,
});

function SummaryCard({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="card-paper rounded-lg p-4">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-editorial text-3xl mt-1 tabular-nums">{value}</div>
    </div>
  );
}

function SignalRow({ signal }: { signal: GeoExternalSignal }) {
  return (
    <div className="border-t border-border py-2 text-sm">
      <div className="flex items-start justify-between gap-3">
        <a href={signal.url ?? undefined} target="_blank" rel="noreferrer"
           className="min-w-0 truncate hover:underline"
           aria-disabled={!signal.url}>
          {signal.title || "(無題)"}
        </a>
        <Badge variant="outline" className="shrink-0">{signal.provider}</Badge>
      </div>
      <div className="text-xs text-muted-foreground mt-0.5 flex flex-wrap gap-x-2">
        <span>{signal.source_name || signal.source_domain || "不明な情報源"}</span>
        {signal.source_country && <span>（{signal.source_country}）</span>}
        <span>{signal.published_at ?? "日時不明"}</span>
      </div>
    </div>
  );
}

function GeoNewsSignalsPage() {
  const { t } = useLanguage();
  const summaryQuery = useExternalSignalSummary();
  const topicsQuery = useExternalSignalTopics(7);
  const latestQuery = useExternalSignals({ limit: 30 });
  const queriesQuery = useExternalSignalQueries({ provider: "gdelt" });
  const eventsQuery = useExternalEvents({ limit: 20 });
  const updateQueryMutation = useUpdateExternalSignalQuery();
  const runFetchMutation = useRunExternalSignalFetch();

  const [searchText, setSearchText] = useState("");
  const [searchCountry, setSearchCountry] = useState("");
  const searchMutation = useExternalSignalSearch();

  const summary = summaryQuery.data;
  const topics = topicsQuery.data?.topics ?? [];
  const latest = latestQuery.data?.items ?? [];
  const queries = queriesQuery.data?.queries ?? [];
  const searchResults = searchMutation.data?.items;
  const events = eventsQuery.data?.events ?? [];

  return (
    <>
      <TopBar breadcrumb={[{ label: t("nav.geoNewsSignals") }]} />
      <main className="flex-1 max-w-[1000px] w-full mx-auto px-8 py-8 space-y-6">
        <section className="border-b border-border pb-5">
          <h1 className="text-editorial text-4xl">{t("geo.newsSignals.title")}</h1>
          <p className="text-sm text-muted-foreground mt-2">{t("geo.newsSignals.subtitle")}</p>
          <Badge variant="outline" className="mt-2">{t("geo.newsSignals.unverifiedNote")}</Badge>
          <div className="mt-3">
            <Link to="/geo/episode-candidates" className="text-sm text-primary hover:underline">
              Actor / Media Episode Candidates →
            </Link>
          </div>
        </section>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {summaryQuery.isLoading ? (
            <p className="text-sm text-muted-foreground col-span-4">{t("geo.newsSignals.loading")}</p>
          ) : summaryQuery.isError ? (
            <p className="text-sm text-destructive col-span-4">
              {(summaryQuery.error as Error)?.message}
            </p>
          ) : (
            <>
              <SummaryCard label={t("geo.newsSignals.signals24h")} value={summary?.signals_24h ?? 0} />
              <SummaryCard label={t("geo.newsSignals.signals7d")} value={summary?.signals_7d ?? 0} />
              <SummaryCard label={t("geo.newsSignals.sources")} value={summary?.sources ?? 0} />
              <SummaryCard label={t("geo.newsSignals.countries")} value={summary?.countries ?? 0} />
            </>
          )}
        </div>

        <div className="card-paper rounded-lg p-4 space-y-2">
          <h2 className="text-editorial text-lg">{t("geo.newsSignals.trendingTopics")}</h2>
          {topicsQuery.isLoading ? (
            <p className="text-sm text-muted-foreground">{t("geo.newsSignals.loading")}</p>
          ) : topicsQuery.isError ? (
            <p className="text-sm text-destructive">{(topicsQuery.error as Error)?.message}</p>
          ) : topics.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("geo.newsSignals.noResults")}</p>
          ) : (
            topics.map((topic) => (
              <div key={topic.query_id} className="flex items-center justify-between text-sm border-t border-border pt-2">
                <span className="truncate">{topic.name}</span>
                <span className="tabular-nums flex items-center gap-2 shrink-0">
                  <span>{topic.current_count}</span>
                  {topic.change_pct !== null && (
                    <Badge variant={topic.change_pct >= 0 ? "secondary" : "outline"}>
                      {topic.change_pct >= 0 ? "+" : ""}{topic.change_pct}%
                    </Badge>
                  )}
                </span>
              </div>
            ))
          )}
        </div>

        <div className="card-paper rounded-lg p-4 space-y-2">
          <h2 className="text-editorial text-lg">{t("geo.newsSignals.queryRegistry")}</h2>
          {queriesQuery.isLoading ? (
            <p className="text-sm text-muted-foreground">{t("geo.newsSignals.loading")}</p>
          ) : queries.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("geo.newsSignals.noResults")}</p>
          ) : (
            queries.map((q) => (
              <div key={q.query_id} className="flex items-center justify-between gap-3 text-sm border-t border-border pt-2">
                <div className="min-w-0">
                  <div className="truncate">{q.name}</div>
                  <div className="text-xs text-muted-foreground">
                    {q.enabled ? "enabled" : "disabled"} / {q.article_lookback} / {q.last_run_at ?? "未実行"}
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <Button size="sm" variant="outline"
                          disabled={updateQueryMutation.isPending}
                          onClick={() => updateQueryMutation.mutate({ queryId: q.query_id, enabled: !q.enabled })}>
                    {q.enabled ? "無効化" : "有効化"}
                  </Button>
                  <Button size="sm" variant="outline"
                          disabled={runFetchMutation.isPending || !q.enabled}
                          onClick={() => runFetchMutation.mutate({ query_id: q.query_id })}>
                    {t("geo.newsSignals.runFetch")}
                  </Button>
                </div>
              </div>
            ))
          )}
          {runFetchMutation.isError && (
            <p className="text-xs text-destructive">{(runFetchMutation.error as Error)?.message}</p>
          )}
          {runFetchMutation.isSuccess && (
            <p className="text-xs text-muted-foreground">
              status={runFetchMutation.data.status} counts={JSON.stringify(runFetchMutation.data.counts)}
            </p>
          )}
        </div>

        <div className="card-paper rounded-lg p-4 space-y-3">
          <h2 className="text-editorial text-lg">{t("geo.newsSignals.search")}</h2>
          <div className="grid grid-cols-3 gap-2 items-end">
            <div className="space-y-1 col-span-2">
              <label className="text-xs text-muted-foreground">Free text</label>
              <Input value={searchText} onChange={(e) => setSearchText(e.target.value)} placeholder="例: semiconductor" />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">Country</label>
              <Input value={searchCountry} onChange={(e) => setSearchCountry(e.target.value)} placeholder="例: Taiwan" />
            </div>
          </div>
          <Button variant="outline" disabled={searchMutation.isPending}
                  onClick={() => searchMutation.mutate({
                    text: searchText || undefined, country: searchCountry || undefined, limit: 30,
                  })}>
            {t("geo.newsSignals.search")}
          </Button>
          {searchMutation.isError && (
            <p className="text-xs text-destructive">{(searchMutation.error as Error)?.message}</p>
          )}
          {searchResults && (
            searchResults.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("geo.newsSignals.noResults")}</p>
            ) : (
              <div>{searchResults.map((s) => <SignalRow key={s.external_signal_id} signal={s} />)}</div>
            )
          )}
        </div>

        <div className="card-paper rounded-lg p-4 space-y-1">
          <h2 className="text-editorial text-lg">{t("geo.newsSignals.latestSignals")}</h2>
          {latestQuery.isLoading ? (
            <p className="text-sm text-muted-foreground">{t("geo.newsSignals.loading")}</p>
          ) : latestQuery.isError ? (
            <p className="text-sm text-destructive">{(latestQuery.error as Error)?.message}</p>
          ) : latest.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("geo.newsSignals.noResults")}</p>
          ) : (
            latest.map((s) => <SignalRow key={s.external_signal_id} signal={s} />)
          )}
        </div>

        <div className="card-paper rounded-lg p-4 space-y-1">
          <h2 className="text-editorial text-lg">Recent External Events</h2>
          <p className="text-xs text-muted-foreground">
            GDELT自動抽出Event（automated event extraction / unverified）。人間のReviewは経ていません。
          </p>
          {eventsQuery.isLoading ? (
            <p className="text-sm text-muted-foreground">{t("geo.newsSignals.loading")}</p>
          ) : eventsQuery.isError ? (
            <p className="text-sm text-destructive">{(eventsQuery.error as Error)?.message}</p>
          ) : events.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("geo.newsSignals.noResults")}</p>
          ) : (
            events.map((ev) => (
              <div key={ev.external_event_id} className="border-t border-border py-2 text-sm">
                <div className="flex items-center justify-between gap-3">
                  <span className="min-w-0 truncate">
                    {ev.actor1_name || "不明"} → {ev.actor2_name || "不明"}
                  </span>
                  <Badge variant="outline" className="shrink-0">{ev.event_code}</Badge>
                </div>
                <div className="text-xs text-muted-foreground mt-0.5 flex flex-wrap gap-x-2">
                  {ev.action_geo_full_name && <span>{ev.action_geo_full_name}</span>}
                  <span>{ev.event_date ?? "日時不明"}</span>
                  <span>Goldstein: {ev.goldstein_scale ?? "-"}</span>
                </div>
              </div>
            ))
          )}
        </div>
      </main>
    </>
  );
}
