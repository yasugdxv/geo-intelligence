"""Report Studio v2 Phase 1: Report Edit Chat（自然言語によるFinal Report部分編集）。

詳細設計: kind-napping-hare.md Phase G「Report Edit Chat」節、および2026-09-13の
Report Studio v2完成方針（Completion Audit 20番）。

設計原則:
1. AIはHTMLを直接生成しない。既存のPrompt2 Composition/Renderer基盤
   （`geo_report_prompt2_composition_service.py`のcomponent_type別Rendererと
   `geo_report_run_artifacts`(artifact_type='composition_result')構造）をそのまま
   再利用する——影響を受けるSectionのcomposed dict（構造化JSON）だけをAIに
   再生成させ、Python Rendererで再描画する。
2. Report Editは必ず独立Execution（`stage='report_edit'`、
   `parent_execution_id`=編集直前の`canonical_report_execution_id`）として記録する。
   `canonical_report_execution_id`のみを更新し、`canonical_prompt2_execution_id`
   （Continuityの起点）は変更しない——Editを何度繰り返してもContinuityは常に
   元のPrompt2 Executionの結果を引き継ぐ。
3. Edit Level A(Local)/B(Multi-section)はReport Edit単独で処理するが、
   C(Analytical Recomposition Required、Central Assessment/Scenario/Watchpoint/
   Continuity等の分析骨格が変わる修正)はAIに勝手に処理させず、
   「Prompt2 Recompositionが必要」という応答のみ返す（canonicalは更新しない）。
4. ChatGPT Importされた結果はcomposition_resultを持たない可能性があるため、
   初回Edit時のみ`normalize_report_to_composition`でHTML→構造化JSONへ1回だけ
   変換し、以後はそのJSONを使う（2回目以降はHTML再解析しない）。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import geo_llm_common
import geo_report_artifact_storage_service as artifact_storage
import geo_report_execution_canonical_service as canonical
import geo_report_project_assets_service as assets
import geo_report_prompt2_composition_service as comp
from geo_external_signal_service import _select_all_paginated

EDIT_LEVELS = ("local", "multi_section", "analytical_recomposition_required")


def _new_id() -> str:
    return str(uuid.uuid4())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_run(client, run_id: str) -> dict:
    rows = client.select("geo_report_collection_runs", {"select": "*", "run_id": f"eq.{run_id}"})
    if not rows:
        raise ValueError(f"run_id={run_id} が見つかりません。")
    return rows[0]


def _get_execution(client, execution_id: str) -> dict:
    rows = client.select("geo_report_executions", {"select": "*", "id": f"eq.{execution_id}"})
    if not rows:
        raise ValueError(f"execution_id={execution_id} が見つかりません。")
    return rows[0]


def _artifact_row(client, run_id: str, execution_id: str, execution_method: str, artifact_type: str,
                   file_name: str, content_text: str, media_type: str) -> dict:
    storage_fields = artifact_storage.build_artifact_storage_fields(
        client, run_id, execution_id, artifact_type, file_name, content_text.encode("utf-8"),
        content_type=media_type)
    return {
        "id": _new_id(), "run_id": run_id, "execution_id": execution_id, "execution_method": execution_method,
        "artifact_type": artifact_type, "file_name": file_name, "media_type": media_type,
        **storage_fields,
    }


# ---------------------------------------------------------------------------
# composition_result読み書き（既存のgeo_report_prompt2_orchestration_service.
# _upsert_composition_resultと同じ形。Report Edit Executionからも同じ形で
# 読み書きできるようにここでも実装する）
# ---------------------------------------------------------------------------

def load_composition_results(client, execution_id: str) -> dict:
    """execution_id直下のcomposition_result一覧を{section_id: composed dict}で返す。
    そのexecution自体が1件もcomposition_resultを持たなければ空dict。"""
    rows = _select_all_paginated(client, "geo_report_run_artifacts", {
        "select": "file_name,content_text,storage_bucket,storage_path", "execution_id": f"eq.{execution_id}",
        "artifact_type": "eq.composition_result",
    })
    return {
        row["file_name"].removesuffix(".json"): json.loads(artifact_storage.read_artifact_content(client, row))
        for row in rows
    }


def save_composition_result(client, run_id: str, execution_id: str, execution_method: str,
                              section_id: str, composed: dict) -> None:
    content_text = json.dumps(composed, ensure_ascii=False)
    existing = client.select("geo_report_run_artifacts", {
        "select": "id", "execution_id": f"eq.{execution_id}", "artifact_type": "eq.composition_result",
        "file_name": f"eq.{section_id}.json",
    })
    row = _artifact_row(client, run_id, execution_id, execution_method, "composition_result",
                          f"{section_id}.json", content_text, "application/json")
    if existing:
        client.update("geo_report_run_artifacts", {"id": f"eq.{existing[0]['id']}"}, {
            "content_text": row["content_text"], "storage_bucket": row["storage_bucket"],
            "storage_path": row["storage_path"], "sha256": row["sha256"], "size_bytes": row["size_bytes"],
        })
    else:
        client.insert("geo_report_run_artifacts", [row], prefer="return=minimal")


# ---------------------------------------------------------------------------
# Execution chainを遡る（report_edit → ... → prompt2）
# ---------------------------------------------------------------------------

def find_source_prompt2_execution(client, execution: dict) -> dict:
    """report_edit executionのparent_execution_idを辿り、元のprompt2 executionを返す
    （Editが複数回連鎖していても正しく最上流のprompt2 executionへ到達する）。"""
    current = execution
    seen = set()
    while current["stage"] != "prompt2":
        if current["id"] in seen:
            raise ValueError(f"execution_id={current['id']}でparent_execution_idの循環を検出しました。")
        seen.add(current["id"])
        parent_id = current.get("parent_execution_id")
        if not parent_id:
            raise ValueError(f"execution_id={current['id']}からprompt2 executionへ到達できません。")
        current = _get_execution(client, parent_id)
    return current


# ---------------------------------------------------------------------------
# ChatGPT Import経路の初回Edit対応（composition_resultが無い場合のみ）
# ---------------------------------------------------------------------------

_NORMALIZE_SCHEMA = {
    "type": "object",
    "properties": {
        "sections": {
            "type": "object",
            "additionalProperties": {"type": "object"},
        },
    },
    "required": ["sections"],
    "additionalProperties": False,
}


def normalize_report_to_composition(client, azure_client, model: str, run_id: str,
                                       execution_id: str, final_report_html: str, template_sections: list) -> dict:
    """ChatGPT Importされたfinal_report.html（composition_resultを持たない）から、
    Template Sectionのcomponent_type/required_fieldsに沿って構造化JSONへ1回だけ
    変換する。変換結果はそのexecution_idへcomposition_resultとして保存し、以後は
    このJSONを使う（2回目以降はHTML再解析しない）。"""
    sections_desc = "\n".join(
        f"- section_id={s['section_id']} title={s.get('title')} component_type={s['component_type']} "
        f"required_fields={s.get('required_fields')}"
        for s in template_sections
    )
    system_prompt = (
        "あなたはHTMLレポートを構造化データへ変換するツールです。以下のFinal Report HTMLから、"
        "指定されたSection定義ごとに、そのcomponent_typeが要求するrequired_fieldsを全て含む"
        "構造化JSONを抽出してください。本文の意味を変えず、要約や創作をしないこと。"
    )
    user_prompt = (
        f"## Section定義\n{sections_desc}\n\n## Final Report HTML\n{final_report_html}\n\n"
        "各section_idについて、対応するcomponent_typeのフィールドを持つオブジェクトを"
        "`sections`へ格納して返してください。"
    )
    result = geo_llm_common.call_llm_structured(
        azure_client, model, system_prompt, user_prompt, _NORMALIZE_SCHEMA,
        "geo_report_normalize_report_to_composition", max_attempts=2)
    composed_by_section = result["data"]["sections"]
    for section_id, composed in composed_by_section.items():
        save_composition_result(client, run_id, execution_id, "chatgpt_import", section_id, composed)
    return composed_by_section


# ---------------------------------------------------------------------------
# Edit Instruction（2段階LLM呼び出し）
#
# 2026-09-13実データ検証で発覚した重大な不具合の修正: 当初はedit_level/
# affected_section_ids/section_patchesを1回のLLM呼び出しでまとめて取得していたが、
# section_patchesのJSON Schemaを`additionalProperties: {"type": "object"}`という
# 緩い定義にしていたため、AIがフィールド名を自由に発明できてしまい
# （実際に、Template Assetのsection_def.required_fields——例:
# `core_hypothesis`/`exec_metric_1_label`等、Project固有のカスタム名——を
# プロンプトへ見せていたことも一因で、AIがそちらのフィールド名を採用してしまい、
# 本来のcomponent_type固定スキーマ（`central_conclusion`等）と食い違う出力を
# 返した）、Python Rendererが本来のキーを読めず内容が空文字列になっていた。
# 修正: ①どのSectionが影響を受けるか（edit_level/affected_section_ids/reason/
# warning）だけを判定するPlan呼び出しと、②影響を受けた各Sectionを
# `geo_report_prompt2_composition_service._COMPONENT_SCHEMAS[component_type]`
# という厳密なJSON Schema（Prompt2本番のcompose_sectionと全く同じschema）で
# 個別に再生成する呼び出しへ分離した。structured outputの
# additionalProperties=false制約により、フィールド名の逸脱が構造的に発生しなく
# なる。
# ---------------------------------------------------------------------------

def _edit_plan_schema(section_ids: list) -> dict:
    return {
        "type": "object",
        "properties": {
            "edit_level": {"type": "string", "enum": list(EDIT_LEVELS)},
            "affected_section_ids": {"type": "array", "items": {"type": "string", "enum": section_ids}},
            "reason": {"type": "string"},
            "warning": {"type": ["string", "null"]},
        },
        "required": ["edit_level", "affected_section_ids", "reason", "warning"],
        "additionalProperties": False,
    }


def _edit_plan_system_prompt() -> str:
    return (
        "あなたはReport StudioのReport Edit Chatです。ユーザーの自然言語による修正指示を受け、"
        "どのSectionを更新すべきか、どのレベルの修正かを判定してください（この段階ではSectionの"
        "内容そのものは生成しません）。\n\n"
        "edit_levelの判定基準:\n"
        "- 'local': 1つのSection内の文章・例・表現レベルの修正。\n"
        "- 'multi_section': 複数Sectionにまたがる、文章レベルの修正。\n"
        "- 'analytical_recomposition_required': Central Assessment・Scenario・"
        "Watchpoint・Continuity評価など、分析結論や解釈そのものが変わる修正の場合。"
        "この場合はaffected_section_idsを空([])にし、reasonとwarningでユーザーへ"
        "Prompt2 Recompositionが必要である旨を説明すること。あなた自身の判断で"
        "分析結論・Continuity評価を書き換えてはならない。"
    )


def _build_edit_plan_user_prompt(sections: list, composed_by_section: dict, instruction: str) -> str:
    lines = ["## 現在のReport構成（Section毎、要約）", ""]
    for s in sections:
        section_id = s["section_id"]
        composed = composed_by_section.get(section_id)
        if composed is None:
            continue
        lines.append(f"### section_id={section_id} title={s.get('title')} "
                      f"component_type={s['component_type']}")
        lines.append(f"現在の内容: {json.dumps(composed, ensure_ascii=False)}")
        lines.append("")
    lines.append("## ユーザーの修正指示")
    lines.append(instruction)
    return "\n".join(lines)


def _regenerate_section_for_edit(azure_client, model: str, section_def: dict, current_composed: dict,
                                    instruction: str, *, max_attempts: int = 2) -> dict:
    """1つのSectionを、そのcomponent_type固定のJSON Schema（Prompt2本番の
    compose_sectionと全く同じschema）で再生成する。フィールド名の逸脱を
    構造的に防ぐため、Template Asset側のrequired_fields（Project固有の
    カスタム名でありcomponent_type標準schemaとは無関係）はここでは一切見せない。"""
    component_type = section_def.get("component_type")
    schema = comp._COMPONENT_SCHEMAS.get(component_type)
    if schema is None:
        raise ValueError(f"未対応のcomponent_typeです: {component_type}")
    system_prompt = (
        "あなたはReport StudioのReport Edit Chatです。指定されたSectionの現在の内容と、"
        "ユーザーの修正指示を受け、そのSectionの新しい内容を指定されたJSON Schemaの形で"
        "返してください。HTMLやCSSは書かないこと。指示に関係しないフィールドは現在の内容を"
        "維持し、指示に関係する部分だけを変更すること。"
    )
    user_prompt = (
        f"## Section: {section_def.get('title')} (section_id={section_def.get('section_id')})\n"
        f"component_type: {component_type}\n\n"
        f"## 現在の内容\n{json.dumps(current_composed, ensure_ascii=False)}\n\n"
        f"## ユーザーの修正指示\n{instruction}"
    )
    result = geo_llm_common.call_llm_structured(
        azure_client, model, system_prompt, user_prompt, schema,
        f"geo_report_edit_section_{component_type}", max_attempts=max_attempts)
    return result["data"]


# ---------------------------------------------------------------------------
# メインオーケストレーション
# ---------------------------------------------------------------------------

def request_report_edit(client, azure_client, model: str, run_id: str, instruction: str, *,
                          execution_method: str = "application_ai") -> dict:
    """自然言語指示を受け、影響を受けるSectionのみ再生成してFinal Reportを更新する。
    戻り値: {"execution_id","edit_level","affected_section_ids","final_report_html",
             "reason","warning","requires_prompt2_recomposition"}。
    edit_level='analytical_recomposition_required'の場合、final_report_htmlはNoneで
    canonicalは変更しない（Prompt2 Recompositionをユーザーへ案内する想定）。"""
    run = _get_run(client, run_id)
    parent_execution_id = run.get("canonical_report_execution_id")
    if not parent_execution_id:
        raise ValueError(
            f"run_id={run_id}にはcanonical_report_execution_idが無く、Report Editの対象がありません"
            "（Prompt2完了前）。")
    parent_execution = _get_execution(client, parent_execution_id)

    template_asset = assets.get_asset_by_id(client, run["report_template_asset_id"])
    sections = template_asset["content"]["sections"]

    composed_by_section = load_composition_results(client, parent_execution_id)
    if not composed_by_section:
        # ChatGPT Import経路、初回のみHTML→composition正規化（2回目以降は
        # load_composition_resultsが上のifを通らずこのブロックをスキップする）。
        final_report_rows = client.select("geo_report_run_artifacts", {
            "select": "content_text,storage_bucket,storage_path", "execution_id": f"eq.{parent_execution_id}",
            "artifact_type": "eq.final_report", "limit": "1",
        })
        if not final_report_rows:
            raise ValueError(f"execution_id={parent_execution_id}にfinal_report artifactがありません。")
        final_report_html = artifact_storage.read_artifact_content(client, final_report_rows[0])
        composed_by_section = normalize_report_to_composition(
            client, azure_client, model, run_id, parent_execution_id, final_report_html, sections)

    plan_schema = _edit_plan_schema([s["section_id"] for s in sections])
    plan_user_prompt = _build_edit_plan_user_prompt(sections, composed_by_section, instruction)
    plan_result = geo_llm_common.call_llm_structured(
        azure_client, model, _edit_plan_system_prompt(), plan_user_prompt, plan_schema,
        "geo_report_edit_plan", max_attempts=2)
    edit_data = plan_result["data"]
    edit_level = edit_data["edit_level"]

    execution_id = _new_id()
    client.insert("geo_report_executions", [{
        "id": execution_id, "run_id": run_id, "stage": "report_edit", "execution_method": execution_method,
        "status": "running", "parent_execution_id": parent_execution_id, "started_at": _now_iso(),
    }])
    instruction_artifact_content = json.dumps({
        "instruction": instruction, "edit_level": edit_level,
        "affected_section_ids": edit_data.get("affected_section_ids", []),
        "reason": edit_data.get("reason"), "warning": edit_data.get("warning"),
    }, ensure_ascii=False)
    client.insert("geo_report_run_artifacts", [_artifact_row(
        client, run_id, execution_id, execution_method, "report_edit_instruction",
        "edit_instruction.json", instruction_artifact_content, "application/json")], prefer="return=minimal")

    if edit_level == "analytical_recomposition_required":
        client.update("geo_report_executions", {"id": f"eq.{execution_id}"}, {
            "status": "completed", "completed_at": _now_iso(),
        })
        return {
            "execution_id": execution_id, "edit_level": edit_level, "affected_section_ids": [],
            "final_report_html": None, "reason": edit_data.get("reason"), "warning": edit_data.get("warning"),
            "requires_prompt2_recomposition": True,
        }

    affected_ids = edit_data.get("affected_section_ids", [])
    section_by_id = {s["section_id"]: s for s in sections}
    new_composed_by_section = dict(composed_by_section)
    for section_id in affected_ids:
        section_def = section_by_id.get(section_id)
        current_composed = composed_by_section.get(section_id)
        if section_def is None or current_composed is None:
            continue
        new_composed_by_section[section_id] = _regenerate_section_for_edit(
            azure_client, model, section_def, current_composed, instruction)

    for section_id, composed in new_composed_by_section.items():
        save_composition_result(client, run_id, execution_id, execution_method, section_id, composed)

    # stacked_bar_chart Sectionの再描画にはAggregation結果(biweekly_rows)が必要
    # なため、chainを遡ってprompt2 executionのinput_aggregation_execution_idを取得する。
    prompt2_execution = find_source_prompt2_execution(client, parent_execution)
    biweekly_rows = []
    if any(s.get("component_type") == "stacked_bar_chart" for s in sections):
        from geo_report_prompt2_orchestration_service import _load_aggregation_artifact_rows
        biweekly_rows = _load_aggregation_artifact_rows(
            client, prompt2_execution["input_aggregation_execution_id"], "biweekly_trend_data.csv")

    section_html_by_id = {}
    for section_def in sections:
        section_id = section_def["section_id"]
        composed = new_composed_by_section.get(section_id)
        if composed is None:
            continue
        chart_svg = comp.render_stacked_bar_chart_svg(biweekly_rows) \
            if section_def.get("component_type") == "stacked_bar_chart" else None
        section_html_by_id[section_id] = comp.render_report_section(section_def, composed, chart_svg=chart_svg)

    final_report_html = comp.render_final_report(template_asset["content"], section_html_by_id)
    client.insert("geo_report_run_artifacts", [_artifact_row(
        client, run_id, execution_id, execution_method, "final_report", "final_report.html",
        final_report_html, "text/html")], prefer="return=minimal")

    client.update("geo_report_executions", {"id": f"eq.{execution_id}"}, {
        "status": "completed", "completed_at": _now_iso(),
    })
    canonical.set_canonical_execution(client, run_id, "report_edit", execution_id)

    return {
        "execution_id": execution_id, "edit_level": edit_level, "affected_section_ids": affected_ids,
        "final_report_html": final_report_html, "reason": edit_data.get("reason"),
        "warning": edit_data.get("warning"), "requires_prompt2_recomposition": False,
    }


# ---------------------------------------------------------------------------
# 履歴・Revert
# ---------------------------------------------------------------------------

def load_report_edit_history(client, run_id: str) -> list:
    """このRunのprompt2/report_edit Executionを、created_at昇順で返す
    （UIでのrevision一覧・現在のCanonicalの表示に使う）。各行に、その
    Executionが保存したreport_edit_instruction artifact(あれば)を付加する。"""
    run = _get_run(client, run_id)
    executions = _select_all_paginated(client, "geo_report_executions", {
        "select": "*", "run_id": f"eq.{run_id}",
    })
    relevant = sorted(
        (e for e in executions if e["stage"] in ("prompt2", "report_edit")),
        key=lambda e: e.get("created_at") or "")

    instruction_rows = _select_all_paginated(client, "geo_report_run_artifacts", {
        "select": "execution_id,content_text,storage_bucket,storage_path", "run_id": f"eq.{run_id}",
        "artifact_type": "eq.report_edit_instruction",
    })
    instruction_by_execution = {
        row["execution_id"]: json.loads(artifact_storage.read_artifact_content(client, row))
        for row in instruction_rows
    }

    canonical_report_execution_id = run.get("canonical_report_execution_id")
    canonical_prompt2_execution_id = run.get("canonical_prompt2_execution_id")
    result = []
    for e in relevant:
        result.append({
            "execution_id": e["id"], "stage": e["stage"], "status": e["status"],
            "created_at": e.get("created_at"), "parent_execution_id": e.get("parent_execution_id"),
            "edit_instruction": instruction_by_execution.get(e["id"]),
            "is_current_report": e["id"] == canonical_report_execution_id,
            "is_current_prompt2": e["id"] == canonical_prompt2_execution_id,
        })
    return result


def revert_report_to_execution(client, run_id: str, execution_id: str) -> dict:
    """既存のprompt2/report_edit Executionをcanonical_report_execution_idへ
    再指定する（破壊的上書きではなく、過去の版へ戻すだけ——そのExecutionの
    final_report artifactはそのまま残っている）。"""
    execution = _get_execution(client, execution_id)
    if execution["run_id"] != run_id:
        raise ValueError(f"execution_id={execution_id}はrun_id={run_id}のExecutionではありません。")
    if execution["stage"] not in ("prompt2", "report_edit"):
        raise ValueError(f"execution_id={execution_id}はprompt2/report_edit stageではありません。")
    return canonical.set_canonical_execution(client, run_id, execution["stage"], execution_id)
