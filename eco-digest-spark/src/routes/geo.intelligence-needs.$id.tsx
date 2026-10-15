import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { TopBar } from "@/components/top-bar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { GeoCard, GeoCardBody, GeoCardHeader } from "@/components/geo/geo-card";
import { GeoStatusBadge, PRIORITY_SEVERITY } from "@/components/geo/geo-badge";
import {
  type GeoIntelligenceNeed,
  useAddResolutionReport,
  useDismissIntelligenceNeed,
  useIntelligenceNeedDetail,
  useUpdateIntelligenceNeed,
} from "@/lib/geoIntelligenceNeedsApi";
import { useLanguage } from "@/lib/i18n";

export const Route = createFileRoute("/geo/intelligence-needs/$id")({
  component: GeoIntelligenceNeedDetailPage,
});

// Report Studioが生成したIntelligence Need（geo_report_monitoring_service.
// select_intelligence_need_candidatesの5条件）の生成理由を人間可読なラベルへ変換する。
const REPORT_STUDIO_REQUEST_TYPE_LABEL: Record<string, string> = {
  watchpoint_possible_trigger: "Watchpointが部分的にTriggerした",
  central_assessment_confidence_low_insufficient_evidence: "Central Assessmentの根拠が不十分",
  missing_counterevidence: "Counterevidenceが不足",
  actor_intent_undeterminable: "Actorの意図が判断できない",
  missing_counterevidence_and_actor_context: "Counterevidence・Actorの意図解釈の両方が不足",
};

// Report Studio起源のIntelligence Needのみ、発生源バッジ＋元Reportへのdeep linkを表示する
// （source_contextはgeo_report_monitoring_service.create_gap_from_report_studioが書き込む、
// sql/2026-09-02_geo_report_studio_deep_link_schema.sql）。
function ReportStudioOriginBadge({ gap }: { gap: GeoIntelligenceNeed }) {
  const ctx = gap.source_context;
  if (gap.requested_by_app !== "report_studio" || !ctx?.project_id) return null;
  const reasonLabel = (gap.request_type && REPORT_STUDIO_REQUEST_TYPE_LABEL[gap.request_type]) || gap.request_type;
  return (
    <Link
      to="/geo/report-studio/$projectId"
      params={{ projectId: ctx.project_id }}
      search={{
        tab: "assessment",
        ...(ctx.run_id ? { runId: ctx.run_id } : {}),
        ...(ctx.narrative_id ? { narrativeId: ctx.narrative_id } : {}),
      }}
    >
      <Badge variant="secondary" className="cursor-pointer hover:bg-muted" title={reasonLabel ?? undefined}>
        Source: Report Studio{reasonLabel ? ` — ${reasonLabel}` : ""}
      </Badge>
    </Link>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <GeoCard>
      <GeoCardHeader title={title} />
      <GeoCardBody className="space-y-2 pt-0">{children}</GeoCardBody>
    </GeoCard>
  );
}

