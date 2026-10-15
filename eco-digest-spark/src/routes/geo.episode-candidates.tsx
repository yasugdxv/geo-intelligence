import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { setHighlightCandidateId } from "@/lib/geoThemeHighlightStore";
import { TopBar } from "@/components/top-bar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  useActorEpisodeCandidates, useUpdateActorEpisodeCandidateStatus,
  useMediaEpisodeCandidates, useUpdateMediaEpisodeCandidateStatus,
  type GeoActorEpisodeCandidate, type GeoMediaEpisodeCandidate,
} from "@/lib/geoExternalEventApi";
import {
  useEpisodeClusters, useEpisodeClusterMembers, useUpdateEpisodeClusterStatus,
  type GeoEpisodeCluster,
} from "@/lib/geoEpisodeClusterApi";
import {
  useThemeCandidatePendingSync, useSyncThemeCandidateForCluster,
  useBatchSyncPendingThemeCandidates, type GeoThemeSyncResult,
} from "@/lib/geoThemeCandidateApi";
import { ThemePipelineStatus } from "@/components/geo/theme-pipeline-status";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import {
  useSituationLinks, useUpdateSituationLinkStatus, type GeoSituationLink,
} from "@/lib/geoSituationLinkApi";
import {
  useSplitCandidates, useUpdateSplitCandidateStatus, type GeoSplitCandidate,
} from "@/lib/geoSplitCandidateApi";
import {
  useStatementCandidates, useUpdateStatementCandidateStatus, type GeoStatementCandidate,
} from "@/lib/geoStatementCandidateApi";
import { useGeoKnowledgeEntities } from "@/lib/geoKnowledgeApi";

export const Route = createFileRoute("/geo/episode-candidates")({
  component: GeoEpisodeCandidatesPage,
});

const STATUS_VARIANT: Record<string, "default" | "secondary" | "outline" | "destructive"> = {
  candidate: "outline", reviewed: "secondary", accepted: "default", rejected: "destructive",
};

function ActorCandidateRow({ candidate }: { candidate: GeoActorEpisodeCandidate }) {
  const updateMutation = useUpdateActorEpisodeCandidateStatus();
  const reviewerId = "analyst1";

  return (
    <div className="border-t border-border py-3 text-sm space-y-1">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate font-medium">{candidate.matched_name_text} ({candidate.actor_role})</div>
          <div className="text-xs text-muted-foreground">
            counterparty: {candidate.counterparty_text || "不明"} / {candidate.event_date ?? "日時不明"}
          </div>
          {candidate.observed_action && (
            <div className="text-xs text-muted-foreground mt-1">{candidate.observed_action}</div>
          )}
          <div className="text-xs text-muted-foreground mt-1">
            Evidence: {candidate.evidence_count} / Sources: {candidate.source_count} / Confidence: {candidate.confidence}
          </div>
        </div>
        <Badge variant={STATUS_VARIANT[candidate.candidate_status]} className="shrink-0">
          {candidate.candidate_status}
        </Badge>
      </div>
      {candidate.candidate_status !== "accepted" && candidate.candidate_status !== "rejected" && (
        <div className="flex items-center gap-2 pt-1">
          <Button size="sm" variant="outline" disabled={updateMutation.isPending}
                  onClick={() => updateMutation.mutate({
                    candidateId: candidate.actor_episode_candidate_id, candidate_status: "reviewed",
                    reviewed_by: reviewerId,
                  })}>
            Mark Reviewed
          </Button>
          <Button size="sm" variant="outline" disabled={updateMutation.isPending}
                  onClick={() => updateMutation.mutate({
                    candidateId: candidate.actor_episode_candidate_id, candidate_status: "accepted",
                    reviewed_by: reviewerId,
                  })}>
            Accept
          </Button>
          <Button size="sm" variant="outline" disabled={updateMutation.isPending}
                  onClick={() => updateMutation.mutate({
                    candidateId: candidate.actor_episode_candidate_id, candidate_status: "rejected",
                    reviewed_by: reviewerId,
                  })}>
            Reject
          </Button>
        </div>
      )}
    </div>
  );
}

const TRAJECTORY_LABEL: Record<string, string> = {
  insufficient_data: "データ不足", stable: "安定", escalating: "悪化傾向",
  de_escalating: "改善傾向", mixed: "混在",
};

const TRAJECTORY_VARIANT: Record<string, "default" | "secondary" | "outline" | "destructive"> = {
  insufficient_data: "outline", stable: "secondary", escalating: "destructive",
  de_escalating: "default", mixed: "outline",
};

const LINK_TYPE_LABEL: Record<string, string> = {
  same_situation_candidate: "同一局面候補", related_context: "関連文脈候補",
};

