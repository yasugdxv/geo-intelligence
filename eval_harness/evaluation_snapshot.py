# -*- coding: utf-8 -*-
"""Phase I-B1 Section 2: Evaluation Snapshot builder.

Freezes every field a real Expert Review session needs into ONE immutable, self-
contained JSON file, read-only against the real production DB. Does not modify the
production Report/Review workflow in any way -- it only reads rows that already exist
via the normal service-layer functions (geo_ios_analysis_session_service,
geo_ios_review_service, geo_report_studio_service) and serializes them.

Immutability: freeze_snapshot_to_file() refuses to overwrite an existing snapshot file,
and every snapshot carries its own content_sha256 so tampering after the fact is
detectable (recompute the hash over the snapshot with content_sha256 removed, compare).
This is a file-level guarantee, not a DB-level one -- no new production table/column is
introduced, per the instruction not to change the production Report Workflow.
"""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_analysis_session_service as session_service  # noqa: E402
import geo_ios_report_studio_adapter as report_studio_adapter  # noqa: E402
import geo_ios_review_service as review_service  # noqa: E402

SCHEMA_VERSION = "phase-i-b1-evaluation-snapshot-v1"


class SnapshotError(Exception):
    pass


def build_evaluation_snapshot(client, analysis_session_id: str) -> dict:
    """Read-only. Raises SnapshotError if the session doesn't exist. Every field listed
    in the Phase I-B1 package spec (Section 2 of the governing instruction) is present,
    even when empty/None for a session that hasn't reached that stage yet -- an absent
    key would be ambiguous between "not reached" and "forgot to capture"; an explicit
    null is not."""
    session = session_service.get_session(client, analysis_session_id)
    if not session:
        raise SnapshotError(f"analysis_session_id not found: {analysis_session_id}")

    all_candidates = session_service.list_scenario_candidates(client, analysis_session_id)
    displayed_top5 = [c for c in all_candidates if c.get("displayed")]
    selected_id = session.get("selected_scenario_id")

    design_versions = session_service.list_design_history(client, analysis_session_id)
    current_design = design_versions[-1] if design_versions else None

    report_project_id = session.get("linked_report_project_id")
    draft_or_report = None
    if report_project_id:
        draft_or_report = report_studio_adapter.get_report_project_summary(client, report_project_id)

    ai_review_findings = []
    if report_project_id:
        for cycle in review_service.list_review_cycles(client, report_project_id):
            comments = review_service.list_comments(client, cycle["review_cycle_id"])
            ai_review_findings.append({"cycle": cycle, "comments": comments})

    knowledge_prompt_model_versions = [
        {"scenario_candidate_id": c.get("scenario_candidate_id"), "model": c.get("model"),
         "prompt_version": c.get("prompt_version"), "cqas_knowledge_version": c.get("cqas_knowledge_version"),
         "generated_at": c.get("generated_at")}
        for c in all_candidates
    ]

    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "analysis_session_id": analysis_session_id,
        "theme": session.get("theme") or session.get("title"),
        "session_title": session.get("title"),
        "all_cqas_candidates": all_candidates,
        "candidate_ranks": {c.get("scenario_candidate_id"): c.get("rank") for c in all_candidates},
        "displayed_top5_ids": [c.get("scenario_candidate_id") for c in displayed_top5],
        "selected_scenario_candidate_id": selected_id,
        "analysis_design_current": current_design,
        "design_versions_history": design_versions,
        "linked_report_project_id": report_project_id,
        "draft_or_report": draft_or_report,
        "ai_review_findings": ai_review_findings,
        "knowledge_prompt_model_versions": knowledge_prompt_model_versions,
        "generation_timestamps": {
            "session_created_at": session.get("created_at"),
            "candidates_generated_at": (all_candidates[0].get("generated_at") if all_candidates else None),
            "design_created_at": (current_design or {}).get("created_at"),
            "snapshot_frozen_at": None,  # filled in below, after content hash is computed over everything else
        },
    }
    snapshot["generation_timestamps"]["snapshot_frozen_at"] = snapshot["frozen_at"]
    snapshot["content_sha256"] = _hash_snapshot(snapshot)
    return snapshot


def _hash_snapshot(snapshot: dict) -> str:
    body = {k: v for k, v in snapshot.items() if k != "content_sha256"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def verify_snapshot_integrity(snapshot: dict) -> bool:
    """Recompute the hash and compare -- detects any edit made after freezing."""
    recorded = snapshot.get("content_sha256")
    return recorded is not None and recorded == _hash_snapshot(snapshot)


def freeze_snapshot_to_file(snapshot: dict, out_path: Path) -> Path:
    """Refuses to overwrite an existing frozen snapshot (immutability guarantee --
    once Expert Review has started against a snapshot file, nothing may silently
    replace it; to capture a materially different state, freeze to a NEW path)."""
    out_path = Path(out_path)
    if out_path.exists():
        raise FileExistsError(
            f"A snapshot is already frozen at {out_path} -- refusing to overwrite. "
            f"If the underlying session genuinely changed, freeze to a new path "
            f"(e.g. append a version suffix) instead of replacing this one.")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


if __name__ == "__main__":
    import config_utils
    import geo_db_client

    if len(sys.argv) < 3:
        print("usage: python evaluation_snapshot.py <analysis_session_id> <out_path.json>")
        raise SystemExit(1)
    cfg = config_utils.load_config()
    real_client = geo_db_client.SupabaseClient(cfg)
    snap = build_evaluation_snapshot(real_client, sys.argv[1])
    path = freeze_snapshot_to_file(snap, Path(sys.argv[2]))
    print(f"frozen: {path}")
    print(f"content_sha256: {snap['content_sha256']}")
    print(f"integrity check: {verify_snapshot_integrity(snap)}")
