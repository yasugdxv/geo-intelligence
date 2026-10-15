"""地政学ナレッジ＆インテリジェンスDB Phase2A: Connector手動実行CLI。

常駐Schedulerは導入しない（docs/geopolitical-ingestion-operations.md参照）。
Windows Task Scheduler・cron・Azure Scheduler等から本コマンドを定期実行する運用を想定する。

使い方:
    python geo_connector_cli.py world_bank --theme-id <uuid> --entity-id <uuid> --country-code JP
    python geo_connector_cli.py ofac --theme-id <uuid> [--max-records 50]
"""
import argparse
import sys

from geo_db_client import SupabaseClient
from config_utils import load_config

import geo_connector_federal_register
import geo_connector_gdelt
import geo_connector_html_list
import geo_connector_gdelt_stream
import geo_connector_imf
import geo_connector_manifesto
import geo_connector_ofac
import geo_connector_rss
import geo_connector_sanctions_xml
import geo_connector_ukmto
import geo_connector_un_comtrade
import geo_connector_world_bank
import geo_connector_wto
import geo_episode_cluster_service
import geo_episode_split_candidate_service
import geo_situation_link_service
import geo_statement_candidate_service


def main():
    parser = argparse.ArgumentParser(description="地政学ナレッジDB Connector手動実行")
    subparsers = parser.add_subparsers(dest="connector", required=True)

    wb_parser = subparsers.add_parser("world_bank")
    wb_parser.add_argument("--theme-id", required=True)
    wb_parser.add_argument("--entity-id", required=True)
    wb_parser.add_argument("--country-code", required=True)
    wb_parser.add_argument("--date-range", default="2015:2026")
    wb_parser.add_argument("--is-demo", action="store_true")

    ofac_parser = subparsers.add_parser("ofac")
    ofac_parser.add_argument("--theme-id", required=True)
    ofac_parser.add_argument("--max-records", type=int, default=None)
    ofac_parser.add_argument("--is-demo", action="store_true")

    imf_parser = subparsers.add_parser("imf")
    imf_parser.add_argument("--theme-id", required=True)
    imf_parser.add_argument("--entity-id", required=True)
    imf_parser.add_argument("--country-code", required=True)
    imf_parser.add_argument("--is-demo", action="store_true")

    un_comtrade_parser = subparsers.add_parser("un_comtrade")
    un_comtrade_parser.add_argument("--theme-id", required=True)
    un_comtrade_parser.add_argument("--reporter-entity-id", required=True)
    un_comtrade_parser.add_argument("--partner-entity-id", default=None)
    un_comtrade_parser.add_argument("--is-demo", action="store_true")

    fr_parser = subparsers.add_parser("federal-register")
    fr_parser.add_argument("--is-demo", action="store_true")

    sanctions_parser = subparsers.add_parser("sanctions")
    sanctions_parser.add_argument("--list", action="append", default=None, help="un_consolidated / uk_fcdo（省略=全部）")
    sanctions_parser.add_argument("--is-demo", action="store_true")

    html_parser = subparsers.add_parser("html-list")
    html_parser.add_argument("--source", action="append", default=None, help="config/geo_direct_sources.yamlのhtml_list_sourcesのキー（省略=全部）")
    html_parser.add_argument("--is-demo", action="store_true")

    ukmto_parser = subparsers.add_parser("ukmto")
    ukmto_parser.add_argument("--ignore-min-interval", action="store_true", help="検証専用。30分の最小間隔を無視する（通常は使わない）")
    ukmto_parser.add_argument("--is-demo", action="store_true")

    rss_parser = subparsers.add_parser("rss")
    rss_parser.add_argument("--source", action="append", default=None, help="config/geo_direct_sources.yamlのキー（省略=全RSS Source）")
    rss_parser.add_argument("--is-demo", action="store_true")

    wto_parser = subparsers.add_parser("wto")
    wto_parser.add_argument("--theme-id", required=True)
    wto_parser.add_argument("--entity-id", required=True)
    wto_parser.add_argument("--is-demo", action="store_true")

    manifesto_parser = subparsers.add_parser("manifesto")
    manifesto_parser.add_argument("--theme-id", required=True)
    manifesto_parser.add_argument("--max-records", type=int, default=None)
    manifesto_parser.add_argument("--is-demo", action="store_true")

    gdelt_parser = subparsers.add_parser("gdelt")
    gdelt_parser.add_argument("--query-id", required=True)
    gdelt_parser.add_argument("--since", default=None)
    gdelt_parser.add_argument("--until", default=None)
    gdelt_parser.add_argument("--is-demo", action="store_true")

    gdelt_batch_parser = subparsers.add_parser("gdelt-batch")
    gdelt_batch_parser.add_argument("--is-demo", action="store_true")

    gdelt_historical_parser = subparsers.add_parser("gdelt-historical")
    gdelt_historical_parser.add_argument("--query-id", default=None,
                                          help="省略時は有効な全queryを対象にする（Initial Load向け）")
    gdelt_historical_parser.add_argument("--since", required=True)
    gdelt_historical_parser.add_argument("--until", required=True)
    gdelt_historical_parser.add_argument("--window-hours", type=int, default=geo_connector_gdelt.DEFAULT_DOC_WINDOW_HOURS)
    gdelt_historical_parser.add_argument("--promote-to-checkpoint", action="store_true")
    gdelt_historical_parser.add_argument("--is-demo", action="store_true")

    gdelt_stream_parser = subparsers.add_parser("gdelt-stream")
    gdelt_stream_parser.add_argument("--since", default=None)
    gdelt_stream_parser.add_argument("--until", default=None)
    gdelt_stream_parser.add_argument("--max-batches", type=int, default=1)
    gdelt_stream_parser.add_argument("--is-demo", action="store_true")

    gdelt_stream_catchup_parser = subparsers.add_parser("gdelt-stream-catchup")
    gdelt_stream_catchup_parser.add_argument("--since", default=None)
    gdelt_stream_catchup_parser.add_argument("--until", default=None)
    gdelt_stream_catchup_parser.add_argument(
        "--batches-per-call", type=int, default=geo_connector_gdelt_stream.DEFAULT_CATCHUP_BATCHES_PER_CALL)
    gdelt_stream_catchup_parser.add_argument(
        "--max-calls", type=int, default=geo_connector_gdelt_stream.DEFAULT_CATCHUP_MAX_CALLS)
    gdelt_stream_catchup_parser.add_argument("--batch-maturity-hours", type=int, default=None)
    gdelt_stream_catchup_parser.add_argument("--allow-immature-batches", action="store_true")
    gdelt_stream_catchup_parser.add_argument("--promote-to-checkpoint", action="store_true")
    gdelt_stream_catchup_parser.add_argument("--is-demo", action="store_true")

    subparsers.add_parser("cluster-actor-episodes")

    link_situations_parser = subparsers.add_parser("link-situations")
    link_situations_parser.add_argument("--is-demo", action="store_true")

    split_candidates_parser = subparsers.add_parser("split-candidates")
    split_candidates_parser.add_argument("--is-demo", action="store_true")

    statement_candidates_parser = subparsers.add_parser("generate-statement-candidates")
    statement_candidates_parser.add_argument("--is-demo", action="store_true")

    args = parser.parse_args()
    config = load_config()
    client = SupabaseClient(config)

    if args.connector == "world_bank":
        result = geo_connector_world_bank.run_world_bank_ingestion(
            client, theme_id=args.theme_id, entity_id=args.entity_id, country_code=args.country_code,
            date_range=args.date_range, requested_by="cli", is_demo=args.is_demo,
        )
    elif args.connector == "federal-register":
        r = geo_connector_federal_register.run_federal_register_collection(client, requested_by="cli", is_demo=args.is_demo)
        print(r)
        return 0 if r.get("status") == "completed" else 1
    elif args.connector == "sanctions":
        import geo_direct_collection_common as _common
        lists = args.list or list(_common.load_direct_config().get("sanctions_lists", {}))
        ok = True
        for key in lists:
            r = geo_connector_sanctions_xml.run_sanctions_collection(client, list_key=key, requested_by="cli", is_demo=args.is_demo)
            print(f"{key}: {r}")
            ok = ok and r.get("status") == "completed"
        return 0 if ok else 1
    elif args.connector == "html-list":
        results = geo_connector_html_list.run_all_html_list_sources(client, requested_by="cli", is_demo=args.is_demo, only=args.source)
        for key, r in results.items():
            print(f"{key}: {r}")
        return 0 if all(r.get("status") == "completed" for r in results.values()) else 1
    elif args.connector == "ukmto":
        r = geo_connector_ukmto.run_ukmto_collection(client, requested_by="cli", is_demo=args.is_demo, ignore_min_interval=args.ignore_min_interval)
        print(r)
        return 0 if r.get("status") in ("completed", "completed_with_warnings", "skipped_too_soon") else 1
    elif args.connector == "rss":
        results = geo_connector_rss.run_all_rss_sources(client, requested_by="cli", is_demo=args.is_demo, only=args.source)
        for key, r in results.items():
            print(f"{key}: {r}")
        return 0 if all(r.get("status") == "completed" for r in results.values()) else 1
    elif args.connector == "ofac":
        result = geo_connector_ofac.run_ofac_ingestion(
            client, theme_id=args.theme_id, max_records=args.max_records, requested_by="cli",
            is_demo=args.is_demo,
        )
    elif args.connector == "imf":
        result = geo_connector_imf.run_imf_ingestion(
            client, theme_id=args.theme_id, entity_id=args.entity_id, country_code=args.country_code,
            requested_by="cli", is_demo=args.is_demo,
        )
    elif args.connector == "un_comtrade":
        result = geo_connector_un_comtrade.run_un_comtrade_ingestion(
            client, theme_id=args.theme_id, reporter_entity_id=args.reporter_entity_id,
            partner_entity_id=args.partner_entity_id, requested_by="cli", is_demo=args.is_demo,
        )
    elif args.connector == "wto":
        result = geo_connector_wto.run_wto_ingestion(
            client, theme_id=args.theme_id, entity_id=args.entity_id, requested_by="cli",
            is_demo=args.is_demo,
        )
    elif args.connector == "manifesto":
        result = geo_connector_manifesto.run_manifesto_ingestion(
            client, theme_id=args.theme_id, max_records=args.max_records, requested_by="cli",
            is_demo=args.is_demo,
        )
    elif args.connector == "gdelt":
        rows = client.select("geo_external_signal_queries", {
            "select": "*", "query_id": f"eq.{args.query_id}", "limit": "1",
        })
        if not rows:
            parser.error(f"query_idが見つかりません: {args.query_id}")
            return 1
        result = geo_connector_gdelt.run_gdelt_ingestion(
            client, query_row=rows[0], run_type="cli", requested_by="cli", is_demo=args.is_demo,
            since=args.since, until=args.until,
        )
    elif args.connector == "gdelt-batch":
        results = geo_connector_gdelt.run_enabled_external_signal_queries(
            client, provider="gdelt", run_type="cli", requested_by="cli", is_demo=args.is_demo,
        )
        for r in results:
            print(f"query={r.get('query_name')} status={r['status']} "
                  f"windows_processed={r.get('windows_processed')} "
                  f"total_article_result_count={r.get('total_article_result_count')} "
                  f"saturated_windows={len(r.get('saturated_windows') or [])} "
                  f"completed_through={r.get('completed_through')}")
        return 0 if all(r["status"] in ("completed", "completed_with_warnings") for r in results) else 1
    elif args.connector == "gdelt-historical":
        if args.query_id:
            rows = client.select("geo_external_signal_queries", {
                "select": "*", "query_id": f"eq.{args.query_id}", "limit": "1",
            })
            if not rows:
                parser.error(f"query_idが見つかりません: {args.query_id}")
                return 1
            result = geo_connector_gdelt.run_gdelt_doc_catchup(
                client, query_row=rows[0], since=args.since, until=args.until,
                window_hours=args.window_hours, promote_to_checkpoint=args.promote_to_checkpoint,
                requested_by="cli", is_demo=args.is_demo,
            )
            print(f"query_id={result['query_id']} windows_processed={result['windows_processed']} "
                  f"total_article_result_count={result['total_article_result_count']} "
                  f"saturated_windows={len(result['saturated_windows'])} "
                  f"completed_through={result['completed_through']} "
                  f"checkpoint_scope={result['checkpoint_scope']} "
                  f"promotion_rejected_reason={result['promotion_rejected_reason']}")
            return 0 if not result["saturated_windows"] else 1
        result = geo_connector_gdelt.run_gdelt_doc_catchup_all_enabled(
            client, since=args.since, until=args.until, window_hours=args.window_hours,
            promote_to_checkpoint=args.promote_to_checkpoint, requested_by="cli", is_demo=args.is_demo,
        )
        for r in result["query_results"]:
            print(f"query_id={r['query_id']} windows_processed={r['windows_processed']} "
                  f"total_article_result_count={r['total_article_result_count']} "
                  f"saturated_windows={len(r['saturated_windows'])} "
                  f"completed_through={r['completed_through']} checkpoint_scope={r['checkpoint_scope']} "
                  f"promotion_rejected_reason={r['promotion_rejected_reason']}")
        return 0 if result["all_clean"] else 1
    elif args.connector == "gdelt-stream-catchup":
        result = geo_connector_gdelt_stream.run_gdelt_stream_catchup(
            client, since=args.since, until=args.until, batches_per_call=args.batches_per_call,
            max_calls=args.max_calls, batch_maturity_hours=args.batch_maturity_hours,
            bypass_maturity_clamp=args.allow_immature_batches, promote_to_checkpoint=args.promote_to_checkpoint,
            requested_by="cli", is_demo=args.is_demo,
        )
        print(f"calls_made={result['calls_made']} remaining_batch_count={result['remaining_batch_count']} "
              f"stopped_reason={result['stopped_reason']} completed_through={result['completed_through']} "
              f"checkpoint_scope={result['checkpoint_scope']} "
              f"promotion_rejected_reason={result['promotion_rejected_reason']}")
        if result["stopped_reason"] != "caught_up" or result["promotion_rejected_reason"]:
            return 1
        return 0
    elif args.connector == "gdelt-stream":
        result = geo_connector_gdelt_stream.run_gdelt_structured_ingestion(
            client, since=args.since, until=args.until, max_batches=args.max_batches,
            requested_by="cli", is_demo=args.is_demo,
        )
        for b in result["batches"]:
            print(f"batch={b['batch_timestamp']} status={b['status']} counts={b['counts']} "
                  f"resolved_events={b['resolved_event_count']} "
                  f"retryable={b['unresolved_event_ids_retryable']} terminal={b['unresolved_event_ids_terminal']} "
                  f"actor={b['actor_candidates']} media={b['media_candidates']}")
        return 0 if all(b["status"] in ("completed", "completed_with_warnings") for b in result["batches"]) else 1
    elif args.connector == "cluster-actor-episodes":
        result = geo_episode_cluster_service.recluster_affected_keys(client, actor_episode_candidate_ids=None)
        print(f"keys_processed={result['keys_processed']} "
              f"total_rebuilt_clusters={result['total_rebuilt_clusters']} "
              f"total_clustered_candidates={result['total_clustered_candidates']}")
        return 0
    elif args.connector == "link-situations":
        result = geo_situation_link_service.recompute_situation_links(client, is_demo=args.is_demo)
        print(f"candidate_links_created={result['candidate_links_created']} "
              f"same_situation_candidate_count={result['same_situation_candidate_count']} "
              f"related_context_count={result['related_context_count']} "
              f"skipped_frozen_pair={result['skipped_frozen_pair']} "
              f"frozen_links_preserved={result['frozen_links_preserved']}")
        return 0
    elif args.connector == "split-candidates":
        result = geo_episode_split_candidate_service.generate_split_candidates(client, is_demo=args.is_demo)
        print(f"clusters_processed={result['clusters_processed']} "
              f"clusters_skipped_parent_rejected={result['clusters_skipped_parent_rejected']} "
              f"split_group_rows={result['split_group_rows']} "
              f"artifact_group_rows={result['artifact_group_rows']} "
              f"outlier_rows={result['outlier_rows']} "
              f"inserted={result['inserted']} deleted={result['deleted']}")
        return 0
    elif args.connector == "generate-statement-candidates":
        result = geo_statement_candidate_service.generate_statement_candidates(client, is_demo=args.is_demo)
        print(f"annotations_processed={result['annotations_processed']} "
              f"inserted={result['inserted']} deleted={result['deleted']} "
              f"statement_kind_counts={result['statement_kind_counts']}")
        return 0
    else:
        parser.error(f"未対応のconnectorです: {args.connector}")
        return 1

    print(f"status={result['status']} counts={result.get('counts')}")
    return 0 if result["status"] in ("completed", "completed_with_warnings") else 1


if __name__ == "__main__":
    sys.exit(main())
