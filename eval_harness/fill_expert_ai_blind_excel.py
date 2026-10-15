# -*- coding: utf-8 -*-
"""Expert AI Blind Test結果を02_expert_ai_blind.xlsxの03_AI出力へ転記する（採点はしない）。

run_expert_ai_blind_test_20cases.py が生成した evaluation/expert_ai_blind_test/raw/*.json
（既存Expert AI frozenパイプラインの生出力、無改変）を読み、既存の03_AI出力シートの
既存行（Run ID / Case ID / AI種別 / Candidate順位は元のまま）に対して、以下の列だけを
埋める。新しい行・新しい採点列は追加しない。

- モデル/Version: raw JSONのcandidate.model / prompt_version
- 実行日: candidate.generated_at（UTC、ISO文字列をそのまま記録）
- 実行条件: mode/profile/execution_mode/include_reading_portfolio（runner呼び出し時の設定）
- Web/外部検索: AsOfEvaluationClientのaudit_report（基準日以前のみに絞られたことの実測値）
- 入力Prompt（完全版）: 呼び出し元が与えたquery_text（Situation/背景情報/問い）。
  既知の制約として、Expert AIエンジン内部で実際にLLMへ渡される検索結果込みの完全な
  組み立て済みプロンプトはfrozenコードを変更しない限り取得できない旨を明記する
  （geo_expert_requestsテーブルにもquery_text以上の保存は無いことをDBで確認済み）。
- AIの読み方 / 根拠 / 弱化/反証: candidate.content.raw から、origin種別
  （main_scenario / alternative_scenario / reading_portfolio_*）ごとに該当フィールドを
  そのまま転記する。フィールドが存在しない場合は空欄のままにし、捏造しない。
- Primary?: rank1（main_scenario）のみ"Yes"、他は空欄（positionalなPrimary、
  明示的なbooleanフィールドは元データに存在しない）。
- 生出力保存先/メモ: raw JSONファイルパス + confidence/evidence_class等の補助情報。
"""
import json
from pathlib import Path

import openpyxl

PROJECT_ROOT = Path(__file__).parent.parent
RAW_DIR = PROJECT_ROOT / "evaluation" / "expert_ai_blind_test" / "raw"

SRC_XLSX = Path(r"C:\Users\269811\Downloads\02_expert_ai_blind.xlsx")
OUT_XLSX = SRC_XLSX.parent / "expert_ai_completed.xlsx"

PROMPT_LIMITATION_NOTE = (
    "【制約】Expert AIエンジン内部で実際にLLMへ渡される、検索結果(source_pack)を"
    "含めた完全な組み立て済みプロンプト文字列は、frozenコードを変更しない限り取得不可"
    "（geo_expert_requestsテーブルの保存列はquery_text以上の情報を持たないことをDBで確認済み）。"
    "以下は呼び出し元がExpert AIへ渡した入力（query_text）のみ。\n\n"
)


def join_list(v):
    if not v:
        return ""
    return " / ".join(str(x) for x in v)


def extract_reading_fields(origin: str, raw: dict):
    """(AIの読み方, 根拠, 弱化/反証, メモ用補助情報) を origin別に取り出す。捏造しない。"""
    if origin == "main_scenario":
        reading = raw.get("description") or ""
        rationale = raw.get("selection_rationale") or ""
        wer = raw.get("weakening_evidence_relevance")
        if wer:
            weakening = join_list(wer)
        else:
            car = raw.get("confidence_adjustment_reason")
            weakening = (
                f"[weakening_evidence_relevance無し。confidence_adjustment_reasonで代替] {car}"
                if car else ""
            )
        memo = (
            f"confidence={raw.get('confidence')} / evidence_class={raw.get('evidence_class')} / "
            f"derived_from_hypothesis_id={raw.get('derived_from_hypothesis_id')}"
        )
    elif origin == "alternative_scenario":
        reading = raw.get("description") or ""
        rationale = join_list(raw.get("strengthening_conditions"))
        weakening = join_list(raw.get("weakening_conditions"))
        switch = join_list(raw.get("switch_conditions"))
        if switch:
            weakening = f"{weakening} / [switch_conditions] {switch}" if weakening else f"[switch_conditions] {switch}"
        memo = (
            f"confidence={raw.get('confidence')} / evidence_class={raw.get('evidence_class')} / "
            f"derived_from_hypothesis_id={raw.get('derived_from_hypothesis_id')}"
        )
    elif origin.startswith("reading_portfolio"):
        title = raw.get("title") or ""
        body = raw.get("reading") or ""
        reading = f"[{title}] {body}" if title else body
        rationale = join_list(raw.get("supporting_reasons"))
        key_cue = raw.get("key_cue")
        if key_cue:
            rationale = f"[key_cue] {key_cue} / {rationale}" if rationale else f"[key_cue] {key_cue}"
        weakening = join_list(raw.get("weakening_information"))
        switch = raw.get("switch_condition")
        if switch:
            weakening = f"{weakening} / [switch_condition] {switch}" if weakening else f"[switch_condition] {switch}"
        memo = f"slot={raw.get('slot')} / insufficient_material={raw.get('insufficient_material')}"
    else:
        reading, rationale, weakening = raw.get("description") or "", "", ""
        memo = f"未知のorigin={origin}（raw.get('description')のみ転記）"
    return reading, rationale, weakening, memo


