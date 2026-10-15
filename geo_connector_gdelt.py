"""地政学ナレッジ＆インテリジェンスDB Phase1: GDELT DOC 2.0 API Connector（無認証）。

Geopolitical News DB / External Intelligence Layer専用のConnector。geo_documents/
geo_claims等のKnowledge/Evidenceパイプラインには一切接続しない（永続化は
geo_external_signal_service.py経由でgeo_external_signals/geo_external_signal_metrics等へ
行う）。GDELTはtheme横断のsource-neutral基盤として設計するため、geo_source_registryへの
Connector登録は行わない（同テーブルのtheme_idはNOT NULL制約があり、特定テーマ専属の
ソースとして登録するのはGDELTの性質と合わない）。有効/無効はconfig.jsonの
geo_external_signals.gdelt.enabledで制御する（check_automation_allowedの代替）。

使い方: python geo_connector_cli.py gdelt --query-id <uuid>
       python geo_connector_cli.py gdelt-batch
"""
import re
import time
from datetime import datetime, timedelta, timezone

import requests

import geo_connector_base as base
import geo_gdelt_rate_limit as rate_limit

BASE_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
CONNECTOR_VERSION = "geo-gdelt-1.0"
REQUEST_TIMEOUT = 30
MAX_RETRIES = 5
DEFAULT_REQUEST_INTERVAL_SECONDS = 5.0
DEFAULT_DOC_WINDOW_HOURS = 24
MIN_DOC_WINDOW_MINUTES = 15  # これ以上は分割しない下限（raw streamのbatch粒度に合わせる）
DEFAULT_DOC_FALLBACK_DAYS = 7  # prior watermarkが無い場合のbootstrap fallback
                                # （既存article_lookbackの既定値7dを踏襲）
DOC_CATCHUP_CONNECTOR_TYPE = "gdelt_doc_catchup"
CHECKPOINT_SCOPE_INCREMENTAL = "incremental"
CHECKPOINT_SCOPE_ADHOC = "adhoc"


class GdeltDisabledError(RuntimeError):
    pass


def _sleep_for_interval(interval_seconds: float) -> None:
    """（互換のため残置）呼び出し間隔の待機。プロセス・スレッドをまたぐ全体の制御は
    geo_gdelt_rate_limit.wait_turnが行う（従来のプロセス内変数だけでは、別プロセスや別スレッドが
    それぞれ「5秒空けたつもり」でも全体では同時アクセスになり429の原因になっていた）。"""
    rate_limit.wait_turn(interval_seconds)


def _backoff_sleep(attempt: int, base_seconds: float) -> None:
    """ネットワーク断・5xx・JSON異常の再試行待ち（指数backoff + jitter）。429は別（Retry-After/クールダウン）。"""
    time.sleep(min(base_seconds * (2 ** attempt), 120.0) + rate_limit.random.uniform(0, rate_limit.JITTER_SECONDS))


