"""地政学ナレッジ＆インテリジェンスDB Phase2B-2: Internal Expert Learning Layer。

社内専門家の実際の業務（AI Draft → Expert Review → Final Report）における差分から、
「何を重視し、どう比較し、どう仮説を修正したか」というReasoningを抽出し、再利用可能な
Internal Expert Knowledge（geo_knowledge_items、information_class=internal_expert_assessment）
として蓄積する。「専門家が言ったから真実」とは扱わない。

Promote/Reinforceは「この修正から、このReasoning Ruleを社内専門家AIの再利用Knowledgeとして
残す価値があるか」という判断であり、内容の正誤判定（Human Approval）ではない。既存の
geo_knowledge_service.create_knowledge_item/review_knowledge_itemをそのまま呼ぶだけで、
新しい状態遷移ロジックは作らない。

Delta Extractionは既存geo_candidate_extraction_service.pyと同じ構成（LLM呼び出し＋jsonschema
検証＋型ゆれ正規化）を踏襲する。
"""
import json
from datetime import datetime, timezone

import geo_audit
import geo_claim_service
import geo_connector_base as base
import geo_knowledge_item_schemas as schemas
import geo_knowledge_service
import geo_llm_common

INTERNAL_REVIEW_SOURCE_NAME = "Internal Expert Review"

CHANGE_TYPES = (
    "judgment_change", "main_hypothesis_change", "alternative_hypothesis_added",
    "stakeholder_added", "stakeholder_removed", "evidence_weight_changed",
    "mechanism_added", "precedent_added", "comparison_rule", "reasoning_rule",
    "scenario_change", "trigger_added", "causal_chain_change", "uncertainty_adjustment",
)
REUSABILITY_CLASSES = ("case_specific_assessment", "reusable_reasoning")
NOTE_ROLES = ("explicit_reason", "supporting_comment", "context")
_CONFIDENCE_ENUM = ["高", "中", "低", "判断不能"]
_CONFIDENCE_WORD_MAP = {
    "high": "高", "medium": "中", "moderate": "中", "mid": "中", "low": "低",
    "unknown": "判断不能", "undetermined": "判断不能", "n/a": "判断不能", "na": "判断不能",
}

# reusability_class='reusable_reasoning'時のchange_type→knowledge_typeマッピング（決定的、LLMに選ばせない）
_CHANGE_TYPE_TO_KNOWLEDGE_TYPE = {
    "mechanism_added": "mechanism",
    "precedent_added": "precedent",
}
_DEFAULT_REUSABLE_KNOWLEDGE_TYPE = "reasoning_rule"

_CHANGE_TYPE_TO_RULE_CATEGORY = {
    "comparison_rule": "comparison",
    "evidence_weight_changed": "evidence_weighting",
    "stakeholder_added": "stakeholder_selection",
    "stakeholder_removed": "stakeholder_selection",
    "main_hypothesis_change": "hypothesis_revision",
    "alternative_hypothesis_added": "hypothesis_revision",
    "uncertainty_adjustment": "hypothesis_revision",
    "scenario_change": "scenario_reasoning",
    "trigger_added": "scenario_reasoning",
}


class ExpertReviewChangeNotPendingError(ValueError):
    """disposition='pending'ではないchangeへpromote/reinforce/keep/reject操作を行おうとした場合
    （二重操作の防止、冪等性）。"""
    pass


def _ensure_internal_review_source(client, theme_id: str) -> dict:
    """Draft/Final文書の保存先source_id用に、内部レビュー専用の固定Source行を用意する
    （trust_tier=E: 内部分析、docs/geopolitical-source-registry.md参照）。既存のConnectorの
    _ensure_source()と同じ「存在すれば再利用、無ければ作成」パターン。"""
    existing = client.select("geo_source_registry", {
        "select": "*", "name": f"eq.{INTERNAL_REVIEW_SOURCE_NAME}", "limit": "1",
    })
    if existing:
        return existing[0]
    return client.insert("geo_source_registry", [{
        "theme_id": theme_id, "name": INTERNAL_REVIEW_SOURCE_NAME,
        "source_type": "other", "source_owner": "internal", "trust_tier": "E",
        "access_method": "manual", "automation_allowed": False,
        "content_storage_allowed": True, "internal_redistribution_allowed": True,
        "reference_availability": "available",
        "reference_availability_set_by": "system", "reference_availability_set_at": datetime.now(timezone.utc).isoformat(),
    }])[0]


