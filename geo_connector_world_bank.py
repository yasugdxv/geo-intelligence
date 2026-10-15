"""地政学ナレッジ＆インテリジェンスDB Phase2A: World Bank Indicators Connector（無認証）。

代表Indicator（config/geo_world_bank_indicators_seed.yaml）のみを対象とし、コードへ大量の
Indicator Codeをハードコードしない。取得ごとにgeo_raw_snapshots（生JSON）を保存し、要約を
geo_documents/geo_document_chunksへ、数値本体をgeo_metric_observationsへ保存する。

使い方: python -m geo_connector_cli world_bank --entity-id <uuid> --country-code JP
"""
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

import geo_audit
import geo_claim_service
import geo_connector_base as base
import geo_metric_service

SEED_PATH = Path(__file__).parent / "config" / "geo_world_bank_indicators_seed.yaml"
API_BASE = "https://api.worldbank.org/v2"
CONNECTOR_VERSION = "geo-world-bank-1.0"
PARSER_VERSION = "geo-world-bank-parser-1.0"
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3


def load_indicator_seed() -> list:
    with open(SEED_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)["indicators"]


def _ensure_source(client, theme_id: str) -> dict:
    existing = client.select("geo_source_registry", {
        "select": "*", "name": "eq.World Bank Indicators API", "limit": "1",
    })
    if existing:
        return existing[0]
    return client.insert("geo_source_registry", [{
        "theme_id": theme_id, "name": "World Bank Indicators API", "url": API_BASE,
        "source_type": "official_site", "source_owner": "World Bank",
        "trust_tier": "A2", "access_method": "public_api", "connector_type": "world_bank",
        "automation_allowed": True, "content_storage_allowed": True,
        "internal_redistribution_allowed": True, "update_cadence": "quarterly",
    }])[0]


