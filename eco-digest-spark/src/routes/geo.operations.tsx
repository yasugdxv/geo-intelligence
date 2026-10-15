import { createFileRoute } from "@tanstack/react-router";
import { OperationsPage } from "@/components/geo/operations-dashboard";

// Phase C: ユーザー向けの運用画面（開発者向けの /geo/monitoring・/geo/news-signals とは別物）
export const Route = createFileRoute("/geo/operations")({
  component: OperationsPage,
});
