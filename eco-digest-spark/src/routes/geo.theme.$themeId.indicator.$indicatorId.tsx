import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowLeft, ExternalLink } from "lucide-react";
import { TopBar } from "@/components/top-bar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  GeoEvent, useGeoDocuments, useGeoEvents, useGeoEvidence, useGeoIndicatorMonitor, useGeoIndicatorObservations,
  useGeoIndicators, useGeoRecalculateIndicator, useGeoVerifyEvent,
} from "@/lib/api";
import { useLanguage } from "@/lib/i18n";
import { GeoIndicatorTrendChart } from "@/components/geo/geo-indicator-trend-chart";
import { DeviationIndicator, deriveIndicatorStatus, IndicatorStatusDot } from "@/components/geo/geo-indicator-status";
import { IndicatorAlertBadge, IndicatorFreshnessBadge } from "@/components/geo/geo-freshness-badge";

export const Route = createFileRoute("/geo/theme/$themeId/indicator/$indicatorId")({
  component: GeoIndicatorDetailPage,
});

// 指標に紐づくEventとそのEvidence（原文引用・出典文書）を1件分表示する。
// event_count_window方式（MIL-01等）の指標はここに複数のEventが並ぶ。
function EventWithEvidence({ event }: { event: GeoEvent }) {
  const { t } = useLanguage();
  const { data } = useGeoEvidence(event.id);
  const evidence = data?.evidence ?? [];
  const verifyMutation = useGeoVerifyEvent();

  return (
    <div className="card-paper rounded-lg p-4 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">{event.actor ?? "?"} — {event.action ?? event.eventType ?? "?"}</span>
        <Badge variant={event.verified ? "secondary" : "outline"}>
          {event.verified ? t("geo.events.filterVerified") : t("geo.events.filterUnverified")}
        </Badge>
      </div>
      <div className="text-xs text-muted-foreground grid grid-cols-2 gap-x-4 gap-y-1">
        {event.target && <span>target: {event.target}</span>}
        {event.location && <span>location: {event.location}</span>}
        {event.startAt && <span>start_at: {event.startAt}</span>}
      </div>
      {evidence.map((e) => (
        <blockquote key={e.id} className="text-sm italic border-l-2 border-border pl-3">
          “{e.quote ?? e.claimText}”
          {e.documentTitle && <span className="not-italic text-xs text-muted-foreground ml-2">— {e.documentTitle}</span>}
        </blockquote>
      ))}
      <Button
        size="sm" variant="outline"
        onClick={() => verifyMutation.mutate({ eventId: event.id, verified: !event.verified })}
        disabled={verifyMutation.isPending}
      >
        {event.verified ? t("geo.events.unverifyButton") : t("geo.events.verifyButton")}
      </Button>
    </div>
  );
}

