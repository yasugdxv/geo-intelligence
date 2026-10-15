"""Source Library（組織共通のSource Master）: 検索・CRUD・Tag・Domain・Theme関連・Freshness。

設計方針（2026-10-05 ユーザー承認済みmigration方針）:
- geo_source_registryは「組織共通のSource Master」。theme_idはlegacy（nullable）で、
  Themeとの関係はgeo_source_theme_links（多対多）、分類Tagはgeo_source_tags（別物）。
- 削除は原則soft delete（archived_at）。物理削除は行わない（Article/Monitoringを壊さない）。
- Article→Source紐付けは「domainが一意に1Sourceへ確定できるものだけ」（geo_source_domains）。
  推測で紐付けない。ambiguousはsource_id=NULLのまま。
- 全件をFrontへ返さない（server-side filtering + pagination）。
- DB migration（sql/2026-10-05*.sql）未適用でも、一覧は旧スキーマ互換で動くようにする
  （legacy modeではtag/domain/theme_links等の新機能は空を返す）。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests

import geo_llm_common

# 新カラム（migration DB-1）。これらが無い旧スキーマではlegacy modeに落とす。
NEW_REGISTRY_COLUMNS = ("region", "media_type", "is_primary", "description",
                        "reliability", "registered_by", "archived_at")
EDITABLE_FIELDS = (
    "name", "url", "media_type", "region", "is_primary", "description", "reliability",
    "source_tier", "source_type", "notes", "languages", "update_cadence",
    "thematic_scope", "geographic_scope", "source_owner", "trust_tier",
)
MEDIA_TYPES = ("government", "international_org", "think_tank", "news_agency", "news",
               "academic", "company_ir", "data_provider", "social", "other")
TAG_TYPES = ("theme", "region", "actor", "other")
# Data Freshness Guard: これを超えて新着が無い場合は「更新停止(Stale)」とする（日数）。
STALE_AFTER_DAYS = 3

_FRACTION_RE = re.compile(r"\.(\d+)")
_DOMAIN_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://")
_DOMAIN_USERINFO_RE = re.compile(r"^[^/?#@]*@")
_DOMAIN_HOST_RE = re.compile(r"^([^/:?#]+)")


def normalize_domain(raw: str | None) -> str | None:
    """sql/2026-10-05_...db1の geo_normalize_domain() と同じ正規化（lowercase / scheme除去 /
    userinfo除去 / hostname抽出 / www除去 / 末尾ドット除去）。空ならNone。"""
    text = (raw or "").strip().lower()
    if not text:
        return None
    text = _DOMAIN_SCHEME_RE.sub("", text)
    text = _DOMAIN_USERINFO_RE.sub("", text)
    m = _DOMAIN_HOST_RE.match(text)
    if not m:
        return None
    host = m.group(1)
    if host.startswith("www."):
        host = host[4:]
    host = host.rstrip(".")
    return host or None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    # Postgresは末尾の0を省く（'.64088'のような小数秒5桁）。Python 3.10のfromisoformatは3/6桁以外を読めず、
    # 以前は黙ってNoneを返していた（取得実績が「無い」ように見える）。6桁へ揃えてから解釈する
    text = _FRACTION_RE.sub(lambda m: "." + m.group(1)[:6].ljust(6, "0"), str(value).replace("Z", "+00:00"), count=1)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _in_list(values) -> str:
    return "in.(" + ",".join(str(v) for v in values) + ")"


def _is_missing_column_error(exc: Exception) -> bool:
    """PostgRESTが「存在しないcolumn/table」で返す400/404（migration未適用）の判定。"""
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code in (400, 404)
    return False


# ---------------------------------------------------------------------------
# Schema状態（migration適用有無をUIへ伝える）
# ---------------------------------------------------------------------------

_SCHEMA_PROBES = {
    "db1_registry": ("geo_source_registry", "archived_at"),
    "db1_tags": ("geo_source_tags", "tag_id"),
    "db1_theme_links": ("geo_source_theme_links", "link_id"),
    "db1_domains": ("geo_source_domains", "domain_id"),
    "db2_source_sets": ("geo_source_sets", "source_set_id"),
    "db3_signal_source_id": ("geo_external_signals", "source_id"),
    "db4_monitoring": ("geo_monitoring_themes", "monitoring_theme_id"),
    "db5_research_context": ("geo_report_projects", "research_context"),
}


def get_schema_status(client) -> dict:
    """各migration Phaseが適用済みかを軽いselectで判定する（読み取りのみ）。"""
    status = {}
    for key, (table, column) in _SCHEMA_PROBES.items():
        try:
            client.select(table, {"select": column, "limit": "1"})
            status[key] = True
        except Exception:  # noqa: BLE001 — 未適用(400/404)も接続不可も「未確認」として扱う
            status[key] = False
    return {"phases": status, "all_applied": all(status.values())}


# ---------------------------------------------------------------------------
# 一覧・検索（server-side filtering + pagination）
# ---------------------------------------------------------------------------

_SORT_COLUMNS = {"name": "name", "created_at": "created_at", "updated_at": "updated_at",
                 "last_ingested": "last_successful_ingestion_at"}


def _source_ids_by_tag(client, tag_value: str, tag_type: str | None = None) -> list:
    params = {"select": "source_id", "tag_value": f"ilike.*{tag_value}*", "limit": "2000"}
    if tag_type:
        params["tag_type"] = f"eq.{tag_type}"
    try:
        return sorted({r["source_id"] for r in client.select("geo_source_tags", params)})
    except Exception as exc:  # noqa: BLE001
        if _is_missing_column_error(exc):
            return []
        raise


def _source_ids_by_theme(client, theme_id: str) -> list:
    try:
        rows = client.select("geo_source_theme_links", {
            "select": "source_id", "theme_id": f"eq.{theme_id}", "limit": "2000"})
    except Exception as exc:  # noqa: BLE001
        if not _is_missing_column_error(exc):
            raise
        rows = []
    legacy = client.select("geo_source_registry", {
        "select": "source_id", "theme_id": f"eq.{theme_id}", "limit": "2000"})
    return sorted({r["source_id"] for r in rows} | {r["source_id"] for r in legacy})


def list_sources(client, *, q: str | None = None, media_type: str | None = None,
                 region: str | None = None, language: str | None = None,
                 is_primary: bool | None = None, theme_id: str | None = None,
                 tag: str | None = None, include_archived: bool = False,
                 created_after: str | None = None, updated_after: str | None = None,
                 sort: str = "name", order: str = "asc", page: int = 1, page_size: int = 25) -> dict:
    """Source一覧。全てserver-side filter。戻り値はhas_more方式（件数countはPostgREST
    Preferヘッダ依存のため使わず、page_size+1件取得して次ページ有無を判定する）。"""
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 100))
    params: dict = {"select": "*", "limit": str(page_size + 1), "offset": str((page - 1) * page_size)}
    sort_col = _SORT_COLUMNS.get(sort, "name")
    params["order"] = f"{sort_col}.{'desc' if order == 'desc' else 'asc'}"

    id_filters: list = []  # 各要素はsource_idのlist（AND条件で交差させる）
    if tag:
        id_filters.append(_source_ids_by_tag(client, tag))
    if theme_id:
        id_filters.append(_source_ids_by_theme(client, theme_id))
    if id_filters:
        ids = set(id_filters[0])
        for other in id_filters[1:]:
            ids &= set(other)
        if not ids:
            return {"sources": [], "page": page, "page_size": page_size, "has_more": False,
                    "legacy_mode": False}
        params["source_id"] = _in_list(sorted(ids))

    if q:
        term = q.strip().replace(",", " ").replace("(", " ").replace(")", " ")
        fields = ("name", "description", "notes", "url", "thematic_scope", "geographic_scope")
        params["or"] = "(" + ",".join(f"{f}.ilike.*{term}*" for f in fields) + ")"
    if media_type:
        params["media_type"] = f"eq.{media_type}"
    if region:
        params["region"] = f"ilike.*{region}*"
    if language:
        params["languages"] = "cs.{" + language + "}"
    if is_primary is not None:
        params["is_primary"] = f"eq.{'true' if is_primary else 'false'}"
    if created_after:
        params["created_at"] = f"gte.{created_after}"
    if not include_archived:
        params["archived_at"] = "is.null"

    legacy_mode = False
    try:
        rows = client.select("geo_source_registry", params)
    except Exception as exc:  # noqa: BLE001
        if not _is_missing_column_error(exc):
            raise
        # migration DB-1未適用: 新カラムの条件を外して旧スキーマで取り直す（読み取りのみ）
        legacy_mode = True
        for key in ("media_type", "region", "is_primary", "archived_at"):
            params.pop(key, None)
        if q:
            params["or"] = "(" + ",".join(
                f"{f}.ilike.*{params_term}*" for f, params_term in
                (("name", q.strip()), ("url", q.strip()), ("notes", q.strip()))) + ")"
        rows = client.select("geo_source_registry", params)

    has_more = len(rows) > page_size
    rows = rows[:page_size]
    _attach_list_extras(client, rows)
    return {"sources": rows, "page": page, "page_size": page_size, "has_more": has_more,
            "legacy_mode": legacy_mode}


def _attach_list_extras(client, rows: list) -> None:
    """一覧行へTag・最近の記事数・Freshnessを付与する（1ページ分だけ、まとめて取得）。"""
    if not rows:
        return
    ids = [r["source_id"] for r in rows]
    tags_by_source: dict = {}
    try:
        for t in client.select("geo_source_tags", {
                "select": "source_id,tag_type,tag_value,origin,is_confirmed",
                "source_id": _in_list(ids), "limit": "5000"}):
            tags_by_source.setdefault(t["source_id"], []).append(t)
    except Exception as exc:  # noqa: BLE001
        if not _is_missing_column_error(exc):
            raise
    # 一覧は軽量に（直近90日・最大2000行。article_count_totalは「90日内の件数」を意味する）
    stats = _article_stats_by_source(client, ids, lookback_days=90, limit=2000)
    for r in rows:
        r["tags"] = tags_by_source.get(r["source_id"], [])
        s = stats.get(r["source_id"], {})
        r["article_count_30d"] = s.get("count_30d", 0)
        r["article_count_total"] = s.get("count_total", 0)
        r["last_article_at"] = s.get("last_article_at")
        r["freshness"] = compute_freshness(s.get("last_article_at"), r.get("last_successful_ingestion_at"))


def _article_stats_by_source(client, source_ids: list, *, lookback_days: int = 365, limit: int = 3000) -> dict:
    """source_idごとの記事数(30日/累計)と最新記事日時。geo_external_signals.source_id
    （migration DB-3）が無い場合は空dict（=「不明」であって「0件」とは扱わせないため、
    呼び出し側はfreshnessを'unknown'にする）。"""
    try:
        rows = client.select("geo_external_signals", {
            "select": "source_id,published_at,created_at", "source_id": _in_list(source_ids),
            "published_at": f"gte.{(_now() - timedelta(days=lookback_days)).isoformat()}",
            "order": "published_at.desc", "limit": str(limit)})
    except Exception as exc:  # noqa: BLE001
        if _is_missing_column_error(exc):
            return {}
        raise
    cutoff = _now() - timedelta(days=30)
    out: dict = {}
    for row in rows:
        s = out.setdefault(row["source_id"], {"count_30d": 0, "count_total": 0, "last_article_at": None})
        s["count_total"] += 1
        ts = _parse_ts(row.get("published_at")) or _parse_ts(row.get("created_at"))
        if ts and ts >= cutoff:
            s["count_30d"] += 1
        if ts and (s["last_article_at"] is None or ts.isoformat() > s["last_article_at"]):
            s["last_article_at"] = ts.isoformat()
    return out


# ---------------------------------------------------------------------------
# Data Freshness Guard
# ---------------------------------------------------------------------------

def compute_freshness(last_article_at: str | None, last_ingestion_at: str | None = None, *,
                      stale_after_days: int = STALE_AFTER_DAYS, now: datetime | None = None) -> dict:
    """「記事が来ていない」ことを「安定」「変化なし」と誤表示させないためのGuard。
    status: fresh（stale_after_days以内に取得あり）/ stale（それ以上更新なし）/
    unknown（取得実績の記録なし）。last_atは記事日時と取得成功日時の新しい方。"""
    now = now or _now()
    candidates = [t for t in (_parse_ts(last_article_at), _parse_ts(last_ingestion_at)) if t]
    if not candidates:
        return {"status": "unknown", "last_data_at": None, "days_since": None,
                "stale_after_days": stale_after_days, "label": "データ取得実績なし"}
    last = max(candidates)
    days = (now - last).total_seconds() / 86400
    status = "fresh" if days <= stale_after_days else "stale"
    return {"status": status, "last_data_at": last.isoformat(), "days_since": round(days, 1),
            "stale_after_days": stale_after_days,
            "label": "データ更新あり" if status == "fresh" else "更新停止（Stale）"}


# 注意: geo_external_signalsにはcreated_atのindexが無く、並べ替え/絞り込みに使うと負荷時にSupabaseの
# statement timeout(500)になる（2026-10-05 E2Eで実際に発生）。時系列の取得は必ずindex済みの
# published_atを使う（nullslastでNULLを後ろへ）。
DIRECT_PROVIDERS = ("rss", "official")


def get_global_freshness(client, *, lookback_days: int = 30, exclude_providers: tuple = DIRECT_PROVIDERS) -> dict:
    """広域メディア探索（GDELT等。Direct Source=rss/officialを除く）の最新取得状況。Monitoringの「データ更新なし」判定の基礎。
    index済みのpublished_atを下限つきで引く（下限なしのORDER BYはindexを使えず遅い）。
    lookback_days以内に1件も無ければ「更新停止」として扱う（Stable側へは決して倒さない）。

    GEO_DIRECT_COLLECTION_V1: 政府・国際機関のDirect Source（rss/official）は特定領域しか覆わないため、
    「全体の鮮度」には含めない（含めると、GDELTが止まったままでも、Direct Sourceのある領域以外まで
    「変化なし」と誤表示される）。Direct Sourceの鮮度はget_coverage_freshnessで領域別に見る。
    exclude_providers=()で全Providerを対象にする（従来の挙動）。"""
    since = (_now() - timedelta(days=lookback_days)).isoformat()
    try:
        params = {
            # is_demo=true（検証用データ。Phase D.1のE2Eで[TEST]行が混入した）は鮮度の判定に含めない。
            # 含めると、実際には記事が増えていないのに「fresh」と誤表示される
            "select": "published_at,created_at", "published_at": f"gte.{since}", "is_demo": "neq.true",
            "order": "published_at.desc", "limit": "1"}
        if exclude_providers:
            params["provider"] = f"not.in.({','.join(exclude_providers)})"
        rows = client.select("geo_external_signals", params)
    except Exception as exc:  # noqa: BLE001
        if _is_missing_column_error(exc):
            rows = []
        else:
            raise
    if not rows:
        return {"status": "stale", "last_data_at": None, "days_since": None,
                "stale_after_days": STALE_AFTER_DAYS,
                "label": f"更新停止（{lookback_days}日以上データなし）"}
    last = rows[0].get("published_at") or rows[0].get("created_at")
    return compute_freshness(last, None)


def get_coverage_freshness(client, *, lookback_days: int = 30, limit: int = 2000) -> dict:
    """Direct Source（rss/official）のSource別の鮮度。「どのSource・領域が最新か」を示す（Coverage別表示用）。
    is_demoは除外。件数・最新published_at・最新observed_at（収集時刻）をsource_idごとに返す。"""
    since = (_now() - timedelta(days=lookback_days)).isoformat()
    rows = _safe_select(client, "geo_external_signals", {
        "select": "source_id,provider,published_at,observed_at", "provider": f"in.({','.join(DIRECT_PROVIDERS)})",
        "is_demo": "neq.true", "published_at": f"gte.{since}", "order": "published_at.desc", "limit": str(limit)})
    by_source: dict = {}
    for r in rows:
        e = by_source.setdefault(r.get("source_id"), {"count": 0, "latest_published_at": None, "latest_collected_at": None})
        e["count"] += 1
        if r.get("published_at") and (not e["latest_published_at"] or r["published_at"] > e["latest_published_at"]):
            e["latest_published_at"] = r["published_at"]
        if r.get("observed_at") and (not e["latest_collected_at"] or r["observed_at"] > e["latest_collected_at"]):
            e["latest_collected_at"] = r["observed_at"]
    names = {}
    ids = [i for i in by_source if i]
    if ids:
        names = {x["source_id"]: x["name"] for x in client.select("geo_source_registry", {
            "select": "source_id,name", "source_id": _in_list(ids)})}
    out = []
    for sid, e in by_source.items():
        out.append({"source_id": sid, "name": names.get(sid), **e,
                    **{"freshness": compute_freshness(e["latest_published_at"], None)}})
    return {"lookback_days": lookback_days, "sources": sorted(out, key=lambda x: x["latest_published_at"] or "", reverse=True)}


# ---------------------------------------------------------------------------
# 詳細
# ---------------------------------------------------------------------------

def get_source(client, source_id: str) -> dict | None:
    rows = client.select("geo_source_registry", {"select": "*", "source_id": f"eq.{source_id}"})
    return rows[0] if rows else None


def _safe_select(client, table: str, params: dict) -> list:
    try:
        return client.select(table, params)
    except Exception as exc:  # noqa: BLE001
        if _is_missing_column_error(exc):
            return []
        raise


def get_source_detail(client, source_id: str, *, article_limit: int = 20) -> dict | None:
    source = get_source(client, source_id)
    if not source:
        return None
    tags = _safe_select(client, "geo_source_tags", {
        "select": "*", "source_id": f"eq.{source_id}", "order": "tag_type.asc", "limit": "500"})
    domains = _safe_select(client, "geo_source_domains", {
        "select": "*", "source_id": f"eq.{source_id}", "limit": "200"})
    links = _safe_select(client, "geo_source_theme_links", {
        "select": "*", "source_id": f"eq.{source_id}", "limit": "500"})
    theme_ids = sorted({l["theme_id"] for l in links} | ({source["theme_id"]} if source.get("theme_id") else set()))
    themes = {}
    if theme_ids:
        # geo_themesの表示名列はname_ja/name_en（nameという列は無い）。UI向けにnameへ正規化する。
        for t in client.select("geo_themes", {"select": "theme_id,name_ja,name_en,code",
                                              "theme_id": _in_list(theme_ids)}):
            t["name"] = t.get("name_ja") or t.get("name_en") or t.get("code")
            themes[t["theme_id"]] = t
    for l in links:
        l["theme"] = themes.get(l["theme_id"])
    articles = _safe_select(client, "geo_external_signals", {
        "select": "external_signal_id,title,summary,url,source_name,source_domain,published_at,created_at",
        "source_id": f"eq.{source_id}", "order": "published_at.desc", "limit": str(article_limit)})
    stats = _article_stats_by_source(client, [source_id]).get(source_id, {})
    usage = _source_usage(client, source_id)
    return {
        "source": source, "tags": tags, "domains": domains, "theme_links": links,
        "legacy_theme": themes.get(source.get("theme_id")),
        "recent_articles": articles,
        "performance": {
            "article_count_total": stats.get("count_total", 0),
            "article_count_30d": stats.get("count_30d", 0),
            "last_article_at": stats.get("last_article_at"),
            **usage,
        },
        "freshness": compute_freshness(stats.get("last_article_at"), source.get("last_successful_ingestion_at")),
    }


def _source_usage(client, source_id: str) -> dict:
    """Source performance/usage（データが存在する範囲）: Source Set採用数・Monitoring採用数・
    Evidence(geo_documents)件数。"""
    set_rows = _safe_select(client, "geo_source_set_members", {
        "select": "source_set_id", "source_id": f"eq.{source_id}", "limit": "1000"})
    set_ids = sorted({r["source_set_id"] for r in set_rows})
    monitoring_count = 0
    if set_ids:
        monitoring_count = len(_safe_select(client, "geo_monitoring_themes", {
            "select": "monitoring_theme_id", "source_set_id": _in_list(set_ids),
            "status": "neq.archived", "limit": "1000"}))
    docs = client.select("geo_documents", {"select": "document_id", "source_id": f"eq.{source_id}", "limit": "1000"})
    return {"source_set_count": len(set_ids), "monitoring_count": monitoring_count,
            "evidence_document_count": len(docs)}


# ---------------------------------------------------------------------------
# CRUD（soft delete）
# ---------------------------------------------------------------------------

class SourceValidationError(ValueError):
    pass


def _clean_payload(data: dict) -> dict:
    out = {}
    for key in EDITABLE_FIELDS:
        if key not in data:
            continue
        val = data[key]
        if isinstance(val, str):
            val = val.strip() or None
        out[key] = val
    if "media_type" in out and out["media_type"] and out["media_type"] not in MEDIA_TYPES:
        raise SourceValidationError(f"media_typeは{', '.join(MEDIA_TYPES)}のいずれかにしてください")
    if out.get("reliability") and out["reliability"] not in ("high", "medium", "low"):
        raise SourceValidationError("reliabilityはhigh/medium/lowのいずれかにしてください")
    if out.get("url") and not re.match(r"^https?://", out["url"], re.I):
        out["url"] = "https://" + out["url"]
    return out


def create_source(client, data: dict, *, registered_by: str | None) -> dict:
    payload = _clean_payload(data)
    if not payload.get("name"):
        raise SourceValidationError("nameは必須です")
    payload.setdefault("source_type", "other")
    payload["registered_by"] = registered_by
    inserted = client.insert("geo_source_registry", [payload])
    source = inserted[0]
    domain = normalize_domain(source.get("url"))
    if domain:
        _add_domain(client, source["source_id"], domain, is_primary=True)
    return source


def update_source(client, source_id: str, data: dict) -> dict | None:
    if not get_source(client, source_id):
        return None
    payload = _clean_payload(data)
    if "name" in payload and not payload["name"]:
        raise SourceValidationError("nameは空にできません")
    if payload:
        client.update("geo_source_registry", {"source_id": f"eq.{source_id}"}, payload)
    new_domain = normalize_domain(payload.get("url")) if "url" in payload else None
    if new_domain:
        _add_domain(client, source_id, new_domain, is_primary=False)
    return get_source(client, source_id)


def archive_source(client, source_id: str) -> dict | None:
    """soft delete。ArticleのsourceId・Source Set・Monitoringの参照は維持する。"""
    if not get_source(client, source_id):
        return None
    client.update("geo_source_registry", {"source_id": f"eq.{source_id}"}, {"archived_at": _now().isoformat()})
    return get_source(client, source_id)


def restore_source(client, source_id: str) -> dict | None:
    if not get_source(client, source_id):
        return None
    client.update("geo_source_registry", {"source_id": f"eq.{source_id}"}, {"archived_at": None})
    return get_source(client, source_id)


# ---------------------------------------------------------------------------
# Domain alias
# ---------------------------------------------------------------------------

def _add_domain(client, source_id: str, domain: str, *, is_primary: bool, match_enabled: bool = True) -> dict | None:
    existing = client.select("geo_source_domains", {
        "select": "*", "source_id": f"eq.{source_id}", "domain": f"eq.{domain}"})
    if existing:
        return existing[0]
    try:
        inserted = client.insert("geo_source_domains", [{
            "source_id": source_id, "domain": domain, "is_primary": is_primary,
            "match_enabled": match_enabled}])
    except Exception as exc:  # noqa: BLE001
        if _is_missing_column_error(exc):  # migration未適用: Domain Aliasはスキップ
            return None
        raise
    return inserted[0] if inserted else None


def add_domain(client, source_id: str, raw_domain: str, *, is_primary: bool = False) -> dict:
    domain = normalize_domain(raw_domain)
    if not domain:
        raise SourceValidationError("domainを正しく指定してください")
    row = _add_domain(client, source_id, domain, is_primary=is_primary)
    if row is None:
        raise SourceValidationError("Domain Alias用テーブルが未作成です（migration DB-1が必要）")
    return row


def remove_domain(client, source_id: str, domain_id: str) -> None:
    client.delete("geo_source_domains", {"domain_id": f"eq.{domain_id}", "source_id": f"eq.{source_id}"})


def set_domain_match_enabled(client, source_id: str, domain_id: str, enabled: bool) -> None:
    client.update("geo_source_domains", {"domain_id": f"eq.{domain_id}", "source_id": f"eq.{source_id}"},
                  {"match_enabled": bool(enabled)})


def find_domain_conflicts(client, domain: str) -> list:
    """同じdomainを持つ（=ambiguousの原因になる）Source一覧。登録時の警告に使う。"""
    d = normalize_domain(domain)
    rows = _safe_select(client, "geo_source_domains", {
        "select": "source_id", "domain": f"eq.{d}", "match_enabled": "eq.true", "limit": "50"})
    return sorted({r["source_id"] for r in rows})


# ---------------------------------------------------------------------------
# Tag（AI提案とHuman確定を区別）
# ---------------------------------------------------------------------------

def _validate_tag(tag_type: str, tag_value: str) -> tuple:
    if tag_type not in TAG_TYPES:
        raise SourceValidationError(f"tag_typeは{', '.join(TAG_TYPES)}のいずれかにしてください")
    value = (tag_value or "").strip().lstrip("#").strip()
    if not value or len(value) > 80:
        raise SourceValidationError("tag_valueは1〜80文字で指定してください")
    return tag_type, value


def add_tag(client, source_id: str, tag_type: str, tag_value: str, *, origin: str = "human",
            created_by: str | None = None) -> dict:
    tag_type, value = _validate_tag(tag_type, tag_value)
    existing = client.select("geo_source_tags", {
        "select": "*", "source_id": f"eq.{source_id}", "tag_type": f"eq.{tag_type}",
        "tag_value": f"ilike.{value}"})
    existing = [t for t in existing if t["tag_value"].lower() == value.lower()]
    if existing:
        # 同じTagが既にある: 人間が追加した場合は確定に昇格する（AI提案の承認と同義）
        if origin == "human" and not existing[0].get("is_confirmed"):
            client.update("geo_source_tags", {"tag_id": f"eq.{existing[0]['tag_id']}"},
                          {"is_confirmed": True, "origin": "human"})
            existing[0].update({"is_confirmed": True, "origin": "human"})
        return existing[0]
    inserted = client.insert("geo_source_tags", [{
        "source_id": source_id, "tag_type": tag_type, "tag_value": value, "origin": origin,
        "is_confirmed": origin != "ai", "created_by": created_by}])
    return inserted[0]


def confirm_tag(client, source_id: str, tag_id: str, *, confirmed: bool = True) -> None:
    client.update("geo_source_tags", {"tag_id": f"eq.{tag_id}", "source_id": f"eq.{source_id}"},
                  {"is_confirmed": bool(confirmed)})


def remove_tag(client, source_id: str, tag_id: str) -> None:
    client.delete("geo_source_tags", {"tag_id": f"eq.{tag_id}", "source_id": f"eq.{source_id}"})


_TAG_SUGGEST_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["tags"],
    "properties": {"tags": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["tag_type", "tag_value"],
        "properties": {"tag_type": {"type": "string", "enum": list(TAG_TYPES)},
                       "tag_value": {"type": "string"}}}}},
}
_TAG_SUGGEST_PROMPT = (
    "あなたは地政学インテリジェンス部門の情報ソース司書です。与えられた情報ソースの情報だけから、"
    "分類Tagを最大10個提案してください。tag_type: theme(主題。例 Semiconductor, ExportControl, AI)、"
    "region(地域・国)、actor(人物・組織)、other。記載の無い事実を推測で足さないこと。"
    "tag_valueは短い英数字または日本語の単語（#なし）。")


def suggest_tags(azure_client, model: str, source: dict) -> dict:
    """LLMでTagを提案する（保存はしない）。失敗してもエラー情報を返すだけで例外にしない
    （LLMが落ちてもSource基本情報の登録・表示は止めない）。"""
    if azure_client is None:
        return {"tags": [], "error": "AI分析エンジンが設定されていません"}
    info = {k: source.get(k) for k in ("name", "url", "media_type", "region", "description",
                                       "thematic_scope", "geographic_scope", "source_owner", "notes")}
    user_prompt = "\n".join(f"{k}: {v}" for k, v in info.items() if v)
    try:
        result = geo_llm_common.call_llm_structured(
            azure_client, model, _TAG_SUGGEST_PROMPT, user_prompt, _TAG_SUGGEST_SCHEMA,
            "source_tag_suggestion", max_attempts=2)
    except geo_llm_common.GeoLLMError as exc:
        return {"tags": [], "error": f"Tag提案に失敗しました: {exc}"}
    tags, seen = [], set()
    for t in result["data"].get("tags", []):
        value = (t.get("tag_value") or "").strip().lstrip("#").strip()
        key = (t.get("tag_type"), value.lower())
        if value and key not in seen and t.get("tag_type") in TAG_TYPES:
            seen.add(key)
            tags.append({"tag_type": t["tag_type"], "tag_value": value})
    return {"tags": tags[:10], "error": None}


def save_suggested_tags(client, source_id: str, tags: list) -> list:
    """AI提案Tagを origin=ai / is_confirmed=false で保存する（人間が承認・修正するまで確定扱いにしない）。"""
    saved = []
    for t in tags:
        try:
            tag_type, value = _validate_tag(t.get("tag_type"), t.get("tag_value"))
        except SourceValidationError:
            continue
        saved.append(add_tag(client, source_id, tag_type, value, origin="ai"))
    return saved


# ---------------------------------------------------------------------------
# Source ↔ Theme（多対多）
# ---------------------------------------------------------------------------

def add_theme_link(client, source_id: str, theme_id: str, *, relation_type: str = "manual",
                   relevance_score: float | None = None, created_by: str | None = None) -> dict:
    if relation_type not in ("manual", "ai_recommended", "usage_derived"):
        raise SourceValidationError("relation_typeが不正です")
    existing = client.select("geo_source_theme_links", {
        "select": "*", "source_id": f"eq.{source_id}", "theme_id": f"eq.{theme_id}",
        "relation_type": f"eq.{relation_type}"})
    if existing:
        return existing[0]
    inserted = client.insert("geo_source_theme_links", [{
        "source_id": source_id, "theme_id": theme_id, "relation_type": relation_type,
        "relevance_score": relevance_score, "created_by": created_by}])
    return inserted[0]


def remove_theme_link(client, source_id: str, link_id: str) -> None:
    client.delete("geo_source_theme_links", {"link_id": f"eq.{link_id}", "source_id": f"eq.{source_id}"})


# ---------------------------------------------------------------------------
# Article → Source の照合（登録済みdomain限定・一意に確定できるものだけ）
# ---------------------------------------------------------------------------

def resolve_source_for_domain(client, raw_domain: str) -> dict:
    """1つのdomain→1つのSourceに一意に確定できる場合のみsource_idを返す。複数候補は
    ambiguous、無ければunmatched（推測で紐付けない）。"""
    domain = normalize_domain(raw_domain)
    if not domain:
        return {"status": "no_domain", "source_id": None}
    rows = _safe_select(client, "geo_source_domains", {
        "select": "source_id,is_primary", "domain": f"eq.{domain}", "match_enabled": "eq.true", "limit": "50"})
    ids = sorted({r["source_id"] for r in rows})
    if not ids:
        return {"status": "unmatched", "source_id": None}
    live = [r["source_id"] for r in client.select("geo_source_registry", {
        "select": "source_id,archived_at", "source_id": _in_list(ids)}) if not r.get("archived_at")]
    if len(live) == 1:
        method = "exact_domain" if any(r["is_primary"] for r in rows if r["source_id"] == live[0]) else "alias_domain"
        return {"status": "unique", "source_id": live[0], "match_method": method, "domain": domain}
    return {"status": "ambiguous" if live else "unmatched", "source_id": None}
