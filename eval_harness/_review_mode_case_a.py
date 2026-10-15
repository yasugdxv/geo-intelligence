import json
import os
import time
import urllib.request

BASE = "http://127.0.0.1:8001"
LOG_PATH = os.path.join(os.environ["TEMP"], "case_a_log.txt")
_log_lines = []


def log(msg):
    _log_lines.append(str(msg))


def req(method, path, json_body=None, form_data=None):
    url = f"{BASE}{path}"
    if form_data is not None:
        boundary = "----geoiosqa"
        body_parts = []
        for key, value in form_data.items():
            body_parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n")
        body_parts.append(f"--{boundary}--\r\n")
        data = "".join(body_parts).encode("utf-8")
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    elif json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
    else:
        data = None
        headers = {}
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=180) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        return e.code, json.loads(body) if body else {}


t0 = time.time()

# 1. Create REVIEW session
status, session = req("POST", "/api/geo/ios/analysis-sessions", json_body={
    "title": "QA CASE A: Hormuz Toll Review", "user_id": "qa_review_mode", "mode": "review"})
assert status == 200, (status, session)
session_id = session["analysis_session_id"]
log(f"[1] session created: {session_id} mode={session.get('mode')} status={status}")

# 2. Import report (pasted text)
report_text = open(os.path.join(os.environ["TEMP"], "case_a_report.txt"), encoding="utf-8").read()
status, review_import = req("POST", f"/api/geo/ios/review-import/{session_id}/import", form_data={
    "created_by": "qa_review_mode", "pasted_text": report_text})
log(f"[2] import status={status} review_import_id={review_import.get('review_import_id')} import_status={review_import.get('status')}")
assert status == 200, (status, review_import)
review_import_id = review_import["review_import_id"]

# 3. Trigger reconstruction + alternatives
status, resp = req("POST", f"/api/geo/ios/review-import/{session_id}/generate-review-readings", json_body={"actor_id": "qa_review_mode"})
log(f"[3] generate-review-readings status={status} resp={resp}")
assert status == 200, (status, resp)

# 4. Poll until readings_ready or failed
deadline = time.time() + 600
poll_count = 0
while time.time() < deadline:
    status, review_import = req("GET", f"/api/geo/ios/review-import/{session_id}")
    poll_count += 1
    if review_import["status"] in ("readings_ready", "failed"):
        break
    time.sleep(5)
log(f"[4] poll_count={poll_count} final_status={review_import['status']} elapsed={time.time()-t0:.1f}s")
if review_import["status"] == "failed":
    log(f"[4] FAILURE: {review_import.get('error_message')}")
    open(LOG_PATH, "w", encoding="utf-8").write("\n".join(_log_lines))
    raise SystemExit(1)

log(f"[4] extracted_context={json.dumps(review_import['extracted_context'], ensure_ascii=False)}")
log(f"[4] reconstructed_reading={json.dumps(review_import['reconstructed_reading'], ensure_ascii=False)}")
log(f"[4] current_reading_scenario_candidate_id={review_import['current_reading_scenario_candidate_id']}")

# 5. Fetch situation presentation (Reading A-E)
status, pres = req("GET", f"/api/geo/ios/situation-presentation/{session_id}")
assert status == 200, (status, pres)
cards = pres["presentation"]["reading_exposure"]
log(f"[5] reading_exposure count={len(cards)}")
for c in cards:
    rp = c.get("reading_presentation")
    log(f"  - role={c['reading_role']} id={c['scenario_candidate_id']} title={c['title'][:50]!r} tag={(rp or {}).get('semantic_tag')}")

alt_cards = [c for c in cards if c["reading_role"] == "ALTERNATIVE"]
assert alt_cards, "no ALTERNATIVE readings generated"
selected = alt_cards[0]
log(f"[5] selecting ALTERNATIVE for replace: {selected['scenario_candidate_id']} title={selected['title']!r}")

# 6. Revision strategy = replace
status, strat_result = req("POST", f"/api/geo/ios/review-import/{session_id}/revision-strategy", json_body={
    "selected_scenario_candidate_id": selected["scenario_candidate_id"], "strategy": "replace", "actor_id": "qa_review_mode"})
log(f"[6] revision-strategy status={status} persisted_strategy={strat_result.get('revision_strategy')} persisted_selected={strat_result.get('selected_scenario_candidate_id')}")
assert status == 200, (status, strat_result)

# 7. Generate revised draft
status, draft = req("POST", f"/api/geo/ios/review-import/{session_id}/generate-revised-draft", json_body={"created_by": "qa_review_mode"})
log(f"[7] generate-revised-draft status={status} report_draft_id={draft.get('report_draft_id')} initial_status={draft.get('status')}")
assert status == 200, (status, draft)

# 8. Poll draft until completed/failed
deadline = time.time() + 300
draft_poll = 0
while time.time() < deadline:
    status, current_draft = req("GET", f"/api/geo/ios/report-drafts/current?analysis_session_id={session_id}")
    draft_poll += 1
    if current_draft.get("status") in ("completed", "failed"):
        break
    time.sleep(4)
log(f"[8] draft_poll={draft_poll} final_draft_status={current_draft.get('status')} elapsed={time.time()-t0:.1f}s")
if current_draft.get("status") == "failed":
    log(f"[8] DRAFT FAILURE")
else:
    sections = current_draft.get("sections") or []
    log(f"[8] draft sections count={len(sections)}")
    for s in sections[:3]:
        log(f"    heading={s.get('heading')!r} body_preview={s.get('body','')[:150]!r}")

# 9. Verify original report unchanged
status, final_review_import = req("GET", f"/api/geo/ios/review-import/{session_id}")
original_unchanged = final_review_import["original_text"] == report_text
log(f"[9] original_text unchanged: {original_unchanged}")
log(f"[9] final review_import: selected={final_review_import['selected_scenario_candidate_id']} strategy={final_review_import['revision_strategy']}")

log(f"TOTAL elapsed: {time.time()-t0:.1f}s")
log(f"session_id={session_id}")
log(f"review_import_id={review_import_id}")
log(f"selected_scenario_candidate_id={selected['scenario_candidate_id']}")
log(f"report_draft_id={draft.get('report_draft_id')}")

open(LOG_PATH, "w", encoding="utf-8").write("\n".join(_log_lines))
print("DONE, see", LOG_PATH)
