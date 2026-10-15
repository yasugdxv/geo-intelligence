import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { TopBar } from "@/components/top-bar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { GEO_KNOWLEDGE_ROLES, GeoKnowledgeItem, useGeoKnowledgeCandidates, useReviewGeoKnowledgeItem } from "@/lib/geoKnowledgeApi";
import { useLanguage } from "@/lib/i18n";

export const Route = createFileRoute("/geo/knowledge-review")({
  component: GeoKnowledgeReviewPage,
});

const EXTERNAL_CANDIDATE_TYPES = ["actor_profile_update", "relationship_update", "dependency_update", "sanctions_update"];

function ExternalSourceInfo({ item }: { item: GeoKnowledgeItem }) {
  if (!EXTERNAL_CANDIDATE_TYPES.includes(item.knowledge_type)) return null;
  const content = item.structured_content as Record<string, string | undefined>;
  const fields: [string, string | undefined][] = [
    ["Source Metric/Claim", content.source_metric_or_claim],
    ["External Record ID (OFAC)", content.external_record_id],
    ["取得日時点", content.as_of_fetched_at],
  ].filter(([, v]) => !!v) as [string, string][];
  if (fields.length === 0) return null;
  return (
    <div className="text-xs bg-muted/40 rounded p-2 space-y-0.5">
      <p className="text-muted-foreground font-medium">外部データソース由来（Phase2A Connector）</p>
      {fields.map(([label, value]) => (
        <div key={label}><span className="text-muted-foreground">{label}:</span> {value}</div>
      ))}
    </div>
  );
}

function CandidateCard({ item }: { item: GeoKnowledgeItem }) {
  const { t } = useLanguage();
  const [reviewerId, setReviewerId] = useState("reviewer1");
  const [reviewerRole, setReviewerRole] = useState("expert_reviewer");
  const [comment, setComment] = useState("");
  const [evidenceAdequacy, setEvidenceAdequacy] = useState("partial");
  const reviewMutation = useReviewGeoKnowledgeItem();

  const decide = (decision: string) => {
    reviewMutation.mutate({
      knowledgeItemId: item.knowledge_item_id, reviewer_id: reviewerId,
      reviewer_role: reviewerRole as (typeof GEO_KNOWLEDGE_ROLES)[number],
      review_decision: decision, review_comment: comment, evidence_adequacy: evidenceAdequacy,
      applicability_checked: true,
    });
  };

  return (
    <div className="card-paper rounded-lg p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Badge variant="outline">{item.knowledge_type}</Badge>
        <Badge variant="secondary">{item.status}</Badge>
      </div>
      <div className="text-editorial text-lg">{item.title}</div>
      {item.summary && <p className="text-sm text-muted-foreground">{item.summary}</p>}
      <ExternalSourceInfo item={item} />
      <details className="text-xs">
        <summary className="cursor-pointer text-muted-foreground">structured_content</summary>
        <pre className="whitespace-pre-wrap mt-1">{JSON.stringify(item.structured_content, null, 2)}</pre>
      </details>

      <div className="grid grid-cols-2 gap-2">
        <Input value={reviewerId} onChange={(e) => setReviewerId(e.target.value)}
               placeholder={t("geo.review.reviewerIdLabel")} />
        <Select value={reviewerRole} onValueChange={setReviewerRole}>
          <SelectTrigger><SelectValue placeholder={t("geo.review.reviewerRoleLabel")} /></SelectTrigger>
          <SelectContent>
            {GEO_KNOWLEDGE_ROLES.map((r) => <SelectItem key={r} value={r}>{r}</SelectItem>)}
          </SelectContent>
        </Select>
      </div>
      <Select value={evidenceAdequacy} onValueChange={setEvidenceAdequacy}>
        <SelectTrigger className="w-48"><SelectValue placeholder={t("geo.review.evidenceAdequacyLabel")} /></SelectTrigger>
        <SelectContent>
          <SelectItem value="sufficient">sufficient</SelectItem>
          <SelectItem value="partial">partial</SelectItem>
          <SelectItem value="insufficient">insufficient</SelectItem>
        </SelectContent>
      </Select>
      <Textarea value={comment} onChange={(e) => setComment(e.target.value)}
                placeholder={t("geo.review.commentLabel")} className="min-h-[50px] text-sm" />

      <div className="flex flex-wrap gap-2">
        {item.status === "candidate" && (
          <Button size="sm" variant="outline" onClick={() => decide("start_review")}>
            {t("geo.review.startReview")}
          </Button>
        )}
        {item.status === "in_review" && (
          <>
            <Button size="sm" onClick={() => decide("approve_active")}>{t("geo.review.approveActive")}</Button>
            <Button size="sm" variant="secondary" onClick={() => decide("approve_contested")}>
              {t("geo.review.approveContested")}
            </Button>
            <Button size="sm" variant="destructive" onClick={() => decide("reject")}>
              {t("geo.review.reject")}
            </Button>
          </>
        )}
      </div>
      {reviewMutation.isError && (
        <p className="text-xs text-destructive">{(reviewMutation.error as Error).message}</p>
      )}
    </div>
  );
}

function GeoKnowledgeReviewPage() {
  const { t } = useLanguage();
  const candidatesQuery = useGeoKnowledgeCandidates();
  const candidates = candidatesQuery.data?.candidates ?? [];

  return (
    <>
      <TopBar breadcrumb={[{ label: t("nav.geoKnowledgeReview") }]} />
      <main className="flex-1 max-w-[900px] w-full mx-auto px-8 py-8 space-y-6">
        <section className="border-b border-border pb-5">
          <h1 className="text-editorial text-4xl">{t("geo.review.title")}</h1>
          <p className="text-sm text-muted-foreground mt-2">{t("geo.review.subtitle")}</p>
          <p className="text-[10px] text-muted-foreground mt-1">{t("geo.knowledge.roleWarning")}</p>
        </section>

        <div className="space-y-4">
          {candidates.map((item) => <CandidateCard key={item.knowledge_item_id} item={item} />)}
          {candidates.length === 0 && <p className="text-sm text-muted-foreground">{t("geo.review.empty")}</p>}
        </div>
      </main>
    </>
  );
}
