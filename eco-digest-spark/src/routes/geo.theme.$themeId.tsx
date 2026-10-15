import { createFileRoute } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { TopBar } from "@/components/top-bar";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { GeoOverviewTab } from "@/components/geo/geo-overview-tab";
import { GeoDocumentsTab } from "@/components/geo/geo-documents-tab";
import { GeoRelationshipsTab } from "@/components/geo/geo-relationships-tab";
import { GeoAiChatPanel } from "@/components/geo/geo-ai-chat";
import { GeoCard, GeoCardBody, GeoCardHeader } from "@/components/geo/geo-card";
import { GeoAssessmentCard } from "@/components/geo/geo-assessment-card";
import { GeoSignalBar } from "@/components/geo/geo-signal-bar";
import { GeoTimeline, type GeoTimelineEventData, type GeoTimelineOrigin } from "@/components/geo/geo-timeline";
import {
  GeoExpertMainScenario, GeoExpertStakeholder,
  useGeoEvents, useGeoExpertRequest, useGeoExpertRequests, useGeoIndicatorObservations,
  useGeoIndicators, useGeoOverview, useGeoThemes,
} from "@/lib/api";
import { useLanguage } from "@/lib/i18n";
import { deriveIndicatorStatus, IndicatorStatusDot } from "@/components/geo/geo-indicator-status";

export const Route = createFileRoute("/geo/theme/$themeId")({
  component: GeoThemeDashboardPage,
});

// Report Studio由来のgeo_events（event_type="report_studio_update"/"report_studio_watchpoint_alert"）を
// 組織内で観測された実世界イベントと視覚的に区別するためのOrigin Badge。unknown_fieldsに
// geo_report_monitoring_serviceが書き込んだreport_studio_*キーからDeep Link先を組み立てる
// （report_studio_project_id/run_id/signal_id/narrative_idが必須、無ければBadgeを出さない）。
function originForReportStudioEvent(
  eventType: string | null,
  unknownFields: Record<string, string>,
): GeoTimelineOrigin | undefined {
  if (eventType !== "report_studio_update" && eventType !== "report_studio_watchpoint_alert") return undefined;
  const projectId = unknownFields["report_studio_project_id"];
  if (!projectId) return undefined;
  const label = eventType === "report_studio_watchpoint_alert" ? "Watchpoint Alert" : "Report Update";
  const runId = unknownFields["report_studio_run_id"];
  const narrativeId = unknownFields["report_studio_narrative_id"];
  return {
    label: `Report Studio: ${label}`,
    variant: eventType === "report_studio_watchpoint_alert" ? "default" : "secondary",
    link: {
      to: "/geo/report-studio/$projectId",
      params: { projectId },
      search: {
        tab: "assessment",
        ...(runId ? { runId } : {}),
        ...(narrativeId ? { narrativeId } : {}),
      },
    },
  };
}

// そのテーマに紐づく直近のExpert AIリクエストの完全なレスポンスを取得する。
// 一覧API(useGeoExpertRequests)はresponse本文を含まない軽量版のため、
// 最新1件のidを特定してから詳細API(useGeoExpertRequest)で本文を取得する2段構え
// （テーマ数分のHookループにはならない、1テーマにつき常に2回の呼び出しで完結する）。
function useLatestThemeAssessment(themeId: string) {
  const listQuery = useGeoExpertRequests(themeId);
  const latest = useMemo(() => {
    const requests = listQuery.data?.requests ?? [];
    if (requests.length === 0) return null;
    return [...requests].sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1))[0];
  }, [listQuery.data]);
  const detailQuery = useGeoExpertRequest(latest?.requestId ?? "");
  return { latest, detail: detailQuery.data, isLoading: listQuery.isLoading || (!!latest && detailQuery.isLoading) };
}

function IndicatorRow({ themeId: _themeId, indicatorId, code, name }: { themeId: string; indicatorId: string; code: string; name: string }) {
  const { data } = useGeoIndicatorObservations(indicatorId);
  const obs = data?.observations?.[0];
  const status = deriveIndicatorStatus(obs);
  return (
    <tr className="border-b border-border last:border-0">
      <td className="py-2.5 pr-3">
        <div className="flex items-center gap-2">
          <IndicatorStatusDot status={status} />
          <span className="font-medium text-sm">{code}</span>
        </div>
        <div className="text-xs text-muted-foreground">{name}</div>
      </td>
      <td className="py-2.5 pr-3 text-sm tabular-nums">
        {obs?.qualitativeLabel ?? obs?.rawValue ?? "—"}
      </td>
      <td className="py-2.5 w-40">
        <GeoSignalBar value={obs?.confidence != null ? obs.confidence * 100 : 0} />
      </td>
    </tr>
  );
}

