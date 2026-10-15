import { createFileRoute, Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { TopBar } from "@/components/top-bar";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { GeoCard, GeoCardBody, GeoCardHeader } from "@/components/geo/geo-card";
import { GeoStatusBadge, type GeoSeverity } from "@/components/geo/geo-badge";
import { useWorkbenchCandidates, type GeoIosWorkbenchCandidate } from "@/lib/geoIosLearningWorkbenchApi";
import { useLanguage } from "@/lib/i18n";

// Geopolitical Intelligence OS: Learning Workbench 一覧。既存geo.ios.learning-candidates.tsx
// （Gate#1の素のInbox）とは独立した画面——Workbenchは追加であり置き換えではない
// （kind-napping-hare.md Plan D章）。Review単位でのグルーピング表示を維持するが、
// 操作対象は常にCandidate単位（詳細画面へ遷移して行う）。
export const Route = createFileRoute("/geo/ios/learning-workbench")({
  component: GeoIosLearningWorkbenchPage,
});

const STATUS_OPTIONS = ["pending", "editing", "ready_for_approval", "approved", "published", "rejected", "all"] as const;

const CANDIDATE_TYPE_OPTIONS = [
  "all", "reasoning_rule", "attention_rule", "failure_pattern", "evidence_rule", "source_preference",
  "actor_intelligence", "media_intelligence", "report_design_pattern", "intelligence_need_pattern",
  "monitoring_indicator", "cqas_question_pattern", "scenario_pattern",
] as const;

const STATUS_SEVERITY: Record<string, GeoSeverity> = {
  pending: "neutral",
  editing: "moderate",
  ready_for_approval: "elevated",
  approved: "low",
  published: "low",
  rejected: "high",
};

function WorkbenchCard({ candidate }: { candidate: GeoIosWorkbenchCandidate }) {
  const { t } = useLanguage();
  const tc = candidate.trigger_context ?? {};
  return (
    <Link to="/geo/ios/learning-workbench/$candidateId" params={{ candidateId: candidate.candidate_id }}>
      <GeoCard className="hover:border-primary/40 transition-colors cursor-pointer">
        <GeoCardBody className="pt-5 space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            <GeoStatusBadge severity={STATUS_SEVERITY[candidate.workbench_status] ?? "neutral"}>
              {candidate.workbench_status}
            </GeoStatusBadge>
            <Badge variant="outline">{candidate.candidate_type}</Badge>
            {candidate.generalization_risk && (
              <Badge variant="outline">{t("geo.ios.learningWorkbench.risk")}: {candidate.generalization_risk}</Badge>
            )}
            {candidate.dedup_verdict && <Badge variant="outline">{candidate.dedup_verdict}</Badge>}
          </div>
          <p className="text-sm font-medium">{tc.title || candidate.generalized_rule}</p>
          <p className="text-xs text-muted-foreground line-clamp-2">{candidate.generalized_rule}</p>
        </GeoCardBody>
      </GeoCard>
    </Link>
  );
}

function GeoIosLearningWorkbenchPage() {
  const { t } = useLanguage();
  const [status, setStatus] = useState<string>("all");
  const [candidateType, setCandidateType] = useState<string>("all");
  const candidatesQuery = useWorkbenchCandidates(
    status === "all" ? undefined : status, candidateType === "all" ? undefined : candidateType);
  const candidates = candidatesQuery.data?.candidates;

  const groups = useMemo(() => {
    const byReviewCycle = new Map<string, GeoIosWorkbenchCandidate[]>();
    for (const c of candidates ?? []) {
      const key = c.source_review_cycle_id || "unscoped";
      if (!byReviewCycle.has(key)) byReviewCycle.set(key, []);
      byReviewCycle.get(key)!.push(c);
    }
    return Array.from(byReviewCycle.entries());
  }, [candidates]);

  return (
    <>
      <TopBar breadcrumb={[{ label: t("nav.geoLearningWorkbench") }]} />
      <main className="flex-1 max-w-[1100px] w-full mx-auto px-8 py-8 space-y-6">
        <section className="border-b border-border pb-5 flex items-center justify-between flex-wrap gap-3">
          <div>
            <h1 className="geo-heading text-3xl">{t("geo.ios.learningWorkbench.title")}</h1>
            <p className="text-sm text-muted-foreground mt-2">{t("geo.ios.learningWorkbench.subtitle")}</p>
          </div>
          <div className="flex gap-2">
            <Select value={candidateType} onValueChange={setCandidateType}>
              <SelectTrigger className="w-48"><SelectValue /></SelectTrigger>
              <SelectContent>
                {CANDIDATE_TYPE_OPTIONS.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
              </SelectContent>
            </Select>
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger className="w-44"><SelectValue /></SelectTrigger>
              <SelectContent>
                {STATUS_OPTIONS.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
        </section>

        {groups.length === 0 && (
          <p className="text-sm text-muted-foreground">{t("geo.ios.learningWorkbench.empty")}</p>
        )}

        {groups.map(([reviewCycleId, items]) => (
          <GeoCard key={reviewCycleId}>
            <GeoCardHeader
              eyebrow={t("geo.ios.learningWorkbench.reviewGroupEyebrow")}
              title={reviewCycleId === "unscoped"
                ? t("geo.ios.learningWorkbench.unscopedGroup")
                : `Review Cycle: ${reviewCycleId.slice(0, 8)}`}
              meta={`${items.length} ${t("geo.ios.learningWorkbench.candidatesCountSuffix")}`}
            />
            <GeoCardBody>
              <div className="grid gap-3 sm:grid-cols-2">
                {items.map((c) => <WorkbenchCard key={c.candidate_id} candidate={c} />)}
              </div>
            </GeoCardBody>
          </GeoCard>
        ))}
      </main>
    </>
  );
}