function SituationLinkRow({ link, otherLabel }: { link: GeoSituationLink; otherLabel: string }) {
  const updateMutation = useUpdateSituationLinkStatus();
  const reviewerId = "analyst1";
  return (
    <div className="text-xs border-t border-border pt-2 mt-2 first:border-t-0 first:mt-0 first:pt-0">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate">
            <Badge variant={link.link_type === "same_situation_candidate" ? "default" : "outline"} className="mr-1">
              {LINK_TYPE_LABEL[link.link_type] ?? link.link_type}
            </Badge>
            {otherLabel}（score={link.link_score}）
          </div>
          <div className="text-muted-foreground mt-0.5">
            {link.link_reasons.map((r) => r.signal).join(", ")}
          </div>
        </div>
        <Badge variant={STATUS_VARIANT[link.link_status]} className="shrink-0">{link.link_status}</Badge>
      </div>
      {link.link_status !== "accepted" && link.link_status !== "rejected" && (
        <div className="flex items-center gap-2 pt-1">
          <Button size="sm" variant="outline" disabled={updateMutation.isPending}
                  onClick={() => updateMutation.mutate({
                    clusterLinkId: link.cluster_link_id, link_status: "reviewed", reviewed_by: reviewerId,
                  })}>
            Mark Reviewed
          </Button>
          <Button size="sm" variant="outline" disabled={updateMutation.isPending}
                  onClick={() => updateMutation.mutate({
                    clusterLinkId: link.cluster_link_id, link_status: "accepted", reviewed_by: reviewerId,
                  })}>
            Accept
          </Button>
          <Button size="sm" variant="outline" disabled={updateMutation.isPending}
                  onClick={() => updateMutation.mutate({
                    clusterLinkId: link.cluster_link_id, link_status: "rejected", reviewed_by: reviewerId,
                  })}>
            Reject
          </Button>
        </div>
      )}
      {updateMutation.isError && (
        <p className="text-destructive mt-1">{(updateMutation.error as Error)?.message}</p>
      )}
    </div>
  );
}

const SPLIT_CANDIDATE_TYPE_LABEL: Record<string, string> = {
  split_group: "分割候補（局面A）", artifact_group: "混入疑い（Cleanup候補・非Episode）",
};

function SplitProposalGroup({ rows }: { rows: GeoSplitCandidate[] }) {
  // 同一sibling_group_keyのsplit_group/artifact_group行群は1つのSplit Proposalとして
  // 常に同じcandidate_statusを保つ（バックエンドがsibling一括更新するため、フロントは
  // 代表として先頭行のsplit_candidate_idを指定するだけでよい）。
  const updateMutation = useUpdateSplitCandidateStatus();
  const reviewerId = "analyst1";
  const status = rows[0].candidate_status;
  return (
    <div className="text-xs border-t border-border pt-2 mt-2 first:border-t-0 first:mt-0 first:pt-0">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 font-medium">このClusterはN個の局面に分かれる可能性があります</div>
        <Badge variant={STATUS_VARIANT[status]} className="shrink-0">{status}</Badge>
      </div>
      {rows.map((row) => (
        <div key={row.split_candidate_id} className="mt-1">
          <Badge variant={row.candidate_type === "artifact_group" ? "destructive" : "default"} className="mr-1">
            {SPLIT_CANDIDATE_TYPE_LABEL[row.candidate_type] ?? row.candidate_type}
          </Badge>
          {row.member_candidate_ids.length}件
          {row.dominant_event_base_code && <> / base={row.dominant_event_base_code}</>}
          <div className="text-muted-foreground mt-0.5">
            {row.separation_evidence.map((e) => e.signal).join(", ") || "根拠情報なし"}
          </div>
        </div>
      ))}
      {status !== "accepted" && status !== "rejected" && (
        <div className="flex items-center gap-2 pt-1">
          <Button size="sm" variant="outline" disabled={updateMutation.isPending}
                  onClick={() => updateMutation.mutate({
                    splitCandidateId: rows[0].split_candidate_id, candidate_status: "reviewed",
                    reviewed_by: reviewerId,
                  })}>
            Mark Reviewed
          </Button>
          <Button size="sm" variant="outline" disabled={updateMutation.isPending}
                  onClick={() => updateMutation.mutate({
                    splitCandidateId: rows[0].split_candidate_id, candidate_status: "accepted",
                    reviewed_by: reviewerId,
                  })}>
            Accept
          </Button>
          <Button size="sm" variant="outline" disabled={updateMutation.isPending}
                  onClick={() => updateMutation.mutate({
                    splitCandidateId: rows[0].split_candidate_id, candidate_status: "rejected",
                    reviewed_by: reviewerId,
                  })}>
            Reject
          </Button>
        </div>
      )}
      {updateMutation.isError && (
        <p className="text-destructive mt-1">{(updateMutation.error as Error)?.message}</p>
      )}
    </div>
  );
}

