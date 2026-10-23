// Intelligence AI の Chat タブ（会話履歴 + プリセットから始める + チャット）。
// 通常のチャット（/api/geo/chat）は従来どおり。プリセットを選んだ会話だけ、条件確認 → 調査 → 追加対話を
// /api/geo/intelligence-ai/presets/* で行う（調査後の自由入力は、直前の調査結果を踏まえた追加対話になる）。
import { useEffect, useRef, useState } from "react";
import { Menu, MessageSquare, Plus, Trash2 } from "lucide-react";
import { GeoAiChatPanel } from "@/components/geo/geo-ai-chat";
import {
  PresetFollowupCard,
  PresetFormCard,
  PresetList,
  PresetResultCard,
  defaultConditions,
} from "@/components/geo/intelligence-presets";
import { useLanguage } from "@/lib/i18n";
import { isConversationModeRecord } from "@/lib/geoConversationModeApi";
import type { ChatMsg } from "@/lib/api";
import {
  deleteGeoConversation,
  listGeoConversations,
  makeGeoConversationId,
  saveGeoConversation,
  titleFromGeoMessages,
  type GeoChatConversation,
} from "@/lib/geoChatHistory";
import {
  deleteServerConversation,
  fetchServerConversations,
  saveServerConversation,
  useExtractPresetConditions,
  useAskCrossSource,
  usePresetFollowup,
  usePresets,
  useRunPreset,
  type PresetConditions,
  type PresetContext,
  type PresetDefinition,
  type PresetId,
} from "@/lib/geoIntelligencePresetApi";

interface PresetState {
  presetId: PresetId | null;
  context: PresetContext | null;
}

const NO_PRESET: PresetState = { presetId: null, context: null };

