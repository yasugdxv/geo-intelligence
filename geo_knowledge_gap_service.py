"""地政学ナレッジ＆インテリジェンスDB: Knowledge Gap検出（12章）。

Deterministic Gap（コードが機械的に判定）とAnalytical Gap Candidate（LLMがEvidenceに
基づき提案、推測補完しない）の2種類を扱う。不足情報をAIが推測で補完しない。

Phase3: Geo Intelligence Agent（外部アプリ向け問い合わせ窓口）がKnowledge Sufficiency
insufficient/partialと判定した際のIntelligence Need管理を、既存geo_knowledge_gapsテーブルを
拡張して同じ機構で扱う（別テーブル・別機構は新設しない。origin_type='external_app_request'で
既存のcase_analysis起点Gapと区別する）。
"""
from datetime import datetime, timezone

import geo_audit
import geo_foundation_pack_service
import geo_llm_common

# ---- Phase3: External Request由来Intelligence Need ------------------------

# geo_chat（ダッシュボード自身のチャット機能）が内部的に投げるrequester_app。外部システム
# からの本当の問い合わせと区別するためのみに使う値で、ユーザーには見せない。チャットでの
# 探索的な質問による知識不足はgeo_knowledge_gaps自体には引き続き記録する（record_external_
# request_gapの動作は変えない）が、Intelligence Needs一覧（list_intelligence_needs）からは
# 除外する——外部アプリの問い合わせ管理用ビューに、自社チャットの会話履歴が紛れ込まないように
# するため。
CHAT_UI_REQUESTER_APP = "geo_chat_ui"

# Sufficiency Evaluatorの10分類のうち、欠落が「回答品質上重要」とみなす分析系カテゴリ。
# observed_fact/actor_claim/external_assessment（個々のClaim）やinternal_report/
# scenario_hypothesis（今回のPilotではそもそも希少）は、単独欠落だけではGap登録の
# トリガーにしない（spec: 軽微な情報不足まで大量にGap登録しない）。
CRITICAL_MISSING_CATEGORIES = ("foundation", "precedent", "internal_expert_knowledge",
                               "stakeholder", "relationship")

# 重複判定の閾値（Region一致0.4 + Topic一致0.3 + 質問文の簡易テキスト類似度0.3の加重和）。
# 完全自動マージはスコアが高い場合のみに限定し、中間スコアはduplicate_candidateとして
# 人手確認に回す（spec: 完全自動Mergeがリスクならduplicate_candidateとして扱ってよい）。
DUPLICATE_MERGE_THRESHOLD = 0.6
DUPLICATE_CANDIDATE_THRESHOLD = 0.35

RESEARCH_SUGGESTIONS = {
    "foundation": "対象国・地域のcountry_foundation/actor_profileの投入を検討",
    "precedent": "類似事例のprecedent Knowledgeの投入を検討",
    "internal_expert_knowledge": "社内専門家によるExpert Review Loop（Delta Extraction→Promote）の実施を検討",
    "observed_fact": "一次情報源（政府発表・主要メディア）の追加調査を検討",
    "actor_claim": "当事者発言の追加収集を検討",
    "external_assessment": "シンクタンク等の第三者分析の追加収集を検討",
    "stakeholder": "関連Entityの登録を検討",
    "relationship": "対象Entity間のRelationship登録を検討",
    "internal_report": "社内レポート・過去Case分析の有無を確認",
    "scenario_hypothesis": "関連CaseでのScenario/Hypothesis整理を検討",
}


def _is_important_partial_gap(missing_categories: list, analysis_requested: bool) -> bool:
    """partial判定時にGapを作るべき「重要な不足」かどうか。分析系カテゴリ（Foundation/
    Precedent/Internal Expert Knowledge/Stakeholder/Relationship）が複数欠けている、または
    高度な分析を要求されているのに1つでも欠けている場合のみ重要とみなす。"""
    missing_critical = [c for c in missing_categories if c in CRITICAL_MISSING_CATEGORIES]
    if len(missing_critical) >= 2:
        return True
    return bool(analysis_requested and missing_critical)


def assess_gap_priority(*, sufficiency: str, missing_categories: list, analysis_requested: bool,
                         request_count: int = 1) -> str:
    """Priority判定（初期版、ルールベースのみ。LLM補助・requester/business_contextによる
    重み付けはPhase4以降の拡張余地として残す）。"""
    missing_critical_count = len([c for c in missing_categories if c in CRITICAL_MISSING_CATEGORIES])
    if request_count >= 3 or (sufficiency == "insufficient" and analysis_requested):
        return "high"
    if sufficiency == "insufficient" or missing_critical_count >= 2:
        return "medium"
    return "low"


