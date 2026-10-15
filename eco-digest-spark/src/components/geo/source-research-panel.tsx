import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { SourceSelector, SourceSetSummary } from "@/components/geo/source-selector";
import { useLanguage } from "@/lib/i18n";
import {
  useResearchContext,
  useRunSourceResearch,
  type ResearchContext,
  type ResearchResult,
} from "@/lib/geoSourceLibraryApi";

// リサーチスタジオの「Source調査」。Theme Explorer等から引き継いだTheme/Query/Sourceで調査する。
// 重要: Referenceが無い状態でLLM回答だけを出さない（Referenceが0件なら回答は出さずnotesを表示）。

export function SourceResearchPanel({ projectId }: { projectId: string }) {
  const { t } = useLanguage();
  const ctxQuery = useResearchContext(projectId);

  if (ctxQuery.isLoading) {
    return <Skeleton className="h-32 w-full rounded-lg" />;
  }
  if (ctxQuery.isError) {
    // 404（引き継ぎ無し）は「引き継ぎなし」扱い。それ以外（503 migration未適用など）はメッセージを表示
    const msg = (ctxQuery.error as Error).message;
    if (/404|not found/i.test(msg)) {
      return <p className="text-sm text-muted-foreground">{t("geo.sourceResearch.noContext")}</p>;
    }
    return (
      <p className="text-sm text-destructive" role="alert">
        {t("geo.sourceResearch.error", { message: msg })}
      </p>
    );
  }
  const context = ctxQuery.data?.research_context;
  if (!context) {
    return <p className="text-sm text-muted-foreground">{t("geo.sourceResearch.noContext")}</p>;
  }
  return <ResearchRunner projectId={projectId} context={context} />;
}

function ResearchRunner({ projectId, context }: { projectId: string; context: ResearchContext }) {
  const { t } = useLanguage();
  const [query, setQuery] = useState(context.query ?? "");
  const [sourceIds, setSourceIds] = useState<string[]>(context.source_ids);
  const [sourceNames, setSourceNames] = useState<string[]>(context.source_names);
  const [useWeb, setUseWeb] = useState(false);
  const [selectorOpen, setSelectorOpen] = useState(false);
  const run = useRunSourceResearch(projectId);

  const execute = () => {
    if (sourceIds.length === 0) return;
    run.mutate({ query: query.trim() || undefined, source_ids: sourceIds, use_web: useWeb });
  };

  return (
    <div className="space-y-5">
      <div className="space-y-1.5">
        <p className="text-xs font-medium text-muted-foreground">
          {t("geo.sourceResearch.sourcesUsing")}
        </p>
        <SourceSetSummary
          names={sourceNames}
          count={sourceIds.length}
          onChange={() => setSelectorOpen(true)}
        />
        {sourceIds.length === 0 && (
          <p className="text-xs text-geo-amber">{t("geo.sourceResearch.needSources")}</p>
        )}
      </div>

      <div className="space-y-1.5">
        <p className="text-xs font-medium text-muted-foreground">{t("geo.sourceResearch.theme")}</p>
        <p className="text-sm font-medium">{context.theme}</p>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="source-research-query">{t("geo.sourceResearch.query")}</Label>
        <Input
          id="source-research-query"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      <div className="flex items-center gap-4 flex-wrap">
        <label className="flex items-center gap-2 text-sm">
          <Switch checked={useWeb} onCheckedChange={setUseWeb} />
          {t("geo.sourceResearch.useWeb")}
        </label>
        <Button onClick={execute} disabled={run.isPending || sourceIds.length === 0}>
          {run.isPending ? t("geo.sourceResearch.running") : t("geo.sourceResearch.run")}
        </Button>
      </div>

      {run.isPending && <Skeleton className="h-40 w-full rounded-lg" />}
      {run.isError && (
        <p className="text-sm text-destructive" role="alert">
          {t("geo.sourceResearch.runError", { message: (run.error as Error).message })}
        </p>
      )}
      {run.data && !run.isPending && <ResearchResultView result={run.data} />}

      <SourceSelector
        open={selectorOpen}
        onOpenChange={setSelectorOpen}
        selectedIds={sourceIds}
        initialQuery=""
        onApply={(ids, srcs) => {
          setSourceIds(ids);
          setSourceNames(srcs.map((s) => s.name));
        }}
      />
    </div>
  );
}

/** 回答本文中の[n]を、対応するReference行へのアンカーとして表示する */
function AnswerText({ text }: { text: string }) {
  const parts = text.split(/(\[\d+\])/g);
  return (
    <p className="text-sm whitespace-pre-wrap">
      {parts.map((p, i) => {
        const m = /^\[(\d+)\]$/.exec(p);
        return m ? (
          <a key={i} href={`#source-ref-${m[1]}`} className="text-primary font-medium">
            {p}
          </a>
        ) : (
          <span key={i}>{p}</span>
        );
      })}
    </p>
  );
}

