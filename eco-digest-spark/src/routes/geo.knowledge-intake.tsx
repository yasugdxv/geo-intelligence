import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useRef, useState } from "react";
import { TopBar } from "@/components/top-bar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  confirmAiApproval,
  EMPTY_CARD,
  getIntakeJob,
  IntakeCard,
  IntakeDuplicate,
  IntakeItem,
  IntakeJob,
  intakeChat,
  listIntakeItems,
  startIntakeJob,
} from "@/lib/geoKnowledgeIntakeApi";
import { useLanguage } from "@/lib/i18n";

export const Route = createFileRoute("/geo/knowledge-intake")({
  component: GeoKnowledgeIntakePage,
});

interface Msg {
  role: "user" | "assistant";
  content: string;
}

const TYPE_LABEL: Record<string, string> = {
  person: "人物",
  government: "政府",
  ministry: "省庁",
  political_party: "政党",
  faction: "派閥",
  military: "軍",
  security_service: "情報・治安機関",
  international_organization: "国際機関",
  state_owned_enterprise: "国有企業",
  company: "企業",
  armed_group: "武装組織",
  country: "国",
};

const APPROVAL_LABEL = {
  human: { text: "人が確認済み", variant: "default" as const },
  ai: { text: "AI暫定承認", variant: "secondary" as const },
  none: { text: "候補（未承認）", variant: "outline" as const },
};

