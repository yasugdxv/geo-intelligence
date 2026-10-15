import { Loader2 } from "lucide-react";
import { ReadingPresentationV2Report } from "@/components/geo/geo-ios-reading-presentation-v2";
import {
  useExpertReadingPresentation,
  type GeoReportReviewV2ComparisonRow,
  type GeoReportReviewV2ComparisonSummary,
  type GeoReportReviewV2ReportReading,
} from "@/lib/geoReportReviewV2Api";
import { useLanguage } from "@/lib/i18n";

// Report Review V2: 「読み方の比較」セクション。ユーザー指示（2026-10-03）に基づき、
// Report Reading側とExpert AI Reading側のUI構造を無理に同一化しない。
// - Report Reading: Extractionが実際に持つ項目だけを表示する専用の軽量カード
//   （Trajectory/Media Context等、本文から抽出できない項目をExpert AI側に合わせる
//   目的で追加生成しない）。
// - Expert AI Reading: 既存ReadingPresentationV2Report（7セクション構造）をそのまま
//   使う（response_json実体があるため）。
// 上部に比較しやすくするための共通サマリー表を置く。左右を同じ情報量にすることが
// 目的ではなく、「どこが違うか」を比較できることを優先する。

function isNotStated(value: string | null | undefined): value is null | undefined {
  return value === null || value === undefined || value === "";
}

function SummaryRow({
  label,
  row,
  notStatedLabel,
}: {
  label: string;
  row: GeoReportReviewV2ComparisonRow;
  notStatedLabel: string;
}) {
  return (
    <tr className="border-b border-[#eef1f3] last:border-b-0">
      <th className="w-[140px] py-2.5 pr-3 text-left text-sm font-medium text-[#5b6b76] align-top">
        {label}
      </th>
      <td className="py-2.5 pr-3 text-sm text-[#102b3f] align-top">
        {isNotStated(row.report) ? (
          <span className="text-[#9aa7ad] italic">{notStatedLabel}</span>
        ) : (
          row.report
        )}
      </td>
      <td className="py-2.5 text-sm text-[#102b3f] align-top">
        {isNotStated(row.expert_ai) ? (
          <span className="text-[#9aa7ad] italic">{notStatedLabel}</span>
        ) : (
          row.expert_ai
        )}
      </td>
    </tr>
  );
}

function ComparisonSummaryTable({ summary }: { summary: GeoReportReviewV2ComparisonSummary }) {
  const { t } = useLanguage();
  return (
    <div className="overflow-x-auto rounded-[12px] border border-[#d8e2e7]">
      <table className="w-full border-collapse">
        <thead>
          <tr className="border-b border-[#d8e2e7] bg-[#f4f7f8]">
            <th className="w-[140px] py-2.5 pl-3 pr-3 text-left text-xs font-semibold text-[#5b6b76]" />
            <th className="py-2.5 pr-3 text-left text-xs font-semibold text-[#5b6b76]">
              {t("geo.reportReviewV2.summaryColumnReport")}
            </th>
            <th className="py-2.5 text-left text-xs font-semibold text-[#5b6b76]">
              {t("geo.reportReviewV2.summaryColumnExpertAi")}
            </th>
          </tr>
        </thead>
        <tbody className="[&>tr>th]:pl-3 [&>tr>td]:pl-0">
          <SummaryRow
            label={t("geo.reportReviewV2.summaryRowCue")}
            row={summary.cue}
            notStatedLabel={t("geo.reportReviewV2.notStated")}
          />
          <SummaryRow
            label={t("geo.reportReviewV2.summaryRowKeyActors")}
            row={summary.key_actors}
            notStatedLabel={t("geo.reportReviewV2.notStated")}
          />
          <SummaryRow
            label={t("geo.reportReviewV2.summaryRowPrimaryReading")}
            row={summary.primary_reading}
            notStatedLabel={t("geo.reportReviewV2.notStated")}
          />
          <SummaryRow
            label={t("geo.reportReviewV2.summaryRowEvidence")}
            row={summary.supporting_evidence}
            notStatedLabel={t("geo.reportReviewV2.notStated")}
          />
          <SummaryRow
            label={t("geo.reportReviewV2.summaryRowWeakening")}
            row={summary.weakening_or_switch_condition}
            notStatedLabel={t("geo.reportReviewV2.notStated")}
          />
        </tbody>
      </table>
    </div>
  );
}

