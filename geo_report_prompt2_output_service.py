"""Report Studio「Design」領域: Current Prompt2 Canonical Output（G章、10ファイル）の
組み立て（Phase E）。

`geo_report_prompt2_aggregation_service`（集計）・`geo_report_prompt2_composition_service`
（Report Composition AI）・`geo_report_prompt1_output_service`（Continuity Change
Candidates、既にPrompt1側で生成済み）の結果を、G章で確定したCanonical Prompt2 Output
契約の10ファイルへ変換する。

report_brief.md/continuity_change_log.md/methodology_notes.mdは決定的テンプレート組み立て
（新規LLM呼び出しはしない）。continuity_context_next.jsonはPrompt1のContinuity Change
Candidatesとprevious_continuity_context.json（あれば）から構造化する。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

REFERENCE_EXAMPLES_USED_COLUMNS = [
    "example_id", "signal_id", "report_topic_label", "source_url", "author_or_source", "timestamp",
    "post_summary", "notable_phrase", "voice_direction", "role_in_report", "continuity_role",
]

_CONTINUITY_SCHEMA_VERSION = "1.2"

# ---------------------------------------------------------------------------
# evidence_intelligence_v1版のPrompt2 Output column schema（Phase H、2026-09-16追加）。
# v4.3（上記REFERENCE_EXAMPLES_USED_COLUMNS等、3-way sentiment構成比向け）とは
# 別の列構成——Evidence Landscape（QA/coverage専用）・Evidence Coverage Audit
# ・sec8_representative_evidenceのrequired_fieldsに揃えたreference examples。
# ---------------------------------------------------------------------------

EI_TREND_DATA_COLUMNS = [
    "period_start", "period_end", "period_label", "signal_id", "actor_category", "evidence_relation",
    "materiality", "evidence_count", "evidence_cluster_count", "hypothesis_ids_touched",
]

EI_DENOMINATOR_AUDIT_COLUMNS = [
    "signal_id", "raw_records", "nonduplicate_records", "cluster_label_joined_records",
    "sentiment_cluster_joined_records", "theme_relevant_records", "evidence_classified_records",
    "actor_category_distribution", "hypothesis_coverage", "ac_sc_join_warning_records",
    "excluded_irrelevant", "excluded_uncertain", "cluster_join_rate", "classification_coverage_rate",
    "qa_status", "qa_notes",
]

# sec8_representative_evidence.required_fields（evidence_sufficiency_status除く）と
# 1:1対応させた列名（timestamp→published_at, notable_phrase→quote_or_excerpt,
# signal_id→linked_signal_id等、Template側の呼称へ揃えた）。
EI_REFERENCE_EXAMPLES_USED_COLUMNS = [
    "example_id", "linked_signal_id", "report_topic_label", "source_url", "author_or_source",
    "published_at", "actor_category", "actor_name_or_actor_type", "evidence_type", "source_authority",
    "source_type", "source_name", "country_or_institution", "language_tag", "evidence_strength",
    "hypothesis_relevance", "linked_hypothesis_id", "supporting_or_contradicting_or_unexpected",
    "evidence_summary", "quote_or_excerpt", "reason_for_selection", "evidence_sufficiency_status",
]

# xius2 Templateのsec8_representative_evidence.required_fieldsそのまま（実データより）。
SEC8_REPRESENTATIVE_EVIDENCE_REQUIRED_FIELDS = [
    "signal_ids", "selected_evidence_per_signal", "evidence_type", "actor_name_or_actor_type",
    "actor_category", "country_or_institution", "source_type", "source_name", "language_tag",
    "published_at", "evidence_summary", "quote_or_excerpt", "hypothesis_relevance",
    "linked_hypothesis_id", "supporting_or_contradicting_or_unexpected", "linked_signal_id",
    "reason_for_selection",
]

# 上記required_fieldsのうち、reference_examples_used.csv（1行=1 example）の列として
# 1:1対応させない項目（Section全体の集約情報であり、個々の行には現れない）。
# signal_ids/selected_evidence_per_signalは「このSectionがどのSignalを扱うか」
# 「Signalごとにどの証拠を選んだか」というSection単位の情報であり、reference_
# examples_used.csv自体（採用された全exampleの集合）がその実体を表す。
# evidence_sufficiency_statusはSection単位の記入判断（Insufficient evidence等）で
# あり、個々のreference example行の列ではない。
SEC8_SECTION_LEVEL_ONLY_FIELDS = {"signal_ids", "selected_evidence_per_signal", "evidence_sufficiency_status"}

_EI_PROMPT2_SCHEMA_BY_FILE = {
    "biweekly_trend_data.csv": EI_TREND_DATA_COLUMNS,
    "weekly_trend_data.csv": EI_TREND_DATA_COLUMNS,
    "monthly_summary_data.csv": EI_TREND_DATA_COLUMNS,
    "denominator_audit.csv": EI_DENOMINATOR_AUDIT_COLUMNS,
    "reference_examples_used.csv": EI_REFERENCE_EXAMPLES_USED_COLUMNS,
}


def _csv_header(text: str) -> list:
    first_line = text.lstrip("﻿").splitlines()[0] if text.strip() else ""
    return [c.strip() for c in first_line.split(",")] if first_line else []


def validate_prompt2_output_schema(files: dict, prompt2_contract_version: str) -> list:
    """Prompt2 Output ZIP（{file_name: text}）を、prompt2_contract_versionに応じた
    契約で検証する（v4.3とevidence_intelligence_v1のschemaを混在させない）。
    戻り値は問題点の文字列リスト（空なら問題なし）。v4.3の場合は列検証しない
    （既存動作を変えない、ファイル名の存在確認のみが呼び出し元で行われる）。"""
    problems = []
    if prompt2_contract_version == "evidence_intelligence_v1":
        for file_name, expected_columns in _EI_PROMPT2_SCHEMA_BY_FILE.items():
            if file_name not in files:
                continue
            header = _csv_header(files[file_name])
            missing_cols = [c for c in expected_columns if c not in header]
            if missing_cols:
                problems.append(f"{file_name}にEI版必須列が不足: {missing_cols}")
    return problems


def validate_section_coverage(final_report_html: str, template_sections: list) -> dict:
    """final_report.htmlのdata-section-id/data-fieldマーカーを、Run-fixed Template
    （report_template.json相当の構造化データ、`template_sections`——report_template.html
    の再パースではない）のsection_id/required_fieldsと突き合わせる（2026-09-16追加、
    Phase H）。

    戻り値: {"missing_sections": [section_id...],
             "warnings": [{"section_id":..., "field":..., "reason": "missing"|"empty"}...]}
    missing_sectionsが非空の場合は呼び出し元がImportをRejectする。個別fieldの
    missing/emptyはwarningとして記録するのみ（Rejectしない）。"Insufficient evidence"
    等、何らかのテキストが記入されている場合はwarning対象外とする。"""
    from html.parser import HTMLParser

    class _SectionFieldParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.sections_found = set()
            self._current_section = None
            self._current_field = None
            self._field_text = {}
            self._depth_stack = []

        def handle_starttag(self, tag, attrs):
            attr_dict = dict(attrs)
            self._depth_stack.append((self._current_section, self._current_field))
            if "data-section-id" in attr_dict:
                self._current_section = attr_dict["data-section-id"]
                self.sections_found.add(self._current_section)
            if "data-field" in attr_dict and self._current_section:
                self._current_field = attr_dict["data-field"]
                self._field_text[(self._current_section, self._current_field)] = ""

        def handle_endtag(self, tag):
            if self._depth_stack:
                self._current_section, self._current_field = self._depth_stack.pop()

        def handle_data(self, data):
            if self._current_section and self._current_field:
                key = (self._current_section, self._current_field)
                self._field_text[key] = self._field_text.get(key, "") + data

    parser = _SectionFieldParser()
    parser.feed(final_report_html)

    missing_sections = []
    warnings = []
    for section in template_sections:
        section_id = section.get("section_id")
        if section_id not in parser.sections_found:
            missing_sections.append(section_id)
            continue
        for field in section.get("required_fields", []):
            key = (section_id, field)
            if key not in parser._field_text:
                warnings.append({"section_id": section_id, "field": field, "reason": "missing"})
            elif not parser._field_text[key].strip():
                warnings.append({"section_id": section_id, "field": field, "reason": "empty"})
    return {"missing_sections": missing_sections, "warnings": warnings}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# reference_examples_used.csv
# ---------------------------------------------------------------------------

def build_reference_examples_used_rows(used_public_voice_examples: list, *,
                                         role_by_example_id: dict = None) -> list:
    """used_public_voice_examples: geo_report_prompt1_output_service.
    build_public_voice_examples_rows()の出力のうち、実際にFinal Reportへ引用したもの
    （呼び出し元がexample_quotes Section等の生成結果から選び出す）。
    role_by_example_id: {example_id: role_in_report}（例: 'primary_quote', 'supporting_quote'）。
    未指定ならroleは空文字列。"""
    role_by_example_id = role_by_example_id or {}
    rows = []
    for ex in used_public_voice_examples:
        rows.append({
            "example_id": ex["example_id"], "signal_id": ex["signal_id"],
            "report_topic_label": ex["report_topic_label"], "source_url": ex.get("source_url", ""),
            "author_or_source": ex.get("author_or_source", ""), "timestamp": ex.get("timestamp", ""),
            "post_summary": ex.get("post_summary", ""), "notable_phrase": ex.get("notable_phrase", ""),
            "voice_direction": ex.get("voice_direction", ""),
            "role_in_report": role_by_example_id.get(ex["example_id"], ""),
            "continuity_role": ex.get("continuity_relevance", ""),
        })
    return rows


# ---------------------------------------------------------------------------
# continuity_context_next.json
# ---------------------------------------------------------------------------

_CONTINUITY_CHANGE_TYPE_TO_STATUS = {
    "New": "New", "Continuing": "Continuing", "Expanding": "Expanding", "Intensifying": "Intensifying",
    "Stable": "Stable", "Fragmenting": "Fragmenting", "Weakening": "Weakening",
    "Dormant candidate": "Dormant", "Reframed": "Reframed", "Contradictory": "Reframed",
    "Insufficient evidence": "Insufficient evidence",
}


def build_continuity_context_next(continuity_change_candidates: list, *,
                                    report_theme: str, current_period: str, previous_period: str = "",
                                    observation_cycle: str = "biweekly",
                                    analysis_start_date: str = "",
                                    previous_continuity_context: dict = None) -> dict:
    """continuity_change_candidates: geo_report_prompt1_output_service.
    generate_continuity_change_candidate()の結果list（1件=1トピック）。
    Plan B章のcontinuity_context schemaへマッピングする。前回Continuity
    （previous_continuity_context、あれば）のtopicのうち今回言及の無いものは
    fading_topics/resolved_or_dormant_topicsへ引き継ぐ（黙って消さない、Plan要件）。"""
    previous_continuity_context = previous_continuity_context or {}
    previous_topics_by_id = {}
    for bucket in ("ongoing_topics", "emerging_topics", "fading_topics"):
        for t in previous_continuity_context.get(bucket, []):
            previous_topics_by_id[t.get("topic_id")] = t

    ongoing, emerging, fading, resolved_or_dormant = [], [], [], []
    seen_previous_ids = set()

    for candidate in continuity_change_candidates:
        topic_id = candidate.get("matched_previous_topic_id") or f"T-{candidate['candidate_id']}"
        seen_previous_ids.add(candidate.get("matched_previous_topic_id"))
        change_type = candidate["candidate_change_type"]
        status = _CONTINUITY_CHANGE_TYPE_TO_STATUS.get(change_type, change_type)
        entry = {
            "topic_id": topic_id, "topic_label": candidate["report_topic_label"], "status": status,
            "current_assessment": candidate["current_evidence_summary"],
            "previous_assessment": (previous_topics_by_id.get(topic_id) or {}).get("current_assessment", ""),
            "change_from_previous": candidate["current_evidence_summary"],
            "change_basis": candidate["business_relevance"],
            "confidence": candidate["confidence"],
            "first_observed_period": (previous_topics_by_id.get(topic_id) or {}).get(
                "first_observed_period", current_period),
            "last_updated_period": current_period,
        }
        if change_type == "New":
            emerging.append(entry)
        elif change_type in ("Dormant candidate",):
            resolved_or_dormant.append(entry)
        elif change_type == "Weakening":
            fading.append(entry)
        else:
            ongoing.append(entry)

    # 前回はあったが今回言及されなかったトピックは、黙って消さずDormant扱いで引き継ぐ
    for topic_id, prev_entry in previous_topics_by_id.items():
        if topic_id in seen_previous_ids:
            continue
        carried = dict(prev_entry)
        carried["status"] = "Dormant"
        carried["last_updated_period"] = current_period
        resolved_or_dormant.append(carried)

    return {
        "schema_version": _CONTINUITY_SCHEMA_VERSION,
        "report_info": {
            "report_theme": report_theme, "current_period": current_period, "previous_period": previous_period,
            "observation_cycle": observation_cycle, "source_scope_summary": "",
            "query_continuity_status": "unchanged", "analysis_start_date": analysis_start_date,
            "primary_chart_granularity": "biweekly", "created_at": _now_iso(),
        },
        "baseline_assessment": previous_continuity_context.get("baseline_assessment", {}),
        "ongoing_topics": ongoing, "emerging_topics": emerging, "fading_topics": fading,
        "resolved_or_dormant_topics": resolved_or_dormant,
        "cross_topic_dynamics": previous_continuity_context.get("cross_topic_dynamics", []),
        "key_public_voice_patterns": previous_continuity_context.get("key_public_voice_patterns", []),
        "stakeholder_positions": previous_continuity_context.get("stakeholder_positions", []),
        "timeline": previous_continuity_context.get("timeline", []),
        "watch_conditions": previous_continuity_context.get("watch_conditions", []),
        "watch_keywords": previous_continuity_context.get("watch_keywords", []),
        "open_questions": previous_continuity_context.get("open_questions", []),
        "next_report_focus": [],
        "methodological_continuity_notes": [],
    }


def build_continuity_change_log_md(continuity_context_next: dict, *, has_baseline: bool) -> str:
    lines = ["# Continuity Change Log", ""]
    if not has_baseline:
        lines.append("- 初回Run（比較対象となる前回Continuityは無し、baseline_assessmentとして記録）")
    else:
        for topic in continuity_context_next.get("emerging_topics", []):
            lines.append(f"- [New] {topic['topic_label']}: {topic['change_from_previous']}")
        for topic in continuity_context_next.get("resolved_or_dormant_topics", []):
            lines.append(f"- [Dormant/Resolved] {topic['topic_label']}")
        for topic in continuity_context_next.get("fading_topics", []):
            lines.append(f"- [Weakening] {topic['topic_label']}: {topic['change_from_previous']}")
    lines.append("")
    lines.append(f"- 主要chart粒度: {continuity_context_next['report_info']['primary_chart_granularity']}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# report_brief.md / methodology_notes.md
# ---------------------------------------------------------------------------

def build_report_brief_md(section_composed_by_id: dict, *, executive_summary_section_id: str = None) -> str:
    """narrative_block（通常Executive Summary）の内容から短いBriefを組み立てる
    （新規LLM呼び出しはしない、既に生成済みのSection結果を再利用する）。"""
    lines = ["# Report Brief", ""]
    exec_composed = section_composed_by_id.get(executive_summary_section_id) if executive_summary_section_id else None
    if exec_composed:
        lines.append(f"- 中心結論: {exec_composed.get('central_conclusion', '')}")
        lines.append(f"- 前回からの変化: {exec_composed.get('change_from_previous', '')}")
        for driver in exec_composed.get("drivers", []):
            lines.append(f"  - Driver: {driver}")
        lines.append(f"- 確度: {exec_composed.get('confidence_note', '')}")
    else:
        lines.append("(Executive Summary Sectionが未生成のため、Briefは空です)")
    return "\n".join(lines) + "\n"


def build_methodology_notes_md(*, prompt1_contract_version: str, prompt2_contract_version: str,
                                 common_primary_graph_start: str, primary_graph_signals: list,
                                 model: str) -> str:
    lines = [
        "# Methodology Notes", "",
        f"- Prompt1 Contract Version: {prompt1_contract_version}",
        f"- Prompt2 Contract Version: {prompt2_contract_version}",
        f"- 3-way分母フィルタ: source_class=Public Voice, theme_relevance=relevant, "
        f"apply_to_cluster=true, attitude in (positive, neutral, negative), "
        f"is_duplicate=false, date>=common_primary_graph_start({common_primary_graph_start})",
        f"- 対象Primary Graph Signals: {', '.join(primary_graph_signals)}",
        f"- 集計粒度: 隔週次(主)・週次/月次(補助)、月曜起点の期間バケット化",
        f"- 使用モデル: {model}",
        "- 件数(n)はFinal Reportの視覚的表現には一切使用しない（denominator_audit.csv/",
        "  各trend csvへのみ記録）。",
    ]
    return "\n".join(lines) + "\n"


def serialize_continuity_context_next(continuity_context_next: dict) -> str:
    return json.dumps(continuity_context_next, ensure_ascii=False, indent=2)
