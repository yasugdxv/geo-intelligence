// Report Studio UI全面見直し（2026-09-16）: 従来のWorkflowStepNavによる6ステップ
// （Design/Collect/Analyze/Compose/Review/Continue）タブ切り替えを廃止し、1画面の
// 縦積み（progressive disclosure）構成へ統合した。内部の技術工程
// （Design→Query→Collection→Preprocess→Prompt1→Aggregation→Prompt2→Report Edit→
// Continuity）はそのまま、Publish/Run（内部名）はユーザーには「Analysis」という
// 言葉で隠蔽する。
//
// Trust Boundary（最重要）: `AnalysisContext.runId`が確定しているかどうかだけで
// 「Current DesignをチャットUIで編集できるか」「Pinned Design Snapshotの読み取り
// 専用表示にするか」を決める（`mode`は「そのAnalysisにどう辿り着いたか」という
// 由来の記録のみで、編集可否には使わない）。runIdはこのコンポーネント内で
// `analysisContext`一本のみをSource of Truthとし、別のローカルstate
// （旧activeRunId等）を並行して持たない。
import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Progress } from "@/components/ui/progress";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { GeoCard, GeoCardBody, GeoCardHeader } from "@/components/geo/geo-card";
import {
  DesignAssetsCard,
  GeoReportDesignPanel,
  hasDesignChangesSinceFix,
  hasFixedDesign,
} from "@/components/geo/geo-report-design-panel";
import {
  useGeoReportRunExecutions,
  useRunAggregationThroughPrompt2,
  useRunPreprocess,
  useSetCanonicalExecution,
  type GeoReportExecution,
} from "@/lib/geoReportCurrentPipelineApi";
import {
  geoReportCombinedExportUrl,
  geoReportPrompt1ExportUrl,
  geoReportPrompt2ExportUrl,
  useGeoReportFinalReport,
  useImportCombinedPackage,
  useImportPrompt1Package,
  useImportPrompt2Package,
} from "@/lib/geoReportPromptExportApi";
import {
  useGeoReportEditHistory,
  useRequestReportEdit,
  useUseReportExecutionAsCurrent,
  type GeoReportEditResult,
} from "@/lib/geoReportEditApi";
import {
  useCancelRun,
  useCreateDesignPinnedRun,
  useDeleteRun,
  useGeoReportAssetHistory,
  useGeoReportDesignState,
  useGeoReportDesignVersions,
  usePublishGeoReportDesign,
  useRenameRun,
} from "@/lib/geoReportDesignApi";
import { useGeoReportRuns, type GeoReportCollectionRun } from "@/lib/geoReportStudioApi";
import { useLanguage } from "@/lib/i18n";

// Run IDのSource of Truth。`mode`は「そのAnalysisにどう辿り着いたか」という由来の
// 記録のみを担い、Design Chatの編集可否（Trust Boundary）には使わない——
// `runId === null`かどうかだけで判定する（状態機械章参照）。
export type AnalysisContext =
  { mode: "new"; runId: string | null } | { mode: "existing"; runId: string };

// geoReportStudioApi.tsのGeoReportCollectionRun型はDesign Pin導入前に書かれたため
// narrative_master_asset_idを宣言していないが、バックエンドはselect: "*"で返すため
// 実際のレスポンスには含まれている。旧Collection（geoReportStudioApi.useCreateGeoReportRun）
// で作られた未Pin Runと、design-pinnedで作られたPin済みRunが同じテーブル・同じ一覧
// エンドポイントに混在するため、ここで判別する（Workflowは常にPin済みRunのみを扱う。
// 未Pin RunをPreprocessへ渡すと`narrative_master_asset_idが固定されていません`という
// 分かりにくいエラーになるため、選択肢自体に出さない）。
export function isPinnedRun(run: GeoReportCollectionRun): boolean {
  return Boolean(run.narrative_master_asset_id);
}

function statusBadgeVariant(status: string): "default" | "secondary" | "outline" | "destructive" {
  if (status === "completed") return "secondary";
  if (status === "failed" || status === "stale") return "destructive";
  if (status === "running") return "default";
  return "outline";
}

function findExecution(
  executions: GeoReportExecution[] | undefined,
  stage: GeoReportExecution["stage"],
): GeoReportExecution | undefined {
  if (!executions) return undefined;
  const candidates = executions.filter((e) => e.stage === stage);
  return candidates.sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
}

// ZIP Export時の無表示問題の修正（2026-10-02、ユーザー報告「ダウンロードできない」）:
// Combined/Prompt1/Prompt2のExportは実データ量次第で数十秒かかる（実測: US中間選挙1の
// Combined Exportで約57秒・81MB）が、従来は素の<a href>リンクだったため、クリックしても
// レスポンスが返るまでブラウザに一切の進捗表示が出ず、「動いていない」ように見えていた
// （実際にはBackend側は200を返しておりBugではない——リンク先のexport endpoint自体は
// 変更しない）。fetch→blob→一時<a download>に差し替え、取得中はLoader2スピナーを出す。
function ExportZipLink({ url, label }: { url: string; label: string }) {
  const { t } = useLanguage();
  const [isDownloading, setIsDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleClick = async (e: React.MouseEvent) => {
    e.preventDefault();
    if (isDownloading) return;
    setError(null);
    setIsDownloading(true);
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`Export failed (HTTP ${res.status})`);
      const blob = await res.blob();
      const disposition = res.headers.get("content-disposition") ?? "";
      const match = disposition.match(/filename="?([^"]+)"?/);
      const filename = match?.[1] ?? "export.zip";
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Export failed");
    } finally {
      setIsDownloading(false);
    }
  };

  return (
    <div className="space-y-1">
      <button
        type="button"
        onClick={handleClick}
        disabled={isDownloading}
        className="text-xs underline underline-offset-2 flex items-center gap-1.5 disabled:opacity-60 disabled:cursor-wait"
      >
        {isDownloading && <Loader2 className="h-3 w-3 animate-spin" />}
        {isDownloading ? t("geo.reportStudio.pipeline.exporting") : label}
      </button>
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}

