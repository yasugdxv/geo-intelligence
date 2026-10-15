"""2026-10-07: Credential手動コピペ無しで低権限Identityを自動発行する機能のテスト。

SEC-1/SEC-2のTrust Boundary（authenticated identity→server-side assigned role→
authorization、payload側のrole自己申告は使わない）を壊していないことを確認する:
- 発行されるroleは常にAUTO_PROVISION_ROLE（analyst）固定で、呼び出し元は一切指定できない
- 発行直後、同一プロセス内でそのcredentialがverify_ios_identityを通ること
- /auto-provisionエンドポイント自体はverify_ios_identityを要求しない（そうでなければ
  「最初の1個」を得られない）が、それ以外の既存エンドポイントの認証要求は変えていない
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_identity  # noqa: E402
import geo_ios_identity_api as identity_api  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def test_auto_provision_identity_always_assigns_fixed_low_privilege_role(monkeypatch):
    monkeypatch.setattr(geo_ios_identity, "_IDENTITY_REGISTRY", {})
    client = FakeSupabaseClient(tables={"geo_ios_identities": []})
    result = geo_ios_identity.auto_provision_identity(client)
    assert result["role"] == geo_ios_identity.AUTO_PROVISION_ROLE == "analyst"
    assert result["identity_id"].startswith("auto_")
    assert len(result["credential"]) > 20


def test_auto_provisioned_credential_is_immediately_usable_in_same_process(monkeypatch):
    monkeypatch.setattr(geo_ios_identity, "_IDENTITY_REGISTRY", {})
    client = FakeSupabaseClient(tables={"geo_ios_identities": []})
    result = geo_ios_identity.auto_provision_identity(client)
    # _load_identity_registryは起動時1回しか走らないため、発行直後に使えるには
    # _IDENTITY_REGISTRYへの即時反映が必須（これが無いと同一プロセス内でも401になる）
    identity = geo_ios_identity.verify_ios_identity(result["credential"])
    assert identity.authenticated is True
    assert identity.identity_id == result["identity_id"]
    assert identity.assigned_role == "analyst"


def test_auto_provision_writes_a_row_with_is_active_true(monkeypatch):
    monkeypatch.setattr(geo_ios_identity, "_IDENTITY_REGISTRY", {})
    client = FakeSupabaseClient(tables={"geo_ios_identities": []})
    geo_ios_identity.auto_provision_identity(client)
    rows = client.tables["geo_ios_identities"]
    assert len(rows) == 1
    assert rows[0]["role"] == "analyst" and rows[0]["is_active"] is True
    assert rows[0]["created_by"] == "auto_provision"


def test_auto_provisioned_identity_cannot_pass_approval_role_checks(monkeypatch):
    """承認(approver)・公開(knowledge_steward)等の強い権限は自動発行の対象外であることを
    固定する——AUTO_PROVISION_ROLEがそれらの値になっていないことを直接確認する。"""
    assert geo_ios_identity.AUTO_PROVISION_ROLE not in ("approver", "knowledge_steward", "administrator")


def test_auto_provision_endpoint_requires_no_prior_credential():
    """このエンドポイント自体はverify_ios_identityのDependsを持たない
    （無ければ「最初の1個」を取得できないため、意図的に未認証で呼べる）。"""
    import inspect
    sig = inspect.signature(identity_api.auto_provision)
    assert "identity" not in sig.parameters


def test_auto_provision_endpoint_returns_identity_id_and_credential(monkeypatch):
    monkeypatch.setattr(geo_ios_identity, "_IDENTITY_REGISTRY", {})
    client = FakeSupabaseClient(tables={"geo_ios_identities": []})
    monkeypatch.setattr(identity_api, "_client", client)
    result = identity_api.auto_provision()
    assert set(result) == {"identity_id", "credential", "role"}
    assert result["role"] == "analyst"


def test_list_identities_still_requires_verify_ios_identity_dependency_unaffected():
    """既存の/identities（一覧）はこの変更後も引き続き認証必須のまま
    （新規エンドポイント追加が既存の認証要求を緩めていないことの回帰確認）。"""
    import inspect
    sig = inspect.signature(identity_api.list_identities)
    default = sig.parameters["identity"].default
    assert default.dependency is geo_ios_identity.verify_ios_identity
