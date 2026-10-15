import { Link, useRouterState } from "@tanstack/react-router";
import {
  Activity,
  BookOpen,
  ClipboardList,
  Database,
  FileStack,
  FlaskConical,
  GraduationCap,
  Home,
  Inbox,
  LayoutGrid,
  Leaf,
  MessageSquare,
  Newspaper,
  Radar,
  Sparkles,
  Sunrise,
  Upload,
  Users,
  Wrench,
} from "lucide-react";
import { useLanguage } from "@/lib/i18n";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarSeparator,
} from "@/components/ui/sidebar";

// Platform IA / Expert AI Reading UI Cleanup（2026-10-02）: 業務構造ベースの3カテゴリ
// （インテリジェンス/ナレッジ/分析）＋開発者向けへ再編する。URLは一切変更しない（既存
// routeは削除も改名もしないため、ブックマーク・直接URLアクセス・他コンポーネントからの
// 既存リンクは全て従来通り動作する）。バックエンド・Expert Reasoning Core・
// Reading Exposure Composer・Reading Presentationには一切触れていない。
//
// Phase 1 Audit結果（Existing→New Menu Mapping）:
// - 「エキスパートAI相談」: 旧collapsible（エキスパートAI＋プロジェクト管理の2項目）を
//   廃止し、/geo/ios/analysisへの単一直接リンクにする（ユーザー指示「読み方提案に特化」）。
//   プロジェクト管理(/geo/ios/board)は開発者向けへ退避（削除しない）。
// - 「レポートレビュー」: 旧「レビューフロー」と同じ/geo/ios/review-intakeを指すまま、
//   ラベルのみ変更しインテリジェンスグループへ移動。
// - 「情報ソースDB」: 既存だがどのSidebarグループにも接続されていなかった
//   /geo/sources-documents route（データコネクタ管理が中心のUI、Phase1監査で確認）を
//   接続する。エンドユーザー向け検索UIとしては発展途上——Known GapsでQuality Gapとして
//   報告する。
// - 「外部専門家DB」: Phase1監査で該当機能が存在しないことを確認（GAP）。新規DB実装は
//   せず、非活性プレースホルダーとして表示するのみ（IA構造上の位置を示すだけ）。
// - 「分析」グループ: Intelligence AIを独立カテゴリへ格上げ。
// - Monitoringは新IAの5メニューに含まれないため開発者向けへ退避（既存route削除なし）。
const GEO_PRIMARY_ROUTES = [{ key: "nav.geoHome", url: "/geo", icon: Home }] as const;

const GEO_INTELLIGENCE_ROUTES = [
  // Phase E（2026-10-06）: 「何を見るべきか」の入口をグループの先頭に置く。既存IA（本グループの
  // 並び: テーマ探索→モニタリング→エキスパートAI相談→リサーチスタジオ→レポートレビュー→
  // インテリジェンス運用）とは整合させつつ、新しい分析AIは作らない（既存資産を束ねるだけ）。
  { key: "nav.geoDailyBrief", url: "/geo/daily-brief", icon: Sunrise },
  { key: "nav.geoThemeDiscovery", url: "/geo/theme-discovery", icon: Newspaper },
  // 2026-10-05: ユーザー向けMonitoring（選んだテーマの変化を継続して見る）。既存の開発者向け
  // /geo/monitoring（指標カード）は開発者セクションに残し、こちらは別route。
  { key: "nav.geoMonitoringThemes", url: "/geo/monitoring-themes", icon: Radar },
  { key: "nav.expertConsultation", url: "/geo/ios/analysis", icon: Sparkles },
  { key: "nav.reportWorkspace.research", url: "/geo/report-studio", icon: FileStack },
  { key: "nav.geoReviewFlow", url: "/geo/ios/review-intake", icon: ClipboardList },
  // 2026-10-05 Phase C: 担当者が朝開いて「何が止まっていて次に何をするか」を確認する運用画面。
  { key: "nav.geoOperations", url: "/geo/operations", icon: Activity },
] as const;

const GEO_KNOWLEDGE_ROUTES = [
  // 2026-10-05: 情報ソースDBは新しいSource Library（検索・Detail・CRUD）へ向ける。旧Connector
  // 実行画面（/geo/sources-documents）は開発者セクションへ退避（route/機能は無改修）。
  { key: "nav.geoSourcesDB", url: "/geo/source-library", icon: Database },
  { key: "nav.geoReportKnowledgeDB", url: "/geo/knowledge-search", icon: BookOpen },
  { key: "nav.geoIntelligenceNeeds", url: "/geo/intelligence-needs", icon: Inbox },
] as const;

const GEO_EXTERNAL_NETWORK_ROUTES = [
  { key: "nav.geoExternalContacts", url: "/geo/external-network/contacts", icon: Users },
  { key: "nav.geoExternalLogs", url: "/geo/external-network/logs", icon: ClipboardList },
] as const;

const GEO_ANALYSIS_ROUTES = [
  { key: "nav.geoIntelligenceAI", url: "/geo/intelligence-ai", icon: MessageSquare },
] as const;

