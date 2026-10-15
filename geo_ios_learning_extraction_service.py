"""Geopolitical Intelligence OS Phase G: Expert Learning Loop / Before-After Learning Extraction。

最重要原則（絶対条件）: Expert CommentをそのままExpert AIへ学習させない。必ずBefore(Initial AI)/
Expert Intervention/Response/Final(Approved)を比較し、「指摘が実際に最終成果物へ反映されたか」を
検証したComment（resolved/partially_resolved）だけをLearning Extractionの入力とする。Rejected/
not_applicable/スタイルのみのコメントは学習対象から除外する（§4）。

責務（§14）: collect source package → determine eligible evidence → LLM structured extraction
（1回、§15） → normalize → dedup/conflict check → geo_ios_learning_candidates作成
（status="candidate"、絶対にactiveへ自動昇格しない）。

既存Expert AI Core Reasoning Pipeline（geo_expert_engine.py）は一切呼ばない・変更しない——
本サービスの構造化LLM呼び出しは既存の汎用ユーティリティgeo_llm_common.call_llm_structured
（geo_knowledge_gap_service.generate_analytical_gap_candidates等が既に使っている、Expert AI
Engine固有ではない共通部品）のみを使う。
"""
import hashlib
import json

import geo_audit
import geo_ios_analysis_session_service as session_service
import geo_ios_approval_service as approval_service
import geo_ios_knowledge_publication_service as publication_service
import geo_ios_learning_candidate_service as learning_service
import geo_ios_report_draft_service as draft_service
import geo_ios_review_service as review_service
import geo_knowledge_gap_service  # text_similarity（既存Dedupロジック）を再利用
import geo_llm_common
import geo_report_studio_service as report_studio_service

ELIGIBLE_RESOLUTION_STATUSES = ("resolved", "partially_resolved")
DEFAULT_PROMPT_VERSION = "geo-ios-learning-extraction-v1"

# §18-19 Dedup/Conflict閾値。geo_knowledge_gap_service.DUPLICATE_MERGE_THRESHOLD(0.6)と
# 同じ考え方（完全自動マージはリスクが高いため高めの閾値のみ真のDuplicate扱いとする）。
DUPLICATE_SIMILARITY_THRESHOLD = 0.75
RELATED_SIMILARITY_THRESHOLD = 0.35

# CQAS Signal（§32-33）判定用の最小類似度。これを下回る場合は「そもそも候補群の中に
# この論点に近いものが無かった」＝generation_missとみなす。
CQAS_SIGNAL_MIN_SIMILARITY = 0.15


class LearningExtractionError(Exception):
    """Learning Extractionの前提条件違反・処理エラー。"""


# ---- §3 Learning Source Package ---------------------------------------------

def build_learning_source_package(client, report_project_id: str) -> dict:
    project = report_studio_service.get_project(client, report_project_id)
    if not project:
        raise LearningExtractionError(f"report_project_idが見つかりません: {report_project_id}")

    analysis_session = session_service.get_session_by_report_project(client, report_project_id)
    scenario_candidates, design_history, draft_history = [], [], []
    if analysis_session:
        scenario_candidates = session_service.list_scenario_candidates(
            client, analysis_session["analysis_session_id"])
        design_history = session_service.list_design_history(
            client, analysis_session["analysis_session_id"])
        # draft_history: Review-bound Before/After解決（review_cycle_id指定時のみ使用）用。
        # design_history（Analysis Designの変遷）とは別物——実際にレビュー対象になった
        # Report Draft本文（geo_ios_report_drafts）の全version。
        draft_history = draft_service.list_draft_history(client, analysis_session["analysis_session_id"])

    review_cycles = review_service.list_review_cycles(client, report_project_id)
    cycles_with_comments = [
        {"cycle": cycle, "comments": review_service.list_comments(client, cycle["review_cycle_id"])}
        for cycle in review_cycles
    ]
    approval_event = approval_service.get_current_approval(client, report_project_id)
    published_knowledge = publication_service.get_active_knowledge_for_report_project(client, report_project_id)

    return {
        "project": project, "analysis_session": analysis_session,
        "scenario_candidates": scenario_candidates, "design_history": design_history,
        "draft_history": draft_history,
        "review_cycles": cycles_with_comments, "approval_event": approval_event,
        "published_knowledge": published_knowledge,
    }


