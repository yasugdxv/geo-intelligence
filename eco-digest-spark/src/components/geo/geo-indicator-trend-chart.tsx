import { Area, AreaChart, ResponsiveContainer, Tooltip, YAxis } from "recharts";
import { GeoObservation } from "@/lib/api";
import { useLanguage } from "@/lib/i18n";

// 単一系列のスパークライン的なトレンド表示。「1軸・1色相・薄いマーク・控えめな軸」の
// 方針に沿い、凡例は付けない（系列が1本のため見出しがそれを兼ねる）。
function TrendTooltip({ active, payload }: { active?: boolean; payload?: { payload: { asOf: string; value: number | null; label?: string | null } }[] }) {
  const { lang } = useLanguage();
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  const date = new Date(point.asOf).toLocaleDateString(lang === "en" ? "en-US" : "ja-JP");
  return (
    <div className="rounded-md border border-border bg-card px-2.5 py-1.5 text-xs shadow-sm">
      <div className="text-muted-foreground">{date}</div>
      <div className="font-medium tabular-nums">{point.label ?? point.value}</div>
    </div>
  );
}

export function GeoIndicatorTrendChart({ observations }: { observations: GeoObservation[] }) {
  const points = [...observations]
    .filter((o) => !o.insufficientData && o.rawValue !== null)
    .reverse() // APIはas_of.desc（新しい順）で返るため、時系列(古→新)に並べ替える
    .map((o) => ({ asOf: o.asOf, value: o.rawValue as number, label: o.qualitativeLabel }));

  if (points.length < 2) return null;

  return (
    <div className="h-14 -mx-1">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={points} margin={{ top: 4, right: 4, bottom: 0, left: 4 }}>
          <defs>
            <linearGradient id="geoTrendFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="oklch(0.62 0.15 145)" stopOpacity={0.35} />
              <stop offset="100%" stopColor="oklch(0.62 0.15 145)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <YAxis hide domain={["dataMin", "dataMax"]} />
          <Tooltip content={<TrendTooltip />} cursor={{ stroke: "oklch(0.87 0.01 90)", strokeWidth: 1 }} />
          <Area
            type="monotone"
            dataKey="value"
            stroke="oklch(0.62 0.15 145)"
            strokeWidth={2}
            fill="url(#geoTrendFill)"
            dot={false}
            activeDot={{ r: 3.5 }}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
