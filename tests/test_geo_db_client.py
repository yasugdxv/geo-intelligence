import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests  # noqa: E402

from geo_db_client import SupabaseClient  # noqa: E402


def _make_client() -> SupabaseClient:
    config = {"supabase": {"url": "https://example.supabase.co", "service_role_key": "key123"}}
    return SupabaseClient(config)


def _ok_response(payload):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    resp.text = "ok"
    return resp


def _error_response(status_code):
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status.side_effect = requests.exceptions.HTTPError(str(status_code))
    return resp


def test_select_retries_on_connection_error_then_succeeds():
    """2026-09-15発覚: 社内プロキシがTCP接続を確立したまま応答を返さずハングする
    事象が実運用で起きた。一時的な接続エラー・タイムアウトは自動リトライで
    吸収されることを確認する。"""
    client = _make_client()
    responses = [
        requests.exceptions.ConnectionError("boom"),
        requests.exceptions.ConnectionError("boom"),
        _ok_response([{"id": "1"}]),
    ]
    with patch.object(client._session, "request", side_effect=responses) as mock_request, \
            patch("geo_db_client.time.sleep") as mock_sleep:
        result = client.select("geo_report_records", {"select": "id"})

    assert result == [{"id": "1"}]
    assert mock_request.call_count == 3
    assert mock_sleep.call_count == 2


def test_select_raises_after_exhausting_retries():
    client = _make_client()
    with patch.object(client._session, "request",
                       side_effect=requests.exceptions.ConnectionError("boom")) as mock_request, \
            patch("geo_db_client.time.sleep"):
        try:
            client.select("geo_report_records", {"select": "id"})
            assert False, "should have raised"
        except requests.exceptions.ConnectionError:
            pass
    assert mock_request.call_count == 4  # 初回 + 3リトライ


def test_select_retries_on_read_timeout():
    client = _make_client()
    responses = [requests.exceptions.ReadTimeout("timed out"), _ok_response([{"id": "1"}])]
    with patch.object(client._session, "request", side_effect=responses) as mock_request, \
            patch("geo_db_client.time.sleep") as mock_sleep:
        result = client.select("geo_report_records", {"select": "id"})

    assert result == [{"id": "1"}]
    assert mock_request.call_count == 2
    mock_sleep.assert_called_once_with(2)


def test_select_does_not_retry_on_4xx_http_error_status():
    """4xxはリクエスト自体の誤りであることが多く、再送しても同じ結果になるため
    リトライ対象にしない。"""
    client = _make_client()
    with patch.object(client._session, "request", return_value=_error_response(400)) as mock_request:
        try:
            client.select("geo_report_records", {"select": "id"})
            assert False, "should have raised"
        except requests.exceptions.HTTPError:
            pass
    assert mock_request.call_count == 1


def test_select_retries_on_5xx_http_error_status_then_succeeds():
    """2026-09-17追加、ユーザー報告「ダウンロードしたらinternal server errorが
    出た、また出た」: geo_report_records（69万件規模）のページング取得中に
    Supabaseが一時的に500を返す事象が発生し、直後に手動で同じクエリを再実行したら
    成功した（一過性のサーバー側障害）。5xxは接続エラー・タイムアウトと同様に
    リトライ対象へ加えたことを確認する。"""
    client = _make_client()
    responses = [_error_response(500), _ok_response([{"id": "1"}])]
    with patch.object(client._session, "request", side_effect=responses) as mock_request, \
            patch("geo_db_client.time.sleep") as mock_sleep:
        result = client.select("geo_report_records", {"select": "id"})

    assert result == [{"id": "1"}]
    assert mock_request.call_count == 2
    mock_sleep.assert_called_once()


def test_select_raises_after_exhausting_5xx_retries():
    client = _make_client()
    with patch.object(client._session, "request", return_value=_error_response(503)) as mock_request, \
            patch("geo_db_client.time.sleep"):
        try:
            client.select("geo_report_records", {"select": "id"})
            assert False, "should have raised"
        except requests.exceptions.HTTPError:
            pass
    assert mock_request.call_count == 4  # 初回 + 3リトライ


