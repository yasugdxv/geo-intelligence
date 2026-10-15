"""Report Studio「Design」領域: Current Prompt1 v4.3 Application AI（Phase D後半）。

Analysis Cluster分類（§3）・Sentiment Cluster分類（§4、11列）・Targeted Stance（§4.5）を
`geo_llm_common.call_llm_structured`で1クラスタ1コールずつ行う（既存
`geo_report_classification_service.classify_cluster`と同じ「1呼び出し=1判断単位」の粒度）。
system promptには実際のPrompt1 v4.3指示文（resources/report_studio/prompt1/v4.3/
instruction.md）をそのまま埋め込む——要約・言い換えでルールを劣化させないため
（ChatGPT Fallback経路が使うのと同じ指示文をApplication AI経路にも使う、
kind-napping-hare.md I.3のComposition ADと同じ思想）。

QA（§7の集計、prompt1_quality_audit.csv相当）はLLMを使わない決定的計算
（`compute_prompt1_quality_audit`）。Continuity Change Candidates（§5）・Narrative
Master Update Candidates（§8.4）は別途追加予定（未実装、Phase D後続）。
"""

from __future__ import annotations

import geo_llm_common
from geo_report_run_provisioning_service import load_prompt_instruction

_PROMPT1_SYSTEM_PREFIX = (
    "あなたはReport Studioの「Prompt 1 Application AI」です。以下は、この分類作業の"
    "正式な指示文（Current Source of Truth）です。この指示文のルール・enum定義・"
    "判定順序に厳密に従い、出力は指定されたJSON Schemaに従ってください。\n\n"
)


def _prompt1_system_prompt() -> str:
    instruction = load_prompt_instruction("prompt1")["text"]
    return _PROMPT1_SYSTEM_PREFIX + instruction


# ---------------------------------------------------------------------------
# バッチ分類（複数クラスタを1回のLLM呼び出しでまとめて処理する、2026-09-11）。
#
# 従来は1クラスタ=1回のLLM呼び出し（かつ毎回Prompt1指示文全文を再送信）だったため、
# Analysis Cluster/Sentiment Clusterとも万単位の実データ規模でChatGPT一括処理（数分）に
# 対して数時間かかっていた。Prompt1 v4.3の分類ルール自体は変えず、実行方式のみを
# 「バッチ内の複数クラスタを1回の呼び出しでまとめて判定する」方式へ変更する。
# バッチ内の各クラスタは完全に独立して判定させる（他クラスタの根拠を混同させない）。
# ---------------------------------------------------------------------------

_BATCH_INDEPENDENCE_CONSTRAINTS = (
    "\n\n## Batch Execution Constraints (must follow exactly)\n"
    "You will receive multiple clusters in a single request, each under its own heading with "
    "its own cluster_id. For every cluster, apply the same classification rules above, but:\n"
    "- Evaluate every cluster independently. Never transfer evidence, sentiment, source class, "
    "target, stance, or interpretation from one cluster to another.\n"
    "- Return exactly one result for every supplied cluster_id.\n"
    "- Do not merge clusters. Do not omit clusters. Do not invent cluster_ids.\n"
    "- Output must be a single JSON object of the form "
    '{"results": [ {"cluster_id": "...", ...fields...}, ... ]} '
    "with exactly one entry per input cluster_id, in any order.\n"
)


def _prompt1_batch_system_prompt() -> str:
    return _prompt1_system_prompt() + _BATCH_INDEPENDENCE_CONSTRAINTS


