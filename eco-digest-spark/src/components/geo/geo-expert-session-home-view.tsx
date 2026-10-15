import { useEffect, useState } from "react";
import { Link } from "@tanstack/react-router";
import { Loader2 } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { useLanguage } from "@/lib/i18n";
import { useExpertSessionCases } from "@/lib/geoExpertSessionCaptureApi";
import { getIosCredential, setIosCredential } from "@/lib/geoIosAuthHeader";

// Expert Session Capture V1（Phase S2-S3、2026-10-02）Session Home: Gold Cases / Calibration
// Cases / Unresolvedを分けて表示するだけの入口。AIの評価結果やPrimary Readingはここでは
// 一切見せない（ユーザー指示§2）——各Caseはtitle/type/progress/Open・Continueのみ。
function statusLabel(t: (key: string) => string, status: string): string {
  switch (status) {
    case "not_started":
      return t("geo.expertSession.statusNotStarted");
    case "in_progress":
      return t("geo.expertSession.statusInProgress");
    case "locked":
      return t("geo.expertSession.statusLocked");
    case "revealed":
      return t("geo.expertSession.statusRevealed");
    case "completed":
      return t("geo.expertSession.statusCompleted");
    default:
      return status;
  }
}

function IdentityBox() {
  const { t } = useLanguage();
  const qc = useQueryClient();
  // SSR hydration safety（2026-10-02、実ブラウザ監査で発見）: localStorageはサーバー側に
  // 存在しないため、render本体で直接getIosCredential()を読むとSSR出力（常に空/null）と
  // クライアント初回描画（実際の値）が食い違い、hydration mismatchを起こす。初期値はSSRと
  // 同じ空/nullにし、マウント後（useEffect、クライアント専用）にのみ実際の値へ補正する。
  const [value, setValue] = useState("");
  const [current, setCurrent] = useState<string | null>(null);

  useEffect(() => {
    const stored = getIosCredential();
    setValue(stored ?? "");
    setCurrent(stored);
  }, []);

  const handleSet = () => {
    const trimmed = value.trim();
    setIosCredential(trimmed);
    setCurrent(trimmed || null);
    qc.invalidateQueries({ queryKey: ["geoExpertSessionCases"] });
    qc.invalidateQueries({ queryKey: ["geoExpertSessionCase"] });
  };
  return (
    <div className="rounded-[12px] border border-[#d8e2e7] bg-[#fbfcfd] p-[14px] flex items-center gap-3 flex-wrap">
      <label className="text-[13px] font-bold text-[#172d3d] shrink-0">
        {t("geo.expertSession.identityLabel")}
      </label>
      <input
        className="flex-1 min-w-[180px] rounded-[8px] border border-[#d8e2e7] px-3 py-1.5 text-sm"
        placeholder={t("geo.expertSession.identityPlaceholder")}
        value={value}
        onChange={(e) => setValue(e.target.value)}
      />
      <Button size="sm" onClick={handleSet}>
        {t("geo.expertSession.identitySetButton")}
      </Button>
      {!current && (
        <p className="m-0 w-full text-[12px] text-[#a97c1f]">
          {t("geo.expertSession.identityMissingNote")}
        </p>
      )}
    </div>
  );
}

function CaseCard({
  title,
  typeLabel,
  status,
  isStarted,
}: {
  title: string;
  typeLabel: string;
  status: string;
  isStarted: boolean;
}) {
  const { t } = useLanguage();
  return (
    <div className="rounded-[14px] border border-[#d8e2e7] bg-white p-[18px] flex flex-col gap-2 h-full hover:border-[#9bbce7] transition-colors">
      <span className="text-[11px] font-black text-[#687b88]">{typeLabel}</span>
      <h3 className="m-0 text-[17px] font-black text-[#0b3568]">{title}</h3>
      <span className="text-[12px] text-[#48617a]">{status}</span>
      <span className="mt-2 text-[13px] font-bold text-[#0d879a]">
        {isStarted ? t("geo.expertSession.continueButton") : t("geo.expertSession.openButton")}
      </span>
    </div>
  );
}

export function GeoExpertSessionHomeView() {
  const { t } = useLanguage();
  const casesQuery = useExpertSessionCases();

  // IdentityBox（credential入力）は、cases取得の成否に関わらず常に表示する——未設定/401の
  // 間だけ画面全体がスピナーのまま固まり、identityを設定する手段自体が無くなっていた実装
  // 上の不具合（2026-10-02、実ブラウザ監査で発見）の修正。
  if (casesQuery.isLoading || casesQuery.isError || !casesQuery.data) {
    return (
      <div className="space-y-6">
        <h1 className="text-xl font-semibold text-[#172d3d]">{t("geo.expertSession.homeTitle")}</h1>
        <IdentityBox />
        {casesQuery.isLoading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground py-6">
            <Loader2 className="h-4 w-4 animate-spin" /> ...
          </div>
        ) : (
          <p className="text-sm text-muted-foreground py-6">
            {casesQuery.error instanceof Error ? casesQuery.error.message : "error"}
          </p>
        )}
      </div>
    );
  }

  const { runnable, unresolved } = casesQuery.data;
  const golds = runnable.filter((c) => c.case_type === "gold");
  const calibrations = runnable.filter((c) => c.case_type === "calibration");

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold text-[#172d3d]">{t("geo.expertSession.homeTitle")}</h1>
      <IdentityBox />

      <section className="space-y-3">
        <h2 className="text-lg font-bold text-[#0b3568]">
          {t("geo.expertSession.goldSectionTitle")}
        </h2>
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-[14px]">
          {golds.map((c) => (
            <Link
              key={c.case_key}
              to="/geo/expert-session/$caseKey"
              params={{ caseKey: c.case_key }}
            >
              <CaseCard
                title={c.title}
                typeLabel="GOLD"
                status={statusLabel(t, c.status)}
                isStarted={c.status !== "not_started"}
              />
            </Link>
          ))}
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-bold text-[#0b3568]">
          {t("geo.expertSession.calibrationSectionTitle")}
        </h2>
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-[14px]">
          {calibrations.map((c) => (
            <Link
              key={c.case_key}
              to="/geo/expert-session/$caseKey"
              params={{ caseKey: c.case_key }}
            >
              <CaseCard
                title={c.title}
                typeLabel="CALIBRATION"
                status={statusLabel(t, c.status)}
                isStarted={c.status !== "not_started"}
              />
            </Link>
          ))}
        </div>
      </section>

      {unresolved.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-lg font-bold text-[#687b88]">
            {t("geo.expertSession.unresolvedSectionTitle")}
          </h2>
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-[14px]">
            {unresolved.map((c) => (
              <div
                key={c.case_key}
                className="rounded-[14px] border border-dashed border-[#d8e2e7] bg-[#fbfcfd] p-[18px] opacity-60"
              >
                <span className="text-[11px] font-black text-[#687b88]">
                  {c.case_type.toUpperCase()}
                </span>
                <h3 className="m-0 text-[17px] font-black text-[#687b88]">{c.title}</h3>
                <span className="text-[12px] text-[#a97c1f]">
                  Session artifact unresolved — not included in current session
                </span>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
