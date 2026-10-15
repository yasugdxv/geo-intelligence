"""Report Studio Phase6: Closed-loop Reassessment。

Intelligence Need（Report Studio起源、geo_report_monitoring_service.
create_gap_from_report_studioが生成したもの）がNew EvidenceでResolveされた際、その
発生源となったReport（run_id/signal_id/narrative_id）を自動で再評価するかどうかを判定する
Gate。

毎回フルSynthesis（LLM 2回、Stage相当のToken消費）を再実行するのはコストが高いため、
まず安価な1回のLLM呼び出しで「新しいEvidenceが現在のCentral Assessmentを意味的に変える
か」だけを判定する（materiality check）。
  - No  : Continuity update only。新規DB書き込みは行わず、geo_auditへ「再評価したが
          重大な変化なし」という監査ログのみ記録する（既存のcontinuity_assessmentは
          次回の通常Run/Synthesis実行時に自然に更新されるため、ここで先取りして
          別テーブルへ書き込む必要はない）。
  - Yes : 既存のフルSynthesis+Expert Reviewパス（geo_report_studio_api.
          run_synthesize_narrative）を再実行する。これは既存のAnti-Backflow検証・
          Monitoring/Intelligence Needsへの昇格処理までそのまま流れる。

トリガー条件は「Intelligence Need（Report Studio起源）が新しいEvidenceで解消された」
場合のみに限定する。Triggered watchpoint/High-priority narrative変化そのものは、既存の
Synthesis自体がassessment_delta/watchpoint_updatesとして生成し、Phase4/5で既に
Monitoring/Intelligence Needsへ選択的昇格される仕組みが完成しているため、本Gateの
対象ではない（二重の仕組みを作らない）。
"""
from __future__ import annotations

from typing import Callable, Optional

import geo_audit
import geo_llm_common as llm_common

_MATERIALITY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["materially_changes_assessment", "rationale", "confidence"],
    "properties": {
        "materially_changes_assessment": {
            "type": "boolean",
            "description": "新しいEvidenceが現在のCentral Assessment（結論・確信度・"
                           "Continuity状態）を意味のある形で変え得るか。件数の増減や"
                           "表現の言い換えだけでは変化とみなさない。判断できない場合は"
                           "falseにし、rationaleでその理由を明示する。",
        },
        "rationale": {"type": "string"},
        "confidence": {"type": "string", "enum": ["高", "中", "低", "判断不能"]},
    },
}

_MATERIALITY_SYSTEM_PROMPT = (
    "あなたは地政学インテリジェンスのReassessment Gate判定者です。既存のCentral "
    "Assessmentに対し、新しく解消されたIntelligence Needの情報が意味のある変化を"
    "もたらすかどうかだけを判定してください。件数の増減や既存主張の言い換えだけでは"
    "変化とみなさない。Assessment自体の結論・確信度・Continuity状態を変え得る場合の"
    "みtrueにしてください。判断できない場合はfalse寄りにし、rationaleでその理由を"
    "明示してください（過剰反応でフルSynthesisを再実行し続けることを避けるため）。"
)


def is_report_studio_sourced(gap: dict) -> bool:
    """このIntelligence NeedがReport Studio起源で、Deep Link用の参照情報
    （project_id/run_id/signal_id/narrative_id）が揃っているかを確認する。
    揃っていなければReassessment Gate自体を発火させない。"""
    ctx = gap.get("source_context") or {}
    return (
        gap.get("requested_by_app") == "report_studio"
        and ctx.get("app") == "report_studio"
        and bool(ctx.get("project_id")) and bool(ctx.get("run_id"))
        and bool(ctx.get("signal_id")) and bool(ctx.get("narrative_id"))
    )


def run_materiality_check(azure_client, model: str, *, prior_central_assessment: str,
                           prior_continuity_state: str, new_evidence_summary: str) -> dict:
    user_prompt = (
        f"## 現在のCentral Assessment\n{prior_central_assessment or '(空)'}\n\n"
        f"## 現在のContinuity状態\n{prior_continuity_state or '(空)'}\n\n"
        f"## 新しく解消されたIntelligence Needの内容\n{new_evidence_summary or '(空)'}\n"
    )
    result = llm_common.call_llm_structured(
        azure_client, model, _MATERIALITY_SYSTEM_PROMPT, user_prompt,
        _MATERIALITY_SCHEMA, "report_studio_reassessment_materiality_check", max_attempts=2)
    return result["data"]


