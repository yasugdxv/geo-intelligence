# -*- coding: utf-8 -*-
"""Phase I-A Mechanical E2E Pilot driver.

Runs the full IOS pipeline against the live api_server.py (http://127.0.0.1:8001)
using real HTTP calls (real LLM calls where the pipeline calls Expert AI).

IMPORTANT: Claude performs Reviewer / Approver / Knowledge Steward actions in this
script as an OPERATIONAL PROXY ONLY. These are NOT real domain-expert judgments.
All quality-related fields recorded from these actions are tagged
expert_verified=False throughout the evaluation record and MUST NOT be used to
justify CQAS tuning or any Phase J decision (per user instruction 2026-09-16).

Usage: python pilot_run.py <case_key>
  case_key: "A" | "B" | "C"
Writes: evaluation_record_<case_key>.json (in this same scratchpad directory)
"""
import json
import sys
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8001"
OUT_DIR = Path(__file__).parent
ACTOR = "claude_pilot_operational_proxy"  # NOT a real domain expert; mechanical proxy only.
ACTOR_ROLE = "knowledge_steward"  # role used purely to satisfy existing permission checks in this pilot.

CASES = {
    "A": {
        "title": "Pilot-A US-China Semiconductor Export Controls",
        "theme": "米国の対中半導体・AI輸出規制の今後を分析したい",
        "reviewer": "pilot_reviewer_A",
        "human_visibility": "company",
        "agent_sharing_policy": "geopolitics_only",
        "refine_message": "議会（上院・下院）の反応をもっと細かく見たい",
        "review_comments": [
            {"comment": "政権と議会のインセンティブの違いをもっと明確に区別してください",
             "anchor_type": "section", "severity": "major"},
            {"comment": "誤字を修正してください（軽微）", "anchor_type": "general", "severity": "minor"},
        ],
    },
    "B": {
        "title": "Pilot-B EUDR Supply Chain Regulation Geopolitical Implications",
        "theme": "EUDR（EU森林破壊フリー規則）のサプライチェーン・地政学的影響を分析したい",
        "reviewer": "pilot_reviewer_B",
        "human_visibility": "department",
        "agent_sharing_policy": "allowed_cross_domain",
        "refine_message": "サステナビリティ部門が参照しやすいよう、サプライチェーン上のリスクをもっと具体的に",
        "review_comments": [
            {"comment": "サステナビリティ部門が読む前提で、規制遵守コストの論点を追加してください",
             "anchor_type": "section", "severity": "major"},
        ],
    },
    "C": {
        "title": "Pilot-C Iran Hormuz Strait Sanctions Fast-Moving Event",
        "theme": "イラン・ホルムズ海峡情勢と制裁の急速な変化を分析したい",
        "reviewer": "pilot_reviewer_C",
        "human_visibility": "private",
        "agent_sharing_policy": "none",
        "refine_message": "直近の事象に基づき、想定より緊張が高まった場合のシナリオも追加して",
        "review_comments": [
            {"comment": "前提となっている事象の日付・根拠を明記してください（Freshness）",
             "anchor_type": "evidence", "severity": "critical"},
        ],
    },
}


def post(path, body, timeout=180):
    r = requests.post(f"{BASE}{path}", json=body, timeout=timeout)
    ok = r.ok
    try:
        data = r.json()
    except Exception:
        data = {"raw_text": r.text[:500]}
    return ok, r.status_code, data


