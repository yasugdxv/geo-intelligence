# -*- coding: utf-8 -*-
"""Phase I-A.1 Section 9: Mechanical E2E Recheck.
Reuses Case A's existing report_project_id (already reviewed/approved/published from
Phase I-A) and re-runs authorization -> approval -> publish through the REAL live
server with real X-IOS-Credential headers, proving the SEC-1/SEC-2 fix works E2E
against genuine authenticated identities (not just unit tests)."""
import json
import sys
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8001"
CRED_PATH = Path(r"C:/Users/269811/AppData/Local/Temp/claude/c--Users-269811-python/01ca28f6-ef1d-4f14-9fac-b45ac6653512/scratchpad/pilot_credentials.txt")
creds = dict(line.split() for line in CRED_PATH.read_text(encoding="utf-8").splitlines() if line.strip())

# Case A/B/Cのreport_projectはこのセッション中に(並行Report Studio作業側の新規delete機能の
# テスト等で)削除されていたため、Section 9再検証用に新規projectを1件作成して使う
# (Phase I-Aの3ケース自体を再実行する必要はない、との指示どおり最小限の再検証)。
project_id = "cb2715f8-1113-4fd0-a5c8-afcba25cdae6"


def post(path, body, headers=None, timeout=60):
    r = requests.post(f"{BASE}{path}", json=body, headers=headers or {}, timeout=timeout)
    try:
        data = r.json()
    except Exception:
        data = {"raw_text": r.text[:400]}
    return r.status_code, data


results = {}

# 1. No credential -> 401
code, body = post("/api/geo/ios/approval/in-app", {
    "report_project_id": project_id, "approval_status": "approved", "approver": "recheck-noauth"})
results["no_credential"] = {"status": code, "body": body}

# 2. Unknown credential -> 401
code, body = post("/api/geo/ios/approval/in-app", {
    "report_project_id": project_id, "approval_status": "approved", "approver": "recheck-unknown"},
    headers={"X-IOS-Credential": "not-a-real-one"})
results["unknown_credential"] = {"status": code, "body": body}

# 3. Authenticated analyst, payload claims approver_role=approver -> DENY (403)
code, body = post("/api/geo/ios/approval/in-app", {
    "report_project_id": project_id, "approval_status": "approved", "approver": "recheck-analyst-spoof",
    "approver_role": "approver",
    "version_snapshot": {"run_id": "recheck", "syntheses": [{"final_html": "<p>x</p>", "final_status": "PILOT"}]},
    }, headers={"X-IOS-Credential": creds["pilot_analyst"]})
results["analyst_spoofing_approver"] = {"status": code, "body": body}

# 4. Authenticated approver, payload claims role=analyst -> ALLOW, uses real role
code, body = post("/api/geo/ios/approval/in-app", {
    "report_project_id": project_id, "approval_status": "approved", "approver": "recheck-real-approver",
    "approver_role": "analyst",
    "version_snapshot": {"run_id": "recheck", "syntheses": [{"final_html": "<p>recheck</p>", "final_status": "PILOT"}]},
}, headers={"X-IOS-Credential": creds["pilot_approver"]})
results["real_approver_with_downgrade_claim"] = {"status": code, "body": body}
approval_event_id = body.get("approval_event_id") if code == 200 else None

# 5. Publish with knowledge_steward credential, using the fresh approval_event_id
if approval_event_id:
    code, body = post("/api/geo/ios/knowledge-publication/report-studio", {
        "report_project_id": project_id, "approval_event_id": approval_event_id,
        "publisher": "recheck-steward", "human_visibility": "company",
        "agent_sharing_policy": "geopolitics_only",
    }, headers={"X-IOS-Credential": creds["pilot_knowledge_steward"]})
    results["publish_with_real_knowledge_steward"] = {"status": code, "body": body}

# 6. Publish attempt with approver credential (wrong role for publish) -> DENY
if approval_event_id:
    code, body = post("/api/geo/ios/knowledge-publication/report-studio", {
        "report_project_id": project_id, "approval_event_id": approval_event_id,
        "publisher": "recheck-wrong-role", "human_visibility": "company",
        "agent_sharing_policy": "geopolitics_only",
    }, headers={"X-IOS-Credential": creds["pilot_approver"]})
    results["publish_with_wrong_role_approver"] = {"status": code, "body": body}

out_path = Path(__file__).parent / "phase_ia1_recheck_results.json"
out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"wrote {out_path}")
for k, v in results.items():
    print(k, "->", v["status"])
