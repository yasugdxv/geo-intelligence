import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Checkbox } from "@/components/ui/checkbox";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import { Search, FileText, BookOpen, Sparkles, Send, X } from "lucide-react";
import { useLanguage } from "@/lib/i18n";
import {
  useAnalyzeIntelligenceAi,
  useIntelligenceAiChat,
  useSearchIntelligenceAi,
  type GeoIntelligenceAiAnalysisMode,
  type GeoIntelligenceAiAnalyzeResponse,
  type GeoIntelligenceAiChatMessage,
  type GeoIntelligenceAiCitation,
  type GeoIntelligenceAiClaimType,
  type GeoIntelligenceAiFilters,
  type GeoIntelligenceAiSearchResult,
  type GeoIntelligenceAiSelectedSourceRef,
} from "@/lib/geoIntelligenceAiApi";
import {
  useKnowledgeItemDetail,
  useReportDetail,
  type GeoReportReviewStatus,
} from "@/lib/geoKnowledgeApi";
import { ReportDetailCard } from "@/components/geo/report-detail-card";

// Phase 2F-2: Intelligence AI Search Results UI。
// Retrieval/Permission logicはPhase 2F-1のPOST /api/geo/intelligence-ai/searchをそのまま
// 呼ぶだけで、ここでは一切再実装しない（Unauthorized Reportはbackend側で既に除外済み）。
// Date/Region/Theme/Key Actor/Review Status/Source Type/Confidentiality Labelの絞り込みは、
// backendに対応するfilter引数が無いため、取得済みresult（permission適用済み・上位limit件）
// に対するUI側の絞り込みとして実装する——backendへ新filterを追加すると新しいAPI変更に
// なるため、今回は行わない（必要になった場合はPhase 2F-3で改めて相談する）。

const ALL = "__all__";

// report-detail-card.tsxのREVIEW_STATUS_STYLEと同じ配色（新しいmeaningを持ち込まない）。
const REVIEW_STATUS_STYLE: Record<GeoReportReviewStatus, string> = {
  uploaded_only: "bg-muted text-muted-foreground",
  in_review: "bg-amber-100 text-amber-800",
  approved: "bg-emerald-100 text-emerald-800",
};

// ts_headline（RPC側、chr(2)/chr(3)をStartSel/StopSelに使用）の出力を、
// dangerouslySetInnerHTMLを使わずに安全にレンダリングする。制御文字はテキストの
// 区切りとしてのみ使い、HTMLとして解釈しない。
function MatchSnippet({ snippet }: { snippet: string }) {
  // backendのts_headlineがStartSel/StopSelとして意図的に使っている区切り文字であり、事故混入ではない
  // eslint-disable-next-line no-control-regex
  const parts = snippet.split(/[\u0002\u0003]/);
  return (
    <p className="text-xs text-muted-foreground leading-relaxed">
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <mark key={i} className="bg-amber-200/70 text-foreground rounded-sm px-0.5">
            {part}
          </mark>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </p>
  );
}

function SourceTypeBadge({ sourceType }: { sourceType: "report" | "knowledge_item" }) {
  const { t } = useLanguage();
  return (
    <Badge variant={sourceType === "report" ? "outline" : "secondary"} className="gap-1">
      {sourceType === "report" ? (
        <FileText className="h-3 w-3" />
      ) : (
        <BookOpen className="h-3 w-3" />
      )}
      {t(`geo.intelligenceAiSearch.sourceType.${sourceType}`)}
    </Badge>
  );
}

interface Filters {
  dateFrom: string;
  dateTo: string;
  region: string;
  theme: string;
  keyActor: string;
  reviewStatus: string;
  sourceType: string;
  confidentialityLabel: string;
}

const EMPTY_FILTERS: Filters = {
  dateFrom: "",
  dateTo: "",
  region: "",
  theme: "",
  keyActor: "",
  reviewStatus: "",
  sourceType: "",
  confidentialityLabel: "",
};

function matchesFilters(r: GeoIntelligenceAiSearchResult, f: Filters): boolean {
  if (f.sourceType && r.source_type !== f.sourceType) return false;
  if (f.reviewStatus && r.review_status !== f.reviewStatus) return false;
  if (f.confidentialityLabel && r.confidentiality_label !== f.confidentialityLabel) return false;
  if (f.region && !(r.region ?? []).includes(f.region)) return false;
  if (f.theme && r.theme !== f.theme) return false;
  if (f.keyActor && !(r.key_actors ?? []).includes(f.keyActor)) return false;
  if (f.dateFrom && (!r.date || r.date < f.dateFrom)) return false;
  if (f.dateTo && (!r.date || r.date > f.dateTo)) return false;
  return true;
}

