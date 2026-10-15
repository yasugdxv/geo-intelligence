"""地政学ナレッジ＆インテリジェンスDB Phase2B: Manifesto Project Database Connector
（Credential-required、Main Datasetのみ）。

今回のスコープはMain Dataset（政党×選挙年ごとのコード化済み変数: rile score、各カテゴリ%等）
のみ。個々の綱領全文ドキュメント（Corpus）の取得・保存・chunkingはcontent_storage_allowed/
external_redistribution_allowedの利用条件確認後の別段階とし、本Connectorでは一切行わない
（docs/geopolitical-connector-guide.md参照）。

エンドポイント（https://manifesto-project.wzb.eu/api/v1/get_core）はAPIキー未取得のため実
アクセスで検証できていない（config/geo_manifesto_seed.yaml参照）。credential_configured=false
の間はcheck_automation_allowedが実行前に弾くため、パラメータ名の誤りが実害を及ぼすことはない。

キーはconfig.jsonへ直接保存せず、connector_credentials.manifesto_project.env_varが指す
環境変数から都度解決する。

使い方: python geo_connector_cli.py manifesto --theme-id <uuid> [--max-records 50]
"""
import time
from pathlib import Path

import requests
import yaml

import geo_audit
import geo_claim_service
import geo_connector_base as base
import geo_entity_resolution
import geo_metric_service

SEED_PATH = Path(__file__).parent / "config" / "geo_manifesto_seed.yaml"
API_BASE = "https://manifesto-project.wzb.eu/api/v1/get_core"
CREDENTIAL_KEY = "manifesto_project"
CONNECTOR_VERSION = "geo-manifesto-1.0"
PARSER_VERSION = "geo-manifesto-parser-1.0"
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3


