"""Report Studio「Design」領域: Current Prompt2 Python Aggregation（Phase E）。

record_cluster_map（geo_report_records）とSentiment Cluster分類結果
（geo_report_sentiment_cluster_classifications）を結合し、G.1の3-way分母フィルタ・
G.2の期間バケット化（隔週次を主、週次・月次は補助）・denominator/join auditを
決定的に計算する（LLM不使用、Prompt2の「計算・JOIN・集計→Python」の役割分担）。

kind-napping-hare.md G章（Current Prompt2 Output契約）・G.1（3-way分母フィルタ）・
G.2（期間バケット化ロジック）に厳密に一致させる。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

_THREEWAY_ATTITUDES = ("positive", "neutral", "negative")
_MEDIA_OFFICIAL_CORPORATE = ("Media", "Official / Government", "Corporate / PR")


def parse_date(value) -> date:
    if not value or value == "unknown":
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# §3.1 record_cluster_map結合
# ---------------------------------------------------------------------------

def join_records_with_sentiment_classification(records: list, sentiment_classifications: dict) -> list:
    """records: geo_report_records行（dict）のlist。sentiment_classifications:
    {sentiment_cluster_id: 分類結果dict（theme_relevance/source_class/attitude/
    apply_to_clusterを含む、geo_report_sentiment_cluster_classifications行）}。

    結合できないrecord（sentiment_cluster_idが空、または該当クラスタが未分類）は
    theme_relevance等をNoneのまま残す——件数を落とさない（§3.1「Join件数を保存する」）。
    戻り値の件数は常にlen(records)と一致する。"""
    joined = []
    for r in records:
        cls = sentiment_classifications.get(r.get("sentiment_cluster_id")) if r.get("sentiment_cluster_id") else None
        merged = dict(r)
        merged["theme_relevance"] = cls.get("theme_relevance") if cls else None
        merged["source_class"] = cls.get("source_class") if cls else None
        merged["attitude"] = cls.get("attitude") if cls else None
        merged["apply_to_cluster"] = cls.get("apply_to_cluster") if cls else None
        joined.append(merged)
    return joined


# ---------------------------------------------------------------------------
# G.1 3-way分母の確定フィルタ（分類結果を単一カテゴリへ落とす、denominator_audit兼用）
# ---------------------------------------------------------------------------

def classify_record_for_denominator(record: dict, *, primary_graph_signals: list,
                                      common_primary_graph_start: date) -> str:
    """戻り値のいずれか1つ:
    - 'out_of_scope': 対象Signal外／重複／期間外／未分類（sentiment_cluster未結合）
    - 'excluded_irrelevant': theme_relevance in (irrelevant, uncertain)、または
      source_classがPublic Voice/Media/Official/Corporate/Fact Only以外(SEO/Affiliate,Mixed,Unknown等)
    - 'excluded_media_official_corporate' / 'excluded_fact_only' / 'excluded_apply_false'
    - 'mixed_unknown': Public Voice・relevant・apply_to_cluster=trueだがattitudeが3値以外
    - 'positive' / 'neutral' / 'negative': 3-way分母に算入

    判定順序はG.1のフィルタ条件（signal→duplicate→期間→theme_relevance→source_class→
    apply_to_cluster→attitude）と一致させる。"""
    if record.get("signal_id") not in primary_graph_signals:
        return "out_of_scope"
    if record.get("is_duplicate"):
        return "out_of_scope"
    record_date = parse_date(record.get("date_key"))
    if record_date is None or record_date < common_primary_graph_start:
        return "out_of_scope"
    theme_relevance = record.get("theme_relevance")
    if theme_relevance is None:
        return "out_of_scope"
    if theme_relevance in ("irrelevant", "uncertain"):
        return "excluded_irrelevant"

    source_class = record.get("source_class")
    if source_class in _MEDIA_OFFICIAL_CORPORATE:
        return "excluded_media_official_corporate"
    if source_class == "Fact Only":
        return "excluded_fact_only"
    if source_class != "Public Voice":
        return "excluded_irrelevant"  # SEO/Affiliate, Mixed, Unknown等

    if not record.get("apply_to_cluster"):
        return "excluded_apply_false"

    attitude = record.get("attitude")
    if attitude in _THREEWAY_ATTITUDES:
        return attitude
    return "mixed_unknown"


# ---------------------------------------------------------------------------
# G.2 期間バケット化（隔週次が主、週次・月次は補助）
# ---------------------------------------------------------------------------

def compute_biweekly_periods(common_start: date, last_date: date) -> list:
    """common_start含む週（月曜起点）と次の週を1つ目のバケットとして結合し（先頭端数期間の
    保持）、以降は厳密に2週間ずつ、最後は端数期間のまま残す（G.2、Prompt2 §4.2の一般化）。"""
    if last_date < common_start:
        return []
    week0_start = common_start - timedelta(days=common_start.weekday())
    week1_start = week0_start + timedelta(days=7)
    first_end = week1_start + timedelta(days=6)
    if first_end >= last_date:
        return [(common_start, last_date)]

    periods = [(common_start, first_end)]
    cursor = first_end + timedelta(days=1)
    while cursor <= last_date:
        tentative_end = cursor + timedelta(days=13)
        remaining_after = (last_date - tentative_end).days
        if tentative_end >= last_date or remaining_after < 14:
            # 残り日数がフル14日未満なら、次に孤立した端数バケットを作らずこの期間へ吸収する
            periods.append((cursor, last_date))
            break
        periods.append((cursor, tentative_end))
        cursor = tentative_end + timedelta(days=1)
    return periods


def find_period(record_date: date, periods: list):
    for start, end in periods:
        if start <= record_date <= end:
            return start, end
    return None


def _period_label(start: date, end: date) -> str:
    return f"{start.isoformat()}~{end.isoformat()}"


def _month_key(record_date: date) -> str:
    return record_date.strftime("%Y-%m")


# ---------------------------------------------------------------------------
# 集計本体
# ---------------------------------------------------------------------------

def _new_bucket() -> dict:
    return {"positive_n": 0, "neutral_n": 0, "negative_n": 0, "mixed_unknown_n": 0,
            "excluded_non_public_voice_n": 0}


def _accumulate(bucket: dict, category: str) -> None:
    if category in _THREEWAY_ATTITUDES:
        bucket[f"{category}_n"] += 1
    elif category == "mixed_unknown":
        bucket["mixed_unknown_n"] += 1
    elif category.startswith("excluded_"):
        bucket["excluded_non_public_voice_n"] += 1


def _finalize_trend_row(period_start, period_end, signal_id, report_topic_label, bucket: dict) -> dict:
    threeway_n = bucket["positive_n"] + bucket["neutral_n"] + bucket["negative_n"]
    shares = {
        f"{k}_share": (round(bucket[f"{k}_n"] / threeway_n, 4) if threeway_n else 0.0)
        for k in _THREEWAY_ATTITUDES
    }
    return {
        "period_start": period_start.isoformat() if isinstance(period_start, date) else period_start,
        "period_end": period_end.isoformat() if isinstance(period_end, date) else period_end,
        "period_label": _period_label(period_start, period_end) if isinstance(period_start, date) else str(period_start),
        "signal_id": signal_id, "report_topic_label": report_topic_label,
        "positive_n": bucket["positive_n"], "neutral_n": bucket["neutral_n"], "negative_n": bucket["negative_n"],
        "threeway_n": threeway_n, **shares,
        "mixed_unknown_n": bucket["mixed_unknown_n"],
        "excluded_non_public_voice_n": bucket["excluded_non_public_voice_n"],
    }


def build_biweekly_trend_rows(joined_records: list, *, primary_graph_signals: list,
                                common_primary_graph_start: date, report_topic_label_by_signal: dict) -> list:
    eligible_dates = [
        parse_date(r.get("date_key")) for r in joined_records
        if r.get("signal_id") in primary_graph_signals and not r.get("is_duplicate")
        and parse_date(r.get("date_key")) and parse_date(r.get("date_key")) >= common_primary_graph_start
    ]
    if not eligible_dates:
        return []
    periods = compute_biweekly_periods(common_primary_graph_start, max(eligible_dates))

    buckets: dict = {}
    for r in joined_records:
        category = classify_record_for_denominator(
            r, primary_graph_signals=primary_graph_signals, common_primary_graph_start=common_primary_graph_start)
        if category == "out_of_scope":
            continue
        record_date = parse_date(r.get("date_key"))
        period = find_period(record_date, periods)
        if period is None:
            continue
        signal_id = r["signal_id"]
        key = (period[0], period[1], signal_id)
        buckets.setdefault(key, _new_bucket())
        _accumulate(buckets[key], category)

    rows = []
    for (period_start, period_end, signal_id), bucket in sorted(buckets.items()):
        rows.append(_finalize_trend_row(
            period_start, period_end, signal_id, report_topic_label_by_signal.get(signal_id, ""), bucket))
    return rows


def build_weekly_trend_rows(joined_records: list, *, primary_graph_signals: list,
                              common_primary_graph_start: date, report_topic_label_by_signal: dict) -> list:
    """隔週次の裏付けデータ（監査用）。record自体が既に持つweek_start/week_endでバケットする。"""
    buckets: dict = {}
    for r in joined_records:
        category = classify_record_for_denominator(
            r, primary_graph_signals=primary_graph_signals, common_primary_graph_start=common_primary_graph_start)
        if category == "out_of_scope":
            continue
        week_start, week_end = r.get("week_start"), r.get("week_end")
        if not week_start or week_start == "unknown":
            continue
        signal_id = r["signal_id"]
        key = (week_start, week_end, signal_id)
        buckets.setdefault(key, _new_bucket())
        _accumulate(buckets[key], category)

    rows = []
    for (week_start, week_end, signal_id), bucket in sorted(buckets.items()):
        rows.append(_finalize_trend_row(week_start, week_end, signal_id,
                                          report_topic_label_by_signal.get(signal_id, ""), bucket))
    return rows


def build_monthly_summary_rows(joined_records: list, *, primary_graph_signals: list,
                                 common_primary_graph_start: date, report_topic_label_by_signal: dict) -> list:
    buckets: dict = {}
    for r in joined_records:
        category = classify_record_for_denominator(
            r, primary_graph_signals=primary_graph_signals, common_primary_graph_start=common_primary_graph_start)
        if category == "out_of_scope":
            continue
        record_date = parse_date(r.get("date_key"))
        if record_date is None:
            continue
        signal_id = r["signal_id"]
        key = (_month_key(record_date), signal_id)
        buckets.setdefault(key, _new_bucket())
        _accumulate(buckets[key], category)

    rows = []
    for (month, signal_id), bucket in sorted(buckets.items()):
        rows.append(_finalize_trend_row(month, month, signal_id,
                                          report_topic_label_by_signal.get(signal_id, ""), bucket))
    return rows


# ---------------------------------------------------------------------------
# denominator_audit.csv（17列、Signal別＋ALL行）
# ---------------------------------------------------------------------------

def build_denominator_audit_rows(records: list, joined_records: list, *, primary_graph_signals: list,
                                   common_primary_graph_start: date) -> list:
    """records: 結合前のgeo_report_records行（raw_records/nonduplicate_recordsの母数用）。
    joined_records: join_records_with_sentiment_classificationの結果。"""
    per_signal: dict = {}

    def _bucket(signal_id: str) -> dict:
        return per_signal.setdefault(signal_id, {
            "raw_records": 0, "nonduplicate_records": 0, "cluster_label_joined_records": 0,
            "period_in_scope_records": 0, "theme_relevant_records": 0, "public_voice_records": 0,
            "threeway_sentiment_records": 0, "mixed_unknown_records": 0,
            "excluded_media_official_corporate": 0, "excluded_fact_only": 0, "excluded_irrelevant": 0,
            "excluded_apply_false": 0,
        })

    for r in records:
        signal_id = r.get("signal_id")
        b = _bucket(signal_id)
        b["raw_records"] += 1
        if not r.get("is_duplicate"):
            b["nonduplicate_records"] += 1

    for r in joined_records:
        signal_id = r.get("signal_id")
        b = _bucket(signal_id)
        if r.get("is_duplicate"):
            continue
        if r.get("theme_relevance") is not None:
            b["cluster_label_joined_records"] += 1
        record_date = parse_date(r.get("date_key"))
        if record_date is None or record_date < common_primary_graph_start:
            continue
        b["period_in_scope_records"] += 1

        category = classify_record_for_denominator(
            r, primary_graph_signals=primary_graph_signals, common_primary_graph_start=common_primary_graph_start)
        if category == "out_of_scope":
            continue
        if category != "excluded_irrelevant":
            b["theme_relevant_records"] += 1
        if category == "excluded_media_official_corporate":
            b["excluded_media_official_corporate"] += 1
        elif category == "excluded_fact_only":
            b["excluded_fact_only"] += 1
        elif category == "excluded_irrelevant":
            b["excluded_irrelevant"] += 1
        elif category == "excluded_apply_false":
            b["excluded_apply_false"] += 1
        elif category == "mixed_unknown":
            b["public_voice_records"] += 1
            b["mixed_unknown_records"] += 1
        elif category in _THREEWAY_ATTITUDES:
            b["public_voice_records"] += 1
            b["threeway_sentiment_records"] += 1

    rows = []
    all_bucket = {k: 0 for k in [
        "raw_records", "nonduplicate_records", "cluster_label_joined_records", "period_in_scope_records",
        "theme_relevant_records", "public_voice_records", "threeway_sentiment_records", "mixed_unknown_records",
        "excluded_media_official_corporate", "excluded_fact_only", "excluded_irrelevant", "excluded_apply_false",
    ]}
    for signal_id in sorted(per_signal.keys()):
        b = per_signal[signal_id]
        rows.append(_finalize_denominator_row(signal_id, b))
        for k in all_bucket:
            all_bucket[k] += b[k]
    rows.append(_finalize_denominator_row("ALL", all_bucket))
    return rows


def _finalize_denominator_row(signal_id: str, b: dict) -> dict:
    nondup = b["nonduplicate_records"]
    cluster_join_rate = round(b["cluster_label_joined_records"] / nondup, 4) if nondup else 0.0
    theme_relevant = b["theme_relevant_records"]
    public_voice_retention_rate = round(b["public_voice_records"] / theme_relevant, 4) if theme_relevant else None

    qa_status = "PASS"
    qa_notes = []
    if cluster_join_rate < 1.0:
        qa_status = "REVIEW REQUIRED"
        qa_notes.append("cluster_join_rateが100%未満（Prompt1未実行のクラスタが残っている可能性）")
    if public_voice_retention_rate is not None and public_voice_retention_rate < 0.30:
        qa_status = "REVIEW REQUIRED"
        qa_notes.append("public_voice_retention_rateが30%未満")

    return {
        "signal_id": signal_id, "raw_records": b["raw_records"], "nonduplicate_records": nondup,
        "cluster_label_joined_records": b["cluster_label_joined_records"],
        "period_in_scope_records": b["period_in_scope_records"],
        "theme_relevant_records": theme_relevant, "public_voice_records": b["public_voice_records"],
        "threeway_sentiment_records": b["threeway_sentiment_records"],
        "mixed_unknown_records": b["mixed_unknown_records"],
        "excluded_media_official_corporate": b["excluded_media_official_corporate"],
        "excluded_fact_only": b["excluded_fact_only"], "excluded_irrelevant": b["excluded_irrelevant"],
        "excluded_apply_false": b["excluded_apply_false"], "cluster_join_rate": cluster_join_rate,
        "public_voice_retention_rate": public_voice_retention_rate, "qa_status": qa_status,
        "qa_notes": "; ".join(qa_notes),
    }
