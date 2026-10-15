# -*- coding: utf-8 -*-
"""Past Case Reproduction / Reading Exposure Validation（2026-09-24、ユーザー指示）。

5つのEguchi Session/Review由来ケース（HORMUZ_TOLL_SESSION1, HORMUZ_BLOCKADE_SESSION3,
RUSSIA_SANCTIONS_SESSION2, US_CHINA_AI_SEMICON_SESSION2, RUBIO_VANCE_ROLE_CHOREOGRAPHY）
について、本番のExpert AIパイプライン（geo_expert_engine.py / geo_ios_expert_ai_adapter.py /
geo_ios_reading_portfolio_service.py / geo_ios_analysis_session_service.py）を一切変更せず、
AsOfEvaluationClient（eval_harness既存の読み取り専用as-of-dateプロキシ）を通してHard Blind
Inputのみを与え、1回だけ実行する。

Gold Leakage Guard: 各Caseに対応するT0（Session/資料作成日より前）を設定し、
AsOfEvaluationClientのcreated_at<=T0ゲートで、Case固有のGold的な知識（Actor Intelligence
含む）が実際に遮断されていることを、実行後に該当knowledge_item_idへの直接クエリで検証する。

各Caseにつき:
  1. Hard Blind Input（Case Packの*_BLIND_INPUT.mdからSituation/Known Background/
     Analysis Requestのみを抽出、出典コメント等は除外）をqueryとして使用。
  2. mode="case_analysis", profile="deep"でgenerate_scenario_candidates()を1回呼ぶ
     （hypothesis_registerを含む完全なresponse_jsonを得るため）。
  3. record_scenario_candidates()で本番同様のrank/displayed付与を行う（Stage D）。
  4. geo_expert_responsesから完全なresponse_jsonを取得（Stage A: hypothesis_register等）。
  5. Reading Portfolio由来のcandidateをoriginで抽出（Stage C）。
  6. AsOfEvaluationClientのaudit_report() + 既知リスクIDへの直接クエリでリークガードを確認。
  7. 結果をJSONへ保存。rerunしない。
"""
import json
import re
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

DOCS_ROOT = Path(__file__).parent.parent / "docs"
OUT_DIR = Path(__file__).parent / "blind_retrospective_results"
OUT_DIR.mkdir(exist_ok=True)


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def extract_blind_query(blind_input_path: Path) -> str:
    """*_BLIND_INPUT.mdから、'# Situation'以降(運用上の注意等の末尾セクションは除く)を
    そのままqueryとして使う。出典コメント（冒頭の '> ...' blockquote）は含めない。"""
    text = blind_input_path.read_text(encoding="utf-8")
    m = re.search(r"^# Situation", text, flags=re.MULTILINE)
    if not m:
        raise ValueError(f"'# Situation' not found in {blind_input_path}")
    body = text[m.start():]
    # Rubio/Vanceケースのみ末尾に運用注意セクションがある。それは除外する。
    body = re.split(r"\n---\n\n## この案件のBlind Test運用上の注意", body)[0]
    return body.strip()


CASES = [
    {
        "case_id": "HORMUZ_TOLL_SESSION1",
        "blind_input": DOCS_ROOT / "session1-hormuz-toll-case-pack" / "HORMUZ_TOLL_SESSION1_BLIND_INPUT.md",
        "t0": "2026-07-15",
        "t0_rationale": "既知の背景事実は2026-07-14の20%案撤回まで。Session1実施日(2026-07-31)より前。",
    },
    {
        "case_id": "HORMUZ_BLOCKADE_SESSION3",
        "blind_input": DOCS_ROOT / "hormuz-blockade-case-pack" / "HORMUZ_BLOCKADE_SESSION3_BLIND_INPUT.md",
        "t0": "2026-02-20",
        "t0_rationale": "M140返信日(2026-02-21)の前日。転送元CSISレポート内容のみを許可。",
    },
    {
        "case_id": "RUSSIA_SANCTIONS_SESSION2",
        "blind_input": DOCS_ROOT / "session2-russia-sanctions-case-pack" / "RUSSIA_SANCTIONS_SESSION2_BLIND_INPUT.md",
        "t0": "2026-08-24",
        "t0_rationale": "テーマ1_0824.docxの基準時点。Session2実施日(2026-08-25)の前日。",
    },
    {
        "case_id": "US_CHINA_AI_SEMICON_SESSION2",
        "blind_input": DOCS_ROOT / "session2-us-china-semicon-case-pack" / "US_CHINA_AI_SEMICON_SESSION2_BLIND_INPUT.md",
        "t0": "2026-08-24",
        "t0_rationale": "テーマ2_0824.docxの基準時点。Session2実施日(2026-08-25)の前日。",
    },
    {
        "case_id": "RUBIO_VANCE_ROLE_CHOREOGRAPHY",
        "blind_input": DOCS_ROOT / "rubio-vance-case-pack" / "RUBIO_VANCE_ROLE_CHOREOGRAPHY_BLIND_INPUT.md",
        "t0": "2026-02-16",
        "t0_rationale": "M059返信日(2026-02-17)の前日。",
    },
]