def start_review_session(client, *, theme_id: str, final_text: str, reviewer_id: str,
                          case_id: str = None, report_id: str = None, draft_text: str = None,
                          draft_expert_response_id: str = None, reviewer_label: str = None,
                          review_type: str = "case_report", is_demo: bool = False) -> dict:
    """AI DraftとExpert Finalの2文書をgeo_documentsとして保存し、Sessionを開始する。
    draft_textの代わりにdraft_expert_response_id（既存geo_expert_responses）を渡せば、
    そのresponse_jsonをDraft本文として使う。

    地政学アプリClooseout Phase4: report_idを渡すとReport Review Loop（Reportのdraft-vs-final
    レビュー）として動作する。case_id/report_idはどちらか一方のみ指定可能（exactly one、
    DB制約とは独立にService層でも検証する）。report_idにはレビュー確定後の新Version
    （v2）のgeo_reports.report_idを渡す（呼び出し元の責任、v1は渡さない）。"""
    if review_type not in ("case_report", "executive_brief", "other", "report_review"):
        raise ValueError(f"未対応のreview_typeです: {review_type}")
    if bool(case_id) == bool(report_id):
        raise ValueError("case_id/report_idはどちらか一方のみ指定してください（両方指定・両方未指定は不可）")
    if not draft_text and not draft_expert_response_id:
        raise ValueError("draft_textまたはdraft_expert_response_idのいずれかが必須です")

    if not draft_text:
        rows = client.select("geo_expert_responses", {
            "select": "*", "response_id": f"eq.{draft_expert_response_id}", "limit": "1",
        })
        if not rows:
            raise ValueError(f"draft_expert_response_idが見つかりません: {draft_expert_response_id}")
        draft_text = json.dumps(rows[0]["response_json"], ensure_ascii=False)

    source = _ensure_internal_review_source(client, theme_id)
    draft_doc, _ = base.save_as_document(
        client, theme_id=theme_id, source_id=source["source_id"],
        title=f"Expert Review Draft ({reviewer_id})", extracted_text=draft_text,
        mime_type="text/plain", is_demo=is_demo)
    final_doc, _ = base.save_as_document(
        client, theme_id=theme_id, source_id=source["source_id"],
        title=f"Expert Review Final ({reviewer_id})", extracted_text=final_text,
        mime_type="text/plain", is_demo=is_demo)

    session = client.insert("geo_expert_review_sessions", [{
        "case_id": case_id, "report_id": report_id, "draft_document_id": draft_doc["document_id"],
        "final_document_id": final_doc["document_id"],
        "draft_expert_response_id": draft_expert_response_id,
        "reviewer_id": reviewer_id, "reviewer_label": reviewer_label,
        "review_type": review_type, "is_demo": is_demo,
        "delta_extraction_status": "pending",  # DB column defaultと明示的に揃える
    }])[0]
    geo_audit.log_audit(client, actor=reviewer_id, action="expert_review_session_start",
                         target_table="geo_expert_review_sessions", target_id=session["review_session_id"],
                         detail={"case_id": case_id, "review_type": review_type})
    return session


def add_review_note(client, *, review_session_id: str, comment_text: str, note_type: str = "free_text",
                     anchor_excerpt: str = None, author: str = None, source_format: str = None) -> dict:
    """専門家のReview Comment/Noteを追加する（任意、何回でも・0回でも可）。note_type='free_text'
    のみ今回のUI入力対象だが、anchor_excerpt/source_formatで将来のWord Comments/Track Changes
    取り込みを吸収できる構造にしてある。"""
    if note_type not in ("free_text", "word_comment", "track_change", "other"):
        raise ValueError(f"未対応のnote_typeです: {note_type}")
    return client.insert("geo_expert_review_notes", [{
        "review_session_id": review_session_id, "note_type": note_type,
        "anchor_excerpt": anchor_excerpt, "comment_text": comment_text,
        "author": author, "source_format": source_format,
    }])[0]


