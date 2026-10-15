import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { TopBar } from "@/components/top-bar";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { GeoIosLearningCandidateCard } from "@/components/geo/geo-ios-learning-candidate-card";
import { useLearningCandidates } from "@/lib/geoIosLearningApi";
import { useLanguage } from "@/lib/i18n";

// Geopolitical Intelligence OS Phase H §26: Learning Candidate Inbox一覧。
export const Route = createFileRoute("/geo/ios/learning-candidates")({
  component: GeoIosLearningCandidatesPage,
});

const STATUS_OPTIONS = ["candidate", "approved", "rejected", "superseded", "all"] as const;

function GeoIosLearningCandidatesPage() {
  const { t } = useLanguage();
  const [status, setStatus] = useState<string>("candidate");
  const candidatesQuery = useLearningCandidates(status === "all" ? undefined : status);
  const candidates = candidatesQuery.data?.candidates ?? [];

  return (
    <>
      <TopBar breadcrumb={[{ label: t("nav.geoLearningCandidates") }]} />
      <main className="flex-1 max-w-[1100px] w-full mx-auto px-8 py-8 space-y-6">
        <section className="border-b border-border pb-5 flex items-center justify-between">
          <div>
            <h1 className="geo-heading text-3xl">{t("geo.ios.learningCandidates.title")}</h1>
            <p className="text-sm text-muted-foreground mt-2">{t("geo.ios.learningCandidates.subtitle")}</p>
          </div>
          <Select value={status} onValueChange={setStatus}>
            <SelectTrigger className="w-44"><SelectValue /></SelectTrigger>
            <SelectContent>
              {STATUS_OPTIONS.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
            </SelectContent>
          </Select>
        </section>
        <div className="grid gap-3 sm:grid-cols-2">
          {candidates.map((c) => <GeoIosLearningCandidateCard key={c.candidate_id} candidate={c} />)}
          {candidates.length === 0 && (
            <p className="text-sm text-muted-foreground">{t("geo.ios.learningCandidates.empty")}</p>
          )}
        </div>
      </main>
    </>
  );
}
