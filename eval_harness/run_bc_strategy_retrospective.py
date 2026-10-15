# -*- coding: utf-8 -*-
"""Phase I-B0.1 Section 10: BC Strategy Retrospective Run.

Calls the REAL, unmodified production functions (geo_ios_analysis_session_service,
geo_ios_expert_ai_adapter -> geo_expert_engine.run_expert_request_with_mode) directly,
substituting an AsOfEvaluationClient(as_of_date="2026-02-19") for the normal DB client.
No production file is edited. Only run after leakage_audit_bc_strategy.py reports PASS.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config_utils  # noqa: E402
import geo_db_client  # noqa: E402
from ai_client import make_openai_client  # noqa: E402
from eval_harness.as_of_evaluation_client import AsOfEvaluationClient  # noqa: E402

import geo_ios_analysis_session_service as session_service  # noqa: E402
import geo_ios_expert_ai_adapter as expert_ai_adapter  # noqa: E402

ACTOR = "phase_ib0_1_retrospective_proxy"
T0 = "2026-02-19"
THEME = (
    "2026年2月19日、訪日中の米共和党系コンサルティング会社の代表と、米国政府関係者を含む"
    "複数名で面談を行った。面談の主題は、米国によるイランへの軍事攻撃切迫の可能性、攻撃"
    "オプション（限定攻撃か体制転換か）、米国内の政治力学、及びイラン・イスラエル情勢が"
    "米中間選挙・日本の対米投資・米中関係・台湾問題に与える波及について。この局面の"
    "地政学的含意を分析してください。"
)


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    audit_path = Path(__file__).parent / "leakage_audit_bc_strategy_result.json"
    leakage = json.loads(audit_path.read_text(encoding="utf-8"))
    if not leakage.get("PASS"):
        log("FATAL: leakage audit did not PASS, refusing to run. See leakage_audit_bc_strategy_result.json")
        raise SystemExit(1)
    log("leakage audit PASS confirmed, proceeding")

    cfg = config_utils.load_config()
    real_client = geo_db_client.SupabaseClient(cfg)
    azure_client, model = make_openai_client(cfg)
    proxy = AsOfEvaluationClient(real_client, T0, allow_write_passthrough=True)

    record = {"case_id": "ib0-bc-strategy", "theme": THEME, "t0_date": T0,
              "evaluation_mode": "retrospective_proxy_directional_as_of_filtered",
              "harness": "eval_harness.AsOfEvaluationClient",
              "notes": "Phase I-B0.1: production pipeline run unmodified, DB client "
                       "substituted with an as-of-date-filtering read proxy. NOT an "
                       "'AI output reviewed by expert' case."}

    session = session_service.create_session(
        proxy, title="I-B0.1 Retrospective: BC Strategy Iran Attack Imminence (T0=2026-02-19)",
        user_id=ACTOR, theme=THEME)
    session_id = session["analysis_session_id"]
    record["analysis_session_id"] = session_id
    log(f"session created: {session_id}")

    t0_time = time.time()
    generated = expert_ai_adapter.generate_scenario_candidates(
        proxy, azure_client, model, theme=THEME, actor_id=ACTOR, profile="quick")
    record["timings_sec"] = {"generate_scenarios": round(time.time() - t0_time, 1)}
    log(f"generated {len(generated['candidates'])} candidates")

    created = session_service.record_scenario_candidates(
        proxy, session_id, candidates=generated["candidates"], display_top_n=5,
        model=generated["model"], prompt_version=generated["prompt_version"],
        cqas_knowledge_version=generated["cqas_knowledge_version"], actor_id=ACTOR)
    record["cqas_all_candidates"] = created
    record["cqas_total_candidates"] = len(created)
    record["cqas_displayed_top5"] = [c for c in created if c.get("displayed")]
    log(f"recorded {len(created)} candidates, {len(record['cqas_displayed_top5'])} displayed")

    displayed = record["cqas_displayed_top5"]
    chosen = displayed[0] if displayed else created[0]
    session_service.select_scenario_candidate(proxy, chosen["scenario_candidate_id"], actor_id=ACTOR)
    record["selected_scenario_candidate_id"] = chosen["scenario_candidate_id"]
    record["selected_scenario_rank"] = chosen.get("rank")
    log(f"selected rank={chosen.get('rank')}")

    fields = expert_ai_adapter.build_design_from_candidate(proxy, theme=THEME, scenario_candidate=chosen)
    design = session_service.create_design_version(
        proxy, session_id, fields=fields,
        change_note="選択したScenario CandidateのCQAS Runから初版を生成(I-B0.1 as-of run)",
        created_by=ACTOR)
    record["analysis_design"] = design
    log("design generated")

    record["as_of_audit_report"] = proxy.audit_report()
    record["write_passthrough_op_count"] = len(proxy._write_passthrough_log)

    out_path = Path(__file__).parent / "phase_ib0_1_bc_strategy_result.json"
    out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"wrote {out_path}")


if __name__ == "__main__":
    main()
