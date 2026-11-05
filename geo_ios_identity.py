"""Geopolitical Intelligence OS Phase I-A.1: Pilot-grade Authenticated Identity層（SEC-2）。

既存の2つの仕組みとは別物であり、置き換えるものでもない:
  - geo_role_guard.require_role: 自己申告roleの許可リスト照合のみ（開発専用、非認証）。
  - geo_intelligence_auth.CallerIdentity: Cross-Agent（他アプリ→本アプリ）境界専用の
    認証（Phase F）。

本モジュールは、IOSの人間アクター操作（Approval/Review/Learning/Publication）向けに、
「authenticated identity → server-side assigned role → authorization」の順序を
機械的に強制する。request payload側のrole自己申告（例: approver_role=knowledge_steward）は
authorizationの根拠に使わない——audit/mismatch detection以外の用途に使ってはならない
（SEC-1対応の絶対条件）。

これはBroad Production向けの最終RBAC実装ではない。正式な社内認証基盤（Entra ID等）が
導入されるまでの、Controlled Pilot専用のtrust boundaryである。credential→identity_id→role
のマッピングはDB（public.geo_ios_identities、server-side SupabaseClient経由でのみ読める、
クライアントは指定不可）で管理する——2026-09-24変更、ユーザー指示「アカウント管理は
configではなくDBでやろう」。以前はconfig.jsonのgeo_ios.identitiesで管理していたが、
アカウント追加・無効化のたびに秘密情報ファイルを手編集する運用が続かないため、
sql/2026-09-24d_geo_ios_identities.sqlのテーブルへ移した（credential自体の値・
authorizationの仕組み・「認証済みidentity→server-side role→authorization」の順序強制は
一切変えていない）。

DBにgeo_ios_identitiesが空の場合、このモジュールを経由する全endpointは認証エラー
（401）で拒否する——geo_intelligence_auth.verify_api_keyのような「未設定なら開発用に
無検証で通す」フォールバックはここでは採用しない。SEC-1/SEC-2はまさに自己申告バイパスを
塞ぐためのものであり、ここに抜け道を残すと意味がないため（既存のFakeSupabaseClient
ベースの単体テストは、HTTPを経由せずservice関数をrole文字列付きで直接呼ぶため、この層の
影響を受けない）。

_IDENTITY_REGISTRYは既存テスト（monkeypatch.setattr(geo_ios_identity, "_IDENTITY_REGISTRY",
{...})多数）との互換のため、起動時に1回だけDBから読み込む素朴なmodule-levelキャッシュの
ままにしている（config.json時代と同じ「サーバー再起動で反映」という制約を維持するだけで、
新しい挙動は増やさない）。"""
import base64
import json
import os
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone

import jwt
import requests

from fastapi import Header, HTTPException

from config_utils import load_config, make_proxies
from geo_db_client import SupabaseClient
import geo_role_guard

# 2026-10-07追加（ユーザー指示）: 「Credentialを手でコピペしないと何も書き込めない」という
# UXへの不満への対応。SEC-1/SEC-2のTrust Boundary（authenticated identity→server-side
# assigned role→authorization、payloadのrole自己申告は使わない）は一切変えない——
# 自動発行されるroleは常にこの固定の低権限ロールのみで、呼び出し元が指定することはできない
# （geo_ios_identity_api.pyの/auto-provisionはrole引数を一切受け取らない）。
# 承認(approver)・公開(knowledge_steward)等の強い権限が必要な利用者は、既存のPilot用
# named credential（設定ダイアログで手入力）を引き続き使う。
AUTO_PROVISION_ROLE = "analyst"


