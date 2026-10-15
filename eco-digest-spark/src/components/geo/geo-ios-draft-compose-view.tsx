import { useEffect, useRef, useState } from "react";
import { Loader2, Paperclip, Upload, X } from "lucide-react";
import { useNavigate } from "@tanstack/react-router";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { GeoExpertAiProgress } from "@/components/geo/geo-expert-ai-progress";
import { useCreateReport } from "@/lib/geoIosAnalysisSessionApi";
import { useSituationPresentation } from "@/lib/geoIosSituationPresentationApi";
import { useReportReviewV2, useRunReportReviewV2 } from "@/lib/geoReportReviewV2Api";
import { GeoReportReviewV2Comparison } from "@/components/geo/geo-report-review-v2-comparison";
import { GeoReportReviewV2Findings } from "@/components/geo/geo-report-review-v2-findings";
import {
  useCurrentDraft,
  useCreateDraft,
  useAiEditDraft,
  useImportDraft,
  useLatestAiReview,
  useRequestAiReview,
  useWaiveFinding,
  type GeoIosDraftSection,
  type GeoIosFindingSeverity,
} from "@/lib/geoIosReportDraftApi";
import { useLanguage } from "@/lib/i18n";
import {
  IMPORT_ACCEPT,
  IMPORT_MAX_FILE_MB,
  IMPORT_MAX_TEXT_CHARS,
  checkImportFile,
} from "@/lib/iosUploadLimits";

// Geopolitical Intelligence OS: Native Report Draft（IOS自身のDraft生成〜AI Review、
// Report Studioに依存しない本文生成〜レビューフローの一部）。
//
// 【設計上守っていること、実装変更時も維持すること】
// - Draft本文はimmutable。手直し保存も常に新Version（source="human_edit"）として送る——
//   in-place更新のAPIは呼ばない。
// - AI Reviewは1 Draftに複数回実行できる（upsertしない）。「解決済みにする」ボタンは無く、
//   採用しない指摘はWaiveのみ。
// - 2026-09-24変更、ユーザー指摘「一個ずつポップアップが開いて理由を書くのが手間すぎる。
//   ボタン押下のみにして」: Waiveは理由入力ダイアログ無しの1クリックにした。Backend
//   （geo_ios_report_draft_service.waive_finding）はwaiver_reason非空を引き続き要求する
//   （監査証跡として指摘が根拠なく揉み消されたのではないことを示す値は残す設計自体は維持）
//   ため、固定の定型文（WAIVE_DEFAULT_REASON）を自動で送る——手入力を要求しない形で
//   両立させる。
// - ドラッグ並べ替え・チャット駆動のAI自動修正(diff適用)はv1では実装しない。
// - 2026-09-23変更、ユーザー指摘「AIレビューを経ないと進めないなら、このメニューごと
//   削って」に一度対応しAI Reviewカード自体を削除したが、これは誤りだった——
//   `geo_ios_report_draft_service.check_expert_review_gate`
//   （`geo_ios_review_api.py`の`POST /api/geo/ios/review/cycles`、Reviewページの
//   「Request Expert Review」実行時）に実在するBackend Gateで、native draft
//   sessionはAI Review完了・open Criticalゼロを満たさないと本当に400で拒否される
//   （調査時に別ファイルのgrepを見落としていた）。そのためAI Review実行・Waiveの
//   手段自体は復元し、以後削除しない。一方「専門家レビューへ進む」ボタン（このDraft
//   画面のもの）はcreateReport（Report Project作成・リンク）とnavigateしかせず、
//   上記Gate自体は呼ばないため、この画面のボタンは引き続き常時有効のままでよい
//   （実際のGateはReviewページの「Request Expert Review」時に効く）。
interface Props {
  sessionId: string;
  actorId: string;
  linkedReportProjectId: string | null | undefined;
  // Phase 2C（レビューフロー独立化）: "review-intake"は「完成済みレポートをアップロード
  // してレビューする」専用の見せ方。AI生成(source="ai_generated")・AIチャット編集・手動編集・
  // 追加調査(Report Studio導線)を隠し、アップロード→AI Review→専門家レビューへ進む、の
  // 3点に絞る——Backend（create_draft/import_draft/AI Review/create_report）自体は一切
  // 変更せず、表示のみを絞り込む。省略時は既存のフル機能表示（"full"）のまま。
  variant?: "full" | "review-intake";
}