// 1画面統合（2026-09-16）: `runId`propとURLのsearch.runIdは初回Deep Link/Reload
// 復元用の初期値としてのみ使う（Report Studio UI全面見直しPlan「ルートとのRun ID
// 受け渡しについて」参照）。マウント後は`analysisContext`だけがRun IDのSource of
// Truthで、`onSelectRun`はURL同期のためだけに呼ぶ（逆方向の同期は行わない）。
export function GeoReportPipelinePanel({
  projectId,
  runId,
  onSelectRun,
}: {
  projectId: string;
  runId: string;
  onSelectRun: (id: string) => void;
}) {
  const { t } = useLanguage();
  const runsQuery = useGeoReportRuns(projectId);
  const versionsQuery = useGeoReportDesignVersions(projectId);
  const designStateQuery = useGeoReportDesignState(projectId);
  const publishMutation = usePublishGeoReportDesign(projectId);
  const createRunMutation = useCreateDesignPinnedRun(projectId);
  const runPreprocessMutation = useRunPreprocess(projectId);

  // 初回マウント時のみ、ルートから渡された初期runIdを反映する（Deep Link/Reload復元）。
  // 以後はこのanalysisContextだけがRun IDのSource of Truthになる。
  const [analysisContext, setAnalysisContext] = useState<AnalysisContext>(() =>
    runId ? { mode: "existing", runId } : { mode: "new", runId: null },
  );
  const [files, setFiles] = useState<File[]>([]);
  const [periodStart, setPeriodStart] = useState("");
  const [periodEnd, setPeriodEnd] = useState("");
  const [orchestratingStage, setOrchestratingStage] = useState<
    "idle" | "publishing" | "creating_run" | "preprocessing"
  >("idle");
  // 2026-09-17追加、ユーザー報告「再実行押したけど、動いてる？」: 従来はmutateAsyncの
  // 失敗がtry/finallyのfinallyでorchestratingStageをidleへ戻すだけで、失敗理由は
  // どこにも表示されずユーザーからは「押しても何も起きない」ように見えていた
  // （実際はCSVキャッシュが見つからず400を返していた）。画面上に理由を出す。
  const [orchestrationError, setOrchestrationError] = useState<string | null>(null);

  // 2026-09-16修正、ユーザー報告「新しい画面と古い画面が行ったり来たりしている」:
  // ルートの`runId`propは、URLに?runIdが無い場合、`useGeoReportRuns`のfetch完了後に
  // 初めて最新Runのidへ非同期に更新される（geo.report-studio.$projectId.tsxの
  // useEffect参照）。上のuseState初期化子は最初のレンダー時点のprop値しか見ないため、
  // 初回マウント時にpropがまだ空文字列だった場合、analysisContextは
  // {mode:"new", runId:null}に固定されたまま、後から届く実際のrunIdを一切反映しない
  // ——つまりRunが既に存在するProjectを開いても、いつまでも「新規Analysis作成」の
  // 画面（編集可能なDesign Chat）のままになる。ユーザーが手動でPrevious Analysesから
  // 選ぶと一瞬「既存」に切り替わるように見えるが、何かの再マウント（タブ切替等）で
  // 上のuseState初期化子が再評価されると、その時点のprop値次第で再び「新規」へ戻る
  // ——これが「新旧画面が行ったり来たりする」という報告の実体。
  // 対策: ユーザーが明示的にAnalysisを切り替えた後は、propの変化を追随しない
  // （userChoseAnalysisRef）。それより前は、propが（非同期に）解決した値を1回だけ
  // 追随して取り込む。
  const userChoseAnalysisRef = useRef(false);
  useEffect(() => {
    if (userChoseAnalysisRef.current) return; // ユーザーが既に明示的に選択済みなら上書きしない
    if (!runId) return; // まだpropが解決していない（空文字列）間は何もしない
    setAnalysisContext({ mode: "existing", runId });
  }, [runId]);

  const effectiveRunId = analysisContext.runId;
  // forcePoll: Preprocessをこのセッションで自分から起動した直後は、Execution行が
  // まだ1件も無い（または直前の一覧取得がそれを反映していない）一瞬の間に
  // 「実行中のExecutionが無い」と判定されてポーリングそのものが止まってしまう
  // レースを避ける（useGeoReportRunExecutionsのforcePollオプション、既存実装）。
  // これが無いと、まさにこの一瞬に初回ポーリングが行われた場合、進捗バーが二度と
  // 表示されなくなる（ユーザー報告「処理進捗がわからない」の実体の一つ）。
  const executionsQuery = useGeoReportRunExecutions(projectId, effectiveRunId ?? "", {
    forcePoll: orchestratingStage === "preprocessing",
  });
  const executions = effectiveRunId ? executionsQuery.data?.executions : undefined;
  const currentRun = runsQuery.data?.runs.find((r) => r.run_id === effectiveRunId);

  const hasPublishedDesign = (versionsQuery.data?.versions.length ?? 0) > 0;
  const readiness = computeReadiness(hasPublishedDesign, currentRun);
  const designState = designStateQuery.data?.design_state;
  const isDesignFixed = !!designState && hasFixedDesign(designState);
  const isDesignDirty = !!designState && hasDesignChangesSinceFix(designState);
  // 「FIX済みだがまだPublishされていない」場合のみtrue。Dirty（未確定の変更あり）は
  // ここに含めない——Publishを承認の代替にしないため、Dirtyのままpublishへ進ませない
  // （呼び出し側のガードで別途ブロックする、下記startPreprocessing参照）。
  const needsPublish = isDesignFixed && !isDesignDirty && !hasPublishedDesign;
  const canStartPreprocess =
    isDesignFixed && !isDesignDirty && files.length > 0 && !!periodStart && !!periodEnd;

  const pinnedVersion = versionsQuery.data?.versions.find(
    (v) => v.id === currentRun?.design_version_id,
  );

  const csvInputRef = useRef<HTMLInputElement>(null);
  const periodStartRef = useRef<HTMLInputElement>(null);

  const handleSelectExistingAnalysis = (id: string) => {
    userChoseAnalysisRef.current = true;
    setFiles([]);
    setAnalysisContext({ mode: "existing", runId: id });
    onSelectRun(id);
  };
  const handleNewAnalysis = () => {
    userChoseAnalysisRef.current = true;
    setFiles([]);
    setAnalysisContext({ mode: "new", runId: null });
    onSelectRun("");
  };
  // Continue Stepで「次のRunを作成」した場合も、そのRunはDesignが既にPin済み
  // （Trust Boundary上はPinned Design Snapshot扱い）なので既存Analysis選択と同じ
  // 経路にする。
  const handleRunCreatedElsewhere = (id: string) => {
    userChoseAnalysisRef.current = true;
    setFiles([]);
    setAnalysisContext({ mode: "existing", runId: id });
    onSelectRun(id);
  };

  // --- Design作成フェーズ専用（runId===nullの場合のみ呼ばれる）。 ---
  async function startPreprocessing() {
    if (analysisContext.runId !== null) return; // システム上のガード（UIのdisabledとは別）
    if (orchestratingStage !== "idle") return; // ダブルクリック防止
    if (!isDesignFixed) return; // 未FIXなら実行しない（UIのdisabledと同じ条件を再確認）
    if (isDesignDirty) return; // Dirtyなら再FIXを促すのみ（Assetsカード側の案内に委ねる）
    if (files.length === 0) {
      csvInputRef.current?.focus();
      return;
    }
    if (!periodStart || !periodEnd) {
      periodStartRef.current?.focus();
      return;
    }
    setOrchestrationError(null);
    try {
      if (needsPublish) {
        setOrchestratingStage("publishing");
        await publishMutation.mutateAsync(undefined);
      }
      setOrchestratingStage("creating_run");
      const { run } = await createRunMutation.mutateAsync({ periodStart, periodEnd });
      const newRunId = run.run_id; // ← このターン内のローカル変数を直接使う（Reactの状態
      // 更新の反映を待たない）。設計上、CreateRun成功時点でmode:"new"のまま runId だけを
      // 確定させる——filesには触れない（次のPreprocess呼び出しでそのまま使うため）。
      userChoseAnalysisRef.current = true;
      setAnalysisContext({ mode: "new", runId: newRunId });
      onSelectRun(newRunId);
      setOrchestratingStage("preprocessing");
      await runPreprocessMutation.mutateAsync({ runId: newRunId, files });
    } catch (err) {
      setOrchestrationError(err instanceof Error ? err.message : String(err));
    } finally {
      setOrchestratingStage("idle");
    }
  }

  // --- runId確定後（新規Analysis作成直後・既存Analysis選択のどちらでも共通）。
  //     Publish/CreateRunには一切触れない、そのAnalysisへ直接Preprocessを（再）実行するだけ。
  // resumeExecutionId（2026-09-16追加、ユーザー要望「途中で保存して失敗したら
  // そこから再開できないの」）: PreprocessActionCard側が、直前のPreprocess
  // Executionがstatus==="failed"の場合にそのidを渡す。バックエンドが完了済みの
  // フェーズ・chunkをスキップして続きから再開するため、失敗直前まで進んだ大量の
  // chunkを毎回やり直す無駄を避けられる。
  async function retryPreprocessForPinnedAnalysis(pinnedRunId: string, resumeExecutionId?: string) {
    if (orchestratingStage !== "idle") return; // ダブルクリック防止
    // 2026-09-17変更、ユーザー指摘「途中まで実行してたのを再開するのに新しくCSVを
    // アップロードするの？別のファイルだとデータが混ざるリスクがあるよね」:
    // resumeExecutionIdがある（＝以前失敗したExecutionを再開する）場合、サーバー側が
    // 最初のアップロード時にキャッシュした元のCSVを自動的に再利用するため、CSVの
    // 再選択は不要。新規（resumeExecutionId無し）の場合のみCSVを必須にする。
    if (!resumeExecutionId && files.length === 0) {
      csvInputRef.current?.focus();
      return;
    }
    setOrchestrationError(null);
    setOrchestratingStage("preprocessing");
    try {
      await runPreprocessMutation.mutateAsync({ runId: pinnedRunId, files, resumeExecutionId });
    } catch (err) {
      setOrchestrationError(err instanceof Error ? err.message : String(err));
    } finally {
      setOrchestratingStage("idle");
    }
  }

  return (
    <div className="space-y-4">
      <AnalysisSelectorBar
        projectId={projectId}
        analysisContext={analysisContext}
        onSelectExisting={handleSelectExistingAnalysis}
        onSelectNew={handleNewAnalysis}
      />

      <GeoReportDesignPanel
        projectId={projectId}
        editable={effectiveRunId === null}
        pinnedVersion={pinnedVersion}
        pinnedVariant={analysisContext.mode === "new" ? "new" : "existing"}
        onPreprocessIntent={startPreprocessing}
        onStartNewAnalysis={handleNewAnalysis}
      />

      {effectiveRunId === null && <DesignAssetsCard projectId={projectId} />}

      <PreprocessActionCard
        runId={effectiveRunId}
        files={files}
        onFilesChange={setFiles}
        periodStart={periodStart}
        onPeriodStartChange={setPeriodStart}
        periodEnd={periodEnd}
        onPeriodEndChange={setPeriodEnd}
        isDesignFixed={isDesignFixed}
        isDesignDirty={isDesignDirty}
        canStartPreprocess={canStartPreprocess}
        orchestratingStage={orchestratingStage}
        orchestrationError={orchestrationError}
        onStartPreprocessing={startPreprocessing}
        onRetryPreprocess={retryPreprocessForPinnedAnalysis}
        executions={executions}
        csvInputRef={csvInputRef}
        periodStartRef={periodStartRef}
      />

      {effectiveRunId && (
        <>
          <AnalyzeStepPanel
            projectId={projectId}
            runId={effectiveRunId}
            run={currentRun}
            executions={executions}
          />
          {readiness.compose && (
            <ComposeStepPanel
              projectId={projectId}
              runId={effectiveRunId}
              run={currentRun}
              executions={executions}
            />
          )}
          {readiness.review && <ReviewStepPanel projectId={projectId} runId={effectiveRunId} />}
          {readiness.continue && (
            <ContinueStepPanel
              projectId={projectId}
              runId={effectiveRunId}
              run={currentRun}
              readiness={readiness.continue}
              onSelectRun={handleRunCreatedElsewhere}
            />
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 分析切り替えバー（旧RunPickerCard、2026-09-16再設計）: Publish/Run/Pinという
// 内部概念をユーザーに出さない——「Run」ではなく「Analysis」という言葉を使う。
// Run作成そのものは、期間入力＋CSV＋「前処理を開始」（PreprocessActionCard）が
// 内部で自動的に行うため、ここには手動のRun作成フォーム（baseline/period/name）は
// 置かない（旧仕様から撤廃）。
// ---------------------------------------------------------------------------

function AnalysisSelectorBar({
  projectId,
  analysisContext,
  onSelectExisting,
  onSelectNew,
}: {
  projectId: string;
  analysisContext: AnalysisContext;
  onSelectExisting: (id: string) => void;
  onSelectNew: () => void;
}) {
  const { t } = useLanguage();
  const runsQuery = useGeoReportRuns(projectId);
  const renameMutation = useRenameRun(projectId);
  const deleteMutation = useDeleteRun(projectId);
  const [editingRunId, setEditingRunId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState("");
  const [expanded, setExpanded] = useState(false);

  const pinnedRuns = (runsQuery.data?.runs ?? []).filter(isPinnedRun);
  const selectedRunId = analysisContext.runId;
  const currentRun = pinnedRuns.find((r) => r.run_id === selectedRunId);

  return (
    <GeoCard>
      <GeoCardBody className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-sm">
            {selectedRunId && currentRun ? (
              <>
                <span className="font-medium">
                  {currentRun.run_name || t("geo.reportStudio.pipeline.currentAnalysis")}
                </span>
                {currentRun.period_start && currentRun.period_end && (
                  <span className="text-muted-foreground ml-2 text-xs">
                    {currentRun.period_start} – {currentRun.period_end}
                  </span>
                )}
                <span className="text-muted-foreground ml-2 text-xs">{currentRun.status}</span>
              </>
            ) : (
              <span className="text-muted-foreground">
                {t("geo.reportStudio.pipeline.newAnalysis")}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button size="sm" variant="outline" onClick={onSelectNew}>
              {t("geo.reportStudio.pipeline.newAnalysisButton")}
            </Button>
            <button
              className="text-xs underline underline-offset-2 text-muted-foreground"
              onClick={() => setExpanded((v) => !v)}
            >
              {expanded
                ? t("geo.reportStudio.pipeline.hidePreviousAnalyses")
                : t("geo.reportStudio.pipeline.showPreviousAnalyses")}
            </button>
          </div>
        </div>
        {expanded && (
          <div className="flex flex-wrap gap-2 pt-2 border-t border-border">
            {pinnedRuns.map((r) => (
              <div
                key={r.run_id}
                className={`flex items-center gap-1 text-xs px-2 py-1 rounded-md border border-border ${
                  selectedRunId === r.run_id ? "bg-muted" : ""
                }`}
              >
                {editingRunId === r.run_id ? (
                  <>
                    <Input
                      autoFocus
                      value={editingName}
                      onChange={(e) => setEditingName(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && editingName.trim()) {
                          renameMutation.mutate(
                            { runId: r.run_id, runName: editingName.trim() },
                            { onSuccess: () => setEditingRunId(null) },
                          );
                        }
                        if (e.key === "Escape") setEditingRunId(null);
                      }}
                      className="h-6 text-xs px-1.5 max-w-[160px]"
                    />
                    <button
                      className="text-muted-foreground hover:text-foreground"
                      disabled={renameMutation.isPending || !editingName.trim()}
                      onClick={() =>
                        renameMutation.mutate(
                          { runId: r.run_id, runName: editingName.trim() },
                          { onSuccess: () => setEditingRunId(null) },
                        )
                      }
                    >
                      ✓
                    </button>
                    <button
                      className="text-muted-foreground hover:text-foreground"
                      onClick={() => setEditingRunId(null)}
                    >
                      ✕
                    </button>
                  </>
                ) : (
                  <>
                    <button onClick={() => onSelectExisting(r.run_id)}>
                      {r.run_name || r.run_id.slice(0, 8)} — {r.status}
                    </button>
                    <button
                      className="text-muted-foreground hover:text-foreground"
                      title={t("geo.reportStudio.pipeline.renameRun")}
                      onClick={() => {
                        setEditingRunId(r.run_id);
                        setEditingName(r.run_name || "");
                      }}
                    >
                      ✎
                    </button>
                    <button
                      className="text-muted-foreground hover:text-destructive"
                      title={t("geo.reportStudio.pipeline.deleteRun")}
                      onClick={() => {
                        if (window.confirm(t("geo.reportStudio.pipeline.deleteRunConfirm"))) {
                          deleteMutation.mutate(r.run_id, {
                            onSuccess: () => {
                              if (selectedRunId === r.run_id) onSelectNew();
                            },
                          });
                        }
                      }}
                    >
                      🗑
                    </button>
                  </>
                )}
              </div>
            ))}
            {pinnedRuns.length === 0 && (
              <p className="text-sm text-muted-foreground">{t("geo.reportStudio.noRunsYet")}</p>
            )}
          </div>
        )}
        {deleteMutation.isError && (
          <p className="text-xs text-destructive">{(deleteMutation.error as Error).message}</p>
        )}
      </GeoCardBody>
    </GeoCard>
  );
}

// ---------------------------------------------------------------------------
// readinessは既存DBの状態（Runのcanonical_*列・Executions）から導出するだけで、
// UI独自の進捗状態は一切持たない——リロードしても壊れない。「タブの活性/非活性」
// ではなく「そのセクションを描画するかどうか」の条件として使う（2026-09-16、
// WorkflowStepNav撤廃に伴い用途変更）。
// ---------------------------------------------------------------------------

interface WorkflowReadiness {
  design: boolean;
  collect: boolean;
  analyze: boolean;
  compose: boolean;
  review: boolean;
  continue: boolean;
}

function computeReadiness(
  hasPublishedDesign: boolean,
  run: GeoReportCollectionRun | undefined,
): WorkflowReadiness {
  return {
    design: true,
    collect: hasPublishedDesign,
    analyze: !!run,
    compose: !!run?.canonical_prompt1_execution_id,
    review: !!run?.canonical_report_execution_id,
    continue: !!run?.canonical_prompt2_execution_id,
  };
}

// 2026-09-15追加、ユーザー報告: 社内プロキシのハングでPreprocessが「6/6ファイル処理済み」
// のままstatus='running'に張り付き続けたが、UI上は普通に進行中と見分けがつかなかった。
// 経過時間がこのしきい値を超えてもrunningのままなら「停滞の可能性」を明示する
// （geo_db_clientのconnect/readタイムアウト+3回リトライでも通常224秒程度で決着するため、
// 5分を超えて動きがなければ通常のリトライでは説明がつかない）。
const STALL_WARNING_MS = 5 * 60 * 1000;

// Preprocessの永続化フェーズ名（2026-09-15追加）→ i18nキーのマッピング。
// 未知のphase値（旧データ・他stageのprogress等）はフォールバックとして生の値をそのまま出す。
const PREPROCESS_PHASE_LABEL_KEYS: Record<string, string> = {
  collecting_files: "geo.reportStudio.pipeline.phase.collectingFiles",
  saving_analysis_clusters: "geo.reportStudio.pipeline.phase.savingAnalysisClusters",
  saving_analysis_representatives: "geo.reportStudio.pipeline.phase.savingAnalysisRepresentatives",
  saving_sentiment_clusters: "geo.reportStudio.pipeline.phase.savingSentimentClusters",
  saving_sentiment_representatives:
    "geo.reportStudio.pipeline.phase.savingSentimentRepresentatives",
  saving_record_map: "geo.reportStudio.pipeline.phase.savingRecordMap",
  building_artifacts: "geo.reportStudio.pipeline.phase.buildingArtifacts",
  // ChatGPT Prompt1 Importの進捗（2026-09-16追加、geo_report_prompt_export_service.
  // import_prompt1_result）。既存のprogressフィールドをそのまま流用しているため、
  // 表示名だけここに足せば済む。
  importing_sentiment_classifications:
    "geo.reportStudio.pipeline.phase.importingSentimentClassifications",
  importing_analysis_classifications:
    "geo.reportStudio.pipeline.phase.importingAnalysisClassifications",
  importing_targeted_stance: "geo.reportStudio.pipeline.phase.importingTargetedStance",
};

// 2026-09-16追加、ユーザー報告「あといくつあるのかわからない」「全部分ステータス
// 表示できない？」: 1本のバーだけでは、今のdone/totalがどのphaseの値なのか・
// 全体で何工程あるうち今どこかが分からなかった（例: 「6/6ファイル」が完了した
// 直後に別のphaseへ切り替わり、バーがdone=0からやり直しに見える）。バックエンドの
// 実行順（geo_report_current_pipeline_persistence_service.run_and_persist_
// preprocess・geo_report_prompt_export_service.import_prompt1_result）と一致する
// 固定の工程順を持たせ、済/現在/未着手をチェックリストとして常時表示する。
const PREPROCESS_PHASE_ORDER = [
  "collecting_files",
  "saving_analysis_clusters",
  "saving_analysis_representatives",
  "saving_sentiment_clusters",
  "saving_sentiment_representatives",
  "saving_record_map",
  "building_artifacts",
];
const PROMPT1_CHATGPT_IMPORT_PHASE_ORDER = [
  "importing_sentiment_classifications",
  "importing_analysis_classifications",
  "importing_targeted_stance",
];

function _phaseOrderFor(execution?: GeoReportExecution): string[] | null {
  if (execution?.stage === "preprocess") return PREPROCESS_PHASE_ORDER;
  if (execution?.stage === "prompt1" && execution.execution_method === "chatgpt_import") {
    return PROMPT1_CHATGPT_IMPORT_PHASE_ORDER;
  }
  return null;
}

function ExecutionProgressBar({ execution }: { execution?: GeoReportExecution }) {
  const { t } = useLanguage();
  const progress = execution?.progress as Record<string, unknown> | null | undefined;
  const done = Number(
    progress?.files_processed ?? progress?.batches_completed ?? progress?.sections_completed ?? 0,
  );
  const total = Number(
    progress?.files_total ?? progress?.batches_total ?? progress?.sections_total ?? 0,
  );
  const phase = typeof progress?.phase === "string" ? progress.phase : null;
  // 2026-09-16追加、ユーザー要望「ファイル毎に進捗があるといいかも」: バックエンドの
  // update_preprocess_progressは元々ファイル単位でcurrent_fileを記録している
  // （geo_report_current_pipeline_persistence_service.py）が、これまでフロント側で
  // 読んでいなかった（done/totalの数だけ表示）。今どのファイルを処理しているかを
  // 追加表示する。
  const currentFile = typeof progress?.current_file === "string" ? progress.current_file : null;

  // 2026-09-16修正: 「開始時刻からの経過時間」だと大規模Runで正常進行中でも
  // 必ず5分超になり誤警告が出ていた。「進捗(done/total/phase)が最後に変化してから
  // の経過時間」で停滞を判定するよう変更。
  const progressSignature = `${phase ?? ""}:${done}:${total}`;
  const lastChangeRef = useRef<{ signature: string; changedAtMs: number } | null>(null);
  const nowMs = Date.now();
  if (lastChangeRef.current === null || lastChangeRef.current.signature !== progressSignature) {
    lastChangeRef.current = { signature: progressSignature, changedAtMs: nowMs };
  }
  const isStalled = nowMs - lastChangeRef.current.changedAtMs > STALL_WARNING_MS;

  if (execution?.status !== "running") return null;
  const phaseLabel = phase
    ? PREPROCESS_PHASE_LABEL_KEYS[phase]
      ? t(PREPROCESS_PHASE_LABEL_KEYS[phase])
      : phase
    : null;
  const phaseOrder = _phaseOrderFor(execution);
  const currentPhaseIndex = phase && phaseOrder ? phaseOrder.indexOf(phase) : -1;
  return (
    <div className="space-y-1">
      {/* 2026-09-16追加: 全工程のチェックリスト（済/現在/未着手）。順序が既知の
          stage（preprocess／prompt1のChatGPT Import）でのみ表示し、他は従来通り
          現在のphaseラベルのみを出す。 */}
      {phaseOrder && (
        <div className="space-y-0.5">
          {phaseOrder.map((p, i) => {
            const label = t(PREPROCESS_PHASE_LABEL_KEYS[p] ?? p);
            const isDone = currentPhaseIndex >= 0 && i < currentPhaseIndex;
            const isCurrent = i === currentPhaseIndex;
            return (
              <p
                key={p}
                className={`text-[10px] ${
                  isCurrent
                    ? "text-foreground font-medium"
                    : isDone
                      ? "text-muted-foreground line-through"
                      : "text-muted-foreground"
                }`}
              >
                {isDone ? "✓" : isCurrent ? "▶" : "○"} {label}
              </p>
            );
          })}
          {currentPhaseIndex >= 0 && (
            <p className="text-[10px] text-muted-foreground">
              {t("geo.reportStudio.pipeline.phaseProgress", {
                current: currentPhaseIndex + 1,
                total: phaseOrder.length,
              })}
            </p>
          )}
        </div>
      )}
      {!phaseOrder && phaseLabel && (
        <p className="text-[10px] text-muted-foreground">{phaseLabel}</p>
      )}
      {total > 0 && (
        <>
          <Progress value={(done / total) * 100} />
          <p className="text-[10px] text-muted-foreground">
            {done} / {total}
            {currentFile &&
              ` — ${t("geo.reportStudio.pipeline.processingFile", { file: currentFile })}`}
          </p>
        </>
      )}
      {isStalled && (
        <p className="text-[10px] text-destructive">
          {t("geo.reportStudio.pipeline.executionMayBeStalled")}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Preprocess Actionカード（2026-09-16新規、旧CollectStepPanelを置き換え）。
// runId===null（Design作成フェーズ）の間は「次に分析データを準備します」として
// 期間入力＋CSV＋Publish→CreateRun→Preprocessのオーケストレーションを一括で
// 起動するボタンを出す。runId!==null（Run作成直後・既存Analysis選択のどちらでも
// 共通、Pinned Design + Run Status）の間はRecovery Action（期間入力不要、
// CSV再選択＋前処理を開始/再実行のみ、Publish/CreateRunは一切呼ばない）に切り替わる。
// Preprocessが既に完了している場合はこのカード自体を表示しない（ChatGPT用ZIP
// セクションに引き継ぐ）。
// ---------------------------------------------------------------------------

// CSVファイルのステージング欄（2026-09-16追加、ユーザー報告:「アップロードは続けて
// 複数あっぷできるの？ 何がアップされているかわからないと困るよ」）。
// 素の<input type="file" multiple>は選び直すたびに前回の選択を丸ごと置き換えて
// しまい（ブラウザ標準の挙動）、かつ選択中ファイル名を表示する手段が無かった——
// 複数回に分けてファイルを追加できず、今何がステージングされているかも見えない
// という2つの不満に直結していた。ここでは選択のたびに既存files配列へ追記し
// （同名・同サイズ・同lastModifiedの重複は追加しない）、常にファイル名一覧＋
// 個別削除ボタンを表示する。
function _fileKey(f: File): string {
  return `${f.name}:${f.size}:${f.lastModified}`;
}

function _formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

function CsvFileStager({
  files,
  onFilesChange,
  inputRef,
}: {
  files: File[];
  onFilesChange: (files: File[]) => void;
  inputRef: React.RefObject<HTMLInputElement | null>;
}) {
  const { t } = useLanguage();

  const handlePick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(e.target.files ?? []);
    e.target.value = ""; // 同じファイルを続けて選び直せるようにする
    const existingKeys = new Set(files.map(_fileKey));
    const merged = [...files, ...picked.filter((f) => !existingKeys.has(_fileKey(f)))];
    onFilesChange(merged);
  };

  const handleRemove = (index: number) => {
    onFilesChange(files.filter((_, i) => i !== index));
  };

  return (
    <div className="space-y-2">
      <input
        ref={inputRef}
        type="file"
        accept=".csv,.tsv"
        multiple
        onChange={handlePick}
        className="hidden"
      />
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" size="sm" variant="outline" onClick={() => inputRef.current?.click()}>
          {t("geo.reportStudio.pipeline.csvStager.addFiles")}
        </Button>
        {files.length > 0 && (
          <button
            className="text-xs underline underline-offset-2 text-muted-foreground"
            onClick={() => onFilesChange([])}
          >
            {t("geo.reportStudio.pipeline.csvStager.clearAll")}
          </button>
        )}
      </div>
      {files.length > 0 ? (
        <ul className="space-y-1">
          {files.map((f, i) => (
            <li
              key={_fileKey(f)}
              className="flex items-center justify-between gap-2 text-xs border border-border rounded-md px-2 py-1"
            >
              <span className="truncate">{f.name}</span>
              <span className="text-muted-foreground shrink-0">{_formatFileSize(f.size)}</span>
              <button
                className="text-muted-foreground hover:text-destructive shrink-0"
                title={t("geo.reportStudio.pipeline.csvStager.remove")}
                onClick={() => handleRemove(i)}
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-muted-foreground">
          {t("geo.reportStudio.pipeline.csvStager.noFiles")}
        </p>
      )}
    </div>
  );
}

function PreprocessActionCard({
  runId,
  files,
  onFilesChange,
  periodStart,
  onPeriodStartChange,
  periodEnd,
  onPeriodEndChange,
  isDesignFixed,
  isDesignDirty,
  canStartPreprocess,
  orchestratingStage,
  orchestrationError,
  onStartPreprocessing,
  onRetryPreprocess,
  executions,
  csvInputRef,
  periodStartRef,
}: {
  runId: string | null;
  files: File[];
  onFilesChange: (files: File[]) => void;
  periodStart: string;
  onPeriodStartChange: (v: string) => void;
  periodEnd: string;
  onPeriodEndChange: (v: string) => void;
  isDesignFixed: boolean;
  isDesignDirty: boolean;
  canStartPreprocess: boolean;
  orchestratingStage: "idle" | "publishing" | "creating_run" | "preprocessing";
  orchestrationError: string | null;
  onStartPreprocessing: () => void;
  onRetryPreprocess: (runId: string, resumeExecutionId?: string) => void;
  executions?: GeoReportExecution[];
  csvInputRef: React.RefObject<HTMLInputElement | null>;
  periodStartRef: React.RefObject<HTMLInputElement | null>;
}) {
  const { t } = useLanguage();
  const isOrchestrating = orchestratingStage !== "idle";

  if (runId === null) {
    // --- Design作成フェーズ ---
    if (!isDesignFixed) return null; // 「この設計でOK」より前は出さない
    return (
      <GeoCard>
        <GeoCardHeader title={t("geo.reportStudio.pipeline.nextAction.title")} />
        <GeoCardBody className="space-y-3">
          {isDesignDirty && (
            <p className="text-xs text-destructive">
              {t("geo.reportStudio.pipeline.nextAction.designDirty")}
            </p>
          )}
          <div className="flex flex-wrap items-end gap-2">
            <div>
              <label className="text-xs text-muted-foreground">
                {t("geo.reportStudio.pipeline.nextAction.periodStart")}
              </label>
              <Input
                ref={periodStartRef}
                type="date"
                value={periodStart}
                onChange={(e) => onPeriodStartChange(e.target.value)}
                className="block mt-1 max-w-[160px]"
              />
            </div>
            <div>
              <label className="text-xs text-muted-foreground">
                {t("geo.reportStudio.pipeline.nextAction.periodEnd")}
              </label>
              <Input
                type="date"
                value={periodEnd}
                onChange={(e) => onPeriodEndChange(e.target.value)}
                className="block mt-1 max-w-[160px]"
              />
            </div>
          </div>
          <CsvFileStager files={files} onFilesChange={onFilesChange} inputRef={csvInputRef} />
          <Button
            size="sm"
            disabled={!canStartPreprocess || isOrchestrating}
            onClick={onStartPreprocessing}
          >
            {isOrchestrating
              ? t(`geo.reportStudio.pipeline.nextAction.stage.${orchestratingStage}`)
              : t("geo.reportStudio.pipeline.nextAction.startButton")}
          </Button>
          {orchestrationError && !isOrchestrating && (
            <p className="text-xs text-destructive">{orchestrationError}</p>
          )}
        </GeoCardBody>
      </GeoCard>
    );
  }

  // --- Pinned Design + Run Status（Run作成直後・既存Analysis選択の両方で共通） ---
  const preprocessExecution = findExecution(executions, "preprocess");
  if (preprocessExecution?.status === "completed") return null; // ChatGPT ZIPセクションへ引き継ぐ
  if (preprocessExecution?.status === "running") return null; // 進捗はAnalyzeStepPanel側で表示

  // 2026-09-17変更、ユーザー指摘「途中まで実行してたのを再開するのに新しくCSVを
  // アップロードするの？別のファイルだとデータが混ざるリスクがあるよね」:
  // 以前失敗したExecutionがある場合（＝resume）は、サーバー側が最初のアップロード時に
  // キャッシュした元のCSVを自動的に再利用するため、CSVの再選択は不要（ボタンを
  // そのまま押せる）。ただしサーバー再起動等でキャッシュが失われる稀なケースの
  // フォールバックとして、CSV選択欄自体は残す（普段は使わなくてよいことをヒントで
  // 明示する）。バックエンド側もキャッシュ優先・キャッシュ無ければ添付ファイルを
  // 使う、かつfile_fingerprint検証で前回と異なるCSVは拒否するため、「一応選んでみる」
  // が事故につながることは無い。
  const isResume = preprocessExecution?.status === "failed";

  return (
    <GeoCard>
      <GeoCardHeader title={t("geo.reportStudio.pipeline.recoveryAction.title")} />
      <GeoCardBody className="space-y-3">
        {isResume && (
          <div className="text-xs text-destructive space-y-1">
            <p>{t("geo.reportStudio.pipeline.recoveryAction.failed")}</p>
            {/* 2026-09-16追加: 失敗理由（progress.error、バックエンド側で新たに記録
                するようにした）が分かれば表示する。ユーザー報告「前処理が失敗してる」
                だけでは原因が分からず、都度DBを直接見て調査する必要があったため。 */}
            {typeof (preprocessExecution.progress as Record<string, unknown> | null)?.error ===
              "string" && (
              <p className="text-muted-foreground">
                {(preprocessExecution.progress as Record<string, unknown>).error as string}
              </p>
            )}
          </div>
        )}
        {isResume && (
          <p className="text-xs text-muted-foreground">
            {t("geo.reportStudio.pipeline.recoveryAction.reuseUploadHint")}
          </p>
        )}
        <CsvFileStager files={files} onFilesChange={onFilesChange} inputRef={csvInputRef} />
        <Button
          size="sm"
          disabled={(!isResume && files.length === 0) || isOrchestrating}
          onClick={() => onRetryPreprocess(runId, isResume ? preprocessExecution.id : undefined)}
        >
          {isOrchestrating
            ? t("geo.reportStudio.processing")
            : t("geo.reportStudio.pipeline.recoveryAction.startButton")}
        </Button>
        {!isResume && files.length === 0 && !isOrchestrating && (
          <p className="text-xs text-muted-foreground">
            {t("geo.reportStudio.pipeline.recoveryAction.selectFilesHint")}
          </p>
        )}
        {orchestrationError && !isOrchestrating && (
          <p className="text-xs text-destructive">{orchestrationError}</p>
        )}
      </GeoCardBody>
    </GeoCard>
  );
}

// ---------------------------------------------------------------------------
// Step 3: Analyze — データを整理・分類・集計する（Preprocess/Prompt1/Aggregationは
// 平易な3ラベルにまとめ、execution_id等の技術詳細とChatGPT Fallback(Prompt1側)は
// Advanced DetailsへFold）
// ---------------------------------------------------------------------------

const _ANALYZE_STAGE_LABELS: Array<{ stage: GeoReportExecution["stage"]; labelKey: string }> = [
  { stage: "preprocess", labelKey: "geo.reportStudio.workflow.analyze.preparingData" },
  { stage: "prompt1", labelKey: "geo.reportStudio.workflow.analyze.analyzingEvidence" },
  { stage: "aggregation", labelKey: "geo.reportStudio.workflow.analyze.calculatingTrends" },
];

function AnalyzeStepPanel({
  projectId,
  runId,
  run,
  executions,
}: {
  projectId: string;
  runId: string;
  run?: GeoReportCollectionRun;
  executions?: GeoReportExecution[];
}) {
  const { t } = useLanguage();
  const cancelMutation = useCancelRun(projectId, runId);
  const setCanonicalMutation = useSetCanonicalExecution(projectId, runId);

  const preprocessExecution = findExecution(executions, "preprocess");
  // 2026-09-16修正: 「最新のpreprocess実行のstatus」ではなく「run.canonical_preprocess_
  // execution_idが設定済みか」で判定する。前者だと、過去に成功したPreprocessがあっても
  // 後から（再試行や誤操作で）別のpreprocess実行を作ってそれが失敗/中断しただけで
  // ChatGPT Fallbackが丸ごと使えなくなり、「途中から再開できない」UIになっていた
  // （バックエンド側は元々canonical_preprocess_execution_idを正としているのと揃える）。
  const preprocessDone = !!run?.canonical_preprocess_execution_id;
  const anyRunning = (executions ?? []).some(
    (e) => e.status === "running" || e.status === "pending",
  );
  useEffect(() => {
    if (anyRunning) cancelMutation.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anyRunning]);

  // 2026-09-16追加: 「この結果を採用する」ボタンが、そのブラウザセッションでImport
  // mutationを実行した直後にしか出ない（importPrompt1Mutation.dataというクライアント
  // ローカルの一時状態にのみ依存していた）ため、ページ再読み込み後やしばらく経ってから
  // 戻ってきた場合にボタンが消えて「採用できない」ように見える不具合があった。
  // ComposeStepPanel（Prompt2側）と同じ、サーバー状態（executions一覧）だけに基づく
  // 永続的な履歴リストへ揃える。
  const prompt1Executions = (executions ?? [])
    .filter((e) => e.stage === "prompt1")
    .sort((a, b) => b.created_at.localeCompare(a.created_at));

  if (!preprocessExecution) {
    return (
      <GeoCard>
        <GeoCardHeader title={t("geo.reportStudio.workflow.step.analyze")} />
        <GeoCardBody>
          <p className="text-sm text-muted-foreground">
            {t("geo.reportStudio.workflow.analyze.notStarted")}
          </p>
        </GeoCardBody>
      </GeoCard>
    );
  }

  return (
    <GeoCard>
      <GeoCardHeader
        title={t("geo.reportStudio.workflow.step.analyze")}
        meta={t("geo.reportStudio.workflow.analyze.subtitle")}
      />
      <GeoCardBody className="space-y-4">
        <div className="space-y-2">
          {_ANALYZE_STAGE_LABELS.map(({ stage, labelKey }) => {
            const execution = findExecution(executions, stage);
            return (
              <div key={stage} className="text-xs space-y-1">
                <div className="flex items-center gap-2">
                  <span className="text-muted-foreground w-40">{t(labelKey)}</span>
                  {execution ? (
                    <Badge variant={statusBadgeVariant(execution.status)}>{execution.status}</Badge>
                  ) : (
                    <Badge variant="outline">—</Badge>
                  )}
                </div>
                <ExecutionProgressBar execution={execution} />
              </div>
            );
          })}
        </div>

        {/* 2026-09-16追加: Prompt1（数十万件の生データ規模のためChatGPT必須）に続けて、
            Prompt2（クラスタ単位の小規模データ、Application AI未実装のため現状は
            ChatGPT経由）も同じセッションで完結させるための入口。従来の
            「Prompt1をExport/Import/採用→Prompt2をExport/Import/採用」という
            6ステップの往復を、1回のExportと1回のImportまで圧縮する
            （ユーザー要望、Application AI化までのブリッジ）。 */}
        <CombinedChatGptSession projectId={projectId} runId={runId} run={run} />

        {preprocessDone && anyRunning && (
          <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-border">
            <Button
              size="sm"
              variant="outline"
              disabled={cancelMutation.isPending || cancelMutation.isSuccess}
              onClick={() => cancelMutation.mutate()}
            >
              {cancelMutation.isSuccess
                ? t("geo.reportStudio.pipeline.cancelRequested")
                : t("geo.reportStudio.pipeline.cancelProcessing")}
            </Button>
          </div>
        )}

        {prompt1Executions.length > 0 && (
          <div className="space-y-2 pt-2 border-t border-border">
            <p className="text-xs font-medium">{t("geo.reportStudio.pipeline.prompt1Results")}</p>
            {prompt1Executions.map((e) => {
              const isCurrent = e.id === run?.canonical_prompt1_execution_id;
              const isStale = e.status === "stale";
              return (
                <div
                  key={e.id}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border px-3 py-2 text-xs"
                >
                  <div className="flex flex-1 flex-col gap-1 min-w-[220px]">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="outline">
                        {e.execution_method === "chatgpt_import" ? "ChatGPT" : "Application AI"}
                      </Badge>
                      {isCurrent && <Badge>{t("geo.reportStudio.workflow.compose.current")}</Badge>}
                      {isStale && (
                        <Badge variant="destructive">
                          {t("geo.reportStudio.workflow.compose.stale")}
                        </Badge>
                      )}
                      {!isCurrent && !isStale && (
                        <Badge variant="outline">
                          {t("geo.reportStudio.workflow.compose.alternative")}
                        </Badge>
                      )}
                      <Badge variant={statusBadgeVariant(e.status)}>{e.status}</Badge>
                      <span className="text-muted-foreground">
                        {new Date(e.created_at).toLocaleString()}
                      </span>
                    </div>
                    {/* 2026-09-16修正: この一覧はstatusバッジのみでExecutionProgressBar
                        （files_processed/files_total）を表示していなかったため、
                        ChatGPT Import中に「動いているのか分からない」状態になっていた。 */}
                    <ExecutionProgressBar execution={e} />
                  </div>
                  {!isCurrent && e.status === "completed" && (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={setCanonicalMutation.isPending}
                      onClick={() =>
                        setCanonicalMutation.mutate({ stage: "prompt1", executionId: e.id })
                      }
                    >
                      {t("geo.reportStudio.pipeline.useAsCurrent")}
                    </Button>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </GeoCardBody>

      <div className="border-t border-border" />
      <Collapsible>
        <GeoCardBody className="py-2">
          <CollapsibleTrigger className="text-xs text-muted-foreground underline underline-offset-2">
            {t("geo.reportStudio.workflow.analyze.advancedDetails")}
          </CollapsibleTrigger>
        </GeoCardBody>
        <CollapsibleContent>
          <GeoCardBody className="pt-0">
            <Prompt1ChatGptFallback projectId={projectId} runId={runId} run={run} />
          </GeoCardBody>
        </CollapsibleContent>
      </Collapsible>
    </GeoCard>
  );
}

function Prompt1ChatGptFallback({
  projectId,
  runId,
  run,
}: {
  projectId: string;
  runId: string;
  run?: GeoReportCollectionRun;
}) {
  const { t } = useLanguage();
  const importPrompt1Mutation = useImportPrompt1Package(projectId, runId);
  const setCanonicalMutation = useSetCanonicalExecution(projectId, runId);
  // AnalyzeStepPanelと同じ理由でcanonical_preprocess_execution_idを見る（2026-09-16）。
  const preprocessDone = !!run?.canonical_preprocess_execution_id;

  return (
    <div className="border border-border rounded-md p-3 space-y-2">
      <p className="text-xs font-medium">{t("geo.reportStudio.pipeline.chatgptFallback")}</p>
      {!preprocessDone && (
        <p className="text-xs text-muted-foreground">
          {t("geo.reportStudio.pipeline.preprocessFirst")}
        </p>
      )}
      {preprocessDone && (
        <>
          <ExportZipLink
            url={geoReportPrompt1ExportUrl(projectId, runId)}
            label={t("geo.reportStudio.pipeline.exportPackage")}
          />
          <input
            type="file"
            accept=".zip"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) importPrompt1Mutation.mutate(f);
            }}
            className="text-xs"
          />
          {importPrompt1Mutation.isError && (
            <p className="text-xs text-destructive">
              {(importPrompt1Mutation.error as Error).message}
            </p>
          )}
          {importPrompt1Mutation.data && (
            <Button
              size="sm"
              variant="outline"
              disabled={setCanonicalMutation.isPending}
              onClick={() =>
                setCanonicalMutation.mutate({
                  stage: "prompt1",
                  executionId: importPrompt1Mutation.data!.execution_id,
                })
              }
            >
              {t("geo.reportStudio.pipeline.useAsCurrent")}
            </Button>
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Combined ChatGPT Session（2026-09-16追加）: Prompt1→Prompt2→レポート生成までを
// 1回のExport・1回のImportで完結させる。Preprocess完了直後（Canonical Prompt1は
// まだ無くてよい）に呼べる。従来の「Prompt1をExport/Import/採用→Prompt2を
// Export/Import/採用」という6ステップの往復をこの1画面へ圧縮する
// （ユーザー要望: UXが最悪なので、ChatGPT 1セッションで完結させたい）。
// ---------------------------------------------------------------------------

function CombinedChatGptSession({
  projectId,
  runId,
  run,
}: {
  projectId: string;
  runId: string;
  run?: GeoReportCollectionRun;
}) {
  const { t } = useLanguage();
  const importCombinedMutation = useImportCombinedPackage(projectId, runId);
  const [prompt1File, setPrompt1File] = useState<File | null>(null);
  const [prompt2File, setPrompt2File] = useState<File | null>(null);
  const preprocessDone = !!run?.canonical_preprocess_execution_id;

  if (!preprocessDone) {
    return null;
  }

  return (
    <GeoCard className="border-primary/40">
      <GeoCardBody className="space-y-3">
        <p className="text-sm font-medium">
          {t("geo.reportStudio.pipeline.combinedSession.title")}
        </p>
        <p className="text-xs text-muted-foreground">
          {t("geo.reportStudio.pipeline.combinedSession.description")}
        </p>
        <ExportZipLink
          url={geoReportCombinedExportUrl(projectId, runId)}
          label={t("geo.reportStudio.pipeline.combinedSession.exportPackage")}
        />
        <div className="grid gap-2 sm:grid-cols-2">
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">
              {t("geo.reportStudio.pipeline.combinedSession.prompt1OutputLabel")}
            </p>
            <input
              type="file"
              accept=".zip"
              onChange={(e) => setPrompt1File(e.target.files?.[0] ?? null)}
              className="text-xs"
            />
          </div>
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">
              {t("geo.reportStudio.pipeline.combinedSession.prompt2OutputLabel")}
            </p>
            <input
              type="file"
              accept=".zip"
              onChange={(e) => setPrompt2File(e.target.files?.[0] ?? null)}
              className="text-xs"
            />
          </div>
        </div>
        <Button
          size="sm"
          disabled={!prompt1File || !prompt2File || importCombinedMutation.isPending}
          onClick={() => {
            if (prompt1File && prompt2File) {
              importCombinedMutation.mutate({ prompt1File, prompt2File });
            }
          }}
        >
          {importCombinedMutation.isPending
            ? t("geo.reportStudio.processing")
            : t("geo.reportStudio.pipeline.combinedSession.importButton")}
        </Button>
        {importCombinedMutation.isError && (
          <p className="text-xs text-destructive">
            {(importCombinedMutation.error as Error).message}
          </p>
        )}
        {importCombinedMutation.data && (
          <div className="text-xs text-emerald-600 space-y-1">
            <p>{t("geo.reportStudio.pipeline.combinedSession.importSuccess")}</p>
            {importCombinedMutation.data.section_coverage_warnings &&
              importCombinedMutation.data.section_coverage_warnings.length > 0 && (
                <p className="text-amber-600">
                  {t("geo.reportStudio.pipeline.combinedSession.sectionCoverageWarnings", {
                    count: importCombinedMutation.data.section_coverage_warnings.length,
                  })}
                </p>
              )}
          </div>
        )}
      </GeoCardBody>
    </GeoCard>
  );
}

// ---------------------------------------------------------------------------
// Step 4: Compose — AIがレポートを作る。Application AI / ChatGPT Importの結果が
// 複数あっても、今どれがCurrent/Alternative/Staleかを一目で分かるようにする。
// ---------------------------------------------------------------------------

function ComposeStepPanel({
  projectId,
  runId,
  run,
  executions,
}: {
  projectId: string;
  runId: string;
  run: GeoReportCollectionRun | undefined;
  executions?: GeoReportExecution[];
}) {
  const { t } = useLanguage();
  const setCanonicalMutation = useSetCanonicalExecution(projectId, runId);
  const runFromPrompt1Mutation = useRunAggregationThroughPrompt2(projectId, runId);

  const prompt2Executions = (executions ?? [])
    .filter((e) => e.stage === "prompt2")
    .sort((a, b) => b.created_at.localeCompare(a.created_at));

  // Canonical Prompt1（Application AIまたはChatGPT Import、どちらでもよい）さえ
  // 確定していれば、ここからAggregation→Prompt2だけをApplication AIで実行できる
  // （大量データでPrompt1のみChatGPTへ退避した場合の続きの入口、2026-09-14）。
  //
  // 2026-09-16修正（Phase H）: evidence_intelligence_v1のRunでは、この経路
  // （run_aggregation、Public Voice 3-way sentiment集計専用）が
  // primary_graph_signals/common_primary_graph_start未設定エラーで必ず失敗する
  // （evidence_intelligenceはそもそもこの集計思想を使わないため）。バックエンドは
  // 無変更のまま、UI側でこのモードではボタン自体を無効化し、ChatGPT Fallbackへ
  // 誘導する（「壊れているボタン」をユーザーに踏ませない）。
  const isEvidenceIntelligenceMode = run?.prompt2_contract_version === "evidence_intelligence_v1";
  const canRunFromCanonicalPrompt1 =
    !!run?.canonical_prompt1_execution_id && !isEvidenceIntelligenceMode;
  const runFromPrompt1Button = isEvidenceIntelligenceMode ? (
    <p className="text-xs text-muted-foreground">
      {t("geo.reportStudio.workflow.compose.evidenceIntelligenceChatGptOnly")}
    </p>
  ) : (
    canRunFromCanonicalPrompt1 && (
      <div className="space-y-2">
        <Button
          size="sm"
          disabled={runFromPrompt1Mutation.isPending}
          onClick={() => runFromPrompt1Mutation.mutate(undefined)}
        >
          {runFromPrompt1Mutation.isPending
            ? t("geo.reportStudio.processing")
            : t("geo.reportStudio.workflow.compose.runFromCanonicalPrompt1")}
        </Button>
        {runFromPrompt1Mutation.isError && (
          <p className="text-xs text-destructive">
            {(runFromPrompt1Mutation.error as Error).message}
          </p>
        )}
      </div>
    )
  );

  // 2026-09-16修正: 従来はprompt2Executions.length===0の場合に早期returnしており、
  // ChatGPT Fallback（Export/Import）のCollapsibleへ到達する手段が無かった
  // （evidence_intelligence_v1のRunで初めてComposeへ来た際、「まだ作成されていません」
  // という案内だけが表示され、そこから先に進む導線が無いデッドエンドだった）。
  // 空状態でもCollapsibleまで含めて同じ構造で表示する。
  return (
    <GeoCard>
      <GeoCardHeader
        title={t("geo.reportStudio.workflow.step.compose")}
        meta={t("geo.reportStudio.workflow.compose.subtitle")}
      />
      <GeoCardBody className="space-y-2">
        {prompt2Executions.length === 0 && (
          <p className="text-sm text-muted-foreground">
            {t("geo.reportStudio.workflow.compose.noResultsYet")}
          </p>
        )}
        {prompt2Executions.map((e) => {
          const isCurrent = e.id === run?.canonical_prompt2_execution_id;
          const isStale = e.status === "stale";
          return (
            <div
              key={e.id}
              className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border px-3 py-2 text-xs"
            >
              <div className="flex flex-1 flex-col gap-1 min-w-[220px]">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="outline">
                    {e.execution_method === "chatgpt_import" ? "ChatGPT" : "Application AI"}
                  </Badge>
                  {isCurrent && <Badge>{t("geo.reportStudio.workflow.compose.current")}</Badge>}
                  {isStale && (
                    <Badge variant="destructive">
                      {t("geo.reportStudio.workflow.compose.stale")}
                    </Badge>
                  )}
                  {!isCurrent && !isStale && (
                    <Badge variant="outline">
                      {t("geo.reportStudio.workflow.compose.alternative")}
                    </Badge>
                  )}
                  <Badge variant={statusBadgeVariant(e.status)}>{e.status}</Badge>
                  <span className="text-muted-foreground">
                    {new Date(e.created_at).toLocaleString()}
                  </span>
                </div>
                <ExecutionProgressBar execution={e} />
              </div>
              {!isCurrent && e.status === "completed" && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={setCanonicalMutation.isPending}
                  onClick={() =>
                    setCanonicalMutation.mutate({ stage: "prompt2", executionId: e.id })
                  }
                >
                  {t("geo.reportStudio.pipeline.useAsCurrent")}
                </Button>
              )}
            </div>
          );
        })}
        {setCanonicalMutation.isError && (
          <p className="text-xs text-destructive">
            {(setCanonicalMutation.error as Error).message}
          </p>
        )}
        {prompt2Executions.length > 0 && (
          <p className="text-xs text-muted-foreground pt-1">
            {t("geo.reportStudio.workflow.compose.regenerateHint")}
          </p>
        )}
        {runFromPrompt1Button}
      </GeoCardBody>

      <div className="border-t border-border" />
      <Collapsible>
        <GeoCardBody className="py-2">
          <CollapsibleTrigger className="text-xs text-muted-foreground underline underline-offset-2">
            {t("geo.reportStudio.workflow.compose.advancedDetails")}
          </CollapsibleTrigger>
        </GeoCardBody>
        <CollapsibleContent>
          <GeoCardBody className="pt-0">
            <Prompt2ChatGptFallback projectId={projectId} runId={runId} run={run} />
          </GeoCardBody>
        </CollapsibleContent>
      </Collapsible>
    </GeoCard>
  );
}

function Prompt2ChatGptFallback({
  projectId,
  runId,
  run,
}: {
  projectId: string;
  runId: string;
  run?: GeoReportCollectionRun;
}) {
  const { t } = useLanguage();
  const importPrompt2Mutation = useImportPrompt2Package(projectId, runId);
  const setCanonicalMutation = useSetCanonicalExecution(projectId, runId);
  // Prompt1ChatGptFallbackと同じ理由でcanonical_prompt1_execution_idを見る（2026-09-16）。
  // Application AIで完了した場合でもChatGPT Importをcanonicalにした場合でも、この列が
  // 設定されていればAggregation/Prompt2へ進める状態——「最新prompt1実行のstatus」だと
  // 後発の失敗実行に隠れてしまう同じ問題があった。
  const prompt1Done = !!run?.canonical_prompt1_execution_id;

  return (
    <div className="border border-border rounded-md p-3 space-y-2">
      <p className="text-xs font-medium">{t("geo.reportStudio.pipeline.chatgptFallback")}</p>
      {!prompt1Done && (
        <p className="text-xs text-muted-foreground">
          {t("geo.reportStudio.pipeline.prompt1First")}
        </p>
      )}
      {prompt1Done && (
        <>
          <ExportZipLink
            url={geoReportPrompt2ExportUrl(projectId, runId)}
            label={t("geo.reportStudio.pipeline.exportPackage")}
          />
          <input
            type="file"
            accept=".zip"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) importPrompt2Mutation.mutate(f);
            }}
            className="text-xs"
          />
          {importPrompt2Mutation.isError && (
            <p className="text-xs text-destructive">
              {(importPrompt2Mutation.error as Error).message}
            </p>
          )}
          {importPrompt2Mutation.data && (
            <Button
              size="sm"
              variant="outline"
              disabled={setCanonicalMutation.isPending}
              onClick={() =>
                setCanonicalMutation.mutate({
                  stage: "prompt2",
                  executionId: importPrompt2Mutation.data!.execution_id,
                })
              }
            >
              {t("geo.reportStudio.pipeline.useAsCurrent")}
            </Button>
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 5: Review — Final ReportとReport Edit Chatを同じ画面（左右Pane）に置く。
// レポートを見ながら自然言語で修正を依頼でき、Revision履歴もここから辿れる。
// ---------------------------------------------------------------------------

function ReviewStepPanel({ projectId, runId }: { projectId: string; runId: string }) {
  const { t } = useLanguage();
  const historyQuery = useGeoReportEditHistory(projectId, runId);
  const currentEntry = historyQuery.data?.history.find((e) => e.is_current_report);
  const currentIsStale = currentEntry?.status === "stale";

  return (
    <GeoCard>
      <GeoCardHeader
        title={t("geo.reportStudio.workflow.step.review")}
        meta={t("geo.reportStudio.workflow.review.subtitle")}
      />
      <GeoCardBody>
        {currentIsStale && (
          <p className="text-sm text-destructive mb-3">
            {t("geo.reportStudio.workflow.review.currentReportStale")}
          </p>
        )}
        <div className="grid gap-4 lg:grid-cols-2">
          <div>
            <p className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground mb-2">
              {t("geo.reportStudio.workflow.review.reportPane")}
            </p>
            <FinalReportPane projectId={projectId} runId={runId} />
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground mb-2">
              {t("geo.reportStudio.workflow.review.editPane")}
            </p>
            <ReportEditPane projectId={projectId} runId={runId} />
          </div>
        </div>
      </GeoCardBody>
    </GeoCard>
  );
}

function FinalReportPane({ projectId, runId }: { projectId: string; runId: string }) {
  const { t } = useLanguage();
  const finalReportQuery = useGeoReportFinalReport(projectId, runId);

  return (
    <>
      {finalReportQuery.isError && (
        <p className="text-sm text-muted-foreground">
          {t("geo.reportStudio.pipeline.finalReportNotReady")}
        </p>
      )}
      {finalReportQuery.data && (
        <div
          className="prose prose-sm max-w-none border border-border rounded-md p-4 max-h-[640px] overflow-y-auto"
          dangerouslySetInnerHTML={{ __html: finalReportQuery.data.html }}
        />
      )}
    </>
  );
}

function ReportEditPane({ projectId, runId }: { projectId: string; runId: string }) {
  const { t } = useLanguage();
  const [instruction, setInstruction] = useState("");
  const requestEdit = useRequestReportEdit(projectId, runId);
  const historyQuery = useGeoReportEditHistory(projectId, runId);
  const useAsCurrent = useUseReportExecutionAsCurrent(projectId, runId);
  const [lastResult, setLastResult] = useState<GeoReportEditResult | null>(null);

  const handleSubmit = () => {
    if (!instruction.trim()) return;
    requestEdit.mutate(instruction, {
      onSuccess: (result) => {
        setLastResult(result);
        setInstruction("");
      },
    });
  };

  return (
    <div className="border border-border rounded-md p-3 space-y-3 max-h-[640px] overflow-y-auto">
      <div className="space-y-2">
        <Textarea
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          placeholder={t("geo.reportStudio.pipeline.reportEditPlaceholder")}
          rows={3}
        />
        <Button
          size="sm"
          onClick={handleSubmit}
          disabled={requestEdit.isPending || !instruction.trim()}
        >
          {requestEdit.isPending
            ? t("geo.reportStudio.pipeline.reportEditApplying")
            : t("geo.reportStudio.pipeline.reportEditSubmit")}
        </Button>

        {requestEdit.isError && (
          <p className="text-sm text-destructive">{(requestEdit.error as Error).message}</p>
        )}

        {lastResult?.requires_prompt2_recomposition && (
          <p className="text-sm text-amber-600">
            {t("geo.reportStudio.pipeline.reportEditRecompositionRequired")}
            {lastResult.reason ? ` (${lastResult.reason})` : ""}
          </p>
        )}
        {lastResult && !lastResult.requires_prompt2_recomposition && (
          <p className="text-xs text-muted-foreground">
            {t("geo.reportStudio.pipeline.reportEditAppliedTo")}
            {lastResult.affected_section_ids.join(", ")}
          </p>
        )}
      </div>

      {historyQuery.data && historyQuery.data.history.length > 0 && (
        <div className="space-y-2 border-t border-border pt-3">
          <p className="text-xs font-medium text-muted-foreground">
            {t("geo.reportStudio.pipeline.reportEditHistory")}
          </p>
          <ul className="space-y-1">
            {[...historyQuery.data.history].reverse().map((entry) => (
              <li
                key={entry.execution_id}
                className="flex items-center justify-between gap-2 rounded-md border border-border px-2 py-1 text-xs"
              >
                <div className="flex items-center gap-2 min-w-0">
                  <Badge variant={entry.stage === "prompt2" ? "outline" : "secondary"}>
                    {entry.stage === "prompt2"
                      ? t("geo.reportStudio.pipeline.reportEditPrompt2Badge")
                      : t("geo.reportStudio.pipeline.reportEditEditBadge")}
                  </Badge>
                  <span className="text-muted-foreground shrink-0">
                    {new Date(entry.created_at).toLocaleString()}
                  </span>
                  {entry.edit_instruction && (
                    <span className="truncate">{entry.edit_instruction.instruction}</span>
                  )}
                  {entry.is_current_report && (
                    <Badge className="shrink-0">
                      {t("geo.reportStudio.pipeline.reportEditCurrentBadge")}
                    </Badge>
                  )}
                  {entry.status === "stale" && (
                    <Badge variant="destructive" className="shrink-0">
                      {t("geo.reportStudio.pipeline.reportEditStaleBadge")}
                    </Badge>
                  )}
                </div>
                {!entry.is_current_report && (
                  <Button
                    size="sm"
                    variant="outline"
                    className="shrink-0"
                    disabled={useAsCurrent.isPending}
                    onClick={() => useAsCurrent.mutate(entry.execution_id)}
                  >
                    {t("geo.reportStudio.pipeline.reportEditUseAsCurrent")}
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 6: Continue — 次のRunに何が引き継がれるかを明示する
// （Design Version / Narrative Master / Report Template / Previous Continuity /
// Baseline Run）。Create Next Run実行後はSnapshot固定であることも案内する。
// ---------------------------------------------------------------------------

function ContinueStepPanel({
  projectId,
  runId,
  run,
  readiness,
  onSelectRun,
}: {
  projectId: string;
  runId: string;
  run: GeoReportCollectionRun | undefined;
  readiness: boolean;
  onSelectRun: (id: string) => void;
}) {
  const { t } = useLanguage();
  const runsQuery = useGeoReportRuns(projectId);
  const versionsQuery = useGeoReportDesignVersions(projectId);
  const narrativeHistoryQuery = useGeoReportAssetHistory(projectId, "narrative_master");
  const templateHistoryQuery = useGeoReportAssetHistory(projectId, "report_template");
  const createRunMutation = useCreateDesignPinnedRun(projectId);

  if (!readiness || !run) {
    return (
      <GeoCard>
        <GeoCardHeader title={t("geo.reportStudio.workflow.step.continue")} />
        <GeoCardBody>
          <p className="text-sm text-muted-foreground">
            {t("geo.reportStudio.workflow.continue.notReadyYet")}
          </p>
        </GeoCardBody>
      </GeoCard>
    );
  }

  const designVersion = versionsQuery.data?.versions.find((v) => v.id === run.design_version_id);
  const narrativeAsset = narrativeHistoryQuery.data?.history.find(
    (a) => a.id === run.narrative_master_asset_id,
  );
  const templateAsset = templateHistoryQuery.data?.history.find(
    (a) => a.id === run.report_template_asset_id,
  );
  const baselineRun = runsQuery.data?.runs.find((r) => r.run_id === run.baseline_run_id);
  const hasPreviousContinuity = !!run.previous_continuity_artifact_id;

  return (
    <GeoCard>
      <GeoCardHeader
        title={t("geo.reportStudio.workflow.step.continue")}
        meta={t("geo.reportStudio.workflow.continue.subtitle")}
      />
      <GeoCardBody className="space-y-4">
        <div>
          <p className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground mb-2">
            {t("geo.reportStudio.workflow.continue.inheritedTitle")}
          </p>
          <dl className="grid gap-2 sm:grid-cols-2 text-xs">
            <div className="flex items-center justify-between border border-border rounded-md px-3 py-2">
              <dt className="text-muted-foreground">
                {t("geo.reportStudio.workflow.continue.designVersion")}
              </dt>
              <dd>{designVersion ? `v${designVersion.version}` : "—"}</dd>
            </div>
            <div className="flex items-center justify-between border border-border rounded-md px-3 py-2">
              <dt className="text-muted-foreground">
                {t("geo.reportStudio.workflow.continue.narrativeMaster")}
              </dt>
              <dd>{narrativeAsset?.version ? `v${narrativeAsset.version}` : "—"}</dd>
            </div>
            <div className="flex items-center justify-between border border-border rounded-md px-3 py-2">
              <dt className="text-muted-foreground">
                {t("geo.reportStudio.workflow.continue.reportTemplate")}
              </dt>
              <dd>{templateAsset?.version ? `v${templateAsset.version}` : "—"}</dd>
            </div>
            <div className="flex items-center justify-between border border-border rounded-md px-3 py-2">
              <dt className="text-muted-foreground">
                {t("geo.reportStudio.workflow.continue.baselineRun")}
              </dt>
              <dd className="truncate max-w-[160px]">
                {baselineRun
                  ? baselineRun.run_name || baselineRun.run_id.slice(0, 8)
                  : t("geo.reportStudio.workflow.continue.noBaselineRun")}
              </dd>
            </div>
            <div className="flex items-center justify-between border border-border rounded-md px-3 py-2 sm:col-span-2">
              <dt className="text-muted-foreground">
                {t("geo.reportStudio.workflow.continue.previousContinuity")}
              </dt>
              <dd>
                {hasPreviousContinuity
                  ? t("geo.reportStudio.workflow.continue.previousContinuityYes")
                  : t("geo.reportStudio.workflow.continue.previousContinuityNo")}
              </dd>
            </div>
          </dl>
        </div>

        <div className="border-t border-border pt-3 space-y-2">
          <Button
            size="sm"
            disabled={createRunMutation.isPending}
            onClick={() =>
              createRunMutation.mutate(
                { baselineRunId: runId },
                { onSuccess: (r) => onSelectRun(r.run.run_id) },
              )
            }
          >
            {createRunMutation.isPending
              ? t("geo.reportStudio.processing")
              : t("geo.reportStudio.workflow.continue.createNextRun")}
          </Button>
          {createRunMutation.isError && (
            <p className="text-xs text-destructive">{(createRunMutation.error as Error).message}</p>
          )}
          <p className="text-xs text-muted-foreground">
            {t("geo.reportStudio.workflow.continue.snapshotNote")}
          </p>
        </div>
      </GeoCardBody>
    </GeoCard>
  );
}
