// Phase E: 今日のインテリジェンス（Daily Intelligence Brief）。
// 「毎朝、今日どこを見るべきか」を短時間で把握するための画面。Operations（何を処理すべきか /
// システムは正常か）とは別物——ここでは「何を見るべきか」だけを扱う。新しい分析AIは作らない。
import { useEffect, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { AlertTriangle, ExternalLink, Loader2, RefreshCw } from "lucide-react";
import { TopBar } from "@/components/top-bar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { setHighlightCandidateId } from "@/lib/geoThemeHighlightStore";
import {
  markSeenNow,
  useDailyBrief,
  useRefreshDailyBrief,
  type BriefEvidence,
  type BriefPoint,
} from "@/lib/geoDailyBriefApi";

type NotificationPreference = "daily" | "important" | "critical";
const PREF_KEY = "geo_daily_brief_notification_preference";

function loadPreference(): NotificationPreference {
  try {
    const v = localStorage.getItem(PREF_KEY);
    if (v === "important" || v === "critical") return v;
  } catch {
    // localStorageが使えない環境では既定(daily)のまま
  }
  return "daily";
}

function savePreference(pref: NotificationPreference): void {
  try {
    localStorage.setItem(PREF_KEY, pref);
  } catch {
    // 保存できなくても画面表示は続ける（ビューア固有の都合設定のため致命的ではない）
  }
}

function passesPreference(severity: BriefPoint["severity"], pref: NotificationPreference): boolean {
  if (pref === "critical") return severity === "high";
  if (pref === "important") return severity === "high" || severity === "medium";
  return true;
}

const SEVERITY_BADGE: Record<BriefPoint["severity"], { variant: "destructive" | "outline" | "secondary"; label: string }> = {
  high: { variant: "destructive", label: "重要" },
  medium: { variant: "secondary", label: "要確認" },
  low: { variant: "outline", label: "参考" },
};

function ago(iso: string | null | undefined): string {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return "—";
  const h = ms / 3600000;
  if (h < 1) return `${Math.max(1, Math.round(ms / 60000))}分前`;
  if (h < 48) return `${Math.round(h)}時間前`;
  return `${Math.round(h / 24)}日前`;
}

function EvidenceList({ evidence }: { evidence: BriefEvidence[] }) {
  if (evidence.length === 0) {
    return <p className="text-xs text-muted-foreground">根拠記事が見つかりませんでした。</p>;
  }
  return (
    <ul className="space-y-1">
      {evidence.map((e, i) => (
        <li key={i} className="text-xs text-muted-foreground flex items-start gap-1.5">
          <ExternalLink className="h-3 w-3 mt-0.5 shrink-0" />
          {e.url ? (
            <a href={e.url} target="_blank" rel="noreferrer" className="hover:text-foreground underline underline-offset-2">
              {e.title ?? e.url}
            </a>
          ) : (
            <span>{e.title ?? "(タイトル不明)"}</span>
          )}
          <span className="shrink-0">
            （{e.source ?? "出典不明"}・{e.published_at ? ago(e.published_at) : "日付不明"}）
          </span>
        </li>
      ))}
    </ul>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="card-paper rounded-lg p-5 space-y-3" data-testid={`brief-${title}`}>
      <h2 className="text-base font-semibold">{title}</h2>
      {children}
    </section>
  );
}

export function DailyBriefPage() {
  const navigate = useNavigate();
  const { data, isLoading, isError, error, refetch, isFetching } = useDailyBrief();
  const refresh = useRefreshDailyBrief();
  const [pref, setPref] = useState<NotificationPreference>(loadPreference());

  useEffect(() => {
    if (data) markSeenNow();        // 画面を開いて読み込めた時点で「確認済み」とする
  }, [data]);

  const openTheme = (candidateId: string) => {
    setHighlightCandidateId(candidateId);
    navigate({ to: "/geo/theme-discovery" });
  };
  const openMonitoring = (monitoringThemeId: string) =>
    navigate({ to: "/geo/monitoring-themes/$monitoringThemeId", params: { monitoringThemeId } });

  const points = data?.points.filter((p) => passesPreference(p.severity, pref)) ?? [];

  return (
    <>
      <TopBar breadcrumb={[{ label: "今日のインテリジェンス" }]} />
      <main className="flex-1 max-w-[1000px] w-full mx-auto px-8 py-8 space-y-6" data-testid="daily-brief-page">
        <section className="border-b border-border pb-5 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-editorial text-4xl">今日のインテリジェンス</h1>
            <p className="text-sm text-muted-foreground mt-2">
              今日どこを見るべきかを短時間で把握します。運用状況の確認は「インテリジェンス運用」を参照してください。
            </p>
          </div>
          <div className="text-right shrink-0 space-y-2">
            <div className="flex items-center gap-2">
              <Select value={pref} onValueChange={(v) => { setPref(v as NotificationPreference); savePreference(v as NotificationPreference); }}>
                <SelectTrigger className="h-8 w-[150px] text-xs" data-testid="brief-notification-pref">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="daily">すべて表示</SelectItem>
                  <SelectItem value="important">要確認以上のみ</SelectItem>
                  <SelectItem value="critical">重要のみ</SelectItem>
                </SelectContent>
              </Select>
              <Button size="sm" variant="outline" onClick={() => refresh()} disabled={isFetching}>
                <RefreshCw className={`h-3.5 w-3.5 mr-1 ${isFetching ? "animate-spin" : ""}`} />
                更新
              </Button>
            </div>
            {data && (
              <div className="text-[11px] text-muted-foreground">
                {new Date(data.generated_at).toLocaleTimeString("ja-JP")} 時点
                {data.window_is_fallback ? "（直近24時間）" : "（前回確認以降）"}
              </div>
            )}
          </div>
        </section>

        {isLoading && (
          <p className="text-sm text-muted-foreground flex items-center gap-2" data-testid="brief-loading">
            <Loader2 className="h-4 w-4 animate-spin" />
            今日のインテリジェンスを読み込んでいます…
          </p>
        )}
        {isError && (
          <div className="card-paper rounded-lg p-5 space-y-2" data-testid="brief-error">
            <p className="text-sm font-medium">取得できませんでした。</p>
            <p className="text-xs text-muted-foreground">{(error as Error).message}</p>
            <Button size="sm" variant="outline" onClick={() => refetch()}>再読み込み</Button>
          </div>
        )}

        {data && (
          <>
            {/* マネージャー向けSummary: 30秒で読める3件程度 */}
            <Section title="今日のポイント">
              {points.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  {data.is_empty ? "今日、特に確認すべき新しい変化はありません。" : "表示条件に合うポイントはありません。"}
                </p>
              ) : (
                <ul className="space-y-2">
                  {points.map((p, i) => (
                    <li key={i} className="flex items-start gap-2 text-sm">
                      <Badge variant={SEVERITY_BADGE[p.severity].variant} className="shrink-0 mt-0.5">
                        {SEVERITY_BADGE[p.severity].label}
                      </Badge>
                      <span>{p.text}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Section>

            {/* 重要な変化: Monitoring Alert */}
            <Section title="重要な変化">
              {data.changes.items.length === 0 ? (
                <p className="text-sm text-muted-foreground">未確認のAlertはありません。</p>
              ) : (
                <div className="space-y-4">
                  {data.changes.items.map((a) => (
                    <div key={a.alert_id} className="border-t border-border pt-3 first:border-t-0 first:pt-0 space-y-1.5">
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <Badge variant={a.severity === "high" ? "destructive" : "outline"} className="mr-2">
                            {a.severity === "high" ? "重要" : a.severity ?? "—"}
                          </Badge>
                          <span className="text-sm font-medium">{a.title}</span>
                          {a.is_new_since_last_visit && <Badge variant="secondary" className="ml-2">前回確認後</Badge>}
                        </div>
                        <Button size="sm" variant="outline" onClick={() => openMonitoring(a.monitoring_theme_id)}>
                          確認する
                        </Button>
                      </div>
                      <p className="text-xs text-muted-foreground">
                        {a.theme_name ?? "(テーマ不明)"} ・ {ago(a.detected_at)} ・ {a.reason}
                      </p>
                      <EvidenceList evidence={a.evidence} />
                    </div>
                  ))}
                </div>
              )}
            </Section>

            {/* 新しく見るべきテーマ: Theme Candidate（記事発見含む） */}
            <Section title="新しく見るべきテーマ">
              {data.new_themes.items.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  新規のテーマ候補はありません。
                  {data.new_themes.suppressed_as_duplicate > 0 &&
                    `（${data.new_themes.suppressed_as_duplicate}件は上の「重要な変化」と同一テーマのため、ここには出していません）`}
                </p>
              ) : (
                <div className="space-y-4">
                  {data.new_themes.items.map((c) => (
                    <div key={c.candidate_id} className="border-t border-border pt-3 first:border-t-0 first:pt-0 space-y-1.5">
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <Badge variant="outline" className="mr-2">{c.origin_label}</Badge>
                          <span className="text-sm font-medium">{c.title}</span>
                          {c.is_new_since_last_visit && <Badge variant="secondary" className="ml-2">前回確認後</Badge>}
                        </div>
                        <Button size="sm" variant="outline" onClick={() => openTheme(c.candidate_id)}>
                          テーマ探索で見る
                        </Button>
                      </div>
                      {c.question && <p className="text-xs text-muted-foreground">{c.question}</p>}
                      <EvidenceList evidence={c.evidence} />
                    </div>
                  ))}
                </div>
              )}
            </Section>

            {/* レビュー / 判断が必要: 件数サマリのみ（全件は出さない） */}
            <Section title="レビュー・判断が必要">
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <p className="text-sm">{data.review.summary_text}</p>
                  {data.review.pending > 0 && (
                    <Button size="sm" variant="outline" onClick={() => navigate({ to: "/geo/episode-candidates" })}>
                      レビューする
                    </Button>
                  )}
                </div>
                <div className="space-y-1.5">
                  <p className="text-sm">{data.theme_sync.summary_text}</p>
                  {data.theme_sync.pending > 0 && (
                    <Button size="sm" variant="outline" onClick={() => navigate({ to: "/geo/operations" })}>
                      テーマ同期へ
                    </Button>
                  )}
                </div>
              </div>
            </Section>

            {/* システム上の注意: 異常が無ければ目立たせない */}
            <Section title="システム上の注意">
              {data.operations_notice.has_issue ? (
                <div className="space-y-2">
                  <p className="text-sm flex items-center gap-1.5">
                    <AlertTriangle className="h-4 w-4 text-amber-500" />
                    {data.operations_notice.summary_text}
                  </p>
                  <ul className="text-xs text-muted-foreground space-y-0.5">
                    {data.operations_notice.items.map((it, i) => (
                      <li key={i}>・{it.detail}</li>
                    ))}
                  </ul>
                  <Button size="sm" variant="outline" onClick={() => navigate({ to: "/geo/operations" })}>
                    運用画面で確認
                  </Button>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">{data.operations_notice.summary_text}</p>
              )}
            </Section>

            {Object.keys(data.errors).length > 0 && (
              <p className="text-xs text-muted-foreground">
                一部の項目は取得できませんでした（{Object.keys(data.errors).join("、")}）。他の項目は表示しています。
              </p>
            )}
          </>
        )}
      </main>
    </>
  );
}
