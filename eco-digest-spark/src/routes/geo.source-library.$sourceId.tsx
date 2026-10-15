import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { AlertTriangle, Check, ExternalLink, Sparkles, Trash2, Undo2 } from "lucide-react";
import { TopBar } from "@/components/top-bar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DataFreshnessBadge } from "@/components/geo/data-freshness";
import {
  ArchiveSourceDialog,
  EditSourceDialog,
  SourceTagChip,
} from "@/components/geo/source-library-parts";
import { useLanguage } from "@/lib/i18n";
import {
  TAG_TYPES,
  useAddDomain,
  useAddTag,
  useConfirmTag,
  useCreateResearchProject,
  useRemoveDomain,
  useRemoveTag,
  useRestoreSource,
  useSourceDetail,
  useSuggestTags,
  useToggleDomain,
  type SourceDetail,
  type TagType,
} from "@/lib/geoSourceLibraryApi";
import { addPick, getPickContext, setPickContext } from "@/lib/sourcePickStore";

export const Route = createFileRoute("/geo/source-library/$sourceId")({
  component: SourceDetailPage,
});

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="card-paper rounded-lg p-5 space-y-3">
      <h2 className="text-sm font-semibold">{title}</h2>
      {children}
    </section>
  );
}

const dash = (v: number | null | undefined) => (v === null || v === undefined ? "—" : v);

function SourceDetailPage() {
  const { t } = useLanguage();
  const { sourceId } = Route.useParams();
  const detail = useSourceDetail(sourceId);

  const crumbs = [
    { label: t("nav.geoSourcesDB"), href: "/geo/source-library" },
    { label: detail.data?.source.name ?? "…" },
  ];

  if (detail.isLoading) {
    return (
      <>
        <TopBar breadcrumb={crumbs} />
        <main className="flex-1 max-w-[1000px] w-full mx-auto px-8 py-8 space-y-4">
          <Skeleton className="h-10 w-1/2" />
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-40 w-full" />
        </main>
      </>
    );
  }
  if (detail.isError || !detail.data) {
    const msg = (detail.error as Error | null)?.message ?? "";
    const notFound = /404|not found/i.test(msg);
    return (
      <>
        <TopBar breadcrumb={crumbs} />
        <main className="flex-1 max-w-[1000px] w-full mx-auto px-8 py-8 space-y-3">
          <div className="card-paper rounded-lg p-6 text-sm space-y-3">
            <p className="text-destructive">
              {notFound
                ? t("geo.sourceLibrary.detail.notFound")
                : t("geo.sourceLibrary.loadFailed", { message: msg })}
            </p>
            <div className="flex gap-2">
              {!notFound && (
                <Button size="sm" variant="outline" onClick={() => detail.refetch()}>
                  {t("geo.sourceLibrary.retry")}
                </Button>
              )}
              <Button size="sm" variant="outline" asChild>
                <Link to="/geo/source-library">{t("geo.sourceLibrary.backToList")}</Link>
              </Button>
            </div>
          </div>
        </main>
      </>
    );
  }
  return <DetailBody data={detail.data} sourceId={sourceId} crumbs={crumbs} />;
}