def _loosen_schema(node):
    """Schema木を再帰的に緩める（enum/required/additionalProperties制約を外す）。
    フィールド名・型は保持する。実機検証（2026-09-11）で、バッチ呼び出し用schemaの
    propertiesにcluster_idしか書かなかった場合、Azure構造化出力が14項目中5項目しか
    生成しないという不具合が確認された——json_schemaのproperties一覧自体が
    「何を生成すべきか」の主要なガイドになっており、strict=Falseでも無視されない。
    そのため各batch item schemaは元の厳密schemaと同じ全フィールド名・型を残しつつ、
    required/additionalPropertiesだけを緩める。

    enumは単純に削除すると、その削除自体が「厳密な値制約が無くなった」というガイド
    低下になり、実機検証で本来High/Medium/Lowを返すべきフィールドに長い説明文を
    書いてしまう不具合が確認された。そのためenumは削除ではなく`description`への
    自然文ヒント（"must be exactly one of: ..."）へ変換する——jsonschemaライブラリは
    descriptionをバリデーション対象にしないため、Azure/OpenAIへの生成ガイドとしての
    効力は保ちつつ、1〜2件の軽微な逸脱でバッチ全体の構造化出力検証が失敗する
    ことは避けられる（最終的な厳密検証は呼び出し元のgeo_llm_common.
    reconcile_batch_llm_resultsが元の厳密schema・実enumで個別に行う）。"""
    if not isinstance(node, dict):
        return node
    node = dict(node)
    enum_values = node.pop("enum", None)
    if enum_values:
        hint = f"must be exactly one of: {', '.join(str(v) for v in enum_values)}"
        existing_description = node.get("description", "")
        node["description"] = f"{existing_description} ({hint})".strip()
    node.pop("additionalProperties", None)
    node.pop("required", None)
    if "properties" in node:
        node["properties"] = {k: _loosen_schema(v) for k, v in node["properties"].items()}
    if "items" in node:
        node["items"] = _loosen_schema(node["items"])
    return node


def _batch_item_schema_from(strict_item_schema: dict) -> dict:
    """1件分の厳密schema（例: ANALYSIS_CLUSTER_CLASSIFICATION_SCHEMA）から、
    バッチ呼び出し用のitem schema（cluster_id必須＋他は緩め）を導出する。"""
    loosened = _loosen_schema(strict_item_schema)
    loosened["properties"] = {"cluster_id": {"type": "string"}, **loosened.get("properties", {})}
    loosened["required"] = ["cluster_id"]
    return loosened


def _batch_results_schema(item_schema: dict) -> dict:
    return {
        "type": "object",
        "properties": {"results": {"type": "array", "items": item_schema}},
        "required": ["results"],
        "additionalProperties": False,
    }


def _format_representatives(representatives: list) -> str:
    lines = ["## クラスタ内の代表投稿（representatives）"]
    for rep in representatives:
        lines.append(
            f"- [record_id: {rep.get('record_id')}] {rep.get('title') or ''}\n"
            f"  出典: {rep.get('source_name') or ''} / 種別: {rep.get('source_bucket') or ''} / "
            f"言語: {rep.get('language_group') or ''} / match_status: {rep.get('match_status') or ''}\n"
            f"  本文: {rep.get('text_for_review') or ''}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# §3 Analysis Cluster Classification
# ---------------------------------------------------------------------------

ANALYSIS_CLUSTER_SCHEMA_NAME = "geo_report_prompt1_analysis_cluster_classification"

ANALYSIS_CLUSTER_CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "final_topic_label": {"type": "string"},
        "source_assessment": {"type": "string"},
        "tone_gradient": {"type": "string"},
        "voice_direction": {"type": "string"},
        "report_use_status": {"type": "string"},
        "continuity_relevance": {"type": "string"},
        "matched_previous_topic_id": {"type": ["string", "null"]},
        "change_signal_summary": {"type": "string"},
        "noise_flag": {"type": "boolean"},
        "noise_reason": {"type": "string"},
        "voice_summary": {"type": "string"},
        "notable_phrases": {"type": "string"},
        "representative_quality": {"type": "string", "enum": ["High", "Medium", "Low"]},
        "notes_for_prompt2": {"type": "string"},
    },
    "required": ["final_topic_label", "source_assessment", "tone_gradient", "voice_direction",
                 "report_use_status", "continuity_relevance", "matched_previous_topic_id",
                 "change_signal_summary", "noise_flag", "noise_reason", "voice_summary",
                 "notable_phrases", "representative_quality", "notes_for_prompt2"],
    "additionalProperties": False,
}


