"""Report Studio: Evidence Classification Service（Prompt1相当、Phase3スコープ）

外部ワークフローのPrompt1（クラスタ単位のEvidence-firstな次元分類）をLLM呼び出し1回/
クラスタで再現する。分類そのものはLLM（Azure OpenAI、既存`geo_llm_common.call_llm_structured`
経由、新規SDK呼び出しは追加しない）が行うが、Evidence根拠の検証（捏造check）・
Validity QA（`classification_validity`）は決定的Pythonコードで行う（本プロジェクトの
「決定的処理とLLM処理を分離する」方針、geo_expert_quality_guard.pyと同じ設計）。

前提となる入力:
  - narrative_row_data: geo_report_narrative_master_rows.row_data（WIDE v6.0の1行）。
    `dimension_config_json`/`evidence_policy_json`/`display_rules_json`をここから読む。
  - cluster: geo_report_clusters の1行相当のdict。
  - representatives: geo_report_cluster_representatives のうちそのclusterに属する行のlist。

Validity QAとAnalytical Coverageは別カテゴリ（Prompt1 v6.2の設計方針）として混同しない:
  - classification_validity: 個別クラスタの分類が「捏造されていないか・矛盾していないか」
    （PASS|REVIEW REQUIRED）。
  - analytical coverage: run全体でNarrative Master設定に対しどれだけ評価できたか
    （疎な結果それ自体は不合格の理由にしない、compute_analytical_coverageで別途算出）。
"""

from __future__ import annotations

import json
from typing import Optional

import geo_llm_common

SCHEMA_NAME = "geo_report_cluster_classification"

_SYSTEM_PROMPT = """あなたはReport Studioの証拠分類エンジン（Prompt1相当）です。以下を厳守してください。

1. 分類はクラスタに含まれる代表記事（representatives）の実際の内容のみを根拠にする。
   signal_id・Narrative名・queryの文言・過去の類似判断からの類推で値を埋めない。
   十分な根拠が無い場合は必ずunknown_valueを選ぶこと（無理に決めない）。
2. cluster全体（apply_to_cluster=true）に適用できる分類か、一部の記事にしか
   当てはまらない分類か（apply_to_cluster=false、その場合はrecord_level_exceptionsで
   個別記事の値を明示）、そもそも判定不能・無関係か（uncertain/irrelevant）を必ず区別する。
   record_count・fallback_share等の統計だけでcluster全体への適用可否を機械的に決めない。
3. evidence_record_idsには、実際に提示されたrepresentativeのrecord_idのみを入れる。
   存在しないrecord_idを作らない。
4. 日本語訳が必要な場合（instructions内で指示された場合）、japanese_translationは
   要約ではなく忠実な翻訳にする。
"""


def _dimension_allowed_values(dim_cfg: dict) -> list:
    allowed = list(dim_cfg.get("allowed_values") or [])
    unknown_value = dim_cfg.get("unknown_value")
    if unknown_value and unknown_value not in allowed:
        allowed = allowed + [unknown_value]
    return allowed


def build_dimension_property_schema(dim_cfg: dict) -> dict:
    allowed = _dimension_allowed_values(dim_cfg)
    if dim_cfg.get("type") == "multi":
        value_schema = {"type": "array", "items": {"type": "string", "enum": allowed}, "minItems": 1}
    else:
        value_schema = {"type": "string", "enum": allowed}
    return {
        "type": "object",
        "properties": {
            "value": value_schema,
            "apply_to_cluster": {"type": "string", "enum": ["true", "false", "irrelevant", "uncertain"]},
            "confidence": {"type": "string", "enum": ["High", "Medium", "Low"]},
            "evidence_record_ids": {"type": "array", "items": {"type": "string"}},
            "rationale": {"type": "string"},
        },
        "required": ["value", "apply_to_cluster", "confidence", "evidence_record_ids", "rationale"],
        "additionalProperties": False,
    }


def build_cluster_classification_schema(dimension_config: dict) -> dict:
    dim_ids = list(dimension_config.keys())
    return {
        "type": "object",
        "properties": {
            "dimensions": {
                "type": "object",
                "properties": {d: build_dimension_property_schema(dimension_config[d]) for d in dim_ids},
                "required": dim_ids,
                "additionalProperties": False,
            },
            "record_level_exceptions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "dimension_id": {"type": "string", "enum": dim_ids} if dim_ids else {"type": "string"},
                        "record_id": {"type": "string"},
                        "value": {"type": "string"},
                        "rationale": {"type": "string"},
                    },
                    "required": ["dimension_id", "record_id", "value", "rationale"],
                    "additionalProperties": False,
                },
            },
            "japanese_translations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "record_id": {"type": "string"},
                        "japanese_translation": {"type": "string"},
                    },
                    "required": ["record_id", "japanese_translation"],
                    "additionalProperties": False,
                },
            },
            "cluster_notes": {"type": "string"},
        },
        "required": ["dimensions", "record_level_exceptions", "japanese_translations", "cluster_notes"],
        "additionalProperties": False,
    }


