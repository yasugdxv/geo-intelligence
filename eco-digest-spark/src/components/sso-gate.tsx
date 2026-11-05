import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { LanguageProvider, useLanguage } from "@/lib/i18n";
import { initSso, retrySso, type SsoState } from "@/lib/ssoAuth";

// SSO（OIDC）が有効なとき、ログインが完了するまで、アプリの本体（＝APIを呼ぶ画面）を表示しない。
// SSOが無効（ローカル開発など）なら、確認が終わり次第（サーバーへの問い合わせ1回）、そのまま本体を表示する。
// ルート画面で、`useSsoState()` の結果が「ready / disabled」でなければ、`SsoGateScreen` を返す（既存のJSXは、包まない）。

export function useSsoState(): SsoState {
  const [state, setState] = useState<SsoState>({ status: "checking" });
  useEffect(() => {
    let alive = true;
    void initSso().then((s) => {
      if (alive) setState(s);
    });
    return () => {
      alive = false;
    };
  }, []);
  return state;
}

export const isSsoPassed = (state: SsoState): boolean =>
  state.status === "disabled" || state.status === "ready";

function Message({ state }: { state: SsoState }) {
  const { t } = useLanguage();

  if (state.status === "needs-localhost") {
    const localhostUrl = `${window.location.protocol}//localhost:${window.location.port}${window.location.pathname}`;
    return (
      <div className="min-h-screen flex items-center justify-center p-6">
        <div className="max-w-md space-y-3 text-sm">
          <p>{t("sso.needLocalhost")}</p>
          <Button asChild>
            <a href={localhostUrl}>{t("sso.openLocalhost")}</a>
          </Button>
        </div>
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div className="min-h-screen flex items-center justify-center p-6">
        <div className="max-w-md space-y-3 text-sm">
          <p className="font-medium">{t("sso.error")}</p>
          {state.message && (
            <p className="text-xs text-muted-foreground break-words">{state.message}</p>
          )}
          <Button onClick={retrySso}>{t("sso.retry")}</Button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center text-sm text-muted-foreground gap-2">
      <Loader2 className="h-4 w-4 animate-spin" /> {t("sso.signingIn")}
    </div>
  );
}

export function SsoGateScreen({ state }: { state: SsoState }) {
  return (
    <LanguageProvider>
      <Message state={state} />
    </LanguageProvider>
  );
}
