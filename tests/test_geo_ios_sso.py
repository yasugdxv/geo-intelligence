"""SSO（Azure App Service の認証 / Entra ID）対応のテスト。

守っていること: SSOが無効なときはAzureのヘッダーを信用しない／有効でも、Azureの認証が実際に有効でなければ全て拒否／
SSO有効時は資格情報を受け付けない／初回は analyst で登録・既存IDにはメールで紐づけ／無効化・ロール変更の反映／
SQL未適用でも安全に失敗する／自動発行はSSO有効時に止まる。
"""
import base64
import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import geo_ios_identity as g
import geo_ios_identity_api as api
from tests._fakes import FakeSupabaseClient


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    g._SSO_CACHE.clear()
    monkeypatch.delenv("GEO_SSO_ENABLED", raising=False)
    monkeypatch.delenv("WEBSITE_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("GEO_SSO_MODE", raising=False)
    yield
    g._SSO_CACHE.clear()


@pytest.fixture
def sso_on(monkeypatch):
    monkeypatch.setenv("GEO_SSO_ENABLED", "true")
    monkeypatch.setenv("GEO_SSO_MODE", "easyauth")                              # このファイルは、Easy Auth（Azureのヘッダー）モードのテスト
    monkeypatch.setenv("WEBSITE_AUTH_ENABLED", "True")


@pytest.fixture
def db(monkeypatch):
    client = FakeSupabaseClient({"geo_ios_identities": [
        {"identity_id": "pilot_approver", "credential": "approver-cred", "role": "approver", "domain": None, "department": None,
         "is_active": True, "email": None, "sso_subject": None, "display_name": None},
        {"identity_id": "auto_deac2a2d86f9", "credential": "auto-cred", "role": "analyst", "domain": None, "department": None,
         "is_active": True, "email": "Taro.Yamada@Example.com", "sso_subject": None, "display_name": "大濱"},
    ]})
    monkeypatch.setattr(g, "_client", client)
    monkeypatch.setattr(g, "_IDENTITY_REGISTRY", {"approver-cred": {"identity_id": "pilot_approver", "role": "approver", "domain": None,
                                                                    "department": None}})
    return client


def principal(claims: dict | None = None, auth_typ: str = "aad") -> str:
    body = {"auth_typ": auth_typ, "claims": [{"typ": k, "val": v} for k, v in (claims or {}).items()]}
    return base64.b64encode(json.dumps(body).encode("utf-8")).decode("ascii")


def sso(oid="oid-1", name="yamada@example.com", claims=None, auth_typ="aad"):
    return dict(x_ms_client_principal_id=oid, x_ms_client_principal_name=name, x_ms_client_principal=principal(claims, auth_typ))


def rows(db):
    return db.tables["geo_ios_identities"]


# ---- なりすまし防止 ----
def test_sso_off_ignores_azure_headers(db):
    with pytest.raises(HTTPException) as e:
        g.verify_ios_identity(x_ios_credential=None, **sso())                     # 偽のヘッダーだけでは、入れない
    assert e.value.status_code == 401
    assert g.get_optional_ios_identity(x_ios_credential=None, **sso()).authenticated is False
    assert g.verify_ios_identity(x_ios_credential="approver-cred").identity_id == "pilot_approver"      # 従来の方式は、そのまま動く


def test_sso_on_but_azure_auth_inactive_fails_closed(db, monkeypatch):
    monkeypatch.setenv("GEO_SSO_ENABLED", "true")                                # WEBSITE_AUTH_ENABLED は無い
    monkeypatch.setenv("GEO_SSO_MODE", "easyauth")
    for fn in (g.verify_ios_identity, g.get_optional_ios_identity):
        with pytest.raises(HTTPException) as e:
            fn(x_ios_credential="approver-cred", **sso())                       # 偽のヘッダーも、正しい資格情報も、通さない
        assert e.value.status_code == 401 and "認証" in e.value.detail
    assert len(rows(db)) == 2                                                    # ユーザーも作られない


def test_credentials_are_not_accepted_in_sso_mode(db, sso_on):
    with pytest.raises(HTTPException) as e:
        g.verify_ios_identity(x_ios_credential="approver-cred")                 # 有効な資格情報でも、SSOのヘッダーが無ければ401
    assert e.value.status_code == 401
    ident = g.verify_ios_identity(x_ios_credential="approver-cred", **sso(oid="oid-x", name="new@example.com"))
    assert ident.identity_id != "pilot_approver" and ident.assigned_role == "analyst"      # 資格情報で、承認者にはなれない


def test_non_entra_login_is_rejected(db, sso_on):
    with pytest.raises(HTTPException) as e:
        g.verify_ios_identity(**sso(auth_typ="github"))
    assert e.value.status_code == 401


def test_missing_principal_is_rejected_but_optional_is_anonymous(db, sso_on):
    with pytest.raises(HTTPException):
        g.verify_ios_identity()
    assert g.get_optional_ios_identity().authenticated is False                  # 認証の対象外のパス（正常性チェック等）は匿名


# ---- 登録と紐づけ ----
def test_first_login_provisions_an_analyst_with_email_and_name(db, sso_on):
    ident = g.verify_ios_identity(**sso(oid="oid-new", name="hanako@example.com", claims={"name": "花子"}))
    assert ident.authenticated and ident.assigned_role == "analyst" and ident.identity_id.startswith("sso_")
    row = [r for r in rows(db) if r["identity_id"] == ident.identity_id][0]
    assert row["email"] == "hanako@example.com" and row["sso_subject"] == "oid-new" and row["display_name"] == "花子"
    assert row["created_by"] == "sso" and row["credential"] and row["is_active"] is True
    again = g.verify_ios_identity(**sso(oid="oid-new", name="hanako@example.com"))
    assert again.identity_id == ident.identity_id and len(rows(db)) == 3        # 2回目は、新しいユーザーを作らない


def test_existing_identity_is_linked_by_email_case_insensitive(db, sso_on):
    ident = g.verify_ios_identity(**sso(oid="oid-taro", name="taro.yamada@example.com"))
    assert ident.identity_id == "auto_deac2a2d86f9" and ident.assigned_role == "analyst"      # それまでのデータが、そのまま引き継がれる
    row = [r for r in rows(db) if r["identity_id"] == "auto_deac2a2d86f9"][0]
    assert row["sso_subject"] == "oid-taro" and row["display_name"] == "大濱"                 # 既存の表示名は、上書きしない
    assert len(rows(db)) == 2


def test_linked_role_is_kept(db, sso_on):
    [r for r in rows(db) if r["identity_id"] == "pilot_approver"][0]["email"] = "boss@example.com"
    ident = g.verify_ios_identity(**sso(oid="oid-boss", name="BOSS@example.com"))
    assert ident.identity_id == "pilot_approver" and ident.assigned_role == "approver"        # 運用アカウントの権限は、紐づけ後も維持


def test_email_linked_to_another_sso_account_is_denied(db, sso_on):
    g.verify_ios_identity(**sso(oid="oid-taro", name="taro.yamada@example.com"))
    g._SSO_CACHE.clear()
    with pytest.raises(HTTPException) as e:
        g.verify_ios_identity(**sso(oid="oid-other", name="taro.yamada@example.com"))        # 同じメールの、別のSSOアカウント
    assert e.value.status_code == 403
    assert len(rows(db)) == 2


def test_inactive_identity_is_denied(db, sso_on):
    [r for r in rows(db) if r["identity_id"] == "auto_deac2a2d86f9"][0]["is_active"] = False
    with pytest.raises(HTTPException) as e:
        g.verify_ios_identity(**sso(oid="oid-taro", name="taro.yamada@example.com"))
    assert e.value.status_code in (401, 403)


def test_role_change_and_deactivation_are_reflected_after_the_cache_expires(db, sso_on, monkeypatch):
    first = g.verify_ios_identity(**sso(oid="oid-new", name="hanako@example.com"))
    row = [r for r in rows(db) if r["identity_id"] == first.identity_id][0]
    row["role"] = "approver"
    assert g.verify_ios_identity(**sso(oid="oid-new", name="hanako@example.com")).assigned_role == "analyst"      # キャッシュの間は、そのまま
    monkeypatch.setattr(g, "SSO_CACHE_TTL_SECONDS", 0)
    assert g.verify_ios_identity(**sso(oid="oid-new", name="hanako@example.com")).assigned_role == "approver"     # 期限後に、反映される
    row["is_active"] = False
    with pytest.raises(HTTPException):
        g.verify_ios_identity(**sso(oid="oid-new", name="hanako@example.com"))


def test_concurrent_first_login_reuses_the_row_created_by_the_other_request(db, sso_on, monkeypatch):
    """同時に初回ログインして、先に登録された場合（一意制約で、insertが失敗する）は、そのユーザーを使う。"""
    real_insert = db.insert

    def racing_insert(table, rows_, **kw):
        real_insert(table, [{**rows_[0], "identity_id": "sso_winner"}])           # 先に、別のリクエストが登録した
        raise RuntimeError("duplicate key value violates unique constraint")
    monkeypatch.setattr(db, "insert", racing_insert)
    ident = g.verify_ios_identity(**sso(oid="oid-race", name="race@example.com"))
    assert ident.identity_id == "sso_winner" and len([r for r in rows(db) if r.get("sso_subject") == "oid-race"]) == 1


def test_missing_sso_columns_fail_closed_with_a_hint(sso_on, monkeypatch):
    class OldSchema(FakeSupabaseClient):
        def select(self, table, params):
            if "sso_subject" in str(params):
                raise RuntimeError("column geo_ios_identities.sso_subject does not exist")
            return super().select(table, params)
    monkeypatch.setattr(g, "_client", OldSchema({"geo_ios_identities": []}))
    with pytest.raises(HTTPException) as e:
        g.verify_ios_identity(**sso())
    assert e.value.status_code == 401 and "2026-10-23_geo_ios_identities_sso.sql" in e.value.detail


def test_principal_claims_are_parsed_and_broken_input_is_ignored():
    c = g._principal_claims(principal({"name": "花子", "preferred_username": "h@example.com"}))
    assert c["name"] == "花子" and c["preferred_username"] == "h@example.com" and c["__auth_typ"] == "aad"
    assert g._principal_claims("not-base64!!") == {} and g._principal_claims(None) == {}
    assert g._s(object()) is None and g._s("  ") is None and g._s(" x ") == "x"       # Header(default=None)の直接呼び出しを無視


# ---- API ----
def make_client(db, monkeypatch):
    monkeypatch.setattr(api, "_client", db)
    app = FastAPI()
    app.include_router(api.geo_ios_identity_router)
    return TestClient(app)


def test_me_returns_the_sso_user_without_secrets(db, sso_on, monkeypatch):
    c = make_client(db, monkeypatch)
    r = c.get("/api/geo/ios/identities/me", headers={"X-MS-CLIENT-PRINCIPAL-ID": "oid-taro", "X-MS-CLIENT-PRINCIPAL-NAME": "taro.yamada@example.com"})
    assert r.status_code == 200
    body = r.json()
    assert body["identity_id"] == "auto_deac2a2d86f9" and body["mode"] == "sso" and body["display_name"] == "大濱"
    assert body["email"] == "Taro.Yamada@Example.com" and "credential" not in body and "credential" not in json.dumps(body)
    assert c.get("/api/geo/ios/identities/me").status_code == 401


def test_me_in_credential_mode(db, monkeypatch):
    c = make_client(db, monkeypatch)
    r = c.get("/api/geo/ios/identities/me", headers={"X-IOS-Credential": "approver-cred"})
    assert r.status_code == 200 and r.json()["mode"] == "credential" and r.json()["role"] == "approver"


def test_auto_provision_is_disabled_in_sso_mode(db, sso_on, monkeypatch):
    c = make_client(db, monkeypatch)
    assert c.post("/api/geo/ios/identities/auto-provision").status_code == 404
    assert len(rows(db)) == 2                                                    # 資格情報を発行しない


def test_auto_provision_still_works_without_sso(db, monkeypatch):
    c = make_client(db, monkeypatch)
    r = c.post("/api/geo/ios/identities/auto-provision")
    assert r.status_code == 200 and r.json()["role"] == "analyst" and r.json()["identity_id"].startswith("auto_")
