"""GEO_DIRECT_COLLECTION（V1_1→V1_2）: 公開HTML一覧の決定論Collector（HTML list）。

RSSが無い公式・企業サイトの「安定した一覧ページ」を、Sourceごとの決定論パーサ（parser名で選択）で取得する。AI・推測は使わない。

Collection Endpointの考え方: 1つのSource（Registry）に複数の取得口がある。例: 既存の「日本 防衛省（報道発表）」Sourceに対し、
Endpoint A=報道資料RSS、Endpoint B=統合幕僚監部のHTML一覧（別Sourceにしない）。Provenanceに Canonical Source / Origin Section /
Collection Method を残す。

V1_2で追加:
- HTML Schema Guard: 一覧のマーカー（期待するselector）が無い=schema_changed、マーカーはあるのに0件=parse_failed。いずれも**失敗**
  （「0件で成功」にしない）。
- Baseline（baseline_on_first_run）: 初回の既存記事は`baseline=true`で取り込み、Monitoring Alertの対象にしない。
- 更新検知（content hash → new / updated / unchanged、version+1、previous_hashes）。同じURLの記事が更新される場合（例: Maerskの
  Middle East Operational Update）に対応。
- 詳細ページ取得（detail）: 決定論filterを通った新規・更新記事だけ、meta descriptionを要約として取得（1回あたり上限・待機あり）。

使い方: python geo_connector_cli.py html-list [--source mod_joint_staff|maersk_advisories]
"""
import hashlib
import html as html_lib
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import geo_connector_base as base
import geo_direct_collection_common as common
import geo_external_signal_service as signal_service

CONNECTOR_TYPE = "html_list"
CONNECTOR_VERSION = "geo-html-list-1.1"
PARSER_VERSION = "geo-html-list-parser-1.1"
JST = timezone(timedelta(hours=9))
UTC = timezone.utc


class HtmlListError(RuntimeError):
    kind = "unexpected"


class SchemaChanged(HtmlListError):
    """期待する一覧のselector/マーカーが無い（HTML構造が変わった）。"""
    kind = "schema_changed"


class ParseFailed(HtmlListError):
    """マーカーはあるのに記事を1件も解析できない。"""
    kind = "parse_failed"


# ---------------------------------------------------------------------------
# パーサ（決定論）
# ---------------------------------------------------------------------------
_MOD_JS_ITEM = re.compile(
    r'<a href="(?P<href>[^"]+)"[^>]*>\s*<time datetime="(?P<date>\d{4}-\d{2}-\d{2})">[^<]*</time>\s*'
    r'(?:<span data-tag="(?P<tag>[^"]*)">[^<]*</span>\s*)?<h5>(?P<title>.*?)</h5>', re.S)


def parse_mod_js_list(page: str, base_url: str) -> list[dict]:
    """統合幕僚監部（mod.go.jp/js/）の一覧: <a href><time datetime><span data-tag><h5>title</h5></a>。"""
    if "<time datetime=" not in page:
        raise SchemaChanged("期待するマーカー（<time datetime=）がありません")
    items = []
    for m in _MOD_JS_ITEM.finditer(page):
        title = common.clean_text(html_lib.unescape(m.group("title")), 500)
        if not title:
            continue
        try:
            published = datetime.strptime(m.group("date"), "%Y-%m-%d").replace(tzinfo=JST)   # 日付のみ公開。JSTの0時として扱う
        except ValueError:
            published = None
        items.append({"title": title, "url": urljoin(base_url, html_lib.unescape(m.group("href"))), "published_at": published,
                      "tag": m.group("tag") or None, "summary": None})
    if not items:
        raise ParseFailed("一覧マーカーはあるが記事を解析できませんでした")
    return items


_MAERSK_ITEM = re.compile(
    r'<a href="(?P<href>/news/articles/[^"]+)"[^>]*class="[^"]*p-section__news__teaser__title[^"]*"[^>]*>\s*'
    r'<span[^>]*teaser__title__line[^>]*>\s*(?P<title>.*?)\s*</span>\s*</a>\s*'
    r'<div[^>]*class="[^"]*p-section__news__teaser__timestamp[^"]*"[^>]*>\s*(?P<date>[^<]*?)\s*</div>', re.S)


