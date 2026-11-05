# Phase 2F-3 §20 Validation Cases（実DB検証用の使い捨てスクリプト）。
# 完了後に自分で作成したtest reportをすべて削除する（try/finallyで保証）。
import json
import sys

sys.path.insert(0, ".")

from config_utils import load_config
from geo_db_client import SupabaseClient
from ai_client import make_openai_client

import geo_intelligence_ai_analysis_service as analysis_service
import geo_ios_knowledge_publication_service as publication_service
import geo_knowledge_access_policy as access_policy
import geo_report_service

config = load_config()
client = SupabaseClient(config)
azure_client, model = make_openai_client(config)

THEME_ID = "aa6a91a7-ebf6-4a86-817a-0ae194e741b8"  # 既存「紅海・ホルムズ海峡」テーマ（Phase 2F-1検証で使用済み）
OWN_DEPT = "サステナビリティ部"
OTHER_DEPT = "経営企画部_2F3TEST"
TOKEN = "PHASE2F3VALIDATION"

created_report_ids = []
created_knowledge_item_ids = []
output = {}


def make_report(*, suffix, summary, uploaded_at_override=None, owner_department=OWN_DEPT,
                 allowed_org_units=None):
    raw = f"{TOKEN} {suffix}\n{summary}\n".encode("utf-8")
    result = geo_report_service.ingest_report(
        client, filename=f"{TOKEN.lower()}_{suffix}.txt", mime_type="text/plain", raw_bytes=raw,
        title=f"{TOKEN} {suffix}", author="claude-2f3-verify", owner_department=owner_department,
        report_type="verification", country_region=["紅海", "ホルムズ海峡"], topics=["GHG", "サプライチェーン"],
        theme_id=THEME_ID, confidentiality_label="DEPARTMENT_ONLY" if allowed_org_units is None else None,
        summary=f"{TOKEN} {suffix} - {summary}", keywords=[TOKEN],
    )
    report_id = result["report"]["report_id"]
    created_report_ids.append(report_id)
    if allowed_org_units is not None:
        client.update("geo_reports", {"report_id": f"eq.{report_id}"}, {"allowed_org_units": allowed_org_units})
    if uploaded_at_override:
        client.update("geo_reports", {"report_id": f"eq.{report_id}"}, {"uploaded_at": uploaded_at_override})
    return report_id


try:
    ctx_own = access_policy.build_requester_context(
        user_id="verify-2f3", department=OWN_DEPT, is_authenticated=True)

    # ---- Case A: 同一テーマの複数Report → 共通点/相違点 -----------------------
    r_a1 = make_report(suffix="CASEA-北回り迂回",
                        summary="北回り迂回ルートは燃料コストが上昇するが安全性は高い")
    r_a2 = make_report(suffix="CASEA-南回り迂回",
                        summary="南回り迂回ルートは距離が短いが海賊リスクが残る")
    r_a_other_dept = make_report(suffix="CASEA-他部門版分析", owner_department=OTHER_DEPT,
                                  allowed_org_units=[OTHER_DEPT],
                                  summary="他部門限定の内部分析、Contextへ入ってはいけない")

    result_a = analysis_service.analyze(
        client, azure_client, model, f"{TOKEN} CASEA",
        mode="compare", requester_context=ctx_own)
    output["case_a"] = result_a

    # ---- Case B: 同一テーマの時系列Report → 見方の変化 -------------------------
    r_b1 = make_report(suffix="CASEB-2026年1月時点", uploaded_at_override="2026-01-15T00:00:00+00:00",
                        summary="2026年1月時点ではリスクはLowと評価していた")
    r_b2 = make_report(suffix="CASEB-2026年4月時点", uploaded_at_override="2026-04-15T00:00:00+00:00",
                        summary="2026年4月時点でMediumへ引き上げ、攻撃再発を懸念")
    r_b3 = make_report(suffix="CASEB-2026年7月時点", uploaded_at_override="2026-07-15T00:00:00+00:00",
                        summary="2026年7月時点でHighへ引き上げ、恒常的な迂回が前提化")

    result_b = analysis_service.analyze(
        client, azure_client, model, f"{TOKEN} CASEB",
        mode="temporal", requester_context=ctx_own)
    output["case_b"] = result_b

    # ---- Case C: Approved + Uploaded-only混在 → Review statusを区別した分析 -----
    r_c_approved = make_report(suffix="CASEC-承認済み分析", summary="承認済みの正式見解")
    r_c_uploaded_only = make_report(suffix="CASEC-未レビュー分析", summary="未レビューの暫定メモ")

    ki = publication_service.publish_external_approved_report(
        client, report_id=r_c_approved, publisher="steward-2f3", publisher_role="knowledge_steward",
        approver="steward-2f3-approver", confidentiality_label="DEPARTMENT_ONLY")
    created_knowledge_item_ids.append(ki["knowledge_item_id"])

    result_c = analysis_service.analyze(
        client, azure_client, model, f"{TOKEN} CASEC",
        mode="reviewed", requester_context=ctx_own)
    output["case_c"] = result_c

    # ---- Permission除外の直接確認（各CaseでOTHER_DEPTのtitleが一切出ないこと） -----
    forbidden_title = f"{TOKEN} CASEA-他部門版分析"
    output["permission_check"] = {
        "forbidden_title_in_case_a_source_refs": any(
            s.get("title") == forbidden_title for s in result_a["source_refs"]),
        "forbidden_title_in_case_a_answer": forbidden_title in (result_a.get("answer") or ""),
    }

    with open("C:/Users/269811/AppData/Local/Temp/claude_2f3_validation_results.json", "w",
              encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print("VALIDATION_DONE")

finally:
    for kid in created_knowledge_item_ids:
        try:
            client.delete("geo_knowledge_items", {"knowledge_item_id": f"eq.{kid}"})
        except Exception as e:  # noqa: BLE001
            print(f"cleanup warning (knowledge_item {kid}): {e}")
    for rid in created_report_ids:
        try:
            geo_report_service.delete_report(client, report_id=rid, actor_id="claude-2f3-verify")
        except Exception as e:  # noqa: BLE001
            print(f"cleanup warning (report {rid}): {e}")
    print("CLEANUP_DONE", len(created_report_ids), "reports,", len(created_knowledge_item_ids), "knowledge_items")
