// Phase C: インテリジェンス運用（Pipeline Health）画面。
// 「担当者が朝開いて、止まっている所と次にやることが分かる」ことが目的。統計Dashboardではなく
// Action Queueを中心にする。表示はBackendのSummary 1本（LLMなし）。Theme同期の実行だけが
// 既存sync API（ユーザーが明示的に押した時のみLLMを使う）を呼ぶ。
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  PauseCircle,
  RefreshCw,
  XCircle,
} from "lucide-react";
import { TopBar } from "@/components/top-bar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { setHighlightCandidateId } from "@/lib/geoThemeHighlightStore";
import {
  useOperationsSummary,
  type OpsAction,
  type OpsCollection,
  type OpsLevel,
  type OpsPendingSync,
  type OpsScheduler,
  type OpsState,
  type OperationsSummary,
} from "@/lib/geoOperationsApi";
import {
  useBatchSyncPendingThemeCandidates,
  useSyncThemeCandidateForCluster,
  type GeoThemeBatchSyncResult,
  type GeoThemeSyncResult,
} from "@/lib/geoThemeCandidateApi";

// ── 小さな共通部品 ─────────────────────────────────────────────

function ago(iso: string | null | undefined): string {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return "—";
  const h = ms / 3600000;
  if (h < 1) return `${Math.max(1, Math.round(ms / 60000))}分前`;
  if (h < 48) return `${Math.round(h)}時間前`;
  return `${Math.round(h / 24)}日前`;
}

const LEVEL_STYLE: Record<OpsLevel, { dot: string; text: string; Icon: typeof CheckCircle2 }> = {
  ok: { dot: "text-emerald-600", text: "正常", Icon: CheckCircle2 },
  attention: { dot: "text-amber-500", text: "要対応", Icon: AlertTriangle },
  stopped: { dot: "text-red-600", text: "停止", Icon: XCircle },
  unknown: { dot: "text-muted-foreground", text: "不明", Icon: PauseCircle },
};

function SectionError({ message }: { message?: string }) {
  return (
    <p className="text-sm text-muted-foreground" data-testid="ops-section-error">
      この項目は取得できませんでした。{message ? `（${message}）` : ""}
      他の項目は表示しています。
    </p>
  );
}

function Section({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section id={id} className="card-paper rounded-lg p-5 space-y-3" data-testid={`ops-${id}`}>
      <h2 className="text-base font-semibold">{title}</h2>
      {children}
    </section>
  );
}

// ── 最上部: Pipeline Health ─────────────────────────────────

const HEALTH_ITEMS: Array<{ key: keyof OperationsSummary["health"]; label: string }> = [
  { key: "collection", label: "収集" },
  { key: "episode", label: "候補整理" },
  { key: "review", label: "レビュー" },
  { key: "theme_sync", label: "テーマ化" },
  { key: "monitoring", label: "モニタリング" },
];

function HealthStrip({ health }: { health: OperationsSummary["health"] }) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-5 gap-3" data-testid="ops-health">
      {HEALTH_ITEMS.map(({ key, label }) => {
        const s: OpsState = health[key];
        const st = LEVEL_STYLE[s.level];
        return (
          <div
            key={key}
            className="card-paper rounded-lg p-3"
            data-testid={`ops-health-${key}`}
            data-level={s.level}
          >
            <div className="text-xs text-muted-foreground">{label}</div>
            <div className="flex items-center gap-1.5 mt-1">
              <st.Icon className={`h-4 w-4 shrink-0 ${st.dot}`} />
              <span className="text-base font-semibold">{s.label}</span>
            </div>
            {s.detail && <div className="text-[11px] text-muted-foreground mt-0.5">{s.detail}</div>}
          </div>
        );
      })}
    </div>
  );
}

// ── 今日の運用状況（クリックで対象へ） ─────────────────────────