function CardView({ card, duplicates }: { card: IntakeCard; duplicates: IntakeDuplicate[] }) {
  const rows: [string, string | null][] = [
    ["名前（英）", card.name_en],
    ["名前（日）", card.name_ja],
    ["種類", card.entity_type ? (TYPE_LABEL[card.entity_type] ?? card.entity_type) : null],
    ["国コード", card.country_code],
    ["重視する視点", card.focus],
  ];
  return (
    <div className="border border-border rounded-md p-3 space-y-2 text-sm">
      <p className="text-xs font-medium text-muted-foreground">要件カード</p>
      {rows.map(([label, value]) => (
        <div key={label} className="flex gap-2">
          <span className="w-24 shrink-0 text-xs text-muted-foreground">{label}</span>
          <span className={value ? "" : "text-muted-foreground"}>{value ?? "未定"}</span>
        </div>
      ))}
      {duplicates.length > 0 && (
        <div className="bg-muted/40 rounded p-2 text-xs space-y-1">
          <p className="font-medium">既存</p>
          {duplicates.map((d) => (
            <p key={d.entity_id}>
              {d.canonical_name}
              {d.name_ja ? `（${d.name_ja}）` : ""} ／ {TYPE_LABEL[d.entity_type] ?? d.entity_type}{" "}
              ／{" "}
              {d.profile_item_id
                ? `プロファイル登録済み（${d.profile_status}）`
                : "プロファイル無し"}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

function ItemRow({ item, onConfirmed }: { item: IntakeItem; onConfirmed: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const label = APPROVAL_LABEL[item.approval];
  const confirm = async () => {
    setBusy(true);
    setErr(null);
    try {
      await confirmAiApproval(item.knowledge_item_id, "ナレッジ追加画面から確認");
      onConfirmed();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="border border-border rounded-md p-3 space-y-2 text-sm">
      <div className="flex items-center gap-2 flex-wrap">
        <button
          type="button"
          className="font-medium text-left hover:underline"
          onClick={() => setOpen(!open)}
        >
          {item.title}
        </button>
        <Badge variant={label.variant}>{label.text}</Badge>
        <span className="text-xs text-muted-foreground">信頼度 {item.confidence ?? "-"}</span>
        <span className="text-xs text-muted-foreground ml-auto">
          {(item.created_at ?? "").slice(0, 10)}
        </span>
      </div>
      {open && (
        <div className="text-xs space-y-1">
          {item.objectives.length > 0 && <p>目的: {item.objectives.join(" ／ ")}</p>}
          {item.core_interests.length > 0 && <p>核心的利益: {item.core_interests.join(" ／ ")}</p>}
          {item.sources.map((s) => (
            <p key={s.url}>
              <a
                href={s.url}
                target="_blank"
                rel="noreferrer"
                className="text-primary hover:underline break-all"
              >
                [{s.n}] {s.title || s.url}
              </a>
            </p>
          ))}
        </div>
      )}
      {item.approval === "ai" && (
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" disabled={busy} onClick={confirm}>
            {busy ? "確認中…" : "人が確認済みにする"}
          </Button>
          <span className="text-[10px] text-muted-foreground">
            レビュー担当・管理担当の権限が必要です
          </span>
        </div>
      )}
      {err && <p className="text-xs text-destructive">{err}</p>}
    </div>
  );
}

function GeoKnowledgeIntakePage() {
  const { t } = useLanguage();
  const [messages, setMessages] = useState<Msg[]>([]);
  const [card, setCard] = useState<IntakeCard>(EMPTY_CARD);
  const [ready, setReady] = useState(false);
  const [duplicates, setDuplicates] = useState<IntakeDuplicate[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<IntakeJob | null>(null);
  const [items, setItems] = useState<IntakeItem[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);

  const reloadItems = useCallback(() => {
    listIntakeItems()
      .then((r) => setItems(r.items))
      .catch(() => undefined);
  }, []);
  useEffect(reloadItems, [reloadItems]);
  useEffect(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), [messages, busy]);

  // 調査ジョブの状況を、完了するまで3秒おきに確認する
  useEffect(() => {
    if (!job || job.status !== "running") return;
    const timer = setInterval(() => {
      getIntakeJob(job.job_id)
        .then((j) => {
          setJob(j);
          if (j.status !== "running") reloadItems();
        })
        .catch((e) => setJob({ ...job, status: "error", error: (e as Error).message }));
    }, 3000);
    return () => clearInterval(timer);
  }, [job, reloadItems]);

  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;
    const next: Msg[] = [...messages, { role: "user", content: text }];
    setMessages(next);
    setInput("");
    setBusy(true);
    setError(null);
    try {
      const res = await intakeChat(next, card);
      setMessages([...next, { role: "assistant", content: res.reply }]);
      setCard(res.card);
      setReady(res.ready);
      setDuplicates(res.duplicates);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const start = async () => {
    setError(null);
    try {
      const { job_id } = await startIntakeJob(card);
      setJob({ job_id, status: "running", stage: "開始しています", error: null, result: null });
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const reset = () => {
    setMessages([]);
    setCard(EMPTY_CARD);
    setReady(false);
    setDuplicates([]);
    setJob(null);
    setError(null);
  };

  const running = job?.status === "running";
  return (
    <>
      <TopBar breadcrumb={[{ label: t("nav.geoKnowledgeIntake") }]} />
      <main className="flex-1 max-w-[1100px] w-full mx-auto px-4 sm:px-8 py-8 space-y-6">
        <section className="border-b border-border pb-5">
          <h1 className="text-editorial text-4xl">ナレッジ追加</h1>
          <p className="text-sm text-muted-foreground mt-2">
            追加したいActor（人物・政府・組織など）をチャットで伝えると、Expert
            AIが要件を確認し、出典つきで調べて、AI作成のナレッジとして登録します。登録後に、人が確認済みにできます。
          </p>
          <p className="text-[10px] text-muted-foreground mt-1">
            外部の検索サービスへ送るのは、名前と視点だけです（社内資料は送りません）。
          </p>
        </section>

        <div className="grid gap-6 md:grid-cols-[1fr_340px]">
          <section className="space-y-3">
            <div className="border border-border rounded-md p-3 min-h-[260px] max-h-[460px] overflow-y-auto space-y-3 text-sm">
              {messages.length === 0 && (
                <p className="text-muted-foreground">
                  例: 「ハンガリーのオルバン首相を追加したい。対EU政策を重視したい」
                </p>
              )}
              {messages.map((m, i) => (
                <div key={i} className={m.role === "user" ? "text-right" : ""}>
                  <span
                    className={`inline-block rounded-md px-3 py-2 whitespace-pre-wrap text-left ${
                      m.role === "user" ? "bg-primary/10" : "bg-muted/50"
                    }`}
                  >
                    {m.content}
                  </span>
                </div>
              ))}
              {busy && <p className="text-xs text-muted-foreground">考えています…</p>}
              <div ref={bottomRef} />
            </div>
            <Textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="追加したい内容を入力（Ctrl+Enterで送信）"
              rows={3}
              disabled={running}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) send();
              }}
            />
            <div className="flex gap-2">
              <Button onClick={send} disabled={busy || running || !input.trim()}>
                送信
              </Button>
              <Button variant="ghost" onClick={reset} disabled={running}>
                最初からやり直す
              </Button>
            </div>
            {error && <p className="text-xs text-destructive">{error}</p>}
          </section>

          <aside className="space-y-3">
            <CardView card={card} duplicates={duplicates} />
            <Button
              className="w-full"
              disabled={!ready || running || job?.status === "done"}
              onClick={start}
            >
              {running ? "調査中…" : "この内容で調査して登録する"}
            </Button>
            {!ready && messages.length > 0 && (
              <p className="text-[10px] text-muted-foreground">
                名前と種類が揃うと、調査を始められます。
              </p>
            )}
            {job && (
              <div className="border border-border rounded-md p-3 text-sm space-y-1">
                <p className="text-xs font-medium text-muted-foreground">進行状況</p>
                <p>{job.stage}</p>
                {job.error && <p className="text-xs text-destructive">{job.error}</p>}
                {job.result && (
                  <div className="text-xs space-y-1 pt-1">
                    <p>
                      登録: {job.result.title}（出典 {job.result.sources.length}件）
                    </p>
                    {job.result.review && (
                      <p>
                        {job.result.review.approved
                          ? "AI暫定承認になりました。"
                          : job.result.review.reason}
                        {job.result.review.reasons.length > 0 && (
                          <span className="block text-muted-foreground">
                            {job.result.review.reasons.join(" ／ ")}
                          </span>
                        )}
                      </p>
                    )}
                  </div>
                )}
              </div>
            )}
          </aside>
        </div>

        <section className="space-y-3">
          <h2 className="text-lg font-medium">登録されたナレッジ</h2>
          {items.map((it) => (
            <ItemRow key={it.knowledge_item_id} item={it} onConfirmed={reloadItems} />
          ))}
          {items.length === 0 && <p className="text-sm text-muted-foreground">まだありません。</p>}
        </section>
      </main>
    </>
  );
}
