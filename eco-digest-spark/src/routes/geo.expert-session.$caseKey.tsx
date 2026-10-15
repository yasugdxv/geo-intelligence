import { createFileRoute } from "@tanstack/react-router";
import { TopBar } from "@/components/top-bar";
import { GeoExpertSessionCaseView } from "@/components/geo/geo-expert-session-case-view";
import { useLanguage } from "@/lib/i18n";

// Expert Session Capture V1（Phase S2-S3、2026-10-02）: Gold/Calibration Case Flow。
export const Route = createFileRoute("/geo/expert-session/$caseKey")({
  component: GeoExpertSessionCasePage,
});

function GeoExpertSessionCasePage() {
  const { t } = useLanguage();
  const { caseKey } = Route.useParams();
  return (
    <>
      <TopBar breadcrumb={[{ label: t("geo.expertSession.homeTitle") }, { label: caseKey }]} />
      <main className="flex-1 max-w-[1100px] w-full mx-auto px-8 py-8">
        <GeoExpertSessionCaseView caseKey={caseKey} />
      </main>
    </>
  );
}
