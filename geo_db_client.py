"""
Geopolitics App専用のSupabase（service_role・PostgREST）薄いラッパー。

article_crawler.SupabaseClient（Sustainability側の記事クロールパイプライン由来）から
Geo側の実装を完全に分離するために新設した。中身は同一（汎用PostgRESTラッパーで、
テーブル名・クエリパラメータ以外に業務ロジックを一切持たない）だが、article_crawler.py を
importしない独立したモジュールにすることで、Geo App → Sustainability コード依存を断つ。

DB接続先（config.jsonのsupabase.url等）自体はまだ変更しない（Phase 3B時点では
Shared Supabaseを指したまま。接続先の分離はPhase 6でGeo repo側のconfig.jsonの値を
差し替えるだけで行う）。
"""
import concurrent.futures
import time

import requests

from config_utils import load_config, make_proxies  # noqa: F401 (呼び出し元がここ経由でimportできるようにする)

# 2026-09-15追加: 社内プロキシ経由の接続が、TCP接続はEstablishedのまま応答が一切
# 返らない状態でハングする事象が実運用で発生した（Preprocess中に40分ハングし、
# プロセスのCPU使用率がほぼゼロのまま進行しなくなった——単一のtimeout値では
# connect/read双方に同じ値しか使えず、実際には効いていなかった）。
# connect timeoutとread timeoutを分離し、接続系エラー・タイムアウトのみ指数
# バックオフで自動リトライすることで、一時的なプロキシの瞬断・遅延への耐性を持たせる。
# これでも解消しない長時間ハングについては、呼び出し元（Preprocess等）側で
# ステージ単位のチェックポイント/再開の仕組みを別途持つ。
_CONNECT_TIMEOUT = 10
_READ_TIMEOUT = 60
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 2  # seconds: 2, 4, 8

# 2026-09-16追加: 上記のconnect/read timeoutを設定していても、実運用でPreprocess
# （geo_report_records、1,388チャンク中239チャンクで停止）が90分以上、CPU使用率
# ゼロのまま一切進まなくなる事象が再発した。原因は、requestsのtimeoutはTCP接続
# 確立後の「次のバイトが来るまでの最大待ち時間」であり、社内プロキシがTLS/CONNECT
# トンネルの確立自体で応答を止めてしまうケース（一部のプロキシ実装で発生し得る、
# urllib3側の既知の限界）には効かないことを確認した——この場合
# self._session.request(...)自体が例外を投げずに永遠に返ってこないため、
# _requestの既存リトライループ（exceptを捕まえて再試行する仕組み）にも一切
# 到達しない。そのため、request呼び出し自体を別スレッドで実行し、
# 一定時間で強制的にTimeoutとして扱う「外側の見張り」を追加する
# （実際のOSソケットは強制終了できないため、ハングしたスレッド自体は残り続けるが、
# 呼び出し元はタイムアウトとして扱い、既存のリトライ/失敗ハンドリングへ進める）。
_HANG_WATCHDOG_TIMEOUT = 90  # connect(10)+read(60)より長めに取り、通常のtimeoutが先に発火する余地を残す
_watchdog_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=16, thread_name_prefix="geo_db_hang_watchdog",
)


def _request_with_hang_watchdog(session: "requests.Session", method: str, url: str, **kwargs):
    # 2026-09-17修正: 従来は呼び出し元がkwargs["timeout"]でread timeoutを個別に
    # 長く設定していても（例: storage_upload/downloadの240秒）、この見張りタイマーは
    # 常に固定の_HANG_WATCHDOG_TIMEOUT(90秒)で強制終了していた。呼び出し元が標準
    # （_READ_TIMEOUT=60秒）より長いread timeoutを明示的に要求している場合のみ、
    # 見張りタイマーもそれより短くならないよう引き上げる（+30秒の余裕）。標準の
    # read timeoutのままの通常呼び出しでは_HANG_WATCHDOG_TIMEOUTをそのまま使う
    # ——テストが_HANG_WATCHDOG_TIMEOUTを短く差し替えて高速化する既存の仕組みを
    # 壊さないため。
    call_timeout = kwargs.get("timeout")
    call_read_timeout = call_timeout[1] if isinstance(call_timeout, tuple) else call_timeout
    if call_read_timeout and call_read_timeout > _READ_TIMEOUT:
        watchdog_timeout = call_read_timeout + 30
    else:
        watchdog_timeout = _HANG_WATCHDOG_TIMEOUT
    future = _watchdog_executor.submit(session.request, method, url, **kwargs)
    try:
        return future.result(timeout=watchdog_timeout)
    except concurrent.futures.TimeoutError:
        raise requests.exceptions.Timeout(
            f"{method} {url} が{watchdog_timeout}秒経っても応答しなかったため、"
            "強制的にタイムアウトとして扱いました（社内プロキシ側のTLS/CONNECTハングの"
            "可能性。requests標準のtimeoutが効かないケースへの対策）。",
        )


