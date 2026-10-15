import { Link } from "@tanstack/react-router";
import { Bell, Settings } from "lucide-react";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { GeoSettingsDialog } from "@/components/geo-settings-dialog";
import { useLanguage } from "@/lib/i18n";
import { type Lang } from "@/lib/api";

function LanguageSwitcher() {
  const { lang, setLang, t } = useLanguage();
  const option = (value: Lang) => (
    <button
      onClick={() => setLang(value)}
      className={`px-2 py-1 rounded-full transition-colors ${
        lang === value ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
      }`}
    >
      {t(`lang.${value}`)}
    </button>
  );
  return (
    <div className="flex items-center gap-0.5 text-xs border border-border rounded-full p-0.5 bg-muted/30">
      {option("ja")}
      {option("en")}
    </div>
  );
}

export function TopBar({
  breadcrumb,
}: {
  breadcrumb?: { label: string; href?: string }[];
}) {
  return (
    <header className="sticky top-0 z-30 h-14 border-b border-border bg-background/85 backdrop-blur-md flex items-center gap-3 px-5">
      <SidebarTrigger className="-ml-1" />
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground min-w-0 flex-1">
        {breadcrumb?.map((b, i) => (
          // min-w-0が無いとflexアイテムの既定min-width:autoにより、子のtruncateが実際には
          // 効かず長いラベルが右側のメニュー(言語切替/ベル/設定/アバター)へ重なっていた。
          <span key={i} className="flex items-center gap-1.5 min-w-0">
            {i > 0 && <span className="text-muted-foreground/40 shrink-0">/</span>}
            {b.href ? (
              <Link to={b.href} className="hover:text-foreground truncate">
                {b.label}
              </Link>
            ) : (
              <span className="text-foreground truncate">{b.label}</span>
            )}
          </span>
        ))}
      </div>

      <LanguageSwitcher />

      <div className="flex items-center gap-1">
        <button className="h-9 w-9 rounded-full hover:bg-muted flex items-center justify-center relative">
          <Bell className="h-4 w-4" />
        </button>
        <GeoSettingsDialog>
          <button className="h-9 w-9 rounded-full hover:bg-muted flex items-center justify-center">
            <Settings className="h-4 w-4" />
          </button>
        </GeoSettingsDialog>
        <div className="ml-2 h-9 w-9 rounded-full bg-gradient-to-br from-primary to-accent-leaf flex items-center justify-center text-primary-foreground text-xs font-semibold">
          MK
        </div>
      </div>
    </header>
  );
}