def _delta_extraction_schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "ExpertReviewDeltaExtraction",
        "type": "object",
        "additionalProperties": False,
        "required": ["changes"],
        "properties": {
            "changes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "change_type", "reusability_class", "reasoning_summary", "confidence",
                        "draft_excerpt", "final_excerpt", "grounding_note_ids",
                    ],
                    "properties": {
                        "change_type": {"type": "string", "enum": list(CHANGE_TYPES)},
                        "reusability_class": {"type": "string", "enum": list(REUSABILITY_CLASSES)},
                        "reasoning_summary": {"type": "string"},
                        "confidence": {"type": "string", "enum": _CONFIDENCE_ENUM},
                        # 片側のみの変更（stakeholder_removedはdraftのみ等）を許容するためnullable。
                        # ただし両方nullは無意味なので後段のPythonコードで除外する
                        "draft_excerpt": {"type": ["string", "null"]},
                        "final_excerpt": {"type": ["string", "null"]},
                        "grounding_note_ids": {
                            "type": "array",
                            "items": {
                                "type": "object", "additionalProperties": False,
                                "required": ["note_id", "role"],
                                "properties": {
                                    "note_id": {"type": "string"},
                                    "role": {"type": "string", "enum": list(NOTE_ROLES)},
                                },
                            },
                        },
                    },
                },
            },
        },
    }


DELTA_EXTRACTION_SCHEMA = _delta_extraction_schema()


def _normalize_confidence(value: str) -> str:
    if value in _CONFIDENCE_ENUM:
        return value
    mapped = _CONFIDENCE_WORD_MAP.get(str(value or "").strip().lower())
    return mapped or "判断不能"


