"""Report Studio Phase5: Intelligence Loop Integration。

Continuous Intelligence（Synthesisのassessment_delta/watchpoint_updates）を
Monitoring（geo_events）とIntelligence Needs（geo_knowledge_gaps）へ選択的に昇格する。

Report Studioの結果を単にMonitoring画面へコピーするのではなく、**意味のある変化だけを
昇格させる**という原則のため、昇格ルールはすべて決定的（LLM呼び出しを追加しない、
Synthesis自体が既に生成したevidence_basis/confidence/change_type/statusを機械的に
評価するのみ）。3段階:

  Update            : 意味のある変化（change_type≠Stable/Insufficient_evidence）
                      → geo_events（Monitoring timeline、event_type="report_studio_update"）
  Alert             : Watchpoint発火（status=Triggered）
                      → geo_events（event_type="report_studio_watchpoint_alert"、
                      confidenceを高めに設定してUI側で強調表示できるようにする）
  Intelligence Need : 追加調査が必要な場合のみ（何でも自動生成しない）
                      → geo_knowledge_gaps（origin_type=external_app_request、
                      requested_by_app=report_studio）。既存のgeo_knowledge_gap_service.
                      find_duplicate_candidateを再利用して重複を防ぐ。

Monitoring側はTheme単位（geo_themes.theme_id必須）で構成されているため、Report Studio
プロジェクトごとにTheme 1件を自動でget-or-createする（project_code起点、既存の
get_or_create_project等と同じ冪等パターン）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

import geo_audit
import geo_knowledge_gap_service as gap_service

# --- 昇格ルール（決定的、ここが「何を意味のある変化とみなすか」の唯一の定義） ---

UPDATE_CHANGE_TYPES = {"Intensified", "Weakened", "Reframed", "New", "Resolved"}
ALERT_WATCHPOINT_STATUSES = {"Triggered"}
INTELLIGENCE_NEED_WATCHPOINT_STATUSES = {"Possible_trigger"}
LOW_CONFIDENCE_VALUES = {"Low", "判断不能"}


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# 1. Update / Alert 選定（assessment_delta / watchpoint_updates）
# ---------------------------------------------------------------------------

def select_updates(final_draft: dict) -> list:
    """意味のある変化のみ（Stable/Insufficient_evidenceは昇格しない、
    件数増減ではなくchange_type自体で判定——Synthesis側が既にそう判定済みのものを
    そのまま使う、ここで独自に再解釈しない）。"""
    return [
        item for item in (final_draft.get("assessment_delta") or [])
        if item.get("change_type") in UPDATE_CHANGE_TYPES
    ]


def select_alerts(final_draft: dict) -> list:
    """Watchpointが実際にTriggeredになったもののみ（Possible_triggerはAlertにしない、
    Intelligence Need候補として別途扱う）。"""
    return [
        item for item in (final_draft.get("watchpoint_updates") or [])
        if item.get("status") in ALERT_WATCHPOINT_STATUSES
    ]


# ---------------------------------------------------------------------------
# 2. Intelligence Need候補選定（ユーザー指定5条件、何でも自動生成しない）
# ---------------------------------------------------------------------------

def select_intelligence_need_candidates(final_draft: dict, narrative_row_data: dict) -> list:
    """5条件に該当するものだけを候補として返す。各候補にreasonを付け、
    どの条件で生成されたかを常に追跡できるようにする（安易な自動生成の温床にしない）。"""
    candidates = []
    is_high_priority_narrative = str(narrative_row_data.get("report_priority", "")).lower() == "high"

    central = final_draft.get("central_assessment") or {}
    continuity = final_draft.get("continuity_assessment") or {}

    # 条件1: central assessmentを左右し得る情報欠落（confidence低 + continuity=Insufficient evidence）
    if central.get("confidence") in LOW_CONFIDENCE_VALUES and continuity.get("state") == "Insufficient evidence":
        candidates.append({
            "reason": "central_assessment_confidence_low_insufficient_evidence",
            "detail": central.get("summary") or "",
            "priority": "high" if is_high_priority_narrative else "medium",
        })

    # 条件2: Watchpoint が Possible Trigger 止まり
    for w in final_draft.get("watchpoint_updates") or []:
        if w.get("status") in INTELLIGENCE_NEED_WATCHPOINT_STATUSES:
            candidates.append({
                "reason": "watchpoint_possible_trigger",
                "detail": w.get("watchpoint") or "",
                "priority": "medium",
            })

    # 条件3/4/5: Findingのうちconfidence低・かつ重要narrativeで、counterevidenceが
    # 全く無い、またはExpert Context（Actorの意図解釈）が全く無いもの
    for finding in final_draft.get("findings") or []:
        provenance = finding.get("provenance") or {}
        confidence_low = finding.get("confidence") in LOW_CONFIDENCE_VALUES
        no_counterevidence = not provenance.get("counterevidence_record_ids")
        no_expert_context = not provenance.get("expert_context_refs")
        if confidence_low and is_high_priority_narrative and (no_counterevidence or no_expert_context):
            reason = "missing_counterevidence" if no_counterevidence and not no_expert_context else (
                "actor_intent_undeterminable" if no_expert_context and not no_counterevidence else
                "missing_counterevidence_and_actor_context")
            candidates.append({
                "reason": reason,
                "detail": finding.get("statement") or "",
                "priority": "medium",
            })

    return candidates


# ---------------------------------------------------------------------------
# 3. Theme紐付け（Report StudioプロジェクトごとにMonitoring Themeを自動確保）
# ---------------------------------------------------------------------------

def get_or_create_theme_for_project(client, project: dict) -> dict:
    code = f"report_studio_{project['project_code']}"
    rows = client.select("geo_themes", {"select": "*", "code": f"eq.{code}"})
    if rows:
        return rows[0]
    theme_id = _new_id()
    inserted = client.insert("geo_themes", [{
        "theme_id": theme_id, "code": code, "name_ja": project["project_title"],
        "description": "Report Studioから自動生成されたMonitoring Theme", "status": "有効",
    }])
    return inserted[0] if inserted else {
        "theme_id": theme_id, "code": code, "name_ja": project["project_title"], "status": "有効",
    }


# ---------------------------------------------------------------------------
# 4. Monitoring（geo_events）への書き込み（冪等: 同一synthesis_id+項目の再投稿を防ぐ）
# ---------------------------------------------------------------------------

def _event_already_exists(client, theme_id: str, synthesis_id: str, item_key: str) -> bool:
    """テーマ内の既存eventをPython側でフィルタする（PostgRESTのJSONパス演算子
    unknown_fields->>key=eq.value はFakeSupabaseClientが対応していない決定的処理の
    テスト容易性を優先し、実運用でも件数が少ない前提で許容できる範囲のスキャンにする）。"""
    existing = client.select("geo_events", {"select": "event_id,unknown_fields", "theme_id": f"eq.{theme_id}"})
    for row in existing:
        fields = row.get("unknown_fields") or {}
        if fields.get("report_studio_synthesis_id") == synthesis_id and fields.get("report_studio_item_key") == item_key:
            return True
    return False


def _create_event(client, *, theme_id: str, item_key: str, event_type: str,
                   actor: Optional[str], action: str, confidence: Optional[float],
                   source_ref: dict) -> Optional[dict]:
    synthesis_id = source_ref["synthesis_id"]
    if _event_already_exists(client, theme_id, synthesis_id, item_key):
        return None
    unknown_fields = {"report_studio_item_key": item_key, **{f"report_studio_{k}": v for k, v in source_ref.items()}}
    inserted = client.insert("geo_events", [{
        "theme_id": theme_id, "actor": actor, "action": action, "event_type": event_type,
        "confidence": confidence, "verified": False, "unknown_fields": unknown_fields,
    }])
    return inserted[0] if inserted else None


def promote_updates_and_alerts(client, *, theme_id: str, narrative_name: str,
                                updates: list, alerts: list, source_ref: dict) -> dict:
    created_updates, created_alerts = [], []
    for i, item in enumerate(updates):
        event = _create_event(
            client, theme_id=theme_id, item_key=f"update[{i}]",
            event_type="report_studio_update", actor=narrative_name,
            action=f"[{item.get('change_type')}] {item.get('item')}", confidence=0.6, source_ref=source_ref)
        if event:
            created_updates.append(event)
    for i, item in enumerate(alerts):
        event = _create_event(
            client, theme_id=theme_id, item_key=f"alert[{i}]",
            event_type="report_studio_watchpoint_alert", actor=narrative_name,
            action=f"[Triggered] {item.get('watchpoint')}", confidence=0.85, source_ref=source_ref)
        if event:
            created_alerts.append(event)
    return {"updates": created_updates, "alerts": created_alerts}


# ---------------------------------------------------------------------------
# 5. Intelligence Needs（geo_knowledge_gaps）への書き込み
# ---------------------------------------------------------------------------

def create_gap_from_report_studio(
    client, *, project: dict, narrative_row_data: dict, candidate: dict, source_ref: dict,
    actor_id: str = "report_studio",
) -> Optional[dict]:
    """既存geo_knowledge_gap_service.find_duplicate_candidateで重複を確認したうえで
    geo_knowledge_gapsへ登録する。request_id/geo_intelligence_requestsとの紐付けは
    行わない（Report StudioにはExternal Request側の概念が無いため）。source_refは
    source_context列（Deep Link用、sql/2026-09-02_geo_report_studio_deep_link_schema.sql）
    へ格納する。"""
    topic = [narrative_row_data.get("report_topic_label")] if narrative_row_data.get("report_topic_label") else []
    question = f"{project['project_title']} / {narrative_row_data.get('narrative_name')}: {candidate['detail']}"

    duplicate = gap_service.find_duplicate_candidate(client, country_region=[], topic=topic, question=question)
    if duplicate and duplicate.get("action") == "merge":
        gap = duplicate["gap"]
        client.update("geo_knowledge_gaps", {"gap_id": f"eq.{gap['gap_id']}"}, {
            "request_count": (gap.get("request_count") or 1) + 1,
            "source_context": {"app": "report_studio", **source_ref},
        })
        geo_audit.log_audit(client, actor=actor_id, action="knowledge_gap_merge_report_studio",
                             target_table="geo_knowledge_gaps", target_id=gap["gap_id"],
                             detail={"reason": candidate["reason"]})
        return gap

    description = (
        f"[Report Studio] {project['project_title']} / {narrative_row_data.get('narrative_name')}: "
        f"{candidate['detail']}（理由: {candidate['reason']}）"
    )
    row = {
        "case_id": None, "origin_type": "external_app_request",
        "gap_category": "deterministic", "gap_type": "external_request_knowledge_gap",
        "gap_description": description, "why_needed": candidate["detail"],
        "requested_by_app": "report_studio", "request_type": candidate["reason"],
        "question": question, "country_region": [], "topic": topic,
        "business_context": project["project_title"],
        "priority": candidate.get("priority", "medium"), "status": "open", "request_count": 1,
        "source_context": {"app": "report_studio", **source_ref},
    }
    created = client.insert("geo_knowledge_gaps", [row])[0]
    geo_audit.log_audit(client, actor=actor_id, action="knowledge_gap_create_report_studio",
                         target_table="geo_knowledge_gaps", target_id=created["gap_id"],
                         detail={"reason": candidate["reason"]})
    return created


# ---------------------------------------------------------------------------
# 6. オーケストレーション
# ---------------------------------------------------------------------------

def promote_synthesis(client, *, project: dict, narrative_row_data: dict, synthesis: dict) -> dict:
    """1件のSynthesis結果（geo_report_synthesesの1行）を評価し、Update/Alert/
    Intelligence Needへ選択的に昇格する。戻り値は昇格件数のサマリ。synthesisには
    Deep Link用にrun_id/signal_id/narrative_idも含めること（呼び出し元の責務）。"""
    final_draft = synthesis["final_draft"]
    theme = get_or_create_theme_for_project(client, project)
    source_ref = {
        "project_id": project["project_id"], "run_id": synthesis.get("run_id"),
        "signal_id": synthesis.get("signal_id"), "narrative_id": synthesis["narrative_id"],
        "synthesis_id": synthesis["id"],
    }

    updates = select_updates(final_draft)
    alerts = select_alerts(final_draft)
    need_candidates = select_intelligence_need_candidates(final_draft, narrative_row_data)

    monitoring_result = promote_updates_and_alerts(
        client, theme_id=theme["theme_id"],
        narrative_name=narrative_row_data.get("narrative_name") or synthesis["narrative_id"],
        updates=updates, alerts=alerts, source_ref=source_ref)

    created_needs = []
    for candidate in need_candidates:
        gap = create_gap_from_report_studio(
            client, project=project, narrative_row_data=narrative_row_data, candidate=candidate,
            source_ref=source_ref)
        if gap:
            created_needs.append(gap)

    return {
        "theme_id": theme["theme_id"],
        "updates_evaluated": len(updates), "updates_created": len(monitoring_result["updates"]),
        "alerts_evaluated": len(alerts), "alerts_created": len(monitoring_result["alerts"]),
        "intelligence_need_candidates": len(need_candidates), "intelligence_needs_created": len(created_needs),
    }
