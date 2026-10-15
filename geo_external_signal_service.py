"""地政学ナレッジ＆インテリジェンスDB Phase1: External Signal正規化・Dedup・集計処理。

geo_external_signals / geo_external_signal_query_matches / geo_external_signal_metrics への
読み書きをここに集約する。GDELT固有のレスポンス整形はgeo_connector_gdelt.py側に閉じ込め、
本ファイルは正規化済みdictのみを扱う（provider非依存、将来Media Cloud/ACLED/RSS/Official/
Babelstreetを追加する際もこのファイルの関数群をそのまま使う想定）。

Knowledge Item状態機械（geo_knowledge_service.py）・Entity Merge（geo_entity_resolution.py）には
一切書き込まない。source_role='discovery', evidence_status='unverified'を既定のまま保持する。
"""
import hashlib
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

_TRACKING_PARAM_PREFIXES = ("utm_", "fbclid", "gclid", "ref", "cmpid")

_IN_FILTER_CHUNK_SIZE = 80


def _select_in_chunked(client, table: str, *, select: str, in_column: str, in_values: list,
                        extra_params: dict = None) -> list:
    """PostgRESTの'in.(...)'フィルタへ渡す値が多いとURLが長大化し（例: SHA256 64文字の
    dedup_keyを250件連結すると16,000文字超）、GETリクエストの長さ上限に抵触しうる。
    _IN_FILTER_CHUNK_SIZE件ずつに分割してSELECTし、結果を結合して返す。"""
    if not in_values:
        return []
    rows = []
    for i in range(0, len(in_values), _IN_FILTER_CHUNK_SIZE):
        chunk = in_values[i:i + _IN_FILTER_CHUNK_SIZE]
        params = {"select": select, in_column: f"in.({','.join(chunk)})", **(extra_params or {})}
        rows.extend(client.select(table, params))
    return rows


def _update_in_chunked(client, table: str, *, in_column: str, in_values: list,
                        patch: dict, extra_params: dict = None) -> None:
    """UPDATE(PATCH)のフィルタもURLクエリ文字列に乗るため、SELECTと同じ理由でチャンク分割する。"""
    for i in range(0, len(in_values), _IN_FILTER_CHUNK_SIZE):
        chunk = in_values[i:i + _IN_FILTER_CHUNK_SIZE]
        params = {in_column: f"in.({','.join(chunk)})", **(extra_params or {})}
        client.update(table, params, patch)


_PAGE_SIZE = 1000


def _select_all_paginated(client, table: str, params: dict) -> list:
    """PostgREST/Supabaseはdb-max-rows設定（既定1000件程度）により、limitを明示的に大きく
    指定しても1回のSELECTで返る件数が暗黙に頭打ちになる。TimelineSourceCountryのように
    1クエリで数千〜数万点になりうるmetricの既存キー取得でこれを見逃すと、実際には存在する
    データを「無い」と誤認して重複INSERTを試み、unique制約違反でchunk全体が失敗しうる。
    offsetを進めながら_PAGE_SIZE件ずつ取得し、返り件数が_PAGE_SIZE未満になった時点で
    全件取得完了とみなす。"""
    rows = []
    offset = 0
    while True:
        page = client.select(table, {**params, "limit": str(_PAGE_SIZE), "offset": str(offset)})
        rows.extend(page)
        if len(page) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE
    return rows


def normalize_url(url: str) -> str:
    """トラッキングパラメータ除去・scheme/host小文字化・末尾スラッシュ除去・fragment除去。
    比較・dedup専用（保存用のurl/canonical_urlは元の値をそのまま保持する）。"""
    if not url:
        return ""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or ""
    query_pairs = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not any(k.lower().startswith(p) or k.lower() == p for p in _TRACKING_PARAM_PREFIXES)
    ]
    query = urlencode(sorted(query_pairs))
    return urlunsplit((scheme, netloc, path, query, ""))


def _normalize_title(title: str) -> str:
    if not title:
        return ""
    return re.sub(r"\s+", " ", title.strip().lower())


