import { createFileRoute, Link, Outlet, useRouterState } from "@tanstack/react-router";
import { useState } from "react";
import { TopBar } from "@/components/top-bar";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { GeoStatusBadge, PRIORITY_SEVERITY } from "@/components/geo/geo-badge";
import { GeoStatTile } from "@/components/geo/geo-stat-tile";
import { GeoIntelligenceNeed, useIntelligenceNeeds } from "@/lib/geoIntelligenceNeedsApi";
import { useLanguage } from "@/lib/i18n";

export const Route = createFileRoute("/geo/intelligence-needs")({
  component: GeoIntelligenceNeedsPage,
});

const STATUS_OPTIONS = [
  "all",
  "open",
  "researching",
  "resolved",
  "dismissed",
  "duplicate_candidate",
] as const;

function NeedRow({ need }: { need: GeoIntelligenceNeed }) {
  return (
    <Link
      to="/geo/intelligence-needs/$id"
      params={{ id: need.gap_id }}
      className="grid grid-cols-[80px_120px_110px_1fr_80px_130px_100px] gap-3 items-center px-4 py-3 border-b border-border last:border-0 hover:bg-accent/30 transition-colors text-sm"
    >
      <GeoStatusBadge severity={PRIORITY_SEVERITY[need.priority] ?? "neutral"}>{need.priority}</GeoStatusBadge>
      <span className="text-muted-foreground truncate">{need.requested_by_app ?? "-"}</span>
      <span className="truncate">{need.country_region.join(", ") || "-"}</span>
      <span className="truncate">{need.question ?? "-"}</span>
      <span className="text-muted-foreground">×{need.request_count}</span>
      <GeoStatusBadge severity="neutral">{need.status}</GeoStatusBadge>
      <span className="text-muted-foreground truncate">{need.assigned_to ?? "未アサイン"}</span>
    </Link>
  );
}

function GeoIntelligenceNeedsPage() {
  const { t } = useLanguage();
  const [status, setStatus] = useState<string>("all");
  const needsQuery = useIntelligenceNeeds(status === "all" ? undefined : status);
  const needs = needsQuery.data?.needs ?? [];
  const pathname = useRouterState({ select: (r) => r.location.pathname });

  // /geo/intelligence-needs/$id（詳細ページ）は本ファイルの子ルートとして登録されるため、
  // 詳細ページ表示中は一覧を出さずOutletのみ描画する（一覧と詳細が二重に表示される、
  // またはクリックしても詳細に切り替わらないのを防ぐ。competitors.companies.tsxと同じ対処）。
  if (pathname !== "/geo/intelligence-needs") {
    return <Outlet />;
  }

  // サマリー統計はクライアント側で全件から集計する（新規エンドポイントは追加しない）
  const openCount = needs.filter((n) => n.status === "open").length;
  const highPriorityCount = needs.filter((n) => n.priority === "high").length;
  const aiGapCount = needs.filter((n) => n.origin_type !== "human").length;
  const resolvedCount = needs.filter((n) => n.status === "resolved").length;

  return (
    <>
      <TopBar breadcrumb={[{ label: t("nav.geoIntelligenceNeeds") }]} />
      <main className="flex-1 max-w-[1100px] w-full mx-auto px-8 py-8 space-y-6">
        <section className="border-b border-border pb-5 flex items-center justify-between">
          <div>
            <h1 className="geo-heading text-4xl">{t("geo.intelligenceNeeds.title")}</h1>
            <p className="text-sm text-muted-foreground mt-2">
              {t("geo.intelligenceNeeds.subtitle")}
            </p>
          </div>
          <Select value={status} onValueChange={setStatus}>
            <SelectTrigger className="w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STATUS_OPTIONS.map((s) => (
                <SelectItem key={s} value={s}>
                  {s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </section>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <GeoStatTile label="Open" value={openCount} />
          <GeoStatTile label="High Priority" value={highPriorityCount} />
          <GeoStatTile label="AI-detected Gaps" value={aiGapCount} />
          <GeoStatTile label="Resolved" value={resolvedCount} />
        </div>

        <div className="geo-card overflow-hidden">
          <div className="grid grid-cols-[80px_120px_110px_1fr_80px_130px_100px] gap-3 px-4 py-2.5 text-xs text-muted-foreground font-medium border-b border-border bg-muted/40">
            <span>Priority</span>
            <span>Requested By</span>
            <span>Country/Region</span>
            <span>Intelligence Need</span>
            <span>Requests</span>
            <span>Status</span>
            <span>Assigned To</span>
          </div>

          {needs.map((need) => (
            <NeedRow key={need.gap_id} need={need} />
          ))}
          {needs.length === 0 && (
            <p className="text-sm text-muted-foreground px-4 py-6">{t("geo.intelligenceNeeds.empty")}</p>
          )}
        </div>
      </main>
    </>
  );
}
