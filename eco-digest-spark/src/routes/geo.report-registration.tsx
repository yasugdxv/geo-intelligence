import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { Loader2, Upload } from "lucide-react";
import { TopBar } from "@/components/top-bar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { GeoCard, GeoCardBody, GeoCardHeader } from "@/components/geo/geo-card";
import { GeoConfidentialityLabelSelect } from "@/components/geo/geo-confidentiality-label-select";
import { ThemeCombobox } from "@/routes/geo.knowledge-search";
import {
  useAnalyzeReport,
  useUploadReport,
  type GeoConfidentialityLabel,
} from "@/lib/geoKnowledgeApi";
import { getIosActorId } from "@/lib/geoIosAuthHeader";
import { useLanguage } from "@/lib/i18n";

// Phase 2D（IA再編）: 「レポート登録」— 完成済みレポートをReview Flowを通さず直接
// Report/Knowledge DBへ登録する専用フロー。Upload → ファイル解析 → AI Metadata
// Extraction（geo_report_service.analyze_report_upload、DBへは何も書かない）→
// User Confirmation/Edit → Register（既存POST /api/geo/reports、registration_source=
// "direct_registration"を付与）。既存/geo/knowledge-searchのUploadダイアログ・検索機能は
// 無変更のまま維持し、このrouteは新設のみ（既存route削除なし）。
export const Route = createFileRoute("/geo/report-registration")({
  component: GeoReportRegistrationPage,
});

const REPORT_KINDS = ["internal_report", "external_report"] as const;

function joinList(items: string[] | undefined): string {
  return (items ?? []).join(", ");
}

