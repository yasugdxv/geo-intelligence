"""GDELT DOC API 用の「アプリケーション全体」のレート制御と排他（Phase D.1 / 429対策）。

背景: GDELT DOCは「同一IPから5秒に1回」を超えると429を返す。従来の間隔制御は
プロセス内のグローバル変数（geo_connector_gdelt._last_request_at）だけで、
- API server / CLI / Scheduler tick / 手動実行が別プロセスだと、それぞれが「5秒空けたつもり」でも
  全体では同時アクセスになる
- スレッド間でも同様（チェックと更新が分離している）
- 429のRetry-Afterを読まず、高速に再試行して状況を悪化させる
という問題があった。

このモジュールは次を提供する（新しいロック基盤は作らず、ローカルファイルだけを使う）:
1. wait_turn(): プロセス・スレッドをまたいで「最低呼び出し間隔」を守る（既定6秒=公式5秒+余裕）。
   待ち行列は「枠の予約」で実現する（待っている間はロックを持たない）。
2. register_429(): 429を受けたら全プロセス共通のクールダウンを設定する（Retry-After優先、無ければ指数backoff+jitter）。
3. collection_lease(): DOC収集全体の排他（定期実行・catch-up・手動実行が同時に走らない）。再入可能。

状態の置き場: logs/（環境変数 GEO_GDELT_STATE_DIR で変更可）。単一ホスト前提。
複数ホストから同じGDELTへアクセスする構成になった場合は、DBベースの共有状態へ置き換える必要がある。
"""
from __future__ import annotations

import contextlib
import json
import os
import random
import threading
import time
from email.utils import parsedate_to_datetime
from pathlib import Path

# ---- 一箇所で管理する設定 ----------------------------------------------------------------
MIN_INTERVAL_SECONDS = 6.0          # 公式は「5秒に1回」。余裕を1秒足す
JITTER_SECONDS = 0.5                # 複数プロセスが同じ瞬間に動かないよう、0〜0.5秒をランダムに足す
MAX_COOLDOWN_WAIT_SECONDS = 120.0   # クールダウンがこれより長いなら、待たずにすぐ失敗する（Job側のbackoffに任せる）
MAX_RETRY_AFTER_SECONDS = 900.0     # Retry-Afterの上限（異常に大きい値でも15分まで）

RATE_LIMIT_POLICY = {
    "max_429_retries": 3,                          # 1回のリクエストで429を受けて再試行する最大回数
    "backoff_seconds": (30.0, 90.0, 180.0),        # Retry-Afterが無い時の待ち時間（+jitter）
    "cooldown_after_exhausted_seconds": 600.0,     # 上限まで429が続いたら、全体で10分休む
}

LEASE_STALE_SECONDS = 30 * 60       # リースの印が30分更新されなければ、落ちたプロセスの残りとみなす
STATE_LOCK_STALE_SECONDS = 60       # 状態ファイルのロックが60秒以上古ければ解放する
STATE_LOCK_TIMEOUT_SECONDS = 30


class GdeltCooldownError(RuntimeError):
    """429による全体クールダウン中。メッセージに 'GDELT status=429' を含める
    （Schedulerはこの文字列でretryableと分類し、backoffして再試行する）。"""


class GdeltBusyError(RuntimeError):
    """別のGDELT DOC収集が実行中。失敗ではなく「今は実行しない」の意味（APIは409、Schedulerはskip）。"""


def state_dir() -> Path:
    return Path(os.environ.get("GEO_GDELT_STATE_DIR") or (Path(__file__).parent / "logs"))


_thread_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 状態ファイル（JSON）と、その更新を守る短いロック
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + STATE_LOCK_TIMEOUT_SECONDS
    fd = None
    while fd is None:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except (FileExistsError, PermissionError):
            # Windowsでは、別プロセスが鍵を削除している最中に作成するとPermissionErrorになる（=競合。再試行する）
            try:
                if time.time() - path.stat().st_mtime > STATE_LOCK_STALE_SECONDS:
                    path.unlink()
                    continue
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError("GDELTの状態ロックを取得できませんでした")
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(fd)
        try:
            path.unlink()
        except OSError:
            pass