def build_analysis_cluster_user_prompt(cluster_row: dict, representatives: list) -> str:
    lines = [
        "## §3 Analysis Cluster Classification対象",
        f"cluster_id: {cluster_row.get('cluster_id')}",
        f"signal_id: {cluster_row.get('signal_id')}",
        f"report_topic_label: {cluster_row.get('report_topic_label')}",
        f"narrative_id: {cluster_row.get('narrative_id')}",
        f"narrative_name: {cluster_row.get('narrative_name')}",
        f"cluster_records: {cluster_row.get('cluster_records')} / "
        f"eligible_records: {cluster_row.get('eligible_records')} / "
        f"preliminary_consistency: {cluster_row.get('preliminary_consistency')}",
        "", _format_representatives(representatives),
    ]
    return "\n".join(lines)


def classify_analysis_cluster(azure_client, model: str, cluster_row: dict, representatives: list, *,
                                max_attempts: int = 2) -> dict:
    """§3の分類結果を返す（call_llm_structuredの戻り値そのまま、
    {"data","mode","usage","latency_ms"}）。呼び出し元がgeo_report_analysis_cluster_
    classificationsへ保存する。"""
    user_prompt = build_analysis_cluster_user_prompt(cluster_row, representatives)
    return geo_llm_common.call_llm_structured(
        azure_client, model, _prompt1_system_prompt(), user_prompt,
        ANALYSIS_CLUSTER_CLASSIFICATION_SCHEMA, ANALYSIS_CLUSTER_SCHEMA_NAME, max_attempts=max_attempts)


ANALYSIS_CLUSTER_BATCH_SCHEMA_NAME = "geo_report_prompt1_analysis_cluster_batch_classification"

# 全フィールド名・型はANALYSIS_CLUSTER_CLASSIFICATION_SCHEMAと同じまま保持し、
# enum/required(cluster_id以外)だけを緩める（_batch_item_schema_from参照。
# フィールド名を落とすとAzure構造化出力が該当フィールドを生成しなくなることを
# 実機確認済み）。最終的な厳密検証（enum含む）はreconcile_batch_llm_resultsが
# ANALYSIS_CLUSTER_CLASSIFICATION_SCHEMAへ個別に照合して行う。
ANALYSIS_CLUSTER_BATCH_SCHEMA = _batch_results_schema(
    _batch_item_schema_from(ANALYSIS_CLUSTER_CLASSIFICATION_SCHEMA))


def build_analysis_cluster_batch_user_prompt(clusters: list) -> str:
    """clusters: [{"cluster_row": dict, "representatives": list}, ...]"""
    lines = ["## §3 Analysis Cluster Classification対象（バッチ、複数クラスタを含む）", ""]
    for item in clusters:
        cluster_row = item["cluster_row"]
        lines.append(f"### cluster_id: {cluster_row.get('cluster_id')}")
        lines.append(
            f"signal_id: {cluster_row.get('signal_id')} / "
            f"report_topic_label: {cluster_row.get('report_topic_label')} / "
            f"narrative_id: {cluster_row.get('narrative_id')} / "
            f"narrative_name: {cluster_row.get('narrative_name')}")
        lines.append(
            f"cluster_records: {cluster_row.get('cluster_records')} / "
            f"eligible_records: {cluster_row.get('eligible_records')} / "
            f"preliminary_consistency: {cluster_row.get('preliminary_consistency')}")
        lines.append("")
        lines.append(_format_representatives(item["representatives"]))
        lines.append("")
    return "\n".join(lines)


def classify_analysis_clusters_batch(azure_client, model: str, clusters: list, *, max_attempts: int = 2) -> dict:
    """clusters: [{"cluster_row":..., "representatives":...}, ...]。1回のLLM呼び出しで
    複数クラスタをまとめて分類する（Prompt1 Batching最適化、2026-09-11）。戻り値:
    {"data": {"results": [{"cluster_id":..., ...§3の14フィールド}, ...]}, "mode","usage","latency_ms"}。"""
    user_prompt = build_analysis_cluster_batch_user_prompt(clusters)
    return geo_llm_common.call_llm_structured(
        azure_client, model, _prompt1_batch_system_prompt(), user_prompt,
        ANALYSIS_CLUSTER_BATCH_SCHEMA, ANALYSIS_CLUSTER_BATCH_SCHEMA_NAME, max_attempts=max_attempts)


