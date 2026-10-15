"""Geopolitical Intelligence OS Phase B: Expert Review Workflow（§7-12、無制限ラウンド）。

既存geo_expert_review_service.py（Internal Expert Learning Layer、Draft-vs-Final単発ペア）とは
別の仕組み。こちらは「Report Studioの特定Versionに対する複数ラウンドの人手レビュー＋Anchor付き
コメント」を扱う。Report Studio内部（geo_report_studio_service.py等、2026-09-16時点で本番
インシデント対応中）のテーブル・関数は一切呼ばない——version_snapshotは呼び出し元（API層）が
既存の公開関数（例: report_studio_service.get_latest_synthesis）経由で組み立てた辞書をそのまま
受け取って複製するだけで、本モジュール自身はReport Studioの内部構造を知らない。
"""
from datetime import datetime, timezone

import geo_audit

REVIEW_CYCLE_STATUSES = ("requested", "in_review", "completed", "superseded")
REVIEW_RESULTS = ("approved", "revision_requested", "rejected")
COMMENT_RESOLUTIONS = ("open", "resolved", "partially_resolved", "rejected", "not_applicable")
ANCHOR_TYPES = ("section", "paragraph", "claim", "evidence", "general")


class ReviewWorkflowError(Exception):
    """Review Cycle/Commentの操作エラー。"""


# ---- Review Cycle（§8, §11） ------------------------------------------------

def _next_round_number(client, report_project_id: str) -> int:
    existing = client.select("geo_ios_review_cycles", {
        "select": "round_number", "report_project_id": f"eq.{report_project_id}",
        "order": "round_number.desc", "limit": "1"})
    return (existing[0]["round_number"] + 1) if existing else 1


def request_review_cycle(client, *, report_project_id: str, reviewer: str, requested_by: str,
                          version_label: str = None, version_snapshot: dict = None,
                          analysis_session_id: str = None, message: str = None,
                          due_date: str = None, actor_id: str = None,
                          source_report_draft_id: str = None, source_draft_version: int = None,
                          source_content_hash: str = None) -> dict:
    """Review Request時点のReport VersionをSnapshotとして固定する（§8）。回数制限は設けない
    （round_numberはreport_project_idごとに単調増加、§11）。

    IOS Native Draft: source_report_draft_id/source_draft_version/source_content_hashは、
    Native経路（呼び出し元geo_ios_review_api.pyがgeo_ios_report_draft_service.
    build_native_review_snapshot経由で組み立てた場合のみ）指定される。Report Studio経路
    （legacy）では常にNoneのままで、既存version_snapshotのみで完結する。"""
    round_number = _next_round_number(client, report_project_id)
    row = {
        "report_project_id": report_project_id, "analysis_session_id": analysis_session_id,
        "round_number": round_number, "reviewer": reviewer, "message": message,
        "reviewed_version_label": version_label, "version_snapshot": version_snapshot,
        "requested_by": requested_by, "due_date": due_date, "status": "requested",
        "source_report_draft_id": source_report_draft_id,
        "source_draft_version": source_draft_version, "source_content_hash": source_content_hash,
    }
    created = client.insert("geo_ios_review_cycles", [row])[0]
    geo_audit.log_audit(client, actor=actor_id or requested_by, action="review_cycle_request",
                         target_table="geo_ios_review_cycles", target_id=created["review_cycle_id"],
                         detail={"report_project_id": report_project_id, "round_number": round_number,
                                 "reviewer": reviewer})
    return created


def get_review_cycle(client, review_cycle_id: str) -> dict | None:
    rows = client.select("geo_ios_review_cycles", {
        "select": "*", "review_cycle_id": f"eq.{review_cycle_id}", "limit": "1"})
    return rows[0] if rows else None


def list_review_cycles(client, report_project_id: str) -> list:
    return client.select("geo_ios_review_cycles", {
        "select": "*", "report_project_id": f"eq.{report_project_id}", "order": "round_number.asc"})


def _require_cycle(client, review_cycle_id: str) -> dict:
    cycle = get_review_cycle(client, review_cycle_id)
    if not cycle:
        raise ReviewWorkflowError(f"review_cycle_idが見つかりません: {review_cycle_id}")
    return cycle


def start_review(client, review_cycle_id: str, *, actor_id: str) -> dict:
    cycle = _require_cycle(client, review_cycle_id)
    if cycle["status"] != "requested":
        raise ReviewWorkflowError(f"status={cycle['status']}からはstart_reviewできません（requestedのみ許可）")
    client.update("geo_ios_review_cycles", {"review_cycle_id": f"eq.{review_cycle_id}"},
                  {"status": "in_review"})
    geo_audit.log_audit(client, actor=actor_id, action="review_cycle_start",
                         target_table="geo_ios_review_cycles", target_id=review_cycle_id, detail={})
    return get_review_cycle(client, review_cycle_id)


