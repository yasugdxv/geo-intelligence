"""Perplexity Sonar API によるWeb検索（テーマ探索の検索部分の差し替え用）。

- キーは config.json の api_keys.perplexity、または環境変数 PERPLEXITY_API_KEY（環境変数を優先）。
  コードに直書きしない・ログや画面に出さない。
- 検索1回 = Sonarへ1リクエスト。戻り値は、Azure版（geo_expert_research_service.run_external_research）と
  同じ形に揃える: {"summary", "citations": [{"url", "title", "published_at"}]}。
  published_at は検索結果のメタデータの公開日（無ければNone＝日付不明のまま扱う）。
- 根拠にするのは search_results（実在するページのURL・タイトル・公開日）だけ。回答本文中のURLは使わない。
- 社内プロキシ・SSL検証は config.json の設定（proxy / ssl.verify）に従う。
- 失敗は例外にする（呼び出し側がAzure版へ切り替える）。
"""
from __future__ import annotations

import os
import time

import requests

from config_utils import load_config, make_proxies

API_URL = "https://api.perplexity.ai/chat/completions"
MODEL = "sonar"
TIMEOUT_SECONDS = 90
MAX_DOMAIN_FILTER = 20
MAX_ATTEMPTS = 3            # 429（レート制限）・5xxのときの試行回数
BACKOFF_SECONDS = (3, 8)    # 1回目・2回目の失敗後に待つ秒数（Retry-Afterがあればそれを優先、上限20秒）


class PerplexityError(Exception):
    """Perplexityの検索に失敗した（キー未設定・HTTPエラー・応答の形が想定外）。"""


def get_settings() -> dict | None:
    """キーが設定されていれば {"key", "proxies", "verify"}、無ければNone。キーの値は外へ出さない。"""
    try:
        cfg = load_config()
    except Exception:  # noqa: BLE001 — config.jsonが読めなくても、Azure版へ切り替えられるようにする
        cfg = {}
    key = (os.environ.get("PERPLEXITY_API_KEY") or (cfg.get("api_keys") or {}).get("perplexity") or "").strip()
    if not key:
        return None
    return {"key": key, "proxies": make_proxies(cfg), "verify": cfg.get("ssl", {}).get("verify", True)}


def _recency(period_days: int | None) -> str | None:
    if not period_days:
        return None
    if period_days <= 1:
        return "day"
    if period_days <= 7:
        return "week"
    if period_days <= 31:
        return "month"
    return None


def search(settings: dict, *, question: str, domains: list | None = None,
           period_days: int | None = None, instruction: str | None = None) -> dict:
    """Sonarで1回検索する。domainsを渡すと、そのドメイン内に限定する。"""
    body: dict = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "地政学インテリジェンスの調査アシスタントです。質問について、"
                                          "最近の動きを、異なる情報源から幅広く調べ、日本語で簡潔にまとめてください。"
                                          + (f" {instruction}" if instruction else "")},
            {"role": "user", "content": question}],
    }
    rec = _recency(period_days)
    if rec:
        body["search_recency_filter"] = rec
    if domains:
        body["search_domain_filter"] = sorted(domains)[:MAX_DOMAIN_FILTER]
    r = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            r = requests.post(
                API_URL, json=body, proxies=settings["proxies"], verify=settings["verify"],
                timeout=TIMEOUT_SECONDS,
                headers={"Authorization": f"Bearer {settings['key']}", "Content-Type": "application/json"})
        except requests.RequestException as exc:
            raise PerplexityError(f"Perplexityに接続できません: {type(exc).__name__}") from exc
        if r.status_code not in (429, 500, 502, 503, 504) or attempt == MAX_ATTEMPTS - 1:
            break
        try:
            wait = float((getattr(r, "headers", None) or {}).get("Retry-After") or BACKOFF_SECONDS[attempt])
        except (TypeError, ValueError):
            wait = BACKOFF_SECONDS[attempt]
        time.sleep(min(max(wait, 1), 20))
    if r.status_code != 200:
        # 応答本文にキーが含まれることは無いが、念のため長さも切る
        raise PerplexityError(f"Perplexityがエラーを返しました（HTTP {r.status_code}）: {r.text[:120]}")
    try:
        data = r.json()
    except ValueError as exc:
        raise PerplexityError("Perplexityの応答を読み取れません") from exc

    results = data.get("search_results") or []
    citations, seen = [], set()
    for item in results:
        url = (item.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        citations.append({"url": url, "title": item.get("title"),
                          "published_at": (item.get("date") or None)})
    if not results:   # search_resultsが無い旧形式の応答: URLだけの引用にする（日付は不明のまま）
        for url in data.get("citations") or []:
            if isinstance(url, str) and url.strip() and url.strip() not in seen:
                seen.add(url.strip())
                citations.append({"url": url.strip(), "title": None, "published_at": None})
    summary = ""
    try:
        summary = (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        pass
    return {"summary": summary, "found": bool(citations), "citations": citations}
