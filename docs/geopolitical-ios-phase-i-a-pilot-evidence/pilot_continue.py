# -*- coding: utf-8 -*-
"""Continuation: approval -> publish -> extract-learning for a case whose earlier
steps already completed. Fixes the driver-script gap found in Case A's first run
(approve_in_app needs an explicit version_snapshot when Report Studio's own
collection-run pipeline was never invoked, per the pilot's documented scope decision)."""
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
    session_id = record["analysis_session_id"]
    project_id = record["report_project_id"]

    ok, code, current_design = get(f"/api/geo/ios/analysis-sessions/{session_id}/design-versions/current")
    body_html = "<h3>Investigation Question</h3><p>{}</p><h3>Key Actors</h3><ul>{}</ul><h3>Hypotheses</h3><ul>{}</ul>".format(
        (current_design or {}).get("investigation_question") or "",
        "".join(f"<li>{a}</li>" for a in (current_design or {}).get("key_actors") or []),
        "".join(f"<li>{h}</li>" for h in (current_design or {}).get("hypotheses") or []),
    )
    snapshot = {"run_id": "pilot-manual-snapshot", "syntheses": [{"final_html": body_html, "final_status": "PILOT"}]}

    def finding(severity, category, text):
        record.setdefault("findings", []).append({"severity": severity, "category": category, "text": text})
        log(f"FINDING[{severity}/{category}] {text}")

    # Approval (explicit snapshot this time -- driver fix, not a product fix)
    ok, code, approval = post("/api/geo/ios/approval/in-app", {
        "report_project_id": project_id, "approval_status": "approved", "approver": ACTOR,
        "version_label": "pilot-v2-revised", "version_snapshot": snapshot,
    })
    if not ok:
        finding("P0", "functional_defect", f"approve_in_app failed: {code} {approval}")
    else:
        record["approval_event_id"] = approval["approval_event_id"]
        record["steps"].append("approved")
        log("approved OK")

        # Publish
        ok, code, published = post("/api/geo/ios/knowledge-publication/report-studio", {
            "report_project_id": project_id, "approval_event_id": approval["approval_event_id"],
            "publisher": ACTOR, "publisher_role": ACTOR_ROLE,
            "human_visibility": human_visibility, "agent_sharing_policy": agent_sharing_policy,
        })
        if not ok:
            finding("P0", "functional_defect", f"publish failed: {code} {published}")
        else:
            record["published_knowledge_item_id"] = published["knowledge_item_id"]
            record["human_visibility"] = human_visibility
            record["agent_sharing_policy"] = agent_sharing_policy
            record["steps"].append("published")
            log(f"published OK: {published['knowledge_item_id']}")

            ok, code, ki = get(f"/api/geo/knowledge/items/{published['knowledge_item_id']}"
                                f"?include_non_retrievable=true&actor_role=knowledge_steward")
            record["published_knowledge_item_status_after_publish"] = ki.get("status") if ok else f"ERROR {code}"

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