def _fetch_indicator(country_code: str, indicator_code: str, date_range: str,
                      proxies: dict, verify: bool) -> tuple[dict, bytes]:
    """1指標分をpagination・retry付きで取得する。戻り値: (パース済みJSON, 生バイト列)。"""
    all_data, page, pages = [], 1, 1
    raw_bytes_parts = []
    while page <= pages:
        url = (f"{API_BASE}/country/{country_code}/indicator/{indicator_code}"
               f"?format=json&per_page=1000&date={date_range}&page={page}")
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = requests.get(url, proxies=proxies, verify=verify, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                break
            except requests.RequestException as e:
                last_error = e
                time.sleep(2 ** attempt)
        else:
            raise RuntimeError(f"World Bank API取得に失敗（{MAX_RETRIES}回リトライ後）: {last_error}")

        raw_bytes_parts.append(resp.content)
        payload = resp.json()
        if not isinstance(payload, list) or len(payload) < 2 or payload[1] is None:
            break
        metadata, data = payload[0], payload[1]
        all_data.extend(data)
        pages = metadata.get("pages", 1)
        page += 1

    return {"indicator_code": indicator_code, "country_code": country_code, "data": all_data}, b"".join(raw_bytes_parts)


def run_world_bank_ingestion(client, *, theme_id: str, entity_id: str, country_code: str,
                             date_range: str = "2015:2026", requested_by: str = "system",
                             is_demo: bool = False) -> dict:
    """代表Indicator一式を1国分取得し、Ingestion Run・Raw Snapshot・Metric Observation・
    要約Document/Chunkまでを一括で行う。"""
    source_row = _ensure_source(client, theme_id)
    base.check_automation_allowed(source_row)

    proxies = {}
    verify = True
    try:
        from config_utils import load_config, make_proxies
        config = load_config()
        proxies = make_proxies(config)
        verify = config.get("ssl", {}).get("verify", True)
    except Exception:
        pass

    run = base.create_ingestion_run(
        client, source_id=source_row["source_id"], connector_type="world_bank", run_type="manual",
        requested_by=requested_by, request_parameters={"country_code": country_code, "date_range": date_range},
        is_demo=is_demo,
    )

    indicators = load_indicator_seed()
    counts = {"fetched_count": 0, "inserted_count": 0, "updated_count": 0, "error_count": 0}
    errors = []
    claims_created = []

    for indicator in indicators:
        try:
            parsed, raw_bytes = _fetch_indicator(country_code, indicator["code"], date_range, proxies, verify)
        except Exception as e:
            counts["error_count"] += 1
            errors.append(f"{indicator['code']}: {type(e).__name__}: {e}")
            continue

        counts["fetched_count"] += 1
        snapshot, is_new_snapshot = base.find_or_save_raw_snapshot(
            client, source_id=source_row["source_id"], raw_bytes=raw_bytes,
            request_url=f"{API_BASE}/country/{country_code}/indicator/{indicator['code']}",
            request_parameters={"date_range": date_range}, content_type="application/json",
            source_vintage=date_range, connector_version=CONNECTOR_VERSION, parser_version=PARSER_VERSION,
            ext=".json", is_demo=is_demo,
        )
        base.link_run_to_snapshot(client, ingestion_run_id=run["ingestion_run_id"],
                                   raw_snapshot_id=snapshot["raw_snapshot_id"])

        metric_def = geo_metric_service.create_metric_definition(
            client, source_id=source_row["source_id"], metric_code=indicator["code"],
            metric_name=indicator["name"], unit=indicator.get("unit"), frequency=indicator.get("frequency"),
            geographic_scope=indicator.get("geographic_scope"), source_url=API_BASE,
        )

        valid_points = [d for d in parsed["data"] if d.get("value") is not None]
        summary_lines = [f"{indicator['name']}（{country_code}、{date_range}）:"]
        observations_for_evidence = []
        for point in valid_points:
            observation = geo_metric_service.upsert_metric_observation(
                client, metric_definition_id=metric_def["metric_definition_id"], entity_id=entity_id,
                period=str(point["date"]), observed_value=point["value"],
                retrieved_at=snapshot["fetched_at"], raw_snapshot_id=snapshot["raw_snapshot_id"],
                estimate_or_actual="actual", source_vintage=date_range, is_demo=is_demo,
            )
            observations_for_evidence.append(observation)
            counts["inserted_count"] += 1
            summary_lines.append(f"  {point['date']}: {point['value']}")

        if not valid_points:
            continue

        document, is_new_doc = base.save_as_document(
            client, theme_id=theme_id, source_id=source_row["source_id"],
            title=f"World Bank {indicator['name']} ({country_code}, {date_range})",
            extracted_text="\n".join(summary_lines), mime_type="application/json",
            url=f"{API_BASE}/country/{country_code}/indicator/{indicator['code']}", is_demo=is_demo,
        )
        chunks = geo_claim_service.chunk_document(client, document["document_id"])
        if not chunks:
            continue

        claim = geo_claim_service.create_claim(
            client, claim_text=summary_lines[0], information_class="observed_fact",
            origin_type="document", origin_id=document["document_id"], actor_id=requested_by,
            subject_entity_id=entity_id, is_demo=is_demo,
        )
        geo_claim_service.link_evidence(client, claim_id=claim["claim_id"], evidence_role="supporting",
                                         actor_id=requested_by, document_chunk_id=chunks[0]["chunk_id"])
        latest_observation = max(observations_for_evidence, key=lambda o: o["period"])
        geo_claim_service.link_evidence(client, claim_id=claim["claim_id"], evidence_role="supporting",
                                         actor_id=requested_by, metric_observation_id=latest_observation["metric_observation_id"])
        claims_created.append(claim["claim_id"])

    status = "completed" if counts["error_count"] == 0 else (
        "completed_with_warnings" if counts["fetched_count"] > 0 else "failed")
    next_scheduled_at = base.calculate_next_run("quarterly")
    base.complete_ingestion_run(
        client, ingestion_run_id=run["ingestion_run_id"], status=status, counts=counts,
        error_summary="; ".join(errors) if errors else None, next_scheduled_at=next_scheduled_at,
        connector_version=CONNECTOR_VERSION, parser_version=PARSER_VERSION, source_vintage=date_range,
    )
    # Phase A（Collection Reliability、2026-10-04）: is_demo=Trueのテスト実行がSource
    # Registryの鮮度指標を書き換えないようにする（全Connector共通対応）。
    if not is_demo:
        now_iso = datetime.now(timezone.utc).isoformat()
        if status == "failed":
            client.update("geo_source_registry", {"source_id": f"eq.{source_row['source_id']}"}, {
                "last_ingestion_run_id": run["ingestion_run_id"], "last_failed_ingestion_at": now_iso,
                "failure_count": source_row.get("failure_count", 0) + 1,
            })
        else:
            client.update("geo_source_registry", {"source_id": f"eq.{source_row['source_id']}"}, {
                "last_ingestion_run_id": run["ingestion_run_id"], "last_successful_ingestion_at": now_iso,
                "failure_count": 0,
            })

    geo_audit.log_audit(client, actor=requested_by, action="world_bank_ingestion_run",
                         target_table="geo_ingestion_runs", target_id=run["ingestion_run_id"],
                         detail={"country_code": country_code, "status": status, **counts})

    return {"ingestion_run_id": run["ingestion_run_id"], "status": status, "counts": counts,
            "claim_ids": claims_created, "errors": errors}
