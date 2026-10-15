// Intelligence AI: プリセット調査の画面部品（一覧・入力フォーム・結果・追加回答）。
// 画面は表示と入力だけを担当し、検索・権限・根拠の検証はすべてバックエンド（geo_intelligence_ai_preset_service）が行う。
import { useEffect, useState } from "react";
import { Link } from "@tanstack/react-router";
import {
  AlertTriangle,
  Diff,
  ExternalLink,
  FlaskConical,
  History,
  Loader2,
  Radar,
  Scale,
  Search,
  Users,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { KnowledgeItemDetailSheet } from "@/components/geo/knowledge-detail-sheets";
import { useKnowledgeItemDetail } from "@/lib/geoKnowledgeApi";
import { useLanguage } from "@/lib/i18n";
import type {
  ClaimType,
  PresetCitation,
  PresetConditions,
  PresetDefinition,
  PresetFollowupResult,
  PresetFormState,
  PresetRunResult,
  PresetSourceStatus,
  SourceAvailability,
} from "@/lib/geoIntelligencePresetApi";
import { useReportDetail } from "@/lib/geoIntelligencePresetApi";
import type { GeoReportSummary } from "@/lib/geoKnowledgeApi";

const PRESET_ICONS: Record<string, LucideIcon> = {
  history: History,
  users: Users,
  diff: Diff,
  scale: Scale,
  flask: FlaskConical,
  radar: Radar,
};

export function presetIcon(name: string): LucideIcon {
  return PRESET_ICONS[name] ?? Search;
}

/** フォームの初期条件（項目の初期値と、検索ソース選択の初期値）。 */
export function defaultConditions(def: PresetDefinition): PresetConditions {
  const c: PresetConditions = {};
  def.fields.forEach((f) => {
    c[f.key] = f.default ?? "";
  });
  def.optional_sources.forEach((o) => {
    c[`include_${o.key}`] = o.default;
  });
  return c;
}

// ---- 一覧（左サイドバー） ----
export function PresetList({
  presets,
  activeId,
  disabled,
  onSelect,
  availability,
}: {
  presets: PresetDefinition[];
  activeId: string | null;
  disabled: boolean;
  onSelect: (p: PresetDefinition) => void;
  availability: Record<string, SourceAvailability>;
}) {
  const { t } = useLanguage();
  const entries = Object.entries(availability);
  return (
    <div className="mt-5">
      <div className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground mb-2 px-2">
        {t("geo.ia.preset.heading")}
      </div>
      <div className="space-y-1">
        {presets.map((p) => {
          const Icon = presetIcon(p.icon);
          return (
            <button
              key={p.id}
              type="button"
              disabled={disabled}
              onClick={() => onSelect(p)}
              className={`w-full text-left rounded-lg px-2.5 py-2 border transition-colors disabled:opacity-50 ${
                activeId === p.id
                  ? "border-primary/50 bg-primary/10"
                  : "border-transparent hover:bg-muted hover:border-border"
              }`}
            >
              <div className="flex items-start gap-2">
                <Icon className="h-4 w-4 text-primary mt-0.5 shrink-0" />
                <div className="min-w-0">
                  <div className="text-sm font-medium leading-snug">{p.title}</div>
                  <div className="text-[11px] text-muted-foreground leading-snug mt-0.5">{p.description}</div>
                </div>
              </div>
            </button>
          );
        })}
      </div>
      {entries.length > 0 && (
        <details className="mt-3 px-2 text-[11px] text-muted-foreground">
          <summary className="cursor-pointer select-none">{t("geo.ia.preset.sourceStatus")}</summary>
          <ul className="mt-1.5 space-y-1">
            {entries.map(([key, a]) => (
              <li key={key} className="flex items-start gap-1.5">
                <span className={a.available ? "text-emerald-600" : "text-destructive"}>{a.available ? "●" : "○"}</span>
                <span>
                  {t(`geo.ia.source.${key}`)}
                  {!a.available && a.reason ? `：${a.reason}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

// ---- 初期画面のカード ----
export function PresetCards({
  presets,
  disabled,
  onSelect,
}: {
  presets: PresetDefinition[];
  disabled: boolean;
  onSelect: (p: PresetDefinition) => void;
}) {
  const { t } = useLanguage();
  if (presets.length === 0) return null;
  return (
    <div className="space-y-2">
      <div className="text-xs text-muted-foreground">{t("geo.ia.preset.cardsHint")}</div>
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-2.5">
        {presets.map((p) => {
          const Icon = presetIcon(p.icon);
          return (
            <button
              key={p.id}
              type="button"
              disabled={disabled}
              onClick={() => onSelect(p)}
              className="text-left rounded-xl border border-border bg-background px-4 py-3 hover:border-primary/50 hover:bg-primary/5 transition-colors disabled:opacity-50"
            >
              <Icon className="h-4.5 w-4.5 text-primary mb-1.5" />
              <div className="text-sm font-medium">{p.title}</div>
              <div className="text-xs text-muted-foreground mt-0.5 leading-snug">{p.description}</div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ---- 入力フォーム（チャット内） ----
export function PresetFormCard({
  def,
  state,
  onRun,
  onDraftChange,
}: {
  def: PresetDefinition;
  state: PresetFormState;
  onRun: (conditions: PresetConditions) => void;
  // 入力中（まだ実行していない）の内容を親へ伝える。自由入力から条件を読み取る際、入力済みの項目を失わないため
  onDraftChange?: (values: PresetConditions) => void;
}) {
  const { t } = useLanguage();
  const [values, setValues] = useState<PresetConditions>(() => ({ ...defaultConditions(def), ...state.conditions }));

  useEffect(() => {
    if (state.status === "editing") onDraftChange?.(values);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [values]);

  // 自由入力から条件が読み取られたとき（version更新）に、入力欄へ反映する
  useEffect(() => {
    setValues({ ...defaultConditions(def), ...state.conditions });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.version]);

  const set = (key: string, v: string | boolean) => setValues((prev) => ({ ...prev, [key]: v }));
  const missing = def.fields.filter((f) => f.required && !String(values[f.key] ?? "").trim());
  const locked = state.status !== "editing";

  return (
    <div className="mt-3 rounded-xl border border-border bg-background p-4 space-y-3 max-w-[640px]">
      {state.status === "superseded" && (
        <p className="text-xs text-muted-foreground">{t("geo.ia.preset.superseded")}</p>
      )}
      {def.fields.map((f) => (
        <div key={f.key} className="space-y-1">
          <Label className="text-xs">
            {f.label}
            {f.required && <span className="text-destructive ml-1">*</span>}
          </Label>
          {f.type === "select" ? (
            <select
              disabled={locked}
              value={String(values[f.key] ?? "")}
              onChange={(e) => set(f.key, e.target.value)}
              className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm disabled:opacity-60"
            >
              {f.options.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          ) : f.type === "date" ? (
            <Input type="date" disabled={locked} value={String(values[f.key] ?? "")} onChange={(e) => set(f.key, e.target.value)} />
          ) : f.type === "textarea" ? (
            <Textarea
              disabled={locked}
              rows={3}
              value={String(values[f.key] ?? "")}
              placeholder={f.placeholder ?? undefined}
              onChange={(e) => set(f.key, e.target.value)}
            />
          ) : (
            <Input
              disabled={locked}
              value={String(values[f.key] ?? "")}
              placeholder={f.placeholder ?? undefined}
              onChange={(e) => set(f.key, e.target.value)}
            />
          )}
        </div>
      ))}
      {def.optional_sources.length > 0 && (
        <div className="space-y-1.5 pt-1">
          <Label className="text-xs">{t("geo.ia.preset.extraSources")}</Label>
          {def.optional_sources.map((o) => (
            <label key={o.key} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                disabled={locked}
                checked={Boolean(values[`include_${o.key}`])}
                onChange={(e) => set(`include_${o.key}`, e.target.checked)}
              />
              {o.label}
            </label>
          ))}
          <p className="text-[11px] text-muted-foreground">{t("geo.ia.preset.extraSourcesNote")}</p>
        </div>
      )}
      <div className="flex items-center gap-3 pt-1">
        <Button disabled={locked || missing.length > 0} onClick={() => onRun(values)}>
          {state.status === "running" ? <Loader2 className="h-4 w-4 mr-1.5 animate-spin" /> : null}
          {state.status === "running" ? t("geo.ia.preset.running") : t("geo.ia.preset.run")}
        </Button>
        {missing.length > 0 && !locked && (
          <span className="text-xs text-muted-foreground">
            {t("geo.ia.preset.missing", { fields: missing.map((f) => f.label).join("、") })}
          </span>
        )}
      </div>
    </div>
  );
}

// ---- 結果 ----
const CLAIM_BADGE: Record<ClaimType, { key: string; cls: string }> = {
  source_supported: { key: "geo.ia.claim.supported", cls: "border-emerald-600/40 bg-emerald-600/10 text-emerald-700 dark:text-emerald-400" },
  cross_source_synthesis: { key: "geo.ia.claim.synthesis", cls: "border-sky-600/40 bg-sky-600/10 text-sky-700 dark:text-sky-400" },
  ai_inference: { key: "geo.ia.claim.inference", cls: "border-amber-600/40 bg-amber-600/10 text-amber-700 dark:text-amber-400" },
};

function ClaimLine({ text, claimType, refs, citations }: { text: string; claimType: ClaimType; refs: string[]; citations: PresetCitation[] }) {
  const { t } = useLanguage();
  const badge = CLAIM_BADGE[claimType];
  return (
    <li className="text-sm leading-relaxed">
      <span className={`mr-1.5 align-middle text-[10px] px-1.5 py-0.5 rounded-full border ${badge.cls}`}>{t(badge.key)}</span>
      <span className="whitespace-pre-wrap">{text}</span>
      {refs.map((r) => {
        const c = citations.find((x) => x.ref_id === r);
        return (
          <span
            key={r}
            title={c?.title ?? undefined}
            className="ml-1 text-[10px] px-1 py-0.5 rounded border border-border bg-muted/60 text-muted-foreground align-middle"
          >
            {r}
          </span>
        );
      })}
    </li>
  );
}

const STATUS_STYLE: Record<PresetSourceStatus["status"], string> = {
  searched: "text-emerald-700 dark:text-emerald-400",
  skipped: "text-muted-foreground",
  not_used: "text-muted-foreground",
  no_access: "text-amber-700 dark:text-amber-400",
  unavailable: "text-destructive",
  error: "text-destructive",
};

export function SourceStatusList({ rows }: { rows: PresetSourceStatus[] }) {
  const { t } = useLanguage();
  if (rows.length === 0) return null;
  return (
    <details className="text-xs">
      <summary className="cursor-pointer select-none text-muted-foreground">{t("geo.ia.result.sources")}</summary>
      <ul className="mt-1.5 space-y-1">
        {rows.map((r) => (
          <li key={r.source} className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-medium">{t(`geo.ia.source.${r.source}`)}</span>
            <span className={STATUS_STYLE[r.status]}>
              {t(`geo.ia.status.${r.status}`)}
              {r.status === "searched" ? `（${r.count}件）` : ""}
            </span>
            {r.reason && <span className="text-muted-foreground">{r.reason}</span>}
          </li>
        ))}
      </ul>
    </details>
  );
}

/** 参照資料のタイトル。種別ごとに、詳細を開く／該当画面へ移動する／外部サイトを開く。 */
function CitationTitle({ c, onOpenDetail }: { c: PresetCitation; onOpenDetail: (c: PresetCitation) => void }) {
  const title = c.title || c.url || c.id;
  const linkCls = "hover:underline inline-flex items-center gap-1 text-left";
  if (c.source_type === "report" || c.source_type === "knowledge_item") {
    return (
      <button type="button" className={`${linkCls} text-primary`} onClick={() => onOpenDetail(c)}>
        {title}
      </button>
    );
  }
  if (c.source_type === "contact") {
    return (
      <Link to="/geo/external-network/contacts/$contactId" params={{ contactId: c.id }} className={`${linkCls} text-primary`}>
        {title}
      </Link>
    );
  }
  if (c.source_type === "contact_log") {
    return (
      <Link to="/geo/external-network/logs/$logId" params={{ logId: c.id }} className={`${linkCls} text-primary`}>
        {title}
      </Link>
    );
  }
  if (c.source_type === "source") {
    return (
      <Link to="/geo/source-library/$sourceId" params={{ sourceId: c.id }} className={`${linkCls} text-primary`}>
        {title}
      </Link>
    );
  }
  if (c.source_type === "monitoring_theme" || c.source_type === "monitoring_alert") {
    return (
      <Link to="/geo/monitoring-themes" className={`${linkCls} text-primary`}>
        {title}
      </Link>
    );
  }
  if (c.url) {
    return (
      <a href={c.url} target="_blank" rel="noreferrer noopener" className={linkCls}>
        {title}
        <ExternalLink className="h-3 w-3" />
      </a>
    );
  }
  return <span>{title}</span>;
}

/** レポートの読み取り専用の概要（削除などの操作は出さない。操作はレポートDBの画面で行う）。 */
function ReportPreview({ report }: { report: GeoReportSummary }) {
  const { t } = useLanguage();
  const row = (label: string, value: React.ReactNode) =>
    value ? (
      <div className="grid grid-cols-[8rem_1fr] gap-2 text-sm">
        <dt className="text-muted-foreground">{label}</dt>
        <dd>{value}</dd>
      </div>
    ) : null;
  return (
    <div className="space-y-3">
      <dl className="space-y-1.5">
        {row(t("geo.ia.report.status"), report.review_status === "approved" ? t("geo.ia.report.reviewed") : t("geo.ia.report.notReviewed"))}
        {row(t("geo.ia.report.date"), report.uploaded_at?.slice(0, 10))}
        {row(t("geo.ia.report.author"), report.author)}
        {row(t("geo.ia.report.department"), report.owner_department)}
        {row(t("geo.ia.report.type"), report.report_type)}
        {row(t("geo.ia.report.regions"), report.country_region?.join("、"))}
        {row(t("geo.ia.report.actors"), report.key_actors?.join("、"))}
        {row(t("geo.ia.report.topics"), report.topics?.join("、"))}
      </dl>
      {report.summary && <p className="text-sm whitespace-pre-wrap border-t border-border pt-3">{report.summary}</p>}
      <Link to="/geo/knowledge-search" className="text-xs text-primary hover:underline">
        {t("geo.ia.report.openDb")}
      </Link>
    </div>
  );
}

/** レポート・ナレッジの詳細（取得は閲覧者の権限で行われる）。 */
function CitationDetail({ citation, onClose }: { citation: PresetCitation | null; onClose: () => void }) {
  const { t } = useLanguage();
  const isReport = citation?.source_type === "report";
  const isKnowledge = citation?.source_type === "knowledge_item";
  const report = useReportDetail(isReport ? citation!.id : "");
  const knowledge = useKnowledgeItemDetail(isKnowledge ? citation!.id : "");
  if (isKnowledge) {
    return <KnowledgeItemDetailSheet item={knowledge.data ?? null} onClose={onClose} />;
  }
  return (
    <Dialog open={isReport} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-3xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{citation?.title ?? t("geo.ia.result.citations")}</DialogTitle>
        </DialogHeader>
        {report.isLoading && <Loader2 className="h-4 w-4 animate-spin" />}
        {report.error && <p className="text-sm text-destructive">{(report.error as Error).message}</p>}
        {report.data && <ReportPreview report={report.data} />}
      </DialogContent>
    </Dialog>
  );
}

export function CitationList({ citations }: { citations: PresetCitation[] }) {
  const { t } = useLanguage();
  const [detail, setDetail] = useState<PresetCitation | null>(null);
  if (citations.length === 0) return null;
  return (
    <div className="space-y-1.5">
      <div className="text-xs font-semibold">{t("geo.ia.result.citations")}</div>
      <ul className="space-y-1.5">
        {citations.map((c) => {
          const profile = c.meta.source_profile as { name?: string; media_type?: string; is_primary?: boolean; reliability?: string } | undefined;
          const stype = t(`geo.ia.stype.${c.source_type}`);
          return (
            <li key={c.ref_id} className="text-xs border border-border rounded-md px-2.5 py-1.5 bg-background">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="font-mono text-[10px] px-1 rounded bg-muted">{c.ref_id}</span>
                <Badge variant={c.origin === "web" ? "outline" : "secondary"} className="text-[10px]">
                  {c.origin === "web" ? t("geo.ia.origin.web") : `${t("geo.ia.origin.internal")}・${stype.startsWith("geo.ia.") ? c.source_label : stype}`}
                </Badge>
                {c.meta.ai_provisional === true && <Badge variant="outline" className="text-[10px]">{t("geo.ia.badge.aiProvisional")}</Badge>}
                {(c.origin === "web" || c.source_type === "external_event" || c.meta.evidence_status === "unverified") && (
                  <Badge variant="outline" className="text-[10px]">{t("geo.ia.badge.unverified")}</Badge>
                )}
                {c.meta.registered === true && <Badge variant="outline" className="text-[10px]">{t("geo.ia.badge.registered")}</Badge>}
                {c.meta.inferred === true && <Badge variant="outline" className="text-[10px]">{t("geo.ia.badge.aiInferred")}</Badge>}
                {typeof c.meta.review_status === "string" && c.meta.review_status !== "approved" && (
                  <Badge variant="outline" className="text-[10px]">{t("geo.ia.badge.notReviewed")}</Badge>
                )}
                {c.meta.is_primary === true && <Badge variant="outline" className="text-[10px]">{t("geo.ia.badge.primary")}</Badge>}
                {profile?.is_primary && <Badge variant="outline" className="text-[10px]">{t("geo.ia.badge.primary")}</Badge>}
                {typeof c.meta.body_status === "string" && (
                  <Badge variant="outline" className="text-[10px]">
                    {c.meta.body_status === "ok" ? t("geo.ia.badge.bodyOk") : t("geo.ia.badge.bodyNone")}
                  </Badge>
                )}
                <span className="text-muted-foreground">{c.date ?? t("geo.ia.result.dateUnknown")}</span>
              </div>
              <div className="mt-0.5">
                <CitationTitle c={c} onOpenDetail={setDetail} />
                {profile?.name && (
                  <span className="ml-2 text-muted-foreground">
                    {t("geo.ia.result.fromSource", { name: profile.name })}
                    {profile.reliability ? `（${profile.reliability}）` : ""}
                  </span>
                )}
              </div>
              {typeof c.meta.caution === "string" && c.meta.caution && (
                <div className="mt-0.5 text-amber-700 dark:text-amber-400">{c.meta.caution}</div>
              )}
            </li>
          );
        })}
      </ul>
      <CitationDetail citation={detail} onClose={() => setDetail(null)} />
    </div>
  );
}

function Warnings({ warnings }: { warnings: string[] }) {
  if (warnings.length === 0) return null;
  return (
    <ul className="space-y-1">
      {warnings.map((w, i) => (
        <li key={i} className="flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-400">
          <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          {w}
        </li>
      ))}
    </ul>
  );
}

export function PresetResultCard({ result }: { result: PresetRunResult }) {
  const { t } = useLanguage();
  return (
    <div className="mt-2 space-y-4 max-w-[760px]">
      {result.assumptions.length > 0 && (
        <ul className="text-xs rounded-md border border-border bg-muted/40 px-3 py-2 space-y-0.5">
          {result.assumptions.map((a, i) => (
            <li key={i}>{a}</li>
          ))}
        </ul>
      )}
      {result.summary && <p className="text-sm leading-relaxed whitespace-pre-wrap">{result.summary}</p>}
      {result.insufficient_evidence && result.sections.every((s) => s.items.length === 0) && (
        <p className="text-sm text-muted-foreground">{t("geo.ia.result.insufficient")}</p>
      )}
      {result.sections
        .filter((s) => s.items.length > 0)
        .map((s) => (
          <div key={s.key} className="space-y-1.5">
            <h4 className="text-sm font-semibold">{s.title}</h4>
            <ul className="space-y-1.5 list-none">
              {s.items.map((it, i) => (
                <ClaimLine key={i} text={it.text} claimType={it.claim_type} refs={it.refs} citations={result.citations} />
              ))}
            </ul>
          </div>
        ))}
      <Warnings warnings={result.warnings} />
      <CitationList citations={result.citations} />
      <SourceStatusList rows={result.source_status} />
      <p className="text-[11px] text-muted-foreground">{t("geo.ia.result.followupHint")}</p>
    </div>
  );
}

export function PresetFollowupCard({ result }: { result: PresetFollowupResult }) {
  const { t } = useLanguage();
  return (
    <div className="mt-2 space-y-3 max-w-[760px]">
      <p className="text-sm leading-relaxed whitespace-pre-wrap">{result.answer}</p>
      {result.claims.length > 0 && (
        <ul className="space-y-1.5 list-none">
          {result.claims.map((c, i) => (
            <ClaimLine key={i} text={c.text} claimType={c.claim_type} refs={c.refs} citations={result.citations} />
          ))}
        </ul>
      )}
      {result.new_search && <p className="text-[11px] text-muted-foreground">{t("geo.ia.result.newSearch")}</p>}
      <Warnings warnings={result.warnings} />
      <CitationList citations={result.citations} />
      {result.new_search && <SourceStatusList rows={result.source_status} />}
    </div>
  );
}
