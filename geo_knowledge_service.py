"""地政学ナレッジ＆インテリジェンスDB: Knowledge Item CRUD・Status遷移・Review記録。

LLMはEntity Merge／Fact確定／Knowledge Active化／Conflicting Evidence削除／
Contested Knowledge解消／Legal Conclusion確定を提案するだけで確定しない。これらは
権限を持つ人間の明示的なレビュー操作（review_knowledge_item）を経て初めて確定する。
本モジュールの役割は「権限があるか」「許可された状態遷移か」「必須項目が揃っているか」を
検証し、条件を満たした場合に人間が下した決定をDBへ反映することであり、内容の正しさを
自動判断してActive化するわけではない（docs/geopolitical-knowledge-architecture.md 8章）。
"""
import geo_audit
import geo_knowledge_item_schemas as schemas
import geo_role_guard

REVIEWER_ROLES = ("expert_reviewer", "knowledge_steward")

# 許可された状態遷移。candidate→in_review→active/contested/rejected、
# active→superseded/deprecated/contested、rejected→archived。
# contestedは追加レビューでactive/rejected/supersededへ解消できるものとする
# （仕様書の状態図には明記が無いため、docs/geopolitical-implementation-status.mdに仮定として記録）。
ALLOWED_TRANSITIONS = {
    "candidate": {"in_review"},
    "in_review": {"active", "contested", "rejected"},
    "active": {"superseded", "deprecated", "contested"},
    "contested": {"active", "rejected", "superseded"},
    "rejected": {"archived"},
}

REVIEW_DECISION_TO_STATUS = {
    "start_review": "in_review",
    "approve_active": "active",
    "approve_contested": "contested",
    "reject": "rejected",
    "supersede": "superseded",
    "deprecate": "deprecated",
    "archive": "archived",
}

# Phase2B-2: retrieval_status（AIが参照してよいか）はstatus（lifecycle）と独立した二軸。
# review_knowledge_itemの初回遷移（candidate/in_reviewから抜ける瞬間）でのみ、この対応表で
# 初期値を1回だけ設定する。それ以降はこの表による自動再同期を一切行わず、set_retrieval_status()
# 経由の明示操作でのみ変更する（Stewardの手動設定を後続レビューが上書きしないため）。
RETRIEVAL_STATUS_INITIAL_MAP = {
    "active": "available",
    "contested": "available",
    "rejected": "archived",
    "superseded": "deprecated",
    "deprecated": "deprecated",
    "archived": "archived",
}

RETRIEVAL_STATUSES = ("available", "restricted", "deprecated", "archived")


class InvalidStatusTransitionError(ValueError):
    pass


class LineageValidationError(ValueError):
    """Human Gate #2 P0 Closure（ユーザー指示、2026-09-23、Post-Freeze Review Platform
    Audit Finding P0-4 再発防止対応）。"""


