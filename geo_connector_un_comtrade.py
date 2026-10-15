"""地政学ナレッジ＆インテリジェンスDB Phase2B: UN Comtrade API Connector（Credential-required）。

UN Comtrade API v1（https://comtradeapi.un.org/data/v1/get/{typeCode}/{freqCode}/HS）は
APIキー登録が必須（comtradeplus.un.orgで無料登録）。ヘッダ`Ocp-Apim-Subscription-Key`で
キーを渡す。reporterCode/partnerCodeはUN M49数値コード（ISO3ではない）。

キーはconfig.jsonへ直接保存せず、config.jsonのconnector_credentials.un_comtrade.env_varが
指す環境変数から都度解決する（geo_connector_base.resolve_credential）。credential_configured
列はConnector実行のたびにsync_credential_statusで再評価し、automation_allowedには一切触れない。

貿易フローは双方向（reporter⇔partner）のためgeo_metric_service.upsert_metric_observationの
counterpart_entity_idを使う。取得対象（reporter/partner/commodity/period）の組み合わせは
config/geo_un_comtrade_flows_seed.yamlで管理する。

使い方: python geo_connector_cli.py un_comtrade --theme-id <uuid> --reporter-entity-id <uuid>
"""
import time
from pathlib import Path

import requests
import yaml

import geo_audit
import geo_claim_service
import geo_connector_base as base
import geo_metric_service

SEED_PATH = Path(__file__).parent / "config" / "geo_un_comtrade_flows_seed.yaml"
API_BASE = "https://comtradeapi.un.org/data/v1/get"
CREDENTIAL_KEY = "un_comtrade"
CONNECTOR_VERSION = "geo-un-comtrade-1.0"
PARSER_VERSION = "geo-un-comtrade-parser-1.0"
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3


