// SSO（Microsoft Entra ID・OIDC・SPA型）のログイン。ブラウザ側で MSAL.js が、認可コードフロー（PKCE）でログインする。
//
// 流れ: サーバーの /api/geo/ios/identities/sso-config で、SSOが有効か（テナントID・クライアントID）を知る。
//   有効なら、未ログインのときは Entra のログイン画面へ移動 → 戻ってきたら、IDトークンを取得 →
//   以降、APIの呼び出しすべてに `Authorization: Bearer <IDトークン>` を、自動で付ける（fetch を1か所だけ包む）。
//   サーバー側（geo_ios_identity.py / geo_sso_gate.py）が、署名・発行元・対象・期限・テナントを検証する。
// リダイレクトURIは、実行中のオリジン（Entraの登録と一致させる）。本番はサイトのルート、ローカルは http://localhost:8080。
//   ※ 127.0.0.1 は、Entraに登録する localhost と別のホスト扱いになり、ログインできない（画面で案内する）。
// シークレットは持たない（公開クライアント）。トークンは sessionStorage（タブを閉じると消える）。
import {
  InteractionRequiredAuthError,
  PublicClientApplication,
  type AccountInfo,
} from "@azure/msal-browser";
import { API_BASE } from "@/lib/apiBase";

export type SsoStatus =
  "checking" | "disabled" | "ready" | "redirecting" | "error" | "needs-localhost";
export interface SsoState {
  status: SsoStatus;
  message?: string;
}

interface SsoPublicConfig {
  enabled: boolean;
  mode: "oidc" | "easyauth" | null;
  tenantId: string | null;
  clientId: string | null;
  authority: string | null;
}

const SCOPES = ["openid", "profile", "email"]; // IDトークンだけを使う（Graph等のAPI権限は、使わない）
const REFRESH_MARGIN_SECONDS = 120; // 期限の2分前から、静かに更新する

let msal: PublicClientApplication | null = null;
let account: AccountInfo | null = null;
let idToken: string | null = null;
let idTokenExp = 0;
let initPromise: Promise<SsoState> | null = null;
let fetchPatched = false;
const originalFetch: typeof fetch | null =
  typeof window !== "undefined" ? window.fetch.bind(window) : null;

/** 「ログイン済みのユーザー」（OIDC）。SSOが無効なときは null。 */
export function getSsoAccount(): AccountInfo | null {
  return account;
}

function isApiUrl(url: string): boolean {
  if (url.startsWith("/api/")) return true;
  return API_BASE !== "" && url.startsWith(`${API_BASE}/api/`);
}

async function refreshToken(force = false): Promise<void> {
  if (!msal || !account) return;
  const now = Math.floor(Date.now() / 1000);
  if (!force && idToken && idTokenExp - now > REFRESH_MARGIN_SECONDS) return;
  try {
    const result = await msal.acquireTokenSilent({ scopes: SCOPES, account });
    idToken = result.idToken;
    idTokenExp = Math.floor(
      (result.idTokenClaims as { exp?: number } | undefined)?.exp ?? now + 300,
    );
  } catch (e) {
    if (e instanceof InteractionRequiredAuthError) {
      await msal.loginRedirect({ scopes: SCOPES }); // 静かな更新ができないときは、もう一度ログインへ
      return;
    }
    throw e;
  }
}

/** APIへのリクエストに、IDトークンを付ける（fetch を、1か所だけ包む）。付いていれば、そのまま。 */
function installFetchPatch(): void {
  if (fetchPatched || typeof window === "undefined" || !originalFetch) return;
  fetchPatched = true;
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (!isApiUrl(url)) return originalFetch(input, init);
    await refreshToken();
    if (!idToken) return originalFetch(input, init);
    const base = init?.headers ?? (input instanceof Request ? input.headers : undefined);
    const headers = new Headers(base);
    if (!headers.has("Authorization")) headers.set("Authorization", `Bearer ${idToken}`);
    if (input instanceof Request) return originalFetch(new Request(input, { ...init, headers }));
    return originalFetch(input, { ...init, headers });
  };
}

async function fetchConfig(): Promise<SsoPublicConfig | null> {
  if (!originalFetch) return null;
  try {
    const res = await originalFetch(`${API_BASE}/api/geo/ios/identities/sso-config`);
    return res.ok ? ((await res.json()) as SsoPublicConfig) : null;
  } catch {
    return null; // サーバーに届かないときは、SSOなしとして続ける（データを返すAPIは、サーバー側が守る）
  }
}

async function start(): Promise<SsoState> {
  if (typeof window === "undefined") return { status: "disabled" };
  const cfg = await fetchConfig();
  if (!cfg || !cfg.enabled || cfg.mode !== "oidc" || !cfg.clientId || !cfg.authority)
    return { status: "disabled" };
  if (window.location.hostname === "127.0.0.1") return { status: "needs-localhost" };
  msal = new PublicClientApplication({
    auth: {
      clientId: cfg.clientId,
      authority: cfg.authority,
      redirectUri: window.location.origin,
      postLogoutRedirectUri: window.location.origin,
    },
    cache: { cacheLocation: "sessionStorage" },
  });
  await msal.initialize();
  const redirectResult = await msal.handleRedirectPromise(); // ログイン画面から戻ってきたとき
  account = redirectResult?.account ?? msal.getAllAccounts()[0] ?? null;
  if (!account) {
    await msal.loginRedirect({ scopes: SCOPES });
    return { status: "redirecting" };
  }
  msal.setActiveAccount(account);
  if (redirectResult?.idToken) {
    idToken = redirectResult.idToken;
    idTokenExp = Math.floor(
      (redirectResult.idTokenClaims as { exp?: number } | undefined)?.exp ?? 0,
    );
  }
  await refreshToken();
  installFetchPatch();
  return { status: "ready" };
}

/** SSOの初期化（何度呼んでも、1回だけ実行される）。ログインが完了すると、"ready"（SSOなしなら "disabled"）で解決する。 */
export function initSso(): Promise<SsoState> {
  if (!initPromise) {
    initPromise = start().catch((e: unknown) => ({
      status: "error" as const,
      message: e instanceof Error ? e.message : String(e),
    }));
  }
  return initPromise;
}

/** ログインの完了を待つ。ログイン画面へ移動する間は、解決しない（ページごと移動するため）。 */
export async function whenSsoReady(): Promise<void> {
  const state = await initSso();
  if (state.status === "redirecting") await new Promise(() => {});
}

/** ログアウト。OIDCなら Entra のログアウト、Easy Auth（Azure）なら /.auth/logout へ。 */
export async function ssoLogout(): Promise<void> {
  if (msal) {
    await msal.logoutRedirect({
      account: account ?? undefined,
      postLogoutRedirectUri: window.location.origin,
    });
    return;
  }
  window.location.href = "/.auth/logout?post_logout_redirect_uri=/";
}

/** ログインをやり直す（エラー画面の「もう一度」用）。 */
export function retrySso(): void {
  initPromise = null;
  msal = null;
  account = null;
  idToken = null;
  window.location.reload();
}
