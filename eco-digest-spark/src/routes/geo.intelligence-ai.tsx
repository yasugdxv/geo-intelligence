import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { TopBar } from "@/components/top-bar";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { GeoIntelligenceChatTab } from "@/components/geo/intelligence-chat-tab";
import { GeoCard, GeoCardBody, GeoCardHeader } from "@/components/geo/geo-card";
import { GeoIntelligenceAiSearchTab } from "@/components/geo/geo-intelligence-ai-search";
import { useLanguage } from "@/lib/i18n";
import { GeoExpertResult, useGeoExpertRequest, useGeoExpertRequests } from "@/lib/api";
import { Sparkles } from "lucide-react";

export const Route = createFileRoute("/geo/intelligence-ai")({
  component: GeoIntelligenceAiPage,
});

// ─── AI Requestsタブ ────────────────────────────────────────────────────
// useGeoExpertRequests()は軽量一覧(response本文なし)のみを返すため、選択された
// requestIdについてだけuseGeoExpertRequest()で完全なレスポンス(qualityEvaluation等)を
// 取得する2段構え（一覧全件ぶんのHookループにはしない）。
function RequestDetail({ requestId }: { requestId: string }) {
  const { data, isLoading } = useGeoExpertRequest(requestId);
  if (isLoading) return <p className="text-sm text-muted-foreground">...</p>;
  if (!data) return <p className="text-sm text-muted-foreground">—</p>;

  const r: GeoExpertResult = data;
  return (
    <div className="space-y-5">
      <div>
        <div className="text-[11px] uppercase tracking-[0.08em] text-muted-foreground mb-1">
          {r.mode} · {r.createdAt.slice(0, 16).replace("T", " ")}
        </div>
        <p className="text-sm text-foreground">{r.queryText}</p>
      </div>

      <div>
        <div className="text-[11px] uppercase tracking-[0.08em] text-muted-foreground mb-1">
          One-line Assessment
        </div>
        <p className="text-sm text-foreground">{r.response.one_line_assessment}</p>
      </div>

      <div>
        <div className="text-[11px] uppercase tracking-[0.08em] text-muted-foreground mb-1">
          Main Scenario
        </div>
        <p className="text-sm text-foreground">{r.response.main_scenario.description}</p>
        <p className="text-xs text-muted-foreground mt-0.5">
          Confidence: {r.response.main_scenario.confidence}
        </p>
      </div>

      {/* Quality Guard: このリクエストが実際にどう処理・検証されたかを示す唯一の実データ
          （retrieval-runはcase紐付けのExpert Engine専用でありこの軽量版リクエストには
          存在しないため、代わりにqualityEvaluationを「処理の可視化」として使う） */}
      <div>
        <div className="text-[11px] uppercase tracking-[0.08em] text-muted-foreground mb-1.5">
          Quality Guard ({r.qualityEvaluation.passedCount}/{r.qualityEvaluation.checks.length})
        </div>
        <ul className="space-y-1">
          {r.qualityEvaluation.checks.map((c) => (
            <li key={c.name} className="flex items-start gap-2 text-xs">
              <span className={c.passed ? "text-geo-green" : "text-geo-red"}>
                {c.passed ? "✓" : "✕"}
              </span>
              <span className="text-muted-foreground">
                {c.name}: {c.detail}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function RequestsTab() {
  const { data, isLoading } = useGeoExpertRequests();
  const requests = [...(data?.requests ?? [])].sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1));
  const [selected, setSelected] = useState<string | null>(null);
  const selectedId = selected ?? requests[0]?.requestId ?? null;

  return (
    <div className="grid grid-cols-[360px_1fr] gap-6 items-start">
      <GeoCard>
        <GeoCardHeader title="AI Requests" meta={`${requests.length} total`} />
        <GeoCardBody className="p-0">
          {isLoading ? (
            <p className="text-sm text-muted-foreground px-5 pb-4">...</p>
          ) : requests.length === 0 ? (
            <p className="text-sm text-muted-foreground px-5 pb-4">—</p>
          ) : (
            <div className="max-h-[560px] overflow-y-auto">
              {requests.map((r) => (
                <button
                  key={r.requestId}
                  onClick={() => setSelected(r.requestId)}
                  className={`w-full text-left px-5 py-3 border-t border-border first:border-t-0 hover:bg-accent/40 transition-colors ${
                    selectedId === r.requestId ? "bg-accent/50" : ""
                  }`}
                >
                  <div className="text-[10px] text-muted-foreground uppercase">{r.mode}</div>
                  <p className="text-sm line-clamp-2 mt-0.5">{r.queryText}</p>
                  <div className="text-[10px] text-muted-foreground mt-1">
                    {r.createdAt.slice(0, 10)}
                  </div>
                </button>
              ))}
            </div>
          )}
        </GeoCardBody>
      </GeoCard>

      <GeoCard>
        <GeoCardHeader title="Response" />
        <GeoCardBody>
          {selectedId ? (
            <RequestDetail requestId={selectedId} />
          ) : (
            <p className="text-sm text-muted-foreground">—</p>
          )}
        </GeoCardBody>
      </GeoCard>
    </div>
  );
}

function GeoIntelligenceAiPage() {
  const { t } = useLanguage();
  return (
    <>
      <TopBar breadcrumb={[{ label: t("nav.geoIntelligenceAI") }]} />
      <main className="flex-1 max-w-[1440px] w-full mx-auto px-8 py-8 space-y-6">
        <section className="border-b border-border pb-5 flex items-center gap-2.5">
          <Sparkles className="h-5 w-5 text-geo-teal" />
          <h1 className="geo-heading text-4xl">{t("nav.geoIntelligenceAI")}</h1>
        </section>

        <Tabs defaultValue="chat">
          <TabsList>
            <TabsTrigger value="chat">Chat</TabsTrigger>
            <TabsTrigger value="search">{t("geo.intelligenceAiSearch.tab")}</TabsTrigger>
            <TabsTrigger value="requests">AI Requests</TabsTrigger>
          </TabsList>
          <TabsContent value="chat">
            <GeoIntelligenceChatTab />
          </TabsContent>
          <TabsContent value="search">
            <GeoIntelligenceAiSearchTab />
          </TabsContent>
          <TabsContent value="requests">
            <RequestsTab />
          </TabsContent>
        </Tabs>
      </main>
    </>
  );
}
