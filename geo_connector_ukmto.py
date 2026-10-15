"""GEO_DIRECT_COLLECTION_V1_1: UKMTO（UK Maritime Trade Operations）Direct Collector。

UKMTO固有のincident structured data（JSON）を取得するSource専用Collector。
**APIの性格**: 正式に文書化された公開APIではなく、公開Web UI（www.ukmto.org「Recent Incidents」）が通常の画面表示のために
利用している、認証不要の公開structured endpointである（GETのみ使用）。Known limitation: schema変更・endpoint変更のリスク、
可用性の保証なし。write系・投稿系・管理系endpointは一切使わない。WAF/認証の回避もしない。

設計の要点:
- 取得頻度: 既定30分（min_interval_minutes）。それより短い間隔では取得しない（手動実行も同じ）。Last-Modifiedがあれば条件付きGET。
- Natural key: `{issuer}-{incidentNumber}-{発行年}`（公式の事案番号）。同じ事案を再取得しても重複しない。
- Version: 同じ事案番号の内容が変わったら content hash で検出し、version+1・previous_hashesに履歴を残す（new / updated / unchanged）。
- Baseline: Signalが1件も無い初回は全件をbaseline（`raw_metadata.baseline=true`）として取り込み、Monitoring Alertの対象にしない。
  次回以降の新規・更新だけが既存Alert policyへ渡る。
- Schema Guard: 必須fieldの欠落・型の不一致・リスト以外は schema_changed として**失敗**にする（空データとして成功にしない）。
  「0件の正当な空応答」と「parserの破損」を区別する。
- Provenance: source_id・endpoint・collection_method・raw payload hash（＋Raw Snapshot）・ingestion_run_id・取得時刻。
  存在しないfield（例: status）は推測で埋めない。
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

import requests

import geo_connector_base as base
import geo_direct_collection_common as common
import geo_external_signal_service as signal_service

CONNECTOR_TYPE = "ukmto"
CONNECTOR_VERSION = "geo-ukmto-1.0"
PARSER_VERSION = "geo-ukmto-parser-1.0"
REQUEST_TIMEOUT = 30

REQUIRED_FIELDS = ("incidentIssuer", "incidentNumber", "utcDateCreated", "utcDateOfIncident", "incidentTypeName", "place")
KNOWN_FIELDS = {
    "incidentIssuer", "incidentNumber", "sitecoreId", "utcDateCreated", "utcDateOfIncident", "pinColour", "incidentTypeName",
    "incidentTypeLevel", "locationLatitude", "locationLongitude", "locationLatitudeDDDMMSS", "locationLongitudeDDDMMSS", "region",
    "place", "vesselName", "vesselType", "vesselUnderPirateControl", "dateVesselTaken", "crewHeld", "otherDetails",
    "extendTickerTime", "hideFromTicker"}
VOLATILE_FIELDS = {"extendTickerTime", "hideFromTicker"}      # 表示制御用。内容のversionには使わない


class UkmtoError(RuntimeError):
    kind = "unexpected"


class FetchTimeout(UkmtoError):
    kind = "timeout"


class FetchConnectionError(UkmtoError):
    kind = "connection_error"


class HttpStatusError(UkmtoError):
    kind = "http_error"


class MalformedPayload(UkmtoError):
    kind = "malformed_json"


class SchemaChanged(UkmtoError):
    kind = "schema_changed"


def classify_failure(exc: Exception) -> str:
    return getattr(exc, "kind", "unexpected")


# ---------------------------------------------------------------------------
# 取得とparse
# ---------------------------------------------------------------------------

def fetch_endpoint(url: str, *, if_modified_since: str | None = None, config: dict | None = None):
    """GETのみ。条件付きGET（If-Modified-Since）に対応。失敗は種類別の例外にする。"""
    proxies, verify = {}, True
    try:
        from config_utils import load_config, make_proxies
        cfg = config or load_config()
        proxies, verify = make_proxies(cfg), cfg.get("ssl", {}).get("verify", True)
    except Exception:  # noqa: BLE001
        pass
    headers = {"User-Agent": common.USER_AGENT, "Accept": "application/json"}
    if if_modified_since:
        headers["If-Modified-Since"] = if_modified_since
    try:
        resp = requests.get(url, headers=headers, proxies=proxies, verify=verify, timeout=REQUEST_TIMEOUT)
    except requests.Timeout as e:
        raise FetchTimeout(f"timeout: {e}") from e
    except requests.RequestException as e:
        raise FetchConnectionError(f"connection error: {type(e).__name__}: {str(e)[:120]}") from e
    if resp.status_code == 304:
        return resp
    if resp.status_code >= 400:
        raise HttpStatusError(f"HTTP {resp.status_code}")
    return resp


_FRACTION_RE = re.compile(r"\.(\d+)")


def _parse_ts(value) -> datetime:
    """ISO8601を解釈する。Postgresは末尾の0を省く（'.80129'のような小数秒5桁）ため、Python 3.10のfromisoformatが失敗する。6桁へ揃えてから解釈する。"""
    text = _FRACTION_RE.sub(lambda m: "." + m.group(1)[:6].ljust(6, "0"), str(value).replace("Z", "+00:00"), count=1)
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_payload(raw: bytes) -> tuple[list[dict], set]:
    """JSONをincidentのlistへ。Schema Guard: 構造が想定と違えばSchemaChangedにする（空データとして扱わない）。
    戻り値: (incidents, 想定外の新しいfield名の集合)。空のlist（[]）は「正当な空応答」として返す。"""
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as e:
        raise MalformedPayload(f"JSONとして解釈できません: {str(e)[:100]}") from e
    if not isinstance(data, list):
        raise SchemaChanged(f"想定外の構造です（listではなく{type(data).__name__}）")
    problems, new_fields = [], set()
    for i, it in enumerate(data):
        if not isinstance(it, dict):
            problems.append(f"[{i}] objectではありません")
            continue
        missing = [f for f in REQUIRED_FIELDS if f not in it or it[f] in (None, "")]
        if missing:
            problems.append(f"[{i}] 必須fieldなし: {','.join(missing)}")
            continue
        try:
            int(it["incidentNumber"]); _parse_ts(it["utcDateCreated"]); _parse_ts(it["utcDateOfIncident"])
        except (ValueError, TypeError) as e:
            problems.append(f"[{i}] 型が想定と違います: {str(e)[:60]}")
            continue
        new_fields |= set(it) - KNOWN_FIELDS
    if problems:
        raise SchemaChanged(f"{len(problems)}/{len(data)}件が想定schemaと一致しません（例: {problems[0]}）")
    return data, new_fields


def natural_key(it: dict) -> str:
    return f"{it['incidentIssuer']}-{int(it['incidentNumber'])}-{_parse_ts(it['utcDateCreated']).year}"


def content_hash(it: dict) -> str:
    stable = {k: v for k, v in it.items() if k not in VOLATILE_FIELDS}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def incident_title(it: dict) -> str:
    return f"UKMTO {it['incidentTypeName'].strip()}: {it['place'].strip()} (#{int(it['incidentNumber'])}/{_parse_ts(it['utcDateCreated']).year % 100:02d})"


def build_incident_signal(source_row: dict, it: dict, *, cfg: dict, run_id: str, payload_hash: str, snapshot_id: str | None,
                          baseline: bool, language: str | None = "en") -> dict:
    key = natural_key(it)
    page = cfg.get("original_page_url", "https://www.ukmto.org/recent-incidents")
    extra = {
        "collection_method": "public_json_endpoint", "endpoint_documented": False, "natural_key": key,
        "incident_number": int(it["incidentNumber"]), "incident_issuer": it["incidentIssuer"], "sitecore_id": it.get("sitecoreId"),
        "issued_at": it["utcDateCreated"], "incident_type": it["incidentTypeName"], "incident_type_level": it.get("incidentTypeLevel"),
        "pin_colour": it.get("pinColour"), "place": it["place"], "region": it.get("region"),
        "latitude": it.get("locationLatitude"), "longitude": it.get("locationLongitude"),
        "latitude_ddmmss": it.get("locationLatitudeDDDMMSS"), "longitude_ddmmss": it.get("locationLongitudeDDDMMSS"),
        "vessel_name": it.get("vesselName"), "vessel_type": (it.get("vesselType") or "").strip() or None,
        "vessel_under_pirate_control": it.get("vesselUnderPirateControl"), "date_vessel_taken": it.get("dateVesselTaken"),
        "crew_held": it.get("crewHeld"), "original_page_url": page,
        "content_hash": content_hash(it), "version": 1, "previous_hashes": [], "baseline": baseline,
        "payload_hash": payload_hash, "raw_snapshot_id": snapshot_id}
    # 個別incidentの公式URLは無いため、公式の一覧ページ＋Geo側の同一性用のfragment（natural key）をURLにする
    sig = common.build_signal(
        source_row=source_row, provider="official", title=incident_title(it), url=f"{page}#{key}",
        summary=common.clean_text((it.get("otherDetails") or "").replace("\r\n", "\n"), 1500),
        published_at=_parse_ts(it["utcDateOfIncident"]), language=language, collector=CONNECTOR_TYPE,
        connector_version=CONNECTOR_VERSION, endpoint=cfg["endpoint_url"], ingestion_run_id=run_id,
        provider_record_id=key, extra_metadata=extra)
    return sig


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------

def _last_run(client, source_id: str, *, is_demo: bool) -> dict | None:
    rows = client.select("geo_ingestion_runs", {
        "select": "ingestion_run_id,started_at,status,source_vintage", "source_id": f"eq.{source_id}",
        "connector_type": f"eq.{CONNECTOR_TYPE}", "is_demo": "eq.true" if is_demo else "eq.false",
        "order": "started_at.desc", "limit": "1"})
    return rows[0] if rows else None


def _existing_signals(client, source_id: str, dedup_keys: list) -> dict:
    rows = signal_service._select_in_chunked(
        client, "geo_external_signals", select="external_signal_id,dedup_key,raw_metadata,title,summary",
        in_column="dedup_key", in_values=dedup_keys, extra_params={"provider": "eq.official", "source_id": f"eq.{source_id}"})
    return {r["dedup_key"]: r for r in rows}


def run_ukmto_collection(client, *, requested_by: str = "system", is_demo: bool = False, config: dict | None = None,
                         run_type: str = "manual", fetch=None, ignore_min_interval: bool = False, now: datetime | None = None) -> dict:
    cfg = (config or common.load_direct_config()).get("ukmto")
    if not cfg:
        raise common.SourceNotReadyError("ukmto の設定がありません")
    source_row = common.resolve_source(client, cfg["registry_domain"])
    base.check_automation_allowed(source_row)
    now = now or datetime.now(timezone.utc)
    last = _last_run(client, source_row["source_id"], is_demo=is_demo)
    min_minutes = int(cfg.get("min_interval_minutes", 30))
    if last and not ignore_min_interval:
        started = _parse_ts(last["started_at"])
        if now - started < timedelta(minutes=min_minutes):           # 30分より高頻度に取得しない（手動実行も同じ）
            return {"status": "skipped_too_soon", "reason": f"前回取得（{started.isoformat()}）から{min_minutes}分未満のため取得しません",
                    "min_interval_minutes": min_minutes}

    last_modified_sent = last["source_vintage"] if (last and last["status"] in ("completed", "completed_with_warnings")) else None
    run = base.create_ingestion_run(
        client, source_id=source_row["source_id"], connector_type=CONNECTOR_TYPE,
        run_type=run_type if run_type in ("manual", "cli", "scheduled_external") else "manual", requested_by=requested_by,
        request_parameters={"endpoint": cfg["endpoint_url"], "method": "GET", "if_modified_since": last_modified_sent,
                            "min_interval_minutes": min_minutes}, is_demo=is_demo)
    counts = {"fetched_count": 0, "inserted_count": 0, "updated_count": 0, "duplicate_count": 0, "skipped_count": 0, "error_count": 0}
    try:
        resp = (fetch or fetch_endpoint)(cfg["endpoint_url"], if_modified_since=last_modified_sent)
        last_modified = resp.headers.get("Last-Modified")
        if resp.status_code == 304:                                   # 変更なし（取得は健全）
            base.complete_ingestion_run(client, ingestion_run_id=run["ingestion_run_id"], status="completed", counts=counts,
                                        connector_version=CONNECTOR_VERSION, parser_version=PARSER_VERSION, source_vintage=last_modified_sent)
            common.record_source_outcome(client, source_row, run, ok=True, is_demo=is_demo)
            return {"status": "completed", "mode": "not_modified", **counts, "ingestion_run_id": run["ingestion_run_id"]}
        incidents, new_fields = parse_payload(resp.content)
        payload_hash = hashlib.sha256(resp.content).hexdigest()
        snapshot, _ = base.find_or_save_raw_snapshot(
            client, source_id=source_row["source_id"], raw_bytes=resp.content, request_url=cfg["endpoint_url"],
            response_status=resp.status_code, content_type=resp.headers.get("Content-Type"), ext=".json",
            connector_version=CONNECTOR_VERSION, parser_version=PARSER_VERSION, is_demo=is_demo)
        base.link_run_to_snapshot(client, ingestion_run_id=run["ingestion_run_id"], raw_snapshot_id=snapshot["raw_snapshot_id"])
        counts["fetched_count"] = len(incidents)

        any_existing = client.select("geo_external_signals", {
            "select": "external_signal_id", "provider": "eq.official", "source_id": f"eq.{source_row['source_id']}",
            "is_demo": "eq.true" if is_demo else "eq.false", "limit": "1"})
        warnings_list = []
        if new_fields:
            warnings_list.append(f"想定外の新しいfield: {sorted(new_fields)}（schema変更の予兆）")
        baseline = not any_existing and bool(incidents)
        mode = "baseline" if baseline else "diff"
        if not incidents:                                              # 正当な空応答（JSONの[]）。parser破損とは区別する
            mode = "empty"
            if any_existing:
                warnings_list.append("空応答です（以前は事案がありました）。既存のSignalは変更しません")

        built = {}
        for it in incidents:
            sig = build_incident_signal(source_row, it, cfg=cfg, run_id=run["ingestion_run_id"], payload_hash=payload_hash,
                                        snapshot_id=snapshot["raw_snapshot_id"], baseline=baseline)
            built[sig["dedup_key"]] = sig
        existing = _existing_signals(client, source_row["source_id"], list(built)) if built else {}
        to_insert, updated, unchanged_ids = [], [], []
        now_iso = now.isoformat()
        for key, sig in built.items():
            ex = existing.get(key)
            if ex is None:
                to_insert.append({**sig, "is_demo": is_demo})
                continue
            old_meta = ex.get("raw_metadata") or {}
            if old_meta.get("content_hash") == sig["raw_metadata"]["content_hash"]:
                unchanged_ids.append(ex["external_signal_id"])
                continue
            meta = {**sig["raw_metadata"], "version": int(old_meta.get("version", 1)) + 1,        # 同じ事案番号の内容が更新された
                    "previous_hashes": (old_meta.get("previous_hashes") or []) + [old_meta.get("content_hash")],
                    "updated_detected_at": now_iso, "baseline": False, "first_collected_at": old_meta.get("collected_at")}
            client.update("geo_external_signals", {"external_signal_id": f"eq.{ex['external_signal_id']}"},
                          {"title": sig["title"], "summary": sig["summary"], "raw_metadata": meta, "last_observed_at": now_iso})
            updated.append(key)
        if to_insert:
            client.insert("geo_external_signals", to_insert, prefer="return=minimal")
        if unchanged_ids:
            signal_service._update_in_chunked(client, "geo_external_signals", in_column="external_signal_id", in_values=unchanged_ids,
                                              patch={"last_observed_at": now_iso})
        counts.update(inserted_count=len(to_insert), updated_count=len(updated), duplicate_count=len(unchanged_ids))
        hormuz = sum(1 for s in built.values() if "hormuz" in (s["raw_metadata"].get("place") or "").lower())
        latest = max((s["published_at"] for s in built.values() if s["published_at"]), default=None)
        status = "completed_with_warnings" if warnings_list else "completed"
        base.complete_ingestion_run(client, ingestion_run_id=run["ingestion_run_id"], status=status, counts=counts,
                                    error_summary="; ".join(warnings_list) or None, connector_version=CONNECTOR_VERSION,
                                    parser_version=PARSER_VERSION, source_vintage=last_modified)
        common.record_source_outcome(client, source_row, run, ok=True, is_demo=is_demo)
        return {"status": status, "mode": mode, "total_incidents": len(incidents), "hormuz_incidents": hormuz,
                "new": len(to_insert), "updated": len(updated), "unchanged": len(unchanged_ids), "latest_incident_at": latest,
                "baseline": baseline, "warnings": warnings_list, "payload_hash": payload_hash, **counts,
                "ingestion_run_id": run["ingestion_run_id"]}
    except Exception as e:  # noqa: BLE001 — Runを必ず閉じ、失敗の種類を残す（空成功にしない）
        kind = classify_failure(e)
        counts["error_count"] += 1
        base.complete_ingestion_run(client, ingestion_run_id=run["ingestion_run_id"], status="failed", counts=counts,
                                    error_summary=f"[{kind}] {type(e).__name__}: {e}"[:500], connector_version=CONNECTOR_VERSION,
                                    parser_version=PARSER_VERSION)
        common.record_source_outcome(client, source_row, run, ok=False, is_demo=is_demo)
        e.failure_kind = kind
        raise