def _normalize_tokens(text: str) -> set:
    return set((text or "").lower().split())


def text_similarity(a: str, b: str) -> float:
    """簡易Jaccard類似度（トークンの単純な集合演算）。Embedding/pgvectorは導入しない
    （既存方針docs/geopolitical-retrieval-design.md参照）ため、決定的で説明可能な近似に留める。"""
    ta, tb = _normalize_tokens(a), _normalize_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def find_duplicate_candidate(client, *, country_region: list, topic: list, question: str) -> dict | None:
    """既存のOpen/researching/duplicate_candidateなExternal Request起点Gapの中から、
    Region一致・Topic一致・質問文の簡易テキスト類似度を組み合わせて重複候補を探す。
    スコアが高い場合のみ「既存Gapへ統合(merge)」、中間スコアは「duplicate_candidateとして
    フラグ(flag_candidate)」を返す（自動マージのリスクを避ける）。"""
    candidates = client.select("geo_knowledge_gaps", {
        "select": "*", "origin_type": "eq.external_app_request",
        "status": "in.(open,researching,duplicate_candidate)",
    })
    region_set, topic_set = set(country_region or []), set(topic or [])

    best, best_score = None, 0.0
    for c in candidates:
        c_region, c_topic = set(c.get("country_region") or []), set(c.get("topic") or [])
        region_overlap = 1.0 if (region_set & c_region) else 0.0
        topic_overlap = 1.0 if (topic_set & c_topic) else 0.0
        text_sim = text_similarity(question, c.get("question") or "")
        score = region_overlap * 0.4 + topic_overlap * 0.3 + text_sim * 0.3
        if score > best_score:
            best, best_score = c, score

    if best is None or best_score < DUPLICATE_CANDIDATE_THRESHOLD:
        return None
    action = "merge" if best_score >= DUPLICATE_MERGE_THRESHOLD else "flag_candidate"
    return {"gap": best, "action": action, "score": best_score}


def _link_request_to_gap(client, gap_id: str, request_id: str) -> None:
    existing = client.select("geo_knowledge_gap_requests", {
        "select": "gap_request_id", "gap_id": f"eq.{gap_id}", "request_id": f"eq.{request_id}",
    })
    if existing:
        return
    client.insert("geo_knowledge_gap_requests", [{"gap_id": gap_id, "request_id": request_id}],
                  prefer="return=minimal")


def record_external_request_gap(client, *, request_id: str, normalized: dict, sufficiency: str,
                                 missing_categories: list, actor_id: str = "geo_intelligence_agent") -> dict | None:
    """Geo Intelligence Agentから呼ばれるGap登録エントリポイント。
    sufficient: 何もしない（Noneを返す）。
    insufficient: 原則Gap登録。
    partial: _is_important_partial_gapがTrueの場合のみGap登録（軽微な不足では登録しない）。
    重複候補が見つかった場合、スコアに応じて既存Gapへの統合（新規Gapを作らずRequestのみ追加
    紐付け）、またはduplicate_candidateとして新規Gapを作った上で人手確認用にフラグする。
    戻り値はGeo Intelligence Responseの`knowledge_gap`へそのまま反映できる形にする。"""
    if sufficiency == "sufficient":
        return None
    if sufficiency == "partial" and not _is_important_partial_gap(missing_categories, normalized["analysis_requested"]):
        return None

    duplicate = find_duplicate_candidate(
        client, country_region=normalized["country_region"], topic=normalized["themes"],
        question=normalized["question"])

    if duplicate and duplicate["action"] == "merge":
        gap = duplicate["gap"]
        client.update("geo_knowledge_gaps", {"gap_id": f"eq.{gap['gap_id']}"}, {
            "request_count": (gap.get("request_count") or 1) + 1,
        })
        _link_request_to_gap(client, gap["gap_id"], request_id)
        geo_audit.log_audit(client, actor=actor_id, action="knowledge_gap_merge_request",
                             target_table="geo_knowledge_gaps", target_id=gap["gap_id"],
                             detail={"request_id": request_id, "similarity_score": duplicate["score"]})
        return {"exists": True, "gap_id": gap["gap_id"], "description": gap.get("gap_description"),
                "merged_into_existing": True}

    priority = assess_gap_priority(sufficiency=sufficiency, missing_categories=missing_categories,
                                    analysis_requested=normalized["analysis_requested"], request_count=1)
    description = (
        f"「{normalized['question']}」への回答に必要なKnowledgeが"
        f"{'ほぼ存在しません' if sufficiency == 'insufficient' else '部分的に不足しています'}"
        f"（不足カテゴリ: {', '.join(missing_categories) if missing_categories else 'なし'}）。"
    )
    row = {
        "case_id": None, "origin_type": "external_app_request",
        "gap_category": "deterministic", "gap_type": "external_request_knowledge_gap",
        "gap_description": description,
        "why_needed": normalized.get("context") or "外部アプリからの問い合わせに回答するため",
        "missing_evidence": ", ".join(missing_categories) if missing_categories else None,
        "requested_by_app": normalized["requester_app"], "request_type": normalized["request_type"],
        "question": normalized["question"], "country_region": normalized["country_region"],
        "topic": normalized["themes"], "business_context": normalized.get("context"),
        "missing_knowledge": missing_categories, "priority": priority, "status": "open",
        "request_count": 1,
    }
    if duplicate and duplicate["action"] == "flag_candidate":
        row["status"] = "duplicate_candidate"
        row["duplicate_of_gap_id"] = duplicate["gap"]["gap_id"]

    created = client.insert("geo_knowledge_gaps", [row])[0]
    _link_request_to_gap(client, created["gap_id"], request_id)
    geo_audit.log_audit(client, actor=actor_id, action="knowledge_gap_create_external_request",
                         target_table="geo_knowledge_gaps", target_id=created["gap_id"],
                         detail={"request_id": request_id, "priority": priority, "status": row["status"]})
    return {"exists": True, "gap_id": created["gap_id"], "description": description,
            "merged_into_existing": False}


