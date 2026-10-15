import { API_BASE as API_BASE_URL } from "@/lib/apiBase";
import { Badge } from "@/components/ui/badge";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { EpistemicTypeBadge, RetrievalStatusBadge } from "@/components/geo/geo-epistemic-badge";
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { getIosAuthHeaders } from "@/lib/geoIosAuthHeader";
import {
  INTERPRETIVE_KNOWLEDGE_TYPES,
  type GeoExpertComment,
  isAiApproved,
  useGeoEntityContext,
  useGeoKnowledgeRelationships,
  type GeoEntity,
  type GeoKnowledgeItem,
} from "@/lib/geoKnowledgeApi";
import { useLanguage } from "@/lib/i18n";

// ナレッジ検索の結果の「詳細」を、右から開くパネルで見せる（エンティティ／ナレッジ項目）。
// 検索の結果に含まれる情報をそのまま表示する（追加の権限判断はしない。見える範囲は検索側で制御済み）。

// structured_content の主なキーの日本語名。無いキーは、_ を空白にして表示する
const KEY_LABELS: Record<string, string> = {
  core_interests: "主な利害・関心",
  core_profile: "基本プロファイル",
  current_posture: "現在の姿勢",
  decision_patterns: "意思決定のパターン",
  default_importance: "既定の重要度",
  issue_positions: "論点ごとの立場",
  directional_relationship_perceptions: "他の主体への見方",
  coverage_notes: "この資料の対象範囲と限界",
  constraints: "制約",
  decision_authority: "決定権限",
  external_dependencies: "外部への依存",
  historical_decision_patterns: "過去の決定パターン",
  internal_stakeholders: "内部の関係者",
  summary: "要約",
  description: "説明",
  mechanism: "メカニズム",
  precedent: "先例",
  outcome: "結果",
  lessons: "教訓",
};
// 画面に出しても利用者に意味が無い内部の参照キー
const HIDDEN_KEYS = new Set(["entity_id", "evidence_ids", "source_ids", "claim_ids", "document_ids", "recent_developments"]);

function label(key: string): string {
  return KEY_LABELS[key] ?? key.replace(/_/g, " ");
}

