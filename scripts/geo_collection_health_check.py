"""Phase A（Collection Reliability、2026-10-04）: 最小限のReliability Monitoring。

常駐Scheduler/Notificationを新設する代わりに、既存のgeo_source_registry（update_cadence＋
last_successful_ingestion_at）とgeo_external_signal_queries（last_run_at）・
geo_ingestion_runsから「止まっていないか」を人が確認できる最小限のCLIサマリを提供する。
新しいテーブル・UIは追加しない（既存データの読み取り専用集計のみ）。

使い方:
    python scripts/geo_collection_health_check.py
    python scripts/geo_collection_health_check.py --is-demo   # demo環境を確認する場合

終了コード: 0=全てcurrent/review_due_soon以内、1=いずれかがoverdue/failing/unknown。
"""
import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_freshness_service as freshness
from config_utils import load_config
from geo_db_client import SupabaseClient

# GDELT（News DOC API）はtheme横断のsource-neutral基盤としてgeo_source_registryへは
# 登録しない設計（geo_connector_gdelt.py docstring参照）ため、query単位でlast_run_atから
# 個別に鮮度を見る。daily cadence（geo_connector_base.UPDATE_CADENCE_DAYS["daily"]）。
_GDELT_CADENCE_DAYS = 1
_GDELT_CONNECTOR_TYPES = ("gdelt", "gdelt_doc_catchup", "gdelt_stream", "gdelt_stream_catchup")


def _parse_iso(ts: str) -> datetime:
    """Python 3.10のdatetime.fromisoformatは秒以下の桁数が3/6桁以外だと
    ValueErrorになるため、PostgRESTが返す任意桁数の小数秒を6桁へ正規化してから渡す。"""
    match = re.match(r"^(.*\.)(\d+)(\+.*)$", ts)
    if match:
        ts = match.group(1) + match.group(2).ljust(6, "0")[:6] + match.group(3)
    return datetime.fromisoformat(ts)


def _days_since(iso_ts: str | None) -> float | None:
    if not iso_ts:
        return None
    then = _parse_iso(iso_ts)
    return (datetime.now(timezone.utc) - then).total_seconds() / 86400


def _gdelt_query_status(days_since: float | None) -> str:
    if days_since is None:
        return "unknown"
    if days_since > _GDELT_CADENCE_DAYS * 10:
        return "failed"
    if days_since > _GDELT_CADENCE_DAYS * 3:
        return "overdue"
    if days_since > _GDELT_CADENCE_DAYS * 1.5:
        return "review_due_soon"
    return "current"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--is-demo", action="store_true")
    args = parser.parse_args()

    config = load_config()
    client = SupabaseClient(config)
    any_problem = False

    print("=== 構造化データConnector（geo_source_registry、connector_type設定済み） ===")
    sources = client.select("geo_source_registry", {
        "select": "name,connector_type,update_cadence,last_successful_ingestion_at,"
                  "last_failed_ingestion_at,failure_count",
        "connector_type": "not.is.null",
    })
    if not sources:
        print("  (該当なし)")
    for s in sources:
        verdict = freshness.classify_source_freshness(s)
        flag = "" if verdict["status"] in ("current", "review_due_soon") else "  <== 要確認"
        if flag:
            any_problem = True
        print(f"  {s['name']} [{s['connector_type']}] cadence={s.get('update_cadence')}: "
              f"{verdict['status']} (経過{verdict['days_since_last_success']}日, "
              f"failure_count={verdict['failure_count']}){flag}")

    print("\n=== GDELT News DOC（geo_external_signal_queries、is_demo={} ） ===".format(args.is_demo))
    queries = client.select("geo_external_signal_queries", {
        "select": "name,enabled,last_run_at", "enabled": "eq.true",
    })
    for q in queries:
        days = _days_since(q["last_run_at"])
        status = _gdelt_query_status(days)
        flag = "" if status in ("current", "review_due_soon") else "  <== 要確認"
        if flag:
            any_problem = True
        days_str = f"{days:.1f}" if days is not None else "N/A"
        print(f"  {q['name']}: {status} (経過{days_str}日, last_run_at={q['last_run_at']}){flag}")

    print("\n=== パイプライン全体の最終実行（geo_ingestion_runs、is_demo={}） ===".format(args.is_demo))
    for ct in ("gdelt", "gdelt_doc_catchup", "gdelt_stream", "gdelt_stream_catchup"):
        rows = client.select("geo_ingestion_runs", {
            "select": "status,started_at", "connector_type": f"eq.{ct}",
            "is_demo": "eq.true" if args.is_demo else "eq.false",
            "order": "started_at.desc", "limit": "1",
        })
        if not rows:
            print(f"  {ct}: 実行履歴なし  <== 要確認")
            any_problem = True
            continue
        days = _days_since(rows[0]["started_at"])
        flag = "  <== 要確認" if (days is not None and days > _GDELT_CADENCE_DAYS * 3) or rows[0]["status"] == "failed" else ""
        if flag:
            any_problem = True
        print(f"  {ct}: 最終status={rows[0]['status']}, 経過{days:.1f}日{flag}")

    print()
    if any_problem:
        print("判定: 要確認項目あり（上記<== 要確認を参照）")
        return 1
    print("判定: 全項目正常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