def load_flow_seed() -> dict:
    with open(SEED_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _ensure_source(client, theme_id: str) -> dict:
    existing = client.select("geo_source_registry", {
        "select": "*", "name": "eq.UN Comtrade API", "limit": "1",
    })
    if existing:
        return existing[0]
    return client.insert("geo_source_registry", [{
        "theme_id": theme_id, "name": "UN Comtrade API", "url": API_BASE,
        "source_type": "official_site", "source_owner": "United Nations Statistics Division",
        "trust_tier": "A2", "access_method": "public_api_credential_required",
        "connector_type": "un_comtrade", "automation_allowed": True,
        "content_storage_allowed": True, "internal_redistribution_allowed": True,
        "update_cadence": "quarterly",
    }])[0]


def _fetch_flow_period(flow: dict, period: str, api_key: str, proxies: dict, verify: bool) -> tuple[list, bytes]:
    """1 reporter×partner×commodity×periodを1コールとして取得する（retry付き）。
    戻り値: (data行のリスト, 生バイト列)。"""
    url = f"{API_BASE}/{flow['type_code']}/{flow['freq_code']}/HS"
    params = {
        "reporterCode": flow["reporter_code"], "partnerCode": flow["partner_code"],
        "cmdCode": flow["cmd_code"], "flowCode": flow["flow_code"], "period": period,
    }
    headers = {"Ocp-Apim-Subscription-Key": api_key, "Accept": "application/json"}
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, params=params, headers=headers, proxies=proxies,
                                 verify=verify, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            last_error = e
            time.sleep(2 ** attempt)
    else:
        raise RuntimeError(f"UN Comtrade API取得に失敗（{MAX_RETRIES}回リトライ後）: {last_error}")

    payload = resp.json()
    return payload.get("data", []), resp.content


def run_un_comtrade_ingestion(client, *, theme_id: str, reporter_entity_id: str,
                              partner_entity_id: str = None, requested_by: str = "system",
                              is_demo: bool = False) -> dict:
    """seedで定義したフロー・期間の組み合わせを取得し、Ingestion Run・Raw Snapshot・
    Metric Observation（counterpart_entity_id=partner）・要約Document/Chunkまでを一括で行う。"""
    source_row = _ensure_source(client, theme_id)

    proxies = {}
    verify = True
    config = {}
    try:
        from config_utils import load_config, make_proxies
        config = load_config()
        proxies = make_proxies(config)
        verify = config.get("ssl", {}).get("verify", True)
    except Exception:
        pass

    base.sync_credential_status(client, source_id=source_row["source_id"], config=config,
                                 credential_key=CREDENTIAL_KEY)
    source_row = client.select("geo_source_registry", {
        "select": "*", "source_id": f"eq.{source_row['source_id']}", "limit": "1",
    })[0]
    base.check_automation_allowed(source_row)
    api_key = base.resolve_credential(config, CREDENTIAL_KEY)

    run = base.create_ingestion_run(
        client, source_id=source_row["source_id"], connector_type="un_comtrade", run_type="manual",
        requested_by=requested_by, request_parameters={"reporter_entity_id": reporter_entity_id}, is_demo=is_demo,
    )

    seed = load_flow_seed()
    counts = {"fetched_count": 0, "inserted_count": 0, "updated_count": 0, "error_count": 0}
    errors = []
    claims_created = []

    for flow in seed["flows"]:
        metric_code = f"{flow['cmd_code']}.{flow['flow_code']}"
        metric_def = geo_metric_service.create_metric_definition(
            client, source_id=source_row["source_id"], metric_code=metric_code,
            metric_name=f"Trade value ({flow['flow_code']}, {flow['cmd_code']})",
            unit="usd", frequency="annual", geographic_scope="bilateral", source_url=API_BASE,
        )

        for period in flow["periods"]:
            try:
                rows, raw_bytes = _fetch_flow_period(flow, period, api_key, proxies, verify)
            except Exception as e:
                counts["error_count"] += 1
                errors.append(f"{flow['reporter_code']}/{period}: {type(e).__name__}: {e}")
                continue

            counts["fetched_count"] += 1
            snapshot, _ = base.find_or_save_raw_snapshot(
                client, source_id=source_row["source_id"], raw_bytes=raw_bytes,
                request_url=f"{API_BASE}/{flow['type_code']}/{flow['freq_code']}/HS",
                request_parameters={"reporterCode": flow["reporter_code"], "period": period},
                content_type="application/json", connector_version=CONNECTOR_VERSION,
                parser_version=PARSER_VERSION, ext=".json", is_demo=is_demo,
            )
            base.link_run_to_snapshot(client, ingestion_run_id=run["ingestion_run_id"],
                                       raw_snapshot_id=snapshot["raw_snapshot_id"])

            trade_value = None
            for row in rows:
                if row.get("primaryValue") is not None:
                    trade_value = row["primaryValue"]
                    break
            if trade_value is None:
                continue

            observation = geo_metric_service.upsert_metric_observation(
                client, metric_definition_id=metric_def["metric_definition_id"], entity_id=reporter_entity_id,
                counterpart_entity_id=partner_entity_id, period=period, observed_value=trade_value,
                retrieved_at=snapshot["fetched_at"], raw_snapshot_id=snapshot["raw_snapshot_id"],
                estimate_or_actual="actual", is_demo=is_demo,
            )
            counts["inserted_count"] += 1

            summary_text = (f"{flow['reporter_name']}→{flow['partner_name']} 貿易額"
                             f"（{flow['flow_code']}, {flow['cmd_code']}, {period}）: {trade_value} USD")
            document, _ = base.save_as_document(
                client, theme_id=theme_id, source_id=source_row["source_id"],
                title=f"UN Comtrade {flow['reporter_name']}-{flow['partner_name']} {flow['flow_code']} {period}",
                extracted_text=summary_text, mime_type="application/json",
                url=f"{API_BASE}/{flow['type_code']}/{flow['freq_code']}/HS", is_demo=is_demo,
            )
            chunks = geo_claim_service.chunk_document(client, document["document_id"])
            if not chunks:
                continue

            claim = geo_claim_service.create_claim(
                client, claim_text=summary_text, information_class="observed_fact",
                origin_type="document", origin_id=document["document_id"], actor_id=requested_by,
                subject_entity_id=reporter_entity_id, object_entity_id=partner_entity_id, is_demo=is_demo,
            )
            geo_claim_service.link_evidence(client, claim_id=claim["claim_id"], evidence_role="supporting",
                                             actor_id=requested_by, document_chunk_id=chunks[0]["chunk_id"])
            geo_claim_service.link_evidence(client, claim_id=claim["claim_id"], evidence_role="supporting",
                                             actor_id=requested_by, metric_observation_id=observation["metric_observation_id"])
            claims_created.append(claim["claim_id"])

    status = "completed" if counts["error_count"] == 0 else (
        "completed_with_warnings" if counts["fetched_count"] > 0 else "failed")
    next_scheduled_at = base.calculate_next_run("quarterly")
    base.complete_ingestion_run(
        client, ingestion_run_id=run["ingestion_run_id"], status=status, counts=counts,
        error_summary="; ".join(errors) if errors else None, next_scheduled_at=next_scheduled_at,
        connector_version=CONNECTOR_VERSION, parser_version=PARSER_VERSION,
    )
    # Phase A（Collection Reliability、2026-10-04）: is_demo=Trueのテスト実行がSource
    # Registryの鮮度指標を書き換えないようにする（全Connector共通対応）。
    if not is_demo:
        from datetime import datetime, timezone
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

    geo_audit.log_audit(client, actor=requested_by, action="un_comtrade_ingestion_run",
                         target_table="geo_ingestion_runs", target_id=run["ingestion_run_id"],
                         detail={"status": status, **counts})

    return {"ingestion_run_id": run["ingestion_run_id"], "status": status, "counts": counts,
            "claim_ids": claims_created, "errors": errors}
