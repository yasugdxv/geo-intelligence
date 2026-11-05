# Phase 2F-4 実DB検証（使い捨てスクリプト）。Multi-turn Chatが
# 1) 会話履歴を使ってfollow-upのquery resolutionを正しく行うこと
# 2) 各turnで現在のIdentityによりPermission/Retrievalを再評価すること（前turnの
#    Permission結果を信用しない）
# を実データで確認する。完了後に自分で作成したtest reportを削除する。
import json
import sys

sys.path.insert(0, ".")

from config_utils import load_config
from geo_db_client import SupabaseClient
from ai_client import make_openai_client

import geo_intelligence_ai_chat_service as chat_service
import geo_knowledge_access_policy as access_policy
import geo_report_service

config = load_config()
client = SupabaseClient(config)
azure_client, model = make_openai_client(config)

THEME_ID = "aa6a91a7-ebf6-4a86-817a-0ae194e741b8"
TOKEN = "PHASE2F4VALIDATION"
OWN_DEPT = "サステナビリティ部"
OTHER_DEPT = "経営企画部_2F4TEST"

created_report_ids = []
output = {}


def mk(suffix, summary, owner_department=OWN_DEPT):
    raw = f"{TOKEN} {suffix}\n{summary}\n".encode("utf-8")
    r = geo_report_service.ingest_report(
        client, filename=f"{TOKEN.lower()}_{suffix}.txt", mime_type="text/plain", raw_bytes=raw,
        title=f"{TOKEN} {suffix}", author="claude-2f4-verify", owner_department=owner_department,
        report_type="verification", country_region=["紅海", "ホルムズ海峡"], topics=["GHG"],
        theme_id=THEME_ID, confidentiality_label="DEPARTMENT_ONLY",
        summary=f"{TOKEN} {suffix} - {summary}", keywords=[TOKEN])
    rid = r["report"]["report_id"]
    created_report_ids.append(rid)
    return rid


try:
    r1 = mk("北回り迂回", "北回り迂回ルートは燃料コストが上昇するが安全性は高い")
    r_other = mk("他部門版", "他部門限定の内部分析、Contextへ入ってはいけない", owner_department=OTHER_DEPT)
    client.update("geo_reports", {"report_id": f"eq.{r_other}"}, {"allowed_org_units": [OTHER_DEPT]})

    ctx_own = access_policy.build_requester_context(
        user_id="verify-2f4", department=OWN_DEPT, is_authenticated=True)

    # Turn 1（'simple' FTS configはAND一致のため、確実にsearch_text_sourceのトークンと
    # 一致する短い語で問い合わせる——2F-3検証と同じ教訓）。
    turn1_message = f"{TOKEN} 北回り迂回"
    turn1 = chat_service.chat(client, azure_client, model, history=[],
                               message=turn1_message, requester_context=ctx_own)
    output["turn1"] = {
        "resolved_query": turn1["resolved_query"],
        "insufficient_evidence": turn1["insufficient_evidence"],
        "source_titles": [s.get("title") for s in turn1["source_refs"]],
        "answer_head": turn1["answer"][:200],
    }

    history = [
        {"role": "user", "content": turn1_message},
        {"role": "assistant", "content": turn1["answer"]},
    ]

    # Turn 2: 前turnを指示語で参照するfollow-up（query resolutionが「北回り迂回」という
    # 話題語を復元できるかの確認）。resolved_queryが実際に使われる検索語になるため、
    # ここも解決後にFTSでヒットしやすいよう、話題語自体は変えない設問にする。
    turn2 = chat_service.chat(client, azure_client, model, history=history,
                               message="それの燃料コストは？", requester_context=ctx_own)
    output["turn2"] = {
        "resolved_query": turn2["resolved_query"],
        "insufficient_evidence": turn2["insufficient_evidence"],
        "source_titles": [s.get("title") for s in turn2["source_refs"]],
        "answer_head": turn2["answer"][:200],
    }

    # Turn 3: 同じhistoryのまま、別部門のidentityで呼ぶ（前turnのPermission結果を
    # 引き継いでいないことの直接確認——同じ会話でも部門が違えば見える範囲が変わるはず）
    ctx_other = access_policy.build_requester_context(
        user_id="verify-2f4-other", department=OTHER_DEPT, is_authenticated=True)
    turn3 = chat_service.chat(client, azure_client, model, history=history,
                               message="それの燃料コストは？", requester_context=ctx_other)
    output["turn3_different_identity_same_history"] = {
        "resolved_query": turn3["resolved_query"],
        "insufficient_evidence": turn3["insufficient_evidence"],
        "source_titles": [s.get("title") for s in turn3["source_refs"]],
    }

    forbidden_title = f"{TOKEN} 他部門版"
    output["permission_check"] = {
        "forbidden_title_in_turn1_sources": forbidden_title in output["turn1"]["source_titles"],
        "forbidden_title_in_turn2_sources": forbidden_title in output["turn2"]["source_titles"],
        "own_dept_title_visible_to_own_identity": any(
            f"{TOKEN} 北回り迂回" == t for t in output["turn1"]["source_titles"]),
        "own_dept_title_hidden_from_other_identity": not any(
            f"{TOKEN} 北回り迂回" == t for t in output["turn3_different_identity_same_history"]["source_titles"]),
    }

    with open("C:/Users/269811/AppData/Local/Temp/claude_2f4_validation_results.json", "w",
              encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print("VALIDATION_DONE")

finally:
    for rid in created_report_ids:
        try:
            geo_report_service.delete_report(client, report_id=rid, actor_id="claude-2f4-verify")
        except Exception as e:  # noqa: BLE001
            print(f"cleanup warning (report {rid}): {e}")
    print("CLEANUP_DONE", len(created_report_ids), "reports")