def list_intelligence_needs(client, *, status: str = None) -> list:
    """Geo DashboardのIntelligence Needs一覧向け。external_app_request起点のGapのみを返す
    （既存Case向けGap一覧とは別ビュー）。geo_chat（ダッシュボード自身のチャット）由来の
    探索的な質問（requested_by_app=CHAT_UI_REQUESTER_APP）は、record_external_request_gap
    でのGap記録自体は変えないが、この一覧（＝外部アプリからの本当の問い合わせを管理する画面）
    には含めない。"""
    params = {
        "select": "*", "origin_type": "eq.external_app_request",
        "requested_by_app": f"neq.{CHAT_UI_REQUESTER_APP}",
        "order": "created_at.desc",
    }
    if status:
        params["status"] = f"eq.{status}"
    return client.select("geo_knowledge_gaps", params)


def _recommend_research(missing_categories: list) -> list:
    return [RESEARCH_SUGGESTIONS[c] for c in (missing_categories or []) if c in RESEARCH_SUGGESTIONS]


def get_intelligence_need_detail(client, gap_id: str) -> dict | None:
    """Intelligence Need Detail画面向け。Original Question/Related Requests/Business
    Context/What We Know/What Is Missing/Related Existing Knowledge/Recommended Research
    を組み立てる。What We Know/Related Existing Knowledgeは、紐づくRequestのうち最新の
    Geo Response（response_payload）が実際に提示したreferencesをそのまま使う（推測しない）。"""
    rows = client.select("geo_knowledge_gaps", {"select": "*", "gap_id": f"eq.{gap_id}", "limit": "1"})
    if not rows:
        return None
    gap = rows[0]

    links = client.select("geo_knowledge_gap_requests", {"select": "request_id", "gap_id": f"eq.{gap_id}"})
    request_ids = sorted({link["request_id"] for link in links})

    related_requests, references = [], []
    if request_ids:
        related_requests = client.select("geo_intelligence_requests", {
            "select": "*", "request_id": f"in.({','.join(request_ids)})", "order": "created_at.asc",
        })
        responses = client.select("geo_intelligence_responses", {
            "select": "*", "request_id": f"in.({','.join(request_ids)})", "order": "created_at.desc",
        })
        if responses:
            references = (responses[0].get("response_payload") or {}).get("references") or []

    missing = gap.get("missing_knowledge") or []
    return {
        "gap": gap,
        "original_question": gap.get("question"),
        "related_requests": related_requests,
        "business_context": gap.get("business_context"),
        "what_we_know": references,
        "what_is_missing": missing,
        "related_existing_knowledge": references,
        "recommended_research": _recommend_research(missing),
        "priority": gap.get("priority"),
        "assigned_to": gap.get("assigned_to"),
        "status": gap.get("status"),
        "resolution": {
            "resolved_at": gap.get("resolved_at"), "resolution_report_id": gap.get("resolution_report_id"),
        },
    }


