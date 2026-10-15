import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  ThemeSourceActions,
  type ThemeActionTarget,
  type ThemeSourceOverride,
} from "@/components/geo/theme-explorer-actions";
import { useLanguage } from "@/lib/i18n";
import { useUpdateThemeCandidateStatus } from "@/lib/geoThemeCandidateApi";
import type { EvidenceArticle, ExplorerTheme } from "@/lib/geoSourceLibraryApi";
import { categorizeTheme } from "@/lib/themeCategories";

// Theme Explorerのカード。注目度（暫定・説明可能）は level のみ表示し、根拠は展開で見せる。
// 「Priority」とは呼ばない（正式なPriorityモデルが無いため）。

const LEVEL_STYLE: Record<ExplorerTheme["attention"]["level"], string> = {
  high: "bg-red-100 text-red-800",
  medium: "bg-amber-100 text-amber-800",
  low: "bg-muted text-muted-foreground",
};
const STATUS_STYLE: Record<ExplorerTheme["status"], string> = {
  NEW: "bg-sky-100 text-sky-800",
  SHORTLISTED: "bg-emerald-100 text-emerald-800",
  MONITORING: "bg-amber-100 text-amber-800",
  DISMISSED: "bg-muted text-muted-foreground",
  HANDED_OFF: "bg-violet-100 text-violet-800",
};

