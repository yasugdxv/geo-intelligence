"""地政学ナレッジ＆インテリジェンスDB Phase2A: External Event / Event-Signal Link /
GKG Annotation の正規化・Dedup・bulk upsert処理。

Phase1の`geo_external_signal_service`から`normalize_url`/`_select_in_chunked`/
`_update_in_chunked`/`_select_all_paginated`をそのままimportして再利用する（重複実装しない）。
"""
from datetime import datetime, timezone

import geo_external_signal_service as signal_svc
from geo_external_signal_service import _select_in_chunked, _update_in_chunked  # noqa: F401


def normalize_event_row(raw: dict, *, event_batch_timestamp: str, ingestion_run_id: str) -> dict:
    """`event_batch_timestamp`はこのEventの実際の取得元batch(=EventTimeDate)であり、
    現在処理中のMention/GKG batchのtimestampではない（呼び出し元で明示的に区別して渡すこと）。"""
    return {
        "provider": "gdelt",
        "provider_event_id": raw["provider_event_id"],
        "event_code": raw.get("event_code"),
        "event_base_code": raw.get("event_base_code"),
        "event_root_code": raw.get("event_root_code"),
        "quad_class": raw.get("quad_class"),
        "goldstein_scale": raw.get("goldstein_scale"),
        "num_mentions": raw.get("num_mentions"),
        "num_sources": raw.get("num_sources"),
        "num_articles": raw.get("num_articles"),
        "avg_tone": raw.get("avg_tone"),
        "actor1_name": raw.get("actor1_name"),
        "actor1_country_code": raw.get("actor1_country_code"),
        "actor2_name": raw.get("actor2_name"),
        "actor2_country_code": raw.get("actor2_country_code"),
        "action_geo_full_name": raw.get("action_geo_full_name"),
        "action_geo_country_code": raw.get("action_geo_country_code"),
        "action_geo_lat": raw.get("action_geo_lat"),
        "action_geo_long": raw.get("action_geo_long"),
        "event_date": raw.get("event_date"),
        "event_date_added": raw.get("event_date_added"),
        "source_url": raw.get("source_url"),
        "source_role": "event_data",
        "evidence_status": "unverified",
        "batch_timestamp": event_batch_timestamp,
        "raw_metadata": raw.get("raw_metadata"),
        "ingestion_run_id": ingestion_run_id,
    }


def bulk_upsert_events(client, normalized_list: list) -> dict:
    """(provider, provider_event_id)で既存チェック→未登録のみbulk insert。既存行は
    num_mentions/num_sources/num_articles/avg_tone等をoriginating Event rowのスナップショット
    値でそのまま上書きする軽量UPDATE（独自に加算・再集計はしない。GDELTのbatchファイルは
    公開後immutableなため通常は同じ値になる）。"""
    if not normalized_list:
        return {"inserted": 0, "updated": 0, "event_id_by_provider_event_id": {}}

    provider_event_ids = [n["provider_event_id"] for n in normalized_list]
    existing_rows = _select_in_chunked(
        client, "geo_external_events", select="external_event_id,provider_event_id",
        in_column="provider_event_id", in_values=provider_event_ids,
        extra_params={"provider": "eq.gdelt"})
    existing_by_id = {r["provider_event_id"]: r["external_event_id"] for r in existing_rows}

    to_insert = [n for n in normalized_list if n["provider_event_id"] not in existing_by_id]
    inserted_rows = client.insert("geo_external_events", to_insert) if to_insert else []

    for n in normalized_list:
        if n["provider_event_id"] in existing_by_id:
            client.update("geo_external_events",
                          {"external_event_id": f"eq.{existing_by_id[n['provider_event_id']]}"},
                          {"num_mentions": n.get("num_mentions"), "num_sources": n.get("num_sources"),
                           "num_articles": n.get("num_articles"), "avg_tone": n.get("avg_tone")})

    event_id_by_provider_event_id = dict(existing_by_id)
    for row in inserted_rows:
        event_id_by_provider_event_id[row["provider_event_id"]] = row["external_event_id"]

    return {"inserted": len(to_insert), "updated": len(existing_by_id),
            "event_id_by_provider_event_id": event_id_by_provider_event_id}