def get_gap_for_request(client, request_id: str) -> dict | None:
    """Request詳細画面から「このRequestが紐づいたIntelligence Need」へ遷移するための逆引き。"""
    links = client.select("geo_knowledge_gap_requests", {
        "select": "gap_id", "request_id": f"eq.{request_id}", "limit": "1",
    })
    if not links:
        return None
    rows = client.select("geo_knowledge_gaps", {"select": "*", "gap_id": f"eq.{links[0]['gap_id']}", "limit": "1"})
    return rows[0] if rows else None


def update_intelligence_need(client, gap_id: str, *, status: str = None, assigned_to: str = None,
                              actor_id: str = "analyst") -> dict:
    """Dashboardからのstatus変更・担当者アサインの最小限の更新（Report Upload連携・正式な
    Resolve運用はPhase4で拡張する）。

    地政学アプリClooseout Phase6: status="dismissed"はこの汎用更新経路では受け付けない。
    dismiss_intelligence_need（専用endpoint）はresolved_by/resolution_noteを必須で記録し、
    監査アクションもintelligence_need_dismissとして区別しているため、汎用PATCHでの
    dismissedへの変更を禁止し、専用経路の利用を強制する。"""
    if status == "dismissed":
        raise ValueError("statusをdismissedへ変更する場合は専用のdismiss_intelligence_need"
                         "（POST /api/geo/intelligence-needs/{gap_id}/dismiss）を使用してください")
    patch = {}
    if status is not None:
        patch["status"] = status
    if assigned_to is not None:
        patch["assigned_to"] = assigned_to
    if not patch:
        raise ValueError("statusまたはassigned_toのいずれかを指定してください")
    client.update("geo_knowledge_gaps", {"gap_id": f"eq.{gap_id}"}, patch)
    geo_audit.log_audit(client, actor=actor_id, action="intelligence_need_update",
                         target_table="geo_knowledge_gaps", target_id=gap_id, detail=patch)
    rows = client.select("geo_knowledge_gaps", {"select": "*", "gap_id": f"eq.{gap_id}", "limit": "1"})
    return rows[0]


class IntelligenceNeedResolutionError(Exception):
    """resolved状態への遷移条件（Reportとの紐付け必須）を満たさない場合に送出する。"""


def resolve_intelligence_need(client, gap_id: str, *, report_id: str, resolved_by: str,
                               resolution_note: str = None, actor_id: str = "analyst") -> dict:
    """Intelligence NeedをresolvedにするPhase4の正式な導線。単にstatusを変えるだけの状態には
    しない: resolution_report_id/resolved_at/resolved_byを必ず同時に設定し、Report↔Gapの
    リンク（geo_report_gaps）が無ければここで追加する。Reportが存在しない場合はエラーにする
    （spec: 「単にStatusだけ変え、Reportと紐付かない状態にはしない」）。"""
    reports = client.select("geo_reports", {"select": "*", "report_id": f"eq.{report_id}", "limit": "1"})
    if not reports:
        raise IntelligenceNeedResolutionError(f"report_idが見つかりません: {report_id}")
    report = reports[0]

    gaps = client.select("geo_knowledge_gaps", {"select": "*", "gap_id": f"eq.{gap_id}", "limit": "1"})
    if not gaps:
        raise IntelligenceNeedResolutionError(f"gap_idが見つかりません: {gap_id}")

    existing_link = client.select("geo_report_gaps", {
        "select": "*", "report_id": f"eq.{report_id}", "gap_id": f"eq.{gap_id}"})
    if not existing_link:
        client.insert("geo_report_gaps", [{"report_id": report_id, "gap_id": gap_id}], prefer="return=minimal")

    resolved_at = datetime.now(timezone.utc).isoformat()
    patch = {
        "status": "resolved", "resolution_report_id": report["document_id"],
        "resolved_at": resolved_at, "resolved_by": resolved_by, "resolution_note": resolution_note,
    }
    client.update("geo_knowledge_gaps", {"gap_id": f"eq.{gap_id}"}, patch)
    geo_audit.log_audit(client, actor=actor_id, action="intelligence_need_resolve",
                         target_table="geo_knowledge_gaps", target_id=gap_id,
                         detail={"report_id": report_id, "resolved_by": resolved_by})
    rows = client.select("geo_knowledge_gaps", {"select": "*", "gap_id": f"eq.{gap_id}", "limit": "1"})
    return rows[0]