# ---- §4 Source Eligibility ---------------------------------------------------

def eligible_comments(source_package: dict) -> list:
    """resolved/partially_resolvedのみを学習対象にする。rejected/not_applicable/未対応(open)は
    除外する（§4絶対条件: Rejected Commentを無条件で学習しない。本Phaseではcontested_pattern
    のような例外扱いは実装せず、単純に除外する——安全側の簡素な設計を優先）。"""
    out = []
    for entry in source_package["review_cycles"]:
        for comment in entry["comments"]:
            if comment.get("resolved_status") in ELIGIBLE_RESOLUTION_STATUSES:
                out.append({"cycle": entry["cycle"], "comment": comment})
    return out


# ---- Review-bound Before/After解決（2026-09-24追加、Learning Workbench対応） --
#
# 従来のProject単位抽出（design_history[0]/[-1]）は、複数Review Roundがある場合に
# 別Roundの変更まで混在しうる不備があった（3回のPlan Modeレビューで確定した対応方針）。
# ここに定義する関数群は、特定の1 review_cycleに対するBefore/Afterを、実際にレビューされた
# geo_ios_report_drafts（Analysis Designではなく Report Draft本文）から、祖先チェーンを
# 検証した上で解決する。extract_learning_candidatesがreview_cycle_idを指定された場合のみ
# 使う——省略時の既存Project単位経路は一切変更しない。

def _draft_ancestor_chain(draft_history: list, report_draft_id: str) -> list:
    """report_draft_id自身を含め、parent_report_draft_idを遡った祖先チェーンを返す
    （[自身, 親, 祖父, ...]の順）。draft_historyは同一analysis_session_id内の全version。"""
    by_id = {d["report_draft_id"]: d for d in draft_history}
    chain, seen, current = [], set(), by_id.get(report_draft_id)
    while current and current["report_draft_id"] not in seen:
        seen.add(current["report_draft_id"])
        chain.append(current["report_draft_id"])
        current = by_id.get(current.get("parent_report_draft_id"))
    return chain