def _parse_json_field(row_data: dict, key: str) -> dict:
    raw = row_data.get(key)
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


# Narrative固有のdimension_config_jsonが未設定の場合に使う汎用フォールバック。
# 外部ツール（Babel Street等）由来のNarrative Masterは、Narrative固有の分類軸を
# 定義しないまま投入されることがある——CSV側を要求せず、programmatic側で安全に
# 分類を成立させるための最小限の1軸（narrative_direction）。build_cluster_
# classification_schemaがdimension_config={}を受け取るとproperties=0件の
# スキーマになり、LLM構造化出力が事実上使い物にならなくなる問題（Iran/Hormuz
# Calibrationで発見、geo_report_processing_service._dimension_config_preflight_error
# の元コメント参照）への根本対応。Narrative固有の分類軸を設定すればそちらが優先され、
# このデフォルトは使われない。
DEFAULT_DIMENSION_CONFIG = {
    "narrative_direction": {
        "type": "single",
        "label": "動向の方向性",
        "definition": "この記事が示す、当該Narrativeにおける動きの方向性",
        "evidence_rule": "本文中の明示的な記述のみに基づく。推測しない。",
        "allowed_values": ["intensifying", "easing", "stable", "mixed", "unknown"],
        "unknown_value": "unknown",
    },
}


def get_dimension_config(narrative_row_data: dict) -> dict:
    """Narrative Master行のdimension_config_jsonを返す。未設定（空文字列・欠落・
    空オブジェクト）の場合はDEFAULT_DIMENSION_CONFIGへフォールバックする——
    Narrative Master CSV側にこの列を必須要求しないための対応。"""
    parsed = _parse_json_field(narrative_row_data, "dimension_config_json")
    return parsed if parsed else DEFAULT_DIMENSION_CONFIG


def get_evidence_policy(narrative_row_data: dict) -> dict:
    return _parse_json_field(narrative_row_data, "evidence_policy_json")


def get_display_rules(narrative_row_data: dict) -> dict:
    return _parse_json_field(narrative_row_data, "display_rules_json")


def build_classification_user_prompt(narrative_row_data: dict, cluster: dict, representatives: list) -> str:
    dimension_config = get_dimension_config(narrative_row_data)
    evidence_policy = get_evidence_policy(narrative_row_data)
    display_rules = get_display_rules(narrative_row_data)
    translate = bool(display_rules.get("translate_evidence_to_japanese"))

    lines = []
    lines.append(f"report_topic_label: {narrative_row_data.get('report_topic_label', '')}")
    lines.append(f"narrative_name: {narrative_row_data.get('narrative_name', '')}")
    lines.append(f"narrative_description: {narrative_row_data.get('narrative_description', '')}")
    lines.append("")
    lines.append("## 分類対象dimension定義")
    for dim_id, cfg in dimension_config.items():
        lines.append(
            f"- {dim_id} ({cfg.get('type', 'single')}): {cfg.get('label', '')}\n"
            f"  定義: {cfg.get('definition', '')}\n"
            f"  evidence_rule: {cfg.get('evidence_rule', '')}\n"
            f"  allowed_values: {cfg.get('allowed_values')}\n"
            f"  unknown_value: {cfg.get('unknown_value')}"
        )
    lines.append("")
    if evidence_policy:
        lines.append(f"## Evidence Policy\n{json.dumps(evidence_policy, ensure_ascii=False)}")
        lines.append("")
    lines.append("## クラスタ内の代表記事（representatives）")
    for rep in representatives:
        lines.append(
            f"- [record_id: {rep.get('record_id')}] {rep.get('title') or ''}\n"
            f"  出典: {rep.get('source_name') or ''} / 公開: {rep.get('published_at') or ''} / "
            f"言語: {rep.get('language') or ''} / 種別: {rep.get('source_bucket') or ''}\n"
            f"  本文: {rep.get('text_for_review') or ''}"
        )
    lines.append("")
    if translate:
        lines.append(
            "## 翻訳指示\n"
            "evidence_record_idsとして引用したrepresentativeについて、japanese_translationへ"
            "本文の忠実な翻訳（要約ではない）を記入すること。"
        )
    else:
        lines.append("japanese_translationsは空配列でよい（翻訳指示なし）。")
    return "\n".join(lines)


