"""Geopolitical Intelligence OS: Identity Roster APIのテスト。credentialを絶対に
返さないこと、role絞り込みが正しく機能することを確認する。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_identity  # noqa: E402
import geo_ios_identity_api as identity_api  # noqa: E402


def _identity(role: str, identity_id: str = "caller") -> geo_ios_identity.IosIdentity:
    return geo_ios_identity.IosIdentity(identity_id=identity_id, assigned_role=role, authenticated=True)


def test_list_identities_requires_verify_ios_identity_dependency():
    """SEC: このエンドポイントもauthenticated IOS identityを必須とする
    （missing/unknown credentialの401自体はverify_ios_identity自身の既存テストで
    保証済み——test_geo_ios_phase_i_a1_trust_boundary.py参照）。"""
    import inspect
    sig = inspect.signature(identity_api.list_identities)
    default = sig.parameters["identity"].default
    assert default.dependency is geo_ios_identity.verify_ios_identity


def test_list_identities_response_allowlist_excludes_config_secrets(monkeypatch):
    """response allowlist: identity_id/role/domain以外は返さない。特にcredential/
    api_key/token/config path/secretやconfig.jsonの生の値は含めない。"""
    monkeypatch.setattr(geo_ios_identity, "_IDENTITY_REGISTRY", {
        "super-secret-credential-value": {
            "identity_id": "eguchi", "role": "expert_reviewer", "domain": "geo",
            "api_key": "should-never-leak", "config_path": "C:/secret/config.json",
        },
    })
    result = identity_api.list_identities(identity=_identity("administrator"))
    assert result == {"identities": [{"identity_id": "eguchi", "role": "expert_reviewer", "domain": "geo"}]}
    serialized = str(result)
    for forbidden in ("super-secret-credential-value", "api_key", "should-never-leak", "config_path", "C:/secret"):
        assert forbidden not in serialized


def test_list_identities_never_returns_credential_key(monkeypatch):
    monkeypatch.setattr(geo_ios_identity, "_IDENTITY_REGISTRY", {
        "super-secret-credential-value": {"identity_id": "eguchi", "role": "expert_reviewer", "domain": None},
    })
    result = identity_api.list_identities(identity=_identity("analyst"))
    assert result == {"identities": [{"identity_id": "eguchi", "role": "expert_reviewer", "domain": None}]}
    assert "super-secret-credential-value" not in str(result)


def test_list_identities_filters_by_role(monkeypatch):
    monkeypatch.setattr(geo_ios_identity, "_IDENTITY_REGISTRY", {
        "cred-1": {"identity_id": "approver-a", "role": "approver", "domain": None},
        "cred-2": {"identity_id": "reviewer-a", "role": "expert_reviewer", "domain": None},
        "cred-3": {"identity_id": "reviewer-b", "role": "expert_reviewer", "domain": None},
    })
    result = identity_api.list_identities(role="expert_reviewer", identity=_identity("analyst"))
    ids = {e["identity_id"] for e in result["identities"]}
    assert ids == {"reviewer-a", "reviewer-b"}


def test_list_identities_no_filter_returns_all(monkeypatch):
    monkeypatch.setattr(geo_ios_identity, "_IDENTITY_REGISTRY", {
        "cred-1": {"identity_id": "approver-a", "role": "approver", "domain": None},
        "cred-2": {"identity_id": "reviewer-a", "role": "expert_reviewer", "domain": None},
    })
    result = identity_api.list_identities(identity=_identity("analyst"))
    assert len(result["identities"]) == 2
