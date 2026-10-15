"""Geopolitical Intelligence OS: REVIEW MODE — Existing Report Import / Reconstruction（§17-22）。

既存レポートをアップロード/貼付し、そこから「読み(Reading)」の構造を再構成して
Reading Studio(Step2)へ載せるための処理。Expert AI Core（geo_expert_engine.py）・CQAS・
S Ranking・Actor Intelligence・Retrieval・Reading Exposure選定ロジックは一切呼ばない
——REVIEW MODEにはそもそもCQAS実行自体が無い。軽量なLLM抽出のみ
（geo_ios_report_draft_service.run_document_importと同じ既存パターン、Expert AI Engineは
経由しない）。

絶対条件（§19、§24と同じ精神）: レポートに明示されていない情報を一般知識で補完しない。
不明な項目はnull/空のままにし、not_explicit_fieldsへ列挙する。
"""
from datetime import datetime, timezone

import geo_audit
import geo_llm_common
import geo_ios_reading_exposure_composer as reading_exposure_composer

REVIEW_REVISION_STRATEGIES = ("keep_and_reinforce", "replace", "integrate")


class ReviewImportError(Exception):
    """REVIEW MODE取込・再構成の操作エラー。"""


# ---- Step1 §18: Analysis Theme / Current Situation / Question / Report Context -----

_CONTEXT_SCHEMA_NAME = "geo_ios_review_report_context_v1"
_CONTEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "analysis_theme": {"type": "string"},
        "current_situation": {"type": "string"},
        "question": {"type": "string"},
        "report_context": {"type": "string"},
        "not_explicit_fields": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["analysis_theme", "current_situation", "question", "report_context",
                 "not_explicit_fields"],
    "additionalProperties": False,
}

_CONTEXT_SYSTEM_PROMPT = """あなたは、既存の地政学分析レポートから前提情報を抽出する
アシスタントです。新しい分析・評価は行わないでください——ここではまだレポートの質を
判断しません。

厳守事項:
- レポート本文に明示されている内容のみを抽出してください。一般知識で補完しないでください。
- analysis_theme: このレポートが扱っているテーマ（1文程度）。
- current_situation: 現在の状況説明（2-4文程度、本文の記述に基づく）。
- question: このレポートが答えようとしている問い（本文に明示が無ければ、本文の構成から
  最も妥当な問いを1文で推定してよいが、新しい論点を作らないこと）。
- report_context: レポートの前提・スコープ・対象期間等（本文にあれば）。
- 該当する記述が本文に無い項目は、分かる範囲で埋めた上で、not_explicit_fieldsに
  そのfield名を追加してください（空文字のまま残さない、必ず何らかの値を入れること）。"""


def extract_report_context(azure_client, model, *, raw_text: str) -> dict:
    user_prompt = f"# レポート全文\n{raw_text}"
    result = geo_llm_common.call_llm_structured(
        azure_client, model, _CONTEXT_SYSTEM_PROMPT, user_prompt,
        _CONTEXT_SCHEMA, _CONTEXT_SCHEMA_NAME, max_attempts=2)
    return result["data"]


# ---- Step1→2 §19: Existing Reading Reconstruction ----------------------------

_RECONSTRUCT_SCHEMA_NAME = "geo_ios_review_reading_reconstruction_v1"
_RECONSTRUCT_SCHEMA = {
    "type": "object",
    "properties": {
        "primary_reading": {"type": ["string", "null"]},
        "key_actors": {"type": "array", "items": {"type": "string"}},
        "actor_objectives": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"actor": {"type": "string"}, "objective": {"type": "string"}},
                "required": ["actor", "objective"], "additionalProperties": False,
            },
        },
        "relationships": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"between": {"type": "string"}, "description": {"type": "string"}},
                "required": ["between", "description"], "additionalProperties": False,
            },
        },
        "cue": {"type": ["string", "null"]},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "causal_logic": {"type": ["string", "null"]},
        "direction_of_travel": {"type": ["string", "null"]},
        "scenario": {"type": ["string", "null"]},
        "supporting_evidence": {"type": "array", "items": {"type": "string"}},
        "weakening_evidence": {"type": "array", "items": {"type": "string"}},
        "missing_evidence": {"type": "array", "items": {"type": "string"}},
        "not_explicit_fields": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["primary_reading", "key_actors", "actor_objectives", "relationships", "cue",
                 "assumptions", "causal_logic", "direction_of_travel", "scenario",
                 "supporting_evidence", "weakening_evidence", "missing_evidence",
                 "not_explicit_fields"],
    "additionalProperties": False,
}

