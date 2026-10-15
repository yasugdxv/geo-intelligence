import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { Loader2 } from "lucide-react";
import { TopBar } from "@/components/top-bar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  useAnalysisSessions,
  useCreateAnalysisSession,
  type GeoIosAnalysisSession,
} from "@/lib/geoIosAnalysisSessionApi";
import { useLanguage } from "@/lib/i18n";

// REVIEW MODE（§17、2026-09-25追加）: Report Studioの2入口のうち「既存レポートを
// レビューする」側の最小限のエントリー（title入力→mode="review"でSession作成→
// Step1取込画面(geo.ios.review.$sessionId)へ遷移）。既存CREATE MODE入口
// （geo.ios.analysis.tsx）とは別ルートにすることで、既存Intake画面のロジックには
// 一切手を入れない。
//
// 2026-10-04変更、ユーザー指摘「IDは管理用なら自動採番でいい」: user_id（created_by
// 相当のattribution用途のみ、CREATE MODE側のような「名前で過去セッションを検索する」
// 機能はこの画面には無い）は手入力させず、ブラウザごとに1回だけ自動生成して
// localStorageへ保存する。CREATE MODE側が使うACTOR_ID_KEY（実名入力・既存セッション
// 検索に使われる）とは別のkeyにする——ここで自動生成した値で実名側を上書きしない。
const REVIEW_ACTOR_ID_KEY = "geo_ios_review_actor_id";

function getOrCreateReviewActorId(): string {
  const existing = localStorage.getItem(REVIEW_ACTOR_ID_KEY);
  if (existing) return existing;
  const generated = `reviewer-${Math.random().toString(36).slice(2, 10)}`;
  localStorage.setItem(REVIEW_ACTOR_ID_KEY, generated);
  return generated;
}

export const Route = createFileRoute("/geo/ios/review-intake")({
  component: GeoIosReviewIntakePage,
});

function ago(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return "";
  const h = ms / 3600000;
  if (h < 1) return `${Math.max(1, Math.round(ms / 60000))}分前`;
  if (h < 48) return `${Math.round(h)}時間前`;
  return `${Math.round(h / 24)}日前`;
}

function PastReviewRow({ session, t }: { session: GeoIosAnalysisSession; t: (k: string) => string }) {
  return (
    <Link
      to="/geo/ios/review-mode/$sessionId"
      params={{ sessionId: session.analysis_session_id }}
      className="flex items-center justify-between gap-3 rounded-md border border-[#d8e2e7] bg-white px-4 py-3 text-sm hover:bg-[#f4f8fa]"
    >
      <span className="min-w-0 truncate font-medium">{session.title}</span>
      <span className="shrink-0 text-xs text-[#687b88]">
        {t(`geo.ios.review.statusLabel.${session.status}`)} ・ {ago(session.created_at)}
      </span>
    </Link>
  );
}

function PastReviewsList() {
  const { t } = useLanguage();
  // 1 = とりあえず全件表示（Pilotの小人数共有ツールのため、ユーザーごとの絞り込みはしない）
  const { data, isLoading, isError, error } = useAnalysisSessions(undefined, "review");
  const sessions = data?.sessions ?? [];
  return (
    <section className="space-y-2">
      <h2 className="text-sm font-medium text-[#687b88]">{t("geo.ios.review.pastReviewsTitle")}</h2>
      {isLoading && <p className="text-xs text-[#687b88]">{t("geo.ios.review.pastReviewsLoading")}</p>}
      {isError && (
        <p className="text-xs text-destructive">
          {t("geo.ios.review.pastReviewsError", { message: (error as Error).message })}
        </p>
      )}
      {!isLoading && !isError && sessions.length === 0 && (
        <p className="text-xs text-[#687b88]">{t("geo.ios.review.pastReviewsEmpty")}</p>
      )}
      <div className="space-y-2">
        {sessions.map((s) => (
          <PastReviewRow key={s.analysis_session_id} session={s} t={t} />
        ))}
      </div>
    </section>
  );
}

function GeoIosReviewIntakePage() {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const createSession = useCreateAnalysisSession();

  const start = () => {
    if (!title.trim()) return;
    const userId = getOrCreateReviewActorId();
    createSession.mutate(
      { title: title.trim(), user_id: userId, mode: "review" },
      {
        onSuccess: (session) => {
          navigate({
            to: "/geo/ios/review-mode/$sessionId",
            params: { sessionId: session.analysis_session_id },
          });
        },
      },
    );
  };

  return (
    <>
      <TopBar
        breadcrumb={[
          { label: t("nav.geoReviewFlow"), href: "/geo/ios/review-intake" },
          { label: t("geo.ios.review.entryButton") },
        ]}
      />
      <main className="flex-1 max-w-[720px] w-full mx-auto px-8 py-8 space-y-5">
        <h1 className="geo-heading text-3xl font-semibold">{t("geo.ios.review.entryButton")}</h1>
        <p className="text-base geo-prose">{t("geo.ios.review.importSubtitle")}</p>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <div className="text-[11px] font-medium text-[#687b88]">
              {t("geo.ios.analysis.titleLabel")} <span className="text-[#d85f62]">*</span>
            </div>
            <Input value={title} onChange={(e) => setTitle(e.target.value)} />
          </div>
          <Button onClick={start} disabled={!title.trim() || createSession.isPending}>
            {createSession.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
            {t("geo.ios.review.importSubmit")}
          </Button>
          {createSession.isError && (
            <p className="text-xs text-destructive">{(createSession.error as Error).message}</p>
          )}
        </div>
        <PastReviewsList />
      </main>
    </>
  );
}
