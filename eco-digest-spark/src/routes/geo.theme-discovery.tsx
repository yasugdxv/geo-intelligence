import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { TopBar } from "@/components/top-bar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { ArrowRight, Send, Sparkles } from "lucide-react";
import {
  ThemeSourceActions,

  type ThemeSourceOverride,
} from "@/components/geo/theme-explorer-actions";
import { ThemeExplorerPanel } from "@/components/geo/theme-explorer-panel";
import { clearPickContext } from "@/lib/sourcePickStore";
import { consumeHighlightCandidateId } from "@/lib/geoThemeHighlightStore";
import { useLanguage } from "@/lib/i18n";
import {
  useApplyThemeCandidateRefinement,
  useHandoffThemeCandidateToExpertAI,
  useThemeCandidateEvidence,
  useThemeCandidateHandoffPreview,
  useThemeCandidateInternalCoverage,
  useThemeCandidates,
  useThemeCandidateWallBounce,
  useUpdateThemeCandidateStatus,
  type GeoThemeCandidate,
  type GeoThemeCandidateRefinementProposal,
  type GeoThemeCandidateStatus,
  type GeoThemeCandidateWallBounceMessage,
} from "@/lib/geoThemeCandidateApi";

// Phase 2G-2: Theme Discovery専用UI。geo_theme_candidates（Phase 2G-1、Candidate生成
// Core/fingerprint/lifecycleは変更しない）をそのまま表示するだけで、新しい生成ロジック・
// 独自Permission判定はここでは一切持たない。中心はNews一覧ではなく「今見るべきテーマ候補
// （Analysis Question）」——旧News & Signals（/geo/news-signals）は削除せず開発者向けへ
// 退避（app-sidebar.tsx参照）。

export const Route = createFileRoute("/geo/theme-discovery")({
  component: GeoThemeDiscoveryPage,
});

const STATUS_BADGE_STYLE: Record<GeoThemeCandidateStatus, string> = {
  NEW: "bg-sky-100 text-sky-800",
  SHORTLISTED: "bg-emerald-100 text-emerald-800",
  MONITORING: "bg-amber-100 text-amber-800",
  DISMISSED: "bg-muted text-muted-foreground",
  HANDED_OFF: "bg-violet-100 text-violet-800",
};

function StatusBadge({ status }: { status: GeoThemeCandidateStatus }) {
  const { t } = useLanguage();
  return (
    <span
      className={`px-2 py-0.5 rounded-full text-[11px] font-bold ${STATUS_BADGE_STYLE[status]}`}
    >
      {t(`geo.themeDiscovery.status.${status}`)}
    </span>
  );
}

