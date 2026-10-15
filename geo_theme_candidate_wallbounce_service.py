"""Geopolitical Intelligence OS Phase 2G-4: Theme Candidate Wall-bounce Chat。

「どう読むか」を決めるExpert AI相談ではない。扱ってよいのは、このテーマは広すぎないか／
何を問うべきか／誰を見るべきか／どの地域に絞るか／既存社内分析との差は何か／追加で何を
確認すべきか、まで。Reading/Primary/Alternativeはここでは一切生成しない（Expert AI Core
とは完全に別物、Do Not Touch）。

Context（毎turn再構築、キャッシュしない）:
    External: Candidate自身のsignal_summary/observed_change/why_now/question/actors/
        regions/themes + Evidence Trace（geo_theme_candidate_service.get_candidate_evidence）
    Internal: INTELLIGENCE_AI_V1の既存assess_internal_coverage()をStage 1
        （metadata-only、LLM無し）で本requestのrequester_contextを使い都度Runtime取得する。
        geo_theme_candidatesへは一切永続化しない（Phase 2G-3と同じ原則）。

Multi-turn: Phase 2F-4のstateless pattern（毎request全履歴をクライアントが送る、Backend
側は会話セッションを保持しない）をそのまま踏襲する。各turnでExternal Evidence/Internal
Coverage/Permissionをゼロから再評価し、前turnのInternal source本文やPermission結果を
一切信用しない（historyは role/content のプレーンテキストのみ）。

Candidateの直接更新は一切行わない。AIはproposal（提案）を返すだけで、
geo_theme_candidate_service.apply_candidate_refinement()はFrontendがユーザーの「反映」
操作を受けて明示的に呼ぶ別経路。
"""
import geo_intelligence_ai_theme_coverage_service as theme_coverage_service
import geo_llm_common
import geo_theme_candidate_service as candidate_service

MAX_HISTORY_MESSAGES = 8
MAX_EVIDENCE_BULLETS = 5
MAX_SIMILAR_INTELLIGENCE = 5