function OutlierCandidateRow({ row, clusterLabelById }: {
  row: GeoSplitCandidate; clusterLabelById: Record<string, string>;
}) {
  const updateMutation = useUpdateSplitCandidateStatus();
  const reviewerId = "analyst1";
  return (
    <div className="text-xs border-t border-border pt-2 mt-2 first:border-t-0 first:mt-0 first:pt-0">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 font-medium">このmemberは他のClusterとの関連が強い可能性があります</div>
        <Badge variant={STATUS_VARIANT[row.candidate_status]} className="shrink-0">{row.candidate_status}</Badge>
      </div>
      {row.cross_cluster_affinity.length === 0 ? (
        <div className="text-muted-foreground mt-0.5">他Clusterとの関連は検出されていません</div>
      ) : (
        row.cross_cluster_affinity.map((a) => (
          <div key={a.target_cluster_id} className="text-muted-foreground mt-0.5">
            → {clusterLabelById[a.target_cluster_id] ?? a.target_cluster_id}
            {a.existing_link_type && <> (既存Situation Link: {a.existing_link_type})</>}
          </div>
        ))
      )}
      {row.candidate_status !== "accepted" && row.candidate_status !== "rejected" && (
        <div className="flex items-center gap-2 pt-1">
          <Button size="sm" variant="outline" disabled={updateMutation.isPending}
                  onClick={() => updateMutation.mutate({
                    splitCandidateId: row.split_candidate_id, candidate_status: "reviewed",
                    reviewed_by: reviewerId,
                  })}>
            Mark Reviewed
          </Button>
          <Button size="sm" variant="outline" disabled={updateMutation.isPending}
                  onClick={() => updateMutation.mutate({
                    splitCandidateId: row.split_candidate_id, candidate_status: "accepted",
                    reviewed_by: reviewerId,
                  })}>
            Accept
          </Button>
          <Button size="sm" variant="outline" disabled={updateMutation.isPending}
                  onClick={() => updateMutation.mutate({
                    splitCandidateId: row.split_candidate_id, candidate_status: "rejected",
                    reviewed_by: reviewerId,
                  })}>
            Reject
          </Button>
        </div>
      )}
      {updateMutation.isError && (
        <p className="text-destructive mt-1">{(updateMutation.error as Error)?.message}</p>
      )}
    </div>
  );
}

const STATEMENT_KIND_LABEL: Record<string, string> = {
  direct_quote_candidate: "直接引用候補", attributed_statement_candidate: "帰属声明候補",
  quotation_fragment: "引用断片（未分類）",
};

const EPISODE_EVIDENCE_LABEL: Record<string, string> = {
  normal: "通常", split_group: "分割候補group", outlier_pending: "outlier保留",
  unresolved_hold: "未解決Proposalによりhold中",
};

function StatementCandidateRow({ candidate }: { candidate: GeoStatementCandidate }) {
  const updateMutation = useUpdateStatementCandidateStatus();
  const reviewerId = "analyst1";
  const [showAcceptForm, setShowAcceptForm] = useState(false);
  const [confirmedKind, setConfirmedKind] = useState<string>("direct_quote");
  const [confirmedText, setConfirmedText] = useState(candidate.quote_text);
  const [confirmedName, setConfirmedName] = useState(candidate.speaker_candidates[0]?.name ?? "");
  const [confirmedType, setConfirmedType] = useState<string>(
    candidate.speaker_candidates[0]?.speaker_type === "organization" ? "organization" : "person");
  const [rejectionReason, setRejectionReason] = useState<string>("interpretive_fragment");

  return (
    <div className="text-xs border-t border-border pt-2 mt-2 first:border-t-0 first:mt-0 first:pt-0">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <Badge variant="outline" className="mr-1">{STATEMENT_KIND_LABEL[candidate.statement_kind] ?? candidate.statement_kind}</Badge>
          <Badge variant="outline" className="mr-1">{candidate.speaker_attribution_status}</Badge>
          {candidate.episode_evidence_status && (
            <Badge variant="outline">{EPISODE_EVIDENCE_LABEL[candidate.episode_evidence_status] ?? candidate.episode_evidence_status}</Badge>
          )}
          <div className="mt-1">"{candidate.quote_text}"</div>
          {candidate.speaker_candidates.length > 0 && (
            <div className="text-muted-foreground mt-0.5">
              話者候補（未確定）: {candidate.speaker_candidates.slice(0, 3).map((s) => `${s.name}(${s.speaker_type})`).join(", ")}
            </div>
          )}
        </div>
        <Badge variant={STATUS_VARIANT[candidate.candidate_status]} className="shrink-0">{candidate.candidate_status}</Badge>
      </div>
      {candidate.candidate_status !== "accepted" && candidate.candidate_status !== "rejected" && (
        <div className="pt-1 space-y-1">
          {!showAcceptForm ? (
            <div className="flex items-center gap-2">
              <Button size="sm" variant="outline" disabled={updateMutation.isPending}
                      onClick={() => updateMutation.mutate({
                        statementCandidateId: candidate.statement_candidate_id, candidate_status: "reviewed",
                        reviewed_by: reviewerId,
                      })}>
                Mark Reviewed
              </Button>
              <Button size="sm" variant="outline" onClick={() => setShowAcceptForm(true)}>Accept...</Button>
              <select className="border rounded text-xs px-1" value={rejectionReason}
                      onChange={(e) => setRejectionReason(e.target.value)}>
                <option value="interpretive_fragment">interpretive_fragment</option>
                <option value="other">other</option>
              </select>
              <Button size="sm" variant="outline" disabled={updateMutation.isPending}
                      onClick={() => updateMutation.mutate({
                        statementCandidateId: candidate.statement_candidate_id, candidate_status: "rejected",
                        reviewed_by: reviewerId, rejection_reason: rejectionReason,
                      })}>
                Reject
              </Button>
            </div>
          ) : (
            <div className="space-y-1 border border-border rounded p-2">
              <div className="font-medium">Accept確認（元記事・一次情報を確認したうえで入力してください）</div>
              <select className="border rounded text-xs px-1 w-full" value={confirmedKind}
                      onChange={(e) => setConfirmedKind(e.target.value)}>
                <option value="direct_quote">direct_quote</option>
                <option value="attributed_statement">attributed_statement</option>
              </select>
              <textarea className="border rounded text-xs px-1 w-full" value={confirmedText}
                        onChange={(e) => setConfirmedText(e.target.value)} rows={2} />
              <input className="border rounded text-xs px-1 w-full" placeholder="confirmed speaker name"
                     value={confirmedName} onChange={(e) => setConfirmedName(e.target.value)} />
              <select className="border rounded text-xs px-1 w-full" value={confirmedType}
                      onChange={(e) => setConfirmedType(e.target.value)}>
                <option value="person">person</option>
                <option value="organization">organization</option>
              </select>
              <div className="flex items-center gap-2">
                <Button size="sm" variant="outline" disabled={updateMutation.isPending || !confirmedText || !confirmedName}
                        onClick={() => updateMutation.mutate({
                          statementCandidateId: candidate.statement_candidate_id, candidate_status: "accepted",
                          reviewed_by: reviewerId, confirmed_statement_kind: confirmedKind,
                          confirmed_statement_text: confirmedText, confirmed_speaker_name: confirmedName,
                          confirmed_speaker_type: confirmedType,
                        })}>
                  Confirm Accept
                </Button>
                <Button size="sm" variant="outline" onClick={() => setShowAcceptForm(false)}>Cancel</Button>
              </div>
            </div>
          )}
        </div>
      )}
      {updateMutation.isError && (
        <p className="text-destructive mt-1">{(updateMutation.error as Error)?.message}</p>
      )}
    </div>
  );
}