# ---------------------------------------------------------------------------
# §4 Sentiment Cluster Classification（11列。targeted_stance_status/countは
# Targeted Stance結果から後付けするためLLM出力には含めない）
# ---------------------------------------------------------------------------

SENTIMENT_CLUSTER_SCHEMA_NAME = "geo_report_prompt1_sentiment_cluster_classification"

_EXCEPTION_PATTERN_VALUES = [
    "mixed_source", "mixed_attitude", "mixed_theme", "fallback_context", "fallback_no_required_context",
    "fallback_excluded_context", "quoted_article_only", "shared_post_without_comment",
    "same_page_multiple_voices", "insufficient_representatives", "other",
]

SENTIMENT_CLUSTER_CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "theme_relevance": {"type": "string", "enum": ["relevant", "irrelevant", "uncertain"]},
        "source_class": {"type": "string", "enum": [
            "Public Voice", "Media", "Official / Government", "Corporate / PR", "SEO / Affiliate",
            "Fact Only", "Mixed", "Unknown"]},
        "reaction_type": {"type": "string", "enum": [
            "Emotion", "Evaluation", "Question", "Prediction", "Comparison", "Behavior", "Life Impact",
            "Mixed", "Information Only", "Unknown"]},
        "attitude": {"type": "string", "enum": [
            "positive", "neutral", "negative", "mixed", "not_applicable", "unknown"]},
        "cluster_consistency": {"type": "string", "enum": ["High", "Medium", "Low"]},
        "apply_to_cluster": {"type": "boolean"},
        "exception_pattern": {"type": "array", "items": {"type": "string", "enum": _EXCEPTION_PATTERN_VALUES}},
        "classification_reason": {"type": "string"},
    },
    "required": ["theme_relevance", "source_class", "reaction_type", "attitude", "cluster_consistency",
                 "apply_to_cluster", "exception_pattern", "classification_reason"],
    "additionalProperties": False,
}


def build_sentiment_cluster_user_prompt(cluster_row: dict, representatives: list) -> str:
    lines = [
        "## §4 Sentiment Cluster Classification対象",
        f"sentiment_cluster_id: {cluster_row.get('cluster_id')}",
        f"signal_id: {cluster_row.get('signal_id')}",
        f"report_topic_label: {cluster_row.get('report_topic_label')}",
        f"cluster_records: {cluster_row.get('cluster_records')} / "
        f"eligible_records: {cluster_row.get('eligible_records')} / "
        f"source_class_preliminary: {cluster_row.get('source_class_preliminary')} (高Recall候補、最終判定ではない) / "
        f"attitude_hint: {cluster_row.get('attitude_hint')} (汎用弱ヒント、最終判定ではない)",
        "", _format_representatives(representatives),
    ]
    return "\n".join(lines)


def classify_sentiment_cluster(azure_client, model: str, cluster_row: dict, representatives: list, *,
                                 max_attempts: int = 2) -> dict:
    user_prompt = build_sentiment_cluster_user_prompt(cluster_row, representatives)
    return geo_llm_common.call_llm_structured(
        azure_client, model, _prompt1_system_prompt(), user_prompt,
        SENTIMENT_CLUSTER_CLASSIFICATION_SCHEMA, SENTIMENT_CLUSTER_SCHEMA_NAME, max_attempts=max_attempts)


SENTIMENT_CLUSTER_BATCH_SCHEMA_NAME = "geo_report_prompt1_sentiment_cluster_batch_classification"

SENTIMENT_CLUSTER_BATCH_SCHEMA = _batch_results_schema(
    _batch_item_schema_from(SENTIMENT_CLUSTER_CLASSIFICATION_SCHEMA))


