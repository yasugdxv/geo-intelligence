# -*- coding: utf-8 -*-
"""Phase I-A.1 Section 5: SEC-3 real-environment Cross-Agent E2E (test cases A-E)."""
import json
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8001"
CRED_PATH = Path(r"C:/Users/269811/AppData/Local/Temp/claude/c--Users-269811-python/01ca28f6-ef1d-4f14-9fac-b45ac6653512/scratchpad/pilot_credentials.txt")
creds = dict(line.split() for line in CRED_PATH.read_text(encoding="utf-8").splitlines() if line.strip())


def query(body, api_key=None, timeout=60):
    headers = {"X-API-Key": api_key} if api_key else {}
    r = requests.post(f"{BASE}/api/v1/geo-intelligence/query", json=body, headers=headers, timeout=timeout)
    try:
        data = r.json()
    except Exception:
        data = {"raw_text": r.text[:400]}
    return r.status_code, data


results = {}
base_body = {"requester_app": "geopolitics", "request_type": "user_question",
             "question": "EUDR規制のサプライチェーン影響について", "themes": ["EUDR"], "country_region": ["EU"]}

# A. Sustainability credential + requester_app=geopolitics(spoof) -> authenticated as Sustainability
code, body = query(base_body, api_key=creds["sustainability_agent"])
results["A_sustainability_cred_spoofing_geopolitics"] = {
    "status": code, "knowledge_sufficiency": body.get("knowledge_sufficiency"),
    "references_count": len(body.get("references") or []), "error": body.get("error")}

# D. unknown credential -> denied (401)
code, body = query(base_body, api_key="not-a-real-key-at-all")
results["D_unknown_credential"] = {"status": code, "body": body}

# E. missing credential -> with api_keys now populated, this must be denied (401)
code, body = query(base_body, api_key=None)
results["E_missing_credential"] = {"status": code, "body": body}

# valid ERM credential, generic query -> should authenticate successfully (not necessarily find matches)
code, body = query({"requester_app": "erm_intelligence", "request_type": "user_question",
                     "question": "地政学リスクの概況", "themes": [], "country_region": []},
                    api_key=creds["erm_agent"])
results["erm_valid_credential_authenticates"] = {
    "status": code, "knowledge_sufficiency": body.get("knowledge_sufficiency"), "error": body.get("error")}

out_path = Path(__file__).parent / "phase_ia1_sec3_results.json"
out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"wrote {out_path}")
for k, v in results.items():
    print(k, "->", v.get("status"))