_RECONSTRUCT_SYSTEM_PROMPT = """あなたは、既存の地政学分析レポートから「読み(Reading)」の
構造を再構成するアシスタントです。新しい分析・新しい評価・新しい代替案の提示は行わないで
ください——ここでは、このレポートが実際に何を主張しているかを構造化して取り出すだけです。

厳守事項（絶対条件）:
- レポート本文に明示されている内容のみを抽出してください。一般知識・外部情報で
  補完・推測しないでください。
- primary_reading: このレポートの中心的な読み・結論を1-3文で（本文の言い換えでよい、
  新しい主張を作らない）。
- key_actors/actor_objectives/relationships/cue/assumptions/causal_logic/
  direction_of_travel/scenario/supporting_evidence/weakening_evidenceは、本文に
  明示されている場合のみ埋めてください。
- 本文に明示されていない項目は、null（文字列項目の場合）または空配列のままにし、
  not_explicit_fieldsにそのfield名を追加してください。無理に埋めないでください。
- missing_evidence: 本文が「まだ分からない」「今後確認が必要」等、明示的に不足を
  述べている箇所があればそれを使う。無ければ空配列のままにしてください（推測しない）。"""


def reconstruct_existing_reading(azure_client, model, *, raw_text: str) -> dict:
    user_prompt = f"# レポート全文\n{raw_text}"
    result = geo_llm_common.call_llm_structured(
        azure_client, model, _RECONSTRUCT_SYSTEM_PROMPT, user_prompt,
        _RECONSTRUCT_SCHEMA, _RECONSTRUCT_SCHEMA_NAME, max_attempts=2)
    return result["data"]


# ---- Reading card materialization（既存geo_ios_scenario_candidatesへ相乗り） --------
#
# §20絶対条件: Reading Card自体は既存presentation_service/frontend Reading Studioを
# そのまま使う。ここではCurrent Reading(Reading A)・Alternative Reading(B-E)を、既存
# scenario_candidatesの行として持たせるためのcontent整形のみ行う（新しいUI/新しい
# Card実装は作らない）。

_SHORT_TITLE_MAX_LEN = 45


def _short_title(text: str | None) -> str:
    text = (text or "").strip()
    if not text:
        return "現在の読み"
    for sep in ("。", "、"):
        idx = text.find(sep)
        if 0 < idx <= _SHORT_TITLE_MAX_LEN:
            return text[:idx]
    if len(text) <= _SHORT_TITLE_MAX_LEN:
        return text
    return text[:_SHORT_TITLE_MAX_LEN] + "…"


def _current_reading_content(reconstructed: dict) -> tuple[str, dict]:
    thesis = reconstructed.get("primary_reading")
    title = _short_title(thesis)
    content = {
        "origin": "review_current_reading",
        "raw": reconstructed,
        "why_this_matters": thesis,
        "key_actors": reconstructed.get("key_actors") or [],
        "supporting_basis": reconstructed.get("supporting_evidence") or [],
        "weakening_basis": reconstructed.get("weakening_evidence") or [],
        "missing_evidence": "; ".join(reconstructed.get("missing_evidence") or []) or None,
    }
    return title, content


