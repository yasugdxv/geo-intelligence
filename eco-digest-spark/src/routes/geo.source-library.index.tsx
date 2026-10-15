import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { AlertTriangle, ArrowLeft, Plus, Search, Undo2 } from "lucide-react";
import { TopBar } from "@/components/top-bar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { DataFreshnessBadge } from "@/components/geo/data-freshness";
import {
  ArchiveSourceDialog,
  CreateSourceDialog,
  EditSourceDialog,
  SourceTagChip,
} from "@/components/geo/source-library-parts";
import { useLanguage } from "@/lib/i18n";
import {
  MEDIA_TYPES,
  useCreateResearchProject,
  useRestoreSource,
  useSourceLibraryMeta,
  useSourceList,
  useSourceSchemaStatus,
  type GeoSource,
  type SourceListFilters,
} from "@/lib/geoSourceLibraryApi";
import { addPick, clearPickContext, getPickContext, setPickContext } from "@/lib/sourcePickStore";

// 一覧（Source Library）。選択モード（Source Selectorからの遷移）は ?pick=1
export const Route = createFileRoute("/geo/source-library/")({
  validateSearch: (search: Record<string, unknown>): { pick?: string } => ({
    pick: typeof search.pick === "string" ? search.pick : undefined,
  }),
  component: SourceLibraryPage,
});

const ANY = "__any";
const PAGE_SIZE = 20;

const AI_MARK = "【AI生成・要確認】";

/** 説明の先頭の「【AI生成・要確認】」を外し、印は別のバッジで出す */
function splitAiMark(description: string | null | undefined): { ai: boolean; text: string } {
  const d = (description ?? "").trim();
  return d.startsWith(AI_MARK) ? { ai: true, text: d.slice(AI_MARK.length).trim() } : { ai: false, text: d };
}

