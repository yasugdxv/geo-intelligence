import { Link } from "@tanstack/react-router";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export interface GeoTimelineOrigin {
  label: string;
  variant?: "default" | "secondary" | "outline";
  /** 遷移先（Report Studio project detail等）。省略時はBadgeのみ表示しリンクにしない。 */
  link?: {
    to: string;
    params?: Record<string, string>;
    search?: Record<string, string>;
  };
}

export interface GeoTimelineEventData {
  id: string;
  date: string;
  title: string;
  description?: string;
  emphasis?: boolean;
  /** 発生源（Report Studio等）を示すBadge。組織内で発生した実世界イベントとの区別に使う。 */
  origin?: GeoTimelineOrigin;
}

/**
 * Timeline — 実世界のGeo Event専用の時系列表示（縦並び）。
 * システム上のAudit Log（分析・ナレッジ更新等）は混在させず、呼び出し側で別セクションに
 * 分けて表示すること（Event＝現実の出来事、Audit Log＝システム上の更新、を区別する）。
 */
export function GeoTimeline({ events }: { events: GeoTimelineEventData[] }) {
  if (events.length === 0) {
    return <p className="text-sm text-muted-foreground">—</p>;
  }
  return (
    <ol className="relative border-l border-border ml-1.5 space-y-4">
      {events.map((e) => (
        <li key={e.id} className="pl-4 relative">
          <span
            className={cn(
              "absolute -left-[5px] top-1.5 h-2 w-2 rounded-full",
              e.emphasis ? "bg-geo-teal" : "bg-muted-foreground/40",
            )}
          />
          <div className="text-[11px] text-muted-foreground">{e.date}</div>
          <div className="flex items-center gap-2 flex-wrap">
            <div className="text-sm text-foreground font-medium">{e.title}</div>
            {e.origin && (
              e.origin.link ? (
                <Link to={e.origin.link.to} params={e.origin.link.params} search={e.origin.link.search}>
                  <Badge variant={e.origin.variant ?? "outline"} className="cursor-pointer hover:bg-muted">
                    {e.origin.label}
                  </Badge>
                </Link>
              ) : (
                <Badge variant={e.origin.variant ?? "outline"}>{e.origin.label}</Badge>
              )
            )}
          </div>
          {e.description && (
            <p className="text-xs text-muted-foreground mt-0.5">{e.description}</p>
          )}
        </li>
      ))}
    </ol>
  );
}