def compute_dedup_key(*, canonical_url: str = None, normalized_url: str = None,
                       source_domain: str = None, normalized_title: str = None) -> str:
    """canonical_url → normalized_url → source_domain+normalized_title の優先順位でsha256を返す。
    いずれも無い場合はdedup不能としてValueErrorを送出する（呼び出し元はskip扱いにすること）。"""
    if canonical_url:
        basis = f"canonical_url:{canonical_url.strip()}"
    elif normalized_url:
        basis = f"normalized_url:{normalized_url}"
    elif source_domain and normalized_title:
        basis = f"domain_title:{source_domain.strip().lower()}:{normalized_title}"
    else:
        raise ValueError("dedup_key算出に必要な情報（canonical_url/normalized_url/domain+title）が不足しています")
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


_TIMELINE_DATE_FORMATS = ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S", "%Y-%m-%dT%H:%M:%SZ")


def parse_gdelt_timeline_date(date_str: str):
    """GDELT Timeline系（TimelineVolRaw/TimelineTone/TimelineSourceCountry）のdate文字列を
    ISO8601（UTC）文字列へ変換する。解釈できない場合はNoneを返す（呼び出し元でskipする）。"""
    if not date_str:
        return None
    for fmt in _TIMELINE_DATE_FORMATS:
        try:
            dt = datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            continue
    return None


def parse_gdelt_seendate(seendate: str):
    """ArtListのseendate（記事をGDELTが観測した時刻。真の媒体公開日時とは限らない）を
    ISO8601へ変換する。解釈できない場合はNoneを返す"""
    return parse_gdelt_timeline_date(seendate)


def normalize_article(raw: dict, *, provider: str = "gdelt") -> dict:
    """GDELT ArtList 1件（url/url_mobile/title/seendate/domain/language/sourcecountry等）を
    geo_external_signals挿入用の正規形dictへ変換する。

    GDELTのArtListは真の媒体公開日時を返さず、"seendate"（GDELTが記事を観測した時刻）のみを
    返すため、published_at=seendate, timestamp_source='gdelt_seen' として保存する
    （'publisher'は将来他providerが真の公開日時を返せる場合のみ使用する）。"""
    url = (raw.get("url") or "").strip()
    title = raw.get("title")
    source_domain = raw.get("domain")
    normalized_url = normalize_url(url)
    normalized_title = _normalize_title(title)

    dedup_key = compute_dedup_key(
        canonical_url=url or None, normalized_url=normalized_url or None,
        source_domain=source_domain, normalized_title=normalized_title,
    )
    published_at = parse_gdelt_seendate(raw.get("seendate"))
    source_country = raw.get("sourcecountry")

    return {
        "provider": provider,
        "provider_record_id": None,
        "signal_type": "article",
        "title": title,
        "summary": None,
        "url": url or None,
        "canonical_url": url or None,
        "normalized_url": normalized_url or None,
        "source_domain": source_domain,
        "source_name": source_domain,
        "source_country": source_country,
        "source_language": raw.get("language"),
        "published_at": published_at,
        "timestamp_source": "gdelt_seen" if published_at else "unknown",
        "entities": [],
        "countries": [source_country] if source_country else [],
        "tone": None,
        "coverage_value": None,
        "source_role": "discovery",
        "evidence_status": "unverified",
        "raw_metadata": raw,
        "validation_status": "unreviewed",
        "knowledge_promotion_status": "not_promoted",
        "dedup_key": dedup_key,
    }


def upsert_signal(client, normalized: dict, *, provider: str = "gdelt") -> tuple[dict, str]:
    """(provider, dedup_key)で検索。無ければinsert("inserted")。あれば
    last_observed_at等を更新("updated")。同一記事を別providerが観測した場合は
    別行として残る（(provider, dedup_key)がuniqueキーのため）。"""
    dedup_key = normalized["dedup_key"]
    existing = client.select("geo_external_signals", {
        "select": "*", "provider": f"eq.{provider}", "dedup_key": f"eq.{dedup_key}", "limit": "1",
    })
    now_iso = datetime.now(timezone.utc).isoformat()
    if existing:
        row = existing[0]
        client.update("geo_external_signals", {"external_signal_id": f"eq.{row['external_signal_id']}"}, {
            "last_observed_at": now_iso,
            "title": normalized.get("title") or row.get("title"),
            "summary": normalized.get("summary") or row.get("summary"),
        })
        row["last_observed_at"] = now_iso
        return row, "updated"

    inserted = client.insert("geo_external_signals", [normalized])[0]
    return inserted, "inserted"