function resultKey(r: GeoIntelligenceAiSearchResult): string {
  return r.source_type === "report" ? `report:${r.report_id}` : `knowledge:${r.knowledge_item_id}`;
}

// Phase 2F-5: Search Result CardからもCitation（source_refs）からも同じPreview Sheetを開ける
// よう、両方が変換できる最小限の共通shape。新しいdetail取得ロジックは作らない——
// ReportPreview/KnowledgeItemPreviewは既存のuseReportDetail/useKnowledgeItemDetailで
// 自分でfull detailを取りに行くため、ここではreport_id/knowledge_item_idだけあればよい。
type PreviewTarget =
  | { source_type: "report"; report_id: string; review_status?: GeoReportReviewStatus | null }
  | {
      source_type: "knowledge_item";
      knowledge_item_id: string;
      review_status?: GeoReportReviewStatus | null;
    };

function previewKey(t: PreviewTarget): string {
  return t.source_type === "report" ? `report:${t.report_id}` : `knowledge:${t.knowledge_item_id}`;
}

function resultToPreviewTarget(r: GeoIntelligenceAiSearchResult): PreviewTarget {
  return r.source_type === "report"
    ? { source_type: "report", report_id: r.report_id, review_status: r.review_status }
    : {
        source_type: "knowledge_item",
        knowledge_item_id: r.knowledge_item_id,
        review_status: r.review_status,
      };
}

function citationToPreviewTarget(c: GeoIntelligenceAiCitation): PreviewTarget | null {
  if (c.source_type === "report" && c.report_id) {
    return { source_type: "report", report_id: c.report_id, review_status: c.review_status };
  }
  if (c.source_type === "knowledge_item" && c.knowledge_item_id) {
    return {
      source_type: "knowledge_item",
      knowledge_item_id: c.knowledge_item_id,
      review_status: c.review_status,
    };
  }
  return null;
}

function ResultCard({
  result,
  onSelect,
  selected,
  checked,
  onToggleChecked,
}: {
  result: GeoIntelligenceAiSearchResult;
  onSelect: () => void;
  selected: boolean;
  checked: boolean;
  onToggleChecked: () => void;
}) {
  const { t } = useLanguage();
  const title =
    result.title || (result.source_type === "report" ? result.report_id : result.knowledge_item_id);
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => e.key === "Enter" && onSelect()}
      className={`w-full text-left card-paper rounded-lg p-4 transition-colors cursor-pointer ${
        selected ? "border-primary ring-1 ring-primary" : ""
      }`}
    >
      <div className="flex items-center gap-2 flex-wrap">
        <Checkbox
          checked={checked}
          onCheckedChange={() => onToggleChecked()}
          onClick={(e) => e.stopPropagation()}
          aria-label={t("geo.intelligenceAiAnalyze.compareSelected", { count: "" })}
        />
        <SourceTypeBadge sourceType={result.source_type} />
        {result.review_status && (
          <span
            className={`px-2 py-0.5 rounded-full text-[11px] font-bold ${REVIEW_STATUS_STYLE[result.review_status]}`}
          >
            {t(`geo.reportRegistration.reviewStatus.${result.review_status}`)}
          </span>
        )}
        {result.confidentiality_label && (
          <Badge variant="secondary">
            {t(`geo.confidentiality.${result.confidentiality_label}`)}
          </Badge>
        )}
        {result.source_type === "report" && result.report_type && (
          <Badge variant="outline">{result.report_type}</Badge>
        )}
      </div>

      <div className="text-editorial text-lg mt-2">{title}</div>

      <div className="text-xs text-muted-foreground mt-1 flex flex-wrap gap-x-3">
        {result.source_type === "report" && result.author && <span>{result.author}</span>}
        {result.theme && <span>{result.theme}</span>}
        {result.region && result.region.length > 0 && <span>{result.region.join(" / ")}</span>}
        {result.key_actors && result.key_actors.length > 0 && (
          <span>{result.key_actors.join(" / ")}</span>
        )}
        {result.date && <span>{new Date(result.date).toLocaleDateString()}</span>}
        {result.source_type === "report" && result.version && <span>v{result.version}</span>}
      </div>

      {result.summary && (
        <p className="text-sm text-muted-foreground mt-2 line-clamp-2">{result.summary}</p>
      )}

      {result.snippet && (
        <div className="mt-2 pt-2 border-t border-border/60">
          <p className="text-[10px] uppercase tracking-[0.08em] text-muted-foreground mb-0.5">
            {t("geo.intelligenceAiSearch.matchReason")}
          </p>
          <MatchSnippet snippet={result.snippet} />
        </div>
      )}
    </div>
  );
}

