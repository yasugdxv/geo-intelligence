"""GEO_DIRECT_COLLECTION_V1: 汎用Geo RSS Collector（政府・国際機関のRSS/Atomを直接取得）。

Source（防衛省・IAEA・WTO等）ごとにConnectorを増やさず、config/geo_direct_sources.yamlの設定で
取得口と言語・Geo向けkeyword方針を切り替える。Sourceの承認ゲート（automation_allowed等）は
Geo Source Registryが正（base.check_automation_allowed）。Sustainability/ERMには依存しない。

使い方: python geo_connector_cli.py rss [--source mod_japan] [--is-demo]
"""
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import geo_connector_base as base
import geo_direct_collection_common as common
import geo_external_signal_service as signal_service

CONNECTOR_TYPE = "rss"
CONNECTOR_VERSION = "geo-rss-1.0"
PARSER_VERSION = "geo-rss-parser-1.0"

_ATOM = "{http://www.w3.org/2005/Atom}"
_DC = "{http://purl.org/dc/elements/1.1/}"
_CONTENT = "{http://purl.org/rss/1.0/modules/content/}"


def _text(el, *names) -> str | None:
    for n in names:
        found = el.find(n)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
    return None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)       # RFC822（RSS 2.0）
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))   # ISO8601（Atom / RSS 1.0 dc:date）
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_feed(raw: bytes) -> list[dict]:
    """RSS 2.0 / RSS 1.0(RDF) / Atom を共通形（guid, title, link, summary, published_at）へ。"""
    root = ET.fromstring(raw)
    items = []
    entries = root.findall(".//item") or root.findall(f".//{_ATOM}entry") or root.findall(
        ".//{http://purl.org/rss/1.0/}item")
    for el in entries:
        link = _text(el, "link", "{http://purl.org/rss/1.0/}link")
        if not link:                                    # Atomはlinkが属性
            a = el.find(f"{_ATOM}link")
            link = a.get("href") if a is not None else None
        items.append({
            "guid": _text(el, "guid", f"{_ATOM}id"),
            "title": common.clean_text(_text(el, "title", f"{_ATOM}title", "{http://purl.org/rss/1.0/}title"), 500),
            "link": (link or "").strip() or None,
            "summary": common.clean_text(_text(el, "description", f"{_ATOM}summary", f"{_CONTENT}encoded",
                                               f"{_ATOM}content", "{http://purl.org/rss/1.0/}description")),
            "published_at": _parse_date(_text(el, "pubDate", f"{_ATOM}published", f"{_ATOM}updated", f"{_DC}date")),
        })
    return items


def run_rss_collection(client, *, source_key: str, requested_by: str = "system", is_demo: bool = False,
                       config: dict = None, run_type: str = "manual") -> dict:
    """1つのRSS Sourceを取得して geo_external_signals へ保存する（再実行しても重複しない）。"""
    cfg = (config or common.load_direct_config()).get("rss_sources", {}).get(source_key)
    if not cfg:
        raise common.SourceNotReadyError(f"rss_sources に {source_key} の設定がありません")
    source_row = common.resolve_source(client, cfg["registry_domain"])
    base.check_automation_allowed(source_row)               # automation_allowed / connector_enabled のゲート

    run = base.create_ingestion_run(client, source_id=source_row["source_id"], connector_type=CONNECTOR_TYPE,
                                     run_type=run_type if run_type in ("manual", "cli", "scheduled_external") else "manual",
                                     requested_by=requested_by,
                                     request_parameters={"source_key": source_key, "feed_url": cfg["feed_url"]},
                                     is_demo=is_demo)
    counts = {"fetched_count": 0, "inserted_count": 0, "updated_count": 0, "skipped_count": 0, "error_count": 0}
    try:
        resp = common.http_get(cfg["feed_url"], accept="application/rss+xml, application/xml, text/xml, */*")
        snapshot, _ = base.find_or_save_raw_snapshot(
            client, source_id=source_row["source_id"], raw_bytes=resp.content, request_url=cfg["feed_url"],
            response_status=resp.status_code, content_type=resp.headers.get("Content-Type"), ext=".xml",
            connector_version=CONNECTOR_VERSION, parser_version=PARSER_VERSION, is_demo=is_demo)
        base.link_run_to_snapshot(client, ingestion_run_id=run["ingestion_run_id"],
                                   raw_snapshot_id=snapshot["raw_snapshot_id"])
        items = parse_feed(resp.content)[: int(cfg.get("max_items", 100))]
        counts["fetched_count"] = len(items)

        normalized, seen = [], set()
        for it in items:
            if not it["title"] or not it["link"]:
                counts["skipped_count"] += 1
                continue
            if not common.passes_policy(it["title"], it["summary"], cfg.get("policy")):
                counts["skipped_count"] += 1          # Geo向け方針で対象外（欠損ではない）
                continue
            sig = common.build_signal(
                source_row=source_row, provider="rss", title=it["title"], url=it["link"], summary=it["summary"],
                published_at=it["published_at"], language=cfg.get("language"), collector=CONNECTOR_TYPE,
                connector_version=CONNECTOR_VERSION, endpoint=cfg["feed_url"],
                ingestion_run_id=run["ingestion_run_id"], provider_record_id=it["guid"],
                extra_metadata={"source_key": source_key})
            if sig["dedup_key"] in seen:
                counts["skipped_count"] += 1
                continue
            seen.add(sig["dedup_key"])
            normalized.append(sig)

        result = signal_service.bulk_upsert_signals(client, normalized, provider="rss", is_demo=is_demo)
        counts["inserted_count"], counts["updated_count"] = result["inserted"], result["updated"]
        latest = max((n["published_at"] for n in normalized if n["published_at"]), default=None)
        common.finish_run(client, run, status="completed", counts=counts, error_summary=None,
                          connector_version=CONNECTOR_VERSION, parser_version=PARSER_VERSION)
        common.record_source_outcome(client, source_row, run, ok=True, is_demo=is_demo)
        return {"source_key": source_key, "source_id": source_row["source_id"], "status": "completed",
                "latest_published_at": latest, **counts, "ingestion_run_id": run["ingestion_run_id"]}
    except Exception as e:  # noqa: BLE001 — Runを必ず閉じる（running放置を作らない）
        counts["error_count"] += 1
        common.finish_run(client, run, status="failed", counts=counts, error_summary=f"{type(e).__name__}: {e}"[:500],
                          connector_version=CONNECTOR_VERSION, parser_version=PARSER_VERSION)
        common.record_source_outcome(client, source_row, run, ok=False, is_demo=is_demo)
        raise


def run_all_rss_sources(client, *, requested_by: str = "system", is_demo: bool = False,
                        only: list | None = None, config: dict = None) -> dict:
    """設定済みの全RSS Sourceを順に実行する。1件の失敗で他を止めない。承認ゲート未通過はnot_readyとして報告。"""
    cfg_all = config or common.load_direct_config()
    out = {}
    for key in cfg_all.get("rss_sources", {}):
        if only and key not in only:
            continue
        try:
            out[key] = run_rss_collection(client, source_key=key, requested_by=requested_by, is_demo=is_demo,
                                          config=cfg_all)
        except (base.ConnectorDisabledError, common.SourceNotReadyError) as e:
            out[key] = {"status": "not_ready", "reason": str(e)}
        except Exception as e:  # noqa: BLE001
            out[key] = {"status": "failed", "error": f"{type(e).__name__}: {e}"[:300]}
    return out
