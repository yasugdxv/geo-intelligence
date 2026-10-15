import { createFileRoute, Outlet, useRouterState } from "@tanstack/react-router";
import { TopBar } from "@/components/top-bar";
import { GeoExpertSessionHomeView } from "@/components/geo/geo-expert-session-home-view";
import { useLanguage } from "@/lib/i18n";

// Expert Session Capture V1（Phase S2-S3、2026-10-02）: Session Home。
export const Route = createFileRoute("/geo/expert-session")({
  component: GeoExpertSessionHomePage,
});

function GeoExpertSessionHomePage() {
  const { t } = useLanguage();
  const pathname = useRouterState({ select: (r) => r.location.pathname });

  // /geo/expert-session/$caseKey（Case詳細）は本ファイルの子ルートとして登録される
  // （TanStack Routerのfile-based routing規約、geo.ios.analysis.tsxと同じ対処）。
  // これを入れていないと、URLはCase詳細に正しく変わるのにこのHomeページの中身が
  // 表示され続ける（2026-10-02、実ブラウザ監査で発見）。
  if (pathname !== "/geo/expert-session") {
    return <Outlet />;
  }

  return (
    <>
      <TopBar breadcrumb={[{ label: t("geo.expertSession.homeTitle") }]} />
      <main className="flex-1 max-w-[1100px] w-full mx-auto px-8 py-8">
        <GeoExpertSessionHomeView />
      </main>
    </>
  );
}
