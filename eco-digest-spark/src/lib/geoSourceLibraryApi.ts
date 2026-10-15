// Source Library / Source Set / Source Research / Monitoring / Theme Explorer のAPIクライアント。
// Backend: geo_source_library_api.py / geo_monitoring_theme_api.py（2026-10-05）。
// Frontendは独自の判定ロジックを持たず、Freshness判定・注目度・Alert判定は全てBackendの結果を表示する。
import { API_BASE as API_BASE_URL } from "@/lib/apiBase";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getIosAuthHeaders } from "@/lib/geoIosAuthHeader";

const API_BASE = API_BASE_URL;

export async function fetchGeoJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...getIosAuthHeaders(),
      ...init?.headers,
    },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = typeof body.detail === "string" ? body.detail : undefined;
    throw new Error(detail || `API error ${res.status}`);
  }
  return res.json();
}

function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

const post = <T>(path: string, body?: unknown) =>
  fetchGeoJson<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
const patch = <T>(path: string, body: unknown) =>
  fetchGeoJson<T>(path, { method: "PATCH", body: JSON.stringify(body) });
const del = <T>(path: string) => fetchGeoJson<T>(path, { method: "DELETE" });

// ---------------------------------------------------------------------------
// 共通型
// ---------------------------------------------------------------------------

export type FreshnessStatus = "fresh" | "stale" | "unknown";
export interface DataFreshness {
  status: FreshnessStatus;
  last_data_at: string | null;
  days_since: number | null;
  stale_after_days: number;
  label: string;
}

export const MEDIA_TYPES = [
  "government",
  "international_org",
  "think_tank",
  "news_agency",
  "news",
  "academic",
  "company_ir",
  "data_provider",
  "social",
  "other",
] as const;
export type MediaType = (typeof MEDIA_TYPES)[number];
export const TAG_TYPES = ["theme", "region", "actor", "other"] as const;
export type TagType = (typeof TAG_TYPES)[number];

export interface GeoSourceTag {
  tag_id?: string;
  source_id?: string;
  tag_type: TagType;
  tag_value: string;
  origin: "ai" | "human" | "system";
  is_confirmed: boolean;
}

export interface GeoSource extends SourceMatchInfo {
  source_id: string;
  name: string;
  url: string | null;
  media_type: string | null;
  region: string | null;
  is_primary: boolean;
  description: string | null;
  reliability: "high" | "medium" | "low" | null;
  registered_by: string | null;
  archived_at: string | null;
  source_tier: string | null;
  source_type: string | null;
  notes: string | null;
  languages: string[] | null;
  update_cadence: string | null;
  thematic_scope: string | null;
  geographic_scope: string | null;
  source_owner: string | null;
  last_successful_ingestion_at: string | null;
  created_at: string;
  updated_at: string;
  // 一覧のみ
  tags?: GeoSourceTag[];
  article_count_30d?: number;
  article_count_total?: number;
  last_article_at?: string | null;
  freshness?: DataFreshness;
}

export interface SourceListFilters {
  q?: string;
  media_type?: string;
  region?: string;
  language?: string;
  is_primary?: boolean;
  theme_id?: string;
  tag?: string;
  include_archived?: boolean;
  created_after?: string;
  sort?: "name" | "created_at" | "updated_at" | "last_ingested";
  order?: "asc" | "desc";
  page?: number;
  page_size?: number;
}

/** 意味検索（特徴カードで探す）の結果に付く、一致の理由と注意 */
export interface SourceMatchInfo {
  match_score?: number;
  match_reason?: string;
  match_caution?: string;
}

export interface SourceListResponse {
  sources: GeoSource[];
  page: number;
  page_size: number;
  has_more: boolean;
  /** migration DB-1未適用の旧スキーマで取得した場合true（新機能は空になる） */
  legacy_mode: boolean;
  interpretation?: SearchInterpretation;
}

export interface SearchInterpretation {
  filters: {
    keywords: string[];
    media_type: string | null;
    region: string | null;
    language: string | null;
    is_primary: boolean | null;
    tags: string[];
  };
  used_llm: boolean;
  error: string | null;
  /** semantic=意味検索（特徴カードで探した）。無ければ従来のキーワード検索 */
  mode?: "semantic";
  /** AIが依頼をどう理解したか */
  understood?: string;
}

