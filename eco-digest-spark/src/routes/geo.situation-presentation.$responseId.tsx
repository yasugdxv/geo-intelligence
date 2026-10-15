import { createFileRoute } from "@tanstack/react-router";
import { TopBar } from "@/components/top-bar";
import { GeoSituationPresentationView } from "@/components/geo/geo-situation-presentation-view";

// Reading Presentation Layer v1（ユーザー指示）: response_id直接指定でGeoExpertAI応答の
// 「読み方」を表示する読み取り専用ページ。IOS Analysis Session（/geo/ios/analysis/
// $sessionId）を経由しないGold Case評価等の確認用。新しい推論・書き込みは行わない。
export const Route = createFileRoute("/geo/situation-presentation/$responseId")({
  component: GeoSituationPresentationPage,
});

function GeoSituationPresentationPage() {
  const { responseId } = Route.useParams();
  return (
    <div className="min-h-screen bg-background">
      <TopBar />
      <main className="mx-auto max-w-[1100px] px-4 py-8">
        <h1 className="text-lg font-bold text-[#172d3d] mb-1">局面を読む（Gold Case確認用）</h1>
        {/* 内部IDは通常表示しない（2026-10-04、読み方UI改善の受け入れ基準）。確認作業用に
            URLへ?debug=1を付けた時だけ表示する。 */}
        {typeof window !== "undefined" &&
        new URLSearchParams(window.location.search).get("debug") === "1" ? (
          <p className="text-xs text-muted-foreground mb-6">response_id: {responseId}</p>
        ) : (
          <div className="mb-6" />
        )}
        <GeoSituationPresentationView responseId={responseId} />
      </main>
    </div>
  );
}
