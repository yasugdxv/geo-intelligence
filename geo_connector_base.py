"""地政学ナレッジ＆インテリジェンスDB Phase2A: 外部Connector共通処理。

クラス階層ではなく関数群として実装する（既存geo_official_crawler.py/geo_documents_store.pyと
同じスタイル）。Connectorごとに以下の流れでこれらの関数を呼び出す:

check_automation_allowed → create_ingestion_run → fetch(Connector固有)
→ find_or_save_raw_snapshot → link_run_to_snapshot → save_as_document
→ (Connector固有のchunking/parse/normalize/resolve_entities/generate_claim_candidates)
→ complete_ingestion_run

Raw Snapshotは原則Immutable。UPDATE操作はここでは一切実装しない。
"""
import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import geo_audit
import geo_documents_store
from config_utils import upload_base

RAW_SNAPSHOT_ROOT = upload_base() / "geo" / "raw_snapshots"

UPDATE_CADENCE_DAYS = {
    "daily": 1, "weekly": 7, "monthly": 30, "quarterly": 91, "annual": 365,
}


class ConnectorDisabledError(RuntimeError):
    pass


def check_automation_allowed(source_row: dict) -> None:
    """3つの独立したゲートをすべて検証する（Phase2B）。
    automation_allowed: terms/steward承認（コードから絶対に自動変更しない）。
    connector_enabled: 運用者による一時停止/再開スイッチ。
    credential_configured: APIキーが実際に利用可能か（sync_credential_statusが都度再評価する）。
    無認証Connectorはcredential_configuredを触らず既定trueのまま「該当しない」を意味する。"""
    if not source_row.get("automation_allowed"):
        raise ConnectorDisabledError(
            f"ソース「{source_row.get('name')}」はautomation_allowed=falseのため自動取得できません"
        )
    if not source_row.get("connector_enabled", True):
        raise ConnectorDisabledError(
            f"ソース「{source_row.get('name')}」はconnector_enabled=falseのため一時停止中です"
        )
    if source_row.get("credential_configured") is False:
        raise ConnectorDisabledError(
            f"ソース「{source_row.get('name')}」はcredential_configured=falseのためAPIキーが未設定です"
        )


def resolve_credential(config: dict, credential_key: str) -> str | None:
    """config.jsonのcredential reference（環境変数名）からOS環境変数を引き、実際の値を返す。
    config.json自体には実キー値を一切保存しない（connector_credentials.{key}.env_varのみ保持する）。"""
    ref = config.get("connector_credentials", {}).get(credential_key, {})
    env_var = ref.get("env_var")
    return os.environ.get(env_var) if env_var else None


def sync_credential_status(client, *, source_id: str, config: dict, credential_key: str) -> bool:
    """credential_configuredを都度再評価して書き込む（automation_allowedには一切触れない）。"""
    configured = resolve_credential(config, credential_key) is not None
    client.update("geo_source_registry", {"source_id": f"eq.{source_id}"},
                  {"credential_configured": configured})
    return configured


def create_ingestion_run(client, *, source_id: str, connector_type: str, run_type: str = "manual",
                          requested_by: str = None, request_parameters: dict = None,
                          is_demo: bool = False) -> dict:
    return client.insert("geo_ingestion_runs", [{
        "source_id": source_id, "connector_type": connector_type, "run_type": run_type,
        "status": "running", "requested_by": requested_by, "request_parameters": request_parameters,
        "is_demo": is_demo,
    }])[0]


def complete_ingestion_run(client, *, ingestion_run_id: str, status: str, counts: dict = None,
                            error_summary: str = None, next_scheduled_at: str = None,
                            connector_version: str = None, parser_version: str = None,
                            source_vintage: str = None) -> None:
    """SupabaseClient.updateはPrefer: return=minimalのため更新後の行を返さない。
    呼び出し元で必要なら別途geo_ingestion_runsをselectし直すこと。"""
    if status not in ("completed", "completed_with_warnings", "failed", "cancelled"):
        raise ValueError(f"未対応のstatusです: {status}")
    patch = {
        "status": status, "completed_at": datetime.now(timezone.utc).isoformat(),
        "error_summary": error_summary, "next_scheduled_at": next_scheduled_at,
        "connector_version": connector_version, "parser_version": parser_version,
        "source_vintage": source_vintage,
    }
    patch.update(counts or {})
    client.update("geo_ingestion_runs", {"ingestion_run_id": f"eq.{ingestion_run_id}"}, patch)
    geo_audit.log_audit(client, actor="system", action="ingestion_run_complete",
                         target_table="geo_ingestion_runs", target_id=ingestion_run_id,
                         detail={"status": status, **(counts or {})})


def get_ingestion_run(client, ingestion_run_id: str) -> dict | None:
    rows = client.select("geo_ingestion_runs", {
        "select": "*", "ingestion_run_id": f"eq.{ingestion_run_id}", "limit": "1",
    })
    return rows[0] if rows else None