function StatementEvidenceForSignal({ signalId }: { signalId: string }) {
  const query = useStatementCandidates({ externalSignalId: signalId });
  const candidates = query.data?.statement_candidates ?? [];
  if (query.isLoading || query.isError || candidates.length === 0) return null;
  return (
    <>
      {candidates.map((sc) => (
        <StatementCandidateRow key={sc.statement_candidate_id} candidate={sc} />
      ))}
    </>
  );
}

// Phase C §9: Acceptした直後、「承認しました」だけで終わらせず、Theme Candidate生成対象に
// なったかどうか・まだなら生成導線を示す。Accept操作とTheme生成は別トランザクション
// （Acceptのmutationとは別に、ユーザーが明示的にボタンを押した時だけsync-clusterを呼ぶ）。
function ThemeSyncGuidance({ episodeClusterId }: { episodeClusterId: string }) {
  const [dismissed, setDismissed] = useState(false);
  const pendingQuery = useThemeCandidatePendingSync();
  const syncMutation = useSyncThemeCandidateForCluster();

  // Accept直前に始まった取得が、Accept前の古い一覧のまま返ってくることがある（ページ表示時の取得が
  // 遅い場合）。この案内はAccept済みのClusterにだけ出すので、表示時に最新の一覧を取り直す。
  const refetchPending = pendingQuery.refetch;
  const queryClient = useQueryClient();
  useEffect(() => {
    // 取得中の古いリクエストを取り消してから取り直す（取り消さないと同じ取得に相乗りしてしまう）
    void queryClient
      .cancelQueries({ queryKey: ["geoThemeCandidatePendingSync"] })
      .then(() => refetchPending());
  }, [episodeClusterId, refetchPending, queryClient]);

  if (dismissed) return null;

  const pending = pendingQuery.data?.pending ?? [];
  const material = pending.find((m) => m.cluster_ids.includes(episodeClusterId));
  const isEligibleButUnsynced = !!material;

  if (pendingQuery.isLoading) return null;
  if (!isEligibleButUnsynced && !syncMutation.data) return null; // 対象外 or 既に同期済み

  const result = syncMutation.data as GeoThemeSyncResult | undefined;

  return (
    <div className="mt-2 rounded-md border border-primary/30 bg-primary/5 p-3 text-xs space-y-2">
      {result ? (
        <ThemeSyncResultMessage result={result} />
      ) : (
        <>
          <p className="font-medium text-foreground">
            承認しました。このClusterはTheme Candidate生成対象になりました
          </p>
          <p className="text-muted-foreground">
            {material?.source_type === "situation_component"
              ? `関連する${material.cluster_ids.length}件のClusterとまとめて1件のTheme Candidateになります。`
              : "承認済みの観測群から、分析対象として立てる価値がある問いをAIが提案します。"}
          </p>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              disabled={syncMutation.isPending}
              onClick={() => syncMutation.mutate({ episodeClusterId })}
            >
              {syncMutation.isPending ? "生成中..." : "Theme Candidateを生成"}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setDismissed(true)}>
              あとで
            </Button>
          </div>
          {syncMutation.isError && (
            <p className="text-destructive">
              {(syncMutation.error as Error).message}（再試行できます）
            </p>
          )}
        </>
      )}
    </div>
  );
}

