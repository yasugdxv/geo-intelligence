import { createFileRoute } from "@tanstack/react-router";
import { TopBar } from "@/components/top-bar";
import { GeoIosDraftComposeView } from "@/components/geo/geo-ios-draft-compose-view";
import { useAnalysisSession } from "@/lib/geoIosAnalysisSessionApi";
import { useLanguage } from "@/lib/i18n";

// Geopolitical Intelligence OS: レビューフロー（Phase 2C、IA再編）。
//
// Phase 2C以前は、ここでStep1取込→既存レポートの読み(Reading)を再構成→代替読みを
// 生成→ユーザーがどれかを選択→Revision Strategy確定→リバイズ版Draftを生成、という
// 「レポートの読み方を作り直す」フローになっていた（geo_ios_review_import_service.py・
// GeoIosReviewImportView・GeoIosSituationAnalysisView(mode="review")・
// RevisionStrategyPanel・ReviseSectionは全て現存、削除していない——単にこのrouteの
// render対象から外しただけ）。
//
// ユーザー指示（Phase 2C仕様）: 「レビューフローは完成済みレポートをアップロードして
// レビューする独立機能であり、新しい地政学Readingをゼロから作ることが目的ではない」
// 「Draft生成はレビューフローに含めない」。既存の読み直し・リバイズ機能は、レビュー対象
// レポートをそのままレビューするという目的には合わないため、このrouteでは使わない。
//
// 代わりに、GeoIosDraftComposeViewの既存「アップロード」経路（source="uploaded"、
// geo_ios_report_draft_service.py、Expert AI Engineを経由しない——監査で確認済み）を
// variant="review-intake"でそのまま再利用する。Session作成(mode="review")→ここで
// アップロード→AI Review→「専門家レビューへ進む」（/geo/ios/review/$reportProjectId、
// Round/Anchor付き専門家レビュー・承認・ストックへ）という直線フローになる。
export const Route = createFileRoute("/geo/ios/review-mode/$sessionId")({
  component: GeoIosReviewSessionPage,
});

const ACTOR_ID_KEY = "geo_ios_actor_id";

function GeoIosReviewSessionPage() {
  const { sessionId } = Route.useParams();
  const { t } = useLanguage();
  const sessionQuery = useAnalysisSession(sessionId);
  const actorId =
    typeof window !== "undefined" ? (localStorage.getItem(ACTOR_ID_KEY) ?? "analyst") : "analyst";

  const session = sessionQuery.data;

  if (!session) {
    return (
      <>
        <TopBar
          breadcrumb={[
            { label: t("nav.geoReviewFlow"), href: "/geo/ios/review-intake" },
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

  return (
    <>
      <TopBar
        breadcrumb={[
          { label: t("nav.geoReviewFlow"), href: "/geo/ios/review-intake" },
          { label: session.title },
        ]}
      />
      <main className="flex-1 max-w-[1000px] w-full mx-auto px-8 py-8 space-y-6">
        <section className="border-b border-border pb-5">
          <p className="m-0 text-[12px] font-bold tracking-[0.08em] text-[#0d879a] uppercase">
            {t("geo.ios.review.stepTitle")}
          </p>
          <h1 className="geo-heading text-3xl font-semibold m-0">{session.title}</h1>
        </section>

        <GeoIosDraftComposeView
          sessionId={sessionId}
          actorId={actorId}
          linkedReportProjectId={session.linked_report_project_id}
          variant="review-intake"
        />
      </main>
    </>
  );
}
