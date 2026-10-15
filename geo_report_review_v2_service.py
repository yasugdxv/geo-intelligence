"""Geopolitical Intelligence OS: Report Review V2 — Orchestration Service。

既存レポートが採用している地政学的な「読み方」をExpert AIが再検討する機能の中核。
以下の順で1回だけ実行する（べき等ではない——既にrun済みならその結果をそのまま返す）:

  1. Report Reading Extraction（geo_report_reading_extraction_service.extract_report_reading、
     Expert AIを呼ばない軽量LLM抽出）
  2. Expert AI独自Readingの生成（geo_ios_expert_ai_adapter.generate_scenario_candidates、
     既存Reading生成経路をそのまま再利用、新しいmode/schemaは追加しない）
  3. Report ReadingとExpert AI Readingの比較（geo_ios_expert_ai_adapter.
     run_report_reading_comparison_review、既存draft_reviewモードを転用）
  4. レポート本文自体のContent Review（Phase R1、geo_ios_expert_ai_adapter.
     run_report_content_review、Reading Reviewとは別レイヤーとして保持する）
  5. 比較結果(review_findings)を既存geo_ios_draft_ai_reviewsへ記録する（§Gate接続、
     Content Reviewの結果はGateには使わない——Reading Reviewのみが既存Gateの対象）

Phase R2（Human Expert Review）: save_human_expert_dispositionで、Reading Review/
Content Reviewの各findingに対する人間専門家のAgree/Modify/Reject判断を記録する。
AI Reviewの結果をそのまま正解扱いしない（ユーザー指示）。Learning Candidateへの
接続は将来のPhaseに先送りする（本serviceは判断の記録のみ）。

Phase R3（Revision→Approval→Stock）: 新しいRevision作成・Approval・Publicationロジックは
一切追加しない——既存geo_ios_report_draft_service.create_draft_version(source=
"human_edit")・geo_ios_review_api.py（Review Cycle）・geo_ios_approval_api.py・
geo_ios_knowledge_publication_service.pyをそのまま使う（既存のNative Draft編集UIが
そのまま使える、新しいeditorは作らない）。

Expert AI Core（geo_expert_engine.py）・CQAS・geo_expert_schema.pyは一切変更しない——
呼び出すのは既存geo_ios_expert_ai_adapter.py経由のみ。

§Gate接続（調査で発見した既存バグへの対応）: review-modeセッション（既存レポートを
アップロードしてレビューする）はDesign/Situationを持たないため、既存run_draft_ai_review
（_fetch_design_and_situationがDesign必須）は常に400で失敗し、check_expert_review_gate
（「Request Expert Review」実行時にgeo_ios_review_api.pyが要求する、AI Review完了＋
open Criticalゼロのgate）を永久に通過できなかった（Design不在が原因で再実行しても
同じ理由で必ず失敗するため）。Review V2はcomparison_findings（実際の内容に基づく
findings、draft_reviewモードと同じschema）を使ってgeo_ios_draft_ai_reviewsへ
create_ai_review_attempt→mark_ai_review_completedで書き込むことで、このGateを
意味のある内容で正しく通過可能にする——check_expert_review_gate自体やそのSchemaは
一切変更しない。

既存Reading Presentation v2（geo-ios-reading-presentation-v2.tsx）をそのまま使うため、
Report Reading・Expert AI Readingの両方を既存geo_ios_scenario_candidatesへmaterialize
する（旧geo_ios_review_import_service._materialize_reading_rowと同じ既存パターン、
新しいUI/Card実装は作らない）。旧geo_ios_review_import_service.py自体（Alternative
Reading生成・Revision Strategy等のフロー固有ロジック）はここに持ち込まない——Review V2
では使わない。

既存Review Mode（geo_ios_review_import_service.pyのreconstruction）の「Review Modeでは
Expert AI Engineを呼ばない」というno-Expert-AI invariantには触れない。本serviceは
Review V2専用の別経路として追加するものであり、既存Review Modeの呼び出し元からは
呼ばれない。
"""
from datetime import datetime, timezone

import geo_audit
import geo_ios_analysis_session_service as session_service
import geo_ios_expert_ai_adapter as expert_ai_adapter
import geo_ios_report_draft_service as draft_service
import geo_ios_situation_presentation_service as presentation_service
import geo_report_reading_extraction_service as extraction_service

