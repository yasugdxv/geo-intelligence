"""地政学ナレッジ＆インテリジェンスDB Phase2B: Indicator Freshnessの単体テスト。
classify_knowledge_freshness/classify_source_freshnessとは独立した第3の判定関数であることを確認する。
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_freshness_service as freshness  # noqa: E402


def test_classify_indicator_freshness_is_independent_function():
    assert freshness.classify_indicator_freshness is not freshness.classify_source_freshness
    assert freshness.classify_indicator_freshness is not freshness.classify_knowledge_freshness


def test_classify_indicator_freshness_unknown_without_cadence():
    result = freshness.classify_indicator_freshness({}, {"as_of": "2026-08-01T00:00:00+00:00"})
    assert result["status"] == "unknown"
    assert result["days_since_last_observation"] is None


def test_classify_indicator_freshness_unknown_without_observation():
    result = freshness.classify_indicator_freshness({"expected_update_cadence": "monthly"}, None)
    assert result["status"] == "unknown"


def test_classify_indicator_freshness_current_within_cadence():
    now = datetime(2026, 8, 14, tzinfo=timezone.utc)
    result = freshness.classify_indicator_freshness(
        {"expected_update_cadence": "monthly"}, {"as_of": "2026-08-01T00:00:00+00:00"}, now=now)
    assert result["status"] == "current"
    assert result["days_since_last_observation"] == 13


def test_classify_indicator_freshness_review_due_soon():
    now = datetime(2026, 8, 14, tzinfo=timezone.utc)
    # dailyの80%(=0.8日)を超えて経過 → review_due_soon
    result = freshness.classify_indicator_freshness(
        {"expected_update_cadence": "daily"}, {"as_of": "2026-08-13T00:00:00+00:00"}, now=now)
    assert result["status"] == "review_due_soon"


def test_classify_indicator_freshness_overdue_beyond_cadence():
    now = datetime(2026, 8, 14, tzinfo=timezone.utc)
    result = freshness.classify_indicator_freshness(
        {"expected_update_cadence": "monthly"}, {"as_of": "2026-01-01T00:00:00+00:00"}, now=now)
    assert result["status"] == "overdue"