def match_signals_for_urls(client, urls: list) -> dict:
    """呼び出し元は`is_web=True`の行のIdentifierのみをこの関数へ渡すこと。
    戻り値: {original_url: {"external_signal_id":..., "link_method": "gdelt_mention_exact_url"
    |"gdelt_mention_normalized_url"}}。まずurl/canonical_urlの完全一致、残りはnormalize_url後に
    normalized_urlで一致判定する。"""
    if not urls:
        return {}

    matches = {}
    exact_rows_by_url = _select_in_chunked(
        client, "geo_external_signals", select="external_signal_id,url",
        in_column="url", in_values=urls)
    for r in exact_rows_by_url:
        if r.get("url") in urls and r["url"] not in matches:
            matches[r["url"]] = {"external_signal_id": r["external_signal_id"],
                                  "link_method": "gdelt_mention_exact_url"}

    exact_rows_by_canonical = _select_in_chunked(
        client, "geo_external_signals", select="external_signal_id,canonical_url",
        in_column="canonical_url", in_values=urls)
    for r in exact_rows_by_canonical:
        cu = r.get("canonical_url")
        if cu in urls and cu not in matches:
            matches[cu] = {"external_signal_id": r["external_signal_id"],
                            "link_method": "gdelt_mention_exact_url"}

    remaining = [u for u in urls if u not in matches]
    if remaining:
        normalized_to_original = {}
        for u in remaining:
            normalized_to_original.setdefault(signal_svc.normalize_url(u), u)
        norm_rows = _select_in_chunked(
            client, "geo_external_signals", select="external_signal_id,normalized_url",
            in_column="normalized_url", in_values=list(normalized_to_original.keys()))
        for r in norm_rows:
            nu = r.get("normalized_url")
            if nu in normalized_to_original:
                original_url = normalized_to_original[nu]
                if original_url not in matches:
                    matches[original_url] = {"external_signal_id": r["external_signal_id"],
                                              "link_method": "gdelt_mention_normalized_url"}

    return matches


def normalize_link(mention_row: dict, *, external_event_id: str, external_signal_id: str,
                    link_method: str) -> dict:
    return {
        "external_event_id": external_event_id,
        "external_signal_id": external_signal_id,
        "link_method": link_method,
        "mention_time_date": mention_row.get("mention_time_date"),
        "confidence": mention_row.get("confidence"),
        "mention_doc_tone": mention_row.get("mention_doc_tone"),
    }


def bulk_upsert_event_signal_links(client, links: list) -> dict:
    """(external_event_id, external_signal_id)でdedup。同一batch内で複数Mentionが同じペアに
    該当する場合はconfidence最大（同点ならmention_time_date早い方）の1件のみinsertする。
    既に別batchで当該ペアが存在する場合はinsertせず、confidence等の再集計・上書きも行わない
    （Immutable方針、初回観測時の値を保持する）。"""
    if not links:
        return {"inserted": 0}

    best_by_pair = {}
    for link in links:
        key = (link["external_event_id"], link["external_signal_id"])
        current = best_by_pair.get(key)
        if current is None:
            best_by_pair[key] = link
            continue
        cur_conf = current.get("confidence") or -1
        new_conf = link.get("confidence") or -1
        if new_conf > cur_conf:
            best_by_pair[key] = link
        elif new_conf == cur_conf:
            cur_time = current.get("mention_time_date") or ""
            new_time = link.get("mention_time_date") or ""
            if new_time and (not cur_time or new_time < cur_time):
                best_by_pair[key] = link

    candidate_links = list(best_by_pair.values())
    event_ids = list({link["external_event_id"] for link in candidate_links})
    existing_rows = _select_in_chunked(
        client, "geo_external_event_signal_links", select="external_event_id,external_signal_id",
        in_column="external_event_id", in_values=event_ids)
    existing_pairs = {(r["external_event_id"], r["external_signal_id"]) for r in existing_rows}

    to_insert = [link for link in candidate_links
                 if (link["external_event_id"], link["external_signal_id"]) not in existing_pairs]
    if to_insert:
        client.insert("geo_external_event_signal_links", to_insert)
    return {"inserted": len(to_insert)}


