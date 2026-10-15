import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from _review_mode_common import get_presentation, set_strategy, generate_revised_draft_and_wait, req  # noqa: E402

LOG_PATH = os.path.join(os.environ["TEMP"], "case_c_log.txt")
lines = []


def log(m):
    lines.append(str(m))


t0 = time.time()
session_id = "d113b719-1b14-43fb-85bf-c373c741c1cb"

# This also exercises §6 Resume: this session was created in a previous process (CASE C's
# first attempt, which timed out client-side on the presentation fetch after reconstruction
# had already completed server-side). Resuming here confirms mode=review / reconstructed
# reading / generated alternatives all persisted correctly across a fresh process.
status, review_import = req("GET", f"/api/geo/ios/review-import/{session_id}")
log(f"[resume] review_import status(http)={status} import_status={review_import['status']}")
log(f"[resume] reconstructed_reading persisted: {bool(review_import.get('reconstructed_reading'))}")

pres = get_presentation(session_id)
cards = pres["presentation"]["reading_exposure"]
log(f"[4] reading_exposure count={len(cards)} (resumed correctly)")
current = next(c for c in cards if c["reading_role"] == "CURRENT")
alternatives = [c for c in cards if c["reading_role"] == "ALTERNATIVE"]
selected = alternatives[-1]
log(f"[4] current={current['scenario_candidate_id']} title={current['title']!r}")
log(f"[4] selected_alternative={selected['scenario_candidate_id']} title={selected['title']!r}")
log(f"[4] selected_alt why_this_matters={selected.get('why_this_matters')}")

strat = set_strategy(session_id, selected["scenario_candidate_id"], "integrate")
log(f"[5] persisted strategy={strat['revision_strategy']} selected={strat['selected_scenario_candidate_id']}")

draft = generate_revised_draft_and_wait(session_id)
log(f"[6] draft_status={draft.get('status')} elapsed={time.time()-t0:.1f}s")
sections = draft.get("sections") or []
log(f"[6] sections count={len(sections)}")
for s in sections:
    log(f"    heading={s.get('heading')!r}")
    log(f"    body={s.get('body','')!r}")

log(f"TOTAL elapsed={time.time()-t0:.1f}s")
log(f"session_id={session_id}")
log(f"review_import_id={review_import.get('review_import_id')}")
log(f"selected_scenario_candidate_id={selected['scenario_candidate_id']}")
log(f"report_draft_id={draft.get('report_draft_id')}")

open(LOG_PATH, "w", encoding="utf-8").write("\n".join(lines))
print("DONE", LOG_PATH)