# Gold Leakage Guard: 事前調査(2026-09-24)で発見済みの既知リスクitem ID。
# 実行後、AsOfEvaluationClient経由でこれらが遮断されている(0件)ことを確認する。
KNOWN_RISK_KNOWLEDGE_ITEM_IDS = [
    "65bbd686-f627-4b9b-b1c6-15fa33820dbe",  # Trump profile (candidate, created 2026-08-30)
    "2ec18b13-f115-4a4d-a47f-dc11263b6704",  # Trump profile (active, created 2026-09-07)
    "6fdacbcf-808e-46ea-9048-f31b38aff29e",  # Trump profile (superseded, created 2026-08-28)
    "3434a15c-b8c2-47be-887e-6ba3afa6a732",  # Xi profile (candidate, created 2026-08-30)
    "f44aac12-a2a1-42f0-9508-0620fa7ba3bc",  # Xi profile (active, created 2026-09-07)
    "2aae845f-2d85-4e01-8a04-375caec5ebdd",  # Xi profile (superseded, created 2026-08-28)
    "b4133e96-303f-4853-8cc3-3793e8145913",  # Putin profile (created 2026-08-28)
    "af80ffc0-36e1-4f18-8435-3c713479b0c8",  # Hormuz mechanical_e2e pilot report (created 2026-09-22)
]
KNOWN_RISK_DOCUMENT_IDS = [
    "3351be8b-3a7b-4428-82c7-f46aa0e6c5ca",  # "Trump backs off 20% fee..." doc
    "4782ea75-15f2-47bb-a050-5408f186b989",  # same story, second doc
    "bce3a535-8b82-41b5-8e3b-9800719cd253",  # Islamabad MoU doc
]


def verify_leakage_guard(proxy: AsOfEvaluationClient) -> dict:
    """既知リスクIDが、このCaseのT0の下で実際に0件になることを確認する。"""
    ki_hits = proxy.select("geo_knowledge_items", {
        "select": "knowledge_item_id,created_at",
        "knowledge_item_id": f"in.({','.join(KNOWN_RISK_KNOWLEDGE_ITEM_IDS)})"})
    doc_hits = proxy.select("geo_documents", {
        "select": "document_id,published_at",
        "document_id": f"in.({','.join(KNOWN_RISK_DOCUMENT_IDS)})"})
    return {
        "known_risk_knowledge_items_leaked": ki_hits,
        "known_risk_documents_leaked": doc_hits,
        "gold_leakage_detected": bool(ki_hits or doc_hits),
    }