def normalize_annotation(gkg_row: dict, *, external_signal_id: str, batch_timestamp: str,
                          ingestion_run_id: str) -> dict:
    return {
        "provider": "gdelt",
        "provider_record_id": gkg_row["provider_record_id"],
        "external_signal_id": external_signal_id,
        "source_common_name": gkg_row.get("source_common_name"),
        "document_identifier": gkg_row.get("document_identifier"),
        "gkg_date": gkg_row.get("gkg_date"),
        "themes": gkg_row.get("themes") or [],
        "persons": gkg_row.get("persons") or [],
        "organizations": gkg_row.get("organizations") or [],
        "locations": gkg_row.get("locations") or [],
        "tone": gkg_row.get("tone"),
        "tone_positive": gkg_row.get("tone_positive"),
        "tone_negative": gkg_row.get("tone_negative"),
        "tone_polarity": gkg_row.get("tone_polarity"),
        "tone_activity_density": gkg_row.get("tone_activity_density"),
        "tone_self_group_density": gkg_row.get("tone_self_group_density"),
        "tone_word_count": gkg_row.get("tone_word_count"),
        "batch_timestamp": batch_timestamp,
        "raw_metadata": gkg_row.get("raw_metadata"),
        "ingestion_run_id": ingestion_run_id,
    }


def bulk_upsert_annotations(client, normalized_list: list) -> dict:
    """(provider, provider_record_id)でdedup。既存なら何もしない（GKG documentは1回きりの
    観測であり再更新の必要が薄いため、Phase2Aではinsertのみ・updateなし）。"""
    if not normalized_list:
        return {"inserted": 0, "annotation_id_by_provider_record_id": {}}

    provider_record_ids = [n["provider_record_id"] for n in normalized_list]
    existing_rows = _select_in_chunked(
        client, "geo_external_signal_annotations", select="annotation_id,provider_record_id",
        in_column="provider_record_id", in_values=provider_record_ids,
        extra_params={"provider": "eq.gdelt"})
    existing_by_id = {r["provider_record_id"]: r["annotation_id"] for r in existing_rows}

    to_insert = [n for n in normalized_list if n["provider_record_id"] not in existing_by_id]
    inserted_rows = client.insert("geo_external_signal_annotations", to_insert) if to_insert else []

    annotation_id_by_provider_record_id = dict(existing_by_id)
    for row in inserted_rows:
        annotation_id_by_provider_record_id[row["provider_record_id"]] = row["annotation_id"]

    return {"inserted": len(to_insert),
            "annotation_id_by_provider_record_id": annotation_id_by_provider_record_id}


def get_related_event_ids_for_signal(client, external_signal_id: str) -> list:
    """geo_external_event_signal_linksから当該Signalに紐づくexternal_event_id一覧を返す
    （Media Candidateのrelated_external_event_ids用、補助参照のみ）。"""
    rows = client.select("geo_external_event_signal_links", {
        "select": "external_event_id", "external_signal_id": f"eq.{external_signal_id}",
    })
    return list({r["external_event_id"] for r in rows})


def search_recent_events_for_theme(client, *, theme_id: str = None, limit: int = 10) -> list:
    """Expert AI統合用。theme_id指定時は既存N:M経路で絞り込む: geo_external_signal_queriesで
    theme_id一致するquery_id一覧→geo_external_signal_query_matchesで該当external_signal_id
    一覧→geo_external_event_signal_linksで該当external_event_id一覧→geo_external_eventsを
    その集合で検索。geo_external_events自体にtheme_id列は持たない。"""
    params = {"select": "*",
              "order": "event_date.desc.nullslast,event_date_added.desc.nullslast,created_at.desc",
              "limit": str(limit)}
    if theme_id:
        query_rows = client.select("geo_external_signal_queries", {
            "select": "query_id", "theme_id": f"eq.{theme_id}",
        })
        query_ids = [r["query_id"] for r in query_rows]
        if not query_ids:
            return []
        match_rows = _select_in_chunked(
            client, "geo_external_signal_query_matches", select="external_signal_id",
            in_column="query_id", in_values=query_ids)
        signal_ids = list({r["external_signal_id"] for r in match_rows})
        if not signal_ids:
            return []
        link_rows = _select_in_chunked(
            client, "geo_external_event_signal_links", select="external_event_id",
            in_column="external_signal_id", in_values=signal_ids)
        event_ids = list({r["external_event_id"] for r in link_rows})
        if not event_ids:
            return []
        params["external_event_id"] = f"in.({','.join(event_ids)})"

    return client.select("geo_external_events", params)