def classify_cluster(
    azure_client,
    model: str,
    narrative_row_data: dict,
    cluster: dict,
    representatives: list,
    *,
    max_attempts: int = 2,
) -> dict:
    """1クラスタをLLMで分類する。戻り値はcall_llm_structuredの戻り値そのもの
    （{"data","mode","usage","latency_ms"}）。呼び出し元がclassification_validityと
    合わせてDBへ保存する"""
    dimension_config = get_dimension_config(narrative_row_data)
    schema = build_cluster_classification_schema(dimension_config)
    user_prompt = build_classification_user_prompt(narrative_row_data, cluster, representatives)
    return geo_llm_common.call_llm_structured(
        azure_client, model, _SYSTEM_PROMPT, user_prompt, schema, SCHEMA_NAME,
        max_attempts=max_attempts,
    )


# ---------------------------------------------------------------------------
# Classification Validity QA（決定的、非LLM、per-cluster）
# ---------------------------------------------------------------------------

def run_classification_validity_checks(
    classification: dict,
    representatives: list,
    narrative_row_data: dict,
) -> dict:
    """各checkを{"passed": bool, "detail": str}で返す。Prompt1 v6.2の
    classification_validityカテゴリ（semantic_dimension_conflicts/
    dimension_without_evidence/translation_fidelity_failures/
    unsafe_cluster_propagation/overconservative_cluster_suppression）に対応する。
    人によるレビュー材料であり、自動修復はしない（geo_expert_quality_guardと同じ思想）。"""
    known_record_ids = {r.get("record_id") for r in representatives if r.get("record_id")}
    dimensions = classification.get("dimensions") or {}
    exceptions = classification.get("record_level_exceptions") or []
    translations = classification.get("japanese_translations") or []
    dimension_config = get_dimension_config(narrative_row_data)
    display_rules = get_display_rules(narrative_row_data)
    translate_required = bool(display_rules.get("translate_evidence_to_japanese"))

    checks: dict = {}

    # dimension_without_evidence: unknown以外の値にはevidence_record_idsが必須、かつ捏造禁止
    fabricated = []
    missing_evidence = []
    for dim_id, entry in dimensions.items():
        cfg = dimension_config.get(dim_id, {})
        unknown_value = cfg.get("unknown_value")
        value = entry.get("value")
        is_unknown = (value == unknown_value) or (isinstance(value, list) and not value)
        evidence_ids = entry.get("evidence_record_ids") or []
        for rid in evidence_ids:
            if rid not in known_record_ids:
                fabricated.append((dim_id, rid))
        if not is_unknown and not evidence_ids:
            missing_evidence.append(dim_id)
    checks["dimension_without_evidence"] = {
        "passed": not missing_evidence,
        "detail": f"evidence無しでunknown以外の値を持つdimension: {missing_evidence}" if missing_evidence else "",
    }
    checks["evidence_ids_not_fabricated"] = {
        "passed": not fabricated,
        "detail": f"クラスタに存在しないrecord_idを引用: {fabricated}" if fabricated else "",
    }

    # record_level_exceptions / japanese_translationsのrecord_idも同様に検証
    exception_fabricated = [e for e in exceptions if e.get("record_id") not in known_record_ids]
    translation_fabricated = [t for t in translations if t.get("record_id") not in known_record_ids]
    if exception_fabricated or translation_fabricated:
        checks["evidence_ids_not_fabricated"] = {
            "passed": False,
            "detail": (checks["evidence_ids_not_fabricated"]["detail"]
                       + f" record_level_exceptions捏造:{len(exception_fabricated)}件"
                       f" japanese_translations捏造:{len(translation_fabricated)}件"),
        }

    # unsafe_cluster_propagation: apply_to_cluster=trueなのに同一dimensionへexceptionが
    # 積まれている（cluster全体に当てはまる、と言いながら個別記事の例外を出しているのは矛盾）
    true_dims = {d for d, e in dimensions.items() if e.get("apply_to_cluster") == "true"}
    conflicting = sorted({e.get("dimension_id") for e in exceptions if e.get("dimension_id") in true_dims})
    checks["unsafe_cluster_propagation"] = {
        "passed": not conflicting,
        "detail": f"apply_to_cluster=trueなのにrecord_level_exceptionsが存在するdimension: {conflicting}"
                  if conflicting else "",
    }

    # semantic_dimension_conflicts: apply_to_cluster=false/irrelevant/uncertainなのに
    # evidence_record_idsが最大件数（cluster全記事数）ある＝実質cluster全体扱いという矛盾
    semantic_conflicts = []
    for dim_id, entry in dimensions.items():
        if entry.get("apply_to_cluster") in ("false", "irrelevant", "uncertain"):
            evidence_ids = entry.get("evidence_record_ids") or []
            if known_record_ids and set(evidence_ids) == known_record_ids and len(known_record_ids) > 1:
                semantic_conflicts.append(dim_id)
    checks["semantic_dimension_conflicts"] = {
        "passed": not semantic_conflicts,
        "detail": f"apply_to_cluster≠trueなのに全代表記事をevidenceにしているdimension: {semantic_conflicts}"
                  if semantic_conflicts else "",
    }

    # translation_fidelity_failures: 翻訳指示があるのに、evidenceとして引用された
    # record_idに対応するjapanese_translationが無い/空/原文と完全同一（ヒューリスティック、
    # 本物の翻訳品質検証ではなく欠落検出のみ）
    translation_failures = []
    if translate_required:
        cited_record_ids = set()
        for entry in dimensions.values():
            cited_record_ids.update(entry.get("evidence_record_ids") or [])
        translated_ids = {t.get("record_id"): t.get("japanese_translation", "") for t in translations}
        rep_text_by_id = {r.get("record_id"): (r.get("text_for_review") or "") for r in representatives}
        for rid in cited_record_ids:
            translation = translated_ids.get(rid)
            if not translation or not translation.strip():
                translation_failures.append(rid)
            elif translation.strip() == (rep_text_by_id.get(rid) or "").strip():
                translation_failures.append(rid)
    checks["translation_fidelity_failures"] = {
        "passed": not translation_failures,
        "detail": f"翻訳が欠落/原文と同一のrecord_id: {translation_failures}" if translation_failures else "",
    }

    # overconservative_cluster_suppression: 十分な代表記事があるのに全dimensionが
    # true以外（人間レビューへの軽いnudge、fail扱いにはしない設計だが可視化のため記録）
    all_non_true = dimensions and all(e.get("apply_to_cluster") != "true" for e in dimensions.values())
    checks["overconservative_cluster_suppression"] = {
        "passed": not (all_non_true and len(representatives) >= 3),
        "detail": "十分な代表記事があるのに全dimensionがapply_to_cluster=true以外（要目視確認）"
                  if (all_non_true and len(representatives) >= 3) else "",
    }

    return checks