class IosIdentity:
    """verify_ios_identityが返す、認証済みIOSアクターの識別情報。

    credential自体は保持しない（__repr__にも出さない）。identity_id/assigned_role/domainは
    すべてserver-side configから解決された値であり、リクエストのペイロードから来ることは
    ない。"""

    __slots__ = ("identity_id", "assigned_role", "domain", "department", "authenticated")

    def __init__(self, *, identity_id: str = None, assigned_role: str = None,
                 domain: str = None, department: str = None, authenticated: bool = False):
        self.identity_id = identity_id
        self.assigned_role = assigned_role
        self.domain = domain
        # Phase 2E（Report/Knowledge Confidentiality）: DEPARTMENT_ONLY/SHD_ONLY判定に使う
        # 所属部門。domain（Cross-Agent境界用の別概念）とは独立。geo_ios_identities.department
        # が未設定（null）の間はNoneのままとなり、department一致判定は常にfalse
        # （fail-closed、geo_knowledge_access_policy.can_human_access参照）。
        self.department = department
        self.authenticated = authenticated

    def __repr__(self) -> str:  # pragma: no cover - デバッグ表示用、credential自体は出さない
        return (f"IosIdentity(identity_id={self.identity_id!r}, assigned_role={self.assigned_role!r}, "
                f"domain={self.domain!r}, department={self.department!r}, "
                f"authenticated={self.authenticated})")


def _load_identity_registry(client: SupabaseClient) -> dict:
    """public.geo_ios_identities（is_active=trueのみ）を、credential文字列 ->
    {identity_id, role, domain}の辞書へ正規化する。role値がgeo_role_guard.ROLESに無い場合は
    安全側でそのエントリ自体を登録しない（テーブルのcheck制約で通常起こらないが、防御的に
    残す）。"""
    # Phase 2E: department列はsql/2026-09-28b_geo_report_knowledge_access_policy.sqlで追加
    # （手動適用が必要、既存運用）。未適用の環境でこのSELECT自体が失敗し、identity検証全体が
    # 全滅（全401）することを避けるため、department列有りで一度試し、失敗したら列無しの
    # 従来SELECTへフォールバックする（その場合departmentは常にNoneのまま＝fail-closed側の
    # 挙動に留まるだけで、既存のrole/domain認証自体は引き続き機能する）。
    try:
        rows = client.select(
            "geo_ios_identities",
            params={"select": "identity_id,credential,role,domain,department", "is_active": "eq.true"},
        )
    except Exception:
        rows = client.select(
            "geo_ios_identities",
            params={"select": "identity_id,credential,role,domain", "is_active": "eq.true"},
        )
    registry = {}
    for entry in rows:
        credential = entry.get("credential")
        role = entry.get("role")
        if not credential or role not in geo_role_guard.ROLES:
            continue
        registry[credential] = {
            "identity_id": entry.get("identity_id") or "unknown",
            "role": role,
            "domain": entry.get("domain"),
            "department": entry.get("department"),
        }
    return registry


_config = load_config()
_client = SupabaseClient(_config)
try:
    _IDENTITY_REGISTRY = _load_identity_registry(_client)
except Exception:
    # sql/2026-09-24d_geo_ios_identities.sqlがまだ適用されていない環境（Supabase Dashboardで
    # 手動適用するまでの間、このモジュールをimportする全プロセスがクラッシュしてしまうのを
    # 防ぐ）。空dictはこのモジュールの既定の安全側動作と一致する——「DBにgeo_ios_identitiesが
    # 空の場合、このモジュールを経由する全endpointは認証エラー（401）で拒否する」という
    # 冒頭docstringの方針どおり、テーブル未適用時も全リクエストを拒否するだけで済む。
    _IDENTITY_REGISTRY = {}


