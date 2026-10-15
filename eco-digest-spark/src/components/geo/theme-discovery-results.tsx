import { Link } from "@tanstack/react-router";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ThemeExplorerCard, EvidenceList } from "@/components/geo/theme-explorer-card";
import type {
  ThemeActionTarget,
  ThemeSourceOverride,
} from "@/components/geo/theme-explorer-actions";
import { useLanguage } from "@/lib/i18n";
import type { DiscoveryResult } from "@/lib/geoSourceLibraryApi";

// 「テーマ探索開始」の結果表示（V1_1）。
//  - 今回見つかった新しいTheme Candidate（記事から発見。AIの「提案」で、人間が判断するまで確定しない）
//  - 既存テーマに新しい動きがあるもの（新規候補を乱造せず、既存への更新候補として見せる）
//  - 材料が足りない時は「該当する新しいテーマ候補は見つかりませんでした」（無理に作らない）

export function ThemeDiscoveryResults({
  result,
  overrides,
  onOpenDetail,
  onChangeSources,
}: {
  result: DiscoveryResult;
  overrides: Map<string, ThemeSourceOverride>;
  onOpenDetail: (candidateId: string) => void;
  onChangeSources?: (target: ThemeActionTarget, currentIds: string[]) => void;
}) {
  const { t } = useLanguage();
  const s = result.stats;
  const scope =
    result.source_scope.mode === "all"
      ? t("geo.themeExplorer.scope.all")
      : t("geo.themeExplorer.scope.selected", { count: result.source_scope.source_count });

  return (
    <section className="space-y-4" data-testid="theme-discovery-results">
      <div className="space-y-1">
        <h2 className="text-sm font-medium">{t("geo.themeExplorer.discovery.resultsTitle")}</h2>
        <p className="text-xs text-muted-foreground" data-testid="theme-discovery-summary">
          {t("geo.themeExplorer.discovery.summary", {
            period: result.period_days,
            scope,
            considered: s.articles_considered,
            used: s.articles_used,
            created: s.themes_created ?? 0,
          })}
          {result.from_cache && <span> {t("geo.themeExplorer.discovery.fromCache")}</span>}
        </p>
        {result.candidates.length > 0 && (
          <p className="text-xs text-muted-foreground">{t("geo.themeExplorer.discovery.pendingNote")}</p>
        )}
      </div>

      {result.message && (
        <div className="card-paper rounded-lg p-6 text-center space-y-1" data-testid="theme-discovery-none">
          <p className="text-sm font-medium">{t("geo.themeExplorer.discovery.noResult")}</p>
          {result.reason && <p className="text-xs text-muted-foreground">{result.reason}</p>}
        </div>
      )}

      {result.candidates.length > 0 && (
        <div className="grid gap-3">
          {result.candidates.map((th) => (
            <ThemeExplorerCard
              key={th.candidate_id}
              theme={th}
              override={overrides.get(th.candidate_id)}
              onOpenDetail={() => onOpenDetail(th.candidate_id)}
              onChangeSources={onChangeSources}
            />
          ))}
        </div>
      )}

      {result.existing_updates.length > 0 && (
        <div className="space-y-2" data-testid="theme-discovery-updates">
          <h3 className="text-sm font-medium">{t("geo.themeExplorer.discovery.updatesTitle")}</h3>
          {result.existing_updates.map((u, i) => (
            <div key={`${u.existing.id}-${i}`} className="card-paper rounded-lg p-4 space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="secondary">
                  {t(`geo.themeExplorer.discovery.existingType.${u.existing.type}`)}
                </Badge>
                <span className="font-medium text-sm">{u.existing.title}</span>
              </div>
              <p className="text-xs text-muted-foreground">
                {t("geo.themeExplorer.discovery.proposedAs", { title: u.proposed_title })}
              </p>
              {u.has_new_articles ? (
                <EvidenceList articles={u.new_articles} total={u.new_articles.length} />
              ) : (
                <p className="text-xs text-muted-foreground">{t("geo.themeExplorer.discovery.noNewArticles")}</p>
              )}
              {u.existing.type === "candidate" && (
                <Button size="sm" variant="outline" onClick={() => onOpenDetail(u.existing.id)}>
                  {t("geo.themeExplorer.discovery.openExisting")}
                </Button>
              )}
              {u.existing.type === "monitoring" && (
                <Button size="sm" variant="outline" asChild>
                  <Link to="/geo/monitoring-themes/$monitoringThemeId" params={{ monitoringThemeId: u.existing.id }}>
                    {t("geo.themeExplorer.discovery.openExisting")}
                  </Link>
                </Button>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
