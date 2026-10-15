import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_prompt2_composition_service as comp  # noqa: E402


def _mock_azure_client(response_data: dict):
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(response_data)))],
    )
    return client


def test_prompt2_system_prompt_embeds_real_instruction_text():
    prompt = comp._prompt2_system_prompt()
    assert len(prompt) > 5000
    assert "HTML" in prompt or "html" in prompt.lower()


def test_compose_section_rejects_unknown_component_type():
    azure_client = _mock_azure_client({})
    section_def = {"section_id": "x", "title": "X", "component_type": "raw_html_dump", "required_fields": []}
    try:
        comp.compose_section(azure_client, "gpt-4o", section_def, {})
        assert False, "should have raised"
    except ValueError as exc:
        assert "raw_html_dump" in str(exc)


def test_compose_section_narrative_block():
    response = {"central_conclusion": "Support is stable", "change_from_previous": "No major shift",
                "drivers": ["economy", "trust"], "confidence_note": "Moderate confidence"}
    azure_client = _mock_azure_client(response)
    section_def = {"section_id": "exec-summary", "title": "Executive Summary",
                   "component_type": "narrative_block", "required_fields": ["central_conclusion"]}
    result = comp.compose_section(azure_client, "gpt-4o", section_def, {"some": "data"})
    assert result["data"]["central_conclusion"] == "Support is stable"


def test_render_narrative_block_escapes_html():
    composed = {"central_conclusion": "<script>alert(1)</script>", "change_from_previous": "x",
                "drivers": ["<b>d1</b>"], "confidence_note": "n"}
    html = comp.render_narrative_block(composed)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;b&gt;d1&lt;/b&gt;" in html


def test_render_metric_grid():
    composed = {"metrics": [{"label": "Retention", "value": "82%", "note": "stable"}]}
    html = comp.render_metric_grid(composed)
    assert "Retention" in html and "82%" in html and "stable" in html


def test_render_scenario_cards_includes_likelihood_class():
    composed = {"scenarios": [{"title": "Escalation", "likelihood": "High", "description": "d",
                                "business_implication": "b"}]}
    html = comp.render_scenario_cards(composed)
    assert 'class="card scenario high"' in html
    assert "Escalation" in html


def test_render_comparison_table():
    composed = {"columns": ["Theme", "Tone"], "rows": [["Economy", "Negative"], ["Trust", "Positive"]]}
    html = comp.render_comparison_table(composed)
    assert "<th>Theme</th>" in html
    assert "<td>Economy</td>" in html


def test_render_example_quotes():
    composed = {"quotes": [{"quote_text": "I support this", "voice_direction": "Positive",
                             "context_note": "supporter"}]}
    html = comp.render_example_quotes(composed)
    assert "I support this" in html
    assert 'class="quote positive"' in html


def test_render_data_limits_note():
    composed = {"limitations": ["Small sample in Signal S3"], "methodology_note": "See appendix"}
    html = comp.render_data_limits_note(composed)
    assert "Small sample in Signal S3" in html
    assert "See appendix" in html


def test_render_report_section_wraps_with_title_and_dispatches():
    section_def = {"section_id": "exec-summary", "title": "Executive Summary", "component_type": "narrative_block"}
    composed = {"central_conclusion": "c", "change_from_previous": "x", "drivers": [], "confidence_note": "n"}
    html = comp.render_report_section(section_def, composed)
    assert 'data-section-id="exec-summary"' in html
    assert "Executive Summary" in html
    assert "narrative-block" in html


def test_render_report_section_stacked_bar_chart_includes_chart_svg():
    section_def = {"section_id": "trend", "title": "Trend", "component_type": "stacked_bar_chart"}
    composed = {"interpretation": "Support is rising", "notable_shifts": ["Shift in week 3"]}
    html = comp.render_report_section(section_def, composed, chart_svg="<svg>CHART</svg>")
    assert "<svg>CHART</svg>" in html
    assert "Support is rising" in html


def test_render_final_report_assembles_sections_in_order_and_skips_missing():
    template = {
        "style_css": "<style>.x{}</style>",
        "shell_html": "<html><head>{style}</head><body>{sections}</body></html>",
        "sections": [
            {"section_id": "a", "order": 2, "title": "A", "component_type": "narrative_block"},
            {"section_id": "b", "order": 1, "title": "B", "component_type": "narrative_block"},
            {"section_id": "c", "order": 3, "title": "C", "component_type": "narrative_block"},
        ],
    }
    section_html = {"a": "<section>A-CONTENT</section>", "b": "<section>B-CONTENT</section>"}
    # cは未生成(セクションcが欠落) -> Final Reportから完全に除去される(Plan I.7)
    final_html = comp.render_final_report(template, section_html)
    assert "<style>.x{}</style>" in final_html
    assert final_html.index("B-CONTENT") < final_html.index("A-CONTENT")  # order順(b=1が先)
    assert "C-CONTENT" not in final_html


def test_render_final_report_injects_style_without_placeholder():
    template = {
        "style_css": "<style>.y{}</style>",
        "shell_html": "<html><head></head><body>{sections}</body></html>",
        "sections": [{"section_id": "a", "order": 1, "title": "A", "component_type": "narrative_block"}],
    }
    final_html = comp.render_final_report(template, {"a": "<section>A</section>"})
    assert "<style>.y{}</style>" in final_html
    assert "A" in final_html


def test_render_stacked_bar_chart_svg_has_no_visible_counts():
    import re
    rows = [
        {"period_label": "2026-05-01~2026-05-10", "positive_share": 0.5, "neutral_share": 0.3,
         "negative_share": 0.2, "positive_n": 50, "neutral_n": 30, "negative_n": 20},
    ]
    svg = comp.render_stacked_bar_chart_svg(rows)
    assert svg.startswith("<svg")
    assert "rect" in svg
    assert "2026-05-01~2026-05-10" in svg
    # レイアウト座標(x/y/width/height)に数字が出るのは当然なので、テキストノードの中身
    # （実際に画面へ表示される文字列）だけを取り出し、件数(n)そのものが表示されていないことを確認する
    # テキストノード集合が期間ラベル＋凡例3種のみであること（件数を表す独立したテキストが
    # 追加されていないこと）を厳密一致で確認する。期間ラベル自体は日付文字列なので偶然
    # "20"等の数字を含み得るため、部分一致ではなく集合の完全一致で検証する
    text_contents = re.findall(r"<text[^>]*>([^<]*)</text>", svg)
    assert text_contents == ["2026-05-01~2026-05-10", "Positive", "Neutral", "Negative"]


def test_render_stacked_bar_chart_svg_bar_widths_proportional_to_share():
    rows = [{"period_label": "p1", "positive_share": 1.0, "neutral_share": 0.0, "negative_share": 0.0}]
    svg = comp.render_stacked_bar_chart_svg(rows, width=100)
    assert 'width="100.0"' in svg
