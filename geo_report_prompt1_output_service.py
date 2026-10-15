"""Report Studio「Design」領域: Current Prompt1 Canonical Output（E章、9ファイル）の
組み立て（Phase D後半）。

`geo_report_current_pipeline_service`（Preprocess/Cluster統計）と
`geo_report_prompt1_classification_service`（Analysis/Sentiment/Targeted Stance分類・
QA）の結果を、E章で確定したCurrent Prompt1 Output契約の9ファイルへ変換する。

Continuity Change Candidates（§5）・Narrative Master Update Candidates（§8.4）のみ
新規のLLM呼び出しを要する（1 Signal（またはTopic）1コール、既存パターンと同じ粒度）。
残り7ファイルは既存の分類・監査結果から決定的に組み立てる。
"""

from __future__ import annotations

import uuid

import geo_llm_common
from geo_report_prompt1_classification_service import _prompt1_system_prompt, exception_pattern_to_text

CLASSIFIED_CLUSTERS_COLUMNS = [
    "cluster_id", "signal_id", "report_topic_label", "final_topic_label", "source_assessment",
    "tone_gradient", "voice_direction", "report_use_status", "continuity_relevance",
    "matched_previous_topic_id", "change_signal_summary", "noise_flag", "noise_reason", "voice_summary",
    "notable_phrases", "representative_quality", "notes_for_prompt2",
]

PUBLIC_VOICE_EXAMPLES_COLUMNS = [
    "example_id", "cluster_id", "signal_id", "report_topic_label", "source_url", "author_or_source",
    "timestamp", "voice_direction", "tone_gradient", "post_summary", "notable_phrase", "why_representative",
    "continuity_relevance", "matched_previous_topic_id", "use_in_report", "targeted_stance_status",
    "primary_stance_target", "primary_stance_polarity", "primary_stance_confidence",
]

CONTINUITY_CHANGE_CANDIDATES_COLUMNS = [
    "candidate_id", "signal_id", "report_topic_label", "matched_previous_topic_id", "candidate_change_type",
    "current_evidence_summary", "representative_example_ids", "counter_evidence_summary",
    "business_relevance", "confidence", "caution_for_prompt2",
]

NARRATIVE_MASTER_UPDATE_CANDIDATES_COLUMNS = [
    "signal_id", "report_topic_label", "current_narrative", "suggested_narrative", "add_include_keywords",
    "add_required_context", "add_exclude_keywords", "add_cluster_split_terms", "reason", "priority",
]

SENTIMENT_CLUSTER_CLASSIFIED_COLUMNS = [
    "sentiment_cluster_id", "theme_relevance", "source_class", "reaction_type", "attitude",
    "cluster_consistency", "apply_to_cluster", "exception_pattern", "classification_reason",
    "targeted_stance_status", "targeted_stance_count",
]

TARGETED_STANCE_CLASSIFIED_COLUMNS = [
    "stance_id", "sentiment_cluster_id", "signal_id", "target_type", "target_name", "target_normalized_id",
    "stance_polarity", "stance_strength", "stance_confidence", "stance_evidence", "stance_basis",
    "stance_cluster_consistency", "apply_stance_to_cluster", "stance_exception_pattern", "stance_reason",
]

PROMPT1_QUALITY_AUDIT_COLUMNS = [
    "signal_id", "total_eligible_records", "preliminary_public_voice_candidates", "final_theme_relevant_records",
    "final_public_voice_records", "final_threeway_sentiment_records", "excluded_media_official_corporate",
    "excluded_fact_only", "excluded_irrelevant", "mixed_unknown_records", "fallback_records_total",
    "fallback_records_included", "fallback_records_excluded", "classification_coverage_rate",
    "public_voice_retention_rate", "targeted_stance_available_records", "targeted_stance_directional_records",
    "targeted_stance_high_medium_records", "targeted_stance_low_confidence_records",
    "targeted_stance_ambiguous_records", "targeted_stance_coverage_rate", "targeted_stance_usable_rate",
    "qa_status", "qa_notes",
]

