import { Badge } from "@/components/ui/badge";
import type { GeoReportRepresentative, GeoReportSynthesisItem } from "@/lib/geoReportStudioApi";

/**
 * Report StudioのSynthesis項目（Finding/Driver/Counterforce/Implication/Watchpoint）を、
 * Observed Evidence / Expert Context / Counterevidence / Interpretationへ分けて表示する。
 *
 * これはReport Studioの核となる設計（geo_report_synthesis_service.pyのAnti-Backflow Guard、
 * provenance.evidence_basis）をUIへそのまま反映するためのコンポーネント。
 * 「AIが外部データから観測したこと」と「専門家AIが既存知識で解釈したこと」を
 * 混同させないことが目的なので、実データに無い区分（Actor/Media/Precedentの内訳等）を
 * 勝手に作らない——expert_context_refsは現状フラットなID配列でしか受け取れないため、
 * そのまま「Expert Context Reference」として列挙する。
 */

const EVIDENCE_BASIS_LABEL: Record<string, string> = {
  observed: "Observed", expert_context: "Expert Context", mixed: "Observed + Expert Context",
};
const EVIDENCE_BASIS_VARIANT: Record<string, "default" | "secondary" | "outline"> = {
  observed: "default", expert_context: "secondary", mixed: "outline",
};

export function GeoReportEvidenceBasisBadge({ evidenceBasis }: { evidenceBasis: string }) {
  return (
    <Badge variant={EVIDENCE_BASIS_VARIANT[evidenceBasis] ?? "outline"}>
      {EVIDENCE_BASIS_LABEL[evidenceBasis] ?? evidenceBasis}
    </Badge>
  );
}

// Continuity（Phase4）: assessment_delta.change_type / watchpoint_updates.status用の記号付きBadge。
// 件数の増減だけでなく意味的な変化区分を表すため、矢印記号で直感的に区別できるようにする。
const CHANGE_TYPE_SYMBOL: Record<string, string> = {
  Intensified: "↑", Weakened: "↓", Reframed: "↻", Stable: "↔",
  New: "＋", Resolved: "✓", Insufficient_evidence: "?",
};
const CHANGE_TYPE_VARIANT: Record<string, "default" | "secondary" | "outline"> = {
  Intensified: "default", Weakened: "outline", Reframed: "secondary", Stable: "outline",
  New: "default", Resolved: "secondary", Insufficient_evidence: "outline",
};

export function ChangeTypeBadge({ changeType }: { changeType: string }) {
  return (
    <Badge variant={CHANGE_TYPE_VARIANT[changeType] ?? "outline"} className="flex-shrink-0">
      {CHANGE_TYPE_SYMBOL[changeType] ?? ""} {changeType}
    </Badge>
  );
}

const WATCHPOINT_STATUS_VARIANT: Record<string, "default" | "secondary" | "outline"> = {
  Triggered: "default", Possible_trigger: "secondary", Not_observed: "outline", Evidence_insufficient: "outline",
};

export function WatchpointStatusBadge({ status }: { status: string }) {
  return (
    <Badge variant={WATCHPOINT_STATUS_VARIANT[status] ?? "outline"} className="flex-shrink-0">
      {status}
    </Badge>
  );
}

function ArticleRefList({ recordIds, recordLookup }: {
  recordIds: string[]; recordLookup: Record<string, GeoReportRepresentative>;
}) {
  if (recordIds.length === 0) return null;
  return (
    <ul className="space-y-1 pl-3">
      {recordIds.map((rid) => {
        const rep = recordLookup[rid];
        return (
          <li key={rid} className="text-xs">
            {rep ? (
              <>
                <span className="text-foreground">{rep.title || rep.record_id}</span>
                {rep.source_name && <span className="text-muted-foreground"> — {rep.source_name}</span>}
                {rep.published_at && <span className="text-muted-foreground"> ({rep.published_at})</span>}
              </>
            ) : (
              <span className="text-muted-foreground font-mono">{rid}</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function GeoReportProvenancePanel({ item, recordLookup, mainText, subLabel, subText }: {
  item: GeoReportSynthesisItem;
  recordLookup: Record<string, GeoReportRepresentative>;
  mainText: string;
  subLabel: string;
  subText: string;
}) {
  const provenance = item.provenance;
  const expertRefs = provenance.expert_context_refs ?? [];
  const counterevidence = (provenance as { counterevidence_record_ids?: string[] }).counterevidence_record_ids ?? [];

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <GeoReportEvidenceBasisBadge evidenceBasis={provenance.evidence_basis} />
        <Badge variant="outline">{item.confidence}</Badge>
      </div>

      <div>
        <div className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground mb-1">Assessment</div>
        <p className="text-sm">{mainText}</p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 border-t border-border pt-3">
        <div>
          <div className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground mb-1">Observed Evidence</div>
          {provenance.observed_record_ids.length > 0 ? (
            <ArticleRefList recordIds={provenance.observed_record_ids} recordLookup={recordLookup} />
          ) : (
            <p className="text-xs text-muted-foreground">なし</p>
          )}
        </div>

        <div>
          <div className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground mb-1">Expert Context</div>
          {expertRefs.length > 0 ? (
            <ul className="space-y-1 pl-3">
              {expertRefs.map((ref) => (
                <li key={ref} className="text-xs font-mono text-muted-foreground">{ref}</li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-muted-foreground">なし</p>
          )}
        </div>

        <div className="sm:col-span-2">
          <div className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground mb-1">Counterevidence</div>
          {counterevidence.length > 0 ? (
            <ArticleRefList recordIds={counterevidence} recordLookup={recordLookup} />
          ) : (
            <p className="text-xs text-muted-foreground">なし</p>
          )}
        </div>
      </div>

      <div className="border-t border-border pt-3">
        <div className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground mb-1">Interpretation</div>
        <div className="text-xs text-muted-foreground">
          <span className="font-medium text-foreground">{subLabel}: </span>{subText}
        </div>
      </div>
    </div>
  );
}