export function GeoIntelligenceChatTab() {
  const { t } = useLanguage();
  const presetsQuery = usePresets();
  const presets = presetsQuery.data?.presets ?? [];
  const extract = useExtractPresetConditions();
  const run = useRunPreset();
  const followup = usePresetFollowup();
  const ask = useAskCrossSource();
  // 自由質問のソース横断（サーバーの機能フラグが有効なとき）。Web検索は、画面の同意チェックがあるときだけ使う
  const crossEnabled = !!presetsQuery.data?.cross_source_enabled;
  const [askWeb, setAskWeb] = useState(false);

  const [conversations, setConversations] = useState<GeoChatConversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMsg[]>(() => [{ role: "assistant", content: t("geo.chat.seedMessage") }]);
  const [preset, setPreset] = useState<PresetState>(NO_PRESET);
  const [busy, setBusy] = useState<string | null>(null);
  // 小さい画面では、左のプリセット・履歴を折りたたむ（md以上では常に表示）
  const [asideOpen, setAsideOpen] = useState(false);
  const syncTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  // コールバック（非同期の完了時）から最新値を読むためのref
  const messagesRef = useRef(messages);
  const presetRef = useRef<PresetState>(NO_PRESET);
  const activeConvRef = useRef<{ id: string } | null>(null);
  const draftRef = useRef<PresetConditions | null>(null); // 入力中（未確定）のフォーム内容
  // 「新しい会話」「別の会話を開く」の後に、前の会話の処理結果が戻ってきても画面へ反映しないための世代番号
  const epochRef = useRef(0);

  useEffect(() => {
    setConversations(listGeoConversations());
    // ログイン中は、サーバーに保存された会話（別のパソコンで始めたもの等）を統合する。新しい方を採用する。
    // 未ログイン・テーブル未作成・通信失敗のときは空が返り、ブラウザ内の履歴だけで動く。
    let cancelled = false;
    fetchServerConversations().then((all) => {
      // 会話モードの会話は通常チャットの履歴に混ぜない（専用の履歴で扱う）
      const remote = all.filter((r) => !isConversationModeRecord(r));
      if (cancelled || remote.length === 0) return;
      const local = new Map(listGeoConversations().map((c) => [c.id, c]));
      remote.forEach((r) => {
        const l = local.get(r.id);
        if (!l || +new Date(r.updatedAt) > +new Date(l.updatedAt)) {
          saveGeoConversation({
            id: r.id,
            title: r.title,
            messages: r.messages as ChatMsg[],
            updatedAt: r.updatedAt,
            preset: (r.preset as GeoChatConversation["preset"]) ?? undefined,
          });
        }
      });
      // サーバーに無い（またはブラウザの方が新しい）会話は、サーバーへも保存する
      const remoteById = new Map(remote.map((r) => [r.id, r]));
      listGeoConversations().forEach((l) => {
        const r = remoteById.get(l.id);
        if (!r || +new Date(l.updatedAt) > +new Date(r.updatedAt)) {
          void saveServerConversation({ id: l.id, title: l.title, messages: l.messages, preset: l.preset ?? null, updatedAt: l.updatedAt });
        }
      });
      setConversations(listGeoConversations());
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const commit = (next: ChatMsg[], nextPreset?: PresetState) => {
    if (nextPreset) {
      presetRef.current = nextPreset;
      setPreset(nextPreset);
    }
    messagesRef.current = next;
    setMessages(next);
    if (!next.some((m) => m.role === "user")) return;
    if (!activeConvRef.current) activeConvRef.current = { id: makeGeoConversationId() };
    const { id } = activeConvRef.current;
    const conv: GeoChatConversation = {
      id,
      title: titleFromGeoMessages(next),
      messages: next,
      updatedAt: new Date().toISOString(),
      preset: presetRef.current.presetId || presetRef.current.context ? presetRef.current : undefined,
    };
    saveGeoConversation(conv);
    // サーバーへの保存は、入力のたびに送らず1秒まとめる（ログイン中のみ。失敗してもブラウザ内には保存済み）
    clearTimeout(syncTimers.current[id]);
    syncTimers.current[id] = setTimeout(() => {
      void saveServerConversation({ id, title: conv.title, messages: conv.messages, preset: conv.preset ?? null, updatedAt: conv.updatedAt });
    }, 1000);
    setConversations(listGeoConversations());
    setActiveId(id);
  };

  const reset = (nextMessages: ChatMsg[], nextPreset: PresetState) => {
    epochRef.current += 1;
    draftRef.current = null;
    setBusy(null);
    presetRef.current = nextPreset;
    setPreset(nextPreset);
    messagesRef.current = nextMessages;
    setMessages(nextMessages);
  };

  const startNew = () => {
    activeConvRef.current = null;
    setActiveId(null);
    reset([{ role: "assistant", content: t("geo.chat.seedMessage") }], NO_PRESET);
  };

  const openConversation = (conv: GeoChatConversation) => {
    activeConvRef.current = { id: conv.id };
    setActiveId(conv.id);
    setAsideOpen(false);
    // 調査中のまま閉じた会話は「実行中」の表示が残るため、入力できる状態に戻す
    const restored = conv.messages.map((m) =>
      m.presetForm?.status === "running" ? { ...m, presetForm: { ...m.presetForm, status: "editing" as const } } : m,
    );
    reset(restored, conv.preset ?? NO_PRESET);
  };

  const removeConversation = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    deleteGeoConversation(id);
    clearTimeout(syncTimers.current[id]);
    void deleteServerConversation(id);
    setConversations(listGeoConversations());
    if (activeId === id) startNew();
  };

  // ---- プリセット選択 ----
  const selectPreset = (def: PresetDefinition) => {
    if (busy) return;
    setAsideOpen(false);
    const cur = messagesRef.current.map((m) =>
      m.presetForm?.status === "editing" ? { ...m, presetForm: { ...m.presetForm, status: "superseded" as const } } : m,
    );
    draftRef.current = null;
    const form: ChatMsg = {
      role: "assistant",
      content: def.intro,
      presetForm: { presetId: def.id, conditions: defaultConditions(def), version: 0, status: "editing" },
    };
    commit([...cur, form], { presetId: def.id, context: null }); // 別のプリセットへ切り替えたら、前の調査の文脈は使わない
  };

  const editingFormIndex = () => {
    const list = messagesRef.current;
    for (let i = list.length - 1; i >= 0; i--) if (list[i].presetForm?.status === "editing") return i;
    return -1;
  };

  // ---- 自由入力（プリセット中の条件確認 / 調査後の追加対話）----
  const interceptSend = (text: string, history: ChatMsg[]): boolean => {
    const st = presetRef.current;
    const epoch = epochRef.current;
    const userMsg: ChatMsg = { role: "user", content: text };

    const formIdx = editingFormIndex();
    if (st.presetId && formIdx >= 0) {
      const form = messagesRef.current[formIdx].presetForm!;
      commit([...history, userMsg]);
      setBusy(t("chat.thinking"));
      extract.mutate(
        { preset_id: st.presetId, message: text, conditions: { ...form.conditions, ...(draftRef.current ?? {}) } },
        {
          onSuccess: (res) => {
            if (epoch !== epochRef.current) return;
            const idx = editingFormIndex();
            const next = messagesRef.current.map((m, i) =>
              i === idx && m.presetForm
                ? { ...m, presetForm: { ...m.presetForm, conditions: res.conditions, version: m.presetForm.version + 1 } }
                : m,
            );
            draftRef.current = null;
            commit([...next, { role: "assistant", content: res.assistant_message }]);
            setBusy(null);
          },
          onError: (err: Error) => {
            if (epoch !== epochRef.current) return;
            commit([...messagesRef.current, { role: "assistant", content: t("geo.ia.preset.extractError", { message: err.message }) }]);
            setBusy(null);
          },
        },
      );
      return true;
    }

    if (st.context) {
      commit([...history, userMsg]);
      setBusy(t("geo.ia.preset.followupThinking"));
      followup.mutate(
        { message: text, context: st.context, history: history.map(({ role, content }) => ({ role, content })) },
        {
          onSuccess: (res) => {
            if (epoch !== epochRef.current) return;
            commit(
              [...messagesRef.current, { role: "assistant", content: res.answer, presetFollowup: res }],
              { presetId: null, context: res.context },
            );
            setBusy(null);
          },
          onError: (err: Error) => {
            if (epoch !== epochRef.current) return;
            commit([...messagesRef.current, { role: "assistant", content: t("geo.ia.preset.followupError", { message: err.message }) }]);
            setBusy(null);
          },
        },
      );
      return true;
    }
    if (crossEnabled) {
      // プリセットを使わない自由質問: 使えるすべてのソースへ横断で投げる。結果は、プリセットの調査結果と同じ形で出し、
      // 続けて入力すると、その結果を踏まえた追加対話になる
      commit([...history, userMsg]);
      setBusy(t("geo.ia.cross.thinking"));
      ask.mutate(
        {
          message: text,
          history: history.map(({ role, content }) => ({ role, content })),
          with_web: askWeb,
        },
        {
          onSuccess: (res) => {
            if (epoch !== epochRef.current) return;
            commit(
              [
                ...messagesRef.current,
                { role: "assistant", content: res.summary, presetResult: res },
              ],
              { presetId: null, context: res.context },
            );
            setBusy(null);
          },
          onError: (err: Error) => {
            if (epoch !== epochRef.current) return;
            commit([
              ...messagesRef.current,
              { role: "assistant", content: t("geo.ia.cross.error", { message: err.message }) },
            ]);
            setBusy(null);
          },
        },
      );
      return true;
    }
    return false; // 機能フラグが無効のときは、従来どおり /api/geo/chat
  };

  // ---- 調査の実行（「この条件で調査する」）----
  const onRun = (formIndex: number, def: PresetDefinition, conditions: PresetConditions) => {
    if (busy) return; // 多重実行の防止
    const epoch = epochRef.current;
    const setFormStatus = (list: ChatMsg[], status: "editing" | "running" | "done", conds?: PresetConditions) =>
      list.map((m, i) =>
        i === formIndex && m.presetForm ? { ...m, presetForm: { ...m.presetForm, status, conditions: conds ?? m.presetForm.conditions } } : m,
      );
    const summary = [def.title, ...def.fields.map((f) => {
      const v = String(conditions[f.key] ?? "").trim();
      if (!v) return null;
      const label = f.options.find((o) => o.value === v)?.label ?? v;
      return `・${f.label}：${label}`;
    }).filter(Boolean) as string[]].join("\n");
    commit([...setFormStatus(messagesRef.current, "running", conditions), { role: "user", content: summary }]);
    setBusy(t("geo.ia.preset.running"));
    run.mutate(
      { preset_id: def.id, conditions },
      {
        onSuccess: (res) => {
          if (epoch !== epochRef.current) return;
          commit(
            [...setFormStatus(messagesRef.current, "done"), { role: "assistant", content: res.summary, presetResult: res }],
            { presetId: null, context: res.context },
          );
          setBusy(null);
        },
        onError: (err: Error) => {
          if (epoch !== epochRef.current) return;
          commit([
            ...setFormStatus(messagesRef.current, "editing", conditions),
            { role: "assistant", content: t("geo.ia.preset.runError", { message: err.message }) },
          ]);
          setBusy(null);
        },
      },
    );
  };

  const renderMessage = (m: ChatMsg, index: number) => {
    if (m.presetForm) {
      const def = presets.find((p) => p.id === m.presetForm!.presetId);
      return (
        <div>
          <div className="text-sm leading-relaxed whitespace-pre-wrap text-foreground">{m.content}</div>
          {def && (
            <PresetFormCard
              def={def}
              state={m.presetForm}
              onDraftChange={(v) => (draftRef.current = v)}
              onRun={(c) => onRun(index, def, c)}
            />
          )}
        </div>
      );
    }
    if (m.presetResult) return <PresetResultCard result={m.presetResult} />;
    if (m.presetFollowup) return <PresetFollowupCard result={m.presetFollowup} />;
    return null;
  };

  return (
    <div className="flex-1 grid grid-cols-1 md:grid-cols-[300px_1fr] min-h-[600px] geo-card overflow-hidden">
      <button
        type="button"
        onClick={() => setAsideOpen((v) => !v)}
        aria-expanded={asideOpen}
        className="md:hidden flex items-center gap-2 px-4 py-2.5 border-b border-border text-sm bg-muted/20"
      >
        <Menu className="h-4 w-4" /> {t("geo.ia.preset.menu")}
      </button>
      <aside className={`${asideOpen ? "block" : "hidden"} md:block border-r border-border p-4 bg-muted/20 overflow-y-auto max-h-[70vh] md:max-h-none`}>
        <button
          onClick={startNew}
          className="w-full flex items-center gap-2 px-3 py-2.5 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:opacity-90"
        >
          <Plus className="h-4 w-4" /> {t("chat.newConversation")}
        </button>

        {presets.length > 0 && (
          <PresetList
            presets={presets}
            activeId={preset.presetId}
            disabled={!!busy}
            onSelect={selectPreset}
            availability={presetsQuery.data?.source_availability ?? {}}
          />
        )}
        {crossEnabled && presetsQuery.data?.source_availability?.web?.available && (
          <label className="mt-4 flex items-start gap-2 px-2 text-[11px] text-muted-foreground">
            <input
              type="checkbox"
              checked={askWeb}
              onChange={(e) => setAskWeb(e.target.checked)}
              className="mt-0.5"
            />
            <span>{t("geo.ia.cross.web")}</span>
          </label>
        )}
        {presetsQuery.error && (
          <p className="mt-4 px-2 text-[11px] text-destructive">{(presetsQuery.error as Error).message}</p>
        )}

        <div className="mt-6">
          <div className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground mb-2 px-2">{t("chat.history")}</div>
          {conversations.length === 0 ? (
            <p className="text-xs text-muted-foreground px-2.5 leading-relaxed">{t("chat.historyEmpty")}</p>
          ) : (
            <div className="space-y-0.5">
              {conversations.map((c) => (
                <button
                  key={c.id}
                  onClick={() => openConversation(c)}
                  className={`group w-full text-left px-2.5 py-2 rounded-md hover:bg-muted transition-colors ${
                    activeId === c.id ? "bg-muted" : ""
                  }`}
                >
                  <div className="flex items-start gap-2">
                    <MessageSquare className="h-3.5 w-3.5 text-muted-foreground mt-0.5 shrink-0" />
                    <div className="min-w-0 flex-1">
                      <div className="text-sm truncate">{c.title}</div>
                      <div className="text-[10px] text-muted-foreground">{new Date(c.updatedAt).toLocaleString()}</div>
                    </div>
                    <Trash2
                      className="h-3.5 w-3.5 text-muted-foreground opacity-0 group-hover:opacity-100 hover:text-destructive shrink-0"
                      onClick={(e) => removeConversation(c.id, e)}
                    />
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      </aside>

      <div className="min-h-0">
        <GeoAiChatPanel
          messages={messages}
          onMessagesChange={(next) => commit(next)}
          interceptSend={interceptSend}
          renderMessage={renderMessage}
          externalBusyLabel={busy}
        />
      </div>
    </div>
  );
}
