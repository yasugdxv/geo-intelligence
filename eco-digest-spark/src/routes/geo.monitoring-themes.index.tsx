import { createFileRoute } from "@tanstack/react-router";
import { MonitoringListPage } from "@/components/geo/monitoring-list";

// ユーザー向けモニタリング一覧（開発者向けの /geo/monitoring とは別物）
export const Route = createFileRoute("/geo/monitoring-themes/")({
  component: MonitoringListPage,
});