export function AttentionBadge({ attention }: { attention: ExplorerTheme["attention"] }) {
  const { t } = useLanguage();
  const [open, setOpen] = useState(false);
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <span
          className={`px-2 py-0.5 rounded-full text-[11px] font-bold ${LEVEL_STYLE[attention.level]}`}
          title={t("geo.themeExplorer.attention.note")}
        >
          {t("geo.themeExplorer.attention.label")}:{" "}
          {t(`geo.themeExplorer.attention.${attention.level}`)}
        </span>
        <button
          type="button"
          className="text-[11px] text-muted-foreground underline underline-offset-2"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          {open ? t("geo.themeExplorer.attention.hide") : t("geo.themeExplorer.attention.show")}
        </button>
      </div>
      {open && (
        <div className="rounded-md border border-border/60 p-2 text-xs space-y-1">
          <p className="text-muted-foreground">{t("geo.themeExplorer.attention.note")}</p>
          <ul className="space-y-0.5">
            {attention.breakdown.map((b) => (
              <li key={b.label}>
                <span className="font-medium">{b.label}</span>{" "}
                <span className="tabular-nums">
                  {b.points}/{b.max}
                </span>
                {b.note && <span className="text-muted-foreground"> — {b.note}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/** テーマの削除（論理削除=DISMISSED。データは保持され、一覧から外れるだけ）。確認ダイアログつき。 */
function DeleteThemeButton({ theme }: { theme: ExplorerTheme }) {
  const { t } = useLanguage();
  const queryClient = useQueryClient();
  const update = useUpdateThemeCandidateStatus();
  const [open, setOpen] = useState(false);
  return (
    <div className="shrink-0">
      <AlertDialog open={open} onOpenChange={setOpen}>
        <AlertDialogTrigger asChild>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="text-muted-foreground hover:text-destructive"
            data-testid="theme-delete-button"
          >
            <Trash2 className="h-4 w-4 mr-1" />
            {t("geo.themeExplorer.delete.button")}
          </Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("geo.themeExplorer.delete.confirmTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("geo.themeExplorer.delete.confirmBody", { title: theme.title })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("geo.themeExplorer.delete.cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault(); // 失敗時にダイアログを閉じないようにする
                update.mutate(
                  { candidateId: theme.candidate_id, status: "DISMISSED" },
                  {
                    onSuccess: () => {
                      queryClient.invalidateQueries({ queryKey: ["geo-theme-explorer"] });
                      setOpen(false);
                    },
                  },
                );
              }}
              disabled={update.isPending}
            >
              {t("geo.themeExplorer.delete.confirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
          {update.isError && (
            <p className="text-xs text-destructive" role="alert">
              {t("geo.themeExplorer.delete.failed", { message: (update.error as Error).message })}
            </p>
          )}
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

/** 根拠記事（なぜこのテーマか）。タイトルから元URLへ移動できる。根拠が追えない候補にしない */
export function EvidenceList({ articles, total }: { articles?: EvidenceArticle[]; total?: number }) {
  const { t } = useLanguage();
  if (!articles || articles.length === 0) return null;
  return (
    <div className="rounded-md border border-border/60 p-2 space-y-1" data-testid="theme-evidence">
      <p className="text-[11px] font-medium text-muted-foreground">
        {t("geo.themeExplorer.card.evidence", { count: total ?? articles.length })}
      </p>
      <ul className="space-y-0.5">
        {articles.map((a) => (
          <li key={a.external_signal_id} className="text-xs leading-snug">
            {a.url ? (
              <a href={a.url} target="_blank" rel="noreferrer" className="text-primary underline underline-offset-2">
                {a.title || t("geo.sourceResearch.untitled")}
              </a>
            ) : (
              <span>{a.title || t("geo.sourceResearch.untitled")}</span>
            )}
            <span className="text-muted-foreground">
              {" "}
              — {a.source ?? "—"} ·{" "}
              {a.published_at && a.date_known !== false
                ? a.published_at.slice(0, 10)
                : t("geo.sourceResearch.dateUnknown")}
            </span>
          </li>
        ))}
      </ul>
      {total && total > articles.length ? (
        <p className="text-[11px] text-muted-foreground">
          {t("geo.themeExplorer.card.evidenceMore", { count: total - articles.length })}
        </p>
      ) : null}
    </div>
  );
}

export function ThemeExplorerCard({
  theme,
  rank,
  maxRelated = 1,
  override,
  onOpenDetail,
  onChangeSources,
  onUsePending,
}: {
  theme: ExplorerTheme;
  /** 一覧内の順位（表示順）。省略時は番号を出さない */
  rank?: number;
  /** 一覧内の関連記事数の最大値（量バーの相対表示用） */
  maxRelated?: number;
  override?: ThemeSourceOverride;
  onOpenDetail: () => void;
  onChangeSources?: (target: ThemeActionTarget, currentIds: string[]) => void;
  /** 情報ソースDBから戻った選択がある時だけ渡す */
  onUsePending?: () => void;
}) {
  const { t } = useLanguage();
  const target: ThemeActionTarget = {
    candidate_id: theme.candidate_id,
    title: theme.title,
    question: theme.question,
  };
  const cats = categorizeTheme(theme);
  const summary = theme.why_now || theme.signal_summary || t("geo.themeExplorer.card.noWhyNow");
  // 直近の関連記事: 日付が新しい順に3件（日付を先頭に置いて読み取りやすくする）
  const recent = [...(theme.evidence_articles ?? [])]
    .sort((x, y) => (y.published_at ?? "").localeCompare(x.published_at ?? ""))
    .slice(0, 3);
  const volumePct = Math.max(6, Math.round((theme.related_article_count / Math.max(1, maxRelated)) * 100));
  return (
    <div className="card-paper rounded-lg p-4 space-y-3" data-testid="theme-explorer-card">
      <div className="flex items-start gap-3">
        {rank !== undefined && (
          <span
            className="shrink-0 h-7 w-7 rounded-full bg-primary/10 text-primary text-sm font-bold flex items-center justify-center tabular-nums"
            aria-label={`#${rank}`}
          >
            {rank}
          </span>
        )}
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex items-center gap-1.5 flex-wrap">
            {cats.slice(0, 3).map((c) => (
              <span key={c.key} className={`px-2 py-0.5 rounded border text-[11px] font-medium ${c.tone}`}>
                {t(`geo.themeExplorer.v2.cat.${c.key}`)}
              </span>
            ))}
            <span className={`px-2 py-0.5 rounded-full text-[11px] font-bold ${STATUS_STYLE[theme.status]}`}>
              {t(`geo.themeDiscovery.status.${theme.status}`)}
            </span>
            <Badge variant="outline" data-testid="theme-origin-badge">
              {t(`geo.themeExplorer.origin.${theme.candidate_origin}`)}
            </Badge>
            {theme.proposal_state === "pending" && (
              <Badge className="bg-amber-100 text-amber-800 hover:bg-amber-100" data-testid="theme-pending-badge">
                {t("geo.themeExplorer.card.pending")}
              </Badge>
            )}
          </div>
          <button type="button" onClick={onOpenDetail} className="block w-full text-left">
            <span className="text-editorial text-lg leading-snug hover:underline">{theme.title}</span>
          </button>
        </div>
        <div className="shrink-0 flex flex-col items-end gap-1">
          <span
            className={`px-2.5 py-1 rounded-full text-xs font-bold ${LEVEL_STYLE[theme.attention.level]}`}
            title={t("geo.themeExplorer.attention.note")}
          >
            {t("geo.themeExplorer.v2.card.attention")}: {t(`geo.themeExplorer.attention.${theme.attention.level}`)}
          </span>
          <DeleteThemeButton theme={theme} />
        </div>
      </div>

      <p className="text-sm leading-relaxed text-foreground/90 line-clamp-3">{summary}</p>

      <div className="grid gap-3 md:grid-cols-[1fr_180px]">
        <div className="rounded-md border border-border/60 p-2.5 space-y-1.5" data-testid="theme-evidence">
          <p className="text-[11px] font-medium text-muted-foreground">{t("geo.themeExplorer.v2.card.recent")}</p>
          {recent.length === 0 ? (
            <p className="text-xs text-muted-foreground">{t("geo.themeExplorer.v2.card.noEvidence")}</p>
          ) : (
            <ul className="space-y-1">
              {recent.map((a) => (
                <li key={a.external_signal_id} className="text-xs leading-snug flex gap-2">
                  <span className="shrink-0 whitespace-nowrap tabular-nums text-muted-foreground">
                    {a.published_at && a.date_known !== false
                      ? a.published_at.slice(0, 10)
                      : t("geo.sourceResearch.dateUnknown")}
                  </span>
                  {a.url ? (
                    <a href={a.url} target="_blank" rel="noreferrer" className="text-primary hover:underline line-clamp-1">
                      {a.title || t("geo.sourceResearch.untitled")}
                    </a>
                  ) : (
                    <span className="line-clamp-1">{a.title || t("geo.sourceResearch.untitled")}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="space-y-1.5 text-xs">
          <p className="font-medium tabular-nums">
            {t("geo.themeExplorer.v2.card.related", { count: theme.related_article_count })}
          </p>
          <div className="h-2 rounded-full bg-muted overflow-hidden" aria-hidden>
            <div className="h-full bg-primary/70" style={{ width: `${volumePct}%` }} />
          </div>
          {theme.latest_signal_at && recent.some((a) => a.date_known !== false) && (
            <p className="text-muted-foreground tabular-nums">
              {t("geo.themeExplorer.v2.card.latest")}: {theme.latest_signal_at.slice(0, 10)}
            </p>
          )}
          <p className="text-muted-foreground tabular-nums">
            {t("geo.themeExplorer.v2.card.updated")}: {theme.updated_at.slice(0, 10)}
          </p>
        </div>
      </div>

      <div className="pt-2 border-t border-border/60 space-y-2">
        {onUsePending && (
          <Button size="sm" variant="secondary" onClick={onUsePending}>
            {t("geo.themeExplorer.sources.useForTheme")}
          </Button>
        )}
        <ThemeSourceActions
          target={target}
          override={override}
          onChangeSources={onChangeSources}
          layout="card"
          extra={
            <Button size="sm" variant="ghost" className="ml-auto" onClick={onOpenDetail}>
              {t("geo.themeExplorer.v2.actions.detail")} →
            </Button>
          }
        />
      </div>
    </div>
  );
}
