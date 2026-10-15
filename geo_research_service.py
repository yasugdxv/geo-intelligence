"""Phase B-C: Intelligence AI / Expert AI Role Separation — 非同期Research実行。

geo_department_intelligence_service（Cross-Department Inquiry Receiver）は、Knowledge不足時に
既存geo_knowledge_gap_service.record_external_request_gapでGapを記録するだけに留め、Expert AI
（geo_expert_engine、1回あたり数分規模）をHTTP同期経路では呼ばない（Phase B-B4までの
`existing_plus_expert`/`expert_only`同期呼び出しは、明示的にsync_expert_enabled=Trueを指定
した場合のみ残す。Regression用途・将来の管理者/内部ユースケース向け）。

本モジュールはそのGapに対する非同期Researchを実際に実行する別エントリポイント
（run_research_request）。既存Expert AI呼び出し経路（geo_department_intelligence_service.
run_expert_for_missing_dimensions、_run_expert_ai_and_shape経由）はそのまま再利用し、新しい
Expert呼び出しロジックは作らない。責務は3つのみ:
    1. 同一Gapに対してExpert AIが重複実行されないようにする（Idempotency）
    2. Expert AI実行結果をgeo_knowledge_gaps.resolution_expert_response_idへ記録する
       （Research完了。Gap解決とは別概念、record_research_response参照）
    3. Expert AI結果をcandidate Knowledge Item（status="candidate"）として既存
       candidate→review→active lifecycleへ接続する（Human Review未了の間はGap未解決のまま）
"""
import geo_audit
import geo_department_intelligence_service as dept
import geo_knowledge_gap_service as gap_service
import geo_knowledge_service


class ResearchRequestError(Exception):
    """run_research_requestが実行できない/しない場合の例外（gap不在等の異常系のみ）。
    Idempotencyによる意図的なskipは例外にせず、戻り値のskipped=Trueで表現する。"""


# Researchを開始してよい（＝まだ未着手の）Gap status。本番実データで判明した不具合の修正:
# "open"だけでなく"duplicate_candidate"（重複候補としてflagされたが、人間がまだ統合/棄却を
# 判断していない実体としては未着手のGap）も対象にする。research_status_for_gapの
# "requested"判定と対称にする（片方だけ対応漏れがあると挙動が矛盾するため）。
_RESEARCHABLE_STATUSES = ("open", "duplicate_candidate")


def _try_start_researching(client, gap_id: str, *, from_status: str) -> bool:
    """from_statusの場合のみ"researching"へ遷移させ、実際に遷移できたプロセスだけがTrueを
    受け取る（compare-and-set相当。WHERE句にstatus=eq.<from_status>を含めたUPDATEを行い、
    直後に再SELECTして実際に遷移できたかを確認する。同時に複数プロセスがこの関数を呼んでも、
    UPDATE自体はDB側で直列化されるため、再SELECTで"researching"かつ自分が更新した直後で
    あることまでは保証できないが、少なくとも「元のstatus以外なら何もしない」「researchingへ
    到達したのが自分か他者かを問わず、二重にExpert AIを呼ばない」という目的は満たす）。"""
    client.update("geo_knowledge_gaps", {"gap_id": f"eq.{gap_id}", "status": f"eq.{from_status}"},
                  {"status": "researching"})
    rows = client.select("geo_knowledge_gaps", {"select": "status", "gap_id": f"eq.{gap_id}", "limit": "1"})
    return bool(rows) and rows[0]["status"] == "researching"


def _build_case_finding_content(gap: dict, analysis: dict) -> dict:
    """Expert AI応答（analysis、geo_department_intelligence_service.run_expert_for_missing_
    dimensionsの戻り値と同形）から、既存CASE_FINDING_SCHEMA（case_id/finding_summary/
    why_reusable必須）を満たすstructured_contentを組み立てる。外部Request起点のGapは
    geo_casesを持たないため、case_idには実在するCase IDではなく、遡って追跡できるGap参照
    ラベルを入れる（存在しないCaseを捏造しない）。"""
    finding_parts = [p for p in (analysis.get("geo_assessment"), analysis.get("outlook")) if p]
    return {
        "case_id": f"external_request_gap:{gap['gap_id']}",
        "finding_summary": "\n".join(finding_parts) or "(Expert AI応答にfinding本文なし)",
        "why_reusable": (
            f"Geopolitics Department Intelligence AI経由の非同期Research Request"
            f"（gap_id={gap['gap_id']}、質問: {gap.get('question') or '(不明)'}）により生成された"
            f"分析。同種の問い合わせで既存Knowledgeとして再利用できる。"
        ),
    }