# ---------------------------------------------------------------------------
# SSO（Azure App Service の認証 = Easy Auth / Microsoft Entra ID）
#
# ログイン画面・セッション・ログアウトは、Azureが担当する。ログイン済みのリクエストには、Azureが
# ユーザーの識別子（X-MS-CLIENT-PRINCIPAL-ID = Entra の oid）・UPN（X-MS-CLIENT-PRINCIPAL-NAME）・
# 要求（X-MS-CLIENT-PRINCIPAL）をヘッダーで付ける。アプリは、そのヘッダーから geo_ios_identities の
# ユーザーに対応づける（初回は analyst で自動登録。昇格はDBで行う）。
#
# 安全の条件（なりすまし防止）:
#   - GEO_SSO_ENABLED=true のときだけSSOの経路を使う（既定は無効＝従来の資格情報方式。ローカル開発・テスト用）。
#   - SSOが有効なときは、Azureの認証が実際に有効（WEBSITE_AUTH_ENABLED=True、Azureが付ける環境変数）でなければ、
#     ヘッダーを信用せず、すべて401にする（認証が外れた状態で、偽のヘッダーで入られないように）。
#   - SSOが有効なときは、資格情報（X-IOS-Credential）を受け付けない（SSOに一本化）。
#   - SSOが無効なときは、X-MS-CLIENT-PRINCIPAL-* を無視する。
# ---------------------------------------------------------------------------
SSO_CACHE_TTL_SECONDS = 300          # ロール変更・無効化が、この時間内に反映される（再起動不要）
_SSO_CACHE: dict = {}                # oid → {"entry": {...}, "at": 取得時刻}
_SSO_LOCK = threading.Lock()
_SSO_COLUMNS = "identity_id,role,domain,department,is_active,email,sso_subject,display_name"
_SSO_SQL_HINT = "SSOのユーザー表を読み書きできません（sql/2026-10-23_geo_ios_identities_sso.sql が未適用の可能性）"
_CLAIM_NAME = ("name", "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name")
_CLAIM_EMAIL = ("preferred_username", "email", "emails", "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
                "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/upn")


