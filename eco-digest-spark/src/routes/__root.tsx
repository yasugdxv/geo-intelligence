import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  Outlet,
  Link,
  createRootRouteWithContext,
  useRouter,
  useRouterState,
  HeadContent,
  Scripts,
} from "@tanstack/react-router";
import { useEffect, type ReactNode } from "react";

import appCss from "../styles.css?url";
import { reportLovableError } from "../lib/lovable-error-reporting";
import { SidebarProvider } from "@/components/ui/sidebar";
import { AppSidebar } from "@/components/app-sidebar";
import { LanguageProvider } from "@/lib/i18n";
import { ensureIosIdentity } from "@/lib/geoIosAuthHeader";

function NotFoundComponent() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="max-w-md text-center">
        <h1 className="text-7xl font-bold text-foreground">404</h1>
        <h2 className="mt-4 text-xl font-semibold text-foreground">Page not found</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          The page you're looking for doesn't exist or has been moved.
        </p>
        <div className="mt-6">
          <Link
            to="/"
            className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          >
            Go home
          </Link>
        </div>
      </div>
    </div>
  );
}

function ErrorComponent({ error, reset }: { error: Error; reset: () => void }) {
  console.error(error);
  const router = useRouter();
  useEffect(() => {
    reportLovableError(error, { boundary: "tanstack_root_error_component" });
  }, [error]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="max-w-md text-center">
        <h1 className="text-xl font-semibold tracking-tight text-foreground">
          This page didn't load
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Something went wrong on our end. You can try refreshing or head back home.
        </p>
        <div className="mt-6 flex flex-wrap justify-center gap-2">
          <button
            onClick={() => {
              router.invalidate();
              reset();
            }}
            className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          >
            Try again
          </button>
          <a
            href="/"
            className="inline-flex items-center justify-center rounded-md border border-input bg-background px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-accent"
          >
            Go home
          </a>
        </div>
      </div>
    </div>
  );
}

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1" },
      { title: "Verdant — Sustainability Intelligence" },
      {
        name: "description",
        content:
          "グローバルなサスティナビリティ関連ニュース・レポートをAIで収集・要約する、CSR/ESG部門のためのインテリジェンス・ダッシュボード。",
      },
      { name: "author", content: "Verdant" },
      { property: "og:title", content: "Verdant — Sustainability Intelligence" },
      {
        property: "og:description",
        content: "サスティナビリティ担当者のためのAIインテリジェンス・ダッシュボード。",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
    links: [
      {
        rel: "stylesheet",
        href: appCss,
      },
      { rel: "icon", href: "/favicon.ico", type: "image/x-icon" },
      {
        rel: "stylesheet",
        href: "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=M+PLUS+Rounded+1c:wght@400;500;700;800;900&family=Instrument+Serif:ital@0;1&display=swap",
      },
    ],
  }),
  shellComponent: RootShell,
  component: RootComponent,
  notFoundComponent: NotFoundComponent,
  errorComponent: ErrorComponent,
});

function RootShell({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        <HeadContent />
      </head>
      <body>
        {children}
        <Scripts />
      </body>
    </html>
  );
}

function RootComponent() {
  const { queryClient } = Route.useRouteContext();
  const pathname = useRouterState({ select: (r) => r.location.pathname });
  const isGeo = pathname.startsWith("/geo");

  // Dialog/Popover/Tooltip等のRadix Portalはdocument.body直下に描画されるため、
  // .geo-shellをラッパーdivに付けるだけではPortal配下に配色が継承されない。
  // geo.*ページ表示中のみbodyにも.geo-shellを付与し、Portal内の要素も
  // 同じCSS変数を参照できるようにする（SPAのため一度に1ルームしか表示されず安全）。
  useEffect(() => {
    document.body.classList.toggle("geo-shell", isGeo);
    return () => {
      document.body.classList.remove("geo-shell");
    };
  }, [isGeo]);

  // 2026-10-07追加: geo.*ページを初めて開いたブラウザが、Credentialを手入力しなくても
  // 書き込み操作（モニタリング追加・テーマ探索実行等）を行えるよう、低権限(analyst)の
  // Identityを裏側で自動取得する（設定ダイアログからの手動設定は引き続き可能）。
  useEffect(() => {
    if (isGeo) void ensureIosIdentity();
  }, [isGeo]);

  return (
    <QueryClientProvider client={queryClient}>
      <LanguageProvider>
        <SidebarProvider>
          {/* geo.*ページ配下では.geo-shellでGeo Intelligence Platform配色に切り替える
              （AppSidebarもこの内側にあるため、サイドバーも含めて自動的にリスキンされる。
              非geoページはこのクラスの外なので既存のVerdant配色のまま変わらない） */}
          <div className={`min-h-screen flex w-full bg-background ${isGeo ? "geo-shell" : ""}`}>
            <AppSidebar />
            <div className="flex-1 min-w-0 flex flex-col">
              <Outlet />
            </div>
          </div>
        </SidebarProvider>
      </LanguageProvider>
    </QueryClientProvider>
  );
}