def extract_deltas(client, azure_client, model: str, review_session_id: str, *, actor_id: str) -> dict:
    """AI DraftとExpert Final（＋任意のReviewer Note）からReasoning Change候補を抽出する。
    冪等: delta_extraction_status='completed'なら再抽出せず既存changesをそのまま返す。"""
    sessions = client.select("geo_expert_review_sessions", {
        "select": "*", "review_session_id": f"eq.{review_session_id}", "limit": "1",
    })
    if not sessions:
        raise ValueError(f"review_session_idが見つかりません: {review_session_id}")
    session = sessions[0]

    if session.get("delta_extraction_status") == "completed":
        existing_changes = client.select("geo_expert_review_changes", {
            "select": "*", "review_session_id": f"eq.{review_session_id}",
        })
        return {"review_session_id": review_session_id, "changes": existing_changes, "already_extracted": True}

    draft_chunks = geo_claim_service.chunk_document(client, session["draft_document_id"])
    final_chunks = geo_claim_service.chunk_document(client, session["final_document_id"])
    notes = client.select("geo_expert_review_notes", {
        "select": "*", "review_session_id": f"eq.{review_session_id}",
    })
    note_ids = {n["note_id"] for n in notes}

    system_prompt = (
        "あなたは社内専門家のAI Draft→Expert Final差分から、分析上意味のある変更のみを抽出する"
        "アシスタントです。専門家が実際に「何を重視し、どう比較し、どう仮説を修正したか」という"
        "Reasoningを抽出してください。\n\n"
        "以下は候補にしないでください: 文言・文法・語調の修正、文の短縮、フォーマット変更のみの差分。\n\n"
        "各変更について、当該Case・当該時点に紐づく判断（reusability_class="
        "case_specific_assessment）か、案件を越えて再利用可能な思考方法（reusability_class="
        "reusable_reasoning）かを分類してください。\n\n"
        "Reviewer Noteが提供されている場合、ある変更に対応する明示的な理由が書かれていれば、"
        "reasoning_summaryにはあなた自身の推測ではなく、そのNoteの説明をそのまま優先的に反映して"
        "ください。参照したNoteのnote_idとroleをgrounding_note_idsに含めてください"
        "（explicit_reason=専門家が明示した理由、supporting_comment=補足、context=背景情報）。"
        "Noteを参照しなかった場合はgrounding_note_idsを空配列にしてください。"
    )
    notes_text = "\n".join(f"- note_id={n['note_id']}: {n['comment_text']}" for n in notes) or "（Noteなし）"
    user_prompt = (
        f"【AI Draft】\n" + "\n".join(c["quoted_excerpt"] for c in draft_chunks) + "\n\n"
        f"【Expert Final】\n" + "\n".join(c["quoted_excerpt"] for c in final_chunks) + "\n\n"
        f"【Reviewer Note】\n{notes_text}"
    )

    try:
        result = geo_llm_common.call_llm_structured(
            azure_client, model, system_prompt, user_prompt, DELTA_EXTRACTION_SCHEMA,
            "geo_expert_review_delta_extraction")
    except Exception:
        client.update("geo_expert_review_sessions", {"review_session_id": f"eq.{review_session_id}"},
                       {"delta_extraction_status": "failed"})
        raise

    raw_changes = result["data"].get("changes", [])

    case_issue_ids = _resolve_session_issue_ids(client, session)

    created_changes = []
    skipped_note_id_count = 0
    for raw in raw_changes:
        draft_excerpt = raw.get("draft_excerpt")
        final_excerpt = raw.get("final_excerpt")
        if not draft_excerpt and not final_excerpt:
            continue  # wording-only等、片側も無い実質差分無しの候補は除外する

        change_row = client.insert("geo_expert_review_changes", [{
            "review_session_id": review_session_id, "change_type": raw["change_type"],
            "draft_excerpt": draft_excerpt, "final_excerpt": final_excerpt,
            "reasoning_summary": raw["reasoning_summary"], "reusability_class": raw["reusability_class"],
            "confidence": _normalize_confidence(raw.get("confidence")), "extraction_model": model,
            "disposition": "pending",  # DB column defaultと明示的に揃える（Fake/実DB双方一貫のため）
        }])[0]

        for issue_id in case_issue_ids:
            client.insert("geo_expert_review_change_issues", [{
                "change_id": change_row["change_id"], "issue_id": issue_id,
            }], prefer="return=minimal")

        # ★LLMが返したnote_idは、当該Sessionに実在するIDであることを検証してからのみ保存する
        # （LLMの幻覚によるFK違反・データ不整合を防ぐ。存在しないnote_id参照は無視しChange抽出
        # 自体は失敗させない）
        for grounding in raw.get("grounding_note_ids", []):
            note_id = grounding.get("note_id")
            if note_id in note_ids:
                client.insert("geo_expert_review_change_notes", [{
                    "change_id": change_row["change_id"], "note_id": note_id,
                    "role": grounding.get("role", "context"),
                }], prefer="return=minimal")
            else:
                skipped_note_id_count += 1

        created_changes.append(change_row)

    client.update("geo_expert_review_sessions", {"review_session_id": f"eq.{review_session_id}"}, {
        "delta_extraction_status": "completed",
        "completed_at": datetime.now(timezone.utc).isoformat(),
    })
    geo_audit.log_audit(client, actor=actor_id, action="expert_review_delta_extract",
                         target_table="geo_expert_review_sessions", target_id=review_session_id,
                         detail={"created_count": len(created_changes),
                                 "skipped_note_id_count": skipped_note_id_count})
    return {"review_session_id": review_session_id, "changes": created_changes, "already_extracted": False}