def sso_enabled() -> bool:
    return os.environ.get("GEO_SSO_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def _easy_auth_active() -> bool:
    return os.environ.get("WEBSITE_AUTH_ENABLED", "").strip().lower() == "true"


def _s(value) -> str | None:
    """ヘッダー値を文字列にする。FastAPIのDependsを通さない直接呼び出しでは、Header(default=None)の
    オブジェクトが渡るため、文字列以外は「無い」とみなす。"""
    return value.strip() if isinstance(value, str) and value.strip() else None


def _principal_claims(principal_b64: str | None) -> dict:
    """X-MS-CLIENT-PRINCIPAL（base64のJSON）から、要求の一覧を取り出す。読めなければ空。"""
    if not principal_b64:
        return {}
    try:
        raw = json.loads(base64.b64decode(principal_b64 + "=" * (-len(principal_b64) % 4)).decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    claims: dict = {}
    for c in raw.get("claims") or []:
        typ, val = c.get("typ"), c.get("val")
        if isinstance(typ, str) and isinstance(val, str):
            claims.setdefault(typ, val)
    claims["__auth_typ"] = raw.get("auth_typ")
    return claims


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sso_deny(detail: str, status: int = 401):
    raise HTTPException(status_code=status, detail=detail)


def _entry_of(row: dict) -> dict:
    return {"identity_id": row["identity_id"], "role": row["role"], "domain": row.get("domain"),
            "department": row.get("department")}


def _sso_lookup_or_provision(oid: str, email: str | None, name: str | None) -> dict:
    """oid（Entra の不変の識別子）で、ユーザーを探す。無ければ、メールで既存のIDに紐づける（初回ログイン）。
    それも無ければ、analyst で新規登録する。"""
    try:
        rows = _client.select("geo_ios_identities", {"select": _SSO_COLUMNS, "sso_subject": f"eq.{oid}", "limit": "1"})
    except Exception:  # noqa: BLE001
        _sso_deny(_SSO_SQL_HINT)
    row = rows[0] if rows else None
    if row is None and email:
        email_l = email.lower()
        try:
            candidates = _client.select("geo_ios_identities", {"select": _SSO_COLUMNS})      # 無効なIDも含める（無効化したユーザーを、新規登録し直させないため）
        except Exception:  # noqa: BLE001
            _sso_deny(_SSO_SQL_HINT)
        for r in candidates:
            if (r.get("email") or "").lower() != email_l:
                continue
            if r.get("sso_subject") and r["sso_subject"] != oid:
                _sso_deny("このメールアドレスは、別のSSOアカウントに紐づいています。管理者へ連絡してください", 403)
            if r.get("is_active") is False:
                _sso_deny("このアカウントは無効化されています。管理者へ連絡してください", 403)
            row = r
            patch = {"sso_subject": oid, "last_login_at": _now_iso()}
            if name and not r.get("display_name"):
                patch["display_name"] = name
            _client.update("geo_ios_identities", {"identity_id": f"eq.{r['identity_id']}"}, patch)
            break
    if row is None:
        new = {"identity_id": f"sso_{uuid.uuid4().hex[:12]}", "credential": secrets.token_urlsafe(32),   # 使われない（SSOでは資格情報を受け付けない）
               "role": AUTO_PROVISION_ROLE, "domain": None, "is_active": True, "created_by": "sso",
               "email": email, "sso_subject": oid, "display_name": name, "last_login_at": _now_iso()}
        try:
            _client.insert("geo_ios_identities", [new])
            row = new
        except Exception:  # noqa: BLE001 — 同時ログインで、先に登録された場合は、そのユーザーを使う
            try:
                again = _client.select("geo_ios_identities", {"select": _SSO_COLUMNS, "sso_subject": f"eq.{oid}", "limit": "1"})
            except Exception:  # noqa: BLE001
                again = []
            if not again:
                _sso_deny(_SSO_SQL_HINT)
            row = again[0]
    if row.get("is_active") is False:
        _sso_deny("このアカウントは無効化されています。管理者へ連絡してください", 403)
    return _entry_of(row)


def identity_from_sso(principal_id, principal_name=None, principal=None) -> IosIdentity:
    """Azureの認証ヘッダーから、IosIdentityを作る。条件を満たさなければ401/403。"""
    if not _easy_auth_active():
        _sso_deny("SSOが有効ですが、Azureの認証（App Service Authentication）が有効ではありません")
    oid = _s(principal_id)
    if not oid:
        _sso_deny("SSOのログインが必要です")
    claims = _principal_claims(_s(principal))
    if claims.get("__auth_typ") not in (None, "aad"):
        _sso_deny("対応していないログイン方式です（Microsoft Entra ID のみ）")
    email = _s(principal_name) or next((claims[k] for k in _CLAIM_EMAIL if claims.get(k)), None)
    name = next((claims[k] for k in _CLAIM_NAME if claims.get(k)), None)
    return _identity_for(oid, email, name)


def _identity_for(oid: str, email: str | None, name: str | None) -> IosIdentity:
    """SSOのユーザー（oid）を、geo_ios_identities のユーザーに対応づける（Easy Auth・OIDC 共通）。結果は5分キャッシュする。"""
    now = time.monotonic()
    with _SSO_LOCK:
        hit = _SSO_CACHE.get(oid)
    if hit and now - hit["at"] < SSO_CACHE_TTL_SECONDS:
        entry = hit["entry"]
    else:
        entry = _sso_lookup_or_provision(oid, email, name)
        with _SSO_LOCK:
            _SSO_CACHE[oid] = {"entry": entry, "at": now}
    return IosIdentity(identity_id=entry["identity_id"], assigned_role=entry["role"], domain=entry.get("domain"),
                       department=entry.get("department"), authenticated=True)


# ---------------------------------------------------------------------------
# OIDC（Microsoft Entra ID・SPA型。画面のブラウザが MSAL.js でログインし、IDトークンをAPIに付ける）
#
# 申請（SSO questionnaire）の登録は「Single-page application」。ログインはブラウザ側で行い、APIは
# `Authorization: Bearer <IDトークン>` を検証する（署名・発行元・対象・期限・テナント）。
#   GEO_SSO_ENABLED=true と GEO_SSO_MODE=oidc（既定）、ENTRA_TENANT_ID、ENTRA_CLIENT_ID が必要。
#   テナントID・クライアントIDは、秘密情報ではない（画面にも渡す）。シークレットは、持たない。
# OIDCモードでは、Azureのヘッダー（X-MS-CLIENT-PRINCIPAL-*）と資格情報（X-IOS-Credential）は、すべて無視する。
# ---------------------------------------------------------------------------
JWKS_TTL_SECONDS = 3600              # 署名鍵のキャッシュ時間
JWKS_REFETCH_MIN_SECONDS = 60        # 未知の鍵IDで取り直す最短の間隔（偽のトークンで、鍵の取得を連発させないため）
_JWKS: dict = {"keys": {}, "at": 0.0, "tried_at": 0.0}
_JWKS_LOCK = threading.Lock()


def sso_mode() -> str:
    """"oidc"（既定。Entra ID・SPA型）または "easyauth"（Azure App Service の認証）。"""
    return "easyauth" if os.environ.get("GEO_SSO_MODE", "oidc").strip().lower() == "easyauth" else "oidc"


def _entra_tenant() -> str | None:
    return _s(os.environ.get("ENTRA_TENANT_ID"))


def _entra_client() -> str | None:
    return _s(os.environ.get("ENTRA_CLIENT_ID"))


def sso_public_config() -> dict:
    """画面が、ログインの方式を知るための設定（秘密情報は含まない）。"""
    enabled = sso_enabled()
    tenant, client = _entra_tenant(), _entra_client()
    return {"enabled": enabled, "mode": sso_mode() if enabled else None,
            "tenantId": tenant if enabled and sso_mode() == "oidc" else None,
            "clientId": client if enabled and sso_mode() == "oidc" else None,
            "authority": f"https://login.microsoftonline.com/{tenant}" if enabled and sso_mode() == "oidc" and tenant else None}


def _fetch_jwks() -> dict:
    """Entraの署名鍵（公開鍵）の一覧を取得する。社内プロキシ・SSL設定は、config.json に従う（Perplexity連携と同じ）。"""
    url = f"https://login.microsoftonline.com/{_entra_tenant()}/discovery/v2.0/keys"
    resp = requests.get(url, timeout=10, proxies=make_proxies(_config), verify=_config.get("ssl", {}).get("verify", True))
    resp.raise_for_status()
    return resp.json()


def _signing_key(kid: str | None):
    if not kid:
        _sso_deny("トークンが不正です（鍵IDがありません）")
    now = time.monotonic()
    with _JWKS_LOCK:
        cached = _JWKS["keys"].get(kid)
        fresh = now - _JWKS["at"] < JWKS_TTL_SECONDS
        recently_tried = now - _JWKS["tried_at"] < JWKS_REFETCH_MIN_SECONDS
        if cached is not None and fresh:
            return cached
        if cached is None and _JWKS["keys"] and recently_tried:
            _sso_deny("署名鍵が見つかりません")          # 取得して間もない。未知の鍵IDのために、取り直さない
        _JWKS["tried_at"] = now
    try:
        jwks = _fetch_jwks()
        keys = {k["kid"]: jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(k))
                for k in jwks.get("keys", []) if k.get("kty") == "RSA" and k.get("kid")}
    except Exception:  # noqa: BLE001 — 取得に失敗したときは、古い鍵が残っていれば、それを使う（Entra側の一時的な障害で、全員が締め出されないように）
        if cached is not None:
            return cached
        _sso_deny("署名鍵を取得できません。しばらくしてからやり直してください", 503)
    with _JWKS_LOCK:
        _JWKS["keys"], _JWKS["at"] = keys, now
    if kid not in keys:
        _sso_deny("署名鍵が見つかりません")
    return keys[kid]


def validate_entra_id_token(token: str) -> dict:
    """EntraのIDトークンを検証して、要求（claims）を返す。署名（RS256）・発行元・対象（クライアントID）・期限・テナントを確認する。"""
    tenant, client = _entra_tenant(), _entra_client()
    if not tenant or not client:
        _sso_deny("SSOの設定（ENTRA_TENANT_ID / ENTRA_CLIENT_ID）が未完了です", 503)
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        _sso_deny("トークンが不正です")
    if header.get("alg") != "RS256":                 # alg=none・HS256 などの偽装を、受け付けない
        _sso_deny("トークンが不正です（署名方式）")
    key = _signing_key(header.get("kid"))
    try:
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=client,
                            issuer=f"https://login.microsoftonline.com/{tenant}/v2.0",
                            options={"require": ["exp", "iat", "iss", "aud", "sub"]}, leeway=60)
    except jwt.ExpiredSignatureError:
        _sso_deny("ログインの有効期限が切れました。もう一度ログインしてください")
    except jwt.PyJWTError:
        _sso_deny("トークンを検証できませんでした")
    if claims.get("tid") != tenant or not claims.get("oid"):
        _sso_deny("このテナントのユーザーではありません")
    return claims


