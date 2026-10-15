import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { ChevronLeft, ChevronRight, Loader2, Sparkles } from "lucide-react";
import { TopBar } from "@/components/top-bar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { GeoCard, GeoCardBody, GeoCardHeader } from "@/components/geo/geo-card";
import { GeoStatusBadge, type GeoSeverity } from "@/components/geo/geo-badge";
import {
  useWorkbenchItem, useWorkbenchLineage, useWorkbenchSiblings, useAssessGeneralizationRisk,
  useSubmitHumanInstruction, useRecordHumanEdit, useRecordDedupVerdict, useMarkReadyForApproval,
  useMarkEditing, useApproveInWorkbench, useRejectInWorkbench, usePublishFromWorkbench,
  type GeoIosDedupVerdict, type GeoIosGeneralizationRisk,
} from "@/lib/geoIosLearningWorkbenchApi";
import { useLanguage } from "@/lib/i18n";

// Geopolitical Intelligence OS: Learning Workbench 詳細。Learning Candidate 1件＝1画面
// （Approval単位もCandidate単位、Plan D章のユーザー確定事項）。Source Reviewとの文脈を
// 失わないよう、画面上部に元Report/Review Round・同じReviewから抽出されたLearning件数・
// 前後のLearningへの移動を表示する。
export const Route = createFileRoute("/geo/ios/learning-workbench/$candidateId")({
  component: GeoIosLearningWorkbenchDetailPage,
});

const STATUS_SEVERITY: Record<string, GeoSeverity> = {
  pending: "neutral", editing: "moderate", ready_for_approval: "elevated",
  approved: "low", published: "low", rejected: "high",
};

const SCOPE_OPTIONS = ["report_specific", "theme_specific", "actor_specific", "domain_general", "general"];
const CONFIDENCE_OPTIONS = ["高", "中", "低", "判断不能"];
const RISK_OPTIONS: GeoIosGeneralizationRisk[] = ["low", "medium", "high"];
const DEDUP_VERDICTS: GeoIosDedupVerdict[] = ["NEW", "UPDATE", "RELATED", "CONFLICT", "DUPLICATE"];
const KNOWLEDGE_TYPE_OPTIONS = [
  "country_foundation", "actor_profile", "party_profile", "relationship_assessment",
  "historical_path_dependency", "precedent", "mechanism", "reasoning_rule", "indicator",
  "foundation_update", "case_finding", "leverage_card", "actor_profile_update", "relationship_update",
  "dependency_update", "sanctions_update", "report_finding", "media_profile", "media_episode",
  "media_pattern", "institution_profile", "approved_report",
];
const VISIBILITY_OPTIONS = ["private", "project", "geopolitics_team", "department", "company", "custom"];
const SHARING_OPTIONS = ["none", "geopolitics_only", "allowed_cross_domain", "custom"];

function EvidenceRow({ label, text }: { label: string; text?: string | null }) {
  if (!text) return null;
  return (
    <div>
      <div className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground mb-1">{label}</div>
      <p className="text-sm">{text}</p>
    </div>
  );
}

