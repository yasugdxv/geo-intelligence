# -*- coding: utf-8 -*-
"""Foundation 段階3: 公開日が空の文書に、「安全側の日付（上限日）」を補う。

as-of検証（過去の事例を、その時点より前の資料だけで評価する）では、公開日が空の文書は使えない。記事ページに日付の
メタ情報が無くても、URLやタイトルから公開時期が分かるものは多い（/2025/10/..、p20260924_01.pdf、令和7年版）。
ここでは「その日より後ではない」と言える日付（上限日）だけを補う:
  - URLに年月日がある → その日（精度: day）
  - URLに年月がある → その月の末日（精度: month）
  - URLまたはタイトルに年だけがある（過去の年）→ その年の12/31（精度: year）。基準日より後の資料を誤って
    過去として含めないよう、最も遅い可能性の日付にする。今年の年だけの場合は補わない（不確かなため）。
採用条件: 本文に書かれた最新の日付が、その上限日より45日を超えて後でないこと（矛盾すれば補わない）。
補った日付は response_headers.date_audit に {kind, precision, basis, set_at} を残す（published_atだけを見る機能と
区別できるように）。年も読み取れないものは空のまま（as-of検証では使わない）。
既定は dry-run。--live で更新（published_atが空のものだけを対象にし、既存の日付は一切上書きしない）。

使い方: python scripts/foundation_date_upper_bound.py [--live]
結果: evaluation/FOUNDATION_DATE_UPPER_BOUND_RESULTS.json
"""
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import config_utils  # noqa: E402
import foundation_backfill_batch as fb  # noqa: E402
import geo_db_client  # noqa: E402

OUT = ROOT / "evaluation" / "FOUNDATION_DATE_UPPER_BOUND_RESULTS.json"
MIN_YEAR = 2000
_MON = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def month_end(y, m):
    nxt = date(y + (m == 12), (m % 12) + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def derive(url: str, title: str):
    """(date, precision, basis) または None。"""
    u = url or ""
    m = re.search(r"(?<![0-9])(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?![0-9])", u)      # 20260924
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3])), "day", "url"
        except ValueError:
            pass
    m = re.search(r"/(20\d{2})[/-](0[1-9]|1[0-2])[/-](0[1-9]|[12]\d|3[01])(?:/|$|[^0-9])", u)   # /2025/10/31/
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3])), "day", "url"
        except ValueError:
            pass
    low = u.lower()
    m = re.search(r"(?<![0-9])(\d{1,2})[-_ ](" + "|".join(_MON) + r")[a-z]*[-_ ](20\d{2})(?![0-9])", low)   # 8-jun-2026
    if m:
        try:
            return date(int(m[3]), _MON[m[2]], int(m[1])), "day", "url_slug"
        except ValueError:
            pass
    m = re.search(r"(" + "|".join(_MON) + r")[a-z]*[-_ ](\d{1,2})[-_ ,]+(20\d{2})(?![0-9])", low)       # june-8-2026
    if m:
        try:
            return date(int(m[3]), _MON[m[1]], int(m[2])), "day", "url_slug"
        except ValueError:
            pass
    m = re.search(r"/(20\d{2})[/-](0[1-9]|1[0-2])(?:/|$|[^0-9])", u)                             # /2025/10/
    if m:
        return month_end(int(m[1]), int(m[2])), "month", "url"
    m = re.search(r"/(20\d{2})(?:/|-|_|\.|$)", u)                                                # /2025/
    if m:
        return date(int(m[1]), 12, 31), "year", "url"
    t = title or ""
    full = fb.latest_date_mentioned(t)                    # タイトルに完全な日付（June 8, 2026 / 2026年6月8日）がある
    if full:
        return full, "day", "title"
    m = re.search(r"令和\s?(\d{1,2})年", t)
    if m:
        return date(2018 + int(m[1]), 12, 31), "year", "title"
    m = re.search(r"(?<![0-9])(20\d{2})(?![0-9])", t)
    if m:
        return date(int(m[1]), 12, 31), "year", "title"
    return None


def main():
    live = "--live" in sys.argv
    client = geo_db_client.SupabaseClient(config_utils.load_config())
    docs = fb.select_all(client, "geo_documents", "document_id,title,url,canonical_url,published_at,extracted_text,response_headers,is_mock,is_demo")
    today = date.today()
    results = {"mode": "live" if live else "dry_run", "items": []}
    counts = {}
    for d in docs:
        if d.get("published_at") or d.get("is_mock") or d.get("is_demo"):
            continue
        got = derive(d.get("canonical_url") or d.get("url") or "", d.get("title") or "")
        item = {"document_id": d["document_id"], "title": (d.get("title") or "")[:50], "url": (d.get("url") or "")[:80]}
        if not got:
            item["status"] = "no_date_clue"
        else:
            dt, precision, basis = got
            if dt.year < MIN_YEAR:
                item["status"] = "too_old_year"
            elif precision == "year" and dt.year >= today.year:
                item["status"] = "current_year_only_not_set"        # 今年の年だけは不確かなので補わない
            elif dt > today:
                item["status"] = "future_date_not_set"
            elif not fb.date_consistent_with_text(dt.isoformat(), d.get("extracted_text") or ""):
                item["status"] = "inconsistent_with_text"
            else:
                item.update(status="dry_run" if not live else "set", date=dt.isoformat(), precision=precision, basis=basis)
                if live:
                    headers = dict(d.get("response_headers") or {})
                    headers["date_audit"] = {"kind": "upper_bound" if precision != "day" else "exact_from_url",
                                             "precision": precision, "basis": basis,
                                             "set_at": datetime.now(timezone.utc).isoformat()}
                    client.update("geo_documents", {"document_id": f"eq.{d['document_id']}", "published_at": "is.null"},
                                  {"published_at": dt.isoformat(), "response_headers": headers})
        counts[item["status"]] = counts.get(item["status"], 0) + 1
        results["items"].append(item)
    results["summary"] = counts
    prec = {}
    for it in results["items"]:
        if it.get("precision"):
            prec[it["precision"]] = prec.get(it["precision"], 0) + 1
    results["precision"] = prec
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print("mode:", results["mode"], "| summary:", json.dumps(counts, ensure_ascii=False), "| precision:", json.dumps(prec))
    print("results:", OUT)


if __name__ == "__main__":
    main()