# ---------------------------------------------------------------------------
# evidence_intelligence_v1版のcolumn schema（Phase H、2026-09-16追加）。
# v4.3（上記CLASSIFIED_CLUSTERS_COLUMNS等、tone_gradient/voice_direction基準）とは
# 別の列構成——Actor/Position/Bargaining基準。import_prompt1_result（ChatGPT取り込み）が
# 保存前にヘッダ検証するためのみに使う（v4.3側は既存Application AI再構成関数が
# 別途この列を使って組み立てるが、EI側はChatGPT出力をそのまま保存するため、ここでは
# 「検証専用」の定数として定義する）。
# ---------------------------------------------------------------------------

EI_CLASSIFIED_CLUSTERS_COLUMNS = [
    "cluster_id", "signal_id", "report_topic_label", "actor_category", "actor_name", "evidence_type",
    "source_authority", "evidence_directness", "position_summary", "objective", "incentive", "constraint",
    "bargaining_position", "red_line", "hypothesis_relevance", "evidence_relation", "evidence_strength",
    "materiality", "report_use_status", "reaction", "attitude", "notes_for_prompt2",
]

EI_SENTIMENT_CLUSTER_CLASSIFIED_COLUMNS = [
    "sentiment_cluster_id", "theme_relevance", "actor_category", "evidence_relation", "cluster_consistency",
    "apply_to_cluster", "exception_pattern", "classification_reason",
]

DISCOVERY_CANDIDATES_CLASSIFIED_COLUMNS = [
    "discovery_cluster_id", "signal_id", "cluster_size", "dominant_tokens", "source_types", "topic_label",
    "topic_summary", "key_actors", "existing_signal_overlap", "linked_signal_ids", "why_new_or_different",
    "potential_H1_H2_H3_effect", "business_relevance", "materiality", "confidence",
]

DISCOVERY_EVIDENCE_EXAMPLES_COLUMNS = [
    "example_id", "discovery_cluster_id", "signal_id", "topic_label", "source_url", "author_or_source",
    "timestamp", "post_summary", "notable_phrase", "why_representative", "existing_signal_overlap",
    "linked_signal_ids", "materiality", "confidence",
]

DISCOVERY_OUTPUT_CONDITIONAL_FILES = ["discovery_candidates_classified.csv", "discovery_evidence_examples.csv"]


def _csv_header(text: str) -> list:
    """CSVテキストの1行目（ヘッダ）だけを列名リストとして返す。"""
    first_line = text.lstrip("﻿").splitlines()[0] if text.strip() else ""
    return [c.strip() for c in first_line.split(",")] if first_line else []


def validate_prompt1_output_schema(files: dict, prompt1_contract_version: str, *,
                                     discovery_expected: bool = False) -> list:
    """Prompt1 Output ZIP（{file_name: text}）を、prompt1_contract_versionに応じた
    契約で検証する（Import時のContract Validation、DB保存前に呼ぶ）。
    戻り値は問題点の文字列リスト（空なら問題なし）。9 Required filesの存在チェック
    自体は呼び出し元（import_prompt1_result）が既に行うため、ここでは列レベルの
    検証のみを行う。

    v4.3とevidence_intelligence_v1のschemaを混在させない——各versionが期待する
    列定義のみを検証する。discovery_expected=Trueの場合のみdiscovery系
    Conditionalファイルの存在・schemaも検証する。"""
    problems = []

    if prompt1_contract_version == "evidence_intelligence_v1":
        if "classified_clusters.csv" in files:
            header = _csv_header(files["classified_clusters.csv"])
            if "evidence_directness" not in header:
                problems.append("classified_clusters.csvにevidence_directness列がありません"
                                 "（evidence_intelligence_v1では必須）")
        if "sentiment_cluster_classified.csv" in files:
            header = _csv_header(files["sentiment_cluster_classified.csv"])
            missing_cols = [c for c in EI_SENTIMENT_CLUSTER_CLASSIFIED_COLUMNS if c not in header]
            if missing_cols:
                problems.append(f"sentiment_cluster_classified.csvにEI版必須列が不足: {missing_cols}")

        if discovery_expected:
            missing_conditional = [f for f in DISCOVERY_OUTPUT_CONDITIONAL_FILES if f not in files]
            if missing_conditional:
                problems.append(f"discovery_mode Signalがあるのに存在しないファイル: {missing_conditional}")
            if "discovery_candidates_classified.csv" in files:
                header = _csv_header(files["discovery_candidates_classified.csv"])
                missing_cols = [c for c in DISCOVERY_CANDIDATES_CLASSIFIED_COLUMNS if c not in header]
                if missing_cols:
                    problems.append(f"discovery_candidates_classified.csvに必須列が不足: {missing_cols}")
            if "discovery_evidence_examples.csv" in files:
                header = _csv_header(files["discovery_evidence_examples.csv"])
                missing_cols = [c for c in DISCOVERY_EVIDENCE_EXAMPLES_COLUMNS if c not in header]
                if missing_cols:
                    problems.append(f"discovery_evidence_examples.csvに必須列が不足: {missing_cols}")

    return problems


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# 1. classified_clusters.csv
# ---------------------------------------------------------------------------

