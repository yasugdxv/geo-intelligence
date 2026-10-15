import { Link } from "@tanstack/react-router";
import { Badge } from "@/components/ui/badge";
import { GeoCard, GeoCardBody, GeoCardHeader } from "@/components/geo/geo-card";
import type { GeoIosLearningCandidate } from "@/lib/geoIosLearningApi";

// Geopolitical Intelligence OS Phase H §26: Learning Candidate Inbox Card。
// Phase Gが抽出したCandidateのフィールドをそのまま表示するだけで、UI側で新しい判定・
// 一般化ロジックは持たない（§37）。
const STATUS_VARIANT: Record<string, "default" | "secondary" | "outline" | "destructive"> = {
  candidate: "outline", approved: "default", rejected: "destructive", superseded: "secondary",
};

export function GeoIosLearningCandidateCard({ candidate }: { candidate: GeoIosLearningCandidate }) {
  const tc = candidate.trigger_context ?? {};
  return (
    <Link to="/geo/ios/learning-candidates/$candidateId" params={{ candidateId: candidate.candidate_id }}>
      <GeoCard className="hover:border-primary/40 transition-colors">
        <GeoCardHeader
          eyebrow={candidate.candidate_type}
          title={tc.title || candidate.generalized_rule.slice(0, 80)}
          actions={<Badge variant={STATUS_VARIANT[candidate.status] ?? "outline"}>{candidate.status}</Badge>}
        />
        <GeoCardBody className="space-y-2">
          <p className="text-sm text-muted-foreground line-clamp-2">{candidate.generalized_rule}</p>
          <div className="flex flex-wrap gap-1.5 text-xs">
            {candidate.scope && <Badge variant="outline">{candidate.scope}</Badge>}
            {tc.temporal_stability && <Badge variant="outline">{tc.temporal_stability}</Badge>}
            {candidate.confidence && <Badge variant="outline">confidence: {candidate.confidence}</Badge>}
            {tc.dedup_status && tc.dedup_status !== "new" && (
              <Badge variant="secondary">{tc.dedup_status}</Badge>
            )}
            {tc.cqas_signal?.signal && <Badge variant="outline">CQAS: {tc.cqas_signal.signal}</Badge>}
          </div>
        </GeoCardBody>
      </GeoCard>
    </Link>
  );
}