def find_or_save_raw_snapshot(client, *, source_id: str, raw_bytes: bytes, request_url: str = None,
                               request_parameters: dict = None, response_headers: dict = None,
                               response_status: int = None, content_type: str = None,
                               source_vintage: str = None, connector_version: str = None,
                               parser_version: str = None, ext: str = ".raw",
                               is_demo: bool = False) -> tuple[dict, bool]:
    """content_hashで既存Snapshotを探し、無ければ保存する（Immutable、既存行はUPDATEしない）。
    戻り値: (snapshot行, is_new)"""
    content_hash = geo_documents_store.compute_content_hash(raw_bytes)
    existing = client.select("geo_raw_snapshots", {
        "select": "*", "content_hash": f"eq.{content_hash}", "limit": "1",
    })
    if existing:
        return existing[0], False

    RAW_SNAPSHOT_ROOT.mkdir(parents=True, exist_ok=True)
    storage_path = RAW_SNAPSHOT_ROOT / f"{content_hash}{ext}"
    storage_path.write_bytes(raw_bytes)

    row = client.insert("geo_raw_snapshots", [{
        "source_id": source_id, "request_url": request_url, "request_parameters": request_parameters,
        "response_headers": response_headers, "response_status": response_status,
        "content_type": content_type, "content_hash": content_hash, "storage_path": str(storage_path),
        "source_vintage": source_vintage, "connector_version": connector_version,
        "parser_version": parser_version, "is_demo": is_demo,
    }])[0]
    return row, True


def link_run_to_snapshot(client, *, ingestion_run_id: str, raw_snapshot_id: str,
                          role: str = "primary_payload") -> dict:
    if role not in ("primary_payload", "pagination_page", "error_response"):
        raise ValueError(f"未対応のroleです: {role}")
    return client.insert("geo_ingestion_run_snapshots", [{
        "ingestion_run_id": ingestion_run_id, "raw_snapshot_id": raw_snapshot_id, "role": role,
    }])[0]


def save_as_document(client, *, theme_id: str, source_id: str, title: str, extracted_text: str,
                      mime_type: str, url: str = None, published_at: str = None,
                      is_demo: bool = False) -> tuple[dict, bool]:
    """Raw Snapshotの内容をgeo_documents（1件の論理文書）へ変換する。
    chunk化は呼び出し元が行う（World Bankは既存geo_claim_service.chunk_document、
    OFACはレコード単位の専用chunkingを使うため、ここでは共通化しない）。"""
    content_hash = geo_documents_store.compute_content_hash(extracted_text.encode("utf-8"))
    existing = client.select("geo_documents", {"select": "*", "content_hash": f"eq.{content_hash}", "limit": "1"})
    if existing:
        return existing[0], False

    row = client.insert("geo_documents", [{
        "theme_id": theme_id, "source_id": source_id, "title": title, "url": url,
        "canonical_url": url, "published_at": published_at, "content_hash": content_hash,
        "mime_type": mime_type, "extracted_text": extracted_text, "fetch_status": "成功",
        "parser_version": "geo-connector-1.0", "is_mock": False, "is_demo": is_demo,
    }])[0]
    return row, True


def set_reference_availability(client, *, source_id: str, reference_availability: str,
                                actor_id: str) -> dict:
    """Source単位でReference Policyを設定する（Phase2B-2）。内容の正誤判定（Human Review）ではなく、
    Sourceの信頼性・利用条件についての運用判断。既存Claimのretrieval_statusも一括再同期するが、
    現在available/restrictedのものだけを対象とする（deprecated/archivedは個別に明示された除外理由が
    あるため、Source側の方針転換だけで復活させない）。"""
    if reference_availability not in ("available", "restricted"):
        raise ValueError(f"未対応のreference_availabilityです: {reference_availability}")

    now_iso = datetime.now(timezone.utc).isoformat()
    client.update("geo_source_registry", {"source_id": f"eq.{source_id}"}, {
        "reference_availability": reference_availability,
        "reference_availability_set_by": actor_id,
        "reference_availability_set_at": now_iso,
    })

    affected = client.select("geo_claims", {
        "select": "claim_id", "source_id": f"eq.{source_id}", "retrieval_status": "in.(available,restricted)",
    })
    for row in affected:
        client.update("geo_claims", {"claim_id": f"eq.{row['claim_id']}"},
                      {"retrieval_status": reference_availability})

    geo_audit.log_audit(client, actor=actor_id, action="source_reference_availability_change",
                         target_table="geo_source_registry", target_id=source_id,
                         detail={"reference_availability": reference_availability,
                                 "resynced_claim_count": len(affected)})

    rows = client.select("geo_source_registry", {"select": "*", "source_id": f"eq.{source_id}", "limit": "1"})
    return rows[0] if rows else None


def calculate_next_run(update_cadence: str, from_time: datetime = None) -> str:
    """update_cadence（daily/weekly/monthly/quarterly/annual）から次回実行予定時刻を計算する。
    実行はしない（DBに記録するのみ、Scheduler方針参照）。"""
    base = from_time or datetime.now(timezone.utc)
    days = UPDATE_CADENCE_DAYS.get(update_cadence, 30)
    return (base + timedelta(days=days)).isoformat()
