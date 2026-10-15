import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { Loader2 } from "lucide-react";
import { TopBar } from "@/components/top-bar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  GeoIosWorkflowHeader,
  deriveWorkflowState,
  type GeoIosWorkflowStep,
} from "@/components/geo/geo-ios-workflow-header";
import { GeoConfidentialityLabelSelect } from "@/components/geo/geo-confidentiality-label-select";
import { useReviewSummary, useRequestReviewCycle } from "@/lib/geoIosReviewApi";
import { useIdentities } from "@/lib/geoIosIdentityApi";
import { useSessionWorkflowState } from "@/lib/geoIosAnalysisSessionApi";
import {
  useCurrentApproval,
  useApproveInApp,
  usePublishReportStudio,
  useCurrentPublishedKnowledge,
  useExtractLearning,
} from "@/lib/geoIosKnowledgePublicationApi";
import type { GeoConfidentialityLabel } from "@/lib/geoKnowledgeApi";
import { useLanguage } from "@/lib/i18n";

// Geopolitical Intelligence OS Phase H §11, §13-14, §19-21: Report Studio Workflow Header /
// Request Expert Review / Approval vs Publication。既存Report Studio本体（geo.report-studio.
// $projectId.tsx等）は変更せず、新規独立ページとして実装する（並行改修との衝突回避、§1/§12）。
// Snapshot処理はPhase D既存Adapterに委ね、UI側では独自のsnapshot取得を行わない（§13）。
//
// 見た目は社内モックアップのApproval画面（Release checklist gate + Outputs + Learning
// candidate）に合わせる。既存のデータ・mutation・承認/公開のロジックは一切変更しない
// （見た目のみのスコープ、他画面のスタイルには影響させない）。
export const Route = createFileRoute("/geo/ios/review/$reportProjectId")({
  component: GeoIosReviewPage,
});

function GateRow({
  done,
  label,
  detail,
  value,
}: {
  done: boolean;
  label: string;
  detail: string;
  value: string;
}) {
  return (
    <div className="grid grid-cols-[30px_1fr_auto] gap-3 items-center py-[14px] border-t border-[#edf3f5] first:border-t-0">
      <span
        className={`w-[30px] h-[30px] rounded-[9px] grid place-items-center font-black ${
          done ? "bg-[#e9f7f1] text-[#2f9e76]" : "bg-[#fff3e7] text-[#e9903d]"
        }`}
      >
        {done ? "✓" : "!"}
      </span>
      <div>
        <b className="block text-[#172d3d] text-lg">{label}</b>
        <div className="geo-meta">{detail}</div>
      </div>
      <span className="geo-meta">{value}</span>
    </div>
  );
}

