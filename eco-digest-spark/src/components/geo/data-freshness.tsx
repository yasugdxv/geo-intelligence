import { AlertTriangle, CheckCircle2, HelpCircle } from "lucide-react";
import { useLanguage } from "@/lib/i18n";
import type { DataFreshness } from "@/lib/geoSourceLibraryApi";
import { cn } from "@/lib/utils";

// Data Freshness Guard の表示。「記事が来ていない」を「安定」「変化なし」と誤読させないため、
// Stale/Unknown のときは必ず警告色＋注記を出す（判定自体はBackend: compute_freshness）。
export function DataFreshnessBadge({
  freshness,
  showNote = false,
  className,
}: {
  freshness: DataFreshness | undefined | null;
  showNote?: boolean;
  className?: string;
}) {
  const { t } = useLanguage();
  if (!freshness) return null;
  const { status } = freshness;
  const Icon = status === "fresh" ? CheckCircle2 : status === "stale" ? AlertTriangle : HelpCircle;
  const tone =
    status === "fresh"
      ? "border-geo-green/40 bg-geo-green-soft text-geo-green"
      : status === "stale"
        ? "border-geo-amber/50 bg-geo-amber-soft text-geo-amber"
        : "border-border bg-muted text-muted-foreground";
  return (
    <div className={cn("space-y-1", className)} data-testid="data-freshness" data-status={status}>
      <div className={cn("inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs", tone)}>
        <Icon className="h-3.5 w-3.5" />
        <span className="font-medium">{t(`geo.freshness.${status}`)}</span>
        {freshness.last_data_at && (
          <span className="opacity-80">
            · {t("geo.freshness.lastData", { date: freshness.last_data_at.slice(0, 10) })}
          </span>
        )}
      </div>
      {showNote && status !== "fresh" && (
        <p className="text-xs text-muted-foreground">{t("geo.freshness.noteStale")}</p>
      )}
    </div>
  );
}