def validate_bearer(authorization) -> dict:
    """Authorization ヘッダー（Bearer <IDトークン>）を検証して、要求（claims）を返す。"""
    value = _s(authorization)
    if not value or not value.lower().startswith("bearer ") or not value[7:].strip():
        _sso_deny("SSOのログインが必要です")
    return validate_entra_id_token(value[7:].strip())


def identity_from_oidc(authorization) -> IosIdentity:
    claims = validate_bearer(authorization)
    email = next((claims[k] for k in ("preferred_username", "email", "upn") if isinstance(claims.get(k), str) and claims[k]), None)
    name = claims.get("name") if isinstance(claims.get("name"), str) else None
    return _identity_for(claims["oid"], email, name)


def verify_ios_identity(x_ios_credential: str | None = Header(default=None),
                        x_ms_client_principal_id: str | None = Header(default=None),
                        x_ms_client_principal_name: str | None = Header(default=None),
                        x_ms_client_principal: str | None = Header(default=None),
                        authorization: str | None = Header(default=None)) -> IosIdentity:
    """FastAPIのDependsとして使う。credentialが無い、または未登録の場合は401で拒否する
    （missing identity / unknown identity はいずれもDENY、§Security Test必須項目）。
    SSOが有効なとき（GEO_SSO_ENABLED=true）は、Azureの認証ヘッダーだけで判定し、資格情報は受け付けない。"""
    if sso_enabled():
        if sso_mode() == "oidc":
            return identity_from_oidc(authorization)
        return identity_from_sso(x_ms_client_principal_id, x_ms_client_principal_name, x_ms_client_principal)
    if not x_ios_credential or x_ios_credential not in _IDENTITY_REGISTRY:
        raise HTTPException(status_code=401, detail="無効または未指定のX-IOS-Credentialです")
    entry = _IDENTITY_REGISTRY[x_ios_credential]
    return IosIdentity(
        identity_id=entry["identity_id"], assigned_role=entry["role"],
        domain=entry.get("domain"), department=entry.get("department"), authenticated=True)