def _alternative_reading_content(generated: dict) -> tuple[str, dict]:
    thesis = generated.get("reading") or ""
    structure_note = generated.get("key_actors_and_structure")
    # key_actors_and_structureは自由記述文（Actor名のリストではない）ため、chip表示用の
    # key_actorsへは変換せず、why_this_matters（Presentation Transformation Layerへの
    # 入力）へ情報が失われないよう連結するだけに留める。
    why_this_matters = f"{thesis} {structure_note}".strip() if structure_note else thesis
    title = generated.get("title") or _short_title(thesis)
    content = {
        "origin": "review_alternative_reading",
        "raw": generated,
        "why_this_matters": why_this_matters,
        "key_actors": None,
        "supporting_basis": generated.get("supporting_reasons") or [],
        "weakening_basis": generated.get("weakening_information") or [],
        "missing_evidence": None,
    }
    return title, content


def _materialize_reading_row(client, analysis_session_id: str, *, title: str, content: dict,
                              reading_role: str, rank: int, actor_id: str) -> dict:
    row = {
        "analysis_session_id": analysis_session_id, "rank": rank, "title": title,
        "content": {**content, "reading_role": reading_role, "exposure_source": "review_mode"},
        "displayed": True, "selected": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": None, "prompt_version": None, "cqas_knowledge_version": None,
    }
    created = client.insert("geo_ios_scenario_candidates", [row])[0]
    geo_audit.log_audit(
        client, actor=actor_id, action="review_reading_materialize",
        target_table="geo_ios_analysis_sessions", target_id=analysis_session_id,
        detail={"scenario_candidate_id": created["scenario_candidate_id"], "reading_role": reading_role})
    return created


# ---- §9 Alternative Reading生成（既存directed explorationを再利用） -----------------
#
# CQAS/hypothesis_register/Reading Portfolioが存在しないREVIEW MODEでは、Reading
# Exposure Composerのpool選定ロジックは使えない——既存
# generate_directed_exploration_reading（§13 Generate More Readingsで新設、Freeze対象
# 外）をそのまま再利用し、既存Reading（Current + 生成済みAlternative）をcontextに
# 渡して重複を避けさせる。新しいLLM Prompt体系は作らない。

_DEFAULT_ALTERNATIVE_DIRECTIONS = ("auto", "invert_premise", "different_actor_lead", "economic_structure")


def generate_alternative_readings_for_review(azure_client, model, *, report_context: dict,
                                              current_reading_title: str,
                                              current_reading_why_this_matters: str,
                                              count: int = 4) -> list[dict]:
    """Current Readingを唯一の既存Readingとして渡し、directionを変えながら最大count件の
    Alternativeを生成する。insufficient_materialが続く場合はそこで打ち切り、無理に埋めない
    （既存generate_directed_exploration_readingと同じ方針）。"""
    situation_text = report_context.get("current_situation") or report_context.get("analysis_theme") or ""
    existing_readings = [{
        "reading_role": "CURRENT",
        "content": {"why_this_matters": current_reading_why_this_matters},
        "title": current_reading_title,
    }]
    generated_list = []
    directions = _DEFAULT_ALTERNATIVE_DIRECTIONS[:count]
    for direction in directions:
        generated = reading_exposure_composer.generate_directed_exploration_reading(
            azure_client, model, situation_text=situation_text, existing_readings=existing_readings,
            direction=direction)
        if not generated:
            continue
        generated_list.append(generated)
        existing_readings.append({
            "reading_role": "ALTERNATIVE",
            "content": {"why_this_matters": generated.get("reading")},
            "title": generated.get("title"),
        })
    return generated_list


# ---- Orchestration -----------------------------------------------------------

def create_review_import(client, analysis_session_id: str, *, original_text: str,
                          original_filename: str | None, original_mime_type: str | None,
                          created_by: str) -> dict:
    row = {
        "analysis_session_id": analysis_session_id, "original_filename": original_filename,
        "original_mime_type": original_mime_type, "original_text": original_text,
        "status": "pending", "created_by": created_by,
    }
    created = client.insert("geo_ios_review_imports", [row])[0]
    geo_audit.log_audit(
        client, actor=created_by, action="review_import_create",
        target_table="geo_ios_review_imports", target_id=created["review_import_id"],
        detail={"analysis_session_id": analysis_session_id, "filename": original_filename})
    return created


