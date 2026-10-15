import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { CheckCircle2, ChevronDown, XCircle } from "lucide-react";
import { TopBar } from "@/components/top-bar";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { GeoExpertAiProgress } from "@/components/geo/geo-expert-ai-progress";
import {
  GEO_EXPERT_MODES, GEO_EXPERT_PROFILES, GeoExpertMode, GeoExpertProfile, GeoExpertResult, useGeoThemes,
  useGeoSubmitExpertRequest, useGeoSubmitExpertFeedback, useGeoExpertRequests, useGeoExpertRequest,
} from "@/lib/api";
import { useLanguage } from "@/lib/i18n";

// テーマ未選択を表すRadix Select向けの番人値（Radixは value="" を「未選択」の
// 内部状態と区別できないため使えない）。APIへ送る直前にundefinedへ変換する。
const NO_THEME = "__none__";

export const Route = createFileRoute("/geo/expert")({
  component: GeoExpertPage,
});

const DRAFT_MODES: GeoExpertMode[] = ["outline_review", "draft_review"];

function ClaimsList({ claims }: { claims: GeoExpertResult["response"]["claims"] }) {
  const { t } = useLanguage();
  if (!claims.length) return null;
  return (
    <div className="space-y-1.5">
      {claims.map((c, i) => (
        <div key={i} className="flex items-start gap-2 text-sm">
          <Badge variant="outline" className="shrink-0 text-[10px]">
            {t(`geo.expert.claimType.${c.claim_type}`)}
          </Badge>
          <span>{c.text}{c.source_ref && <span className="text-xs text-muted-foreground ml-1">[{c.source_ref}]</span>}</span>
        </div>
      ))}
    </div>
  );
}