function ThemeSyncResultMessage({ result }: { result: GeoThemeSyncResult }) {
  const navigate = useNavigate();
  if (result.outcome === "created" || result.outcome === "merged") {
    return (
      <div className="space-y-1.5">
        <p className="font-medium text-foreground">
          Theme Candidateを{result.outcome === "created" ? "生成しました" : "更新しました"}
        </p>
        <Button
          size="sm"
          variant="outline"
          onClick={() => {
            if (result.candidate_id) setHighlightCandidateId(result.candidate_id);
            navigate({ to: "/geo/theme-discovery" });
          }}
        >
          テーマ探索で確認
        </Button>
      </div>
    );
  }
  if (result.outcome === "insufficient_evidence") {
    return (
      <p className="text-muted-foreground">
        Theme
        Candidateとして立てるには根拠（Evidence）が不足しています。観測が増えてから再度お試しください。
      </p>
    );
  }
  return (
    <p className="text-destructive">
      AI生成に失敗しました（LLM呼び出しエラー）。Episode
      Clusterはacceptedのまま保持されているため、再試行できます。
    </p>
  );
}

function EpisodeClusterRow({ cluster, entityName, clusterLabelById, onAccepted }: {
  cluster: GeoEpisodeCluster; entityName: string; clusterLabelById: Record<string, string>;
  onAccepted?: (episodeClusterId: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const updateMutation = useUpdateEpisodeClusterStatus();
  const membersQuery = useEpisodeClusterMembers(cluster.episode_cluster_id, expanded);
  const linksQuery = useSituationLinks({ clusterId: expanded ? cluster.episode_cluster_id : undefined });
  const splitQuery = useSplitCandidates({ parentClusterId: expanded ? cluster.episode_cluster_id : undefined });
  const reviewerId = "analyst1";

  return (
    <div className="border-t border-border py-3 text-sm space-y-1">
      <div className="flex items-start justify-between gap-3">
        <button className="min-w-0 text-left" onClick={() => setExpanded((v) => !v)}>
          <div className="truncate font-medium">
            {entityName} — quad{cluster.quad_class} / root{cluster.event_root_code}
          </div>
          <div className="text-xs text-muted-foreground">
            {cluster.first_event_date ?? "?"} 〜 {cluster.last_event_date ?? "?"} / {cluster.member_count}件
            {cluster.representative_counterparties.length > 0 && (
              <> / vs {cluster.representative_counterparties.slice(0, 3).join(", ")}</>
            )}
          </div>
        </button>
        <div className="flex items-center gap-2 shrink-0">
          <Badge variant={TRAJECTORY_VARIANT[cluster.trajectory] ?? "outline"}>
            {TRAJECTORY_LABEL[cluster.trajectory] ?? cluster.trajectory}
          </Badge>
          <Badge variant={STATUS_VARIANT[cluster.cluster_status]}>{cluster.cluster_status}</Badge>
        </div>
      </div>

      {expanded && (
        <div className="pl-3 border-l border-border ml-1 mt-1 space-y-1">
          {membersQuery.isLoading ? (
            <p className="text-xs text-muted-foreground">読み込み中...</p>
          ) : membersQuery.isError ? (
            <p className="text-xs text-destructive">{(membersQuery.error as Error)?.message}</p>
          ) : (
            (membersQuery.data?.members ?? []).map((m) => {
              const member = m as GeoActorEpisodeCandidate;
              return (
                <div key={member.actor_episode_candidate_id} className="text-xs text-muted-foreground">
                  {member.event_date ?? "日時不明"} / {member.observed_action ?? "(観測記述なし)"}
                  {expanded && (member.external_signal_ids ?? []).slice(0, 1).map((sid) => (
                    <StatementEvidenceForSignal key={sid} signalId={sid} />
                  ))}
                </div>
              );
            })
          )}

          <div className="pt-2 mt-2 border-t border-border">
            <div className="text-xs font-medium text-muted-foreground mb-1">Related Clusters</div>
            {linksQuery.isLoading ? (
              <p className="text-xs text-muted-foreground">読み込み中...</p>
            ) : linksQuery.isError ? (
              <p className="text-xs text-destructive">{(linksQuery.error as Error)?.message}</p>
            ) : (linksQuery.data?.links ?? []).length === 0 ? (
              <p className="text-xs text-muted-foreground">関連するClusterはありません</p>
            ) : (
              (linksQuery.data?.links ?? []).map((link) => {
                const otherId = link.source_cluster_id === cluster.episode_cluster_id
                  ? link.target_cluster_id : link.source_cluster_id;
                return (
                  <SituationLinkRow key={link.cluster_link_id} link={link}
                                     otherLabel={clusterLabelById[otherId] ?? otherId} />
                );
              })
            )}
          </div>

          <div className="pt-2 mt-2 border-t border-border">
            <div className="text-xs font-medium text-muted-foreground mb-1">Split Suggestion</div>
            {splitQuery.isLoading ? (
              <p className="text-xs text-muted-foreground">読み込み中...</p>
            ) : splitQuery.isError ? (
              <p className="text-xs text-destructive">{(splitQuery.error as Error)?.message}</p>
            ) : (splitQuery.data?.split_candidates ?? []).length === 0 ? (
              <p className="text-xs text-muted-foreground">分割・混入の疑いは検出されていません</p>
            ) : (
              (() => {
                const rows = splitQuery.data?.split_candidates ?? [];
                const proposalGroups = new Map<string, GeoSplitCandidate[]>();
                const outliers: GeoSplitCandidate[] = [];
                for (const row of rows) {
                  if (row.candidate_type === "outlier") {
                    outliers.push(row);
                    continue;
                  }
                  const group = proposalGroups.get(row.sibling_group_key) ?? [];
                  group.push(row);
                  proposalGroups.set(row.sibling_group_key, group);
                }
                return (
                  <>
                    {[...proposalGroups.entries()].map(([key, group]) => (
                      <SplitProposalGroup key={key} rows={group} />
                    ))}
                    {outliers.map((row) => (
                      <OutlierCandidateRow key={row.split_candidate_id} row={row}
                                            clusterLabelById={clusterLabelById} />
                    ))}
                  </>
                );
              })()
            )}
          </div>
        </div>
      )}

      {cluster.cluster_status !== "accepted" && cluster.cluster_status !== "rejected" && (
        <div className="flex items-center gap-2 pt-1">
          <Button size="sm" variant="outline" disabled={updateMutation.isPending}
                  onClick={() => updateMutation.mutate({
                    episodeClusterId: cluster.episode_cluster_id, cluster_status: "reviewed",
                    reviewed_by: reviewerId,
                  })}>
            Mark Reviewed
          </Button>
          <Button size="sm" variant="outline" disabled={updateMutation.isPending}
                  onClick={() => updateMutation.mutate({
                    episodeClusterId: cluster.episode_cluster_id, cluster_status: "accepted",
                    reviewed_by: reviewerId,
                  }, { onSuccess: () => onAccepted?.(cluster.episode_cluster_id) })}>
            Accept
          </Button>
          <Button size="sm" variant="outline" disabled={updateMutation.isPending}
                  onClick={() => updateMutation.mutate({
                    episodeClusterId: cluster.episode_cluster_id, cluster_status: "rejected",
                    reviewed_by: reviewerId,
                  })}>
            Reject
          </Button>
        </div>
      )}

      {cluster.cluster_status === "accepted" && (
        <ThemeSyncGuidance episodeClusterId={cluster.episode_cluster_id} />
      )}
      <p className="text-xs text-muted-foreground">
        Rejectはこの観測群をEpisode追跡対象から外すことを意味し、個々のCandidateの事実が誤りだった
        という意味ではありません。Rejectされた所属Candidateは今後の自動再クラスタリング対象にも
        戻りません。Acceptしても、Actor Intelligence本体（Core Profile / Decision Pattern /
        Current Posture等）は一切自動更新されません。
      </p>
    </div>
  );
}

function MediaCandidateRow({ candidate }: { candidate: GeoMediaEpisodeCandidate }) {
  const updateMutation = useUpdateMediaEpisodeCandidateStatus();
  const reviewerId = "analyst1";

  return (
    <div className="border-t border-border py-3 text-sm space-y-1">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate font-medium">{candidate.headline || "(無題)"}</div>
          <div className="text-xs text-muted-foreground">
            {candidate.media_source_id} ({candidate.source_domain}) / Tone: {candidate.tone ?? "-"}
          </div>
          <div className="text-xs text-muted-foreground mt-1">
            Related Events: {candidate.related_external_event_ids.length} / Confidence: {candidate.confidence}
          </div>
        </div>
        <Badge variant={STATUS_VARIANT[candidate.candidate_status]} className="shrink-0">
          {candidate.candidate_status}
        </Badge>
      </div>
      {candidate.candidate_status !== "accepted" && candidate.candidate_status !== "rejected" && (
        <div className="flex items-center gap-2 pt-1">
          <Button size="sm" variant="outline" disabled={updateMutation.isPending}
                  onClick={() => updateMutation.mutate({
                    candidateId: candidate.media_episode_candidate_id, candidate_status: "reviewed",
                    reviewed_by: reviewerId,
                  })}>
            Mark Reviewed
          </Button>
          <Button size="sm" variant="outline" disabled={updateMutation.isPending}
                  onClick={() => updateMutation.mutate({
                    candidateId: candidate.media_episode_candidate_id, candidate_status: "accepted",
                    reviewed_by: reviewerId,
                  })}>
            Accept
          </Button>
          <Button size="sm" variant="outline" disabled={updateMutation.isPending}
                  onClick={() => updateMutation.mutate({
                    candidateId: candidate.media_episode_candidate_id, candidate_status: "rejected",
                    reviewed_by: reviewerId,
                  })}>
            Reject
          </Button>
        </div>
      )}
    </div>
  );
}

// Phase C §6/§7: 運用効率のための一括Sync。対象件数・実行前確認・成功/候補なし/失敗件数を
// 必ず示す。生成ロジック自体は既存batch_sync_pending（=既存のbuild_candidate_material/
// generate_candidate_framing/create_or_merge_candidateをそのまま使う）を呼ぶだけ。
function BatchThemeSyncPanel() {
  const pendingQuery = useThemeCandidatePendingSync();
  const batchMutation = useBatchSyncPendingThemeCandidates();
  const navigate = useNavigate();

  const pendingCount = pendingQuery.data?.pending.length ?? 0;
  if (pendingCount === 0 && !batchMutation.data) return null;

  const result = batchMutation.data;

  return (
    <div className="card-paper rounded-lg p-4 space-y-2">
      {result ? (
        <div className="space-y-2 text-sm">
          <p className="font-medium">一括Sync結果</p>
          <ul className="text-xs text-muted-foreground space-y-0.5">
            <li>生成成功: {result.counts.inserted_count}件</li>
            <li>更新（既存Theme Candidateへ反映）: {result.counts.updated_count}件</li>
            <li>候補なし（Evidence不足）: {result.counts.skipped_count}件</li>
            <li>失敗（AI生成エラー）: {result.counts.error_count}件</li>
          </ul>
          {(result.counts.inserted_count > 0 || result.counts.updated_count > 0) && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                const first = result.results.find((r) => r.candidate_id);
                if (first?.candidate_id) setHighlightCandidateId(first.candidate_id);
                navigate({ to: "/geo/theme-discovery" });
              }}
            >
              テーマ探索で確認
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={() => batchMutation.reset()}>
            閉じる
          </Button>
        </div>
      ) : (
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm">
            Theme同期待ちのEpisode Clusterが
            <span className="font-medium">{pendingCount}件</span>あります。
          </p>
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button size="sm" disabled={batchMutation.isPending}>
                未同期をまとめて生成
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>{pendingCount}件の承認済みEpisode Clusterから</AlertDialogTitle>
                <AlertDialogDescription>
                  Theme
                  Candidateを生成します。それぞれ個別にAIが分析対象として立てる価値がある問いを提案します。
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>キャンセル</AlertDialogCancel>
                <AlertDialogAction onClick={() => batchMutation.mutate({})}>
                  {pendingCount}件を生成
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      )}
      {batchMutation.isError && (
        <p className="text-xs text-destructive">{(batchMutation.error as Error).message}</p>
      )}
    </div>
  );
}

function GeoEpisodeCandidatesPage() {
  const [statusFilter, setStatusFilter] = useState<string>("candidate");
  const [clustersExpanded, setClustersExpanded] = useState(true);
  // Phase C §11: 「candidate」で絞り込み中にAcceptすると、そのClusterは一覧から消えて案内も一緒に
  // 消えてしまう。今回のセッションでAcceptしたClusterは、一覧に出ていなくても案内を表示し続ける。
  const [acceptedIds, setAcceptedIds] = useState<string[]>([]);
  const actorQuery = useActorEpisodeCandidates({ candidateStatus: statusFilter || undefined, limit: 50 });
  const mediaQuery = useMediaEpisodeCandidates({ candidateStatus: statusFilter || undefined, limit: 50 });
  const clustersQuery = useEpisodeClusters({ clusterStatus: statusFilter || undefined, limit: 200 });
  const entitiesQuery = useGeoKnowledgeEntities();

  const actorCandidates = actorQuery.data?.candidates ?? [];
  const mediaCandidates = mediaQuery.data?.candidates ?? [];
  // Phase D §22: レビュー待ちが多い時に処理しやすいよう、Clusterの並び順を選べる（画面上で並べ替えるだけ）。
  // 既定は従来どおり「出来事が新しい順」（APIの返却順）。
  const [clusterSort, setClusterSort] = useState<"newest" | "oldest" | "evidence">("newest");
  const rawClusters = clustersQuery.data?.clusters ?? [];
  const clusters = [...rawClusters].sort((a, b) => {
    if (clusterSort === "evidence") return (b.member_count ?? 0) - (a.member_count ?? 0);
    const da = a.last_event_date ?? "";
    const db = b.last_event_date ?? "";
    if (clusterSort === "oldest") return da < db ? -1 : da > db ? 1 : 0;
    return da < db ? 1 : da > db ? -1 : 0;
  });
  const entityNameById = Object.fromEntries(
    (entitiesQuery.data?.entities ?? []).map((e) => [e.entity_id, e.canonical_name]));
  const clusterLabelById = Object.fromEntries(
    clusters.map((c) => [c.episode_cluster_id,
      `${entityNameById[c.actor_entity_id] ?? c.actor_entity_id} / quad${c.quad_class} / root${c.event_root_code}`]));

  return (
    <>
      <TopBar breadcrumb={[{ label: "News & Signals", href: "/geo/news-signals" }, { label: "Episode Candidates" }]} />
      <main className="flex-1 max-w-[1000px] w-full mx-auto px-8 py-8 space-y-6">
        <section className="border-b border-border pb-5">
          <h1 className="text-editorial text-4xl">Actor / Media Episode Candidates</h1>
          <p className="text-sm text-muted-foreground mt-2">
            GDELT自動抽出の未検証Candidate（Phase2A）。Human Reviewを経ておらず、Acceptしても
            Actor/Media Intelligence本体（Core Profile / Decision Pattern / Media Pattern等）は
            一切自動更新されません。
          </p>
        </section>

        <ThemePipelineStatus />
        <BatchThemeSyncPanel />

        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">Status:</span>
          {["candidate", "reviewed", "accepted", "rejected", ""].map((s) => (
            <Button key={s || "all"} size="sm" variant={statusFilter === s ? "default" : "outline"}
                    onClick={() => setStatusFilter(s)}>
              {s || "all"}
            </Button>
          ))}
        </div>

        <Tabs defaultValue="actor">
          <TabsList>
            <TabsTrigger value="actor">Actor Candidates ({actorCandidates.length})</TabsTrigger>
            <TabsTrigger value="media">Media Candidates ({mediaCandidates.length})</TabsTrigger>
          </TabsList>
          <TabsContent value="actor">
            <div className="card-paper rounded-lg p-4 mb-3">
              <button className="flex items-center justify-between w-full text-left"
                      onClick={() => setClustersExpanded((v) => !v)}>
                <h2 className="text-editorial text-lg">Episode Clusters ({clusters.length})</h2>
                <span className="text-xs text-muted-foreground">{clustersExpanded ? "▲" : "▼"}</span>
              </button>
              <p className="text-xs text-muted-foreground mt-1">
                Actor Episode Candidateをactor×CAMEO行動分類×時間的近さでルールベースに
                まとめた局面（Phase2B-1、embedding/LLM不使用）。Acceptしても
                Actor Intelligence本体は自動更新されません。
              </p>
              {clustersExpanded && (
                <div className="flex items-center gap-2 mt-2" data-testid="cluster-sort">
                  <span className="text-xs text-muted-foreground">並び順:</span>
                  {([["newest", "新しい順"], ["oldest", "古い順"], ["evidence", "根拠の多い順"]] as const).map(([k, label]) => (
                    <Button key={k} size="sm" variant={clusterSort === k ? "default" : "outline"}
                            onClick={() => setClusterSort(k)}>
                      {label}
                    </Button>
                  ))}
                </div>
              )}
              {clustersExpanded && acceptedIds
                .filter((id) => !clusters.some((c) => c.episode_cluster_id === id))
                .map((id) => (
                  <div key={`accepted-${id}`} data-testid="accepted-guidance">
                    <ThemeSyncGuidance episodeClusterId={id} />
                  </div>
                ))}
              {clustersExpanded && (
                clustersQuery.isLoading ? (
                  <p className="text-sm text-muted-foreground mt-2">読み込み中...</p>
                ) : clustersQuery.isError ? (
                  <p className="text-sm text-destructive mt-2">{(clustersQuery.error as Error)?.message}</p>
                ) : clusters.length === 0 ? (
                  <p className="text-sm text-muted-foreground mt-2">該当するClusterがありません</p>
                ) : (
                  clusters.map((c) => (
                    <EpisodeClusterRow key={c.episode_cluster_id} cluster={c}
                                       onAccepted={(id) => setAcceptedIds((prev) => (prev.includes(id) ? prev : [...prev, id]))}
                                       entityName={entityNameById[c.actor_entity_id] ?? c.actor_entity_id}
                                       clusterLabelById={clusterLabelById} />
                  ))
                )
              )}
            </div>
            <div className="card-paper rounded-lg p-4">
              {actorQuery.isLoading ? (
                <p className="text-sm text-muted-foreground">読み込み中...</p>
              ) : actorQuery.isError ? (
                <p className="text-sm text-destructive">{(actorQuery.error as Error)?.message}</p>
              ) : actorCandidates.length === 0 ? (
                <p className="text-sm text-muted-foreground">該当するCandidateがありません</p>
              ) : (
                actorCandidates.map((c) => (
                  <ActorCandidateRow key={c.actor_episode_candidate_id} candidate={c} />
                ))
              )}
            </div>
          </TabsContent>
          <TabsContent value="media">
            <div className="card-paper rounded-lg p-4">
              {mediaQuery.isLoading ? (
                <p className="text-sm text-muted-foreground">読み込み中...</p>
              ) : mediaQuery.isError ? (
                <p className="text-sm text-destructive">{(mediaQuery.error as Error)?.message}</p>
              ) : mediaCandidates.length === 0 ? (
                <p className="text-sm text-muted-foreground">該当するCandidateがありません</p>
              ) : (
                mediaCandidates.map((c) => (
                  <MediaCandidateRow key={c.media_episode_candidate_id} candidate={c} />
                ))
              )}
            </div>
          </TabsContent>
        </Tabs>
      </main>
    </>
  );
}
