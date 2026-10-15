import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Loader2 } from "lucide-react";
import { TopBar } from "@/components/top-bar";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useReviewCycle,
  useComments,
  useAddComment,
  useRespondToComment,
  useSubmitReviewFindings,
  useStartReview,
} from "@/lib/geoIosReviewApi";
import { useLanguage } from "@/lib/i18n";

// Geopolitical Intelligence OS Phase H §15-18: Reviewer専用画面（Report Studio全機能は見せない）
// + Author Review Response。§16のAnchor構造（section/paragraph/claim/evidence）を利用。
// Report本文はPhase D Adapterが固定したversion_snapshotをそのまま表示するだけで、UI側で
// 独自snapshot処理はしない（§37）。
//
// 見た目は社内モックアップ（地政学レポート生成・レビューフロー_UIモックアップ）のReviewer
// 画面（reviewer-banner + document + アンカーコメントパネル）に合わせる。既存のデータ・
// mutation・result種別（approved/revision_requested/rejected）は一切変更しない
// （見た目のみのスコープ、他画面のスタイルには影響させない）。
export const Route = createFileRoute("/geo/ios/review-cycle/$reviewCycleId")({
  component: GeoIosReviewCyclePage,
});

const ANCHOR_TYPES = ["section", "paragraph", "claim", "evidence", "general"] as const;
const RESOLUTION_OPTIONS = [
  "resolved",
  "partially_resolved",
  "rejected",
  "not_applicable",
] as const;

const RESULT_STYLE: Record<string, string> = {
  approved: "bg-[#0d879a] text-white border-transparent hover:bg-[#096f80]",
  revision_requested: "bg-white text-[#172d3d] border-[#d8e2e7] hover:border-[#a8bdc8]",
  rejected: "bg-white text-[#d85f62] border-[#efbfc1] hover:bg-[#fdeced]",
};