function ReportPreview({ reportId }: { reportId: string }) {
  const { data, isLoading } = useReportDetail(reportId);
  if (isLoading) return <p className="text-sm text-muted-foreground">...</p>;
  if (!data) return <p className="text-sm text-muted-foreground">—</p>;
  // ReportDetailCardは既存Report DBの詳細・Version履歴・Reviewをそのまま表示する
  // （Intelligence AI側で独自のReport管理機能は作らない）。
  return <ReportDetailCard report={data} />;
}

function KnowledgeItemPreview({ knowledgeItemId }: { knowledgeItemId: string }) {
  const { t } = useLanguage();
  const { data, isLoading } = useKnowledgeItemDetail(knowledgeItemId);
  if (isLoading) return <p className="text-sm text-muted-foreground">...</p>;
  if (!data) return <p className="text-sm text-muted-foreground">—</p>;
  const approvalDate = (data.structured_content?.approval_date as string | undefined) ?? null;
  const sourceReportId = (data.structured_content?.source_report_id as string | undefined) ?? null;
  return (
    <div className="card-paper rounded-lg p-4 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <Badge variant="secondary">{t("geo.intelligenceAiSearch.sourceType.knowledge_item")}</Badge>
        <Badge variant="outline">{data.knowledge_type}</Badge>
      </div>
      <div className="text-editorial text-lg">{data.title}</div>
      {approvalDate && (
        <p className="text-xs text-muted-foreground">
          {new Date(approvalDate).toLocaleDateString()}
        </p>
      )}
      {data.summary && <p className="text-sm text-muted-foreground">{data.summary}</p>}
      {sourceReportId && (
        <p className="text-xs text-muted-foreground">
          {t("geo.intelligenceAiSearch.sourceReport")}: {sourceReportId}
        </p>
      )}
    </div>
  );
}

const CLAIM_TYPE_STYLE: Record<GeoIntelligenceAiClaimType, string> = {
  source_supported: "bg-emerald-100 text-emerald-800",
  cross_report_synthesis: "bg-sky-100 text-sky-800",
  ai_inference: "bg-amber-100 text-amber-800",
};

// Phase 2F-5: Citation/Source UX強化。新しい分析ロジックは作らず、既存source_refs
// （ref_id/title/date/version/review_status/confidentiality_label/snippet）をそのまま使い、
// 「この記述はどのレポートのどこに基づくのか」をhoverで確認・clickで全文Previewを開ける
// ようにするだけ。
function CitationChip({
  refId,
  citation,
  onOpen,
}: {
  refId: string;
  citation?: GeoIntelligenceAiCitation;
  onOpen: (c: GeoIntelligenceAiCitation) => void;
}) {
  const { t } = useLanguage();
  if (!citation) {
    // Backend側で既に存在しないref_idのfindingは除外されているため、通常は起こらない
    // （geo_intelligence_ai_analysis_service.analyzeのHallucination Guard）。防御的表示のみ。
    return <span className="text-muted-foreground font-mono text-[11px]">[{refId}]</span>;
  }
  return (
    <HoverCard openDelay={150}>
      <HoverCardTrigger asChild>
        <button
          type="button"
          onClick={() => onOpen(citation)}
          className="inline-flex items-center px-1.5 py-0.5 rounded bg-muted hover:bg-accent text-[11px] font-mono text-muted-foreground hover:text-foreground align-middle"
        >
          [{refId}]
        </button>
      </HoverCardTrigger>
      <HoverCardContent className="w-80 text-xs space-y-1.5">
        <div className="flex items-center gap-1.5 flex-wrap">
          <SourceTypeBadge sourceType={citation.source_type} />
          {citation.review_status && (
            <span
              className={`px-1.5 py-0.5 rounded-full text-[10px] font-bold ${REVIEW_STATUS_STYLE[citation.review_status]}`}
            >
              {t(`geo.reportRegistration.reviewStatus.${citation.review_status}`)}
            </span>
          )}
        </div>
        <p className="font-medium text-foreground">{citation.title}</p>
        <div className="text-muted-foreground flex gap-2 flex-wrap">
          {citation.date && <span>{citation.date.slice(0, 10)}</span>}
          {citation.version && <span>v{citation.version}</span>}
        </div>
        {citation.snippet && (
          <p className="text-muted-foreground leading-relaxed">{citation.snippet}</p>
        )}
        <p className="text-primary underline underline-offset-2 pt-1">
          {t("geo.intelligenceAiAnalyze.openSource")}
        </p>
      </HoverCardContent>
    </HoverCard>
  );
}

