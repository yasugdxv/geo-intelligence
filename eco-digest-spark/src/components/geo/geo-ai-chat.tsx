import { useEffect, useRef, useState } from "react";
import { Link } from "@tanstack/react-router";
import { Sparkles, Send, X, Maximize2, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useGeoChatSend, type ChatMsg } from "@/lib/api";
import { useLanguage } from "@/lib/i18n";

// 地政学専門家AI（MVP軽量版）のチャットパネル。既存のAiChatPanel(サスティナAI、
// ai-chat.tsx)とは構造は似せているが独立した実装で、既存ファイルは一切変更しない。
// 指示書14章のフル7モード・15段階Reasoning Pipelineではなく、単一モードの会話窓口。
export function GeoAiChatPanel({
  compact = false,
  onClose,
  messages: controlledMessages,
  onMessagesChange,
  themeId,
  interceptSend,
  renderMessage,
  afterMessages,
  externalBusyLabel,
}: {
  compact?: boolean;
  onClose?: () => void;
  messages?: ChatMsg[];
  onMessagesChange?: (messages: ChatMsg[]) => void;
  themeId?: string;
  // Intelligence AI プリセット用の拡張点（未指定なら従来どおり。ポップアップ等の他の利用箇所には影響しない）:
  // interceptSend: trueを返すと、この送信は呼び出し側（プリセットの条件確認・追加対話）が処理し、通常のチャット送信はしない
  interceptSend?: (text: string, history: ChatMsg[]) => boolean;
  // renderMessage: nullでなければ、そのassistantメッセージの本文表示を置き換える（フォーム・調査結果カード等）
  renderMessage?: (message: ChatMsg, index: number) => React.ReactNode | null;
  afterMessages?: React.ReactNode;
  // 呼び出し側が処理中のとき、その説明（スピナーと一緒に表示し、送信ボタンを止める）
  externalBusyLabel?: string | null;
}) {
  const { lang, t } = useLanguage();
  const [internalMessages, setInternalMessages] = useState<ChatMsg[]>(() => [
    { role: "assistant", content: t("geo.chat.seedMessage") },
  ]);
  const messages = controlledMessages ?? internalMessages;
  // 新しいメッセージ（プリセットを選び直したときの入力フォームを含む）が増えたら、画面内に表示する。
  // これが無いと、選び直した新しいフォームが画面の下に追加されるだけで、「選んでも何も起きない」ように見えた。
  const lastRef = useRef<HTMLDivElement>(null);
  const skipFirstScroll = useRef(true);
  useEffect(() => {
    if (skipFirstScroll.current) {
      skipFirstScroll.current = false; // 開いた直後は、画面を動かさない
      return;
    }
    lastRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages.length]);
  const [input, setInput] = useState("");
  const chatSend = useGeoChatSend();

  const updateMessages = (next: ChatMsg[]) => {
    if (onMessagesChange) onMessagesChange(next);
    else setInternalMessages(next);
  };

  const send = (text: string) => {
    if (!text.trim() || chatSend.isPending || externalBusyLabel) return;
    const history = messages;
    if (interceptSend?.(text, history)) {
      setInput("");
      return;
    }
    updateMessages([...history, { role: "user", content: text }]);
    setInput("");

    chatSend.mutate(
      { message: text, history, themeId, lang },
      {
        onSuccess: (data) => {
          updateMessages([
            ...history,
            { role: "user", content: text },
            {
              role: "assistant",
              content: data.reply,
              sources: data.sources,
              sufficiency: data.sufficiency,
              expertAiCalled: data.expert_ai_called,
              intelligenceNeedCreated: data.intelligence_need_created,
              intelligenceNeedId: data.intelligence_need_id,
            },
          ]);
        },
        onError: (err: Error) => {
          updateMessages([
            ...history,
            { role: "user", content: text },
            {
              role: "assistant",
              content: `${lang === "en" ? "An error occurred" : "エラーが発生しました"}: ${err.message}`,
            },
          ]);
        },
      },
    );
  };

  return (
    <div className="flex h-full flex-col bg-card">
      <header className="flex items-center justify-between border-b border-border px-5 py-3.5">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <Sparkles className="h-4 w-4" />
          </div>
          <div>
            <div className="text-editorial text-lg leading-none">{t("geo.chat.title")}</div>
            <div className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground mt-1">
              {t("geo.chat.headerSubtitle")}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1">
          {compact && (
            <Link to="/geo/intelligence-ai">
              <Button variant="ghost" size="icon" className="h-8 w-8">
                <Maximize2 className="h-4 w-4" />
              </Button>
            </Link>
          )}
          {onClose && (
            <Button variant="ghost" size="icon" onClick={onClose} className="h-8 w-8">
              <X className="h-4 w-4" />
            </Button>
          )}
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-5 py-5 space-y-5">
        {messages.map((m, i) => (
          <div
            key={i}
            ref={i === messages.length - 1 ? lastRef : undefined}
            className={m.role === "user" ? "flex justify-end" : ""}
          >
            {m.role === "user" ? (
              <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary text-primary-foreground px-4 py-2.5 text-sm">
                {m.content}
              </div>
            ) : renderMessage?.(m, i) != null ? (
              <div className="max-w-[92%]">{renderMessage(m, i)}</div>
            ) : (
              <div className="max-w-[92%]">
                <div className="text-sm leading-relaxed whitespace-pre-wrap text-foreground">
                  {m.content}
                </div>
                {m.sources && m.sources.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {m.sources.map((s) => (
                      <span
                        key={s}
                        className="text-[11px] px-2 py-0.5 rounded-full border border-border bg-muted/60 text-muted-foreground"
                      >
                        {s}
                      </span>
                    ))}
                  </div>
                )}
                {(m.sufficiency || m.expertAiCalled || m.intelligenceNeedCreated) && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {m.sufficiency && (
                      <span
                        className={
                          "text-[10px] px-2 py-0.5 rounded-full border " +
                          (m.sufficiency === "sufficient"
                            ? "border-emerald-600/40 bg-emerald-600/10 text-emerald-700 dark:text-emerald-400"
                            : m.sufficiency === "partial"
                              ? "border-amber-600/40 bg-amber-600/10 text-amber-700 dark:text-amber-400"
                              : "border-destructive/40 bg-destructive/10 text-destructive")
                        }
                      >
                        {t("geo.chat.sufficiency")}: {m.sufficiency}
                      </span>
                    )}
                    {m.expertAiCalled && (
                      <span className="text-[10px] px-2 py-0.5 rounded-full border border-primary/40 bg-primary/10 text-primary">
                        {t("geo.chat.expertAiCalled")}
                      </span>
                    )}
                    {m.intelligenceNeedCreated && (
                      m.intelligenceNeedId ? (
                        <Link
                          to="/geo/intelligence-needs/$id"
                          params={{ id: m.intelligenceNeedId }}
                          className="text-[10px] px-2 py-0.5 rounded-full border border-border bg-muted/60 text-muted-foreground hover:bg-muted"
                        >
                          {t("geo.chat.needCreated")}
                        </Link>
                      ) : (
                        <span className="text-[10px] px-2 py-0.5 rounded-full border border-border bg-muted/60 text-muted-foreground">
                          {t("geo.chat.needCreated")}
                        </span>
                      )
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
        {afterMessages}
        {(chatSend.isPending || externalBusyLabel) && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> {externalBusyLabel ?? t("chat.thinking")}
          </div>
        )}
      </div>

      <footer className="border-t border-border p-3">
        <div className="relative flex items-end gap-2 rounded-xl border border-input bg-background px-3 py-2 focus-within:ring-2 focus-within:ring-ring/40">
          <Textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(input);
              }
            }}
            placeholder={t("geo.chat.inputPlaceholder")}
            className="min-h-[36px] max-h-40 border-0 shadow-none focus-visible:ring-0 resize-none px-0 py-1 text-sm"
          />
          <Button
            size="icon"
            onClick={() => send(input)}
            disabled={chatSend.isPending || !!externalBusyLabel}
            className="h-8 w-8 shrink-0 rounded-lg"
          >
            <Send className="h-4 w-4" />
          </Button>
        </div>
        <p className="text-[10px] text-muted-foreground text-center mt-1.5">
          {t("geo.chat.disclaimer")}
        </p>
      </footer>
    </div>
  );
}

// フローティングアイコンは本格ワークスペース(/geo/expert)への入り口とする（サイドバーの
// 「地政学専門家AI」と機能が重複するため、サイドバー側は削除しapp-sidebar.tsx参照）。
// 軽量な会話チャット(GeoAiChatPanel/geo.chat.tsx)はポップアップとしては提供せず、
// URLを直接開けば引き続き使える状態のまま残す。
export function GeoAiChatFab() {
  const { t } = useLanguage();
  return (
    <Link
      to="/geo/expert"
      className="fixed bottom-6 right-6 z-40 h-14 w-14 rounded-full bg-primary text-primary-foreground shadow-lg shadow-primary/30 hover:scale-105 active:scale-95 transition-transform flex items-center justify-center group"
      aria-label={t("nav.geoExpertAI")}
    >
      <Sparkles className="h-6 w-6" />
      <span className="absolute right-full mr-3 whitespace-nowrap text-xs bg-foreground text-background px-2.5 py-1 rounded opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none">
        {t("nav.geoExpertAI")}
      </span>
    </Link>
  );
}