def build_classified_clusters_rows(analysis_cluster_rows: list, analysis_classifications: dict) -> list:
    """analysis_cluster_rows: geo_report_current_pipeline_service.build_cluster_labeling_rows()
    の出力。analysis_classifications: {cluster_id: §3分類結果dict（LLM出力そのまま）}。
    分類結果が無いクラスタ（未実行）は含めない——Canonical Outputは分類済みクラスタのみ。"""
    rows = []
    for cluster_row in analysis_cluster_rows:
        cluster_id = cluster_row["cluster_id"]
        cls = analysis_classifications.get(cluster_id)
        if cls is None:
            continue
        rows.append({
            "cluster_id": cluster_id, "signal_id": cluster_row["signal_id"],
            "report_topic_label": cluster_row["report_topic_label"],
            "final_topic_label": cls["final_topic_label"], "source_assessment": cls["source_assessment"],
            "tone_gradient": cls["tone_gradient"], "voice_direction": cls["voice_direction"],
            "report_use_status": cls["report_use_status"], "continuity_relevance": cls["continuity_relevance"],
            "matched_previous_topic_id": cls.get("matched_previous_topic_id") or "",
            "change_signal_summary": cls["change_signal_summary"], "noise_flag": cls["noise_flag"],
            "noise_reason": cls["noise_reason"], "voice_summary": cls["voice_summary"],
            "notable_phrases": cls["notable_phrases"], "representative_quality": cls["representative_quality"],
            "notes_for_prompt2": cls["notes_for_prompt2"],
        })
    return rows


# ---------------------------------------------------------------------------
# 2. public_voice_examples.csv（§6の代表投稿選定、Prompt2への候補プール）
# ---------------------------------------------------------------------------

def _best_stance_for_cluster(stances: list) -> dict:
    """confidence順(high>medium>low)で最上位のTargeted Stanceを1件返す（無ければNone）。"""
    if not stances:
        return None
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(stances, key=lambda s: order.get(s.get("stance_confidence"), 3))[0]


def build_public_voice_examples_rows(analysis_cluster_rows: list, analysis_classifications: dict,
                                       representatives_by_cluster: dict,
                                       targeted_stance_by_cluster: dict = None, *,
                                       max_examples_per_cluster: int = 3) -> list:
    """report_use_status='exclude'のクラスタは候補に含めない。それ以外は代表投稿を
    最大max_examples_per_cluster件、候補プールとして含める（Prompt2側で最終3-5件へ絞る
    ため、ここでは絞り込みすぎない）。"""
    targeted_stance_by_cluster = targeted_stance_by_cluster or {}
    rows = []
    for cluster_row in analysis_cluster_rows:
        cluster_id = cluster_row["cluster_id"]
        cls = analysis_classifications.get(cluster_id)
        if cls is None or cls.get("report_use_status") == "exclude":
            continue
        reps = representatives_by_cluster.get(cluster_id, [])[:max_examples_per_cluster]
        best_stance = _best_stance_for_cluster(targeted_stance_by_cluster.get(cluster_id, []))
        for rep in reps:
            rows.append({
                "example_id": _new_id("PVE"), "cluster_id": cluster_id, "signal_id": cluster_row["signal_id"],
                "report_topic_label": cluster_row["report_topic_label"], "source_url": rep.get("url", ""),
                "author_or_source": rep.get("author") or rep.get("source_name") or "",
                "timestamp": rep.get("published_at", ""), "voice_direction": cls["voice_direction"],
                "tone_gradient": cls["tone_gradient"], "post_summary": rep.get("text_for_review", ""),
                "notable_phrase": cls["notable_phrases"], "why_representative": cls["representative_quality"],
                "continuity_relevance": cls["continuity_relevance"],
                "matched_previous_topic_id": cls.get("matched_previous_topic_id") or "",
                "use_in_report": cls.get("report_use_status") == "include",
                "targeted_stance_status": "available" if best_stance else "unavailable",
                "primary_stance_target": (best_stance or {}).get("target_name", ""),
                "primary_stance_polarity": (best_stance or {}).get("stance_polarity", ""),
                "primary_stance_confidence": (best_stance or {}).get("stance_confidence", ""),
            })
    return rows


