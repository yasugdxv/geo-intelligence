import { Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useState, type ReactNode } from "react";
import { Bell, ExternalLink, FileText, RefreshCw } from "lucide-react";
import { TopBar } from "@/components/top-bar";
import { DataFreshnessBadge } from "@/components/geo/data-freshness";
import { MonitoringEditDialog } from "@/components/geo/monitoring-edit-dialog";
import {
  MonitoringStateBadge,
  CoveragePanel,
  NoFreshDataNote,
  RegionBreakdown,
  useApplyMonitoringSources,
  useRelativeTime,
} from "@/components/geo/monitoring-common";
import { SourceSelector, SourceSetSummary } from "@/components/geo/source-selector";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
  useAcknowledgeAlert,
  useCreateResearchProject,
  useMonitoringDetail,
  useRefreshMonitoringDetail,
  type MonitoringAlert,
  type MonitoringDetail,
  type RecommendInput,
} from "@/lib/geoSourceLibraryApi";
import {
  useHandoffThemeCandidateToExpertAI,
  useThemeCandidateHandoffPreview,
} from "@/lib/geoThemeCandidateApi";
import { clearPickContext, getPickContext, type SourcePickContext } from "@/lib/sourcePickStore";
import { cn } from "@/lib/utils";

const SEVERITY_STYLE: Record<string, string> = {
  high: "bg-red-100 text-red-800 border-red-300",
  medium: "bg-amber-100 text-amber-800 border-amber-300",
  low: "bg-muted text-muted-foreground border-border",
};

function Section({
  title,
  children,
  testId,
}: {
  title: string;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <section className="geo-card p-5 space-y-3" data-testid={testId}>
      <h2 className="geo-heading text-xl">{title}</h2>
      {children}
    </section>
  );
}

function AlertCard({ alert }: { alert: MonitoringAlert }) {
  const { t } = useLanguage();
  const relTime = useRelativeTime();
  const ack = useAcknowledgeAlert();
  const unread = !alert.acknowledged_at;
  return (
    <div
      className={cn(
        "rounded-md border p-3 space-y-1.5",
        unread ? "border-red-300 bg-red-50/60 dark:bg-red-950/20" : "border-border/60 opacity-75",
      )}
      data-testid="monitoring-alert"
      data-unread={unread}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={cn(
            "rounded border px-1.5 py-0.5 text-[11px] font-medium",
            SEVERITY_STYLE[alert.severity] ?? SEVERITY_STYLE.low,
          )}
        >
          {t(`geo.monitoringThemes.severity.${alert.severity}`)}
        </span>
        <span className={cn("text-sm", unread ? "font-semibold" : "font-medium")}>
          {alert.title}
        </span>
        {unread && <Badge variant="destructive">{t("geo.monitoringThemes.alert.unread")}</Badge>}
      </div>
      {/* なぜAlertになったか（必ず表示） */}
      <p className="text-sm">
        <span className="text-xs font-medium text-muted-foreground">
          {t("geo.monitoringThemes.alert.reason")}:{" "}
        </span>
        {alert.reason}
      </p>
      {/* Candidate Detection → Context Validation の監査情報（DB-8適用後に保存される） */}
      {alert.validation_reason && (
        <p className="text-xs text-muted-foreground" data-testid="alert-validation">
          <span className="font-medium">{t("geo.monitoringThemes.alertValidation")}: </span>
          {alert.validation_reason}
          {alert.candidate_rule && (
            <span className="ml-1 opacity-70">
              ({t("geo.monitoringThemes.alertRule")}: {alert.candidate_rule})
            </span>
          )}
        </p>
      )}
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>
          {relTime(alert.detected_at)} ·{" "}
          {t("geo.monitoringThemes.alert.related", { count: alert.related_signal_ids.length })}
        </span>
        {unread && (
          <Button
            size="sm"
            variant="outline"
            disabled={ack.isPending}
            onClick={() => ack.mutate(alert.alert_id)}
          >
            {t("geo.monitoringThemes.alert.ack")}
          </Button>
        )}
      </div>
      {ack.isError && <p className="text-xs text-destructive">{(ack.error as Error).message}</p>}
    </div>
  );
}