def run_case(case: dict, smoke_test: bool = False) -> dict:
    case_id = case["case_id"]
    log(f"=== {case_id} (T0={case['t0']}) ===")
    query_text = extract_blind_query(case["blind_input"])
    log(f"query length: {len(query_text)} chars")

    cfg = config_utils.load_config()
    real_client = geo_db_client.SupabaseClient(cfg)
    azure_client, model = make_openai_client(cfg)
    proxy = AsOfEvaluationClient(real_client, case["t0"], allow_write_passthrough=True)

    actor_id = f"blind_retrospective_diagnostic_{case_id.lower()}"

    session = session_service.create_session(
        proxy, title=f"Blind Retrospective Diagnostic: {case_id} (T0={case['t0']})",
        user_id=actor_id, theme=query_text)
    session_id = session["analysis_session_id"]
    log(f"session created: {session_id}")

    t0_time = time.time()
    generated = expert_ai_adapter.generate_scenario_candidates(
        proxy, azure_client, model, theme=query_text, theme_id=None,
        mode="case_analysis", profile="deep", execution_mode="staged",
        actor_id=actor_id, include_reading_portfolio=True)
    elapsed = round(time.time() - t0_time, 1)
    log(f"generate_scenario_candidates: {len(generated['candidates'])} candidates in {elapsed}s")

    # Stage A: 完全なresponse_json（hypothesis_register等）を取得
    resp_rows = proxy.select("geo_expert_responses", {
        "select": "*", "response_id": f"eq.{generated['response_id']}", "limit": "1"})
    response_json = resp_rows[0]["response_json"] if resp_rows else None

    # Stage D: 本番同様のrank/displayed付与
    created = session_service.record_scenario_candidates(
        proxy, session_id, candidates=generated["candidates"], display_top_n=5,
        model=generated["model"], prompt_version=generated["prompt_version"],
        cqas_knowledge_version=generated["cqas_knowledge_version"], actor_id=actor_id)
    displayed = [c for c in created if c.get("displayed")]
    log(f"recorded {len(created)} candidates, {len(displayed)} displayed (top5)")

    # Stage C: Reading Portfolio由来のcandidateを抽出
    portfolio_candidates = [c for c in created if str(c.get("content", {}).get("origin", "")).startswith("reading_portfolio_")]

    leakage = verify_leakage_guard(proxy)
    if leakage["gold_leakage_detected"]:
        log(f"!!! GOLD LEAKAGE DETECTED for {case_id}: {leakage}")

    record = {
        "case_id": case_id,
        "smoke_test": smoke_test,
        "t0": case["t0"],
        "t0_rationale": case["t0_rationale"],
        "actor_id": actor_id,
        "mode": "case_analysis",
        "profile": "deep",
        "execution_mode": "staged",
        "query_text": query_text,
        "analysis_session_id": session_id,
        "expert_request_id": generated["request_id"],
        "expert_response_id": generated["response_id"],
        "elapsed_sec": elapsed,
        "stage_a_full_response_json": response_json,
        "stage_bd_ranked_candidates": created,
        "stage_bd_displayed_top5": displayed,
        "stage_c_reading_portfolio_candidates": portfolio_candidates,
        "leakage_guard": leakage,
        "as_of_audit_report": proxy.audit_report(),
        "valid": not leakage["gold_leakage_detected"],
    }

    out_path = OUT_DIR / f"{case_id}{'_SMOKE' if smoke_test else ''}.json"
    out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    log(f"saved: {out_path}")
    return record


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    if target == "--smoke":
        smoke_case = {
            "case_id": "SMOKE_TEST",
            "t0": "2026-01-01",
            "t0_rationale": "smoke test, not a real case",
        }
        # 使い捨ての無関係なダミーqueryでプラミングだけ確認する（実ケースのrerun regulationとは無関係）。
        cfg = config_utils.load_config()
        real_client = geo_db_client.SupabaseClient(cfg)
        azure_client, model = make_openai_client(cfg)
        proxy = AsOfEvaluationClient(real_client, smoke_case["t0"], allow_write_passthrough=True)
        actor_id = "blind_retrospective_diagnostic_smoke_test"
        session = session_service.create_session(
            proxy, title="Smoke test (pipeline plumbing only, not a real case)",
            user_id=actor_id, theme="スモークテスト：この文章はダミーです。日本の食品業界における物価動向を一般的に分析してください。")
        session_id = session["analysis_session_id"]
        log(f"smoke session created: {session_id}")
        generated = expert_ai_adapter.generate_scenario_candidates(
            proxy, azure_client, model, theme="スモークテスト：この文章はダミーです。日本の食品業界における物価動向を一般的に分析してください。",
            theme_id=None, mode="case_analysis", profile="deep", execution_mode="staged",
            actor_id=actor_id, include_reading_portfolio=True)
        log(f"smoke test OK: {len(generated['candidates'])} candidates, response_id={generated['response_id']}")
        resp_rows = proxy.select("geo_expert_responses", {
            "select": "*", "response_id": f"eq.{generated['response_id']}", "limit": "1"})
        rj = resp_rows[0]["response_json"] if resp_rows else {}
        log(f"response_json top-level keys: {list(rj.keys())}")
        log(f"hypothesis_register present: {'hypothesis_register' in rj}, "
            f"len={len(rj.get('hypothesis_register') or [])}")
        created = session_service.record_scenario_candidates(
            proxy, session_id, candidates=generated["candidates"], display_top_n=5,
            model=generated["model"], prompt_version=generated["prompt_version"],
            cqas_knowledge_version=generated["cqas_knowledge_version"], actor_id=actor_id)
        log(f"record_scenario_candidates OK: {len(created)} rows, "
            f"{len([c for c in created if c.get('displayed')])} displayed")
        log("SMOKE TEST PASSED")
    elif target:
        matching = [c for c in CASES if c["case_id"] == target]
        if not matching:
            print(f"unknown case_id: {target}")
            sys.exit(1)
        run_case(matching[0])
    else:
        for case in CASES:
            run_case(case)
