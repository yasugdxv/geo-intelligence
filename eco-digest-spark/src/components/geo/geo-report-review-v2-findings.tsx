import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  useSaveHumanExpertDisposition,
  type GeoReportReviewV2ComparisonFinding,
  type GeoReportReviewV2Disposition,
  type GeoReportReviewV2DispositionRecord,
} from "@/lib/geoReportReviewV2Api";
import { useLanguage } from "@/lib/i18n";

// Report Review V2 Phase R2: Human Expert Review。
// AIの主要Finding（Reading Review/Content Review、別レイヤーとして保持——ユーザー指示）に
// 対してAgree/Modify/Rejectを選択可能にし、理由・修正内容を保存する。
// AI Reviewの結果をそのまま正解扱いしない——Agreeであっても必ず人間の明示的な判断として
// 記録する。Learning Candidateへの接続は将来のPhaseに先送りする（ここでは保存のみ）。

const SEVERITY_LABEL: Record<string, string> = { Critical: "P0", Major: "P1", Minor: "P2" };
const SEVERITY_CHIP: Record<string, string> = {
  Critical: "bg-[#fdeced] text-[#d85f62] border border-[#f6cfd0]",
  Major: "bg-[#fff3e7] text-[#e9903d] border border-[#f1d1ad]",
  Minor: "bg-[#eaf8fb] text-[#0d879a] border border-[#c9ecf1]",
};

const DISPOSITION_LABEL: Record<GeoReportReviewV2Disposition, string> = {
  agree: "Agree",
  modify: "Modify",
  reject: "Reject",
};
const DISPOSITION_CHIP: Record<GeoReportReviewV2Disposition, string> = {
  agree: "bg-[#eaf6ea] text-[#3f8f52] border border-[#c9e6cd]",
  modify: "bg-[#fff3e7] text-[#e9903d] border border-[#f1d1ad]",
  reject: "bg-[#fdeced] text-[#d85f62] border border-[#f6cfd0]",
};

type Source = "reading" | "content";

