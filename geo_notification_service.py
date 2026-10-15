"""Geopolitical Intelligence OS §9: Expert Notificationのインターフェース。

メール送信基盤（SMTP/Microsoft 365/Teams）は本リポジトリに未実装のため（Phase 0監査で確認済み、
config.jsonのemailブロックはSustainability側テンプレートの流用でどのコードからも呼ばれていない）、
まずは「呼び出し先が確定するまでWorkflow実装全体を止めない」ためのログのみのスタブとして提供する。
実送信経路（SMTP等）が決まり次第、_deliver()の中身だけを差し替える想定。呼び出し元
（geo_ios_review_service等）はこのモジュールのpublic関数のシグネチャにのみ依存する。
"""
import logging
from datetime import datetime, timezone

import geo_audit

logger = logging.getLogger("geo_notification")


def _deliver(*, channel: str, to: str, subject: str, body: str) -> dict:
    """実送信の差し替えポイント。現状はログ出力のみ（メール環境未確定のため）。"""
    logger.info("[geo_notification stub] channel=%s to=%s subject=%s", channel, to, subject)
    return {
        "delivered": False,
        "channel": channel,
        "reason": "notification_backend_not_configured",
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }


def send_review_request(client, *, reviewer: str, report_project_id: str, review_cycle_id: str,
                         round_number: int, message: str = None, due_date: str = None,
                         actor_id: str = "geo_ios_review") -> dict:
    subject = f"[Geo Intelligence] Expert Review依頼（Round {round_number}）"
    body = (f"report_project_id={report_project_id}\nreview_cycle_id={review_cycle_id}\n"
            f"due_date={due_date}\nmessage={message or ''}")
    result = _deliver(channel="email", to=reviewer, subject=subject, body=body)
    geo_audit.log_audit(client, actor=actor_id, action="notification_review_request",
                         target_table="geo_ios_review_cycles", target_id=review_cycle_id,
                         detail={"reviewer": reviewer, "delivered": result["delivered"]})
    return result


def send_review_reminder(client, *, reviewer: str, review_cycle_id: str,
                          actor_id: str = "geo_ios_review") -> dict:
    result = _deliver(channel="email", to=reviewer, subject="[Geo Intelligence] Expert Reviewリマインド",
                       body=f"review_cycle_id={review_cycle_id}")
    geo_audit.log_audit(client, actor=actor_id, action="notification_review_reminder",
                         target_table="geo_ios_review_cycles", target_id=review_cycle_id,
                         detail={"reviewer": reviewer, "delivered": result["delivered"]})
    return result


def send_review_completed(client, *, notify: str, review_cycle_id: str, result_value: str,
                           actor_id: str = "geo_ios_review") -> dict:
    result = _deliver(channel="email", to=notify,
                       subject=f"[Geo Intelligence] Expert Review完了（{result_value}）",
                       body=f"review_cycle_id={review_cycle_id}\nresult={result_value}")
    geo_audit.log_audit(client, actor=actor_id, action="notification_review_completed",
                         target_table="geo_ios_review_cycles", target_id=review_cycle_id,
                         detail={"notify": notify, "delivered": result["delivered"]})
    return result
