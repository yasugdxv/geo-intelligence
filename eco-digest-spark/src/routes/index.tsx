import { createFileRoute, redirect } from "@tanstack/react-router";

// Geo専用アプリのため、"/"へのアクセスは常にGeoの入口へ流す。
export const Route = createFileRoute("/")({
  beforeLoad: () => {
    throw redirect({ to: "/geo" });
  },
});