def parse_maersk_news_list(page: str, base_url: str) -> list[dict]:
    """Maersk News & Advisories（/news/category/advisories）の一覧。表示日付（例: '2 Oct 2026'）をpublished_atにする
    （URLの日付は初回公開日で、同じURLの記事が更新されると表示日付と食い違う）。"""
    if "p-section__news__teaser" not in page:
        raise SchemaChanged("期待するマーカー（p-section__news__teaser）がありません")
    items = []
    for m in _MAERSK_ITEM.finditer(page):
        title = common.clean_text(html_lib.unescape(m.group("title")), 500)
        if not title:
            continue
        published = None
        for fmt in ("%d %b %Y", "%d %B %Y"):
            try:
                published = datetime.strptime(m.group("date").strip(), fmt).replace(tzinfo=UTC)
                break
            except ValueError:
                continue
        url = urljoin(base_url, html_lib.unescape(m.group("href")))
        if published is None:                                   # 表示日付が読めない場合のみURLの日付（推測ではなく記載値）
            d = re.search(r"/news/articles/(\d{4})/(\d{2})/(\d{2})/", url)
            published = datetime(int(d.group(1)), int(d.group(2)), int(d.group(3)), tzinfo=UTC) if d else None
        items.append({"title": title, "url": url, "published_at": published, "tag": "advisory", "summary": None,
                      "displayed_date": m.group("date").strip()})
    if not items:
        raise ParseFailed("一覧マーカーはあるが記事を解析できませんでした")
    return items


PARSERS = {"mod_js_list": parse_mod_js_list, "maersk_news_list": parse_maersk_news_list}

_META_DESC = re.compile(r'<meta[^>]+name="description"[^>]+content="([^"]*)"', re.I)
_OG_DESC = re.compile(r'<meta[^>]+property="og:description"[^>]+content="([^"]*)"', re.I)


def parse_detail_summary(page: str) -> str | None:
    """詳細ページのmeta description（無ければog:description）。無ければNone（推測しない）。"""
    for pat in (_META_DESC, _OG_DESC):
        m = pat.search(page)
        if m and m.group(1).strip():
            return common.clean_text(html_lib.unescape(m.group(1)), 800)
    return None


def _item_hash(it: dict) -> str:
    """一覧上の内容のhash（タイトル・表示日付・URL）。詳細ページは毎回取らないため、詳細本文はhashに含めない。"""
    return hashlib.sha256(f"{it['title']}|{it.get('displayed_date') or (it['published_at'].isoformat() if it['published_at'] else '')}|{it['url']}".encode("utf-8")).hexdigest()


def _existing(client, source_id: str, keys: list) -> dict:
    rows = signal_service._select_in_chunked(
        client, "geo_external_signals", select="external_signal_id,dedup_key,raw_metadata", in_column="dedup_key", in_values=keys,
        extra_params={"provider": "eq.official", "source_id": f"eq.{source_id}"})
    return {r["dedup_key"]: r for r in rows}


