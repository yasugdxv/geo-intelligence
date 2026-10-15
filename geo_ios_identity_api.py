"""Geopolitical Intelligence OS: Pilot-grade Identity Roster API（読み取り専用）。

Reviewer/Approver選択UI向けに、DB（public.geo_ios_identities、2026-09-24にconfig.jsonの
geo_ios.identitiesから移行、ユーザー指示「アカウント管理はconfigではなくDBでやろう」）から
identity_id/role/domainのみを返す。credential自体は絶対に含めない
（geo_ios_identity._IDENTITY_REGISTRYのkeyはcredential文字列そのものだが、
このAPIはkeyを一切レスポンスへ含めない）。

SEC: 一覧そのものはPilot内の他アクターのidentity_id/role構成を明らかにするため、
authenticated IOS identity（どのroleでもよい——選択UIはPilot参加者全員が使うため
role制限まではしない）を必須とする。config.jsonの生の値をそのままserializeしない
（identity_id/role/domainのみを明示的に組み立てる）。
"""
from fastapi import APIRouter, Depends

from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

import geo_ios_identity

_client = SupabaseClient(load_config())

geo_ios_identity_router = APIRouter(prefix="/api/geo/ios/identities")


@geo_ios_identity_router.post("/auto-provision")
def auto_provision():
    """2026-10-07追加: credentialを一度も持っていないブラウザが、何も手入力せずに
    低権限（analyst）のIdentityを自動で受け取れるようにする（ユーザー指示——手動での
    Credentialコピペという体験への不満への対応）。このendpoint自体は未認証で呼べる
    （そうでなければ「最初の1個」を取得できない）。ここで払い出されるroleは常に
    geo_ios_identity.AUTO_PROVISION_ROLE固定であり、呼び出し元が指定することはできない
    （SEC-1/SEC-2のTrust Boundaryは維持——approver/knowledge_steward等の強い権限は
    引き続き既存のnamed pilot credentialでのみ得られる）。"""
    return geo_ios_identity.auto_provision_identity(_client)


@geo_ios_identity_router.get("")
def list_identities(
    role: str | None = None,
    identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity),
):
    entries = [
        {"identity_id": entry["identity_id"], "role": entry["role"], "domain": entry.get("domain")}
        for entry in geo_ios_identity._IDENTITY_REGISTRY.values()
    ]
    if role:
        entries = [e for e in entries if e["role"] == role]
    return {"identities": entries}
