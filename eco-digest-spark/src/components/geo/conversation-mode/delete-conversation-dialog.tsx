// 会話モードの会話を削除する前の確認ダイアログ（選んだ1件だけを削除する）。
// 削除できる場所と、削除後も残り得るものを、操作の前に明示する。
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import type { CmServerDeleteResult } from "@/lib/geoConversationModeApi";

export interface DeleteTarget {
  id: string;
  theme: string;
}

export interface DeleteOutcome {
  theme: string;
  local: "deleted" | "not_found" | "error";
  server: CmServerDeleteResult;
}

export function DeleteConversationDialog({
  target,
  deleting,
  loggedIn,
  onConfirm,
  onCancel,
}: {
  target: DeleteTarget | null;
  deleting: boolean;
  loggedIn: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <AlertDialog open={!!target} onOpenChange={(open) => !open && !deleting && onCancel()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>この会話を削除しますか？</AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="space-y-2 text-sm">
              <p className="text-foreground break-words">「{target?.theme}」</p>
              <p>
                選んだこの会話だけを削除します。ほかの会話や、通常チャットの履歴は削除しません。元に戻せません。
              </p>
              <ul className="list-disc pl-5 space-y-1">
                <li>このブラウザに保存された会話のコピーを削除します。</li>
                <li>
                  {loggedIn
                    ? "サーバーに保存されている自分の会話があれば、それも削除します（サーバー保存が使えない場合は、サーバー側は削除できません。結果は削除後に表示します）。"
                    : "未ログインのため、サーバー側の会話は削除しません（サーバーには保存されていません）。"}
                </li>
                <li>
                  サーバー側の削除後も、データベースのバックアップには、保持期間が過ぎるまで残る場合があります。
                </li>
              </ul>
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={deleting} onClick={onCancel}>
            キャンセル
          </AlertDialogCancel>
          <AlertDialogAction
            disabled={deleting}
            onClick={(e) => {
              e.preventDefault(); // 削除中もダイアログを閉じない（結果が出てから閉じる）
              onConfirm();
            }}
          >
            {deleting ? "削除しています…" : "削除する"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

const LOCAL_TEXT: Record<DeleteOutcome["local"], string> = {
  deleted: "削除しました",
  not_found: "このブラウザには見つかりませんでした（すでに削除済み）",
  error: "削除できませんでした（保存内容を読み書きできません）",
};

function serverText(r: CmServerDeleteResult): string {
  switch (r.status) {
    case "deleted":
      return "削除しました";
    case "not_found":
      return "該当する会話は見つかりませんでした（サーバーには保存されていないか、すでに削除済みです）";
    case "unavailable":
      return "サーバー保存が利用できないため、サーバー側の削除は確認できませんでした（削除できたとは言えません）";
    case "login_required":
      return "未ログインまたは認証が無効のため、サーバー側は削除していません";
    default:
      return "削除に失敗しました。サーバー上の会話は残っている可能性があります。あとでもう一度お試しください";
  }
}

/** 削除の結果。ブラウザ内とサーバーを分けて表示し、失敗したものを成功と表示しない。 */
export function DeleteOutcomeNotice({
  outcome,
  onClose,
}: {
  outcome: DeleteOutcome;
  onClose: () => void;
}) {
  const failed = outcome.local === "error" || outcome.server.status === "error";
  return (
    <div
      role={failed ? "alert" : "status"}
      aria-live={failed ? "assertive" : "polite"}
      className={`rounded-md border px-3 py-2 text-sm space-y-1 ${
        failed ? "border-destructive/50 bg-destructive/5" : "border-border bg-muted/40"
      }`}
    >
      <p className="font-medium break-words">会話「{outcome.theme}」の削除結果</p>
      <ul className="list-disc pl-5 text-xs space-y-0.5">
        <li>ブラウザ内: {LOCAL_TEXT[outcome.local]}</li>
        <li>サーバー: {serverText(outcome.server)}</li>
      </ul>
      <div>
        <button
          type="button"
          onClick={onClose}
          className="text-xs underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
        >
          閉じる
        </button>
      </div>
    </div>
  );
}
