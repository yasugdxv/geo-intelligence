import { createFileRoute, redirect } from "@tanstack/react-router";

// Chatは/geo/intelligence-ai（Chatタブ）へ統合された（geo.intelligence-ai.tsx参照）。
// 旧URL(/geo/chat)を直接開いた場合の互換リダイレクトのみ残す。
export const Route = createFileRoute("/geo/chat")({
  beforeLoad: () => {
    throw redirect({ to: "/geo/intelligence-ai" });
  },
});