def dismiss_intelligence_need(client, gap_id: str, *, dismissed_by: str, reason: str = None,
                               actor_id: str = "analyst") -> dict:
    """調査不要と判断した場合の例外的な遷移。Reportとの紐付けは要求しない。"""
    client.update("geo_knowledge_gaps", {"gap_id": f"eq.{gap_id}"}, {
        "status": "dismissed", "resolved_by": dismissed_by, "resolution_note": reason,
    })
    geo_audit.log_audit(client, actor=actor_id, action="intelligence_need_dismiss",
                         target_table="geo_knowledge_gaps", target_id=gap_id,
                         detail={"dismissed_by": dismissed_by, "reason": reason})
    rows = client.select("geo_knowledge_gaps", {"select": "*", "gap_id": f"eq.{gap_id}", "limit": "1"})
    return rows[0]


# ---- Phase B-C: 非同期Research（Intelligence AI / Expert AI Role Separation） -------------

def record_research_response(client, gap_id: str, *, expert_response_id: str,
                              actor_id: str = "geo_research_worker") -> dict:
    """run_research_request()がExpert AI呼び出し成功後に呼ぶ。Gapへ「現在採用されている
    Research Response」を記録するだけで、status=resolvedへは遷移させない（Research完了と
    Gap解決は別概念。Expert AIの生成物はまだcandidate Knowledgeであり、Human Reviewで
    activeへ昇格するまでGapは未解決のまま——既存candidate/active lifecycleを迂回しない）。"""
    client.update("geo_knowledge_gaps", {"gap_id": f"eq.{gap_id}"}, {
        "resolution_expert_response_id": expert_response_id,
    })
    geo_audit.log_audit(client, actor=actor_id, action="knowledge_gap_research_response_recorded",
                         target_table="geo_knowledge_gaps", target_id=gap_id,
                         detail={"expert_response_id": expert_response_id})
    rows = client.select("geo_knowledge_gaps", {"select": "*", "gap_id": f"eq.{gap_id}", "limit": "1"})
    return rows[0]


def resolve_intelligence_need_from_promoted_knowledge(client, gap_id: str, *, knowledge_item_id: str,
                                                       resolved_by: str, resolution_note: str = None,
                                                       actor_id: str = "analyst") -> dict:
    """Phase B-C版のresolve導線。resolve_intelligence_need（Report必須）と対になる、
    非同期Research由来のcandidate KnowledgeがHuman Reviewでactiveへ昇格した後にのみ呼ぶ
    （既存review_knowledge_item自体は変更しない、その後の別呼び出しとして提供する）。
    Knowledgeがまだcandidate/in_review等でactiveでない場合はエラーにする——「Expert AIが
    生成しただけ」でGapを解決済み扱いにしない、という設計原則を機械的に強制するため。"""
    items = client.select("geo_knowledge_items", {
        "select": "*", "knowledge_item_id": f"eq.{knowledge_item_id}", "limit": "1"})
    if not items:
        raise IntelligenceNeedResolutionError(f"knowledge_item_idが見つかりません: {knowledge_item_id}")
    if items[0].get("status") != "active":
        raise IntelligenceNeedResolutionError(
            f"knowledge_item {knowledge_item_id} はまだactiveではありません"
            f"（現在のstatus: {items[0].get('status')}）。Human Reviewでのactive昇格後に呼んでください。")

    gaps = client.select("geo_knowledge_gaps", {"select": "*", "gap_id": f"eq.{gap_id}", "limit": "1"})
    if not gaps:
        raise IntelligenceNeedResolutionError(f"gap_idが見つかりません: {gap_id}")

    resolved_at = datetime.now(timezone.utc).isoformat()
    note = f"[knowledge_item_id={knowledge_item_id}] {resolution_note or ''}".strip()
    client.update("geo_knowledge_gaps", {"gap_id": f"eq.{gap_id}"}, {
        "status": "resolved", "resolved_at": resolved_at, "resolved_by": resolved_by,
        "resolution_note": note,
    })
    geo_audit.log_audit(client, actor=actor_id, action="intelligence_need_resolve_from_knowledge",
                         target_table="geo_knowledge_gaps", target_id=gap_id,
                         detail={"knowledge_item_id": knowledge_item_id, "resolved_by": resolved_by})
    rows = client.select("geo_knowledge_gaps", {"select": "*", "gap_id": f"eq.{gap_id}", "limit": "1"})
    return rows[0]