function DailySummary({
  daily,
  onGo,
}: {
  daily: OperationsSummary["daily"];
  onGo: (target: string) => void;
}) {
  const items: Array<{ label: string; value: number | null; target: string; warn: boolean }> = [
    { label: "レビュー待ち", value: daily.review_pending, target: "review", warn: true },
    { label: "Theme同期待ち", value: daily.theme_sync_pending, target: "theme_sync", warn: true },
    { label: "新規Theme候補（7日）", value: daily.new_themes, target: "candidates", warn: false },
    { label: "未確認Alert", value: daily.unacknowledged_alerts, target: "monitoring", warn: true },
    { label: "更新停止Source", value: daily.stale_sources, target: "collection", warn: true },
  ];
  return (
    <div data-testid="ops-daily">
      <div className="text-xs font-medium text-muted-foreground mb-2">今日の運用状況</div>
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        {items.map((i) => (
          <button
            key={i.label}
            type="button"
            onClick={() => onGo(i.target)}
            className="card-paper rounded-lg p-3 text-left hover:bg-muted/40 transition-colors"
            data-testid={`ops-daily-${i.target}`}
          >
            <div
              className={`text-2xl font-semibold tabular-nums leading-none ${
                i.warn && (i.value ?? 0) > 0 ? "text-amber-600" : ""
              }`}
            >
              {i.value ?? "—"}
            </div>
            <div className="text-[11px] text-muted-foreground mt-1">{i.label}</div>
          </button>
        ))}
      </div>
    </div>
  );
}

// ── 次にやること（Action Queue） ──────────────────────────────

function ActionQueue({
  actions,
  onAction,
}: {
  actions: OpsAction[];
  onAction: (a: OpsAction) => void;
}) {
  if (actions.length === 0) {
    return (
      <div className="card-paper rounded-lg p-5" data-testid="ops-queue-empty">
        <p className="flex items-center gap-2 text-sm font-medium">
          <CheckCircle2 className="h-4 w-4 text-emerald-600" />
          今すぐ対応が必要なものはありません。
        </p>
      </div>
    );
  }
  return (
    <div className="space-y-2" data-testid="ops-queue">
      {actions.map((a) => (
        <div
          key={a.key}
          className={`card-paper rounded-lg p-4 flex items-center justify-between gap-4 border-l-4 ${
            a.severity === "high" ? "border-l-red-500" : "border-l-amber-400"
          }`}
          data-testid={`ops-queue-${a.key}`}
        >
          <div className="min-w-0">
            <div className="text-sm font-semibold">{a.title}</div>
            {a.detail && <div className="text-xs text-muted-foreground mt-0.5">{a.detail}</div>}
          </div>
          <Button size="sm" onClick={() => onAction(a)} className="shrink-0">
            {a.cta.label}
          </Button>
        </div>
      ))}
    </div>
  );
}

// ── Theme同期（個別 / 一括） ─────────────────────────────────

type SyncTarget = { kind: "single"; item: OpsPendingSync } | { kind: "batch" } | null;

const OUTCOME_TEXT: Record<GeoThemeSyncResult["outcome"], string> = {
  created: "Theme Candidateを生成しました",
  merged: "すでに同じTheme Candidateがあるため、重複させずに再利用しました",
  insufficient_evidence: "候補なし：根拠が足りないため生成しませんでした",
  generation_failed: "失敗：AIの生成に失敗しました（Clusterは承認済みのままです。再試行できます）",
};

function describeItem(p: OpsPendingSync): string {
  const who = p.actors.join("・") || "(対象不明)";
  const where = p.regions.length ? ` / ${p.regions.join("・")}` : "";
  return `${who}${where}`;
}

