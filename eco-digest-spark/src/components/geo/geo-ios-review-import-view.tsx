import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  useImportReportForReview,
  useGenerateReviewReadings,
  useReviewImport,
} from "@/lib/geoIosReviewImportApi";
import { useLanguage } from "@/lib/i18n";

// REVIEW MODE Step1（§18）: 既存レポートの取込・前提確認。PDF/Word/テキスト貼り付けを
// 受け付ける（既存geo_documents_store.extract_text経由、report-drafts/.../importと同じ
// 既存パターンをそのまま再利用——新しい独自File storageは作らない）。PowerPointは
// 現時点で未対応（python-pptx等の新規依存が必要なため、Known Gapとして明示する）。
interface Props {
  sessionId: string;
  actorId: string;
  onReadingsReady: () => void;
}

export function GeoIosReviewImportView({ sessionId, actorId, onReadingsReady }: Props) {
  const { t } = useLanguage();
  const [pastedText, setPastedText] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const importReport = useImportReportForReview(sessionId);
  const generateReadings = useGenerateReviewReadings(sessionId);
  const reviewImportQuery = useReviewImport(sessionId, { pollWhilePending: true });

  const reviewImport = reviewImportQuery.data;
  const readingsReady = reviewImport?.status === "readings_ready";

  useEffect(() => {
    if (readingsReady) onReadingsReady();
  }, [readingsReady, onReadingsReady]);

  if (readingsReady) return null;

  const handleImport = () => {
    const file = fileInputRef.current?.files?.[0];
    if (!file && !pastedText.trim()) return;
    importReport.mutate({
      created_by: actorId,
      file: file ?? undefined,
      pastedText: pastedText || undefined,
    });
  };

  const handleStartReview = () => {
    generateReadings.mutate({ actor_id: actorId });
  };

  return (
    <div className="space-y-5">
      {!reviewImport && (
        <div className="rounded-[14px] border border-[#d8e2e7] bg-white p-[20px] space-y-4">
          <h2 className="text-xl font-semibold text-[#172d3d]">
            {t("geo.ios.review.importTitle")}
          </h2>
          <p className="text-base geo-prose m-0">{t("geo.ios.review.importSubtitle")}</p>
          <div className="space-y-2">
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.docx,.doc,.txt,.html"
              className="text-sm"
            />
            <p className="geo-meta">{t("geo.ios.review.pptxUnsupportedNote")}</p>
          </div>
          <div className="space-y-1.5">
            <p className="text-[13px] font-bold text-[#0d879a] m-0">
              {t("geo.ios.review.pasteTextLabel")}
            </p>
            <Textarea
              rows={8}
              value={pastedText}
              onChange={(e) => setPastedText(e.target.value)}
              placeholder={t("geo.ios.review.pasteTextPlaceholder")}
            />
          </div>
          <Button onClick={handleImport} disabled={importReport.isPending}>
            {importReport.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
            {t("geo.ios.review.importSubmit")}
          </Button>
          {importReport.isError && (
            <p className="text-xs text-destructive">{(importReport.error as Error).message}</p>
          )}
        </div>
      )}

      {reviewImport && reviewImport.status !== "readings_ready" && (
        <div className="rounded-[14px] border border-[#d8e2e7] bg-white p-[20px] space-y-4">
          <h2 className="text-xl font-semibold text-[#172d3d]">
            {t("geo.ios.review.contextTitle")}
          </h2>
          {reviewImport.status === "pending" && !generateReadings.isPending && (
            <p className="text-base geo-prose">{t("geo.ios.review.readyToExtract")}</p>
          )}
          {reviewImport.status === "pending" && generateReadings.isPending && (
            <div className="flex items-center gap-2 text-base text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> {t("geo.ios.review.extracting")}
            </div>
          )}
          {reviewImport.status === "context_extracted" && reviewImport.extracted_context && (
            <div className="space-y-3">
              <div>
                <b className="block text-[13px] text-[#0d879a]">
                  {t("geo.ios.situation.contextLabel")}
                </b>
                <p className="m-0 text-base geo-prose">
                  {reviewImport.extracted_context.analysis_theme}
                </p>
              </div>
              <div>
                <b className="block text-[13px] text-[#0d879a]">
                  {t("geo.ios.situation.currentSituationLabel")}
                </b>
                <p className="m-0 text-base geo-prose">
                  {reviewImport.extracted_context.current_situation}
                </p>
              </div>
              <div>
                <b className="block text-[13px] text-[#0d879a]">
                  {t("geo.ios.situation.questionLabel")}
                </b>
                <p className="m-0 text-base geo-prose">{reviewImport.extracted_context.question}</p>
              </div>
              <div>
                <b className="block text-[13px] text-[#0d879a]">
                  {t("geo.ios.review.reportContextLabel")}
                </b>
                <p className="m-0 text-base geo-prose">
                  {reviewImport.extracted_context.report_context}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                <p className="m-0 text-base text-muted-foreground">
                  {t("geo.ios.review.reconstructingReading")}
                </p>
              </div>
            </div>
          )}
          {reviewImport.status === "failed" && (
            <p className="text-sm text-destructive">
              {t("geo.ios.review.extractionFailed")}: {reviewImport.error_message}
            </p>
          )}
          {reviewImport.status === "pending" && (
            <Button onClick={handleStartReview} disabled={generateReadings.isPending}>
              {generateReadings.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              {t("geo.ios.review.startReviewCta")}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