def reopen_research_gap_after_rejection(client, gap_id: str, *, knowledge_item_id: str,
                                        reopened_by: str, reason: str = None,
                                        actor_id: str = "analyst") -> dict:
    """Phase B-D P0: Human ReviewでcandidateがRejectされた場合、そのGapを再調査可能な状態へ
    戻す。resolution_expert_response_idが設定済みのままだと、run_research_requestの冪等性判定
    （already_researched）により未来永劫再調査されなくなるため、必ずこの関数でNoneへ戻してから
    再度run_research_requestを呼べるようにする。過去の実行履歴（geo_expert_requests/
    geo_expert_responses、rejectされたgeo_knowledge_items行自体）は一切削除しない——
    「現在採用している1件」の参照を外すだけで、履歴はそのまま監査可能な状態で残す。

    誤って任意の状態のGapを再openできないよう、対象knowledge_itemが実際にstatus="rejected"
    であることを要求する（Reject以外の理由でのGap再open操作にはこの関数を使わない）。"""
    items = client.select("geo_knowledge_items", {
        "select": "*", "knowledge_item_id": f"eq.{knowledge_item_id}", "limit": "1"})
    if not items:
        raise IntelligenceNeedResolutionError(f"knowledge_item_idが見つかりません: {knowledge_item_id}")
    if items[0].get("status") != "rejected":
        raise IntelligenceNeedResolutionError(
            f"knowledge_item {knowledge_item_id} はrejectedではありません"
            f"（現在のstatus: {items[0].get('status')}）。Human ReviewでReject後にのみ呼んでください。")

    gaps = client.select("geo_knowledge_gaps", {"select": "*", "gap_id": f"eq.{gap_id}", "limit": "1"})
    if not gaps:
        raise IntelligenceNeedResolutionError(f"gap_idが見つかりません: {gap_id}")
    if gaps[0].get("status") == "resolved":
        raise IntelligenceNeedResolutionError(
            f"gap {gap_id} は既にresolved済みです。resolved後のReject処理は想定していません"
            "（resolve前＝Human Review未了の段階でのみRejectからの再openを行う）。")

    client.update("geo_knowledge_gaps", {"gap_id": f"eq.{gap_id}"}, {
        "status": "open", "resolution_expert_response_id": None,
    })
    geo_audit.log_audit(client, actor=actor_id, action="knowledge_gap_reopened_after_rejection",
                         target_table="geo_knowledge_gaps", target_id=gap_id,
                         detail={"knowledge_item_id": knowledge_item_id, "reopened_by": reopened_by,
                                 "reason": reason})
    rows = client.select("geo_knowledge_gaps", {"select": "*", "gap_id": f"eq.{gap_id}", "limit": "1"})
    return rows[0]


def research_status_for_gap(client, gap_id: str) -> str:
    """department_intelligence.research_status（none/requested/in_progress/completed/failed）
    をGapの現在状態から導出する。新規カラムは増やさず、既存statusとresolution_expert_
    response_idの組み合わせだけで表現する（API向けの導出値であり、DBには保存しない）。

    本番実データで判明した不具合の修正: statusは"open"以外にも"duplicate_candidate"
    （重複候補としてflagされたが実体はまだ未着手のGap）等の値を取りうる。「未着手」判定を
    "open"のみに限定すると、duplicate_candidate等が誤って"none"（調査不要）扱いになって
    しまう。"dismissed"（人間が明示的に調査不要と判断）だけを"none"とし、それ以外の
    未解決status（open/duplicate_candidate/in_progress等、将来の値を含む）は全て
    "requested"扱いにする方が安全（Retrieved=Coveredにしない、と同じ考え方）。"""
    rows = client.select("geo_knowledge_gaps", {"select": "*", "gap_id": f"eq.{gap_id}", "limit": "1"})
    if not rows:
        return "none"
    gap = rows[0]
    if gap.get("status") == "resolved":
        return "completed"
    if gap.get("resolution_expert_response_id"):
        return "completed"
    if gap.get("status") == "researching":
        return "in_progress"
    if gap.get("status") == "dismissed":
        return "none"
    if _has_failed_research_attempt(client, gap["gap_id"]):
        return "failed"
    return "requested"


def _has_failed_research_attempt(client, gap_id: str) -> bool:
    """statusがopenへ戻っている（＝research失敗でロールバックされた）gapについて、直近の
    監査ログにrun_research_request失敗の記録があるかを確認する（新規カラムなしでfailedを
    表現するための読み取り専用の補助）。"""
    logs = client.select("geo_audit_logs", {
        "select": "action,created_at", "target_table": "eq.geo_knowledge_gaps",
        "target_id": f"eq.{gap_id}", "order": "created_at.desc", "limit": "1",
    })
    return bool(logs) and logs[0].get("action") == "knowledge_gap_research_failed"