export interface SchemaStatus {
  phases: Record<string, boolean>;
  all_applied: boolean;
}

// ---------------------------------------------------------------------------
// Source Library hooks
// ---------------------------------------------------------------------------

export const sourceKeys = {
  all: ["geo-source-library"] as const,
  list: (f: object) => ["geo-source-library", "list", f] as const,
  search: (text: string, f: object) => ["geo-source-library", "search", text, f] as const,
  detail: (id: string) => ["geo-source-library", "detail", id] as const,
  meta: ["geo-source-library", "meta"] as const,
  schema: ["geo-source-library", "schema-status"] as const,
};

export function useSourceSchemaStatus() {
  return useQuery({
    // 4xx/5xx(migration未適用の503等)で3回リトライして「読み込み中」のまま待たせない
    retry: false,
    queryKey: sourceKeys.schema,
    queryFn: () => fetchGeoJson<SchemaStatus>("/api/geo/source-library/schema-status"),
    staleTime: 60_000,
  });
}

export function useSourceLibraryMeta() {
  return useQuery({
    // 4xx/5xx(migration未適用の503等)で3回リトライして「読み込み中」のまま待たせない
    retry: false,
    queryKey: sourceKeys.meta,
    queryFn: () =>
      fetchGeoJson<{
        media_types: string[];
        tag_types: string[];
        stale_after_days: number;
        data_freshness: DataFreshness;
      }>("/api/geo/source-library/meta"),
    staleTime: 60_000,
  });
}

/** Server-side filter + pagination。`nlText`があれば自然言語検索（解釈結果interpretation付き）。 */
export function useSourceList(filters: SourceListFilters, nlText?: string) {
  const text = (nlText ?? "").trim();
  return useQuery({
    // 4xx/5xx(migration未適用の503等)で3回リトライして「読み込み中」のまま待たせない
    retry: false,
    queryKey: text ? sourceKeys.search(text, filters) : sourceKeys.list(filters),
    queryFn: () =>
      text
        ? fetchGeoJson<SourceListResponse>(
            `/api/geo/source-library/search${qs({
              text,
              media_type: filters.media_type,
              region: filters.region,
              language: filters.language,
              is_primary: filters.is_primary,
              page: filters.page,
              page_size: filters.page_size,
            })}`,
          )
        : fetchGeoJson<SourceListResponse>(`/api/geo/source-library${qs({ ...filters })}`),
    placeholderData: keepPreviousData,
  });
}

export interface SourceThemeLink {
  link_id: string;
  source_id: string;
  theme_id: string;
  relation_type: "manual" | "ai_recommended" | "usage_derived";
  relevance_score: number | null;
  theme: { theme_id: string; name: string; code: string } | null;
}
export interface SourceDomain {
  domain_id: string;
  source_id: string;
  domain: string;
  is_primary: boolean;
  match_enabled: boolean;
}
export interface SourceArticle {
  external_signal_id: string;
  title: string | null;
  summary: string | null;
  url: string | null;
  source_name: string | null;
  source_domain: string | null;
  published_at: string | null;
  created_at: string;
}
export interface SourceDetail {
  source: GeoSource;
  tags: GeoSourceTag[];
  domains: SourceDomain[];
  theme_links: SourceThemeLink[];
  legacy_theme: { theme_id: string; name: string; code: string } | null;
  recent_articles: SourceArticle[];
  performance: {
    article_count_total: number;
    article_count_30d: number;
    last_article_at: string | null;
    source_set_count: number;
    monitoring_count: number;
    evidence_document_count: number;
  };
  freshness: DataFreshness;
}

export function useSourceDetail(sourceId: string) {
  return useQuery({
    // 4xx/5xx(migration未適用の503等)で3回リトライして「読み込み中」のまま待たせない
    retry: false,
    queryKey: sourceKeys.detail(sourceId),
    queryFn: () => fetchGeoJson<SourceDetail>(`/api/geo/source-library/${sourceId}`),
    enabled: !!sourceId,
  });
}

