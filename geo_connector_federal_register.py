"""GEO_DIRECT_COLLECTION_V1: 米連邦官報（Federal Register）公式APIのDirect Collector。

全文書を無条件に保存せず、config/geo_direct_sources.yamlのqueries（機関・検索語）で決定的に絞る。
AI filteringは使わない。保存先は geo_external_signals（provider='official'）。承認ゲートはGeo Source Registry。

使い方: python geo_connector_cli.py federal-register [--is-demo]
"""
from datetime import datetime, timedelta, timezone

import geo_connector_base as base
import geo_direct_collection_common as common
import geo_external_signal_service as signal_service

CONNECTOR_TYPE = "federal_register"
CONNECTOR_VERSION = "geo-federal-register-1.0"
PARSER_VERSION = "geo-federal-register-parser-1.0"
FIELDS = ["title", "abstract", "html_url", "publication_date", "document_number", "type", "agency_names"]


def build_params(query: dict, *, since: str, per_page: int) -> dict:
    params = {"per_page": str(per_page), "order": "newest", "fields[]": FIELDS,
              "conditions[publication_date][gte]": since}
    if query.get("agencies"):
        params["conditions[agencies][]"] = list(query["agencies"])
    if query.get("term"):
        params["conditions[term]"] = query["term"]
    return params


def _parse_pub_date(value: str | None) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc) if value else None
    except ValueError:
        return None


def collect_documents(cfg: dict, *, now: datetime | None = None, fetch=None) -> tuple[dict, dict]:
    """全queryを実行し、html_url単位に統合した文書dictと、queryごとの取得件数を返す。"""
    fetch = fetch or (lambda params: common.http_get(cfg["api_url"], params=params, accept="application/json").json())
    since = ((now or datetime.now(timezone.utc)) - timedelta(days=int(cfg.get("lookback_days", 14)))).strftime("%Y-%m-%d")
    docs, per_query = {}, {}
    for name, query in cfg["queries"].items():
        payload = fetch(build_params(query, since=since, per_page=int(cfg.get("per_page", 100))))
        results = payload.get("results", [])
        per_query[name] = {"count_total": payload.get("count"), "fetched": len(results)}
        for d in results:
            url = d.get("html_url")
            if not url:
                continue
            e = docs.setdefault(url, {**d, "matched_queries": []})
            for k, v in d.items():                      # 先に当たったqueryで欠けていた項目（abstract等）は後のqueryで補う
                if not e.get(k) and v:
                    e[k] = v
            e["matched_queries"].append(name)
    return docs, per_query


def run_federal_register_collection(client, *, requested_by: str = "system", is_demo: bool = False,
                                    config: dict = None, run_type: str = "manual", fetch=None) -> dict:
    cfg = (config or common.load_direct_config()).get("federal_register")
    if not cfg:
        raise common.SourceNotReadyError("federal_register の設定がありません")
    source_row = common.resolve_source(client, cfg["registry_domain"])
    base.check_automation_allowed(source_row)

    run = base.create_ingestion_run(client, source_id=source_row["source_id"], connector_type=CONNECTOR_TYPE,
                                     run_type=run_type if run_type in ("manual", "cli", "scheduled_external") else "manual",
                                     requested_by=requested_by,
                                     request_parameters={"api_url": cfg["api_url"], "queries": list(cfg["queries"])},
                                     is_demo=is_demo)
    counts = {"fetched_count": 0, "inserted_count": 0, "updated_count": 0, "skipped_count": 0, "error_count": 0}
    try:
        docs, per_query = collect_documents(cfg, fetch=fetch)
        counts["fetched_count"] = len(docs)
        normalized = []
        for url, d in docs.items():
            title = common.clean_text(d.get("title"), 500)
            if not title or common.passes_policy(title, None, {"exclude_any": cfg.get("exclude_any")}) is False:
                counts["skipped_count"] += 1
                continue
            normalized.append(common.build_signal(
                source_row=source_row, provider="official", title=title, url=url,
                summary=common.clean_text(d.get("abstract")), published_at=_parse_pub_date(d.get("publication_date")),
                language=cfg.get("language"), collector=CONNECTOR_TYPE, connector_version=CONNECTOR_VERSION,
                endpoint=cfg["api_url"], ingestion_run_id=run["ingestion_run_id"],
                provider_record_id=d.get("document_number"),
                extra_metadata={"document_number": d.get("document_number"), "document_type": d.get("type"),
                                "agencies": d.get("agency_names"), "matched_queries": d["matched_queries"]}))
        result = signal_service.bulk_upsert_signals(client, normalized, provider="official", is_demo=is_demo)
        counts["inserted_count"], counts["updated_count"] = result["inserted"], result["updated"]
        latest = max((n["published_at"] for n in normalized if n["published_at"]), default=None)
        common.finish_run(client, run, status="completed", counts=counts, error_summary=None,
                          connector_version=CONNECTOR_VERSION, parser_version=PARSER_VERSION)
        common.record_source_outcome(client, source_row, run, ok=True, is_demo=is_demo)
        return {"status": "completed", "source_id": source_row["source_id"], "latest_published_at": latest,
                "per_query": per_query, **counts, "ingestion_run_id": run["ingestion_run_id"]}
    except Exception as e:  # noqa: BLE001 — Runを必ず閉じる
        counts["error_count"] += 1
        common.finish_run(client, run, status="failed", counts=counts, error_summary=f"{type(e).__name__}: {e}"[:500],
                          connector_version=CONNECTOR_VERSION, parser_version=PARSER_VERSION)
        common.record_source_outcome(client, source_row, run, ok=False, is_demo=is_demo)
        raise
