"""地政学ナレッジ＆インテリジェンスDB Phase2A/2B: OFAC Sanctions List Connector。

対象は公開SDN CSV（sdn.csv）・別名CSV（alt.csv）・住所CSV（add.csv）。
国籍・身分証明書番号はadd.csv同様の単純CSVでは提供されておらず、OFACの関連XML形式
`sdn_advanced.xml`でのみ取得できることをWeb検索で確認済み（`citizen.csv`/`id.csv`という
ファイルは実在しない。旧版の本ファイルdocstringにあった記述は誤りだったため訂正した）。
sdn_advanced.xml対応は別タスクとする（docs/geopolitical-connector-guide.md参照）。

Entity自動Mergeは行わない（geo_entity_match_candidatesへ候補登録のみ）。OFACレコードの継続追跡は
geo_sanctions_entries（current_status）+ geo_sanctions_entry_versions（change_type、上書きしない）
で行う。delisted判定はRunがcompleted（全件正常取得）の場合のみ行う。
"""
import hashlib
import io
from datetime import datetime, timezone

import pandas as pd
import requests

import geo_audit
import geo_claim_service
import geo_connector_base as base
import geo_entity_resolution

SDN_URL = "https://www.treasury.gov/ofac/downloads/sdn.csv"
ALT_URL = "https://www.treasury.gov/ofac/downloads/alt.csv"
ADD_URL = "https://www.treasury.gov/ofac/downloads/add.csv"
SDN_COLUMNS = ["ent_num", "sdn_name", "sdn_type", "program", "title", "call_sign",
               "vess_type", "tonnage", "grt", "vess_flag", "vess_owner", "remarks"]
ALT_COLUMNS = ["ent_num", "alt_num", "alt_type", "alt_name", "alt_remarks"]
ADD_COLUMNS = ["ent_num", "add_num", "address", "city_state_province_postal_code",
               "country", "add_remarks"]
CONNECTOR_VERSION = "geo-ofac-1.1"
PARSER_VERSION = "geo-ofac-parser-1.1"
REQUEST_TIMEOUT = 30


def _ensure_source(client, theme_id: str) -> dict:
    existing = client.select("geo_source_registry", {
        "select": "*", "name": "eq.OFAC Sanctions List Service", "limit": "1",
    })
    if existing:
        return existing[0]
    return client.insert("geo_source_registry", [{
        "theme_id": theme_id, "name": "OFAC Sanctions List Service", "url": SDN_URL,
        "source_type": "official_site", "source_owner": "U.S. Department of the Treasury",
        "trust_tier": "A1", "access_method": "public_csv", "connector_type": "ofac",
        "automation_allowed": True, "content_storage_allowed": True,
        "internal_redistribution_allowed": True, "update_cadence": "daily",
    }])[0]


