import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Loader2 } from "lucide-react";
import { TopBar } from "@/components/top-bar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { GeoCard, GeoCardBody, GeoCardHeader } from "@/components/geo/geo-card";
import { GeoStatusBadge } from "@/components/geo/geo-badge";
import {
  useLearningCandidate, useApproveCandidate, useRejectCandidate, usePromoteCandidate,
  useSuggestIssues, useLinkIssue,
} from "@/lib/geoIosLearningApi";
import { useLanguage } from "@/lib/i18n";

// Geopolitical Intelligence OS Phase H §26-29: Learning Candidate Detail。Evidence Chain
// （Initial AI → Expert Comment → Author Response → Approved Result）を必ず表示し、
// Approve/Edit&Approve/Reject → Promote → Issue Link Suggestion（Human Confirmation必須、
// 自動リンク禁止、§29）までを一連の操作として提供する。二重Gate（Phase G）は維持（§27）。
export const Route = createFileRoute("/geo/ios/learning-candidates/$candidateId")({
  component: GeoIosLearningCandidateDetailPage,
});

function EvidenceRow({ label, text }: { label: string; text?: string | null }) {
  if (!text) return null;
  return (
    <div>
      <div className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground mb-1">{label}</div>
      <p className="text-sm">{text}</p>
    </div>
  );
}

