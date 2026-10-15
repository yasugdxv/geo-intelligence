"""地政学ナレッジ＆インテリジェンスDB Phase2B: IMF DataMapper API Connector（無認証）。

実アクセスで確認済み（2026-08-14）: https://www.imf.org/external/datamapper/api/v1/ は
認証ヘッダ無しで実データ（World Economic Outlook）を返す。api.imf.org のSDMX 3.0/2.1 REST APIは
dataflow識別子が特定できず404が続いたため採用しない。DataMapper APIは
「/{indicator_code}/{country_code}」で年次系列をまとめて返し、World Bank Indicators APIのような
pageパラメータ形式のページングを持たない（1コールで全期間分が返る）。

代表Indicator（config/geo_imf_indicators_seed.yaml）のみを対象とし、コードへ大量のIndicator Codeを
ハードコードしない。取得ごとにgeo_raw_snapshots（生JSON）を保存し、要約をgeo_documents/
geo_document_chunksへ、数値本体をgeo_metric_observationsへ保存する（World Bank Connectorと同じ
Evidence構成: Claimはdocument_chunkとmetric_observationの2本のEvidenceを持つ）。

使い方: python geo_connector_cli.py imf --theme-id <uuid> --entity-id <uuid> --country-code JP
"""
import time
from pathlib import Path

import requests
import yaml

import geo_audit
import geo_claim_service
import geo_connector_base as base
import geo_metric_service

SEED_PATH = Path(__file__).parent / "config" / "geo_imf_indicators_seed.yaml"
API_BASE = "https://www.imf.org/external/datamapper/api/v1"
CONNECTOR_VERSION = "geo-imf-1.0"
PARSER_VERSION = "geo-imf-parser-1.0"
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3


def load_indicator_seed() -> list:
    with open(SEED_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)["indicators"]


def _ensure_source(client, theme_id: str) -> dict:
    existing = client.select("geo_source_registry", {
        "select": "*", "name": "eq.IMF DataMapper API", "limit": "1",
    })
    if existing:
        return existing[0]
    return client.insert("geo_source_registry", [{
        "theme_id": theme_id, "name": "IMF DataMapper API", "url": API_BASE,
        "source_type": "official_site", "source_owner": "International Monetary Fund",
        "trust_tier": "A2", "access_method": "public_api", "connector_type": "imf",
        "automation_allowed": True, "content_storage_allowed": True,
        "internal_redistribution_allowed": True, "update_cadence": "quarterly",
    }])[0]


def _fetch_indicator(country_code: str, indicator_code: str, proxies: dict, verify: bool) -> tuple[dict, bytes]:
    """1指標分をretry付きで取得する。DataMapper APIはpageパラメータを持たず、1コールで
    全期間分の年次系列が返る。戻り値: (パース済みJSON, 生バイト列)。"""
    url = f"{API_BASE}/{indicator_code}/{country_code}"
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, proxies=proxies, verify=verify, timeout=REQUEST_TIMEOUT,
                                 headers={"Accept": "application/json"})
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            last_error = e
            time.sleep(2 ** attempt)
    else:
        raise RuntimeError(f"IMF API取得に失敗（{MAX_RETRIES}回リトライ後）: {last_error}")

    payload = resp.json()
    series = payload.get("values", {}).get(indicator_code, {}).get(country_code, {})
    data = [{"date": year, "value": value} for year, value in sorted(series.items())]
    return {"indicator_code": indicator_code, "country_code": country_code, "data": data}, resp.content