function SyncDialog({
  target,
  pendingCount,
  onClose,
}: {
  target: SyncTarget;
  pendingCount: number;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const single = useSyncThemeCandidateForCluster();
  const batch = useBatchSyncPendingThemeCandidates();
  const busy = single.isPending || batch.isPending;
  const singleResult = single.data as GeoThemeSyncResult | undefined;
  const batchResult = batch.data as GeoThemeBatchSyncResult | undefined;
  const done = !!singleResult || !!batchResult;

  const close = () => {
    if (busy) return;
    single.reset();
    batch.reset();
    onClose();
  };

  const goDiscovery = (candidateId: string | null) => {
    if (candidateId) setHighlightCandidateId(candidateId);
    single.reset();
    batch.reset();
    onClose();
    navigate({ to: "/geo/theme-discovery" });
  };

  const run = () => {
    if (!target) return;
    if (target.kind === "single") single.mutate({ episodeClusterId: target.item.cluster_ids[0] });
    else batch.mutate({});
  };

  const firstCandidate = (batchResult?.results ?? []).find((r) => r.candidate_id)?.candidate_id;
  const error = (single.error ?? batch.error) as Error | null;

  return (
    <Dialog open={!!target} onOpenChange={(o) => !o && close()}>
      <DialogContent data-testid="ops-sync-dialog">
        <DialogHeader>
          <DialogTitle>
            {target?.kind === "batch" ? "未同期をまとめて生成" : "Theme Candidateを生成"}
          </DialogTitle>
          <DialogDescription>
            {target?.kind === "single" &&
              `対象: ${describeItem(target.item)}（根拠 ${target.item.evidence_count}件${
                target.item.cluster_ids.length > 1
                  ? `、関連${target.item.cluster_ids.length}件のまとまりを1つにします`
                  : ""
              }）`}
            {target?.kind === "batch" &&
              `承認済み・未同期の${pendingCount}件からTheme Candidateを生成します。対象：${pendingCount}件`}
          </DialogDescription>
        </DialogHeader>

        {!done && !error && (
          <p className="text-xs text-muted-foreground">
            AIが承認済みの根拠から候補を作ります。同じ内容の候補が既にある場合は重複させません。
          </p>
        )}

        {singleResult && (
          <div className="space-y-2 text-sm" data-testid="ops-sync-result">
            <p className="font-medium">{OUTCOME_TEXT[singleResult.outcome]}</p>
            {singleResult.candidate_id && (
              <Button size="sm" variant="outline" onClick={() => goDiscovery(singleResult.candidate_id)}>
                テーマ探索で確認
              </Button>
            )}
          </div>
        )}

        {batchResult && (
          <div className="space-y-2 text-sm" data-testid="ops-sync-result">
            <div className="grid grid-cols-3 gap-2 text-center">
              <div className="rounded-md border p-2">
                <div className="text-xl font-semibold tabular-nums">
                  {batchResult.counts.inserted_count + batchResult.counts.updated_count}
                </div>
                <div className="text-[11px] text-muted-foreground">成功</div>
              </div>
              <div className="rounded-md border p-2">
                <div className="text-xl font-semibold tabular-nums">{batchResult.counts.skipped_count}</div>
                <div className="text-[11px] text-muted-foreground">候補なし</div>
              </div>
              <div className="rounded-md border p-2">
                <div className="text-xl font-semibold tabular-nums">{batchResult.counts.error_count}</div>
                <div className="text-[11px] text-muted-foreground">失敗</div>
              </div>
            </div>
            {batchResult.counts.error_count > 0 && (
              <p className="text-xs text-muted-foreground">
                失敗した項目は承認済みのまま残り、「次にやること」から再試行できます。
              </p>
            )}
            {firstCandidate && (
              <Button size="sm" variant="outline" onClick={() => goDiscovery(firstCandidate)}>
                テーマ探索で確認
              </Button>
            )}
          </div>
        )}

        {error && (
          <p className="text-sm text-destructive" data-testid="ops-sync-error">
            失敗しました: {error.message}（Clusterは承認済みのままです。再試行できます）
          </p>
        )}

        <DialogFooter>
          {done ? (
            <Button onClick={close}>閉じる</Button>
          ) : (
            <>
              <Button variant="ghost" onClick={close} disabled={busy}>
                キャンセル
              </Button>
              <Button onClick={run} disabled={busy} data-testid="ops-sync-run">
                {busy ? (
                  <>
                    <Loader2 className="h-4 w-4 mr-1 animate-spin" />
                    生成中...
                  </>
                ) : error ? (
                  "再試行する"
                ) : (
                  "生成する"
                )}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ── ページ本体 ─────────────────────────────────────────────

export function OperationsPage() {
  const navigate = useNavigate();
  const { data, isLoading, isError, error, refetch, isFetching } = useOperationsSummary();
  const [syncTarget, setSyncTarget] = useState<SyncTarget>(null);

  const scrollTo = (id: string) =>
    document.getElementById(`ops-section-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  const go = (to: string) => navigate({ to: to as never });

  const onAction = (a: OpsAction) => {
    if (a.cta.kind === "link") go(a.cta.to);
    else if (a.cta.kind === "anchor") scrollTo(a.cta.to);
    else setSyncTarget({ kind: "batch" });
  };

  return (
    <>
      <TopBar breadcrumb={[{ label: "インテリジェンス運用" }]} />
      <main className="flex-1 max-w-[1000px] w-full mx-auto px-8 py-8 space-y-6" data-testid="ops-page">
        <section className="border-b border-border pb-5 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-editorial text-4xl">インテリジェンス運用</h1>
            <p className="text-sm text-muted-foreground mt-2">
              収集 → 候補整理 → レビュー → テーマ化 → モニタリング の流れで、止まっている所と次にやることを確認します。
            </p>
          </div>
          <div className="text-right shrink-0">
            <Button size="sm" variant="outline" onClick={() => refetch()} disabled={isFetching}>
              <RefreshCw className={`h-3.5 w-3.5 mr-1 ${isFetching ? "animate-spin" : ""}`} />
              更新
            </Button>
            {data && (
              <div className="text-[11px] text-muted-foreground mt-1">
                {new Date(data.generated_at).toLocaleTimeString("ja-JP")} 時点
              </div>
            )}
          </div>
        </section>

        {isLoading && (
          <p className="text-sm text-muted-foreground flex items-center gap-2" data-testid="ops-loading">
            <Loader2 className="h-4 w-4 animate-spin" />
            運用状況を読み込んでいます…
          </p>
        )}
        {isError && (
          <div className="card-paper rounded-lg p-5 space-y-2" data-testid="ops-error">
            <p className="text-sm font-medium">運用状況を取得できませんでした。</p>
            <p className="text-xs text-muted-foreground">{(error as Error).message}</p>
            <Button size="sm" variant="outline" onClick={() => refetch()}>
              再読み込み
            </Button>
          </div>
        )}

        {data && (
          <>
            <HealthStrip health={data.health} />
            <div>
              <div className="text-xs font-medium text-muted-foreground mb-2">次にやること</div>
              <ActionQueue actions={data.action_queue} onAction={onAction} />
            </div>
            <DailySummary daily={data.daily} onGo={scrollTo} />
            <Details
              data={data}
              onSync={(item) => setSyncTarget(item ? { kind: "single", item } : { kind: "batch" })}
              go={go}
            />
          </>
        )}
      </main>
      <SyncDialog
        target={syncTarget}
        pendingCount={data?.theme_sync?.pending_count ?? 0}
        onClose={() => setSyncTarget(null)}
      />
    </>
  );
}

const JOB_STATE_LABEL: Record<string, { text: string; cls: string }> = {
  scheduled: { text: "待機中", cls: "text-muted-foreground" },
  due: { text: "実行待ち", cls: "text-amber-600" },
  running: { text: "実行中", cls: "text-emerald-700" },
  stale: { text: "停止の疑い", cls: "text-red-600" },
  backoff: { text: "再試行待ち", cls: "text-amber-600" },
  exhausted: { text: "再試行を使い切り", cls: "text-red-600" },
  blocked: { text: "要対応（設定・認証）", cls: "text-red-600" },
  not_configured: { text: "未設定", cls: "text-muted-foreground" },
  disabled: { text: "無効", cls: "text-muted-foreground" },
};

const JOB_NAME: Record<string, string> = {
  COLLECTION_GDELT_DOC: "GDELT 記事（News DOC）",
  COLLECTION_GDELT_STREAM: "GDELT Event/Episode（候補整理まで）",
  COLLECTION_IMF: "IMF",
  COLLECTION_WORLD_BANK: "World Bank",
  COLLECTION_OFAC: "OFAC（制裁リスト・専用枠）",
  COLLECTION_UN_COMTRADE: "UN Comtrade",
  COLLECTION_WTO: "WTO",
  COLLECTION_MANIFESTO: "Manifesto",
};

function SchedulerBlock({
  scheduler,
  staleRunning,
}: {
  scheduler: OpsScheduler;
  staleRunning: NonNullable<OpsCollection["stale_running"]>;
}) {
  const status = scheduler.status ?? (scheduler.configured ? "healthy" : "not_configured");
  const label =
    status === "healthy" ? "稼働中" : status === "unhealthy" ? "停止の疑い" : "未設定（本番サーバへの登録待ち）";
  const jobs = (scheduler.jobs ?? []).filter((j) => j.state !== "disabled");
  return (
    <div className="space-y-1.5" data-testid="ops-scheduler" data-status={status}>
      <p className="text-sm">
        定期実行（Scheduler）：
        <span className={status === "healthy" ? "text-emerald-700" : status === "unhealthy" ? "text-red-600" : "text-muted-foreground"}>
          {label}
        </span>
        {scheduler.last_heartbeat_at && (
          <span className="text-muted-foreground ml-2">最終heartbeat {ago(scheduler.last_heartbeat_at)}</span>
        )}
        {scheduler.next_expected_at && (
          <span className="text-muted-foreground ml-2">
            次回予定 {new Date(scheduler.next_expected_at).toLocaleString("ja-JP")}
          </span>
        )}
      </p>
      <p className="text-[11px] text-muted-foreground">{scheduler.note}</p>
      {scheduler.gdelt_doc && (scheduler.gdelt_doc.cooldown_remaining_seconds > 0 || scheduler.gdelt_doc.collection_running) && (
        <p className="text-xs text-amber-700" data-testid="ops-gdelt-doc">
          GDELT 記事取得：
          {scheduler.gdelt_doc.collection_running ? "収集を実行中です（重ねて実行されません）。" : ""}
          {scheduler.gdelt_doc.cooldown_remaining_seconds > 0
            ? `GDELTの利用制限（429）のため休止中です（あと約${Math.ceil(scheduler.gdelt_doc.cooldown_remaining_seconds / 60)}分）。自動で再開します。`
            : ""}
        </p>
      )}
      {staleRunning.length > 0 && (
        <ul className="text-xs text-red-700" data-testid="ops-stale-running">
          {staleRunning.map((s) => (
            <li key={s.ingestion_run_id}>
              {JOB_NAME[s.job_id] ?? s.job_id}：{s.running_hours}時間「実行中」のままです（上限{s.stale_after_hours}時間）。
              次のtick、または recover-stale で補正されます。
            </li>
          ))}
        </ul>
      )}
      {jobs.length > 0 && (
        <details className="text-xs" data-testid="ops-scheduler-jobs">
          <summary className="cursor-pointer text-muted-foreground">Jobごとの状態（{jobs.length}件）</summary>
          <ul className="mt-1 space-y-0.5">
            {jobs.map((j) => {
              const st = JOB_STATE_LABEL[j.state] ?? { text: j.state, cls: "" };
              return (
                <li key={j.job_id} data-testid={`ops-job-${j.job_id}`} data-state={j.state}>
                  <span className="font-medium">{JOB_NAME[j.job_id] ?? j.job_id}</span>
                  <span className={`ml-2 ${st.cls}`}>{st.text}</span>
                  <span className="text-muted-foreground ml-2">
                    最終成功 {ago(j.last_success_at)}
                    {j.consecutive_failures > 0 ? `／連続失敗${j.consecutive_failures}回` : ""}
                  </span>
                </li>
              );
            })}
          </ul>
        </details>
      )}
    </div>
  );
}

// ── 詳細セクション ───────────────────────────────────────────

const SEVERITY_BADGE: Record<string, string> = { high: "重要", medium: "中", low: "低" };
const ORIGIN_LABEL: Record<string, string> = {
  situation: "Situation経由",
  article_discovery: "記事から発見",
  monitoring: "モニタリング",
  manual: "手動",
};
const STATUS_LABEL: Record<string, string> = {
  NEW: "提案・新規",
  SHORTLISTED: "候補",
  MONITORING: "モニタリング中",
  HANDED_OFF: "Expert AIへ引き継ぎ済み",
  DISMISSED: "却下",
};

function Details({
  data,
  onSync,
  go,
}: {
  data: OperationsSummary;
  onSync: (item: OpsPendingSync | null) => void;
  go: (to: string) => void;
}) {
  const { review, theme_sync: sync, theme_candidates: tc, monitoring: mon, collection: col, errors } = data;
  return (
    <div className="space-y-4">
      <Section id="section-review" title="レビュー待ち">
        {!review ? (
          <SectionError message={errors.review} />
        ) : (
          <>
            <p className="text-sm">
              <span className="text-2xl font-semibold tabular-nums mr-1">{review.pending}</span>件
              {review.oldest_waiting_days != null && review.pending > 0 && (
                <span className="text-muted-foreground ml-2">最も古い待ち：{review.oldest_waiting_days}日前</span>
              )}
            </p>
            {review.items.length > 0 && (
              <ul className="text-xs divide-y" data-testid="ops-review-items">
                {review.items.map((i) => (
                  <li key={i.episode_cluster_id} className="py-1.5 flex flex-wrap gap-x-3">
                    <span className="font-medium">{i.actor}</span>
                    <span className="text-muted-foreground">{i.regions.join("・") || "地域不明"}</span>
                    <Badge variant="outline">{i.episode_type}</Badge>
                    <span className="text-muted-foreground">根拠 {i.evidence_count}件</span>
                    <span className="text-muted-foreground">待ち {ago(i.waiting_since)}</span>
                  </li>
                ))}
              </ul>
            )}
            {review.last_review && (
              <p className="text-[11px] text-muted-foreground">
                直近のレビュー：{ago(review.last_review.reviewed_at)}（{review.last_review.reviewed_by ?? "不明"}／
                {review.last_review.status === "accepted" ? "承認" : review.last_review.status}）
              </p>
            )}
            <Button size="sm" variant="outline" onClick={() => go("/geo/episode-candidates")}>
              レビューする
            </Button>
          </>
        )}
      </Section>

      <Section id="section-theme_sync" title="承認済み・Theme同期待ち">
        {!sync ? (
          <SectionError message={errors.theme_sync} />
        ) : (
          <>
            {sync.pending_count === 0 ? (
              <p className="text-sm text-muted-foreground" data-testid="ops-sync-empty">
                Theme同期待ちはありません。
              </p>
            ) : (
              <>
                <ul className="text-xs divide-y" data-testid="ops-sync-items">
                  {sync.pending.map((p) => (
                    <li key={p.cluster_ids.join(",")} className="py-2 flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <div className="font-medium">{describeItem(p)}</div>
                        <div className="text-muted-foreground">
                          根拠 {p.evidence_count}件
                          {p.cluster_ids.length > 1 ? ` ／ 関連${p.cluster_ids.length}件をまとめて` : ""}
                        </div>
                        {p.retry && (
                          <div className="text-amber-600" data-testid="ops-sync-retry">
                            前回失敗しました。再試行できます{p.last_error ? `（${p.last_error}）` : ""}
                          </div>
                        )}
                      </div>
                      <Button size="sm" variant="outline" onClick={() => onSync(p)}>
                        {p.retry ? "再試行" : "Theme Candidateを生成"}
                      </Button>
                    </li>
                  ))}
                </ul>
                {sync.pending_count > 1 && (
                  <Button size="sm" onClick={() => onSync(null)} data-testid="ops-sync-batch">
                    未同期をまとめて生成
                  </Button>
                )}
              </>
            )}
            {sync.accepted_not_eligible > 0 && (
              <p className="text-[11px] text-muted-foreground">
                承認済みのうち{sync.accepted_not_eligible}件は、根拠が少ないためTheme化の対象外です（異常ではありません）。
              </p>
            )}
            {sync.recent_runs.length > 0 && (
              <details className="text-xs">
                <summary className="cursor-pointer text-muted-foreground">直近の生成履歴（{sync.recent_runs.length}件）</summary>
                <ul className="mt-1 space-y-0.5" data-testid="ops-sync-runs">
                  {sync.recent_runs.map((r) => (
                    <li key={r.run_id}>
                      {ago(r.started_at)}／{r.mode === "batch" ? "一括" : "個別"}／{r.requested_by ?? "—"}／ 生成{r.created}・再利用
                      {r.merged}・候補なし{r.no_candidate}・失敗{r.failed}
                      {r.error_summary ? `（${r.error_summary}）` : ""}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </>
        )}
      </Section>

      <Section id="section-candidates" title="Theme Candidateの状況">
        {!tc ? (
          <SectionError message={errors.theme_candidates} />
        ) : (
          <>
            <div className="flex flex-wrap gap-2 text-xs" data-testid="ops-candidate-status">
              {Object.entries(tc.by_status).map(([k, v]) => (
                <Badge key={k} variant="secondary">
                  {STATUS_LABEL[k] ?? k}：{v}
                </Badge>
              ))}
            </div>
            <div className="flex flex-wrap gap-2 text-xs text-muted-foreground" data-testid="ops-candidate-origin">
              入口別：
              {Object.entries(tc.by_origin).map(([k, v]) => (
                <span key={k}>
                  {ORIGIN_LABEL[k] ?? k} {v}件
                </span>
              ))}
            </div>
            <Button size="sm" variant="outline" onClick={() => go("/geo/theme-discovery")}>
              テーマ探索を開く
            </Button>
          </>
        )}
      </Section>

      <Section id="section-monitoring" title="モニタリングとAlert">
        {!mon ? (
          <SectionError message={errors.monitoring} />
        ) : (
          <>
            <p className="text-sm">
              モニタリング中 {mon.active_themes}テーマ／未確認Alert {mon.unacknowledged_alerts}件
            </p>
            {mon.alerts.length > 0 && (
              <ul className="text-xs divide-y" data-testid="ops-alerts">
                {mon.alerts.map((a) => (
                  <li key={a.alert_id} className="py-1.5 flex items-center justify-between gap-3">
                    <span>
                      <Badge variant={a.severity === "high" ? "destructive" : "outline"} className="mr-2">
                        {SEVERITY_BADGE[a.severity ?? ""] ?? "—"}
                      </Badge>
                      {a.theme_name}：{a.title}
                      <span className="text-muted-foreground ml-2">{ago(a.detected_at)}</span>
                    </span>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => go(`/geo/monitoring-themes/${a.monitoring_theme_id}`)}
                    >
                      確認する
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </Section>

      <Section id="section-collection" title="情報収集の状態">
        {!col ? (
          <SectionError message={errors.collection} />
        ) : (
          <>
            <p className="text-sm" data-testid="ops-collection-freshness">
              記事データ：
              {col.data_freshness.status === "stale" ? "更新が止まっています" : "更新されています"}
              <span className="text-muted-foreground ml-2">
                最新 {ago(col.data_freshness.last_data_at)}
              </span>
            </p>
            {col.problem_sources.length > 0 && (
              <div data-testid="ops-problem-sources">
                <div className="text-xs font-medium">
                  データ更新停止：{col.problem_sources.length} Source
                </div>
                <ul className="text-xs mt-1 space-y-0.5">
                  {col.problem_sources.map((s) => (
                    <li key={s.source_id}>
                      {s.name}
                      <span className="text-muted-foreground ml-2">
                        最終成功 {s.hours_since_success != null ? ago(s.last_success_at) : "なし"}
                        {s.kind === "failed" ? "（失敗が続いています）" : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {col.running.length > 0 && (
              <ul className="text-xs space-y-0.5" data-testid="ops-running">
                {col.running.map((r) => (
                  <li key={`${r.connector}-${r.started_at}`} className="flex items-center gap-1.5">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    {r.connector.toUpperCase()}：実行中（{ago(r.started_at)}から）
                    {r.stale ? (
                      <span className="text-red-600">止まっている可能性があります（回復対象）</span>
                    ) : (
                      r.too_long && <span className="text-amber-600">長時間かかっています</span>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {col.failed_runs.length > 0 && (
              <ul className="text-xs space-y-0.5" data-testid="ops-failed-runs">
                {col.failed_runs.map((r) => (
                  <li key={`${r.connector}-${r.at}`} className="text-red-700">
                    {r.connector.toUpperCase()}：直近の実行が失敗（{ago(r.at)}）
                    {r.message ? ` ${r.message.slice(0, 80)}` : ""}
                  </li>
                ))}
              </ul>
            )}
            <p className="text-[11px] text-muted-foreground">
              自動収集の対象 {col.auto_sources} Source（問題なし {col.fresh_sources}／未実行 {col.never_run_sources}）。
              問題のあるSourceだけを表示しています。
            </p>
            <SchedulerBlock scheduler={col.scheduler} staleRunning={col.stale_running ?? []} />
          </>
        )}
      </Section>
    </div>
  );
}
