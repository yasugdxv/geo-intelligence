# Phase 2F-6 実DB検証（使い捨てスクリプト）。Theme Discovery Bridge
# (assess_internal_coverage) がCase A/B/Cで正しくcoverageを返し、各Caseに混ぜた
# Unauthorized Reportが一切結果へ影響しないことを確認する。完了後に作成したReport/
# Knowledge Itemを削除する。
import json
import sys

sys.path.insert(0, ".")

from config_utils import load_config
from geo_db_client import SupabaseClient
from ai_client import make_openai_client

import geo_intelligence_ai_theme_coverage_service as coverage_service
import geo_ios_knowledge_publication_service as publication_service
import geo_knowledge_access_policy as access_policy
import geo_report_service

config = load_config()
client = SupabaseClient(config)
azure_client, model = make_openai_client(config)

THEME_ID = "aa6a91a7-ebf6-4a86-817a-0ae194e741b8"
TOKEN = "PHASE2F6VALIDATION"
OWN_DEPT = "サステナビリティ部"
OTHER_DEPT = "経営企画部_2F6TEST"

created_report_ids = []
created_knowledge_item_ids = []
output = {}


def mk(suffix, summary, owner_department=OWN_DEPT, uploaded_at_override=None, key_actors=None):
    raw = f"{TOKEN} {suffix}\n{summary}\n".encode("utf-8")
    r = geo_report_service.ingest_report(
        client, filename=f"{TOKEN.lower()}_{suffix}.txt", mime_type="text/plain", raw_bytes=raw,
        title=f"{TOKEN} {suffix}", author="claude-2f6-verify", owner_department=owner_department,
        report_type="verification", country_region=["紅海", "ホルムズ海峡"], topics=["GHG"],
        theme_id=THEME_ID, confidentiality_label="DEPARTMENT_ONLY" if owner_department == OWN_DEPT else None,
        summary=f"{TOKEN} {suffix} - {summary}", keywords=[TOKEN], key_actors=key_actors or [])
    rid = r["report"]["report_id"]
    created_report_ids.append(rid)
    if owner_department != OWN_DEPT:
        client.update("geo_reports", {"report_id": f"eq.{rid}"}, {"allowed_org_units": [owner_department]})
    if uploaded_at_override:
        client.update("geo_reports", {"report_id": f"eq.{rid}"}, {"uploaded_at": uploaded_at_override})
    return rid


try:
    ctx_own = access_policy.build_requester_context(
        user_id="verify-2f6", department=OWN_DEPT, is_authenticated=True)

    # ---- Case A: 既存分析あり（Approved 1件 + 別部門限定Reportを混入） ----------------
    r_a_approved = mk("CASEA-承認済み", "White Houseの裁量とBIS/Commerceの制度化を整理",
                       uploaded_at_override="2026-08-14T00:00:00+00:00", key_actors=["White House", "BIS"])
    ki = publication_service.publish_external_approved_report(
        client, report_id=r_a_approved, publisher="steward-2f6", publisher_role="knowledge_steward",
        approver="steward-2f6-approver", approval_date="2026-08-14T00:00:00+00:00",
        confidentiality_label="DEPARTMENT_ONLY")
    created_knowledge_item_ids.append(ki["knowledge_item_id"])
    r_a_other = mk("CASEA-他部門版", "他部門限定、Contextへ入ってはいけない", owner_department=OTHER_DEPT)

    theme_candidate_a = {
        "theme_title": f"{TOKEN} CASEA",
        "keywords": [TOKEN, "CASEA"],
        "actors": ["White House", "新しい議会動向"],
        "regions": ["紅海"],
    }
    result_a = coverage_service.assess_internal_coverage(
        client, azure_client, model, theme_candidate_a, requester_context=ctx_own, include_analysis=True)
    output["case_a"] = result_a

    # ---- Case B: 古い分析のみ（uploaded_only、8ヶ月以上前） ------------------------------
    r_b_old = mk("CASEB-古い分析", "2025年初頭時点の暫定メモ", uploaded_at_override="2025-01-10T00:00:00+00:00")
    r_b_other = mk("CASEB-他部門版", "他部門限定", owner_department=OTHER_DEPT)

    theme_candidate_b = {"theme_title": f"{TOKEN} CASEB", "keywords": [TOKEN, "CASEB"]}
    result_b = coverage_service.assess_internal_coverage(
        client, azure_client, model, theme_candidate_b, requester_context=ctx_own, include_analysis=False)
    output["case_b"] = result_b

    # ---- Case C: 内部coverageなし（アクセス可能なReportが無い、他部門のみ存在） ------------
    r_c_other = mk("CASEC-他部門版のみ", "他部門限定のみ", owner_department=OTHER_DEPT)

    theme_candidate_c = {
        "theme_title": f"{TOKEN} CASEC", "keywords": [TOKEN, "CASEC"], "actors": ["Some Actor"],
    }
    result_c = coverage_service.assess_internal_coverage(
        client, azure_client, model, theme_candidate_c, requester_context=ctx_own, include_analysis=False)
    output["case_c"] = result_c

    forbidden_titles = [f"{TOKEN} CASEA-他部門版", f"{TOKEN} CASEB-他部門版", f"{TOKEN} CASEC-他部門版のみ"]
    all_titles = (
        [s.get("title") for r in (result_a, result_b, result_c) for s in r["similar_internal_intelligence"]]
        + [json.dumps(result_a, ensure_ascii=False)]
    )
    output["permission_check"] = {
        "forbidden_title_leaked": any(ft in " ".join(str(t) for t in all_titles) for ft in forbidden_titles),
    }

    with open("C:/Users/269811/AppData/Local/Temp/claude_2f6_validation_results.json", "w",
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
            geo_report_service.delete_report(client, report_id=rid, actor_id="claude-2f6-verify")
        except Exception as e:  # noqa: BLE001
            print(f"cleanup warning (report {rid}): {e}")
    print("CLEANUP_DONE", len(created_report_ids), "reports,", len(created_knowledge_item_ids), "knowledge_items")