function AnalysisAnswerBody({
  result,
  onOpenSource,
}: {
  result: GeoIntelligenceAiAnalyzeResponse;
  onOpenSource: (c: GeoIntelligenceAiCitation) => void;
}) {
  const { t } = useLanguage();
  const citationsByRefId = useMemo(
    () => Object.fromEntries(result.source_refs.map((s) => [s.ref_id, s])),
    [result.source_refs],
  );
  return (
    <div className="space-y-3">
      {result.insufficient_evidence && (
        <p className="text-sm text-amber-800 bg-amber-50 rounded p-2">
          {t("geo.intelligenceAiAnalyze.insufficientEvidence")}
        </p>
      )}

      <p className="text-sm text-foreground whitespace-pre-wrap">{result.answer}</p>

      {result.findings.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-medium text-muted-foreground">
            {t("geo.intelligenceAiAnalyze.findings")}
          </p>
          {result.findings.map((f, i) => (
            <div key={i} className="text-sm flex items-start gap-2">
              <span
                className={`shrink-0 px-2 py-0.5 rounded-full text-[10px] font-bold ${CLAIM_TYPE_STYLE[f.claim_type]}`}
              >
                {t(`geo.intelligenceAiAnalyze.claimType.${f.claim_type}`)}
              </span>
              <span className="text-foreground space-x-1">
                <span>{f.claim}</span>
                {f.source_refs.map((refId) => (
                  <CitationChip
                    key={refId}
                    refId={refId}
                    citation={citationsByRefId[refId]}
                    onOpen={onOpenSource}
                  />
                ))}
              </span>
            </div>
          ))}
        </div>
      )}

      {(result.coverage.regions.length > 0 ||
        result.coverage.themes.length > 0 ||
        result.coverage.key_actors.length > 0) && (
        <div className="text-xs text-muted-foreground">
          <span className="font-medium">{t("geo.intelligenceAiAnalyze.coverage")}: </span>
          {[
            ...result.coverage.regions,
            ...result.coverage.themes,
            ...result.coverage.key_actors,
          ].join(" / ")}
        </div>
      )}

      {result.temporal_notes && (
        <div className="text-xs text-muted-foreground">
          <span className="font-medium">{t("geo.intelligenceAiAnalyze.temporalNotes")}: </span>
          {result.temporal_notes}
        </div>
      )}

      {result.source_refs.length > 0 && (
        <div className="space-y-2 pt-2 border-t border-border/60">
          <p className="text-xs font-medium text-muted-foreground">
            {t("geo.intelligenceAiAnalyze.sourcesUsed")}
          </p>
          {result.source_refs.map((s) => (
            <button
              key={s.ref_id}
              type="button"
              onClick={() => onOpenSource(s)}
              className="w-full text-left text-xs space-y-1 rounded p-1.5 -mx-1.5 hover:bg-muted/60"
            >
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-mono text-muted-foreground">[{s.ref_id}]</span>
                <SourceTypeBadge sourceType={s.source_type} />
                {s.review_status && (
                  <span
                    className={`px-1.5 py-0.5 rounded-full text-[10px] font-bold ${REVIEW_STATUS_STYLE[s.review_status]}`}
                  >
                    {t(`geo.reportRegistration.reviewStatus.${s.review_status}`)}
                  </span>
                )}
                <span className="text-foreground">{s.title}</span>
                {s.date && <span className="text-muted-foreground">{s.date.slice(0, 10)}</span>}
                {s.version && <span className="text-muted-foreground">v{s.version}</span>}
              </div>
              {s.snippet && (
                <p className="text-muted-foreground leading-relaxed pl-0.5">{s.snippet}</p>
              )}
            </button>
          ))}
        </div>
      )}

      {result.warnings.length > 0 && (
        <div className="text-[11px] text-muted-foreground pt-1">
          <span className="font-medium">{t("geo.intelligenceAiAnalyze.warnings")}: </span>
          {result.warnings.join(" / ")}
        </div>
      )}
    </div>
  );
}

