"""GEO_DIRECT_COLLECTION_V1: 構造化された制裁リスト（国連安保理統合リスト・英国制裁リスト）のDirect Collector。

制裁リストは「記事」ではないため、既存の geo_sanctions_entries（現在の代表行）と
geo_sanctions_entry_versions（added/amended/delisted/relisted、上書きしない）にそのまま保存する。
- 初回（Entryが0件）= Baseline: 全件を登録するが Signal / Alert は作らない（数千件のAlert防止）。
- 2回目以降 = Diff: 追加・変更・削除だけを検出し、要約Signalを1件だけ作る（XML全文・全件はSignalにしない）。
- リストのXMLが前回と同一（Raw Snapshotのcontent_hash一致）なら何もしない。
OFAC既存Connectorには触れない（OFACは遅い1件ずつ処理のため、本Connectorは一括処理）。

使い方: python geo_connector_cli.py sanctions [--list un_consolidated] [--list uk_fcdo]
"""
import hashlib
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import geo_audit
import geo_connector_base as base
import geo_direct_collection_common as common
import geo_external_signal_service as signal_service

CONNECTOR_TYPE = "sanctions_xml"
CONNECTOR_VERSION = "geo-sanctions-xml-1.0"
PARSER_VERSION = "geo-sanctions-xml-parser-1.0"
CHUNK = 500
EXAMPLE_LIMIT = 30           # 要約Signalのraw_metadataに載せる変更例の最大件数


def _t(el, tag) -> str:
    x = el.find(tag)
    return (x.text or "").strip() if x is not None and x.text else ""


def _element_hash(el) -> str:
    """要素内の全項目（タグ名＋テキスト）から決定的にhashを作る（並び替え・空白差の影響を受けにくい）。"""
    parts = sorted(f"{e.tag}={(e.text or '').strip()}" for e in el.iter() if (e.text or "").strip())
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _date_prefix(value: str) -> str | None:
    """'2015-07-01-04:00' のようにtimezone付きで来るdateから、先頭のYYYY-MM-DDだけを取り出す（不正ならNone）。"""
    m = re.match(r"\d{4}-\d{2}-\d{2}", value or "")
    if not m:
        return None
    try:
        datetime.strptime(m.group(0), "%Y-%m-%d")
    except ValueError:
        return None
    return m.group(0)


def _iso_date(value: str, fmt: str) -> str | None:
    try:
        return datetime.strptime(value, fmt).date().isoformat() if value else None
    except ValueError:
        return None


def parse_un_xml(raw: bytes) -> tuple[list[dict], str | None]:
    """国連安保理 統合リスト（INDIVIDUAL / ENTITY）。戻り値: (records, generated_at ISO)"""
    root = ET.fromstring(raw)
    generated = root.attrib.get("dateGenerated")
    records = []
    for el in root.iter():
        if el.tag not in ("INDIVIDUAL", "ENTITY"):
            continue
        name = " ".join(x for x in (_t(el, "FIRST_NAME"), _t(el, "SECOND_NAME"), _t(el, "THIRD_NAME"),
                                    _t(el, "FOURTH_NAME")) if x)
        alias_tag = "INDIVIDUAL_ALIAS" if el.tag == "INDIVIDUAL" else "ENTITY_ALIAS"
        aliases = sorted({_t(a, "ALIAS_NAME") for a in el.findall(alias_tag) if _t(a, "ALIAS_NAME")})
        nat = [v.text.strip() for n in el.findall("NATIONALITY") for v in n.findall("VALUE") if v.text]
        records.append({
            "external_record_id": _t(el, "DATAID"), "entity_name": name, "aliases": aliases,
            "program": " / ".join(x for x in (_t(el, "UN_LIST_TYPE"), _t(el, "REFERENCE_NUMBER")) if x) or None,
            "list_date": _date_prefix(_t(el, "LISTED_ON")), "nationality": ", ".join(nat) or None,
            "record_hash": _element_hash(el), "kind": "individual" if el.tag == "INDIVIDUAL" else "entity"})
    return [r for r in records if r["external_record_id"] and r["entity_name"]], generated