function ReportReadingField({
  label,
  value,
  notStatedLabel,
}: {
  label: string;
  value: string | string[] | null | undefined;
  notStatedLabel: string;
}) {
  const text = Array.isArray(value) ? value.join("; ") : value;
  return (
    <div className="space-y-0.5">
      <p className="m-0 text-xs font-medium text-[#5b6b76]">{label}</p>
      {isNotStated(text) ? (
        <p className="m-0 text-sm text-[#9aa7ad] italic">{notStatedLabel}</p>
      ) : (
        <p className="m-0 text-sm text-[#102b3f] geo-prose">{text}</p>
      )}
    </div>
  );
}

function ReportReadingCard({ reading }: { reading: GeoReportReviewV2ReportReading }) {
  const { t } = useLanguage();
  const notStated = t("geo.reportReviewV2.notStated");
  const relationships =
    (reading.relationships ?? []).map((r) => `${r.between}: ${r.description}`).join("; ") || null;

  return (
    <div className="rounded-[14px] border border-[#d8e2e7] bg-white p-[16px] space-y-3">
      <h4 className="text-sm font-semibold text-[#102b3f]">
        {t("geo.reportReviewV2.reportReadingCardTitle")}
      </h4>
      <ReportReadingField
        label={t("geo.reportReviewV2.fieldSituation")}
        value={reading.current_situation}
        notStatedLabel={notStated}
      />
      <ReportReadingField
        label={t("geo.reportReviewV2.fieldCue")}
        value={reading.cue}
        notStatedLabel={notStated}
      />
      <ReportReadingField
        label={t("geo.reportReviewV2.fieldKeyActors")}
        value={reading.key_actors}
        notStatedLabel={notStated}
      />
      <ReportReadingField
        label={t("geo.reportReviewV2.fieldRelationships")}
        value={relationships}
        notStatedLabel={notStated}
      />
      <ReportReadingField
        label={t("geo.reportReviewV2.fieldPrimaryReading")}
        value={reading.report_primary_reading}
        notStatedLabel={notStated}
      />
      <ReportReadingField
        label={t("geo.reportReviewV2.fieldSupportingEvidence")}
        value={reading.supporting_evidence}
        notStatedLabel={notStated}
      />
      <ReportReadingField
        label={t("geo.reportReviewV2.fieldWeakeningEvidence")}
        value={reading.weakening_evidence}
        notStatedLabel={notStated}
      />
      <ReportReadingField
        label={t("geo.reportReviewV2.fieldScenario")}
        value={reading.scenario}
        notStatedLabel={notStated}
      />
      <ReportReadingField
        label={t("geo.reportReviewV2.fieldSwitchCondition")}
        value={reading.switch_condition}
        notStatedLabel={notStated}
      />
    </div>
  );
}

export function GeoReportReviewV2Comparison({
  sessionId,
  reportReading,
  comparisonSummary,
}: {
  sessionId: string;
  reportReading: GeoReportReviewV2ReportReading;
  comparisonSummary: GeoReportReviewV2ComparisonSummary;
}) {
  const { t } = useLanguage();
  const expertPresentationQuery = useExpertReadingPresentation(sessionId, true);

  return (
    <div className="rounded-[14px] border border-[#d8e2e7] bg-[#fbfcfd] p-[18px] space-y-4">
      <div>
        <h3 className="text-lg font-semibold text-[#102b3f]">
          {t("geo.reportReviewV2.comparisonTitle")}
        </h3>
        <p className="m-0 mt-1 text-sm text-muted-foreground">
          {t("geo.reportReviewV2.comparisonSubtitle")}
        </p>
      </div>

      <ComparisonSummaryTable summary={comparisonSummary} />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
        <ReportReadingCard reading={reportReading} />
        <div className="space-y-2">
          <h4 className="text-sm font-semibold text-[#102b3f]">
            {t("geo.reportReviewV2.expertReadingCardTitle")}
          </h4>
          {expertPresentationQuery.isLoading && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              {t("geo.reportReviewV2.expertReadingLoading")}
            </div>
          )}
          {expertPresentationQuery.data && (
            <ReadingPresentationV2Report
              presentation={expertPresentationQuery.data}
              showAnalysisBrief={false}
            />
          )}
        </div>
      </div>
    </div>
  );
}