def _resolve_session_issue_ids(client, session: dict) -> list:
    """地政学アプリClooseout Phase4/5: SessionがCase由来ならgeo_case_issues経由、Report由来なら
    そのReportのdocumentから生成されたClaim（geo_claims.origin_type='document'）が
    geo_claim_issuesでリンクされているIssueを辿る（既存Reportのissue解決と同じ経路、
    新しいIssue紐付け方式は作らない）。Promote後のKnowledge Itemを既存Retrieval
    （geo_retrieval_service.get_internal_expert_assessments、Issue単位）から見つけられるように
    するため、extract_deltas（Change→Issue）とpromote_change（Knowledge Item→Issue）の
    両方でこれを使う。"""
    if session.get("case_id"):
        links = client.select("geo_case_issues", {"select": "issue_id", "case_id": f"eq.{session['case_id']}"})
        return sorted({link["issue_id"] for link in links})
    if session.get("report_id"):
        reports = client.select("geo_reports", {
            "select": "document_id", "report_id": f"eq.{session['report_id']}", "limit": "1"})
        if not reports:
            return []
        claims = client.select("geo_claims", {
            "select": "claim_id", "origin_type": "eq.document", "origin_id": f"eq.{reports[0]['document_id']}"})
        claim_ids = sorted({c["claim_id"] for c in claims})
        if not claim_ids:
            return []
        links = client.select("geo_claim_issues", {"select": "issue_id", "claim_id": f"in.({','.join(claim_ids)})"})
        return sorted({link["issue_id"] for link in links})
    return []


def _claim_pending_change(client, change_id: str) -> dict:
    """disposition='pending'の行のみを対象に取得する（冪等性チェックの第一段階）。
    既に他のdispositionへ遷移済みならExpertReviewChangeNotPendingErrorを送出する
    （promote/reinforce/keep/rejectの二重実行・Knowledge二重生成・support_count二重加算を防ぐ）。"""
    rows = client.select("geo_expert_review_changes", {
        "select": "*", "change_id": f"eq.{change_id}", "limit": "1",
    })
    if not rows:
        raise ValueError(f"change_idが見つかりません: {change_id}")
    if rows[0]["disposition"] != "pending":
        raise ExpertReviewChangeNotPendingError(
            f"change_id={change_id}は既にdisposition={rows[0]['disposition']}です（pendingのみ操作可能）"
        )
    return rows[0]


def _default_structured_content(knowledge_type: str, change: dict) -> dict:
    """edited_content未指定時の最小限の自動生成structured_content。Schema検証は通すが、
    内容は簡素なため実運用では「Edit & Promote」での加筆を推奨する。"""
    excerpt = change.get("final_excerpt") or change.get("draft_excerpt") or change["reasoning_summary"]
    if knowledge_type == "reasoning_rule":
        return {
            "rule": change["reasoning_summary"], "rationale": excerpt,
            "applicable_question_types": [], "required_checks": [], "comparison_dimensions": [],
            "common_failure_modes": [], "expert_source": "expert_review_session",
            "approval_status": "expert_review_promoted",
            "rule_category": _CHANGE_TYPE_TO_RULE_CATEGORY.get(change["change_type"], "general"),
        }
    if knowledge_type == "mechanism":
        return {
            "mechanism_name": change["reasoning_summary"][:80], "mechanism_summary": change["reasoning_summary"],
            "actor_objective": "", "causal_chain": [], "enabling_conditions": [], "required_assets": [],
            "target_dependencies": [], "constraints": [], "costs": [], "risks": [],
            "likely_countermeasures": [], "escalation_path": "", "de_escalation_path": "",
            "indicators": [], "confidence": change["confidence"],
        }
    if knowledge_type == "precedent":
        placeholder = "Expert Reviewから自動生成（要Edit & Promoteでの加筆）"
        return {
            "precedent_title": change["reasoning_summary"][:80], "question_examined": excerpt,
            "period": "不明", "geography": "不明", "actors": [], "background": excerpt,
            "triggering_event": placeholder, "actor_objectives": [], "instruments_used": [],
            "legal_and_institutional_conditions": placeholder, "counterpart_responses": placeholder,
            "outcome": placeholder, "success_factors": [], "failure_factors": [],
            "applicable_conditions": [placeholder], "non_applicable_conditions": [placeholder],
            "indicators_to_watch": [], "sources": [], "confidence": change["confidence"],
        }
    if knowledge_type == "case_finding":
        return {
            "case_id": change.get("_case_id_for_finding"), "finding_summary": change["reasoning_summary"],
            "why_reusable": "当該Case・当該時点における専門家の判断として記録する",
        }
    if knowledge_type == "report_finding":
        return {
            "report_id": change.get("_report_id_for_finding"), "finding_summary": change["reasoning_summary"],
            "why_reusable": "当該Report・当該時点における専門家の判断として記録する",
        }
    raise ValueError(f"未対応のknowledge_typeです: {knowledge_type}")