function FindingCard({
  sessionId,
  source,
  finding,
  existingDisposition,
  reviewerId,
}: {
  sessionId: string;
  source: Source;
  finding: GeoReportReviewV2ComparisonFinding;
  existingDisposition: GeoReportReviewV2DispositionRecord | undefined;
  reviewerId: string;
}) {
  const { t } = useLanguage();
  const saveDisposition = useSaveHumanExpertDisposition(sessionId);
  const [editingAs, setEditingAs] = useState<GeoReportReviewV2Disposition | null>(null);
  const [reason, setReason] = useState("");
  const [modification, setModification] = useState("");

  const findingId = finding.finding_id ?? "";
  const severity = finding.severity ?? "Minor";

  const startEditing = (disposition: GeoReportReviewV2Disposition) => {
    if (disposition === "agree") {
      saveDisposition.mutate({
        source,
        finding_id: findingId,
        disposition: "agree",
        reason: "妥当",
        reviewed_by: reviewerId,
      });
      return;
    }
    setEditingAs(disposition);
    setReason("");
    setModification("");
  };

  const submitEditing = () => {
    if (!editingAs || !reason.trim()) return;
    saveDisposition.mutate(
      {
        source,
        finding_id: findingId,
        disposition: editingAs,
        reason: reason.trim(),
        modification: editingAs === "modify" ? modification.trim() || null : null,
        reviewed_by: reviewerId,
      },
      { onSuccess: () => setEditingAs(null) },
    );
  };

  return (
    <div className="rounded-[10px] border border-[#d8e2e7] bg-white p-[13px] space-y-2">
      <div className="flex items-center gap-2">
        <span
          className={`px-2 py-0.5 rounded-full text-xs font-medium ${SEVERITY_CHIP[severity] ?? SEVERITY_CHIP.Minor}`}
        >
          {SEVERITY_LABEL[severity] ?? severity}
        </span>
        {finding.category && (
          <span className="text-xs text-muted-foreground">{finding.category}</span>
        )}
        {existingDisposition && (
          <span
            className={`ml-auto px-2 py-0.5 rounded-full text-xs font-medium ${DISPOSITION_CHIP[existingDisposition.disposition]}`}
          >
            {DISPOSITION_LABEL[existingDisposition.disposition]}
          </span>
        )}
      </div>
      <p className="m-0 text-sm geo-prose">{finding.finding}</p>
      {finding.recommendation && (
        <p className="m-0 text-sm text-muted-foreground">{finding.recommendation}</p>
      )}

      {existingDisposition ? (
        <div className="rounded-[8px] bg-[#f4f7f8] p-2 text-xs text-[#5b6b76] space-y-1">
          <p className="m-0">{existingDisposition.reason}</p>
          {existingDisposition.modification && (
            <p className="m-0 whitespace-pre-wrap">{existingDisposition.modification}</p>
          )}
        </div>
      ) : editingAs ? (
        <div className="space-y-2">
          <Textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder={t("geo.reportReviewV2.dispositionReasonPlaceholder")}
            rows={2}
            className="text-sm"
          />
          {editingAs === "modify" && (
            <Textarea
              value={modification}
              onChange={(e) => setModification(e.target.value)}
              placeholder={t("geo.reportReviewV2.dispositionModificationPlaceholder")}
              rows={3}
              className="text-sm"
            />
          )}
          <div className="flex gap-2">
            <Button
              size="sm"
              className="bg-[#6d68e8] hover:bg-[#5b56d6] text-white text-sm"
              disabled={!reason.trim() || saveDisposition.isPending}
              onClick={submitEditing}
            >
              {saveDisposition.isPending && <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />}
              {t("geo.reportReviewV2.dispositionSave")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="text-sm"
              onClick={() => setEditingAs(null)}
            >
              {t("geo.reportReviewV2.dispositionCancel")}
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            className="text-sm"
            onClick={() => startEditing("agree")}
          >
            Agree
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="text-sm"
            onClick={() => startEditing("modify")}
          >
            Modify
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="text-sm"
            onClick={() => startEditing("reject")}
          >
            Reject
          </Button>
        </div>
      )}
    </div>
  );
}

function FindingsSection({
  sessionId,
  source,
  title,
  findings,
  dispositions,
  reviewerId,
}: {
  sessionId: string;
  source: Source;
  title: string;
  findings: GeoReportReviewV2ComparisonFinding[];
  dispositions: Record<string, GeoReportReviewV2DispositionRecord>;
  reviewerId: string;
}) {
  const { t } = useLanguage();
  if (findings.length === 0) {
    return (
      <div className="space-y-2">
        <h4 className="text-sm font-semibold text-[#102b3f]">{title}</h4>
        <p className="text-sm text-muted-foreground">{t("geo.reportReviewV2.noFindings")}</p>
      </div>
    );
  }
  return (
    <div className="space-y-2">
      <h4 className="text-sm font-semibold text-[#102b3f]">{title}</h4>
      <div className="space-y-2">
        {findings.map((finding, i) => (
          <FindingCard
            key={finding.finding_id ?? i}
            sessionId={sessionId}
            source={source}
            finding={finding}
            existingDisposition={dispositions[`${source}:${finding.finding_id}`]}
            reviewerId={reviewerId}
          />
        ))}
      </div>
    </div>
  );
}

export function GeoReportReviewV2Findings({
  sessionId,
  readingFindings,
  contentFindings,
  dispositions,
  reviewerId,
}: {
  sessionId: string;
  readingFindings: GeoReportReviewV2ComparisonFinding[];
  contentFindings: GeoReportReviewV2ComparisonFinding[];
  dispositions: Record<string, GeoReportReviewV2DispositionRecord>;
  reviewerId: string;
}) {
  const { t } = useLanguage();
  return (
    <div className="rounded-[14px] border border-[#d8e2e7] bg-[#fbfcfd] p-[18px] space-y-4">
      <div>
        <h3 className="text-lg font-semibold text-[#102b3f]">
          {t("geo.reportReviewV2.humanExpertReviewTitle")}
        </h3>
        <p className="m-0 mt-1 text-sm text-muted-foreground">
          {t("geo.reportReviewV2.humanExpertReviewSubtitle")}
        </p>
      </div>
      <FindingsSection
        sessionId={sessionId}
        source="reading"
        title={t("geo.reportReviewV2.readingReviewFindingsTitle")}
        findings={readingFindings}
        dispositions={dispositions}
        reviewerId={reviewerId}
      />
      <FindingsSection
        sessionId={sessionId}
        source="content"
        title={t("geo.reportReviewV2.contentReviewFindingsTitle")}
        findings={contentFindings}
        dispositions={dispositions}
        reviewerId={reviewerId}
      />
    </div>
  );
}