READING_ROLE_REPORT_OWN = "REPORT_OWN_READING"
READING_ROLE_EXPERT_AI = "EXPERT_AI_INDEPENDENT_READING"

_RUNS_TABLE = "geo_report_review_v2_runs"


class ReportReviewV2Error(Exception):
    """Report Review V2の操作エラー。"""


# ---- 書き込み整合性チェック（間欠的な文字化けへの防御） ----------------------------
#
# 調査(2026-10-03)で、report_reading/expert_readingの書き込み後に、ごく稀に不正な
# lone surrogate文字（U+DC80-U+DCFF、不正なバイト列をerrors='surrogateescape'相当で
# 取り込んだ場合の既知シグネチャ）が混入する事象を1件確認した。geo_db_client.py・
# 本service双方のロジックには再現性のあるバグが見つからず（同一コード・同一データで
# 繰り返し検証しても正常に書き込めた）、既存コードが社内プロキシ経由の接続不安定さを
# 複数箇所で記録している経緯からも、間欠的なネットワーク起因の事象である可能性が高いと
# 判断した。原因そのものは特定できていないため、書き込み直後に実際の保存内容を読み直し、
# 壊れていれば同じ内容で1回だけ上書き再試行する防御策を追加する（既存の5xxリトライと
# 同じ「一時的な障害への耐性」という方針）。1回retryしても治らない場合はそのまま返す
# ——機能停止はさせない（表示専用データであり、Gate/Approval判断に使うfindingsは
# 別経路・別テーブル(geo_ios_draft_ai_reviews)へ既に書き込み済みのため実害が無い）。

_LONE_SURROGATE_LOW = 0xDC80
_LONE_SURROGATE_HIGH = 0xDCFF