def upsert_query_match(client, *, external_signal_id: str, query_id: str, ingestion_run_id: str) -> str:
    """(external_signal_id, query_id)で検索。無ければinsert("matched")。あれば
    last_matched_at/ingestion_run_idを更新("rematched")。1記事が複数Queryにヒットした場合は
    query毎に1行できる（N:M）。"""
    existing = client.select("geo_external_signal_query_matches", {
        "select": "*", "external_signal_id": f"eq.{external_signal_id}",
        "query_id": f"eq.{query_id}", "limit": "1",
    })
    now_iso = datetime.now(timezone.utc).isoformat()
    if existing:
        client.update("geo_external_signal_query_matches",
                       {"match_id": f"eq.{existing[0]['match_id']}"},
                       {"last_matched_at": now_iso, "ingestion_run_id": ingestion_run_id})
        return "rematched"

    client.insert("geo_external_signal_query_matches", [{
        "external_signal_id": external_signal_id, "query_id": query_id,
        "ingestion_run_id": ingestion_run_id,
    }])
    return "matched"


def upsert_metric(client, *, provider: str, query_id: str, metric_type: str, series_key: str,
                   bucket_at: str, value, norm_value=None, raw_metadata: dict = None,
                   ingestion_run_id: str = None) -> str:
    """(provider, query_id, metric_type, series_key, bucket_at)で検索。無ければinsert、
    あればvalue/norm_value/observed_atをUPDATE（GDELTが直近バケットの値を改訂することが
    あるため、Phase1では最新観測値で上書きする。行は増殖させない）。"""
    existing = client.select("geo_external_signal_metrics", {
        "select": "metric_id", "provider": f"eq.{provider}", "query_id": f"eq.{query_id}",
        "metric_type": f"eq.{metric_type}", "series_key": f"eq.{series_key}",
        "bucket_at": f"eq.{bucket_at}", "limit": "1",
    })
    now_iso = datetime.now(timezone.utc).isoformat()
    if existing:
        client.update("geo_external_signal_metrics", {"metric_id": f"eq.{existing[0]['metric_id']}"}, {
            "value": value, "norm_value": norm_value, "raw_metadata": raw_metadata,
            "ingestion_run_id": ingestion_run_id, "observed_at": now_iso,
        })
        return "updated"

    client.insert("geo_external_signal_metrics", [{
        "provider": provider, "query_id": query_id, "metric_type": metric_type,
        "series_key": series_key, "bucket_at": bucket_at, "value": value,
        "norm_value": norm_value, "raw_metadata": raw_metadata, "ingestion_run_id": ingestion_run_id,
    }])
    return "inserted"