# ---------------------------------------------------------------------------
# 3. sentiment_cluster_classified.csv / 4. targeted_stance_classified.csv
# ---------------------------------------------------------------------------

def build_sentiment_cluster_classified_rows(sentiment_classifications: dict,
                                              targeted_stance_by_cluster: dict = None) -> list:
    targeted_stance_by_cluster = targeted_stance_by_cluster or {}
    rows = []
    for cluster_id, cls in sentiment_classifications.items():
        stances = targeted_stance_by_cluster.get(cluster_id, [])
        if not stances:
            status = "not_applicable"
        elif any(s.get("stance_confidence") in ("high", "medium") for s in stances):
            status = "available"
        else:
            status = "ambiguous"
        rows.append({
            "sentiment_cluster_id": cluster_id, "theme_relevance": cls["theme_relevance"],
            "source_class": cls["source_class"], "reaction_type": cls["reaction_type"],
            "attitude": cls["attitude"], "cluster_consistency": cls["cluster_consistency"],
            "apply_to_cluster": cls["apply_to_cluster"],
            "exception_pattern": exception_pattern_to_text(cls.get("exception_pattern", [])),
            "classification_reason": cls["classification_reason"],
            "targeted_stance_status": status, "targeted_stance_count": len(stances),
        })
    return rows


def build_targeted_stance_classified_rows(targeted_stance_by_cluster: dict, signal_by_cluster: dict) -> list:
    """targeted_stance_by_cluster: {sentiment_cluster_id: [stance dict,...]}。
    signal_by_cluster: {sentiment_cluster_id: signal_id}（cluster統計から引く）。
    0件でもヘッダのみ出力できるよう、空listを返すことを許容する（呼び出し元がCSV化する）。"""
    rows = []
    for cluster_id, stances in targeted_stance_by_cluster.items():
        signal_id = signal_by_cluster.get(cluster_id, "")
        for stance in stances:
            rows.append({
                "stance_id": _new_id("TS"), "sentiment_cluster_id": cluster_id, "signal_id": signal_id,
                "target_type": stance["target_type"], "target_name": stance["target_name"],
                "target_normalized_id": stance.get("target_normalized_id") or "",
                "stance_polarity": stance["stance_polarity"], "stance_strength": stance["stance_strength"],
                "stance_confidence": stance["stance_confidence"], "stance_evidence": stance["stance_evidence"],
                "stance_basis": stance["stance_basis"],
                "stance_cluster_consistency": stance["stance_cluster_consistency"],
                "apply_stance_to_cluster": stance["apply_stance_to_cluster"],
                "stance_exception_pattern": stance.get("stance_exception_pattern", ""),
                "stance_reason": stance["stance_reason"],
            })
    return rows


# ---------------------------------------------------------------------------
# 5. continuity_change_candidates.csv（§5、LLM 1コール/Topic）
# ---------------------------------------------------------------------------

CONTINUITY_CANDIDATE_SCHEMA_NAME = "geo_report_prompt1_continuity_change_candidate"

_CONTINUITY_CHANGE_TYPES = [
    "New", "Continuing", "Expanding", "Intensifying", "Stable", "Fragmenting", "Weakening",
    "Dormant candidate", "Reframed", "Contradictory", "Insufficient evidence",
]