function AnalysisAnswerPanel({
  result,
  onClose,
  onOpenSource,
}: {
  result: GeoIntelligenceAiAnalyzeResponse;
  onClose: () => void;
  onOpenSource: (c: GeoIntelligenceAiCitation) => void;
}) {
  const { t } = useLanguage();
  return (
    <div className="card-paper rounded-lg p-4 space-y-3">
      <div className="flex items-start justify-between gap-2">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-[0.08em]">
          {t("geo.intelligenceAiAnalyze.answer")}
        </p>
        <button
          type="button"
          onClick={onClose}
          className="text-muted-foreground hover:text-foreground"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <AnalysisAnswerBody result={result} onOpenSource={onOpenSource} />
    </div>
  );
}

const ANALYSIS_MODES: GeoIntelligenceAiAnalysisMode[] = [
  "summary",
  "compare",
  "temporal",
  "coverage",
  "reviewed",
];

type ChatTurn =
  | { role: "user"; content: string }
  | { role: "assistant"; response: GeoIntelligenceAiAnalyzeResponse };

// Phase 2F-4 §絶対条件: historyへはrole/contentのプレーンテキストのみを送る
// （assistant側もanswer本文のみ——source_refs/permission結果等は含めない。それらはBackendが
// 本requestのidentityで毎turnゼロから再評価する）。
function turnsToHistory(turns: ChatTurn[]): GeoIntelligenceAiChatMessage[] {
  return turns.map((t) =>
    t.role === "user"
      ? { role: "user", content: t.content }
      : { role: "assistant", content: t.response.answer },
  );
}