def load_seed() -> dict:
    with open(SEED_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _ensure_source(client, theme_id: str) -> dict:
    existing = client.select("geo_source_registry", {
        "select": "*", "name": "eq.Manifesto Project Database", "limit": "1",
    })
    if existing:
        return existing[0]
    return client.insert("geo_source_registry", [{
        "theme_id": theme_id, "name": "Manifesto Project Database", "url": API_BASE,
        "source_type": "research_institution", "source_owner": "WZB Berlin Social Science Center",
        "trust_tier": "B", "access_method": "public_api_credential_required",
        "connector_type": "manifesto", "automation_allowed": True,
        "content_storage_allowed": True, "internal_redistribution_allowed": True,
        "update_cadence": "annual",
    }])[0]


def _fetch_main_dataset(country_name: str, version: str, api_key: str,
                         proxies: dict, verify: bool) -> tuple[list, bytes]:
    """Main Dataset（政党×選挙年ごとのコード化済み変数）を国単位で取得する（retry付き）。
    綱領全文（Corpus）はここでは取得しない。戻り値: (data行のリスト, 生バイト列)。"""
    params = {"api_key": api_key, "kind": version, "country": country_name}
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(API_BASE, params=params, proxies=proxies, verify=verify,
                                 timeout=REQUEST_TIMEOUT, headers={"Accept": "application/json"})
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            last_error = e
            time.sleep(2 ** attempt)
    else:
        raise RuntimeError(f"Manifesto Project API取得に失敗（{MAX_RETRIES}回リトライ後）: {last_error}")

    payload = resp.json()
    rows = payload if isinstance(payload, list) else payload.get("data", [])
    return rows, resp.content


def run_manifesto_ingestion(client, *, theme_id: str, requested_by: str = "system",
                            max_records: int = None, is_demo: bool = False) -> dict:
    """seedで定義した国ごとにMain Datasetを取得し、Ingestion Run・Raw Snapshot・
    Metric Observation（rile score）・党単位Document/Chunk・Entity Match Candidateまでを
    一括で行う（OFAC Connectorと同様、自動Entity Mergeは行わない）。
    max_records: テスト・デモ用に処理件数を制限する（本番運用ではNoneで全件処理）。"""
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
        client, source_id=source_row["source_id"], connector_type="manifesto", run_type="manual",
        requested_by=requested_by, request_parameters={"max_records": max_records}, is_demo=is_demo,
    )

    seed = load_seed()
    metric_def = geo_metric_service.create_metric_definition(
        client, source_id=source_row["source_id"], metric_code="rile",
        metric_name="RILE (right-left) score", unit="score", frequency="per_election",
        geographic_scope="party", source_url=API_BASE,
    )

    counts = {"fetched_count": 0, "inserted_count": 0, "updated_count": 0, "error_count": 0}
    errors = []
    claims_created, match_candidates_created = [], []

    for country in seed["countries"]:
        try:
            rows, raw_bytes = _fetch_main_dataset(country["country_name"], seed["main_dataset_version"],
                                                   api_key, proxies, verify)
        except Exception as e:
            counts["error_count"] += 1
            errors.append(f"{country['country_name']}: {type(e).__name__}: {e}")
            continue

        counts["fetched_count"] += 1
        snapshot, _ = base.find_or_save_raw_snapshot(
            client, source_id=source_row["source_id"], raw_bytes=raw_bytes,
            request_url=API_BASE, request_parameters={"country": country["country_name"]},
            content_type="application/json", connector_version=CONNECTOR_VERSION,
            parser_version=PARSER_VERSION, ext=".json", is_demo=is_demo,
        )
        base.link_run_to_snapshot(client, ingestion_run_id=run["ingestion_run_id"],
                                   raw_snapshot_id=snapshot["raw_snapshot_id"])

        document, _ = base.save_as_document(
            client, theme_id=theme_id, source_id=source_row["source_id"],
            title=f"Manifesto Project Main Dataset ({country['country_name']})",
            extracted_text=f"Manifesto Project Main Dataset, country={country['country_name']}, "
                            f"{len(rows)} party-election records", mime_type="application/json",
            url=API_BASE, is_demo=is_demo,
        )

        party_rows = rows if max_records is None else rows[:max_records]
        for row_number, row in enumerate(party_rows):
            party_name = row.get("partyname") or row.get("party") or f"party_{row.get('party')}"
            election_date = str(row.get("edate") or row.get("date") or "")
            rile = row.get("rile")

            record_text = (f"party={party_name}\ndate={election_date}\ncountry={country['country_name']}\n"
                           f"rile={rile}")
            import hashlib
            chunk_hash = hashlib.sha256(record_text.encode("utf-8")).hexdigest()
            chunk = client.insert("geo_document_chunks", [{
                "document_id": document["document_id"], "chunk_index": row_number,
                "section": f"{country['country_name']}:{party_name}:{election_date}",
                "quoted_excerpt": record_text, "parser_version": PARSER_VERSION, "content_hash": chunk_hash,
            }])[0]

            match_result = geo_entity_resolution.resolve_or_flag(client, party_name)
            match_status = {"resolved": "exact_match_candidate", "ambiguous": "possible_match",
                            "unresolved": "no_match"}[match_result["status"]]
            matched_entity_id = match_result.get("entity_id")
            match_candidate = client.insert("geo_entity_match_candidates", [{
                "source_id": source_row["source_id"], "raw_snapshot_id": snapshot["raw_snapshot_id"],
                "external_entity_name": party_name, "external_entity_aliases": [],
                "match_status": match_status, "matched_entity_id": matched_entity_id,
                "confidence": "高" if match_status == "exact_match_candidate" else "低",
            }])[0]
            match_candidates_created.append(match_candidate["match_candidate_id"])

            if rile is None or matched_entity_id is None or not election_date:
                continue

            period = election_date[:4] if len(election_date) >= 4 else election_date
            observation = geo_metric_service.upsert_metric_observation(
                client, metric_definition_id=metric_def["metric_definition_id"], entity_id=matched_entity_id,
                period=period, observed_value=rile, retrieved_at=snapshot["fetched_at"],
                raw_snapshot_id=snapshot["raw_snapshot_id"], estimate_or_actual="actual", is_demo=is_demo,
            )
            counts["inserted_count"] += 1

            claim = geo_claim_service.create_claim(
                client, claim_text=f"{party_name}の{election_date}選挙時点でのRILEスコアは{rile}",
                information_class="observed_fact", origin_type="document",
                origin_id=document["document_id"], actor_id=requested_by,
                subject_entity_id=matched_entity_id, is_demo=is_demo,
            )
            geo_claim_service.link_evidence(client, claim_id=claim["claim_id"], evidence_role="supporting",
                                             actor_id=requested_by, document_chunk_id=chunk["chunk_id"])
            geo_claim_service.link_evidence(client, claim_id=claim["claim_id"], evidence_role="supporting",
                                             actor_id=requested_by, metric_observation_id=observation["metric_observation_id"])
            claims_created.append(claim["claim_id"])

    status = "completed" if (counts["error_count"] == 0 and max_records is None) else (
        "completed_with_warnings" if counts["fetched_count"] > 0 else "failed")
    next_scheduled_at = base.calculate_next_run("annual")
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

    geo_audit.log_audit(client, actor=requested_by, action="manifesto_ingestion_run",
                         target_table="geo_ingestion_runs", target_id=run["ingestion_run_id"],
                         detail={"status": status, **counts})

    return {"ingestion_run_id": run["ingestion_run_id"], "status": status, "counts": counts,
            "claim_ids": claims_created, "match_candidate_ids": match_candidates_created, "errors": errors}