def get_optional_ios_identity(x_ios_credential: str | None = Header(default=None),
                              x_ms_client_principal_id: str | None = Header(default=None),
                              x_ms_client_principal_name: str | None = Header(default=None),
                              x_ms_client_principal: str | None = Header(default=None),
                              authorization: str | None = Header(default=None)) -> IosIdentity:
    """Phase 2E: Report/Knowledge一覧・検索・詳細取得のような「未ログインでも一応閲覧できる
    べきだが、権限に応じて見える範囲を絞りたい」読み取り経路向け。credential未指定なら
    匿名（authenticated=False）のIosIdentityを返す（401にしない）。credentialが指定
    されたが無効/未登録の場合はverify_ios_identityと同じく401で拒否する——不正な
    credentialを「とりあえず匿名として通す」と、typo/漏洩したcredentialでの試行錯誤を
    黙って許してしまうため（credential未指定と無効指定は区別して扱う）。"""
    if sso_enabled():          # SSO有効時: ログイン済み（トークン／Azureのヘッダーあり）ならそのユーザー。無ければ匿名（認証の対象外のパス用）
        if sso_mode() == "oidc":
            return identity_from_oidc(authorization) if _s(authorization) else IosIdentity(authenticated=False)
        if not _s(x_ms_client_principal_id):
            return IosIdentity(authenticated=False)
        return identity_from_sso(x_ms_client_principal_id, x_ms_client_principal_name, x_ms_client_principal)
    if not x_ios_credential:
        return IosIdentity(authenticated=False)
    return verify_ios_identity(x_ios_credential)