const SEVERITY_LABEL: Record<GeoIosFindingSeverity, string> = {
  Critical: "P0",
  Major: "P1",
  Minor: "P2",
};
const SEVERITY_CHIP: Record<GeoIosFindingSeverity, string> = {
  Critical: "bg-[#fdeced] text-[#d85f62] border border-[#f6cfd0]",
  Major: "bg-[#fff3e7] text-[#e9903d] border border-[#f1d1ad]",
  Minor: "bg-[#eaf8fb] text-[#0d879a] border border-[#c9ecf1]",
};
const WAIVE_DEFAULT_REASON = "担当者判断によりWaive（詳細理由は未入力）";

export function GeoIosDraftComposeView({
  sessionId,
  actorId,
  linkedReportProjectId,
  variant = "full",
}: Props) {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const isReviewIntake = variant === "review-intake";

  const currentDraftQuery = useCurrentDraft(sessionId);
  const draft = currentDraftQuery.data;
  const createDraft = useCreateDraft(sessionId);
  const aiEditDraft = useAiEditDraft(sessionId);
  const importDraft = useImportDraft(sessionId);
  const createReport = useCreateReport(sessionId);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const aiEditFileInputRef = useRef<HTMLInputElement>(null);

  const reviewableDraftId = draft?.status === "completed" ? draft.report_draft_id : undefined;
  const reviewQuery = useLatestAiReview(reviewableDraftId);
  const review = reviewQuery.data;
  const requestAiReview = useRequestAiReview(reviewableDraftId);
  const waiveFinding = useWaiveFinding(reviewableDraftId, review?.draft_ai_review_id);
  // review-intake variant専用: 既存run_draft_ai_review（_fetch_design_and_situationが
  // Design必須）はreview-modeセッションにDesignが無いため常に400で失敗し、
  // check_expert_review_gateを永久に通過できなかった（調査で確認した既存バグ）。
  // Report Review V2（run_report_reading_review）が実際のレポート内容に基づく
  // comparison_findingsを生成し、完了時にgeo_ios_draft_ai_reviewsへ直接書き込むため
  // （geo_report_review_v2_service.execute_run参照）、findings/Waive UIは下の
  // 既存reviewQuery/waiveFindingをそのまま使い続けられる——ここではトリガーと
  // 実行中/失敗状態の表示元だけをisReviewIntakeで出し分ける。
  const reportReviewV2Query = useReportReviewV2(isReviewIntake ? sessionId : undefined);
  const runReportReviewV2 = useRunReportReviewV2(sessionId);
  useEffect(() => {
    if (isReviewIntake && reportReviewV2Query.data?.status === "completed") {
      reviewQuery.refetch();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isReviewIntake, reportReviewV2Query.data?.status]);
  // 2026-09-23追加、ユーザー指示「ドラフトの下部に、追加調査提案パートを作って」:
  // 「2 局面を読む」タブと同じuseSituationPresentationを再利用する（research_needs＝
  // identified_knowledge_gaps/additional_researchの統合済み一覧、新しいデータ取得先は
  // 増やさない）。
  const presentationQuery = useSituationPresentation(sessionId);
  const researchNeeds = presentationQuery.data?.presentation.research_needs ?? [];

  const [editing, setEditing] = useState(false);
  const [editedSections, setEditedSections] = useState<GeoIosDraftSection[]>([]);
  const [waivingFindingId, setWaivingFindingId] = useState<string | null>(null);
  const [proceeding, setProceeding] = useState(false);
  const [openingResearch, setOpeningResearch] = useState(false);
  const [editInstruction, setEditInstruction] = useState("");
  const [editAttachment, setEditAttachment] = useState<File | null>(null);
  // Platform IA / Expert AI Reading UI Cleanup §13（2026-10-02）: レポートレビューの
  // 入口にテキスト貼り付けを追加する。新しいStorage/Parsing基盤は作らず、既存の
  // importDraft（ファイルアップロード）経路をそのまま再利用する——貼り付けたテキストを
  // クライアント側でFile化し、同じmutationへ渡すだけ（Backend側のgeo_documents_store.
  // extract_textは.txtをそのままテキストとして扱うため、新規APIは不要）。
  const [pasteMode, setPasteMode] = useState(false);
  const [pasteTitle, setPasteTitle] = useState("");
  const [pasteText, setPasteText] = useState("");
  // アップロード前の事前チェックで見つかった問題（送信せずに表示する）
  const [uploadError, setUploadError] = useState<string | null>(null);

  const findings = review?.findings ?? [];
  const openCritical = findings.filter((f) => f.severity === "Critical" && f.status === "open");
  const reviewCompleted = review?.status === "completed";
  const aiReviewPassed = reviewCompleted && openCritical.length === 0;
  const staleAfterEdit = draft?.source === "human_edit" && !review; // 新Version直後、まだAI Review未実施

  // review-intake variant: トリガー元・実行中/失敗状態の表示元だけReview V2側にする
  // （findings自体はcheck_expert_review_gateが見るgeo_ios_draft_ai_reviewsへReview V2が
  // 直接書き込むため、上のreview/findings/waiveFindingはそのまま使い続けられる）。
  const aiReviewRunning = isReviewIntake
    ? runReportReviewV2.isPending || reportReviewV2Query.data?.status === "pending"
    : requestAiReview.isPending || review?.status === "pending";
  const aiReviewV2Failed = isReviewIntake && reportReviewV2Query.data?.status === "failed";
  const runAiReview = () => {
    if (!reviewableDraftId) return;
    if (isReviewIntake) {
      runReportReviewV2.mutate({ report_draft_id: reviewableDraftId, requested_by: actorId });
    } else {
      requestAiReview.mutate({ requested_by: actorId });
    }
  };

  const startEdit = () => {
    setEditedSections(draft?.sections ?? []);
    setEditing(true);
  };

  const saveEdit = () => {
    if (!draft) return;
    createDraft.mutate(
      {
        created_by: actorId,
        source: "human_edit",
        sections: editedSections,
        base_report_draft_id: draft.report_draft_id,
      },
      { onSuccess: () => setEditing(false) },
    );
  };

  // アップロード: 既存Draft生成(source="ai_generated")と並ぶ、もう一つのDraft作成経路。
  // ファイルはBackendでgeo_documents_store.extract_text（docx/pdf/html/text対応の
  // 既存共通処理）によりテキスト抽出され、AIがセクション分割して取り込む
  // （source="uploaded"、内容の要約・改変はしない指示——Backend側の役割）。
  const handleFileSelected = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // 同じファイルを連続選択しても onChange が発火するように
    if (!file) return;
    // 形式・サイズが合わない場合は、送信せずにその場で理由を表示する（処理を進めない）
    const problem = checkImportFile(file);
    if (problem) {
      setUploadError(
        problem.kind === "extension"
          ? problem.ext === ".doc"
            ? t("geo.ios.upload.errorExtensionDoc")
            : t("geo.ios.upload.errorExtension", { ext: problem.ext || "?" })
          : problem.kind === "empty"
            ? t("geo.ios.upload.errorEmpty")
            : t("geo.ios.upload.errorTooLarge", { mb: problem.mb, max: IMPORT_MAX_FILE_MB }),
      );
      return;
    }
    setUploadError(null);
    importDraft.mutate({ created_by: actorId, file });
  };

  const handlePasteImport = () => {
    if (!pasteText.trim()) return;
    if (pasteText.length > IMPORT_MAX_TEXT_CHARS) {
      setUploadError(
        t("geo.ios.upload.errorPasteTooLong", {
          n: Math.floor(pasteText.length / 10000),
          man: IMPORT_MAX_TEXT_CHARS / 10000,
        }),
      );
      return;
    }
    setUploadError(null);
    const file = new File([pasteText], `${(pasteTitle || "pasted-report").trim()}.txt`, {
      type: "text/plain",
    });
    importDraft.mutate(
      { created_by: actorId, file },
      {
        onSuccess: () => {
          setPasteMode(false);
          setPasteTitle("");
          setPasteText("");
        },
      },
    );
  };

  // 2026-09-23追加、ユーザー指示「AIで編集するところに追加資料をアップする機能が
  // いるね」: 既存importDraftと同じ拡張子を受け付ける（Backend側もgeo_documents_store.
  // extract_textを共用するため対応形式は同じ）。
  const handleAiEditAttachmentSelected = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (file) setEditAttachment(file);
  };

  // チャットでAIにDraftの書き換えを指示する。モックアップの「AIで編集」と同じく、
  // AIが直接Current Draftを書き換えて新Versionとして保存する(即時反映)。
  const submitAiEdit = () => {
    if (!editInstruction.trim()) return;
    aiEditDraft.mutate(
      { actor_id: actorId, instruction: editInstruction.trim(), file: editAttachment ?? undefined },
      {
        onSuccess: () => {
          setEditInstruction("");
          setEditAttachment(null);
        },
      },
    );
  };

  // 2026-09-24追加、ユーザー指示「ボタン押下のみにして」: ダイアログを挟まず、
  // クリックした瞬間に定型理由でWaiveする。複数の指摘ボタンが同時に処理中表示に
  // ならないよう、押されたfinding_idだけをwaivingFindingIdで覚えておく。
  const waiveFindingOneClick = (findingId: string) => {
    setWaivingFindingId(findingId);
    waiveFinding.mutate(
      { finding_id: findingId, waiver_reason: WAIVE_DEFAULT_REASON, waived_by: actorId },
      { onSettled: () => setWaivingFindingId(null) },
    );
  };

  const proceedToExpertReview = async () => {
    setProceeding(true);
    try {
      let reportProjectId = linkedReportProjectId;
      if (!reportProjectId) {
        const created = await createReport.mutateAsync({ actor_id: actorId });
        reportProjectId = created.report_project.project_id;
      }
      navigate({ to: "/geo/ios/review/$reportProjectId", params: { reportProjectId } });
    } finally {
      setProceeding(false);
    }
  };

  // 追加調査: リサーチスタジオ(Report Studio、CSV取込・クラスタリング・追加合成が
  // できる別ツール)への導線。「追加調査する場合だけ使う」という既存の役割分担どおり、
  // ここではリンクを開くだけでIOS Native Draft自体のsectionsには一切書き戻さない。
  // Report Studio Projectが未リンクなら、専門家レビュー導線と同じcreateReport
  // （project_codeがSession由来で決定的なため、後から呼んでも同じProjectを指す）で
  // その場で作成してから開く。
  const openResearchStudio = async () => {
    setOpeningResearch(true);
    try {
      let reportProjectId = linkedReportProjectId;
      if (!reportProjectId) {
        const created = await createReport.mutateAsync({ actor_id: actorId });
        reportProjectId = created.report_project.project_id;
      }
      navigate({ to: "/geo/report-studio/$projectId", params: { projectId: reportProjectId } });
    } finally {
      setOpeningResearch(false);
    }
  };

  const additionalResearchButton = (
    <Button
      variant="outline"
      size="sm"
      className="border-[#f1d1ad] text-[#e9903d] hover:bg-[#fff3e7] text-sm"
      disabled={openingResearch || createReport.isPending}
      onClick={openResearchStudio}
    >
      {openingResearch || createReport.isPending ? (
        <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
      ) : (
        <span className="mr-1">+</span>
      )}
      {t("geo.ios.draft.additionalResearch")}
    </Button>
  );

  const hiddenFileInput = (
    <input
      ref={fileInputRef}
      type="file"
      accept={IMPORT_ACCEPT}
      className="hidden"
      onChange={handleFileSelected}
    />
  );

  if (!draft && !currentDraftQuery.isLoading) {
    return (
      <div className="space-y-3">
        {hiddenFileInput}
        <div className="rounded-[14px] border border-[#d8e2e7] bg-white p-[22px] text-center space-y-3">
          <p className="text-lg text-muted-foreground">
            {isReviewIntake ? t("geo.ios.review.uploadPrompt") : t("geo.ios.draft.noDraftYet")}
          </p>
          <div className="flex items-center justify-center gap-2">
            {!isReviewIntake && (
              <Button
                className="bg-[#36bfd6] hover:bg-[#2ea9bf] text-white text-base"
                disabled={createDraft.isPending || importDraft.isPending}
                onClick={() => createDraft.mutate({ created_by: actorId, source: "ai_generated" })}
              >
                {createDraft.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                {t("geo.ios.draft.generate")}
              </Button>
            )}
            <Button
              variant="outline"
              className="text-base"
              disabled={createDraft.isPending || importDraft.isPending}
              onClick={() => fileInputRef.current?.click()}
            >
              {importDraft.isPending ? (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              ) : (
                <Upload className="h-4 w-4 mr-2" />
              )}
              {t("geo.ios.draft.uploadOption")}
            </Button>
            {/* Platform IA / Expert AI Reading UI Cleanup §13: ファイルアップロードに
                並ぶもう一つの投入方式（テキスト貼り付け）。既存importDraftをそのまま
                再利用する（上のhandlePasteImport参照）。 */}
            {isReviewIntake && !pasteMode && (
              <Button
                variant="outline"
                className="text-base"
                disabled={createDraft.isPending || importDraft.isPending}
                onClick={() => setPasteMode(true)}
              >
                {t("geo.ios.review.pasteTextLabel")}
              </Button>
            )}
          </div>
          {/* アップロードできる条件（事前に見えるようにする。APIも同じ条件で確認する） */}
          <div
            className="text-left max-w-[640px] mx-auto rounded-lg bg-[#f3f7f9] px-4 py-3 text-sm text-muted-foreground space-y-0.5"
            data-testid="upload-conditions"
          >
            <p className="font-medium text-foreground">{t("geo.ios.upload.conditionsTitle")}</p>
            <ul className="list-disc pl-5 space-y-0.5">
              <li>{t("geo.ios.upload.conditionFormats")}</li>
              <li>{t("geo.ios.upload.conditionSize", { mb: IMPORT_MAX_FILE_MB })}</li>
              <li>{t("geo.ios.upload.conditionLength", { man: IMPORT_MAX_TEXT_CHARS / 10000 })}</li>
              <li>{t("geo.ios.upload.conditionScan")}</li>
              <li>{t("geo.ios.upload.conditionDoc")}</li>
            </ul>
          </div>
          {uploadError && (
            <p className="text-base text-destructive" role="alert" data-testid="upload-error">
              {uploadError}
            </p>
          )}
          {isReviewIntake && pasteMode && (
            <div className="text-left space-y-2 max-w-[640px] mx-auto">
              <Input
                value={pasteTitle}
                onChange={(e) => setPasteTitle(e.target.value)}
                placeholder={t("geo.ios.review.pasteTitlePlaceholder")}
                className="text-base"
              />
              <Textarea
                value={pasteText}
                onChange={(e) => setPasteText(e.target.value)}
                placeholder={t("geo.ios.review.pasteTextPlaceholder")}
                rows={10}
                className="text-base"
              />
              <p
                className={`text-xs tabular-nums ${
                  pasteText.length > IMPORT_MAX_TEXT_CHARS ? "text-destructive" : "text-muted-foreground"
                }`}
              >
                {t("geo.ios.upload.pasteCount", {
                  n: pasteText.length.toLocaleString(),
                  max: IMPORT_MAX_TEXT_CHARS.toLocaleString(),
                })}
              </p>
              <div className="flex items-center gap-2">
                <Button
                  className="bg-[#36bfd6] hover:bg-[#2ea9bf] text-white text-base"
                  disabled={!pasteText.trim() || importDraft.isPending}
                  onClick={handlePasteImport}
                >
                  {importDraft.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                  {t("geo.ios.review.importSubmit")}
                </Button>
                <Button
                  variant="ghost"
                  className="text-base"
                  disabled={importDraft.isPending}
                  onClick={() => setPasteMode(false)}
                >
                  {t("geo.ios.review.pasteCancel")}
                </Button>
              </div>
            </div>
          )}
          <GeoExpertAiProgress
            active={createDraft.isPending || importDraft.isPending}
            className="justify-center"
          />
          {createDraft.isError && (
            <p className="text-base text-destructive">{(createDraft.error as Error).message}</p>
          )}
          {importDraft.isError && (
            <p className="text-base text-destructive">{(importDraft.error as Error).message}</p>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {hiddenFileInput}

      {/* チャットでAIにDraftの書き換えを指示するボックス（モックアップの
          「AIレビュー結果を見ながら修正」と同じ、AIが直接書き換えて新Versionとして
          保存する即時反映パターン）。編集対象のDraftがcompletedである間のみ使える。
          Phase 2C: レビューフローではレビュー対象本文を書き換えさせないため非表示
          （修正が必要な場合は§8 Review Roundの「修正版Upload」で対応する）。 */}
      {!isReviewIntake && (
        <div className="rounded-[14px] border border-[#d8e2e7] bg-[#fbfcfd] p-[18px] space-y-3">
          <h3 className="text-lg font-semibold text-[#102b3f]">{t("geo.ios.draft.aiEditTitle")}</h3>
          {/* 2026-09-23追加、ユーザー指示「AIで編集するところに追加資料をアップする
              機能がいるね」: 編集指示と一緒に参考資料（docx/pdf/html/text）を添付できる。
              添付したファイルはBackendでテキスト抽出され、この編集リクエストにのみ
              使われる（既存Draft本文としては保存されない、importDraftとは別経路）。 */}
          <input
            ref={aiEditFileInputRef}
            type="file"
            accept=".docx,.pdf,.txt,.html,.htm"
            className="hidden"
            onChange={handleAiEditAttachmentSelected}
          />
          <div className="flex gap-2">
            <Input
              value={editInstruction}
              onChange={(e) => setEditInstruction(e.target.value)}
              placeholder={t("geo.ios.draft.aiEditPlaceholder")}
              disabled={aiEditDraft.isPending || draft?.status !== "completed"}
              onKeyDown={(e) => {
                if (e.key === "Enter") submitAiEdit();
              }}
              className="text-base md:text-base"
            />
            <Button
              type="button"
              variant="outline"
              size="icon"
              className="shrink-0"
              title={t("geo.ios.draft.aiEditAttachTitle")}
              disabled={aiEditDraft.isPending || draft?.status !== "completed"}
              onClick={() => aiEditFileInputRef.current?.click()}
            >
              <Paperclip className="h-4 w-4" />
            </Button>
            <Button
              className="bg-[#6d68e8] hover:bg-[#5b56d6] text-white shrink-0 text-base"
              disabled={
                aiEditDraft.isPending || !editInstruction.trim() || draft?.status !== "completed"
              }
              onClick={submitAiEdit}
            >
              {aiEditDraft.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              {t("geo.ios.draft.aiEditButton")}
            </Button>
          </div>
          {editAttachment && (
            <div className="flex items-center gap-2 geo-meta">
              <Paperclip className="h-3.5 w-3.5" />
              <span className="truncate">{editAttachment.name}</span>
              <button
                type="button"
                className="text-[#687b88] hover:text-destructive"
                onClick={() => setEditAttachment(null)}
                title={t("geo.ios.draft.aiEditRemoveAttachment")}
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          )}
          <GeoExpertAiProgress active={aiEditDraft.isPending} className="justify-start" />
          {aiEditDraft.isError && (
            <p className="text-base text-destructive">{(aiEditDraft.error as Error).message}</p>
          )}
        </div>
      )}

      <div className="rounded-[14px] border border-[#d8e2e7] bg-white p-[18px] space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-lg font-semibold text-[#102b3f]">
              {t("geo.ios.draft.title")} v{draft?.version}
            </h3>
            <p className="geo-meta">
              {draft?.source === "ai_generated" && t("geo.ios.draft.sourceAiGenerated")}
              {draft?.source === "human_edit" && t("geo.ios.draft.sourceHumanEdit")}
              {draft?.source === "uploaded" && t("geo.ios.draft.sourceUploaded")}
            </p>
          </div>
          {/* Phase 2C: レビューフローでは本文の手直し・AI再生成を出さない
              （§7 Reviewer-first Principle・§1「Draft生成はレビューフローに含めない」）。 */}
          {!isReviewIntake && draft?.status === "completed" && !editing && (
            <div className="flex gap-2">
              <Button variant="outline" size="sm" className="text-sm" onClick={startEdit}>
                {t("geo.ios.draft.edit")}
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="text-sm"
                disabled={createDraft.isPending}
                onClick={() =>
                  createDraft.mutate({
                    created_by: actorId,
                    source: "ai_generated",
                    change_note: t("geo.ios.draft.regenerateNote"),
                  })
                }
              >
                {createDraft.isPending && <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />}
                {t("geo.ios.draft.regenerate")}
              </Button>
            </div>
          )}
        </div>

        {draft?.status === "pending" && (
          <div className="py-6 text-center">
            <GeoExpertAiProgress active className="justify-center" />
          </div>
        )}
        {draft?.status === "failed" && (
          <p className="text-base text-destructive">
            {draft.error_message || t("geo.ios.draft.generationFailed")}
          </p>
        )}
        {createDraft.isError && (
          <p className="text-base text-destructive">
            {(createDraft.error as Error & { status?: number }).status === 409
              ? t("geo.ios.draft.staleEditConflict")
              : (createDraft.error as Error).message}
          </p>
        )}

        {draft?.status === "completed" && !editing && (
          <div className="space-y-3">
            {(draft.sections ?? []).map((s, i) => (
              <div key={i} className="rounded-[10px] border border-[#d8e2e7] bg-white p-[13px]">
                <h4 className="text-lg font-semibold text-[#102b3f] mb-1">
                  {String(i + 1).padStart(2, "0")}. {s.heading}
                </h4>
                <p className="text-lg geo-prose">{s.body}</p>
              </div>
            ))}
          </div>
        )}

        {editing && (
          <div className="space-y-3">
            {editedSections.map((s, i) => (
              <div
                key={i}
                className="rounded-[10px] border border-[#d8e2e7] bg-white p-[13px] space-y-2"
              >
                <Input
                  value={s.heading}
                  onChange={(e) =>
                    setEditedSections((prev) =>
                      prev.map((p, j) => (j === i ? { ...p, heading: e.target.value } : p)),
                    )
                  }
                  className="font-semibold text-base md:text-base"
                />
                <Textarea
                  value={s.body}
                  onChange={(e) =>
                    setEditedSections((prev) =>
                      prev.map((p, j) => (j === i ? { ...p, body: e.target.value } : p)),
                    )
                  }
                  className="min-h-[80px] text-base md:text-base"
                />
              </div>
            ))}
            <div className="flex items-center justify-between">
              <p className="geo-meta">{t("geo.ios.draft.editSavesNewVersion")}</p>
              <div className="flex gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-sm"
                  onClick={() => setEditing(false)}
                >
                  {t("geo.ios.draft.cancelEdit")}
                </Button>
                <Button
                  size="sm"
                  className="bg-[#36bfd6] hover:bg-[#2ea9bf] text-white text-sm"
                  disabled={createDraft.isPending}
                  onClick={saveEdit}
                >
                  {createDraft.isPending && <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />}
                  {t("geo.ios.draft.saveAsNewVersion")}
                </Button>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* 2026-09-23追加、ユーザー指示「＋追加調査ってあるけど、これだと意味が分からない
          から、ドラフトの下部に、追加調査提案パートを作って、ドラフト生成するときに
          その内容を記載しよう。そこに＋追加調査ボタンを置けばいい」: research_needs
          （identified_knowledge_gaps/additional_researchの統合済み一覧、「2 局面を
          読む」タブと同じデータ）をドラフト下部にリスト表示し、ボタンをその文脈の
          中へ移す（従来は右上に単独で浮いていて、何の追加調査かが分からなかった）。 */}
      {/* Phase 2C: 追加調査（Report Studioを開く導線）はレビューフロー対象外のため非表示。 */}
      {!isReviewIntake && draft?.status === "completed" && !editing && (
        <div className="rounded-[14px] border border-[#f1d1ad] bg-[#fff8f0] p-[18px] space-y-3">
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-lg font-semibold text-[#102b3f]">
              {t("geo.ios.draft.additionalResearchTitle")}
            </h3>
            {additionalResearchButton}
          </div>
          {researchNeeds.length > 0 ? (
            <div className="space-y-2">
              {researchNeeds.map((item, i) => (
                <div key={i} className="rounded-[10px] border border-[#f1d1ad] bg-white p-[13px]">
                  <p className="m-0 text-lg geo-prose">{item.text}</p>
                  {item.why && <p className="m-0 mt-1 geo-meta">{item.why}</p>}
                </div>
              ))}
            </div>
          ) : (
            <p className="geo-meta">{t("geo.ios.draft.noAdditionalResearch")}</p>
          )}
        </div>
      )}

      {/* AI Review: 2026-09-23に一度削除したが、Reviewページの「Request Expert
          Review」が実際に呼ぶBackend Gate（check_expert_review_gate）を満たす
          唯一の手段のため復元した（このファイル冒頭のコメント参照）。 */}
      {draft?.status === "completed" && (
        <div className="rounded-[14px] border border-[#d8e2e7] bg-[#fbfcfd] p-[18px] space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-semibold text-[#102b3f]">
              {t("geo.ios.draft.aiReviewTitle")}
            </h3>
            {!reviewableDraftId ? null : (
              <Button
                size="sm"
                className="bg-[#6d68e8] hover:bg-[#5b56d6] text-white text-sm"
                disabled={aiReviewRunning}
                onClick={runAiReview}
              >
                {aiReviewRunning && <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />}
                {/* 2026-09-24変更、ユーザー指摘「実行済みなのにボタンが常に『実行』の
                    ままで紛らわしい」への対応: 既にreviewが存在する（completed/failed）
                    場合はラベルを「再実行」に変える。 */}
                {review ? t("geo.ios.draft.rerunAiReview") : t("geo.ios.draft.runAiReview")}
              </Button>
            )}
          </div>

          {staleAfterEdit && (
            <p className="text-sm text-[#e9903d]">{t("geo.ios.draft.newVersionNeedsReview")}</p>
          )}
          {aiReviewRunning && <GeoExpertAiProgress active className="justify-center" />}
          {aiReviewV2Failed && (
            <p className="text-sm text-destructive">
              {reportReviewV2Query.data?.error_message || t("geo.ios.draft.aiReviewFailed")}
            </p>
          )}
          {!isReviewIntake && review?.status === "failed" && (
            <p className="text-sm text-destructive">
              {review.error_message || t("geo.ios.draft.aiReviewFailed")}
            </p>
          )}

          {isReviewIntake &&
            reportReviewV2Query.data?.status === "completed" &&
            reportReviewV2Query.data.report_reading &&
            reportReviewV2Query.data.comparison_summary && (
              <GeoReportReviewV2Comparison
                sessionId={sessionId}
                reportReading={reportReviewV2Query.data.report_reading}
                comparisonSummary={reportReviewV2Query.data.comparison_summary}
              />
            )}

          {isReviewIntake && reportReviewV2Query.data?.status === "completed" && (
            <GeoReportReviewV2Findings
              sessionId={sessionId}
              readingFindings={findings}
              contentFindings={reportReviewV2Query.data.content_findings ?? []}
              dispositions={reportReviewV2Query.data.human_expert_dispositions ?? {}}
              reviewerId={actorId}
            />
          )}

          {reviewCompleted && findings.length === 0 && (
            <p className="text-sm text-muted-foreground">{t("geo.ios.draft.noFindings")}</p>
          )}
          {reviewCompleted && findings.length > 0 && (
            <div className="space-y-2">
              {findings.map((f) => (
                <div
                  key={f.finding_id}
                  className="rounded-[10px] border border-[#d8e2e7] bg-white p-[13px] space-y-1.5"
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span
                        className={`px-2 py-0.5 rounded-full text-xs font-medium ${SEVERITY_CHIP[f.severity]}`}
                      >
                        {SEVERITY_LABEL[f.severity]}
                      </span>
                      {f.status === "waived" && (
                        <span className="px-2 py-0.5 rounded-full text-xs bg-muted text-muted-foreground">
                          {t("geo.ios.draft.waived")}
                        </span>
                      )}
                    </div>
                    {/* 2026-09-24変更、ユーザー指示「ボタン押下のみにして」:
                        理由入力ダイアログを廃止し、押したら即Waiveする（定型理由を
                        自動送信、WAIVE_DEFAULT_REASON参照）。 */}
                    {f.status === "open" && (
                      <Button
                        variant="outline"
                        size="sm"
                        className="text-sm shrink-0"
                        disabled={waivingFindingId === f.finding_id && waiveFinding.isPending}
                        onClick={() => waiveFindingOneClick(f.finding_id)}
                      >
                        {waivingFindingId === f.finding_id && waiveFinding.isPending && (
                          <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                        )}
                        {t("geo.ios.draft.waive")}
                      </Button>
                    )}
                  </div>
                  <p className="text-base geo-prose">{f.finding}</p>
                  {f.recommendation && (
                    <p className="text-sm text-muted-foreground">{f.recommendation}</p>
                  )}
                  {f.status === "waived" && f.waiver_reason && (
                    <p className="text-sm text-[#687b88] italic">
                      {t("geo.ios.draft.waiverReasonLabel")}: {f.waiver_reason}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
          {!review && <p className="geo-meta">{t("geo.ios.draft.aiReviewHint")}</p>}
          {aiReviewPassed && (
            <p className="text-sm text-[#2f9e76]">{t("geo.ios.draft.aiReviewPassed")}</p>
          )}
        </div>
      )}

      {draft?.status === "completed" && (
        <div className="rounded-[14px] border border-[#d8e2e7] bg-white p-[18px] flex items-center justify-between gap-3 flex-wrap">
          {/* 2026-09-24追加、ユーザー指摘「専門家レビュー画面へ移動できる ≠
              専門家レビューを正式に開始できる、という関係が利用者に分かる状態に
              しておいた方がいい」への対応: このボタンはReport Project作成＋画面
              遷移のみ行い、Backend Gate（check_expert_review_gate）自体はここでは
              呼ばない（実際のGateはReviewページの「Request Expert Review」実行時）。
              その区別を明示するキャプションを添える。 */}
          <p className="geo-meta max-w-[46ch]">{t("geo.ios.draft.proceedIsNavigationOnly")}</p>
          <Button
            className="bg-[#0d879a] hover:bg-[#096f80] text-white text-base"
            disabled={proceeding || createReport.isPending}
            onClick={proceedToExpertReview}
          >
            {(proceeding || createReport.isPending) && (
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
            )}
            {t("geo.ios.draft.proceedToExpertReview")}
          </Button>
        </div>
      )}
    </div>
  );
}
