import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { TopBar } from "@/components/top-bar";
import { GeoStatTile } from "@/components/geo/geo-stat-tile";
import { GeoCard, GeoCardBody } from "@/components/geo/geo-card";
import { useCqasSignalSummary } from "@/lib/geoIosLearningApi";
import { useLanguage } from "@/lib/i18n";

// Geopolitical Intelligence OS Phase H §30-31: CQAS Learning Signals集計 + Drilldown。
// CQAS ranking algorithm自体は変更しない（§41絶対条件）——記録済みSignal（Phase G）の
// 集計・可視化のみ。
export const Route = createFileRoute("/geo/ios/cqas-signals")({
  component: GeoIosCqasSignalsPage,
});

function GeoIosCqasSignalsPage() {
  const { t } = useLanguage();
  const [filterSignal, setFilterSignal] = useState<string | null>(null);
  const summaryQuery = useCqasSignalSummary();
  const summary = summaryQuery.data;

  const rows = filterSignal ? (summary?.drilldown ?? []).filter((d) => d.signal === filterSignal) : (summary?.drilldown ?? []);

  return (
    <>
      <TopBar breadcrumb={[{ label: t("nav.geoCqasSignals") }]} />
      <main className="flex-1 max-w-[1100px] w-full mx-auto px-8 py-8 space-y-6">
        <section className="border-b border-border pb-5">
          <h1 className="geo-heading text-3xl">{t("geo.ios.cqasSignals.title")}</h1>
          <p className="text-sm text-muted-foreground mt-2">{t("geo.ios.cqasSignals.subtitle")}</p>
        </section>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {(["generation_miss", "ranking_miss", "selection_miss", "validated_candidate"] as const).map((signal) => (
            <button key={signal} onClick={() => setFilterSignal(filterSignal === signal ? null : signal)} className="text-left">
              <GeoStatTile label={signal} value={summary?.counts[signal] ?? 0} />
            </button>
          ))}
        </div>

        <GeoCard>
          <GeoCardBody className="pt-5 space-y-2">
            {rows.map((row) => (
              <div key={row.candidate_id} className="flex items-center justify-between border-b border-border pb-2 last:border-0 text-sm">
                <div className="min-w-0">
                  <p className="truncate">{row.generalized_rule}</p>
                  <p className="text-xs text-muted-foreground">
                    {row.candidate_type} · {row.status} · similarity {row.similarity != null ? row.similarity.toFixed(2) : "-"}
                  </p>
                </div>
              </div>
            ))}
            {rows.length === 0 && <p className="text-xs text-muted-foreground">-</p>}
          </GeoCardBody>
        </GeoCard>
      </main>
    </>
  );
}
