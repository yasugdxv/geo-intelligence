import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from _review_mode_common import req  # noqa: E402

from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

_config = load_config()
_client = SupabaseClient(_config)

CASES = [
    ("Russia Sanctions", "russia", "QA MODE-PROFILE: Russia Sanctions"),
    ("US-China AI/Semicon", "semicon", "QA MODE-PROFILE: US-China AI Semicon"),
    ("Hormuz Toll", "hormuz_toll", "QA MODE-PROFILE: Hormuz Toll"),
    ("Hormuz Blockade", "hormuz_blockade", "QA MODE-PROFILE: Hormuz Blockade"),
]

LOG_PATH = os.path.join(os.environ["TEMP"], "mode_profile_e2e_log.txt")
lines = []


def log(m):
    print(m)
    lines.append(str(m))


for case_name, key, title in CASES:
    t0 = time.time()
    log(f"\n===== {case_name} =====")
    theme_text = open(os.path.join(os.environ["TEMP"], f"e2e_{key}_theme.txt"), encoding="utf-8").read()

    # 1. Create a CREATE-mode session (real production path, mode omitted -> defaults to "create")
    status, session = req("POST", "/api/geo/ios/analysis-sessions",
                           json_body={"title": title, "user_id": "qa_mode_profile", "theme": theme_text})
    assert status == 200, (status, session)
    sid = session["analysis_session_id"]
    log(f"session_id={sid}")

    # 2. Call generate-scenarios EXACTLY as the real frontend does (onHandoff): only actor_id.
    #    No mode/profile in the request body -- this is what useGenerateScenarios actually sends.
    status, gen_result = req("POST", f"/api/geo/ios/analysis-sessions/{sid}/generate-scenarios",
                              json_body={"actor_id": "qa_mode_profile"}, timeout=420)
    elapsed_gen = time.time() - t0
    log(f"generate-scenarios status={status} elapsed={elapsed_gen:.1f}s")
    if status != 200:
        log(f"FAILED: {gen_result}")
        continue

    # 3. Inspect the actual persisted candidates + source_cqas metadata (mode/profile actually used).
    candidates = _client.select("geo_ios_scenario_candidates", {
        "select": "scenario_candidate_id,rank,title,content", "analysis_session_id": f"eq.{sid}",
        "order": "rank.asc"})
    log(f"scenario_candidates count={len(candidates)}")
    source_cqas = None
    for c in candidates:
        content = c.get("content") or {}
        origin = content.get("origin")
        sc = content.get("source_cqas") or {}
        if sc and not source_cqas:
            source_cqas = sc
        log(f"  rank={c['rank']} origin={origin} title={c['title'][:40]!r}")
    log(f"source_cqas metadata={source_cqas}")

    # 4. Check hypothesis_register on the actual response_json.
    response_id = (source_cqas or {}).get("response_id")
    hr_count = None
    if response_id:
        resp_rows = _client.select("geo_expert_responses", {
            "select": "response_json,mode", "response_id": f"eq.{response_id}"})
        if resp_rows:
            rj = resp_rows[0]["response_json"]
            hr_count = len(rj.get("hypothesis_register") or [])
            log(f"response mode column={resp_rows[0]['mode']} hypothesis_register count={hr_count}")

    # 5. Fetch the situation-presentation exactly as the Step2 UI would.
    status, pres = req("GET", f"/api/geo/ios/situation-presentation/{sid}", timeout=300)
    assert status == 200, (status, pres)
    cards = pres["presentation"]["reading_exposure"]
    log(f"reading_exposure (Step2 UI) count={len(cards)}")
    for c in cards:
        log(f"  {c['reading_role']} {c['scenario_candidate_id']} {c['title'][:40]!r}")

    log(f"TOTAL elapsed={time.time()-t0:.1f}s")

open(LOG_PATH, "w", encoding="utf-8").write("\n".join(lines))
print("\nDONE, log at", LOG_PATH)