export function GeoIntelligenceAiSearchTab() {
  const { t } = useLanguage();
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [previewTarget, setPreviewTarget] = useState<PreviewTarget | null>(null);
  const [checkedKeys, setCheckedKeys] = useState<Set<string>>(new Set());
  const [mode, setMode] = useState<GeoIntelligenceAiAnalysisMode>("summary");
  const [chatTurns, setChatTurns] = useState<ChatTurn[]>([]);
  const [followUp, setFollowUp] = useState("");
  const searchMutation = useSearchIntelligenceAi();
  const analyzeMutation = useAnalyzeIntelligenceAi();
  const chatMutation = useIntelligenceAiChat();

  const results = useMemo(() => searchMutation.data?.results ?? [], [searchMutation.data]);
  const hasSearched = searchMutation.isSuccess || searchMutation.isError;

  const regionOptions = useMemo(
    () => Array.from(new Set(results.flatMap((r) => r.region ?? []))).sort(),
    [results],
  );
  const themeOptions = useMemo(
    () => Array.from(new Set(results.map((r) => r.theme).filter((v): v is string => !!v))).sort(),
    [results],
  );
  const actorOptions = useMemo(
    () => Array.from(new Set(results.flatMap((r) => r.key_actors ?? []))).sort(),
    [results],
  );

  const filteredResults = useMemo(
    () => results.filter((r) => matchesFilters(r, filters)),
    [results, filters],
  );

  // Phase 2F-5: findings内のCitation Chip・「使用したSource」一覧のどちらからでも、
  // 既存Preview Sheet（ReportPreview/KnowledgeItemPreview）をそのまま開く。
  const openCitation = (c: GeoIntelligenceAiCitation) => {
    const target = citationToPreviewTarget(c);
    if (target) setPreviewTarget(target);
  };

  const runSearch = () => {
    if (!query.trim()) return;
    setPreviewTarget(null);
    setCheckedKeys(new Set());
    analyzeMutation.reset();
    setChatTurns([]);
    searchMutation.mutate({ query, limit: 50 });
  };

  const filterCount = Object.values(filters).filter(Boolean).length;

  // Phase 2F-3 §0: UI filter状態をBackend filter（geo_intelligence_ai_search_service.
  // resolve_eligible_*_idsが解釈するdate_from/date_to/region/theme/key_actor/review_status/
  // source_type/confidentiality_label）へ変換する。空文字はfilter無しとして省略する。
  const buildBackendFilters = (): GeoIntelligenceAiFilters | undefined => {
    const f: GeoIntelligenceAiFilters = {};
    if (filters.dateFrom) f.date_from = filters.dateFrom;
    if (filters.dateTo) f.date_to = filters.dateTo;
    if (filters.region) f.region = filters.region;
    if (filters.theme) f.theme = filters.theme;
    if (filters.keyActor) f.key_actor = filters.keyActor;
    if (filters.reviewStatus)
      f.review_status = filters.reviewStatus as GeoIntelligenceAiFilters["review_status"];
    if (filters.sourceType)
      f.source_type = filters.sourceType as GeoIntelligenceAiFilters["source_type"];
    if (filters.confidentialityLabel) {
      f.confidentiality_label =
        filters.confidentialityLabel as GeoIntelligenceAiFilters["confidentiality_label"];
    }
    return Object.keys(f).length > 0 ? f : undefined;
  };

  const toggleChecked = (key: string) => {
    setCheckedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const checkedResults = useMemo(
    () => filteredResults.filter((r) => checkedKeys.has(resultKey(r))),
    [filteredResults, checkedKeys],
  );

  // Phase 2F-4: 「検索結果をまとめる」はMulti-turn Chatの最初のturnとして扱う（会話は
  // ここから始まり、以降はfollow-up入力で続けられる）。
  const runSummarize = () => {
    const userTurn: ChatTurn = { role: "user", content: query };
    setChatTurns([userTurn]);
    chatMutation.mutate(
      { message: query, history: [], mode, filters: buildBackendFilters() },
      {
        onSuccess: (data) => {
          setChatTurns([userTurn, { role: "assistant", response: data }]);
        },
      },
    );
  };

  const sendFollowUp = () => {
    if (!followUp.trim() || chatMutation.isPending) return;
    const message = followUp;
    const userTurn: ChatTurn = { role: "user", content: message };
    const historyBefore = turnsToHistory(chatTurns);
    setChatTurns((prev) => [...prev, userTurn]);
    setFollowUp("");
    chatMutation.mutate(
      { message, history: historyBefore, mode, filters: buildBackendFilters() },
      {
        onSuccess: (data) => {
          setChatTurns((prev) => [...prev, { role: "assistant", response: data }]);
        },
      },
    );
  };

  const runCompareSelected = () => {
    const refs: GeoIntelligenceAiSelectedSourceRef[] = checkedResults.map((r) =>
      r.source_type === "report"
        ? { source_type: "report", id: r.report_id }
        : { source_type: "knowledge_item", id: r.knowledge_item_id },
    );
    analyzeMutation.mutate({
      query,
      mode: mode === "summary" ? "compare" : mode,
      selectedSourceRefs: refs,
    });
  };

  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && runSearch()}
          placeholder={t("geo.intelligenceAiSearch.placeholder")}
          className="flex-1"
        />
        <Button onClick={runSearch} disabled={searchMutation.isPending || !query.trim()}>
          <Search className="h-4 w-4 mr-1.5" />
          {searchMutation.isPending
            ? t("geo.intelligenceAiSearch.searching")
            : t("geo.intelligenceAiSearch.searchButton")}
        </Button>
      </div>

      {searchMutation.isError && (
        <p className="text-sm text-destructive">{(searchMutation.error as Error).message}</p>
      )}

      {hasSearched && (
        <div className="flex flex-wrap items-center gap-2">
          <Select value={mode} onValueChange={(v) => setMode(v as GeoIntelligenceAiAnalysisMode)}>
            <SelectTrigger className="h-8 text-xs w-40">
              <SelectValue placeholder={t("geo.intelligenceAiAnalyze.mode")} />
            </SelectTrigger>
            <SelectContent>
              {ANALYSIS_MODES.map((m) => (
                <SelectItem key={m} value={m}>
                  {t(`geo.intelligenceAiAnalyze.mode.${m}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            size="sm"
            variant="outline"
            onClick={runSummarize}
            disabled={chatMutation.isPending || results.length === 0}
          >
            <Sparkles className="h-3.5 w-3.5 mr-1.5" />
            {chatMutation.isPending && chatTurns.length <= 1
              ? t("geo.intelligenceAiAnalyze.analyzing")
              : t("geo.intelligenceAiAnalyze.summarizeResults")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={runCompareSelected}
            disabled={
              analyzeMutation.isPending || checkedResults.length < 2 || checkedResults.length > 5
            }
          >
            <Sparkles className="h-3.5 w-3.5 mr-1.5" />
            {t("geo.intelligenceAiAnalyze.compareSelected", { count: checkedResults.length })}
          </Button>
          {checkedResults.length > 0 && checkedResults.length < 2 && (
            <span className="text-[11px] text-muted-foreground">
              {t("geo.intelligenceAiAnalyze.selectHint")}
            </span>
          )}
        </div>
      )}

      {analyzeMutation.isError && (
        <p className="text-sm text-destructive">{(analyzeMutation.error as Error).message}</p>
      )}
      {analyzeMutation.data && (
        <AnalysisAnswerPanel
          result={analyzeMutation.data}
          onClose={() => analyzeMutation.reset()}
          onOpenSource={openCitation}
        />
      )}

      {chatTurns.length > 0 && (
        <div className="card-paper rounded-lg p-4 space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-[0.08em]">
              {t("geo.intelligenceAiAnalyze.answer")}
            </p>
            <button
              type="button"
              onClick={() => setChatTurns([])}
              className="text-muted-foreground hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="space-y-3">
            {chatTurns.map((turn, i) =>
              turn.role === "user" ? (
                <p key={i} className="text-sm font-medium text-foreground">
                  {turn.content}
                </p>
              ) : (
                <AnalysisAnswerBody key={i} result={turn.response} onOpenSource={openCitation} />
              ),
            )}
          </div>
          {chatMutation.isError && (
            <p className="text-sm text-destructive">{(chatMutation.error as Error).message}</p>
          )}
          <div className="flex gap-2 pt-2 border-t border-border/60">
            <Input
              value={followUp}
              onChange={(e) => setFollowUp(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && sendFollowUp()}
              placeholder={t("geo.intelligenceAiAnalyze.followUpPlaceholder")}
              className="flex-1 h-9 text-sm"
              disabled={chatMutation.isPending}
            />
            <Button
              size="sm"
              onClick={sendFollowUp}
              disabled={chatMutation.isPending || !followUp.trim()}
            >
              <Send className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      )}

      {hasSearched && (
        <div className="grid grid-cols-[240px_1fr] gap-5 items-start">
          <aside className="card-paper rounded-lg p-4 space-y-3 sticky top-4">
            <div className="flex items-center justify-between">
              <p className="text-xs font-medium text-muted-foreground">
                {t("geo.intelligenceAiSearch.filters")}
              </p>
              {filterCount > 0 && (
                <button
                  type="button"
                  className="text-[11px] text-primary underline underline-offset-2"
                  onClick={() => setFilters(EMPTY_FILTERS)}
                >
                  {t("geo.intelligenceAiSearch.filters.clear")}
                </button>
              )}
            </div>

            <FilterSelect
              label={t("geo.intelligenceAiSearch.filters.sourceType")}
              value={filters.sourceType}
              onChange={(v) => setFilters((f) => ({ ...f, sourceType: v }))}
              options={[
                { value: "report", label: t("geo.intelligenceAiSearch.sourceType.report") },
                {
                  value: "knowledge_item",
                  label: t("geo.intelligenceAiSearch.sourceType.knowledge_item"),
                },
              ]}
              allLabel={t("geo.intelligenceAiSearch.filters.all")}
            />
            <FilterSelect
              label={t("geo.intelligenceAiSearch.filters.reviewStatus")}
              value={filters.reviewStatus}
              onChange={(v) => setFilters((f) => ({ ...f, reviewStatus: v }))}
              options={(["uploaded_only", "in_review", "approved"] as const).map((s) => ({
                value: s,
                label: t(`geo.reportRegistration.reviewStatus.${s}`),
              }))}
              allLabel={t("geo.intelligenceAiSearch.filters.all")}
            />
            <FilterSelect
              label={t("geo.intelligenceAiSearch.filters.confidentialityLabel")}
              value={filters.confidentialityLabel}
              onChange={(v) => setFilters((f) => ({ ...f, confidentialityLabel: v }))}
              options={(["DEPARTMENT_ONLY", "SHD_ONLY", "GROUP_SHAREABLE"] as const).map((l) => ({
                value: l,
                label: t(`geo.confidentiality.${l}`),
              }))}
              allLabel={t("geo.intelligenceAiSearch.filters.all")}
            />
            {regionOptions.length > 0 && (
              <FilterSelect
                label={t("geo.intelligenceAiSearch.filters.region")}
                value={filters.region}
                onChange={(v) => setFilters((f) => ({ ...f, region: v }))}
                options={regionOptions.map((v) => ({ value: v, label: v }))}
                allLabel={t("geo.intelligenceAiSearch.filters.all")}
              />
            )}
            {themeOptions.length > 0 && (
              <FilterSelect
                label={t("geo.intelligenceAiSearch.filters.theme")}
                value={filters.theme}
                onChange={(v) => setFilters((f) => ({ ...f, theme: v }))}
                options={themeOptions.map((v) => ({ value: v, label: v }))}
                allLabel={t("geo.intelligenceAiSearch.filters.all")}
              />
            )}
            {actorOptions.length > 0 && (
              <FilterSelect
                label={t("geo.intelligenceAiSearch.filters.keyActor")}
                value={filters.keyActor}
                onChange={(v) => setFilters((f) => ({ ...f, keyActor: v }))}
                options={actorOptions.map((v) => ({ value: v, label: v }))}
                allLabel={t("geo.intelligenceAiSearch.filters.all")}
              />
            )}
            <div className="space-y-1.5">
              <label className="text-[11px] text-muted-foreground">
                {t("geo.intelligenceAiSearch.filters.dateFrom")}
              </label>
              <Input
                type="date"
                value={filters.dateFrom}
                onChange={(e) => setFilters((f) => ({ ...f, dateFrom: e.target.value }))}
                className="h-8 text-xs"
              />
              <label className="text-[11px] text-muted-foreground">
                {t("geo.intelligenceAiSearch.filters.dateTo")}
              </label>
              <Input
                type="date"
                value={filters.dateTo}
                onChange={(e) => setFilters((f) => ({ ...f, dateTo: e.target.value }))}
                className="h-8 text-xs"
              />
            </div>

            <Link
              to="/geo/knowledge-search"
              className="block text-xs text-primary underline underline-offset-2 pt-2"
            >
              {t("geo.intelligenceAiSearch.openInDb")}
            </Link>
          </aside>

          <div className="space-y-3">
            {results.length > 0 && (
              <p className="text-xs text-muted-foreground">
                {t("geo.intelligenceAiSearch.resultCount", {
                  count: filteredResults.length,
                  reportCount: filteredResults.filter((r) => r.source_type === "report").length,
                  knowledgeCount: filteredResults.filter((r) => r.source_type === "knowledge_item")
                    .length,
                })}
              </p>
            )}

            {results.length === 0 ? (
              <div className="card-paper rounded-lg p-6 text-center space-y-1">
                <p className="text-sm text-foreground">{t("geo.intelligenceAiSearch.noHit")}</p>
                <p className="text-xs text-muted-foreground">
                  {t("geo.intelligenceAiSearch.noHitHint")}
                </p>
              </div>
            ) : (
              <div className="space-y-2">
                {filteredResults.map((r) => (
                  <ResultCard
                    key={resultKey(r)}
                    result={r}
                    selected={previewTarget !== null && previewKey(previewTarget) === resultKey(r)}
                    onSelect={() => setPreviewTarget(resultToPreviewTarget(r))}
                    checked={checkedKeys.has(resultKey(r))}
                    onToggleChecked={() => toggleChecked(resultKey(r))}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      <Sheet open={previewTarget !== null} onOpenChange={(open) => !open && setPreviewTarget(null)}>
        <SheetContent className="w-full sm:max-w-xl overflow-y-auto">
          <SheetHeader>
            <SheetTitle>{t("geo.intelligenceAiSearch.preview")}</SheetTitle>
          </SheetHeader>
          <div className="mt-4">
            {previewTarget?.source_type === "report" && (
              <ReportPreview reportId={previewTarget.report_id} />
            )}
            {previewTarget?.source_type === "knowledge_item" && (
              <KnowledgeItemPreview knowledgeItemId={previewTarget.knowledge_item_id} />
            )}
            {previewTarget?.review_status === "uploaded_only" && (
              <p className="text-[11px] text-amber-800 bg-amber-50 rounded p-2 mt-3">
                {t("geo.intelligenceAiSearch.reviewStatusNote")}
              </p>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}

function FilterSelect({
  label,
  value,
  onChange,
  options,
  allLabel,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  allLabel: string;
}) {
  return (
    <div className="space-y-1">
      <label className="text-[11px] text-muted-foreground">{label}</label>
      <Select value={value || ALL} onValueChange={(v) => onChange(v === ALL ? "" : v)}>
        <SelectTrigger className="h-8 text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>{allLabel}</SelectItem>
          {options.map((o) => (
            <SelectItem key={o.value} value={o.value}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