_WALLBOUNCE_SCHEMA_NAME = "geo_theme_candidate_wallbounce_v1"
_WALLBOUNCE_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "has_proposal": {"type": "boolean"},
        "proposal": {
            "type": "object",
            "properties": {
                "proposed_title": {"type": ["string", "null"]},
                "proposed_question": {"type": ["string", "null"]},
                "proposed_actors": {"type": "array", "items": {"type": "string"}},
                "proposed_regions": {"type": "array", "items": {"type": "string"}},
                "proposed_scope": {"type": ["string", "null"]},
                "investigation_questions": {"type": "array", "items": {"type": "string"}},
                "rationale": {"type": ["string", "null"]},
            },
            "required": ["proposed_title", "proposed_question", "proposed_actors", "proposed_regions",
                         "proposed_scope", "investigation_questions", "rationale"],
            "additionalProperties": False,
        },
    },
    "required": ["answer", "has_proposal", "proposal"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = """あなたはTheme Candidate（社内で分析候補として立てられたテーマ）を、
人間と一緒に磨き上げる壁打ち相手です。

絶対条件:
- ここは「どう読むか」を決めるExpert AI相談ではありません。Reading/Primary Scenario/
  Alternative Scenarioは一切作らないこと。
- 扱ってよいのは次の範囲までです: このテーマは広すぎないか／何を問うべきか／誰を見るべきか
  ／どの地域に絞るか／既存社内分析との差は何か／追加で何を確認すべきか。
- 回答の中では、External Evidence（社内Candidateに紐づく外部Signal/Event由来）、
  Internal Intelligence（社内Report/Knowledgeのcoverage結果由来）、あなた自身の示唆
  （AI suggestion）を混同しないこと。Internal Intelligenceとして与えられていない内容を、
  社内分析の事実であるかのように語らないこと。
- 新しい事実（Event・Actor・日付等）を一般知識から補完・創作しないこと。
- 改善の余地がある場合のみhas_proposal=trueとし、proposalへ具体的な改善案
  （title/question/actors/regions/scope/investigation_questions/rationale）を書くこと。
  改善提案が無い場合はhas_proposal=falseとし、proposalの各fieldは変更なし
  （文字列はnull、配列は空）のままにすること。
- proposalはあくまで提案であり、あなたがCandidateを直接書き換えることはできません。
  人間が明示的に「反映」を選んだ場合のみ適用されます。"""


def _render_history(history: list) -> str:
    trimmed = history[-MAX_HISTORY_MESSAGES:]
    lines = []
    for m in trimmed:
        role = "User" if m.get("role") == "user" else "Assistant"
        content = (m.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _render_external_evidence(client, candidate: dict) -> str:
    lines = [
        f"title: {candidate.get('title')}",
        f"question: {candidate.get('question')}",
        f"signal_summary: {candidate.get('signal_summary')}",
        f"observed_change: {candidate.get('observed_change')}",
        f"why_now: {candidate.get('why_now')}",
        f"actors: {', '.join(candidate.get('actors') or [])}",
        f"regions: {', '.join(candidate.get('regions') or [])}",
        f"themes: {', '.join(candidate.get('themes') or [])}",
    ]
    evidence = candidate_service.get_candidate_evidence(client, candidate["candidate_id"]) or {}
    signals = (evidence.get("external_signals") or [])[:MAX_EVIDENCE_BULLETS]
    if signals:
        lines.append("evidence bullets:")
        for s in signals:
            bullet = f"- {s.get('title') or '(無題)'}"
            if s.get("source_domain"):
                bullet += f" ({s['source_domain']})"
            if s.get("published_at"):
                bullet += f" [{s['published_at'][:10]}]"
            lines.append(bullet)
    return "\n".join(lines)


def _render_internal_coverage(coverage: dict) -> str:
    c = coverage["coverage"]
    lines = [
        f"accessible_report_count: {c['accessible_report_count']}",
        f"accessible_knowledge_count: {c['accessible_knowledge_count']}",
        f"approved_report_count: {c['approved_report_count']}",
        f"in_review_report_count: {c['in_review_report_count']}",
        f"uploaded_only_report_count: {c['uploaded_only_report_count']}",
        f"latest_report_date: {coverage['recency']['latest_report_date']}",
        f"latest_approved_report_date: {coverage['recency']['latest_approved_report_date']}",
        f"key_actors_already_covered: {', '.join(coverage['key_actors_already_covered'])}",
        f"potential_coverage_gaps: {', '.join(coverage['potential_coverage_gaps'])}",
    ]
    similar = coverage["similar_internal_intelligence"][:MAX_SIMILAR_INTELLIGENCE]
    if similar:
        lines.append("similar internal intelligence:")
        for s in similar:
            lines.append(
                f"- [{s['source_ref']}] {s.get('title')} ({s.get('review_status')}, {s.get('date')})")
    return "\n".join(lines)


def chat(client, azure_client, model, candidate_id: str, *, history: list, message: str,
         requester_context: dict = None, is_internal_caller: bool = True,
         caller_domain: str = None) -> dict:
    """Phase 2G-4本体。requester_context/is_internal_caller/caller_domainは必ず本requestの
    呼び出し元（API層）がその場で組み立てた値を渡すこと（historyの中にidentityや前turnの
    Permission結果を混ぜて渡してはならない、§絶対条件）。"""
    candidate = candidate_service.get_candidate(client, candidate_id)
    if not candidate:
        raise candidate_service.ThemeCandidateError(f"指定されたTheme Candidateが見つかりません: {candidate_id}")

    # Internal CoverageはStage 1（metadata-only、LLM無し）のみ。毎turn本requestのidentityで
    # ゼロから再評価する——前turnの結果をキャッシュ・使い回ししない。
    theme_candidate_input = candidate_service.candidate_to_theme_candidate_input(candidate)
    coverage = theme_coverage_service.assess_internal_coverage(
        client, azure_client, model, theme_candidate_input, requester_context=requester_context,
        is_internal_caller=is_internal_caller, caller_domain=caller_domain, include_analysis=False)

    user_prompt = (
        "# Candidate（現在の内容）\n" + _render_external_evidence(client, candidate) + "\n\n"
        "# Internal Coverage（本requestのIdentityでRuntime評価済み）\n"
        + _render_internal_coverage(coverage) + "\n\n"
        "# 会話履歴\n" + (_render_history(history) or "(無し、これが最初のturn)") + "\n\n"
        "# 最新のユーザー発言\n" + message
    )

    try:
        result = geo_llm_common.call_llm_structured(
            azure_client, model, _SYSTEM_PROMPT, user_prompt, _WALLBOUNCE_SCHEMA,
            _WALLBOUNCE_SCHEMA_NAME, max_attempts=2)
        data = result["data"]
    except geo_llm_common.GeoLLMError:
        # §malformed LLM output fail-safe: Chat全体を止めず、安全な既定応答を返す。
        return {
            "answer": "現在AIからの応答を生成できませんでした。もう一度お試しください。",
            "has_proposal": False,
            "proposal": {
                "proposed_title": None, "proposed_question": None, "proposed_actors": [],
                "proposed_regions": [], "proposed_scope": None, "investigation_questions": [],
                "rationale": None,
            },
            "source_refs": [],
        }

    return {
        "answer": data["answer"],
        "has_proposal": bool(data["has_proposal"]),
        "proposal": data["proposal"],
        # §Security: source_refsはassess_internal_coverageが既に本requestのPermissionで
        # 絞り込んだsimilar_internal_intelligenceをそのまま転用するだけで、独自IDを作らない。
        "source_refs": coverage["similar_internal_intelligence"],
    }
