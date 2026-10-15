# -*- coding: utf-8 -*-
"""Expert AI Blind Test（20ケース、02_EXPERT_AI_CLAUDE_CODE_INSTRUCTIONS.md指示、2026-10-13）。

絶対ルール（指示書より、厳守）:
- geo_expert_engine.py / geo_expert_schema.py / geo_ios_expert_ai_adapter.py 等、既存Expert AI
  のコード・prompt・retrieval・ranking・候補選択ロジックは一切変更しない。呼び出すのは既存の
  唯一の公開Entry Point経由（geo_ios_expert_ai_adapter.generate_scenario_candidates、
  run_blind_retrospective_diagnostic.pyと全く同じ呼び出し方）のみ。
- Gold・過去レポート・江口コメント・Baseline回答はモデル入力へ渡さない（01_テストケースの
  E/F/G/D列＝Situation/背景情報/問い/基準日のみを使う）。
- 基準日後の情報をretrieval/contextへ混入させない: eval_harness.as_of_evaluation_client.
  AsOfEvaluationClient（既存の読み取り専用as-of-dateプロキシ、無改変）で実DB clientをラップする。
- Goldを見て再生成・prompt調整・コード修正はしない。失敗・低品質出力もそのまま保存する。
- 各ケースを独立sessionとして実行する（geo_ios_analysis_sessionsへ1件ずつ作成、
  run_blind_retrospective_diagnostic.pyのrun_case()と同じ構成）。

既知の制約（正直に記録する、指示書にない推測はしない）:
- 「入力Prompt（完全版）」列について: Expert AIエンジン内部で実際にLLMへ渡される、
  検索結果（source_pack）を含めた完全な組み立て済みプロンプト文字列は、frozenコードを
  変更しない限り取得できない（geo_expert_requestsテーブルにもquery_text以上の保存は無い、
  実DBで確認済み）。本runnerは「呼び出し元が与えた入力」（mode/profile/execution_mode/
  query_text）を記録するに留める。
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

OUT_DIR = Path(__file__).parent.parent / "evaluation" / "expert_ai_blind_test" / "raw"
OUT_DIR.mkdir(parents=True, exist_ok=True)

MODE = "case_analysis"
PROFILE = "deep"
EXECUTION_MODE = "staged"
DISPLAY_TOP_N = 5


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run_case(case: dict, *, smoke_test: bool = False) -> dict:
    """case: {"case_id","category","theme","as_of_date","situation","background","question"}
    （01_テストケースのB/C/D/E/F/G列からそのまま組み立てる、Gold/除外情報列は使わない）。"""
    case_id = case["case_id"]
    log(f"=== {case_id} (基準日={case['as_of_date']}) ===")

    query_text = (
        f"【Situation】\n{case['situation']}\n\n"
        f"【背景情報】\n{case['background']}\n\n"
        f"【問い】\n{case['question']}"
    )

    cfg = config_utils.load_config()
    real_client = geo_db_client.SupabaseClient(cfg)
    azure_client, model = make_openai_client(cfg)
    # allow_write_passthrough=True: geo_expert_requests/geo_expert_responses/
    # geo_ios_analysis_sessions/geo_ios_scenario_candidates は「このrun自身が今日作る新規行」
    # であり世界知識のリークではないため書き込みを許可する（run_blind_retrospective_
    # diagnostic.pyと同じ判断、AsOfEvaluationClient自身のdocstring参照）。それ以外の
    # 全テーブル（geo_documents/geo_events/geo_knowledge_items等）はas_of_date以前のみ。
    proxy = AsOfEvaluationClient(real_client, case["as_of_date"], allow_write_passthrough=True)

    actor_id = f"expert_ai_blind_test_{case_id.lower()}"

    session = session_service.create_session(
        proxy, title=f"Expert AI Blind Test: {case_id} ({case['theme']})",
        user_id=actor_id, theme=query_text)
    session_id = session["analysis_session_id"]
    log(f"session created: {session_id}")

    t0_time = time.time()
    try:
        generated = expert_ai_adapter.generate_scenario_candidates(
            proxy, azure_client, model, theme=query_text, theme_id=None,
            mode=MODE, profile=PROFILE, execution_mode=EXECUTION_MODE,
            actor_id=actor_id, include_reading_portfolio=True)
        error = None
    except Exception as e:  # noqa: BLE001 — 失敗もそのまま記録する（絶対ルール: 失敗を隠さない）
        generated = None
        error = f"{type(e).__name__}: {e}"
        log(f"!!! FAILED: {error}")
    elapsed = round(time.time() - t0_time, 1)

    created, displayed = [], []
    if generated is not None:
        log(f"generate_scenario_candidates: {len(generated['candidates'])} candidates in {elapsed}s")
        created = session_service.record_scenario_candidates(
            proxy, session_id, candidates=generated["candidates"], display_top_n=DISPLAY_TOP_N,
            model=generated["model"], prompt_version=generated["prompt_version"],
            cqas_knowledge_version=generated["cqas_knowledge_version"], actor_id=actor_id)
        displayed = [c for c in created if c.get("displayed")]
        log(f"recorded {len(created)} candidates, {len(displayed)} displayed (top{DISPLAY_TOP_N})")

    record = {
        "case_id": case_id,
        "category": case["category"],
        "theme": case["theme"],
        "smoke_test": smoke_test,
        "as_of_date": case["as_of_date"],
        "actor_id": actor_id,
        "mode": MODE, "profile": PROFILE, "execution_mode": EXECUTION_MODE,
        "input_given": {
            "situation": case["situation"], "background": case["background"],
            "question": case["question"], "query_text_as_sent": query_text,
        },
        "analysis_session_id": session_id,
        "expert_request_id": generated["request_id"] if generated else None,
        "expert_response_id": generated["response_id"] if generated else None,
        "model": generated["model"] if generated else None,
        "prompt_version": generated["prompt_version"] if generated else None,
        "elapsed_sec": elapsed,
        "error": error,
        "ranked_candidates": created,
        "displayed_top_n": displayed,
        "as_of_audit_report": proxy.audit_report(),
    }

    suffix = "_SMOKE" if smoke_test else ""
    out_path = OUT_DIR / f"{case_id}{suffix}.json"
    out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    log(f"saved: {out_path}")
    return record


def load_cases_from_xlsx(xlsx_path: str) -> list:
    import openpyxl
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb["01_テストケース"]
    cases = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        case_id, category, theme, as_of_date, situation, background, question, _excluded = row[:8]
        cases.append({
            "case_id": case_id, "category": category, "theme": theme,
            "as_of_date": str(as_of_date)[:10], "situation": situation,
            "background": background, "question": question,
        })
    return cases


if __name__ == "__main__":
    xlsx_path = sys.argv[2] if len(sys.argv) > 2 else r"C:\Users\269811\Downloads\02_expert_ai_blind.xlsx"
    all_cases = load_cases_from_xlsx(xlsx_path)
    target = sys.argv[1] if len(sys.argv) > 1 else None

    if target == "--smoke":
        run_case(all_cases[0], smoke_test=True)
    elif target == "--all":
        for c in all_cases:
            run_case(c, smoke_test=False)
    elif target:
        case = next((c for c in all_cases if c["case_id"] == target), None)
        if not case:
            print(f"case not found: {target}")
            sys.exit(1)
        run_case(case, smoke_test=False)
    else:
        print("usage: python run_expert_ai_blind_test_20cases.py [--smoke|--all|TC01] [xlsx_path]")