function EvidenceSection({ candidateId }: { candidateId: string }) {
  const { t } = useLanguage();
  const { data, isLoading } = useThemeCandidateEvidence(candidateId);
  if (isLoading) return <p className="text-sm text-muted-foreground">...</p>;
  if (!data) return <p className="text-sm text-muted-foreground">—</p>;

  const hasAny =
    data.episode_clusters.length > 0 ||
    data.situation_links.length > 0 ||
    data.external_signals.length > 0 ||
    data.external_events.length > 0;
  if (!hasAny)
    return (
      <p className="text-sm text-muted-foreground">{t("geo.themeDiscovery.evidence.empty")}</p>
    );

  return (
    <div className="space-y-3">
      {data.external_signals.length > 0 && (
        <div>
          <p className="text-xs font-medium text-muted-foreground mb-1">
            {t("geo.themeDiscovery.evidence.externalSignals")}
          </p>
          <div className="space-y-1">
            {data.external_signals.map((s) => (
              <div key={s.external_signal_id} className="text-xs">
                {s.url ? (
                  <a href={s.url} target="_blank" rel="noreferrer" className="hover:underline">
                    {s.title || "(無題)"}
                  </a>
                ) : (
                  <span>{s.title || "(無題)"}</span>
                )}
                <span className="text-muted-foreground">
                  {" "}
                  — {s.source_domain || "不明な情報源"}
                  {s.published_at && ` (${s.published_at.slice(0, 10)})`}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {data.external_events.length > 0 && (
        <div>
          <p className="text-xs font-medium text-muted-foreground mb-1">
            {t("geo.themeDiscovery.evidence.externalEvents")}
          </p>
          <div className="space-y-1">
            {data.external_events.map((e) => (
              <div key={e.external_event_id} className="text-xs text-muted-foreground">
                {e.actor1_name}
                {e.actor2_name ? ` ↔ ${e.actor2_name}` : ""}
                {e.event_code && ` [${e.event_code}]`}
                {e.event_date && ` — ${e.event_date}`}
                {e.action_geo_full_name && ` (${e.action_geo_full_name})`}
              </div>
            ))}
          </div>
        </div>
      )}

      {data.episode_clusters.length > 0 && (
        <div>
          <p className="text-xs font-medium text-muted-foreground mb-1">
            {t("geo.themeDiscovery.evidence.episodeClusters")}
          </p>
          <div className="space-y-1">
            {data.episode_clusters.map((c) => (
              <div key={c.episode_cluster_id} className="text-xs text-muted-foreground">
                trajectory={c.trajectory}, member_count={c.member_count}
                {c.first_event_date && `, ${c.first_event_date} 〜 ${c.last_event_date}`}
                {c.representative_geo.length > 0 && ` — ${c.representative_geo.join(", ")}`}
              </div>
            ))}
          </div>
        </div>
      )}

      {data.situation_links.length > 0 && (
        <div>
          <p className="text-xs font-medium text-muted-foreground mb-1">
            {t("geo.themeDiscovery.evidence.situationLinks")}
          </p>
          <div className="space-y-1">
            {data.situation_links.map((l) => (
              <div key={l.cluster_link_id} className="text-xs text-muted-foreground">
                {l.link_type} (score={l.link_score})
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// Phase 2G-3: Internal Coverage（Runtime計算、geo_theme_candidatesへは保存しない）。
// Stage 1（metadata-only、LLM無し）はDetailを開いた時に自動取得する。Stage 2
// （既存analyze()、mode="coverage"）はCandidate一覧取得時には絶対に自動実行されない——
// このSectionが実際にマウントされ、かつユーザーが明示的にボタンを押した時だけ発火する。
function CoverageSection({ candidateId }: { candidateId: string }) {
  const { t } = useLanguage();
  const [wantAnalysis, setWantAnalysis] = useState(false);
  const { data, isLoading, isError, error } = useThemeCandidateInternalCoverage(candidateId, {
    includeAnalysis: wantAnalysis,
  });

  if (isLoading) return <p className="text-sm text-muted-foreground">...</p>;
  if (isError) return <p className="text-sm text-destructive">{(error as Error).message}</p>;
  if (!data) return <p className="text-sm text-muted-foreground">—</p>;

  const hasCoverage =
    data.coverage.accessible_report_count > 0 || data.coverage.accessible_knowledge_count > 0;

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
        <div className="card-paper rounded p-2">
          <div className="text-muted-foreground">
            {t("geo.themeDiscovery.coverage.reportCount")}
          </div>
          <div className="text-lg tabular-nums">{data.coverage.accessible_report_count}</div>
        </div>
        <div className="card-paper rounded p-2">
          <div className="text-muted-foreground">
            {t("geo.themeDiscovery.coverage.knowledgeCount")}
          </div>
          <div className="text-lg tabular-nums">{data.coverage.accessible_knowledge_count}</div>
        </div>
        <div className="card-paper rounded p-2">
          <div className="text-muted-foreground">
            {t("geo.themeDiscovery.coverage.approvedCount")}
          </div>
          <div className="text-lg tabular-nums">{data.coverage.approved_report_count}</div>
        </div>
        <div className="card-paper rounded p-2">
          <div className="text-muted-foreground">
            {t("geo.themeDiscovery.coverage.uploadedOnlyCount")}
          </div>
          <div className="text-lg tabular-nums">{data.coverage.uploaded_only_report_count}</div>
        </div>
      </div>

      {!hasCoverage ? (
        <p className="text-sm text-muted-foreground">{t("geo.themeDiscovery.coverage.notFound")}</p>
      ) : (
        <>
          {(data.recency.latest_report_date || data.recency.latest_approved_report_date) && (
            <div className="text-xs text-muted-foreground flex gap-3 flex-wrap">
              {data.recency.latest_report_date && (
                <span>
                  {t("geo.themeDiscovery.coverage.latestReportDate")}:{" "}
                  {data.recency.latest_report_date.slice(0, 10)}
                </span>
              )}
              {data.recency.latest_approved_report_date && (
                <span>
                  {t("geo.themeDiscovery.coverage.latestApprovedDate")}:{" "}
                  {data.recency.latest_approved_report_date.slice(0, 10)}
                </span>
              )}
            </div>
          )}

          {data.key_actors_already_covered.length > 0 && (
            <div className="text-xs">
              <span className="text-muted-foreground">
                {t("geo.themeDiscovery.coverage.actorsCovered")}:{" "}
              </span>
              {data.key_actors_already_covered.join(" / ")}
            </div>
          )}

          {data.similar_internal_intelligence.length > 0 && (
            <div className="space-y-1">
              <p className="text-xs font-medium text-muted-foreground">
                {t("geo.themeDiscovery.coverage.similarIntelligence")}
              </p>
              {data.similar_internal_intelligence.map((s) => (
                <div key={s.source_ref} className="text-xs flex items-center gap-2 flex-wrap">
                  <SourceTypeTag sourceType={s.source_type} />
                  {s.review_status && (
                    <span className="text-muted-foreground">[{s.review_status}]</span>
                  )}
                  <span>{s.title}</span>
                  {s.date && <span className="text-muted-foreground">{s.date.slice(0, 10)}</span>}
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {data.potential_coverage_gaps.length > 0 && (
        <div className="text-xs">
          <span className="text-muted-foreground">{t("geo.themeDiscovery.coverage.gaps")}: </span>
          {data.potential_coverage_gaps.join(" / ")}
        </div>
      )}

      {data.stage === "with_analysis"
        ? data.existing_analysis_themes.length > 0 && (
            <div className="space-y-1 pt-2 border-t border-border/60">
              <p className="text-xs font-medium text-muted-foreground">
                {t("geo.themeDiscovery.coverage.existingThemes")}
              </p>
              {data.existing_analysis_themes.map((f, i) => (
                <p key={i} className="text-xs text-foreground">
                  {f.claim}{" "}
                  <span className="text-muted-foreground">[{f.source_refs.join(", ")}]</span>
                </p>
              ))}
            </div>
          )
        : hasCoverage && (
            <Button size="sm" variant="outline" onClick={() => setWantAnalysis(true)}>
              {t("geo.themeDiscovery.coverage.viewAnalysis")}
            </Button>
          )}

      {data.warnings.length > 0 && (
        <p className="text-[11px] text-muted-foreground">{data.warnings.join(" / ")}</p>
      )}
    </div>
  );
}

function SourceTypeTag({ sourceType }: { sourceType: "report" | "knowledge_item" }) {
  const { t } = useLanguage();
  return (
    <Badge variant={sourceType === "report" ? "outline" : "secondary"} className="text-[10px]">
      {t(`geo.intelligenceAiSearch.sourceType.${sourceType}`)}
    </Badge>
  );
}

function CandidateDetail({ candidate }: { candidate: GeoThemeCandidate }) {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const updateStatus = useUpdateThemeCandidateStatus();
  const [wallBounceOpen, setWallBounceOpen] = useState(false);
  const [handoffPreviewOpen, setHandoffPreviewOpen] = useState(false);
  // Phase 2G-5 §7: WallBounceChatの最新proposalのinvestigation_questionsを、
  // Handoff Previewのcheckbox一覧の候補として持ち上げる（反映はここでは一切しない、
  // 表示するだけ——Candidate DBへの保存は従来通り行わない）。
  const [availableInvestigationQuestions, setAvailableInvestigationQuestions] = useState<string[]>(
    [],
  );
  const handoffToExpertAi = useHandoffThemeCandidateToExpertAI();

  const openExistingSession = () => {
    handoffToExpertAi.mutate(
      { candidateId: candidate.candidate_id, investigationQuestions: [] },
      {
        onSuccess: (result) => {
          navigate({
            to: "/geo/ios/analysis/$sessionId",
            params: { sessionId: result.analysis_session_id },
          });
        },
      },
    );
  };

  const lifecycleButton = (status: GeoThemeCandidateStatus, label: string) => (
    <Button
      key={status}
      size="sm"
      variant={candidate.status === status ? "default" : "outline"}
      disabled={updateStatus.isPending || candidate.status === status}
      onClick={() => updateStatus.mutate({ candidateId: candidate.candidate_id, status })}
    >
      {updateStatus.isPending ? t("geo.themeDiscovery.lifecycle.updating") : label}
    </Button>
  );

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 flex-wrap">
        <StatusBadge status={candidate.status} />
        <Badge variant="outline">
          {t(`geo.themeDiscovery.candidateType.${candidate.candidate_type}`)}
        </Badge>
        <Badge variant="secondary">
          {t(`geo.themeDiscovery.changeType.${candidate.change_type}`)}
        </Badge>
      </div>

      <h2 className="text-editorial text-xl">{candidate.title}</h2>

      <section>
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-[0.08em] mb-1">
          {t("geo.themeDiscovery.detail.whatIsHappening")}
        </p>
        <p className="text-sm text-foreground whitespace-pre-wrap">{candidate.signal_summary}</p>
      </section>

      <section>
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-[0.08em] mb-1">
          {t("geo.themeDiscovery.detail.observedChange")}
        </p>
        <p className="text-sm text-foreground whitespace-pre-wrap">{candidate.observed_change}</p>
      </section>

      <section>
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-[0.08em] mb-1">
          {t("geo.themeDiscovery.detail.whyNow")}
        </p>
        <p className="text-sm text-foreground whitespace-pre-wrap">{candidate.why_now}</p>
      </section>

      <section>
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-[0.08em] mb-1">
          {t("geo.themeDiscovery.detail.question")}
        </p>
        <p className="text-sm text-foreground whitespace-pre-wrap">{candidate.question}</p>
      </section>

      <section>
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-[0.08em] mb-1">
          {t("geo.themeDiscovery.detail.whoToWatch")}
        </p>
        <div className="flex flex-wrap gap-2 text-sm">
          {candidate.actors.map((a) => (
            <Badge key={a} variant="outline">
              {a}
            </Badge>
          ))}
          {candidate.regions.map((r) => (
            <Badge key={r} variant="secondary">
              {r}
            </Badge>
          ))}
          {candidate.themes.map((th) => (
            <Badge key={th} variant="outline">
              {th}
            </Badge>
          ))}
        </div>
      </section>

      <section>
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-[0.08em] mb-2">
          {t("geo.themeDiscovery.detail.internalCoverage")}
        </p>
        <CoverageSection candidateId={candidate.candidate_id} />
      </section>

      <section>
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-[0.08em] mb-2">
          {t("geo.themeDiscovery.detail.evidence")}
        </p>
        <EvidenceSection candidateId={candidate.candidate_id} />
      </section>

      <div className="flex gap-2 flex-wrap items-center pt-3 border-t border-border/60">
        {candidate.status !== "HANDED_OFF" && (
          <>
            {lifecycleButton("SHORTLISTED", t("geo.themeDiscovery.lifecycle.shortlist"))}
            {lifecycleButton("MONITORING", t("geo.themeDiscovery.lifecycle.monitor"))}
            {lifecycleButton("DISMISSED", t("geo.themeDiscovery.lifecycle.dismiss"))}
            <Button
              size="sm"
              variant={wallBounceOpen ? "default" : "outline"}
              onClick={() => setWallBounceOpen((v) => !v)}
            >
              <Sparkles className="h-3.5 w-3.5 mr-1.5" />
              {t("geo.themeDiscovery.wallBounce.cta")}
            </Button>
          </>
        )}
        <Button
          size="sm"
          variant="default"
          disabled={handoffToExpertAi.isPending}
          onClick={() =>
            candidate.status === "HANDED_OFF" ? openExistingSession() : setHandoffPreviewOpen(true)
          }
        >
          <ArrowRight className="h-3.5 w-3.5 mr-1.5" />
          {candidate.status === "HANDED_OFF"
            ? t("geo.themeDiscovery.handoff.ctaOpen")
            : t("geo.themeDiscovery.handoff.cta")}
        </Button>
      </div>
      {updateStatus.isError && (
        <p className="text-sm text-destructive">{(updateStatus.error as Error).message}</p>
      )}
      {handoffToExpertAi.isError && (
        <p className="text-sm text-destructive">{(handoffToExpertAi.error as Error).message}</p>
      )}

      {wallBounceOpen && (
        <WallBounceChat
          candidate={candidate}
          onInvestigationQuestionsAvailable={setAvailableInvestigationQuestions}
        />
      )}

      <HandoffPreviewDialog
        candidate={candidate}
        open={handoffPreviewOpen}
        onOpenChange={setHandoffPreviewOpen}
        availableInvestigationQuestions={availableInvestigationQuestions}
      />
    </div>
  );
}

type WallBounceTurn =
  | { role: "user"; content: string }
  | {
      role: "assistant";
      answer: string;
      hasProposal: boolean;
      proposal: GeoThemeCandidateRefinementProposal;
    };

function turnsToWallBounceHistory(turns: WallBounceTurn[]): GeoThemeCandidateWallBounceMessage[] {
  return turns.map((t) =>
    t.role === "user"
      ? { role: "user", content: t.content }
      : { role: "assistant", content: t.answer },
  );
}

// Phase 2G-4: Wall-bounce Chat。既存Phase 2F-4と同じstateless multi-turn
// パターン（毎request全履歴をクライアントが送る、Backendは会話セッションを保持しない）。
// Reading/Primary/Alternativeは作らず、scope/question/actors/regionsの改善提案
// （structured proposal）のみを扱う。
function WallBounceChat({
  candidate,
  onInvestigationQuestionsAvailable,
}: {
  candidate: GeoThemeCandidate;
  onInvestigationQuestionsAvailable?: (questions: string[]) => void;
}) {
  const { t } = useLanguage();
  const [turns, setTurns] = useState<WallBounceTurn[]>([]);
  const [message, setMessage] = useState("");
  const wallBounce = useThemeCandidateWallBounce();
  const applyRefinement = useApplyThemeCandidateRefinement();

  const latestProposal = [...turns]
    .reverse()
    .find(
      (t): t is Extract<WallBounceTurn, { role: "assistant" }> =>
        t.role === "assistant" && t.hasProposal,
    );

  // Phase 2G-5 §7: investigation_questionsはCandidate DBへ保存しない——Handoff Preview
  // dialogが「今の会話で出ている候補」を表示できるよう、最新値を都度親へ伝えるだけ。
  useEffect(() => {
    onInvestigationQuestionsAvailable?.(latestProposal?.proposal.investigation_questions ?? []);
  }, [latestProposal, onInvestigationQuestionsAvailable]);

  const send = () => {
    if (!message.trim() || wallBounce.isPending) return;
    const userTurn: WallBounceTurn = { role: "user", content: message };
    const historyBefore = turnsToWallBounceHistory(turns);
    setTurns((prev) => [...prev, userTurn]);
    setMessage("");
    wallBounce.mutate(
      { candidateId: candidate.candidate_id, message: userTurn.content, history: historyBefore },
      {
        onSuccess: (data) => {
          setTurns((prev) => [
            ...prev,
            {
              role: "assistant",
              answer: data.answer,
              hasProposal: data.has_proposal,
              proposal: data.proposal,
            },
          ]);
        },
      },
    );
  };

  const applyProposal = (proposal: GeoThemeCandidateRefinementProposal) => {
    const patch: Record<string, string | string[]> = {};
    if (proposal.proposed_title) patch.title = proposal.proposed_title;
    if (proposal.proposed_question) patch.question = proposal.proposed_question;
    if (proposal.proposed_actors.length > 0) patch.actors = proposal.proposed_actors;
    if (proposal.proposed_regions.length > 0) patch.regions = proposal.proposed_regions;
    if (Object.keys(patch).length === 0) return;
    applyRefinement.mutate({ candidateId: candidate.candidate_id, patch });
  };

  return (
    <div className="card-paper rounded-lg p-4 space-y-3">
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-[0.08em]">
        {t("geo.themeDiscovery.wallBounce.title")}
      </p>

      <div className="space-y-3 max-h-72 overflow-y-auto">
        {turns.map((turn, i) =>
          turn.role === "user" ? (
            <p key={i} className="text-sm font-medium text-foreground">
              {turn.content}
            </p>
          ) : (
            <p key={i} className="text-sm text-foreground whitespace-pre-wrap">
              {turn.answer}
            </p>
          ),
        )}
      </div>

      {wallBounce.isError && (
        <p className="text-sm text-destructive">{(wallBounce.error as Error).message}</p>
      )}

      <div className="flex gap-2">
        <Input
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder={t("geo.themeDiscovery.wallBounce.placeholder")}
          className="flex-1 h-9 text-sm"
          disabled={wallBounce.isPending}
        />
        <Button size="sm" onClick={send} disabled={wallBounce.isPending || !message.trim()}>
          {wallBounce.isPending ? (
            t("geo.themeDiscovery.wallBounce.thinking")
          ) : (
            <Send className="h-3.5 w-3.5" />
          )}
        </Button>
      </div>

      {latestProposal && (
        <div className="space-y-2 pt-3 border-t border-border/60">
          <p className="text-xs font-medium text-muted-foreground">
            {t("geo.themeDiscovery.wallBounce.proposalTitle")}
          </p>
          <div className="text-xs space-y-1">
            {latestProposal.proposal.proposed_title && (
              <p>
                <span className="text-muted-foreground">
                  {t("geo.themeDiscovery.wallBounce.proposal.title")}:{" "}
                </span>
                {latestProposal.proposal.proposed_title}
              </p>
            )}
            {latestProposal.proposal.proposed_question && (
              <p>
                <span className="text-muted-foreground">
                  {t("geo.themeDiscovery.wallBounce.proposal.question")}:{" "}
                </span>
                {latestProposal.proposal.proposed_question}
              </p>
            )}
            {latestProposal.proposal.proposed_actors.length > 0 && (
              <p>
                <span className="text-muted-foreground">
                  {t("geo.themeDiscovery.wallBounce.proposal.actors")}:{" "}
                </span>
                {latestProposal.proposal.proposed_actors.join(" / ")}
              </p>
            )}
            {latestProposal.proposal.proposed_regions.length > 0 && (
              <p>
                <span className="text-muted-foreground">
                  {t("geo.themeDiscovery.wallBounce.proposal.regions")}:{" "}
                </span>
                {latestProposal.proposal.proposed_regions.join(" / ")}
              </p>
            )}
            {latestProposal.proposal.proposed_scope && (
              <p>
                <span className="text-muted-foreground">
                  {t("geo.themeDiscovery.wallBounce.proposal.scope")}:{" "}
                </span>
                {latestProposal.proposal.proposed_scope}
              </p>
            )}
            {latestProposal.proposal.investigation_questions.length > 0 && (
              <p>
                <span className="text-muted-foreground">
                  {t("geo.themeDiscovery.wallBounce.proposal.investigationQuestions")}:{" "}
                </span>
                {latestProposal.proposal.investigation_questions.join(" / ")}
              </p>
            )}
            {latestProposal.proposal.rationale && (
              <p className="text-muted-foreground">{latestProposal.proposal.rationale}</p>
            )}
          </div>
          <Button
            size="sm"
            variant="outline"
            disabled={applyRefinement.isPending}
            onClick={() => applyProposal(latestProposal.proposal)}
          >
            {applyRefinement.isSuccess
              ? t("geo.themeDiscovery.wallBounce.applied")
              : t("geo.themeDiscovery.wallBounce.applyProposal")}
          </Button>
          {applyRefinement.isSuccess && (
            <p className="text-[11px] text-muted-foreground">
              {t("geo.themeDiscovery.wallBounce.applyHint")}
            </p>
          )}
          {applyRefinement.isError && (
            <p className="text-sm text-destructive">{(applyRefinement.error as Error).message}</p>
          )}
        </div>
      )}
    </div>
  );
}

// Phase 2G-5 §8: Handoff Preview。「この内容で相談を開始」を押すまでSession/Intake
// Contextは一切作成しない（開いただけでは副作用無し——previewは読み取り専用endpoint）。
function HandoffPreviewDialog({
  candidate,
  open,
  onOpenChange,
  availableInvestigationQuestions,
}: {
  candidate: GeoThemeCandidate;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  availableInvestigationQuestions: string[];
}) {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const preview = useThemeCandidateHandoffPreview();
  const handoff = useHandoffThemeCandidateToExpertAI();
  const [selected, setSelected] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!open) return;
    setSelected(new Set(availableInvestigationQuestions));
    preview.mutate({
      candidateId: candidate.candidate_id,
      investigationQuestions: availableInvestigationQuestions,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, candidate.candidate_id]);

  const toggle = (q: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(q)) next.delete(q);
      else next.add(q);
      return next;
    });
  };

  const confirm = () => {
    handoff.mutate(
      { candidateId: candidate.candidate_id, investigationQuestions: [...selected] },
      {
        onSuccess: (result) => {
          onOpenChange(false);
          navigate({
            to: "/geo/ios/analysis/$sessionId",
            params: { sessionId: result.analysis_session_id },
          });
        },
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("geo.themeDiscovery.handoff.previewTitle")}</DialogTitle>
        </DialogHeader>

        {preview.isPending ? (
          <p className="text-sm text-muted-foreground">...</p>
        ) : preview.isError ? (
          <p className="text-sm text-destructive">{(preview.error as Error).message}</p>
        ) : preview.data ? (
          <div className="space-y-3 text-sm">
            <div>
              <p className="text-xs font-medium text-muted-foreground">
                {t("geo.themeDiscovery.handoff.sessionTitle")}
              </p>
              <p>{preview.data.session_title}</p>
            </div>
            <div>
              <p className="text-xs font-medium text-muted-foreground">
                {t("geo.themeDiscovery.detail.question")}
              </p>
              <p>{preview.data.core_question}</p>
            </div>
            <div className="flex flex-wrap gap-2">
              {preview.data.actors.map((a) => (
                <Badge key={a} variant="outline">
                  {a}
                </Badge>
              ))}
              {preview.data.regions.map((r) => (
                <Badge key={r} variant="secondary">
                  {r}
                </Badge>
              ))}
            </div>
            <div>
              <p className="text-xs font-medium text-muted-foreground">
                {t("geo.themeDiscovery.detail.whyNow")}
              </p>
              <p className="text-muted-foreground">{preview.data.why_now}</p>
            </div>

            {availableInvestigationQuestions.length > 0 && (
              <div className="space-y-1.5 pt-2 border-t border-border/60">
                <p className="text-xs font-medium text-muted-foreground">
                  {t("geo.themeDiscovery.handoff.carryQuestions")}
                </p>
                {availableInvestigationQuestions.map((q) => (
                  <label key={q} className="flex items-start gap-2 text-xs cursor-pointer">
                    <Checkbox
                      checked={selected.has(q)}
                      onCheckedChange={() => toggle(q)}
                      className="mt-0.5"
                    />
                    <span>{q}</span>
                  </label>
                ))}
              </div>
            )}

            {preview.data.existing_session_id && (
              <p className="text-xs text-muted-foreground">
                {t("geo.themeDiscovery.handoff.alreadyHandedOff")}
              </p>
            )}
          </div>
        ) : null}

        {handoff.isError && (
          <p className="text-sm text-destructive">{(handoff.error as Error).message}</p>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("geo.themeDiscovery.handoff.cancel")}
          </Button>
          <Button onClick={confirm} disabled={handoff.isPending || preview.isPending}>
            {handoff.isPending
              ? t("geo.themeDiscovery.handoff.starting")
              : t("geo.themeDiscovery.handoff.confirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function GeoThemeDiscoveryPage() {
  const { t } = useLanguage();
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(null);
  // Detail Sheet用の既存Candidate（Wall-bounce/Handoff等の既存コンポーネントが使う型）
  const { data } = useThemeCandidates();
  const selectedCandidate =
    data?.candidates.find((c) => c.candidate_id === selectedCandidateId) ?? null;

  // ソース選択機能は廃止。常におすすめ上位を使うため、上書きは空のまま渡す
  const overrides = new Map<string, ThemeSourceOverride>();

  // Phase C §8: Episode Cluster Reviewで生成したTheme Candidateを「テーマ探索で確認」から
  // 遷移した直後、その候補の詳細Sheetを直接開く。
  useEffect(() => {
    const highlightId = consumeHighlightCandidateId();
    if (highlightId) setSelectedCandidateId(highlightId);
  }, []);

  // 情報ソースDBの選択モードから戻った場合は、保存済みの文脈だけ破棄する（ソース選択機能は廃止）
  useEffect(() => {
    clearPickContext();
  }, []);

  return (
    <>
      <TopBar breadcrumb={[{ label: t("nav.geoThemeDiscovery") }]} />
      <main className="flex-1 max-w-[1200px] w-full mx-auto px-8 py-8 space-y-6">
        <section className="border-b border-border pb-5 flex items-center gap-2.5">
          <Sparkles className="h-5 w-5 text-geo-teal" />
          <div>
            <h1 className="geo-heading text-4xl">{t("nav.geoThemeDiscovery")}</h1>
            <p className="text-sm text-muted-foreground mt-1">{t("geo.themeExplorer.subtitle")}</p>
          </div>
        </section>

        <ThemeExplorerPanel
          overrides={overrides}
          onOpenDetail={setSelectedCandidateId}
        />
      </main>

      <Sheet
        open={selectedCandidateId !== null}
        onOpenChange={(open) => !open && setSelectedCandidateId(null)}
      >
        <SheetContent className="w-full sm:max-w-2xl overflow-y-auto">
          <SheetHeader>
            <SheetTitle>{t("geo.themeDiscovery.detail.title")}</SheetTitle>
          </SheetHeader>
          <div className="mt-4 space-y-5">
            {selectedCandidate && (
              <>
                <section className="space-y-2">
                  <p className="text-xs font-medium text-muted-foreground uppercase tracking-[0.08em]">
                    {t("geo.themeExplorer.sources.heading")}
                  </p>
                  <ThemeSourceActions
                    target={{
                      candidate_id: selectedCandidate.candidate_id,
                      title: selectedCandidate.title,
                      question: selectedCandidate.question,
                    }}
                    override={overrides.get(selectedCandidate.candidate_id)}
                    showHints
                  />
                </section>
                <CandidateDetail candidate={selectedCandidate} />
              </>
            )}
          </div>
        </SheetContent>
      </Sheet>

    </>
  );
}
