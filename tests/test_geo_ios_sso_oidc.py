"""SSO（OIDC・Entra ID・SPA型）のテスト。テスト用のRSA鍵でIDトークンを作り、検証・ゲート・対応づけを確認する。

守っていること: 署名・発行元・対象（クライアントID）・期限・テナントの検証／alg偽装・別の鍵・別のテナントの拒否／
OIDCモードでは、Azureのヘッダーと資格情報を無視／サイト全体のゲート（/api/health と sso-config だけ公開、OPTIONSは通す）／
署名鍵の取得失敗時の挙動／テナントID・クライアントIDだけを公開（秘密は無い）。
"""
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import geo_ios_identity as g
import geo_ios_identity_api as api
import geo_sso_gate
from tests._fakes import FakeSupabaseClient

TENANT = "11111111-2222-3333-4444-555555555555"
CLIENT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
ISS = f"https://login.microsoftonline.com/{TENANT}/v2.0"

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwk_of(private_key, kid):
    d = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    d.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return d


def token(claims=None, key=KEY, kid="kid-1", alg="RS256"):
    now = int(time.time())
    base = {"iss": ISS, "aud": CLIENT, "sub": "sub-1", "oid": "oid-1", "tid": TENANT, "iat": now, "nbf": now, "exp": now + 3600,
            "preferred_username": "Hanako@Example.com", "name": "花子"}
    base.update(claims or {})
    for k in [k for k, v in base.items() if v is None]:
        del base[k]
    return jwt.encode(base, key, algorithm=alg, headers={"kid": kid})


def bearer(t=None, **kw):
    return {"authorization": "Bearer " + (t or token(**kw))}


@pytest.fixture(autouse=True)
def oidc_env(monkeypatch):
    for k in ("GEO_SSO_ENABLED", "GEO_SSO_MODE", "WEBSITE_AUTH_ENABLED", "ENTRA_TENANT_ID", "ENTRA_CLIENT_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GEO_SSO_ENABLED", "true")
    monkeypatch.setenv("ENTRA_TENANT_ID", TENANT)
    monkeypatch.setenv("ENTRA_CLIENT_ID", CLIENT)
    g._SSO_CACHE.clear()
    g._JWKS.update({"keys": {}, "at": 0.0, "tried_at": 0.0})
    calls = {"n": 0}

    def fake_jwks():
        calls["n"] += 1
        return {"keys": [jwk_of(KEY, "kid-1")]}
    monkeypatch.setattr(g, "_fetch_jwks", fake_jwks)
    monkeypatch.setattr(g, "_JWKS_STUB_CALLS", calls, raising=False)
    yield calls
    g._SSO_CACHE.clear()


@pytest.fixture
def db(monkeypatch):
    client = FakeSupabaseClient({"geo_ios_identities": [
        {"identity_id": "auto_deac2a2d86f9", "credential": "auto-cred", "role": "analyst", "domain": None, "department": None,
         "is_active": True, "email": "hanako@example.com", "sso_subject": None, "display_name": "大濱"},
    ]})
    monkeypatch.setattr(g, "_client", client)
    monkeypatch.setattr(g, "_IDENTITY_REGISTRY", {"auto-cred": {"identity_id": "auto_deac2a2d86f9", "role": "analyst", "domain": None,
                                                                "department": None}})
    return client


def deny(**kw):
    with pytest.raises(HTTPException) as e:
        g.verify_ios_identity(**kw)
    return e.value


# ---- 検証 ----
def test_valid_token_maps_to_the_existing_identity_by_email(db):
    ident = g.verify_ios_identity(**bearer())
    assert ident.identity_id == "auto_deac2a2d86f9" and ident.authenticated and ident.assigned_role == "analyst"
    assert db.tables["geo_ios_identities"][0]["sso_subject"] == "oid-1"          # 初回で、SSOアカウントに紐づく


def test_unknown_user_is_provisioned_as_analyst(db):
    ident = g.verify_ios_identity(**bearer(claims={"oid": "oid-new", "preferred_username": "new@example.com", "name": "新人"}))
    assert ident.identity_id.startswith("sso_") and ident.assigned_role == "analyst"
    row = [r for r in db.tables["geo_ios_identities"] if r["identity_id"] == ident.identity_id][0]
    assert row["email"] == "new@example.com" and row["display_name"] == "新人" and row["sso_subject"] == "oid-new"


