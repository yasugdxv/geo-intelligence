import { Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { useLanguage } from "@/lib/i18n";
import {
  useCreateMonitoringFromCandidate,
  useCreateResearchProject,
  useRecommendSources,
} from "@/lib/geoSourceLibraryApi";

// テーマ探索で使う「Theme別のSource Set」と「詳しく調べる / モニタリングに追加」アクション。
// カードとDetail Sheetの両方から使う。選択状態は親（ページ）がMap<candidateId, override>で保持する。

export interface ThemeSourceOverride {
  ids: string[];
  names: string[];
  isRecommended: boolean;
}

/** overrideがあればそれ、無ければおすすめ上位を既定のSource Setとして返す */
export function useEffectiveSources(candidateId: string, override?: ThemeSourceOverride) {
  const rec = useRecommendSources({ candidate_id: candidateId }, !override);
  if (override) return { ...override, isLoading: false, isError: false };
  const items = rec.data?.recommendations ?? [];
  return {
    ids: items.map((r) => r.source_id),
    names: items.map((r) => r.name),
    isRecommended: true,
    isLoading: rec.isLoading,
    isError: rec.isError,
  };
}

export interface ThemeActionTarget {
  candidate_id: string;
  title: string;
  question: string | null;
}

export function ThemeSourceActions({
  target,
  override,
  onChangeSources,
  showHints = false,
  extra,
  layout = "default",
}: {
  target: ThemeActionTarget;
  override?: ThemeSourceOverride;
  /** [変更する]押下。現在のSource Set（ids）を渡す */
  onChangeSources?: (target: ThemeActionTarget, currentIds: string[]) => void;
  showHints?: boolean;
  extra?: React.ReactNode;
  /** card: 一覧カード用（モニタリングを主ボタン、リサーチ・分析を副ボタンにする） */
  layout?: "default" | "card";
}) {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const sources = useEffectiveSources(target.candidate_id, override);
  const createProject = useCreateResearchProject();
  const createMonitoring = useCreateMonitoringFromCandidate();
  const [needSelect, setNeedSelect] = useState(false);
  const [monitorResult, setMonitorResult] = useState<{ id: string; already: boolean } | null>(null);

  const research = () => {
    if (sources.ids.length === 0) {
      setNeedSelect(true);
      return;
    }
    setNeedSelect(false);
    createProject.mutate(
      {
        theme: target.title,
        query: target.question ?? undefined,
        source_ids: sources.ids,
        origin: "theme_explorer",
      },
      {
        onSuccess: (res) => {
          navigate({
            to: "/geo/report-studio/$projectId",
            params: { projectId: res.project.project_id },
            search: { tab: "research" } as never,
          });
        },
      },
    );
  };

  const monitor = () => {
    createMonitoring.mutate(
      { candidate_id: target.candidate_id, source_ids: sources.ids },
      {
        onSuccess: (res) => {
          const already = res.already_monitoring;
          setMonitorResult({ id: res.monitoring_theme.monitoring_theme_id, already });
          toast(
            already
              ? t("geo.themeExplorer.actions.alreadyMonitoring")
              : t("geo.themeExplorer.actions.monitorAdded"),
          );
        },
      },
    );
  };

  return (
    <div className="space-y-2" data-testid="theme-source-actions">
      {needSelect && (
        <p className="text-xs text-geo-amber">{t("geo.themeExplorer.sources.pleaseSelect")}</p>
      )}

      {layout === "card" ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={monitor} disabled={createMonitoring.isPending || sources.isLoading}>
            {createMonitoring.isPending
              ? t("geo.themeExplorer.actions.monitoring")
              : t("geo.themeExplorer.v2.actions.monitor")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={research}
            disabled={createProject.isPending || sources.isLoading}
          >
            {createProject.isPending
              ? t("geo.themeExplorer.actions.researching")
              : t("geo.themeExplorer.v2.actions.research")}
          </Button>
          {extra}
        </div>
      ) : (
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          onClick={research}
          disabled={createProject.isPending || sources.isLoading}
        >
          {createProject.isPending
            ? t("geo.themeExplorer.actions.researching")
            : t("geo.themeExplorer.actions.research")}
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={monitor}
          disabled={createMonitoring.isPending || sources.isLoading}
        >
          {createMonitoring.isPending
            ? t("geo.themeExplorer.actions.monitoring")
            : t("geo.themeExplorer.actions.monitor")}
        </Button>
        {extra}
      </div>

      )}

      {showHints && (
        <div className="text-[11px] text-muted-foreground space-y-0.5">
          <p>{t("geo.themeExplorer.actions.researchHint")}</p>
          <p>{t("geo.themeExplorer.actions.monitorHint")}</p>
        </div>
      )}

      {monitorResult && (
        <p className="text-xs">
          <span className="font-medium">
            {monitorResult.already
              ? t("geo.themeExplorer.actions.alreadyMonitoring")
              : t("geo.themeExplorer.actions.monitorAdded")}
          </span>{" "}
          <Link
            to={"/geo/monitoring-themes/$id" as never}
            params={{ id: monitorResult.id } as never}
            className="text-primary underline underline-offset-2"
          >
            {t("geo.themeExplorer.actions.openMonitoring")}
          </Link>
        </p>
      )}
      {createProject.isError && (
        <p className="text-xs text-destructive">{(createProject.error as Error).message}</p>
      )}
      {createMonitoring.isError && (
        <p className="text-xs text-destructive">{(createMonitoring.error as Error).message}</p>
      )}
    </div>
  );
}