def build_sentiment_cluster_batch_user_prompt(clusters: list) -> str:
    """clusters: [{"cluster_row": dict, "representatives": list}, ...]"""
    lines = ["## §4 Sentiment Cluster Classification対象（バッチ、複数クラスタを含む）", ""]
    for item in clusters:
        cluster_row = item["cluster_row"]
        lines.append(f"### sentiment_cluster_id: {cluster_row.get('cluster_id')}")
        lines.append(
            f"signal_id: {cluster_row.get('signal_id')} / "
            f"report_topic_label: {cluster_row.get('report_topic_label')}")
        lines.append(
            f"cluster_records: {cluster_row.get('cluster_records')} / "
            f"eligible_records: {cluster_row.get('eligible_records')} / "
            f"source_class_preliminary: {cluster_row.get('source_class_preliminary')} (高Recall候補、最終判定ではない) / "
            f"attitude_hint: {cluster_row.get('attitude_hint')} (汎用弱ヒント、最終判定ではない)")
        lines.append("")
        lines.append(_format_representatives(item["representatives"]))
        lines.append("")
    return "\n".join(lines)


def classify_sentiment_clusters_batch(azure_client, model: str, clusters: list, *, max_attempts: int = 2) -> dict:
    """clusters: [{"cluster_row":..., "representatives":...}, ...]。1回のLLM呼び出しで
    複数クラスタをまとめて分類する（Prompt1 Batching最適化、2026-09-11）。"""
    user_prompt = build_sentiment_cluster_batch_user_prompt(clusters)
    return geo_llm_common.call_llm_structured(
        azure_client, model, _prompt1_batch_system_prompt(), user_prompt,
        SENTIMENT_CLUSTER_BATCH_SCHEMA, SENTIMENT_CLUSTER_BATCH_SCHEMA_NAME, max_attempts=max_attempts)


def exception_pattern_to_text(values) -> str:
    """LLMのarray出力をCSV/DB保存用の'|'区切り文字列へ変換する（E章classified_clusters.csv
    等のexception_pattern列と同じ表現形式）。

    ChatGPT Import経路（geo_report_prompt_export_service.import_prompt1_result）は
    CSVから読んだ時点で既にこの'|'区切りテキスト形式の文字列を渡してくる
    （2026-09-16発見: ここでさらに"|".join(str)してしまうと1文字ずつ区切られて
    データが壊れる不具合があった）。str入力はそのまま返し、list入力のみ結合する。"""
    if isinstance(values, str):
        return values
    return "|".join(values)


# ---------------------------------------------------------------------------
# §4.5 Targeted Stance（Precision優先、全クラスタへ強制しない）
# ---------------------------------------------------------------------------

TARGETED_STANCE_SCHEMA_NAME = "geo_report_prompt1_targeted_stance"

TARGETED_STANCE_SCHEMA = {
    "type": "object",
    "properties": {
        "targets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "target_type": {"type": "string", "enum": [
                        "candidate", "person", "party", "organization", "company", "brand", "policy",
                        "institution", "other"]},
                    "target_name": {"type": "string"},
                    "target_normalized_id": {"type": ["string", "null"]},
                    "stance_polarity": {"type": "string", "enum": [
                        "favorable", "unfavorable", "neutral", "mixed", "unclear"]},
                    "stance_strength": {"type": "string", "enum": ["strong", "moderate", "weak", "not_applicable"]},
                    "stance_confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "stance_evidence": {"type": "string"},
                    "stance_basis": {"type": "string", "enum": [
                        "direct_evaluation", "direct_support", "direct_opposition", "vote_intent",
                        "comparison", "defense", "attack", "conditional_evaluation", "other"]},
                    "stance_cluster_consistency": {"type": "string", "enum": ["High", "Medium", "Low"]},
                    "apply_stance_to_cluster": {"type": "boolean"},
                    "stance_exception_pattern": {"type": "string"},
                    "stance_reason": {"type": "string"},
                },
                "required": ["target_type", "target_name", "target_normalized_id", "stance_polarity",
                             "stance_strength", "stance_confidence", "stance_evidence", "stance_basis",
                             "stance_cluster_consistency", "apply_stance_to_cluster", "stance_exception_pattern",
                             "stance_reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["targets"],
    "additionalProperties": False,
}


