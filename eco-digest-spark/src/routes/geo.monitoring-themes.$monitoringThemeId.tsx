import { createFileRoute } from "@tanstack/react-router";
import { MonitoringDetailPage } from "@/components/geo/monitoring-detail";

function DetailRoute() {
  const { monitoringThemeId } = Route.useParams();
  return <MonitoringDetailPage id={monitoringThemeId} />;
}

export const Route = createFileRoute("/geo/monitoring-themes/$monitoringThemeId")({
  component: DetailRoute,
});