CONTINUITY_CANDIDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidate_change_type": {"type": "string", "enum": _CONTINUITY_CHANGE_TYPES},
        "current_evidence_summary": {"type": "string"},
        "counter_evidence_summary": {"type": "string"},
        "business_relevance": {"type": "string"},
        "confidence": {"type": "string", "enum": ["High", "Medium", "Low"]},
        "caution_for_prompt2": {"type": "string"},
    },
    "required": ["candidate_change_type", "current_evidence_summary", "counter_evidence_summary",
                 "business_relevance", "confidence", "caution_for_prompt2"],
    "additionalProperties": False,
}


def build_continuity_candidate_user_prompt(signal_id: str, report_topic_label: str,
                                             topic_classified_clusters: list,
                                             previous_topic_entry: dict = None) -> str:
    lines = [
        "## §5 Continuity変化候補の判定対象",
        f"signal_id: {signal_id}", f"report_topic_label: {report_topic_label}",
        f"今回Runでのこのトピックの分類結果クラスタ数: {len(topic_classified_clusters)}",
    ]
    for c in topic_classified_clusters:
        lines.append(
            f"- final_topic_label: {c.get('final_topic_label')} / voice_direction: {c.get('voice_direction')} / "
            f"tone_gradient: {c.get('tone_gradient')} / voice_summary: {c.get('voice_summary')}"
        )
    if previous_topic_entry:
        lines.append("")
        lines.append(f"## 前回Continuityの該当トピック（比較対象）\n{previous_topic_entry}")
    else:
        lines.append("")
        lines.append("前回Continuityにこのトピックへ対応する記録はありません（New候補の可能性を検討）。")
    return "\n".join(lines)


def generate_continuity_change_candidate(azure_client, model: str, signal_id: str, report_topic_label: str,
                                           topic_classified_clusters: list, *,
                                           matched_previous_topic_id: str = None,
                                           previous_topic_entry: dict = None,
                                           representative_example_ids: list = None,
                                           max_attempts: int = 2) -> dict:
    user_prompt = build_continuity_candidate_user_prompt(
        signal_id, report_topic_label, topic_classified_clusters, previous_topic_entry)
    result = geo_llm_common.call_llm_structured(
        azure_client, model, _prompt1_system_prompt(), user_prompt, CONTINUITY_CANDIDATE_SCHEMA,
        CONTINUITY_CANDIDATE_SCHEMA_NAME, max_attempts=max_attempts)
    data = result["data"]
    return {
        "candidate_id": _new_id("CC"), "signal_id": signal_id, "report_topic_label": report_topic_label,
        "matched_previous_topic_id": matched_previous_topic_id or "",
        "candidate_change_type": data["candidate_change_type"],
        "current_evidence_summary": data["current_evidence_summary"],
        "representative_example_ids": "|".join(representative_example_ids or []),
        "counter_evidence_summary": data["counter_evidence_summary"],
        "business_relevance": data["business_relevance"], "confidence": data["confidence"],
        "caution_for_prompt2": data["caution_for_prompt2"],
    }


def group_classified_clusters_by_topic(classified_cluster_rows: list) -> dict:
    """classified_clusters.csv行を(signal_id, report_topic_label)単位でグルーピングする
    （§5/§8.4呼び出しの単位、1トピック=1LLMコール）。"""
    groups: dict = {}
    for row in classified_cluster_rows:
        key = (row["signal_id"], row["report_topic_label"])
        groups.setdefault(key, []).append(row)
    return groups


# ---------------------------------------------------------------------------
# 6. narrative_master_update_candidates.csv（§8.4、LLM 1コール/Signal）
# ---------------------------------------------------------------------------

NARRATIVE_MASTER_UPDATE_SCHEMA_NAME = "geo_report_prompt1_narrative_master_update_candidate"

