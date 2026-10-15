"""地政学ナレッジ＆インテリジェンスDB Phase2A: Media Episode Candidate生成。

Media Episode Candidateは**Media×GKG document単位**（1 GKG document = 1 candidate）。
GKG Tone/Themes/Personsはdocument単体のobservationであり、Eventは`related_external_event_ids`
という補助情報に留め、特定Eventへの評価として帰属させない（document全体のToneを「このEventへの
論調」と解釈しない）。

domain対応はコード定数ではなくsidecar reference data
（docs/media-intelligence/media_source_domain_map.json）として管理する。
Media Intelligence本体（Media Profile/Media Pattern）は一切更新しない。
"""
import hashlib
import json
from pathlib import Path

from geo_external_signal_service import _select_in_chunked

_DOMAIN_MAP_PATH = Path(__file__).parent / "docs" / "media-intelligence" / "media_source_domain_map.json"
_domain_map_cache = None


def load_domain_map() -> dict:
    """docs/media-intelligence/media_source_domain_map.jsonを読み込み{domain: source_id}へ変換。
    モジュールレベルでキャッシュする。"""
    global _domain_map_cache
    if _domain_map_cache is not None:
        return _domain_map_cache
    with open(_DOMAIN_MAP_PATH, encoding="utf-8") as f:
        data = json.load(f)
    mapping = {}
    for entry in data.get("mappings", []):
        for domain in entry.get("domains", []):
            mapping[domain.lower()] = entry["source_id"]
    _domain_map_cache = mapping
    return mapping


def match_media_source(source_domain):
    if not source_domain:
        return None
    normalized = source_domain.strip().lower()
    if normalized.startswith("www."):
        normalized = normalized[4:]
    return load_domain_map().get(normalized)


def compute_fingerprint(media_source_id: str, provider_record_id: str) -> str:
    basis = f"media:{media_source_id}:{provider_record_id}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def generate_candidates_for_batch(client, *, annotations: list, signals_by_id: dict,
                                   related_event_ids_by_signal_id: dict = None,
                                   ingestion_run_id: str = None, is_demo: bool = False) -> dict:
    """入力はGKG document（保存済みannotation情報を含むdict、"external_signal_id"キー必須）単位。
    domain解決はgeo_external_signals.source_domain（Phase1で正規化済み）を第一優先とし、
    それが空/欠損の場合のみGKGのsource_common_nameへfallbackする。"""
    related_event_ids_by_signal_id = related_event_ids_by_signal_id or {}
    created = 0
    skipped_unmatched_domain = 0

    candidates_to_process = []
    for ann in annotations:
        signal_id = ann.get("external_signal_id")
        if not signal_id:
            continue
        signal_row = signals_by_id.get(signal_id, {})
        domain = signal_row.get("source_domain") or ann.get("source_common_name")
        media_source_id = match_media_source(domain)
        if not media_source_id:
            skipped_unmatched_domain += 1
            continue

        fingerprint = compute_fingerprint(media_source_id, ann["provider_record_id"])
        candidates_to_process.append({
            "fingerprint": fingerprint, "media_source_id": media_source_id, "source_domain": domain,
            "provider_record_id": ann["provider_record_id"],
            "annotation_id": ann.get("annotation_id"), "external_signal_id": signal_id,
            "headline": signal_row.get("title"), "document_identifier": ann.get("document_identifier"),
            "persons_mentioned": ann.get("persons") or [], "organizations_mentioned": ann.get("organizations") or [],
            "themes_observed": ann.get("themes") or [], "tone": ann.get("tone"),
            "published_at": signal_row.get("published_at"),
            "related_external_event_ids": related_event_ids_by_signal_id.get(signal_id, []),
        })

    if not candidates_to_process:
        return {"created": created, "skipped_unmatched_domain": skipped_unmatched_domain}

    fingerprints = [c["fingerprint"] for c in candidates_to_process]
    existing_rows = _select_in_chunked(
        client, "geo_media_episode_candidates", select="fingerprint",
        in_column="fingerprint", in_values=fingerprints)
    existing_fingerprints = {r["fingerprint"] for r in existing_rows}

    to_insert = []
    for c in candidates_to_process:
        if c["fingerprint"] in existing_fingerprints:
            continue
        to_insert.append({
            "media_source_id": c["media_source_id"], "source_domain": c["source_domain"],
            "provider_record_id": c["provider_record_id"], "annotation_id": c["annotation_id"],
            "external_signal_id": c["external_signal_id"], "fingerprint": c["fingerprint"],
            "headline": c["headline"], "document_identifier": c["document_identifier"],
            "persons_mentioned": c["persons_mentioned"], "organizations_mentioned": c["organizations_mentioned"],
            "themes_observed": c["themes_observed"], "tone": c["tone"], "published_at": c["published_at"],
            "related_external_event_ids": c["related_external_event_ids"],
            "candidate_status": "candidate", "ingestion_run_id": ingestion_run_id, "is_demo": is_demo,
        })

    if to_insert:
        client.insert("geo_media_episode_candidates", to_insert)
        created = len(to_insert)

    return {"created": created, "skipped_unmatched_domain": skipped_unmatched_domain}


def list_media_episode_candidates(client, *, media_source_id: str = None, candidate_status: str = None,
                                   limit: int = 50, offset: int = 0) -> dict:
    params = {"select": "*", "order": "created_at.desc", "limit": str(limit), "offset": str(offset)}
    if media_source_id:
        params["media_source_id"] = f"eq.{media_source_id}"
    if candidate_status:
        params["candidate_status"] = f"eq.{candidate_status}"
    return {"candidates": client.select("geo_media_episode_candidates", params)}


_ALLOWED_TRANSITIONS = {
    "candidate": {"reviewed", "accepted", "rejected"},
    "reviewed": {"accepted", "rejected"},
}


def update_media_episode_candidate_status(client, *, candidate_id: str, candidate_status: str,
                                           reviewed_by: str, review_notes: str = None) -> dict:
    """Accept時もMedia Profile/Media Patternへの書き込みは一切行わない（単純なUPDATEのみ）。"""
    from datetime import datetime, timezone

    rows = client.select("geo_media_episode_candidates", {
        "select": "candidate_status", "media_episode_candidate_id": f"eq.{candidate_id}", "limit": "1",
    })
    if not rows:
        raise ValueError(f"media_episode_candidate_idが見つかりません: {candidate_id}")
    current_status = rows[0]["candidate_status"]
    allowed = _ALLOWED_TRANSITIONS.get(current_status, set())
    if candidate_status not in allowed:
        raise ValueError(f"許容されない状態遷移です: {current_status} -> {candidate_status}")

    now_iso = datetime.now(timezone.utc).isoformat()
    client.update("geo_media_episode_candidates", {"media_episode_candidate_id": f"eq.{candidate_id}"}, {
        "candidate_status": candidate_status, "reviewed_by": reviewed_by,
        "review_notes": review_notes, "reviewed_at": now_iso,
    })
    result_rows = client.select("geo_media_episode_candidates", {
        "select": "*", "media_episode_candidate_id": f"eq.{candidate_id}", "limit": "1",
    })
    return result_rows[0] if result_rows else None
