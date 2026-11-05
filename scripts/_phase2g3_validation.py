# Phase 2G-3 実DB検証（使い捨てスクリプト）。Theme Candidate 1件 + 内部Report
# （同部門1件・別部門1件）を作成し、/internal-coverage相当（candidate_api関数を直接
# 呼ぶ）が、同部門Reportのみをcoverageとして返し、別部門Reportの存在を一切匂わせない
# ことを確認する。完了後にすべて削除する。
import json
import sys

sys.path.insert(0, ".")

from config_utils import load_config
from geo_db_client import SupabaseClient
from ai_client import make_openai_client

import geo_ios_identity
import geo_knowledge_access_policy as access_policy
import geo_report_service
import geo_theme_candidate_api as candidate_api

config = load_config()
client = SupabaseClient(config)
azure_client, model = make_openai_client(config)
candidate_api._client = client

TOKEN = "PHASE2G3VALIDATION"
OWN_DEPT = "サステナビリティ部"
OTHER_DEPT = "経営企画部_2G3TEST"

created_reports = []
created_candidates = []
output = {}


def mk_report(suffix, owner_department=OWN_DEPT):
    raw = f"{TOKEN} {suffix}\n internal analysis\n".encode("utf-8")
    r = geo_report_service.ingest_report(
        client, filename=f"{TOKEN.lower()}_{suffix}.txt", mime_type="text/plain", raw_bytes=raw,
        title=f"{TOKEN} {suffix}", author="claude-2g3-verify", owner_department=owner_department,
        report_type="verification", country_region=[f"{TOKEN} Washington"], topics=["GHG"],
        theme_id="aa6a91a7-ebf6-4a86-817a-0ae194e741b8",
        confidentiality_label="DEPARTMENT_ONLY" if owner_department == OWN_DEPT else None,
        summary=f"{TOKEN} {suffix} summary", keywords=[TOKEN], key_actors=[f"{TOKEN} White House"])
    rid = r["report"]["report_id"]
    created_reports.append(rid)
    if owner_department != OWN_DEPT:
        client.update("geo_reports", {"report_id": f"eq.{rid}"}, {"allowed_org_units": [owner_department]})
    return rid


try:
    mk_report("own-dept-analysis", owner_department=OWN_DEPT)
    mk_report("other-dept-analysis", owner_department=OTHER_DEPT)

    candidate_row = client.insert("geo_theme_candidates", [{
        "candidate_fingerprint": f"{TOKEN}-fp",
        # 既存Lexical Hybrid Search（'simple' config、AND一致）向けに、確実にReportの
        # search_text_sourceとトークンが一致する短い語にする（2F-1/2F-3/2F-4/2F-6で
        # 繰り返し確認済みの教訓——自然文の末尾(例:「の動き」)がAND一致を壊す）。
        "title": f"{TOKEN} White House",
        "signal_summary": "test", "observed_change": "test", "why_now": "test",
        "question": "test question?",
        "actors": [f"{TOKEN} White House"], "regions": [f"{TOKEN} Washington"], "themes": [],
        "business_relevance": ["regulatory"],
        "candidate_type": "emerging", "change_type": "actor_movement",
        "first_seen_at": "2026-09-01T00:00:00+00:00", "latest_signal_at": "2026-09-01T00:00:00+00:00",
        "signal_count": 1, "status": "NEW", "is_demo": True,
    }])[0]
    candidate_id = candidate_row["candidate_id"]
    created_candidates.append(candidate_id)

    ctx_own = access_policy.build_requester_context(
        user_id="verify-2g3", department=OWN_DEPT, is_authenticated=True)
    identity_own = geo_ios_identity.IosIdentity(
        identity_id="verify-2g3", assigned_role="viewer", domain=None, department=OWN_DEPT, authenticated=True)

    result_stage1 = candidate_api.get_theme_candidate_internal_coverage(
        candidate_id, include_analysis=False, identity=identity_own)
    output["stage1_own_dept"] = result_stage1

    identity_anon = geo_ios_identity.IosIdentity(authenticated=False)
    result_anon = candidate_api.get_theme_candidate_internal_coverage(
        candidate_id, include_analysis=False, identity=identity_anon)
    output["stage1_anonymous"] = result_anon

    forbidden_title = f"{TOKEN} other-dept-analysis"
    output["permission_check"] = {
        "forbidden_title_in_own_dept_result": any(
            s.get("title") == forbidden_title for s in result_stage1["similar_internal_intelligence"]),
        "forbidden_title_in_anon_result": any(
            s.get("title") == forbidden_title for s in result_anon["similar_internal_intelligence"]),
        "accessible_count_anon": result_anon["coverage"]["accessible_report_count"],
        "accessible_count_own_dept": result_stage1["coverage"]["accessible_report_count"],
    }

    # geo_theme_candidatesへ何も書き込まれていないこと（永続化しない）の直接確認
    refetched = client.select("geo_theme_candidates", {
        "select": "*", "candidate_id": f"eq.{candidate_id}"})[0]
    output["candidate_unchanged_after_coverage_calls"] = (
        "internal_coverage" not in refetched and refetched["status"] == "NEW"
    )

    with open("C:/Users/269811/AppData/Local/Temp/claude_2g3_validation_results.json", "w",
              encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print("VALIDATION_DONE")

finally:
    for cid in created_candidates:
        client.delete("geo_theme_candidates", {"candidate_id": f"eq.{cid}"})
    for rid in created_reports:
        try:
            geo_report_service.delete_report(client, report_id=rid, actor_id="claude-2g3-verify")
        except Exception as e:  # noqa: BLE001
            print(f"cleanup warning (report {rid}): {e}")
    print("CLEANUP_DONE", {"reports": len(created_reports), "candidates": len(created_candidates)})
