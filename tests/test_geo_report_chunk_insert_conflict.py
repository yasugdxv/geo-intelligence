"""chunk保存で409（再送による一意制約違反）が返った場合の扱いのテスト（2026-10-05）。"""
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_current_pipeline_persistence_service as persistence  # noqa: E402


class StubClient:
    """insertの呼び出しを記録し、指定した呼び出し番号でHTTPエラーを返す。"""

    def __init__(self, fail_on_call=None, status=409):
        self.calls, self.fail_on_call, self.status = [], fail_on_call, status

    def insert(self, table, rows):
        self.calls.append(len(rows))
        if self.fail_on_call == len(self.calls):
            response = requests.Response()
            response.status_code = self.status
            raise requests.HTTPError(f"{self.status}", response=response)


@pytest.fixture(autouse=True)
def no_progress_writes(monkeypatch):
    monkeypatch.setattr(persistence, "update_preprocess_progress", lambda *a, **k: None)


def test_409_means_chunk_already_stored_and_saving_continues():
    client = StubClient(fail_on_call=2, status=409)
    payload = [{"id": i} for i in range(1200)]          # 500 + 500 + 200
    persistence._insert_chunks_with_progress(client, "t", payload, execution_id="e", phase="p")
    assert client.calls == [500, 500, 200]               # 2番目が409でも3番目まで進む（再送もしない）


def test_other_http_errors_still_fail():
    client = StubClient(fail_on_call=1, status=500)
    with pytest.raises(requests.HTTPError):
        persistence._insert_chunks_with_progress(client, "t", [{"id": 1}], execution_id="e", phase="p")
