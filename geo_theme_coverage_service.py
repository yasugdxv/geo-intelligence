"""GEO_DIRECT_COLLECTION_V1: Coverage-aware Theme Freshness（決定論。LLMは使わない）。

目的: GDELT（広域メディア）が止まっていても、Direct Source（政府・国際機関）で覆える範囲は最新情報で
Monitoringできるようにする。ただし「一部のSourceがfreshだからテーマ全体がfresh」「GDELTがstaleだから
全テーマが使えない」のどちらにもしない。Themeごとに、どの観測範囲が最新で何が欠けているかを返す。

Theme Freshness（Theme Stateとは別物）:
  fresh              : 広域メディアがfresh、かつ設定済みの一次Direct Sourceがすべてfresh（または未設定）
  partial_freshness  : 一部の観測範囲だけ最新（例: 防衛省=fresh / 広域=stale）
  no_fresh_data      : 最新の観測範囲が1つも無い
  unknown            : 取得実績の記録が無く判定できない

不変の原則: 0件 ≠ stable ／ 最新データなし ≠ 変化なし ／ partialでは決してstableと断定しない。

入力: Source Set（Registry）、Sourceの取得実績（last_successful_ingestion_at）、最新published_at、
期待cadence（config/geo_direct_sources.yamlのcoverage_areas）、広域メディアの鮮度。
"""
from datetime import datetime, timedelta, timezone

import geo_direct_collection_common as direct_common
import geo_source_library_service as lib

DIRECT_CONNECTOR_TYPES = ("rss", "federal_register", "sanctions_xml", "ofac", "ukmto", "html_list")