function DetailBody({
  data,
  sourceId,
  crumbs,
}: {
  data: SourceDetail;
  sourceId: string;
  crumbs: { label: string; href?: string }[];
}) {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const {
    source,
    tags,
    domains,
    theme_links,
    legacy_theme,
    recent_articles,
    performance,
    freshness,
  } = data;
  const archived = !!source.archived_at;

  const suggest = useSuggestTags(sourceId);
  const addTag = useAddTag(sourceId);
  const confirmTag = useConfirmTag(sourceId);
  const removeTag = useRemoveTag(sourceId);
  const addDomain = useAddDomain(sourceId);
  const toggleDomain = useToggleDomain(sourceId);
  const removeDomain = useRemoveDomain(sourceId);
  const restore = useRestoreSource();
  const research = useCreateResearchProject();

  const [editing, setEditing] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [newTagType, setNewTagType] = useState<TagType>("theme");
  const [newTagValue, setNewTagValue] = useState("");
  const [newDomain, setNewDomain] = useState("");
  const [newDomainPrimary, setNewDomainPrimary] = useState(false);
  const [domainConflicts, setDomainConflicts] = useState<string[]>([]);

  const startResearch = () =>
    research.mutate(
      { theme: source.name, source_ids: [sourceId], origin: "source_library" },
      {
        onSuccess: (r) =>
          navigate({
            to: "/geo/report-studio/$projectId",
            params: { projectId: r.project.project_id },
            search: { tab: "research" } as never,
          } as never),
      },
    );

  const goMonitoring = () => {
    setPickContext({
      returnTo: "/geo/monitoring-themes",
      label: "Monitoring",
      selectedIds: [sourceId],
    });
    navigate({ to: "/geo/monitoring-themes" } as never);
  };

  const addToThemeDiscovery = () => {
    const ctx = getPickContext();
    if (ctx) {
      addPick(sourceId);
      navigate({ to: ctx.returnTo } as never);
      return;
    }
    setPickContext({
      returnTo: "/geo/theme-discovery",
      label: t("nav.geoThemeDiscovery"),
      selectedIds: [sourceId],
    });
    navigate({ to: "/geo/theme-discovery" } as never);
  };

  const submitTag = () => {
    const v = newTagValue.trim();
    if (!v) return;
    addTag.mutate({ tag_type: newTagType, tag_value: v }, { onSuccess: () => setNewTagValue("") });
  };
  const submitDomain = () => {
    const d = newDomain.trim();
    if (!d) return;
    addDomain.mutate(
      { domain: d, is_primary: newDomainPrimary },
      {
        onSuccess: (r) => {
          setDomainConflicts(r.conflicts);
          setNewDomain("");
          setNewDomainPrimary(false);
        },
      },
    );
  };

  return (
    <>
      <TopBar breadcrumb={crumbs} />
      <main className="flex-1 max-w-[1000px] w-full mx-auto px-8 py-8 space-y-5">
        {/* ヘッダー */}
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-editorial text-2xl">{source.name}</h1>
            {source.media_type && (
              <Badge variant="outline">
                {t(`geo.sourceLibrary.mediaType.${source.media_type}`)}
              </Badge>
            )}
            <Badge variant={source.is_primary ? "default" : "secondary"}>
              {source.is_primary
                ? t("geo.sourceLibrary.primary")
                : t("geo.sourceLibrary.secondary")}
            </Badge>
            {archived && <Badge variant="destructive">{t("geo.sourceLibrary.archived")}</Badge>}
          </div>
          {source.source_owner && (
            <p className="text-sm text-muted-foreground">
              {t("geo.sourceLibrary.detail.owner")}: {source.source_owner}
            </p>
          )}
          {source.description && <p className="text-sm">{source.description}</p>}
        </div>

        {archived && (
          <div className="rounded-md border border-border bg-muted px-4 py-3 text-sm flex items-center gap-3">
            <span className="flex-1">{t("geo.sourceLibrary.detail.archivedNote")}</span>
            <Button size="sm" disabled={restore.isPending} onClick={() => restore.mutate(sourceId)}>
              <Undo2 className="h-3.5 w-3.5 mr-1" />
              {t("geo.sourceLibrary.restore")}
            </Button>
          </div>
        )}

        {/* Actions */}
        <div className="flex flex-wrap gap-2">
          <Button onClick={startResearch} disabled={research.isPending}>
            {t("geo.sourceLibrary.detail.research")}
          </Button>
          <Button variant="outline" onClick={goMonitoring}>
            {t("geo.sourceLibrary.action.monitoring")}
          </Button>
          <Button variant="outline" onClick={addToThemeDiscovery}>
            {t("geo.sourceLibrary.detail.addToDiscovery")}
          </Button>
          {!archived && (
            <>
              <Button variant="ghost" onClick={() => setEditing(true)}>
                {t("geo.sourceLibrary.edit")}
              </Button>
              <Button variant="ghost" onClick={() => setArchiving(true)}>
                {t("geo.sourceLibrary.archive")}
              </Button>
            </>
          )}
        </div>
        {research.isError && (
          <p className="text-xs text-destructive">{(research.error as Error).message}</p>
        )}

        {/* 基本情報 */}
        <Section title={t("geo.sourceLibrary.detail.basic")}>
          <dl className="grid grid-cols-[120px_1fr] gap-y-2 text-sm">
            <dt className="text-muted-foreground">{t("geo.sourceLibrary.col.mediaType")}</dt>
            <dd>
              {source.media_type ? t(`geo.sourceLibrary.mediaType.${source.media_type}`) : "—"}
            </dd>
            <dt className="text-muted-foreground">{t("geo.sourceLibrary.col.region")}</dt>
            <dd>{source.region || "—"}</dd>
            <dt className="text-muted-foreground">{t("geo.sourceLibrary.col.language")}</dt>
            <dd>{source.languages?.join(", ") || "—"}</dd>
            <dt className="text-muted-foreground">URL</dt>
            <dd>
              {source.url ? (
                <a
                  href={source.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary hover:underline inline-flex items-center gap-1 break-all"
                >
                  {source.url}
                  <ExternalLink className="h-3 w-3 shrink-0" />
                </a>
              ) : (
                "—"
              )}
            </dd>
            <dt className="text-muted-foreground">{t("geo.sourceLibrary.form.thematicScope")}</dt>
            <dd>{source.thematic_scope || "—"}</dd>
            <dt className="text-muted-foreground">{t("geo.sourceLibrary.form.updateCadence")}</dt>
            <dd>{source.update_cadence || "—"}</dd>
            <dt className="text-muted-foreground">{t("geo.sourceLibrary.form.reliability")}</dt>
            <dd>
              {source.reliability ? t(`geo.sourceLibrary.reliability.${source.reliability}`) : "—"}
            </dd>
            {source.notes && (
              <>
                <dt className="text-muted-foreground">{t("geo.sourceLibrary.form.notes")}</dt>
                <dd className="whitespace-pre-wrap">{source.notes}</dd>
              </>
            )}
          </dl>
        </Section>

        {/* Tag */}
        <Section title={t("geo.sourceLibrary.detail.specialty")}>
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">{t("geo.sourceLibrary.detail.tagNote")}</p>
            <Button
              size="sm"
              variant="outline"
              disabled={suggest.isPending}
              onClick={() => suggest.mutate()}
            >
              <Sparkles className="h-3.5 w-3.5 mr-1" />
              {suggest.isPending
                ? t("geo.sourceLibrary.saving")
                : t("geo.sourceLibrary.detail.suggestTags")}
            </Button>
          </div>
          {(suggest.isError || suggest.data?.error) && (
            <p className="text-xs text-destructive">
              {t("geo.sourceLibrary.detail.suggestFailed", {
                message: suggest.data?.error ?? (suggest.error as Error).message,
              })}
            </p>
          )}
          {TAG_TYPES.map((type) => {
            const group = tags.filter((x) => x.tag_type === type);
            if (!group.length) return null;
            return (
              <div key={type} className="space-y-1">
                <div className="text-xs text-muted-foreground">
                  {t(`geo.sourceLibrary.tagType.${type}`)}
                </div>
                <div className="flex flex-wrap gap-2">
                  {group.map((tag) => {
                    const pending = tag.origin === "ai" && !tag.is_confirmed;
                    return (
                      <span
                        key={tag.tag_id ?? tag.tag_value}
                        className="inline-flex items-center gap-1"
                      >
                        <SourceTagChip tag={tag} className="text-xs" />
                        {tag.tag_id && pending && (
                          <button
                            type="button"
                            className="text-geo-green hover:opacity-80"
                            title={t("geo.sourceLibrary.detail.approve")}
                            onClick={() => confirmTag.mutate({ tagId: tag.tag_id! })}
                          >
                            <Check className="h-3.5 w-3.5" />
                          </button>
                        )}
                        {tag.tag_id && (
                          <button
                            type="button"
                            className="text-muted-foreground hover:text-destructive"
                            title={t("geo.sourceLibrary.detail.remove")}
                            onClick={() => removeTag.mutate(tag.tag_id!)}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        )}
                      </span>
                    );
                  })}
                </div>
              </div>
            );
          })}
          {tags.length === 0 && (
            <p className="text-xs text-muted-foreground">{t("geo.sourceLibrary.detail.noTags")}</p>
          )}
          <div className="flex gap-2 pt-2 border-t border-border/60">
            <Select value={newTagType} onValueChange={(v) => setNewTagType(v as TagType)}>
              <SelectTrigger className="w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TAG_TYPES.map((x) => (
                  <SelectItem key={x} value={x}>
                    {t(`geo.sourceLibrary.tagType.${x}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Input
              value={newTagValue}
              placeholder={t("geo.sourceLibrary.detail.tagPlaceholder")}
              onChange={(e) => setNewTagValue(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submitTag()}
            />
            <Button
              size="sm"
              disabled={!newTagValue.trim() || addTag.isPending}
              onClick={submitTag}
            >
              {t("geo.sourceLibrary.detail.addTag")}
            </Button>
          </div>
        </Section>

        {/* Domain Alias */}
        <Section title={t("geo.sourceLibrary.detail.domains")}>
          <p className="text-xs text-muted-foreground">
            {t("geo.sourceLibrary.detail.domainNote")}
          </p>
          {domainConflicts.length > 0 && (
            <div className="rounded-md border border-geo-amber/50 bg-geo-amber-soft p-3 text-xs text-geo-amber flex gap-2">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              <div>
                {t("geo.sourceLibrary.detail.domainConflict")}
                <div className="mt-1 opacity-80">{domainConflicts.join(", ")}</div>
              </div>
            </div>
          )}
          {domains.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              {t("geo.sourceLibrary.detail.noDomains")}
            </p>
          ) : (
            <ul className="divide-y divide-border/60">
              {domains.map((d) => (
                <li key={d.domain_id} className="py-2 flex items-center gap-3 text-sm">
                  <span className="flex-1 font-mono text-xs">{d.domain}</span>
                  {d.is_primary && (
                    <Badge variant="secondary">{t("geo.sourceLibrary.detail.primaryDomain")}</Badge>
                  )}
                  <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    <Switch
                      checked={d.match_enabled}
                      onCheckedChange={(c) =>
                        toggleDomain.mutate({ domainId: d.domain_id, matchEnabled: c })
                      }
                    />
                    {t("geo.sourceLibrary.detail.matchEnabled")}
                  </label>
                  <button
                    type="button"
                    className="text-muted-foreground hover:text-destructive"
                    title={t("geo.sourceLibrary.detail.remove")}
                    onClick={() => removeDomain.mutate(d.domain_id)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="flex items-center gap-2 pt-2 border-t border-border/60">
            <Input
              value={newDomain}
              placeholder="example.com"
              onChange={(e) => setNewDomain(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submitDomain()}
            />
            <label className="flex items-center gap-1.5 text-xs whitespace-nowrap">
              <Checkbox
                checked={newDomainPrimary}
                onCheckedChange={(c) => setNewDomainPrimary(c === true)}
              />
              {t("geo.sourceLibrary.detail.primaryDomain")}
            </label>
            <Button
              size="sm"
              disabled={!newDomain.trim() || addDomain.isPending}
              onClick={submitDomain}
            >
              {t("geo.sourceLibrary.detail.addDomain")}
            </Button>
          </div>
          {(addDomain.isError || toggleDomain.isError || removeDomain.isError) && (
            <p className="text-xs text-destructive">
              {((addDomain.error ?? toggleDomain.error ?? removeDomain.error) as Error).message}
            </p>
          )}
        </Section>

        {/* 最近の記事 */}
        <Section title={t("geo.sourceLibrary.detail.recentArticles")}>
          <DataFreshnessBadge freshness={freshness} showNote />
          {recent_articles.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {freshness.status !== "fresh"
                ? t("geo.sourceLibrary.detail.noArticlesStale")
                : t("geo.sourceLibrary.detail.noArticles")}
            </p>
          ) : (
            <ul className="divide-y divide-border/60">
              {recent_articles.map((a) => (
                <li key={a.external_signal_id} className="py-2 space-y-0.5">
                  <div className="text-sm font-medium">
                    {a.url ? (
                      <a
                        href={a.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="hover:underline inline-flex items-center gap-1"
                      >
                        {a.title || a.url}
                        <ExternalLink className="h-3 w-3 shrink-0" />
                      </a>
                    ) : (
                      (a.title ?? "—")
                    )}
                  </div>
                  <div className="text-[11px] text-muted-foreground">
                    {(a.published_at ?? a.created_at).slice(0, 10)}
                  </div>
                  {a.summary && (
                    <p className="text-xs text-muted-foreground line-clamp-2">{a.summary}</p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Section>

        {/* よく使われているTheme */}
        <Section title={t("geo.sourceLibrary.detail.themes")}>
          {theme_links.length === 0 && !legacy_theme ? (
            <p className="text-xs text-muted-foreground">
              {t("geo.sourceLibrary.detail.noThemes")}
            </p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {theme_links.map((l) => (
                <span key={l.link_id} className="inline-flex items-center gap-1.5 text-sm">
                  {l.theme?.name ?? l.theme_id}
                  <Badge variant="outline" className="text-[10px]">
                    {t(`geo.sourceLibrary.relation.${l.relation_type}`)}
                  </Badge>
                </span>
              ))}
              {legacy_theme && (
                <span className="inline-flex items-center gap-1.5 text-sm">
                  {legacy_theme.name}
                  <Badge variant="secondary" className="text-[10px]">
                    {t("geo.sourceLibrary.relation.legacy")}
                  </Badge>
                </span>
              )}
            </div>
          )}
        </Section>

        {/* Performance / Usage */}
        <Section title={t("geo.sourceLibrary.detail.performance")}>
          <dl className="grid grid-cols-2 md:grid-cols-3 gap-4 text-sm">
            {[
              ["articleTotal", performance.article_count_total],
              ["article30d", performance.article_count_30d],
              ["sourceSets", performance.source_set_count],
              ["monitoring", performance.monitoring_count],
              ["evidence", performance.evidence_document_count],
            ].map(([k, v]) => (
              <div key={k as string}>
                <dt className="text-xs text-muted-foreground">
                  {t(`geo.sourceLibrary.perf.${k}`)}
                </dt>
                <dd className="text-lg tabular-nums">{dash(v as number | null)}</dd>
              </div>
            ))}
          </dl>
        </Section>
      </main>

      {editing && <EditSourceDialog source={source} onClose={() => setEditing(false)} />}
      {archiving && (
        <ArchiveSourceDialog
          source={source}
          onClose={() => setArchiving(false)}
          onArchived={() => navigate({ to: "/geo/source-library", search: {} } as never)}
        />
      )}
    </>
  );
}
