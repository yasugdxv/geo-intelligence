"""地政学インテリジェンス基盤 Phase4: Geo Intelligence Agent向け認証の抽象化レイヤー。

Business LogicからAuthを分離する。開発時はconfig.jsonのgeo_intelligence.api_keysと
X-API-Keyヘッダーを照合する単純なAPI Key認証のみを提供する。本番導入時にMicrosoft
Entra ID / Managed Identity / Client Credentials等へ置き換える場合、verify_api_key関数の
中身だけを差し替えればよく、呼び出し側（geo_intelligence_api.py）・geo_intelligence_service.py
のBusiness Logicには一切手を入れない設計にする。

config.jsonにgeo_intelligence.api_keysが未設定の場合は開発環境とみなし認証をスキップする
（既存のローカル動作・テストを壊さないため）。本番運用時は必ずapi_keysを設定すること。

Phase F: geo_intelligence.api_keysの各エントリは、既存互換の単純な文字列（caller_app/
caller_domain紐付け無し、これまでどおりrequester_appの自己申告を信頼する後方互換モード）
に加えて、{"key": ..., "caller_app": ..., "caller_domain": ...}形式のオブジェクトも受け付ける。
後者を設定したAPI Keyについては、以後Access Policyの判定はこのCallerIdentityを正とし、
リクエストBody側のrequester_app/source_departmentは無視する（Business Logic側で上書きする、
geo_intelligence_service.process_request参照）——requester_appの自己申告だけでは
Cross-Agent Access Policyを迂回できてしまうため（Phase E完了時点の既知の残課題）。
"""
from fastapi import Header, HTTPException

from config_utils import load_config

# geo_department_intelligence_service等、地政学アプリ自身の内部利用を表すdomain値。
# この値がcaller_domainとして解決された場合のみ、agent_sharing_policyの対象外
# （＝geopolitics_only等の制限を受けない）として扱う。
GEOPOLITICS_INTERNAL_DOMAIN = "geopolitics"


class CallerIdentity:
    """verify_api_keyが返す、認証済み呼び出し元の識別情報。

    authenticated=Falseは開発環境（api_keys未設定）でのdev-no-authモードを表す——この場合
    caller_app/caller_domainは常にNoneであり、Business Logic側は既存のrequester_app自己申告
    ベースの挙動へフォールバックする（Phase E以前と同じ、後方互換）。"""

    __slots__ = ("key", "caller_app", "caller_domain", "authenticated")

    def __init__(self, *, key: str = None, caller_app: str = None, caller_domain: str = None,
                 authenticated: bool = False):
        self.key = key
        self.caller_app = caller_app
        self.caller_domain = caller_domain
        self.authenticated = authenticated

    def __repr__(self) -> str:  # pragma: no cover - デバッグ表示用、Credential自体は出さない
        return (f"CallerIdentity(caller_app={self.caller_app!r}, caller_domain={self.caller_domain!r}, "
                f"authenticated={self.authenticated})")


def _load_caller_registry(config: dict) -> dict:
    """config.jsonのgeo_intelligence.api_keysを、key文字列 -> {"caller_app", "caller_domain"}の
    辞書へ正規化する。既存の「文字列のみのlist」（caller_app/caller_domain無し）もそのまま
    サポートする——後方互換のため、値の型で自動判別する。"""
    entries = (config.get("geo_intelligence") or {}).get("api_keys") or []
    registry = {}
    for entry in entries:
        if isinstance(entry, str):
            registry[entry] = {"caller_app": None, "caller_domain": None}
        elif isinstance(entry, dict) and entry.get("key"):
            registry[entry["key"]] = {
                "caller_app": entry.get("caller_app"), "caller_domain": entry.get("caller_domain"),
            }
    return registry


_config = load_config()
_CALLER_REGISTRY = _load_caller_registry(_config)


def verify_api_key(x_api_key: str | None = Header(default=None)) -> CallerIdentity:
    """FastAPIのDependsとして使う。戻り値は認証済み呼び出し元のCallerIdentity
    （本番導入時にEntra ID/Client Credentialsへ置き換える場合、この関数の中身だけを
    差し替え、CallerIdentityを返す形は維持する想定）。"""
    if not _CALLER_REGISTRY:
        return CallerIdentity(authenticated=False)
    if not x_api_key or x_api_key not in _CALLER_REGISTRY:
        raise HTTPException(status_code=401, detail="無効または未指定のX-API-Keyです")
    entry = _CALLER_REGISTRY[x_api_key]
    return CallerIdentity(
        key=x_api_key, caller_app=entry["caller_app"], caller_domain=entry["caller_domain"], authenticated=True)
