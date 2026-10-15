import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { TopBar } from "@/components/top-bar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ExpertReviewLoop } from "@/components/geo/expert-review-loop";
import {
  useBuildFoundationPack, useCaseRetrievalRuns, useCloseGeoCase, useCreateGeoHypothesis,
  useDetectAnalyticalGaps, useDetectDeterministicGaps, useExpertAnalyzeCase, useExtractCandidates,
  useGeoCase, useGeoCaseGaps, useGeoHypotheses, useRetrievalRunDetail, useStartReviewSession,
} from "@/lib/geoKnowledgeApi";
import { useLanguage } from "@/lib/i18n";

export const Route = createFileRoute("/geo/cases/$id")({
  component: GeoCaseDetailPage,
});

function GeoCaseDetailPage() {
  const { id } = Route.useParams();
  const { t } = useLanguage();
  const [actorId, setActorId] = useState("analyst1");
  const [hypothesisText, setHypothesisText] = useState("");
  const [hypothesisType, setHypothesisType] = useState<"main" | "alternative">("main");
  const [expertQueryText, setExpertQueryText] = useState("");
  const [selectedTraceRunId, setSelectedTraceRunId] = useState<string | null>(null);

  // Phase2B-2: Internal Expert Learning Layer（Expert Review Session）
  const [reviewThemeId, setReviewThemeId] = useState("");
  const [draftText, setDraftText] = useState("");
  const [finalText, setFinalText] = useState("");
  const [reviewSessionId, setReviewSessionId] = useState<string | null>(null);

  const caseQuery = useGeoCase(id);
  const hypothesesQuery = useGeoHypotheses(id);
  const gapsQuery = useGeoCaseGaps(id);
  const foundationPackMutation = useBuildFoundationPack(id);
  const deterministicGapsMutation = useDetectDeterministicGaps(id);
  const analyticalGapsMutation = useDetectAnalyticalGaps(id);
  const candidatesMutation = useExtractCandidates(id);
  const closeCaseMutation = useCloseGeoCase(id);
  const createHypothesisMutation = useCreateGeoHypothesis(id);
  const expertAnalyzeMutation = useExpertAnalyzeCase(id);
  const retrievalRunsQuery = useCaseRetrievalRuns(id);
  const retrievalRunDetailQuery = useRetrievalRunDetail(selectedTraceRunId ?? "");

  const startReviewSessionMutation = useStartReviewSession();

  const geoCase = caseQuery.data;

  return (
    <>
      <TopBar breadcrumb={[{ label: t("nav.geoCases"), href: "/geo/cases" }, { label: geoCase?.case_title ?? id }]} />
      <main className="flex-1 max-w-[1100px] w-full mx-auto px-8 py-8 space-y-6">
        {geoCase && (
          <section className="border-b border-border pb-5">
            <h1 className="text-editorial text-3xl">{geoCase.case_title}</h1>
            <p className="text-sm text-muted-foreground mt-2">{geoCase.original_question}</p>
            <Badge variant="outline" className="mt-2">{geoCase.status}</Badge>
          </section>
        )}

        <div className="card-paper rounded-lg p-4 space-y-2">
          <label className="text-xs text-muted-foreground">{t("geo.cases.actorIdLabel")}</label>
          <Input value={actorId} onChange={(e) => setActorId(e.target.value)} className="max-w-xs" />
          <p className="text-[10px] text-muted-foreground">{t("geo.knowledge.roleWarning")}</p>
        </div>

        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={() => foundationPackMutation.mutate()}
                  disabled={foundationPackMutation.isPending}>
            {t("geo.case.foundationPack")}
          </Button>
          <Button variant="outline" onClick={() => deterministicGapsMutation.mutate(actorId)}
                  disabled={deterministicGapsMutation.isPending}>
            {t("geo.case.detectDeterministicGaps")}
          </Button>
          <Button variant="outline" onClick={() => analyticalGapsMutation.mutate(actorId)}
                  disabled={analyticalGapsMutation.isPending}>
            {t("geo.case.detectAnalyticalGaps")}
          </Button>
          <Button variant="outline" onClick={() => candidatesMutation.mutate(actorId)}
                  disabled={candidatesMutation.isPending}>
            {t("geo.case.extractCandidates")}
          </Button>
          <Button variant="destructive" onClick={() => closeCaseMutation.mutate({ actor_id: actorId })}
                  disabled={closeCaseMutation.isPending}>
            {t("geo.case.closeCase")}
          </Button>
        </div>

        {foundationPackMutation.data && (
          <div className="card-paper rounded-lg p-4 overflow-x-auto">
            <h2 className="text-editorial text-lg mb-2">Foundation Pack</h2>
            <pre className="text-xs whitespace-pre-wrap">{JSON.stringify(foundationPackMutation.data, null, 2)}</pre>
          </div>
        )}

        {candidatesMutation.data && (
          <div className="card-paper rounded-lg p-4 space-y-1">
            <h2 className="text-editorial text-lg">Knowledge Candidates</h2>
            {candidatesMutation.data.created.map((c) => (
              <div key={c.knowledge_item_id} className="text-sm">
                <Badge variant="outline" className="mr-2">{c.knowledge_type}</Badge>{c.title}
              </div>
            ))}
          </div>
        )}

        <div className="card-paper rounded-lg p-4 space-y-3">
          <h2 className="text-editorial text-lg">Expert Engine（Phase2A: Knowledge DB統合）</h2>
          <Textarea value={expertQueryText} onChange={(e) => setExpertQueryText(e.target.value)}
                    placeholder="分析してほしい問いを入力" className="min-h-[70px] text-sm" />
          <Button onClick={() => {
            if (!expertQueryText.trim()) return;
            expertAnalyzeMutation.mutate({ query_text: expertQueryText });
          }} disabled={expertAnalyzeMutation.isPending}>
            {expertAnalyzeMutation.isPending ? "分析中..." : "Expert Engine実行"}
          </Button>
          {expertAnalyzeMutation.isError && (
            <p className="text-xs text-destructive">{(expertAnalyzeMutation.error as Error).message}</p>
          )}
          {expertAnalyzeMutation.data && (
            <div className="space-y-2 pt-2 border-t border-border">
              <Badge variant="outline">
                Quality Guard {expertAnalyzeMutation.data.qualityEvaluation.passedCount}/
                {expertAnalyzeMutation.data.qualityEvaluation.passedCount + expertAnalyzeMutation.data.qualityEvaluation.failedCount}
              </Badge>
              <pre className="text-xs whitespace-pre-wrap overflow-x-auto">
                {JSON.stringify(expertAnalyzeMutation.data.response, null, 2)}
              </pre>
            </div>
          )}

          <div className="pt-2 border-t border-border space-y-1">
            <p className="text-xs text-muted-foreground">Retrieval Trace（過去の実行履歴）</p>
            {(retrievalRunsQuery.data?.retrieval_runs ?? []).map((run) => (
              <button key={run.retrieval_run_id}
                      onClick={() => setSelectedTraceRunId(run.retrieval_run_id)}
                      className={`block w-full text-left text-xs px-2 py-1 rounded hover:bg-muted ${
                        selectedTraceRunId === run.retrieval_run_id ? "bg-muted" : ""
                      }`}>
                {run.retrieval_mode} — {run.retrieved_count}件取得
              </button>
            ))}
            {retrievalRunDetailQuery.data && (
              <div className="mt-2 space-y-1">
                {retrievalRunDetailQuery.data.results.map((r, i) => (
                  <div key={i} className="text-xs flex items-center gap-2">
                    <Badge variant={r.used_in_final_context ? "default" : "secondary"}>
                      {r.used_in_final_context ? "採用" : "除外"}
                    </Badge>
                    <span>{r.result_type}</span>
                    {r.final_score !== undefined && <span className="tabular-nums">score={r.final_score.toFixed(2)}</span>}
                    {r.exclusion_reason && <span className="text-muted-foreground">({r.exclusion_reason})</span>}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="card-paper rounded-lg p-4 space-y-3">
          <h2 className="text-editorial text-lg">Expert Review（Phase2B-2: Internal Expert Learning Layer）</h2>
          <p className="text-xs text-muted-foreground">
            AI DraftとExpert Finalの差分から、専門家が何を重視し・どう仮説を修正したかを抽出する。
            正誤判定ではなく、他案件でも再利用する価値があるかをレビューする。
          </p>

          {!reviewSessionId ? (
            <div className="space-y-2">
              <Input value={reviewThemeId} onChange={(e) => setReviewThemeId(e.target.value)}
                     placeholder="theme_id" className="max-w-xs text-sm" />
              <Textarea value={draftText} onChange={(e) => setDraftText(e.target.value)}
                        placeholder="AI Draft本文" className="min-h-[70px] text-sm" />
              <Textarea value={finalText} onChange={(e) => setFinalText(e.target.value)}
                        placeholder="Expert Final本文" className="min-h-[70px] text-sm" />
              <Button size="sm" disabled={startReviewSessionMutation.isPending}
                      onClick={() => {
                        if (!reviewThemeId.trim() || !finalText.trim()) return;
                        startReviewSessionMutation.mutate(
                          { theme_id: reviewThemeId, reviewer_id: actorId, case_id: id,
                            draft_text: draftText || undefined, final_text: finalText },
                          { onSuccess: (session) => setReviewSessionId(session.review_session_id) },
                        );
                      }}>
                Review Session開始
              </Button>
            </div>
          ) : (
            <ExpertReviewLoop reviewSessionId={reviewSessionId} actorId={actorId} />
          )}
        </div>

        <div className="card-paper rounded-lg p-4 space-y-3">
          <h2 className="text-editorial text-lg">{t("geo.case.hypotheses")}</h2>
          {(hypothesesQuery.data?.hypotheses ?? []).map((h) => (
            <div key={h.hypothesis_id} className="text-sm border-t border-border pt-2">
              <Badge variant={h.hypothesis_type === "main" ? "default" : "secondary"} className="mr-2">
                {h.hypothesis_type}
              </Badge>
              {h.hypothesis_statement}
            </div>
          ))}
          <div className="space-y-2 pt-2 border-t border-border">
            <Textarea value={hypothesisText} onChange={(e) => setHypothesisText(e.target.value)}
                      placeholder={t("geo.case.hypothesisStatementLabel")} className="min-h-[60px] text-sm" />
            <div className="flex gap-2 items-center">
              <select className="border border-input rounded px-2 py-1 text-sm bg-background"
                      value={hypothesisType} onChange={(e) => setHypothesisType(e.target.value as "main" | "alternative")}>
                <option value="main">{t("geo.case.hypothesisTypeMain")}</option>
                <option value="alternative">{t("geo.case.hypothesisTypeAlternative")}</option>
              </select>
              <Button size="sm" onClick={() => {
                if (!hypothesisText.trim()) return;
                createHypothesisMutation.mutate(
                  { hypothesis_statement: hypothesisText, hypothesis_type: hypothesisType, actor_id: actorId },
                  { onSuccess: () => setHypothesisText("") },
                );
              }}>{t("geo.case.addHypothesis")}</Button>
            </div>
          </div>
        </div>

        <div className="card-paper rounded-lg p-4 space-y-2">
          <h2 className="text-editorial text-lg">{t("geo.case.gaps")}</h2>
          {(gapsQuery.data?.gaps ?? []).map((g) => (
            <div key={g.gap_id} className="text-sm border-t border-border pt-2">
              <Badge variant="outline" className="mr-2">
                {g.gap_category === "deterministic" ? t("geo.case.gapDeterministic") : t("geo.case.gapAnalytical")}
              </Badge>
              <Badge variant="secondary" className="mr-2">{g.priority}</Badge>
              {g.gap_description}
            </div>
          ))}
        </div>
      </main>
    </>
  );
}
