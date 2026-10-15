import { createFileRoute, Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { Loader2 } from "lucide-react";
import { TopBar } from "@/components/top-bar";
import { GeoCard, GeoCardBody } from "@/components/geo/geo-card";
import { GeoStatusBadge } from "@/components/geo/geo-badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  deriveWorkflowState,
  type GeoIosStepState,
  type GeoIosWorkflowStep,
} from "@/components/geo/geo-ios-workflow-header";
import {
  useWorkflowBoard,
  useWorkflowBoardHome,
  type GeoIosBoardCase,
} from "@/lib/geoIosWorkflowBoardApi";
import { useIdentities } from "@/lib/geoIosIdentityApi";
import { useLanguage } from "@/lib/i18n";

// Geopolitical Intelligence OS: Workflow Kanban Board。
// 既存geo_ios_workflow_header.deriveWorkflowStateをそのまま使い、UI独自のStatusは
// 一切持たない（既存絶対条件の継承）。各案件のカードは、5ステップ中「current」に
// 該当する列へ配置する（全ステップdone済みならPublished列）。
export const Route = createFileRoute("/geo/ios/board")({
  component: GeoIosBoardPage,
});

const STEP_ORDER: GeoIosWorkflowStep[] = [
  "design",
  "draft",
  "ai_review",
  "expert_review",
  "approval",
];
const STEP_LABELS: Record<GeoIosWorkflowStep, string> = {
  intake: "前提整理",
  design: "読み方提案",
  draft: "ドラフト",
  ai_review: "AIレビュー",
  expert_review: "専門家レビュー",
  approval: "承認・ストック",
};

function currentStepOf(state: Record<GeoIosWorkflowStep, GeoIosStepState>): GeoIosWorkflowStep {
  const found = STEP_ORDER.find((step) => state[step] !== "done");
  return found ?? "approval";
}

function boardStateFor(c: GeoIosBoardCase) {
  return deriveWorkflowState({
    hasDesign: c.has_design,
    hasReportProject: c.has_report_project,
    reportGenerationMode: c.report_generation_mode,
    hasNativeDraft: c.has_native_draft,
    hasAiReviewCompleted: c.has_ai_review_completed,
    latestReviewResult: c.latest_review_result,
    hasOpenReviewCycle: c.has_open_review_cycle,
    approvalStatus: c.approval_status,
    publicationStatus: c.publication_status,
  });
}

function MetricTile({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: "orange" | "violet" | "green";
}) {
  const tones: Record<string, string> = {
    default: "bg-white border border-[#d8e2e7]",
    orange: "bg-[#fff3e7] border-transparent",
    violet: "bg-[#f0efff] border-transparent",
    green: "bg-[#e9f7f1] border-transparent",
  };
  return (
    <div className={`rounded-[13px] px-[18px] py-4 ${tones[tone ?? "default"]}`}>
      <span className="text-xs text-muted-foreground">{label}</span>
      <strong className="block text-[27px] mt-1 text-[#172d3d]">{value}</strong>
    </div>
  );
}

