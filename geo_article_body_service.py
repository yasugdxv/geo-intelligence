"""記事の本文取得（Intelligence AI用）。

記事DB（geo_external_signals）にはタイトルと短い要約しかないため、回答の根拠にする記事（上位の数件）だけ、
記事URLから本文を取得して使う。

  - 取得は「その場で必要な分だけ」（1回の調査につき最大 MAX_FETCH 件）。取得結果は geo_external_signal_bodies に
    キャッシュし、同じ記事は2回目以降は取得しない（テーブル未作成のときは、キャッシュせず毎回取得する）。
  - ボット対策・購読制限で取れない記事は、無理に回避しない。status=blocked/failed/too_short として記録し、
    回答では「本文は確認できていない（要約のみ）」として扱う。
  - 本文の抽出は既存の geo_documents_store.extract_text（trafilatura / pypdf）をそのまま使う。
  - 社内の分析用途のみ。本文は回答の根拠にする短い抜粋だけをLLMへ渡し、画面にも全文は出さない。
  - 安全: http/https のみ。名前解決の結果が社内・ループバック等のアドレスなら取得しない。サイズ・時間の上限あり。
"""
from __future__ import annotations

import hashlib
import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests

import geo_documents_store
from config_utils import load_config, make_proxies

MAX_BYTES = 2_000_000
FETCH_TIMEOUT = 15
MAX_FETCH = 4                     # 1回の調査で新規に取得する記事の上限
BUDGET_SECONDS = 25               # 取得全体の待ち時間の上限
MAX_CHARS_STORED = 30_000
MIN_CHARS = 200                   # これより短い抽出結果は「本文が取れていない」とみなす
RETRY_AFTER_DAYS = 3              # 失敗・拒否した記事を、再取得しない期間


def _safe_url(url: str) -> bool:
    try:
        p = urlparse(url)
    except ValueError:
        return False
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    try:
        infos = socket.getaddrinfo(p.hostname, None)
    except OSError:
        return True                # 社内プロキシ経由でしか名前解決できない環境がある。解決できない場合は取得先をプロキシに任せる
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def fetch_body(url: str) -> dict:
    """1件の記事URLから本文を取得する。戻り値: {status, body_text, char_count, content_hash, http_status, error}
    status: ok / blocked（401/403/429）/ failed（接続・HTTPエラー）/ too_short（本文が取れなかった）。"""
    base = {"status": "failed", "body_text": None, "char_count": 0, "content_hash": None, "http_status": None, "error": None}
    if not _safe_url(url):
        return {**base, "error": "取得できないURLです"}
    try:
        cfg = load_config()
    except Exception:  # noqa: BLE001
        cfg = {}
    try:
        resp = requests.get(url, headers=geo_documents_store._HEADERS, proxies=make_proxies(cfg) or None,
                            verify=cfg.get("ssl", {}).get("verify", True), timeout=FETCH_TIMEOUT, stream=True, allow_redirects=True)
        raw = b""
        for chunk in resp.iter_content(65536):
            raw += chunk
            if len(raw) > MAX_BYTES:
                break
        ctype = (resp.headers.get("Content-Type") or "").lower()
    except requests.RequestException as exc:
        return {**base, "error": f"接続できません（{type(exc).__name__}）"}
    if resp.status_code in (401, 403, 429):
        return {**base, "status": "blocked", "http_status": resp.status_code, "error": f"アクセスが拒否されました（HTTP {resp.status_code}）"}
    if resp.status_code != 200:
        return {**base, "http_status": resp.status_code, "error": f"HTTP {resp.status_code}"}
    filename = "x.pdf" if "pdf" in ctype else "x.html"
    ext = geo_documents_store.extract_text(raw[:MAX_BYTES], filename, ctype)
    text = (ext.get("text") or "").strip()
    chash = hashlib.sha256(raw).hexdigest()
    if not ext.get("ok") or len(text) < MIN_CHARS:
        return {**base, "status": "too_short", "http_status": 200, "content_hash": chash, "char_count": len(text),
                "error": "本文を抽出できませんでした（購読制限・動的ページの可能性）"}
    text = text[:MAX_CHARS_STORED]
    return {"status": "ok", "body_text": text, "char_count": len(text), "content_hash": chash, "http_status": 200, "error": None}


