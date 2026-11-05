# -*- coding: utf-8 -*-
"""Foundation強化 段階0: 既存の文書（geo_documents）の品質回復バッチ（公的資料のみ）。

やること（既定は dry-run＝DBを一切書き換えない。--live で実際にUPDATE）:
  1. 本文がスタブ（タイトルのみ/ごく短い）でURLがある文書 → 本文を取得して埋める
     （既存 geo_documents_store.backfill_stub_document の品質ゲートをそのまま使う:
       本文が実質あること・既存と重複しないこと。ゲート不合格の行は一切触れない）。
  2. 本文はあるが公開日が空でURLがある記事 → ページのメタ情報から公開日だけを補完
     （URLや現在時刻からの推測はしない。取れなければ空のまま）。
対象は公的資料（政府・国際機関・公的研究機関）のドメインのみ。報道・民間・有料サイト、
トップページや統計系列ページ（記事ではないもの）は対象外にして、理由を記録する。

使い方（プロジェクト直下で）:
    python scripts/foundation_backfill_batch.py                # dry-run（書き込みなし）
    python scripts/foundation_backfill_batch.py --live         # 実行（ゲート合格分のみUPDATE）
    python scripts/foundation_backfill_batch.py --limit 10     # 件数を絞る
結果: evaluation/FOUNDATION_BACKFILL_RESULTS.json
"""
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config_utils  # noqa: E402
import geo_db_client  # noqa: E402
import geo_documents_store as store  # noqa: E402

OUT_PATH = Path(__file__).resolve().parent.parent / "evaluation" / "FOUNDATION_BACKFILL_RESULTS.json"

# 公的資料のドメイン（政府・国際機関・公的研究機関）。サフィックス/完全一致のどちらかで判定する。
PUBLIC_DOMAIN_PATTERNS = [
    r"(^|\.)gov$", r"(^|\.)gov\.[a-z]{2}$", r"(^|\.)go\.jp$", r"(^|\.)mil$", r"(^|\.)gouv\.fr$",
    r"(^|\.)europa\.eu$", r"(^|\.)nato\.int$", r"(^|\.)un\.org$", r"(^|\.)imf\.org$",
    r"(^|\.)worldbank\.org$", r"(^|\.)iaea\.org$", r"(^|\.)wto\.org$", r"(^|\.)oecd\.org$",
    r"(^|\.)jetro\.go\.jp$", r"(^|\.)efi\.int$", r"(^|\.)bundesregierung\.de$", r"(^|\.)admin\.ch$",
    r"(^|\.)gc\.ca$", r"(^|\.)canada\.ca$", r"(^|\.)parlament\.[a-z]+$", r"(^|\.)senate\.gov$",
    r"(^|\.)congress\.gov$", r"(^|\.)federalreserve\.gov$", r"(^|\.)bis\.org$",
    r"(^|\.)iea\.org$", r"(^|\.)opec\.org$", r"(^|\.)icj-cij\.org$", r"(^|\.)icc-cpi\.int$",
]
PUBLIC_RX = [re.compile(p) for p in PUBLIC_DOMAIN_PATTERNS]
# 記事ではない（トップページ・統計系列・一覧）と判断するタイトルの先頭語
NON_ARTICLE_TITLE_RX = re.compile(r"^(IMF\s|World Bank\s|統計|Data\s|Dashboard)", re.IGNORECASE)
DELAY_SECONDS = 1.0
MIN_TEXT_CHARS = 400          # これ未満の本文は実質的な資料とみなさない（空ページ・エラーページ対策）
MAX_TEXT_CHARS = 400_000      # これを超えるものは記事ではなくデータ/リストのダンプとみなして対象外
MIN_PLAUSIBLE_DATE = "2015-01-01"
# 記事ではないタイトル（ドメイン名そのもの・目次・読み込み中・制裁リストのスナップショット等）
NON_ARTICLE_TITLE_EXTRA_RX = re.compile(
    r"^(Loading\.\.\.|Contents|OFAC SDN List snapshot|[\w\-]+(\.[\w\-]+)+)$|:::|（METI/|^[^\s]{1,12}新聞$", re.IGNORECASE)


