// Intelligence AI「会話モード」の本体（テーマ選択 → AIが先に会話を始める → ユーザーが参加する）。
// 会話の生成はすべてサーバー側（実LLM＋既存のIntelligence AI/DB）。この画面は固定台本を持たない。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Loader2,
  Pause,
  Play,
  RotateCcw,
  Send,
  Trash2,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  DeleteConversationDialog,
  DeleteOutcomeNotice,
  type DeleteOutcome,
  type DeleteTarget,
} from "@/components/geo/conversation-mode/delete-conversation-dialog";
import {
  CharacterAvatar,
  type CharacterId,
} from "@/components/geo/conversation-mode/character-art";
import { getIosCredential } from "@/lib/geoIosAuthHeader";
import { fetchServerConversations } from "@/lib/geoIntelligencePresetApi";
import {
  deleteCmLocal,
  deleteCmServer,
  fetchCmThemes,
  listCmLocal,
  saveCmLocal,
  startCmStream,
  storedFromServer,
  turnCm,
  turnCmStream,
  type CmStreamEvent,
  type CmAction,
  type CmBasis,
  type CmAccess,
  type CmCarry,
  type CmConfig,
  type CmMessage,
  type CmModeId,
  type CmPersistence,
  type CmState,
  type CmStored,
  type CmTheme,
} from "@/lib/geoConversationModeApi";

const STATIC_SPEAKERS: Record<string, { id: string; name: string }[]> = {
  gal_oyaji: [
    { id: "gal", name: "みか" },
    { id: "oyaji", name: "中曽根さん" },
  ],
  consult_partner: [
    { id: "consultant", name: "コンサル" },
    { id: "partner", name: "東条さん" },
  ],
};

const BASIS_LABEL: Record<CmBasis, string> = {
  evidence: "根拠あり",
  inference: "推測",
  assumption: "仮定",
  question: "質問",
  unknown: "不明",
};

const GROUNDING_LABEL: Record<string, string> = {
  db_and_evidence: "DB・資料に基づく",
  evidence: "資料に基づく",
  ai_inference: "AIの推測（DB・資料では未確認）",
};

const AUTO_STEPS = 3;
const AUTO_DELAY_MS = 1500;

const LIMIT_NOTE_FALLBACK =
  "AI呼び出しの上限は、サーバー側（プロセス内）の概算です。再起動などで数え直されることがあり、厳密な上限ではありません。";

const PERSIST_NOTE: Record<string, string> = {
  login_required: "未ログインのため、この会話はこのブラウザにだけ保存されます。",
  table_missing: "サーバー側の保存先が未設定のため、この会話はこのブラウザにだけ保存されます。",
};

const fmtTime = (ts: string) => {
  const d = new Date(ts);
  return Number.isNaN(+d)
    ? ""
    : d.toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit" });
};

/** 出典番号の種類。S=社内資料、K=Expert AIの基盤知識、F=基盤文脈、W/WS=Web（未検証）。 */
function refKind(ref: string): string {
  if (ref === "WS" || ref.startsWith("W")) return "Web・未検証";
  if (ref.startsWith("K")) return "Expert AIの基盤知識";
  if (ref.startsWith("F")) return "Expert AIの基盤文脈";
  return "社内資料";
}

function webLine(web?: {
  status: string;
  reason?: string | null;
  checked_at?: string | null;
}): string {
  if (!web) return "Web検索: 未確認";
  switch (web.status) {
    case "searched":
      return `Web検索: 実施（確認時点 ${web.checked_at ? new Date(web.checked_at).toLocaleString("ja-JP") : "不明"}・未検証の情報）`;
    case "unavailable":
      return `Web検索: 不可（${web.reason ?? "利用できません"}）`;
    case "error":
      return `Web検索: 失敗・未確認（${web.reason ?? ""}）`;
    case "skipped":
      return `Web検索: 未実施（${web.reason ?? ""}）`;
    default:
      return "Web検索: 未実施（実際には検索していません）";
  }
}

interface Conv {
  state: CmState;
  messages: CmMessage[];
  persistence: CmPersistence | null;
}

