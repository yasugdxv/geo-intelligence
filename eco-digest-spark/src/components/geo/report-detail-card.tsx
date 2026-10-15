import { useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { ExpertReviewLoop } from "@/components/geo/expert-review-loop";
import {
  geoReportDownloadUrl,
  useDeleteReport,
  useReportDetail,
  useReportDiff,
  useReportVersions,
  useStartReportReview,
  type GeoReportReviewStatus,
  type GeoReportSummary,
} from "@/lib/geoKnowledgeApi";
import { useLanguage } from "@/lib/i18n";

// Phase 2D（レポート登録）: uploaded_only/in_review/approvedをバッジで区別する
// （geo_report_service.derive_report_review_status、既存Review/Approvalデータからの
// 導出のみで新しいstatus columnは使わない）。
const REVIEW_STATUS_STYLE: Record<GeoReportReviewStatus, string> = {
  uploaded_only: "bg-muted text-muted-foreground",
  in_review: "bg-amber-100 text-amber-800",
  approved: "bg-emerald-100 text-emerald-800",
};

// 地政学アプリClooseout Phase5: Report Detail相当の最小表示。一覧カードから展開する形で、
// Version履歴・Review開始（v2作成＋Delta Extractionまで一括実行）・Human Review
// （共通ExpertReviewLoop、Caseページと同じコンポーネント）まで一つの画面から辿れるようにする。
// 大規模な専用ページは作らない（既存Reportsタブのカードを展開するだけ）。
export function ReportDetailCard({ report }: { report: GeoReportSummary }) {
  const { t } = useLanguage();
  const [expanded, setExpanded] = useState(false);
  const [showFullText, setShowFullText] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [reviewerId, setReviewerId] = useState("analyst1");
  const [reviewSessionId, setReviewSessionId] = useState<string | null>(null);
  const [diffTarget, setDiffTarget] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const versionsQuery = useReportVersions(expanded ? report.report_id : "");
  const startReviewMutation = useStartReportReview(report.report_id);
  const diffQuery = useReportDiff(diffTarget ? report.report_id : "", diffTarget ?? "");
  const detailQuery = useReportDetail(expanded && showFullText ? report.report_id : "");
  const deleteMutation = useDeleteReport();

  return (
    <div className="card-paper rounded-lg p-4">
      <div className="flex items-center gap-2 flex-wrap">
        {report.review_status && (
          <span
            className={`px-2 py-0.5 rounded-full text-[11px] font-bold ${REVIEW_STATUS_STYLE[report.review_status]}`}
          >
            {t(`geo.reportRegistration.reviewStatus.${report.review_status}`)}
          </span>
        )}
        {report.confidentiality_label && (
          <Badge variant="secondary">{t(`geo.confidentiality.${report.confidentiality_label}`)}</Badge>
        )}
        {report.report_type && <Badge variant="outline">{report.report_type}</Badge>}
        {report.confidentiality && <Badge variant="secondary">{report.confidentiality}</Badge>}
        {report.is_demo && <Badge variant="outline">demo</Badge>}
      </div>
      <div className="text-editorial text-lg mt-2">{report.document_title || report.report_id}</div>
      <div className="text-xs text-muted-foreground mt-1 flex flex-wrap gap-x-3">
        {report.theme_name && <span>{report.theme_name}</span>}
        {report.author && <span>{report.author}</span>}
        {report.owner_department && <span>{report.owner_department}</span>}
        {report.uploaded_at && <span>{new Date(report.uploaded_at).toLocaleDateString()}</span>}
      </div>

      <div className="flex items-center gap-3 mt-2 flex-wrap">
        <Button
          size="sm"
          variant="ghost"
          className="px-0 h-auto text-xs"
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? "詳細を閉じる" : "詳細・Version履歴・Review"}
        </Button>
        <a
          href={geoReportDownloadUrl(report.report_id)}
          download
          className="text-xs text-primary underline underline-offset-2"
        >
          ダウンロード
        </a>
        <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
          <AlertDialogTrigger asChild>
            <button type="button" className="text-xs text-destructive underline underline-offset-2">
              削除
            </button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>このレポートを削除しますか？</AlertDialogTitle>
              <AlertDialogDescription>
                「{report.document_title || report.report_id}
                」を削除します。元のファイル・抽出済み本文・
                このレポートから作られたClaimも削除され、元に戻せません。他のCaseのレビュー記録から
                参照されている場合は削除できません。
              </AlertDialogDescription>
            </AlertDialogHeader>
            {deleteMutation.isError && (
              <p className="text-xs text-destructive">{(deleteMutation.error as Error).message}</p>
            )}
            <AlertDialogFooter>
              <AlertDialogCancel>キャンセル</AlertDialogCancel>
              <AlertDialogAction
                disabled={deleteMutation.isPending}
                onClick={(e) => {
                  e.preventDefault();
                  deleteMutation.mutate(
                    { reportId: report.report_id },
                    { onSuccess: () => setDeleteDialogOpen(false) },
                  );
                }}
              >
                {deleteMutation.isPending ? "削除中..." : "削除する"}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>

      {expanded && (
        <div className="mt-3 pt-3 border-t border-border space-y-3">
          <div className="space-y-1">
            <Button
              size="sm"
              variant="ghost"
              className="px-0 h-auto text-xs"
              onClick={() => setShowFullText((v) => !v)}
            >
              {showFullText ? "本文を隠す" : "本文を表示"}
            </Button>
            {showFullText && (
              <pre className="text-xs bg-muted/40 rounded p-2 whitespace-pre-wrap max-h-80 overflow-y-auto">
                {detailQuery.isLoading
                  ? "読み込み中..."
                  : detailQuery.data?.document?.extracted_text || "本文がありません。"}
              </pre>
            )}
          </div>
          <div className="space-y-1">
            <p className="text-xs font-medium text-muted-foreground">Version履歴</p>
            {(versionsQuery.data?.versions ?? []).map((v) => (
              <div key={v.report_id} className="text-xs flex items-center gap-2">
                <Badge variant="outline">v{v.version}</Badge>
                <span>{v.document_title || v.report_id}</span>
                {v.report_id !== report.report_id && (
                  <button
                    className="text-primary underline"
                    onClick={() => setDiffTarget(v.report_id)}
                  >
                    このReportとdiff
                  </button>
                )}
              </div>
            ))}
          </div>

          {diffQuery.data && (
            <div className="text-xs bg-muted/40 rounded p-2 space-y-1 overflow-x-auto">
              <p className="font-medium">Metadata差分</p>
              {Object.entries(diffQuery.data.metadata_diff).map(([k, v]) => (
                <div key={k}>
                  {k}: {String(v.from)} → {String(v.to)}
                </div>
              ))}
              <p className="font-medium pt-1">本文差分</p>
              <pre className="whitespace-pre-wrap">{diffQuery.data.text_diff.join("\n")}</pre>
            </div>
          )}

          {!reviewSessionId ? (
            <div className="space-y-2">
              <p className="text-xs font-medium text-muted-foreground">
                レビュー版登録 / Review開始
              </p>
              <p className="text-[11px] text-muted-foreground">
                レビュー後のファイルをアップロードすると、新Version（v2）の作成とDelta Extractionが
                一括で実行されます。
              </p>
              <div className="flex gap-2 items-center">
                <Input
                  value={reviewerId}
                  onChange={(e) => setReviewerId(e.target.value)}
                  placeholder="reviewer_id"
                  className="max-w-[160px] text-xs h-8"
                />
                <input ref={fileInputRef} type="file" className="text-xs" />
                <Button
                  size="sm"
                  disabled={startReviewMutation.isPending}
                  onClick={() => {
                    const file = fileInputRef.current?.files?.[0];
                    if (!file || !reviewerId.trim()) return;
                    startReviewMutation.mutate(
                      { file, reviewerId },
                      {
                        onSuccess: (data) =>
                          setReviewSessionId(data.review_session.review_session_id),
                      },
                    );
                  }}
                >
                  {startReviewMutation.isPending ? "処理中..." : "Review開始"}
                </Button>
              </div>
              {startReviewMutation.isError && (
                <p className="text-xs text-destructive">
                  {(startReviewMutation.error as Error).message}
                </p>
              )}
            </div>
          ) : (
            <ExpertReviewLoop reviewSessionId={reviewSessionId} actorId={reviewerId} />
          )}
        </div>
      )}
    </div>
  );
}
