import { createFileRoute, redirect } from "@tanstack/react-router";

// Monitoringページは/geo/monitoringへリネームされた（geo.monitoring.tsx参照）。
// 旧URL(/geo/situations)を直接開いた場合の互換リダイレクトのみ残す。
export const Route = createFileRoute("/geo/situations")({
  beforeLoad: () => {
    throw redirect({ to: "/geo/monitoring" });
  },
});