def _fetch_csv(url: str, proxies: dict, verify: bool) -> bytes:
    resp = requests.get(url, proxies=proxies, verify=verify, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.content


def _parse_sdn(raw_bytes: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(raw_bytes), header=None, names=SDN_COLUMNS,
                      quotechar='"', skipinitialspace=True, dtype=str, keep_default_na=False)
    return df


def _parse_alt(raw_bytes: bytes) -> dict:
    df = pd.read_csv(io.BytesIO(raw_bytes), header=None, names=ALT_COLUMNS,
                      quotechar='"', skipinitialspace=True, dtype=str, keep_default_na=False)
    aliases = {}
    for _, row in df.iterrows():
        aliases.setdefault(row["ent_num"], []).append(row["alt_name"])
    return aliases


def _parse_add(raw_bytes: bytes) -> dict:
    df = pd.read_csv(io.BytesIO(raw_bytes), header=None, names=ADD_COLUMNS,
                      quotechar='"', skipinitialspace=True, dtype=str, keep_default_na=False)
    addresses = {}
    for _, row in df.iterrows():
        addresses.setdefault(row["ent_num"], []).append({
            "address": row["address"],
            "city_state_province_postal_code": row["city_state_province_postal_code"],
            "country": row["country"],
            "remarks": row["add_remarks"],
        })
    return addresses


def _record_hash(entity_name: str, program: str, remarks: str, aliases: list, addresses: list) -> str:
    addresses_key = ",".join(sorted(
        f"{a['address']}|{a['city_state_province_postal_code']}|{a['country']}|{a['remarks']}"
        for a in addresses
    ))
    payload = f"{entity_name}|{program}|{remarks}|{','.join(sorted(aliases))}|{addresses_key}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def run_ofac_ingestion(client, *, theme_id: str, requested_by: str = "system",
                       max_records: int = None, is_demo: bool = False) -> dict:
    """SDN CSV（+別名CSV・住所CSV）を取得し、Ingestion Run・Raw Snapshot・レコード単位のDocument Chunk・
    geo_sanctions_entries/versions・Entity Match Candidate・Claim候補までを一括で行う。
    max_records: テスト・デモ用に処理件数を制限する（本番運用ではNoneで全件処理）。"""
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
        client, source_id=source_row["source_id"], connector_type="ofac", run_type="manual",
        requested_by=requested_by, request_parameters={"max_records": max_records}, is_demo=is_demo,
    )

    counts = {"fetched_count": 0, "inserted_count": 0, "updated_count": 0, "skipped_count": 0,
              "duplicate_count": 0, "error_count": 0}
    errors = []
    fetched_at = datetime.now(timezone.utc).isoformat()

    try:
        sdn_bytes = _fetch_csv(SDN_URL, proxies, verify)
        alt_bytes = _fetch_csv(ALT_URL, proxies, verify)
        add_bytes = _fetch_csv(ADD_URL, proxies, verify)
        sdn_df = _parse_sdn(sdn_bytes)
        aliases_by_ent_num = _parse_alt(alt_bytes)
        addresses_by_ent_num = _parse_add(add_bytes)
    except Exception as e:
        counts["error_count"] += 1
        errors.append(f"{type(e).__name__}: {e}")
        base.complete_ingestion_run(
            client, ingestion_run_id=run["ingestion_run_id"], status="failed", counts=counts,
            error_summary="; ".join(errors), connector_version=CONNECTOR_VERSION,
            parser_version=PARSER_VERSION,
        )
        return {"ingestion_run_id": run["ingestion_run_id"], "status": "failed", "counts": counts, "errors": errors}

    sdn_snapshot, _ = base.find_or_save_raw_snapshot(
        client, source_id=source_row["source_id"], raw_bytes=sdn_bytes, request_url=SDN_URL,
        content_type="text/csv", connector_version=CONNECTOR_VERSION, parser_version=PARSER_VERSION,
        ext=".csv", is_demo=is_demo,
    )
    base.link_run_to_snapshot(client, ingestion_run_id=run["ingestion_run_id"],
                               raw_snapshot_id=sdn_snapshot["raw_snapshot_id"])
    alt_snapshot, _ = base.find_or_save_raw_snapshot(
        client, source_id=source_row["source_id"], raw_bytes=alt_bytes, request_url=ALT_URL,
        content_type="text/csv", connector_version=CONNECTOR_VERSION, parser_version=PARSER_VERSION,
        ext=".csv", is_demo=is_demo,
    )
    base.link_run_to_snapshot(client, ingestion_run_id=run["ingestion_run_id"],
                               raw_snapshot_id=alt_snapshot["raw_snapshot_id"])
    add_snapshot, _ = base.find_or_save_raw_snapshot(
        client, source_id=source_row["source_id"], raw_bytes=add_bytes, request_url=ADD_URL,
        content_type="text/csv", connector_version=CONNECTOR_VERSION, parser_version=PARSER_VERSION,
        ext=".csv", is_demo=is_demo,
    )
    base.link_run_to_snapshot(client, ingestion_run_id=run["ingestion_run_id"],
                               raw_snapshot_id=add_snapshot["raw_snapshot_id"])

    document, _ = base.save_as_document(
        client, theme_id=theme_id, source_id=source_row["source_id"],
        title=f"OFAC SDN List snapshot ({fetched_at})",
        extracted_text=f"OFAC SDN List, fetched_at={fetched_at}, {len(sdn_df)} entries",
        mime_type="text/csv", url=SDN_URL, is_demo=is_demo,
    )

    rows = sdn_df.iterrows() if max_records is None else list(sdn_df.iterrows())[:max_records]
    seen_external_ids = set()
    claims_created, match_candidates_created = [], []

    for row_number, row in rows:
        counts["fetched_count"] += 1
        external_record_id = row["ent_num"]
        seen_external_ids.add(external_record_id)
        aliases = aliases_by_ent_num.get(external_record_id, [])
        addresses = addresses_by_ent_num.get(external_record_id, [])
        record_hash = _record_hash(row["sdn_name"], row["program"], row["remarks"], aliases, addresses)

        try:
            existing_entry = client.select("geo_sanctions_entries", {
                "select": "*", "source_id": f"eq.{source_row['source_id']}",
                "external_record_id": f"eq.{external_record_id}", "limit": "1",
            })

            if existing_entry:
                entry = existing_entry[0]
                latest_version = client.select("geo_sanctions_entry_versions", {
                    "select": "*", "version_id": f"eq.{entry['latest_version_id']}", "limit": "1",
                })
                if latest_version and latest_version[0]["source_record_hash"] == record_hash:
                    # 変更なし: last_seen_atのみ更新し、新versionは作らない
                    client.update("geo_sanctions_entries", {"sanctions_entry_id": f"eq.{entry['sanctions_entry_id']}"},
                                  {"last_seen_at": fetched_at})
                    counts["duplicate_count"] += 1
                    continue
                change_type = "relisted" if entry["current_status"] == "delisted" else "amended"
            else:
                entry = client.insert("geo_sanctions_entries", [{
                    "source_id": source_row["source_id"], "external_record_id": external_record_id,
                    "first_seen_at": fetched_at, "last_seen_at": fetched_at, "current_status": "active",
                }])[0]
                change_type = "added"

            addresses_text = "; ".join(
                ", ".join(v for v in (a["address"], a["city_state_province_postal_code"], a["country"]) if v)
                for a in addresses
            )
            record_text = (f"ent_num={external_record_id}\nname={row['sdn_name']}\ntype={row['sdn_type']}\n"
                           f"program={row['program']}\naliases={'; '.join(aliases)}\n"
                           f"addresses={addresses_text}\nremarks={row['remarks']}")
            chunk_hash = hashlib.sha256(record_text.encode("utf-8")).hexdigest()
            chunk = client.insert("geo_document_chunks", [{
                "document_id": document["document_id"], "chunk_index": int(row_number),
                "section": external_record_id, "quoted_excerpt": record_text,
                "parser_version": PARSER_VERSION, "content_hash": chunk_hash,
            }])[0]

            # 直前versionのvalid_toを更新してから新versionを追加する（同一行を上書きしない）
            if existing_entry and entry.get("latest_version_id"):
                client.update("geo_sanctions_entry_versions", {"version_id": f"eq.{entry['latest_version_id']}"},
                              {"valid_to": fetched_at[:10]})

            new_version = client.insert("geo_sanctions_entry_versions", [{
                "sanctions_entry_id": entry["sanctions_entry_id"], "source_record_hash": record_hash,
                "raw_snapshot_id": sdn_snapshot["raw_snapshot_id"], "document_chunk_id": chunk["chunk_id"],
                "entity_name": row["sdn_name"], "aliases": aliases, "program": row["program"],
                "list_date": None,  # 基本SDN.csvには正式な発効日が含まれないため推定せずnullのまま
                "addresses": addresses,
                # 国籍・身分証明書番号はadd.csv同様の単純CSVでは提供されずsdn_advanced.xmlのみに
                # 含まれる（別タスク）。ここでは推定しない
                "nationality": None, "identifiers": [],
                "change_type": change_type, "observed_at": fetched_at, "valid_from": None,
            }])[0]
            client.update("geo_sanctions_entries", {"sanctions_entry_id": f"eq.{entry['sanctions_entry_id']}"}, {
                "latest_version_id": new_version["version_id"], "current_status": "active",
                "last_seen_at": fetched_at,
            })
            counts["inserted_count" if change_type == "added" else "updated_count"] += 1

            match_result = geo_entity_resolution.resolve_or_flag(client, row["sdn_name"])
            match_status = {"resolved": "exact_match_candidate", "ambiguous": "possible_match",
                            "unresolved": "no_match"}[match_result["status"]]
            matched_entity_id = match_result.get("entity_id")
            match_candidate = client.insert("geo_entity_match_candidates", [{
                "source_id": source_row["source_id"], "raw_snapshot_id": sdn_snapshot["raw_snapshot_id"],
                "external_entity_name": row["sdn_name"], "external_entity_aliases": aliases,
                "match_status": match_status, "matched_entity_id": matched_entity_id,
                "confidence": "高" if match_status == "exact_match_candidate" else "低",
            }])[0]
            match_candidates_created.append(match_candidate["match_candidate_id"])

            claim = geo_claim_service.create_claim(
                client, claim_text=f"取得日時点（{fetched_at}）でOFAC制裁リストに「{row['sdn_name']}」が"
                                    f"掲載されている（プログラム: {row['program']}）",
                information_class="observed_fact", origin_type="document", origin_id=document["document_id"],
                actor_id=requested_by, subject_entity_id=matched_entity_id, is_demo=is_demo,
            )
            geo_claim_service.link_evidence(client, claim_id=claim["claim_id"], evidence_role="supporting",
                                             actor_id=requested_by, document_chunk_id=chunk["chunk_id"])
            claims_created.append(claim["claim_id"])
        except Exception as e:
            # Phase A（Collection Reliability、2026-10-05）: 1万件規模のSDNリストを1件ずつ
            # HTTP往復で処理するため、途中でプロキシ/Supabase側の一時的なネットワーク障害が
            # 起きると、この1行のみ未捕捉例外でプロセス全体がクラッシュし、geo_ingestion_runs
            # がstatus=runningのまま復旧不能で残留する事象を実際に2回確認した。1レコード単位で
            # 捕捉し、以降のレコード処理を継続する（既存Raw Snapshotはimmutableなので次回実行で
            # このレコードだけ再処理すれば足りる）。
            counts["error_count"] += 1
            errors.append(f"{external_record_id}: {type(e).__name__}: {e}")
            continue

    # このRunは全件正常取得（completed）とみなし、消失レコードのみdelisted判定を行う
    active_entries = client.select("geo_sanctions_entries", {
        "select": "*", "source_id": f"eq.{source_row['source_id']}", "current_status": "eq.active",
    })
    delisted_count = 0
    if max_records is None:
        for entry in active_entries:
            if entry["external_record_id"] in seen_external_ids:
                continue
            latest_version = client.select("geo_sanctions_entry_versions", {
                "select": "*", "version_id": f"eq.{entry['latest_version_id']}", "limit": "1",
            })
            if latest_version:
                client.update("geo_sanctions_entry_versions", {"version_id": f"eq.{entry['latest_version_id']}"},
                              {"valid_to": fetched_at[:10]})
            new_version = client.insert("geo_sanctions_entry_versions", [{
                "sanctions_entry_id": entry["sanctions_entry_id"],
                "source_record_hash": latest_version[0]["source_record_hash"] if latest_version else "",
                "entity_name": latest_version[0]["entity_name"] if latest_version else "",
                "change_type": "delisted", "observed_at": fetched_at,
            }])[0]
            client.update("geo_sanctions_entries", {"sanctions_entry_id": f"eq.{entry['sanctions_entry_id']}"}, {
                "latest_version_id": new_version["version_id"], "current_status": "delisted",
            })
            delisted_count += 1

    status = ("completed" if max_records is None and counts["error_count"] == 0
               else "completed_with_warnings")
    next_scheduled_at = base.calculate_next_run("daily")
    base.complete_ingestion_run(
        client, ingestion_run_id=run["ingestion_run_id"], status=status, counts=counts,
        error_summary="; ".join(errors) if errors else None, next_scheduled_at=next_scheduled_at,
        connector_version=CONNECTOR_VERSION, parser_version=PARSER_VERSION,
    )
    # Phase A（Collection Reliability、2026-10-04）: is_demo=Trueのテスト実行がSource
    # Registryの鮮度指標を書き換えないようにする（全Connector共通対応）。
    if not is_demo:
        now_iso = datetime.now(timezone.utc).isoformat()
        client.update("geo_source_registry", {"source_id": f"eq.{source_row['source_id']}"}, {
            "last_ingestion_run_id": run["ingestion_run_id"], "last_successful_ingestion_at": now_iso,
            "failure_count": 0,
        })

    geo_audit.log_audit(client, actor=requested_by, action="ofac_ingestion_run",
                         target_table="geo_ingestion_runs", target_id=run["ingestion_run_id"],
                         detail={"status": status, "delisted_count": delisted_count, **counts})

    return {"ingestion_run_id": run["ingestion_run_id"], "status": status, "counts": counts,
            "claim_ids": claims_created, "match_candidate_ids": match_candidates_created,
            "delisted_count": delisted_count}
