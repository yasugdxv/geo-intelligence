import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from _review_mode_common import (  # noqa: E402
    create_review_session, import_report, generate_readings_and_wait, get_presentation,
    set_strategy, generate_revised_draft_and_wait, generate_more_readings, load_report_text,
)

LOG_PATH = os.path.join(os.environ["TEMP"], "case_d_log.txt")
lines = []


def log(m):
    lines.append(str(m))


t0 = time.time()
report_text = load_report_text("case_a_report.txt")

session_id = create_review_session("QA CASE D: Hormuz Toll Generate More")
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

pres_before = get_presentation(session_id)
cards_before = pres_before["presentation"]["reading_exposure"]
ids_before = [c["scenario_candidate_id"] for c in cards_before]
titles_before = {c["scenario_candidate_id"]: c["title"] for c in cards_before}
log(f"[4] before count={len(cards_before)}")
for c in cards_before:
    log(f"    - {c['reading_role']} {c['scenario_candidate_id']} {c['title']!r}")

gen_result = generate_more_readings(session_id, direction="different_actor_lead")
log(f"[5] generate_more_readings result={gen_result}")

pres_after = get_presentation(session_id)
cards_after = pres_after["presentation"]["reading_exposure"]
log(f"[6] after count={len(cards_after)}")
for c in cards_after:
    log(f"    - {c['reading_role']} {c['scenario_candidate_id']} {c['title']!r}")

if gen_result.get("created"):
    new_cards = [c for c in cards_after if c["scenario_candidate_id"] not in ids_before]
    assert len(new_cards) == 1, f"expected exactly 1 new card, got {len(new_cards)}"
    new_card = new_cards[0]
    log(f"[6] new reading F: id={new_card['scenario_candidate_id']} title={new_card['title']!r} role={new_card['reading_role']}")
    # existing A-E unchanged check
    unchanged = all(titles_before[cid] == next(c["title"] for c in cards_after if c["scenario_candidate_id"] == cid) for cid in ids_before)
    log(f"[6] existing A-E titles unchanged: {unchanged}")

    strat = set_strategy(session_id, new_card["scenario_candidate_id"], "replace")
    log(f"[7] persisted strategy={strat['revision_strategy']} selected={strat['selected_scenario_candidate_id']}")

    draft = generate_revised_draft_and_wait(session_id)
    log(f"[8] draft_status={draft.get('status')} elapsed={time.time()-t0:.1f}s")
    sections = draft.get("sections") or []
    for s in sections:
        log(f"    heading={s.get('heading')!r}")
        log(f"    body={s.get('body','')[:400]!r}")
    log(f"report_draft_id={draft.get('report_draft_id')}")
else:
    log(f"[6] Generate More did not create a new reading, reason={gen_result.get('reason')} (insufficient_material is an acceptable honest outcome, not a bug)")

log(f"TOTAL elapsed={time.time()-t0:.1f}s")
log(f"session_id={session_id}")
log(f"review_import_id={review_import_id}")

open(LOG_PATH, "w", encoding="utf-8").write("\n".join(lines))
print("DONE", LOG_PATH)