export type SourceInput = Partial<
  Pick<
    GeoSource,
    | "name"
    | "url"
    | "media_type"
    | "region"
    | "is_primary"
    | "description"
    | "reliability"
    | "source_tier"
    | "source_type"
    | "notes"
    | "languages"
    | "update_cadence"
    | "thematic_scope"
    | "geographic_scope"
    | "source_owner"
  >
> & { suggest_tags?: boolean };

export interface CreateSourceResponse {
  source: GeoSource;
  /** 同じdomainを持つ既存Source（=ambiguousの原因になる）。警告表示に使う */
  domain_conflicts: string[];
  tag_suggestion: { tags: { tag_type: TagType; tag_value: string }[]; error: string | null } | null;
}

function useInvalidateSources() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: sourceKeys.all });
}

export function useCreateSource() {
  const invalidate = useInvalidateSources();
  return useMutation({
    mutationFn: (body: SourceInput) => post<CreateSourceResponse>("/api/geo/source-library", body),
    onSuccess: invalidate,
  });
}
export function useUpdateSource(sourceId: string) {
  const invalidate = useInvalidateSources();
  return useMutation({
    mutationFn: (body: SourceInput) =>
      patch<{ source: GeoSource }>(`/api/geo/source-library/${sourceId}`, body),
    onSuccess: invalidate,
  });
}
/** soft delete（archived_at）。Article/Source Set/Monitoringの参照は維持される */
export function useArchiveSource() {
  const invalidate = useInvalidateSources();
  return useMutation({
    mutationFn: (sourceId: string) =>
      del<{ source: GeoSource; archived: boolean }>(`/api/geo/source-library/${sourceId}`),
    onSuccess: invalidate,
  });
}
export function useRestoreSource() {
  const invalidate = useInvalidateSources();
  return useMutation({
    mutationFn: (sourceId: string) =>
      post<{ source: GeoSource }>(`/api/geo/source-library/${sourceId}/restore`),
    onSuccess: invalidate,
  });
}

export function useSuggestTags(sourceId: string) {
  const invalidate = useInvalidateSources();
  return useMutation({
    mutationFn: () =>
      post<{ tags: { tag_type: TagType; tag_value: string }[]; error: string | null }>(
        `/api/geo/source-library/${sourceId}/suggest-tags?save=true`,
      ),
    onSuccess: invalidate,
  });
}
export function useAddTag(sourceId: string) {
  const invalidate = useInvalidateSources();
  return useMutation({
    mutationFn: (body: { tag_type: TagType; tag_value: string }) =>
      post<{ tag: GeoSourceTag }>(`/api/geo/source-library/${sourceId}/tags`, body),
    onSuccess: invalidate,
  });
}
export function useConfirmTag(sourceId: string) {
  const invalidate = useInvalidateSources();
  return useMutation({
    mutationFn: (v: { tagId: string; confirmed?: boolean }) =>
      post<{ ok: boolean }>(
        `/api/geo/source-library/${sourceId}/tags/${v.tagId}/confirm?confirmed=${v.confirmed ?? true}`,
      ),
    onSuccess: invalidate,
  });
}
export function useRemoveTag(sourceId: string) {
  const invalidate = useInvalidateSources();
  return useMutation({
    mutationFn: (tagId: string) => del<{ ok: boolean }>(`/api/geo/source-library/${sourceId}/tags/${tagId}`),
    onSuccess: invalidate,
  });
}
export function useAddDomain(sourceId: string) {
  const invalidate = useInvalidateSources();
  return useMutation({
    mutationFn: (body: { domain: string; is_primary?: boolean }) =>
      post<{ domain: SourceDomain; conflicts: string[] }>(
        `/api/geo/source-library/${sourceId}/domains`,
        body,
      ),
    onSuccess: invalidate,
  });
}
export function useToggleDomain(sourceId: string) {
  const invalidate = useInvalidateSources();
  return useMutation({
    mutationFn: (v: { domainId: string; matchEnabled: boolean }) =>
      patch<{ ok: boolean }>(
        `/api/geo/source-library/${sourceId}/domains/${v.domainId}?match_enabled=${v.matchEnabled}`,
        {},
      ),
    onSuccess: invalidate,
  });
}
export function useRemoveDomain(sourceId: string) {
  const invalidate = useInvalidateSources();
  return useMutation({
    mutationFn: (domainId: string) =>
      del<{ ok: boolean }>(`/api/geo/source-library/${sourceId}/domains/${domainId}`),
    onSuccess: invalidate,
  });
}

