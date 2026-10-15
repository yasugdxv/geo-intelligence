"""分析期間(period_start/period_end)による絞り込みのテスト（2026-10-05）。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_current_pipeline_service as cp  # noqa: E402
from tests.test_geo_report_review_reasons_and_lint_integration import master_rows, news, write  # noqa: E402


def make_rows(dates):
    """dates: 'YYYY-MM-DD'のlist。1日1件、本文は件ごとに異なる（重複扱いにならない）。"""
    rows = []
    for i, d in enumerate(dates):
        row = news(i, f"alpha topic details number{i}x")
        row["timestamp"] = f"{d}T10:00:00Z"
        rows.append(row)
    return rows


def run(tmp_path, dates, **kw):
    write(tmp_path / "S1_x.csv", make_rows(dates))
    return cp.run_preprocess([str(tmp_path / "S1_x.csv")], master_rows(), **kw)


DATES = ["2026-09-05", "2026-09-06", "2026-09-07", "2026-09-15", "2026-09-30", "2026-10-01", "2026-10-02"]


def test_no_period_keeps_every_row_as_before(tmp_path):
    result = run(tmp_path, DATES)
    assert result.total_records == 7 and result.out_of_period_total == 0
    assert result.period_start == "" and result.period_end == ""


def test_period_excludes_rows_outside_both_ends_inclusive(tmp_path):
    result = run(tmp_path, DATES, period_start="2026-09-07", period_end="2026-09-30")
    assert result.total_records == 3                       # 9/7, 9/15, 9/30（両端を含む）
    assert result.out_of_period_total == 4                 # 9/5, 9/6, 10/1, 10/2
    assert result.out_of_period_by_signal == {"S1": 4}
    kept_dates = {r["date_key"] for r in result.record_map_rows}
    assert kept_dates == {"2026-09-07", "2026-09-15", "2026-09-30"}


def test_excluded_rows_do_not_leak_into_counts_or_weeks(tmp_path):
    result = run(tmp_path, DATES, period_start="2026-09-07", period_end="2026-09-30")
    assert result.signal_total == {"S1": 3} and result.nonduplicate_records == 3
    assert len(result.record_map_rows) == result.total_records
    # weekly_countsのキーは「週開始|週終了|…」。9/5・9/6が属する週(9/1開始)は現れない。
    week_starts = {k.split("|")[0] for k in result.weekly_counts}
    assert week_starts == {"2026-09-07", "2026-09-14", "2026-09-28"}


def test_only_start_or_only_end_works(tmp_path):
    only_start = run(tmp_path, DATES, period_start="2026-09-15")
    assert only_start.total_records == 4 and only_start.out_of_period_total == 3
    other = Path(tmp_path) / "b"
    other.mkdir()
    only_end = run(other, DATES, period_end="2026-09-07")
    assert only_end.total_records == 3 and only_end.out_of_period_total == 4


def test_rows_with_unparsable_dates_are_kept(tmp_path):
    rows = make_rows(["2026-09-10", "2026-09-10"])
    rows[1]["timestamp"] = "not a date"
    write(tmp_path / "S1_x.csv", rows)
    result = cp.run_preprocess([str(tmp_path / "S1_x.csv")], master_rows(),
                               period_start="2026-09-07", period_end="2026-09-30")
    assert result.total_records == 2 and result.out_of_period_total == 0


def test_record_ids_do_not_change_when_rows_are_excluded(tmp_path):
    """record_id列が無く代替ハッシュを使う場合でも、期間外行を挟んで読んだ行のIDは期間指定なしと同じ。"""
    rows = make_rows(DATES)
    for row in rows:
        row["documentId"] = ""
    write(tmp_path / "S1_x.csv", rows)
    full = cp.run_preprocess([str(tmp_path / "S1_x.csv")], master_rows())
    cut = cp.run_preprocess([str(tmp_path / "S1_x.csv")], master_rows(),
                            period_start="2026-09-07", period_end="2026-09-30")
    full_ids = {r["date_key"]: r["record_id"] for r in full.record_map_rows}
    assert {r["date_key"]: r["record_id"] for r in cut.record_map_rows} == {
        d: full_ids[d] for d in ("2026-09-07", "2026-09-15", "2026-09-30")}


def test_invalid_period_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        run(tmp_path, DATES, period_start="2026-09-30", period_end="2026-09-07")
    with pytest.raises(ValueError):
        run(tmp_path, DATES, period_start="garbage")


def test_all_rows_outside_period_gives_clear_error(tmp_path):
    with pytest.raises(ValueError, match="期間"):
        run(tmp_path, DATES, period_start="2027-01-01", period_end="2027-01-31")
