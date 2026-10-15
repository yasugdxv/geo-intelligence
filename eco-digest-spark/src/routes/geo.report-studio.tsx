import { createFileRoute, Link, Outlet, useRouterState } from "@tanstack/react-router";
import { useState } from "react";
import { TopBar } from "@/components/top-bar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { GeoStatusBadge } from "@/components/geo/geo-badge";
import { useCreateGeoReportProject, useDeleteGeoReportProject, useGeoReportProjects } from "@/lib/geoReportStudioApi";
import { useLanguage } from "@/lib/i18n";

export const Route = createFileRoute("/geo/report-studio")({
  component: GeoReportStudioPage,
});

function GeoReportStudioPage() {
  const { t } = useLanguage();
  const pathname = useRouterState({ select: (r) => r.location.pathname });
  const [showNewProject, setShowNewProject] = useState(false);
  const [projectCode, setProjectCode] = useState("");
  const [projectTitle, setProjectTitle] = useState("");
  const projectsQuery = useGeoReportProjects();
  const createMutation = useCreateGeoReportProject();
  const deleteMutation = useDeleteGeoReportProject();

  // /geo/report-studio/$projectId は本ファイルの子ルートとして登録されるため、
  // 詳細ページ表示中は一覧を出さずOutletのみ描画する（geo.cases.tsxと同じ対処）。
  // Hooksは全てこのif分岐より前で呼び出すこと（Rules of Hooks違反防止、実際に
  // 子ルート遷移時にクラッシュするバグとしてVisual QAで発見・修正済み）。
  if (pathname !== "/geo/report-studio") {
    return <Outlet />;
  }

  const onCreate = () => {
    if (!projectCode.trim() || !projectTitle.trim()) return;
    createMutation.mutate(
      { project_code: projectCode.trim(), project_title: projectTitle.trim() },
      { onSuccess: () => { setProjectCode(""); setProjectTitle(""); setShowNewProject(false); } },
    );
  };

  return (
    <>
      <TopBar breadcrumb={[{ label: t("nav.geoReportStudio") }]} />
      <main className="flex-1 max-w-[1000px] w-full mx-auto px-8 py-8 space-y-6">
        <section className="border-b border-border pb-5 flex items-start justify-between">
          <div>
            <h1 className="text-editorial text-4xl">{t("geo.reportStudio.title")}</h1>
            <p className="text-sm text-muted-foreground mt-2">{t("geo.reportStudio.subtitle")}</p>
          </div>
          <Button onClick={() => setShowNewProject((v) => !v)}>{t("geo.reportStudio.newProject")}</Button>
        </section>

        {showNewProject && (
          <div className="card-paper rounded-lg p-5 space-y-3">
            <h2 className="text-editorial text-lg">{t("geo.reportStudio.newProject")}</h2>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">{t("geo.reportStudio.projectCodeLabel")}</label>
              <Input value={projectCode} onChange={(e) => setProjectCode(e.target.value)}
                     placeholder="例: iran" className="max-w-xs" />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">{t("geo.reportStudio.projectTitleLabel")}</label>
              <Input value={projectTitle} onChange={(e) => setProjectTitle(e.target.value)} />
            </div>
            {createMutation.isError && (
              <p className="text-xs text-destructive">{(createMutation.error as Error).message}</p>
            )}
            <Button onClick={onCreate} disabled={createMutation.isPending}>
              {t("geo.reportStudio.create")}
            </Button>
          </div>
        )}

        {deleteMutation.isError && (
          <p className="text-xs text-destructive">{(deleteMutation.error as Error).message}</p>
        )}

        <div className="space-y-2">
          {(projectsQuery.data?.projects ?? []).map(({ project, latest_run, review_required_count }) => (
            <Link key={project.project_id} to="/geo/report-studio/$projectId"
                  params={{ projectId: project.project_id }}
                  className="card-paper rounded-lg p-4 block hover:bg-muted/40 transition-colors">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-editorial text-lg">{project.project_title}</div>
                  <div className="text-xs text-muted-foreground mt-1 font-mono">{project.project_code}</div>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  {review_required_count > 0 && (
                    <GeoStatusBadge severity="elevated">
                      {t("geo.reportStudio.reviewRequired")} {review_required_count}
                    </GeoStatusBadge>
                  )}
                  <Badge variant={latest_run?.status === "completed" ? "secondary" : "outline"}>
                    {latest_run?.status ?? t("geo.reportStudio.noRunsYet")}
                  </Badge>
                  <button
                    type="button"
                    className="text-xs text-muted-foreground hover:text-destructive px-1"
                    title={t("geo.reportStudio.deleteProject")}
                    disabled={deleteMutation.isPending}
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      if (window.confirm(t("geo.reportStudio.deleteProjectConfirm", { title: project.project_title }))) {
                        deleteMutation.mutate(project.project_id);
                      }
                    }}
                  >
                    🗑
                  </button>
                </div>
              </div>
              <div className="text-xs text-muted-foreground mt-2 flex gap-4">
                {latest_run?.period_start && latest_run?.period_end && (
                  <span>{t("geo.reportStudio.period")}: {latest_run.period_start} – {latest_run.period_end}</span>
                )}
                <span>{t("geo.reportStudio.updated")}: {new Date(project.updated_at).toLocaleString()}</span>
              </div>
            </Link>
          ))}
          {(!projectsQuery.data || projectsQuery.data.projects.length === 0) && (
            <p className="text-sm text-muted-foreground">{t("geo.reportStudio.empty")}</p>
          )}
        </div>
      </main>
    </>
  );
}
