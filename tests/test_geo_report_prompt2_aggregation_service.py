import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_prompt2_aggregation_service as agg  # noqa: E402

_PRIMARY_SIGNALS = ["S1", "S7"]
_COMMON_START = date(2026, 7, 1)


def _record(*, signal_id="S1", is_duplicate=False, date_key="2026-08-10", sentiment_cluster_id="SC_1",
             week_start="2026-08-10", week_end="2026-08-16"):
    return {
        "signal_id": signal_id, "is_duplicate": is_duplicate, "date_key": date_key,
        "sentiment_cluster_id": sentiment_cluster_id, "week_start": week_start, "week_end": week_end,
    }


def _classification(*, theme_relevance="relevant", source_class="Public Voice", attitude="positive",
                      apply_to_cluster=True):
    return {"theme_relevance": theme_relevance, "source_class": source_class, "attitude": attitude,
            "apply_to_cluster": apply_to_cluster}


def test_parse_date_handles_valid_invalid_and_unknown():
    assert agg.parse_date("2026-08-10") == date(2026, 8, 10)
    assert agg.parse_date("unknown") is None
    assert agg.parse_date("") is None
    assert agg.parse_date("not-a-date") is None


def test_join_records_with_sentiment_classification_preserves_count_and_merges():
    records = [_record(sentiment_cluster_id="SC_1"), _record(sentiment_cluster_id="SC_missing")]
    classifications = {"SC_1": _classification()}
    joined = agg.join_records_with_sentiment_classification(records, classifications)
    assert len(joined) == 2
    assert joined[0]["theme_relevance"] == "relevant"
    assert joined[1]["theme_relevance"] is None  # 未結合


def test_classify_record_for_denominator_out_of_scope_cases():
    r1 = {**_record(signal_id="S2"), **_classification()}
    assert agg.classify_record_for_denominator(
        r1, primary_graph_signals=_PRIMARY_SIGNALS, common_primary_graph_start=_COMMON_START) == "out_of_scope"

    r2 = {**_record(is_duplicate=True), **_classification()}
    assert agg.classify_record_for_denominator(
        r2, primary_graph_signals=_PRIMARY_SIGNALS, common_primary_graph_start=_COMMON_START) == "out_of_scope"

    r3 = {**_record(date_key="2026-06-01"), **_classification()}
    assert agg.classify_record_for_denominator(
        r3, primary_graph_signals=_PRIMARY_SIGNALS, common_primary_graph_start=_COMMON_START) == "out_of_scope"

    r4 = dict(_record())  # theme_relevance未設定(結合できなかった)
    assert agg.classify_record_for_denominator(
        r4, primary_graph_signals=_PRIMARY_SIGNALS, common_primary_graph_start=_COMMON_START) == "out_of_scope"


def test_classify_record_for_denominator_exclusion_categories():
    def _classify(**overrides):
        r = {**_record(), **_classification(**overrides)}
        return agg.classify_record_for_denominator(
            r, primary_graph_signals=_PRIMARY_SIGNALS, common_primary_graph_start=_COMMON_START)

    assert _classify(theme_relevance="irrelevant") == "excluded_irrelevant"
    assert _classify(theme_relevance="uncertain") == "excluded_irrelevant"
    assert _classify(source_class="Media") == "excluded_media_official_corporate"
    assert _classify(source_class="Official / Government") == "excluded_media_official_corporate"
    assert _classify(source_class="Corporate / PR") == "excluded_media_official_corporate"
    assert _classify(source_class="Fact Only") == "excluded_fact_only"
    assert _classify(source_class="SEO / Affiliate") == "excluded_irrelevant"
    assert _classify(apply_to_cluster=False) == "excluded_apply_false"
    assert _classify(attitude="mixed") == "mixed_unknown"
    assert _classify(attitude="unknown") == "mixed_unknown"
    assert _classify(attitude="positive") == "positive"
    assert _classify(attitude="neutral") == "neutral"
    assert _classify(attitude="negative") == "negative"


def test_compute_biweekly_periods_matches_prompt2_spec_worked_example():
    # Plan G.2の実例（Prompt2 §4.2）: common_primary_graph_start=2026-05-01(金曜)
    periods = agg.compute_biweekly_periods(date(2026, 5, 1), date(2026, 7, 20))
    assert periods == [
        (date(2026, 5, 1), date(2026, 5, 10)),
        (date(2026, 5, 11), date(2026, 5, 24)),
        (date(2026, 5, 25), date(2026, 6, 7)),
        (date(2026, 6, 8), date(2026, 6, 21)),
        (date(2026, 6, 22), date(2026, 7, 5)),
        (date(2026, 7, 6), date(2026, 7, 20)),
    ]