function SourceLibraryPage() {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const { pick } = Route.useSearch();

  // 検索入力（確定した文言のみAPIへ渡す）と絞り込み
  const [nlInput, setNlInput] = useState("");
  const [nlText, setNlText] = useState("");
  const [filters, setFilters] = useState<SourceListFilters>({
    sort: "updated_at",
    order: "desc",
    page: 1,
    page_size: PAGE_SIZE,
  });
  const [regionDraft, setRegionDraft] = useState("");
  const [languageDraft, setLanguageDraft] = useState("");
  const patchFilters = (p: Partial<SourceListFilters>) =>
    setFilters((f) => ({ ...f, ...p, page: p.page ?? 1 }));

  // 複数選択（ページをまたいで保持。名前は調査テーマ名の生成に使う）
  const [selected, setSelected] = useState<Map<string, string>>(new Map());
  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<GeoSource | null>(null);
  const [archiving, setArchiving] = useState<GeoSource | null>(null);
  // 選択モードのpick状態はsessionStorageなので、更新時に再描画させるためのカウンタ
  const [, setPickTick] = useState(0);

  const list = useSourceList(filters, nlText);
  const meta = useSourceLibraryMeta();
  const schema = useSourceSchemaStatus();
  const restore = useRestoreSource();
  const research = useCreateResearchProject();

  const pickCtx = pick ? getPickContext() : null;
  const sources = list.data?.sources ?? [];
  const page = filters.page ?? 1;
  const missingPhases = Object.entries(schema.data?.phases ?? {})
    .filter(([, ok]) => !ok)
    .map(([k]) => k);
  const interp = list.data?.interpretation;

  const toggle = (s: GeoSource, on: boolean) =>
    setSelected((prev) => {
      const next = new Map(prev);
      if (on) next.set(s.source_id, s.name);
      else next.delete(s.source_id);
      return next;
    });

  const goDetail = (id: string) =>
    navigate({ to: "/geo/source-library/$sourceId", params: { sourceId: id } });

  const startResearch = () => {
    const ids = [...selected.keys()];
    const names = [...selected.values()];
    const theme =
      names.length === 1
        ? names[0]
        : t("geo.sourceLibrary.researchTheme", { name: names[0], count: names.length - 1 });
    research.mutate(
      { theme, source_ids: ids, origin: "source_library" },
      {
        onSuccess: (r) =>
          navigate({
            to: "/geo/report-studio/$projectId",
            params: { projectId: r.project.project_id },
            search: { tab: "research" } as never,
          } as never),
      },
    );
  };

  const startMonitoring = () => {
    setPickContext({
      returnTo: "/geo/monitoring-themes",
      label: "Monitoring",
      selectedIds: [...selected.keys()],
    });
    navigate({ to: "/geo/monitoring-themes" } as never);
  };

  const th = (key: string) => <TableHead className="whitespace-nowrap">{t(key)}</TableHead>;

  return (
    <>
      <TopBar breadcrumb={[{ label: t("nav.geoSourcesDB") }]} />
      <main className="flex-1 max-w-[1280px] w-full mx-auto px-8 py-8 space-y-5">
        {/* 選択モードバナー */}
        {pickCtx && (
          <div className="rounded-md border border-primary/40 bg-primary/5 px-4 py-3 flex flex-wrap items-center gap-3 text-sm">
            <span className="font-medium flex-1">
              {t("geo.pickMode.banner", {
                label: pickCtx.label,
                count: pickCtx.selectedIds.length,
              })}
            </span>
            <Button
              size="sm"
              variant="outline"
              onClick={() => navigate({ to: pickCtx.returnTo } as never)}
            >
              <ArrowLeft className="h-3.5 w-3.5 mr-1" />
              {t("geo.pickMode.back")}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                clearPickContext();
                navigate({ to: "/geo/source-library", search: {} } as never);
              }}
            >
              {t("geo.pickMode.cancel")}
            </Button>
          </div>
        )}

        <div className="flex flex-wrap items-start gap-4 justify-between">
          <div className="space-y-1">
            <h1 className="text-editorial text-2xl">{t("nav.geoSourcesDB")}</h1>
            <p className="text-sm text-muted-foreground max-w-2xl">
              {t("geo.sourceLibrary.subtitle")}
            </p>
          </div>
          <div className="flex items-start gap-3">
            <DataFreshnessBadge freshness={meta.data?.data_freshness} showNote />
            <Button onClick={() => setCreateOpen(true)}>
              <Plus className="h-4 w-4 mr-1" />
              {t("geo.sourceLibrary.create")}
            </Button>
          </div>
        </div>

        {/* migration未適用 / 旧スキーマ */}
        {schema.data && !schema.data.all_applied && (
          <div className="rounded-md border border-geo-amber/50 bg-geo-amber-soft px-4 py-3 text-xs text-geo-amber flex gap-2">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <div>
              {t("geo.sourceLibrary.migrationBanner")}
              {missingPhases.length > 0 && (
                <span className="ml-1 opacity-80">({missingPhases.join(", ")})</span>
              )}
            </div>
          </div>
        )}
        {list.data?.legacy_mode && (
          <div className="rounded-md border border-border bg-muted px-4 py-2 text-xs text-muted-foreground">
            {t("geo.sourceLibrary.legacyMode")}
          </div>
        )}

        {/* 自然言語検索 */}
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            setNlText(nlInput.trim());
            patchFilters({});
          }}
        >
          <Input
            value={nlInput}
            onChange={(e) => setNlInput(e.target.value)}
            placeholder={t("geo.sourceLibrary.searchPlaceholder")}
            className="flex-1"
          />
          <Button type="submit">
            <Search className="h-4 w-4 mr-1" />
            {t("geo.sourceLibrary.search")}
          </Button>
          {nlText && (
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                setNlInput("");
                setNlText("");
              }}
            >
              {t("geo.sourceLibrary.clearSearch")}
            </Button>
          )}
        </form>

        {/* 解釈結果chip */}
        {nlText && interp?.mode === "semantic" && (
          <div className="text-xs text-muted-foreground space-y-0.5" data-testid="source-library-semantic-note">
            {interp.understood && <p>{t("geo.sourceSelector.understood", { text: interp.understood })}</p>}
            <p>{t("geo.sourceSelector.semanticNote")}</p>
          </div>
        )}
        {nlText && interp && (
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <span className="text-muted-foreground">{t("geo.sourceSelector.interpreted")}:</span>
            {interp.filters.keywords.map((k) => (
              <Badge key={`k-${k}`} variant="secondary">
                {k}
              </Badge>
            ))}
            {interp.filters.media_type && (
              <Badge variant="secondary">
                {t(`geo.sourceLibrary.mediaType.${interp.filters.media_type}`)}
              </Badge>
            )}
            {interp.filters.region && <Badge variant="secondary">{interp.filters.region}</Badge>}
            {interp.filters.language && (
              <Badge variant="secondary">{interp.filters.language}</Badge>
            )}
            {interp.filters.is_primary && (
              <Badge variant="secondary">{t("geo.sourceLibrary.primary")}</Badge>
            )}
            {interp.filters.tags.map((k) => (
              <Badge key={`t-${k}`} variant="secondary">
                #{k}
              </Badge>
            ))}
            {interp.error ? (
              <span className="text-geo-amber">{interp.error}</span>
            ) : (
              !interp.used_llm && <span className="text-geo-amber">{t("geo.sourceSelector.llmFallback")}</span>
            )}
          </div>
        )}
        {nlText && (
          <p className="text-[11px] text-muted-foreground">{t("geo.sourceLibrary.nlFilterNote")}</p>
        )}

        {/* Filter / Sort */}
        <div className="card-paper rounded-lg p-4 grid gap-3 md:grid-cols-4 items-end">
          <div className="space-y-1">
            <Label className="text-xs">{t("geo.sourceLibrary.col.mediaType")}</Label>
            <Select
              value={filters.media_type ?? ANY}
              onValueChange={(v) => patchFilters({ media_type: v === ANY ? undefined : v })}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>{t("geo.sourceLibrary.any")}</SelectItem>
                {MEDIA_TYPES.map((m) => (
                  <SelectItem key={m} value={m}>
                    {t(`geo.sourceLibrary.mediaType.${m}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">{t("geo.sourceLibrary.col.region")}</Label>
            <Input
              value={regionDraft}
              onChange={(e) => setRegionDraft(e.target.value)}
              onBlur={() => patchFilters({ region: regionDraft.trim() || undefined })}
              onKeyDown={(e) =>
                e.key === "Enter" && patchFilters({ region: regionDraft.trim() || undefined })
              }
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">{t("geo.sourceLibrary.col.language")}</Label>
            <Input
              value={languageDraft}
              placeholder="ja / en"
              onChange={(e) => setLanguageDraft(e.target.value)}
              onBlur={() => patchFilters({ language: languageDraft.trim() || undefined })}
              onKeyDown={(e) =>
                e.key === "Enter" && patchFilters({ language: languageDraft.trim() || undefined })
              }
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">{t("geo.sourceLibrary.createdAfter")}</Label>
            <Input
              type="date"
              value={filters.created_after ?? ""}
              onChange={(e) => patchFilters({ created_after: e.target.value || undefined })}
            />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <Switch
              checked={!!filters.is_primary}
              onCheckedChange={(c) => patchFilters({ is_primary: c ? true : undefined })}
            />
            {t("geo.sourceSelector.primaryOnly")}
          </label>
          <label className="flex items-center gap-2 text-sm">
            <Switch
              checked={!!filters.include_archived}
              onCheckedChange={(c) => patchFilters({ include_archived: c ? true : undefined })}
            />
            {t("geo.sourceLibrary.showArchived")}
          </label>
          <div className="space-y-1">
            <Label className="text-xs">{t("geo.sourceLibrary.sort")}</Label>
            <Select
              value={filters.sort ?? "updated_at"}
              onValueChange={(v) => patchFilters({ sort: v as SourceListFilters["sort"] })}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {["name", "created_at", "updated_at", "last_ingested"].map((s) => (
                  <SelectItem key={s} value={s}>
                    {t(`geo.sourceLibrary.sort.${s}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">{t("geo.sourceLibrary.order")}</Label>
            <Select
              value={filters.order ?? "desc"}
              onValueChange={(v) => patchFilters({ order: v as "asc" | "desc" })}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="asc">{t("geo.sourceLibrary.order.asc")}</SelectItem>
                <SelectItem value="desc">{t("geo.sourceLibrary.order.desc")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        {/* 複数選択アクション */}
        {selected.size > 0 && (
          <div className="rounded-md border border-border bg-muted/40 px-4 py-2 flex flex-wrap items-center gap-3 text-sm">
            <span className="flex-1">
              {t("geo.sourceSelector.selected", { count: selected.size })}
            </span>
            <Button size="sm" onClick={startResearch} disabled={research.isPending}>
              {t("geo.sourceLibrary.action.research")}
            </Button>
            <Button size="sm" variant="outline" onClick={startMonitoring}>
              {t("geo.sourceLibrary.action.monitoring")}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setSelected(new Map())}>
              {t("geo.sourceSelector.clear")}
            </Button>
            {research.isError && (
              <span className="w-full text-xs text-destructive">
                {(research.error as Error).message}
              </span>
            )}
          </div>
        )}

        {/* 一覧 */}
        {list.isError && !list.data ? (
          <div className="card-paper rounded-lg p-6 text-sm space-y-2">
            <p className="text-destructive">
              {t("geo.sourceLibrary.loadFailed", { message: (list.error as Error).message })}
            </p>
            <Button size="sm" variant="outline" onClick={() => list.refetch()}>
              {t("geo.sourceLibrary.retry")}
            </Button>
          </div>
        ) : list.isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : sources.length === 0 ? (
          <div className="card-paper rounded-lg p-8 text-center text-sm text-muted-foreground space-y-2">
            <p>{t("geo.sourceLibrary.empty")}</p>
            <p className="text-xs">{t("geo.sourceLibrary.emptyHint")}</p>
          </div>
        ) : (
          <div className="card-paper rounded-lg overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8" />
                  {th("geo.sourceLibrary.col.name")}
                  {th("geo.sourceLibrary.col.description")}
                  {th("geo.sourceLibrary.col.mediaType")}
                  {th("geo.sourceLibrary.col.region")}
                  {th("geo.sourceLibrary.col.language")}
                  {th("geo.sourceLibrary.col.primary")}
                  {th("geo.sourceLibrary.col.themeTags")}
                  {th("geo.sourceLibrary.col.actorTags")}
                  {th("geo.sourceLibrary.col.articles30d")}
                  {th("geo.sourceLibrary.col.updated")}
                  {th("geo.sourceLibrary.col.freshness")}
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {sources.map((s) => {
                  const tags = s.tags ?? [];
                  const themeTags = tags.filter((x) => x.tag_type === "theme");
                  const actorTags = tags.filter((x) => x.tag_type === "actor");
                  const archived = !!s.archived_at;
                  const picked = pickCtx?.selectedIds.includes(s.source_id);
                  return (
                    <TableRow
                      key={s.source_id}
                      className={`cursor-pointer ${archived ? "opacity-60" : ""}`}
                      onClick={() => goDetail(s.source_id)}
                    >
                      <TableCell onClick={(e) => e.stopPropagation()}>
                        <Checkbox
                          checked={selected.has(s.source_id)}
                          onCheckedChange={(c) => toggle(s, c === true)}
                          aria-label={s.name}
                        />
                      </TableCell>
                      <TableCell className="font-medium min-w-[160px] max-w-[220px] align-top">
                        <span className="block break-words">{s.name}</span>
                        {archived && (
                          <Badge variant="outline" className="mt-1 text-[10px]">
                            {t("geo.sourceLibrary.archived")}
                          </Badge>
                        )}
                      </TableCell>
                      <TableCell className="min-w-[260px] max-w-[380px] align-top" data-testid="source-description-cell">
                        {(() => {
                          const d = splitAiMark(s.description);
                          return d.text || d.ai ? (
                            <div className="space-y-1">
                              {d.ai && (
                                <Badge variant="outline" className="text-[10px] text-muted-foreground">
                                  {t("geo.sourceLibrary.aiGenerated")}
                                </Badge>
                              )}
                              <p className="text-xs text-muted-foreground line-clamp-4 break-words">{d.text}</p>
                            </div>
                          ) : (
                            <span className="text-xs text-muted-foreground">—</span>
                          );
                        })()}
                        {s.match_reason && (
                          <p className="text-xs mt-2 rounded-sm bg-primary/5 px-2 py-1" data-testid="source-match-reason">
                            <span className="font-medium text-primary">{t("geo.sourceSelector.matchReason")}: </span>
                            {s.match_reason}
                          </p>
                        )}
                        {s.match_caution && (
                          <p className="text-xs mt-1 rounded-sm bg-geo-amber-soft px-2 py-1 text-geo-amber" data-testid="source-match-caution">
                            <span className="font-medium">{t("geo.sourceSelector.matchCaution")}: </span>
                            {s.match_caution}
                          </p>
                        )}
                      </TableCell>
                      <TableCell className="text-xs">
                        {s.media_type ? t(`geo.sourceLibrary.mediaType.${s.media_type}`) : "—"}
                      </TableCell>
                      <TableCell className="text-xs">{s.region || "—"}</TableCell>
                      <TableCell className="text-xs">{s.languages?.join(", ") || "—"}</TableCell>
                      <TableCell>
                        <Badge
                          variant={s.is_primary ? "default" : "secondary"}
                          className="text-[10px]"
                        >
                          {s.is_primary
                            ? t("geo.sourceLibrary.primary")
                            : t("geo.sourceLibrary.secondary")}
                        </Badge>
                      </TableCell>
                      <TableCell className="max-w-[200px]">
                        <div className="flex flex-wrap gap-1">
                          {themeTags.length
                            ? themeTags.map((x, i) => <SourceTagChip key={i} tag={x} />)
                            : "—"}
                        </div>
                      </TableCell>
                      <TableCell className="max-w-[200px]">
                        <div className="flex flex-wrap gap-1">
                          {actorTags.length
                            ? actorTags.map((x, i) => <SourceTagChip key={i} tag={x} />)
                            : "—"}
                        </div>
                      </TableCell>
                      <TableCell className="tabular-nums text-xs">
                        {s.article_count_30d ?? "—"}
                      </TableCell>
                      <TableCell className="text-xs whitespace-nowrap">
                        {s.updated_at.slice(0, 10)}
                      </TableCell>
                      <TableCell>
                        <DataFreshnessBadge freshness={s.freshness} />
                      </TableCell>
                      <TableCell onClick={(e) => e.stopPropagation()}>
                        <div className="flex gap-1 justify-end">
                          {pickCtx &&
                            (picked ? (
                              <Badge variant="secondary">{t("geo.pickMode.added")}</Badge>
                            ) : (
                              <Button
                                size="sm"
                                onClick={() => {
                                  addPick(s.source_id);
                                  setPickTick((n) => n + 1);
                                }}
                              >
                                {t("geo.sourceLibrary.pick.add")}
                              </Button>
                            ))}
                          {archived ? (
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={restore.isPending}
                              onClick={() => restore.mutate(s.source_id)}
                            >
                              <Undo2 className="h-3.5 w-3.5 mr-1" />
                              {t("geo.sourceLibrary.restore")}
                            </Button>
                          ) : (
                            <>
                              <Button size="sm" variant="ghost" onClick={() => setEditing(s)}>
                                {t("geo.sourceLibrary.edit")}
                              </Button>
                              <Button size="sm" variant="ghost" onClick={() => setArchiving(s)}>
                                {t("geo.sourceLibrary.archive")}
                              </Button>
                            </>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}

        {/* Pagination */}
        {sources.length > 0 && (
          <div className="flex items-center justify-end gap-3 text-sm">
            <span className="text-xs text-muted-foreground">
              {t("geo.sourceLibrary.page", { page })}
            </span>
            <Button
              size="sm"
              variant="outline"
              disabled={page <= 1}
              onClick={() => setFilters((f) => ({ ...f, page: page - 1 }))}
            >
              {t("geo.sourceLibrary.prev")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={!list.data?.has_more}
              onClick={() => setFilters((f) => ({ ...f, page: page + 1 }))}
            >
              {t("geo.sourceLibrary.next")}
            </Button>
          </div>
        )}
      </main>

      <CreateSourceDialog open={createOpen} onOpenChange={setCreateOpen} />
      {editing && <EditSourceDialog source={editing} onClose={() => setEditing(null)} />}
      {archiving && <ArchiveSourceDialog source={archiving} onClose={() => setArchiving(null)} />}
    </>
  );
}