def build_targeted_stance_user_prompt(cluster_row: dict, representatives: list,
                                        candidate_target_names: list = None) -> str:
    lines = [
        "## §4.5 Targeted Stance対象",
        f"sentiment_cluster_id: {cluster_row.get('cluster_id')}",
        f"signal_id: {cluster_row.get('signal_id')}",
    ]
    if candidate_target_names:
        lines.append(
            "既知のTarget候補（Narrative Master由来、明示的な評価が読み取れる場合のみtargetsに含めること。"
            f"単なる言及だけでは対象化しない）: {', '.join(candidate_target_names)}")
    lines.append(
        "明確なTargetに対する評価が読み取れないクラスタの場合、targetsは空配列で構いません"
        "（無理に生成しないこと）。")
    lines.append("")
    lines.append(_format_representatives(representatives))
    return "\n".join(lines)


def classify_targeted_stance(azure_client, model: str, cluster_row: dict, representatives: list, *,
                               candidate_target_names: list = None, max_attempts: int = 2) -> dict:
    user_prompt = build_targeted_stance_user_prompt(cluster_row, representatives, candidate_target_names)
    return geo_llm_common.call_llm_structured(
        azure_client, model, _prompt1_system_prompt(), user_prompt,
        TARGETED_STANCE_SCHEMA, TARGETED_STANCE_SCHEMA_NAME, max_attempts=max_attempts)


TARGETED_STANCE_BATCH_SCHEMA_NAME = "geo_report_prompt1_targeted_stance_batch"

TARGETED_STANCE_BATCH_SCHEMA = _batch_results_schema(
    _batch_item_schema_from(TARGETED_STANCE_SCHEMA))


def build_targeted_stance_batch_user_prompt(clusters: list) -> str:
    """clusters: [{"cluster_row":..., "representatives":..., "candidate_target_names": list|None}, ...]"""
    lines = ["## §4.5 Targeted Stance対象（バッチ、複数クラスタを含む）", ""]
    for item in clusters:
        cluster_row = item["cluster_row"]
        lines.append(f"### sentiment_cluster_id: {cluster_row.get('cluster_id')}")
        lines.append(f"signal_id: {cluster_row.get('signal_id')}")
        candidate_target_names = item.get("candidate_target_names")
        if candidate_target_names:
            lines.append(
                "既知のTarget候補（Narrative Master由来、明示的な評価が読み取れる場合のみtargetsに含めること。"
                f"単なる言及だけでは対象化しない）: {', '.join(candidate_target_names)}")
        lines.append(
            "明確なTargetに対する評価が読み取れないクラスタの場合、targetsは空配列で構いません"
            "（無理に生成しないこと）。")
        lines.append("")
        lines.append(_format_representatives(item["representatives"]))
        lines.append("")
    return "\n".join(lines)


def classify_targeted_stance_batch(azure_client, model: str, clusters: list, *, max_attempts: int = 2) -> dict:
    """clusters: [{"cluster_row":..., "representatives":..., "candidate_target_names": list|None}, ...]。
    1回のLLM呼び出しで複数クラスタをまとめて判定する（Prompt1 Batching最適化、2026-09-11）。
    各resultは{"cluster_id":..., "targets": [...]}の形（reconcile側でcluster_id除去後、
    残りの{"targets": [...]}がTARGETED_STANCE_SCHEMAへ照合される）。"""
    user_prompt = build_targeted_stance_batch_user_prompt(clusters)
    return geo_llm_common.call_llm_structured(
        azure_client, model, _prompt1_batch_system_prompt(), user_prompt,
        TARGETED_STANCE_BATCH_SCHEMA, TARGETED_STANCE_BATCH_SCHEMA_NAME, max_attempts=max_attempts)