export function ConversationModePanel({
  mode,
  config,
  carry,
  onStateChange,
  onBusyChange,
}: {
  mode: CmModeId;
  config: CmConfig;
  carry: CmCarry | null;
  onStateChange: (s: CmState | null) => void;
  onBusyChange: (b: boolean) => void;
}) {
  const modeInfo = config.modes?.find((m) => m.id === mode);
  const [themes, setThemes] = useState<CmTheme[] | null>(null);
  const [themeError, setThemeError] = useState<string | null>(null);
  const [freeTheme, setFreeTheme] = useState("");
  const [useWeb, setUseWeb] = useState(true); // Web調査に同意するか（テーマの題名を外部へ送る）
  const [conv, setConv] = useState<Conv | null>(null);
  const [busyLabel, setBusyLabel] = useState<string | null>(null);
  const [error, setError] = useState<{ message: string; retry: () => void } | null>(null);
  const [input, setInput] = useState("");
  const [addressee, setAddressee] = useState("");
  const [showEvidence, setShowEvidence] = useState(false);
  const [showLedger, setShowLedger] = useState(false);
  const [highlight, setHighlight] = useState<string | null>(null);
  const [history, setHistory] = useState<CmStored[]>([]);
  const [access, setAccess] = useState<CmAccess | null>(null);
  // 生成の途中経過（できた発言を、完了を待たずに表示する）。完了したら会話本体に反映して消す。
  const [live, setLive] = useState<{
    theme?: string;
    messages: CmMessage[];
    speaker: string | null;
    status: string | null;
  } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteOutcome, setDeleteOutcome] = useState<DeleteOutcome | null>(null);
  const lock = useRef(false); // 二重実行の防止（state更新より早く効かせる）
  // 半自動の進行: 「続きを聞く」を、最大 AUTO_STEPS 回まで、少し間をあけて自動で続ける。入力・操作・エラー・停止で止まる。
  const [auto, setAuto] = useState<{ remaining: number } | null>(null);
  const pollTries = useRef(0);
  const autoStarted = useRef(false);
  const endRef = useRef<HTMLDivElement | null>(null);
  const maxChars = config.limits?.max_user_chars ?? 800;
  const maxCalls = config.limits?.max_llm_calls ?? 40;

  const commit = useCallback(
    (res: {
      state: CmState;
      messages: CmMessage[];
      persistence: CmPersistence;
      access?: CmAccess;
    }) => {
      setAccess(res.access ?? null);
      setConv({ state: res.state, messages: res.messages, persistence: res.persistence });
      saveCmLocal(res.state, res.messages, `【${modeInfo?.name ?? ""}】${res.state.theme}`);
      onStateChange(res.state);
    },
    [modeInfo?.name, onStateChange],
  );

  const onStreamEvent = useCallback((e: CmStreamEvent) => {
    setLive((prev) => {
      const cur = prev ?? { messages: [], speaker: null, status: null };
      switch (e.type) {
        case "status":
          return { ...cur, status: e.text };
        case "speaker":
          return { ...cur, speaker: e.speaker, status: null };
        case "speakers":
          return { ...cur, speaker: e.speakers.join("・"), status: null };
        case "utterance":
          return { ...cur, messages: [...cur.messages, e.message], speaker: null, status: null };
        default:
          return cur;
      }
    });
  }, []);

  // 1つの操作を実行する。実行中は他の操作を受け付けない。失敗したら同じ操作をやり直せる。
  // fn は、できた発言を onEvent で受け取りながら実行する（受け取らない処理は、そのまま結果だけ返せばよい）。
  const run = useCallback(
    async (
      label: string,
      fn: (onEvent: (e: CmStreamEvent) => void) => Promise<{
        state: CmState;
        messages: CmMessage[];
        persistence: CmPersistence;
        access?: CmAccess;
      }>,
      opts: { theme?: string; preview?: CmMessage[] } = {},
    ) => {
      if (lock.current) return;
      lock.current = true;
      onBusyChange(true);
      setBusyLabel(label);
      setError(null);
      setDeleteOutcome(null);
      setLive({ theme: opts.theme, messages: opts.preview ?? [], speaker: null, status: label });
      try {
        commit(await fn(onStreamEvent));
      } catch (e) {
        const err = e as Error & { status?: number };
        setError({
          message: err.message || "うまくいきませんでした",
          retry: () => void run(label, fn, opts),
        });
      } finally {
        lock.current = false;
        onBusyChange(false);
        setBusyLabel(null);
        setLive(null);
      }
    },
    [commit, onBusyChange, onStreamEvent],
  );

  const begin = useCallback(
    (theme: string, source: "recommended" | "free", withCarry: CmCarry | null) => {
      const t = theme.trim();
      if (!t) return;
      void run(
        `${modeInfo?.name ?? "会話"}を始めています…`,
        (onEvent) =>
          startCmStream(
            {
              mode,
              theme: t,
              theme_source: source,
              carry: withCarry,
              with_web: !!config.web_search_enabled && useWeb,
            },
            onEvent,
          ),
        { theme: t },
      );
    },
    [mode, modeInfo?.name, run, config.web_search_enabled, useWeb],
  );

  // モード切替で引き継ぎがあるときは、テーマ選択を飛ばして同じテーマで始める
  useEffect(() => {
    if (carry && !autoStarted.current) {
      autoStarted.current = true;
      begin(carry.theme, "free", carry);
    }
  }, [carry, begin]);

  useEffect(() => {
    if (carry) return;
    let cancelled = false;
    fetchCmThemes(mode)
      .then((r) => !cancelled && setThemes(r.themes))
      .catch((e: Error) => {
        if (!cancelled) {
          setThemes([]);
          setThemeError(e.message);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [mode, carry]);

  // 最近の会話（ブラウザ内 + ログイン中はサーバー保存分）。通常チャットの履歴は含めない。
  const mergeHistory = useCallback(
    (remote: CmStored[]) => {
      const map = new Map<string, CmStored>();
      [...listCmLocal(), ...remote].forEach((c) => {
        const old = map.get(c.id);
        if (!old || +new Date(c.updatedAt) > +new Date(old.updatedAt)) map.set(c.id, c);
      });
      return [...map.values()]
        .filter((c) => c.state?.mode === mode)
        .sort((a, b) => +new Date(b.updatedAt) - +new Date(a.updatedAt))
        .slice(0, 8);
    },
    [mode],
  );
  const reloadHistory = useCallback(
    async (isCancelled: () => boolean = () => false) => {
      setHistory(mergeHistory([]));
      const items = await fetchServerConversations();
      if (isCancelled()) return;
      setHistory(mergeHistory(items.map(storedFromServer).filter((c): c is CmStored => !!c)));
    },
    [mergeHistory],
  );
  useEffect(() => {
    let cancelled = false;
    void reloadHistory(() => cancelled);
    return () => {
      cancelled = true;
    };
  }, [reloadHistory]);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end", behavior: "smooth" });
  }, [conv?.messages.length]);

  const loggedIn = !!getIosCredential();

  const requestDelete = (id: string, theme: string) => {
    if (lock.current) return;
    setDeleteOutcome(null);
    setDeleteTarget({ id, theme });
  };

  // 選んだ1件だけを削除する。ブラウザ内とサーバーを別々に実行し、結果を区別して表示する。
  const confirmDelete = async () => {
    const target = deleteTarget;
    if (!target || lock.current) return;
    lock.current = true;
    onBusyChange(true);
    setBusyLabel("会話を削除しています…");
    setDeleting(true);
    try {
      const wasActive = conv?.state.conversation_id === target.id;
      const server = await deleteCmServer(target.id);
      const local = deleteCmLocal(target.id);
      if (wasActive) {
        setConv(null);
        setAccess(null);
        setError(null);
        setInput("");
        setAddressee("");
        setShowEvidence(false);
        setShowLedger(false);
        onStateChange(null); // モード切替の引き継ぎに、削除した会話の内容が残らないようにする
      }
      setDeleteOutcome({ theme: target.theme, local, server });
      setDeleteTarget(null);
      // サーバー側の削除に失敗した会話は、サーバー保存分から一覧に戻る（もう一度削除できる）
      await reloadHistory();
    } finally {
      lock.current = false;
      onBusyChange(false);
      setBusyLabel(null);
      setDeleting(false);
    }
  };

  const speakers = useMemo(() => {
    if (!conv) return [];
    return mode === "stakeholder"
      ? conv.state.stakeholders.map((s) => ({ id: s.id, name: s.name }))
      : (STATIC_SPEAKERS[mode] ?? []);
  }, [conv, mode]);

  const send = (
    action: CmAction,
    extra: { text?: string; chip?: string; addressee?: string } = {},
    fromAuto = false,
  ) => {
    if (!conv) return;
    if (!fromAuto) setAuto(null); // 人の操作が入ったら、自動進行は止める（ユーザーの入力が最優先）
    const labels: Record<CmAction, string> = {
      ask: "みんなが答えを考えています…",
      continue: "続きを考えています…",
      summarize: "まとめを作っています…",
      pause: "停止しています…",
      resume: "再開しています…",
      refresh: "権限を確認しています…",
      research: "Webで最新の情報を調べています…",
    };
    const { state, messages } = conv;
    const streams =
      action === "ask" || action === "continue" || action === "summarize" || action === "research";
    const chipLabel = config.chips?.find((c) => c.id === extra.chip)?.label;
    const preview: CmMessage[] =
      action === "ask" && (extra.text || chipLabel)
        ? [
            {
              role: "user",
              kind: "user",
              speaker: "あなた",
              content: extra.text ?? chipLabel ?? "",
              ts: new Date().toISOString(),
              chip: extra.chip ?? null,
            },
          ]
        : [];
    void run(
      labels[action],
      (onEvent) =>
        streams
          ? turnCmStream({ state, messages, action, ...extra }, onEvent)
          : turnCm({ state, messages, action, ...extra }),
      { preview },
    );
  };

  // 自動進行: 生成が終わって、入力・エラー・停止がなければ、少し間をあけて続きを進める
  useEffect(() => {
    if (!auto) return;
    if (auto.remaining <= 0) {
      if (!busyLabel) setAuto(null);
      return;
    }
    if (busyLabel || error || conv?.state.status === "paused" || input.trim() || !conv) return;
    const t = setTimeout(() => {
      setAuto((a) => (a ? { remaining: a.remaining - 1 } : a));
      send("continue", {}, true);
    }, AUTO_DELAY_MS);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auto, busyLabel, error, input, conv?.state.rev, conv?.state.status]);

  // 裏での取得（Web調査・Expert AIの基盤知識）が終わったら、AIを呼ばずに取り込む。会話を止めないよう、操作中でなければ静かに行う。
  useEffect(() => {
    if (!conv || conv.state.evidence.enrichment !== "pending") {
      pollTries.current = 0;
      return;
    }
    if (busyLabel || pollTries.current >= 4) return;
    const t = setTimeout(
      () => {
        if (lock.current) return;
        pollTries.current += 1;
        void run("知識を取り込んでいます…", (onEvent) =>
          turnCm({ state: conv.state, messages: conv.messages, action: "refresh" }),
        );
      },
      pollTries.current === 0 ? 8000 : 6000,
    );
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conv?.state.rev, conv?.state.evidence.enrichment, busyLabel]);

  const submit = () => {
    const text = input.trim();
    if (!text || lock.current) return;
    setInput("");
    send("ask", { text, addressee: addressee || undefined });
  };

  const openRef = (ref: string) => {
    setShowEvidence(true);
    setHighlight(ref);
    setTimeout(
      () =>
        document
          .getElementById(`cm-ev-${ref}`)
          ?.scrollIntoView?.({ block: "center", behavior: "smooth" }),
      50,
    );
  };

  const busy = !!busyLabel;
  const paused = conv?.state.status === "paused";

  return (
    <section aria-label={modeInfo?.name} className="space-y-4">
      {deleteOutcome && (
        <DeleteOutcomeNotice outcome={deleteOutcome} onClose={() => setDeleteOutcome(null)} />
      )}
      {mode === "stakeholder" && (
        <div
          role="note"
          className="flex gap-2 rounded-md border border-border bg-muted/40 px-3 py-2 text-xs"
        >
          <AlertTriangle className="h-4 w-4 shrink-0 text-geo-amber" aria-hidden="true" />
          <span>
            {config.simulation_notice ??
              "ロールプレイング会議は仮想シミュレーションです。発言は公的事実ではなく、根拠に基づく推論または仮定です。"}
          </span>
        </div>
      )}

      {!conv && !live && (
        <div className="geo-card p-5 space-y-4">
          <h2 className="text-sm font-medium">会話のテーマを選んでください</h2>
          {themes === null && !carry ? (
            <p className="text-sm text-muted-foreground flex items-center gap-2">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              おすすめテーマを探しています…
            </p>
          ) : (
            <>
              <div className="grid gap-2 sm:grid-cols-3">
                {(themes ?? []).map((t) => (
                  <Button
                    key={t.title}
                    variant="outline"
                    className="h-auto whitespace-normal text-left justify-start py-2"
                    disabled={busy}
                    onClick={() => begin(t.title, "recommended", null)}
                  >
                    {t.title}
                  </Button>
                ))}
              </div>
              {(themes ?? []).some((t) => t.source === "fallback") && (
                <p className="text-[11px] text-muted-foreground">
                  モニタリング中のテーマを取得できなかったため、定型の候補を表示しています。
                </p>
              )}
              {themeError && (
                <p className="text-[11px] text-muted-foreground">
                  おすすめの取得に失敗しました（{themeError}）。自由入力でも始められます。
                </p>
              )}
            </>
          )}
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              begin(freeTheme, "free", null);
            }}
          >
            <label className="sr-only" htmlFor="cm-free-theme">
              自由入力のテーマ
            </label>
            <input
              id="cm-free-theme"
              value={freeTheme}
              onChange={(e) => setFreeTheme(e.target.value)}
              maxLength={120}
              disabled={busy}
              placeholder="自由にテーマを入力（例: 〇〇の関税政策が当社に与える影響）"
              className="flex-1 rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
            <Button type="submit" disabled={busy || !freeTheme.trim()}>
              始める
            </Button>
          </form>
          {config.web_search_enabled ? (
            <label className="flex items-start gap-2 rounded-md border border-border bg-muted/30 px-3 py-2 text-xs">
              <input
                type="checkbox"
                checked={useWeb}
                onChange={(e) => setUseWeb(e.target.checked)}
                disabled={busy}
                className="mt-0.5"
              />
              <span>
                <span className="font-medium">Web調査も使う</span>
                （最新ニュースなど、公開情報を調べます）
                <span className="block text-muted-foreground">
                  外部のWeb検索には、テーマの題名（自由入力の場合は入力した文）だけを送ります。社内資料・会話の内容は送りません。
                  社外秘の内容は、テーマに書かないでください。
                </span>
              </span>
            </label>
          ) : (
            <p className="text-[11px] text-muted-foreground">
              Web調査は、利用できません（外部のWeb検索には、何も送りません）。
            </p>
          )}
          {history.length > 0 && (
            <div>
              <h3 className="text-xs text-muted-foreground mb-1.5">最近の会話</h3>
              <ul className="space-y-1">
                {history.map((h) => (
                  <li key={h.id} className="flex items-center gap-1">
                    <button
                      type="button"
                      className="flex-1 min-w-0 text-left text-sm rounded px-2 py-1.5 hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      onClick={() =>
                        // 保存された会話は、開く前にサーバーで今の権限を取り直す（取り消された根拠は表示しない）
                        void run("権限を確認しています…", () =>
                          turnCm({ state: h.state, messages: h.messages, action: "refresh" }),
                        )
                      }
                    >
                      <span className="line-clamp-1">{h.state.theme}</span>
                      <span className="text-[10px] text-muted-foreground">
                        {new Date(h.updatedAt).toLocaleString("ja-JP")}・{h.messages.length}件
                      </span>
                    </button>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      disabled={busy}
                      aria-label={`会話「${h.state.theme}」を削除`}
                      onClick={() => requestDelete(h.id, h.state.theme)}
                    >
                      <Trash2 className="h-4 w-4" aria-hidden="true" />
                    </Button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {!conv && error && (
        <div
          role="alert"
          className="flex flex-wrap items-center gap-2 rounded-md border border-destructive/50 bg-destructive/5 px-3 py-2 text-sm"
        >
          <AlertTriangle className="h-4 w-4 text-destructive" aria-hidden="true" />
          <span className="flex-1">{error.message}</span>
          <Button size="sm" variant="outline" onClick={error.retry} disabled={busy}>
            もう一度
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setError(null)}>
            閉じる
          </Button>
        </div>
      )}

      {!conv && live && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <Badge variant="secondary">{modeInfo?.name}</Badge>
            {live.theme && <span className="text-foreground text-sm">{live.theme}</span>}
          </div>
          <div
            role="log"
            aria-live="polite"
            aria-label="会話"
            className="geo-card !bg-[#dde8f4] p-4 space-y-3 max-h-[60vh] overflow-y-auto dark:!bg-slate-900"
          >
            {live.messages.map((m, i) => (
              <MessageBubble key={`live-${m.ts}-${i}`} m={m} onRef={openRef} />
            ))}
            <TypingBubble
              speaker={live?.speaker ?? null}
              label={live?.speaker ? `${live.speaker}が入力中…` : (live?.status ?? busyLabel ?? "")}
            />
          </div>
        </div>
      )}

      {busy && !conv && !live && (
        <p role="status" className="text-sm text-muted-foreground flex items-center gap-2">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          {busyLabel}（数十秒かかることがあります）
        </p>
      )}

      {conv && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <Badge variant="secondary">{modeInfo?.name}</Badge>
            <span className="text-foreground text-sm">{conv.state.theme}</span>
            {paused && <Badge variant="outline">停止中</Badge>}
            <span
              className="ml-auto"
              title={config.limits?.note ?? LIMIT_NOTE_FALLBACK}
              aria-label={`AI呼び出し ${conv.state.budget.llm_calls}回（目安の上限 ${maxCalls}回）。${config.limits?.note ?? LIMIT_NOTE_FALLBACK}`}
            >
              AI呼び出し {conv.state.budget.llm_calls}/{maxCalls}（目安）
            </span>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={() => requestDelete(conv.state.conversation_id, conv.state.theme)}
            >
              <Trash2 className="h-4 w-4 mr-1" aria-hidden="true" />
              この会話を削除
            </Button>
          </div>

          {mode === "stakeholder" && (
            <ul className="flex flex-wrap gap-2" aria-label="参加している主体">
              {conv.state.stakeholders.map((s) => (
                <li key={s.id} className="text-xs rounded border border-border px-2 py-1">
                  {s.name}{" "}
                  <span className="text-muted-foreground">（{GROUNDING_LABEL[s.grounding]}）</span>
                </li>
              ))}
            </ul>
          )}

          <div
            role="log"
            aria-live="polite"
            aria-label="会話"
            className="geo-card !bg-[#dde8f4] p-4 space-y-3 max-h-[60vh] overflow-y-auto dark:!bg-slate-900"
          >
            {conv.messages.map((m, i) => (
              <MessageBubble
                key={`${m.ts}-${i}`}
                m={m}
                onRef={openRef}
                read={
                  m.role === "user" &&
                  conv.messages.slice(i + 1).some((x) => x.role === "assistant")
                }
              />
            ))}
            {live?.messages.map((m, i) => (
              <MessageBubble key={`live-${m.ts}-${i}`} m={m} onRef={openRef} />
            ))}
            {busy && (
              <TypingBubble
                speaker={live?.speaker ?? null}
                label={
                  live?.speaker ? `${live.speaker}が入力中…` : (live?.status ?? busyLabel ?? "")
                }
              />
            )}
            <div ref={endRef} />
          </div>

          {error && (
            <div
              role="alert"
              className="flex flex-wrap items-center gap-2 rounded-md border border-destructive/50 bg-destructive/5 px-3 py-2 text-sm"
            >
              <AlertTriangle className="h-4 w-4 text-destructive" aria-hidden="true" />
              <span className="flex-1">{error.message}</span>
              <Button size="sm" variant="outline" onClick={error.retry} disabled={busy}>
                <RotateCcw className="h-3.5 w-3.5 mr-1" aria-hidden="true" />
                もう一度
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setError(null)}>
                閉じる
              </Button>
            </div>
          )}

          {((conv.state.history_cutoff ?? 0) > 0 || (access?.revoked_count ?? 0) > 0) && (
            <div
              role="note"
              className="flex gap-2 rounded-md border border-border bg-muted/40 px-3 py-2 text-xs"
            >
              <AlertTriangle className="h-4 w-4 shrink-0 text-geo-amber" aria-hidden="true" />
              <span>
                権限が変更されたため、一部の根拠と発言は表示・利用していません。それ以前のAIの発言は、これからの回答の材料にも使いません。
              </span>
            </div>
          )}

          <p className="text-[11px] text-muted-foreground">
            {config.limits?.note ?? LIMIT_NOTE_FALLBACK}
          </p>

          {conv.persistence?.status === "local_only" && (
            <p className="text-[11px] text-muted-foreground">
              {PERSIST_NOTE[conv.persistence.reason ?? ""] ??
                "サーバーへ保存できなかったため、この会話はこのブラウザにだけ保存されます。"}
            </p>
          )}

          <div className="flex flex-wrap gap-2" aria-label="質問チップ">
            {(config.chips ?? []).map((c) => (
              <Button
                key={c.id}
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => send("ask", { chip: c.id, addressee: addressee || undefined })}
              >
                {c.label}
              </Button>
            ))}
          </div>

          <div className="space-y-2">
            <label htmlFor="cm-input" className="sr-only">
              発言を入力
            </label>
            <Textarea
              id="cm-input"
              value={input}
              maxLength={maxChars}
              disabled={busy}
              rows={2}
              placeholder="質問・反論・方向転換など、自由に話しかけてください（Ctrl+Enterで送信）"
              onChange={(e) => {
                setInput(e.target.value);
                if (auto) setAuto(null);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                  e.preventDefault();
                  submit();
                }
              }}
            />
            <div className="flex flex-wrap items-center gap-2">
              <label className="text-xs text-muted-foreground" htmlFor="cm-addressee">
                話しかける相手
              </label>
              <select
                id="cm-addressee"
                value={addressee}
                onChange={(e) => setAddressee(e.target.value)}
                disabled={busy}
                className="rounded-md border border-input bg-background px-2 py-1 text-sm"
              >
                <option value="">おまかせ</option>
                {speakers.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
              <Button onClick={submit} disabled={busy || !input.trim()}>
                <Send className="h-4 w-4 mr-1" aria-hidden="true" />
                送信
              </Button>
              <Button variant="outline" onClick={() => send("continue")} disabled={busy || paused}>
                続きを聞く
              </Button>
              {config.web_search_enabled && (
                <Button
                  variant="outline"
                  disabled={busy}
                  title="最新の情報をWebで調べて、会話に反映します。入力欄に文を書いている場合は、その文も検索語に加えて外部へ送ります（書いていなければ、テーマの題名だけ）"
                  onClick={() => {
                    const text = input.trim();
                    setInput("");
                    send("research", text ? { text } : {});
                  }}
                >
                  Webで調べる
                </Button>
              )}
              {auto ? (
                <Button variant="destructive" onClick={() => setAuto(null)}>
                  自動進行を止める（あと{Math.max(auto.remaining, 0)}回）
                </Button>
              ) : (
                <Button
                  variant="outline"
                  disabled={busy || paused}
                  onClick={() => {
                    setAuto({ remaining: AUTO_STEPS });
                  }}
                  title="続きを、最大3回まで自動で進めます。入力や操作をすると止まります"
                >
                  自動で進める（{AUTO_STEPS}回）
                </Button>
              )}
              <Button variant="outline" onClick={() => send("summarize")} disabled={busy}>
                まとめ
              </Button>
              {paused ? (
                <Button variant="outline" onClick={() => send("resume")} disabled={busy}>
                  <Play className="h-4 w-4 mr-1" aria-hidden="true" />
                  再開
                </Button>
              ) : (
                <Button variant="outline" onClick={() => send("pause")} disabled={busy}>
                  <Pause className="h-4 w-4 mr-1" aria-hidden="true" />
                  停止
                </Button>
              )}
            </div>
          </div>

          <EvidencePanel
            state={conv.state}
            open={showEvidence}
            onToggle={() => setShowEvidence((v) => !v)}
            highlight={highlight}
          />
          <div className="geo-card p-3">
            <button
              type="button"
              className="flex w-full items-center justify-between text-sm"
              aria-expanded={showLedger}
              onClick={() => setShowLedger((v) => !v)}
            >
              <span>会話の整理（事実・推測・未解決の問い）</span>
              {showLedger ? (
                <ChevronUp className="h-4 w-4" aria-hidden="true" />
              ) : (
                <ChevronDown className="h-4 w-4" aria-hidden="true" />
              )}
            </button>
            {showLedger && (
              <div className="mt-2 grid gap-3 sm:grid-cols-3 text-xs">
                <LedgerColumn title="出典つきの事実" items={conv.state.ledger.facts} />
                <LedgerColumn
                  title="推測・仮定（事実ではない）"
                  items={conv.state.ledger.assumptions}
                />
                <LedgerColumn title="未解決の問い" items={conv.state.ledger.open_questions} />
              </div>
            )}
          </div>
        </div>
      )}
      <DeleteConversationDialog
        target={deleteTarget}
        deleting={deleting}
        loggedIn={loggedIn}
        onConfirm={() => void confirmDelete()}
        onCancel={() => {
          if (!deleting) setDeleteTarget(null);
        }}
      />
    </section>
  );
}