def _read_cache(client, ids: list) -> tuple:
    """戻り値: (キャッシュ有効か, {id: 行})。テーブル未作成なら (False, {})。"""
    if not ids:
        return True, {}
    try:
        rows = client.select("geo_external_signal_bodies", {
            "select": "*", "external_signal_id": "in.(" + ",".join(ids) + ")"})
    except Exception:  # noqa: BLE001 — テーブル未作成（SQL未適用）
        return False, {}
    return True, {r["external_signal_id"]: r for r in rows}


def _recent_failure(row: dict) -> bool:
    if row.get("status") == "ok":
        return False
    try:
        fetched = datetime.fromisoformat(str(row.get("fetched_at"))[:19]).replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - fetched < timedelta(days=RETRY_AFTER_DAYS)


def ensure_bodies(client, items: list, *, max_fetch: int = MAX_FETCH, budget_seconds: float = BUDGET_SECONDS) -> dict:
    """items: [{"id": 記事ID, "url": URL}]。戻り値: {id: {"status","body_text","cached": bool}}（取れなかった記事も status付きで返す）。
    キャッシュ済みの記事は取得しない。新規取得は max_fetch 件まで、全体で budget_seconds 秒まで。"""
    out: dict = {}
    ids = [i["id"] for i in items if i.get("id")]
    cache_ok, cache = _read_cache(client, ids)
    todo = []
    for it in items:
        row = cache.get(it["id"])
        if row and (row.get("status") == "ok" or _recent_failure(row)):
            out[it["id"]] = {"status": row["status"], "body_text": row.get("body_text"), "cached": True}
        elif it.get("url"):
            todo.append(it)
        else:
            out[it["id"]] = {"status": "failed", "body_text": None, "cached": False}
    todo = todo[:max_fetch]
    if todo:
        pool = ThreadPoolExecutor(max_workers=min(4, len(todo)))
        futures = {it["id"]: pool.submit(fetch_body, it["url"]) for it in todo}
        try:
            deadline_each = max(budget_seconds, 1)
            for it in todo:
                try:
                    res = futures[it["id"]].result(timeout=deadline_each)
                except FutureTimeout:
                    res = {"status": "failed", "body_text": None, "char_count": 0, "content_hash": None, "http_status": None,
                           "error": "取得に時間がかかりすぎました"}
                    deadline_each = 1
                out[it["id"]] = {"status": res["status"], "body_text": res["body_text"], "cached": False}
                if cache_ok:
                    row = {"status": res["status"], "body_text": res["body_text"], "char_count": res["char_count"],
                           "content_hash": res["content_hash"], "http_status": res["http_status"], "error": res["error"],
                           "fetched_at": datetime.now(timezone.utc).isoformat()}
                    try:
                        if it["id"] in cache:
                            client.update("geo_external_signal_bodies", {"external_signal_id": f"eq.{it['id']}"}, row)
                        else:
                            client.insert("geo_external_signal_bodies", [{"external_signal_id": it["id"], **row}])
                    except Exception:  # noqa: BLE001 — キャッシュできなくても、取得した本文は今回の調査に使う
                        pass
        finally:
            pool.shutdown(wait=False)
    for it in items:                           # 今回の上限で取得しなかった記事
        out.setdefault(it["id"], {"status": "not_fetched", "body_text": None, "cached": False})
    return out


def best_passage(text: str, tokens: list, n: int = 1200) -> str:
    """本文のうち、検索語が最初に出てくる付近の抜粋を返す（無ければ冒頭）。LLMへ渡す量を抑えるため。"""
    t = " ".join((text or "").split())
    if len(t) <= n:
        return t
    low = t.lower()
    pos = min((p for p in (low.find(tok.lower()) for tok in tokens if tok) if p >= 0), default=0)
    start = max(0, min(pos - n // 4, len(t) - n))
    return ("…" if start else "") + t[start:start + n] + ("…" if start + n < len(t) else "")