def _contains_lone_surrogate(obj) -> bool:
    if isinstance(obj, str):
        return any(_LONE_SURROGATE_LOW <= ord(ch) <= _LONE_SURROGATE_HIGH for ch in obj)
    if isinstance(obj, dict):
        return any(_contains_lone_surrogate(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(_contains_lone_surrogate(v) for v in obj)
    return False


def _verify_and_retry_write(client, table: str, pk_field: str, pk_value: str, written_fields: dict,
                             *, actor_id: str) -> dict:
    refetched = client.select(table, {"select": "*", pk_field: f"eq.{pk_value}"})[0]
    if not _contains_lone_surrogate(refetched):
        return refetched
    geo_audit.log_audit(
        client, actor=actor_id, action="report_review_v2_corruption_retry",
        target_table=table, target_id=pk_value, detail={"stage": "before_retry"})
    client.update(table, {pk_field: f"eq.{pk_value}"}, written_fields)
    refetched = client.select(table, {"select": "*", pk_field: f"eq.{pk_value}"})[0]
    if _contains_lone_surrogate(refetched):
        geo_audit.log_audit(
            client, actor=actor_id, action="report_review_v2_corruption_unresolved",
            target_table=table, target_id=pk_value, detail={"stage": "after_retry"})
    return refetched


# ---- Reading Card materialization（既存geo_ios_scenario_candidatesへ相乗り） --------

def _materialize_reading_row(client, analysis_session_id: str, *, title: str, content: dict,
                              reading_role: str, rank: int, actor_id: str) -> dict:
    row = {
        "analysis_session_id": analysis_session_id, "rank": rank, "title": title,
        "content": {**content, "reading_role": reading_role, "exposure_source": "report_review_v2"},
        "displayed": True, "selected": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": None, "prompt_version": None, "cqas_knowledge_version": None,
    }
    created = client.insert("geo_ios_scenario_candidates", [row])[0]
    created = _verify_and_retry_write(
        client, "geo_ios_scenario_candidates", "scenario_candidate_id", created["scenario_candidate_id"],
        {"title": row["title"], "content": row["content"]}, actor_id=actor_id)
    geo_audit.log_audit(
        client, actor=actor_id, action="report_review_v2_reading_materialize",
        target_table="geo_ios_analysis_sessions", target_id=analysis_session_id,
        detail={"scenario_candidate_id": created["scenario_candidate_id"], "reading_role": reading_role})
    return created


def _report_reading_content(report_reading: dict) -> dict:
    return {
        "origin": "report_review_v2_report_reading", "raw": report_reading,
        "why_this_matters": report_reading.get("report_primary_reading"),
        "key_actors": report_reading.get("key_actors") or [],
        "supporting_basis": report_reading.get("supporting_evidence") or [],
        "weakening_basis": report_reading.get("weakening_evidence") or [],
        "missing_evidence": "; ".join(report_reading.get("missing_evidence") or []) or None,
    }


def _report_field(report_reading: dict, field: str) -> str | list | None:
    """report_readingのfieldを返す。not_explicit_fieldsに含まれる（本文に明示されて
    いない）場合はNoneとして扱う——値が技術的に埋まっていても、比較表では
    「明示されていない」として表示させるため（ユーザー指示）。"""
    if field in (report_reading.get("not_explicit_fields") or []):
        return None
    return report_reading.get(field)


def _join_or_none(value) -> str | None:
    if not value:
        return None
    if isinstance(value, list):
        return "; ".join(str(v) for v in value) or None
    return str(value)


def _comparison_row(*, report_value, expert_value) -> dict:
    return {"report": _join_or_none(report_value), "expert_ai": _join_or_none(expert_value)}


def _expert_evidence_rationales(main_raw: dict) -> list[str]:
    """main_scenario.supporting_evidenceはevidence_id（UUID）の配列であり、比較表に
    そのまま出すと人間可読でない。evidence_relevance[].rationale（同じevidence_idに
    紐づく根拠説明文、既存Expert AI出力そのまま）があればそちらを使う——新しい文章は
    作らない。無ければID配列のまま返す（無いよりはまし、推測で埋めない）。"""
    evidence_relevance = main_raw.get("evidence_relevance") or []
    rationales = [r.get("rationale") for r in evidence_relevance if r.get("rationale")]
    return rationales or (main_raw.get("supporting_evidence") or [])


def _build_comparison_summary(report_reading: dict, expert_reading_result: dict) -> dict:
    """比較表上段の要約行（着眼点/Key Actor/Primary Reading/根拠/弱化条件）を、
    report_reading/expert_reading_resultから機械的に導出する（新しいLLM呼び出し無し、
    report側をExpert AI構造へ寄せるための拡張もしない——ユーザー指示）。"""
    candidates = expert_reading_result.get("candidates") or []
    main = next((c for c in candidates if c.get("content", {}).get("origin") == "main_scenario"), None)
    main_content = (main or {}).get("content") or {}
    main_raw = main_content.get("raw") or {}

    alternative_switch_conditions = []
    for c in candidates:
        if c.get("content", {}).get("origin") != "main_scenario":
            alternative_switch_conditions.extend((c["content"].get("raw") or {}).get("switch_conditions") or [])

    return {
        "cue": _comparison_row(
            report_value=_report_field(report_reading, "cue"),
            expert_value=main_content.get("analysis_question")),
        "key_actors": _comparison_row(
            report_value=_report_field(report_reading, "key_actors"),
            expert_value=main_content.get("key_actors")),
        "primary_reading": _comparison_row(
            report_value=_report_field(report_reading, "report_primary_reading"),
            expert_value=main_raw.get("description")),
        "supporting_evidence": _comparison_row(
            report_value=_report_field(report_reading, "supporting_evidence"),
            expert_value=_expert_evidence_rationales(main_raw)),
        "weakening_or_switch_condition": _comparison_row(
            report_value=(_report_field(report_reading, "weakening_evidence")
                          or _report_field(report_reading, "switch_condition")),
            expert_value=alternative_switch_conditions),
    }


def _normalize_findings_with_ids(findings: list) -> list[dict]:
    """findingsにfinding_id（連番、Human Expert Reviewが参照するためだけの決定的ID）を
    付与する。既存mark_ai_review_completedと同じ考え方（既存Expert AI Schema自体は
    変更しない、永続化層でのみIDを付与する）。既にfinding_idがあれば変更しない。"""
    return [{**f, "finding_id": f.get("finding_id") or f"f{i + 1}"} for i, f in enumerate(findings or [])]


def _build_theme_text(report_reading: dict) -> str:
    """Expert AI独自Reading生成（generate_scenario_candidates）へ渡すtheme文字列。
    report_readingに明示されている内容のみを使う（新しい前提を作らない）。"""
    parts = [
        report_reading.get("current_situation"),
        f"問い: {report_reading['question']}" if report_reading.get("question") else None,
        f"前提: {report_reading['report_context']}" if report_reading.get("report_context") else None,
    ]
    return "\n".join(p for p in parts if p)


# ---- Persistence ---------------------------------------------------------------

def get_run(client, analysis_session_id: str) -> dict | None:
    rows = client.select(_RUNS_TABLE, {
        "select": "*", "analysis_session_id": f"eq.{analysis_session_id}", "limit": "1"})
    return rows[0] if rows else None


def get_expert_reading_presentation(client, analysis_session_id: str) -> dict | None:
    """Expert AI Reading側を既存Reading Presentation v2（geo-ios-reading-presentation-v2.
    tsx）でそのまま表示するためのGeoIosSituationPresentationを構築する。Report Reading
    側はExtraction結果しか持たず構造が異なるため対象外（Frontend側の軽量専用カードで
    表示する、ユーザー指示）——designは review-modeセッションに存在しないため{}を渡す
    （build_key_point/build_research_needsはdesign未設定時も動作する、§Gate接続と同じ
    _fetch_design_and_situationパターン）。runが無い/未完了ならNoneを返す。"""
    run = get_run(client, analysis_session_id)
    if not run or run.get("status") != "completed":
        return None
    response_id = (run.get("expert_reading") or {}).get("response_id")
    if not response_id:
        return None
    rows = client.select("geo_expert_responses", {
        "select": "response_json", "response_id": f"eq.{response_id}", "limit": "1"})
    if not rows:
        return None
    response_json = rows[0]["response_json"]

    all_candidates = session_service.list_scenario_candidates(client, analysis_session_id)
    expert_candidates = [
        c for c in all_candidates
        if (c.get("content") or {}).get("reading_role") == READING_ROLE_EXPERT_AI
    ]
    return presentation_service.strip_debug_sources(
        presentation_service.build_situation_presentation(
            design={}, candidates=expert_candidates, response_json=response_json))


def start_run(client, analysis_session_id: str, *, report_draft_id: str | None,
              created_by: str) -> dict:
    """pending行を作成する（既に行があればstatusを問わずそのまま返す——completed済みの
    結果を上書きしない、既存create_ai_review_attemptと同じ「呼び出し側がすぐ使える行を
    即時に返す」パターン）。実際のExpert AI呼び出しはexecute_runが行う。

    Phase R3（Revision→再Gate確認）対応: 既存runがcompleted/failed済みでも、
    report_draft_idが前回と異なる（§手直し保存でRevisionが作られた）場合はpending
    へリセットして再実行可能にする——既存の結果（findings/dispositions）は新しい
    Draftに対応しなくなるため破棄する（finding_idは実行のたびに採番し直すため、
    古いdispositionを残すと対応関係が壊れる）。同じreport_draft_idでの再呼び出しは
    従来どおり冪等（結果を上書きしない）。"""
    existing = get_run(client, analysis_session_id)
    if existing:
        if report_draft_id is None or existing.get("report_draft_id") == report_draft_id:
            return existing
        client.update(_RUNS_TABLE, {"analysis_session_id": f"eq.{analysis_session_id}"}, {
            "report_draft_id": report_draft_id, "status": "pending", "error_message": None,
            "report_reading": None, "report_reading_scenario_candidate_id": None,
            "expert_reading": None, "comparison_findings": None, "comparison_summary": None,
            "content_findings": None, "human_expert_dispositions": {},
        })
        geo_audit.log_audit(
            client, actor=created_by, action="report_review_v2_run_reset_for_revision",
            target_table=_RUNS_TABLE, target_id=existing["run_id"],
            detail={"analysis_session_id": analysis_session_id,
                    "previous_report_draft_id": existing.get("report_draft_id"),
                    "new_report_draft_id": report_draft_id})
        return get_run(client, analysis_session_id)
    row = {
        "analysis_session_id": analysis_session_id, "report_draft_id": report_draft_id,
        "status": "pending", "created_by": created_by,
    }
    created = client.insert(_RUNS_TABLE, [row])[0]
    geo_audit.log_audit(
        client, actor=created_by, action="report_review_v2_run_create",
        target_table=_RUNS_TABLE, target_id=created["run_id"],
        detail={"analysis_session_id": analysis_session_id})
    return created


def reset_failed_run_for_retry(client, analysis_session_id: str) -> dict | None:
    """前回failedのrunを、同じDraftで再実行するためにpendingへ戻し、古いerror_messageを消す。
    再実行が終わるまで画面に前回の失敗が出続けないようにするため（実行を始めた時点で
    「実行中」と分かる状態にする）。failed以外（pending/completed）には何もしない。"""
    existing = get_run(client, analysis_session_id)
    if not existing or existing.get("status") != "failed":
        return existing
    client.update(_RUNS_TABLE, {"analysis_session_id": f"eq.{analysis_session_id}"},
                  {"status": "pending", "error_message": None})
    return get_run(client, analysis_session_id)


def _update_run(client, analysis_session_id: str, patch: dict, *, actor_id: str | None = None) -> dict:
    client.update(_RUNS_TABLE, {"analysis_session_id": f"eq.{analysis_session_id}"}, patch)
    current = get_run(client, analysis_session_id)
    if actor_id is not None:
        current = _verify_and_retry_write(
            client, _RUNS_TABLE, "run_id", current["run_id"], patch, actor_id=actor_id)
    return current


# ---- Phase R2: Human Expert Review（Agree/Modify/Reject） -------------------------

HUMAN_EXPERT_DISPOSITIONS = ("agree", "modify", "reject")
FINDING_SOURCES = ("reading", "content")


class HumanExpertReviewError(Exception):
    """Human Expert Review操作のエラー。"""


def save_human_expert_disposition(client, analysis_session_id: str, *, source: str, finding_id: str,
                                   disposition: str, reason: str, modification: str | None,
                                   reviewed_by: str) -> dict:
    """Reading Review（source="reading"）/Content Review（source="content"）の各findingに
    対する人間専門家の判断を記録する。AI Reviewの結果をそのまま正解扱いしない
    （ユーザー指示）——Agreeであっても、必ず人間がここで明示的に確認した記録を残す。
    同一findingへの再判断は上書きする（最新の判断のみを保持、履歴は持たない——
    既存Waiveと同じ「最終状態のみ」という既存パターンに合わせる）。"""
    if source not in FINDING_SOURCES:
        raise HumanExpertReviewError(f"未対応のsourceです: {source}（許可: {FINDING_SOURCES}）")
    if disposition not in HUMAN_EXPERT_DISPOSITIONS:
        raise HumanExpertReviewError(
            f"未対応のdispositionです: {disposition}（許可: {HUMAN_EXPERT_DISPOSITIONS}）")
    if disposition != "agree" and not (reason or "").strip():
        raise HumanExpertReviewError("modify/rejectの場合、reasonは必須です")

    run = get_run(client, analysis_session_id)
    if not run:
        raise HumanExpertReviewError(f"analysis_session_id={analysis_session_id}のrunが見つかりません")

    dispositions = dict(run.get("human_expert_dispositions") or {})
    key = f"{source}:{finding_id}"
    dispositions[key] = {
        "source": source, "finding_id": finding_id, "disposition": disposition,
        "reason": reason, "modification": modification, "reviewed_by": reviewed_by,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }
    client.update(_RUNS_TABLE, {"analysis_session_id": f"eq.{analysis_session_id}"},
                  {"human_expert_dispositions": dispositions})
    geo_audit.log_audit(
        client, actor=reviewed_by, action="report_review_v2_human_expert_disposition",
        target_table=_RUNS_TABLE, target_id=run["run_id"],
        detail={"source": source, "finding_id": finding_id, "disposition": disposition})
    return get_run(client, analysis_session_id)


# ---- Orchestration ---------------------------------------------------------------

def _clear_previous_materialized_readings(client, analysis_session_id: str, *, actor_id: str) -> None:
    """Phase R3（Revision後の再実行）対応: geo_ios_scenario_candidatesは
    (analysis_session_id, rank)にunique制約があるため、同一セッションへ2回目の
    materializeを行うと必ず409 Conflictになる。再実行時は、前回run（exposure_source
    =="report_review_v2"でmaterializeした行のみ、他の生成経路の行には触れない）の
    Reading Cardを削除してから新しく作り直す——Reading Presentation v2は常に最新の
    Review V2結果のみを表示する前提のため、古いRevision前のCardを残す理由が無い。"""
    existing_candidates = session_service.list_scenario_candidates(client, analysis_session_id)
    stale_ids = [
        c["scenario_candidate_id"] for c in existing_candidates
        if (c.get("content") or {}).get("exposure_source") == "report_review_v2"
    ]
    if not stale_ids:
        return
    client.delete("geo_ios_scenario_candidates",
                  {"scenario_candidate_id": f"in.({','.join(stale_ids)})"})
    geo_audit.log_audit(
        client, actor=actor_id, action="report_review_v2_clear_previous_readings",
        target_table="geo_ios_scenario_candidates", target_id=analysis_session_id,
        detail={"cleared_count": len(stale_ids)})


def execute_run(client, azure_client, model, *, analysis_session_id: str,
                 report_text: str, actor_id: str) -> dict:
    """Step1-3を実行する。start_runで作成済みのpending行があることを前提とする
    （既にcompletedならそのまま返す、同一analysis_session_idに対して複数回呼ばれる
    運用は想定しない）。"""
    existing = get_run(client, analysis_session_id)
    if not existing:
        raise ReportReviewV2Error(
            f"analysis_session_id={analysis_session_id}のrunがまだ作成されていません"
            "（先にstart_runを呼んでください）")
    if existing.get("status") == "completed":
        return existing

    try:
        _clear_previous_materialized_readings(client, analysis_session_id, actor_id=actor_id)

        report_reading = extraction_service.extract_report_reading(
            azure_client, model, raw_text=report_text)

        report_row = _materialize_reading_row(
            client, analysis_session_id,
            title=report_reading.get("analysis_theme") or "レポートの読み",
            content=_report_reading_content(report_reading),
            reading_role=READING_ROLE_REPORT_OWN, rank=1, actor_id=actor_id)

        expert_reading_result = expert_ai_adapter.generate_scenario_candidates(
            client, azure_client, model, theme=_build_theme_text(report_reading),
            actor_id=actor_id, include_reading_portfolio=False)

        next_rank = 2
        for candidate in expert_reading_result["candidates"]:
            _materialize_reading_row(
                client, analysis_session_id,
                title=candidate.get("title") or "Expert AIの読み",
                content=candidate["content"], reading_role=READING_ROLE_EXPERT_AI,
                rank=next_rank, actor_id=actor_id)
            next_rank += 1

        comparison = expert_ai_adapter.run_report_reading_comparison_review(
            client, azure_client, model, report_reading=report_reading,
            expert_reading=expert_reading_result, actor_id=actor_id)
        comparison_findings = _normalize_findings_with_ids(comparison["findings"])

        content_review = expert_ai_adapter.run_report_content_review(
            client, azure_client, model, report_text=report_text, report_reading=report_reading,
            expert_reading=expert_reading_result, actor_id=actor_id)
        content_findings = _normalize_findings_with_ids(content_review["findings"])

        if existing.get("report_draft_id"):
            review_attempt = draft_service.create_ai_review_attempt(
                client, existing["report_draft_id"], requested_by=actor_id)
            draft_service.mark_ai_review_completed(
                client, review_attempt["draft_ai_review_id"], findings=comparison_findings,
                expert_request_id=comparison["request_id"], expert_response_id=comparison["response_id"])

        return _update_run(client, analysis_session_id, {
            "report_reading": report_reading,
            "report_reading_scenario_candidate_id": report_row["scenario_candidate_id"],
            "expert_reading": expert_reading_result,
            "comparison_findings": comparison_findings,
            "comparison_summary": _build_comparison_summary(report_reading, expert_reading_result),
            "content_findings": content_findings,
            "status": "completed",
        }, actor_id=actor_id)
    except Exception as e:  # noqa: BLE001 — 失敗を記録し、フロントがポーリングで検知できるようにする
        _update_run(client, analysis_session_id,
                    {"status": "failed", "error_message": f"{type(e).__name__}: {e}"})
        raise
