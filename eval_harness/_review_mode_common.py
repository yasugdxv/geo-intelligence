import json
import os
import time
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8001"


def req(method, path, json_body=None, form_data=None, timeout=420):
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
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        return e.code, (json.loads(body) if body else {})


def create_review_session(title, actor_id="qa_review_mode"):
    status, session = req("POST", "/api/geo/ios/analysis-sessions", json_body={
        "title": title, "user_id": actor_id, "mode": "review"})
    assert status == 200, (status, session)
    return session["analysis_session_id"]


def import_report(session_id, report_text, actor_id="qa_review_mode"):
    status, review_import = req("POST", f"/api/geo/ios/review-import/{session_id}/import", form_data={
        "created_by": actor_id, "pasted_text": report_text})
    assert status == 200, (status, review_import)
    return review_import


def generate_readings_and_wait(session_id, actor_id="qa_review_mode", deadline_s=600):
    status, resp = req("POST", f"/api/geo/ios/review-import/{session_id}/generate-review-readings",
                        json_body={"actor_id": actor_id})
    assert status == 200, (status, resp)
    deadline = time.time() + deadline_s
    review_import = None
    while time.time() < deadline:
        status, review_import = req("GET", f"/api/geo/ios/review-import/{session_id}")
        if review_import["status"] in ("readings_ready", "failed"):
            return review_import
        time.sleep(5)
    raise TimeoutError(f"generate_readings timed out, last status={review_import}")


def get_presentation(session_id):
    status, pres = req("GET", f"/api/geo/ios/situation-presentation/{session_id}")
    assert status == 200, (status, pres)
    return pres


def set_strategy(session_id, selected_scenario_candidate_id, strategy, actor_id="qa_review_mode"):
    status, result = req("POST", f"/api/geo/ios/review-import/{session_id}/revision-strategy", json_body={
        "selected_scenario_candidate_id": selected_scenario_candidate_id, "strategy": strategy, "actor_id": actor_id})
    assert status == 200, (status, result)
    return result


def generate_revised_draft_and_wait(session_id, actor_id="qa_review_mode", deadline_s=300):
    status, draft = req("POST", f"/api/geo/ios/review-import/{session_id}/generate-revised-draft",
                         json_body={"created_by": actor_id})
    assert status == 200, (status, draft)
    deadline = time.time() + deadline_s
    current_draft = None
    while time.time() < deadline:
        status, current_draft = req("GET", f"/api/geo/ios/report-drafts/current?analysis_session_id={session_id}")
        if current_draft.get("status") in ("completed", "failed"):
            return current_draft
        time.sleep(4)
    raise TimeoutError(f"generate_revised_draft timed out, last={current_draft}")


def generate_more_readings(session_id, direction, actor_id="qa_review_mode", custom_text=None):
    body = {"direction": direction, "actor_id": actor_id}
    if custom_text:
        body["custom_text"] = custom_text
    status, result = req("POST", f"/api/geo/ios/situation-presentation/{session_id}/generate-more-readings",
                          json_body=body, timeout=180)
    assert status == 200, (status, result)
    return result


def load_report_text(path_key="case_a_report.txt"):
    return open(os.path.join(os.environ["TEMP"], path_key), encoding="utf-8").read()
