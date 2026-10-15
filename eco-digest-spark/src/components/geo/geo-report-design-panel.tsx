// Report Studio「Design」領域: Design Chat 1本に統合したUI（2026-09-16リデザイン、
// 2026-09-16 UI全面見直しで再構成）。
// Expert AIとの自然言語チャットだけで、Report Design/Signals/Queries/Narrative
// Master/Report Templateの全てを設計・生成・修正でき、既存材料（Narrative Master
// CSV・final_report.html）はファイル添付から始められる。「この設計でOK」で確定すると、
// 確定した全アセットのダウンロードリンクが表示され、Publishまでこの1画面で完結する。
//
// Trust Boundary（2026-09-16 UI全面見直し、Report Studio全面Plan参照）: このコンポーネント
// はProject単位の「Current Design」を編集するチャットUIだが、既にRun作成済みの
// Analysis（`analysisContext.runId !== null`）はDesign作成時点のバージョンを
// Pinで固定している。編集可能なチャットとPinned Snapshotを混同させないため、
// 呼び出し元（GeoReportPipelinePanel）が`editable`（= runId===null）を渡し、
// falseの間はこのコンポーネントは編集UIの代わりにPinnedDesignSnapshotCardを表示する。
import { useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import { GeoCard, GeoCardBody, GeoCardHeader } from "@/components/geo/geo-card";
import {
  useFixDesign,
  useGeoReportAssetDraft,
  useGeoReportAssetPublished,
  useGeoReportDesignChatTurns,
  useGeoReportDesignState,
  useGeoReportDesignVersions,
  usePublishGeoReportDesign,
  useResetDesign,
  useSendDesignChatMessage,
  useUploadDesignChatFile,
  geoReportNarrativeMasterDownloadUrl,
  geoReportQueryPlanExportUrl,
  geoReportReportDesignDownloadUrl,
  geoReportSignalsDownloadUrl,
  geoReportTemplatePreviewUrl,
  type GeoReportDesignState,
  type GeoReportDesignVersion,
  type GeoReportProjectAsset,
} from "@/lib/geoReportDesignApi";
import { useLanguage } from "@/lib/i18n";

// ---------------------------------------------------------------------------
// Design承認状態の純粋な判定ロジック（GeoReportPipelinePanel側でも同じ判定基準を
// 使うため、JSXを持たない純関数としてここからexportする——ロジックの二重実装を避ける）。
// ---------------------------------------------------------------------------

// hasFixedDesignは`fixMutation.data`（画面リロードで消えるmutationの一時state）や
// ダウンロードリンクの有無（過去のFIXの残骸である可能性がある）から推測しない。
// これはPreprocess開始の可否を決める「承認境界」であるため、推測判定にしない。
// geo_report_project_assets_service.pyのfix_design_state/get_design_stateを確認した
// ところ、確定済みDesign状態には明確な確定フラグ・確定時刻（fixed_at等）は存在せず、
// 初回状態は必ず report_design={} / signals=[] であることを確認済み（2026-09-16）。
// そのため、確定(pending_*ではない)フィールドが実際に非空であることを
// hasFixedDesignの判定として使う（確定信号が無いことをコードで確認済みのフォールバック）。
export function hasFixedDesign(state: GeoReportDesignState): boolean {
  return Object.keys(state.report_design ?? {}).length > 0 || (state.signals ?? []).length > 0;
}

// FIX後にDesign Chatで変更すると、pending_*が再び非nullになる（既存のFIX前提の
// 「提案→FIX」ワークフローそのもの）。これがtrueの間はDirty（承認境界に戻っていない）。
export function hasDesignChangesSinceFix(state: GeoReportDesignState): boolean {
  return Boolean(state.pending_report_design || state.pending_signals || state.pending_queries);
}

// チャットの「開始して」コマンドを厳格な完全一致でintercept する（部分一致は
// 「まだ開始してほしくない」のような否定文にも誤爆するため使わない）。Publish＋
// Run作成という後戻りしにくい操作を伴うため、決定的な判定にする（LLMには送らない）。
const _PREPROCESS_COMMANDS = new Set([
  "開始して",
  "前処理を開始して",
  "スタート",
  "前処理スタート",
  "始めて",
  "実行して",
]);

export function isPreprocessCommand(message: string): boolean {
  const normalized = message
    .trim()
    .replace(new RegExp("[\\s\\u3000]+", "g"), "")
    .replace(/[。.！!？?]+$/g, "");
  return _PREPROCESS_COMMANDS.has(normalized);
}

export function GeoReportDesignPanel({
  projectId,
  editable,
  pinnedVersion,
  pinnedVariant,
  onPreprocessIntent,
  onStartNewAnalysis,
}: {
  projectId: string;
  editable: boolean;
  pinnedVersion?: GeoReportDesignVersion;
  pinnedVariant?: "new" | "existing";
  onPreprocessIntent?: () => void;
  onStartNewAnalysis?: () => void;
}) {
  return (
    <div className="space-y-4">
      {editable ? (
        <DesignChatCard projectId={projectId} onPreprocessIntent={onPreprocessIntent} />
      ) : (
        <PinnedDesignSnapshotCard
          pinnedVersion={pinnedVersion}
          variant={pinnedVariant ?? "existing"}
          onStartNewAnalysis={onStartNewAnalysis}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pinned Design Snapshot（Trust Boundary）: runId確定後（新規Analysis作成直後・
// 既存Analysis選択のどちらでも共通）は、Current Designの編集UIの代わりにこれを表示する。
// ---------------------------------------------------------------------------

function PinnedDesignSnapshotCard({
  pinnedVersion,
  variant,
  onStartNewAnalysis,
}: {
  pinnedVersion?: GeoReportDesignVersion;
  variant: "new" | "existing";
  onStartNewAnalysis?: () => void;
}) {
  const { t } = useLanguage();
  const reportDesign = pinnedVersion?.report_design ?? {};
  const purpose = typeof reportDesign.purpose === "string" ? reportDesign.purpose : null;
  const audience = typeof reportDesign.audience === "string" ? reportDesign.audience : null;

  return (
    <GeoCard className="border-muted-foreground/30">
      <GeoCardHeader
        title={
          variant === "new"
            ? t("geo.reportStudio.design.pinned.justFixedTitle", {
                version: pinnedVersion?.version ?? 0,
              })
            : t("geo.reportStudio.design.pinned.title", { version: pinnedVersion?.version ?? 0 })
        }
      />
      <GeoCardBody className="space-y-2 text-sm">
        <p className="text-xs text-muted-foreground">
          {t("geo.reportStudio.design.pinned.notice")}
        </p>
        {purpose && (
          <div>
            <p className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
              {t("geo.reportStudio.design.pinned.purpose")}
            </p>
            <p className="whitespace-pre-wrap">{purpose}</p>
          </div>
        )}
        {audience && (
          <div>
            <p className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
              {t("geo.reportStudio.design.pinned.audience")}
            </p>
            <p className="whitespace-pre-wrap">{audience}</p>
          </div>
        )}
        <p className="text-xs">
          {t("geo.reportStudio.design.pinned.signalCount", {
            count: (pinnedVersion?.signals ?? []).length,
          })}
        </p>
        <button
          className="text-xs underline underline-offset-2 text-primary"
          onClick={onStartNewAnalysis}
        >
          {t("geo.reportStudio.design.pinned.startNewAnalysis")}
        </button>
      </GeoCardBody>
    </GeoCard>
  );
}

// ---------------------------------------------------------------------------
// Design Chat（編集可能な場合のみ表示、runId===nullの間）
// ---------------------------------------------------------------------------

function DesignChatCard({
  projectId,
  onPreprocessIntent,
}: {
  projectId: string;
  onPreprocessIntent?: () => void;
}) {
  const { t } = useLanguage();
  const turnsQuery = useGeoReportDesignChatTurns(projectId);
  const sendMutation = useSendDesignChatMessage(projectId);
  const uploadMutation = useUploadDesignChatFile(projectId);
  const stateQuery = useGeoReportDesignState(projectId);
  const fixMutation = useFixDesign(projectId);
  const publishMutation = usePublishGeoReportDesign(projectId);
  const versionsQuery = useGeoReportDesignVersions(projectId);
  const resetMutation = useResetDesign(projectId);
  const [message, setMessage] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const turns = turnsQuery.data?.turns ?? [];
  const state = stateQuery.data?.design_state;
  const hasPendingChanges = Boolean(
    state?.pending_report_design || state?.pending_signals || state?.pending_queries,
  );

  const handleSend = () => {
    if (!message.trim()) return;
    // 2026-09-16追加: 厳格な完全一致コマンドの場合はLLMへ送らずintercept する
    // （Publish/Run作成/Preprocessが並行して走る事故を防ぐ、Report Studio UI
    // 全面見直しPlan B章②）。
    if (isPreprocessCommand(message)) {
      onPreprocessIntent?.();
      setMessage("");
      return;
    }
    sendMutation.mutate(message, { onSuccess: () => setMessage("") });
  };

  const handleFileSelected = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // 同じファイルを連続で選び直せるようにする
    if (file) uploadMutation.mutate(file);
  };

  return (
    <GeoCard>
      <GeoCardHeader
        title={t("geo.reportStudio.design.chatTitle")}
        meta={t("geo.reportStudio.design.chatSubtitle")}
        actions={
          <Button
            size="sm"
            variant="outline"
            className="text-destructive hover:text-destructive"
            disabled={resetMutation.isPending}
            onClick={() => {
              if (window.confirm(t("geo.reportStudio.design.resetConfirm"))) {
                resetMutation.mutate();
              }
            }}
          >
            {resetMutation.isPending
              ? t("geo.reportStudio.processing")
              : t("geo.reportStudio.design.reset")}
          </Button>
        }
      />
      <GeoCardBody className="space-y-3">
        <ScrollArea className="h-[420px] pr-3">
          <div className="space-y-3">
            {turns.length === 0 && (
              <p className="text-sm text-muted-foreground">
                {t("geo.reportStudio.design.chatEmpty")}
              </p>
            )}
            {turns.map((turn) => (
              <div
                key={turn.id}
                className={`text-sm rounded-md px-3 py-2 ${
                  turn.role === "user" ? "bg-muted/60 ml-8" : "bg-primary/5 mr-8"
                }`}
              >
                <div className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground mb-1">
                  {turn.role === "user"
                    ? t("geo.reportStudio.design.you")
                    : t("geo.reportStudio.design.expertAi")}
                </div>
                <p className="whitespace-pre-wrap">{turn.content}</p>
              </div>
            ))}
            {sendMutation.data && (
              <div className="text-sm rounded-md px-3 py-2 bg-primary/5 mr-8">
                <div className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground mb-1">
                  {t("geo.reportStudio.design.expertAi")}
                </div>
                <p className="whitespace-pre-wrap">{sendMutation.data.assistant_reply}</p>
                {sendMutation.data.clarifying_questions.length > 0 && (
                  <ul className="mt-2 list-disc list-inside text-muted-foreground">
                    {sendMutation.data.clarifying_questions.map((q, i) => (
                      <li key={i}>{q}</li>
                    ))}
                  </ul>
                )}
                {sendMutation.data.narrative_master_generation && (
                  <div className="mt-2 text-xs space-y-1">
                    {sendMutation.data.narrative_master_generation.row_count > 0 && (
                      <p className="text-muted-foreground">
                        {t("geo.reportStudio.design.narrativeMasterGenerated", {
                          count: sendMutation.data.narrative_master_generation.row_count,
                          signals:
                            sendMutation.data.narrative_master_generation.signals_generated.join(
                              ", ",
                            ),
                        })}
                      </p>
                    )}
                    {sendMutation.data.narrative_master_generation.errors.length > 0 && (
                      <p className="text-destructive">
                        {t("geo.reportStudio.design.narrativeMasterGenerationErrors", {
                          errors: sendMutation.data.narrative_master_generation.errors.join(" / "),
                        })}
                      </p>
                    )}
                  </div>
                )}
                {sendMutation.data.report_template_generation && (
                  <p className="mt-2 text-xs">
                    <a
                      className="underline underline-offset-2 text-primary"
                      href={geoReportTemplatePreviewUrl(projectId)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {t("geo.reportStudio.design.templatePreviewLink", {
                        count: sendMutation.data.report_template_generation.section_count,
                      })}
                    </a>
                  </p>
                )}
              </div>
            )}
            {uploadMutation.data && (
              <div className="text-sm rounded-md px-3 py-2 bg-primary/5 mr-8">
                <div className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground mb-1">
                  {t("geo.reportStudio.design.expertAi")}
                </div>
                <p className="whitespace-pre-wrap">
                  {uploadMutation.data.assistant_reply ?? uploadMutation.data.message}
                </p>
                {uploadMutation.data.kind === "report_template" && uploadMutation.data.success && (
                  <p className="mt-2 text-xs">
                    <a
                      className="underline underline-offset-2 text-primary"
                      href={geoReportTemplatePreviewUrl(projectId)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {t("geo.reportStudio.design.templatePreviewLink", {
                        count: uploadMutation.data.section_count ?? 0,
                      })}
                    </a>
                  </p>
                )}
              </div>
            )}
          </div>
        </ScrollArea>
        {sendMutation.isError && (
          <p className="text-xs text-destructive">{(sendMutation.error as Error).message}</p>
        )}
        {uploadMutation.isError && (
          <p className="text-xs text-destructive">{(uploadMutation.error as Error).message}</p>
        )}
        <div className="flex gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv,.html,.htm"
            className="hidden"
            onChange={handleFileSelected}
          />
          <Button
            size="sm"
            variant="outline"
            disabled={uploadMutation.isPending}
            onClick={() => fileInputRef.current?.click()}
            title={t("geo.reportStudio.design.attachFileHint")}
          >
            {uploadMutation.isPending
              ? t("geo.reportStudio.processing")
              : t("geo.reportStudio.design.attachFile")}
          </Button>
          <Textarea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder={t("geo.reportStudio.design.chatPlaceholder")}
            className="text-sm min-h-[60px]"
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleSend();
            }}
          />
          <Button
            size="sm"
            disabled={!message.trim() || sendMutation.isPending}
            onClick={handleSend}
          >
            {sendMutation.isPending
              ? t("geo.reportStudio.processing")
              : t("geo.reportStudio.design.chatSend")}
          </Button>
        </div>
        <div className="flex items-center justify-between gap-2 border-t border-border pt-2">
          <p className="text-xs text-muted-foreground">
            {hasPendingChanges
              ? t("geo.reportStudio.design.hasPendingChanges")
              : t("geo.reportStudio.design.noPendingChanges")}
          </p>
          <Button
            size="sm"
            variant={hasPendingChanges ? "default" : "outline"}
            disabled={!hasPendingChanges || fixMutation.isPending}
            onClick={() => fixMutation.mutate()}
          >
            {fixMutation.isPending
              ? t("geo.reportStudio.processing")
              : t("geo.reportStudio.design.fix")}
          </Button>
        </div>
        {fixMutation.isError && (
          <p className="text-xs text-destructive">{(fixMutation.error as Error).message}</p>
        )}
        {fixMutation.data?.narrative_master_generation && (
          <div className="text-xs space-y-1 rounded-md border border-border p-3">
            {fixMutation.data.narrative_master_generation.row_count > 0 && (
              <p className="text-muted-foreground">
                {t("geo.reportStudio.design.narrativeMasterGenerated", {
                  count: fixMutation.data.narrative_master_generation.row_count,
                  signals:
                    fixMutation.data.narrative_master_generation.signals_generated.join(", "),
                })}
              </p>
            )}
            {fixMutation.data.narrative_master_generation.errors.length > 0 && (
              <p className="text-destructive">
                {t("geo.reportStudio.design.narrativeMasterGenerationErrors", {
                  errors: fixMutation.data.narrative_master_generation.errors.join(" / "),
                })}
              </p>
            )}
          </div>
        )}
        <div className="flex items-center justify-between gap-2 border-t border-border pt-2">
          <div className="flex flex-wrap gap-2">
            {(versionsQuery.data?.versions ?? []).map((v) => (
              <Badge key={v.id} variant="outline">
                v{v.version} — {new Date(v.published_at).toLocaleDateString()}
              </Badge>
            ))}
            {(!versionsQuery.data || versionsQuery.data.versions.length === 0) && (
              <p className="text-xs text-muted-foreground">
                {t("geo.reportStudio.design.noVersionsYet")}
              </p>
            )}
          </div>
          <Button
            size="sm"
            variant="outline"
            disabled={publishMutation.isPending}
            onClick={() => publishMutation.mutate(undefined)}
          >
            {publishMutation.isPending
              ? t("geo.reportStudio.processing")
              : t("geo.reportStudio.design.publish")}
          </Button>
        </div>
        {publishMutation.isError && (
          <p className="text-xs text-destructive">{(publishMutation.error as Error).message}</p>
        )}
        {resetMutation.isError && (
          <p className="text-xs text-destructive">{(resetMutation.error as Error).message}</p>
        )}
        {resetMutation.isSuccess && (
          <p className="text-xs text-muted-foreground">{t("geo.reportStudio.design.resetDone")}</p>
        )}
        {publishMutation.data && (
          <p className="text-xs text-muted-foreground">
            Published v{publishMutation.data.design_version.version}
          </p>
        )}
      </GeoCardBody>
    </GeoCard>
  );
}

// ---------------------------------------------------------------------------
// Assetsカード（設計確定後のダウンロードリンク一式、常時表示。2026-09-16追加）。
// runId===nullかつhasFixedDesignの間、GeoReportPipelinePanelから呼ばれる。
// ---------------------------------------------------------------------------

function _assetItemCount(asset: GeoReportProjectAsset | undefined): number | null {
  if (!asset) return null;
  if (typeof asset.row_count === "number") return asset.row_count;
  const content = asset.content ?? {};
  const rows = content.rows;
  if (Array.isArray(rows)) return rows.length;
  const sections = content.sections;
  if (Array.isArray(sections)) return sections.length;
  return null;
}

export function DesignAssetsCard({ projectId }: { projectId: string }) {
  const { t } = useLanguage();
  const stateQuery = useGeoReportDesignState(projectId);
  const narrativeMasterDraftQuery = useGeoReportAssetDraft(projectId, "narrative_master");
  const narrativeMasterPublishedQuery = useGeoReportAssetPublished(projectId, "narrative_master");
  const reportTemplateDraftQuery = useGeoReportAssetDraft(projectId, "report_template");
  const reportTemplatePublishedQuery = useGeoReportAssetPublished(projectId, "report_template");

  const state = stateQuery.data?.design_state;
  if (!state || !hasFixedDesign(state)) return null;
  const dirty = hasDesignChangesSinceFix(state);

  const narrativeMasterDraft = narrativeMasterDraftQuery.data?.asset;
  const narrativeMasterPublished = narrativeMasterPublishedQuery.data?.asset;
  const reportTemplateDraft = reportTemplateDraftQuery.data?.asset;
  const reportTemplatePublished = reportTemplatePublishedQuery.data?.asset;

  const statusRows: Array<{
    labelKey: string;
    draft?: GeoReportProjectAsset;
    published?: GeoReportProjectAsset;
    draftUrl: string;
    publishedUrl: string;
  }> = [
    {
      labelKey: "geo.reportStudio.design.status.narrativeMaster",
      draft: narrativeMasterDraft,
      published: narrativeMasterPublished,
      draftUrl: geoReportNarrativeMasterDownloadUrl(projectId, "draft"),
      publishedUrl: geoReportNarrativeMasterDownloadUrl(projectId, "published"),
    },
    {
      labelKey: "geo.reportStudio.design.status.reportTemplate",
      draft: reportTemplateDraft,
      published: reportTemplatePublished,
      draftUrl: geoReportTemplatePreviewUrl(projectId, "draft"),
      publishedUrl: geoReportTemplatePreviewUrl(projectId, "published"),
    },
  ];

  return (
    <GeoCard className={dirty ? "border-amber-500/50" : undefined}>
      <GeoCardHeader title={t("geo.reportStudio.design.assets.title")} />
      <GeoCardBody className="space-y-3">
        {dirty && (
          <p className="text-xs text-amber-600">
            {t("geo.reportStudio.design.assets.dirtyNotice")}
          </p>
        )}
        <div className="flex flex-wrap gap-3 text-xs">
          <a
            className="underline underline-offset-2 text-primary"
            href={geoReportReportDesignDownloadUrl(projectId)}
            target="_blank"
            rel="noreferrer"
          >
            {t("geo.reportStudio.design.downloadReportDesign")}
          </a>
          <a
            className="underline underline-offset-2 text-primary"
            href={geoReportSignalsDownloadUrl(projectId)}
            target="_blank"
            rel="noreferrer"
          >
            {t("geo.reportStudio.design.downloadSignals")}
          </a>
        </div>
        <div className="space-y-2 text-xs">
          {statusRows.map((row) => {
            const draftCount = _assetItemCount(row.draft);
            const publishedCount = _assetItemCount(row.published);
            return (
              <div key={row.labelKey} className="flex items-center justify-between gap-2">
                <span className="text-muted-foreground">{t(row.labelKey)}</span>
                <div className="flex items-center gap-3">
                  {row.draft ? (
                    <a
                      className="underline underline-offset-2 text-primary"
                      href={row.draftUrl}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {t("geo.reportStudio.design.status.draft", { count: draftCount ?? 0 })}
                    </a>
                  ) : (
                    <span className="text-muted-foreground">
                      {t("geo.reportStudio.design.status.noDraft")}
                    </span>
                  )}
                  {row.published ? (
                    <a
                      className="underline underline-offset-2 text-primary"
                      href={row.publishedUrl}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {t("geo.reportStudio.design.status.published", {
                        version: row.published.version ?? 0,
                        count: publishedCount ?? 0,
                      })}
                    </a>
                  ) : (
                    <span className="text-muted-foreground">
                      {t("geo.reportStudio.design.status.noPublished")}
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
        <QueryAccordionSection projectId={projectId} queries={state.queries ?? []} />
      </GeoCardBody>
    </GeoCard>
  );
}

// ---------------------------------------------------------------------------
// Query（bs_query_plan.csv、Design Stateのqueriesをそのままserialize、変換ロジック無し）。
// 2026-09-16再設計: 17レース分等を常時全展開すると縦に長大化するため、Signal単位の
// 1行サマリ＋accordion展開に変更。展開時はA群/B群等をワード1行ずつ、引用符・カンマ等の
// 装飾を付けないプレーンテキストブロックとして表示する（コピペしやすい形式、
// ユーザー要望）。
// ---------------------------------------------------------------------------

const BS_QUERY_PLAN_COLUMNS = [
  "signal_id",
  "report_topic_label",
  "fixed_query_group_a",
  "fixed_query_group_b",
  "event_terms",
  "exclude_terms",
  "continuity_role",
  "collection_notes",
] as const;

function _wordList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((v) => String(v));
  if (typeof value === "string" && value.trim()) return [value];
  return [];
}

function _queryRowToPlainTextBlock(row: Record<string, unknown>): string {
  const lines: string[] = [];
  const signalId = String(row.signal_id ?? "");
  const label = String(row.report_topic_label ?? "");
  lines.push(`${signalId}｜${label}`);
  lines.push("A群", ..._wordList(row.fixed_query_group_a), "");
  lines.push("B群", ..._wordList(row.fixed_query_group_b), "");
  lines.push("Event terms", ..._wordList(row.event_terms), "");
  lines.push("除外語", ..._wordList(row.exclude_terms), "");
  const notes = String(row.collection_notes ?? "");
  if (notes) lines.push("収集メモ", notes);
  return lines.join("\n");
}

function _queryRowToTsv(row: Record<string, unknown>): string {
  return BS_QUERY_PLAN_COLUMNS.map((col) => {
    const value = row[col];
    return Array.isArray(value) ? value.join("|") : String(value ?? "");
  }).join("\t");
}

function QueryAccordionSection({
  projectId,
  queries,
}: {
  projectId: string;
  queries: Array<Record<string, unknown>>;
}) {
  const { t } = useLanguage();
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [copiedAll, setCopiedAll] = useState(false);
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);

  const toggle = (i: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  };

  const handleCopyOne = async (i: number, row: Record<string, unknown>) => {
    await navigator.clipboard.writeText(_queryRowToPlainTextBlock(row));
    setCopiedIndex(i);
    setTimeout(() => setCopiedIndex(null), 1500);
  };

  const handleCopyAll = async () => {
    const text = queries.map(_queryRowToPlainTextBlock).join("\n\n");
    await navigator.clipboard.writeText(text);
    setCopiedAll(true);
    setTimeout(() => setCopiedAll(false), 1500);
  };

  return (
    <div className="border-t border-border pt-3">
      <div className="flex items-center justify-between mb-1">
        <p className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
          {t("geo.reportStudio.design.queries")} ({queries.length})
        </p>
        {queries.length > 0 && (
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={handleCopyAll}>
              {copiedAll
                ? t("geo.reportStudio.design.copied")
                : t("geo.reportStudio.design.copyAll")}
            </Button>
            <a
              className="text-xs underline underline-offset-2 self-center"
              href={geoReportQueryPlanExportUrl(projectId)}
              target="_blank"
              rel="noreferrer"
            >
              {t("geo.reportStudio.design.download")}
            </a>
          </div>
        )}
      </div>
      {queries.length === 0 && (
        <p className="text-sm text-muted-foreground">{t("geo.reportStudio.design.noQueriesYet")}</p>
      )}
      <div className="space-y-1">
        {queries.map((q, i) => (
          <div key={i} className="border border-border rounded-md">
            <div className="flex items-center justify-between gap-2 px-2 py-1.5 text-xs">
              <button className="flex-1 text-left" onClick={() => toggle(i)}>
                {String(q.report_topic_label ?? q.signal_id ?? "")}
              </button>
              <Badge variant="outline">{t("geo.reportStudio.design.queryReady")}</Badge>
              <button
                className="underline underline-offset-2 text-primary"
                onClick={() => toggle(i)}
              >
                {expanded.has(i)
                  ? t("geo.reportStudio.design.queryHide")
                  : t("geo.reportStudio.design.queryShow")}
              </button>
              <button
                className="underline underline-offset-2 text-primary"
                onClick={() => handleCopyOne(i, q)}
              >
                {copiedIndex === i
                  ? t("geo.reportStudio.design.copied")
                  : t("geo.reportStudio.design.copyAll")}
              </button>
            </div>
            {expanded.has(i) && (
              <pre className="whitespace-pre-wrap text-xs px-2 pb-2 pt-0 font-mono">
                {_queryRowToPlainTextBlock(q)}
              </pre>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