def classification_validity_status(checks: dict) -> str:
    return "PASS" if all(c["passed"] for c in checks.values()) else "REVIEW REQUIRED"


# ---------------------------------------------------------------------------
# Analytical Coverage（run全体、決定的集計。疎な結果それ自体はfailにしない）
# ---------------------------------------------------------------------------

def compute_analytical_coverage(cluster_rows: list, narrative_configs: dict) -> dict:
    """cluster_rows: geo_report_clustersの各行（classification列を含む）。
    narrative_configs: {(signal_id, narrative_id): row_data}のマップ（dimension_config_json等）。
    疎な結果（該当証拠が無い）はそれ自体で不合格にしない、という方針を維持するため
    'REVIEW REQUIRED'ではなく素の数値・比率のみを返す（Prompt1 v6.2の設計を踏襲）。"""
    total_narratives = len(narrative_configs)
    covered_narratives = set()
    applicability_counts = {"true": 0, "false": 0, "irrelevant": 0, "uncertain": 0}
    high_priority_narratives = set()
    high_priority_covered = set()
    configured_dimensions = set()
    covered_dimensions = set()
    usable_clusters = 0

    for (signal_id, narrative_id), row_data in narrative_configs.items():
        for dim_id in get_dimension_config(row_data):
            configured_dimensions.add((signal_id, narrative_id, dim_id))
        if str(row_data.get("report_priority", "")).lower() == "high":
            high_priority_narratives.add((signal_id, narrative_id))

    for cluster in cluster_rows:
        classification = cluster.get("classification")
        if not classification:
            continue
        key = (cluster.get("signal_id"), cluster.get("narrative_id"))
        dimensions = classification.get("dimensions") or {}
        cluster_has_true = False
        for dim_id, entry in dimensions.items():
            applicability = entry.get("apply_to_cluster")
            if applicability in applicability_counts:
                applicability_counts[applicability] += 1
            if applicability == "true":
                cluster_has_true = True
                covered_narratives.add(key)
                covered_dimensions.add((key[0], key[1], dim_id))
                if key in high_priority_narratives:
                    high_priority_covered.add(key)
        if cluster_has_true:
            usable_clusters += 1

    classified_cluster_count = sum(1 for c in cluster_rows if c.get("classification"))
    return {
        "high_priority_narrative_coverage": (
            len(high_priority_covered) / len(high_priority_narratives) if high_priority_narratives else None
        ),
        "configured_narrative_coverage": (
            len(covered_narratives) / total_narratives if total_narratives else None
        ),
        "configured_dimension_coverage": (
            len(covered_dimensions) / len(configured_dimensions) if configured_dimensions else None
        ),
        "cluster_applicability_distribution": applicability_counts,
        "report_usable_evidence_coverage": (
            usable_clusters / classified_cluster_count if classified_cluster_count else None
        ),
        "classified_cluster_count": classified_cluster_count,
        "total_cluster_count": len(cluster_rows),
    }