DETERMINISTIC_GAP_TYPES = (
    "missing_entity", "missing_actor_profile", "missing_relationship", "missing_dependency",
    "missing_historical_context", "missing_precedent", "missing_legal_basis",
    "missing_quantitative_data", "stale_knowledge", "conflicting_evidence",
    "insufficient_source_diversity", "missing_indicator",
)
ANALYTICAL_GAP_TYPES = (
    "applicability_gap", "precedent_comparison_gap", "causal_gap", "legal_institutional_gap",
    "actor_intent_gap", "countermeasure_gap", "enforcement_gap",
    "alternative_explanation_gap", "falsification_gap",
)

ANALYTICAL_GAP_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "AnalyticalGapCandidates",
    "type": "object",
    "additionalProperties": False,
    "required": ["gaps"],
    "properties": {
        "gaps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "gap_type", "gap_description", "why_needed", "available_evidence",
                    "missing_evidence", "recommended_source_categories", "recommended_queries",
                    "priority", "uncertainty",
                ],
                "properties": {
                    "gap_type": {"type": "string", "enum": list(ANALYTICAL_GAP_TYPES)},
                    "gap_description": {"type": "string"},
                    "why_needed": {"type": "string"},
                    "related_hypothesis_statement": {"type": ["string", "null"]},
                    "available_evidence": {"type": "string"},
                    "missing_evidence": {"type": "string"},
                    "recommended_source_categories": {"type": "array", "items": {"type": "string"}},
                    "recommended_queries": {"type": "array", "items": {"type": "string"}},
                    "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                    "uncertainty": {"type": "string"},
                },
            },
        },
    },
}


def _insert_gap_if_new(client, case_id: str, gap_category: str, gap_type: str, **fields) -> dict | None:
    existing = client.select("geo_knowledge_gaps", {
        "select": "gap_id", "case_id": f"eq.{case_id}", "gap_category": f"eq.{gap_category}",
        "gap_type": f"eq.{gap_type}", "status": "eq.open", "limit": "1",
    })
    if existing:
        return None
    row = {"case_id": case_id, "gap_category": gap_category, "gap_type": gap_type, **fields}
    return client.insert("geo_knowledge_gaps", [row])[0]


def detect_and_create_deterministic_gaps(client, case_id: str, *, actor_id: str) -> list:
    """Foundation Packの内容から機械的に不足を判定し、geo_knowledge_gapsへ登録する
    （同一case・同一gap_typeで既にopenなgapがあれば重複登録しない）。"""
    pack = geo_foundation_pack_service.build_foundation_pack(client, case_id)
    created = []

    def add(gap_type: str, description: str, why_needed: str, priority: str = "medium"):
        row = _insert_gap_if_new(client, case_id, "deterministic", gap_type,
                                  gap_description=description, why_needed=why_needed, priority=priority)
        if row:
            created.append(row)

    entities = pack["2_relevant_entities"]
    if not entities:
        add("missing_entity", "この案件に紐づくEntityが登録されていません",
            "Foundation Packの基礎となる主体情報が無いと分析を開始できない", "high")

    actor_profile_entity_ids = {
        item["structured_content"].get("entity_id")
        for item in pack["5_objectives_core_interests"]
    }
    for entity in entities:
        if entity["entity_id"] not in actor_profile_entity_ids:
            add("missing_actor_profile", f"{entity['canonical_name']}のActor Profileがありません",
                "目的・優先順位・レッドラインが無いと意思決定分析ができない")

    if len(entities) >= 2 and not pack["6_relationships_and_dependencies"]["relationships"]:
        add("missing_relationship", "対象Entity間のRelationshipが登録されていません",
            "誰が誰に依存し、どちらが影響力を持つかが分析できない", "high")

    if entities and not pack["6_relationships_and_dependencies"]["dependencies"]:
        add("missing_dependency", "対象Entityの依存関係（Dependency）が登録されていません",
            "エネルギー・貿易・安全保障等の依存構造が無いとLeverage分析ができない")

    if not pack["10_historical_context"]:
        add("missing_historical_context", "関連するHistorical Path Dependencyがありません",
            "歴史的経緯が現在の判断にどう影響するかを分析できない")

    if not pack["11_relevant_precedents"]:
        add("missing_precedent", "関連するPrecedent（前例）がありません",
            "前例に基づく実現可能性・条件の比較ができない", "high")

    has_legal_basis = any(
        (p.get("structured_content") or {}).get("legal_and_institutional_conditions")
        for p in pack["11_relevant_precedents"]
    )
    if pack["11_relevant_precedents"] and not has_legal_basis:
        add("missing_legal_basis", "前例の法的・制度的根拠が記録されていません",
            "実現可能性の判断には法的・制度的根拠の明記が必須")

    if entities and not any(a.get("quantitative_value") for a in pack["7_assets"]):
        add("missing_quantitative_data", "対象Entityの定量的Assetデータがありません",
            "規模感（軍事力・市場規模等）を伴わない定性評価だけでは説得力が弱い", "low")

    stale = [k for k in (pack["5_objectives_core_interests"] + pack["10_historical_context"]
                         + pack["11_relevant_precedents"] + pack["12_relevant_mechanisms"])
             if k.get("next_review_at") and k["next_review_at"] < pack["1_event_question_overview"]["as_of_date"]]
    if stale:
        add("stale_knowledge", f"{len(stale)}件のKnowledge Itemがnext_review_atを過ぎています",
            "古い前提のまま参照すると判断を誤る可能性がある")

    if pack["22_confidence_and_caveats"]["contested_knowledge_count"] > 0:
        add("conflicting_evidence", "対立するKnowledge（contested）が存在します",
            "本文に反映する前に対立点を解消・明示する必要がある")

    documents_referenced = {
        ev.get("document_chunk_id") for c in pack["14_existing_claims_and_evidence"]
        for ev in c["evidence"] if ev.get("document_chunk_id")
    }
    if pack["14_existing_claims_and_evidence"] and len(documents_referenced) < 2:
        add("insufficient_source_diversity", "根拠が1つの文書チャンクに偏っています",
            "単一ソースへの依存はcorroboration不足のリスクがある")

    if not pack["20_scenarios_and_indicators"]:
        add("missing_indicator", "監視すべきIndicatorが登録されていません",
            "シナリオの成否を判定する観測指標が無いと事後検証ができない")

    geo_audit.log_audit(client, actor=actor_id, action="knowledge_gap_detect_deterministic",
                         target_table="geo_knowledge_gaps", target_id=case_id,
                         detail={"created_count": len(created)})
    return created