def resolve_review_bound_after(*, review_cycle: dict, all_cycles: list, cycle_comments: list,
                                approval_event: dict | None, draft_history: list) -> dict:
    """1つのreview_cycleについて、After Draftを優先順位チェーンで解決する
    （3回のPlan Modeレビューで確定した仕様、A章）。戻り値:
    {"before_draft": dict|None, "after_draft": dict|None,
     "after_resolution_method": "resolved_in_version"|"next_round"|"final_approval"|"not_evaluable"}
    """
    before_draft_id = review_cycle.get("source_report_draft_id")
    by_id = {d["report_draft_id"]: d for d in draft_history}
    before_draft = by_id.get(before_draft_id) if before_draft_id else None
    not_evaluable = {"before_draft": before_draft, "after_draft": None,
                      "after_resolution_method": "not_evaluable"}
    if not before_draft:
        return not_evaluable

    # 優先度1: このRoundのeligible commentsのresolved_in_versionを集め、それら全てを
    # 内包する最新のDraft versionを候補にする。version番号が最大というだけでは全変更を
    # 含む保証にならないため、(a) Beforeの子孫、(b) 同一analysis_session、
    # (c) 集めたresolved_in_versionの全てが候補の祖先チェーン上にあること、を検証する。
    resolved_versions = sorted({
        c.get("resolved_in_version") for c in cycle_comments if c.get("resolved_in_version")})
    if resolved_versions:
        candidate_draft_id = max(
            resolved_versions,
            key=lambda did: (by_id.get(did) or {}).get("version", -1))
        candidate_draft = by_id.get(candidate_draft_id)
        if candidate_draft:
            chain = _draft_ancestor_chain(draft_history, candidate_draft_id)
            is_descendant = before_draft_id in chain
            same_session = candidate_draft.get("analysis_session_id") == before_draft.get("analysis_session_id")
            all_ancestors = all(rv in chain for rv in resolved_versions)
            if is_descendant and same_session and all_ancestors:
                return {"before_draft": before_draft, "after_draft": candidate_draft,
                        "after_resolution_method": "resolved_in_version"}

    # 優先度2: 次のReview Round（round_number昇順で1つ後）
    later_cycles = sorted(
        (c for c in all_cycles if (c.get("round_number") or 0) > (review_cycle.get("round_number") or 0)),
        key=lambda c: c.get("round_number") or 0)
    if later_cycles:
        next_draft = by_id.get(later_cycles[0].get("source_report_draft_id"))
        if next_draft:
            return {"before_draft": before_draft, "after_draft": next_draft,
                    "after_resolution_method": "next_round"}

    # 優先度3: 次のRoundが無い（＝これが最終Round）場合のみ、Approval Eventの承認済みDraft
    if not later_cycles and approval_event:
        final_draft = by_id.get(approval_event.get("source_report_draft_id"))
        if final_draft:
            return {"before_draft": before_draft, "after_draft": final_draft,
                    "after_resolution_method": "final_approval"}

    return not_evaluable


# ---- §17 Idempotency ---------------------------------------------------------

def _compute_fingerprint(*, report_project_id: str, review_cycle_ids: list, prompt_version: str,
                          extraction_model: str) -> str:
    basis = "|".join([report_project_id, ",".join(sorted(review_cycle_ids)), prompt_version, extraction_model])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _existing_candidates_linked_to_cycles(client, review_cycle_ids: list) -> list:
    seen, out = set(), []
    for cycle_id in review_cycle_ids:
        rows = client.select("geo_ios_learning_candidates", {
            "select": "*", "source_review_cycle_id": f"eq.{cycle_id}"})
        for row in rows:
            if row["candidate_id"] not in seen:
                seen.add(row["candidate_id"])
                out.append(row)
    return out


# ---- §32-33 CQAS Learning Signal ---------------------------------------------

def _compute_cqas_signal(scenario_candidates: list, selected_scenario_id: str, generalized_rule: str) -> dict | None:
    """既存Scenario Candidate全件保持（Phase C）を使い、Expertの指摘が
    generation_miss/ranking_miss/selection_miss/validated_candidateのどれかを判定する
    （§32-33。CQAS rankingアルゴリズム自体は変更しない、§34、記録のみ）。"""
    if not scenario_candidates or not generalized_rule:
        return None
    best_score, best_candidate = 0.0, None
    for candidate in scenario_candidates:
        content = candidate.get("content") or {}
        text = " ".join(filter(None, [candidate.get("title"), content.get("why_this_matters")]))
        score = geo_knowledge_gap_service.text_similarity(generalized_rule, text)
        if score > best_score:
            best_score, best_candidate = score, candidate

    if best_candidate is None or best_score < CQAS_SIGNAL_MIN_SIMILARITY:
        return {"signal": "generation_miss", "closest_candidate_id": None, "similarity": round(best_score, 3)}
    if best_candidate["scenario_candidate_id"] == selected_scenario_id:
        signal = "validated_candidate"
    elif best_candidate.get("displayed"):
        signal = "selection_miss"
    else:
        signal = "ranking_miss"
    return {"signal": signal, "closest_candidate_id": best_candidate["scenario_candidate_id"],
            "similarity": round(best_score, 3)}


# ---- §18 Dedup / §19 Conflict -------------------------------------------------