class SupabaseClient:
    """service_role key でGeoテーブル（geo_*）を読み書きする"""

    def __init__(self, config: dict):
        sb = config.get("supabase", {})
        url = sb.get("url", "").rstrip("/")
        if url.endswith("/rest/v1"):
            url = url[: -len("/rest/v1")]
        api_key = sb.get("service_role_key") or sb.get("key")
        if not url or not api_key:
            raise ValueError("config.json に supabase.url / service_role_key がありません")
        self.base_url = url
        self.headers = {
            "apikey": api_key,
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self.proxies = make_proxies(config)
        self.verify = config.get("ssl", {}).get("verify", True)
        self._session = requests.Session()

    def _request(self, method: str, url: str, *, max_retries: int = _MAX_RETRIES, **kwargs):
        """接続エラー・タイムアウト、および5xx（サーバー側の一時的な障害）のみ
        指数バックオフで自動リトライする（4xxは呼び出し元のraise_for_statusに
        判断を委ね、ここではリトライしない——リクエスト自体が不正な場合は再送しても
        同じ結果になるため）。

        max_retries（2026-09-17追加）: 実運用で、数十MB級のStorageアップロードが
        3回のリトライでは（社内プロキシ経由の不安定さのため）毎回失敗することが
        あった。呼び出し元が大容量転送だと分かっている場合のみ、この呼び出し単位で
        リトライ回数を通常より増やせるようにする（他の軽量な呼び出しの既定動作は
        変えない）。

        5xxリトライ（2026-09-17追加、ユーザー報告「ダウンロードしたらinternal
        server errorが出た、また出た」）: geo_report_records（大量データ、
        694,000件規模）のページング取得中にSupabase側が500を返す事象が発生した。
        直後に手動で同じクエリを再実行したところ成功したため、一過性のサーバー側
        障害と判断し、接続エラー/タイムアウトと同様にリトライ対象へ加える
        （4xxは含めない——リクエスト自体の誤りは再送しても直らないため）。"""
        kwargs.setdefault("timeout", (_CONNECT_TIMEOUT, _READ_TIMEOUT))
        kwargs.setdefault("proxies", self.proxies)
        kwargs.setdefault("verify", self.verify)
        last_exc = None
        last_resp = None
        for attempt in range(max_retries + 1):
            try:
                resp = _request_with_hang_watchdog(self._session, method, url, **kwargs)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                last_exc = exc
                if attempt < max_retries:
                    # 2026-09-17追加、実運用で発生した事象への対応: Supabase Storageへの
                    # 大容量ファイルアップロード中に`SSLError(SSLWantWriteError(...))`が
                    # "Max retries exceeded"付きで発生し、既存の（session再利用のままの）
                    # リトライを3回行っても毎回同じ失敗になった。requests.Sessionは内部で
                    # コネクションプーリングを行うため、TLSハンドシェイクが中途半端な
                    # 状態で失敗した接続がプールに残ったまま次のリトライで再利用され、
                    # 同じ壊れた接続に当たり続けていた可能性が高い。ConnectionError/
                    # Timeoutで再試行する際は、self._session.close()でプール中の接続を
                    # 破棄する（sessionオブジェクト自体は差し替えない——テストが
                    # client._session.requestへ直接patchしている前提を壊さないため、
                    # また.close()後もrequests.Sessionは次回呼び出し時に新しい接続を
                    # 自動的に張り直すため安全）。
                    self._session.close()
                    time.sleep(_RETRY_BACKOFF_BASE * (2 ** attempt))
                continue
            if resp.status_code >= 500 and attempt < max_retries:
                last_resp = resp
                self._session.close()
                time.sleep(_RETRY_BACKOFF_BASE * (2 ** attempt))
                continue
            return resp
        if last_resp is not None:
            return last_resp  # 最後の(失敗した)レスポンスを返し、呼び出し元のraise_for_statusに委ねる
        raise last_exc

    def select(self, table: str, params: dict) -> list:
        resp = self._request(
            "GET", f"{self.base_url}/rest/v1/{table}", headers=self.headers, params=params,
        )
        resp.raise_for_status()
        return resp.json()

    def insert(self, table: str, rows: list, prefer: str = "return=representation"):
        resp = self._request(
            "POST", f"{self.base_url}/rest/v1/{table}",
            headers={**self.headers, "Prefer": prefer}, json=rows,
        )
        resp.raise_for_status()
        return resp.json() if resp.text else None

    def update(self, table: str, params: dict, patch: dict):
        resp = self._request(
            "PATCH", f"{self.base_url}/rest/v1/{table}",
            headers={**self.headers, "Prefer": "return=minimal"}, params=params, json=patch,
        )
        resp.raise_for_status()

    def delete(self, table: str, params: dict):
        resp = self._request(
            "DELETE", f"{self.base_url}/rest/v1/{table}",
            headers={**self.headers, "Prefer": "return=minimal"}, params=params,
        )
        resp.raise_for_status()

    def rpc(self, fn_name: str, params: dict):
        resp = self._request(
            "POST", f"{self.base_url}/rest/v1/rpc/{fn_name}", headers=self.headers, json=params,
        )
        resp.raise_for_status()
        return resp.json() if resp.text else None

    # -----------------------------------------------------------------------
    # Storage（Report Studio v2 Phase 3: 大容量Artifactの正式保存先）
    # -----------------------------------------------------------------------

    # 2026-09-17変更: 実運用でcluster_representatives.csv（数MB級）のアップロード中に
    # "Connection aborted / write operation timed out"が発生した（社内プロキシ経由での
    # 大容量POSTが120秒では終わらないケースがある）。読み書きとも余裕を持たせる。
    _STORAGE_TIMEOUT = 240
    # 2026-09-17追加: 実運用でPrompt1入力CSV(cluster_representatives.csv等)が
    # 22万行を超えるほど大きくなるケースがあり、gzip圧縮・timeout延長後もなお
    # 社内プロキシ経由のアップロードが3回のリトライでは毎回失敗することがあった。
    # Storageの読み書きだけリトライ回数を通常(_MAX_RETRIES=3)より増やす。
    _STORAGE_MAX_RETRIES = 6

    def storage_upload(self, bucket: str, path: str, data: bytes, *, content_type: str = "application/octet-stream",
                        upsert: bool = True) -> None:
        headers = {"apikey": self.headers["apikey"], "Authorization": self.headers["Authorization"],
                    "Content-Type": content_type}
        if upsert:
            headers["x-upsert"] = "true"
        resp = self._request(
            "POST", f"{self.base_url}/storage/v1/object/{bucket}/{path}", headers=headers, data=data,
            timeout=(_CONNECT_TIMEOUT, self._STORAGE_TIMEOUT), max_retries=self._STORAGE_MAX_RETRIES,
        )
        resp.raise_for_status()

    def storage_download(self, bucket: str, path: str) -> bytes:
        headers = {"apikey": self.headers["apikey"], "Authorization": self.headers["Authorization"]}
        resp = self._request(
            "GET", f"{self.base_url}/storage/v1/object/{bucket}/{path}", headers=headers,
            timeout=(_CONNECT_TIMEOUT, self._STORAGE_TIMEOUT), max_retries=self._STORAGE_MAX_RETRIES,
        )
        resp.raise_for_status()
        return resp.content

    def storage_delete(self, bucket: str, path: str) -> None:
        headers = {"apikey": self.headers["apikey"], "Authorization": self.headers["Authorization"]}
        resp = self._request(
            "DELETE", f"{self.base_url}/storage/v1/object/{bucket}/{path}", headers=headers,
        )
        resp.raise_for_status()
