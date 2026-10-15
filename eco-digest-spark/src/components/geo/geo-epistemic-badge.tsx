import { Badge } from "@/components/ui/badge";
import { useLanguage } from "@/lib/i18n";

// Phase2B-2: Epistemic Type（information_class）とRetrieval Statusを一目で判別できるようにする
// Badge。既存geo-freshness-badge.tsxと同じ設計思想（ローカルなラベル対応表＋Badge variant）。

const EPISTEMIC_LABEL_JA: Record<string, string> = {
  observed_fact: "観測・記録された事実情報",
  actor_claim: "Actor Claim",
  external_assessment: "External Assessment",
  internal_hypothesis: "Internal Hypothesis",
  internal_expert_assessment: "Internal Expert Assessment",
};
const EPISTEMIC_LABEL_EN: Record<string, string> = {
  observed_fact: "Observed Fact",
  actor_claim: "Actor Claim",
  external_assessment: "External Assessment",
  internal_hypothesis: "Internal Hypothesis",
  internal_expert_assessment: "Internal Expert Assessment",
};
const EPISTEMIC_VARIANT: Record<string, "default" | "secondary" | "outline"> = {
  observed_fact: "secondary",
  actor_claim: "outline",
  external_assessment: "outline",
  internal_hypothesis: "secondary",
  internal_expert_assessment: "default",
};

export function EpistemicTypeBadge({ informationClass }: { informationClass: string }) {
  const { lang } = useLanguage();
  const label = (lang === "en" ? EPISTEMIC_LABEL_EN : EPISTEMIC_LABEL_JA)[informationClass] ?? informationClass;
  return <Badge variant={EPISTEMIC_VARIANT[informationClass] ?? "outline"}>{label}</Badge>;
}

const RETRIEVAL_STATUS_LABEL_JA: Record<string, string> = {
  available: "参照可能", restricted: "参照制限中", deprecated: "非推奨", archived: "アーカイブ済み",
};
const RETRIEVAL_STATUS_LABEL_EN: Record<string, string> = {
  available: "Available", restricted: "Restricted", deprecated: "Deprecated", archived: "Archived",
};
const RETRIEVAL_STATUS_VARIANT: Record<string, "secondary" | "outline" | "destructive"> = {
  available: "secondary", restricted: "outline", deprecated: "outline", archived: "destructive",
};

export function RetrievalStatusBadge({ retrievalStatus }: { retrievalStatus: string }) {
  const { lang } = useLanguage();
  const label = (lang === "en" ? RETRIEVAL_STATUS_LABEL_EN : RETRIEVAL_STATUS_LABEL_JA)[retrievalStatus]
    ?? retrievalStatus;
  return (
    <Badge variant={RETRIEVAL_STATUS_VARIANT[retrievalStatus] ?? "outline"}
           title="AIが参照してよいかを管理する運用フラグ（内容の正誤判定ではありません）">
      {label}
    </Badge>
  );
}

// Source/Perspective/観測日を併記するExternal Assessment用の補助表示（Badgeというより短い注記）
export function SourcePerspectiveNote({ sourceName, perspective, observedAt }: {
  sourceName?: string | null; perspective?: string | null; observedAt?: string | null;
}) {
  if (!sourceName && !perspective && !observedAt) return null;
  const parts = [sourceName, perspective ? `（${perspective}）` : null, observedAt].filter(Boolean);
  return <span className="text-[10px] text-muted-foreground ml-1">{parts.join(" ")}</span>;
}