function ResultView({ result }: { result: GeoExpertResult }) {
  const { t } = useLanguage();
  const r = result.response;
  const feedbackMutation = useGeoSubmitExpertFeedback();
  const [feedbackSent, setFeedbackSent] = useState<string | null>(null);

  const sendFeedback = (rating: string) => {
    feedbackMutation.mutate({ responseId: result.responseId, rating }, {
      onSuccess: () => setFeedbackSent(rating),
    });
  };

  return (
    <div className="space-y-4">
      <div className="card-paper rounded-lg p-5 space-y-2">
        <h2 className="text-editorial text-lg">{t("geo.expert.oneLineAssessment")}</h2>
        <p className="text-sm leading-relaxed">{r.one_line_assessment}</p>
        {r.initial_anomaly && (
          <p className="text-xs text-muted-foreground"><b>{t("geo.expert.initialAnomaly")}:</b> {r.initial_anomaly}</p>
        )}
      </div>

      {r.claims.length > 0 && (
        <div className="card-paper rounded-lg p-5 space-y-2">
          <h2 className="text-editorial text-base">{t("geo.expert.claims")}</h2>
          <ClaimsList claims={r.claims} />
        </div>
      )}

      {r.stakeholders.length > 0 && (
        <div className="card-paper rounded-lg p-5 space-y-2 overflow-x-auto">
          <h2 className="text-editorial text-base">{t("geo.expert.stakeholders")}</h2>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-muted-foreground">
                <th className="pr-3 py-1">{t("geo.expert.actor")}</th>
                <th className="pr-3 py-1">{t("geo.expert.purpose")}</th>
                <th className="pr-3 py-1">{t("geo.expert.constraints")}</th>
                <th className="pr-3 py-1">{t("geo.expert.redLines")}</th>
              </tr>
            </thead>
            <tbody>
              {r.stakeholders.map((s, i) => (
                <tr key={i} className="border-t border-border">
                  <td className="pr-3 py-1.5 font-medium">{s.actor}</td>
                  <td className="pr-3 py-1.5">{s.purpose ?? "—"}</td>
                  <td className="pr-3 py-1.5">{s.constraints_fears ?? "—"}</td>
                  <td className="pr-3 py-1.5">{s.red_lines ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {r.dynamics && (
        <div className="card-paper rounded-lg p-5 space-y-1">
          <h2 className="text-editorial text-base">{t("geo.expert.dynamics")}</h2>
          <p className="text-sm">{r.dynamics}</p>
        </div>
      )}

      {r.key_stakeholder_assessment && (
        <div className="card-paper rounded-lg p-5 space-y-2">
          <div className="flex items-center gap-2">
            <h2 className="text-editorial text-base">Key Stakeholder</h2>
            <Badge variant="outline" className="text-[10px]">{r.key_stakeholder_assessment.determination}</Badge>
          </div>
          <p className="text-sm">{r.key_stakeholder_assessment.rationale}</p>
          {r.key_stakeholder_assessment.decision_structure && (
            <p className="text-xs text-muted-foreground">
              <b>分散意思決定構造:</b> {r.key_stakeholder_assessment.decision_structure}
            </p>
          )}
          <Badge variant="secondary">{t("geo.expert.confidence")}: {r.key_stakeholder_assessment.confidence}</Badge>
        </div>
      )}

      {(r.key_actor_profiles ?? []).length > 0 && (
        <div className="card-paper rounded-lg p-5 space-y-2 overflow-x-auto">
          <div className="flex items-center gap-2">
            <h2 className="text-editorial text-base">Key Actor Profile</h2>
            {r.actor_intelligence_status && (
              <Badge variant="outline" className="text-[10px]">
                Actor Intelligence: {r.actor_intelligence_status}
              </Badge>
            )}
          </div>
          <div className="space-y-3">
            {r.key_actor_profiles!.map((kap) => (
              <div key={kap.actor_ref} className="border-t border-border pt-2 first:border-0 first:pt-0 space-y-1 text-xs">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{kap.actor}</span>
                  <Badge variant="secondary" className="text-[10px]">{kap.confidence}</Badge>
                </div>
                {kap.ultimate_objective && <p><b>Ultimate Objective:</b> {kap.ultimate_objective}</p>}
                {kap.persistent_preference && <p><b>Persistent Preference:</b> {kap.persistent_preference}</p>}
                {kap.issue_objective && <p><b>Issue Objective:</b> {kap.issue_objective}</p>}
                {kap.observed_action && <p><b>Observed Action:</b> {kap.observed_action}</p>}
                {kap.preference_action_gap && (
                  <p className="text-amber-700 dark:text-amber-500"><b>Preference-Action Gap:</b> {kap.preference_action_gap}</p>
                )}
                {(kap.driving_forces ?? []).length > 0 && (
                  <p><b>Driving Forces:</b> {kap.driving_forces!.map((f) => `${f.force}(${f.direction})`).join(", ")}</p>
                )}
                {(kap.primary_audience || kap.secondary_audience) && (
                  <p><b>Audience:</b> {[kap.primary_audience, kap.secondary_audience].filter(Boolean).join(" / ")}</p>
                )}
                {/* Actor Intelligence Phase AI-3 */}
                {kap.why_actor_matters && <p><b>Why This Actor Matters:</b> {kap.why_actor_matters}</p>}
                {(kap.available_leverage ?? []).length > 0 && (
                  <p><b>Available Leverage:</b> {kap.available_leverage!.join(", ")}</p>
                )}
                {(kap.unavailable_or_costly_cards ?? []).length > 0 && (
                  <p><b>Unavailable/Costly Cards:</b> {kap.unavailable_or_costly_cards!.join(", ")}</p>
                )}
                {(kap.vulnerabilities ?? []).length > 0 && (
                  <p className="text-amber-700 dark:text-amber-500"><b>Vulnerabilities:</b> {kap.vulnerabilities!.join(", ")}</p>
                )}
                {(kap.relevant_decision_patterns ?? []).length > 0 && (
                  <p><b>Decision Patterns:</b> {kap.relevant_decision_patterns!.join("; ")}</p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="card-paper rounded-lg p-5 space-y-2">
        <h2 className="text-editorial text-base">{t("geo.expert.mainScenario")}</h2>
        <p className="text-sm">{r.main_scenario.description}</p>
        <Badge variant="secondary">{t("geo.expert.confidence")}: {r.main_scenario.confidence}</Badge>
        {r.main_scenario.selection_rationale && (
          <p className="text-xs text-muted-foreground"><b>選択理由:</b> {r.main_scenario.selection_rationale}</p>
        )}
        {(r.main_scenario.supporting_evidence ?? []).length > 0 && (
          <ul className="text-xs text-muted-foreground list-disc list-inside">
            {r.main_scenario.supporting_evidence!.map((e, i) => <li key={i}>{e}</li>)}
          </ul>
        )}
        {(r.alternative_scenarios ?? []).length > 0 && (
          <div className="pt-1">
            <p className="text-xs font-medium text-muted-foreground">{t("geo.expert.alternativeScenarios")}</p>
            <ul className="text-xs list-disc list-inside space-y-0.5">
              {r.alternative_scenarios!.map((s, i) => (
                <li key={i}>
                  {s.description}
                  {(s.switch_conditions ?? []).length > 0 && (
                    <span className="text-muted-foreground"> — 切替条件: {s.switch_conditions!.join("; ")}</span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {(r.identified_knowledge_gaps ?? []).length > 0 && (
        <div className="card-paper rounded-lg p-5 space-y-1">
          <h2 className="text-editorial text-base">不足情報</h2>
          <ul className="text-sm list-disc list-inside space-y-0.5">
            {r.identified_knowledge_gaps!.map((g, i) => <li key={i}>{g.description}</li>)}
          </ul>
        </div>
      )}

      <div className="card-paper rounded-lg p-5 space-y-2">
        <h2 className="text-editorial text-base">{t("geo.expert.counterEvidence")}</h2>
        <p className="text-sm"><b>{t("geo.expert.strongestCounterargument")}:</b> {r.strongest_counterargument}</p>
        {r.weakening_information && <p className="text-sm">{r.weakening_information}</p>}
        <div>
          <p className="text-xs font-medium text-muted-foreground">{t("geo.expert.retractionConditions")}</p>
          <ul className="text-xs list-disc list-inside">
            {r.retraction_conditions.map((c, i) => <li key={i}>{c}</li>)}
          </ul>
        </div>
      </div>

      {(r.additional_research ?? []).length > 0 && (
        <div className="card-paper rounded-lg p-5 space-y-2">
          <h2 className="text-editorial text-base">{t("geo.expert.additionalResearch")}</h2>
          <ul className="text-sm space-y-1">
            {r.additional_research!.map((q, i) => (
              <li key={i}>
                {q.question}
                {q.recommended_sources && <span className="text-xs text-muted-foreground ml-1">({q.recommended_sources})</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {r.business_impact && (
        <div className="card-paper rounded-lg p-5 space-y-1.5">
          <h2 className="text-editorial text-base">{t("geo.expert.businessImpact")}</h2>
          {r.business_impact.direct && <p className="text-sm"><b>{t("geo.expert.direct")}:</b> {r.business_impact.direct}</p>}
          {r.business_impact.indirect && <p className="text-sm"><b>{t("geo.expert.indirect")}:</b> {r.business_impact.indirect}</p>}
          {r.business_impact.environment_change && <p className="text-sm"><b>{t("geo.expert.environmentChange")}:</b> {r.business_impact.environment_change}</p>}
          {r.business_impact.unrelated_note && <p className="text-sm text-muted-foreground">{r.business_impact.unrelated_note}</p>}
        </div>
      )}

      {r.executive_summary && (r.executive_summary.what_changed || r.executive_summary.why_it_matters) && (
        <div className="card-paper rounded-lg p-5 space-y-1.5">
          <h2 className="text-editorial text-base">{t("geo.expert.executiveSummary")}</h2>
          {r.executive_summary.what_changed && <p className="text-sm"><b>What changed:</b> {r.executive_summary.what_changed}</p>}
          {r.executive_summary.why_it_matters && <p className="text-sm"><b>Why it matters:</b> {r.executive_summary.why_it_matters}</p>}
          {r.executive_summary.what_to_watch_decide && <p className="text-sm"><b>What to watch/decide:</b> {r.executive_summary.what_to_watch_decide}</p>}
        </div>
      )}

      {(r.analyst_coaching_notes ?? []).length > 0 && (
        <div className="card-paper rounded-lg p-5 space-y-1">
          <h2 className="text-editorial text-base">{t("geo.expert.coachingNotes")}</h2>
          <ul className="text-sm list-disc list-inside">
            {r.analyst_coaching_notes!.map((n, i) => <li key={i}>{n}</li>)}
          </ul>
        </div>
      )}

      {r.unknown_or_uncertain && (
        <div className="card-paper rounded-lg p-5">
          <p className="text-sm text-muted-foreground">{r.unknown_or_uncertain}</p>
        </div>
      )}

      {r.root_premise_check && (
        <div className="card-paper rounded-lg p-5 space-y-2">
          <div className="flex items-center gap-2">
            <h2 className="text-editorial text-base">Root Premise Check</h2>
            <Badge variant={r.root_premise_check.overall_result === "fail" ? "destructive" : "secondary"} className="text-[10px]">
              {r.root_premise_check.overall_result}
            </Badge>
          </div>
          <ul className="text-xs space-y-1">
            {([
              ["Key Stakeholderの妥当性", r.root_premise_check.key_stakeholder_validity],
              ["Persistent Preferenceの根拠", r.root_premise_check.persistent_preference_grounding],
              ["目的階層の分離", r.root_premise_check.objective_hierarchy_separation],
              ["Preference-Action Gapの検討", r.root_premise_check.preference_action_gap_analysis],
              ["今回行動を上位目的化していないか", r.root_premise_check.observed_action_not_treated_as_ultimate_objective],
            ] as const).map(([label, item]) => (
              <li key={label} className="flex items-start gap-1.5">
                {item.result === "pass"
                  ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 shrink-0 mt-0.5" />
                  : <XCircle className="h-3.5 w-3.5 text-destructive shrink-0 mt-0.5" />}
                <span><Badge variant="outline" className="text-[10px] mr-1">{item.result}</Badge>{label}: {item.assessment}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {(r.review_findings ?? []).length > 0 && (
        <div className="card-paper rounded-lg p-5 space-y-2">
          <h2 className="text-editorial text-base">Review Findings</h2>
          {(["Critical", "Major", "Minor"] as const).map((severity) => {
            const items = r.review_findings!.filter((f) => f.severity === severity);
            if (!items.length) return null;
            return (
              <div key={severity} className="space-y-1">
                <Badge variant={severity === "Critical" ? "destructive" : "outline"} className="text-[10px]">{severity}</Badge>
                <ul className="text-xs list-disc list-inside space-y-0.5">
                  {items.map((f, i) => (
                    <li key={i}>{f.finding}{f.recommendation && <span className="text-muted-foreground"> — {f.recommendation}</span>}</li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
      )}

      <div className="card-paper rounded-lg p-5 space-y-2">
        <div className="flex items-center justify-between">
          <h2 className="text-editorial text-base">{t("geo.expert.qualityGuard")}</h2>
          <span className="text-xs text-muted-foreground tabular-nums">
            {result.qualityEvaluation.passedCount}/{result.qualityEvaluation.checks.length}
          </span>
        </div>
        {result.qualityEvaluation.completenessStatus && (
          <Badge variant={result.qualityEvaluation.completenessStatus === "complete" ? "secondary" : "destructive"} className="text-[10px]">
            {result.qualityEvaluation.completenessStatus}
            {result.qualityEvaluation.repairAttempted ? "（修復生成を実施）" : ""}
          </Badge>
        )}
        <ul className="text-xs space-y-1">
          {result.qualityEvaluation.checks.map((c) => (
            <li key={c.name} className="flex items-start gap-1.5">
              {c.passed
                ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 shrink-0 mt-0.5" />
                : <XCircle className="h-3.5 w-3.5 text-destructive shrink-0 mt-0.5" />}
              <span>{c.name}{c.detail && <span className="text-muted-foreground"> — {c.detail}</span>}</span>
            </li>
          ))}
        </ul>
        {(result.qualityEvaluation.remainingFailedChecks ?? []).length > 0 && (
          <p className="text-xs text-destructive">
            修復後も残っている不足項目: {result.qualityEvaluation.remainingFailedChecks!.join(", ")}
          </p>
        )}
        <p className="text-[10px] text-muted-foreground">{t("geo.expert.qualityGuardDisclaimer")}</p>
        <div className="flex items-center gap-2 pt-1">
          <Button size="sm" variant="outline" onClick={() => sendFeedback("good")} disabled={!!feedbackSent}>
            {t("geo.expert.feedbackGood")}
          </Button>
          <Button size="sm" variant="outline" onClick={() => sendFeedback("needs_improvement")} disabled={!!feedbackSent}>
            {t("geo.expert.feedbackNeedsImprovement")}
          </Button>
          <Button size="sm" variant="outline" onClick={() => sendFeedback("incorrect")} disabled={!!feedbackSent}>
            {t("geo.expert.feedbackIncorrect")}
          </Button>
          {feedbackSent && <span className="text-xs text-muted-foreground">{t("geo.expert.feedbackSent")}</span>}
        </div>
        <p className="text-[10px] text-muted-foreground">
          {t("geo.expert.versionsLabel")}: {result.promptVersion} / {result.knowledgeVersion} / {result.model}
        </p>
        {result.executionMetadata && (
          <p className="text-[10px] text-muted-foreground">
            {t("geo.expert.executionMetadataLabel")}: {result.executionMetadata.executionMode ?? "—"}
            {result.executionMetadata.profile ? ` / ${result.executionMetadata.profile}` : ""}
            {(result.executionMetadata.stage2Modules ?? []).length > 0
              ? ` / stage2: ${result.executionMetadata.stage2Modules!.join("+")}`
              : ""}
          </p>
        )}
      </div>
    </div>
  );
}

function GeoExpertPage() {
  const { t } = useLanguage();
  const { data: themesData } = useGeoThemes();
  const themes = themesData?.themes ?? [];
  // テーマ・モードは既定値（テーマ指定なし／ケース分析）で即質問できるようにし、
  // 詳細設定は任意の折りたたみに置く（以前はSelectを埋めないと質問できないように見えていた）。
  const [themeId, setThemeId] = useState<string>(NO_THEME);
  const [mode, setMode] = useState<GeoExpertMode>("case_analysis");
  // v1.2 段階実行(Staged Execution): 既定はstandard。quick/deepは詳細設定内でのみ選ぶ
  // （通常利用者は処理方式を意識しなくてよい状態を維持する）。
  const [profile, setProfile] = useState<GeoExpertProfile>("standard");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [queryText, setQueryText] = useState("");
  const [draftText, setDraftText] = useState("");
  const [activeRequestId, setActiveRequestId] = useState<string | null>(null);

  const submitMutation = useGeoSubmitExpertRequest();
  const historyQuery = useGeoExpertRequests(themeId === NO_THEME ? "" : themeId);
  const activeRequestQuery = useGeoExpertRequest(activeRequestId ?? "");

  const onSubmit = () => {
    if (!queryText.trim()) return;
    submitMutation.mutate(
      { mode, themeId: themeId === NO_THEME ? undefined : themeId, queryText, draftText: draftText || undefined, profile },
      { onSuccess: (result) => setActiveRequestId(result.requestId) },
    );
  };

  // v1.2: 生成直後のmutationの戻り値にはexecutionMetadata/completenessStatus等の
  // 生成時メタ情報が入っているが、GET再取得（activeRequestQuery、履歴クリック時等）は
  // DBに永続化されていないため持たない。同じrequestIdを指している間は、より情報が多い
  // mutationの結果を優先して表示する（履歴の別項目をクリックした場合はrequestIdが
  // 一致しなくなるため、自然にactiveRequestQuery側へフォールバックする）。
  const displayedResult =
    (submitMutation.data && submitMutation.data.requestId === activeRequestId ? submitMutation.data : undefined)
    ?? activeRequestQuery.data;

  return (
    <>
      <TopBar breadcrumb={[{ label: t("nav.geoExpertAI") }]} />
      <main className="flex-1 max-w-[1440px] w-full mx-auto px-8 py-8 space-y-6">
        <section className="border-b border-border pb-5">
          <h1 className="text-editorial text-4xl">{t("geo.expert.title")}</h1>
          <p className="text-sm text-muted-foreground mt-2">{t("geo.expert.subtitle")}</p>
        </section>

        <div className="grid grid-cols-[280px_1fr] gap-6">
          <aside className="space-y-4">
            <div className="card-paper rounded-lg p-4 space-y-3">
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">{t("geo.expert.queryLabel")}</label>
                <Textarea value={queryText} onChange={(e) => setQueryText(e.target.value)}
                  placeholder={t("geo.expert.queryPlaceholder")} className="min-h-[100px] text-sm" />
              </div>

              <Collapsible open={showAdvanced} onOpenChange={setShowAdvanced}>
                <CollapsibleTrigger className="flex w-full items-center justify-between text-xs text-muted-foreground hover:text-foreground transition-colors">
                  <span>{t("geo.expert.advancedSettings")}</span>
                  <ChevronDown className={`h-3.5 w-3.5 transition-transform ${showAdvanced ? "rotate-180" : ""}`} />
                </CollapsibleTrigger>
                <CollapsibleContent className="space-y-3 pt-3">
                  <div className="space-y-1">
                    <label className="text-xs text-muted-foreground">{t("geo.expert.themeLabel")}</label>
                    <Select value={themeId} onValueChange={setThemeId}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value={NO_THEME}>{t("geo.expert.themeNone")}</SelectItem>
                        {themes.map((th) => <SelectItem key={th.id} value={th.id}>{th.nameJa}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1">
                    <label className="text-xs text-muted-foreground">{t("geo.expert.modeLabel")}</label>
                    <Select value={mode} onValueChange={(v) => setMode(v as GeoExpertMode)}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {GEO_EXPERT_MODES.map((m) => (
                          <SelectItem key={m} value={m}>{t(`geo.expert.mode.${m}`)}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1">
                    <label className="text-xs text-muted-foreground">{t("geo.expert.profileLabel")}</label>
                    <Select value={profile} onValueChange={(v) => setProfile(v as GeoExpertProfile)}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {GEO_EXPERT_PROFILES.map((p) => (
                          <SelectItem key={p} value={p}>{t(`geo.expert.profile.${p}`)}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </CollapsibleContent>
              </Collapsible>

              {DRAFT_MODES.includes(mode) && (
                <div className="space-y-1">
                  <label className="text-xs text-muted-foreground">{t("geo.expert.draftLabel")}</label>
                  <Textarea value={draftText} onChange={(e) => setDraftText(e.target.value)}
                    className="min-h-[120px] text-sm" />
                </div>
              )}
              <Button className="w-full" onClick={onSubmit} disabled={submitMutation.isPending || !queryText.trim()}>
                {submitMutation.isPending ? t("geo.expert.submitting") : t("geo.expert.submit")}
              </Button>
              {submitMutation.isError && (
                <p className="text-xs text-destructive">{(submitMutation.error as Error).message}</p>
              )}
            </div>

            <div className="card-paper rounded-lg p-4 space-y-2">
              <p className="text-xs font-medium text-muted-foreground">{t("geo.expert.history")}</p>
              <div className="space-y-0.5 max-h-72 overflow-y-auto">
                {(historyQuery.data?.requests ?? []).map((h) => (
                  <button
                    key={h.requestId}
                    onClick={() => setActiveRequestId(h.requestId)}
                    className={`w-full text-left px-2 py-1.5 rounded text-xs hover:bg-muted transition-colors ${
                      activeRequestId === h.requestId ? "bg-muted" : ""
                    }`}
                  >
                    <div className="truncate">{h.queryText}</div>
                    <div className="text-muted-foreground">{t(`geo.expert.mode.${h.mode}`)}</div>
                  </button>
                ))}
              </div>
            </div>
          </aside>

          <div>
            {submitMutation.isPending ? (
              <div className="space-y-2">
                <p className="text-muted-foreground">{t("geo.expert.analyzing")}</p>
                <GeoExpertAiProgress active={submitMutation.isPending} />
              </div>
            ) : displayedResult ? (
              <ResultView result={displayedResult} />
            ) : (
              <p className="text-sm text-muted-foreground">{t("geo.expert.empty")}</p>
            )}
          </div>
        </div>
      </main>
    </>
  );
}
