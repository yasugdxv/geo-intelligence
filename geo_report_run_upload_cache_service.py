"""Report Studio: Preprocess用に最初にアップロードされたCSVをRun単位でOS一時領域へ
キャッシュする薄いヘルパー（2026-09-17追加）。

resume_execution_id付きの再実行（前処理の途中失敗からの再開）で、ブラウザから同じ
CSVを毎回選び直させないために導入した（geo_report_current_pipeline_api.py参照）。
`geo_report_design_api.py`のRun削除（`delete_run`）からも、Run削除時にこのキャッシュ
ディレクトリを消すために参照する——キャッシュはRun単位で数百MB〜数GBになり得るため
（ユーザー指摘「データは大きくなりやすいのですぐストレージが一杯になるはず」）、
Run削除時に忘れず消す必要がある。
"""

import os
import shutil
import tempfile

_UPLOAD_CACHE_ROOT = os.path.join(tempfile.gettempdir(), "geo_report_studio_uploads")


def run_upload_dir(run_id: str) -> str:
    return os.path.join(_UPLOAD_CACHE_ROOT, run_id)


def clear_run_upload_dir(run_id: str) -> None:
    shutil.rmtree(run_upload_dir(run_id), ignore_errors=True)


def cached_upload_paths(run_id: str) -> list:
    upload_dir = run_upload_dir(run_id)
    if not os.path.isdir(upload_dir):
        return []
    return [os.path.join(upload_dir, name) for name in sorted(os.listdir(upload_dir))]