def resolve_reviewer_identity(identity_id: str | None) -> dict | None:
    """Review Cycle作成時のreviewer spoofing対策（SEC）。clientから送られてくるreviewer
    identity_id文字列（UI Selectで選ばれた値）を、server-side geo_ios.identitiesの登録済み
    エントリと照合し、role=expert_reviewerであることまで確認する。UI Selectの選択肢制御は
    Security Boundaryにならない——ここで存在確認とrole確認の両方を行う。見つからない、
    またはrole不一致ならNoneを返す（呼び出し側でHTTPException(403)等に変換する）。"""
    if not identity_id:
        return None
    for entry in _IDENTITY_REGISTRY.values():
        if entry["identity_id"] == identity_id and entry["role"] == "expert_reviewer":
            return entry
    return None


def check_role_mismatch(identity: IosIdentity, claimed_role: str | None) -> str | None:
    """audit/mismatch detection専用（authorizationには使わない、モジュールdocstring参照）。
    claimed_role（request payload由来の自己申告値、互換性のため受け取るだけの値）が
    authenticated roleと食い違う場合に一言だけ返す。一致 or claimed_role未指定ならNone。"""
    if claimed_role and claimed_role != identity.assigned_role:
        return (f"payload claimed_role={claimed_role!r} != authenticated assigned_role="
                f"{identity.assigned_role!r} (authorization used assigned_role only)")
    return None


def auto_provision_identity(client: SupabaseClient) -> dict:
    """ブラウザが初めてアクセスした時に、人手の設定作業なしで低権限のIdentityを1つ払い出す
    （2026-10-07追加）。role は AUTO_PROVISION_ROLE 固定——呼び出し元から変更できない。
    DBへ1行insertすると同時に、このプロセスの_IDENTITY_REGISTRYにも即座に反映する
    （_load_identity_registryは起動時1回だけの読み込みのため、反映しないと同一プロセス内で
    発行直後のcredentialが401になってしまう）。戻り値のcredentialは呼び出し元
    （/auto-provisionエンドポイント）がレスポンスとして返す1回限りの値——ここでも
    ログには出さない。"""
    if sso_enabled():
        raise HTTPException(status_code=404, detail="SSOが有効なため、IDの自動発行は使えません")
    identity_id = f"auto_{uuid.uuid4().hex[:12]}"
    credential = secrets.token_urlsafe(24)
    client.insert("geo_ios_identities", [{
        "identity_id": identity_id, "credential": credential, "role": AUTO_PROVISION_ROLE,
        "domain": None, "is_active": True, "created_by": "auto_provision",
    }])
    _IDENTITY_REGISTRY[credential] = {
        "identity_id": identity_id, "role": AUTO_PROVISION_ROLE, "domain": None, "department": None,
    }
    return {"identity_id": identity_id, "credential": credential, "role": AUTO_PROVISION_ROLE}


def check_identity_mismatch(identity: IosIdentity, claimed_identity_id: str | None) -> str | None:
    """P1 Identity Attribution Closure（ユーザー指示、2026-09-23、Mechanical E2E Pilot後の
    Known P1対応）: check_role_mismatchと同じ方針。approver/reviewed_by/publisher/created_by等、
    「このin-app操作を実際に行った人物」を表す表示名フィールドは、request payload由来の
    自己申告値ではなく認証済みidentity.identity_idを正規の記録値として使う（呼び出し元API層の
    責務）。本関数はaudit用のmismatch検知にのみ使う——claimed_identity_id（互換性のため
    受け取るだけの値）が認証済みidentity_idと食い違う場合に一言だけ返す。一致 or未指定ならNone。

    Route B（Upload/外部Approval等、別人が別プロセスで行った操作をこのAPI呼び出し者が代理登録
    する経路）は対象外——その場合はclaimed値こそが正しいattributionであり、認証済み呼び出し者の
    identity_idで上書きしてはならない（各serviceモジュールのdocstring参照）。"""
    if claimed_identity_id and claimed_identity_id != identity.identity_id:
        return (f"payload claimed_identity={claimed_identity_id!r} != authenticated identity_id="
                f"{identity.identity_id!r} (canonical attribution used authenticated identity_id only)")
    return None
