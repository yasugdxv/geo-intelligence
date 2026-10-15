import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Loader2, Search } from "lucide-react";
import { TopBar } from "@/components/top-bar";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { getIosActorId } from "@/lib/geoIosAuthHeader";
import { EpistemicTypeBadge, RetrievalStatusBadge } from "@/components/geo/geo-epistemic-badge";
import { ReportDetailCard } from "@/components/geo/report-detail-card";
import { EntityDetailSheet, KnowledgeItemDetailSheet } from "@/components/geo/knowledge-detail-sheets";
import {
  useGeoKnowledgeEntities,
  useGeoKnowledgeItems,
  useGeoReports,
  useNaturalKnowledgeSearch,
  useSearchEntities,
  useSearchKnowledgeItems,
  useSearchReports,
  useUploadReport,
  isAiApproved,
  type GeoEntity,
  type GeoKnowledgeItem,
} from "@/lib/geoKnowledgeApi";
import { useCreateGeoTheme, useGeoThemes } from "@/lib/api";
import { useLanguage } from "@/lib/i18n";

export const Route = createFileRoute("/geo/knowledge-search")({
  component: GeoKnowledgeSearchPage,
});

const KNOWLEDGE_TYPES = [
  "country_foundation",
  "actor_profile",
  "party_profile",
  "relationship_assessment",
  "historical_path_dependency",
  "precedent",
  "mechanism",
  "reasoning_rule",
  "indicator",
  "foundation_update",
  "case_finding",
  "leverage_card",
];
const STATUSES = [
  "candidate",
  "in_review",
  "active",
  "contested",
  "rejected",
  "superseded",
  "deprecated",
  "archived",
];

function statusVariant(status: string): "default" | "secondary" | "outline" | "destructive" {
  if (status === "active") return "default";
  if (status === "contested") return "destructive";
  if (status === "candidate" || status === "in_review") return "outline";
  return "secondary";
}

// DB制約（sql/2026-08-21_geo_report_upload.sql）通りの固定選択肢。自由記述にしない。
const REPORT_KINDS = ["internal_report", "external_report"] as const;
// 機密区分（confidentiality）はDB上は4値enumだが、現時点で運用ルールが無いためUIからは
// 一旦外す（バックエンドのデフォルト"internal"のまま送る。ルールが決まったら復活させる）。

