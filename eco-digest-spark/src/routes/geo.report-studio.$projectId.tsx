import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { TopBar } from "@/components/top-bar";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { GeoCard, GeoCardBody, GeoCardHeader } from "@/components/geo/geo-card";
import { GeoStatusBadge } from "@/components/geo/geo-badge";
import { ChangeTypeBadge, WatchpointStatusBadge } from "@/components/geo/geo-report-provenance";
import { SourceResearchPanel } from "@/components/geo/source-research-panel";
import { GeoReportPipelinePanel, isPinnedRun } from "@/components/geo/geo-report-pipeline-panel";
import {
  useGeoReportOverview,
  useGeoReportProject,
  useGeoReportRuns,
} from "@/lib/geoReportStudioApi";
import { useLanguage } from "@/lib/i18n";

interface GeoReportStudioSearch {
  tab?: string;
  runId?: string;
}

const _VALID_TABS = new Set(["overview", "pipeline", "research"]);

export const Route = createFileRoute("/geo/report-studio/$projectId")({
  // Monitoring/Intelligence NeedsからのDeep Link用（例: ?tab=assessment&runId=...）。
  // 旧Assessmentタブは廃止済みのため、未知のtab値はoverviewへフォールバックする
  // （runIdは引き継がれるので、ユーザーは手動でワークフロータブへ切り替えられる）。
  validateSearch: (search: Record<string, unknown>): GeoReportStudioSearch => ({
    tab: typeof search.tab === "string" ? search.tab : undefined,
    runId: typeof search.runId === "string" ? search.runId : undefined,
  }),
  component: GeoReportProjectDetailPage,
});

function GeoReportProjectDetailPage() {
  const { projectId } = Route.useParams();
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const { t } = useLanguage();

  const projectQuery = useGeoReportProject(projectId);
  const runsQuery = useGeoReportRuns(projectId);

  // 2026-09-17修正、ユーザー報告「古い画面（+ New Analysisの初期状態）がまだ出てくる」
  // 「新旧画面が行ったり来たりする」:
  // 以前の実装は「URLに?runIdが無ければ常に+ New Analysisのまま」にしていたが、
  // これはユーザーが毎回手動で自分の作業中Analysisを選び直さなければならない不便を
  // 生んでいた。一方、その前の実装（fetch完了後に自動で最新Runへ切り替える）は、
  // GeoReportPipelinePanelを先にrunId=""でマウントしてから後で正しい値へ更新する
  // ため、「+ New Analysisの編集可能なDesign Chatが一瞬見えてから既存Analysisへ
  // 切り替わる」というフラッシュ（ユーザーには"新旧画面が行ったり来たりする"と
  // 見える）を引き起こしていた。
  // 対策: 初期runIdが確定する（1. URLに明示的な?runIdがある→即確定、または
  // 2. runsQueryのfetchが完了した→Pin済みRunの中で最新のものをデフォルト選択、
  // 無ければ""で確定）まで GeoReportPipelinePanel 自体をマウントしない。
  // これにより「間違った状態で一度描画してから正しい状態へ切り替える」フラッシュが
  // 構造的に発生しなくなる。
  const [selectedRunId, setSelectedRunId] = useState<string | null>(
    search.runId ? search.runId : null, // null = 「まだ確定していない」（初回のみ）
  );
  useEffect(() => {
    if (selectedRunId !== null) return; // 既に確定済み（URL指定、または後述の自動選択で1度だけ確定する）
    if (!runsQuery.isSuccess) return; // 一覧取得が完了するまで確定を待つ（フラッシュを避けるため）
    const latestPinnedRun = runsQuery.data.runs.find(isPinnedRun);
    setSelectedRunId(latestPinnedRun?.run_id ?? "");
  }, [runsQuery.isSuccess, runsQuery.data, selectedRunId]);

  // 2026-09-16修正、ユーザー報告「処理が終わったら初期画面に戻ってしまう」:
  // 従来はonSelectRunがこのuseStateだけを更新し、URLの?runIdへは一切反映していな
  // かった。ネットワーク不調時にTanStack RouterのCatchBoundary（__root__）が
  // ツリー全体を再構築すると、このコンポーネント自体も再マウントされ、
  // selectedRunIdの初期値は再びURLのsearch.runIdから読み直される——URLが追随して
  // いなければ空文字列に戻り、Preprocess実行中だったAnalysisを見失って「新規
  // Analysis作成」画面（初期画面）に戻ってしまう。onSelectRunを呼ぶたびにURLへも
  // 同期し、再マウントされても現在のAnalysisを復元できるようにする。
  const handleSelectRun = (id: string) => {
    setSelectedRunId(id);
    navigate({ search: (prev) => ({ ...prev, runId: id || undefined }) });
  };

  const project = projectQuery.data?.project;
  const activeTab = search.tab && _VALID_TABS.has(search.tab) ? search.tab : "overview";

  return (
    <>
      <TopBar
        breadcrumb={[
          { label: t("nav.geoReportStudio"), href: "/geo/report-studio" },
          { label: project?.project_title ?? projectId },
        ]}
      />
      <main className="flex-1 max-w-[1200px] w-full mx-auto px-8 py-8 space-y-6">
        {project && (
          <section className="border-b border-border pb-5 flex items-center justify-between">
            <div>
              <h1 className="text-editorial text-3xl">{project.project_title}</h1>
              <p className="text-xs text-muted-foreground mt-1 font-mono">{project.project_code}</p>
            </div>
            {projectQuery.data && projectQuery.data.review_required_count > 0 && (
              <GeoStatusBadge severity="elevated">
                {t("geo.reportStudio.reviewRequired")} {projectQuery.data.review_required_count}
              </GeoStatusBadge>
            )}
          </section>
        )}

        <Tabs
          value={activeTab}
          onValueChange={(tab) => navigate({ search: (prev) => ({ ...prev, tab }) })}
          className="w-full"
        >
          <TabsList>
            <TabsTrigger value="overview">{t("geo.reportStudio.tab.overview")}</TabsTrigger>
            <TabsTrigger value="pipeline">{t("geo.reportStudio.tab.pipeline")}</TabsTrigger>
            <TabsTrigger value="research">{t("geo.sourceResearch.tab")}</TabsTrigger>
          </TabsList>

          <TabsContent value="overview" className="pt-4">
            <OverviewTab projectId={projectId} />
          </TabsContent>
          <TabsContent value="pipeline" className="pt-4">
            {selectedRunId === null ? (
              <p className="text-sm text-muted-foreground">{t("geo.reportStudio.loading")}</p>
            ) : (
              <GeoReportPipelinePanel
                projectId={projectId}
                runId={selectedRunId}
                onSelectRun={handleSelectRun}
              />
            )}
          </TabsContent>
          {/* Source調査: Theme Explorer / Source Library / Monitoringから引き継いだTheme・Query・Sourceで調査する */}
          <TabsContent value="research" className="pt-4">
            <SourceResearchPanel projectId={projectId} />
          </TabsContent>
        </Tabs>
      </main>
    </>
  );
}

