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
import secrets
import uuid

from fastapi import Header, HTTPException

from config_utils import load_config
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


def verify_ios_identity(x_ios_credential: str | None = Header(default=None)) -> IosIdentity:
    """FastAPIのDependsとして使う。credentialが無い、または未登録の場合は401で拒否する
    （missing identity / unknown identity はいずれもDENY、§Security Test必須項目）。"""
    if not x_ios_credential or x_ios_credential not in _IDENTITY_REGISTRY:
        raise HTTPException(status_code=401, detail="無効または未指定のX-IOS-Credentialです")
    entry = _IDENTITY_REGISTRY[x_ios_credential]
    return IosIdentity(
        identity_id=entry["identity_id"], assigned_role=entry["role"],
        domain=entry.get("domain"), department=entry.get("department"), authenticated=True)


def get_optional_ios_identity(x_ios_credential: str | None = Header(default=None)) -> IosIdentity:
    """Phase 2E: Report/Knowledge一覧・検索・詳細取得のような「未ログインでも一応閲覧できる
    べきだが、権限に応じて見える範囲を絞りたい」読み取り経路向け。credential未指定なら
    匿名（authenticated=False）のIosIdentityを返す（401にしない）。credentialが指定
    されたが無効/未登録の場合はverify_ios_identityと同じく401で拒否する——不正な
    credentialを「とりあえず匿名として通す」と、typo/漏洩したcredentialでの試行錯誤を
    黙って許してしまうため（credential未指定と無効指定は区別して扱う）。"""
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