function GeoIosReviewPage() {
  const { reportProjectId } = Route.useParams();
  const { t } = useLanguage();
  const navigate = useNavigate();
  const actorId =
    typeof window !== "undefined"
      ? (localStorage.getItem("geo_ios_actor_id") ?? "steward")
      : "steward";

  const [requestDialogOpen, setRequestDialogOpen] = useState(false);
  const [reviewer, setReviewer] = useState("");
  const [message, setMessage] = useState("");
  const [dueDate, setDueDate] = useState("");
  const reviewersQuery = useIdentities("expert_reviewer");
  const reviewers = reviewersQuery.data?.identities ?? [];

  const [publishDialogOpen, setPublishDialogOpen] = useState(false);
  // Phase 2E §9: 安全側のdefaultを優先する（fail-closed/narrow default）。ここは既に
  // Review Flowで承認済みのReportのPublishだが、それでも「分からないから広く共有する」を
  // 避け、DEPARTMENT_ONLYを既定にする——広い範囲へ共有したい場合はPublisherが明示的に選ぶ。
  const [confidentialityLabel, setConfidentialityLabel] = useState<GeoConfidentialityLabel | "">(
    "DEPARTMENT_ONLY",
  );
  const [approverRole, setApproverRole] = useState("knowledge_steward");

  const summaryQuery = useReviewSummary(reportProjectId);
  const requestReview = useRequestReviewCycle(reportProjectId);
  const currentApprovalQuery = useCurrentApproval(reportProjectId);
  const approveInApp = useApproveInApp(reportProjectId);
  const publishedKnowledgeQuery = useCurrentPublishedKnowledge(reportProjectId);
  const publish = usePublishReportStudio(reportProjectId);
  const extractLearning = useExtractLearning(reportProjectId);

  const summary = summaryQuery.data;
  const cycles = summary?.cycles ?? [];
  const latestCycle = summary?.latest_cycle ?? null;
  const currentApproval = currentApprovalQuery.data;
  const publishedKnowledge = publishedKnowledgeQuery.data;

  // 2026-09-24修正、ユーザー指摘「AIレビュー結果のページから戻れない」「ステップの
  // チェック状態がおかしい」への対応:
  // (1) 従来はreportGenerationMode/hasNativeDraft/hasAiReviewCompletedを渡していな
  //     かったため、deriveWorkflowStateが常にai_reviewを"done"扱いにしていた
  //     （native draft sessionで実際にはAI Reviewが未合格でもチェック✓が付く不具合）。
  //     セッション詳細ページ（geo.ios.analysis.$sessionId.tsx）と同じ
  //     useSessionWorkflowStateから実データを取り、同じ判定基準に揃える。
  // (2) このページのGeoIosWorkflowHeaderはonStepClickが無く、全ステップが非
  //     インタラクティブなまま（前のステップへ戻る手段が無かった）。前提整理/読み方
  //     提案/ドラフト/AIレビューは全てセッション詳細ページ側の画面なので、そちらへ
  //     navigateする。
  const sessionWorkflowQuery = useSessionWorkflowState(summary?.analysis_session_id ?? "");
  const wc = sessionWorkflowQuery.data;
  const workflowState = deriveWorkflowState({
    hasDesign: true,
    hasReportProject: true,
    reportGenerationMode: wc?.report_generation_mode,
    hasNativeDraft: wc?.has_native_draft,
    hasAiReviewCompleted: wc?.has_ai_review_completed,
    hasOpenReviewCycle: !!latestCycle && latestCycle.status !== "completed",
    latestReviewResult: latestCycle?.result ?? null,
    approvalStatus: currentApproval?.approval_status ?? null,
    publicationStatus: currentApproval?.publication_status ?? null,
  });

  const handleWorkflowStepClick = (step: GeoIosWorkflowStep) => {
    if (step === "expert_review" || step === "approval") return; // 既にこのページ
    if (!summary?.analysis_session_id) return; // Report Studio直作成等、紐づくSessionが無い
    navigate({
      to: "/geo/ios/analysis/$sessionId",
      params: { sessionId: summary.analysis_session_id },
    });
  };

  const canApprove =
    !!latestCycle &&
    latestCycle.result === "approved" &&
    currentApproval?.approval_status !== "approved";
  const canPublish =
    currentApproval?.approval_status === "approved" &&
    currentApproval.publication_status !== "published";

  return (
    <>
      <TopBar
        breadcrumb={[
          { label: t("nav.geoReviewFlow"), href: "/geo/ios/review-intake" },
          { label: reportProjectId.slice(0, 8) },
        ]}
      />
      <main className="flex-1 max-w-[1000px] w-full mx-auto px-8 py-8 space-y-6">
        <section className="border-b border-border pb-5 space-y-3">
          <div className="flex items-center justify-between">
            <h1 className="geo-heading text-3xl">
              {summary?.report_project?.project_title ?? reportProjectId}
            </h1>
            {/* 2026-09-24変更、ユーザー指摘「Report Studioで開くって何？いらないよね」:
                IOS Native Draft経路（report_generation_mode==="native"）のReport
                Projectには、Report Studio側に実質的な中身が無い（Design Chat/
                Preprocess等はReport Studio独自のフロー用で、Native Draftはそれらを
                経由しない）。report_studio経路のReport Projectでは引き続き必要な
                導線なので、native以外の場合のみ表示する。 */}
            {summary?.report_generation_mode !== "native" && (
              <Link to="/geo/report-studio/$projectId" params={{ projectId: reportProjectId }}>
                <Button variant="outline" size="sm">
                  {t("geo.ios.review.openInReportStudio")}
                </Button>
              </Link>
            )}
          </div>
          <GeoIosWorkflowHeader state={workflowState} onStepClick={handleWorkflowStepClick} />
        </section>

        {/* §13 Request Expert Review */}
        <section className="flex items-center justify-between">
          <h2 className="text-xl font-semibold text-[#172d3d]">
            {t("geo.ios.review.expertReview")}
          </h2>
          <Dialog open={requestDialogOpen} onOpenChange={setRequestDialogOpen}>
            <DialogTrigger asChild>
              <Button size="sm" className="bg-[#0d879a] hover:bg-[#096f80]">
                {t("geo.ios.review.requestExpertReview")}
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>{t("geo.ios.review.requestExpertReview")}</DialogTitle>
              </DialogHeader>
              <div className="space-y-3">
                <Select value={reviewer} onValueChange={setReviewer}>
                  <SelectTrigger>
                    <SelectValue placeholder={t("geo.ios.review.reviewerPlaceholder")} />
                  </SelectTrigger>
                  <SelectContent>
                    {reviewers.map((r) => (
                      <SelectItem key={r.identity_id} value={r.identity_id}>
                        {r.identity_id}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {reviewers.length === 0 && (
                  <p className="text-sm text-muted-foreground">
                    {t("geo.ios.review.noReviewersConfigured")}
                  </p>
                )}
                <Textarea
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  placeholder={t("geo.ios.review.messagePlaceholder")}
                  className="text-base md:text-base"
                />
                <Input
                  type="date"
                  value={dueDate}
                  onChange={(e) => setDueDate(e.target.value)}
                  className="text-base md:text-base"
                />
                <p className="geo-meta">{t("geo.ios.review.snapshotNote")}</p>
              </div>
              <DialogFooter>
                <Button
                  className="bg-[#0d879a] hover:bg-[#096f80]"
                  disabled={!reviewer.trim() || requestReview.isPending}
                  onClick={() =>
                    requestReview.mutate(
                      {
                        reviewer,
                        requested_by: actorId,
                        message: message || undefined,
                        due_date: dueDate || undefined,
                        analysis_session_id: summary?.analysis_session_id,
                      },
                      {
                        onSuccess: () => {
                          setRequestDialogOpen(false);
                          setReviewer("");
                          setMessage("");
                          setDueDate("");
                        },
                      },
                    )
                  }
                >
                  {requestReview.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                  {t("geo.ios.review.send")}
                </Button>
              </DialogFooter>
              {requestReview.isError && (
                <div className="space-y-1">
                  <p className="text-sm text-destructive">
                    {(requestReview.error as Error).message}
                  </p>
                  {/* 2026-09-24追加、ユーザー指摘「このダイアログのどこにWaiveがあるのか
                      分からない」への対応: エラー文だけでは「AI Reviewを実行/Waiveする」の
                      実際の操作場所（Draftタブ）が分からず迷わせていた。導線を直接添える。 */}
                  {summary?.analysis_session_id && (
                    <Link
                      to="/geo/ios/analysis/$sessionId"
                      params={{ sessionId: summary.analysis_session_id }}
                      className="text-sm text-[#0d879a] underline"
                    >
                      {t("geo.ios.review.openDraftForAiReview")}
                    </Link>
                  )}
                </div>
              )}
            </DialogContent>
          </Dialog>
        </section>

        {/* §14, §18 Multiple Review Rounds */}
        <div className="space-y-2">
          {cycles.map((cycle) => (
            <Link
              key={cycle.review_cycle_id}
              to="/geo/ios/review-cycle/$reviewCycleId"
              params={{ reviewCycleId: cycle.review_cycle_id }}
            >
              <div
                className="grid grid-cols-[1fr_auto_auto] gap-3 items-center px-1 py-[14px] border-t border-[#edf3f5] first:border-t-0
                              hover:bg-[#fbfcfd] transition-colors rounded-lg"
              >
                <div>
                  <b className="text-[#172d3d] text-base">Round {cycle.round_number}</b>
                  <span className="geo-meta ml-2">{cycle.reviewer}</span>
                </div>
                <span className="geo-meta">
                  {new Date(cycle.requested_at).toLocaleDateString()}
                </span>
                <span className="inline-flex items-center justify-center min-h-[22px] px-[9px] rounded-full text-xs font-extrabold text-[#0d879a] bg-[#eaf8fb]">
                  {cycle.result ?? cycle.status}
                </span>
              </div>
            </Link>
          ))}
          {cycles.length === 0 && (
            <p className="text-base text-muted-foreground">{t("geo.ios.review.noRoundsYet")}</p>
          )}
        </div>

        {/* §19-20 Approval vs Publication（別ボタン、明確に分離） */}
        <section className="border-t border-border pt-5 space-y-4">
          <h2 className="text-xl font-semibold text-[#172d3d]">
            {t("geo.ios.review.approvalAndPublication")}
          </h2>

          <div className="rounded-[14px] border border-[#d8e2e7] bg-white shadow-[0_14px_34px_rgba(16,43,63,.08)] px-5">
            <GateRow
              done={currentApproval?.approval_status === "approved"}
              label={t("geo.ios.review.approvalAndPublication")}
              detail={
                currentApproval
                  ? `Approved by ${currentApproval.approver}`
                  : t("geo.ios.review.notYetApproved")
              }
              value=""
            />
            {currentApproval?.approval_status === "approved" && (
              <GateRow
                done={currentApproval.publication_status === "published"}
                label={t("geo.ios.review.publishToLibrary")}
                detail={currentApproval.publication_status ?? ""}
                value=""
              />
            )}
          </div>

          <div className="flex items-center gap-3">
            {canApprove && (
              <Button
                size="sm"
                className="bg-[#0d879a] hover:bg-[#096f80]"
                disabled={approveInApp.isPending}
                onClick={() =>
                  approveInApp.mutate({ approval_status: "approved", approver: actorId })
                }
              >
                {t("geo.ios.review.approve")}
              </Button>
            )}
            {canPublish && (
              <Dialog open={publishDialogOpen} onOpenChange={setPublishDialogOpen}>
                <DialogTrigger asChild>
                  <Button size="sm" className="bg-[#0d879a] hover:bg-[#096f80]">
                    {t("geo.ios.review.publishToLibrary")}
                  </Button>
                </DialogTrigger>
                <DialogContent>
                  <DialogHeader>
                    <DialogTitle>{t("geo.ios.review.publishToLibrary")}</DialogTitle>
                  </DialogHeader>
                  <div className="space-y-3">
                    <GeoConfidentialityLabelSelect
                      value={confidentialityLabel}
                      onChange={setConfidentialityLabel}
                    />
                    <Input
                      value={approverRole}
                      onChange={(e) => setApproverRole(e.target.value)}
                      placeholder="publisher role (knowledge_steward)"
                    />
                  </div>
                  <DialogFooter>
                    <Button
                      className="bg-[#0d879a] hover:bg-[#096f80]"
                      disabled={publish.isPending || !confidentialityLabel}
                      onClick={() =>
                        confidentialityLabel &&
                        publish.mutate(
                          {
                            approval_event_id: currentApproval.approval_event_id,
                            publisher: actorId,
                            publisher_role: approverRole,
                            confidentiality_label: confidentialityLabel,
                          },
                          { onSuccess: () => setPublishDialogOpen(false) },
                        )
                      }
                    >
                      {publish.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                      {t("geo.ios.review.publish")}
                    </Button>
                  </DialogFooter>
                  {publish.isError && (
                    <p className="text-xs text-destructive">{(publish.error as Error).message}</p>
                  )}
                </DialogContent>
              </Dialog>
            )}
          </div>

          {publishedKnowledge && (
            <div className="rounded-[11px] border border-[#d8e2e7] bg-[#edf3f5] p-[16px]">
              <p className="text-base geo-prose mb-2">{t("geo.ios.review.publishedAppears")}</p>
              <Button
                size="sm"
                variant="outline"
                disabled={extractLearning.isPending}
                onClick={() => extractLearning.mutate({ actor_id: actorId })}
              >
                {extractLearning.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                {t("geo.ios.review.extractLearning")}
              </Button>
              {extractLearning.data && (
                <p className="text-xs text-muted-foreground mt-2">
                  {extractLearning.data.idempotent_reuse
                    ? t("geo.ios.review.extractLearningAlreadyRun")
                    : `${extractLearning.data.candidates.length} candidate(s) created`}
                  {" — "}
                  <Link to="/geo/ios/learning-candidates" className="underline">
                    {t("geo.ios.review.openLearningInbox")}
                  </Link>
                </p>
              )}
              {extractLearning.isError && (
                <p className="text-xs text-destructive">
                  {(extractLearning.error as Error).message}
                </p>
              )}
            </div>
          )}
        </section>
      </main>
    </>
  );
}