def _link_supporting_evidence_for_promotion(client, *, knowledge_item_id: str, change: dict,
                                             reviewer_id: str) -> None:
    """Promoteされたitemに、geo_expert_engine._has_supporting_evidence_chainのEvidence必須条件を
    満たすClaim/Evidenceを張る（既存geo_candidate_extraction_serviceの候補作成時と同じ対応。
    この処理が無いと、Active化してもExpert Engineへ二度と投入されなくなる。実DB検証で発見）。
    Expert Final文書のchunkのうち、final_excerptを含むもの（無ければ先頭chunk）を根拠にする。"""
    sessions = client.select("geo_expert_review_sessions", {
        "select": "final_document_id", "review_session_id": f"eq.{change['review_session_id']}", "limit": "1",
    })
    if not sessions:
        return
    chunks = geo_claim_service.chunk_document(client, sessions[0]["final_document_id"])
    if not chunks:
        return
    final_excerpt = change.get("final_excerpt") or ""
    chunk = next((c for c in chunks if final_excerpt and final_excerpt in c["quoted_excerpt"]), chunks[0])

    claim = geo_claim_service.create_claim(
        client, claim_text=change["reasoning_summary"], information_class="internal_expert_assessment",
        origin_type="document", origin_id=sessions[0]["final_document_id"], actor_id=reviewer_id,
    )
    geo_claim_service.link_evidence(client, claim_id=claim["claim_id"], evidence_role="supporting",
                                     actor_id=reviewer_id, document_chunk_id=chunk["chunk_id"])
    client.insert("geo_knowledge_item_claims", [{
        "knowledge_item_id": knowledge_item_id, "claim_id": claim["claim_id"], "relationship": "supporting",
    }], prefer="return=minimal")


