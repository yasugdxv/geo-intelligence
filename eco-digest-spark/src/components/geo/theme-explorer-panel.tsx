import { Link } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, Circle, Loader2, Search, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { DataFreshnessBadge } from "@/components/geo/data-freshness";
import { SourceSelector } from "@/components/geo/source-selector";
import { ThemeDiscoveryResults } from "@/components/geo/theme-discovery-results";
import { ThemeExplorerCard } from "@/components/geo/theme-explorer-card";
import type {
  ThemeActionTarget,
  ThemeSourceOverride,
} from "@/components/geo/theme-explorer-actions";
import { useLanguage } from "@/lib/i18n";
import {
  useDiscoveryJob,
  useThemeExplorer,
  type ExplorerFilters,
  type ExplorerTheme,
} from "@/lib/geoSourceLibraryApi";
import {
  useDiscoveryChat,
  useStartLiveDiscovery,
  type DiscoveryBrief,
  type DiscoveryChatTurn,
} from "@/lib/geoThemeDiscoveryLiveApi";
import {
  useThemeCandidatePipelineStatus,
  useBatchSyncPendingThemeCandidates,
} from "@/lib/geoThemeCandidateApi";
import {
  OTHER_CATEGORY,
  QUICK_KEYWORDS,
  THEME_CATEGORIES,
  categorizeTheme,
  matchesKeyword,
  type ThemeCategoryKey,
} from "@/lib/themeCategories";

// テーマ探索（Explorer）本体。
//   テーマ探索開始 = 入力文を解釈 → 直近の記事を検索 → 関連記事を束ねてTheme Candidateを提案（V1_1 Article Discovery）。
//   従来のSituation由来の候補も、下の「登録済みのテーマ候補」に引き続き表示する。
//   一覧は 検索・カテゴリ・注目度・新規のみ の絞り込みと並べ替え（新しい順／注目度／関連記事数）、削除ができる。
//   絞り込みは取得済みの候補（最大100件）に対して画面側で行う。
// Detail Sheet / 選択モード復帰は親ページが持つ。

const PAGE_SIZE = 12; // 画面1ページの表示件数
const FETCH_SIZE = 100; // API1回の取得件数（APIの上限）

// Phase C §14: Theme Candidateが0件の場合、「データが無い」のか「Episode Reviewで
// 止まっている」のかを区別して示す。承認済みEpisode Clusterや同期待ちが無ければ何も表示しない
// （Article Discovery起点の空画面案内と重複させない）。
function EpisodePipelineEmptyStateHint() {
  const statusQuery = useThemeCandidatePipelineStatus();
  const batchMutation = useBatchSyncPendingThemeCandidates();
  const status = statusQuery.data;

  if (
    !status ||
    (status.review_pending === 0 && status.accepted === 0 && status.pending_theme_sync === 0)
  )
    return null;

  return (
    <div className="mt-4 pt-4 border-t border-border/60 text-left space-y-1.5">
      <p className="text-xs text-muted-foreground" data-testid="theme-empty-reason">
        テーマ候補がまだ無いのは、候補を作る前の確認が終わっていないためです:
      </p>
      <ul className="text-xs text-muted-foreground space-y-0.5">
        <li>
          レビュー待ち:{" "}
          <span className="tabular-nums font-medium text-foreground">{status.review_pending}</span>件
        </li>
        <li>
          承認済みEpisode Cluster:{" "}
          <span className="tabular-nums font-medium text-foreground">{status.accepted}</span>件
        </li>
        <li>
          Theme同期待ち:{" "}
          <span className="tabular-nums font-medium text-foreground">
            {status.pending_theme_sync}
          </span>
          件
        </li>
      </ul>
      <div className="flex flex-wrap gap-2">
        {status.review_pending > 0 && (
          <Button asChild size="sm" variant="outline">
            <Link to="/geo/episode-candidates">レビューする</Link>
          </Button>
        )}
        <Button asChild size="sm" variant="ghost">
          <Link to="/geo/operations">運用状況を見る</Link>
        </Button>
      </div>
      {status.pending_theme_sync > 0 && (
        <>
          <Button
            size="sm"
            disabled={batchMutation.isPending}
            onClick={() => batchMutation.mutate({})}
          >
            {batchMutation.isPending ? "生成中..." : "Theme Candidateを生成"}
          </Button>
          {batchMutation.data && (
            <p className="text-xs text-muted-foreground">
              生成成功 {batchMutation.data.counts.inserted_count}件 / 候補なし{" "}
              {batchMutation.data.counts.skipped_count}件 / 失敗{" "}
              {batchMutation.data.counts.error_count}件
            </p>
          )}
        </>
      )}
    </div>
  );
}

