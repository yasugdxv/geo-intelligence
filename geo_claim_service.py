"""地政学ナレッジ＆インテリジェンスDB: Claim / Evidence登録、文書のchunking。

geo_events(既存, 変更なし)は「日時・場所・主体を持つ観測」、geo_claims(新規)は
「そこから派生する解釈を伴う主張」を表す。同一内容を両方へ二重に生登録しない
（docs/geopolitical-knowledge-architecture.md 7章）。
"""
import hashlib

import geo_audit

ORIGIN_TYPES = ("event", "observation", "document", "knowledge_item", "case", "manual")
EVIDENCE_ROLES = ("supporting", "contradicting", "contextual", "qualification")
CHUNK_SIZE_CHARS = 1500
RETRIEVAL_STATUSES = ("available", "restricted", "deprecated", "archived")
# origin_typeの値そのものでは「外部由来か」を判定しない（Phase2B-2）。この2つは定義上必ず
# Sourceを持たない、明示的にInternal生成と判定できるoriginのみ（それ以外はsource_id解決を試み、
# 解決できなければ安全側でrestrictedのままにする）
_INTERNAL_ORIGIN_TYPES_WITHOUT_SOURCE = ("manual", "case")


def _resolve_source_id(client, *, origin_type: str, origin_id: str) -> str | None:
    """Claimの情報の出所となるSourceを、origin_typeに応じた経路で解決する（Phase2B-2）。
    origin_type='document'以外でも、event/observationは証拠(geo_evidence)経由でSourceに
    たどり着けることがあるため、origin_typeだけでInternal/Externalを判定しない。"""
    if not origin_id:
        return None
    if origin_type == "document":
        rows = client.select("geo_documents", {
            "select": "source_id", "document_id": f"eq.{origin_id}", "limit": "1",
        })
        return rows[0].get("source_id") if rows else None
    if origin_type == "event":
        evidence = client.select("geo_evidence", {
            "select": "document_id", "event_id": f"eq.{origin_id}", "order": "created_at.asc", "limit": "1",
        })
        if not evidence:
            return None
        doc = client.select("geo_documents", {
            "select": "source_id", "document_id": f"eq.{evidence[0]['document_id']}", "limit": "1",
        })
        return doc[0].get("source_id") if doc else None
    if origin_type == "observation":
        obs = client.select("geo_observations", {
            "select": "evidence_id", "observation_id": f"eq.{origin_id}", "limit": "1",
        })
        if not obs or not obs[0].get("evidence_id"):
            return None
        evidence = client.select("geo_evidence", {
            "select": "document_id", "evidence_id": f"eq.{obs[0]['evidence_id']}", "limit": "1",
        })
        if not evidence:
            return None
        doc = client.select("geo_documents", {
            "select": "source_id", "document_id": f"eq.{evidence[0]['document_id']}", "limit": "1",
        })
        return doc[0].get("source_id") if doc else None
    return None  # knowledge_item/manual/caseはsource_id解決を試みない


def _resolve_initial_retrieval_status(client, *, origin_type: str, origin_id: str,
                                       source_id: str = None) -> tuple[str, str | None]:
    """(retrieval_status, resolved_source_id) を決める（Phase2B-2、1章参照）。
    1. source_idが解決できればSourceのreference_availabilityをコピーする
    2. source_idが解決できず、origin_typeがmanual/caseならavailable
    3. それ以外はdefaultのrestrictedのまま"""
    resolved_source_id = source_id or _resolve_source_id(client, origin_type=origin_type, origin_id=origin_id)
    if resolved_source_id:
        sources = client.select("geo_source_registry", {
            "select": "reference_availability", "source_id": f"eq.{resolved_source_id}", "limit": "1",
        })
        # 列のDB defaultはrestrictedのため、行に未設定（キー欠損）の場合もrestrictedとして扱う
        if sources:
            return sources[0].get("reference_availability", "restricted"), resolved_source_id
        return "restricted", resolved_source_id
    if origin_type in _INTERNAL_ORIGIN_TYPES_WITHOUT_SOURCE:
        return "available", None
    return "restricted", None


def create_claim(client, *, claim_text: str, information_class: str, origin_type: str,
                  actor_id: str, origin_id: str = None, normalized_claim: str = None,
                  subject_entity_id: str = None, predicate: str = None,
                  object_entity_id: str = None, object_value: dict = None,
                  confidence: str = "判断不能", valid_from: str = None, valid_to: str = None,
                  source_id: str = None, is_demo: bool = False) -> dict:
    if origin_type not in ORIGIN_TYPES:
        raise ValueError(f"未対応のorigin_typeです: {origin_type}")
    if information_class not in ("observed_fact", "actor_claim", "external_assessment",
                                  "internal_hypothesis", "internal_expert_assessment"):
        raise ValueError(f"未対応のinformation_classです: {information_class}")
    if origin_type != "manual" and not origin_id:
        raise ValueError("origin_type='manual'以外はorigin_idが必須です")

    retrieval_status, resolved_source_id = _resolve_initial_retrieval_status(
        client, origin_type=origin_type, origin_id=origin_id, source_id=source_id)

    row = {
        "claim_text": claim_text, "normalized_claim": normalized_claim,
        "information_class": information_class, "subject_entity_id": subject_entity_id,
        "predicate": predicate, "object_entity_id": object_entity_id, "object_value": object_value,
        "confidence": confidence, "valid_from": valid_from, "valid_to": valid_to,
        "origin_type": origin_type, "origin_id": origin_id, "is_demo": is_demo,
        "source_id": resolved_source_id, "retrieval_status": retrieval_status,
    }
    created = client.insert("geo_claims", [row])[0]
    geo_audit.log_audit(client, actor=actor_id, action="claim_create",
                         target_table="geo_claims", target_id=created["claim_id"],
                         detail={"information_class": information_class, "origin_type": origin_type,
                                 "retrieval_status": retrieval_status})
    return created


