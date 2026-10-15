import { Check } from "lucide-react";
import { cn } from "@/lib/utils";

// Geopolitical Intelligence OS Phase H §11: Report Studio Workflow Header。
// 既存State Machine（Phase D geo_ios_report_drafts.status/geo_ios_draft_ai_reviews.status、
// geo_ios_review_cycles.status/result、Phase E geo_ios_approval_events.approval_status/
// publication_status）から導出するだけで、UI独自のstatusは持たない（§11絶対条件）。
//
// 社内モックアップは「設計/Evidence/ドラフト/AIレビュー/専門家レビュー/承認・ストック」の
// 6タブだが、「Evidence」（参照資料アップロード〜Grounding Context投入）に対応する実装は
// 無く、UI独自のStatusを持たないという下の絶対条件に反するため、このタブは追加しない。
// 「専門家レビュー」の前段にある「AIレビュー」は、geo_ios_report_draft_service.
// check_expert_review_gate/is_ai_review_passedが実際に強制しているGateをそのまま可視化した
// もの（新設ではない）。旧「承認」「公開」は同じ承認イベント1行から導出される連続した
// フェーズのため、モックアップと同じく1タブ「承認・ストック」に統合する。
//
// 「前提整理」（intake）はユーザー指示（2026-09-24、Intake画面改修）で追加した先頭step。
// Analysis Session自体の存在（＝Session詳細ページを表示できている）だけで判定でき、
// 新しいDB State Machineを増やさない（Sessionが無ければそもそもこのstepを表示する
// 画面に来られないため、常にdone/current以外にならない——deriveWorkflowStateは
// 呼び出し元がSession detail context内でのみ使うことを前提とする）。旧来の「設計」は
// 「読み方提案」へ改称した（Scenario探索〜Analysis Design Workspaceの内容自体は無変更、
// ラベルのみ変更——hasDesign判定ロジックは従来のまま）。
export type GeoIosWorkflowStep =
  "intake" | "design" | "draft" | "ai_review" | "expert_review" | "approval";
export type GeoIosStepState = "done" | "current" | "pending";

const STEP_LABELS: Record<GeoIosWorkflowStep, string> = {
  intake: "前提整理",
  design: "読み方提案",
  draft: "ドラフト",
  ai_review: "AIレビュー",
  expert_review: "専門家レビュー",
  approval: "承認・ストック",
};
const STEP_ORDER: GeoIosWorkflowStep[] = [
  "intake",
  "design",
  "draft",
  "ai_review",
  "expert_review",
  "approval",
];