def run_imf_ingestion(client, *, theme_id: str, entity_id: str, country_code: str,
                      requested_by: str = "system", is_demo: bool = False) -> dict:
    """代表Indicator一式を1国分取得し、Ingestion Run・Raw Snapshot・Metric Observation・
    要約Document/Chunkまでを一括で行う（World Bank Connectorと同じ流れ）。"""
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
        client, source_id=source_row["source_id"], connector_type="imf", run_type="manual",
        requested_by=requested_by, request_parameters={"country_code": country_code}, is_demo=is_demo,
    )

    indicators = load_indicator_seed()
    counts = {"fetched_count": 0, "inserted_count": 0, "updated_count": 0, "error_count": 0}
    errors = []
    claims_created = []

    for indicator in indicators:
        try:
            parsed, raw_bytes = _fetch_indicator(country_code, indicator["code"], proxies, verify)
        except Exception as e:
            counts["error_count"] += 1
            errors.append(f"{indicator['code']}: {type(e).__name__}: {e}")
            continue

        counts["fetched_count"] += 1
        snapshot, is_new_snapshot = base.find_or_save_raw_snapshot(
            client, source_id=source_row["source_id"], raw_bytes=raw_bytes,
            request_url=f"{API_BASE}/{indicator['code']}/{country_code}",
            content_type="application/json", connector_version=CONNECTOR_VERSION,
            parser_version=PARSER_VERSION, ext=".json", is_demo=is_demo,
        )
        base.link_run_to_snapshot(client, ingestion_run_id=run["ingestion_run_id"],
                                   raw_snapshot_id=snapshot["raw_snapshot_id"])

        metric_def = geo_metric_service.create_metric_definition(
            client, source_id=source_row["source_id"], metric_code=indicator["code"],
            metric_name=indicator["name"], unit=indicator.get("unit"), frequency=indicator.get("frequency"),
            geographic_scope=indicator.get("geographic_scope"), source_url=API_BASE,
        )

        valid_points = [d for d in parsed["data"] if d.get("value") is not None]
        summary_lines = [f"{indicator['name']}（{country_code}）:"]
        observations_for_evidence = []
        for point in valid_points:
            observation = geo_metric_service.upsert_metric_observation(
                client, metric_definition_id=metric_def["metric_definition_id"], entity_id=entity_id,
                period=str(point["date"]), observed_value=point["value"],
                retrieved_at=snapshot["fetched_at"], raw_snapshot_id=snapshot["raw_snapshot_id"],
                estimate_or_actual="actual", is_demo=is_demo,
            )
            observations_for_evidence.append(observation)
            counts["inserted_count"] += 1
            summary_lines.append(f"  {point['date']}: {point['value']}")

        if not valid_points:
            continue

        document, is_new_doc = base.save_as_document(
            client, theme_id=theme_id, source_id=source_row["source_id"],
            title=f"IMF {indicator['name']} ({country_code})",
            extracted_text="\n".join(summary_lines), mime_type="application/json",
            url=f"{API_BASE}/{indicator['code']}/{country_code}", is_demo=is_demo,
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
        connector_version=CONNECTOR_VERSION, parser_version=PARSER_VERSION,
    )
    _update_source_registry_after_run(client, source_row, run["ingestion_run_id"], status, is_demo=is_demo)

    geo_audit.log_audit(client, actor=requested_by, action="imf_ingestion_run",
                         target_table="geo_ingestion_runs", target_id=run["ingestion_run_id"],
                         detail={"country_code": country_code, "status": status, **counts})

    return {"ingestion_run_id": run["ingestion_run_id"], "status": status, "counts": counts,
            "claim_ids": claims_created, "errors": errors}


def _update_source_registry_after_run(client, source_row: dict, ingestion_run_id: str, status: str,
                                       *, is_demo: bool = False) -> None:
    # Phase A（Collection Reliability、2026-10-04）: is_demo=Trueのテスト実行がSource
    # Registryのlast_successful_ingestion_at/failure_count（実運用のFreshness/Staleness
    # 判定が参照する値）を書き換えないようにする——調査中に実際にgeo_external_signal_queries
    # で同種の汚染を確認したため、全Connector共通で対応する。
    if is_demo:
        return
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    if status == "failed":
        client.update("geo_source_registry", {"source_id": f"eq.{source_row['source_id']}"}, {
            "last_ingestion_run_id": ingestion_run_id, "last_failed_ingestion_at": now_iso,
            "failure_count": source_row.get("failure_count", 0) + 1,
        })
    else:
        client.update("geo_source_registry", {"source_id": f"eq.{source_row['source_id']}"}, {
            "last_ingestion_run_id": ingestion_run_id, "last_successful_ingestion_at": now_iso,
            "failure_count": 0,
        })