// ---------------------------------------------------------------------------
// Overview: Current Assessment / Key Findings / Watchpoints / Analysis Status を
// 上位に、件数統計等のOperational DetailはCollapsibleへ落とす
// ---------------------------------------------------------------------------

function OverviewTab({ projectId }: { projectId: string }) {
  const overviewQuery = useGeoReportOverview(projectId);
  const { t } = useLanguage();

  if (overviewQuery.isError) {
    return (
      <GeoCard>
        <GeoCardBody className="pt-5">
          <p className="text-sm text-muted-foreground">
            {t("geo.reportStudio.overviewUnavailable")}
          </p>
        </GeoCardBody>
      </GeoCard>
    );
  }
  if (!overviewQuery.data) return null;

  const { latest_run, narrative_count, review_required_count, syntheses } = overviewQuery.data;
  const findings = syntheses.flatMap((s) =>
    s.final_draft.findings.map((f) => ({ synthesis: s, finding: f })),
  );
  const deltaItems = syntheses.flatMap((s) =>
    (s.final_draft.assessment_delta ?? []).map((d) => ({ synthesis: s, delta: d })),
  );
  const watchpointUpdates = syntheses.flatMap((s) =>
    (s.final_draft.watchpoint_updates ?? []).map((w) => ({ synthesis: s, update: w })),
  );
  const staticWatchpoints = syntheses.flatMap((s) =>
    s.final_draft.watchpoints.map((w) => ({ synthesis: s, watchpoint: w })),
  );
  const watchpointStatusCounts = watchpointUpdates.reduce<Record<string, number>>(
    (acc, { update }) => {
      acc[update.status] = (acc[update.status] ?? 0) + 1;
      return acc;
    },
    {},
  );

  return (
    <div className="space-y-6">
      <GeoCard>
        <GeoCardHeader title={t("geo.reportStudio.currentAssessment")} />
        <GeoCardBody className="space-y-3">
          {syntheses.length === 0 && (
            <p className="text-sm text-muted-foreground">{t("geo.reportStudio.noSynthesisYet")}</p>
          )}
          {syntheses.map((s) => (
            <div key={s.id} className="border-t border-border pt-2 first:border-t-0 first:pt-0">
              <div className="text-xs text-muted-foreground mb-1">{s.narrative_id}</div>
              <p className="text-sm">{s.final_draft.central_assessment.summary}</p>
            </div>
          ))}
        </GeoCardBody>
      </GeoCard>

      {deltaItems.length > 0 && (
        <GeoCard>
          <GeoCardHeader
            title={t("geo.reportStudio.whatChanged")}
            meta={`${deltaItems.length} meaningful ${deltaItems.length === 1 ? "change" : "changes"}`}
          />
          <GeoCardBody className="space-y-2">
            {deltaItems.map(({ delta }, i) => (
              <div
                key={i}
                className="text-sm border-t border-border pt-2 first:border-t-0 first:pt-0 flex items-start gap-2"
              >
                <ChangeTypeBadge changeType={delta.change_type} />
                <span>{delta.item}</span>
              </div>
            ))}
          </GeoCardBody>
        </GeoCard>
      )}

      <div className="grid gap-6 md:grid-cols-2">
        <GeoCard>
          <GeoCardHeader title={t("geo.reportStudio.keyFindings")} />
          <GeoCardBody className="space-y-2">
            {findings.length === 0 && <p className="text-sm text-muted-foreground">—</p>}
            {findings.slice(0, 8).map(({ finding }, i) => (
              <div
                key={i}
                className="text-sm border-t border-border pt-2 first:border-t-0 first:pt-0"
              >
                {finding.statement}
              </div>
            ))}
          </GeoCardBody>
        </GeoCard>

        <GeoCard>
          <GeoCardHeader
            title={t("geo.reportStudio.watchpoints")}
            meta={
              watchpointUpdates.length > 0
                ? Object.entries(watchpointStatusCounts)
                    .map(([k, v]) => `${v} ${k}`)
                    .join(" / ")
                : undefined
            }
          />
          <GeoCardBody className="space-y-2">
            {watchpointUpdates.length === 0 && staticWatchpoints.length === 0 && (
              <p className="text-sm text-muted-foreground">—</p>
            )}
            {watchpointUpdates.length > 0
              ? watchpointUpdates.slice(0, 8).map(({ update }, i) => (
                  <div
                    key={i}
                    className="text-sm border-t border-border pt-2 first:border-t-0 first:pt-0 flex items-start gap-2"
                  >
                    <WatchpointStatusBadge status={update.status} />
                    <span>{update.watchpoint}</span>
                  </div>
                ))
              : staticWatchpoints.slice(0, 8).map(({ watchpoint }, i) => (
                  <div
                    key={i}
                    className="text-sm border-t border-border pt-2 first:border-t-0 first:pt-0"
                  >
                    {watchpoint.watchpoint}
                  </div>
                ))}
          </GeoCardBody>
        </GeoCard>
      </div>

      <GeoCard>
        <GeoCardHeader title={t("geo.reportStudio.analysisStatus")} />
        <GeoCardBody>
          <div className="flex flex-wrap gap-2">
            <Badge variant={latest_run?.status === "completed" ? "secondary" : "outline"}>
              {latest_run?.status ?? t("geo.reportStudio.noRunsYet")}
            </Badge>
            <Badge variant="outline">{narrative_count} narratives configured</Badge>
            <Badge variant="outline">{syntheses.length} synthesized</Badge>
            {review_required_count > 0 && (
              <GeoStatusBadge severity="elevated">
                {t("geo.reportStudio.reviewRequired")} {review_required_count}
              </GeoStatusBadge>
            )}
          </div>
        </GeoCardBody>
      </GeoCard>

      {latest_run && (
        <Collapsible>
          <CollapsibleTrigger className="text-xs text-muted-foreground underline underline-offset-2">
            {t("geo.reportStudio.operationalDetail")}
          </CollapsibleTrigger>
          <CollapsibleContent className="pt-3">
            <div className="text-xs text-muted-foreground space-y-1 card-paper rounded-lg p-4">
              <div>{latest_run.total_records} records processed</div>
              <div>{latest_run.nonduplicate_records} nonduplicate</div>
              <div>{latest_run.cluster_count} clusters classified</div>
              <div>
                {t("geo.reportStudio.period")}: {latest_run.period_start} – {latest_run.period_end}
              </div>
            </div>
          </CollapsibleContent>
        </Collapsible>
      )}
    </div>
  );
}