export function deriveWorkflowState(params: {
  hasDesign: boolean;
  hasReportProject: boolean;
  // IOS Native Draft: report_generation_modeが唯一のSource of Truth。省略時（既存呼び出し元
  // 未対応のまま）はreport_studio相当として振る舞い、既存の見た目を変えない。
  reportGenerationMode?: "native" | "report_studio";
  hasNativeDraft?: boolean;
  // AIレビューGateはreport_generation_mode==="native"の経路にのみ実在する（report_studio
  // 経路には対応するGateが無いため、そちらでは常に素通りにする＝存在しない状態を
  // 捏造しない）。
  hasAiReviewCompleted?: boolean;
  latestReviewResult?: "approved" | "revision_requested" | "rejected" | null;
  hasOpenReviewCycle: boolean;
  approvalStatus?: "approved" | "rejected" | null;
  publicationStatus?: "not_published" | "pending" | "published" | null;
}): Record<GeoIosWorkflowStep, GeoIosStepState> {
  const {
    hasDesign,
    hasReportProject,
    reportGenerationMode,
    hasNativeDraft,
    hasAiReviewCompleted,
    latestReviewResult,
    hasOpenReviewCycle,
    approvalStatus,
    publicationStatus,
  } = params;
  const state: Record<GeoIosWorkflowStep, GeoIosStepState> = {
    // このSessionのSession detail contextを表示できている時点でIntakeは完了している
    // （Session自体が無ければこの関数を呼ぶ画面に到達できない）。
    intake: "done",
    design: "pending",
    draft: "pending",
    ai_review: "pending",
    expert_review: "pending",
    approval: "pending",
  };
  state.design = hasDesign ? "done" : "current";
  if (!hasDesign) return state;
  // Native Draftが偶然存在するかどうかでは判定しない（geo_ios_workflow_board_service.
  // derive_current_stepと同じ分岐——変更時は両方揃えて直すこと）。
  const draftDone = reportGenerationMode === "native" ? !!hasNativeDraft : hasReportProject;
  state.draft = draftDone ? "done" : "current";
  if (!draftDone) return state;

  if (reportGenerationMode === "native") {
    state.ai_review = hasAiReviewCompleted ? "done" : "current";
    if (!hasAiReviewCompleted) return state;
  } else {
    // report_studio経路にはAIレビューGateという概念自体が無いため、素通り扱い
    // （「未着手のまま止まっている」ように見せない）。
    state.ai_review = "done";
  }

  if (hasOpenReviewCycle) {
    state.expert_review = "current";
    return state;
  }
  state.expert_review = latestReviewResult ? "done" : "current";
  if (!latestReviewResult) return state;
  // 承認・ストック: 旧approval/publishedの2フェーズを1タブに統合。
  state.approval = publicationStatus === "published" ? "done" : "current";
  return state;
}

export function GeoIosWorkflowHeader({
  state,
  onStepClick,
  steps,
}: {
  state: Record<GeoIosWorkflowStep, GeoIosStepState>;
  // 到達済み(done/current)のstepだけクリック可能にする——まだ到達していないstepへ
  // 先読みで飛べてしまうと、実State Machineと矛盾するUI独自の遷移を許すことになる
  // （§11絶対条件）。onStepClick省略時はIntake画面(Session未作成)のように従来通り
  // 非インタラクティブなまま。
  onStepClick?: (step: GeoIosWorkflowStep) => void;
  // Phase 2B（IA再編）: 呼び出し元が「表示するstepの部分集合」を指定できるようにする
  // （例: Expert AI相談画面はintake/designの2stepのみ見せ、Draft以降はレビューフロー側
  // に任せる）。省略時は既存挙動のまま全6step表示（既存の3呼び出し元は無変更で動く）。
  // state自体はderiveWorkflowStateの実データそのままで、UI側で新しいstatusは作らない
  // （§11絶対条件は維持——ここでは「どのstepを描画するか」を絞るだけ）。
  steps?: GeoIosWorkflowStep[];
}) {
  const visibleSteps = steps ?? STEP_ORDER;
  return (
    <div className="flex items-stretch gap-2 overflow-x-auto">
      {visibleSteps.map((step) => {
        const clickable = !!onStepClick && state[step] !== "pending";
        return (
          <button
            key={step}
            type="button"
            disabled={!clickable}
            onClick={clickable ? () => onStepClick!(step) : undefined}
            className={cn(
              "flex flex-1 min-w-[120px] items-center justify-center gap-1.5 rounded-[10px] px-3 py-2.5 text-sm font-medium whitespace-nowrap transition-colors",
              state[step] === "current" && "bg-[#0b2a3d] text-white",
              state[step] === "done" && "bg-[#eaf8fb] text-[#0d879a] border border-[#c9ecf1]",
              state[step] === "pending" && "bg-white text-[#687b88] border border-[#d8e2e7]",
              clickable ? "cursor-pointer hover:opacity-80" : "cursor-default",
            )}
          >
            {state[step] === "done" && <Check className="h-3.5 w-3.5 shrink-0" />}
            <span>
              {STEP_ORDER.indexOf(step) + 1}&nbsp;{STEP_LABELS[step]}
            </span>
          </button>
        );
      })}
    </div>
  );
}
