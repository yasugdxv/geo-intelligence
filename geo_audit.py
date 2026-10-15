"""地政学インテリジェンス基盤: 監査ログ（geo_audit_logs）への記録ヘルパー。

誰がどの資料を参照・操作したかを記録する（指示書17章）。MVPはモック認証のため
actorは固定文字列またはconfigのロール設定から渡される想定。Level3/4承認の
改変不可ログはPhase2以降で別途扱う。
"""


def log_audit(client, actor: str, action: str, target_table: str = None,
              target_id: str = None, detail: dict = None) -> None:
    client.insert("geo_audit_logs", [{
        "actor": actor,
        "action": action,
        "target_table": target_table,
        "target_id": target_id,
        "detail": detail,
    }], prefer="return=minimal")
