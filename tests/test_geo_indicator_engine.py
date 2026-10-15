"""Indicator閾値アラート（Phase2B: evaluate_threshold / create_alert_if_needed）の単体テスト。
既存のcompute_*_observation系（Phase1以前）は他モジュールの統合テストで間接的に検証済み・
本ファイルではPhase2B新規追加分のみを対象とする。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_indicator_engine as engine  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _observation(**overrides):
    base = {
        "observation_id": "obs-1", "indicator_id": "ind-1", "rule_version_id": "rule-1",
        "as_of": "2026-08-14T00:00:00+00:00", "raw_value": 50.0, "deviation": 10.0,
        "insufficient_data": False,
    }
    base.update(overrides)
    return base


def test_evaluate_threshold_above():
    rule = {"thresholds": [{"severity": "critical", "compare_field": "raw_value", "operator": "above", "value": 40}]}
    assert engine.evaluate_threshold(_observation(raw_value=50.0), rule) == "critical"
    assert engine.evaluate_threshold(_observation(raw_value=30.0), rule) is None


def test_evaluate_threshold_below():
    rule = {"thresholds": [{"severity": "warning", "compare_field": "deviation", "operator": "below", "value": -5}]}
    assert engine.evaluate_threshold(_observation(deviation=-10.0), rule) == "warning"
    assert engine.evaluate_threshold(_observation(deviation=0.0), rule) is None


def test_evaluate_threshold_gte_lte():
    rule_gte = {"thresholds": [{"severity": "critical", "compare_field": "raw_value", "operator": "gte", "value": 50}]}
    assert engine.evaluate_threshold(_observation(raw_value=50.0), rule_gte) == "critical"
    rule_lte = {"thresholds": [{"severity": "critical", "compare_field": "raw_value", "operator": "lte", "value": 50}]}
    assert engine.evaluate_threshold(_observation(raw_value=50.0), rule_lte) == "critical"


def test_evaluate_threshold_critical_takes_priority_over_warning():
    rule = {"thresholds": [
        {"severity": "warning", "compare_field": "raw_value", "operator": "above", "value": 10},
        {"severity": "critical", "compare_field": "raw_value", "operator": "above", "value": 40},
    ]}
    assert engine.evaluate_threshold(_observation(raw_value=50.0), rule) == "critical"


def test_evaluate_threshold_no_thresholds_defined_returns_none():
    assert engine.evaluate_threshold(_observation(), {}) is None
    assert engine.evaluate_threshold(_observation(), {"method": "event_count_window"}) is None


def test_evaluate_threshold_insufficient_data_returns_none():
    rule = {"thresholds": [{"severity": "critical", "compare_field": "raw_value", "operator": "above", "value": 0}]}
    assert engine.evaluate_threshold(_observation(insufficient_data=True), rule) is None


def test_evaluate_threshold_unknown_operator_raises():
    with pytest.raises(ValueError):
        engine._threshold_matches(1.0, "unknown_op", 0.0)


def test_create_alert_if_needed_idempotent_per_observation():
    client = FakeSupabaseClient()
    obs = _observation()
    first = engine.create_alert_if_needed(client, observation=obs, severity="critical")
    second = engine.create_alert_if_needed(client, observation=obs, severity="critical")
    assert first["alert_id"] == second["alert_id"]
    assert len(client.tables["geo_indicator_alerts"]) == 1


def test_create_alert_if_needed_no_severity_creates_nothing():
    client = FakeSupabaseClient()
    result = engine.create_alert_if_needed(client, observation=_observation(), severity=None)
    assert result is None
    assert "geo_indicator_alerts" not in client.tables or len(client.tables["geo_indicator_alerts"]) == 0
