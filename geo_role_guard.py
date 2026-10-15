"""地政学ナレッジ＆インテリジェンスDB: 開発専用の軽量Role制御。

Development-only role control. This role value is not authenticated and must not be
used in production. リクエストのactor_role文字列を許可リストと照合するのみで、
パスワード・セッション等の実認証は行わない。本格的なEntra ID連携等はPhase 2以降で
別途対応する（docs/geopolitical-operations-playbook.md 3章）。

Entity Merge確定・Knowledge Active化等、LLMが提案するだけで確定できない操作は、
すべてこのrequire_roleを通してから実行する。
"""

ROLES = ("viewer", "analyst", "expert_reviewer", "knowledge_steward", "approver", "administrator")


class RoleDeniedError(PermissionError):
    pass


def require_role(actor_role: str, allowed_roles: tuple) -> None:
    """actor_roleがallowed_rolesのいずれか（またはadministrator）でなければ拒否する。"""
    if actor_role not in ROLES:
        raise RoleDeniedError(f"未知のroleです: {actor_role}")
    if actor_role == "administrator":
        return
    if actor_role not in allowed_roles:
        raise RoleDeniedError(f"この操作には{allowed_roles}のいずれかのroleが必要です（現在のrole: {actor_role}）")
