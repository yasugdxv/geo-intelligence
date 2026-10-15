"""Geopolitical Intelligence OS Phase 2G-5: Theme Discovery → Expert AI Consultation Handoff。

Theme Candidate（Phase 2G-1）のtitle/question/actors/regions等を、既存Expert AI Intake
（geo_ios_analysis_session_service.create_session / geo_ios_intake_service.create_context_version、
Phase B/2026-09-24で実装済み・本Phaseでは一切変更しない）の実在fieldへ最小限mappingし、
Analysis Session + 初期Analysis Context versionを作る薄いBridgeのみを提供する。

絶対条件（§14 Expert AI Independence）: ここで渡すのは「分析すべき問いと、その背景」
（Initial Analytical Context）であり、Expert AIのReading/Primary Hypothesisとして
扱われることはない——Intake Context（geo_ios_analysis_contexts）は既存Intake Chat
フローが毎ターン新versionを追記していく土台であり、ここで作るのはその最初のversionに
過ぎない。Expert AI Core（geo_expert_engine.py）・CQAS・Reading生成には一切触れない。

Handoff Mapping（§3、既存CONTEXT_FIELDSの実在field名のみを使う。新しいfieldは増やさない）:
    title              → Session.title
    question           → core_question
    why_now            → analysis_objective（なぜ今これを検討すべきかという分析動機）
    observed_change + signal_summary
                       → current_situation（現在観測されている状況の要約）
    business_relevance → business_context（タグを日本語の短い説明文として結合）
    regions            → geographic_scope（リストを結合、geographic_scopeはtext列のため）
    actors             → relevant_actors（jsonb配列、そのままコピー）
    themes             → must_consider_topics（jsonb配列、そのままコピー）
    Internal Coverage  → internal_context（§5: 「社内では既に扱われている」というReference
                         Contextとして。Primary Readingの根拠としては固定しない）
    External Evidence  → reference_materials（§4: 本文を複製せず、source_ref/title/date/
                         summaryのみをtrace可能な形で渡す）
    investigation_questions（Wall-bounce由来、明示的に引き継ぐ場合のみ）
                       → information_gaps（§7: 既存schemaに新columnを足さない。「まだ
                         埋まっていない情報」という意味で最も近い実在field）

time_horizon/user_assumptions/known_events/constraints/key_actors_detailはCandidateに
対応する情報が無いため触れない（Expert AI Intake Chatで人間が後から埋める）。

Concurrent Idempotency（2026-10-01修正）: 同一candidate_idへの同時Handoff request
（Double-click等）でもSessionが1件しか作られないことを、Postgres側のtransaction-scoped
advisory lock（geo_handoff_create_session_if_absent RPC、sql/2026-10-01_...sql、
session_service.find_or_create_session_for_theme_candidate経由で呼ぶ）で保証する。
恒久的なunique制約にはしていない——将来「同一Candidateから複数の相談を開始する」機能の
余地を残す（ユーザー判断）。
"""
import geo_audit
import geo_ios_analysis_session_service as session_service
import geo_ios_intake_service as intake_service
import geo_intelligence_ai_theme_coverage_service as theme_coverage_service
import geo_theme_candidate_service as candidate_service

_MAX_REFERENCE_MATERIALS = 10


class ThemeCandidateHandoffError(Exception):
    """Theme Candidate → Expert AI HandoffのBridge操作エラー。"""


def _find_existing_session(client, candidate_id: str) -> dict | None:
    """§8 Handoff Preview専用（読み取りのみ、作成しない）。source_theme_candidate_id経由で
    既存Sessionを探す。statusの値に関わらずこのFK参照だけで判定する（statusとSessionの
    有無がずれる余地を作らない）。本当のcheck-and-create（race防止）は
    session_service.find_or_create_session_for_theme_candidateが担う——ここは参照のみ。"""
    rows = client.select("geo_ios_analysis_sessions", {
        "select": "*", "source_theme_candidate_id": f"eq.{candidate_id}",
        "order": "created_at.asc", "limit": "1"})
    return rows[0] if rows else None


def _join(values: list, sep: str = "、") -> str:
    return sep.join(v for v in (values or []) if v)


def _build_reference_materials(client, candidate: dict) -> list:
    """§4 External Evidence: Evidence本文を複製せず、trace可能なID/title/date/summaryのみ。"""
    evidence = candidate_service.get_candidate_evidence(client, candidate["candidate_id"]) or {}
    materials = []
    for signal in evidence.get("external_signals") or []:
        materials.append({
            "source_type": "external_signal", "source_id": signal.get("external_signal_id"),
            "title": signal.get("title"), "date": signal.get("published_at"),
            "summary": signal.get("source_domain"),
        })
    for event in evidence.get("external_events") or []:
        materials.append({
            "source_type": "external_event", "source_id": event.get("external_event_id"),
            "title": f"{event.get('actor1_name')} - {event.get('actor2_name')}".strip(" -"),
            "date": event.get("event_date"), "summary": event.get("action_geo_full_name"),
        })
    return materials[:_MAX_REFERENCE_MATERIALS]


