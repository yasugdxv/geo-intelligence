import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_benchmark_raw_text_service as rawtext  # noqa: E402

_FIELDS = ["documentId", "timestamp", "documentName", "documentText", "sourceUrl"]


def _write_csv(path: Path, rows: list):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in _FIELDS})


def test_load_record_texts_finds_requested_ids_across_multiple_files(tmp_path):
    path_a = tmp_path / "a.csv"
    path_b = tmp_path / "b.csv"
    _write_csv(path_a, [
        {"documentId": "1", "documentName": "Alice", "documentText": "hello world"},
        {"documentId": "2", "documentName": "Bob", "documentText": "not needed"},
    ])
    _write_csv(path_b, [
        {"documentId": "3", "documentName": "Carol", "documentText": "found in second file"},
    ])

    found = rawtext.load_record_texts([str(path_a), str(path_b)], {"1", "3", "999"})
    assert set(found.keys()) == {"1", "3"}
    assert found["1"] == {"title": "Alice", "text": "hello world"}
    assert found["3"] == {"title": "Carol", "text": "found in second file"}
    assert "999" not in found
    assert "2" not in found  # not requested, should not be loaded


def test_load_record_texts_returns_empty_when_none_requested(tmp_path):
    path_a = tmp_path / "a.csv"
    _write_csv(path_a, [{"documentId": "1", "documentName": "Alice", "documentText": "x"}])
    assert rawtext.load_record_texts([str(path_a)], set()) == {}
