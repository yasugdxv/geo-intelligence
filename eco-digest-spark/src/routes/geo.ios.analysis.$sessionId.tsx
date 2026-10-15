import { createFileRoute } from "@tanstack/react-router";
import { TopBar } from "@/components/top-bar";
import { GeoStatusBadge } from "@/components/geo/geo-badge";
import { GeoIosIntakeView } from "@/components/geo/geo-ios-intake-view";
import { GeoIosSituationAnalysisView } from "@/components/geo/geo-ios-situation-analysis-view";
import { GeoExpertAiProgress } from "@/components/geo/geo-expert-ai-progress";
import {
  useAnalysisSession,
  useScenarioCandidates,
  useGenerateScenarios,
} from "@/lib/geoIosAnalysisSessionApi";
import { useLanguage } from "@/lib/i18n";

// Geopolitical Intelligence OS Phase H §5-10 → Platform IA / Expert AI Reading UI Cleanup
// （2026-10-02、ユーザー指示）: Expert AI相談は「Readingを提案・分析するだけの機能」に
// 専念させる。旧「相談する」モード（切り口相談＋Scenario Exploration＋Analysis Design
// Workspace、Draft生成への導線）は全て削除した——次に進むフロー（Draft/専門家レビュー/
// 承認・ストック）自体が無くなったため、「この設計で確定」ボタンと合わせて丸ごと撤去する
// （ユーザー指示「次に進むフローはないため不要」）。レポート作成が必要な場合は、独立した
// レポートレビュー（/geo/ios/review-intake）のアップロード/貼り付け入口を使う。
// Expert AI Engine・CQAS Logicは変更しない。
export const Route = createFileRoute("/geo/ios/analysis/$sessionId")({
  component: GeoIosAnalysisSessionPage,
});

function GeoIosAnalysisSessionPage() {
  const { sessionId } = Route.useParams();
  const { t } = useLanguage();
  const actorId =
    typeof window !== "undefined"
      ? (localStorage.getItem("geo_ios_actor_id") ?? "analyst")
      : "analyst";

  const sessionQuery = useAnalysisSession(sessionId);
  const allCandidatesQuery = useScenarioCandidates(sessionId, false);
  const generateScenarios = useGenerateScenarios(sessionId);

  const session = sessionQuery.data;
  const candidates = allCandidatesQuery.data?.candidates ?? [];

  if (!session) {
    return (
      <>
        <TopBar
          breadcrumb={[
            { label: t("nav.geoExpertAI"), href: "/geo/ios/analysis" },
            { label: sessionId.slice(0, 8) },
          ]}
        />
        <main className="flex-1 max-w-[1100px] w-full mx-auto px-8 py-8">
          <p className="text-sm text-muted-foreground">
            {sessionQuery.isLoading ? "..." : "Session not found"}
          </p>
        </main>
      </>
    );
  }

  // titleはIntake画面で必須入力される独立したフィールド（テーマ本文の先頭を流用しない、
  // geo.ios.analysis.tsxのstart()参照）。Breadcrumbはメニューと被らないようさらに短縮する。
  const breadcrumbLabel =
    session.title.length > 40 ? `${session.title.slice(0, 40)}…` : session.title;

  return (
    <>
      <TopBar
        breadcrumb={[
          { label: t("nav.geoExpertAI"), href: "/geo/ios/analysis" },
          { label: breadcrumbLabel },
        ]}
      />
      <main className="flex-1 max-w-[1200px] w-full mx-auto px-8 py-8 space-y-6">
        <section className="border-b border-border pb-5 space-y-4">
          {/* Platform IA / Expert AI Reading UI Cleanup（2026-10-02）: 冒頭説明を狭い
              左カラムに閉じ込めず、メインコンテンツ領域の横幅を十分使った1カラム表示に
              する（ユーザー指示§9）。下の「今回のポイント」等、他セクションと同じ幅まで
              広げる（独自のmax-widthは付けない）——Context Label → Title → Current
              Situationの順は維持する。 */}
          <div className="flex items-center justify-between gap-4">
            <p className="m-0 text-[12px] font-bold tracking-[0.08em] text-[#0d879a] uppercase">
              {t("geo.ios.situation.contextLabel")}
            </p>
            <GeoStatusBadge severity="neutral" className="shrink-0">
              {session.status}
            </GeoStatusBadge>
          </div>
          <div className="space-y-3">
            <h1 className="geo-heading text-3xl font-semibold m-0">{session.title}</h1>
            {session.theme && (
              <div className="space-y-2">
                <b className="block text-[13px] text-[#687b88]">
                  {t("geo.ios.situation.currentSituationLabel")}
                </b>
                {session.theme.split(/\n{2,}/).map((para, i) => (
                  <p key={i} className="text-base geo-prose m-0">
                    {para.trim()}
                  </p>
                ))}
              </div>
            )}
          </div>
        </section>

        {/* 前提整理（intake）: Scenario未生成の間はここを表示する。ユーザー指示
            （2026-09-24、「1 設計」ページ改修）——担当者とAIの前提整理チャットで
            Analysis Contextを揃え、CTA押下で既存generate-scenariosをtheme_override付きで
            呼ぶ（Expert AI/CQAS呼び出し経路自体は無変更）。 */}
        {candidates.length === 0 ? (
          <div className="space-y-3">
            <GeoIosIntakeView
              sessionId={sessionId}
              actorId={actorId}
              sessionTitle={session.title}
              sessionTheme={session.theme}
              onHandoff={(themeOverride) =>
                generateScenarios.mutate({ actor_id: actorId, theme_override: themeOverride })
              }
            />
            <GeoExpertAiProgress active={generateScenarios.isPending} className="justify-center" />
            {generateScenarios.isError && (
              <p className="text-xs text-destructive text-center">
                {(generateScenarios.error as Error).message}
              </p>
            )}
          </div>
        ) : (
          <GeoIosSituationAnalysisView sessionId={sessionId} actorId={actorId} />
        )}
      </main>
    </>
  );
}