function GeoIntelligenceNeedDetailPage() {
  const { id } = Route.useParams();
  const { t } = useLanguage();
  const detailQuery = useIntelligenceNeedDetail(id);
  const updateMutation = useUpdateIntelligenceNeed();
  const addReportMutation = useAddResolutionReport();
  const dismissMutation = useDismissIntelligenceNeed();
  const [assignedTo, setAssignedTo] = useState("");
  const [resolutionFile, setResolutionFile] = useState<File | null>(null);
  const [resolvedBy, setResolvedBy] = useState("");
  const [resolutionNote, setResolutionNote] = useState("");
  const [showDismissForm, setShowDismissForm] = useState(false);
  const [dismissedBy, setDismissedBy] = useState("");
  const [dismissReason, setDismissReason] = useState("");

  const detail = detailQuery.data;
  if (!detail) {
    return (
      <>
        <TopBar
          breadcrumb={[
            { label: t("nav.geoIntelligenceNeeds"), href: "/geo/intelligence-needs" },
            { label: id },
          ]}
        />
        <main className="flex-1 max-w-[900px] w-full mx-auto px-8 py-8">
          <p className="text-sm text-muted-foreground">
            {detailQuery.isLoading ? "..." : t("geo.intelligenceNeeds.notFound")}
          </p>
        </main>
      </>
    );
  }

  return (
    <>
      <TopBar
        breadcrumb={[
          { label: t("nav.geoIntelligenceNeeds"), href: "/geo/intelligence-needs" },
          { label: detail.gap.gap_id.slice(0, 8) },
        ]}
      />
      <main className="flex-1 max-w-[900px] w-full mx-auto px-8 py-8 space-y-6">
        <section className="border-b border-border pb-5 space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            <GeoStatusBadge severity={PRIORITY_SEVERITY[detail.priority] ?? "neutral"}>{detail.priority}</GeoStatusBadge>
            <GeoStatusBadge severity="neutral">{detail.status}</GeoStatusBadge>
            {detail.gap.duplicate_of_gap_id && (
              <GeoStatusBadge severity="neutral">duplicate_candidate</GeoStatusBadge>
            )}
            <ReportStudioOriginBadge gap={detail.gap} />
          </div>
          <h1 className="geo-heading text-3xl">{detail.original_question}</h1>
        </section>

        <Section title={t("geo.intelligenceNeeds.businessContext")}>
          <p className="text-sm text-muted-foreground">{detail.business_context || "-"}</p>
        </Section>

        <Section title={t("geo.intelligenceNeeds.relatedRequests")}>
          <div className="space-y-1">
            {detail.related_requests.map((r) => (
              <div key={r.request_id} className="text-sm flex items-center gap-2">
                <Badge variant="outline">{r.requester_app}</Badge>
                <span>{r.question}</span>
                <span className="text-xs text-muted-foreground">
                  {new Date(r.created_at).toLocaleString()}
                </span>
              </div>
            ))}
            {detail.related_requests.length === 0 && (
              <p className="text-xs text-muted-foreground">-</p>
            )}
          </div>
        </Section>

        <Section title={t("geo.intelligenceNeeds.whatWeKnow")}>
          <ul className="text-sm list-disc list-inside space-y-1">
            {detail.what_we_know.map((ref, i) => (
              <li key={i}>
                {((ref as Record<string, unknown>).title as string) ?? JSON.stringify(ref)}
              </li>
            ))}
            {detail.what_we_know.length === 0 && <p className="text-xs text-muted-foreground">-</p>}
          </ul>
        </Section>

        <Section title={t("geo.intelligenceNeeds.whatIsMissing")}>
          <div className="flex flex-wrap gap-1">
            {detail.what_is_missing.map((cat) => (
              <Badge key={cat} variant="secondary">
                {cat}
              </Badge>
            ))}
          </div>
        </Section>

        <Section title={t("geo.intelligenceNeeds.recommendedResearch")}>
          <ul className="text-sm list-disc list-inside space-y-1">
            {detail.recommended_research.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </Section>

        <Section title={t("geo.intelligenceNeeds.assignmentAndStatus")}>
          <div className="flex flex-wrap items-center gap-2">
            <Select
              value={detail.status}
              onValueChange={(v) => updateMutation.mutate({ gapId: detail.gap.gap_id, status: v })}
            >
              <SelectTrigger className="w-44">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {/* 地政学アプリClooseout Phase6: "dismissed"はここでは選べない。
                    専用のdismiss経路（下記「却下」ボタン）のみで遷移させる
                    （resolved_by/resolution_note/監査アクションを一貫して記録するため）。 */}
                {["open", "researching", "resolved", "duplicate_candidate"].map(
                  (s) => (
                    <SelectItem key={s} value={s}>
                      {s}
                    </SelectItem>
                  ),
                )}
              </SelectContent>
            </Select>
            <Input
              value={assignedTo}
              onChange={(e) => setAssignedTo(e.target.value)}
              placeholder={detail.assigned_to ?? t("geo.intelligenceNeeds.assignedToPlaceholder")}
              className="w-48"
            />
            <Button
              size="sm"
              disabled={!assignedTo}
              onClick={() =>
                updateMutation.mutate({ gapId: detail.gap.gap_id, assigned_to: assignedTo })
              }
            >
              {t("geo.intelligenceNeeds.assign")}
            </Button>
            {detail.status !== "dismissed" && (
              <Button size="sm" variant="destructive" onClick={() => setShowDismissForm((v) => !v)}>
                却下
              </Button>
            )}
          </div>
          {showDismissForm && detail.status !== "dismissed" && (
            <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-border">
              <Input value={dismissedBy} onChange={(e) => setDismissedBy(e.target.value)}
                     placeholder="dismissed_by" className="w-40" />
              <Input value={dismissReason} onChange={(e) => setDismissReason(e.target.value)}
                     placeholder="却下理由（任意）" className="w-64" />
              <Button size="sm" variant="destructive" disabled={!dismissedBy || dismissMutation.isPending}
                      onClick={() => {
                        if (!dismissedBy) return;
                        dismissMutation.mutate(
                          { gapId: detail.gap.gap_id, dismissedBy, reason: dismissReason || undefined },
                          { onSuccess: () => setShowDismissForm(false) },
                        );
                      }}>
                {dismissMutation.isPending ? "処理中..." : "却下を確定"}
              </Button>
              {dismissMutation.isError && (
                <p className="text-xs text-destructive">{(dismissMutation.error as Error).message}</p>
              )}
            </div>
          )}
        </Section>

        <Section title={t("geo.intelligenceNeeds.resolution")}>
          <p className="text-sm text-muted-foreground">
            {detail.resolution.resolved_at
              ? new Date(detail.resolution.resolved_at).toLocaleString()
              : t("geo.intelligenceNeeds.unresolved")}
          </p>
          {detail.status !== "resolved" && (
            <div className="space-y-2 pt-2 border-t border-border">
              <p className="text-xs text-muted-foreground">
                {t("geo.intelligenceNeeds.addResolutionReport")}
              </p>
              <input
                type="file"
                accept=".pdf,.docx,.txt,.md"
                onChange={(e) => setResolutionFile(e.target.files?.[0] ?? null)}
                className="text-sm"
              />
              <Input
                value={resolvedBy}
                onChange={(e) => setResolvedBy(e.target.value)}
                placeholder={t("geo.intelligenceNeeds.resolvedByPlaceholder")}
                className="w-48"
              />
              <Input
                value={resolutionNote}
                onChange={(e) => setResolutionNote(e.target.value)}
                placeholder={t("geo.intelligenceNeeds.resolutionNotePlaceholder")}
              />
              <Button
                size="sm"
                disabled={!resolutionFile || !resolvedBy || addReportMutation.isPending}
                onClick={() => {
                  if (!resolutionFile || !resolvedBy) return;
                  addReportMutation.mutate({
                    gapId: detail.gap.gap_id,
                    file: resolutionFile,
                    countryRegion: detail.gap.country_region,
                    topics: detail.gap.topic,
                    resolvedBy,
                    resolutionNote,
                  });
                }}
              >
                {t("geo.intelligenceNeeds.addResolutionReport")}
              </Button>
              {addReportMutation.isError && (
                <p className="text-xs text-destructive">
                  {(addReportMutation.error as Error).message}
                </p>
              )}
            </div>
          )}
        </Section>
      </main>
    </>
  );
}