def parse_uk_xml(raw: bytes) -> tuple[list[dict], str | None]:
    """英国 制裁リスト（Designation）。戻り値: (records, generated_at ISO)"""
    root = ET.fromstring(raw)
    gen = root.find("DateGenerated")
    generated = _iso_date((gen.text or "").strip(), "%d/%m/%Y") if gen is not None else None
    records = []
    for el in root.findall("Designation"):
        primary, aliases = "", []
        for nm in el.findall("Names/Name"):
            full = " ".join((x.text or "").strip() for x in nm
                            if x.tag.startswith("Name") and x.tag != "NameType" and (x.text or "").strip())
            if _t(nm, "NameType") == "Primary Name" and not primary:
                primary = full
            elif full:
                aliases.append(full)
        records.append({
            "external_record_id": _t(el, "UniqueID"), "entity_name": primary or (aliases[0] if aliases else ""),
            "aliases": sorted(set(aliases)), "program": _t(el, "RegimeName") or None,
            "list_date": _iso_date(_t(el, "DateDesignated"), "%d/%m/%Y"), "nationality": None,
            "record_hash": _element_hash(el), "kind": _t(el, "IndividualEntityShip").lower() or None})
    return [r for r in records if r["external_record_id"] and r["entity_name"]], generated


PARSERS = {"un_xml": parse_un_xml, "uk_xml": parse_uk_xml}


def diff_records(existing: dict, records: list[dict]) -> dict:
    """existing: {external_record_id: {"status","hash","entry_id","version_id"}}。
    戻り値: added / amended / relisted（recordのlist）、delisted（external_idのlist）、unchanged（件数）。"""
    out = {"added": [], "amended": [], "relisted": [], "delisted": [], "unchanged": 0}
    seen = set()
    for r in records:
        seen.add(r["external_record_id"])
        ex = existing.get(r["external_record_id"])
        if ex is None:
            out["added"].append(r)
        elif ex["status"] == "delisted":
            out["relisted"].append(r)
        elif ex["hash"] != r["record_hash"]:
            out["amended"].append(r)
        else:
            out["unchanged"] += 1
    out["delisted"] = [eid for eid, ex in existing.items() if ex["status"] == "active" and eid not in seen]
    return out


def _chunks(seq, n=CHUNK):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _load_existing(client, source_id: str) -> dict:
    """このSourceの既存Entryと、その最新versionのhashを読み出す。"""
    entries, offset = [], 0
    while True:
        page = client.select("geo_sanctions_entries", {
            "select": "sanctions_entry_id,external_record_id,latest_version_id,current_status",
            "source_id": f"eq.{source_id}", "order": "external_record_id", "limit": "1000", "offset": str(offset)})
        entries += page
        if len(page) < 1000:
            break
        offset += 1000
    hashes = {}
    ids = [e["latest_version_id"] for e in entries if e["latest_version_id"]]
    for part in _chunks(ids, 80):
        for v in client.select("geo_sanctions_entry_versions", {
                "select": "version_id,source_record_hash", "version_id": f"in.({','.join(part)})"}):
            hashes[v["version_id"]] = v["source_record_hash"]
    return {e["external_record_id"]: {"status": e["current_status"], "entry_id": e["sanctions_entry_id"],
                                       "version_id": e["latest_version_id"], "hash": hashes.get(e["latest_version_id"]),
                                       "source_id": source_id}
            for e in entries}


def _version_row(entry_id, rec, *, snapshot_id, change_type, now_iso):
    return {"version_id": str(uuid.uuid4()), "sanctions_entry_id": entry_id, "source_record_hash": rec["record_hash"],
            "raw_snapshot_id": snapshot_id, "entity_name": rec["entity_name"], "aliases": rec["aliases"],
            "program": rec["program"], "list_date": rec["list_date"], "nationality": rec["nationality"],
            "change_type": change_type, "observed_at": now_iso, "valid_from": rec["list_date"]}


def _write_baseline(client, source_id, records, *, snapshot_id, now_iso):
    entries = [{"sanctions_entry_id": str(uuid.uuid4()), "source_id": source_id,
                "external_record_id": r["external_record_id"], "first_seen_at": now_iso, "last_seen_at": now_iso,
                "current_status": "active"} for r in records]
    for part in _chunks(entries):
        client.insert("geo_sanctions_entries", part, prefer="return=minimal")
    versions = [_version_row(e["sanctions_entry_id"], r, snapshot_id=snapshot_id, change_type="added", now_iso=now_iso)
                for e, r in zip(entries, records)]
    for part in _chunks(versions):
        client.insert("geo_sanctions_entry_versions", part, prefer="return=minimal")
    # 循環FK（entry→latest_version）のため、versionを作ってから主キーでmerge-duplicatesして最新versionを結ぶ
    linked = [{**e, "latest_version_id": v["version_id"]} for e, v in zip(entries, versions)]
    for part in _chunks(linked):
        client.insert("geo_sanctions_entries", part, prefer="resolution=merge-duplicates,return=minimal")