THEME_FRESHNESS_LABEL = {
    "fresh": "最新", "partial_freshness": "一部の情報源は最新です",
    "no_fresh_data": "データ更新なし（状況判定不能）", "unknown": "鮮度を判定できません",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def source_freshness(source: dict, rule: dict | None, latest_published_at: str | None, *, now: datetime | None = None) -> dict:
    """1つのDirect Sourceの鮮度。取得の健全性（last_successful_ingestion_at）と、記事の最新性（basis=published）から決める。
    状態: fresh / stale / unknown。"""
    now = now or _now()
    rule = rule or {}
    ingest = lib._parse_ts(source.get("last_successful_ingestion_at"))
    collect_days = float(rule.get("collection_stale_days", 2))
    published = lib._parse_ts(latest_published_at)
    info = {"source_id": source["source_id"], "name": source.get("name"), "role": rule.get("role", "primary"),
            "area": rule.get("area"), "basis": rule.get("freshness_basis", "published"),
            "latest_published_at": published.isoformat() if published else None,
            "last_collected_at": ingest.isoformat() if ingest else None}
    if not source.get("automation_allowed") or not source.get("connector_enabled", True):
        return {**info, "status": "unknown", "reason": "取得が承認・有効化されていません"}
    if ingest is None:
        return {**info, "status": "unknown", "reason": "取得実績がありません"}
    failed = lib._parse_ts(source.get("last_failed_ingestion_at"))
    if failed is not None and failed > ingest:         # 直近の取得が失敗している（Collector障害・Endpoint変更）。他Sourceが最新でもfreshにしない
        return {**info, "status": "stale", "reason": f"直近の取得が失敗しています（失敗 {failed.isoformat()} / 最終成功 {ingest.isoformat()}）"}
    if (now - ingest) > timedelta(days=collect_days):
        return {**info, "status": "stale", "reason": f"取得が{collect_days:g}日以上止まっています（最終取得 {ingest.date()}）"}
    if info["basis"] == "collection":
        return {**info, "status": "fresh", "reason": "取得は正常です（このSourceは更新を差分で検知します）"}
    stale_days = float(rule.get("stale_after_days", 3))
    if published is None or (now - published) > timedelta(days=stale_days):
        return {**info, "status": "stale",
                "reason": f"公開元の最新記事が{(published.date().isoformat() if published else '不明')}で止まっています"}
    return {**info, "status": "fresh", "reason": f"最新記事 {published.date()}"}


def _rollup(statuses: list) -> str:
    """構成要素の状態: fresh / partial / stale / unknown / not_configured。"""
    if not statuses:
        return "not_configured"
    if all(s == "fresh" for s in statuses):
        return "fresh"
    if any(s == "fresh" for s in statuses):
        return "partial"
    if all(s == "unknown" for s in statuses):
        return "unknown"
    return "stale"


def compute_theme_freshness(*, broad: dict, direct: list) -> dict:
    """純関数。broad=get_global_freshness相当（status: fresh/stale/unknown）、direct=source_freshnessのlist。
    主要判定には role=primary のSourceだけを使う（auxiliaryは表示のみ）。"""
    primary = [d for d in direct if d["role"] == "primary"]
    primary_roll = _rollup([d["status"] for d in primary])
    broad_status = broad.get("status", "unknown")
    broad_fresh = broad_status == "fresh"
    fresh_primary = [d for d in primary if d["status"] == "fresh"]

    if broad_fresh and primary_roll in ("fresh", "not_configured"):
        state = "fresh"
    elif broad_fresh:                        # 広域は最新だが、一次Directの一部が止まっている
        state = "partial_freshness"
    elif fresh_primary:                      # 広域が止まっていても、最新の一次Directがある
        state = "partial_freshness"
    elif broad_status == "unknown" and primary_roll in ("unknown", "not_configured"):
        state = "unknown"
    else:
        state = "no_fresh_data"

    broad_roll = {"fresh": "fresh", "stale": "stale"}.get(broad_status, "unknown")
    specialist = [d for d in direct if d["role"] in ("specialist", "commercial")]      # 専門メディア・企業の運航情報（判定には使わない）
    components = [
        {"key": "primary", "label": "一次情報（公式・国際機関）", "status": primary_roll,
         "sources": [d for d in direct if d["role"] == "primary"]},
        {"key": "specialist", "label": "専門・商用Source", "sources": specialist,
         "status": _rollup([d["status"] for d in specialist]) if specialist else "not_configured"},
        {"key": "broad_media", "label": "広域Media", "status": broad_roll, "sources": [],
         "last_data_at": broad.get("last_data_at"), "label_detail": broad.get("label")},
    ]
    auxiliary = [d for d in direct if d["role"] not in ("primary", "specialist", "commercial")]
    if auxiliary:
        components[0]["auxiliary_sources"] = auxiliary
    return {"freshness_state": state, "freshness_label": THEME_FRESHNESS_LABEL[state], "components": components,
            "fresh_primary_source_ids": [d["source_id"] for d in fresh_primary]}


def compute_theme_coverage(client, sources: list, *, broad: dict, now: datetime | None = None,
                           direct_config: dict | None = None) -> dict:
    """Source Setのsources（resolve_set_sources）から、Theme単位のCoverageを算出する。"""
    now = now or _now()
    rules = (direct_config or direct_common.load_direct_config()).get("coverage_areas", {})
    live = [s for s in sources if not s.get("archived_at")]
    direct_rows = []
    if live:
        rows = client.select("geo_source_registry", {
            "select": "source_id,name,url,connector_type,automation_allowed,connector_enabled,last_successful_ingestion_at,last_failed_ingestion_at",
            "source_id": lib._in_list([s["source_id"] for s in live])})
        direct_rows = [r for r in rows if r.get("connector_type") in DIRECT_CONNECTOR_TYPES]
    latest = {}
    if direct_rows:
        latest = {x["source_id"]: x["latest_published_at"]
                  for x in lib.get_coverage_freshness(client, lookback_days=60)["sources"]}
    direct = [source_freshness(r, rules.get(lib.normalize_domain(r.get("url"))), latest.get(r["source_id"]), now=now)
              for r in direct_rows]
    return compute_theme_freshness(broad=broad, direct=direct)


compute_theme_freshness_for = compute_theme_coverage


def coverage_note(freshness_state: str, coverage: dict, *, alerts: int = 0) -> str | None:
    """「変化なし」と誤読させないための注記（決定的な文言）。fresh以外で必ず付ける。"""
    if freshness_state == "fresh":
        return None
    comps = {c["key"]: c for c in coverage["components"]}
    primary = comps["primary"]
    if freshness_state == "partial_freshness":
        fresh = [s["name"] for s in primary["sources"] if s["status"] == "fresh"]
        stale = [s["name"] for s in primary["sources"] if s["status"] == "stale"]
        parts = []
        if fresh:
            parts.append("確認できる情報源（" + "・".join(n.split("（")[0] for n in fresh) + "）の範囲では、"
                         + ("新しい動きを検知しました。" if alerts else "新しい動きは確認されていません。"))
        if comps["broad_media"]["status"] != "fresh":
            parts.append("ただし広域ニュースの取得が停止しているため、テーマ全体の「変化なし」を意味しません。")
        if stale:
            parts.append("更新が止まっている情報源: " + "・".join(n.split("（")[0] for n in stale) + "。")
        return "".join(parts)
    if freshness_state == "unknown":
        return "取得実績が無く鮮度を判定できません。「変化なし」を意味するものではありません。"
    if primary["status"] == "not_configured":
        return ("このテーマを直接覆う一次情報の情報源が未設定で、広域ニュースの取得も停止しているため、状況を判定できません。"
                "「変化なし」を意味するものではありません。")
    return "最新の情報源がないため状況を判定できません。「変化なし」を意味するものではありません。"


def classify_area(*, broad: dict, direct: list, broad_only: bool = False) -> dict:
    """Coverage別のLive Intelligence Status（READY / WARNING / BLOCKED）。純関数。
    主要判定には role=primary のDirect Sourceだけを使う（auxiliaryは参考表示）。"""
    broad_fresh = broad.get("status") == "fresh"
    if broad_only:
        return {"status": "READY" if broad_fresh else "BLOCKED",
                "reason": "広域メディア（GDELT）は最新です" if broad_fresh else
                          f"広域メディア（GDELT）の更新が停止しています（最終 {(broad.get('last_data_at') or '記録なし')[:10]}）"}
    primary = [d for d in direct if d["role"] == "primary"]
    fresh = [d for d in primary if d["status"] == "fresh"]
    if primary and len(fresh) == len(primary) and broad_fresh:
        return {"status": "READY", "reason": "一次情報の取得が最新で、広域メディアも最新です"}
    if fresh:
        missing = [d["name"].split("（")[0] for d in primary if d["status"] != "fresh"]
        parts = [] if broad_fresh else ["広域メディアは停止中"]
        if missing:
            parts.append("更新が止まっている一次情報: " + "・".join(missing))
        return {"status": "WARNING", "reason": "一部の情報源は最新（" + "・".join(d["name"].split("（")[0] for d in fresh) + "）。"
                + ("。".join(parts) if parts else "")}
    if not direct:
        return {"status": "BLOCKED", "reason": "この領域を直接覆う情報源が未設定で、広域メディア（GDELT）に依存しています"
                + ("" if not broad_fresh else "（広域は最新）")}
    return {"status": "BLOCKED", "reason": "直接の情報源に最新のものがありません"}


def classify_categories(categories: dict, *, broad: dict, direct_for) -> list:
    """領域内の種類別Coverage（Official/Military/Shipping/Specialist Media/Broad等）。
    状態: fresh / partial / stale / unknown / not_configured。国営・専門・報道Sourceは一次と同じ重みで領域の判定に使わない
    （ここは「どの種類が最新で、どの種類が欠けているか」を別々に見せるための表示）。"""
    out = []
    for key, c in categories.items():
        if c.get("broad"):
            status = "fresh" if broad.get("status") == "fresh" else "stale"
            out.append({"key": key, "label": c["label"], "status": status, "sources": [],
                        "note": "広域メディア（GDELT）" + ("は最新です" if status == "fresh" else "の更新が停止しています")})
            continue
        srcs = [direct_for(d) for d in c.get("domains", [])]
        out.append({"key": key, "label": c["label"], "status": _rollup([x["status"] for x in srcs]) if srcs else "not_configured",
                    "sources": [{"name": x["name"], "role": x["role"], "status": x["status"], "reason": x.get("reason")} for x in srcs]})
    return out


def build_live_intelligence_status(client, *, now: datetime | None = None, direct_config: dict | None = None) -> dict:
    """Coverage別のLive Intelligence Status。全体で1つのREADY/BLOCKEDにしない。"""
    now = now or _now()
    cfg = direct_config or direct_common.load_direct_config()
    rules = cfg.get("coverage_areas", {})
    broad = lib.get_global_freshness(client)
    latest = {x["source_id"]: x["latest_published_at"] for x in lib.get_coverage_freshness(client, lookback_days=60)["sources"]}
    cache: dict = {}

    def direct_for(domain: str):
        if domain not in cache:
            try:
                row = direct_common.resolve_source(client, domain)
                cache[domain] = source_freshness(row, rules.get(domain), latest.get(row["source_id"]), now=now)
            except direct_common.SourceNotReadyError as exc:
                cache[domain] = {"source_id": None, "name": domain, "role": rules.get(domain, {}).get("role", "primary"),
                                 "status": "unknown", "reason": str(exc)}
        return cache[domain]

    areas = []
    for key, a in (cfg.get("live_intelligence_areas") or {}).items():
        direct = [direct_for(d) for d in a.get("direct", [])]
        entry = {"key": key, "label": a["label"], "sources": direct,
                 **classify_area(broad=broad, direct=direct, broad_only=bool(a.get("broad_only")))}
        # Limited Live Intelligence Pilot: READY / WARNING の領域は評価可能。BLOCKEDは未評価（Broad Media系は停止中は評価しない）
        entry["pilot_evaluable"] = entry["status"] in ("READY", "WARNING")
        if a.get("categories"):          # Source数ではなく「種類別」のCoverage（例: Middle East Maritime）
            entry["categories"] = classify_categories(a["categories"], broad=broad, direct_for=direct_for)
            missing = [c["label"] for c in entry["categories"] if c["status"] == "not_configured"]
            if missing and entry["status"] != "BLOCKED":       # Source数ではなく種類で見る: 欠けている種類を理由に明示する
                entry["reason"] += "。未設定の種類: " + "・".join(missing)
        areas.append(entry)
    counts = {k: sum(1 for x in areas if x["status"] == k) for k in ("READY", "WARNING", "BLOCKED")}
    return {"generated_at": now.isoformat(), "areas": areas, "counts": counts, "broad_media": broad}