@pytest.mark.parametrize("claims,why", [
    ({"aud": "someone-elses-client"}, "別のアプリ向けのトークン（対象が違う）"),
    ({"iss": "https://login.microsoftonline.com/other-tenant/v2.0"}, "別のテナントが発行（発行元が違う）"),
    ({"tid": "other-tenant"}, "テナントが違う"),
    ({"exp": int(time.time()) - 3600}, "期限切れ"),
    ({"oid": None}, "oid が無い"),
    ({"exp": None}, "期限が無い"),
])
def test_invalid_claims_are_rejected(db, claims, why):
    assert deny(authorization=bearer(claims=claims)["authorization"]).status_code in (401, 403), why


def test_token_signed_with_another_key_is_rejected(db):
    assert deny(authorization=bearer(key=OTHER_KEY)["authorization"]).status_code == 401


def test_alg_none_and_hs256_confusion_are_rejected(db):
    forged_none = jwt.encode({"iss": ISS, "aud": CLIENT, "sub": "x", "oid": "oid-1", "tid": TENANT, "exp": int(time.time()) + 600,
                              "iat": int(time.time())}, key=None, algorithm="none", headers={"kid": "kid-1"})
    assert deny(authorization="Bearer " + forged_none).status_code == 401
    forged_hs = jwt.encode({"iss": ISS, "aud": CLIENT, "sub": "x", "oid": "oid-1", "tid": TENANT, "exp": int(time.time()) + 600,
                            "iat": int(time.time())}, key="shared-secret-that-is-long-enough-for-hs256", algorithm="HS256", headers={"kid": "kid-1"})
    assert deny(authorization="Bearer " + forged_hs).status_code == 401           # 公開鍵を、共有鍵として使う攻撃（alg差し替え）


@pytest.mark.parametrize("value", [None, "", "Bearer", "Bearer ", "Basic abc", "abc.def.ghi", "Bearer not-a-jwt"])
def test_missing_or_malformed_authorization_is_rejected(db, value):
    assert deny(authorization=value).status_code == 401


def test_credentials_and_azure_headers_are_ignored_in_oidc_mode(db, monkeypatch):
    monkeypatch.setattr(g, "_IDENTITY_REGISTRY", {"cred": {"identity_id": "pilot_approver", "role": "approver", "domain": None, "department": None}})
    e = deny(x_ios_credential="cred")                                              # 有効な資格情報でも、通らない
    assert e.status_code == 401
    e = deny(x_ms_client_principal_id="oid-1", x_ms_client_principal_name="hanako@example.com")      # Azureのヘッダーの偽造も、通らない
    assert e.status_code == 401
    ident = g.verify_ios_identity(x_ios_credential="cred", x_ms_client_principal_id="oid-evil", **bearer())
    assert ident.identity_id == "auto_deac2a2d86f9"                                # 判定は、トークンだけ


def test_missing_configuration_fails_closed(db, monkeypatch):
    monkeypatch.delenv("ENTRA_CLIENT_ID")
    assert deny(**bearer()).status_code == 503


# ---- 署名鍵 ----
def test_signing_keys_are_cached_and_refetch_is_rate_limited(db, oidc_env):
    g.verify_ios_identity(**bearer())
    g.verify_ios_identity(**bearer())
    assert oidc_env["n"] == 1                                                      # 2回目は、キャッシュ
    for _ in range(5):                                                             # 未知の鍵IDの偽トークンで、鍵の取得を連発させない
        assert deny(authorization=bearer(kid="unknown-kid")["authorization"]).status_code == 401
    assert oidc_env["n"] <= 2


def test_key_rotation_is_picked_up_after_the_minimum_interval(db, monkeypatch):
    g.verify_ios_identity(**bearer())
    new_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(g, "_fetch_jwks", lambda: {"keys": [jwk_of(KEY, "kid-1"), jwk_of(new_key, "kid-2")]})
    g._JWKS["tried_at"] = 0.0                                                      # 最短の間隔が過ぎた
    assert g.verify_ios_identity(**bearer(key=new_key, kid="kid-2")).identity_id == "auto_deac2a2d86f9"


