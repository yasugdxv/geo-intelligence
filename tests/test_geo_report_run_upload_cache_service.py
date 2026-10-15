import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_run_upload_cache_service as upload_cache  # noqa: E402


def test_cached_upload_paths_returns_empty_list_when_no_cache_exists():
    assert upload_cache.cached_upload_paths("run-does-not-exist") == []


def test_run_upload_dir_writes_and_lists_and_clears(tmp_path, monkeypatch):
    monkeypatch.setattr(upload_cache, "_UPLOAD_CACHE_ROOT", str(tmp_path))
    run_id = "run-1"

    upload_dir = upload_cache.run_upload_dir(run_id)
    os.makedirs(upload_dir, exist_ok=True)
    with open(os.path.join(upload_dir, "a.csv"), "wb") as fh:
        fh.write(b"hello")
    with open(os.path.join(upload_dir, "b.csv"), "wb") as fh:
        fh.write(b"world")

    paths = upload_cache.cached_upload_paths(run_id)
    assert sorted(os.path.basename(p) for p in paths) == ["a.csv", "b.csv"]

    upload_cache.clear_run_upload_dir(run_id)
    assert upload_cache.cached_upload_paths(run_id) == []


def test_clear_run_upload_dir_is_a_noop_when_nothing_cached():
    # 存在しないディレクトリを消そうとしてもエラーにならないこと
    upload_cache.clear_run_upload_dir("run-never-cached")