function GeoIosLearningCandidateDetailPage() {
  const { candidateId } = Route.useParams();
  const { t } = useLanguage();
  const actorId = typeof window !== "undefined" ? localStorage.getItem("geo_ios_actor_id") ?? "steward" : "steward";
  const [reviewerRole, setReviewerRole] = useState("knowledge_steward");
  const [editedRule, setEditedRule] = useState("");
  const [targetEntityId, setTargetEntityId] = useState("");

  const candidateQuery = useLearningCandidate(candidateId);
  const approve = useApproveCandidate(candidateId);
  const reject = useRejectCandidate(candidateId);
  const promote = usePromoteCandidate(candidateId);
  const suggestIssuesQuery = useSuggestIssues(candidateId);
  const linkIssue = useLinkIssue(candidateId);

  const candidate = candidateQuery.data;
  if (!candidate) {
    return (
      <>
        <TopBar breadcrumb={[{ label: t("nav.geoLearningCandidates"), href: "/geo/ios/learning-candidates" }]} />
        <main className="flex-1 max-w-[900px] w-full mx-auto px-8 py-8">
          <p className="text-sm text-muted-foreground">{candidateQuery.isLoading ? "..." : "Not found"}</p>
        </main>
      </>
    );
  }
  const tc = candidate.trigger_context ?? {};

  return (
    <>
      <TopBar breadcrumb={[
        { label: t("nav.geoLearningCandidates"), href: "/geo/ios/learning-candidates" },
        { label: tc.title || candidate.candidate_type },
      ]} />
      <main className="flex-1 max-w-[900px] w-full mx-auto px-8 py-8 space-y-6">
        <section className="border-b border-border pb-5 space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            <Badge variant="outline">{candidate.candidate_type}</Badge>
            <GeoStatusBadge severity="neutral">{candidate.status}</GeoStatusBadge>
            {candidate.scope && <Badge variant="outline">{candidate.scope}</Badge>}
            {tc.temporal_stability && <Badge variant="outline">{tc.temporal_stability}</Badge>}
            {candidate.confidence && <Badge variant="outline">confidence: {candidate.confidence}</Badge>}
          </div>
          <h1 className="geo-heading text-2xl">{tc.title || candidate.generalized_rule}</h1>
        </section>

        <GeoCard>
          <GeoCardHeader title={t("geo.ios.learningCandidates.proposedLearning")} />
          <GeoCardBody className="space-y-2">
            <p className="text-sm font-medium">{candidate.generalized_rule}</p>
            {tc.trigger && <p className="text-xs text-muted-foreground">Trigger: {tc.trigger}</p>}
            {tc.exceptions && tc.exceptions.length > 0 && (
              <p className="text-xs text-muted-foreground">Exceptions: {tc.exceptions.join("; ")}</p>
            )}
          </GeoCardBody>
        </GeoCard>

        {/* §12/§26 Evidence Chain */}
        <GeoCard>
          <GeoCardHeader title={t("geo.ios.learningCandidates.evidenceChain")} />
          <GeoCardBody className="space-y-3">
            <EvidenceRow label="Initial AI" text={tc.initial_ai_weakness} />
            <EvidenceRow label="Expert Comment" text={candidate.observation} />
            <EvidenceRow label="Approved Result" text={tc.final_outcome} />
          </GeoCardBody>
        </GeoCard>

        {candidate.status === "candidate" && (
          <GeoCard>
            <GeoCardHeader title={t("geo.ios.learningCandidates.reviewActions")} />
            <GeoCardBody className="space-y-3">
              <Input value={reviewerRole} onChange={(e) => setReviewerRole(e.target.value)} placeholder="reviewer_role (knowledge_steward)" />
              <Textarea value={editedRule} onChange={(e) => setEditedRule(e.target.value)} placeholder={t("geo.ios.learningCandidates.editRulePlaceholder")} />
              <div className="flex gap-2">
                <Button
                  disabled={approve.isPending}
                  onClick={() => approve.mutate({ reviewed_by: actorId, reviewer_role: reviewerRole, edited_generalized_rule: editedRule || undefined })}
                >
                  {editedRule ? t("geo.ios.learningCandidates.editAndApprove") : t("geo.ios.learningCandidates.approve")}
                </Button>
                <Button
                  variant="destructive"
                  disabled={reject.isPending}
                  onClick={() => reject.mutate({ reviewed_by: actorId, reviewer_role: reviewerRole })}
                >
                  {t("geo.ios.learningCandidates.reject")}
                </Button>
              </div>
              {(approve.isError || reject.isError) && (
                <p className="text-xs text-destructive">
                  {((approve.error ?? reject.error) as Error)?.message}
                </p>
              )}
            </GeoCardBody>
          </GeoCard>
        )}

        {candidate.status === "approved" && !candidate.approved_knowledge_item_id && (
          <GeoCard>
            <GeoCardHeader title={t("geo.ios.learningCandidates.promote")} />
            <GeoCardBody className="space-y-3">
              {candidate.candidate_type === "actor_intelligence" && (
                <Input value={targetEntityId} onChange={(e) => setTargetEntityId(e.target.value)} placeholder="target_entity_id" />
              )}
              <Button
                disabled={promote.isPending}
                onClick={() => promote.mutate({ created_by: actorId, created_by_role: reviewerRole, target_entity_id: targetEntityId || undefined })}
              >
                {promote.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                {t("geo.ios.learningCandidates.promote")}
              </Button>
              {promote.isError && <p className="text-xs text-destructive">{(promote.error as Error).message}</p>}
            </GeoCardBody>
          </GeoCard>
        )}

        {/* §28-29 Issue Link Suggestion — Human Confirmation必須、自動リンクしない */}
        {candidate.approved_knowledge_item_id && (
          <GeoCard>
            <GeoCardHeader title={t("geo.ios.learningCandidates.suggestedIssues")} />
            <GeoCardBody className="space-y-2">
              {(suggestIssuesQuery.data?.suggestions ?? []).map((s) => (
                <div key={s.issue_id} className="flex items-center justify-between border-b border-border pb-2 last:border-0">
                  <span className="text-sm">{s.name_ja} <span className="text-xs text-muted-foreground">({Math.round(s.score * 100)}%)</span></span>
                  <Button size="sm" variant="outline" disabled={linkIssue.isPending} onClick={() => linkIssue.mutate({ issue_id: s.issue_id })}>
                    {t("geo.ios.learningCandidates.confirmLink")}
                  </Button>
                </div>
              ))}
              {(suggestIssuesQuery.data?.suggestions ?? []).length === 0 && (
                <p className="text-xs text-muted-foreground">{t("geo.ios.learningCandidates.noIssueMatch")}</p>
              )}
              <p className="text-[11px] text-muted-foreground pt-1">{t("geo.ios.learningCandidates.issueLinkNote")}</p>
            </GeoCardBody>
          </GeoCard>
        )}
      </main>
    </>
  );
}