def _check_dedup_and_conflict(client, *, candidate_type: str, generalized_rule: str) -> dict:
    """新Candidateを、既存Learning Candidate（全status）・既存Active Knowledgeの両方と比較する
    （§18）。高類似度=duplicate（新規作成をスキップ）、中類似度=related（作成はするがフラグを
    付与しHuman Reviewへ）。意味的な矛盾の自動判定は行わない——中類似度帯は
    "possible_conflict_or_overlap"として一律Human Reviewへ委ねる（誤ったConflict自動判定を
    避けるための簡素化、正直に限界を明示する）。"""
    existing_candidates = learning_service.list_candidates(client, candidate_type=candidate_type, limit=500)
    existing_knowledge = client.select("geo_knowledge_items", {
        "select": "knowledge_item_id,title,summary,structured_content,status",
        "status": "eq.active", "limit": "500"})

    best_score, best_match = 0.0, None
    for row in existing_candidates:
        score = geo_knowledge_gap_service.text_similarity(generalized_rule, row.get("generalized_rule") or "")
        if score > best_score:
            best_score, best_match = score, {"kind": "learning_candidate", "id": row["candidate_id"], "score": score}
    for row in existing_knowledge:
        content = row.get("structured_content") or {}
        text = " ".join(filter(None, [row.get("title"), row.get("summary"), content.get("rule")]))
        score = geo_knowledge_gap_service.text_similarity(generalized_rule, text)
        if score > best_score:
            best_score, best_match = score, {"kind": "knowledge_item", "id": row["knowledge_item_id"], "score": score}

    if best_match is None or best_score < RELATED_SIMILARITY_THRESHOLD:
        return {"dedup_status": "new", "related": []}
    if best_score >= DUPLICATE_SIMILARITY_THRESHOLD:
        return {"dedup_status": "duplicate", "related": [best_match]}
    return {"dedup_status": "possible_conflict_or_overlap", "related": [best_match]}


# ---- §6 Extraction Question / LLM Schema -------------------------------------

EXTRACTION_SYSTEM_PROMPT = (
    "あなたは地政学分析組織のLearning Extraction Analystです。Report ReviewのBefore/After（"
    "初期AI分析・Expert指摘・Authorの対応・最終Approved Report）を比較し、将来の分析に再利用"
    "可能な学習知を抽出します。単に『何を学べるか』を聞かれているのではなく、各候補について"
    "以下を厳密に判定してください:\n"
    "1. 初期AI分析の何が不足・弱かったか\n"
    "2. Expertは何を指摘したか\n"
    "3. その指摘は実際に受け入れられ、最終成果物へ反映されたか（yes/noを厳密に判定。"
    "反映されていないなら候補を作らない）\n"
    "4. 承認された成果物では何が変わったか\n"
    "5. このReportを超えて再利用可能か（report固有の事実は絶対に汎用Ruleにしない）\n"
    "6. どういうTrigger/文脈で再利用可能か\n"
    "7. 例外・限界は何か\n"
    "8. どのExpert Knowledge分類に該当するか\n"
    "推測で一般化しないでください。Expertの意図を過剰に一般化しないでください。"
    "時点依存の判断（特定の会談日程・特定の一時的な政治情勢等）は恒久Ruleにしないでください。"
)