def run_reassessment_gate(
    client, azure_client, model, *, gap: dict, new_evidence_summary: str,
    get_latest_synthesis: Callable[[object, str, str, str], Optional[dict]],
    run_synthesize_narrative: Callable[[str, str, str], dict],
    actor_id: str = "report_studio_reassessment_gate",
) -> dict:
    """Need解消をトリガーに、元Reportの再評価要否を判定し必要なら再実行する。

    呼び出し元（Intelligence Need解消エンドポイント）の応答を壊さないよう、内部の
    どの段階で失敗しても例外を外へ投げず、戻り値のstatusで結果を判断できるようにする
    （resolve自体はReassessment Gateの成否と無関係に成立済みであるべきため）。

    get_latest_synthesis/run_synthesize_narrativeは呼び出し元（geo_intelligence_gap_api）
    から注入する。本サービスはgeo_report_studio_api/geo_report_studio_serviceを直接
    importしない（API層のモジュールをservice層から参照する逆依存を作らないため）。
    """
    ctx = gap.get("source_context") or {}
    run_id = ctx.get("run_id")
    signal_id = ctx.get("signal_id")
    narrative_id = ctx.get("narrative_id")

    try:
        prior_synthesis = get_latest_synthesis(client, run_id, signal_id, narrative_id)
        if not prior_synthesis:
            geo_audit.log_audit(
                client, actor=actor_id, action="report_studio_reassessment_skipped",
                target_table="geo_knowledge_gaps", target_id=gap.get("gap_id"),
                detail={"reason": "no_prior_synthesis", "run_id": run_id,
                        "signal_id": signal_id, "narrative_id": narrative_id})
            return {"status": "skipped", "reason": "no_prior_synthesis"}

        prior_draft = prior_synthesis.get("final_draft") or {}
        materiality = run_materiality_check(
            azure_client, model,
            prior_central_assessment=(prior_draft.get("central_assessment") or {}).get("summary", ""),
            prior_continuity_state=(prior_draft.get("continuity_assessment") or {}).get("state", ""),
            new_evidence_summary=new_evidence_summary,
        )

        if not materiality.get("materially_changes_assessment"):
            geo_audit.log_audit(
                client, actor=actor_id, action="report_studio_reassessment_no_material_change",
                target_table="geo_report_syntheses", target_id=prior_synthesis.get("id"),
                detail={"gap_id": gap.get("gap_id"), "rationale": materiality.get("rationale"),
                        "confidence": materiality.get("confidence")})
            return {"status": "continuity_only", "rationale": materiality.get("rationale"),
                    "confidence": materiality.get("confidence")}

        result = run_synthesize_narrative(run_id, signal_id, narrative_id)
        geo_audit.log_audit(
            client, actor=actor_id, action="report_studio_reassessment_resynthesized",
            target_table="geo_report_syntheses", target_id=result.get("synthesis_id"),
            detail={"gap_id": gap.get("gap_id"), "rationale": materiality.get("rationale"),
                    "final_validity_status": result.get("final_validity_status")})
        return {
            "status": "resynthesized", "synthesis_id": result.get("synthesis_id"),
            "final_validity_status": result.get("final_validity_status"),
            "rationale": materiality.get("rationale"),
        }
    except Exception as exc:  # noqa: BLE001 — resolve応答を壊さないため意図的に握りつぶす
        geo_audit.log_audit(
            client, actor=actor_id, action="report_studio_reassessment_error",
            target_table="geo_knowledge_gaps", target_id=gap.get("gap_id"),
            detail={"error": str(exc)})
        return {"status": "error", "error": str(exc)}


def build_new_evidence_summary(*, resolution_note: Optional[str], document_title: Optional[str],
                                document_text: Optional[str], char_budget: int = 2000) -> str:
    """Resolve時に渡されたReportから、Materiality Check用の新Evidence要約テキストを作る。
    Analyst自身の言葉であるresolution_noteを主として扱い、Report本文は判断材料として
    後ろへ添える（本文のみで安易に「意図」を読み取らせない、既存Anti-Backflow思想を踏襲）。"""
    parts = []
    if resolution_note:
        parts.append(f"解消時のAnalystコメント: {resolution_note}")
    if document_title:
        parts.append(f"根拠Reportのタイトル: {document_title}")
    if document_text:
        parts.append(f"根拠Reportの本文(冒頭抜粋): {document_text[:char_budget]}")
    return "\n\n".join(parts) if parts else ""