function Chip({
  tone,
  children,
}: {
  tone: "orange" | "violet" | "green" | "red" | "cyan";
  children: React.ReactNode;
}) {
  const tones: Record<string, string> = {
    orange: "text-[#e9903d] bg-[#fff3e7]",
    violet: "text-[#6d68e8] bg-[#f0efff]",
    green: "text-[#2f9e76] bg-[#e9f7f1]",
    red: "text-[#d85f62] bg-[#fdeced]",
    cyan: "text-[#0d879a] bg-[#eaf8fb]",
  };
  return (
    <span
      className={`inline-flex items-center justify-center min-h-[22px] px-[9px] rounded-full text-[11px] font-extrabold ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

function GeoIosReviewCyclePage() {
  const { reviewCycleId } = Route.useParams();
  const { t } = useLanguage();
  const actorId =
    typeof window !== "undefined"
      ? (localStorage.getItem("geo_ios_actor_id") ?? "reviewer")
      : "reviewer";

  const [commentText, setCommentText] = useState("");
  const [anchorType, setAnchorType] = useState<string>("general");
  const [severity, setSeverity] = useState<string>("minor");
  const [responses, setResponses] = useState<Record<string, string>>({});

  const cycleQuery = useReviewCycle(reviewCycleId);
  const commentsQuery = useComments(reviewCycleId);
  const addComment = useAddComment(reviewCycleId);
  const respond = useRespondToComment(reviewCycleId);
  const startReview = useStartReview(reviewCycleId);
  const submitFindings = useSubmitReviewFindings(reviewCycleId, cycleQuery.data?.report_project_id);

  const cycle = cycleQuery.data;
  const comments = commentsQuery.data?.comments ?? [];
  const syntheses = cycle?.version_snapshot?.syntheses ?? [];

  if (!cycle) {
    return (
      <>
        <TopBar breadcrumb={[{ label: t("nav.geoReviewFlow"), href: "/geo/ios/review-intake" }]} />
        <main className="flex-1 max-w-[1200px] w-full mx-auto px-8 py-8">
          <p className="text-sm text-muted-foreground">
            {cycleQuery.isLoading ? "..." : "Review cycle not found"}
          </p>
        </main>
      </>
    );
  }

  return (
    <>
      <TopBar
        breadcrumb={[
          { label: t("nav.geoReviewFlow"), href: "/geo/ios/review-intake" },
          { label: `Round ${cycle.round_number}` },
        ]}
      />
      <main className="flex-1 max-w-[1300px] w-full mx-auto px-8 py-8 space-y-5">
        <div className="rounded-[14px] bg-[#102b3f] text-white px-[18px] py-[14px] flex items-center justify-between gap-3">
          <div>
            <b className="block">
              Round {cycle.round_number} — {cycle.reviewer}
            </b>
            <p className="m-0 mt-[3px] text-[13px] text-[#c7d7e0]">
              {cycle.reviewed_version_label}
            </p>
          </div>
          <Chip tone="orange">{cycle.result ?? cycle.status}</Chip>
        </div>

        <div className="grid gap-6 lg:grid-cols-[1fr_420px]">
          {/* Report読み取り */}
          <div className="space-y-4">
            {syntheses.length === 0 && (
              <p className="text-sm text-muted-foreground">No report content in this snapshot.</p>
            )}
            {syntheses.map((s, i) => (
              <article
                key={i}
                className="bg-white border border-[#d8e2e7] rounded-[14px] p-[25px] shadow-[0_14px_34px_rgba(16,43,63,.08)]
                           prose prose-sm dark:prose-invert max-w-none [&_p]:leading-[1.9]"
                dangerouslySetInnerHTML={{ __html: s.final_html ?? "" }}
              />
            ))}
          </div>

          {/* §16 Anchored Comments + §17 Author Response */}
          <div className="space-y-4 h-fit">
            <div className="bg-white border border-[#d8e2e7] rounded-[14px] shadow-[0_14px_34px_rgba(16,43,63,.08)]">
              <div className="px-5 pt-[18px] pb-3">
                <h3 className="m-0 text-[17px] font-bold text-[#172d3d]">
                  {t("geo.ios.reviewCycle.comments")}
                </h3>
              </div>
              <div className="px-5 pb-5 space-y-3">
                {comments.map((c) => {
                  const open = c.resolved_status === "open";
                  return (
                    <div
                      key={c.review_comment_id}
                      className={`rounded-[12px] border p-[13px] space-y-2 ${
                        open ? "border-2 border-[#e9903d] bg-[#fff3e7]" : "border-[#d8e2e7]"
                      }`}
                    >
                      <div className="flex items-center gap-1.5 flex-wrap">
                        {c.anchor_type && <Chip tone="cyan">{c.anchor_type}</Chip>}
                        {c.severity && (
                          <Chip tone={c.severity === "critical" ? "red" : "orange"}>
                            {c.severity}
                          </Chip>
                        )}
                        <Chip tone={c.resolved_status === "resolved" ? "green" : "violet"}>
                          {c.resolved_status}
                        </Chip>
                      </div>
                      <p className="m-0 text-[13px] text-[#172d3d] leading-relaxed">{c.comment}</p>
                      {c.author_response && (
                        <p className="m-0 text-[13px] text-[#687b88] border-l-2 border-[#d8e2e7] pl-2 leading-relaxed">
                          {t("geo.ios.reviewCycle.authorResponse")}: {c.author_response}
                        </p>
                      )}
                      {open && (
                        <div className="flex gap-1.5 flex-wrap pt-1">
                          <Textarea
                            value={responses[c.review_comment_id] ?? ""}
                            onChange={(e) =>
                              setResponses((r) => ({ ...r, [c.review_comment_id]: e.target.value }))
                            }
                            placeholder={t("geo.ios.reviewCycle.authorResponsePlaceholder")}
                            className="text-xs min-h-[32px] bg-white"
                          />
                          <div className="flex gap-1 flex-wrap">
                            {RESOLUTION_OPTIONS.map((opt) => (
                              <Button
                                key={opt}
                                size="sm"
                                variant="outline"
                                disabled={respond.isPending}
                                className="bg-white"
                                onClick={() =>
                                  respond.mutate({
                                    reviewCommentId: c.review_comment_id,
                                    resolution_status: opt,
                                    author_response: responses[c.review_comment_id],
                                    actor_id: actorId,
                                  })
                                }
                              >
                                {opt}
                              </Button>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
                {comments.length === 0 && <p className="text-xs text-muted-foreground">-</p>}
              </div>
            </div>

            {cycle.status !== "completed" && (
              <div className="bg-white border border-[#d8e2e7] rounded-[14px] shadow-[0_14px_34px_rgba(16,43,63,.08)] px-5 py-[18px] space-y-3">
                <h3 className="m-0 text-[15px] font-bold text-[#172d3d]">
                  {t("geo.ios.reviewCycle.addComment")}
                </h3>
                <Textarea
                  value={commentText}
                  onChange={(e) => setCommentText(e.target.value)}
                  placeholder={t("geo.ios.reviewCycle.addCommentPlaceholder")}
                  className="text-sm min-h-[100px]"
                />
                <div className="flex gap-2 flex-wrap">
                  <Select value={anchorType} onValueChange={setAnchorType}>
                    <SelectTrigger className="w-32">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {ANCHOR_TYPES.map((a) => (
                        <SelectItem key={a} value={a}>
                          {a}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Select value={severity} onValueChange={setSeverity}>
                    <SelectTrigger className="w-28">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {["critical", "major", "minor"].map((s) => (
                        <SelectItem key={s} value={s}>
                          {s}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button
                    size="sm"
                    className="flex-1 bg-[#0d879a] hover:bg-[#096f80]"
                    disabled={!commentText.trim() || addComment.isPending}
                    onClick={() => {
                      if (cycle.status === "requested") startReview.mutate({ actor_id: actorId });
                      addComment.mutate(
                        {
                          comment: commentText,
                          created_by: actorId,
                          anchor_type: anchorType,
                          severity,
                        },
                        { onSuccess: () => setCommentText("") },
                      );
                    }}
                  >
                    {t("geo.ios.reviewCycle.addComment")}
                  </Button>
                </div>
              </div>
            )}

            {cycle.status !== "completed" && (
              <div className="flex gap-2 flex-wrap">
                {["approved", "revision_requested", "rejected"].map((result) => (
                  <Button
                    key={result}
                    size="sm"
                    variant="outline"
                    className={RESULT_STYLE[result]}
                    disabled={submitFindings.isPending}
                    onClick={() =>
                      submitFindings.mutate({
                        findings: {
                          critical_findings: comments
                            .filter((c) => c.severity === "critical")
                            .map((c) => c.comment),
                        },
                        result,
                        reviewer: cycle.reviewer,
                      })
                    }
                  >
                    {submitFindings.isPending && (
                      <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                    )}
                    {t(`geo.ios.reviewCycle.result.${result}`)}
                  </Button>
                ))}
              </div>
            )}
          </div>
        </div>
      </main>
    </>
  );
}