function splitList(value: string): string[] {
  return value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function GeoReportRegistrationPage() {
  const { t } = useLanguage();
  const analyzeMutation = useAnalyzeReport();
  const uploadMutation = useUploadReport();

  const [file, setFile] = useState<File | null>(null);
  const [analyzed, setAnalyzed] = useState(false);

  const [title, setTitle] = useState("");
  const [reportDate, setReportDate] = useState("");
  const [region, setRegion] = useState("");
  const [topics, setTopics] = useState("");
  const [keyActors, setKeyActors] = useState("");
  const [author, setAuthor] = useState("");
  const [organization, setOrganization] = useState("");
  const [reportType, setReportType] = useState("");
  const [language, setLanguage] = useState("");
  const [keywords, setKeywords] = useState("");
  const [summary, setSummary] = useState("");
  const [businessContext, setBusinessContext] = useState("");
  const [notExplicitFields, setNotExplicitFields] = useState<string[]>([]);
  const [themeId, setThemeId] = useState("");
  const [reportKind, setReportKind] = useState<string>("internal_report");
  const [confidentialityLabel, setConfidentialityLabel] = useState<GeoConfidentialityLabel | "">(
    "",
  );
  const [registeredReportId, setRegisteredReportId] = useState<string | null>(null);

  const handleFileSelected = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0] ?? null;
    setFile(f);
    setAnalyzed(false);
    setRegisteredReportId(null);
  };

  const handleAnalyze = () => {
    if (!file) return;
    analyzeMutation.mutate(file, {
      onSuccess: ({ metadata }) => {
        setTitle(metadata.title ?? "");
        setReportDate(metadata.report_date ?? "");
        setRegion(joinList(metadata.region));
        setTopics(joinList(metadata.topics));
        setKeyActors(joinList(metadata.key_actors));
        setAuthor(metadata.author ?? "");
        setOrganization(metadata.organization ?? "");
        setReportType(metadata.report_type ?? "");
        setLanguage(metadata.language ?? "");
        setKeywords(joinList(metadata.keywords));
        setSummary(metadata.summary ?? "");
        setBusinessContext(metadata.business_context ?? "");
        setNotExplicitFields(metadata.not_explicit_fields ?? []);
        setAnalyzed(true);
      },
    });
  };

  const canRegister = !!file && analyzed && !!themeId && !!confidentialityLabel;

  const handleRegister = () => {
    if (!file || !canRegister || !confidentialityLabel) return;
    uploadMutation.mutate(
      {
        file,
        title,
        themeId,
        reportKind,
        author,
        ownerDepartment: organization,
        reportType,
        countryRegion: splitList(region),
        topics: splitList(topics),
        keyActors: splitList(keyActors),
        keywords: splitList(keywords),
        summary,
        businessContext,
        registrationSource: "direct_registration",
        confidentialityLabel,
        // 2026-10-09修正: actor_id未指定だとサーバー既定の固定文字列"analyst"が
        // created_byになり、実在のIdentityと一致せず閲覧できなくなる経路があるため
        // （geo.knowledge-search.tsxの同種修正と同じ理由）、実際のIdentityを送る。
        actorId: getIosActorId() ?? undefined,
      },
      {
        onSuccess: (result) => {
          setRegisteredReportId(result.report.report_id);
        },
      },
    );
  };

  const startOver = () => {
    setFile(null);
    setAnalyzed(false);
    setRegisteredReportId(null);
  };

  return (
    <>
      <TopBar breadcrumb={[{ label: t("nav.geoReportRegistration") }]} />
      <main className="flex-1 max-w-[820px] w-full mx-auto px-8 py-8 space-y-6">
        <section className="border-b border-border pb-5">
          <h1 className="text-editorial text-4xl">{t("geo.reportRegistration.title")}</h1>
          <p className="text-sm text-muted-foreground mt-2">
            {t("geo.reportRegistration.subtitle")}
          </p>
        </section>

        {registeredReportId ? (
          <GeoCard>
            <GeoCardBody className="space-y-3 text-center py-8">
              <p className="text-base">{t("geo.reportRegistration.registeredSuccess")}</p>
              <div className="flex items-center justify-center gap-3">
                <Link to="/geo/knowledge-search">
                  <Button size="sm">{t("geo.reportRegistration.viewInDb")}</Button>
                </Link>
                <Button size="sm" variant="outline" onClick={startOver}>
                  {t("geo.reportRegistration.registerAnother")}
                </Button>
              </div>
            </GeoCardBody>
          </GeoCard>
        ) : (
          <>
            <GeoCard>
              <GeoCardHeader title={t("geo.reportRegistration.step1Title")} />
              <GeoCardBody className="space-y-3">
                <Input
                  type="file"
                  accept=".pdf,.docx,.html,.htm,.txt,.md"
                  onChange={handleFileSelected}
                />
                <Button
                  size="sm"
                  disabled={!file || analyzeMutation.isPending}
                  onClick={handleAnalyze}
                >
                  {analyzeMutation.isPending ? (
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  ) : (
                    <Upload className="h-4 w-4 mr-2" />
                  )}
                  {analyzeMutation.isPending
                    ? t("geo.reportRegistration.analyzing")
                    : t("geo.reportRegistration.analyzeButton")}
                </Button>
                {analyzeMutation.isError && (
                  <p className="text-xs text-destructive">
                    {(analyzeMutation.error as Error).message}
                  </p>
                )}
              </GeoCardBody>
            </GeoCard>

            {analyzed && (
              <GeoCard>
                <GeoCardHeader
                  title={t("geo.reportRegistration.step2Title")}
                  meta={t("geo.reportRegistration.step2Hint")}
                />
                <GeoCardBody className="space-y-3">
                  {notExplicitFields.length > 0 && (
                    <p className="text-xs text-muted-foreground">
                      {t("geo.reportRegistration.notExplicitHint")}: {notExplicitFields.join(", ")}
                    </p>
                  )}
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1 col-span-2">
                      <label className="text-xs text-muted-foreground">
                        {t("geo.knowledge.search.uploadReportTitle")}
                      </label>
                      <Input value={title} onChange={(e) => setTitle(e.target.value)} />
                    </div>
                    <div className="space-y-1">
                      <label className="text-xs text-muted-foreground">
                        {t("geo.reportRegistration.reportDate")}
                      </label>
                      <Input
                        type="date"
                        value={reportDate}
                        onChange={(e) => setReportDate(e.target.value)}
                      />
                    </div>
                    <div className="space-y-1">
                      <label className="text-xs text-muted-foreground">
                        {t("geo.reportRegistration.language")}
                      </label>
                      <Input value={language} onChange={(e) => setLanguage(e.target.value)} />
                    </div>
                    <div className="space-y-1">
                      <label className="text-xs text-muted-foreground">
                        {t("geo.knowledge.search.uploadReportCountryRegion")}
                      </label>
                      <Input value={region} onChange={(e) => setRegion(e.target.value)} />
                    </div>
                    <div className="space-y-1">
                      <label className="text-xs text-muted-foreground">
                        {t("geo.knowledge.search.uploadReportTopics")}
                      </label>
                      <Input value={topics} onChange={(e) => setTopics(e.target.value)} />
                    </div>
                    <div className="space-y-1 col-span-2">
                      <label className="text-xs text-muted-foreground">
                        {t("geo.reportRegistration.keyActors")}
                      </label>
                      <Input value={keyActors} onChange={(e) => setKeyActors(e.target.value)} />
                    </div>
                    <div className="space-y-1">
                      <label className="text-xs text-muted-foreground">
                        {t("geo.knowledge.search.uploadReportAuthor")}
                      </label>
                      <Input value={author} onChange={(e) => setAuthor(e.target.value)} />
                    </div>
                    <div className="space-y-1">
                      <label className="text-xs text-muted-foreground">
                        {t("geo.reportRegistration.organization")}
                      </label>
                      <Input
                        value={organization}
                        onChange={(e) => setOrganization(e.target.value)}
                      />
                    </div>
                    <div className="space-y-1">
                      <label className="text-xs text-muted-foreground">
                        {t("geo.knowledge.search.uploadReportType")}
                      </label>
                      <Input value={reportType} onChange={(e) => setReportType(e.target.value)} />
                    </div>
                    <div className="space-y-1">
                      <label className="text-xs text-muted-foreground">
                        {t("geo.knowledge.search.uploadReportKind")}
                      </label>
                      <Select value={reportKind} onValueChange={setReportKind}>
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {REPORT_KINDS.map((k) => (
                            <SelectItem key={k} value={k}>
                              {t(`geo.knowledge.search.uploadReportKind.${k}`)}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1 col-span-2">
                      <label className="text-xs text-muted-foreground">
                        {t("geo.reportRegistration.keywords")}
                      </label>
                      <Input value={keywords} onChange={(e) => setKeywords(e.target.value)} />
                    </div>
                    <div className="space-y-1 col-span-2">
                      <label className="text-xs text-muted-foreground">
                        {t("geo.reportRegistration.summary")}
                      </label>
                      <Textarea
                        value={summary}
                        onChange={(e) => setSummary(e.target.value)}
                        rows={3}
                      />
                    </div>
                    <div className="space-y-1 col-span-2">
                      <label className="text-xs text-muted-foreground">
                        {t("geo.reportRegistration.businessContext")}
                      </label>
                      <Textarea
                        value={businessContext}
                        onChange={(e) => setBusinessContext(e.target.value)}
                        rows={2}
                      />
                    </div>
                    <div className="space-y-1 col-span-2">
                      <label className="text-xs text-muted-foreground">
                        {t("geo.knowledge.search.uploadReportTheme")}（
                        {t("geo.knowledge.search.required")}）
                      </label>
                      <ThemeCombobox value={themeId} onChange={setThemeId} />
                    </div>
                  </div>

                  <div className="space-y-1 pt-2 border-t border-border">
                    <label className="text-xs text-muted-foreground">
                      {t("geo.confidentiality.label")}
                    </label>
                    <GeoConfidentialityLabelSelect
                      value={confidentialityLabel}
                      onChange={setConfidentialityLabel}
                    />
                  </div>

                  <div className="flex items-center gap-3 pt-2">
                    <Button
                      disabled={!canRegister || uploadMutation.isPending}
                      onClick={handleRegister}
                    >
                      {uploadMutation.isPending && (
                        <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                      )}
                      {t("geo.reportRegistration.registerButton")}
                    </Button>
                    {uploadMutation.isError && (
                      <span className="text-xs text-destructive">
                        {(uploadMutation.error as Error).message}
                      </span>
                    )}
                  </div>
                </GeoCardBody>
              </GeoCard>
            )}
          </>
        )}
      </main>
    </>
  );
}
