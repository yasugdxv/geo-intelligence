import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { GeoCard, GeoCardBody, GeoCardHeader } from "@/components/geo/geo-card";
import type { GeoIosScenarioCandidate } from "@/lib/geoIosAnalysisSessionApi";

// Geopolitical Intelligence OS Phase H §5: CQAS Scenario CandidateのCard表示。
// title/analysis_question/why_this_matters/main_lens/key_actorsはgeo_ios_expert_ai_adapter
// （Phase C）が既存Expert AI応答から決定的に導出した値をそのまま表示するだけで、UI側で
// 新しいranking/生成ロジックは持たない（§37）。
export function GeoIosScenarioCard({
  candidate, selected, onSelect, isSelecting,
}: {
  candidate: GeoIosScenarioCandidate;
  selected: boolean;
  onSelect: () => void;
  isSelecting?: boolean;
}) {
  const content = candidate.content ?? {};
  return (
    <GeoCard className={selected ? "ring-2 ring-primary" : undefined}>
      <GeoCardHeader
        eyebrow={`#${candidate.rank}`}
        title={candidate.title}
        actions={selected ? <Badge>Selected</Badge> : undefined}
      />
      <GeoCardBody className="space-y-3">
        {content.why_this_matters && (
          <div>
            <div className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground mb-1">
              Why this matters
            </div>
            <p className="text-sm text-muted-foreground">{content.why_this_matters}</p>
          </div>
        )}
        {content.main_lens && (
          <div>
            <div className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground mb-1">
              Main lens
            </div>
            <p className="text-sm">{content.main_lens}</p>
          </div>
        )}
        {content.key_actors && content.key_actors.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {content.key_actors.map((actor) => (
              <Badge key={actor} variant="outline">{actor}</Badge>
            ))}
          </div>
        )}
        {content.confidence && (
          <p className="text-xs text-muted-foreground">Confidence: {content.confidence}</p>
        )}
        <Button
          size="sm"
          variant={selected ? "secondary" : "default"}
          className="w-full"
          disabled={isSelecting}
          onClick={onSelect}
        >
          {selected ? "Selected" : "Use this approach"}
        </Button>
      </GeoCardBody>
    </GeoCard>
  );
}