def _load() -> dict:
    try:
        return json.loads((state_dir() / "gdelt_doc_rate_state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(state: dict) -> None:
    p = state_dir() / "gdelt_doc_rate_state.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, p)


def _locked_update(fn):
    with _thread_lock, _file_lock(state_dir() / "gdelt_doc_rate_state.lock"):
        state = _load()
        result = fn(state)
        _save(state)
        return result


# ---------------------------------------------------------------------------
# 1) 最低呼び出し間隔 + クールダウン
# ---------------------------------------------------------------------------

def wait_turn(min_interval_seconds: float = MIN_INTERVAL_SECONDS) -> float:
    """次のGDELT DOCリクエストを送ってよい時刻まで待つ。戻り値は待った秒数。
    設定値が公式+余裕より小さくても、最低 MIN_INTERVAL_SECONDS は守る。
    クールダウンが MAX_COOLDOWN_WAIT_SECONDS より長い場合は待たず GdeltCooldownError を送出する。"""
    interval = max(float(min_interval_seconds or 0), MIN_INTERVAL_SECONDS)

    def reserve(state: dict) -> float:
        now = time.time()
        cooldown_until = float(state.get("cooldown_until") or 0)
        remaining = cooldown_until - now
        if remaining > MAX_COOLDOWN_WAIT_SECONDS:
            raise GdeltCooldownError(
                f"GDELT status=429: 全体クールダウン中です（あと{int(remaining)}秒）。時間をおいて再試行します")
        slot = max(now, float(state.get("last_request_at") or 0) + interval + random.uniform(0, JITTER_SECONDS),
                   cooldown_until)
        state["last_request_at"] = slot            # 枠を予約（待っている間は他のプロセスが次の枠を取る）
        return slot - now

    wait = _locked_update(reserve)
    if wait > 0:
        time.sleep(wait)
    _touch_lease()                                  # 実行中の印を更新（長い収集でも「落ちた」と誤判定しない）
    return wait


def parse_retry_after(value) -> float | None:
    """Retry-After（秒数、またはHTTP日付）を秒数にする。解釈できなければNone。"""
    if not isinstance(value, (str, int, float)):
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            dt = parsedate_to_datetime(str(value))
            seconds = dt.timestamp() - time.time()
        except (TypeError, ValueError):
            return None
    if seconds < 0:
        return None
    return min(seconds, MAX_RETRY_AFTER_SECONDS)


def register_429(retry_after_header=None, consecutive_429: int = 1) -> dict:
    """429を受けた。全プロセス共通のクールダウンを設定し、{"cooldown_seconds", "exhausted", "source"}を返す。
    Retry-Afterがあればそれを尊重し、無ければ backoff_seconds（+jitter）。上限回数に達したら長いcooldown。"""
    policy = RATE_LIMIT_POLICY
    exhausted = consecutive_429 >= policy["max_429_retries"]
    ra = parse_retry_after(retry_after_header)
    if exhausted:
        seconds, source = max(policy["cooldown_after_exhausted_seconds"], ra or 0), "exhausted"
    elif ra is not None:
        seconds, source = ra + random.uniform(0, JITTER_SECONDS), "retry-after"
    else:
        steps = policy["backoff_seconds"]
        seconds, source = steps[min(consecutive_429 - 1, len(steps) - 1)] + random.uniform(0, JITTER_SECONDS * 4), "backoff"

    def apply(state: dict) -> None:
        state["cooldown_until"] = max(float(state.get("cooldown_until") or 0), time.time() + seconds)
        state["last_429_at"] = time.time()
    _locked_update(apply)
    return {"cooldown_seconds": round(seconds, 1), "exhausted": exhausted, "source": source}


def get_state() -> dict:
    """診断用（Operations/Runbook）。現在のクールダウンと最終リクエスト時刻。"""
    s = _load()
    now = time.time()
    return {"cooldown_remaining_seconds": max(0.0, float(s.get("cooldown_until") or 0) - now),
            "last_request_age_seconds": (now - float(s["last_request_at"])) if s.get("last_request_at") else None,
            "last_429_age_seconds": (now - float(s["last_429_at"])) if s.get("last_429_at") else None}


# ---------------------------------------------------------------------------
# 2) DOC収集全体の排他（リース）
# ---------------------------------------------------------------------------

_lease_local = threading.local()
_lease_owner = {"thread": None}          # 同一プロセスの別スレッドも「別の収集」として扱う
_lease_guard = threading.Lock()


def _lease_path() -> Path:
    return state_dir() / "gdelt_doc_collection.lease"


def _touch_lease() -> None:
    if getattr(_lease_local, "depth", 0) > 0:
        try:
            os.utime(_lease_path(), None)
        except OSError:
            pass


@contextlib.contextmanager
def collection_lease(name: str = "gdelt_doc"):
    """DOC収集の入口（定期実行・catch-up・手動実行）が取る排他。
    既に別の収集（別プロセス・別スレッド）が実行中ならGdeltBusyErrorを送出する。
    同じスレッド内の入れ子呼び出し（batch → catch-up → 1 query）は再入として通す。"""
    depth = getattr(_lease_local, "depth", 0)
    if depth > 0:
        _lease_local.depth = depth + 1
        try:
            yield
        finally:
            _lease_local.depth -= 1
        return

    path = _lease_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lease_guard:
        if _lease_owner["thread"] is not None and _lease_owner["thread"] != threading.get_ident():
            raise GdeltBusyError("別のGDELT DOC収集が実行中です（同一プロセス内）")
        fd = None
        for _ in range(2):
            try:
                fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{name} pid={os.getpid()} at={time.time()}".encode())
                break
            except FileExistsError:
                try:
                    if time.time() - path.stat().st_mtime > LEASE_STALE_SECONDS:
                        path.unlink()           # 落ちたプロセスの残り
                        continue
                except OSError:
                    pass
                raise GdeltBusyError("別のGDELT DOC収集が実行中です") from None
        if fd is None:
            raise GdeltBusyError("別のGDELT DOC収集が実行中です")
        os.close(fd)
        _lease_owner["thread"] = threading.get_ident()
    _lease_local.depth = 1
    try:
        yield
    finally:
        _lease_local.depth = 0
        with _lease_guard:
            _lease_owner["thread"] = None
        try:
            path.unlink()
        except OSError:
            pass


def lease_status() -> dict:
    """現在DOC収集が実行中か（診断・UI用）。ファイルを読むだけで状態は変えない。"""
    p = _lease_path()
    try:
        age = time.time() - p.stat().st_mtime
    except OSError:
        return {"held": False, "age_seconds": None}
    return {"held": age <= LEASE_STALE_SECONDS, "age_seconds": round(age, 1)}


def doc_collection_guard(fn):
    """DOC収集の公開入口に付けるデコレータ（collection_leaseで包む）。"""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with collection_lease():
            return fn(*args, **kwargs)
    return wrapper
