# -*- coding: utf-8 -*-
"""Phase I-B0: Retrospective Proxy Evaluation -- Hungary case, T0 generation step.

Generates CQAS scenario candidates + Analysis Design from ONLY the objective,
public Event/Situation facts (election date, vote shares, who won/lost) --
deliberately NOT using Eguchi's own framing language (Trump/MAGA linkage, EU-Russia
pivot, etc.) as the input theme, so the system's own candidate generation is not
nudged toward his specific answer. Ground-truth comparison happens in a SEPARATE
step, only after this generation completes.
"""
import json
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8001"
ACTOR = "phase_ib0_retrospective_proxy"

THEME = (
    "2026年4月12日、ハンガリー議会選挙が実施された。野党ティサ党(中道右派)が得票率54%・"
    "138議席(定数の3分の2超)を獲得する歴史的勝利を収め、与党フィデス党(オルバン首相、"
    "16年間政権を維持)は得票率38%・55議席で大敗し、オルバン首相は即日敗北を宣言した。"
    "この政権交代の地政学的含意を分析してください。"
)


def post(path, body, timeout=240):
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


record = {"case_id": "ib0-hungary", "theme": THEME, "t0_date": "2026-04-12",
          "expert_review_is_real": True, "evaluation_mode": "retrospective_proxy_directional",
          "notes": "Phase I-B0: NOT an 'AI output reviewed by expert' case. Eguchi's real "
                   "material was NOT consulted before this generation step."}

# 1. Create Analysis Session
ok, code, session = post("/api/geo/ios/analysis-sessions",
                          {"title": "I-B0 Retrospective: Hungary 2026 Election", "user_id": ACTOR, "theme": THEME})
if not ok:
    log(f"FATAL session_create failed: {code} {session}")
    raise SystemExit(1)
session_id = session["analysis_session_id"]
record["analysis_session_id"] = session_id
log(f"session created: {session_id}")

# 2. Generate scenarios (CQAS, real LLM call) -- request a larger display_top_n so we can
#    see ALL generated candidates, not just the Top 5, per the I-B0 evaluation requirement.
t0 = time.time()
ok, code, gen = post(f"/api/geo/ios/analysis-sessions/{session_id}/generate-scenarios",
                      {"actor_id": ACTOR, "display_top_n": 5, "profile": "quick"}, timeout=240)
record["timings_sec"] = {"generate_scenarios": round(time.time() - t0, 1)}
if not ok:
    log(f"FATAL generate_scenarios failed: {code} {gen}")
    Path(__file__).with_name("phase_ib0_hungary_result.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    raise SystemExit(1)
candidates = gen["candidates"]
record["cqas_all_candidates"] = candidates
record["cqas_total_candidates"] = len(candidates)
record["cqas_displayed_top5"] = [c for c in candidates if c.get("displayed")]
log(f"scenarios generated: {len(candidates)} total, {len(record['cqas_displayed_top5'])} displayed (Top N)")

# 3. Select the rank-1 displayed candidate (operational proxy pick, not an expert judgment)
displayed = record["cqas_displayed_top5"]
chosen = displayed[0] if displayed else candidates[0]
ok, code, sel = post(f"/api/geo/ios/analysis-sessions/scenario-candidates/{chosen['scenario_candidate_id']}/select",
                      {"actor_id": ACTOR})
record["selected_scenario_candidate_id"] = chosen["scenario_candidate_id"]
record["selected_scenario_rank"] = chosen.get("rank")
log(f"selected candidate rank={chosen.get('rank')}: {ok}")

# 4. Generate Analysis Design (reuses stored CQAS response, no new LLM call)
ok, code, design = post(f"/api/geo/ios/analysis-sessions/{session_id}/generate-design",
                         {"created_by": ACTOR}, timeout=60)
if not ok:
    log(f"FATAL generate_design failed: {code} {design}")
else:
    record["analysis_design"] = design
    log("design generated")

Path(__file__).with_name("phase_ib0_hungary_result.json").write_text(
    json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
log("wrote phase_ib0_hungary_result.json")
