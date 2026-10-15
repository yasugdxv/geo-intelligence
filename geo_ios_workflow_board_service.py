"""Geopolitical Intelligence OS: Workflow Board Aggregation Service。

既存のAnalysis Session/Review Cycle/Approval Eventサービスをそのまま呼び出し、
束ねて返すだけの読み取り専用集計層。新しいUI独自Statusは持たない
（既存geo_ios_workflow_header.deriveWorkflowStateが使う5フィールドをそのまま
導出する、既存State Machine定義を再利用する方針）。新規テーブル・書き込みは無い。
"""
import geo_ios_analysis_session_service as session_service
import geo_ios_approval_service as approval_service
import geo_ios_review_service as review_service
import geo_ios_report_draft_service as report_draft_service


def _build_case(client, session: dict) -> dict:
    """1つのAnalysis Sessionから、既存の各サービスを呼んでWorkflow State付きcase dictを
    組み立てる（list_board_cases/get_case_for_session共通、ロジックの二重管理を避ける）。"""
    report_project_id = session.get("linked_report_project_id")
    latest_cycle = None
    has_open_review_cycle = False
    current_approval = None
    if report_project_id:
        cycles = review_service.list_review_cycles(client, report_project_id)
        if cycles:
            latest_cycle = cycles[-1]  # list_review_cyclesはround_number.asc（昇順）で返すため、
            # 最新ラウンドは末尾（既存呼び出し元のバグではなく、本関数側で正しく解釈する）
            has_open_review_cycle = latest_cycle.get("status") != "completed"
        current_approval = approval_service.get_current_approval(client, report_project_id)

    # 「設計」step = Analysis Design Workspace（geo_ios_analysis_designs）が実際に
    # 生成済みかどうか。以前はSessionが存在するだけで常にTrueにしていたが、それだと
    # まだシナリオ候補を検討中（＝局面を読む画面でまだAnalysis Designすら生成していない
    # 段階）のSessionまで「設計」が完了済み・「ドラフト」が現在地であるかのように
    # 見えてしまう（ユーザー指摘: 「このページはまだ設計だよね」）。
    has_design = session_service.get_current_design(client, session["analysis_session_id"]) is not None

    report_generation_mode = session.get("report_generation_mode") or "report_studio"
    has_native_draft = (
        report_draft_service.has_completed_draft(client, session["analysis_session_id"])
        if report_generation_mode == "native" else False
    )
    has_ai_review_completed = (
        report_draft_service.is_ai_review_passed(client, session["analysis_session_id"])
        if report_generation_mode == "native" else False
    )

    return {
        "analysis_session_id": session["analysis_session_id"],
        "title": session.get("title"),
        "theme": session.get("theme"),
        "user_id": session.get("user_id"),
        "created_at": session.get("created_at"),
        "report_project_id": report_project_id,
        "latest_review_cycle_id": (latest_cycle or {}).get("review_cycle_id"),
        "latest_review_reviewer": (latest_cycle or {}).get("reviewer"),
        "latest_review_result": (latest_cycle or {}).get("result"),
        "has_open_review_cycle": has_open_review_cycle,
        "approval_status": (current_approval or {}).get("approval_status"),
        "publication_status": (current_approval or {}).get("publication_status"),
        "has_design": has_design,
        "has_report_project": bool(report_project_id),
        # IOS Native Draft: report_generation_modeがSource of Truth。Native Draftが
        # 偶然存在するかどうかでは判定しない——report_studio modeのSessionではhas_native_
        # draftは常にFalse（クエリすら発行しない）。
        "report_generation_mode": report_generation_mode,
        "has_native_draft": has_native_draft,
        "has_ai_review_completed": has_ai_review_completed,
    }


def list_board_cases(client) -> list:
    """全Analysis Sessionを、既存の各サービスから導出したWorkflow State付きで返す。
    N+1呼び出しになるが、既存の他IOS画面と同じ規模想定(大量データ向け最適化はしない)。"""
    sessions = session_service.list_sessions(client, limit=200)
    return [_build_case(client, session) for session in sessions]


def get_case_for_session(client, analysis_session_id: str) -> dict | None:
    """Session詳細ページ(geo.ios.analysis.$sessionId)のWorkflow Header表示用に、
    1件分のcaseだけを返す。list_board_casesと同じ_build_caseを使うため、Board/
    Session詳細ページ間でWorkflow Stateの導出結果がズレることはない。"""
    session = session_service.get_session(client, analysis_session_id)
    if not session:
        return None
    return _build_case(client, session)