function ResearchResultView({ result }: { result: ResearchResult }) {
  const { t } = useLanguage();
  const hasRefs = result.references.length > 0;
  return (
    <div className="space-y-4" data-testid="source-research-result">
      {result.errors.length > 0 && (
        <div
          className="rounded-md border border-destructive/50 bg-destructive/5 p-3 text-sm space-y-1"
          role="alert"
        >
          <p className="font-medium text-destructive">{t("geo.sourceResearch.errors")}</p>
          <ul className="list-disc pl-5">
            {result.errors.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </div>
      )}
      {result.notes.length > 0 && (
        <div className="rounded-md border border-geo-amber/50 bg-geo-amber-soft p-3 text-sm space-y-1">
          <p className="font-medium text-geo-amber">{t("geo.sourceResearch.notes")}</p>
          <ul className="list-disc pl-5">
            {result.notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </div>
      )}

      {/* 検索に使った語（原文の問いは変更されない。英訳・展開語は出自を区別して表示） */}
      {result.query_expansion && result.query_expansion.search_terms.length > 0 && (
        <section className="space-y-1.5" data-testid="query-expansion">
          <h3 className="text-sm font-medium">{t("geo.sourceResearch.expansion.title")}</h3>
          <p className="text-xs text-muted-foreground">
            {t("geo.sourceResearch.expansion.original")}: {result.query_expansion.original}
          </p>
          {result.query_expansion.translated && (
            <p className="text-xs text-muted-foreground">
              {t("geo.sourceResearch.expansion.translated")}: {result.query_expansion.translated}
            </p>
          )}
          <div className="flex flex-wrap gap-1">
            {result.query_expansion.search_terms.map((term) => (
              <Badge
                key={`${term.kind}-${term.term}`}
                variant={term.kind === "original" ? "outline" : "secondary"}
                className="text-[10px]"
              >
                {t(`geo.sourceResearch.expansion.kind.${term.kind}`)}: {term.term}
              </Badge>
            ))}
          </div>
          {result.query_expansion.guard.dropped.length > 0 && (
            <p className="text-[11px] text-muted-foreground">
              {t("geo.sourceResearch.expansion.dropped", {
                count: result.query_expansion.guard.dropped.length,
              })}
            </p>
          )}
        </section>
      )}

      {/* Referenceが無い場合は回答を出さない（根拠なしの回答を作らない） */}
      {hasRefs && result.answer?.text ? (
        <section className="space-y-1.5">
          <h3 className="text-sm font-medium">{t("geo.sourceResearch.answer")}</h3>
          <AnswerText text={result.answer.text} />
          <p className="text-[11px] text-muted-foreground">{t("geo.sourceResearch.answerHint")}</p>
        </section>
      ) : (
        <p className="text-sm text-muted-foreground">
          {hasRefs ? t("geo.sourceResearch.noDirectAnswer") : t("geo.sourceResearch.noAnswer")}
        </p>
      )}

      <section className="space-y-1.5">
        <h3 className="text-sm font-medium">{t("geo.sourceResearch.references")}</h3>
        {hasRefs ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>#</TableHead>
                <TableHead>{t("geo.sourceResearch.col.title")}</TableHead>
                <TableHead>{t("geo.sourceResearch.col.source")}</TableHead>
                <TableHead>{t("geo.sourceResearch.col.published")}</TableHead>
                <TableHead>{t("geo.sourceResearch.col.url")}</TableHead>
                <TableHead>{t("geo.sourceResearch.col.kind")}</TableHead>
                <TableHead>{t("geo.sourceResearch.col.match")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {result.references.map((r) => (
                <TableRow
                  key={r.index}
                  id={`source-ref-${r.index}`}
                  data-match-type={r.match_type}
                  className={r.match_type === "supplementary" ? "bg-muted/40 text-muted-foreground" : undefined}
                >
                  <TableCell className="tabular-nums">[{r.index}]</TableCell>
                  <TableCell className="whitespace-normal">
                    {r.article_title || t("geo.sourceResearch.untitled")}
                  </TableCell>
                  <TableCell>{r.source ?? "—"}</TableCell>
                  <TableCell className="tabular-nums">
                    {r.published_at
                      ? r.published_at.slice(0, 10)
                      : t("geo.sourceResearch.dateUnknown")}
                  </TableCell>
                  <TableCell className="max-w-[240px] truncate">
                    <a
                      href={r.original_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-primary underline underline-offset-2"
                    >
                      {r.original_url}
                    </a>
                  </TableCell>
                  <TableCell>
                    <Badge variant={r.origin === "web" ? "secondary" : "outline"}>
                      {r.origin === "web"
                        ? t("geo.sourceResearch.web")
                        : t("geo.sourceResearch.internal")}
                    </Badge>
                  </TableCell>
                  <TableCell className="whitespace-normal">
                    {/* matched=質問に直接関連 / supplementary=補完（質問に直接関連するEvidenceではない） */}
                    {r.match_type === "supplementary" ? (
                      <Badge variant="outline" className="border-geo-amber/60 text-geo-amber">
                        {t("geo.sourceResearch.match.supplementary")}
                      </Badge>
                    ) : (
                      <Badge variant="secondary">{t("geo.sourceResearch.match.matched")}</Badge>
                    )}
                    {r.matched_terms && r.matched_terms.length > 0 && (
                      <span className="mt-1 block text-[10px] text-muted-foreground">
                        {r.matched_terms
                          .slice(0, 4)
                          .map((m) => `${t(`geo.sourceResearch.expansion.kind.${m.kind}`)}:${m.term}`)
                          .join(" / ")}
                      </span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <p className="text-sm text-muted-foreground">{t("geo.sourceResearch.refEmpty")}</p>
        )}
      </section>

      {result.searched_domains.length > 0 && (
        <p className="text-xs text-muted-foreground">
          {t("geo.sourceResearch.searchedDomains")}: {result.searched_domains.join(", ")}
        </p>
      )}
    </div>
  );
}