// ボタンをクリックしてPopoverを開かないと入力欄が現れないコンボボックスは分かりにくい
// というフィードバックを受け、常に見えているテキスト入力＋その下に候補一覧、という
// 素直な作りに変更した（隠れたUIを無くす）。
export function ThemeCombobox({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const { t } = useLanguage();
  const themesQuery = useGeoThemes();
  const createThemeMutation = useCreateGeoTheme();
  const [search, setSearch] = useState("");
  const themes = themesQuery.data?.themes ?? [];
  const selected = themes.find((th) => th.id === value);

  if (selected) {
    return (
      <div className="flex items-center gap-2">
        <div className="flex-1 h-9 rounded-md border border-input bg-muted/40 px-3 flex items-center text-sm">
          {selected.nameJa || selected.nameEn || selected.code}
        </div>
        <Button type="button" variant="outline" size="sm" onClick={() => onChange("")}>
          {t("geo.knowledge.search.uploadReportThemeChange")}
        </Button>
      </div>
    );
  }

  const trimmedSearch = search.trim();
  const lowerSearch = trimmedSearch.toLowerCase();
  const filtered = trimmedSearch
    ? themes.filter(
        (th) =>
          (th.nameJa ?? "").toLowerCase().includes(lowerSearch) ||
          (th.nameEn ?? "").toLowerCase().includes(lowerSearch) ||
          th.code.toLowerCase().includes(lowerSearch),
      )
    : themes;
  const hasExactMatch = themes.some(
    (th) =>
      (th.nameJa ?? "").trim() === trimmedSearch || (th.nameEn ?? "").trim() === trimmedSearch,
  );
  const canCreate = trimmedSearch.length > 0 && !hasExactMatch;

  const onCreate = () => {
    if (!canCreate) return;
    createThemeMutation.mutate(
      { nameJa: trimmedSearch },
      {
        onSuccess: (result) => {
          onChange(result.theme.id);
          setSearch("");
        },
      },
    );
  };

  return (
    <div className="space-y-1">
      <Input
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder={t("geo.knowledge.search.uploadReportThemeSearchPlaceholder")}
      />
      <div className="border border-input rounded-md max-h-48 overflow-y-auto divide-y divide-border">
        {filtered.map((th) => (
          <button
            key={th.id}
            type="button"
            className="w-full text-left px-3 py-1.5 text-sm hover:bg-accent/40"
            onClick={() => {
              onChange(th.id);
              setSearch("");
            }}
          >
            {th.nameJa || th.nameEn || th.code}
          </button>
        ))}
        {filtered.length === 0 && !canCreate && (
          <p className="px-3 py-2 text-xs text-muted-foreground">
            {t("geo.knowledge.search.uploadReportThemeNoMatch")}
          </p>
        )}
        {canCreate && (
          <button
            type="button"
            disabled={createThemeMutation.isPending}
            onClick={onCreate}
            className="w-full text-left px-3 py-1.5 text-sm text-primary hover:bg-accent/40 disabled:opacity-60"
          >
            {createThemeMutation.isPending
              ? t("geo.knowledge.search.uploadReportThemeCreating")
              : t("geo.knowledge.search.uploadReportThemeCreateNew", { name: trimmedSearch })}
          </button>
        )}
      </div>
    </div>
  );
}

function UploadReportForm({ onUploaded }: { onUploaded: () => void }) {
  const { t } = useLanguage();
  const uploadMutation = useUploadReport();
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [themeId, setThemeId] = useState("");
  const [reportKind, setReportKind] = useState<string>("internal_report");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [author, setAuthor] = useState("");
  const [ownerDepartment, setOwnerDepartment] = useState("");
  const [reportType, setReportType] = useState("");
  const [countryRegion, setCountryRegion] = useState("");
  const [topics, setTopics] = useState("");
  const [justUploaded, setJustUploaded] = useState(false);

  const canSubmit = !!file && !!themeId;

  const onSubmit = () => {
    if (!canSubmit) return;
    uploadMutation.mutate(
      {
        file: file as File,
        title,
        themeId,
        reportKind,
        author,
        ownerDepartment,
        reportType,
        countryRegion: countryRegion
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
        topics: topics
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
        // 2026-10-09修正: 従来actor_idを指定していなかったため、サーバー既定値の
        // 固定文字列"analyst"がcreated_byとして記録されていた。アップロードしたReportは
        // human_visibility=custom（confidentiality_label未指定時の既定）かつ
        // allowed_org_unitsが空のため、閲覧時はcreated_by本人のみ許可されるfail-closed
        // 設計になっている（geo_knowledge_access_policy.can_human_access）。固定文字列
        // "analyst"は実在のどのIdentityのidentity_idとも一致しないため、アップロード者
        // 本人を含め誰からも見えなくなっていた（「アップロードしたのに出てこない」症状の
        // 直接原因）。実際の認証済みIdentity（geo_ios_actor_id）を送ることで、
        // 少なくともアップロードした本人は自分のReportを見られるようにする。
        actorId: getIosActorId() ?? undefined,
      },
      {
        onSuccess: () => {
          setFile(null);
          setTitle("");
          setThemeId("");
          setReportKind("internal_report");
          setAuthor("");
          setOwnerDepartment("");
          setReportType("");
          setCountryRegion("");
          setTopics("");
          setJustUploaded(true);
          onUploaded();
        },
      },
    );
  };

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">{t("geo.knowledge.search.uploadReportHint")}</p>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">
            {t("geo.knowledge.search.uploadReportFile")}（{t("geo.knowledge.search.required")}）
          </label>
          <Input
            type="file"
            accept=".pdf,.docx,.html,.htm,.txt,.md"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              setJustUploaded(false);
            }}
          />
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">
            {t("geo.knowledge.search.uploadReportTitle")}
          </label>
          <Input value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div className="space-y-1 col-span-2">
          <label className="text-xs text-muted-foreground">
            {t("geo.knowledge.search.uploadReportTheme")}（{t("geo.knowledge.search.required")}）
          </label>
          <p className="text-[11px] text-muted-foreground">
            {t("geo.knowledge.search.uploadReportThemeHint")}
          </p>
          <ThemeCombobox value={themeId} onChange={setThemeId} />
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">
            {t("geo.knowledge.search.uploadReportKind")}
          </label>
          <Select value={reportKind} onValueChange={setReportKind}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {REPORT_KINDS.map((k) => (
                <SelectItem key={k} value={k}>
                  {t(`geo.knowledge.search.uploadReportKind.${k}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <button
        type="button"
        onClick={() => setShowAdvanced((v) => !v)}
        className="text-xs text-muted-foreground underline underline-offset-2"
      >
        {showAdvanced
          ? t("geo.knowledge.search.uploadReportHideAdvanced")
          : t("geo.knowledge.search.uploadReportShowAdvanced")}
      </button>

      {showAdvanced && (
        <div className="grid grid-cols-2 gap-3 pt-1 border-t border-border">
          <div className="space-y-1 pt-2">
            <label className="text-xs text-muted-foreground">
              {t("geo.knowledge.search.uploadReportAuthor")}
            </label>
            <Input value={author} onChange={(e) => setAuthor(e.target.value)} />
          </div>
          <div className="space-y-1 pt-2">
            <label className="text-xs text-muted-foreground">
              {t("geo.knowledge.search.uploadReportDepartment")}
            </label>
            <Input value={ownerDepartment} onChange={(e) => setOwnerDepartment(e.target.value)} />
          </div>
          <div className="space-y-1 pt-2">
            <label className="text-xs text-muted-foreground">
              {t("geo.knowledge.search.uploadReportType")}
            </label>
            <Input value={reportType} onChange={(e) => setReportType(e.target.value)} />
          </div>
          <div className="space-y-1 pt-2">
            <label className="text-xs text-muted-foreground">
              {t("geo.knowledge.search.uploadReportCountryRegion")}
            </label>
            <Input value={countryRegion} onChange={(e) => setCountryRegion(e.target.value)} />
          </div>
          <div className="space-y-1 pt-2 col-span-2">
            <label className="text-xs text-muted-foreground">
              {t("geo.knowledge.search.uploadReportTopics")}
            </label>
            <Input value={topics} onChange={(e) => setTopics(e.target.value)} />
          </div>
        </div>
      )}

      <div className="flex items-center gap-3">
        <Button size="sm" disabled={!canSubmit || uploadMutation.isPending} onClick={onSubmit}>
          {uploadMutation.isPending
            ? t("geo.knowledge.search.uploadReportUploading")
            : t("geo.knowledge.search.uploadReportSubmit")}
        </Button>
        {justUploaded && (
          <span className="text-xs text-muted-foreground">
            {t("geo.knowledge.search.uploadReportSuccess")}
          </span>
        )}
        {uploadMutation.isError && (
          <span className="text-xs text-destructive">
            {(uploadMutation.error as Error).message}
          </span>
        )}
      </div>
    </div>
  );
}

function GeoKnowledgeSearchPage() {
  const { t } = useLanguage();
  const [entityType, setEntityType] = useState("");
  const [knowledgeType, setKnowledgeType] = useState("");
  const [status, setStatus] = useState("active");
  const [includeDemo, setIncludeDemo] = useState(false);
  // 検索は「検索」ボタン（またはEnter）で実行する。入力のたびには検索しない（AIを呼ぶため）
  const [queryInput, setQueryInput] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false);
  const [detailItem, setDetailItem] = useState<{ item: GeoKnowledgeItem; reason?: string } | null>(null);
  const [detailEntity, setDetailEntity] = useState<{ entity: GeoEntity; reason?: string } | null>(null);

  const entitiesQuery = useGeoKnowledgeEntities({
    entityType: entityType || undefined,
    includeDemo,
  });
  const itemsQuery = useGeoKnowledgeItems({
    knowledgeType: knowledgeType || undefined,
    status: status || undefined,
    includeDemo,
  });
  const isSearching = submittedQuery.trim().length > 0;
  // 自然言語検索（理由つき）。使えなかった場合だけ、従来のHybrid検索（語の一致）へ切り替える
  const nl = useNaturalKnowledgeSearch(submittedQuery, {
    status,
    knowledgeType: knowledgeType || undefined,
    includeDemo,
  });
  const fallback = isSearching && nl.isError;
  const hybridKnowledgeQuery = useSearchKnowledgeItems(fallback ? submittedQuery : "", { status, includeDemo });
  const hybridEntityQuery = useSearchEntities(fallback ? submittedQuery : "", { includeDemo });
  const reportsQuery = useGeoReports();
  const hybridReportsQuery = useSearchReports(fallback ? submittedQuery : "", { includeDemo });

  const nameById = new Map<string, string>(
    (entitiesQuery.data?.entities ?? []).map((e) => [e.entity_id, e.name_ja || e.canonical_name]),
  );
  const submit = () => setSubmittedQuery(queryInput.trim());

  // 結果を、表示用の同じ形（対象＋合う理由）にそろえる
  type EntityRow = { entity: GeoEntity; reason?: string; score?: number };
  type ItemRow = { item: GeoKnowledgeItem; reason?: string; score?: number };
  const entityRows: EntityRow[] = isSearching
    ? fallback
      ? (hybridEntityQuery.data?.results ?? []).map((r) => ({ entity: r.entity as GeoEntity }))
      : (nl.data?.entities ?? []).map((r) => ({ entity: r.entity, reason: r.reason, score: r.score }))
    : (entitiesQuery.data?.entities ?? []).map((e) => ({ entity: e }));
  const itemRows: ItemRow[] = isSearching
    ? fallback
      ? (hybridKnowledgeQuery.data?.results ?? []).map((r) => ({ item: r.knowledge_item as unknown as GeoKnowledgeItem }))
      : (nl.data?.knowledge_items ?? []).map((r) => ({ item: r.knowledge_item, reason: r.reason, score: r.score }))
    : (itemsQuery.data?.items ?? []).map((i) => ({ item: i }));
  const reportRows = isSearching
    ? fallback
      ? (hybridReportsQuery.data?.reports ?? []).map((r) => ({ report: r, reason: undefined as string | undefined }))
      : (nl.data?.reports ?? []).map((r) => ({ report: r.report, reason: r.reason }))
    : (reportsQuery.data?.reports ?? []).map((r) => ({ report: r, reason: undefined as string | undefined }));
  const searchDone = isSearching && !nl.isLoading && (fallback || !!nl.data);

  return (
    <>
      <TopBar breadcrumb={[{ label: t("nav.geoKnowledgeSearch") }]} />
      <main className="flex-1 max-w-[1200px] w-full mx-auto px-8 py-8 space-y-6">
        <section className="border-b border-border pb-5 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-editorial text-4xl">{t("geo.knowledge.search.title")}</h1>
            <p className="text-sm text-muted-foreground mt-2">
              {t("geo.knowledge.search.subtitle")}
            </p>
          </div>
          <Button onClick={() => setUploadDialogOpen(true)}>
            {t("geo.knowledge.search.uploadReport")}
          </Button>
        </section>

        <Dialog open={uploadDialogOpen} onOpenChange={setUploadDialogOpen}>
          <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle>{t("geo.knowledge.search.uploadReport")}</DialogTitle>
            </DialogHeader>
            <UploadReportForm onUploaded={() => setUploadDialogOpen(false)} />
          </DialogContent>
        </Dialog>

        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <Input
            placeholder={t("geo.knowledge.nl.placeholder")}
            value={queryInput}
            onChange={(e) => setQueryInput(e.target.value)}
            className="flex-1"
            aria-label={t("geo.knowledge.nl.placeholder")}
          />
          <Button type="submit" disabled={!queryInput.trim() || nl.isFetching}>
            {nl.isFetching ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Search className="h-4 w-4 mr-1" />}
            {nl.isFetching ? t("geo.knowledge.nl.searching") : t("geo.knowledge.nl.button")}
          </Button>
          {(isSearching || queryInput) && (
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                setQueryInput("");
                setSubmittedQuery("");
              }}
            >
              {t("geo.knowledge.nl.clear")}
            </Button>
          )}
        </form>

        {isSearching && (
          <div className="text-xs text-muted-foreground space-y-0.5" data-testid="knowledge-search-note">
            {fallback ? (
              <p className="text-geo-amber">{t("geo.knowledge.nl.fallback")}</p>
            ) : (
              nl.data && (
                <>
                  {nl.data.understood && <p>{t("geo.knowledge.nl.understood", { text: nl.data.understood })}</p>}
                  <p>{t("geo.knowledge.nl.note")}</p>
                </>
              )
            )}
          </div>
        )}

        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={includeDemo}
            onChange={(e) => setIncludeDemo(e.target.checked)}
          />
          {t("geo.knowledge.search.includeDemo")}
        </label>

        <Tabs defaultValue="knowledge">
          <TabsList>
            <TabsTrigger value="entities">
              {t("geo.knowledge.search.entitiesTab")}
              {isSearching && searchDone ? `（${entityRows.length}）` : ""}
            </TabsTrigger>
            <TabsTrigger value="knowledge">
              {t("geo.knowledge.search.knowledgeTab")}
              {isSearching && searchDone ? `（${itemRows.length}）` : ""}
            </TabsTrigger>
            <TabsTrigger value="reports">
              {t("geo.knowledge.search.reportsTab")}
              {isSearching && searchDone ? `（${reportRows.length}）` : ""}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="entities" className="space-y-3 pt-4">
            {!isSearching && (
              <Input
                placeholder="entity_type (country, government, ...)"
                value={entityType}
                onChange={(e) => setEntityType(e.target.value)}
                className="max-w-xs"
              />
            )}
            {isSearching && nl.isFetching && <p className="text-sm text-muted-foreground">{t("geo.knowledge.nl.searching")}</p>}
            <div className="grid grid-cols-2 gap-3">
              {entityRows.map(({ entity, reason }) => (
                <button
                  key={entity.entity_id}
                  type="button"
                  onClick={() => setDetailEntity({ entity, reason })}
                  className="card-paper rounded-lg p-4 text-left hover:ring-1 hover:ring-primary/40 transition"
                  data-testid="entity-card"
                  title={t("geo.knowledge.nl.openDetail")}
                >
                  <div className="text-xs text-muted-foreground uppercase tracking-wider">{entity.entity_type}</div>
                  <div className="text-editorial text-lg mt-1">{entity.name_ja || entity.canonical_name}</div>
                  {entity.name_ja && <div className="text-xs text-muted-foreground">{entity.canonical_name}</div>}
                  {reason && (
                    <p className="text-xs mt-2">
                      <span className="font-medium text-primary">{t("geo.knowledge.nl.whyMatched")}: </span>
                      {reason}
                    </p>
                  )}
                  {entity.is_demo && (
                    <Badge variant="outline" className="mt-1">
                      demo
                    </Badge>
                  )}
                </button>
              ))}
            </div>
            {searchDone && entityRows.length === 0 && (
              <p className="text-sm text-muted-foreground">{t("geo.knowledge.nl.noResults")}</p>
            )}
          </TabsContent>

          <TabsContent value="knowledge" className="space-y-3 pt-4">
            <div className="flex gap-3">
              <Select value={status} onValueChange={setStatus}>
                <SelectTrigger className="w-48">
                  <SelectValue placeholder={t("geo.knowledge.search.statusFilter")} />
                </SelectTrigger>
                <SelectContent>
                  {STATUSES.map((s) => (
                    <SelectItem key={s} value={s}>
                      {s}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={knowledgeType} onValueChange={setKnowledgeType}>
                <SelectTrigger className="w-56">
                  <SelectValue placeholder={t("geo.knowledge.search.typeFilter")} />
                </SelectTrigger>
                <SelectContent>
                  {KNOWLEDGE_TYPES.map((k) => (
                    <SelectItem key={k} value={k}>
                      {k}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {isSearching && nl.isFetching && <p className="text-sm text-muted-foreground">{t("geo.knowledge.nl.searching")}</p>}
            <div className="space-y-2">
              {itemRows.map(({ item, reason }) => (
                <button
                  key={item.knowledge_item_id}
                  type="button"
                  onClick={() => setDetailItem({ item, reason })}
                  className="card-paper rounded-lg p-4 w-full text-left hover:ring-1 hover:ring-primary/40 transition"
                  data-testid="knowledge-item-card"
                  title={t("geo.knowledge.nl.openDetail")}
                >
                  <div className="flex items-center gap-2 flex-wrap">
                    <Badge variant={statusVariant(item.status)}>{item.status}</Badge>
                    <Badge variant="outline">{item.knowledge_type}</Badge>
                    {isAiApproved(item) && (
                      <Badge className="bg-geo-amber text-white hover:bg-geo-amber">{t("geo.knowledge.ai.badge")}</Badge>
                    )}
                    {item.information_class && <EpistemicTypeBadge informationClass={item.information_class} />}
                    {item.retrieval_status && <RetrievalStatusBadge retrievalStatus={item.retrieval_status} />}
                    {item.is_demo && <Badge variant="outline">demo</Badge>}
                  </div>
                  <div className="text-editorial text-lg mt-2">{item.title}</div>
                  {item.summary && <p className="text-sm text-muted-foreground mt-1">{item.summary}</p>}
                  {reason && (
                    <p className="text-xs mt-2">
                      <span className="font-medium text-primary">{t("geo.knowledge.nl.whyMatched")}: </span>
                      {reason}
                    </p>
                  )}
                </button>
              ))}
              {!isSearching && itemsQuery.data && itemsQuery.data.items.length === 0 && (
                <p className="text-sm text-muted-foreground">—</p>
              )}
              {searchDone && itemRows.length === 0 && (
                <p className="text-sm text-muted-foreground">{t("geo.knowledge.nl.noResults")}</p>
              )}
            </div>
          </TabsContent>

          <TabsContent value="reports" className="space-y-2 pt-4">
            {isSearching && nl.isFetching && <p className="text-sm text-muted-foreground">{t("geo.knowledge.nl.searching")}</p>}
            {reportRows.map(({ report, reason }) => (
              <div key={report.report_id} className="space-y-1">
                <ReportDetailCard report={report} />
                {reason && (
                  <p className="text-xs px-1">
                    <span className="font-medium text-primary">{t("geo.knowledge.nl.whyMatched")}: </span>
                    {reason}
                  </p>
                )}
              </div>
            ))}
            {!isSearching && reportsQuery.data && reportsQuery.data.reports.length === 0 && (
              <p className="text-sm text-muted-foreground">—</p>
            )}
            {searchDone && reportRows.length === 0 && (
              <p className="text-sm text-muted-foreground">{t("geo.knowledge.nl.noResults")}</p>
            )}
          </TabsContent>
        </Tabs>
      </main>

      <KnowledgeItemDetailSheet
        item={detailItem?.item ?? null}
        reason={detailItem?.reason}
        onClose={() => setDetailItem(null)}
      />
      <EntityDetailSheet
        entity={detailEntity?.entity ?? null}
        reason={detailEntity?.reason}
        nameById={nameById}
        onClose={() => setDetailEntity(null)}
      />
    </>
  );
}