def generate_analytical_gap_candidates(client, azure_client, model: str, case_id: str, *,
                                        actor_id: str) -> list:
    """LLMにFoundation Packの内容を渡し、Analytical Gap Candidateを提案させる
    （不足情報を推測で補完させず、available_evidence/missing_evidenceを明記させる）。"""
    pack = geo_foundation_pack_service.build_foundation_pack(client, case_id)
    hypotheses = pack["18_main_hypothesis"] + pack["19_alternative_hypotheses"]

    system_prompt = (
        "あなたは地政学分析のKnowledge Gap検出アシスタントです。与えられたFoundation Packの"
        "内容だけを根拠に、分析上まだ確認できていない論点（Analytical Gap）を提案してください。"
        "根拠の無い推測でgapの中身を埋めてはいけません。available_evidenceには実際に存在する"
        "根拠を、missing_evidenceには不足している具体的な情報を書いてください。"
    )
    user_prompt = (
        f"案件: {pack['1_event_question_overview']['case_title']}\n"
        f"問い: {pack['1_event_question_overview']['original_question']}\n"
        f"主仮説・代替仮説: {[h['hypothesis_statement'] for h in hypotheses]}\n"
        f"既存Precedent数: {len(pack['11_relevant_precedents'])}、"
        f"既存Mechanism数: {len(pack['12_relevant_mechanisms'])}、"
        f"既存Claim数: {len(pack['14_existing_claims_and_evidence'])}"
    )

    result = geo_llm_common.call_llm_structured(
        azure_client, model, system_prompt, user_prompt, ANALYTICAL_GAP_SCHEMA, "geo_analytical_gaps")
    gaps = result["data"].get("gaps", [])

    created = []
    for gap in gaps:
        related_hypothesis_id = None
        statement = gap.get("related_hypothesis_statement")
        if statement:
            for h in hypotheses:
                if h["hypothesis_statement"] == statement:
                    related_hypothesis_id = h["hypothesis_id"]
                    break
        row = client.insert("geo_knowledge_gaps", [{
            "case_id": case_id, "gap_category": "analytical_candidate",
            "gap_type": gap["gap_type"], "gap_description": gap["gap_description"],
            "why_needed": gap["why_needed"], "related_hypothesis_id": related_hypothesis_id,
            "available_evidence": gap["available_evidence"], "missing_evidence": gap["missing_evidence"],
            "recommended_source_categories": gap["recommended_source_categories"],
            "recommended_queries": gap["recommended_queries"], "priority": gap["priority"],
            "uncertainty": gap["uncertainty"],
        }])[0]
        created.append(row)

    geo_audit.log_audit(client, actor=actor_id, action="knowledge_gap_generate_analytical",
                         target_table="geo_knowledge_gaps", target_id=case_id,
                         detail={"created_count": len(created)})
    return created
