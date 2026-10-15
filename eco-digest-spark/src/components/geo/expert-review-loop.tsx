import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  useAddReviewNote, useExtractDeltas, useKeepCaseOnly, usePromoteChange, useReinforceChange,
  useRejectChange, useReviewChanges, useReviewNotes,
} from "@/lib/geoKnowledgeApi";

// 地政学アプリClooseout Phase5: geo.cases.$id.tsxにインラインJSXとして実装されていた
// Expert Review UI（Note追加・Delta Extraction・Promote/Reinforce/Keep/Reject）を、
// Case専用のハードコードを外して独立コンポーネントへ切り出したもの。
// Review Session自体の開始方法（Case: 手動でdraft/final本文入力、Report: ファイルアップロード）は
// ページごとに異なるため、このコンポーネントは「reviewSessionIdが既に確立している状態」から
// 引き継ぐ（Session開始UI自体はここに含めない）。Case詳細ページ・Report詳細ページの両方から
// 同じコンポーネントをreviewSessionIdだけ渡して呼び出せる。
export function ExpertReviewLoop({ reviewSessionId, actorId }: { reviewSessionId: string; actorId: string }) {
  const [reviewNoteText, setReviewNoteText] = useState("");
  const [reinforceTargets, setReinforceTargets] = useState<Record<string, string>>({});

  const reviewNotesQuery = useReviewNotes(reviewSessionId);
  const addReviewNoteMutation = useAddReviewNote(reviewSessionId);
  const extractDeltasMutation = useExtractDeltas(reviewSessionId);
  const reviewChangesQuery = useReviewChanges(reviewSessionId);
  const promoteChangeMutation = usePromoteChange(reviewSessionId);
  const reinforceChangeMutation = useReinforceChange(reviewSessionId);
  const keepCaseOnlyMutation = useKeepCaseOnly(reviewSessionId);
  const rejectChangeMutation = useRejectChange(reviewSessionId);

  return (
    <div className="space-y-3">
      <Badge variant="outline">Session: {reviewSessionId}</Badge>

      <div className="space-y-1">
        {(reviewNotesQuery.data?.notes ?? []).map((n) => (
          <div key={n.note_id} className="text-base geo-prose border-t border-border pt-2">{n.comment_text}</div>
        ))}
        <div className="flex gap-2">
          <Input value={reviewNoteText} onChange={(e) => setReviewNoteText(e.target.value)}
                 placeholder="Review Comment（この修正をした理由、任意）" className="text-sm" />
          <Button size="sm" variant="outline" disabled={addReviewNoteMutation.isPending}
                  onClick={() => {
                    if (!reviewNoteText.trim()) return;
                    addReviewNoteMutation.mutate(
                      { comment_text: reviewNoteText, author: actorId },
                      { onSuccess: () => setReviewNoteText("") },
                    );
                  }}>
            Note追加
          </Button>
        </div>
        {addReviewNoteMutation.isError && (
          <p className="text-xs text-destructive">{(addReviewNoteMutation.error as Error).message}</p>
        )}
      </div>

      <Button size="sm" disabled={extractDeltasMutation.isPending}
              onClick={() => extractDeltasMutation.mutate(actorId)}>
        {extractDeltasMutation.isPending ? "抽出中..." : "Delta Extraction実行"}
      </Button>
      {extractDeltasMutation.isError && (
        <p className="text-xs text-destructive">{(extractDeltasMutation.error as Error).message}</p>
      )}

      <div className="space-y-2 pt-2 border-t border-border">
        {(reviewChangesQuery.data?.changes ?? []).map((c) => (
          <div key={c.change_id} className="text-sm border border-border rounded p-2 space-y-1">
            <div className="flex flex-wrap gap-2 items-center">
              <Badge variant="outline">{c.change_type}</Badge>
              <Badge variant={c.reusability_class === "reusable_reasoning" ? "default" : "secondary"}>
                {c.reusability_class === "reusable_reasoning" ? "再利用可能な思考方法" : "本件・時点固有の判断"}
              </Badge>
              <Badge variant="outline">{c.disposition}</Badge>
            </div>
            <p className="text-base geo-prose">{c.reasoning_summary}</p>
            {c.disposition === "pending" && (
              <div className="flex flex-wrap gap-2 items-center pt-1">
                <Button size="sm" variant="outline" disabled={promoteChangeMutation.isPending}
                        onClick={() => promoteChangeMutation.mutate({ changeId: c.change_id, reviewer_id: actorId })}>
                  Promote
                </Button>
                <Input value={reinforceTargets[c.change_id] ?? ""} placeholder="既存knowledge_item_id"
                       className="max-w-[220px] text-xs h-8"
                       onChange={(e) => setReinforceTargets((prev) => ({ ...prev, [c.change_id]: e.target.value }))} />
                <Button size="sm" variant="outline" disabled={reinforceChangeMutation.isPending}
                        onClick={() => {
                          const target = reinforceTargets[c.change_id];
                          if (!target) return;
                          reinforceChangeMutation.mutate({
                            changeId: c.change_id, reviewer_id: actorId, reinforced_knowledge_item_id: target,
                          });
                        }}>
                  Reinforce
                </Button>
                <Button size="sm" variant="ghost" disabled={keepCaseOnlyMutation.isPending}
                        onClick={() => keepCaseOnlyMutation.mutate({ changeId: c.change_id, reviewer_id: actorId })}>
                  Keep Case Only
                </Button>
                <Button size="sm" variant="ghost" disabled={rejectChangeMutation.isPending}
                        onClick={() => rejectChangeMutation.mutate({ changeId: c.change_id, reviewer_id: actorId })}>
                  Reject
                </Button>
              </div>
            )}
            {promoteChangeMutation.isError && (
              <p className="text-xs text-destructive">{(promoteChangeMutation.error as Error).message}</p>
            )}
            {reinforceChangeMutation.isError && (
              <p className="text-xs text-destructive">{(reinforceChangeMutation.error as Error).message}</p>
            )}
            {keepCaseOnlyMutation.isError && (
              <p className="text-xs text-destructive">{(keepCaseOnlyMutation.error as Error).message}</p>
            )}
            {rejectChangeMutation.isError && (
              <p className="text-xs text-destructive">{(rejectChangeMutation.error as Error).message}</p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