def _build_internal_context(client, azure_client, model, candidate: dict, *,
                             requester_context: dict = None, is_internal_caller: bool = True,
                             caller_domain: str = None) -> str:
    """§5/§18: Current requesterで都度Runtime取得する（cacheしない、permission再評価必須）。
    Stage 1（metadata-only、LLM無し）のみ——Handoffのためだけに追加のLLM analyzeは不要。"""
    theme_candidate_input = candidate_service.candidate_to_theme_candidate_input(candidate)
    coverage = theme_coverage_service.assess_internal_coverage(
        client, azure_client, model, theme_candidate_input, requester_context=requester_context,
        is_internal_caller=is_internal_caller, caller_domain=caller_domain, include_analysis=False)
    if not coverage.get("similar_internal_intelligence") and not coverage.get("key_actors_already_covered"):
        return "社内に関連する既存分析は確認されていません（現時点のRuntime判定）。"
    lines = ["社内の既存分析Coverage（Runtime判定、Expert AIのReading根拠として固定しない参考情報）:"]
    for item in coverage.get("similar_internal_intelligence") or []:
        lines.append(f"- {item.get('title')}（{item.get('date')}, {item.get('review_status')}）")
    if coverage.get("key_actors_already_covered"):
        lines.append(f"既にcoverageのあるActor: {_join(coverage['key_actors_already_covered'])}")
    if coverage.get("potential_coverage_gaps"):
        lines.append(f"Coverage gap: {_join(coverage['potential_coverage_gaps'])}")
    return "\n".join(lines)


def build_handoff_preview(client, azure_client, model, candidate_id: str, *,
                           requester_context: dict = None, is_internal_caller: bool = True,
                           caller_domain: str = None, investigation_questions: list = None) -> dict:
    """§8 Handoff Preview: 何も作成しない、確認専用の読み取り。既存Sessionがあれば
    それを示す（Previewの時点でDouble-click/再Handoffかどうかを人間が判断できるように）。"""
    candidate = candidate_service.get_candidate(client, candidate_id)
    if not candidate:
        raise ThemeCandidateHandoffError(f"指定されたTheme Candidateが見つかりません: {candidate_id}")
    existing_session = _find_existing_session(client, candidate_id)
    return {
        "candidate_id": candidate_id,
        "session_title": candidate["title"],
        "core_question": candidate.get("question"),
        "actors": candidate.get("actors") or [],
        "regions": candidate.get("regions") or [],
        "why_now": candidate.get("why_now"),
        "carried_investigation_questions": list(investigation_questions or []),
        "existing_session_id": existing_session["analysis_session_id"] if existing_session else None,
    }


def handoff_theme_candidate_to_expert_ai(
        client, azure_client, model, candidate_id: str, *, actor_id: str, user_id: str,
        requester_context: dict = None, is_internal_caller: bool = True, caller_domain: str = None,
        investigation_questions: list = None) -> dict:
    """§9 Handoff Bridge本体。成功時のみCandidate status→HANDED_OFF（§10/§12）。
    §11 Idempotency（2026-10-01修正、concurrent idempotency補強）: Session自体の
    check-and-createは、Postgres側のadvisory lockで直列化されたatomic RPC
    （session_service.find_or_create_session_for_theme_candidate）に委ねる——
    ここでのPython側の事前チェックは行わない（逐次チェックだけではDouble-clickの
    同時requestに対してraceが残るため、§Concurrent Idempotency修正で廃止）。"""
    candidate = candidate_service.get_candidate(client, candidate_id)
    if not candidate:
        raise ThemeCandidateHandoffError(f"指定されたTheme Candidateが見つかりません: {candidate_id}")

    session, created = session_service.find_or_create_session_for_theme_candidate(
        client, candidate_id, title=candidate["title"], user_id=user_id, actor_id=actor_id)
    if not created:
        return {"session": session, "created": False}

    internal_context = _build_internal_context(
        client, azure_client, model, candidate, requester_context=requester_context,
        is_internal_caller=is_internal_caller, caller_domain=caller_domain)
    reference_materials = _build_reference_materials(client, candidate)

    current_situation_parts = [p for p in (candidate.get("observed_change"), candidate.get("signal_summary")) if p]
    fields = {
        "analysis_objective": candidate.get("why_now"),
        "core_question": candidate.get("question"),
        "current_situation": "\n\n".join(current_situation_parts) or None,
        "business_context": _join(candidate.get("business_relevance")) or None,
        "geographic_scope": _join(candidate.get("regions")) or None,
        "relevant_actors": candidate.get("actors") or [],
        "must_consider_topics": candidate.get("themes") or [],
        "internal_context": internal_context,
        "reference_materials": reference_materials,
    }
    if investigation_questions:
        fields["information_gaps"] = list(investigation_questions)
    fields = {k: v for k, v in fields.items() if v is not None}

    try:
        intake_service.create_context_version(
            client, session["analysis_session_id"], fields=fields, ready_for_handoff=False,
            change_note="Theme Discoveryからのhandoff初期Context", created_by=actor_id)
    except Exception as e:
        # §10 Atomicity: Intake Context作成に失敗した場合、作成済みSessionを残さない
        # （CandidateもNEWのままにする——このexcept節の外でstatus更新は一切行わない）。
        client.delete("geo_ios_analysis_sessions",
                       {"analysis_session_id": f"eq.{session['analysis_session_id']}"})
        raise ThemeCandidateHandoffError(
            f"Expert AI Intake Contextの作成に失敗したためHandoffを中止しました: {e}") from e

    candidate_service.update_candidate_status(client, candidate_id, "HANDED_OFF", actor_id=actor_id)
    geo_audit.log_audit(
        client, actor=actor_id, action="theme_candidate_handoff_to_expert_ai",
        target_table="geo_theme_candidates", target_id=candidate_id,
        detail={"analysis_session_id": session["analysis_session_id"],
                "carried_investigation_questions": bool(investigation_questions)})
    return {"session": session, "created": True}