// 話者ごとのアイコン（絵文字）と色。LINEのように、誰の発言かが一目で分かるようにする。
const AVATARS: Record<string, { icon: string; bg: string }> = {
  gal: { icon: "💅", bg: "bg-pink-200" },
  oyaji: { icon: "🍶", bg: "bg-amber-200" },
  consultant: { icon: "💼", bg: "bg-indigo-200" },
  partner: { icon: "🔍", bg: "bg-teal-200" },
  summarizer: { icon: "📝", bg: "bg-slate-200" },
};
const ACTOR_AVATARS = [
  { icon: "🏛️", bg: "bg-sky-200" },
  { icon: "🛡️", bg: "bg-emerald-200" },
  { icon: "🏭", bg: "bg-orange-200" },
  { icon: "🌐", bg: "bg-violet-200" },
];

function avatarOf(speakerId?: string, speaker?: string) {
  if (speakerId && AVATARS[speakerId]) return AVATARS[speakerId];
  const m = /^actor(\d+)$/.exec(speakerId ?? "");
  if (m) return ACTOR_AVATARS[Number(m[1]) % ACTOR_AVATARS.length];
  return { icon: (speaker ?? "?").slice(0, 1), bg: "bg-slate-200" };
}

const CHARACTER_IDS: readonly string[] = ["gal", "oyaji", "consultant", "partner", "summarizer"];
const isCharacterId = (id?: string): id is CharacterId => !!id && CHARACTER_IDS.includes(id);