def _heal_orphan_entries(client, existing, records, *, snapshot_id, now_iso) -> int:
    """途中で失敗したBaselineが残した「最新versionを持たないEntry」に、'added' versionを付けて結ぶ（削除はしない）。"""
    by_id = {r["external_record_id"]: r for r in records}
    orphans = [(eid, ex) for eid, ex in existing.items() if not ex["version_id"] and eid in by_id]
    versions = [_version_row(ex["entry_id"], by_id[eid], snapshot_id=snapshot_id, change_type="added", now_iso=now_iso)
                for eid, ex in orphans]
    for part in _chunks(versions):
        client.insert("geo_sanctions_entry_versions", part, prefer="return=minimal")
    for (eid, ex), v in zip(orphans, versions):
        ex.update(version_id=v["version_id"], hash=v["source_record_hash"])
    linked = [{"sanctions_entry_id": ex["entry_id"], "source_id": ex.get("source_id"), "external_record_id": eid,
               "latest_version_id": ex["version_id"]} for eid, ex in orphans]
    for part in _chunks(linked):
        client.insert("geo_sanctions_entries", part, prefer="resolution=merge-duplicates,return=minimal")
    return len(orphans)


def _write_diff(client, source_id, diff, existing, *, snapshot_id, now_iso):
    versions = []
    for rec in diff["added"]:
        eid = str(uuid.uuid4())
        client.insert("geo_sanctions_entries", [{
            "sanctions_entry_id": eid, "source_id": source_id, "external_record_id": rec["external_record_id"],
            "first_seen_at": now_iso, "last_seen_at": now_iso, "current_status": "active"}], prefer="return=minimal")
        versions.append((_version_row(eid, rec, snapshot_id=snapshot_id, change_type="added", now_iso=now_iso), None))
    for rec in diff["amended"] + diff["relisted"]:
        ex = existing[rec["external_record_id"]]
        ct = "relisted" if ex["status"] == "delisted" else "amended"
        versions.append((_version_row(ex["entry_id"], rec, snapshot_id=snapshot_id, change_type=ct, now_iso=now_iso), ex))
    for external_id in diff["delisted"]:
        ex = existing[external_id]
        versions.append(({"version_id": str(uuid.uuid4()), "sanctions_entry_id": ex["entry_id"],
                          "source_record_hash": ex["hash"] or "", "entity_name": external_id, "change_type": "delisted",
                          "observed_at": now_iso}, ex))
    for part in _chunks(versions):
        client.insert("geo_sanctions_entry_versions", [v for v, _ in part], prefer="return=minimal")
    for v, ex in versions:
        if ex and ex.get("version_id"):
            client.update("geo_sanctions_entry_versions", {"version_id": f"eq.{ex['version_id']}"},
                          {"valid_to": now_iso[:10]})
        client.update("geo_sanctions_entries", {"sanctions_entry_id": f"eq.{v['sanctions_entry_id']}"}, {
            "latest_version_id": v["version_id"], "last_seen_at": now_iso,
            "current_status": "delisted" if v["change_type"] == "delisted" else "active"})


def _summary_signal(source_row, *, list_key, cfg, diff, generated, run_id, snapshot_hash) -> dict:
    n_add, n_amd = len(diff["added"]), len(diff["amended"]) + len(diff["relisted"])
    n_del = len(diff["delisted"])
    title = f"{source_row['name'].split('（')[0]} 更新: 追加{n_add}件・変更{n_amd}件・削除{n_del}件"
    examples = ([{"change": "added", "name": r["entity_name"], "program": r["program"]}
                 for r in diff["added"][:EXAMPLE_LIMIT]]
                + [{"change": "amended", "name": r["entity_name"], "program": r["program"]}
                   for r in (diff["amended"] + diff["relisted"])[:EXAMPLE_LIMIT]])
    published = datetime.fromisoformat(generated.replace("Z", "+00:00")) if generated else datetime.now(timezone.utc)
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return common.build_signal(
        source_row=source_row, provider="official", title=title, url=f"{cfg['url']}#diff-{snapshot_hash[:16]}",
        summary=f"リスト生成日: {generated or '不明'}。個別のEntryはgeo_sanctions_entry_versionsに保存。",
        published_at=published, language=cfg.get("language"), collector=CONNECTOR_TYPE,
        connector_version=CONNECTOR_VERSION, endpoint=cfg["url"], ingestion_run_id=run_id,
        extra_metadata={"list_key": list_key, "signal_kind": "sanctions_diff", "added": n_add, "amended": n_amd,
                        "delisted": n_del, "examples": examples})