const GEO_DEV_ROUTES = [
  { key: "nav.geoNewsSignals", url: "/geo/news-signals", icon: Newspaper },
  { key: "nav.geoExpertAIClassic", url: "/geo/expert", icon: FlaskConical },
  { key: "nav.geoLearningCandidates", url: "/geo/ios/learning-candidates", icon: GraduationCap },
  { key: "nav.geoLearningWorkbench", url: "/geo/ios/learning-workbench", icon: Wrench },
  { key: "nav.geoCqasSignals", url: "/geo/ios/cqas-signals", icon: Activity },
  { key: "nav.reportWorkspace.projects", url: "/geo/ios/board", icon: LayoutGrid },
  { key: "nav.geoReportRegistration", url: "/geo/report-registration", icon: Upload },
  { key: "nav.geoMonitoring", url: "/geo/monitoring", icon: Activity },
  { key: "nav.geoSourceConnectors", url: "/geo/sources-documents", icon: Database },
] as const;

export function AppSidebar() {
  const pathname = useRouterState({ select: (r) => r.location.pathname });
  // "/geo"(Home)はどのgeo.*配下パスにも前方一致してしまうため、Homeだけ完全一致で判定する
  const isActive = (url: string) =>
    url === "/geo" ? pathname === url : pathname === url || pathname.startsWith(`${url}/`);
  const { t } = useLanguage();

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="px-4 pt-5 pb-3">
        <Link to="/geo" className="flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-md bg-sidebar-primary text-sidebar-primary-foreground">
            <Leaf className="h-4.5 w-4.5" strokeWidth={2.2} />
          </div>
          <div className="flex flex-col leading-tight group-data-[collapsible=icon]:hidden">
            <span className="text-editorial text-lg text-sidebar-foreground">Verdant</span>
            <span className="text-[10px] uppercase tracking-[0.18em] text-sidebar-foreground/60">
              Geopolitical Intelligence
            </span>
          </div>
        </Link>
      </SidebarHeader>

      <SidebarContent className="px-2">
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              {GEO_PRIMARY_ROUTES.map((item) => (
                <SidebarMenuItem key={item.url}>
                  <SidebarMenuButton asChild isActive={isActive(item.url)}>
                    <Link to={item.url}>
                      <item.icon />
                      <span>{t(item.key)}</span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        {/* テーマ探索 / エキスパートAI相談 / レポートレビュー */}
        <SidebarGroup>
          <SidebarGroupLabel>{t("nav.geoGroupIntelligence")}</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {GEO_INTELLIGENCE_ROUTES.map((item) => (
                <SidebarMenuItem key={item.url}>
                  <SidebarMenuButton asChild isActive={isActive(item.url)}>
                    <Link to={item.url}>
                      <item.icon />
                      <span>{t(item.key)}</span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        {/* 情報ソースDB / レポートDB / ニーズDB */}
        <SidebarGroup>
          <SidebarGroupLabel>{t("nav.geoGroupKnowledge")}</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {GEO_KNOWLEDGE_ROUTES.map((item) => (
                <SidebarMenuItem key={item.url + item.key}>
                  <SidebarMenuButton asChild isActive={isActive(item.url)}>
                    <Link to={item.url}>
                      <item.icon />
                      <span>{t(item.key)}</span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        {/* 外部ネットワーク（2026-10-09: 旧「外部専門家DB 準備中」を置き換え）。外部専門家・関係者の
            コンタクトと面談ログ。Expert AIの根拠には自動投入しない独立した記録。 */}
        <SidebarGroup>
          <SidebarGroupLabel>{t("nav.geoExternalNetwork")}</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {GEO_EXTERNAL_NETWORK_ROUTES.map((item) => (
                <SidebarMenuItem key={item.url}>
                  <SidebarMenuButton asChild isActive={isActive(item.url)}>
                    <Link to={item.url}>
                      <item.icon />
                      <span>{t(item.key)}</span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        {/* インテリジェンスAI */}
        <SidebarGroup>
          <SidebarGroupLabel>{t("nav.geoGroupAnalysis")}</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {GEO_ANALYSIS_ROUTES.map((item) => (
                <SidebarMenuItem key={item.url}>
                  <SidebarMenuButton asChild isActive={isActive(item.url)}>
                    <Link to={item.url}>
                      <item.icon />
                      <span>{t(item.key)}</span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        {/* 開発者向けセクション: 新IAの通常導線から外れる既存機能を退避するだけで、
            route/component/backendは一切削除しない（後続Phaseで個別判断）。 */}
        <SidebarGroup className="mt-auto">
          <SidebarSeparator className="mb-2" />
          <SidebarGroupLabel>{t("nav.geoDevSection")}</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {GEO_DEV_ROUTES.map((item) => (
                <SidebarMenuItem key={item.url}>
                  <SidebarMenuButton asChild isActive={isActive(item.url)}>
                    <Link to={item.url}>
                      <item.icon />
                      <span>{t(item.key)}</span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
    </Sidebar>
  );
}