_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}
_DATE_LIKE_PATTERNS = [
    (re.compile(r"(20\d{2})[-/.年]\s?(\d{1,2})[-/.月]\s?(\d{1,2})"), lambda m: (int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(r"\b(" + "|".join(_MONTHS) + r")[a-z]*\.? (\d{1,2}),? (20\d{2})\b", re.IGNORECASE),
     lambda m: (int(m[3]), _MONTHS[m[1].lower()], int(m[2]))),
    (re.compile(r"\b(\d{1,2}) (" + "|".join(_MONTHS) + r")[a-z]* (20\d{2})\b", re.IGNORECASE),
     lambda m: (int(m[3]), _MONTHS[m[2].lower()], int(m[1]))),
]


def latest_date_mentioned(text: str):
    """本文中の「日付の形をした記述」（2025年3月4日 / March 4, 2025 / 4 March 2025 / 2025-03-04）のうち最も新しいもの
    （今日以前）。将来予定を指す「2030年までに」のような年だけの記述は拾わない。無ければNone。"""
    from datetime import date as _date
    best, today = None, _date.today()
    for rx, conv in _DATE_LIKE_PATTERNS:
        for m in rx.finditer((text or "")[:60000]):
            try:
                d = _date(*conv(m))
            except ValueError:
                continue
            if d <= today and (best is None or d > best):
                best = d
    return best


def date_consistent_with_text(published_at, text, *, slack_days: int = 45) -> bool:
    """公開日が、本文に書かれた日付と矛盾しないか。本文が公開日より明らかに後の日付に言及している場合は、
    公開日がサイトのテンプレート日付等で誤って早い可能性が高いので False（as-of検証で未来の資料を
    過去として混ぜてしまう危険を避ける）。"""
    from datetime import date as _date
    if not published_at:
        return True
    try:
        pub = _date.fromisoformat(str(published_at)[:10])
    except ValueError:
        return False
    latest = latest_date_mentioned(text)
    return latest is None or (latest - pub).days <= slack_days


def trustworthy_date(value):
    """ページのメタ情報の日付が信用できるか。サイトのテンプレート日付（1月1日固定・極端に古い日付）は
    公開日として採用しない（誤った公開日はas-of検証を壊すため、空のままにする方が安全）。"""
    if not value:
        return None
    v = str(value)[:10]
    if v.endswith("-01-01") or v < MIN_PLAUSIBLE_DATE:
        return None
    return v


def is_public(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower().split(":")[0]
    return any(rx.search(host) for rx in PUBLIC_RX)


def is_landing_or_series(doc: dict, url: str) -> bool:
    path = urlparse(url).path or "/"
    if path in ("", "/") or path.rstrip("/").lower() in ("/index", "/index.html", "/index.htm"):
        return True
    title = (doc.get("title") or "").strip()
    return bool(NON_ARTICLE_TITLE_RX.search(title) or NON_ARTICLE_TITLE_EXTRA_RX.search(title))


def select_all(client, table, select):
    rows, off = [], 0
    while True:
        got = client.select(table, {"select": select, "limit": "500", "offset": str(off)})
        rows += got
        if len(got) < 500:
            return rows
        off += 500


def backfill_stub_with_extra_gates(client, doc: dict, *, proxies, verify, live: bool) -> dict:
    """store.backfill_stub_document と同じゲート（本文が実質ある／既存と重複しない）に、追加ゲート
    （最小/最大の本文長、信用できる公開日のみ採用）を加えた版。ゲート不合格の行は一切書き換えない。"""
    url = doc.get("canonical_url") or doc.get("url")
    fetched = store.fetch_url_for_backfill(url, proxies=proxies, verify=verify)
    if not fetched["ok"]:
        return {"status": "fetch_failed", "error": fetched["error"], "http_status": fetched["http_status"]}
    text = fetched["text"] or ""
    reasons = []
    if store._is_stub_text(doc.get("title"), text) or len(text) < MIN_TEXT_CHARS:
        reasons.append("text_too_short_or_stub")
    if len(text) > MAX_TEXT_CHARS:
        reasons.append("text_too_large_data_dump")
    dup = [r for r in client.select("geo_documents", {"select": "document_id",
                                                      "content_hash": f"eq.{fetched['content_hash']}", "limit": "2"})
           if r["document_id"] != doc["document_id"]]
    if dup:
        reasons.append("duplicate_of_existing_document")
    if reasons:
        return {"status": "rejected", "reasons": reasons, "after_len": len(text)}
    date = trustworthy_date(fetched["published_at"])
    result = {"status": "dry_run" if not live else "updated", "after_len": len(text), "published_at": date,
              "raw_date": fetched["published_at"]}
    if live:
        store.update_existing_document_from_fetch(
            client, doc["document_id"], extracted_text=text, content_hash=fetched["content_hash"],
            http_status=fetched["http_status"], published_at=date, fetch_method="foundation_backfill_batch_v1",
            pre_backfill_content_hash=doc.get("content_hash"),
            backfilled_at=datetime.now(timezone.utc).isoformat())
    return result


def fill_published_at_only(client, doc: dict, *, proxies, verify, live: bool) -> dict:
    """本文は触らず、公開日だけをページのメタ情報から補完する（既存値は上書きしない）。"""
    url = doc.get("canonical_url") or doc.get("url")
    fetched = store.fetch_url_for_backfill(url, proxies=proxies, verify=verify)
    if not fetched["ok"]:
        return {"status": "fetch_failed", "error": fetched["error"], "http_status": fetched["http_status"]}
    date = trustworthy_date(fetched["published_at"])
    if not date:
        return {"status": "date_untrusted_or_missing", "raw_date": fetched["published_at"]}
    result = {"status": "dry_run" if not live else "updated", "published_at": date}
    if live:
        prior = client.select("geo_documents", {"select": "response_headers,published_at",
                                                "document_id": f"eq.{doc['document_id']}", "limit": "1"})
        if prior and prior[0].get("published_at"):
            return {"status": "already_dated"}
        headers = dict((prior[0].get("response_headers") if prior else None) or {})
        headers["date_fill"] = {"method": "page_metadata", "filled_at": datetime.now(timezone.utc).isoformat(),
                                 "url": url}
        client.update("geo_documents", {"document_id": f"eq.{doc['document_id']}"},
                      {"published_at": date, "response_headers": headers})
    return result


def main():
    args = sys.argv[1:]
    live = "--live" in args
    limit = None
    if "--limit" in args:
        limit = int(args[args.index("--limit") + 1])

    cfg = config_utils.load_config()
    client = geo_db_client.SupabaseClient(cfg)
    proxies = config_utils.make_proxies(cfg)
    verify = cfg.get("ssl", {}).get("verify", True)

    docs = select_all(client, "geo_documents", "document_id,title,url,canonical_url,published_at,extracted_text,content_hash,is_mock,is_demo")
    plan_stub, plan_date, skipped = [], [], []
    for d in docs:
        url = d.get("canonical_url") or d.get("url")
        if d.get("is_mock") or d.get("is_demo"):
            continue
        is_stub = store._is_stub_text(d.get("title"), d.get("extracted_text"))
        needs_date = not d.get("published_at")
        if not (is_stub or needs_date):
            continue
        if not url:
            skipped.append({"document_id": d["document_id"], "title": (d.get("title") or "")[:60], "reason": "no_url"})
        elif not is_public(url):
            skipped.append({"document_id": d["document_id"], "title": (d.get("title") or "")[:60],
                            "reason": "non_public_domain", "domain": urlparse(url).netloc})
        elif is_landing_or_series(d, url):
            skipped.append({"document_id": d["document_id"], "title": (d.get("title") or "")[:60],
                            "reason": "landing_or_series_page", "domain": urlparse(url).netloc})
        elif is_stub:
            plan_stub.append(d)
        else:
            plan_date.append(d)

    if limit:
        plan_stub, plan_date = plan_stub[:limit], plan_date[:limit]
    print(f"mode={'LIVE' if live else 'DRY-RUN'} | stub_backfill={len(plan_stub)} date_fill={len(plan_date)} skipped={len(skipped)}")

    results = {"mode": "live" if live else "dry_run", "started_at": datetime.now(timezone.utc).isoformat(),
               "stub_backfill": [], "date_fill": [], "skipped": skipped}
    for i, d in enumerate(plan_stub, 1):
        try:
            r = backfill_stub_with_extra_gates(client, d, proxies=proxies, verify=verify, live=live)
            entry = {"document_id": d["document_id"], "title": (d.get("title") or "")[:60],
                     "domain": urlparse(d.get("canonical_url") or d.get("url")).netloc, **r}
        except Exception as e:  # noqa: BLE001 — 1件の失敗で全体を止めない
            entry = {"document_id": d["document_id"], "status": f"error:{type(e).__name__}", "error": str(e)[:160]}
        results["stub_backfill"].append(entry)
        print(f"  [stub {i}/{len(plan_stub)}] {entry['status']} {entry.get('domain', '')}")
        time.sleep(DELAY_SECONDS)
    for i, d in enumerate(plan_date, 1):
        try:
            r = fill_published_at_only(client, d, proxies=proxies, verify=verify, live=live)
            entry = {"document_id": d["document_id"], "title": (d.get("title") or "")[:60],
                     "domain": urlparse(d.get("canonical_url") or d.get("url")).netloc, **r}
        except Exception as e:  # noqa: BLE001
            entry = {"document_id": d["document_id"], "status": f"error:{type(e).__name__}", "error": str(e)[:160]}
        results["date_fill"].append(entry)
        print(f"  [date {i}/{len(plan_date)}] {entry['status']} {entry.get('domain', '')}")
        time.sleep(DELAY_SECONDS)

    results["finished_at"] = datetime.now(timezone.utc).isoformat()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = {}
    for key in ("stub_backfill", "date_fill"):
        c = {}
        for e in results[key]:
            c[e["status"]] = c.get(e["status"], 0) + 1
        summary[key] = c
    reasons = {}
    for e in skipped:
        reasons[e["reason"]] = reasons.get(e["reason"], 0) + 1
    print("summary:", json.dumps(summary), "| skipped reasons:", json.dumps(reasons))
    print("results:", OUT_PATH)


if __name__ == "__main__":
    main()