def promote_change(client, *, change_id: str, reviewer_id: str, reviewer_role: str = "expert_reviewer",
                    edited_content: dict = None) -> dict:
    """Reusability Rule候補をInternal Expert Knowledgeとして昇格させる。既存の
    create_knowledge_item（status=candidate）→review_knowledge_item（approve_active）を
    そのまま2連続で呼ぶだけで、新しい状態遷移ロジックは作らない。"""
    change = _claim_pending_change(client, change_id)

    if change["reusability_class"] == "case_specific_assessment":
        if not edited_content:
            sessions = client.select("geo_expert_review_sessions", {
                "select": "case_id,report_id", "review_session_id": f"eq.{change['review_session_id']}", "limit": "1",
            })
            session_for_promote = sessions[0] if sessions else {}
            case_id = session_for_promote.get("case_id")
            report_id = session_for_promote.get("report_id")
            if not case_id and not report_id:
                raise ValueError("case_specific_assessmentのPromoteにはSessionにcase_idまたはreport_idが"
                                 "必要です（edited_contentで明示すれば省略可）")
            # 地政学アプリClooseout Phase4: SessionがReport由来（report_id設定済み）ならreport_finding、
            # Case由来（case_id設定済み）ならcase_findingへ振り分ける（exactly-one制約により
            # 両方は起こらない）。
            knowledge_type = "report_finding" if report_id else "case_finding"
            change_with_subject = {**change, "_case_id_for_finding": case_id, "_report_id_for_finding": report_id}
            structured_content = _default_structured_content(knowledge_type, change_with_subject)
        else:
            # edited_content指定時は、含まれるキーからknowledge_typeを判別する
            # （report_id指定ならreport_finding、それ以外はcase_finding、既存互換）。
            knowledge_type = "report_finding" if "report_id" in edited_content else "case_finding"
            structured_content = edited_content
    else:
        knowledge_type = _CHANGE_TYPE_TO_KNOWLEDGE_TYPE.get(change["change_type"], _DEFAULT_REUSABLE_KNOWLEDGE_TYPE)
        structured_content = edited_content or _default_structured_content(knowledge_type, change)

    schemas.validate_structured_content(knowledge_type, structured_content)

    item = geo_knowledge_service.create_knowledge_item(
        client, knowledge_type=knowledge_type, title=change["reasoning_summary"][:120],
        structured_content=structured_content, information_class="internal_expert_assessment",
        created_by=reviewer_id, summary=change["reasoning_summary"], confidence=change["confidence"],
        origin="internal_expert_review", derived_from_review_session_id=change["review_session_id"],
        derived_from_change_id=change_id,
    )
    _link_supporting_evidence_for_promotion(client, knowledge_item_id=item["knowledge_item_id"],
                                             change=change, reviewer_id=reviewer_id)

    # ALLOWED_TRANSITIONSはcandidate→in_review→activeの2段階のため、既存review_knowledge_item
    # を2回連続で呼ぶ（新しい状態遷移ロジックは作らず、既存の許可された遷移だけを通す）
    geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=item["knowledge_item_id"], reviewer_id=reviewer_id,
        reviewer_role=reviewer_role, review_decision="start_review",
        review_comment="Expert Review Delta由来のPromote", evidence_adequacy="sufficient",
        applicability_checked=True,
    )
    geo_knowledge_service.review_knowledge_item(
        client, knowledge_item_id=item["knowledge_item_id"], reviewer_id=reviewer_id,
        reviewer_role=reviewer_role, review_decision="approve_active",
        review_comment="Expert Review Delta由来のPromote", evidence_adequacy="sufficient",
        applicability_checked=True,
    )
    # ★実DB検証で発見: create_knowledge_item()が返す初回のitemはstatus='candidate'のままの
    # スナップショットで、review_knowledge_item()を呼んでもこのローカル変数は自動更新されない
    # （FakeSupabaseClientは辞書を参照共有するため気づけなかったが、実際のHTTP経由の
    # SupabaseClientでは別リクエストであり同期しない）。呼び出し元に正しいstatus=activeを
    # 返すため、明示的に再取得する
    item = geo_knowledge_service.get_knowledge_item(client, item["knowledge_item_id"])

    # 地政学アプリClooseout Phase5: 既存Retrieval（geo_retrieval_service.get_foundation_knowledge/
    # get_reusable_knowledge/get_internal_expert_assessments等）はいずれもgeo_knowledge_item_issues
    # 経由のIssue紐付けを前提にしている。PromoteされたKnowledge Itemがこのリンクを持たないと、
    # 次回のExpert AI/Intelligence AI分析から永久に参照されない（実データ検証で判明した既存の
    # 未接続）。SessionのCase/Report起点で解決できるissue_idsへ、新しいIssue紐付け方式を作らず
    # 既存link_to_issueで接続する。
    sessions_for_link = client.select("geo_expert_review_sessions", {
        "select": "case_id,report_id", "review_session_id": f"eq.{change['review_session_id']}", "limit": "1",
    })
    if sessions_for_link:
        for issue_id in _resolve_session_issue_ids(client, sessions_for_link[0]):
            geo_knowledge_service.link_to_issue(client, knowledge_item_id=item["knowledge_item_id"],
                                                 issue_id=issue_id)

    # ★冪等性(disposition='pending'を条件にした更新。0件更新なら既に他プロセスが遷移させた
    # ということなので、ここではChange側の記録のみ諦める（Knowledge Itemは既に作成済みのまま残す。
    # 実運用ではChange取得時点でのpendingチェックが主たる防御であり、ここはDB側の二重防御）
    client.update("geo_expert_review_changes",
                  {"change_id": f"eq.{change_id}", "disposition": "eq.pending"}, {
        "disposition": "promoted", "promoted_knowledge_item_id": item["knowledge_item_id"],
        "disposition_by": reviewer_id, "disposition_at": datetime.now(timezone.utc).isoformat(),
    })
    geo_audit.log_audit(client, actor=reviewer_id, action="expert_review_change_promote",
                         target_table="geo_expert_review_changes", target_id=change_id,
                         detail={"knowledge_item_id": item["knowledge_item_id"], "knowledge_type": knowledge_type})

    updated_change = client.select("geo_expert_review_changes",
                                    {"select": "*", "change_id": f"eq.{change_id}", "limit": "1"})[0]
    return {"change": updated_change, "knowledge_item": item}