function ScenarioDetailDialog({
  scenario,
  open,
  onOpenChange,
}: {
  scenario: GeoExpertMainScenario | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Scenario</DialogTitle>
        </DialogHeader>
        {scenario && (
          <div className="space-y-3 text-sm">
            <p>{scenario.description}</p>
            <p className="text-xs text-muted-foreground">Confidence: {scenario.confidence}</p>
            {scenario.supporting_evidence && scenario.supporting_evidence.length > 0 && (
              <ul className="list-disc list-inside text-xs text-muted-foreground space-y-1">
                {scenario.supporting_evidence.map((e, i) => <li key={i}>{e}</li>)}
              </ul>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function StakeholderDetailDialog({
  stakeholder,
  open,
  onOpenChange,
}: {
  stakeholder: GeoExpertStakeholder | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{stakeholder?.actor}</DialogTitle>
        </DialogHeader>
        {stakeholder && (
          <dl className="space-y-2.5 text-sm">
            {stakeholder.purpose && (<div><dt className="text-xs text-muted-foreground">Purpose</dt><dd>{stakeholder.purpose}</dd></div>)}
            {stakeholder.constraints_fears && (<div><dt className="text-xs text-muted-foreground">Constraints / Fears</dt><dd>{stakeholder.constraints_fears}</dd></div>)}
            {stakeholder.red_lines && (<div><dt className="text-xs text-muted-foreground">Red Lines</dt><dd>{stakeholder.red_lines}</dd></div>)}
            {stakeholder.key_relationships && (<div><dt className="text-xs text-muted-foreground">Key Relationships</dt><dd>{stakeholder.key_relationships}</dd></div>)}
          </dl>
        )}
      </DialogContent>
    </Dialog>
  );
}

function GeoThemeDashboardPage() {
  const { themeId } = Route.useParams();
  const { lang, t } = useLanguage();
  const { data, isLoading } = useGeoThemes();
  const theme = data?.themes.find((th) => th.id === themeId);

  const { data: indicatorsData } = useGeoIndicators(themeId);
  const indicators = (indicatorsData?.indicators ?? []).filter((i) => i.mvpEnabled);

  const { data: eventsData } = useGeoEvents({ themeId });
  const { data: overview } = useGeoOverview(themeId);

  const { latest, detail: assessment } = useLatestThemeAssessment(themeId);

  const timelineEvents: GeoTimelineEventData[] = (eventsData?.events ?? [])
    .filter((e) => e.startAt)
    .sort((a, b) => (a.startAt! < b.startAt! ? 1 : -1))
    .slice(0, 12)
    .map((e) => ({
      id: e.id,
      date: e.startAt!,
      title: `${e.actor ?? "?"} — ${e.action ?? e.eventType ?? "?"}`,
      description: e.location ?? undefined,
      emphasis: e.verified,
      origin: originForReportStudioEvent(e.eventType, e.unknownFields),
    }));

  const [openScenario, setOpenScenario] = useState<GeoExpertMainScenario | null>(null);
  const [openStakeholder, setOpenStakeholder] = useState<GeoExpertStakeholder | null>(null);

  const scenarios: GeoExpertMainScenario[] = assessment?.response
    ? [assessment.response.main_scenario, ...(assessment.response.alternative_scenarios ?? []).map((s) => ({ description: s.description, confidence: "—" }))]
    : [];
  const stakeholders: GeoExpertStakeholder[] = assessment?.response?.stakeholders ?? [];

  return (
    <>
      <TopBar
        breadcrumb={[
          { label: t("nav.geoMonitoring"), href: "/geo/monitoring" },
          { label: theme ? (lang === "en" && theme.nameEn ? theme.nameEn : theme.nameJa) : "..." },
        ]}
      />
      <main className="flex-1 max-w-[1440px] w-full mx-auto px-8 py-8 space-y-6">
        <section className="border-b border-border pb-5">
          <h1 className="geo-heading text-4xl">
            {isLoading ? "..." : theme ? (lang === "en" && theme.nameEn ? theme.nameEn : theme.nameJa) : t("geo.theme.notFound")}
          </h1>
          {theme?.description && <p className="text-sm text-muted-foreground mt-2">{theme.description}</p>}
        </section>

        {!isLoading && !theme ? (
          <p className="text-sm text-muted-foreground">{t("geo.theme.notFound")}</p>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-6 items-start">
            <div className="space-y-6 min-w-0">
              {/* Latest Assessment — 実データが無い場合は捏造せず空状態を表示する */}
              {assessment ? (
                <GeoAssessmentCard
                  response={assessment.response}
                  sourceLabel={`Expert AI · ${assessment.createdAt.slice(0, 10)}`}
                />
              ) : (
                <GeoCard>
                  <GeoCardBody className="pt-6 text-sm text-muted-foreground">
                    {t("geo.theme.noAssessment")}
                  </GeoCardBody>
                </GeoCard>
              )}

              <GeoCard>
                <GeoCardHeader title="Key Indicators" />
                <GeoCardBody>
                  {indicators.length === 0 ? (
                    <p className="text-sm text-muted-foreground">—</p>
                  ) : (
                    <table className="w-full">
                      <tbody>
                        {indicators.map((i) => (
                          <IndicatorRow key={i.id} themeId={themeId} indicatorId={i.id} code={i.code} name={i.name} />
                        ))}
                      </tbody>
                    </table>
                  )}
                </GeoCardBody>
              </GeoCard>

              <div className="grid sm:grid-cols-2 gap-6">
                <GeoCard>
                  <GeoCardHeader title="Event Timeline" meta="Verified geo events" />
                  <GeoCardBody>
                    <GeoTimeline events={timelineEvents} />
                  </GeoCardBody>
                </GeoCard>
                <GeoCard>
                  <GeoCardHeader title="Analysis / Knowledge Updates" meta="System audit log" />
                  <GeoCardBody>
                    {(overview?.recentAuditLogs ?? []).length === 0 ? (
                      <p className="text-sm text-muted-foreground">—</p>
                    ) : (
                      <ul className="space-y-2 text-sm">
                        {overview!.recentAuditLogs.slice(0, 8).map((log) => (
                          <li key={log.id} className="flex items-baseline justify-between gap-2">
                            <span className="truncate">{log.action}</span>
                            <span className="text-xs text-muted-foreground flex-shrink-0">{log.createdAt.slice(0, 16).replace("T", " ")}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </GeoCardBody>
                </GeoCard>
              </div>

              <div className="grid sm:grid-cols-2 gap-6">
                <GeoCard>
                  <GeoCardHeader
                    title="Scenario Monitor"
                    meta={latest ? `From Expert AI · ${latest.createdAt.slice(0, 10)}` : undefined}
                  />
                  <GeoCardBody className="space-y-2">
                    {scenarios.length === 0 ? (
                      <p className="text-sm text-muted-foreground">{t("geo.theme.noAssessment")}</p>
                    ) : (
                      scenarios.map((s, i) => (
                        <button
                          key={i}
                          onClick={() => setOpenScenario(s)}
                          className="w-full text-left rounded-md border border-border px-3 py-2 hover:bg-accent/40 transition-colors"
                        >
                          <p className="text-sm line-clamp-2">{s.description}</p>
                          <p className="text-xs text-muted-foreground mt-0.5">Confidence: {s.confidence}</p>
                        </button>
                      ))
                    )}
                  </GeoCardBody>
                </GeoCard>
                <GeoCard>
                  <GeoCardHeader
                    title="Stakeholder Movement"
                    meta={latest ? `From Expert AI · ${latest.createdAt.slice(0, 10)}` : undefined}
                  />
                  <GeoCardBody className="space-y-2">
                    {stakeholders.length === 0 ? (
                      <p className="text-sm text-muted-foreground">{t("geo.theme.noAssessment")}</p>
                    ) : (
                      stakeholders.map((s, i) => (
                        <button
                          key={i}
                          onClick={() => setOpenStakeholder(s)}
                          className="w-full text-left rounded-md border border-border px-3 py-2 hover:bg-accent/40 transition-colors"
                        >
                          <p className="text-sm font-medium">{s.actor}</p>
                          {s.purpose && <p className="text-xs text-muted-foreground line-clamp-1">{s.purpose}</p>}
                        </button>
                      ))
                    )}
                  </GeoCardBody>
                </GeoCard>
              </div>

              <Tabs defaultValue="overview">
                <TabsList>
                  <TabsTrigger value="overview">{t("geo.tabs.overview")}</TabsTrigger>
                  <TabsTrigger value="documents">{t("geo.tabs.documents")}</TabsTrigger>
                  <TabsTrigger value="relationships">{t("geo.tabs.relationships")}</TabsTrigger>
                </TabsList>
                <TabsContent value="overview"><GeoOverviewTab themeId={themeId} /></TabsContent>
                <TabsContent value="documents"><GeoDocumentsTab themeId={themeId} /></TabsContent>
                <TabsContent value="relationships"><GeoRelationshipsTab themeId={themeId} /></TabsContent>
              </Tabs>
            </div>

            <div className="lg:sticky lg:top-20">
              <GeoCard className="overflow-hidden">
                <GeoCardHeader title={`Ask about ${theme ? (lang === "en" && theme.nameEn ? theme.nameEn : theme.nameJa) : "..."}`} />
                <div className="h-[420px]">
                  <GeoAiChatPanel compact themeId={themeId} />
                </div>
              </GeoCard>
            </div>
          </div>
        )}
      </main>

      <ScenarioDetailDialog scenario={openScenario} open={!!openScenario} onOpenChange={(o) => !o && setOpenScenario(null)} />
      <StakeholderDetailDialog stakeholder={openStakeholder} open={!!openStakeholder} onOpenChange={(o) => !o && setOpenStakeholder(null)} />
    </>
  );
}
