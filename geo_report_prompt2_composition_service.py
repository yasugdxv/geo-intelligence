"""Report Studio「Design」領域: Current Prompt2 Report Composition AI（Phase E）。

Published Report Templateの`sections[]`定義（geo_report_project_assets_service.
TEMPLATE_COMPONENT_TYPES）に沿って、Section単位で構造化JSONを生成する
（`geo_llm_common.call_llm_structured`、1 Section=1コール）。AIはHTML/CSSを書かない
——Python Renderer（本モジュール）がcomponent_type別にHTML片を組み立てる
（kind-napping-hare.md I.3/I.4「Composition AIはHTMLを書かない」原則）。

system promptには実際のPrompt2 v4.3指示文の執筆原則（パイプライン用語の露出禁止、
証拠不足は「analytical uncertainty」として書く等）をそのまま埋め込む。
"""

from __future__ import annotations

import html as html_escape_module

import geo_llm_common
from geo_report_run_provisioning_service import load_prompt_instruction

_PROMPT2_SYSTEM_PREFIX = (
    "あなたはReport Studioの「Prompt 2 Report Composition AI」です。以下は、このReport"
    "作成作業の正式な指示文（Current Source of Truth）です。特に執筆原則・禁止事項"
    "（パイプライン用語の露出禁止、証拠不足は analytical uncertainty として書く等）に"
    "厳密に従い、出力は指定されたJSON Schemaに従ってください。HTMLやCSSは書かないこと"
    "——構造化データのみを返してください。\n\n"
)


def _prompt2_system_prompt() -> str:
    instruction = load_prompt_instruction("prompt2")["text"]
    return _PROMPT2_SYSTEM_PREFIX + instruction


def _e(value) -> str:
    """HTMLエスケープ（Python Rendererが唯一HTMLを組み立てる場所であるため、
    ここでのエスケープ漏れがXSS相当の問題に直結する）。"""
    return html_escape_module.escape(str(value) if value is not None else "")


# ---------------------------------------------------------------------------
# component_type別 Composition Schema（AIは構造化データのみ返す）
# ---------------------------------------------------------------------------

NARRATIVE_BLOCK_SCHEMA = {
    "type": "object",
    "properties": {
        "central_conclusion": {"type": "string"},
        "change_from_previous": {"type": "string"},
        "drivers": {"type": "array", "items": {"type": "string"}},
        "confidence_note": {"type": "string"},
    },
    "required": ["central_conclusion", "change_from_previous", "drivers", "confidence_note"],
    "additionalProperties": False,
}

METRIC_GRID_SCHEMA = {
    "type": "object",
    "properties": {
        "metrics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"label": {"type": "string"}, "value": {"type": "string"}, "note": {"type": "string"}},
                "required": ["label", "value", "note"], "additionalProperties": False,
            },
        },
    },
    "required": ["metrics"], "additionalProperties": False,
}

STACKED_BAR_CHART_SCHEMA = {
    "type": "object",
    "properties": {
        "interpretation": {"type": "string"},
        "notable_shifts": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["interpretation", "notable_shifts"], "additionalProperties": False,
}

SCENARIO_CARDS_SCHEMA = {
    "type": "object",
    "properties": {
        "scenarios": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"}, "likelihood": {"type": "string", "enum": ["High", "Medium", "Low"]},
                    "description": {"type": "string"}, "business_implication": {"type": "string"},
                },
                "required": ["title", "likelihood", "description", "business_implication"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["scenarios"], "additionalProperties": False,
}

COMPARISON_TABLE_SCHEMA = {
    "type": "object",
    "properties": {
        "columns": {"type": "array", "items": {"type": "string"}},
        "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
    },
    "required": ["columns", "rows"], "additionalProperties": False,
}

EXAMPLE_QUOTES_SCHEMA = {
    "type": "object",
    "properties": {
        "quotes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "quote_text": {"type": "string"}, "voice_direction": {"type": "string"},
                    "context_note": {"type": "string"},
                },
                "required": ["quote_text", "voice_direction", "context_note"], "additionalProperties": False,
            },
        },
    },
    "required": ["quotes"], "additionalProperties": False,
}

DATA_LIMITS_NOTE_SCHEMA = {
    "type": "object",
    "properties": {
        "limitations": {"type": "array", "items": {"type": "string"}},
        "methodology_note": {"type": "string"},
    },
    "required": ["limitations", "methodology_note"], "additionalProperties": False,
}