def run_research_request(client, azure_client, model: str, gap_id: str, *,
                          actor_id: str = "geo_research_worker") -> dict:
    """非同期Research実行のエントリポイント。同期HTTPリクエストの外（別プロセス・別呼び出し・
    将来のスケジューラ等、どこから呼ばれてもよい）から呼ばれる想定。

    戻り値: {"skipped": bool, "reason": str | None, "status": "completed"|"failed"|None,
             "expert_response_id": str | None, "knowledge_item_id": str | None,
             "error": dict | None}

    Idempotency（B-C, item2）: gapの現在状態を見て、既にresolved/researching/Research実施済み
    （resolution_expert_response_id設定済み）ならExpert AIを呼ばずskipped=Trueを返す。
    status="open"からこのプロセスが実際に"researching"へ遷移できた場合のみExpert AIを呼ぶ。"""
    gaps = client.select("geo_knowledge_gaps", {"select": "*", "gap_id": f"eq.{gap_id}", "limit": "1"})
    if not gaps:
        raise ResearchRequestError(f"gap_idが見つかりません: {gap_id}")
    gap = gaps[0]

    if gap.get("status") == "resolved":
        return {"skipped": True, "reason": "already_resolved", "status": None,
                "expert_response_id": gap.get("resolution_expert_response_id"), "knowledge_item_id": None,
                "error": None}
    if gap.get("resolution_expert_response_id"):
        return {"skipped": True, "reason": "already_researched", "status": "completed",
                "expert_response_id": gap["resolution_expert_response_id"], "knowledge_item_id": None,
                "error": None}
    if gap.get("status") == "researching":
        return {"skipped": True, "reason": "already_researching", "status": None,
                "expert_response_id": None, "knowledge_item_id": None, "error": None}
    if gap.get("status") not in _RESEARCHABLE_STATUSES:
        return {"skipped": True, "reason": f"unexpected_status_{gap.get('status')}", "status": None,
                "expert_response_id": None, "knowledge_item_id": None, "error": None}

    if not _try_start_researching(client, gap_id, from_status=gap["status"]):
        # 他プロセスが同時に同じstatusを消費した（レースに負けた）ため、ここでは何もしない。
        return {"skipped": True, "reason": "lost_race_to_another_worker", "status": None,
                "expert_response_id": None, "knowledge_item_id": None, "error": None}

    geo_audit.log_audit(client, actor=actor_id, action="knowledge_gap_research_started",
                         target_table="geo_knowledge_gaps", target_id=gap_id, detail={})

    normalized = {
        "question": gap.get("question") or "", "context": gap.get("business_context"),
        "request_type": "user_question", "analysis_requested": True,
    }
    missing_categories = gap.get("missing_knowledge") or []
    dimension_assessment = [{"dimension": c, "status": "missing", "freshness": "not_applicable",
                             "source_ids": []} for c in missing_categories]
    try:
        analysis = dept.run_expert_for_missing_dimensions(
            client, azure_client, model, normalized, {"issue_ids": []},
            dimension_assessment, "existing_plus_expert")
        expert_response_id = analysis["expert_response_id"]

        gap_service.record_research_response(client, gap_id, expert_response_id=expert_response_id,
                                             actor_id=actor_id)

        knowledge_item = geo_knowledge_service.create_knowledge_item(
            client, knowledge_type="case_finding",
            title=f"[外部Request由来Research] {(gap.get('question') or '')[:100]}",
            structured_content=_build_case_finding_content(gap, analysis),
            information_class="internal_expert_assessment", created_by=actor_id,
            status="candidate", origin="async_expert_research")

        geo_audit.log_audit(client, actor=actor_id, action="knowledge_gap_research_completed",
                             target_table="geo_knowledge_gaps", target_id=gap_id,
                             detail={"expert_response_id": expert_response_id,
                                     "knowledge_item_id": knowledge_item["knowledge_item_id"]})
        return {"skipped": False, "reason": None, "status": "completed",
                "expert_response_id": expert_response_id,
                "knowledge_item_id": knowledge_item["knowledge_item_id"], "error": None}
    except Exception as e:  # noqa: BLE001 — Research失敗時はGapをopenへ戻し、後で再試行できるようにする
        client.update("geo_knowledge_gaps", {"gap_id": f"eq.{gap_id}"}, {"status": "open"})
        geo_audit.log_audit(client, actor=actor_id, action="knowledge_gap_research_failed",
                             target_table="geo_knowledge_gaps", target_id=gap_id,
                             detail={"error": f"{type(e).__name__}: {e}"})
        return {"skipped": False, "reason": None, "status": "failed", "expert_response_id": None,
                "knowledge_item_id": None, "error": {"code": type(e).__name__, "message": str(e)}}
