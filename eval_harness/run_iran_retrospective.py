# -*- coding: utf-8 -*-
"""Phase I-B0.2: Iran/Hormuz Retrospective Run (T0=2026-05-14).

T0 theme is built ONLY from R006->R002 (dated ~2026-03-25, Pollack/Vatanka's MEI
analysis, adopted-external, pre-dating M086 by ~7 weeks) plus bare situational framing.
It deliberately does NOT include: M086's conclusion (war resumption likely), M086's
rejection reasoning (gradual-negotiation / stalemate rejected), M086's own indicators,
M032/M140/M109's framing, or any post-T0 fact (e.g. the Islamabad MoU, confirmed via
the leakage audit to be a real July-2026-dated document in this DB -- excluded here
both by the as-of proxy at generation time AND by never being mentioned in the T0
theme text at all).
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

ACTOR = "phase_ib0_2_retrospective_proxy"
T0 = "2026-05-14"
THEME = (
    "2026年2月頃、米国はイランの核施設に対する軍事攻撃を実施した。3月下旬時点の"
    "専門家分析によれば、当初は限定的な核施設破壊を意図していたとみられる米国の"
    "軍事行動は、体制転換的な性格を帯びつつあるとの見方が示されている。ホルムズ"
    "海峡においては掃討作戦が続くものの後手に回っているとの評価もある。イスラエル"
    "国内世論は対イラン軍事行動を支持しているが、これは必ずしもネタニヤフ首相個人"
    "への支持を意味するものではないとされる。2026年5月中旬時点で、この情勢は"
    "依然として継続しており、今後の展開(交渉による緊張緩和か、軍事的対立の"
    "更なる激化か)は不透明である。この局面の地政学的含意を分析してください。"
)


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    audit_path = Path(__file__).parent / "leakage_audit_iran_result.json"
    leakage = json.loads(audit_path.read_text(encoding="utf-8"))
    if not leakage.get("PASS"):
        log("FATAL: leakage audit did not PASS, refusing to run.")
        raise SystemExit(1)
    log("leakage audit PASS confirmed, proceeding")

    cfg = config_utils.load_config()
    real_client = geo_db_client.SupabaseClient(cfg)
    azure_client, model = make_openai_client(cfg)
    proxy = AsOfEvaluationClient(real_client, T0, allow_write_passthrough=True)

    record = {"case_id": "ib0-2-iran-hormuz", "theme": THEME, "t0_date": T0,
              "evaluation_mode": "retrospective_proxy_directional_as_of_filtered",
              "harness": "eval_harness.AsOfEvaluationClient",
              "blinding_note": "T0 theme built ONLY from R006->R002 (2026-03-25, "
                  "Pollack/Vatanka MEI analysis, adopted-external) plus bare "
                  "situational framing. Does NOT include M086's conclusion, rejection "
                  "reasoning, indicators, or framing; does NOT include any post-T0 "
                  "fact (e.g. Islamabad MoU, confirmed present in this DB as a "
                  "2026-07-01-dated document, excluded both from the theme text and "
                  "by the as-of proxy).",
              "notes": "Phase I-B0.2: production pipeline run unmodified, DB client "
                       "substituted with an as-of-date-filtering read proxy. NOT an "
                       "'AI output reviewed by expert' case, NOT Phase I-B1."}

    session = session_service.create_session(
        proxy, title="I-B0.2 Retrospective: Iran/Hormuz Strait Crisis (T0=2026-05-14)",
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
        change_note="選択したScenario CandidateのCQAS Runから初版を生成(I-B0.2 as-of run)",
        created_by=ACTOR)
    record["analysis_design"] = design
    log("design generated")

    record["as_of_audit_report"] = proxy.audit_report()
    record["write_passthrough_op_count"] = len(proxy._write_passthrough_log)

    out_path = Path(__file__).parent / "phase_ib0_2_iran_result.json"
    out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"wrote {out_path}")
    log("===== FREEZE ===== (do not read Eguchi's real Iran material until after this line)")


if __name__ == "__main__":
    main()