def reinforce_change(client, *, change_id: str, reviewer_id: str, reinforced_knowledge_item_id: str) -> dict:
    """既存Reasoningを別案件で再確認した場合の記録。新規Knowledge Itemは作らず、対象itemの
    support_countのみ増分する（"duplicate"＝重複破棄ではなく、"reinforced"＝再確認）。"""
    change = _claim_pending_change(client, change_id)

    item = geo_knowledge_service.get_knowledge_item(client, reinforced_knowledge_item_id)
    if not item:
        raise ValueError(f"reinforced_knowledge_item_idが見つかりません: {reinforced_knowledge_item_id}")

    client.update("geo_knowledge_items", {"knowledge_item_id": f"eq.{reinforced_knowledge_item_id}"}, {
        "support_count": item.get("support_count", 0) + 1,
        "last_confirmed_at": datetime.now(timezone.utc).isoformat(),
    })

    client.update("geo_expert_review_changes",
                  {"change_id": f"eq.{change_id}", "disposition": "eq.pending"}, {
        "disposition": "reinforced", "reinforced_knowledge_item_id": reinforced_knowledge_item_id,
        "disposition_by": reviewer_id, "disposition_at": datetime.now(timezone.utc).isoformat(),
    })
    geo_audit.log_audit(client, actor=reviewer_id, action="expert_review_change_reinforce",
                         target_table="geo_expert_review_changes", target_id=change_id,
                         detail={"reinforced_knowledge_item_id": reinforced_knowledge_item_id,
                                 "change_type": change["change_type"]})

    updated_change = client.select("geo_expert_review_changes",
                                    {"select": "*", "change_id": f"eq.{change_id}", "limit": "1"})[0]
    updated_item = geo_knowledge_service.get_knowledge_item(client, reinforced_knowledge_item_id)
    return {"change": updated_change, "knowledge_item": updated_item}


def keep_case_only(client, *, change_id: str, reviewer_id: str) -> dict:
    """このReasoningは当該Case限りとし、Reusable Knowledgeへは昇格させない。"""
    _claim_pending_change(client, change_id)
    client.update("geo_expert_review_changes",
                  {"change_id": f"eq.{change_id}", "disposition": "eq.pending"}, {
        "disposition": "kept_case_only", "disposition_by": reviewer_id,
        "disposition_at": datetime.now(timezone.utc).isoformat(),
    })
    geo_audit.log_audit(client, actor=reviewer_id, action="expert_review_change_keep_case_only",
                         target_table="geo_expert_review_changes", target_id=change_id, detail={})
    return client.select("geo_expert_review_changes", {"select": "*", "change_id": f"eq.{change_id}", "limit": "1"})[0]


def reject_change(client, *, change_id: str, reviewer_id: str) -> dict:
    """このChangeは再利用価値が無いと判断し、破棄する。"""
    _claim_pending_change(client, change_id)
    client.update("geo_expert_review_changes",
                  {"change_id": f"eq.{change_id}", "disposition": "eq.pending"}, {
        "disposition": "rejected_as_non_reusable", "disposition_by": reviewer_id,
        "disposition_at": datetime.now(timezone.utc).isoformat(),
    })
    geo_audit.log_audit(client, actor=reviewer_id, action="expert_review_change_reject",
                         target_table="geo_expert_review_changes", target_id=change_id, detail={})
    return client.select("geo_expert_review_changes", {"select": "*", "change_id": f"eq.{change_id}", "limit": "1"})[0]