# ---------------------------------------------------------------------------
# §7 QA（決定的計算、LLM不使用。prompt1_quality_audit.csv相当、24列）
# ---------------------------------------------------------------------------

def compute_prompt1_quality_audit(sentiment_clusters: dict, sentiment_classifications: dict,
                                    targeted_stance_by_cluster: dict = None) -> list:
    """sentiment_clusters: run_preprocess結果のPreprocessResult.sentiment_clusters
    （{cluster_id: ClusterStats}、record_count/matched_count/fallback_countを持つ）。
    sentiment_classifications: {sentiment_cluster_id: 分類結果dict（theme_relevance等、
    LLM出力そのまま）}。targeted_stance_by_cluster: {sentiment_cluster_id: [stance dict,...]}。
    戻り値: per-Signal + 'ALL'行のリスト（E章24列契約）。Prompt1はCluster単位で分類する
    ため、fallback_records_included/excludedはそのクラスタのapply_to_cluster判定に応じて
    fallback_countをそのまま按分せず全数を割り当てる（Clusterごとの判定は全レコード共通）。"""
    targeted_stance_by_cluster = targeted_stance_by_cluster or {}
    per_signal: dict = {}

    def _bucket(signal_id: str) -> dict:
        return per_signal.setdefault(signal_id, {
            "total_eligible_records": 0, "preliminary_public_voice_candidates": 0,
            "final_theme_relevant_records": 0, "final_public_voice_records": 0,
            "final_threeway_sentiment_records": 0, "excluded_media_official_corporate": 0,
            "excluded_fact_only": 0, "excluded_irrelevant": 0, "mixed_unknown_records": 0,
            "fallback_records_total": 0, "fallback_records_included": 0, "fallback_records_excluded": 0,
            "targeted_stance_available_records": 0, "targeted_stance_directional_records": 0,
            "targeted_stance_high_medium_records": 0, "targeted_stance_low_confidence_records": 0,
            "targeted_stance_ambiguous_records": 0, "unclassified_records": 0,
        })

    for cluster_id, stats in sentiment_clusters.items():
        signal_id = stats.rule.signal_source
        eligible = stats.record_count - stats.duplicate_count
        b = _bucket(signal_id)
        b["total_eligible_records"] += eligible
        if stats.source_class_preliminary == "Public_Voice_Candidate":
            b["preliminary_public_voice_candidates"] += eligible
        b["fallback_records_total"] += stats.fallback_count

        cls = sentiment_classifications.get(cluster_id)
        if cls is None:
            b["unclassified_records"] += eligible
            b["fallback_records_excluded"] += stats.fallback_count
            continue
        if cls.get("theme_relevance") == "relevant":
            b["final_theme_relevant_records"] += eligible
        elif cls.get("theme_relevance") == "irrelevant":
            b["excluded_irrelevant"] += eligible

        source_class = cls.get("source_class")
        attitude = cls.get("attitude")
        apply_to_cluster = cls.get("apply_to_cluster")
        if apply_to_cluster:
            b["fallback_records_included"] += stats.fallback_count
        else:
            b["fallback_records_excluded"] += stats.fallback_count
        if source_class == "Public Voice" and cls.get("theme_relevance") == "relevant" and apply_to_cluster:
            b["final_public_voice_records"] += eligible
            if attitude in ("positive", "neutral", "negative"):
                b["final_threeway_sentiment_records"] += eligible
        if source_class in ("Media", "Official / Government", "Corporate / PR"):
            b["excluded_media_official_corporate"] += eligible
        if source_class == "Fact Only":
            b["excluded_fact_only"] += eligible
        if attitude in ("mixed", "unknown"):
            b["mixed_unknown_records"] += eligible

        stances = targeted_stance_by_cluster.get(cluster_id, [])
        if stances:
            b["targeted_stance_available_records"] += eligible
            if any(s.get("stance_polarity") in ("favorable", "unfavorable") for s in stances):
                b["targeted_stance_directional_records"] += eligible
            if any(s.get("stance_confidence") in ("high", "medium") for s in stances):
                b["targeted_stance_high_medium_records"] += eligible
            if any(s.get("stance_confidence") == "low" for s in stances):
                b["targeted_stance_low_confidence_records"] += eligible
            if any(s.get("stance_polarity") == "unclear" for s in stances):
                b["targeted_stance_ambiguous_records"] += eligible

    rows = []
    all_row = {k: 0 for k in [
        "total_eligible_records", "preliminary_public_voice_candidates", "final_theme_relevant_records",
        "final_public_voice_records", "final_threeway_sentiment_records", "excluded_media_official_corporate",
        "excluded_fact_only", "excluded_irrelevant", "mixed_unknown_records", "fallback_records_total",
        "fallback_records_included", "fallback_records_excluded", "targeted_stance_available_records",
        "targeted_stance_directional_records", "targeted_stance_high_medium_records",
        "targeted_stance_low_confidence_records", "targeted_stance_ambiguous_records", "unclassified_records",
    ]}
    for signal_id in sorted(per_signal.keys()):
        b = per_signal[signal_id]
        rows.append(_finalize_audit_row(signal_id, b))
        for k in all_row:
            all_row[k] += b.get(k, 0)
    rows.append(_finalize_audit_row("ALL", all_row))
    return rows


