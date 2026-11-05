# -*- coding: utf-8 -*-
"""Continuation: publish -> extract-learning ONLY, for a case whose approval
already succeeded (approval_event_id already recorded). Skips re-approving to
avoid creating a duplicate approval_event."""
import json
import sys
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8001"
OUT_DIR = Path(__file__).parent
ACTOR = "claude_pilot_operational_proxy"
ACTOR_ROLE = "knowledge_steward"


def post(path, body, timeout=180):
    r = requests.post(f"{BASE}{path}", json=body, timeout=timeout)
    try:
        data = r.json()
    except Exception:
        data = {"raw_text": r.text[:500]}
    return r.ok, r.status_code, data


def get(path, timeout=60):
    r = requests.get(f"{BASE}{path}", timeout=timeout)
    try:
        data = r.json()
    except Exception:
        data = {"raw_text": r.text[:500]}
    return r.ok, r.status_code, data


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


if __name__ == "__main__":
    case_key = sys.argv[1]
    human_visibility = sys.argv[2]
    agent_sharing_policy = sys.argv[3]

    record_path = OUT_DIR / f"evaluation_record_{case_key}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    project_id = record["report_project_id"]
    approval_event_id = record["approval_event_id"]

    def finding(severity, category, text):
        record.setdefault("findings", []).append({"severity": severity, "category": category, "text": text})
        log(f"FINDING[{severity}/{category}] {text}")

    # Publish (retry now that the Phase E migration is confirmed applied)
    ok, code, published = post("/api/geo/ios/knowledge-publication/report-studio", {
        "report_project_id": project_id, "approval_event_id": approval_event_id,
        "publisher": ACTOR, "publisher_role": ACTOR_ROLE,
        "human_visibility": human_visibility, "agent_sharing_policy": agent_sharing_policy,
    })
    if not ok:
        finding("P0", "functional_defect", f"publish failed (retry after migration fix): {code} {published}")
    else:
        record["published_knowledge_item_id"] = published["knowledge_item_id"]
        record["human_visibility"] = human_visibility
        record["agent_sharing_policy"] = agent_sharing_policy
        record["steps"].append("published")
        log(f"published OK: {published['knowledge_item_id']}")

        ok, code, ki = get(f"/api/geo/knowledge/items/{published['knowledge_item_id']}"
                            f"?include_non_retrievable=true&actor_role=knowledge_steward")
        record["published_knowledge_item_status_after_publish"] = ki.get("status") if ok else f"ERROR {code}"
        log(f"status_after_publish: {record['published_knowledge_item_status_after_publish']}")

        # Extract Learning (real LLM call)
        t0 = time.time()
        ok, code, extracted = post(
            f"/api/geo/ios/knowledge-publication/report-studio/{project_id}/extract-learning",
            {"actor_id": ACTOR}, timeout=240)
        record.setdefault("timings_sec", {})["extract_learning"] = round(time.time() - t0, 1)
        if not ok:
            finding("P1", "functional_defect", f"extract_learning failed: {code} {extracted}")
        else:
            record["learning_candidates_created"] = len(extracted.get("candidates", []))
            record["learning_extraction_reason"] = extracted.get("reason")
            record["learning_candidate_ids"] = [c["candidate_id"] for c in extracted.get("candidates", [])]
            record["steps"].append("learning_extraction_run")
            log(f"extract_learning OK: {len(extracted.get('candidates', []))} candidates, reason={extracted.get('reason')}")

    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"updated {record_path}")