def create_knowledge_item(client, *, knowledge_type: str, title: str, structured_content: dict,
                           information_class: str, created_by: str, summary: str = None,
                           confidence: str = "判断不能", confidence_rationale: str = None,
                           valid_from: str = None, valid_to: str = None, as_of_date: str = None,
                           is_demo: bool = False, status: str = "candidate", origin: str = None,
                           derived_from_review_session_id: str = None,
                           derived_from_change_id: str = None, supersedes_id: str = None,
                           human_visibility: str = None, agent_sharing_policy: str = None) -> dict:
    """新規Knowledge Itemを作成する。既定status=candidate（人間レビュー前）。
    schema検証に失敗した場合はKnowledgeContentValidationErrorを送出する。
    retrieval_statusは常にrestrictedで作成する（review_knowledge_itemの初回遷移で初期化される、2章参照）。

    supersedes_id: このCandidateが（承認された場合に）置き換える対象の既存active Knowledge
    Item ID（既存カラムへの配線のみ、追加は後方互換）。承認自体・旧itemのsuperseded遷移は
    従来通りreview_knowledge_itemを人間が個別に呼ぶ（本関数は自動supersedeを行わない、
    地政学専門家AI v1.2 Persistent Preference Candidate生成で使用）。

    human_visibility/agent_sharing_policy（2026-09-24追加、Learning Workbench対応）:
    省略時はDB列既定値（company/geopolitics_only）のまま——呼び出し側が明示的に指定した
    場合のみ上書きする。Learning Workbenchのpublish_from_workbenchは、Candidate本体に
    人間が設定した現在値をこの引数で渡す（未設定のままpublishしようとした場合はWorkbench
    Service層で拒否する、既定値へのフォールバックに頼らない）。"""
    schemas.validate_structured_content(knowledge_type, structured_content)
    if status not in ("candidate",):
        # candidate以外での新規作成（例: 信頼済みA1/A2ソースの自動反映）はPhase1では扱わない
        raise ValueError("Phase1では新規作成のstatusはcandidateのみ許可されます")

    row = {
        "knowledge_type": knowledge_type, "title": title, "summary": summary,
        "structured_content": structured_content, "status": status,
        "confidence": confidence, "confidence_rationale": confidence_rationale,
        "information_class": information_class, "valid_from": valid_from, "valid_to": valid_to,
        "created_by": created_by, "is_demo": is_demo,
        "retrieval_status": "restricted",  # DB column defaultと明示的に揃える（Fake/実DB双方一貫のため）
        "origin": origin, "derived_from_review_session_id": derived_from_review_session_id,
        "derived_from_change_id": derived_from_change_id,
    }
    if as_of_date:
        row["as_of_date"] = as_of_date
    if supersedes_id:
        row["supersedes_id"] = supersedes_id
    if human_visibility:
        row["human_visibility"] = human_visibility
    if agent_sharing_policy:
        row["agent_sharing_policy"] = agent_sharing_policy
    created = client.insert("geo_knowledge_items", [row])[0]
    geo_audit.log_audit(client, actor=created_by, action="knowledge_item_create",
                         target_table="geo_knowledge_items", target_id=created["knowledge_item_id"],
                         detail={"knowledge_type": knowledge_type, "status": status})
    return created


def get_knowledge_item(client, knowledge_item_id: str) -> dict | None:
    rows = client.select("geo_knowledge_items", {
        "select": "*", "knowledge_item_id": f"eq.{knowledge_item_id}", "limit": "1",
    })
    return rows[0] if rows else None


def list_knowledge_items(client, *, knowledge_type: str = None, status: str = None,
                          retrieval_status: str = None, include_demo: bool = False,
                          limit: int = 100) -> list:
    params = {"select": "*", "order": "updated_at.desc", "limit": str(limit)}
    if knowledge_type:
        params["knowledge_type"] = f"eq.{knowledge_type}"
    if status:
        params["status"] = f"eq.{status}"
    if retrieval_status:
        params["retrieval_status"] = f"eq.{retrieval_status}"
    if not include_demo:
        params["is_demo"] = "eq.false"
    return client.select("geo_knowledge_items", params)


def list_knowledge_items_for_issue(client, issue_id: str, *, status: str = "active",
                                    include_demo: bool = False) -> list:
    links = client.select("geo_knowledge_item_issues", {
        "select": "knowledge_item_id", "issue_id": f"eq.{issue_id}",
    })
    ids = [link["knowledge_item_id"] for link in links]
    if not ids:
        return []
    params = {"select": "*", "knowledge_item_id": f"in.({','.join(ids)})"}
    if status:
        params["status"] = f"eq.{status}"
    if not include_demo:
        params["is_demo"] = "eq.false"
    return client.select("geo_knowledge_items", params)