// Expert AIへの引き継ぎ確認ダイアログ（Theme Discoveryのhandoff-previewを簡易に踏襲）
function HandoffDialog({
  candidateId,
  open,
  onOpenChange,
}: {
  candidateId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const preview = useThemeCandidateHandoffPreview();
  const handoff = useHandoffThemeCandidateToExpertAI();

  useEffect(() => {
    if (!open) return;
    preview.mutate({ candidateId, investigationQuestions: [] });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, candidateId]);

  const confirm = () =>
    handoff.mutate(
      { candidateId, investigationQuestions: [] },
      {
        onSuccess: (r) => {
          onOpenChange(false);
          navigate({
            to: "/geo/ios/analysis/$sessionId",
            params: { sessionId: r.analysis_session_id },
          });
        },
      },
    );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("geo.monitoringThemes.handoff.title")}</DialogTitle>
        </DialogHeader>
        {preview.isPending ? (
          <p className="text-sm text-muted-foreground">...</p>
        ) : preview.isError ? (
          <p className="text-sm text-destructive">{(preview.error as Error).message}</p>
        ) : preview.data ? (
          <div className="space-y-3 text-sm">
            <div>
              <p className="text-xs font-medium text-muted-foreground">
                {t("geo.monitoringThemes.handoff.sessionTitle")}
              </p>
              <p>{preview.data.session_title}</p>
            </div>
            {preview.data.core_question && (
              <div>
                <p className="text-xs font-medium text-muted-foreground">
                  {t("geo.monitoringThemes.handoff.question")}
                </p>
                <p>{preview.data.core_question}</p>
              </div>
            )}
            {preview.data.existing_session_id && (
              <p className="text-xs text-muted-foreground">
                {t("geo.monitoringThemes.handoff.existing")}
              </p>
            )}
          </div>
        ) : null}
        {handoff.isError && (
          <p className="text-sm text-destructive">{(handoff.error as Error).message}</p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("geo.monitoringThemes.handoff.cancel")}
          </Button>
          <Button onClick={confirm} disabled={handoff.isPending || preview.isPending}>
            {handoff.isPending
              ? t("geo.monitoringThemes.handoff.starting")
              : t("geo.monitoringThemes.handoff.confirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DetailBody({ detail, id }: { detail: MonitoringDetail; id: string }) {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const relTime = useRelativeTime();
  const th = detail.monitoring_theme;
  const a = detail.assessment;
  const wp = detail.watchpoints;
  const noFresh = a.state === "no_fresh_data";

  const [selectorOpen, setSelectorOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [handoffOpen, setHandoffOpen] = useState(false);
  const [pickCtx, setPickCtx] = useState<SourcePickContext | null>(() => {
    const ctx = getPickContext();
    return ctx && ctx.returnTo.replace(/\/$/, "") === `/geo/monitoring-themes/${id}` ? ctx : null;
  });
  const sourceApply = useApplyMonitoringSources(th);
  const research = useCreateResearchProject();

  const sourceIds = detail.sources.map((s) => s.source_id);
  const recommendInput: RecommendInput = th.theme_candidate_id
    ? { candidate_id: th.theme_candidate_id }
    : {
        title: th.display_name,
        actors: th.actors,
        regions: th.regions,
        themes: th.tags,
        keywords: th.keywords,
      };

  const applySources = async (ids: string[]) => {
    try {
      await sourceApply.apply(ids);
    } catch {
      // エラーは Source Set セクションに表示する
    }
  };

  const startResearch = () =>
    research.mutate(
      {
        theme: th.display_name,
        query: th.monitoring_criteria ?? undefined,
        source_ids: sourceIds,
        source_set_id: th.source_set_id,
        origin: "monitoring",
      },
      {
        onSuccess: (r) =>
          navigate({
            to: "/geo/report-studio/$projectId",
            params: { projectId: r.project.project_id },
            search: { tab: "research" } as never,
          }),
      },
    );

  const topAlerts = detail.alerts.slice(0, 3);

  return (
    <>
      {pickCtx && (
        <div
          className="geo-card p-4 flex flex-wrap items-center justify-between gap-2"
          data-testid="monitoring-pick-return"
        >
          <p className="text-sm">
            {t("geo.monitoringThemes.pick.returned", { count: pickCtx.selectedIds.length })}
          </p>
          <div className="flex gap-2">
            <Button
              size="sm"
              disabled={sourceApply.isPending}
              onClick={async () => {
                await applySources(pickCtx.selectedIds);
                clearPickContext();
                setPickCtx(null);
              }}
            >
              {t("geo.monitoringThemes.pick.replace")}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                clearPickContext();
                setPickCtx(null);
              }}
            >
              {t("geo.monitoringThemes.pick.discard")}
            </Button>
          </div>
        </div>
      )}

      {/* 1. 現在の状態 */}
      <Section title={t("geo.monitoringThemes.sec.state")} testId="monitoring-sec-state">
        <div className="flex flex-wrap items-center gap-3">
          <MonitoringStateBadge state={a.state} className="text-sm px-2.5 py-1" />
          <DataFreshnessBadge freshness={a.freshness} showNote />
          <span className="text-xs text-muted-foreground">
            {t("geo.monitoringThemes.evaluatedAt", { time: relTime(a.evaluated_at) })}
          </span>
        </div>
        {noFresh ? (
          <NoFreshDataNote lastDataAt={a.freshness.last_data_at} />
        ) : (
          <p className="text-sm">{a.summary}</p>
        )}
        <CoveragePanel coverage={a.coverage} note={a.coverage_note} />
        <RegionBreakdown breakdown={a.region_breakdown} />
        {a.unlinked_note && <p className="text-xs text-muted-foreground">{a.unlinked_note}</p>}
      </Section>

      {/* 2. 前回から何が変わったか */}
      <Section title={t("geo.monitoringThemes.sec.change")} testId="monitoring-sec-change">
        {noFresh ? (
          <p className="text-sm font-medium text-geo-amber">
            {t("geo.monitoringThemes.undeterminable")}
          </p>
        ) : (
          <>
            <p className="text-sm">
              {a.latest_change ??
                (a.state === "no_new_articles"
                  ? t("geo.monitoringThemes.changeNoNew")
                  : a.state === "partial_coverage"
                    ? t("geo.coverage.changeNoneInScope")
                    : t("geo.monitoringThemes.changeNone"))}
            </p>
            {topAlerts.length > 0 && (
              <ul className="list-disc pl-5 text-sm space-y-0.5">
                {topAlerts.map((al) => (
                  <li key={al.alert_id}>
                    {al.title}
                    <span className="text-xs text-muted-foreground"> — {al.reason}</span>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </Section>

      {/* 3. Alert */}
      <Section title={t("geo.monitoringThemes.sec.alerts")} testId="monitoring-sec-alerts">
        {detail.alerts.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {noFresh
              ? t("geo.monitoringThemes.alertsEmptyUndeterminable")
              : t("geo.monitoringThemes.alertsEmpty")}
          </p>
        ) : (
          <div className="space-y-2">
            {detail.alerts.map((al) => (
              <AlertCard key={al.alert_id} alert={al} />
            ))}
          </div>
        )}
        {/* 文脈検証で除外した候補（否定・仮定・予測など）。なぜAlertにならなかったかを確認できる */}
        {detail.assessment.validation && detail.assessment.validation.candidates > 0 && (
          <div className="mt-3 space-y-1.5 border-t pt-3" data-testid="alert-validation-summary">
            <p className="text-xs font-medium">{t("geo.monitoringThemes.validation.title")}</p>
            <p className="text-xs text-muted-foreground">
              {t("geo.monitoringThemes.validation.summary", {
                candidates: detail.assessment.validation.candidates,
                valid: detail.assessment.validation.valid,
              })}
            </p>
            {detail.assessment.validation.excluded.length > 0 && (
              <details className="text-xs">
                <summary className="cursor-pointer text-muted-foreground">
                  {t("geo.monitoringThemes.validation.excluded")} (
                  {detail.assessment.validation.excluded.length})
                </summary>
                <ul className="mt-1 space-y-1">
                  {detail.assessment.validation.excluded.map((x, i) => (
                    <li key={i} className="rounded border p-1.5">
                      <Badge variant="outline" className="mr-1 text-[10px]">
                        {t(`geo.monitoringThemes.validation.label.${x.label}`)}
                      </Badge>
                      {x.title}
                      <span className="block text-muted-foreground">{x.reason}</span>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}
      </Section>

      {/* 4. Timeline */}
      <Section title={t("geo.monitoringThemes.sec.timeline")} testId="monitoring-sec-timeline">
        {detail.timeline.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("geo.monitoringThemes.timelineEmpty")}</p>
        ) : (
          <ul className="space-y-1.5">
            {detail.timeline.map((ev, i) => (
              <li key={i} className="flex items-start gap-2 text-sm">
                {ev.kind === "alert" ? (
                  <Bell
                    className="h-4 w-4 mt-0.5 shrink-0 text-red-600"
                    aria-label={t("geo.monitoringThemes.kind.alert")}
                  />
                ) : (
                  <FileText
                    className="h-4 w-4 mt-0.5 shrink-0 text-muted-foreground"
                    aria-label={t("geo.monitoringThemes.kind.article")}
                  />
                )}
                <span className="w-24 shrink-0 text-xs text-muted-foreground pt-0.5">
                  {ev.at.slice(0, 10)}
                </span>
                <span className="min-w-0 flex-1">
                  {ev.url ? (
                    <a href={ev.url} target="_blank" rel="noreferrer" className="hover:underline">
                      {ev.title ?? ev.url}
                    </a>
                  ) : (
                    (ev.title ?? "-")
                  )}
                  {ev.source && (
                    <span className="text-xs text-muted-foreground"> · {ev.source}</span>
                  )}
                </span>
                {ev.severity && (
                  <span
                    className={cn(
                      "rounded border px-1.5 text-[11px]",
                      SEVERITY_STYLE[ev.severity] ?? SEVERITY_STYLE.low,
                    )}
                  >
                    {t(`geo.monitoringThemes.severity.${ev.severity}`)}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>

      {/* 5. Articles / Evidence */}
      <Section title={t("geo.monitoringThemes.sec.articles")} testId="monitoring-sec-articles">
        {detail.articles.length === 0 ? (
          <div className="space-y-1">
            <p className="text-sm text-muted-foreground">
              {t("geo.monitoringThemes.articles.empty")}
            </p>
            {a.freshness.status !== "fresh" && (
              <p className="text-sm text-geo-amber">
                {t("geo.monitoringThemes.articles.emptyStale")}
              </p>
            )}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("geo.monitoringThemes.articles.title")}</TableHead>
                  <TableHead>{t("geo.monitoringThemes.articles.source")}</TableHead>
                  <TableHead>{t("geo.monitoringThemes.articles.published")}</TableHead>
                  <TableHead>{t("geo.monitoringThemes.articles.url")}</TableHead>
                  <TableHead>{t("geo.monitoringThemes.articles.via")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {detail.articles.map((ar) => (
                  <TableRow key={ar.external_signal_id}>
                    <TableCell className="max-w-[360px]">{ar.article_title ?? "-"}</TableCell>
                    <TableCell className="text-xs">{ar.source ?? "-"}</TableCell>
                    <TableCell className="text-xs whitespace-nowrap">
                      {ar.published_at?.slice(0, 10) ?? "-"}
                    </TableCell>
                    <TableCell>
                      {ar.url ? (
                        <a
                          href={ar.url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                        >
                          {t("geo.monitoringThemes.articles.open")}
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      ) : (
                        "-"
                      )}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {ar.matched_via ?? "-"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </Section>

      {/* 6. Key Actors */}
      <Section title={t("geo.monitoringThemes.sec.actors")} testId="monitoring-sec-actors">
        {detail.key_actors.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("geo.monitoringThemes.actorsEmpty")}</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {detail.key_actors.map((ac) => (
              <Badge key={ac.name} variant="outline">
                {ac.name} ({ac.article_count})
              </Badge>
            ))}
          </div>
        )}
        {wp.actors.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
            <span>{t("geo.monitoringThemes.watchActors")}:</span>
            {wp.actors.map((x) => (
              <Badge key={x} variant="secondary">
                {x}
              </Badge>
            ))}
          </div>
        )}
      </Section>

      {/* 7. Source Set */}
      <Section title={t("geo.monitoringThemes.sec.sources")} testId="monitoring-sec-sources">
        {detail.source_set?.archived_at && (
          <p className="mb-2 rounded-md border border-geo-amber/50 bg-geo-amber-soft p-2 text-xs text-geo-amber">
            {t("geo.monitoringThemes.sourceSetArchived")}
          </p>
        )}
        <SourceSetSummary
          names={detail.sources.map((s) => s.name)}
          count={detail.sources.length}
          onChange={() => setSelectorOpen(true)}
        />
        {sourceApply.isPending && <p className="text-xs text-muted-foreground">...</p>}
        {sourceApply.error && (
          <p className="text-xs text-destructive">{sourceApply.error.message}</p>
        )}
        {detail.sources.length > 0 && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("geo.monitoringThemes.sources.name")}</TableHead>
                <TableHead>{t("geo.monitoringThemes.sources.mediaType")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {detail.sources.map((s) => (
                <TableRow key={s.source_id}>
                  <TableCell className="text-sm">
                    {s.name}
                    {s.archived_at && (
                      <Badge variant="outline" className="ml-2 text-[10px]">
                        {t("geo.monitoringThemes.sources.archived")}
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {s.media_type ?? "-"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Section>

      {/* 8. Watchpoints */}
      <Section
        title={t("geo.monitoringThemes.sec.watchpoints")}
        testId="monitoring-sec-watchpoints"
      >
        <dl className="grid grid-cols-1 sm:grid-cols-[160px_1fr] gap-x-4 gap-y-2 text-sm">
          <dt className="text-xs text-muted-foreground">
            {t("geo.monitoringThemes.watch.keywords")}
          </dt>
          <dd className="flex flex-wrap gap-1.5">
            {wp.keywords.length ? (
              wp.keywords.map((k) => (
                <Badge key={k} variant="outline">
                  {k}
                </Badge>
              ))
            ) : (
              <span className="text-muted-foreground">{t("geo.monitoringThemes.none")}</span>
            )}
          </dd>
          <dt className="text-xs text-muted-foreground">
            {t("geo.monitoringThemes.watch.actors")}
          </dt>
          <dd className="flex flex-wrap gap-1.5">
            {wp.actors.length ? (
              wp.actors.map((k) => (
                <Badge key={k} variant="secondary">
                  {k}
                </Badge>
              ))
            ) : (
              <span className="text-muted-foreground">{t("geo.monitoringThemes.none")}</span>
            )}
          </dd>
          <dt className="text-xs text-muted-foreground">
            {t("geo.monitoringThemes.watch.criteria")}
          </dt>
          <dd>{wp.criteria ?? t("geo.monitoringThemes.none")}</dd>
          <dt className="text-xs text-muted-foreground">
            {t("geo.monitoringThemes.watch.sensitivity")}
          </dt>
          <dd>{wp.sensitivity ? t(`geo.monitoringThemes.level.${wp.sensitivity}`) : "-"}</dd>
          <dt className="text-xs text-muted-foreground">
            {t("geo.monitoringThemes.watch.frequency")}
          </dt>
          <dd>{wp.frequency ? t(`geo.monitoringThemes.freq.${wp.frequency}`) : "-"}</dd>
        </dl>
      </Section>

      {/* 9. Actions */}
      <Section title={t("geo.monitoringThemes.sec.actions")} testId="monitoring-sec-actions">
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => setHandoffOpen(true)} disabled={!th.theme_candidate_id}>
            {t("geo.monitoringThemes.action.expert")}
          </Button>
          <Button variant="outline" onClick={startResearch} disabled={research.isPending}>
            {research.isPending
              ? t("geo.monitoringThemes.action.researching")
              : t("geo.monitoringThemes.action.research")}
          </Button>
          <Button variant="outline" onClick={() => setSelectorOpen(true)}>
            {t("geo.monitoringThemes.sources.change")}
          </Button>
          <Button variant="outline" onClick={() => setEditOpen(true)}>
            {t("geo.monitoringThemes.action.edit")}
          </Button>
        </div>
        {!th.theme_candidate_id && (
          <p className="text-xs text-muted-foreground">
            {t("geo.monitoringThemes.action.expertDisabled")}
          </p>
        )}
        {research.isError && (
          <p className="text-xs text-destructive">{(research.error as Error).message}</p>
        )}
      </Section>

      <SourceSelector
        open={selectorOpen}
        onOpenChange={setSelectorOpen}
        selectedIds={sourceIds}
        onApply={(ids) => void applySources(ids)}
        recommendInput={recommendInput}
        returnContext={{ returnTo: "/geo/monitoring-themes/" + id, label: th.display_name }}
      />
      <MonitoringEditDialog theme={th} open={editOpen} onOpenChange={setEditOpen} />
      {th.theme_candidate_id && (
        <HandoffDialog
          candidateId={th.theme_candidate_id}
          open={handoffOpen}
          onOpenChange={setHandoffOpen}
        />
      )}
    </>
  );
}

export function MonitoringDetailPage({ id }: { id: string }) {
  const { t } = useLanguage();
  const query = useMonitoringDetail(id);
  const refresh = useRefreshMonitoringDetail(id);
  const detail = query.data;
  const msg = query.error ? (query.error as Error).message : "";
  const notFound = query.isError && /404|not found/i.test(msg);

  return (
    <>
      <TopBar
        breadcrumb={[
          { label: t("nav.geoMonitoringThemes"), href: "/geo/monitoring-themes" },
          { label: detail?.monitoring_theme.display_name ?? id },
        ]}
      />
      <main className="flex-1 max-w-[1200px] w-full mx-auto px-8 py-8 space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-4">
          <div className="min-w-0">
            <Link
              to="/geo/monitoring-themes"
              className="text-xs text-muted-foreground hover:underline"
            >
              ← {t("geo.monitoringThemes.back")}
            </Link>
            <h1 className="geo-heading text-3xl truncate">
              {detail?.monitoring_theme.display_name ?? ""}
            </h1>
          </div>
          <Button onClick={() => refresh.mutate()} disabled={refresh.isPending || !detail}>
            <RefreshCw className={`h-4 w-4 mr-1 ${refresh.isPending ? "animate-spin" : ""}`} />
            {refresh.isPending
              ? t("geo.monitoringThemes.refreshing")
              : t("geo.monitoringThemes.refresh")}
          </Button>
        </div>

        {refresh.isError && (
          <p className="text-sm text-destructive" role="alert">
            {(refresh.error as Error).message}
          </p>
        )}
        {query.isLoading && (
          <p className="text-sm text-muted-foreground">{t("geo.monitoringThemes.loading")}</p>
        )}
        {notFound && (
          <p className="text-sm text-muted-foreground">{t("geo.monitoringThemes.notFound")}</p>
        )}
        {query.isError && !notFound && (
          <p className="text-sm text-destructive" role="alert">
            {t("geo.monitoringThemes.detailError", { message: msg })}
          </p>
        )}
        {detail && <DetailBody detail={detail} id={id} />}
      </main>
    </>
  );
}