_COMPONENT_SCHEMAS = {
    "narrative_block": NARRATIVE_BLOCK_SCHEMA,
    "metric_grid": METRIC_GRID_SCHEMA,
    "stacked_bar_chart": STACKED_BAR_CHART_SCHEMA,
    "scenario_cards": SCENARIO_CARDS_SCHEMA,
    "comparison_table": COMPARISON_TABLE_SCHEMA,
    "example_quotes": EXAMPLE_QUOTES_SCHEMA,
    "data_limits_note": DATA_LIMITS_NOTE_SCHEMA,
}


def build_section_user_prompt(section_def: dict, python_data: dict) -> str:
    lines = [
        f"## Section: {section_def.get('title')} (section_id={section_def.get('section_id')})",
        f"component_type: {section_def.get('component_type')}",
        f"required_fields: {section_def.get('required_fields')}",
    ]
    example_markup = section_def.get("example_markup")
    if example_markup:
        lines.append(
            "参考: このSectionの過去の構造見本（スタイルの参考のみ。実データはここには含めない）:\n"
            f"{example_markup}"
        )
    lines.append("")
    lines.append("## Python側で計算済みのデータ（このSectionの根拠として使うこと）")
    lines.append(str(python_data))
    return "\n".join(lines)


def compose_section(azure_client, model: str, section_def: dict, python_data: dict, *,
                     max_attempts: int = 2) -> dict:
    """component_typeに応じたSchemaでcall_llm_structuredを1回呼ぶ。戻り値は
    call_llm_structuredの戻り値そのまま（{"data","mode","usage","latency_ms"}）。"""
    component_type = section_def.get("component_type")
    schema = _COMPONENT_SCHEMAS.get(component_type)
    if schema is None:
        raise ValueError(f"未対応のcomponent_typeです: {component_type}")
    user_prompt = build_section_user_prompt(section_def, python_data)
    return geo_llm_common.call_llm_structured(
        azure_client, model, _prompt2_system_prompt(), user_prompt, schema,
        f"geo_report_prompt2_section_{component_type}", max_attempts=max_attempts)


# ---------------------------------------------------------------------------
# Python Renderer（component_type別、AIはここに一切関与しない）
# ---------------------------------------------------------------------------

def render_narrative_block(composed: dict) -> str:
    drivers_html = "".join(f"<li>{_e(d)}</li>" for d in composed.get("drivers", []))
    return (
        '<div class="narrative-block">'
        f'<p class="central-conclusion">{_e(composed.get("central_conclusion"))}</p>'
        f'<p class="change-from-previous">{_e(composed.get("change_from_previous"))}</p>'
        f'<ul class="drivers">{drivers_html}</ul>'
        f'<p class="confidence-note">{_e(composed.get("confidence_note"))}</p>'
        '</div>'
    )


def render_metric_grid(composed: dict) -> str:
    cells = "".join(
        f'<div class="metric"><span class="label">{_e(m.get("label"))}</span>'
        f'<span class="value">{_e(m.get("value"))}</span>'
        f'<span class="note">{_e(m.get("note"))}</span></div>'
        for m in composed.get("metrics", [])
    )
    return f'<div class="metric-grid">{cells}</div>'


def render_stacked_bar_chart(composed: dict, *, chart_svg: str) -> str:
    shifts_html = "".join(f"<li>{_e(s)}</li>" for s in composed.get("notable_shifts", []))
    return (
        '<div class="stacked-bar-chart">'
        f'{chart_svg}'
        f'<p class="interpretation">{_e(composed.get("interpretation"))}</p>'
        f'<ul class="notable-shifts">{shifts_html}</ul>'
        '</div>'
    )


def render_scenario_cards(composed: dict) -> str:
    cards = "".join(
        f'<div class="card scenario {_e(s.get("likelihood", "").lower())}">'
        f'<div class="title">{_e(s.get("title"))}</div>'
        f'<div class="likelihood">{_e(s.get("likelihood"))}</div>'
        f'<div class="description">{_e(s.get("description"))}</div>'
        f'<div class="business-implication">{_e(s.get("business_implication"))}</div>'
        '</div>'
        for s in composed.get("scenarios", [])
    )
    return f'<div class="scenario-cards">{cards}</div>'


def render_comparison_table(composed: dict) -> str:
    header = "".join(f"<th>{_e(c)}</th>" for c in composed.get("columns", []))
    body = "".join(
        "<tr>" + "".join(f"<td>{_e(cell)}</td>" for cell in row) + "</tr>"
        for row in composed.get("rows", [])
    )
    return f'<table class="comparison-table"><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>'


def render_example_quotes(composed: dict) -> str:
    items = "".join(
        f'<blockquote class="quote {_e(q.get("voice_direction", "").lower())}">'
        f'<p>{_e(q.get("quote_text"))}</p><cite>{_e(q.get("context_note"))}</cite>'
        '</blockquote>'
        for q in composed.get("quotes", [])
    )
    return f'<div class="example-quotes">{items}</div>'