def test_request_uses_separate_connect_and_read_timeouts_by_default():
    client = _make_client()
    with patch.object(client._session, "request", return_value=_ok_response([])) as mock_request:
        client.select("geo_report_records", {"select": "id"})

    _, kwargs = mock_request.call_args
    assert kwargs["timeout"] == (10, 60)


def test_request_that_never_returns_is_treated_as_timeout_by_hang_watchdog():
    """2026-09-16発覚: requestsのtimeout設定があっても、社内プロキシがTLS/CONNECT
    トンネル確立自体で応答を止めるケースではsession.request自体が例外を投げずに
    永遠に返ってこず、_requestの既存リトライループにも到達しなかった（実運用で
    Preprocessが90分以上、CPU使用率ゼロのまま停止）。session.requestが本当に
    ハングするケースを再現し、外側の見張り（_HANG_WATCHDOG_TIMEOUT）が
    強制的にTimeoutへ変換することを確認する。"""
    client = _make_client()
    release_event = threading.Event()

    def _hang_forever(*args, **kwargs):
        release_event.wait()  # テスト終了までブロックし続ける（本物のハングを模倣）
        return _ok_response([{"id": "1"}])

    try:
        with patch.object(client._session, "request", side_effect=_hang_forever), \
                patch("geo_db_client._HANG_WATCHDOG_TIMEOUT", 0.2), \
                patch("geo_db_client.time.sleep"):
            try:
                client.select("geo_report_records", {"select": "id"})
                assert False, "should have raised"
            except requests.exceptions.Timeout:
                pass
    finally:
        release_event.set()  # ハングしていたワーカースレッドを解放してリークさせない


def test_connection_error_retry_closes_session_to_discard_pooled_connection():
    """2026-09-17発覚: Supabase Storageへの大容量アップロード中に
    SSLError(SSLWantWriteError)が"Max retries exceeded"付きで発生し、session再利用の
    ままリトライしても毎回同じ失敗になった。requests.Sessionが内部で保持する
    コネクションプールに、TLSハンドシェイクが中途半端な状態で失敗した接続が残ったまま
    次のリトライで再利用されていた可能性が高い。ConnectionError/Timeoutでの再試行の
    直前に、session.close()でプール中の接続を破棄することを確認する（session
    オブジェクト自体は差し替えない——テストがclient._session.requestへ直接patchして
    いる前提を壊さないため）。"""
    client = _make_client()
    responses = [
        requests.exceptions.ConnectionError("boom"),
        _ok_response([{"id": "1"}]),
    ]
    with patch.object(client._session, "request", side_effect=responses), \
            patch.object(client._session, "close") as mock_close, \
            patch("geo_db_client.time.sleep"):
        result = client.select("geo_report_records", {"select": "id"})

    assert result == [{"id": "1"}]
    mock_close.assert_called_once()


def test_insert_and_update_and_delete_also_retry_on_connection_error():
    client = _make_client()

    with patch.object(client._session, "request",
                       side_effect=[requests.exceptions.ConnectionError("boom"), _ok_response([{"id": "1"}])]), \
            patch("geo_db_client.time.sleep"):
        result = client.insert("geo_report_records", [{"a": 1}])
    assert result == [{"id": "1"}]

    ok = _ok_response(None)
    ok.text = ""
    with patch.object(client._session, "request",
                       side_effect=[requests.exceptions.ConnectionError("boom"), ok]), \
            patch("geo_db_client.time.sleep"):
        client.update("geo_report_records", {"id": "eq.1"}, {"a": 2})

    with patch.object(client._session, "request",
                       side_effect=[requests.exceptions.ConnectionError("boom"), ok]), \
            patch("geo_db_client.time.sleep"):
        client.delete("geo_report_records", {"id": "eq.1"})