STEP_ORDER = ("design", "draft", "expert_review", "approval", "published")


def derive_current_step(case: dict) -> str:
    """フロントエンドgeo-ios-workflow-header.tsxのderiveWorkflowStateと同じ5フィールド・
    同じ分岐順序をPython側に写したもの（Authorization用フィルタでBackend側にも同じ
    「現在どのステップか」判定が必要なため）。UI独自Statusを新設するものではなく、
    既存State Machineの判定ロジックを言語境界を越えて複製しているだけ——ロジックの
    定義元はderiveWorkflowStateのまま（変更時は両方揃えて直す必要がある）。"""
    if not case.get("has_design"):
        return "design"
    # IOS Native Draft: report_generation_modeが唯一のSource of Truth。nativeなら完了済み
    # Native Draftの有無、report_studioなら既存has_report_project（Native Draftが偶然存在
    # するかどうかでは判定しない）。
    draft_done = (
        case.get("has_native_draft") if case.get("report_generation_mode") == "native"
        else case.get("has_report_project")
    )
    if not draft_done:
        return "draft"
    if case.get("has_open_review_cycle"):
        return "expert_review"
    if not case.get("latest_review_result"):
        return "expert_review"
    if case.get("approval_status") != "approved":
        return "approval"
    if case.get("publication_status") != "published":
        return "approval"
    return "published"


def _needs_attention(case: dict) -> bool:
    """「要対応」= 著者側の修正待ち（Revision Requested後、次のRound未着手）、または
    承認者側の承認待ち（Approval段階でまだapproved出ない）。新しい優先度スコアリング
    （P0/P1等）は作らない——既に持っているstatusのみから判定する。"""
    if case.get("latest_review_result") == "revision_requested" and not case.get("has_open_review_cycle"):
        return True
    if derive_current_step(case) == "approval" and case.get("approval_status") != "approved":
        return True
    return False


def compute_home_summary(cases: list, identity) -> dict:
    """Homeダッシュボード相当のメトリクス・タスク・再開候補を、既存Board case
    （filter_cases_for_identity適用後の、そのidentityが見える範囲）から集計する
    純粋関数。新しいフィールド・重要度モデルは追加しない。"""
    needs_attention = [c for c in cases if _needs_attention(c)]
    in_progress = [c for c in cases if derive_current_step(c) != "published"]
    in_expert_review = [c for c in cases if c.get("has_open_review_cycle")]
    approved = [
        c for c in cases
        if c.get("publication_status") == "published" or c.get("approval_status") == "approved"
    ]

    tasks = [
        {
            "analysis_session_id": c["analysis_session_id"],
            "title": c.get("title"),
            "reason": "revision_requested" if c.get("latest_review_result") == "revision_requested" else "approval_pending",
        }
        for c in needs_attention
    ]

    my_active = [
        c for c in cases
        if derive_current_step(c) != "published" and c.get("user_id") == identity.identity_id
    ]
    resume = sorted(my_active, key=lambda c: c.get("created_at") or "", reverse=True)[0] if my_active else None

    return {
        "metrics": {
            "in_progress": len(in_progress),
            "needs_attention": len(needs_attention),
            "in_expert_review": len(in_expert_review),
            "approved": len(approved),
        },
        "tasks": tasks[:5],
        "resume": resume,
    }


def filter_cases_for_identity(cases: list, identity) -> list:
    """§1 Workflow Board Authorization: authenticated roleに応じて返す案件を絞り込む。
    knowledge_steward/administratorは全案件、approverはapproval/published段階の案件、
    expert_reviewerは自分がreviewerとしてassignされた案件のみ、analystは自分が作成した
    案件のみ。それ以外の（未対応）roleはfail-closedで空リストを返す。"""
    role = identity.assigned_role
    if role in ("knowledge_steward", "administrator"):
        return cases
    if role == "approver":
        return [c for c in cases if derive_current_step(c) in ("approval", "published")]
    if role == "expert_reviewer":
        return [c for c in cases if c.get("latest_review_reviewer") == identity.identity_id]
    if role == "analyst":
        return [c for c in cases if c.get("user_id") == identity.identity_id]
    return []