def _request(params: dict, *, proxies: dict = None, verify: bool = True,
             request_interval_seconds: float = DEFAULT_REQUEST_INTERVAL_SECONDS) -> dict:
    """GDELT DOC 2.0への唯一のHTTP呼び出し口。ArtList/TimelineVolRaw/TimelineTone/
    TimelineSourceCountryのすべてがこの関数を通る。

    レート制御（Phase D.1）: 送信前に必ずgeo_gdelt_rate_limit.wait_turn()を通し、アプリケーション全体
    （全プロセス・全スレッド）で最低6秒（公式「5秒に1回」+余裕）の間隔を守る。
    429: Retry-Afterがあれば尊重し、無ければbackoff（30/90/180秒+jitter）。待ち時間は全プロセス共通の
    クールダウンとして記録される（他のJob/手動実行も同じ時間だけ待つ）。429が max_429_retries 回続いたら
    10分のクールダウンを置いて失敗にする（短い間隔の連打はしない）。失敗メッセージには
    'GDELT status=429' を含める（Schedulerはretryableと分類する）。
    ネットワーク断・5xx・JSON異常は従来どおり指数backoffで再試行（最大MAX_RETRIES回）。"""
    last_error = None
    consecutive_429 = 0
    for attempt in range(MAX_RETRIES):
        rate_limit.wait_turn(request_interval_seconds)
        try:
            resp = requests.get(BASE_URL, params={**params, "format": "json"},
                                 proxies=proxies or {}, verify=verify, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            last_error = e
            _backoff_sleep(attempt, request_interval_seconds)
            continue
        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError as e:
                last_error = RuntimeError(f"GDELT応答のJSON解析に失敗: {e} body={resp.text[:200]!r}")
                _backoff_sleep(attempt, request_interval_seconds)
                continue
        if resp.status_code == 429:
            consecutive_429 += 1
            cooldown = rate_limit.register_429(getattr(resp, "headers", {}).get("Retry-After")
                                               if hasattr(resp, "headers") else None, consecutive_429)
            last_error = RuntimeError(f"GDELT status=429: {resp.text[:200]!r}")
            if cooldown["exhausted"]:
                raise RuntimeError(
                    f"GDELT API取得に失敗（429が{consecutive_429}回続いたため、{int(cooldown['cooldown_seconds'])}秒休みます）: {last_error}")
            continue        # 次のループ先頭のwait_turnがクールダウン分だけ待つ（長すぎる場合はすぐ失敗）
        if resp.status_code >= 500:
            last_error = RuntimeError(f"GDELT status={resp.status_code}: {resp.text[:200]!r}")
            _backoff_sleep(attempt, request_interval_seconds)
            continue
        resp.raise_for_status()
    raise RuntimeError(f"GDELT API取得に失敗（{MAX_RETRIES}回リトライ後）: {last_error}")


def search_articles(query, *, timespan=None, startdatetime=None, enddatetime=None,
                     maxrecords=250, sort="datedesc", proxies=None, verify=True,
                     request_interval_seconds=DEFAULT_REQUEST_INTERVAL_SECONDS) -> list:
    """mode=artlist。GDELT側のクエリ文法（(A OR B), "exact phrase", domain:, domainis:,
    sourcecountry:, sourcelang: 等）はqueryへそのまま渡すだけで透過する（本Connectorでは解釈しない）。
    戻り値はGDELTの生article dictのリスト（url/url_mobile/title/seendate/domain/language/
    sourcecountry等）。正規化はgeo_external_signal_service.normalize_articleで行う"""
    params = {"query": query, "mode": "artlist", "maxrecords": str(maxrecords), "sort": sort}
    if timespan:
        params["timespan"] = timespan
    if startdatetime:
        params["startdatetime"] = startdatetime
    if enddatetime:
        params["enddatetime"] = enddatetime
    payload = _request(params, proxies=proxies, verify=verify,
                        request_interval_seconds=request_interval_seconds)
    return payload.get("articles", [])


def _get_timeline(query, *, mode, timespan, proxies, verify, request_interval_seconds) -> list:
    params = {"query": query, "mode": mode}
    if timespan:
        params["timespan"] = timespan
    payload = _request(params, proxies=proxies, verify=verify,
                        request_interval_seconds=request_interval_seconds)
    return payload.get("timeline", [])


def get_coverage_timeline(query, *, timespan=None, proxies=None, verify=True,
                           request_interval_seconds=DEFAULT_REQUEST_INTERVAL_SECONDS) -> list:
    """mode=timelinevolraw（記事量の実測値。報道量の正として使う。ArtListの保存件数は
    1リクエスト最大250件という制約があり報道量の代替にならないため使わない）。
    戻り値: [{"series": ..., "data": [{"date": "20260901T000000Z", "value": ..., "norm": ...}, ...]}]"""
    return _get_timeline(query, mode="timelinevolraw", timespan=timespan, proxies=proxies,
                          verify=verify, request_interval_seconds=request_interval_seconds)


def get_tone_timeline(query, *, timespan=None, proxies=None, verify=True,
                       request_interval_seconds=DEFAULT_REQUEST_INTERVAL_SECONDS) -> list:
    """mode=timelinetone（TimelineTone=時間軸の平均Tone推移。ToneChart=Tone分布ヒストグラムとは
    別モードであり、Phase1で使うのはTimelineToneのみ）。"""
    return _get_timeline(query, mode="timelinetone", timespan=timespan, proxies=proxies,
                          verify=verify, request_interval_seconds=request_interval_seconds)


def get_source_country_timeline(query, *, timespan=None, proxies=None, verify=True,
                                 request_interval_seconds=DEFAULT_REQUEST_INTERVAL_SECONDS) -> list:
    """mode=timelinesourcecountry（媒体所在国別Coverageの時系列。国ごとに別seriesが返る。
    seriesラベルは"South Korea Volume Intensity"のような文字列で、国コードへの変換は行わず
    Phase1ではラベルそのものをseries_keyとして保存する）。"""
    return _get_timeline(query, mode="timelinesourcecountry", timespan=timespan, proxies=proxies,
                          verify=verify, request_interval_seconds=request_interval_seconds)


def search_articles_by_source_country(query, source_country, *, maxrecords=250, timespan=None,
                                       proxies=None, verify=True,
                                       request_interval_seconds=DEFAULT_REQUEST_INTERVAL_SECONDS) -> list:
    """記事ドリルダウン用。queryへ sourcecountry:<code> を付加してartlist検索する
    （source_countryはGDELTのFIPS国コード。媒体の所在国であり、記事が言及する国ではない）。"""
    combined = f"{query} sourcecountry:{source_country}"
    return search_articles(combined, timespan=timespan, maxrecords=maxrecords, proxies=proxies,
                            verify=verify, request_interval_seconds=request_interval_seconds)


def _load_runtime_config():
    """proxies/verify/enabled/request_interval_secondsをconfig.jsonから読む。
    config.json未設定でも動く（enabled既定true、GDELTは無認証APIのためcredential不要）。"""
    try:
        from config_utils import load_config, make_proxies
        config = load_config()
        proxies = make_proxies(config)
        verify = config.get("ssl", {}).get("verify", True)
    except Exception:
        proxies, verify = {}, True
    gdelt_cfg = {}
    try:
        gdelt_cfg = (config.get("geo_external_signals", {}) or {}).get("gdelt", {}) or {}
    except NameError:
        pass
    enabled = gdelt_cfg.get("enabled", True)
    request_interval_seconds = gdelt_cfg.get("request_interval_seconds", DEFAULT_REQUEST_INTERVAL_SECONDS)
    return proxies, verify, enabled, request_interval_seconds


@rate_limit.doc_collection_guard
def run_gdelt_ingestion(client, *, query_row: dict, run_type: str = "manual",
                         requested_by: str = "system", is_demo: bool = False,
                         since: str = None, until: str = None, fetch_metrics: bool = True) -> dict:
    """1つの登録済みQueryについて、ArticleList（article_lookback、since/until明示時は
    そちらを優先）＋Coverage/Tone/SourceCountry Timeline（metric_lookback、fetch_metrics=True
    の場合のみ）を取得し、Ingestion Run記録込みでgeo_external_signals/
    geo_external_signal_query_matches/geo_external_signal_metricsへ正規化・dedup・upsertする。

    fetch_metrics=Falseは`_fetch_window_recursive`（DOC window分割catch-up）専用——Timeline
    3種はmetric_lookback（例:"30d"）というarticle since/untilとは無関係な固定lookbackで
    取得するため、window分割の再帰呼び出しのたびに同じ集計期間を何度も再取得するのは無駄
    （社内プロキシ経由のrequest_interval_secondsレート制限下では、飽和queryのwindow分割が
    実用的でない時間まで遅くなる）。Timeline取得はarticle_lookbackベースの通常呼び出し
    （`gdelt`/`gdelt-batch`）でのみ行う。"""
    import geo_external_signal_service as svc

    proxies, verify, enabled, request_interval_seconds = _load_runtime_config()
    if not enabled:
        raise GdeltDisabledError(
            "config.jsonのgeo_external_signals.gdelt.enabled=false のためGDELT取得は無効です"
        )

    run = base.create_ingestion_run(
        client, source_id=None, connector_type="gdelt", run_type=run_type,
        requested_by=requested_by,
        request_parameters={"query_id": query_row["query_id"], "query": query_row["query"],
                             "since": since, "until": until},
        is_demo=is_demo,
    )

    # counts はgeo_ingestion_runsの実列（fetched/inserted/updated/error_count）へそのまま
    # complete_ingestion_runのpatchとして書き込まれるため、DBに存在しない列を混ぜてはならない。
    # article_result_countはDB非永続の戻り値専用フィールドとして別変数で保持する。
    counts = {"fetched_count": 0, "inserted_count": 0, "updated_count": 0, "error_count": 0}
    errors = []

    search_kwargs = {"maxrecords": query_row.get("max_records") or 250, "proxies": proxies,
                      "verify": verify, "request_interval_seconds": request_interval_seconds}
    if since or until:
        search_kwargs["startdatetime"] = since
        search_kwargs["enddatetime"] = until
    else:
        search_kwargs["timespan"] = query_row.get("article_lookback") or "7d"
    try:
        articles = search_articles(query_row["query"], **search_kwargs)
    except Exception as e:
        articles = []
        counts["error_count"] += 1
        errors.append(f"search_articles: {type(e).__name__}: {e}")

    counts["fetched_count"] = len(articles)
    # GDELT DOC APIが実際に返した生ArticleListの件数（DB insert/upsert/dedup前）。
    # window分割catch-upの250件飽和判定は、既存重複の多寡に影響されうるfetched_countではなく
    # 必ずこのarticle_result_countを使う（geo_ingestion_runsに列を持たないためcountsとは
    # 別に戻り値へ載せる）。
    article_result_count = len(articles)
    try:
        normalized_list = []
        for raw in articles:
            try:
                normalized_list.append(svc.normalize_article(raw, provider="gdelt"))
            except Exception as e:
                counts["error_count"] += 1
                errors.append(f"normalize ({raw.get('url')}): {type(e).__name__}: {e}")

        signal_result = svc.bulk_upsert_signals(client, normalized_list, provider="gdelt", is_demo=is_demo)
        counts["inserted_count"] += signal_result["inserted"]
        counts["updated_count"] += signal_result["updated"]

        signal_ids = list(signal_result["signal_id_by_dedup_key"].values())
        svc.bulk_upsert_query_matches(
            client, external_signal_ids=signal_ids, query_id=query_row["query_id"],
            ingestion_run_id=run["ingestion_run_id"],
        )
    except Exception as e:
        counts["error_count"] += 1
        errors.append(f"bulk article upsert: {type(e).__name__}: {e}")

    if fetch_metrics:
        metric_fetchers = (
            ("coverage_volume", get_coverage_timeline),
            ("tone", get_tone_timeline),
            ("source_country_coverage", get_source_country_timeline),
        )
        for metric_type, fetch_fn in metric_fetchers:
            try:
                series_list = fetch_fn(
                    query_row["query"], timespan=query_row.get("metric_lookback") or "30d",
                    proxies=proxies, verify=verify, request_interval_seconds=request_interval_seconds,
                )
            except Exception as e:
                counts["error_count"] += 1
                errors.append(f"{metric_type}: {type(e).__name__}: {e}")
                continue

            points = []
            for series in series_list:
                series_key = series.get("series") or "default" if metric_type == "source_country_coverage" else "default"
                for point in series.get("data", []):
                    bucket_at = svc.parse_gdelt_timeline_date(point.get("date"))
                    if bucket_at is None or point.get("value") is None:
                        continue
                    points.append({"series_key": series_key, "bucket_at": bucket_at, "value": point.get("value"),
                                    "norm_value": point.get("norm"), "raw_metadata": point,
                                    "ingestion_run_id": run["ingestion_run_id"]})
            try:
                svc.bulk_upsert_metrics(client, provider="gdelt", query_id=query_row["query_id"],
                                         metric_type=metric_type, points=points)
            except Exception as e:
                counts["error_count"] += 1
                errors.append(f"{metric_type} bulk upsert: {type(e).__name__}: {e}")

    status = "completed" if counts["error_count"] == 0 else (
        "completed_with_warnings" if counts["fetched_count"] > 0 else "failed")
    next_scheduled_at = base.calculate_next_run("daily")
    base.complete_ingestion_run(
        client, ingestion_run_id=run["ingestion_run_id"], status=status, counts=counts,
        error_summary="; ".join(errors) if errors else None, next_scheduled_at=next_scheduled_at,
        connector_version=CONNECTOR_VERSION,
    )
    # Phase A（Collection Reliability、2026-10-04）: is_demo=Trueのテスト実行がlast_run_at
    # （実運用のFreshness/Staleness判定が参照する値）を更新してしまうと、実際には本番
    # データが収集されていないのに「最近収集成功した」ように見えてしまう——調査中に実際に
    # 発生した事象（is_demoテスト実行でqueryのlast_run_atが更新された）への対応。
    if not is_demo:
        client.update("geo_external_signal_queries", {"query_id": f"eq.{query_row['query_id']}"},
                       {"last_run_at": datetime.now(timezone.utc).isoformat()})

    return {"ingestion_run_id": run["ingestion_run_id"], "query_id": query_row["query_id"],
            "status": status, "counts": counts, "article_result_count": article_result_count,
            "errors": errors}


# ---------------------------------------------------------------------------
# Window分割 + per-query completed_through checkpoint（Historical/Incremental共用）
# ---------------------------------------------------------------------------

_LOOKBACK_DAYS_RE = re.compile(r"^(\d+)d$")


def _parse_lookback_days(article_lookback: str) -> int | None:
    """既存article_lookback文字列（例:"7d"）から日数を取り出す。既存コードでは"Nd"形式
    のみ使われているため単純な正規表現で足りる。解釈できない場合はNoneを返す
    （呼び出し側がDEFAULT_DOC_FALLBACK_DAYSへフォールバックする）。"""
    if not article_lookback:
        return None
    m = _LOOKBACK_DAYS_RE.match(article_lookback.strip())
    return int(m.group(1)) if m else None


def _minutes_between(since: str, until: str) -> float:
    start = datetime.strptime(since, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    end = datetime.strptime(until, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    return (end - start).total_seconds() / 60.0


def _midpoint_timestamp(since: str, until: str) -> str:
    start = datetime.strptime(since, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    end = datetime.strptime(until, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    mid = start + (end - start) / 2
    return mid.strftime("%Y%m%d%H%M%S")


def _split_into_fixed_windows(since: str, until: str, window_hours: int) -> list:
    """[since, until]をwindow_hours単位の連続した時間windowに分割する（古い順）。"""
    start = datetime.strptime(since, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    end = datetime.strptime(until, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    step = timedelta(hours=window_hours)
    windows = []
    cursor = start
    while cursor < end:
        window_end = min(cursor + step, end)
        windows.append((cursor.strftime("%Y%m%d%H%M%S"), window_end.strftime("%Y%m%d%H%M%S")))
        cursor = window_end
    return windows


def _fetch_window_recursive(client, *, query_row: dict, since: str, until: str,
                             min_window_minutes: int, requested_by: str, is_demo: bool,
                             run_type: str = "manual") -> list:
    """1 windowを取得し、GDELT DOC APIが実際に返した生ArticleList件数（article_result_count、
    DB insert/upsert/dedup前）がmaxrecordsに達していて、かつwindow幅がmin_window_minutesより
    大きい場合は中点で2分割して再帰する（DB側のdedup後件数fetched_countで判定すると、APIは
    250件返しているのに既存重複が多く実質のfetched_countが小さいケースで飽和を見逃し、その
    250件のうち収まりきらなかった記事を静かに欠落させる）。min_window_minutesに達しても
    なお飽和する場合はsaturated_window=Trueとして返す（これ以上は自動分割しない、実データの
    実件数を明示的に報告し人間の判断を促す）。既存のdedup_key冪等性
    （geo_external_signal_service.compute_dedup_key/bulk_upsert_signals）は変更しないため、
    window境界付近で同じ記事を重複取得しても安全（行が二重にはならない）。Timeline 3種は
    fetch_metrics=Falseで取得しない（metric_lookbackという固定lookbackはwindow分割の
    再帰ごとに再取得する意味が無く、社内プロキシのrate limit下では飽和queryのwindow分割が
    実用的でない時間まで遅くなる）。"""
    result = run_gdelt_ingestion(client, query_row=query_row, since=since, until=until,
                                  run_type=run_type, requested_by=requested_by, is_demo=is_demo,
                                  fetch_metrics=False)
    maxrecords = query_row.get("max_records") or 250
    article_result_count = result["article_result_count"]
    window_minutes = _minutes_between(since, until)
    if article_result_count >= maxrecords and window_minutes > min_window_minutes:
        mid = _midpoint_timestamp(since, until)
        return (_fetch_window_recursive(client, query_row=query_row, since=since, until=mid,
                                         min_window_minutes=min_window_minutes,
                                         requested_by=requested_by, is_demo=is_demo, run_type=run_type)
                + _fetch_window_recursive(client, query_row=query_row, since=mid, until=until,
                                           min_window_minutes=min_window_minutes,
                                           requested_by=requested_by, is_demo=is_demo, run_type=run_type))
    saturated = article_result_count >= maxrecords and window_minutes <= min_window_minutes
    # Phase D.1: 取得自体が失敗した窓（429・ネットワーク断・5xx等）は「確認済み」と主張できない。
    # 従来はarticle_result_count=0・非飽和として扱われ、checkpointが失敗した窓を飛び越えて進み、
    # その期間の記事が二度と再取得されなかった（記事が増えなくなった原因）。
    failed = result.get("status") == "failed"
    return [{"since": since, "until": until, "article_result_count": article_result_count,
             "saturated_window": saturated, "ingestion_run_id": result["ingestion_run_id"],
             "failed_window": failed, "error": "; ".join(result.get("errors") or [])[:300] if failed else None}]


def _find_last_doc_watermark(client, *, query_id: str, is_demo: bool) -> str | None:
    """query_id・is_demoスコープに閉じた直近のgdelt_doc_catchup incremental summary runから
    completed_throughを読み出す（raw stream側の_find_last_catchup_watermarkと同じ設計、
    query_id次元が増えただけ）。"""
    import geo_external_signal_service as signal_svc
    rows = signal_svc._select_all_paginated(client, "geo_ingestion_runs", {
        "select": "started_at,request_parameters", "connector_type": f"eq.{DOC_CATCHUP_CONNECTOR_TYPE}",
        "is_demo": "eq.true" if is_demo else "eq.false",
    })
    latest = None
    for r in rows:
        rp = r.get("request_parameters") or {}
        if rp.get("query_id") != query_id or rp.get("checkpoint_scope") != CHECKPOINT_SCOPE_INCREMENTAL:
            continue
        wm = rp.get("completed_through")
        if wm and (latest is None or r["started_at"] > latest[0]):
            latest = (r["started_at"], wm)
    return latest[1] if latest else None


def resolve_doc_incremental_since(client, *, query_row: dict, is_demo: bool) -> str:
    """定常Incremental運用で`since`が指定されなかった場合の開始点を決定する。queryごとの
    checkpointが無い場合（bootstrap）は、既存article_lookback（例:"7d"）から日数を解釈し、
    それも解釈できなければDEFAULT_DOC_FALLBACK_DAYSへフォールバックする。"""
    prior_watermark = _find_last_doc_watermark(client, query_id=query_row["query_id"], is_demo=is_demo)
    if prior_watermark:
        return prior_watermark
    fallback_days = _parse_lookback_days(query_row.get("article_lookback")) or DEFAULT_DOC_FALLBACK_DAYS
    now = datetime.now(timezone.utc)
    return (now - timedelta(days=fallback_days)).strftime("%Y%m%d%H%M%S")


@rate_limit.doc_collection_guard
def run_gdelt_doc_catchup(client, *, query_row: dict, since: str = None, until: str = None,
                           window_hours: int = DEFAULT_DOC_WINDOW_HOURS,
                           min_window_minutes: int = MIN_DOC_WINDOW_MINUTES,
                           promote_to_checkpoint: bool = False,
                           requested_by: str = "cli", is_demo: bool = False) -> dict:
    """Historical（since明示指定）とIncremental（since省略、queryごとのcheckpointから自動
    catch-up）を同一関数で扱う。windowを時系列順に処理し、saturated_windowが出た時点
    （このwindowが「確認済み」と主張できない最初の地点）でcompleted_throughの前進を止める
    ——raw stream側のcontiguous frontier設計と同じ考え方を、15分batch gridの無いDOCの
    window単位に適用したもの。

    checkpoint_scopeは以下のルールで決まる（raw stream側と同じ設計）:
    - since省略（定常Incremental運用）→常に"incremental"（定常watermarkを更新する）
    - since明示指定かつpromote_to_checkpoint=False（既定、Historical/Ad-hoc catch-up）→
      "adhoc"（記録は残すがresolve_doc_incremental_sinceからは無視される）
    - since明示指定かつpromote_to_checkpoint=Trueは、以下を満たす場合のみ"incremental":
      - 全windowがsaturationなく解決していること（all_clean）。満たさなければ
        promotion_rejected_reason="incomplete_catchup"。
      - 既存query checkpointが無ければこのrange自身をbootstrap checkpointとして許可する。
      - 既存checkpointがある場合、新しいcompleted_throughが既存より古ければ
        "would_regress_checkpoint"、同値ならno-op（前進させない）、既存より後の場合のみ
        effective_since<=existing_watermarkの連続性を確認し、満たさなければ
        "checkpoint_gap"として拒否する（例: 既存checkpoint=9/8の状態で6/1〜7/1という古い
        rangeを後からpromoteしてもcheckpointを7/1へ巻き戻さない）。"""
    query_id = query_row["query_id"]
    now = datetime.now(timezone.utc)
    effective_since = since or resolve_doc_incremental_since(client, query_row=query_row, is_demo=is_demo)
    effective_until = until or now.strftime("%Y%m%d%H%M%S")

    # Phase D（Production Scheduling、2026-10-06）: requested_byからrun_typeを導出する
    # （raw stream側のgeo_connector_gdelt_stream.pyと同じ設計）。既存呼び出し元（CLI="cli"）
    # の挙動は変えず、Schedulerがrequested_by="batch"（既定）で呼んだ場合のみ
    # run_type="scheduled_external"としてgeo_ingestion_runsへ正しく記録されるようにする
    # （「本当にSchedulerが動いたか」を事後に判別可能にするための記録のみで、catch-up・
    # retry・重複防止ロジック自体は一切変更しない）。
    run_type = "cli" if requested_by == "cli" else ("scheduled_external" if requested_by == "batch" else "manual")

    windows = _split_into_fixed_windows(effective_since, effective_until, window_hours)
    window_results = []
    all_clean = True
    completed_through = effective_since
    for window_start, window_end in windows:
        results = _fetch_window_recursive(client, query_row=query_row, since=window_start, until=window_end,
                                           min_window_minutes=min_window_minutes,
                                           requested_by=requested_by, is_demo=is_demo, run_type=run_type)
        window_results.extend(results)
        if any(w["saturated_window"] or w.get("failed_window") for w in results):
            all_clean = False
            break  # このwindowは全件確認できていない（飽和、または取得失敗）ため、completed_throughをこれ以上進めない
        completed_through = window_end

    saturated = [w for w in window_results if w["saturated_window"]]
    failed_windows = [w for w in window_results if w.get("failed_window")]

    checkpoint_scope = CHECKPOINT_SCOPE_ADHOC
    promotion_rejected_reason = None
    if since is None:
        checkpoint_scope = CHECKPOINT_SCOPE_INCREMENTAL
    elif promote_to_checkpoint:
        if not all_clean:
            promotion_rejected_reason = "incomplete_catchup"
        else:
            existing_watermark = _find_last_doc_watermark(client, query_id=query_id, is_demo=is_demo)
            if existing_watermark is None:
                checkpoint_scope = CHECKPOINT_SCOPE_INCREMENTAL
            elif completed_through < existing_watermark:
                # 新completed_throughが既存より古い場合は無条件で拒否する（例: 既存
                # checkpoint=9/8の状態で6/1〜7/1という古いrangeを後からpromoteしても
                # checkpointを7/1へ巻き戻さない）。
                promotion_rejected_reason = "would_regress_checkpoint"
            elif completed_through == existing_watermark:
                pass  # 同値はno-op（前進させないが拒否でもない）
            elif effective_since > existing_watermark:
                # 既存watermarkとこのrangeの開始点の間に未確認の期間が残る
                # （raw stream側のcheckpoint_gapと同じ考え方）。
                promotion_rejected_reason = "checkpoint_gap"
            else:
                checkpoint_scope = CHECKPOINT_SCOPE_INCREMENTAL

    request_parameters = {"query_id": query_id, "completed_through": completed_through,
                           "checkpoint_scope": checkpoint_scope}
    if promotion_rejected_reason:
        request_parameters["promotion_rejected_reason"] = promotion_rejected_reason
    summary_run = base.create_ingestion_run(
        client, source_id=None, connector_type=DOC_CATCHUP_CONNECTOR_TYPE, run_type=run_type,
        requested_by=requested_by, request_parameters=request_parameters, is_demo=is_demo)
    base.complete_ingestion_run(
        client, ingestion_run_id=summary_run["ingestion_run_id"],
        # 失敗した窓がある収集は「失敗」として記録する（"completed_with_warnings"のままだと、Schedulerが
        # 成功と見なして次の周期まで再試行しない）。部分的な進捗（completed_through）はstatusに関係なく
        # 次回のcatch-upの開始点として使われるため、失敗でも保持される。
        status="failed" if failed_windows else "completed",
        error_summary=("取得失敗のwindowがあるためcheckpointを進めていません: " + failed_windows[0]["error"]) if failed_windows else None)

    # 取得に失敗したwindowがある場合は"failed"として返す（Schedulerがretryable/non-retryableを分類して
    # backoff付きで再試行する。checkpointは失敗した窓の手前で止まっているので、次回はそこから再取得する）。
    status = "failed" if failed_windows else ("completed" if all_clean else "completed_with_warnings")
    return {"query_id": query_id, "status": status, "failed_windows": failed_windows,
            "error_summary": failed_windows[0]["error"] if failed_windows else None,
            "windows_processed": len(window_results),
            "total_article_result_count": sum(w["article_result_count"] for w in window_results),
            "saturated_windows": saturated, "completed_through": completed_through,
            "checkpoint_scope": checkpoint_scope, "promotion_rejected_reason": promotion_rejected_reason}


@rate_limit.doc_collection_guard
def run_gdelt_doc_catchup_all_enabled(client, *, since: str, until: str,
                                       window_hours: int = DEFAULT_DOC_WINDOW_HOURS,
                                       promote_to_checkpoint: bool = False,
                                       requested_by: str = "cli", is_demo: bool = False) -> dict:
    """Initial Loadは1 query-idだけでなく、有効な全geo_external_signal_queriesを対象にDOC
    catch-upを完了させてからraw streamを開始する（1 query-idだけInitial Loadしても、他の
    有効queryのSignalが無いままraw streamのURLマッチングが行われ、本来Actor/Media
    Candidateへ昇格できるはずのEventが取りこぼされる）。"""
    enabled_queries = list_enabled_external_signal_queries(client, provider="gdelt")
    results = [run_gdelt_doc_catchup(client, query_row=row, since=since, until=until,
                                      window_hours=window_hours, promote_to_checkpoint=promote_to_checkpoint,
                                      requested_by=requested_by, is_demo=is_demo)
               for row in enabled_queries]
    return {"query_results": results,
            "all_clean": all(not r["saturated_windows"] and not r.get("failed_windows") for r in results)}


def list_enabled_external_signal_queries(client, *, provider: str = "gdelt") -> list:
    """provider='gdelt' AND enabled=trueのgeo_external_signal_queries一覧を返す。
    run_enabled_external_signal_queries（gdelt-batch）とrun_gdelt_doc_catchup_all_enabled
    （gdelt-historicalのInitial Load、全query対象）の両方が共有する（重複実装しない）。"""
    return client.select("geo_external_signal_queries", {
        "select": "*", "provider": f"eq.{provider}", "enabled": "eq.true",
    })


@rate_limit.doc_collection_guard
def run_enabled_external_signal_queries(client, *, provider: str = "gdelt",
                                         run_type: str = "scheduled_external",
                                         requested_by: str = "batch", is_demo: bool = False) -> list:
    """「継続収集」の実行口。geo_external_signal_queries を provider='gdelt' AND enabled=true で
    取得し、順次run_gdelt_doc_catchup（since省略＝queryごとのcompleted_through checkpoint
    からの自動catch-up）を呼ぶ（providerで絞ることで、将来Media Cloud/ACLED等のqueryが
    追加されてもGDELT ingestionへ誤配送しない）。固定article_lookback（既定7d）ベースの
    run_gdelt_ingestion直接呼び出しはやめる——connectorが7日より長く停止すると、直近7日
    より前の期間が永久に取りこぼされるため（raw stream側で修正した固定24hルックバック
    問題と同じ形の不具合）。本格schedulerはPhase1外だが、Windows Task Scheduler/Azure
    scheduled job/cron等からこの関数（または`python geo_connector_cli.py gdelt-batch`）を
    定期実行できる。"""
    queries = list_enabled_external_signal_queries(client, provider=provider)
    results = []
    for query_row in queries:
        try:
            result = run_gdelt_doc_catchup(client, query_row=query_row, since=None,
                                            requested_by=requested_by, is_demo=is_demo)
        except Exception as e:
            result = {"query_id": query_row["query_id"], "status": "failed",
                      "counts": {}, "errors": [f"{type(e).__name__}: {e}"]}
        result["query_name"] = query_row.get("name")
        results.append(result)
    return results