def _finalize_audit_row(signal_id: str, b: dict) -> dict:
    total = b["total_eligible_records"]
    prelim_pv = b["preliminary_public_voice_candidates"]
    final_pv = b["final_public_voice_records"]
    ts_available = b["targeted_stance_available_records"]
    ts_high_medium = b["targeted_stance_high_medium_records"]
    classification_coverage_rate = round((total - b["unclassified_records"]) / total, 4) if total else 0.0
    public_voice_retention_rate = round(final_pv / prelim_pv, 4) if prelim_pv else None
    targeted_stance_coverage_rate = round(ts_available / final_pv, 4) if final_pv else 0.0
    targeted_stance_usable_rate = round(ts_high_medium / ts_available, 4) if ts_available else 0.0

    qa_status = "PASS"
    qa_notes = []
    if classification_coverage_rate < 1.0:
        qa_status = "REVIEW REQUIRED"
        qa_notes.append("分類coverageが100%未満")
    if public_voice_retention_rate is not None and public_voice_retention_rate < 0.30:
        qa_status = "REVIEW REQUIRED"
        qa_notes.append("Public Voice retention rateが30%未満（要再レビュー、Phase A確認済みの閾値）")

    return {
        "signal_id": signal_id, "total_eligible_records": total,
        "preliminary_public_voice_candidates": prelim_pv,
        "final_theme_relevant_records": b["final_theme_relevant_records"],
        "final_public_voice_records": final_pv,
        "final_threeway_sentiment_records": b["final_threeway_sentiment_records"],
        "excluded_media_official_corporate": b["excluded_media_official_corporate"],
        "excluded_fact_only": b["excluded_fact_only"], "excluded_irrelevant": b["excluded_irrelevant"],
        "mixed_unknown_records": b["mixed_unknown_records"],
        "fallback_records_total": b["fallback_records_total"],
        "fallback_records_included": b["fallback_records_included"],
        "fallback_records_excluded": b["fallback_records_excluded"],
        "classification_coverage_rate": classification_coverage_rate,
        "public_voice_retention_rate": public_voice_retention_rate,
        "targeted_stance_available_records": ts_available,
        "targeted_stance_directional_records": b["targeted_stance_directional_records"],
        "targeted_stance_high_medium_records": ts_high_medium,
        "targeted_stance_low_confidence_records": b["targeted_stance_low_confidence_records"],
        "targeted_stance_ambiguous_records": b["targeted_stance_ambiguous_records"],
        "targeted_stance_coverage_rate": targeted_stance_coverage_rate,
        "targeted_stance_usable_rate": targeted_stance_usable_rate,
        "qa_status": qa_status, "qa_notes": "; ".join(qa_notes),
    }
