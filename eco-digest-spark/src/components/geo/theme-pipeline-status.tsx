// Phase C §3/§15: Collection → Episode Candidate → Review → Theme Sync → Handoffの
// 滞留状況を、運用担当者が一目で「次に何をすべきか」判断できる形で表示する。
// 開発者向け監視画面ではなく、簡潔なステータス表示のみ（詳細はEpisode Candidates画面を参照）。
import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { useThemeCandidatePipelineStatus } from "@/lib/geoThemeCandidateApi";

export function ThemePipelineStatus({ isDemo }: { isDemo?: boolean } = {}) {
  const { data, isLoading, isError } = useThemeCandidatePipelineStatus({ isDemo });

  if (isLoading || isError || !data) return null;

  const rows: Array<{ label: string; count: number; attentionIfPositive: boolean }> = [
    { label: "レビュー待ち", count: data.review_pending, attentionIfPositive: true },
    { label: "承認済み", count: data.accepted, attentionIfPositive: false },
    { label: "Theme同期待ち", count: data.pending_theme_sync, attentionIfPositive: true },
    {
      label: "Theme Candidate生成済み",
      count: data.theme_candidates_total,
      attentionIfPositive: false,
    },
    { label: "Expert AI Handoff済み", count: data.handed_off, attentionIfPositive: false },
  ];

  return (
    <div className="card-paper rounded-lg p-4" data-testid="theme-pipeline-status">
      <div className="text-xs font-medium text-muted-foreground mb-2">Episode Pipeline</div>
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        {rows.map((r) => {
          const needsAttention = r.attentionIfPositive && r.count > 0;
          return (
            <div key={r.label} className="flex items-center gap-1.5">
              {needsAttention ? (
                <AlertTriangle className="h-3.5 w-3.5 text-amber-500 shrink-0" />
              ) : (
                <CheckCircle2 className="h-3.5 w-3.5 text-muted-foreground/50 shrink-0" />
              )}
              <div>
                <div className="text-lg font-semibold tabular-nums leading-none">{r.count}</div>
                <div className="text-[11px] text-muted-foreground leading-tight">{r.label}</div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