def _validate_active_lineage(client, item: dict) -> None:
    """Human Gate #2 P0 Closure（ユーザー指示、2026-09-23、Post-Freeze Review Platform
    Audit Finding P0-4再発防止対応）: candidate/in_review/contested → activeへの遷移時、
    このKnowledge Itemが実在する正規の起点（Learning CandidateまたはReport Publication）
    から来ていることを検証する。認証（Gate #2の呼び出し者確認）とは独立の防御層——
    直接DB/scriptでcreate_knowledge_item+review_knowledge_itemを呼び、authenticatedな
    credentialだけは正しく用意する、という経路（P0-4で実際に見つかった4件は、Gate #2
    自体は現在のような認証すら無い状態で作られたが、認証だけ塞いでも「本物のreviewer
    権限を持つ誰かが、実体の無いlineageを持つitemを誤ってapproveする」事故までは防げない
    ため）に対する備え。

    対象はIOS Review Platformが経由する2つの起点のみ（origin="geo_ios_learning_
    candidate"、またはsource_report_project_id設定済み=Report Publication経由）。
    それ以外の既存origin（Entity解決・Connector等、本Remediationのスコープ外）は
    従来通り無検証のまま通す——他の正規経路を壊さないため。"""
    origin = item.get("origin")
    knowledge_item_id = item["knowledge_item_id"]

    if origin == "geo_ios_learning_candidate":
        candidates = client.select("geo_ios_learning_candidates", {
            "select": "candidate_id,status", "approved_knowledge_item_id": f"eq.{knowledge_item_id}"})
        matching = [c for c in candidates if c.get("status") == "approved"]
        if not matching:
            raise LineageValidationError(
                f"knowledge_item_id={knowledge_item_id}はorigin=geo_ios_learning_candidateですが、"
                "approved_knowledge_item_idがこのIDを指し、かつstatus=approvedのLearning "
                "Candidateが見つかりません（Human Gate #1未通過、またはlineageが矛盾しています）")
        return

    source_report_project_id = item.get("source_report_project_id")
    source_approval_event_id = item.get("source_approval_event_id")
    if source_report_project_id or source_approval_event_id:
        if not (source_report_project_id and source_approval_event_id):
            raise LineageValidationError(
                f"knowledge_item_id={knowledge_item_id}はsource_report_project_id/"
                "source_approval_event_idの片方のみ設定されています（Report Publication "
                "lineageが不完全です）")
        events = client.select("geo_ios_approval_events", {
            "select": "approval_event_id,approval_status,report_project_id",
            "approval_event_id": f"eq.{source_approval_event_id}"})
        event = events[0] if events else None
        if not event or event.get("report_project_id") != source_report_project_id \
                or event.get("approval_status") != "approved":
            raise LineageValidationError(
                f"knowledge_item_id={knowledge_item_id}のsource_approval_event_id="
                f"{source_approval_event_id}は、report_project_id={source_report_project_id}に"
                "一致するapproval_status=approvedのApproval Eventとして解決できません")
        return

    # origin不明・lineage未設定の既存item（本Remediationのスコープ外の他経路）はそのまま通す。