def run_sanctions_collection(client, *, list_key: str, requested_by: str = "system", is_demo: bool = False,
                             config: dict = None, run_type: str = "manual", fetch=None) -> dict:
    cfg = (config or common.load_direct_config()).get("sanctions_lists", {}).get(list_key)
    if not cfg:
        raise common.SourceNotReadyError(f"sanctions_lists に {list_key} の設定がありません")
    source_row = common.resolve_source(client, cfg["registry_domain"])
    base.check_automation_allowed(source_row)
    run = base.create_ingestion_run(
        client, source_id=source_row["source_id"], connector_type=CONNECTOR_TYPE,
        run_type=run_type if run_type in ("manual", "cli", "scheduled_external") else "manual",
        requested_by=requested_by, request_parameters={"list_key": list_key, "url": cfg["url"]}, is_demo=is_demo)
    counts = {"fetched_count": 0, "inserted_count": 0, "updated_count": 0, "duplicate_count": 0,
              "skipped_count": 0, "error_count": 0}
    try:
        resp = fetch(cfg["url"]) if fetch else common.http_get(cfg["url"], accept="application/xml, text/xml, */*")
        snapshot, is_new = base.find_or_save_raw_snapshot(
            client, source_id=source_row["source_id"], raw_bytes=resp.content, request_url=cfg["url"],
            response_status=resp.status_code, content_type=resp.headers.get("Content-Type"), ext=".xml",
            connector_version=CONNECTOR_VERSION, parser_version=PARSER_VERSION, is_demo=is_demo)
        base.link_run_to_snapshot(client, ingestion_run_id=run["ingestion_run_id"],
                                   raw_snapshot_id=snapshot["raw_snapshot_id"])
        existing = _load_existing(client, source_row["source_id"])
        records, generated = PARSERS[cfg["format"]](resp.content)
        if not records:
            raise RuntimeError("リストを解析できませんでした（0件）。形式変更の可能性があります")
        # リストのXMLが前回と完全に同一で、登録済みEntryも欠けていなければ何もしない（途中失敗したBaselineは再開する）
        if existing and not is_new and len(existing) >= len(records) and all(ex["version_id"] for ex in existing.values()):
            common.finish_run(client, run, status="completed", counts=counts, error_summary=None,
                              connector_version=CONNECTOR_VERSION, parser_version=PARSER_VERSION)
            return {"list_key": list_key, "mode": "unchanged", "status": "completed", **counts,
                    "entries": len(existing), "ingestion_run_id": run["ingestion_run_id"]}
        counts["fetched_count"] = len(records)
        now_iso = datetime.now(timezone.utc).isoformat()
        result = {"list_key": list_key, "generated": generated}
        healed = _heal_orphan_entries(client, existing, records, snapshot_id=snapshot["raw_snapshot_id"], now_iso=now_iso)
        if healed:
            result["healed_entries"] = healed
        if not existing:                                   # Baseline: 全件登録、Signal/Alertなし
            _write_baseline(client, source_row["source_id"], records, snapshot_id=snapshot["raw_snapshot_id"],
                            now_iso=now_iso)
            counts["inserted_count"] = len(records)
            result.update(mode="baseline", signal_created=False)
        else:                                              # Diff: 変更分のみ。要約Signal 1件
            diff = diff_records(existing, records)
            _write_diff(client, source_row["source_id"], diff, existing, snapshot_id=snapshot["raw_snapshot_id"],
                        now_iso=now_iso)
            counts.update(inserted_count=len(diff["added"]),
                          updated_count=len(diff["amended"]) + len(diff["relisted"]), duplicate_count=diff["unchanged"])
            changed = (len(diff["added"]) + len(diff["amended"]) + len(diff["relisted"]) + len(diff["delisted"]))
            result.update(mode="diff", added=len(diff["added"]), amended=len(diff["amended"]),
                          relisted=len(diff["relisted"]), delisted=len(diff["delisted"]), signal_created=bool(changed))
            if changed:
                sig = _summary_signal(source_row, list_key=list_key, cfg=cfg, diff=diff, generated=generated,
                                      run_id=run["ingestion_run_id"], snapshot_hash=snapshot["content_hash"])
                signal_service.bulk_upsert_signals(client, [sig], provider="official", is_demo=is_demo)
        common.record_source_outcome(client, source_row, run, ok=True, is_demo=is_demo)
        common.finish_run(client, run, status="completed", counts=counts, error_summary=None,
                          connector_version=CONNECTOR_VERSION, parser_version=PARSER_VERSION)
        geo_audit.log_audit(client, actor=requested_by, action="sanctions_xml_run", target_table="geo_ingestion_runs",
                             target_id=run["ingestion_run_id"], detail={**result, **counts})
        return {**result, "status": "completed", **counts, "ingestion_run_id": run["ingestion_run_id"]}
    except Exception as e:  # noqa: BLE001 — Runを必ず閉じる
        counts["error_count"] += 1
        common.finish_run(client, run, status="failed", counts=counts, error_summary=f"{type(e).__name__}: {e}"[:500],
                          connector_version=CONNECTOR_VERSION, parser_version=PARSER_VERSION)
        common.record_source_outcome(client, source_row, run, ok=False, is_demo=is_demo)
        raise