const LEVEL_ORDER = { high: 3, medium: 2, low: 1 } as const;
type SortMode = "newest" | "attention" | "related";
type LevelFilter = "all" | "high" | "medium" | "low";

export function ThemeExplorerPanel({
  overrides,
  onOpenDetail,
  onChangeSources,
  hasPendingPick = false,
  onUsePending,
}: {
  overrides: Map<string, ThemeSourceOverride>;
  onOpenDetail: (candidateId: string) => void;
  onChangeSources?: (target: ThemeActionTarget, currentIds: string[]) => void;
  hasPendingPick?: boolean;
  onUsePending?: (candidateId: string) => void;
}) {
  const { t } = useLanguage();
  const queryClient = useQueryClient();
  const [keywordInput, setKeywordInput] = useState("");
  const [keyword, setKeyword] = useState(""); // 確定した検索語（クライアント側で絞り込む）
  const [category, setCategory] = useState<ThemeCategoryKey | "all">("all");
  const [level, setLevel] = useState<LevelFilter>("all");
  const [newOnly, setNewOnly] = useState(false);
  const [sortMode, setSortMode] = useState<SortMode>("newest");
  const [page, setPage] = useState(1);
  const [jobId, setJobId] = useState<string | null>(null);
  // 2026-10-07: 「テーマ探索」を本当の探索にする——チャットで要件をすり合わせ(chatHistory/brief)、
  // おすすめソースから選び(briefSourceIds)、必要ならWeb検索(includeWeb)もしてから実行する。
  const [chatHistory, setChatHistory] = useState<DiscoveryChatTurn[]>([]);
  const [chatMessage, setChatMessage] = useState("");
  const [brief, setBrief] = useState<DiscoveryBrief | null>(null);
  const [briefSourceIds, setBriefSourceIds] = useState<string[]>([]);
  const [briefSourceNames, setBriefSourceNames] = useState<string[]>([]);
  const [includeWeb, setIncludeWeb] = useState(true); // 既定はWeb検索あり（ソース外も探す）
  const [sourcePickerOpen, setSourcePickerOpen] = useState(false);
  // 一覧は新しい順で最大100件（APIの上限）を取得し、検索・カテゴリ・注目度の絞り込みは画面側で行う
  const filters: ExplorerFilters = { sort: "newest", page: 1, page_size: FETCH_SIZE };

  const { data, isLoading, isError, error } = useThemeExplorer(filters);
  const chat = useDiscoveryChat();
  const startLive = useStartLiveDiscovery();
  const job = useDiscoveryJob(jobId);
  const running = startLive.isPending || job.data?.status === "running";
  const result = job.data?.status === "done" ? job.data.result : null;

  // 探索が終わったら、保存された候補を一覧（と詳細Sheet用の候補一覧）へ反映する
  const jobStatus = job.data?.status;
  useEffect(() => {
    if (jobStatus === "done") {
      queryClient.invalidateQueries({ queryKey: ["geo-theme-explorer"] });
      queryClient.invalidateQueries({ queryKey: ["geoThemeCandidates"] });
    }
  }, [jobStatus, jobId, queryClient]);

  // 入力から少し待って検索語を反映する（1文字ごとに再計算しない）
  useEffect(() => {
    const id = setTimeout(() => {
      setKeyword(keywordInput.trim());
      setPage(1);
    }, 250);
    return () => clearTimeout(id);
  }, [keywordInput]);

  const sendChat = () => {
    const message = chatMessage.trim();
    if (!message) return;
    chat.mutate(
      { history: chatHistory, message },
      {
        onSuccess: (r) => {
          setChatHistory((h) => [...h, { role: "user", content: message }, { role: "assistant", content: r.assistant_reply }]);
          setBrief(r.brief);
          setChatMessage("");
        },
      },
    );
  };

  const applySources = (ids: string[], sources: { source_id: string; name: string }[]) => {
    setBriefSourceIds(ids);
    setBriefSourceNames(sources.map((s) => s.name));
  };

  const startLiveExplore = () => {
    if (!brief) return;
    setJobId(null);
    startLive.reset();
    startLive.mutate(
      { brief, source_ids: briefSourceIds, include_web: includeWeb },
      { onSuccess: (r) => setJobId(r.job_id) },
    );
  };

  const recommendInputForBrief = brief
    ? {
        title: brief.summary,
        themes: brief.keywords,
        keywords: brief.keywords,
        regions: brief.region ? [brief.region] : [],
      }
    : undefined;

  // 今回の探索で表示した候補は、下の一覧に重複して出さない
  const shownIds = useMemo(
    () => new Set((result?.candidates ?? []).map((c) => c.candidate_id)),
    [result],
  );
  const baseThemes = useMemo(
    () => (data?.themes ?? []).filter((th) => !shownIds.has(th.candidate_id)),
    [data, shownIds],
  );

  // 検索語・注目度・新規のみ で絞った集合（カテゴリ件数はこの集合で数える）
  const preFiltered = useMemo(
    () =>
      baseThemes.filter(
        (th) =>
          matchesKeyword(th, keyword) &&
          (level === "all" || th.attention.level === level) &&
          (!newOnly || th.status === "NEW"),
      ),
    [baseThemes, keyword, level, newOnly],
  );
  const categoryOf = useMemo(() => {
    const m = new Map<string, ThemeCategoryKey[]>();
    for (const th of preFiltered) m.set(th.candidate_id, categorizeTheme(th).map((c) => c.key));
    return m;
  }, [preFiltered]);
  const categoryCounts = useMemo(() => {
    const counts = new Map<ThemeCategoryKey, number>();
    for (const keys of categoryOf.values()) for (const k of keys) counts.set(k, (counts.get(k) ?? 0) + 1);
    return counts;
  }, [categoryOf]);
  const listThemes = useMemo(() => {
    const rows = preFiltered.filter((th) => category === "all" || categoryOf.get(th.candidate_id)?.includes(category));
    const byNewest = (a: ExplorerTheme, b: ExplorerTheme) =>
      (b.latest_signal_at ?? b.updated_at).localeCompare(a.latest_signal_at ?? a.updated_at);
    if (sortMode === "attention")
      rows.sort(
        (a, b) =>
          LEVEL_ORDER[b.attention.level] - LEVEL_ORDER[a.attention.level] ||
          b.attention.score - a.attention.score ||
          byNewest(a, b),
      );
    else if (sortMode === "related")
      rows.sort((a, b) => b.related_article_count - a.related_article_count || byNewest(a, b));
    else rows.sort(byNewest);
    return rows;
  }, [preFiltered, category, categoryOf, sortMode]);

  const availableTotal = baseThemes.length;
  const total = listThemes.length;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const curPage = Math.min(page, pages);
  const pageRows = listThemes.slice((curPage - 1) * PAGE_SIZE, curPage * PAGE_SIZE);
  const maxRelated = Math.max(1, ...listThemes.map((th) => th.related_article_count));
  const narrowed = !!keyword || level !== "all" || newOnly || category !== "all";
  const truncated = (data?.available_total ?? 0) > (data?.themes.length ?? 0);
  const errorMessage = startLive.isError ? (startLive.error as Error).message : job.data?.error;
  const clearFilters = () => {
    setKeywordInput("");
    setKeyword("");
    setCategory("all");
    setLevel("all");
    setNewOnly(false);
    setPage(1);
  };
  const pickQuick = (w: string) => {
    setKeywordInput(w);
    setKeyword(w);
    setPage(1);
  };

  return (
    <div className="space-y-5">
      {/* AIによる新規テーマ探索: メイン機能。チャットで要件をすり合わせ→おすすめソースを選ぶ→必要ならWeb検索も含めて実行する */}
      <section className="card-paper rounded-lg p-4 space-y-3" data-testid="theme-explorer-prompt">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Sparkles className="h-4 w-4 text-primary" />
          {t("geo.themeExplorer.v2.discoverTitle")}
          <span className="text-xs font-normal text-muted-foreground">{t("geo.themeExplorer.v2.discoverHint")}</span>
        </div>
        <div className="space-y-3">
          <p className="text-xs text-muted-foreground">{t("geo.themeExplorer.live.intro")}</p>

          {/* チャット履歴 */}
          {chatHistory.length > 0 && (
            <div className="space-y-2 max-h-56 overflow-y-auto pr-1" data-testid="theme-explorer-chat-history">
              {chatHistory.map((turn, i) => (
                <div key={i} className="text-sm">
                  <span className="font-medium">
                    {turn.role === "user" ? t("geo.themeExplorer.live.you") : t("geo.themeExplorer.live.assistant")}:
                  </span>{" "}
                  <span className="text-muted-foreground">{turn.content}</span>
                </div>
              ))}
            </div>
          )}

          <form
            onSubmit={(e) => {
              e.preventDefault();
              sendChat();
            }}
          >
            <Textarea
              value={chatMessage}
              onChange={(e) => setChatMessage(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                  e.preventDefault();
                  sendChat();
                }
              }}
              rows={3}
              className="min-h-[84px] resize-y text-base leading-relaxed"
              placeholder={t("geo.themeExplorer.live.chatPlaceholder")}
              aria-label={t("geo.themeExplorer.live.chatPlaceholder")}
            />
            <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
              <p className="text-[11px] text-muted-foreground">{t("geo.themeExplorer.prompt.hint")}</p>
              <Button type="submit" variant="secondary" disabled={chat.isPending || !chatMessage.trim()}>
                {chat.isPending ? (
                  <Loader2 className="h-4 w-4 mr-1.5 animate-spin" />
                ) : (
                  <Sparkles className="h-4 w-4 mr-1.5" />
                )}
                {chat.isPending ? t("geo.themeExplorer.live.sending") : t("geo.themeExplorer.live.send")}
              </Button>
            </div>
          </form>
          {chat.isError && (
            <p className="text-sm text-destructive" role="alert">
              {t("geo.themeExplorer.live.chatError", { message: (chat.error as Error).message })}
            </p>
          )}

          {/* すり合わせ済みのBrief: ソース選定・Web検索・実行 */}
          {brief && (
            <div className="rounded-md border border-border/60 p-3 space-y-2" data-testid="theme-explorer-brief">
              <p className="text-xs font-medium text-muted-foreground">{t("geo.themeExplorer.live.briefTitle")}</p>
              <div className="flex flex-wrap items-center gap-1.5">
                {brief.keywords.map((k) => (
                  <Badge key={k} variant="outline">{k}</Badge>
                ))}
                {brief.exclude_mainstream && <Badge variant="secondary">{t("geo.themeExplorer.live.briefNiche")}</Badge>}
                {brief.region && <Badge variant="outline">{t("geo.themeExplorer.live.briefRegion", { region: brief.region })}</Badge>}
                {(brief.actors?.length ?? 0) > 0 && (
                  <Badge variant="outline">{t("geo.themeExplorer.live.briefActors", { names: (brief.actors ?? []).join("・") })}</Badge>
                )}
                {brief.angle && <Badge variant="outline">{t("geo.themeExplorer.live.briefAngle", { angle: brief.angle })}</Badge>}
                <Badge variant="outline">{t("geo.themeExplorer.live.briefPeriod", { days: brief.period_days })}</Badge>
              </div>

              <div className="flex items-center justify-between gap-2 pt-1">
                <p className="text-xs text-muted-foreground">
                  {briefSourceIds.length > 0
                    ? briefSourceNames.slice(0, 4).join(" / ") + (briefSourceIds.length > 4 ? ` ほか${briefSourceIds.length - 4}件` : "")
                    : t("geo.themeExplorer.live.noSourcesPicked")}
                </p>
                <Button variant="outline" size="sm" onClick={() => setSourcePickerOpen(true)}>
                  {briefSourceIds.length > 0 ? t("geo.themeExplorer.live.changeSources") : t("geo.themeExplorer.live.pickSources")}
                </Button>
              </div>

              <label className="flex items-center gap-2 text-xs cursor-pointer pt-1">
                <Switch checked={includeWeb} onCheckedChange={setIncludeWeb} />
                {t("geo.themeExplorer.live.includeWeb")}
              </label>

              <div className="flex justify-end pt-1">
                <Button size="lg" disabled={running} onClick={startLiveExplore}>
                  {running ? (
                    <Loader2 className="h-4 w-4 mr-1.5 animate-spin" />
                  ) : (
                    <Sparkles className="h-4 w-4 mr-1.5" />
                  )}
                  {running ? t("geo.themeExplorer.exploringButton") : t("geo.themeExplorer.live.startButton")}
                </Button>
              </div>
            </div>
          )}

          {result?.web && (
            <div className="text-xs text-muted-foreground space-y-0.5">
              <p>{t("geo.themeExplorer.live.webResult", { fetched: result.web.fetched })}</p>
              {result.expert && (
                <p data-testid="theme-explorer-expert-note">
                  {result.expert.has_support
                    ? t("geo.themeExplorer.live.expertSupport", {
                        names: [...result.expert.knowledge.map((k) => k.title ?? k.type), ...result.expert.issues].join("、"),
                      })
                    : t("geo.themeExplorer.live.expertNoSupport")}
                </p>
              )}
              {(result.searched_topics?.length ?? 0) > 0 && (
                <details>
                  <summary className="cursor-pointer">
                    {t("geo.themeExplorer.live.searchedTopics", { count: result.searched_topics?.length ?? 0 })}
                  </summary>
                  <ul className="list-disc pl-5 space-y-0.5">
                    {(result.searched_topics ?? []).map((q, i) => (
                      <li key={i}>{q}</li>
                    ))}
                  </ul>
                </details>
              )}
              {result.web.errors.map((m, i) => (
                <p key={i}>{t("geo.themeExplorer.live.webErrors", { message: m })}</p>
              ))}
            </div>
          )}

          {/* 進行状況: 実際の処理段階（入力を解釈／関連記事を検索／テーマ候補を整理）に連動。疑似進捗ではない */}
          {running && (
            <div
              className="rounded-md border border-primary/30 bg-primary/5 p-3 space-y-2"
              role="status"
              data-testid="theme-explorer-exploring"
            >
              <p className="text-sm flex items-center gap-2">
                <Loader2 className="h-4 w-4 animate-spin shrink-0" />
                {t("geo.themeExplorer.exploring")}
              </p>
              {job.data && (
                <ol className="space-y-1 pl-6 text-xs" data-testid="theme-explorer-steps">
                  {job.data.steps.map((s) => (
                    <li key={s.key} className="flex items-center gap-1.5" data-state={s.state}>
                      {s.state === "done" ? (
                        <CheckCircle2 className="h-3.5 w-3.5 text-geo-green" />
                      ) : s.state === "running" ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
                      ) : (
                        <Circle className="h-3.5 w-3.5 text-muted-foreground/50" />
                      )}
                      <span className={s.state === "pending" ? "text-muted-foreground" : ""}>
                        {t(`geo.themeExplorer.step.${s.key}`)}
                      </span>
                    </li>
                  ))}
                </ol>
              )}
            </div>
          )}
          {errorMessage && !running && (
            <p className="text-sm text-destructive" role="alert" data-testid="theme-explorer-discovery-error">
              {t("geo.themeExplorer.discovery.error", { message: errorMessage })}
            </p>
          )}
        </div>
      </section>

      {/* 過去に見つかったテーマ候補の検索: 補助機能のため折りたたみ（既定は閉じた状態）。
          キーワード・注目度・新規のみ・並べ替えの設定は折りたたんでいても有効で、下の一覧へ反映される */}
      <details className="card-paper rounded-lg p-4 group" data-testid="theme-explorer-toolbar">
        <summary className="cursor-pointer list-none flex items-center justify-between gap-3 flex-wrap">
          <span className="text-sm font-medium">{t("geo.themeExplorer.v2.searchLabel")}</span>
          <DataFreshnessBadge freshness={data?.data_freshness} />
        </summary>
        <div className="mt-3 space-y-3">
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              setKeyword(keywordInput.trim());
              setPage(1);
            }}
          >
            <div className="relative flex-1">
              <Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
              <Input
                id="theme-keyword"
                value={keywordInput}
                onChange={(e) => setKeywordInput(e.target.value)}
                placeholder={t("geo.themeExplorer.v2.searchPlaceholder")}
                className="pl-9 h-10"
              />
            </div>
            <Button type="submit" className="h-10 px-5">
              {t("geo.themeExplorer.v2.searchButton")}
            </Button>
          </form>

          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <div className="flex items-center gap-1.5" role="group" aria-label={t("geo.themeExplorer.v2.level")}>
              <span className="text-xs text-muted-foreground">{t("geo.themeExplorer.v2.level")}</span>
              {(["all", "high", "medium", "low"] as const).map((lv) => (
                <button
                  key={lv}
                  type="button"
                  aria-pressed={level === lv}
                  onClick={() => {
                    setLevel(lv);
                    setPage(1);
                  }}
                  className={`px-2.5 py-1 rounded-full border text-xs transition-colors ${
                    level === lv
                      ? "bg-primary text-primary-foreground border-primary"
                      : "bg-background hover:bg-muted border-border"
                  }`}
                >
                  {lv === "all" ? t("geo.themeExplorer.v2.levelAll") : t(`geo.themeExplorer.attention.${lv}`)}
                </button>
              ))}
            </div>
            <label className="flex items-center gap-2 text-xs cursor-pointer">
              <Switch
                checked={newOnly}
                onCheckedChange={(v) => {
                  setNewOnly(v);
                  setPage(1);
                }}
              />
              {t("geo.themeExplorer.v2.newOnly")}
            </label>
            <div className="flex items-center gap-1.5 ml-auto">
              <span className="text-xs text-muted-foreground">{t("geo.themeExplorer.v2.sort")}</span>
              <Select value={sortMode} onValueChange={(v) => setSortMode(v as SortMode)}>
                <SelectTrigger className="w-[170px] h-8" aria-label={t("geo.themeExplorer.sort.label")}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="newest">{t("geo.themeExplorer.sort.newest")}</SelectItem>
                  <SelectItem value="attention">{t("geo.themeExplorer.v2.sortAttention")}</SelectItem>
                  <SelectItem value="related">{t("geo.themeExplorer.v2.sortRelated")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-1.5" data-testid="theme-quick-search">
            <span className="text-xs text-muted-foreground mr-1">{t("geo.themeExplorer.v2.quickTitle")}</span>
            {QUICK_KEYWORDS.map((w) => (
              <button
                key={w}
                type="button"
                onClick={() => pickQuick(w)}
                className={`px-2.5 py-1 rounded-full border text-xs transition-colors ${
                  keyword === w ? "bg-primary/10 border-primary text-primary" : "bg-background hover:bg-muted border-border"
                }`}
              >
                {w}
              </button>
            ))}
          </div>
        </div>
      </details>

      {recommendInputForBrief && (
        <SourceSelector
          open={sourcePickerOpen}
          onOpenChange={setSourcePickerOpen}
          selectedIds={briefSourceIds}
          onApply={applySources}
          recommendInput={recommendInputForBrief}
        />
      )}

      {data && data.data_freshness.status !== "fresh" && (
        <p className="text-sm rounded-md border border-geo-amber/50 bg-geo-amber-soft text-geo-amber p-3">
          {t("geo.themeExplorer.staleNote")}
        </p>
      )}

      {/* 今回の探索結果（新しい候補・既存テーマの更新・見つからなかった場合の説明） */}
      {result && (
        <ThemeDiscoveryResults
          result={result}
          overrides={overrides}
          onOpenDetail={onOpenDetail}
          onChangeSources={onChangeSources}
        />
      )}

      <div className="grid gap-5 lg:grid-cols-[230px_1fr] items-start">
        {/* 左: テーマカテゴリ（件数つき）。クリックで絞り込み */}
        <aside className="card-paper rounded-lg p-3 lg:sticky lg:top-4" data-testid="theme-category-sidebar">
          <h2 className="text-sm font-medium mb-2 px-1">{t("geo.themeExplorer.v2.categoryTitle")}</h2>
          <ul className="space-y-0.5">
            {[{ key: "all" as const }, ...THEME_CATEGORIES, OTHER_CATEGORY].map((c) => {
              const count = c.key === "all" ? preFiltered.length : (categoryCounts.get(c.key) ?? 0);
              if (c.key !== "all" && count === 0 && category !== c.key) return null;
              const active = category === c.key;
              return (
                <li key={c.key}>
                  <button
                    type="button"
                    aria-pressed={active}
                    onClick={() => {
                      setCategory(c.key);
                      setPage(1);
                    }}
                    className={`w-full flex items-center justify-between gap-2 rounded-md px-2.5 py-1.5 text-sm text-left transition-colors ${
                      active ? "bg-primary text-primary-foreground font-medium" : "hover:bg-muted"
                    }`}
                  >
                    <span>
                      {c.key === "all"
                        ? t("geo.themeExplorer.v2.categoryAll")
                        : t(`geo.themeExplorer.v2.cat.${c.key}`)}
                    </span>
                    <span
                      className={`tabular-nums text-xs rounded-full px-1.5 ${
                        active ? "bg-primary-foreground/20" : "bg-muted text-muted-foreground"
                      }`}
                    >
                      {count}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </aside>

        <div className="space-y-3 min-w-0">
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <h2 className="text-sm font-medium">{t("geo.themeExplorer.listTitle")}</h2>
            <div className="flex items-center gap-3">
              {data && (
                <span className="text-xs text-muted-foreground tabular-nums" data-testid="theme-explorer-count">
                  {narrowed
                    ? t("geo.themeExplorer.v2.resultCountNarrowed", { total, available: availableTotal })
                    : t("geo.themeExplorer.v2.resultCount", { total })}
                </span>
              )}
              {narrowed && (
                <button type="button" onClick={clearFilters} className="text-xs text-primary underline underline-offset-2">
                  {t("geo.themeExplorer.v2.clearFilters")}
                </button>
              )}
            </div>
          </div>
          {truncated && (
            <p className="text-[11px] text-muted-foreground">
              {t("geo.themeExplorer.v2.truncated", { n: data?.themes.length ?? 0 })}
            </p>
          )}

          {isError && (
            <p className="text-sm text-destructive" role="alert">
              {t("geo.themeExplorer.error", { message: (error as Error).message })}
            </p>
          )}

          {isLoading ? (
            <div className="grid gap-3" data-testid="theme-explorer-loading">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-40 w-full rounded-lg" />
              ))}
            </div>
          ) : data && listThemes.length === 0 ? (
            <div className="card-paper rounded-lg p-8 text-center space-y-2" data-testid="theme-explorer-empty">
              {availableTotal === 0 && shownIds.size === 0 ? (
                <>
                  {/* 候補そのものが0件: 「テーマ探索開始」で記事から候補を作れることを案内するだけでなく、
                      Phase C §14: Episode Review側で止まっている場合はそれも分かるようにする
                      （「データが無い」のか「処理していない」のかを区別する）。 */}
                  <p className="text-sm font-medium text-foreground">
                    {t("geo.themeExplorer.emptyNoCandidates.title")}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {t("geo.themeExplorer.emptyNoCandidates.body")}
                  </p>
                  <Link to="/geo/news-signals" className="text-xs text-primary underline underline-offset-2">
                    {t("geo.themeDiscovery.emptyState.devLink")}
                  </Link>
                  <EpisodePipelineEmptyStateHint />
                </>
              ) : availableTotal === 0 ? (
                <p className="text-sm text-muted-foreground">{t("geo.themeExplorer.allShownAbove")}</p>
              ) : (
                <p className="text-sm text-foreground">
                  {t("geo.themeExplorer.emptyNarrowed", { available: availableTotal })}
                </p>
              )}
            </div>
          ) : (
            <div className="grid gap-3">
              {pageRows.map((th, i) => (
                <ThemeExplorerCard
                  key={th.candidate_id}
                  theme={th}
                  rank={(curPage - 1) * PAGE_SIZE + i + 1}
                  maxRelated={maxRelated}
                  override={overrides.get(th.candidate_id)}
                  onOpenDetail={() => onOpenDetail(th.candidate_id)}
                  onChangeSources={onChangeSources}
                  onUsePending={hasPendingPick && onUsePending ? () => onUsePending(th.candidate_id) : undefined}
                />
              ))}
            </div>
          )}

          {data && total > 0 && (
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>{t("geo.themeExplorer.page", { page: curPage, pages, total })}</span>
              <div className="flex gap-2">
                <Button size="sm" variant="outline" disabled={curPage <= 1} onClick={() => setPage(curPage - 1)}>
                  {t("geo.themeExplorer.prev")}
                </Button>
                <Button size="sm" variant="outline" disabled={curPage >= pages} onClick={() => setPage(curPage + 1)}>
                  {t("geo.themeExplorer.next")}
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>

    </div>
  );
}