def test_compute_biweekly_periods_single_short_range():
    periods = agg.compute_biweekly_periods(date(2026, 7, 1), date(2026, 7, 3))
    assert periods == [(date(2026, 7, 1), date(2026, 7, 3))]


def test_find_period():
    periods = [(date(2026, 5, 1), date(2026, 5, 10)), (date(2026, 5, 11), date(2026, 5, 24))]
    assert agg.find_period(date(2026, 5, 15), periods) == (date(2026, 5, 11), date(2026, 5, 24))
    assert agg.find_period(date(2026, 6, 1), periods) is None


def test_build_biweekly_trend_rows_computes_shares_and_excludes_out_of_scope():
    records = [
        {**_record(date_key="2026-08-10"), **_classification(attitude="positive")},
        {**_record(date_key="2026-08-11"), **_classification(attitude="positive")},
        {**_record(date_key="2026-08-12"), **_classification(attitude="negative")},
        {**_record(date_key="2026-08-13"), **_classification(attitude="mixed")},
        {**_record(signal_id="S2", date_key="2026-08-10"), **_classification(attitude="positive")},  # 対象外signal
    ]
    rows = agg.build_biweekly_trend_rows(
        records, primary_graph_signals=_PRIMARY_SIGNALS, common_primary_graph_start=_COMMON_START,
        report_topic_label_by_signal={"S1": "Arizona Governor"})
    assert len(rows) == 1
    row = rows[0]
    assert row["signal_id"] == "S1"
    assert row["report_topic_label"] == "Arizona Governor"
    assert row["positive_n"] == 2
    assert row["negative_n"] == 1
    assert row["threeway_n"] == 3
    assert row["positive_share"] == round(2 / 3, 4)
    assert row["mixed_unknown_n"] == 1


def test_build_weekly_and_monthly_trend_rows():
    records = [
        {**_record(date_key="2026-08-10", week_start="2026-08-10", week_end="2026-08-16"),
         **_classification(attitude="positive")},
        {**_record(date_key="2026-08-11", week_start="2026-08-10", week_end="2026-08-16"),
         **_classification(attitude="negative")},
    ]
    weekly = agg.build_weekly_trend_rows(
        records, primary_graph_signals=_PRIMARY_SIGNALS, common_primary_graph_start=_COMMON_START,
        report_topic_label_by_signal={})
    assert len(weekly) == 1
    assert weekly[0]["threeway_n"] == 2

    monthly = agg.build_monthly_summary_rows(
        records, primary_graph_signals=_PRIMARY_SIGNALS, common_primary_graph_start=_COMMON_START,
        report_topic_label_by_signal={})
    assert len(monthly) == 1
    assert monthly[0]["period_start"] == "2026-08"
    assert monthly[0]["threeway_n"] == 2


def test_build_denominator_audit_rows_basic():
    raw_records = [_record(is_duplicate=False), _record(is_duplicate=True), _record(is_duplicate=False)]
    joined = [
        {**raw_records[0], **_classification(attitude="positive")},
        {**raw_records[1], **_classification(attitude="positive")},  # 重複なのでnondup対象外
        {**raw_records[2], **_classification(source_class="Media")},
    ]
    rows = agg.build_denominator_audit_rows(
        raw_records, joined, primary_graph_signals=_PRIMARY_SIGNALS, common_primary_graph_start=_COMMON_START)
    s1_row = next(r for r in rows if r["signal_id"] == "S1")
    all_row = next(r for r in rows if r["signal_id"] == "ALL")

    assert s1_row["raw_records"] == 3
    assert s1_row["nonduplicate_records"] == 2
    assert s1_row["cluster_label_joined_records"] == 2
    assert s1_row["public_voice_records"] == 1
    assert s1_row["threeway_sentiment_records"] == 1
    assert s1_row["excluded_media_official_corporate"] == 1
    assert s1_row["cluster_join_rate"] == 1.0
    assert s1_row["qa_status"] == "PASS"
    assert all_row["raw_records"] == 3


def test_build_denominator_audit_rows_flags_low_join_rate():
    raw_records = [_record(), _record(sentiment_cluster_id="SC_unclassified")]
    joined = [
        {**raw_records[0], **_classification()},
        dict(raw_records[1]),  # theme_relevance未設定=未結合
    ]
    rows = agg.build_denominator_audit_rows(
        raw_records, joined, primary_graph_signals=_PRIMARY_SIGNALS, common_primary_graph_start=_COMMON_START)
    s1_row = next(r for r in rows if r["signal_id"] == "S1")
    assert s1_row["cluster_join_rate"] == 0.5
    assert s1_row["qa_status"] == "REVIEW REQUIRED"