def set_retrieval_status(client, *, claim_id: str, retrieval_status: str,
                          actor_id: str, reason: str = None) -> dict:
    """Claim個別のretrieval_statusを直接上書きする（Phase2B-2）。Sourceのreference_availabilityが
    availableでも、この特定Claimだけはrestrictedにする、といった個別判断に対応する。
    内容の正誤判定ではなく参照可否を管理する運用操作のため、geo_audit.log_auditにのみ記録する。"""
    if retrieval_status not in RETRIEVAL_STATUSES:
        raise ValueError(f"未対応のretrieval_statusです: {retrieval_status}")
    rows = client.select("geo_claims", {"select": "*", "claim_id": f"eq.{claim_id}", "limit": "1"})
    if not rows:
        raise ValueError(f"claim_idが見つかりません: {claim_id}")

    client.update("geo_claims", {"claim_id": f"eq.{claim_id}"}, {"retrieval_status": retrieval_status})
    geo_audit.log_audit(client, actor=actor_id, action="claim_retrieval_status_change",
                         target_table="geo_claims", target_id=claim_id,
                         detail={"previous_retrieval_status": rows[0].get("retrieval_status"),
                                 "new_retrieval_status": retrieval_status, "reason": reason})
    return client.select("geo_claims", {"select": "*", "claim_id": f"eq.{claim_id}", "limit": "1"})[0]


def link_claim_to_issue(client, *, claim_id: str, issue_id: str) -> dict:
    """geo_claim_issues経由でIssueに紐付ける（Phase2B-2。geo_knowledge_item_issuesと同一パターン）。"""
    return client.insert("geo_claim_issues", [{"claim_id": claim_id, "issue_id": issue_id}])[0]


def link_evidence(client, *, claim_id: str, evidence_role: str, actor_id: str,
                   document_chunk_id: str = None, event_id: str = None,
                   observation_id: str = None, metric_observation_id: str = None,
                   directness: str = "direct") -> dict:
    """Claimをdocument_chunk/event/observation/metric_observationのいずれか1つへ接続する
    （Phase2A: metric_observation_idを追加。DB側CHECK制約と同じ「ちょうど1つ」ルールを
    ここでも検証し、わかりやすいエラーメッセージを返す）。"""
    if evidence_role not in EVIDENCE_ROLES:
        raise ValueError(f"未対応のevidence_roleです: {evidence_role}")
    targets = [t for t in (document_chunk_id, event_id, observation_id, metric_observation_id) if t]
    if len(targets) != 1:
        raise ValueError("document_chunk_id/event_id/observation_id/metric_observation_idの"
                          "うちちょうど1つを指定してください")

    row = {
        "claim_id": claim_id, "document_chunk_id": document_chunk_id, "event_id": event_id,
        "observation_id": observation_id, "metric_observation_id": metric_observation_id,
        "evidence_role": evidence_role, "directness": directness,
    }
    created = client.insert("geo_claim_evidence", [row])[0]
    geo_audit.log_audit(client, actor=actor_id, action="claim_evidence_link",
                         target_table="geo_claim_evidence", target_id=created["claim_evidence_id"],
                         detail={"claim_id": claim_id, "evidence_role": evidence_role})
    return created


def get_claim_with_evidence(client, claim_id: str) -> dict:
    claim_rows = client.select("geo_claims", {"select": "*", "claim_id": f"eq.{claim_id}", "limit": "1"})
    if not claim_rows:
        raise ValueError(f"claim_idが見つかりません: {claim_id}")
    evidence = client.select("geo_claim_evidence", {"select": "*", "claim_id": f"eq.{claim_id}"})
    return {"claim": claim_rows[0], "evidence": evidence}


def _split_into_chunks(text: str, chunk_size: int = CHUNK_SIZE_CHARS) -> list:
    """段落単位（空行区切り）で、chunk_size文字を超えない範囲にまとめる素朴な分割。
    高度なトークン境界処理はPhase2以降で検討する。"""
    paragraphs = [p.strip() for p in (text or "").split("\n\n") if p.strip()]
    if not paragraphs:
        return [text.strip()] if (text or "").strip() else []

    chunks, current = [], ""
    for para in paragraphs:
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) > chunk_size and current:
            chunks.append(current)
            current = para
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def chunk_document(client, document_id: str, *, parser_version: str = "geo-knowledge-1.0") -> list:
    """geo_documents.extracted_textをgeo_document_chunksへ分割保存する（冪等: 既にchunk済みなら
    既存行をそのまま返す）。既存geo_documents.extracted_text（全文）は変更しない。"""
    existing = client.select("geo_document_chunks", {
        "select": "*", "document_id": f"eq.{document_id}", "order": "chunk_index.asc",
    })
    if existing:
        return existing

    doc_rows = client.select("geo_documents", {"select": "*", "document_id": f"eq.{document_id}", "limit": "1"})
    if not doc_rows:
        raise ValueError(f"document_idが見つかりません: {document_id}")

    chunks = _split_into_chunks(doc_rows[0].get("extracted_text") or "")
    rows = []
    for i, chunk_text in enumerate(chunks):
        content_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
        rows.append({
            "document_id": document_id, "chunk_index": i, "page": None, "section": None,
            "quoted_excerpt": chunk_text, "parser_version": parser_version, "content_hash": content_hash,
        })
    if not rows:
        return []
    return client.insert("geo_document_chunks", rows)
