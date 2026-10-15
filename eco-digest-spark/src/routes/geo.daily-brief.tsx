import { createFileRoute } from "@tanstack/react-router";
import { DailyBriefPage } from "@/components/geo/daily-brief";

// Phase E: 毎朝「今日どこを見るべきか」を短時間で把握する入口画面（インテリジェンスグループの先頭）
export const Route = createFileRoute("/geo/daily-brief")({
  component: DailyBriefPage,
});