function GeoIosLearningWorkbenchDetailPage() {
  const { candidateId } = Route.useParams();
  const { t } = useLanguage();
  const actorId = typeof window !== "undefined" ? localStorage.getItem("geo_ios_actor_id") ?? "steward" : "steward";
  const [reviewerRole, setReviewerRole] = useState("knowledge_steward");

  const itemQuery = useWorkbenchItem(candidateId);
  const lineageQuery = useWorkbenchLineage(candidateId);
  const candidate = itemQuery.data?.candidate;
  const siblingsQuery = useWorkbenchSiblings(candidateId, candidate?.source_review_cycle_id ?? undefined);

  const assessRisk = useAssessGeneralizationRisk(candidateId);
  const submitInstruction = useSubmitHumanInstruction(candidateId);
  const recordEdit = useRecordHumanEdit(candidateId);
  const recordVerdict = useRecordDedupVerdict(candidateId);
  const markReady = useMarkReadyForApproval(candidateId);
  const markEditing = useMarkEditing(candidateId);
  const approve = useApproveInWorkbench(candidateId);
  const reject = useRejectInWorkbench(candidateId);
  const publish = usePublishFromWorkbench(candidateId);

  const [ruleDraft, setRuleDraft] = useState<string | null>(null);
  const [scopeDraft, setScopeDraft] = useState<string | null>(null);
  const [confidenceDraft, setConfidenceDraft] = useState<string | null>(null);
  const [instruction, setInstruction] = useState("");
  const [targetTypeDraft, setTargetTypeDraft] = useState<string | null>(null);
  const [verdict, setVerdict] = useState<GeoIosDedupVerdict | null>(null);
  const [updateTargetId, setUpdateTargetId] = useState("");
  const [relatedIds, setRelatedIds] = useState("");
  const [conflictNote, setConflictNote] = useState("");
  const [visibilityDraft, setVisibilityDraft] = useState<string | null>(null);
  const [sharingDraft, setSharingDraft] = useState<string | null>(null);

  if (!candidate) {
    return (
      <>
        <TopBar breadcrumb={[{ label: t("nav.geoLearningWorkbench"), href: "/geo/ios/learning-workbench" }]} />
        <main className="flex-1 max-w-[900px] w-full mx-auto px-8 py-8">
          <p className="text-sm text-muted-foreground">{itemQuery.isLoading ? "..." : "Not found"}</p>
        </main>
      </>
    );
  }

  const tc = candidate.trigger_context ?? {};
  const lineage = lineageQuery.data;
  const siblings = siblingsQuery.data;
  const revisions = itemQuery.data?.revisions ?? [];
  const turns = itemQuery.data?.turns ?? [];
  const pendingTurn = turns.find((tn) => tn.status === "pending");

  const rule = ruleDraft ?? candidate.generalized_rule;
  const scope = scopeDraft ?? candidate.scope ?? "";
  const confidence = confidenceDraft ?? candidate.confidence ?? "";
  const hasEditPending = ruleDraft !== null || scopeDraft !== null || confidenceDraft !== null;

  const effectiveVerdict = verdict ?? candidate.dedup_verdict ?? null;
  const targetKnowledgeType = targetTypeDraft ?? candidate.target_knowledge_type ?? "";
  const visibility = visibilityDraft ?? candidate.human_visibility ?? "";
  const sharing = sharingDraft ?? candidate.agent_sharing_policy ?? "";

  const canReadyForApproval = candidate.workbench_status === "editing";
  const canBackToEditing = candidate.workbench_status === "ready_for_approval";
  const canApprove = candidate.workbench_status === "ready_for_approval";
  const canPublish = candidate.workbench_status === "approved";
  const canReject = ["pending", "editing", "ready_for_approval"].includes(candidate.workbench_status);
  const isTerminal = candidate.workbench_status === "published" || candidate.workbench_status === "rejected";

  return (
    <>
      <TopBar breadcrumb={[
        { label: t("nav.geoLearningWorkbench"), href: "/geo/ios/learning-workbench" },
        { label: tc.title || candidate.candidate_type },
      ]} />
      <main className="flex-1 max-w-[900px] w-full mx-auto px-8 py-8 space-y-6">
        {/* 文脈ヘッダー */}
        <GeoCard>
          <GeoCardBody className="pt-5 space-y-2 text-sm">
            <div className="flex items-center gap-3 flex-wrap text-xs text-muted-foreground">
              {lineage?.report_project && (
                <Link to="/geo/report-studio/$projectId" params={{ projectId: lineage.report_project.project_id }} className="underline">
                  {lineage.report_project.project_title || lineage.report_project.project_code}
                </Link>
              )}
              {lineage?.review_cycle && (
                <>
                  <span>/</span>
                  <Link
                    to="/geo/ios/review-cycle/$reviewCycleId"
                    params={{ reviewCycleId: lineage.review_cycle.review_cycle_id }}
                    className="underline"
                  >
                    Round {lineage.review_cycle.round_number}
                  </Link>
                </>
              )}
              {lineage?.after_resolution_method && (
                <Badge variant="outline">{t("geo.ios.learningWorkbench.afterResolvedBy")}: {lineage.after_resolution_method}</Badge>
              )}
            </div>
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground">
                {t("geo.ios.learningWorkbench.siblingCount", { count: siblings?.total ?? 0 })}
              </span>
              <div className="flex gap-2">
                <Button
                  variant="outline" size="sm" disabled={!siblings?.previous_candidate_id}
                  asChild={!!siblings?.previous_candidate_id}
                >
                  {siblings?.previous_candidate_id ? (
                    <Link to="/geo/ios/learning-workbench/$candidateId" params={{ candidateId: siblings.previous_candidate_id }}>
                      <ChevronLeft className="h-4 w-4" />
                    </Link>
                  ) : <ChevronLeft className="h-4 w-4" />}
                </Button>
                <Button
                  variant="outline" size="sm" disabled={!siblings?.next_candidate_id}
                  asChild={!!siblings?.next_candidate_id}
                >
                  {siblings?.next_candidate_id ? (
                    <Link to="/geo/ios/learning-workbench/$candidateId" params={{ candidateId: siblings.next_candidate_id }}>
                      <ChevronRight className="h-4 w-4" />
                    </Link>
                  ) : <ChevronRight className="h-4 w-4" />}
                </Button>
              </div>
            </div>
          </GeoCardBody>
        </GeoCard>

        <section className="border-b border-border pb-5 space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            <GeoStatusBadge severity={STATUS_SEVERITY[candidate.workbench_status] ?? "neutral"}>
              {candidate.workbench_status}
            </GeoStatusBadge>
            <Badge variant="outline">{candidate.candidate_type}</Badge>
            {candidate.dedup_verdict && <Badge variant="outline">{candidate.dedup_verdict}</Badge>}
          </div>
          <h1 className="geo-heading text-2xl">{tc.title || candidate.generalized_rule}</h1>
        </section>

        {/* A. AI-detected changes */}
        <GeoCard>
          <GeoCardHeader title={t("geo.ios.learningWorkbench.aiDetectedChanges")} />
          <GeoCardBody className="space-y-3">
            <EvidenceRow label="Trigger" text={tc.trigger} />
            <EvidenceRow label={t("geo.ios.learningWorkbench.initialAiWeakness")} text={tc.initial_ai_weakness} />
            <EvidenceRow label={t("geo.ios.learningWorkbench.expertComment")} text={candidate.observation} />
            <EvidenceRow label={t("geo.ios.learningWorkbench.finalOutcome")} text={tc.final_outcome} />
            {tc.exceptions && tc.exceptions.length > 0 && (
              <EvidenceRow label="Exceptions" text={tc.exceptions.join("; ")} />
            )}
          </GeoCardBody>
        </GeoCard>

        {/* B. AI-proposed Learning（編集可能） */}
        <GeoCard>
          <GeoCardHeader title={t("geo.ios.learningWorkbench.proposedLearning")} />
          <GeoCardBody className="space-y-3">
            <Textarea value={rule} onChange={(e) => setRuleDraft(e.target.value)} rows={4} />
            <div className="flex gap-2">
              <Select value={scope} onValueChange={setScopeDraft}>
                <SelectTrigger className="w-56"><SelectValue placeholder="scope" /></SelectTrigger>
                <SelectContent>
                  {SCOPE_OPTIONS.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
                </SelectContent>
              </Select>
              <Select value={confidence} onValueChange={setConfidenceDraft}>
                <SelectTrigger className="w-32"><SelectValue placeholder="confidence" /></SelectTrigger>
                <SelectContent>
                  {CONFIDENCE_OPTIONS.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <Button
              size="sm" disabled={!hasEditPending || recordEdit.isPending || isTerminal}
              onClick={() => {
                recordEdit.mutate({
                  edited_fields: { generalized_rule: rule, scope, confidence },
                  change_summary: t("geo.ios.learningWorkbench.humanEditSummary"),
                }, { onSuccess: () => { setRuleDraft(null); setScopeDraft(null); setConfidenceDraft(null); } });
              }}
            >
              {recordEdit.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              {t("geo.ios.learningWorkbench.saveEdit")}
            </Button>
          </GeoCardBody>
        </GeoCard>

        {/* Generalization Risk */}
        <GeoCard>
          <GeoCardHeader title={t("geo.ios.learningWorkbench.generalizationRisk")} />
          <GeoCardBody className="space-y-3">
            {!candidate.generalization_risk && (
              <Button
                size="sm" variant="outline" disabled={assessRisk.isPending}
                onClick={() => assessRisk.mutate({ actor_id: actorId })}
              >
                <Sparkles className="h-4 w-4 mr-2" />
                {assessRisk.isPending
                  ? t("geo.ios.learningWorkbench.assessingRisk")
                  : t("geo.ios.learningWorkbench.assessRisk")}
              </Button>
            )}
            {candidate.generalization_risk && (
              <>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">{t("geo.ios.learningWorkbench.risk")}:</span>
                  <Select
                    value={candidate.generalization_risk}
                    onValueChange={(v) => recordEdit.mutate({
                      edited_fields: { generalization_risk: v }, change_summary: "Risk manually adjusted",
                    })}
                  >
                    <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {RISK_OPTIONS.map((r) => <SelectItem key={r} value={r}>{r}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
                <p className="text-sm text-muted-foreground">{candidate.generalization_risk_reason}</p>
              </>
            )}
          </GeoCardBody>
        </GeoCard>

        {/* C. 編集Chat */}
        <GeoCard>
          <GeoCardHeader title={t("geo.ios.learningWorkbench.editingChat")} />
          <GeoCardBody className="space-y-3">
            <div className="space-y-3 max-h-72 overflow-y-auto">
              {turns.map((tn) => (
                <div key={tn.workbench_turn_id} className="border-b border-border pb-2 last:border-0 space-y-1">
                  <div className="flex items-center gap-2">
                    <Badge variant="outline" className="text-[10px]">{tn.status}</Badge>
                    <span className="text-xs text-muted-foreground">{new Date(tn.created_at).toLocaleString()}</span>
                  </div>
                  <p className="text-sm">{tn.human_instruction}</p>
                  {tn.ai_response_text && <p className="text-xs text-muted-foreground">{tn.ai_response_text}</p>}
                  {tn.status === "failed" && <p className="text-xs text-destructive">{tn.error_message}</p>}
                  {tn.status === "stale" && (
                    <p className="text-xs text-amber-600">{t("geo.ios.learningWorkbench.staleTurnNote")}</p>
                  )}
                </div>
              ))}
              {turns.length === 0 && (
                <p className="text-xs text-muted-foreground">{t("geo.ios.learningWorkbench.noChatYet")}</p>
              )}
            </div>
            <Textarea
              value={instruction} onChange={(e) => setInstruction(e.target.value)}
              placeholder={t("geo.ios.learningWorkbench.instructionPlaceholder")} rows={2}
              disabled={!!pendingTurn || isTerminal}
            />
            <Button
              size="sm" disabled={!instruction.trim() || !!pendingTurn || submitInstruction.isPending || isTerminal}
              onClick={() => submitInstruction.mutate({ human_instruction: instruction }, { onSuccess: () => setInstruction("") })}
            >
              {(submitInstruction.isPending || !!pendingTurn) && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              {t("geo.ios.learningWorkbench.sendInstruction")}
            </Button>
          </GeoCardBody>
        </GeoCard>

        {/* D. 反映先・Dedup */}
        <GeoCard>
          <GeoCardHeader title={t("geo.ios.learningWorkbench.targetAndDedup")} />
          <GeoCardBody className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">{t("geo.ios.learningWorkbench.targetKnowledgeType")}</label>
              <Select
                value={targetKnowledgeType || undefined}
                onValueChange={(v) => {
                  setTargetTypeDraft(v);
                  recordEdit.mutate({ edited_fields: { target_knowledge_type: v } });
                }}
              >
                <SelectTrigger className="w-64"><SelectValue placeholder="knowledge_type" /></SelectTrigger>
                <SelectContent>
                  {KNOWLEDGE_TYPE_OPTIONS.map((k) => <SelectItem key={k} value={k}>{k}</SelectItem>)}
                </SelectContent>
              </Select>
              <p className="text-[11px] text-muted-foreground">{t("geo.ios.learningWorkbench.targetSingleSelectNote")}</p>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">{t("geo.ios.learningWorkbench.dedupVerdict")}</label>
              <RadioGroup value={effectiveVerdict ?? undefined} onValueChange={(v) => setVerdict(v as GeoIosDedupVerdict)}>
                {DEDUP_VERDICTS.map((v) => (
                  <div key={v} className="flex items-center gap-2">
                    <RadioGroupItem value={v} id={`verdict-${v}`} />
                    <label htmlFor={`verdict-${v}`} className="text-sm cursor-pointer">{v}</label>
                  </div>
                ))}
              </RadioGroup>
              {effectiveVerdict === "UPDATE" && (
                <Input
                  value={updateTargetId || candidate.dedup_target_knowledge_item_id || ""}
                  onChange={(e) => setUpdateTargetId(e.target.value)}
                  placeholder={t("geo.ios.learningWorkbench.updateTargetPlaceholder")}
                />
              )}
              {effectiveVerdict === "RELATED" && (
                <Input
                  value={relatedIds} onChange={(e) => setRelatedIds(e.target.value)}
                  placeholder={t("geo.ios.learningWorkbench.relatedIdsPlaceholder")}
                />
              )}
              {effectiveVerdict === "CONFLICT" && (
                <Textarea
                  value={conflictNote || candidate.dedup_conflict_resolution_note || ""}
                  onChange={(e) => setConflictNote(e.target.value)}
                  placeholder={t("geo.ios.learningWorkbench.conflictNotePlaceholder")} rows={2}
                />
              )}
              <Button
                size="sm" variant="outline" disabled={!effectiveVerdict || recordVerdict.isPending}
                onClick={() => recordVerdict.mutate({
                  verdict: effectiveVerdict as GeoIosDedupVerdict,
                  target_knowledge_item_id: effectiveVerdict === "UPDATE" ? (updateTargetId || candidate.dedup_target_knowledge_item_id || undefined) : undefined,
                  related_knowledge_item_ids: effectiveVerdict === "RELATED" ? relatedIds.split(",").map((s) => s.trim()).filter(Boolean) : undefined,
                  resolution_note: effectiveVerdict === "CONFLICT" ? conflictNote || undefined : undefined,
                })}
              >
                {recordVerdict.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                {t("geo.ios.learningWorkbench.saveVerdict")}
              </Button>
              {recordVerdict.isError && <p className="text-xs text-destructive">{(recordVerdict.error as Error).message}</p>}
            </div>

            <div className="flex gap-2">
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">{t("geo.ios.learningWorkbench.humanVisibility")}</label>
                <Select value={visibility || undefined} onValueChange={(v) => { setVisibilityDraft(v); recordEdit.mutate({ edited_fields: { human_visibility: v } }); }}>
                  <SelectTrigger className="w-48"><SelectValue placeholder="human_visibility" /></SelectTrigger>
                  <SelectContent>
                    {VISIBILITY_OPTIONS.map((v) => <SelectItem key={v} value={v}>{v}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">{t("geo.ios.learningWorkbench.agentSharingPolicy")}</label>
                <Select value={sharing || undefined} onValueChange={(v) => { setSharingDraft(v); recordEdit.mutate({ edited_fields: { agent_sharing_policy: v } }); }}>
                  <SelectTrigger className="w-48"><SelectValue placeholder="agent_sharing_policy" /></SelectTrigger>
                  <SelectContent>
                    {SHARING_OPTIONS.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>
          </GeoCardBody>
        </GeoCard>

        {/* 版履歴タイムライン */}
        <GeoCard>
          <GeoCardHeader title={t("geo.ios.learningWorkbench.revisionHistory")} />
          <GeoCardBody className="space-y-2">
            {revisions.map((r) => (
              <div key={r.revision_id} className="flex items-center gap-2 text-xs border-b border-border pb-2 last:border-0">
                <Badge variant="outline">V{r.revision_number}</Badge>
                <Badge variant="outline">{r.revised_by}</Badge>
                <span className="text-muted-foreground">{r.change_summary}</span>
                <span className="text-muted-foreground ml-auto">{new Date(r.created_at).toLocaleString()}</span>
              </div>
            ))}
          </GeoCardBody>
        </GeoCard>

        {/* E. Actions */}
        <GeoCard>
          <GeoCardHeader title={t("geo.ios.learningWorkbench.actions")} />
          <GeoCardBody className="space-y-3">
            <Input value={reviewerRole} onChange={(e) => setReviewerRole(e.target.value)} placeholder="reviewer_role (knowledge_steward / expert_reviewer)" className="w-72" />
            <div className="flex gap-2 flex-wrap">
              {canReadyForApproval && (
                <Button size="sm" disabled={markReady.isPending} onClick={() => markReady.mutate({ actor_id: actorId })}>
                  {t("geo.ios.learningWorkbench.readyForApproval")}
                </Button>
              )}
              {canBackToEditing && (
                <Button size="sm" variant="outline" disabled={markEditing.isPending} onClick={() => markEditing.mutate({ actor_id: actorId })}>
                  {t("geo.ios.learningWorkbench.backToEditing")}
                </Button>
              )}
              {canApprove && (
                <Button size="sm" disabled={approve.isPending} onClick={() => approve.mutate({ reviewed_by: actorId, reviewer_role: reviewerRole })}>
                  {approve.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                  {t("geo.ios.learningWorkbench.approve")}
                </Button>
              )}
              {canPublish && (
                <Button size="sm" disabled={publish.isPending} onClick={() => publish.mutate({ publisher: actorId, publisher_role: reviewerRole })}>
                  {publish.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                  {t("geo.ios.learningWorkbench.publish")}
                </Button>
              )}
              {canReject && (
                <Button size="sm" variant="destructive" disabled={reject.isPending} onClick={() => reject.mutate({ reviewed_by: actorId, reviewer_role: reviewerRole })}>
                  {t("geo.ios.learningWorkbench.reject")}
                </Button>
              )}
            </div>
            {(approve.isError || publish.isError || reject.isError || markReady.isError) && (
              <p className="text-xs text-destructive">
                {((approve.error ?? publish.error ?? reject.error ?? markReady.error) as Error)?.message}
              </p>
            )}
            {isTerminal && (
              <p className="text-xs text-muted-foreground">{t("geo.ios.learningWorkbench.terminalStateNote")}</p>
            )}
          </GeoCardBody>
        </GeoCard>
      </main>
    </>
  );
}
