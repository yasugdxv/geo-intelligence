import { useState } from "react";
import { Loader2, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { GeoCard, GeoCardHeader } from "@/components/geo/geo-card";
import { GeoExpertAiProgress } from "@/components/geo/geo-expert-ai-progress";
import {
  useConsultationTurns, useCreateConsultationTurn,
  type GeoIosConsultationMode, type GeoIosConsultationTurn,
} from "@/lib/geoIosConsultationApi";
import { useLanguage } from "@/lib/i18n";

// Geopolitical Intelligence OS: Consultation Turns（切り口相談/調査方針/プレレビュー）。
// 各モードは既存Expert AI mode（research_design/theme_design/draft_review）への
// 委譲であり、新しいLLM Schemaは追加しない。各ターンはDBに永続化されたstatus
// （pending/completed/failed）をポーリングするだけで、ページ離脱・再訪後も進捗が
// 追跡できる（ユーザー要件: フルパワーの標準実行を維持し、速度より継続性を優先する）。
//
// 「切り口相談」(framing/research_designモード)は「局面を読む」タブが既に同じ
// research_designの結果を7セクションで表示しているため、ここには置かない
// （2箇所で同じ内容が別デザインで出て読みづらい、というユーザー指摘への対応）。
// framingモードのチャットはGeoIosConsultationThreadとして切り出し、
// geo-ios-situation-analysis-view.tsx側（「局面を読む」の下）に直接埋め込む。
export const MODE_LABEL_KEY: Record<GeoIosConsultationMode, string> = {
  framing: "geo.ios.consultation.mode.framing",
  research_direction: "geo.ios.consultation.mode.researchDirection",
  pre_review: "geo.ios.consultation.mode.preReview",
};

function TurnBubbleUser({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[90%] rounded-xl rounded-br-sm bg-primary text-primary-foreground px-3.5 py-2.5 text-base leading-[1.7] whitespace-pre-wrap">
        {text}
      </div>
    </div>
  );
}

function TurnBubbleAssistant({ turn }: { turn: GeoIosConsultationTurn }) {
  const { t } = useLanguage();

  if (turn.status === "pending") {
    return <GeoExpertAiProgress active startedAt={turn.created_at} />;
  }
  if (turn.status === "failed") {
    return (
      <p className="text-sm text-destructive">
        {t("geo.ios.consultation.failed")}: {turn.error_message}
      </p>
    );
  }
  const answer = turn.answer;
  if (!answer) return null;
  return (
    <div className="max-w-[90%] text-base geo-prose space-y-2">
      {answer.one_line_assessment && <p>{answer.one_line_assessment}</p>}
      {answer.what_changed && (
        <p className="text-[#425968]">
          <span className="font-medium text-foreground">{t("geo.ios.consultation.whatChanged")}:</span>{" "}
          {answer.what_changed}
        </p>
      )}
      {answer.why_it_matters && (
        <p className="text-[#425968]">
          <span className="font-medium text-foreground">{t("geo.ios.consultation.whyItMatters")}:</span>{" "}
          {answer.why_it_matters}
        </p>
      )}
      {answer.what_to_watch_decide && (
        <p className="text-[#425968]">
          <span className="font-medium text-foreground">{t("geo.ios.consultation.whatToWatch")}:</span>{" "}
          {answer.what_to_watch_decide}
        </p>
      )}
      {answer.review_findings_count > 0 && (
        <p className="geo-meta">
          {t("geo.ios.consultation.reviewFindingsCount", { count: String(answer.review_findings_count) })}
        </p>
      )}
    </div>
  );
}

// 単一モード固定のチャットスレッド（モード切替UIは持たない）。「局面を読む」からの
// framing用埋め込み、および相談するタブ内の各モードから共通で使う。
export function GeoIosConsultationThread({
  sessionId, actorId, mode, draftText, className, height = "h-[420px]",
}: {
  sessionId: string; actorId: string; mode: GeoIosConsultationMode;
  draftText?: string | null; className?: string; height?: string;
}) {
  const { t } = useLanguage();
  const [input, setInput] = useState("");

  const turnsQuery = useConsultationTurns(sessionId, mode);
  const createTurn = useCreateConsultationTurn(sessionId);

  const turns = turnsQuery.data?.turns ?? [];
  const hasPending = turns.some((t2) => t2.status === "pending");

  const send = () => {
    const message = input.trim();
    if (!message || hasPending || createTurn.isPending) return;
    if (mode === "pre_review" && !(draftText ?? "").trim()) return;
    createTurn.mutate({
      consultation_mode: mode, user_message: message,
      draft_text: mode === "pre_review" ? draftText : null, created_by: actorId,
    });
    setInput("");
  };

  return (
    <GeoCard className={`flex flex-col ${height} ${className ?? ""}`}>
      <GeoCardHeader title={t(MODE_LABEL_KEY[mode])} />
      <div className="flex-1 overflow-y-auto px-5 py-3 space-y-3">
        {turns.map((turn) => (
          <div key={turn.consultation_turn_id} className="space-y-1.5">
            <TurnBubbleUser text={turn.user_message} />
            <TurnBubbleAssistant turn={turn} />
          </div>
        ))}
        {turns.length === 0 && (
          <p className="geo-meta">{t("geo.ios.consultation.hint")}</p>
        )}
      </div>
      <div className="border-t border-border p-3 flex items-end gap-2">
        <Textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
          }}
          placeholder={t("geo.ios.consultation.messagePlaceholder")}
          className="min-h-[36px] max-h-32 resize-none text-base md:text-base"
        />
        <Button size="icon" disabled={hasPending || createTurn.isPending} onClick={send}>
          {hasPending || createTurn.isPending
            ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
        </Button>
      </div>
    </GeoCard>
  );
}

// 「相談する」タブ本体。調査方針/レポートレビューの2モードを切り替える
// （切り口相談は「局面を読む」側に埋め込み済みのため、ここには含めない）。
const TAB_MODES: GeoIosConsultationMode[] = ["research_direction", "pre_review"];

export function GeoIosConsultationView({ sessionId, actorId }: { sessionId: string; actorId: string }) {
  const { t } = useLanguage();
  const [mode, setMode] = useState<GeoIosConsultationMode>("research_direction");
  const [draftInput, setDraftInput] = useState("");

  return (
    <div className="space-y-3">
      <div className="flex gap-1.5 flex-wrap">
        {TAB_MODES.map((m) => (
          <Button
            key={m} size="sm" variant={mode === m ? "default" : "outline"}
            onClick={() => setMode(m)}
          >
            {t(MODE_LABEL_KEY[m])}
          </Button>
        ))}
      </div>

      {mode === "pre_review" && (
        <Textarea
          value={draftInput}
          onChange={(e) => setDraftInput(e.target.value)}
          placeholder={t("geo.ios.consultation.draftPlaceholder")}
          className="min-h-[100px] text-base md:text-base"
        />
      )}

      <GeoIosConsultationThread
        key={mode}
        sessionId={sessionId} actorId={actorId} mode={mode} draftText={draftInput} height="h-[480px]"
      />
    </div>
  );
}