def bulk_upsert_signals(client, normalized_list: list, *, provider: str = "gdelt", is_demo: bool = False) -> dict:
    """upsert_signalの一括版。1クエリで最大250記事に達しうるため、記事ごとに逐次
    select+insert/update（最大2N回のHTTP往復）を行うと、社内プロキシ経由では実用的な
    時間で終わらないことが実測で判明した（250記事で1時間以上かかり未完走）。
    (provider, dedup_key)一括SELECT→未登録分だけbulk INSERT→既存分はdedup_key一括で
    1回のUPDATEにまとめることで、記事数に依らずおおよそ3回のHTTP往復に収める。

    is_demo: Trueの場合、新規挿入分のみgeo_external_signals.is_demo=trueで保存し、本番の
    実記事データと区別する（Phase A・2026-10-04: is_demoテスト実行がこの区別なしに実記事を
    本番同然でgeo_external_signalsへ挿入していた事象への対応）。既存行（dedup_key一致）は
    is_demoの値を上書きしない——本番で既に収集済みの記事をdemo実行が再観測しただけの場合に、
    本番行を誤ってdemo扱いにしないため。
    戻り値: {"inserted": n, "updated": n, "signal_id_by_dedup_key": {dedup_key: external_signal_id}}"""
    if not normalized_list:
        return {"inserted": 0, "updated": 0, "signal_id_by_dedup_key": {}}

    dedup_keys = [n["dedup_key"] for n in normalized_list]
    existing_rows = _select_in_chunked(
        client, "geo_external_signals", select="external_signal_id,dedup_key",
        in_column="dedup_key", in_values=dedup_keys, extra_params={"provider": f"eq.{provider}"})
    existing_by_key = {r["dedup_key"]: r["external_signal_id"] for r in existing_rows}

    to_insert = [{**n, "is_demo": is_demo} for n in normalized_list if n["dedup_key"] not in existing_by_key]
    inserted_rows = client.insert("geo_external_signals", to_insert) if to_insert else []

    now_iso = datetime.now(timezone.utc).isoformat()
    existing_keys = list(existing_by_key.keys())
    _update_in_chunked(
        client, "geo_external_signals", in_column="dedup_key", in_values=existing_keys,
        patch={"last_observed_at": now_iso}, extra_params={"provider": f"eq.{provider}"})

    signal_id_by_key = dict(existing_by_key)
    for row in inserted_rows:
        signal_id_by_key[row["dedup_key"]] = row["external_signal_id"]

    return {"inserted": len(to_insert), "updated": len(existing_keys),
            "signal_id_by_dedup_key": signal_id_by_key}


def bulk_upsert_query_matches(client, *, external_signal_ids: list, query_id: str,
                               ingestion_run_id: str) -> dict:
    """upsert_query_matchの一括版。同一query_idに対する複数external_signal_idの
    matchをまとめて1〜2回のHTTP往復で処理する。"""
    if not external_signal_ids:
        return {"matched": 0, "rematched": 0}

    existing_rows = _select_in_chunked(
        client, "geo_external_signal_query_matches", select="external_signal_id",
        in_column="external_signal_id", in_values=external_signal_ids,
        extra_params={"query_id": f"eq.{query_id}"})
    existing_ids = {r["external_signal_id"] for r in existing_rows}
    to_insert_ids = [sid for sid in external_signal_ids if sid not in existing_ids]

    if to_insert_ids:
        client.insert("geo_external_signal_query_matches", [
            {"external_signal_id": sid, "query_id": query_id, "ingestion_run_id": ingestion_run_id}
            for sid in to_insert_ids
        ])
    now_iso = datetime.now(timezone.utc).isoformat()
    _update_in_chunked(
        client, "geo_external_signal_query_matches", in_column="external_signal_id",
        in_values=list(existing_ids), patch={"last_matched_at": now_iso, "ingestion_run_id": ingestion_run_id},
        extra_params={"query_id": f"eq.{query_id}"})

    return {"matched": len(to_insert_ids), "rematched": len(existing_ids)}


_METRIC_INSERT_CHUNK_SIZE = 500


def bulk_upsert_metrics(client, *, provider: str, query_id: str, metric_type: str, points: list) -> dict:
    """upsert_metricの一括版。(series_key, bucket_at)の組を1回のSELECTでまとめて突合し、
    未登録分のみbulk INSERTする。【Phase1の意図的な簡略化】既存点の値再更新（GDELTが直近
    バケットを改訂した場合の上書き）は行わない（1点ずつのUPDATEが必要になり、社内プロキシ
    経由では記事同様に非現実的な時間がかかるため）。行が増殖しないこと（Idempotency）を優先し、
    値の再同期はPhase2で「直近48h等の限定ウィンドウのみ再更新」のような形で再検討する。

    metric_lookback=30d・timelinesourcecountryのように1回のfetchで数千〜数万点になりうる
    （実測: timespan=7dのTimelineSourceCountryだけで約40 series・6,240点）ため、1回のINSERT
    payloadが肥大化しないよう_METRIC_INSERT_CHUNK_SIZE件ずつに分割して投入する。"""
    if not points:
        return {"inserted": 0, "skipped_existing": 0}

    existing_rows = _select_all_paginated(client, "geo_external_signal_metrics", {
        "select": "series_key,bucket_at", "provider": f"eq.{provider}",
        "query_id": f"eq.{query_id}", "metric_type": f"eq.{metric_type}",
    })
    existing_keys = {(r["series_key"], r["bucket_at"]) for r in existing_rows}

    to_insert = [
        {"provider": provider, "query_id": query_id, "metric_type": metric_type, **p}
        for p in points if (p["series_key"], p["bucket_at"]) not in existing_keys
    ]
    for i in range(0, len(to_insert), _METRIC_INSERT_CHUNK_SIZE):
        client.insert("geo_external_signal_metrics", to_insert[i:i + _METRIC_INSERT_CHUNK_SIZE])

    return {"inserted": len(to_insert), "skipped_existing": len(points) - len(to_insert)}


