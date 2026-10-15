import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from _review_mode_common import req  # noqa: E402

LOG_PATH = os.path.join(os.environ["TEMP"], "create_regression_log.txt")
lines = []


def log(m):
    lines.append(str(m))


t0 = time.time()
SESSION_ID = "9301e1fe-1e05-4292-92b5-4e3ad09d991e"  # HORMUZ_TOLL_SESSION1 blind retrospective, mode=create

status, session = req("GET", f"/api/geo/ios/analysis-sessions/{SESSION_ID}")
log(f"[1] session mode={session.get('mode')} status(http)={status} session_status={session.get('status')}")
assert status == 200
assert session.get("mode") == "create", "existing session should default to mode=create"

status, pres = req("GET", f"/api/geo/ios/situation-presentation/{SESSION_ID}")
log(f"[2] presentation http_status={status}")
assert status == 200
cards = pres["presentation"]["reading_exposure"]
log(f"[2] reading_exposure count={len(cards)} (should still be the previously-verified 6 incl. the F reading from earlier CREATE MODE test)")
for c in cards:
    log(f"    - {c['reading_role']} {c['scenario_candidate_id']} {c['title'][:40]!r}")
assert pres["raw_analysis"]["expert_response_id"], "CREATE MODE must still populate expert_response_id (was None only for review mode)"

# select a reading (idempotent-ish: re-select the currently selected one, or pick main)
main_card = next((c for c in cards if c["reading_role"] == "MAIN"), cards[0])
status, sel_result = req("POST", f"/api/geo/ios/analysis-sessions/scenario-candidates/{main_card['scenario_candidate_id']}/select",
                          json_body={"actor_id": "qa_regression", "confirm_after_handoff": False})
log(f"[3] select status={status} selected={sel_result.get('selected')}")
assert status == 200

status, pres2 = req("GET", f"/api/geo/ios/situation-presentation/{SESSION_ID}")
effective_selected = pres2["raw_analysis"]["selected_scenario_candidate_id"]
log(f"[4] effective_selected_scenario_candidate_id={effective_selected} matches={effective_selected == main_card['scenario_candidate_id']}")
assert effective_selected == main_card["scenario_candidate_id"]

status, design = req("GET", f"/api/geo/ios/analysis-sessions/{SESSION_ID}/design-versions/current")
log(f"[5] existing design lookup http_status={status} (200=already has design, 404=needs generation, both acceptable)")

log(f"TOTAL elapsed={time.time()-t0:.1f}s")
open(LOG_PATH, "w", encoding="utf-8").write("\n".join(lines))
print("DONE", LOG_PATH)