def test_jwks_failure_uses_stale_keys_but_denies_when_no_keys(db, monkeypatch):
    g.verify_ios_identity(**bearer())                                              # 鍵を、取得済み
    monkeypatch.setattr(g, "_fetch_jwks", lambda: (_ for _ in ()).throw(RuntimeError("entra down")))
    g._JWKS["at"] = 0.0                                                            # キャッシュの期限切れ
    g._SSO_CACHE.clear()
    assert g.verify_ios_identity(**bearer()).authenticated                         # 古い鍵で、検証を続ける（Entraの一時障害で、全員が締め出されない）
    g._JWKS.update({"keys": {}, "at": 0.0, "tried_at": 0.0})
    assert deny(**bearer()).status_code == 503                                     # 鍵が1つも無ければ、拒否


# ---- 任意の認証・ゲート・公開設定 ----
def test_optional_identity_is_anonymous_without_token_but_rejects_bad_tokens(db):
    assert g.get_optional_ios_identity().authenticated is False
    assert g.get_optional_ios_identity(**bearer()).authenticated is True
    with pytest.raises(HTTPException):
        g.get_optional_ios_identity(authorization="Bearer forged.token.value")


def make_app(db, monkeypatch):
    monkeypatch.setattr(api, "_client", db)
    app = FastAPI()
    geo_sso_gate.install(app)
    app.include_router(api.geo_ios_identity_router)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/geo/anything")
    def anything():
        return {"data": "secret"}

    @app.get("/index.html")
    def page():
        return {"page": "shell"}
    return TestClient(app)


def test_gate_protects_api_but_not_health_config_options_or_static(db, monkeypatch):
    c = make_app(db, monkeypatch)
    assert c.get("/api/geo/anything").status_code == 401
    r = c.get("/api/geo/anything", headers={"authorization": "Bearer forged.token.value"})
    assert r.status_code == 401 and r.headers.get("www-authenticate") == "Bearer"
    assert c.get("/api/geo/anything", headers=bearer()).status_code == 200
    assert c.get("/api/health").status_code == 200                                  # 正常性チェックは、ログイン不要
    assert c.get("/api/geo/ios/identities/sso-config").status_code == 200           # ログインの前に必要な設定
    assert c.get("/index.html").status_code == 200                                  # 画面の静的ファイルは、ゲートの対象外（データを含まない）
    assert c.options("/api/geo/anything").status_code != 401                        # CORSのプリフライトは、通す
    assert c.post("/api/geo/ios/identities/auto-provision").status_code == 401      # 自動発行も、ゲートの内側（認証なしでは、呼べない）


def test_gate_is_inactive_when_sso_is_off_or_in_easyauth_mode(db, monkeypatch):
    c = make_app(db, monkeypatch)
    monkeypatch.setenv("GEO_SSO_ENABLED", "false")
    assert c.get("/api/geo/anything").status_code == 200
    monkeypatch.setenv("GEO_SSO_ENABLED", "true")
    monkeypatch.setenv("GEO_SSO_MODE", "easyauth")                                  # Easy Auth は、Azureが守る（アプリのゲートは何もしない）
    assert c.get("/api/geo/anything").status_code == 200


def test_sso_config_is_public_and_has_no_secrets(db, monkeypatch):
    c = make_app(db, monkeypatch)
    body = c.get("/api/geo/ios/identities/sso-config").json()
    assert body == {"enabled": True, "mode": "oidc", "tenantId": TENANT, "clientId": CLIENT,
                    "authority": f"https://login.microsoftonline.com/{TENANT}"}
    monkeypatch.setenv("GEO_SSO_ENABLED", "false")
    assert c.get("/api/geo/ios/identities/sso-config").json() == {"enabled": False, "mode": None, "tenantId": None,
                                                                   "clientId": None, "authority": None}


def test_me_with_token(db, monkeypatch):
    c = make_app(db, monkeypatch)
    r = c.get("/api/geo/ios/identities/me", headers=bearer())
    assert r.status_code == 200 and r.json()["mode"] == "sso" and r.json()["identity_id"] == "auto_deac2a2d86f9"
    assert "credential" not in json.dumps(r.json())


def test_oidc_disabled_by_default_keeps_the_legacy_behavior(db, monkeypatch):
    monkeypatch.delenv("GEO_SSO_ENABLED")
    monkeypatch.setattr(g, "_IDENTITY_REGISTRY", {"cred": {"identity_id": "pilot_approver", "role": "approver", "domain": None, "department": None}})
    assert g.verify_ios_identity(x_ios_credential="cred").identity_id == "pilot_approver"
    assert deny(**bearer()).status_code == 401                                      # SSOが無効なら、トークンを信用しない