function GeoIosBoardPage() {
  const { t } = useLanguage();
  const boardQuery = useWorkflowBoard();
  const homeQuery = useWorkflowBoardHome();
  const reviewersQuery = useIdentities("expert_reviewer");
  const [reviewerFilter, setReviewerFilter] = useState<string>("__all__");
  const home = homeQuery.data;

  const cases = boardQuery.data?.cases ?? [];
  const reviewers = reviewersQuery.data?.identities ?? [];

  const filtered = useMemo(() => {
    if (reviewerFilter === "__all__") return cases;
    return cases.filter((c) => c.latest_review_reviewer === reviewerFilter);
  }, [cases, reviewerFilter]);

  const columns = useMemo(() => {
    // intake（前提整理）はderiveWorkflowStateが常にdoneを返す（§geo-ios-workflow-header.tsx
    // 参照）ため、Board自身のSTEP_ORDERには含めない——currentStepOfがintakeを選ぶことはないが、
    // Record<GeoIosWorkflowStep, ...>型を満たすためだけに空配列を持たせる。
    const grouped: Record<GeoIosWorkflowStep, GeoIosBoardCase[]> = {
      intake: [],
      design: [],
      draft: [],
      ai_review: [],
      expert_review: [],
      approval: [],
    };
    for (const c of filtered) {
      grouped[currentStepOf(boardStateFor(c))].push(c);
    }
    return grouped;
  }, [filtered]);

  return (
    <>
      <TopBar
        breadcrumb={[
          { label: t("nav.geoExpertAI"), href: "/geo/ios/analysis" },
          { label: t("geo.ios.board.title") },
        ]}
      />
      <main className="flex-1 w-full px-8 py-8 space-y-5">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h1 className="geo-heading text-2xl">{t("geo.ios.board.title")}</h1>
            <p className="text-sm text-muted-foreground">{t("geo.ios.board.subtitle")}</p>
          </div>
          <div className="w-56">
            <Select value={reviewerFilter} onValueChange={setReviewerFilter}>
              <SelectTrigger>
                <SelectValue placeholder={t("geo.ios.board.filterByReviewer")} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">{t("geo.ios.board.allCases")}</SelectItem>
                {reviewers.map((r) => (
                  <SelectItem key={r.identity_id} value={r.identity_id}>
                    {r.identity_id}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        {home && (
          <div className="grid gap-4 lg:grid-cols-[1fr_320px] items-start">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3.5">
              <MetricTile
                label={t("geo.ios.board.metricInProgress")}
                value={home.metrics.in_progress}
              />
              <MetricTile
                label={t("geo.ios.board.metricNeedsAttention")}
                value={home.metrics.needs_attention}
                tone="orange"
              />
              <MetricTile
                label={t("geo.ios.board.metricInExpertReview")}
                value={home.metrics.in_expert_review}
                tone="violet"
              />
              <MetricTile
                label={t("geo.ios.board.metricApproved")}
                value={home.metrics.approved}
                tone="green"
              />
            </div>

            {home.resume ? (
              <div className="rounded-[14px] bg-[#102b3f] text-white px-5 py-4">
                <h3 className="m-0 text-sm font-bold">{t("geo.ios.board.resumeTitle")}</h3>
                <p className="m-0 mt-1.5 mb-3 text-[13px] text-[#c3d3dd] line-clamp-2">
                  {home.resume.title}
                </p>
                <Link
                  to={
                    home.resume.report_project_id
                      ? "/geo/ios/review/$reportProjectId"
                      : "/geo/ios/analysis/$sessionId"
                  }
                  params={
                    (home.resume.report_project_id
                      ? { reportProjectId: home.resume.report_project_id }
                      : { sessionId: home.resume.analysis_session_id }) as never
                  }
                >
                  <button className="rounded-[10px] px-4 py-2 text-sm font-bold bg-[#0d879a] text-white hover:bg-[#096f80]">
                    {t("geo.ios.board.resumeButton")}
                  </button>
                </Link>
              </div>
            ) : home.tasks.length > 0 ? (
              <div className="rounded-[14px] border border-[#d8e2e7] bg-white px-5 py-4 space-y-2">
                <h3 className="m-0 text-sm font-bold text-[#172d3d]">
                  {t("geo.ios.board.tasksTitle")}
                </h3>
                {home.tasks.map((task) => (
                  <Link
                    key={task.analysis_session_id}
                    to="/geo/ios/analysis/$sessionId"
                    params={{ sessionId: task.analysis_session_id }}
                    className="block text-xs"
                  >
                    <span className="text-[#172d3d] font-medium line-clamp-1">{task.title}</span>
                    <span className="text-muted-foreground">
                      {" — "}
                      {task.reason === "revision_requested"
                        ? t("geo.ios.board.taskRevisionRequested")
                        : t("geo.ios.board.taskApprovalPending")}
                    </span>
                  </Link>
                ))}
              </div>
            ) : null}
          </div>
        )}

        {boardQuery.isLoading && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> {t("geo.ios.board.loading")}
          </div>
        )}

        {!boardQuery.isLoading && (
          <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
            {STEP_ORDER.map((step) => (
              <div key={step} className="space-y-2 min-w-0">
                <div className="flex items-center justify-between px-1">
                  <h2 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    {STEP_LABELS[step]}
                  </h2>
                  <span className="text-xs text-muted-foreground">{columns[step].length}</span>
                </div>
                <div className="space-y-2">
                  {columns[step].map((c) => (
                    <BoardCard key={c.analysis_session_id} case={c} />
                  ))}
                  {columns[step].length === 0 && (
                    <div className="rounded-md border border-dashed border-border/60 px-3 py-6 text-center text-xs text-muted-foreground">
                      —
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </main>
    </>
  );
}

function BoardCard({ case: c }: { case: GeoIosBoardCase }) {
  const { t } = useLanguage();
  const needsRevision = c.latest_review_result === "revision_requested" && !c.has_open_review_cycle;
  const to = c.report_project_id
    ? "/geo/ios/review/$reportProjectId"
    : "/geo/ios/analysis/$sessionId";
  const params = c.report_project_id
    ? { reportProjectId: c.report_project_id }
    : { sessionId: c.analysis_session_id };

  return (
    <Link to={to} params={params as never} className="block">
      <GeoCard className="hover:border-primary/40 transition-colors">
        <GeoCardBody className="space-y-1.5 py-3">
          <p className="text-sm font-medium leading-snug line-clamp-2">{c.title}</p>
          <p className="text-xs text-muted-foreground">{c.user_id}</p>
          <div className="flex items-center gap-1.5 flex-wrap pt-1">
            {needsRevision && (
              <GeoStatusBadge severity="elevated">
                {t("geo.ios.board.needsRevision")}
              </GeoStatusBadge>
            )}
            {c.latest_review_reviewer && (
              <GeoStatusBadge severity="neutral">{c.latest_review_reviewer}</GeoStatusBadge>
            )}
          </div>
        </GeoCardBody>
      </GeoCard>
    </Link>
  );
}