// ---------------------------------------------------------------------------
// Source Recommendation（Theme → おすすめSource、理由つき）
// ---------------------------------------------------------------------------

export interface SourceRecommendation {
  source_id: string;
  name: string;
  url: string | null;
  media_type: string | null;
  is_primary: boolean;
  score: number;
  /** 「なぜおすすめか」の短い説明 */
  reason: string;
  reasons: string[];
}
export interface RecommendResponse {
  recommendations: SourceRecommendation[];
  unregistered_domains: { domain: string; article_count: number }[];
  basis: { terms: string[]; theme_article_count?: number; note?: string };
}
export interface RecommendInput {
  candidate_id?: string;
  title?: string;
  actors?: string[];
  regions?: string[];
  themes?: string[];
  keywords?: string[];
  theme_id?: string;
  limit?: number;
}
export function useRecommendSources(input: RecommendInput, enabled = true) {
  return useQuery({
    // 4xx/5xx(migration未適用の503等)で3回リトライして「読み込み中」のまま待たせない
    retry: false,
    queryKey: ["geo-source-library", "recommend", input],
    queryFn: () => post<RecommendResponse>("/api/geo/source-library/recommend", input),
    enabled,
    staleTime: 5 * 60_000,
  });
}

// ---------------------------------------------------------------------------
// Source Set（Theme Explorer / Monitoring / Research Studioで共有）
// ---------------------------------------------------------------------------

export interface SourceSetMember {
  source_id: string;
  name: string;
  url: string | null;
  media_type: string | null;
  region: string | null;
  is_primary: boolean;
  source_tier: string | null;
  archived_at: string | null;
  sort_order: number;
}
export interface GeoSourceSet {
  source_set_id: string;
  name: string;
  description: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  member_count: number;
  sources?: SourceSetMember[];
}

export const sourceSetKeys = {
  all: ["geo-source-sets"] as const,
  detail: (id: string) => ["geo-source-sets", id] as const,
};