/** 入れ子のJSONを、読みやすい見出し・箇条書き・段落で表示する */
export function StructuredView({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "string")
    return <p className="text-sm leading-relaxed whitespace-pre-wrap">{value}</p>;
  if (typeof value === "number" || typeof value === "boolean")
    return <p className="text-sm tabular-nums">{String(value)}</p>;
  if (Array.isArray(value)) {
    const items = value.filter((v) => v !== null && v !== undefined && v !== "");
    if (items.length === 0) return null;
    if (items.every((v) => typeof v === "string" || typeof v === "number")) {
      return (
        <ul className="list-disc pl-5 space-y-0.5 text-sm">
          {items.map((v, i) => (
            <li key={i}>{String(v)}</li>
          ))}
        </ul>
      );
    }
    return (
      <div className="space-y-2">
        {items.map((v, i) => (
          <div key={i} className="rounded-md border border-border/60 p-2.5">
            <StructuredView value={v} depth={depth + 1} />
          </div>
        ))}
      </div>
    );
  }
  const entries = Object.entries(value as Record<string, unknown>).filter(
    ([k, v]) => !HIDDEN_KEYS.has(k) && v !== null && v !== undefined && v !== "" && !(Array.isArray(v) && v.length === 0),
  );
  if (entries.length === 0) return null;
  return (
    <dl className="space-y-2.5">
      {entries.map(([k, v]) => (
        <div key={k}>
          <dt className={depth === 0 ? "text-xs font-semibold text-primary" : "text-xs font-medium text-muted-foreground"}>
            {label(k)}
          </dt>
          <dd className="mt-0.5">
            <StructuredView value={v} depth={depth + 1} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

const API_BASE = API_BASE_URL;

/** AI暫定承認の注意と、AIの評価（出典つき）。人の確認ボタン付き */
export function AiApprovalNotice({ item }: { item: GeoKnowledgeItem }) {
  const { t } = useLanguage();
  const queryClient = useQueryClient();
  const [state, setState] = useState<"idle" | "busy" | "done" | string>("idle");
  if (!isAiApproved(item) && state !== "done") return null;
  const confirm = async () => {
    setState("busy");
    try {
      const res = await fetch(`${API_BASE}/api/geo/knowledge/items/${item.knowledge_item_id}/confirm-ai-approval`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getIosAuthHeaders() },
        body: JSON.stringify({ note: "" }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(typeof d.detail === "string" ? d.detail : `API error ${res.status}`);
      }
      setState("done");
      queryClient.invalidateQueries({ queryKey: ["geoKnowledgeItems"] });
    } catch (e) {
      setState(t("geo.knowledge.ai.confirmFailed", { message: (e as Error).message }));
    }
  };
  return (
    <section className="rounded-md border border-geo-amber/60 bg-geo-amber-soft p-3 space-y-2" data-testid="ai-approval-notice">
      {state === "done" ? (
        <p className="text-sm">{t("geo.knowledge.ai.confirmed")}</p>
      ) : (
        <>
          <div className="flex items-center gap-2">
            <Badge className="bg-geo-amber text-white hover:bg-geo-amber">{t("geo.knowledge.ai.badge")}</Badge>
            <span className="text-xs text-muted-foreground">{item.approved_by}</span>
          </div>
          <p className="text-sm">{t("geo.knowledge.ai.notice")}</p>
          {item.confidence_rationale && (
            <div className="text-xs rounded bg-background/70 p-2">
              <p className="font-medium mb-0.5">{t("geo.knowledge.ai.evaluation")}</p>
              <p className="whitespace-pre-wrap leading-relaxed break-words">{item.confidence_rationale}</p>
            </div>
          )}
          <div className="flex items-center gap-2 flex-wrap">
            <Button size="sm" variant="outline" disabled={state === "busy"} onClick={confirm}>
              {state === "busy" ? t("geo.knowledge.ai.confirming") : t("geo.knowledge.ai.confirm")}
            </Button>
            <span className="text-[11px] text-muted-foreground">{t("geo.knowledge.ai.reviseHint")}</span>
          </div>
          {!["idle", "busy", "done"].includes(state) && <p className="text-xs text-destructive" role="alert">{state}</p>}
        </>
      )}
    </section>
  );
}

function Field({ name, children }: { name: string; children: React.ReactNode }) {
  if (children === null || children === undefined || children === "" || children === false) return null;
  return (
    <div className="flex gap-3 text-sm">
      <span className="w-28 shrink-0 text-xs text-muted-foreground pt-0.5">{name}</span>
      <span className="min-w-0 flex-1">{children}</span>
    </div>
  );
}

/** URLからドメイン名を取り出す（不正なURLでも画面を壊さない） */
function hostOf(u: string): string {
  try {
    return new URL(u).hostname.replace(/^www\./, "");
  } catch {
    return u;
  }
}

interface RecentDevelopments {
  as_of?: string;
  items?: { date?: string; event?: string; bearing?: string; sources?: string[] }[];
  posture_note?: string;
  no_notable_change?: boolean;
}

/** 月次更新の「最新動向」。基本の人物像とは別の部分として、日付・出典つきで表示する */
export function RecentDevelopmentsSection({ item }: { item: GeoKnowledgeItem }) {
  const { t } = useLanguage();
  if (!INTERPRETIVE_KNOWLEDGE_TYPES.includes(item.knowledge_type)) return null;
  const rec = (item.structured_content as Record<string, unknown>)?.recent_developments as RecentDevelopments | undefined;
  return (
    <section className="space-y-2" data-testid="recent-developments">
      <h3 className="text-sm font-medium">{t("geo.knowledge.recent.title")}</h3>
      {!rec ? (
        <p className="text-xs text-muted-foreground">{t("geo.knowledge.recent.empty")}</p>
      ) : (
        <div className="rounded-md border border-border/60 p-3 space-y-2">
          <p className="text-[11px] text-muted-foreground">
            {t("geo.knowledge.recent.asOf", { date: rec.as_of ?? "—" })}
            <br />
            {t("geo.knowledge.recent.note")}
          </p>
          {(rec.items ?? []).length === 0 && <p className="text-sm">{t("geo.knowledge.recent.noChange")}</p>}
          <ul className="space-y-2">
            {(rec.items ?? []).map((e, i) => (
              <li key={i} className="text-sm">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="tabular-nums text-xs text-muted-foreground">{e.date}</span>
                  {e.bearing && (
                    <Badge variant="outline" className="text-[10px]">
                      {t(`geo.knowledge.recent.bearing.${e.bearing}`)}
                    </Badge>
                  )}
                </div>
                <p>{e.event}</p>
                <div className="flex flex-wrap gap-x-3">
                  {(e.sources ?? []).map((u) => (
                    <a key={u} href={u} target="_blank" rel="noreferrer" className="text-[11px] text-primary underline underline-offset-2 break-all">
                      {hostOf(u)}
                    </a>
                  ))}
                </div>
              </li>
            ))}
          </ul>
          {rec.posture_note && (
            <p className="text-xs rounded bg-muted/50 p-2">
              <span className="font-medium">{t("geo.knowledge.recent.posture")}: </span>
              {rec.posture_note}
            </p>
          )}
        </div>
      )}
    </section>
  );
}

/** 専門家のコメント（一覧と投稿）。コメントだけでは内容も状態も変わらない */
export function ExpertComments({ itemId }: { itemId: string }) {
  const { t } = useLanguage();
  const [comments, setComments] = useState<GeoExpertComment[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/geo/knowledge/items/${itemId}/expert-comments`);
      if (res.ok) setComments(((await res.json()) as { comments: GeoExpertComment[] }).comments);
    } catch {
      /* 一覧が読めなくても、投稿欄は使えるようにする */
    }
  };
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [itemId]);
  const submit = async () => {
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/geo/knowledge/items/${itemId}/expert-comments`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getIosAuthHeaders() },
        body: JSON.stringify({ comment: text.trim() }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(typeof d.detail === "string" ? d.detail : `API error ${res.status}`);
      }
      setText("");
      await load();
    } catch (e) {
      setError(t("geo.knowledge.comment.failed", { message: (e as Error).message }));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="space-y-2" data-testid="expert-comments">
      <h3 className="text-sm font-medium">{t("geo.knowledge.comment.title")}</h3>
      {comments.length === 0 && <p className="text-xs text-muted-foreground">{t("geo.knowledge.comment.none")}</p>}
      <ul className="space-y-1.5">
        {comments.map((c, i) => (
          <li key={i} className="rounded-md border border-border/60 p-2 text-sm">
            <p className="whitespace-pre-wrap break-words">{c.comment}</p>
            <p className="text-[11px] text-muted-foreground mt-0.5">
              {c.author ?? "—"} {c.created_at ? `· ${c.created_at.slice(0, 10)}` : ""}
            </p>
          </li>
        ))}
      </ul>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={3}
        placeholder={t("geo.knowledge.comment.placeholder")}
        className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
      />
      <div className="flex items-center gap-2">
        <Button size="sm" variant="secondary" disabled={busy || !text.trim()} onClick={submit}>
          {t("geo.knowledge.comment.submit")}
        </Button>
        <span className="text-[11px] text-muted-foreground">{t("geo.knowledge.comment.note")}</span>
      </div>
      {error && <p className="text-xs text-destructive" role="alert">{error}</p>}
    </section>
  );
}

export function KnowledgeItemDetailSheet({
  item,
  reason,
  onClose,
}: {
  item: GeoKnowledgeItem | null;
  reason?: string;
  onClose: () => void;
}) {
  const { t } = useLanguage();
  const extra = (item ?? {}) as unknown as Record<string, unknown>;
  const asOf = typeof extra.as_of_date === "string" ? extra.as_of_date : null;
  return (
    <Sheet open={!!item} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full sm:max-w-2xl overflow-y-auto">
        {item && (
          <>
            <SheetHeader>
              <div className="flex items-center gap-2 flex-wrap">
                <Badge variant="outline">{item.knowledge_type}</Badge>
                <Badge variant={item.status === "active" ? "default" : "secondary"}>{item.status}</Badge>
                {item.information_class && <EpistemicTypeBadge informationClass={item.information_class} />}
                {item.retrieval_status && <RetrievalStatusBadge retrievalStatus={item.retrieval_status} />}
                {item.is_demo && <Badge variant="outline">demo</Badge>}
              </div>
              <SheetTitle className="text-editorial text-2xl leading-snug">{item.title}</SheetTitle>
              <SheetDescription>{t("geo.knowledge.detail.itemHint")}</SheetDescription>
            </SheetHeader>
            <div className="mt-4 space-y-5" data-testid="knowledge-item-detail">
              {reason && (
                <p className="rounded-md bg-primary/5 border border-primary/20 p-3 text-sm">
                  <span className="font-medium text-primary">{t("geo.knowledge.detail.whyMatched")}: </span>
                  {reason}
                </p>
              )}
              <AiApprovalNotice item={item} />
              {INTERPRETIVE_KNOWLEDGE_TYPES.includes(item.knowledge_type) && (
                <p className="rounded-md border border-border/60 bg-muted/40 p-3 text-xs" data-testid="interpretation-notice">
                  {t("geo.knowledge.interp.notice")}
                </p>
              )}
              {item.summary && <p className="text-sm leading-relaxed">{item.summary}</p>}
              <section className="space-y-1.5 rounded-md border border-border/60 p-3">
                <Field name={t("geo.knowledge.detail.confidence")}>{item.confidence}</Field>
                <Field name={t("geo.knowledge.detail.asOf")}>{asOf}</Field>
                <Field name={t("geo.knowledge.detail.version")}>{`v${item.version}`}</Field>
                <Field name={t("geo.knowledge.detail.origin")}>{item.origin}</Field>
                <Field name={t("geo.knowledge.detail.lastConfirmed")}>{item.last_confirmed_at?.slice(0, 10)}</Field>
                <Field name={t("geo.knowledge.detail.usage")}>
                  {item.support_count !== undefined || item.usage_count !== undefined
                    ? t("geo.knowledge.detail.usageValue", {
                        support: item.support_count ?? 0,
                        usage: item.usage_count ?? 0,
                      })
                    : null}
                </Field>
              </section>
              <section>
                <h3 className="text-sm font-medium mb-2">{t("geo.knowledge.detail.content")}</h3>
                <StructuredView value={item.structured_content} />
              </section>
              <RecentDevelopmentsSection item={item} />
              <ExpertComments itemId={item.knowledge_item_id} />
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

/** ナレッジ項目を、パネルの中で開閉できる形で表示する（クリックで内容を読める） */
function KnowledgeInline({ item, defaultOpen = false }: { item: GeoKnowledgeItem; defaultOpen?: boolean }) {
  return (
    <details open={defaultOpen} className="rounded-md border border-border/60 p-3 group">
      <summary className="cursor-pointer list-none flex items-center gap-2 flex-wrap">
        <Badge variant="outline">{item.knowledge_type}</Badge>
        {isAiApproved(item) && <Badge className="bg-geo-amber text-white hover:bg-geo-amber">AI暫定承認</Badge>}
        <span className="text-sm font-medium">{item.title}</span>
        <span className="text-[11px] text-muted-foreground">{item.confidence}</span>
      </summary>
      <div className="mt-3 space-y-3">
        <AiApprovalNotice item={item} />
        {item.summary && <p className="text-sm mb-3">{item.summary}</p>}
        <StructuredView value={item.structured_content} />
        <RecentDevelopmentsSection item={item} />
      </div>
    </details>
  );
}

export function EntityDetailSheet({
  entity,
  reason,
  nameById,
  onClose,
}: {
  entity: GeoEntity | null;
  reason?: string;
  nameById: Map<string, string>;
  onClose: () => void;
}) {
  const { t } = useLanguage();
  const rel = useGeoKnowledgeRelationships(entity?.entity_id ?? "");
  const ctx = useGeoEntityContext(entity?.entity_id ?? "");
  const e = (entity ?? {}) as unknown as Record<string, unknown>;
  const list = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);
  const nm = (id: string) => nameById.get(id) ?? id.slice(0, 8);
  return (
    <Sheet open={!!entity} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full sm:max-w-xl overflow-y-auto">
        {entity && (
          <>
            <SheetHeader>
              <div className="flex items-center gap-2 flex-wrap">
                <Badge variant="outline" className="uppercase">{entity.entity_type}</Badge>
                {entity.is_demo && <Badge variant="outline">demo</Badge>}
              </div>
              <SheetTitle className="text-editorial text-2xl">{entity.name_ja || entity.canonical_name}</SheetTitle>
              <SheetDescription>{t("geo.knowledge.detail.entityHint")}</SheetDescription>
            </SheetHeader>
            <div className="mt-4 space-y-5" data-testid="entity-detail">
              {reason && (
                <p className="rounded-md bg-primary/5 border border-primary/20 p-3 text-sm">
                  <span className="font-medium text-primary">{t("geo.knowledge.detail.whyMatched")}: </span>
                  {reason}
                </p>
              )}
              <section className="space-y-1.5 rounded-md border border-border/60 p-3">
                <Field name={t("geo.knowledge.detail.canonicalName")}>{entity.canonical_name}</Field>
                <Field name={t("geo.knowledge.detail.nameJa")}>{entity.name_ja}</Field>
                <Field name={t("geo.knowledge.detail.nameEn")}>{entity.name_en}</Field>
                <Field name={t("geo.knowledge.detail.localName")}>{typeof e.local_name === "string" ? e.local_name : null}</Field>
                <Field name={t("geo.knowledge.detail.abbreviations")}>{list(e.abbreviations).join("、")}</Field>
                <Field name={t("geo.knowledge.detail.formerNames")}>{list(e.former_names).join("、")}</Field>
                <Field name={t("geo.knowledge.detail.country")}>{entity.country_code}</Field>
                <Field name={t("geo.knowledge.detail.status")}>{entity.status}</Field>
                <Field name={t("geo.knowledge.detail.activePeriod")}>
                  {e.active_from || e.active_to
                    ? `${String(e.active_from ?? "").slice(0, 10)} 〜 ${String(e.active_to ?? "").slice(0, 10)}`
                    : null}
                </Field>
              </section>
              <section data-testid="entity-own-knowledge">
                <h3 className="text-sm font-medium mb-2">{t("geo.knowledge.detail.ownKnowledge")}</h3>
                {ctx.isLoading && <p className="text-xs text-muted-foreground">…</p>}
                {ctx.data && ctx.data.knowledge_items.length === 0 && (
                  <p className="text-xs text-muted-foreground">{t("geo.knowledge.detail.noOwnKnowledge")}</p>
                )}
                <div className="space-y-2">
                  {(ctx.data?.knowledge_items ?? []).map((k) => (
                    <KnowledgeInline key={k.knowledge_item_id} item={k} defaultOpen />
                  ))}
                </div>
                {ctx.data && ctx.data.pending_count > 0 && (
                  <p className="mt-2 text-xs rounded-md bg-muted px-3 py-2 text-muted-foreground" data-testid="entity-pending-note">
                    {t("geo.knowledge.detail.pendingKnowledge", { count: ctx.data.pending_count })}
                  </p>
                )}
              </section>
              <section data-testid="entity-issues">
                <h3 className="text-sm font-medium mb-2">{t("geo.knowledge.detail.issues")}</h3>
                {ctx.data && ctx.data.issues.length === 0 && (
                  <p className="text-xs text-muted-foreground">{t("geo.knowledge.detail.noIssues")}</p>
                )}
                <ul className="space-y-2">
                  {(ctx.data?.issues ?? []).map((i) => (
                    <li key={i.issue_id} className="rounded-md border border-border/60 p-2.5 text-sm">
                      <div className="font-medium">{i.name_ja}</div>
                      {i.name_en && <div className="text-xs text-muted-foreground">{i.name_en}</div>}
                      {i.description && <p className="text-xs mt-1">{i.description}</p>}
                    </li>
                  ))}
                </ul>
              </section>
              {(ctx.data?.issue_knowledge_items ?? []).length > 0 && (
                <section>
                  <h3 className="text-sm font-medium mb-2">{t("geo.knowledge.detail.issueKnowledge")}</h3>
                  <div className="space-y-2">
                    {(ctx.data?.issue_knowledge_items ?? []).map((k) => (
                      <KnowledgeInline key={k.knowledge_item_id} item={k} />
                    ))}
                  </div>
                </section>
              )}
              <section>
                <h3 className="text-sm font-medium mb-2">{t("geo.knowledge.detail.relationships")}</h3>
                {rel.isLoading && <p className="text-xs text-muted-foreground">…</p>}
                {rel.data && rel.data.relationships.length === 0 && (
                  <p className="text-xs text-muted-foreground">{t("geo.knowledge.detail.noRelationships")}</p>
                )}
                <ul className="space-y-2">
                  {(rel.data?.relationships ?? []).map((r) => (
                    <li key={r.relationship_id} className="rounded-md border border-border/60 p-2.5 text-sm">
                      <div className="font-medium">
                        {nm(r.from_entity_id)} <span className="text-muted-foreground">→</span> {nm(r.to_entity_id)}
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {r.relationship_domain} / {r.relationship_type}（{r.confidence}）
                      </div>
                      {r.description && <p className="text-xs mt-1">{r.description}</p>}
                    </li>
                  ))}
                </ul>
              </section>
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
