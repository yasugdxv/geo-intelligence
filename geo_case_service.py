"""地政学ナレッジ＆インテリジェンスDB: Case作成・更新、Hypothesis管理（9章）。"""
from datetime import datetime, timezone

import geo_audit

HYPOTHESIS_TYPES = ("main", "alternative", "rejected", "superseded")
HYPOTHESIS_STATUSES = ("open", "confirmed", "rejected", "superseded")


def create_case(client, *, case_title: str, original_question: str, actor_id: str,
                 scope: str = None, analyst: str = None, is_demo: bool = False) -> dict:
    row = {
        "case_title": case_title, "original_question": original_question,
        "scope": scope, "analyst": analyst or actor_id, "is_demo": is_demo,
    }
    created = client.insert("geo_cases", [row])[0]
    geo_audit.log_audit(client, actor=actor_id, action="case_create",
                         target_table="geo_cases", target_id=created["case_id"],
                         detail={"case_title": case_title})
    return created


def get_case(client, case_id: str) -> dict | None:
    rows = client.select("geo_cases", {"select": "*", "case_id": f"eq.{case_id}", "limit": "1"})
    return rows[0] if rows else None


def list_cases(client, *, status: str = None, include_demo: bool = False, limit: int = 50) -> list:
    params = {"select": "*", "order": "created_at.desc", "limit": str(limit)}
    if status:
        params["status"] = f"eq.{status}"
    if not include_demo:
        params["is_demo"] = "eq.false"
    return client.select("geo_cases", params)


def link_case_to_issue(client, *, case_id: str, issue_id: str) -> dict:
    return client.insert("geo_case_issues", [{"case_id": case_id, "issue_id": issue_id}])[0]


def link_case_to_entity(client, *, case_id: str, entity_id: str, issue_id: str) -> dict:
    """CaseとEntityは共通のissue_idを介して紐付ける（geo_case_issues/geo_entity_issuesが
    同一issue_idを共有することで、Foundation Pack生成時に関連Entityを引ける）。"""
    return client.insert("geo_entity_issues", [{"entity_id": entity_id, "issue_id": issue_id}])[0]


def close_case(client, *, case_id: str, actor_id: str, final_output: dict = None,
               assessment: str = None, business_implications: str = None) -> dict:
    # SupabaseClient.update はpatchをそのままJSONとして送るため、DB側のnow()は使えず
    # アプリ側でISO8601のclosed_atを積む。
    patch = {"status": "closed", "closed_at": datetime.now(timezone.utc).isoformat()}
    if final_output is not None:
        patch["final_output"] = final_output
    if assessment is not None:
        patch["assessment"] = assessment
    if business_implications is not None:
        patch["business_implications"] = business_implications
    client.update("geo_cases", {"case_id": f"eq.{case_id}"}, patch)
    geo_audit.log_audit(client, actor=actor_id, action="case_close",
                         target_table="geo_cases", target_id=case_id, detail={})
    return get_case(client, case_id)


def record_case_outcome(client, *, case_id: str, actor_id: str, actual_outcome: str,
                         post_case_evaluation: dict) -> dict:
    """事後評価。過去の仮説は削除せず、当時の情報条件とともに残す（19.4章）。"""
    client.update("geo_cases", {"case_id": f"eq.{case_id}"}, {
        "actual_outcome": actual_outcome, "post_case_evaluation": post_case_evaluation,
    })
    geo_audit.log_audit(client, actor=actor_id, action="case_record_outcome",
                         target_table="geo_cases", target_id=case_id, detail={})
    return get_case(client, case_id)


def create_hypothesis(client, *, case_id: str, hypothesis_statement: str, hypothesis_type: str,
                       actor_id: str, rationale: str = None, assumptions: list = None,
                       confirmation_indicators: list = None, falsification_indicators: list = None,
                       confidence: str = "判断不能") -> dict:
    if hypothesis_type not in HYPOTHESIS_TYPES:
        raise ValueError(f"未対応のhypothesis_typeです: {hypothesis_type}")
    row = {
        "case_id": case_id, "hypothesis_statement": hypothesis_statement,
        "hypothesis_type": hypothesis_type, "rationale": rationale,
        "assumptions": assumptions or [], "confirmation_indicators": confirmation_indicators or [],
        "falsification_indicators": falsification_indicators or [], "confidence": confidence,
    }
    created = client.insert("geo_case_hypotheses", [row])[0]
    geo_audit.log_audit(client, actor=actor_id, action="hypothesis_create",
                         target_table="geo_case_hypotheses", target_id=created["hypothesis_id"],
                         detail={"case_id": case_id, "hypothesis_type": hypothesis_type})
    return created


def attach_evidence_to_hypothesis(client, *, hypothesis_id: str, claim_id: str,
                                   stance: str) -> dict:
    """stance: 'supporting' または 'contradicting'。既存のsupporting/contradicting_claim_idsへ追記する。"""
    if stance not in ("supporting", "contradicting"):
        raise ValueError("stanceは'supporting'または'contradicting'を指定してください")
    rows = client.select("geo_case_hypotheses", {
        "select": "*", "hypothesis_id": f"eq.{hypothesis_id}", "limit": "1",
    })
    if not rows:
        raise ValueError(f"hypothesis_idが見つかりません: {hypothesis_id}")
    field = "supporting_claim_ids" if stance == "supporting" else "contradicting_claim_ids"
    current = rows[0].get(field) or []
    if claim_id not in current:
        current = current + [claim_id]
    client.update("geo_case_hypotheses", {"hypothesis_id": f"eq.{hypothesis_id}"}, {field: current})
    return client.select("geo_case_hypotheses", {
        "select": "*", "hypothesis_id": f"eq.{hypothesis_id}", "limit": "1",
    })[0]


def evaluate_hypothesis(client, *, hypothesis_id: str, status: str, evaluation_result: str,
                         actor_id: str) -> dict:
    if status not in HYPOTHESIS_STATUSES:
        raise ValueError(f"未対応のstatusです: {status}")
    client.update("geo_case_hypotheses", {"hypothesis_id": f"eq.{hypothesis_id}"}, {
        "status": status, "evaluation_result": evaluation_result,
    })
    geo_audit.log_audit(client, actor=actor_id, action="hypothesis_evaluate",
                         target_table="geo_case_hypotheses", target_id=hypothesis_id,
                         detail={"status": status})
    return client.select("geo_case_hypotheses", {
        "select": "*", "hypothesis_id": f"eq.{hypothesis_id}", "limit": "1",
    })[0]


def list_hypotheses(client, case_id: str) -> list:
    return client.select("geo_case_hypotheses", {"select": "*", "case_id": f"eq.{case_id}", "order": "created_at.asc"})