def get(path, timeout=60):
    r = requests.get(f"{BASE}{path}", timeout=timeout)
    try:
        data = r.json()
    except Exception:
        data = {"raw_text": r.text[:500]}
    return r.ok, r.status_code, data


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run_case(case_key, existing_session_id=None):
    case = CASES[case_key]
    record = {
        "pilot_case_id": f"pilot-{case_key.lower()}", "theme": case["theme"],
        "expert_review_is_real": False,
        "notes": "Reviewer/Approver/Steward actions performed by Claude as an operational "
                 "proxy only. Content-quality fields are expert_unverified.",
        "steps": [], "timings_sec": {}, "findings": [],
    }

    def finding(severity, category, text):
        record["findings"].append({"severity": severity, "category": category, "text": text})
        log(f"FINDING[{severity}/{category}] {text}")

    t_start = time.time()

    # 1. Analysis Session
    if existing_session_id:
        ok, code, session = get(f"/api/geo/ios/analysis-sessions/{existing_session_id}")
        if not ok:
            finding("P0", "functional_defect", f"could not reuse existing session: {code} {session}")
            return record
        session_id = existing_session_id
        log(f"reusing existing session {session_id}")
    else:
        ok, code, session = post("/api/geo/ios/analysis-sessions",
                                  {"title": case["title"], "user_id": ACTOR, "theme": case["theme"]})
        if not ok:
            finding("P0", "functional_defect", f"create_session failed: {code} {session}")
            return record
        session_id = session["analysis_session_id"]
    record["analysis_session_id"] = session_id
    record["steps"].append("analysis_session_created")

    # 2. Generate Scenarios (real LLM, unless already generated on a reused session)
    ok, code, candidates_resp = get(f"/api/geo/ios/analysis-sessions/{session_id}/scenario-candidates")
    existing_candidates = candidates_resp.get("candidates", []) if ok else []
    if existing_candidates:
        log(f"scenarios already exist ({len(existing_candidates)}), skipping generate-scenarios")
        candidates = existing_candidates
    else:
        t0 = time.time()
        ok, code, gen = post(f"/api/geo/ios/analysis-sessions/{session_id}/generate-scenarios",
                              {"actor_id": ACTOR, "display_top_n": 5, "profile": "quick"}, timeout=240)
        record["timings_sec"]["generate_scenarios"] = round(time.time() - t0, 1)
        if not ok:
            finding("P0", "functional_defect", f"generate_scenarios failed: {code} {gen}")
            return record
        candidates = gen["candidates"]
    record["cqas_total_candidates"] = len(candidates)
    record["cqas_displayed_candidates"] = sum(1 for c in candidates if c.get("displayed"))
    record["steps"].append("scenarios_generated")

    # 3. Select scenario (Claude's operational pick: the main/rank-1 candidate; NOT expert judgment)
    displayed = [c for c in candidates if c.get("displayed")]
    chosen = displayed[0] if displayed else candidates[0]
    ok, code, sel = post(
        f"/api/geo/ios/analysis-sessions/scenario-candidates/{chosen['scenario_candidate_id']}/select",
        {"actor_id": ACTOR})
    if not ok:
        finding("P1", "functional_defect", f"select_scenario failed: {code} {sel}")
    record["selected_scenario_candidate_id"] = chosen["scenario_candidate_id"]
    record["selected_scenario_rank"] = chosen["rank"]
    record["steps"].append("scenario_selected")

    # 4. Generate Design (reuses stored CQAS response, no new LLM call)
    ok, code, design = get(f"/api/geo/ios/analysis-sessions/{session_id}/design-versions/current")
    if not ok:
        ok, code, design = post(f"/api/geo/ios/analysis-sessions/{session_id}/generate-design",
                                 {"created_by": ACTOR}, timeout=60)
        if not ok:
            finding("P0", "functional_defect", f"generate_design failed: {code} {design}")
            return record
    record["design_v1_fields_present"] = {
        k: bool(design.get(k)) for k in
        ("investigation_question", "key_actors", "hypotheses", "watch_points", "knowledge_gaps")}
    record["steps"].append("design_generated")

    # 5. Refine Design via chat (real LLM call)
    t0 = time.time()
    ok, code, refined = post(f"/api/geo/ios/analysis-sessions/{session_id}/refine-design",
                              {"user_message": case["refine_message"], "created_by": ACTOR}, timeout=240)
    record["timings_sec"]["refine_design"] = round(time.time() - t0, 1)
    if not ok:
        finding("P1", "functional_defect", f"refine_design failed: {code} {refined}")
    else:
        record["design_v2_version"] = refined["design_version"]["version"]
        record["design_v2_changed_fields"] = refined["change_summary"]["changed_fields"]
        record["steps"].append("design_refined")

    # 6. Create Report (handoff)
    ok, code, session_now = get(f"/api/geo/ios/analysis-sessions/{session_id}")
    if ok and session_now.get("linked_report_project_id"):
        project_id = session_now["linked_report_project_id"]
        log(f"report already linked: {project_id}")
    else:
        ok, code, handoff = post(f"/api/geo/ios/analysis-sessions/{session_id}/create-report",
                                  {"actor_id": ACTOR}, timeout=60)
        if not ok:
            finding("P0", "functional_defect", f"create_report failed: {code} {handoff}")
            return record
        project_id = handoff["report_project"]["project_id"]
    record["report_project_id"] = project_id
    record["steps"].append("report_created")

    # ---- Provenance check: session -> report project reverse lookup (existing IOS feature) ----
    ok, code, traced = get(f"/api/geo/ios/analysis-sessions/by-report-project/{project_id}")
    if not ok or traced.get("analysis_session_id") != session_id:
        finding("P0", "data_integrity",
                f"provenance reverse lookup (report_project -> analysis_session) failed or mismatched: {code} {traced}")
    else:
        record["provenance_reverse_lookup_ok"] = True

    # 7. Build a manual review snapshot (Report Studio's own generation pipeline is
    # under active, unstable concurrent development and was NOT invoked -- see Phase I
    # report for rationale. The design content -- itself real LLM output -- stands in
    # for the report body to exercise the IOS review/approval/publication mechanics.)
    current_design_ok, _, current_design = get(f"/api/geo/ios/analysis-sessions/{session_id}/design-versions/current")
    body_html = "<h3>Investigation Question</h3><p>{}</p><h3>Key Actors</h3><ul>{}</ul><h3>Hypotheses</h3><ul>{}</ul>".format(
        (current_design or {}).get("investigation_question") or "",
        "".join(f"<li>{a}</li>" for a in (current_design or {}).get("key_actors") or []),
        "".join(f"<li>{h}</li>" for h in (current_design or {}).get("hypotheses") or []),
    )
    snapshot = {"run_id": "pilot-manual-snapshot", "syntheses": [{"final_html": body_html, "final_status": "PILOT"}]}

    # 8. Request Expert Review round 1
    ok, code, cycle1 = post("/api/geo/ios/review/cycles", {
        "report_project_id": project_id, "reviewer": case["reviewer"], "requested_by": ACTOR,
        "version_label": "pilot-v1", "version_snapshot": snapshot,
    })
    if not ok:
        finding("P0", "functional_defect", f"request_review_cycle failed: {code} {cycle1}")
        return record
    record["review_round_1_id"] = cycle1["review_cycle_id"]
    record["steps"].append("review_round_1_requested")

    # 9. Add comments (Claude as reviewer PROXY -- content is mechanical, not domain-expert)
    comment_ids = []
    for c in case["review_comments"]:
        ok, code, comment = post(f"/api/geo/ios/review/cycles/{cycle1['review_cycle_id']}/comments",
                                  {"comment": c["comment"], "created_by": case["reviewer"],
                                   "anchor_type": c["anchor_type"], "severity": c["severity"]})
        if ok:
            comment_ids.append(comment["review_comment_id"])
        else:
            finding("P1", "functional_defect", f"add_comment failed: {code} {comment}")
    record["review_round_1_comment_count"] = len(comment_ids)

    # 10. Author responds (resolve first as "resolved", rest as "partially_resolved")
    for i, cid in enumerate(comment_ids):
        status = "resolved" if i == 0 else "partially_resolved"
        ok, code, resp = post(f"/api/geo/ios/review/comments/{cid}/respond",
                               {"resolution_status": status,
                                "author_response": "Pilotにより対応（機械的操作、内容の妥当性は専門家未検証）",
                                "actor_id": ACTOR})
        if not ok:
            finding("P1", "functional_defect", f"respond_to_comment failed: {code} {resp}")

    # 11. Submit round 1 findings -> revision_requested (to exercise multi-round mechanics)
    ok, code, submitted = post(f"/api/geo/ios/review/cycles/{cycle1['review_cycle_id']}/submit", {
        "findings": {"major_findings": [c["comment"] for c in case["review_comments"] if c["severity"] in ("critical", "major")]},
        "result": "revision_requested", "reviewer": case["reviewer"],
    })
    if not ok:
        finding("P0", "functional_defect", f"submit_review_findings round1 failed: {code} {submitted}")
        return record
    record["steps"].append("review_round_1_completed_revision_requested")

    # 12. Round 2 (unlimited rounds mechanic) -> approved
    ok, code, cycle2 = post("/api/geo/ios/review/cycles", {
        "report_project_id": project_id, "reviewer": case["reviewer"], "requested_by": ACTOR,
        "version_label": "pilot-v2-revised", "version_snapshot": snapshot,
    })
    if not ok:
        finding("P0", "functional_defect", f"request_review_cycle round2 failed: {code} {cycle2}")
        return record
    record["review_round_2_id"] = cycle2["review_cycle_id"]
    if cycle2.get("round_number") != 2:
        finding("P0", "functional_defect", f"round_number did not increment as expected: {cycle2.get('round_number')}")

    ok, code, submitted2 = post(f"/api/geo/ios/review/cycles/{cycle2['review_cycle_id']}/submit", {
        "findings": {"critical_findings": []}, "result": "approved", "reviewer": case["reviewer"],
    })
    if not ok:
        finding("P0", "functional_defect", f"submit_review_findings round2 failed: {code} {submitted2}")
        return record
    record["steps"].append("review_round_2_completed_approved")

    # 13. Approval (in-app)
    # NOTE: pass an explicit version_snapshot -- Report Studio's own Collection Run
    # pipeline was deliberately not invoked in this pilot (see step 7 rationale), so
    # approve_in_app's auto-snapshot-from-Report-Studio fallback has nothing to build
    # from. This is a driver-script fix (found in Case A), not a product change.
    ok, code, approval = post("/api/geo/ios/approval/in-app", {
        "report_project_id": project_id, "approval_status": "approved", "approver": ACTOR,
        "version_label": "pilot-v2-revised", "version_snapshot": snapshot,
    })
    if not ok:
        finding("P0", "functional_defect", f"approve_in_app failed: {code} {approval}")
        return record
    record["approval_event_id"] = approval["approval_event_id"]
    record["steps"].append("approved")

    # 14. Publish
    ok, code, published = post("/api/geo/ios/knowledge-publication/report-studio", {
        "report_project_id": project_id, "approval_event_id": approval["approval_event_id"],
        "publisher": ACTOR, "publisher_role": ACTOR_ROLE,
        "human_visibility": case["human_visibility"], "agent_sharing_policy": case["agent_sharing_policy"],
    })
    if not ok:
        finding("P0", "functional_defect", f"publish failed: {code} {published}")
        return record
    record["published_knowledge_item_id"] = published["knowledge_item_id"]
    record["steps"].append("published")

    # ---- Access control spot-check: verify NOT active yet (double gate), then via
    # existing review_knowledge_item promote to active for later cross-agent checks ----
    ok, code, ki = get(f"/api/geo/knowledge/items/{published['knowledge_item_id']}?include_non_retrievable=true&actor_role=knowledge_steward")
    record["published_knowledge_item_status_after_publish"] = ki.get("status") if ok else f"ERROR {code}"

    # 15. Extract Learning
    ok, code, extracted = post(
        f"/api/geo/ios/knowledge-publication/report-studio/{project_id}/extract-learning",
        {"actor_id": ACTOR}, timeout=180)
    if not ok:
        finding("P1", "functional_defect", f"extract_learning failed: {code} {extracted}")
    else:
        record["learning_candidates_created"] = len(extracted.get("candidates", []))
        record["learning_extraction_reason"] = extracted.get("reason")
        record["steps"].append("learning_extraction_run")

    record["timings_sec"]["total"] = round(time.time() - t_start, 1)
    return record


if __name__ == "__main__":
    case_key = sys.argv[1]
    reuse_session = sys.argv[2] if len(sys.argv) > 2 else None
    result = run_case(case_key, existing_session_id=reuse_session)
    out_path = OUT_DIR / f"evaluation_record_{case_key}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"wrote {out_path}")
    log(f"steps completed: {result['steps']}")
    log(f"findings: {len(result['findings'])}")