EXTRACTION_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "LearningExtractionResult",
    "type": "object",
    "additionalProperties": False,
    "required": ["candidates"],
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "candidate_type", "title", "initial_ai_weakness", "expert_observation",
                    "was_accepted_and_reflected", "final_outcome", "generalized_rule",
                    "is_reusable_beyond_report", "trigger", "exceptions", "scope",
                    "temporal_stability", "confidence", "source_comment_indices",
                ],
                "properties": {
                    "candidate_type": {"type": "string", "enum": list(learning_service.CANDIDATE_TYPES)},
                    "title": {"type": "string"},
                    "initial_ai_weakness": {"type": "string"},
                    "expert_observation": {"type": "string"},
                    "was_accepted_and_reflected": {"type": "boolean"},
                    "final_outcome": {"type": "string"},
                    "generalized_rule": {"type": "string"},
                    "is_reusable_beyond_report": {"type": "boolean"},
                    "trigger": {"type": "string"},
                    "exceptions": {"type": "array", "items": {"type": "string"}},
                    "positive_example": {"type": ["string", "null"]},
                    "failure_pattern_example": {"type": ["string", "null"]},
                    "scope": {"type": "string", "enum": [
                        "report_specific", "theme_specific", "actor_specific", "domain_general", "general"]},
                    "temporal_stability": {"type": "string", "enum": [
                        "stable", "slow_changing", "time_sensitive", "event_specific"]},
                    "confidence": {"type": "string", "enum": ["高", "中", "低", "判断不能"]},
                    "source_comment_indices": {
                        "type": "array", "items": {"type": "integer"},
                        "description": "入力comments配列（0-based）のうち、この候補の根拠になった要素番号",
                    },
                },
            },
        },
    },
}


