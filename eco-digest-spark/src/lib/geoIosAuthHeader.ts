import { API_BASE as API_BASE_URL } from "@/lib/apiBase";
// Geopolitical Intelligence OS: Pilot-grade Authenticated Identity（Phase I-A.1）用の
// X-IOS-CredentialヘッダーをlocalStorageから読んで付与する共有ヘルパー。
//
// 2026-10-07追加（ユーザー指示）: 「Credentialを手でコピペしないと書き込み操作が
// 何もできない」という体験への不満への対応として、ensureIosIdentity()を追加した。
// 初回アクセス時にcredentialが無ければ、サーバー側の/auto-provisionで低権限
// （analyst固定、geo_ios_identity.AUTO_PROVISION_ROLE）のIdentityを自動取得して保存する。
// Trust Boundary（SEC-1/SEC-2）自体は変えていない——ここで発行されるroleは常に
// サーバー側固定値で、クライアントが指定することはできない。承認(approver)・
// 公開(knowledge_steward)等の強い権限が必要な利用者は、引き続き設定ダイアログから
// 既存のnamed pilot credentialを手入力する運用のまま。
const IOS_CREDENTIAL_KEY = "geo_ios_credential";
const IOS_ACTOR_ID_KEY = "geo_ios_actor_id";
const API_BASE = API_BASE_URL;

export function getIosAuthHeaders(): HeadersInit {
  if (typeof window === "undefined") return {};
  try {
    const credential = localStorage.getItem(IOS_CREDENTIAL_KEY);
    return credential ? { "X-IOS-Credential": credential } : {};
  } catch {
    return {};
  }
}

// Expert Session Capture V1（Phase S2-S3、2026-10-02）: このファイル冒頭の既知ギャップ
// （credential入力UIが無い）に対する最小限の対応。新しい認証方式は増やさない——同じ
// localStorageキーを読み書きするだけの薄いヘルパーを追加し、Expert Session Home画面から
// 使う。credential自体の値（実在するgeo_ios_identities.credential）は、Session前に
// ユーザーがSupabase Dashboard側で参加専門家ごとに登録しておく運用を前提とする。
/** 認証済みIdentityのidentity_id（geo-settings-dialog.tsxでの手動設定／
 * ensureIosIdentity()での自動発行のいずれかで保存される）。Report Uploadのactor_id等、
 * 「誰がこの操作をしたか」の記録に使う。未設定ならnull。 */
export function getIosActorId(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return localStorage.getItem(IOS_ACTOR_ID_KEY);
  } catch {
    return null;
  }
}

export function getIosCredential(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return localStorage.getItem(IOS_CREDENTIAL_KEY);
  } catch {
    return null;
  }
}

export function setIosCredential(credential: string): void {
  if (typeof window === "undefined") return;
  try {
    if (credential) localStorage.setItem(IOS_CREDENTIAL_KEY, credential);
    else localStorage.removeItem(IOS_CREDENTIAL_KEY);
  } catch {
    // noop: private window / blocked storage等（既存getIosAuthHeadersと同じ扱い）
  }
}

let _autoProvisionInFlight: Promise<void> | null = null;

/** credentialが無ければ、低権限(analyst)のIdentityをサーバーから自動取得して保存する。
 * 複数回呼ばれても実際のPOSTは1回だけ（同時に呼ばれた場合は同じPromiseを共有する）。
 * 失敗しても例外は投げない——その場合は従来通りcredential無しのまま、既存の401挙動になる
 * （設定ダイアログからの手動設定は引き続き使える）。 */
export function ensureIosIdentity(): Promise<void> {
  if (getIosCredential()) return Promise.resolve();
  if (_autoProvisionInFlight) return _autoProvisionInFlight;
  _autoProvisionInFlight = (async () => {
    try {
      const res = await fetch(`${API_BASE}/api/geo/ios/identities/auto-provision`, { method: "POST" });
      if (!res.ok) return;
      const data = (await res.json()) as { identity_id: string; credential: string };
      setIosCredential(data.credential);
      try {
        localStorage.setItem(IOS_ACTOR_ID_KEY, data.identity_id);
      } catch {
        // noop
      }
    } catch {
      // noop: ネットワーク不通時も従来通りcredential無しのまま進める
    }
  })();
  return _autoProvisionInFlight;
}