def run_html_list_collection(client, *, source_key: str, requested_by: str = "system", is_demo: bool = False,
                             config: dict = None, run_type: str = "manual", fetch=None, fetch_detail=None,
                             sleep=time.sleep) -> dict:
    cfg = (config or common.load_direct_config()).get("html_list_sources", {}).get(source_key)
    if not cfg:
        raise common.SourceNotReadyError(f"html_list_sources に {source_key} の設定がありません")
    source_row = common.resolve_source(client, cfg["registry_domain"])     # Canonical Source（既存。新規作成しない）
    base.check_automation_allowed(source_row)
    run = base.create_ingestion_run(
        client, source_id=source_row["source_id"], connector_type=CONNECTOR_TYPE,
        run_type=run_type if run_type in ("manual", "cli", "scheduled_external") else "manual", requested_by=requested_by,
        request_parameters={"source_key": source_key, "page_url": cfg["page_url"], "endpoint_name": cfg["endpoint_name"],
                            "origin_section": cfg["origin_section"]}, is_demo=is_demo)
    counts = {"fetched_count": 0, "inserted_count": 0, "updated_count": 0, "duplicate_count": 0, "skipped_count": 0, "error_count": 0}
    try:
        resp = fetch(cfg["page_url"]) if fetch else common.http_get(cfg["page_url"], accept="text/html, */*")
        snapshot, _ = base.find_or_save_raw_snapshot(
            client, source_id=source_row["source_id"], raw_bytes=resp.content, request_url=cfg["page_url"],
            response_status=resp.status_code, content_type=resp.headers.get("Content-Type"), ext=".html",
            connector_version=CONNECTOR_VERSION, parser_version=PARSER_VERSION, is_demo=is_demo)
        base.link_run_to_snapshot(client, ingestion_run_id=run["ingestion_run_id"], raw_snapshot_id=snapshot["raw_snapshot_id"])
        payload_hash = hashlib.sha256(resp.content).hexdigest()
        items = PARSERS[cfg["parser"]](resp.content.decode("utf-8", "replace"), cfg.get("base_url") or cfg["page_url"])
        items = items[: int(cfg.get("max_items", 100))]
        counts["fetched_count"] = len(items)

        selected = []
        for it in items:
            if not common.passes_policy(it["title"], it["summary"], cfg.get("policy")):
                counts["skipped_count"] += 1          # Geo方針（決定論filter）で対象外
                continue
            selected.append(it)

        # 既存Signalとの照合（natural key=正規化した元URL）。dedup_keyは build_signal と同じ算出
        pre = {}
        for it in selected:
            sig = common.build_signal(source_row=source_row, provider="official", title=it["title"], url=it["url"], summary=None,
                                      published_at=it["published_at"], language=cfg.get("language"), collector=CONNECTOR_TYPE,
                                      connector_version=CONNECTOR_VERSION, endpoint=cfg["page_url"], ingestion_run_id=run["ingestion_run_id"])
            pre[sig["dedup_key"]] = it
        existing = _existing(client, source_row["source_id"], list(pre)) if pre else {}
        # Baseline: このEndpoint（origin_section）のSignalが1件も無い初回は、既存記事をbaselineとして取り込む（Alert対象にしない）
        baseline = False
        if cfg.get("baseline_on_first_run") and selected:
            prior = client.select("geo_external_signals", {
                "select": "external_signal_id", "provider": "eq.official", "source_id": f"eq.{source_row['source_id']}",
                "raw_metadata->>origin_section": f"eq.{cfg['origin_section']}", "is_demo": "eq.true" if is_demo else "eq.false", "limit": "1"})
            baseline = not prior

        dcfg = cfg.get("detail") or {}
        detail_budget = int(dcfg.get("max_per_run", 0)) if dcfg.get("enabled") else 0
        to_insert, updated, unchanged_ids, now_iso = [], [], [], datetime.now(timezone.utc).isoformat()
        detail_fetched = detail_failed = 0
        for key, it in pre.items():
            ex = existing.get(key)
            chash = _item_hash(it)
            old_meta = (ex or {}).get("raw_metadata") or {}
            if ex is not None and (old_meta.get("content_hash") in (None, chash)):
                unchanged_ids.append(ex["external_signal_id"])
                if old_meta.get("content_hash") is None:          # 旧版で取り込んだ行にhashを補う（更新扱いにしない）
                    client.update("geo_external_signals", {"external_signal_id": f"eq.{ex['external_signal_id']}"},
                                  {"raw_metadata": {**old_meta, "content_hash": chash, "version": old_meta.get("version", 1)}})
                continue
            page = ""
            if detail_budget > 0:                                   # 決定論filterを通った新規・更新記事だけ詳細（meta description）を取得
                sleep(float(dcfg.get("delay_seconds", 1.0)))
                try:
                    raw = fetch_detail(it["url"]) if fetch_detail else common.http_get(it["url"], accept="text/html, */*").content
                    page = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
                except Exception:  # noqa: BLE001 — 詳細が取れなくても一覧の情報（タイトル・日付・URL）は保存する
                    detail_failed += 1
                detail_budget -= 1
                detail_fetched += 1
            summary = parse_detail_summary(page) if page else it["summary"]
            sig = common.build_signal(
                source_row=source_row, provider="official", title=it["title"], url=it["url"], summary=summary,
                published_at=it["published_at"], language=cfg.get("language"), collector=CONNECTOR_TYPE,
                connector_version=CONNECTOR_VERSION, endpoint=cfg["page_url"], ingestion_run_id=run["ingestion_run_id"],
                extra_metadata={"source_key": source_key, "canonical_source": source_row.get("name"),
                                "canonical_source_id": source_row["source_id"], "origin_section": cfg["origin_section"],
                                "endpoint_name": cfg["endpoint_name"], "collection_method": "html_list", "item_tag": it["tag"],
                                "page_url": cfg["page_url"], "original_url": it["url"], "displayed_date": it.get("displayed_date"),
                                "content_hash": chash, "version": 1, "previous_hashes": [], "baseline": bool(baseline),
                                "payload_hash": payload_hash, "raw_snapshot_id": snapshot["raw_snapshot_id"],
                                "summary_source": "detail_meta_description" if summary else None})
            if cfg.get("source_role"):
                sig["source_role"] = cfg["source_role"]
            if ex is None:
                to_insert.append({**sig, "is_demo": is_demo})
            else:                                                  # 同じURLの記事が更新された
                meta = {**sig["raw_metadata"], "version": int(old_meta.get("version", 1)) + 1,
                        "previous_hashes": (old_meta.get("previous_hashes") or []) + [old_meta.get("content_hash")],
                        "updated_detected_at": now_iso, "baseline": False, "first_collected_at": old_meta.get("collected_at")}
                client.update("geo_external_signals", {"external_signal_id": f"eq.{ex['external_signal_id']}"},
                              {"title": sig["title"], "summary": sig["summary"], "published_at": sig["published_at"], "raw_metadata": meta,
                               "last_observed_at": now_iso})
                updated.append(key)
        if to_insert:
            client.insert("geo_external_signals", to_insert, prefer="return=minimal")
        if unchanged_ids:
            signal_service._update_in_chunked(client, "geo_external_signals", in_column="external_signal_id", in_values=unchanged_ids,
                                              patch={"last_observed_at": now_iso})
        counts.update(inserted_count=len(to_insert), updated_count=len(updated), duplicate_count=len(unchanged_ids))
        latest = max((s["published_at"] for s in to_insert if s["published_at"]), default=None)
        common.finish_run(client, run, status="completed", counts=counts, error_summary=None,
                          connector_version=CONNECTOR_VERSION, parser_version=PARSER_VERSION)
        common.record_source_outcome(client, source_row, run, ok=True, is_demo=is_demo)
        return {"source_key": source_key, "source_id": source_row["source_id"], "origin_section": cfg["origin_section"],
                "status": "completed", "mode": "baseline" if baseline else "diff", "new": len(to_insert), "updated": len(updated),
                "unchanged": len(unchanged_ids), "detail_fetched": detail_fetched, "detail_failed": detail_failed,
                "latest_published_at": latest, "payload_hash": payload_hash, **counts, "ingestion_run_id": run["ingestion_run_id"]}
    except Exception as e:  # noqa: BLE001 — Runを必ず閉じ、失敗の種類を残す
        kind = getattr(e, "kind", "unexpected")
        counts["error_count"] += 1
        common.finish_run(client, run, status="failed", counts=counts, error_summary=f"[{kind}] {type(e).__name__}: {e}"[:500],
                          connector_version=CONNECTOR_VERSION, parser_version=PARSER_VERSION)
        common.record_source_outcome(client, source_row, run, ok=False, is_demo=is_demo)
        e.failure_kind = kind
        raise


def run_all_html_list_sources(client, *, requested_by: str = "system", is_demo: bool = False, only: list | None = None,
                              config: dict = None) -> dict:
    cfg_all = config or common.load_direct_config()
    out = {}
    for key in cfg_all.get("html_list_sources", {}):
        if only and key not in only:
            continue
        try:
            out[key] = run_html_list_collection(client, source_key=key, requested_by=requested_by, is_demo=is_demo, config=cfg_all)
        except (base.ConnectorDisabledError, common.SourceNotReadyError) as e:
            out[key] = {"status": "not_ready", "reason": str(e)}
        except Exception as e:  # noqa: BLE001
            out[key] = {"status": "failed", "error": f"[{getattr(e, 'kind', 'unexpected')}] {type(e).__name__}: {e}"[:300]}
    return out