function GeoIndicatorDetailPage() {
  const { themeId, indicatorId } = Route.useParams();
  const { t } = useLanguage();

  const { data: indicatorsData } = useGeoIndicators(themeId);
  const indicator = indicatorsData?.indicators.find((i) => i.id === indicatorId);

  const { data: obsData } = useGeoIndicatorObservations(indicatorId);
  const observations = obsData?.observations ?? [];
  const obs = observations[0];
  const status = deriveIndicatorStatus(obs);
  const recalcMutation = useGeoRecalculateIndicator();

  // Phase2B: 鮮度・Alert（mvp_enabled指標のみ集約するIndicator Monitor APIを再利用。
  // 専用のsingle-indicator向けエンドポイントは新設しない）
  const monitorQuery = useGeoIndicatorMonitor();
  const monitorRow = monitorQuery.data?.indicators.find((r) => r.id === indicatorId);

  // 検知イベント方式(event_count_window)の指標は、indicatorCodeが一致するEventを表示する
  const { data: eventsData } = useGeoEvents({ themeId });
  const relatedEvents = (eventsData?.events ?? []).filter((e) => e.indicatorCode === indicator?.code);

  // 文書ベースの分類方式(qualitative_llm_state/regex_level_from_document等)の指標は、
  // Eventを経由しないため、根拠となった文書そのものを情報源URLの一致で表示する
  const { data: docsData } = useGeoDocuments(themeId);
  const relatedDocuments = (docsData?.documents ?? []).filter((d) => d.url && d.url === indicator?.sourceUrl);

  if (!indicator) {
    return (
      <>
        <TopBar breadcrumb={[{ label: t("nav.geoSituations"), href: "/geo/monitoring" }]} />
        <main className="flex-1 max-w-[1080px] w-full mx-auto px-8 py-8">
          <p className="text-muted-foreground">...</p>
        </main>
      </>
    );
  }

  return (
    <>
      <TopBar
        breadcrumb={[
          { label: t("nav.geoSituations"), href: "/geo/monitoring" },
          { label: t("geo.tabs.overview"), href: `/geo/theme/${themeId}` },
          { label: indicator.code },
        ]}
      />
      <main className="flex-1 max-w-[1080px] w-full mx-auto px-8 py-8 space-y-6">
        <Link
          to="/geo/theme/$themeId" params={{ themeId }}
          className="text-sm text-muted-foreground flex items-center gap-1 hover:text-foreground w-fit"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> {t("geo.tabs.overview")}
        </Link>

        <section className="border-b border-border pb-5 space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            <IndicatorStatusDot status={status} />
            <h1 className="text-editorial text-3xl">{indicator.code}: {indicator.name}</h1>
            {monitorRow && <IndicatorFreshnessBadge freshness={monitorRow.freshness} />}
            {monitorRow && <IndicatorAlertBadge alert={monitorRow.latestAlert} />}
          </div>
          {indicator.description && (
            <p className="text-sm text-muted-foreground leading-relaxed">{indicator.description}</p>
          )}
          {indicator.sourceName && (
            <p className="text-xs text-muted-foreground flex items-center gap-1">
              {t("geo.indicatorDetail.source")}: {indicator.sourceName}
              {indicator.sourceUrl && (
                <a href={indicator.sourceUrl} target="_blank" rel="noreferrer" className="underline inline-flex items-center gap-0.5">
                  <ExternalLink className="h-3 w-3" />
                </a>
              )}
            </p>
          )}
        </section>

        <div className="card-paper rounded-lg p-5 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-editorial text-lg">{t("geo.tabs.overview")}</h2>
            <Button
              size="sm" variant="outline"
              onClick={() => recalcMutation.mutate(indicator.id)}
              disabled={recalcMutation.isPending}
            >
              {recalcMutation.isPending ? t("geo.overview.recalculating") : t("geo.overview.recalcButton")}
            </Button>
          </div>
          {!obs ? (
            <p className="text-sm text-muted-foreground">{t("geo.overview.noObservation")}</p>
          ) : obs.insufficientData ? (
            <Badge variant="secondary">{t("geo.overview.insufficientData")}</Badge>
          ) : (
            <div className="space-y-2">
              <div className="flex items-baseline gap-2 flex-wrap">
                <span className="text-3xl font-semibold tabular-nums">{obs.qualitativeLabel ?? obs.rawValue}</span>
                {indicator.unit && !obs.qualitativeLabel && (
                  <span className="text-xs text-muted-foreground">{indicator.unit}</span>
                )}
                <DeviationIndicator deviation={obs.deviation} />
                {obs.isMock && <Badge variant="outline">{t("geo.overview.mockBadge")}</Badge>}
              </div>
              <GeoIndicatorTrendChart observations={observations} />
              {obs.rationale && (
                <p className="text-xs text-muted-foreground border-l-2 border-border pl-3 italic">{obs.rationale}</p>
              )}
              <div className="text-xs text-muted-foreground space-x-3 tabular-nums">
                {obs.baseline30d !== null && <span>baseline30d: {obs.baseline30d.toFixed(1)}</span>}
                {obs.confidence !== null && <span>confidence: {obs.confidence.toFixed(2)}</span>}
              </div>
              <Badge variant="outline" className="text-[10px]">{t("geo.overview.draftBadge")}</Badge>
            </div>
          )}
        </div>

        {relatedEvents.length > 0 ? (
          <div className="space-y-3">
            <h2 className="text-editorial text-lg">{t("geo.tabs.events")}</h2>
            <p className="text-xs text-muted-foreground">{t("geo.indicatorDetail.eventsExplain")}</p>
            {relatedEvents.map((e) => <EventWithEvidence key={e.id} event={e} />)}
          </div>
        ) : relatedDocuments.length > 0 ? (
          <div className="space-y-3">
            <h2 className="text-editorial text-lg">{t("geo.tabs.documents")}</h2>
            <p className="text-xs text-muted-foreground">{t("geo.indicatorDetail.documentsExplain")}</p>
            {relatedDocuments.map((d) => (
              <div key={d.id} className="card-paper rounded-lg p-4 space-y-1.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{d.title}</span>
                  {d.isMock && <Badge variant="outline">{t("geo.overview.mockBadge")}</Badge>}
                </div>
                <p className="text-xs text-muted-foreground">{t("geo.documents.fetchedAt")}: {d.fetchedAt}</p>
                <p className="text-sm text-muted-foreground line-clamp-3">{d.extractedTextPreview}</p>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">{t("geo.indicatorDetail.empty")}</p>
        )}
      </main>
    </>
  );
}
