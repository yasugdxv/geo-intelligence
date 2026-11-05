# -*- coding: utf-8 -*-
"""Foundation強化 段階1: 収集済みの公的シグナル(geo_external_signals)を、Expert AIが参照する文書(geo_documents)へ昇格する。

対象: provider が official / rss のシグナル（政府・国際機関の公式発表。GDELT・Web探索由来・民間報道は対象外）。
  - UKMTOのJSONアラート（記事ページを持たない）は除外。
  - 相対URL（防衛省RSS等）は、ソースのURLを基準に絶対URLへ直してから取得する。
  - 本文を取得できない／短すぎる(<400字)／大きすぎる(>400k字)／既存文書と重複するものは昇格しない。
  - 公開日はフィードの公開日（シグナルのpublished_at）を使う。ページのメタ日付は使わない。
昇格した文書は research_origin="signal_promotion:<provider>" で出所を残し、シグナル側は
knowledge_promotion_status="promoted" と raw_metadata.promoted_document_id を記録する。
既定は dry-run（DBを書き換えない）。--live でのみ書き込む。受け皿のテーマ(foundation_public_sources、
無効状態=画面のテーマ選択には出さない)は --live 時に無ければ1件作る。

使い方:  python scripts/foundation_signal_promotion.py [--live] [--limit N]
結果: evaluation/FOUNDATION_SIGNAL_PROMOTION_RESULTS.json
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config_utils  # noqa: E402
import geo_db_client  # noqa: E402
import geo_documents_store as store  # noqa: E402

OUT_PATH = Path(__file__).resolve().parent.parent / "evaluation" / "FOUNDATION_SIGNAL_PROMOTION_RESULTS.json"
THEME_CODE = "foundation_public_sources"
MIN_TEXT_CHARS, MAX_TEXT_CHARS = 400, 400_000
EXCLUDED_DOMAINS = {"sccd.royalnavy.mod.uk"}  # UKMTO: JSON API。記事ページが無い
DELAY_SECONDS = 1.0


def select_all(client, table, params):
    rows, off = [], 0
    while True:
        got = client.select(table, dict(params, limit="500", offset=str(off)))
        rows += got
        if len(got) < 500:
            return rows
        off += 500


def resolve_url(signal, source):
    url = (signal.get("url") or "").strip()
    if "news.un.org/feed/view/" in url:  # RSSの表示用URL(406になる)を、記事ページのURLへ直す
        url = url.replace("/feed/view/", "/")
    if url.startswith("http"):
        return url
    base = (source or {}).get("url") or ""
    return urljoin(base, url) if base and url else None


def ensure_theme(client, live):
    rows = client.select("geo_themes", {"select": "theme_id", "code": f"eq.{THEME_CODE}", "limit": "1"})
    if rows:
        return rows[0]["theme_id"]
    if not live:
        return "(dry-run: テーマは --live 時に作成)"
    inserted = client.insert("geo_themes", [{
        "code": THEME_CODE, "name_ja": "Foundation 公的資料（収集済みシグナルの昇格先）",
        "name_en": "Foundation public sources", "status": "無効",
        "description": "収集済みの公的シグナルから昇格した文書の受け皿。画面のテーマ選択には出さない（無効）。"}])
    return inserted[0]["theme_id"]


def main():
    args = sys.argv[1:]
    live = "--live" in args
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else None
    cfg = config_utils.load_config()
    client = geo_db_client.SupabaseClient(cfg)
    proxies, verify = config_utils.make_proxies(cfg), cfg.get("ssl", {}).get("verify", True)

    signals = select_all(client, "geo_external_signals", {
        "select": "external_signal_id,provider,title,url,source_domain,source_id,published_at,source_language,raw_metadata,knowledge_promotion_status,is_demo",
        "provider": "in.(official,rss)", "knowledge_promotion_status": "eq.not_promoted"})
    sources = {s["source_id"]: s for s in select_all(client, "geo_source_registry", {"select": "source_id,name,url"})}
    existing = select_all(client, "geo_documents", {"select": "canonical_url,url,content_hash"})
    known_urls = {u for d in existing for u in (d.get("canonical_url"), d.get("url")) if u}
    known_hashes = {d["content_hash"] for d in existing if d.get("content_hash")}
    theme_id = ensure_theme(client, live)

    todo, results = [], {"mode": "live" if live else "dry_run", "started_at": datetime.now(timezone.utc).isoformat(),
                         "theme_id": theme_id, "items": []}
    for s in signals:
        if s.get("is_demo"):
            continue
        if (s.get("source_domain") or "") in EXCLUDED_DOMAINS:
            results["items"].append({"signal": s["external_signal_id"], "status": "excluded", "reason": "json_alert_no_article_page"})
            continue
        url = resolve_url(s, sources.get(s.get("source_id")))
        if not url:
            results["items"].append({"signal": s["external_signal_id"], "status": "excluded", "reason": "no_resolvable_url"})
        elif url in known_urls:
            results["items"].append({"signal": s["external_signal_id"], "status": "excluded", "reason": "url_already_in_documents"})
        else:
            todo.append((s, url))
    if limit:
        todo = todo[:limit]
    print(f"mode={'LIVE' if live else 'DRY-RUN'} | candidates={len(todo)} excluded={len(results['items'])}", flush=True)

    for i, (s, url) in enumerate(todo, 1):
        item = {"signal": s["external_signal_id"], "title": (s.get("title") or "")[:60], "url": url,
                "domain": urlparse(url).netloc}
        try:
            f = store.fetch_url_for_backfill(url, proxies=proxies, verify=verify)
            text = (f.get("text") or "") if f["ok"] else ""
            if not f["ok"]:
                item.update(status="fetch_failed", reason=str(f.get("http_status") or f.get("error"))[:80])
            elif len(text) < MIN_TEXT_CHARS or store._is_stub_text(s.get("title"), text):
                item.update(status="rejected", reason="text_too_short", length=len(text))
            elif len(text) > MAX_TEXT_CHARS:
                item.update(status="rejected", reason="text_too_large", length=len(text))
            elif f["content_hash"] in known_hashes:
                item.update(status="rejected", reason="duplicate_content_hash")
            else:
                item.update(status="dry_run" if not live else "promoted", length=len(text),
                            published_at=s.get("published_at"))
                if live:
                    row = {"theme_id": theme_id, "source_id": s.get("source_id"), "title": s.get("title") or f.get("title"),
                           "url": url, "canonical_url": url, "published_at": s.get("published_at"),
                           "language": s.get("source_language"), "content_hash": f["content_hash"],
                           "mime_type": "text/html", "extracted_text": text, "fetch_status": "成功",
                           "http_status": f.get("http_status"), "parser_version": store.PARSER_VERSION,
                           "is_mock": False, "research_origin": f"signal_promotion:{s['provider']}"}
                    doc = client.insert("geo_documents", [row])[0]
                    meta = dict(s.get("raw_metadata") or {})
                    meta["promoted_document_id"] = doc["document_id"]
                    meta["promoted_at"] = datetime.now(timezone.utc).isoformat()
                    client.update("geo_external_signals", {"external_signal_id": f"eq.{s['external_signal_id']}"},
                                  {"knowledge_promotion_status": "promoted", "raw_metadata": meta})
                    item["document_id"] = doc["document_id"]
                    known_hashes.add(f["content_hash"])
                    known_urls.add(url)
        except Exception as e:  # noqa: BLE001 — 1件の失敗で全体を止めない
            item.update(status=f"error:{type(e).__name__}", reason=str(e)[:100])
        results["items"].append(item)
        print(f"  [{i}/{len(todo)}] {item['status']} {item['domain']} {item.get('reason', '')}", flush=True)
        time.sleep(DELAY_SECONDS)

    results["finished_at"] = datetime.now(timezone.utc).isoformat()
    OUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = {}
    for it in results["items"]:
        key = it["status"] + (":" + it["reason"] if it.get("reason") and it["status"] in ("excluded", "rejected") else "")
        summary[key] = summary.get(key, 0) + 1
    print("summary:", json.dumps(summary, ensure_ascii=False), "\nresults:", OUT_PATH, flush=True)


if __name__ == "__main__":
    main()
