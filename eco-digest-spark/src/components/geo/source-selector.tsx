import { useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { ExternalLink, Search, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { useLanguage } from "@/lib/i18n";
import {
  MEDIA_TYPES,
  useRecommendSources,
  useSourceList,
  type GeoSource,
  type RecommendInput,
  type SourceListFilters,
  type SourceMatchInfo,
} from "@/lib/geoSourceLibraryApi";
import { setPickContext } from "@/lib/sourcePickStore";

// 共通Source Selector（Theme Explorer / Monitoring / Research Studio で共用）。
// 情報ソースDBと同じSource Master（/api/geo/source-library）を参照する。各画面で別々の
// Source検索UIを作らないこと。選択結果はsource_idの配列としてonApplyで返す。
//
// 使い方:
//   <SourceSelector open={open} onOpenChange={setOpen} selectedIds={ids}
//     onApply={(ids) => setIds(ids)} recommendInput={{ candidate_id }}
//     returnContext={{ returnTo: "/geo/theme-discovery", contextId: candidateId, label: title }} />

const ANY = "__any__";
const PAGE_SIZE = 20;

export interface SourceSelectorProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 現在選択中のsource_id（Source Set） */
  selectedIds: string[];
  /** 「N ソースを使用」押下時。名前も渡す（呼び出し側のラベル表示用） */
  onApply: (ids: string[], sources: Pick<GeoSource, "source_id" | "name">[]) => void;
  /** Themeに基づくおすすめ（candidate_id か title/actors等）。無ければ「おすすめ」欄は出さない */
  recommendInput?: RecommendInput;
  initialQuery?: string;
  /** 「情報ソースDBで詳しく探す」から戻るための情報。無ければそのリンクは出さない */
  returnContext?: { returnTo: string; contextId?: string; label: string };
}

function SourceRow({
  source,
  checked,
  onToggle,
  reason,
}: {
  source: Pick<GeoSource, "source_id" | "name" | "url" | "media_type" | "is_primary"> & SourceMatchInfo;
  checked: boolean;
  onToggle: () => void;
  reason?: string;
}) {
  const { t } = useLanguage();
  return (
    <label
      className="flex items-start gap-2 rounded-md border border-border/60 p-2 text-sm cursor-pointer hover:bg-muted/40"
      data-testid="source-selector-row"
    >
      <Checkbox checked={checked} onCheckedChange={onToggle} className="mt-0.5" />
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-1.5">
          <span className="font-medium">{source.name}</span>
          {source.is_primary && (
            <Badge variant="secondary" className="text-[10px]">
              {t("geo.sourceSelector.primary")}
            </Badge>
          )}
          {source.media_type && (
            <Badge variant="outline" className="text-[10px]">
              {source.media_type}
            </Badge>
          )}
        </span>
        {source.url && <span className="block truncate text-xs text-muted-foreground">{source.url}</span>}
        {reason && <span className="block text-xs text-muted-foreground mt-0.5">{reason}</span>}
        {source.match_reason && (
          <span className="block text-xs mt-0.5" data-testid="source-match-reason">
            <span className="font-medium text-primary">{t("geo.sourceSelector.matchReason")}: </span>
            {source.match_reason}
          </span>
        )}
        {source.match_caution && (
          <span className="block text-xs mt-0.5 text-geo-amber" data-testid="source-match-caution">
            <span className="font-medium">{t("geo.sourceSelector.matchCaution")}: </span>
            {source.match_caution}
          </span>
        )}
      </span>
    </label>
  );
}

export function SourceSelector({
  open,
  onOpenChange,
  selectedIds,
  onApply,
  recommendInput,
  initialQuery = "",
  returnContext,
}: SourceSelectorProps) {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const [picked, setPicked] = useState<Set<string>>(new Set(selectedIds));
  const [known, setKnown] = useState<Record<string, Pick<GeoSource, "source_id" | "name">>>({});
  const [text, setText] = useState(initialQuery);
  const [submittedText, setSubmittedText] = useState(initialQuery);
  const [mediaType, setMediaType] = useState(ANY);
  const [region, setRegion] = useState("");
  const [language, setLanguage] = useState("");
  const [primaryOnly, setPrimaryOnly] = useState(false);
  const [page, setPage] = useState(1);

  // 開くたびに現在のSource Setへ同期する
  useEffect(() => {
    if (open) {
      setPicked(new Set(selectedIds));
      setPage(1);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const recommend = useRecommendSources(recommendInput ?? {}, open && !!recommendInput);

  const filters: SourceListFilters = useMemo(
    () => ({
      media_type: mediaType === ANY ? undefined : mediaType,
      region: region.trim() || undefined,
      language: language.trim() || undefined,
      is_primary: primaryOnly ? true : undefined,
      page,
      page_size: PAGE_SIZE,
    }),
    [mediaType, region, language, primaryOnly, page],
  );
  const list = useSourceList(filters, submittedText);

  // ページを進めても前ページの結果を保持して一覧を伸ばす（lazy loading）
  const [accumulated, setAccumulated] = useState<GeoSource[]>([]);
  const filterKey = JSON.stringify({ ...filters, page: 0, submittedText });
  useEffect(() => {
    setPage(1);
    setAccumulated([]);
  }, [filterKey]);
  useEffect(() => {
    if (!list.data) return;
    setAccumulated((prev) => {
      const map = new Map(prev.map((s) => [s.source_id, s]));
      for (const s of list.data!.sources) map.set(s.source_id, s);
      return Array.from(map.values());
    });
    setKnown((prev) => {
      const next = { ...prev };
      for (const s of list.data!.sources) next[s.source_id] = { source_id: s.source_id, name: s.name };
      return next;
    });
  }, [list.data]);
  useEffect(() => {
    if (!recommend.data) return;
    setKnown((prev) => {
      const next = { ...prev };
      for (const r of recommend.data!.recommendations)
        next[r.source_id] = { source_id: r.source_id, name: r.name };
      return next;
    });
  }, [recommend.data]);

  const toggle = (id: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const recommendedIds = new Set((recommend.data?.recommendations ?? []).map((r) => r.source_id));
  const others = accumulated.filter((s) => !recommendedIds.has(s.source_id));
  const interp = list.data?.interpretation;

  const apply = () => {
    const ids = Array.from(picked);
    onApply(
      ids,
      ids.map((id) => known[id] ?? { source_id: id, name: id }),
    );
    onOpenChange(false);
  };

  const openLibrary = () => {
    if (!returnContext) return;
    setPickContext({ ...returnContext, selectedIds: Array.from(picked) });
    onOpenChange(false);
    navigate({ to: "/geo/source-library", search: { pick: "1" } as never });
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="sm:max-w-xl w-full flex flex-col gap-3 p-5">
        <SheetHeader>
          <SheetTitle>{t("geo.sourceSelector.title")}</SheetTitle>
          <SheetDescription>{t("geo.sourceSelector.description")}</SheetDescription>
        </SheetHeader>

        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            setSubmittedText(text.trim());
          }}
        >
          <Input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={t("geo.sourceSelector.searchPlaceholder")}
            aria-label={t("geo.sourceSelector.search")}
          />
          <Button type="submit" variant="secondary">
            <Search className="h-4 w-4 mr-1" />
            {t("geo.sourceSelector.search")}
          </Button>
        </form>

        <div className="grid grid-cols-2 gap-2 text-xs">
          <Select value={mediaType} onValueChange={setMediaType}>
            <SelectTrigger aria-label={t("geo.sourceSelector.mediaType")}>
              <SelectValue placeholder={t("geo.sourceSelector.mediaType")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>
                {t("geo.sourceSelector.mediaType")}: {t("geo.sourceSelector.any")}
              </SelectItem>
              {MEDIA_TYPES.map((m) => (
                <SelectItem key={m} value={m}>
                  {m}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input
            value={region}
            onChange={(e) => setRegion(e.target.value)}
            placeholder={t("geo.sourceSelector.region")}
          />
          <Input
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            placeholder={`${t("geo.sourceSelector.language")} (ja/en/zh)`}
          />
          <label className="flex items-center gap-2">
            <Checkbox checked={primaryOnly} onCheckedChange={(v) => setPrimaryOnly(v === true)} />
            {t("geo.sourceSelector.primaryOnly")}
          </label>
        </div>

        {interp && (
          <div className="text-xs text-muted-foreground space-y-1" data-testid="source-selector-interpretation">
            {interp.error && <p className="text-geo-amber">{interp.error}</p>}
            {!interp.error && !interp.used_llm && <p>{t("geo.sourceSelector.llmFallback")}</p>}
            {interp.mode === "semantic" && (
              <>
                {interp.understood && <p>{t("geo.sourceSelector.understood", { text: interp.understood })}</p>}
                <p>{t("geo.sourceSelector.semanticNote")}</p>
              </>
            )}
            <div className="flex flex-wrap items-center gap-1">
              <span>{t("geo.sourceSelector.interpreted")}:</span>
              {interp.filters.keywords.map((k) => (
                <Badge key={`k-${k}`} variant="outline">
                  {k}
                </Badge>
              ))}
              {interp.filters.tags.map((k) => (
                <Badge key={`t-${k}`} variant="secondary">
                  #{k}
                </Badge>
              ))}
              {interp.filters.media_type && <Badge variant="outline">{interp.filters.media_type}</Badge>}
              {interp.filters.region && <Badge variant="outline">{interp.filters.region}</Badge>}
            </div>
          </div>
        )}

        <div className="flex-1 overflow-y-auto space-y-4 pr-1">
          {recommendInput && (
            <section className="space-y-2">
              <h3 className="text-sm font-medium flex items-center gap-1.5">
                <Sparkles className="h-4 w-4 text-primary" />
                {t("geo.sourceSelector.recommended")}
              </h3>
              {recommend.isLoading && (
                <p className="text-xs text-muted-foreground">{t("geo.sourceSelector.loading")}</p>
              )}
              {recommend.isError && (
                <p className="text-xs text-destructive">
                  {t("geo.sourceSelector.error", { message: (recommend.error as Error).message })}
                </p>
              )}
              {recommend.data && recommend.data.recommendations.length === 0 && (
                <p className="text-xs text-muted-foreground">{t("geo.sourceSelector.recommendedEmpty")}</p>
              )}
              {recommend.data?.recommendations.map((r) => (
                <SourceRow
                  key={r.source_id}
                  source={r}
                  checked={picked.has(r.source_id)}
                  onToggle={() => toggle(r.source_id)}
                  reason={r.reason}
                />
              ))}
              {recommend.data && recommend.data.unregistered_domains.length > 0 && (
                <p className="text-xs text-muted-foreground">
                  {t("geo.sourceSelector.unregistered")}:{" "}
                  {recommend.data.unregistered_domains.map((d) => `${d.domain}(${d.article_count})`).join(", ")}
                </p>
              )}
            </section>
          )}

          <section className="space-y-2">
            <h3 className="text-sm font-medium">{t("geo.sourceSelector.fromLibrary")}</h3>
            {list.isLoading && (
              <p className="text-xs text-muted-foreground">{t("geo.sourceSelector.loading")}</p>
            )}
            {list.isError && (
              <p className="text-xs text-destructive" role="alert">
                {t("geo.sourceSelector.error", { message: (list.error as Error).message })}
              </p>
            )}
            {!list.isLoading && !list.isError && others.length === 0 && (
              <p className="text-xs text-muted-foreground">{t("geo.sourceSelector.noResults")}</p>
            )}
            {others.map((s) => (
              <SourceRow key={s.source_id} source={s} checked={picked.has(s.source_id)} onToggle={() => toggle(s.source_id)} />
            ))}
            {list.data?.has_more && (
              <Button variant="outline" size="sm" onClick={() => setPage((p) => p + 1)} disabled={list.isFetching}>
                {t("geo.sourceSelector.more")}
              </Button>
            )}
          </section>
        </div>

        <div className="border-t pt-3 space-y-2">
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>{t("geo.sourceSelector.selected", { count: picked.size })}</span>
            <button type="button" className="underline" onClick={() => setPicked(new Set())}>
              {t("geo.sourceSelector.clear")}
            </button>
          </div>
          <div className="flex items-center justify-between gap-2">
            {returnContext ? (
              <Button variant="ghost" size="sm" onClick={openLibrary}>
                <ExternalLink className="h-4 w-4 mr-1" />
                {t("geo.sourceSelector.openLibrary")}
              </Button>
            ) : (
              <span />
            )}
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                {t("geo.sourceSelector.cancel")}
              </Button>
              <Button onClick={apply} disabled={picked.size === 0}>
                {t("geo.sourceSelector.apply", { count: picked.size })}
              </Button>
            </div>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}

/** 「おすすめ 12ソースを使用中 [変更する]」の要約表示（Theme Explorer / Monitoring / Research Studio共通） */
export function SourceSetSummary({
  names,
  count,
  isRecommended,
  onChange,
  maxNames = 5,
  emptyLabel,
}: {
  names: string[];
  count: number;
  isRecommended?: boolean;
  onChange?: () => void;
  maxNames?: number;
  /** 未選択(count=0)のときの表示文。省略時は「Sourceは未選択です」 */
  emptyLabel?: string;
}) {
  const { t } = useLanguage();
  return (
    <div className="rounded-md border border-border/60 p-3 text-sm space-y-1.5" data-testid="source-set-summary">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">
          {count === 0
            ? (emptyLabel ?? t("geo.sourceSummary.none"))
            : isRecommended
              ? t("geo.sourceSummary.recommendedUsing", { count })
              : t("geo.sourceSummary.using", { count })}
        </span>
        {onChange && (
          <Button variant="outline" size="sm" onClick={onChange}>
            {t("geo.sourceSummary.change")}
          </Button>
        )}
      </div>
      {names.length > 0 && (
        <p className="text-xs text-muted-foreground">
          {names.slice(0, maxNames).join(" / ")}
          {names.length > maxNames && ` ${t("geo.sourceSummary.more", { count: names.length - maxNames })}`}
        </p>
      )}
    </div>
  );
}
