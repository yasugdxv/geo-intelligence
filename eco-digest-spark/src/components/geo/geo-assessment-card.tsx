import type { GeoExpertResponseJson } from "@/lib/api";
import { GeoCard, GeoCardBody, GeoCardHeader } from "@/components/geo/geo-card";
import { GeoStatusBadge } from "@/components/geo/geo-badge";

/**
 * AssessmentCard — Expert AIの実レスポンス(GeoExpertResponseJson)から構成する
 * 「Latest Assessment」カード。存在するフィールドのみ表示し、無いフィールド
 * (dynamics/alternative_scenariosは任意)は推定値で埋めずセルごと省略する。
 */
export function GeoAssessmentCard({
  response,
  sourceLabel,
  className,
}: {
  response: GeoExpertResponseJson;
  /** 「Expert AIケースX（日付Y）からの最新分析」のような出所表示 */
  sourceLabel?: string;
  className?: string;
}) {
  return (
    <GeoCard className={className}>
      <GeoCardHeader
        eyebrow={sourceLabel}
        title={response.one_line_assessment}
        actions={
          <GeoStatusBadge severity="moderate">{response.main_scenario.confidence}</GeoStatusBadge>
        }
      />
      <GeoCardBody className="grid gap-4 sm:grid-cols-2">
        <div>
          <div className="text-[11px] uppercase tracking-[0.08em] text-muted-foreground mb-1">
            Main Scenario
          </div>
          <p className="text-sm text-foreground">{response.main_scenario.description}</p>
        </div>
        {response.dynamics && (
          <div>
            <div className="text-[11px] uppercase tracking-[0.08em] text-muted-foreground mb-1">
              Dynamics
            </div>
            <p className="text-sm text-foreground">{response.dynamics}</p>
          </div>
        )}
      </GeoCardBody>
    </GeoCard>
  );
}
