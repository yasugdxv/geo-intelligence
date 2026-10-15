import { createFileRoute, Outlet, useNavigate, useRouterState } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { Loader2, Paperclip, Sparkles } from "lucide-react";
import { TopBar } from "@/components/top-bar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { GeoStatusBadge } from "@/components/geo/geo-badge";
import { useAnalysisSessions, useCreateAnalysisSession } from "@/lib/geoIosAnalysisSessionApi";
import { uploadIntakeAttachment } from "@/lib/geoIosIntakeApi";
import { useLanguage } from "@/lib/i18n";

// Geopolitical Intelligence OS Phase H §4 → Intake改修（ユーザー指示、2026-09-24）:
// 「1 前提整理」の最初の入口。ここでの入力は最小限（タイトル/分析したいこと/参考資料）に
// 留め、前提整理チャット本体はSession作成後のセッション詳細ページ
// （geo-ios-intake-view.tsx）が担う。既存Expert AI本体（geo_expert_engine.py）・
// CQASロジックには一切触れず、既存Phase C API（/api/geo/ios/analysis-sessions）と
// 新設のIntake API（/api/geo/ios/analysis-sessions/{id}/intake/*）を呼ぶだけ。
//
// 「あなたの名前/ID」: 当初は認証identityへの一本化を検討したが、実際の認証機能は
// まだ無い（ユーザー指示、2026-09-24「認証機能はあとからなので一旦は入力フィールドを
// 用意しておいて」）——本画面でも引き続き手入力を必須とし、既存Settingsダイアログと
// 同じgeo_ios_actor_idへ保存する（保存場所は一本化したまま、入力経路だけこの画面にも残す）。
const ACTOR_ID_KEY = "geo_ios_actor_id";

export const Route = createFileRoute("/geo/ios/analysis")({
  component: GeoIosAnalysisEntryPage,
});