const NAME_TO_ID: Record<string, CharacterId> = {
  みか: "gal",
  中曽根さん: "oyaji",
  コンサル: "consultant",
  東条さん: "partner",
  ミナト: "summarizer",
};

function Avatar({ speakerId, speaker }: { speakerId?: string; speaker?: string }) {
  const id = isCharacterId(speakerId) ? speakerId : speaker ? NAME_TO_ID[speaker] : undefined;
  if (id) return <CharacterAvatar id={id} size={36} />;
  const a = avatarOf(speakerId, speaker);
  return (
    <span
      aria-hidden="true"
      data-avatar={speakerId ?? "other"}
      className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-lg shadow-sm ${a.bg}`}
    >
      {a.icon}
    </span>
  );
}

/** 入力中の表示（LINEのような、点が跳ねる吹き出し）。 */
function TypingBubble({ speaker, label }: { speaker: string | null; label: string }) {
  return (
    <div role="status" className="flex items-end gap-2">
      <Avatar speaker={speaker ?? "…"} speakerId={undefined} />
      <div>
        <div className="mb-0.5 text-[11px] text-muted-foreground">{label}</div>
        <div className="inline-flex items-center gap-1 rounded-2xl rounded-bl-sm bg-white px-4 py-3 shadow-sm dark:bg-slate-700">
          <span className="h-2 w-2 animate-bounce rounded-full bg-slate-400" />
          <span className="h-2 w-2 animate-bounce rounded-full bg-slate-400 [animation-delay:150ms]" />
          <span className="h-2 w-2 animate-bounce rounded-full bg-slate-400 [animation-delay:300ms]" />
        </div>
      </div>
    </div>
  );
}

function MessageBubble({
  m,
  onRef,
  read,
}: {
  m: CmMessage;
  onRef: (ref: string) => void;
  read?: boolean;
}) {
  // Webだけが出典のときは、「根拠あり」ではなく「Web情報（未検証）」と表示する
  const webOnly = (m.ref_ids ?? []).length > 0 && (m.ref_ids ?? []).every((r) => r.startsWith("W"));
  if (m.role === "user") {
    return (
      <div className="flex items-end justify-end gap-1.5">
        <div className="text-right text-[10px] leading-tight text-muted-foreground">
          {read && <div>既読</div>}
          <div>{fmtTime(m.ts)}</div>
        </div>
        <div className="max-w-[75%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-[#8de055] px-3.5 py-2 text-sm text-slate-900 shadow-sm">
          {m.content}
        </div>
      </div>
    );
  }
  return (
    <div data-testid="cm-msg" className="flex items-start gap-2">
      <Avatar speakerId={m.speaker_id} speaker={m.speaker} />
      <div className="min-w-0 max-w-[82%]">
        <div className="mb-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
          <span className="font-medium text-foreground">{m.speaker}</span>
          <span>{m.role_label}</span>
          {m.simulation && (
            <Badge variant="outline" className="text-[10px]">
              仮想発言
            </Badge>
          )}
          {m.kind === "summary" && (
            <Badge variant="secondary" className="text-[10px]">
              まとめ
            </Badge>
          )}
        </div>
        <div className="flex items-end gap-1.5">
          <div className="whitespace-pre-wrap rounded-2xl rounded-tl-sm bg-white px-3.5 py-2 text-sm text-slate-900 shadow-sm dark:bg-slate-700 dark:text-slate-50">
            {m.content}
          </div>
          <span className="shrink-0 text-[10px] text-muted-foreground">{fmtTime(m.ts)}</span>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          {m.basis && (
            <Badge
              variant={m.basis === "evidence" && !webOnly ? "secondary" : "outline"}
              className="text-[10px]"
            >
              {m.basis === "evidence" && webOnly ? "Web情報（未検証）" : BASIS_LABEL[m.basis]}
            </Badge>
          )}
          {m.ungrounded && (
            <Badge variant="outline" className="text-[10px]">
              出典なし・不確実
            </Badge>
          )}
          {(m.ref_ids ?? []).map((r) => (
            <button
              key={r}
              type="button"
              onClick={() => onRef(r)}
              aria-label={`根拠 ${r}（${refKind(r)}）を表示`}
              className="rounded text-[10px] text-geo-teal underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              [{r}]
            </button>
          ))}
          {m.redacted && (
            <Badge variant="outline" className="text-[10px]">
              権限変更のため非表示
            </Badge>
          )}
        </div>
        {m.caveat && (
          <span
            title={m.caveat}
            aria-label={`注意: ${m.caveat}`}
            className="mt-1 inline-block cursor-help text-[10px] text-muted-foreground"
          >
            ⓘ 注意
          </span>
        )}
      </div>
    </div>
  );
}

function EvidencePanel({
  state,
  open,
  onToggle,
  highlight,
}: {
  state: CmState;
  open: boolean;
  onToggle: () => void;
  highlight: string | null;
}) {
  const ev = state.evidence;
  const sources = ev.sources ?? [];
  return (
    <div className="geo-card p-3">
      <button
        type="button"
        className="flex w-full items-center justify-between text-sm"
        aria-expanded={open}
        onClick={onToggle}
      >
        <span>
          根拠（社内資料 {sources.length}件
          {(ev.knowledge ?? []).length > 0 &&
            `・Expert AIの基盤知識 ${(ev.knowledge ?? []).length}件`}
          {(ev.web?.items ?? []).length > 0 && `・Web ${(ev.web?.items ?? []).length}件`}）
          {ev.enrichment === "pending" && (
            <span className="ml-2 text-muted-foreground">（Web・基盤知識を取得中…）</span>
          )}
        </span>
        {open ? (
          <ChevronUp className="h-4 w-4" aria-hidden="true" />
        ) : (
          <ChevronDown className="h-4 w-4" aria-hidden="true" />
        )}
      </button>
      {open && (
        <div className="mt-2 space-y-2 text-xs">
          <p className="text-muted-foreground">
            取得時点: {ev.as_of ? new Date(ev.as_of).toLocaleString("ja-JP") : "不明"}
            （保存時点のスナップショットで、最新とは限りません）
          </p>
          {ev.query_used && (
            <p className="text-muted-foreground">
              社内資料の検索語: 「{ev.query_used}」（{ev.search_hits ?? 0}件ヒット）
            </p>
          )}
          {ev.insufficient && (
            <p className="text-geo-amber">
              {(ev.knowledge ?? []).length > 0 || (ev.web?.items ?? []).length > 0
                ? "社内レポートでは根拠が不足しています（Expert AIの基盤知識・Webで補っています）。"
                : "社内の資料では根拠が不足しています。発言は推測を含みます。"}
            </p>
          )}
          {ev.summary_withdrawn && (
            <p className="text-geo-amber">権限が変更されたため、要点は表示していません。</p>
          )}
          {ev.summary && <p>{ev.summary}</p>}
          <ul className="space-y-1">
            {sources.map((s) => (
              <li
                key={s.ref_id}
                id={`cm-ev-${s.ref_id}`}
                className={`rounded px-2 py-1 ${highlight === s.ref_id ? "bg-accent" : ""}`}
              >
                <span className="font-medium">[{s.ref_id}]</span> {s.title ?? "（題名なし）"}
                <span className="text-muted-foreground">
                  {s.source_type ?? ""} {s.date ?? ""}
                </span>
              </li>
            ))}
          </ul>
          {(ev.knowledge ?? []).length > 0 && (
            <div>
              <h4 className="font-medium">Expert AIの基盤知識（承認済みのナレッジ）</h4>
              <ul className="mt-1 space-y-1">
                {(ev.knowledge ?? []).map((k) => (
                  <li
                    key={k.ref_id}
                    id={`cm-ev-${k.ref_id}`}
                    className={`rounded px-2 py-1 ${highlight === k.ref_id ? "bg-accent" : ""}`}
                  >
                    <span className="font-medium">[{k.ref_id}]</span> {k.title}
                    <span className="text-muted-foreground"> {k.kind}</span>
                    {k.text && <span className="block text-muted-foreground">{k.text}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {ev.foundation?.text && (
            <div
              id="cm-ev-F1"
              className={`rounded px-2 py-1 ${highlight === "F1" ? "bg-accent" : ""}`}
            >
              <h4 className="font-medium">[F1] Expert AIの基盤文脈（この状況の過去の経緯）</h4>
              <p className="text-muted-foreground whitespace-pre-wrap">
                {ev.foundation.text.slice(0, 500)}…
              </p>
            </div>
          )}
          <div>
            <h4 className="font-medium">Web調査（公開情報・未検証）</h4>
            <p className="text-muted-foreground">{webLine(ev.web)}</p>
            {ev.web?.query && (
              <p className="text-muted-foreground">外部へ送った検索語: 「{ev.web.query}」</p>
            )}
            {ev.web?.summary && (
              <p
                id="cm-ev-WS"
                className={`rounded px-2 py-1 ${highlight === "WS" ? "bg-accent" : ""}`}
              >
                <span className="font-medium">[WS]</span> {ev.web.summary}
                <span className="text-muted-foreground">
                  （Web検索AIによる要約。複数ページのまとめ）
                </span>
              </p>
            )}
            {(ev.web?.items ?? []).length > 0 && (
              <ul className="mt-1 space-y-0.5">
                {(ev.web?.items ?? []).map((i) => (
                  <li
                    key={i.url}
                    id={i.ref_id ? `cm-ev-${i.ref_id}` : undefined}
                    className={`rounded px-2 py-0.5 ${i.ref_id && highlight === i.ref_id ? "bg-accent" : ""}`}
                  >
                    <span className="font-medium">[{i.ref_id}]</span>{" "}
                    <a href={i.url} target="_blank" rel="noopener noreferrer" className="underline">
                      {i.title ?? i.url}
                    </a>{" "}
                    <span className="text-muted-foreground">{i.date ?? ""}（未検証）</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function LedgerColumn({
  title,
  items,
}: {
  title: string;
  items: { text: string; speaker?: string; ref_ids?: string[]; simulated?: boolean }[];
}) {
  return (
    <div>
      <h4 className="font-medium mb-1">{title}</h4>
      {items.length === 0 ? (
        <p className="text-muted-foreground">—</p>
      ) : (
        <ul className="space-y-1">
          {items.map((i, idx) => (
            <li key={idx}>
              {i.text}
              {i.simulated ? "（仮想発言）" : ""}
              {(i.ref_ids ?? []).length > 0 && (
                <span className="text-muted-foreground"> [{(i.ref_ids ?? []).join(", ")}]</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