def submit_review_findings(client, review_cycle_id: str, *, findings: dict, result: str,
                            reviewer: str, actor_id: str = None) -> dict:
    """§7の構造化Finding（critical_findings/major_findings/minor_findings/suggested_revision/
    evidence_gap/alternative_interpretation等）を保存し、レビューを完了させる。Report本文は
    ここでは一切書き換えない（§7: Apply/Apply selected/Rejectは別途Author側の操作）。"""
    if result not in REVIEW_RESULTS:
        raise ReviewWorkflowError(f"未対応のresultです: {result}（許可: {REVIEW_RESULTS}）")
    cycle = _require_cycle(client, review_cycle_id)
    if cycle["status"] == "completed":
        raise ReviewWorkflowError("既に完了済みのreview_cycleです")

    client.update("geo_ios_review_cycles", {"review_cycle_id": f"eq.{review_cycle_id}"}, {
        "findings": findings, "result": result, "status": "completed",
        "completed_at": datetime.now(timezone.utc).isoformat(),
    })
    geo_audit.log_audit(client, actor=actor_id or reviewer, action="review_cycle_submit",
                         target_table="geo_ios_review_cycles", target_id=review_cycle_id,
                         detail={"result": result})
    return get_review_cycle(client, review_cycle_id)


def record_author_response(client, review_cycle_id: str, *, author_response: str, actor_id: str) -> dict:
    _require_cycle(client, review_cycle_id)
    client.update("geo_ios_review_cycles", {"review_cycle_id": f"eq.{review_cycle_id}"},
                  {"author_response": author_response})
    geo_audit.log_audit(client, actor=actor_id, action="review_cycle_author_response",
                         target_table="geo_ios_review_cycles", target_id=review_cycle_id, detail={})
    return get_review_cycle(client, review_cycle_id)


# ---- Review Comment（§10, §12） ---------------------------------------------

def add_comment(client, review_cycle_id: str, *, comment: str, created_by: str,
                 anchor_type: str = None, anchor_id: str = None, severity: str = None,
                 actor_id: str = None) -> dict:
    if anchor_type is not None and anchor_type not in ANCHOR_TYPES:
        raise ReviewWorkflowError(f"未対応のanchor_typeです: {anchor_type}（許可: {ANCHOR_TYPES}）")
    cycle = _require_cycle(client, review_cycle_id)
    row = {
        "review_cycle_id": review_cycle_id, "report_project_id": cycle["report_project_id"],
        "anchor_type": anchor_type, "anchor_id": anchor_id, "comment": comment,
        "severity": severity, "created_by": created_by, "resolved_status": "open",
    }
    created = client.insert("geo_ios_review_comments", [row])[0]
    geo_audit.log_audit(client, actor=actor_id or created_by, action="review_comment_add",
                         target_table="geo_ios_review_comments", target_id=created["review_comment_id"],
                         detail={"review_cycle_id": review_cycle_id})
    return created


def list_comments(client, review_cycle_id: str) -> list:
    return client.select("geo_ios_review_comments", {
        "select": "*", "review_cycle_id": f"eq.{review_cycle_id}", "order": "created_at.asc"})


def respond_to_comment(client, review_comment_id: str, *, resolution_status: str,
                        author_response: str = None, resolved_in_version: str = None,
                        actor_id: str) -> dict:
    """§12: コメント削除で対応履歴を消さない——resolved_statusの更新のみを提供し、削除APIは
    提供しない。"""
    if resolution_status not in COMMENT_RESOLUTIONS:
        raise ReviewWorkflowError(
            f"未対応のresolution_statusです: {resolution_status}（許可: {COMMENT_RESOLUTIONS}）")
    rows = client.select("geo_ios_review_comments", {
        "select": "*", "review_comment_id": f"eq.{review_comment_id}", "limit": "1"})
    if not rows:
        raise ReviewWorkflowError(f"review_comment_idが見つかりません: {review_comment_id}")

    patch = {"resolved_status": resolution_status, "author_response": author_response,
             "resolved_in_version": resolved_in_version}
    if resolution_status != "open":
        patch["resolved_at"] = datetime.now(timezone.utc).isoformat()
    client.update("geo_ios_review_comments", {"review_comment_id": f"eq.{review_comment_id}"}, patch)
    geo_audit.log_audit(client, actor=actor_id, action="review_comment_respond",
                         target_table="geo_ios_review_comments", target_id=review_comment_id,
                         detail={"resolution_status": resolution_status})
    rows = client.select("geo_ios_review_comments", {
        "select": "*", "review_comment_id": f"eq.{review_comment_id}", "limit": "1"})
    return rows[0]
