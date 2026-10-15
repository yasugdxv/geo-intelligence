import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import { useLanguage } from "@/lib/i18n";

// TopBarの歯車アイコン（元々は未配線の飾りボタンだった）から開く設定ダイアログ。
// あなたの名前/ID（geo_ios_actor_id、既存の各IOS画面がaudit/created_by用に読んでいる
// localStorageキー）と、Geopolitical Intelligence OS Pilotの認証Credential
// （geo_ios_credential、geoIosAuthHeader.tsが読むキー）をここでまとめて設定できるように
// する。Credential自体はconfig.json（server-side、.gitignore対象）でのみ発行され、
// Pilot参加者へは別途（口頭・社内チャット等）配布される想定——この画面はその値を
// ブラウザへ保存するだけで、値の発行・検証ロジックには一切触れない（Phase I-A.1
// Trust Boundaryの絶対条件を継承）。既存値は再表示しない（保存済みかどうかのみ表示）。
const ACTOR_ID_KEY = "geo_ios_actor_id";
const CREDENTIAL_KEY = "geo_ios_credential";

export function GeoSettingsDialog({ children }: { children: React.ReactNode }) {
  const { t } = useLanguage();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [actorId, setActorId] = useState("");
  const [credential, setCredential] = useState("");
  const [hasStoredCredential, setHasStoredCredential] = useState(false);

  const load = () => {
    if (typeof window === "undefined") return;
    setActorId(localStorage.getItem(ACTOR_ID_KEY) ?? "");
    setCredential("");
    setHasStoredCredential(!!localStorage.getItem(CREDENTIAL_KEY));
  };

  const save = () => {
    if (typeof window === "undefined") return;
    if (actorId.trim()) localStorage.setItem(ACTOR_ID_KEY, actorId.trim());
    if (credential.trim()) localStorage.setItem(CREDENTIAL_KEY, credential.trim());
    queryClient.invalidateQueries();
    setOpen(false);
  };

  const clearCredential = () => {
    if (typeof window === "undefined") return;
    localStorage.removeItem(CREDENTIAL_KEY);
    setHasStoredCredential(false);
    setCredential("");
    queryClient.invalidateQueries();
  };

  return (
    <Dialog open={open} onOpenChange={(v) => { setOpen(v); if (v) load(); }}>
      <DialogTrigger asChild>{children}</DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("geo.settings.title")}</DialogTitle>
          <DialogDescription>{t("geo.settings.description")}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">{t("geo.settings.actorIdLabel")}</label>
            <Input value={actorId} onChange={(e) => setActorId(e.target.value)} placeholder={t("geo.settings.actorIdPlaceholder")} />
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">{t("geo.settings.credentialLabel")}</label>
            <Input
              type="password" value={credential} onChange={(e) => setCredential(e.target.value)}
              placeholder={hasStoredCredential ? t("geo.settings.credentialConfiguredPlaceholder") : t("geo.settings.credentialPlaceholder")}
            />
            <p className="text-xs text-muted-foreground">{t("geo.settings.credentialHint")}</p>
            {hasStoredCredential && (
              <Button variant="ghost" size="sm" className="text-destructive px-0" onClick={clearCredential}>
                {t("geo.settings.clearCredential")}
              </Button>
            )}
          </div>
        </div>
        <DialogFooter>
          <Button onClick={save}>{t("geo.settings.save")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
