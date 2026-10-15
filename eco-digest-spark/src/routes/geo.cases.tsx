import { createFileRoute, Link, Outlet, useRouterState } from "@tanstack/react-router";
import { useState } from "react";
import { TopBar } from "@/components/top-bar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useCreateGeoCase, useGeoCases } from "@/lib/geoKnowledgeApi";
import { useLanguage } from "@/lib/i18n";

export const Route = createFileRoute("/geo/cases")({
  component: GeoCasesPage,
});

function GeoCasesPage() {
  const { t } = useLanguage();
  const [caseTitle, setCaseTitle] = useState("");
  const [originalQuestion, setOriginalQuestion] = useState("");
  const [actorId, setActorId] = useState("analyst1");
  const casesQuery = useGeoCases();
  const createMutation = useCreateGeoCase();
  const pathname = useRouterState({ select: (r) => r.location.pathname });

  // /geo/cases/$id（Case詳細）は本ファイルの子ルートとして登録されるため、詳細ページ
  // 表示中は一覧を出さずOutletのみ描画する（competitors.companies.tsxと同じ対処）。
  if (pathname !== "/geo/cases") {
    return <Outlet />;
  }

  const onCreate = () => {
    if (!caseTitle.trim() || !originalQuestion.trim()) return;
    createMutation.mutate(
      { case_title: caseTitle, original_question: originalQuestion, actor_id: actorId },
      { onSuccess: () => { setCaseTitle(""); setOriginalQuestion(""); } },
    );
  };

  return (
    <>
      <TopBar breadcrumb={[{ label: t("nav.geoCases") }]} />
      <main className="flex-1 max-w-[1000px] w-full mx-auto px-8 py-8 space-y-6">
        <section className="border-b border-border pb-5">
          <h1 className="text-editorial text-4xl">{t("geo.cases.title")}</h1>
          <p className="text-sm text-muted-foreground mt-2">{t("geo.cases.subtitle")}</p>
        </section>

        <div className="card-paper rounded-lg p-5 space-y-3">
          <h2 className="text-editorial text-lg">{t("geo.cases.newCase")}</h2>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">{t("geo.cases.caseTitleLabel")}</label>
            <Input value={caseTitle} onChange={(e) => setCaseTitle(e.target.value)} />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">{t("geo.cases.originalQuestionLabel")}</label>
            <Textarea value={originalQuestion} onChange={(e) => setOriginalQuestion(e.target.value)}
                      className="min-h-[80px]" />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">{t("geo.cases.actorIdLabel")}</label>
            <Input value={actorId} onChange={(e) => setActorId(e.target.value)} className="max-w-xs" />
          </div>
          <Button onClick={onCreate} disabled={createMutation.isPending}>{t("geo.cases.create")}</Button>
        </div>

        <div className="space-y-2">
          {(casesQuery.data?.cases ?? []).map((c) => (
            <Link key={c.case_id} to="/geo/cases/$id" params={{ id: c.case_id }}
                  className="card-paper rounded-lg p-4 block hover:bg-muted/40 transition-colors">
              <div className="text-editorial text-lg">{c.case_title}</div>
              <div className="text-xs text-muted-foreground mt-1">{c.status}</div>
            </Link>
          ))}
          {(!casesQuery.data || casesQuery.data.cases.length === 0) && (
            <p className="text-sm text-muted-foreground">{t("geo.cases.empty")}</p>
          )}
        </div>
      </main>
    </>
  );
}