def _signal_ids_for_theme(client, theme_id: str) -> list:
    """theme_id → geo_external_signal_queries → geo_external_signal_query_matches の
    2段階lookupでexternal_signal_id一覧を返す（geo_external_signalsはtheme_id列を持たない）。"""
    query_rows = client.select("geo_external_signal_queries", {
        "select": "query_id", "theme_id": f"eq.{theme_id}",
    })
    query_ids = [r["query_id"] for r in query_rows]
    if not query_ids:
        return []
    match_rows = client.select("geo_external_signal_query_matches", {
        "select": "external_signal_id", "query_id": f"in.({','.join(query_ids)})",
    })
    return list({r["external_signal_id"] for r in match_rows})


def summarize_signals(client, *, theme_id: str = None) -> dict:
    """24h/7d件数・source数・country数。geo_external_signalsをpublished_at
    （欠損時observed_at）で集計する。DBのみ、GDELTを叩かない。"""
    now = datetime.now(timezone.utc)
    since_24h = (now - timedelta(hours=24)).isoformat()
    since_7d = (now - timedelta(days=7)).isoformat()

    base_filter = {"select": "external_signal_id,published_at,observed_at,source_domain,source_country"}
    if theme_id:
        signal_ids = _signal_ids_for_theme(client, theme_id)
        if not signal_ids:
            return {"signals_24h": 0, "signals_7d": 0, "sources": 0, "countries": 0}
        base_filter["external_signal_id"] = f"in.({','.join(signal_ids)})"

    rows = client.select("geo_external_signals", base_filter)

    def _effective_time(row):
        return row.get("published_at") or row.get("observed_at")

    signals_24h = sum(1 for r in rows if _effective_time(r) and _effective_time(r) >= since_24h)
    signals_7d = sum(1 for r in rows if _effective_time(r) and _effective_time(r) >= since_7d)
    sources = len({r["source_domain"] for r in rows if r.get("source_domain")})
    countries = len({r["source_country"] for r in rows if r.get("source_country")})
    return {"signals_24h": signals_24h, "signals_7d": signals_7d, "sources": sources, "countries": countries}


