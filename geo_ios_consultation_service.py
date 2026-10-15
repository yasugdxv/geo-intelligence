"""Geopolitical Intelligence OS: Consultation Turns（切り口相談/調査方針/プレレビュー）。

既存Analysis Design（geo_ios_analysis_designs、research_designモード専用でCQAS候補化を
伴う）とは別の対話単位。本サービスは「1ターン＝1回の既存Expert AI呼び出し」を
geo_ios_consultation_turnsへ永続化するだけで、Expert AI core・Analysis Designの既存
フィールドには一切書き込まない。

Expert AI呼び出しは実測1〜3分かかる（フルパワーの標準実行を維持する、というユーザー
判断のため簡略化しない）。呼び出し開始前にstatus='pending'で1行作成し、完了/失敗時に
呼び出し元（geo_ios_consultation_api.py、BackgroundTasks経由）がstatusを更新する——
Frontend側の一時状態（React Queryのmutation.isPending等）に依存せず、DBのstatusを
ポーリングするだけでページ離脱・再訪後も進捗を追跡できる。
"""
from datetime import datetime, timezone

import geo_audit

CONSULTATION_MODES = ("framing", "research_direction", "pre_review")

# 既存Expert AI mode（geo_expert_schema.MODES）への対応。新しいモード・Schemaは追加しない。
MODE_TO_EXPERT_MODE = {
    "framing": "research_design",
    "research_direction": "theme_design",
    "pre_review": "draft_review",
}


class ConsultationError(Exception):
    """Consultation Turnの操作エラー。"""


def create_pending_turn(client, analysis_session_id: str, *, consultation_mode: str,
                         user_message: str, draft_text: str | None, created_by: str) -> dict:
    if consultation_mode not in CONSULTATION_MODES:
        raise ConsultationError(
            f"未対応のconsultation_modeです: {consultation_mode}（許可: {CONSULTATION_MODES}）")
    if consultation_mode == "pre_review" and not (draft_text or "").strip():
        raise ConsultationError("pre_reviewモードにはdraft_textが必須です")
    if not (user_message or "").strip():
        raise ConsultationError("user_messageが空です")

    row = {
        "analysis_session_id": analysis_session_id, "consultation_mode": consultation_mode,
        "user_message": user_message, "draft_text": draft_text, "status": "pending",
        "created_by": created_by,
    }
    created = client.insert("geo_ios_consultation_turns", [row])[0]
    geo_audit.log_audit(
        client, actor=created_by, action="consultation_turn_create",
        target_table="geo_ios_consultation_turns", target_id=created["consultation_turn_id"],
        detail={"analysis_session_id": analysis_session_id, "consultation_mode": consultation_mode})
    return created


def get_turn(client, consultation_turn_id: str) -> dict | None:
    rows = client.select("geo_ios_consultation_turns", {
        "select": "*", "consultation_turn_id": f"eq.{consultation_turn_id}", "limit": "1"})
    return rows[0] if rows else None


def list_turns(client, analysis_session_id: str, *, consultation_mode: str) -> list:
    return client.select("geo_ios_consultation_turns", {
        "select": "*", "analysis_session_id": f"eq.{analysis_session_id}",
        "consultation_mode": f"eq.{consultation_mode}", "order": "created_at.asc"})


def mark_completed(client, consultation_turn_id: str, *, expert_request_id: str,
                    expert_response_id: str) -> dict:
    client.update("geo_ios_consultation_turns", {"consultation_turn_id": f"eq.{consultation_turn_id}"}, {
        "status": "completed", "expert_request_id": expert_request_id,
        "expert_response_id": expert_response_id,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    })
    return get_turn(client, consultation_turn_id)


def mark_failed(client, consultation_turn_id: str, *, error_message: str) -> dict:
    client.update("geo_ios_consultation_turns", {"consultation_turn_id": f"eq.{consultation_turn_id}"}, {
        "status": "failed", "error_message": (error_message or "")[:2000],
        "completed_at": datetime.now(timezone.utc).isoformat(),
    })
    return get_turn(client, consultation_turn_id)


def build_answer_summary(response_json: dict) -> dict:
    """完了したTurnをChat上に表示するための要約。モードによらず存在するfield
    （one_line_assessment/executive_summary）のみを使い、モード別の新しい要約生成
    （新しい推論）はしない。review_findingsは件数のみ添える（詳細は既存geo.expert.tsx等の
    Classic画面で見る想定、Chat上では全文を展開しない）。"""
    executive_summary = response_json.get("executive_summary") or {}
    return {
        "one_line_assessment": response_json.get("one_line_assessment"),
        "what_changed": executive_summary.get("what_changed"),
        "why_it_matters": executive_summary.get("why_it_matters"),
        "what_to_watch_decide": executive_summary.get("what_to_watch_decide"),
        "review_findings_count": len(response_json.get("review_findings") or []),
    }
