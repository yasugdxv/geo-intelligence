"""Geopolitical Intelligence OS Phase D: IOS Review/ApprovalとReport Studioを繋ぐAdapter。

絶対条件: Report Studio本体（geo_report_studio_service.py等、2026-09-16時点でも別チームが
改修中）の内部実装・UI・生成Promptには一切触れない。呼び出すのは、既に他モジュール
（geo_intelligence_gap_api.py）からも参照されている安定した既存公開関数
（get_latest_run/list_latest_syntheses_for_project、いずれも読み取り専用・Azure呼び出し無し）
のみ。Report Studio側のスキーマが今後変わっても、本Adapterの戻り値の形（version_label/
version_snapshot）だけをIOS Review/Approval側との契約として維持する——両者の間の緩衝材。
"""
import geo_report_studio_service as report_studio_service


class ReportStudioAdapterError(Exception):
    """Report StudioとIOS Review/Approvalの接続に関するエラー。"""


def build_review_snapshot(client, report_project_id: str) -> dict:
    """report_project_idの「現在Reviewerが見るべき状態」を、Report Studioの最新run×
    Narrative別最新Synthesisからスナップショットする。Review Request時点で固定するための
    材料（§8: Review Request時点のReport VersionはSnapshotとして固定）。"""
    latest_run = report_studio_service.get_latest_run(client, report_project_id)
    if not latest_run:
        raise ReportStudioAdapterError(
            f"report_project_id={report_project_id}にはまだCollection Runがありません"
            "（Report Studio側でRunを実行してからReviewを依頼してください）")

    syntheses = report_studio_service.list_latest_syntheses_for_project(client, report_project_id)
    version_label = f"run:{latest_run['run_id']}"
    version_snapshot = {
        "run_id": latest_run["run_id"],
        "run_started_at": latest_run.get("started_at"),
        "run_status": latest_run.get("status"),
        "narrative_count": len(syntheses),
        "syntheses": [
            {
                "signal_id": s.get("signal_id"),
                "narrative_id": s.get("narrative_id"),
                "final_status": s.get("final_status"),
                "final_draft": s.get("final_draft"),
                "final_html": s.get("final_html"),
                "synthesis_created_at": s.get("created_at"),
            }
            for s in syntheses
        ],
    }
    return {"version_label": version_label, "version_snapshot": version_snapshot}


def get_approvable_snapshot(client, report_project_id: str) -> dict:
    """Review Platform P0 Remediation（ユーザー指示、2026-09-22、Post-Freeze Review
    Platform Audit Finding P0-2対応）: geo_ios_report_draft_service.get_approvable_
    snapshot（Native経路）と同じ設計をLegacy（report_studio mode）経路にも適用する。

    以前はこのAdapter自体にApproval Gateが一切無く、呼び出し元（geo_ios_approval_api.
    approve_in_app）がgeo_ios_review_cyclesを全く参照せずに「今のReport Studio状態」を
    無条件でApproval可能にしていた——レビュー未実施、またはresult=rejected/revision_
    requested後でも承認できてしまう脆弱性だった（実DB検証で、現存する全report_projectが
    この無gate経路の対象であることが確認されている）。

    常に「今のReport Studio状態」を再構築し（build_review_snapshotと同じ入力）、その
    version_labelに一致する最新のReview Cycle（geo_ios_review_cycles.reviewed_version_
    label）を探し、result=="approved"の場合のみ返す。Current状態を再構築するだけで
    Review Cycle側を見ないという、以前のCurrent状態自動承認の設計は取らない——Native経路と
    同じく「Freezeされた内容のみ承認可能」という原則をLegacy経路にも揃える。"""
    built = build_review_snapshot(client, report_project_id)
    cycles = client.select("geo_ios_review_cycles", {
        "select": "*", "report_project_id": f"eq.{report_project_id}",
        "reviewed_version_label": f"eq.{built['version_label']}",
        "order": "round_number.desc", "limit": "1"})
    if not cycles:
        raise ReportStudioAdapterError(
            "この内容（現在のCollection Run/Synthesis状態）はまだExpert Reviewに提出されて"
            "いません")
    latest_cycle = cycles[0]
    if latest_cycle.get("result") != "approved":
        raise ReportStudioAdapterError(
            f"最新のExpert Review結果はapprovedではありません（result={latest_cycle.get('result')}）")
    return built


def get_report_project_summary(client, report_project_id: str) -> dict | None:
    """UI/Adapter双方が使う、report_project_idの人間向け要約（project_code/title等）。
    存在しなければNone（呼び出し元でHTTP 404等に変換する）。"""
    project = report_studio_service.get_project(client, report_project_id)
    if not project:
        return None
    return {
        "project_id": project["project_id"],
        "project_code": project.get("project_code"),
        "project_title": project.get("project_title"),
        "status": project.get("status"),
    }
