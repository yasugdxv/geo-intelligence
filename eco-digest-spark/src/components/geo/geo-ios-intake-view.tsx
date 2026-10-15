import { useEffect, useRef, useState } from "react";
import { Loader2, Paperclip, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { GeoCard, GeoCardHeader, GeoCardBody } from "@/components/geo/geo-card";
import {
  useIntakeTurns,
  useSendIntakeMessage,
  useIntakeContext,
  useIntakeAttachments,
  useUploadIntakeAttachment,
  type GeoIosAnalysisContext,
  type GeoIosKeyActorDetail,
} from "@/lib/geoIosIntakeApi";
import { useLanguage } from "@/lib/i18n";

// Geopolitical Intelligence OS: 前提整理（Intake）画面（ユーザー指示、2026-09-24、
// 「1 設計」ページの改修）。担当者とAIが数回のQ&Aで、専門家AIへ渡すAnalysis Contextを
// 揃える。Expert AI本体（geo_expert_engine.py）・CQASロジックには一切触れず、
// geo_ios_intake_service（軽量な会話用LLM呼び出し）の結果を表示するだけ。

const SCALAR_FIELDS: Array<{ key: keyof GeoIosAnalysisContext; i18nKey: string }> = [
  { key: "analysis_objective", i18nKey: "geo.ios.intake.fields.analysisObjective" },
  { key: "core_question", i18nKey: "geo.ios.intake.fields.coreQuestion" },
  { key: "current_situation", i18nKey: "geo.ios.intake.fields.currentSituation" },
  { key: "business_context", i18nKey: "geo.ios.intake.fields.businessContext" },
  { key: "time_horizon", i18nKey: "geo.ios.intake.fields.timeHorizon" },
  { key: "geographic_scope", i18nKey: "geo.ios.intake.fields.geographicScope" },
  { key: "internal_context", i18nKey: "geo.ios.intake.fields.internalContext" },
];

const LIST_FIELDS: Array<{ key: keyof GeoIosAnalysisContext; i18nKey: string }> = [
  { key: "relevant_actors", i18nKey: "geo.ios.intake.fields.relevantActors" },
  { key: "known_events", i18nKey: "geo.ios.intake.fields.knownEvents" },
  // key_actors_detailはオブジェクト配列（string[]ではない）なので、専用の
  // レンダリング・組み立てロジックで別扱いする（下記参照）。ここには含めない。
  { key: "user_assumptions", i18nKey: "geo.ios.intake.fields.userAssumptions" },
  { key: "must_consider_topics", i18nKey: "geo.ios.intake.fields.mustConsiderTopics" },
  { key: "reference_materials", i18nKey: "geo.ios.intake.fields.referenceMaterials" },
  { key: "constraints", i18nKey: "geo.ios.intake.fields.constraints" },
  { key: "information_gaps", i18nKey: "geo.ios.intake.fields.informationGaps" },
];

// key_actors_detail 1件を、専門家AIへのtheme_override・画面表示の両方で使う
// 「name(role): controls_or_leverage / key_relationships / constraints」形式へ整形する。
// 空のsub-fieldは省略する（無い情報を捏造しない）。
function formatKeyActorDetail(actor: GeoIosKeyActorDetail): string {
  const parts: string[] = [actor.role ? `${actor.name}(${actor.role})` : actor.name];
  const sub = [
    actor.controls_or_leverage && `動かせるもの: ${actor.controls_or_leverage}`,
    actor.key_relationships && `重要な関係: ${actor.key_relationships}`,
    actor.constraints && `制約: ${actor.constraints}`,
    actor.notable_actions && `行動: ${actor.notable_actions}`,
  ].filter(Boolean);
  if (sub.length > 0) parts.push(sub.join(" / "));
  return parts.join(" — ");
}

// CTAで専門家AIへ渡すtheme_overrideの組み立て。既存generate-scenariosのtheme_override
// パラメータをそのまま使うだけで、CQAS/Expert AI呼び出し経路自体は無変更。
export function composeThemeOverride(
  originalTheme: string,
  context: GeoIosAnalysisContext | undefined,
): string {
  if (!context) return originalTheme;
  const lines: string[] = [`分析したいこと: ${originalTheme}`];
  for (const { key } of SCALAR_FIELDS) {
    const value = context[key];
    if (typeof value === "string" && value.trim()) lines.push(`${key}: ${value}`);
  }
  for (const { key } of LIST_FIELDS) {
    const value = context[key];
    if (Array.isArray(value) && value.length > 0) lines.push(`${key}: ${value.join("、")}`);
  }
  // key_actors_detail: ユーザー指示（2026-09-24、Step2品質検証の結果を受けた改善①）。
  // 個人名・役職・レバレッジ・関係性・制約まで専門家AIへのtheme_overrideに含める
  // ——ここが薄いままだと、CQASが機関レベルの主体だけで読みを組み立ててしまう
  // （Gold Case Blind Testで確認済みのギャップ）。
  if (context.key_actors_detail && context.key_actors_detail.length > 0) {
    lines.push("key_actors_detail:");
    for (const actor of context.key_actors_detail) {
      lines.push(`- ${formatKeyActorDetail(actor)}`);
    }
  }
  return lines.join("\n");
}

function ContextSummary({ context }: { context: GeoIosAnalysisContext | undefined }) {
  const { t } = useLanguage();
  const hasAny =
    !!context &&
    (SCALAR_FIELDS.some(({ key }) => !!context[key]) ||
      LIST_FIELDS.some(
        ({ key }) => Array.isArray(context[key]) && (context[key] as string[]).length > 0,
      ) ||
      (context.key_actors_detail && context.key_actors_detail.length > 0));
  if (!hasAny) {
    return (
      <p className="geo-meta italic">{t("geo.ios.intake.summaryEmpty")}</p>
    );
  }
  return (
    <div className="space-y-4">
      {SCALAR_FIELDS.map(({ key, i18nKey }) => {
        const value = context?.[key];
        if (!value || typeof value !== "string") return null;
        return (
          <div key={key}>
            <div className="geo-meta uppercase tracking-[0.1em] mb-1">
              {t(i18nKey)}
            </div>
            <p className="text-base geo-prose">{value}</p>
          </div>
        );
      })}
      {LIST_FIELDS.map(({ key, i18nKey }) => {
        const value = context?.[key];
        if (!Array.isArray(value) || value.length === 0) return null;
        return (
          <div key={key}>
            <div className="geo-meta uppercase tracking-[0.1em] mb-1">
              {t(i18nKey)}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {value.map((v, i) => (
                <Badge key={i} variant="outline">
                  {String(v)}
                </Badge>
              ))}
            </div>
          </div>
        );
      })}
      {context?.key_actors_detail && context.key_actors_detail.length > 0 && (
        <div>
          <div className="geo-meta uppercase tracking-[0.1em] mb-1">
            {t("geo.ios.intake.fields.keyActorsDetail")}
          </div>
          <ul className="space-y-1.5">
            {context.key_actors_detail.map((actor, i) => (
              <li key={i} className="text-base geo-prose">
                {formatKeyActorDetail(actor)}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export function GeoIosIntakeView({
  sessionId,
  actorId,
  sessionTitle,
  sessionTheme,
  onHandoff,
}: {
  sessionId: string;
  actorId: string;
  sessionTitle: string;
  sessionTheme?: string | null;
  onHandoff: (themeOverride: string) => void;
}) {
  const { t } = useLanguage();
  const [input, setInput] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const autoSentRef = useRef(false);

  const turnsQuery = useIntakeTurns(sessionId);
  const contextQuery = useIntakeContext(sessionId);
  const attachmentsQuery = useIntakeAttachments(sessionId);
  const sendMessage = useSendIntakeMessage(sessionId);
  const uploadAttachment = useUploadIntakeAttachment(sessionId);

  const turns = turnsQuery.data?.turns ?? [];
  const context = contextQuery.data;
  const attachments = attachmentsQuery.data?.attachments ?? [];

  // 最初のフォーム入力（session.theme）を起点に、Session到着時点でまだ会話が
  // 無ければ自動で最初の1往復を送る（担当者が同じ内容を二度打たなくて済むように）。
  useEffect(() => {
    if (autoSentRef.current) return;
    if (!turnsQuery.isSuccess) return;
    if (turns.length > 0) {
      autoSentRef.current = true;
      return;
    }
    if (!sessionTheme || !sessionTheme.trim()) return;
    autoSentRef.current = true;
    sendMessage.mutate({ user_message: sessionTheme, actor_id: actorId });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [turnsQuery.isSuccess, turns.length, sessionTheme]);

  const send = () => {
    const message = input.trim();
    if (!message || sendMessage.isPending) return;
    sendMessage.mutate({ user_message: message, actor_id: actorId });
    setInput("");
  };

  const handleFilesPicked = (files: FileList | null) => {
    if (!files || files.length === 0) return;
    Array.from(files).forEach((file) => uploadAttachment.mutate({ file, actorId }));
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const busy = sendMessage.isPending || (turnsQuery.isLoading && turns.length === 0);

  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_320px] items-start">
      {/* 左・中央: 前提整理チャット */}
      <GeoCard className="flex flex-col h-[560px]">
        <GeoCardHeader title={t("geo.ios.intake.chatTitle")} />
        <div className="flex-1 overflow-y-auto px-5 py-3 space-y-3">
          {turns.map((turn) => (
            <div key={turn.intake_turn_id} className="space-y-2">
              <div className="flex justify-end">
                <div className="max-w-[85%] rounded-xl rounded-br-sm bg-primary text-primary-foreground px-3.5 py-2.5 text-base leading-[1.7] whitespace-pre-wrap">
                  {turn.user_message}
                </div>
              </div>
              <div className="max-w-[85%] text-base geo-prose whitespace-pre-wrap">
                {turn.assistant_message}
              </div>
            </div>
          ))}
          {turns.length === 0 && !busy && (
            <p className="geo-meta">{t("geo.ios.intake.hint")}</p>
          )}
          {sendMessage.isPending && (
            <div className="flex items-center gap-2 geo-meta">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            </div>
          )}
          {sendMessage.isError && (
            <p className="text-sm text-destructive">{(sendMessage.error as Error).message}</p>
          )}
        </div>
        <div className="border-t border-border p-3 space-y-2">
          <div className="flex items-center gap-2">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              hidden
              onChange={(e) => handleFilesPicked(e.target.files)}
            />
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={uploadAttachment.isPending}
              onClick={() => fileInputRef.current?.click()}
            >
              {uploadAttachment.isPending ? (
                <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
              ) : (
                <Paperclip className="h-3.5 w-3.5 mr-1.5" />
              )}
              {uploadAttachment.isPending
                ? t("geo.ios.intake.uploading")
                : t("geo.ios.intake.addFiles")}
            </Button>
          </div>
          {uploadAttachment.isError && (
            <p className="text-sm text-destructive">{(uploadAttachment.error as Error).message}</p>
          )}
          <div className="flex items-end gap-2">
            <Textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
              placeholder={t("geo.ios.intake.messagePlaceholder")}
              className="min-h-[36px] max-h-32 resize-none text-base md:text-base"
            />
            <Button size="icon" disabled={sendMessage.isPending || !input.trim()} onClick={send}>
              {sendMessage.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Send className="h-4 w-4" />
              )}
            </Button>
          </div>
        </div>
      </GeoCard>

      {/* 右: AIが整理した分析前提 / 添付資料 / 準備状況 / CTA */}
      <div className="space-y-4">
        <GeoCard>
          <GeoCardHeader title={t("geo.ios.intake.summaryTitle")} />
          <GeoCardBody>
            <ContextSummary context={context} />
          </GeoCardBody>
        </GeoCard>

        <GeoCard>
          <GeoCardHeader
            title={t("geo.ios.intake.attachmentsTitle")}
            meta={`${attachments.length}`}
          />
          <GeoCardBody className="space-y-2">
            {attachments.length === 0 && (
              <p className="geo-meta italic">
                {t("geo.ios.intake.attachmentsEmpty")}
              </p>
            )}
            {attachments.map((a) => (
              <div
                key={a.attachment_id}
                className="flex items-center justify-between gap-2 text-base"
              >
                <span className="truncate">{a.filename}</span>
                {a.extraction_status === "unsupported" && (
                  <Badge variant="outline" className="text-xs shrink-0">
                    {t("geo.ios.intake.extractionUnsupported")}
                  </Badge>
                )}
                {a.extraction_status === "failed" && (
                  <Badge variant="outline" className="text-xs shrink-0 text-destructive">
                    {t("geo.ios.intake.extractionFailed")}
                  </Badge>
                )}
              </div>
            ))}
          </GeoCardBody>
        </GeoCard>

        <GeoCard>
          <GeoCardHeader title={t("geo.ios.intake.readinessTitle")} />
          <GeoCardBody className="space-y-3">
            <p className="text-base geo-prose">
              {context?.ready_for_handoff
                ? t("geo.ios.intake.readinessReady")
                : t("geo.ios.intake.readinessNotReady")}
            </p>
            <Button
              className="w-full bg-[#36bfd6] hover:bg-[#2ea9bf] text-white text-base"
              onClick={() => onHandoff(composeThemeOverride(sessionTheme || sessionTitle, context))}
            >
              {t("geo.ios.intake.ctaButton")}
            </Button>
            <p className="geo-meta">{t("geo.ios.intake.ctaHint")}</p>
          </GeoCardBody>
        </GeoCard>
      </div>
    </div>
  );
}