def render_data_limits_note(composed: dict) -> str:
    items = "".join(f"<li>{_e(item)}</li>" for item in composed.get("limitations", []))
    return (
        '<div class="data-limits-note">'
        f'<ul class="limitations">{items}</ul>'
        f'<p class="methodology-note">{_e(composed.get("methodology_note"))}</p>'
        '</div>'
    )


_RENDERERS = {
    "narrative_block": render_narrative_block,
    "metric_grid": render_metric_grid,
    "scenario_cards": render_scenario_cards,
    "comparison_table": render_comparison_table,
    "example_quotes": render_example_quotes,
    "data_limits_note": render_data_limits_note,
}


def render_report_section(section_def: dict, composed: dict, *, chart_svg: str = None) -> str:
    """component_type別にHTML片を組み立てる（AIはここに一切関与しない、決定的関数）。"""
    component_type = section_def.get("component_type")
    title_html = f'<h2 class="section-title">{_e(section_def.get("title"))}</h2>'
    if component_type == "stacked_bar_chart":
        body = render_stacked_bar_chart(composed, chart_svg=chart_svg or "")
    else:
        renderer = _RENDERERS.get(component_type)
        if renderer is None:
            raise ValueError(f"未対応のcomponent_typeです: {component_type}")
        body = renderer(composed)
    return f'<section class="report-section" data-section-id="{_e(section_def.get("section_id"))}">{title_html}{body}</section>'


def render_final_report(template_asset_content: dict, section_html_by_id: dict) -> str:
    """Templateのstyle_css＋shell_htmlに、Section順にレンダリング済みHTML片を差し込む。
    未対応/未生成のSectionはHTMLから完全に除去する（Plan I.7、未サポートslotをFinal Reportへ
    出さない）。"""
    sections = sorted(template_asset_content.get("sections", []), key=lambda s: s.get("order", 0))
    body = "".join(
        section_html_by_id[s["section_id"]] for s in sections if s["section_id"] in section_html_by_id
    )
    shell_html = template_asset_content.get("shell_html", "<html><body>{sections}</body></html>")
    style_css = template_asset_content.get("style_css", "")
    if "{style}" in shell_html:
        shell_html = shell_html.replace("{style}", style_css)
    elif style_css and "<style>" not in shell_html:
        shell_html = shell_html.replace("<head>", f"<head>{style_css}", 1) if "<head>" in shell_html \
            else style_css + shell_html
    return shell_html.replace("{sections}", body)


# ---------------------------------------------------------------------------
# 100%積み上げ棒チャート（SVG、n/件数を一切表示しない）
# ---------------------------------------------------------------------------

def render_stacked_bar_chart_svg(trend_rows: list, *, width: int = 640, bar_height: int = 28,
                                   gap: int = 10) -> str:
    """biweekly_trend_data相当のrowsから100%積み上げ棒（positive/neutral/negative）を
    SVGで生成する。n/件数は一切表示しない（Prompt2 §9.1-9.3の禁止事項）。"""
    colors = {"positive": "#2e7d32", "neutral": "#9e9e9e", "negative": "#c62828"}
    total_height = len(trend_rows) * (bar_height + gap) + gap
    bars = []
    for i, row in enumerate(trend_rows):
        y = gap + i * (bar_height + gap)
        x = 0.0
        for key in ("positive_share", "neutral_share", "negative_share"):
            share = row.get(key, 0.0) or 0.0
            bar_width = share * width
            color = colors[key.split("_")[0]]
            if bar_width > 0:
                bars.append(f'<rect x="{x:.1f}" y="{y}" width="{bar_width:.1f}" height="{bar_height}" '
                            f'fill="{color}"/>')
            x += bar_width
        label = _e(row.get("period_label", ""))
        bars.append(f'<text x="0" y="{y - 4}" font-size="11">{label}</text>')
    legend = (
        '<g class="legend" font-size="11">'
        f'<rect x="0" y="{total_height}" width="10" height="10" fill="{colors["positive"]}"/>'
        f'<text x="14" y="{total_height + 9}">Positive</text>'
        f'<rect x="90" y="{total_height}" width="10" height="10" fill="{colors["neutral"]}"/>'
        f'<text x="104" y="{total_height + 9}">Neutral</text>'
        f'<rect x="180" y="{total_height}" width="10" height="10" fill="{colors["negative"]}"/>'
        f'<text x="194" y="{total_height + 9}">Negative</text>'
        '</g>'
    )
    return (
        f'<svg viewBox="0 0 {width} {total_height + 24}" xmlns="http://www.w3.org/2000/svg">'
        f'{"".join(bars)}{legend}</svg>'
    )
