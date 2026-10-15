"""GEO_DIRECT_COLLECTION_V1: Direct Collection（政府・国際機関のRSS/API直接取得）の共通処理。

Sustainability / ERM のcrawler・記事には一切依存しない（Geo独立）。
- 設定: config/geo_direct_sources.yaml（取得口とGeo向け方針。Source IDは書かず、Registryのdomainで引く）
- Sourceの同一性と承認ゲート: Geo Source Registry（geo_source_registry / geo_source_domains）
- 保存先: 既存の geo_external_signals（provider='rss' / 'official'、source_idで出所を追跡）
"""
import html
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

import geo_connector_base as base
import geo_external_signal_service as signal_service
import geo_source_library_service as lib

CONFIG_PATH = Path(__file__).parent / "config" / "geo_direct_sources.yaml"
USER_AGENT = "GeoDirectCollection/1.0 (internal intelligence research; contact: IT)"
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class SourceNotReadyError(RuntimeError):
    """Registry上でSourceが一意に解決できない／archived／ゲート未承認など、取得してはいけない状態。"""


def load_direct_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def resolve_source(client, registry_domain: str) -> dict:
    """Registryのdomainから、取得対象のSource行を一意に解決する（推測で紐付けない）。"""
    res = lib.resolve_source_for_domain(client, registry_domain)
    if res["status"] != "unique":
        raise SourceNotReadyError(f"domain {registry_domain} をRegistryで一意に解決できません（{res['status']}）")
    rows = client.select("geo_source_registry", {"select": "*", "source_id": f"eq.{res['source_id']}", "limit": "1"})
    if not rows or rows[0].get("archived_at"):
        raise SourceNotReadyError(f"domain {registry_domain} のSourceがありません（またはarchived）")
    return rows[0]


def http_get(url: str, *, params: dict = None, accept: str = "*/*", config: dict = None) -> requests.Response:
    """社内proxy・SSL設定（config.json）に従って取得する。一時的な失敗のみretry（429/5xx/接続系）。"""
    proxies, verify = {}, True
    try:
        from config_utils import load_config, make_proxies
        cfg = config or load_config()
        proxies = make_proxies(cfg)
        verify = cfg.get("ssl", {}).get("verify", True)
    except Exception:  # noqa: BLE001 — 設定が読めない環境でも取得自体は試みる
        pass
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    last = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, params=params, headers=headers, proxies=proxies, verify=verify,
                                timeout=REQUEST_TIMEOUT)
            if resp.status_code in (429, 500, 502, 503, 504):
                last = RuntimeError(f"HTTP {resp.status_code}")
                time.sleep(2 ** attempt * 2)
                continue
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"取得に失敗（{MAX_RETRIES}回retry後）: {url} : {last}")


def clean_text(value: str | None, limit: int = 1500) -> str | None:
    """HTMLタグ・実体参照を除去して空白を整える。"""
    if not value:
        return None
    text = _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", value))).strip()
    return text[:limit] if text else None


def passes_policy(title: str | None, summary: str | None, policy: dict | None) -> bool:
    """Geo向けの決定的なkeyword方針。include_anyが空なら全件対象、exclude_anyは常に優先して除外。"""
    policy = policy or {}
    haystack = f"{title or ''} {summary or ''}".lower()
    if any(k.lower() in haystack for k in (policy.get("exclude_any") or [])):
        return False
    include = policy.get("include_any") or []
    return not include or any(k.lower() in haystack for k in include)


def build_signal(*, source_row: dict, provider: str, title: str, url: str, summary: str | None,
                  published_at: datetime | None, language: str | None, collector: str,
                  connector_version: str, endpoint: str, ingestion_run_id: str,
                  provider_record_id: str | None = None, extra_metadata: dict | None = None) -> dict:
    """geo_external_signals挿入用の正規形（Provenance込み）。dedupは既存のnormalize_url/compute_dedup_key。"""
    domain = lib.normalize_domain(source_row.get("url"))
    normalized_url = signal_service.normalize_url(url)
    dedup_key = signal_service.compute_dedup_key(
        canonical_url=url or None, normalized_url=normalized_url or None, source_domain=domain,
        normalized_title=signal_service._normalize_title(title))
    return {
        "provider": provider, "provider_record_id": provider_record_id, "signal_type": "article",
        "title": title, "summary": summary, "url": url or None, "canonical_url": url or None,
        "normalized_url": normalized_url or None,
        "source_id": source_row["source_id"], "source_domain": domain, "source_name": source_row.get("name"),
        "source_country": None, "source_language": language,
        "published_at": published_at.astimezone(timezone.utc).isoformat() if published_at else None,
        "timestamp_source": "publisher" if published_at else "unknown",
        "entities": [], "countries": [], "tone": None, "coverage_value": None,
        "source_role": "official_statement", "evidence_status": "unverified",
        "raw_metadata": {"collector": collector, "connector_version": connector_version, "endpoint": endpoint,
                         "ingestion_run_id": ingestion_run_id, "source_id": source_row["source_id"],
                         "collected_at": datetime.now(timezone.utc).isoformat(), **(extra_metadata or {})},
        "validation_status": "unreviewed", "knowledge_promotion_status": "not_promoted", "dedup_key": dedup_key,
    }


def record_source_outcome(client, source_row: dict, run: dict, *, ok: bool, is_demo: bool = False) -> None:
    """Source Registryの鮮度指標（最終成功・最終失敗・失敗回数）を更新する。is_demo実行では書き換えない（既存Connectorと同じ）。"""
    if is_demo:
        return
    now_iso = datetime.now(timezone.utc).isoformat()
    if ok:
        patch = {"last_ingestion_run_id": run["ingestion_run_id"], "last_successful_ingestion_at": now_iso, "failure_count": 0}
    else:
        patch = {"last_failed_ingestion_at": now_iso, "failure_count": int(source_row.get("failure_count") or 0) + 1}
    client.update("geo_source_registry", {"source_id": f"eq.{source_row['source_id']}"}, patch)


def finish_run(client, run: dict, *, status: str, counts: dict, error_summary: str | None,
               connector_version: str, parser_version: str) -> None:
    base.complete_ingestion_run(client, ingestion_run_id=run["ingestion_run_id"], status=status, counts=counts,
                                 error_summary=error_summary, connector_version=connector_version,
                                 parser_version=parser_version)