def _build_user_prompt(source_package: dict, comments: list, review_bound: dict | None = None) -> str:
    project = source_package["project"]
    design_history = source_package["design_history"]
    initial_design = design_history[0] if design_history else None
    final_design = design_history[-1] if design_history else None
    approval_event = source_package["approval_event"] or {}

    comment_blocks = []
    for i, entry in enumerate(comments):
        c = entry["comment"]
        comment_blocks.append({
            "index": i, "review_round": entry["cycle"].get("round_number"),
            "anchor_type": c.get("anchor_type"), "severity": c.get("severity"),
            "comment": c.get("comment"), "resolved_status": c.get("resolved_status"),
            "author_response": c.get("author_response"),
        })

    payload = {
        "report_title": project.get("project_title") or project.get("project_code"),
        "initial_design": {
            "investigation_question": (initial_design or {}).get("investigation_question"),
            "key_actors": (initial_design or {}).get("key_actors"),
            "hypotheses": (initial_design or {}).get("hypotheses"),
        } if initial_design else None,
        "final_design": {
            "investigation_question": (final_design or {}).get("investigation_question"),
            "key_actors": (final_design or {}).get("key_actors"),
            "hypotheses": (final_design or {}).get("hypotheses"),
        } if final_design else None,
        "eligible_expert_comments": comment_blocks,
        "approved_report_summary": (approval_event.get("version_snapshot") or {}).get("syntheses"),
    }
    # review_bound（review_cycle_id指定時のみ）: Before/Afterの主軸をReport Draft本文
    # （geo_ios_report_drafts.sections）へ差し替える。design_historyのinvestigation_
    # question/key_actors/hypothesesは補助情報としてそのまま残す（A章）。
    if review_bound:
        before_draft = review_bound.get("before_draft") or {}
        after_draft = review_bound.get("after_draft") or {}
        payload["before_report_sections"] = before_draft.get("sections")
        payload["after_report_sections"] = after_draft.get("sections")
        payload["after_resolution_method"] = review_bound.get("after_resolution_method")
        # Approved report summaryはProject全体の最終承認内容（後段のfallbackとしてのみ
        # 残す）。Round-boundなafter_report_sectionsが本来のAfterであることを明示する。
        payload["approved_report_summary_is_supplementary_only"] = True
    return (
        "以下はReport ReviewのBefore/After情報です。JSON以外は出力しないでください。\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


# ---- §14 Main Orchestrator ----------------------------------------------------

def extract_learning_candidates(client, azure_client, model: str, *, report_project_id: str,
                                 extraction_model: str = None, prompt_version: str = None,
                                 require_published: bool = True, actor_id: str = "geo_learning_extraction",
                                 review_cycle_id: str = None) -> dict:
    """Approved/Published Reportから Learning Candidateを抽出する（§16: 既定は
    Published Official Knowledge後を要求。§17: 同一Report×同一Review集合×同一
    extraction_model×prompt_versionでは再実行しても重複作成しない）。

    review_cycle_id（2026-09-24追加、Learning Workbench対応、A章）: 指定した場合、
    Before/AfterをそのReview Cycle単体にスコープし、実際にレビューされたReport Draft本文
    （resolve_review_bound_after、design_history[0]/[-1]ではない）から解決する。
    複数Roundがある場合の混在を防ぐ——省略時（従来どおりProject全体を対象とする既存の
    呼び出し方）の挙動は一切変更しない。"""
    prompt_version = prompt_version or DEFAULT_PROMPT_VERSION
    extraction_model = extraction_model or model

    source_package = build_learning_source_package(client, report_project_id)
    if require_published and not source_package["published_knowledge"]:
        raise LearningExtractionError(
            "このreport_project_idはまだOfficial Knowledgeとしてpublishされていません"
            "（§16: 原則Publish後に実行してください。手動で先行実行する場合は"
            "require_published=Falseを指定してください）")

    review_bound = None
    if review_cycle_id:
        all_cycles = [entry["cycle"] for entry in source_package["review_cycles"]]
        target_entry = next(
            (e for e in source_package["review_cycles"]
             if e["cycle"]["review_cycle_id"] == review_cycle_id), None)
        if not target_entry:
            raise LearningExtractionError(f"review_cycle_idが見つかりません: {review_cycle_id}")
        cycle_comments = [c for c in target_entry["comments"]
                           if c.get("resolved_status") in ELIGIBLE_RESOLUTION_STATUSES]
        comments = [{"cycle": target_entry["cycle"], "comment": c} for c in cycle_comments]
        review_bound = resolve_review_bound_after(
            review_cycle=target_entry["cycle"], all_cycles=all_cycles,
            cycle_comments=cycle_comments, approval_event=source_package["approval_event"],
            draft_history=source_package["draft_history"])
        if review_bound["after_resolution_method"] == "not_evaluable":
            # A章絶対条件: 解決不能なRoundからはcandidateを生成しない（注記して残さない）。
            return {"candidates": [], "idempotent_reuse": False, "deduplicated_count": 0,
                    "conflict_count": 0, "reason": "not_evaluable",
                    "after_resolution_method": "not_evaluable"}
        review_cycle_ids = [review_cycle_id]
    else:
        comments = eligible_comments(source_package)
        review_cycle_ids = [entry["cycle"]["review_cycle_id"] for entry in source_package["review_cycles"]]

    fingerprint = _compute_fingerprint(
        report_project_id=report_project_id, review_cycle_ids=review_cycle_ids,
        prompt_version=prompt_version, extraction_model=extraction_model)
    existing = _existing_candidates_linked_to_cycles(client, review_cycle_ids)
    reused = [c for c in existing if (c.get("trigger_context") or {}).get("extraction_fingerprint") == fingerprint]
    if reused:
        return {"candidates": reused, "idempotent_reuse": True, "deduplicated_count": 0, "conflict_count": 0}

    if not comments:
        return {"candidates": [], "idempotent_reuse": False, "deduplicated_count": 0, "conflict_count": 0,
                "reason": "no_eligible_review_comments"}

    system_prompt = EXTRACTION_SYSTEM_PROMPT
    user_prompt = _build_user_prompt(source_package, comments, review_bound=review_bound)
    result = geo_llm_common.call_llm_structured(
        azure_client, model, system_prompt, user_prompt, EXTRACTION_SCHEMA, "geo_ios_learning_extraction")
    proposed = result["data"].get("candidates", [])

    analysis_session = source_package["analysis_session"]
    selected_scenario_id = analysis_session.get("selected_scenario_id") if analysis_session else None
    scenario_candidates = source_package["scenario_candidates"]

    created, deduplicated_count, conflict_count = [], 0, 0
    for item in proposed:
        # §4/§1絶対条件: 受け入れられ反映されなかった指摘・Report固有事実は候補化しない。
        if not item.get("was_accepted_and_reflected") or not item.get("is_reusable_beyond_report"):
            continue

        dedup_result = _check_dedup_and_conflict(
            client, candidate_type=item["candidate_type"], generalized_rule=item["generalized_rule"])
        if dedup_result["dedup_status"] == "duplicate":
            deduplicated_count += 1
            continue
        if dedup_result["dedup_status"] == "possible_conflict_or_overlap":
            conflict_count += 1

        cqas_signal = _compute_cqas_signal(scenario_candidates, selected_scenario_id, item["generalized_rule"])
        source_indices = item.get("source_comment_indices") or []
        source_comment_ids = [comments[i]["comment"]["review_comment_id"] for i in source_indices
                               if 0 <= i < len(comments)]
        primary_comment = comments[source_indices[0]]["comment"] if source_indices else (
            comments[0]["comment"] if comments else None)
        primary_cycle = comments[source_indices[0]]["cycle"] if source_indices else (
            comments[0]["cycle"] if comments else None)

        trigger_context = {
            "title": item["title"], "trigger": item["trigger"],
            "initial_ai_weakness": item["initial_ai_weakness"], "final_outcome": item["final_outcome"],
            "exceptions": item.get("exceptions") or [], "temporal_stability": item["temporal_stability"],
            "source_report_project_id": report_project_id,
            "source_analysis_session_id": (analysis_session or {}).get("analysis_session_id"),
            "source_selected_scenario_id": selected_scenario_id,
            "source_comment_ids": source_comment_ids,
            "cqas_signal": cqas_signal,
            "dedup_status": dedup_result["dedup_status"], "related": dedup_result["related"],
            "extraction_fingerprint": fingerprint, "prompt_version": prompt_version,
            "after_resolution_method": (review_bound or {}).get("after_resolution_method"),
        }

        candidate_row = learning_service.create_candidate(
            client, candidate_type=item["candidate_type"], observation=item["expert_observation"],
            generalized_rule=item["generalized_rule"], extraction_model=extraction_model,
            trigger_context=trigger_context, failure_pattern_example=item.get("failure_pattern_example"),
            positive_example=item.get("positive_example"), scope=item["scope"], confidence=item["confidence"],
            source_report_id=None,
            source_review_cycle_id=(primary_cycle or {}).get("review_cycle_id"),
            source_review_comment_id=(primary_comment or {}).get("review_comment_id"),
            actor_id=actor_id,
        )
        # Learning Workbench 版履歴: revision_number=1は必ずこの関数自身（Service層）が
        # candidate insert直後に書き込む——API層経由以外（テスト・スクリプト等）でも
        # V1が必ず残るようにする（3回目Plan Modeレビュー「実装条件」）。
        learning_service.record_revision(
            client, candidate_row["candidate_id"], revised_by="ai",
            revision_snapshot={
                "generalized_rule": candidate_row.get("generalized_rule"),
                "observation": candidate_row.get("observation"),
                "scope": candidate_row.get("scope"),
                "confidence": candidate_row.get("confidence"),
                "candidate_type": candidate_row.get("candidate_type"),
            },
            change_summary="Initial extraction", created_by=actor_id,
        )
        created.append(candidate_row)

    usage = result.get("usage") or {}
    geo_audit.log_audit(
        client, actor=actor_id, action="learning_extraction_run",
        target_table="geo_ios_learning_candidates", target_id=None,
        detail={"report_project_id": report_project_id, "extraction_model": extraction_model,
                "prompt_version": prompt_version, "eligible_comment_count": len(comments),
                "proposed_count": len(proposed), "created_count": len(created),
                "deduplicated_count": deduplicated_count, "conflict_count": conflict_count,
                "input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens")})

    return {"candidates": created, "idempotent_reuse": False, "deduplicated_count": deduplicated_count,
            "conflict_count": conflict_count, "proposed_count": len(proposed)}