NARRATIVE_MASTER_UPDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "report_topic_label": {"type": "string"},
                    "current_narrative": {"type": "string"},
                    "suggested_narrative": {"type": "string"},
                    "add_include_keywords": {"type": "string"},
                    "add_required_context": {"type": "string"},
                    "add_exclude_keywords": {"type": "string"},
                    "add_cluster_split_terms": {"type": "string"},
                    "reason": {"type": "string"},
                    "priority": {"type": "string", "enum": ["High", "Medium", "Low"]},
                },
                "required": ["report_topic_label", "current_narrative", "suggested_narrative",
                             "add_include_keywords", "add_required_context", "add_exclude_keywords",
                             "add_cluster_split_terms", "reason", "priority"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["candidates"],
    "additionalProperties": False,
}


def build_narrative_master_update_user_prompt(signal_id: str, audit_row: dict,
                                                fallback_heavy_clusters: list) -> str:
    lines = [
        "## §8.4 Narrative Master更新候補の判定対象（このSignalのみ、必ず候補のみ・自動適用しない）",
        f"signal_id: {signal_id}",
        f"fallback_share等の監査サマリ: {audit_row}",
        "fallback比率が高い、またはnoise_flag=trueだったクラスタ:",
    ]
    for c in fallback_heavy_clusters:
        lines.append(
            f"- final_topic_label: {c.get('final_topic_label')} / noise_reason: {c.get('noise_reason')} / "
            f"notes_for_prompt2: {c.get('notes_for_prompt2')}"
        )
    if not fallback_heavy_clusters:
        lines.append("(該当クラスタなし。改善候補が無ければcandidatesは空配列で構いません)")
    return "\n".join(lines)


def generate_narrative_master_update_candidates(azure_client, model: str, signal_id: str, audit_row: dict,
                                                   fallback_heavy_clusters: list, *,
                                                   max_attempts: int = 2) -> list:
    user_prompt = build_narrative_master_update_user_prompt(signal_id, audit_row, fallback_heavy_clusters)
    result = geo_llm_common.call_llm_structured(
        azure_client, model, _prompt1_system_prompt(), user_prompt, NARRATIVE_MASTER_UPDATE_SCHEMA,
        NARRATIVE_MASTER_UPDATE_SCHEMA_NAME, max_attempts=max_attempts)
    return [{"signal_id": signal_id, **c} for c in result["data"]["candidates"]]


# ---------------------------------------------------------------------------
# 7. prompt1_summary.md / 8. classification_rule_applied.md（決定的テンプレート組み立て）
# ---------------------------------------------------------------------------

def build_prompt1_summary_md(audit_rows: list, classified_cluster_rows: list,
                               narrative_master_update_candidates: list) -> str:
    all_row = next((r for r in audit_rows if r["signal_id"] == "ALL"), {})
    lines = [
        "# Prompt1 Summary", "",
        f"- 対象Signal数: {len({r['signal_id'] for r in audit_rows if r['signal_id'] != 'ALL'})}",
        f"- 分類済みAnalysis Cluster数: {len(classified_cluster_rows)}",
        f"- 全体classification_coverage_rate: {all_row.get('classification_coverage_rate', 'N/A')}",
        f"- 全体public_voice_retention_rate: {all_row.get('public_voice_retention_rate', 'N/A')}",
        f"- targeted_stance_usable_rate: {all_row.get('targeted_stance_usable_rate', 'N/A')}",
        f"- qa_status: {all_row.get('qa_status', 'N/A')}",
        f"- Narrative Master更新候補件数: {len(narrative_master_update_candidates)}",
        "",
        "## REVIEW REQUIREDのSignal",
    ]
    review_required = [r["signal_id"] for r in audit_rows if r.get("qa_status") == "REVIEW REQUIRED"]
    lines.append(", ".join(review_required) if review_required else "なし")
    return "\n".join(lines) + "\n"


def build_classification_rule_applied_md(prompt1_contract_version: str,
                                           applied_exceptions: list = None) -> str:
    lines = [
        "# Classification Rule Applied", "",
        f"- 適用Prompt1 Contract Version: {prompt1_contract_version}",
        "- 分類はCurrent Source of Truth（resources/report_studio/prompt1/）の指示文に厳密に従った。",
    ]
    if applied_exceptions:
        lines.append("")
        lines.append("## 個別の例外対応")
        for note in applied_exceptions:
            lines.append(f"- {note}")
    return "\n".join(lines) + "\n"