export function useSourceSets() {
  return useQuery({
    // 4xx/5xx(migration未適用の503等)で3回リトライして「読み込み中」のまま待たせない
    retry: false,
    queryKey: sourceSetKeys.all,
    queryFn: () => fetchGeoJson<{ source_sets: GeoSourceSet[] }>("/api/geo/source-sets"),
  });
}
export function useSourceSet(sourceSetId: string | null | undefined) {
  return useQuery({
    // 4xx/5xx(migration未適用の503等)で3回リトライして「読み込み中」のまま待たせない
    retry: false,
    queryKey: sourceSetKeys.detail(sourceSetId ?? ""),
    queryFn: () => fetchGeoJson<{ source_set: GeoSourceSet }>(`/api/geo/source-sets/${sourceSetId}`),
    enabled: !!sourceSetId,
  });
}
export function useCreateSourceSet() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; description?: string; source_ids?: string[] }) =>
      post<{ source_set: GeoSourceSet }>("/api/geo/source-sets", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: sourceSetKeys.all }),
  });
}
export function useUpdateSourceSet(sourceSetId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name?: string; description?: string; source_ids?: string[] }) =>
      patch<{ source_set: GeoSourceSet }>(`/api/geo/source-sets/${sourceSetId}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: sourceSetKeys.all }),
  });
}

// ---------------------------------------------------------------------------
// Source Research（既存Research Studio Projectへ調査コンテキストを渡す）
// ---------------------------------------------------------------------------

export interface ResearchContext {
  theme: string;
  query: string | null;
  source_ids: string[];
  source_names: string[];
  source_set_id: string | null;
  origin: "theme_explorer" | "source_library" | "monitoring";
}
export interface ResearchReference {
  index: number;
  article_title: string | null;
  source: string | null;
  published_at: string | null;
  original_url: string;
  origin: "internal" | "web";
  snippet?: string | null;
  /** matched=質問に直接関連 / supplementary=直近記事での補完（関連性は保証されない） */
  match_type: "matched" | "supplementary";
  /** どの出自の検索語に一致したか（original=原文 / translated=英訳 / expanded=展開語） */
  matched_terms?: { term: string; kind: "original" | "translated" | "expanded" }[];
}
export interface QueryExpansion {
  version?: string;
  original: string;
  translated: string | null;
  key_concepts: string[];
  expanded_terms: string[];
  search_terms: { term: string; kind: "original" | "translated" | "expanded" }[];
  used_llm: boolean;
  guard: { dropped: { term: string; reason: string }[] };
}
export interface ResearchResult {
  theme: string;
  query: string;
  answer: { text: string; used_references: number[] } | null;
  references: ResearchReference[];
  /** 検索語の展開結果（問い自体は変更されない）。無い場合は旧API */
  query_expansion?: QueryExpansion;
  sources: { source_id: string; name: string; url: string | null; archived: boolean }[];
  searched_domains: string[];
  notes: string[];
  errors: string[];
}

/** Theme / Query / source_ids を引き継いだResearch Studio Projectを作成する */
export function useCreateResearchProject() {
  return useMutation({
    mutationFn: (body: {
      theme: string;
      query?: string;
      source_ids?: string[];
      source_set_id?: string | null;
      origin: ResearchContext["origin"];
    }) =>
      post<{ project: { project_id: string; project_title: string; research_context: ResearchContext } }>(
        "/api/geo/source-research/projects",
        body,
      ),
  });
}
export function useResearchContext(projectId: string) {
  return useQuery({
    // 4xx/5xx(migration未適用の503等)で3回リトライして「読み込み中」のまま待たせない
    retry: false,
    queryKey: ["geo-source-research", "context", projectId],
    queryFn: () =>
      fetchGeoJson<{ project_id: string; research_context: ResearchContext | null }>(
        `/api/geo/source-research/projects/${projectId}/context`,
      ),
    enabled: !!projectId,
  });
}
export function useRunSourceResearch(projectId: string) {
  return useMutation({
    mutationFn: (body: { query?: string; source_ids?: string[]; use_web?: boolean }) =>
      post<ResearchResult>(`/api/geo/source-research/projects/${projectId}/run`, body),
  });
}

// ---------------------------------------------------------------------------
// Theme Explorer（既存geo_theme_candidatesの閲覧・絞り込み。「注目度」は暫定・説明可能）
// ---------------------------------------------------------------------------

export type ThemeBucket = "notable" | "new" | "ongoing" | "past";
export interface AttentionBreakdown {
  label: string;
  points: number;
  max: number;
  note: string;
}
export interface ExplorerTheme {
  candidate_id: string;
  title: string;
  question: string | null;
  signal_summary: string | null;
  observed_change: string | null;
  why_now: string | null;
  actors: string[];
  regions: string[];
  themes: string[];
  business_relevance: string[];
  candidate_type: string;
  change_type: string;
  status: "NEW" | "SHORTLISTED" | "DISMISSED" | "MONITORING" | "HANDED_OFF";
  signal_count: number;
  related_article_count: number;
  first_seen_at: string | null;
  latest_signal_at: string | null;
  updated_at: string;
  attention: { score: number; level: "high" | "medium" | "low"; breakdown: AttentionBreakdown[] };
  buckets: ThemeBucket[];
  /** V1_1: 候補の由来。article_discovery=記事から発見 / situation=Situationから発見 / monitoring / manual */
  candidate_origin: "article_discovery" | "web_discovery" | "situation" | "monitoring" | "manual";
  candidate_origin_label: string;
  /** AIが見つけた候補は人間が判断するまで pending（提案）。situation由来は n/a */
  proposal_state: "pending" | "decided" | "n/a";
  /** V1_1: 根拠記事（Article title / Source / Published date / Original URL） */
  evidence_articles: EvidenceArticle[];
  evidence_total: number;
}
export interface EvidenceArticle {
  external_signal_id: string;
  title: string | null;
  url: string | null;
  source: string | null;
  published_at: string | null;
  /** false=公開日が分からない（Web検索の引用など）。画面では「日付不明」と表示する */
  date_known?: boolean;
}
export interface ExplorerFilters {
  q?: string;
  region?: string;
  category?: string;
  level?: string;
  bucket?: ThemeBucket;
  new_only?: boolean;
  period_days?: number;
  status?: string;
  /** V1_1: キーワード絞り込み（AIを使わない単純な文字列。空白区切りは全語を含む） */
  keyword?: string;
  /** V1_1: 並び替え。newest=新しい順(既定) / oldest=古い順 */
  sort?: "newest" | "oldest" | "attention";
  page?: number;
  page_size?: number;
}
export interface ExplorerResponse {
  themes: ExplorerTheme[];
  total: number;
  /** V1_1: 削除済みを除く候補の総数（絞り込み前）。0なら候補自体がまだ無い */
  available_total: number;
  page: number;
  page_size: number;
  bucket_counts: Record<ThemeBucket, number>;
  bucket_labels: Record<ThemeBucket, string>;
  facets: { regions: string[]; categories: string[] };
  interpretation: {
    filters: { keywords: string[]; region: string | null; new_only: boolean; period_days: number };
    used_llm: boolean;
    error: string | null;
  } | null;
  data_freshness: DataFreshness;
}
export function useThemeExplorer(filters: ExplorerFilters) {
  return useQuery({
    // 4xx/5xx(migration未適用の503等)で3回リトライして「読み込み中」のまま待たせない
    retry: false,
    queryKey: ["geo-theme-explorer", filters],
    queryFn: () => fetchGeoJson<ExplorerResponse>(`/api/geo/theme-explorer${qs({ ...filters })}`),
    placeholderData: keepPreviousData,
  });
}

// ---------------------------------------------------------------------------
// Monitoring
// ---------------------------------------------------------------------------

/** no_fresh_data = データ更新なし（状況判定不能）。Stableと明確に区別する */
export type MonitoringState =
  | "no_fresh_data"
  | "partial_coverage"
  | "no_new_articles"
  | "stable"
  | "watch"
  | "changed";

/** Theme Freshness（Theme Stateとは別）。Coverage-aware: 一部の情報源だけ最新なら partial_freshness */
export type ThemeFreshnessState = "fresh" | "partial_freshness" | "no_fresh_data" | "unknown";
export type CoverageStatus = "fresh" | "partial" | "stale" | "unknown" | "not_configured";
export interface CoverageSource {
  source_id: string;
  name: string | null;
  role: "primary" | "auxiliary";
  status: "fresh" | "stale" | "unknown";
  reason: string;
  latest_published_at: string | null;
  last_collected_at: string | null;
}
export interface CoverageComponent {
  key: "primary" | "specialist" | "broad_media";
  label: string;
  status: CoverageStatus;
  sources: CoverageSource[];
  auxiliary_sources?: CoverageSource[];
  last_data_at?: string | null;
}
export interface ThemeCoverage {
  freshness_state: ThemeFreshnessState;
  freshness_label: string;
  components: CoverageComponent[];
}
export interface MonitoringAssessment {
  state: MonitoringState;
  state_label: string;
  summary: string;
  latest_change: string | null;
  new_articles_24h: number;
  articles_in_window: number;
  latest_article_at: string | null;
  freshness: DataFreshness;
  evaluated_at: string;
  source_count: number;
  unlinked_note: string | null;
  /** Coverage-aware Theme Freshness（決定論）。旧い評価では未設定 */
  freshness_state?: ThemeFreshnessState;
  freshness_label?: string;
  coverage?: ThemeCoverage;
  /** 「変化なし」と誤読させないための注記 */
  coverage_note?: string | null;
  /** Middle East Maritime系テーマの関連記事の海域別件数（hormuz / gulf_of_oman / persian_gulf / red_sea / wider_middle_east / unclassified） */
  region_breakdown?: Record<string, number> | null;
  /** Alert Context Validationの監査情報（候補が採用/除外された理由） */
  validation?: {
    version?: string;
    candidates: number;
    valid: number;
    excluded: { title: string; rule: string; label: string; reason: string }[];
  };
}
export interface MonitoringTheme {
  monitoring_theme_id: string;
  display_name: string;
  theme_candidate_id: string | null;
  theme_id: string | null;
  source_set_id: string | null;
  regions: string[];
  actors: string[];
  tags: string[];
  keywords: string[];
  monitoring_criteria: string | null;
  sensitivity: "low" | "medium" | "high";
  frequency: "hourly" | "daily" | "weekly";
  status: "active" | "paused" | "archived";
  latest_assessment: MonitoringAssessment | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  last_checked_at: string | null;
  // 一覧のみ
  unacknowledged_alerts?: number;
  assessment_state?: MonitoringState | null;
  freshness_state?: ThemeFreshnessState | null;
  coverage_note?: string | null;
}
export interface MonitoringAlert {
  alert_id: string;
  monitoring_theme_id: string;
  severity: "low" | "medium" | "high";
  alert_type: string;
  title: string;
  /** なぜAlertになったか（必須） */
  reason: string;
  related_signal_ids: string[];
  detected_at: string;
  acknowledged_at: string | null;
  /** Candidate Detectionのrule / Context Validationの結果と根拠（DB-8適用後に保存される） */
  candidate_rule?: string | null;
  validation_result?: string | null;
  validation_reason?: string | null;
}
export interface MonitoringListResponse {
  themes: MonitoringTheme[];
  summary: {
    monitoring_count: number;
    unacknowledged_alerts: number;
    new_articles_today: number;
    important_changes: number;
  };
  data_freshness: DataFreshness;
}
export interface MonitoringReference {
  article_title: string | null;
  source: string | null;
  published_at: string | null;
  url: string | null;
  external_signal_id: string;
  matched_via: string | null;
}
export interface MonitoringDetail {
  /** Source Setの情報。archived_atがあれば「アーカイブ済みだが監視は継続中」 */
  source_set?: { source_set_id: string; name: string; archived_at: string | null } | null;
  monitoring_theme: MonitoringTheme;
  assessment: MonitoringAssessment;
  alerts: MonitoringAlert[];
  timeline: {
    at: string;
    kind: "alert" | "article";
    title: string | null;
    severity?: string;
    url?: string | null;
    source?: string | null;
  }[];
  articles: MonitoringReference[];
  key_actors: { name: string; article_count: number }[];
  sources: SourceSetMember[];
  watchpoints: {
    keywords: string[];
    actors: string[];
    criteria: string | null;
    sensitivity: string | null;
    frequency: string | null;
  };
}
export type MonitoringInput = Partial<
  Pick<
    MonitoringTheme,
    | "display_name"
    | "source_set_id"
    | "regions"
    | "actors"
    | "tags"
    | "keywords"
    | "monitoring_criteria"
    | "sensitivity"
    | "frequency"
    | "status"
  >
>;

export const monitoringKeys = {
  all: ["geo-monitoring-themes"] as const,
  detail: (id: string) => ["geo-monitoring-themes", "detail", id] as const,
};

export function useMonitoringThemes() {
  return useQuery({
    // 4xx/5xx(migration未適用の503等)で3回リトライして「読み込み中」のまま待たせない
    retry: false,
    queryKey: [...monitoringKeys.all, "list"],
    queryFn: () => fetchGeoJson<MonitoringListResponse>("/api/geo/monitoring-themes"),
  });
}
export function useMonitoringDetail(id: string) {
  return useQuery({
    // 4xx/5xx(migration未適用の503等)で3回リトライして「読み込み中」のまま待たせない
    retry: false,
    queryKey: monitoringKeys.detail(id),
    queryFn: () => fetchGeoJson<MonitoringDetail>(`/api/geo/monitoring-themes/${id}`),
    enabled: !!id,
  });
}
function useInvalidateMonitoring() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: monitoringKeys.all });
}
/** Theme Explorerの候補 → Monitoring。Theme + Source Set + Actors + Tags を引き継ぐ */
export function useCreateMonitoringFromCandidate() {
  const invalidate = useInvalidateMonitoring();
  return useMutation({
    mutationFn: (body: {
      candidate_id: string;
      source_ids?: string[];
      source_set_id?: string | null;
      keywords?: string[];
      sensitivity?: "low" | "medium" | "high";
      frequency?: "hourly" | "daily" | "weekly";
    }) =>
      post<{
        monitoring_theme: MonitoringTheme;
        already_monitoring: boolean;
        check: MonitoringAssessment | { state: null; error: string } | null;
      }>("/api/geo/monitoring-themes/from-candidate", body),
    onSuccess: invalidate,
  });
}
export function useUpdateMonitoringTheme(id: string) {
  const invalidate = useInvalidateMonitoring();
  return useMutation({
    mutationFn: (body: MonitoringInput) =>
      patch<{ monitoring_theme: MonitoringTheme }>(`/api/geo/monitoring-themes/${id}`, body),
    onSuccess: invalidate,
  });
}
export function useArchiveMonitoringTheme() {
  const invalidate = useInvalidateMonitoring();
  return useMutation({
    mutationFn: (id: string) => del<{ archived: boolean }>(`/api/geo/monitoring-themes/${id}`),
    onSuccess: invalidate,
  });
}
export function useCheckAllMonitoring() {
  const invalidate = useInvalidateMonitoring();
  return useMutation({
    mutationFn: () => post<{ checked: number }>("/api/geo/monitoring-themes/check-all"),
    onSuccess: invalidate,
  });
}
/** 再評価（Freshness Guard → 記事収集 → Alert判定）して最新状態を返す */
export function useRefreshMonitoringDetail(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => fetchGeoJson<MonitoringDetail>(`/api/geo/monitoring-themes/${id}?refresh=true`),
    onSuccess: (data) => {
      qc.setQueryData(monitoringKeys.detail(id), data);
      qc.invalidateQueries({ queryKey: [...monitoringKeys.all, "list"] });
    },
  });
}
export function useAcknowledgeAlert() {
  const invalidate = useInvalidateMonitoring();
  return useMutation({
    mutationFn: (alertId: string) =>
      post<{ ok: boolean }>(`/api/geo/monitoring-themes/alerts/${alertId}/acknowledge`),
    onSuccess: invalidate,
  });
}


// ---------------------------------------------------------------------------
// V1_1: テーマ探索開始（直近の記事からTheme Candidateを提案する。実処理の段階に連動する進行状況つき）
// ---------------------------------------------------------------------------

export interface DiscoveryStep {
  key: "interpret" | "search" | "cluster";
  label: string;
  state: "pending" | "running" | "done";
}
export interface DiscoveryExistingUpdate {
  kind: "existing_update";
  existing: { type: "candidate" | "monitoring" | "theme"; id: string; title: string | null; status?: string | null };
  proposed_title: string;
  summary: string;
  new_articles: EvidenceArticle[];
  has_new_articles: boolean;
}
export interface DiscoveryResult {
  version: string;
  prompt: string;
  period_days: number;
  source_scope: { mode: "all" | "selected"; source_count: number };
  stats: {
    articles_considered: number;
    articles_relevant: number;
    articles_used: number;
    themes_proposed?: number;
    themes_created?: number;
    existing_updates?: number;
    skipped_deleted?: number;
  };
  candidates: ExplorerTheme[];
  existing_updates: DiscoveryExistingUpdate[];
  /** 候補が無い時の表示文（例: 該当する新しいテーマ候補は見つかりませんでした） */
  message: string | null;
  reason?: string;
  from_cache: boolean;
  /** Web検索を含めた探索の結果（Web検索の件数・注記）。従来の記事だけの探索には無い */
  /** 専門家の視点: 知識DBの裏付けの有無と、使った切り口 */
  expert?: { has_support: boolean; knowledge: { title: string | null; type: string }[]; issues: string[]; lenses: string[] };
  searched_topics?: string[];
  web?: { fetched: number; inserted: number; updated: number; open_web?: number; selected_sources?: number; errors: string[] };
}
export interface DiscoveryJob {
  job_id: string;
  status: "running" | "done" | "error";
  steps: DiscoveryStep[];
  result: DiscoveryResult | null;
  error: string | null;
}

export function useStartDiscovery() {
  return useMutation({
    mutationFn: (body: { prompt: string; source_ids?: string[]; source_set_id?: string | null; period_days?: number }) =>
      post<{ job_id: string }>("/api/geo/theme-explorer/discover", body),
  });
}

/** 探索ジョブの進行状況。running の間だけ1秒ごとに取得する */
export function useDiscoveryJob(jobId: string | null) {
  return useQuery({
    queryKey: ["geo-theme-discovery-job", jobId],
    queryFn: () => fetchGeoJson<DiscoveryJob>(`/api/geo/theme-explorer/discover/${jobId}`),
    enabled: !!jobId,
    retry: false,
    staleTime: 0,
    refetchInterval: (q) => (!q.state.data || q.state.data.status === "running" ? 1000 : false),
  });
}