def extract_topics(client, *, since_days: int = 7) -> list:
    """Trending Topics。【重要】保存済み記事件数ではなく geo_external_signal_metrics の
    metric_type='coverage_volume'（TimelineVolRaw）をQuery単位でcurrent期間/previous同期間で
    比較する。ArtListは1リクエスト最大250件までしか記事を返さないため、記事保存件数を
    母数にすると実際の報道量から乖離する。metric_lookback既定値が30日のため、
    current7d/previous7d比較に必要な最低14日分が初回ingestion時点から揃っている。

    【正規化】change_pctはraw valueの単純な前後比較ではなく、GDELTがTimelineVolRawの
    各バケットで同時に返すnorm（その時間帯にGDELTが監視した全記事数、報道量の母数）を使い、
    normalized coverage share = sum(value) / sum(norm) をcurrent/previous期間それぞれで
    算出したうえでその変化率を取る。GDELT全体の監視記事総量は日によって変動するため、
    raw valueだけを比較すると「本当にこのTopicの相対的な注目度が上がったか」ではなく
    「その日のGDELT全体の記事流量が多かっただけか」を区別できない。current_count/
    previous_countは表示用の実記事数（raw value合計）としてそのまま残し、change_pctだけ
    normalized shareベースに切り替える。"""
    now = datetime.now(timezone.utc)
    current_start = now - timedelta(days=since_days)
    previous_start = now - timedelta(days=since_days * 2)

    queries = client.select("geo_external_signal_queries", {"select": "query_id,name"})
    query_names = {q["query_id"]: q["name"] for q in queries}
    if not query_names:
        return []

    metrics = _select_all_paginated(client, "geo_external_signal_metrics", {
        "select": "query_id,value,norm_value,bucket_at",
        "metric_type": "eq.coverage_volume",
        "query_id": f"in.({','.join(query_names.keys())})",
        "bucket_at": f"gte.{previous_start.isoformat()}",
    })

    topics = []
    for query_id, name in query_names.items():
        current_raw, previous_raw = 0.0, 0.0
        current_norm, previous_norm = 0.0, 0.0
        for m in metrics:
            if m["query_id"] != query_id:
                continue
            bucket_at = m.get("bucket_at") or ""
            value = m.get("value") or 0
            norm_value = m.get("norm_value") or 0
            if bucket_at >= current_start.isoformat():
                current_raw += value
                current_norm += norm_value
            elif bucket_at >= previous_start.isoformat():
                previous_raw += value
                previous_norm += norm_value

        current_share = (current_raw / current_norm) if current_norm > 0 else None
        previous_share = (previous_raw / previous_norm) if previous_norm > 0 else None
        change_pct = None
        if current_share is not None and previous_share is not None and previous_share > 0:
            change_pct = round((current_share - previous_share) / previous_share * 100, 1)

        topics.append({
            "query_id": query_id, "name": name,
            "current_count": round(current_raw, 1), "previous_count": round(previous_raw, 1),
            "current_coverage_share": round(current_share, 6) if current_share is not None else None,
            "previous_coverage_share": round(previous_share, 6) if previous_share is not None else None,
            "change_pct": change_pct,
        })
    topics.sort(key=lambda t: t["current_count"], reverse=True)
    return topics


def search_stored_signals(client, *, text: str = None, date_from: str = None, date_to: str = None,
                           country: str = None, provider: str = None, query_id: str = None,
                           theme_id: str = None, source_domain: str = None,
                           limit: int = 50, offset: int = 0) -> dict:
    """News DB検索。DBに保存済みのExternal Signalsに対してのみ検索し、GDELT APIは叩かない
    （Ingestionとの責務分離）。query_id/theme_id指定時はgeo_external_signal_query_matches経由の
    2段階lookupでexternal_signal_idを絞ってからgeo_external_signalsを検索する。"""
    params = {"select": "*", "order": "published_at.desc.nullslast",
              "limit": str(limit), "offset": str(offset)}

    if query_id:
        match_rows = client.select("geo_external_signal_query_matches", {
            "select": "external_signal_id", "query_id": f"eq.{query_id}",
        })
        ids = [r["external_signal_id"] for r in match_rows]
        if not ids:
            return {"items": [], "total_hint": 0}
        params["external_signal_id"] = f"in.({','.join(ids)})"
    elif theme_id:
        ids = _signal_ids_for_theme(client, theme_id)
        if not ids:
            return {"items": [], "total_hint": 0}
        params["external_signal_id"] = f"in.({','.join(ids)})"

    if text:
        escaped = text.replace("%", "").replace(",", " ")
        params["or"] = f"(title.ilike.*{escaped}*,summary.ilike.*{escaped}*)"
    date_filters = []
    if date_from:
        date_filters.append(f"gte.{date_from}")
    if date_to:
        date_filters.append(f"lte.{date_to}")
    if date_filters:
        # requestsはlist値を同名の複数クエリパラメータへ展開する。PostgRESTは同一列への
        # 複数フィルタをAND結合するため、gte/lteを同時指定してもrangeとして機能する。
        params["published_at"] = date_filters if len(date_filters) > 1 else date_filters[0]
    if country:
        params["source_country"] = f"eq.{country}"
    if provider:
        params["provider"] = f"eq.{provider}"
    if source_domain:
        params["source_domain"] = f"eq.{source_domain}"

    items = client.select("geo_external_signals", params)
    return {"items": items, "total_hint": len(items)}
