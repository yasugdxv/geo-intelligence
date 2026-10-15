"""地政学ナレッジ＆インテリジェンスDB Phase2A: Freshness / Staleness分類。

古いKnowledge/Sourceを自動削除しない。状態は保存せず、参照時に都度算出する
（保存すると「鮮度フラグ自体が古くなる」問題が生じるため）。
"""
import re
from datetime import date, datetime, timezone

from geo_connector_base import UPDATE_CADENCE_DAYS

KNOWLEDGE_FRESHNESS_STATUSES = ("current", "review_due", "stale", "superseded", "unknown")
SOURCE_FRESHNESS_STATUSES = ("current", "review_due_soon", "overdue", "failing", "unknown")
INDICATOR_FRESHNESS_STATUSES = ("current", "review_due_soon", "overdue", "unknown")


_FRACTION_RE = re.compile(r"\.(\d+)")


def _fromiso(value: str) -> datetime:
    """Python 3.10のfromisoformatは小数秒が3/6桁以外（例: Postgresが末尾の0を省いた'.64088'）だと失敗するため、
    6桁へ揃えてから解釈する（Direct Collectionが書く最終成功時刻で運用サマリーが壊れた不具合への対処）。"""
    text = str(value).replace("Z", "+00:00")
    text = _FRACTION_RE.sub(lambda m: "." + m.group(1)[:6].ljust(6, "0"), text, count=1)
    return datetime.fromisoformat(text)


def _parse_date(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return _fromiso(value).date()
    except ValueError:
        return None


def classify_knowledge_freshness(item: dict, *, today: date = None) -> str:
    """current/review_due/stale/superseded/unknownのいずれかを返す。
    itemはgeo_knowledge_itemsの1行相当（status/as_of_date/next_review_at/stale_after_daysを参照）。"""
    today = today or datetime.now(timezone.utc).date()

    if item.get("status") == "superseded":
        return "superseded"

    as_of_date = _parse_date(item.get("as_of_date"))
    if as_of_date is None:
        return "unknown"

    next_review_at = _parse_date(item.get("next_review_at"))
    if next_review_at and today >= next_review_at:
        return "review_due"

    stale_after_days = item.get("stale_after_days")
    if stale_after_days is not None and (today - as_of_date).days > stale_after_days:
        return "stale"

    return "current"


def classify_source_freshness(source: dict, *, now: datetime = None) -> dict:
    """{"status": ..., "days_since_last_success": int|None, "failure_count": int}を返す。
    sourceはgeo_source_registryの1行相当（update_cadence/last_successful_ingestion_at/
    last_failed_ingestion_at/failure_countを参照）。"""
    now = now or datetime.now(timezone.utc)
    failure_count = source.get("failure_count") or 0

    last_success_str = source.get("last_successful_ingestion_at")
    if not last_success_str:
        return {"status": "unknown", "days_since_last_success": None, "failure_count": failure_count}

    last_success = _fromiso(last_success_str)
    if last_success.tzinfo is None:
        last_success = last_success.replace(tzinfo=timezone.utc)
    days_since = (now - last_success).days

    if failure_count >= 3:
        return {"status": "failing", "days_since_last_success": days_since, "failure_count": failure_count}

    expected_days = UPDATE_CADENCE_DAYS.get(source.get("update_cadence"), 30)
    if days_since > expected_days:
        return {"status": "overdue", "days_since_last_success": days_since, "failure_count": failure_count}
    if days_since > expected_days * 0.8:
        return {"status": "review_due_soon", "days_since_last_success": days_since, "failure_count": failure_count}
    return {"status": "current", "days_since_last_success": days_since, "failure_count": failure_count}


def classify_indicator_freshness(indicator: dict, latest_observation: dict | None, *, now: datetime = None) -> dict:
    """indicator.expected_update_cadenceを基準に、最新Observationのas_ofからの経過日数で判定する。
    classify_knowledge_freshness/classify_source_freshnessとは独立した、Indicator固有の判定ロジック
    （Phase2A/Phase2Bで別々に追加された概念であり、意図的に使い回さない）。
    expected_update_cadence未設定、またはlatest_observationが無い場合はunknownを返す。"""
    now = now or datetime.now(timezone.utc)
    cadence = indicator.get("expected_update_cadence")
    if not cadence or not latest_observation or not latest_observation.get("as_of"):
        return {"status": "unknown", "days_since_last_observation": None}

    as_of = _fromiso(latest_observation["as_of"])
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    days_since = (now - as_of).days

    expected_days = UPDATE_CADENCE_DAYS.get(cadence, 30)
    if days_since > expected_days:
        return {"status": "overdue", "days_since_last_observation": days_since}
    if days_since > expected_days * 0.8:
        return {"status": "review_due_soon", "days_since_last_observation": days_since}
    return {"status": "current", "days_since_last_observation": days_since}