def get_review_import(client, analysis_session_id: str) -> dict | None:
    rows = client.select("geo_ios_review_imports", {
        "select": "*", "analysis_session_id": f"eq.{analysis_session_id}", "limit": "1"})
    return rows[0] if rows else None


def run_reconstruction_and_alternatives(client, azure_client, model, analysis_session_id: str, *,
                                         actor_id: str) -> dict:
    """Step1の「この内容で読み方をレビューする」CTA押下後に実行する。
    Context抽出→Reading再構成→Reading A(Current)をmaterialize→Alternative B-E生成・
    materialize、の順に1回だけ行う（べき等ではない——同一importに対して複数回呼ばれる
    運用は想定しない、既に読みが生成済みならそのまま返す）。"""
    review_import = get_review_import(client, analysis_session_id)
    if not review_import:
        raise ReviewImportError("review_importがまだ作成されていません")
    if review_import.get("status") == "readings_ready":
        return review_import

    try:
        report_context = extract_report_context(azure_client, model, raw_text=review_import["original_text"])
        client.update("geo_ios_review_imports", {"analysis_session_id": f"eq.{analysis_session_id}"},
                      {"extracted_context": report_context, "status": "context_extracted"})

        reconstructed = reconstruct_existing_reading(azure_client, model, raw_text=review_import["original_text"])
        current_title, current_content = _current_reading_content(reconstructed)
        current_row = _materialize_reading_row(
            client, analysis_session_id, title=current_title, content=current_content,
            reading_role="CURRENT", rank=1, actor_id=actor_id)

        generated_alternatives = generate_alternative_readings_for_review(
            azure_client, model, report_context=report_context, current_reading_title=current_title,
            current_reading_why_this_matters=current_content["why_this_matters"])
        next_rank = 2
        for generated in generated_alternatives:
            alt_title, alt_content = _alternative_reading_content(generated)
            _materialize_reading_row(
                client, analysis_session_id, title=alt_title, content=alt_content,
                reading_role="ALTERNATIVE", rank=next_rank, actor_id=actor_id)
            next_rank += 1

        client.update("geo_ios_review_imports", {"analysis_session_id": f"eq.{analysis_session_id}"}, {
            "reconstructed_reading": reconstructed, "status": "readings_ready",
            "current_reading_scenario_candidate_id": current_row["scenario_candidate_id"],
        })
    except Exception as e:  # noqa: BLE001 — 失敗を記録し、フロントがポーリングで検知できるようにする
        client.update("geo_ios_review_imports", {"analysis_session_id": f"eq.{analysis_session_id}"},
                      {"status": "failed", "error_message": f"{type(e).__name__}: {e}"})
        raise

    return get_review_import(client, analysis_session_id)


def set_revision_strategy(client, analysis_session_id: str, *, selected_scenario_candidate_id: str,
                           strategy: str, actor_id: str) -> dict:
    if strategy not in REVIEW_REVISION_STRATEGIES:
        raise ReviewImportError(f"未対応のrevision_strategyです: {strategy}")
    review_import = get_review_import(client, analysis_session_id)
    if not review_import:
        raise ReviewImportError("review_importがまだ作成されていません")
    client.update("geo_ios_review_imports", {"analysis_session_id": f"eq.{analysis_session_id}"}, {
        "selected_scenario_candidate_id": selected_scenario_candidate_id, "revision_strategy": strategy,
    })
    geo_audit.log_audit(
        client, actor=actor_id, action="review_revision_strategy_set",
        target_table="geo_ios_review_imports", target_id=review_import["review_import_id"],
        detail={"strategy": strategy, "selected_scenario_candidate_id": selected_scenario_candidate_id})
    return get_review_import(client, analysis_session_id)