def review_knowledge_item(client, *, knowledge_item_id: str, reviewer_id: str, reviewer_role: str,
                           review_decision: str, review_comment: str, evidence_adequacy: str,
                           applicability_checked: bool, change_diff: dict = None) -> dict:
    """人間のレビュー操作を検証・記録し、許可された状態遷移であればstatusを更新する。
    LLMはこの関数を直接呼び出さない（呼び出し元は必ずhuman-triggeredのAPI経路を通ること）。"""
    geo_role_guard.require_role(reviewer_role, REVIEWER_ROLES)

    if review_decision not in REVIEW_DECISION_TO_STATUS:
        raise ValueError(f"未対応のreview_decisionです: {review_decision}")
    if evidence_adequacy not in ("sufficient", "partial", "insufficient"):
        raise ValueError(f"未対応のevidence_adequacyです: {evidence_adequacy}")

    item = get_knowledge_item(client, knowledge_item_id)
    if not item:
        raise ValueError(f"knowledge_item_idが見つかりません: {knowledge_item_id}")

    previous_status = item["status"]
    new_status = REVIEW_DECISION_TO_STATUS[review_decision]
    allowed = ALLOWED_TRANSITIONS.get(previous_status, set())
    if new_status not in allowed:
        raise InvalidStatusTransitionError(
            f"{previous_status} から {new_status} への遷移は許可されていません（許可: {sorted(allowed)}）"
        )
    if new_status == "active":
        _validate_active_lineage(client, item)

    review_row = client.insert("geo_knowledge_reviews", [{
        "knowledge_item_id": knowledge_item_id, "reviewer_id": reviewer_id,
        "reviewer_role": reviewer_role, "review_decision": review_decision,
        "review_comment": review_comment, "evidence_adequacy": evidence_adequacy,
        "applicability_checked": applicability_checked, "change_diff": change_diff,
        "previous_status": previous_status, "new_status": new_status,
    }])[0]

    patch = {
        "status": new_status, "reviewed_by": reviewer_id,
        "approved_by": reviewer_id if new_status == "active" else item.get("approved_by"),
        "version": item.get("version", 1) + 1,
    }
    # Phase2B-2: retrieval_statusはcandidate/in_reviewから初めて抜ける瞬間のみ初期化する
    # （二軸の独立性を保つため、以降の遷移ではretrieval_statusに一切触れない）
    if previous_status in ("candidate", "in_review"):
        patch["retrieval_status"] = RETRIEVAL_STATUS_INITIAL_MAP.get(new_status, "restricted")
    client.update("geo_knowledge_items", {"knowledge_item_id": f"eq.{knowledge_item_id}"}, patch)
    # SupabaseClient.updateはPrefer: return=minimalのため更新後の行を返さない。
    # 反映結果を呼び出し元へ正しく返すため、更新後に改めて取得する。
    updated = get_knowledge_item(client, knowledge_item_id)

    geo_audit.log_audit(client, actor=reviewer_id, action="knowledge_item_review",
                         target_table="geo_knowledge_items", target_id=knowledge_item_id,
                         detail={"review_decision": review_decision, "previous_status": previous_status,
                                 "new_status": new_status, "reviewer_role": reviewer_role})

    return {"knowledge_item": updated, "review": review_row}


def set_retrieval_status(client, *, knowledge_item_id: str, retrieval_status: str,
                          actor_id: str, reason: str = None) -> dict:
    """Knowledge Item個別のretrieval_statusを直接更新する（Phase2B-2）。statusのlifecycleには
    一切触れない。内容の正誤判定（Human Approval）ではなく、AIが参照してよいかを管理する
    運用操作のため、geo_knowledge_reviewsは使わずgeo_audit.log_auditにのみ記録する。"""
    if retrieval_status not in RETRIEVAL_STATUSES:
        raise ValueError(f"未対応のretrieval_statusです: {retrieval_status}")
    item = get_knowledge_item(client, knowledge_item_id)
    if not item:
        raise ValueError(f"knowledge_item_idが見つかりません: {knowledge_item_id}")

    client.update("geo_knowledge_items", {"knowledge_item_id": f"eq.{knowledge_item_id}"},
                  {"retrieval_status": retrieval_status})
    geo_audit.log_audit(client, actor=actor_id, action="knowledge_item_retrieval_status_change",
                         target_table="geo_knowledge_items", target_id=knowledge_item_id,
                         detail={"previous_retrieval_status": item.get("retrieval_status"),
                                 "new_retrieval_status": retrieval_status, "reason": reason})
    return get_knowledge_item(client, knowledge_item_id)


def link_to_issue(client, *, knowledge_item_id: str, issue_id: str) -> dict:
    return client.insert("geo_knowledge_item_issues", [{
        "knowledge_item_id": knowledge_item_id, "issue_id": issue_id,
    }])[0]
