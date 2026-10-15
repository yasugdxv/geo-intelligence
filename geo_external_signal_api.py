"""地政学ナレッジ＆インテリジェンスDB Phase1: Geopolitical News DB（External Signal）APIルーター。

既存route構造を踏襲し、/api/geo/external-signals/* に独立してマウントする。既存api_server.pyの
変更は「このモジュールのimportとinclude_routerの2行追加」のみ（既存geo_intelligence_gap_api.py
分離時と同じ方針）。

News DB検索（GET/POST search, summary, topics）はDBに保存済みのExternal Signalsに対してのみ
行い、GDELT APIを直接叩かない（Ingestionとの責務分離）。GDELTを実際に叩くのは
POST /fetch（管理用手動ingestion、登録済みquery_id必須）のみ。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

import geo_connector_gdelt
import geo_gdelt_rate_limit
import geo_external_signal_service as svc

_config = load_config()
_client = SupabaseClient(_config)

geo_external_signal_router = APIRouter(prefix="/api/geo/external-signals")


# ---------------------------------------------------------------------------
# Signals（Latest / Search）
# ---------------------------------------------------------------------------

@geo_external_signal_router.get("")
def list_external_signals(theme_id: str | None = None, provider: str | None = None,
                           source_country: str | None = None, source_domain: str | None = None,
                           limit: int = 50, offset: int = 0):
    result = svc.search_stored_signals(
        _client, theme_id=theme_id, provider=provider, country=source_country,
        source_domain=source_domain, limit=limit, offset=offset)
    return result


class SearchExternalSignalsRequest(BaseModel):
    text: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    country: str | None = None
    provider: str | None = None
    query_id: str | None = None
    theme_id: str | None = None
    source_domain: str | None = None
    limit: int = 50
    offset: int = 0


@geo_external_signal_router.post("/search")
def search_external_signals(req: SearchExternalSignalsRequest):
    return svc.search_stored_signals(
        _client, text=req.text, date_from=req.date_from, date_to=req.date_to,
        country=req.country, provider=req.provider, query_id=req.query_id,
        theme_id=req.theme_id, source_domain=req.source_domain,
        limit=req.limit, offset=req.offset)


@geo_external_signal_router.get("/summary")
def get_external_signal_summary(theme_id: str | None = None):
    return svc.summarize_signals(_client, theme_id=theme_id)


@geo_external_signal_router.get("/topics")
def get_external_signal_topics(since_days: int = 7):
    return {"topics": svc.extract_topics(_client, since_days=since_days)}


# ---------------------------------------------------------------------------
# Query Registry（管理用）
# ---------------------------------------------------------------------------

@geo_external_signal_router.get("/queries")
def list_external_signal_queries(theme_id: str | None = None, provider: str | None = None):
    params = {"select": "*", "order": "name.asc"}
    if theme_id:
        params["theme_id"] = f"eq.{theme_id}"
    if provider:
        params["provider"] = f"eq.{provider}"
    return {"queries": _client.select("geo_external_signal_queries", params)}


class CreateExternalSignalQueryRequest(BaseModel):
    name: str
    query: str
    provider: str = "gdelt"
    theme_id: str | None = None
    case_id: str | None = None
    enabled: bool = True
    article_lookback: str = "7d"
    metric_lookback: str = "30d"
    max_records: int = 250


@geo_external_signal_router.post("/queries")
def create_external_signal_query(req: CreateExternalSignalQueryRequest):
    existing = _client.select("geo_external_signal_queries", {
        "select": "query_id", "name": f"eq.{req.name}", "limit": "1",
    })
    if existing:
        raise HTTPException(status_code=400, detail=f"同名のQueryが既に存在します: {req.name}")
    row = _client.insert("geo_external_signal_queries", [req.model_dump()])[0]
    return row


class UpdateExternalSignalQueryRequest(BaseModel):
    enabled: bool | None = None
    query: str | None = None
    theme_id: str | None = None
    case_id: str | None = None
    article_lookback: str | None = None
    metric_lookback: str | None = None
    max_records: int | None = None


@geo_external_signal_router.patch("/queries/{query_id}")
def update_external_signal_query(query_id: str, req: UpdateExternalSignalQueryRequest):
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    if not patch:
        raise HTTPException(status_code=400, detail="更新項目がありません")
    _client.update("geo_external_signal_queries", {"query_id": f"eq.{query_id}"}, patch)
    rows = _client.select("geo_external_signal_queries", {
        "select": "*", "query_id": f"eq.{query_id}", "limit": "1",
    })
    if not rows:
        raise HTTPException(status_code=404, detail="query_idが見つかりません")
    return rows[0]


# ---------------------------------------------------------------------------
# Ingestion（管理用手動実行。一般検索とは分離）
# ---------------------------------------------------------------------------

class FetchExternalSignalsRequest(BaseModel):
    query_id: str
    requested_by: str = "manual"


@geo_external_signal_router.post("/fetch")
def fetch_external_signals(req: FetchExternalSignalsRequest):
    """Phase1では登録済みquery_id必須（ad-hoc queryの永続取得は行わない。query_matches/
    metricsがquery_id必須のため、未登録queryを保存しようとするとスキーマと矛盾する）。"""
    rows = _client.select("geo_external_signal_queries", {
        "select": "*", "query_id": f"eq.{req.query_id}", "limit": "1",
    })
    if not rows:
        raise HTTPException(status_code=404, detail="query_idが見つかりません（先にQuery Registryへ登録してください）")
    query_row = rows[0]
    if query_row.get("provider") != "gdelt":
        raise HTTPException(status_code=400,
                             detail=f"未対応のproviderです（Phase1はgdeltのみ）: {query_row.get('provider')}")
    try:
        result = geo_connector_gdelt.run_gdelt_ingestion(
            _client, query_row=query_row, run_type="manual", requested_by=req.requested_by)
    except geo_connector_gdelt.GdeltDisabledError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except geo_gdelt_rate_limit.GdeltBusyError as e:
        # 定期実行・catch-upなど別のDOC収集が実行中（二重にGDELTへアクセスしない）
        raise HTTPException(status_code=409, detail=f"現在実行中です。終わってから再実行してください（{e}）")
    return result


# ---------------------------------------------------------------------------
# Metrics（Coverage / Tone / Source Country Timeline）
# ---------------------------------------------------------------------------

def _timeline_for(query_id: str, metric_type: str, since: str | None):
    params = {
        "select": "*", "query_id": f"eq.{query_id}", "metric_type": f"eq.{metric_type}",
        "order": "series_key.asc,bucket_at.asc",
    }
    if since:
        params["bucket_at"] = f"gte.{since}"
    rows = _client.select("geo_external_signal_metrics", params)
    series: dict[str, list] = {}
    for row in rows:
        series.setdefault(row["series_key"], []).append(
            {"bucket_at": row["bucket_at"], "value": row["value"], "norm_value": row.get("norm_value")})
    return {"query_id": query_id, "metric_type": metric_type,
            "series": [{"series_key": k, "data": v} for k, v in series.items()]}


@geo_external_signal_router.get("/coverage-timeline")
def get_coverage_timeline(query_id: str, since: str | None = None):
    return _timeline_for(query_id, "coverage_volume", since)


@geo_external_signal_router.get("/tone-timeline")
def get_tone_timeline(query_id: str, since: str | None = None):
    return _timeline_for(query_id, "tone", since)


@geo_external_signal_router.get("/source-country-timeline")
def get_source_country_timeline(query_id: str, since: str | None = None):
    return _timeline_for(query_id, "source_country_coverage", since)