def format_web_search(audit: dict) -> str:
    as_of = audit.get("as_of_date")
    tables = audit.get("tables", {})
    content_tables = [
        t for t in ("geo_documents", "geo_events", "geo_knowledge_items", "geo_relationships")
        if t in tables
    ]
    parts = [f"ON（AsOfEvaluationClient経由、基準日={as_of}以前に限定。実測値:）"]
    for t in content_tables:
        info = tables[t]
        parts.append(
            f"{t}: included={info.get('included_count')} "
            f"excluded_future={info.get('excluded_future_count')} "
            f"excluded_unverified={info.get('excluded_unverified_count')}"
        )
    return " / ".join(parts)


def main():
    wb = openpyxl.load_workbook(SRC_XLSX)
    ws = wb["03_AI出力"]

    cache = {}
    missing_cases = set()
    filled = 0

    for row in ws.iter_rows(min_row=2):
        case_id_cell, rank_cell = row[1], row[8]
        case_id = case_id_cell.value
        rank = rank_cell.value
        if not case_id or rank is None:
            continue

        if case_id not in cache:
            path = RAW_DIR / f"{case_id}.json"
            if not path.exists():
                missing_cases.add(case_id)
                cache[case_id] = None
            else:
                cache[case_id] = json.loads(path.read_text(encoding="utf-8"))
        record = cache[case_id]
        if record is None:
            continue

        cand = next((c for c in record["displayed_top_n"] if c["rank"] == rank), None)
        if cand is None:
            # このrankは今回のrunでは生成されなかった(5件未満)。捏造せず空欄のままにする。
            note_cell = row[13]
            note_cell.value = (
                f"このケースはdisplayed_top_n={len(record['displayed_top_n'])}件のみで、"
                f"rank={rank}は生成されていない。raw: evaluation/expert_ai_blind_test/raw/{case_id}.json"
            )
            continue

        origin = cand["content"]["origin"]
        raw = cand["content"]["raw"]
        reading, rationale, weakening, extra_memo = extract_reading_fields(origin, raw)

        row[3].value = f"{cand.get('model')} / prompt_version={cand.get('prompt_version')}"  # モデル/Version
        row[4].value = cand.get("generated_at")  # 実行日
        row[5].value = (  # 実行条件
            f"mode={record['mode']} / profile={record['profile']} / "
            f"execution_mode={record['execution_mode']} / include_reading_portfolio=True / "
            f"cqas_knowledge_version={cand.get('cqas_knowledge_version')}"
        )
        row[6].value = format_web_search(record["as_of_audit_report"])  # Web/外部検索
        row[7].value = PROMPT_LIMITATION_NOTE + record["input_given"]["query_text_as_sent"]  # 入力Prompt（完全版）
        row[9].value = reading  # AIの読み方
        row[10].value = rationale  # 根拠
        row[11].value = weakening  # 弱化/反証
        row[12].value = "Yes" if origin == "main_scenario" else ""  # Primary?
        row[13].value = (  # 生出力保存先/メモ
            f"evaluation/expert_ai_blind_test/raw/{case_id}.json "
            f"(analysis_session_id={record['analysis_session_id']}, "
            f"expert_request_id={record['expert_request_id']}) / origin={origin} / {extra_memo}"
        )
        filled += 1

    wb.save(OUT_XLSX)
    print(f"filled rows: {filled}")
    if missing_cases:
        print(f"missing raw json for cases: {sorted(missing_cases)}")
    print(f"saved: {OUT_XLSX}")


if __name__ == "__main__":
    main()