function GeoIosAnalysisEntryPage() {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (r) => r.location.pathname });
  const [title, setTitle] = useState("");
  const [theme, setTheme] = useState("");
  // SSRとClientの初回描画を一致させるため、useStateの初期値は常に""（未読込状態）にし、
  // localStorageの読み込みはhydration完了後のuseEffectで行う（useState初期化関数内で
  // localStorageへ直接触ると、SSR側は""・Client側は既存の値、という食い違いが生まれ、
  // Hydration Mismatchでツリー全体がやり直しになってしまう——クリックしても反応が
  // 鈍く見えていた実際の原因）。
  const [userId, setUserId] = useState("");
  useEffect(() => {
    const stored = localStorage.getItem(ACTOR_ID_KEY);
    if (stored) setUserId(stored);
  }, []);
  const [files, setFiles] = useState<File[]>([]);
  const [uploadingFiles, setUploadingFiles] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const sessionsQuery = useAnalysisSessions(userId || undefined);
  const createSession = useCreateAnalysisSession();

  // /geo/ios/analysis/$sessionId（セッション詳細）は本ファイルの子ルートとして登録される
  // ため（TanStack Routerのfile-based routing規約: geo.ios.analysis.$sessionId.tsxの親は
  // 自動的にgeo.ios.analysis.tsxになる）、詳細ページ表示中は一覧を出さずOutletのみ描画する
  // （geo.cases.tsx/competitors.companies.tsxと同じ対処）。これを入れていないと、URLは
  // /geo/ios/analysis/{id}に正しく変わるのにこのエントリーページの中身が表示され続ける
  // （実際に発生していた「クリックしても画面が変わらない」の根本原因）。Hook呼び出しは
  // 全て上（useState/useEffect/useAnalysisSessions/useCreateAnalysisSession）で済ませて
  // からこの分岐に入ること（Rules of Hooks: 早期returnより前にHookを呼び終える）。
  if (pathname !== "/geo/ios/analysis") {
    return <Outlet />;
  }

  const start = () => {
    if (!title.trim() || !theme.trim() || !userId.trim()) return;
    localStorage.setItem(ACTOR_ID_KEY, userId.trim());
    const effectiveUserId = userId.trim();
    createSession.mutate(
      { title: title.trim(), user_id: effectiveUserId, theme },
      {
        onSuccess: async (session) => {
          if (files.length > 0) {
            setUploadingFiles(true);
            // Sessionの作成自体は既に成功しているため、資料アップロードが失敗しても
            // 遷移をブロックしない（ユーザーはセッション詳細ページのチャットから
            // 再度＋資料を追加できる、§4）。以前はcatchが無く、1件でも失敗すると
            // Promise.allが例外を投げてnavigateまで到達しない（無言で画面が
            // 固まって見える）不具合があった。失敗の詳細はこのページが直後に
            // 遷移してしまうため表示できない——遷移先のチャット添付一覧が実際の
            // 反映結果（0件ならアップロード失敗）を正直に示す。
            const results = await Promise.allSettled(
              files.map((file) =>
                uploadIntakeAttachment(session.analysis_session_id, file, effectiveUserId),
              ),
            );
            setUploadingFiles(false);
            const failedCount = results.filter((r) => r.status === "rejected").length;
            if (failedCount > 0) {
              console.error(`${failedCount}/${files.length} 件の資料アップロードに失敗しました`);
            }
          }
          navigate({
            to: "/geo/ios/analysis/$sessionId",
            params: { sessionId: session.analysis_session_id },
          });
        },
      },
    );
  };

  const handleFilesPicked = (picked: FileList | null) => {
    if (!picked || picked.length === 0) return;
    setFiles((prev) => [...prev, ...Array.from(picked)]);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const sessions = sessionsQuery.data?.sessions ?? [];

  return (
    <>
      <TopBar breadcrumb={[{ label: t("nav.geoExpertAI") }]} />
      <main className="flex-1 max-w-[1160px] w-full mx-auto px-8 py-8 space-y-6">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[#eaf8fb] text-[#0d879a]">
              <Sparkles className="h-5 w-5" />
            </div>
            <div>
              <h1 className="geo-heading text-2xl">{t("geo.ios.analysis.title")}</h1>
              <p className="text-sm text-muted-foreground">{t("geo.ios.analysis.subtitle")}</p>
            </div>
          </div>
          {/* §17（2026-09-25追加）: Report Studioの2入口のうち「既存レポートをレビュー
              する」側。既存Intakeフォームのロジックには一切触れず、別ルートへのリンクのみ
              追加する。 */}
          <Button variant="outline" onClick={() => navigate({ to: "/geo/ios/review-intake" })}>
            {t("geo.ios.review.entryButton")}
          </Button>
        </div>

        {/* 2026-09-24変更、ユーザー指示「相談履歴(最近のAnalysis Session)は書く必要は
            ない⇒ペインにリストしておこう」: 同日早くの「主要領域から外し、小さい
            トグルで別導線にする」という指示を撤回し、逆に常時見えるサイドリストへ
            戻す。トグル開閉ではなく、フォームの隣に独立したペインとして並べる。 */}
        <div className="grid gap-6 lg:grid-cols-[640px_1fr] items-start">
          <div className="rounded-[14px] border border-[#d8e2e7] bg-white p-[22px] space-y-4">
            <div className="space-y-1.5">
              <div className="text-[11px] font-medium text-[#687b88]">
                {t("geo.ios.analysis.yourNameLabel")} <span className="text-[#d85f62]">*</span>
              </div>
              <Input
                value={userId}
                onChange={(e) => setUserId(e.target.value)}
                placeholder={t("geo.ios.analysis.yourNamePlaceholder")}
                className="border-[#d8e2e7]"
              />
            </div>
            <div className="space-y-1.5">
              <div className="text-[11px] font-medium text-[#687b88]">
                {t("geo.ios.analysis.titleLabel")} <span className="text-[#d85f62]">*</span>
              </div>
              <Input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder={t("geo.ios.analysis.titlePlaceholder")}
                className="border-[#d8e2e7]"
              />
            </div>
            <div className="space-y-1.5">
              <div className="text-[11px] font-medium text-[#687b88]">
                {t("geo.ios.analysis.themeLabel")} <span className="text-[#d85f62]">*</span>
              </div>
              <Textarea
                value={theme}
                onChange={(e) => setTheme(e.target.value)}
                placeholder={t("geo.ios.analysis.themePlaceholder")}
                className="min-h-[120px] border-[#d8e2e7]"
              />
            </div>
            <div className="space-y-1.5">
              <div className="text-[11px] font-medium text-[#687b88]">
                {t("geo.ios.analysis.attachmentsLabel")}
              </div>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                hidden
                onChange={(e) => handleFilesPicked(e.target.files)}
              />
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => fileInputRef.current?.click()}
              >
                <Paperclip className="h-3.5 w-3.5 mr-1.5" />
                {t("geo.ios.analysis.addFiles")}
              </Button>
              {files.length > 0 && (
                <ul className="text-xs text-[#687b88] space-y-0.5 pt-1">
                  {files.map((f, i) => (
                    <li key={i} className="truncate">
                      {f.name}
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <Button
              className="w-full bg-[#36bfd6] hover:bg-[#2ea9bf] text-white"
              disabled={
                !title.trim() ||
                !theme.trim() ||
                !userId.trim() ||
                createSession.isPending ||
                uploadingFiles
              }
              onClick={start}
            >
              {(createSession.isPending || uploadingFiles) && (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              )}
              {createSession.isPending || uploadingFiles
                ? t("geo.ios.analysis.starting")
                : t("geo.ios.analysis.start")}
            </Button>
            {createSession.isError && (
              <p className="text-xs text-destructive">{(createSession.error as Error).message}</p>
            )}
          </div>

          <div className="rounded-[14px] border border-[#d8e2e7] bg-[#fbfcfd] p-[16px] space-y-2 lg:sticky lg:top-6">
            <h2 className="text-sm font-semibold text-[#102b3f]">
              {t("geo.ios.analysis.recentSessionsTitle")}
            </h2>
            {sessions.map((session) => (
              <button
                key={session.analysis_session_id}
                type="button"
                className="w-full text-left rounded-[10px] border border-[#d8e2e7] bg-white p-[11px] hover:border-[#36bfd6] transition-colors space-y-1"
                onClick={() =>
                  navigate({
                    to: "/geo/ios/analysis/$sessionId",
                    params: { sessionId: session.analysis_session_id },
                  })
                }
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="text-sm font-medium text-[#102b3f] line-clamp-2">
                    {session.title}
                  </span>
                  <GeoStatusBadge severity="neutral" className="shrink-0">
                    {session.status}
                  </GeoStatusBadge>
                </div>
                <p className="text-xs text-[#687b88] line-clamp-2">{session.theme}</p>
              </button>
            ))}
            {sessions.length === 0 && (
              <p className="text-xs text-[#687b88]">
                {userId ? t("geo.ios.analysis.noSessions") : t("geo.ios.analysis.enterNameFirst")}
              </p>
            )}
          </div>
        </div>
      </main>
    </>
  );
}
