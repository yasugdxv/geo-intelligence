import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from _review_mode_common import (  # noqa: E402
    create_review_session, import_report, generate_readings_and_wait, get_presentation,
    set_strategy, generate_revised_draft_and_wait, load_report_text,
)

LOG_PATH = os.path.join(os.environ["TEMP"], "case_c_log.txt")
lines = []


def log(m):
    lines.append(str(m))


t0 = time.time()
report_text = load_report_text("case_a_report.txt")

session_id = create_review_session("QA CASE C: Hormuz Toll Integrate")
log(f"[1] session_id={session_id}")

review_import = import_report(session_id, report_text)
review_import_id = review_import["review_import_id"]
log(f"[2] review_import_id={review_import_id}")

review_import = generate_readings_and_wait(session_id)
log(f"[3] status={review_import['status']} elapsed={time.time()-t0:.1f}s")
if review_import["status"] == "failed":
    log(f"FAILED: {review_import.get('error_message')}")
    open(LOG_PATH, "w", encoding="utf-8").write("\n".join(lines))
    raise SystemExit(1)

pres = get_presentation(session_id)
cards = pres["presentation"]["reading_exposure"]
current = next(c for c in cards if c["reading_role"] == "CURRENT")
alternatives = [c for c in cards if c["reading_role"] == "ALTERNATIVE"]
selected = alternatives[-1]  # pick a different alternative than CASE A used
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
log(f"review_import_id={review_import_id}")
log(f"selected_scenario_candidate_id={selected['scenario_candidate_id']}")
log(f"report_draft_id={draft.get('report_draft_id')}")

open(LOG_PATH, "w", encoding="utf-8").write("\n".join(lines))
print("DONE", LOG_PATH)
